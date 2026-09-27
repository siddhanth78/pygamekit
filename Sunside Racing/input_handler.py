"""Turn Pygame events and held keys into driving intent."""

import pygame


class InputHandler:
    def __init__(self, game_state=None):
        self.game_state = game_state
        self.keys_held = set()

    def handle_events(self, mx=0, my=0):
        intents = []
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                intents.append(("quit", None))
            elif event.type == pygame.KEYDOWN:
                self.keys_held.add(event.key)
                if event.key == pygame.K_ESCAPE:
                    intents.append(("pause", None))
                elif event.key == pygame.K_r:
                    intents.append(("reset", None))
                elif event.key == pygame.K_e:
                    intents.append(("interact", None))
                elif event.key == pygame.K_q:
                    intents.append(("call_car", None))
                elif event.key == pygame.K_t:
                    intents.append(("island", None))
                elif event.key == pygame.K_m:
                    intents.append(("map", None))
                elif event.key == pygame.K_c:
                    intents.append(("clear", None))
                elif event.key == pygame.K_i:
                    intents.append(("inventory", None))
                elif event.key in (pygame.K_UP, pygame.K_w):
                    intents.append(("menu_up", None))
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    intents.append(("menu_down", None))
                elif event.key in (pygame.K_LEFT, pygame.K_a):
                    intents.append(("menu_left", None))
                elif event.key in (pygame.K_RIGHT, pygame.K_d):
                    intents.append(("menu_right", None))
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    intents.append(("confirm", None))
            elif event.type == pygame.MOUSEMOTION:
                intents.append(("pointer", event.pos))
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                intents.append(("click", event.pos))
            elif event.type == pygame.KEYUP:
                self.keys_held.discard(event.key)
            elif event.type == pygame.VIDEORESIZE:
                intents.append(("resize", (event.w, event.h)))
            elif event.type == pygame.WINDOWFOCUSLOST:
                self.keys_held.clear()
                intents.append(("focus_lost", None))
        return intents

    def get_held_keys(self):
        return self.keys_held.copy()

    def driving(self):
        keys = self.keys_held
        throttle = int(bool(keys & {pygame.K_w, pygame.K_UP})) - int(
            bool(keys & {pygame.K_s, pygame.K_DOWN}))
        steer = int(bool(keys & {pygame.K_d, pygame.K_RIGHT})) - int(
            bool(keys & {pygame.K_a, pygame.K_LEFT}))
        return throttle, steer

    def walking(self):
        """On foot, keys move in screen directions: W/Up is north, D/Right is east."""
        keys = self.keys_held
        move_x = int(bool(keys & {pygame.K_d, pygame.K_RIGHT})) - int(
            bool(keys & {pygame.K_a, pygame.K_LEFT}))
        move_y = int(bool(keys & {pygame.K_s, pygame.K_DOWN})) - int(
            bool(keys & {pygame.K_w, pygame.K_UP}))
        return move_x, move_y, bool(keys & {pygame.K_LSHIFT, pygame.K_RSHIFT})
