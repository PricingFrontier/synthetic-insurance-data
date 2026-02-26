"""
Generate synthetic UK motor insurance quote request JSONs.

Usage:
    uv run python generate.py --n 10 --seed 42 --output data/output/quotes.jsonl
    uv run python generate.py --n 10000000 --seed 42 --workers 20 --output data/output/quotes.jsonl
    uv run python generate.py --n 1 --pretty           # single pretty-printed quote to stdout
"""

import argparse
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from generator import QuoteGenerator
from generator.data_loader import DistributionData


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


# ── Shared state for forked workers (set before fork) ────────────────────────
_shared_data: DistributionData | None = None


def _init_worker(data: DistributionData):
    """Store shared DistributionData in the forked child (copy-on-write)."""
    global _shared_data
    _shared_data = data


def _worker_generate(args: tuple) -> str:
    """Worker: generate n quotes, write to temp file, return the path."""
    worker_id, n, seed, quote_id_start, tmp_file = args

    gen = QuoteGenerator(seed=seed, data=_shared_data)
    gen._quote_counter = quote_id_start

    with open(tmp_file, "w") as f:
        for quote in gen.generate_iter(n):
            f.write(json.dumps(quote, ensure_ascii=False, cls=NumpyEncoder) + "\n")

    return tmp_file


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic motor insurance quotes")
    parser.add_argument("--n", type=int, default=10, help="Number of quotes to generate")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (.jsonl or .json). Prints to stdout if not specified.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--format", choices=["jsonl", "json"], default="jsonl",
                        help="Output format: jsonl (one JSON per line) or json (single array)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel worker processes (default: 1)")
    args = parser.parse_args()

    use_json_array = args.format == "json" or (args.output and Path(args.output).suffix == ".json")

    # ── Parallel JSONL generation ────────────────────────────────────────
    if args.workers > 1 and args.output and not use_json_array:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        n_workers = args.workers
        chunk_size = args.n // n_workers
        remainder = args.n % n_workers

        # Load data ONCE in the parent process
        print(f"Loading distribution data...", file=sys.stderr)
        t_load = time.time()
        data = DistributionData()
        print(f"  Loaded in {time.time() - t_load:.1f}s", file=sys.stderr)

        print(f"Generating {args.n:,} quotes across {n_workers} workers...", file=sys.stderr)
        t0 = time.time()

        # Build worker args
        tmp_dir = tempfile.mkdtemp(prefix="quotegen_")
        worker_args = []
        for i in range(n_workers):
            worker_n = chunk_size + (1 if i < remainder else 0)
            worker_seed = (args.seed + i) if args.seed is not None else None
            quote_id_start = i * chunk_size + min(i, remainder)
            tmp_file = os.path.join(tmp_dir, f"chunk_{i:03d}.jsonl")
            worker_args.append((i, worker_n, worker_seed, quote_id_start, tmp_file))

        # Use fork context so children inherit DistributionData via COW
        ctx = mp.get_context("fork")
        with ctx.Pool(
            processes=n_workers,
            initializer=_init_worker,
            initargs=(data,),
        ) as pool:
            # imap_unordered lets us report progress as chunks complete
            completed = 0
            tmp_files = []
            for tmp_file in pool.imap_unordered(_worker_generate, worker_args):
                completed += 1
                elapsed = time.time() - t0
                print(f"  Worker {completed}/{n_workers} done  ({elapsed:.0f}s elapsed)",
                      file=sys.stderr)
                tmp_files.append(tmp_file)

        # Concatenate temp files in the correct order
        print(f"  Concatenating {n_workers} chunks...", file=sys.stderr)
        ordered_files = [a[4] for a in worker_args]  # preserve chunk order
        with open(out_path, "wb") as out_f:
            for tmp_file in ordered_files:
                with open(tmp_file, "rb") as chunk_f:
                    while True:
                        buf = chunk_f.read(8 * 1024 * 1024)
                        if not buf:
                            break
                        out_f.write(buf)
                os.unlink(tmp_file)
        os.rmdir(tmp_dir)

        elapsed = time.time() - t0
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"  Done in {elapsed:.1f}s ({args.n / elapsed:,.0f} quotes/sec)", file=sys.stderr)
        print(f"  Saved: {out_path} ({size_mb:,.1f} MB)", file=sys.stderr)
        return

    # ── Single-process paths ─────────────────────────────────────────────
    print(f"Loading distribution data...", file=sys.stderr)
    t0 = time.time()
    gen = QuoteGenerator(seed=args.seed)
    load_time = time.time() - t0
    print(f"  Loaded in {load_time:.1f}s", file=sys.stderr)

    # JSON array format: must collect all in memory
    if use_json_array:
        print(f"Generating {args.n} quotes...", file=sys.stderr)
        t1 = time.time()
        quotes = gen.generate(args.n)
        gen_time = time.time() - t1
        print(f"  Generated in {gen_time:.1f}s ({args.n / gen_time:.0f} quotes/sec)", file=sys.stderr)

        indent = 2 if args.pretty else None
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w") as f:
                json.dump(quotes, f, indent=indent, ensure_ascii=False, cls=NumpyEncoder)
            size_kb = out_path.stat().st_size / 1024
            print(f"  Saved: {out_path} ({size_kb:.1f} KB)", file=sys.stderr)
        else:
            print(json.dumps(quotes if args.n > 1 else quotes[0],
                             indent=indent, ensure_ascii=False, cls=NumpyEncoder))
        return

    # JSONL streaming (single process)
    print(f"Generating {args.n:,} quotes (streaming)...", file=sys.stderr)
    t1 = time.time()
    progress_interval = max(1, args.n // 20)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            for i, quote in enumerate(gen.generate_iter(args.n), 1):
                f.write(json.dumps(quote, ensure_ascii=False, cls=NumpyEncoder) + "\n")
                if i % progress_interval == 0:
                    elapsed = time.time() - t1
                    rate = i / elapsed
                    eta = (args.n - i) / rate
                    print(f"  {i:>12,} / {args.n:,} ({i/args.n:.0%})  "
                          f"{rate:,.0f} quotes/sec  ETA {eta:.0f}s",
                          file=sys.stderr)
        elapsed = time.time() - t1
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"  Done in {elapsed:.1f}s ({args.n / elapsed:,.0f} quotes/sec)", file=sys.stderr)
        print(f"  Saved: {out_path} ({size_mb:.1f} MB)", file=sys.stderr)
    else:
        indent = 2 if args.pretty else None
        for i, quote in enumerate(gen.generate_iter(args.n), 1):
            print(json.dumps(quote, indent=indent, ensure_ascii=False, cls=NumpyEncoder))
            if args.n > 1000 and i % progress_interval == 0:
                elapsed = time.time() - t1
                rate = i / elapsed
                print(f"  {i:>12,} / {args.n:,} ({i/args.n:.0%})  "
                      f"{rate:,.0f} quotes/sec",
                      file=sys.stderr)


if __name__ == "__main__":
    main()
