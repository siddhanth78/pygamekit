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
        "Locked": (150, 170, 172)}
PANEL_SIZE = (680, 420)
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
                              for _ in range(2)]
        self.quads = {}
        for label in (self.title, self.chip, *self.lines, *self.button_labels):
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

    def show_message(self, title: str, line: str):
        self._show("result", title, "", (line, "", ""), ("OK",))

    def _show(self, mode, title, chip, lines, buttons):
        self.open, self.mode, self.selected, self.buttons = True, mode, 0, buttons
        self.title.set(title)
        self.chip_name = chip
        self.chip.set(chip.upper())
        for label, text in zip(self.lines, list(lines) + ["", "", ""]):
            label.set(text)
        for label, text in zip(self.button_labels, buttons):
            label.set(text)

    def _button_centers(self):
        width, height = self.viewport
        y = height // 2 + 140
        if len(self.buttons) == 1:
            return [(width // 2, y)]
        return [(width // 2 - 140, y), (width // 2 + 140, y)]

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
            rects = [_rect(x, y, *BUTTON_SIZE, (0, 0, 0, 0)) for x, y in self._button_centers()]
            hits = check_mouse_collisions(*value, rects, "rect")
            if hits:
                self.selected = hits[0]
                if action == "click":
                    return self._choose(hits[0])
        return None

    def _choose(self, index):
        if self.mode == "offer":
            return self._close("accept" if index == 0 else "decline")
        return self._close("close")

    def _close(self, outcome):
        self.open = False
        return outcome

    def render(self):
        width, height = self.viewport
        cx, cy = width // 2, height // 2
        pw, ph = PANEL_SIZE
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
        for i, label in enumerate(self.lines):
            labels.append((label, label.record(cx, top + 170 + i * 40, CREAM if i < 2 else MUTED)))
        for i, (x, y) in enumerate(self._button_centers()):
            chosen = i == self.selected
            bw, bh = BUTTON_SIZE
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
