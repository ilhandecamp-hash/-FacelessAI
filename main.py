"""API FastAPI orchestrant la génération de vidéos "faceless" pour TikTok/Shorts.

Pipeline de la route POST /generate :
  1. Génération du script (Groq ou fallback local)       - core.script_generator
  2. Génération de la voix off (edge-tts)                - core.voice_generator
  3. Téléchargement d'un ou plusieurs fonds (Pexels)      - core.video_fetcher
  4. Montage final (moviepy)                              - core.video_composer

Authentification : Google OAuth 2.0 (core.auth). Les 2 premières générations
sont libres pour tout visiteur ; au-delà, une connexion est requise. Une fois
connecté, l'historique des générations est privé (core.database, SQLite).
"""

import logging
import os
import traceback
import uuid

from dotenv import load_dotenv

# Doit être appelé avant tout import de module qui lit des variables d'env au
# chargement (core.auth configure Authlib avec GOOGLE_CLIENT_ID/SECRET dès
# son import), sans quoi ces modules ne verraient jamais les valeurs de .env.
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from core import database
from core.auth import (
    fetch_discord_userinfo,
    fetch_github_userinfo,
    get_current_user,
    is_discord_configured,
    is_github_configured,
    is_google_configured,
    login_user,
    logout_user,
    oauth,
)
from core.payments import (
    create_checkout_session,
    is_stripe_configured,
    parse_webhook_event,
)
from core.script_generator import DEFAULT_DURATION, DURATION_PRESETS, generate_script
from core.video_composer import DEFAULT_ORIENTATION, compose_video
from core.video_fetcher import fetch_background_videos
from core.voice_generator import AVAILABLE_VOICES, DEFAULT_VOICE, generate_voice

ORIENTATIONS = {
    "portrait": "Portrait (9:16) — TikTok, Shorts, Reels",
    "paysage": "Paysage (16:9) — YouTube",
}

# Nombre de fonds distincts téléchargés en mode "fonds multiples", selon la
# durée cible : une vidéo longue mérite plus de variété pour ne pas boucler
# trop souvent sur les mêmes 4 clips.
MULTI_BACKGROUND_COUNTS = {
    "court": 4,
    "1min": 5,
    "2min": 7,
    "3min": 9,
}

FREE_GENERATIONS_WITHOUT_ACCOUNT = 2
FREE_DAILY_GENERATIONS = 5
PREMIUM_MONTHLY_PRICE_EUR = 9.99

# Compte développeur : toujours traité comme premium, sans dépendre d'un
# paiement réel. Utile pour tester le comportement premium avant que Stripe
# (ou équivalent) soit branché.
DEVELOPER_EMAILS = {"ilhandecamp@gmail.com"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("videoia")

database.init_db()

app = FastAPI(title="Faceless Video SaaS", version="1.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "dev-insecure-secret-change-in-production"),
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class GenerateRequest(BaseModel):
    sujet: str
    multi_fond: bool = False
    voice: str = DEFAULT_VOICE
    duration: str = DEFAULT_DURATION
    orientation: str = DEFAULT_ORIENTATION


class GenerateResponse(BaseModel):
    success: bool
    video_url: str | None = None
    script: str | None = None
    error: str | None = None
    requires_login: bool = False
    requires_premium: bool = False


def _finish_login(request: Request, user: dict) -> dict:
    """Active le premium développeur si nécessaire, puis ouvre la session."""
    if user["email"] in DEVELOPER_EMAILS and not user.get("is_premium"):
        database.set_premium(user["id"], True)
        user = database.get_user(user["id"])
    login_user(request, user)
    request.session.pop("anonymous_generations", None)
    return user


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    return templates.TemplateResponse("app.html", {"request": request})


# --- Authentification Google + GitHub OAuth ---


@app.get("/auth/login")
async def auth_login(request: Request):
    if not is_google_configured():
        raise HTTPException(
            status_code=503,
            detail="La connexion Google n'est pas configurée sur ce serveur (GOOGLE_CLIENT_ID/SECRET manquants).",
        )
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(status_code=400, detail="Impossible de récupérer les informations du compte Google.")

    user = database.upsert_user(
        user_id=userinfo["sub"],
        email=userinfo["email"],
        name=userinfo.get("name", userinfo["email"]),
        picture=userinfo.get("picture"),
    )
    _finish_login(request, user)
    return RedirectResponse(url="/app")


@app.get("/auth/github/login")
async def auth_github_login(request: Request):
    if not is_github_configured():
        raise HTTPException(
            status_code=503,
            detail="La connexion GitHub n'est pas configurée sur ce serveur (GITHUB_CLIENT_ID/SECRET manquants).",
        )
    redirect_uri = request.url_for("auth_github_callback")
    return await oauth.github.authorize_redirect(request, redirect_uri)


@app.get("/auth/github/callback")
async def auth_github_callback(request: Request):
    token = await oauth.github.authorize_access_token(request)
    userinfo = await fetch_github_userinfo(token)

    user = database.upsert_user(
        user_id=userinfo["sub"],
        email=userinfo["email"],
        name=userinfo["name"],
        picture=userinfo.get("picture"),
    )
    _finish_login(request, user)
    return RedirectResponse(url="/app")


@app.get("/auth/discord/login")
async def auth_discord_login(request: Request):
    if not is_discord_configured():
        raise HTTPException(
            status_code=503,
            detail="La connexion Discord n'est pas configurée sur ce serveur (DISCORD_CLIENT_ID/SECRET manquants).",
        )
    redirect_uri = request.url_for("auth_discord_callback")
    return await oauth.discord.authorize_redirect(request, redirect_uri)


@app.get("/auth/discord/callback")
async def auth_discord_callback(request: Request):
    token = await oauth.discord.authorize_access_token(request)
    userinfo = await fetch_discord_userinfo(token)

    user = database.upsert_user(
        user_id=userinfo["sub"],
        email=userinfo["email"],
        name=userinfo["name"],
        picture=userinfo.get("picture"),
    )
    _finish_login(request, user)
    return RedirectResponse(url="/app")


@app.post("/auth/logout")
async def auth_logout(request: Request):
    logout_user(request)
    return {"success": True}


@app.post("/auth/refresh")
async def auth_refresh(request: Request):
    """Relit l'utilisateur depuis la base et met à jour la session en cours.

    Nécessaire après un paiement Stripe : le webhook active is_premium en
    base, mais la session déjà ouverte dans le navigateur ne le reflète pas
    tant qu'elle n'a pas été relue, sans quoi seule une déconnexion/reconnexion
    ferait apparaître le statut Premium.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Connexion requise.")

    fresh_user = database.get_user(user["id"])
    if not fresh_user:
        raise HTTPException(status_code=404, detail="Compte introuvable.")

    login_user(request, fresh_user)
    return {"user": fresh_user}


@app.get("/auth/me")
async def auth_me(request: Request):
    user = get_current_user(request)
    payload = {
        "user": user,
        "google_configured": is_google_configured(),
        "github_configured": is_github_configured(),
        "discord_configured": is_discord_configured(),
        "premium_price_eur": PREMIUM_MONTHLY_PRICE_EUR,
        "free_daily_generations": FREE_DAILY_GENERATIONS,
        "stripe_configured": is_stripe_configured(),
    }

    if user and not user.get("is_premium"):
        payload["daily_usage"] = database.get_daily_usage(user["id"])

    return payload


# --- Génération de vidéo ---


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: Request, payload: GenerateRequest):
    user = get_current_user(request)

    if not user:
        used = request.session.get("anonymous_generations", 0)
        if used >= FREE_GENERATIONS_WITHOUT_ACCOUNT:
            return GenerateResponse(
                success=False,
                requires_login=True,
                error=(
                    f"Tu as utilisé tes {FREE_GENERATIONS_WITHOUT_ACCOUNT} générations gratuites. "
                    "Connecte-toi pour continuer à générer des vidéos."
                ),
            )
    else:
        is_premium = bool(user.get("is_premium"))

        if not is_premium:
            used_today = database.get_daily_usage(user["id"])
            if used_today >= FREE_DAILY_GENERATIONS:
                return GenerateResponse(
                    success=False,
                    requires_premium=True,
                    error=(
                        f"Tu as atteint la limite gratuite de {FREE_DAILY_GENERATIONS} vidéos par jour. "
                        f"Passe Premium ({PREMIUM_MONTHLY_PRICE_EUR}€/mois) pour un usage illimité."
                    ),
                )

            # Le plan gratuit connecté reste limité aux vidéos courtes en
            # portrait, sans fonds multiples : les options avancées sont
            # réservées à Premium (visibles dans l'UI avec un badge couronne,
            # mais refusées ici si jamais contournées côté client).
            if payload.duration != "court" or payload.orientation != "portrait" or payload.multi_fond:
                return GenerateResponse(
                    success=False,
                    requires_premium=True,
                    error=(
                        "Les vidéos longues, le format paysage et les fonds multiples sont "
                        f"réservés aux membres Premium ({PREMIUM_MONTHLY_PRICE_EUR}€/mois)."
                    ),
                )

    sujet = payload.sujet.strip()
    if not sujet:
        raise HTTPException(status_code=400, detail="Le sujet ne peut pas être vide.")

    duration = payload.duration if payload.duration in DURATION_PRESETS else DEFAULT_DURATION
    orientation = payload.orientation if payload.orientation in ORIENTATIONS else DEFAULT_ORIENTATION

    job_id = uuid.uuid4().hex[:12]
    audio_path = f"static/audio/{job_id}.mp3"
    output_path = f"static/output/{job_id}_final.mp4"

    background_count = MULTI_BACKGROUND_COUNTS.get(duration, 4) if payload.multi_fond else 1
    background_paths = [f"static/videos/{job_id}_bg{i}.mp4" for i in range(background_count)]

    # 1. Génération du script (appel réseau synchrone -> thread séparé)
    try:
        script_text = await run_in_threadpool(generate_script, sujet, duration)
        logger.info("Script généré pour le job %s : %s", job_id, script_text[:80])
    except Exception as exc:
        logger.error("Échec génération script (job %s): %s", job_id, exc)
        return GenerateResponse(success=False, error="Échec de la génération du script.")

    # 2. Génération de la voix off (coroutine native edge-tts)
    try:
        await generate_voice(script_text, audio_path, payload.voice)
    except Exception as exc:
        logger.error("Échec génération audio (job %s): %s", job_id, exc)
        return GenerateResponse(
            success=False, script=script_text, error="Échec de la génération de la voix off (edge-tts)."
        )

    # 3. Téléchargement du/des fond(s) (bloquant -> thread séparé ; ne doit jamais faire planter le serveur)
    try:
        downloaded_paths = await run_in_threadpool(
            fetch_background_videos, sujet, background_paths, orientation
        )
    except Exception as exc:
        logger.error("Échec téléchargement vidéo de fond (job %s): %s", job_id, exc)
        return GenerateResponse(
            success=False,
            script=script_text,
            error=(
                "Impossible de récupérer une vidéo de fond depuis Pexels. "
                "Vérifiez votre clé PEXELS_API_KEY ou réessayez avec un autre sujet."
            ),
        )

    # 4. Montage final (CPU-bound, bloquant -> thread séparé)
    try:
        await run_in_threadpool(
            compose_video, downloaded_paths, audio_path, script_text, output_path, orientation
        )
    except Exception as exc:
        logger.error("Échec montage vidéo (job %s): %s\n%s", job_id, exc, traceback.format_exc())
        return GenerateResponse(
            success=False, script=script_text, error="Échec du montage vidéo final (moviepy)."
        )

    video_url = f"/{output_path}"

    if user:
        database.add_history_entry(
            job_id, user["id"], sujet, script_text, video_url,
            payload.multi_fond, payload.voice, duration, orientation,
        )
        if not user.get("is_premium"):
            database.increment_daily_usage(user["id"])
    else:
        request.session["anonymous_generations"] = request.session.get("anonymous_generations", 0) + 1

    return GenerateResponse(success=True, video_url=video_url, script=script_text)


# --- Historique (réservé aux utilisateurs connectés) ---


@app.get("/history")
async def history(request: Request):
    user = get_current_user(request)
    if not user:
        return {"history": [], "requires_login": True}
    return {"history": database.get_history_for_user(user["id"])}


@app.delete("/history/{job_id}")
async def delete_history_entry(request: Request, job_id: str):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Connexion requise.")

    entry = database.get_history_entry(job_id, user["id"])
    found = database.delete_history_entry(job_id, user["id"])
    if not found:
        raise HTTPException(status_code=404, detail="Entrée d'historique introuvable.")

    if entry:
        video_path = entry["video_url"].lstrip("/")
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
            except OSError:
                pass

    return {"success": True}


@app.get("/voices")
async def voices():
    return {"voices": AVAILABLE_VOICES, "default": DEFAULT_VOICE}


@app.get("/durations")
async def durations():
    return {"durations": DURATION_PRESETS, "default": DEFAULT_DURATION}


@app.get("/orientations")
async def orientations():
    return {"orientations": ORIENTATIONS, "default": DEFAULT_ORIENTATION}


# --- Paiement Premium (Stripe Checkout) ---


@app.post("/billing/checkout")
async def billing_checkout(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Connexion requise pour passer Premium.")
    if user.get("is_premium"):
        raise HTTPException(status_code=400, detail="Ce compte est déjà Premium.")
    if not is_stripe_configured():
        raise HTTPException(status_code=503, detail="Le paiement n'est pas encore configuré sur ce serveur.")

    base_url = str(request.base_url).rstrip("/")
    checkout_url = await run_in_threadpool(
        create_checkout_session,
        user["id"],
        user["email"],
        f"{base_url}/app?premium=success",
        f"{base_url}/app?premium=cancelled",
    )
    return {"checkout_url": checkout_url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = parse_webhook_event(payload, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Signature webhook invalide.")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        if user_id:
            database.set_premium(user_id, True)
            logger.info("Premium activé pour l'utilisateur %s (paiement Stripe confirmé)", user_id)

    return {"received": True}


@app.get("/health")
async def health():
    return {"status": "ok"}
