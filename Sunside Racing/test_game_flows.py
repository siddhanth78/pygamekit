"""Whole-game flows driven by real key events (needs an OpenGL window; skipped without one)."""

import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLKIT_ROOT = PROJECT_ROOT.parent
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import pygame

import main
import player_save
import world_save
from progression import mastery_to_next, mastery_to_reach, rating_speed
from missions import Offer
from walker import Walker
from fast_travel import on_island


class GameFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = folder = Path(cls.temp.name)
        cls.patches = (main.PlayerSave, main.WorldStore)
        main.PlayerSave = lambda: player_save.PlayerSave(folder / "player.json")
        main.WorldStore = lambda path: world_save.WorldStore(folder / "world.json")
        try:
            import moderngl
            pygame.init()
            for attr, value in ((pygame.GL_CONTEXT_MAJOR_VERSION, 3), (pygame.GL_CONTEXT_MINOR_VERSION, 3),
                                (pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)):
                pygame.display.gl_set_attribute(attr, value)
            pygame.display.set_mode((1280, 720), pygame.OPENGL | pygame.DOUBLEBUF | pygame.HIDDEN)
            cls.ctx = moderngl.create_context()
        except Exception as exc:  # No display or GL: nothing to drive.
            raise unittest.SkipTest(f"OpenGL window unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        main.PlayerSave, main.WorldStore = cls.patches
        pygame.quit()
        cls.temp.cleanup()

    def setUp(self):
        # Fresh saves for every test: autosaves from one test must not leak into the next.
        if (PROJECT_ROOT / "world.json").exists():
            shutil.copy(PROJECT_ROOT / "world.json", self.folder / "world.json")
        (self.folder / "player.json").write_text(json.dumps(
            {"version": 2, "mode": "drive", "car": {"x": 13600, "y": 16160, "heading": 0}}))
        self.game = main.Game(self.ctx)
        self.game.inputs.driving = lambda: (0, 0)

    def press(self, *keys):
        for key in keys:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="", scancode=0))
            pygame.event.post(pygame.event.Event(pygame.KEYUP, key=key, mod=0, unicode="", scancode=0))
            for action, value in self.game.inputs.handle_events():
                self.game.handle(action, value)
            self.game.update(1 / 60)
            self.game.render()

    def test_quitting_a_center_race_returns_to_the_center(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN)               # Accept race 1.
        self.assertIsNotNone(g.race)
        # Pause, move to QUIT RACE, choose it, move to QUIT, confirm, dismiss the result.
        self.press(pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN, pygame.K_UP, pygame.K_RETURN)
        self.assertIsNone(g.race)
        self.assertEqual(g.panel.title.text, "City Racing Center")
        self.press(pygame.K_RETURN)
        for _ in range(60):
            g.update(1 / 60)
        self.assertIsNone(g.race)                            # No surprise restart.
        self.assertEqual(g.missions.progress.races["city"], 0)
        self.assertLess(math.dist((g.walker.x, g.walker.y), (cx, cy)), 250)

    def test_center_offers_show_both_ratings(self):
        g = self.game
        g.missions.progress.add("city", mastery_to_reach(3))   # Level 3: rating 120.
        g.missions.progress.races["city"] = 3            # Race 4's rival is rated 140.
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e)
        self.assertEqual(g.panel.lines[1].text, "Mara Quill (140) VS You (120)")
        self.assertEqual(g.panel.buttons, ("ACCEPT",))    # Center races can't be declined.

    def test_left_and_right_pick_the_confirm_buttons(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN)
        self.assertEqual(g.panel.selected, 1)                     # KEEP RACING by default.
        self.press(pygame.K_LEFT)
        self.assertEqual(g.panel.selected, 0)                     # QUIT.
        self.press(pygame.K_LEFT)
        self.assertEqual(g.panel.selected, 0)                     # Stays at the left edge.
        self.press(pygame.K_d, pygame.K_a, pygame.K_RIGHT)        # A/D work too; back to KEEP RACING.
        self.assertEqual(g.panel.selected, 1)
        self.press(pygame.K_LEFT, pygame.K_RETURN)                # Quit.
        self.assertIsNone(g.race)
        self.assertEqual(g.panel.title.text, "City Racing Center")

    def test_declining_the_quit_keeps_racing(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN)
        self.press(pygame.K_ESCAPE)                           # Esc on the confirm = keep racing.
        self.assertIsNotNone(g.race)
        self.assertFalse(g.panel.open)

    def test_aborting_a_delivery_with_keys(self):
        g = self.game
        giver = g.missions.by_id["city-delivery"]
        g._step_out(Walker(giver.x + 20, giver.y))
        g.missions.offers[giver.id] = Offer(giver.id, "delivery", 1.0, (giver.x + 3000, giver.y))
        self.press(pygame.K_e, pygame.K_DOWN, pygame.K_UP, pygame.K_RETURN)  # Wiggle, then accept.
        self.assertIsNotNone(g.missions.active)
        self.press(pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN, pygame.K_UP, pygame.K_RETURN)
        self.assertIsNone(g.missions.active)
        self.assertIn(giver.id, g.missions.offers)
        self.assertEqual(g.panel.chip_name, "Failed")

    def test_a_lost_drag_race_fails_its_mission_at_once(self):
        g = self.game
        dg = g.missions.by_id["city-drag"]
        g._step_out(Walker(dg.x + 20, dg.y))
        g.missions.offers[dg.id] = Offer(dg.id, "drag", 1.25, None, {"kind": "straight", "theme": "city"}, 5)
        self.press(pygame.K_e, pygame.K_RETURN)
        while g.race and not g.race.result:          # Don't drive: the rival wins.
            g.update(1 / 60)
        self.assertIsNone(g.race)                     # No lingering "YOU LOSE" level.
        self.assertIsNone(g.missions.active)          # The mission is failed, not ongoing.
        self.assertEqual(g.panel.chip_name, "Failed")
        self.press(pygame.K_RETURN, pygame.K_ESCAPE)
        self.assertNotIn("abort", g.menu.items)
        self.assertNotIn("quit_race", g.menu.items)

    def test_a_drag_rival_keeps_its_rating_after_leveling(self):
        g = self.game
        dg = g.missions.by_id["city-drag"]
        g.missions.progress.add("city", mastery_to_reach(2))   # Level 2 (110) when offered.
        g.missions.offers[dg.id] = Offer(dg.id, "drag", 1.0, None, {"kind": "straight", "theme": "city"},
                                         5, dict(g.missions.progress.levels), 125)
        g.missions.progress.add("city", mastery_to_next(2))    # Level 3 (120) by the time it's accepted.
        g._step_out(Walker(dg.x + 20, dg.y))
        self.press(pygame.K_e)
        self.assertEqual(g.panel.lines[1].text, "Rival (125) VS You (120)")
        self.assertEqual(g.panel.chip_name, "Hard")     # A straight: no corners to cut.
        self.press(pygame.K_RETURN)
        # A car rated 125, less its random off-day for this attempt.
        self.assertAlmostEqual(g.race.rival.scale, rating_speed(125 - g.race.off_day))
        self.assertAlmostEqual(g.race.speed_scale, 1.08)           # The player is level 3.

    def test_declines_halve_the_reward_down_to_one_and_escape_keeps_the_offer(self):
        g = self.game
        dg = g.missions.by_id["city-drag"]
        g.missions.offers[dg.id] = Offer(dg.id, "drag", 1.0, None, {"kind": "straight", "theme": "city"},
                                         5, dict(g.missions.progress.levels), 112)
        g._step_out(Walker(dg.x + 20, dg.y))
        self.press(pygame.K_e, pygame.K_ESCAPE)          # Walking away keeps the offer.
        self.assertEqual(g.missions.offers[dg.id].rating, 112)
        g.missions.progress.add("city", mastery_to_reach(3))   # Level 3: a drag pays 5.
        self.press(pygame.K_e, pygame.K_RIGHT, pygame.K_RETURN)   # DECLINE: 5 -> 2.
        self.assertEqual(g.missions.offers[dg.id].declines, 1)
        self.assertTrue(g.panel.open)                     # The easier offer is shown at once.
        self.assertEqual(g.panel.chip_name, "Easy")
        self.assertIn("Win: 2 mastery", g.panel.lines[2].text)
        self.assertEqual(g.panel.buttons, ("ACCEPT", "DECLINE"))
        self.press(pygame.K_RIGHT, pygame.K_RETURN)       # DECLINE again: 2 -> 1.
        self.assertEqual(g.missions.offers[dg.id].declines, 2)
        self.assertIn("Win: 1 mastery", g.panel.lines[2].text)
        self.assertEqual(g.panel.buttons, ("ACCEPT",))    # At 1 mastery: no more declines.
        self.press(pygame.K_RIGHT, pygame.K_RETURN)       # Only ACCEPT is left.
        self.assertIsNotNone(g.race)

    def test_pausing_during_a_win_cannot_turn_it_into_a_loss(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN)
        g.race.times["player"], g.race.result = 60.0, "win"   # Just crossed the line first.
        self.press(pygame.K_ESCAPE)
        self.assertNotIn("quit_race", g.menu.items)
        self.press(pygame.K_ESCAPE)                            # Resume; the banner finishes.
        for _ in range(int(main.RACE_OVER_DELAY * 60) + 5):
            g.update(1 / 60)
        self.assertIsNone(g.race)
        self.assertEqual(g.missions.progress.races["city"], 1)

    def reload(self):
        """Save and start a fresh game from the saved files, as if relaunched."""
        self.game.save()
        self.game = main.Game(self.ctx)
        self.game.inputs.driving = lambda: (0, 0)
        return self.game

    def test_quitting_the_game_mid_mission_reopens_at_home(self):
        g = self.game
        giver = g.missions.by_id["city-delivery"]
        g._step_out(Walker(giver.x + 20, giver.y))
        g.missions.offers[giver.id] = Offer(giver.id, "delivery", 1.0, (giver.x + 3000, giver.y))
        self.press(pygame.K_e, pygame.K_RETURN)
        g.walker.x += 900                                  # Wander far off mid-delivery.
        g = self.reload()
        self.assertIsNone(g.missions.active)               # Quit, not resumed.
        self.assertIsNone(g.missions.status())
        self.assert_at_home(g)
        self.assertIn(giver.id, g.missions.offers)         # Same mission waits there.

    def test_quitting_the_game_mid_race_reopens_at_home(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN)
        for _ in range(240):
            g.update(1 / 60)
        g = self.reload()
        self.assertIsNone(g.race)
        self.assertEqual(g.missions.progress.races["city"], 0)
        self.assert_at_home(g)

    def assert_at_home(self, g):
        """Every launch: sitting in the car on the home driveway."""
        from world import HOME_PARK
        self.assertIsNone(g.walker)
        self.assertEqual((g.car.x, g.car.y, g.car.heading), HOME_PARK)
        self.assertEqual(g.state.player_record(g.player_id)[9], -HOME_PARK[2])  # Drawn as parked.
        self.assertFalse(g.inside)

    def test_autosave_mid_mission_saves_back_at_the_giver_without_interrupting(self):
        g = self.game
        giver = g.missions.by_id["city-speed"]
        g._step_out(Walker(giver.x + 20, giver.y))
        g.missions.offers[giver.id] = Offer(giver.id, "speed", 1.0, (giver.x + 3000, giver.y))
        self.press(pygame.K_e, pygame.K_RETURN)
        g.walker.x += 900
        g._autosave()
        g.world_store.wait()
        self.assertIsNotNone(g.missions.active)            # Still running in this session.
        self.assertGreater(math.dist((g.walker.x, g.walker.y), (giver.x, giver.y)), 800)
        saved = json.loads((self.folder / "player.json").read_text())
        self.assertEqual(saved["mode"], "walk")
        self.assertLess(math.dist((saved["walker"]["x"], saved["walker"]["y"]), (giver.x, giver.y)), 80)
        self.assertNotIn("queued", saved["missions"])

    # Title screen -------------------------------------------------------------------

    def title_with_keys(self, *keys):
        """Run the title loop on these key presses, failing if any game system loads."""
        for key in keys:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="", scancode=0))
            pygame.event.post(pygame.event.Event(pygame.KEYUP, key=key, mod=0, unicode="", scancode=0))
        def forbidden(*args, **kwargs):
            raise AssertionError("the game loaded behind the title screen")
        saved = main.World, main.Game, main.PlayerSave
        main.World = main.Game = main.PlayerSave = forbidden
        try:
            return main.run_title(self.ctx)
        finally:
            main.World, main.Game, main.PlayerSave = saved

    def test_title_play_with_arrows_loads_nothing_first(self):
        self.assertTrue(self.title_with_keys(pygame.K_DOWN, pygame.K_UP, pygame.K_ESCAPE, pygame.K_RETURN))

    def test_title_exit_with_arrows_or_w_s(self):
        self.assertFalse(self.title_with_keys(pygame.K_DOWN, pygame.K_RETURN))
        self.assertFalse(self.title_with_keys(pygame.K_s, pygame.K_SPACE))
        self.assertTrue(self.title_with_keys(pygame.K_s, pygame.K_w, pygame.K_SPACE))

    def test_title_mouse_and_scene(self):
        from title_menu import ROAD_HEADING, TitleMenu
        title = TitleMenu(self.ctx, main.PROJECT_ROOT, main.TOOLKIT_ROOT, main.WINDOW_SIZE)
        play, exit_ = title._button_centers()
        title.handle("pointer", exit_)
        self.assertEqual(title.selected, 1)
        self.assertEqual(title.handle("click", play), "play")
        car = [s for s in title.sprites if s.name == "racer_player"]
        self.assertEqual(len(car), 1)
        self.assertEqual(car[0].rotation, -ROAD_HEADING)                    # Tilted 45 degrees.
        self.assertEqual({s.rotation for s in title.sprites if s.atlas == "road-atlas"}, {-ROAD_HEADING})
        self.assertNotIn("street_lamp", {s.name for s in title.sprites})
        oncoming = [s for s in title.sprites if s.name.startswith("traffic_")]
        self.assertEqual({s.rotation for s in oncoming}, {-ROAD_HEADING - 180})    # Other lane.
        self.assertGreater(math.dist((oncoming[0].x, oncoming[0].y), (oncoming[1].x, oncoming[1].y)), 256)
        self.assertEqual(len([s for s in title.sprites if s.atlas == "prop-atlas"]), 2)   # Trees.
        title.render()

    # Home -------------------------------------------------------------------------------

    def walk_to(self, x, y):
        self.game.walker.x, self.game.walker.y = x, y

    def test_opens_at_home_and_the_house_goes_in_and_out(self):
        from world import HOME_DOOR
        g = self.game
        self.assert_at_home(g)                                      # Launch: in the car.
        self.press(pygame.K_e)                                      # Get out on the driveway.
        self.assertIsNotNone(g.walker)
        self.walk_to(*HOME_DOOR)
        g.render()
        self.assertEqual(g.hud.prompt.text, "E   Go inside")
        self.press(pygame.K_e)
        self.assertTrue(g.inside)
        g.render()
        self.assertEqual(g.hud.prompt.text, "E   Go outside")        # Standing by the front door.
        traffic_before = [(c.x, c.y) for c in g.traffic.cars[:5]] if hasattr(g.traffic, "cars") else []
        g.inputs.walking = lambda: (0, 1, False)                    # Walk in a little.
        for _ in range(30):
            g.update(1 / 60)
        g.inputs.walking = lambda: (0, 0, False)
        self.assertGreater(g.walker.y, g.home.entry[1])
        if traffic_before:                                          # The city waits outside.
            self.assertEqual([(c.x, c.y) for c in g.traffic.cars[:5]], traffic_before)
        self.walk_to(*g.home.entry)
        self.press(pygame.K_e)                                      # Front door: back out.
        self.assertFalse(g.inside)
        self.assertEqual((g.walker.x, g.walker.y), HOME_DOOR)

    def enter_home(self):
        from world import HOME_DOOR
        g = self.game
        self.press(pygame.K_e)
        self.walk_to(*HOME_DOOR)
        self.press(pygame.K_e)
        self.assertTrue(g.inside)
        return g

    def spot(self, g, kind, label=None):
        return next(s for s in g.home.spots if s.kind == kind and (label is None or s.label == label))

    def test_sit_sleep_lamp_and_tv(self):
        g = self.enter_home()
        couch = self.spot(g, "sit", "Sit on the couch")
        self.walk_to(couch.x, couch.y)
        self.assertEqual(g._home_prompt(), "E   Sit on the couch")
        self.press(pygame.K_e)
        self.assertIs(g.resting, couch)
        self.assertEqual((g.walker.x, g.walker.y), (couch.px, couch.py))
        tile = g.state._tile("people-atlas", "player_sit")
        self.assertEqual(g.state.entities[g.walker_id]["record"][10:12], tile)
        g.inputs.walking = lambda: (1, 0, False)                    # Moving gets up.
        g.update(1 / 60)
        g.inputs.walking = lambda: (0, 0, False)
        self.assertIsNone(g.resting)
        bed = self.spot(g, "sleep")
        self.walk_to(bed.x, bed.y)
        self.press(pygame.K_e)
        self.assertIs(g.resting, bed)
        g.render()
        self.assertEqual(g.hud.mission_title.text, "Sleeping...")
        self.press(pygame.K_e)                                      # E also gets up.
        self.assertIsNone(g.resting)
        lamp = self.spot(g, "lamp", "Lamp")
        self.walk_to(lamp.x, lamp.y)
        self.assertEqual(g._home_prompt(), "E   Turn off the lamp")
        self.press(pygame.K_e)
        self.assertFalse(g.home.lamps["living"])
        tv = self.spot(g, "look", "TV")
        self.walk_to(tv.x, tv.y)
        self.press(pygame.K_e)
        self.assertEqual(g.panel.lines[0].text, "The TV is off.")

    def test_arcade_plays_lane_dodge_and_keeps_the_best_score(self):
        g = self.enter_home()
        cab = self.spot(g, "arcade")
        self.walk_to(cab.x, cab.y)
        self.press(pygame.K_e)
        self.assertTrue(g.arcade.open)
        self.press(pygame.K_DOWN, pygame.K_RETURN)                  # Game 2 is locked.
        self.assertEqual(g.arcade.mode, "menu")
        self.press(pygame.K_UP, pygame.K_RETURN)                    # Lane Dodge.
        self.assertEqual(g.arcade.mode, "play")
        self.press(pygame.K_LEFT)
        self.assertEqual(g.arcade.game.lane, 0)
        for _ in range(60 * 60):                                    # Sit still until a crash.
            g.update(1 / 60)
            if g.arcade.game.over:
                break
        g.render()
        score = g.arcade.game.score
        self.assertTrue(g.arcade.game.over)
        self.assertEqual(g.missions.arcade["lane_dodge"], score)
        self.press(pygame.K_ESCAPE, pygame.K_ESCAPE)                # Games list, then walk away.
        self.assertFalse(g.arcade.open)
        g._autosave()
        g.world_store.wait()
        saved = json.loads((self.folder / "player.json").read_text())["missions"]
        self.assertEqual(saved["arcade"]["lane_dodge"], score)

    def test_go_home_from_the_pause_menu(self):
        g = self.game
        g.car.x, g.car.y = 13600, 16160                             # Somewhere else in the city.
        self.press(pygame.K_ESCAPE)
        self.assertIn("home", g.menu.items)
        g.menu.selected = g.menu.items.index("home")
        self.press(pygame.K_RETURN)
        self.assertFalse(g.menu.open)
        self.assert_at_home(g)

    def test_mastery_completed_splits_into_del_time_drag(self):
        g = self.game
        g.missions.progress.completed["city"].update(delivery=12, speed=3, drag=7)
        self.press(pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN)      # Pause > Mastery.
        self.assertEqual(g.menu.page, "mastery")
        self.assertEqual([h.text for h in g.menu.sub_headers], ["Del", "Time", "Drag"])
        self.assertEqual([c.text for c in g.menu.done_cells[0]], ["12", "3", "7"])      # City row.
        self.assertEqual([c.text for c in g.menu.done_cells[5]], ["", "", ""])          # Beach row.
        self.assertEqual(g.menu.cells[0][5].text, "")

    # Inventory ----------------------------------------------------------------------

    def test_inventory_opens_with_i_moves_with_arrows_and_closes(self):
        g = self.game
        g.missions.fish.add("uncommon")
        g.missions.unspent = 4
        g.inputs.driving = lambda: (1, 0)
        start = (g.car.x, g.car.y)
        self.press(pygame.K_i)
        self.assertTrue(g.inventory.open)
        self.assertEqual([(i.id, n) for i, n in g.inventory.stacks],
                         [("sunside_tokens", 0), ("fish_uncommon", 1), ("mastery_points", 4)])
        self.assertEqual((g.car.x, g.car.y), start)                 # Paused while open.
        self.assertEqual(g.inventory.name.text, "Sunside Tokens")    # Always slot one.
        self.assertEqual(g.inventory.amount.text, "0 / 999,999")
        self.press(pygame.K_RIGHT)
        self.assertEqual(g.inventory.name.text, "Uncommon fish")
        self.press(pygame.K_RIGHT)
        self.assertEqual(g.inventory.name.text, "Mastery points")
        self.assertEqual(g.inventory.amount.text, "4 / 999")
        self.press(pygame.K_DOWN)
        self.assertEqual(g.inventory.name.text, "Empty slot")
        for _ in range(5):
            self.press(pygame.K_DOWN, pygame.K_RIGHT)               # Stays inside the grid.
        self.assertEqual(g.inventory.selected, 27)                  # 7 x 4.
        x, y = g.inventory.slot_centers()[0]
        g.handle("pointer", (x, y))
        self.assertEqual(g.inventory.selected, 0)
        self.press(pygame.K_i)
        self.assertFalse(g.inventory.open)
        self.press(pygame.K_i, pygame.K_ESCAPE)                     # Esc closes it too,
        self.assertFalse(g.inventory.open)
        self.assertFalse(g.menu.open)                               # without pausing.

    def test_trader_says_so_when_points_are_full(self):
        g = self.game
        self.unlock_fishing()
        g.missions.unspent = 999
        g.missions.fish.add("common")
        self.at_trader()
        self.press(pygame.K_e)
        self.assertIn("full", g.panel.lines[0].text)
        self.assertEqual(g.missions.fish.count, 1)
        self.assertFalse(g.spend_menu.open)

    # General Store ------------------------------------------------------------------

    def enter_store(self):
        g = self.game
        self.press(pygame.K_e)                                       # Out of the car,
        g.walker.x, g.walker.y = g.world.general_store.door           # to the store's door.
        g.render()
        self.assertEqual(g.hud.prompt.text, "E   Enter the General Store")
        self.press(pygame.K_e)
        self.assertTrue(g.in_store)
        return g

    def at_spot(self, g, kind, item=""):
        spot = next(s for s in g.store.spots if s.kind == kind and s.item == item)
        g.walker.x, g.walker.y = spot.stand

    def test_shopping_pay_at_the_cashier(self):
        g = self.enter_store()
        g.missions.add_item("sunside_tokens", 100)
        self.at_spot(g, "product", "seeds_corn")
        self.assertEqual(g._store_prompt(), "E   Corn seeds  ·  10 S")
        self.press(pygame.K_e, pygame.K_e)                            # Two packets.
        self.at_spot(g, "product", "super_fertilizer")
        self.press(pygame.K_e)
        self.assertEqual((g.store.cart.count, g.store.cart.total), (3, 70))
        g.render()
        self.assertEqual(g.hud.mission_title.text, "CART  ·  3 items")
        self.at_spot(g, "cashier")
        self.press(pygame.K_e)                                        # An itemized bill.
        self.assertEqual(g.panel.buttons, ("PAY", "NOT YET"))
        self.assertEqual([(g.panel.bill_left[i].text, g.panel.bill_right[i].text) for i in range(2)],
                         [("Corn seeds  2 x 10 S", "20 S"), ("Super fertilizer  1 x 50 S", "50 S")])
        self.assertEqual((g.panel.bill_left[-1].text, g.panel.bill_right[-1].text), ("TOTAL", "70 S"))
        self.press(pygame.K_RETURN)                                   # PAY.
        self.assertEqual(g.store.cart.count, 0)
        self.assertEqual((g.missions.tokens, g.missions.items["seeds_corn"],
                          g.missions.items["super_fertilizer"]), (30, 2, 1))
        self.assertEqual((g.panel.chip_name, g.panel.title.text), ("Success", "Receipt"))
        self.assertEqual(g.panel.bill_right[-1].text, "70 S  PAID")
        self.press(pygame.K_RETURN)
        self.at_spot(g, "door")
        self.press(pygame.K_e)                                        # Empty cart: out.
        self.assertFalse(g.in_store)
        self.assertEqual((g.walker.x, g.walker.y), g.world.general_store.door)

    def test_cant_leave_with_an_unpaid_cart(self):
        g = self.enter_store()
        self.at_spot(g, "product", "island_pass")
        self.press(pygame.K_e)
        self.at_spot(g, "cashier")
        self.press(pygame.K_e, pygame.K_RETURN)                       # PAY with 0 tokens.
        self.assertIn("Not enough", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        self.assertEqual(g.store.cart.count, 1)
        self.at_spot(g, "door")
        self.press(pygame.K_e)
        self.assertEqual(g.panel.title.text, "Leaving?")
        self.press(pygame.K_RETURN)                                   # STAY (selected first).
        self.assertTrue(g.in_store)
        self.assertEqual(g.store.cart.count, 1)
        self.press(pygame.K_e, pygame.K_LEFT, pygame.K_RETURN)        # EMPTY CART.
        self.assertFalse(g.in_store)
        self.assertEqual(g.store.cart.count, 0)
        self.assertEqual(g.missions.items.get("island_pass", 0), 0)

    def test_going_home_from_the_store_puts_the_cart_back(self):
        g = self.enter_store()
        self.at_spot(g, "product", "fair_ticket")
        self.press(pygame.K_e)
        g.menu.toggle()
        g.menu.selected = g.menu.items.index("home")
        self.press(pygame.K_RETURN)
        self.assertFalse(g.in_store)
        self.assertEqual(g.store.cart.count, 0)
        self.assert_at_home(g)

    # Veterans -----------------------------------------------------------------------

    def test_locked_veterans_show_their_badge_and_explain(self):
        g = self.game
        vet = g.missions.by_id["snow-drag-hard"]
        badges = [s for s in g.missions.sprites(0.0) if s.name == "icon_drag_hard"]
        self.assertTrue(any(abs(s.x - vet.x) < 1 for s in badges))    # Badge while locked.
        g._step_out(Walker(vet.x + 20, vet.y))
        self.assertIs(g.missions.giver_near(g.walker.x, g.walker.y), vet)
        self.press(pygame.K_e)
        self.assertEqual(g.panel.title.text, vet.name)
        self.assertEqual(g.panel.chip_name, "Locked")
        self.assertIn("level 5", g.panel.lines[0].text)
        self.assertIsNone(g.pending_offer)
        self.press(pygame.K_RETURN)
        g.missions.progress.levels["snow"] = 5                        # Unlocked: an offer.
        self.press(pygame.K_e)
        self.assertIs(g.pending_offer, vet)

    # World map ----------------------------------------------------------------------

    def test_no_guide_until_a_landmark_is_picked_on_the_map(self):
        g = self.game
        self.assertIsNone(g.guide_to)
        drawn = []
        g.arrow.render = lambda *args: drawn.append(args)
        g.render()
        self.assertEqual(drawn, [])                                   # No arrow at launch.
        self.press(pygame.K_m)
        self.assertTrue(g.world_map.open)
        start = (g.car.x, g.car.y)
        g.inputs.driving = lambda: (1, 0)
        for _ in range(20):
            g.update(1 / 60)
        self.assertEqual((g.car.x, g.car.y), start)                   # Paused while open.
        dock = next(m for m in g.landmarks if m.kind == "dock")
        g.handle("click", g.world_map.to_screen(dock.x, dock.y))
        self.assertEqual(g.guide_to, dock)
        self.press(pygame.K_m)                                        # Close.
        self.assertFalse(g.world_map.open)
        g.render()
        self.assertEqual(drawn[-1][:2], (dock.x, dock.y))
        self.assertEqual(drawn[-1][-1], (242, 150, 60))              # Piers: orange, like the diamond.
        self.press(pygame.K_m, pygame.K_c, pygame.K_ESCAPE)          # Clear, then Esc closes.
        self.assertIsNone(g.guide_to)
        self.assertFalse(g.world_map.open)
        self.assertFalse(g.menu.open)
        count = len(drawn)
        g.render()
        self.assertEqual(len(drawn), count)                           # Arrow gone again.

    def test_map_arrow_colors_and_landmarks(self):
        from world_map import KINDS
        g = self.game
        kinds = [m.kind for m in g.landmarks]
        self.assertEqual(kinds.count("center"), 5)                   # The island's waits for the ferry.
        self.assertEqual(kinds.count("dock"), 3)
        self.assertEqual(kinds.count("camp"), sum(r == "jungle" for r in g.world.camps.values()))
        self.assertEqual({k: v[0] for k, v in KINDS.items()},
                         {"center": (212, 80, 66), "dock": (242, 150, 60), "camp": (236, 120, 170),
                          "veteran": (150, 226, 140), "store": (160, 96, 220), "farm": (176, 122, 72),
                          "buyer": (232, 196, 120), "fair": (110, 200, 236),
                          "factory": (170, 180, 196), "ferry": (90, 200, 200), "club": (255, 150, 90)})
        self.assertEqual(kinds.count("store"), 1)
        self.assertEqual(kinds.count("farm"), 1)
        self.assertEqual(kinds.count("buyer"), 0)                    # Until the farm is owned.
        self.assertEqual(kinds.count("veteran"), 15)
        for mark in g.landmarks:                                      # Arrow matches the diamond.
            self.assertEqual(mark.arrow_color, KINDS[mark.kind][0])
        g.world_map.toggle()
        camp = next(m for m in g.landmarks if m.kind == "camp")
        g.handle("pointer", g.world_map.to_screen(camp.x, camp.y))
        self.assertEqual(g.world_map.hovered, camp)
        g.render()                                                    # Map draws.
        for mark in g.landmarks:                                      # Every diamond is pickable,
            g.handle("click", g.world_map.to_screen(mark.x, mark.y))  # even where they overlap.
            self.assertEqual(g.guide_to, mark)

    def test_a_mission_target_still_gets_the_yellow_arrow(self):
        from hud import FILL
        g = self.game
        g.guide_to = next(m for m in g.landmarks if m.kind == "center")
        giver = g.missions.by_id["city-delivery"]
        g._step_out(Walker(giver.x + 20, giver.y))
        g.missions.offers[giver.id] = Offer(giver.id, "delivery", 1.0, (giver.x + 3000, giver.y))
        self.press(pygame.K_e, pygame.K_RETURN)
        drawn = []
        g.arrow.render = lambda *args: drawn.append(args)
        g.render()
        self.assertEqual(drawn[-1][:2], (giver.x + 3000, giver.y))
        self.assertEqual(drawn[-1][-1], FILL)

    def test_no_map_during_races(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN)
        self.assertIsNotNone(g.race)
        self.press(pygame.K_m)
        self.assertFalse(g.world_map.open)

    # Zoom ---------------------------------------------------------------------------

    def test_eased_zoom_lands_exactly_and_only_moves_toward_the_target(self):
        values = [main.eased_zoom(1.0, 2.0, i / 60) for i in range(40)]
        self.assertEqual(values[0], 1.0)
        self.assertEqual(values[18], 2.0)                          # 0.3 s at 60 fps.
        self.assertTrue(all(b >= a for a, b in zip(values, values[1:])))
        self.assertTrue(all(1.0 < v < 2.0 for v in values[1:18]))
        self.assertEqual(main.eased_zoom(2.0, 1.0, 5.0), 1.0)

    def test_getting_out_and_in_zooms_in_a_third_of_a_second(self):
        g = self.game
        self.assertEqual(g.zoom, main.DRIVE_ZOOM)
        self.press(pygame.K_e)                                     # Out (one frame runs).
        self.assertIsNotNone(g.walker)
        frames = 1
        while g.zoom != main.WALK_ZOOM:
            g.update(1 / 60)
            frames += 1
        self.assertLessEqual(frames, round(main.ZOOM_TIME * 60) + 1)
        for _ in range(5):                                         # Settled: no creeping.
            g.update(1 / 60)
            self.assertEqual(g.zoom, main.WALK_ZOOM)
        self.press(pygame.K_e)                                     # Back in.
        self.assertIsNone(g.walker)
        for _ in range(3):
            g.update(1 / 60)
        midway = g.zoom
        self.assertLess(midway, main.WALK_ZOOM)
        self.press(pygame.K_e)                                     # Out again mid-zoom:
        self.assertIsNotNone(g.walker)
        self.assertGreater(g.zoom, midway - 0.2)                   # turns around, no jump.
        for _ in range(20):
            g.update(1 / 60)
        self.assertEqual(g.zoom, main.WALK_ZOOM)

    def test_travel_snaps_the_zoom_even_mid_ease(self):
        g = self.game
        self.press(pygame.K_e)
        g.update(1 / 60)
        self.assertTrue(1.0 < g.zoom < 2.0)
        g.missions.progress.add("rural", mastery_to_reach(4))
        g.menu.toggle()
        g._fast_travel("rural")
        self.assertEqual(g.zoom, main.DRIVE_ZOOM)
        g.update(1 / 60)
        self.assertEqual(g.zoom, main.DRIVE_ZOOM)

    # Fishing ------------------------------------------------------------------------

    def at_pier_end(self):
        dock = self.game.world.docks[0]
        ex, ey = dock.end
        self.game._step_out(Walker((ex + 0.5) * 64, (ey + 0.5) * 64))
        return dock

    def at_trader(self):
        from fishing import trader_spots
        x, y, _ = trader_spots(self.game.world)[0]
        self.game._step_out(Walker(x + 20, y))

    def unlock_fishing(self):
        self.game.missions.progress.add("rural", mastery_to_reach(4))   # Level 4 in any region.

    def test_locked_piers_and_traders_say_level_four(self):
        g = self.game
        self.at_pier_end()
        self.assertEqual(g._fishing_prompt(g.walker), "Needs any region at level 4")
        self.press(pygame.K_e)
        self.assertIsNone(g.fishing)
        self.assertEqual(g.panel.title.text, "Pier 1  ·  West")
        self.assertEqual(g.panel.lines[0].text, "Needs any region at level 4 to fish here.")
        self.press(pygame.K_RETURN)
        g.missions.fish.add("rare")
        self.at_trader()
        self.assertEqual(g._fishing_prompt(g.walker), "Trading opens at level 4")
        self.press(pygame.K_e)
        self.assertEqual(g.panel.title.text, "Fish trader")
        self.assertEqual(g.missions.fish.count, 1)                 # No trade below level 4.
        self.assertFalse(g.spend_menu.open)

    def test_catching_a_fish_with_keys(self):
        from fishing import SWEEP
        g = self.game
        self.unlock_fishing()
        dock = self.at_pier_end()
        self.assertEqual(g._fishing_prompt(g.walker), "E   Fish")
        self.press(pygame.K_e)
        self.assertIsNotNone(g.fishing)
        self.assertEqual(g.walker.heading, dock.heading)
        session = g.fishing
        requests = []
        g.autosave.request = lambda: requests.append(session.phase)
        for _ in range(20):
            while session.phase != "strike":
                g.update(1 / 60)
            self.assertEqual(g._fishing_prompt(g.walker), "SPACE   Strike!")
            session.sweep_t = sum(session.zone) / 2 / SWEEP[session.rarity]
            self.press(pygame.K_SPACE)
            if session.phase in ("reel", "caught"):
                break
        while session.phase != "caught":
            g.update(1 / 60)
        g.render()
        self.assertEqual(g.missions.fish.count, 1)
        self.assertEqual(requests, ["caught"])                   # Saved as it lands.
        self.assertEqual(g._fishing_prompt(g.walker), "E   Cast again")
        self.press(pygame.K_e)
        self.assertEqual(session.phase, "cast")
        g.inputs.walking = lambda: (0, 1, False)                  # Walking off ends it.
        g.update(1 / 60)
        self.assertIsNone(g.fishing)

    def test_trading_then_spending_points_with_keys_and_later_from_mastery(self):
        g = self.game
        self.unlock_fishing()
        g.missions.fish.add("epic")
        g.missions.fish.add("epic")
        self.at_trader()
        self.assertEqual(g._fishing_prompt(g.walker), "E   Trade 2 fish")
        self.press(pygame.K_e)
        # The points go to the inventory; the trader says so, and no spend menu opens.
        self.assertFalse(g.spend_menu.open)
        self.assertEqual(g.panel.lines[0].text, "Traded 2 fish for 10 mastery points.")
        self.assertIn("inventory (I)", g.panel.lines[1].text)
        self.assertEqual((g.missions.unspent, g.missions.fish.count), (10, 0))
        self.press(pygame.K_RETURN)
        self.assertFalse(g.panel.open)
        # I, then pick Mastery points in the grid: the spend menu opens.
        self.press(pygame.K_i)
        slot = [item.id for item, _ in g.inventory.stacks].index("mastery_points")
        self.press(*[pygame.K_RIGHT] * (slot % 4), *[pygame.K_DOWN] * (slot // 4), pygame.K_RETURN)
        self.assertFalse(g.inventory.open)
        self.assertTrue(g.spend_menu.open)
        # City +1 starts selected; Down to Jungle +1, Right to Jungle +5, spend it.
        self.press(pygame.K_DOWN, pygame.K_RIGHT, pygame.K_RETURN)
        self.assertEqual((g.missions.progress.mastery["jungle"], g.missions.unspent), (5, 5))
        self.press(pygame.K_ESCAPE)                               # Keep the other 5.
        self.assertFalse(g.spend_menu.open)
        self.assertEqual(g.missions.unspent, 5)
        self.press(pygame.K_i)                                    # Still in the inventory.
        self.assertIn(("mastery_points", 5), [(item.id, n) for item, n in g.inventory.stacks])
        self.press(pygame.K_i)
        # Pause > Mastery; Back starts selected and SPEND POINTS sits just before it.
        self.press(pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN)
        self.assertEqual(g.menu.page, "mastery")
        self.assertIn("spend", g.menu.items)
        self.assertEqual(g.menu.available.text, "Available points: 5")
        self.press(pygame.K_UP, pygame.K_RETURN)
        self.assertTrue(g.spend_menu.open)
        self.assertFalse(g.menu.open)
        self.press(pygame.K_SPACE)                                # City +1.
        self.assertEqual((g.missions.progress.mastery["city"], g.missions.unspent), (1, 4))
        g._autosave()
        g.world_store.wait()
        saved = json.loads((self.folder / "player.json").read_text())["missions"]
        self.assertEqual(saved["unspent_mastery"], 4)
        self.assertEqual(sum(saved["fish"]["bag"].values()), 0)
        self.assertEqual(saved["fish"]["caught"]["epic"], 2)

    def test_farm_claim_feed_animals_and_grow_crops_with_keys(self):
        from farm import FARM_LEVEL
        g = self.game
        farm = g.world.farm
        g._step_out(Walker(*farm.door))
        self.assertEqual(g._farm_door_prompt(), "E   Abandoned farmhouse")
        self.press(pygame.K_e)                                        # Locked: says why.
        self.assertEqual((g.panel.chip_name, g.missions.farm.owned), ("Locked", False))
        self.press(pygame.K_RETURN)
        g.missions.progress.add("rural", mastery_to_reach(FARM_LEVEL))
        self.assertEqual(g._farm_door_prompt(), "E   Claim the farmhouse")
        self.press(pygame.K_e)
        self.assertTrue(g.missions.farm.owned)
        self.assertEqual((g.missions.tokens, g.panel.lines[1].text),
                         (500, "+500 Sunside Tokens to get you started."))
        self.press(pygame.K_RETURN, pygame.K_e)                       # Now E goes inside.
        self.assertTrue(g.in_farm)
        self.assertEqual(g.missions.tokens, 500)                      # Paid once.
        # Feed Daisy: without feed the panel says so; with it, milk.
        cow = next(s for s in g.farmhouse.spots if s.kind == "cow")
        g.walker.x, g.walker.y = cow.x, cow.y
        self.press(pygame.K_e)
        self.assertIn("cow feed", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        g.missions.add_item("cow_feed", 2)
        self.press(pygame.K_e, pygame.K_e)
        self.assertEqual((g.missions.items.get("cow_feed", 0), g.missions.items["milk"]), (0, 2))
        g.missions.add_item("hen_feed", 1)
        hen = g.farmhouse.hens[0]
        g.walker.x, g.walker.y = hen.x, hen.y
        self.press(pygame.K_e)
        self.assertEqual(g.missions.items["eggs"], 1)
        door = next(s for s in g.farmhouse.spots if s.kind == "door")
        g.walker.x, g.walker.y = door.x, door.y
        self.press(pygame.K_e)
        self.assertFalse(g.in_farm)
        self.assertEqual((g.walker.x, g.walker.y), farm.door)
        # The plot: two kinds of seed ask which one; E fertilizes, then harvests.
        g.missions.add_item("seeds_corn", 1)
        g.missions.add_item("seeds_tomato", 1)
        g.missions.add_item("super_fertilizer", 1)
        tx, ty = farm.plot_tiles()[0]
        g.walker.x, g.walker.y = (tx + 0.5) * 64, (ty + 0.5) * 64
        self.assertEqual(g._plot_prompt(g.walker), "E   Plant a seed")
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("CORN", "TOMATO"))
        self.press(pygame.K_RIGHT, pygame.K_RETURN)
        self.assertEqual(g.missions.farm.plot[0], ["tomato", "sprout"])
        self.assertEqual(g.missions.items.get("seeds_tomato", 0), 0)
        self.press(pygame.K_e)
        self.assertEqual(g.missions.farm.plot[0], ["tomato", "ripe"])
        from farm import outdoor_sprites
        self.assertEqual([s.name for s in outdoor_sprites(farm, g.missions.farm)], ["plant_tomato"])
        self.assertEqual(g._plot_prompt(g.walker), "E   Harvest  ·  Tomato")
        self.press(pygame.K_e)
        self.assertEqual((g.missions.farm.plot[0], g.missions.items["tomato"]), (None, 1))
        g._autosave()
        g.world_store.wait()
        saved = json.loads((self.folder / "player.json").read_text())["missions"]
        self.assertTrue(saved["farm"]["owned"])
        self.assertEqual((saved["inventory"]["tomato"], saved["inventory"]["milk"]), (1, 2))

    def test_farm_buyer_order_taken_then_delivered_with_keys(self):
        from farm import FARM_LEVEL
        g = self.game
        g.missions.progress.add("rural", mastery_to_reach(FARM_LEVEL))
        g._step_out(Walker(*g.world.farm.door))
        self.press(pygame.K_e, pygame.K_RETURN)                       # Claim the farm.
        self.assertEqual([m.kind for m in g.landmarks].count("buyer"), 5)
        orders = g.missions.orders
        order = orders.orders["city"]
        g.walker.x, g.walker.y = order.x, order.y + 20
        g.collisions.fixed = []
        self.press(pygame.K_e)                                        # Take the order.
        self.assertEqual(g.panel.buttons, ("TAKE ORDER", "NOT NOW"))
        self.press(pygame.K_RETURN)
        self.assertEqual(orders.active, "city")
        self.assertEqual((g.guide_to.kind, (g.guide_to.x, g.guide_to.y)), ("buyer", (order.x, order.y)))
        self.press(pygame.K_e)                                        # Nothing yet: still waiting.
        self.assertTrue(g.panel.lines[0].text.startswith("Still waiting for"))
        self.press(pygame.K_RETURN)
        for good, n in order.goods.items():
            g.missions.add_item(good, n)
        tokens, points = g.missions.tokens, g.missions.unspent
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("DELIVER", "NOT NOW"))
        self.press(pygame.K_RETURN)
        self.assertEqual(g.panel.chip_name, "Success")
        if order.pay == "tokens":
            self.assertEqual(g.missions.tokens, tokens + order.value)
        else:
            self.assertEqual(g.missions.unspent, points + order.points)
        self.assertTrue(all(g.missions.items.get(good, 0) == 0 for good in order.goods))
        self.assertIsNone(orders.active)
        self.assertIsNone(g.guide_to)                                 # That buyer has gone.
        new = orders.orders["city"]
        self.assertNotEqual((new.x, new.y), (order.x, order.y))
        self.assertEqual(g.world.region_at(new.x, new.y), "city")
        self.assertIn((new.x, new.y), [(m.x, m.y) for m in g.landmarks if m.kind == "buyer"])

    def test_orders_view_from_anywhere(self):
        from farm import FARM_LEVEL
        g = self.game
        self.press(pygame.K_o)                                        # Before the farm: none.
        self.assertTrue(g.orders_menu.open)
        self.assertEqual(g.orders_menu.rows, [])
        self.assertIn("rural level 6", g.orders_menu.empty)
        self.press(pygame.K_o)
        self.assertFalse(g.orders_menu.open)
        g.missions.progress.add("rural", mastery_to_reach(FARM_LEVEL))
        g.missions.farm.claim(g.missions)
        g._refresh_landmarks()
        self.press(pygame.K_o)                                        # Five orders, nearest first.
        rows = g.orders_menu.rows
        self.assertEqual(len(rows), 5)
        here = g._where()
        near = [math.dist(here, (g.missions.orders.orders[r["region"]].x,
                                 g.missions.orders.orders[r["region"]].y)) for r in rows]
        self.assertEqual(near, sorted(near))
        self.press(pygame.K_DOWN, pygame.K_RETURN)                    # Work on the second.
        region = rows[1]["region"]
        self.assertFalse(g.orders_menu.open)
        self.assertEqual(g.missions.orders.active, region)
        self.assertEqual(g.guide_to.kind, "buyer")
        # O works from inside buildings too, e.g. the store.
        g._step_out(Walker(*g.world.general_store.door))
        g._enter_store()
        self.press(pygame.K_o)
        self.assertEqual([r["active"] for r in g.orders_menu.rows].count(True), 1)
        self.assertTrue(g.orders_menu.rows[g.orders_menu.selected]["active"])  # Starts on it.
        self.press(pygame.K_ESCAPE)
        self.assertFalse(g.orders_menu.open)
        self.assertFalse(g.menu.open)                                  # ESC only closed the orders.

    def test_orders_wait_while_a_mission_runs(self):
        from farm import FARM_LEVEL
        g = self.game
        g.missions.progress.add("rural", mastery_to_reach(FARM_LEVEL))
        g.missions.farm.claim(g.missions)
        g._refresh_landmarks()
        g.missions.orders.take("city")
        g.missions.add_item("seeds_corn", 1)
        g._accept(g.missions.by_id["city-delivery"])                 # A mission starts:
        self.press(pygame.K_o)                                        # O says to finish first,
        self.assertFalse(g.orders_menu.open)
        self.assertIn("Finish your current mission", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        order = g.missions.orders.orders["city"]                      # buyers wait,
        g._step_out(Walker(order.x, order.y + 20))
        g.collisions.fixed = []
        self.press(pygame.K_e)
        self.assertIn("Finish your current mission", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        tx, ty = g.world.farm.plot_tiles()[0]                         # and the plot ignores E.
        g.walker.x, g.walker.y = (tx + 0.5) * 64, (ty + 0.5) * 64
        self.assertEqual(g._plot_prompt(g.walker), "")
        self.press(pygame.K_e)
        self.assertIsNone(g.missions.farm.plot[0])
        self.assertEqual(g.missions.items["seeds_corn"], 1)
        g.missions.abort()                                            # Mission over: all back.
        self.assertEqual(g._plot_prompt(g.walker), "E   Plant a seed")
        self.press(pygame.K_o)
        self.assertTrue(g.orders_menu.open)

    def test_snow_fair_tickets_a_booth_a_ride_and_the_grand_prize(self):
        from fair_games import GAMES
        g = self.game
        site = g.world.fair
        g._step_out(Walker(*site.door))
        g.collisions.fixed = []
        self.press(pygame.K_e)                                        # No ticket: says where to buy.
        self.assertIn("1 fair ticket", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        g.missions.add_item("fair_ticket", 2)
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("GO IN", "NOT NOW"))
        self.press(pygame.K_RETURN)
        self.assertTrue(g.at_fair)
        self.assertEqual(g.missions.items["fair_ticket"], 1)          # Entry only.
        # Game tickets come from the counter inside: 50 S each, in 1, 5, or 10.
        counter = next(s for s in g.fair_level.spots if s.kind == "counter")
        g.walker.x, g.walker.y = counter.x, counter.y
        self.assertEqual(g._fair_prompt(), "E   Game tickets  ·  50 S each")
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("BUY 1", "BUY 5", "BUY 10"))
        self.press(pygame.K_RIGHT, pygame.K_RETURN)                   # BUY 5 with no money:
        self.assertIn("don't have enough", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        g.missions.add_item("sunside_tokens", 150)
        self.press(pygame.K_e, pygame.K_RETURN)                       # BUY 1,
        self.press(pygame.K_RETURN, pygame.K_e, pygame.K_RIGHT, pygame.K_RIGHT, pygame.K_RETURN)   # not 10,
        self.assertIn("don't have enough", g.panel.lines[0].text)
        self.press(pygame.K_RETURN, pygame.K_e, pygame.K_RETURN)      # then another 1 ...
        self.press(pygame.K_RETURN)
        g.missions.add_item("game_ticket", 1)                         # ... and one more given.
        self.assertEqual((g.missions.items["game_ticket"], g.missions.tokens), (3, 50))
        # Darts: the intro costs nothing; ENTER spends a ticket on an attempt.
        spot = next(s for s in g.fair_level.spots if s.key == "darts")
        g.walker.x, g.walker.y = spot.x, spot.y
        self.assertEqual(g._fair_prompt(), "E   Play Darts  ·  1 ticket")
        self.press(pygame.K_e)
        self.assertTrue(g.fair_game.open)
        self.assertEqual(g.fair_game.mode, "intro")
        self.press(pygame.K_RETURN)
        self.assertEqual((g.fair_game.mode, g.missions.items["game_ticket"]), ("play", 2))
        game = g.fair_game.game
        tokens = g.missions.tokens
        for _ in range(3):                                            # Three bullseyes.
            while math.dist(game.aim(), game.CENTER) > 6:
                game.update(1 / 240)
            self.press(pygame.K_RETURN)
        g.update(1 / 60)
        self.assertEqual((g.fair_game.mode, game.score), ("over", 150))
        self.assertEqual(g.missions.tokens, tokens + 10 + 25 + 50 + 150)
        self.assertTrue(g.missions.fair.closed("darts"))
        self.press(pygame.K_ESCAPE)
        self.assertFalse(g.fair_game.open)
        self.press(pygame.K_e)                                        # Closed for good.
        self.assertEqual(g.panel.chip_name, "Champion")
        self.press(pygame.K_RETURN)
        # A ride: one ticket, then the ride carries the player and puts them back.
        ride = next(s for s in g.fair_level.spots if s.key == "carousel")
        g.walker.x, g.walker.y = ride.x, ride.y
        self.press(pygame.K_e)
        self.assertIsNotNone(g.fair_level.ride)
        self.assertEqual(g.missions.items["game_ticket"], 1)
        for _ in range(int(12 / (1 / 30))):                           # Two laps of the carousel.
            g.update(1 / 30)
        self.assertIsNone(g.fair_level.ride)
        self.assertEqual((g.walker.x, g.walker.y), (ride.x, ride.y))
        # The last booth at platinum wins the F1 car and Pit Stop.
        for game_id in list(GAMES)[1:-1]:
            g.missions.fair.record(game_id, GAMES[game_id].thresholds[-1])
        spot = next(s for s in g.fair_level.spots if s.key == "whack")
        g.walker.x, g.walker.y = spot.x, spot.y
        self.press(pygame.K_e, pygame.K_RETURN)
        whack = g.fair_game.game
        while not whack.over:
            up = whack.moles_up()
            if up:
                whack.cursor = next(iter(up.values()))
                whack.press()
            g.update(1 / 60)
        self.assertEqual(g.panel.title.text, "GRAND PRIZE")
        self.assertTrue(g.missions.fair.f1 and g.car.f1)
        self.assertEqual(g.state.entities[g.player_id]["record"][10:],
                         g.state._tile("vehicle-atlas", "racer_f1"))
        self.press(pygame.K_RETURN, pygame.K_ESCAPE)
        exit_spot = next(s for s in g.fair_level.spots if s.kind == "exit")
        g.walker.x, g.walker.y = exit_spot.x, exit_spot.y
        self.press(pygame.K_e)
        self.assertFalse(g.at_fair)
        self.assertEqual((g.walker.x, g.walker.y), site.door)
        g.arcade.show(g.missions.arcade, ("pit_stop",) if g.missions.fair.f1 else ())
        self.assertTrue(g.arcade.unlocked("pit_stop"))

    def test_factory_pass_depot_machine_and_dock_with_keys(self):
        g = self.game
        site = g.world.factory
        g._step_out(Walker(*site.door))
        g.collisions.fixed = []
        self.assertEqual(g._factory_spot(g.walker), "door")
        self.press(pygame.K_e)                                        # No pass: locked.
        self.assertEqual(g.panel.chip_name, "Locked")
        self.press(pygame.K_RETURN)
        g.missions.add_item("factory_pass", 1)
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("UNLOCK", "NOT NOW"))
        self.press(pygame.K_RETURN)
        self.assertTrue(g.in_factory and g.missions.factory.unlocked)
        self.assertEqual(g.missions.items.get("factory_pass", 0), 0)  # Used up, unlocked for good.
        # The machine with an empty tank says to fill it.
        machine = next(s for s in g.factory_level.spots if s.kind == "machine")
        g.walker.x, g.walker.y = machine.x, machine.y
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("2", "5", "10", "20"))
        self.press(pygame.K_RETURN)
        self.assertIn("Not enough biofuel", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        exit_spot = next(s for s in g.factory_level.spots if s.kind == "exit")
        g.walker.x, g.walker.y = exit_spot.x, exit_spot.y
        self.press(pygame.K_e)
        self.assertFalse(g.in_factory)
        # The depot: 25 corn in (only corn), 25 biofuel in the tank.
        g.missions.add_item("corn", 25)
        g.missions.add_item("tomato", 3)
        g.walker.x, g.walker.y = site.depot_spot
        self.assertEqual(g._factory_spot(g.walker), "depot")
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("FILL UP", "NOT NOW"))
        self.press(pygame.K_RETURN)
        self.assertEqual((g.missions.factory.tank, g.missions.items.get("corn", 0), g.missions.items["tomato"]),
                         (25, 0, 3))
        self.press(pygame.K_RETURN)
        # Back inside: 20 biofuel makes a gold stone, which lands in the inventory.
        g.walker.x, g.walker.y = site.door
        self.press(pygame.K_e)
        self.assertTrue(g.in_factory)
        g.walker.x, g.walker.y = machine.x, machine.y
        self.press(pygame.K_e)                                        # The card follows the highlight.
        self.assertEqual([l.text for l in g.panel.lines], ["Tank: 25 / 200 biofuel", "2 biofuel",
                                                           "80% iron  ·  20% copper"])
        self.press(pygame.K_RIGHT)
        self.assertEqual(g.panel.lines[2].text, "80% copper  ·  20% silver")
        self.press(pygame.K_RIGHT, pygame.K_RIGHT)
        self.assertEqual((g.panel.lines[1].text, g.panel.lines[2].text), ("20 biofuel", "100% gold"))
        self.press(pygame.K_RETURN)
        self.assertEqual(g.missions.factory.tank, 5)
        self.assertIsNotNone(g.factory_level.running)
        for _ in range(4 * 30):
            g.update(1 / 30)
        self.assertEqual(g.missions.items["stone_gold"], 1)
        self.assertIn("Gold stone", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        g.walker.x, g.walker.y = exit_spot.x, exit_spot.y
        self.press(pygame.K_e)
        # The market board shows today's prices; the dock ships at them and the market moves.
        g.walker.x, g.walker.y = site.door
        self.press(pygame.K_e)
        board = next(s for s in g.factory_level.spots if s.kind == "market")
        g.walker.x, g.walker.y = board.x, board.y
        self.assertEqual(g._factory_prompt(), "E   Stone market")
        self.press(pygame.K_e)
        self.assertTrue(g.market_board.open)
        self.press(pygame.K_ESCAPE)
        self.assertFalse(g.market_board.open or g.menu.open)
        g.walker.x, g.walker.y = exit_spot.x, exit_spot.y
        self.press(pygame.K_e)
        price = g.missions.factory.market.price("gold")
        shipments = len(g.missions.factory.market.history["gold"])
        tokens, points = g.missions.tokens, g.missions.unspent
        g.walker.x, g.walker.y = site.dock_spot
        self.assertEqual(g._factory_spot(g.walker), "dock")
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("SHIP", "NOT NOW"))
        self.assertEqual(g.panel.bill_left[0].text, f"Gold stone  1 x {price:,} S")
        self.press(pygame.K_RETURN)
        self.assertEqual((g.missions.tokens, g.missions.unspent), (tokens + price, points + 25))
        self.assertEqual(g.missions.items.get("stone_gold", 0), 0)
        self.assertEqual(g.panel.bill_right[-1].text, f"{price:,} S + 25 mastery  PAID")
        self.assertEqual(len(g.missions.factory.market.history["gold"]), min(12, shipments + 1))
        self.assertEqual(g.panel.title.text, "Shipped")

    def test_elite_island_ferry_races_club_and_tournament_with_keys(self):
        from grid_race import GridRace
        from progression import REGIONS
        g = self.game
        progress = g.missions.progress
        for region in REGIONS:
            progress.races[region] = 10
        progress.add("city", mastery_to_reach(25))
        g._refresh_landmarks()
        self.assertIn("ferry", [m.kind for m in g.landmarks])
        mainland, island = __import__("island").ferry_spots(g.world)
        g._step_out(Walker(*mainland))
        g.collisions.fixed = []
        self.assertEqual(g._ferry_side(g.walker), "mainland")
        self.press(pygame.K_e)                                        # No pass yet.
        self.assertIn("Island pass", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        g.missions.add_item("island_pass", 1)
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("BOARD", "NOT NOW"))
        self.press(pygame.K_RETURN)
        self.assertTrue(g.missions.island.crossed)
        self.assertEqual(g.missions.items.get("island_pass", 0), 0)   # Used up, open for good.
        self.assertTrue(on_island(g.world, g.car.x, g.car.y))
        self.assertIn("club", [m.kind for m in g.landmarks])
        self.assertIn("Island racing center", [m.name for m in g.landmarks])
        self.press(pygame.K_RETURN)
        # The island racing center: race 1 against a 360.
        cx, cy = g.missions.center_position("island")
        g._step_out(Walker(cx, cy + 110))
        g.collisions.fixed = []
        self.assertEqual(g._near_island_center(g.walker), True)
        self.press(pygame.K_e)
        self.assertTrue(g.in_island_center)
        desk = {s.kind: s for s in g.island_level.spots}
        g.walker.x, g.walker.y = desk["clubs"].x, desk["clubs"].y
        self.press(pygame.K_e)                                        # Clubs wait for race 1.
        self.assertIn("first race", g.panel.lines[0].text)
        self.press(pygame.K_RETURN)
        g.walker.x, g.walker.y = desk["races"].x, desk["races"].y
        self.press(pygame.K_e)
        self.assertIn("(360)", g.panel.lines[1].text)
        self.press(pygame.K_RETURN)
        self.assertEqual((g.center_race, g.race.level.surface), (("island", 1), "island"))
        self.assertAlmostEqual(g.race.speed_scale, progress.speed_scale("city"))   # The best region's.
        g.race.times["player"], g.race.result = 60.0, "win"
        g._end_race()
        self.assertEqual(g.missions.island.races, 1)
        self.press(pygame.K_RETURN)
        # Join the Palm Runners (league 1, 20,000 S).
        g._enter_island_center()
        g.walker.x, g.walker.y = desk["clubs"].x, desk["clubs"].y
        g.missions.add_item("sunside_tokens", 30_000)
        self.press(pygame.K_e)
        self.assertEqual(g.panel.buttons, ("L1",))
        self.press(pygame.K_RETURN)
        self.assertEqual(g.panel.buttons, ("PALM", "REEF"))
        self.press(pygame.K_RETURN, pygame.K_RETURN)                  # PALM, then JOIN.
        self.assertEqual((g.missions.island.club, g.missions.tokens), ("palm", 10_000))
        self.press(pygame.K_RETURN)
        # A tournament: buy a pass, start, four races.
        g.walker.x, g.walker.y = desk["tourney"].x, desk["tourney"].y
        self.press(pygame.K_e, pygame.K_RETURN)                       # BUY PASS.
        self.assertEqual((g.missions.items["tourney_pass"], g.missions.tokens), (1, 8_000))
        self.press(pygame.K_RETURN, pygame.K_e, pygame.K_RIGHT, pygame.K_RETURN)   # START.
        self.assertIsInstance(g.race, GridRace)
        self.assertEqual(len(g.race.rivals), 7)
        themes = [t.theme for t in g.tournament.tracks]
        self.assertEqual(len(set(themes)), 4)
        self.assertAlmostEqual(g.race.speed_scale, progress.speed_scale(themes[0]))   # That region's.
        tokens, points = g.missions.tokens, g.missions.unspent
        for n in range(4):
            race = g.race
            race.times[-1] = 1.0                                      # The player wins each race.
            race.finish()
            g._end_race()
            self.assertEqual(g.tournament.race if g.tournament else 4, n + 1)
            self.press(pygame.K_RETURN)
        self.assertIsNone(g.tournament)
        self.assertEqual(g.missions.tokens, tokens + 10_000)          # A quarter of the 40,000 S pool.
        self.assertEqual(g.missions.unspent, points + 50)
        self.assertEqual(g.missions.island.won, 1)
        # GO HOME asks: city home or club camp.
        g._offer_spawn()
        self.assertEqual(g.panel.buttons, ("CITY HOME", "CLUB CAMP"))
        self.press(pygame.K_RIGHT, pygame.K_RETURN)
        camp = g.world.island_camps["palm"]
        self.assertEqual((g.car.x, g.car.y), camp.parking[:2])

    def test_beach_travel_picks_a_pier_with_keys(self):
        g = self.game
        self.unlock_fishing()
        # Pause > Mastery; Back starts selected, just after the rural and beach TRAVEL buttons.
        self.press(pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN)
        self.assertEqual(g.menu.items[-2:], ("travel:beach", "back"))
        self.press(pygame.K_UP, pygame.K_RETURN)
        self.assertEqual(g.menu.page, "docks")
        self.press(pygame.K_ESCAPE)                                # Back to the table.
        self.assertEqual((g.menu.page, g.menu.items[g.menu.selected]), ("mastery", "travel:beach"))
        self.press(pygame.K_RETURN, pygame.K_DOWN, pygame.K_RETURN)   # Pier 2 is locked.
        self.assertEqual(g.menu.page, "docks")
        self.assertEqual(g.menu.dock_notes["north"].text, "Needs 3 regions at level 7")
        for region in ("city", "snow", "desert"):
            g.missions.progress.add(region, mastery_to_reach(7))
        self.press(pygame.K_RETURN)                                # Now it opens.
        self.assertFalse(g.menu.open)
        north = next(d for d in g.world.docks if d.name == "north")
        self.assertEqual(g.world.region_at(g.car.x, g.car.y), "beach")
        self.assertLess(math.dist((g.car.x, g.car.y), north.shore()), 400)
        self.assertIsNone(g.walker)

    def test_each_pier_has_its_own_lock_and_notice(self):
        g = self.game
        self.unlock_fishing()
        east = next(d for d in g.world.docks if d.name == "east")
        ex, ey = east.end
        g._step_out(Walker((ex + 0.5) * 64, (ey + 0.5) * 64))
        self.assertEqual(g._fishing_prompt(g.walker), "Needs every region at level 12")
        self.press(pygame.K_e)
        self.assertIsNone(g.fishing)
        self.assertEqual(g.panel.title.text, "Pier 3  ·  East")
        self.press(pygame.K_RETURN)
        levels = g.missions.progress.levels
        levels.update({region: 12 for region in levels})
        self.press(pygame.K_e)
        self.assertIsNotNone(g.fishing)

    def test_menu_travel_to_the_beach(self):
        g = self.game
        self.unlock_fishing()
        g.menu.toggle()
        g._fast_travel("beach")
        self.assertFalse(g.menu.open)
        self.assertEqual(g.world.region_at(g.car.x, g.car.y), "beach")
        g.render()


if __name__ == "__main__":
    unittest.main()
