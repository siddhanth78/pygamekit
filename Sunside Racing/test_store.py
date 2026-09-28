"""The General Store: where it stands, its cart rules, and its level."""

import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from collision_manager import CollisionManager
from inventory import BY_ID, STACK_MAX
from missions import Missions
from pedestrians import Pedestrians
from store import AISLES, Cart, StoreInterior
from walker import Walker
from world import CENTERS, SECTOR_SIZE, World


MANIFEST = json.loads((PROJECT_ROOT / "assets" / "atlas-manifest.json").read_text())["atlases"]
PRICES = {"seeds_corn": 10, "seeds_tomato": 20, "seeds_lettuce": 30, "super_fertilizer": 50, "cow_feed": 10, "hen_feed": 10,
          "fair_ticket": 100, "factory_pass": 20_000, "island_pass": 100_000}


class PlaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_one_city_building_two_or_more_blocks_from_the_center_is_the_store(self):
        shop = self.world.general_store
        center = next(s for s, n in CENTERS.items() if n == "center_city")
        self.assertEqual(self.world.region(*shop.sector), "city")
        self.assertGreaterEqual(max(abs(shop.sector[0] - center[0]), abs(shop.sector[1] - center[1])), 2)
        stores = [s for s in self.world.sector(*shop.sector) if s.name == "general_store"]
        self.assertEqual([(s.x, s.y) for s in stores], [(shop.x, shop.y)])
        self.assertEqual(World().general_store, shop)                 # Same every launch.

    def test_shoppers_come_and_go_through_its_door(self):
        shop = self.world.general_store
        peds = Pedestrians(self.world, self.world.seed)
        peds.update(1 / 60, [shop.x, shop.y, 0, 0, 0, 0, 0, 12, 12, 0])
        door_points = [pt for group in peds.groups.values() for p in group
                       for pt in p.path if math.dist(pt, (shop.x, shop.y)) < 60]
        self.assertTrue(door_points)
        collisions = CollisionManager(None, self.world)
        self.assertTrue(collisions.can_walk(Walker(*shop.door).collision_record()))


class CartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def missions(self):
        return Missions(self.world, self.world.seed)

    def test_prices(self):
        self.assertEqual({i: BY_ID[i].price for i in PRICES}, PRICES)
        self.assertEqual([items for _, _, items in AISLES],
                         [("seeds_corn", "seeds_tomato", "seeds_lettuce"), ("super_fertilizer", "cow_feed", "hen_feed"),
                          ("fair_ticket", "factory_pass", "island_pass")])

    def test_passes_are_one_time(self):
        m, cart = self.missions(), Cart()
        self.assertIsNone(cart.take("factory_pass", m))
        self.assertIn("already in your cart", cart.take("factory_pass", m))
        m.add_item("sunside_tokens", 20_000)
        self.assertIsNone(cart.pay(m))
        self.assertEqual((m.tokens, m.items["factory_pass"]), (0, 1))
        self.assertIn("already have", cart.take("factory_pass", m))

    def test_paying_needs_enough_tokens_and_moves_everything_in(self):
        m, cart = self.missions(), Cart()
        for item in ("seeds_corn", "seeds_corn", "fair_ticket"):
            cart.take(item, m)
        self.assertEqual((cart.count, cart.total), (3, 120))
        m.add_item("sunside_tokens", 119)
        self.assertIn("Not enough", cart.pay(m))
        self.assertEqual((cart.count, m.tokens), (3, 119))             # Nothing changed.
        m.add_item("sunside_tokens", 1)
        self.assertIsNone(cart.pay(m))
        self.assertEqual((cart.count, m.tokens, m.items["seeds_corn"], m.items["fair_ticket"]), (0, 0, 2, 1))

    def test_stacks_stop_at_their_limit(self):
        m, cart = self.missions(), Cart()
        m.add_item("seeds_tomato", STACK_MAX - 1)
        self.assertIsNone(cart.take("seeds_tomato", m))
        self.assertIn("can't carry more", cart.take("seeds_tomato", m))


class InteriorTests(unittest.TestCase):
    def setUp(self):
        self.store = StoreInterior()
        self.collisions = CollisionManager(None, self.store)

    def test_every_spot_and_shopper_path_is_walkable(self):
        for spot in self.store.spots:
            self.assertTrue(self.collisions.can_walk(Walker(*spot.stand).collision_record()), spot)
            self.assertIs(self.store.spot_near(*spot.stand), spot)

    def test_prompts_come_from_the_icons_the_counter_and_the_mat(self):
        spots = {s.item or s.kind: s for s in self.store.spots}
        corn, tomato = spots["seeds_corn"], spots["seeds_tomato"]
        # Right beside an icon, from any side of it the player can reach.
        for dx, dy in ((26, 0), (30, -20), (30, 20)):
            self.assertIs(self.store.spot_near(corn.x + dx, corn.y + dy), corn)
        # Walking along the shelf there's no gap: the nearer icon always answers.
        for y in range(int(corn.y) - 30, int(tomato.y) + 30, 4):
            self.assertIn(self.store.spot_near(corn.x + 26, y), (corn, tomato))
        self.assertIs(self.store.spot_near(corn.x + 26, (corn.y + tomato.y) / 2 + 5), tomato)
        self.assertIsNone(self.store.spot_near(corn.x + 80, corn.y))   # Across the aisle.
        cashier = spots["cashier"]
        self.assertIs(self.store.spot_near(cashier.x + 20, cashier.y + 30), cashier)
        door = spots["door"]
        self.assertIs(self.store.spot_near(door.x - 40, door.y + 15), door)   # Anywhere on the mat,
        self.assertIsNone(self.store.spot_near(*self.store.entry))            # not before stepping on.
        for person in self.store.people:
            for point in person.path:
                self.assertTrue(self.collisions.can_walk(Walker(*point).collision_record()), person.kind)

    def test_aisles_are_named_and_every_sprite_exists(self):
        spots = {s.item: s for s in self.store.spots if s.kind == "product"}
        self.assertEqual(self.store.aisle_at(*spots["seeds_corn"].stand), "Seeds aisle")
        self.assertEqual(self.store.aisle_at(*spots["super_fertilizer"].stand), "Items aisle")
        self.assertEqual(self.store.aisle_at(*spots["island_pass"].stand), "Tickets aisle")
        for _ in range(600):
            self.store.update(1 / 60)
        for sprite in self.store.visible_sprites():
            self.assertIn(sprite.name, MANIFEST[sprite.atlas]["sprites"])

    def test_the_cashier_stays_and_shoppers_come_and_go(self):
        cashier, *others = self.store.people
        self.assertTrue(cashier.still)
        hidden = set()
        for _ in range(60 * 60):
            self.store.update(1 / 60)
            hidden |= {id(p) for p in others if p.inside}
        self.assertEqual(len(hidden), 2)                              # The two who come and go.

    def test_walls_keep_the_player_in(self):
        walker = Walker(*self.store.entry)
        for _ in range(300):
            walker.update(1 / 60, 0, 1, True, self.collisions)
        self.assertLess(walker.y, self.store.height - 64)


if __name__ == "__main__":
    unittest.main()
