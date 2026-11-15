from itertools import permutations
from random import Random
import unittest
from unittest.mock import patch

from engine.allocation import MECHANISMS, hungarian
from engine.model import Order, Simulation
from engine.scenarios import scenario_config


def small_config(order_count=2):
    config = scenario_config()
    config["couriers"] = config["couriers"][:2]
    config["order_count"] = order_count
    config["arrival_interval"] = 0
    return config


def fixed_orders(count):
    return [Order(f"O{i + 1:03}", i + 1, 1, 0, 60, "W1") for i in range(count)]


class AssignmentTests(unittest.TestCase):
    def test_hungarian_matches_exhaustive_search(self):
        rng = Random(27)
        for n in range(1, 5):
            for m in range(n, 6):
                for repeat in range(12):
                    matrix = [[rng.randint(-100, 300) / 7 for _ in range(m)] for _ in range(n)]
                    assignment = hungarian(matrix)
                    actual = sum(matrix[i][assignment[i]] for i in range(n))
                    expected = min(sum(matrix[i][a[i]] for i in range(n))
                                   for a in permutations(range(m), n))
                    with self.subTest(n=n, m=m, repeat=repeat):
                        self.assertEqual(len(set(assignment)), n)
                        self.assertAlmostEqual(actual, expected, places=8)

    def test_bad_matrix(self):
        self.assertEqual(hungarian([]), [])
        for matrix in ([[1], [2]], [[1, 2], [3]], [[float("nan")]], [[float("inf")]]):
            with self.assertRaises(ValueError):
                hungarian(matrix)

    def test_global_and_local_decisions_differ(self):
        costs = {"C1": {"O001": 1, "O002": 2}, "C2": {"O001": 2, "O002": 100}}
        for mechanism in MECHANISMS:
            sim = Simulation(config=small_config(), mechanism=mechanism, orders=fixed_orders(2))
            with patch.object(sim, "estimate", side_effect=lambda c, o: costs[c.id][o.id]):
                sim.step()
            assignments = {a["courier"]: a["order"] for a in sim.assignment_history}
            if mechanism == "centralized":
                self.assertEqual(assignments, {"C1": "O002", "C2": "O001"})
            else:
                self.assertEqual(assignments, {"C1": "O001", "C2": "O002"})

    def test_auction_selects_orders_instead_of_following_fifo(self):
        costs = {"C1": {"O001": 10, "O002": 1, "O003": 20},
                 "C2": {"O001": 15, "O002": 20, "O003": 1}}
        chosen = {}
        for mechanism in MECHANISMS:
            sim = Simulation(config=small_config(3), mechanism=mechanism, orders=fixed_orders(3))
            with patch.object(sim, "estimate", side_effect=lambda c, o: costs[c.id][o.id]):
                sim.step()
            chosen[mechanism] = {a["order"] for a in sim.assignment_history}
        self.assertEqual(chosen["contract_net"], {"O001", "O002"})
        self.assertEqual(chosen["auction"], {"O002", "O003"})
        self.assertEqual(chosen["centralized"], {"O002", "O003"})

    def test_capacity_and_dummy_assignments(self):
        config = small_config(2)
        config["couriers"][0]["capacity"] = 2
        orders = [Order("O001", 1, 6, 0, 60, "W1"), Order("O002", 2, 6, 0, 60, "W2")]
        for mechanism in MECHANISMS:
            sim = Simulation(config=config, mechanism=mechanism, orders=orders)
            sim.step()
            self.assertEqual(len(sim.assignment_history), 1)
            self.assertEqual(sim.assignment_history[0]["courier"], "C2")
            sim.run_until_finished()
            self.assertEqual({o.courier for o in sim.orders}, {"C2"})
            if mechanism == "contract_net":
                self.assertIn("REFUSE", {m["type"] for m in sim.messages})
            if mechanism == "auction":
                self.assertIn("NO_BID", {m["type"] for m in sim.messages})

    def test_maximum_cardinality_has_priority_over_cost(self):
        config = small_config(2)
        config["couriers"][0]["capacity"] = 1
        orders = [Order("O001", 1, 1, 0, 60, "W1"), Order("O002", 2, 6, 0, 60, "W1")]
        sim = Simulation(config=config, orders=orders)
        with patch.object(sim, "estimate", side_effect=lambda c, o: None if c.capacity < o.weight else (1000 if c.id == "C1" else 1)):
            sim.step()
        self.assertEqual(len(sim.assignment_history), 2)
        self.assertEqual(sim.order("O002").courier, "C2")


if __name__ == "__main__":
    unittest.main()
