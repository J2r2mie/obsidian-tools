#!/usr/bin/env python3
"""
lechat_to_obsidian.py  v2
Convertit les exports JSON de Le Chat (Mistral) en fichiers Markdown pour Obsidian.
Dépose les fichiers dans 00-Inbox/ avec un frontmatter normalisé.
Utilise export_state.json pour la déduplication et la date de référence.

Usage:
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/ --dry-run
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/ --date-from 2026-01-01
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/ --overwrite

Structure JSON attendue (un fichier par conversation) :
    [
      {"role": "user",      "content": "...", "createdAt": "2025-02-10T08:34:18.173Z", ...},
      {"role": "assistant", "content": "...", "createdAt": "2025-02-10T08:34:19.343Z", ...},
      ...
    ]
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Import du gestionnaire d'état (même dossier que ce script)
try:
    from state_manager import (
        load_state, save_state,
        get_last_import, get_imported_ids,
        update_state, print_import_context,
    )
    STATE_AVAILABLE = True
except ImportError:
    print("⚠ state_manager.py introuvable — déduplication par ID désactivée.")
    STATE_AVAILABLE = False


# ─── CONFIGURATION ────────────────────────────────────────────────────────────

DEFAULT_OUTPUT = Path.home() / "Documents" / "Obsidian Vault" / "00-Inbox"

# Dictionnaire thématique — hint conservé dans le frontmatter pour inbox_processor
TOPIC_KEYWORDS = {
    "polymeres-recyclage": [
        "polymere", "polymer", "recyclage", "recycling", "plastique", "elastomere",
        "caoutchouc", "silicone", "epdm", "composite", "devulcanisation",
        "rheologie", "extrusion", "mise en oeuvre",
    ],
    "pkm-workflow": [
        "obsidian", "pkm", "zettelkasten", "workflow", "vault", "note", "knowledge",
        "perplexity", "claude", "mistral", "lechat", "dataview", "templater",
    ],
    "tech-dev": [
        "python", "script", "api", "github", "docker", "sql", "javascript",
        "bash", "terminal", "code", "fonction", "programmation",
    ],
    "ia-llm": [
        "llm", "ollama", "gpt", "prompt", "embeddings", "rag", "fine-tuning",
        "intelligence artificielle", "language model", "mistral", "gemini",
    ],
    "pedagogie-recherche": [
        "cours", "etudiant", "enseignement", "these", "publication", "bibliographie",
        "article", "recherche", "formation", "imt", "tp", "td",
    ],
    "apple-macos": [
        "iphone", "ipad", "mac", "macos", "ios", "icloud", "apple", "shortcut",
        "finder", "safari",
    ],
    "sante-sport": [
        "velo", "sport", "entrainement", "sante", "nutrition", "cardio", "cycling",
        "kalkhoff",
    ],
    "budget-finance": [
        "budget", "banque", "impot", "finance", "depense", "facture", "assurance",
    ],
    "perso-general": [],  # fallback
}

# ──────────────────────────────────────────────────────────────────────────────


def slugify(text: str, max_len: int = 70) -> str:
    """Convertit un texte en slug ASCII pour nom de fichier."""
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = text.strip("-")
    return text[:max_len]


def parse_date(date_str: str) -> datetime:
    """Parse une date ISO 8601 avec ou sans milliseconde."""
    date_str = re.sub(r"\.\d+Z$", "Z", date_str)
    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def detect_topic(title: str, body: str) -> str:
    """Détecte le topic thématique par comptage de mots-clés pondérés."""
    text_title = unicodedata.normalize("NFD", title.lower()).encode("ascii", "ignore").decode()
    text_body  = unicodedata.normalize("NFD", body.lower()).encode("ascii", "ignore").decode()

    scores = {topic: 0 for topic in TOPIC_KEYWORDS}
    for topic, keywords in TOPIC_KEYWORDS.items():
        for kw in keywords:
            scores[topic] += len(re.findall(r'\b' + re.escape(kw) + r'\b', text_title)) * 3
            scores[topic] += len(re.findall(r'\b' + re.escape(kw) + r'\b', text_body))

    best = max(scores, key=lambda t: scores[t])
    return best if scores[best] >= 3 else "perso-general"


def extract_conversation(messages: list) -> tuple[str, str, str, int, int]:
    """
    Extrait depuis la liste de messages :
    - title      : premier message utilisateur tronqué
    - date_str   : date ISO du premier message
    - body_md    : contenu Markdown de la conversation
    - num_turns  : nombre de tours (messages user)
    - word_count : nombre de mots du body
    """
    if not messages:
        raise ValueError("Conversation vide")

    # Date du premier message
    first_date = parse_date(messages[0]["createdAt"])
    date_str   = first_date.strftime("%Y-%m-%dT%H:%M:%S")

    # Titre = premier message utilisateur (120 chars max)
    first_user = next((m["content"] for m in messages if m["role"] == "user"), "sans-titre")
    title = first_user.strip().replace("\n", " ")[:120]

    # Corps Markdown
    lines     = []
    num_turns = 0
    for msg in messages:
        role    = msg.get("role", "")
        content = msg.get("content", "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"## Human\n\n{content}\n")
            num_turns += 1
        elif role == "assistant":
            lines.append(f"## Le Chat\n\n{content}\n")

    body_md    = "\n---\n\n".join(lines)
    word_count = len(body_md.split())

    return title, date_str, body_md, num_turns, word_count


def build_frontmatter(title: str, date_str: str, chat_id: str,
                      num_turns: int, word_count: int, topic: str) -> str:
    """Génère le bloc YAML frontmatter normalisé (schéma Inbox)."""
    safe_title   = title.replace('"', "'")
    date_imported = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return f"""---
title: "{safe_title}"
date_created: {date_str}
date_imported: {date_imported}
source: lechat
conversation_id: {chat_id}
tags: []
keywords: []
topic: {topic}
status: inbox
processed: false
num_turns: {num_turns}
word_count: {word_count}
summary: ""
---"""


def process_file(
    json_path: Path,
    output_dir: Path,
    imported_ids: set,
    dry_run: bool,
    date_from: datetime | None,
    overwrite: bool,
) -> tuple[str, str, str | None]:
    """
    Traite un fichier JSON Le Chat.
    Retourne (statut, message_affichage, chat_id_si_ok).
    Statuts : "ok", "skip_date", "skip_id", "error"
    """
    # ── Lecture JSON ──────────────────────────────────────────────────────────
    try:
        with open(json_path, encoding="utf-8") as f:
            messages = json.load(f)
    except Exception as e:
        return "error", f"{json_path.name} : {e}", None

    if not isinstance(messages, list) or not messages:
        return "error", f"{json_path.name} : format inattendu (pas une liste)", None

    # ── Extraction du chat_id depuis le nom de fichier (chat-<uuid>.json) ────
    chat_id = json_path.stem
    if chat_id.startswith("chat-"):
        chat_id = chat_id[5:]

    # ── Déduplication par ID ──────────────────────────────────────────────────
    if chat_id in imported_ids:
        return "skip_id", json_path.name, None

    # ── Extraction des données ────────────────────────────────────────────────
    try:
        title, date_str, body_md, num_turns, word_count = extract_conversation(messages)
    except Exception as e:
        return "error", f"{json_path.name} : {e}", None

    # ── Filtre par date ───────────────────────────────────────────────────────
    if date_from:
        conv_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        if conv_date < date_from:
            return "skip_date", json_path.name, None

    # ── Détection du topic (hint pour inbox_processor) ────────────────────────
    topic = detect_topic(title, body_md)

    # ── Nom du fichier de sortie ──────────────────────────────────────────────
    filename = f"{date_str[:10]}_lechat_{slugify(title)}.md"
    out_path = output_dir / filename

    # Gestion des doublons de nom de fichier (cas rare)
    counter = 1
    while out_path.exists() and not overwrite:
        out_path = output_dir / f"{date_str[:10]}_lechat_{slugify(title)}_{counter}.md"
        counter += 1

    # ── Construction du contenu ───────────────────────────────────────────────
    fm           = build_frontmatter(title, date_str, chat_id, num_turns, word_count, topic)
    full_content = f"{fm}\n\n# {title}\n\n{body_md}\n"

    if dry_run:
        info = f"[DRY-RUN] {filename} ({num_turns} tours, {word_count} mots, topic: {topic})"
        return "ok", info, chat_id

    # ── Écriture ──────────────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return "ok", f"{filename} ({num_turns} tours, {word_count} mots)", chat_id


def main():
    parser = argparse.ArgumentParser(
        description="Convertit les exports JSON Le Chat en Markdown pour Obsidian (v2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-i", "--input", required=True,
                        help="Dossier contenant les fichiers JSON Le Chat (un par conversation)")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT),
                        help=f"Dossier de sortie (défaut : {DEFAULT_OUTPUT})")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Simule sans écrire aucun fichier")
    parser.add_argument("--date-from", default=None,
                        help="N'importe que les conversations après cette date (YYYY-MM-DD). "
                             "Ignoré si --all est passé.")
    parser.add_argument("--all",       action="store_true",
                        help="Importe tout sans filtre de date (ignore export_state.json)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Écrase les fichiers existants si même nom")
    args = parser.parse_args()

    input_dir  = Path(args.input).expanduser()
    output_dir = Path(args.output).expanduser()

    if not input_dir.exists():
        print(f"Erreur : le dossier d'entrée n'existe pas : {input_dir}")
        sys.exit(1)

    # ── Chargement de l'état ──────────────────────────────────────────────────
    state        = load_state() if STATE_AVAILABLE else {}
    imported_ids = get_imported_ids(state, "lechat") if STATE_AVAILABLE else set()
    last_import  = get_last_import(state, "lechat") if STATE_AVAILABLE else None

    # ── Résolution de la date de référence ───────────────────────────────────
    if args.all:
        date_from = None
        print("\n── Mode --all : aucun filtre de date ──────────────────────")
    elif args.date_from:
        date_from = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        print_import_context("lechat", last_import, args.date_from)
    else:
        date_from = last_import  # peut être None → import de tout avec avertissement
        print_import_context("lechat", last_import, None)

    # ── Collecte des fichiers JSON ────────────────────────────────────────────
    json_files = sorted(input_dir.glob("*.json"))
    if not json_files:
        print(f"Aucun fichier .json trouvé dans {input_dir}")
        sys.exit(1)

    mode_label = "[DRY-RUN] " if args.dry_run else ""
    print(f"{mode_label}Traitement de {len(json_files)} fichiers JSON")
    print(f"Déjà importés (index) : {len(imported_ids)} conversation(s)")
    print(f"Sortie : {output_dir}\n")

    counts   = {"ok": 0, "skip_date": 0, "skip_id": 0, "error": 0}
    new_ids  = []

    for json_path in json_files:
        status, info, chat_id = process_file(
            json_path, output_dir,
            imported_ids=imported_ids,
            dry_run=args.dry_run,
            date_from=date_from,
            overwrite=args.overwrite,
        )
        counts[status] += 1
        if status == "ok" and chat_id:
            new_ids.append(chat_id)

        prefix = {
            "ok":        "  [ok      ]",
            "skip_date": "  [date    ]",
            "skip_id":   "  [déjà vu ]",
            "error":     "  [ERREUR  ]",
        }[status]
        print(f"{prefix} {info}")

    # ── Résumé ────────────────────────────────────────────────────────────────
    print(f"""
────────────────────────────────────────
  Total            : {len(json_files)}
  Importés         : {counts['ok']}
  Ignorés (date)   : {counts['skip_date']}
  Ignorés (déjà vu): {counts['skip_id']}
  Erreurs          : {counts['error']}
────────────────────────────────────────""")

    if args.dry_run:
        print("\nMode DRY-RUN — aucun fichier écrit ni état mis à jour.")
        print("Relance sans --dry-run pour appliquer.")
        return

    # ── Mise à jour de l'état (seulement si import réel et sans erreur totale) ─
    if STATE_AVAILABLE and counts["ok"] > 0:
        state = update_state(state, "lechat", new_ids, counts["ok"])
        save_state(state)
        print(f"  État mis à jour : {counts['ok']} ID(s) ajouté(s) à l'index.")


if __name__ == "__main__":
    main()
