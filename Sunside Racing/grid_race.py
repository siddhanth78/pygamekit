"""Grid races: the player and seven rated AI racers on one track (Elite Island
tournaments), two lanes of four rows behind the line.

Each AI is a rated Rival (drag_race): it drives exactly like a car of its rating on the
racing line, cutting half the corners. The player drives through the other racers (no
car-to-car collisions); walls and tire stacks still stop them. The race ends when the player finishes (everyone
still on track is behind them) or when all seven AIs have finished (the player is last).
Finishing order: finished cars by time, then everyone else by distance covered.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from car import Car
from collision_manager import CollisionManager
from drag_race import (CLEAN_LAP, COUNTDOWN, RIVAL_CUT, STRAIGHT_BOOST, TrackLevel, Rival, calibrated_corner,
                       flawless_time)
from progression import RATING_EDGE, rating_speed
from track_gen import generate
from world import TILE_SIZE


ROWS, LANES = 4, (-1, 1)
ROW_GAP = 0.95                 # Tiles between grid rows (a start straight has 4 tiles behind the line).
FIRST_ROW = 1.0
# The grid alternates like a chessboard (slots front row first, left then right):
#   row 1: home, away   row 2: away, home   row 3: home, away   row 4: away, home
# The player's team (the player and 3 teammates) takes the home squares, the rival club
# the away squares; each race everyone is shuffled onto their team's squares, the player
# included.
TEAM_SLOTS = {"home": (0, 3, 4, 7), "away": (1, 2, 5, 6)}


@dataclass
class Entrant:
    name: str
    rating: int
    team: str                  # "home" (the player's club) or "away".
    sprite: str


def grid_slots(level: TrackLevel):
    """(x, y, heading) of the 8 grid slots, front row first, left lane then right."""
    (ax, ay), (bx, by) = level.path[0], level.path[1]
    heading = math.degrees(math.atan2(bx - ax, -(by - ay))) % 360
    length = math.dist((ax, ay), (bx, by))
    fx, fy = (bx - ax) / length, (by - ay) / length
    rx, ry = -fy, fx
    slots = []
    for row in range(ROWS):
        back = (FIRST_ROW + row * ROW_GAP) * TILE_SIZE
        for lane in LANES:
            slots.append((level.start_x - fx * back + rx * lane * 44, ay - fy * back + ry * lane * 44, heading))
    return slots


# On ice, tournament racers slide more than one-on-one rivals: they steer a little
# softer, drift further off their line, and take longer to recover (about 40 px off the
# line on average, against 25). The room still keeps them on the 3-tile track.
TOURNEY_ICE = {"ice_steer": 1.6, "ice_recover": 0.6, "ice_room": 84}


def rated_rival(level: TrackLevel, rating: float, seed: int, sprite: str, start) -> Rival:
    """A rival that drives like a car of `rating` (see DragRace._rated_rival)."""
    rival = Rival(level, 1.0, random.Random(seed), sprite, RIVAL_CUT, wide_misses=True, start=start,
                  **TOURNEY_ICE)
    rival.rate(rating_speed(rating), math.inf)
    target = flawless_time(level, multiplier=rating_speed(rating - RATING_EDGE)) * CLEAN_LAP * 1.005
    rival.rate(rating_speed(rating), calibrated_corner(rival, target))
    rival.top *= STRAIGHT_BOOST                    # A little quicker down the straights.
    return rival


class GridRace:
    """One 8-car race. track: {"theme", "seed", "size", "laps"}."""

    def __init__(self, track: dict, speed_scale: float, seed: int, entrants: list[Entrant], f1: bool = False):
        rng = random.Random(seed)
        self.level = TrackLevel("circuit", track["theme"], 0, track.get("laps", 2),
                                generate(track["seed"], track.get("size", 6)))
        self.speed_scale = speed_scale
        self.entrants = entrants
        slots = grid_slots(self.level)
        # Each team fills its own squares of the chessboard in a random order; the player
        # draws one of the home squares first.
        free = {team: rng.sample(TEAM_SLOTS[team], len(TEAM_SLOTS[team])) for team in TEAM_SLOTS}
        self.player_slot = free["home"].pop()
        self.car = Car(*slots[self.player_slot][:2], heading=slots[self.player_slot][2], f1=f1)
        taken = {self.player_slot}
        self.slots = []
        for e in entrants:
            own = [s for s in free.get(e.team, []) if s not in taken]
            if not own:                              # A team with more cars than squares:
                reserved = {s for team in free.values() for s in team}
                own = ([s for s in range(len(slots)) if s not in taken | reserved]
                       or [s for s in range(len(slots)) if s not in taken])
            slot = own[-1]
            taken.add(slot)
            self.slots.append(slot)
        self.rivals = [rated_rival(self.level, e.rating, rng.randrange(1 << 30), e.sprite, slots[slot])
                       for e, slot in zip(entrants, self.slots)]
        self.collisions = CollisionManager(None, self.level)
        self.clock = -COUNTDOWN
        self.player_progress = self.level.progress_of(self.car.x, self.car.y, -2 * TILE_SIZE)
        self.times: dict[int, float] = {}         # Entrant index (or -1: the player) -> finish time.
        self.result = None                        # "done" once the order is decided.
        self.order: list[int] = []                # Final order: -1 is the player.
        self.quit = False

    @property
    def countdown(self):
        return max(0, math.ceil(-self.clock)) if self.clock < 0 else 0

    def update(self, dt, throttle, steer):
        if self.result:
            self.car.speed *= max(0.0, 1 - 2 * dt)
            return
        self.clock += dt
        if self.clock < 0:
            return
        # The other seven racers are ghosts to the player (eight cars on a 3-tile track is
        # a pileup): only the track's walls and tire stacks stop the car.
        self.collisions.fixed = []
        self.car.update(dt, throttle, steer, self.level, self.collisions, self.speed_scale)
        length = self.level.race_length
        for i, rival in enumerate(self.rivals):
            rival.update(dt, self.clock)
            if rival.progress >= length and i not in self.times:
                self.times[i] = self.clock
        self.player_progress = self.level.progress_of(self.car.x, self.car.y, self.player_progress)
        if self.player_progress >= length and -1 not in self.times:
            self.times[-1] = self.clock
        if -1 in self.times or len(self.times) == len(self.rivals):
            self.finish()

    def finish(self, quit: bool = False):
        """Decide the order: finished cars by time, then the rest by distance covered (a
        quit puts the player last)."""
        self.quit = quit
        finished = sorted(self.times, key=lambda k: self.times[k])
        rest = [k for k in [-1] + list(range(len(self.rivals))) if k not in self.times]
        progress = {k: (self.player_progress if k == -1 else self.rivals[k].progress) for k in rest}
        if quit:
            rest.remove(-1)
            if -1 in finished:
                finished.remove(-1)
        rest.sort(key=lambda k: -progress[k])
        self.order = finished + rest + ([-1] if quit else [])
        self.result = "done"

    def position(self) -> int:
        """The player's place right now (1-8)."""
        if self.result:
            return self.order.index(-1) + 1
        mine = self.times.get(-1)
        ahead = 0
        for i, rival in enumerate(self.rivals):
            if i in self.times and (mine is None or self.times[i] < mine):
                ahead += 1
            elif i not in self.times and mine is None and rival.progress > self.player_progress:
                ahead += 1
        return ahead + 1

    def lap(self):
        return max(1, min(self.level.laps, int(self.player_progress // self.level.lap_length) + 1))

    def sprites(self, camera_x, camera_y, width, height):
        return self.level.visible_sprites(camera_x, camera_y, width, height) + [r.sprite() for r in self.rivals]
