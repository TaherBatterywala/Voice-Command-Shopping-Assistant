"""
Voice Shopping Assistant — API route handlers.

Routes (all prefixed /api/v1):
  POST   /voice-command          — Full pipeline: STT → NLP → cart → suggestions → filter
  GET    /cart                   — Current in-memory shopping list
  DELETE /cart                   — Clear the entire shopping list
  DELETE /cart/{item_name}       — Silently remove one item (no transcript banner)
  GET    /suggestions            — Startup / on-demand personalised suggestions
"""
import logging
from typing import Optional
from urllib.parse import unquote

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
    RemoveItemResponse,
    SuggestionResult,
    VoiceCommandResponse,
)
from app.services.catalog_search import search_products
from app.services.nlp_engine import process_transcript
from app.services.stt import transcribe
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
        "voice_command | transcript=%r | intent=%s | items=%d | item=%s",
        transcript, nlp.intent, len(nlp.items), nlp.item_name,
    )

    # ── 3. Cart / list management (multi-item aware) ─────────────────────────
    message: str = _apply_intent(nlp)

    # ── 4. Smart suggestions (cart-aware) ──────────────────────────────────
    suggestions: SuggestionResult = await generate_suggestions(nlp.item_name, get_cart())

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
#  DELETE /api/v1/cart/{item_name}  — silent per-item removal for UI buttons
# ─────────────────────────────────────────────────────────────────────────────

@router.delete(
    "/cart/{item_name}",
    response_model=RemoveItemResponse,
    summary="Silently remove a single item",
    description=(
        "Removes one item by name. Designed for UI remove buttons — "
        "returns the updated cart WITHOUT triggering a transcript banner."
    ),
)
async def remove_cart_item(item_name: str) -> RemoveItemResponse:
    name = unquote(item_name).strip()
    remove_item(name)          # returns False if not found — that's fine, just silently sync
    cart = get_cart()
    return RemoveItemResponse(success=True, cart=cart, total_items=len(cart))


# ─────────────────────────────────────────────────────────────────────────────
#  DELETE /api/v1/cart  — clear entire list
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
    return await generate_suggestions(item_name=None, cart_items=get_cart())


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helper — applies NLP intent to in-memory cart (multi-item aware)
# ─────────────────────────────────────────────────────────────────────────────

def _apply_intent(nlp: NLPResult) -> str:
    """Mutate the in-memory cart and return a user-facing message."""

    # ── ADD_ITEM ─────────────────────────────────────────────────────────────
    if nlp.intent == Intent.ADD_ITEM:
        if not nlp.items:
            return "I couldn't identify any items to add. Please say something like 'Add 2 litres of milk'."

        added_labels: list[str] = []
        for entity in nlp.items:
            cart_item = CartItem(
                item_name=entity.item_name,
                quantity=entity.quantity,
                unit=entity.unit,
                category=entity.category,
            )
            result = add_item(cart_item)
            qty_str = _fmt_qty(result.quantity, result.unit)
            added_labels.append(f"{qty_str} {result.item_name}")

        if len(added_labels) == 1:
            return f"✅ Added {added_labels[0]} to your list."
        return f"✅ Added {len(added_labels)} items: {', '.join(added_labels)}."

    # ── REMOVE_ITEM ──────────────────────────────────────────────────────────
    if nlp.intent == Intent.REMOVE_ITEM:
        if not nlp.items:
            return "I couldn't identify any items to remove. Please say 'Remove milk' for example."

        removed: list[str] = []
        reduced: list[str] = []
        not_found: list[str] = []
        for entity in nlp.items:
            # If a quantity was specified (e.g. "remove 2 mangoes"), do partial remove
            # Otherwise (e.g. "remove mangoes"), delete the whole entry
            qty_to_remove = entity.quantity if entity.quantity and entity.quantity > 0 else None
            # Treat qty=1 from implicit parse as "no specific qty" → full remove
            explicit_qty = qty_to_remove if (qty_to_remove and qty_to_remove != 1.0) else None
            found = remove_item(entity.item_name, explicit_qty)
            if found:
                if explicit_qty:
                    reduced.append(f"{_fmt_qty(explicit_qty, entity.unit)} {entity.item_name}")
                else:
                    removed.append(entity.item_name)
            else:
                not_found.append(entity.item_name)

        parts: list[str] = []
        if removed:
            parts.append(f"Removed: {', '.join(removed)}")
        if reduced:
            parts.append(f"Reduced: {', '.join(reduced)}")
        if not_found:
            parts.append(f"Not found: {', '.join(not_found)}")
        return " · ".join(parts) or "Nothing was removed."

    # ── MODIFY_QUANTITY ──────────────────────────────────────────────────────
    if nlp.intent == Intent.MODIFY_QUANTITY:
        name = nlp.item_name or (nlp.items[0].item_name if nlp.items else None)
        qty  = nlp.quantity  or (nlp.items[0].quantity  if nlp.items else None)
        unit = nlp.unit or (nlp.items[0].unit if nlp.items else None)
        cat  = nlp.category or (nlp.items[0].category if nlp.items else Category.OTHER)
        if not name or qty is None:
            return "Please specify both the item name and the new quantity."
        result = modify_item(name, qty)
        if result:
            return f"✅ Updated '{name}' to {_fmt_qty(result.quantity, result.unit)}."
        # Item not in cart — upsert (add it)
        new_item = CartItem(item_name=name, quantity=qty, unit=unit, category=cat)
        add_item(new_item)
        return f"✅ Added '{name}' ({_fmt_qty(qty, unit)}) to your list."

    # ── SEARCH_FILTER ─────────────────────────────────────────────────────────
    if nlp.intent == Intent.SEARCH_FILTER:
        parts = [nlp.item_name or "items"]
        fc = nlp.filter_criteria
        if fc:
            if fc.brand:          parts.append(f"brand: {fc.brand}")
            if fc.max_price:      parts.append(f"under ${fc.max_price:.2f}")
            if fc.min_price:      parts.append(f"above ${fc.min_price:.2f}")
            if fc.tags:           parts.append(f"tags: {', '.join(fc.tags)}")
        return f"🔍 Searching for {' | '.join(parts)}."

    # ── GET_SUGGESTIONS ───────────────────────────────────────────────────────
    if nlp.intent == Intent.GET_SUGGESTIONS:
        return "✨ Here are your personalised shopping suggestions."

    return "I didn't understand that command. Please try again with a clearer phrase."


def _fmt_qty(quantity: float, unit: Optional[str]) -> str:
    qty_str = str(int(quantity)) if quantity == int(quantity) else str(quantity)
    return f"{qty_str} {unit}" if unit else qty_str
