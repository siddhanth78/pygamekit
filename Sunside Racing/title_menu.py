"""Title screen shown at launch, before the game (world, saves, traffic) is loaded.

The background is a small staged scene, not the game: the player's race car on a city
road, the whole scene turned 45 degrees. Only the game title and PLAY / EXIT sit over it,
chosen with the arrow keys (or W/S), Enter/Space, or the mouse."""

from __future__ import annotations

import math

import moderngl

from game_state import GameState
from gl_utils import build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances, load_program, to_gl
from pause_menu import ACCENT, BUTTON, BUTTON_EDGE, CREAM, INK, SHADOW, _rect
from ui_text import LabelAtlas
from world import TILE_SIZE, Sprite


ITEMS = ("play", "exit")
LABEL_CELL = (720, 96)
LABELS = {
    "title": ("SUNSIDE RACING", 84, True, "center"),
    "play": ("PLAY", 40, True, "center"),
    "exit": ("EXIT", 40, True, "center"),
}
BUTTON_SIZE = (340, 72)
BUTTON_GAP = 96
MENU_X = 0.3         # Title and buttons: this fraction of the width from the left.

SCENE_ZOOM = 2.0     # Scene pixels to screen pixels, like the on-foot camera.
ROAD_HEADING = 45.0  # Degrees clockwise from north: the road runs up to the right.
CAR_AT = (0.76, 0.56)  # The car's screen position as fractions of the viewport.
CAR_LANE = 0.2       # Tiles right of the center line: driving in the right-hand lane.
# Oncoming traffic in the other lane: (sprite, tiles up the road from the player).
ONCOMING = (("traffic_blue", 2.6), ("traffic_taxi", -2.9))
# Trees on the grass, clear of the title and buttons: (sprite, up the road, across, size).
TREES = (("jungle_tree_a", -4.5, -1.7, 80), ("jungle_tree_d", 0.4, 1.9, 72))


def scene_sprites(viewport) -> list[Sprite]:
    """The player's car on a city road through grass, with two oncoming cars and a couple
    of trees, all turned ROAD_HEADING.

    Sprite positions are scene pixels; the renderer multiplies them by SCENE_ZOOM."""
    width, height = viewport
    heading = math.radians(ROAD_HEADING)
    along = (math.sin(heading), -math.cos(heading))    # Up the road.
    across = (math.cos(heading), math.sin(heading))    # To the road's right.
    car_x, car_y = width * CAR_AT[0] / SCENE_ZOOM, height * CAR_AT[1] / SCENE_ZOOM
    reach = int(math.hypot(width, height) / SCENE_ZOOM / TILE_SIZE) + 2
    rotation = -ROAD_HEADING      # GL rotation is counterclockwise.

    def at(u: float, v: float):
        return (car_x + (along[0] * u + across[0] * v) * TILE_SIZE,
                car_y + (along[1] * u + across[1] * v) * TILE_SIZE)

    out = []
    for u in range(-reach, reach + 1):
        for v in range(-reach, reach + 1):
            x, y = at(u, v)
            if v == 0:
                out.append(Sprite("road-atlas", "city_ns", x, y, TILE_SIZE, TILE_SIZE, rotation))
            else:
                out.append(Sprite("terrain-atlas", "grass", x, y, TILE_SIZE, TILE_SIZE, rotation))
    for name, u, v, size in TREES:
        out.append(Sprite("prop-atlas", name, *at(u, v), size, size))
    for name, u in ONCOMING:   # Facing down the road, in the left-hand lane.
        out.append(Sprite("vehicle-atlas", name, *at(u, -CAR_LANE), 64, 64, rotation - 180.0))
    out.append(Sprite("vehicle-atlas", "racer_player", *at(0, CAR_LANE), 64, 64, rotation))
    return out


class TitleMenu:
    def __init__(self, ctx, project_root, toolkit_root, viewport):
        self.viewport = viewport
        self.open = True
        self.selected = 0
        # Only the sprite renderer: no world, saves, or game systems until PLAY.
        self.scene = GameState(ctx, project_root, toolkit_root, viewport)
        self.sprites = scene_sprites(viewport)
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.labels = LabelAtlas(ctx, LABEL_CELL, LABELS)
        self.text_program["u_atlas_grid"].value = self.labels.grid
        rects, _, texts = get_new_instances(24, 0, 8)
        self.rect_instances, self.text_instances = rects, texts
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, rects)
        self.text_vao, self.text_vbo = build_tex_objs(ctx, self.text_program, texts)

    def _menu_x(self):
        return round(self.viewport[0] * MENU_X)

    def _button_centers(self):
        height = self.viewport[1]
        return [(self._menu_x(), height // 2 + 20 + i * BUTTON_GAP) for i in range(len(ITEMS))]

    def handle(self, action, value):
        """Returns 'play' or 'exit' once chosen, else None."""
        if action == "menu_up":
            self.selected = (self.selected - 1) % len(ITEMS)
        elif action == "menu_down":
            self.selected = (self.selected + 1) % len(ITEMS)
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
        self.open = False
        return ITEMS[index]

    def render(self):
        self.scene.render(self.sprites, 0.0, 0.0, [], SCENE_ZOOM)
        width, height = self.viewport
        x = self._menu_x()
        rects = [
            _rect(width // 2, height // 2, width, height, (8, 16, 22, 70)),   # Soften the scene.
            _rect(x, height // 2 - 96, 640, 6, (*ACCENT, 255)),              # Under the title.
        ]
        texts = [self.labels.record("title", x + 3, height // 2 - 157, INK),  # Drop shadow.
                 self.labels.record("title", x, height // 2 - 160, ACCENT)]
        bw, bh = BUTTON_SIZE
        for i, (bx, by) in enumerate(self._button_centers()):
            chosen = i == self.selected
            rects += [_rect(bx + 5, by + 6, bw, bh, SHADOW),
                      _rect(bx, by, bw + 4, bh + 4, (*ACCENT, 255) if chosen else BUTTON_EDGE),
                      _rect(bx, by, bw, bh, (*ACCENT, 255) if chosen else BUTTON)]
            if chosen:
                rects.append(_rect(bx - bw // 2 + 16, by, 6, bh - 26, (*INK, 255)))   # Selection tick.
            texts.append(self.labels.record(ITEMS[i], bx, by + 2, INK if chosen else CREAM))
        _, self.rect_instances = to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))
        _, self.text_instances = to_gl(texts, self.text_instances, "tex")
        self.text_vbo.write(self.text_instances[:len(texts)].tobytes(), offset=0)
        self.labels.texture.use(location=0)
        self.text_vao.render(moderngl.TRIANGLES, instances=len(texts))
