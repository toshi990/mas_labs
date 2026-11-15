from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from engine.allocation import MECHANISMS
from engine.experiments import compare, run_experiments
from engine.model import Simulation, generate_orders
from engine.scenarios import SCENARIOS, scenario_config


ROOT = Path(__file__).resolve().parents[1]


class ModelTests(unittest.TestCase):
    def test_all_scenarios_finish_and_keep_constraints(self):
        for scenario in SCENARIOS:
            config = scenario_config(scenario)
            for seed in (0, 7, 42):
                orders = generate_orders(config, seed)
                hashes = set()
                for mechanism in MECHANISMS:
                    with self.subTest(scenario=scenario, seed=seed, mechanism=mechanism):
                        sim = Simulation(seed, config, mechanism, orders)
                        sim.run_until_finished()
                        sim.check_invariants()
                        self.assertEqual(sim.metrics()["delivered"], config["order_count"])
                        hashes.add(sim.input_hash)
                self.assertEqual(len(hashes), 1)
                self.assertTrue(all(o.status == "unreleased" for o in orders))

    def test_replay_and_byte_accounting(self):
        for mechanism in MECHANISMS:
            first = Simulation(7, scenario_config(), mechanism)
            second = Simulation(7, scenario_config(), mechanism)
            first.run_until_finished()
            second.run_until_finished()
            self.assertEqual(first.messages, second.messages)
            a, b = first.metrics(), second.metrics()
            del a["allocation_ms"], b["allocation_ms"]
            self.assertEqual(a, b)
            actual_bytes = sum(len(json.dumps(m, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
                               for m in first.messages)
            self.assertEqual(actual_bytes, a["wire_bytes"])
            self.assertEqual(a["messages"] - a["coordination_messages"], 5 * a["total"])
            if mechanism == "centralized":
                self.assertEqual(a["messages"], 8 * a["total"])

    def test_no_knowledge_of_future_orders(self):
        config = scenario_config()
        config["order_count"] = 1
        orders = generate_orders(config, 1)
        orders[0].release_at, orders[0].deadline = 10, 60
        for mechanism in MECHANISMS:
            sim = Simulation(1, config, mechanism, orders)
            sim.step(9)
            self.assertEqual(sim.messages, [])
            sim.step()
            self.assertEqual(sim.assignment_history[0]["time"], 10)

    def test_zero_orders_and_limit(self):
        config = scenario_config()
        config["order_count"] = 0
        for mechanism in MECHANISMS:
            sim = Simulation(config=config, mechanism=mechanism)
            sim.run_until_finished()
            self.assertEqual(sim.metrics()["messages"], 0)
            self.assertEqual(sim.time, 0)
        with self.assertRaises(RuntimeError):
            Simulation(config=scenario_config()).run_until_finished(1)

    def test_parallel_loading_and_manual_order(self):
        config = scenario_config()
        config["order_count"] = 0
        config["warehouses"][0]["slots"] = 2
        for courier in config["couriers"]:
            courier["node"] = 0
        for mechanism in MECHANISMS:
            sim = Simulation(config=config, mechanism=mechanism)
            for target in (1, 2, 3, 4):
                sim.add_order(target, 1, 50, "W1")
            sim.step(2)
            self.assertEqual(len(sim.warehouses["W1"].active), 2)
            self.assertEqual(len(sim.warehouses["W1"].queue), 2)
            sim.run_until_finished()
            self.assertEqual(sim.metrics()["delivered"], 4)

    def test_bad_inputs(self):
        config = scenario_config()
        orders = generate_orders(config, 1)
        orders[0].weight = 100
        with self.assertRaises(ValueError):
            Simulation(config=config, orders=orders)
        with self.assertRaises(ValueError):
            Simulation(mechanism="missing")
        with self.assertRaises(ValueError):
            Simulation().step(-1)
        sim = Simulation(config=config)
        before = [asdict(o) for o in sim.orders]
        with self.assertRaises(ValueError):
            sim.add_order(0, 1, 10, "W1")
        self.assertEqual(before, [asdict(o) for o in sim.orders])

    def test_comparison_and_export(self):
        config = scenario_config()
        config["order_count"] = 6
        rows, traces = compare(config, seed=1, repeats=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["input_hash"] for r in rows}), 1)
        self.assertEqual(set(traces), set(MECHANISMS))
        with tempfile.TemporaryDirectory() as temp:
            rows, summary = run_experiments(temp, seed_count=2, repeats=1, scenario_names=["normal"])
            self.assertEqual(len(rows), 6)
            self.assertEqual(len(summary), 3)
            inputs = json.loads((Path(temp) / "inputs.json").read_text())
            self.assertEqual(len(inputs), 2)
            metadata = json.loads((Path(temp) / "metadata.json").read_text())
            self.assertEqual(metadata["executions"], 6)

    def test_console_comparison(self):
        result = subprocess.run([sys.executable, "main.py"], input="2\n1\n42\n0\n", cwd=ROOT,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for title in MECHANISMS.values():
            self.assertIn(title, result.stdout)

    def test_console_steps(self):
        for number in (1, 2, 3):
            data = f"1\n1\n42\n{number}\n2\n4\n6\n0\n0\n"
            result = subprocess.run([sys.executable, "main.py"], input=data, cwd=ROOT,
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Доставлено: 40 из 40", result.stdout)


if __name__ == "__main__":
    unittest.main()
