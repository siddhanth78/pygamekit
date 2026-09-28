"""The rural farm: the farmhouse, its 5 x 5 mud plot, and the animals inside.

The farmhouse stands abandoned (boarded up) until the player reaches rural level
FARM_LEVEL; E at its door then makes it theirs and pays FARM_GIFT Sunside Tokens, once.
Outside, each plot tile takes one store seed (E), grows at once with one super
fertilizer (E), and is harvested into the inventory (E). Inside (its own level, like the
house) two cows and four hens turn cow feed into milk and hen feed into eggs, one each.

Farm holds the saved state (owned, the plot); FarmInterior stands in for the world while
the player is inside.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from inventory import BY_ID, count_of, room
from world import FARM_HOUSE_SIZE, TILE_SIZE, Sprite


FARM_LEVEL = 6                 # Rural level at which the farmhouse becomes the player's.
FARM_GIFT = 500                # Sunside Tokens given when it does.
PLOT_SIZE = 25                 # The 5 x 5 mud plot.
CROPS = ("corn", "tomato", "lettuce")
CROP_NAMES = {"corn": "Corn", "tomato": "Tomato", "lettuce": "Lettuce"}
STAGES = ("sprout", "ripe")
ANIMAL_FEED = {"cow": ("cow_feed", "milk"), "hen": ("hen_feed", "eggs")}


def seed_of(crop: str) -> str:
    return f"seeds_{crop}"


class Farm:
    """Saved farm state: whether it's the player's, and what grows on each plot tile
    (None, or [crop, "sprout" | "ripe"]), row by row."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.owned = data.get("owned") is True
        plot = data.get("plot")
        self.plot: list[list | None] = [None] * PLOT_SIZE
        if isinstance(plot, list) and len(plot) == PLOT_SIZE:
            for i, cell in enumerate(plot):
                if (isinstance(cell, list) and len(cell) == 2
                        and cell[0] in CROPS and cell[1] in STAGES):
                    self.plot[i] = list(cell)

    def to_dict(self) -> dict:
        return {"owned": self.owned, "plot": [list(c) if c else None for c in self.plot]}

    # Owning it -------------------------------------------------------------------------

    def claim(self, missions) -> bool:
        """Make the farmhouse the player's at rural level FARM_LEVEL, paying FARM_GIFT
        once. Returns True when it was claimed just now."""
        if self.owned or missions.progress.levels["rural"] < FARM_LEVEL:
            return False
        self.owned = True
        missions.add_item("sunside_tokens", FARM_GIFT)
        return True

    # The plot ----------------------------------------------------------------------------

    @staticmethod
    def seeds_owned(missions) -> list[str]:
        """Crops the player has seeds for, in CROPS order."""
        return [crop for crop in CROPS if count_of(missions, seed_of(crop)) > 0]

    def plant(self, missions, index: int, crop: str) -> str | None:
        """Plant one seed on an empty tile; returns why not, or None when planted."""
        if self.plot[index]:
            return "Something is already growing here."
        if count_of(missions, seed_of(crop)) <= 0:
            return f"You have no {BY_ID[seed_of(crop)].name.lower()}."
        missions.add_item(seed_of(crop), -1)
        self.plot[index] = [crop, "sprout"]
        return None

    def fertilize(self, missions, index: int) -> str | None:
        """Grow a sprout at once with one super fertilizer."""
        cell = self.plot[index]
        if not cell or cell[1] != "sprout":
            return "There's nothing here to fertilize."
        if count_of(missions, "super_fertilizer") <= 0:
            return f"You need super fertilizer ({BY_ID['super_fertilizer'].price} S)."
        missions.add_item("super_fertilizer", -1)
        cell[1] = "ripe"
        return None

    def harvest(self, missions, index: int) -> str | None:
        """Pick a ripe crop into the inventory."""
        cell = self.plot[index]
        if not cell or cell[1] != "ripe":
            return "Nothing is ready to harvest here."
        crop = cell[0]
        if room(count_of(missions, crop), crop) <= 0:
            return f"You can't carry more {BY_ID[crop].name.lower()} ({BY_ID[crop].max_stack})."
        missions.add_item(crop, 1)
        self.plot[index] = None
        return None


def feed(missions, animal: str) -> str | None:
    """Feed one animal: one feed in, one milk (cow) or egg (hen) out. Returns why not."""
    feed_id, product = ANIMAL_FEED[animal]
    if count_of(missions, feed_id) <= 0:
        return f"You need {BY_ID[feed_id].name.lower()} ({BY_ID[feed_id].price} S)."
    if room(count_of(missions, product), product) <= 0:
        return f"You can't carry more {BY_ID[product].name.lower()} ({BY_ID[product].max_stack})."
    missions.add_item(feed_id, -1)
    missions.add_item(product, 1)
    return None


def plot_index(site, x: float, y: float) -> int | None:
    """Which plot tile (0-24, row by row) is under (x, y), if any."""
    tile = (int(x // TILE_SIZE), int(y // TILE_SIZE))
    tiles = site.plot_tiles()
    return tiles.index(tile) if tile in tiles else None


def outdoor_sprites(site, farm: Farm) -> list[Sprite]:
    """What the farm adds to the world: boards over the house until it's owned, and
    whatever grows on the plot."""
    out = []
    if not farm.owned:
        out.append(Sprite("structure-atlas", "farmhouse_abandoned", *site.house, FARM_HOUSE_SIZE, FARM_HOUSE_SIZE))
    for (tx, ty), cell in zip(site.plot_tiles(), farm.plot):
        if cell:
            name = "sprout" if cell[1] == "sprout" else f"plant_{cell[0]}"
            out.append(Sprite("farm-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                              TILE_SIZE, TILE_SIZE))
    return out


# Inside the farmhouse ---------------------------------------------------------------------

COLS, ROWS = 14, 9
DOOR_TILE = (7, 8)                     # Front door, in the bottom wall.
WINDOWS = ((3, 0), (10, 0), (3, 8), (11, 8))
FLOOR = "floor_straw"
FIXTURE = 128                          # farm-atlas cells drawn at 2x, like the floor.
COWS = (("Daisy", "cow_a", 1.9, 2.3), ("Clover", "cow_b", 1.9, 5.3))
HENS = ("hen_white", "hen_brown", "hen_white", "hen_brown")
COOP = (9.3, 1.5, 11.7, 7.4)           # Where hens wander: tile x0, y0, x1, y1.
HEN_SPEED = 34
HEN_REACH = 52


def at(tx: float, ty: float) -> tuple[float, float]:
    return tx * TILE_SIZE, ty * TILE_SIZE


@dataclass(frozen=True)
class Spot:
    """Something to do with E near (x, y): door, cow (feed it), or look (a remark)."""
    kind: str
    label: str
    x: float
    y: float
    reach: float = 44
    key: str = ""


class Hen:
    def __init__(self, sprite: str, x: float, y: float, rng: random.Random):
        self.sprite, self.x, self.y = sprite, x, y
        self.heading = rng.uniform(0, 360)
        self.target = (x, y)
        self.wait = rng.uniform(0.5, 2.5)

    def update(self, dt, rng):
        if self.wait > 0:
            self.wait -= dt
            if self.wait <= 0:
                x0, y0, x1, y1 = COOP
                self.target = (rng.uniform(x0, x1) * TILE_SIZE, rng.uniform(y0, y1) * TILE_SIZE)
            return
        dx, dy = self.target[0] - self.x, self.target[1] - self.y
        distance = math.hypot(dx, dy)
        if distance < 2:
            self.wait = rng.uniform(1.0, 3.5)       # Peck about a while.
            return
        step = min(distance, HEN_SPEED * dt)
        self.x += dx / distance * step
        self.y += dy / distance * step
        self.heading = math.degrees(math.atan2(dx, -dy))


class FarmInterior:
    """Stands in for the world while the player is inside the farmhouse."""

    def __init__(self, seed: int = 0):
        self.width, self.height = COLS * TILE_SIZE, ROWS * TILE_SIZE
        self.rng = random.Random(seed + 777)
        self.entry = at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.6)
        self.tiles = {(tx, ty): self._tile(tx, ty) for ty in range(ROWS) for tx in range(COLS)}
        self.fixtures = self._fixtures()
        self.obstacles = [f for f in self.fixtures if f.solid_width]
        self.spots = self._spots()
        x0, y0, x1, y1 = COOP
        self.hens = [Hen(name, *at(x0 + (x1 - x0) * (i % 2 + 0.5) / 2, y0 + (y1 - y0) * (i // 2 + 0.5) / 2),
                         self.rng) for i, name in enumerate(HENS)]

    def _tile(self, tx, ty):
        if (tx, ty) == DOOR_TILE:
            return "wall_door"
        if (tx, ty) in WINDOWS:
            return "wall_window"
        if tx in (0, COLS - 1) or ty in (0, ROWS - 1):
            return "wall"
        return FLOOR

    def _fixtures(self):
        def piece(name, tx, ty, rotation=0.0, solid=(0, 0), size=FIXTURE):
            return Sprite("farm-atlas", name, *at(tx, ty), size, size, rotation, *solid)

        out = []
        for _, sprite, tx, ty in COWS:                         # Cows face their troughs (east).
            out.append(piece(sprite, tx, ty, -90.0, (40, 92)))
            out.append(piece("trough", tx + 1.4, ty, 90.0, (96, 24)))
        for ty in (0.9, 3.8, 6.8):                             # Stall rails.
            out.append(piece("fence_rail", 2.0, ty, 0.0, (120, 10)))
        for ty in (1.95, 5.55, 7.2):                           # The coop's fence, a gate at y 3-4.5.
            out.append(piece("fence_rail", 8.7, ty, 90.0, (120, 10)))
        for ty in (1.6, 3.2, 4.8):                             # Nest boxes on the east wall.
            out.append(piece("nest_box", 12.45, ty, 90.0, (40, 60)))
        out.append(piece("feeder", 10.6, 6.9, 0.0, (30, 30)))
        out.append(piece("hay_pile", 6.2, 1.5, 0.0, (80, 44)))
        out.append(piece("hay_pile", 5.0, 7.3, 0.0, (80, 44)))
        return out

    def _spots(self):
        spots = [Spot("door", "Go outside", *at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.45), 40)]
        for name, _, tx, ty in COWS:
            spots.append(Spot("cow", f"Feed {name}", *at(tx + 2.2, ty), 44, key=name))
        spots.append(Spot("look", "Nest boxes", *at(11.7, 3.2), 40, key="Straw nests. The hens lay here."))
        spots.append(Spot("look", "Hay", *at(6.2, 2.3), 40, key="Fresh hay for the animals."))
        return spots

    # World stand-in -----------------------------------------------------------------

    def region_at(self, x, y):
        return "farmhouse"

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
        for hen in self.hens:
            hen.update(dt, self.rng)

    def visible_sprites(self):
        out = [Sprite("terrain-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                      TILE_SIZE, TILE_SIZE) for (tx, ty), name in self.tiles.items()]
        out += self.fixtures
        out += [Sprite("farm-atlas", h.sprite, h.x, h.y, 56, 56, -h.heading) for h in self.hens]
        return out

    # Where the player is --------------------------------------------------------------

    def spot_near(self, x, y):
        """The closest thing within reach: a fixed spot, or a hen (kind "hen")."""
        near = [(math.dist((x, y), (s.x, s.y)), s) for s in self.spots
                if math.dist((x, y), (s.x, s.y)) <= s.reach]
        for hen in self.hens:
            d = math.dist((x, y), (hen.x, hen.y))
            if d <= HEN_REACH:
                near.append((d, Spot("hen", "Feed the hen", hen.x, hen.y, HEN_REACH)))
        return min(near, key=lambda item: item[0])[1] if near else None

    def room_at(self, x, y) -> str:
        return "Cow stalls" if x < 4.8 * TILE_SIZE else "Hen coop" if x > 8.7 * TILE_SIZE else "Farmhouse"
