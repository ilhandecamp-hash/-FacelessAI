"""Base de données SQLite : utilisateurs et historique de générations par utilisateur.

Un simple fichier .db suffit pour ce prototype : pas de serveur de base de
données à gérer, et SQLite gère très bien la concurrence en lecture pour un
trafic modeste. Migrable vers PostgreSQL plus tard si le site grossit.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "static/app.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _get_conn():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    """Crée les tables si elles n'existent pas encore. À appeler au démarrage de l'app."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                picture TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                sujet TEXT NOT NULL,
                script TEXT NOT NULL,
                video_url TEXT NOT NULL,
                multi_fond INTEGER NOT NULL,
                voice TEXT NOT NULL,
                duration TEXT NOT NULL,
                orientation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)


def upsert_user(user_id: str, email: str, name: str, picture: str | None) -> dict:
    """Crée l'utilisateur s'il n'existe pas, ou met à jour ses infos de profil sinon.

    `user_id` est préfixé par le fournisseur (ex: "github:12345") pour les
    utilisateurs Google (identifiant "sub" brut, déjà unique côté Google) afin
    d'éviter toute collision entre espaces d'identifiants de deux fournisseurs.
    """
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, name, picture, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET email = excluded.email, name = excluded.name, picture = excluded.picture
            """,
            (user_id, email, name, picture, datetime.now(timezone.utc).isoformat()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row)


def get_user(user_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def add_history_entry(
    job_id: str,
    user_id: str,
    sujet: str,
    script: str,
    video_url: str,
    multi_fond: bool,
    voice: str,
    duration: str,
    orientation: str,
) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO history
                (job_id, user_id, sujet, script, video_url, multi_fond, voice, duration, orientation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, user_id, sujet, script, video_url,
                int(multi_fond), voice, duration, orientation,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_history_for_user(user_id: str, limit: int = 50) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) | {"multi_fond": bool(r["multi_fond"])} for r in rows]


def get_history_entry(job_id: str, user_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM history WHERE job_id = ? AND user_id = ?", (job_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def delete_history_entry(job_id: str, user_id: str) -> bool:
    """Supprime l'entrée si elle appartient bien à cet utilisateur.

    Retourne True si une ligne a été supprimée.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM history WHERE job_id = ? AND user_id = ?", (job_id, user_id)
        )
        return cursor.rowcount > 0
