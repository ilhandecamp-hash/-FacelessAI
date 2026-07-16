# FacelessAI

Générateur de vidéos "faceless" pour TikTok, YouTube Shorts et Instagram Reels — script, voix off, fond vidéo et sous-titres générés et montés automatiquement à partir d'un simple sujet.

100% gratuit à l'usage : uniquement des API et bibliothèques gratuites (Groq, edge-tts, Pexels, MoviePy), aucune carte bancaire requise.

## Fonctionnalités

- **Script par IA** — texte de voix off généré via Groq (avec fallback local si aucune clé n'est configurée)
- **Voix off naturelle** — plusieurs voix françaises/francophones au choix, via edge-tts
- **Fonds libres de droits** — vidéos Pexels assorties au sujet, en un seul fond ou en plusieurs qui s'enchaînent
- **Sous-titres incrustés** — montage automatique avec MoviePy, sans dépendance à ImageMagick
- **Durée réglable** — court (~30s), 1, 2 ou 3 minutes
- **Format réglable** — portrait (9:16) ou paysage (16:9)
- **Historique des générations** — regroupé par date, avec régénération et suppression
- **Landing page + outil séparés** (`/` et `/app`)

## Stack technique

- **Backend** : Python, FastAPI, Jinja2
- **Frontend** : HTML/CSS/JS vanilla, Tailwind CSS (CDN)
- **Génération de texte** : Groq (`llama-3.1-8b-instant`)
- **Voix off** : edge-tts (Microsoft Edge TTS, gratuit)
- **Vidéos de fond** : API Pexels
- **Montage vidéo** : MoviePy 1.0.3 + Pillow (rendu des sous-titres)

## Installation locale

### Prérequis

- Python 3.11+ (testé avec 3.12)
- Une clé API [Groq](https://console.groq.com/keys) (gratuite)
- Une clé API [Pexels](https://www.pexels.com/api/) (gratuite)

### Étapes

```bash
git clone <url-du-depot>
cd videoia

python -m venv venv
# Windows
.\venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# éditer .env et renseigner GROQ_API_KEY et PEXELS_API_KEY
```

### Lancer le serveur

```bash
python -m uvicorn main:app --reload --port 8000
```

- Landing page : http://127.0.0.1:8000
- Outil de génération : http://127.0.0.1:8000/app

## Structure du projet

```
main.py                    # FastAPI, routes et orchestration du pipeline
core/
  script_generator.py       # Génération du script (Groq + fallback local)
  voice_generator.py        # Génération de la voix off (edge-tts)
  video_fetcher.py          # Récupération des fonds vidéo (Pexels)
  video_composer.py         # Montage final (MoviePy + Pillow)
  history.py                # Historique des générations (JSON local)
templates/
  landing.html               # Page d'accueil marketing
  app.html                   # Outil de génération
static/
  demo/                       # Vidéo de démonstration utilisée sur la landing
  audio/ videos/ output/      # Fichiers générés à l'exécution (non versionnés)
```

## Notes de déploiement

Ce projet effectue du traitement vidéo (MoviePy/ffmpeg) qui peut prendre de quelques secondes à plusieurs minutes selon la durée demandée, et écrit des fichiers sur disque. Il n'est **pas compatible avec des plateformes serverless à courte durée d'exécution** (ex. Vercel Functions) sans adaptation significative (file d'attente asynchrone, stockage externe). Il convient nativement à un hébergement type VPS, Railway, Render ou Fly.io où le process Python tourne en continu.

## Licence

Projet personnel / prototype.
