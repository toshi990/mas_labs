from heapq import heappop, heappush


def build_network():
    nodes = []
    for row in range(4):
        for col in range(5):
            idx = row * 5 + col
            nodes.append({"id": idx, "x": 90 + col * 160, "y": 75 + row * 125,
                          "label": "Склад" if idx == 11 else f"Точка {idx + 1}"})
    edges = []
    for node in nodes:
        idx = node["id"]
        for target in (idx + 1 if idx % 5 < 4 else None,
                       idx + 5 if idx < 15 else None):
            if target is not None:
                minutes = 2 + (idx + target) % 3
                edges.append({"a": idx, "b": target, "minutes": minutes,
                              "km": round(minutes * 0.28, 2)})
    return nodes, edges


def shortest_path(edges, start, end):
    graph = {}
    for edge in edges:
        graph.setdefault(edge["a"], []).append((edge["b"], edge["minutes"]))
        graph.setdefault(edge["b"], []).append((edge["a"], edge["minutes"]))
    queue = [(0, start)]
    cost, previous = {start: 0}, {}
    while queue:
        distance, node = heappop(queue)
        if distance != cost[node]:
            continue
        if node == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return distance, list(reversed(path))
        for target, weight in graph.get(node, []):
            new_cost = distance + weight
            if new_cost < cost.get(target, float("inf")):
                cost[target], previous[target] = new_cost, node
                heappush(queue, (new_cost, target))
    raise ValueError("Между вершинами нет пути")
