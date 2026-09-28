"""The stone market board (inside the factory): today's price of each stone, its margin
over the cost to make it, and a bar chart of the last shipments' margins."""

from __future__ import annotations

import moderngl

from factory import HISTORY, MARGIN_RANGE, MASTERY, STONE_NAMES, STONES, stone_cost
from gl_utils import build_rect_objs, build_tex_objs, get_new_instances, load_program, to_gl
from ui_text import DynamicLabel
from world import Sprite


INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
UP = (111, 208, 138)
DOWN = (226, 110, 96)
PANEL = (26, 40, 48, 242)
CARD = (36, 54, 62, 255)
SCREEN = (22, 36, 42, 255)
SHADOW = (8, 14, 18, 150)

PANEL_SIZE = (1000, 580)
CARD_W, CARD_H, CARD_GAP = 220, 400, 20
CHART_W, CHART_H = 190, 120


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


def margin_text(margin: float) -> str:
    pct = round(margin * 100)
    return f"+{pct}% over cost" if pct >= 0 else f"{-pct}% under cost"


class MarketBoard:
    def __init__(self, ctx, toolkit_root, viewport, state):
        self.viewport = viewport
        self.state = state                     # GameState: draws the stone icons.
        self.open = False
        self.market = None
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(24 + len(STONES) * (HISTORY + 6), 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.title = DynamicLabel(ctx, (500, 56), 46, bold=True, align="center")
        self.title.set("STONE MARKET")
        self.subtitle = DynamicLabel(ctx, (900, 24), 19, align="center")
        low, high = (round(v * 100) for v in MARGIN_RANGE)
        self.subtitle.set(f"Price = cost to make + the market ({low}% to +{high}%). It moves after every shipment.")
        self.names = [DynamicLabel(ctx, (CARD_W, 28), 23, bold=True, align="center") for _ in STONES]
        self.prices = [DynamicLabel(ctx, (CARD_W, 40), 34, bold=True, align="center") for _ in STONES]
        self.margins = [DynamicLabel(ctx, (CARD_W, 24), 20, bold=True, align="center") for _ in STONES]
        self.costs = [DynamicLabel(ctx, (CARD_W, 22), 17, align="center") for _ in STONES]
        self.captions = [DynamicLabel(ctx, (CARD_W, 20), 15, align="center") for _ in STONES]
        self.hint = DynamicLabel(ctx, (600, 22), 18, align="center")
        self.hint.set("E or ESC close")
        self.quads = {}
        for label in (self.title, self.subtitle, *self.names, *self.prices, *self.margins, *self.costs,
                      *self.captions, self.hint):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    def show(self, market):
        self.open, self.market = True, market

    def handle(self, action, value):
        """Returns "close" when the board is closed."""
        if action in ("pause", "interact", "confirm", "click"):
            self.open = False
            return "close"
        return None

    def render(self):
        width, height = self.viewport
        pw, ph = PANEL_SIZE
        cx, cy = width // 2, height // 2
        top = cy - ph // 2
        rects = [_rect(cx, cy, width, height, (8, 16, 22, 170)),
                 _rect(cx + 8, cy + 10, pw, ph, SHADOW),
                 _rect(cx, cy, pw + 8, ph + 8, (*ACCENT, 255)),
                 _rect(cx, cy, pw, ph, PANEL),
                 _rect(cx, top + 6, pw, 12, (*ACCENT, 255))]
        labels = [(self.title, self.title.record(cx, top + 50, ACCENT)),
                  (self.subtitle, self.subtitle.record(cx, top + 92, MUTED)),
                  (self.hint, self.hint.record(cx, top + ph - 22, MUTED))]
        icons = []
        low, high = MARGIN_RANGE
        first = cx - (len(STONES) * CARD_W + (len(STONES) - 1) * CARD_GAP) // 2 + CARD_W // 2
        for i, stone in enumerate(STONES):
            x = first + i * (CARD_W + CARD_GAP)
            y0 = top + 122                                       # The card's top.
            rects.append(_rect(x, y0 + CARD_H // 2, CARD_W, CARD_H, CARD))
            icons.append(Sprite("factory-atlas", f"stone_{stone}", x, y0 + 40, 64, 64))
            margin = self.market.margin(stone)
            color = UP if margin >= 0 else DOWN
            self.names[i].set(STONE_NAMES[stone])
            self.prices[i].set(f"{self.market.price(stone):,} S")
            self.margins[i].set(margin_text(margin))
            self.costs[i].set(f"Cost {stone_cost(stone):,} S  ·  +{MASTERY[stone]} mastery")
            self.captions[i].set(f"Last {len(self.market.history[stone])} shipments")
            labels += [(self.names[i], self.names[i].record(x, y0 + 88, CREAM)),
                       (self.prices[i], self.prices[i].record(x, y0 + 126, ACCENT)),
                       (self.margins[i], self.margins[i].record(x, y0 + 160, color)),
                       (self.costs[i], self.costs[i].record(x, y0 + 188, MUTED)),
                       (self.captions[i], self.captions[i].record(x, y0 + CARD_H - 18, MUTED))]
            # The chart: one bar per shipment, up from the cost line (green) or down (red).
            chart_top, chart_left = y0 + 214, x - CHART_W // 2
            rects.append(_rect(x, chart_top + CHART_H // 2, CHART_W, CHART_H, SCREEN))
            zero = chart_top + CHART_H * high / (high - low)             # The cost line.
            history = self.market.history[stone]
            slot = CHART_W / HISTORY
            for j, m in enumerate(history):
                bx = chart_left + (HISTORY - len(history) + j + 0.5) * slot
                h = abs(m) / (high - low) * CHART_H
                newest = j == len(history) - 1
                bar = ((*ACCENT, 255) if newest else (*(UP if m >= 0 else DOWN), 255))
                rects.append(_rect(bx, zero - h / 2 if m >= 0 else zero + h / 2, slot - 3, max(2, h), bar))
            rects.append(_rect(x, zero, CHART_W, 2, (*CREAM, 200)))
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        self.state.render(icons, 0.0, 0.0, [], 1.0)
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)
