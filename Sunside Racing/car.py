"""Small arcade driving model with terrain-dependent grip and speed."""

from __future__ import annotations

import math
from dataclasses import dataclass

from collision_manager import nearest_clear_spot
from world import SECTOR_SIZE, TILE_SIZE, Sprite


START_X = 26 * SECTOR_SIZE + 4.5 * TILE_SIZE
START_Y = 31 * SECTOR_SIZE + 4.5 * TILE_SIZE
ACCELERATION = 85          # px/s^2 before terrain grip.
BRAKING = 420
REVERSE_ACCELERATION = 90
TOP_SPEED = 170            # Fastest surface (city); the HUD scales its bar to this.
# Surface -> (grip, top speed px/s). Top speeds stay low so the car is
# controllable in traffic; "track" is the asphalt of mission race levels.
SURFACES = {
    "city": (1.0, TOP_SPEED), "jungle": (0.72, 110),
    "desert": (0.78, 132), "snow": (0.55, 128),
    "rural": (0.80, 140), "beach": (0.68, 105),
    "island": (0.90, 155), "track": (1.0, TOP_SPEED),
}
OFF_SURFACE = (0.75, 110)
# Ice (the snow region's icy roads and the snow race tracks): the car keeps sliding the
# way it was going and only gradually follows where it points. Traction is how fast the
# direction of travel catches up with the heading (per second); sliding sideways scrubs
# a little speed. Rivals slip less (see drag_race.RIVAL_ICE_*).
ICE_TRACTION = 1.8
ICE_SCRUB = 0.9
CRASH_SPEED = 40.0         # A hit that stops the car from above this counts as a crash.
RESPAWN_STEP = 8        # Search ring spacing in pixels.
RESPAWN_CLEARANCE = 16  # Extra width and length so the car is not left wedged.


@dataclass
class Car:
    x: float = START_X
    y: float = START_Y
    heading: float = 0.0  # Degrees clockwise from north.
    speed: float = 0.0
    crashed: bool = False
    travel: float | None = None  # Direction of motion; differs from heading while sliding on ice.

    def reset(self):
        self.x, self.y, self.heading, self.speed = START_X, START_Y, 0.0, 0.0

    def respawn_nearby(self, collisions, max_radius: int = 480) -> bool:
        """Stop at the closest spot with some clearance; fall back to the start."""
        spot = nearest_clear_spot(collisions, self.collision_record, self.x, self.y,
                                  RESPAWN_CLEARANCE, max_radius, RESPAWN_STEP)
        if spot is None:
            self.reset()
            return False
        (self.x, self.y), self.speed = spot, 0.0
        return True

    def obstacle(self):
        """The car as a solid world sprite, used while the player is on foot."""
        return Sprite("vehicle-atlas", "racer_player", self.x, self.y, 64, 64,
                      -self.heading, 24, 44)

    def collision_record(self, x: float | None = None, y: float | None = None):
        return [self.x if x is None else x, self.y if y is None else y,
                255, 255, 255, 255, 0, 24, 44, -self.heading]

    def update(self, dt: float, throttle: int, steer: int, world, collisions,
               speed_scale: float = 1.0):
        """Drive one step. speed_scale multiplies top speed (region level upgrades).

        Sets self.crashed when a collision stops the car from above CRASH_SPEED.
        """
        self.crashed = False
        dt = min(max(dt, 0.0), 0.05)
        # The highway's asphalt counts as city road wherever it runs.
        surface = getattr(world, "surface_at", world.region_at)(self.x, self.y)
        grip, max_speed = SURFACES.get(surface, OFF_SURFACE)
        max_speed *= speed_scale
        # Gentle acceleration (about 2 s to top speed in the city), firm brakes.
        if throttle > 0:
            self.speed += (ACCELERATION if self.speed >= 0 else BRAKING) * grip * dt
        elif throttle < 0:
            self.speed -= (BRAKING if self.speed > 0 else REVERSE_ACCELERATION) * grip * dt
        else:
            drag = 95 * dt
            self.speed = math.copysign(max(0.0, abs(self.speed) - drag), self.speed)
        self.speed = max(-80 * grip, min(max_speed, self.speed))

        if steer and abs(self.speed) > 4:
            turn = 135 * grip * min(1.0, abs(self.speed) / 130)
            self.heading = (self.heading + steer * turn * dt *
                            (1 if self.speed > 0 else -1)) % 360

        # Where the car actually goes: the way it points, except on ice, where the
        # direction of travel lags behind the heading (a slide), scrubbing some speed.
        is_ice = getattr(world, "is_ice", None)
        if self.travel is None or abs(self.speed) < 1 or not (is_ice and is_ice(self.x, self.y)):
            self.travel = self.heading
        else:
            slide = (self.heading - self.travel + 540) % 360 - 180
            self.travel = (self.travel + slide * min(1.0, ICE_TRACTION * dt)) % 360
            self.speed *= max(0.0, 1 - ICE_SCRUB * abs(math.sin(math.radians(slide))) * dt)

        distance = self.speed * dt
        if not distance:
            return
        direction = math.radians(self.travel)
        dx, dy = math.sin(direction) * distance, -math.cos(direction) * distance
        steps = max(1, math.ceil(abs(distance) / 12))
        for _ in range(steps):
            candidate_x, candidate_y = self.x + dx / steps, self.y + dy / steps
            if collisions.can_move(self.collision_record(candidate_x, candidate_y)):
                self.x, self.y = candidate_x, candidate_y
            else:
                self.crashed = abs(self.speed) > CRASH_SPEED
                self.speed = 0.0
                break
