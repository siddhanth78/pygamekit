"""The inside of the player's house: a separate little level loaded when they walk in.

A living room (couch facing the TV, coffee table, rug, floor lamp, bookshelf, dining
table with chairs, the arcade cabinet) and a bedroom (bed, nightstand lamp, wardrobe).
Most furniture does something with E: sit, sleep, switch a lamp, play the arcade, or a
short remark. The level stands in for the world: region_at, can_place_walker,
can_place_car, nearby_obstacles, and visible_sprites.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from world import TILE_SIZE, Sprite


COLS, ROWS = 14, 9                    # Tiles, walls included.
DIVIDER_X = 9                         # Wall column between the living room and bedroom,
DOORWAY_ROWS = (4, 5)                 # open on these rows.
DOOR_TILE = (2, 0)                    # Front door, in the top wall.
WINDOWS = ((6, 0), (11, 0), (4, 8), (11, 8))
FURNITURE_SIZE = 128                  # 64 px cells drawn at 2x, like the floor.


def at(tx: float, ty: float) -> tuple[float, float]:
    return tx * TILE_SIZE, ty * TILE_SIZE


@dataclass(frozen=True)
class Piece:
    name: str                          # home-atlas sprite.
    x: float
    y: float
    rotation: float = 0.0              # GL degrees; art faces north.
    solid: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class Spot:
    """Something to do with E near (x, y).

    kind: sit, sleep, lamp, arcade, door, or look (a short remark).
    For sit/sleep: pose is placed at (px, py) facing heading, and getting up returns
    the player to (x, y)."""
    kind: str
    label: str
    x: float
    y: float
    reach: float = 44
    px: float = 0.0
    py: float = 0.0
    heading: float = 0.0
    key: str = ""                      # Lamp id, or the remark for "look".


def _pieces():
    return (
        Piece("rug", *at(4.8, 4.3)),
        Piece("lamp_off", *at(6.9, 5.9), 0.0, (20, 20)),   # Floor lamp's base (drawn as on/off).
        Piece("tv", *at(4.8, 1.3), 180.0, (108, 28)),
        Piece("coffee_table", *at(4.8, 3.5), 0.0, (76, 32)),
        Piece("couch", *at(4.8, 5.8), 0.0, (120, 56)),
        Piece("arcade", *at(8.2, 1.35), 180.0, (60, 76)),
        Piece("bookshelf", *at(1.35, 5.4), -90.0, (116, 40)),
        Piece("dining_table", *at(2.4, 3.1), 0.0, (64, 64)),
        Piece("chair", *at(1.55, 3.1), -90.0, (36, 36)),
        Piece("chair", *at(3.25, 3.1), 90.0, (36, 36)),
        Piece("plant", *at(1.4, 7.4), 0.0, (26, 26)),
        Piece("plant", *at(8.3, 7.4), 0.0, (26, 26)),
        Piece("bed", *at(11.3, 2.3), 0.0, (76, 116)),
        Piece("nightstand", *at(12.5, 1.45), 0.0, (44, 44)),
        Piece("wardrobe", *at(12.45, 6.6), 90.0, (108, 52)),
        Piece("plant", *at(10.4, 7.4), 0.0, (26, 26)),
    )


LAMPS = {"living": at(6.9, 5.9), "bedroom": at(12.5, 1.45)}
# The badge board: a white board on the bedroom's east wall, between the bed and the
# wardrobe. E in front of it shows the badges.
BADGE_BOARD = at(12.6, 4.35)
BADGE_SPOT = at(11.9, 4.35)


def _spots():
    return (
        Spot("door", "Go outside", *at(2.5, 1.45), 40),
        Spot("sit", "Sit on the couch", *at(4.35, 4.55), 34, *at(4.35, 5.75), 0.0),
        Spot("sit", "Sit on the couch", *at(5.25, 4.55), 34, *at(5.25, 5.75), 0.0),
        Spot("look", "TV", *at(4.8, 2.25), 40, key="The TV is off."),
        Spot("arcade", "Play the arcade", *at(8.2, 2.35), 40),
        Spot("lamp", "Lamp", *at(6.9, 5.1), 44, key="living"),
        Spot("look", "Bookshelf", *at(2.15, 5.4), 40,
             key="Racing magazines, a road atlas, and a manual you never read."),
        Spot("sit", "Sit at the table", *at(1.55, 4.0), 30, *at(1.55, 3.1), 90.0),
        Spot("sit", "Sit at the table", *at(3.25, 4.0), 30, *at(3.25, 3.1), 270.0),
        Spot("sleep", "Sleep", *at(10.3, 2.4), 40, *at(11.3, 2.35), 180.0),
        Spot("lamp", "Bedside lamp", *at(12.4, 2.5), 44, key="bedroom"),
        Spot("look", "Wardrobe", *at(11.55, 6.6), 40, key="Racing suits, hung up neatly."),
        Spot("badges", "Badge board", *BADGE_SPOT, 40),
        Spot("look", "Plant", *at(8.3, 6.7), 36, key="It could use some water."),
    )


class HomeInterior:
    kind = "home"

    def __init__(self):
        self.width, self.height = COLS * TILE_SIZE, ROWS * TILE_SIZE
        self.pieces = _pieces()
        self.spots = _spots()
        self.lamps = {name: True for name in LAMPS}
        self.entry = at(2.5, 1.55)                 # Just inside the front door.
        self.tiles = {}
        for ty in range(ROWS):
            for tx in range(COLS):
                self.tiles[(tx, ty)] = self._tile(tx, ty)
        self.obstacles = [Sprite("home-atlas", p.name, p.x, p.y, FURNITURE_SIZE, FURNITURE_SIZE,
                                 p.rotation, *p.solid) for p in self.pieces if p.solid[0]]

    def _tile(self, tx, ty):
        if (tx, ty) == DOOR_TILE:
            return "wall_door"
        if (tx, ty) in WINDOWS:
            return "wall_window"
        if tx in (0, COLS - 1) or ty in (0, ROWS - 1):
            return "wall"
        if tx == DIVIDER_X:
            return "floor_wood" if ty in DOORWAY_ROWS else "wall"
        return "floor_bedroom" if tx > DIVIDER_X else "floor_wood"

    # World stand-in -----------------------------------------------------------------

    def region_at(self, x, y):
        return "home"

    def _floor(self, x, y):
        tile = self.tiles.get((int(x // TILE_SIZE), int(y // TILE_SIZE)))
        return tile is not None and tile.startswith("floor") and x >= 0 and y >= 0

    def can_place_walker(self, rect):
        half_w, half_h = rect[7] / 2, rect[8] / 2
        return all(self._floor(rect[0] + dx, rect[1] + dy)
                   for dx in (-half_w, half_w) for dy in (-half_h, half_h))

    def can_place_car(self, rect):
        return False

    def nearby_obstacles(self, x, y):
        return [o for o in self.obstacles if abs(o.x - x) < 120 and abs(o.y - y) < 120]

    def visible_sprites(self, camera_x=0, camera_y=0, width=0, height=0):
        out = [Sprite("terrain-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                      TILE_SIZE, TILE_SIZE) for (tx, ty), name in self.tiles.items()]
        out += [Sprite("home-atlas", p.name, p.x, p.y, FURNITURE_SIZE, FURNITURE_SIZE, p.rotation)
                for p in self.pieces if not p.name.startswith("lamp")]
        # The floor lamp shows on or off (its "on" art has a warm halo); the bedside lamp
        # glows on its nightstand only while it's on.
        x, y = LAMPS["living"]
        out.append(Sprite("home-atlas", "lamp_on" if self.lamps["living"] else "lamp_off", x, y, 72, 72))
        if self.lamps["bedroom"]:
            x, y = LAMPS["bedroom"]
            out.append(Sprite("home-atlas", "lamp_on", x, y, 48, 48))
        out.append(Sprite("home-atlas", "badge_board", *BADGE_BOARD, 64, 64, 90.0))   # Facing into the room.
        return out

    # Things to do ---------------------------------------------------------------------

    def spot_near(self, x, y):
        """The closest thing within reach of (x, y), if any."""
        near = [(math.dist((x, y), (s.x, s.y)), s) for s in self.spots
                if math.dist((x, y), (s.x, s.y)) <= s.reach]
        return min(near, key=lambda item: item[0])[1] if near else None

    def toggle_lamp(self, key) -> bool:
        self.lamps[key] = not self.lamps[key]
        return self.lamps[key]
