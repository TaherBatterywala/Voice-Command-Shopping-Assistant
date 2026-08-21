"""
Voice Shopping Assistant — API route handlers.

Routes (all prefixed /api/v1):
  POST   /voice-command  — Full pipeline: STT → NLP → cart → suggestions → filter
  GET    /cart           — Current in-memory shopping list
  DELETE /cart           — Clear the shopping list
  GET    /suggestions    — Startup / on-demand personalised suggestions
"""
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from app.data.mock_db import (
    add_item,
    clear_cart,
    get_cart,
    modify_item,
    remove_item,
)
from app.models import (
    CartItem,
    CartResponse,
    Category,
    ClearCartResponse,
    Intent,
    NLPResult,
    SuggestionResult,
    VoiceCommandResponse,
)
from app.services.nlp_engine import process_transcript
from app.services.stt import transcribe
from app.services.catalog_search import search_products
from app.services.suggestions import generate_suggestions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Voice Shopping Assistant"])


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/v1/voice-command
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/voice-command",
    response_model=VoiceCommandResponse,
    summary="Process a voice command",
    description=(
        "Accepts a live audio file (multipart/form-data) **or** a plain-text "
        "`transcript_override` for testing. Runs the full pipeline: "
        "STT → NLP entity extraction → cart management → smart suggestions → search/filter."
    ),
)
async def voice_command(
    audio: Optional[UploadFile] = File(
        default=None,
        description="Audio file from the browser (.wav, .mp3, .webm, .m4a)",
    ),
    transcript_override: Optional[str] = Form(
        default=None,
        description="Plain-text test bypass — omit when sending real audio",
    ),
) -> VoiceCommandResponse:

    # ── 1. Speech-to-Text ────────────────────────────────────────────────────
    transcript: str = await transcribe(audio, transcript_override)

    # ── 2. NLP extraction ────────────────────────────────────────────────────
    nlp: NLPResult = await process_transcript(transcript)
    logger.info(
        "voice_command | transcript=%r | intent=%s | item=%s",
        transcript, nlp.intent, nlp.item_name,
    )

    # ── 3. Cart / list management ────────────────────────────────────────────
    message: str = _apply_intent(nlp)

    # ── 4. Smart suggestions ─────────────────────────────────────────────────
    suggestions: SuggestionResult = await generate_suggestions(nlp.item_name)

    # ── 5. Search / filter (two-layer: local catalog → LLM universal lookup) ──
    search_results = None
    if nlp.intent == Intent.SEARCH_FILTER:
        search_results = await search_products(nlp.item_name, nlp.filter_criteria)

    return VoiceCommandResponse(
        transcript=transcript,
        intent=nlp.intent,
        message=message,
        cart=get_cart(),
        suggestions=suggestions,
        search_results=search_results,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/v1/cart
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/cart",
    response_model=CartResponse,
    summary="Get the current shopping list",
    description="Returns the full in-memory shopping list with the total item count.",
)
async def get_cart_state() -> CartResponse:
    cart = get_cart()
    return CartResponse(cart=cart, total_items=len(cart))


# ─────────────────────────────────────────────────────────────────────────────
#  DELETE /api/v1/cart
# ─────────────────────────────────────────────────────────────────────────────

@router.delete(
    "/cart",
    response_model=ClearCartResponse,
    summary="Clear the shopping list",
    description="Removes every item from the in-memory shopping list.",
)
async def clear_cart_state() -> ClearCartResponse:
    clear_cart()
    return ClearCartResponse(message="Shopping list cleared successfully.", cart=[])


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/v1/suggestions
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/suggestions",
    response_model=SuggestionResult,
    summary="Get startup suggestions",
    description=(
        "Returns personalised suggestions without any audio input: "
        "historical restock items, seasonal picks, and common substitutes. "
        "Designed to pre-populate the UI on first load."
    ),
)
async def get_suggestions() -> SuggestionResult:
    return await generate_suggestions(item_name=None)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helper — applies NLP intent to in-memory cart
# ─────────────────────────────────────────────────────────────────────────────

def _apply_intent(nlp: NLPResult) -> str:
    """Mutate the in-memory cart based on the extracted intent and return a user message."""

    if nlp.intent == Intent.ADD_ITEM:
        if not nlp.item_name:
            return "I couldn't identify the item to add. Please try again."
        item = CartItem(
            item_name=nlp.item_name,
            quantity=nlp.quantity or 1.0,
            unit=nlp.unit,
            category=nlp.category or Category.OTHER,
        )
        added = add_item(item)
        qty = _fmt_qty(added.quantity, added.unit)
        return f"Added {qty} of {added.item_name} ({added.category.value}) to your shopping list."

    if nlp.intent == Intent.REMOVE_ITEM:
        if not nlp.item_name:
            return "I couldn't identify the item to remove. Please try again."
        if remove_item(nlp.item_name):
            return f"Removed '{nlp.item_name}' from your shopping list."
        return f"'{nlp.item_name}' was not found in your shopping list."

    if nlp.intent == Intent.MODIFY_QUANTITY:
        if not nlp.item_name or nlp.quantity is None:
            return "Please specify both the item name and the new quantity."
        modified = modify_item(nlp.item_name, nlp.quantity)
        if modified:
            qty = _fmt_qty(modified.quantity, modified.unit)
            return f"Updated '{nlp.item_name}' to {qty}."
        return f"'{nlp.item_name}' was not found in your shopping list."

    if nlp.intent == Intent.SEARCH_FILTER:
        parts = [nlp.item_name or "items"]
        fc = nlp.filter_criteria
        if fc:
            if fc.brand:
                parts.append(f"brand: {fc.brand}")
            if fc.max_price is not None:
                parts.append(f"under ${fc.max_price:.2f}")
            if fc.min_price is not None:
                parts.append(f"above ${fc.min_price:.2f}")
            if fc.tags:
                parts.append(f"tags: {', '.join(fc.tags)}")
        return f"Searching for {' | '.join(parts)}."

    if nlp.intent == Intent.GET_SUGGESTIONS:
        return "Here are your personalised shopping suggestions."

    return "I didn't understand that command. Please try again with a clearer phrase."


def _fmt_qty(quantity: float, unit: Optional[str]) -> str:  # type: ignore[name-defined]
    """Format quantity + unit into a readable string."""
    qty_str = str(int(quantity)) if quantity == int(quantity) else str(quantity)
    return f"{qty_str} {unit}" if unit else qty_str
