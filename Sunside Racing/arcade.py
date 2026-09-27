"""The arcade cabinet in the player's house: a menu of three games, one playable so far.

Lane Dodge: three lanes, cones fall faster and faster, Left/Right switch lanes, one hit
ends the run. The best score is saved with the player. The other two games are locked
until their unlock rules are decided (see ArcadeCabinet.unlocked).
"""

from __future__ import annotations

import random

import moderngl

from gl_utils import build_rect_objs, build_tex_objs, get_new_instances, load_program, to_gl
from ui_text import DynamicLabel


GAMES = (("lane_dodge", "LANE DODGE"), ("game_2", "LOCKED"), ("game_3", "LOCKED"))
LOCKED_NOTE = "Unlocks later"

LANES = 3
FIELD = (360, 480)            # Playfield size on screen, px.
CAR_Y = 430                   # Player car's center, from the field's top.
CAR_SIZE = (40, 64)
CONE_SIZE = (40, 40)
START_SPEED = 220.0           # px/s the cones fall at first,
SPEED_UP = 12.0               # plus this much per cone dodged,
MAX_SPEED = 720.0
CLEAR_TIME = (0.6, 0.95)      # Seconds between one row leaving the car and the next arriving:
                              # always time for two taps across all three lanes, at any speed.
HIT_REACH = (CAR_SIZE[1] + CONE_SIZE[1]) / 2 - 6   # A cone this close (center to center) hits.

INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
CABINET = (90, 48, 120, 255)
SCREEN = (16, 22, 30, 255)


class LaneDodge:
    """Pure game state, so it can be tested without a window."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.lane = LANES // 2
        self.cones: list[list[float]] = []    # [lane, y] of each cone's center.
        self.speed = START_SPEED
        self.score = 0
        self.over = False
        self.next_row = -CONE_SIZE[1]

    def steer(self, direction: int):
        if not self.over:
            self.lane = max(0, min(LANES - 1, self.lane + direction))

    def update(self, dt: float):
        if self.over:
            return
        step = self.speed * dt
        for cone in self.cones:
            cone[1] += step
        self.next_row += step
        if self.next_row >= 0:
            # One or two cones per row, never all three lanes.
            lanes = self.rng.sample(range(LANES), self.rng.choice((1, 1, 2)))
            self.cones += [[lane, -CONE_SIZE[1] / 2] for lane in lanes]
            # Space rows in time, not pixels: the gap grows with the speed so the window
            # between rows at the car never shrinks as the cones speed up.
            self.next_row = -(2 * HIT_REACH + self.speed * self.rng.uniform(*CLEAR_TIME))
        passed = [c for c in self.cones if c[1] - CONE_SIZE[1] / 2 > FIELD[1]]
        self.score += len(passed)
        self.speed = min(MAX_SPEED, START_SPEED + SPEED_UP * self.score)
        self.cones = [c for c in self.cones if c not in passed]
        if any(c[0] == self.lane and abs(c[1] - CAR_Y) < HIT_REACH for c in self.cones):
            self.over = True


def lane_x(lane: int) -> float:
    return (lane + 0.5) * FIELD[0] / LANES


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


class ArcadeCabinet:
    """Overlay: 'menu' (pick a game) and 'play' (a run, then game over)."""

    def __init__(self, ctx, toolkit_root, viewport):
        self.viewport = viewport
        self.open = False
        self.mode = "menu"
        self.selected = 0
        self.best: dict[str, int] = {}
        self.game: LaneDodge | None = None
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(96, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.title = DynamicLabel(ctx, (400, 44), 36, bold=True, align="center")
        self.items = [DynamicLabel(ctx, (300, 34), 28, bold=True, align="center") for _ in GAMES]
        self.notes = [DynamicLabel(ctx, (300, 22), 18, align="center") for _ in GAMES]
        self.score = DynamicLabel(ctx, (340, 30), 24, bold=True, align="center")
        self.status = DynamicLabel(ctx, (400, 28), 22, bold=True, align="center")
        self.hint = DynamicLabel(ctx, (520, 22), 18, align="center")
        self.quads = {}
        for label in (self.title, *self.items, *self.notes, self.score, self.status, self.hint):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    # Games ----------------------------------------------------------------------------

    @staticmethod
    def unlocked(game_id: str) -> bool:
        """Only Lane Dodge for now; the other two get their own unlock rules later."""
        return game_id == "lane_dodge"

    def show(self, best: dict[str, int]):
        self.open, self.mode, self.selected, self.best = True, "menu", 0, best

    def _start(self):
        self.mode, self.game = "play", LaneDodge()

    # Input ------------------------------------------------------------------------------

    def handle(self, action, value):
        """Returns 'close' when the player walks away from the cabinet, else None."""
        if self.mode == "menu":
            if action == "pause":
                self.open = False
                return "close"
            if action == "menu_up":
                self.selected = (self.selected - 1) % len(GAMES)
            elif action == "menu_down":
                self.selected = (self.selected + 1) % len(GAMES)
            elif action == "confirm" and self.unlocked(GAMES[self.selected][0]):
                self._start()
            return None
        game = self.game
        if action == "pause":
            self.mode = "menu"           # Leave the run, back to the game list.
        elif action == "menu_left":
            game.steer(-1)
        elif action == "menu_right":
            game.steer(1)
        elif action == "confirm" and game.over:
            self._start()
        return None

    def update(self, dt: float):
        if self.open and self.mode == "play" and not self.game.over:
            self.game.update(dt)
            if self.game.over:
                game_id = GAMES[0][0]
                self.best[game_id] = max(self.best.get(game_id, 0), self.game.score)

    # Render ---------------------------------------------------------------------------

    def render(self):
        width, height = self.viewport
        cx, cy = width // 2, height // 2
        rects = [_rect(cx, cy, width, height, (8, 16, 22, 200)),
                 _rect(cx, cy, FIELD[0] + 80, FIELD[1] + 150, CABINET),             # Cabinet,
                 _rect(cx, cy + 20, FIELD[0] + 12, FIELD[1] + 12, (*ACCENT, 255)),   # bezel,
                 _rect(cx, cy + 20, *FIELD, SCREEN)]                                 # screen.
        left, top = cx - FIELD[0] / 2, cy + 20 - FIELD[1] / 2
        labels = []
        if self.mode == "menu":
            self.title.set("ARCADE")
            for i, ((game_id, name), label, note) in enumerate(zip(GAMES, self.items, self.notes)):
                y = top + 140 + i * 96
                chosen = i == self.selected
                playable = self.unlocked(game_id)
                label.set(name)
                note.set(f"Best {self.best.get(game_id, 0)}" if playable else LOCKED_NOTE)
                rects.append(_rect(cx, y + 10, 300, 74, (*ACCENT, 255) if chosen else (44, 66, 76, 255)))
                ink = INK if chosen else CREAM if playable else MUTED
                labels += [(label, label.record(cx, y, ink)), (note, note.record(cx, y + 28, ink))]
            self.hint.set("UP / DOWN choose  ·  ENTER play  ·  ESC leave")
        else:
            game = self.game
            self.title.set("LANE DODGE")
            for lane in range(1, LANES):
                x = left + lane * FIELD[0] / LANES
                for y in range(0, FIELD[1], 40):                             # Dashed lane lines.
                    rects.append(_rect(x, top + y + 10, 4, 20, (70, 80, 90, 255)))
            for lane, y in game.cones:
                # Clip to the screen, so cones slide in at the top and out at the bottom.
                y0, y1 = max(0.0, y - CONE_SIZE[1] / 2), min(FIELD[1], y + CONE_SIZE[1] / 2)
                if y1 <= y0:
                    continue
                x = left + lane_x(lane)
                rects.append(_rect(x, top + (y0 + y1) / 2, CONE_SIZE[0], y1 - y0, (242, 150, 60, 255)))
                if 0 <= y - 4 and y + 4 <= FIELD[1]:
                    rects.append(_rect(x, top + y, CONE_SIZE[0] - 16, 8, (244, 234, 208, 255)))
            car_x = left + lane_x(game.lane)
            rects += [_rect(car_x, top + CAR_Y, *CAR_SIZE, (217, 69, 63, 255)),
                      _rect(car_x, top + CAR_Y - 12, CAR_SIZE[0] - 12, 16, (131, 184, 192, 255))]
            self.score.set(f"Score {game.score}   ·   Best {self.best.get('lane_dodge', 0)}")
            labels.append((self.score, self.score.record(cx, top + 24, CREAM)))
            if game.over:
                rects.append(_rect(cx, cy + 20, FIELD[0], 110, (8, 16, 22, 230)))
                self.status.set(f"CRASH!  Score {game.score}")
                labels.append((self.status, self.status.record(cx, cy + 4, ACCENT)))
                self.hint.set("ENTER play again  ·  ESC back to the games")
            else:
                self.hint.set("LEFT / RIGHT switch lanes  ·  ESC back to the games")
        labels += [(self.title, self.title.record(cx, top - 44, ACCENT)),
                   (self.hint, self.hint.record(cx, top + FIELD[1] + 40, CREAM))]
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)
