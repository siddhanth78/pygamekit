"""The badge board (the bedroom wall at home): a white board with the six top-league
badges in a pyramid, three on top, then two, then one. Earned badges show in their
colors; the rest are grey slots. No text."""

from __future__ import annotations

import moderngl

from gl_utils import build_rect_objs, get_new_instances, load_program, to_gl
from island import BADGES
from world import Sprite


ROWS = (3, 2, 1)                    # Badges per row, top to bottom (bronze first, purple at the tip).
BOARD = 460                         # The white board, px square.
SLOT = 128                          # Badge icon size.
GAP = 136                           # Between slot centers.
WHITE = (247, 247, 242, 255)
FRAME = (201, 206, 210, 255)
SHADOW = (8, 14, 18, 150)


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


def slot_positions(cx: float, cy: float):
    """Screen centers of the six slots, in BADGES order."""
    out = []
    for row, count in enumerate(ROWS):
        y = cy + (row - (len(ROWS) - 1) / 2) * GAP
        out += [(cx + (i - (count - 1) / 2) * GAP, y) for i in range(count)]
    return out


class BadgeBoard:
    def __init__(self, ctx, toolkit_root, viewport, state):
        self.viewport = viewport
        self.state = state                     # GameState: draws the badge icons.
        self.open = False
        self.earned: list[str] = []
        shaders = toolkit_root / "shaders"
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.rect_program["u_viewport_size"].value = tuple(float(v) for v in viewport)
        self.rect_instances = get_new_instances(8, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)

    def show(self, earned):
        self.open, self.earned = True, list(earned)

    def handle(self, action, value):
        """Returns "close" when the board is closed."""
        if action in ("pause", "interact", "confirm", "click"):
            self.open = False
            return "close"
        return None

    def icons(self):
        """The six badge sprites (earned in color, the rest grey), in screen space."""
        cx, cy = self.viewport[0] / 2, self.viewport[1] / 2
        return [Sprite("home-atlas", f"badge_{name}" if name in self.earned else "badge_empty", x, y, SLOT, SLOT)
                for (name, _), (x, y) in zip(BADGES, slot_positions(cx, cy))]

    def render(self):
        width, height = self.viewport
        cx, cy = width // 2, height // 2
        rects = [_rect(cx, cy, width, height, (8, 16, 22, 170)),
                 _rect(cx + 8, cy + 10, BOARD, BOARD, SHADOW),
                 _rect(cx, cy, BOARD + 16, BOARD + 16, FRAME),
                 _rect(cx, cy, BOARD, BOARD, WHITE)]
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        self.state.render(self.icons(), 0.0, 0.0, [], 1.0)
