from copy import deepcopy
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from math import isfinite
from random import Random
from time import perf_counter

from .allocation import ALLOCATORS
from .config import default_config, validate_config
from .network import build_network, shortest_path


SERVICE_MESSAGES = {"LOAD_REQUEST", "QUEUED", "LOAD_START", "LOAD_DONE", "DELIVERED"}


@dataclass
class Order:
    id: str
    destination: int
    weight: int
    release_at: int
    deadline: int
    warehouse: str
    status: str = "unreleased"
    courier: str | None = None
    delivered_at: int | None = None
    loading_requested_at: int | None = None
    loading_started_at: int | None = None


@dataclass
class Courier:
    id: str
    capacity: int
    speed: float
    node: int
    state: str = "idle"
    order_id: str | None = None
    route: list[int] = field(default_factory=list)
    edge_remaining: float = 0.0
    edge_total: float = 0.0
    until: int = 0
    distance: float = 0.0
    completed: int = 0

    def receive(self, message, sim):
        kind, data = message["type"], message["data"]
        order_id = message["order_id"]
        order = sim.order(order_id) if order_id else None
        if kind == "CFP":
            cost = sim.estimate(self, order)
            if self.state != "idle" or cost is None:
                sim.send(self.id, "dispatcher", "REFUSE", order_id,
                         "Не могу выполнить заказ", {"round": data["round"]})
            else:
                sim.send(self.id, "dispatcher", "PROPOSE", order_id,
                         f"Оценка: {cost:.3f} мин", {"round": data["round"], "cost": cost})
        elif kind == "AUCTION_OPEN":
            candidates = []
            if self.state == "idle":
                for item in data["orders"]:
                    candidate = sim.order(item["id"])
                    cost = sim.estimate(self, candidate)
                    if cost is not None:
                        candidates.append((cost, candidate.id))
            if not candidates:
                sim.send(self.id, "dispatcher", "NO_BID", "",
                         "Подходящих заказов нет", {"round": data["round"]})
            else:
                cost, chosen = min(candidates)
                sim.send(self.id, "dispatcher", "BID", chosen,
                         f"Заявка: {cost:.3f} мин", {"round": data["round"], "cost": cost})
        elif kind == "TASK_OFFER":
            if self.state != "idle" or order.weight > self.capacity:
                raise ValueError("Недопустимое назначение")
            self.state, self.order_id = "reserved", order.id
            sim.send(self.id, "dispatcher", "ACCEPT", order.id, "Принимаю заказ")
        elif kind == "TASK_COMMIT":
            if self.state != "reserved" or self.order_id != order.id:
                raise ValueError("Назначение не подтверждено")
            sim.set_route(self, sim.warehouses[order.warehouse].node, "to_warehouse")
        elif kind == "LOAD_START":
            self.state = "loading"
            self.until = sim.time + sim.warehouses[message["from"]].loading_minutes
            order.loading_started_at = sim.time
        elif kind == "LOAD_DONE":
            sim.set_route(self, order.destination, "delivering")


class Warehouse:
    def __init__(self, warehouse_id, node, slots, loading_minutes):
        self.id, self.node = warehouse_id, node
        self.slots, self.loading_minutes = slots, loading_minutes
        self.queue, self.active = [], {}

    def receive(self, message, sim):
        if message["type"] != "LOAD_REQUEST":
            return
        courier = sim.couriers[message["from"]]
        order = sim.order(courier.order_id)
        if order.warehouse != self.id or courier.node != self.node:
            raise ValueError("Запрос отправлен не на склад заказа")
        if courier.id not in self.queue and courier.id not in self.active:
            self.queue.append(courier.id)
            courier.state = "waiting_loading"
            order.loading_requested_at = sim.time
            sim.send(self.id, courier.id, "QUEUED", order.id, "Курьер поставлен в очередь")

    def step(self, sim):
        for courier_id in list(self.active):
            courier = sim.couriers[courier_id]
            if sim.time >= self.active[courier_id]:
                del self.active[courier_id]
                sim.send(self.id, courier_id, "LOAD_DONE", courier.order_id, "Погрузка завершена")
        while self.queue and len(self.active) < self.slots:
            courier_id = self.queue.pop(0)
            courier = sim.couriers[courier_id]
            self.active[courier_id] = sim.time + self.loading_minutes
            sim.send(self.id, courier_id, "LOAD_START", courier.order_id, "Погрузка началась")


class Dispatcher:
    def __init__(self):
        self.proposals, self.bids = {}, {}

    def receive(self, message, sim):
        kind, sender = message["type"], message["from"]
        if kind in ("PROPOSE", "BID"):
            data = message["data"]
            if data["round"] != sim.allocation_rounds or not isfinite(data["cost"]):
                raise ValueError("Некорректное предложение")
            if kind == "PROPOSE":
                self.proposals[sender] = data["cost"]
            else:
                self.bids[sender] = (message["order_id"], data["cost"])
        elif kind == "ACCEPT":
            order = sim.order(message["order_id"])
            if order.status != "pending":
                raise ValueError("Заказ уже назначен")
            order.status, order.courier = "assigned", sender
            sim.send("dispatcher", sender, "TASK_COMMIT", order.id, "Назначение подтверждено")
        elif kind == "DELIVERED":
            order = sim.order(message["order_id"])
            if order.courier != sender or order.status != "assigned":
                raise ValueError("Недопустимый отчёт о доставке")
            order.status, order.delivered_at = "delivered", sim.time


def generate_orders(config, seed):
    rng = Random(seed)
    warehouse_nodes = {w["node"] for w in config["warehouses"]}
    destinations = [node for node in range(20) if node not in warehouse_nodes]
    orders = []
    for index in range(config["order_count"]):
        destination = rng.choice(destinations)
        release = (index // config["batch_size"]) * config["arrival_interval"]
        deadline = release + rng.randint(config["deadline_min"], config["deadline_max"])
        weight = rng.randint(1, config["max_weight"])
        warehouse = rng.choice(config["warehouses"])["id"]
        orders.append(Order(f"O{index + 1:03}", destination, weight, release, deadline, warehouse))
    return orders


class Simulation:
    def __init__(self, seed=42, config=None, mechanism="centralized", orders=None):
        if mechanism not in ALLOCATORS:
            raise ValueError("Неизвестный механизм")
        config = default_config("normal") if config is None else config
        validate_config(config)
        self.seed, self.time, self.mechanism = seed, 0, mechanism
        self.config = deepcopy(config)
        self.nodes, self.edges = build_network()
        self.path_cache = {}
        self.warehouses = {}
        for w in config["warehouses"]:
            self.warehouses[w["id"]] = Warehouse(w["id"], w["node"], w["slots"], w["loading_minutes"])
        self.couriers = {c["id"]: Courier(c["id"], c["capacity"], c["speed"], c["node"])
                         for c in config["couriers"]}
        self.orders = deepcopy(orders) if orders is not None else generate_orders(config, seed)
        self.validate_orders()
        self.order_index = {o.id: o for o in self.orders}
        self.dispatcher = Dispatcher()
        self.messages, self.events = [], []
        self.allocation_rounds = self.evaluations = 0
        self.allocation_seconds = 0.0
        self.wire_bytes = self.coordination_bytes = self.coordination_messages = 0
        self.assignment_history = []
        initial = {"config": self.config, "orders": [asdict(o) for o in self.orders],
                   "edges": self.edges}
        self.input_hash = sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest()
        self.release_orders()

    def validate_orders(self):
        if len(self.orders) != self.config["order_count"]:
            raise ValueError("Количество заказов не соответствует конфигурации")
        if len({o.id for o in self.orders}) != len(self.orders):
            raise ValueError("Идентификаторы заказов повторяются")
        warehouses = {w.id for w in self.warehouses.values()}
        depot_nodes = {w.node for w in self.warehouses.values()}
        max_capacity = max(c.capacity for c in self.couriers.values())
        for o in self.orders:
            if (o.warehouse not in warehouses or o.destination not in range(20)
                    or o.destination in depot_nodes or not 1 <= o.weight <= max_capacity
                    or o.release_at < 0 or o.deadline < o.release_at
                    or o.status != "unreleased" or o.courier is not None
                    or o.delivered_at is not None or o.loading_requested_at is not None
                    or o.loading_started_at is not None):
                raise ValueError("Некорректный исходный заказ")

    @staticmethod
    def clock(value):
        return f"{9 + value // 60:02}:{value % 60:02}"

    def order(self, order_id):
        return self.order_index[order_id]

    @staticmethod
    def order_description(order):
        return {key: getattr(order, key) for key in
                ("id", "destination", "weight", "release_at", "deadline", "warehouse")}

    def path(self, start, target):
        key = start, target
        if key not in self.path_cache:
            self.path_cache[key] = shortest_path(self.edges, start, target)
        cost, route = self.path_cache[key]
        return cost, route[:]

    def estimate(self, courier, order):
        self.evaluations += 1
        if order.weight > courier.capacity:
            return None
        warehouse = self.warehouses[order.warehouse]
        first, _ = self.path(courier.node, warehouse.node)
        second, _ = self.path(warehouse.node, order.destination)
        return (first + second) / courier.speed + warehouse.loading_minutes + self.config["service_minutes"]

    def free_couriers(self):
        return sorted((c for c in self.couriers.values() if c.state == "idle"), key=lambda c: c.id)

    def pending_orders(self):
        return sorted((o for o in self.orders if o.status == "pending"), key=lambda o: (o.release_at, o.id))

    def award(self, courier, order):
        if courier.state != "idle" or order.status != "pending" or courier.capacity < order.weight:
            raise ValueError("Пара недопустима")
        self.assignment_history.append({"time": self.time, "courier": courier.id, "order": order.id})
        self.send("dispatcher", courier.id, "TASK_OFFER", order.id,
                  "Предлагаю выполнить заказ", {"order": self.order_description(order)})

    def send(self, sender, recipient, kind, order_id, text, data=None):
        message = {"id": len(self.messages) + 1, "time": self.time, "from": sender,
                   "to": recipient, "type": kind, "order_id": order_id,
                   "text": text, "data": {} if data is None else data}
        size = len(json.dumps(message, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8"))
        self.messages.append(message)
        self.wire_bytes += size
        if kind not in SERVICE_MESSAGES:
            self.coordination_messages += 1
            self.coordination_bytes += size
        if recipient == "dispatcher":
            agent = self.dispatcher
        elif recipient in self.warehouses:
            agent = self.warehouses[recipient]
        else:
            agent = self.couriers[recipient]
        agent.receive(message, self)

    def release_orders(self):
        for order in self.orders:
            if order.status == "unreleased" and order.release_at <= self.time:
                order.status = "pending"
                self.events.append({"time": self.time, "type": "ORDER_ARRIVAL", "order_id": order.id})

    def set_route(self, courier, target, state):
        _, courier.route = self.path(courier.node, target)
        courier.state = state
        courier.edge_remaining = courier.edge_total = 0.0
        if len(courier.route) == 1:
            self.arrive(courier)

    def arrive(self, courier):
        courier.route = []
        order = self.order(courier.order_id)
        if courier.state == "to_warehouse":
            self.send(courier.id, order.warehouse, "LOAD_REQUEST", order.id, "Запрашиваю погрузку")
        elif courier.state == "delivering":
            courier.state, courier.until = "servicing", self.time + self.config["service_minutes"]

    def move(self, courier):
        budget = 1.0
        while budget > 1e-9 and len(courier.route) > 1:
            a, b = courier.route[:2]
            edge = next(e for e in self.edges if {e["a"], e["b"]} == {a, b})
            if courier.edge_remaining <= 1e-9:
                courier.edge_total = edge["minutes"] / courier.speed
                courier.edge_remaining = courier.edge_total
            spent = min(budget, courier.edge_remaining)
            courier.edge_remaining -= spent
            courier.distance += edge["km"] * spent / courier.edge_total
            budget -= spent
            if courier.edge_remaining <= 1e-9:
                courier.edge_remaining = courier.edge_total = 0.0
                courier.node = b
                courier.route.pop(0)
                if len(courier.route) == 1:
                    self.arrive(courier)
                    break

    def step(self, count=1):
        if type(count) is not int or count < 0:
            raise ValueError("Число шагов должно быть неотрицательным целым")
        for _ in range(count):
            if self.finished:
                break
            self.time += 1
            self.release_orders()
            for courier in self.couriers.values():
                if courier.state in ("to_warehouse", "delivering"):
                    self.move(courier)
                elif courier.state == "servicing" and self.time >= courier.until:
                    self.send(courier.id, "dispatcher", "DELIVERED", courier.order_id, "Заказ передан")
                    courier.completed += 1
                    courier.state, courier.order_id = "idle", None
            for warehouse in self.warehouses.values():
                warehouse.step(self)
            if self.free_couriers() and self.pending_orders():
                start = perf_counter()
                ALLOCATORS[self.mechanism](self)
                self.allocation_seconds += perf_counter() - start
            self.check_invariants()

    @property
    def finished(self):
        return all(o.status == "delivered" for o in self.orders)

    def run_until_finished(self, limit=100000):
        self.step(limit)
        if not self.finished:
            raise RuntimeError("Достигнут предел шагов, результат не завершён")
        return self.metrics()

    def add_order(self, destination, weight, due_in, warehouse_id=None):
        if warehouse_id is None:
            warehouse_id = next(iter(self.warehouses))
        warehouse_nodes = {w.node for w in self.warehouses.values()}
        if (warehouse_id not in self.warehouses or destination not in range(20)
                or destination in warehouse_nodes or not 1 <= due_in <= 10000
                or not 1 <= weight <= max(c.capacity for c in self.couriers.values())):
            raise ValueError("Некорректный заказ")
        order = Order(f"O{len(self.orders) + 1:03}", destination, weight, self.time,
                      self.time + due_in, warehouse_id, "pending")
        self.orders.append(order)
        self.order_index[order.id] = order
        self.events.append({"time": self.time, "type": "ORDER_ARRIVAL", "order_id": order.id})

    def check_invariants(self):
        active = [c.order_id for c in self.couriers.values() if c.order_id]
        if len(active) != len(set(active)):
            raise AssertionError("Повторное назначение заказа")
        loading_or_waiting = []
        for warehouse in self.warehouses.values():
            assert len(warehouse.active) <= warehouse.slots
            assert len(warehouse.queue) == len(set(warehouse.queue))
            for courier_id in warehouse.queue:
                courier = self.couriers[courier_id]
                assert courier.state == "waiting_loading"
                assert self.order(courier.order_id).warehouse == warehouse.id
            for courier_id, until in warehouse.active.items():
                courier = self.couriers[courier_id]
                assert courier.state == "loading" and courier.until == until
                assert self.order(courier.order_id).warehouse == warehouse.id
                assert courier_id not in warehouse.queue
            loading_or_waiting.extend(warehouse.queue)
            loading_or_waiting.extend(warehouse.active)
        assert len(loading_or_waiting) == len(set(loading_or_waiting))
        for courier in self.couriers.values():
            if courier.order_id:
                order = self.order(courier.order_id)
                assert order.status == "assigned" and order.courier == courier.id
                assert order.weight <= courier.capacity
            else:
                assert courier.state == "idle"
            if courier.state in ("waiting_loading", "loading"):
                assert courier.id in loading_or_waiting
        for order in self.orders:
            if order.status == "assigned":
                assert self.couriers[order.courier].order_id == order.id
            elif order.status == "delivered":
                assert order.delivered_at is not None and order.id not in active

    def metrics(self):
        delivered = [o for o in self.orders if o.status == "delivered"]
        count = len(delivered)
        on_time = sum(o.delivered_at <= o.deadline for o in delivered)
        return {"delivered": count, "total": len(self.orders), "on_time": on_time,
                "late": count - on_time, "on_time_percent": 100 * on_time / count if count else 0.0,
                "completion_time": self.time,
                "mean_delivery_time": sum(o.delivered_at - o.release_at for o in delivered) / count if count else 0.0,
                "mean_lateness": sum(max(0, o.delivered_at - o.deadline) for o in delivered) / count if count else 0.0,
                "loading_wait": sum(o.loading_started_at - o.loading_requested_at for o in delivered) / count if count else 0.0,
                "distance": round(sum(c.distance for c in self.couriers.values()), 6),
                "messages": len(self.messages), "coordination_messages": self.coordination_messages,
                "wire_bytes": self.wire_bytes, "coordination_bytes": self.coordination_bytes,
                "evaluations": self.evaluations, "allocation_rounds": self.allocation_rounds,
                "allocation_ms": self.allocation_seconds * 1000,
                "active": sum(o.status == "assigned" for o in self.orders),
                "waiting": sum(o.status == "pending" for o in self.orders)}

    def snapshot(self):
        return {"time": self.time, "finished": self.finished, "mechanism": self.mechanism,
                "orders": [asdict(o) for o in self.orders],
                "couriers": [asdict(c) for c in self.couriers.values()],
                "warehouses": [{"id": w.id, "node": w.node, "queue": w.queue[:],
                                "active": dict(w.active), "slots": w.slots,
                                "loading_minutes": w.loading_minutes} for w in self.warehouses.values()],
                "metrics": self.metrics()}

    def trace(self):
        return {"configuration": {"seed": self.seed, "mechanism": self.mechanism,
                                  "parameters": deepcopy(self.config), "input_hash": self.input_hash},
                "snapshot": self.snapshot(), "messages": self.messages,
                "assignments": self.assignment_history, "events": self.events}
