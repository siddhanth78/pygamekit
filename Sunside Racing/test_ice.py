"""Ice: the snow region's icy roads and snow race tracks make cars slide; rivals slide
exactly like the player. Also: the handbrake is gone."""

import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

from car import Car
from drag_race import RIVAL_ICE_ROOM, DragRace
from racers import LAPS, RIVAL_RATINGS, track_size
from world import TILE_SIZE, World
from world_save import DEFAULT_WORLD_SEED


class Sheet:
    """An open stretch of road or ice with nothing to hit."""

    def __init__(self, ice):
        self.ice = ice

    def region_at(self, x, y):
        return "snow" if self.ice else "city"

    def is_ice(self, x, y):
        return self.ice

    def can_move(self, rect):
        return True


def turn_then_straight(ice):
    car = Car(x=0, y=0, heading=0, speed=110)
    for _ in range(40):
        car.update(1 / 60, 1, 1, Sheet(ice), Sheet(ice))
    lag = (car.heading - car.travel + 540) % 360 - 180
    return car, lag


class PlayerIceTests(unittest.TestCase):
    def test_the_car_slides_on_ice_and_grips_elsewhere(self):
        road, road_lag = turn_then_straight(False)
        ice, ice_lag = turn_then_straight(True)
        self.assertEqual(road_lag, 0)
        self.assertGreater(ice_lag, 15)                   # Still sliding the old way.
        self.assertLess(ice.speed, road.speed)            # Sliding scrubs speed.

    def test_the_slide_catches_up_when_straight(self):
        car, _ = turn_then_straight(True)
        for _ in range(180):
            car.update(1 / 60, 1, 0, Sheet(True), Sheet(True))
        self.assertLess(abs((car.heading - car.travel + 540) % 360 - 180), 1)


    def test_icy_roads_are_ice_but_snow_powder_and_the_city_are_not(self):
        world = World()
        ice_tile = next(iter(world.snow_roads))
        x, y = (ice_tile[0] + 0.5) * TILE_SIZE, (ice_tile[1] + 0.5) * TILE_SIZE
        self.assertTrue(world.is_ice(x, y))
        powder = next((tx, ty) for tx in range(ice_tile[0] - 8, ice_tile[0] + 9)
                      for ty in range(ice_tile[1] - 8, ice_tile[1] + 9)
                      if (tx, ty) not in world.snow_roads
                      and world.region_at((tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE) == "snow")
        self.assertFalse(world.is_ice((powder[0] + 0.5) * TILE_SIZE, (powder[1] + 0.5) * TILE_SIZE))
        car = Car()
        self.assertFalse(world.is_ice(car.x, car.y))       # The city.


class RivalIceTests(unittest.TestCase):
    def race(self, theme, number=5):
        track = {"kind": "circuit", "theme": theme, "laps": LAPS,
                 "seed": f"{DEFAULT_WORLD_SEED}-{theme}-{number}", "size": track_size(number)}
        return DragRace(track, None, 1.0, number * 101, rival_rating=RIVAL_RATINGS[number - 1])

    def drive(self, race):
        """Largest drift off the line (px) and slide angle (degrees) over the race, checking
        the rival never jumps (moves more than its speed plus a few px in one frame)."""
        drift = slide = 0.0
        rival = race.rival
        prev = (rival.x, rival.y)
        while "rival" not in race.times and race.clock < 600:
            race.update(1 / 60, 0, 0)
            drift = max(drift, math.hypot(*rival.offset))
            slide = max(slide, abs((rival.heading - rival.travel + 540) % 360 - 180))
            self.assertLess(math.dist(prev, (rival.x, rival.y)), rival.speed / 60 + 4)
            prev = (rival.x, rival.y)
        return drift, slide

    def test_rivals_slide_smoothly_without_jumping(self):
        for number in (1, 5, 10):
            drift, _ = self.drive(self.race("snow", number))
            self.assertLessEqual(drift, RIVAL_ICE_ROOM + 0.5)

    def test_rivals_slide_like_the_player_and_stay_on_the_track(self):
        drift, slide = self.drive(self.race("snow"))
        _, player_slide = turn_then_straight(True)
        self.assertGreater(drift, 30)
        self.assertGreater(slide, player_slide)            # A full corner slides at least as much
        self.assertLess(slide, 60)                         # as the player's short hard turn.

    def test_rivals_use_the_players_ice_physics(self):
        import drag_race
        from car import ICE_SCRUB, ICE_TRACTION
        self.assertIs(drag_race.ICE_TRACTION, ICE_TRACTION)
        self.assertIs(drag_race.ICE_SCRUB, ICE_SCRUB)

    def test_no_slide_on_other_tracks(self):
        self.assertEqual(self.drive(self.race("city")), (0.0, 0.0))


class NoHandbrakeTests(unittest.TestCase):
    def test_driving_input_is_throttle_and_steer_only(self):
        import inspect
        from input_handler import InputHandler
        handler = InputHandler()
        self.assertEqual(handler.driving(), (0, 0))
        self.assertNotIn("handbrake", inspect.signature(Car.update).parameters)


if __name__ == "__main__":
    unittest.main()
