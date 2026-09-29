"""Export the fixed world for the Godot port (godot-plans/2-world.txt).

The Godot game uses today's world as a permanent, hand-editable map instead of
porting the generator. This writes that world out as JSON: every sector's ground,
roads, and sprites exactly as the game draws them, the regions, the highway, every
fixed site, traffic routes, parked commuters, pedestrian groups, mission givers,
farm-buyer spots, the 55 racing-center and island race tracks, and a set of sample
points with the answers the world gives there (for Godot to test against).

The world is the one the player has: world.json's seed and cached sectors, with the
rest generated from that seed exactly as the game would. world.json is only read
(through a temporary copy), never written.

    python3 export_world.py                  # -> ~/Desktop/sunside-racing/export
    python3 export_world.py --out some/dir   # anywhere else

See the README.md written into the output folder for the format.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import random
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import fishing
import highway
import island
import pedestrians as pedestrians_module
import world as world_module
from car import Car
import missions as missions_module
from missions import Missions
from parking import Parking
from pedestrians import Pedestrians
from progression import REGIONS
from racers import LAPS, RIVAL_RATINGS, track_size
from track_gen import TRACK_GENERATOR_VERSION, generate
from traffic import Traffic
from world import (CENTERS, HOME_DOOR, HOME_HOUSE, HOME_LOT_TILE, HOME_PARK, HOME_SECTOR, PIER_HALF,
                   SECTOR_SIZE, SECTORS, TILE_SIZE, TILES_PER_SECTOR, World)
from world_save import GENERATOR_VERSION, WorldStore

EXPORT_VERSION = 1
DEFAULT_OUT = PROJECT_ROOT.parent.parent / "sunside-racing" / "export"
DIGITS = 2                       # Decimal places kept for coordinates.
REGION_CODES = {"sea": "s", "beach": "b", "city": "c", "jungle": "j", "desert": "d",
                "snow": "n", "rural": "r", "island": "i"}
BUYER_SPOTS = 32                 # Candidate farm-buyer spots exported per region.
SAMPLES = {"uniform": 6000, "highway": 1200, "piers": 400}


# JSON helpers ----------------------------------------------------------------------

def jsonable(value):
    """Plain JSON data: tuples and sets become lists, floats are rounded, dataclasses
    become dicts."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        rounded = round(value, DIGITS)
        return int(rounded) if rounded.is_integer() else rounded
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else json.dumps(jsonable(k))): jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    raise TypeError(f"Can't export {type(value).__name__}")


def describe(obj) -> dict:
    """A site object's fields plus every no-argument property that gives plain data."""
    out: dict = dict(jsonable(obj)) if dataclasses.is_dataclass(obj) else {}
    for name in dir(type(obj)):
        if name.startswith("_") or not isinstance(getattr(type(obj), name), property):
            continue
        try:
            out[name] = jsonable(getattr(obj, name))
        except (TypeError, ValueError, AttributeError):
            pass
    return out


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n")


# The world --------------------------------------------------------------------------

def stale_cached(world: World, cached: set) -> int:
    """Cached sectors that differ from what today's generator makes (the export keeps the
    cached ones: that's the world the player has seen)."""
    fresh = World(seed=world.seed)
    return sum(1 for sector in cached if world.sector(*sector) != fresh.sector(*sector))


def load_world(world_json: Path) -> tuple[World, set]:
    """The player's world: world.json's seed and cached sectors (read from a temporary
    copy so nothing is ever written back), the rest generated like the game does."""
    if not world_json.exists():
        return World(), set()
    temp = Path(tempfile.mkdtemp()) / "world.json"
    shutil.copy(world_json, temp)
    store = WorldStore(temp)
    cached = {(sx, sy) for sx in range(SECTORS) for sy in range(SECTORS) if store.get(sx, sy) is not None}
    return World(store=store), cached


def split_sector(world: World, sx: int, sy: int) -> dict:
    """A sector as ground tiles (8 x 8), road tiles, and the other sprites, in draw order."""
    ox, oy = sx * SECTOR_SIZE, sy * SECTOR_SIZE
    ground = [None] * (TILES_PER_SECTOR * TILES_PER_SECTOR)
    roads, sprites = [], []
    for s in world.sector(sx, sy):
        lx, ly = (s.x - ox) / TILE_SIZE - 0.5, (s.y - oy) / TILE_SIZE - 0.5
        on_tile = (s.width == s.height == TILE_SIZE and lx.is_integer() and ly.is_integer()
                   and 0 <= lx < TILES_PER_SECTOR and 0 <= ly < TILES_PER_SECTOR
                   and not s.solid_width and not s.solid_height)
        index = int(ly) * TILES_PER_SECTOR + int(lx) if on_tile else None
        if on_tile and s.atlas == "terrain-atlas" and ground[index] is None:
            ground[index] = [s.name, jsonable(s.rotation)]
        elif on_tile and s.atlas == "road-atlas":
            roads.append([int(lx), int(ly), s.name, jsonable(s.rotation)])
        else:
            sprites.append([s.atlas, s.name, *jsonable([s.x, s.y, s.width, s.height, s.rotation,
                                                         s.solid_width, s.solid_height])])
    if any(tile is None for tile in ground):
        raise ValueError(f"Sector {sx},{sy} is missing ground tiles")
    return {"sector": [sx, sy], "region": world.region(sx, sy), "ground": ground,
            "roads": roads, "sprites": sprites}


def rebuild_sector(data: dict) -> list[tuple]:
    """The sprite tuples a split sector stands for (tests compare with world.sector)."""
    sx, sy = data["sector"]
    ox, oy = sx * SECTOR_SIZE, sy * SECTOR_SIZE
    out = []
    for i, (name, rotation) in enumerate(data["ground"]):
        lx, ly = i % TILES_PER_SECTOR, i // TILES_PER_SECTOR
        out.append(("terrain-atlas", name, ox + (lx + 0.5) * TILE_SIZE, oy + (ly + 0.5) * TILE_SIZE,
                    TILE_SIZE, TILE_SIZE, rotation, 0, 0))
    for lx, ly, name, rotation in data["roads"]:
        out.append(("road-atlas", name, ox + (lx + 0.5) * TILE_SIZE, oy + (ly + 0.5) * TILE_SIZE,
                    TILE_SIZE, TILE_SIZE, rotation, 0, 0))
    out += [tuple(row) for row in data["sprites"]]
    return out


def regions(world: World) -> dict:
    rows = ["".join(REGION_CODES[world.region(sx, sy)] for sx in range(SECTORS)) for sy in range(SECTORS)]
    return {"size": SECTORS, "codes": {v: k for k, v in REGION_CODES.items()}, "rows": rows}


def highway_data(world: World) -> dict:
    return {"half_width": highway.HALF, "lane": highway.LANE, "asphalt": list(highway.ASPHALT),
            "roads": [{"kind": road.kind, "closed": road.closed, "points": jsonable(road.points)}
                      for road in world.highway_roads]}


def sites(world: World) -> dict:
    mainland, isle = island.ferry_spots(world)
    return {
        "home": {"sector": HOME_SECTOR, "house": HOME_HOUSE, "door": HOME_DOOR, "park": HOME_PARK,
                 "lot_tile": HOME_LOT_TILE},
        "general_store": describe(world.general_store),
        "farm": describe(world.farm),
        "fair": describe(world.fair),
        "factory": describe(world.factory),
        "piers": {"half_width": PIER_HALF, "docks": [describe(d) for d in world.docks]},
        "ferry": {"mainland_dock": world.mainland_dock, "island_dock": world.island_dock,
                  "mainland_spot": mainland, "island_spot": isle},
        "centers": [{"sector": sector, "building": name, "position": world.center_position(*sector),
                     "parking": world.center_parking(*sector)} for sector, name in sorted(CENTERS.items())],
        "camps": [{"sector": sector, "region": region, "center": world.camp_center(*sector)}
                  for sector, region in sorted(world.camps.items())],
        "fish_traders": fishing.trader_spots(world),
        "island_camps": {club: describe(camp) for club, camp in sorted(world.island_camps.items())},
    }


def traffic_data(world: World) -> dict:
    traffic = Traffic(world, world.seed)
    cars = [{"name": car.name, "path": car.path, "speed": car.speed, "size": car.size, "solid": car.solid,
             "segment": car.segment, "progress": car.progress, "reverse_segments": car.reverse_segments,
             "trip": car.trip_length is not None} for car in traffic.cars]
    parking = Parking(world, traffic, world.seed)
    commuters = []
    for sy in range(SECTORS):
        for sx in range(SECTORS):
            for sprite in parking._commuters(sx, sy):
                commuters.append({"stall": [sprite.x, sprite.y], "name": sprite.name,
                                  "trip": parking.trip_path(sprite)})
    return jsonable({"cars": cars, "commuters": commuters})


def pedestrian_data(world: World) -> dict:
    """Every pedestrian group the game makes near the player, made up front for the whole map."""
    people = Pedestrians(world, world.seed)
    (cx_lo, cx_hi), (cy_lo, cy_hi) = world_module.CITY_SECTORS_X, world_module.CITY_SECTORS_Y
    groups = []
    for sy in range(SECTORS):
        for sx in range(SECTORS):
            candidates = []
            if cx_lo <= sx < cx_hi and cy_lo <= sy < cy_hi:
                candidates.append(("block", people._city_block))
            if (sx, sy) in CENTERS:
                candidates.append(("plaza", people._plaza))
            if (sx, sy) in world.camps:
                candidates.append(("camp", people._camp))
            elif world.region(sx, sy) == "rural":
                candidates.append(("farm", people._farm))
            elif world.region(sx, sy) == "beach":
                candidates.append(("beach", people._beach))
            for kind, make in candidates:
                key = (kind, sx, sy)
                members = make(sx, sy, people._group_rng(key))
                props = people.props.get(key, [])
                if members or props:
                    groups.append({"kind": kind, "sector": [sx, sy], "people": [_person(p) for p in members],
                                   "props": [[s.atlas, s.name, s.x, s.y, s.width, s.height, s.rotation,
                                              s.solid_width, s.solid_height] for s in props]})
    return jsonable({"groups": groups, "road_walkers": [_person(p) for p in people.walkers],
                     "scan_sectors": pedestrians_module.SCAN_SECTORS,
                     "keep_sectors": pedestrians_module.KEEP_SECTORS})


def _person(p) -> dict:
    return {"kind": p.kind, "path": p.path, "speed": p.speed, "stops": p.stops, "heading": p.heading,
            "pose": p.pose, "segment": p.segment, "progress": p.progress, "x": p.x, "y": p.y}


def people_spots(world: World) -> dict:
    missions = Missions(world, world.seed, None)
    buyers = {}
    for region in REGIONS:
        spots = []
        for i in range(BUYER_SPOTS * 3):
            spot = missions._buyer_spot(region, random.Random(f"export-buyer-{region}-{i}"))
            if spot and spot not in spots:
                spots.append(spot)
            if len(spots) == BUYER_SPOTS:
                break
        buyers[region] = spots
    givers = [{"id": g.id, "region": g.region, "type": g.type, "harder": g.harder, "x": g.x, "y": g.y}
              for g in missions.givers]
    return jsonable({"givers": givers, "giver_kinds": missions_module.GIVER_KINDS,
                     "buyer_spots": buyers})


def tracks(world: World) -> dict:
    """Racing-center and island race layouts: corner tiles of each circuit, as the game builds them."""
    out = {}
    for region in (*REGIONS, "island"):
        count = island.ISLAND_RACES if region == "island" else len(RIVAL_RATINGS)
        for race in range(1, count + 1):
            seed = f"{world.seed}-{region}-{race}"
            out[f"center_{region}_{race}"] = {
                "region": region, "race": race, "seed": seed, "size": track_size(race),
                "laps": island.ISLAND_LAPS if region == "island" else LAPS,
                "rival_rating": (island.ISLAND_RATINGS if region == "island" else RIVAL_RATINGS)[race - 1],
                "corners": jsonable(generate(seed, track_size(race)))}
    return out


def samples(world: World) -> dict:
    """Points with the answers the world gives there, for Godot to check itself against."""
    rng = random.Random(f"export-samples-{world.seed}")
    points = [(rng.uniform(0, world_module.WORLD_SIZE), rng.uniform(0, world_module.WORLD_SIZE))
              for _ in range(SAMPLES["uniform"])]
    road_points = [p for road in world.highway_roads for p in road.points]
    for _ in range(SAMPLES["highway"]):
        x, y = rng.choice(road_points)
        points.append((x + rng.uniform(-80, 80), y + rng.uniform(-80, 80)))
    for _ in range(SAMPLES["piers"]):
        dock = rng.choice(world.docks)
        tx, ty = rng.choice(dock.tiles)
        points.append(((tx + rng.random()) * TILE_SIZE, (ty + rng.random()) * TILE_SIZE))
    rows = []
    for x, y in points:
        heading = rng.choice((0.0, 45.0, 90.0, 135.0))
        dock = world.dock_at(x, y)
        rows.append({
            "x": x, "y": y, "region": world.region_at(x, y), "surface": world.surface_at(x, y),
            "ice": world.is_ice(x, y), "highway": world.on_highway(x, y), "road": world.roads.road_at(x, y),
            "drivable": world.is_drivable(x, y), "pier": dock.name if dock else None,
            "walker_fits": world.can_place_walker([x, y, 0, 0, 0, 0, 0, 12, 12, 0]),
            "car_heading": heading,
            "car_fits": world.can_place_car(Car(x=x, y=y, heading=heading).collision_record())})
    return jsonable({"walker_size": 12, "car_size": [24, 44], "points": rows})


# Output -----------------------------------------------------------------------------

README = """# Sunside Racing world export (for the Godot import)

Made by `export_world.py` in the Python project. The world is fixed: this is the map.

## Conventions
- World pixels. Tiles 64 px, sectors 8 x 8 tiles (512 px), the world 64 x 64 sectors.
- Sprite rows: [atlas, name, x, y, width, height, rotation, solid_width, solid_height].
  x, y is the center. Art faces north at rotation 0.
- ROTATION: Python's rotation turns COUNTERCLOCKWISE on screen, for the art and for
  the solid box alike. In Godot use `rotation_degrees = -rotation`.
- A sprite with solid_width and solid_height > 0 is an obstacle: a box of that size
  at (x, y) with the same rotation. It blocks cars and people alike.
- Draw order: atlases back to front as in 0-foundation (terrain ... marker); inside
  one atlas, the order the rows appear (ground, then roads, then sprites).
- Game-logic headings (the car, people, stops) are degrees clockwise from north.

## Files
- `world/sectors/sector_<sx>_<sy>.json`: {sector, region, ground: 64 x [name,
  rotation] row by row (terrain-atlas, 64 px), roads: [lx, ly, name, rotation]
  (road-atlas, 64 px), sprites: [...] every other sprite}.
- `world/regions.json`: 64 rows of 64 region codes (s sea, b beach, c city, j jungle,
  d desert, n snow, r rural, i island), row = sector y.
- `world/highway.json`: highway and ramp centerlines (points 64 px apart), half
  drivable widths by kind, the traffic lane offset.
- `world/sites.json`: home, General Store, farm, Snow Fair, Mining Factory, piers,
  ferry, racing centers, desert/jungle camps, fish traders, island club camps.
- `world/traffic.json`: every traffic car (sprite, path, speed, size, solid box, start
  segment and progress) and every parked commuter (its stall and its trip path).
- `world/pedestrians.json`: every pedestrian group (city blocks, plazas, camps, farms,
  beaches) with its people and beach gear, plus the always-on road walkers.
- `world/people.json`: mission givers (id, region, type, veteran, position) and
  candidate farm-buyer spots per region.
- `tracks/center_<region>_<race>.json`: racing-center (10 per region) and island (5)
  race layouts: the circuit's corner tiles (clockwise, starting on the longest
  eastbound straight), laps, rival rating.
- `samples.json`: test points with region, surface, ice, highway, road, drivable,
  pier, walker_fits (12 px box) and car_fits (24 x 44 box at car_heading).
- `export_manifest.json`: versions, counts, and a sha256 per file.

## Rules to rebuild in Godot (checked by samples.json)
- region_at: the region of the sector under the point (outside the world: sea).
- drivable: region is not sea. Walkable: drivable, or on a pier's planks (a pier is
  its tile run, `half_width` px either side of its center line).
- road_at: "hwy" / "ramp" within that kind's half width of its centerline, else none.
- highway (on_highway): road_at is hwy or ramp. surface: "city" on the highway, else
  the region.
- ice: never on the highway; otherwise an ice_* road tile in the snow region.
- A car or walker fits where the center and all four corners of its box are
  drivable (walkable for people).
"""


def export(out: Path, world_json: Path | None = None, sectors=None) -> dict:
    """Write the export into out (replacing its world/, tracks/, and top-level files).
    sectors limits which sectors are written (tests); the default is all of them."""
    world, cached = load_world(world_json if world_json is not None else PROJECT_ROOT / "world.json")
    for part in ("world", "tracks"):
        shutil.rmtree(out / part, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gdignore").write_text("")       # The Godot editor leaves raw export data alone.
    wanted = sorted(sectors) if sectors is not None else [(sx, sy) for sy in range(SECTORS) for sx in range(SECTORS)]
    counts = {"sectors": 0, "sprites": 0, "road_tiles": 0}
    for sx, sy in wanted:
        data = split_sector(world, sx, sy)
        write_json(out / "world" / "sectors" / f"sector_{sx}_{sy}.json", data)
        counts["sectors"] += 1
        counts["sprites"] += len(data["sprites"])
        counts["road_tiles"] += len(data["roads"])
    write_json(out / "world" / "regions.json", regions(world))
    write_json(out / "world" / "highway.json", highway_data(world))
    write_json(out / "world" / "sites.json", jsonable(sites(world)))
    traffic = traffic_data(world)
    write_json(out / "world" / "traffic.json", traffic)
    people = pedestrian_data(world)
    write_json(out / "world" / "pedestrians.json", people)
    spots = people_spots(world)
    write_json(out / "world" / "people.json", spots)
    for name, track in tracks(world).items():
        write_json(out / "tracks" / f"{name}.json", track)
    write_json(out / "samples.json", samples(world))
    (out / "README.md").write_text(README)
    counts.update(traffic_cars=len(traffic["cars"]), commuters=len(traffic["commuters"]),
                  pedestrian_groups=len(people["groups"]), givers=len(spots["givers"]))
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "export_manifest.json")
    manifest = {
        "export_version": EXPORT_VERSION, "seed": world.seed, "world_generator_version": GENERATOR_VERSION,
        "track_generator_version": TRACK_GENERATOR_VERSION, "cached_sectors": len(cached),
        "cached_sectors_unlike_generator": stale_cached(world, cached),
        "counts": counts,
        "files": {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }
    write_json(out / "export_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    args = parser.parse_args()
    manifest = export(args.out.expanduser())
    c = manifest["counts"]
    print(f"Exported seed {manifest['seed']} to {args.out}: {c['sectors']} sectors, {c['sprites']:,} sprites, "
          f"{c['road_tiles']:,} road tiles, {c['traffic_cars']} traffic cars, {c['commuters']} commuters, "
          f"{c['pedestrian_groups']} pedestrian groups, {c['givers']} givers, "
          f"{len(manifest['files'])} files ({manifest['cached_sectors']} sectors from world.json).")


if __name__ == "__main__":
    main()
