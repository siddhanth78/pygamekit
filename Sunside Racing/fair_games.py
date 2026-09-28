"""The fair's six booth games: pure game state (testable without a window).

Every game is a short attempt with limited tries; its score is compared against four
bands. Bronze, silver, and gold pay Sunside Tokens the first time they're reached;
platinum is the game's maximum score, pays the most, and closes that booth for good.
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
    """6 balls up the lane: stop the aim, then the power. Corner holes pay 100."""
    name, tries = "SKEE-BALL", 6
    CORNERS = (70, 410)
    CORNER_AIM = 10                   # Aim within this of a corner hole's x ...
    CORNER_POWER = 92                 # ... with at least this much power drops in for 100.
    RINGS = ((20, 50), (45, 40), (75, 30), (110, 20))     # (|aim - center|, points); else 10.
    LANE = (340, 30)                  # Where a ball lands at power 0 and 100 (field y), shown
                                      # by the yellow bar sweeping the lane.
    thresholds = (100, 180, 280, 600)
    rules = "6 balls. ENTER stops the aim, then the yellow bar (how far it rolls). Top corners pay 100."

    @classmethod
    def landing_y(cls, power: float) -> float:
        bottom, top = cls.LANE
        return bottom - (bottom - top) * power / 100

    def __init__(self, seed=None):
        super().__init__(seed)
        self.stage = "aim"
        self.x = None
        self.travel = self.rng.uniform(0, 400)
        self.last = None

    def marker(self) -> float:
        if self.stage == "aim":
            return _bounce(self.travel, 40, 440)
        return _bounce(self.travel, 0, 100)

    def update(self, dt, move_x=0, move_y=0):
        super().update(dt)
        if not self.over:
            self.travel += (330 + 20 * self.used if self.stage == "aim" else 150 + 15 * self.used) * dt

    @classmethod
    def points_for(cls, x, power) -> int:
        if power >= cls.CORNER_POWER and any(abs(x - c) <= cls.CORNER_AIM for c in cls.CORNERS):
            return 100
        if power < 55:
            return 0                                      # Rolls back down the lane.
        if power > 90:
            return 10                                     # Jumps the rings.
        off = abs(x - 240)
        return next((p for r, p in cls.RINGS if off <= r), 10)

    def press(self):
        if self.over:
            return
        if self.stage == "aim":
            self.x, self.stage, self.travel = self.marker(), "power", 0.0
            return
        power = self.marker()
        self.last = (self.x, power)
        points = self.points_for(self.x, power)
        self.stage, self.travel = "aim", self.rng.uniform(0, 400)
        self._use(points, "CORNER! +100" if points == 100 else f"+{points}" if points else "Rolled back")


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
