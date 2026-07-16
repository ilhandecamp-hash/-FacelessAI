"""Génération du script (texte) à partir d'un sujet, via l'API Groq.

Si aucune clé GROQ_API_KEY n'est configurée (ou en cas d'erreur réseau/API),
un texte de secours est généré localement afin que le pipeline complet reste
fonctionnel sans dépendance externe obligatoire.
"""

import os
import re

from groq import Groq

GROQ_MODEL = "llama-3.1-8b-instant"

# Paliers de durée cible proposés dans l'interface. Les bornes de mots sont
# calibrées sur un débit de narration naturel d'environ 150 mots/minute.
DURATION_PRESETS = {
    "court": {"label": "Court (~30s)", "seconds": 30, "min_words": 70, "max_words": 110},
    "1min": {"label": "1 minute", "seconds": 60, "min_words": 130, "max_words": 170},
    "2min": {"label": "2 minutes", "seconds": 120, "min_words": 270, "max_words": 330},
    "3min": {"label": "3 minutes", "seconds": 180, "min_words": 410, "max_words": 490},
}
DEFAULT_DURATION = "court"

SYSTEM_PROMPT_TEMPLATE = (
    "Tu es un scénariste spécialisé dans les vidéos pour TikTok/YouTube Shorts/"
    "Instagram Reels. Tu écris des textes de voix off percutants, en français, "
    "sans emoji, sans hashtag, sans markdown. "
    "Le texte doit durer environ {seconds} secondes à l'oral (entre {min_words} et "
    "{max_words} mots), avoir une accroche immédiate dans la première phrase, "
    "développer le sujet avec plusieurs idées ou étapes distinctes si la durée le "
    "permet, et se terminer sur une chute ou un appel à l'action. Réponds "
    "uniquement avec le texte final de la voix off."
)


def _fallback_script(sujet: str, duration: str) -> str:
    """Texte de secours généré localement, sans appel API.

    Répète et étoffe un gabarit de base autant de fois que nécessaire pour
    approcher la durée cible, sans jamais dépasser la borne haute en mots.
    """
    preset = DURATION_PRESETS.get(duration, DURATION_PRESETS[DEFAULT_DURATION])

    segments = [
        f"Saviez-vous que {sujet} cache bien plus de secrets qu'il n'y paraît ? "
        f"Aujourd'hui, on plonge ensemble dans {sujet}, un sujet qui fascine autant qu'il surprend.",
        f"En quelques instants, vous allez découvrir des faits sur {sujet} qui changeront "
        f"votre façon de voir les choses.",
        f"Peu de gens le savent, mais {sujet} a une histoire bien plus riche que ce que l'on imagine, "
        f"faite de rebondissements et de découvertes surprenantes.",
        f"Chaque détail autour de {sujet} raconte quelque chose d'important, que ce soit sur notre "
        f"passé, notre présent ou notre façon de penser le monde.",
        "Restez jusqu'à la fin, car la dernière information risque bien de vous étonner.",
        "Et si ce contenu vous a plu, abonnez-vous pour ne rien manquer des prochaines vidéos.",
    ]

    text = ""
    i = 0
    while len(text.split()) < preset["min_words"] and i < 50:
        text = (text + " " + segments[i % len(segments)]).strip()
        i += 1

    words = text.split()
    if len(words) > preset["max_words"]:
        words = words[: preset["max_words"]]
        text = " ".join(words)

    return text


def _clean_text(text: str) -> str:
    text = re.sub(r"[*_#`~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def generate_script(sujet: str, duration: str = DEFAULT_DURATION) -> str:
    """Génère un script de voix off pour le sujet donné, calibré sur la durée cible.

    Utilise l'API Groq si GROQ_API_KEY est définie, sinon retourne un texte
    de secours généré localement.
    """
    preset = DURATION_PRESETS.get(duration, DURATION_PRESETS[DEFAULT_DURATION])
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key or api_key == "your_groq_api_key_here":
        return _fallback_script(sujet, duration)

    try:
        client = Groq(api_key=api_key)
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            seconds=preset["seconds"], min_words=preset["min_words"], max_words=preset["max_words"]
        )
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Sujet de la vidéo : {sujet}"},
            ],
            temperature=0.8,
            max_tokens=max(300, int(preset["max_words"] * 2)),
        )
        content = completion.choices[0].message.content or ""
        content = _clean_text(content)
        return content if content else _fallback_script(sujet, duration)
    except Exception:
        return _fallback_script(sujet, duration)
