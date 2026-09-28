"""The highway: a four-lane loop around the city with on-ramps and region exits."""

import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import highway
from car import SURFACES, Car
from collision_manager import CollisionManager
from progression import Progress
from traffic import HIGHWAY_CARS, HIGHWAY_SPEED, Traffic
from world import CITY_SECTORS_X, CITY_SECTORS_Y, SECTOR_SIZE, TILE_SIZE, World


MANIFEST = json.loads((PROJECT_ROOT / "assets" / "atlas-manifest.json").read_text())["atlases"]


class HighwayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()
        cls.roads = {}
        for road in cls.world.highway_roads:
            cls.roads.setdefault(road.kind, []).append(road)
        cls.loop = cls.roads["hwy"][0]

    def test_loop_circles_the_city_through_all_four_outer_regions(self):
        w, loop = self.world, self.loop
        self.assertTrue(loop.closed)
        city = (CITY_SECTORS_X[0] * SECTOR_SIZE, CITY_SECTORS_Y[0] * SECTOR_SIZE,
                (CITY_SECTORS_X[1] + 1) * SECTOR_SIZE, (CITY_SECTORS_Y[1] + 1) * SECTOR_SIZE)
        regions = set()
        for x, y in loop.points:
            self.assertFalse(city[0] <= x <= city[2] and city[1] <= y <= city[3])   # Outside it.
            regions.add(w.region_at(x, y))
        self.assertEqual(regions, {"jungle", "desert", "snow", "rural"})    # No beach or sea.
        self.assertEqual(set(self.roads), {"hwy", "ramp"})                # No made-up exits.

    def test_mostly_straight_with_smooth_curves(self):
        pts = self.loop.points + self.loop.points[:1]
        headings = [math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) for a, b in zip(pts, pts[1:])]
        turns = [abs((b - a + 180) % 360 - 180) for a, b in zip(headings, headings[1:] + headings[:1])]
        self.assertLess(max(turns), 4.0)                          # No kinks anywhere.
        self.assertGreater(sum(t < 0.01 for t in turns) / len(turns), 0.7)   # Mostly straight.

    def test_asphalt_is_city_road(self):
        w = self.world
        x, y = self.loop.points[5]
        self.assertEqual(w.surface_at(x, y), "city")
        self.assertTrue(w.on_highway(x, y))
        self.assertNotEqual(w.region_at(x, y), "city")
        progress = Progress()
        progress.levels["city"] = 5
        self.assertEqual(progress.speed_scale(w.surface_at(x, y)), progress.speed_scale("city"))
        off = (x, y - 400)
        self.assertEqual(w.surface_at(*off), w.region_at(*off))

    def test_asphalt_is_never_ice_where_the_snow_road_crosses_under(self):
        w = self.world
        crossings = [(x, y) for x, y in self.loop.points
                     if (int(x // TILE_SIZE), int(y // TILE_SIZE)) in w.snow_roads]
        self.assertTrue(crossings)
        for x, y in crossings:
            self.assertFalse(w.is_ice(x, y))

    def crossings(self, route):
        """Runs of a route's samples lying on the highway: (middle sample, its segment)."""
        pts = [((x + 0.5) * TILE_SIZE, (y + 0.5) * TILE_SIZE) for x, y in route]
        runs, inside = [], False
        for a, b in zip(pts, pts[1:]):
            n = max(1, int(math.dist(a, b) // 8))
            for i in range(n):
                p = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                on = self.world.roads.road_at(*p) == "hwy"
                if on and not inside:
                    runs.append([])
                if on:
                    runs[-1].append((p, (a, b)))
                inside = on
        return [run[len(run) // 2] for run in runs], pts

    def test_region_roads_cross_once_square_on_away_from_their_bends(self):
        from world import DIRT_ROUTES, SNOW_ROUTE
        for route in (SNOW_ROUTE, DIRT_ROUTES[0]):
            found, waypoints = self.crossings(route)
            self.assertEqual(len(found), 1)                             # Exactly one crossing,
            (x, y), (a, b) = found[0]
            nearest = min(self.loop.points, key=lambda q: math.dist(q, (x, y)))
            i = self.loop.points.index(nearest)
            ahead = self.loop.points[(i + 1) % len(self.loop.points)]
            loop_dir = (ahead[0] - nearest[0], ahead[1] - nearest[1])
            road_dir = (b[0] - a[0], b[1] - a[1])
            dot = abs(loop_dir[0] * road_dir[0] + loop_dir[1] * road_dir[1]) / (
                math.hypot(*loop_dir) * math.hypot(*road_dir))
            self.assertLess(dot, 0.01)                                  # square-on (a straight),
            self.assertGreater(min(math.dist((x, y), v) for v in waypoints), 3 * TILE_SIZE)  # off bends.
        for branch in DIRT_ROUTES[1:]:
            self.assertEqual(self.crossings(branch)[0], [])            # Branches stay clear.

    def test_loop_never_runs_along_or_over_a_road(self):
        w = self.world
        tiles = w.snow_roads | w.dirt_roads
        clearance = highway.HALF["hwy"] + 15 + 48
        crossing_tiles = set()
        from world import DIRT_ROUTES, SNOW_ROUTE
        for route in (SNOW_ROUTE, DIRT_ROUTES[0]):
            (x, y), _ = self.crossings(route)[0][0]
            crossing_tiles |= {(int(x // TILE_SIZE) + dx, int(y // TILE_SIZE) + dy)
                               for dx in range(-3, 4) for dy in range(-3, 4)}
        for x, y in self.loop.points:
            for tx, ty in tiles - crossing_tiles:
                self.assertGreater(math.dist((x, y), ((tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE)),
                                   clearance, (tx, ty))

    def test_givers_and_road_markers_keep_clear(self):
        from missions import Missions
        w = self.world
        for giver in Missions(w, w.seed).givers:
            self.assertFalse(w.roads.near(giver.x, giver.y, 60), giver.id)
        for sector in sorted(w.roads.sectors()):
            for sprite in w.sector(*sector):
                if sprite.name == "snow_marker":
                    self.assertFalse(w.roads.near(sprite.x, sprite.y, 40))

    def test_keeps_clear_of_the_racing_centers(self):
        from world import CENTERS
        for sector in CENTERS:
            center = self.world.center_position(*sector)
            self.assertGreater(min(math.dist(center, q) for q in self.loop.points), 20 * TILE_SIZE)

    def test_on_ramps_continue_city_roads_to_the_loop(self):
        w = self.world
        long_ramps = [r for r in self.roads["ramp"] if len(r.points) > 10]
        self.assertEqual(len(long_ramps), 4)
        for ramp in long_ramps:
            (ax, ay), (bx, by) = ramp.points[0], ramp.points[-1]
            dx, dy = bx - ax, by - ay
            inside = (ax - math.copysign(32, dx) if dx else ax, ay - math.copysign(32, dy) if dy else ay)
            self.assertEqual(w._road_style(int(inside[0] // TILE_SIZE), int(inside[1] // TILE_SIZE)), "city")
            self.assertEqual(w.roads.road_at(bx, by), "hwy")

    def test_nothing_solid_on_the_road_and_every_piece_exists(self):
        w = self.world
        for sector in sorted(w.roads.sectors()):
            for sprite in w.sector(*sector):
                self.assertIn(sprite.name, MANIFEST[sprite.atlas]["sprites"])
                if sprite.solid_width and sprite.atlas != "highway-atlas":
                    self.assertIsNone(w.roads.road_at(sprite.x, sprite.y), (sector, sprite.name))
        collisions = CollisionManager(None, w)
        for road in w.highway_roads:                     # Every piece, loop and ramps.
            pts = road.points
            for i, (x, y) in enumerate(pts):
                nxt = pts[(i + 1) % len(pts)] if road.closed or i + 1 < len(pts) else pts[i - 1]
                heading = math.degrees(math.atan2(nxt[0] - x, -(nxt[1] - y)))
                self.assertTrue(collisions.can_move(Car(x=x, y=y, heading=heading).collision_record()),
                                (road.kind, x, y))

    def test_only_a_few_fast_cars_and_they_stay_on_it(self):
        traffic = Traffic(self.world, self.world.seed)
        cars = [c for c in traffic.cars if HIGHWAY_SPEED[0] <= c.speed <= HIGHWAY_SPEED[1]
                and len(c.path) >= len(self.loop.points)]
        self.assertEqual(len(cars), 2 * HIGHWAY_CARS)
        for _ in range(600):
            traffic.update(1 / 30, [-9999, -9999, 0, 0, 0, 0, 0, 1, 1, 0])
            for car in cars:
                self.assertEqual(self.world.roads.road_at(car.x, car.y), "hwy")


if __name__ == "__main__":
    unittest.main()
