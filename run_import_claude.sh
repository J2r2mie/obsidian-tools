#!/bin/bash
# run_import_claude.sh
# Détecte automatiquement le dossier d'export Claude (data-*) et lance l'import.
# Usage :
#   ./run_import_claude.sh            # import normal
#   ./run_import_claude.sh --dry-run  # simulation
#   ./run_import_claude.sh --all      # import complet sans filtre de date

source ~/Scripts/.venv/bin/activate
cd ~/Scripts/obsidian-tools

# ── Détection du dossier d'export le plus récent ──────────────────────────────
EXPORT_DIR=$(ls -dt ~/Downloads/data-*/ 2>/dev/null | head -1)

if [ -z "$EXPORT_DIR" ]; then
    echo "❌ Aucun dossier data-* trouvé dans ~/Downloads/"
    echo "   Vérifie que l'export Claude a bien été décompressé."
    exit 1
fi

if [ ! -f "${EXPORT_DIR}conversations.json" ]; then
    echo "❌ conversations.json introuvable dans $EXPORT_DIR"
    echo "   Contenu du dossier :"
    ls "$EXPORT_DIR"
    exit 1
fi

echo "→ Export détecté : $EXPORT_DIR"
echo ""

# ── Lancement du script Python (dossier complet — gère conversations + projets)
python3 claude_to_obsidian.py -i "$EXPORT_DIR" "$@"
