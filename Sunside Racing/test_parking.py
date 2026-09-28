"""Parked commuters leave their stall, lap a block, and park again."""

import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from gl_utils import check_collision
from parking import Parking
from traffic import EXTRA_CITY_LOOPS, HIGHWAY_CARS, Traffic
from world import CITY_SECTORS_X, CITY_SECTORS_Y, World

FAR_AWAY = [-9999, -9999, 0, 0, 0, 0, 0, 1, 1, 0]


class ParkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def setUp(self):
        self.traffic = Traffic(self.world, self.world.seed)
        self.parking = Parking(self.world, self.traffic, self.world.seed)
        self.commuters = [s for sy in range(CITY_SECTORS_Y[0], CITY_SECTORS_Y[1] + 1)
                          for sx in range(CITY_SECTORS_X[0], CITY_SECTORS_X[1] + 1)
                          for s in self.parking._commuters(sx, sy)]

    def test_some_parked_cars_are_commuters(self):
        self.assertGreater(len(self.commuters), 20)

    def test_trip_backs_out_and_never_crosses_buildings(self):
        for sprite in self.commuters:
            path = self.parking.trip_path(sprite)
            self.assertEqual(path[0], (sprite.x, sprite.y))
            self.assertEqual(path[1][0], sprite.x)
            self.assertGreater(path[1][1], sprite.y)  # Aisle is behind a nose-in car.
            for (ax, ay), (bx, by) in zip(path, path[1:] + path[:1]):
                steps = max(1, int(math.dist((ax, ay), (bx, by)) // 8))
                for i in range(steps + 1):
                    x, y = ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps
                    probe = [x, y, 0, 0, 0, 0, 0, 4, 4, 0]
                    solids = [s.obstacle_record() for s in self.world.nearby_obstacles(x, y)
                              if s.atlas == "structure-atlas"]
                    self.assertFalse(check_collision(probe, solids, "rect"), (sprite, x, y))

    def test_car_leaves_then_returns_to_its_stall(self):
        sprite = self.commuters[0]
        departed = returned = False
        for _ in range(3000):  # Up to 150 simulated seconds.
            self.parking.update(0.05, sprite.x, sprite.y)
            self.traffic.update(0.05, FAR_AWAY)
            if self.parking.is_away(sprite):
                departed = True
                car = self.parking.away[sprite]
                self.assertIn(car, self.traffic.cars)
            elif departed:
                returned = True
                break
        self.assertTrue(departed and returned)
        self.assertNotIn(sprite, self.parking.away)
        # Other commuters in the same lots may still be out.
        self.assertEqual(len(self.traffic.cars), 56 + 3 * EXTRA_CITY_LOOPS + 2 * HIGHWAY_CARS
                         + len(self.parking.away))
        self.assertIn(sprite, self.parking.waits)

    def test_away_stall_is_not_solid(self):
        from collision_manager import CollisionManager
        sprite = self.commuters[0]
        collisions = CollisionManager(None, self.world)
        collisions.parking = self.parking
        record = [sprite.x, sprite.y, 0, 0, 0, 0, 0, 10, 10, 0]
        self.assertIn(sprite, collisions.colliding_obstacles(record))
        self.parking._depart(sprite)
        self.assertNotIn(sprite, collisions.colliding_obstacles(record))


if __name__ == "__main__":
    unittest.main()
