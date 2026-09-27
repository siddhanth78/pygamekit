"""The player's house and parking lot, the interior level, and the arcade's Lane Dodge."""

import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from arcade import CAR_Y, LANES, LaneDodge
from car import Car
from collision_manager import CollisionManager
from home import HomeInterior
from walker import Walker
from world import HOME_DOOR, HOME_HOUSE, HOME_PARK, HOME_SECTOR, TILE_SIZE, World


MANIFEST = json.loads((PROJECT_ROOT / "assets" / "atlas-manifest.json").read_text())["atlases"]


class HouseInTheCityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()
        cls.collisions = CollisionManager(None, cls.world)
        cls.sprites = cls.world.sector(*HOME_SECTOR)

    def test_house_and_its_two_stall_lot_replace_the_corner_lot(self):
        houses = [s for s in self.sprites if s.name == "player_house"]
        self.assertEqual([(s.x, s.y) for s in houses], [HOME_HOUSE])
        self.assertEqual(self.world.region(*HOME_SECTOR), "city")
        near = [s for s in self.sprites if s.atlas == "structure-atlas" and s.name != "player_house"
                and math.dist((s.x, s.y), HOME_HOUSE) < 150]
        self.assertEqual(near, [])                        # No city building on the lot.
        lot = [s for s in self.sprites if s.name == "home_lot"
               and math.dist((s.x, s.y), HOME_PARK[:2]) < 64]
        self.assertEqual(len(lot), 1)                     # One tile: two stalls.
        self.assertEqual(lot[0].x - TILE_SIZE / 2 + 20, HOME_PARK[0])   # West stall, like public lots.
        self.assertFalse(any(s.atlas == "vehicle-atlas" and math.dist((s.x, s.y), HOME_PARK[:2]) < 100
                             for s in self.sprites))      # Nobody else parks there.
        for sprite in self.sprites:
            self.assertIn(sprite.name, MANIFEST[sprite.atlas]["sprites"])

    def test_car_parks_clear_and_drives_out_through_the_aisle(self):
        from walker import exit_spot
        x, y, heading = HOME_PARK
        car = Car(x=x, y=y, heading=heading)
        self.assertTrue(self.collisions.can_move(car.collision_record()))
        self.assertEqual(heading, 0.0)                         # Facing up: press Up to leave.
        aisle_y = (int(y // TILE_SIZE) + 0.15) * TILE_SIZE     # The lot's upper half.
        for step in range(0, int(y - aisle_y) + 1, 4):        # Forward (up) into the aisle,
            self.assertTrue(self.collisions.can_move(Car(x=x, y=y - step, heading=0).collision_record()))
        road_middle = (int(x // TILE_SIZE) + 1.5) * TILE_SIZE
        self.assertTrue(self.world._road_style(int(road_middle // TILE_SIZE), int(aisle_y // TILE_SIZE)))
        for step in range(0, int(road_middle - x) + 1, 8):    # then east onto the road.
            self.assertTrue(self.collisions.can_move(Car(x=x + step, y=aisle_y, heading=90).collision_record()))
        out = exit_spot(car, self.collisions)                 # Getting out: into the empty stall,
        self.assertIsNotNone(out)
        self.assertFalse(self.world._road_style(int(out[0] // TILE_SIZE), int(out[1] // TILE_SIZE)))

    def test_door_and_car_each_have_their_own_e(self):
        from walker import ENTER_RANGE
        self.assertGreater(math.dist(HOME_DOOR, HOME_PARK[:2]), ENTER_RANGE)   # At the door: go in.

    def test_front_door_is_reachable_on_foot_beside_the_car(self):
        self.collisions.fixed = [Car(x=HOME_PARK[0], y=HOME_PARK[1], heading=HOME_PARK[2]).obstacle()]
        try:
            self.assertTrue(self.collisions.can_walk(Walker(*HOME_DOOR).collision_record()))
        finally:
            self.collisions.fixed = []


class InteriorTests(unittest.TestCase):
    def setUp(self):
        self.home = HomeInterior()
        self.collisions = CollisionManager(None, self.home)

    def test_every_spot_is_reachable_on_foot_and_leaves_room_to_stand(self):
        for spot in self.home.spots:
            walker = Walker(spot.x, spot.y)
            self.assertTrue(self.collisions.can_walk(walker.collision_record()), spot)
            self.assertIs(self.home.spot_near(spot.x, spot.y), spot)

    def test_walls_keep_the_player_in(self):
        walker = Walker(*self.home.entry)
        for _ in range(300):
            walker.update(1 / 60, 0, -1, True, self.collisions)   # Push into the top wall.
        self.assertGreater(walker.y, TILE_SIZE)
        walker = Walker(*self.home.entry)
        for _ in range(600):
            walker.update(1 / 60, -1, 1, True, self.collisions)
        self.assertGreater(walker.x, TILE_SIZE)

    def test_bedroom_is_reachable_through_the_doorway(self):
        walker = Walker(*self.home.entry)
        # Down to the doorway row, then east through it.
        for dx, dy, frames in ((1, 0, 50), (0, 1, 120), (1, 0, 400)):
            for _ in range(frames):
                walker.update(1 / 60, dx, dy, True, self.collisions)
        self.assertGreater(walker.x, 9 * TILE_SIZE)

    def test_lamps_toggle_and_every_sprite_exists(self):
        self.assertTrue(self.home.lamps["living"])
        self.assertFalse(self.home.toggle_lamp("living"))
        names = {s.name for s in self.home.visible_sprites()}
        self.assertIn("lamp_off", names)
        for sprite in self.home.visible_sprites():
            self.assertIn(sprite.name, MANIFEST[sprite.atlas]["sprites"])
        self.assertTrue({"couch", "tv", "arcade", "bed", "dining_table", "chair"} <= names)
        self.assertEqual({"floor_wood", "floor_bedroom", "wall", "wall_door", "wall_window"},
                         {s.name for s in self.home.visible_sprites() if s.atlas == "terrain-atlas"})


class LaneDodgeTests(unittest.TestCase):
    def test_steering_stays_on_the_road(self):
        game = LaneDodge(1)
        for _ in range(5):
            game.steer(-1)
        self.assertEqual(game.lane, 0)
        for _ in range(5):
            game.steer(1)
        self.assertEqual(game.lane, LANES - 1)

    def test_dodging_scores_and_speeds_up_and_a_hit_ends_the_run(self):
        game = LaneDodge(3)
        start_speed = game.speed
        for _ in range(60 * 30):
            # Careful play: step aside from a cone about to reach the car.
            danger = {c[0] for c in game.cones if CAR_Y - 110 < c[1] < CAR_Y + 40}
            safe = [lane for lane in range(LANES) if lane not in danger]
            if game.lane in danger and safe:
                game.steer(1 if min(safe, key=lambda s: abs(s - game.lane)) > game.lane else -1)
            game.update(1 / 60)
            if game.over:
                break
        self.assertGreater(game.score, 10)
        self.assertGreater(game.speed, start_speed)
        crash = LaneDodge(5)
        for _ in range(60 * 60):
            crash.update(1 / 60)                           # Never steer: hit eventually.
            if crash.over:
                break
        self.assertTrue(crash.over)
        score = crash.score
        crash.update(1.0)
        self.assertEqual(crash.score, score)               # Frozen after the crash.

    def test_always_time_to_cross_all_three_lanes_between_rows(self):
        from arcade import CLEAR_TIME, HIT_REACH
        game = LaneDodge(11)
        rows, t = [], 0.0
        seen = set()
        for frame in range(60 * 90):
            game.update(1 / 60)
            game.over = False                              # Keep going to measure the rows.
            t += 1 / 60
            for cone in game.cones:
                key = id(cone)
                # When each row's cones first reach, then leave, the car's danger zone.
                if key not in seen and cone[1] > CAR_Y - HIT_REACH:
                    seen.add(key)
                    rows.append((t, cone[1]))
        arrivals = sorted({round(t, 3) for t, _ in rows})
        self.assertGreater(game.speed, 500)                # Fast by the end.
        danger = 2 * HIT_REACH
        for (a, b) in zip(arrivals, arrivals[1:]):
            # A row is in the way for danger / speed seconds after it arrives.
            gap = b - a - danger / game.speed
            self.assertGreater(gap, CLEAR_TIME[0] * 0.8)    # Two taps fit easily.

    def test_a_lane_is_always_open(self):
        game = LaneDodge(9)
        for _ in range(60 * 20):
            game.update(1 / 60)
            game.over = False                              # Keep going to watch the rows.
            rows = {}
            for lane, y in game.cones:
                rows.setdefault(round(y), set()).add(lane)
            self.assertTrue(all(len(lanes) < LANES for lanes in rows.values()))


if __name__ == "__main__":
    unittest.main()
