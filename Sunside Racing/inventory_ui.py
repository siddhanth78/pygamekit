"""Inventory overlay (I): a 7 x 4 grid of stacks and a details panel for the chosen one."""

from __future__ import annotations

import moderngl

from gl_utils import build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances, load_program, to_gl
from inventory import GRID, STACK_MAX, contents
from ui_text import DynamicLabel
from world import Sprite


INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
PANEL = (26, 40, 48, 242)
SLOT = (44, 66, 76, 255)
SLOT_EMPTY = (34, 50, 58, 255)
SHADOW = (8, 14, 18, 150)

PANEL_SIZE = (1190, 640)
SLOT_SIZE = 104
SLOT_GAP = 12
ICON = 72
DETAIL_WIDTH = 260
NOTE_CHARS = 26                 # Characters per line of the item's note.


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


def wrap(text: str, width: int = NOTE_CHARS, lines: int = 3) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(line)
    return (out + [""] * lines)[:lines]


class InventoryMenu:
    def __init__(self, ctx, toolkit_root, viewport, state):
        self.viewport = viewport
        self.state = state               # GameState: draws the item icons.
        self.open = False
        self.selected = 0
        self.stacks = []
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(128, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.title = DynamicLabel(ctx, (400, 56), 50, bold=True, align="center")
        self.title.set("INVENTORY")
        self.counts = [DynamicLabel(ctx, (90, 24), 20, bold=True, align="center")
                       for _ in range(GRID[0] * GRID[1])]
        self.name = DynamicLabel(ctx, (DETAIL_WIDTH, 32), 26, bold=True)
        self.amount = DynamicLabel(ctx, (DETAIL_WIDTH, 26), 22)
        self.note = [DynamicLabel(ctx, (DETAIL_WIDTH, 24), 18) for _ in range(3)]
        self.hint = DynamicLabel(ctx, (600, 22), 18, align="center")
        self.hint.set("ARROWS move  ·  ENTER use  ·  I or ESC close")
        self.quads = {}
        for label in (self.title, *self.counts, self.name, self.amount, *self.note, self.hint):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    # State ----------------------------------------------------------------------------

    def toggle(self, missions=None):
        self.open = not self.open
        if self.open:
            self.selected = 0
            self.refresh(missions)

    def refresh(self, missions):
        self.stacks = contents(missions)

    # Layout ---------------------------------------------------------------------------

    def _frame(self):
        width, height = self.viewport
        return width // 2 - PANEL_SIZE[0] // 2, height // 2 - PANEL_SIZE[1] // 2

    def slot_centers(self):
        left, top = self._frame()
        x0, y0 = left + 48 + SLOT_SIZE // 2, top + 112 + SLOT_SIZE // 2
        return [(x0 + c * (SLOT_SIZE + SLOT_GAP), y0 + r * (SLOT_SIZE + SLOT_GAP))
                for r in range(GRID[1]) for c in range(GRID[0])]

    # Input ------------------------------------------------------------------------------

    def selected_item(self):
        """The Item in the selected slot, or None for an empty slot."""
        return self.stacks[self.selected][0] if self.selected < len(self.stacks) else None

    def handle(self, action, value):
        """Returns 'close' when the inventory closes, 'spend' when the player picks their
        mastery points (the caller opens the spend menu), else None."""
        columns, total = GRID[0], GRID[0] * GRID[1]
        if action in ("inventory", "pause"):
            self.open = False
            return "close"
        if action == "menu_left" and self.selected % columns:
            self.selected -= 1
        elif action == "menu_right" and self.selected % columns < columns - 1:
            self.selected += 1
        elif action == "menu_up" and self.selected >= columns:
            self.selected -= columns
        elif action == "menu_down" and self.selected + columns < total:
            self.selected += columns
        elif action in ("pointer", "click"):
            records = [_rect(x, y, SLOT_SIZE, SLOT_SIZE, (0, 0, 0, 0)) for x, y in self.slot_centers()]
            hits = check_mouse_collisions(*value, records, "rect")
            if hits:
                self.selected = hits[0]
            if action == "click" and hits:
                return self._use()
        elif action in ("confirm", "interact"):
            return self._use()
        return None

    def _use(self):
        """Selecting an item: mastery points open the spend menu; nothing else is usable yet."""
        item = self.selected_item()
        if item and item.id == "mastery_points":
            self.open = False
            return "spend"
        return None

    # Render -------------------------------------------------------------------------------

    def render(self):
        width, height = self.viewport
        left, top = self._frame()
        pw, ph = PANEL_SIZE
        cx, cy = left + pw // 2, top + ph // 2
        rects = [_rect(width // 2, height // 2, width, height, (8, 16, 22, 170)),
                 _rect(cx + 8, cy + 10, pw, ph, SHADOW),
                 _rect(cx, cy, pw + 8, ph + 8, (*ACCENT, 255)),
                 _rect(cx, cy, pw, ph, PANEL),
                 _rect(cx, top + 6, pw, 12, (*ACCENT, 255))]
        icons, labels = [], []
        for i, (x, y) in enumerate(self.slot_centers()):
            filled = i < len(self.stacks)
            if i == self.selected:
                rects.append(_rect(x, y, SLOT_SIZE + 8, SLOT_SIZE + 8, (*ACCENT, 255)))
            rects.append(_rect(x, y, SLOT_SIZE, SLOT_SIZE, SLOT if filled else SLOT_EMPTY))
            if filled:
                item, count = self.stacks[i]
                icons.append(Sprite(item.atlas, item.sprite, x, y - 6, ICON, ICON))
                label = self.counts[i]
                label.set(f"x{count}")
                rects.append(_rect(x + 22, y + 38, 58, 22, (20, 30, 36, 230)))
                labels.append((label, label.record(x + 22, y + 39, ACCENT if count >= item.max_stack else CREAM)))
        detail_x = left + 48 + GRID[0] * (SLOT_SIZE + SLOT_GAP) + 24
        detail_y = top + 112
        rects.append(_rect(detail_x + DETAIL_WIDTH // 2, detail_y + 120, DETAIL_WIDTH + 24, 256, (20, 30, 36, 255)))
        if self.selected < len(self.stacks):
            item, count = self.stacks[self.selected]
            self.name.set(item.name)
            self.amount.set(f"{count:,} / {item.max_stack:,}")
            note = wrap(item.note)
        else:
            self.name.set("Empty slot")
            self.amount.set("")
            note = []
        labels += [(self.title, self.title.record(cx, top + 58, ACCENT)),
                   (self.name, self.name.record(detail_x, detail_y + 24, CREAM)),
                   (self.amount, self.amount.record(detail_x, detail_y + 60, ACCENT)),
                   (self.hint, self.hint.record(cx, top + ph - 26, MUTED))]
        for i, (label, line) in enumerate(zip(self.note, note)):
            label.set(line)
            labels.append((label, label.record(detail_x, detail_y + 104 + i * 26, MUTED)))
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        if icons:
            self.state.render(icons, 0.0, 0.0, [], 1.0)      # Screen-space sprites.
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)
