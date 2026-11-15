import json
from pathlib import Path

from .model import Simulation


STATES = {
    "idle": "свободен",
    "reserved": "принимает заказ",
    "to_warehouse": "едет на склад",
    "waiting_loading": "ждёт погрузку",
    "loading": "загружается",
    "delivering": "доставляет",
    "servicing": "передаёт заказ"
}

ORDER_STATES = {
    "unreleased": "ещё не поступил",
    "pending": "ожидает назначения",
    "assigned": "в работе",
    "delivered": "доставлен"
}


def read_int(prompt, default, minimum, maximum):
    while True:
        text = input(f"{prompt} [{default}]: ").strip()
        if text == "":
            return default
        try:
            value = int(text)
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"Введите целое число от {minimum} до {maximum}.")


def read_speed():
    while True:
        text = input("Скорость (1 — обычная, 2 — вдвое быстрее) [1]: ").strip()
        if text == "":
            return 1.0
        try:
            speed = float(text.replace(",", "."))
            if 0.1 <= speed <= 5:
                return speed
        except ValueError:
            pass
        print("Введите число от 0.1 до 5.")


def read_config():
    print("Настройка доставки. Enter оставляет значение в скобках.")
    print("В городе 20 точек. Один шаг — одна минута.")
    config = {"warehouses": [], "couriers": [], "batch_size": 1}
    warehouse_count = read_int("Количество складов", 1, 1, 19)
    used_nodes = []
    for index in range(warehouse_count):
        print(f"\nСклад W{index + 1}")
        default_node = 12
        while default_node in used_nodes:
            default_node = default_node % 20 + 1
        while True:
            node = read_int("Точка склада", default_node, 1, 20)
            if node not in used_nodes:
                break
            print("В этой точке уже есть склад.")
        used_nodes.append(node)
        slots = read_int("Мест погрузки", 1, 1, 10)
        loading = read_int("Погрузка одного заказа, мин", 2, 1, 60)
        config["warehouses"].append({"id": f"W{index + 1}", "node": node - 1,
                                      "slots": slots, "loading_minutes": loading})
    courier_count = read_int("\nКоличество курьеров", 4, 1, 50)
    for index in range(courier_count):
        print(f"\nКурьер C{index + 1}")
        capacity = read_int("Вместимость, кг", 4, 1, 100)
        speed = read_speed()
        warehouse = config["warehouses"][index % warehouse_count]
        node = read_int("Начальная точка", warehouse["node"] + 1, 1, 20)
        config["couriers"].append({"id": f"C{index + 1}", "capacity": capacity,
                                    "speed": speed, "node": node - 1})
    print("\nЗаказы")
    config["order_count"] = read_int("Количество заказов", 10, 0, 200)
    config["arrival_interval"] = read_int("Интервал поступления, мин (0 — все сразу)", 2, 0, 60)
    deadline = read_int("Срок доставки от поступления, мин", 45, 1, 10000)
    config["deadline_min"] = deadline
    config["deadline_max"] = deadline
    max_capacity = max(c["capacity"] for c in config["couriers"])
    config["max_weight"] = read_int("Максимальная масса заказа, кг", min(4, max_capacity), 1, max_capacity)
    config["service_minutes"] = read_int("Передача получателю, мин", 1, 1, 60)
    seed = read_int("Seed для повторения сценария", 42, 0, 1000000)
    return config, seed


def show_summary(sim):
    metrics = sim.snapshot()["metrics"]
    print(f"\nВремя: {sim.time} мин ({sim.clock(sim.time)})")
    print(f"Доставлено: {metrics['delivered']} из {metrics['total']}. "
          f"В работе: {metrics['active']}. Ожидают: {metrics['waiting']}.")
    print(f"Вовремя: {metrics['on_time']}. С опозданием: {metrics['late']}.")
    print(f"Пробег: {metrics['distance']:.2f} км. Сообщений: {metrics['messages']}.")
    if sim.finished:
        print("Все заказы выполнены.")


def show_state(sim):
    show_summary(sim)
    print("\nСклады")
    for warehouse in sim.warehouses.values():
        loading = ", ".join(warehouse.active) or "нет"
        queue = ", ".join(warehouse.queue) or "нет"
        print(f"{warehouse.id}: точка {warehouse.node + 1}, "
              f"мест {warehouse.slots}, погрузка {warehouse.loading_minutes} мин")
        print(f"  Загружаются: {loading}. Очередь: {queue}.")
    print("\nКурьеры")
    for courier in sim.couriers.values():
        order = courier.order_id or "нет"
        print(f"{courier.id}: {STATES[courier.state]}, заказ {order}, "
              f"вместимость {courier.capacity} кг, скорость {courier.speed:g}")
        if len(courier.route) > 1 and courier.edge_remaining > 0:
            print(f"  Между точками {courier.node + 1} и {courier.route[1] + 1}, "
                  f"до следующей {courier.edge_remaining:.1f} мин")
        else:
            print(f"  Точка {courier.node + 1}")
        if courier.route:
            route = " -> ".join(str(node + 1) for node in courier.route)
            print(f"  Маршрут: {route}")
        print(f"  Выполнено: {courier.completed}, пробег: {courier.distance:.2f} км")


def show_orders(sim):
    if not sim.orders:
        print("Заказов нет.")
        return
    for order in sim.orders:
        courier = order.courier or "нет"
        print(f"{order.id}: склад {order.warehouse}, точка {order.destination + 1}, "
              f"{order.weight} кг, {ORDER_STATES[order.status]}")
        print(f"  Поступление: {order.release_at} мин, срок: {order.deadline} мин, курьер: {courier}")
        if order.delivered_at is not None:
            late = max(0, order.delivered_at - order.deadline)
            print(f"  Доставка: {order.delivered_at} мин, опоздание: {late} мин")


def show_messages(messages):
    if not messages:
        print("Сообщений нет.")
    for message in messages:
        print(f"{message['time']} мин: {message['from']} -> {message['to']} "
              f"{message['type']} {message['order_id']}")
        print(f"  {message['text']}")


def add_order(sim):
    warehouses = list(sim.warehouses.values())
    for index, warehouse in enumerate(warehouses, 1):
        print(f"{index}. {warehouse.id}, точка {warehouse.node + 1}")
    number = read_int("Номер склада отправления", 1, 1, len(warehouses))
    warehouse_nodes = [w.node + 1 for w in warehouses]
    destination = 1
    while destination in warehouse_nodes:
        destination += 1
    while True:
        node = read_int("Точка получателя", destination, 1, 20)
        if node not in warehouse_nodes:
            break
        print("Выберите точку, в которой нет склада.")
    max_capacity = max(c.capacity for c in sim.couriers.values())
    weight = read_int("Масса, кг", 1, 1, max_capacity)
    due_in = read_int("Доставить в течение, мин", 45, 1, 10000)
    sim.add_order(node - 1, weight, due_in, warehouses[number - 1].id)
    print(f"Добавлен заказ {sim.orders[-1].id}.")


def run():
    config, seed = read_config()
    sim = Simulation(seed=seed, config=config)
    show_state(sim)
    while True:
        print("\n1. Состояние складов и курьеров")
        print("2. Один шаг")
        print("3. Несколько шагов")
        print("4. Заказы")
        print("5. Последние сообщения")
        print("6. Выполнить все заказы")
        print("7. Добавить заказ")
        print("8. Сохранить трассу")
        print("0. Выход")
        choice = input("Выбор: ").strip()
        if choice == "0":
            break
        if choice == "1":
            show_state(sim)
        elif choice == "2":
            start = len(sim.messages)
            sim.step()
            show_summary(sim)
            show_messages(sim.messages[start:])
        elif choice == "3":
            count = read_int("Количество шагов", 5, 1, 10000)
            sim.step(count)
            show_summary(sim)
        elif choice == "4":
            show_orders(sim)
        elif choice == "5":
            count = read_int("Количество сообщений", 20, 1, 1000)
            show_messages(sim.messages[-count:])
        elif choice == "6":
            sim.step(100000)
            show_summary(sim)
            if not sim.finished:
                print("Достигнут предел 100000 шагов. Можно продолжить выполнение.")
        elif choice == "7":
            add_order(sim)
        elif choice == "8":
            path = Path(__file__).resolve().parents[1] / "output" / "console_trace.json"
            try:
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(sim.trace(), ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Трасса сохранена: {path}")
            except OSError as error:
                print(f"Не удалось сохранить трассу: {error}")
        else:
            print("Выберите пункт от 0 до 8.")


def main():
    try:
        run()
    except (EOFError, KeyboardInterrupt):
        print("\nРабота завершена.")
