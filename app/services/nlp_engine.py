"""
NLP Engine — extracts structured intent and entities from a voice transcript.

Pipeline:
  1. Primary  : Groq openai/gpt-oss-120b  (JSON mode enforced)
  2. Fallback : Gemini gemini-2.5-flash    (google-genai SDK, JSON MIME type)
  3. Last resort: return NLPResult(intent=UNKNOWN) — never raises to the caller

All languages (Hindi, Spanish, French, Arabic, etc.) are handled natively
by the LLM without any pre-processing step.
"""
import asyncio
import json
import logging
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
from app.models import Category, FilterCriteria, Intent, ItemEntity, NLPResult


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Client initialisation
# ─────────────────────────────────────────────────────────────────────────────

_groq_client: AsyncGroq = AsyncGroq(api_key=GROQ_API_KEY)
_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ─────────────────────────────────────────────────────────────────────────────
#  System prompt  (identical schema demanded from both providers)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT: str = """You are an NLP extraction engine for a multilingual voice-controlled shopping assistant.
Parse the user's voice transcript and extract structured intent and entities.

Return ONLY a valid JSON object matching this exact schema (no markdown, no extra text):
{
  "intent": "<ADD_ITEM | REMOVE_ITEM | MODIFY_QUANTITY | SEARCH_FILTER | GET_SUGGESTIONS | UNKNOWN>",
  "items": [
    {
      "item_name": "<item name in lowercase>",
      "quantity": <number or 1>,
      "unit": "<unit string or null>",
      "category": "<Dairy | Produce | Snacks | Beverages | Pantry | Other>"
    }
  ],
  "item_name": "<primary item name in lowercase, or null — used for SEARCH_FILTER / GET_SUGGESTIONS>",
  "quantity": <numeric value or null>,
  "unit": "<unit string e.g. bottles, kg, pack, litre, dozen, grams — or null>",
  "category": "<Dairy | Produce | Snacks | Beverages | Pantry | Other — or null>",
  "filter_criteria": {
    "brand": "<brand name or null>",
    "max_price": <maximum price as a number or null>,
    "min_price": <minimum price as a number or null>,
    "tags": ["<tag1>", "<tag2>"]
  }
}

CRITICAL RULE for `items` array:
- For ADD_ITEM and REMOVE_ITEM: extract EVERY item mentioned in the command into the `items` array.
  Example: "Add tomato ketchup, almond milk and water" → items has 3 entries.
  Example: "I need eggs and bread" → items has 2 entries.
  Example: "Remove oranges and milk" → items has 2 entries.
- For all other intents: `items` should be an empty array [].
- Always populate `items` for ADD_ITEM/REMOVE_ITEM, even for a single item.

Intent classification rules:
- ADD_ITEM       → "add", "I need", "buy", "get me", "I want", "put in", "include", "order", "chahiye"
- REMOVE_ITEM    → "remove", "delete", "take off", "don't need", "cancel", "drop", "hatao"
- MODIFY_QUANTITY → "change quantity", "update", "make it", "instead of X use Y amount"
- SEARCH_FILTER  → "find", "search", "look for", "show me", "under $X", "brand X", "organic", "filter"
- GET_SUGGESTIONS → "suggest", "recommend", "what should I buy", "ideas", "any suggestions"
- UNKNOWN        → intent cannot be determined

Category inference (apply per item in the `items` array):
- Dairy     : milk, cheese, yogurt, butter, cream, eggs
- Produce   : fruits, vegetables, herbs, mushrooms, mango, apple, orange, watermelon, spinach
- Beverages : water, juice, soda, tea, coffee, energy drinks
- Snacks    : chips, crackers, cookies, candy, nuts, popcorn
- Pantry    : bread, pasta, rice, flour, oil, sauce, ketchup, canned goods, spices, cereal
- Other     : personal care, cleaning, household, medicine

Handle all languages (Hindi, Spanish, French, Arabic, etc.) natively.
Set filter_criteria to null when not applicable. Use null for undetermined scalar fields."""


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

async def process_transcript(transcript: str) -> NLPResult:
    """
    Extract intent and entities from a voice transcript.
    Groq primary → Gemini fallback → UNKNOWN on total failure.
    """
    try:
        return await _process_with_groq(transcript)
    except Exception as exc:
        logger.warning("NLP: Groq failed (%s). Falling back to Gemini.", exc)

    try:
        return await _process_with_gemini(transcript)
    except Exception as exc:
        logger.error("NLP: Gemini also failed (%s). Returning UNKNOWN.", exc)
        return NLPResult(intent=Intent.UNKNOWN)


# ─────────────────────────────────────────────────────────────────────────────
#  Provider implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _process_with_groq(transcript: str) -> NLPResult:
    response = await _groq_client.chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": transcript},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=512,
    )
    raw: str = response.choices[0].message.content or "{}"
    logger.debug("NLP (Groq) raw: %s", raw)
    return _parse(raw)


async def _process_with_gemini(transcript: str) -> NLPResult:
    prompt = f"{_SYSTEM_PROMPT}\n\nUser transcript: {transcript}"
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: _gemini_client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        ),
    )
    raw: str = response.text or "{}"
    logger.debug("NLP (Gemini) raw: %s", raw)
    return _parse(raw)


# ─────────────────────────────────────────────────────────────────────────────
#  JSON parser — tolerates partial / malformed LLM output
# ─────────────────────────────────────────────────────────────────────────────

def _parse(raw: str) -> NLPResult:
    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("NLP: JSON decode failed — '%s'", raw)
        return NLPResult(intent=Intent.UNKNOWN)

    # Coerce intent
    try:
        intent = Intent(data.get("intent", "UNKNOWN"))
    except ValueError:
        intent = Intent.UNKNOWN

    # Coerce scalar category (for single-item / SEARCH_FILTER / MODIFY_QUANTITY)
    category: Optional[Category] = None
    cat_str: Optional[str] = data.get("category")
    if cat_str:
        try:
            category = Category(cat_str)
        except ValueError:
            category = None

    # Coerce filter_criteria
    filter_criteria: Optional[FilterCriteria] = None
    fc: Optional[dict] = data.get("filter_criteria")
    if isinstance(fc, dict):
        filter_criteria = FilterCriteria(
            brand=fc.get("brand"),
            max_price=fc.get("max_price"),
            min_price=fc.get("min_price"),
            tags=fc.get("tags") or [],
        )

    # Coerce multi-item `items` array
    items: list[ItemEntity] = []
    raw_items = data.get("items") or []
    if isinstance(raw_items, list):
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            name = entry.get("item_name") or entry.get("name")
            if not name:
                continue
            try:
                item_cat = Category(entry.get("category") or "Other")
            except ValueError:
                item_cat = Category.OTHER
            items.append(ItemEntity(
                item_name=str(name).lower().strip(),
                quantity=float(entry.get("quantity") or 1.0),
                unit=entry.get("unit") or None,
                category=item_cat,
            ))

    # For ADD/REMOVE without items array, synthesize from scalar fields
    if not items and intent in (Intent.ADD_ITEM, Intent.REMOVE_ITEM):
        scalar_name = data.get("item_name")
        if scalar_name:
            items.append(ItemEntity(
                item_name=str(scalar_name).lower().strip(),
                quantity=float(data.get("quantity") or 1.0),
                unit=data.get("unit") or None,
                category=category or Category.OTHER,
            ))

    # Primary item_name (first item or scalar, used for suggestions/search)
    primary_name = data.get("item_name")
    if not primary_name and items:
        primary_name = items[0].item_name

    return NLPResult(
        intent=intent,
        items=items,
        item_name=primary_name,
        quantity=data.get("quantity"),
        unit=data.get("unit"),
        category=category,
        filter_criteria=filter_criteria,
    )
