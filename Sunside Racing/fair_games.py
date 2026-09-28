"""The fair's six booth games: pure game state (testable without a window).

Every game is a short attempt with limited tries; its score is compared against four
bands. Bronze, silver, and gold pay Sunside Tokens the first time they're reached;
platinum is the game's maximum score and pays the most (the booth stays open after).
Platinum always needs a flawless run (every dart a bullseye, every mole whacked, ...).

Games share one small interface: update(dt, move_x, move_y), press(), point(x, y),
click(x, y), and the fields score, over, and status. Coordinates are field pixels
(FIELD wide and tall, origin at the top left).
"""

from __future__ import annotations

import math
import random


FIELD = (480, 360)
BANDS = ("bronze", "silver", "gold", "platinum")
BAND_PAY = {"bronze": 10, "silver": 25, "gold": 50, "platinum": 150}


def _bounce(t: float, low: float, high: float) -> float:
    """Ping-pong between low and high; t is the distance travelled."""
    span = high - low
    t %= 2 * span
    return low + (t if t <= span else 2 * span - t)


class Game:
    tries = 1
    thresholds = (1, 2, 3, 4)          # Score needed for bronze, silver, gold, platinum.

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.score = 0
        self.used = 0                   # Tries used.
        self.over = False
        self.status = ""                # One line about the last throw ("Bullseye! +50").
        self.clock = 0.0

    def update(self, dt, move_x=0, move_y=0):
        self.clock += dt

    def press(self):
        pass

    def point(self, x, y):
        pass

    def click(self, x, y):
        self.point(x, y)
        self.press()

    def _use(self, points: int, status: str):
        self.score += points
        self.used += 1
        self.status = status
        if self.used >= self.tries:
            self.over = True

    @classmethod
    def band(cls, score: int) -> int:
        """Bands reached by score: 0 (none) to 4 (platinum)."""
        return sum(score >= t for t in cls.thresholds)


class Darts(Game):
    """3 darts at a board; the crosshair sways, faster with every dart."""
    name, tries = "DARTS", 3
    CENTER, RADIUS = (240, 180), 150
    RINGS = ((14, 50), (30, 25), (60, 20), (90, 15), (120, 10), (150, 5))   # (radius, points)
    thresholds = (20, 50, 100, 150)
    rules = "3 darts. ENTER throws where the crosshair is. Bullseye 50."

    def __init__(self, seed=None):
        super().__init__(seed)
        self.phase = self.rng.uniform(0, 6.28)
        self.darts: list[tuple[float, float]] = []

    def aim(self):
        """A figure-8 through the bullseye, twice per sway, faster with every dart."""
        w = 1.5 + 0.3 * self.used
        t = self.clock * w + self.phase
        cx, cy = self.CENTER
        return cx + 110 * math.sin(t), cy + 110 * math.sin(2 * t)

    @classmethod
    def points_at(cls, x, y) -> int:
        d = math.dist((x, y), cls.CENTER)
        return next((p for r, p in cls.RINGS if d <= r), 0)

    def press(self):
        if self.over:
            return
        x, y = self.aim()
        self.darts.append((x, y))
        points = self.points_at(x, y)
        self._use(points, "BULLSEYE! +50" if points == 50 else f"+{points}" if points else "Missed the board")


class Hammer(Game):
    """High striker: 3 swings; stop the power meter at the top to ring the bell."""
    name, tries = "HIGH STRIKER", 3
    BELL = 95                          # Meter at or above this rings the bell: 100 points.
    thresholds = (150, 210, 260, 300)
    rules = "3 swings. ENTER stops the meter. Ring the bell (top) for 100."

    def __init__(self, seed=None):
        super().__init__(seed)
        self.travel = self.rng.uniform(0, 100)
        self.last = None

    def meter(self) -> float:
        return _bounce(self.travel, 0, 100)

    def update(self, dt, move_x=0, move_y=0):
        super().update(dt)
        if not self.over:
            self.travel += (140 + 45 * self.used) * dt

    def press(self):
        if self.over:
            return
        value = self.meter()
        self.last = value
        points = 100 if value >= self.BELL else int(value)
        self._use(points, "DING! The bell rings: +100" if points == 100 else f"+{points}")


class Balloons(Game):
    """Shooting gallery: 10 pellets at balloons drifting across three rows."""
    name, tries = "BALLOON GALLERY", 10
    ROWS = ((90, 70), (180, -105), (270, 140))          # (y, px/s); negative drifts left.
    RADIUS = 18
    AIM_SPEED = 280
    thresholds = (3, 6, 8, 10)
    rules = "10 pellets. ARROWS or the mouse aim, ENTER or click shoots."

    def __init__(self, seed=None):
        super().__init__(seed)
        self.aim = [240.0, 180.0]
        self.balloons = []                               # [x, row index]
        for row in range(len(self.ROWS)):
            for i in range(3):
                self.balloons.append([i * 160 + self.rng.uniform(0, 100), row])
        self.popped: list[tuple[float, float]] = []      # Recent pops (for a flash).

    def update(self, dt, move_x=0, move_y=0):
        super().update(dt)
        self.aim[0] = min(FIELD[0], max(0, self.aim[0] + move_x * self.AIM_SPEED * dt))
        self.aim[1] = min(FIELD[1], max(0, self.aim[1] + move_y * self.AIM_SPEED * dt))
        for balloon in self.balloons:
            balloon[0] = (balloon[0] + self.ROWS[balloon[1]][1] * dt) % (FIELD[0] + 60)

    def balloon_xy(self, balloon):
        return balloon[0] - 30, self.ROWS[balloon[1]][0]

    def point(self, x, y):
        self.aim = [min(FIELD[0], max(0, x)), min(FIELD[1], max(0, y))]

    def press(self):
        if self.over:
            return
        hit = next((b for b in self.balloons if math.dist(self.balloon_xy(b), self.aim) <= self.RADIUS), None)
        if hit:
            self.popped.append(self.balloon_xy(hit))
            hit[0] = (hit[0] + FIELD[0] / 2) % (FIELD[0] + 60)     # A new balloon, elsewhere.
        self._use(1 if hit else 0, "POP! +1" if hit else "Missed")


class RingToss(Game):
    """5 rings: stop the sideways marker, then the distance marker. Back bottles pay most."""
    name, tries = "RING TOSS", 5
    COLUMNS = (80, 160, 240, 320, 400)
    ROWS = ((70, 30), (140, 20), (210, 10))              # (y, points): back, middle, front.
    REACH = 12                                           # A ring this close to a neck rings it.
    thresholds = (20, 60, 100, 150)
    rules = "5 rings. ENTER stops the side marker, then the distance. Back row 30."

    def __init__(self, seed=None):
        super().__init__(seed)
        self.stage = "side"
        self.x = None
        self.travel = self.rng.uniform(0, 400)
        self.rings: list[tuple[float, float, bool]] = []

    def marker(self) -> float:
        speed_up = 1 + 0.12 * self.used
        if self.stage == "side":
            return _bounce(self.travel * speed_up, 40, 440)
        return _bounce(self.travel * speed_up, 40, 250)   # Distance: y the ring lands at.

    def update(self, dt, move_x=0, move_y=0):
        super().update(dt)
        if not self.over:
            self.travel += (300 if self.stage == "side" else 260) * dt

    def press(self):
        if self.over:
            return
        if self.stage == "side":
            self.x, self.stage, self.travel = self.marker(), "far", 0.0
            return
        x, y = self.x, self.marker()
        self.stage, self.travel = "side", self.rng.uniform(0, 400)
        points = 0
        for by, value in self.ROWS:
            for bx in self.COLUMNS:
                if math.dist((x, y), (bx, by)) <= self.REACH:
                    points = value
        self.rings.append((x, y, bool(points)))
        self._use(points, f"Ringer! +{points}" if points else "Bounced off")


class SkeeBall(Game):
    """6 balls, slingshot style. The ball slides along the bottom of the lane: stop it,
    then pull back and let go (like a slingshot). It flies the opposite way from the pull,
    farther the longer the pull, and lands where it's aimed. Touching a top corner hole
    at all (even its rim) is a corner, 100, but only when the ball was stopped in that
    corner's quarter of the lane (left quarter for the left hole, right for the right);
    from anywhere else it bounces out off the rim (10). A corner ball drops into the
    middle of the hole."""
    name, tries = "SKEE-BALL", 6
    BALL_Y = 250                      # The ball slides along this line,
    SLIDE = (40, 440)                 # between these x.
    MAX_PULL = 100                    # px the band stretches;
    STRETCH = 3.0                     # a pull of p px sends the ball 3p px.
    MIN_PULL = 8                      # Letting go of a shorter pull throws nothing.
    PULL_SPEED = 160                  # px/s the arrows pull the band.
    CORNERS = ((70, 42), (410, 42))   # Corner holes (field px),
    QUARTERS = ((0, FIELD[0] / 4), (FIELD[0] * 3 / 4, FIELD[0]))   # where each must be shot from,
    HOLE_R, BALL_R = 17, 10           # a hole's and the ball's drawn radius.
    RING_CENTER = (240, 115)          # The ring target (drawn 220 x 110, so rings are ellipses
    RINGS = ((18, 50), (41, 40), (69, 30), (101, 20))    # twice as wide as tall): (radius, points).
    ROLL_BACK = 175                   # Landing below this line: it rolls back down (0).
    thresholds = (100, 180, 280, 600)
    rules = ("6 balls. ENTER or click stops the ball; pull back and let go to throw "
             "(or ARROWS, then ENTER). A corner pays 100, shot from its own light end of the lane.")

    def __init__(self, seed=None):
        super().__init__(seed)
        self.stage = "slide"              # slide, then pull.
        self.travel = self.rng.uniform(0, 400)
        self.x = None                     # Where the ball stopped.
        self.pull = None                  # The band's end (field px) while pulling.
        self.dragging = False             # The mouse is holding the band,
        self.grab = (0.0, 0.0)            # grabbed here: the band moves as the mouse does from it.
        self.shots: list[tuple[float, float, int]] = []    # (x, y, points) where balls landed.

    def marker(self) -> float:
        """The sliding ball's x."""
        return _bounce(self.travel, *self.SLIDE)

    def ball(self):
        return (self.x if self.stage == "pull" else self.marker()), self.BALL_Y

    def update(self, dt, move_x=0, move_y=0):
        super().update(dt)
        if self.over:
            return
        if self.stage == "slide":
            self.travel += (300 + 20 * self.used) * dt
        elif (move_x or move_y) and not self.dragging:
            px, py = self.pull
            self._set_pull(px + move_x * self.PULL_SPEED * dt, py + move_y * self.PULL_SPEED * dt)

    def _set_pull(self, x, y):
        """The band's end, kept within MAX_PULL of the ball."""
        bx, by = self.ball()
        dx, dy = x - bx, y - by
        d = math.hypot(dx, dy)
        if d > self.MAX_PULL:
            dx, dy = dx * self.MAX_PULL / d, dy * self.MAX_PULL / d
        self.pull = (bx + dx, by + dy)

    def _stop(self):
        self.x, self.stage = self.marker(), "pull"
        self.pull = self.ball()

    def landing(self):
        """Where the ball lands if let go now (field px, kept on the field)."""
        (bx, by), (px, py) = self.ball(), self.pull
        x = bx + (bx - px) * self.STRETCH
        y = by + (by - py) * self.STRETCH
        return min(FIELD[0] - 10, max(10, x)), min(FIELD[1] - 10, max(20, y))

    @classmethod
    def corner_hit(cls, x, y):
        """The corner hole the ball at (x, y) touches, or None."""
        return next((i for i, hole in enumerate(cls.CORNERS)
                     if math.dist((x, y), hole) <= cls.HOLE_R + cls.BALL_R), None)

    @classmethod
    def points_at(cls, x, y, from_x=None) -> int:
        """Points for a ball landing at (x, y), thrown from x = from_x (None: from the
        corner's own quarter)."""
        corner = cls.corner_hit(x, y)
        if corner is not None:
            low, high = cls.QUARTERS[corner]
            return 100 if from_x is None or low <= from_x <= high else 10     # Off the rim.
        cx, cy = cls.RING_CENTER
        d = math.hypot(x - cx, (y - cy) * 2)
        ring = next((p for r, p in cls.RINGS if d <= r), None)
        if ring:
            return ring
        return 0 if y >= cls.ROLL_BACK else 10

    def _throw(self):
        (bx, by), (px, py) = self.ball(), self.pull
        self.dragging = False
        if math.hypot(px - bx, py - by) < self.MIN_PULL:
            self.pull = self.ball()                        # Too short: nothing thrown.
            return
        x, y = self.landing()
        points = self.points_at(x, y, self.x)
        corner = self.corner_hit(x, y)
        if points == 100:
            x, y = self.CORNERS[corner]                    # It drops into the middle of the hole.
        elif corner is not None:                           # Off the rim: it ends up just below
            hx, hy = self.CORNERS[corner]                  # the hole, clear of it.
            x, y = hx, hy + self.HOLE_R + self.BALL_R + 4
        self.shots.append((x, y, points))
        self.stage, self.pull, self.travel = "slide", None, self.rng.uniform(0, 400)
        status = ("CORNER! +100" if points == 100 else "Off the rim  +10"
                  if corner is not None else f"+{points}" if points else "Rolled back")
        self._use(points, status)

    def press(self):
        """ENTER: stop the ball, or throw with the band as pulled."""
        if self.over:
            return
        if self.stage == "slide":
            self._stop()
        else:
            self._throw()

    def click(self, x, y):
        """Mouse down (anywhere): stop the ball if it's sliding, and grab the band."""
        if self.over:
            return
        if self.stage == "slide":
            self._stop()
        self.dragging = True
        px, py = self.pull
        self.grab = (x - px, y - py)

    def point(self, x, y):
        if self.dragging and not self.over:
            self._set_pull(x - self.grab[0], y - self.grab[1])

    def release(self, x, y):
        """Mouse up: let go of the band."""
        if self.dragging and not self.over:
            self.point(x, y)
            self._throw()


class WhackAMole(Game):
    """30 seconds, 30 moles in a 3 x 3 grid; they stay up for less and less time."""
    name = "WHACK-A-MOLE"
    DURATION, MOLES = 30.0, 30
    UP_TIME = (1.2, 0.55)                                 # Seconds a mole stays up, first to last.
    thresholds = (10, 18, 25, 30)
    rules = "30 seconds. ARROWS + ENTER, or click, to whack. Every mole counts."

    def __init__(self, seed=None):
        super().__init__(seed)
        self.tries = self.MOLES
        self.cursor = 4
        self.schedule = []                                # (start time, hole)
        last = None
        for i in range(self.MOLES):
            hole = self.rng.choice([h for h in range(9) if h != last])
            self.schedule.append((0.5 + i * (self.DURATION - 1.5) / self.MOLES, hole))
            last = hole
        self.whacked: set[int] = set()                    # Mole indices hit.
        self.status = ""

    @staticmethod
    def hole_xy(hole):
        return 120 + (hole % 3) * 120, 70 + (hole // 3) * 110

    def up_time(self, index):
        t = index / max(1, self.MOLES - 1)
        return self.UP_TIME[0] + (self.UP_TIME[1] - self.UP_TIME[0]) * t

    def moles_up(self):
        """Mole index -> hole for every mole showing now."""
        return {i: hole for i, (start, hole) in enumerate(self.schedule)
                if i not in self.whacked and start <= self.clock < start + self.up_time(i)}

    def update(self, dt, move_x=0, move_y=0):
        if self.over:
            return
        super().update(dt)
        if self.clock >= self.DURATION:
            self.over = True
            self.status = "Time!"

    def move(self, dx, dy):
        col, row = self.cursor % 3, self.cursor // 3
        self.cursor = max(0, min(2, row + dy)) * 3 + max(0, min(2, col + dx))

    def point(self, x, y):
        near = min(range(9), key=lambda h: math.dist(self.hole_xy(h), (x, y)))
        if math.dist(self.hole_xy(near), (x, y)) <= 55:
            self.cursor = near

    def press(self):
        if self.over:
            return
        hit = next((i for i, hole in self.moles_up().items() if hole == self.cursor), None)
        if hit is not None:
            self.whacked.add(hit)
            self.score += 1
            self.status = "WHACK! +1"
        else:
            self.status = "Empty hole"


GAMES = {"darts": Darts, "hammer": Hammer, "balloons": Balloons, "ring_toss": RingToss,
         "skee_ball": SkeeBall, "whack": WhackAMole}
