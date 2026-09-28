"""The fair booth overlay: an intro card (rules, prize bands, best), the game itself,
and a result card. The caller spends the ticket: handle() returns "start" when the
player wants to play, and the caller answers with begin() once a ticket is paid."""

from __future__ import annotations

import moderngl

from fair_games import BAND_PAY, BANDS, FIELD, GAMES, Balloons, Darts, Hammer, RingToss, SkeeBall, WhackAMole
from gl_utils import build_rect_objs, build_tex_objs, get_new_instances, load_program, to_gl
from ui_text import DynamicLabel
from world import Sprite


INK = (32, 45, 52)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (150, 170, 172)
GOOD = (140, 214, 150)
BOOTH = (150, 52, 60, 255)
SCREEN = (28, 44, 52, 255)
BAND_COLORS = {"bronze": (205, 127, 50), "silver": (200, 206, 212), "gold": (242, 202, 87),
               "platinum": (160, 226, 236)}


MAX_LINES = 6
WRAP_CHARS = 48                 # Characters per line before a description wraps.


def wrap(text: str, width: int = WRAP_CHARS) -> list[str]:
    """Split a long description into lines of whole words."""
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return out + [line] if line else out


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


class FairGameOverlay:
    def __init__(self, ctx, toolkit_root, viewport, state):
        self.viewport = viewport
        self.state = state                 # GameState: draws the fair-atlas game pieces.
        self.open = False
        self.mode = "intro"                # intro, play, or over.
        self.game_id = None
        self.game = None
        self.best = 0
        self.band = 0                      # Bands already reached before this attempt.
        self.earned = 0                    # Tokens this attempt paid (set by the caller).
        self.reported = False
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        self.rect_instances = get_new_instances(160, 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        # Crosshairs and markers draw over the sprites: their own buffer (no mid-frame rewrite).
        self.front_instances = get_new_instances(24, 0, 0)[0]
        self.front_vao, self.front_vbo = build_rect_objs(ctx, self.rect_program, self.front_instances)
        self.title = DynamicLabel(ctx, (500, 50), 42, bold=True, align="center")
        self.score = DynamicLabel(ctx, (480, 28), 23, bold=True, align="center")
        self.status = DynamicLabel(ctx, (480, 30), 24, bold=True, align="center")
        self.lines = [DynamicLabel(ctx, (560, 28), 22, align="center") for _ in range(MAX_LINES)]
        self.bands = [DynamicLabel(ctx, (260, 26), 21, bold=True) for _ in BANDS]
        self.band_pay = [DynamicLabel(ctx, (160, 26), 21, align="right") for _ in BANDS]
        self.hint = DynamicLabel(ctx, (640, 22), 18, align="center")
        self.quads = {}
        for label in (self.title, self.score, self.status, *self.lines, *self.bands, *self.band_pay, self.hint):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    # Flow ------------------------------------------------------------------------------

    def show(self, game_id: str, best: int, band: int):
        self.open, self.mode, self.game_id, self.game = True, "intro", game_id, None
        self.best, self.band = best, band

    def begin(self, seed=None):
        """A ticket was paid: a fresh attempt."""
        self.mode, self.game, self.reported, self.earned = "play", GAMES[self.game_id](seed), False, 0

    def _field_origin(self):
        width, height = self.viewport
        return width // 2 - FIELD[0] // 2, height // 2 - FIELD[1] // 2 + 20

    def handle(self, action, value):
        """Returns "start" (the player wants a new attempt), "close", or None."""
        if self.mode in ("intro", "over"):
            if action == "pause":
                self.open = False
                return "close"
            if action in ("confirm", "interact", "click"):
                return "start"
            return None
        game = self.game
        if action == "pause":
            self.mode = "intro"            # Walking away mid-attempt: the ticket is gone.
            self.game = None
            return None
        left, top = self._field_origin()
        if action in ("confirm", "interact"):
            game.press()
        elif isinstance(game, WhackAMole) and action.startswith("menu_"):
            game.move(*{"menu_left": (-1, 0), "menu_right": (1, 0), "menu_up": (0, -1),
                        "menu_down": (0, 1)}[action])
        elif action == "pointer":
            game.point(value[0] - left, value[1] - top)
        elif action == "click":
            game.click(value[0] - left, value[1] - top)
        return None

    def update(self, dt, move_x=0, move_y=0):
        """Advance the attempt; returns the final score once, when it ends, else None."""
        if not (self.open and self.mode == "play"):
            return None
        self.game.update(dt, move_x, move_y)
        if self.game.over and not self.reported:
            self.reported = True
            self.mode = "over"
            return self.game.score
        return None

    # Render -------------------------------------------------------------------------------

    def render(self):
        width, height = self.viewport
        cx, cy = width // 2, height // 2
        left, top = self._field_origin()
        cls = GAMES[self.game_id]
        rects = [_rect(cx, cy, width, height, (8, 16, 22, 200)),
                 _rect(cx, cy + 10, FIELD[0] + 120, FIELD[1] + 190, BOOTH),               # The booth,
                 _rect(cx, cy - FIELD[1] // 2 - 58, FIELD[0] + 140, 26, (*ACCENT, 255)),   # its awning,
                 _rect(cx, cy + 20, FIELD[0] + 12, FIELD[1] + 12, (*ACCENT, 255)),
                 _rect(cx, cy + 20, *FIELD, SCREEN)]
        for i in range(8):                                                                 # striped.
            rects.append(_rect(cx - FIELD[0] // 2 - 70 + 8 + i * 80 + 20, cy - FIELD[1] // 2 - 58, 40, 26,
                               (217, 69, 63, 255)))
        sprites, labels, front = [], [], []
        self.title.set(cls.name)
        labels.append((self.title, self.title.record(cx, top - 58, CREAM)))
        if self.mode == "intro" or (self.mode == "over" and self.game is None):
            self._intro(rects, labels, cx, top)
        else:
            self._draw_game(rects, sprites, front, left, top)
            game = self.game
            self.score.set(f"Score {game.score}   ·   {self._tries_left()}")
            labels.append((self.score, self.score.record(cx, top + 16, CREAM)))
            if game.status:
                self.status.set(game.status)
                labels.append((self.status, self.status.record(cx, top + FIELD[1] - 20, ACCENT)))
            if self.mode == "over":
                self._result(rects, labels, cx, top)
                self.hint.set("ENTER play again (1 game ticket)  ·  ESC leave the booth")
            else:
                self.hint.set("ESC leave (the ticket is used up)")
        labels.append((self.hint, self.hint.record(cx, top + FIELD[1] + 50, CREAM)))
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        if sprites:
            self.state.render(sprites, 0.0, 0.0, [], 1.0)
        if front:
            to_gl(front, self.front_instances, "rect")
            self.front_vbo.write(self.front_instances[:len(front)].tobytes(), offset=0)
            self.front_vao.render(moderngl.TRIANGLES, instances=len(front))
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)

    def _tries_left(self):
        game = self.game
        if isinstance(game, WhackAMole):
            return f"{max(0.0, game.DURATION - game.clock):.1f} s"
        return f"{game.tries - game.used} left"

    def _intro(self, rects, labels, cx, top):
        cls = GAMES[self.game_id]
        rules = wrap(cls.rules)
        texts = rules + [f"Your best: {self.best}"] + wrap("Each band pays once; platinum closes the booth.")
        for i, (label, text) in enumerate(zip(self.lines, texts)):
            label.set(text)
            labels.append((label, label.record(cx, top + 36 + i * 28, CREAM if i < len(rules) else MUTED)))
        first = max(top + 170, top + 36 + len(texts) * 28 + 24)
        for i, (band, label, pay) in enumerate(zip(BANDS, self.bands, self.band_pay)):
            y = first + i * 40
            reached = self.band > i
            label.set(f"{band.upper()}  {cls.thresholds[i]}{' (max)' if band == 'platinum' else ''}")
            pay.set("reached" if reached else f"{BAND_PAY[band]} S")
            rects.append(_rect(cx - 170, y, 14, 14, (*BAND_COLORS[band], 255)))
            labels += [(label, label.record(cx - 150, y, BAND_COLORS[band])),
                       (pay, pay.record(cx + 200, y, GOOD if reached else CREAM))]
        self.hint.set("ENTER play (1 game ticket)  ·  ESC leave the booth")

    def _result(self, rects, labels, cx, top):
        game = self.game
        reached = GAMES[self.game_id].band(game.score)
        name = BANDS[reached - 1].upper() if reached else "NO PRIZE"
        texts = [(f"Final score {game.score}  ·  {name}", BAND_COLORS[BANDS[reached - 1]] if reached else CREAM),
                 (f"+{self.earned} Sunside Tokens" if self.earned else "No new prize this time",
                  GOOD if self.earned else CREAM)]
        last = "PLATINUM! This booth is yours: it's closed now." if reached == 4 else f"Best {self.best}"
        texts += [(line, CREAM) for line in wrap(last)]
        height = 60 + 34 * len(texts)
        rects.append(_rect(cx, top + FIELD[1] / 2, FIELD[0] - 40, height, (8, 16, 22, 235)))
        y0 = top + FIELD[1] / 2 - 34 * (len(texts) - 1) / 2
        for i, (label, (text, color)) in enumerate(zip(self.lines, texts)):
            label.set(text)
            labels.append((label, label.record(cx, y0 + i * 34, color)))

    # Game pieces --------------------------------------------------------------------------------

    def _draw_game(self, rects, sprites, front, left, top):
        game = self.game

        def at(x, y):
            return left + x, top + y

        if isinstance(game, Darts):
            sprites.append(Sprite("fair-atlas", "dartboard", *at(*Darts.CENTER), 300, 300))
            for x, y in game.darts:
                sprites.append(Sprite("fair-atlas", "dart", *at(x, y), 32, 32))
            if not game.over:
                x, y = at(*game.aim())
                front += [_rect(x, y, 30, 3, (255, 255, 255, 255)), _rect(x, y, 3, 30, (255, 255, 255, 255))]
        elif isinstance(game, Hammer):
            bx = left + FIELD[0] // 2
            rects += [_rect(bx, top + 190, 40, 280, (60, 70, 80, 255)),                 # Tower,
                      _rect(bx, top + 50 + 280 * (100 - game.BELL) / 200, 40, 280 * (100 - game.BELL) / 100,
                            (242, 202, 87, 120))]                                    # bell zone.
            sprites.append(Sprite("fair-atlas", "bell", bx, top + 44, 48, 48))
            value = game.meter() if not game.over else (game.last or 0)
            y = top + 330 - 280 * value / 100
            rects.append(_rect(bx, y, 64, 10, (217, 69, 63, 255)))                     # The puck.
            rects.append(_rect(bx + 90, top + 190, 20, 280, (40, 50, 58, 255)))
            rects.append(_rect(bx + 90, top + 330 - 140 * value / 100, 20, 280 * value / 100,
                               (242, 202, 87, 255) if value >= game.BELL else (140, 214, 150, 255)))
        elif isinstance(game, Balloons):
            for i, balloon in enumerate(game.balloons):
                x, y = game.balloon_xy(balloon)
                if -20 <= x <= FIELD[0] + 20:
                    sprites.append(Sprite("fair-atlas", ("balloon_red", "balloon_blue", "balloon_yellow")[i % 3],
                                          *at(x, y), 44, 44))
            x, y = at(*game.aim)
            front += [_rect(x, y, 34, 3, (255, 255, 255, 255)), _rect(x, y, 3, 34, (255, 255, 255, 255))]
        elif isinstance(game, RingToss):
            for by, _ in RingToss.ROWS:
                rects.append(_rect(left + FIELD[0] // 2, top + by + 14, FIELD[0] - 40, 8, (120, 84, 60, 255)))
                for bx in RingToss.COLUMNS:
                    sprites.append(Sprite("fair-atlas", "bottle", *at(bx, by), 40, 40))
            for x, y, hit in game.rings:
                sprites.append(Sprite("fair-atlas", "ring", *at(x, y), 34 if hit else 30, 34 if hit else 30))
            if not game.over:
                if game.stage == "side":
                    front.append(_rect(left + game.marker(), top + FIELD[1] - 30, 6, 40, (242, 202, 87, 255)))
                else:
                    front.append(_rect(left + game.x, top + FIELD[1] - 30, 6, 40, (120, 120, 120, 255)))
                    front.append(_rect(left + game.x, top + game.marker(), 40, 4, (242, 202, 87, 255)))
        elif isinstance(game, SkeeBall):
            # Drawn to match the scoring: the rings span the aim offsets (20-110 px) and the
            # distances that land in them (power 55-90); the corner holes sit where 92+ lands.
            sprites.append(Sprite("fair-atlas", "skee_rings", left + 240, top + 115, 220, 110))
            for cx in SkeeBall.CORNERS:
                sprites.append(Sprite("fair-atlas", "skee_hole", *at(cx, 42), 40, 40))
            rects.append(_rect(left + FIELD[0] // 2, top + 280, FIELD[0] - 20, 60, (150, 110, 70, 255)))
            if not game.over:
                if game.stage == "aim":
                    sprites.append(Sprite("fair-atlas", "skee_ball", *at(game.marker(), 290), 28, 28))
                else:
                    # Like ring toss: the aim is set, and a yellow bar sweeps up and down the
                    # lane on that line; where it stops is where the ball lands.
                    sprites.append(Sprite("fair-atlas", "skee_ball", *at(game.x, 290), 28, 28))
                    front.append(_rect(left + game.x, top + game.landing_y(game.marker()), 40, 4,
                                       (242, 202, 87, 255)))
        elif isinstance(game, WhackAMole):
            up = set(game.moles_up().values())
            for hole in range(9):
                x, y = at(*game.hole_xy(hole))
                sprites.append(Sprite("fair-atlas", "mole" if hole in up else "mole_hole", x, y, 80, 80))
            if not game.over:                                    # The mallet hovers where you aim.
                x, y = at(*game.hole_xy(game.cursor))
                sprites.append(Sprite("fair-atlas", "mallet", x + 26, y - 30, 64, 64, -20.0))
