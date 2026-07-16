"""Historique des vidéos générées, persisté dans un simple fichier JSON.

Suffisant pour un prototype mono-utilisateur : pas besoin d'une base de
données pour une liste qui ne grossit que d'une entrée par génération.
"""

import json
import os
import threading
from datetime import datetime, timezone

HISTORY_PATH = "static/history.json"
MAX_ENTRIES = 50

_lock = threading.Lock()


def _read_all() -> list[dict]:
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def add_entry(
    job_id: str,
    sujet: str,
    script: str,
    video_url: str,
    multi_fond: bool,
    voice: str,
    duration: str,
    orientation: str,
) -> None:
    """Ajoute une entrée en tête d'historique et tronque au-delà de MAX_ENTRIES."""
    entry = {
        "job_id": job_id,
        "sujet": sujet,
        "script": script,
        "video_url": video_url,
        "multi_fond": multi_fond,
        "voice": voice,
        "duration": duration,
        "orientation": orientation,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with _lock:
        entries = _read_all()
        entries.insert(0, entry)
        entries = entries[:MAX_ENTRIES]

        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)


def get_history() -> list[dict]:
    with _lock:
        return _read_all()


def get_entry(job_id: str) -> dict | None:
    with _lock:
        for entry in _read_all():
            if entry["job_id"] == job_id:
                return entry
    return None


def delete_entry(job_id: str) -> bool:
    """Retire l'entrée du JSON et supprime le fichier vidéo associé sur disque.

    Retourne True si une entrée correspondante a été trouvée et supprimée.
    """
    with _lock:
        entries = _read_all()
        remaining = [e for e in entries if e["job_id"] != job_id]
        found = len(remaining) != len(entries)

        if found:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)

    if found:
        deleted_entry = next((e for e in entries if e["job_id"] == job_id), None)
        if deleted_entry:
            video_path = deleted_entry["video_url"].lstrip("/")
            if os.path.exists(video_path):
                try:
                    os.remove(video_path)
                except OSError:
                    pass

    return found
