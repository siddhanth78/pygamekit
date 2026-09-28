"""Fishing overlays: the strike bar while a fish fights, and the menu that spends the
universal mastery points fish traders pay."""

from __future__ import annotations

import moderngl

from fishing import STRIKES
from gl_utils import (
    build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances, load_program,
    to_gl,
)
from progression import REGIONS, mastery_to_next, rating
from ui_text import DynamicLabel


INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
GOOD = (86, 176, 104)
PANEL = (26, 40, 48, 242)
HUD_PANEL = (20, 32, 40, 215)
BUTTON = (44, 66, 76, 255)
BUTTON_EDGE = (78, 104, 112, 255)
SHADOW = (8, 14, 18, 150)
RARITY_COLORS = {"common": (154, 163, 168), "uncommon": (63, 127, 208),
                 "rare": (154, 85, 208), "epic": (255, 216, 74)}

BAR_SIZE = (440, 28)
BAR_LIFT = 118        # px from the bottom of the screen to the bar's center (above prompts).

SPEND_PANEL = (820, 600)
SPEND_ROW_GAP = 62
SPEND_BUTTON = (72, 44)
SPEND_AMOUNTS = (1, 5)
DONE_BUTTON = (240, 56)
SPEND_BAR = 220


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


class _Overlay:
    """Shared GL setup: one rect batch and one quad per DynamicLabel."""

    def __init__(self, ctx, toolkit_root, viewport, rect_capacity):
        self.viewport = viewport
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(rect_capacity, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.quads = {}

    def _quads_for(self, ctx, labels):
        for label in labels:
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    def _draw(self, rects, labels):
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)


class StrikeBar(_Overlay):
    """Bottom-center timing bar: the green zone, the sweeping marker, and strike pips."""

    def __init__(self, ctx, toolkit_root, viewport):
        super().__init__(ctx, toolkit_root, viewport, 24)
        self.fish = DynamicLabel(ctx, (440, 28), 22, bold=True, align="center")
        self._quads_for(ctx, (self.fish,))

    def render(self, session):
        if session.phase not in ("strike", "between"):
            return
        width, height = self.viewport
        cx, cy = width // 2, height - BAR_LIFT
        bw, bh = BAR_SIZE
        left = cx - bw / 2
        low, high = session.zone
        total = STRIKES[session.rarity]
        color = RARITY_COLORS[session.rarity]
        rects = [
            _rect(cx + 4, cy + 5 - 14, bw + 24, bh + 64, SHADOW),
            _rect(cx, cy - 14, bw + 24, bh + 64, HUD_PANEL),
            _rect(cx, cy - 14 - (bh + 64) / 2 + 2, bw + 24, 4, (*color, 255)),  # Rarity stripe.
            _rect(cx, cy, bw, bh, (12, 20, 26, 255)),
            _rect(left + (low + high) / 2 * bw, cy, (high - low) * bw, bh, (*GOOD, 255)),
        ]
        if session.phase == "strike":
            x = left + session.marker() * bw
            rects += [_rect(x, cy, 8, bh + 12, (*INK, 255)), _rect(x, cy, 4, bh + 8, (*CREAM, 255))]
        for i in range(total):   # Pips: landed strikes fill in.
            px = cx + (i - (total - 1) / 2) * 22
            rects.append(_rect(px, cy + 26, 14, 8, (*ACCENT, 255) if i < session.strike else BUTTON_EDGE))
        self.fish.set(f"{session.rarity.upper()} FISH")
        self._draw(rects, [(self.fish, self.fish.record(cx, cy - 32, color))])


class SpendMenu(_Overlay):
    """Spend universal mastery: +1 / +5 per region, then DONE. Unspent points are kept.

    handle() returns ("spend", region, amount), "close", or None."""

    def __init__(self, ctx, toolkit_root, viewport):
        super().__init__(ctx, toolkit_root, viewport, 64)
        self.open = False
        self.selected = 0
        self.rows: list[dict] = []
        self.unspent = 0
        self.title = DynamicLabel(ctx, (600, 56), 52, bold=True, align="center")
        self.title.set("SPEND MASTERY")
        self.points = DynamicLabel(ctx, (600, 34), 30, bold=True, align="center")
        self.note = DynamicLabel(ctx, (700, 28), 22, align="center")
        self.status = DynamicLabel(ctx, (700, 28), 24, bold=True, align="center")
        self.names = [DynamicLabel(ctx, (140, 30), 26, bold=True) for _ in REGIONS]
        self.levels = [DynamicLabel(ctx, (110, 30), 24, bold=True) for _ in REGIONS]
        self.progress = [DynamicLabel(ctx, (120, 26), 20) for _ in REGIONS]
        self.buttons = [[DynamicLabel(ctx, SPEND_BUTTON, 26, bold=True, align="center")
                         for _ in SPEND_AMOUNTS] for _ in REGIONS]
        for row in self.buttons:
            for label, amount in zip(row, SPEND_AMOUNTS):
                label.set(f"+{amount}")
        self.done = DynamicLabel(ctx, DONE_BUTTON, 30, bold=True, align="center")
        self.done.set("DONE")
        self._quads_for(ctx, (self.title, self.points, self.note, self.status, *self.names,
                              *self.levels, *self.progress, *(l for r in self.buttons for l in r),
                              self.done))

    # State ----------------------------------------------------------------------------

    def show(self, missions, note: str = ""):
        self.open, self.selected = True, 0
        self.note.set(note or "Unspent points are kept; spend them any time.")
        self.status.set("")
        self.refresh(missions)

    def refresh(self, missions, status: str | None = None):
        progress = missions.progress
        self.unspent = missions.unspent
        self.rows = [{"region": region, "level": progress.levels[region],
                      "mastery": progress.mastery[region],
                      "need": mastery_to_next(progress.levels[region]),
                      "max": progress.is_max(region)} for region in REGIONS]
        self.points.set(f"{self.unspent} point{'s' if self.unspent != 1 else ''} to spend")
        for i, row in enumerate(self.rows):
            self.names[i].set(row["region"].title())
            self.levels[i].set(f"Lvl {row['level']}")
            self.progress[i].set("MAX" if row["max"] else f"{row['mastery']} / {row['need']}")
        if status is not None:
            self.status.set(status)

    @property
    def items(self):
        """(row, amount) for each +button, then "done"."""
        return [(r, amount) for r in range(len(REGIONS)) for amount in SPEND_AMOUNTS] + ["done"]

    # Layout ---------------------------------------------------------------------------

    def _frame(self):
        width, height = self.viewport
        return width // 2, height // 2 - SPEND_PANEL[1] // 2

    def _button_centers(self):
        cx, top = self._frame()
        centers = []
        for r in range(len(REGIONS)):
            y = top + 196 + r * SPEND_ROW_GAP
            centers += [(cx + 230 + c * (SPEND_BUTTON[0] + 14), y) for c in range(len(SPEND_AMOUNTS))]
        return centers + [(cx, top + SPEND_PANEL[1] - 52)]

    def _button_sizes(self):
        return [SPEND_BUTTON] * (len(self.items) - 1) + [DONE_BUTTON]

    # Input ----------------------------------------------------------------------------

    def handle(self, action, value):
        columns = len(SPEND_AMOUNTS)
        last = len(self.items) - 1
        if action == "pause":
            return self._close()
        if action == "menu_up":
            if self.selected == last:
                self.selected = last - columns
            elif self.selected >= columns:
                self.selected -= columns
        elif action == "menu_down":
            self.selected = last if self.selected + columns >= last else self.selected + columns
        elif action in ("menu_left", "menu_right") and self.selected != last:
            row = self.selected // columns
            column = self.selected % columns + (1 if action == "menu_right" else -1)
            self.selected = row * columns + max(0, min(columns - 1, column))
        elif action == "confirm":
            return self._choose(self.selected)
        elif action in ("pointer", "click"):
            records = [_rect(x, y, *size, (0, 0, 0, 0))
                       for (x, y), size in zip(self._button_centers(), self._button_sizes())]
            hits = check_mouse_collisions(*value, records, "rect")
            if hits:
                self.selected = hits[0]
                if action == "click":
                    return self._choose(hits[0])
        return None

    def _choose(self, index):
        item = self.items[index]
        if item == "done":
            return self._close()
        row, amount = item
        if self.unspent <= 0:
            return None
        return ("spend", REGIONS[row], amount)

    def _close(self):
        self.open = False
        return "close"

    # Render ---------------------------------------------------------------------------

    def render(self):
        width, height = self.viewport
        cx, top = self._frame()
        pw, ph = SPEND_PANEL
        cy = top + ph // 2
        rects = [
            _rect(width // 2, height // 2, width, height, (8, 16, 22, 165)),
            _rect(cx + 8, cy + 10, pw, ph, SHADOW),
            _rect(cx, cy, pw + 8, ph + 8, (*ACCENT, 255)),
            _rect(cx, cy, pw, ph, PANEL),
            _rect(cx, top + 6, pw, 12, (*ACCENT, 255)),
        ]
        labels = [(self.title, self.title.record(cx, top + 58, ACCENT)),
                  (self.points, self.points.record(cx, top + 104, CREAM if self.unspent else MUTED)),
                  (self.note, self.note.record(cx, top + 138, MUTED))]
        left = cx - pw // 2 + 40
        centers, can_spend = self._button_centers(), self.unspent > 0
        for r, row in enumerate(self.rows):
            y = top + 196 + r * SPEND_ROW_GAP
            if r % 2 == 0:
                rects.append(_rect(cx, y, pw - 60, SPEND_ROW_GAP - 6, (44, 66, 76, 120)))
            labels += [(self.names[r], self.names[r].record(left, y, CREAM)),
                       (self.levels[r], self.levels[r].record(left + 150, y, ACCENT))]
            bar_left = left + 260
            fraction = 1.0 if row["max"] else min(1.0, row["mastery"] / row["need"])
            rects.append(_rect(bar_left + SPEND_BAR / 2, y - 6, SPEND_BAR, 8, (20, 30, 36, 255)))
            if fraction:
                rects.append(_rect(bar_left + SPEND_BAR * fraction / 2, y - 6, SPEND_BAR * fraction, 8,
                                   (*ACCENT, 255)))
            labels.append((self.progress[r], self.progress[r].record(bar_left, y + 14, MUTED)))
        for i, ((x, y), size) in enumerate(zip(centers, self._button_sizes())):
            chosen = i == self.selected
            live = can_spend or i == len(centers) - 1
            face = (*ACCENT, 255) if chosen and live else BUTTON if live else (32, 46, 54, 255)
            edge = (*ACCENT, 255) if chosen else BUTTON_EDGE
            rects += [_rect(x, y, size[0] + 4, size[1] + 4, edge), _rect(x, y, *size, face)]
            label = self.done if i == len(centers) - 1 else self.buttons[i // len(SPEND_AMOUNTS)][i % len(SPEND_AMOUNTS)]
            ink = INK if chosen and live else CREAM if live else MUTED
            labels.append((label, label.record(x, y + 2, ink)))
        labels.append((self.status, self.status.record(cx, top + ph - 108, GOOD)))
        self._draw(rects, labels)


def level_up_line(region: str, levels) -> str:
    """Status after a spend that levelled a region up."""
    level = levels[-1]
    return f"{region.title()} level {level}!  Rating {rating(level)}"
