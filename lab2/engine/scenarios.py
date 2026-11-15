from copy import deepcopy


SCENARIOS = {
    "normal": "Обычная нагрузка",
    "burst": "Пакет заказов",
    "loading": "Медленная погрузка",
    "heterogeneous": "Разные вместимости"
}


def scenario_config(name="normal"):
    if name not in SCENARIOS:
        raise ValueError("Неизвестный сценарий")
    config = {
        "warehouses": [
            {"id": "W1", "node": 0, "slots": 1, "loading_minutes": 2},
            {"id": "W2", "node": 19, "slots": 1, "loading_minutes": 2}
        ],
        "couriers": [
            {"id": "C1", "node": 0, "capacity": 6, "speed": 1.0},
            {"id": "C2", "node": 19, "capacity": 6, "speed": 1.2},
            {"id": "C3", "node": 0, "capacity": 6, "speed": 0.8},
            {"id": "C4", "node": 19, "capacity": 6, "speed": 1.1}
        ],
        "order_count": 40, "arrival_interval": 3, "batch_size": 2,
        "deadline_min": 45, "deadline_max": 70, "max_weight": 6, "service_minutes": 1
    }
    if name == "burst":
        config["arrival_interval"] = 0
    elif name == "loading":
        for warehouse in config["warehouses"]:
            warehouse["loading_minutes"] = 8
    elif name == "heterogeneous":
        for courier, capacity in zip(config["couriers"], [2, 4, 3, 6]):
            courier["capacity"] = capacity
    return deepcopy(config)
