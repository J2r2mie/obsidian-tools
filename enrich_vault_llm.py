#!/usr/bin/env python3
"""
enrich_vault_llm.py
Enrichit les fichiers Markdown du vault Obsidian via un LLM local (LM Studio / Ollama).

Pour chaque fichier .md, le script :
  - Extrait un résumé intelligent du contenu (titre + 1er échange)
  - Envoie au LLM un prompt structuré demandant : résumé, tags, domaine
  - Écrit les résultats dans le frontmatter YAML

Le script est incrémental : il ignore les fichiers déjà enrichis (sauf --force).
Il sauvegarde sa progression dans un fichier JSON pour pouvoir reprendre en cas d'arrêt.

Usage :
  python enrich_vault_llm.py --dry-run              # Test sur 3 fichiers, sans écrire
  python enrich_vault_llm.py --test 5                # Test sur 5 fichiers, avec écriture
  python enrich_vault_llm.py                         # Traitement complet
  python enrich_vault_llm.py --force                 # Ré-enrichir même les fichiers déjà faits
  python enrich_vault_llm.py --dirs Perplexity       # Seulement les fichiers Perplexity

Prérequis :
  pip install python-frontmatter requests
  LM Studio (ou Ollama) avec serveur local actif sur le port 1234
"""

import argparse
import json
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import frontmatter
import requests
import yaml

# ==============================================================================
# CONFIGURATION
# ==============================================================================

VAULT_PATH = Path("/Users/jeremie/Documents/Obsidian Vault")
SOURCE_DIRS = ["Perplexity", "Claude"]

# API LM Studio (compatible OpenAI)
API_URL = "http://localhost:1234/v1/chat/completions"
API_TIMEOUT = 120  # secondes max par requête

# Fichier de progression (pour reprendre après interruption)
PROGRESS_FILE = Path("enrich_llm_progress.json")

# Longueur max de l'extrait envoyé au LLM (en caractères)
MAX_EXTRACT_LENGTH = 3000

# ==============================================================================
# LISTE DES TOPICS AUTORISÉS (identique à enrich_vault.py)
# ==============================================================================

ALLOWED_TOPICS = [
    "polymeres-recyclage",
    "pkm-workflow",
    "tech-dev",
    "ia-llm",
    "pedagogie-recherche",
    "photo-media",
    "sante-sport",
    "apple-macos",
    "budget-finance",
    "cuisine",
    "domotique",
    "musique",
    "voyage",
    "general",
]

# ==============================================================================
# PROMPT SYSTÈME
# ==============================================================================

SYSTEM_PROMPT = f"""Tu es un assistant spécialisé dans l'analyse et le classement de conversations.
On te fournit un extrait d'une conversation entre un humain et une IA (Claude ou Perplexity).
Tu dois analyser le contenu et répondre UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ou après.

Le JSON doit contenir exactement ces 3 champs :
- "ai_summary" : un résumé factuel de la conversation en 2 phrases maximum (en français).
- "ai_tags" : une liste de 2 à 4 tags spécifiques au contenu (en français, en minuscules, avec tirets). Ces tags doivent être plus précis que le domaine général.
- "ai_topic" : le domaine principal parmi cette liste fermée : {json.dumps(ALLOWED_TOPICS)}

Règles :
- Le résumé doit capturer le sujet principal et ce qui a été accompli ou discuté.
- Les tags doivent être spécifiques (ex: "recyclage-epdm", "tri-nir", "obsidian-dataview") et non génériques.
- Si le sujet ne correspond à aucun domaine, utilise "general".
- Réponds UNIQUEMENT avec le JSON, pas de markdown, pas de backticks, pas de texte.

Exemple de réponse attendue :
{{"ai_summary": "Discussion sur le recyclage mécanique des polyoléfines PP/PE, couvrant la compatibilisation et les techniques de tri NIR.", "ai_tags": ["recyclage-mécanique", "polyoléfines", "tri-nir"], "ai_topic": "polymeres-recyclage"}}"""


# ==============================================================================
# EXTRACTION D'UN RÉSUMÉ INTELLIGENT DU FICHIER
# ==============================================================================

def extract_snippet(post) -> str:
    """
    Extrait un résumé intelligent du fichier pour envoyer au LLM.
    Stratégie : titre + première question Human + début de la première réponse IA.
    """
    title = str(post.get("title", "") or "Sans titre")
    body = post.content

    # Chercher les tours de conversation
    parts = re.split(r"^## (?:Human|Claude|Perplexity)\s*$", body, flags=re.MULTILINE)

    # parts[0] = avant le premier ## Human (souvent le titre H1, vide ou presque)
    # parts[1] = contenu du premier Human
    # parts[2] = contenu de la première réponse IA
    # etc.

    human_msg = ""
    ai_msg = ""

    if len(parts) >= 2:
        human_msg = parts[1].replace("---", "").strip()[:500]
    if len(parts) >= 3:
        ai_msg = parts[2].replace("---", "").strip()[:2000]

    # Si pas de tours détectés, prendre le début du body
    if not human_msg and not ai_msg:
        ai_msg = body[:MAX_EXTRACT_LENGTH]

    snippet = f"Titre : {title}\n"
    if human_msg:
        snippet += f"\nQuestion : {human_msg}\n"
    if ai_msg:
        snippet += f"\nRéponse : {ai_msg}\n"

    return snippet[:MAX_EXTRACT_LENGTH]


# ==============================================================================
# APPEL AU LLM LOCAL
# ==============================================================================

def call_llm(snippet: str) -> dict | None:
    """
    Envoie l'extrait au LLM et parse la réponse JSON.
    Retourne un dict avec ai_summary, ai_tags, ai_topic ou None en cas d'erreur.
    """
    payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Voici l'extrait de conversation à analyser :\n\n{snippet}"},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    try:
        r = requests.post(API_URL, json=payload, timeout=API_TIMEOUT)
        r.raise_for_status()
    except requests.ConnectionError:
        print("\n  ERREUR : impossible de contacter le serveur LM Studio sur localhost:1234")
        print("  Vérifie que LM Studio est lancé et que le serveur local est activé.")
        sys.exit(1)
    except requests.Timeout:
        return None
    except requests.HTTPError as e:
        print(f"\n  ERREUR HTTP {e.response.status_code}")
        return None

    data = r.json()
    raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Nettoyage : enlever les backticks markdown si présentes
    raw_content = raw_content.strip()
    raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
    raw_content = re.sub(r"\s*```$", "", raw_content)
    raw_content = raw_content.strip()

    # Parser le JSON
    try:
        result = json.loads(raw_content)
    except json.JSONDecodeError:
        # Tenter d'extraire un objet JSON du texte
        match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Validation minimale
    if not isinstance(result, dict):
        return None
    if "ai_summary" not in result:
        return None

    # Normaliser les tags
    tags = result.get("ai_tags", [])
    if isinstance(tags, list):
        result["ai_tags"] = [str(t).lower().strip() for t in tags[:5]]
    else:
        result["ai_tags"] = []

    # Valider le topic
    topic = result.get("ai_topic", "general")
    if topic not in ALLOWED_TOPICS:
        result["ai_topic"] = "general"

    return result


# ==============================================================================
# SÉRIALISATION YAML (même logique que enrich_vault.py)
# ==============================================================================

YAML_KEY_ORDER = [
    "title", "date", "updated", "tags", "source",
    "conversation_id", "thread_id", "slug", "model",
    "nb_messages",
    "topic", "language", "word_count",
    "importance", "content_type", "triage_score", "triage_date",
    # Champs LLM
    "ai_summary", "ai_tags", "ai_topic", "ai_model", "ai_date",
]


def _yaml_representer_str(dumper, data):
    if any(c in data for c in (':', '#', '{', '}', '[', ']', "'", '"', '\n')):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


def _yaml_representer_datetime(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data.strftime("%Y-%m-%dT%H:%M:%S"))


def dump_post_preserving_order(post) -> str:
    metadata = OrderedDict()
    all_keys = set(post.metadata.keys())

    for key in YAML_KEY_ORDER:
        if key in all_keys:
            metadata[key] = post.metadata[key]
            all_keys.discard(key)

    for key in post.metadata:
        if key in all_keys:
            metadata[key] = post.metadata[key]

    class OrderedDumper(yaml.SafeDumper):
        pass

    OrderedDumper.add_representer(
        OrderedDict,
        lambda dumper, data: dumper.represent_mapping('tag:yaml.org,2002:map', data.items())
    )
    OrderedDumper.add_representer(datetime, _yaml_representer_datetime)
    OrderedDumper.add_representer(str, _yaml_representer_str)

    yaml_str = yaml.dump(
        metadata,
        Dumper=OrderedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )

    return f"---\n{yaml_str}---\n{post.content}"


# ==============================================================================
# GESTION DE LA PROGRESSION
# ==============================================================================

def load_progress(path: Path) -> set:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return set(data.get("done", []))
        except Exception:
            pass
    return set()


def save_progress(path: Path, done: set):
    path.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False), encoding="utf-8")


# ==============================================================================
# TRAITEMENT D'UN FICHIER
# ==============================================================================

def process_file(filepath: Path, model_name: str, dry_run: bool = False) -> dict | None:
    """Enrichit un fichier avec les métadonnées LLM."""
    try:
        raw = filepath.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ERR lecture {filepath.name} : {e}")
        return None

    if not raw.startswith("---"):
        return None

    try:
        post = frontmatter.loads(raw)
    except Exception as e:
        print(f"  ERR frontmatter {filepath.name} : {e}")
        return None

    # Extraire le snippet
    snippet = extract_snippet(post)
    title = str(post.get("title", "") or "")[:50]

    if dry_run:
        print(f"  [extrait] {filepath.name}")
        print(f"            {snippet[:120]}...")
        return {"status": "dry-run", "file": filepath.name}

    # Appel LLM
    result = call_llm(snippet)

    if result is None:
        print(f"  FAIL  {filepath.name:<50} (réponse LLM invalide)")
        return None

    # Écrire dans le frontmatter
    post["ai_summary"] = result["ai_summary"]
    post["ai_tags"] = result["ai_tags"]
    post["ai_topic"] = result["ai_topic"]
    post["ai_model"] = model_name
    post["ai_date"] = datetime.today().strftime("%Y-%m-%d")

    try:
        output = dump_post_preserving_order(post)
        filepath.write_text(output, encoding="utf-8")
    except Exception as e:
        print(f"  ERR écriture {filepath.name} : {e}")
        return None

    # Affichage
    topic_match = "✓" if result["ai_topic"] == post.get("topic", "") else "≠"
    print(f"  OK  {filepath.name:<50} {topic_match} {result['ai_topic']:<22} | {', '.join(result['ai_tags'][:3])}")

    return {"status": "ok", "file": filepath.name, **result}


# ==============================================================================
# MAIN
# ==============================================================================

def build_parser():
    p = argparse.ArgumentParser(
        description="Enrichit le vault Obsidian via un LLM local (LM Studio / Ollama).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dry-run", "-n", action="store_true",
                   help="Affiche les extraits sans appeler le LLM")
    p.add_argument("--test", type=int, metavar="N",
                   help="Traite seulement les N premiers fichiers")
    p.add_argument("--force", action="store_true",
                   help="Ré-enrichir même les fichiers déjà traités")
    p.add_argument("--vault", default=str(VAULT_PATH),
                   help=f"Chemin du vault (défaut: {VAULT_PATH})")
    p.add_argument("--dirs", nargs="*", default=SOURCE_DIRS,
                   help=f"Sous-dossiers à traiter (défaut: {SOURCE_DIRS})")
    p.add_argument("--model-name", default="mistral-7b-instruct",
                   help="Nom du modèle (pour traçabilité dans le frontmatter)")
    p.add_argument("--api-url", default=API_URL,
                   help=f"URL de l'API (défaut: {API_URL})")
    return p


def main():
    args = build_parser().parse_args()
    vault = Path(args.vault).expanduser()

    global API_URL
    API_URL = args.api_url

    if not vault.exists():
        print(f"Vault introuvable : {vault}", file=sys.stderr)
        sys.exit(1)

    # Vérifier la connexion au LLM (sauf dry-run)
    if not args.dry_run:
        try:
            r = requests.get(args.api_url.replace("/chat/completions", "/models"), timeout=5)
            r.raise_for_status()
            print(f"Serveur LLM connecté sur {args.api_url}")
        except Exception:
            print(f"ERREUR : impossible de contacter le serveur sur {args.api_url}")
            print("Vérifie que LM Studio est lancé et le serveur local activé.")
            sys.exit(1)

    # Charger la progression
    progress = load_progress(PROGRESS_FILE)
    if progress and not args.force:
        print(f"Progression chargée : {len(progress)} fichiers déjà traités.")

    # Collecter les fichiers
    all_files = []
    for dir_name in args.dirs:
        directory = vault / dir_name
        if not directory.exists():
            print(f"Répertoire introuvable : {directory}")
            continue
        all_files.extend(sorted(directory.glob("*.md")))

    # Filtrer les fichiers déjà traités
    if not args.force:
        files = []
        for f in all_files:
            if str(f) in progress:
                continue
            # Vérifier aussi si le fichier a déjà un ai_summary
            if not args.force:
                try:
                    raw = f.read_text(encoding="utf-8")
                    if "ai_summary:" in raw:
                        continue
                except Exception:
                    pass
            files.append(f)
    else:
        files = all_files

    if args.test:
        files = files[:args.test]

    total = len(files)
    total_all = len(all_files)
    skipped = total_all - total

    print(f"\nFichiers à traiter : {total} (sur {total_all}, {skipped} déjà enrichis)")

    if total == 0:
        print("Rien à faire.")
        return

    # Traitement
    ok = fail = 0
    start_time = time.time()

    for i, filepath in enumerate(files, 1):
        elapsed = time.time() - start_time
        rate = elapsed / i if i > 1 else 0
        remaining = rate * (total - i)
        eta = f"{remaining / 60:.0f}min" if remaining > 60 else f"{remaining:.0f}s"

        print(f"[{i}/{total}] (ETA ~{eta})", end=" ")

        result = process_file(filepath, args.model_name, dry_run=args.dry_run)

        if result and result.get("status") == "ok":
            ok += 1
            progress.add(str(filepath))
            # Sauvegarder la progression tous les 10 fichiers
            if ok % 10 == 0 and not args.dry_run:
                save_progress(PROGRESS_FILE, progress)
        elif result and result.get("status") == "dry-run":
            pass
        else:
            fail += 1

    # Sauvegarder la progression finale
    if not args.dry_run:
        save_progress(PROGRESS_FILE, progress)

    # Résumé
    elapsed_total = time.time() - start_time
    minutes = elapsed_total / 60

    print(f"\n{'═' * 60}")
    print(f"TERMINÉ {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'═' * 60}")
    print(f"  Traités    : {ok}")
    print(f"  Échecs     : {fail}")
    print(f"  Ignorés    : {skipped}")
    print(f"  Durée      : {minutes:.1f} min")
    if ok > 0:
        print(f"  Moyenne    : {elapsed_total / ok:.1f}s par fichier")
    print(f"  Modèle     : {args.model_name}")
    if not args.dry_run:
        print(f"  Progression sauvegardée dans {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
