"""Pause overlay: Resume / Mastery / Help / Exit, a per-region mastery page, and controls."""

from __future__ import annotations

import moderngl

from fishing import PIER_CATCH, PIERS, pier_requirement
from world import DOCK_SITES
from gl_utils import build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances, load_program, to_gl
from ui_text import DynamicLabel, LabelAtlas


# Text is rendered white into equal atlas cells, then tinted per state by the shader.
LABEL_CELL = (512, 64)
CONTROLS = (
    ("W  /  UP", "Accelerate  ·  walk north"),
    ("S  /  DOWN", "Brake, reverse  ·  walk south"),
    ("A  D  /  LEFT  RIGHT", "Steer  ·  walk west, east"),
    ("SPACE", "Confirm  ·  strike a hooked fish"),
    ("SHIFT", "Run while on foot"),
    ("E", "Get in/out  ·  talk  ·  fish  ·  trade"),
    ("Q", "Call your car (on foot)"),
    ("T", "Sea travel (once unlocked)"),
    ("M", "World map  ·  pick a guide  ·  C clears it"),
    ("I  /  O", "Inventory  ·  orders"),
    ("R", "Unstick yourself nearby"),
    ("ESC", "Pause menu"),
)
LABELS = {
    "paused": ("PAUSED", 72, True, "center"),
    "controls": ("CONTROLS", 60, True, "center"),
    "mastery_title": ("MASTERY", 60, True, "center"),
    "mastery": ("MASTERY", 36, True, "center"),
    "abort": ("ABORT MISSION", 32, True, "center"),
    "quit_race": ("QUIT RACE", 36, True, "center"),
    "resume": ("RESUME", 36, True, "center"),
    "help": ("HELP", 36, True, "center"),
    "exit": ("EXIT", 36, True, "center"),
    "back": ("BACK", 36, True, "center"),
    "spend": ("SPEND POINTS", 32, True, "center"),
    "home": ("GO HOME", 36, True, "center"),
    "docks_title": ("CHOOSE A PIER", 52, True, "center"),
    **{f"dock:{name}": (f"PIER {PIERS[name][0]}  ·  {name.upper()}", 30, True, "center")
       for name, _, _ in DOCK_SITES},
    "hint": ("W/S or arrows to choose  ·  Enter to confirm  ·  Esc to resume", 20, False, "center"),
    "goal": ("Open the map (M) and click a landmark to get a guide arrow.", 24, False, "center"),
    **{f"key{i}": (key, 28, True, "left") for i, (key, _) in enumerate(CONTROLS)},
    **{f"act{i}": (action, 28, False, "left") for i, (_, action) in enumerate(CONTROLS)},
}
PAGES = {"main": ("resume", "mastery", "help", "exit"), "help": ("back",), "mastery": ("back",),
         # Beach travel: pick the pier the car parks beside.
         "docks": tuple(f"dock:{name}" for name, _, _ in DOCK_SITES) + ("back",)}

INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
PANEL = (26, 40, 48, 242)
BUTTON = (44, 66, 76, 255)
BUTTON_EDGE = (78, 104, 112, 255)
KEY_CAP = (44, 66, 76, 255)
SHADOW = (8, 14, 18, 150)

PANEL_SIZE = {"main": (480, 560), "help": (720, 700), "mastery": (1180, 640), "docks": (480, 560)}
# Mastery table: (header, x offset from the panel's left edge).
# Mastery table: (header, x offset from the panel's left edge). Unlocks read in level
# order: fast travel (level 3) before veteran givers (level 5).
MASTERY_COLUMNS = (("REGION", 40), ("LEVEL", 160), ("PROGRESS", 225), ("CENTER", 395),
                   ("RATING", 475), ("COMPLETED", 560), ("MISSION", 755), ("TRAVEL", 950),
                   ("VETERANS", 1050))
COLUMN_WIDTHS = (120, 60, 160, 70, 80, 190, 170, 84, 100)
PROGRESS_COL, CENTER_COL, TRAVEL_COL, VETERANS_COL = 2, 3, 7, 8
BAR_WIDTH = 150
TRAVEL_BUTTON = (84, 36)
TRAVEL_TEXT = {"ready": "TRAVEL", "here": "Here", "busy": "Busy", "locked": "Lvl 3"}
MASTERY_ROW_GAP = 54
MASTERY_FIRST_ROW = 226    # Row centers from the panel's top; the sixth row is the beach.
# COMPLETED splits into sub-columns: (mission type, sub-header), centered this far apart.
COMPLETED_KINDS = (("delivery", "Del"), ("speed", "Time"), ("drag", "Drag"))
COMPLETED_STEP = 62
COMPLETED_COL = 5
MASTERY_BUTTONS_Y = 598    # SPEND POINTS and BACK.
GOOD = (120, 200, 130)
LOCKED = (226, 120, 100)
BUTTON_SIZE = (312, 64)
BUTTON_GAP = 84
ROW_GAP = 30


def _rect(x, y, width, height, rgba, thickness=0.0):
    return [x, y, *rgba, thickness, width, height, 0.0]


class PauseMenu:
    def __init__(self, ctx, toolkit_root, viewport):
        self.ctx = ctx
        self.viewport = viewport
        self.open = False
        self.page = "main"
        self.selected = 0
        shaders = toolkit_root / "shaders"
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        size = tuple(float(v) for v in viewport)
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.labels = LabelAtlas(ctx, LABEL_CELL, LABELS)
        self.text_program["u_atlas_grid"].value = self.labels.grid
        rects, _, texts = get_new_instances(48, 0, 32)
        self.rect_instances, self.text_instances = rects, texts
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, rects)
        self.text_vao, self.text_vbo = build_tex_objs(ctx, self.text_program, texts)
        # Mastery page text changes with play, so each cell is a DynamicLabel with its
        # own quad buffer (rewriting one shared buffer between draws stalls the GPU).
        self.mastery_rows = []
        self.headers = [DynamicLabel(ctx, (160, 24), 20, bold=True) for _ in MASTERY_COLUMNS]
        self.footer = DynamicLabel(ctx, (760, 26), 20, bold=True, align="center")
        self.available = DynamicLabel(ctx, (320, 30), 24, bold=True)   # Unspent mastery points.
        self.available.set("Available points: 0")
        for label, (text, _) in zip(self.headers, MASTERY_COLUMNS):
            label.set(text)
        self.cells = [[DynamicLabel(ctx, (w, 30), 30 if c == 1 else 22,
                                    bold=c in (0, 1, TRAVEL_COL, VETERANS_COL),
                                    align="center" if c == TRAVEL_COL else "left")
                       for c, w in enumerate(COLUMN_WIDTHS)] for _ in range(6)]
        # COMPLETED: Del / Time / Drag sub-headers, and a count under each per row.
        self.sub_headers = [DynamicLabel(ctx, (60, 20), 17, bold=True, align="center")
                            for _ in COMPLETED_KINDS]
        for label, (_, text) in zip(self.sub_headers, COMPLETED_KINDS):
            label.set(text)
        self.done_cells = [[DynamicLabel(ctx, (60, 30), 22, align="center") for _ in COMPLETED_KINDS]
                           for _ in range(6)]
        # Under each pier button: its catch, or what it needs while locked.
        self.docks = {name: False for name, _, _ in DOCK_SITES}
        self.dock_notes = {name: DynamicLabel(ctx, (300, 22), 18, bold=True, align="center")
                           for name in self.docks}
        self.quads = {}
        for label in (self.headers + [self.footer, self.available] + list(self.dock_notes.values())
                      + [cell for row in self.cells for cell in row] + self.sub_headers
                      + [cell for row in self.done_cells for cell in row]):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    def set_mastery(self, rows, footer: str = ""):
        """Rows from Missions.mastery_rows() plus an island status line; labels only
        re-render when their text changes."""
        if getattr(self, "footer", None) is not None:
            self.footer.set(footer)
            points = sum(row.get("unspent", 0) for row in rows)
            self.available.set(f"Available points: {points}")
        # Keep the same button selected when the set of travel buttons changes.
        current = self.items[self.selected] if self.page == "mastery" else None
        self.mastery_rows = rows
        if current is not None:
            items = self.items
            self.selected = items.index(current) if current in items else len(items) - 1
        for done_cells, row in zip(getattr(self, "done_cells", []), rows):
            counts = row.get("completed") or {}
            for label, (kind, _) in zip(done_cells, COMPLETED_KINDS):
                label.set(str(counts[kind]) if kind in counts else "")
        for cells, row in zip(self.cells, rows):
            if row.get("kind") == "beach":
                # No mastery on the beach: only its fast-travel button, once unlocked.
                texts = ("Beach", "", "", "", "", "", "",
                         TRAVEL_TEXT[row["travel"]] if row["travel"] != "locked" else "", "")
                for cell, text in zip(cells, texts):
                    cell.set(text)
                continue
            done = row["completed"]
            texts = (row["region"].title(), str(row["level"]),
                     "MAX" if row.get("max") else f"{row['mastery']} / {row['need']}",
                     f"{row['races']} / 10", str(row["rating"]),
                     "",   # COMPLETED shows as three sub-columns (done_cells).
                     row["mission"] or "—", TRAVEL_TEXT[row["travel"]],
                     "Unlocked" if row["veterans"] else "Lvl 5")
            for cell, text in zip(cells, texts):
                cell.set(text)

    def set_docks(self, docks):
        """(pier name, open) pairs for the pier picker."""
        for name, is_open in docks:
            self.docks[name] = is_open
            self.dock_notes[name].set(PIER_CATCH[name] if is_open else pier_requirement(name))

    @property
    def items(self):
        if self.page == "mastery":
            # A travel button for each region the player can jump to, then Spend and Back.
            spend = ("spend",) if any(row.get("unspent") for row in self.mastery_rows) else ()
            return tuple(f"travel:{row['region']}" for row in self.mastery_rows
                         if row["travel"] == "ready") + spend + ("back",)
        if self.page == "main":
            # While a mission or race is under way, offer to abandon it under Resume.
            # GO HOME (above EXIT) only when nothing is under way, since it would abandon it.
            ongoing = getattr(self, "ongoing", None)
            give_up = {"mission": ("abort",), "race": ("quit_race",)}.get(ongoing, ())
            home = () if ongoing else ("home",)
            return PAGES["main"][:1] + give_up + PAGES["main"][1:-1] + home + PAGES["main"][-1:]
        return PAGES[self.page]

    def set_ongoing(self, kind):
        """kind: None, "mission" (in-world mission), or "race" (center or drag race)."""
        current = self.items[self.selected] if self.open and self.page == "main" else None
        self.ongoing = kind
        if current is not None:
            items = self.items
            self.selected = items.index(current) if current in items else 0

    def _panel_size(self):
        width, height = PANEL_SIZE[self.page]
        if self.page == "main":
            height += BUTTON_GAP * (len(self.items) - len(PAGES["main"]))
        return width, height

    def toggle(self):
        self.open = not self.open
        self.page, self.selected = "main", 0

    def _show(self, page):
        self.page = page
        # On the mastery page Back starts selected, so Enter never travels by accident.
        self.selected = len(self.items) - 1 if page == "mastery" else 0

    def _button_centers(self):
        width, height = self.viewport
        if self.page == "help":
            return [(width // 2, height // 2 + 274)]
        if self.page == "mastery":
            left = width // 2 - PANEL_SIZE["mastery"][0] // 2
            top = height // 2 - PANEL_SIZE["mastery"][1] // 2
            ready = [i for i, row in enumerate(self.mastery_rows) if row["travel"] == "ready"]
            x = left + MASTERY_COLUMNS[TRAVEL_COL][1] + TRAVEL_BUTTON[0] // 2
            bottom = top + MASTERY_BUTTONS_Y
            closing = ([(width // 2 - 180, bottom), (width // 2 + 180, bottom)]
                       if "spend" in self.items else [(width // 2, bottom)])
            return [(x, top + MASTERY_FIRST_ROW + i * MASTERY_ROW_GAP) for i in ready] + closing
        # Keep the column of buttons centered when an extra one is shown.
        first = height // 2 - 60 - BUTTON_GAP * (len(self.items) - len(PAGES["main"])) // 2
        return [(width // 2, first + i * BUTTON_GAP) for i in range(len(self.items))]

    def _button_records(self):
        sizes = [TRAVEL_BUTTON if item.startswith("travel:") else BUTTON_SIZE for item in self.items]
        return [_rect(x, y, *size, (0, 0, 0, 0))
                for (x, y), size in zip(self._button_centers(), sizes)]

    def _choose(self, item):
        """Pages change here; 'resume', 'exit', 'abort', 'quit_race', 'spend',
        'travel:<region>', and 'dock:<pier>' go to the game."""
        if item in ("help", "mastery"):
            self._show(item)
        elif item == "travel:beach":
            self._show("docks")
        elif item.startswith("dock:") and not self.docks.get(item.split(":", 1)[1]):
            return None   # Locked: its note says what it needs.
        elif item == "back" and self.page == "docks":
            self._show("mastery")
            if "travel:beach" in self.items:
                self.selected = self.items.index("travel:beach")
        elif item == "back":
            came_from = self.page
            self._show("main")
            self.selected = self.items.index(came_from)
        else:
            return item
        return None

    def handle(self, action, value):
        """Apply one input intent; return 'resume', 'exit', or None."""
        if action == "pause":
            if self.page != "main":
                return self._choose("back")
            return "resume"
        if action == "menu_up":
            self.selected = (self.selected - 1) % len(self.items)
        elif action == "menu_down":
            self.selected = (self.selected + 1) % len(self.items)
        elif action == "confirm":
            return self._choose(self.items[self.selected])
        elif action in ("pointer", "click"):
            hits = check_mouse_collisions(*value, self._button_records(), "rect")
            if hits:
                self.selected = hits[0]
                if action == "click":
                    return self._choose(self.items[hits[0]])
        return None

    def render(self):
        width, height = self.viewport
        cx, cy = width // 2, height // 2
        panel_w, panel_h = self._panel_size()
        top = cy - panel_h // 2
        rects = [
            _rect(cx, cy, width, height, (8, 16, 22, 165)),                       # Dim the world.
            _rect(cx + 8, cy + 10, panel_w, panel_h, SHADOW),                    # Drop shadow.
            _rect(cx, cy, panel_w + 8, panel_h + 8, (*ACCENT, 255)),             # Accent frame.
            _rect(cx, cy, panel_w, panel_h, PANEL),
            _rect(cx, top + 6, panel_w, 12, (*ACCENT, 255)),                     # Header stripe.
            _rect(cx, top + 136, 132, 4, (*ACCENT, 255)),                        # Title underline.
        ]
        title = {"main": "paused", "help": "controls", "mastery": "mastery_title",
                 "docks": "docks_title"}[self.page]
        texts = [self.labels.record(title, cx, top + 82, ACCENT)]
        cells = []
        if self.page == "main":
            texts.append(self.labels.record("hint", cx, cy + panel_h // 2 - 30, MUTED))
        elif self.page == "mastery":
            cells = self._mastery_table(rects, cx - panel_w // 2, top, panel_w)
        elif self.page == "docks":
            pass   # Each pier's note is added with its button below.
        else:
            key_left, action_left = cx - 320, cx - 50
            for i in range(len(CONTROLS)):
                y = top + 190 + i * ROW_GAP
                rects.append(_rect(key_left + 118, y, 244, 28, KEY_CAP))
                texts.append(self.labels.record(f"key{i}", key_left + 8, y, ACCENT, align="left"))
                texts.append(self.labels.record(f"act{i}", action_left, y, CREAM, align="left"))
            texts.append(self.labels.record("goal", cx, top + 190 + len(CONTROLS) * ROW_GAP, MUTED))
        for i, (x, y) in enumerate(self._button_centers()):
            chosen = i == self.selected
            if self.items[i].startswith("travel:"):
                # Compact table button; its text is the row's TRAVEL cell (drawn later).
                tw, th = TRAVEL_BUTTON
                rects.append(_rect(x, y, tw + 4, th + 4, (*ACCENT, 255) if chosen else BUTTON_EDGE))
                rects.append(_rect(x, y, tw, th, (*ACCENT, 255) if chosen else BUTTON))
                continue
            bw, bh = BUTTON_SIZE
            dock = self.items[i].split(":", 1)[1] if self.items[i].startswith("dock:") else None
            locked = dock is not None and not self.docks[dock]
            face = (32, 46, 54, 255) if locked else (*ACCENT, 255) if chosen else BUTTON
            rects.append(_rect(x + 4, y + 5, bw, bh, SHADOW))
            rects.append(_rect(x, y, bw + 4, bh + 4, (*ACCENT, 255) if chosen else BUTTON_EDGE))
            rects.append(_rect(x, y, bw, bh, face))
            if chosen:
                rects.append(_rect(x - bw // 2 + 14, y, 6, bh - 24,
                                   (*(ACCENT if locked else INK), 255)))  # Selection tick.
            ink = MUTED if locked else INK if chosen else CREAM
            if dock:
                texts.append(self.labels.record(self.items[i], x, y - 8, ink))
                note = self.dock_notes[dock]
                cells.append((note, note.record(x, y + 19, LOCKED if locked else ink)))
                continue
            texts.append(self.labels.record(self.items[i], x, y + 2, ink))

        _, self.rect_instances = to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        _, self.text_instances = to_gl(texts, self.text_instances, "tex")
        self.text_vbo.write(self.text_instances[:len(texts)].tobytes(), offset=0)
        self.labels.texture.use(location=0)
        self.text_vao.render(moderngl.TRIANGLES, instances=len(texts))
        if cells:
            # Dynamic labels are whole textures, not rows of the label atlas.
            self.text_program["u_atlas_grid"].value = (1.0, 1.0)
            for label, record in cells:
                instances, vao, vbo = self.quads[id(label)]
                to_gl([record], instances, "tex")
                vbo.write(instances.tobytes(), offset=0)
                label.texture.use(location=0)
                vao.render(moderngl.TRIANGLES, instances=1)
            self.text_program["u_atlas_grid"].value = self.labels.grid

    def _mastery_table(self, rects, left, top, panel_w):
        """Add the table's rects; return (label, record) pairs for its text."""
        cells = [(self.available, self.available.record(left + 44, top + 82, ACCENT))]  # Beside the title.
        header_y = top + 160
        for label, (_, x) in zip(self.headers, MASTERY_COLUMNS):
            cells.append((label, label.record(left + x, header_y, MUTED)))
        done_x = [left + MASTERY_COLUMNS[COMPLETED_COL][1] + 28 + k * COMPLETED_STEP
                  for k in range(len(COMPLETED_KINDS))]
        for label, x in zip(self.sub_headers, done_x):          # Del  Time  Drag
            cells.append((label, label.record(x, header_y + 24, MUTED)))
        rects.append(_rect(left + panel_w // 2, header_y + 38, panel_w - 60, 2, (*MUTED, 120)))
        for i, (row, labels) in enumerate(zip(self.mastery_rows, self.cells)):
            y = top + MASTERY_FIRST_ROW + i * MASTERY_ROW_GAP
            if i % 2 == 0:
                rects.append(_rect(left + panel_w // 2, y, panel_w - 60, MASTERY_ROW_GAP - 6,
                                   (44, 66, 76, 120)))
            if row.get("kind") == "beach":
                chosen = self.items[self.selected] == "travel:beach"
                travel = (INK if chosen else CREAM) if row["travel"] == "ready" else MUTED
                colors = (CREAM, MUTED, MUTED, MUTED, MUTED, MUTED, MUTED, travel, MUTED)
                for c, (label, (_, x), color) in enumerate(zip(labels, MASTERY_COLUMNS, colors)):
                    x = left + x + (TRAVEL_BUTTON[0] // 2 if c == TRAVEL_COL else 0)
                    cells.append((label, label.record(x, y + (1 if c == TRAVEL_COL else 0), color)))
                continue
            # Progress bar toward the next level.
            bar_x = left + MASTERY_COLUMNS[PROGRESS_COL][1]
            fraction = 1.0 if row.get("max") else min(1.0, row["mastery"] / row["need"])
            half = BAR_WIDTH // 2
            rects.append(_rect(bar_x + half, y + 13, BAR_WIDTH, 8, (20, 30, 36, 255)))
            if fraction:
                rects.append(_rect(bar_x + half - half * (1 - fraction), y + 13, BAR_WIDTH * fraction, 8,
                                   (*ACCENT, 255)))
            chosen = self.items[self.selected] == f"travel:{row['region']}"
            travel = (INK if chosen else CREAM) if row["travel"] == "ready" else MUTED
            colors = (CREAM, ACCENT, CREAM, GOOD if row["races"] >= 10 else CREAM,
                      GOOD if row["speed"] else MUTED, CREAM,
                      ACCENT if row["mission"] else MUTED, travel,
                      GOOD if row["veterans"] else MUTED)
            offsets = (0, 0, -9, 0, 0, 0, 0, 1, 0)
            for c, (label, (_, x), color, dy) in enumerate(zip(labels, MASTERY_COLUMNS, colors, offsets)):
                # The TRAVEL cell is centered on its button; the rest are left-aligned.
                x = left + x + (TRAVEL_BUTTON[0] // 2 if c == TRAVEL_COL else 0)
                cells.append((label, label.record(x, y + dy, color)))
            for label, x, (kind, _) in zip(self.done_cells[i], done_x, COMPLETED_KINDS):
                cells.append((label, label.record(x, y, CREAM if row["completed"][kind] else MUTED)))
        # Elite Island progress under the table.
        cells.append((self.footer, self.footer.record(left + panel_w // 2,
                                                      top + MASTERY_FIRST_ROW + 6 * MASTERY_ROW_GAP - 10,
                                                      MUTED)))
        return cells
