"""
Smart Suggestions Service.

Generates three types of personalized grocery suggestions via LLM:
  - historical_recommendations : items from the user's mock purchase history
  - seasonal_recommendations   : in-season or trending items for the current month
  - substitutes                : smart alternatives for the item being discussed

Provider strategy: Groq primary → Gemini fallback → empty SuggestionResult on total failure.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types as genai_types
from groq import AsyncGroq

from app.config import (
    GEMINI_API_KEY,
    GEMINI_CHAT_MODEL,
    GROQ_API_KEY,
    GROQ_CHAT_MODEL,
)
from app.data.mock_db import PURCHASE_HISTORY
from app.models import SubstitutePair, SuggestionResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Client initialisation
# ─────────────────────────────────────────────────────────────────────────────

_groq_client: AsyncGroq = AsyncGroq(api_key=GROQ_API_KEY)
_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ─────────────────────────────────────────────────────────────────────────────
#  Season helpers
# ─────────────────────────────────────────────────────────────────────────────

_SEASON_MAP: dict[int, str] = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring",  4: "Spring", 5: "Spring",
    6: "Summer",  7: "Summer", 8: "Summer",
    9: "Autumn",  10: "Autumn", 11: "Autumn",
}


def _get_month_and_season() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%B"), _SEASON_MAP.get(now.month, "Summer")


# ─────────────────────────────────────────────────────────────────────────────
#  Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt(item_name: Optional[str]) -> str:
    month, season = _get_month_and_season()
    history_str = ", ".join(PURCHASE_HISTORY[:12])
    item_ctx = (
        f"The user is currently discussing: '{item_name}'."
        if item_name
        else "No specific item is being discussed."
    )

    return f"""You are a personalized grocery shopping suggestion engine.
Generate recommendations based on the provided context.

Return ONLY a valid JSON object (no markdown, no extra text):
{{
  "historical_recommendations": ["item1", "item2", "item3", "item4"],
  "seasonal_recommendations":   ["item1", "item2", "item3", "item4"],
  "substitutes": [
    {{"original": "item", "substitute": "alternative", "reason": "brief reason"}}
  ]
}}

Context:
- User purchase history : {history_str}
- Current month        : {month}
- Current season       : {season}
- {item_ctx}

Rules:
- historical_recommendations : 3-4 items from purchase history the user likely needs to restock.
- seasonal_recommendations   : 3-4 fresh, in-season, or trending grocery items for {season} in {month}.
- substitutes                : If an item is mentioned, provide 1-3 smart, practical substitutes with
  a brief reason each (e.g., almond milk for whole milk → "lower calorie, dairy-free").
  If no item is mentioned, suggest 2 common healthy swaps from the purchase history.
- Keep all suggestions realistic and practical grocery items only."""


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

async def generate_suggestions(item_name: Optional[str] = None) -> SuggestionResult:
    """
    Generate personalized suggestions for the given item context.
    Groq primary → Gemini fallback → empty SuggestionResult on total failure.
    """
    prompt = _build_prompt(item_name)

    try:
        return await _suggest_with_groq(prompt)
    except Exception as exc:
        logger.warning("Suggestions: Groq failed (%s). Falling back to Gemini.", exc)

    try:
        return await _suggest_with_gemini(prompt)
    except Exception as exc:
        logger.error("Suggestions: Gemini also failed (%s). Returning empty result.", exc)
        return SuggestionResult()


# ─────────────────────────────────────────────────────────────────────────────
#  Provider implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _suggest_with_groq(prompt: str) -> SuggestionResult:
    response = await _groq_client.chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.6,
        max_tokens=800,
    )
    raw: str = response.choices[0].message.content or "{}"
    logger.debug("Suggestions (Groq) raw: %s", raw)
    return _parse(raw)


async def _suggest_with_gemini(prompt: str) -> SuggestionResult:
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: _gemini_client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.6,
            ),
        ),
    )
    raw: str = response.text or "{}"
    logger.debug("Suggestions (Gemini) raw: %s", raw)
    return _parse(raw)


# ─────────────────────────────────────────────────────────────────────────────
#  JSON parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse(raw: str) -> SuggestionResult:
    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Suggestions: JSON decode failed — '%s'", raw)
        return SuggestionResult()

    substitutes: list[SubstitutePair] = []
    for entry in data.get("substitutes", []):
        if isinstance(entry, dict) and entry.get("original") and entry.get("substitute"):
            substitutes.append(
                SubstitutePair(
                    original=entry["original"],
                    substitute=entry["substitute"],
                    reason=entry.get("reason", ""),
                )
            )

    return SuggestionResult(
        historical_recommendations=data.get("historical_recommendations", []),
        seasonal_recommendations=data.get("seasonal_recommendations", []),
        substitutes=substitutes,
    )
