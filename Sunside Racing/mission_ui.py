"""Modal mission panel: offers (accept / decline) and results (continue)."""

from __future__ import annotations

import moderngl

from gl_utils import (
    build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances, load_program,
    to_gl,
)
from progression import rating
from ui_text import DynamicLabel


INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (160, 180, 180)
PANEL = (26, 40, 48, 242)
BUTTON = (44, 66, 76, 255)
BUTTON_EDGE = (78, 104, 112, 255)
SHADOW = (8, 14, 18, 150)
CHIP = {"Easy": (86, 176, 104), "Medium": (226, 176, 72), "Hard": (212, 80, 66),
        "Success": (86, 176, 104), "Failed": (212, 80, 66), "Level up": (242, 202, 87),
        "Champion": (242, 202, 87), "Island unlocked": (86, 176, 104),
        "Locked": (150, 170, 172), "Tokens": (242, 202, 87), "Mastery": (120, 170, 230)}
PANEL_SIZE = (680, 420)
BILL_ROWS = 8                   # Item lines a bill shows (more fold into "and N more").
BILL_ROW_GAP = 32
BUTTON_SIZE = (240, 60)


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


class MissionPanel:
    def __init__(self, ctx, toolkit_root, viewport):
        self.viewport = viewport
        self.open = False
        self.mode = None      # "offer" or "result".
        self.selected = 0
        self.buttons = ()
        self.chip_name = ""
        self.details = None           # Per-option description lines (show_choice).
        self.fixed_lines = []
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(32, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.title = DynamicLabel(ctx, (600, 48), 42, bold=True, align="center")
        self.chip = DynamicLabel(ctx, (200, 28), 24, bold=True, align="center")
        self.lines = [DynamicLabel(ctx, (620, 30), 25, align="center") for _ in range(3)]
        self.button_labels = [DynamicLabel(ctx, BUTTON_SIZE, 32, bold=True, align="center")
                              for _ in range(4)]
        # A bill: one line per item (name left, amount right), then the total and a note.
        self.bill = None              # (rows, total, note) while a bill is shown.
        self.bill_left = [DynamicLabel(ctx, (420, 28), 23) for _ in range(BILL_ROWS + 1)]
        # Item amounts are short; the TOTAL line (last) can be long ("1,560 S + 25 mastery PAID").
        self.bill_right = [DynamicLabel(ctx, (180, 28), 23, align="right") for _ in range(BILL_ROWS)]
        self.bill_right.append(DynamicLabel(ctx, (420, 28), 23, align="right"))
        self.bill_note = DynamicLabel(ctx, (600, 26), 21, align="center")
        self.quads = {}
        for label in (self.title, self.chip, *self.lines, *self.button_labels,
                      *self.bill_left, *self.bill_right, self.bill_note):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    def show_offer(self, preview: dict):
        buttons = ("ACCEPT", "DECLINE") if preview.get("can_decline", True) else ("ACCEPT",)
        self._show("offer", preview["title"], preview["difficulty"],
                   (preview["detail"], preview["rules"], preview["reward"]), buttons)

    def show_result(self, result: dict):
        chip = "Success" if result["success"] else "Failed"
        lines = [result["detail"]]
        if result["success"]:
            lines.append(f"+{result['mastery']} mastery" if result["mastery"] else "No mastery earned")
        else:
            lines.append("Back at the mission giver  ·  talk to them to retry")
        level, (have, need) = result["level"], result["progress"]
        if result["levels"]:
            chip = "Level up"
            lines.append(f"{result['region'].title()} level {level}!  Rating {rating(level)}"
                         + ("  ·  veteran givers unlocked" if 5 in result["levels"] else ""))
        else:
            lines.append(f"{result['region'].title()} level {level}  ·  {have}/{need} mastery to next")
        self._show("result", result["title"], chip, lines, ("CONTINUE",))

    def show_lines(self, title: str, chip: str, lines, buttons=("CONTINUE",)):
        """A result-style panel with custom text (e.g. racing-center results)."""
        self._show("result", title, chip, lines, buttons)

    def show_confirm(self, title: str, chip: str, lines, yes: str, no: str):
        """Yes/no question; returns 'accept' or 'decline'. The safe answer (no) starts selected."""
        self._show("offer", title, chip, lines, (yes, no))
        self.selected = 1

    def show_choice(self, title: str, chip: str, lines, options, details=None):
        """Pick one of up to four options; returns "choice:<index>", or "close" (Escape).
        details: optional lines per option, shown after `lines` for the highlighted one
        (they change as the selection moves)."""
        self.fixed_lines = list(lines)
        self._show("choice", title, chip, lines, tuple(options)[:4])
        self.details = list(details) if details else None
        self._show_details()

    def _show_details(self):
        if not self.details:
            return
        texts = (self.fixed_lines + list(self.details[self.selected]) + ["", "", ""])[:3]
        for label, text in zip(self.lines, texts):
            label.set(text)

    def show_bill(self, title: str, rows, total: str, note: str, buttons, chip: str = ""):
        """An itemized bill: rows of (item, amount), a TOTAL line, and a note under it.
        Two buttons ask like show_confirm (the first is accept) but start on the first;
        one button just closes it (a receipt)."""
        rows = list(rows)
        if len(rows) > BILL_ROWS:
            rows = rows[:BILL_ROWS - 1] + [(f"...and {len(rows) - BILL_ROWS + 1} more", "")]
        self._show("offer" if len(buttons) > 1 else "result", title, chip, ("", "", ""), tuple(buttons))
        self.bill = (rows, total, note)
        for i, (left, right) in enumerate(rows):
            self.bill_left[i].set(left)
            self.bill_right[i].set(right)
        self.bill_left[BILL_ROWS].set("TOTAL")
        self.bill_right[BILL_ROWS].set(total)
        self.bill_note.set(note)

    def show_message(self, title: str, line: str):
        self._show("result", title, "", (line, "", ""), ("OK",))

    def _show(self, mode, title, chip, lines, buttons):
        self.open, self.mode, self.selected, self.buttons = True, mode, 0, buttons
        self.bill = None
        self.details = None
        self.title.set(title)
        self.chip_name = chip
        self.chip.set(chip.upper())
        for label, text in zip(self.lines, list(lines) + ["", "", ""]):
            label.set(text)
        for label, text in zip(self.button_labels, buttons):
            label.set(text)

    def _height(self):
        """The panel grows with a bill's lines."""
        if not self.bill:
            return PANEL_SIZE[1]
        return max(PANEL_SIZE[1], 250 + (len(self.bill[0]) + 1) * BILL_ROW_GAP + 70)

    def _button_centers(self):
        width, height = self.viewport
        y = height // 2 + self._height() // 2 - 70
        if len(self.buttons) == 1:
            return [(width // 2, y)]
        if len(self.buttons) == 3:
            return [(width // 2 - 215, y), (width // 2, y), (width // 2 + 215, y)]
        if len(self.buttons) == 4:
            return [(width // 2 + dx, y) for dx in (-234, -78, 78, 234)]
        return [(width // 2 - 140, y), (width // 2 + 140, y)]

    def _button_size(self):
        return {3: (200, BUTTON_SIZE[1]), 4: (140, BUTTON_SIZE[1])}.get(len(self.buttons), BUTTON_SIZE)

    def handle(self, action, value):
        """Returns 'accept', 'decline', or 'close' when the panel is dismissed."""
        if action == "pause":
            # Escape walks away; only the DECLINE button turns an offer down.
            return self._close("close")
        if action in ("menu_left", "menu_right"):
            # Buttons sit side by side: Left picks the left one, Right the right one.
            last = len(self.buttons) - 1
            self.selected = max(0, self.selected - 1) if action == "menu_left" else min(last, self.selected + 1)
        elif action in ("menu_up", "menu_down"):
            self.selected = (self.selected + 1) % len(self.buttons)
        elif action == "confirm":
            return self._choose(self.selected)
        elif action in ("pointer", "click"):
            rects = [_rect(x, y, *self._button_size(), (0, 0, 0, 0)) for x, y in self._button_centers()]
            hits = check_mouse_collisions(*value, rects, "rect")
            if hits:
                self.selected = hits[0]
                if action == "click":
                    return self._choose(hits[0])
        self._show_details()             # The highlighted option's description.
        return None

    def _choose(self, index):
        if self.mode == "choice":
            return self._close(f"choice:{index}")
        if self.mode == "offer":
            return self._close("accept" if index == 0 else "decline")
        return self._close("close")

    def _close(self, outcome):
        self.open = False
        return outcome

    def render(self):
        width, height = self.viewport
        cx, cy = width // 2, height // 2
        pw, ph = PANEL_SIZE[0], self._height()
        top = cy - ph // 2
        rects = [
            _rect(cx, cy, width, height, (8, 16, 22, 150)),
            _rect(cx + 8, cy + 10, pw, ph, SHADOW),
            _rect(cx, cy, pw + 8, ph + 8, (*ACCENT, 255)),
            _rect(cx, cy, pw, ph, PANEL),
            _rect(cx, top + 6, pw, 12, (*ACCENT, 255)),
        ]
        labels = [(self.title, self.title.record(cx, top + 62, ACCENT))]
        if self.chip_name:
            rects.append(_rect(cx, top + 112, 200, 32, (*CHIP.get(self.chip_name, ACCENT), 255)))
            labels.append((self.chip, self.chip.record(cx, top + 113, INK)))
        if self.bill:
            rows, _, _ = self.bill
            left, right = cx - pw // 2 + 70, cx + pw // 2 - 70
            y = top + (150 if self.chip_name else 120)
            for i in range(len(rows)):
                labels += [(self.bill_left[i], self.bill_left[i].record(left, y, CREAM)),
                           (self.bill_right[i], self.bill_right[i].record(right, y, CREAM))]
                y += BILL_ROW_GAP
            rects.append(_rect(cx, y - 12, pw - 120, 2, (*MUTED, 200)))           # Rule above the total.
            y += 6
            labels += [(self.bill_left[BILL_ROWS], self.bill_left[BILL_ROWS].record(left, y, ACCENT)),
                       (self.bill_right[BILL_ROWS], self.bill_right[BILL_ROWS].record(right, y, ACCENT)),
                       (self.bill_note, self.bill_note.record(cx, y + 38, MUTED))]
        else:
            for i, label in enumerate(self.lines):
                labels.append((label, label.record(cx, top + 170 + i * 40, CREAM if i < 2 else MUTED)))
        for i, (x, y) in enumerate(self._button_centers()):
            chosen = i == self.selected
            bw, bh = self._button_size()
            rects += [_rect(x + 4, y + 5, bw, bh, SHADOW),
                      _rect(x, y, bw + 4, bh + 4, (*ACCENT, 255) if chosen else BUTTON_EDGE),
                      _rect(x, y, bw, bh, (*ACCENT, 255) if chosen else BUTTON)]
            label = self.button_labels[i]
            labels.append((label, label.record(x, y + 2, INK if chosen else CREAM)))
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)
