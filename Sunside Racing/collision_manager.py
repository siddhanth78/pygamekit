"""Local collision checks and enter/exit tracking for streamed scenery."""

import math

from gl_utils import check_collision, check_mouse_collisions


def nearest_clear_spot(collisions, make_record, x, y, clearance, max_radius=480, step=8):
    """Search outward rings for the closest spot where make_record(x, y), padded, is free."""
    for radius in range(0, max_radius + 1, step):
        steps = max(1, round(2 * math.pi * radius / step))
        for i in range(steps):
            angle = 2 * math.pi * i / steps
            cx, cy = x + math.cos(angle) * radius, y + math.sin(angle) * radius
            roomy = make_record(cx, cy)
            roomy[7] += clearance
            roomy[8] += clearance
            if collisions.can_move(roomy):
                return cx, cy
    return None


class CollisionManager:
    def __init__(self, game_state, world):
        self.game_state = game_state
        self.world = world
        self.traffic = None  # Attached after the player save is validated.
        self.parking = None
        self.fixed = []  # Extra solid sprites, e.g. the player's parked car while on foot.
        self.pedestrians = None
        self.others = []  # More moving solids with nearby_obstacles(x, y), e.g. the fair's visitor cars.
        self.current_collisions = {}
        self.previous_collisions = {}
        self.on_collision_enter = {}
        self.on_collision_exit = {}

    def register_enter_callback(self, entity_id, callback):
        self.on_collision_enter[entity_id] = callback

    def register_exit_callback(self, entity_id, callback):
        self.on_collision_exit[entity_id] = callback

    def colliding_obstacles(self, player_rect):
        obstacles = self.world.nearby_obstacles(player_rect[0], player_rect[1])
        if self.parking is not None:
            obstacles = [item for item in obstacles if not self.parking.is_away(item)]
        if self.traffic is not None:
            obstacles += self.traffic.nearby_obstacles(player_rect[0], player_rect[1])
        if self.pedestrians is not None:
            obstacles += self.pedestrians.nearby_obstacles(player_rect[0], player_rect[1])
        for other in self.others:
            obstacles += other.nearby_obstacles(player_rect[0], player_rect[1])
        obstacles += [item for item in self.fixed
                      if abs(item.x - player_rect[0]) < 145 and abs(item.y - player_rect[1]) < 145]
        hits = check_collision(player_rect, [item.obstacle_record() for item in obstacles], "rect")
        return {obstacles[index] for _, index in hits}

    def can_move(self, player_rect):
        return self.world.can_place_car(player_rect) and not self.colliding_obstacles(player_rect)

    def can_walk(self, walker_rect):
        """Like can_move, but people may also stand on the fishing piers."""
        return self.world.can_place_walker(walker_rect) and not self.colliding_obstacles(walker_rect)

    def update(self, entity_id, player_rect):
        previous = self.current_collisions.get(entity_id, set())
        current = self.colliding_obstacles(player_rect)
        self.previous_collisions[entity_id] = previous
        self.current_collisions[entity_id] = current
        for item in current - previous:
            if entity_id in self.on_collision_enter:
                self.on_collision_enter[entity_id](entity_id, item, "rect")
        for item in previous - current:
            if entity_id in self.on_collision_exit:
                self.on_collision_exit[entity_id](entity_id, item, "rect")
        return current

    def get_mouse_collisions(self, mx, my, atlas_name):
        records = self.game_state.data[atlas_name]
        return check_mouse_collisions(mx, my, records, "tex") if records else []
