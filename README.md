# FacelessAI

Générateur de vidéos "faceless" pour TikTok, YouTube Shorts et Instagram Reels — script, voix off, fond vidéo et sous-titres générés et montés automatiquement à partir d'un simple sujet.

100% gratuit à l'usage : uniquement des API et bibliothèques gratuites (Groq, edge-tts, Pexels, MoviePy), aucune carte bancaire requise.

## Fonctionnalités

- **Script par IA** — texte de voix off généré via Groq (avec fallback local si aucune clé n'est configurée)
- **Voix off naturelle** — plusieurs voix françaises/francophones au choix, via edge-tts
- **Fonds libres de droits** — vidéos Pexels assorties au sujet, en un seul fond ou en plusieurs qui s'enchaînent
- **Sous-titres incrustés** — montage automatique avec MoviePy, police embarquée (Inter Bold) pour un rendu identique sur tous les OS
- **Durée réglable** — court (~30s), 1, 2 ou 3 minutes
- **Format réglable** — portrait (9:16) ou paysage (16:9)
- **Connexion Google (OAuth 2.0)** — 2 générations gratuites sans compte, puis connexion requise
- **Historique privé par utilisateur** — regroupé par date, avec régénération et suppression
- **Landing page + outil séparés** (`/` et `/app`)

## Stack technique

- **Backend** : Python, FastAPI, Jinja2, SQLite
- **Frontend** : HTML/CSS/JS vanilla, Tailwind CSS (CDN)
- **Authentification** : Google OAuth 2.0 via Authlib
- **Génération de texte** : Groq (`llama-3.1-8b-instant`)
- **Voix off** : edge-tts (Microsoft Edge TTS, gratuit)
- **Vidéos de fond** : API Pexels
- **Montage vidéo** : MoviePy 1.0.3 + Pillow (rendu des sous-titres)

## Installation locale

### Prérequis

- Python 3.11+ (testé avec 3.12)
- Une clé API [Groq](https://console.groq.com/keys) (gratuite)
- Une clé API [Pexels](https://www.pexels.com/api/) (gratuite)
- Des identifiants OAuth [Google Cloud Console](https://console.cloud.google.com) (gratuits, optionnels — sans eux la connexion est simplement désactivée)

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
# éditer .env et renseigner les clés (voir ci-dessous)
```

### Configurer Google OAuth (pour activer la connexion)

1. Sur [console.cloud.google.com](https://console.cloud.google.com), créer un projet.
2. **APIs et services > Écran de consentement OAuth** : type Externe, remplir les infos de base.
3. **APIs et services > Identifiants > Créer des identifiants > ID client OAuth**, type Application Web :
   - Origine JavaScript autorisée : `http://localhost:8000`
   - URI de redirection autorisé : `http://localhost:8000/auth/callback`
4. Copier le **Client ID** et le **Client Secret** dans `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).
5. Définir `SESSION_SECRET_KEY` dans `.env` avec une valeur aléatoire (`python -c "import secrets; print(secrets.token_hex(32))"`).

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
  auth.py                    # Configuration OAuth Google (Authlib) + session
  database.py                # SQLite : utilisateurs + historique par utilisateur
  script_generator.py        # Génération du script (Groq + fallback local)
  voice_generator.py         # Génération de la voix off (edge-tts)
  video_fetcher.py           # Récupération des fonds vidéo (Pexels)
  video_composer.py          # Montage final (MoviePy + Pillow)
templates/
  landing.html                # Page d'accueil marketing
  app.html                    # Outil de génération
static/
  demo/                        # Vidéo de démonstration utilisée sur la landing
  fonts/                       # Police embarquée (Inter Bold, SIL OFL)
  audio/ videos/ output/        # Fichiers générés à l'exécution (non versionnés)
  app.db                        # Base SQLite (non versionnée)
```

## Notes de déploiement

Ce projet effectue du traitement vidéo (MoviePy/ffmpeg) qui peut prendre de quelques secondes à plusieurs minutes selon la durée demandée, et écrit des fichiers sur disque. Il n'est **pas compatible avec des plateformes serverless à courte durée d'exécution** (ex. Vercel Functions) sans adaptation significative (file d'attente asynchrone, stockage externe). Il convient nativement à un hébergement type VPS, Railway, Render ou Fly.io où le process Python tourne en continu.

### Déployer sur Render (gratuit)

Le fichier `render.yaml` à la racine du projet configure automatiquement le service. Étapes :

1. Sur [render.com](https://render.com), créer un compte et cliquer **New > Blueprint**.
2. Connecter ce dépôt GitHub — Render détecte `render.yaml` automatiquement.
3. Renseigner les variables d'environnement demandées dans le tableau de bord Render (elles ne sont jamais commitées dans le dépôt) :
   - `GROQ_API_KEY`, `PEXELS_API_KEY`
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (si la connexion Google doit fonctionner en production)
   - `SESSION_SECRET_KEY` est généré automatiquement par Render, rien à faire.
4. Déployer. Le build installe `requirements.txt`, puis démarre `uvicorn main:app`.
5. Une fois l'URL Render connue (ex. `https://facelessai.onrender.com`), retourner dans Google Cloud Console → Identifiants → ton ID client OAuth, et ajouter :
   - Origine JavaScript autorisée : `https://facelessai.onrender.com`
   - URI de redirection autorisé : `https://facelessai.onrender.com/auth/callback`

Sur le plan gratuit, le service se met en veille après 15 minutes d'inactivité et redémarre (~30-50s) à la requête suivante. Le disque n'est pas persistant entre redéploiements : la base SQLite (`static/app.db`) et les vidéos générées sont perdues à chaque redeploy — normal pour un prototype, mais à garder en tête si des comptes utilisateurs doivent survivre dans la durée (prévoir alors un disque persistant Render ou une vraie base externe).

## Licence

Projet personnel / prototype.
