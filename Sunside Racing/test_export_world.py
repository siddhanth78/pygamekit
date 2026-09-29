"""The world export for the Godot port: faithful sectors, complete data, deterministic."""

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

import export_world
from world import CENTERS, HOME_SECTOR, SECTORS, World


def close(a, b):
    """Sprite tuples equal, numbers to the export's 2 decimal places."""
    return len(a) == len(b) and all(
        x == y if isinstance(x, str) else math.isclose(x, y, abs_tol=0.006) for x, y in zip(a, b))


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World()                                        # Fresh seed 2026, no cache.
        w = cls.world
        cls.sectors = sorted({HOME_SECTOR, w.general_store.sector, w.farm.sector, w.fair.sector,
                              w.factory.sector, w.mainland_dock, w.island_dock, *list(CENTERS)[:3],
                              next(iter(w.camps)), next(iter(w.island_camps.values())).sector,
                              (0, 0), (30, 20)})
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name) / "export"
        cls.manifest = export_world.export(cls.out, world_json=Path(cls.temp.name) / "none.json",
                                           sectors=cls.sectors)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def load(self, name):
        return json.loads((self.out / name).read_text())

    def test_sectors_rebuild_exactly_what_the_game_draws(self):
        for sx, sy in self.sectors:
            data = self.load(f"world/sectors/sector_{sx}_{sy}.json")
            self.assertEqual(len(data["ground"]), 64)
            rebuilt = export_world.rebuild_sector(data)
            original = [(s.atlas, s.name, s.x, s.y, s.width, s.height, s.rotation, s.solid_width,
                         s.solid_height) for s in self.world.sector(sx, sy)]
            # Same sprites, and within each atlas the same draw order.
            for atlas in {row[0] for row in original}:
                a = [r for r in original if r[0] == atlas]
                b = [r for r in rebuilt if r[0] == atlas]
                self.assertEqual(len(a), len(b), (sx, sy, atlas))
                self.assertTrue(all(close(x, y) for x, y in zip(a, b)), (sx, sy, atlas))

    def test_data_files_are_complete(self):
        regions = self.load("world/regions.json")
        self.assertEqual((len(regions["rows"]), len(regions["rows"][0])), (SECTORS, SECTORS))
        sites = self.load("world/sites.json")
        for key in ("home", "general_store", "farm", "fair", "factory", "piers", "ferry", "centers",
                    "camps", "fish_traders", "island_camps"):
            self.assertIn(key, sites)
        self.assertEqual(len(sites["centers"]), len(CENTERS))
        self.assertEqual(len(sites["island_camps"]), 8)
        self.assertEqual(len(self.load("world/highway.json")["roads"]), len(self.world.highway_roads))
        self.assertGreater(len(self.load("world/traffic.json")["cars"]), 200)
        self.assertGreater(len(self.load("world/pedestrians.json")["groups"]), 100)
        people = self.load("world/people.json")
        self.assertEqual(len(people["givers"]), 30)
        self.assertTrue(all(people["buyer_spots"][r] for r in ("city", "jungle", "desert", "snow", "rural")))
        self.assertEqual(len(list((self.out / "tracks").glob("*.json"))), 5 * 10 + 5)
        self.assertTrue((self.out / ".gdignore").exists() and (self.out / "README.md").exists())

    def test_samples_match_the_world(self):
        points = self.load("samples.json")["points"]
        self.assertGreater(len(points), 7000)
        for p in points[:300] + points[-300:]:
            x, y = p["x"], p["y"]
            self.assertEqual(p["region"], self.world.region_at(x, y))
            self.assertEqual(p["surface"], self.world.surface_at(x, y))
            self.assertEqual(p["ice"], self.world.is_ice(x, y))
        self.assertTrue(any(p["pier"] for p in points))              # Some land on the piers.
        self.assertTrue(any(p["highway"] for p in points))

    def test_the_manifest_lists_every_file_and_the_export_repeats_exactly(self):
        files = {str(p.relative_to(self.out)) for p in self.out.rglob("*")
                 if p.is_file() and p.name != "export_manifest.json"}
        self.assertEqual(set(self.manifest["files"]), files)
        again = export_world.export(Path(self.temp.name) / "again", world_json=Path(self.temp.name) / "none.json",
                                    sectors=self.sectors)
        self.assertEqual(again["files"], self.manifest["files"])


if __name__ == "__main__":
    unittest.main()
