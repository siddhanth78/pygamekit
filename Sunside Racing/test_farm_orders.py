"""Farm buyers: their orders, prices, payment, saving, and delivery."""

import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from farm_orders import GOODS, MAX_EACH, MAX_KINDS, PRICES, REGIONS, Order, order_value
from inventory import STACK_MAX
from missions import Missions
from world import CENTERS, SECTOR_SIZE, World


class OrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_one_buyer_per_mainland_region_with_a_valid_order(self):
        m = Missions(self.world, self.world.seed)
        self.assertEqual(sorted(m.orders.orders), sorted(REGIONS))
        for region, order in m.orders.orders.items():
            self.assertEqual(self.world.region_at(order.x, order.y), region)
            self.assertFalse(self.world.on_highway(order.x, order.y))
            for c in CENTERS:
                self.assertGreater(math.dist((order.x, order.y), self.world.center_position(*c)), SECTOR_SIZE)
            self.assertTrue(1 <= len(order.goods) <= MAX_KINDS)
            self.assertTrue(all(g in GOODS and 1 <= n <= MAX_EACH for g, n in order.goods.items()))
            km = math.dist((order.x, order.y), self.world.farm.door) / 10000
            self.assertEqual(order.value, order_value(order.goods, km))
            self.assertIn(order.pay, ("tokens", "mastery"))
        again = Missions(self.world, self.world.seed)                   # Seeded.
        self.assertEqual(again.orders.to_dict(), m.orders.to_dict())

    def test_prices_distance_bonus_and_mastery_rate(self):
        self.assertEqual(PRICES, {"corn": 100, "tomato": 120, "lettuce": 140, "milk": 40, "eggs": 25})
        self.assertEqual(order_value({"corn": 2, "milk": 1}, 0), 240)
        self.assertEqual(order_value({"corn": 2, "milk": 1}, 2.5), 300)  # +10% per km.
        self.assertEqual(Order("city", 0, 0, {"corn": 4}, "mastery", 400).points, 20)

    def test_deliver_pays_takes_the_goods_and_moves_the_buyer(self):
        m = Missions(self.world, self.world.seed)
        orders = m.orders
        region = "snow"
        order = orders.orders[region]
        orders.take(region)
        self.assertEqual(orders.missing(m, order), order.goods)
        self.assertIn("don't have everything", orders.deliver(m, region))
        for good, n in order.goods.items():
            m.add_item(good, n + 1)                                   # One spare of each.
        for pay in ("tokens", "mastery"):
            order = orders.orders[region]
            order.pay = pay
            for good, n in order.goods.items():
                m.items[good] = n + 1
            tokens, points = m.tokens, m.unspent
            self.assertIsNone(orders.deliver(m, region))
            if pay == "tokens":
                self.assertEqual(m.tokens, tokens + order.value)
            else:
                self.assertEqual(m.unspent, points + order.points)    # Universal, in the inventory.
            self.assertTrue(all(m.items[g] == 1 for g in order.goods))
            self.assertIsNone(orders.active)
            new = orders.orders[region]
            self.assertEqual(self.world.region_at(new.x, new.y), region)
            self.assertNotEqual((new.x, new.y), (order.x, order.y))

    def test_full_mastery_blocks_a_mastery_delivery(self):
        m = Missions(self.world, self.world.seed)
        order = m.orders.orders["city"]
        order.pay = "mastery"
        for good, n in order.goods.items():
            m.add_item(good, n)
        m.unspent = STACK_MAX
        self.assertIn("full", m.orders.deliver(m, "city"))
        self.assertIs(m.orders.orders["city"], order)

    def test_saved_and_bad_saves(self):
        m = Missions(self.world, self.world.seed)
        m.orders.take("desert")
        data = json.loads(json.dumps(m.to_dict()))
        again = Missions(self.world, self.world.seed, data)
        self.assertEqual(again.orders.to_dict(), m.orders.to_dict())
        self.assertEqual(again.orders.active, "desert")
        data["farm_orders"]["orders"]["city"] = {"x": 1, "y": 2, "goods": {"gold": 1}, "pay": "tokens", "value": 5}
        data["farm_orders"]["active"] = "island"
        broken = Missions(self.world, self.world.seed, data)
        self.assertIn("city", broken.orders.orders)                    # Replaced with a new one.
        self.assertNotIn("gold", broken.orders.orders["city"].goods)
        self.assertIsNone(broken.orders.active)


if __name__ == "__main__":
    unittest.main()
