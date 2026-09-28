"""A deterministic 64 x 64 sector world, generated only around the camera."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache

import highway
from gl_utils import get_rect_corners
from world_save import DEFAULT_WORLD_SEED, WorldStore


SECTORS = 64
TILES_PER_SECTOR = 8
TILE_SIZE = 64
SECTOR_SIZE = TILES_PER_SECTOR * TILE_SIZE
WORLD_SIZE = SECTORS * SECTOR_SIZE
GATHER_MARGIN = 96  # World px of sprite overhang considered outside the view.
ISLAND_ROW = 31  # Sector row through the island's middle and the ferry crossing.
CITY_SECTORS_X = (21, 32)  # Inclusive sector bounds of the city grid.
CITY_SECTORS_Y = (24, 38)

# Road waypoints in tile coordinates; shared by world generation and traffic.
SNOW_ROUTE = (
    (172, 308), (172, 318), (164, 318), (164, 330), (148, 330), (148, 346),
    (132, 346), (132, 370), (112, 370), (112, 388), (100, 388),
)
DIRT_ROUTES = (
    ((260, 308), (260, 320), (276, 320), (276, 340), (292, 340), (292, 360),
     (316, 360), (316, 384), (328, 384)),
    ((292, 340), (292, 325), (309, 325)),
    ((316, 360), (330, 360), (330, 371)),
)

CENTERS = {
    (25, 30): "center_city", (12, 14): "center_jungle",
    (40, 14): "center_desert", (12, 48): "center_snow",
    (40, 48): "center_rural", (56, 31): "center_island",
}
CITY_BUILDINGS = (
    "city_apartment_red", "city_apartment_blue", "city_office_low", "city_office_tower",
    "city_shop_red", "city_shop_blue", "city_warehouse", "city_parking_deck",
)
PARKED_SIZE = 56
PARKED_SOLID = (18, 38)
STALL_Y = 24  # Parked car center below its tile's top edge; the aisle is below that.
PARKED_CARS = (
    "traffic_red", "traffic_blue", "traffic_green", "traffic_white", "traffic_gray",
    "traffic_yellow", "traffic_taxi", "traffic_van", "traffic_pickup", "traffic_suv",
    "traffic_wagon", "traffic_compact",
)
# Fishing piers: (name, sector row or column along the shore, outward direction).
# West and east piers sit on a sector row, the north pier on a column.
DOCK_SITES = (("west", ISLAND_ROW, (-1, 0)), ("north", 26, (0, -1)), ("east", 22, (1, 0)))
PIER_TILES = 4      # Tiles a pier reaches out over the sea.
PIER_HALF = 22      # Half the plank width, px (the art's planks span 44 of 64 px).
END_FACING = {(0, -1): 0.0, (-1, 0): 90.0, (0, 1): 180.0, (1, 0): -90.0}  # GL rotation.


@dataclass(frozen=True)
class Dock:
    """A walkable pier: tiles from the shore outward; only people can use it."""
    name: str
    direction: tuple[int, int]
    tiles: tuple[tuple[int, int], ...]

    @property
    def end(self) -> tuple[int, int]:
        return self.tiles[-1]

    @property
    def heading(self) -> float:
        """Degrees clockwise from north, facing out to sea."""
        return {(0, -1): 0.0, (1, 0): 90.0, (0, 1): 180.0, (-1, 0): 270.0}[self.direction]

    def contains(self, x: float, y: float) -> bool:
        """On the planks: between the shore and the end, within PIER_HALF of the axis."""
        (x0, y0), (x1, y1) = self.tiles[0], self.tiles[-1]
        dx, dy = self.direction
        left = min(x0, x1) * TILE_SIZE + (6 if dx < 0 else 0)
        right = (max(x0, x1) + 1) * TILE_SIZE - (6 if dx > 0 else 0)
        top = min(y0, y1) * TILE_SIZE + (6 if dy < 0 else 0)
        bottom = (max(y0, y1) + 1) * TILE_SIZE - (6 if dy > 0 else 0)
        if dx:
            mid = (y0 + 0.5) * TILE_SIZE
            return left <= x <= right and abs(y - mid) <= PIER_HALF
        mid = (x0 + 0.5) * TILE_SIZE
        return top <= y <= bottom and abs(x - mid) <= PIER_HALF

    def at_end(self, x: float, y: float) -> bool:
        return self.contains(x, y) and (int(x // TILE_SIZE), int(y // TILE_SIZE)) == self.end

    def shore(self) -> tuple[float, float]:
        """A point on the beach just inland of the pier."""
        tx, ty = self.tiles[0]
        dx, dy = self.direction
        return (tx + 0.5 - 2 * dx) * TILE_SIZE, (ty + 0.5 - 2 * dy) * TILE_SIZE


# The player's house: the corner lot of a quiet sector on the city's west edge (no
# pedestrian block covers it), with a private two-stall parking lot on its east side:
# one home_lot tile (the public lots' art with the stalls along the bottom and the aisle
# along the top), opening onto the road. Only the player's car parks there, in the west
# stall facing up toward the aisle; the east stall stays empty.
HOME_SECTOR = (21, 31)
HOME_LOT_TILE = (3, 1)                # Local tile of the two-stall lot.
_HOME_X, _HOME_Y = HOME_SECTOR[0] * SECTOR_SIZE, HOME_SECTOR[1] * SECTOR_SIZE
HOME_HOUSE = (_HOME_X + 1.6 * TILE_SIZE, _HOME_Y + 2 * TILE_SIZE)        # House center.
HOME_PARK = (_HOME_X + HOME_LOT_TILE[0] * TILE_SIZE + 20,                  # Car: x, y, heading
             _HOME_Y + (HOME_LOT_TILE[1] + 1) * TILE_SIZE - STALL_Y, 0.0)  # (facing up).
HOME_DOOR = (_HOME_X + 2.5 * TILE_SIZE, _HOME_Y + 2.2 * TILE_SIZE)      # Stand here to go inside
                                                                         # (beyond the car's reach).

# Every mainland racing center has its own two-stall lot (the home_lot tile) beside its
# plaza; fast travel parks the car there, west stall, facing up. Local tile in the
# center's sector: east of the plaza, or (city) where the building east of it would be.
CENTER_LOT_TILE = {"city": (5, 1), "other": (6, 3)}

# The General Store: one city building, chosen from the seed, at least STORE_MIN_BLOCKS
# sectors from the city's racing center. Its door faces the sidewalk the block's
# pedestrians walk (west for a lot at local x 6, east for x 2).
STORE_MIN_BLOCKS = 2
STORE_DOOR_OUT = 64           # px from the building's center to stand at its door.


@dataclass(frozen=True)
class StoreSite:
    sector: tuple[int, int]
    lot: tuple[int, int]
    x: float                  # Building center.
    y: float
    face: int                 # -1: door on the west wall, +1: east.

    @property
    def rotation(self) -> float:
        """GL rotation turning the art's front (south) toward the door side."""
        return -90.0 if self.face < 0 else 90.0

    @property
    def door(self) -> tuple[float, float]:
        return self.x + self.face * STORE_DOOR_OUT, self.y

# The farmhouse: one per world, on the valid rural sector nearest the region's middle
# (the middle itself is on the highway). Valid: rural all around, no highway within a
# sector, no dirt road in it or on its edge, and clear of the rural racing center. It
# is chosen by these rules alone, so every seed has it in the same place. Local tiles:
# the house on the northwest, a gravel yard down the west side and along the north,
# and the 5 x 5 mud plot in the southeast corner.
FARM_HOUSE_TILE = (1.5, 1.5)          # House center, local tiles.
FARM_HOUSE_SIZE, FARM_HOUSE_SOLID = 136, (112, 104)
FARM_DOOR_TILE = (1.5, 3.05)          # Stand here (south of the house) to go in.
FARM_PLOT = (3, 3, 5, 5)              # Local tile x, y, width, height.
FARM_YARD = [(x, y) for x in range(3) for y in range(3, 8)] + [(x, y) for x in range(3, 8) for y in range(3)]


@dataclass(frozen=True)
class FarmSite:
    sector: tuple[int, int]

    def at(self, lx: float, ly: float) -> tuple[float, float]:
        return (self.sector[0] * TILES_PER_SECTOR + lx) * TILE_SIZE, (self.sector[1] * TILES_PER_SECTOR + ly) * TILE_SIZE

    @property
    def house(self) -> tuple[float, float]:
        return self.at(*FARM_HOUSE_TILE)

    @property
    def door(self) -> tuple[float, float]:
        return self.at(*FARM_DOOR_TILE)

    def plot_tiles(self) -> list[tuple[int, int]]:
        """World tiles of the mud plot, row by row."""
        x0, y0, w, h = FARM_PLOT
        tx, ty = self.sector[0] * TILES_PER_SECTOR, self.sector[1] * TILES_PER_SECTOR
        return [(tx + x0 + i, ty + y0 + j) for j in range(h) for i in range(w)]


# Encampments: offsets from the camp center (the sector's middle).
CAMPS_PER_REGION = 5
CAMP_TENTS = ((-88, -56), (84, -60), (6, 92))
CAMP_SEATS = ((-14, -40), (14, -40))   # On the log bench, facing the fire.
CAMP_RING = 58                         # Radius of the walkable ring around the fire.
CAMP_STYLE = {  # region -> (tent sprite, trampled ground tile)
    "desert": ("tent_tan", "dirt_gravel"),
    "jungle": ("tent_green", "jungle_mud"),
}

# Region -> (prop choices, scatter attempts per sector).
SCATTER = {
    "jungle": (("jungle_tree_a", "jungle_tree_b", "jungle_tree_c", "jungle_tree_d",
                "bush_green"), 7),
    "desert": (("cactus_a", "cactus_b", "cactus_c", "rock_desert_a", "rock_desert_b",
                "bush_dry"), 5),
    "snow": (("pine_a", "pine_b", "pine_c", "pine_d", "rock_snow_a", "rock_snow_b"), 6),
    "rural": (("bush_green", "hay_a", "hay_b", "jungle_tree_a", "jungle_tree_d",
               "rock_desert_a"), 4),
    "beach": (("palm_a", "palm_b", "palm_c", "bush_dry"), 3),
    "island": (("palm_a", "palm_b", "palm_c", "rock_desert_a", "bush_green"), 5),
}


@dataclass(frozen=True)
class Sprite:
    atlas: str
    name: str
    x: float
    y: float
    width: float
    height: float
    rotation: float = 0.0
    solid_width: float = 0.0
    solid_height: float = 0.0

    def obstacle_record(self) -> list[float]:
        return [self.x, self.y, 255, 255, 255, 255, 0, self.solid_width,
                self.solid_height, self.rotation]


def _segment(tiles: set[tuple[int, int]], start: tuple[int, int], end: tuple[int, int]):
    x0, y0 = start
    x1, y1 = end
    if x0 != x1 and y0 != y1:
        raise ValueError("Road waypoints must join horizontally or vertically")
    for y in range(min(y0, y1), max(y0, y1) + 1):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            tiles.add((x, y))


def _route(tiles: set[tuple[int, int]], waypoints: list[tuple[int, int]]):
    for start, end in zip(waypoints, waypoints[1:]):
        _segment(tiles, start, end)


class World:
    def __init__(self, seed: int = DEFAULT_WORLD_SEED, store: WorldStore | None = None):
        if type(seed) is not int:
            raise ValueError("World seed must be an integer")
        self.store = store
        self.seed = store.seed if store is not None else seed
        self.snow_roads: set[tuple[int, int]] = set()
        self.dirt_roads: set[tuple[int, int]] = set()
        _route(self.snow_roads, list(SNOW_ROUTE))
        for route in DIRT_ROUTES:
            _route(self.dirt_roads, list(route))
        # The highway loop, its on-ramps, and its exits (see highway.py).
        self.highway_roads = highway.build(self)
        self.roads = highway.RoadIndex(self.highway_roads, SECTOR_SIZE)
        self._highway_pieces: dict[tuple[int, int], list[Sprite]] = {}
        for road in self.highway_roads:
            for name, x, y, width, length, rotation in highway.pieces(road):
                self._highway_pieces.setdefault((int(x // SECTOR_SIZE), int(y // SECTOR_SIZE)), []).append(
                    Sprite("highway-atlas", name, x, y, width, length, rotation))
        # Ferry docks face each other across the channel on the island's row.
        self.mainland_dock = (max(sx for sx in range(SECTORS)
                                  if self._landmass(sx, ISLAND_ROW) == "mainland"), ISLAND_ROW)
        self.island_dock = (min(sx for sx in range(SECTORS)
                                if self._landmass(sx, ISLAND_ROW) == "island"), ISLAND_ROW)
        self.docks = self._place_docks()
        self.pier_tiles = {tile: (dock, i) for dock in self.docks for i, tile in enumerate(dock.tiles)}
        # The Desert Mining Factory, its biofuel depot, and its cargo dock (see factory.py).
        import factory
        self.factory = factory.choose_site(self)
        self._factory_sprites = {}
        for sprite in factory.exterior_sprites(self.factory):
            key = (int(sprite.x // SECTOR_SIZE), int(sprite.y // SECTOR_SIZE))
            self._factory_sprites.setdefault(key, []).append(sprite)
        # Elite Island: the eight club camps and the ferry (see island.py).
        import island
        self.island_camps = island.choose_camps(self)
        self._camp_sectors = {camp.sector: camp for camp in self.island_camps.values()}
        self._island_sprites = {}
        extra = [s for camp in self.island_camps.values() for s in island.camp_sprites(camp, self.seed)]
        for sprite in extra + island.ferry_sprites(self):
            key = (int(sprite.x // SECTOR_SIZE), int(sprite.y // SECTOR_SIZE))
            self._island_sprites.setdefault(key, []).append(sprite)
        self.camps = self._choose_camps()
        self.general_store = self._choose_store()
        self.farm = self._choose_farm()
        # The Snow Fair: its block, and its spur road down to the snow road (see fair.py).
        from fair import choose_site, exterior_sprites
        self.fair = choose_site(self)
        self.snow_roads.update(self.fair.spur)
        self._fair_sprites = {}
        for sprite in exterior_sprites(self.fair, self.seed):
            key = (int(sprite.x // SECTOR_SIZE), int(sprite.y // SECTOR_SIZE))
            self._fair_sprites.setdefault(key, []).append(sprite)

    def _place_docks(self) -> tuple[Dock, ...]:
        """Fishing piers on the mainland's west, north, and east shores."""
        docks = []
        for name, line, (dx, dy) in DOCK_SITES:
            mid = TILES_PER_SECTOR // 2
            if dx:  # Along a sector row: the outermost mainland sector that way.
                land = [sx for sx in range(SECTORS) if self._landmass(sx, line) == "mainland"]
                sx = min(land) if dx < 0 else max(land)
                edge = sx * TILES_PER_SECTOR + (-1 if dx < 0 else TILES_PER_SECTOR)
                tiles = tuple((edge + dx * i, line * TILES_PER_SECTOR + mid) for i in range(PIER_TILES))
            else:
                land = [sy for sy in range(SECTORS) if self._landmass(line, sy) == "mainland"]
                sy = min(land) if dy < 0 else max(land)
                edge = sy * TILES_PER_SECTOR + (-1 if dy < 0 else TILES_PER_SECTOR)
                tiles = tuple((line * TILES_PER_SECTOR + mid, edge + dy * i) for i in range(PIER_TILES))
            docks.append(Dock(name, (dx, dy), tiles))
        return tuple(docks)

    def dock_at(self, x: float, y: float):
        """The pier whose planks are under (x, y), if any."""
        entry = self.pier_tiles.get((int(x // TILE_SIZE), int(y // TILE_SIZE)))
        return entry[0] if entry and entry[0].contains(x, y) else None

    def _choose_store(self) -> StoreSite:
        city_center = next(s for s, name in CENTERS.items() if name == "center_city")
        lots = []
        for sy in range(CITY_SECTORS_Y[0], CITY_SECTORS_Y[1] + 1):
            for sx in range(CITY_SECTORS_X[0], CITY_SECTORS_X[1] + 1):
                if (max(abs(sx - city_center[0]), abs(sy - city_center[1])) < STORE_MIN_BLOCKS
                        or (sx, sy) in CENTERS or (sx, sy) == HOME_SECTOR):
                    continue
                # Lots at local x 2 belong to the block to the west; the city's first column
                # has none, so its pedestrians would never visit.
                lots += [((sx, sy), lot) for lot in ((2, 2), (6, 2), (2, 6), (6, 6))
                         if not (lot[0] == 2 and sx == CITY_SECTORS_X[0])]
        (sx, sy), (lx, ly) = random.Random(f"{self.seed}-store").choice(sorted(lots))
        return StoreSite((sx, sy), (lx, ly), sx * SECTOR_SIZE + lx * TILE_SIZE,
                         sy * SECTOR_SIZE + ly * TILE_SIZE, -1 if lx == 6 else 1)

    def _choose_farm(self) -> FarmSite:
        rural = [(sx, sy) for sy in range(SECTORS) for sx in range(SECTORS) if self.region(sx, sy) == "rural"]
        mid_x = sum(sx + 0.5 for sx, _ in rural) / len(rural)
        mid_y = sum(sy + 0.5 for _, sy in rural) / len(rural)
        center = next(s for s, name in CENTERS.items() if name == "center_rural")
        near_highway = self.roads.sectors()

        def valid(sx, sy):
            around = [(sx + dx, sy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            return (all(self.region(*s) == "rural" for s in around)
                    and not any(s in near_highway or s in self.camps for s in around)
                    and max(abs(sx - center[0]), abs(sy - center[1])) >= 2
                    and not any(self._road_style(sx * TILES_PER_SECTOR + i, sy * TILES_PER_SECTOR + j)
                                for i in range(-1, TILES_PER_SECTOR + 1) for j in range(-1, TILES_PER_SECTOR + 1)))

        best = min((s for s in rural if valid(*s)),
                   key=lambda s: ((s[0] + 0.5 - mid_x) ** 2 + (s[1] + 0.5 - mid_y) ** 2, s))
        return FarmSite(best)

    def _choose_camps(self) -> dict[tuple[int, int], str]:
        """Spread a few encampments through the desert and jungle, clear of centers."""
        rng = random.Random(self.seed * 7 + 17)
        camps: dict[tuple[int, int], str] = {}
        for region in CAMP_STYLE:
            candidates = [(sx, sy) for sy in range(SECTORS) for sx in range(SECTORS)
                          if self.region(sx, sy) == region
                          and all(max(abs(sx - cx), abs(sy - cy)) >= 2 for cx, cy in CENTERS)]
            rng.shuffle(candidates)
            chosen = []
            on_highway = self.roads.sectors()
            factory = self.factory.sectors()[:2]
            for sector in candidates:
                if sector in on_highway:
                    continue   # Keep tents and the fire off the highway and its exits.
                if any(max(abs(sector[0] - fx), abs(sector[1] - fy)) <= 1 for fx, fy in factory):
                    continue   # And clear of the Mining Factory.
                if all(max(abs(sector[0] - x), abs(sector[1] - y)) >= 3 for x, y in chosen):
                    chosen.append(sector)
                if len(chosen) == CAMPS_PER_REGION:
                    break
            camps.update({sector: region for sector in chosen})
        return camps

    def camp_center(self, sx: int, sy: int) -> tuple[float, float]:
        return (sx + 0.5) * SECTOR_SIZE, (sy + 0.5) * SECTOR_SIZE

    @staticmethod
    def tent_facing(dx: float, dy: float) -> float:
        """Rotation that turns a tent's south-facing door toward the fire."""
        return math.degrees(math.atan2(-dx, -dy))

    @staticmethod
    def _landmass(sx: int, sy: int) -> str:
        if not (0 <= sx < SECTORS and 0 <= sy < SECTORS):
            return "sea"
        main = ((sx - 26) / 21.5) ** 2 + ((sy - 31) / 27.0) ** 2 <= 1
        island = ((sx - 56) / 4.3) ** 2 + ((sy - 31) / 6.8) ** 2 <= 1
        if island:
            return "island"
        return "mainland" if main else "sea"

    @lru_cache(maxsize=4096)
    def region(self, sx: int, sy: int) -> str:
        mass = self._landmass(sx, sy)
        if mass == "sea":
            return "sea"
        if any(self._landmass(sx + dx, sy + dy) == "sea"
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            return "beach"
        if mass == "island":
            return "island"
        if (CITY_SECTORS_X[0] <= sx <= CITY_SECTORS_X[1]
                and CITY_SECTORS_Y[0] <= sy <= CITY_SECTORS_Y[1]):
            return "city"
        if sy < 31:
            return "jungle" if sx < 27 else "desert"
        return "snow" if sx < 27 else "rural"

    def region_at(self, x: float, y: float) -> str:
        if not (0 <= x < WORLD_SIZE and 0 <= y < WORLD_SIZE):
            return "sea"
        return self.region(int(x // SECTOR_SIZE), int(y // SECTOR_SIZE))

    def is_ice(self, x: float, y: float) -> bool:
        """The snow region's icy roads (and the highway's icy exit), where cars slide.
        The highway's asphalt is never ice, even where the snow road crosses under it."""
        road = self.roads.road_at(x, y)
        if road in highway.ASPHALT:
            return False
        tx, ty = int(x // TILE_SIZE), int(y // TILE_SIZE)
        return road == "ice" or (self.region_at(x, y) == "snow" and self._road_style(tx, ty) == "ice")

    def surface_at(self, x: float, y: float) -> str:
        """What the car drives on: the region, except the highway's asphalt, which is city
        road (city grip, top speed, and city mastery speed)."""
        return "city" if self.roads.road_at(x, y) in highway.ASPHALT else self.region_at(x, y)

    def on_highway(self, x: float, y: float) -> bool:
        return self.roads.road_at(x, y) in highway.ASPHALT

    def is_drivable(self, x: float, y: float) -> bool:
        return self.region_at(x, y) != "sea"

    def can_place_walker(self, rect: list[float]) -> bool:
        """People can also stand on the fishing piers' planks, over the sea."""
        corners = get_rect_corners(rect[0], rect[1], rect[7], rect[8], rect[9])
        return all(self.is_drivable(x, y) or self.dock_at(x, y) is not None
                   for x, y in [(rect[0], rect[1]), *corners])

    def can_place_car(self, rect: list[float]) -> bool:
        corners = get_rect_corners(rect[0], rect[1], rect[7], rect[8], rect[9])
        return all(self.is_drivable(x, y) for x, y in [
            (rect[0], rect[1]), *corners,
        ])

    def _road_style(self, tx: int, ty: int) -> str | None:
        region = self.region(tx // TILES_PER_SECTOR, ty // TILES_PER_SECTOR)
        if region == "city" and (tx % TILES_PER_SECTOR == 4 or ty % TILES_PER_SECTOR == 4):
            return "city"
        if region == "snow" and (tx, ty) in self.snow_roads:
            return "ice"
        if region == "rural" and (tx, ty) in self.dirt_roads:
            return "dirt"
        return None

    def _road_tile(self, tx: int, ty: int, material: str) -> str:
        neighbors = "".join(direction for direction, dx, dy in (
            ("N", 0, -1), ("S", 0, 1), ("E", 1, 0), ("W", -1, 0),
        ) if self._road_style(tx + dx, ty + dy))
        if len(neighbors) == 4:
            shape = "cross"
        elif len(neighbors) == 3:
            if material == "city":
                shape = {"NSE": "tee", "NSW": "t_nsw", "NEW": "t_new", "SEW": "t_sew"}[neighbors]
                return f"city_{shape}"
            shape = "cross"  # A small extra spur is preferable to a broken route.
        elif len(neighbors) == 2 and neighbors not in ("NS", "EW"):
            shape = neighbors.lower()
        elif "N" in neighbors or "S" in neighbors:
            shape = "ns"
        else:
            shape = "ew"
        return f"{material}_{shape}"

    def _terrain_tile(self, region: str, tx: int, ty: int) -> str:
        variation = (tx * 73 + ty * 151 + tx * ty * 7 + self.seed - DEFAULT_WORLD_SEED) % 17
        choices = {
            "city": ("city_pavement", "city_concrete", "city_plaza"),
            "jungle": ("jungle_ground", "jungle_leaves", "jungle_mud"),
            "desert": ("desert_sand", "desert_dunes", "desert_rock"),
            "snow": ("snow", "snow_drift", "snow_rock"),
            "rural": ("rural_grass", "rural_earth", "farm_field"),
            "beach": ("beach_sand", "wet_sand", "beach_sand"),
            "island": ("island_grass", "island_stone", "grass"),
            "sea": ("deep_sea", "shallow_sea", "deep_sea"),
        }
        return choices[region][1 if variation == 0 else 2 if variation == 1 else 0]

    @lru_cache(maxsize=96)
    def sector(self, sx: int, sy: int) -> tuple[Sprite, ...]:
        if not (0 <= sx < SECTORS and 0 <= sy < SECTORS):
            return ()
        if self.store is not None:
            saved = self.store.get(sx, sy)
            if saved is not None:
                try:
                    if not isinstance(saved, list):
                        raise ValueError("Sector is not a sprite list")
                    sprites = []
                    for row in saved:
                        if (not isinstance(row, list) or len(row) != 9
                                or not all(isinstance(value, str) for value in row[:2])
                                or not all(type(value) in (int, float) and math.isfinite(value)
                                           for value in row[2:])):
                            raise ValueError("Invalid sprite record")
                        sprites.append(Sprite(*row))
                    return tuple(sprites)
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid saved world sector {sx},{sy}") from None
        result = self._generate_sector(sx, sy)
        if self.store is not None:
            self.store.put(sx, sy, [
                [sprite.atlas, sprite.name, sprite.x, sprite.y, sprite.width,
                 sprite.height, sprite.rotation, sprite.solid_width, sprite.solid_height]
                for sprite in result
            ])
        return result

    def _edge_neighbors(self, sx: int, sy: int, lx: int, ly: int):
        """Yield (side, landmass) for sector edges that this local tile touches."""
        last = TILES_PER_SECTOR - 1
        for side, edge, dx, dy in (("north", ly == 0, 0, -1), ("south", ly == last, 0, 1),
                                   ("west", lx == 0, -1, 0), ("east", lx == last, 1, 0)):
            if edge:
                yield side, self._landmass(sx + dx, sy + dy)

    def _generate_sector(self, sx: int, sy: int) -> tuple[Sprite, ...]:
        region = self.region(sx, sy)
        ox, oy = sx * SECTOR_SIZE, sy * SECTOR_SIZE
        tx0, ty0 = sx * TILES_PER_SECTOR, sy * TILES_PER_SECTOR
        rng = random.Random(sx * 65537 + sy * 9973 + self.seed)
        ground: dict[tuple[int, int], str] = {}  # Local-tile terrain overrides.
        occupied: set[tuple[int, int]] = set()   # Local tiles kept clear of scatter.
        scenery: list[Sprite] = []

        def grid(lx: float, ly: float) -> tuple[float, float]:
            return ox + lx * TILE_SIZE, oy + ly * TILE_SIZE

        def prop(name: str, x: float, y: float, size: float = 72, rotation: float = 0.0,
                 solid: tuple[float, float] = (0.0, 0.0)):
            scenery.append(Sprite("prop-atlas", name, x, y, size, size, rotation, *solid))

        for dock in self.docks:
            # Keep palms and scrub off the sand leading onto a pier.
            tx, ty = dock.tiles[0]
            for i in range(1, 4):
                for side in (-1, 0, 1):
                    dx, dy = dock.direction
                    lx = tx - dx * i + (side if dy else 0) - tx0
                    ly = ty - dy * i + (side if dx else 0) - ty0
                    if 0 <= lx < TILES_PER_SECTOR and 0 <= ly < TILES_PER_SECTOR:
                        occupied.add((lx, ly))
        center = CENTERS.get((sx, sy))
        center_xy = None
        if center:
            lot = self._center_lot(sx, sy)
            center_xy = grid(*lot)
            self._center_plaza(center, lot, center_xy, ground, occupied, scenery, prop)
            if region != "island":
                tx, ty = self._center_lot_tile(sx, sy)
                ground[(tx, ty)] = "home_lot"
                occupied |= {(tx, ty), (tx, ty - 1), (tx, ty - 2)}   # Clear way out, north.

        if region == "city":
            home = (sx, sy) == HOME_SECTOR
            skip = {(2, 2)} if center or home else set()
            if center:
                skip.add((6, 2))                  # The center's parking lot goes here.
            shop = self.general_store
            if (sx, sy) == shop.sector:
                skip.add(shop.lot)
                scenery.append(Sprite("structure-atlas", "general_store", shop.x, shop.y,
                                      106, 106, shop.rotation, 82, 82))
            self._city_blocks(rng, grid, ground, scenery, prop, skip_lots=skip)
            if home:
                scenery.append(Sprite("structure-atlas", "player_house", *HOME_HOUSE, 106, 106,
                                      solid_width=82, solid_height=82))
                ground[HOME_LOT_TILE] = "home_lot"
            if center_xy:
                # Street poles from the road grid must not stand inside the plaza.
                half = 2 * TILE_SIZE
                scenery[:] = [item for item in scenery if not (
                    item.name in ("street_lamp", "traffic_light")
                    and abs(item.x - center_xy[0]) < half and abs(item.y - center_xy[1]) < half)]
        elif (sx, sy) in self.fair.sectors():
            from fair import exterior_ground
            bx, by = (sx - self.fair.sector[0]) * TILES_PER_SECTOR, (sy - self.fair.sector[1]) * TILES_PER_SECTOR
            occupied |= {(x, y) for x in range(TILES_PER_SECTOR) for y in range(TILES_PER_SECTOR)}
            for lx in range(TILES_PER_SECTOR):
                for ly in range(TILES_PER_SECTOR):
                    tile = exterior_ground(bx + lx, by + ly)
                    if tile:
                        ground[(lx, ly)] = tile
            scenery += self._fair_sprites.get((sx, sy), [])
        elif region == "rural" and (sx, sy) == self.farm.sector:
            self._farmhouse(grid, ground, occupied, scenery, prop)
        elif region == "rural" and not center and rng.random() < 0.5 and (sx, sy) not in self.roads.sectors():
            self._farmstead(rng, tx0, ty0, grid, ground, occupied, scenery, prop)
        elif region == "beach" and (sx, sy) in (self.mainland_dock, self.island_dock):
            # The pier is drawn pointing south; rotate it out toward the island channel.
            east = (sx, sy) == self.mainland_dock
            scenery.append(Sprite("structure-atlas", "beach_ferry_dock",
                                  ox + (SECTOR_SIZE - 44 if east else 44), oy + SECTOR_SIZE / 2,
                                  106, 106, 90.0 if east else -90.0, 70, 70))
            occupied |= {(lx, ly) for lx in ((6, 7) if east else (0, 1)) for ly in (3, 4)}
        elif (sx, sy) in self.camps:
            self._camp(region, grid, ground, occupied, scenery, prop)
        elif region == "sea":
            coastal = any(self._landmass(sx + dx, sy + dy) != "sea"
                          for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            if coastal and rng.random() < 0.35:
                bx, by = ox + rng.randrange(96, SECTOR_SIZE - 96), oy + rng.randrange(96, SECTOR_SIZE - 96)
                if not any(abs(bx - (t[0] + 0.5) * TILE_SIZE) < 96 and abs(by - (t[1] + 0.5) * TILE_SIZE) < 96
                           for t in self.pier_tiles):
                    prop("buoy", bx, by, 44)
            if sy == ISLAND_ROW and self.mainland_dock[0] < sx < self.island_dock[0]:
                for dy in (-96, 96):
                    prop("buoy", ox + SECTOR_SIZE / 2, oy + SECTOR_SIZE / 2 + dy, 44)

        if (sx, sy) in self.factory.sectors():
            from factory import exterior_ground
            if region != "sea":
                occupied |= {(x, y) for x in range(TILES_PER_SECTOR) for y in range(TILES_PER_SECTOR)}
            if (sx, sy) == self.factory.sector:
                for lx in range(TILES_PER_SECTOR):
                    for ly in range(TILES_PER_SECTOR):
                        tile = exterior_ground(self.factory, tx0 + lx, ty0 + ly)
                        if tile:
                            ground[(lx, ly)] = tile
            scenery += self._factory_sprites.get((sx, sy), [])
        if (sx, sy) in self._camp_sectors:
            from island import camp_ground
            occupied |= {(x, y) for x in range(TILES_PER_SECTOR) for y in range(TILES_PER_SECTOR)}
            for lx in range(TILES_PER_SECTOR):
                for ly in range(TILES_PER_SECTOR):
                    if camp_ground(lx, ly):
                        ground[(lx, ly)] = camp_ground(lx, ly)
        scenery += self._island_sprites.get((sx, sy), [])

        result: list[Sprite] = []
        for ly in range(TILES_PER_SECTOR):
            for lx in range(TILES_PER_SECTOR):
                tx, ty = tx0 + lx, ty0 + ly
                x, y = (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE
                tile = ground.get((lx, ly)) or self._terrain_tile(region, tx, ty)
                if region == "sea":
                    touching_land = [side for side, mass in self._edge_neighbors(sx, sy, lx, ly)
                                     if mass != "sea"]
                    if touching_land:
                        tile = "sea_foam"
                    elif any(self._landmass(sx + dx, sy + dy) != "sea"
                             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                        tile = "shallow_sea"
                elif region == "beach":
                    shore = next((side for side, mass in self._edge_neighbors(sx, sy, lx, ly)
                                  if mass == "sea"), None)
                    if shore:
                        tile = f"shore_{shore}"
                rotation = 0.0
                pier = self.pier_tiles.get((tx, ty))
                if pier:
                    dock, index = pier
                    # Planks run along the pier; the end tile's posts face the sea.
                    if index == len(dock.tiles) - 1:
                        tile, rotation = "pier_end", END_FACING[dock.direction]
                    else:
                        tile, rotation = "pier_planks", 0.0 if dock.direction[0] == 0 else 90.0
                result.append(Sprite("terrain-atlas", tile, x, y, TILE_SIZE, TILE_SIZE, rotation))
                road_style = self._road_style(tx, ty)
                if not road_style:
                    continue
                road = self._road_tile(tx, ty, road_style)
                rotation = 0.0
                if road in ("city_ns", "city_ew") and (lx, ly) in ((4, 3), (4, 5), (3, 4), (5, 4)):
                    rotation = 0.0 if road == "city_ns" else 90.0
                    road = "city_crosswalk"
                result.append(Sprite("road-atlas", road, x, y, TILE_SIZE, TILE_SIZE, rotation))
                if road_style == "ice" and (tx + ty) % 3 == 0 and not self.roads.near(x, y, 48):
                    if road == "ice_ns":
                        prop("snow_marker", x - 24, y, 40)
                        prop("snow_marker", x + 24, y, 40)
                    elif road == "ice_ew":
                        prop("snow_marker", x, y - 24, 40)
                        prop("snow_marker", x, y + 24, 40)

        choices = SCATTER.get(region)
        if choices:
            names, count = choices
            for _ in range(count):
                x = ox + rng.randrange(48, SECTOR_SIZE - 48)
                y = oy + rng.randrange(48, SECTOR_SIZE - 48)
                name = rng.choice(names)
                size = rng.choice((64, 72, 80))
                # Keep the plaza, its gates, and its barriers clear of scatter.
                if center_xy and math.hypot(x - center_xy[0], y - center_xy[1]) < 210:
                    continue
                if self.roads.near(x, y, 40):
                    continue   # Clear of the highway and its ramps.
                if (int((x - ox) // TILE_SIZE), int((y - oy) // TILE_SIZE)) in occupied:
                    continue
                if any(self._road_style(int((x + dx) // TILE_SIZE), int((y + dy) // TILE_SIZE))
                       for dx in (-28, 28) for dy in (-28, 28)):
                    continue
                solid = 40 if name.startswith("rock") else 30 if name.startswith("hay") else 18
                prop(name, x, y, size, solid=(solid, solid))
        result += self._highway_pieces.get((sx, sy), [])
        return tuple(result + scenery)

    def _center_lot_tile(self, sx: int, sy: int) -> tuple[int, int]:
        return CENTER_LOT_TILE["city" if self.region(sx, sy) == "city" else "other"]

    def center_parking(self, sx: int, sy: int) -> tuple[float, float, float]:
        """Where fast travel parks the car at this center: its lot's west stall, facing up."""
        tx, ty = self._center_lot_tile(sx, sy)
        return (sx * SECTOR_SIZE + tx * TILE_SIZE + 20,
                sy * SECTOR_SIZE + (ty + 1) * TILE_SIZE - STALL_Y, 0.0)

    def _center_lot(self, sx: int, sy: int) -> tuple[int, int]:
        # City centers take a building lot so they never sit on the road grid.
        return (2, 2) if self.region(sx, sy) == "city" else (4, 4)

    def center_position(self, sx: int, sy: int) -> tuple[float, float]:
        lx, ly = self._center_lot(sx, sy)
        return sx * SECTOR_SIZE + lx * TILE_SIZE, sy * SECTOR_SIZE + ly * TILE_SIZE

    def center_for(self, x: float, y: float):
        """Return (name, x, y) of the racing center serving this spot, or None at sea."""
        region = self.region_at(x, y)
        if region == "sea":
            return None
        candidates = [(name, *self.center_position(*sector)) for sector, name in CENTERS.items()]
        exact = [c for c in candidates if c[0] == f"center_{region}"]
        # Beaches have no center of their own; use the closest one.
        return exact[0] if exact else min(
            candidates, key=lambda c: math.hypot(c[1] - x, c[2] - y))

    def _center_plaza(self, center, lot, center_xy, ground, occupied, scenery, prop):
        """A large center on an asphalt plaza ringed by race gates, barriers, and cones."""
        lx, ly = lot
        for tx in range(lx - 2, lx + 2):
            for ty in range(ly - 2, ly + 2):
                ground[(tx, ty)] = "dark_asphalt"
                occupied.add((tx, ty))
        x, y = center_xy
        scenery.append(Sprite("structure-atlas", center, x, y, 160, 160,
                              solid_width=104, solid_height=104))
        edge = 2 * TILE_SIZE
        prop("race_start", x, y + edge - 8, 80)
        prop("race_finish", x, y - edge + 8, 80)
        for along in (-88, 88):
            prop("barrier", x + along, y - edge + 8, 56, solid=(48, 10))
            prop("barrier", x + along, y + edge - 8, 56, solid=(48, 10))
            prop("barrier", x - edge + 8, y + along, 56, 90.0, solid=(48, 10))
            prop("barrier", x + edge - 8, y + along, 56, 90.0, solid=(48, 10))
        for dx in (-edge + 12, edge - 12):
            for dy in (-edge + 12, edge - 12):
                prop("cone", x + dx, y + dy, 40)

    def _camp(self, region, grid, ground, occupied, scenery, prop):
        """Tents facing a campfire, supplies, and a bench on trampled ground."""
        tent, floor = CAMP_STYLE[region]
        for lx in range(2, 6):
            for ly in range(2, 6):
                ground[(lx, ly)] = floor
                occupied.add((lx, ly))
        cx, cy = grid(4, 4)
        for dx, dy in CAMP_TENTS:
            scenery.append(Sprite("camp-atlas", tent, cx + dx, cy + dy, 72, 72,
                                  self.tent_facing(dx, dy), 54, 50))
        for name, dx, dy, size, rotation, solid in (
            ("campfire", 0, 0, 56, 0.0, 20),
            ("crate_stack", 92, 38, 60, 0.0, 40),
            ("barrel_pair", -96, 40, 56, 0.0, 34),
            ("bedroll_red", -44, 30, 44, 90.0, 0),
            ("bedroll_blue", 44, 34, 44, -90.0, 0),
            ("log_bench", 0, -44, 56, 0.0, 0),
        ):
            scenery.append(Sprite("camp-atlas", name, cx + dx, cy + dy, size, size, rotation,
                                  solid, solid))

    def _city_blocks(self, rng, grid, ground, scenery, prop, skip_lots=()):
        for lot in ((2, 2), (6, 2), (2, 6), (6, 6)):
            if lot in skip_lots:
                continue
            if rng.random() < 0.2:
                # Parking lot: 2 x 2 tiles whose painted stalls sit in each tile's top half.
                for lx in (lot[0] - 1, lot[0]):
                    for ly in (lot[1] - 1, lot[1]):
                        ground[(lx, ly)] = "parking_lot"
                        for stall_x in (20, 44):
                            if rng.random() < 0.55:
                                x, y = grid(lx, ly)
                                scenery.append(Sprite(
                                    "vehicle-atlas", rng.choice(PARKED_CARS), x + stall_x, y + STALL_Y,
                                    PARKED_SIZE, PARKED_SIZE, rng.choice((0.0, 180.0)), *PARKED_SOLID))
            else:
                scenery.append(Sprite("structure-atlas", rng.choice(CITY_BUILDINGS), *grid(*lot),
                                      106, 106, solid_width=82, solid_height=82))
        # Every city sector's road grid crosses at local tile (4, 4). Poles are
        # non-solid so cornering at intersections never snags on them.
        cx, cy = grid(4.5, 4.5)
        for dx, dy, name in ((-42, -42, "street_lamp"), (42, 42, "street_lamp"),
                             (42, -42, "traffic_light"), (-42, 42, "traffic_light")):
            prop(name, cx + dx, cy + dy, 52)
        prop("street_lamp", cx + 42, grid(0, 0.5)[1], 52)
        prop("street_lamp", grid(0.5, 0)[0], cy - 42, 52)

    def _farmhouse(self, grid, ground, occupied, scenery, prop):
        """The player's farm: the house (drawn abandoned over this until it's theirs),
        a gravel yard, and the empty mud plot."""
        occupied |= {(x, y) for x in range(TILES_PER_SECTOR) for y in range(TILES_PER_SECTOR)}
        for tile in FARM_YARD:
            ground[tile] = "dirt_gravel"
        x0, y0, w, h = FARM_PLOT
        for i in range(w):
            for j in range(h):
                ground[(x0 + i, y0 + j)] = "farm_mud"
        scenery.append(Sprite("structure-atlas", "farmhouse", *grid(*FARM_HOUSE_TILE),
                              FARM_HOUSE_SIZE, FARM_HOUSE_SIZE, 0.0, *FARM_HOUSE_SOLID))
        for x, y, name in ((3.6, 0.6, "hay_a"), (4.5, 0.75, "hay_b"), (7.4, 0.6, "hay_a")):
            prop(name, *grid(x, y), 52, solid=(30, 30))

    def _farmstead(self, rng, tx0, ty0, grid, ground, occupied, scenery, prop):
        """Barn and gravel yard, a fenced plowed field, and hay bales, clear of roads."""
        for _ in range(6):
            fx, fy = rng.randrange(0, 4), rng.randrange(0, 6)
            footprint = {(fx + i, fy + j) for i in range(5) for j in range(3)}
            if any(self._road_style(tx0 + lx, ty0 + ly) for lx, ly in footprint):
                continue
            occupied |= footprint
            for i in (0, 1):
                for j in (0, 1, 2):
                    ground[(fx + i, fy + j)] = "dirt_gravel"
            for i in (2, 3, 4):
                for j in (0, 1):
                    ground[(fx + i, fy + j)] = "farm_rows"
            scenery.append(Sprite("structure-atlas", "rural_barn", *grid(fx + 1, fy + 1),
                                  106, 106, solid_width=82, solid_height=82))
            for i in (2, 3, 4):
                x = grid(fx + i + 0.5, 0)[0]
                prop("farm_fence", x, grid(0, fy)[1] + 6, 64, solid=(56, 6))
                prop("farm_fence", x, grid(0, fy + 2)[1] - 6, 64, solid=(56, 6))
            for j in (0, 1):
                prop("farm_fence", grid(fx + 5, 0)[0] - 6, grid(0, fy + j + 0.5)[1], 64, 90.0,
                     solid=(56, 6))
            for i, name in ((0, "hay_a"), (1, "hay_b")):
                prop(name, *grid(fx + i + 0.5, fy + 2.5), 52, solid=(30, 30))
            return

    def visible_sprites(self, camera_x: float, camera_y: float,
                        width: int, height: int) -> list[Sprite]:
        # Sprites overhang their sector by at most half a 160 px center building
        # (plus a small margin), so only sectors touching the padded view matter.
        pad = GATHER_MARGIN
        left = max(0, int((camera_x - pad) // SECTOR_SIZE))
        top = max(0, int((camera_y - pad) // SECTOR_SIZE))
        right = min(SECTORS - 1, int((camera_x + width + pad) // SECTOR_SIZE))
        bottom = min(SECTORS - 1, int((camera_y + height + pad) // SECTOR_SIZE))
        return [sprite for sy in range(top, bottom + 1)
                for sx in range(left, right + 1)
                for sprite in self.sector(sx, sy)]

    def nearby_obstacles(self, x: float, y: float) -> list[Sprite]:
        sx, sy = int(x // SECTOR_SIZE), int(y // SECTOR_SIZE)
        return [sprite for row in range(max(0, sy - 1), min(SECTORS, sy + 2))
                for col in range(max(0, sx - 1), min(SECTORS, sx + 2))
                for sprite in self.sector(col, row)
                if sprite.solid_width and abs(sprite.x - x) < 145
                and abs(sprite.y - y) < 145]
