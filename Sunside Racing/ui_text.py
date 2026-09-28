"""Pygame-font text uploaded to GL textures for the shared textured-quad shader."""

from __future__ import annotations

import moderngl
import pygame


_fonts: dict[tuple[int, bool], pygame.font.Font] = {}


def font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    if key not in _fonts:
        pygame.font.init()
        _fonts[key] = pygame.font.Font(None, size)
        _fonts[key].set_bold(bold)
    return _fonts[key]


MIN_FIT_SIZE = 10


def _blit_text(surface, text, size, bold, align, cell_rect):
    """Draw text in its cell; text too wide for the cell shrinks until it fits (never
    clipped). Text that already fits keeps its size."""
    image = font(size, bold).render(text, True, (255, 255, 255))
    while image.get_width() > cell_rect.width - 8 and size > MIN_FIT_SIZE:
        size -= 1
        image = font(size, bold).render(text, True, (255, 255, 255))
    if align == "left":
        rect = image.get_rect(midleft=(cell_rect.left + 4, cell_rect.centery))
    elif align == "right":
        rect = image.get_rect(midright=(cell_rect.right - 4, cell_rect.centery))
    else:
        rect = image.get_rect(center=cell_rect.center)
    surface.blit(image, rect)


def upload(ctx, surface, texture=None):
    """Same RGBA, bottom-up upload and NEAREST filtering as gl_utils.load_texture."""
    data = pygame.image.tobytes(surface, "RGBA", True)
    if texture is None:
        texture = ctx.texture(surface.get_size(), 4, data)
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
    else:
        texture.write(data)
    return texture


class LabelAtlas:
    """Fixed labels stacked in equal-height rows; draw one with record(name, ...)."""

    def __init__(self, ctx, cell, labels):
        # labels: {name: (text, size, bold, align)}
        self.cell = cell
        self.rows = {name: row for row, name in enumerate(labels)}
        width, height = cell
        surface = pygame.Surface((width, height * len(labels)), pygame.SRCALPHA)
        for row, (text, size, bold, align) in enumerate(labels.values()):
            _blit_text(surface, text, size, bold, align, pygame.Rect(0, row * height, width, height))
        self.texture = upload(ctx, surface)
        self.grid = (1.0, float(len(labels)))

    def record(self, name, x, y, rgb, align="center"):
        """A 'tex' record; x is the text's left edge when align='left', else its center."""
        width, height = self.cell
        if align == "left":
            x += width // 2
        # Atlas rows count up from the bottom, matching the shared texture shader.
        return [x, y, *rgb, 255, 0, width, height, 0.0, 0, len(self.rows) - 1 - self.rows[name]]


class DynamicLabel:
    """One re-renderable line of text in its own texture."""

    def __init__(self, ctx, size, font_size, bold=False, align="left"):
        self.ctx = ctx
        self.size = size
        self.font_size, self.bold, self.align = font_size, bold, align
        self.text = None
        self.texture = upload(ctx, pygame.Surface(size, pygame.SRCALPHA))

    def set(self, text: str):
        if text == self.text:
            return
        self.text = text
        surface = pygame.Surface(self.size, pygame.SRCALPHA)
        _blit_text(surface, text, self.font_size, self.bold, self.align, surface.get_rect())
        upload(self.ctx, surface, self.texture)

    def record(self, x, y, rgb):
        """A 'tex' record; x is the left edge for left-aligned labels, the right edge for
        right-aligned ones, else the center."""
        width, height = self.size
        if self.align == "left":
            x += width // 2
        elif self.align == "right":
            x -= width // 2
        return [x, y, *rgb, 255, 0, width, height, 0.0, 0, 0]
