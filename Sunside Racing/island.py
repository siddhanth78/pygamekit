"""Elite Island: the ferry, the island racing center, clubs and leagues, and tournaments.

The island opens (the ferry runs) once every racing center is complete, a region is at
level 25, and the Island pass has been bought; the first crossing uses the pass up and
the island stays open for good (T travels there from then on). On the island the car
drives at the best region's speed.

The island racing center is a level: the races desk (five races, rivals rated 360 to 550),
the clubs desk (join a club: one-time entry fee per league, never back down), and the
tournaments desk (tourney passes; each pass starts a tournament). Clubs are encampments
around the island; the two clubs of a league race each other in tournaments of four
races on four tracks from around Sunside (two laps each): you and three teammates against
the other club's four. Places score 10-8-6-5-4-3-2-1; the team with more points wins (a
tie goes to the team whose best finisher placed higher in the last race). A win pays the
player a quarter of the league's pool plus mastery.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from grid_race import Entrant
from pedestrians import Pedestrian
from progression import REGIONS
from world import SECTOR_SIZE, TILE_SIZE, TILES_PER_SECTOR, Sprite


ISLAND_RACES = 5
ISLAND_RATINGS = (360, 390, 450, 500, 550)
ISLAND_LAPS = 3
ISLAND_RIVALS = (
    ("Coral Jax", "Welcome to the island. It gets harder from here.", "racer_cyan"),
    ("Mako Senn", "The mainland champs all lose their first lap here.", "racer_blue"),
    ("Lani Kade", "Salt air, hot tires. You ready?", "racer_orange"),
    ("Riptide Rue", "Clubs are watching this race. Make it count.", "racer_purple"),
    ("The Admiral", "Every club on this island answers to me.", "racer_black"),
)

# League -> (lowest rating, highest rating or None, entry fee, rating needed to join).
LEAGUES = {1: (350, 490, 20_000, 0), 2: (500, 600, 40_000, 500),
           3: (610, 800, 80_000, 610), 4: (810, None, 150_000, 810)}
# (id, name, league, flag color, racer sprite, roster)
CLUBS = (
    ("palm", "Palm Runners", 1, "#e05a4a", "racer_orange", ("Kai Moana", "Tess Reyes", "Duke Palmer", "Ivy Hale")),
    ("reef", "Reef Raiders", 1, "#3fb0c9", "racer_cyan", ("Nalu Brooks", "Pia Lorne", "Sid Varga", "Mira Coast")),
    ("lagoon", "Lagoon Kings", 2, "#4f9a5a", "racer_lime", ("Rafe Lagos", "Uma Chen", "Bo Tatum", "Ines Vall")),
    ("coral", "Coral Crew", 2, "#f28fb0", "racer_purple", ("Zed Coral", "Anya Ro", "Theo Marsh", "Lulu Park")),
    ("volcano", "Volcano Vipers", 3, "#e08a2a", "racer_yellow", ("Ash Kane", "Pele Ruiz", "Cinder Moss", "Vex Hart")),
    ("tide", "Tidebreakers", 3, "#3f6fd0", "racer_blue", ("Marin Ode", "Cal Surf", "Nerissa Vo", "Kip Shoal")),
    ("summit", "Summit Aces", 4, "#f2ca57", "racer_black", ("Ace Holloway", "Rhea Stone", "Jax Peak", "Nova Crest")),
    ("elite", "Sunside Elite", 4, "#8a55c9", "racer_purple", ("Sol Vantage", "Aria Blaze", "Rex Monarch", "Lyra Vance")),
)
CLUB = {c[0]: c for c in CLUBS}
TOURNEY_PASS_PRICE = 2_000
TOURNEY_RACES = 4
TOURNEY_LAPS = 2
POINTS = (10, 8, 6, 5, 4, 3, 2, 1)
# AI ratings: the player's rating in each track's region plus an offset rolled once when
# the tournament starts (kept for all four tracks). Teammates: one -5..0, two +1..+8.
# Rivals: one -5..0, two +1..+5, one +2..+8.
HOME_OFFSETS = ((-5, 0), (1, 8), (1, 8))
AWAY_OFFSETS = ((-5, 0), (1, 5), (1, 5), (2, 8))


def league_name(league: int) -> str:
    low, high, _, _ = LEAGUES[league]
    return f"League {league} ({low}-{high})" if high else f"League {league} ({low}+)"


def payout(league: int):
    """(the player's tokens, mastery, the pool) for winning a tournament in `league`."""
    share = 10_000 * 2 ** (league - 1)
    return share, 50 * 2 ** (league - 1), 4 * share


def rivals_of(club_id: str) -> str:
    """The other club in the same league."""
    league = CLUB[club_id][2]
    return next(c[0] for c in CLUBS if c[2] == league and c[0] != club_id)


class IslandState:
    """Saved as missions "island": the ferry crossed, center races won, the club, and
    tournaments played and won."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.crossed = data.get("crossed") is True
        races = data.get("races")
        self.races = races if type(races) is int and 0 <= races <= ISLAND_RACES else 0
        self.club = data.get("club") if data.get("club") in CLUB else None
        self.played = data.get("played") if type(data.get("played")) is int and data["played"] >= 0 else 0
        self.won = data.get("won") if type(data.get("won")) is int and data["won"] >= 0 else 0

    def to_dict(self) -> dict:
        return {"crossed": self.crossed, "races": self.races, "club": self.club,
                "played": self.played, "won": self.won}

    @property
    def league(self) -> int:
        return CLUB[self.club][2] if self.club else 0

    def can_join(self, league: int, rating: int) -> bool:
        """Clubs open after the first center race; only leagues above your own, and only
        once your rating reaches the league's floor."""
        return self.races >= 1 and league > self.league and rating >= LEAGUES[league][3]

    def join(self, missions, club_id: str, rating: int) -> str | None:
        """Pay the entry fee and join; returns why not, or None when joined."""
        league = CLUB[club_id][2]
        if not self.can_join(league, rating):
            return "You can't join that club."
        fee = LEAGUES[league][2]
        if missions.tokens < fee:
            return f"The entry fee is {fee:,} S. You don't have enough Sunside Tokens."
        missions.add_item("sunside_tokens", -fee)
        self.club = club_id
        return None


# Tournaments -------------------------------------------------------------------------------

@dataclass
class TourTrack:
    theme: str                  # A mainland region's surface (ice, mud, sand, grass, asphalt).
    seed: str
    size: int

    def as_track(self):
        return {"theme": self.theme, "seed": self.seed, "size": self.size, "laps": TOURNEY_LAPS}


class Tournament:
    """Four races for the player's club against its league rival."""

    def __init__(self, club_id: str, seed):
        rng = random.Random(f"tourney-{seed}")
        self.rng = rng
        self.home, self.away = club_id, rivals_of(club_id)
        self.tracks = [TourTrack(theme, f"tour-{seed}-{i}", rng.randint(5, 9))
                       for i, theme in enumerate(rng.sample(REGIONS, TOURNEY_RACES))]
        self.race = 0                       # Races finished.
        self.points = {"home": 0, "away": 0}
        self.results: list[list[str]] = []  # Per race: team of each place, first to last.
        self.last_order = []
        # Each AI racer's rating offset, fixed for the whole tournament (shuffled so the
        # strong and weak racers aren't always the same names).
        home_offsets = [rng.randint(*r) for r in HOME_OFFSETS]
        away_offsets = [rng.randint(*r) for r in AWAY_OFFSETS]
        rng.shuffle(home_offsets)
        rng.shuffle(away_offsets)
        self.offsets = {"home": home_offsets, "away": away_offsets}

    @property
    def over(self) -> bool:
        return self.race >= TOURNEY_RACES

    def entrants(self, progress) -> list[Entrant]:
        """The next race's 7 AI racers: 3 teammates, then 4 rivals, each rated the
        player's rating in that track's region plus their tournament offset."""
        base = progress.rating(self.tracks[self.race].theme)
        home, away = CLUB[self.home], CLUB[self.away]
        out = [Entrant(name, base + off, "home", home[4]) for name, off in zip(home[5][:3], self.offsets["home"])]
        out += [Entrant(name, base + off, "away", away[4]) for name, off in zip(away[5], self.offsets["away"])]
        return out

    def record(self, order: list[int], entrants: list[Entrant]):
        """A race's finishing order (-1 is the player): scores both teams."""
        teams = ["home" if k == -1 else entrants[k].team for k in order]
        for place, team in enumerate(teams):
            self.points[team] += POINTS[place]
        self.results.append(teams)
        self.last_order = [("You" if k == -1 else entrants[k].name, t) for k, t in zip(order, teams)]
        self.race += 1

    def winner(self) -> str:
        """"home" or "away": more points, or on a tie the best finisher of the last race."""
        if self.points["home"] != self.points["away"]:
            return max(self.points, key=self.points.get)
        return self.results[-1][0]


# Club camps --------------------------------------------------------------------------------

@dataclass(frozen=True)
class ClubCamp:
    club: str
    sector: tuple[int, int]

    def at(self, lx, ly):
        return (self.sector[0] * TILES_PER_SECTOR + lx) * TILE_SIZE, (self.sector[1] * TILES_PER_SECTOR + ly) * TILE_SIZE

    @property
    def tent(self):
        return self.at(3.5, 3.0)

    @property
    def spot(self):
        """Stand here (in front of the tent) to talk to the club."""
        return self.at(3.5, 4.55)

    @property
    def parking(self):
        """(x, y, heading): where the car waits when spawning at the camp."""
        return (*self.at(5.2, 6.1), 0.0)


def choose_camps(world) -> dict[str, ClubCamp]:
    """Eight camps spread over the island's interior, clear of its racing center and the
    ferry dock. Same on every seed."""
    from world import CENTERS
    center = next(s for s, n in CENTERS.items() if n == "center_island")
    dock = world.island_dock
    inner = sorted((x, y) for y in range(64) for x in range(64)
                   if world.region(x, y) == "island"
                   and max(abs(x - center[0]), abs(y - center[1])) >= 2
                   and max(abs(x - dock[0]), abs(y - dock[1])) >= 2)
    rng = random.Random("island-club-camps")
    for apart in (3, 2, 1):
        pool = inner[:]
        rng.shuffle(pool)
        chosen = []
        for sector in pool:
            if all(max(abs(sector[0] - s[0]), abs(sector[1] - s[1])) >= apart for s in chosen):
                chosen.append(sector)
            if len(chosen) == len(CLUBS):
                return {club[0]: ClubCamp(club[0], s) for club, s in zip(CLUBS, sorted(chosen))}
    raise RuntimeError("No room for the club camps")


def camp_sprites(camp: ClubCamp, seed: int) -> list[Sprite]:
    rng = random.Random(f"{seed}-{camp.club}")
    club = CLUB[camp.club]

    def piece(atlas, name, lx, ly, size, rotation=0.0, solid=(0, 0)):
        return Sprite(atlas, name, *camp.at(lx, ly), size, size, rotation, *solid)

    return [piece("island-atlas", "club_tent", 3.5, 3.0, 176, 0.0, (150, 110)),
            piece("island-atlas", f"club_flag_{camp.club}", 5.7, 2.4, 96, 0.0, (12, 12)),
            piece("vehicle-atlas", club[4], 1.8, 5.9, 56, 0.0, (18, 38)),
            piece("vehicle-atlas", club[4], 2.8, 5.9, 56, 0.0, (18, 38)),
            piece("people-atlas", f"{rng.choice(('city_a', 'city_c', 'city_e', 'city_g'))}_idle", 5.0, 4.3, 32, 180.0),
            piece("people-atlas", f"{rng.choice(('city_b', 'city_d', 'city_h'))}_idle", 1.9, 4.6, 32, 150.0)]


def camp_ground(lx: int, ly: int) -> str | None:
    return "dirt_gravel" if 1 <= lx <= 6 and 1 <= ly <= 6 else None


# The ferry ---------------------------------------------------------------------------------

def ferry_spots(world):
    """(mainland spot, island spot): stand here on the sand beside each ferry dock."""
    (mx, my), (ix, iy) = world.mainland_dock, world.island_dock
    mainland = ((mx + 1) * SECTOR_SIZE - 150, (my + 0.5) * SECTOR_SIZE)
    island = (ix * SECTOR_SIZE + 150, (iy + 0.5) * SECTOR_SIZE)
    return mainland, island


def ferry_sprites(world) -> list[Sprite]:
    """The ferry moored at the mainland dock and the ticket kiosks on both sides."""
    (mainland, island) = ferry_spots(world)
    mx, my = world.mainland_dock
    return [Sprite("island-atlas", "ferry_boat", (mx + 1) * SECTOR_SIZE + 150, (my + 0.5) * SECTOR_SIZE - 150,
                   256, 256, 90.0),
            Sprite("island-atlas", "ferry_kiosk", mainland[0] - 20, mainland[1] - 70, 96, 96, 0.0, 60, 40),
            Sprite("island-atlas", "ferry_kiosk", island[0] + 20, island[1] - 70, 96, 96, 0.0, 60, 40)]


# Inside: the island racing center ----------------------------------------------------------------

COLS, ROWS = 16, 10
DOOR_TILE = (8, 9)
WINDOWS = ((3, 0), (12, 0), (3, 9), (12, 9))
FLOOR = "city_plaza"
DESKS = (("races", "Races", 4.0), ("clubs", "Clubs", 8.0), ("tourney", "Tournaments", 12.0))


def at(tx: float, ty: float) -> tuple[float, float]:
    return tx * TILE_SIZE, ty * TILE_SIZE


@dataclass(frozen=True)
class Spot:
    kind: str                # races, clubs, tourney, exit, or look.
    label: str
    x: float
    y: float
    reach: float = 48
    key: str = ""


class IslandCenterInterior:
    """Stands in for the world inside the island racing center."""

    def __init__(self, seed: int = 0):
        self.width, self.height = COLS * TILE_SIZE, ROWS * TILE_SIZE
        self.rng = random.Random(seed + 777_000)
        self.entry = at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.6)
        self.tiles = {(tx, ty): self._tile(tx, ty) for ty in range(ROWS) for tx in range(COLS)}
        self.fixtures = self._fixtures()
        self.obstacles = [f for f in self.fixtures if f.solid_width]
        self.spots = [Spot("exit", "Go outside", *at(DOOR_TILE[0] + 0.5, DOOR_TILE[1] - 0.45), 44)]
        self.spots += [Spot(kind, label, *at(x, 3.35), 52) for kind, label, x in DESKS]
        self.spots += [Spot("look", "Trophies", *at(2.2, 6.6), 44, "Every island champion's name, engraved in gold."),
                       Spot("look", "Trophies", *at(13.8, 6.6), 44, "The Tournament Cup. Four leagues, one name each season.")]
        kinds = ("city_a", "city_c", "city_e", "city_g", "snow_a", "city_h")
        self.people = [Pedestrian(kinds[i % len(kinds)], path, 26, start, {0: ("pause", (2.0, 6.0))})
                       for i, (path, start) in enumerate((
                           ([at(5.5, 5.5), at(10.5, 5.5)], 0.0), ([at(10.5, 7.5), at(5.5, 7.5)], 120.0),
                           ([at(6.2, 6.8)], 0.0), ([at(9.8, 6.2)], 0.0)))]

    def _tile(self, tx, ty):
        if (tx, ty) == DOOR_TILE:
            return "wall_door"
        if (tx, ty) in WINDOWS:
            return "wall_window"
        if tx in (0, COLS - 1) or ty in (0, ROWS - 1):
            return "wall"
        return FLOOR

    def _fixtures(self):
        def piece(name, tx, ty, size, rotation=0.0, solid=(0, 0)):
            return Sprite("island-atlas", name, *at(tx, ty), size, size, rotation, *solid)

        out = [piece(f"desk_{kind}", x, 1.9, 160, 0.0, (140, 60)) for kind, _, x in DESKS]
        out += [piece("trophy_case", 1.4, 6.6, 128, -90.0, (110, 40)),     # The box turns with the art.
                piece("trophy_case", 14.6, 6.6, 128, 90.0, (110, 40)),
                piece("podium", 8.0, 6.4, 128, 0.0, (100, 50))]
        return out

    # World stand-in -----------------------------------------------------------------

    def region_at(self, x, y):
        return "island_center"

    def can_place_walker(self, rect):
        half_w, half_h = rect[7] / 2, rect[8] / 2
        return all(self.tiles.get((int((rect[0] + dx) // TILE_SIZE), int((rect[1] + dy) // TILE_SIZE))) == FLOOR
                   for dx in (-half_w, half_w) for dy in (-half_h, half_h))

    def can_place_car(self, rect):
        return False

    def nearby_obstacles(self, x, y):
        return [o for o in self.obstacles if abs(o.x - x) < 200 and abs(o.y - y) < 200]

    def update(self, dt):
        for person in self.people:
            person.update(dt, self.rng, False)

    def visible_sprites(self):
        out = [Sprite("terrain-atlas", name, (tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE,
                      TILE_SIZE, TILE_SIZE) for (tx, ty), name in self.tiles.items()]
        out += self.fixtures
        out += [p.sprite() for p in self.people]
        return out

    def spot_near(self, x, y):
        near = [(math.dist((x, y), (s.x, s.y)), s) for s in self.spots
                if math.dist((x, y), (s.x, s.y)) <= s.reach]
        return min(near, key=lambda item: item[0])[1] if near else None
