"""World map (M): regions as flat colors, the player, and clickable landmark diamonds.

Clicking a diamond selects that landmark for the guide arrow; C clears the selection.
Nothing is selected when the game starts, so the arrow only shows once you pick one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import moderngl

from fishing import pier_title, trader_spots
from gl_utils import (
    build_polygon_obj, build_rect_objs, build_tex_objs, check_mouse_collisions, get_new_instances,
    load_program, render_polygon, to_gl, update_polygon_obj,
)
from ui_text import DynamicLabel
from world import CENTERS, SECTOR_SIZE, SECTORS, WORLD_SIZE


CELL = 9                       # Screen px per sector: 64 sectors -> a 576 px map.
DIAMOND = 16                   # Diamond size, px.
PLAYER_DOT = (255, 128, 0)     # You: a bright orange dot, round where piers are diamonds.
DOT_RADIUS, DOT_RIM = 6, 3
DOT_SIDES = 20
REGION_COLORS = {
    "sea": (40, 98, 143), "beach": (240, 213, 156), "city": (150, 156, 160),
    "jungle": (51, 115, 76), "desert": (214, 172, 104), "snow": (224, 237, 240),
    "rural": (138, 162, 83), "island": (113, 170, 107),
}
# Landmark kinds: (diamond color on the map, guide arrow color, legend text).
KINDS = {
    "center": ((212, 80, 66), (212, 80, 66), "Racing center"),
    "dock": ((242, 150, 60), (70, 140, 220), "Fishing pier"),
    "camp": ((236, 120, 170), (236, 120, 170), "Fish trader camp"),
}
PANEL = (20, 32, 40, 235)
ACCENT = (242, 202, 87)
CREAM = (238, 232, 208)
MUTED = (160, 180, 180)
INK = (32, 45, 52)


@dataclass(frozen=True)
class Landmark:
    kind: str          # "center", "dock", or "camp".
    name: str
    x: float
    y: float

    @property
    def arrow_color(self):
        return KINDS[self.kind][1]


def landmarks(world) -> list[Landmark]:
    """Every landmark the map shows. More kinds can be added here."""
    out = []
    for (sx, sy), name in sorted(CENTERS.items()):
        region = name.removeprefix("center_")
        out.append(Landmark("center", f"{region.title()} racing center", *world.center_position(sx, sy)))
    for dock in world.docks:
        ex, ey = dock.end
        out.append(Landmark("dock", pier_title(dock.name), (ex + 0.5) * 64, (ey + 0.5) * 64))
    for x, y, _ in trader_spots(world):
        out.append(Landmark("camp", "Fish trader camp", x, y))
    return out


def _rect(x, y, width, height, rgba, rotation=0.0):
    return [x, y, *rgba, 0.0, width, height, rotation]


class WorldMap:
    def __init__(self, ctx, toolkit_root, viewport, world, marks: list[Landmark]):
        self.viewport = viewport
        self.open = False
        self.marks = marks
        self.hovered: Landmark | None = None
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        # The regions never change: pack them once.
        left, top = self.origin()
        tiles = [_rect(left + (sx + 0.5) * CELL, top + (sy + 0.5) * CELL, CELL, CELL,
                       (*REGION_COLORS.get(world.region(sx, sy), (0, 0, 0)), 255))
                 for sy in range(SECTORS) for sx in range(SECTORS)]
        self.tile_count = len(tiles)
        tile_instances = get_new_instances(len(tiles), 0, 0)[0]
        _, tile_instances = to_gl(tiles, tile_instances, "rect")
        self.tile_vao, self.tile_vbo = build_rect_objs(ctx, self.rect_program, tile_instances)
        self.tile_vbo.write(tile_instances.tobytes(), offset=0)
        # Separate buffers for the backdrop and the markers: rewriting one buffer between
        # draws in the same frame stalls until the GPU is done with the first draw.
        self.frame_instances = get_new_instances(2, 0, 0)[0]
        self.frame_vao, self.frame_vbo = build_rect_objs(ctx, self.rect_program, self.frame_instances)
        self.rect_instances = get_new_instances(24 + 2 * len(marks), 0, 0)[0]
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, self.rect_instances)
        self.title = DynamicLabel(ctx, (200, 44), 40, bold=True, align="center")
        self.title.set("MAP")
        self.info = DynamicLabel(ctx, (700, 28), 24, bold=True, align="center")
        self.hint = DynamicLabel(ctx, (700, 24), 18, align="center")
        self.hint.set("Click a diamond to set the guide  ·  C clear  ·  M close")
        self.legend = [DynamicLabel(ctx, (220, 26), 20, bold=True) for _ in range(len(KINDS) + 1)]
        for label, text in zip(self.legend, ["You"] + [text for _, _, text in KINDS.values()]):
            label.set(text)
        # The player's dot: filled circles (convex polygons) drawn with the line program.
        self.line_program = load_program(ctx, str(shaders / "line.vert"), str(shaders / "line.frag"))
        self.line_program["u_viewport_size"].value = size
        placeholder = [(0.0, 0.0)] * DOT_SIDES
        # One rim + fill pair each for the map and the legend (own buffers, no stalls).
        self.dots = [[build_polygon_obj(ctx, self.line_program, placeholder, color)
                      for color in ((*INK, 255), (*PLAYER_DOT, 255))] for _ in range(2)]
        self.quads = {}
        for label in (self.title, self.info, self.hint, *self.legend):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    # Layout -----------------------------------------------------------------------

    def origin(self):
        width, height = self.viewport
        return width // 2 - SECTORS * CELL // 2, height // 2 - SECTORS * CELL // 2

    def to_screen(self, x, y):
        left, top = self.origin()
        return left + x / SECTOR_SIZE * CELL, top + y / SECTOR_SIZE * CELL

    def _diamonds(self):
        return [_rect(*self.to_screen(m.x, m.y), DIAMOND, DIAMOND, (0, 0, 0, 0), 45.0) for m in self.marks]

    # Input ------------------------------------------------------------------------

    def toggle(self):
        self.open = not self.open
        self.hovered = None

    def handle(self, action, value):
        """Returns ("select", landmark), "clear", "close", or None."""
        if action in ("map", "pause"):
            self.toggle()
            return "close"
        if action == "clear":
            return "clear"
        if action in ("pointer", "click"):
            hits = check_mouse_collisions(*value, self._diamonds(), "rect")
            # Overlapping diamonds: the one whose middle is nearest the pointer wins.
            nearest = min(hits, key=lambda i: math.dist(self.to_screen(self.marks[i].x, self.marks[i].y),
                                                        value), default=None)
            self.hovered = self.marks[nearest] if nearest is not None else None
            if action == "click" and self.hovered:
                return ("select", self.hovered)
        return None

    # Render -----------------------------------------------------------------------

    def render(self, player_x, player_y, selected: Landmark | None):
        width, height = self.viewport
        left, top = self.origin()
        span = SECTORS * CELL
        frame = [
            _rect(width // 2, height // 2, width, height, (8, 16, 22, 190)),       # Dim the game.
            _rect(width // 2, height // 2, span + 16, span + 16, (*ACCENT, 255)),   # Frame.
        ]
        to_gl(frame, self.frame_instances, "rect")
        self.frame_vbo.write(self.frame_instances.tobytes(), offset=0)
        self.frame_vao.render(moderngl.TRIANGLES, instances=len(frame))
        self.tile_vao.render(moderngl.TRIANGLES, instances=self.tile_count)
        rects = []
        for mark in self.marks:
            x, y = self.to_screen(mark.x, mark.y)
            chosen = mark == selected or mark == self.hovered
            outline = DIAMOND + (8 if chosen else 4)
            rects.append(_rect(x, y, outline, outline, (255, 255, 255, 255) if chosen else (*INK, 255), 45.0))
            rects.append(_rect(x, y, DIAMOND, DIAMOND, (*KINDS[mark.kind][0], 255), 45.0))
        legend_x, legend_y = left - 250, height // 2 - 70
        for i, (color, _, _) in enumerate(KINDS.values(), 1):
            rects.append(_rect(legend_x, legend_y + i * 40, DIAMOND + 4, DIAMOND + 4, (*INK, 255), 45.0))
            rects.append(_rect(legend_x, legend_y + i * 40, DIAMOND, DIAMOND, (*color, 255), 45.0))
        self._draw_rects(rects)
        px, py = self.to_screen(max(0, min(WORLD_SIZE, player_x)), max(0, min(WORLD_SIZE, player_y)))
        self._dot(0, px, py)                   # On top of the diamonds.
        self._dot(1, legend_x, legend_y)       # "You" in the legend.
        focus = self.hovered or selected
        self.info.set(f"{'Guide: ' if focus == selected and focus else ''}{focus.name}" if focus
                      else "No guide selected")
        labels = [(self.title, self.title.record(width // 2, top - 22, ACCENT)),
                  (self.info, self.info.record(width // 2, top + span + 26,
                                               CREAM if focus else MUTED)),
                  (self.hint, self.hint.record(width // 2, top + span + 52, MUTED))]
        for i, label in enumerate(self.legend):
            labels.append((label, label.record(legend_x + 22, legend_y + i * 40 + 1, CREAM)))
        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)

    def _dot(self, which, x, y):
        (rim_vao, rim_vbo), (dot_vao, dot_vbo) = self.dots[which]
        for vao, vbo, radius, color in ((rim_vao, rim_vbo, DOT_RADIUS + DOT_RIM, (*INK, 255)),
                                        (dot_vao, dot_vbo, DOT_RADIUS, (*PLAYER_DOT, 255))):
            points = [(x + math.cos(a) * radius, y + math.sin(a) * radius)
                      for a in (2 * math.pi * i / DOT_SIDES for i in range(DOT_SIDES))]
            update_polygon_obj(vbo, points, color)
            render_polygon(vao, points, fill=True)

    def _draw_rects(self, rects):
        to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))


def guide_line(player, mark: Landmark, compass) -> str:
    dx, dy = mark.x - player.x, mark.y - player.y
    return f"{mark.name}  ·  {math.hypot(dx, dy) / 10:.0f} m {compass(dx, dy)}"
