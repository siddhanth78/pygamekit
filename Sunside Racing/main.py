"""Sunside Racing: an open-world top-down racing game."""

import dataclasses
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import moderngl
import pygame

from autosave import Autosave
from car import TOP_SPEED, Car
from collision_manager import CollisionManager, nearest_clear_spot
from drag_race import DragRace
from fast_travel import destination, island_destination, on_island, on_mainland_beach
from fishing import (FishingSession, beach_destination, fishing_spot, pier_open, pier_requirement,
                     pier_title, trader_near, trader_sprites)
from fishing_ui import SpendMenu, StrikeBar, level_up_line
from game_state import GameState
from hud import FILL, CenterArrow, Hud, compass
from input_handler import InputHandler
from mission_ui import MissionPanel
from missions import TITLES, Missions
from parking import Parking
from pedestrians import Pedestrians
from progression import CENTER_RACES, FISHING_LEVEL, HARDER_LEVEL, ISLAND_LEVEL, REGIONS
from racers import LAPS, RIVAL_RATINGS, rival, track_size
from pause_menu import PauseMenu
from title_menu import TitleMenu
from world_map import WorldMap, guide_line, landmarks
from arcade import ArcadeCabinet
from home import HomeInterior
from inventory import STACK_MAX
from inventory_ui import InventoryMenu
from world import HOME_DOOR, HOME_HOUSE, HOME_PARK
from player_save import PlayerSave
from traffic import Traffic
from walker import CALL_PROMPT_DISTANCE, Walker, call_spot, exit_spot
from world import WORLD_SIZE, World
from world_save import WorldStore


WINDOW_SIZE = (1280, 720)
TITLE = "Sunside Racing"
DRIVE_ZOOM = 1.0
WALK_ZOOM = 2.0     # On foot the camera zooms in so people read at 64 screen px.
ZOOM_TIME = 0.3     # Seconds to zoom in or out when getting out of or into the car.
EXIT_SPEED = 15.0   # The car must be nearly stopped to get out.
RACE_OVER_DELAY = 2.0  # Seconds a win's banner shows before returning (losses end at once).
CENTER_TALK_RANGE = 130  # On foot, px from a racing center building to enter races.
HOME_DOOR_RANGE = 40     # On foot, px from the house's front door to go inside.
SURFACE_NAMES = {"city": "asphalt", "snow": "ice", "rural": "mud", "desert": "sand",
                 "jungle": "grass"}


def camera_position(target, width: float, height: float, zoom: float = 1.0,
                    bounds: tuple[float, float] = (WORLD_SIZE, WORLD_SIZE)):
    """Center the view (in world px) on the car or walker, clamped to the world or level."""
    x = max(0.0, min(target.x - width / 2, bounds[0] - width))
    y = max(0.0, min(target.y - height / 2, bounds[1] - height))
    # Whole-screen-pixel camera keeps NEAREST-filtered tiles from shimmering.
    return round(x * zoom) / zoom, round(y * zoom) / zoom


def eased_zoom(start: float, end: float, elapsed: float) -> float:
    """Ease-out cubic from start to end over ZOOM_TIME, landing exactly on end.

    NEAREST-filtered pixel art shimmers at in-between scales, so the zoom spends as
    little time there as possible and never creeps toward (then snaps onto) its target."""
    k = min(1.0, max(0.0, elapsed / ZOOM_TIME))
    return end if k >= 1.0 else start + (end - start) * (1 - (1 - k) ** 3)


def center_guide(car, center) -> str:
    if not center:
        return "Open water"
    dx, dy = center[1] - car.x, center[2] - car.y
    name = center[0].removeprefix("center_").title()
    # 10 px per metre, for a readable distance.
    return f"{name} racing center  ·  {math.hypot(dx, dy) / 10:.0f} m {compass(dx, dy)}"


def target_guide(player, target, label) -> str:
    dx, dy = target[0] - player.x, target[1] - player.y
    return f"{label}  ·  {math.hypot(dx, dy) / 10:.0f} m {compass(dx, dy)}"


class Game:
    def __init__(self, ctx):
        self.ctx = ctx
        self.world_store = WorldStore(PROJECT_ROOT / "world.json")
        self.world = world = World(store=self.world_store)
        self.state = GameState(ctx, PROJECT_ROOT, TOOLKIT_ROOT, WINDOW_SIZE)
        self.inputs = InputHandler(self.state)
        self.collisions = CollisionManager(self.state, world)
        self.player_save = PlayerSave()
        # The save keeps progress, fish, and points; the game always opens at home, the
        # player sitting in their car in the home parking lot (no resuming the last position).
        self.player_save.load_state(self.collisions)
        self.car = Car(x=HOME_PARK[0], y=HOME_PARK[1], heading=HOME_PARK[2])
        self.walker = None
        self.missions = Missions(world, world.seed, self.player_save.missions_data)
        self.traffic = Traffic(world, world.seed)
        self.collisions.traffic = self.traffic
        self.parking = Parking(world, self.traffic, world.seed)
        self.collisions.parking = self.parking
        self.pedestrians = Pedestrians(world, world.seed)
        self.collisions.pedestrians = self.pedestrians
        viewport = self.state.viewport
        self.arrow = CenterArrow(ctx, TOOLKIT_ROOT, viewport)
        self.menu = PauseMenu(ctx, TOOLKIT_ROOT, viewport)
        self.panel = MissionPanel(ctx, TOOLKIT_ROOT, viewport)
        self.hud = Hud(ctx, TOOLKIT_ROOT, viewport, TOP_SPEED)
        self.spend_menu = SpendMenu(ctx, TOOLKIT_ROOT, viewport)
        # M: the world map. A diamond clicked there sets the guide arrow; nothing is
        # selected at launch, so there is no arrow until the player picks a landmark.
        self.landmarks = landmarks(world, self.missions.givers)
        self.world_map = WorldMap(ctx, TOOLKIT_ROOT, viewport, world, self.landmarks)
        self.guide_to = None
        self.strike_bar = StrikeBar(ctx, TOOLKIT_ROOT, viewport)
        self.fishing: FishingSession | None = None   # On foot at a pier's end, rod out.
        # The house: its own little level, loaded while the player is inside.
        self.home = HomeInterior()
        self.home_collisions = CollisionManager(None, self.home)
        self.inside = False
        self.resting = None     # The home Spot the player is sitting or sleeping on.
        self.arcade = ArcadeCabinet(ctx, TOOLKIT_ROOT, viewport)
        self.inventory = InventoryMenu(ctx, TOOLKIT_ROOT, viewport, self.state)   # I
        self.player_id = self.state.spawn_player(self.car.x, self.car.y)
        # Face the way it's parked from the first frame (getting out at once kept it north).
        self.state.set_player_pose(self.player_id, self.car.x, self.car.y, self.car.heading)
        self.walker_id = None
        self._snap_zoom(DRIVE_ZOOM)
        self.race: DragRace | None = None
        self.race_over = 0.0        # Seconds the finished race has been showing its result.
        self.clock = 0.0
        self.pending_offer = None   # Giver whose offer the panel is showing.
        self.pending_center = None  # Region whose center race the panel is offering.
        self.pending_confirm = None  # "abort" or "quit_race" while the panel asks to confirm.
        self.center_race = None     # (region, race number) while a center race runs.
        self.autosave = Autosave()

    # Helpers --------------------------------------------------------------------

    def _snap_zoom(self, zoom: float):
        """Jump straight to a zoom (loading, travel, races), ending any zoom in progress."""
        self.zoom = self.zoom_from = self.zoom_to = zoom
        self.zoom_time = ZOOM_TIME

    def _spawn_walker_entity(self):
        if self.walker_id is None:
            self.walker_id = self.state.spawn_walker(self.walker.x, self.walker.y)
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _step_out(self, walker: Walker):
        self.car.speed = 0.0
        self.walker = walker
        self._spawn_walker_entity()
        # While parked, the car is solid for the walker and traffic.
        self.collisions.fixed = [self.car.obstacle()]

    def _return_to_giver(self, giver):
        """Failed or finished-level missions end beside their giver, on foot, car nearby."""
        probe = Walker(giver.x, giver.y + 30)
        spot = nearest_clear_spot(self.collisions, probe.collision_record, probe.x, probe.y, 6) \
            or (giver.x, giver.y + 30)
        self.walker = Walker(*spot, heading=0.0)
        self._spawn_walker_entity()
        self.collisions.fixed = []
        car_spot = call_spot(self.walker, self.car, self.collisions)
        if car_spot:
            (self.car.x, self.car.y) = car_spot
        self.car.speed = 0.0
        self.state.set_player_pose(self.player_id, self.car.x, self.car.y, self.car.heading)
        self.collisions.fixed = [self.car.obstacle()]
        self._snap_zoom(WALK_ZOOM)

    # Giving up -----------------------------------------------------------------------

    def _ongoing(self):
        """What the pause menu may abandon: a race (center or drag), or an in-world mission.

        A race that already has a result is over (a win showing its banner), so there is
        nothing left to quit."""
        if self.race:
            return None if self.race.result else "race"
        return "mission" if self.missions.active else None

    def _confirm_give_up(self, choice):
        self.pending_confirm = choice
        if choice == "quit_race":
            if self.center_race:
                region, number = self.center_race
                lines = (f"Race {number} counts as a loss", "You'll be back at the racing center",
                         "You can race it again any time")
            else:
                lines = ("The race counts as a loss and the mission fails",
                         "You'll be back at the mission giver", "You can retry the same mission")
            self.panel.show_confirm("Quit race?", "Failed", lines, "QUIT", "KEEP RACING")
        else:
            mission = self.missions.active
            self.panel.show_confirm("Abort mission?", "Failed", (
                f"{TITLES[mission.offer.type]} for {mission.giver.name}",
                "It counts as failed; you'll be back at the giver",
                "You can retry the same mission"), "ABORT", "KEEP GOING")

    def _give_up(self, choice):
        if choice == "quit_race" and self.race and not self.race.result:
            self.race.result, self.race.quit = "lose", True
            self._end_race()
        elif choice == "abort" and self.missions.active and not self.race:
            self._show_result(self.missions.abort())

    def _refresh_mastery(self):
        player = self.walker or self.car
        here = "" if self.race else self.world.region_at(player.x, player.y)
        self.menu.set_mastery(self.missions.mastery_rows(here), self._island_status())
        self.menu.set_docks([(dock.name, pier_open(self.missions.progress, dock.name))
                             for dock in self.world.docks])

    def _fast_travel(self, region, dock=None):
        """Jump to the region's edge in the car; only offered when no mission is running.
        The beach lands by the chosen fishing pier."""
        self.inside, self.resting = False, None
        player = self.walker or self.car
        if region == "beach":
            landing = beach_destination(self.world, self.collisions, self.car, player.x, player.y, dock)
        else:
            landing = destination(self.world, self.collisions, self.car, region)
        self.menu.toggle()
        if landing is None:
            self.panel.show_message("Fast travel", f"No clear spot at the {region} border right now.")
            return
        self.car.x, self.car.y, self.car.heading = landing
        self.car.speed = 0.0
        self.walker = None
        self.collisions.fixed = []
        self._snap_zoom(DRIVE_ZOOM)
        self.state.set_player_pose(self.player_id, self.car.x, self.car.y, self.car.heading)

    def _show_result(self, result):
        self.autosave.request()  # Lock in mastery and offers right away.
        self.panel.show_result(result)
        if not result["success"] and self.race is None:
            self._return_to_giver(result["giver"])

    # Input ----------------------------------------------------------------------

    def handle(self, action, value) -> bool:
        """Apply one input intent; returns False to quit."""
        if action == "quit":
            return False
        if self.inventory.open:
            self.inventory.handle(action, value)
            return True
        if self.arcade.open:
            if self.arcade.handle(action, value) == "close":
                self.autosave.request()   # Keep a new best score.
            return True
        if self.world_map.open:
            outcome = self.world_map.handle(action, value)
            if isinstance(outcome, tuple):
                self.guide_to = outcome[1]
            elif outcome == "clear":
                self.guide_to = None
            return True
        if self.menu.open:
            self._refresh_mastery()  # Travel buttons depend on the latest state.
            self.menu.set_ongoing(self._ongoing())
            choice = self.menu.handle(action, value)
            if choice == "resume":
                self.menu.toggle()
            elif choice in ("abort", "quit_race"):
                self.menu.toggle()
                self._confirm_give_up(choice)
            elif choice == "home":
                self.menu.toggle()
                self._go_home()
            elif choice == "spend":
                self.menu.toggle()
                self.spend_menu.show(self.missions)
            elif choice and choice.startswith("dock:"):
                self._fast_travel("beach", choice.split(":", 1)[1])
            elif choice and choice.startswith("travel:"):
                self._fast_travel(choice.split(":", 1)[1])
            return choice != "exit"
        if self.spend_menu.open:
            outcome = self.spend_menu.handle(action, value)
            if isinstance(outcome, tuple):
                _, region, amount = outcome
                before = self.missions.unspent
                levels = self.missions.spend(region, amount)
                spent = before - self.missions.unspent
                self.spend_menu.refresh(self.missions, level_up_line(region, levels) if levels
                                        else f"+{spent} {region.title()} mastery")
                self.autosave.request()
            return True
        if self.panel.open:
            outcome = self.panel.handle(action, value)
            if outcome is None:
                return True  # Still choosing (e.g. arrow keys): keep what the panel is for.
            # The panel closed: consume what it was asking about, so nothing stale lingers
            # (a leftover race offer once restarted a race the player had just quit).
            confirm, self.pending_confirm = self.pending_confirm, None
            center, self.pending_center = self.pending_center, None
            giver, self.pending_offer = self.pending_offer, None
            if outcome == "accept":
                if confirm:
                    self._give_up(confirm)
                elif center:
                    self._start_center_race(center)
                elif giver:
                    self._accept(giver)
            elif outcome == "decline" and giver:
                # The giver offers something easy for half the mastery (rounded down, at
                # least 1), shown straight away; at 1 mastery DECLINE goes away.
                offer = self.missions.decline(giver)
                self.pending_offer = giver
                self.panel.show_offer(self.missions.preview(offer))
                self.autosave.request()
            return True
        if action in ("pause", "focus_lost"):
            self.menu.toggle()
        elif self.race:
            if action == "reset" and self.race.result is None:
                self.race.car.respawn_nearby(self.race.collisions)
        elif action == "map":
            self.world_map.toggle()
        elif action == "inventory":
            self.inventory.toggle(self.missions)
        elif action == "confirm":
            if self.fishing:
                self.fishing.press()   # Space: strike while the marker is in the green.
        elif action in ("reset", "island") and self.fishing:
            self.fishing = None        # Put the rod away first; press again to act.
        elif self.inside and action in ("island", "call_car"):
            pass                       # Nothing to travel to or call from inside the house.
        elif self.inside and action == "reset":
            self._stand_up()
            self.walker.respawn_nearby(self.home_collisions)
        elif action == "reset":
            (self.walker or self.car).respawn_nearby(self.collisions)
        elif action == "interact":
            self._interact()
        elif action == "island":
            self._island_travel()
        elif action == "call_car" and self.walker:
            spot = call_spot(self.walker, self.car, self.collisions)
            if spot:
                (self.car.x, self.car.y), self.car.speed = spot, 0.0
                self.state.set_player_pose(self.player_id, self.car.x, self.car.y, self.car.heading)
                self.collisions.fixed = [self.car.obstacle()]
        return True

    def _interact(self):
        if self.inside:
            self._interact_home()
            return
        if self.walker is None:
            spot = exit_spot(self.car, self.collisions) if abs(self.car.speed) < EXIT_SPEED else None
            if spot:
                self._step_out(Walker(*spot, heading=self.car.heading))
            return
        if self.fishing:
            self.fishing.cast()   # Casts again once the last fish is landed or gone.
            return
        # Getting out of the car lands beside the house: E gets back in first; a step
        # toward the door (out of the car's reach) offers the house.
        if (math.dist((self.walker.x, self.walker.y), HOME_DOOR) <= HOME_DOOR_RANGE
                and not self.walker.can_enter(self.car)):
            self._enter_home()
            return
        giver = self.missions.giver_near(self.walker.x, self.walker.y)
        center = self._center_near(self.walker.x, self.walker.y)
        trader = trader_near(self.world, self.walker.x, self.walker.y)
        dock = fishing_spot(self.world, self.walker.x, self.walker.y)
        if giver:
            if self.missions.is_locked(giver):
                self.panel.show_lines(giver.name, "Locked", (
                    f"Veterans work with {giver.region} level {HARDER_LEVEL} drivers.",
                    f"You're {giver.region} level {self.missions.progress.levels[giver.region]}.",
                    "Earn mastery in this region to unlock them."))
            elif self.missions.active:
                self.panel.show_message(giver.name, "Finish your current mission first.")
            else:
                self.pending_offer, self.pending_center = giver, None
                self.panel.show_offer(self.missions.preview(self.missions.offer_for(giver)))
        elif center:
            self._offer_center_race(center)
        elif trader:
            self._talk_to_trader()
        elif dock:
            self._start_fishing(dock)
        elif self.walker.can_enter(self.car):
            self.walker = None
            self.collisions.fixed = []

    # Home -------------------------------------------------------------------------

    def _go_home(self):
        """GO HOME: in the car in the home parking lot, like opening the game."""
        self.inside, self.resting, self.fishing = False, None, None
        self.car.x, self.car.y, self.car.heading = HOME_PARK
        self.car.speed = 0.0
        self.walker = None
        self.collisions.fixed = []
        self._snap_zoom(DRIVE_ZOOM)
        self.state.set_player_pose(self.player_id, self.car.x, self.car.y, self.car.heading)

    def _enter_home(self):
        if self.missions.active:
            self.panel.show_message("Home", "Finish your current mission first.")
            return
        self.inside = True
        self.walker.x, self.walker.y = self.home.entry
        self.walker.heading, self.walker.speed = 180.0, 0.0
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _leave_home(self):
        self._stand_up()
        self.inside = False
        self.walker.x, self.walker.y = HOME_DOOR
        self.walker.heading = 90.0
        self.collisions.fixed = [self.car.obstacle()]   # The parked car stays solid.
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _stand_up(self):
        if self.resting:
            self.walker.x, self.walker.y = self.resting.x, self.resting.y
            self.resting = None

    def _interact_home(self):
        if self.resting:
            self._stand_up()
            return
        spot = self.home.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return
        if spot.kind == "door":
            self._leave_home()
        elif spot.kind in ("sit", "sleep"):
            self.resting = spot
            self.walker.x, self.walker.y, self.walker.heading = spot.px, spot.py, spot.heading
            self.walker.speed = 0.0
        elif spot.kind == "lamp":
            self.home.toggle_lamp(spot.key)
        elif spot.kind == "arcade":
            self.arcade.show(self.missions.arcade)
        else:
            self.panel.show_message(spot.label, spot.key)

    def _home_prompt(self):
        if self.resting:
            return "Move to get up"
        spot = self.home.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return ""
        if spot.kind == "lamp":
            return f"E   Turn {'off' if self.home.lamps[spot.key] else 'on'} the lamp"
        return f"E   {spot.label}" if spot.kind != "look" else f"E   Look  ·  {spot.label}"

    def _update_home(self, dt):
        walker = self.walker
        move_x, move_y, run = self.inputs.walking()
        if self.resting and (move_x or move_y):
            self._stand_up()               # Moving gets the player up.
        if self.resting:
            pose = "player_lie" if self.resting.kind == "sleep" else "player_sit"
        else:
            walker.update(dt, move_x, move_y, run, self.home_collisions)
            pose = walker.frame()
        self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
        self.state.set_frame(self.walker_id, pose)
        self._ease_zoom(dt)

    def _render_home(self):
        walker, zoom = self.walker, self.zoom
        home = self.home
        view_w, view_h = self.state.viewport[0] / zoom, self.state.viewport[1] / zoom
        camera_x, camera_y = camera_position(walker, view_w, view_h, zoom, (home.width, home.height))
        self.state.render(home.visible_sprites(), camera_x, camera_y, [self.walker_id], zoom)
        room = "Bedroom" if walker.x > 9 * 64 else "Living room"
        sleeping = self.resting is not None and self.resting.kind == "sleep"
        self.hud.render(0.0, "Home", room, self._home_prompt(), show_speed=False,
                        mission=("Sleeping...", "Zzz") if sleeping else None, toast=self._toast())
        if self.world_map.open:
            self.world_map.render(*HOME_HOUSE, self.guide_to)

    # Fishing ----------------------------------------------------------------------

    def _start_fishing(self, dock):
        if not pier_open(self.missions.progress, dock.name):
            self.panel.show_message(pier_title(dock.name), f"{pier_requirement(dock.name)} to fish here.")
        elif self.missions.active:
            self.panel.show_message("Fishing pier", "Finish your current mission first.")
        else:
            self.walker.heading, self.walker.speed = dock.heading, 0.0   # Face the sea.
            self.fishing = FishingSession(dock, self.walker.x, self.walker.y)

    def _talk_to_trader(self):
        """Trade the whole bag for universal points, then choose where they go."""
        title = "Fish trader"
        if not self.missions.progress.fishing_unlocked():
            self.panel.show_message(title, f"Fishing and trading open at level {FISHING_LEVEL} in any region.")
            return
        if not self.missions.fish.count:
            unspent = self.missions.unspent
            self.panel.show_lines(title, "", (
                "Bring me fish from the beach piers.",
                "I pay mastery points you can spend on any region.",
                f"You have {unspent} unspent point{'s' if unspent != 1 else ''}" if unspent else ""))
            return
        count, value = self.missions.trade_fish()
        if not count:
            self.panel.show_message(title, f"Your mastery points are full ({STACK_MAX}). Spend some first.")
            return
        self.autosave.request()
        self.spend_menu.show(self.missions, f"Traded {count} fish for {value} point{'s' if value != 1 else ''}")

    def _fishing_prompt(self, walker):
        """Bottom prompt at a trader or a pier's end, or while the rod is out."""
        unlocked = self.missions.progress.fishing_unlocked()
        if self.fishing:
            phase = self.fishing.phase
            if phase in ("ready", "caught", "escaped"):
                return "E   Cast again"
            return "SPACE   Strike!" if phase == "strike" else ""   # The panel says the rest.
        if trader_near(self.world, walker.x, walker.y):
            if not unlocked:
                return f"Trading opens at level {FISHING_LEVEL}"
            bag = self.missions.fish.count
            return f"E   Trade {bag} fish" if bag else "E   Fish trader"
        dock = fishing_spot(self.world, walker.x, walker.y)
        if dock:
            return "E   Fish" if pier_open(self.missions.progress, dock.name) else pier_requirement(dock.name)
        return ""

    # Racing centers -------------------------------------------------------------

    def _center_near(self, x, y):
        """Region of the mainland racing center within talking range, if any."""
        for region in REGIONS:
            cx, cy = self.missions.center_position(region)
            if math.dist((x, y), (cx, cy)) <= CENTER_TALK_RANGE:
                return region
        return None

    def _offer_center_race(self, region):
        title = f"{region.title()} Racing Center"
        won = self.missions.progress.races[region]
        if self.missions.active:
            self.panel.show_message(title, "Finish your current mission first.")
            return
        if won >= CENTER_RACES:
            self.panel.show_lines(title, "Champion", (
                f"You rule the {region} circuit: all {CENTER_RACES} races won.",
                self._island_status(), ""))
            return
        race = won + 1
        name, line, _ = rival(region, race)
        self.pending_center, self.pending_offer = region, None
        self.panel.show_offer({
            "title": title, "difficulty": f"Race {race} / {CENTER_RACES}",
            "detail": f'{name}: "{line}"',
            "rules": f"{name} ({RIVAL_RATINGS[race - 1]}) VS "
                     f"You ({self.missions.progress.rating(region)})",
            "reward": f"{LAPS} laps on {SURFACE_NAMES[region]}  ·  "
                      + ("win to become champion" if race == CENTER_RACES
                         else f"win to unlock race {race + 1}"),
        })

    def _start_center_race(self, region):
        race = self.missions.progress.races[region] + 1
        # Each race has its own seed-generated track; later races are bigger blobs
        # (longer laps, more corners). The rival drives at its rating, tuned to the track.
        track = {"kind": "circuit", "theme": region, "laps": LAPS,
                 "seed": f"{self.world.seed}-{region}-{race}", "size": track_size(race)}
        _, _, sprite = rival(region, race)
        scale = self.missions.progress.speed_scale(region)
        self.race = DragRace(track, None, scale, race * 101 + REGIONS.index(region),
                             rival_sprite=sprite, rival_rating=RIVAL_RATINGS[race - 1])
        self.center_race = (region, race)
        self.race_over = 0.0
        self._snap_zoom(DRIVE_ZOOM)

    def _finish_center_race(self):
        region, number = self.center_race
        race = self.race
        name, _, _ = rival(region, number)
        progress = self.missions.progress
        self.race, self.center_race = None, None
        cx, cy = self.missions.center_position(region)
        self._return_to_giver(type("Spot", (), {"x": cx, "y": cy + 150})())
        title = f"{region.title()} Racing Center"
        if race.result == "win":
            was_unlocked = progress.island_unlocked()
            won = progress.win_race(region)
            chip = "Champion" if won >= CENTER_RACES else "Success"
            lines = [f"You beat {name} in {race.times['player']:.1f} s",
                     (f"{region.title()} champion! Center complete." if won >= CENTER_RACES
                      else f"Race {won + 1} unlocked  ·  {won}/{CENTER_RACES} won")]
            if progress.island_unlocked() and not was_unlocked:
                chip = "Island unlocked"
                lines.append("Elite Island is open: press T on any beach")
            else:
                lines.append(self._island_status() if won >= CENTER_RACES else "")
        else:
            chip = "Failed"
            beaten_by = (f"You quit the race against {name}" if getattr(race, "quit", False)
                         else f"{name} finished first ({race.times['rival']:.1f} s)")
            lines = [beaten_by,
                     "Talk to the center to try again",
                     f"{name} ({RIVAL_RATINGS[number - 1]}) VS You ({progress.rating(region)})"]
        self.autosave.request()
        self.panel.show_lines(title, chip, lines)

    def _island_status(self):
        progress = self.missions.progress
        if progress.island_unlocked():
            return "Elite Island is open: press T on any beach"
        return (f"Elite Island: centers {progress.centers_done()}/{len(REGIONS)}  ·  "
                f"best level {max(progress.levels.values())}/{ISLAND_LEVEL}")

    # Elite Island -----------------------------------------------------------------

    def _island_prompt(self, player):
        if not self.missions.progress.island_unlocked() or self.race:
            return ""
        if on_island(self.world, player.x, player.y):
            return "T   Return to mainland"
        if on_mainland_beach(self.world, player.x, player.y):
            return "T   Travel to Elite Island"
        return ""

    def _island_travel(self):
        player = self.walker or self.car
        prompt = self._island_prompt(player)
        if not prompt:
            return
        if self.missions.active:
            self.panel.show_message("Elite Island", "Finish your current mission first.")
            return
        landing = island_destination(self.world, self.collisions, self.car,
                                     to_island=prompt.endswith("Island"))
        if landing is None:
            self.panel.show_message("Elite Island", "The crossing is blocked right now.")
            return
        self.car.x, self.car.y, self.car.heading = landing
        self.car.speed = 0.0
        self.walker = None
        self.collisions.fixed = []
        self._snap_zoom(DRIVE_ZOOM)
        self.state.set_player_pose(self.player_id, self.car.x, self.car.y, self.car.heading)

    def _accept(self, giver):
        offer = self.missions.accept(giver)
        if offer.type == "drag":
            scale = self.missions.progress.speed_scale(giver.region)
            # The rival's rating was fixed when the offer was made.
            self.race = DragRace(offer.track, None, scale, offer.seed, rival_rating=offer.rating)
            self.race_over = 0.0
            self._snap_zoom(DRIVE_ZOOM)

    # Update -----------------------------------------------------------------------

    def update(self, dt):
        self.clock += dt
        # Runs even while paused; drag races are skipped (they save when they end).
        if self.autosave.tick(dt, allowed=self.race is None):
            self._autosave()
        if self.arcade.open:
            self.arcade.update(dt)
            return
        if self.inventory.open:
            return   # Paused while the inventory is open.
        if self.menu.open or self.panel.open or self.spend_menu.open or self.world_map.open:
            return
        if self.race:
            self._update_race(dt)
        else:
            self._update_world(dt)

    def _update_world(self, dt):
        if self.inside:
            self._update_home(dt)    # The city waits outside while the house is loaded.
            return
        car, walker = self.car, self.walker
        player = walker or car
        if self.fishing and walker is None:
            self.fishing = None   # Travel or the car took the player away from the pier.
        blockers = [car.collision_record()] if walker else []
        self.parking.update(dt, player.x, player.y)
        self.pedestrians.update(dt, player.collision_record())
        self.traffic.update(dt, player.collision_record(), blockers + self.pedestrians.road_blockers())
        if walker and self.fishing:
            move_x, move_y, _ = self.inputs.walking()
            if move_x or move_y:
                self.fishing = None   # Walking off puts the rod away (a hooked fish is lost).
        if walker and self.fishing:
            if self.fishing.update(dt, self.missions.fish):
                self.autosave.request()   # A fish in the bag is saved at once.
            self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
            self.state.set_frame(self.walker_id, self.fishing.pose())
        elif walker:
            walker.update(dt, *self.inputs.walking(), self.collisions)
            self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
            self.state.set_frame(self.walker_id, walker.frame())
        else:
            throttle, steer = self.inputs.driving()
            scale = self.missions.progress.speed_scale(self.world.region_at(car.x, car.y))
            car.update(dt, throttle, steer, self.world, self.collisions, scale)
            self.state.set_player_pose(self.player_id, car.x, car.y, car.heading)
            self.collisions.update(self.player_id, car.collision_record())
        player = self.walker or car
        result = self.missions.update(dt, player.x, player.y, self.walker is None,
                                      abs(car.speed) > 5, car.crashed and self.walker is None)
        if result:
            self._show_result(result)
        self._ease_zoom(dt)

    def _ease_zoom(self, dt):
        target_zoom = WALK_ZOOM if self.walker else DRIVE_ZOOM
        if target_zoom != self.zoom_to:   # Got in or out: ease from wherever the zoom is.
            self.zoom_from, self.zoom_to, self.zoom_time = self.zoom, target_zoom, 0.0
        self.zoom_time += dt
        self.zoom = eased_zoom(self.zoom_from, self.zoom_to, self.zoom_time)

    def _update_race(self, dt):
        race = self.race
        race.update(dt, *self.inputs.driving())
        if race.result == "lose":
            self._end_race()  # A lost race (or failed drag mission) ends at once.
        elif race.result:
            self.race_over += dt  # A win shows its banner for a moment first.
            if self.race_over >= RACE_OVER_DELAY:
                self._end_race()

    def _end_race(self):
        """Leave the race level with its result (a quit counts as a loss)."""
        race = self.race
        if self.center_race:
            self._finish_center_race()
            return
        won = race.result == "win"
        seconds = race.times.get("player", race.clock)
        if getattr(race, "quit", False):
            detail = "You quit the race"
        else:
            detail = (f"You won in {seconds:.1f} s" if won
                      else f"The rival finished first ({race.times['rival']:.1f} s)")
        giver = self.missions.active.giver
        result = self.missions.finish_drag(won, detail)
        self.race = None
        self._return_to_giver(giver)  # Leaving the level puts you back at the giver.
        self.autosave.request()
        self.panel.show_result(result)

    # Render -----------------------------------------------------------------------

    def render(self):
        self.ctx.clear(0.10, 0.25, 0.36, 1.0)
        if self.race:
            self._render_race()
        elif self.inside:
            self._render_home()
        else:
            self._render_world()
        if self.panel.open:
            self.panel.render()
        if self.spend_menu.open:
            self.spend_menu.render()
        if self.arcade.open:
            self.arcade.render()
        if self.inventory.open:
            self.inventory.render()
        if self.menu.open:
            if self.menu.page in ("mastery", "docks"):
                self._refresh_mastery()
            self.menu.set_ongoing(self._ongoing())
            self.menu.render()

    def _render_world(self):
        car, walker, zoom = self.car, self.walker, self.zoom
        player = walker or car
        view_w, view_h = self.state.viewport[0] / zoom, self.state.viewport[1] / zoom
        camera_x, camera_y = camera_position(player, view_w, view_h, zoom)
        # Parked cars out on a trip leave an empty stall behind.
        visible = [s for s in self.world.visible_sprites(camera_x, camera_y, view_w, view_h)
                   if not self.parking.is_away(s)]
        visible += self.traffic.sprites(camera_x, camera_y, view_w, view_h)
        visible += self.pedestrians.sprites(camera_x, camera_y, view_w, view_h)
        visible += self.missions.sprites(self.clock)
        visible += trader_sprites(self.world, self.clock)
        fishing = self.fishing if walker else None
        if fishing:
            visible += fishing.sprites()
        entities = [self.player_id] + ([self.walker_id] if walker else [])
        self.state.render(visible, camera_x, camera_y, entities, zoom)

        region = self.world.region_at(player.x, player.y)
        center = self.world.center_for(player.x, player.y)
        target = self.missions.target()
        if target:
            label = ("Deliver here" if self.missions.active and self.missions.active.offer.type == "delivery"
                     else "Checkpoint" if self.missions.active else "Mission giver")
            guide = target_guide(player, target, label)
        elif self.guide_to:
            guide = guide_line(player, self.guide_to, compass)
        else:
            guide = center_guide(player, center)
        prompt = ""
        giver = self.missions.giver_near(player.x, player.y) if walker else None
        center_region = self._center_near(player.x, player.y) if walker else None
        island = self._island_prompt(player)
        if giver and self.missions.is_locked(giver):
            prompt = f"Veteran  ·  {giver.region.title()} level {HARDER_LEVEL}"
        elif giver:
            prompt = f"E   Talk  ·  {TITLES[giver.type]}"
        elif center_region:
            won = self.missions.progress.races[center_region]
            prompt = ("E   Racing center  ·  Champion" if won >= CENTER_RACES
                      else f"E   Racing center  ·  Race {won + 1}/{CENTER_RACES}")
        elif (walker and math.dist((walker.x, walker.y), HOME_DOOR) <= HOME_DOOR_RANGE
              and not walker.can_enter(car)):
            prompt = "E   Go inside"
        elif walker and (self.fishing or self._fishing_prompt(walker)):
            prompt = self._fishing_prompt(walker)
        elif walker and walker.can_enter(car):
            prompt = "E   Get in"
        elif island:
            prompt = island
        elif walker and math.dist((walker.x, walker.y), (car.x, car.y)) > CALL_PROMPT_DISTANCE:
            prompt = "Q   Call car"
        self.hud.top_speed = TOP_SPEED * self.missions.progress.speed_scale(region)
        mission = self.missions.status()
        if fishing and fishing.status()[0]:
            mission = fishing.status()
        self.hud.render(car.speed, region, guide, prompt, show_speed=walker is None,
                        mission=mission, toast=self._toast())
        if fishing and not (self.menu.open or self.panel.open):
            self.strike_bar.render(fishing)
        # Missions point at their target (yellow); otherwise only a landmark picked on the
        # map gets an arrow, in its kind's color.
        point, color = (target, FILL) if target else (
            ((self.guide_to.x, self.guide_to.y), self.guide_to.arrow_color) if self.guide_to else (None, None))
        if point and not (self.menu.open or self.panel.open or self.spend_menu.open or self.world_map.open):
            # Drawn last so nothing in the world or HUD can cover it.
            self.arrow.render(point[0], point[1], player.x, player.y, camera_x, camera_y, zoom, color)
        if self.world_map.open:
            self.world_map.render(player.x, player.y, self.guide_to)

    def _render_race(self):
        race = self.race
        level, car = race.level, race.car
        view_w, view_h = self.state.viewport
        camera_x, camera_y = camera_position(car, view_w, view_h, 1.0, (level.width, level.height))
        self.state.set_player_pose(self.player_id, car.x, car.y, car.heading)
        self.state.render(race.sprites(camera_x, camera_y, view_w, view_h), camera_x, camera_y,
                          [self.player_id], 1.0)
        remaining = max(0.0, level.race_length - race.player_progress) / 10
        place = "1ST" if race.position() == 1 else "2ND"
        clock = max(0.0, race.clock)
        banner = ""
        if race.clock < 0:
            banner = str(race.countdown)
        elif race.clock < 0.8 and not race.result:
            banner = "GO!"
        elif race.result:
            banner = "YOU WIN!" if race.result == "win" else "YOU LOSE"
        self.hud.top_speed = TOP_SPEED * race.speed_scale
        if self.center_race:
            region, number = self.center_race
            name, _, _ = rival(region, number)
            title, guide = (f"{region.title()} race {number}",
                            f"Lap {race.lap()}/{LAPS}  ·  {remaining:.0f} m to go")
            panel = (f"VS {name.upper()}", f"{place}  ·  Lap {race.lap()}/{LAPS}  ·  {clock:.1f} s")
        else:
            kind = "Quarter mile" if level.kind == "straight" else "Single lap"
            title, guide = "Drag race", f"{kind}  ·  {remaining:.0f} m to go"
            panel = ("DRAG RACE", f"{place}  ·  {clock:.1f} s")
        self.hud.render(car.speed, title, guide, show_speed=True, mission=panel, banner=banner)

    def _toast(self):
        return "Saved" if self.autosave.toast > 0 else ""

    def _saved_pose(self):
        """Car and walker as they should be saved.

        Missions and races are never saved mid-way: while one is under way, the save puts
        the player on foot back where it began (the giver or racing center) with the car
        beside them, so loading (or quitting) counts as quitting it. The live game is not
        changed, so an autosave does not interrupt the mission."""
        if self.center_race:
            cx, cy = self.missions.center_position(self.center_race[0])
            start = (cx, cy + 150)
        elif self.missions.active:
            giver = self.missions.active.giver
            start = (giver.x, giver.y + 30)
        else:
            return self.car, self.walker
        probe = Walker(*start)
        spot = nearest_clear_spot(self.collisions, probe.collision_record, *start, 6) or start
        walker = Walker(*spot)
        car = dataclasses.replace(self.car, speed=0.0)
        car_spot = call_spot(walker, car, self.collisions)
        if car_spot:
            car.x, car.y = car_spot
        return car, walker

    def _autosave(self):
        """Player state is tiny and saved now; the world file is written in the background."""
        self.player_save.save(*self._saved_pose(), self.missions.to_dict())
        self.world_store.save(background=True)

    def save(self):
        self.world_store.save()  # Waits for any background autosave first.
        # Quitting mid-mission or mid-race quits it: saved back at the giver or center.
        self.player_save.save(*self._saved_pose(), self.missions.to_dict())


def run_title(ctx, inputs: InputHandler | None = None) -> bool:
    """Show the title screen until PLAY (True) or EXIT / closing the window (False).
    Nothing of the game is loaded here: just the staged scene and the menu."""
    title = TitleMenu(ctx, PROJECT_ROOT, TOOLKIT_ROOT, WINDOW_SIZE)
    inputs = inputs or InputHandler()
    while True:
        for action, value in inputs.handle_events():
            if action == "quit":
                return False
            choice = title.handle(action, value)
            if choice:
                return choice == "play"
        ctx.clear(0.10, 0.25, 0.36, 1.0)
        title.render()
        pygame.display.flip()


def main():
    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK,
                                    pygame.GL_CONTEXT_PROFILE_CORE)
    pygame.display.set_mode(WINDOW_SIZE, pygame.OPENGL | pygame.DOUBLEBUF, vsync=1)
    pygame.display.set_caption(TITLE)
    ctx = moderngl.create_context()
    ctx.enable(moderngl.BLEND)
    ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
    try:
        if not run_title(ctx):
            return   # EXIT (or closing the window) before anything was loaded or saved.
        game = Game(ctx)
        clock = pygame.time.Clock()
        running = True
        while running:
            # Vsync'd flip() paces frames; a second software cap drops frames.
            dt = min(clock.tick() / 1000.0, 0.05)
            for action, value in game.inputs.handle_events():
                if not game.handle(action, value):
                    running = False
                    break
            if not running:
                break
            game.update(dt)
            game.render()
            pygame.display.flip()
        game.save()
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
