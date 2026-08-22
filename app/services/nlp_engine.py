"""
NLP Engine — extracts structured intent and entities from a voice transcript.

Pipeline:
  1. Primary  : Groq (JSON mode enforced)
  2. Fallback : Gemini (google-genai SDK, JSON MIME type)
  3. Last resort: return NLPResult(intent=UNKNOWN)

Key capabilities:
  - Multi-item extraction from a single command
  - Implicit ADD_ITEM when no verb is present (e.g. "Almond milk and bread")
  - Desi unit normalisation (dazan→12pcs, quintal→100kg, paav→250g, etc.)
  - English + Hindi/Hinglish only; Hindi item names translated to English
  - Partial-quantity REMOVE ("Remove 2 mangoes" → qty=2 in the items array)
  - Long narrative scanning (wedding lists, stories) for all FMCG items
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
#  System prompt
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT: str = """You are the NLP extraction engine for a multilingual voice shopping assistant.
SUPPORTED LANGUAGES: English and Hindi/Hinglish ONLY.
- If the transcript is in Hindi (Devanagari or romanised Hinglish), translate all item names to English before filling item_name fields.
- Do NOT produce Urdu script or other languages. Treat Urdu as Hindi and translate to English.

Return ONLY a valid JSON object matching this exact schema (no markdown, no extra text):
{
  "intent": "<ADD_ITEM | REMOVE_ITEM | MODIFY_QUANTITY | SEARCH_FILTER | GET_SUGGESTIONS | UNKNOWN>",
  "items": [
    {
      "item_name": "<English item name in lowercase>",
      "quantity": <number>,
      "unit": "<normalised unit string or null>",
      "category": "<Dairy | Produce | Snacks | Beverages | Pantry | Other>"
    }
  ],
  "item_name": "<primary English item name in lowercase, or null>",
  "quantity": <numeric value or null>,
  "unit": "<unit string or null>",
  "category": "<Dairy | Produce | Snacks | Beverages | Pantry | Other | null>",
  "filter_criteria": {
    "brand": "<brand name or null>",
    "max_price": <max price as number or null>,
    "min_price": <min price as number or null>,
    "tags": ["<tag1>", "<tag2>"]
  }
}

═══════════════════════════════════════════
INTENT CLASSIFICATION — read every rule:
═══════════════════════════════════════════
ADD_ITEM:
  - Explicit verbs: "add", "buy", "get me", "I need", "I want", "chahiye", "lena hai", "order", "put", "include"
  - IMPLICIT (no verb): if the user simply names grocery/FMCG items with no action verb, DEFAULT to ADD_ITEM.
    Examples: "almond milk", "apples and bread", "doodh aur chawal" → ADD_ITEM
  - Narrative / long text: scan for every FMCG item + quantity mentioned and extract them all.
    Example: "For my wedding I need 10 kg moong dal, 20 kg rice, and 50 kg sugar" → ADD_ITEM, 3 items.

REMOVE_ITEM:
  - "remove", "delete", "take off", "don't need", "cancel", "drop", "hatao", "nikalo"
  - IMPORTANT: when a quantity is specified (e.g. "remove 2 mangoes"), set quantity=2 in the item entry.
    This allows partial removal (reduce qty by 2) rather than deleting the whole entry.

MODIFY_QUANTITY:
  - "change quantity", "update", "make it", "set X to Y", "badlo", "update karo"

SEARCH_FILTER:
  - "find", "search", "look for", "show me", "under $X", "below ₹X", "brand X", "organic", "filter"

GET_SUGGESTIONS:
  - "suggest", "recommend", "what should I buy", "ideas", "kya kharidun"

UNKNOWN:
  - Cannot determine intent

═══════════════════════════════════════════
DESI UNIT NORMALISATION (critical):
═══════════════════════════════════════════
Convert spoken/desi units to standard values BEFORE filling the JSON:
- dazan / dozen                → quantity × 12, unit = "pieces"  (e.g. "2 dazan aam" → qty=24, unit="pieces")
- quintal / quintal            → quantity × 100, unit = "kg"      (e.g. "1 quintal sugar" → qty=100, unit="kg")
- paav / paao / quarter kg     → 0.25 kg per unit, unit = "kg"
- aadha kilo / half kg         → 0.5 kg, unit = "kg"
- litre / liter / L            → unit = "litres"
- gram / grams / g             → unit = "grams"
- kg / kilo / kilogram         → unit = "kg"
- packet / pack / pkt          → unit = "packets"
- bottle / botol               → unit = "bottles"
- piece / pcs / nag            → unit = "pieces"
Quantity like "4 dazan" means 4×12=48 pieces. Fill qty=48, unit="pieces".
Quantity "1 quintal" means qty=100, unit="kg". 

DIFFERENCE: "4 mangoes" vs "4 kg mango" — the first is qty=4 pieces, the second is qty=4 unit=kg.
Always pick the correct interpretation from context.

═══════════════════════════════════════════
MULTI-ITEM EXTRACTION (critical):
═══════════════════════════════════════════
For ADD_ITEM and REMOVE_ITEM: extract EVERY item into the `items` array.
- "Add tomato ketchup, almond milk and water" → 3 entries
- "I need eggs and bread" → 2 entries
- "10 kg moong dal, 20 kg rice, 50 kg sugar" → 3 entries
- "2 dazan aam aur 4 kg chawal" → aam=24pieces + chawal=4kg
For all other intents: `items = []`.

═══════════════════════════════════════════
HINDI TRANSLATION TABLE (examples):
═══════════════════════════════════════════
aam=mango, doodh=milk, chawal=rice, aata=wheat flour, dal=lentils,
moong dal=green lentils, chini=sugar, namak=salt, tel=oil, sabzi=vegetables,
pyaz=onion, aalu=potato, tamatar=tomato, lassan=garlic, adrak=ginger,
makhan=butter, paneer=cottage cheese, dahi=yogurt, ghee=clarified butter,
chai=tea, pani=water, anda=egg, bread=bread, biscuit=biscuit, maida=refined flour

═══════════════════════════════════════════
CATEGORY INFERENCE (per item):
═══════════════════════════════════════════
Dairy:     milk, cheese, yogurt, butter, cream, eggs, paneer, dahi, ghee
Produce:   fruits, vegetables, herbs, mango, apple, orange, watermelon, onion, tomato, potato
Beverages: water, juice, soda, tea, coffee, energy drinks, chai, pani
Snacks:    chips, crackers, cookies, candy, nuts, popcorn, biscuit
Pantry:    bread, pasta, rice, flour, oil, sauce, ketchup, dal, sugar, salt, spices, canned goods
Other:     personal care, cleaning, household, medicine

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
        temperature=0.05,   # very low — deterministic extraction
        max_tokens=1024,    # allow long multi-item lists
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
                temperature=0.05,
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

    # Coerce scalar category
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
            qty = float(entry.get("quantity") or 1.0)
            if qty <= 0:
                qty = 1.0
            items.append(ItemEntity(
                item_name=str(name).lower().strip(),
                quantity=qty,
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

    # Primary item_name
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
