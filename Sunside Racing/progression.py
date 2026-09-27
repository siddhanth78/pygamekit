"""Per-region mastery and levels: missions pay mastery; level-ups grant upgrades."""

from __future__ import annotations


REGIONS = ("city", "jungle", "desert", "snow", "rural")
MISSION_TYPES = ("delivery", "speed", "drag")
SPEED_PER_LEVEL = 0.04   # +4% top speed in a region per level above 1.
HARDER_LEVEL = 5         # Harder mission givers appear from this level.
HARDER_MULTIPLIER = 2
FAST_TRAVEL_LEVEL = 3    # Reaching this level lets the player fast travel to the region.
CENTER_RACES = 10        # Races at each region's racing center.
FISHING_LEVEL = 4        # Any region at this level opens beach fishing and fast travel there.
ISLAND_LEVEL = 25        # With every center complete, one region at this level opens the island.
RATING_BASE = 100        # A level-1 (stock) car's rating.
RATING_PER_LEVEL = 10    # Each level adds this much rating (and SPEED_PER_LEVEL top speed).
# On circuits a clean, corner-cutting race is worth this much rating: a circuit rival rated
# R just loses to a clean driver rated R - RATING_EDGE. Straights have no corners to cut, so
# a straight rival rated R is simply a car rated R.
RATING_EDGE = 10


def rating_difficulty(gap: float, edge: int = RATING_EDGE) -> str:
    """Label for a rival rated gap above the player: at or below them is Easy, up to edge
    above is Medium (a clean, corner-cutting race wins), beyond is Hard."""
    return "Easy" if gap <= 0 else "Medium" if gap <= edge else "Hard"


def rating(level: int) -> int:
    """The car rating at a region level: 100, 110, 120, ..."""
    return RATING_BASE + RATING_PER_LEVEL * (level - 1)


def rating_speed(value: float) -> float:
    """Top-speed multiplier of a car rated value: every 10 points is +4% over stock."""
    return 1.0 + SPEED_PER_LEVEL * (value - RATING_BASE) / RATING_PER_LEVEL


MASTERY_PER_LEVEL = 15  # Mastery to go from level L to L + 1 is this x L.


def mastery_to_next(level: int) -> int:
    """Mastery needed to go from level to level + 1."""
    return MASTERY_PER_LEVEL * level


def mastery_to_reach(level: int) -> int:
    """Total mastery from level 1 up to `level`."""
    return sum(mastery_to_next(l) for l in range(1, level))


def reward(base: int, level: int, harder: bool = False) -> int:
    """Rewards grow by 1 per level; harder givers double them. Nothing earned stays 0."""
    if base <= 0:
        return 0
    return (base + level - 1) * (HARDER_MULTIPLIER if harder else 1)


class Progress:
    def __init__(self, data: dict | None = None):
        self.levels = {region: 1 for region in REGIONS}
        self.mastery = {region: 0 for region in REGIONS}  # Toward the next level.
        self.completed = {region: {kind: 0 for kind in MISSION_TYPES} for region in REGIONS}
        self.races = {region: 0 for region in REGIONS}  # Center races won (0-10).
        for region, state in (data or {}).items():
            if region in REGIONS and isinstance(state, dict):
                level, mastery = state.get("level"), state.get("mastery")
                if type(level) is int and level >= 1 and type(mastery) is int and mastery >= 0:
                    self.levels[region], self.mastery[region] = level, mastery
                races = state.get("races")  # Absent in saves from before racing centers.
                if type(races) is int and 0 <= races <= CENTER_RACES:
                    self.races[region] = races
                done = state.get("completed")  # Absent in saves made before it was tracked.
                if isinstance(done, dict):
                    for kind in MISSION_TYPES:
                        if type(done.get(kind)) is int and done[kind] >= 0:
                            self.completed[region][kind] = done[kind]

    def to_dict(self) -> dict:
        return {region: {"level": self.levels[region], "mastery": self.mastery[region],
                         "completed": dict(self.completed[region]), "races": self.races[region]}
                for region in REGIONS}

    def win_race(self, region: str) -> int:
        """Record a center race win; returns races now won there."""
        self.races[region] = min(CENTER_RACES, self.races[region] + 1)
        return self.races[region]

    def centers_done(self) -> int:
        return sum(self.races[region] >= CENTER_RACES for region in REGIONS)

    def island_unlocked(self) -> bool:
        """Every center complete, and at least one region at ISLAND_LEVEL."""
        return self.centers_done() == len(REGIONS) and max(self.levels.values()) >= ISLAND_LEVEL

    def record_completion(self, region: str, kind: str):
        self.completed[region][kind] += 1

    def add(self, region: str, amount: int) -> list[int]:
        """Add mastery; return every new level reached (possibly several)."""
        gained = []
        self.mastery[region] += amount
        while self.mastery[region] >= mastery_to_next(self.levels[region]):
            self.mastery[region] -= mastery_to_next(self.levels[region])
            self.levels[region] += 1
            gained.append(self.levels[region])
        return gained

    def speed_scale(self, region: str) -> float:
        """Top-speed multiplier where the car is; beaches, sea, and island have no level."""
        level = self.levels.get(region, 1)
        return 1.0 + SPEED_PER_LEVEL * (level - 1)

    def rating(self, region: str) -> int:
        return rating(self.levels.get(region, 1))

    def fishing_unlocked(self) -> bool:
        return max(self.levels.values()) >= FISHING_LEVEL

    def harder_unlocked(self, region: str) -> bool:
        return self.levels[region] >= HARDER_LEVEL
