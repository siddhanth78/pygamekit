"""The player's inventory: a 4 x 4 grid of item stacks, at most STACK_MAX of each.

Items are listed in ITEMS (grid order). Sunside Tokens, the currency, are permanent:
always the first slot, even at 0. Fish live in the fish log's bag and unspent mastery
points in Missions.unspent (their long-standing saves); tokens and anything added later
are kept in Missions.items, saved as "inventory". contents() gathers them all.
"""

from __future__ import annotations

from dataclasses import dataclass


STACK_MAX = 999
GRID = (4, 4)                       # Columns, rows.


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    atlas: str                      # Icon sprite.
    sprite: str
    note: str                       # One line: what it's for.
    permanent: bool = False         # Always shown (first), even at 0.


ITEMS = (
    Item("sunside_tokens", "Sunside Tokens", "marker-atlas", "icon_token",
         "Sunside's currency. Earned in different ways around the island.", permanent=True),
    Item("fish_common", "Common fish", "people-atlas", "fish_common",
         "Trade at a jungle fish trader for 1 mastery point each."),
    Item("fish_uncommon", "Uncommon fish", "people-atlas", "fish_uncommon",
         "Trade at a jungle fish trader for 2 mastery points each."),
    Item("fish_rare", "Rare fish", "people-atlas", "fish_rare",
         "Trade at a jungle fish trader for 3 mastery points each."),
    Item("fish_epic", "Epic fish", "people-atlas", "fish_epic",
         "Trade at a jungle fish trader for 5 mastery points each."),
    Item("mastery_points", "Mastery points", "marker-atlas", "icon_points",
         "Spend on any region: Pause > Mastery > SPEND POINTS."),
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


def room(count: int) -> int:
    """How many more fit on a stack of `count`."""
    return max(0, STACK_MAX - count)
