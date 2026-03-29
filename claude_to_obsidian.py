#!/usr/bin/env python3
"""
claude_to_obsidian.py  v5
Export JSON/ZIP/dossier de Claude -> Markdown + YAML frontmatter pour Obsidian.
Dépose les fichiers dans 00-Inbox/ avec un frontmatter normalisé.
Utilise export_state.json pour la déduplication et la date de référence.

Note : l'export Claude ne distingue pas les conversations de projet des
conversations ordinaires — toutes sont dans conversations.json sans marqueur.

Exemples :
  python claude_to_obsidian.py -i ~/Downloads/data-2026-03-29-*/
  python claude_to_obsidian.py -i ~/Downloads/claude_export.zip
  python claude_to_obsidian.py -i conversations.json
  python claude_to_obsidian.py -i ~/Downloads/data-2026-03-29-*/ --dry-run
  python claude_to_obsidian.py -i ~/Downloads/data-2026-03-29-*/ --date-from 2026-03-13
  python claude_to_obsidian.py -i ~/Downloads/data-2026-03-29-*/ --all
"""

import argparse
import json
import re
import sys
import unicodedata
import zipfile
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

# ─── CONSTANTES ───────────────────────────────────────────────────────────────

IGNORED_TOOL_NAMES = {
    "bash", "computer", "terminal",
    "str_replace_editor", "str_replace_based_edit_tool",
    "read_file", "write_file", "create_file", "view_file",
    "list_directory", "list_dir",
    "memory_search", "memorysearch",
    "conversation_search", "conversationsearch",
    "web_search", "websearch",
    "search_browser", "search_web", "get_full_page_content",
    "search_user_memories", "search_files_v2",
    "run_code", "execute_code", "python",
}

IGNORED_BLOCK_TYPES = {"thinking", "redacted_thinking"}

ARTIFACT_LANGS = {
    "text/html":              "html",
    "application/javascript": "javascript",
    "text/x-python":          "python",
    "text/markdown":          "markdown",
    "application/json":       "json",
    "text/css":               "css",
    "text/x-code":            "code",
    "image/svg+xml":          "svg",
    "text/x-sh":              "bash",
}

JUNK_PATTERNS = [
    re.compile(r"Artifact:\s*undefined", re.IGNORECASE),
    re.compile(r"^undefined\s*$", re.MULTILINE),
    re.compile(r"(undefined\s*){3,}"),
    re.compile(r"Rendered by Claude Chat Viewer[^\n]*", re.IGNORECASE),
]


# ─── CHARGEMENT DE L'EXPORT ───────────────────────────────────────────────────

def load_conversations(input_path: Path) -> list:
    """
    Charge les conversations depuis :
    - un dossier décompressé (data-*/) → lit conversations.json dedans
    - un ZIP officiel Claude            → extrait conversations.json
    - un fichier JSON brut              → lit directement
    Retourne une liste de conversations.
    """
    if input_path.is_dir():
        conv_file = input_path / "conversations.json"
        if not conv_file.exists():
            raise FileNotFoundError(f"conversations.json introuvable dans {input_path}")
        return _load_json_file(conv_file)

    if input_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(input_path, "r") as zf:
            names = zf.namelist()
            candidates = [n for n in names
                          if n.endswith("conversations.json") and n.count("/") <= 1]
            if not candidates:
                raise FileNotFoundError("conversations.json introuvable dans le ZIP")
            with zf.open(candidates[0]) as f:
                data = json.load(f)
                return data if isinstance(data, list) else []

    # JSON brut
    return _load_json_file(input_path)


def _load_json_file(path: Path) -> list:
    """Charge un fichier JSON ou JSONL et retourne une liste de conversations."""
    text = path.read_text(encoding="utf-8")
    if text.strip().startswith("{"):
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(l) for l in lines]
            except json.JSONDecodeError:
                pass
    data = json.loads(text)
    if isinstance(data, list):
        return data
    for key in ("conversations", "chats", "data"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return [data]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_conv_title(conv: dict) -> str:
    for key in ("name", "title", "chat_title"):
        val = conv.get(key)
        if val:
            return str(val).strip()
    return "Sans titre"


def parse_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    raise ValueError(f"Format de date non reconnu : {s}")


def get_conv_date(conv: dict) -> datetime | None:
    for key in ("created_at", "createdAt", "create_time", "timestamp", "updated_at"):
        val = conv.get(key)
        if val:
            try:
                return parse_date(str(val))
            except ValueError:
                continue
    return None


def get_conv_id(conv: dict) -> str:
    for key in ("uuid", "id", "conversation_id"):
        val = conv.get(key)
        if val:
            return str(val)
    return ""


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text[:max_len]


# ─── CONVERSION DU CONTENU ────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    for pat in JUNK_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def extract_tool_use_content(block: dict) -> str:
    name = (block.get("name") or "").lower()
    inp  = block.get("input") or {}

    if "compose" in name or "message" in name or "email" in name:
        parts    = []
        variants = inp.get("variants") or []
        if not variants and "body" in inp:
            variants = [inp]
        for v in variants:
            subj   = v.get("subject", "")
            body   = v.get("body", "") or v.get("content", "")
            label  = v.get("label", "")
            header = f"**{label}**" if label else ""
            if subj:
                header = (header + f"  Objet : *{subj}*").strip()
            if body:
                parts.append((header + "\n\n" + body.strip()).strip())
        if parts:
            return "\n\n---\n\n".join(parts)

    if "document" in name or "create" in name or "note" in name:
        content = inp.get("content") or inp.get("text") or inp.get("body") or ""
        title   = inp.get("title") or inp.get("name") or ""
        if content:
            header = f"**{title}**\n\n" if title else ""
            return header + content.strip()

    for key in ("content", "text", "body", "message", "result"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return ""


def artifact_to_md(block: dict) -> str:
    content    = block.get("content", "") or block.get("input", {})
    media_type = block.get("media_type", "")
    lang       = ARTIFACT_LANGS.get(media_type, "")
    title      = block.get("title") or block.get("name") or ""
    header     = f"# {title}\n" if title else ""
    if isinstance(content, str) and content.strip():
        return f"```{lang}\n{header}{content.strip()}\n```"
    elif isinstance(content, dict):
        try:
            return f"```json\n{header}{json.dumps(content, indent=2, ensure_ascii=False)}\n```"
        except Exception:
            pass
    return f"> **[Artefact : {title or media_type}]** *(contenu non exportable)*"


def block_to_md(block, keep_artifacts: bool = True) -> str:
    if isinstance(block, str):
        return clean_text(block)

    btype = block.get("type", "")

    if btype in IGNORED_BLOCK_TYPES:
        return ""
    if btype == "text":
        return clean_text(block.get("text", ""))
    if btype == "artifact":
        if not keep_artifacts:
            title = block.get("title") or block.get("name") or "artefact"
            return f"> **[Artefact supprimé : {title}]**"
        return artifact_to_md(block)
    if btype == "tool_use":
        name = (block.get("name") or "").lower().replace("-", "_")
        if name in IGNORED_TOOL_NAMES:
            return ""
        return extract_tool_use_content(block)
    if btype == "tool_result":
        return ""
    if btype in ("document", "image", "attachment"):
        name = block.get("name") or block.get("file_name") or btype
        return f"> **[Pièce jointe : {name}]**"

    text = block.get("text") or block.get("content") or ""
    if isinstance(text, str) and text.strip():
        return clean_text(text)
    return ""


def messages_from_conv(conv: dict, keep_artifacts: bool = True) -> list:
    raw = (conv.get("chat_messages") or
           conv.get("messages") or
           conv.get("chats") or [])
    result = []
    for msg in raw:
        role = (msg.get("role") or msg.get("sender") or
                msg.get("type") or "inconnu")
        if role in ("human", "user", "prompt"):
            role = "Human"
        elif role in ("assistant", "ai", "response"):
            role = "Claude"

        content = msg.get("content") or msg.get("message") or msg.get("text") or ""
        if isinstance(content, str):
            md = clean_text(content)
        elif isinstance(content, list):
            parts = [block_to_md(b, keep_artifacts) for b in content]
            md = "\n\n".join(p for p in parts if p)
        else:
            md = str(content)

        if md.strip():
            result.append({"role": role, "content": md})
    return result


# ─── FRONTMATTER ──────────────────────────────────────────────────────────────

def build_frontmatter(title: str, date_str: str, updated_str: str,
                      conv_id: str, nb_messages: int, word_count: int) -> str:
    """Génère le bloc YAML frontmatter normalisé (schéma Inbox)."""
    safe_title    = title.strip().replace('"', "'")
    date_imported = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return f"""---
title: "{safe_title}"
date_created: {date_str}
date_updated: {updated_str}
date_imported: {date_imported}
source: claude
conversation_id: {conv_id}
tags: []
keywords: []
topic: ""
status: inbox
processed: false
nb_messages: {nb_messages}
word_count: {word_count}
summary: ""
---"""


# ─── TRAITEMENT D'UNE CONVERSATION ────────────────────────────────────────────

def process_conversation(
    conv: dict,
    output_dir: Path,
    imported_ids: set,
    dry_run: bool,
    date_from: datetime | None,
    overwrite: bool,
    keep_artifacts: bool,
) -> tuple[str, str, str | None]:
    """
    Traite une conversation Claude.
    Retourne (statut, message_affichage, conv_id_si_ok).
    Statuts : "ok", "skip_date", "skip_id", "error"
    """
    title   = get_conv_title(conv)
    conv_id = get_conv_id(conv)
    dt      = get_conv_date(conv)

    # ── Déduplication par ID ──────────────────────────────────────────────────
    if conv_id and conv_id in imported_ids:
        return "skip_id", title[:60], None

    # ── Filtre par date ───────────────────────────────────────────────────────
    if date_from and dt:
        if dt < date_from:
            return "skip_date", title[:60], None

    # ── Extraction du contenu ─────────────────────────────────────────────────
    date_str = dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else ""

    updated = None
    for key in ("updated_at", "updatedAt", "update_time"):
        val = conv.get(key)
        if val:
            try:
                updated = parse_date(str(val))
            except ValueError:
                pass
            break
    updated_str = updated.strftime("%Y-%m-%dT%H:%M:%S") if updated else date_str

    messages    = messages_from_conv(conv, keep_artifacts)
    nb_messages = len(messages)
    if nb_messages == 0:
        return "error", f"{title[:60]} : aucun message extrait", None

    body = f"# {title}\n\n"
    for msg in messages:
        label = "## Human" if msg["role"] == "Human" else "## Claude"
        body += f"{label}\n\n{msg['content']}\n\n---\n\n"

    word_count = len(body.split())

    # ── Nom de fichier et destination ─────────────────────────────────────────
    date_pfx = dt.strftime("%Y-%m-%d") if dt else "0000-00-00"
    filename  = f"{date_pfx}_claude_{slugify(title)}.md"
    out_path  = output_dir / filename

    counter = 1
    while out_path.exists() and not overwrite:
        out_path = output_dir / f"{date_pfx}_claude_{slugify(title)}_{counter}.md"
        counter += 1

    # ── Construction du contenu ───────────────────────────────────────────────
    fm           = build_frontmatter(title, date_str, updated_str,
                                     conv_id, nb_messages, word_count)
    full_content = f"{fm}\n\n{body}"
    info         = f"{filename} ({nb_messages} msg, {word_count} mots)"

    if dry_run:
        return "ok", f"[DRY-RUN] {info}", conv_id

    # ── Écriture ──────────────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full_content, encoding="utf-8")

    return "ok", info, conv_id


# ─── FILTRAGE ─────────────────────────────────────────────────────────────────

def filter_conversations(convs: list, args) -> list:
    result = []
    for conv in convs:
        if args.id and get_conv_id(conv) != args.id:
            continue
        if args.title_contains:
            if args.title_contains.lower() not in get_conv_title(conv).lower():
                continue
        if args.date_to:
            dt = get_conv_date(conv)
            if dt:
                lim = parse_date(args.date_to).replace(tzinfo=timezone.utc)
                if dt > lim:
                    continue
        result.append(conv)
    return result


# ─── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convertit l'export Claude en Markdown+YAML pour Obsidian (v5).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--input", "-i", required=True,
                   help="Dossier data-*, fichier ZIP ou JSON d'export Claude")
    p.add_argument("--output-dir", "-o", default=str(DEFAULT_OUTPUT),
                   help=f"Dossier de sortie (défaut : {DEFAULT_OUTPUT})")
    p.add_argument("--title-contains", "-t",
                   help="Filtre : titre contient cette chaîne (insensible à la casse)")
    p.add_argument("--date-from",
                   help="Date de début inclusive (YYYY-MM-DD). Ignoré si --all.")
    p.add_argument("--date-to",
                   help="Date de fin inclusive (YYYY-MM-DD)")
    p.add_argument("--all", action="store_true",
                   help="Importe tout sans filtre de date")
    p.add_argument("--id",
                   help="ID exact d'une conversation (pour debug)")
    p.add_argument("--no-artifacts", action="store_true",
                   help="Supprimer les blocs de code/artefacts")
    p.add_argument("--list", "-l", action="store_true",
                   help="Lister les conversations sans rien écrire")
    p.add_argument("--dry-run", "-n", action="store_true",
                   help="Simuler sans écrire de fichiers")
    p.add_argument("--overwrite", action="store_true",
                   help="Écraser les fichiers existants")
    return p


def main():
    args       = build_parser().parse_args()
    input_path = Path(args.input).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    keep_arts  = not args.no_artifacts

    if not input_path.exists():
        print(f"Fichier/dossier introuvable : {input_path}", file=sys.stderr)
        sys.exit(1)

    # ── Chargement ────────────────────────────────────────────────────────────
    print(f"Chargement de {input_path.name} ...")
    try:
        convs = load_conversations(input_path)
    except Exception as e:
        print(f"Erreur chargement : {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(convs)} conversation(s) trouvée(s).")

    # ── Filtres titre / date_to ───────────────────────────────────────────────
    convs = filter_conversations(convs, args)
    print(f"  {len(convs)} après filtrage.")

    # ── État et date de référence ─────────────────────────────────────────────
    state        = load_state() if STATE_AVAILABLE else {}
    imported_ids = get_imported_ids(state, "claude") if STATE_AVAILABLE else set()
    last_import  = get_last_import(state, "claude") if STATE_AVAILABLE else None

    if args.all:
        date_from = None
        print("\n── Mode --all : aucun filtre de date ──────────────────────")
    elif args.date_from:
        date_from = parse_date(args.date_from).replace(tzinfo=timezone.utc)
        print_import_context("claude", last_import, args.date_from)
    else:
        date_from = last_import
        print_import_context("claude", last_import, None)

    # ── Mode liste ────────────────────────────────────────────────────────────
    if args.list:
        print(f"\n{'Titre':<45} {'Date':^20} {'ID'}")
        print("-" * 90)
        for conv in convs:
            t   = get_conv_title(conv)[:43]
            dt  = get_conv_date(conv)
            d   = dt.strftime("%Y-%m-%d %H:%M") if dt else "-"
            cid = get_conv_id(conv)[:20]
            print(f"{t:<45} {d:^20} {cid}")
        return

    # ── Traitement ────────────────────────────────────────────────────────────
    mode_label = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{mode_label}Traitement de {len(convs)} conversation(s)")
    print(f"Déjà importées (index) : {len(imported_ids)}")
    print(f"Sortie : {output_dir}\n")

    counts  = {"ok": 0, "skip_date": 0, "skip_id": 0, "error": 0}
    new_ids = []

    for conv in convs:
        status, info, conv_id = process_conversation(
            conv, output_dir,
            imported_ids=imported_ids,
            dry_run=args.dry_run,
            date_from=date_from,
            overwrite=args.overwrite,
            keep_artifacts=keep_arts,
        )
        counts[status] += 1
        if status == "ok" and conv_id:
            new_ids.append(conv_id)

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
  Total             : {len(convs)}
  Importées         : {counts['ok']}
  Ignorées (date)   : {counts['skip_date']}
  Ignorées (déjà vu): {counts['skip_id']}
  Erreurs           : {counts['error']}
────────────────────────────────────────""")

    if args.dry_run:
        print("\nMode DRY-RUN — aucun fichier écrit ni état mis à jour.")
        print("Relance sans --dry-run pour appliquer.")
        return

    # ── Mise à jour de l'état ─────────────────────────────────────────────────
    if STATE_AVAILABLE and counts["ok"] > 0:
        state = update_state(state, "claude", new_ids, counts["ok"])
        save_state(state)
        print(f"  État mis à jour : {counts['ok']} ID(s) ajouté(s) à l'index.")


if __name__ == "__main__":
    main()
