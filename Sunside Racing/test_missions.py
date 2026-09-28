"""Progression, mission givers and offers, in-world missions, drag races, and saving."""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from car import ACCELERATION, TOP_SPEED, Car
from collision_manager import CollisionManager
from drag_race import CLEAN_LAP, DragRace, TrackLevel, flawless_time, off_day
from missions import DELIVERY_PENALTY, Missions, Offer, difficulty
from player_save import PlayerSave
from progression import Progress, mastery_to_next, mastery_to_reach, rating_difficulty, rating_speed, reward
from world import TILE_SIZE, World


def center_rival_time(race):
    """Seconds the race's rival needs from its start, ignoring its reaction delay."""
    rival, seconds = race.rival, 0.0
    while rival.progress < race.level.race_length:
        rival.update(1 / 60, rival.reaction)
        seconds += 1 / 60
    return seconds


class ProgressionTests(unittest.TestCase):
    def test_levels_cost_fifteen_times_level(self):
        progress = Progress()
        self.assertEqual(progress.add("city", 14), [])
        self.assertEqual(progress.add("city", 1), [2])
        self.assertEqual(progress.add("city", 30 + 45), [3, 4])
        self.assertEqual(mastery_to_reach(4), 15 + 30 + 45)
        self.assertEqual((progress.levels["city"], progress.mastery["city"]), (4, 0))
        self.assertEqual(progress.levels["snow"], 1)  # Levels are per region.

    def test_levels_cost_thirty_times_level_from_level_ten(self):
        self.assertEqual([mastery_to_next(level) for level in (8, 9, 10, 11, 15)],
                         [120, 135, 300, 330, 450])
        self.assertEqual(mastery_to_reach(12), 15 * sum(range(1, 10)) + 300 + 330)

    def test_rewards_grow_by_one_per_level_and_double_for_veterans(self):
        self.assertEqual(reward(2, 1), 2)
        self.assertEqual(reward(2, 4), 5)
        self.assertEqual(reward(3, 5, harder=True), 14)
        self.assertEqual(reward(0, 9, harder=True), 0)

    def test_level_upgrades(self):
        progress = Progress({"desert": {"level": 5, "mastery": 3}})
        self.assertAlmostEqual(progress.speed_scale("desert"), 1.16)
        self.assertEqual(progress.speed_scale("beach"), 1.0)
        self.assertEqual((progress.rating("desert"), progress.rating("city")), (140, 100))
        self.assertTrue(progress.harder_unlocked("desert"))
        self.assertFalse(progress.harder_unlocked("city"))

    def test_difficulty_labels(self):
        self.assertEqual([difficulty(s) for s in (0.5, 0.79, 0.8, 1.04, 1.05, 1.25)],
                         ["Easy", "Easy", "Medium", "Medium", "Hard", "Hard"])


class GiverAndOfferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_three_givers_and_three_veterans_per_region_off_the_road(self):
        missions = Missions(self.world, self.world.seed)
        self.assertEqual(len(missions.givers), 30)
        for giver in missions.givers:
            self.assertEqual(self.world.region_at(giver.x, giver.y), giver.region)
            self.assertFalse(self.world._road_style(int(giver.x // TILE_SIZE), int(giver.y // TILE_SIZE)))
        self.assertEqual(len(missions.visible_givers()), 15)  # Veterans wait for level 5.

    def test_veterans_are_spread_across_their_region(self):
        from world import SECTOR_SIZE
        missions = Missions(self.world, self.world.seed)
        for region in ("city", "jungle", "desert", "snow", "rural"):
            center = missions.center_position(region)
            vets = [g for g in missions.givers if g.harder and g.region == region]
            self.assertEqual(sorted(g.type for g in vets), ["delivery", "drag", "speed"])
            for vet in vets:
                self.assertGreaterEqual(math.dist((vet.x, vet.y), center), 3.5 * SECTOR_SIZE)
                sx, sy = int(vet.x // SECTOR_SIZE), int(vet.y // SECTOR_SIZE)
                self.assertTrue(all(self.world.region(sx + dx, sy + dy) == region  # Inside, not
                                    for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)))  # on the edge.
            for i, a in enumerate(vets):
                for b in vets[i + 1:]:
                    self.assertGreater(math.dist((a.x, a.y), (b.x, b.y)), 4 * SECTOR_SIZE)
        again = Missions(self.world, self.world.seed)                 # Same spots every time.
        self.assertEqual([(g.x, g.y) for g in again.givers], [(g.x, g.y) for g in missions.givers])

    def test_offers_match_their_type_and_survive_a_round_trip(self):
        missions = Missions(self.world, self.world.seed)
        for giver in missions.givers[:6]:
            offer = missions.offer_for(giver)
            self.assertEqual(offer.type, giver.type)
            self.assertTrue(0.8 if giver.harder else 0.5 <= offer.scale <= 1.25)
            if offer.type == "drag":
                self.assertIn(offer.track["kind"], ("straight", "circuit"))
            else:
                self.assertNotIn(self.world.region_at(*offer.target), ("sea", "island"))
        again = Missions(self.world, self.world.seed, json.loads(json.dumps(missions.to_dict())))
        self.assertEqual({k: v.to_dict() for k, v in again.offers.items()},
                         {k: v.to_dict() for k, v in missions.offers.items()})

    def test_drag_rivals_are_rated_minus_10_to_plus_15_around_the_player(self):
        missions = Missions(self.world, self.world.seed,
                            {"progress": {"city": {"level": 2, "mastery": 0},
                                          "desert": {"level": 5, "mastery": 0}}})
        for giver in (g for g in missions.givers if g.type == "drag"):
            base = missions.progress.rating(giver.region)
            gaps = set()
            for _ in range(60):
                missions.offers.pop(giver.id, None)
                gaps.add(missions.offer_for(giver).rating - base)
            if giver.harder:
                continue   # See test_veterans_race_only_medium_circuits.
            self.assertLessEqual(max(gaps), 15)
            self.assertGreaterEqual(min(gaps), -10, giver.id)
            self.assertGreater(len(gaps), 8)

    def test_veterans_race_only_medium_circuits_and_set_medium_or_hard_trials(self):
        missions = Missions(self.world, self.world.seed,
                            {"progress": {region: {"level": 6, "mastery": 0} for region in ("city", "desert")}})
        for giver in (g for g in missions.givers if g.harder and g.region in ("city", "desert")):
            base = missions.progress.rating(giver.region)
            gaps = set()
            for _ in range(40):
                missions.offers.pop(giver.id, None)
                offer = missions.offer_for(giver)
                self.assertFalse(missions.preview(offer)["can_decline"], giver.id)
                self.assertIn(missions.label(offer), ("Medium", "Hard"), giver.id)
                if offer.type == "drag":
                    self.assertEqual(offer.track["kind"], "circuit")
                    self.assertEqual(missions.label(offer), "Medium")
                    gaps.add(offer.rating - base)
            if giver.type == "drag":
                self.assertTrue(1 <= min(gaps) and max(gaps) <= 10, gaps)
                self.assertGreater(len(gaps), 5)

    def test_drag_difficulty_is_the_rating_gap_and_the_rival_keeps_its_rating(self):
        missions = Missions(self.world, self.world.seed,
                            {"progress": {"city": {"level": 2, "mastery": 0}}})
        giver = missions.by_id["city-drag"]
        offer = Offer(giver.id, "drag", 1.0, None, {"kind": "circuit", "theme": "city", "seed": 1, "size": 4},
                      1, dict(missions.progress.levels), 125)
        missions.offers[giver.id] = offer
        preview = missions.preview(offer)
        self.assertEqual((preview["difficulty"], preview["rules"]), ("Hard", "Rival (125) VS You (110)"))
        missions.progress.add("city", mastery_to_next(2))      # Level 3 (120): +5 is Medium.
        preview = missions.preview(offer)
        self.assertEqual((preview["difficulty"], preview["rules"]), ("Medium", "Rival (125) VS You (120)"))
        missions.progress.add("city", mastery_to_next(3))      # Level 4 (130): below the player is Easy.
        self.assertEqual(missions.label(offer), "Easy")
        restored = Missions(self.world, self.world.seed, json.loads(json.dumps(missions.to_dict())))
        self.assertEqual(restored.offers[giver.id].rating, 125)
        missions.progress.levels["city"] = 3            # 120 again, on a straight: no corners to cut.
        offer.track = {"kind": "straight", "theme": "city"}
        self.assertEqual(missions.label(offer), "Hard")
        self.assertEqual([rating_difficulty(gap) for gap in (-10, 0, 1, 10, 11, 15)],
                         ["Easy", "Easy", "Medium", "Medium", "Hard", "Hard"])

    def test_time_trial_clocks_stay_at_the_offer_time_car_after_leveling(self):
        missions = Missions(self.world, self.world.seed)
        giver = missions.by_id["city-speed"]
        offer = Offer(giver.id, "speed", 1.0, (giver.x + 3000, giver.y), levels={"city": 1})
        start = (giver.x, giver.y)
        before = missions.speed_limit(offer, start)
        missions.progress.add("city", mastery_to_reach(4))  # Level 4.
        self.assertAlmostEqual(missions.speed_limit(offer, start), before)
        self.assertAlmostEqual(missions.effective_scale(offer), 1.0 / 1.12)
        self.assertEqual(missions.label(offer), "Medium")
        missions.progress.add("city", mastery_to_reach(8) - mastery_to_reach(4))  # Level 8: 1 / 1.28 = 0.78.
        self.assertEqual(missions.label(offer), "Easy")
        delivery = Offer(giver.id, "delivery", 1.0, (giver.x + 3000, giver.y), levels={"city": 1})
        self.assertEqual(missions.label(delivery), "Medium")  # Deliveries aren't speed-based.

    def test_offers_saved_without_levels_adopt_the_current_ones(self):
        saved = {"progress": {"city": {"level": 2, "mastery": 7}},
                 "offers": {"city-drag": {"type": "drag", "scale": 1.15, "target": None,
                                          "track": {"kind": "straight", "theme": "city"}, "seed": 1}}}
        missions = Missions(self.world, self.world.seed, saved)
        self.assertEqual(missions.offers["city-drag"].levels["city"], 2)
        # Saved before ratings: 1.15 x 110 = 126.5, kept within +15 of 110.
        self.assertEqual(missions.offers["city-drag"].rating, 125)
        with self.assertRaises(ValueError):
            Offer.from_dict("city-drag", {**saved["offers"]["city-drag"], "levels": {"city": 0}})

    def test_each_decline_offers_an_easy_one_for_half_the_mastery_down_to_one(self):
        missions = Missions(self.world, self.world.seed,
                            {"progress": {"city": {"level": 3, "mastery": 0}}})
        for giver in (g for g in missions.givers if g.region == "city" and not g.harder):
            offer = missions.offer_for(giver)
            self.assertFalse(offer.eased)
            while missions.can_decline(offer):
                offer = missions.decline(giver)
                self.assertTrue(offer.eased)
                self.assertEqual(missions.label(offer), "Easy", giver.id)
                if offer.type == "drag":
                    self.assertLessEqual(offer.rating, missions.progress.rating("city"))
            self.assertIs(missions.decline(giver), offer)          # At 1 mastery: no more.
            self.assertFalse(missions.preview(offer)["can_decline"])
            self.assertIn("reduced reward", missions.preview(offer)["reward"])
        giver = missions.by_id["city-drag"]                          # Level 3: 5 -> 2 -> 1.
        missions.offers.pop(giver.id)
        chain = [missions.reward_for(missions.offer_for(giver), 3)]
        while missions.can_decline(missions.offer_for(giver)):
            chain.append(missions.reward_for(missions.decline(giver), 3))
        self.assertEqual(chain, [5, 2, 1])
        veteran = missions.by_id["city-drag-hard"]
        missions.progress.levels["city"] = 5                          # Veterans: (3+4) x 2 = 14.
        offer = missions.offer_for(veteran)
        self.assertEqual(missions.reward_for(offer, 3), 14)
        self.assertFalse(missions.can_decline(offer))                 # Veterans: no declining.
        self.assertIs(missions.decline(veteran), offer)
        # A straight (or eased) veteran offer from an older save is replaced by a circuit.
        missions.offers[veteran.id] = Offer(veteran.id, "drag", 1.0, None, {"kind": "straight", "theme": "city"},
                                            1, dict(missions.progress.levels), 140, 1)
        offer = missions.offer_for(veteran)
        self.assertEqual((offer.track["kind"], offer.declines), ("circuit", 0))
        missions.progress.levels["city"] = 3
        restored = Missions(self.world, self.world.seed, json.loads(json.dumps(missions.to_dict())))
        self.assertEqual(restored.offers[giver.id].declines, 2)
        missions.accept(giver)
        result = missions.finish_drag(True, "Won")
        self.assertEqual(result["mastery"], 1)
        self.assertFalse(missions.offer_for(giver).eased)             # Full offers return.
        saved = missions.offer_for(giver).to_dict()
        del saved["declines"]
        self.assertEqual(Offer.from_dict(giver.id, {**saved, "eased": True}).declines, 1)  # Older saves.

    def test_bad_saved_offers_are_dropped(self):
        data = {"offers": {"city-drag": {"type": "drag", "scale": 9}, "nobody": {"type": "speed"}}}
        missions = Missions(self.world, self.world.seed, data)
        self.assertEqual(missions.offers, {})
        with self.assertRaises(ValueError):
            Offer.from_dict("city-speed", {"type": "speed", "scale": 1.0, "target": None})

    def test_speed_limit_tightens_with_difficulty(self):
        missions = Missions(self.world, self.world.seed)
        giver = missions.by_id["city-speed"]
        easy = Offer(giver.id, "speed", 0.5, (giver.x + 3000, giver.y))
        hard = Offer(giver.id, "speed", 1.25, (giver.x + 3000, giver.y))
        start = (giver.x, giver.y)
        self.assertAlmostEqual(missions.speed_limit(easy, start) / missions.speed_limit(hard, start), 2.5)


class InWorldMissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def start(self, giver_id, scale=1.0):
        missions = Missions(self.world, self.world.seed)
        giver = missions.by_id[giver_id]
        missions.offers[giver_id] = Offer(giver_id, giver.type, scale, (giver.x + 3000, giver.y))
        missions.accept(giver)
        return missions, giver

    def test_delivery_crashes_cost_points_by_difficulty_with_grace(self):
        missions, giver = self.start("city-delivery", 1.2)  # Hard: -15.
        missions.update(0.1, giver.x, giver.y, True, True, True)
        missions.update(0.1, giver.x, giver.y, True, True, True)   # Within the grace period.
        self.assertEqual((missions.active.points, missions.active.crashes), (85, 1))
        self.assertEqual(missions.status()[1], f"CRASH  -{DELIVERY_PENALTY['Hard']}")
        missions.update(1.0, giver.x, giver.y, True, True, False)
        missions.update(0.1, giver.x, giver.y, True, True, True)
        self.assertEqual(missions.active.points, 70)
        result = missions.update(0.1, giver.x + 3000, giver.y, False, False, False)
        self.assertTrue(result["success"])
        self.assertEqual(result["mastery"], 1)          # 70 points: 50+ tier.
        self.assertNotIn(giver.id, missions.offers)     # A fresh offer is ready.

    def test_delivery_fails_below_fifty_points(self):
        missions, giver = self.start("city-delivery", 0.9)  # Medium: -10.
        result = None
        for _ in range(6):
            result = missions.update(1.1, giver.x, giver.y, True, True, True)
        self.assertFalse(result["success"])
        self.assertIn(giver.id, missions.offers)        # Same offer kept for a retry.

    def test_aborting_fails_the_mission_and_keeps_the_offer(self):
        missions, giver = self.start("city-delivery", 1.0)
        offer = missions.offers[giver.id]
        result = missions.abort()
        self.assertFalse(result["success"])
        self.assertEqual((result["mastery"], result["giver"]), (0, giver))
        self.assertIsNone(missions.active)
        self.assertIs(missions.offers[giver.id], offer)   # Same mission to retry.
        self.assertEqual(missions.progress.completed["city"]["delivery"], 0)

    def test_speed_check_clock_starts_when_driving(self):
        missions, giver = self.start("city-speed", 1.0)
        limit = missions.active.time_limit
        missions.update(5.0, giver.x, giver.y, False, False, False)  # Still on foot.
        self.assertEqual(missions.active.time_left, limit)
        missions.update(1.0, giver.x, giver.y, True, True, False)
        self.assertAlmostEqual(missions.active.time_left, limit - 1.0)
        result = missions.update(0.1, giver.x + 3000, giver.y, True, True, False)
        self.assertTrue(result["success"])
        self.assertEqual(result["mastery"], 1)

    def test_speed_check_times_out(self):
        missions, giver = self.start("city-speed", 1.0)
        missions.update(0.1, giver.x, giver.y, True, True, False)
        result = missions.update(missions.active.time_limit, giver.x, giver.y, True, True, False)
        self.assertFalse(result["success"])

    def test_level_up_from_missions(self):
        missions, giver = self.start("city-delivery", 0.6)
        missions.progress.mastery["city"] = mastery_to_next(1) - 1   # One short of level 2.
        result = missions.update(0.1, giver.x + 3000, giver.y, True, False, False)
        self.assertEqual((result["mastery"], result["levels"], result["level"]), (2, [2], 2))

    def test_mastery_rows_show_levels_bonuses_completions_and_missions(self):
        missions, giver = self.start("city-speed", 1.0)
        missions.update(0.1, giver.x, giver.y, True, True, False)
        missions.update(0.1, giver.x + 3000, giver.y, True, True, False)  # Completed.
        missions.progress.add("desert", mastery_to_reach(5))                # Level 5.
        desert = missions.by_id["desert-delivery"]
        missions.offers[desert.id] = Offer(desert.id, "delivery", 0.9, (desert.x + 3000, desert.y))
        missions.accept(desert)
        rows = {row["region"]: row for row in missions.mastery_rows()}
        self.assertEqual(rows["city"]["completed"], {"delivery": 0, "speed": 1, "drag": 0})
        self.assertEqual((rows["city"]["mastery"], rows["city"]["need"]), (1, 15))
        self.assertEqual((rows["desert"]["level"], rows["desert"]["speed"], rows["desert"]["veterans"]),
                         (5, 16, True))
        self.assertEqual(rows["desert"]["mission"], "Ongoing  ·  Delivery")
        restored = Missions(self.world, self.world.seed, json.loads(json.dumps(missions.to_dict())))
        rows = {row["region"]: row for row in restored.mastery_rows()}
        self.assertEqual(rows["desert"]["mission"], "")          # Ongoing missions aren't saved.
        self.assertEqual(rows["city"]["completed"]["speed"], 1)  # Completions are saved.

    def test_an_ongoing_mission_is_never_saved_and_its_offer_stays(self):
        missions, giver = self.start("city-speed", 1.0)
        offer = missions.offers[giver.id]
        missions.update(3.0, giver.x, giver.y, True, True, False)
        data = json.loads(json.dumps(missions.to_dict()))
        self.assertNotIn("queued", data)
        restored = Missions(self.world, self.world.seed, data)
        self.assertIsNone(restored.active)
        self.assertIsNone(restored.status())
        self.assertEqual(restored.offers[giver.id].to_dict(), offer.to_dict())  # Same mission.
        restored.accept(restored.by_id[giver.id])
        self.assertEqual(restored.active.time_left, restored.active.time_limit)  # Fresh clock.

    def test_old_saves_with_a_queued_mission_load_without_it(self):
        missions = Missions(self.world, self.world.seed, {"queued": "city-drag", "offers": {}})
        self.assertIsNone(missions.active)
        self.assertFalse(hasattr(missions, "queued"))

    def test_player_save_keeps_missions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PlayerSave(Path(temp) / "player.json")
            missions, giver = self.start("city-delivery", 1.0)
            missions.progress.add("snow", mastery_to_reach(3) + 5)
            store.save(Car(), None, missions.to_dict())
            store.load_state(CollisionManager(None, self.world))
            restored = Missions(self.world, self.world.seed, store.missions_data)
            self.assertEqual(restored.progress.levels["snow"], 3)
            self.assertIsNone(restored.active)
            self.assertIn(giver.id, restored.offers)


class FastTravelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_travel_unlocks_at_level_three_and_not_during_missions(self):
        missions = Missions(self.world, self.world.seed)
        missions.progress.add("snow", mastery_to_reach(3))
        rows = {r["region"]: r["travel"] for r in missions.mastery_rows("city")}
        self.assertEqual((rows["snow"], rows["jungle"]), ("ready", "locked"))
        self.assertEqual({r["region"]: r["travel"] for r in missions.mastery_rows("snow")}["snow"], "here")
        giver = missions.by_id["city-speed"]
        missions.offers[giver.id] = Offer(giver.id, "speed", 1.0, (giver.x + 3000, giver.y))
        missions.accept(giver)
        self.assertEqual({r["region"]: r["travel"] for r in missions.mastery_rows("city")}["snow"], "busy")

    def test_parks_in_each_racing_centers_own_lot(self):
        from fast_travel import destination, region_anchor
        from world import TILE_SIZE
        collisions = CollisionManager(None, self.world)
        start = Car(heading=90.0)
        for region in ("city", "jungle", "desert", "snow", "rural"):
            x, y, heading = destination(self.world, collisions, start, region)
            self.assertEqual(self.world.region_at(x, y), region)
            lot = [s for s in self.world.sector(int(x // 512), int(y // 512)) if s.name == "home_lot"]
            self.assertEqual(len(lot), 1)                               # One two-stall lot,
            self.assertEqual((lot[0].x - TILE_SIZE / 2 + 20, heading), (x, 0.0))   # west stall, up.
            self.assertLess(math.dist((x, y), region_anchor(self.world, region)), 400)
            for step in range(0, 97, 8):                                # Clear to drive up and out.
                self.assertTrue(collisions.can_move(Car(x=x, y=y - step, heading=0).collision_record()),
                                (region, step))
            self.assertEqual(start.heading, 90.0)

class DragRaceTests(unittest.TestCase):
    def test_tracks_are_walled_and_tiled(self):
        for kind, shape in (("straight", 0), ("circuit", 0), ("circuit", 1), ("circuit", 2)):
            level = TrackLevel(kind, "desert", shape)
            names = {s.name for s in level.sprites if s.atlas == "track-atlas"}
            self.assertTrue({"track_desert_base", "track_desert_edge", "track_fence",
                             "track_desert_finish"} <= names)
            if kind == "circuit":
                self.assertTrue({"track_desert_corner", "track_desert_inner", "track_tires"} <= names)
            self.assertEqual(level.region_at(*level.path[0]), "desert")  # Desert grip on track.
            # Every track cell is fenced off from the outside world.
            for tx, ty in level.track:
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    cell = (tx + dx, ty + dy)
                    if cell not in level.track:
                        x, y = (cell[0] + 0.5) * TILE_SIZE, (cell[1] + 0.5) * TILE_SIZE
                        self.assertTrue(any(abs(o.x - x) < 1 and abs(o.y - y) < 1
                                            for o in level.nearby_obstacles(x, y)))

    def run_race(self, track, scale, drive):
        race = DragRace(track, scale, 1.0, 3)
        for _ in range(60 * 120):
            throttle, steer = drive(race)
            race.update(1 / 60, throttle, steer)
            if race.result:
                return race
        self.fail("race never finished")

    def test_full_throttle_beats_a_slow_rival_on_the_straight(self):
        race = self.run_race({"kind": "straight", "theme": "city"}, 0.5, lambda r: (1, 0))
        self.assertEqual(race.result, "win")
        self.assertEqual(race.position(), 1)

    def test_a_fast_rival_wins_the_straight(self):
        race = self.run_race({"kind": "straight", "theme": "city"}, 1.25, lambda r: (1, 0))
        self.assertEqual(race.result, "lose")

    def test_straight_rivals_drive_at_their_rating_start_delay_included(self):
        straight = {"kind": "straight", "theme": "city"}
        # The user's race: a 131 rival must beat a flat-out 120, and only a 131 wins.
        for player, result in ((120, "lose"), (129, "lose"), (131, "win")):
            race = DragRace(straight, None, rating_speed(player), 1, rival_rating=131, rival_off_day=0.0)
            for _ in range(60 * 60):
                race.update(1 / 60, 1, 0)
                if race.result:
                    break
            self.assertEqual(race.result, result, player)

    def test_circuit_rivals_lose_to_a_clean_human_lap_ten_below(self):
        circuit = DragRace({"kind": "circuit", "theme": "city", "seed": 370177870, "size": 5}, None, 1.0, 3,
                           rival_rating=125, rival_off_day=0.0)
        clean = lambda rated: flawless_time(circuit.level, multiplier=rating_speed(rated)) * CLEAN_LAP
        seconds = center_rival_time(circuit) + circuit.rival.reaction
        # Never harder than its rating (a clean 115 wins); its wide corners (half of them) can
        # make it easier, but a clean 100 still loses. The user's lap here was ~18.3 s at 120.
        self.assertGreater(seconds, clean(115) * 1.005 - 0.05)
        self.assertLess(seconds, clean(100))

    def test_rated_rivals_never_outrun_their_rating_and_have_off_days(self):
        # The user's report: a 114 circuit rival caught a 120 on the straights.
        circuit = {"kind": "circuit", "theme": "city", "seed": 370177870, "size": 5}
        race = DragRace(circuit, None, rating_speed(120), 3, rival_rating=114, rival_off_day=0.0)
        top, accel = race.rival.top, race.rival.accel
        from drag_race import STRAIGHT_BOOST
        self.assertAlmostEqual(top, 170 * rating_speed(114) * STRAIGHT_BOOST)   # A little quicker on straights,
        self.assertLess(top, 170 * rating_speed(120))                           # still slower than a 120.
        straight = DragRace({"kind": "straight", "theme": "city"}, None, 1.0, 3, rival_rating=114, rival_off_day=0.0)
        self.assertAlmostEqual(straight.rival.top, 170 * rating_speed(114))     # Quarter miles: exactly its rating.
        self.assertEqual(accel, ACCELERATION)          # The player's acceleration: no boost.
        days = {round(DragRace({"kind": "straight", "theme": "city"}, None, 1.0, 3, rival_rating=120).off_day, 3)
                for _ in range(30)}
        self.assertGreater(len(days), 20)               # Rerolled every attempt.
        self.assertTrue(all(0 <= day <= 10 for day in days))
        self.assertEqual([round(off_day(u), 2) for u in (0, 0.5, 1)], [0, 1.25, 10])

    def test_the_rival_is_not_solid_but_walls_are(self):
        straight = {"kind": "straight", "theme": "city"}
        race = DragRace(straight, None, 1.0, 3, rival_rating=100, rival_off_day=0.0)
        race.clock, race.rival.reaction = 0.0, 1e9         # Racing; the rival sits still.
        car = race.car
        race.rival.x, race.rival.y, race.rival.heading = car.x + 45, car.y, car.heading  # Nose to tail.
        car.speed = 150.0
        x0 = car.x
        race.update(1 / 60, 1, 0)
        self.assertGreater(car.x, x0)                      # Straight through the rival.
        self.assertGreater(car.speed, 100)
        self.assertEqual(race.collisions.fixed, [])
        race = DragRace(straight, None, 1.0, 3, rival_rating=100, rival_off_day=0.0)
        race.clock, race.rival.reaction = 0.0, 1e9
        car = race.car
        car.heading, car.speed = 0.0, 150.0                # Straight into the side barrier.
        stopped = False
        for _ in range(120):
            before = car.speed
            race.update(1 / 60, 1, 0)
            if before > 0 and car.speed == 0:
                stopped = True
                break
        self.assertTrue(stopped)

    def test_rival_laps_the_circuit_on_the_track(self):
        race = DragRace({"kind": "circuit", "theme": "snow", "shape": 2}, 1.0, 1.0, 3)
        race.clock = 0.0
        on_track = 0
        steps = 0
        while race.rival.progress < race.level.race_length:
            race.rival.update(1 / 60, 10.0)
            steps += 1
            cell = (int(race.rival.x // TILE_SIZE), int(race.rival.y // TILE_SIZE))
            on_track += cell in race.level.track
        self.assertEqual(on_track, steps)
        self.assertLess(steps / 60, 60)

    def test_circuit_progress_follows_the_lap(self):
        level = TrackLevel("circuit", "city", 0)
        progress = level.progress_of(level.start_x - 96, level.path[0][1], -96)
        self.assertAlmostEqual(progress, -96, delta=1)
        # Walk the centerline all the way round; progress should rise to a full lap.
        x, y = level.path[0]
        for (ax, ay), (bx, by) in level._segments():
            for i in range(1, 41):
                x, y = ax + (bx - ax) * i / 40, ay + (by - ay) * i / 40
                progress = level.progress_of(x, y, progress)
        # Back at the first corner, 256 px short of the start/finish line...
        self.assertAlmostEqual(progress, level.lap_length - (level.start_x - level.path[0][0]), delta=2)
        # ...and crossing the line completes the lap.
        for i in range(1, 11):
            progress = level.progress_of(level.path[0][0] + (level.start_x - level.path[0][0]) * i / 10,
                                         level.path[0][1], progress)
        self.assertAlmostEqual(progress, level.lap_length, delta=2)


if __name__ == "__main__":
    unittest.main()
