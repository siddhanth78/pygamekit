"""The farm: the farmhouse's site, owning it, the plot, and the animals inside."""

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
from farm import FARM_GIFT, FARM_LEVEL, PLOT_SIZE, Farm, FarmInterior, feed, outdoor_sprites, plot_index
from inventory import STACK_MAX
from missions import Missions
from progression import mastery_to_reach
from walker import Walker
from world import CENTERS, FARM_PLOT, SECTOR_SIZE, TILE_SIZE, TILES_PER_SECTOR, World


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def test_farmhouse_sits_on_valid_ground_nearest_the_middle_of_rural(self):
        world, site = self.world, self.world.farm
        sx, sy = site.sector
        around = [(sx + dx, sy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        self.assertTrue(all(world.region(*s) == "rural" for s in around))
        self.assertFalse(any(s in world.roads.sectors() for s in around))     # Off the highway.
        self.assertFalse(any(world._road_style(sx * 8 + i, sy * 8 + j)
                             for i in range(-1, 9) for j in range(-1, 9)))  # And the dirt road.
        center = next(s for s, n in CENTERS.items() if n == "center_rural")
        self.assertGreaterEqual(max(abs(sx - center[0]), abs(sy - center[1])), 2)
        rural = [(x, y) for y in range(64) for x in range(64) if world.region(x, y) == "rural"]
        mid = (sum(x + 0.5 for x, _ in rural) / len(rural), sum(y + 0.5 for _, y in rural) / len(rural))
        self.assertLess(math.dist((sx + 0.5, sy + 0.5), mid), 3)               # Near the middle.
        self.assertEqual(World(seed=12345).farm, site)                        # Same on every seed.

    def test_sector_has_the_house_yard_and_an_empty_mud_plot(self):
        sprites = self.world._generate_sector(*self.world.farm.sector)
        names = [s.name for s in sprites]
        self.assertEqual(names.count("farmhouse"), 1)
        self.assertNotIn("rural_barn", names)                                 # No random farmstead.
        tiles = {(int(s.x // TILE_SIZE), int(s.y // TILE_SIZE)): s.name for s in sprites if s.atlas == "terrain-atlas"}
        plot = self.world.farm.plot_tiles()
        self.assertEqual(len(plot), PLOT_SIZE)
        self.assertTrue(all(tiles[t] == "farm_mud" for t in plot))
        door = self.world.farm.door
        self.assertEqual(tiles[(int(door[0] // TILE_SIZE), int(door[1] // TILE_SIZE))], "dirt_gravel")

    def test_player_can_walk_to_the_door_and_every_plot_tile(self):
        collisions = CollisionManager(None, self.world)
        for x, y in [self.world.farm.door] + [((tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE)
                                              for tx, ty in self.world.farm.plot_tiles()]:
            self.assertTrue(collisions.can_walk(Walker(x, y).collision_record()), (x, y))
        self.assertEqual(plot_index(self.world.farm, *self.world.farm.at(FARM_PLOT[0] + 0.5, FARM_PLOT[1] + 0.5)), 0)
        self.assertIsNone(plot_index(self.world.farm, *self.world.farm.door))

    def test_boards_until_owned_and_crops_on_the_plot(self):
        farm = Farm()
        self.assertEqual([s.name for s in outdoor_sprites(self.world.farm, farm)], ["farmhouse_abandoned"])
        farm.owned = True
        farm.plot[0], farm.plot[6] = ["corn", "sprout"], ["tomato", "ripe"]
        self.assertEqual([s.name for s in outdoor_sprites(self.world.farm, farm)], ["sprout", "plant_tomato"])


class FarmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def missions(self, rural=1):
        return Missions(self.world, self.world.seed,
                        {"progress": {"rural": {"level": rural, "mastery": 0}}})

    def test_the_farmhouse_is_given_at_rural_level_six_with_500_tokens_once(self):
        m = self.missions(FARM_LEVEL - 1)
        self.assertFalse(m.farm.claim(m))
        self.assertEqual((m.farm.owned, m.tokens), (False, 0))
        m.progress.add("rural", mastery_to_reach(FARM_LEVEL) - mastery_to_reach(FARM_LEVEL - 1))
        self.assertTrue(m.farm.claim(m))
        self.assertEqual((m.farm.owned, m.tokens, FARM_GIFT), (True, 500, 500))
        self.assertFalse(m.farm.claim(m))                                     # Only once.
        self.assertEqual(m.tokens, 500)
        again = Missions(self.world, self.world.seed, json.loads(json.dumps(m.to_dict())))
        self.assertTrue(again.farm.owned)
        self.assertFalse(again.farm.claim(again))

    def test_plant_fertilize_harvest(self):
        m = self.missions(FARM_LEVEL)
        farm = m.farm
        self.assertEqual(farm.seeds_owned(m), [])
        self.assertIn("no corn seeds", farm.plant(m, 0, "corn"))
        m.add_item("seeds_corn", 2)
        m.add_item("seeds_lettuce", 1)
        self.assertEqual(farm.seeds_owned(m), ["corn", "lettuce"])
        self.assertIsNone(farm.plant(m, 0, "corn"))
        self.assertEqual((farm.plot[0], m.items["seeds_corn"]), (["corn", "sprout"], 1))
        self.assertIn("already growing", farm.plant(m, 0, "lettuce"))
        self.assertIn("Nothing is ready", farm.harvest(m, 0))
        self.assertIn("need super fertilizer", farm.fertilize(m, 0))
        m.add_item("super_fertilizer", 1)
        self.assertIsNone(farm.fertilize(m, 0))
        self.assertEqual((farm.plot[0], m.items.get("super_fertilizer", 0)), (["corn", "ripe"], 0))
        self.assertIsNone(farm.harvest(m, 0))
        self.assertEqual((farm.plot[0], m.items["corn"]), (None, 1))
        m.items["corn"] = STACK_MAX                                            # Full stack: stays ripe.
        farm.plot[3] = ["corn", "ripe"]
        self.assertIn("can't carry", farm.harvest(m, 3))
        self.assertEqual(farm.plot[3], ["corn", "ripe"])
        again = Missions(self.world, self.world.seed, json.loads(json.dumps(m.to_dict())))
        self.assertEqual(again.farm.plot[3], ["corn", "ripe"])

    def test_bad_saved_farm_is_ignored(self):
        self.assertEqual(Farm({"owned": "yes", "plot": [["weeds", "ripe"]]}).to_dict(),
                         {"owned": False, "plot": [None] * PLOT_SIZE})
        self.assertEqual(Farm({"plot": [["corn", "huge"]] + [None] * 24}).plot[0], None)

    def test_feeding_turns_feed_into_milk_and_eggs(self):
        m = self.missions()
        self.assertIn("cow feed", feed(m, "cow"))
        m.add_item("cow_feed", 2)
        m.add_item("hen_feed", 1)
        self.assertIsNone(feed(m, "cow"))
        self.assertIsNone(feed(m, "cow"))
        self.assertIsNone(feed(m, "hen"))
        self.assertEqual({k: m.items.get(k, 0) for k in ("cow_feed", "hen_feed", "milk", "eggs")},
                         {"cow_feed": 0, "hen_feed": 0, "milk": 2, "eggs": 1})


class InteriorTests(unittest.TestCase):
    def test_cows_hens_and_the_door_are_reachable(self):
        house = FarmInterior(7)
        collisions = CollisionManager(None, house)
        self.assertTrue(collisions.can_walk(Walker(*house.entry).collision_record()))
        self.assertEqual(house.spot_near(*house.entry).kind, "door")
        gate = (8.7 * TILE_SIZE, 3.75 * TILE_SIZE)                          # Into the coop.
        self.assertTrue(collisions.can_walk(Walker(*gate).collision_record()))
        for spot in house.spots:
            if spot.kind == "cow":
                self.assertTrue(collisions.can_walk(Walker(spot.x, spot.y).collision_record()), spot.label)
                self.assertEqual(house.spot_near(spot.x, spot.y).kind, "cow")
        self.assertEqual(len(house.hens), 4)
        for _ in range(3000):                                                 # Hens stay in the coop.
            house.update(1 / 30)
        for hen in house.hens:
            self.assertTrue(9.2 * TILE_SIZE <= hen.x <= 11.8 * TILE_SIZE and 1.4 * TILE_SIZE <= hen.y <= 7.5 * TILE_SIZE)
        hen = house.hens[0]
        self.assertEqual(house.spot_near(hen.x + 20, hen.y).kind, "hen")


if __name__ == "__main__":
    unittest.main()
