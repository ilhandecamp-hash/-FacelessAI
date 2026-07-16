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
                is_premium INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        # Migration douce : ajoute la colonne si la base existait déjà sans elle.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "is_premium" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER NOT NULL DEFAULT 0")

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
                is_public INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT 'autre',
                likes_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Migration douce : ajoute les colonnes de galerie publique si la
        # table "history" existait déjà sans elles.
        existing_history_cols = {row["name"] for row in conn.execute("PRAGMA table_info(history)")}
        if "is_public" not in existing_history_cols:
            conn.execute("ALTER TABLE history ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
        if "category" not in existing_history_cols:
            conn.execute("ALTER TABLE history ADD COLUMN category TEXT NOT NULL DEFAULT 'autre'")
        if "likes_count" not in existing_history_cols:
            conn.execute("ALTER TABLE history ADD COLUMN likes_count INTEGER NOT NULL DEFAULT 0")

        # Compte les générations par utilisateur et par jour calendaire (UTC),
        # pour appliquer le quota gratuit de 5 vidéos/jour.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, usage_date),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Un utilisateur ne peut liker qu'une fois chaque vidéo publique :
        # la clé primaire composite empêche les doubles likes au niveau DB.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_likes (
                job_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (job_id, user_id),
                FOREIGN KEY (job_id) REFERENCES history(job_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
                # Changer l'id d'un utilisateur référencé par des lignes
                # d'historique nécessite de désactiver temporairement les
                # contraintes FK : ni "renommer users.id" ni "réattacher
                # history.user_id" ne peut se faire en premier sans violer
                # la contrainte dans un sens ou dans l'autre.
                conn.execute("PRAGMA foreign_keys = OFF")
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
                conn.execute("PRAGMA foreign_keys = ON")
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
        user = dict(row)
        user["is_premium"] = bool(user["is_premium"])
        return user


def set_premium(user_id: str, is_premium: bool) -> None:
    """Active/désactive le statut premium d'un utilisateur (ex: après paiement Stripe)."""
    with _get_conn() as conn:
        conn.execute("UPDATE users SET is_premium = ? WHERE id = ?", (int(is_premium), user_id))


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
            ON CONFLICT(user_id, usage_date) DO UPDATE SET count = count + 1
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
        if not row:
            return None
        user = dict(row)
        user["is_premium"] = bool(user["is_premium"])
        return user


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
        return [dict(r) | {"multi_fond": bool(r["multi_fond"]), "is_public": bool(r["is_public"])} for r in rows]


def get_history_entry(job_id: str, user_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM history WHERE job_id = ? AND user_id = ?", (job_id, user_id)
        ).fetchone()
        if not row:
            return None
        return dict(row) | {"multi_fond": bool(row["multi_fond"]), "is_public": bool(row["is_public"])}


VIDEO_CATEGORIES = {
    "histoire": "Histoire",
    "science": "Science",
    "motivation": "Motivation",
    "insolite": "Insolite",
    "astuces": "Astuces",
    "autre": "Autre",
}


def set_video_visibility(job_id: str, user_id: str, is_public: bool, category: str) -> bool:
    """Publie/dépublie une vidéo de l'historique et fixe sa catégorie.

    Ne modifie la ligne que si elle appartient bien à `user_id` (retourne
    False sinon, pour empêcher un utilisateur de modifier la vidéo d'un
    autre en devinant un job_id).
    """
    if category not in VIDEO_CATEGORIES:
        category = "autre"
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE history SET is_public = ?, category = ? WHERE job_id = ? AND user_id = ?",
            (int(is_public), category, job_id, user_id),
        )
        return cursor.rowcount > 0


def toggle_video_like(job_id: str, user_id: str) -> tuple[bool, int]:
    """Ajoute ou retire le like de `user_id` sur la vidéo `job_id`.

    Retourne (liked, likes_count) : `liked` indique le nouvel état (True si
    on vient d'ajouter le like), `likes_count` le total à jour. N'agit que
    sur des vidéos publiques.
    """
    with _get_conn() as conn:
        video = conn.execute(
            "SELECT is_public FROM history WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not video or not video["is_public"]:
            return False, 0

        existing = conn.execute(
            "SELECT 1 FROM video_likes WHERE job_id = ? AND user_id = ?", (job_id, user_id)
        ).fetchone()

        if existing:
            conn.execute("DELETE FROM video_likes WHERE job_id = ? AND user_id = ?", (job_id, user_id))
            conn.execute("UPDATE history SET likes_count = likes_count - 1 WHERE job_id = ?", (job_id,))
            liked = False
        else:
            conn.execute(
                "INSERT INTO video_likes (job_id, user_id, created_at) VALUES (?, ?, ?)",
                (job_id, user_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("UPDATE history SET likes_count = likes_count + 1 WHERE job_id = ?", (job_id,))
            liked = True

        row = conn.execute("SELECT likes_count FROM history WHERE job_id = ?", (job_id,)).fetchone()
        return liked, row["likes_count"]


def get_public_gallery(category: str | None = None, limit: int = 60) -> list[dict]:
    """Retourne les vidéos publiques, les plus likées d'abord, avec le nom
    de l'auteur (pas d'email ni d'infos privées)."""
    with _get_conn() as conn:
        query = """
            SELECT history.job_id, history.sujet, history.video_url, history.orientation,
                   history.category, history.likes_count, history.created_at,
                   users.name AS author_name
            FROM history
            JOIN users ON users.id = history.user_id
            WHERE history.is_public = 1
        """
        params: list = []
        if category and category in VIDEO_CATEGORIES:
            query += " AND history.category = ?"
            params.append(category)
        query += " ORDER BY history.likes_count DESC, history.created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def user_liked_videos(user_id: str, job_ids: list[str]) -> set[str]:
    """Retourne le sous-ensemble de `job_ids` déjà likés par `user_id`."""
    if not job_ids:
        return set()
    with _get_conn() as conn:
        placeholders = ",".join("?" for _ in job_ids)
        rows = conn.execute(
            f"SELECT job_id FROM video_likes WHERE user_id = ? AND job_id IN ({placeholders})",
            (user_id, *job_ids),
        ).fetchall()
        return {row["job_id"] for row in rows}


def delete_history_entry(job_id: str, user_id: str) -> bool:
    """Supprime l'entrée si elle appartient bien à cet utilisateur.

    Retourne True si une ligne a été supprimée.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM history WHERE job_id = ? AND user_id = ?", (job_id, user_id)
        )
        return cursor.rowcount > 0
