"""Heads-up display: region and racing-center guidance, speedometer, and guide arrow."""

from __future__ import annotations

import math
import time

import moderngl

from gl_utils import (
    build_polygon_obj, build_rect_objs, build_tex_objs, get_new_instances, load_program,
    render_polygon, to_gl, update_polygon_obj,
)
from ui_text import DynamicLabel


ORBIT_RADIUS = 56     # Arrow base distance from the car's center; clears the 64 px sprite.
ARROW_LENGTH = 32
ARROW_WIDTH = 16  # Long and narrow so the tip reads clearly.
ARRIVED_DISTANCE = 300  # Hide once the car is about at the center's plaza.
FILL = (242, 202, 87, 235)
OUTLINE = (32, 45, 52, 235)


def _triangle(tip_x, tip_y, dx, dy, length, width):
    back_x, back_y = tip_x - dx * length, tip_y - dy * length
    side_x, side_y = -dy * width / 2, dx * width / 2
    return [(tip_x, tip_y), (back_x + side_x, back_y + side_y),
            (back_x - side_x, back_y - side_y)]


def orbit_tip(target_x, target_y, car_x, car_y, camera_x, camera_y, zoom=1.0):
    """Screen tip and unit direction of the arrow circling the player, or None on arrival."""
    dx, dy = target_x - car_x, target_y - car_y
    distance = math.hypot(dx, dy)
    if distance < ARRIVED_DISTANCE:
        return None
    dx, dy = dx / distance, dy / distance
    reach = ORBIT_RADIUS + ARROW_LENGTH
    # The orbit is in screen pixels; only the player's position scales with zoom.
    screen_x, screen_y = (car_x - camera_x) * zoom, (car_y - camera_y) * zoom
    return screen_x + dx * reach, screen_y + dy * reach, dx, dy


class CenterArrow:
    def __init__(self, ctx, toolkit_root, viewport):
        self.program = load_program(ctx, str(toolkit_root / "shaders" / "line.vert"),
                                    str(toolkit_root / "shaders" / "line.frag"))
        self.program["u_viewport_size"].value = tuple(float(v) for v in viewport)
        self.viewport = viewport
        placeholder = [(0.0, 0.0)] * 3
        self.outline_vao, self.outline_vbo = build_polygon_obj(ctx, self.program, placeholder, OUTLINE)
        self.fill_vao, self.fill_vbo = build_polygon_obj(ctx, self.program, placeholder, FILL)

    def render(self, target_x, target_y, car_x, car_y, camera_x, camera_y, zoom=1.0, fill=FILL):
        """Orbit the player, pointing at the target, until the player arrives there.
        fill: RGB or RGBA; the arrow is yellow for missions, colored by landmark otherwise."""
        fill = tuple(fill) if len(fill) == 4 else (*fill, FILL[3])
        placement = orbit_tip(target_x, target_y, car_x, car_y, camera_x, camera_y, zoom)
        if placement is None:
            return
        tip_x, tip_y, dx, dy = placement
        outline = _triangle(tip_x + dx * 4, tip_y + dy * 4, dx, dy,
                            ARROW_LENGTH + 8, ARROW_WIDTH + 8)
        tip = _triangle(tip_x, tip_y, dx, dy, ARROW_LENGTH, ARROW_WIDTH)
        update_polygon_obj(self.outline_vbo, outline, OUTLINE)
        render_polygon(self.outline_vao, outline, fill=True)
        update_polygon_obj(self.fill_vbo, tip, fill)
        render_polygon(self.fill_vao, tip, fill=True)


def compass(dx, dy):
    """Eight-way compass label for a screen-space offset (y points south)."""
    labels = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return labels[round(math.degrees(math.atan2(dx, -dy)) / 45) % 8]


SPEED_SEGMENTS = 20
GREEN, YELLOW, RED = (86, 196, 112), (242, 202, 87), (226, 88, 72)
HUD_PANEL = (20, 32, 40, 205)
HUD_ACCENT = (242, 202, 87, 255)
UNLIT = (52, 70, 78, 255)
CREAM = (238, 232, 208)
MUTED = (160, 180, 180)
MARGIN = 24
TEXT_REFRESH = 0.2   # Seconds between region and guidance text re-renders.
SPEED_REFRESH = 0.1  # Seconds between speed number re-renders.
SPEEDO_SCALE = 2  # Multiplies the compact 124 x 32 speedometer layout.


def speed_color(t: float):
    """Green at 0, yellow at 0.5, red at 1."""
    a, b, k = (GREEN, YELLOW, t * 2) if t < 0.5 else (YELLOW, RED, (t - 0.5) * 2)
    return tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))


def lit_segments(speed: float, top_speed: float) -> int:
    fraction = min(1.0, abs(speed) / top_speed)
    return max(1 if abs(speed) >= 1 else 0, round(fraction * SPEED_SEGMENTS))


def _rect(x, y, width, height, rgba):
    return [x, y, *rgba, 0.0, width, height, 0.0]


class Hud:
    def __init__(self, ctx, toolkit_root, viewport, top_speed):
        self.viewport = viewport
        self.top_speed = top_speed
        shaders = toolkit_root / "shaders"
        size = tuple(float(v) for v in viewport)
        self.rect_program = load_program(ctx, str(shaders / "rect.vert"), str(shaders / "rect.frag"))
        self.text_program = load_program(ctx, str(shaders / "tex.vert"), str(shaders / "tex.frag"))
        self.rect_program["u_viewport_size"].value = size
        self.text_program["u_viewport_size"].value = size
        self.text_program["u_texture"].value = 0
        self.text_program["u_atlas_grid"].value = (1.0, 1.0)
        rects, _, _ = get_new_instances(48, 0, 0)
        self.rect_instances = rects
        self.rect_vao, self.rect_vbo = build_rect_objs(ctx, self.rect_program, rects)
        self.region = DynamicLabel(ctx, (360, 40), 36, bold=True)
        self.guide = DynamicLabel(ctx, (360, 28), 22)
        k = SPEEDO_SCALE
        self.speed = DynamicLabel(ctx, (44 * k, 24 * k), 24 * k, bold=True, align="center")
        self.unit = DynamicLabel(ctx, (44 * k, 14 * k), 14 * k + 1, bold=True)
        self.unit.set("KM/H")
        self.prompt = DynamicLabel(ctx, (320, 28), 24, bold=True, align="center")
        self.mission_title = DynamicLabel(ctx, (360, 32), 26, bold=True)
        self.mission_line = DynamicLabel(ctx, (360, 28), 24)
        self.banner = DynamicLabel(ctx, (640, 120), 110, bold=True, align="center")
        self.toast = DynamicLabel(ctx, (120, 24), 20, bold=True, align="center")
        self._text_due = self._speed_due = 0.0
        # One instance buffer per label: rewriting a single shared buffer between
        # draws in the same frame stalls until the GPU finishes the previous draw.
        self.quads = {}
        for label in (self.region, self.guide, self.speed, self.unit, self.prompt,
                      self.mission_title, self.mission_line, self.banner, self.toast):
            instances = get_new_instances(0, 0, 1)[2]
            self.quads[id(label)] = (instances, *build_tex_objs(ctx, self.text_program, instances))

    def render(self, speed: float, region: str, guide: str, prompt: str = "",
               show_speed: bool = True, mission=None, banner: str = "", toast: str = ""):
        """mission: (title, line) for the top-right panel; banner: big centered text;
        toast: a small top-center note such as "Saved"."""
        width, height = self.viewport
        # Re-rendering text uploads a texture, so refresh it a few times a second.
        now = time.perf_counter()
        if now >= self._text_due:
            self.region.set(region.upper())
            self.guide.set(guide)
            self._text_due = now + TEXT_REFRESH
        if now >= self._speed_due:
            self.speed.set(f"{abs(speed):.0f}")
            self._speed_due = now + SPEED_REFRESH
        self.prompt.set(prompt)
        if mission:
            self.mission_title.set(mission[0])
            self.mission_line.set(mission[1])  # Timers and crash flashes update every frame.
        self.banner.set(banner)
        self.toast.set(toast)

        # Top-left: where you are and where the racing center is.
        info_w, info_h = 384, 84
        info_x, info_y = MARGIN + info_w // 2, MARGIN + info_h // 2
        # Bottom-right speedometer; every measure below scales with SPEEDO_SCALE.
        k = SPEEDO_SCALE
        speed_w, speed_h = 124 * k, 32 * k
        speed_x, speed_y = width - MARGIN - speed_w // 2, height - MARGIN - speed_h // 2
        speed_left = speed_x - speed_w // 2
        bar_left, bar_bottom = speed_left + 54 * k, speed_y + 11 * k
        rects = [
            _rect(info_x + 4, info_y + 5, info_w, info_h, (8, 14, 18, 120)),
            _rect(info_x, info_y, info_w, info_h, HUD_PANEL),
            _rect(MARGIN + 3, info_y, 6, info_h, HUD_ACCENT),
        ]
        tint = speed_color(min(1.0, abs(speed) / self.top_speed))
        labels = [
            (self.region, self.region.record(MARGIN + 20, MARGIN + 30, CREAM)),
            (self.guide, self.guide.record(MARGIN + 20, MARGIN + 62, MUTED)),
        ]
        if show_speed:
            rects += [
                _rect(speed_x + 2 * k, speed_y + 2 * k, speed_w, speed_h, (8, 14, 18, 120)),
                _rect(speed_x, speed_y, speed_w, speed_h, HUD_PANEL),
                _rect(speed_left + k, speed_y, 2 * k, speed_h, HUD_ACCENT),
            ]
            lit = lit_segments(speed, self.top_speed)
            for i in range(SPEED_SEGMENTS):
                color = (*speed_color(i / (SPEED_SEGMENTS - 1)), 255) if i < lit else UNLIT
                # Segments step up in height like a tachometer.
                seg_h = (4 + i // 4) * k
                rects.append(_rect(bar_left + (1 + i * 3) * k, bar_bottom - seg_h / 2, 2 * k,
                                   seg_h, color))
            labels += [
                (self.speed, self.speed.record(speed_left + 28 * k, speed_y, tint)),
                (self.unit, self.unit.record(bar_left, speed_y - 7 * k, MUTED)),
            ]
        if mission:
            # Top-right mission panel; the accent turns red while a crash flashes.
            mission_w, mission_h = 384, 84
            mission_x = width - MARGIN - mission_w // 2
            alert = mission[1].startswith("CRASH") or mission[1].startswith("Time 0.")
            accent = (212, 80, 66, 255) if alert else HUD_ACCENT
            rects += [
                _rect(mission_x + 4, info_y + 5, mission_w, mission_h, (8, 14, 18, 120)),
                _rect(mission_x, info_y, mission_w, mission_h, HUD_PANEL),
                _rect(width - MARGIN - 3, info_y, 6, mission_h, accent),
            ]
            left = width - MARGIN - mission_w + 16
            labels += [
                (self.mission_title, self.mission_title.record(left, MARGIN + 28, CREAM)),
                (self.mission_line, self.mission_line.record(
                    left, MARGIN + 60, (240, 140, 120) if alert else MUTED)),
            ]
        if toast:
            rects += [_rect(width // 2, MARGIN + 14, 120, 28, HUD_PANEL),
                      _rect(width // 2 - 58, MARGIN + 14, 4, 28, HUD_ACCENT)]
            labels.append((self.toast, self.toast.record(width // 2 + 2, MARGIN + 15, CREAM)))
        if banner:
            labels.append((self.banner, self.banner.record(width // 2, height // 2 - 120, CREAM)))
        if prompt:
            # Bottom-center interaction prompt, e.g. "E  Get in".
            prompt_y = height - MARGIN - 20
            rects += [
                _rect(width // 2 + 3, prompt_y + 4, 300, 40, (8, 14, 18, 120)),
                _rect(width // 2, prompt_y, 300, 40, HUD_PANEL),
                _rect(width // 2, prompt_y - 19, 300, 2, HUD_ACCENT),
            ]
            labels.append((self.prompt, self.prompt.record(width // 2, prompt_y + 1, CREAM)))
        _, self.rect_instances = to_gl(rects, self.rect_instances, "rect")
        self.rect_vbo.write(self.rect_instances[:len(rects)].tobytes(), offset=0)
        self.rect_vao.render(moderngl.TRIANGLES, instances=len(rects))

        for label, record in labels:
            instances, vao, vbo = self.quads[id(label)]
            to_gl([record], instances, "tex")
            vbo.write(instances.tobytes(), offset=0)
            label.texture.use(location=0)
            vao.render(moderngl.TRIANGLES, instances=1)
