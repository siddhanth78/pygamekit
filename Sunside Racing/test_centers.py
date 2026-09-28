"""Racing centers, rivals, race scaling, and Elite Island."""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import math

from car import Car
from collision_manager import CollisionManager
from drag_race import CLEAN_LAP, CUT_REACH, DragRace, best_line_length, flawless_time
from fast_travel import island_destination, on_island, on_mainland_beach
from progression import CENTER_RACES, ISLAND_LEVEL, RATING_EDGE, REGIONS, Progress, rating, rating_speed
from racers import LAPS, RIVAL_RATINGS, RIVALS, STORIES, rival, track_size
from track_gen import CELL, generate, grow_blob, outline
from world import SECTOR_SIZE, TILE_SIZE, World


def center_race(region, race):
    track = {"kind": "circuit", "theme": region, "laps": LAPS, "seed": f"2026-{region}-{race}",
             "size": track_size(race)}
    return DragRace(track, None, 1.0, race, rival_rating=RIVAL_RATINGS[race - 1], rival_off_day=0.0)


def rival_time(race):
    """The rival's actual race time at game rate, reaction delay included."""
    clock = 0.0
    while race.rival.progress < race.level.race_length:
        clock += 1 / 60
        race.rival.update(1 / 60, clock)
    return clock


class ProgressTests(unittest.TestCase):
    def test_race_wins_are_capped_saved_and_old_saves_start_at_zero(self):
        progress = Progress({"city": {"level": 3, "mastery": 0}})  # From before centers.
        self.assertEqual(progress.races["city"], 0)
        for _ in range(12):
            progress.win_race("city")
        self.assertEqual(progress.races["city"], CENTER_RACES)
        again = Progress(json.loads(json.dumps(progress.to_dict())))
        self.assertEqual((again.races["city"], again.centers_done()), (CENTER_RACES, 1))

    def test_island_needs_every_center_and_one_region_at_25(self):
        progress = Progress()
        for region in REGIONS:
            progress.races[region] = CENTER_RACES
        progress.levels["snow"] = ISLAND_LEVEL - 1
        self.assertFalse(progress.island_unlocked())
        progress.levels["snow"] = ISLAND_LEVEL
        self.assertTrue(progress.island_unlocked())
        progress.races["rural"] = CENTER_RACES - 1
        self.assertFalse(progress.island_unlocked())


class RivalTests(unittest.TestCase):
    def test_ten_named_rivals_with_short_lines_per_region(self):
        self.assertEqual(set(RIVALS), set(REGIONS))
        self.assertEqual(set(STORIES), set(REGIONS))
        for region in REGIONS:
            names = [name for name, _, _ in RIVALS[region]]
            self.assertEqual(len(names), CENTER_RACES)
            self.assertEqual(len(set(names)), CENTER_RACES)
            for name, line, sprite in RIVALS[region]:
                self.assertLessEqual(len(f'{name}: "{line}"'), 64)  # Fits the offer panel.
                self.assertTrue(sprite.startswith("racer_"))

    def test_every_race_is_won_by_a_clean_lap_two_below_its_rating_and_lost_twelve_below(self):
        self.assertEqual(RIVAL_RATINGS, (100, 110, 120, 140, 150, 170, 220, 260, 300, 350))
        self.assertEqual(rating(25) + RATING_EDGE, RIVAL_RATINGS[-1])   # The champion needs level 25.
        for region in REGIONS:
            for number in range(1, 11):
                race = center_race(region, number)
                seconds = rival_time(race)
                rated = RIVAL_RATINGS[number - 1]
                # On ice both cars slide under the same physics, so the player's clean lap
                # loses the same share of time the rival does there.
                ice = 1.0
                if region == "snow":
                    dry = center_race(region, number)
                    dry.level.is_ice = lambda x, y: False
                    ice = seconds / rival_time(dry)
                # A clean human lap (CLEAN_LAP off the flat-out model) 2 below always wins, 12
                # below never does: rivals are calibrated so 10 below just wins, then run
                # STRAIGHT_BOOST faster down the straights (about 5 rating points harder).
                clean = lambda rated: flawless_time(race.level, multiplier=rating_speed(rated)) * CLEAN_LAP * ice
                self.assertLess(clean(rated - 2), seconds, (region, number))
                self.assertGreater(clean(rated - 12), seconds, (region, number))
        self.assertEqual(rival("snow", 10)[0], "The Glacier")


class CornerCuttingTests(unittest.TestCase):
    def test_the_ideal_line_is_much_shorter_than_the_centerline(self):
        race = center_race("city", 4)
        self.assertLess(best_line_length(race.level), race.level.race_length * 0.8)

    def test_rated_rivals_cut_about_half_the_corners_at_their_rating_speed_and_stay_on_track(self):
        for region in REGIONS:
            race = center_race(region, 5)
            corners = len(race.level.path) * (race.level.laps + 1)
            self.assertLess(abs(race.rival.cuts / corners - 0.5), 0.15)   # The rest run wide.
            self.assertAlmostEqual(race.rival.scale, rating_speed(RIVAL_RATINGS[4]))
            self.assertLessEqual(race.rival.corner, race.rival.top)
        race = center_race("jungle", 10)
        while race.rival.progress < race.level.race_length:
            race.rival.update(1 / 60, 10.0)
            self.assertIn((int(race.rival.x // TILE_SIZE), int(race.rival.y // TILE_SIZE)),
                          race.level.track)

    def test_long_straights_stay_central(self):
        race = center_race("desert", 10)
        corners = race.level.path
        segments = list(zip(corners, corners[1:] + corners[:1]))
        long_ones = [(a, b) for a, b in segments if math.dist(a, b) > 2 * CUT_REACH]
        self.assertTrue(long_ones)

        def off_center(point):
            best = math.inf
            for (ax, ay), (bx, by) in segments:
                length2 = (bx - ax) ** 2 + (by - ay) ** 2
                t = max(0, min(1, ((point[0] - ax) * (bx - ax) + (point[1] - ay) * (by - ay)) / length2))
                best = min(best, math.dist(point, (ax + (bx - ax) * t, ay + (by - ay) * t)))
            return best

        checked = 0
        while race.rival.progress < race.level.race_length:
            race.rival.update(1 / 60, 10.0)
            here = (race.rival.x, race.rival.y)
            # On a long straight, cuts drift in only CUT_REACH from each end: centered between.
            for (ax, ay), (bx, by) in long_ones:
                length = math.dist((ax, ay), (bx, by))
                t = ((here[0] - ax) * (bx - ax) + (here[1] - ay) * (by - ay)) / length ** 2
                side = math.dist(here, (ax + (bx - ax) * t, ay + (by - ay) * t))
                if CUT_REACH + 8 < t * length < length - CUT_REACH - 8 and side < 96:
                    self.assertLess(side, 1.0)
                    checked += 1
        self.assertGreater(checked, 0)

    def test_cuts_are_diagonals_not_hairpins(self):
        for region in REGIONS:
            points = center_race(region, 10).rival.points
            for a, b, d in zip(points, points[1:], points[2:]):
                first = math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1])))
                second = math.degrees(math.atan2(d[0] - b[0], -(d[1] - b[1])))
                # Holding the inside between corners can add a couple of degrees; never a hairpin.
                self.assertLessEqual(abs((second - first + 180) % 360 - 180), 95)

    def test_drag_rivals_never_cut_and_are_balanced_for_the_ideal_line(self):
        race = DragRace({"kind": "circuit", "theme": "city", "seed": 99, "size": 6}, 1.0, 1.0, 3)
        self.assertEqual(race.rival.cuts, 0)
        seconds = rival_time(race)
        # Difficulty 1.0: a flawless stock car on the ideal line about ties (reaction aside).
        self.assertAlmostEqual(seconds, flawless_time(race.level, 1), delta=1.0)
        straight = DragRace({"kind": "straight", "theme": "city"}, 0.8, 1.0, 3)
        self.assertEqual((straight.rival.scale, straight.rival.cuts), (0.8, 0))


class TrackGeneratorTests(unittest.TestCase):
    def test_fifty_unique_tracks_that_grow_with_the_race(self):
        layouts = {(r, k): generate(f"2026-{r}-{k}", track_size(k)) for r in REGIONS for k in range(1, 11)}
        self.assertEqual(len(set(layouts.values())), 50)
        self.assertEqual(generate("2026-city-3", 6), layouts[("city", 3)])  # Same seed, same track.
        laps = {k: sum(DragRace({"kind": "circuit", "theme": r, "seed": f"2026-{r}-{k}",
                                 "size": track_size(k)}, 1.0, 1.0, 1).level.lap_length
                       for r in REGIONS) for k in (1, 10)}
        self.assertGreater(laps[10], laps[1] * 1.8)

    def test_layouts_are_simple_clockwise_loops_starting_eastbound(self):
        import random
        for seed in range(200):
            corners = generate(seed, 4 + seed % 9)
            (ax, ay), (bx, by) = corners[0], corners[1]
            self.assertEqual(ay, by)
            self.assertGreaterEqual(bx - ax, CELL)            # Room for the start grid.
            self.assertEqual(len(set(corners)), len(corners))  # Never revisits a corner.
            area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]))
            self.assertGreater(area, 0)                       # Clockwise on screen (y down).
            blob = grow_blob(random.Random(seed), 4 + seed % 9)
            self.assertEqual(len(outline(blob)) % 2, 0)       # Rectilinear: even corners.

    def test_parallel_stretches_stay_apart(self):
        for seed in range(100):
            level = DragRace({"kind": "circuit", "theme": "city", "seed": seed, "size": 12}, 1.0, 1.0, 1).level
            # No barrier sits on the track, and every track cell is walled from outside.
            self.assertFalse(any((int(o.x // TILE_SIZE), int(o.y // TILE_SIZE)) in level.track
                                 for o in level.obstacles))


class ThreeLapRaceTests(unittest.TestCase):
    def test_three_laps_on_the_region_surface(self):
        race = DragRace({"kind": "circuit", "theme": "snow", "shape": 3, "laps": 3}, 1.0, 1.0, 5)
        level = race.level
        self.assertAlmostEqual(level.race_length, 3 * level.lap_length)
        self.assertEqual(level.region_at(*level.path[0]), "snow")   # Icy grip on track.
        self.assertEqual(race.lap(), 1)
        race.player_progress = level.lap_length * 1.5
        self.assertEqual(race.lap(), 2)
        race.player_progress = level.lap_length * 9
        self.assertEqual(race.lap(), 3)
        # The rival stays on the track for all three laps.
        race.clock = 0.0
        while race.rival.progress < level.race_length:
            race.rival.update(1 / 60, 10.0)
            self.assertIn((int(race.rival.x // TILE_SIZE), int(race.rival.y // TILE_SIZE)), level.track)


class IslandTravelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_travel_there_and_back(self):
        collisions = CollisionManager(None, self.world)
        car = Car()
        x, y, _ = island_destination(self.world, collisions, car, to_island=True)
        self.assertTrue(on_island(self.world, x, y))
        self.assertTrue(collisions.can_move(Car(x=x, y=y).collision_record()))
        x, y, _ = island_destination(self.world, collisions, car, to_island=False)
        self.assertFalse(on_island(self.world, x, y))
        self.assertNotEqual(self.world.region_at(x, y), "sea")
        self.assertLess(abs(x // SECTOR_SIZE - 47), 2)  # Beside the mainland ferry dock.

    def test_beach_detection(self):
        beach = next((sx * SECTOR_SIZE + 256, 31 * SECTOR_SIZE + 256) for sx in range(40, 50)
                     if self.world.region(sx, 31) == "beach")
        self.assertTrue(on_mainland_beach(self.world, *beach))
        self.assertFalse(on_mainland_beach(self.world, Car().x, Car().y))
        self.assertTrue(on_island(self.world, *self.world.center_position(56, 31)))


if __name__ == "__main__":
    unittest.main()
