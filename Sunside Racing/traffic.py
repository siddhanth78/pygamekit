"""Rudimentary traffic: cars drive fixed lanes and yield to the player and each other."""

from __future__ import annotations

import math
import random

from gl_utils import check_collision
from world import (
    CENTERS, CITY_SECTORS_X, CITY_SECTORS_Y, DIRT_ROUTES, SECTOR_SIZE, SNOW_ROUTE,
    TILE_SIZE, Sprite,
)


CITY_CARS = ("traffic_red", "traffic_blue", "traffic_green", "traffic_white", "traffic_gray",
             "traffic_yellow", "traffic_taxi", "traffic_van", "traffic_bus", "traffic_police",
             "traffic_delivery", "traffic_compact", "traffic_wagon")
RURAL_CARS = ("traffic_pickup", "traffic_service", "traffic_wagon", "traffic_gray")
SNOW_CARS = ("traffic_suv", "traffic_van", "traffic_pickup", "traffic_white")
OFFROAD_CARS = ("traffic_pickup", "traffic_suv", "traffic_service")
# Extra pairs of one type each: (sprite, where). City pairs share their own loop.
PAIRS = (("traffic_taxi", "city"), ("traffic_bus", "city"), ("traffic_police", "city"),
         ("traffic_delivery", "city"), ("traffic_suv", "snow"))
CAR_SIZE = 60
SOLID = {"traffic_bus": (22, 50)}
DEFAULT_SOLID = (20, 40)
CITY_LANE = 10   # City roads are 42 px wide; ice and dirt roads are 30 px wide.
RURAL_LANE = 7
ACTIVE_RADIUS = 1600  # Cars farther than this from the player skip yielding checks.
FOLLOW_GAP = 56
TURN_RATE = 360.0     # Degrees per second the sprite rotates toward its lane.
CROSSING_HALF = 30    # Half-size of the box drivers treat as a city intersection.
APPROACH = 34         # How far ahead of its center a car checks for a busy crossing.
PATIENCE = 4.0        # Seconds a car waits before squeezing through anyway (no gridlock).
EXTRA_CITY_LOOPS = 70  # Busy city; rural, snow, jungle, and desert stay quiet.
HIGHWAY_CARS = 3       # Per direction: the highway stays open for speed.
HIGHWAY_SPEED = (185, 215)
HIGHWAY_LANE = 40      # px right of the median: the outer lane each way.


def _tile_center(tile):
    return (tile[0] + 0.5) * TILE_SIZE, (tile[1] + 0.5) * TILE_SIZE


def _right(a, b):
    (ax, ay), (bx, by) = a, b
    length = math.hypot(bx - ax, by - ay)
    # Screen y points down, so a north-facing driver's right hand is east.
    return -(by - ay) / length, (bx - ax) / length


def lane_path(points, lane):
    """Offset a closed centerline to its right-hand lane, mitering corners."""
    result = []
    count = len(points)
    for i, (px, py) in enumerate(points):
        prev_pt, next_pt = points[i - 1], points[(i + 1) % count]
        rin = _right(prev_pt, (px, py))
        rout = _right((px, py), next_pt)
        dot = rin[0] * rout[0] + rin[1] * rout[1]
        if dot < -0.99:
            # Dead end: U-turn across both lanes.
            result.append((px + rin[0] * lane, py + rin[1] * lane))
            result.append((px + rout[0] * lane, py + rout[1] * lane))
        else:
            scale = lane / (1 + dot)
            result.append((px + (rin[0] + rout[0]) * scale, py + (rin[1] + rout[1]) * scale))
    return result


def there_and_back(points):
    """Close an open route so cars drive to its end and return."""
    return list(points) + list(points[-2:0:-1])


class TrafficCar:
    def __init__(self, name, path, speed, distance, size=CAR_SIZE, solid=None,
                 reverse_segments=frozenset(), trip=False):
        self.name = name
        self.path = path
        self.lengths = [math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path))]
        self.speed = speed
        self.size = size
        self.solid = solid or SOLID.get(name, DEFAULT_SOLID)
        self.reverse_segments = reverse_segments  # Segments driven backwards (backing out).
        self.trip_length = sum(self.lengths) if trip else None  # One pass, then done.
        self.travelled = 0.0
        self.waited = 0.0
        self.segment = 0
        self.progress = 0.0
        self.x, self.y = path[0]
        self.advance(distance % sum(self.lengths))
        self.heading = self.lane_heading()

    @property
    def finished(self):
        return self.trip_length is not None and self.travelled >= self.trip_length

    def advance(self, distance):
        self.travelled += distance
        if self.finished:
            self.segment, self.progress = 0, 0.0
            self.x, self.y = self.path[0]
            return
        self.progress += distance
        while self.progress >= self.lengths[self.segment]:
            self.progress -= self.lengths[self.segment]
            self.segment = (self.segment + 1) % len(self.path)
        (ax, ay), (bx, by) = self.path[self.segment], self.path[(self.segment + 1) % len(self.path)]
        t = self.progress / self.lengths[self.segment]
        self.x, self.y = ax + (bx - ax) * t, ay + (by - ay) * t

    def lane_heading(self):
        (ax, ay), (bx, by) = self.path[self.segment], self.path[(self.segment + 1) % len(self.path)]
        # Degrees clockwise from north, matching Car.heading.
        heading = math.degrees(math.atan2(bx - ax, -(by - ay)))
        if self.segment in self.reverse_segments:
            heading += 180
        return heading % 360

    def forward(self):
        angle = math.radians(self.heading)
        return math.sin(angle), -math.cos(angle)

    def record(self, ahead=0.0):
        fx, fy = self.forward()
        width, height = self.solid
        return [self.x + fx * ahead, self.y + fy * ahead, 255, 255, 255, 255, 0,
                width, height, -self.heading]

    def sprite(self):
        width, height = self.solid
        return Sprite("vehicle-atlas", self.name, self.x, self.y, self.size, self.size,
                      -self.heading, width, height)


class Traffic:
    def __init__(self, world, seed: int):
        self.world = world
        rng = random.Random(seed * 31 + 404)
        self.cars: list[TrafficCar] = []
        self._city_loops(rng, loops=10, per_loop=3)
        self._road(rng, SNOW_ROUTE, SNOW_CARS, cars=4, speed=(100, 120))
        for route, cars in zip(DIRT_ROUTES, (4, 1, 1)):
            self._road(rng, route, RURAL_CARS, cars=cars, speed=(120, 140))
        for region in ("jungle", "desert"):
            self._offroad(rng, region, cars=3)
        # Added after the original traffic so its routes and spacing stay unchanged.
        for name, where in PAIRS:
            if where == "city":
                self._city_loops(rng, loops=1, per_loop=2, names=(name,))
            else:
                self._road(rng, SNOW_ROUTE, (name,), cars=2, speed=(100, 120))
        self._city_loops(rng, loops=EXTRA_CITY_LOOPS, per_loop=3)
        self._highway(random.Random(seed * 31 + 505))

    def _highway(self, rng):
        """A few cars around the highway loop, both ways, so it stays open for speed."""
        loop = next((r for r in getattr(self.world, "highway_roads", ()) if r.kind == "hwy"), None)
        if loop is None:
            return
        for points in (loop.points, loop.points[::-1]):     # Clockwise, then the other way.
            self._add_loop(rng, lane_path(points, HIGHWAY_LANE), CITY_CARS, HIGHWAY_CARS, HIGHWAY_SPEED)

    def _add_loop(self, rng, path, names, cars, speed):
        total = sum(math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path)))
        start = rng.uniform(0, total)
        loop_speed = rng.uniform(*speed)
        for i in range(cars):
            self.cars.append(TrafficCar(rng.choice(names), path, loop_speed,
                                        start + total * i / cars))

    def _city_loops(self, rng, loops, per_loop, names=CITY_CARS):
        # Every city sector's roads cross at local pixel (288, 288).
        def crossing(sx, sy):
            return sx * SECTOR_SIZE + 4.5 * TILE_SIZE, sy * SECTOR_SIZE + 4.5 * TILE_SIZE

        (x_lo, x_hi), (y_lo, y_hi) = CITY_SECTORS_X, CITY_SECTORS_Y
        for _ in range(loops):
            x0 = rng.randint(x_lo, x_hi - 2)
            x1 = rng.randint(x0 + 2, min(x_hi, x0 + 6))
            y0 = rng.randint(y_lo, y_hi - 2)
            y1 = rng.randint(y0 + 2, min(y_hi, y0 + 6))
            corners = [crossing(x0, y0), crossing(x1, y0), crossing(x1, y1), crossing(x0, y1)]
            if rng.random() < 0.5:
                corners.reverse()
            self._add_loop(rng, lane_path(corners, CITY_LANE), names, per_loop, (140, 180))

    def _road(self, rng, route, names, cars, speed):
        path = lane_path(there_and_back([_tile_center(t) for t in route]), RURAL_LANE)
        self._add_loop(rng, path, names, cars, speed)

    def _offroad(self, rng, region, cars):
        sector = next(s for s, name in CENTERS.items() if name == f"center_{region}")
        cx, cy = (sector[0] + 0.5) * SECTOR_SIZE, (sector[1] + 0.5) * SECTOR_SIZE
        placed = 0
        for _ in range(500):
            if placed == cars:
                break
            radius = rng.uniform(700, 1500)
            spin = rng.uniform(0, 2 * math.pi)
            points = [(cx + math.cos(spin + a) * radius * rng.uniform(0.8, 1.2),
                       cy + math.sin(spin + a) * radius * rng.uniform(0.8, 1.2))
                      for a in (i * 2 * math.pi / 5 for i in range(5))]
            # Keep the whole loop, including edge midpoints, inside the region.
            checks = points + [((ax + bx) / 2, (ay + by) / 2)
                               for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1])]
            if all(self.world.region_at(x, y) == region for x, y in checks):
                self._add_loop(rng, points, OFFROAD_CARS, 1, (85, 110))
                placed += 1

    def update(self, dt: float, player_rect: list[float], blockers=()):
        """Advance traffic; cars also stop for any extra blocker rects (a parked player car)."""
        px, py = player_rect[0], player_rect[1]
        active = [car for car in self.cars
                  if abs(car.x - px) < ACTIVE_RADIUS and abs(car.y - py) < ACTIVE_RADIUS]
        # Queues behind the player or a car ahead always hold; only an intersection
        # wait gives up after PATIENCE, so crossing traffic can never gridlock.
        stoppers = [player_rect, *blockers]
        held = {id(car) for car in active if self._must_wait(car, active, stoppers)}
        crossing = self._crossing_waits(active)
        for car in self.cars:
            if id(car) in held:
                continue
            if id(car) in crossing and car.waited < PATIENCE:
                car.waited += dt
                continue
            car.waited = 0.0
            car.advance(car.speed * dt)
            target = car.lane_heading()
            turn = (target - car.heading + 180) % 360 - 180
            step = TURN_RATE * dt
            car.heading = (car.heading + max(-step, min(step, turn))) % 360

    @staticmethod
    def _crossing_at(x, y):
        """City intersection (sector coordinates) whose box contains this point, if any."""
        road = 4.5 * TILE_SIZE
        sx, sy = round((x - road) / SECTOR_SIZE), round((y - road) / SECTOR_SIZE)
        if not (CITY_SECTORS_X[0] <= sx <= CITY_SECTORS_X[1]
                and CITY_SECTORS_Y[0] <= sy <= CITY_SECTORS_Y[1]):
            return None
        if (abs(x - (sx * SECTOR_SIZE + road)) <= CROSSING_HALF
                and abs(y - (sy * SECTOR_SIZE + road)) <= CROSSING_HALF):
            return sx, sy
        return None

    def _crossing_waits(self, active):
        """Cars about to enter an intersection that a crossing car occupies or claimed first."""
        inside, approaching = {}, {}
        for car in active:
            here = self._crossing_at(car.x, car.y)
            if here:
                inside.setdefault(here, []).append(car)
                continue
            fx, fy = car.forward()
            ahead = self._crossing_at(car.x + fx * APPROACH, car.y + fy * APPROACH)
            if ahead:
                approaching.setdefault(ahead, []).append(car)
        waits = set()
        for crossing, cars in approaching.items():
            for i, car in enumerate(cars):
                fx, fy = car.forward()
                # Cars already inside, then earlier arrivals in list order, have priority.
                for other in inside.get(crossing, []) + cars[:i]:
                    ofx, ofy = other.forward()
                    if abs(fx * ofx + fy * ofy) < 0.5:
                        waits.add(id(car))
                        break
        return waits

    def _must_wait(self, car, active, stoppers):
        for rect in stoppers:
            if math.hypot(car.x - rect[0], car.y - rect[1]) < 120:
                # Stop short of the player, but never freeze while already overlapping.
                if (check_collision(car.record(ahead=18), [rect], "rect")
                        and not check_collision(car.record(), [rect], "rect")):
                    return True
        fx, fy = car.forward()
        for other in active:
            if other is car:
                continue
            dx, dy = other.x - car.x, other.y - car.y
            ahead = dx * fx + dy * fy
            if 0 < ahead < FOLLOW_GAP and abs(dx * fy - dy * fx) < 16:
                ofx, ofy = other.forward()
                if fx * ofx + fy * ofy > 0.5:  # Only queue behind same-direction cars.
                    return True
        return False

    def nearby_obstacles(self, x: float, y: float) -> list[Sprite]:
        return [car.sprite() for car in self.cars
                if abs(car.x - x) < 145 and abs(car.y - y) < 145]

    def sprites(self, camera_x: float, camera_y: float, width: int, height: int) -> list[Sprite]:
        margin = CAR_SIZE
        return [car.sprite() for car in self.cars
                if camera_x - margin <= car.x <= camera_x + width + margin
                and camera_y - margin <= car.y <= camera_y + height + margin]
