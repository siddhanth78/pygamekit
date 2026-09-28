"""Sunside Racing: an open-world top-down racing game."""

import dataclasses
import math
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import moderngl
import pygame

from autosave import Autosave
from car import F1_SPEED, TOP_SPEED, Car
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
from store import StoreInterior
from factory import (RUNS, STONE_NAMES, TANK_MAX, FactoryInterior, odds_line, ship, shipment,
                     stone_item)
from fair import GAME_TICKET_PACKS, GAME_TICKET_PRICE, RIDE_NAMES, RIDE_ZOOM, FairInterior, FairOutside
from fair_ui import FairGameOverlay
from farm import CROP_NAMES, FARM_GIFT, FARM_LEVEL, FarmInterior, feed, outdoor_sprites, plot_index
from inventory import BY_ID, count_of, room
from inventory import STACK_MAX
from inventory_ui import InventoryMenu
from orders_ui import OrdersMenu
from market_ui import MarketBoard
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
STORE_DOOR_RANGE = 40    # On foot, px from the General Store's door to go in.
FARM_DOOR_RANGE = 44     # On foot, px from the farmhouse's porch door.
FAIR_DOOR_RANGE = 52     # On foot, px from the Snow Fair's ticket booth window.
FACTORY_RANGE = 50       # On foot, px from the factory's door, the depot, or the cargo dock.
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
        self.landmarks = self._landmarks()
        self.world_map = WorldMap(ctx, TOOLKIT_ROOT, viewport, world, self.landmarks)
        self.guide_to = None
        self.strike_bar = StrikeBar(ctx, TOOLKIT_ROOT, viewport)
        self.fishing: FishingSession | None = None   # On foot at a pier's end, rod out.
        # The house: its own little level, loaded while the player is inside.
        self.home = HomeInterior()
        self.home_collisions = CollisionManager(None, self.home)
        self.inside = False
        self.resting = None     # The home Spot the player is sitting or sleeping on.
        # The General Store: another little level, with its cart of unpaid items.
        self.store = StoreInterior(world.seed)
        self.store_collisions = CollisionManager(None, self.store)
        self.in_store = False
        # The farmhouse: a third little level (cows and hens), once the farm is the player's.
        self.farmhouse = FarmInterior(world.seed)
        self.farm_collisions = CollisionManager(None, self.farmhouse)
        self.in_farm = False
        self.pending_plant = None    # (plot index, crops) while the panel asks which seed.
        self.pending_buy = False     # The fair's ticket counter is asking how many.
        # The Snow Fair: visitors at its lots (outside) and the fairground level (inside).
        self.fair_outside = FairOutside(world.fair, world.seed)
        self.collisions.others.append(self.fair_outside)
        self.fair_level = FairInterior(world.seed)
        self.fair_collisions = CollisionManager(None, self.fair_level)
        self.at_fair = False
        self.fair_game = FairGameOverlay(ctx, TOOLKIT_ROOT, viewport, self.state)
        # The Mining Factory: its floor is a level; the depot and the dock are outside.
        self.factory_level = FactoryInterior(world.seed)
        self.factory_collisions = CollisionManager(None, self.factory_level)
        self.in_factory = False
        self.factory_rng = random.Random()
        self.pending_run = False     # The stone machine is asking how much biofuel to burn.
        self.market_board = MarketBoard(ctx, TOOLKIT_ROOT, viewport, self.state)
        self.arcade = ArcadeCabinet(ctx, TOOLKIT_ROOT, viewport)
        self.inventory = InventoryMenu(ctx, TOOLKIT_ROOT, viewport, self.state)   # I
        self.orders_menu = OrdersMenu(ctx, TOOLKIT_ROOT, viewport)                # O
        self.player_id = self.state.spawn_player(self.car.x, self.car.y)
        self.car.f1 = self.missions.fair.f1
        if self.car.f1:
            self.state.set_sprite(self.player_id, "vehicle-atlas", "racer_f1")
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

    def _landmarks(self):
        """Map landmarks; farm buyers show once the farm is the player's."""
        m = self.missions
        return landmarks(self.world, m.givers, m.orders.orders.values() if m.farm.owned else ())

    def _refresh_landmarks(self):
        """Buyers come and go: rebuild the map's landmarks, keeping the guide if it's still there."""
        self.landmarks = self._landmarks()
        self.world_map.set_marks(self.landmarks)
        if self.guide_to and self.guide_to not in self.landmarks:
            self.guide_to = None

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
        self._leave_store_by_travel()
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
            if self.inventory.handle(action, value) == "spend":
                self.spend_menu.show(self.missions)
            return True
        if self.market_board.open:
            self.market_board.handle(action, value)
            return True
        if self.orders_menu.open:
            outcome = self.orders_menu.handle(action, value)
            if isinstance(outcome, tuple):
                self._take_order(outcome[1])
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
            plant, self.pending_plant = self.pending_plant, None
            buy, self.pending_buy = self.pending_buy, False
            run, self.pending_run = self.pending_run, False
            if outcome.startswith("choice:") and run:
                self._run_machine(list(RUNS)[int(outcome.split(":")[1])])
            elif outcome.startswith("choice:") and buy:
                self._buy_game_tickets(GAME_TICKET_PACKS[int(outcome.split(":")[1])])
            elif outcome.startswith("choice:") and plant:
                index, crops = plant
                self._plant(index, crops[int(outcome.split(":")[1])])
            elif outcome == "accept":
                if confirm == "store_pay":
                    self._pay_at_store()
                elif confirm == "store_leave":
                    self.store.cart.empty()            # Put everything back and go.
                    self._leave_store()
                elif confirm == "factory_unlock":
                    if self.missions.factory.unlock(self.missions):
                        self.autosave.request()
                        self._enter_factory()
                elif confirm == "depot_fill":
                    self._fill_depot()
                elif confirm == "ship_stones":
                    self._ship_stones()
                elif confirm == "fair_enter":
                    self._enter_fair()
                elif confirm and confirm.startswith("order:"):
                    self._take_order(confirm.split(":", 1)[1])
                elif confirm and confirm.startswith("deliver:"):
                    self._deliver_order(confirm.split(":", 1)[1])
                elif confirm:
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
        if self.fair_game.open:
            if self.fair_game.handle(action, value) == "start":
                self._start_fair_game()
            return True
        if self.at_fair and self.fair_level.ride and action not in ("pause", "focus_lost"):
            return True                  # On a ride: sit back and enjoy it.
        if action in ("pause", "focus_lost"):
            self.menu.toggle()
        elif self.race:
            if action == "reset" and self.race.result is None:
                self.race.car.respawn_nearby(self.race.collisions)
        elif action == "map":
            self.world_map.toggle()
        elif action == "inventory":
            self.inventory.toggle(self.missions)
        elif action == "orders":
            if self.missions.active:
                self.panel.show_message("Orders", "Finish your current mission first.")
            else:
                self._show_orders()
        elif action == "confirm":
            if self.fishing:
                self.fishing.press()   # Space: strike while the marker is in the green.
        elif action in ("reset", "island") and self.fishing:
            self.fishing = None        # Put the rod away first; press again to act.
        elif self.in_store and action == "reset":
            self.walker.respawn_nearby(self.store_collisions)
        elif self.in_farm and action == "reset":
            self.walker.respawn_nearby(self.farm_collisions)
        elif self.at_fair and action == "reset":
            self.walker.respawn_nearby(self.fair_collisions)
        elif self.in_factory and action == "reset":
            self.walker.respawn_nearby(self.factory_collisions)
        elif (self.inside or self.in_store or self.in_farm or self.at_fair or self.in_factory) \
                and action in ("island", "call_car"):
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
        if self.in_store:
            self._interact_store()
            return
        if self.in_farm:
            self._interact_farm()
            return
        if self.at_fair:
            self._interact_fair()
            return
        if self.in_factory:
            self._interact_factory()
            return
        if self.walker is None:
            spot = exit_spot(self.car, self.collisions) if abs(self.car.speed) < EXIT_SPEED else None
            if spot:
                self._step_out(Walker(*spot, heading=self.car.heading))
            return
        if self.fishing:
            self.fishing.cast()   # Casts again once the last fish is landed or gone.
            return
        if math.dist((self.walker.x, self.walker.y), self.world.general_store.door) <= STORE_DOOR_RANGE:
            self._enter_store()
            return
        # Getting out of the car lands beside the house: E gets back in first; a step
        # toward the door (out of the car's reach) offers the house.
        if (math.dist((self.walker.x, self.walker.y), HOME_DOOR) <= HOME_DOOR_RANGE
                and not self.walker.can_enter(self.car)):
            self._enter_home()
            return
        factory_spot = self._factory_spot(self.walker)
        if factory_spot:
            {"door": self._factory_door, "depot": self._depot, "dock": self._dock}[factory_spot]()
            return
        if (math.dist((self.walker.x, self.walker.y), self.world.fair.door) <= FAIR_DOOR_RANGE
                and not self.walker.can_enter(self.car)):
            self._fair_gate()
            return
        if (math.dist((self.walker.x, self.walker.y), self.world.farm.door) <= FARM_DOOR_RANGE
                and not self.walker.can_enter(self.car)):
            self._farm_door()
            return
        tile = plot_index(self.world.farm, self.walker.x, self.walker.y)
        # On the plot, E tends it (step off to get in); during a mission the plot waits.
        if tile is not None and self.missions.farm.owned and not self.missions.active:
            self._tend_plot(tile)
            return
        buyer = self.missions.orders.buyer_near(self.walker.x, self.walker.y) if self.missions.farm.owned else None
        if buyer:
            self._talk_to_buyer(buyer)
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
        self._leave_store_by_travel()
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
            extra = (("pit_stop",) if self.missions.fair.f1 else ()) + tuple(self.missions.arcade_unlocked)
            self.arcade.show(self.missions.arcade, extra)
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

    # General Store ----------------------------------------------------------------

    def _enter_store(self):
        self.in_store = True
        self.walker.x, self.walker.y = self.store.entry
        self.walker.heading, self.walker.speed = 0.0, 0.0
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _leave_store(self):
        self.in_store = False
        shop = self.world.general_store
        self.walker.x, self.walker.y = shop.door
        self.walker.heading = 90.0 if shop.face > 0 else 270.0
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _leave_store_by_travel(self):
        """Fast travel or GO HOME from inside: the unpaid cart goes back on the shelves."""
        if self.in_store:
            self.store.cart.empty()
            self.in_store = False
        self.in_farm = False
        self.at_fair = False
        self.fair_level.ride = None
        self.fair_game.open = False
        if self.in_factory:
            self._collect_running_stone()
            self.in_factory = False

    def _interact_store(self):
        spot = self.store.spot_near(self.walker.x, self.walker.y)
        cart = self.store.cart
        if spot is None:
            return
        if spot.kind == "door":
            if cart.count:
                self.pending_confirm = "store_leave"
                self.panel.show_confirm("Leaving?", "", (
                    f"Your cart has {cart.count} item{'s' if cart.count != 1 else ''} ({cart.total:,} S).",
                    "Empty the cart to leave,", "or stay and pay at the cashier first."),
                    "EMPTY CART", "STAY")
            else:
                self._leave_store()
        elif spot.kind == "cashier":
            if not cart.count:
                self.panel.show_message("Cashier", "Pick something from the aisles, then pay here.")
                return
            self.pending_confirm = "store_pay"
            self.panel.show_bill("Checkout", cart.bill(), f"{cart.total:,} S",
                                 f"You have {self.missions.tokens:,} Sunside Tokens", ("PAY", "NOT YET"))
        else:
            why = cart.take(spot.item, self.missions)
            if why:
                self.panel.show_message(BY_ID[spot.item].name, why)

    def _pay_at_store(self):
        cart = self.store.cart
        total, bill, unlocks = cart.total, cart.bill(), cart.unlocks()
        why = cart.pay(self.missions)
        if why:
            self.panel.show_message("Checkout", why)
        else:
            self.autosave.request()
            note = (f"New on your arcade at home: {', '.join(unlocks)}." if unlocks
                    else "It's all in your inventory (I).")
            self.panel.show_bill("Receipt", bill, f"{total:,} S  PAID", note,
                                 ("OK",), chip="Success")

    def _store_prompt(self):
        spot = self.store.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return ""
        if spot.kind == "door":
            return "E   Leave the store"
        if spot.kind == "cashier":
            return f"E   Pay {self.store.cart.total:,} S" if self.store.cart.count else "E   Cashier"
        item = BY_ID[spot.item]
        return f"E   {item.name}  ·  {item.price:,} S"

    def _update_store(self, dt):
        walker = self.walker
        walker.update(dt, *self.inputs.walking(), self.store_collisions)
        self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
        self.state.set_frame(self.walker_id, walker.frame())
        self.store.update(dt)
        self._ease_zoom(dt)

    def _render_store(self):
        walker, zoom, store = self.walker, self.zoom, self.store
        view_w, view_h = self.state.viewport[0] / zoom, self.state.viewport[1] / zoom
        camera_x, camera_y = camera_position(walker, view_w, view_h, zoom, (store.width, store.height))
        self.state.render(store.visible_sprites(), camera_x, camera_y, [self.walker_id], zoom)
        cart = store.cart
        panel = ((f"CART  ·  {cart.count} item{'s' if cart.count != 1 else ''}",
                  f"{cart.total:,} S  ·  you have {self.missions.tokens:,} S") if cart.count else None)
        self.hud.render(0.0, "General Store", store.aisle_at(walker.x, walker.y), self._store_prompt(),
                        show_speed=False, mission=panel, toast=self._toast())
        if self.world_map.open:
            shop = self.world.general_store
            self.world_map.render(shop.x, shop.y, self.guide_to)

    # Mining Factory ---------------------------------------------------------------

    def _factory_spot(self, walker):
        """"door", "depot", or "dock" when standing at one of them outside, else None."""
        site = self.world.factory
        for name, xy in (("door", site.door), ("depot", site.depot_spot), ("dock", site.dock_spot)):
            if math.dist((walker.x, walker.y), xy) <= FACTORY_RANGE and not walker.can_enter(self.car):
                return name
        return None

    def _factory_door(self):
        factory = self.missions.factory
        if self.missions.active:
            self.panel.show_message("Mining Factory", "Finish your current mission first.")
        elif factory.unlocked:
            self._enter_factory()
        elif count_of(self.missions, "factory_pass"):
            self.pending_confirm = "factory_unlock"
            self.panel.show_confirm("Mining Factory", "", ("Use your Factory pass?",
                                                           "It unlocks the factory for good.", ""),
                                    "UNLOCK", "NOT NOW")
            self.panel.selected = 0
        else:
            self.panel.show_lines("Mining Factory", "Locked", ("Entry needs a Factory pass.",
                                                               "The General Store sells it.",
                                                               "One pass unlocks the factory for good."))

    def _enter_factory(self):
        self.in_factory = True
        self.walker.x, self.walker.y = self.factory_level.entry
        self.walker.heading, self.walker.speed = 0.0, 0.0
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _leave_factory(self):
        self._collect_running_stone()
        self.in_factory = False
        self.walker.x, self.walker.y = self.world.factory.door
        self.walker.heading = 180.0
        self.collisions.fixed = [self.car.obstacle()]
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _depot(self):
        factory = self.missions.factory
        if self.missions.active:
            self.panel.show_message("Biofuel depot", "Finish your current mission first.")
        elif not factory.unlocked:
            self.panel.show_message("Biofuel depot", "It fuels the Mining Factory. Unlock the factory first.")
        else:
            self.pending_confirm = "depot_fill"
            self.panel.show_confirm("Biofuel depot", "", (f"Tank: {factory.tank} / {TANK_MAX} biofuel",
                                                          "1 corn makes 1 biofuel.",
                                                          "FILL UP turns your corn into biofuel."),
                                    "FILL UP", "NOT NOW")
            self.panel.selected = 0

    def _fill_depot(self):
        factory = self.missions.factory
        amount = factory.fill(self.missions)
        if amount:
            self.autosave.request()
            self.panel.show_lines("Biofuel depot", "Success", (f"Turned {amount} corn into biofuel",
                                                               f"Tank: {factory.tank} / {TANK_MAX}", ""))
        elif factory.tank >= TANK_MAX:
            self.panel.show_message("Biofuel depot", "The tank is full.")
        else:
            self.panel.show_message("Biofuel depot", "You have no corn. Grow it on your farm's plot.")

    def _dock(self):
        if self.missions.active:
            self.panel.show_message("Cargo dock", "Finish your current mission first.")
            return
        rows, tokens, mastery = shipment(self.missions)
        if not rows:
            self.panel.show_message("Cargo dock", "Bring stones from the factory to ship them.")
            return
        self.pending_confirm = "ship_stones"
        self.panel.show_bill("Cargo dock", self._stone_bill(rows), f"{tokens:,} S + {mastery} mastery",
                             "Mastery points go to your inventory.", ("SHIP", "NOT NOW"))

    def _stone_bill(self, rows):
        """Bill lines at today's market prices."""
        market = self.missions.factory.market
        return [(f"{STONE_NAMES[s]}  {n} x {market.price(s):,} S", f"{market.price(s) * n:,} S") for s, n in rows]

    def _ship_stones(self):
        rows, tokens, mastery = shipment(self.missions)
        bill = self._stone_bill(rows)                    # At the prices it sold for.
        why = ship(self.missions, self.factory_rng)
        if why:
            self.panel.show_message("Cargo dock", why)
            return
        self.autosave.request()
        self.panel.show_bill("Shipped", bill, f"{tokens:,} S + {mastery} mastery  PAID",
                             "The mastery points are in your inventory (I).", ("OK",), chip="Success")

    def _interact_factory(self):
        spot = self.factory_level.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return
        if spot.kind == "exit":
            self._leave_factory()
        elif spot.kind == "market":
            self.market_board.show(self.missions.factory.market)
        elif spot.kind == "machine":
            if self.factory_level.running:
                return                                     # It's already working.
            self.pending_run = True
            sizes = list(RUNS)
            # The description follows the highlighted run size.
            self.panel.show_choice("Stone machine", "", (f"Tank: {self.missions.factory.tank} / {TANK_MAX} biofuel",),
                                   [str(n) for n in sizes],
                                   [(f"{n} biofuel", odds_line(n)) for n in sizes])
        else:
            self.panel.show_message(spot.label, spot.key)

    def _run_machine(self, size):
        if not self.missions.factory.burn(size):
            self.panel.show_message("Stone machine", f"Not enough biofuel in the tank for {size}. "
                                                     "Fill it at the depot outside.")
            return
        self.factory_level.start(size, self.factory_rng)
        self.autosave.request()

    def _collect_running_stone(self):
        """Leaving mid-run: the stone was already paid for, so it comes with the player."""
        if self.factory_level.running:
            stone = self.factory_level.running[1]
            self.factory_level.running = None
            self.missions.add_item(stone_item(stone), 1)

    def _factory_prompt(self):
        spot = self.factory_level.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return ""
        if spot.kind == "machine":
            return "" if self.factory_level.running else "E   Run the stone machine"
        if spot.kind == "exit":
            return "E   Go outside"
        if spot.kind == "market":
            return "E   Stone market"
        return f"E   Look  ·  {spot.label}"

    def _update_factory(self, dt):
        walker = self.walker
        stone = self.factory_level.update(dt)
        if stone:
            self.missions.add_item(stone_item(stone), 1)
            self.autosave.request()
            self.panel.show_lines("Stone machine", "Success", (f"You made {'an' if stone == 'iron' else 'a'} "
                                                               f"{STONE_NAMES[stone]}!",
                                                               "It's in your inventory (I).",
                                                               "Ship stones at the cargo dock outside."))
        walker.update(dt, *self.inputs.walking(), self.factory_collisions)
        self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
        self.state.set_frame(self.walker_id, walker.frame())
        self._ease_zoom(dt)

    def _render_factory(self):
        walker, zoom, level = self.walker, self.zoom, self.factory_level
        view_w, view_h = self.state.viewport[0] / zoom, self.state.viewport[1] / zoom
        camera_x, camera_y = camera_position(walker, view_w, view_h, zoom, (level.width, level.height))
        self.state.render(level.visible_sprites(), camera_x, camera_y, [self.walker_id], zoom)
        tank = self.missions.factory.tank
        panel = ((f"MAKING A STONE  ·  {level.running[0]} BIOFUEL", f"{max(0.0, level.running[2]):.1f} s")
                 if level.running else (f"BIOFUEL  {tank} / {TANK_MAX}", "Run the stone machine"))
        self.hud.render(0.0, "Mining Factory", "Factory floor", self._factory_prompt(), show_speed=False,
                        mission=panel, toast=self._toast())
        if self.world_map.open:
            self.world_map.render(*self.world.factory.building, self.guide_to)

    # Snow Fair --------------------------------------------------------------------

    def _tickets(self) -> int:
        """Game tickets: what the fair's games and rides take (sold at its counter)."""
        return count_of(self.missions, "game_ticket")

    def _fair_gate(self):
        """The ticket booth: one fair ticket to go in."""
        if self.missions.active:
            self.panel.show_message("Snow Fair", "Finish your current mission first.")
            return
        if not count_of(self.missions, "fair_ticket"):
            self.panel.show_lines("Snow Fair", "", ("Entry is 1 fair ticket.",
                                                    "The General Store sells them (100 S each).",
                                                    "Games and rides use game tickets, sold inside."))
            return
        self.pending_confirm = "fair_enter"
        self.panel.show_confirm("Snow Fair", "", ("Entry: 1 fair ticket",
                                                  f"Game tickets for games and rides: {GAME_TICKET_PRICE} S inside.",
                                                  f"Platinum booths: {self.missions.fair.maxed()} / 6"),
                                "GO IN", "NOT NOW")
        self.panel.selected = 0

    def _enter_fair(self):
        if not self.missions.add_item("fair_ticket", -1):
            return
        self.autosave.request()
        self.at_fair = True
        self.walker.x, self.walker.y = self.fair_level.entry
        self.walker.heading, self.walker.speed = 0.0, 0.0
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _leave_fair(self):
        self.at_fair = False
        self.fair_level.ride = None
        self.walker.x, self.walker.y = self.world.fair.door
        self.walker.heading = 180.0
        self.collisions.fixed = [self.car.obstacle()]
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _interact_fair(self):
        spot = self.fair_level.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return
        fair = self.missions.fair
        if spot.kind == "exit":
            self._leave_fair()
        elif spot.kind == "booth":
            if fair.closed(spot.key):
                self.panel.show_lines(spot.label, "Champion", ("You scored platinum here.",
                                                                "This booth is closed for good.",
                                                                f"Platinum booths: {fair.maxed()} / 6"))
            else:
                self.fair_game.show(spot.key, fair.best.get(spot.key, 0), fair.band.get(spot.key, 0))
        elif spot.kind == "counter":
            self.pending_buy = True
            self.panel.show_choice("Game tickets", "", (f"{GAME_TICKET_PRICE} S each.",
                                                        "One ticket plays a game or rides a ride.", ""),
                                   [f"BUY {n}" for n in GAME_TICKET_PACKS])
        elif spot.kind == "ride":
            if not self._tickets():
                self.panel.show_message(spot.label, "Rides take 1 game ticket. The ticket counter by the gate sells them.")
                return
            self.missions.add_item("game_ticket", -1)
            self.autosave.request()
            self.fair_level.start_ride(spot.key)
            self.ride_exit = (spot.x, spot.y)
        else:
            self.panel.show_message(spot.label, spot.key)

    def _start_fair_game(self):
        """ENTER at a booth: one ticket buys an attempt."""
        if not self._tickets():
            self.panel.show_message("Game tickets", "You're out of game tickets. The ticket counter by the gate sells them.")
            return
        self.missions.add_item("game_ticket", -1)
        self.autosave.request()
        self.fair_game.begin()

    def _buy_game_tickets(self, count: int):
        """The fair's ticket counter: count game tickets for GAME_TICKET_PRICE S each."""
        cost = count * GAME_TICKET_PRICE
        if self.missions.tokens < cost:
            self.panel.show_message("Game tickets", f"{count} ticket{'s' if count != 1 else ''} cost {cost:,} S. "
                                                    "You don't have enough Sunside Tokens.")
            return
        if count > room(count_of(self.missions, "game_ticket"), "game_ticket"):
            self.panel.show_message("Game tickets", "You can't carry that many game tickets.")
            return
        self.missions.add_item("sunside_tokens", -cost)
        self.missions.add_item("game_ticket", count)
        self.autosave.request()
        self.panel.show_lines("Game tickets", "Success", (f"Bought {count} game ticket{'s' if count != 1 else ''}",
                                                          f"Paid {cost:,} S", "Enjoy the fair!"))

    def _update_fair_game(self, dt):
        move_x, move_y, _ = self.inputs.walking()
        score = self.fair_game.update(dt, move_x, move_y)
        if score is None:
            return
        fair, game_id = self.missions.fair, self.fair_game.game_id
        earned, new, prize = fair.record(game_id, score)
        self.missions.add_item("sunside_tokens", earned)
        self.fair_game.earned, self.fair_game.best = earned, fair.best[game_id]
        self.fair_game.band = fair.band[game_id]
        self.autosave.request()
        if prize:
            self._grant_f1()

    def _grant_f1(self):
        """Every booth at platinum: the F1 car, and Pit Stop at the home arcade."""
        self.car.f1 = True
        self.state.set_sprite(self.player_id, "vehicle-atlas", "racer_f1")
        self.panel.show_lines("GRAND PRIZE", "Champion", (
            "All six booths at platinum! The F1 car is yours:",
            "+10% top speed and +20% grip everywhere.",
            "And a new game on your arcade at home: PIT STOP."))

    def _fair_prompt(self):
        if self.fair_level.ride:
            return ""
        spot = self.fair_level.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return ""
        if spot.kind == "booth":
            return (f"{spot.label}  ·  closed (platinum)" if self.missions.fair.closed(spot.key)
                    else f"E   Play {spot.label}  ·  1 ticket")
        if spot.kind == "ride":
            return f"E   Ride the {spot.label.lower()}  ·  1 ticket"
        if spot.kind == "counter":
            return f"E   Game tickets  ·  {GAME_TICKET_PRICE} S each"
        if spot.kind == "exit":
            return "E   Leave the fair"
        return f"E   Look  ·  {spot.label}"

    def _update_fair(self, dt):
        level, walker = self.fair_level, self.walker
        level.update(dt)
        if level.ride:
            driving = self.inputs.driving() if level.ride.kind == "bumper_cars" else (0, 0)
            if level.update_ride(dt, *driving):
                walker.x, walker.y = self.ride_exit          # Off the ride, back where we got on.
            else:
                x, y, heading = level.ride_pose()            # Sitting in their seat as it goes round.
                walker.x, walker.y = x, y
                self.state.set_frame(self.walker_id, "player_sit")
                self.state.set_player_pose(self.walker_id, x, y + (2 if level.ride.kind == "ferris_wheel" else 0), heading)
                self._ease_ride_zoom(dt, RIDE_ZOOM[level.ride.kind])
                return
        walker.update(dt, *self.inputs.walking(), self.fair_collisions)
        self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
        self.state.set_frame(self.walker_id, walker.frame())
        self._ease_zoom(dt)

    def _ease_ride_zoom(self, dt, target):
        if target != self.zoom_to:
            self.zoom_from, self.zoom_to, self.zoom_time = self.zoom, target, 0.0
        self.zoom_time += dt
        self.zoom = eased_zoom(self.zoom_from, self.zoom_to, self.zoom_time)

    def _render_fair(self):
        walker, zoom, level = self.walker, self.zoom, self.fair_level
        view_w, view_h = self.state.viewport[0] / zoom, self.state.viewport[1] / zoom
        camera_x, camera_y = camera_position(walker, view_w, view_h, zoom, (level.width, level.height))
        riding_bumper = level.ride and level.ride.kind == "bumper_cars"
        self.state.render(level.visible_sprites(), camera_x, camera_y, [] if riding_bumper else [self.walker_id], zoom)
        fair = self.missions.fair
        panel = (f"GAME TICKETS  {self._tickets()}", f"Platinum booths {fair.maxed()} / 6")
        if level.ride:
            panel = (RIDE_NAMES[level.ride.kind].upper(),
                     "ARROWS drive  ·  bump away!" if level.ride.kind == "bumper_cars" else "Enjoy the ride!")
        area = level.area_at(walker.x, walker.y)
        self.hud.render(0.0, "Snow Fair", area, self._fair_prompt(), show_speed=False, mission=panel,
                        toast=self._toast())
        if self.world_map.open:
            self.world_map.render(*self.world.fair.center, self.guide_to)

    # Farm -------------------------------------------------------------------------

    def _farm_door(self):
        """Abandoned until rural level FARM_LEVEL; then E makes it the player's (with
        FARM_GIFT tokens, once); after that E goes inside."""
        farm = self.missions.farm
        if not farm.owned:
            level = self.missions.progress.levels["rural"]
            if farm.claim(self.missions):
                self._refresh_landmarks()          # Farm buyers appear on the map.
                self.autosave.request()
                self.panel.show_lines("Farmhouse", "Success", (
                    "The farmhouse is yours!", f"+{FARM_GIFT} Sunside Tokens to get you started.",
                    "Seeds, fertilizer, and feed: the General Store."))
            else:
                self.panel.show_lines("Abandoned farmhouse", "Locked", (
                    "The windows are boarded up; nobody lives here.",
                    f"Reach rural level {FARM_LEVEL} to make it yours.", f"You're rural level {level}."))
            return
        if self.missions.active:
            self.panel.show_message("Farmhouse", "Finish your current mission first.")
            return
        self.in_farm = True
        self.walker.x, self.walker.y = self.farmhouse.entry
        self.walker.heading, self.walker.speed = 0.0, 0.0
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _leave_farm(self):
        self.in_farm = False
        self.walker.x, self.walker.y = self.world.farm.door
        self.walker.heading = 180.0
        self.collisions.fixed = [self.car.obstacle()]   # The parked car stays solid.
        self.state.set_player_pose(self.walker_id, self.walker.x, self.walker.y, self.walker.heading)

    def _tend_plot(self, index):
        """E on a plot tile: plant (asking which seed when there's a choice), fertilize a
        sprout, or harvest a ripe crop."""
        farm, cell = self.missions.farm, self.missions.farm.plot[index]
        if cell is None:
            crops = farm.seeds_owned(self.missions)
            if not crops:
                self.panel.show_message("Farm plot", "You have no seeds. The General Store sells them.")
            elif len(crops) == 1:
                self._plant(index, crops[0])
            else:
                self.pending_plant = (index, crops)
                self.panel.show_choice("Plant a seed", "", (
                    "  ·  ".join(f"{CROP_NAMES[c]} x{count_of(self.missions, f'seeds_{c}')}" for c in crops),
                    "One seed per tile; super fertilizer grows it.", ""),
                    [CROP_NAMES[c].upper() for c in crops])
            return
        why = farm.fertilize(self.missions, index) if cell[1] == "sprout" else farm.harvest(self.missions, index)
        if why:
            self.panel.show_message("Farm plot", why)
        else:
            self.autosave.request()

    def _plant(self, index, crop):
        why = self.missions.farm.plant(self.missions, index, crop)
        if why:
            self.panel.show_message("Farm plot", why)
        else:
            self.autosave.request()

    def _plot_prompt(self, walker):
        if not self.missions.farm.owned or self.missions.active:
            return ""
        index = plot_index(self.world.farm, walker.x, walker.y)
        if index is None:
            return ""
        cell = self.missions.farm.plot[index]
        if cell is None:
            return "E   Plant a seed"
        name = CROP_NAMES[cell[0]]
        if cell[1] == "sprout":
            return f"E   Fertilize  ·  {name}"
        return f"E   Harvest  ·  {name}"

    def _farm_door_prompt(self):
        if self.missions.farm.owned:
            return "E   Go inside the farmhouse"
        if self.missions.progress.levels["rural"] >= FARM_LEVEL:
            return "E   Claim the farmhouse"
        return "E   Abandoned farmhouse"

    def _talk_to_buyer(self, order):
        """Take a buyer's order, deliver it when the goods are in the inventory, or say
        what's still missing. Only one order is worked on at a time."""
        orders, title = self.missions.orders, order.title
        if self.missions.active:
            self.panel.show_message(title, "Finish your current mission first.")
            return
        missing = orders.missing(self.missions, order)
        chip = "Tokens" if order.pay == "tokens" else "Mastery"
        if not missing:
            self.pending_confirm = f"deliver:{order.region}"
            self.panel.show_confirm(title, chip, (f"Wants {order.goods_line()}", order.pay_line(),
                                                  "You have everything they need."), "DELIVER", "NOT NOW")
            self.panel.selected = 0
        elif orders.active == order.region:
            self.panel.show_lines(title, chip, (
                "Still waiting for " + ",  ".join(f"{n} {BY_ID[g].name.lower()}" for g, n in missing.items()),
                order.pay_line(), "Grow crops on your plot; milk and eggs come from the farmhouse."))
        else:
            self.pending_confirm = f"order:{order.region}"
            switching = orders.active and orders.orders.get(orders.active)
            self.panel.show_confirm(title, chip, (
                f"Wants {order.goods_line()}", order.pay_line(),
                f"Instead of the {orders.active.title()} order (it stays open)" if switching
                else "Bring the goods here to deliver."), "TAKE ORDER", "NOT NOW")
            self.panel.selected = 0

    def _show_orders(self):
        """O: every open order, from wherever the player is (house, store, ...)."""
        m, orders = self.missions, self.missions.orders
        if not m.farm.owned:
            self.orders_menu.show([], f"No orders yet. Farm buyers come once the farmhouse is yours "
                                      f"(rural level {FARM_LEVEL}).")
            return
        here = self._where()
        rows = []
        for order in sorted(orders.orders.values(), key=lambda o: math.dist(here, (o.x, o.y))):
            dx, dy = order.x - here[0], order.y - here[1]
            rows.append({"region": order.region, "title": order.title, "wants": f"Wants {order.goods_line()}",
                         "pays": (f"{order.value:,} S" if order.pay == "tokens" else f"{order.points} mastery points"),
                         "where": f"{math.hypot(dx, dy) / 10000:.1f} km {compass(dx, dy)}",
                         "active": orders.active == order.region,
                         "ready": not orders.missing(m, order)})
        self.orders_menu.show(rows)

    def _where(self):
        """The player's spot in the world, even from inside a building."""
        if self.inside:
            return HOME_HOUSE
        if self.in_store:
            shop = self.world.general_store
            return shop.x, shop.y
        if self.in_farm:
            return self.world.farm.house
        if self.at_fair:
            return self.world.fair.center
        if self.in_factory:
            return self.world.factory.building
        player = self.walker or self.car
        return player.x, player.y

    def _buyer_mark(self, region):
        order = self.missions.orders.orders.get(region)
        return next((m for m in self.landmarks if m.kind == "buyer" and order
                     and (m.x, m.y) == (order.x, order.y)), None)

    def _take_order(self, region):
        self.missions.orders.take(region)
        self.guide_to = self._buyer_mark(region)     # The guide arrow points at the buyer.
        self.autosave.request()

    def _deliver_order(self, region):
        order = self.missions.orders.orders[region]
        why = self.missions.orders.deliver(self.missions, region)
        if why:
            self.panel.show_message(order.title, why)
            return
        self._refresh_landmarks()                    # A new buyer elsewhere in the region.
        self.autosave.request()
        paid = (f"+{order.value:,} Sunside Tokens" if order.pay == "tokens"
                else f"+{order.points} mastery points (in your inventory)")
        self.panel.show_lines(order.title, "Success", (f"Delivered {order.goods_line()}", paid,
                                                       f"A new {region} buyer is waiting somewhere else."))

    def _interact_farm(self):
        spot = self.farmhouse.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return
        if spot.kind == "door":
            self._leave_farm()
        elif spot.kind in ("cow", "hen"):
            why = feed(self.missions, spot.kind)
            if why:
                self.panel.show_message(spot.label, why)
            else:
                self.autosave.request()
        else:
            self.panel.show_message(spot.label, spot.key)

    def _farmhouse_prompt(self):
        spot = self.farmhouse.spot_near(self.walker.x, self.walker.y)
        if spot is None:
            return ""
        if spot.kind in ("cow", "hen"):
            return f"E   {spot.label}"
        return f"E   {spot.label}" if spot.kind != "look" else f"E   Look  ·  {spot.label}"

    def _update_farm(self, dt):
        walker = self.walker
        walker.update(dt, *self.inputs.walking(), self.farm_collisions)
        self.state.set_player_pose(self.walker_id, walker.x, walker.y, walker.heading)
        self.state.set_frame(self.walker_id, walker.frame())
        self.farmhouse.update(dt)
        self._ease_zoom(dt)

    def _render_farm(self):
        walker, zoom, house = self.walker, self.zoom, self.farmhouse
        view_w, view_h = self.state.viewport[0] / zoom, self.state.viewport[1] / zoom
        camera_x, camera_y = camera_position(walker, view_w, view_h, zoom, (house.width, house.height))
        self.state.render(house.visible_sprites(), camera_x, camera_y, [self.walker_id], zoom)
        m = self.missions
        panel = (f"COW FEED {count_of(m, 'cow_feed')}  ·  HEN FEED {count_of(m, 'hen_feed')}",
                 f"Milk {count_of(m, 'milk')}  ·  Eggs {count_of(m, 'eggs')}")
        self.hud.render(0.0, "Farmhouse", house.room_at(walker.x, walker.y), self._farmhouse_prompt(),
                        show_speed=False, mission=panel, toast=self._toast())
        if self.world_map.open:
            self.world_map.render(*self.world.farm.house, self.guide_to)

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
        """Trade the whole bag for universal points, which go to the inventory."""
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
        # The points go to the inventory; the player spends them from there (or Mastery).
        self.panel.show_lines(title, "", (
            f"Traded {count} fish for {value} mastery point{'s' if value != 1 else ''}.",
            "They're in your inventory (I): select them to spend.", ""))

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
            "can_decline": False,
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
                             rival_sprite=sprite, rival_rating=RIVAL_RATINGS[race - 1], f1=self.car.f1)
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
            self.race = DragRace(offer.track, None, scale, offer.seed, rival_rating=offer.rating,
                                 f1=self.car.f1)   # Rival ratings stay on the base car (F1 is a real edge).
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
        if self.inventory.open or self.orders_menu.open or self.market_board.open:
            return   # Paused while the inventory or the orders are open.
        if self.menu.open or self.panel.open or self.spend_menu.open or self.world_map.open:
            return
        if self.fair_game.open:
            self._update_fair_game(dt)   # The fair waits while a booth game is on.
            return
        if self.race:
            self._update_race(dt)
        else:
            self._update_world(dt)

    def _update_world(self, dt):
        if self.inside:
            self._update_home(dt)    # The city waits outside while the house is loaded.
            return
        if self.in_store:
            self._update_store(dt)   # Likewise the store.
            return
        if self.in_factory:
            self._update_factory(dt)  # And the factory.
            return
        if self.at_fair:
            self._update_fair(dt)    # And the fair.
            return
        if self.in_farm:
            self._update_farm(dt)    # And the farmhouse.
            return
        car, walker = self.car, self.walker
        player = walker or car
        if self.fishing and walker is None:
            self.fishing = None   # Travel or the car took the player away from the pier.
        blockers = [car.collision_record()] if walker else []
        self.parking.update(dt, player.x, player.y)
        self.fair_outside.update(dt, player.x, player.y)
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
            scale = self.missions.progress.speed_scale(self.world.surface_at(car.x, car.y))
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
        elif self.in_store:
            self._render_store()
        elif self.in_farm:
            self._render_farm()
        elif self.at_fair:
            self._render_fair()
        elif self.in_factory:
            self._render_factory()
        else:
            self._render_world()
        if self.fair_game.open:
            self.fair_game.render()
        if self.panel.open:
            self.panel.render()
        if self.spend_menu.open:
            self.spend_menu.render()
        if self.arcade.open:
            self.arcade.render()
        if self.inventory.open:
            self.inventory.render()
        if self.orders_menu.open:
            self.orders_menu.render()
        if self.market_board.open:
            self.market_board.render()
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
        if self.missions.farm.owned:
            visible += self.missions.orders.sprites(self.clock)
        if self.fair_outside.near(player.x, player.y):
            visible += self.fair_outside.sprites()
        farm_x, farm_y = self.world.farm.house
        if abs(farm_x - (camera_x + view_w / 2)) < view_w + 600 and abs(farm_y - (camera_y + view_h / 2)) < view_h + 600:
            visible += outdoor_sprites(self.world.farm, self.missions.farm)
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
        buyer = (self.missions.orders.buyer_near(player.x, player.y)
                 if walker and self.missions.farm.owned else None)
        if buyer:
            prompt = "E   Talk  ·  Farm buyer"
        elif giver and self.missions.is_locked(giver):
            prompt = f"Veteran  ·  {giver.region.title()} level {HARDER_LEVEL}"
        elif giver:
            prompt = f"E   Talk  ·  {TITLES[giver.type]}"
        elif center_region:
            won = self.missions.progress.races[center_region]
            prompt = ("E   Racing center  ·  Champion" if won >= CENTER_RACES
                      else f"E   Racing center  ·  Race {won + 1}/{CENTER_RACES}")
        elif walker and math.dist((walker.x, walker.y), self.world.general_store.door) <= STORE_DOOR_RANGE:
            prompt = "E   Enter the General Store"
        elif (walker and math.dist((walker.x, walker.y), HOME_DOOR) <= HOME_DOOR_RANGE
              and not walker.can_enter(car)):
            prompt = "E   Go inside"
        elif walker and self._factory_spot(walker):
            prompt = {"door": ("E   Mining Factory" if self.missions.factory.unlocked
                               else "E   Mining Factory  ·  Factory pass"),
                      "depot": "E   Biofuel depot", "dock": "E   Ship stones"}[self._factory_spot(walker)]
        elif (walker and math.dist((walker.x, walker.y), self.world.fair.door) <= FAIR_DOOR_RANGE
              and not walker.can_enter(car)):
            prompt = "E   Snow Fair  ·  1 ticket"
        elif (walker and math.dist((walker.x, walker.y), self.world.farm.door) <= FARM_DOOR_RANGE
              and not walker.can_enter(car)):
            prompt = self._farm_door_prompt()
        elif walker and self._plot_prompt(walker):
            prompt = self._plot_prompt(walker)
        elif walker and (self.fishing or self._fishing_prompt(walker)):
            prompt = self._fishing_prompt(walker)
        elif walker and walker.can_enter(car):
            prompt = "E   Get in"
        elif island:
            prompt = island
        elif walker and math.dist((walker.x, walker.y), (car.x, car.y)) > CALL_PROMPT_DISTANCE:
            prompt = "Q   Call car"
        self.hud.top_speed = TOP_SPEED * self.missions.progress.speed_scale(
            self.world.surface_at(player.x, player.y)) * (F1_SPEED if car.f1 else 1.0)
        mission = self.missions.status()
        if fishing and fishing.status()[0]:
            mission = fishing.status()
        label = "Highway" if self.world.on_highway(player.x, player.y) else region
        self.hud.render(car.speed, label, guide, prompt, show_speed=walker is None,
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
        self.hud.top_speed = TOP_SPEED * race.speed_scale * (F1_SPEED if race.car.f1 else 1.0)
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
