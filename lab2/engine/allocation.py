from math import isfinite


MECHANISMS = {
    "centralized": "Централизованное назначение",
    "contract_net": "Контрактная сеть",
    "auction": "Аукцион заявок"
}


def hungarian(costs):
    n = len(costs)
    if n == 0:
        return []
    m = len(costs[0])
    if m < n or any(len(row) != m for row in costs):
        raise ValueError("В матрице должно быть не меньше столбцов, чем строк")
    if any(not isfinite(value) for row in costs for value in row):
        raise ValueError("Стоимость должна быть конечным числом")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    owner, previous = [0] * (m + 1), [0] * (m + 1)
    for row in range(1, n + 1):
        owner[0] = row
        column = 0
        minimum = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[column] = True
            current_row = owner[column]
            delta, next_column = float("inf"), 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                reduced = costs[current_row - 1][j - 1] - u[current_row] - v[j]
                if reduced < minimum[j]:
                    minimum[j], previous[j] = reduced, column
                if minimum[j] < delta:
                    delta, next_column = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[owner[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            column = next_column
            if owner[column] == 0:
                break
        while column:
            before = previous[column]
            owner[column] = owner[before]
            column = before
    assignment = [-1] * n
    for j in range(1, m + 1):
        if owner[j]:
            assignment[owner[j] - 1] = j - 1
    return assignment


def centralized(sim):
    couriers = sim.free_couriers()
    orders = sim.pending_orders()
    if not couriers or not orders:
        return
    values = []
    largest = 0.0
    for courier in couriers:
        row = []
        for order in orders:
            cost = sim.estimate(courier, order)
            row.append(cost)
            if cost is not None:
                largest = max(largest, cost)
        values.append(row)
    penalty = (len(couriers) + 1) * (largest + 1)
    forbidden = (len(couriers) + 1) * penalty
    matrix = []
    for row in values:
        matrix.append([forbidden if c is None else c for c in row]
                      + [penalty] * len(couriers))
    assignment = hungarian(matrix)
    sim.allocation_rounds += 1
    for i, j in enumerate(assignment):
        if j < len(orders) and values[i][j] is not None:
            sim.award(couriers[i], orders[j])


def contract_net(sim):
    for order in sim.pending_orders():
        couriers = sim.free_couriers()
        if not couriers:
            break
        sim.allocation_rounds += 1
        round_id = sim.allocation_rounds
        sim.dispatcher.proposals = {}
        for courier in couriers:
            sim.send("dispatcher", courier.id, "CFP", order.id,
                     "Оценить доставку заказа", {"round": round_id,
                     "order": sim.order_description(order)})
        proposals = sim.dispatcher.proposals
        if not proposals:
            continue
        winner = min(proposals, key=lambda name: (proposals[name], name))
        for courier_id in proposals:
            if courier_id != winner:
                sim.send("dispatcher", courier_id, "REJECT_PROPOSAL", order.id,
                         "Выбран другой исполнитель", {"round": round_id})
        sim.award(sim.couriers[winner], order)


def auction(sim):
    while True:
        couriers, orders = sim.free_couriers(), sim.pending_orders()
        if not couriers or not orders:
            return
        sim.allocation_rounds += 1
        round_id = sim.allocation_rounds
        sim.dispatcher.bids = {}
        for courier in couriers:
            sim.send("dispatcher", courier.id, "AUCTION_OPEN", "",
                     "Выбрать один заказ для заявки", {"round": round_id,
                     "orders": [sim.order_description(o) for o in orders]})
        bids = sim.dispatcher.bids
        if not bids:
            return
        winners = {}
        for courier_id, bid in bids.items():
            order_id, cost = bid
            if order_id not in winners or (cost, courier_id) < winners[order_id]:
                winners[order_id] = (cost, courier_id)
        winner_ids = {courier_id for cost, courier_id in winners.values()}
        for courier_id, (order_id, cost) in bids.items():
            if courier_id not in winner_ids:
                sim.send("dispatcher", courier_id, "BID_LOST", order_id,
                         "Заявка проиграла, можно участвовать в следующем раунде",
                         {"round": round_id})
        for order_id in sorted(winners):
            cost, courier_id = winners[order_id]
            sim.award(sim.couriers[courier_id], sim.order(order_id))


ALLOCATORS = {"centralized": centralized, "contract_net": contract_net, "auction": auction}
