"""The Mining Factory: the pass, the depot's tank, stone runs, and shipping."""

import json
import math
import random
import sys
import unittest
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from collision_manager import CollisionManager
from factory import (HISTORY, MARGIN_RANGE, MASTERY, RUN_TIME, RUNS, STONES, TANK_MAX, FactoryInterior,
                     FactoryState, Market, roll, ship, shipment, stone_cost)
from inventory import STACK_MAX
from missions import Missions
from store import Cart
from walker import Walker
from world import CENTERS, World


class StateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def missions(self):
        return Missions(self.world, self.world.seed)

    def test_one_pass_unlocks_it_for_good(self):
        m = self.missions()
        self.assertFalse(m.factory.unlock(m))                      # No pass.
        m.add_item("factory_pass", 1)
        self.assertTrue(m.factory.unlock(m))
        self.assertEqual((m.factory.unlocked, m.items.get("factory_pass", 0)), (True, 0))
        again = Missions(self.world, self.world.seed, json.loads(json.dumps(m.to_dict())))
        self.assertTrue(again.factory.unlocked)
        self.assertIn("already yours", Cart().take("factory_pass", again))   # Not sold again.

    def test_the_depot_turns_corn_into_biofuel_up_to_the_tank(self):
        m = self.missions()
        state = m.factory
        self.assertEqual(state.fill(m), 0)                          # No corn.
        m.add_item("corn", 150)
        m.add_item("tomato", 5)
        self.assertEqual(state.fill(m), 150)
        self.assertEqual((state.tank, m.items.get("corn", 0), m.items["tomato"]), (150, 0, 5))  # Only corn.
        m.add_item("corn", 80)
        self.assertEqual(state.fill(m), 50)                         # The tank holds 200.
        self.assertEqual((state.tank, m.items["corn"]), (TANK_MAX, 30))
        self.assertTrue(state.burn(20))
        self.assertFalse(state.burn(200))
        self.assertEqual(state.tank, 180)
        bad = FactoryState({"tank": 999, "unlocked": "yes"}).to_dict()
        self.assertEqual((bad["unlocked"], bad["tank"]), (False, 0))

    def test_more_biofuel_rarer_stone(self):
        rng = random.Random(1)
        for size, table in RUNS.items():
            counts = Counter(roll(size, rng) for _ in range(4000))
            floor = STONES.index(table[0][0])
            self.assertTrue(all(STONES.index(s) >= floor for s in counts), size)   # Never worse.
            for stone, chance in table:
                self.assertAlmostEqual(counts[stone] / 4000, chance, delta=0.03)
        self.assertEqual(set(RUNS), {2, 5, 10, 20})
        self.assertEqual(RUNS[20], (("gold", 1.0),))

    def test_shipping_pays_the_market_price_and_universal_mastery(self):
        m = self.missions()
        self.assertIn("no stones to ship", ship(m))
        m.add_item("stone_gold", 2)
        m.add_item("stone_iron", 1)
        market = m.factory.market
        gold, iron = market.price("gold"), market.price("iron")
        rows, tokens, mastery = shipment(m)
        self.assertEqual((tokens, mastery), (2 * gold + iron, 2 * 25 + 3))
        before = market.to_dict()
        self.assertIsNone(ship(m, random.Random(4)))
        self.assertEqual((m.tokens, m.unspent), (2 * gold + iron, 53))
        self.assertEqual(shipment(m)[0], [])
        after = market.to_dict()
        self.assertTrue(all(len(after[s]) == len(before[s]) + 1 for s in STONES))   # The market moved.
        m.add_item("stone_gold", 1)
        m.unspent = STACK_MAX
        self.assertIn("full", ship(m))
        self.assertEqual(m.items["stone_gold"], 1)                  # Nothing shipped, no move.
        self.assertEqual(market.to_dict(), after)

    def test_prices_are_cost_plus_a_market_margin(self):
        from factory import CORN_COST, FLOOR_RUN
        self.assertEqual(CORN_COST, 60)                             # Corn seed 10 + fertilizer 50.
        self.assertEqual({s: stone_cost(s) for s in STONES}, {"iron": 120, "copper": 300, "silver": 600, "gold": 1200})
        self.assertEqual(MARGIN_RANGE, (-0.10, 0.20))
        self.assertEqual(MASTERY, {"iron": 3, "copper": 5, "silver": 10, "gold": 25})
        market = Market(rng=random.Random(1))
        rng = random.Random(2)
        seen = []
        for _ in range(300):
            market.shift(rng)
            for stone in STONES:
                margin = market.margin(stone)
                self.assertTrue(-0.10 <= margin <= 0.20)
                self.assertEqual(market.price(stone), round(stone_cost(stone) * (1 + margin)))
                seen.append(margin)
        self.assertLess(min(seen), -0.05)
        self.assertGreater(max(seen), 0.15)
        self.assertEqual(len(market.history["gold"]), HISTORY)       # Only the recent ones.
        again = Market(json.loads(json.dumps(market.to_dict())))
        self.assertEqual(again.to_dict(), market.to_dict())
        bad = Market({"gold": [5.0], "iron": "x"}, random.Random(3))
        self.assertTrue(all(-0.10 <= bad.margin(s) <= 0.20 for s in STONES))


class SiteTests(unittest.TestCase):
    def test_on_the_desert_shore_with_a_dock_and_walkable_spots(self):
        world = World()
        site = world.factory
        self.assertEqual(world.region(*site.sector), "desert")
        self.assertEqual(world.region(*site.beach), "beach")
        self.assertEqual(world.region(*site.sectors()[2]), "sea")
        self.assertFalse(any(s in world.roads.sectors() for s in site.sectors()))
        center = next(s for s, n in CENTERS.items() if n == "center_desert")
        self.assertGreaterEqual(max(abs(site.sector[0] - center[0]), abs(site.sector[1] - center[1])), 3)
        collisions = CollisionManager(None, world)
        for xy in (site.door, site.depot_spot, site.dock_spot):
            self.assertTrue(collisions.can_walk(Walker(*xy).collision_record()), xy)
        self.assertEqual(World(seed=77).factory, site)
        names = [s.name for s in world._generate_sector(*site.sector)]
        self.assertIn("factory_building", names)
        self.assertIn("depot_tank", names)


class InteriorTests(unittest.TestCase):
    def test_the_machine_runs_then_drops_the_stone(self):
        level = FactoryInterior(1)
        collisions = CollisionManager(None, level)
        for spot in level.spots:
            self.assertTrue(collisions.can_walk(Walker(spot.x, spot.y).collision_record()), spot.label)
        self.assertEqual(level.spot_near(*level.entry).kind, "exit")
        level.start(20, random.Random(3))
        got, t = None, 0.0
        while got is None:
            got = level.update(0.1)
            t += 0.1
        self.assertEqual(got, "gold")
        self.assertAlmostEqual(t, RUN_TIME, delta=0.15)
        self.assertIsNone(level.running)


if __name__ == "__main__":
    unittest.main()
