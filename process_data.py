"""
Orchestrator: run all data processors to turn raw downloads into
compact distribution tables in data/processed/.

Usage:
    uv run python process_data.py [--only NAME ...] [--list]
"""

import argparse
import importlib
import time
import traceback
from pathlib import Path

PROCESSORS = {
    "postcodes":           "processors.postcodes",
    "driver_demographics": "processors.driver_demographics",
    "marital_status":      "processors.marital_status",
    "occupation":          "processors.occupation",
    "names":               "processors.names",
    "vehicles":            "processors.vehicles",
    "claims":              "processors.claims",
    "convictions":         "processors.convictions",
    "accidents":           "processors.accidents",
    "deprivation":         "processors.deprivation",
    "mot_mileage":         "processors.mot_mileage",
}


def main():
    parser = argparse.ArgumentParser(description="Process raw data into distribution tables")
    parser.add_argument("--only", nargs="+", choices=list(PROCESSORS.keys()),
                        help="Run only specific processors")
    parser.add_argument("--list", action="store_true", help="List available processors")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable processors:\n")
        for name, module in PROCESSORS.items():
            print(f"  {name:25s} ({module})")
        print()
        return

    to_run = {k: PROCESSORS[k] for k in args.only} if args.only else PROCESSORS

    print(f"\n{'=' * 60}")
    print(f"Processing {len(to_run)} dataset(s)")
    print(f"{'=' * 60}")

    results = {}
    for name, module_path in to_run.items():
        print(f"\n{'─' * 60}")
        print(f"[{name}]")
        print(f"{'─' * 60}")
        t0 = time.time()
        try:
            mod = importlib.import_module(module_path)
            mod.process()
            elapsed = time.time() - t0
            results[name] = ("✓", f"{elapsed:.1f}s")
            print(f"  Done in {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - t0
            results[name] = ("✗", str(e))
            print(f"  FAILED ({elapsed:.1f}s): {e}")
            traceback.print_exc()

    # Summary
    print(f"\n{'=' * 60}")
    print("Summary:")
    print(f"{'=' * 60}")
    for name, (status, detail) in results.items():
        print(f"  {status} {name:25s} {detail}")

    succeeded = sum(1 for s, _ in results.values() if s == "✓")
    failed = sum(1 for s, _ in results.values() if s == "✗")
    print(f"\n  {succeeded} succeeded, {failed} failed")

    # List output files
    proc_dir = Path(__file__).parent / "data" / "processed"
    if proc_dir.exists():
        parquets = sorted(proc_dir.glob("*.parquet"))
        if parquets:
            print(f"\n  Output files in {proc_dir}:")
            for p in parquets:
                size_kb = p.stat().st_size / 1024
                print(f"    {p.name:45s} {size_kb:>8.1f} KB")


if __name__ == "__main__":
    main()
