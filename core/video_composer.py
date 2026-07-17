"""Montage final : assemble la vidéo de fond, la voix off et les sous-titres.

Utilise moviepy 1.0.3. La vidéo de fond est recadrée/redimensionnée au format
cible (portrait 1080x1920 pour TikTok/Shorts, ou paysage 1920x1080 pour
YouTube), bouclée si elle est plus courte que l'audio, et le texte du script
est découpé en groupes de mots affichés successivement au centre de l'écran,
synchronisés sur la durée totale de l'audio.

Les sous-titres sont dessinés avec Pillow (PIL) plutôt qu'avec TextClip de
moviepy, afin de ne dépendre d'aucun binaire externe (ImageMagick n'est pas
nécessaire) : cela rend le déploiement du serveur beaucoup plus simple.
"""

import os
import random

import numpy as np
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)
from PIL import Image, ImageDraw, ImageFont

# moviepy 1.0.3 (resize.py) utilise l'ancienne constante Image.ANTIALIAS,
# supprimée depuis Pillow 10. On la restaure pour rester compatible sans
# devoir downgrader Pillow (une vieille roue 9.5.0 provoque un crash natif
# access-violation dans PIL.ImageFont.getbbox sur certains setups Windows).
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

# Dimensions de sortie selon l'orientation choisie. Volontairement bien en
# dessous du Full HD : le plan gratuit Render (512 Mo de RAM) ne supporte pas
# de composer/encoder des frames 1080p (voire 720p sur les vidéos longues)
# sans faire tuer le process par l'hôte (OOM silencieux, sans exception
# Python). 540p reste tout à fait net sur mobile/petit écran.
ORIENTATION_DIMENSIONS = {
    "portrait": (540, 960),
    "paysage": (960, 540),
}
DEFAULT_ORIENTATION = "portrait"

WORDS_PER_CHUNK = 4
MIN_SEGMENT_DURATION = 4.0
MAX_SEGMENT_DURATION = 5.5

# Police embarquée avec le projet (Inter Bold, SIL OFL) en priorité : garantit
# un rendu de sous-titres identique quel que soit l'OS hôte (Windows en local,
# Linux sur Render/production), sans dépendre de polices système absentes.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_CANDIDATES = [
    os.path.join(_PROJECT_ROOT, "static", "fonts", "Inter-Bold.ttf"),
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _resize_and_crop(clip: VideoFileClip, target_width: int, target_height: int) -> VideoFileClip:
    """Redimensionne et recadre le clip pour remplir un cadre cible sans déformation."""
    target_ratio = target_width / target_height
    clip_ratio = clip.w / clip.h

    if clip_ratio > target_ratio:
        # Vidéo trop large : on redimensionne sur la hauteur puis on recadre la largeur.
        resized = clip.resize(height=target_height)
        excess = resized.w - target_width
        resized = resized.crop(x1=excess / 2, x2=resized.w - excess / 2)
    else:
        # Vidéo trop haute/étroite : on redimensionne sur la largeur puis on recadre la hauteur.
        resized = clip.resize(width=target_width)
        excess = resized.h - target_height
        resized = resized.crop(y1=excess / 2, y2=resized.h - excess / 2)

    return resized.resize((target_width, target_height))


def _loop_to_duration(clip: VideoFileClip, duration: float) -> VideoFileClip:
    if clip.duration >= duration:
        return clip.subclip(0, duration)
    return clip.fx(vfx.loop, duration=duration)


def _build_background_sequence(
    background_video_paths: list[str], total_duration: float, target_width: int, target_height: int
) -> tuple[VideoFileClip, list[VideoFileClip]]:
    """Enchaîne plusieurs clips de fond distincts par segments de 4 à 5 secondes
    afin de couvrir toute la durée de la vidéo, plutôt que de boucler un seul clip.

    Retourne le clip de fond final ainsi que la liste des VideoFileClip source
    ouverts (un par fichier lu), que l'appelant doit fermer explicitement une
    fois l'export terminé pour libérer les handles de fichiers sous Windows.
    """
    if len(background_video_paths) == 1:
        source_clip = VideoFileClip(background_video_paths[0])
        resized = _resize_and_crop(source_clip, target_width, target_height)
        final = _loop_to_duration(resized, total_duration).without_audio().set_duration(total_duration)
        return final, [source_clip]

    segments = []
    source_clips = []
    elapsed = 0.0
    path_index = 0

    while elapsed < total_duration:
        remaining = total_duration - elapsed
        segment_duration = min(random.uniform(MIN_SEGMENT_DURATION, MAX_SEGMENT_DURATION), remaining)

        source_path = background_video_paths[path_index % len(background_video_paths)]
        path_index += 1

        source_clip = VideoFileClip(source_path)
        source_clips.append(source_clip)

        raw_clip = _resize_and_crop(source_clip, target_width, target_height)
        raw_clip = _loop_to_duration(raw_clip, segment_duration)
        raw_clip = raw_clip.without_audio().set_duration(segment_duration)

        segments.append(raw_clip)
        elapsed += segment_duration

    final = concatenate_videoclips(segments, method="compose").set_duration(total_duration)
    return final, source_clips


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        candidate = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current_line:
            current_line = candidate
        else:
            lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines


def _render_subtitle_image(text: str, target_width: int, font_size: int) -> np.ndarray:
    """Dessine le texte du sous-titre (fond transparent, contour noir) via Pillow."""
    font = _load_font(font_size)
    max_text_width = target_width - int(target_width * 0.12)

    scratch = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    scratch_draw = ImageDraw.Draw(scratch)
    lines = _wrap_text(scratch_draw, text, font, max_text_width)

    line_height = int(font_size * 1.3)
    image_height = line_height * len(lines) + 40
    image = Image.new("RGBA", (target_width, image_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    stroke_width = max(3, font_size // 18)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (target_width - text_width) / 2
        y = i * line_height + 20
        draw.text(
            (x, y), line, font=font, fill="white",
            stroke_width=stroke_width, stroke_fill="black",
        )

    return np.array(image)


def _build_subtitle_clips(script_text: str, total_duration: float, target_width: int, font_size: int) -> list:
    """Découpe le texte en morceaux courts affichés successivement, centrés à l'écran."""
    words = script_text.split()
    if not words:
        return []

    chunks = [
        " ".join(words[i:i + WORDS_PER_CHUNK])
        for i in range(0, len(words), WORDS_PER_CHUNK)
    ]

    chunk_duration = total_duration / len(chunks)
    subtitle_clips = []

    for index, chunk_text in enumerate(chunks):
        start_time = index * chunk_duration
        image_array = _render_subtitle_image(chunk_text, target_width, font_size)

        img_clip = (
            ImageClip(image_array)
            .set_start(start_time)
            .set_duration(chunk_duration)
            .set_position("center")
        )
        subtitle_clips.append(img_clip)

    return subtitle_clips


def compose_video(
    background_video_paths: list[str],
    audio_path: str,
    script_text: str,
    output_path: str,
    orientation: str = DEFAULT_ORIENTATION,
) -> str:
    """Assemble la vidéo finale : fond(s) + voix off + sous-titres centrés.

    `orientation` vaut "portrait" (1080x1920, par défaut) ou "paysage" (1920x1080).

    Si plusieurs chemins de fond sont fournis, ils sont enchaînés par segments
    de quelques secondes pour toute la durée de la vidéo (mode "fonds multiples").
    Avec un seul chemin, le comportement est identique à avant (fond unique bouclé).

    Retourne le chemin du fichier vidéo final généré.
    """
    target_width, target_height = ORIENTATION_DIMENSIONS.get(
        orientation, ORIENTATION_DIMENSIONS[DEFAULT_ORIENTATION]
    )
    # Le texte occupe moins de hauteur relative en paysage : une police plus
    # petite garde des sous-titres proportionnés plutôt qu'écrasants. Tailles
    # recalibrées au même ratio qu'avant le passage des sorties en 540p.
    font_size = 35 if orientation == "portrait" else 28

    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    background_clip, source_clips = _build_background_sequence(
        background_video_paths, total_duration, target_width, target_height
    )

    subtitle_clips = _build_subtitle_clips(script_text, total_duration, target_width, font_size)

    final_clip = CompositeVideoClip(
        [background_clip, *subtitle_clips],
        size=(target_width, target_height),
    ).set_audio(audio_clip).set_duration(total_duration)

    final_clip.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        threads=1,
        preset="ultrafast",
        logger=None,
    )

    audio_clip.close()
    final_clip.close()
    background_clip.close()
    for source_clip in source_clips:
        source_clip.close()

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Le montage vidéo a échoué : le fichier final est absent ou vide.")

    return output_path
