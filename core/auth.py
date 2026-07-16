"""Authentification via Google, GitHub et Discord OAuth 2.0 (Authlib).

La session utilisateur est stockée côté serveur dans un cookie de session
signé (SessionMiddleware de Starlette) : après connexion, seul l'identifiant
utilisateur est gardé en session, jamais de token brut du fournisseur.
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

# GitHub n'expose pas de découverte OpenID Connect : les endpoints et le
# profil utilisateur doivent être déclarés/récupérés manuellement.
oauth.register(
    name="github",
    client_id=os.getenv("GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "read:user user:email"},
)

# Discord non plus : endpoints déclarés manuellement, profil récupéré via
# l'API REST. Le scope "email" est indispensable, sinon /users/@me omet
# l'adresse même quand l'utilisateur en a une de vérifiée.
oauth.register(
    name="discord",
    client_id=os.getenv("DISCORD_CLIENT_ID"),
    client_secret=os.getenv("DISCORD_CLIENT_SECRET"),
    access_token_url="https://discord.com/api/oauth2/token",
    authorize_url="https://discord.com/api/oauth2/authorize",
    api_base_url="https://discord.com/api/",
    client_kwargs={"scope": "identify email"},
)


def is_google_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def is_github_configured() -> bool:
    return bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))


def is_discord_configured() -> bool:
    return bool(os.getenv("DISCORD_CLIENT_ID") and os.getenv("DISCORD_CLIENT_SECRET"))


async def fetch_github_userinfo(token: dict) -> dict:
    """Récupère le profil GitHub (id, email, nom, avatar) via l'API REST.

    GitHub ne renvoie pas toujours l'email dans /user si l'utilisateur l'a
    rendu privé : on interroge /user/emails en secours pour trouver l'adresse
    primaire vérifiée.
    """
    resp = await oauth.github.get("user", token=token)
    profile = resp.json()

    email = profile.get("email")
    if not email:
        emails_resp = await oauth.github.get("user/emails", token=token)
        emails = emails_resp.json()
        primary = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
        email = primary or (emails[0]["email"] if emails else f"{profile['id']}@users.noreply.github.com")

    return {
        "sub": f"github:{profile['id']}",
        "email": email,
        "name": profile.get("name") or profile.get("login"),
        "picture": profile.get("avatar_url"),
    }


async def fetch_discord_userinfo(token: dict) -> dict:
    """Récupère le profil Discord (id, email, nom, avatar) via l'API REST.

    L'avatar Discord n'est qu'un hash : il faut reconstruire l'URL complète
    à partir de l'id utilisateur et de ce hash (ou utiliser l'avatar par
    défaut numéroté si l'utilisateur n'en a pas défini).
    """
    resp = await oauth.discord.get("users/@me", token=token)
    profile = resp.json()

    avatar_hash = profile.get("avatar")
    if avatar_hash:
        picture = f"https://cdn.discordapp.com/avatars/{profile['id']}/{avatar_hash}.png"
    else:
        default_index = (int(profile["id"]) >> 22) % 6
        picture = f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"

    email = profile.get("email") or f"{profile['id']}@users.noreply.discord.com"

    return {
        "sub": f"discord:{profile['id']}",
        "email": email,
        "name": profile.get("global_name") or profile.get("username"),
        "picture": picture,
    }


def get_current_user(request: Request) -> dict | None:
    """Retourne l'utilisateur en session (dict avec id/email/name/picture), ou None."""
    return request.session.get("user")


def login_user(request: Request, user: dict) -> None:
    request.session["user"] = {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user.get("picture"),
        "is_premium": bool(user.get("is_premium")),
    }


def logout_user(request: Request) -> None:
    request.session.pop("user", None)
