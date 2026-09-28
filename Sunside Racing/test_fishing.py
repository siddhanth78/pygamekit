"""Beach fishing: the fish log, the catch flow, trading for universal points, spending them,
beach fast travel, and the pier and fishing art."""

import json
import math
import random
import sys
import tempfile
import unittest

from inventory import STACK_MAX
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from car import Car
from collision_manager import CollisionManager
from fishing import (ODDS, PIERS, RARITIES, pier_open, STRIKE_TIME, STRIKES, SWEEP, VALUE, FishingSession, FishLog,
                     beach_destination, fishing_spot, roll_rarity, trader_near, trader_spots,
                     trader_sprites)
from missions import Missions
from progression import mastery_to_reach
from player_save import PlayerSave
from world import TILE_SIZE, World


MANIFEST = json.loads((PROJECT_ROOT / "assets" / "atlas-manifest.json").read_text())["atlases"]


def pier_end(dock):
    ex, ey = dock.end
    return (ex + 0.5) * TILE_SIZE, (ey + 0.5) * TILE_SIZE


def run_until(session, log, phase, limit=20.0):
    elapsed = 0.0
    while session.phase != phase and elapsed < limit:
        session.update(1 / 60, log)
        elapsed += 1 / 60
    return session.phase == phase


def hit(session):
    """Put the marker in the middle of the green zone and strike."""
    center = sum(session.zone) / 2
    session.sweep_t = center / SWEEP[session.rarity]
    return session.press()


class FishLogTests(unittest.TestCase):
    def test_round_trip_and_bad_counts(self):
        log = FishLog()
        log.add("epic")
        log.add("common")
        restored = FishLog(json.loads(json.dumps(log.to_dict())))
        self.assertEqual((restored.bag, restored.caught), (log.bag, log.caught))
        self.assertEqual(restored.value, VALUE["epic"] + VALUE["common"])
        broken = FishLog({"bag": {"common": -3, "rare": "2", "epic": 1.5, "uncommon": 2}, "caught": []})
        self.assertEqual(broken.bag, {"common": 0, "uncommon": 2, "rare": 0, "epic": 0})

    def test_taking_the_bag_keeps_the_lifetime_count(self):
        log = FishLog()
        for rarity in ("rare", "rare", "uncommon"):
            log.add(rarity)
        self.assertEqual(log.take_bag(), (3, 2 * VALUE["rare"] + VALUE["uncommon"]))
        self.assertEqual(log.count, 0)
        self.assertEqual(sum(log.caught.values()), 3)

    def test_each_pier_leans_toward_its_fish(self):
        odds = {name: PIERS[name][1] for name in PIERS}
        for mix in odds.values():
            self.assertAlmostEqual(sum(mix.values()), 1.0)
        self.assertEqual(max(odds["west"], key=odds["west"].get), "common")
        self.assertEqual(max(odds["north"], key=odds["north"].get), "uncommon")
        self.assertGreater(odds["east"]["rare"], max(odds["west"]["rare"], odds["north"]["rare"]))
        self.assertGreater(odds["east"]["epic"], max(odds["west"]["epic"], odds["north"]["epic"]))
        rng = random.Random(5)
        for name, mix in odds.items():
            draws = [roll_rarity(rng, mix) for _ in range(20000)]
            for rarity in RARITIES:
                self.assertAlmostEqual(draws.count(rarity) / len(draws), mix[rarity], delta=0.012)

    def test_pier_unlocks(self):
        from progression import Progress
        progress = Progress()
        self.assertEqual([pier_open(progress, n) for n in PIERS], [False, False, False])
        progress.levels["rural"] = 4
        self.assertEqual([pier_open(progress, n) for n in PIERS], [True, False, False])
        progress.levels.update(city=7, snow=7)
        self.assertFalse(pier_open(progress, "north"))            # Only two at 7.
        progress.levels["desert"] = 7
        self.assertEqual([pier_open(progress, n) for n in PIERS], [True, True, False])
        progress.levels.update({region: 12 for region in progress.levels})
        progress.levels["jungle"] = 11
        self.assertFalse(pier_open(progress, "east"))             # One short of every region.
        progress.levels["jungle"] = 12
        self.assertTrue(pier_open(progress, "east"))

    def test_sessions_roll_with_their_piers_odds(self):
        world = World()
        east = next(d for d in world.docks if d.name == "east")
        session = FishingSession(east, *pier_end(east), random.Random(1))
        rolled = []
        for _ in range(400):
            session.phase, session.timer = "wait", 99.0
            session.update(0.0, FishLog())
            rolled.append(session.rarity)
        self.assertGreater(rolled.count("rare") / len(rolled), 0.25)

    def test_rarity_odds(self):
        rng = random.Random(7)
        draws = [roll_rarity(rng) for _ in range(40000)]
        for rarity in RARITIES:
            self.assertAlmostEqual(draws.count(rarity) / len(draws), ODDS[rarity], delta=0.01)


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()
        cls.dock = cls.world.docks[0]

    def session(self, seed=3):
        return FishingSession(self.dock, *pier_end(self.dock), random.Random(seed))

    def test_winning_every_strike_lands_the_fish(self):
        log, session = FishLog(), self.session()
        self.assertEqual(session.phase, "cast")
        self.assertFalse(session.cast())               # Already fishing.
        for strike in range(1, 99):
            self.assertTrue(run_until(session, log, "strike"))
            self.assertEqual(hit(session), "hit")
            if session.phase == "reel":
                break
        self.assertEqual(strike, STRIKES[session.rarity])
        self.assertTrue(run_until(session, log, "caught"))
        self.assertEqual(log.bag[session.rarity], 1)
        self.assertTrue(run_until(session, log, "ready"))
        self.assertTrue(session.cast())

    def test_missing_or_waiting_loses_the_fish(self):
        log, session = FishLog(), self.session()
        run_until(session, log, "strike")
        low, _ = session.zone
        session.sweep_t = max(0.0, low - 0.03) / SWEEP[session.rarity]
        self.assertEqual(session.press(), "miss")
        self.assertEqual(session.phase, "escaped")
        self.assertEqual(session.press(), None)        # Nothing to strike now.
        session.phase = "ready"
        session.cast()
        run_until(session, log, "strike")
        for _ in range(int(STRIKE_TIME * 60) + 2):
            session.update(1 / 60, log)
        self.assertEqual(session.phase, "escaped")
        self.assertEqual(log.count, 0)

    def test_the_bobber_lands_out_at_sea_and_the_line_reaches_it(self):
        log, session = FishLog(), self.session()
        run_until(session, log, "wait")
        self.assertEqual(self.world.region_at(*session.bobber), "sea")
        line = next(s for s in session.sprites() if s.name == "fishing_line")
        self.assertAlmostEqual(line.height, math.dist(session.rod_tip(), session.bobber), places=3)
        self.assertEqual(session.pose(), "player_fish_hold")


class TradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_trades_pay_universal_points_spent_on_any_region(self):
        missions = Missions(self.world, self.world.seed)
        for rarity in ("epic", "epic", "common"):
            missions.fish.add(rarity)
        self.assertEqual(missions.trade_fish(), (3, 11))
        self.assertEqual((missions.fish.count, missions.unspent), (0, 11))
        self.assertEqual(missions.progress.mastery, {r: 0 for r in missions.progress.mastery})
        self.assertEqual(missions.spend("snow", 5), [])
        self.assertEqual(missions.spend("snow", 5), [])
        self.assertEqual(missions.spend("desert", 5), [])  # Only 1 left.
        self.assertEqual((missions.unspent, missions.progress.mastery["desert"]), (0, 1))
        missions.unspent = 5
        self.assertEqual(missions.spend("snow", 5), [2])   # Level 1 needs 15.
        self.assertEqual(missions.spend("city", 1), [])
        self.assertEqual(missions.spend("beach", 1), [])

    def test_universal_points_stack_in_the_inventory_up_to_the_cap(self):
        missions = Missions(self.world, self.world.seed)
        self.assertEqual(missions.add_universal(12), 12)
        missions.spend("city", 5)                            # Partly spent: the rest stays.
        self.assertEqual(missions.unspent, 7)
        missions.unspent = STACK_MAX - 3
        self.assertEqual(missions.add_universal(10), 3)
        self.assertEqual(missions.unspent, STACK_MAX)

    def test_unspent_points_and_the_bag_are_saved(self):
        missions = Missions(self.world, self.world.seed)
        missions.fish.add("rare")
        missions.unspent = 7
        with tempfile.TemporaryDirectory() as temp:
            store = PlayerSave(Path(temp) / "player.json")
            store.save(Car(), None, missions.to_dict())
            saved = json.loads((Path(temp) / "player.json").read_text())["missions"]
            self.assertEqual(saved["unspent_mastery"], 7)
            self.assertEqual(saved["fish"]["bag"]["rare"], 1)
            store.load_state(CollisionManager(None, self.world))
            restored = Missions(self.world, self.world.seed, store.missions_data)
        self.assertEqual((restored.unspent, restored.fish.bag["rare"]), (7, 1))
        for bad in (-2, "7", 3.5, None):
            self.assertEqual(Missions(self.world, self.world.seed, {"unspent_mastery": bad}).unspent, 0)

    def test_mastery_page_beach_row_is_only_fast_travel(self):
        missions = Missions(self.world, self.world.seed)
        beach = missions.mastery_rows("city")[-1]
        self.assertEqual((beach["region"], beach["travel"]), ("beach", "locked"))
        self.assertNotIn("level", beach)                    # No mastery on the beach.
        missions.progress.add("rural", mastery_to_reach(4))  # Level 4 anywhere opens travel.
        self.assertEqual(missions.mastery_rows("city")[-1]["travel"], "ready")
        self.assertEqual(missions.mastery_rows("beach")[-1]["travel"], "ready")  # Another pier.

    def test_every_jungle_camp_has_a_trader_with_a_badge(self):
        jungle = [s for s, region in self.world.camps.items() if region == "jungle"]
        spots = trader_spots(self.world)
        self.assertEqual(len(spots), len(jungle))
        x, y, _ = spots[0]
        self.assertEqual(trader_near(self.world, x + 20, y), (x, y))
        self.assertIsNone(trader_near(self.world, x + 200, y))
        names = [s.name for s in trader_sprites(self.world)]
        self.assertEqual(names.count("fish_trader_idle"), len(jungle))
        self.assertEqual(names.count("icon_fishing"), len(jungle))


class PlacesAndArtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_only_the_end_of_a_pier_is_a_fishing_spot(self):
        for dock in self.world.docks:
            self.assertIs(fishing_spot(self.world, *pier_end(dock)), dock)
            sx, sy = dock.tiles[0]
            self.assertIsNone(fishing_spot(self.world, (sx + 0.5) * TILE_SIZE, (sy + 0.5) * TILE_SIZE))

    def test_beach_travel_parks_on_clear_sand_by_the_nearest_pier(self):
        collisions = CollisionManager(None, self.world)
        start = Car()
        x, y, heading = beach_destination(self.world, collisions, start, start.x, start.y)
        self.assertEqual(self.world.region_at(x, y), "beach")
        self.assertTrue(collisions.can_move(Car(x=x, y=y, heading=heading).collision_record()))
        nearest = min(self.world.docks, key=lambda d: math.dist(d.shore(), (start.x, start.y)))
        self.assertLess(math.dist((x, y), nearest.shore()), 400)

    def test_beach_travel_can_pick_each_pier(self):
        collisions = CollisionManager(None, self.world)
        start = Car()
        for dock in self.world.docks:
            x, y, heading = beach_destination(self.world, collisions, start, start.x, start.y, dock.name)
            self.assertEqual(self.world.region_at(x, y), "beach")
            nearest = min(self.world.docks, key=lambda d: math.dist(d.shore(), (x, y)))
            self.assertIs(nearest, dock)
            self.assertLess(math.dist((x, y), dock.shore()), 400)

    def test_only_people_can_go_out_on_a_pier(self):
        collisions = CollisionManager(None, self.world)
        for dock in self.world.docks:
            for tx, ty in dock.tiles:
                x, y = (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE
                self.assertTrue(collisions.can_walk([x, y, 255, 255, 255, 255, 0, 12, 12, 0.0]))
                self.assertFalse(collisions.can_move(Car(x=x, y=y, heading=dock.heading).collision_record()))

    def test_every_fishing_sprite_is_in_the_atlases(self):
        needed = {("terrain-atlas", "pier_planks"), ("terrain-atlas", "pier_end"),
                  ("marker-atlas", "icon_fishing"), ("people-atlas", "fish_trader_idle"),
                  ("people-atlas", "fishing_line"), ("people-atlas", "fishing_bobber"),
                  ("people-atlas", "fishing_bobber_bite")}
        needed |= {("people-atlas", f"player_fish_{p}") for p in ("cast", "hold", "reel")}
        needed |= {("people-atlas", f"fish_{r}") for r in RARITIES}
        for atlas, name in needed:
            self.assertIn(name, MANIFEST[atlas]["sprites"], f"{atlas}: {name}")

    def test_pier_sectors_draw_with_known_sprites(self):
        for dock in self.world.docks:
            x, y = pier_end(dock)
            sprites = self.world.visible_sprites(x - 400, y - 400, 800, 800)
            names = {s.name for s in sprites if s.atlas == "terrain-atlas"}
            self.assertTrue({"pier_planks", "pier_end"} <= names)
            for sprite in sprites:
                self.assertIn(sprite.name, MANIFEST[sprite.atlas]["sprites"])


if __name__ == "__main__":
    unittest.main()
