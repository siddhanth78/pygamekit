"""Versioned, atomic disk cache for generated world sectors."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


SAVE_VERSION = 1
DEFAULT_WORLD_SEED = 2026
# Bump when world.py places scenery differently so cached sectors regenerate.
GENERATOR_VERSION = 14  # 5: piers. 6: house. 7-8: its lot. 9: center lots. 10: store. 11-13: highway. 14: farmhouse.


class WorldStore:
    def __init__(self, path: Path | None = None, default_seed: int = DEFAULT_WORLD_SEED):
        self.path = Path(path) if path is not None else Path(__file__).resolve().parent / "world.json"
        if type(default_seed) is not int:
            raise ValueError("World seed must be an integer")
        self.seed = default_seed
        self.sectors: dict[str, list] = {}
        self.dirty = not self.path.exists()
        self._writer: threading.Thread | None = None
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Cannot read world save {self.path}: {exc}") from exc
            if (not isinstance(data, dict) or type(data.get("version")) is not int
                    or data["version"] != SAVE_VERSION or type(data.get("seed")) is not int
                    or not isinstance(data.get("sectors"), dict)):
                raise ValueError(f"Invalid or unsupported world save: {self.path}")
            self.seed = data["seed"]
            if data.get("generator") == GENERATOR_VERSION:
                self.sectors = data["sectors"]
            else:
                # Stale scenery from an older generator; keep the seed, regenerate lazily.
                self.dirty = True

    @staticmethod
    def key(sx: int, sy: int) -> str:
        return f"{sx},{sy}"

    def get(self, sx: int, sy: int):
        return self.sectors.get(self.key(sx, sy))

    def put(self, sx: int, sy: int, sprites: list):
        self.sectors[self.key(sx, sy)] = sprites
        self.dirty = True

    def save(self, background: bool = False):
        """Write world.json atomically if anything changed.

        background=True snapshots the sector table here and does the encoding and
        disk write on a worker thread, so autosaves do not stall the frame. Only one
        write runs at a time; a request while one is running is skipped (the next
        autosave or the exit save picks the changes up).
        """
        if self._writer is not None and self._writer.is_alive():
            if not background:
                self._writer.join()  # Exit save: let the running write finish first.
            else:
                return False
        if not self.dirty:
            return False
        # Sector lists are never mutated after put(), so a shallow copy is a safe snapshot.
        payload = {"version": SAVE_VERSION, "generator": GENERATOR_VERSION,
                   "seed": self.seed, "sectors": dict(self.sectors)}
        self.dirty = False
        if not background:
            self._write(payload)
            return True
        self._writer = threading.Thread(target=self._write, args=(payload,), daemon=True)
        self._writer.start()
        return True

    def wait(self):
        """Block until a background write finishes."""
        if self._writer is not None:
            self._writer.join()

    def _write(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".world-", suffix=".tmp", delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(text)  # One write; streaming json.dump is much slower.
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            self.dirty = True  # Nothing was saved; try again next time.
            raise
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
