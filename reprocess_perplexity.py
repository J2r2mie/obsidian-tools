#!/usr/bin/env python3
"""
reprocess_perplexity.py  v2
Retraite les fichiers Perplexity bruts pour extraire les conversations complètes,
tout en préservant les métadonnées enrichies (topic, ai_summary, etc.) du vault.

Pipeline :
  1. Lit le fichier brut (RAW_DIR) → extrait les tours Human/Perplexity depuis le JSON
  2. Lit le fichier enrichi existant (VAULT_DIR) → récupère tout le frontmatter
  3. Fusionne : frontmatter enrichi + contenu Markdown propre
  4. Écrit le résultat dans VAULT_DIR

Usage :
  python reprocess_perplexity.py --dry-run     # Diagnostic sans écriture
  python reprocess_perplexity.py               # Mode écriture
  python reprocess_perplexity.py --test 5      # Teste sur 5 fichiers

Dépendances :
  pip install python-frontmatter
"""

import argparse
import json
import re
import shutil
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import frontmatter
import yaml

# ==============================================================================
# CONFIGURATION — adapter ces chemins
# ==============================================================================

RAW_DIR   = Path("/Users/jeremie/Desktop/RAW_perplexity")
VAULT_DIR = Path("/Users/jeremie/Documents/Obsidian Vault/Perplexity")
BACKUP    = True
BACKUP_DIR = VAULT_DIR.parent / "_backup_reprocess"

# ==============================================================================
# EXTRACTION DES TOURS DEPUIS LE JSON BRUT
# ==============================================================================

def extract_json_blocks(text):
    """Extrait les blocs JSON (arrays [...]) du texte brut."""
    blocks, depth, start = [], 0, None
    in_string, escape_next = False, False
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(text[start:i + 1])
                start = None
    return blocks


def parse_answer(raw):
    """Extrait le texte de réponse depuis différents formats."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get("answer", raw)
        except Exception:
            pass
        return raw
    if isinstance(raw, dict):
        return raw.get("answer", str(raw))
    return str(raw) if raw else ""


def extract_turns(raw_content):
    """Extrait les tours (question, réponse) depuis le contenu brut."""
    # Chercher dans le body (après le frontmatter)
    parts = raw_content.split("---", 2)
    body = parts[2] if len(parts) >= 3 else raw_content

    turns = []
    for block_str in extract_json_blocks(body):
        try:
            steps = json.loads(block_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(steps, list):
            continue

        human_msg, ai_answer = None, None
        for step in steps:
            if not isinstance(step, dict):
                continue
            stype   = step.get("step_type", "")
            content = step.get("content", {}) or {}
            if stype == "INITIAL_QUERY":
                human_msg = content.get("query", "").strip()
            elif stype == "FINAL":
                ai_answer = parse_answer(content.get("answer", "")).strip()

        if human_msg and ai_answer:
            turns.append((human_msg, ai_answer))

    return turns


# ==============================================================================
# SÉRIALISATION YAML (même logique que enrich_vault.py)
# ==============================================================================

YAML_KEY_ORDER = [
    "title", "date", "updated", "tags", "source",
    "conversation_id", "thread_id", "slug", "model",
    "nb_messages",
    "topic", "language", "word_count",
    "importance", "content_type", "triage_score", "triage_date",
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
# TRAITEMENT D'UN FICHIER
# ==============================================================================

def process_file(raw_path: Path, vault_path: Path, dry_run: bool = False) -> dict:
    """
    Retraite un fichier Perplexity brut.
    - Extrait les tours depuis le fichier brut
    - Préserve le frontmatter enrichi du fichier vault existant
    - Écrit le résultat propre
    """
    raw_content = raw_path.read_text(encoding="utf-8", errors="replace")
    turns = extract_turns(raw_content)

    result = {
        "file": raw_path.name,
        "turns": len(turns),
        "status": "ok" if turns else "no_turns",
        "preserved_fields": 0,
    }

    if dry_run:
        return result

    if not turns:
        # Pas de tours : copier le brut tel quel (format ancien)
        shutil.copy2(raw_path, vault_path)
        return result

    # Lire le frontmatter enrichi existant dans le vault
    existing_metadata = {}
    if vault_path.exists():
        try:
            existing_post = frontmatter.loads(vault_path.read_text(encoding="utf-8"))
            existing_metadata = dict(existing_post.metadata)
            result["preserved_fields"] = len(existing_metadata)
        except Exception:
            pass

    # Lire le frontmatter du fichier brut (comme base)
    try:
        raw_post = frontmatter.loads(raw_content)
        base_metadata = dict(raw_post.metadata)
    except Exception:
        # Fallback : extraire le frontmatter par regex
        match = re.match(r"^---\n(.*?)\n---", raw_content, re.DOTALL)
        base_metadata = {}
        if match:
            try:
                base_metadata = yaml.safe_load(match.group(1)) or {}
            except Exception:
                pass

    # Fusionner : base (brut) + enrichi (vault) — l'enrichi a priorité
    merged = {}
    merged.update(base_metadata)
    merged.update(existing_metadata)

    # Recalculer le word_count sur le nouveau contenu propre
    body_text = ""
    for human, perplexity in turns:
        body_text += human + " " + perplexity + " "
    merged["word_count"] = len(re.findall(r"\w+", body_text))

    # Construire le body Markdown propre
    title = str(merged.get("title", raw_path.stem)).strip()
    body_parts = [f"# {title}\n"]
    for human, perplexity in turns:
        body_parts.append(f"\n## Human\n\n{human}\n\n---\n\n## Perplexity\n\n{perplexity}\n\n---\n")

    # Assembler le post final
    post = frontmatter.Post("")
    post.metadata = merged
    post.content = "\n".join(body_parts)

    # Écrire avec l'ordre YAML préservé
    output = dump_post_preserving_order(post)
    vault_path.write_text(output, encoding="utf-8")

    return result


# ==============================================================================
# MAIN
# ==============================================================================

def build_parser():
    p = argparse.ArgumentParser(
        description="Retraite les fichiers Perplexity bruts en préservant les métadonnées enrichies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dry-run", "-n", action="store_true",
                   help="Mode diagnostic : aucun fichier modifié")
    p.add_argument("--test", type=int, metavar="N",
                   help="Traite seulement les N premiers fichiers")
    p.add_argument("--raw-dir", default=str(RAW_DIR),
                   help=f"Dossier des fichiers bruts (défaut: {RAW_DIR})")
    p.add_argument("--vault-dir", default=str(VAULT_DIR),
                   help=f"Dossier vault Obsidian (défaut: {VAULT_DIR})")
    p.add_argument("--no-backup", action="store_true",
                   help="Ne pas faire de backup avant écriture")
    return p


def main():
    args = build_parser().parse_args()
    raw_dir = Path(args.raw_dir).expanduser()
    vault_dir = Path(args.vault_dir).expanduser()

    if not raw_dir.exists():
        print(f"Dossier brut introuvable : {raw_dir}", file=sys.stderr)
        sys.exit(1)

    raw_files = sorted(raw_dir.glob("*.md"))
    if not raw_files:
        print(f"Aucun fichier .md dans {raw_dir}")
        return

    if args.test:
        raw_files = raw_files[:args.test]

    # Backup
    if not args.dry_run and not args.no_backup and BACKUP:
        backup_dir = Path(str(BACKUP_DIR))
        if not backup_dir.exists():
            print(f"Sauvegarde du vault Perplexity dans {backup_dir} ...")
            shutil.copytree(vault_dir, backup_dir)
            print("Sauvegarde OK.\n")

    # Compter combien de fichiers ont un enrichissement existant
    vault_files = {f.name for f in vault_dir.glob("*.md")} if vault_dir.exists() else set()
    matching = sum(1 for f in raw_files if f.name in vault_files)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Traitement de {len(raw_files)} fichiers bruts...")
    print(f"  {matching} fichiers ont un enrichissement existant dans le vault.\n")

    total_ok = total_no_turns = total_multi = total_preserved = 0
    issues = []

    for raw_path in raw_files:
        vault_path = vault_dir / raw_path.name
        res = process_file(raw_path, vault_path, args.dry_run)

        if res["status"] == "ok":
            total_ok += 1
            if res["turns"] > 1:
                total_multi += 1
            preserved = res.get("preserved_fields", 0)
            if preserved > 0:
                total_preserved += 1
            status = f"OK  {res['turns']} tours"
            if preserved:
                status += f" | {preserved} champs préservés"
        else:
            total_no_turns += 1
            issues.append(res["file"])
            status = "COPIE BRUTE (pas de tours)"

        if args.dry_run:
            print(f"  [{status:<45}] {raw_path.name[:60]}")
        else:
            print(f"  [{status:<45}] {raw_path.name[:60]}")

    # Résumé
    print(f"\n{'─' * 60}")
    print(f"  Total traités       : {len(raw_files)}")
    print(f"  OK (tours extraits) : {total_ok}")
    print(f"  Multi-tours         : {total_multi}")
    print(f"  Copies brutes       : {total_no_turns}")
    print(f"  Enrichissement préservé : {total_preserved} fichiers")

    if args.dry_run:
        print(f"\n  Mode DRY RUN — aucun fichier écrit.")
        print(f"  Relance sans --dry-run pour appliquer.")
    else:
        print(f"\n  Fichiers écrits dans : {vault_dir}")
        if BACKUP and not args.no_backup:
            print(f"  Sauvegarde dans      : {BACKUP_DIR}")

    if issues:
        print(f"\n  Fichiers copiés bruts (format non reconnu) :")
        for f in issues[:10]:
            print(f"    - {f}")
        if len(issues) > 10:
            print(f"    ... et {len(issues) - 10} autres.")


if __name__ == "__main__":
    main()
