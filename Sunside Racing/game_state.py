"""Entity records and GPU batches for the streamed racing world."""

from __future__ import annotations

import json
from pathlib import Path

import moderngl

from gl_utils import (
    build_tex_objs,
    get_new_instances,
    load_program,
    load_texture,
    modify_rot,
    modify_size,
    modify_texture,
    modify_xy,
    set_viewport_size,
    to_gl,
)


# Race-level track tiles sit with roads; camp gear with props; people above both;
# beach umbrella canopies over the people lying under them, still below vehicles;
# floating mission badges over everything.
DRAW_ORDER = (
    "terrain-atlas", "road-atlas", "highway-atlas", "track-atlas", "structure-atlas", "prop-atlas",
    "camp-atlas", "home-atlas", "store-atlas", "farm-atlas", "fair-atlas", "factory-atlas", "people-atlas", "canopy-atlas", "vehicle-atlas", "marker-atlas",
)
CAPACITY = {
    "terrain-atlas": 8192,
    "road-atlas": 4096,
    "structure-atlas": 1024,
    "prop-atlas": 1024,
    "track-atlas": 4096,
    "camp-atlas": 256,
    "people-atlas": 512,
    "canopy-atlas": 128,
    "home-atlas": 64,
    "highway-atlas": 512,
    "store-atlas": 64,
    "farm-atlas": 96,
    "fair-atlas": 320,
    "factory-atlas": 64,
    "vehicle-atlas": 512,
    "marker-atlas": 128,
}


class GameState:
    """Keeps mutable entity records and streams visible textured quads to GL."""

    def __init__(self, ctx, project_root: Path, toolkit_root: Path,
                 viewport: tuple[int, int]):
        self.ctx = ctx
        self.project_root = project_root
        manifest = json.loads((project_root / "assets" / "atlas-manifest.json").read_text())
        self.atlases = manifest["atlases"]
        self.program = load_program(
            ctx, str(toolkit_root / "shaders" / "tex.vert"),
            str(toolkit_root / "shaders" / "tex.frag"),
        )
        self.program["u_texture"].value = 0
        self.data: dict[str, list[list[float]]] = {name: [] for name in DRAW_ORDER}
        self.instances = {}
        self.vao = {}
        self.vbo = {}
        self.textures = {}
        self.entities: dict[int, dict] = {}
        self.next_entity_id = 1
        for name in DRAW_ORDER:
            self.instances[name] = get_new_instances(0, 0, CAPACITY[name])[2]
            self.vao[name], self.vbo[name] = build_tex_objs(ctx, self.program, self.instances[name])
            self.textures[name] = load_texture(ctx, str(project_root / "assets" / self.atlases[name]["png"]))
        self.resize(*viewport)

    def resize(self, width: int, height: int):
        width, height = set_viewport_size(width, height)
        self.ctx.viewport = (0, 0, width, height)
        self.program["u_viewport_size"].value = (float(width), float(height))
        self.viewport = (width, height)

    def _tile(self, atlas: str, sprite: str) -> list[int]:
        return self.atlases[atlas]["sprites"][sprite]["tile"]

    def spawn_player(self, x: float, y: float) -> int:
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        tile_x, tile_y = self._tile("vehicle-atlas", "racer_player")
        self.entities[entity_id] = {
            "atlas": "vehicle-atlas",
            "record": [x, y, 255, 255, 255, 255, 0, 64, 64, 0, tile_x, tile_y],
        }
        return entity_id

    def spawn_walker(self, x: float, y: float) -> int:
        """The player on foot: a 32 px people-atlas sprite drawn at native size."""
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        tile_x, tile_y = self._tile("people-atlas", "player_idle")
        self.entities[entity_id] = {
            "atlas": "people-atlas",
            "record": [x, y, 255, 255, 255, 255, 0, 32, 32, 0, tile_x, tile_y],
        }
        return entity_id

    def set_sprite(self, entity_id: int, atlas: str, sprite: str):
        """Swap an entity's art (e.g. the player's car becoming the F1 car)."""
        entity = self.entities[entity_id]
        entity["atlas"] = atlas
        modify_texture(0, [entity["record"]], *self._tile(atlas, sprite))

    def set_frame(self, entity_id: int, sprite: str):
        entity = self.entities[entity_id]
        modify_texture(0, [entity["record"]], *self._tile(entity["atlas"], sprite))

    def set_player_pose(self, entity_id: int, x: float, y: float, heading: float):
        record = self.entities[entity_id]["record"]
        modify_xy(0, [record], x, y)
        # World heading is clockwise from north; GL rotation is counterclockwise.
        modify_rot(0, [record], -heading, "tex")

    def player_record(self, entity_id: int) -> list[float]:
        return self.entities[entity_id]["record"]

    def _screen_record(self, atlas: str, sprite: str, x: float, y: float,
                       width: float, height: float, rotation: float,
                       camera_x: float, camera_y: float, zoom: float = 1.0) -> list[float]:
        tile_x, tile_y = self._tile(atlas, sprite)
        return [(x - camera_x) * zoom, (y - camera_y) * zoom, 255, 255, 255, 255, 0,
                width * zoom, height * zoom, rotation, tile_x, tile_y]

    def render(self, world_sprites, camera_x: float, camera_y: float,
               entity_ids, zoom: float = 1.0):
        """Draw world sprites and the given entities; zoom scales world px to screen px."""
        view_w, view_h = self.viewport[0] / zoom, self.viewport[1] / zoom
        for records in self.data.values():
            records.clear()
        for sprite in world_sprites:
            sx, sy = sprite.x - camera_x, sprite.y - camera_y
            if not (-sprite.width <= sx <= view_w + sprite.width
                    and -sprite.height <= sy <= view_h + sprite.height):
                continue
            self.data[sprite.atlas].append(self._screen_record(
                sprite.atlas, sprite.name, sprite.x, sprite.y,
                sprite.width, sprite.height, sprite.rotation,
                camera_x, camera_y, zoom,
            ))
        for entity_id in entity_ids:
            entity = self.entities[entity_id]
            record = entity["record"].copy()
            modify_xy(0, [record], (record[0] - camera_x) * zoom, (record[1] - camera_y) * zoom)
            modify_size(0, [record], record[7] * zoom, record[8] * zoom, "tex")
            self.data[entity["atlas"]].append(record)

        for name in DRAW_ORDER:
            records = self.data[name]
            count = len(records)
            if not count:
                continue
            if count > CAPACITY[name]:
                raise RuntimeError(f"Visible {name} instances exceed capacity {CAPACITY[name]}")
            # All camera-relative records are mutated, packed, then streamed.
            _, self.instances[name] = to_gl(records, self.instances[name], "tex")
            self.vbo[name].write(self.instances[name][:count].tobytes(), offset=0)
            self.textures[name].use(location=0)
            columns, rows = self.atlases[name]["atlas_grid"]
            self.program["u_atlas_grid"].value = (float(columns), float(rows))
            self.vao[name].render(moderngl.TRIANGLES, instances=count)
