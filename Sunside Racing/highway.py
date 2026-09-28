"""The highway: a four-lane loop around the city, with an on-ramp from each side of the
city. Its exits are the region roads it crosses.

Geometry is in world pixels. The loop is a rounded rectangle (LOOP_SECTORS) with long
straights and CORNER_RADIUS curves. Its size was chosen so that it never runs along or
over an existing road: the snow road and the rural road (which leave the city's corners
for their racing centers) each cross it exactly once, square-on, on a straight, away from
their bends. Those crossings are the exits; the jungle and desert have no roads, so no
exits. It also keeps clear of the racing centers and the coast. The on-ramps continue the
middle city road on each side out to the loop.

Roads are drawn as short pieces laid along each path (smooth curves, not grid tiles),
and road_at() answers what road, if any, is under a point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


LOOP_SECTORS = (14, 21, 39, 44)   # Centerline x0, y0, x1, y1, in sectors.
CORNER_RADIUS = 1024          # px.
STEP = 64                     # px between road pieces (one tile), each PIECE_LENGTH long.
PIECE_LENGTH = 72             # A little overlap keeps curves closed.
HALF = {"hwy": 52, "ramp": 21}                # Half the drivable width.
DRAW_WIDTH = {"hwy": 128, "ramp": 64}
ASPHALT = ("hwy", "ramp")     # Count as city road: city grip, top speed, and mastery.
INDEX_STEP = 16               # px between the samples road_at() checks.
LANE = 40                     # Traffic: px from the median to the lane highway cars drive.


@dataclass
class Road:
    kind: str                 # hwy or ramp.
    points: list              # Centerline, STEP apart.
    closed: bool = False


def _line(a, b, step=STEP):
    length = math.dist(a, b)
    n = max(1, round(length / step))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]


def _arc(center, radius, a0, a1, step=STEP):
    """Points from angle a0 to a1 (radians, screen: y down), about step apart."""
    n = max(1, math.ceil(abs(a1 - a0) * radius / step))
    return [(center[0] + radius * math.cos(a0 + (a1 - a0) * i / n),
             center[1] + radius * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n)]


def loop_bounds(sector_size):
    """Centerline rectangle (x0, y0, x1, y1), px."""
    return tuple(s * sector_size for s in LOOP_SECTORS)


def loop_points(x0, y0, x1, y1, r=CORNER_RADIUS):
    """The loop's centerline, clockwise on screen: straights and rounded corners."""
    half_pi = math.pi / 2
    return (_line((x0 + r, y0), (x1 - r, y0)) + _arc((x1 - r, y0 + r), r, -half_pi, 0)
            + _line((x1, y0 + r), (x1, y1 - r)) + _arc((x1 - r, y1 - r), r, 0, half_pi)
            + _line((x1 - r, y1), (x0 + r, y1)) + _arc((x0 + r, y1 - r), r, half_pi, math.pi)
            + _line((x0, y1 - r), (x0, y0 + r)) + _arc((x0 + r, y0 + r), r, math.pi, 1.5 * math.pi))


def build(world) -> list[Road]:
    """The on-ramps, then the loop (so its pieces draw over the ramps where they join)."""
    from world import CITY_SECTORS_X, CITY_SECTORS_Y, SECTOR_SIZE, TILE_SIZE

    x0, y0, x1, y1 = loop_bounds(SECTOR_SIZE)
    mid_x = ((CITY_SECTORS_X[0] + CITY_SECTORS_X[1]) // 2 * 8 + 4.5) * TILE_SIZE
    mid_y = ((CITY_SECTORS_Y[0] + CITY_SECTORS_Y[1]) // 2 * 8 + 4.5) * TILE_SIZE
    edges = (CITY_SECTORS_X[0] * SECTOR_SIZE, CITY_SECTORS_Y[0] * SECTOR_SIZE,
             (CITY_SECTORS_X[1] + 1) * SECTOR_SIZE, (CITY_SECTORS_Y[1] + 1) * SECTOR_SIZE)
    roads = [Road("ramp", _line(a, b) + [b]) for a, b in (
        ((mid_x, edges[1]), (mid_x, y0)), ((mid_x, edges[3]), (mid_x, y1)),
        ((edges[0], mid_y), (x0, mid_y)), ((edges[2], mid_y), (x1, mid_y)))]
    roads.append(Road("hwy", loop_points(x0, y0, x1, y1), closed=True))
    return roads


def pieces(road: Road):
    """(sprite name, x, y, width, length, GL rotation) for each piece along the road."""
    pts = road.points + (road.points[:1] if road.closed else [])
    for a, b in zip(pts, pts[1:]):
        heading = math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1])))
        length = max(PIECE_LENGTH, math.dist(a, b) + 8)
        yield (road.kind, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, DRAW_WIDTH[road.kind], length, -heading)


class RoadIndex:
    """Samples along every road, binned by sector, for "what road is here?"."""

    def __init__(self, roads, sector_size):
        self.sector_size = sector_size
        self.bins: dict[tuple[int, int], list] = {}
        self.covered: set[tuple[int, int]] = set()   # Every sector the roads' width reaches.
        for road in roads:
            pts = road.points + (road.points[:1] if road.closed else [])
            for a, b in zip(pts, pts[1:]):
                n = max(1, math.ceil(math.dist(a, b) / INDEX_STEP))
                for i in range(n + 1):
                    x, y = a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n
                    key = (int(x // sector_size), int(y // sector_size))
                    self.bins.setdefault(key, []).append((x, y, road.kind))
                    reach = HALF[road.kind] + 64
                    for dx in (-reach, reach):
                        for dy in (-reach, reach):
                            self.covered.add((int((x + dx) // sector_size), int((y + dy) // sector_size)))

    def _near(self, x, y):
        sx, sy = int(x // self.sector_size), int(y // self.sector_size)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yield from self.bins.get((sx + dx, sy + dy), ())

    def road_at(self, x, y):
        """The kind of highway road under (x, y), or None (asphalt wins where they meet)."""
        kinds = {kind for px, py, kind in self._near(x, y)
                 if math.dist((px, py), (x, y)) <= HALF[kind] + INDEX_STEP / 2}
        for kind in ("hwy", "ramp"):
            if kind in kinds:
                return kind
        return None

    def near(self, x, y, margin) -> bool:
        """Within margin px of any highway road's edge (keeps scenery off the road)."""
        return any(math.dist((px, py), (x, y)) <= HALF[kind] + margin for px, py, kind in self._near(x, y))

    def sectors(self):
        """Sectors the roads (with a tile of margin) pass through: no camps or farms there."""
        return self.covered
