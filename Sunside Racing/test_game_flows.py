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
from progression import rating_speed
from missions import Offer
from walker import Walker


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
        self.game.inputs.driving = lambda: (0, 0, False)

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
        g.missions.progress.add("city", 10 + 20)         # Level 3: rating 120.
        g.missions.progress.races["city"] = 3            # Race 4's rival is rated 140.
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e)
        self.assertEqual(g.panel.lines[1].text, "Mara Quill (140) VS You (120)")

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
        g.missions.progress.add("city", 10)             # Level 2 (110) when offered.
        g.missions.offers[dg.id] = Offer(dg.id, "drag", 1.0, None, {"kind": "straight", "theme": "city"},
                                         5, dict(g.missions.progress.levels), 125)
        g.missions.progress.add("city", 20)             # Level 3 (120) by the time it's accepted.
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
        g.missions.progress.add("city", 10 + 20)          # Level 3: a drag pays 5.
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
        self.game.inputs.driving = lambda: (0, 0, False)
        return self.game

    def test_quitting_the_game_mid_mission_returns_to_the_giver(self):
        g = self.game
        giver = g.missions.by_id["city-delivery"]
        g._step_out(Walker(giver.x + 20, giver.y))
        g.missions.offers[giver.id] = Offer(giver.id, "delivery", 1.0, (giver.x + 3000, giver.y))
        self.press(pygame.K_e, pygame.K_RETURN)
        g.walker.x += 900                                  # Wander far off mid-delivery.
        g = self.reload()
        self.assertIsNone(g.missions.active)               # Quit, not resumed.
        self.assertIsNone(g.missions.status())
        self.assertIsNotNone(g.walker)
        self.assertLess(math.dist((g.walker.x, g.walker.y), (giver.x, giver.y)), 80)
        self.assertLess(math.dist((g.car.x, g.car.y), (giver.x, giver.y)), 400)
        self.assertIn(giver.id, g.missions.offers)         # Same mission waits there.

    def test_quitting_the_game_mid_race_returns_to_the_center(self):
        g = self.game
        cx, cy = g.missions.center_position("city")
        g._step_out(Walker(cx, cy + 110))
        self.press(pygame.K_e, pygame.K_RETURN)
        for _ in range(240):
            g.update(1 / 60)
        g = self.reload()
        self.assertIsNone(g.race)
        self.assertEqual(g.missions.progress.races["city"], 0)
        self.assertLess(math.dist((g.walker.x, g.walker.y), (cx, cy)), 250)

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
        g.inputs.driving = lambda: (1, 0, False)
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
        self.assertEqual(drawn[-1][-1], (70, 140, 220))              # Piers: blue arrow.
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
        self.assertEqual(kinds.count("center"), 6)
        self.assertEqual(kinds.count("dock"), 3)
        self.assertEqual(kinds.count("camp"), sum(r == "jungle" for r in g.world.camps.values()))
        self.assertEqual({k: v[1] for k, v in KINDS.items()},
                         {"center": (212, 80, 66), "dock": (70, 140, 220), "camp": (236, 120, 170)})
        self.assertEqual({k: v[0] for k, v in KINDS.items()}["dock"], (242, 150, 60))   # Orange on the map.
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
        g.missions.progress.add("rural", 60)
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
        self.game.missions.progress.add("rural", 10 + 20 + 30)   # Level 4 in any region.

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
        self.assertTrue(g.spend_menu.open)
        self.assertEqual((g.missions.unspent, g.missions.fish.count), (10, 0))
        # City +1 starts selected; Down to Jungle +1, Right to Jungle +5, spend it.
        self.press(pygame.K_DOWN, pygame.K_RIGHT, pygame.K_RETURN)
        self.assertEqual((g.missions.progress.mastery["jungle"], g.missions.unspent), (5, 5))
        self.press(pygame.K_ESCAPE)                               # Keep the other 5.
        self.assertFalse(g.spend_menu.open)
        self.assertEqual(g.missions.unspent, 5)
        # Pause > Mastery; Back starts selected and SPEND POINTS sits just before it.
        self.press(pygame.K_ESCAPE, pygame.K_DOWN, pygame.K_RETURN)
        self.assertEqual(g.menu.page, "mastery")
        self.assertIn("spend", g.menu.items)
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
            g.missions.progress.add(region, 210)                   # Level 7.
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
