"""Pedestrian rounds, doors, yielding, encampments, and calling the car."""

import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import random

from car import Car
from collision_manager import CollisionManager
from gl_utils import check_collision
from pedestrians import (
    CITY_PER_BLOCK, KEEP_SECTORS, Pedestrian, Pedestrians, build_loop, reverse_route,
)
from traffic import Traffic, TrafficCar
from walker import CALL_MIN_DISTANCE, Walker, call_spot
from world import CAMP_STYLE, CITY_SECTORS_X, CITY_SECTORS_Y, SECTOR_SIZE, World


def far_player(x=-9999.0, y=-9999.0):
    return [x, y, 0, 0, 0, 0, 0, 12, 12, 0]


class RouteTests(unittest.TestCase):
    def test_build_loop_orders_detours_along_each_edge(self):
        corners = [(0, 0), (100, 0), (100, 100), (0, 100)]
        detours = [((70, 0), [((70, -20), ("door", (1, 1)))]),
                   ((30, 0), [((30, -20), ("pause", (1, 1)))])]
        path, stops = build_loop(corners, detours)
        self.assertEqual(path[:7], [(0, 0), (30, 0), (30, -20), (30, 0), (70, 0), (70, -20), (70, 0)])
        self.assertEqual(stops, {2: ("pause", (1, 1)), 5: ("door", (1, 1))})

    def test_reverse_route_keeps_stops_on_the_same_points(self):
        path, stops = [(0, 0), (1, 0), (1, 1), (0, 1)], {2: ("door", (1, 1))}
        rpath, rstops = reverse_route(path, stops)
        self.assertEqual(rpath, [(0, 0), (0, 1), (1, 1), (1, 0)])
        self.assertEqual(rpath[next(iter(rstops))], (1, 1))

    def test_door_hides_then_reappears(self):
        person = Pedestrian("city_a", [(0, 0), (10, 0), (10, 10)], 100, 0,
                            {1: ("door", (0.5, 0.5))})
        rng = random.Random(1)
        person.update(0.2, rng, False)
        self.assertTrue(person.inside)
        self.assertEqual((person.x, person.y), (10, 0))
        for _ in range(4):
            person.update(0.2, rng, False)
        self.assertFalse(person.inside)
        self.assertTrue(person.frame().startswith("city_a_walk_"))


class WorldPeopleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def assert_path_clear(self, path, label):
        for (ax, ay), (bx, by) in zip(path, path[1:] + path[:1]):
            steps = max(1, int(math.dist((ax, ay), (bx, by)) // 6))
            for i in range(steps + 1):
                x, y = ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps
                probe = [x, y, 0, 0, 0, 0, 0, 6, 6, 0]
                solids = [s.obstacle_record() for s in self.world.nearby_obstacles(x, y)
                          if s.atlas in ("structure-atlas", "camp-atlas")]
                self.assertFalse(check_collision(probe, solids, "rect"), (label, x, y))

    def test_every_city_block_route_avoids_buildings_and_is_dense(self):
        peds = Pedestrians(self.world, self.world.seed)
        doors = 0
        for by in range(CITY_SECTORS_Y[0], CITY_SECTORS_Y[1]):
            for bx in range(CITY_SECTORS_X[0], CITY_SECTORS_X[1]):
                group = peds._city_block(bx, by, random.Random(0))
                self.assertEqual(len(group), CITY_PER_BLOCK)
                for route in {id(p.path): p.path for p in group}.values():
                    self.assert_path_clear(route, ("block", bx, by))
                doors += sum(1 for k, _ in group[0].stops.values() if k == "door")
        self.assertGreater(doors, 200)

    def test_camps_farms_and_plazas_avoid_solids(self):
        peds = Pedestrians(self.world, self.world.seed)
        self.assertEqual(sorted(self.world.camps.values()).count("desert"), 5)
        self.assertEqual(sorted(self.world.camps.values()).count("jungle"), 5)
        for (sx, sy), region in self.world.camps.items():
            self.assertEqual(self.world.region(sx, sy), region)
            group = peds._camp(sx, sy, random.Random(0))
            self.assertEqual(sum(p.still for p in group), 2)
            for p in group:
                if not p.still:
                    self.assert_path_clear(p.path, ("camp", sx, sy))
            names = {s.name for s in self.world.sector(sx, sy) if s.atlas == "camp-atlas"}
            self.assertIn(CAMP_STYLE[region][0], names)
            self.assertIn("campfire", names)
        farms = 0
        for sy in range(64):
            for sx in range(64):
                if self.world.region(sx, sy) == "rural":
                    for p in peds._farm(sx, sy, random.Random(0)):
                        farms += 1
                        self.assert_path_clear(p.path, ("farm", sx, sy))
        self.assertGreater(farms, 50)

    def test_road_walkers_stay_out_of_car_lanes(self):
        peds = Pedestrians(self.world, self.world.seed)
        self.assertEqual(len(peds.walkers), 16)
        for _ in range(200):
            peds.update(0.1, far_player())
        self.assertEqual(peds.road_blockers(), [])  # No groups near a far-away player.

    def test_groups_follow_the_player_and_are_dropped_behind(self):
        peds = Pedestrians(self.world, self.world.seed)
        x, y = 26 * SECTOR_SIZE + 200, 31 * SECTOR_SIZE + 200
        peds.update(0.1, far_player(x, y))
        city_blocks = [k for k in peds.groups if k[0] == "block"]
        self.assertEqual(len(city_blocks), 25)
        peds.update(0.1, far_player(x + (KEEP_SECTORS + 3) * SECTOR_SIZE, y))
        self.assertFalse(any(abs(k[1] - 26) > KEEP_SECTORS + 3 + KEEP_SECTORS for k in peds.groups))
        self.assertNotIn(("block", 24, 29), peds.groups)


class PlayerInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_waits_for_player_ahead_but_never_traps_them(self):
        peds = Pedestrians(self.world, self.world.seed)
        person = Pedestrian("city_a", [(0, 0), (0, -500)], 40, 0)
        peds.walkers, peds._scanned_sector = [person], (-1, -1)
        peds.update(0.1, [0, -30, 0, 0, 0, 0, 0, 12, 12, 0])  # Standing just ahead.
        self.assertEqual(person.y, 0)
        self.assertEqual(len(peds.nearby_obstacles(0, -30)), 1)
        peds.update(0.1, [0, -3, 0, 0, 0, 0, 0, 12, 12, 0])   # Already overlapping.
        self.assertLess(person.y, 0)
        self.assertEqual(peds.nearby_obstacles(0, -3), [])

    def test_traffic_stops_for_someone_on_a_crosswalk(self):
        traffic = Traffic(self.world, self.world.seed)
        car = TrafficCar("traffic_red", [(0, 0), (0, -1000)], 100, 0)
        traffic.cars = [car]
        crossing = [0, -40, 0, 0, 0, 0, 0, 14, 14, 0]  # Just past the car's stopping reach.
        traffic.update(0.1, far_player(300, 300), [crossing])
        self.assertEqual((car.x, car.y), (0, 0))

    def test_called_car_lands_beside_the_walker(self):
        collisions = CollisionManager(None, self.world)
        car = Car()
        walker = Walker(car.x + 700, car.y, heading=90)  # Down the road, facing east.
        spot = call_spot(walker, car, collisions)
        self.assertIsNotNone(spot)
        self.assertLess(math.dist(spot, (walker.x, walker.y)), 120)
        self.assertEqual(car.heading, 90)
        car.x, car.y = spot
        self.assertFalse(check_collision(walker.collision_record(), [car.collision_record()], "rect"))
        self.assertTrue(collisions.can_move(car.collision_record()))
        near = Walker(car.x + CALL_MIN_DISTANCE - 1, car.y)
        self.assertIsNone(call_spot(near, car, collisions))



class BeachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        cls.world = World()
        cls.peds = Pedestrians(cls.world, cls.world.seed)
        cls.peds._player = (-9999.0, -9999.0)
        cls.sectors = [(sx, sy) for sy in range(64) for sx in range(64)
                       if cls.world.region(sx, sy) == "beach" and cls.world._landmass(sx, sy) == "mainland"]
        cls.groups = {s: cls.peds._beach(*s, cls.peds._group_rng(("beach", *s))) for s in cls.sectors}
        cls.manifest = json.loads((PROJECT_ROOT / "assets" / "atlas-manifest.json").read_text())["atlases"]

    def test_quiet_like_the_countryside_and_mostly_resting(self):
        people = [p for group in self.groups.values() for p in group]
        rural = [(sx, sy) for sy in range(64) for sx in range(64) if self.world.region(sx, sy) == "rural"]
        rural_people = sum(len(self.peds._farm(sx, sy, self.peds._group_rng(("farm", sx, sy))))
                           for sx, sy in rural) + len(rural and self.peds.walkers) // 2
        per_beach, per_rural = len(people) / len(self.sectors), rural_people / len(rural)
        self.assertLess(per_beach, 2 * per_rural)
        self.assertGreater(per_beach, 0.5 * per_rural)
        resting = [p for p in people if p.still]
        self.assertGreater(len(resting) / len(people), 0.65)
        self.assertEqual({p.pose for p in resting}, {"lounge", "lie"})
        self.assertTrue(all(p.kind.startswith("beach_") for p in people))

    def test_gear_and_people_stay_on_open_sand(self):
        world = self.world
        for (sx, sy), group in self.groups.items():
            props = self.peds.props[("beach", sx, sy)]
            scenery = [s for s in world.sector(sx, sy) if s.solid_width]
            for thing in props + [p.sprite() for p in group]:
                self.assertEqual(world.region_at(thing.x, thing.y), "beach")
                for s in scenery:
                    self.assertGreater(math.dist((thing.x, thing.y), (s.x, s.y)), 30, (thing.name, s.name))
            for dock in world.docks:
                for thing in props:
                    self.assertGreater(math.dist((thing.x, thing.y), dock.shore()), 200)

    def test_everyone_faces_the_sea_and_every_sprite_exists(self):
        for (sx, sy), group in self.groups.items():
            heading = self.peds._sea_heading(sx, sy)
            dx, dy = math.sin(math.radians(heading)), -math.cos(math.radians(heading))
            self.assertEqual(self.world._landmass(sx + round(dx), sy + round(dy)), "sea")
            for person in group:
                if person.still:
                    self.assertEqual(person.heading, heading)
            for sprite in self.peds.props[("beach", sx, sy)] + [p.sprite() for p in group]:
                self.assertIn(sprite.name, self.manifest[sprite.atlas]["sprites"], sprite.name)

    def test_umbrellas_shade_heads_but_leave_legs_showing(self):
        for (sx, sy), group in self.groups.items():
            canopies = [s for s in self.peds.props[("beach", sx, sy)] if s.atlas == "canopy-atlas"]
            heading = self.peds._sea_heading(sx, sy)
            fx, fy = math.sin(math.radians(heading)), -math.cos(math.radians(heading))
            for canopy in canopies:
                under = [p for p in group if math.dist((p.x, p.y), (canopy.x, canopy.y)) < 40]
                for person in under:
                    feet = (person.x + fx * 14, person.y + fy * 14)
                    self.assertGreater(math.dist(feet, (canopy.x, canopy.y)), canopy.width / 2)

    def test_loungers_and_umbrella_poles_are_solid_mats_are_not(self):
        peds = Pedestrians(self.world, self.world.seed)
        sx, sy = next(s for s, g in self.groups.items()
                      if any("chair" in p.name for p in self.peds.props[("beach", *s)]))
        chair = next(p for p in self.peds.props[("beach", sx, sy)] if "chair" in p.name)
        peds.update(1 / 60, far_player(chair.x + 400, chair.y))     # Load the beach nearby.
        names = {o.name for o in peds.nearby_obstacles(chair.x, chair.y)}
        self.assertIn(chair.name, names)
        self.assertFalse(any("mat" in n or n == "umbrella_shade" for n in names))

    def test_never_spawns_on_the_player(self):
        sx, sy, spot = next((s[0], s[1], p) for s, g in self.groups.items()
                            for p in self.peds.props[("beach", *s)])
        peds = Pedestrians(self.world, self.world.seed)
        peds.update(1 / 60, far_player(spot.x, spot.y))
        for props in peds.props.values():
            for thing in props:
                self.assertGreater(math.dist((thing.x, thing.y), (spot.x, spot.y)), 60)


if __name__ == "__main__":
    unittest.main()
