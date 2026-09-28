"""Mission givers, offers, and in-world missions (delivery and time trial).

Drag races are accepted here but run in drag_race.DragRace, a separate level; their
result comes back through finish_drag(). At most one mission is ongoing. Ongoing missions
are never saved: quitting the game quits the mission (the giver keeps its offer, and the
save puts the player back at the giver), exactly like aborting it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from car import OFF_SURFACE, SURFACES, TOP_SPEED
from collision_manager import CollisionManager, nearest_clear_spot
from fishing import FishLog
from factory import FactoryState
from fair import FairState
from farm import Farm
from farm_orders import FarmOrders
from inventory import BY_ID, STACK_MAX, room
from progression import (FAST_TRAVEL_LEVEL, FISHING_LEVEL, RATING_EDGE, REGIONS, SPEED_PER_LEVEL, Progress,
                         mastery_to_next, rating,
                         rating_difficulty, reward)
from walker import Walker
from world import CENTERS, SECTOR_SIZE, SECTORS, TILE_SIZE, Sprite


TYPES = ("delivery", "speed", "drag")
# The "speed" key is the time trial (it was once called the speed check); saves use the key.
TITLES = {"delivery": "Delivery", "speed": "Time Trial", "drag": "Drag Race"}
BASE_REWARD = {"delivery": 2, "speed": 1, "drag": 3}
MAX_SCALE = 1.25
DRAG_RATING_GAP = (-10, 15)   # Drag rivals: rated this far from the player's rating when offered.
STRAIGHT_RATING_GAP = (-10, 10)  # Straights have no corners: above +10 not even an off-day wins.
VETERAN_RATING_GAP = (1, 10)  # Veterans' circuits: always Medium (see RATING_EDGE).
EASED_RATING_GAP = (-10, 0)   # After a decline: never above the player.
EASED_SCALE = 0.79            # After a decline, time trials and deliveries roll 0.5 up to this (Easy).  # Delivery at 75+ points; 1 at 50+.
DELIVERY_PENALTY = {"Easy": 5, "Medium": 10, "Hard": 15}
DELIVERY_TIERS = ((75, 2), (50, 1))
CRASH_GRACE = 1.0            # Seconds after a crash before another one counts.
TALK_RANGE = 40              # On foot, px from a giver to talk.
DROP_RANGE = 64              # px from the recipient (on foot or in the car) to deliver.
FINISH_RANGE = 90            # px from the speed-check finish, in the car.
DETOUR = 1.3                 # Straight-line distance x this approximates the drive.
SPEED_EFFICIENCY = 0.8       # Timer assumes 80% of top speed the whole way.
DELIVERY_DISTANCE = (3000, 15000)  # px (10 px = 1 m): 0.3-1.5 km, often another region.
SPEED_DISTANCE = (2000, 6000)
GIVER_RADIUS = 200           # Givers stand about this far from their racing center.
BASE_ANGLES = (200, 240, 280)   # Degrees (screen, y down) for delivery, speed, drag givers.
VETERAN_MIN_SECTORS = 4      # Veterans stand at least this many sectors from their center,
VETERAN_EDGE_MARGIN = 2      # this many sectors inside their region's edges,
VETERAN_APART = 5            # and this many sectors from each other.
GIVER_KINDS = {"city": "city_d", "rural": "farmer_b", "snow": "snow_a",
               "desert": "nomad_b", "jungle": "explorer_a"}


def difficulty(scale: float) -> str:
    return "Easy" if scale < 0.8 else "Medium" if scale < 1.05 else "Hard"


@dataclass
class Giver:
    id: str
    region: str
    type: str
    harder: bool
    x: float
    y: float

    @property
    def name(self):
        return f"{'Veteran ' if self.harder else ''}{self.region.title()} {TITLES[self.type]}"


@dataclass
class Offer:
    giver_id: str
    type: str
    scale: float
    target: tuple[float, float] | None = None   # Delivery recipient or speed-check finish.
    track: dict | None = None                   # Drag race level: kind, shape, theme.
    seed: int = 0
    # The player's region levels when the offer was made. Drag rivals and time-trial
    # clocks are fixed to the player's car at that moment, so leveling up afterwards
    # makes the same offer easier.
    levels: dict | None = None
    rating: int | None = None                   # Drag: the rival's rating.
    declines: int = 0                           # Times declined: Easy, reward halved each time.

    @property
    def eased(self) -> bool:
        return self.declines > 0

    def to_dict(self):
        return {"type": self.type, "scale": self.scale,
                "target": list(self.target) if self.target else None,
                "track": self.track, "seed": self.seed, "levels": self.levels, "rating": self.rating,
                "declines": self.declines}

    @classmethod
    def from_dict(cls, giver_id, data):
        """Validated rebuild; raises ValueError for anything malformed."""
        if not isinstance(data, dict) or data.get("type") not in TYPES:
            raise ValueError("bad offer")
        scale = data.get("scale")
        if type(scale) not in (int, float) or not 0.5 <= scale <= 1.25:
            raise ValueError("bad scale")
        target = data.get("target")
        if target is not None:
            if (not isinstance(target, list) or len(target) != 2
                    or not all(type(v) in (int, float) and math.isfinite(v) for v in target)):
                raise ValueError("bad target")
            target = tuple(target)
        track = data.get("track")
        if track is not None and (not isinstance(track, dict)
                                  or track.get("kind") not in ("straight", "circuit")):
            raise ValueError("bad track")
        if (data["type"] == "drag") != (track is not None) or (data["type"] != "drag") != (target is not None):
            raise ValueError("offer shape does not match its type")
        levels = data.get("levels")
        if levels is not None and (not isinstance(levels, dict) or not all(
                region in REGIONS and type(level) is int and level >= 1
                for region, level in levels.items())):
            raise ValueError("bad levels")
        rival = data.get("rating")
        if rival is not None and not (type(rival) is int and 1 <= rival <= 1000):
            raise ValueError("bad rating")
        declines = data.get("declines", 1 if data.get("eased") is True else 0)  # "eased": older saves.
        if not (type(declines) is int and 0 <= declines <= 10):
            raise ValueError("bad declines")
        return cls(giver_id, data["type"], float(scale), target, track, int(data.get("seed", 0)),
                   levels, rival, declines)


class ActiveMission:
    def __init__(self, offer: Offer, giver: Giver, time_limit: float = 0.0, label: str = ""):
        self.offer, self.giver = offer, giver
        self.label = label or difficulty(offer.scale)
        self.points = 100
        self.crashes = 0
        self.grace = 0.0
        self.flash = 0.0           # Seconds left on the "crash" flash.
        self.time_limit = time_limit
        self.time_left = time_limit
        self.started = False       # Time trial: the clock starts once you drive off.


class Missions:
    def __init__(self, world, seed: int, data: dict | None = None):
        self.world = world
        self.seed = seed
        data = data if isinstance(data, dict) else {}
        self.progress = Progress(data.get("progress"))
        self.fish = FishLog(data.get("fish"))
        # Universal mastery from traded fish, spent on any region whenever the player likes.
        unspent = data.get("unspent_mastery")
        self.unspent = min(unspent, STACK_MAX) if type(unspent) is int and unspent >= 0 else 0
        # Inventory items beyond fish and points (Sunside Tokens, ...): item id -> count.
        items = data.get("inventory")
        self.items = {k: min(v, BY_ID[k].max_stack) for k, v in items.items()
                      if k in BY_ID and type(v) is int and v > 0} if isinstance(items, dict) else {}
        # Best arcade scores by game id (the cabinet at home).
        arcade = data.get("arcade")
        self.arcade = {k: v for k, v in arcade.items() if isinstance(k, str) and type(v) is int and v >= 0} \
            if isinstance(arcade, dict) else {}
        self.counter = data.get("counter") if type(data.get("counter")) is int else 0
        # Arcade games bought at the store (one-time purchases that never enter the inventory).
        unlocked = data.get("arcade_unlocked")
        self.arcade_unlocked = {g for g in unlocked if isinstance(g, str)} if isinstance(unlocked, list) else set()
        self.farm = Farm(data.get("farm"))    # The farmhouse (owned?) and its plot.
        self.fair = FairState(data.get("fair"))  # Snow Fair booths and the F1 prize.
        self.factory = FactoryState(data.get("factory"))   # Unlocked? and the depot's biofuel.
        self.givers = self._place_givers()
        self.by_id = {g.id: g for g in self.givers}
        # Farm buyers (shown once the farm is owned): one open order per mainland region.
        self.orders = FarmOrders(world.farm.door, seed, data.get("farm_orders"), self._buyer_spot)
        self.offers: dict[str, Offer] = {}
        for giver_id, offer in (data.get("offers") or {}).items():
            if giver_id in self.by_id:
                try:
                    parsed = Offer.from_dict(giver_id, offer)
                    self._snapshot(parsed)
                    if parsed.type == self.by_id[giver_id].type:
                        self.offers[giver_id] = parsed
                except (ValueError, TypeError):
                    pass
        # Saves from before this revamp may hold a "queued" mission; it is ignored.
        self.active: ActiveMission | None = None

    # Placement ------------------------------------------------------------------

    def _place_givers(self):
        """Three givers on open ground near each center, and three veterans spread out
        across the region, far from the center and from each other."""
        probe_collisions = CollisionManager(None, self.world)
        givers = []
        for (sx, sy), center in CENTERS.items():
            region = center.removeprefix("center_")
            if region not in REGIONS:
                continue  # The island has no givers.
            cx, cy = self.world.center_position(sx, sy)
            for mission_type, angle in zip(TYPES, BASE_ANGLES):
                ax = cx + math.cos(math.radians(angle)) * GIVER_RADIUS
                ay = cy + math.sin(math.radians(angle)) * GIVER_RADIUS
                spot = self._open_spot(probe_collisions, ax, ay, region, givers)
                if spot:
                    givers.append(Giver(f"{region}-{mission_type}", region, mission_type, False, *spot))
            for mission_type, (vx, vy) in zip(TYPES, self._veteran_sectors(region, (sx, sy))):
                spot = self._open_spot(probe_collisions, (vx + 0.5) * SECTOR_SIZE,
                                       (vy + 0.5) * SECTOR_SIZE, region, givers)
                if spot:
                    givers.append(Giver(f"{region}-{mission_type}-hard", region, mission_type, True, *spot))
        return givers

    def _veteran_sectors(self, region, center):
        """Three random sectors inside the region (not on its edges), away from the center
        and from each other. Seeded, so veterans always stand in the same places."""
        rng = random.Random(f"{self.seed}-veterans-{region}")
        world = self.world

        def inside(sx, sy, margin):
            return all(world.region(sx + dx, sy + dy) == region
                       for dx in range(-margin, margin + 1) for dy in range(-margin, margin + 1)
                       if 0 <= sx + dx < SECTORS and 0 <= sy + dy < SECTORS)

        region_sectors = [(sx, sy) for sy in range(SECTORS) for sx in range(SECTORS)
                          if world.region(sx, sy) == region and (sx, sy) not in CENTERS
                          and (sx, sy) not in world.camps
                          and math.dist((sx, sy), center) >= VETERAN_MIN_SECTORS]
        # Prefer the region's interior; relax the edge margin and spacing if it is small.
        for margin, apart in ((VETERAN_EDGE_MARGIN, VETERAN_APART), (1, VETERAN_APART), (1, 3), (0, 2)):
            candidates = [s for s in region_sectors if inside(*s, margin)]
            rng.shuffle(candidates)
            chosen = []
            for sector in candidates:
                if all(math.dist(sector, other) >= apart for other in chosen):
                    chosen.append(sector)
                    if len(chosen) == len(TYPES):
                        return chosen
        return chosen

    def _buyer_spot(self, region, rng):
        """A farm buyer's spot: somewhere open in the region, away from its center, the
        farm, camps, the highway, and the other people."""
        world = self.world
        if not hasattr(self, "_buyer_sectors"):
            self._buyer_sectors = {}
            self._buyer_probe = CollisionManager(None, world)
        if region not in self._buyer_sectors:
            near_highway = world.roads.sectors()
            self._buyer_sectors[region] = [
                (sx, sy) for sy in range(SECTORS) for sx in range(SECTORS)
                if world.region(sx, sy) == region and (sx, sy) not in world.camps
                and (sx, sy) not in near_highway and (sx, sy) != world.farm.sector
                and all(math.dist((sx, sy), c) >= 2 for c in CENTERS)]
        sectors = self._buyer_sectors[region]
        orders = getattr(self, "orders", None)
        others = list(self.givers) + (list(orders.orders.values()) if orders else [])
        for _ in range(12):
            if not sectors:
                return None
            sx, sy = rng.choice(sectors)
            x = (sx + rng.uniform(0.2, 0.8)) * SECTOR_SIZE
            y = (sy + rng.uniform(0.2, 0.8)) * SECTOR_SIZE
            spot = self._open_spot(self._buyer_probe, x, y, region, others)
            if spot:
                return spot
        return None

    def _open_spot(self, collisions, x, y, region=None, others=(), clearance=24):
        """Nearest clear, off-road spot a person can stand on (and a car can reach)."""
        world = self.world

        class OffRoad:
            def can_move(self, rect):
                tx, ty = int(rect[0] // TILE_SIZE), int(rect[1] // TILE_SIZE)
                if (world._road_style(tx, ty) or world.region_at(rect[0], rect[1]) in ("sea", "island")
                        or world.roads.near(rect[0], rect[1], 64)):   # A tile clear of the highway.
                    return False
                if region and world.region_at(rect[0], rect[1]) != region:
                    return False
                if any(math.dist((rect[0], rect[1]), (o.x, o.y)) < 48 for o in others):
                    return False
                return collisions.can_move(rect)

        return nearest_clear_spot(OffRoad(), Walker(x, y).collision_record, x, y, clearance, 200)

    # Offers ---------------------------------------------------------------------

    def visible_givers(self):
        """Givers who will offer work now (veterans wait for level 5 in their region)."""
        return [g for g in self.givers if not g.harder or self.progress.harder_unlocked(g.region)]

    def is_locked(self, giver: Giver) -> bool:
        return giver.harder and not self.progress.harder_unlocked(giver.region)

    def offer_for(self, giver: Giver) -> Offer:
        old = self.offers.get(giver.id)
        if old and giver.harder and (old.declines or old.type == "drag" and old.track.get("kind") != "circuit"):
            del self.offers[giver.id]   # Saved before veterans became circuits-only, no declines.
        if giver.id not in self.offers:
            self.offers[giver.id] = self._new_offer(giver)
        return self._snapshot(self.offers[giver.id])

    def can_decline(self, offer: Offer) -> bool:
        """Declining halves the reward (rounded down, never below 1); an offer already
        paying 1 mastery can't be declined, and veterans' offers never can."""
        if self.by_id[offer.giver_id].harder:
            return False
        return self.reward_for(offer, BASE_REWARD[offer.type]) > 1

    def decline(self, giver: Giver) -> Offer:
        """The player turned the offer down: the giver swaps in an easy one paying half as
        much as this one (10 -> 5 -> 2 -> 1). Completing any offer brings full ones back."""
        offer = self.offer_for(giver)
        if self.can_decline(offer):
            self.offers[giver.id] = self._new_offer(giver, offer.declines + 1)
        return self.offers[giver.id]

    def _new_offer(self, giver: Giver, declines: int = 0) -> Offer:
        eased = declines > 0
        self.counter += 1
        rng = random.Random(f"{self.seed}-{giver.id}-{self.counter}")
        if eased:
            scale = round(rng.uniform(0.5, EASED_SCALE), 3)   # Always labeled Easy.
        else:
            scale = round(rng.uniform(0.8 if giver.harder else 0.5, MAX_SCALE), 3)
        seed = rng.randrange(1 << 30)
        if giver.type == "drag":
            # Veterans only race circuits, and only Medium ones.
            kind = "circuit" if giver.harder else rng.choice(("straight", "circuit"))
            track = {"kind": kind, "theme": giver.region}
            if kind == "circuit":
                track.update(seed=seed, size=rng.randint(4, 7))  # Its own generated layout.
            # The rival is rated around the player's rating now, and keeps it; veterans'
            # rivals are never below the player. Eased offers are never above them.
            low, high = (EASED_RATING_GAP if eased else VETERAN_RATING_GAP if giver.harder else
                         STRAIGHT_RATING_GAP if kind == "straight" else DRAG_RATING_GAP)
            rival = self.progress.rating(giver.region) + rng.randint(low, high)
            return Offer(giver.id, "drag", 1.0, None, track, seed, dict(self.progress.levels), rival,
                         declines)
        low, high = DELIVERY_DISTANCE if giver.type == "delivery" else SPEED_DISTANCE
        collisions = CollisionManager(None, self.world)
        for _ in range(40):
            angle, distance = rng.uniform(0, 2 * math.pi), rng.uniform(low, high)
            x = giver.x + math.cos(angle) * distance
            y = giver.y + math.sin(angle) * distance
            if not (0 < x < SECTORS * SECTOR_SIZE and 0 < y < SECTORS * SECTOR_SIZE):
                continue
            if self.world.region_at(x, y) in ("sea", "island"):
                continue
            spot = self._open_spot(collisions, x, y, clearance=32)
            if spot and low * 0.8 <= math.dist(spot, (giver.x, giver.y)):
                return Offer(giver.id, giver.type, scale, spot, None, seed, dict(self.progress.levels),
                             declines=declines)
        # Fall back to another racing center, which always has open ground around it.
        sector = rng.choice([s for s, n in CENTERS.items() if n != "center_island"])
        cx, cy = self.world.center_position(*sector)
        return Offer(giver.id, giver.type, scale, self._open_spot(collisions, cx, cy + 220), None, seed,
                     dict(self.progress.levels), declines=declines)

    def _snapshot(self, offer: Offer) -> Offer:
        """Fill in what offers saved (or built) before levels and drag ratings lack."""
        if offer.levels is None:
            offer.levels = dict(self.progress.levels)
        if offer.type == "drag" and offer.rating is None:
            # The old scale x the rating then, kept within DRAG_RATING_GAP.
            base = rating(offer.levels.get(self.by_id[offer.giver_id].region, 1))
            low, high = DRAG_RATING_GAP
            offer.rating = max(base + low, min(base + high, int(round(offer.scale * base, 6) + 0.5)))
        return offer

    def offer_multiplier(self, offer: Offer, region: str | None = None) -> float:
        """The player's top-speed multiplier in region (default: the giver's) when offered."""
        region = region or self.by_id[offer.giver_id].region
        level = (offer.levels or self.progress.levels).get(region, 1)
        return 1.0 + SPEED_PER_LEVEL * (level - 1)

    def effective_scale(self, offer: Offer) -> float:
        """Time trials and deliveries: difficulty against the player's car now. Time trials
        get easier as the player levels past the offer; deliveries don't."""
        if offer.type == "speed":
            region = self.by_id[offer.giver_id].region
            return offer.scale * self.offer_multiplier(offer) / self.progress.speed_scale(region)
        return offer.scale

    def label(self, offer: Offer) -> str:
        if offer.type == "drag":
            region = self.by_id[offer.giver_id].region
            # Straights have no corners to cut: anything rated above the player is Hard.
            edge = RATING_EDGE if offer.track.get("kind") == "circuit" else 0
            return rating_difficulty(offer.rating - self.progress.rating(region), edge)
        return difficulty(self.effective_scale(offer))

    def speed_limit(self, offer: Offer, start) -> float:
        """Timer: distance x DETOUR at 80% x scale of the top speed of the ground covered,
        using the player's levels when the offer was made (so it never shrinks later)."""
        (ax, ay), (bx, by) = start, offer.target
        steps = max(1, int(math.dist(start, offer.target) // 64))
        seconds = 0.0
        for i in range(steps):
            x, y = ax + (bx - ax) * (i + 0.5) / steps, ay + (by - ay) * (i + 0.5) / steps
            region = self.world.region_at(x, y)
            top = SURFACES.get(region, OFF_SURFACE)[1] * self.offer_multiplier(offer, region)
            seconds += math.dist(start, offer.target) / steps * DETOUR / (
                top * SPEED_EFFICIENCY * offer.scale)
        return seconds

    def reward_for(self, offer: Offer, base: int) -> int:
        giver = self.by_id[offer.giver_id]
        full = reward(base, self.progress.levels[giver.region], giver.harder)
        if full <= 0:
            return 0
        return max(1, full >> offer.declines)   # Halved per decline, rounded down, at least 1.

    def preview(self, offer: Offer) -> dict:
        """What the offer panel shows."""
        giver = self.by_id[offer.giver_id]
        label = self.label(offer)
        lines = {"title": giver.name, "difficulty": label}
        if offer.type == "delivery":
            where = self.world.region_at(*offer.target).title()
            km = math.dist((giver.x, giver.y), offer.target) / 10000
            lines["detail"] = f"Deliver a package to {where}, {km:.1f} km away"
            lines["rules"] = f"No time limit  ·  -{DELIVERY_PENALTY[label]} points per crash"
            lines["reward"] = (f"75+ points: {self.reward_for(offer, 2)} mastery  ·  "
                               f"50+: {self.reward_for(offer, 1)}")
        elif offer.type == "speed":
            limit = self.speed_limit(offer, (giver.x, giver.y))
            km = math.dist((giver.x, giver.y), offer.target) / 10000
            lines["detail"] = f"Reach the checkpoint {km:.1f} km away"
            lines["rules"] = f"Time limit {limit:.0f} s  ·  clock starts when you drive off"
            lines["reward"] = f"Beat the clock: {self.reward_for(offer, 1)} mastery"
        else:
            track = "quarter-mile straight" if offer.track["kind"] == "straight" else "single-lap circuit"
            lines["detail"] = f"Race a rival on a {track}"
            lines["rules"] = f"Rival ({offer.rating}) VS You ({self.progress.rating(giver.region)})"
            lines["reward"] = f"Win: {self.reward_for(offer, 3)} mastery"
        if offer.eased:
            lines["reward"] += "  ·  easier offer, reduced reward"
        lines["can_decline"] = self.can_decline(offer)
        return lines

    # Running missions -------------------------------------------------------------

    def accept(self, giver: Giver) -> Offer:
        """Start the giver's offer. Returns it; drag races are run by the caller."""
        offer = self.offer_for(giver)
        limit = self.speed_limit(offer, (giver.x, giver.y)) if offer.type == "speed" else 0.0
        self.active = ActiveMission(offer, giver, limit, self.label(offer))
        return offer

    def update(self, dt: float, player_x: float, player_y: float, in_car: bool,
               moving: bool, crashed: bool):
        """Advance an in-world mission. Returns a result dict when it ends, else None."""
        mission = self.active
        if mission is None or mission.offer.type == "drag":
            return None
        mission.grace = max(0.0, mission.grace - dt)
        mission.flash = max(0.0, mission.flash - dt)
        target = mission.offer.target
        if mission.offer.type == "delivery":
            if crashed and in_car and mission.grace <= 0:
                mission.crashes += 1
                mission.points -= DELIVERY_PENALTY[mission.label]
                mission.grace, mission.flash = CRASH_GRACE, 1.0
                if mission.points < 50:
                    return self._finish(False, f"Too many crashes: {mission.points} points")
            if math.dist((player_x, player_y), target) <= DROP_RANGE:
                base = next((m for need, m in DELIVERY_TIERS if mission.points >= need), 0)
                return self._finish(True, f"Delivered with {mission.points} points", base)
            return None
        # Time trial.
        if not mission.started and in_car and moving:
            mission.started = True
        if mission.started:
            mission.time_left -= dt
            if in_car and math.dist((player_x, player_y), target) <= FINISH_RANGE:
                spare = mission.time_left
                return self._finish(True, f"Made it with {spare:.1f} s to spare", BASE_REWARD["speed"])
            if mission.time_left <= 0:
                return self._finish(False, "Out of time")
        return None

    def abort(self):
        """Give up the ongoing mission: it counts as failed, and the offer stays for a retry."""
        return self._finish(False, "You abandoned the mission")

    def finish_drag(self, won: bool, detail: str):
        return self._finish(won, detail, BASE_REWARD["drag"] if won else 0)

    def _finish(self, success: bool, detail: str, base: int = 0):
        mission = self.active
        giver = mission.giver
        earned = self.reward_for(mission.offer, base) if success else 0
        levels = self.progress.add(giver.region, earned) if earned else []
        if success:
            self.progress.record_completion(giver.region, mission.offer.type)
            del self.offers[giver.id]  # The giver has a fresh mission ready.
        self.active = None
        return {"success": success, "title": giver.name, "detail": detail, "mastery": earned,
                "region": giver.region, "levels": levels, "giver": giver,
                "level": self.progress.levels[giver.region],
                "progress": (self.progress.mastery[giver.region],
                             mastery_to_next(self.progress.levels[giver.region]))}

    # Display ----------------------------------------------------------------------

    def target(self):
        """Where the guide arrow should point, or None to point at the racing center."""
        if self.active and self.active.offer.target:
            return self.active.offer.target
        return None

    def status(self):
        """(title, line) for the HUD's mission panel, or None."""
        if self.active:
            m = self.active
            title = f"{TITLES[m.offer.type].upper()}  ·  {m.label.upper()}"
            if m.offer.type == "delivery":
                line = f"CRASH  -{DELIVERY_PENALTY[m.label]}" if m.flash else \
                    f"Points {m.points}  ·  Crashes {m.crashes}"
            elif m.offer.type == "speed":
                line = f"Time {max(0.0, m.time_left):.1f} s" if m.started else \
                    f"{m.time_limit:.0f} s  ·  starts when you drive"
            else:
                line = "Racing"
            return title, line
        return None

    def sprites(self, clock: float):
        """Givers with bobbing badges, plus the active mission's recipient or finish."""
        out = []
        bob = math.sin(clock * 3) * 3
        busy = self.active is not None
        for giver in self.givers:   # Veterans show with their badges even while locked.
            kind = GIVER_KINDS[giver.region]
            out.append(Sprite("people-atlas", f"{kind}_idle", giver.x, giver.y, 32, 32, 180.0))
            if not busy:
                icon = f"icon_{giver.type}{'_hard' if giver.harder else ''}"
                out.append(Sprite("marker-atlas", icon, giver.x, giver.y - 34 + bob, 32, 32))
        if self.active and self.active.offer.target:
            x, y = self.active.offer.target
            if self.active.offer.type == "delivery":
                out.append(Sprite("people-atlas", "city_c_idle", x, y, 32, 32, 180.0))
                out.append(Sprite("marker-atlas", "icon_dropoff", x, y - 34 + bob, 32, 32))
            else:
                out.append(Sprite("prop-atlas", "race_finish", x, y, 96, 96))
                out.append(Sprite("marker-atlas", "icon_finish", x, y - 60 + bob, 32, 32))
        return out

    def mastery_rows(self, here: str = ""):
        """One row per region for the pause menu's mastery page; here is the player's region."""
        rows = []
        for region in REGIONS:
            level = self.progress.levels[region]
            mission = ""
            if self.active and self.active.giver.region == region:
                mission = f"Ongoing  ·  {TITLES[self.active.offer.type]}"
            rows.append({
                "region": region, "level": level,
                "mastery": self.progress.mastery[region], "need": mastery_to_next(level),
                "speed": round((self.progress.speed_scale(region) - 1) * 100),
                "rating": self.progress.rating(region),
                "veterans": self.progress.harder_unlocked(region),
                "completed": dict(self.progress.completed[region]),
                "mission": mission,
                "races": self.progress.races[region],
                # ready / here / busy (a mission is running) / locked (below level 3).
                "travel": ("locked" if level < FAST_TRAVEL_LEVEL else "here" if region == here
                           else "busy" if self.active else "ready"),
            })
        rows.append({
            # The beach has no mastery: just fast travel to a pier, open with fishing (level 4).
            # There is no "here": travel picks a pier, and another pier may be far along the coast.
            "region": "beach", "kind": "beach", "unlock": f"Lvl {FISHING_LEVEL}",
            "unspent": self.unspent,
            "travel": ("locked" if not self.progress.fishing_unlocked()
                       else "busy" if self.active else "ready"),
        })
        return rows

    def center_position(self, region: str):
        sector = next(sec for sec, name in CENTERS.items() if name == f"center_{region}")
        return self.world.center_position(*sector)

    def giver_near(self, x, y):
        """Any giver within talking range, locked veterans included (they explain)."""
        return next((g for g in self.givers
                     if math.dist((x, y), (g.x, g.y)) <= TALK_RANGE), None)

    def to_dict(self):
        # The ongoing mission is deliberately absent: its offer stays with its giver.
        return {"progress": self.progress.to_dict(), "counter": self.counter,
                "offers": {gid: offer.to_dict() for gid, offer in self.offers.items()},
                "fish": self.fish.to_dict(), "unspent_mastery": self.unspent,
                "arcade": dict(self.arcade), "arcade_unlocked": sorted(self.arcade_unlocked),
                "inventory": dict(self.items), "farm": self.farm.to_dict(),
                "farm_orders": self.orders.to_dict(), "fair": self.fair.to_dict(),
                "factory": self.factory.to_dict()}

    def add_universal(self, points: int) -> int:
        """Universal mastery (fish trades now; farm deliveries and factory stones later)
        goes to the inventory's Mastery points stack, up to STACK_MAX; spend() puts it into
        regions and whatever isn't spent stays. Returns how many were added."""
        added = max(0, min(points, room(self.unspent)))
        self.unspent += added
        return added

    def trade_fish(self):
        """Hand fish to a jungle fish trader: returns (fish, points). The points go to the
        inventory (add_universal); fish that would push it past the stack limit stay in
        the bag."""
        count, value = self.fish.take_bag(room(self.unspent))
        self.add_universal(value)
        return count, value

    def add_item(self, item_id: str, amount: int = 1) -> int:
        """Add to an inventory stack (e.g. "sunside_tokens"), up to the item's max; returns
        how many were added. Negative amounts take away, never below 0."""
        have = self.items.get(item_id, 0)
        new = max(0, min(BY_ID[item_id].max_stack, have + amount))
        if new:
            self.items[item_id] = new
        else:
            self.items.pop(item_id, None)
        return new - have

    @property
    def tokens(self) -> int:
        return self.items.get("sunside_tokens", 0)

    def spend(self, region: str, amount: int = 1) -> list[int]:
        """Put up to amount unspent points into a region; returns the levels reached."""
        amount = min(amount, self.unspent)
        if amount <= 0 or region not in REGIONS:
            return []
        self.unspent -= amount
        return self.progress.add(region, amount)
