"""
Speech-to-Text service — Groq Whisper API (whisper-large-v3).

Accepts live audio uploads from the browser (.webm, .wav, .mp3, .m4a).
No language parameter is passed so Whisper auto-detects the language,
providing native multilingual support out of the box.

transcript_override is an optional Form field reserved exclusively for
testing without an actual audio file.
"""
import logging
from typing import Optional

from fastapi import HTTPException, UploadFile
from groq import AsyncGroq

from app.config import GROQ_API_KEY, GROQ_WHISPER_MODEL

logger = logging.getLogger(__name__)

# Groq Whisper hard limit
_MAX_FILE_BYTES: int = 25 * 1024 * 1024  # 25 MB

_groq_client: AsyncGroq = AsyncGroq(api_key=GROQ_API_KEY)


async def transcribe(
    audio: Optional[UploadFile],
    transcript_override: Optional[str] = None,
) -> str:
    """
    Transcribe an audio file using Groq's Whisper large-v3 model.

    Args:
        audio: Multipart audio upload from the browser.
        transcript_override: Plain-text bypass used **only** for testing;
                             has no effect when a real audio file is supplied.

    Returns:
        The transcribed text string.

    Raises:
        HTTPException 400: No audio and no override provided.
        HTTPException 413: File exceeds the 25 MB Groq Whisper limit.
        HTTPException 502: Groq Whisper API call failed.
    """
    # ── Test bypass ──────────────────────────────────────────────────────────
    if transcript_override and transcript_override.strip():
        logger.info("STT: using transcript_override (test mode).")
        return transcript_override.strip()

    # ── Input validation ─────────────────────────────────────────────────────
    if audio is None:
        raise HTTPException(
            status_code=400,
            detail="Provide either an 'audio' file or a 'transcript_override' string.",
        )

    content: bytes = await audio.read()

    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audio file exceeds the 25 MB Groq Whisper limit "
                f"({len(content) / 1_048_576:.1f} MB received)."
            ),
        )

    filename: str = audio.filename or "audio.wav"
    content_type: str = audio.content_type or "audio/wav"

    logger.info(
        "STT: transcribing '%s' (%s, %.1f KB) via Groq Whisper.",
        filename, content_type, len(content) / 1024,
    )

    # ── Groq Whisper call ────────────────────────────────────────────────────
    try:
        transcription = await _groq_client.audio.transcriptions.create(
            file=(filename, content, content_type),
            model=GROQ_WHISPER_MODEL,
            # Omitting `language` → auto-detection for multilingual support
        )
        text: str = transcription.text.strip()
        logger.info("STT: transcript='%s'", text)
        return text
    except Exception as exc:
        logger.error("STT: Groq Whisper failed — %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Speech-to-Text transcription failed: {exc}",
        ) from exc
