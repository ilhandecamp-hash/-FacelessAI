"""Base de données PostgreSQL (Supabase) : utilisateurs et historique par utilisateur.

Utilise Supabase (PostgreSQL gratuit) plutôt qu'un fichier SQLite local : le
plan gratuit Render n'offre aucun disque persistant, donc un fichier SQLite
serait entièrement effacé à chaque redéploiement — inacceptable une fois que
de vrais paiements Stripe activent le statut Premium des comptes.
"""

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "")

_lock = threading.Lock()


def _connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


class _ConnWrapper:
    """Adapte psycopg2 à l'API sqlite3 utilisée dans ce module (conn.execute(...)).

    Convertit aussi les placeholders `?` (style sqlite3) en `%s` (style
    psycopg2) pour ne pas avoir à récrire chaque requête SQL du projet.
    """

    def __init__(self, conn: psycopg2.extensions.connection):
        self._conn = conn

    def execute(self, query: str, params: tuple = ()):
        cur = self._conn.cursor()
        cur.execute(query.replace("?", "%s"), params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


@contextmanager
def _get_conn():
    with _lock:
        raw_conn = _connect()
        conn = _ConnWrapper(raw_conn)
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
                is_premium BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TEXT NOT NULL
            )
        """)

        # Contraintes FK déclarées DEFERRABLE INITIALLY DEFERRED : le
        # changement d'id d'un utilisateur lors de la fusion de comptes
        # (upsert_user) touche users.id puis history.user_id/daily_usage.user_id
        # dans la même transaction, dans un ordre qui violerait la contrainte
        # à un moment ou un autre si elle était vérifiée immédiatement.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                sujet TEXT NOT NULL,
                script TEXT NOT NULL,
                video_url TEXT NOT NULL,
                multi_fond BOOLEAN NOT NULL,
                voice TEXT NOT NULL,
                duration TEXT NOT NULL,
                orientation TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # Compte les générations par utilisateur et par jour calendaire (UTC),
        # pour appliquer le quota gratuit de 5 vidéos/jour.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                usage_date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, usage_date)
            )
        """)


def upsert_user(user_id: str, email: str, name: str, picture: str | None) -> dict:
    """Crée l'utilisateur s'il n'existe pas, ou met à jour son profil sinon.

    Les comptes sont fusionnés par email : si la même personne se connecte
    une fois avec Google puis avec GitHub (même email), c'est le même compte
    et le même historique — mais nom/avatar affichés reflètent toujours le
    dernier fournisseur utilisé pour se connecter, pas le premier.

    `user_id` est préfixé par le fournisseur (ex: "github:12345", vs l'ID
    "sub" brut pour Google) uniquement pour distinguer l'origine de la
    dernière connexion ; la ligne en base reste identifiée par son email.
    """
    with _get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        if existing:
            old_id = existing["id"]
            if old_id != user_id:
                # Renomme l'id de l'utilisateur et répercute la référence
                # sur ses lignes d'historique/usage (ON UPDATE CASCADE
                # n'étant pas déclaré, on met à jour explicitement).
                conn.execute(
                    "UPDATE users SET id = ?, name = ?, picture = ? WHERE email = ?",
                    (user_id, name, picture, email),
                )
                conn.execute(
                    "UPDATE history SET user_id = ? WHERE user_id = ?", (user_id, old_id)
                )
                conn.execute(
                    "UPDATE daily_usage SET user_id = ? WHERE user_id = ?", (user_id, old_id)
                )
            else:
                conn.execute(
                    "UPDATE users SET name = ?, picture = ? WHERE email = ?",
                    (name, picture, email),
                )
        else:
            conn.execute(
                "INSERT INTO users (id, email, name, picture, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, email, name, picture, datetime.now(timezone.utc).isoformat()),
            )

        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)


def set_premium(user_id: str, is_premium: bool) -> None:
    """Active/désactive le statut premium d'un utilisateur (ex: après paiement Stripe)."""
    with _get_conn() as conn:
        conn.execute("UPDATE users SET is_premium = ? WHERE id = ?", (is_premium, user_id))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_daily_usage(user_id: str) -> int:
    """Retourne le nombre de générations déjà effectuées aujourd'hui (UTC) par cet utilisateur."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, _today()),
        ).fetchone()
        return row["count"] if row else 0


def increment_daily_usage(user_id: str) -> int:
    """Incrémente le compteur du jour et retourne la nouvelle valeur."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_usage (user_id, usage_date, count) VALUES (?, ?, 1)
            ON CONFLICT (user_id, usage_date) DO UPDATE SET count = daily_usage.count + 1
            """,
            (user_id, _today()),
        )
        row = conn.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, _today()),
        ).fetchone()
        return row["count"]


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
                multi_fond, voice, duration, orientation,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_history_for_user(user_id: str, limit: int = 50) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


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
