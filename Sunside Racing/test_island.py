"""Elite Island: clubs and leagues, tournaments, the camps, island speed, and grid races."""

import json
import math
import random
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from collision_manager import CollisionManager
from grid_race import Entrant, GridRace, grid_slots
from island import (CLUB, CLUBS, ISLAND_RATINGS, LEAGUES, POINTS, TOURNEY_LAPS, IslandCenterInterior, IslandState,
                    Tournament, choose_camps, ferry_spots, payout, rivals_of)
from missions import Missions
from progression import Progress, REGIONS, mastery_to_reach, rating
from walker import Walker
from world import World


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_leagues_fees_and_payouts(self):
        self.assertEqual(ISLAND_RATINGS, (360, 390, 450, 500, 550))
        self.assertEqual({n: LEAGUES[n][2] for n in LEAGUES}, {1: 20_000, 2: 40_000, 3: 80_000, 4: 150_000})
        self.assertEqual([payout(n) for n in LEAGUES],
                         [(10_000, 50, 40_000), (20_000, 100, 80_000), (40_000, 200, 160_000), (80_000, 400, 320_000)])
        self.assertEqual(len(CLUBS), 8)
        for club in CLUBS:                                     # Two clubs a league, rivals of each other.
            self.assertEqual(CLUB[rivals_of(club[0])][2], club[2])
            self.assertNotEqual(rivals_of(club[0]), club[0])

    def test_joining_needs_race_one_the_rating_and_only_goes_up(self):
        m = Missions(self.world, self.world.seed)
        isl = m.island
        m.add_item("sunside_tokens", 500_000)
        self.assertIn("can't", isl.join(m, "palm", 400))        # Race 1 not won yet.
        isl.races = 1
        self.assertIn("can't", isl.join(m, "lagoon", 499))      # League 2 needs 500.
        self.assertIsNone(isl.join(m, "palm", 400))
        self.assertEqual((isl.club, isl.league, m.tokens), ("palm", 1, 480_000))
        self.assertIn("can't", isl.join(m, "reef", 400))        # No sideways (or down).
        self.assertIsNone(isl.join(m, "volcano", 650))          # Up two leagues at once.
        self.assertIn("can't", isl.join(m, "lagoon", 650))
        again = Missions(self.world, self.world.seed, json.loads(json.dumps(m.to_dict())))
        self.assertEqual((again.island.club, again.island.races), ("volcano", 1))
        self.assertEqual(IslandState({"club": "nope", "races": 9, "crossed": 1}).to_dict()["club"], None)

    def test_island_speed_and_rating_are_the_best_regions(self):
        progress = Progress()
        progress.add("desert", mastery_to_reach(30))
        self.assertEqual(progress.rating("island"), rating(30))
        self.assertEqual(progress.speed_scale("island"), progress.speed_scale("desert"))
        self.assertLess(progress.speed_scale("snow"), progress.speed_scale("island"))


class TournamentTests(unittest.TestCase):
    def test_four_tracks_ratings_around_that_region(self):
        progress = Progress({r: {"level": 20 + i * 3, "mastery": 0} for i, r in enumerate(REGIONS)})
        tour = Tournament("palm", 7)
        self.assertEqual(len({t.theme for t in tour.tracks}), 4)
        self.assertTrue(all(t.as_track()["laps"] == TOURNEY_LAPS == 2 for t in tour.tracks))
        for _ in range(4):
            theme = tour.tracks[tour.race].theme
            base = progress.rating(theme)
            entrants = tour.entrants(progress)
            self.assertEqual([e.team for e in entrants].count("home"), 3)
            self.assertEqual([e.team for e in entrants].count("away"), 4)
            self.assertTrue(all(base - 5 <= e.rating <= base + 8 for e in entrants), theme)
            home = sorted(e.rating - base for e in entrants if e.team == "home")
            away = sorted(e.rating - base for e in entrants if e.team == "away")
            # Teammates: one -5..0, two +1..+8. Rivals: one -5..0, two +1..+5, one +2..+8.
            self.assertTrue(-5 <= home[0] <= 0 and all(1 <= d <= 8 for d in home[1:]), home)
            self.assertTrue(-5 <= away[0] <= 0 and all(1 <= d <= 5 for d in away[1:3]) and 2 <= away[3] <= 8, away)
            offsets = [e.rating - base for e in entrants]
            if tour.race:
                self.assertEqual(offsets, first)                     # Same for every track.
            else:
                first = offsets
            tour.record([-1, 0, 1, 2, 3, 4, 5, 6], entrants)
        self.assertTrue(tour.over)
        self.assertEqual(tour.points["home"], 4 * (10 + 8 + 6 + 5))
        self.assertEqual(tour.winner(), "home")

    def test_the_offset_mix_over_many_tournaments(self):
        from island import AWAY_OFFSETS, HOME_OFFSETS
        self.assertEqual(HOME_OFFSETS, ((-5, 0), (1, 8), (1, 8)))
        self.assertEqual(AWAY_OFFSETS, ((-5, 0), (1, 5), (1, 5), (2, 8)))
        for seed in range(200):
            offsets = Tournament("tide", seed).offsets
            self.assertEqual(sum(d <= 0 for d in offsets["home"]), 1)
            self.assertEqual(sum(d <= 0 for d in offsets["away"]), 1)
            self.assertTrue(all(-5 <= d <= 8 for d in offsets["home"] + offsets["away"]))

    def test_a_tie_goes_to_the_best_finisher_of_the_last_race(self):
        tour = Tournament("reef", 1)
        tour.results = [["away"] + ["home"] * 3 + ["away"] * 3 + ["home"]]
        tour.points = {"home": 50, "away": 50}
        self.assertEqual(tour.winner(), "away")
        self.assertEqual(POINTS, (10, 8, 6, 5, 4, 3, 2, 1))


class PlaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_eight_camps_spread_over_the_island(self):
        camps = self.world.island_camps
        self.assertEqual(set(camps), {c[0] for c in CLUBS})
        sectors = [c.sector for c in camps.values()]
        self.assertEqual(len(set(sectors)), 8)
        self.assertTrue(all(self.world.region(*s) == "island" for s in sectors))
        self.assertEqual(choose_camps(World(seed=5)), camps)
        collisions = CollisionManager(None, self.world)
        for camp in camps.values():
            self.assertTrue(collisions.can_walk(Walker(*camp.spot).collision_record()), camp.club)
        for spot in ferry_spots(self.world):
            self.assertTrue(collisions.can_walk(Walker(*spot).collision_record()), spot)

    def test_island_center_desks_are_reachable(self):
        level = IslandCenterInterior(1)
        collisions = CollisionManager(None, level)
        self.assertEqual({s.kind for s in level.spots}, {"exit", "races", "clubs", "tourney", "look"})
        for spot in level.spots:
            self.assertTrue(collisions.can_walk(Walker(spot.x, spot.y).collision_record()), spot.label)


class GridRaceTests(unittest.TestCase):
    def race(self):
        entrants = [Entrant(f"R{i}", 400 + i, "home" if i < 3 else "away", "racer_cyan") for i in range(7)]
        return GridRace({"theme": "jungle", "seed": "grid", "size": 6, "laps": 2}, 1.8, 3, entrants)

    def test_eight_grid_slots_on_the_track_behind_the_line(self):
        race = self.race()
        slots = grid_slots(race.level)
        self.assertEqual(len(slots), 8)
        for x, y, _ in slots:
            self.assertIn((int(x // 64), int(y // 64)), race.level.track)
            self.assertLess(x, race.level.start_x)
        pts = [(x, y) for x, y, _ in slots]
        self.assertTrue(all(math.dist(a, b) > 40 for i, a in enumerate(pts) for b in pts[i + 1:]))

    def test_the_player_drives_through_the_other_racers_but_not_walls(self):
        race = self.race()
        race.clock = 0.0
        rival = race.rivals[0]
        rival.x, rival.y = race.car.x + 30, race.car.y                # Right on the nose.
        race.car.heading = 90.0
        x0 = race.car.x
        for _ in range(30):
            race.update(1 / 30, 1, 0)
            rival.x, rival.y = race.car.x + 30, race.car.y            # Keep it in the way.
        self.assertGreater(race.car.x, x0 + 20)                       # Straight through.
        self.assertEqual(race.collisions.fixed, [])
        self.assertTrue(race.level.obstacles)                        # Walls are still solid.
        wall = race.level.obstacles[0]
        self.assertFalse(race.collisions.can_move(race.car.collision_record(wall.x, wall.y)))

    def test_racers_slide_on_ice_more_than_one_on_one_rivals(self):
        from drag_race import RIVAL_ICE_ROOM
        entrants = [Entrant(f"R{i}", 400, "home" if i < 3 else "away", "racer_cyan") for i in range(7)]
        race = GridRace({"theme": "snow", "seed": "ice1", "size": 7, "laps": 2}, 1.9, 5, entrants)
        self.assertTrue(race.level.is_ice(*race.level.path[1]))
        slides = []
        for _ in range(900):
            race.update(1 / 30, 0, 0)
            slides += [math.hypot(*r.offset) for r in race.rivals]
        self.assertGreater(sum(slides) / len(slides), 30)
        self.assertGreater(max(slides), RIVAL_ICE_ROOM)               # Wider than a one-on-one rival.

    def test_the_grid_alternates_like_a_chessboard(self):
        from grid_race import PLAYER_SLOT
        race = self.race()
        teams = {PLAYER_SLOT: "player"}
        for entrant, slot in zip(race.entrants, race.slots):
            teams[slot] = entrant.team
        rows = [(teams[2 * r], teams[2 * r + 1]) for r in range(4)]
        self.assertEqual(rows, [("home", "away"), ("away", "home"), ("player", "away"), ("away", "home")])
        slots = grid_slots(race.level)
        for rival, slot in zip(race.rivals, race.slots):             # Each car really starts there.
            self.assertAlmostEqual(math.dist((rival.x, rival.y), slots[slot][:2]), 0, places=3)

    def test_idle_player_finishes_last_and_quitting_is_last(self):
        race = self.race()
        t = 0.0
        while not race.result and t < 300:
            race.update(1 / 30, 0, 0)
            t += 1 / 30
        self.assertEqual(race.order[-1], -1)
        self.assertEqual(race.position(), 8)
        self.assertEqual(sorted(race.order), [-1, 0, 1, 2, 3, 4, 5, 6])
        other = self.race()
        other.clock = 5.0
        other.finish(quit=True)
        self.assertEqual(other.order[-1], -1)


if __name__ == "__main__":
    unittest.main()
