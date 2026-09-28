"""Race levels: separate, empty tracks (straight or circuit) with one AI rival.

Used by drag race missions (a quarter mile or a single lap) and racing-center races
(3 laps). Each track uses its region's surface (city asphalt, snow ice, rural mud,
desert sand, jungle grass) with that region's grip and top speed.

TrackLevel stands in for World while racing: the player's Car and CollisionManager use its
region_at, can_place_car, and nearby_obstacles, so driving and crashes work unchanged.
"""

from __future__ import annotations

import math
import random

from car import ACCELERATION, BRAKING, ICE_SCRUB, ICE_TRACTION, SURFACES, Car
from collision_manager import CollisionManager
from progression import RATING_EDGE, SPEED_PER_LEVEL, rating_speed
from track_gen import generate
from traffic import lane_path
from world import TILE_SIZE, Sprite


STRAIGHT_RACE_TILES = 63      # 63 x 64 px = 4,032 px = a quarter mile at 10 px per metre.
STRAIGHT_LEAD, STRAIGHT_RUNOFF = 4, 14
MARGIN_TILES = 10             # Ground drawn around the track.
TRACK_HALF = 1                # Track is 3 tiles wide: centerline cell +/- 1.
# Rectilinear single-lap circuits, centerline corners in tile units, driven clockwise.
CIRCUITS = (
    ((0, 0), (26, 0), (26, 14), (0, 14)),
    ((0, 0), (28, 0), (28, 10), (14, 10), (14, 18), (0, 18)),
    ((0, 0), (28, 0), (28, 18), (20, 18), (20, 8), (8, 8), (8, 18), (0, 18)),
    ((0, 0), (20, 0), (20, 6), (30, 6), (30, 16), (12, 16), (12, 10), (0, 10)),
    ((0, 0), (34, 0), (34, 10), (0, 10)),
)
# Region -> off-track ground tile. The track itself is that region's surface, driven with
# the region's grip and top speed (car.SURFACES); the runoff off the track is slower.
THEMES = {"city": "city_concrete", "rural": "rural_grass", "snow": "snow",
          "desert": "desert_sand", "jungle": "jungle_ground", "island": "island_grass"}
OFF_TRACK = "offtrack"        # Not in car.SURFACES, so it drives with car.OFF_SURFACE.
COUNTDOWN = 3.0
# A skilled human's clean lap vs flawless_time's model (flat out through every corner on
# the apex line): measured at about 3.6% slower on a size-5 city circuit.
CLEAN_LAP = 1.04
RIVAL_BUMP_SPEED = 20.0       # px/s kept when the player bumps the rival with the throttle down.
OFF_DAY = 10.0                # A rated rival's worst day costs it this many rating points.
RIVAL_CUT = 0.5               # Rated rivals cut half the corners (seeded) and run wide at the rest.
CORNER_SPEED = 70.0           # AI corner speed at scale 1; a clean player line carries more.
AI_TURN_RATE = 300.0
RIVALS = ("racer_cyan", "racer_yellow", "racer_purple", "racer_orange",
          "racer_lime", "racer_blue", "racer_black")
# Edge art sits on a tile's north side; GL rotation is counterclockwise in degrees.
EDGE_ROTATION = {"N": 0.0, "W": 90.0, "S": 180.0, "E": -90.0}
CORNER_ROTATION = {frozenset("NW"): 0.0, frozenset("NE"): -90.0,
                   frozenset("SE"): 180.0, frozenset("SW"): 90.0}
DIAGONALS = {(-1, -1): 0.0, (1, -1): -90.0, (1, 1): 180.0, (-1, 1): 90.0}


def _cells_along(corners, closed):
    """Every tile on the centerline between successive corners."""
    cells = []
    pairs = zip(corners, corners[1:] + corners[:1]) if closed else zip(corners, corners[1:])
    for (ax, ay), (bx, by) in pairs:
        steps = max(abs(bx - ax), abs(by - ay))
        for i in range(steps):
            cells.append((ax + (bx - ax) * i // steps, ay + (by - ay) * i // steps))
    if not closed:
        cells.append(corners[-1])
    return cells


class TrackLevel:
    def __init__(self, kind: str, theme: str, shape: int = 0, laps: int = 1,
                 corners=None):
        """corners: a generated circuit (track_gen); otherwise the fixed layout `shape`."""
        self.kind = kind
        self.surface = theme if theme in THEMES else "city"
        self.ground = THEMES[self.surface]
        self.laps = laps if kind == "circuit" else 1
        if kind == "straight":
            corners = [(0, 0), (STRAIGHT_LEAD + STRAIGHT_RACE_TILES + STRAIGHT_RUNOFF, 0)]
            self.closed = False
        else:
            corners = list(corners or CIRCUITS[shape % len(CIRCUITS)])
            self.closed = True
        # Shift so the whole level (with margins) sits at positive coordinates.
        min_x = min(x for x, _ in corners) - MARGIN_TILES
        min_y = min(y for _, y in corners) - MARGIN_TILES
        self.corners = [(x - min_x, y - min_y) for x, y in corners]
        centerline = _cells_along(self.corners, self.closed)
        self.track = {(cx + i, cy + j) for cx, cy in centerline
                      for i in range(-TRACK_HALF, TRACK_HALF + 1)
                      for j in range(-TRACK_HALF, TRACK_HALF + 1)}
        self.cols = max(x for x, _ in self.track) + MARGIN_TILES + 1
        self.rows = max(y for _, y in self.track) + MARGIN_TILES + 1
        self.width, self.height = self.cols * TILE_SIZE, self.rows * TILE_SIZE
        # Centerline in pixels; the race runs from the start line along it.
        self.path = [((x + 0.5) * TILE_SIZE, (y + 0.5) * TILE_SIZE) for x, y in self.corners]
        start_col = self.corners[0][0] + STRAIGHT_LEAD
        self.start_x = (start_col + 0.5) * TILE_SIZE
        self.finish_x = (start_col + STRAIGHT_RACE_TILES + 0.5) * TILE_SIZE
        self.lap_length = sum(math.dist(a, b) for a, b in self._segments())
        self.sprites, self.obstacles = [], []
        self._build(start_col)
        self._bins = {}
        for sprite in self.sprites:
            self._bins.setdefault((int(sprite.x // 512), int(sprite.y // 512)), []).append(sprite)
        self._obstacle_bins = {}
        for sprite in self.obstacles:
            self._obstacle_bins.setdefault((int(sprite.x // 256), int(sprite.y // 256)), []).append(sprite)

    def _segments(self):
        points = self.path + (self.path[:1] if self.closed else [])
        return list(zip(points, points[1:]))

    def _build(self, start_col):
        track, sprites = self.track, self.sprites
        start_row = self.corners[0][1]
        for ty in range(self.rows):
            for tx in range(self.cols):
                x, y = (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE
                sprites.append(Sprite("terrain-atlas", self.ground, x, y, TILE_SIZE, TILE_SIZE))
                if (tx, ty) in track:
                    name, rotation = self._track_tile(tx, ty)
                    on_line = abs(ty - start_row) <= TRACK_HALF
                    if on_line and tx == start_col:
                        # A single lap starts and ends on the same checkered line.
                        name, rotation = self._tile("finish" if self.closed else "start"), 0.0
                    elif on_line and not self.closed and tx == start_col + STRAIGHT_RACE_TILES:
                        name, rotation = self._tile("finish"), 0.0
                    sprites.append(Sprite("track-atlas", name, x, y, TILE_SIZE, TILE_SIZE, rotation))
                    continue
                # Barriers wall off the track on both sides; tire stacks fill the corners.
                beside = [(dx, dy) for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0))
                          if (tx + dx, ty + dy) in track]
                diagonal = any((tx + dx, ty + dy) in track for dx, dy in DIAGONALS)
                if len(beside) == 1:
                    horizontal = beside[0][0] == 0
                    # The solid box rotates with the sprite, like every obstacle record.
                    fence = Sprite("track-atlas", "track_fence", x, y, TILE_SIZE, TILE_SIZE,
                                   0.0 if horizontal else 90.0, TILE_SIZE, 16)
                    sprites.append(fence)
                    self.obstacles.append(fence)
                elif beside or diagonal:
                    tires = Sprite("track-atlas", "track_tires", x, y, TILE_SIZE, TILE_SIZE,
                                   0.0, 44, 44)
                    sprites.append(tires)
                    self.obstacles.append(tires)

    def _track_tile(self, tx, ty):
        off = [side for side, (dx, dy) in (("N", (0, -1)), ("S", (0, 1)), ("W", (-1, 0)), ("E", (1, 0)))
               if (tx + dx, ty + dy) not in self.track]
        if len(off) == 1:
            return self._tile("edge"), EDGE_ROTATION[off[0]]
        if len(off) == 2 and frozenset(off) in CORNER_ROTATION:
            return self._tile("corner"), CORNER_ROTATION[frozenset(off)]
        missing = [d for d in DIAGONALS if (tx + d[0], ty + d[1]) not in self.track]
        if not off and len(missing) == 1:
            return self._tile("inner"), DIAGONALS[missing[0]]
        return self._tile("base"), 0.0

    def _tile(self, part):
        return f"track_{self.surface}_{part}"

    # World stand-in -------------------------------------------------------------

    def region_at(self, x, y):
        on_track = (int(x // TILE_SIZE), int(y // TILE_SIZE)) in self.track
        return self.surface if on_track else OFF_TRACK

    def is_ice(self, x, y):
        """Snow tracks are ice: cars slide on them (rivals less than the player)."""
        return self.surface == "snow" and (int(x // TILE_SIZE), int(y // TILE_SIZE)) in self.track

    def can_place_car(self, rect):
        return 0 <= rect[0] <= self.width and 0 <= rect[1] <= self.height

    def nearby_obstacles(self, x, y):
        bx, by = int(x // 256), int(y // 256)
        return [s for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                for s in self._obstacle_bins.get((bx + dx, by + dy), ())
                if abs(s.x - x) < 145 and abs(s.y - y) < 145]

    def visible_sprites(self, camera_x, camera_y, width, height):
        pad = TILE_SIZE
        x0, x1 = int((camera_x - pad) // 512), int((camera_x + width + pad) // 512)
        y0, y1 = int((camera_y - pad) // 512), int((camera_y + height + pad) // 512)
        return [s for by in range(y0, y1 + 1) for bx in range(x0, x1 + 1)
                for s in self._bins.get((bx, by), ())]

    # Race geometry ----------------------------------------------------------------

    def start_pose(self, lane: int):
        """Start behind the line; lane -1 is left of the centerline, +1 right."""
        (ax, ay), (bx, by) = self.path[0], self.path[1]
        heading = math.degrees(math.atan2(bx - ax, -(by - ay))) % 360
        fx, fy = (bx - ax) / math.dist((ax, ay), (bx, by)), (by - ay) / math.dist((ax, ay), (bx, by))
        rx, ry = -fy, fx
        back = 1.5 * TILE_SIZE
        line_x, line_y = self.start_x, ay
        return line_x - fx * back + rx * lane * 44, line_y - fy * back + ry * lane * 44, heading

    def progress_of(self, x, y, previous):
        """Distance driven along the course, kept continuous from the previous value."""
        if not self.closed:
            return x - self.start_x
        best, best_gap = previous, math.inf
        run = 0.0
        for (ax, ay), (bx, by) in self._segments():
            length = math.dist((ax, ay), (bx, by))
            t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / (length * length)))
            along = run + t * length - (self.start_x - self.path[0][0])
            distance = math.dist((x, y), (ax + (bx - ax) * t, ay + (by - ay) * t))
            # Pick the projection closest to where we were (handles the lap seam).
            laps = round((previous - along) / self.lap_length)
            candidate = along + laps * self.lap_length
            if distance < 160 and abs(candidate - previous) < best_gap:
                best, best_gap = candidate, abs(candidate - previous)
            run += length
        return best if best_gap < 300 else previous

    @property
    def race_length(self):
        return self.finish_x - self.start_x if not self.closed else self.lap_length * self.laps


APEX = 84                     # A cut corner's apex: this far inside the centerline corner.
WIDE_APEX = 60                # A missed cut: the rival runs a little wide, leaving about a car's
                              # width on the inside (wider gaps cost it more time than its rating
                              # speed can win back, so rivals would play easier than rated).
CUT_REACH = 5 * TILE_SIZE     # A cut drifts from the centerline this far before the corner
                              # (at most half the straight) to the apex, and back after it, so
                              # long straights stay central and cuts are shallow diagonals.


def _toward(point, other, distance):
    """The point `distance` px from point in the direction of other."""
    length = math.dist(point, other)
    return (point[0] + (other[0] - point[0]) * distance / length,
            point[1] + (other[1] - point[1]) * distance / length)


def apex(prev, corner, following, depth: float = APEX):
    """Inside apex of a centerline corner, depth px in (APEX: the tightest line a car can take)."""
    (ax, ay), (px, py), (bx, by) = prev, corner, following
    cross = (px - ax) * (by - py) - (py - ay) * (bx - px)  # > 0: a right turn (screen y down).
    return lane_path([prev, corner, following], depth if cross > 0 else -depth)[1]


def best_line_length(level: TrackLevel) -> float:
    """Race distance on the ideal line: every corner cut at its apex."""
    if not level.closed:
        return level.race_length
    pts, n = level.path, len(level.path)
    apexes = [apex(pts[i - 1], pts[i], pts[(i + 1) % n]) for i in range(n)]
    lap = sum(math.dist(apexes[i], apexes[(i + 1) % n]) for i in range(n))
    return lap * level.laps


# Rivals slide on ice with the player's car physics (car.ICE_TRACTION, car.ICE_SCRUB):
# the direction of travel lags the nose, and sliding sideways scrubs speed. They steer
# sharper than the player on ice (RIVAL_ICE_STEER x the player's rate), so slides stay
# modest. The slide carries them off their line as a world-space offset that builds and
# fades smoothly (no jumps at corners); near the track's edge it is eased back in.
RIVAL_ICE_STEER = 2.5
RIVAL_ICE_RECOVER = 1.5        # Per second: steering back onto the line.
RIVAL_ICE_ROOM = 72            # Largest offset from the line, px (held there, not snapped).
RIVAL_ICE_EASE = 3.0           # px per frame it eases back in when near the track's edge.


class Rival:
    """Drives a line fixed for the whole race: full throttle on straights, braking for
    every corner. It cuts each corner with probability cut_chance (seeded, so a race always
    plays out the same way): a shallow drift from mid-straight to the apex and back."""

    def __init__(self, level: TrackLevel, scale: float, rng: random.Random,
                 sprite: str | None = None, cut_chance: float = 0.0, wide_misses: bool = False,
                 start=None):
        self.level = level
        self.name = sprite or rng.choice(RIVALS)
        self.reaction = rng.uniform(0.2, 0.6)
        self.set_scale(scale)
        # Starts in the right-hand grid slot, or `start` (x, y, heading) on a bigger grid.
        x, y, self.heading = start or level.start_pose(1)
        points, self.cuts = [(x, y)], 0
        brake_points = set()  # Indexes into points where the rival slows for a corner.
        if level.closed:
            pts, n = level.path, len(level.path)
            # Merge from the grid slot onto the centerline right away.
            points.append((x + TILE_SIZE, pts[0][1]))
            # One extra lap of path so the car never runs out of road before it finishes.
            order = [i for _ in range(level.laps + 1) for i in list(range(1, n)) + [0]]
            cuts = [rng.random() < cut_chance for _ in order]

            def turn(i):
                (ax, ay), (px, py), (bx, by) = pts[i - 1], pts[i], pts[(i + 1) % n]
                return (px - ax) * (by - py) - (py - ay) * (bx - px) > 0

            def hold_inside(k):
                """Cut corners k and k + 1 turn the same way over a short straight: stay on the
                inside between them, as a racer would, instead of returning to center."""
                if k + 1 >= len(order) or not (cuts[k] and cuts[k + 1]):
                    return False
                i, j = order[k], order[k + 1]
                return turn(i) == turn(j) and math.dist(pts[i], pts[j]) <= 2 * CUT_REACH

            for k, i in enumerate(order):
                prev, corner, following = pts[i - 1], pts[i], pts[(i + 1) % n]
                if cuts[k] or wide_misses:
                    # Drift to the apex from mid-straight (or CUT_REACH before), brake at
                    # the apex, and drift back: long straights stay central. A missed cut
                    # (rated rivals) runs wide, WIDE_APEX in, leaving the inside open.
                    self.cuts += cuts[k]
                    if not (k and hold_inside(k - 1)):
                        points.append(_toward(corner, prev, min(CUT_REACH, math.dist(prev, corner) / 2)))
                    brake_points.add(len(points))
                    points.append(apex(prev, corner, following, APEX if cuts[k] else WIDE_APEX))
                    if not hold_inside(k):
                        points.append(_toward(corner, following,
                                              min(CUT_REACH, math.dist(corner, following) / 2)))
                else:
                    brake_points.add(len(points))
                    points.append(corner)
            if points[2][0] <= points[1][0]:
                # The first corner's cut begins before the merge point: steer straight for
                # the diagonal instead of doubling back.
                points.pop(1)
                brake_points = {i - 1 for i in brake_points}
            # A cut's exit and the next cut's entry can meet mid-straight: drop repeats.
            kept, renumber = [points[0]], {0: 0}
            for index, point in enumerate(points[1:], start=1):
                if math.dist(point, kept[-1]) > 1e-6:
                    kept.append(point)
                renumber[index] = len(kept) - 1
            points, brake_points = kept, {renumber[i] for i in brake_points}
        else:
            points.append((level.finish_x + 8 * TILE_SIZE, y))
        self.points = points
        self.lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        # Distances along the path of every corner (a centerline corner or a cut apex).
        self.corners, self._starts, run = [], [], 0.0
        for index, length in enumerate(self.lengths, start=1):
            self._starts.append(run)
            run += length
            if index in brake_points:
                self.corners.append(run)
        self.distance, self.speed = 0.0, 0.0
        self._next_corner, self._segment = 0, 0
        self.offset = (0.0, 0.0)           # Ice slide: world px off the line.
        self.travel = self.heading         # Direction of motion; lags the nose on ice.
        self.x, self.y = x, y
        self.progress = (level.progress_of(x, y, -1.5 * TILE_SIZE) if level.closed
                         else x - level.start_x)

    def set_scale(self, scale: float):
        """Scale the base car on this surface (not the player's upgrades: levels are the
        player's edge). Grip limits acceleration, braking, and cornering, as for the player."""
        grip, top = SURFACES[self.level.surface]
        self.scale = scale
        self.top = top * scale
        self.accel = ACCELERATION * grip * scale
        self.brake = BRAKING * 0.6 * grip * scale
        self.corner = CORNER_SPEED * grip * scale

    def rate(self, top_multiplier: float, corner: float):
        """A rated rival: a car rated at top_multiplier (the player's acceleration and top
        speed at that rating) that slows to `corner` px/s for every corner."""
        grip, top = SURFACES[self.level.surface]
        self.scale = top_multiplier
        self.top = top * top_multiplier
        self.accel = ACCELERATION * grip
        self.brake = BRAKING * 0.6 * grip
        self.corner = min(corner, self.top)

    def update(self, dt, clock):
        if clock < self.reaction:
            return
        self.speed, self.distance, self._next_corner = _step(
            self.speed, self.distance, self._next_corner, self.corners,
            self.top, self.accel, self.brake, self.corner, dt)
        self._place(dt)
        # Race progress is measured on the centerline, exactly like the player's.
        self.progress = self.level.progress_of(self.x, self.y, self.progress)

    def _place(self, dt):
        # Walk forward from the current segment (the rival never reverses). Corners repeat
        # every lap, so the final segment is found by index, not by point.
        last = len(self.lengths) - 1
        while self._segment < last and self.distance > self._starts[self._segment + 1]:
            self._segment += 1
        i = self._segment
        a, b, length = self.points[i], self.points[i + 1], self.lengths[i]
        t = min(1.0, (self.distance - self._starts[i]) / length)
        line_x, line_y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        target = math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360
        turn = (target - self.heading + 180) % 360 - 180
        icy = self.level.is_ice(line_x, line_y)
        # On ice the rival steers sharper than the player can, keeping slides modest.
        rate = (RIVAL_ICE_STEER * 135 * SURFACES[self.level.surface][0] * min(1.0, self.speed / 130)
                if icy else AI_TURN_RATE)
        step = rate * dt
        self.heading = (self.heading + max(-step, min(step, turn))) % 360
        # Its travel lags its nose like the player's; moving along the travel instead of
        # the line carries it off the line, as a world-space offset (continuous at corners).
        ox, oy = self.offset
        if icy and self.speed > 1:
            slide = (self.heading - self.travel + 540) % 360 - 180
            self.travel = (self.travel + slide * min(1.0, ICE_TRACTION * dt)) % 360
            self.speed *= max(0.0, 1 - ICE_SCRUB * abs(math.sin(math.radians(slide))) * dt)
            tr, ln = math.radians(self.travel), math.radians(target)
            ox += self.speed * dt * (math.sin(tr) - math.sin(ln))
            oy += self.speed * dt * (-math.cos(tr) + math.cos(ln))
        else:
            self.travel = self.heading
        recover = min(1.0, RIVAL_ICE_RECOVER * dt)
        ox, oy = ox * (1 - recover), oy * (1 - recover)           # Steering back to the line.
        # Hold the slide at RIVAL_ICE_ROOM, and near the track's edge ease it back in a few
        # px a frame: the offset only ever changes gradually, so the car never jumps.
        size = math.hypot(ox, oy)
        if size > RIVAL_ICE_ROOM:
            ox, oy = ox * RIVAL_ICE_ROOM / size, oy * RIVAL_ICE_ROOM / size
            size = RIVAL_ICE_ROOM
        if size and (int((line_x + ox) // TILE_SIZE), int((line_y + oy) // TILE_SIZE)) not in self.level.track:
            shrink = max(0.0, size - RIVAL_ICE_EASE) / size
            ox, oy = ox * shrink, oy * shrink
        self.offset = (ox, oy)
        self.x, self.y = line_x + ox, line_y + oy

    def sprite(self):
        return Sprite("vehicle-atlas", self.name, self.x, self.y, 64, 64, -self.heading, 24, 44)


def _step(speed, distance, next_corner, corners, top, accel, brake, corner, dt):
    """One tick of a rival's speed along its path: brake in time to take the next corner
    at `corner` px/s, otherwise accelerate to top. Returns speed, distance, next corner."""
    while next_corner < len(corners) and corners[next_corner] < distance:
        next_corner += 1
    target = top
    if next_corner < len(corners):
        ahead = corners[next_corner] - distance
        if speed * speed - corner * corner > 2 * brake * max(ahead - 8, 0):
            target = corner
    if speed < target:
        speed = min(target, speed + accel * dt)
    else:
        speed = max(target, speed - brake * dt)
    return speed, distance + speed * dt, next_corner


def finish_distance(rival: Rival) -> float:
    """How far along its own path the rival crosses the finish line."""
    level = rival.level
    if not level.closed:
        return level.finish_x - rival.points[0][0]
    progress, run = rival.progress, 0.0
    for (a, b), length in zip(zip(rival.points, rival.points[1:]), rival.lengths):
        steps = max(1, int(length // 8))
        for k in range(1, steps + 1):
            t = k / steps
            progress = level.progress_of(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, progress)
            if progress >= level.race_length:
                return run + length * t
        run += length
    raise RuntimeError("rival path never reaches the finish")


def rated_time(rival: Rival, finish: float, corner: float) -> float:
    """Seconds from GO for this rated rival (start delay included) at this corner speed."""
    speed = distance = 0.0
    next_corner, seconds, dt = 0, rival.reaction, 1 / 60
    corner = min(corner, rival.top)
    while distance < finish:
        speed, distance, next_corner = _step(speed, distance, next_corner, rival.corners, rival.top,
                                             rival.accel, rival.brake, corner, dt)
        seconds += dt
    return seconds


def calibrated_corner(rival: Rival, target: float) -> float:
    """Corner speed at which this rated rival finishes in `target` seconds: its one tuned
    imperfection. If even never slowing is too slow, it never slows (and is a bit easier)."""
    finish = finish_distance(rival)
    low, high = 5.0, rival.top
    if rated_time(rival, finish, high) >= target:
        return high
    if rated_time(rival, finish, low) <= target:
        return low
    for _ in range(30):
        mid = (low + high) / 2
        if rated_time(rival, finish, mid) > target:
            low = mid
        else:
            high = mid
        if high - low < 0.05:
            break
    return (low + high) / 2


def off_day(roll: float) -> float:
    """Rating points a rival loses on the day, 0-OFF_DAY: small ones common, big ones rare
    (roll is uniform 0-1)."""
    return OFF_DAY * roll ** 3


def level_multiplier(region_level: int) -> float:
    return 1 + SPEED_PER_LEVEL * (region_level - 1)


def flawless_time(level: TrackLevel, region_level: int = 1, multiplier: float | None = None) -> float:
    """Race time of a flawless player: flat out on the ideal line (every corner cut),
    at region_level (or a given top-speed multiplier)."""
    grip, top = SURFACES[level.surface]
    speed = top * (multiplier if multiplier is not None else level_multiplier(region_level))
    accel = ACCELERATION * grip
    distance = best_line_length(level) + 1.5 * TILE_SIZE  # Grid slot is behind the line.
    return speed / accel + (distance - speed * speed / (2 * accel)) / speed


def rival_time(level: TrackLevel, rival_seed: int, cut_chance: float, scale: float = 1.0,
               sprite: str | None = None, reaction: bool = False) -> float:
    """Seconds this exact rival (same seeded line and start delay) needs at this scale,
    with its start delay if reaction."""
    rival = Rival(level, scale, random.Random(rival_seed), sprite, cut_chance)
    seconds, dt = (rival.reaction if reaction else 0.0), 1 / 60  # The game's rate.
    while rival.progress < level.race_length:
        rival.update(dt, rival.reaction)
        seconds += dt
        if seconds > 1200:  # Twenty minutes: the rival is stuck, not slow.
            raise RuntimeError("rival never finished the calibration run")
    return seconds


def calibrated_scale(level: TrackLevel, multiplier: float, rival_seed: int,
                     cut_chance: float = 0.0, margin: float = 1.0, sprite: str | None = None,
                     reaction: bool = False) -> float:
    """Rival scale at which a flawless player with this top-speed multiplier wins by margin
    (counting the rival's start delay if reaction).

    Race time falls roughly as 1 / scale, but not exactly (acceleration and braking also
    scale), so start from that estimate and correct it with a few runs of the same
    seeded rival until it lands within 0.02 s of the target."""
    target = flawless_time(level, multiplier=multiplier) * margin
    scale = rival_time(level, rival_seed, cut_chance, 1.0, sprite, reaction) / target
    for _ in range(6):
        seconds = rival_time(level, rival_seed, cut_chance, scale, sprite, reaction)
        if abs(seconds - target) < 0.02:
            break
        scale *= seconds / target
    return round(scale, 4)


class DragRace:
    """One race: countdown, both racers go, first across the finish wins."""

    def __init__(self, track: dict, scale: float | None, speed_scale: float, seed: int,
                 rival_sprite: str | None = None, rival_rating: float | None = None,
                 rival_off_day: float | None = None, f1: bool = False):
        rng = random.Random(seed)
        corners = (generate(track["seed"], track.get("size", 5))
                   if track["kind"] == "circuit" and "seed" in track else None)
        self.level = TrackLevel(track["kind"], track.get("theme", "city"), track.get("shape", 0),
                                track.get("laps", 1), corners)
        self.speed_scale = speed_scale
        x, y, heading = self.level.start_pose(-1)
        self.car = Car(x=x, y=y, heading=heading, f1=f1)
        rival_seed = rng.randrange(1 << 30)
        self.off_day = 0.0
        if rival_rating is not None:
            self.rival = self._rated_rival(track, rival_rating, rival_off_day, rival_seed, rival_sprite)
        else:
            cut_chance = track.get("cut_chance", 0.0)
            if self.level.closed:
                # A bare scale means "a flawless s x stock car on the ideal line".
                scale = calibrated_scale(self.level, scale, rival_seed, cut_chance)
            self.rival = Rival(self.level, scale, random.Random(rival_seed), rival_sprite, cut_chance)
        self.collisions = CollisionManager(None, self.level)
        self.clock = -COUNTDOWN     # Negative while counting down.
        self.player_progress = self.level.progress_of(x, y, -1.5 * TILE_SIZE) \
            if self.level.closed else x - self.level.start_x
        self.result = None          # "win" or "lose" once decided.
        self.times = {}

    def _rated_rival(self, track, rating, off_day_points, rival_seed, sprite):
        """A rival that drives exactly like a car of its rating (top speed and acceleration),
        rerolling an off-day of 0-OFF_DAY points each attempt.

        Straight: no corners and no start delay, so a flat-out player rated R ties it and
        wins. Circuit: it takes the racing line, cutting RIVAL_CUT of the corners and running
        wide at the rest (an opening to pass on the inside). Its one tuned imperfection is
        how much it slows for corners, aiming for a clean lap (CLEAN_LAP x the flat-out
        model) rated RATING_EDGE below it to just win; where the wide corners already cost
        more than that, it never slows and is a little easier than its rating."""
        self.off_day = off_day(random.random()) if off_day_points is None else off_day_points
        rated = rating - self.off_day
        rival = Rival(self.level, 1.0, random.Random(rival_seed), sprite,
                      RIVAL_CUT if self.level.closed else 0.0, wide_misses=True)
        rival.rate(rating_speed(rated), math.inf)
        if not self.level.closed:
            rival.reaction = 0.0
            return rival
        target = flawless_time(self.level, multiplier=rating_speed(rated - RATING_EDGE)) * CLEAN_LAP * 1.005
        rival.rate(rating_speed(rated), calibrated_corner(rival, target))
        return rival

    def _blocked_only_by_rival(self) -> bool:
        """Whether the car's next step forward is clear once the rival is ignored."""
        car = self.car
        heading = math.radians(car.heading)
        step_x, step_y = car.x + math.sin(heading) * 12, car.y - math.cos(heading) * 12
        record = car.collision_record(step_x, step_y)
        self.collisions.fixed = []
        clear_without = self.collisions.can_move(record)
        self.collisions.fixed = [self.rival.sprite()]
        return clear_without and not self.collisions.can_move(record)

    @property
    def countdown(self):
        return max(0, math.ceil(-self.clock)) if self.clock < 0 else 0

    def update(self, dt, throttle, steer):
        if self.result:
            self.car.speed *= max(0.0, 1 - 2 * dt)
            return
        self.clock += dt
        if self.clock < 0:
            return
        # The rival is solid to the player; it holds its line regardless.
        self.collisions.fixed = [self.rival.sprite()]
        before = self.car.speed
        self.car.update(dt, throttle, steer, self.level, self.collisions,
                        self.speed_scale)
        if throttle > 0 and before > 0 and self.car.speed == 0 and self._blocked_only_by_rival():
            # Bumping the rival with the throttle down keeps a little momentum (walls don't).
            self.car.speed = min(before, RIVAL_BUMP_SPEED)
        self.rival.update(dt, self.clock)
        self.player_progress = self.level.progress_of(self.car.x, self.car.y, self.player_progress)
        length = self.level.race_length
        if self.player_progress >= length and "player" not in self.times:
            self.times["player"] = self.clock
        if self.rival.progress >= length and "rival" not in self.times:
            self.times["rival"] = self.clock
        if self.times:
            self.result = "win" if "player" in self.times and (
                "rival" not in self.times or self.times["player"] <= self.times["rival"]) else "lose"

    def position(self):
        return 1 if self.player_progress >= self.rival.progress else 2

    def lap(self):
        """Current lap (1-based) of the player, for multi-lap races."""
        if not self.level.closed:
            return 1
        return max(1, min(self.level.laps, int(self.player_progress // self.level.lap_length) + 1))

    def sprites(self, camera_x, camera_y, width, height):
        return self.level.visible_sprites(camera_x, camera_y, width, height) + [self.rival.sprite()]
