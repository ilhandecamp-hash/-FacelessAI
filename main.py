"""API FastAPI orchestrant la génération de vidéos "faceless" pour TikTok/Shorts.

Pipeline de la route POST /generate :
  1. Génération du script (Groq ou fallback local)       - core.script_generator
  2. Génération de la voix off (edge-tts)                - core.voice_generator
  3. Téléchargement d'un ou plusieurs fonds (Pexels)      - core.video_fetcher
  4. Montage final (moviepy)                              - core.video_composer
"""

import logging
import traceback
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from core.history import add_entry, delete_entry, get_history
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

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("videoia")

app = FastAPI(title="Faceless Video SaaS", version="1.0.0")

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


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    return templates.TemplateResponse("app.html", {"request": request})


@app.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest):
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
    add_entry(job_id, sujet, script_text, video_url, payload.multi_fond, payload.voice, duration, orientation)

    return GenerateResponse(success=True, video_url=video_url, script=script_text)


@app.get("/history")
async def history():
    return {"history": get_history()}


@app.delete("/history/{job_id}")
async def delete_history_entry(job_id: str):
    found = delete_entry(job_id)
    if not found:
        raise HTTPException(status_code=404, detail="Entrée d'historique introuvable.")
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


@app.get("/health")
async def health():
    return {"status": "ok"}
