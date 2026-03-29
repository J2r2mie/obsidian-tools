#!/usr/bin/env python3
"""
enrich_vault.py  v2
Enrichit les fichiers Markdown exportés (Claude / Perplexity) dans le vault Obsidian.

Pour chaque fichier .md avec frontmatter YAML, le script :
  - Détecte le topic principal (keyword-matching pondéré sur tout le contenu)
  - Détecte la langue dominante (fr / en)
  - Compte les mots
  - Calcule un score de triage (importance, content_type)
  - Écrit les métadonnées enrichies dans le frontmatter

Usage :
  python enrich_vault.py --dry-run          # Diagnostic sans modification
  python enrich_vault.py                    # Mode écriture
  python enrich_vault.py --csv rapport.csv  # Exporte aussi un CSV récapitulatif
  python enrich_vault.py --backup           # Sauvegarde avant modification

Dépendances :
  pip install python-frontmatter
"""

import argparse
import csv
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import frontmatter
import yaml

# ==============================================================================
# CONFIGURATION — adapter ces chemins à ta machine
# ==============================================================================

VAULT_PATH = Path("/Users/jeremie/Documents/Obsidian Vault")

# Sous-dossiers à traiter (relatifs au vault)
SOURCE_DIRS = ["Perplexity", "Claude"]

# Dossier de backup (créé à côté du vault)
BACKUP_DIR = VAULT_PATH / "_backup_enrich"

# CSV de rapport (optionnel, activé via --csv)
DEFAULT_CSV = VAULT_PATH / "00-Index" / "enrich_report.csv"

# ==============================================================================
# TOPICS — mots-clés pondérés pour la détection thématique
#
# Format : { "topic_slug": [(mot_clé, poids), ...] }
# Le poids par défaut est 1. Les termes très spécifiques ont un poids > 1
# pour éviter les faux positifs sur des mots génériques.
# ==============================================================================

TOPIC_KEYWORDS = {
    "polymeres-recyclage": [
        ("polymere", 2), ("polymer", 2), ("recyclage", 2), ("recycling", 2),
        ("plastique", 1), ("composite", 1), ("degradation", 1),
        ("materiau", 1), ("fibre", 1), ("resine", 1), ("resin", 1),
        ("pellet", 1), ("extrusion", 2), ("thermoplastique", 2),
        ("bioplastique", 2), ("polyolefine", 3), ("polyolefin", 3),
        ("epdm", 3), ("nbr", 2), ("elastomere", 3), ("elastomer", 3),
        ("caoutchouc", 3), ("rubber", 2), ("eva", 1),
        ("moussage", 2), ("foaming", 2), ("broyage", 2), ("grinding", 2),
        ("granulometrie", 2), ("injection", 1), ("moulage", 1),
        ("compatibilisant", 3), ("compatibilizer", 3),
        ("devulcanisation", 3), ("devulcanization", 3),
    ],
    "pkm-workflow": [
        ("obsidian", 2), ("zettelkasten", 3), ("vault", 1), ("dataview", 3),
        ("templater", 3), ("second cerveau", 2), ("personal knowledge", 2),
        ("prise de notes", 2), ("atomic note", 2), ("moc", 1),
        ("map of content", 2), ("pkm", 3), ("frontmatter", 2),
    ],
    "tech-dev": [
        ("python", 1), ("script", 1), ("api", 1), ("github", 2),
        ("docker", 2), ("sql", 1), ("javascript", 1), ("bash", 1),
        ("terminal", 1), ("git", 1), ("json", 1), ("yaml", 1),
        ("html", 1), ("css", 1), ("framework", 1), ("debug", 1),
        ("regex", 2), ("pip", 1), ("homebrew", 2), ("npm", 1),
    ],
    "ia-llm": [
        ("ollama", 3), ("gpt", 1), ("prompt", 1), ("embeddings", 2),
        ("rag", 2), ("fine-tuning", 2), ("fine tuning", 2),
        ("language model", 2), ("tokens", 1), ("inference", 1),
        ("mistral", 2), ("llama", 2), ("transformer", 2),
        ("bert", 2), ("hugging face", 2), ("openai", 2),
        ("anthropic", 2), ("llm", 2), ("chatgpt", 2), ("copilot", 1),
        ("claude", 1), ("perplexity", 1),
    ],
    "pedagogie-recherche": [
        ("cours", 1), ("etudiant", 1), ("enseignement", 2), ("these", 2),
        ("publication", 1), ("bibliographie", 2), ("recherche", 1),
        ("formation", 1), ("imt", 3), ("universite", 1), ("conference", 1),
        ("review", 1), ("abstract", 1), ("latex", 2), ("zotero", 3),
        ("doctorant", 2), ("encadrement", 2), ("ademe", 3),
    ],
    "photo-media": [
        ("photo", 1), ("video", 1), ("camera", 1), ("lightroom", 3),
        ("raw", 1), ("retouche", 2), ("exposition", 1), ("objectif", 1),
        ("focale", 2), ("photographie", 2), ("lumix", 3), ("gh6", 3),
        ("premiere", 1), ("final cut", 2), ("lut", 2), ("imagej", 2),
    ],
    "sante-sport": [
        ("velo", 1), ("sport", 1), ("entrainement", 2), ("sante", 1),
        ("nutrition", 1), ("cardio", 1), ("cycling", 1), ("running", 1),
        ("watt", 1), ("ftp", 2), ("garmin", 2), ("strava", 2),
        ("ping", 1), ("pong", 1), ("tennis de table", 3),
    ],
    "apple-macos": [
        ("macos", 2), ("icloud", 2), ("shortcuts", 1), ("applescript", 3),
        ("finder", 1), ("keynote", 1), ("pages", 1), ("swift", 1),
        ("xcode", 2), ("homebrew", 2), ("mac mini", 2), ("macbook", 2),
        ("ipad", 1), ("silicon", 1), ("launchagent", 3),
    ],
    "budget-finance": [
        ("budget", 2), ("enveloppe", 2), ("depense", 1), ("revenu", 1),
        ("epargne", 2), ("compte", 1), ("banque", 1), ("salaire", 1),
        ("impot", 2), ("fiscalite", 2),
    ],
}

# Score minimum pondéré pour qu'un topic soit retenu (sinon -> "general")
MIN_TOPIC_SCORE = 3


# ==============================================================================
# DÉTECTION DE TOPIC
# ==============================================================================

def detect_topic(title: str, body: str) -> tuple[str, dict]:
    """
    Détecte le topic principal par scoring pondéré.
    Analyse le titre (poids x3) + le corps entier.
    Retourne (topic, {topic: score, ...}) pour diagnostic.
    """
    title_lower = title.lower()
    body_lower = body.lower()

    scores = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = 0
        for kw, weight in keywords:
            # Le titre compte triple
            score += len(re.findall(r'\b' + re.escape(kw) + r'\b', title_lower)) * weight * 3
            score += len(re.findall(r'\b' + re.escape(kw) + r'\b', body_lower)) * weight
        scores[topic] = score

    best_topic = max(scores, key=scores.get)
    if scores[best_topic] < 3:
        best_topic = "perso-general"

    return best_topic, scores


# ==============================================================================
# DÉTECTION DE LANGUE
# ==============================================================================

FR_MARKERS = [
    "le ", "la ", "les ", "de ", "du ", "des ", "que ", "est ", "pour ",
    "avec ", "dans ", "sur ", "par ", "une ", "son ", "qui ", "cette ",
    "peut ", "sont ", "nous ", "vous ", "mais ", "aussi ", "entre ",
]
EN_MARKERS = [
    "the ", "this ", "that ", "with ", "from ", "your ", "have ", "are ",
    "for ", "and ", "was ", "been ", "will ", "would ", "should ",
    "about ", "which ", "their ", "into ", "some ", "each ", "could ",
]


def detect_language(body: str) -> str:
    """Détecte la langue dominante par fréquence de marqueurs."""
    sample = body[:3000].lower()
    fr = sum(sample.count(m) for m in FR_MARKERS)
    en = sum(sample.count(m) for m in EN_MARKERS)
    return "fr" if fr >= en else "en"


# ==============================================================================
# COMPTAGE DE MOTS
# ==============================================================================

def count_words(text: str) -> int:
    return len(re.findall(r"\w+", text))


# ==============================================================================
# SCORING DE TRIAGE (repris de triage_vault.py, légèrement affiné)
# ==============================================================================

def compute_triage(content: str, char_count: int) -> dict:
    """
    Calcule un score d'importance et un type de contenu.
    Score sur 13 points.
    """
    code_blocks = len(re.findall(r"```", content)) // 2
    turns = len(re.findall(r"^## (?:Human|Claude|Perplexity)", content, re.MULTILINE))
    table_rows = len(re.findall(r"^\|.+\|", content, re.MULTILINE))
    bullet_lines = len(re.findall(r"^[\-\*] .+", content, re.MULTILINE))

    s = 0

    # Longueur (0–5 pts)
    if   char_count > 30_000: s += 5
    elif char_count > 15_000: s += 4
    elif char_count >  7_000: s += 3
    elif char_count >  3_000: s += 2
    elif char_count >  1_000: s += 1

    # Blocs de code (0–3 pts)
    if   code_blocks >= 6: s += 3
    elif code_blocks >= 3: s += 2
    elif code_blocks >= 1: s += 1

    # Nombre de tours (0–3 pts)
    if   turns >= 7: s += 3
    elif turns >= 4: s += 2
    elif turns >= 2: s += 1

    # Tableaux et listes structurées (0–2 pts)
    if table_rows >= 3:   s += 1
    if bullet_lines >= 5: s += 1

    # Importance
    if   s >= 8: importance = "high"
    elif s >= 4: importance = "medium"
    else:        importance = "low"

    # Type de contenu
    if code_blocks >= 2:
        content_type = "code"
    elif turns >= 4 and char_count > 7_000:
        content_type = "research"
    elif turns <= 1 and char_count < 3_000:
        content_type = "factual"
    elif turns >= 2 and code_blocks == 0:
        content_type = "brainstorm"
    else:
        content_type = "general"

    return {
        "importance":   importance,
        "content_type": content_type,
        "triage_score": s,
        "turns":        turns,
        "code_blocks":  code_blocks,
    }


# ==============================================================================
# SÉRIALISATION YAML (préservation de l'ordre et du format de date)
# ==============================================================================

# Ordre souhaité des clés dans le frontmatter
# Les clés non listées ici sont ajoutées à la fin dans l'ordre d'apparition.
YAML_KEY_ORDER = [
    "title", "date", "updated", "tags", "source",
    "conversation_id", "thread_id", "slug", "model",
    "nb_messages",
    # Champs ajoutés par enrich_vault
    "topic", "language", "word_count",
    "importance", "content_type", "triage_score", "triage_date",
]


def _yaml_representer_str(dumper, data):
    """Force les guillemets doubles sur les strings contenant des caractères spéciaux."""
    if any(c in data for c in (':', '#', '{', '}', '[', ']', "'", '"', '\n')):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


def _yaml_representer_datetime(dumper, data):
    """Préserve le format ISO avec le T séparateur."""
    return dumper.represent_scalar('tag:yaml.org,2002:str', data.strftime("%Y-%m-%dT%H:%M:%S"))


def dump_post_preserving_order(post) -> str:
    """
    Sérialise un objet frontmatter.Post en préservant :
    - L'ordre logique des clés (YAML_KEY_ORDER)
    - Le format ISO des dates (avec T)
    - Les guillemets sur le title
    """
    # Construire un OrderedDict selon YAML_KEY_ORDER
    from collections import OrderedDict
    metadata = OrderedDict()
    all_keys = set(post.metadata.keys())

    # D'abord les clés dans l'ordre défini
    for key in YAML_KEY_ORDER:
        if key in all_keys:
            metadata[key] = post.metadata[key]
            all_keys.discard(key)

    # Puis les clés restantes dans leur ordre d'apparition
    for key in post.metadata:
        if key in all_keys:
            metadata[key] = post.metadata[key]

    # Dumper YAML personnalisé
    class OrderedDumper(yaml.SafeDumper):
        pass

    # Préserver l'ordre des OrderedDict
    OrderedDumper.add_representer(
        OrderedDict,
        lambda dumper, data: dumper.represent_mapping('tag:yaml.org,2002:map', data.items())
    )
    # Format datetime avec T
    OrderedDumper.add_representer(datetime, _yaml_representer_datetime)
    # Strings spéciales entre guillemets
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
# TRAITEMENT D'UN FICHIER
# ==============================================================================

def enrich_file(filepath: Path, dry_run: bool = False) -> dict | None:
    """
    Enrichit un fichier Markdown avec les métadonnées calculées.
    Retourne un dict de métriques pour le rapport CSV, ou None si skip.
    """
    try:
        raw = filepath.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ERR lecture {filepath.name} : {e}")
        return None

    # Vérifier la présence d'un frontmatter
    if not raw.startswith("---"):
        print(f"  SKIP (pas de frontmatter) : {filepath.name}")
        return None

    try:
        post = frontmatter.loads(raw)
    except Exception as e:
        print(f"  ERR frontmatter {filepath.name} : {e}")
        return None

    title = str(post.get("title", "") or "")
    body = post.content
    char_count = len(body)

    # Détection topic
    topic, topic_scores = detect_topic(title, body)
    old_topic = post.get("topic", "")

    # Détection langue
    language = detect_language(body)

    # Comptage mots
    word_count = count_words(body)

    # Triage
    triage = compute_triage(body, char_count)

    # Déterminer la source depuis le frontmatter ou le chemin
    source = post.get("source", "")
    if not source:
        parent = filepath.parent.name.lower()
        if "perplexity" in parent:
            source = "perplexity"
        elif "claude" in parent:
            source = "claude"
        else:
            source = "unknown"

    # Rapport
    report = {
        "source":       source,
        "filename":     filepath.name,
        "title":        title[:80],
        "date":         str(post.get("date", "")),
        "topic_old":    old_topic,
        "topic_new":    topic,
        "topic_changed": "→" if old_topic and old_topic != topic else "",
        "language":     language,
        "word_count":   word_count,
        "importance":   triage["importance"],
        "content_type": triage["content_type"],
        "triage_score": triage["triage_score"],
        "turns":        triage["turns"],
        "code_blocks":  triage["code_blocks"],
        "char_count":   char_count,
    }

    if dry_run:
        changed = "→" if (old_topic and old_topic != topic) else " "
        print(f"  {old_topic or '(vide)':<22} {changed} {topic:<22} "
              f"| {triage['importance']:<6} | {language} | {word_count:>5}w | {title[:45]}")
        return report

    # Écriture des métadonnées dans le frontmatter
    post["topic"] = topic
    post["language"] = language
    post["word_count"] = word_count
    post["importance"] = triage["importance"]
    post["content_type"] = triage["content_type"]
    post["triage_score"] = triage["triage_score"]
    post["triage_date"] = datetime.today().strftime("%Y-%m-%d")

    # Écriture du fichier avec préservation de l'ordre et du format de date
    try:
        output = dump_post_preserving_order(post)
        filepath.write_text(output, encoding="utf-8")
        print(f"  OK  {filepath.name:<55} topic={topic} | {triage['importance']} | {language}")
    except Exception as e:
        print(f"  ERR ecriture {filepath.name} : {e}")
        return None

    return report


# ==============================================================================
# MAIN
# ==============================================================================

def build_parser():
    p = argparse.ArgumentParser(
        description="Enrichit les fichiers Markdown du vault Obsidian (topic, langue, triage).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dry-run", "-n", action="store_true",
                   help="Mode diagnostic : aucun fichier modifié")
    p.add_argument("--backup", "-b", action="store_true",
                   help="Sauvegarde les dossiers source avant modification")
    p.add_argument("--csv", nargs="?", const=str(DEFAULT_CSV), default=None,
                   help="Exporte un CSV récapitulatif (chemin optionnel)")
    p.add_argument("--vault", default=str(VAULT_PATH),
                   help=f"Chemin du vault Obsidian (défaut: {VAULT_PATH})")
    p.add_argument("--dirs", nargs="*", default=SOURCE_DIRS,
                   help=f"Sous-dossiers à traiter (défaut: {SOURCE_DIRS})")
    return p


def main():
    args = build_parser().parse_args()
    vault = Path(args.vault).expanduser()

    if not vault.exists():
        print(f"Vault introuvable : {vault}", file=sys.stderr)
        sys.exit(1)

    # Backup
    if args.backup and not args.dry_run:
        backup_dir = vault / "_backup_enrich"
        if not backup_dir.exists():
            print(f"Sauvegarde dans {backup_dir} ...")
            for d in args.dirs:
                src = vault / d
                if src.exists():
                    dest = backup_dir / d
                    shutil.copytree(src, dest, dirs_exist_ok=True)
                    print(f"  {d} → {dest}")
            print("Sauvegarde OK.\n")

    # En-tête console
    if args.dry_run:
        print("\nMODE DIAGNOSTIC — aucun fichier modifié")
        print(f"{'ANCIEN TOPIC':<22}   {'NOUVEAU TOPIC':<22} | {'IMP':<6} | LG | {'MOTS':>5}  | TITRE")
        print("─" * 100)
    else:
        print("\nMODE ÉCRITURE — enrichissement en cours...")

    all_reports = []

    for dir_name in args.dirs:
        directory = vault / dir_name
        if not directory.exists():
            print(f"\nRépertoire introuvable : {directory}")
            continue

        files = sorted(directory.glob("*.md"))
        print(f"\n{'─' * 60}")
        print(f"{dir_name} — {len(files)} fichiers")
        print(f"{'─' * 60}")

        for f in files:
            report = enrich_file(f, dry_run=args.dry_run)
            if report:
                all_reports.append(report)

    # Résumé
    total = len(all_reports)
    if total == 0:
        print("\nAucun fichier traité.")
        return

    high   = sum(1 for r in all_reports if r["importance"] == "high")
    medium = sum(1 for r in all_reports if r["importance"] == "medium")
    low    = sum(1 for r in all_reports if r["importance"] == "low")
    changed = sum(1 for r in all_reports if r["topic_changed"])

    # Répartition par topic
    topic_counts = {}
    for r in all_reports:
        t = r["topic_new"]
        topic_counts[t] = topic_counts.get(t, 0) + 1

    print(f"\n{'═' * 60}")
    print(f"RÉSULTAT {'(DRY RUN)' if args.dry_run else '(fichiers modifiés)'}")
    print(f"{'═' * 60}")
    print(f"  Total        : {total}")
    print(f"  High         : {high}  ({high * 100 // total}%)")
    print(f"  Medium       : {medium}  ({medium * 100 // total}%)")
    print(f"  Low          : {low}  ({low * 100 // total}%)")
    if changed:
        print(f"  Topics modifiés : {changed}")
    print()
    print("  Répartition par topic :")
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
        bar = "█" * (count * 30 // total)
        print(f"    {topic:<25} {count:>3}  {bar}")

    # Export CSV
    if args.csv:
        csv_path = Path(args.csv).expanduser()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(all_reports[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_reports)
        print(f"\n  CSV exporté : {csv_path}")

    if args.dry_run:
        print("\nSi les résultats semblent corrects, relance sans --dry-run.")
        print("Ajoute --backup pour sauvegarder avant modification.")


if __name__ == "__main__":
    main()
