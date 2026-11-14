from dataclasses import asdict, dataclass, field
from copy import deepcopy
from random import Random

from .config import default_config, validate_config
from .network import build_network, shortest_path


@dataclass
class Order:
    id: str
    destination: int
    weight: int
    release_at: int
    deadline: int
    status: str = "unreleased"
    courier: str | None = None
    delivered_at: int | None = None
    warehouse: str = "warehouse"


@dataclass
class Courier:
    id: str
    name: str
    color: str
    capacity: int
    speed: float
    node: int
    state: str = "idle"
    order_id: str | None = None
    route: list[int] = field(default_factory=list)
    edge_remaining: float = 0
    edge_total: float = 0
    until: int = 0
    distance: float = 0
    completed: int = 0

    def receive(self, message, sim):
        order = sim.order(message["order_id"]) if message.get("order_id") else None
        if message["type"] == "TASK_OFFER":
            if self.state != "idle" or order.weight > self.capacity:
                sim.send(self.id, "dispatcher", "REFUSE", order.id, "Задание недоступно")
                return
            self.state, self.order_id = "reserved", order.id
            sim.send(self.id, "dispatcher", "ACCEPT", order.id, "Готов принять заказ")
        elif message["type"] == "TASK_COMMIT":
            if self.state != "reserved" or self.order_id != order.id:
                raise ValueError("Назначение не подтверждено курьером")
            warehouse = sim.warehouses[order.warehouse]
            sim.set_route(self, warehouse.node, "to_warehouse")
        elif message["type"] == "LOAD_START":
            warehouse = sim.warehouses[message["from"]]
            self.state, self.until = "loading", sim.time + warehouse.loading_minutes
        elif message["type"] == "LOAD_DONE":
            sim.set_route(self, order.destination, "delivering")


class Warehouse:
    def __init__(self, warehouse_id="warehouse", node=11, slots=1, loading_minutes=2):
        self.id = warehouse_id
        self.node = node
        self.slots = slots
        self.loading_minutes = loading_minutes
        self.queue = []
        self.active = {}

    @property
    def loading(self):
        return next(iter(self.active), None)

    def receive(self, message, sim):
        if message["type"] == "LOAD_REQUEST":
            courier = sim.couriers[message["from"]]
            if courier.id not in self.queue and courier.id not in self.active:
                self.queue.append(courier.id)
                courier.state = "waiting_loading"
                sim.send(self.id, courier.id, "QUEUED", courier.order_id,
                         f"Место в очереди: {len(self.queue)}")

    def step(self, sim):
        for courier_id in list(self.active):
            courier = sim.couriers[courier_id]
            if sim.time >= courier.until:
                del self.active[courier_id]
                sim.send(self.id, courier.id, "LOAD_DONE", courier.order_id,
                         "Погрузка завершена, можно выезжать")
        while len(self.active) < self.slots and self.queue:
            courier_id = self.queue.pop(0)
            self.active[courier_id] = sim.time + self.loading_minutes
            courier = sim.couriers[courier_id]
            sim.send(self.id, courier.id, "LOAD_START", courier.order_id,
                     f"Место погрузки выделено на {self.loading_minutes} мин")


class Dispatcher:
    id = "dispatcher"

    def receive(self, message, sim):
        if message["type"] == "ACCEPT":
            order = sim.order(message["order_id"])
            courier = sim.couriers[message["from"]]
            if order.status != "pending":
                raise ValueError("Заказ уже назначен")
            order.status, order.courier = "assigned", courier.id
            sim.send(self.id, courier.id, "TASK_COMMIT", order.id,
                     f"Назначение подтверждено: точка {order.destination + 1}")
        elif message["type"] == "DELIVERED":
            order = sim.order(message["order_id"])
            if order.courier != message["from"]:
                raise ValueError("Отчёт отправлен чужим курьером")
            order.status, order.delivered_at = "delivered", sim.time

    def assign(self, sim):
        for order in sorted(sim.orders, key=lambda item: (item.release_at, item.id)):
            if order.status != "pending":
                continue
            candidates = [c for c in sim.couriers.values()
                          if c.state == "idle" and c.capacity >= order.weight]
            if not candidates:
                continue
            def score(courier):
                warehouse = sim.warehouses[order.warehouse]
                to_depot, _ = shortest_path(sim.edges, courier.node, warehouse.node)
                to_client, _ = shortest_path(sim.edges, warehouse.node, order.destination)
                return ((to_depot + to_client) / courier.speed, courier.id)
            winner = min(candidates, key=score)
            sim.send(self.id, winner.id, "TASK_OFFER", order.id,
                     f"Доставить {order.weight} кг, срок {sim.clock(order.deadline)}")


class Simulation:
    warehouse_node = 11
    loading_minutes = 2

    def __init__(self, seed=42, scenario="normal", config=None):
        if scenario not in ("normal", "busy"):
            raise ValueError("Неизвестный сценарий")
        self.seed, self.scenario, self.time = seed, scenario, 0
        self.custom = config is not None
        if config is None:
            config = default_config(scenario)
        validate_config(config)
        self.config = deepcopy(config)
        self.nodes, self.edges = build_network()
        self.warehouses = {}
        for item in config["warehouses"]:
            warehouse = Warehouse(item["id"], item["node"], item["slots"], item["loading_minutes"])
            self.warehouses[warehouse.id] = warehouse
        if self.custom:
            for node in self.nodes:
                node["label"] = f"Точка {node['id'] + 1}"
            for warehouse in self.warehouses.values():
                self.nodes[warehouse.node]["label"] = f"Склад {warehouse.id}"
        self.warehouse = next(iter(self.warehouses.values()))
        self.warehouse_node = self.warehouse.node
        self.loading_minutes = self.warehouse.loading_minutes
        self.dispatcher = Dispatcher()
        self.messages, self.events = [], []
        self.couriers = {}
        colors = ["#287568", "#517ac2", "#bb7b32", "#9466ab"]
        for index, item in enumerate(config["couriers"]):
            courier = Courier(item["id"], f"Курьер {index + 1:02}", colors[index % 4],
                              item["capacity"], item["speed"], item["node"])
            self.couriers[courier.id] = courier
        rng = Random(seed)
        self.orders = []
        warehouse_nodes = [w.node for w in self.warehouses.values()]
        addresses = [n["id"] for n in self.nodes if n["id"] not in warehouse_nodes]
        for idx in range(config["order_count"]):
            destination = rng.choice(addresses)
            release = (idx // config["batch_size"]) * config["arrival_interval"]
            deadline = release + rng.randint(config["deadline_min"], config["deadline_max"])
            weight = rng.randint(1, config["max_weight"])
            warehouse_id = self.warehouse.id
            if len(self.warehouses) > 1:
                warehouse_id = rng.choice(list(self.warehouses))
            self.orders.append(Order(f"O{idx + 1:03}", destination, weight, release,
                                     deadline, warehouse=warehouse_id))
        self.release_orders()

    @staticmethod
    def clock(value):
        return f"{9 + value // 60:02}:{value % 60:02}"

    def order(self, order_id):
        return next(order for order in self.orders if order.id == order_id)

    def send(self, sender, recipient, kind, order_id, text):
        message = {"id": len(self.messages) + 1, "time": self.time, "from": sender,
                   "to": recipient, "type": kind, "order_id": order_id, "text": text}
        self.messages.append(message)
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
        _, courier.route = shortest_path(self.edges, courier.node, target)
        courier.state = state
        courier.edge_remaining = courier.edge_total = 0
        if len(courier.route) == 1:
            self.arrive(courier)

    def arrive(self, courier):
        courier.route = []
        if courier.state == "to_warehouse":
            warehouse_id = self.order(courier.order_id).warehouse
            self.send(courier.id, warehouse_id, "LOAD_REQUEST", courier.order_id,
                      "Прибыл на склад, запрашиваю погрузку")
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
                courier.edge_remaining = courier.edge_total = 0
                courier.node = b
                courier.route.pop(0)
                if len(courier.route) == 1:
                    self.arrive(courier)
                    break

    def step(self, count=1):
        for _ in range(count):
            if self.finished:
                break
            self.time += 1
            self.release_orders()
            for courier in self.couriers.values():
                if courier.state in ("to_warehouse", "delivering"):
                    self.move(courier)
                elif courier.state == "servicing" and self.time >= courier.until:
                    self.send(courier.id, "dispatcher", "DELIVERED", courier.order_id,
                              "Заказ передан получателю")
                    courier.completed += 1
                    courier.state, courier.order_id = "idle", None
            for warehouse in self.warehouses.values():
                warehouse.step(self)
            self.dispatcher.assign(self)
            self.check_invariants()
        return self.snapshot()

    @property
    def finished(self):
        return all(o.status == "delivered" for o in self.orders)

    def add_order(self, destination, weight, due_in, warehouse_id=None):
        if warehouse_id is None:
            warehouse_id = self.warehouse.id
        if warehouse_id not in self.warehouses:
            raise ValueError("Неизвестный склад")
        warehouse_nodes = [w.node for w in self.warehouses.values()]
        if destination in warehouse_nodes or destination not in range(len(self.nodes)):
            raise ValueError("Выберите адрес получателя на карте")
        capacity = max(c.capacity for c in self.couriers.values())
        if not 1 <= weight <= capacity or not 1 <= due_in <= 10000:
            raise ValueError("Масса превышает вместимость или неверно указан срок")
        self.orders.append(Order(f"O{len(self.orders) + 1:03}", destination, weight,
                                 self.time, self.time + due_in, "pending", warehouse=warehouse_id))
        self.events.append({"time": self.time, "type": "ORDER_ARRIVAL", "order_id": self.orders[-1].id})
        return self.snapshot()

    def check_invariants(self):
        active = [c.order_id for c in self.couriers.values() if c.order_id]
        assert len(active) == len(set(active)), "Один заказ назначен нескольким курьерам"
        waiting_or_loading = []
        for warehouse in self.warehouses.values():
            assert len(warehouse.queue) == len(set(warehouse.queue))
            assert len(warehouse.active) <= warehouse.slots
            for courier_id in warehouse.queue:
                courier = self.couriers[courier_id]
                assert courier.state == "waiting_loading"
                assert self.order(courier.order_id).warehouse == warehouse.id
            for courier_id in warehouse.active:
                courier = self.couriers[courier_id]
                assert courier.state == "loading"
                assert courier_id not in warehouse.queue
                assert self.order(courier.order_id).warehouse == warehouse.id
                assert courier.until == warehouse.active[courier_id]
            waiting_or_loading.extend(warehouse.queue)
            waiting_or_loading.extend(warehouse.active)
        assert len(waiting_or_loading) == len(set(waiting_or_loading))
        for courier in self.couriers.values():
            if courier.order_id:
                order = self.order(courier.order_id)
                assert order.weight <= courier.capacity
                assert order.courier == courier.id and order.status == "assigned"
            else:
                assert courier.state == "idle"
            if courier.state in ("waiting_loading", "loading"):
                assert courier.id in waiting_or_loading
        for order in self.orders:
            if order.status == "assigned":
                assert self.couriers[order.courier].order_id == order.id
            if order.status == "delivered":
                assert order.delivered_at is not None and order.id not in active

    def snapshot(self):
        couriers = []
        for courier in self.couriers.values():
            item = asdict(courier)
            position = dict(self.nodes[courier.node])
            if len(courier.route) > 1 and courier.edge_total:
                target = self.nodes[courier.route[1]]
                fraction = 1 - courier.edge_remaining / courier.edge_total
                position["x"] += (target["x"] - position["x"]) * fraction
                position["y"] += (target["y"] - position["y"]) * fraction
            item["position"] = position
            couriers.append(item)
        delivered = [o for o in self.orders if o.status == "delivered"]
        on_time = sum(o.delivered_at <= o.deadline for o in delivered)
        orders = [asdict(o) for o in self.orders]
        if not self.custom:
            for order in orders:
                del order["warehouse"]
        result = {"time": self.time, "clock": self.clock(self.time), "seed": self.seed,
                "scenario": self.scenario, "finished": self.finished,
                "nodes": self.nodes, "edges": self.edges, "couriers": couriers,
                "orders": orders, "messages": self.messages[-160:],
                "warehouse": {"node": self.warehouse_node, "queue": self.warehouse.queue[:],
                              "loading": self.warehouse.loading, "slots": self.warehouse.slots},
                "metrics": {"delivered": len(delivered), "total": len(self.orders),
                            "on_time": on_time, "late": len(delivered) - on_time,
                            "distance": round(sum(c.distance for c in self.couriers.values()), 2),
                            "messages": len(self.messages),
                            "active": sum(o.status == "assigned" for o in self.orders),
                            "waiting": sum(o.status == "pending" for o in self.orders)}}
        if self.custom:
            result["warehouses"] = []
            for warehouse in self.warehouses.values():
                result["warehouses"].append({"id": warehouse.id, "node": warehouse.node,
                    "slots": warehouse.slots, "loading_minutes": warehouse.loading_minutes,
                    "queue": warehouse.queue[:], "active": dict(warehouse.active)})
        return result

    def trace(self):
        configuration = {"seed": self.seed, "scenario": self.scenario,
                         "allocation": "centralized", "warehouse_slots": self.warehouse.slots}
        if self.custom:
            configuration["parameters"] = deepcopy(self.config)
        return {"configuration": configuration,
                "snapshot": self.snapshot(), "messages": self.messages, "events": self.events}
