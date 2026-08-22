"""
Smart Suggestions Service — dynamic, cart-aware.

Generates three types of personalised grocery suggestions via LLM:
  - historical_recommendations : restock items from purchase history (varied each call)
  - seasonal_recommendations   : in-season / trending items for the current month
  - substitutes                : smart alternatives relevant to the current cart + item

Provider strategy: Groq primary → Gemini fallback → empty SuggestionResult on total failure.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import List, Optional

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
from app.models import CartItem, SubstitutePair, SuggestionResult

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

# India-specific season overrides (monsoon / harvest context)
_INDIA_SEASON_MAP: dict[int, str] = {
    6: "Monsoon", 7: "Monsoon", 8: "Monsoon", 9: "Monsoon",
    10: "Post-Monsoon / Festival Season (Navratri, Diwali)",
    11: "Early Winter / Festival Season",
    12: "Winter",  1: "Winter",  2: "Winter",
    3: "Spring / Holi",  4: "Summer",  5: "Peak Summer",
}


def _get_time_context() -> tuple[str, str, str]:
    now = datetime.now()
    month = now.strftime("%B")
    season = _INDIA_SEASON_MAP.get(now.month, _SEASON_MAP.get(now.month, "Summer"))
    weekday = now.strftime("%A")
    return month, season, weekday


# ─────────────────────────────────────────────────────────────────────────────
#  Prompt builder — cart-aware & varied
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt(item_name: Optional[str], cart_items: List[CartItem]) -> str:
    month, season, weekday = _get_time_context()

    history_str = ", ".join(PURCHASE_HISTORY[:15])

    # Current cart context
    if cart_items:
        cart_str = ", ".join(
            f"{c.item_name} ({c.quantity}{' ' + c.unit if c.unit else ''})"
            for c in cart_items
        )
        cart_ctx = f"Current shopping cart: {cart_str}."
    else:
        cart_ctx = "Shopping cart is currently empty."

    item_ctx = (
        f"The user is currently discussing: '{item_name}'."
        if item_name
        else "No specific item is being discussed."
    )

    return f"""You are a smart, personalised grocery shopping assistant generating dynamic suggestions.
Today is {weekday}, {month}. Season: {season}.

Return ONLY a valid JSON object (no markdown, no extra text):
{{
  "historical_recommendations": ["item1", "item2", "item3", "item4"],
  "seasonal_recommendations":   ["item1", "item2", "item3", "item4"],
  "substitutes": [
    {{"original": "item", "substitute": "alternative", "reason": "brief reason"}}
  ]
}}

Context:
- {cart_ctx}
- Purchase history : {history_str}
- Current month   : {month}
- Season          : {season}
- {item_ctx}

Rules:
HISTORICAL RECOMMENDATIONS:
  - Pick 3-5 items from purchase history the user likely needs to restock NOW.
  - Cross-reference the cart: do NOT recommend items already in the cart.
  - Vary the selection each time — avoid always returning the same 4 items.
  - If cart has tea, suggest sugar, biscuits, or milk.
  - If cart has rice, suggest dal, oil, or spices.
  - If cart has pasta, suggest tomato sauce or parmesan.

SEASONAL RECOMMENDATIONS:
  - 3-5 fresh, in-season, or trending grocery items for {season} in {month} in India.
  - Include seasonal fruits, vegetables, or festival foods appropriate for this time.
  - Do NOT repeat items already in the cart.
  - Examples for Monsoon: corn, jamun, litchi, green tea, ginger, tulsi.
  - Examples for Winter: gajar (carrot), methi, sarson, peanuts, jaggery.
  - Examples for Summer: watermelon, mango, kokum, coconut water, cucumber.

SUBSTITUTES:
  - If an item is mentioned, provide 1-3 smart practical substitutes with brief reasons.
  - If no specific item is mentioned but cart is non-empty, suggest substitutes for 1-2 cart items.
  - If cart is empty, suggest 2 common healthy swaps from purchase history.
  - Keep reasons short (under 10 words).
  - Do NOT suggest swaps for items not relevant to grocery/FMCG.

Keep all suggestions realistic, practical grocery items only. Do not repeat cart items."""


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

async def generate_suggestions(
    item_name: Optional[str] = None,
    cart_items: Optional[List[CartItem]] = None,
) -> SuggestionResult:
    """
    Generate dynamic, cart-aware personalised suggestions.
    Groq primary → Gemini fallback → empty SuggestionResult on total failure.
    """
    prompt = _build_prompt(item_name, cart_items or [])

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
        temperature=0.8,   # higher → more varied, non-repetitive
        max_tokens=900,
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
                temperature=0.8,
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
