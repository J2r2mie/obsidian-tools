#!/usr/bin/env python3
"""
claude_to_obsidian.py  v3
Export JSON de Claude -> Markdown + YAML frontmatter pour Obsidian.

Corrections v3 :
  - Slugify correct via unicodedata (tous les accents preserves -> supprimes proprement)
  - Extraction du contenu des tool_use "generateurs" (messagecomposev1, document...)
  - Suppression silencieuse des tool_use "operationnels" (bash, file, memory, search...)
  - Blocs de code artefacts conserves, option --no-artifacts pour les supprimer

Exemples :
  python claude_to_obsidian.py -i conversations.json -o ~/Notes/Claude/ --overwrite
  python claude_to_obsidian.py -i conversations.json --title-contains "budget"
  python claude_to_obsidian.py -i conversations.json --date-from 2026-01-01
  python claude_to_obsidian.py -i conversations.json --id abc123
  python claude_to_obsidian.py -i conversations.json --list
  python claude_to_obsidian.py -i conversations.json --dry-run
  python claude_to_obsidian.py -i conversations.json --no-artifacts
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


# ------------------------------------------------------------------ constantes

# Outils "operationnels" de Claude -> ignores completement
IGNORED_TOOL_NAMES = {
    # Execution de code / fichiers
    "bash", "computer", "terminal",
    "str_replace_editor", "str_replace_based_edit_tool",
    "read_file", "write_file", "create_file", "view_file",
    "list_directory", "list_dir",
    # Memoire / recherche interne
    "memory_search", "memorysearch",
    "conversation_search", "conversationsearch",
    "web_search", "websearch",
    # Outils Perplexity / Claude internes
    "search_browser", "search_web", "get_full_page_content",
    "search_user_memories", "search_files_v2",
    # Divers
    "run_code", "execute_code", "python",
}

# Types de blocs toujours ignores (hors tool_use)
IGNORED_BLOCK_TYPES = {"thinking", "redacted_thinking"}

# Langages des artefacts de code
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


# ------------------------------------------------------------------ chargement

def load_conversations(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    if text.strip().startswith("{"):
        ls = [l for l in text.splitlines() if l.strip()]
        if len(ls) > 1:
            try:
                return [json.loads(l) for l in ls]
            except json.JSONDecodeError:
                pass
    data = json.loads(text)
    if isinstance(data, list):
        return data
    for key in ("conversations", "chats", "data"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return [data]


# ------------------------------------------------------------------ helpers

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


def get_conv_date(conv: dict):
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


# ------------------------------------------------------------------ filtrage

def filter_conversations(convs: list, args) -> list:
    result = []
    for conv in convs:
        if args.id and get_conv_id(conv) != args.id:
            continue
        if args.title_contains:
            if args.title_contains.lower() not in get_conv_title(conv).lower():
                continue
        if args.date_from or args.date_to:
            dt = get_conv_date(conv)
            if dt is None:
                continue
            if args.date_from:
                lim = parse_date(args.date_from).replace(tzinfo=timezone.utc)
                if dt < lim:
                    continue
            if args.date_to:
                lim = parse_date(args.date_to).replace(tzinfo=timezone.utc)
                if dt > lim:
                    continue
        result.append(conv)
    return result


# ------------------------------------------------------------------ conversion contenu

def clean_text(text: str) -> str:
    for pat in JUNK_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def extract_tool_use_content(block: dict) -> str:
    """
    Tente d'extraire du contenu lisible depuis un tool_use non-operationnel.
    Ex : messagecomposev1 -> corps du mail, document -> contenu, etc.
    Retourne une chaine vide si rien d'utile n'est trouve.
    """
    name  = (block.get("name") or "").lower()
    inp   = block.get("input") or {}

    # Outil de composition de message / email
    if "compose" in name or "message" in name or "email" in name:
        parts = []
        variants = inp.get("variants") or []
        if not variants and "body" in inp:
            variants = [inp]
        for v in variants:
            subj = v.get("subject", "")
            body = v.get("body", "") or v.get("content", "")
            label = v.get("label", "")
            header = f"**{label}**" if label else ""
            if subj:
                header = (header + f"  Objet : *{subj}*").strip()
            if body:
                parts.append((header + "\n\n" + body.strip()).strip())
        if parts:
            return "\n\n---\n\n".join(parts)

    # Outil de creation de document / note
    if "document" in name or "create" in name or "note" in name:
        content = inp.get("content") or inp.get("text") or inp.get("body") or ""
        title   = inp.get("title") or inp.get("name") or ""
        if content:
            header = f"**{title}**\n\n" if title else ""
            return header + content.strip()

    # Outil generique : essayer de trouver un champ texte dans l'input
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

    # Blocs internes toujours ignores
    if btype in IGNORED_BLOCK_TYPES:
        return ""

    # Texte pur
    if btype == "text":
        return clean_text(block.get("text", ""))

    # Artefacts de code
    if btype == "artifact":
        if not keep_artifacts:
            title = block.get("title") or block.get("name") or "artefact"
            return f"> **[Artefact supprime : {title}]**"
        return artifact_to_md(block)

    # tool_use : distinguer operationnels (ignorer) et generateurs (extraire)
    if btype == "tool_use":
        name = (block.get("name") or "").lower().replace("-", "_")
        if name in IGNORED_TOOL_NAMES:
            return ""
        # Outil non-operationnel : essayer d'en extraire le contenu
        return extract_tool_use_content(block)

    # tool_result : ignorer (c'est la reponse aux outils operationnels)
    if btype == "tool_result":
        return ""

    # Pieces jointes
    if btype in ("document", "image", "attachment"):
        name = block.get("name") or block.get("file_name") or btype
        return f"> **[Piece jointe : {name}]**"

    # Fallback
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


# ------------------------------------------------------------------ slugify (v3 : unicodedata)

def slugify(text: str, max_len: int = 60) -> str:
    """Converti un titre en slug ASCII lisible, sans perte d'accents -> remplace proprement."""
    # Decomposition NFD : separe les lettres de leurs diacritiques
    text = unicodedata.normalize("NFD", text.lower())
    # Supprime les diacritiques (categorie Unicode "Mn" = Mark, Nonspacing)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Nettoie ce qui reste
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text[:max_len]


# ------------------------------------------------------------------ Markdown final

def conversation_to_markdown(conv: dict, extra_tags: list, keep_artifacts: bool = True) -> str:
    title    = get_conv_title(conv)
    conv_id  = get_conv_id(conv)
    dt       = get_conv_date(conv)
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

    messages   = messages_from_conv(conv, keep_artifacts)
    tags       = ["claude", "ia/conversation"] + extra_tags
    tags_block = "\n  - ".join(tags)

    yaml_fm = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f"date: {date_str}\n"
        f"updated: {updated_str}\n"
        f"tags:\n  - {tags_block}\n"
        "source: claude\n"
        f"conversation_id: {conv_id}\n"
        f"nb_messages: {len(messages)}\n"
        "---\n\n"
    )

    body = f"# {title}\n\n"
    for msg in messages:
        if msg["role"] == "Human":
            body += f"## Human\n\n{msg['content']}\n\n---\n\n"
        else:
            body += f"## Claude\n\n{msg['content']}\n\n---\n\n"
    return yaml_fm + body


# ------------------------------------------------------------------ CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convertit l'export JSON de Claude en Markdown+YAML pour Obsidian (v3).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--input",  "-i", required=True,
                   help="Fichier JSON/JSONL d'export Claude")
    p.add_argument("--output-dir", "-o", default="./claude_export",
                   help="Dossier de sortie. Defaut : ./claude_export")
    p.add_argument("--title-contains", "-t",
                   help="Filtre : titre contient cette chaine (insensible a la casse)")
    p.add_argument("--date-from", help="Date de debut inclusive (YYYY-MM-DD)")
    p.add_argument("--date-to",   help="Date de fin inclusive (YYYY-MM-DD)")
    p.add_argument("--id",        help="ID exact d'une conversation")
    p.add_argument("--tags", nargs="*", default=[],
                   help="Tags Obsidian supplementaires (ex: --tags projet/budget)")
    p.add_argument("--no-artifacts", action="store_true",
                   help="Supprimer les blocs de code/artefacts")
    p.add_argument("--list", "-l", action="store_true",
                   help="Lister les conversations sans rien ecrire")
    p.add_argument("--dry-run", "-n", action="store_true",
                   help="Simuler sans ecrire de fichiers")
    p.add_argument("--overwrite", action="store_true",
                   help="Ecraser les fichiers existants")
    return p


def main():
    args       = build_parser().parse_args()
    input_path = Path(args.input).expanduser()
    keep_arts  = not args.no_artifacts

    if not input_path.exists():
        print(f"Fichier introuvable : {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Chargement de {input_path} ...")
    try:
        convs = load_conversations(input_path)
    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(convs)} conversation(s) trouvee(s).")

    filtered = filter_conversations(convs, args)
    print(f"  {len(filtered)} apres filtrage.")

    if args.list:
        print()
        print(f"{'Titre':<42} {'Date':^20} {'ID'}")
        print("-" * 90)
        for conv in filtered:
            t   = get_conv_title(conv)[:40]
            dt  = get_conv_date(conv)
            d   = dt.strftime("%Y-%m-%d %H:%M") if dt else "-"
            cid = get_conv_id(conv)[:20]
            print(f"{t:<42} {d:^20} {cid}")
        return

    if not filtered:
        print("Aucune conversation ne correspond aux filtres.")
        return

    output_dir = Path(args.output_dir).expanduser()
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    for conv in filtered:
        dt       = get_conv_date(conv)
        date_pfx = dt.strftime("%Y-%m-%d") if dt else "0000-00-00"
        filename = f"{date_pfx}_{slugify(get_conv_title(conv))}.md"
        out_path = output_dir / filename

        if out_path.exists() and not args.overwrite:
            print(f"  [ignore]   {filename}")
            skipped += 1
            continue

        md = conversation_to_markdown(conv, args.tags, keep_arts)
        if args.dry_run:
            print(f"  [dry-run]  -> {out_path}")
        else:
            out_path.write_text(md, encoding="utf-8")
            print(f"  [ok]       {filename}")
        written += 1

    label = "[dry-run] " if args.dry_run else ""
    print(f"\n{label}Termine : {written} fichier(s) ecrit(s), {skipped} ignore(s).")
    if not args.dry_run and written:
        print(f"Dossier : {output_dir.resolve()}")


if __name__ == "__main__":
    main()