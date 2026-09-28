"""The Snow Fair: its saved progress, its place in the world, the busy lots outside, and
the fairground level inside.

Outside (in the world): a fenced mini fair (big tent, ferris wheel, carousel) whose only
way in is the ticket booth in the south fence. South of it are two rows of parking lots,
joined to the snow road by a short icy spur road. Visitor cars drive in, park, and leave
again; visitors walk between the lots and the gate.

Inside (a level, like the store): six game booths, three rides, food stands, and crowds.
Entry costs one fair ticket (General Store). Every game attempt and every ride costs one
game ticket, sold at the ticket counter inside the gate (GAME_TICKET_PRICE S each). Booth
bands pay Sunside Tokens once each; booths stay open for fun after platinum. With all six
at platinum the grand prize is the F1 car and a second game on the arcade at home.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from fair_games import BAND_PAY, BANDS, GAMES
from pedestrians import Pedestrian
from world import TILE_SIZE, TILES_PER_SECTOR, Sprite


GAME_IDS = tuple(GAMES)
GAME_TICKET_PRICE = 50
GAME_TICKET_PACKS = (1, 5, 10)      # How many the counter sells at a time.
RIDES = ("ferris_wheel", "carousel", "bumper_cars")
RIDE_NAMES = {"ferris_wheel": "Ferris wheel", "carousel": "Carousel", "bumper_cars": "Bumper cars"}
# Rides turn at a steady rate, and the player rides one seat: exactly one lap of the
# ferris wheel, two of the carousel (so they get off where they got on).
FERRIS_R, FERRIS_CARS, FERRIS_SPIN = 118, 8, 2 * math.pi / 16      # rad/s: 16 s a lap.
CAROUSEL_R, HORSES, CAROUSEL_SPIN = 88, 6, 1.2
RIDE_TIME = {"ferris_wheel": 2 * math.pi / FERRIS_SPIN, "carousel": 4 * math.pi / CAROUSEL_SPIN,
             "bumper_cars": 20.0}
RIDER_KINDS = ("city_a", "city_b", "city_c", "city_d", "city_e", "city_g", "city_h", "snow_a", "snow_b")
RIDE_ZOOM = {"ferris_wheel": 0.9, "carousel": 1.5, "bumper_cars": 1.4}


class FairState:
    """Saved as missions "fair": best score and bands reached per game, and the prize."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        best, band = data.get("best"), data.get("band")
        self.best = {g: best[g] for g in GAME_IDS
                     if isinstance(best, dict) and type(best.get(g)) is int and best[g] >= 0}
        self.band = {g: band[g] for g in GAME_IDS
                     if isinstance(band, dict) and type(band.get(g)) is int and 0 <= band[g] <= len(BANDS)}
        self.f1 = data.get("f1") is True

    def to_dict(self) -> dict:
        return {"best": dict(self.best), "band": dict(self.band), "f1": self.f1}

    def platinum(self, game_id: str) -> bool:
        """Platinum reached (the booth stays open to play for fun)."""
        return self.band.get(game_id, 0) >= len(BANDS)

    def maxed(self) -> int:
        return sum(self.platinum(g) for g in GAME_IDS)

    def record(self, game_id: str, score: int):
        """An attempt ended: returns (tokens earned, bands newly reached, prize won now)."""
        self.best[game_id] = max(self.best.get(game_id, 0), score)
        before, reached = self.band.get(game_id, 0), GAMES[game_id].band(score)
        new = [BANDS[i] for i in range(before, reached)]
        self.band[game_id] = max(before, reached)
        prize = not self.f1 and self.maxed() == len(GAME_IDS)
        if prize:
            self.f1 = True
        return sum(BAND_PAY[b] for b in new), new, prize


# The site -------------------------------------------------------------------------------

BLOCK = 2                      # Sectors wide; the fair's row, then the lots' row below it.
SPUR_COLUMN = 12               # Block-local tile column of the spur road down to the snow road.
SPUR_MAX = 16                  # Tiles the spur may run to reach the snow road.
STALL_OFFSETS = (20, 44)       # Stall centers across a parking_lot tile (like the city's).
STALL_Y = 24
LOT_ROWS = (9, 11)             # Block-local rows of parking_lot tiles (stalls on the top half),
AISLE_ROWS = (10, 12)          # each with an asphalt aisle below.
LOT_COLUMNS = tuple(range(1, 7)) + tuple(range(9, 15))
WALKWAY = (7, 8)               # Block-local columns of the walkway from the lots to the gate.
SHUTTLES = 4                   # Visitor cars that come and go.
VISITORS = 7                   # Visitors walking between the lots and the gate.


@dataclass(frozen=True)
class FairSite:
    sector: tuple[int, int]            # The block's top-left sector.
    spur: tuple[tuple[int, int], ...]  # World tiles of the spur road, north to south.

    @property
    def tile0(self):
        return self.sector[0] * TILES_PER_SECTOR, self.sector[1] * TILES_PER_SECTOR

    def at(self, lx: float, ly: float) -> tuple[float, float]:
        """World px of block-local tile coordinates (x 0-16, y 0-16)."""
        tx, ty = self.tile0
        return (tx + lx) * TILE_SIZE, (ty + ly) * TILE_SIZE

    def sectors(self):
        sx, sy = self.sector
        return [(sx + dx, sy + dy) for dy in range(BLOCK) for dx in range(BLOCK)]

    @property
    def booth(self) -> tuple[float, float]:
        return self.at(8.0, 7.55)

    @property
    def door(self) -> tuple[float, float]:
        """Where the player stands to buy their way in (south of the booth)."""
        return self.at(8.0, 8.55)

    @property
    def center(self) -> tuple[float, float]:
        return self.at(8.0, 4.0)

    def stalls(self):
        """(x, y) of every stall, row by row, west to east."""
        out = []
        for row in LOT_ROWS:
            for col in LOT_COLUMNS:
                for dx in STALL_OFFSETS:
                    x, y = self.at(col, row)
                    out.append((x + dx, y + STALL_Y))
        return out


def spur_for(world, sector) -> tuple | None:
    """The spur's tiles from the lots' south aisle down to the snow road, or None."""
    tx, ty = sector[0] * TILES_PER_SECTOR + SPUR_COLUMN, sector[1] * TILES_PER_SECTOR + AISLE_ROWS[1] + 1
    tiles = []
    for y in range(ty, ty + SPUR_MAX):
        if (tx, y) in world.snow_roads:
            return tuple(tiles)
        if world.region(tx // TILES_PER_SECTOR, y // TILES_PER_SECTOR) != "snow":
            return None
        tiles.append((tx, y))
    return None


def choose_site(world) -> FairSite:
    """The 2 x 2 snow block (plus a sector of margin, all snow) nearest the snow road:
    off the highway, with no road inside it, 3+ sectors from the snow racing center, and
    a spur straight down to the snow road. Rules only, so it's the same on every seed."""
    from world import CENTERS, SECTORS
    center = next(s for s, name in CENTERS.items() if name == "center_snow")
    near_highway = world.roads.sectors()
    snow = [(sx, sy) for sy in range(SECTORS) for sx in range(SECTORS) if world.region(sx, sy) == "snow"]
    best = None
    for sx, sy in snow:
        around = [(sx + dx, sy + dy) for dx in range(-1, BLOCK + 1) for dy in range(-1, BLOCK + 1)]
        if not all(world.region(*s) == "snow" for s in around):
            continue
        if any(s in near_highway or s in world.camps for s in around):
            continue
        if any(max(abs(s[0] - center[0]), abs(s[1] - center[1])) < 3 for s in around):
            continue
        if any(world._road_style(x * TILES_PER_SECTOR + i, y * TILES_PER_SECTOR + j)
               for x, y in around for i in range(TILES_PER_SECTOR) for j in range(TILES_PER_SECTOR)
               if (x, y) in [(sx + dx, sy + dy) for dx in range(BLOCK) for dy in range(BLOCK)]):
            continue
        spur = spur_for(world, (sx, sy))
        if spur is None:
            continue
        key = (len(spur), sx, sy)
        if best is None or key < best[0]:
            best = (key, FairSite((sx, sy), spur))
    return best[1]


# Outside: visitor cars and walkers ----------------------------------------------------------

CAR_SPEED = 90.0
PARKED_SIZE, PARKED_SOLID = 56, (18, 38)
VISITOR_CARS = ("traffic_red", "traffic_blue", "traffic_green", "traffic_white", "traffic_gray",
                "traffic_yellow", "traffic_pickup", "traffic_suv", "traffic_wagon", "traffic_compact")


class Shuttle:
    """A visitor car: drives up the spur into its stall, stays, backs out, and leaves."""

    def __init__(self, site: FairSite, stall, name, rng: random.Random):
        self.name, self.rng = name, rng
        sx, sy = stall
        jx, jy = (site.spur[-1][0] + 0.5) * TILE_SIZE, (site.spur[-1][1] + 1.5) * TILE_SIZE
        top_y = site.at(0, AISLE_ROWS[1] + 0.5)[1]
        spur_x = jx
        aisle = (sx, top_y)
        self.arrive = [(jx, jy), (spur_x, top_y), aisle, (sx, sy)]
        self.leave = [(sx, sy), aisle, (spur_x, top_y), (jx, jy)]
        self.state, self.timer = "away", rng.uniform(0, 20)
        self.path, self.index = self.arrive, 0
        self.x, self.y = jx, jy
        self.heading = 0.0

    @property
    def visible(self):
        return self.state != "away"

    def update(self, dt):
        if self.state in ("away", "parked"):
            self.timer -= dt
            if self.timer <= 0:
                self.state = "arriving" if self.state == "away" else "leaving"
                self.path, self.index = (self.arrive, 0) if self.state == "arriving" else (self.leave, 0)
                self.x, self.y = self.path[0]
            return
        step = CAR_SPEED * dt
        while step > 0 and self.index < len(self.path) - 1:
            tx, ty = self.path[self.index + 1]
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            if dist <= step:
                self.x, self.y, step = tx, ty, step - dist
                self.index += 1
                continue
            self.x += dx / dist * step
            self.y += dy / dist * step
            backing = self.state == "leaving" and self.index == 0   # Reversing out of the stall.
            if not backing:
                self.heading = math.degrees(math.atan2(dx, -dy)) % 360
            step = 0
        if self.index >= len(self.path) - 1:
            if self.state == "arriving":
                self.state, self.timer, self.heading = "parked", self.rng.uniform(20, 50), 0.0
            else:
                self.state, self.timer = "away", self.rng.uniform(8, 25)

    def sprite(self) -> Sprite:
        return Sprite("vehicle-atlas", self.name, self.x, self.y, PARKED_SIZE, PARKED_SIZE,
                      -self.heading, *PARKED_SOLID)


class FairOutside:
    """Visitor cars and walkers at the fair's lots; simulated only while the player is near."""

    ACTIVE_RANGE = 2600

    def __init__(self, site: FairSite, seed: int):
        self.site = site
        rng = random.Random(seed + 5150)
        self.rng = rng
        stalls = site.stalls()
        # The east end of the south row is kept for the cars that come and go.
        shuttle_stalls = [s for s in stalls if s[1] == stalls[-1][1]][-SHUTTLES * 2::2]
        self.shuttles = [Shuttle(site, stall, rng.choice(VISITOR_CARS), random.Random(seed + i))
                         for i, stall in enumerate(shuttle_stalls)]
        self.reserved = set(shuttle_stalls)
        gate_x, gate_y = site.door
        walk_x = site.at(7.5, 0)[0]
        self.walkers = []
        for i in range(VISITORS):
            sx, sy = stalls[rng.randrange(len(stalls))]
            aisle_y = sy + TILE_SIZE - STALL_Y + 8
            path = [(sx, aisle_y), (walk_x + (i % 3 - 1) * 18, aisle_y), (walk_x + (i % 3 - 1) * 18, gate_y),
                    (gate_x + (i % 3 - 1) * 14, gate_y - 10)]
            path = path + path[-2:0:-1]            # There and back.
            kind = rng.choice(("snow_a", "snow_b", "city_a", "city_c", "city_e", "city_g"))
            self.walkers.append(Pedestrian(kind, path, rng.uniform(26, 38), rng.uniform(0, 900),
                                           {3: ("door", (15.0, 45.0)), 0: ("pause", (4.0, 12.0))}))

    def near(self, x, y) -> bool:
        cx, cy = self.site.center
        return abs(x - cx) < self.ACTIVE_RANGE and abs(y - cy) < self.ACTIVE_RANGE

    def update(self, dt, x, y):
        if not self.near(x, y):
            return
        for car in self.shuttles:
            car.update(dt)
        for walker in self.walkers:
            walker.update(dt, self.rng, False)

    def sprites(self):
        return ([c.sprite() for c in self.shuttles if c.visible]
                + [w.sprite() for w in self.walkers if not w.inside])

    def nearby_obstacles(self, x, y):
        """Visitor cars are solid (walkers aren't, like the city's)."""
        return [s for s in (c.sprite() for c in self.shuttles if c.visible)
                if abs(s.x - x) < 145 and abs(s.y - y) < 145]


# Inside: the fairground level ------------------------------------------------------------------

COLS, ROWS = 26, 18
FLOOR = "city_plaza"
EDGE = "snow"                                     # Around the fence: not walkable.
BOOTHS = (("darts", 3.2, 2.4), ("hammer", 6.8, 2.4), ("balloons", 10.4, 2.4),
          ("ring_toss", 3.2, 8.2), ("skee_ball", 6.8, 8.2), ("whack", 10.4, 8.2))
BOOTH_SIZE, BOOTH_SOLID = 160, (148, 84)
FERRIS = (20.0, 4.6)
CAROUSEL = (20.0, 12.4)
ARENA = (2, 11, 11, 15)                           # Bumper car floor: tile x0, y0, x1, y1 (whole tiles).
ENTRY = (13.0, 16.2)
COUNTER = (15.6, 16.35)                           # The game ticket counter, just inside the gate.


def at(tx: float, ty: float) -> tuple[float, float]:
    return tx * TILE_SIZE, ty * TILE_SIZE


@dataclass(frozen=True)
class Spot:
    """E near (x, y): a booth (key = game id), a ride (key = ride id), the exit, or a stand."""
    kind: str
    label: str
    x: float
    y: float
    reach: float = 48
    key: str = ""


BUMP_REACH = 46                  # Bumper cars closer than this (center to center) bump.
BUMPER_MARGIN = 26


class BumperCar:
    def __init__(self, name, x, y, heading, speed, rider=None):
        self.name, self.x, self.y, self.heading, self.speed = name, x, y, heading, speed
        self.rider = rider               # people-atlas kind sitting in it.

    def _move(self, dt) -> bool:
        """Move; returns True when it hit the arena's barrier (and was kept inside)."""
        x0, y0, x1, y1 = (v * TILE_SIZE for v in ARENA)
        rad = math.radians(self.heading)
        self.x += math.sin(rad) * self.speed * dt
        self.y -= math.cos(rad) * self.speed * dt
        m = BUMPER_MARGIN
        if not x0 + m <= self.x <= x1 - m or not y0 + m <= self.y <= y1 - m:
            self.x = min(x1 - m, max(x0 + m, self.x))
            self.y = min(y1 - m, max(y0 + m, self.y))
            return True
        return False

    def update(self, dt, rng):
        """Riders who wander: turn now and then, and bounce off the barrier."""
        if self._move(dt):
            self.heading = (self.heading + 180 + rng.uniform(-60, 60)) % 360   # Bump!
        elif rng.random() < 0.6 * dt:
            self.heading = (self.heading + rng.uniform(-50, 50)) % 360

    def drive(self, dt, throttle, steer):
        """The player's car: UP/DOWN go and reverse, LEFT/RIGHT steer."""
        if steer and abs(self.speed) > 5:
            self.heading = (self.heading + steer * 150 * dt * (1 if self.speed > 0 else -1)) % 360
        if throttle:
            self.speed = max(-70.0, min(120.0, self.speed + throttle * 170 * dt))
        else:
            self.speed *= max(0.0, 1 - 1.6 * dt)
        if self._move(dt):
            self.speed = -0.5 * self.speed                  # Bounce off the barrier.

    def sprites(self):
        out = [Sprite("fair-atlas", self.name, self.x, self.y, 56, 56, -self.heading)]
        if self.rider:
            out.append(Sprite("people-atlas", f"{self.rider}_sit", self.x, self.y + 4, 32, 32, -self.heading))
        return out

    def sprite(self):
        return self.sprites()[0]


def ferris_seat(i: int, clock: float):
    """Where ferris car i hangs at this time (the wheel seen from the front)."""
    a = clock * FERRIS_SPIN + i * 2 * math.pi / FERRIS_CARS
    fx, fy = at(*FERRIS)
    return fx + FERRIS_R * math.sin(a), fy + FERRIS_R * math.cos(a) - 12


def horse_seat(i: int, clock: float):
    """(x, y, heading) of carousel horse i: going round, facing the way it moves."""
    a = clock * CAROUSEL_SPIN + i * 2 * math.pi / HORSES
    cx, cy = at(*CAROUSEL)
    return cx + CAROUSEL_R * math.cos(a), cy + CAROUSEL_R * math.sin(a), (math.degrees(a) + 180) % 360


class Ride:
    """The player on a ride: which seat (a ferris car or a horse) or their bumper car."""

    def __init__(self, kind: str, seat: int = 0, player_bumper: BumperCar | None = None):
        self.kind, self.seat, self.clock, self.bumper = kind, seat, 0.0, player_bumper

    @property
    def done(self):
        return self.clock >= RIDE_TIME[self.kind]


class FairInterior:
    """Stands in for the world while the player is at the fair."""

    def __init__(self, seed: int = 0):
        self.width, self.height = COLS * TILE_SIZE, ROWS * TILE_SIZE
        self.rng = random.Random(seed + 2718)
        self.entry = at(*ENTRY)
        self.tiles = {(tx, ty): self._tile(tx, ty) for ty in range(ROWS) for tx in range(COLS)}
        self.fixtures = self._fixtures()
        self.obstacles = [f for f in self.fixtures if f.solid_width]
        self.spots = self._spots()
        rng = self.rng
        self.bumpers = [BumperCar(name, *at(3 + i * 2, 12 + (i % 2) * 2), rng.uniform(0, 360), 70,
                                  rng.choice(RIDER_KINDS))
                        for i, name in enumerate(("bumper_red", "bumper_blue", "bumper_yellow", "bumper_green"))]
        # Most ferris cars and horses have someone on them.
        self.ferris_riders = {i: rng.choice(RIDER_KINDS) for i in range(FERRIS_CARS) if rng.random() < 0.7}
        self.horse_riders = {i: rng.choice(RIDER_KINDS) for i in range(HORSES) if rng.random() < 0.7}
        self.crowd = self._crowd()
        self.ride: Ride | None = None
        self.clock = 0.0

    def _tile(self, tx, ty):
        if tx in (0, COLS - 1) or ty in (0, ROWS - 1):
            return EDGE
        x0, y0, x1, y1 = ARENA
        if x0 <= tx < x1 and y0 <= ty < y1:
            return "dark_asphalt"
        return FLOOR

    def _fixtures(self):
        def piece(name, tx, ty, size, rotation=0.0, solid=(0, 0)):
            return Sprite("fair-atlas", name, *at(tx, ty), size, size, rotation, *solid)

        out = []
        for tx in range(COLS):                                   # The fence all round,
            for ty in (0.5, ROWS - 0.5):
                if ty > 1 and abs(tx + 0.5 - ENTRY[0]) < 1.1:
                    continue                                     # but for the gate.
                out.append(piece("fair_fence", tx + 0.5, ty, 64, 0.0, (64, 10)))
        for ty in range(1, ROWS - 1):
            for tx in (0.5, COLS - 0.5):
                out.append(piece("fair_fence", tx, ty + 0.5, 64, 90.0, (64, 10)))
        out.append(piece("fair_gate", ENTRY[0], ROWS - 0.6, 160))
        for game, tx, ty in BOOTHS:
            out.append(piece(f"booth_{game}", tx, ty, BOOTH_SIZE, 0.0, BOOTH_SOLID))
        out.append(piece("ferris_wheel", *FERRIS, 320, 0.0, (180, 70)))
        out.append(piece("carousel", *CAROUSEL, 256, 0.0, (200, 200)))
        x0, y0, x1, y1 = ARENA                                   # The bumper car barrier.
        for tx in range(int(x0), int(x1)):
            for ty in (y0, y1):
                out.append(piece("barrier_rail", tx + 0.5, ty, 64, 0.0, (64, 10)))
        for ty in range(int(y0), int(y1)):
            for tx in (x0, x1):
                out.append(piece("barrier_rail", tx, ty + 0.5, 64, 90.0, (64, 10)))
        for name, tx, ty in (("popcorn_stand", 14.4, 2.4), ("candy_stand", 14.4, 8.2),
                             ("food_stand", 23.8, 16.0)):
            out.append(piece(name, tx, ty, 128, 0.0, (96, 56)))
        out.append(piece("ticket_booth", *COUNTER, 128, 180.0, (120, 52)))   # Its window faces the fair.
        for tx, ty in ((13.2, 5.3), (16.6, 8.4), (18.4, 16.3), (8.2, 16.4), (24.7, 8.4)):
            out.append(piece("fair_lamp", tx, ty, 64, 0.0, (14, 14)))
        return out

    def _spots(self):
        spots = [Spot("exit", "Leave the fair", *at(ENTRY[0], ENTRY[1] + 0.35), 50)]
        for game, tx, ty in BOOTHS:
            spots.append(Spot("booth", GAMES[game].name.title(), *at(tx, ty + 1.45), 46, game))
        spots.append(Spot("counter", "Game tickets", *at(COUNTER[0], COUNTER[1] - 1.05), 46))
        spots.append(Spot("ride", "Ferris wheel", *at(FERRIS[0], FERRIS[1] + 1.7), 56, "ferris_wheel"))
        spots.append(Spot("ride", "Carousel", *at(CAROUSEL[0] - 2.65, CAROUSEL[1]), 56, "carousel"))
        spots.append(Spot("ride", "Bumper cars", *at(ARENA[2] + 0.6, ARENA[3] - 0.7), 50, "bumper_cars"))
        spots.append(Spot("look", "Popcorn", *at(14.4, 3.5), 46, "Warm, buttery, and 0 S today. Delicious."))
        spots.append(Spot("look", "Cotton candy", *at(14.4, 9.3), 46, "Pink sugar clouds. Your teeth ache already."))
        return spots

    def _crowd(self):
        rng = self.rng
        kinds = ("city_a", "city_b", "city_c", "city_d", "city_e", "city_g", "city_h", "snow_a", "snow_b")
        # The midway loop between the booths and the rides, and a lap of the whole fair.
        loops = [
            [at(2, 5.3), at(12.4, 5.3), at(12.4, 10.6), at(2, 10.6)],
            [at(12.8, 4.2), at(17.4, 4.2), at(17.4, 14.6), at(12.8, 14.6)],
            [at(1.6, 5.8), at(24, 5.8), at(24, 9.4), at(1.6, 9.4)],
            [at(15.6, 1.6), at(24.2, 1.6), at(24.2, 14.8), at(15.6, 14.8)],
        ]
        crowd = []
        for i in range(16):
            path = loops[i % len(loops)]
            stops = {j: ("pause", (1.0, 4.0)) for j in range(len(path)) if rng.random() < 0.5}
            crowd.append(Pedestrian(rng.choice(kinds), path, rng.uniform(24, 36),
                                    rng.uniform(0, 3000), stops))
        for game, tx, ty in BOOTHS:                              # Someone queueing at most booths.
            if rng.random() < 0.7:
                crowd.append(Pedestrian(rng.choice(kinds), [at(tx + 0.8, ty + 1.7)], 0, heading=0.0))
        crowd.append(Pedestrian(rng.choice(kinds), [at(FERRIS[0] + 0.9, FERRIS[1] + 1.8)], 0, heading=0.0))
        return crowd

    # World stand-in -----------------------------------------------------------------

    def region_at(self, x, y):
        return "fair"

    def can_place_walker(self, rect):
        half_w, half_h = rect[7] / 2, rect[8] / 2
        return all(self.tiles.get((int((rect[0] + dx) // TILE_SIZE), int((rect[1] + dy) // TILE_SIZE))) == FLOOR
                   for dx in (-half_w, half_w) for dy in (-half_h, half_h))

    def can_place_car(self, rect):
        return False

    def nearby_obstacles(self, x, y):
        return [o for o in self.obstacles if abs(o.x - x) < 200 and abs(o.y - y) < 200]

    # Each frame -----------------------------------------------------------------------

    def update(self, dt):
        self.clock += dt
        for person in self.crowd:
            person.update(dt, self.rng, False)
        for car in self.bumpers:
            car.update(dt, self.rng)
        cars = self.bumpers + ([self.ride.bumper] if self.ride and self.ride.bumper else [])
        for i, a in enumerate(cars):                     # Bumps push both cars apart.
            for b in cars[i + 1:]:
                d = math.dist((a.x, a.y), (b.x, b.y))
                if 0 < d < BUMP_REACH:
                    push = (BUMP_REACH - d) / 2
                    nx, ny = (b.x - a.x) / d, (b.y - a.y) / d
                    a.x, a.y, b.x, b.y = a.x - nx * push, a.y - ny * push, b.x + nx * push, b.y + ny * push
                    for car, sign in ((a, -1), (b, 1)):
                        if car.name == "bumper_player":
                            car.speed *= -0.5
                        else:
                            car.heading = math.degrees(math.atan2(sign * nx, -sign * ny)) % 360
        if self.ride:
            self.ride.clock += dt

    def start_ride(self, kind: str):
        """Get on: the ferris car at the bottom, the horse nearest the gate, or a bumper car."""
        if kind == "bumper_cars":
            x0, y0, x1, y1 = ARENA
            bumper = BumperCar("bumper_player", *at(x1 - 1.0, y1 - 1.0), 270.0, 0.0, "player")
            self.ride = Ride(kind, 0, bumper)
        elif kind == "ferris_wheel":
            bottom = max(range(FERRIS_CARS), key=lambda i: ferris_seat(i, self.clock)[1])
            self.ride = Ride(kind, bottom)
        else:
            spot = next(s for s in self.spots if s.key == "carousel")
            near = min(range(HORSES), key=lambda i: math.dist(horse_seat(i, self.clock)[:2], (spot.x, spot.y)))
            self.ride = Ride(kind, near)
        self.ride_start = self.clock

    def ride_pose(self):
        """(x, y, heading) of the player on their ride now."""
        ride = self.ride
        if ride.kind == "ferris_wheel":
            return (*ferris_seat(ride.seat, self.clock), 0.0)
        if ride.kind == "carousel":
            return horse_seat(ride.seat, self.clock)
        return ride.bumper.x, ride.bumper.y, ride.bumper.heading

    def update_ride(self, dt, throttle=0, steer=0):
        """Drive the bumper car (if that's the ride); returns True when the ride just ended."""
        ride = self.ride
        if ride and ride.bumper:
            ride.bumper.drive(dt, throttle, steer)
        if ride and ride.done:
            self.ride = None
            return True
        return False

    def visible_sprites(self):
        out = [Sprite("terrain-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                      TILE_SIZE, TILE_SIZE) for (tx, ty), name in self.tiles.items()]
        for car in self.bumpers:
            out += car.sprites()
        out += self.fixtures
        # The carousel's horses go round; the ferris wheel's cars hang from the rim. Riders
        # sit on most of them (the player's seat is left for the player).
        mine = (self.ride.kind, self.ride.seat) if self.ride else None
        for i in range(HORSES):
            x, y, heading = horse_seat(i, self.clock)
            out.append(Sprite("fair-atlas", "carousel_horse", x, y, 40, 40, -heading))
            if i in self.horse_riders and mine != ("carousel", i):
                out.append(Sprite("people-atlas", f"{self.horse_riders[i]}_sit", x, y, 28, 28, -heading))
        for i in range(FERRIS_CARS):
            x, y = ferris_seat(i, self.clock)
            out.append(Sprite("fair-atlas", "ferris_car", x, y, 36, 36))
            if i in self.ferris_riders and mine != ("ferris_wheel", i):
                out.append(Sprite("people-atlas", f"{self.ferris_riders[i]}_sit", x, y + 2, 24, 24))
        out += [p.sprite() for p in self.crowd]
        if self.ride and self.ride.bumper:
            out += self.ride.bumper.sprites()
        return out

    # Where the player is --------------------------------------------------------------

    def spot_near(self, x, y):
        near = [(math.dist((x, y), (s.x, s.y)), s) for s in self.spots
                if math.dist((x, y), (s.x, s.y)) <= s.reach]
        return min(near, key=lambda item: item[0])[1] if near else None

    def area_at(self, x, y) -> str:
        if x > 16.5 * TILE_SIZE:
            return "Rides"
        if y > 10.8 * TILE_SIZE and x < 11.5 * TILE_SIZE:
            return "Bumper cars"
        return "Midway"


# Outside: what the world draws on the fair's block --------------------------------------------

def exterior_ground(bx: int, by: int) -> str | None:
    """Ground tile at block-local tile (bx, by), or None for plain snow."""
    if by < 8:
        return "city_plaza"                                     # The fairground behind the fence.
    if by in LOT_ROWS and bx in LOT_COLUMNS:
        return "parking_lot"
    if by in AISLE_ROWS and 1 <= bx <= 14:
        return "dark_asphalt"
    if bx in WALKWAY and by <= AISLE_ROWS[1]:
        return "city_plaza"                                     # The walkway to the gate.
    return None


def exterior_sprites(site: FairSite, seed: int) -> list[Sprite]:
    """The fence, the mini fair behind it, the ticket booth, lamps, and parked cars."""
    rng = random.Random(seed + 8080)

    def piece(name, lx, ly, size, rotation=0.0, solid=(0, 0)):
        return Sprite("fair-atlas", name, *site.at(lx, ly), size, size, rotation, *solid)

    out = []
    for i in range(16):
        out.append(piece("fair_fence", i + 0.5, 0.3, 64, 0.0, (64, 10)))
        if i not in (7, 8):                                     # The booth fills the gate.
            out.append(piece("fair_fence", i + 0.5, 7.7, 64, 0.0, (64, 10)))
    for j in range(8):
        out.append(piece("fair_fence", 0.3, j + 0.5, 64, 90.0, (64, 10)))
        out.append(piece("fair_fence", 15.7, j + 0.5, 64, 90.0, (64, 10)))
    out += [piece("big_top", 3.6, 3.8, 240), piece("carousel", 8.0, 3.0, 192),
            piece("ferris_wheel", 12.3, 3.4, 288), piece("popcorn_stand", 2.4, 6.6, 112),
            piece("candy_stand", 13.6, 6.6, 112), piece("booth_darts", 5.6, 6.5, 112),
            piece("booth_balloons", 10.5, 6.5, 112)]
    out.append(piece("ticket_booth", 8.0, 7.55, 160, 0.0, (150, 64)))
    for lx, ly in ((0.6, 10.5), (15.4, 10.5), (6.6, 8.6), (9.4, 8.6), (0.6, 12.5), (15.4, 12.5)):
        out.append(piece("fair_lamp", lx, ly, 64, 0.0, (14, 14)))
    reserved = set(site.stalls()[len(site.stalls()) - SHUTTLES * 2::2])
    for x, y in site.stalls():
        if (x, y) not in reserved and rng.random() < 0.55:
            out.append(Sprite("vehicle-atlas", rng.choice(VISITOR_CARS), x, y, PARKED_SIZE, PARKED_SIZE,
                              rng.choice((0.0, 180.0)), *PARKED_SOLID))
    return out
