#!/usr/bin/env python3
"""
lechat_to_obsidian.py
Convertit les exports JSON de Le Chat (Mistral) en fichiers Markdown pour Obsidian.

Usage:
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/ -o ~/Documents/"Obsidian Vault"/40-Sources/Conversations-IA/
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/ -o ... --dry-run
    python3 lechat_to_obsidian.py -i ~/Downloads/lechat_export/ -o ... --date-from 2026-01-01

Structure JSON attendue (un fichier par conversation) :
    [
      {"role": "user",      "content": "...", "createdAt": "2025-02-10T08:34:18.173Z", ...},
      {"role": "assistant", "content": "...", "createdAt": "2025-02-10T08:34:19.349Z", ...},
      ...
    ]
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


# ─── CONFIGURATION ────────────────────────────────────────────────────────────

# Dossier de sortie par défaut (peut être surchargé via -o)
DEFAULT_OUTPUT = Path.home() / "Documents" / "Obsidian Vault" / "40-Sources" / "Conversations-IA"

# Dictionnaire thématique — même logique que enrich_vault.py
TOPIC_KEYWORDS = {
    "polymeres-recyclage": [
        "polymere", "polymer", "recyclage", "recycling", "plastique", "elastomere",
        "caoutchouc", "silicone recyclage", "epdm", "composite", "devulcanisation", "materiaux",
        "epi", "rheologie", "extrusion", "mise en oeuvre",
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
            scores[topic] += len(re.findall(r'\b' + re.escape(kw) + r'\b', text_title)) * 3  # titre poids x3
            scores[topic] += len(re.findall(r'\b' + re.escape(kw) + r'\b', text_body))

    best = max(scores, key=lambda t: scores[t])
    return best if scores[best] >= 3 else "perso-general"


def extract_conversation(messages: list) -> tuple[str, str, str, str, int, int]:
    """
    Extrait depuis la liste de messages :
    - title      : premier message utilisateur tronqué
    - date_str   : date ISO du premier message
    - month_dir  : sous-dossier YYYY-MM
    - body_md    : contenu Markdown de la conversation
    - num_turns  : nombre de tours (paires user/assistant)
    - word_count : nombre de mots du body
    """
    if not messages:
        raise ValueError("Conversation vide")

    # Date du premier message
    first_date = parse_date(messages[0]["createdAt"])
    date_str   = first_date.strftime("%Y-%m-%dT%H:%M:%S")
    month_dir  = first_date.strftime("%Y-%m")

    # Titre = premier message utilisateur (60 chars max)
    first_user = next((m["content"] for m in messages if m["role"] == "user"), "sans-titre")
    title = first_user.strip().replace("\n", " ")[:120]

    # Corps Markdown
    lines = []
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

    return title, date_str, month_dir, body_md, num_turns, word_count


def build_frontmatter(title: str, date_str: str, chat_id: str,
                      num_turns: int, word_count: int, topic: str) -> str:
    """Génère le bloc YAML frontmatter."""
    # Nettoyage du titre pour YAML (échappe les guillemets doubles)
    safe_title = title.replace('"', "'")

    fm = f"""---
title: "{safe_title}"
date: {date_str}
source: lechat
chat_id: {chat_id}
tags:
  - lechat
  - conversation-ia
num_turns: {num_turns}
word_count: {word_count}
topic: {topic}
importance: ""
ai_summary: ""
ai_tags: []
---"""
    return fm


def process_file(json_path: Path, output_dir: Path,
                 dry_run: bool, date_from: datetime | None,
                 overwrite: bool) -> tuple[str, str]:
    """
    Traite un fichier JSON Le Chat.
    Retourne (statut, nom_fichier_output).
    Statuts : "ok", "skip_date", "skip_exists", "error"
    """
    # Lecture JSON
    try:
        with open(json_path, encoding="utf-8") as f:
            messages = json.load(f)
    except Exception as e:
        return "error", f"{json_path.name} : {e}"

    if not isinstance(messages, list) or not messages:
        return "error", f"{json_path.name} : format inattendu (pas une liste)"

    # Extraction du chat_id depuis le nom de fichier (chat-<uuid>.json)
    chat_id = json_path.stem
    if chat_id.startswith("chat-"):
        chat_id = chat_id[5:]

    # Extraction des données
    try:
        title, date_str, month_dir, body_md, num_turns, word_count = extract_conversation(messages)
    except Exception as e:
        return "error", f"{json_path.name} : {e}"

    # Filtre par date
    if date_from:
        conv_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        if conv_date < date_from:
            return "skip_date", json_path.name

    # Détection du topic
    topic = detect_topic(title, body_md)

    # Sous-dossier par topic dans output_dir
    topic_subdir = output_dir / topic_to_subdir(topic)
    filename     = f"{date_str[:10]}_{slugify(title)}.md"
    out_path     = topic_subdir / filename

    # Vérification existence
    if out_path.exists() and not overwrite:
        return "skip_exists", filename

    # Construction du fichier
    frontmatter = build_frontmatter(title, date_str, chat_id, num_turns, word_count, topic)
    full_content = f"{frontmatter}\n\n# {title}\n\n{body_md}\n"

    if dry_run:
        return "ok", f"[DRY-RUN] {topic_subdir.name}/{filename} ({num_turns} tours, {word_count} mots, topic: {topic})"

    # Écriture
    topic_subdir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return "ok", f"{topic_subdir.name}/{filename} ({num_turns} tours, {word_count} mots)"


def topic_to_subdir(topic: str) -> str:
    """Mappe un topic vers un sous-dossier de Conversations-IA."""
    return "Le Chat"


def main():
    parser = argparse.ArgumentParser(
        description="Convertit les exports JSON Le Chat en Markdown pour Obsidian"
    )
    parser.add_argument("-i", "--input",  required=True,
                        help="Dossier contenant les fichiers JSON Le Chat (un par conversation)")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT),
                        help="Dossier de sortie dans le vault Obsidian")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Simule sans écrire aucun fichier")
    parser.add_argument("--date-from", default=None,
                        help="N'importe que les conversations après cette date (YYYY-MM-DD)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Écrase les fichiers existants")
    args = parser.parse_args()

    input_dir  = Path(args.input).expanduser()
    output_dir = Path(args.output).expanduser()

    if not input_dir.exists():
        print(f"Erreur : le dossier d'entrée n'existe pas : {input_dir}")
        sys.exit(1)

    date_from = None
    if args.date_from:
        date_from = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # Collecte des fichiers JSON
    json_files = sorted(input_dir.glob("*.json"))
    if not json_files:
        print(f"Aucun fichier .json trouvé dans {input_dir}")
        sys.exit(1)

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}Traitement de {len(json_files)} fichiers JSON Le Chat")
    print(f"Sortie : {output_dir}\n")

    counts = {"ok": 0, "skip_date": 0, "skip_exists": 0, "error": 0}

    for json_path in json_files:
        status, info = process_file(
            json_path, output_dir,
            dry_run=args.dry_run,
            date_from=date_from,
            overwrite=args.overwrite,
        )
        counts[status] += 1
        prefix = {
            "ok":           "  [ok   ]",
            "skip_date":    "  [date ]",
            "skip_exists":  "  [skip ]",
            "error":        "  [ERROR]",
        }[status]
        print(f"{prefix} {info}")

    print(f"""
────────────────────────────────────────
  Total         : {len(json_files)}
  Importés      : {counts['ok']}
  Ignorés (date): {counts['skip_date']}
  Déjà présents : {counts['skip_exists']}
  Erreurs       : {counts['error']}
────────────────────────────────────────""")

    if args.dry_run:
        print("\nMode DRY-RUN — aucun fichier écrit. Relance sans --dry-run pour appliquer.")


if __name__ == "__main__":
    main()
