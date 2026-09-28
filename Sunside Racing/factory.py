"""The Desert Mining Factory: stones made from biofuel, shipped for tokens and mastery.

A factory on the desert's shore with a biofuel depot beside it and a cargo dock on the
beach. One Factory pass unlocks it for good. At the depot, corn becomes biofuel (1 corn,
1 biofuel) in a tank that holds TANK_MAX; the player never carries biofuel. Inside (its
own level), the stone machine runs on biofuel drawn from the tank: the more a run
burns, the rarer the stone (RUNS). At the dock, every stone carried ships at once for
Sunside Tokens (the stone market's price) and universal mastery (MASTERY).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from inventory import BY_ID, count_of, room
from pedestrians import Pedestrian
from world import SECTOR_SIZE, TILE_SIZE, TILES_PER_SECTOR, Sprite


TANK_MAX = 200
STONES = ("iron", "copper", "silver", "gold")            # Least to most valuable.
STONE_NAMES = {s: f"{s.title()} stone" for s in STONES}
# The stone market: a stone ships for what it costs to make plus that stone's market
# margin (MARGIN_RANGE), and a fixed mastery. Every shipment moves the market: each
# stone's margin is drawn again. A stone costs the biofuel of the run whose floor it is
# (iron 2, copper 5, silver 10, gold 20), and each biofuel is 1 corn: a corn seed plus
# the super fertilizer that grows it.
MARGIN_RANGE = (-0.10, 0.20)
HISTORY = 12                   # Margins remembered per stone (the market board's chart).
MASTERY = {"iron": 3, "copper": 5, "silver": 10, "gold": 25}
FLOOR_RUN = {"iron": 2, "copper": 5, "silver": 10, "gold": 20}
CORN_COST = BY_ID["seeds_corn"].price + BY_ID["super_fertilizer"].price      # 10 + 50 = 60 S


def stone_cost(stone: str) -> int:
    return FLOOR_RUN[stone] * CORN_COST


class Market:
    """Each stone's recent margins, newest last; the last one sets today's price."""

    def __init__(self, data=None, rng: random.Random | None = None):
        rng = rng or random.Random()
        low, high = MARGIN_RANGE
        self.history: dict[str, list[float]] = {}
        for stone in STONES:
            saved = data.get(stone) if isinstance(data, dict) else None
            if (isinstance(saved, list) and saved
                    and all(type(v) in (int, float) and low - 1e-9 <= v <= high + 1e-9 for v in saved)):
                self.history[stone] = [float(v) for v in saved][-HISTORY:]
            else:
                self.history[stone] = [self._draw(rng)]

    @staticmethod
    def _draw(rng) -> float:
        return round(rng.uniform(*MARGIN_RANGE), 2)

    def margin(self, stone: str) -> float:
        return self.history[stone][-1]

    def price(self, stone: str, margin: float | None = None) -> int:
        return round(stone_cost(stone) * (1 + (self.margin(stone) if margin is None else margin)))

    def shift(self, rng: random.Random):
        """A shipment went out: every stone's margin moves."""
        for stone in STONES:
            self.history[stone] = (self.history[stone] + [self._draw(rng)])[-HISTORY:]

    def to_dict(self) -> dict:
        return {stone: list(h) for stone, h in self.history.items()}
# Biofuel burned -> (stone, chance): the first stone is the floor, never worse.
RUNS = {2: (("iron", 0.8), ("copper", 0.2)), 5: (("copper", 0.8), ("silver", 0.2)),
        10: (("silver", 0.9), ("gold", 0.1)), 20: (("gold", 1.0),)}
RUN_TIME = 3.0                                          # Seconds the machine works.


def stone_item(stone: str) -> str:
    return f"stone_{stone}"


def roll(size: int, rng: random.Random) -> str:
    """The stone a run of `size` biofuel makes."""
    r, total = rng.random(), 0.0
    for stone, chance in RUNS[size]:
        total += chance
        if r < total:
            return stone
    return RUNS[size][-1][0]


def odds_line(size: int) -> str:
    """What a run of `size` biofuel makes, e.g. "80% iron · 20% copper"."""
    return "  ·  ".join(f"{round(c * 100)}% {s}" for s, c in RUNS[size])


class FactoryState:
    """Saved as missions "factory": unlocked (the pass was used) and the depot's tank."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.unlocked = data.get("unlocked") is True
        tank = data.get("tank")
        self.tank = tank if type(tank) is int and 0 <= tank <= TANK_MAX else 0
        self.market = Market(data.get("market"))

    def to_dict(self) -> dict:
        return {"unlocked": self.unlocked, "tank": self.tank, "market": self.market.to_dict()}

    def unlock(self, missions) -> bool:
        """Use the Factory pass: the factory is open for good."""
        if self.unlocked or count_of(missions, "factory_pass") < 1:
            return False
        missions.add_item("factory_pass", -1)
        self.unlocked = True
        return True

    def fill(self, missions) -> int:
        """Turn as much corn as fits into biofuel (1 each); returns how much went in."""
        amount = min(count_of(missions, "corn"), TANK_MAX - self.tank)
        if amount > 0:
            missions.add_item("corn", -amount)
            self.tank += amount
        return max(0, amount)

    def burn(self, size: int) -> bool:
        if size not in RUNS or self.tank < size:
            return False
        self.tank -= size
        return True


def shipment(missions):
    """Stones carried: [(stone, count)], total tokens at today's prices, total mastery."""
    market = missions.factory.market
    rows = [(s, count_of(missions, stone_item(s))) for s in STONES]
    rows = [(s, n) for s, n in rows if n]
    return rows, sum(market.price(s) * n for s, n in rows), sum(MASTERY[s] * n for s, n in rows)


def ship(missions, rng: random.Random | None = None) -> str | None:
    """Ship every stone carried at today's prices, then the market moves; returns why
    not, or None when shipped and paid."""
    rows, tokens, mastery = shipment(missions)
    if not rows:
        return "Bring stones from the factory to ship them."
    if missions.tokens + tokens > BY_ID["sunside_tokens"].max_stack:
        return "Your Sunside Tokens are full."
    if mastery > room(missions.unspent):
        return "Your mastery points are full. Spend some first."
    for stone, n in rows:
        missions.add_item(stone_item(stone), -n)
    missions.add_item("sunside_tokens", tokens)
    missions.add_universal(mastery)
    missions.factory.market.shift(rng or random.Random())
    return None


# The site -------------------------------------------------------------------------------

@dataclass(frozen=True)
class FactorySite:
    sector: tuple[int, int]            # The desert sector with the factory and depot.
    seaward: tuple[int, int]           # Direction to the beach sector (the dock) and the sea.

    def at(self, u: float, v: float) -> tuple[float, float]:
        """World px of site-local tiles: u runs seaward from the desert sector's inland
        edge (0-8 in it, 8-16 on the beach, beyond is sea); v runs across (-4 to 4)."""
        sx, sy = self.sector
        dx, dy = self.seaward
        cx, cy = (sx + 0.5) * SECTOR_SIZE, (sy + 0.5) * SECTOR_SIZE
        along, across = (u - 4) * TILE_SIZE, v * TILE_SIZE
        return cx + dx * along - dy * across, cy + dy * along + dx * across

    @property
    def beach(self):
        return self.sector[0] + self.seaward[0], self.sector[1] + self.seaward[1]

    @property
    def building(self):
        return self.at(3.0, -0.4)

    @property
    def door(self):
        return self.at(3.0, 2.25)

    @property
    def depot(self):
        return self.at(6.4, -2.2)

    @property
    def depot_spot(self):
        return self.at(6.4, -0.95)

    @property
    def dock(self):
        return self.at(15.4, 0.0)

    @property
    def dock_spot(self):
        return self.at(13.7, 0.0)

    def sectors(self):
        sea = (self.beach[0] + self.seaward[0], self.beach[1] + self.seaward[1])
        return [self.sector, self.beach, sea]


def choose_site(world) -> FactorySite:
    """A desert sector with beach, then sea, beside it (beach on both sides of that
    beach too), clear of the highway, the desert center, and the piers; nearest the
    desert's middle. Rules only (camps are placed after it and keep clear), so it's the
    same on every seed."""
    from world import CENTERS, SECTORS
    desert = [(x, y) for y in range(SECTORS) for x in range(SECTORS) if world.region(x, y) == "desert"]
    mid = (sum(x + 0.5 for x, _ in desert) / len(desert), sum(y + 0.5 for _, y in desert) / len(desert))
    center = next(s for s, name in CENTERS.items() if name == "center_desert")
    near_highway = world.roads.sectors()
    piers = {(d.tiles[0][0] // TILES_PER_SECTOR, d.tiles[0][1] // TILES_PER_SECTOR) for d in world.docks}
    piers.add(world.mainland_dock)
    best = None
    for sx, sy in desert:
        for dx, dy in ((1, 0), (0, -1), (-1, 0), (0, 1)):
            beach, sea = (sx + dx, sy + dy), (sx + 2 * dx, sy + 2 * dy)
            sides = [(beach[0] + dy, beach[1] + dx), (beach[0] - dy, beach[1] - dx)]
            if world.region(*beach) != "beach" or world.region(*sea) != "sea":
                continue
            if any(world.region(*s) != "beach" for s in sides):
                continue
            around = [(sx + i, sy + j) for i in (-1, 0, 1) for j in (-1, 0, 1)] + [beach] + sides
            if any(a in near_highway for a in around):   # Camps keep clear of it instead.
                continue
            if max(abs(sx - center[0]), abs(sy - center[1])) < 3:
                continue
            if any(max(abs(beach[0] - px), abs(beach[1] - py)) <= 2 for px, py in piers):
                continue
            key = ((sx + 0.5 - mid[0]) ** 2 + (sy + 0.5 - mid[1]) ** 2, sx, sy)
            if best is None or key < best[0]:
                best = (key, FactorySite((sx, sy), (dx, dy)))
    return best[1]


def exterior_ground(site: FactorySite, tx: int, ty: int) -> str | None:
    """The concrete yard around the factory and depot (world tile), or None."""
    x, y = (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE
    for u in range(8):
        for v in range(-4, 4):
            if u >= 1 and -3 <= v <= 3:
                cx, cy = site.at(u + 0.5, v + 0.5)
                if abs(cx - x) < 1 and abs(cy - y) < 1:
                    return "city_concrete"
    return None


def exterior_sprites(site: FactorySite) -> list[Sprite]:
    """The factory, its depot and pipe, crates, the cargo dock, and a freighter."""
    turn = {(1, 0): 0.0, (-1, 0): 180.0, (0, -1): 90.0, (0, 1): -90.0}[site.seaward]   # Art runs east.
    front = {(1, 0): 0.0, (0, -1): 90.0, (-1, 0): 180.0, (0, 1): -90.0}[site.seaward]

    def piece(name, u, v, size, rotation=0.0, solid=(0, 0)):
        return Sprite("factory-atlas", name, *site.at(u, v), size, size, rotation, *solid)

    return [
        # The building's front (south in the art) faces +v, where its door is.
        piece("factory_building", 3.0, -0.4, 272, front, (250, 200)),
        piece("depot_tank", 6.4, -2.2, 128, 0.0, (100, 100)),
        piece("pipe", 5.3, -2.2, 96, turn, (0, 0)),
        piece("crate_stack", 6.6, 2.4, 96, 0.0, (72, 72)),
        piece("barrel", 1.4, 2.8, 64, 0.0, (36, 36)),
        piece("crate_stack", 12.8, -2.2, 96, 0.0, (72, 72)),
        piece("crate_stack", 12.8, 2.2, 96, 0.0, (72, 72)),
        piece("cargo_dock", 15.4, 0.0, 192, turn, (0, 0)),
        piece("cargo_ship", 19.2, 0.0, 288, turn, (0, 0)),
    ]


# Inside: the factory floor -------------------------------------------------------------------

COLS, ROWS = 16, 10
DOOR_TILE = (8, 9)
WINDOWS = ((3, 0), (6, 0), (10, 0), (13, 0))
FLOOR = "city_concrete"
MACHINE = (8.0, 3.4)
MARKET_BOARD = (4.5, 0.95)                     # On the north wall, between the windows.
MARKET_SPOT = (4.5, 1.9)
BIN = (13.6, 3.6)


def at(tx: float, ty: float) -> tuple[float, float]:
    return tx * TILE_SIZE, ty * TILE_SIZE


@dataclass(frozen=True)
class Spot:
    kind: str                # machine, exit, or look.
    label: str
    x: float
    y: float
    reach: float = 48
    key: str = ""


class FactoryInterior:
    """Stands in for the world while the player is inside the factory."""

    def __init__(self, seed: int = 0):
        self.width, self.height = COLS * TILE_SIZE, ROWS * TILE_SIZE
        self.rng = random.Random(seed + 3141)
        self.entry = at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.6)
        self.tiles = {(tx, ty): self._tile(tx, ty) for ty in range(ROWS) for tx in range(COLS)}
        self.fixtures = self._fixtures()
        self.obstacles = [f for f in self.fixtures if f.solid_width]
        self.spots = [Spot("exit", "Go outside", *at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.45), 44),
                      Spot("market", "Stone market", *at(*MARKET_SPOT), 46),
                      Spot("machine", "Stone machine", *at(MACHINE[0], MACHINE[1] + 2.05), 52),
                      Spot("look", "Stone bin", *at(BIN[0], BIN[1] + 1.35), 44,
                           "Finished stones drop here. Ship them at the dock outside."),
                      Spot("look", "Biofuel pipe", *at(3.0, MACHINE[1] + 0.9), 44,
                           "Biofuel comes in from the depot's tank outside.")]
        self.workers = [Pedestrian(kind, path, 30, start, {0: ("pause", (2.0, 5.0))})
                        for kind, path, start in (
                            ("city_d", [at(2.0, 6.4), at(6.0, 6.4), at(6.0, 8.0), at(2.0, 8.0)], 0.0),
                            ("city_h", [at(10.2, 6.2), at(14.4, 6.2), at(14.4, 8.0), at(10.2, 8.0)], 90.0))]
        self.running = None          # (size, stone, seconds left) while the machine works.
        self.clock = 0.0

    def _tile(self, tx, ty):
        if (tx, ty) == DOOR_TILE:
            return "wall_door"
        if (tx, ty) in WINDOWS:
            return "wall_window"
        if tx in (0, COLS - 1) or ty in (0, ROWS - 1):
            return "wall"
        return FLOOR

    def _fixtures(self):
        def piece(name, tx, ty, size, rotation=0.0, solid=(0, 0)):
            return Sprite("factory-atlas", name, *at(tx, ty), size, size, rotation, *solid)

        out = [piece("stone_machine", *MACHINE, 256, 0.0, (224, 116)),
               piece("market_board", *MARKET_BOARD, 128)]
        out += [piece("pipe", x, MACHINE[1], 96, 0.0, (96, 20)) for x in (1.75, 3.25, 4.75)]
        out += [piece("conveyor", x, BIN[1], 96, 0.0, (96, 40)) for x in (10.6, 12.0)]
        out.append(piece("stone_bin", *BIN, 128, 0.0, (100, 80)))
        out += [piece("barrel", 1.6, 7.9, 64, 0.0, (36, 36)), piece("barrel", 2.4, 8.2, 64, 0.0, (36, 36)),
                piece("crate_stack", 14.0, 7.9, 96, 0.0, (72, 72))]
        return out

    # World stand-in -----------------------------------------------------------------

    def region_at(self, x, y):
        return "factory"

    def can_place_walker(self, rect):
        half_w, half_h = rect[7] / 2, rect[8] / 2
        return all(self.tiles.get((int((rect[0] + dx) // TILE_SIZE), int((rect[1] + dy) // TILE_SIZE))) == FLOOR
                   for dx in (-half_w, half_w) for dy in (-half_h, half_h))

    def can_place_car(self, rect):
        return False

    def nearby_obstacles(self, x, y):
        return [o for o in self.obstacles if abs(o.x - x) < 200 and abs(o.y - y) < 200]

    # The machine ----------------------------------------------------------------------

    def start(self, size: int, rng: random.Random):
        """Burn `size` biofuel (the caller took it from the tank): the stone is decided now
        and comes out after RUN_TIME."""
        self.running = [size, roll(size, rng), RUN_TIME]

    def update(self, dt):
        """Returns the stone when a run finishes, else None."""
        self.clock += dt
        for worker in self.workers:
            worker.update(dt, self.rng, False)
        if self.running:
            self.running[2] -= dt
            if self.running[2] <= 0:
                stone, self.running = self.running[1], None
                return stone
        return None

    def visible_sprites(self):
        out = [Sprite("terrain-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                      TILE_SIZE, TILE_SIZE) for (tx, ty), name in self.tiles.items()]
        out += self.fixtures
        if self.running:
            # The furnace glows (pulsing) and the stone rides the conveyor to the bin.
            if int(self.clock * 6) % 2 == 0:
                out.append(Sprite("factory-atlas", "machine_glow", *at(*MACHINE), 256, 256))
            share = 1 - self.running[2] / RUN_TIME
            if share > 0.5:
                x = at(9.8, 0)[0] + (at(BIN[0], 0)[0] - at(9.8, 0)[0]) * (share - 0.5) * 2
                out.append(Sprite("factory-atlas", f"stone_{self.running[1]}", x, at(0, BIN[1] - 0.1)[1], 40, 40))
        out += [w.sprite() for w in self.workers]
        return out

    def spot_near(self, x, y):
        near = [(math.dist((x, y), (s.x, s.y)), s) for s in self.spots
                if math.dist((x, y), (s.x, s.y)) <= s.reach]
        return min(near, key=lambda item: item[0])[1] if near else None
