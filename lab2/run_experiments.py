import argparse
from pathlib import Path

from engine.experiments import run_experiments
from engine.scenarios import SCENARIOS


def main():
    parser = argparse.ArgumentParser(description="Сравнение трёх механизмов распределения")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", nargs="+", choices=list(SCENARIOS))
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results" / "lr2")
    args = parser.parse_args()
    run_experiments(args.output, args.seeds, args.repeats, args.scenarios)
    print(f"\nРезультаты: {args.output.resolve()}")


if __name__ == "__main__":
    main()
