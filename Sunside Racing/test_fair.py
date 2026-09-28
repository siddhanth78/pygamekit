"""The Snow Fair: booth games, prizes, the site, the fairground, rides, and the F1 car."""

import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from arcade import PitStop
from car import Car
from collision_manager import CollisionManager
from fair import (BOOTHS, GAME_IDS, RIDES, FairInterior, FairOutside, FairState, exterior_sprites)
from fair_games import BAND_PAY, GAMES, Balloons, Darts, Hammer, RingToss, SkeeBall, WhackAMole
from missions import Missions
from walker import Walker
from world import CENTERS, SECTOR_SIZE, TILE_SIZE, World


def advance_until(game, test, step=1 / 240, limit=30.0):
    t = 0.0
    while not test() and t < limit:
        game.update(step)
        t += step
    return test()


def perfect(game_id):
    """Play one attempt flawlessly (as a very good player could)."""
    game = GAMES[game_id](7)
    if isinstance(game, Darts):
        for _ in range(3):
            assert advance_until(game, lambda: math.dist(game.aim(), Darts.CENTER) <= 6)
            game.press()
    elif isinstance(game, Hammer):
        for _ in range(3):
            assert advance_until(game, lambda: game.meter() >= 99)
            game.press()
    elif isinstance(game, Balloons):
        for _ in range(10):
            game.update(0.3)
            target = next(b for b in game.balloons if 20 < game.balloon_xy(b)[0] < 460)
            game.point(*game.balloon_xy(target))
            game.press()
    elif isinstance(game, RingToss):
        for _ in range(5):
            assert advance_until(game, lambda: abs(game.marker() - 240) <= 2)
            game.press()
            assert advance_until(game, lambda: abs(game.marker() - 70) <= 2)
            game.press()
    elif isinstance(game, SkeeBall):
        for _ in range(6):
            assert advance_until(game, lambda: abs(game.marker() - 70) <= 3)
            game.press()
            assert advance_until(game, lambda: game.marker() >= 97)
            game.press()
    elif isinstance(game, WhackAMole):
        while not game.over:
            up = game.moles_up()
            if up:
                game.cursor = next(iter(up.values()))
                game.press()
            game.update(1 / 60)
    return game


class GameTests(unittest.TestCase):
    def test_a_flawless_run_is_platinum_in_every_game(self):
        for game_id, cls in GAMES.items():
            game = perfect(game_id)
            self.assertTrue(game.over, game_id)
            self.assertEqual(game.score, cls.thresholds[-1], game_id)
            self.assertEqual(cls.band(game.score), 4, game_id)

    def test_platinum_is_the_maximum_and_needs_every_try(self):
        self.assertEqual(Darts.thresholds[-1], 3 * 50)
        self.assertEqual(Hammer.thresholds[-1], 3 * 100)
        self.assertEqual(Balloons.thresholds[-1], 10)
        self.assertEqual(RingToss.thresholds[-1], 5 * 30)
        self.assertEqual(SkeeBall.thresholds[-1], 6 * 100)
        self.assertEqual(WhackAMole.thresholds[-1], WhackAMole.MOLES)
        self.assertEqual(BAND_PAY, {"bronze": 10, "silver": 25, "gold": 50, "platinum": 150})

    def test_one_slip_costs_platinum(self):
        game = Darts(1)
        for _ in range(2):
            advance_until(game, lambda: math.dist(game.aim(), Darts.CENTER) <= 6)
            game.press()
        advance_until(game, lambda: math.dist(game.aim(), Darts.CENTER) > 40)
        game.press()
        self.assertTrue(game.over)
        self.assertLess(game.score, 150)
        self.assertEqual(Darts.band(game.score), 3)

    def test_bullseye_windows_are_short_but_fair(self):
        game = Darts(3)
        for dart in range(3):
            game.used, inside, windows, t = dart, False, [], 0.0
            while t < 8:
                game.clock = t
                hit = math.dist(game.aim(), Darts.CENTER) <= Darts.RINGS[0][0]
                if hit and not inside:
                    windows.append(t)
                if not hit and inside:
                    windows[-1] = t - windows[-1]
                inside, t = hit, t + 0.001
            self.assertTrue(all(0.045 < w < 0.09 for w in windows[:-1]), windows)

    def test_whack_a_mole_moves_and_clicks(self):
        game = WhackAMole(2)
        game.move(1, 1)
        self.assertEqual(game.cursor, 8)
        game.move(-5, -5)
        self.assertEqual(game.cursor, 0)
        game.point(*WhackAMole.hole_xy(4))
        self.assertEqual(game.cursor, 4)


class StateTests(unittest.TestCase):
    def test_bands_pay_once_platinum_closes_and_six_win_the_f1(self):
        state = FairState()
        self.assertEqual(state.record("darts", 60), (35, ["bronze", "silver"], False))
        self.assertEqual(state.record("darts", 40), (0, [], False))            # Nothing new.
        self.assertEqual(state.record("darts", 150), (200, ["gold", "platinum"], False))
        self.assertTrue(state.closed("darts"))
        for game_id in GAME_IDS[1:-1]:
            state.record(game_id, GAMES[game_id].thresholds[-1])
        self.assertFalse(state.f1)
        earned, new, prize = state.record(GAME_IDS[-1], GAMES[GAME_IDS[-1]].thresholds[-1])
        self.assertTrue(prize and state.f1)
        self.assertEqual(earned, 235)
        again = FairState(json.loads(json.dumps(state.to_dict())))
        self.assertEqual((again.maxed(), again.f1), (6, True))
        self.assertEqual(FairState({"best": {"darts": -3}, "band": {"darts": 9}, "f1": "yes"}).to_dict(),
                         {"best": {}, "band": {}, "f1": False})


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_site_is_open_snow_joined_to_the_snow_road(self):
        world, site = self.world, self.world.fair
        sx, sy = site.sector
        around = [(sx + dx, sy + dy) for dx in range(-1, 3) for dy in range(-1, 3)]
        self.assertTrue(all(world.region(*s) == "snow" for s in around))
        self.assertFalse(any(s in world.roads.sectors() for s in around))
        center = next(s for s, n in CENTERS.items() if n == "center_snow")
        self.assertTrue(all(max(abs(s[0] - center[0]), abs(s[1] - center[1])) >= 3 for s in around))
        tx, ty = site.spur[-1]
        self.assertIn((tx, ty + 1), world.snow_roads)                     # Joins the snow road.
        self.assertTrue(all(t in world.snow_roads for t in site.spur))
        self.assertEqual(World(seed=4242).fair, site)                     # Same on every seed.

    def test_the_gate_is_the_only_way_in(self):
        collisions = CollisionManager(None, self.world)
        site = self.world.fair
        self.assertTrue(collisions.can_walk(Walker(*site.door).collision_record()))
        blocked = [site.at(8.0, 7.55), site.at(0.3, 4), site.at(15.7, 4), site.at(4, 0.3), site.at(4, 7.7)]
        for x, y in blocked:
            self.assertFalse(collisions.can_walk(Walker(x, y).collision_record()), (x, y))
        names = [s.name for s in exterior_sprites(site, self.world.seed)]
        for name in ("ticket_booth", "big_top", "ferris_wheel", "carousel"):
            self.assertIn(name, names)

    def test_visitor_cars_come_park_and_leave(self):
        outside = FairOutside(self.world.fair, 1)
        x, y = self.world.fair.center
        seen = set()
        for _ in range(4000):
            outside.update(0.05, x, y)
            seen |= {c.state for c in outside.shuttles}
        self.assertEqual(seen, {"away", "arriving", "parked", "leaving"})
        self.assertTrue(outside.sprites())


class InteriorTests(unittest.TestCase):
    def test_booths_rides_and_exit_can_be_reached(self):
        level = FairInterior(3)
        collisions = CollisionManager(None, level)
        self.assertTrue(collisions.can_walk(Walker(*level.entry).collision_record()))
        kinds = {}
        for spot in level.spots:
            self.assertTrue(collisions.can_walk(Walker(spot.x, spot.y).collision_record()), spot.label)
            self.assertIs(level.spot_near(spot.x, spot.y), spot)
            kinds.setdefault(spot.kind, set()).add(spot.key)
        self.assertEqual(kinds["booth"], set(GAME_IDS))
        self.assertEqual(kinds["ride"], set(RIDES))
        self.assertEqual(len(BOOTHS), 6)

    def test_rides_run_then_end(self):
        level = FairInterior(3)
        for kind in RIDES:
            level.start_ride(kind)
            done, t = False, 0.0
            start = level.ride_pose()[:2]
            while not done:
                level.update(0.1)
                done = level.update_ride(0.1)
                if not done:
                    x, y, _ = level.ride_pose()
                    self.assertTrue(0 < x < level.width and 0 < y < level.height)
                t += 0.1
            self.assertIsNone(level.ride)
            self.assertLess(t, 22)

    def test_riders_move_with_their_seat(self):
        from fair import CAROUSEL_SPIN, FERRIS_SPIN, ferris_seat, horse_seat
        level = FairInterior(3)
        level.update(3.3)
        level.start_ride("ferris_wheel")
        seat = level.ride.seat
        for _ in range(50):
            level.update(0.1)
            self.assertEqual(level.ride_pose()[:2], ferris_seat(seat, level.clock))  # The same car.
        level.ride = None
        level.start_ride("carousel")
        seat = level.ride.seat
        for _ in range(30):
            level.update(0.1)
            self.assertEqual(level.ride_pose(), horse_seat(seat, level.clock))
        # One lap of the wheel, two of the carousel: off where they got on.
        self.assertAlmostEqual(FERRIS_SPIN * 16, 2 * math.pi)
        self.assertGreater(len(level.ferris_riders) + len(level.horse_riders), 4)
        self.assertTrue(all(c.rider for c in level.bumpers))

    def test_the_bumper_car_drives_and_bumps(self):
        level = FairInterior(3)
        level.start_ride("bumper_cars")
        car = level.ride.bumper
        x0 = car.x
        for _ in range(20):
            level.update(1 / 30)
            level.update_ride(1 / 30, 1, 0)                         # UP: go (it starts facing west).
        self.assertLess(car.x, x0 - 20)
        heading = car.heading
        for _ in range(10):
            level.update_ride(1 / 30, 1, 1)                         # RIGHT: turn.
        self.assertNotAlmostEqual(car.heading, heading)
        other = level.bumpers[0]
        other.x, other.y = car.x + 20, car.y
        level.update(1 / 30)
        self.assertGreater(math.dist((car.x, car.y), (other.x, other.y)), 38)    # Pushed apart.


class F1Tests(unittest.TestCase):
    def test_f1_is_ten_percent_faster_and_grips_more(self):
        class Open:
            def can_move(self, rect):
                return True

        class City:
            def region_at(self, x, y):
                return "city"

        tops = []
        for f1 in (False, True):
            car = Car(x=0, y=0, f1=f1)
            for _ in range(600):
                car.update(1 / 60, 1, 0, City(), Open())
            tops.append(car.speed)
        self.assertAlmostEqual(tops[1] / tops[0], 1.10, places=3)
        self.assertEqual((Car(f1=True).sprite_name, Car().sprite_name), ("racer_f1", "racer_player"))

    def test_ratings_ignore_the_f1(self):
        world = World()
        m = Missions(world, world.seed, {"progress": {"city": {"level": 3, "mastery": 0}}})
        before = m.progress.rating("city")
        m.fair.f1 = True
        self.assertEqual(m.progress.rating("city"), before)     # Offers still scale on the base car.

    def test_pit_stop(self):
        game = PitStop(1)
        call = game.call
        game.press(call)
        self.assertEqual(game.score, 1)
        self.assertLess(game.window, 1.3)
        wrong = next(a for a in ("menu_up", "menu_down", "menu_left", "menu_right") if a != game.call)
        game.press(wrong)
        self.assertTrue(game.over)
        slow = PitStop(2)
        slow.update(2.0)
        self.assertTrue(slow.over)


if __name__ == "__main__":
    unittest.main()


class TowTrainTests(unittest.TestCase):
    def test_tows_grows_speeds_up_and_crashes(self):
        from arcade import TOW_GRID, TOW_STEP, TowTrain
        game = TowTrain(1)
        head = game.chain[0]
        game.target = (head[0], head[1] - 2)                 # Two cells straight ahead.
        game.update(TOW_STEP[0] * 2 + 0.001)
        self.assertEqual((game.score, len(game.chain)), (1, 4))
        self.assertLess(game.step, TOW_STEP[0])
        game.turn("menu_down")                                # Can't reverse into the chain.
        self.assertEqual(game.turning, (0, -1))
        game.turn("menu_left")
        game.target = (0, 0)
        while not game.over:
            game.update(0.05)
        self.assertEqual(game.chain[0][0], 0)                 # Ran into the west wall.

    def test_hitting_its_own_chain_ends_the_run(self):
        from arcade import TowTrain
        game = TowTrain(2)
        game.chain = [(5, 5), (5, 6), (4, 6), (4, 5), (4, 4)]
        game.heading = game.turning = (0, -1)
        game.turn("menu_left")                                # Into (4, 5): part of the chain.
        game.target = (9, 9)
        game.update(game.step + 0.001)
        self.assertTrue(game.over)
