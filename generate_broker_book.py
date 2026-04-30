"""
Generate a broker book of business from existing quotes and panel premiums.

Creates the prior-year book: policies that were sold, with premiums paid,
underwriter placement, commission, and claims history.  These become the
basis for renewal modelling in the next policy year.

For each quote the script:
  1. Picks a cover level (gold 20%, silver 50%, bronze 30%)
  2. Selects an underwriter via a broker placement model
  3. Applies the position-based conversion model
  4. Generates exposure (with 8% mid-term cancellation)
  5. Generates claims using the same peril/severity model as generate_claims.py

Output is two Parquet files:
  - <output>_policies.parquet: one row per bound policy
  - <output>_claims.parquet:   one row per claim linked to policy_id

Usage:
    uv run python generate_broker_book.py \\
        --quotes data/output/quotes/quotes_1m.jsonl \\
        --premiums data/output/broker/panel_premiums_1m.parquet \\
        --seed 42 --workers 20 \\
        --output data/output/broker/book
"""

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from generate_claims import (
    _conversion_probability,
    _calc_age,
    _gen_exposure,
    _gen_claims,
    PERIL_WEIGHTS,
    SEVERITY_PARAMS,
    FAULT_PROBS,
)
from broker_data.config import (
    PANEL_UNDERWRITERS,
    UNDERWRITER_COLUMNS,
    TIER_NAMES,
    IPT_RATE,
    DEFAULT_BROKER,
)


# ─────────────────────────────────────────────────────────────────────────────
# Cover level selection weights
# ─────────────────────────────────────────────────────────────────────────────

_TIER_WEIGHTS = {"gold": 0.20, "silver": 0.50, "bronze": 0.30}
_TIER_NAMES_LIST = list(_TIER_WEIGHTS.keys())
_TIER_PROBS = np.array([_TIER_WEIGHTS[t] for t in _TIER_NAMES_LIST])

N_UW = len(PANEL_UNDERWRITERS)
N_TIERS = len(TIER_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# Shared data (loaded once in parent, inherited via fork COW)
# ─────────────────────────────────────────────────────────────────────────────

_SHARED_DATA = None
_SHARED_PREMIUMS = None  # dict: quote_id -> {tier: [(uw_idx, premium, commission_rate), ...]}


def _load_shared_data():
    global _SHARED_DATA
    from generator.data_loader import DistributionData
    _SHARED_DATA = DistributionData()


def _load_premiums(premiums_path: str):
    """Load panel premiums Parquet and build a lookup dict per quote.

    For each quote_id, stores a dict mapping tier name to a list of
    (uw_index, premium, commission_rate) tuples for underwriters that quoted.
    """
    global _SHARED_PREMIUMS
    table = pq.read_table(premiums_path)
    quote_ids = table.column("quote_id").to_pylist()

    # Build column name -> numpy array mapping for fast access
    col_arrays = {}
    for col_name in table.column_names:
        if col_name == "quote_id":
            continue
        col_arrays[col_name] = table.column(col_name).to_numpy(zero_copy_only=False)

    _SHARED_PREMIUMS = {}
    for row_idx, qid in enumerate(quote_ids):
        tiers = {}
        for tier_name in TIER_NAMES:
            entries = []
            for uw_idx, uw_col in enumerate(UNDERWRITER_COLUMNS):
                prem_col = f"{uw_col}_{tier_name}"
                comm_col = f"{uw_col}_{tier_name}_commission"

                prem = col_arrays[prem_col][row_idx]
                if math.isnan(prem):
                    continue  # underwriter declined

                comm_amount = col_arrays[comm_col][row_idx]
                # Derive commission rate from amount / premium
                comm_rate = float(comm_amount / prem) if prem > 0 else 0.0

                entries.append((uw_idx, float(prem), comm_rate))

            if entries:
                # Sort by premium ascending
                entries.sort(key=lambda x: x[1])
                tiers[tier_name] = entries

        if tiers:
            _SHARED_PREMIUMS[qid] = tiers


# ─────────────────────────────────────────────────────────────────────────────
# Broker placement model
# ─────────────────────────────────────────────────────────────────────────────

def _select_underwriter(
    entries: list[tuple[int, float, float]],
    rng: np.random.Generator,
) -> tuple[int, float, float, int]:
    """Select which underwriter the broker places this policy with.

    Returns (uw_index, premium, commission_rate, rank).

    Placement logic:
      70% - cheapest underwriter
      20% - highest commission (if within 10% of cheapest)
      10% - random from those that quoted
    """
    # entries is already sorted by premium ascending
    cheapest_idx, cheapest_prem, cheapest_comm = entries[0]

    roll = rng.random()

    if roll < 0.70 or len(entries) == 1:
        # Place with cheapest
        return cheapest_idx, cheapest_prem, cheapest_comm, 1

    if roll < 0.90:
        # Place with highest commission, if within 10% of cheapest
        best_comm_entry = None
        best_comm_rate = -1.0
        for uw_idx, prem, comm_rate in entries:
            if prem <= cheapest_prem * 1.10 and comm_rate > best_comm_rate:
                best_comm_rate = comm_rate
                best_comm_entry = (uw_idx, prem, comm_rate)

        if best_comm_entry is not None:
            # Determine rank of selected
            selected_prem = best_comm_entry[1]
            rank = sum(1 for _, p, _ in entries if p < selected_prem) + 1
            return best_comm_entry[0], best_comm_entry[1], best_comm_entry[2], rank

        # Fallback to cheapest if no commission-advantaged option
        return cheapest_idx, cheapest_prem, cheapest_comm, 1

    # 10%: random selection
    pick = int(rng.integers(0, len(entries)))
    uw_idx, prem, comm_rate = entries[pick]
    rank = sum(1 for _, p, _ in entries if p < prem) + 1
    return uw_idx, prem, comm_rate, rank


# ─────────────────────────────────────────────────────────────────────────────
# Worker: read a slice of input JSONL, write two parquet chunks
# ─────────────────────────────────────────────────────────────────────────────

def _worker(args: tuple) -> tuple[str, str]:
    worker_id, input_path, start_line, n_lines, seed, tmp_pol, tmp_clm = args

    data = _SHARED_DATA
    premiums_lookup = _SHARED_PREMIUMS
    rng = np.random.default_rng(seed)

    peril_names = list(PERIL_WEIGHTS.keys())
    peril_probs = np.array(list(PERIL_WEIGHTS.values()))
    peril_probs /= peril_probs.sum()

    broker_fee = DEFAULT_BROKER.broker_fee

    # Policy columns
    pol_policy_id = []
    pol_quote_id = []
    pol_cover_level = []
    pol_underwriter = []
    pol_inception_premium = []
    pol_ipt_amount = []
    pol_broker_fee = []
    pol_total_customer_price = []
    pol_commission_rate = []
    pol_commission_amount = []
    pol_net_to_underwriter = []
    pol_quote_date = []
    pol_sale_date = []
    pol_policy_start_date = []
    pol_policy_end_date = []
    pol_ncd_years = []
    pol_proposer_age = []
    pol_gender = []
    pol_marital_status = []
    pol_insurance_group = []
    pol_postcode_area = []
    pol_annual_mileage = []
    pol_vehicle_value = []
    pol_vehicle_age = []
    pol_body_type = []
    pol_fuel_type = []
    pol_exposure = []

    # Claim columns
    clm_claim_id = []
    clm_policy_id = []
    clm_claim_type = []
    clm_fault_status = []
    clm_amount = []

    policy_counter = 0
    claim_counter = 0

    with open(input_path) as fin:
        for _ in range(start_line):
            fin.readline()
        for _ in range(n_lines):
            line = fin.readline()
            if not line:
                break
            quote = json.loads(line)

            proposer = quote["proposer"]
            policy = quote["policy_details"]
            vehicle = quote["vehicle"]
            quote_id = quote["quote_metadata"]["quote_id"]

            # Look up panel premiums for this quote
            quote_tiers = premiums_lookup.get(quote_id) if premiums_lookup else None
            if quote_tiers is None:
                continue  # no underwriter quoted for this quote

            # 1. Pick a cover level
            tier_name = rng.choice(_TIER_NAMES_LIST, p=_TIER_PROBS)

            # Check if any underwriter quoted for this tier
            entries = quote_tiers.get(tier_name)
            if not entries:
                continue  # no underwriter quoted for the selected tier

            # 2. Select underwriter via broker placement model
            uw_idx, selected_premium, commission_rate, rank = _select_underwriter(entries, rng)

            # 3. Build conversion context
            cheapest_prem = entries[0][1]
            n_competitors = len(entries)
            ratio_to_cheapest = selected_premium / cheapest_prem if cheapest_prem > 0 else 1.0

            # Gap to next cheapest (when rank 1)
            if rank == 1 and len(entries) > 1:
                gap_to_next_pct = (entries[1][1] - selected_premium) / selected_premium
            else:
                gap_to_next_pct = 0.0

            age = _calc_age(proposer.get("date_of_birth"))
            licence_type = proposer.get("licence", {}).get("licence_type", "full_uk")

            # Per-quote brand-preference noise
            brand_noise = float(rng.lognormal(0, 0.15))

            conv_prob = _conversion_probability(
                rank, ratio_to_cheapest, gap_to_next_pct,
                n_competitors, cheapest_prem,
                age, licence_type, brand_noise,
            )

            if rng.random() > conv_prob:
                continue

            # ── Policy sold ──────────────────────────────────────────────
            policy_counter += 1
            pid = f"POL-BRK-{worker_id:03d}-{policy_counter:09d}"

            # Underwriter name
            uw_name = PANEL_UNDERWRITERS[uw_idx].insurer.name

            # Financial calculations
            ipt_amount = round(selected_premium * IPT_RATE, 2)
            total_customer_price = round(selected_premium + ipt_amount + broker_fee, 2)
            commission_amount = round(selected_premium * commission_rate, 2)
            net_to_underwriter = round(selected_premium - commission_amount, 2)

            # Dates
            quote_ts = quote["quote_metadata"].get("quote_timestamp", "")
            quote_date_str = quote_ts[:10] if quote_ts else None
            policy_start_str = policy.get("cover_start_date")

            # Sale date: between quote date and cover start
            sale_date_str = quote_date_str
            if quote_date_str and policy_start_str:
                q_date = date.fromisoformat(quote_date_str)
                p_start = date.fromisoformat(policy_start_str)
                gap_days = max(0, (p_start - q_date).days)
                sale_offset = int(rng.uniform(0, max(1, gap_days + 1)))
                sale_date_str = (q_date + timedelta(days=sale_offset)).isoformat()

            # Policy end date = start + 1 year
            policy_end_str = None
            if policy_start_str:
                p_start = date.fromisoformat(policy_start_str)
                try:
                    policy_end_str = p_start.replace(year=p_start.year + 1).isoformat()
                except ValueError:
                    # Feb 29 start → use Mar 1 next year
                    policy_end_str = date(p_start.year + 1, 3, 1).isoformat()

            # NCD years
            ncd_years = policy.get("ncd_years", 0)

            # Exposure (with cancellation)
            exposure = _gen_exposure(rng)

            # Risk factors for claims
            gender = proposer.get("gender", "male")
            marital_status = proposer.get("marital_status", "unknown")
            ins_group = vehicle.get("insurance_group", 20)
            address = quote.get("address", {})
            postcode = address.get("postcode", "")
            postcode_area = "".join(c for c in postcode.strip().split()[0] if c.isalpha()) if postcode else ""
            annual_mileage = policy.get("annual_mileage", 8000)
            vehicle_value = vehicle.get("estimated_value", 10000)
            veh_age = max(0, date.today().year - vehicle.get("year_of_manufacture", 2020))
            body_type = vehicle.get("body_type", "hatchback")
            fuel_type = vehicle.get("fuel_type", "petrol")

            # Generate claims
            claims = _gen_claims(
                rng, data, peril_names, peril_probs,
                exposure, age, gender, ins_group, annual_mileage, veh_age,
            )

            # Append policy record
            pol_policy_id.append(pid)
            pol_quote_id.append(quote_id)
            pol_cover_level.append(tier_name)
            pol_underwriter.append(uw_name)
            pol_inception_premium.append(round(selected_premium, 2))
            pol_ipt_amount.append(ipt_amount)
            pol_broker_fee.append(broker_fee)
            pol_total_customer_price.append(total_customer_price)
            pol_commission_rate.append(round(commission_rate, 4))
            pol_commission_amount.append(commission_amount)
            pol_net_to_underwriter.append(net_to_underwriter)
            pol_quote_date.append(quote_date_str)
            pol_sale_date.append(sale_date_str)
            pol_policy_start_date.append(policy_start_str)
            pol_policy_end_date.append(policy_end_str)
            pol_ncd_years.append(ncd_years)
            pol_proposer_age.append(age)
            pol_gender.append(gender)
            pol_marital_status.append(marital_status)
            pol_insurance_group.append(ins_group)
            pol_postcode_area.append(postcode_area)
            pol_annual_mileage.append(annual_mileage)
            pol_vehicle_value.append(vehicle_value)
            pol_vehicle_age.append(veh_age)
            pol_body_type.append(body_type)
            pol_fuel_type.append(fuel_type)
            pol_exposure.append(round(exposure, 4))

            # Append claim records
            for peril, fault, amount in claims:
                claim_counter += 1
                clm_claim_id.append(f"CLM-BRK-{worker_id:03d}-{claim_counter:09d}")
                clm_policy_id.append(pid)
                clm_claim_type.append(peril)
                clm_fault_status.append(fault)
                clm_amount.append(amount)

    # Write policy chunk
    pol_table = pa.table({
        "policy_id": pa.array(pol_policy_id, type=pa.string()),
        "quote_id": pa.array(pol_quote_id, type=pa.string()),
        "cover_level": pa.array(pol_cover_level, type=pa.string()),
        "underwriter": pa.array(pol_underwriter, type=pa.string()),
        "inception_premium": pa.array(pol_inception_premium, type=pa.float32()),
        "ipt_amount": pa.array(pol_ipt_amount, type=pa.float32()),
        "broker_fee": pa.array(pol_broker_fee, type=pa.float32()),
        "total_customer_price": pa.array(pol_total_customer_price, type=pa.float32()),
        "commission_rate": pa.array(pol_commission_rate, type=pa.float32()),
        "commission_amount": pa.array(pol_commission_amount, type=pa.float32()),
        "net_to_underwriter": pa.array(pol_net_to_underwriter, type=pa.float32()),
        "quote_date": pa.array(pol_quote_date, type=pa.string()),
        "sale_date": pa.array(pol_sale_date, type=pa.string()),
        "policy_start_date": pa.array(pol_policy_start_date, type=pa.string()),
        "policy_end_date": pa.array(pol_policy_end_date, type=pa.string()),
        "ncd_years": pa.array(pol_ncd_years, type=pa.int32()),
        "proposer_age": pa.array(pol_proposer_age, type=pa.int32()),
        "gender": pa.array(pol_gender, type=pa.string()),
        "marital_status": pa.array(pol_marital_status, type=pa.string()),
        "insurance_group": pa.array(pol_insurance_group, type=pa.int32()),
        "postcode_area": pa.array(pol_postcode_area, type=pa.string()),
        "annual_mileage": pa.array(pol_annual_mileage, type=pa.int32()),
        "vehicle_value": pa.array(pol_vehicle_value, type=pa.int32()),
        "vehicle_age": pa.array(pol_vehicle_age, type=pa.int32()),
        "body_type": pa.array(pol_body_type, type=pa.string()),
        "fuel_type": pa.array(pol_fuel_type, type=pa.string()),
        "exposure": pa.array(pol_exposure, type=pa.float32()),
    })
    pq.write_table(pol_table, tmp_pol, compression="zstd", compression_level=3)

    # Write claims chunk
    clm_table = pa.table({
        "claim_id": pa.array(clm_claim_id, type=pa.string()),
        "policy_id": pa.array(clm_policy_id, type=pa.string()),
        "claim_type": pa.array(clm_claim_type, type=pa.string()),
        "fault_status": pa.array(clm_fault_status, type=pa.string()),
        "amount": pa.array(clm_amount, type=pa.float32()),
    })
    pq.write_table(clm_table, tmp_clm, compression="zstd", compression_level=3)

    return tmp_pol, tmp_clm


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a broker book of business from quotes and panel premiums (outputs Parquet)"
    )
    parser.add_argument(
        "--quotes", type=str, required=True,
        help="Path to quotes JSONL file"
    )
    parser.add_argument(
        "--premiums", type=str, required=True,
        help="Path to panel premiums Parquet file (from generate_broker_panel_premiums.py)"
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output path prefix (produces <output>_policies.parquet and <output>_claims.parquet)"
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

    quotes_path = Path(args.quotes)
    if not quotes_path.exists():
        print(f"Error: quotes file not found: {quotes_path}", file=sys.stderr)
        sys.exit(1)

    premiums_path = Path(args.premiums)
    if not premiums_path.exists():
        print(f"Error: premiums file not found: {premiums_path}", file=sys.stderr)
        sys.exit(1)

    out_prefix = Path(args.output)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    pol_path = Path(f"{out_prefix}_policies.parquet")
    clm_path = Path(f"{out_prefix}_claims.parquet")

    # Count lines
    print(f"Counting quotes in {quotes_path}...", file=sys.stderr)
    t0 = time.time()
    total_lines = 0
    with open(quotes_path, "rb") as f:
        for _ in f:
            total_lines += 1
    print(f"  {total_lines:,} quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    # Load shared data
    print("Loading distribution data...", file=sys.stderr)
    _load_shared_data()
    print(f"  Loaded in {time.time() - t0:.1f}s", file=sys.stderr)

    # Load premiums lookup
    print(f"Loading panel premiums from {premiums_path}...", file=sys.stderr)
    t_prem = time.time()
    _load_premiums(str(premiums_path))
    print(f"  Loaded {len(_SHARED_PREMIUMS):,} quote premiums in {time.time() - t_prem:.1f}s",
          file=sys.stderr)

    # Build worker args
    n_workers = max(1, args.workers)
    chunk_size = total_lines // n_workers
    remainder = total_lines % n_workers

    tmp_dir = tempfile.mkdtemp(prefix="bookgen_")
    worker_args = []
    offset = 0
    for i in range(n_workers):
        worker_n = chunk_size + (1 if i < remainder else 0)
        worker_seed = (args.seed + i) if args.seed is not None else None
        tmp_pol = os.path.join(tmp_dir, f"pol_{i:03d}.parquet")
        tmp_clm = os.path.join(tmp_dir, f"clm_{i:03d}.parquet")
        worker_args.append((i, str(quotes_path), offset, worker_n, worker_seed, tmp_pol, tmp_clm))
        offset += worker_n

    print(f"Generating book of business across {n_workers} worker(s) "
          f"(position-based conversion, broker placement)...", file=sys.stderr)
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

    # Concatenate parquet chunks
    print(f"  Concatenating {n_workers} chunks...", file=sys.stderr)
    pol_files = [a[5] for a in worker_args]
    clm_files = [a[6] for a in worker_args]

    pol_tables = [pq.read_table(f) for f in pol_files]
    clm_tables = [pq.read_table(f) for f in clm_files]

    pol_combined = pa.concat_tables(pol_tables)
    clm_combined = pa.concat_tables(clm_tables)

    pq.write_table(pol_combined, str(pol_path), compression="zstd", compression_level=3)
    pq.write_table(clm_combined, str(clm_path), compression="zstd", compression_level=3)

    # Cleanup temp files
    for f in pol_files + clm_files:
        os.unlink(f)
    os.rmdir(tmp_dir)

    elapsed = time.time() - t1
    pol_mb = pol_path.stat().st_size / (1024 * 1024)
    clm_mb = clm_path.stat().st_size / (1024 * 1024)

    print(f"\n  Done in {elapsed:.1f}s ({total_lines / elapsed:,.0f} quotes/sec)", file=sys.stderr)
    print(f"  Policies: {pol_path} ({pol_combined.num_rows:,} rows, {pol_mb:.1f} MB)", file=sys.stderr)
    print(f"  Claims:   {clm_path} ({clm_combined.num_rows:,} rows, {clm_mb:.1f} MB)", file=sys.stderr)

    # Summary stats
    if pol_combined.num_rows > 0:
        conv_rate = pol_combined.num_rows / total_lines * 100
        avg_claims = clm_combined.num_rows / pol_combined.num_rows
        print(f"  Conversion: {conv_rate:.1f}%  |  Claims/policy: {avg_claims:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
