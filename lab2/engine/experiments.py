from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
import csv
import json
from pathlib import Path
import platform
from statistics import mean, median, stdev
import sys

from .allocation import MECHANISMS
from .model import Simulation, generate_orders
from .scenarios import SCENARIOS, scenario_config


METRICS = ["completion_time", "mean_delivery_time", "mean_lateness", "on_time_percent",
           "distance", "loading_wait", "messages", "coordination_messages",
           "wire_bytes", "coordination_bytes", "evaluations", "allocation_rounds", "allocation_ms"]


def compare(config, seed=42, repeats=1):
    if type(repeats) is not int or repeats < 1:
        raise ValueError("Число повторений должно быть положительным")
    orders = generate_orders(config, seed)
    rows, traces = [], {}
    common_hash = None
    for offset in range(repeats):
        mechanisms = list(MECHANISMS)
        rotation = (seed + offset) % len(mechanisms)
        mechanisms = mechanisms[rotation:] + mechanisms[:rotation]
        for mechanism in mechanisms:
            sim = Simulation(seed, config, mechanism, orders)
            metrics = sim.run_until_finished()
            if common_hash is None:
                common_hash = sim.input_hash
            if sim.input_hash != common_hash:
                raise AssertionError("Механизмы получили разные исходные данные")
            row = {"seed": seed, "mechanism": mechanism, "input_hash": sim.input_hash, **metrics}
            rows.append(row)
            if offset == 0:
                traces[mechanism] = sim.trace()
    result = []
    for mechanism in MECHANISMS:
        copies = [r for r in rows if r["mechanism"] == mechanism]
        first = dict(copies[0])
        for row in copies[1:]:
            for key, value in first.items():
                if key != "allocation_ms" and row[key] != value:
                    raise AssertionError("Повторный запуск изменил результат")
        first["allocation_ms"] = median(r["allocation_ms"] for r in copies)
        first["timing_repeats"] = repeats
        result.append(first)
    return result, traces


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def source_hash():
    root = Path(__file__).resolve().parents[1]
    digest = sha256()
    names = ["main.py", "run_experiments.py"] + [str(p.relative_to(root)) for p in sorted((root / "lab1").glob("*.py"))]
    for name in names:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def run_experiments(output, seed_count=30, repeats=3, scenario_names=None):
    if type(seed_count) is not int or seed_count < 1:
        raise ValueError("Количество seed должно быть положительным")
    scenario_names = list(SCENARIOS) if scenario_names is None else list(scenario_names)
    if not scenario_names or any(name not in SCENARIOS for name in scenario_names):
        raise ValueError("Некорректный набор сценариев")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, inputs, sample_traces = [], [], {}
    for scenario in scenario_names:
        config = scenario_config(scenario)
        for seed in range(seed_count):
            values, traces = compare(config, seed, repeats)
            for value in values:
                rows.append({"scenario": scenario, **value})
            inputs.append({"scenario": scenario, "seed": seed, "config": config,
                           "input_hash": values[0]["input_hash"],
                           "orders": [asdict(o) for o in generate_orders(config, seed)]})
            if seed == 0:
                sample_traces[scenario] = traces
        print(f"{SCENARIOS[scenario]}: {seed_count} наборов заказов, 3 механизма")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["scenario"], row["mechanism"]].append(row)
    summary = []
    for (scenario, mechanism), group in grouped.items():
        item = {"scenario": scenario, "mechanism": mechanism, "runs": len(group)}
        for metric in METRICS:
            values = [r[metric] for r in group]
            item[metric + "_mean"] = mean(values)
            item[metric + "_sd"] = stdev(values) if len(values) > 1 else 0.0
        summary.append(item)
    differences = []
    for scenario in scenario_names:
        for first, second in [("centralized", "contract_net"), ("centralized", "auction"),
                              ("contract_net", "auction")]:
            pairs = {m: {r["seed"]: r for r in rows if r["scenario"] == scenario and r["mechanism"] == m}
                     for m in (first, second)}
            item = {"scenario": scenario, "first": first, "second": second}
            for metric in ("completion_time", "mean_delivery_time", "mean_lateness", "on_time_percent", "distance"):
                delta = [pairs[second][s][metric] - pairs[first][s][metric] for s in range(seed_count)]
                average = mean(delta)
                half_width = 1.96 * stdev(delta) / len(delta) ** 0.5 if len(delta) > 1 else 0.0
                item[metric] = {"mean_delta_second_minus_first": average,
                                "normal_95_interval": [average - half_width, average + half_width]}
            differences.append(item)
    metadata = {"seed_values": list(range(seed_count)), "timing_repeats": repeats,
                "scenarios": {name: scenario_config(name) for name in scenario_names},
                "mechanisms": MECHANISMS, "scenario_count": len(scenario_names),
                "unique_runs": len(rows), "executions": len(rows) * repeats,
                "python": sys.version.split()[0], "platform": platform.platform(),
                "source_sha256": source_hash(),
                "timing": "median of repeated allocation times; mechanism order rotated by seed and repeat",
                "message_size": "UTF-8 JSON, ensure_ascii=False, separators=(',', ':'); no transport headers"}
    write_csv(output / "runs.csv", rows)
    write_csv(output / "summary.csv", summary)
    for name, value in [("metadata.json", metadata), ("inputs.json", inputs),
                        ("paired_differences.json", differences), ("sample_traces.json", sample_traces)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows, summary


def print_comparison(rows):
    for row in rows:
        print(f"\n{MECHANISMS[row['mechanism']]}")
        print(f"Все заказы: {row['completion_time']:.0f} мин. Вовремя: {row['on_time_percent']:.1f}%.")
        print(f"Средняя доставка: {row['mean_delivery_time']:.2f} мин. "
              f"Среднее опоздание: {row['mean_lateness']:.2f} мин.")
        print(f"Пробег: {row['distance']:.2f} км. Сообщений: {row['messages']}.")
        print(f"Распределение: {row['coordination_messages']} сообщений, "
              f"{row['coordination_bytes'] / 1024:.2f} КиБ, {row['allocation_ms']:.2f} мс.")
