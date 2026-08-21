"""
In-memory state management.

Provides:
  - Shopping list CRUD  (add, remove, modify, get, clear)
  - Mock user purchase history for suggestion context
  - Mock product catalog with catalog search / filter logic
"""
from typing import Dict, List, Optional

from app.models import CartItem, FilterCriteria, ProductResult


# ─────────────────────────────────────────────────────────────────────────────
#  In-memory shopping list  (keyed by normalised item name)
# ─────────────────────────────────────────────────────────────────────────────

_shopping_list: Dict[str, CartItem] = {}


# ─────────────────────────────────────────────────────────────────────────────
#  Mock user purchase history
# ─────────────────────────────────────────────────────────────────────────────

PURCHASE_HISTORY: List[str] = [
    "whole milk",
    "bread",
    "eggs",
    "orange juice",
    "greek yogurt",
    "chicken breast",
    "pasta",
    "olive oil",
    "tomatoes",
    "bananas",
    "cheddar cheese",
    "coffee",
    "oats",
    "spinach",
    "butter",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Mock product catalog  (simulates a store inventory)
# ─────────────────────────────────────────────────────────────────────────────

MOCK_PRODUCT_CATALOG: List[ProductResult] = [
    ProductResult(name="organic apples",        brand="Nature's Best",  category="Produce",       price=3.99,  tags=["organic", "fruit", "fresh"]),
    ProductResult(name="whole milk",             brand="Amul",           category="Dairy",         price=2.49,  tags=["dairy", "fresh"]),
    ProductResult(name="almond milk",            brand="Silk",           category="Dairy",         price=4.49,  tags=["dairy-free", "vegan", "milk"]),
    ProductResult(name="oat milk",               brand="Oatly",          category="Dairy",         price=5.99,  tags=["dairy-free", "vegan", "milk"]),
    ProductResult(name="toothpaste",             brand="Colgate",        category="Personal Care", price=3.49,  tags=["hygiene", "dental"]),
    ProductResult(name="toothpaste",             brand="Sensodyne",      category="Personal Care", price=6.99,  tags=["hygiene", "dental", "sensitive"]),
    ProductResult(name="orange juice",           brand="Tropicana",      category="Beverages",     price=4.29,  tags=["juice", "fruit", "fresh"]),
    ProductResult(name="greek yogurt",           brand="Chobani",        category="Dairy",         price=1.99,  tags=["dairy", "probiotic", "protein"]),
    ProductResult(name="whole wheat bread",      brand="Nature's Own",   category="Pantry",        price=3.79,  tags=["bread", "whole grain", "bakery"]),
    ProductResult(name="baby spinach",           brand="Earthbound Farm",category="Produce",       price=4.99,  tags=["organic", "vegetable", "leafy green"]),
    ProductResult(name="chicken breast",         brand="Pilgrim's",      category="Meat",          price=8.99,  tags=["protein", "fresh", "meat"]),
    ProductResult(name="pasta",                  brand="Barilla",        category="Pantry",        price=1.49,  tags=["grain", "italian"]),
    ProductResult(name="extra virgin olive oil", brand="Kirkland",       category="Pantry",        price=12.99, tags=["cooking", "oil"]),
    ProductResult(name="banana",                 brand="Chiquita",       category="Produce",       price=0.29,  tags=["fruit", "fresh"]),
    ProductResult(name="cheddar cheese",         brand="Kraft",          category="Dairy",         price=5.49,  tags=["dairy", "cheese"]),
    ProductResult(name="free-range eggs",        brand="Happy Egg",      category="Dairy",         price=4.99,  tags=["protein", "fresh", "free-range"]),
    ProductResult(name="coffee",                 brand="Nescafe",        category="Beverages",     price=7.99,  tags=["hot drink", "caffeine"]),
    ProductResult(name="green tea",              brand="Lipton",         category="Beverages",     price=3.29,  tags=["tea", "healthy", "antioxidant"]),
    ProductResult(name="potato chips",           brand="Lay's",          category="Snacks",        price=3.49,  tags=["snack", "crunchy"]),
    ProductResult(name="mixed nuts",             brand="Planters",       category="Snacks",        price=8.99,  tags=["snack", "protein", "healthy"]),
    ProductResult(name="brown rice",             brand="Lundberg",       category="Pantry",        price=4.29,  tags=["grain", "gluten-free", "healthy"]),
    ProductResult(name="coconut water",          brand="Vita Coco",      category="Beverages",     price=3.99,  tags=["hydration", "natural", "electrolytes"]),
    ProductResult(name="dark chocolate",         brand="Lindt",          category="Snacks",        price=4.49,  tags=["snack", "chocolate", "antioxidant"]),
    ProductResult(name="frozen mixed berries",   brand="Wyman's",        category="Produce",       price=5.99,  tags=["fruit", "frozen", "antioxidant"]),
    ProductResult(name="tomato sauce",           brand="Rao's",          category="Pantry",        price=8.49,  tags=["sauce", "italian", "canned"]),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(name: str) -> str:
    return name.strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
#  Shopping list CRUD
# ─────────────────────────────────────────────────────────────────────────────

def add_item(item: CartItem) -> CartItem:
    """
    Add an item to the cart.
    If the item already exists, its quantity is accumulated.
    """
    key = _normalise(item.item_name)
    if key in _shopping_list:
        existing = _shopping_list[key]
        existing.quantity = round(existing.quantity + (item.quantity or 1.0), 2)
        return existing
    _shopping_list[key] = item
    return item


def remove_item(item_name: str) -> bool:
    """Remove an item by name. Returns True if removed, False if not found."""
    key = _normalise(item_name)
    if key in _shopping_list:
        del _shopping_list[key]
        return True
    return False


def modify_item(item_name: str, quantity: float) -> Optional[CartItem]:
    """Update the quantity of an existing item. Returns updated item or None."""
    key = _normalise(item_name)
    if key in _shopping_list:
        _shopping_list[key].quantity = round(quantity, 2)
        return _shopping_list[key]
    return None


def get_cart() -> List[CartItem]:
    """Return the full shopping list as an ordered list."""
    return list(_shopping_list.values())


def clear_cart() -> None:
    """Remove all items from the shopping list."""
    _shopping_list.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  Catalog search
# ─────────────────────────────────────────────────────────────────────────────

def search_catalog(
    item_name: Optional[str] = None,
    filter_criteria: Optional[FilterCriteria] = None,
) -> List[ProductResult]:
    """
    Filter the mock product catalog by name, brand, price range, and tags.
    All filters are applied cumulatively (AND logic).
    """
    results: List[ProductResult] = MOCK_PRODUCT_CATALOG.copy()

    if item_name:
        query = _normalise(item_name)
        results = [p for p in results if query in p.name.lower()]

    if filter_criteria:
        if filter_criteria.brand:
            brand_q = filter_criteria.brand.lower()
            results = [p for p in results if p.brand and brand_q in p.brand.lower()]

        if filter_criteria.max_price is not None:
            results = [p for p in results if p.price is not None and p.price <= filter_criteria.max_price]

        if filter_criteria.min_price is not None:
            results = [p for p in results if p.price is not None and p.price >= filter_criteria.min_price]

        if filter_criteria.tags:
            filter_tags = {t.lower() for t in filter_criteria.tags}
            results = [p for p in results if filter_tags.intersection({t.lower() for t in p.tags})]

    return results
