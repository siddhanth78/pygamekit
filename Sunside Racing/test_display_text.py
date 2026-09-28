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


if __name__ == "__main__":
    unittest.main()
