#!/usr/bin/env python3
"""
perplexity_md_to_obsidian.py
Normalise les exports .md natifs de Perplexity vers 00-Inbox/ du vault Obsidian.
Usage : python perplexity_md_to_obsidian.py [--source-dir /chemin/vers/exports]
"""

import re
import argparse
import unicodedata
from pathlib import Path
from datetime import datetime
import yaml

# ─── Configuration ────────────────────────────────────────────────────────────
VAULT_PATH         = Path.home() / "Documents" / "Obsidian Vault"
INBOX_PATH         = VAULT_PATH / "00-Inbox"
DEFAULT_SOURCE_DIR = Path.home() / "Downloads" / "perplexity-exports"
# ──────────────────────────────────────────────────────────────────────────────


def slugify(text: str, max_len: int = 70) -> str:
    """Convertit un texte en slug ASCII pour nom de fichier."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text[:max_len]


def extract_title_from_content(content: str, filename: str) -> str:
    """Extrait le titre depuis le premier H1, ou utilise le nom de fichier."""
    match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return Path(filename).stem.replace('-', ' ').replace('_', ' ').title()


def extract_existing_frontmatter(content: str) -> tuple[dict, str]:
    """
    Extrait le frontmatter YAML existant s'il y en a un.
    Retourne (dict_frontmatter, contenu_sans_frontmatter).
    """
    if content.startswith('---'):
        end = content.find('---', 3)
        if end != -1:
            fm_str = content[3:end].strip()
            body   = content[end+3:].strip()
            try:
                fm = yaml.safe_load(fm_str) or {}
                return fm, body
            except yaml.YAMLError:
                pass
    return {}, content


def build_normalized_frontmatter(existing_fm: dict, title: str,
                                  body: str, source_file: Path) -> dict:
    """Construit le frontmatter normalisé à partir des données disponibles."""

    # Tente de récupérer la date depuis le frontmatter existant ou le nom de fichier
    date_created = existing_fm.get('date') or existing_fm.get('created') or ''
    if not date_created:
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', source_file.stem)
        date_created = date_match.group(1) if date_match else datetime.now().strftime('%Y-%m-%d')

    return {
        'title':           title,
        'date_created':    str(date_created),
        'date_imported':   datetime.now().isoformat(timespec='seconds'),
        'source':          'perplexity',
        'model':           existing_fm.get('model', 'perplexity-unknown'),
        'conversation_id': existing_fm.get('id') or existing_fm.get('conversation_id') or '',
        'tags':            [],
        'keywords':        [],
        'topic':           '',
        'status':          'inbox',
        'processed':       False,
        'word_count':      len(body.split()),
        'summary':         '',
        'original_file':   source_file.name,
    }


def process_perplexity_md(source_file: Path, inbox_path: Path,
                           dry_run: bool = False) -> Path | None:
    """Traite un fichier .md Perplexity et le dépose dans l'Inbox."""

    print(f"  → Traitement : {source_file.name}")

    raw_content  = source_file.read_text(encoding='utf-8')
    existing_fm, body = extract_existing_frontmatter(raw_content)
    title        = extract_title_from_content(body, source_file.name)
    fm           = build_normalized_frontmatter(existing_fm, title, body, source_file)

    # Contenu final
    fm_yaml       = yaml.dump(fm, allow_unicode=True, default_flow_style=False,
                               sort_keys=False)
    final_content = f"---\n{fm_yaml}---\n\n{body}\n"

    # Nom de fichier — format cohérent avec claude et lechat
    date_prefix  = fm['date_created']
    slug_title   = slugify(title)
    out_filename = f"{date_prefix}_perplexity_{slug_title}.md"
    out_path     = inbox_path / out_filename

    # Gestion des doublons
    counter = 1
    while out_path.exists():
        out_path = inbox_path / f"{date_prefix}_perplexity_{slug_title}_{counter}.md"
        counter += 1

    if dry_run:
        print(f"    [DRY RUN] Serait créé : {out_path.name}")
        return None

    out_path.write_text(final_content, encoding='utf-8')
    print(f"    ✓ Créé : {out_path.name}")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description='Import exports .md Perplexity vers Obsidian Inbox'
    )
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f'Dossier source (défaut: {DEFAULT_SOURCE_DIR})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simule sans créer de fichiers')
    parser.add_argument('--move', action='store_true',
                        help='Déplace (au lieu de copier) les fichiers traités')
    args = parser.parse_args()

    if not args.source_dir.exists():
        args.source_dir.mkdir(parents=True)
        print(f"Dossier source créé : {args.source_dir}")
        print("Déposez vos exports .md Perplexity dans ce dossier, puis relancez.")
        return

    INBOX_PATH.mkdir(parents=True, exist_ok=True)

    md_files = sorted(args.source_dir.glob('*.md'))
    if not md_files:
        print(f"Aucun fichier .md trouvé dans {args.source_dir}")
        return

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Traitement de {len(md_files)} fichier(s)...\n")

    processed, errors = [], []

    for md_file in md_files:
        try:
            out = process_perplexity_md(md_file, INBOX_PATH, args.dry_run)
            if out:
                processed.append((md_file, out))
                if args.move and not args.dry_run:
                    md_file.unlink()
        except Exception as e:
            print(f"    ✗ Erreur sur {md_file.name} : {e}")
            errors.append(md_file)

    print(f"\n{'─'*50}")
    print(f"✓ {len(processed)} fichier(s) importé(s) dans {INBOX_PATH}")
    if errors:
        print(f"✗ {len(errors)} erreur(s)")


if __name__ == '__main__':
    main()
