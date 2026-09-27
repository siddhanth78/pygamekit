"""Beach fishing: cast from a pier's end, wait for a bite, win the strikes, reel it in.

Fish go into a bag (saved with missions) and are traded at the fish traders in the jungle
encampments for universal mastery points, which the player spends on any region. Fishing,
trading, and fast travel to the beach open once any region reaches FISHING_LEVEL.

The fight is a row of timing strikes: a marker sweeps across a bar and the player presses
Space (the confirm intent) while it is inside the green zone. Rarer fish need more strikes,
with a narrower zone and a faster marker. Missing one, or waiting too long, loses the fish.
"""

from __future__ import annotations

import math
import random

from collision_manager import nearest_clear_spot
from inventory import STACK_MAX
from world import TILE_SIZE, Sprite


RARITIES = ("common", "uncommon", "rare", "epic")
VALUE = {"common": 1, "uncommon": 2, "rare": 3, "epic": 5}        # Points at the trader.
ODDS = {"common": 0.62, "uncommon": 0.27, "rare": 0.10, "epic": 0.01}   # Default mix.
# Piers in world.DOCK_SITES order. Each has its own catch and unlock:
# (number, odds, level, regions needed at that level; None means every region).
PIERS = {
    "west": (1, {"common": 0.75, "uncommon": 0.20, "rare": 0.045, "epic": 0.005}, 4, 1),
    "north": (2, {"common": 0.40, "uncommon": 0.45, "rare": 0.14, "epic": 0.01}, 7, 3),
    "east": (3, {"common": 0.25, "uncommon": 0.35, "rare": 0.37, "epic": 0.03}, 12, None),
}
PIER_CATCH = {"west": "Mostly common fish", "north": "More uncommon fish",
              "east": "More rare fish, more epics"}
STRIKES = {"common": 2, "uncommon": 3, "rare": 4, "epic": 5}
ZONE = {"common": 0.30, "uncommon": 0.22, "rare": 0.16, "epic": 0.11}  # Fraction of the bar.
SWEEP = {"common": 0.8, "uncommon": 1.0, "rare": 1.25, "epic": 1.5}   # Bar crossings per second.
NAMES = {
    "common": ("Sardine", "Mackerel", "Herring"),
    "uncommon": ("Sea Bass", "Red Snapper"),
    "rare": ("Bluefin Tuna", "Swordfish"),
    "epic": ("Golden Marlin",),
}
CAST_TIME = 0.5       # Bobber flight.
WAIT_TIME = 2.0       # Until the bite.
HOOKED_TIME = 0.5     # "Fish on!" before the first strike.
STRIKE_TIME = 4.0     # Seconds allowed per strike before the fish gets away.
BETWEEN_TIME = 0.35   # Pause after a good strike.
REEL_TIME = 1.0       # Automatic reel-in after the last strike.
SHOW_TIME = 2.0       # The catch (or escape) stays up this long.
CAST_DISTANCE = 150   # px beyond the pier's end.
ROD_REACH = 14        # px from the player's center to the rod tip.
TRADER_OFFSET = (-64, 100)  # From a jungle camp's center: beside the south tent.
TRADER_RANGE = 48
BADGE_LIFT = 34       # px the fishing badge floats above a trader, like mission badges.


class FishLog:
    """Fish in the bag (to trade) and every fish ever caught, by rarity."""

    def __init__(self, data: dict | None = None):
        self.bag = {r: 0 for r in RARITIES}
        self.caught = {r: 0 for r in RARITIES}
        if isinstance(data, dict):
            for key in ("bag", "caught"):
                counts = data.get(key)
                if isinstance(counts, dict):
                    for rarity in RARITIES:
                        value = counts.get(rarity)
                        if type(value) is int and value >= 0:
                            getattr(self, key)[rarity] = min(value, STACK_MAX) if key == "bag" else value

    def to_dict(self) -> dict:
        return {"bag": dict(self.bag), "caught": dict(self.caught)}

    def add(self, rarity: str) -> bool:
        """Put a fish in the bag; False (released) if that stack is already full."""
        if self.bag[rarity] >= STACK_MAX:
            return False
        self.bag[rarity] += 1
        self.caught[rarity] += 1
        return True

    @property
    def count(self) -> int:
        return sum(self.bag.values())

    @property
    def value(self) -> int:
        return sum(VALUE[r] * n for r, n in self.bag.items())

    def summary(self) -> str:
        return ",  ".join(f"{n} {r}" for r, n in self.bag.items() if n) or "empty"

    def take_bag(self, points_room: int | None = None) -> tuple[int, int]:
        """Hand over fish for a trade: (fish, mastery). With points_room, only as many as
        pay at most that many points (rarest first); the rest stay in the bag."""
        count = value = 0
        for rarity in reversed(RARITIES):
            n = self.bag[rarity]
            if points_room is not None:
                n = min(n, (points_room - value) // VALUE[rarity])
            self.bag[rarity] -= n
            count, value = count + n, value + n * VALUE[rarity]
        return count, value


def pier_title(name: str) -> str:
    return f"Pier {PIERS[name][0]}  ·  {name.title()}"


def pier_open(progress, name: str) -> bool:
    """Pier 1: any region at 4; pier 2: three regions at 7; pier 3: every region at 12."""
    _, _, level, count = PIERS[name]
    reached = sum(lvl >= level for lvl in progress.levels.values())
    return reached >= (len(progress.levels) if count is None else count)


def pier_requirement(name: str) -> str:
    _, _, level, count = PIERS[name]
    who = "every region" if count is None else "any region" if count == 1 else f"{count} regions"
    return f"Needs {who} at level {level}"


def roll_rarity(rng: random.Random, odds: dict | None = None) -> str:
    odds = odds or ODDS
    roll, total = rng.random(), 0.0
    for rarity in RARITIES:
        total += odds[rarity]
        if roll < total:
            return rarity
    return "common"


class FishingSession:
    """One visit to a pier's end: cast, bite, strikes, reel, and cast again.

    phase: cast, wait, hooked, strike, between, reel, caught, escaped, or ready (waiting
    for the player to cast again)."""

    def __init__(self, dock, x: float, y: float, rng: random.Random | None = None):
        self.dock = dock
        self.rng = rng or random.Random()
        self.x, self.y = x, y
        self.heading = dock.heading
        self.phase, self.timer = "ready", 0.0
        self.rarity, self.name = "common", ""   # Rolled at each bite.
        self.strike = 0               # Strikes passed.
        self.bag_full = False         # The last catch was released: its stack was full.
        self.sweep_t = 0.0            # Drives the marker.
        self.zone = (0.0, 0.0)
        self.bobber = self.rod_tip()
        self.target = self.bobber
        self.cast()

    # Geometry -------------------------------------------------------------------

    def rod_tip(self) -> tuple[float, float]:
        h = math.radians(self.heading)
        fx, fy = math.sin(h), -math.cos(h)
        rx, ry = math.cos(h), math.sin(h)
        return self.x + fx * ROD_REACH + rx * 6, self.y + fy * ROD_REACH + ry * 6

    # Flow ---------------------------------------------------------------------------

    def cast(self) -> bool:
        """E: throw the hook out, if not already fishing."""
        if self.phase not in ("ready", "caught", "escaped"):
            return False
        dx, dy = self.dock.direction
        ex, ey = self.dock.end
        side = self.rng.uniform(-40, 40)
        self.target = ((ex + 0.5) * TILE_SIZE + dx * CAST_DISTANCE + (side if dy else 0),
                       (ey + 0.5) * TILE_SIZE + dy * CAST_DISTANCE + (side if dx else 0))
        self.bobber = self.rod_tip()
        self.phase, self.timer = "cast", 0.0
        self.strike = 0
        return True

    def marker(self) -> float:
        """Marker position 0-1, sweeping back and forth."""
        t = (self.sweep_t * SWEEP[self.rarity]) % 2.0
        return t if t <= 1.0 else 2.0 - t

    def _new_zone(self):
        width = ZONE[self.rarity]
        center = self.rng.uniform(width / 2 + 0.04, 1 - width / 2 - 0.04)
        self.zone = (center - width / 2, center + width / 2)

    def press(self) -> str | None:
        """Space during a strike: returns 'hit' or 'miss' (None if no strike is on)."""
        if self.phase != "strike":
            return None
        low, high = self.zone
        if low <= self.marker() <= high:
            self.strike += 1
            if self.strike >= STRIKES[self.rarity]:
                self.phase, self.timer = "reel", 0.0
            else:
                self.phase, self.timer = "between", 0.0
            return "hit"
        self.phase, self.timer = "escaped", 0.0
        return "miss"

    def update(self, dt: float, log: FishLog):
        """Advance; returns the rarity when a fish lands in the bag, else None."""
        self.timer += dt
        if self.phase == "cast":
            t = min(1.0, self.timer / CAST_TIME)
            (ax, ay), (bx, by) = self.rod_tip(), self.target
            self.bobber = (ax + (bx - ax) * t, ay + (by - ay) * t)
            if t >= 1.0:
                self.phase, self.timer = "wait", 0.0
        elif self.phase == "wait" and self.timer >= WAIT_TIME:
            self.rarity = roll_rarity(self.rng, PIERS[self.dock.name][1])
            self.name = self.rng.choice(NAMES[self.rarity])
            self.phase, self.timer = "hooked", 0.0
        elif self.phase == "hooked" and self.timer >= HOOKED_TIME:
            self._start_strike()
        elif self.phase == "between" and self.timer >= BETWEEN_TIME:
            self._start_strike()
        elif self.phase == "strike":
            self.sweep_t += dt
            if self.timer >= STRIKE_TIME:
                self.phase, self.timer = "escaped", 0.0
        elif self.phase == "reel":
            t = min(1.0, self.timer / REEL_TIME)
            (ax, ay), (bx, by) = self.target, self.rod_tip()
            self.bobber = (ax + (bx - ax) * t, ay + (by - ay) * t)
            if t >= 1.0:
                self.bag_full = not log.add(self.rarity)
                self.phase, self.timer = "caught", 0.0
                return None if self.bag_full else self.rarity
        elif self.phase in ("caught", "escaped") and self.timer >= SHOW_TIME:
            self.phase, self.timer = "ready", 0.0
        return None

    def _start_strike(self):
        self.phase, self.timer = "strike", 0.0
        self.sweep_t = self.rng.uniform(0.0, 2.0)  # The marker starts somewhere new.
        self._new_zone()

    # Display ------------------------------------------------------------------------

    @property
    def line_out(self) -> bool:
        return self.phase in ("cast", "wait", "hooked", "strike", "between", "reel")

    def pose(self) -> str:
        if self.phase == "cast" and self.timer < CAST_TIME * 0.5:
            return "player_fish_cast"
        if self.phase in ("hooked", "strike", "between", "reel"):
            return "player_fish_reel"
        return "player_fish_hold"

    def status(self) -> tuple[str, str]:
        """(headline, detail) for the fishing panel."""
        if self.phase in ("cast", "wait"):
            return "Waiting for a bite...", "Move to stop fishing"
        if self.phase == "hooked":
            return "FISH ON!", "Get ready to strike"
        if self.phase in ("strike", "between"):
            return (f"Strike {self.strike + 1} of {STRIKES[self.rarity]}",
                    "SPACE when the marker is in the green")
        if self.phase == "reel":
            return "Reeling in...", ""
        if self.phase == "caught" and self.bag_full:
            return (f"Bag full: released the {self.name}",
                    f"{STACK_MAX} is the most you can carry  ·  trade at a jungle camp")
        if self.phase == "caught":
            return (f"Caught: {self.rarity.title()} {self.name}!",
                    f"Worth {VALUE[self.rarity]} mastery at a jungle fish trader")
        if self.phase == "escaped":
            return "It got away...", "E to cast again"
        return "", ""

    def sprites(self) -> list[Sprite]:
        """The line, the bobber (or the fish on the line), and the catch held up."""
        out = []
        if self.line_out:
            (ax, ay), (bx, by) = self.rod_tip(), self.bobber
            length = math.dist((ax, ay), (bx, by))
            if length > 1:
                # A solid cell stretched thin; GL rotation is counterclockwise.
                angle = math.degrees(math.atan2(bx - ax, -(by - ay)))
                out.append(Sprite("people-atlas", "fishing_line", (ax + bx) / 2, (ay + by) / 2,
                                  1, length, -angle))
        if self.phase in ("cast", "wait"):
            out.append(Sprite("people-atlas", "fishing_bobber", *self.bobber, 20, 20))
        elif self.phase in ("hooked", "strike", "between"):
            out.append(Sprite("people-atlas", "fishing_bobber_bite", *self.bobber, 28, 28))
        elif self.phase == "reel":
            # The art hangs nose north; reeling, the nose points back at the player.
            out.append(Sprite("people-atlas", f"fish_{self.rarity}", *self.bobber, 28, 28,
                              -(self.heading + 180)))
        elif self.phase == "caught":
            h = math.radians(self.heading)
            out.append(Sprite("people-atlas", f"fish_{self.rarity}",
                              self.x + math.sin(h) * 20, self.y - math.cos(h) * 20, 28, 28))
        return out


# Places ---------------------------------------------------------------------------------

def trader_spots(world) -> list[tuple[float, float, float]]:
    """(x, y, heading) of the fish trader at every jungle encampment, facing the fire."""
    spots = []
    for (sx, sy), region in sorted(world.camps.items()):
        if region != "jungle":
            continue
        cx, cy = world.camp_center(sx, sy)
        dx, dy = TRADER_OFFSET
        spots.append((cx + dx, cy + dy, math.degrees(math.atan2(-dx, dy)) % 360))
    return spots


def trader_near(world, x: float, y: float):
    return next(((tx, ty) for tx, ty, _ in trader_spots(world)
                 if math.dist((x, y), (tx, ty)) <= TRADER_RANGE), None)


def trader_sprites(world, clock: float = 0.0) -> list[Sprite]:
    """Each trader, with a bobbing fishing badge like the mission givers'."""
    bob = math.sin(clock * 3) * 3
    out = []
    for x, y, heading in trader_spots(world):
        out.append(Sprite("people-atlas", "fish_trader_idle", x, y, 32, 32, -heading))
        out.append(Sprite("marker-atlas", "icon_fishing", x, y - BADGE_LIFT + bob, 32, 32))
    return out


def fishing_spot(world, x: float, y: float):
    """The pier whose end the player stands on, or None."""
    return next((dock for dock in world.docks if dock.at_end(x, y)), None)


def beach_destination(world, collisions, car, from_x: float, from_y: float, dock=None):
    """Fast travel to the beach: the car parks on the sand by the chosen pier (by default
    the nearest). Only people can walk out on the planks."""
    if isinstance(dock, str):
        dock = next(d for d in world.docks if d.name == dock)
    dock = dock or min(world.docks, key=lambda d: math.dist(d.shore(), (from_x, from_y)))
    x, y = dock.shore()
    heading = (dock.heading + 180) % 360   # Parked facing inland.

    class OnBeach:
        def can_move(self, rect):
            return world.region_at(rect[0], rect[1]) == "beach" and collisions.can_move(rect)

    saved = car.heading
    car.heading = heading
    try:
        spot = nearest_clear_spot(OnBeach(), car.collision_record, x, y, 24, 400)
    finally:
        car.heading = saved
    return (*spot, heading) if spot else None
