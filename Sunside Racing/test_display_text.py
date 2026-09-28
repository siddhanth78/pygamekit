"""On-screen text never clips: labels shrink text that's too wide for them."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import pygame

from ui_text import _blit_text


class FitTests(unittest.TestCase):
    def test_long_text_shrinks_to_fit_and_short_text_keeps_its_size(self):
        pygame.init()
        for text in ("TOURNAMENT  ·  RACE 4/4", "17,024 S + 301 mastery  PAID", "OK"):
            surface = pygame.Surface((200, 40), pygame.SRCALPHA)
            _blit_text(surface, text, 36, True, "left", surface.get_rect())
            columns = [x for x in range(200) if any(surface.get_at((x, y))[3] for y in range(40))]
            self.assertTrue(columns)
            self.assertLess(max(columns), 200 - 2, text)            # Nothing runs off the edge.
        small = pygame.Surface((400, 40), pygame.SRCALPHA)
        _blit_text(small, "OK", 36, True, "left", small.get_rect())
        from ui_text import font
        drawn = [x for x in range(400) if any(small.get_at((x, y))[3] for y in range(40))]
        self.assertGreaterEqual(max(drawn) - min(drawn) + 2, font(36, True).size("OK")[0] - 4)   # Unshrunk.


class WordingTests(unittest.TestCase):
    """Player-facing text explains mechanics and costs only: no directions ("the General
    Store sells it", "at the Clubs desk") and nothing about final prizes before they happen."""
    BANNED = ("General Store sells", "General Store,", "the General Store.", "desk first", "at the Clubs desk",
              "at the Tournaments desk", "at the Races desk", "Buy one here", "at the dock outside",
              "depot outside", "cargo dock outside", "Grow it on", "talk to them", "Talk to the center",
              "Earn mastery in this region", "grand prize\"", "Bring me fish", "fish trader\"",
              "ticket counter by the gate", "centers {", "best level {")

    def test_no_directions_or_prize_spoilers(self):
        for path in PROJECT_ROOT.glob("*.py"):
            if path.name.startswith("test_") or path.name == "asset_sources.py":
                continue
            source = path.read_text()
            for phrase in self.BANNED:
                self.assertNotIn(phrase, source, f"{path.name}: {phrase!r}")


if __name__ == "__main__":
    unittest.main()
