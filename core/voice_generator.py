"""Génération de la voix off avec edge-tts (gratuit, sans clé API)."""

import os

import edge_tts

DEFAULT_VOICE = os.getenv("TTS_VOICE", "fr-FR-HenriNeural")

# Voix françaises/francophones proposées dans l'interface (toutes gratuites,
# aucune clé API requise). La clé est l'identifiant edge-tts envoyé au moteur,
# la valeur les métadonnées affichées côté frontend.
AVAILABLE_VOICES = {
    "fr-FR-HenriNeural": {"label": "Henri", "gender": "Homme", "region": "France"},
    "fr-FR-DeniseNeural": {"label": "Denise", "gender": "Femme", "region": "France"},
    "fr-FR-EloiseNeural": {"label": "Éloise", "gender": "Femme", "region": "France"},
    "fr-FR-RemyMultilingualNeural": {"label": "Rémy", "gender": "Homme", "region": "France"},
    "fr-FR-VivienneMultilingualNeural": {"label": "Vivienne", "gender": "Femme", "region": "France"},
    "fr-CA-ThierryNeural": {"label": "Thierry", "gender": "Homme", "region": "Canada"},
    "fr-CA-SylvieNeural": {"label": "Sylvie", "gender": "Femme", "region": "Canada"},
}


async def generate_voice(text: str, output_path: str, voice: str = DEFAULT_VOICE) -> str:
    """Génère un fichier audio MP3 à partir du texte fourni.

    Retourne le chemin du fichier audio généré.
    """
    if voice not in AVAILABLE_VOICES:
        voice = DEFAULT_VOICE

    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(output_path)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("La génération audio edge-tts a échoué (fichier vide ou absent).")

    return output_path
