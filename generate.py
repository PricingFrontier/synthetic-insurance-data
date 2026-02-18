"""
Generate synthetic UK motor insurance quote request JSONs.

Usage:
    uv run python generate.py --n 10 --seed 42 --output data/output/quotes.jsonl
    uv run python generate.py --n 1 --pretty           # single pretty-printed quote to stdout
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from generator import QuoteGenerator


class NumpyEncoder(json.JSONEncoder):
    """Handle numpy types in JSON serialization."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic motor insurance quotes")
    parser.add_argument("--n", type=int, default=10, help="Number of quotes to generate")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (.jsonl or .json). Prints to stdout if not specified.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--format", choices=["jsonl", "json"], default="jsonl",
                        help="Output format: jsonl (one JSON per line) or json (single array)")
    args = parser.parse_args()

    print(f"Loading distribution data...", file=sys.stderr)
    t0 = time.time()
    gen = QuoteGenerator(seed=args.seed)
    load_time = time.time() - t0
    print(f"  Loaded in {load_time:.1f}s", file=sys.stderr)

    print(f"Generating {args.n} quotes...", file=sys.stderr)
    t1 = time.time()
    quotes = gen.generate(args.n)
    gen_time = time.time() - t1
    print(f"  Generated in {gen_time:.1f}s ({args.n / gen_time:.0f} quotes/sec)", file=sys.stderr)

    # Output
    indent = 2 if args.pretty else None

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if args.format == "json" or out_path.suffix == ".json":
            with open(out_path, "w") as f:
                json.dump(quotes, f, indent=indent, ensure_ascii=False, cls=NumpyEncoder)
        else:
            with open(out_path, "w") as f:
                for q in quotes:
                    f.write(json.dumps(q, ensure_ascii=False, cls=NumpyEncoder) + "\n")

        size_kb = out_path.stat().st_size / 1024
        print(f"  Saved: {out_path} ({size_kb:.1f} KB)", file=sys.stderr)
    else:
        if args.format == "json" or args.pretty:
            print(json.dumps(quotes if args.n > 1 else quotes[0], indent=indent, ensure_ascii=False, cls=NumpyEncoder))
        else:
            for q in quotes:
                print(json.dumps(q, ensure_ascii=False, cls=NumpyEncoder))


if __name__ == "__main__":
    main()
