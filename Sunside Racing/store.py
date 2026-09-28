"""The General Store: a level loaded when the player walks in (like the house).

A checkout counter by the door with the cashier, and three aisles: seeds (corn, tomato,
lettuce), items (super fertilizer, cow feed, hen feed), and tickets (fair ticket, factory pass, island pass).
E on a product puts one in the cart; the cashier takes Sunside Tokens for the whole cart;
the door won't let the player out with an unpaid cart (empty it, or stay and pay).
A few shoppers browse and come and go; none of them can be talked to.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from inventory import BY_ID, count_of
from pedestrians import Pedestrian
from world import TILE_SIZE, Sprite


COLS, ROWS = 16, 11
DOOR_TILE = (3, 10)                   # Front door, in the bottom wall.
WINDOWS = ((7, 10), (11, 10), (13, 10))
FLOOR = "city_plaza"
FIXTURE = 128                         # store-atlas cells drawn at 2x, like the floor.
SHELF_XS = (5.2, 7.9, 10.6, 13.3)     # Shelf units (tile x); the aisles run between them.
SHELF_YS = (3.0, 4.9)
AISLES = (("seeds", "Seeds aisle", ("seeds_corn", "seeds_tomato", "seeds_lettuce")),
          ("items", "Items aisle", ("super_fertilizer", "cow_feed", "hen_feed")),
          ("tickets", "Tickets aisle", ("fair_ticket", "factory_pass", "island_pass")))
PRODUCT_YS = {3: (2.7, 4.0, 5.3), 1: (4.0,)}   # Tile y of each product, by how many an aisle has.
PRODUCT_SIZE = 44
# Where E works: bands along the shelf face beside each icon (halfway to the next icon,
# so walking the shelf always prompts the nearest product), along the counter's customer
# side, and the welcome mat for the exit. (Half width, half height; offset from x, y.)
PRODUCT_BAND = ((34, 41.5), (30, 0))
CASHIER_BAND = ((30, 58), (30, 0))
MAT = ((48, 24), (0, 0))
# The game corner: a display stand by the south wall with the arcade cartridge on it.
GAME_STAND = (10.4, 9.25)
GAME_PRODUCTS = ("arcade_tow_train",)
STAND_BAND = ((44, 30), (0, -50))


def at(tx: float, ty: float) -> tuple[float, float]:
    return tx * TILE_SIZE, ty * TILE_SIZE


def aisle_x(index: int) -> float:
    return (SHELF_XS[index] + SHELF_XS[index + 1]) / 2


@dataclass(frozen=True)
class Spot:
    """Something to do with E at (x, y): a product's icon, the counter, or the mat. It
    counts while the player stands in its area (half size, offset from x, y); `stand`
    is a clear place for the player inside it."""
    kind: str            # product, cashier, or door.
    x: float
    y: float
    item: str = ""
    area: tuple = MAT
    stand: tuple[float, float] = (0.0, 0.0)

    def distance(self, x, y) -> float | None:
        """How far (x, y) is from the area's middle, or None when outside it."""
        (hw, hh), (ox, oy) = self.area
        cx, cy = self.x + ox, self.y + oy
        return math.dist((x, y), (cx, cy)) if abs(x - cx) <= hw and abs(y - cy) <= hh else None


class Cart:
    """What the player has picked up but not paid for: item id -> count."""

    def __init__(self):
        self.items: dict[str, int] = {}

    @property
    def count(self) -> int:
        return sum(self.items.values())

    @property
    def total(self) -> int:
        return sum(BY_ID[i].price * n for i, n in self.items.items())

    def take(self, item_id: str, missions) -> str | None:
        """Put one in the cart; returns why not, or None when it went in."""
        item = BY_ID[item_id]
        have, wanted = count_of(missions, item_id), self.items.get(item_id, 0)
        factory = getattr(missions, "factory", None)
        if item_id == "factory_pass" and factory is not None and factory.unlocked:
            return "The factory is already yours; no pass needed."
        if item.unlocks and item.unlocks in getattr(missions, "arcade_unlocked", ()):
            return f"You already own {item.name.removesuffix(' cartridge')}. It's on your arcade at home."
        if item.max_stack == 1 and (have or wanted):
            return f"You already have the {item.name.lower()}." if have else \
                f"The {item.name.lower()} is already in your cart."
        if have + wanted >= item.max_stack:
            return f"You can't carry more {item.name.lower()} ({item.max_stack})."
        self.items[item_id] = wanted + 1
        return None

    def pay(self, missions) -> str | None:
        """Pay for everything; returns why not, or None when paid (items now owned)."""
        if missions.tokens < self.total:
            return f"Not enough Sunside Tokens: {self.total:,} S due, you have {missions.tokens:,} S."
        missions.add_item("sunside_tokens", -self.total)
        for item_id, n in self.items.items():
            if BY_ID[item_id].unlocks:
                missions.arcade_unlocked.add(BY_ID[item_id].unlocks)   # Unlocked, not carried.
            else:
                missions.add_item(item_id, n)
        self.items = {}
        return None

    def unlocks(self) -> list[str]:
        """Names of what the cart unlocks when paid for (e.g. an arcade game)."""
        return [BY_ID[i].name.removesuffix(" cartridge") for i in self.items if BY_ID[i].unlocks]

    def empty(self):
        self.items = {}

    def bill(self) -> list[tuple[str, str]]:
        """One line per item, like a receipt: ("Corn seeds  3 x 10 S", "30 S")."""
        return [(f"{BY_ID[i].name}  {n} x {BY_ID[i].price:,} S", f"{BY_ID[i].price * n:,} S")
                for i, n in self.items.items()]

    def summary(self) -> str:
        return ",  ".join(f"{BY_ID[i].name} x{n}" for i, n in self.items.items())


class StoreInterior:
    """Stands in for the world while the player is in the store."""

    def __init__(self, seed: int = 0):
        self.width, self.height = COLS * TILE_SIZE, ROWS * TILE_SIZE
        self.rng = random.Random(seed + 4242)
        self.cart = Cart()
        self.entry = at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 1.2)
        self.tiles = {(tx, ty): self._tile(tx, ty) for ty in range(ROWS) for tx in range(COLS)}
        self.fixtures = self._fixtures()
        self.obstacles = [f for f in self.fixtures if f.solid_width]
        self.spots = self._spots()
        self.people = self._people()

    def _tile(self, tx, ty):
        if (tx, ty) == DOOR_TILE:
            return "wall_door"
        if (tx, ty) in WINDOWS:
            return "wall_window"
        if tx in (0, COLS - 1) or ty in (0, ROWS - 1):
            return "wall"
        return FLOOR

    def _fixtures(self):
        out = [Sprite("store-atlas", "store_mat", *at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.6), FIXTURE, FIXTURE)]
        out.append(Sprite("store-atlas", "counter", *at(2.0, 6.0), FIXTURE, FIXTURE, 90.0, 116, 44))
        for x in SHELF_XS:
            for y in SHELF_YS:
                out.append(Sprite("store-atlas", "shelf", *at(x, y), FIXTURE, FIXTURE, 0.0, 40, 116))
        out.append(Sprite("store-atlas", "ticket_board", *at(aisle_x(2), 1.2), FIXTURE, FIXTURE, 180.0))
        out.append(Sprite("store-atlas", "game_stand", *at(*GAME_STAND), FIXTURE, FIXTURE, 0.0, 88, 40))
        out.append(Sprite("store-atlas", "crate", *at(14.3, 8.6), 96, 96, 0.0, 60, 60))
        out.append(Sprite("store-atlas", "crate", *at(13.4, 9.1), 96, 96, 0.0, 60, 60))
        out.append(Sprite("home-atlas", "plant", *at(1.4, 9.2), FIXTURE, FIXTURE, 0.0, 26, 26))
        out.append(Sprite("home-atlas", "plant", *at(8.6, 9.2), FIXTURE, FIXTURE, 0.0, 26, 26))
        return out

    def _spots(self):
        """The mat by the door, the counter's customer side, and each product's icon on
        the shelf face it's stocked on (where the player sees it)."""
        mat_x, mat_y = at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.6)
        counter_x, counter_y = at(2.0, 6.0)
        counter_face = counter_x + 22                      # Its solid is 44 px wide.
        spots = [Spot("door", mat_x, mat_y, area=MAT, stand=(mat_x, mat_y)),
                 Spot("cashier", counter_face, counter_y, area=CASHIER_BAND,
                      stand=(counter_face + 20, counter_y))]
        for index, (_, _, items) in enumerate(AISLES):
            icon_x = SHELF_XS[index] * TILE_SIZE + 16      # East face of the aisle's west shelf.
            for item_id, y in zip(items, PRODUCT_YS[len(items)]):
                spots.append(Spot("product", icon_x, y * TILE_SIZE, item_id, area=PRODUCT_BAND,
                                  stand=(icon_x + 28, y * TILE_SIZE)))
        sx, sy = at(*GAME_STAND)
        for item_id in GAME_PRODUCTS:                      # On top of the game stand.
            spots.append(Spot("product", sx, sy - 6, item_id, area=STAND_BAND, stand=(sx, sy - 56)))
        return spots

    def _people(self):
        """The cashier, three browsers, and two shoppers who come in and go out again."""
        pause, door = ("pause", (1.5, 4.0)), ("door", (3.0, 7.0))
        cashier = Pedestrian("city_f", [at(1.35, 6.0)], 0, heading=90.0)
        lanes = [aisle_x(i) for i in range(3)]
        browsers = [
            Pedestrian("city_a", [at(lanes[0], 1.6), at(lanes[0], 6.6), at(lanes[1], 6.6), at(lanes[1], 1.6)],
                       24, 0, {0: pause, 1: pause, 2: pause, 3: pause}),
            Pedestrian("city_c", [at(lanes[2], 6.6), at(lanes[2], 1.6), at(lanes[1], 1.6), at(lanes[1], 6.6)],
                       22, 150, {1: pause, 3: pause}),
            Pedestrian("city_h", [at(6.0, 8.2), at(12.5, 8.2), at(lanes[2], 6.6)], 20, 60, {0: pause, 1: pause}),
        ]
        comers = [Pedestrian(kind, [at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.8), at(3.5, 7.4),
                                    at(lanes[lane], 7.0), at(lanes[lane], 3.4), at(lanes[lane], 7.0), at(3.5, 7.4)],
                             26, start, {0: door, 3: pause})
                  for kind, lane, start in (("city_b", 0, 0), ("city_g", 1, 420))]
        return [cashier] + browsers + comers

    # World stand-in -----------------------------------------------------------------

    def region_at(self, x, y):
        return "store"

    def can_place_walker(self, rect):
        half_w, half_h = rect[7] / 2, rect[8] / 2
        return all(self.tiles.get((int((rect[0] + dx) // TILE_SIZE), int((rect[1] + dy) // TILE_SIZE))) == FLOOR
                   for dx in (-half_w, half_w) for dy in (-half_h, half_h))

    def can_place_car(self, rect):
        return False

    def nearby_obstacles(self, x, y):
        return [o for o in self.obstacles if abs(o.x - x) < 140 and abs(o.y - y) < 140]

    # Each frame -----------------------------------------------------------------------

    def update(self, dt):
        for person in self.people:
            person.update(dt, self.rng, False)

    def visible_sprites(self):
        out = [Sprite("terrain-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                      TILE_SIZE, TILE_SIZE) for (tx, ty), name in self.tiles.items()]
        out += self.fixtures
        for spot in self.spots:
            if spot.kind == "product":   # Its icon on the shelf face.
                out.append(Sprite("store-atlas", spot.item, spot.x, spot.y, PRODUCT_SIZE, PRODUCT_SIZE))
        out += [p.sprite() for p in self.people if not p.inside]
        return out

    # Where the player is --------------------------------------------------------------

    def spot_near(self, x, y):
        near = [(d, s) for s in self.spots if (d := s.distance(x, y)) is not None]
        return min(near, key=lambda item: item[0])[1] if near else None

    def aisle_at(self, x, y) -> str:
        for index, (_, name, _) in enumerate(AISLES):
            if abs(x - aisle_x(index) * TILE_SIZE) < 0.9 * TILE_SIZE and 1.8 * TILE_SIZE < y < 6.2 * TILE_SIZE:
                return name
        gx, gy = at(*GAME_STAND)
        if abs(x - gx) < 1.4 * TILE_SIZE and gy - 2 * TILE_SIZE < y < gy:
            return "Game corner"
        return "Checkout" if x < 4.5 * TILE_SIZE and y > 4.5 * TILE_SIZE else "General Store"
