"""Authentification via Google OAuth 2.0 (Authlib).

La session utilisateur est stockée côté serveur dans un cookie de session
signé (SessionMiddleware de Starlette) : après connexion, seul l'identifiant
utilisateur est gardé en session, jamais de token Google brut.
"""

import os

from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def is_google_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def get_current_user(request: Request) -> dict | None:
    """Retourne l'utilisateur en session (dict avec id/email/name/picture), ou None."""
    return request.session.get("user")


def login_user(request: Request, user: dict) -> None:
    request.session["user"] = {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user.get("picture"),
    }


def logout_user(request: Request) -> None:
    request.session.pop("user", None)
