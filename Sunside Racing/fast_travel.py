"""Fast travel: jump (in the car) to a region's racing center, unlocked at level 3.

The car parks in the center's own two-stall lot (world.center_parking), facing up, like
at home. (The beach goes to a chosen fishing pier instead: fishing.beach_destination.)
"""

from __future__ import annotations

from collision_manager import nearest_clear_spot
from world import CENTERS, SECTOR_SIZE


def center_sector(region: str):
    return next(s for s, name in CENTERS.items() if name == f"center_{region}")


def region_anchor(world, region: str):
    """The region's racing center."""
    return world.center_position(*center_sector(region))


def destination(world, collisions, car, region: str):
    """The region's racing-center lot: (x, y, heading)."""
    return world.center_parking(*center_sector(region))


MAINLAND_DOCK = (47, 31)   # Mainland ferry-dock sector; returning from the island lands here.
ISLAND_CENTER = (56, 31)


def island_destination(world, collisions, car, to_island: bool):
    """A clear spot on Elite Island (south of its center) or back at the mainland dock."""
    sector = ISLAND_CENTER if to_island else MAINLAND_DOCK
    x, y = world.center_position(*sector)
    y += 200 if to_island else 0
    mass = "island" if to_island else "mainland"

    class OnLandmass:
        def can_move(self, rect):
            sx, sy = int(rect[0] // SECTOR_SIZE), int(rect[1] // SECTOR_SIZE)
            return world._landmass(sx, sy) == mass and world.region_at(rect[0], rect[1]) != "sea" \
                and collisions.can_move(rect)

    saved = car.heading
    car.heading = 0.0
    try:
        spot = nearest_clear_spot(OnLandmass(), car.collision_record, x, y, 24, 600)
    finally:
        car.heading = saved
    return (*spot, 0.0) if spot else None


def on_island(world, x, y) -> bool:
    return world._landmass(int(x // SECTOR_SIZE), int(y // SECTOR_SIZE)) == "island"


def on_mainland_beach(world, x, y) -> bool:
    return world.region_at(x, y) == "beach" and not on_island(world, x, y)
