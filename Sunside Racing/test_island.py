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
                    Tournament, badges_for, choose_camps, ferry_spots, next_badge, payout, rivals_of)
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
                         [(10_000, 50, 40_000), (20_000, 100, 80_000), (40_000, 150, 160_000), (80_000, 0, 320_000)])
        self.assertEqual(len(CLUBS), 8)
        for club in CLUBS:                                     # Two clubs a league, rivals of each other.
            self.assertEqual(CLUB[rivals_of(club[0])][2], club[2])
            self.assertNotEqual(rivals_of(club[0]), club[0])

    def test_top_league_wins_earn_badges(self):
        self.assertEqual(badges_for(9), [])
        self.assertEqual(badges_for(10), ["bronze"])
        self.assertEqual(badges_for(74), ["bronze", "silver", "gold"])
        self.assertEqual(badges_for(150), ["bronze", "silver", "gold", "green", "blue", "purple"])
        self.assertEqual(next_badge(0), ("bronze", 10))
        self.assertEqual(next_badge(99), ("blue", 100))
        self.assertIsNone(next_badge(150))
        state = IslandState({"club": "elite", "elite_wins": 26})
        self.assertEqual(state.badges, ["bronze", "silver"])
        self.assertEqual(IslandState(state.to_dict()).elite_wins, 26)      # Saved.
        self.assertEqual(IslandState({"elite_wins": -3}).elite_wins, 0)

    def test_the_bedroom_has_a_plain_badge_board(self):
        from home import HomeInterior
        home = HomeInterior()
        on_wall = [s.name for s in home.visible_sprites() if s.name.startswith("badge_")]
        self.assertEqual(on_wall, ["badge_board"])                  # Just the board, no medals.
        self.assertTrue(any(s.kind == "badges" for s in home.spots))

    def test_badge_board_is_a_three_two_one_pyramid(self):
        from badge_ui import slot_positions
        slots = slot_positions(640, 360)
        rows = sorted({round(y) for _, y in slots})
        self.assertEqual([sum(1 for _, y in slots if round(y) == r) for r in rows], [3, 2, 1])
        self.assertEqual(slots[-1], (640, rows[-1]))                # Purple at the tip, centered.

    def test_joining_needs_race_one_the_rating_and_only_goes_up(self):
        m = Missions(self.world, self.world.seed)
        isl = m.island
        m.add_item("sunside_tokens", 500_000)
        self.assertIn("can't", isl.join(m, "palm", 400))        # Race 1 not won yet.
        isl.races = 1
        self.assertIn("can't", isl.join(m, "lagoon", 409))      # League 2 needs 410.
        self.assertIsNone(isl.join(m, "palm", 400))
        self.assertEqual((isl.club, isl.league, m.tokens), ("palm", 1, 480_000))
        self.assertIn("can't", isl.join(m, "reef", 400))        # No sideways (or down).
        self.assertIsNone(isl.join(m, "volcano", 495))          # Up two leagues at once (490+).
        self.assertIn("can't", isl.join(m, "lagoon", 495))
        self.assertEqual({n: LEAGUES[n][:2] + LEAGUES[n][3:] for n in LEAGUES},
                         {1: (360, 400, 0), 2: (410, 480, 410), 3: (490, 590, 490), 4: (600, None, 600)})
        again = Missions(self.world, self.world.seed, json.loads(json.dumps(m.to_dict())))
        self.assertEqual((again.island.club, again.island.races), ("volcano", 1))
        self.assertEqual(IslandState({"club": "nope", "races": 9, "crossed": 1}).to_dict()["club"], None)

    def test_regions_stop_at_level_51_max(self):
        from progression import MAX_LEVEL
        self.assertEqual((MAX_LEVEL, rating(MAX_LEVEL)), (51, 600))          # Exactly league 4.
        progress = Progress()
        self.assertEqual(progress.add("city", mastery_to_reach(60))[-1], 51)
        self.assertEqual((progress.levels["city"], progress.mastery["city"]), (51, 0))
        self.assertTrue(progress.is_max("city"))
        self.assertEqual(progress.add("city", 10_000), [])                    # Nothing more sticks.
        self.assertEqual(progress.mastery["city"], 0)
        self.assertEqual(Progress({"city": {"level": 70, "mastery": 5}}).levels["city"], 51)
        m = Missions(self.world, self.world.seed, {"progress": {"city": {"level": 51, "mastery": 0}}})
        m.unspent = 40
        self.assertEqual(m.spend("city", 10), [])
        self.assertEqual(m.unspent, 40)                                       # Points aren't wasted.
        self.assertTrue(next(r for r in m.mastery_rows() if r.get("region") == "city")["max"])

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
        self.assertEqual(len({t.theme for t in tour.tracks}), 3)
        self.assertTrue(all(t.as_track()["laps"] == TOURNEY_LAPS == 2 for t in tour.tracks))
        for _ in range(3):
            theme = tour.tracks[tour.race].theme
            base = progress.rating(theme)
            entrants = tour.entrants(progress)
            self.assertEqual([e.team for e in entrants].count("home"), 3)
            self.assertEqual([e.team for e in entrants].count("away"), 4)
            self.assertTrue(all(base - 5 <= e.rating <= base + 8 for e in entrants if e.team == "home"), theme)
            home = sorted(e.rating - base for e in entrants if e.team == "home")
            away = sorted(e.rating - base for e in entrants if e.team == "away")
            # Teammates: -5..0, +1..+5, +3..+8; the rivals are set by the edge.
            self.assertTrue(-5 <= home[0] <= 0 and 1 <= home[1] <= 5 and 3 <= home[2] <= 8, home)
            self.assertEqual((sum(home) + 0) / 4 - sum(away) / 4, tour.edge)
            offsets = [e.rating - base for e in entrants]
            if tour.race:
                self.assertEqual(offsets, first)                     # Same for every track.
            else:
                first = offsets
            tour.record([-1, 0, 1, 2, 3, 4, 5, 6], entrants)
        self.assertTrue(tour.over)
        self.assertEqual(tour.points["home"], 3 * (10 + 8 + 6 + 5))
        self.assertEqual(tour.winner(), "home")

    def test_the_teams_differ_by_the_edge_and_no_more(self):
        from island import AWAY_ROLLED, HOME_OFFSETS
        self.assertEqual(HOME_OFFSETS, ((-5, 0), (1, 5), (3, 8)))
        self.assertEqual(AWAY_ROLLED, ((-5, 0), (3, 8)))
        edges = set()
        for seed in range(300):
            tour = Tournament("tide", seed)
            home, away = tour.offsets["home"], tour.offsets["away"]
            self.assertEqual(sum(d <= 0 for d in home), 1)
            self.assertTrue(-5 <= tour.edge <= 5)
            self.assertEqual((sum(home) + 0) / 4 - sum(away) / 4, tour.edge)   # Home avg - away avg.
            self.assertTrue(any(-5 <= d <= 0 for d in away) and any(3 <= d <= 8 for d in away))
            edges.add(tour.edge)
        self.assertEqual(edges, set(range(-5, 6)))
        from island import team_offsets
        home, away, edge = team_offsets(random.Random(9))
        self.assertLessEqual(abs(away[2] - away[3]), 1)                       # The two share it evenly.

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
        race = self.race()
        teams = {race.player_slot: "home"}                             # The player is on the home team.
        for entrant, slot in zip(race.entrants, race.slots):
            teams[slot] = entrant.team
        rows = [(teams[2 * r], teams[2 * r + 1]) for r in range(4)]
        self.assertEqual(rows, [("home", "away"), ("away", "home"), ("home", "away"), ("away", "home")])
        self.assertEqual(len(set(race.slots) | {race.player_slot}), 8)
        entrants = race.entrants
        seen = {GridRace({"theme": "jungle", "seed": "grid", "size": 6, "laps": 2}, 1.8, seed,
                         entrants).player_slot for seed in range(40)}
        self.assertEqual(seen, {0, 3, 4, 7})                           # The player's square varies.
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
