"""
Generate broker panel premiums from existing quote JSONL files.

Simulates pricing from a 5-underwriter broker panel, each offering 3 cover
levels (Gold/Silver/Bronze) = 15 premiums per quote.  Commission amounts and
net premiums are computed per underwriter per tier.

Each underwriter applies the same rating engine as the aggregator (GLM
multiplicative), but with broker-specific commission structures and tier
multipliers defined in broker_data/config.py.

Primary output is a JSONL file: one line per quote, with the original quote
dict enriched with a "panel_premiums" key containing per-underwriter
per-tier gross premiums.

A companion Parquet file ({output_stem}_premiums.parquet) is also written,
containing premiums + commissions in a wide format for downstream scripts
(e.g. generate_broker_prices.py, generate_broker_demand_model.py).

Usage:
    uv run python generate_broker_panel_premiums.py --input data/output/quotes/quotes_1m.jsonl --seed 42 --workers 20 --output data/output/broker/quotes_broker_1m.jsonl
"""

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import tempfile
import time
import warnings
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from generate_premiums import _extract_quote_fields, _check_decline, _price_quote_premium, InsurerProfile
from broker_data.config import PANEL_UNDERWRITERS, COVER_LEVELS, TIER_NAMES, UNDERWRITER_COLUMNS, IPT_RATE


N_UW = len(PANEL_UNDERWRITERS)
N_TIERS = len(TIER_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# Worker: read a slice of input JSONL, write a JSONL chunk + Parquet chunk
# ─────────────────────────────────────────────────────────────────────────────

def _worker(args: tuple) -> str:
    worker_id, input_path, start_line, n_lines, seed, tmp_jsonl, tmp_parquet = args

    rng = np.random.default_rng(seed)

    # Parquet arrays: pre-allocate for premiums and commissions
    quote_ids = []
    premiums = np.empty((n_lines, N_UW * N_TIERS), dtype=np.float32)
    commissions = np.empty((n_lines, N_UW * N_TIERS), dtype=np.float32)

    with open(input_path) as fin, open(tmp_jsonl, "w") as fout:
        for _ in range(start_line):
            fin.readline()
        for row in range(n_lines):
            line = fin.readline()
            if not line:
                break
            quote = json.loads(line)
            quote_id = quote["quote_metadata"]["quote_id"]
            quote_ids.append(quote_id)

            # Extract fields once per quote
            fields = _extract_quote_fields(quote)

            # Build panel_premiums dict for JSONL output
            panel_premiums = {}

            for uw_idx, uw in enumerate(PANEL_UNDERWRITERS):
                uw_key = UNDERWRITER_COLUMNS[uw_idx]

                # Check decline once per underwriter
                if _check_decline(fields, uw.insurer):
                    # All 3 tiers are NaN for this underwriter
                    for tier_idx in range(N_TIERS):
                        col = uw_idx * N_TIERS + tier_idx
                        premiums[row, col] = np.nan
                        commissions[row, col] = np.nan
                    panel_premiums[uw_key] = None
                    continue

                # Price the silver (baseline) tier once — this includes noise
                silver_premium = _price_quote_premium(quote, uw.insurer, rng, _fields=fields)

                # If silver came back NaN (shouldn't happen since we checked decline,
                # but defensive), propagate NaN
                if math.isnan(silver_premium):
                    for tier_idx in range(N_TIERS):
                        col = uw_idx * N_TIERS + tier_idx
                        premiums[row, col] = np.nan
                        commissions[row, col] = np.nan
                    panel_premiums[uw_key] = None
                    continue

                silver_mult = uw.silver_multiplier
                tier_premiums_dict = {}

                for tier_idx, tier_name in enumerate(TIER_NAMES):
                    col = uw_idx * N_TIERS + tier_idx
                    tier_mult = uw.tier_multiplier(tier_name)

                    # Scale from silver baseline to this tier
                    tier_premium = silver_premium * (tier_mult / silver_mult)
                    tier_premium = round(tier_premium, 2)

                    premiums[row, col] = tier_premium
                    tier_premiums_dict[tier_name] = tier_premium

                    # Commission (Parquet only)
                    comm_rate = uw.commission_rate(tier_name)
                    commission_amount = round(tier_premium * comm_rate, 2)
                    commissions[row, col] = commission_amount

                panel_premiums[uw_key] = tier_premiums_dict

            # Write enriched quote as JSONL line
            quote["panel_premiums"] = panel_premiums
            fout.write(json.dumps(quote, separators=(",", ":")) + "\n")

    actual_rows = len(quote_ids)

    # Build Parquet chunk (premiums + commissions for downstream scripts)
    columns = {"quote_id": pa.array(quote_ids, type=pa.string())}

    # Premium columns: {underwriter_column}_{tier}
    for uw_idx, uw_col in enumerate(UNDERWRITER_COLUMNS):
        for tier_idx, tier_name in enumerate(TIER_NAMES):
            col_idx = uw_idx * N_TIERS + tier_idx
            col_name = f"{uw_col}_{tier_name}"
            columns[col_name] = pa.array(premiums[:actual_rows, col_idx], type=pa.float32())

    # Commission columns: {underwriter_column}_{tier}_commission
    for uw_idx, uw_col in enumerate(UNDERWRITER_COLUMNS):
        for tier_idx, tier_name in enumerate(TIER_NAMES):
            col_idx = uw_idx * N_TIERS + tier_idx
            col_name = f"{uw_col}_{tier_name}_commission"
            columns[col_name] = pa.array(commissions[:actual_rows, col_idx], type=pa.float32())

    # Cheapest per tier (across underwriters, ignoring NaN)
    for tier_idx, tier_name in enumerate(TIER_NAMES):
        tier_cols = [uw_idx * N_TIERS + tier_idx for uw_idx in range(N_UW)]
        tier_premiums_arr = premiums[:actual_rows, tier_cols]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            cheapest = np.nanmin(tier_premiums_arr, axis=1)
        columns[f"cheapest_{tier_name}"] = pa.array(cheapest, type=pa.float32())

    table = pa.table(columns)
    pq.write_table(table, tmp_parquet, compression="zstd", compression_level=3)

    return tmp_jsonl


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate broker panel premiums from quote JSONL files "
                    "(outputs JSONL + companion Parquet)"
    )
    parser.add_argument("--input", type=str, required=True, help="Path to quotes JSONL file")
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output JSONL file path (companion Parquet written alongside as "
             "{stem}_premiums.parquet)",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Companion Parquet path: same stem + _premiums.parquet
    parquet_path = out_path.parent / f"{out_path.stem}_premiums.parquet"

    # Count lines
    print(f"Counting quotes in {input_path}...", file=sys.stderr)
    t0 = time.time()
    total_lines = 0
    with open(input_path, "rb") as f:
        for _ in f:
            total_lines += 1
    print(f"  {total_lines:,} quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    n_workers = max(1, args.workers)
    chunk_size = total_lines // n_workers
    remainder = total_lines % n_workers

    tmp_dir = tempfile.mkdtemp(prefix="panelpremgen_")
    worker_args = []
    offset = 0
    for i in range(n_workers):
        worker_n = chunk_size + (1 if i < remainder else 0)
        worker_seed = (args.seed + i) if args.seed is not None else None
        tmp_jsonl = os.path.join(tmp_dir, f"chunk_{i:03d}.jsonl")
        tmp_parquet = os.path.join(tmp_dir, f"chunk_{i:03d}.parquet")
        worker_args.append((i, str(input_path), offset, worker_n, worker_seed, tmp_jsonl, tmp_parquet))
        offset += worker_n

    n_uw = len(PANEL_UNDERWRITERS)
    n_tiers = len(TIER_NAMES)
    print(f"Generating panel premiums for {n_uw} underwriters x {n_tiers} tiers "
          f"across {n_workers} worker(s)...", file=sys.stderr)
    t1 = time.time()

    if n_workers > 1:
        ctx = mp.get_context("fork")
        with ctx.Pool(processes=n_workers) as pool:
            completed = 0
            for _ in pool.imap_unordered(_worker, worker_args):
                completed += 1
                elapsed = time.time() - t1
                rate = int(completed / n_workers * total_lines / elapsed) if elapsed > 0 else 0
                print(f"  Worker {completed}/{n_workers} done  "
                      f"({elapsed:.0f}s elapsed, ~{rate:,} quotes/sec)",
                      file=sys.stderr)
    else:
        _worker(worker_args[0])

    # ── Concatenate JSONL chunks ─────────────────────────────────────────────
    print(f"  Concatenating {n_workers} JSONL chunks...", file=sys.stderr)
    ordered_jsonl_files = [a[5] for a in worker_args]
    with open(out_path, "wb") as fout:
        for chunk_file in ordered_jsonl_files:
            with open(chunk_file, "rb") as fin:
                while True:
                    block = fin.read(1024 * 1024)  # 1MB blocks
                    if not block:
                        break
                    fout.write(block)

    jsonl_size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  Saved JSONL: {out_path} ({jsonl_size_mb:,.1f} MB)", file=sys.stderr)

    # ── Concatenate Parquet chunks ───────────────────────────────────────────
    print(f"  Concatenating {n_workers} Parquet chunks...", file=sys.stderr)
    ordered_parquet_files = [a[6] for a in worker_args]
    tables = [pq.read_table(f) for f in ordered_parquet_files]
    combined = pa.concat_tables(tables)
    pq.write_table(combined, str(parquet_path), compression="zstd", compression_level=3)

    parquet_size_mb = parquet_path.stat().st_size / (1024 * 1024)
    print(f"  Saved Parquet: {parquet_path} ({parquet_size_mb:,.1f} MB)", file=sys.stderr)

    # Cleanup
    for f in ordered_jsonl_files:
        os.unlink(f)
    for f in ordered_parquet_files:
        os.unlink(f)
    os.rmdir(tmp_dir)

    elapsed = time.time() - t1
    print(f"  Done in {elapsed:.1f}s ({total_lines / elapsed:,.0f} quotes/sec)", file=sys.stderr)
    print(f"  Parquet shape: {combined.num_rows:,} rows x {combined.num_columns} cols", file=sys.stderr)
    print(f"  Parquet columns: {combined.column_names}", file=sys.stderr)


if __name__ == "__main__":
    main()
