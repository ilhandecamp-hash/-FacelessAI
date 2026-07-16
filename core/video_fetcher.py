"""Récupération de vidéos de fond libres de droits via l'API gratuite Pexels."""

import os
import random

import requests

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
DEFAULT_QUERY_FALLBACK = "nature abstract background"

ORIENTATIONS = {"portrait": "portrait", "paysage": "landscape"}
DEFAULT_ORIENTATION = "portrait"



# Limite volontairement la résolution des vidéos de fond téléchargées : le
# serveur tourne avec 512 Mo de RAM sur le plan gratuit Render, et décoder des
# sources HD/2K/4K image par image avec moviepy y provoque un OOM silencieux
# (le process est tué par l'hôte, sans exception Python à intercepter). 720p
# s'est encore avéré trop lourd en pratique : on vise du 480p pour la source,
# largement suffisant en qualité perçue une fois recadré/affiché en mobile.
MAX_SOURCE_DIMENSION = 854


def _pick_best_video_file(video: dict, orientation: str) -> str | None:
    """Choisit le meilleur fichier vidéo pour l'orientation demandée, plafonné à
    une résolution raisonnable à décoder (voir MAX_SOURCE_DIMENSION)."""
    files = video.get("video_files", [])
    if not files:
        return None

    def is_matching_orientation(f: dict) -> bool:
        w, h = f.get("width"), f.get("height")
        if not w or not h:
            return False
        return h > w if orientation == "portrait" else w > h

    def is_small_enough(f: dict) -> bool:
        w, h = f.get("width"), f.get("height")
        return bool(w and h and max(w, h) <= MAX_SOURCE_DIMENSION)

    candidates = [f for f in files if is_matching_orientation(f) and is_small_enough(f)]
    if candidates:
        return max(candidates, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))["link"]

    oriented_hd = [f for f in files if is_matching_orientation(f) and f.get("quality") == "hd"]
    if oriented_hd:
        return oriented_hd[0]["link"]

    hd_files = [f for f in files if f.get("quality") == "hd"]
    if hd_files:
        return hd_files[0]["link"]

    return files[0]["link"]


def _search_videos(query: str, headers: dict, page: int, orientation: str) -> list[dict]:
    response = requests.get(
        PEXELS_SEARCH_URL,
        headers=headers,
        params={"query": query, "orientation": orientation, "per_page": 20, "page": page},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("videos", [])


def _download_video(video_url: str, output_path: str) -> bool:
    video_response = requests.get(video_url, stream=True, timeout=30)
    video_response.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in video_response.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def fetch_background_videos(
    sujet: str, output_paths: list[str], orientation: str = DEFAULT_ORIENTATION
) -> list[str]:
    """Télécharge plusieurs vidéos de fond distinctes liées au sujet depuis Pexels.

    `orientation` vaut "portrait" (format TikTok/Shorts, par défaut) ou "paysage"
    (format YouTube 16:9) et détermine à la fois le filtre de recherche Pexels
    et le fichier vidéo choisi dans chaque résultat.

    Une vidéo différente est téléchargée pour chaque chemin de `output_paths`,
    afin de pouvoir les enchaîner dans le montage final plutôt que de boucler
    sur un seul et même clip. En cas de pénurie de résultats distincts, les
    vidéos déjà utilisées peuvent être réutilisées pour compléter la liste.

    Lève une exception explicite si aucune vidéo n'a pu être récupérée du tout.
    L'appelant (main.py) doit gérer cette exception pour éviter de faire
    planter le serveur.
    """
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key or api_key == "your_pexels_api_key_here":
        raise RuntimeError("PEXELS_API_KEY manquante ou invalide : impossible de récupérer une vidéo de fond.")

    pexels_orientation = ORIENTATIONS.get(orientation, ORIENTATIONS[DEFAULT_ORIENTATION])
    headers = {"Authorization": api_key}

    candidate_urls: list[str] = []
    for query in (sujet, DEFAULT_QUERY_FALLBACK):
        for page in random.sample(range(1, 6), 5):
            try:
                videos = _search_videos(query, headers, page, pexels_orientation)
            except requests.RequestException:
                continue

            for video in videos:
                url = _pick_best_video_file(video, orientation)
                if url:
                    candidate_urls.append(url)

            if len(candidate_urls) >= len(output_paths):
                break

        if len(candidate_urls) >= len(output_paths):
            break

    if not candidate_urls:
        raise RuntimeError("Impossible de télécharger une vidéo de fond depuis Pexels (aucun résultat ou erreur réseau).")

    random.shuffle(candidate_urls)

    downloaded_paths: list[str] = []
    url_index = 0

    for output_path in output_paths:
        success = False
        # Essaie les URLs candidates restantes une à une jusqu'à un téléchargement réussi.
        while url_index < len(candidate_urls):
            url = candidate_urls[url_index]
            url_index += 1
            try:
                if _download_video(url, output_path):
                    downloaded_paths.append(output_path)
                    success = True
                    break
            except requests.RequestException:
                continue

        if not success and downloaded_paths:
            # Plus aucune URL neuve disponible : réutilise un fond déjà téléchargé
            # avec succès plutôt que de laisser un trou dans la séquence.
            reused_path = random.choice(downloaded_paths)
            downloaded_paths.append(reused_path)

    if not downloaded_paths:
        raise RuntimeError("Impossible de télécharger une vidéo de fond depuis Pexels (aucun résultat ou erreur réseau).")

    return downloaded_paths
