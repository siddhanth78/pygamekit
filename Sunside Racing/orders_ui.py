"""Orders overlay (O): every open order (farm buyers for now), which one is being worked
on, and whether the goods are in the inventory. ENTER (or a click) works on the chosen
order, pointing the guide arrow at its buyer."""

from __future__ import annotations

import moderngl

from gl_utils import build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances, load_program, to_gl
from ui_text import DynamicLabel


INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
GOOD = (140, 214, 150)
PANEL = (26, 40, 48, 242)
ROW = (36, 54, 62, 255)
SHADOW = (8, 14, 18, 150)

PANEL_SIZE = (1000, 600)
MAX_ROWS = 5
ROW_H, ROW_GAP = 80, 12
FIRST_ROW = 150                 # Row center from the panel's top.


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


class OrdersMenu:
    def __init__(self, ctx, toolkit_root, viewport):
        self.viewport = viewport
        self.open = False
        self.selected = 0
        self.rows: list[dict] = []
        self.empty = ""
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(48, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.title = DynamicLabel(ctx, (400, 56), 50, bold=True, align="center")
        self.title.set("ORDERS")
        self.names = [DynamicLabel(ctx, (300, 30), 25, bold=True) for _ in range(MAX_ROWS)]
        self.wants = [DynamicLabel(ctx, (560, 26), 21) for _ in range(MAX_ROWS)]
        self.pays = [DynamicLabel(ctx, (300, 24), 20, bold=True, align="right") for _ in range(MAX_ROWS)]
        self.wheres = [DynamicLabel(ctx, (300, 24), 19, align="right") for _ in range(MAX_ROWS)]
        self.chips = [DynamicLabel(ctx, (120, 22), 18, bold=True, align="center") for _ in range(MAX_ROWS)]
        self.note = DynamicLabel(ctx, (800, 30), 24, align="center")
        self.hint = DynamicLabel(ctx, (800, 22), 18, align="center")
        self.hint.set("ARROWS choose  ·  ENTER work on this order  ·  O or ESC close")
        self.quads = {}
        for label in (self.title, *self.names, *self.wants, *self.pays, *self.wheres, *self.chips,
                      self.note, self.hint):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    # State ----------------------------------------------------------------------------

    def show(self, rows: list[dict], empty: str = ""):
        """rows: {region, title, wants, pays, where, active, ready}; empty: shown when there
        are no rows (e.g. before the farm is the player's)."""
        self.open = True
        self.rows = rows[:MAX_ROWS]
        self.empty = empty
        active = next((i for i, r in enumerate(self.rows) if r["active"]), 0)
        self.selected = active

    def _frame(self):
        width, height = self.viewport
        return width // 2 - PANEL_SIZE[0] // 2, height // 2 - PANEL_SIZE[1] // 2

    def row_centers(self):
        left, top = self._frame()
        return [(left + PANEL_SIZE[0] // 2, top + FIRST_ROW + i * (ROW_H + ROW_GAP)) for i in range(len(self.rows))]

    # Input ------------------------------------------------------------------------------

    def handle(self, action, value):
        """Returns ("take", region), "close", or None."""
        if action in ("orders", "pause"):
            self.open = False
            return "close"
        if not self.rows:
            return None
        if action == "menu_up":
            self.selected = (self.selected - 1) % len(self.rows)
        elif action == "menu_down":
            self.selected = (self.selected + 1) % len(self.rows)
        elif action in ("confirm", "interact"):
            return self._take()
        elif action in ("pointer", "click"):
            records = [_rect(x, y, PANEL_SIZE[0] - 60, ROW_H, (0, 0, 0, 0)) for x, y in self.row_centers()]
            hits = check_mouse_collisions(*value, records, "rect")
            if hits:
                self.selected = hits[0]
                if action == "click":
                    return self._take()
        return None

    def _take(self):
        self.open = False
        return ("take", self.rows[self.selected]["region"])

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
        labels = [(self.title, self.title.record(cx, top + 58, ACCENT)),
                  (self.hint, self.hint.record(cx, top + ph - 26, MUTED))]
        row_w = pw - 60
        x0 = left + 30
        for i, ((x, y), row) in enumerate(zip(self.row_centers(), self.rows)):
            if i == self.selected:
                rects.append(_rect(x, y, row_w + 8, ROW_H + 8, (*ACCENT, 255)))
            rects.append(_rect(x, y, row_w, ROW_H, ROW))
            name, wants, pays, where, chip = self.names[i], self.wants[i], self.pays[i], self.wheres[i], self.chips[i]
            name.set(row["title"])
            wants.set(row["wants"])
            pays.set(row["pays"])
            where.set(row["where"])
            labels += [(name, name.record(x0 + 20, y - 16, CREAM)),
                       (wants, wants.record(x0 + 20, y + 18, MUTED)),
                       (pays, pays.record(x0 + row_w - 16, y - 16, ACCENT)),
                       (where, where.record(x0 + row_w - 16, y + 18, MUTED))]
            status = "WORKING ON" if row["active"] else "READY" if row["ready"] else ""
            if status:
                chip.set(status)
                color = ACCENT if row["active"] else GOOD
                rects.append(_rect(x0 + 380, y - 16, 124, 26, (*color, 255)))
                labels.append((chip, chip.record(x0 + 380, y - 15, INK)))
        if not self.rows:
            self.note.set(self.empty)
            labels.append((self.note, self.note.record(cx, cy, MUTED)))
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)
