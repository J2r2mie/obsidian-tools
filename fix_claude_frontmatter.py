#!/usr/bin/env python3
"""Corrige le frontmatter cassé des fichiers Claude."""
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path("/Users/jeremie/Documents/Obsidian Vault/Claude")
DRY_RUN = "--dry-run" in sys.argv

files = sorted(CLAUDE_DIR.glob("*.md"))
fixed = 0

for f in files:
    text = f.read_text(encoding="utf-8")
    new_text = re.sub(r'(title: ".*)\n"(date:)', r'\1"\n\2', text)
    if new_text != text:
        if DRY_RUN:
            print(f"  [fix] {f.name}")
        else:
            f.write_text(new_text, encoding="utf-8")
            print(f"  [ok]  {f.name}")
        fixed += 1

print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}{fixed} fichier(s) corriges sur {len(files)}.")
