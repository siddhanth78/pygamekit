"""Inventory: stacks in a 4 x 4 grid, at most 999 of each; fish and points for now."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from car import Car
from collision_manager import CollisionManager
from fishing import VALUE, FishingSession, FishLog
from inventory import GRID, ITEMS, STACK_MAX, TOKEN_MAX, contents
from inventory_ui import wrap
from missions import Missions
from player_save import PlayerSave
from world import TILE_SIZE, World


MANIFEST = json.loads((PROJECT_ROOT / "assets" / "atlas-manifest.json").read_text())["atlases"]


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()

    def missions(self, data=None):
        return Missions(self.world, self.world.seed, data)

    def test_holds_fish_and_unspent_points_in_order_skipping_empties(self):
        m = self.missions()
        self.assertEqual([(item.id, n) for item, n in contents(m)], [("sunside_tokens", 0)])
        m.fish.add("epic")
        m.fish.add("common")
        m.fish.add("common")
        m.unspent = 7
        self.assertEqual([(item.id, n) for item, n in contents(m)],
                         [("sunside_tokens", 0), ("fish_common", 2), ("fish_epic", 1), ("mastery_points", 7)])
        self.assertEqual(GRID, (4, 4))
        self.assertLessEqual(len(ITEMS), GRID[0] * GRID[1])
        for item in ITEMS:                                   # Every icon exists.
            self.assertIn(item.sprite, MANIFEST[item.atlas]["sprites"])

    def test_every_stack_stops_at_999(self):
        log = FishLog({"bag": {"rare": 5000}})
        self.assertEqual(log.bag["rare"], STACK_MAX)         # Old saves clamp.
        self.assertFalse(log.add("rare"))                    # Full: not added,
        self.assertEqual(log.caught["rare"], 0)              # and not counted as caught.
        self.assertTrue(log.add("common"))
        self.assertEqual(self.missions({"unspent_mastery": 12345}).unspent, STACK_MAX)

    def test_a_full_bag_releases_the_catch(self):
        dock = self.world.docks[0]
        ex, ey = dock.end
        log = FishLog({"bag": {r: STACK_MAX for r in ("common", "uncommon", "rare", "epic")}})
        session = FishingSession(dock, (ex + 0.5) * TILE_SIZE, (ey + 0.5) * TILE_SIZE, random.Random(2))
        session.phase, session.timer, session.rarity, session.name = "reel", 99.0, "rare", "Swordfish"
        self.assertIsNone(session.update(1 / 60, log))       # Nothing landed in the bag.
        self.assertTrue(session.bag_full)
        self.assertIn("Bag full", session.status()[0])

    def test_trades_stop_before_points_overflow(self):
        m = self.missions()
        m.unspent = STACK_MAX - 7
        for _ in range(3):
            m.fish.add("epic")                               # 5 points each.
        for _ in range(4):
            m.fish.add("common")
        count, points = m.trade_fish()
        self.assertEqual((count, points), (3, 7))            # One epic (5) and two common (2).
        self.assertEqual(m.unspent, STACK_MAX)
        self.assertEqual((m.fish.bag["epic"], m.fish.bag["common"]), (2, 2))
        self.assertEqual(m.trade_fish(), (0, 0))             # Full: nothing more.

    def test_sunside_tokens_start_at_zero_hold_slot_one_and_are_saved(self):
        m = self.missions()
        self.assertEqual(m.tokens, 0)
        self.assertEqual(contents(m)[0][0].id, "sunside_tokens")   # First, even at 0.
        self.assertEqual(m.add_item("sunside_tokens", 25), 25)
        self.assertEqual(m.add_item("sunside_tokens", 5000), 5000)   # Past 999: the currency
        self.assertEqual(m.add_item("sunside_tokens", 10 ** 7), TOKEN_MAX - 5025)   # stops at TOKEN_MAX.
        self.assertEqual(m.add_item("sunside_tokens", -10), -10)
        self.assertEqual(m.tokens, TOKEN_MAX - 10)
        with tempfile.TemporaryDirectory() as temp:
            store = PlayerSave(Path(temp) / "player.json")
            store.save(Car(), None, m.to_dict())
            store.load_state(CollisionManager(None, self.world))
            self.assertEqual(self.missions(store.missions_data).tokens, TOKEN_MAX - 10)
        self.assertEqual(m.add_item("sunside_tokens", -10 ** 7), -(TOKEN_MAX - 10))
        self.assertEqual((m.tokens, contents(m)[0][1]), (0, 0))   # Back to 0, still slot one.

    def test_passes_stop_at_one_other_items_at_999(self):
        m = self.missions()
        self.assertEqual(m.add_item("island_pass", 3), 1)
        self.assertEqual(m.add_item("seeds_corn", 5000), STACK_MAX)

    def test_saved_items_are_checked(self):
        m = self.missions({"inventory": {"sunside_tokens": 3, "not_an_item": 2, "fish_epic": -1}})
        self.assertEqual(m.items, {"sunside_tokens": 3})     # Known ids, positive counts only.

    def test_notes_wrap_into_the_details_panel(self):
        for item in ITEMS:
            lines = wrap(item.note)
            self.assertEqual(" ".join(l for l in lines if l), item.note)
            self.assertTrue(all(len(l) <= 26 for l in lines))


if __name__ == "__main__":
    unittest.main()
