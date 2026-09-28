"""Farm deliveries: one buyer in each mainland region, each with an open order.

An order asks for one to three kinds of farm goods (corn, tomato, lettuce, milk, eggs),
one to five of each. It is worth the goods' prices plus DISTANCE_BONUS per km from the
farm, and each buyer pays either Sunside Tokens or universal mastery points (1 per
MASTERY_RATE S, into the inventory). The player works one order at a time: talking to a
buyer takes their order (the guide arrow then points at them); bringing everything
delivers it, and a new buyer appears somewhere else in that region. There is no load
limit: goods are simply in the inventory. Buyers only appear once the farm is owned.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from inventory import BY_ID, count_of, room
from world import Sprite


REGIONS = ("city", "jungle", "desert", "snow", "rural")
GOODS = ("corn", "tomato", "lettuce", "milk", "eggs")
PRICES = {"corn": 100, "tomato": 120, "lettuce": 140, "milk": 40, "eggs": 25}
MAX_KINDS, MAX_EACH = 3, 5
DISTANCE_BONUS = 0.10          # +10% of the goods' value per km from the farm.
MASTERY_RATE = 20              # A mastery payer gives 1 point per 20 S the order is worth.
BUYER_KINDS = {"city": "city_e", "jungle": "explorer_b", "desert": "nomad_a",
               "snow": "snow_b", "rural": "farmer_a"}
BUYER_RANGE = 40               # On foot, px from a buyer to talk.


@dataclass
class Order:
    region: str
    x: float
    y: float
    goods: dict = field(default_factory=dict)   # Item id -> how many.
    pay: str = "tokens"                         # "tokens" or "mastery".
    value: int = 0                              # Sunside Tokens the goods are worth, bonus in.

    @property
    def points(self) -> int:
        return max(1, self.value // MASTERY_RATE)

    @property
    def title(self) -> str:
        return f"{self.region.title()} farm buyer"

    def goods_line(self) -> str:
        return ",  ".join(f"{n} {BY_ID[g].name.lower()}" for g, n in self.goods.items())

    def pay_line(self) -> str:
        return (f"Pays {self.value:,} Sunside Tokens" if self.pay == "tokens"
                else f"Pays {self.points} mastery points ({self.value:,} S of goods)")

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "goods": dict(self.goods), "pay": self.pay, "value": self.value}

    @staticmethod
    def from_dict(region, data) -> "Order":
        if not isinstance(data, dict):
            raise ValueError("Order is not a dict")
        x, y, goods, pay, value = (data.get(k) for k in ("x", "y", "goods", "pay", "value"))
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (x, y)):
            raise ValueError("Bad buyer position")
        if (not isinstance(goods, dict) or not 1 <= len(goods) <= MAX_KINDS
                or not all(g in GOODS and type(n) is int and 1 <= n <= MAX_EACH for g, n in goods.items())):
            raise ValueError("Bad goods")
        if pay not in ("tokens", "mastery") or type(value) is not int or value <= 0:
            raise ValueError("Bad pay")
        return Order(region, float(x), float(y), dict(goods), pay, value)


def order_value(goods: dict, km: float) -> int:
    """The goods' prices plus the distance bonus, rounded to 5 S."""
    base = sum(PRICES[g] * n for g, n in goods.items())
    return int(round(base * (1 + DISTANCE_BONUS * km) / 5)) * 5


class FarmOrders:
    """The five open orders (one per region) and which one the player is working on.

    place(region, rng) returns an (x, y) spot for a new buyer in that region, or None."""

    def __init__(self, farm_xy, seed: int, data=None, place=None):
        self.farm_xy = farm_xy
        self.seed = seed
        self.place = place
        data = data if isinstance(data, dict) else {}
        self.counter = data.get("counter") if type(data.get("counter")) is int and data["counter"] >= 0 else 0
        self.orders: dict[str, Order] = {}
        for region, raw in (data.get("orders") or {}).items() if isinstance(data.get("orders"), dict) else ():
            if region in REGIONS:
                try:
                    self.orders[region] = Order.from_dict(region, raw)
                except (ValueError, TypeError):
                    pass
        active = data.get("active")
        self.active = active if active in self.orders else None
        self.fill()

    def fill(self):
        """Give every region without an order a new buyer."""
        for region in REGIONS:
            if region not in self.orders:
                order = self._new_order(region)
                if order:
                    self.orders[region] = order

    def _new_order(self, region) -> Order | None:
        self.counter += 1
        rng = random.Random(f"{self.seed}-farm-order-{region}-{self.counter}")
        spot = self.place(region, rng) if self.place else None
        if spot is None:
            return None
        kinds = rng.sample(GOODS, rng.randint(1, MAX_KINDS))
        goods = {g: rng.randint(1, MAX_EACH) for g in GOODS if g in kinds}
        km = math.dist(spot, self.farm_xy) / 10000
        return Order(region, *spot, goods, rng.choice(("tokens", "mastery")), order_value(goods, km))

    def to_dict(self) -> dict:
        return {"counter": self.counter, "active": self.active,
                "orders": {r: o.to_dict() for r, o in self.orders.items()}}

    # Playing -----------------------------------------------------------------------------

    def buyer_near(self, x, y) -> Order | None:
        return next((o for o in self.orders.values() if math.dist((x, y), (o.x, o.y)) <= BUYER_RANGE), None)

    @staticmethod
    def missing(missions, order: Order) -> dict:
        """Goods still needed: item id -> how many more."""
        return {g: n - count_of(missions, g) for g, n in order.goods.items() if count_of(missions, g) < n}

    def take(self, region: str):
        """Work on this buyer's order (only one at a time; others stay open)."""
        self.active = region

    def deliver(self, missions, region: str) -> str | None:
        """Hand over the goods and get paid; a new buyer takes this region's place.
        Returns why not, or None when delivered."""
        order = self.orders[region]
        if self.missing(missions, order):
            return "You don't have everything they asked for."
        if order.pay == "tokens":
            if missions.tokens + order.value > BY_ID["sunside_tokens"].max_stack:
                return "Your Sunside Tokens are full."
        elif order.points > room(missions.unspent):
            return "Your mastery points are full. Spend some first."
        for good, n in order.goods.items():
            missions.add_item(good, -n)
        if order.pay == "tokens":
            missions.add_item("sunside_tokens", order.value)
        else:
            missions.add_universal(order.points)
        if self.active == region:
            self.active = None
        del self.orders[region]
        self.fill()
        return None

    # Display -----------------------------------------------------------------------------

    def sprites(self, clock: float) -> list[Sprite]:
        bob = math.sin(clock * 3 + 1) * 3
        out = []
        for region, order in self.orders.items():
            out.append(Sprite("people-atlas", f"{BUYER_KINDS[region]}_idle", order.x, order.y, 32, 32, 180.0))
            icon = "icon_dropoff" if region == self.active else "icon_order"
            out.append(Sprite("marker-atlas", icon, order.x, order.y - 34 + bob, 32, 32))
        return out

