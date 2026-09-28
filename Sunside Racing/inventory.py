"""The player's inventory: a 7 x 4 grid of item stacks, each capped at its item's max
(999 unless the item says otherwise: Sunside Tokens go to TOKEN_MAX, one-time passes
to 1).

Items are listed in ITEMS (grid order). Sunside Tokens, the currency, are permanent:
always the first slot, even at 0. Fish live in the fish log's bag and unspent mastery
points in Missions.unspent (their long-standing saves); tokens and anything added later
are kept in Missions.items, saved as "inventory". contents() gathers them all.
"""

from __future__ import annotations

from dataclasses import dataclass


STACK_MAX = 999
TOKEN_MAX = 999_999             # The currency: store passes cost up to 100,000.
GRID = (7, 4)                       # Columns, rows.


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    atlas: str                      # Icon sprite.
    sprite: str
    note: str                       # One line: what it's for.
    permanent: bool = False         # Always shown (first), even at 0.
    max_stack: int = STACK_MAX
    price: int = 0                  # Sunside Tokens at the General Store (0: not sold).
    aisle: str = ""                 # Store aisle it's sold in.
    unlocks: str = ""               # Paying unlocks this (e.g. an arcade game) instead of
                                    # adding the item: it never enters the inventory.


ITEMS = (
    Item("sunside_tokens", "Sunside Tokens", "marker-atlas", "icon_token",
         "The island's currency.", permanent=True,
         max_stack=TOKEN_MAX),
    Item("fish_common", "Common fish", "people-atlas", "fish_common",
         "Trades for 1 mastery."),
    Item("fish_uncommon", "Uncommon fish", "people-atlas", "fish_uncommon",
         "Trades for 2 mastery."),
    Item("fish_rare", "Rare fish", "people-atlas", "fish_rare",
         "Trades for 3 mastery."),
    Item("fish_epic", "Epic fish", "people-atlas", "fish_epic",
         "Trades for 5 mastery."),
    Item("mastery_points", "Mastery points", "marker-atlas", "icon_points",
         "Spend on any region."),
    # Sold at the General Store (aisles: seeds, items, tickets).
    Item("seeds_corn", "Corn seeds", "store-atlas", "seeds_corn",
         "Plants one corn crop.", price=10, aisle="seeds"),
    Item("seeds_tomato", "Tomato seeds", "store-atlas", "seeds_tomato",
         "Plants one tomato crop.", price=20, aisle="seeds"),
    Item("seeds_lettuce", "Lettuce seeds", "store-atlas", "seeds_lettuce",
         "Plants one lettuce crop.", price=30, aisle="seeds"),
    Item("super_fertilizer", "Super fertilizer", "store-atlas", "super_fertilizer",
         "Grows a sprout at once.", price=50, aisle="items"),
    Item("cow_feed", "Cow feed", "store-atlas", "cow_feed",
         "Feeds a cow for 1 milk.", price=10, aisle="items"),
    Item("hen_feed", "Hen feed", "store-atlas", "hen_feed",
         "Feeds a hen for 1 egg.", price=10, aisle="items"),
    # From the farm (for deliveries, coming soon).
    Item("corn", "Corn", "farm-atlas", "corn", "Sold to farm buyers."),
    Item("tomato", "Tomatoes", "farm-atlas", "tomato", "Sold to farm buyers."),
    Item("lettuce", "Lettuce", "farm-atlas", "lettuce", "Sold to farm buyers."),
    Item("milk", "Milk", "farm-atlas", "milk", "Sold to farm buyers."),
    Item("eggs", "Eggs", "farm-atlas", "eggs", "Sold to farm buyers."),
    Item("fair_ticket", "Fair ticket", "store-atlas", "fair_ticket",
         "Entry to the Snow Fair.", price=100, aisle="tickets"),
    Item("game_ticket", "Game ticket", "fair-atlas", "game_ticket",
         "One fair game or ride."),
    # From the Mining Factory, shipped at its dock.
    *(Item(f"stone_{s}", f"{s.title()} stone", "factory-atlas", f"stone_{s}", "Ship for tokens, mastery.")
      for s in ("iron", "copper", "silver", "gold")),
    Item("factory_pass", "Factory pass", "store-atlas", "factory_pass",
         "Unlocks the factory.", max_stack=1, price=20_000, aisle="tickets"),
    Item("arcade_tow_train", "Tow Train cartridge", "store-atlas", "arcade_tow_train",
         "Unlocks Tow Train.", max_stack=1, price=10_000, aisle="games", unlocks="tow_train"),
    Item("island_pass", "Island pass", "store-atlas", "island_pass",
         "Passage to the island.", max_stack=1, price=100_000, aisle="tickets"),
)
BY_ID = {item.id: item for item in ITEMS}


def count_of(missions, item_id: str) -> int:
    if item_id.startswith("fish_"):
        return missions.fish.bag[item_id.removeprefix("fish_")]
    if item_id == "mastery_points":
        return missions.unspent
    return missions.items.get(item_id, 0)


def contents(missions) -> list[tuple[Item, int]]:
    """Every stack the player holds, in ITEMS order (at most one grid's worth)."""
    stacks = [(item, count_of(missions, item.id)) for item in ITEMS]
    return [(item, count) for item, count in stacks if count > 0 or item.permanent][:GRID[0] * GRID[1]]


def room(count: int, item_id: str | None = None) -> int:
    """How many more fit on a stack of `count` (of item_id, or a regular 999 stack)."""
    limit = BY_ID[item_id].max_stack if item_id in BY_ID else STACK_MAX
    return max(0, limit - count)
