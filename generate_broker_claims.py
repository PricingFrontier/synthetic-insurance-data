"""
Generate synthetic claims for broker-channel converted policies.

Reads new business and renewal conversion Parquets, joins back to original
quotes JSONL for risk factors, and generates claims using the same
Poisson frequency x log-normal severity model as the direct channel.

Cover level (gold/silver/bronze) affects claim reporting via excess thresholds:
higher excess means more small claims are absorbed by the customer.

Output: single Parquet file with one row per claim.

Usage:
    uv run python generate_broker_claims.py \
        --new-business data/output/broker/conversions_new_business.parquet \
        --renewals data/output/broker/conversions_renewals.parquet \
        --quotes data/output/quotes/quotes_1m.jsonl \
        --seed 42 --workers 20 \
        --output data/output/broker/claims
"""

import argparse
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from generate_claims import (
    _gen_claims,
    _gen_exposure,
    _calc_age,
    PERIL_WEIGHTS,
    SEVERITY_PARAMS,
    FAULT_PROBS,
    CANCELLATION_RATE,
)
from broker_data.config import COVER_LEVELS


# ─────────────────────────────────────────────────────────────────────────────
# Shared data (loaded once in parent, inherited via fork COW)
# ─────────────────────────────────────────────────────────────────────────────

_SHARED_DATA = None       # DistributionData (for claim rates)
_SHARED_POLICIES = None   # dict: quote_id -> (policy_id, business_type, cover_level,
                          #   underwriter, exposure, proposer_age, vol_excess, comp_excess)


def _load_shared_data():
    global _SHARED_DATA
    from generator.data_loader import DistributionData
    _SHARED_DATA = DistributionData()


def _load_policies(
    nb_path: str,
    renewals_path: str,
    seed: int | None,
) -> int:
    """Load converted policies from both Parquets and build the shared lookup.

    Pre-generates exposure for each policy so workers don't need to coordinate.
    Returns the total number of converted policies loaded.
    """
    global _SHARED_POLICIES

    rng = np.random.default_rng(seed)
    _SHARED_POLICIES = {}

    # ── New business ────────────────────────────────────────────────────────
    if nb_path and Path(nb_path).exists():
        nb_table = pq.read_table(nb_path)
        nb_policy_ids = nb_table.column("policy_id").to_pylist()
        nb_quote_ids = nb_table.column("quote_id").to_pylist()
        nb_cover_levels = nb_table.column("cover_level").to_pylist()
        nb_underwriters = nb_table.column("selected_underwriter").to_pylist()
        nb_ages = nb_table.column("proposer_age").to_pylist()

        for i in range(len(nb_policy_ids)):
            cover = nb_cover_levels[i]
            cl = COVER_LEVELS.get(cover)
            vol_excess = cl.voluntary_excess if cl else 250
            comp_excess = cl.compulsory_excess if cl else 200
            exposure = _gen_exposure(rng)

            _SHARED_POLICIES[nb_quote_ids[i]] = (
                nb_policy_ids[i],
                "new_business",
                cover,
                nb_underwriters[i],
                exposure,
                int(nb_ages[i]),
                vol_excess,
                comp_excess,
            )

    # ── Renewals (retained only) ────────────────────────────────────────────
    if renewals_path and Path(renewals_path).exists():
        ren_table = pq.read_table(renewals_path)
        ren_retained = ren_table.column("retained").to_pylist()
        ren_policy_ids = ren_table.column("policy_id").to_pylist()
        ren_quote_ids = ren_table.column("quote_id").to_pylist()
        ren_cover_levels = ren_table.column("cover_level").to_pylist()
        ren_underwriters = ren_table.column("underwriter").to_pylist()
        ren_ages = ren_table.column("proposer_age").to_pylist()

        for i in range(len(ren_policy_ids)):
            if not ren_retained[i]:
                continue

            cover = ren_cover_levels[i]
            cl = COVER_LEVELS.get(cover)
            vol_excess = cl.voluntary_excess if cl else 250
            comp_excess = cl.compulsory_excess if cl else 200
            exposure = _gen_exposure(rng)

            _SHARED_POLICIES[ren_quote_ids[i]] = (
                ren_policy_ids[i],
                "renewal",
                cover,
                ren_underwriters[i],
                exposure,
                int(ren_ages[i]),
                vol_excess,
                comp_excess,
            )

    return len(_SHARED_POLICIES)


# ─────────────────────────────────────────────────────────────────────────────
# Cover-level claim suppression filter
# ─────────────────────────────────────────────────────────────────────────────

def _apply_cover_filter(
    rng: np.random.Generator,
    claims: list[tuple[str, str, float]],
    cover_level: str,
    vol_excess: int,
    comp_excess: int,
) -> list[tuple[str, str, float, float, float, bool]]:
    """Apply cover-level excess and small-claim suppression.

    Returns list of (peril, fault, gross_amount, excess_applied, net_amount, reported).

    Incidents happen regardless of cover level. But customers with high excess
    may not bother reporting small claims where the payout would be minimal.
    """
    total_excess = vol_excess + comp_excess
    results = []

    for peril, fault, gross_amount in claims:
        excess_applied = min(float(total_excess), gross_amount)
        net_amount = max(0.0, gross_amount - total_excess)

        # Determine if the claim is reported
        reported = True

        if cover_level == "silver":
            # Total excess £450; claims under £500 often not reported (net payout too small)
            if gross_amount < 500:
                if rng.random() < 0.15:
                    reported = False

        elif cover_level == "bronze":
            # Total excess £850; claims under £1000 often not reported (net payout too small)
            if gross_amount < 1000:
                if rng.random() < 0.30:
                    reported = False

        # Gold: all claims reported (low excess, everything worth claiming)

        results.append((
            peril,
            fault,
            round(gross_amount, 2),
            round(excess_applied, 2),
            round(net_amount, 2),
            reported,
        ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Worker: read a slice of JSONL, match to policies, generate claims
# ─────────────────────────────────────────────────────────────────────────────

def _worker(args: tuple) -> str:
    worker_id, input_path, start_line, n_lines, seed, tmp_out = args

    data = _SHARED_DATA
    policies = _SHARED_POLICIES
    rng = np.random.default_rng(seed)

    peril_names = list(PERIL_WEIGHTS.keys())
    peril_probs = np.array(list(PERIL_WEIGHTS.values()))
    peril_probs /= peril_probs.sum()

    # Output columns
    out_claim_id = []
    out_policy_id = []
    out_quote_id = []
    out_business_type = []
    out_cover_level = []
    out_underwriter = []
    out_claim_type = []
    out_fault_status = []
    out_gross_amount = []
    out_excess_applied = []
    out_net_amount = []
    out_reported = []
    out_exposure = []
    out_proposer_age = []

    claim_counter = 0

    with open(input_path) as fin:
        for _ in range(start_line):
            fin.readline()
        for _ in range(n_lines):
            line = fin.readline()
            if not line:
                break
            quote = json.loads(line)

            quote_id = quote["quote_metadata"]["quote_id"]

            # Only process quotes that have a converted policy
            pol = policies.get(quote_id)
            if pol is None:
                continue

            (policy_id, business_type, cover_level, underwriter,
             exposure, proposer_age, vol_excess, comp_excess) = pol

            # Extract risk factors from quote
            proposer = quote["proposer"]
            vehicle = quote["vehicle"]
            policy_details = quote["policy_details"]

            gender = proposer.get("gender", "male")
            ins_group = vehicle.get("insurance_group", 20)
            annual_mileage = policy_details.get("annual_mileage", 8000)
            veh_age = date.today().year - vehicle.get("year_of_manufacture", 2020)
            age = _calc_age(proposer.get("date_of_birth"))

            # Generate claims using the same model as direct channel
            raw_claims = _gen_claims(
                rng, data, peril_names, peril_probs,
                exposure, age, gender, ins_group, annual_mileage, veh_age,
            )

            if not raw_claims:
                continue

            # Apply cover-level excess and suppression filter
            filtered_claims = _apply_cover_filter(
                rng, raw_claims, cover_level, vol_excess, comp_excess,
            )

            for peril, fault, gross, excess, net, reported in filtered_claims:
                claim_counter += 1
                out_claim_id.append(f"CLM-BRK-{worker_id:03d}-{claim_counter:09d}")
                out_policy_id.append(policy_id)
                out_quote_id.append(quote_id)
                out_business_type.append(business_type)
                out_cover_level.append(cover_level)
                out_underwriter.append(underwriter)
                out_claim_type.append(peril)
                out_fault_status.append(fault)
                out_gross_amount.append(gross)
                out_excess_applied.append(excess)
                out_net_amount.append(net)
                out_reported.append(reported)
                out_exposure.append(exposure)
                out_proposer_age.append(proposer_age)

    # Write chunk
    out_table = pa.table({
        "claim_id": pa.array(out_claim_id, type=pa.string()),
        "policy_id": pa.array(out_policy_id, type=pa.string()),
        "quote_id": pa.array(out_quote_id, type=pa.string()),
        "business_type": pa.array(out_business_type, type=pa.string()),
        "cover_level": pa.array(out_cover_level, type=pa.string()),
        "underwriter": pa.array(out_underwriter, type=pa.string()),
        "claim_type": pa.array(out_claim_type, type=pa.string()),
        "fault_status": pa.array(out_fault_status, type=pa.string()),
        "gross_amount": pa.array(out_gross_amount, type=pa.float32()),
        "excess_applied": pa.array(out_excess_applied, type=pa.float32()),
        "net_amount": pa.array(out_net_amount, type=pa.float32()),
        "reported": pa.array(out_reported, type=pa.bool_()),
        "exposure": pa.array(out_exposure, type=pa.float32()),
        "proposer_age": pa.array(out_proposer_age, type=pa.int32()),
    })
    pq.write_table(out_table, tmp_out, compression="zstd", compression_level=3)

    return tmp_out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate broker claims from converted policies (new business + retained renewals)"
    )
    parser.add_argument(
        "--new-business", type=str, required=True,
        help="Path to new business conversions Parquet"
    )
    parser.add_argument(
        "--renewals", type=str, required=True,
        help="Path to renewal conversions Parquet"
    )
    parser.add_argument(
        "--quotes", type=str, required=True,
        help="Path to quotes JSONL file (for risk factor lookup)"
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output path prefix (produces <output>.parquet)"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel workers (default: 1)"
    )
    args = parser.parse_args()

    # Validate inputs
    nb_path = Path(args.new_business)
    if not nb_path.exists():
        print(f"Error: new business file not found: {nb_path}", file=sys.stderr)
        sys.exit(1)

    ren_path = Path(args.renewals)
    if not ren_path.exists():
        print(f"Error: renewals file not found: {ren_path}", file=sys.stderr)
        sys.exit(1)

    quotes_path = Path(args.quotes)
    if not quotes_path.exists():
        print(f"Error: quotes file not found: {quotes_path}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(f"{args.output}.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # ── Load distribution data ──────────────────────────────────────────────
    print("Loading distribution data...", file=sys.stderr)
    _load_shared_data()
    print(f"  Loaded in {time.time() - t0:.1f}s", file=sys.stderr)

    # ── Load converted policies ─────────────────────────────────────────────
    print("Loading converted policies...", file=sys.stderr)
    t_pol = time.time()
    exposure_seed = (args.seed + 5000) if args.seed is not None else None
    n_policies = _load_policies(str(nb_path), str(ren_path), exposure_seed)

    n_nb = sum(1 for v in _SHARED_POLICIES.values() if v[1] == "new_business")
    n_ren = sum(1 for v in _SHARED_POLICIES.values() if v[1] == "renewal")
    print(f"  {n_policies:,} policies ({n_nb:,} new business, {n_ren:,} renewals) "
          f"in {time.time() - t_pol:.1f}s", file=sys.stderr)

    # ── Count quotes ────────────────────────────────────────────────────────
    print(f"Counting quotes in {quotes_path}...", file=sys.stderr)
    t_count = time.time()
    total_lines = 0
    with open(quotes_path, "rb") as f:
        for _ in f:
            total_lines += 1
    print(f"  {total_lines:,} quotes ({time.time() - t_count:.1f}s)", file=sys.stderr)

    # ── Build worker args ───────────────────────────────────────────────────
    n_workers = max(1, args.workers)
    chunk_size = total_lines // n_workers
    remainder = total_lines % n_workers

    tmp_dir = tempfile.mkdtemp(prefix="brkclmgen_")
    worker_args = []
    offset = 0
    for i in range(n_workers):
        worker_n = chunk_size + (1 if i < remainder else 0)
        worker_seed = (args.seed + i) if args.seed is not None else None
        tmp_out = os.path.join(tmp_dir, f"clm_{i:03d}.parquet")
        worker_args.append((i, str(quotes_path), offset, worker_n, worker_seed, tmp_out))
        offset += worker_n

    # ── Run workers ─────────────────────────────────────────────────────────
    print(f"Generating broker claims across {n_workers} worker(s)...", file=sys.stderr)
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

    # ── Concatenate chunks ──────────────────────────────────────────────────
    print(f"  Concatenating {n_workers} chunks...", file=sys.stderr)
    chunk_files = [a[5] for a in worker_args]
    chunk_tables = [pq.read_table(f) for f in chunk_files]
    combined = pa.concat_tables(chunk_tables)

    pq.write_table(combined, str(out_path), compression="zstd", compression_level=3)

    # Cleanup temp files
    for f in chunk_files:
        os.unlink(f)
    os.rmdir(tmp_dir)

    elapsed = time.time() - t1
    out_mb = out_path.stat().st_size / (1024 * 1024)

    n_reported = sum(1 for v in combined.column("reported").to_pylist() if v)
    n_total = combined.num_rows
    report_rate = n_reported / n_total * 100 if n_total > 0 else 0

    print(f"\n  Done in {elapsed:.1f}s ({total_lines / elapsed:,.0f} quotes/sec)", file=sys.stderr)
    print(f"  Claims: {out_path} ({n_total:,} rows, {out_mb:.1f} MB)", file=sys.stderr)
    print(f"  Reported: {n_reported:,}/{n_total:,} = {report_rate:.1f}%", file=sys.stderr)

    # Break down by cover level
    cover_levels = combined.column("cover_level").to_pylist()
    reported_flags = combined.column("reported").to_pylist()
    for level in ("gold", "silver", "bronze"):
        level_total = sum(1 for c in cover_levels if c == level)
        level_reported = sum(1 for c, r in zip(cover_levels, reported_flags) if c == level and r)
        if level_total > 0:
            print(f"    {level}: {level_reported:,}/{level_total:,} reported "
                  f"({level_reported / level_total * 100:.1f}%)", file=sys.stderr)

    total_elapsed = time.time() - t0
    print(f"\n  Total time: {total_elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
