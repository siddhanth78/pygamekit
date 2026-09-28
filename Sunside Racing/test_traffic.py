"""Traffic lane geometry, road adherence, and yielding checks."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from traffic import (
    HIGHWAY_CARS, HIGHWAY_SPEED,
    EXTRA_CITY_LOOPS, PAIRS, RURAL_CARS, Traffic, TrafficCar, lane_path, there_and_back,
)
from world import SECTOR_SIZE, TILE_SIZE, World


class TrafficTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_lanes_keep_to_the_right(self):
        # Clockwise square on screen: heading east along the top, the right lane is south.
        path = lane_path([(0, 0), (100, 0), (100, 100), (0, 100)], 10)
        self.assertEqual(path[0], (10.0, 10.0))
        self.assertEqual(path[1], (90.0, 10.0))

    def test_dead_end_makes_a_u_turn(self):
        path = lane_path(there_and_back([(0, 0), (0, 100)]), 7)
        self.assertEqual(len(path), 4)
        self.assertEqual({round(x) for x, _ in path}, {-7, 7})

    def test_road_traffic_stays_on_roads(self):
        traffic = Traffic(self.world, self.world.seed)
        for _ in range(300):
            traffic.update(1 / 30, [-9999, -9999, 0, 0, 0, 0, 0, 1, 1, 0])
            for car in traffic.cars:
                region = self.world.region_at(car.x, car.y)
                if self.world.on_highway(car.x, car.y):
                    continue   # Highway traffic (see test_highway).
                if region in ("city", "snow", "rural"):
                    tile = int(car.x // TILE_SIZE), int(car.y // TILE_SIZE)
                    self.assertTrue(self.world._road_style(*tile), (car.name, region, tile))
                self.assertNotEqual(region, "sea")

    def test_offroaders_stay_in_jungle_and_desert(self):
        traffic = Traffic(self.world, self.world.seed)
        offroad = [car for car in traffic.cars if car.speed < 115 and len(car.path) == 5]
        self.assertEqual(len(offroad), 6)
        for car in offroad:
            self.assertIn(self.world.region_at(car.x, car.y), ("jungle", "desert"))

    def test_car_stops_for_player_ahead_and_never_freezes_on_overlap(self):
        traffic = Traffic(self.world, self.world.seed)
        car = TrafficCar(RURAL_CARS[0], [(0, 0), (0, -1000)], 100, 0)
        traffic.cars = [car]
        blocker = [0, -50, 255, 255, 255, 255, 0, 24, 44, 0]
        traffic.update(0.1, blocker)
        self.assertEqual((car.x, car.y), (0, 0))
        overlapping = [0, -2, 255, 255, 255, 255, 0, 24, 44, 0]
        traffic.update(0.1, overlapping)
        self.assertLess(car.y, 0)

    def test_ten_extra_cars_are_five_pairs(self):
        traffic = Traffic(self.world, self.world.seed)
        extra = [car.name for car in traffic.cars[46:56]]
        self.assertEqual(sorted(extra), sorted(
            name for name, _ in PAIRS for _ in range(2)))
        # Each pair shares one path.
        for i in range(46, 56, 2):
            self.assertIs(traffic.cars[i].path, traffic.cars[i + 1].path)

    def test_city_is_busy_and_countryside_quiet(self):
        traffic = Traffic(self.world, self.world.seed)
        regions = [self.world.region_at(car.x, car.y) for car in traffic.cars
                   if not self.world.on_highway(car.x, car.y)]
        self.assertEqual(len(traffic.cars), 56 + 3 * EXTRA_CITY_LOOPS + 2 * HIGHWAY_CARS)
        self.assertGreater(regions.count("city"), 200)
        self.assertLessEqual(regions.count("jungle") + regions.count("desert"), 6)

    def test_car_yields_to_crossing_car_in_intersection(self):
        traffic = Traffic(self.world, self.world.seed)
        cx, cy = 26 * SECTOR_SIZE + 4.5 * TILE_SIZE, 31 * SECTOR_SIZE + 4.5 * TILE_SIZE
        crossing = TrafficCar("traffic_red", [(cx - 200, cy), (cx + 200, cy)], 0, 200)
        waiting = TrafficCar("traffic_blue", [(cx, cy + 200), (cx, cy - 200)], 100, 140)
        traffic.cars = [crossing, waiting]
        bystander = [cx + 800, cy + 800, 0, 0, 0, 0, 0, 1, 1, 0]  # Near enough to simulate.
        start = waiting.y
        traffic.update(0.1, bystander)
        self.assertEqual(waiting.y, start)
        crossing.advance(150)  # Crossing car clears the box.
        traffic.update(0.1, bystander)
        self.assertLess(waiting.y, start)

    def test_busy_city_never_gridlocks(self):
        traffic = Traffic(self.world, self.world.seed)
        # Player waits on the sidewalk near the busiest crossing, out of every lane.
        player = [26 * SECTOR_SIZE + 220, 31 * SECTOR_SIZE + 220, 0, 0, 0, 0, 0, 1, 1, 0]
        for _ in range(600):  # 30 simulated seconds around the busiest spot.
            traffic.update(0.05, player)
        before = {id(car): car.travelled for car in traffic.cars}
        for _ in range(200):  # Every car must make progress within 10 seconds.
            traffic.update(0.05, player)
        stuck = [car for car in traffic.cars if car.speed and car.travelled == before[id(car)]]
        self.assertEqual(stuck, [])


if __name__ == "__main__":
    unittest.main()
