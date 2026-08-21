"""
Catalog Search Service — two-layer product lookup.

Layer 1 (Fast): Local mock catalog in mock_db.py — 25 pre-seeded FMCG items.
Layer 2 (Universal): LLM-powered lookup for anything not found locally.
             The LLM has knowledge of virtually all real FMCG brands and SKUs
             and generates realistic product data (name, brand, price, tags) on demand.

This ensures the assistant handles *any* product query during testing or production —
regardless of whether it exists in the local catalog.
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
from app.data.mock_db import search_catalog
from app.models import FilterCriteria, ProductResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Clients
# ─────────────────────────────────────────────────────────────────────────────

_groq_client: AsyncGroq = AsyncGroq(api_key=GROQ_API_KEY)
_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

async def search_products(
    item_name: Optional[str],
    filter_criteria: Optional[FilterCriteria],
) -> list[ProductResult]:
    """
    Two-layer product search:
      1. Local catalog (instant, deterministic).
      2. LLM universal FMCG lookup (fallback for any unknown product/brand).

    Results from both layers are merged and de-duplicated by name+brand.
    """
    # ── Layer 1: local catalog ────────────────────────────────────────────────
    local_results = search_catalog(item_name, filter_criteria)

    if local_results:
        logger.info("Catalog search: %d local result(s) for '%s'.", len(local_results), item_name)
        return local_results

    # ── Layer 2: LLM universal lookup ────────────────────────────────────────
    logger.info(
        "Catalog search: no local results for '%s' — querying LLM.", item_name
    )
    return await _llm_search(item_name, filter_criteria)


# ─────────────────────────────────────────────────────────────────────────────
#  LLM universal search helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_search_prompt(
    item_name: Optional[str],
    filter_criteria: Optional[FilterCriteria],
) -> str:
    """Build a targeted product-search prompt for the LLM."""
    query_desc = item_name or "grocery items"

    filter_lines: list[str] = []
    if filter_criteria:
        if filter_criteria.brand:
            filter_lines.append(f"- Brand must include: {filter_criteria.brand}")
        if filter_criteria.max_price is not None:
            filter_lines.append(f"- Price must be ≤ ${filter_criteria.max_price:.2f}")
        if filter_criteria.min_price is not None:
            filter_lines.append(f"- Price must be ≥ ${filter_criteria.min_price:.2f}")
        if filter_criteria.tags:
            filter_lines.append(f"- Must match tags: {', '.join(filter_criteria.tags)}")

    filter_block = (
        "Active filters:\n" + "\n".join(filter_lines)
        if filter_lines
        else "No additional filters."
    )

    return f"""You are a universal FMCG product search engine for a retail shopping assistant.
The user is searching for: "{query_desc}"
{filter_block}

Return ONLY a valid JSON array of up to 6 realistic matching products (no markdown, no extra text):
[
  {{
    "name": "<full product name>",
    "brand": "<real or realistic brand name>",
    "category": "<Dairy | Produce | Snacks | Beverages | Pantry | Personal Care | Bakery | Meat | Frozen | Other>",
    "price": <realistic USD price as a number>,
    "tags": ["<tag1>", "<tag2>", "<tag3>"]
  }}
]

Rules:
- Use well-known, real FMCG brands wherever applicable (e.g., Heinz, Nestlé, Unilever, P&G, Kellogg's).
- Prices must be realistic supermarket USD prices.
- Strictly apply price filters — do NOT include products outside the specified range.
- Strictly apply brand filters — only include products from the specified brand.
- Tags should reflect meaningful product attributes (e.g., "organic", "gluten-free", "vegan", "low-fat").
- Vary product options where possible (e.g., different sizes, flavours, or sub-brands).
- Return an empty array [] only if no product could realistically match the query."""


async def _llm_search(
    item_name: Optional[str],
    filter_criteria: Optional[FilterCriteria],
) -> list[ProductResult]:
    """Call Groq (primary) → Gemini (fallback) for universal FMCG product lookup."""
    prompt = _build_search_prompt(item_name, filter_criteria)

    try:
        return await _search_with_groq(prompt)
    except Exception as exc:
        logger.warning("Catalog LLM (Groq) failed (%s). Falling back to Gemini.", exc)

    try:
        return await _search_with_gemini(prompt)
    except Exception as exc:
        logger.error("Catalog LLM (Gemini) also failed (%s). Returning empty results.", exc)
        return []


async def _search_with_groq(prompt: str) -> list[ProductResult]:
    response = await _groq_client.chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=1024,
    )
    raw: str = response.choices[0].message.content or "[]"
    logger.debug("Catalog LLM (Groq) raw: %s", raw)
    return _parse_products(raw)


async def _search_with_gemini(prompt: str) -> list[ProductResult]:
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: _gemini_client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
            ),
        ),
    )
    raw: str = response.text or "[]"
    logger.debug("Catalog LLM (Gemini) raw: %s", raw)
    return _parse_products(raw)


def _parse_products(raw: str) -> list[ProductResult]:
    """Parse LLM JSON output into a validated list of ProductResult objects."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Catalog LLM: JSON decode failed — '%s'", raw)
        return []

    # LLM might wrap list in an object key — handle both formats
    if isinstance(data, dict):
        # Try common wrapper keys
        for key in ("products", "results", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            logger.error("Catalog LLM: unexpected dict shape — '%s'", raw)
            return []

    if not isinstance(data, list):
        return []

    products: list[ProductResult] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        products.append(
            ProductResult(
                name=str(name),
                brand=entry.get("brand"),
                category=entry.get("category"),
                price=entry.get("price"),
                tags=entry.get("tags") or [],
            )
        )
    return products
