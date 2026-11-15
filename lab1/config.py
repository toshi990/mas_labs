from math import isfinite


def default_config(scenario):
    return {
        "warehouses": [{"id": "warehouse", "node": 11, "slots": 1, "loading_minutes": 2}],
        "couriers": [
            {"id": "C1", "capacity": 2, "speed": 1.0, "node": 11},
            {"id": "C2", "capacity": 4, "speed": 1.2, "node": 11},
            {"id": "C3", "capacity": 3, "speed": 0.9, "node": 5},
            {"id": "C4", "capacity": 6, "speed": 1.1, "node": 16}
        ],
        "order_count": 30 if scenario == "busy" else 20,
        "arrival_interval": 1 if scenario == "busy" else 3,
        "batch_size": 3,
        "deadline_min": 24 if scenario == "busy" else 45,
        "deadline_max": 38 if scenario == "busy" else 70,
        "max_weight": 5,
        "service_minutes": 1
    }


def validate_config(config):
    warehouses = config["warehouses"]
    couriers = config["couriers"]
    if not 1 <= len(warehouses) <= 19 or not 1 <= len(couriers) <= 50:
        raise ValueError("Нужны 1–19 складов и 1–50 курьеров")
    ids = ["dispatcher"]
    nodes = []
    for warehouse in warehouses:
        ids.append(warehouse["id"])
        nodes.append(warehouse["node"])
        if any(type(warehouse[key]) is not int for key in ("node", "slots", "loading_minutes")):
            raise ValueError("Параметры склада должны быть целыми")
        if warehouse["node"] not in range(20):
            raise ValueError("Точка склада должна быть от 0 до 19")
        if not 1 <= warehouse["slots"] <= 50 or not 1 <= warehouse["loading_minutes"] <= 60:
            raise ValueError("Некорректные параметры погрузки")
    if len(nodes) != len(set(nodes)):
        raise ValueError("Склады должны находиться в разных точках")
    for courier in couriers:
        ids.append(courier["id"])
        if type(courier["node"]) is not int or type(courier["capacity"]) is not int:
            raise ValueError("Точка и вместимость курьера должны быть целыми")
        if courier["node"] not in range(20) or not 1 <= courier["capacity"] <= 100:
            raise ValueError("Некорректная точка или вместимость курьера")
        if not isfinite(courier["speed"]) or not 0.1 <= courier["speed"] <= 5:
            raise ValueError("Скорость должна быть от 0.1 до 5")
    if any(not isinstance(name, str) or not name for name in ids):
        raise ValueError("У каждого агента должен быть идентификатор")
    if len(ids) != len(set(ids)):
        raise ValueError("Идентификаторы агентов не должны повторяться")
    for key in ("order_count", "arrival_interval", "batch_size", "deadline_min",
                "deadline_max", "max_weight", "service_minutes"):
        if not isinstance(config[key], int):
            raise ValueError("Параметры заказов и времени должны быть целыми")
    if not 0 <= config["order_count"] <= 200 or not 0 <= config["arrival_interval"] <= 60:
        raise ValueError("Заказов должно быть 0–200, интервал — 0–60 минут")
    if not 1 <= config["batch_size"] <= 200:
        raise ValueError("Некорректный размер группы заказов")
    if not 1 <= config["deadline_min"] <= config["deadline_max"] <= 10000:
        raise ValueError("Некорректный срок доставки")
    if not 1 <= config["service_minutes"] <= 60:
        raise ValueError("Передача заказа должна занимать 1–60 минут")
    if not 1 <= config["max_weight"] <= max(c["capacity"] for c in couriers):
        raise ValueError("Максимальная масса превышает вместимость всех курьеров")
