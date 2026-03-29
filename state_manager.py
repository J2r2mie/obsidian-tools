#!/usr/bin/env python3
"""
state_manager.py
Gestion centralisée du fichier export_state.json.
Utilisé par claude_to_obsidian.py et lechat_to_obsidian.py.

Ne pas exécuter directement — importer depuis les scripts d'import.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# Emplacement du fichier d'état — même dossier que les scripts
STATE_FILE = Path(__file__).parent / "export_state.json"

SOURCES = ("claude", "lechat", "perplexity")


def load_state() -> dict:
    """
    Charge le fichier export_state.json.
    Si le fichier est absent ou corrompu, retourne un état vide et prévient l'utilisateur.
    """
    if not STATE_FILE.exists():
        print(f"⚠ Fichier d'état introuvable : {STATE_FILE}")
        print("  Créez export_state.json dans le dossier des scripts ou relancez setup.")
        return _empty_state()

    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Validation minimale
        for source in SOURCES:
            if source not in data:
                data[source] = _empty_source(source)
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠ Erreur lecture export_state.json : {e}")
        print("  L'état sera ignoré pour cet import.")
        return _empty_state()


def save_state(state: dict) -> None:
    """
    Sauvegarde le fichier export_state.json.
    En cas d'erreur d'écriture, affiche un avertissement sans planter le script.
    """
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"⚠ Impossible de sauvegarder export_state.json : {e}")
        print("  Le prochain import ne pourra pas s'appuyer sur la date de référence.")


def get_last_import(state: dict, source: str) -> datetime | None:
    """
    Retourne la date du dernier import pour une source donnée.
    Retourne None si aucun import n'a encore été effectué (last_import: null).
    """
    raw = state.get(source, {}).get("last_import")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_imported_ids(state: dict, source: str) -> set:
    """
    Retourne l'ensemble des conversation_id déjà importés pour une source.
    Perplexity ne gère pas les IDs — retourne toujours un set vide.
    """
    if source == "perplexity":
        return set()
    return set(state.get(source, {}).get("imported_ids", []))


def update_state(state: dict, source: str, new_ids: list, count: int) -> dict:
    """
    Met à jour l'état après un import réussi :
    - Enregistre l'horodatage de l'import
    - Ajoute les nouveaux IDs à la liste (Claude et Le Chat uniquement)
    - Incrémente le compteur total

    Retourne le state modifié (ne sauvegarde pas — appeler save_state() ensuite).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if source not in state:
        state[source] = _empty_source(source)

    state[source]["last_import"] = now
    state[source]["total_imported"] = state[source].get("total_imported", 0) + count

    if source != "perplexity" and new_ids:
        existing = set(state[source].get("imported_ids", []))
        existing.update(new_ids)
        state[source]["imported_ids"] = sorted(existing)

    return state


def print_import_context(source: str, last_import: datetime | None, date_from_arg: str | None) -> None:
    """
    Affiche en début d'import les informations de contexte :
    - date de référence utilisée
    - avertissement si aucune date de référence (premier import)
    """
    print(f"\n── État import [{source}] ──────────────────────────")

    if date_from_arg:
        print(f"  Date de référence : {date_from_arg} (argument --date-from)")
    elif last_import:
        print(f"  Dernier import    : {last_import.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"  → Seules les conversations postérieures seront importées")
    else:
        print(f"  ⚠ Aucune date de référence — import de TOUTES les conversations")
        print(f"  (Premier import ou état réinitialisé)")

    print(f"────────────────────────────────────────────────\n")


# ── Helpers internes ──────────────────────────────────────────────────────────

def _empty_source(source: str) -> dict:
    base = {"last_import": None, "total_imported": 0}
    if source != "perplexity":
        base["imported_ids"] = []
    return base


def _empty_state() -> dict:
    return {
        "_comment": "Fichier d'état des imports Obsidian.",
        "_version": 1,
        **{source: _empty_source(source) for source in SOURCES}
    }
