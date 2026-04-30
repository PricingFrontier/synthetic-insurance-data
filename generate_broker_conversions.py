"""
Generate quote-to-sale conversions for both new business and renewal quotes.

Reads:
  - New business quotes (JSONL) + broker prices (Parquet) → new business conversions
  - Existing book of policies (Parquet) → renewal retention outcomes

New business conversion uses the position-based model from generate_claims.py,
rescaled for broker context (~25-30% base purchase rate vs 65% aggregator).

Renewal retention uses a price-sensitivity model targeting ~50% overall retention.

Output is two Parquet files:
  - <output>_new_business.parquet: converted new business policies
  - <output>_renewals.parquet:     renewal outcomes for every book policy

Usage:
    uv run python generate_broker_conversions.py \\
        --quotes data/output/quotes/quotes_1m.jsonl \\
        --broker-prices data/output/broker/broker_prices_1m.parquet \\
        --book data/output/broker/book_policies.parquet \\
        --seed 42 --workers 20 \\
        --output data/output/broker/conversions
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
    _MARKET_PURCHASE_RATE,
)
from broker_data.config import (
    TIER_NAMES,
    IPT_RATE,
    DEFAULT_BROKER,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

BROKER_NB_PURCHASE_RATE = 0.30

# New business tier selection weights (customers shopping new prefer value)
_NB_TIER_WEIGHTS = {"gold": 0.15, "silver": 0.55, "bronze": 0.30}
_NB_TIER_NAMES_LIST = list(_NB_TIER_WEIGHTS.keys())
_NB_TIER_PROBS = np.array([_NB_TIER_WEIGHTS[t] for t in _NB_TIER_NAMES_LIST])


# ─────────────────────────────────────────────────────────────────────────────
# Shared data (loaded once in parent, inherited via fork COW)
# ─────────────────────────────────────────────────────────────────────────────

_SHARED_BROKER_PRICES = None   # dict: quote_id -> per-tier broker price data
_SHARED_BOOK = None            # dict of column arrays from book Parquet
_SHARED_BOOK_CLAIMS = None     # dict: policy_id -> (n_claims, n_at_fault)


def _load_broker_prices(prices_path: str):
    """Load broker prices Parquet and build a lookup dict per quote.

    For each quote_id, stores a dict mapping tier name to:
      (customer_premium, ipt_amount, broker_fee, total_price,
       selected_underwriter, commission_rate, commission_amount,
       net_to_underwriter)
    """
    global _SHARED_BROKER_PRICES
    table = pq.read_table(prices_path)
    quote_ids = table.column("quote_id").to_pylist()

    # Also grab quotes_available
    quotes_available = table.column("quotes_available").to_numpy(zero_copy_only=False)

    # Build column arrays
    col_arrays = {}
    for col_name in table.column_names:
        if col_name in ("quote_id", "total_broker_revenue", "strategy"):
            continue
        col_arrays[col_name] = table.column(col_name).to_numpy(zero_copy_only=False)

    _SHARED_BROKER_PRICES = {}
    for row_idx, qid in enumerate(quote_ids):
        tiers = {}
        for tier_name in TIER_NAMES:
            prem_col = f"{tier_name}_customer_premium"
            prem = col_arrays[prem_col][row_idx]
            if np.isnan(prem):
                continue

            tiers[tier_name] = (
                float(prem),
                float(col_arrays[f"{tier_name}_ipt"][row_idx]),
                float(col_arrays[f"{tier_name}_broker_fee"][row_idx]),
                float(col_arrays[f"{tier_name}_total_price"][row_idx]),
                col_arrays[f"{tier_name}_selected_underwriter"][row_idx]
                if isinstance(col_arrays[f"{tier_name}_selected_underwriter"][row_idx], str)
                else str(col_arrays[f"{tier_name}_selected_underwriter"][row_idx]),
                float(col_arrays[f"{tier_name}_commission_rate"][row_idx]),
                float(col_arrays[f"{tier_name}_commission_amount"][row_idx]),
                float(col_arrays[f"{tier_name}_net_to_underwriter"][row_idx]),
            )

        if tiers:
            _SHARED_BROKER_PRICES[qid] = (tiers, int(quotes_available[row_idx]))


def _load_book(book_path: str, claims_path: str | None):
    """Load the prior-year book of policies and optional claims summary."""
    global _SHARED_BOOK, _SHARED_BOOK_CLAIMS

    table = pq.read_table(book_path)
    _SHARED_BOOK = {
        col: table.column(col).to_pylist() for col in table.column_names
    }

    # Build claims summary per policy from claims Parquet if available
    _SHARED_BOOK_CLAIMS = {}
    if claims_path and Path(claims_path).exists():
        clm_table = pq.read_table(claims_path)
        policy_ids = clm_table.column("policy_id").to_pylist()
        fault_statuses = clm_table.column("fault_status").to_pylist()

        for pid, fault in zip(policy_ids, fault_statuses):
            if pid not in _SHARED_BOOK_CLAIMS:
                _SHARED_BOOK_CLAIMS[pid] = [0, 0]  # [total, at_fault]
            _SHARED_BOOK_CLAIMS[pid][0] += 1
            if fault == "at_fault":
                _SHARED_BOOK_CLAIMS[pid][1] += 1


# ─────────────────────────────────────────────────────────────────────────────
# New business worker
# ─────────────────────────────────────────────────────────────────────────────

def _nb_worker(args: tuple) -> str:
    worker_id, input_path, start_line, n_lines, seed, tmp_out = args

    broker_prices = _SHARED_BROKER_PRICES
    rng = np.random.default_rng(seed)

    rescale = BROKER_NB_PURCHASE_RATE / _MARKET_PURCHASE_RATE

    # Output column lists
    out_policy_id = []
    out_quote_id = []
    out_cover_level = []
    out_selected_underwriter = []
    out_customer_premium = []
    out_ipt_amount = []
    out_broker_fee = []
    out_total_price = []
    out_commission_rate = []
    out_commission_amount = []
    out_net_to_underwriter = []
    out_quote_date = []
    out_sale_date = []
    out_policy_start_date = []
    out_proposer_age = []
    out_ncd_years = []
    out_gender = []
    out_marital_status = []
    out_insurance_group = []
    out_postcode_area = []
    out_annual_mileage = []
    out_vehicle_value = []
    out_vehicle_age = []
    out_body_type = []
    out_fuel_type = []
    out_business_type = []

    policy_counter = 0

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

            # Look up broker prices for this quote
            price_data = broker_prices.get(quote_id) if broker_prices else None
            if price_data is None:
                continue

            tiers, n_competitors = price_data

            # 1. Pick a tier
            tier_name = rng.choice(_NB_TIER_NAMES_LIST, p=_NB_TIER_PROBS)

            tier_data = tiers.get(tier_name)
            if tier_data is None:
                # Fall back to any available tier
                available = list(tiers.keys())
                if not available:
                    continue
                tier_name = rng.choice(available)
                tier_data = tiers[tier_name]

            (customer_premium, ipt_amount, tier_broker_fee, total_price,
             selected_uw, commission_rate, commission_amount,
             net_to_underwriter) = tier_data

            # 2. Compute conversion probability
            age = _calc_age(proposer.get("date_of_birth"))
            licence_type = proposer.get("licence", {}).get("licence_type", "full_uk")
            brand_noise = float(rng.lognormal(0, 0.15))

            # Broker context: rank=1, ratio=1.0, gap=0.0
            conv_prob = _conversion_probability(
                rank=1,
                ratio_to_cheapest=1.0,
                gap_to_next_pct=0.0,
                n_competitors=max(1, n_competitors),
                cheapest_premium=total_price,
                age=age,
                licence_type=licence_type,
                brand_noise=brand_noise,
            )

            # Rescale from aggregator purchase rate to broker purchase rate
            conv_prob *= rescale

            conv_prob = min(max(conv_prob, 0.001), 0.90)

            if rng.random() > conv_prob:
                continue

            # ── Sale ──────────────────────────────────────────────────────
            policy_counter += 1
            pid = f"POL-NB-{worker_id:03d}-{policy_counter:09d}"

            # Dates
            quote_ts = quote["quote_metadata"].get("quote_timestamp", "")
            quote_date_str = quote_ts[:10] if quote_ts else None
            policy_start_str = policy.get("cover_start_date")

            sale_date_str = quote_date_str
            if quote_date_str and policy_start_str:
                q_date = date.fromisoformat(quote_date_str)
                p_start = date.fromisoformat(policy_start_str)
                gap_days = max(0, (p_start - q_date).days)
                sale_offset = int(rng.uniform(0, max(1, gap_days + 1)))
                sale_date_str = (q_date + timedelta(days=sale_offset)).isoformat()

            ncd_years = policy.get("ncd_years", 0)

            # Risk factors
            gender = proposer.get("gender", "unknown")
            marital_status = proposer.get("marital_status", "unknown")
            insurance_group = vehicle.get("insurance_group", 20)
            address = quote.get("address", {})
            postcode = address.get("postcode", "")
            postcode_area = "".join(c for c in postcode.strip().split()[0] if c.isalpha()) if postcode else ""
            annual_mileage = policy.get("annual_mileage", 8000)
            vehicle_value = vehicle.get("estimated_value", 10000)
            vehicle_age_val = max(0, date.today().year - vehicle.get("year_of_manufacture", 2020))
            body_type = vehicle.get("body_type", "hatchback")
            fuel_type = vehicle.get("fuel_type", "petrol")

            out_policy_id.append(pid)
            out_quote_id.append(quote_id)
            out_cover_level.append(tier_name)
            out_selected_underwriter.append(selected_uw)
            out_customer_premium.append(round(customer_premium, 2))
            out_ipt_amount.append(round(ipt_amount, 2))
            out_broker_fee.append(round(tier_broker_fee, 2))
            out_total_price.append(round(total_price, 2))
            out_commission_rate.append(round(commission_rate, 4))
            out_commission_amount.append(round(commission_amount, 2))
            out_net_to_underwriter.append(round(net_to_underwriter, 2))
            out_quote_date.append(quote_date_str)
            out_sale_date.append(sale_date_str)
            out_policy_start_date.append(policy_start_str)
            out_proposer_age.append(age)
            out_ncd_years.append(ncd_years)
            out_gender.append(gender)
            out_marital_status.append(marital_status)
            out_insurance_group.append(insurance_group)
            out_postcode_area.append(postcode_area)
            out_annual_mileage.append(annual_mileage)
            out_vehicle_value.append(vehicle_value)
            out_vehicle_age.append(vehicle_age_val)
            out_body_type.append(body_type)
            out_fuel_type.append(fuel_type)
            out_business_type.append("new_business")

    # Write chunk
    out_table = pa.table({
        "policy_id": pa.array(out_policy_id, type=pa.string()),
        "quote_id": pa.array(out_quote_id, type=pa.string()),
        "cover_level": pa.array(out_cover_level, type=pa.string()),
        "selected_underwriter": pa.array(out_selected_underwriter, type=pa.string()),
        "customer_premium": pa.array(out_customer_premium, type=pa.float32()),
        "ipt_amount": pa.array(out_ipt_amount, type=pa.float32()),
        "broker_fee": pa.array(out_broker_fee, type=pa.float32()),
        "total_price": pa.array(out_total_price, type=pa.float32()),
        "commission_rate": pa.array(out_commission_rate, type=pa.float32()),
        "commission_amount": pa.array(out_commission_amount, type=pa.float32()),
        "net_to_underwriter": pa.array(out_net_to_underwriter, type=pa.float32()),
        "quote_date": pa.array(out_quote_date, type=pa.string()),
        "sale_date": pa.array(out_sale_date, type=pa.string()),
        "policy_start_date": pa.array(out_policy_start_date, type=pa.string()),
        "proposer_age": pa.array(out_proposer_age, type=pa.int32()),
        "ncd_years": pa.array(out_ncd_years, type=pa.int32()),
        "business_type": pa.array(out_business_type, type=pa.string()),
    })
    pq.write_table(out_table, tmp_out, compression="zstd", compression_level=3)

    return tmp_out


# ─────────────────────────────────────────────────────────────────────────────
# Renewal retention model
# ─────────────────────────────────────────────────────────────────────────────

def _compute_retention_probability(
    prior_premium: float,
    renewal_premium: float,
    ncd_years: int,
    proposer_age: int,
    at_fault_claims: int,
) -> float:
    """Compute probability that a policyholder renews.

    Targeting ~50% overall retention after all adjustments.
    """
    base_retention = 0.48

    # Price increase penalty
    if prior_premium > 0:
        increase_pct = (renewal_premium - prior_premium) / prior_premium
        if increase_pct > 0.10:
            base_retention *= math.exp(-2.0 * (increase_pct - 0.10))

    # NCD loyalty bonus
    if ncd_years >= 5:
        base_retention *= 1.10

    # Age loyalty
    if proposer_age > 50:
        base_retention *= 1.15
    elif proposer_age < 25:
        base_retention *= 0.85

    # Claims penalty
    if at_fault_claims > 0:
        base_retention *= 0.80 ** at_fault_claims

    return min(max(base_retention, 0.05), 0.90)


def _generate_renewals(seed: int | None) -> pa.Table:
    """Generate renewal outcomes for every policy in the book.

    Runs in a single pass (no JSONL, no multiprocessing needed).
    """
    book = _SHARED_BOOK
    claims_lookup = _SHARED_BOOK_CLAIMS
    rng = np.random.default_rng(seed)

    n_policies = len(book["policy_id"])
    broker_fee = DEFAULT_BROKER.broker_fee

    # Output columns
    out_policy_id = []
    out_quote_id = []
    out_cover_level = []
    out_underwriter = []
    out_prior_premium = []
    out_renewal_premium = []
    out_premium_change_pct = []
    out_ipt_amount = []
    out_broker_fee = []
    out_total_renewal_price = []
    out_commission_rate = []
    out_commission_amount = []
    out_retained = []
    out_retention_probability = []
    out_ncd_years = []
    out_prior_ncd_years = []
    out_claims_in_year = []
    out_at_fault_claims = []
    out_proposer_age = []
    out_business_type = []

    for i in range(n_policies):
        policy_id = book["policy_id"][i]
        quote_id = book["quote_id"][i]
        cover_level = book["cover_level"][i]
        underwriter = book["underwriter"][i]
        prior_premium = float(book["inception_premium"][i])
        prior_ncd = int(book["ncd_years"][i])
        proposer_age = int(book["proposer_age"][i])
        commission_rate = float(book["commission_rate"][i])

        # Look up claims for this policy
        claim_info = claims_lookup.get(policy_id, [0, 0])
        n_claims = claim_info[0]
        n_at_fault = claim_info[1]

        # Generate renewal premium
        inflation = rng.uniform(0.03, 0.08)
        claims_loading = 0.15 * n_at_fault
        ncd_improvement = -0.02 if n_at_fault == 0 else 0.0

        renewal_factor = 1.0 + inflation + claims_loading + ncd_improvement
        renewal_factor *= float(rng.lognormal(0, 0.05))
        renewal_premium = round(prior_premium * renewal_factor, 2)

        # Premium change
        if prior_premium > 0:
            change_pct = round((renewal_premium - prior_premium) / prior_premium, 4)
        else:
            change_pct = 0.0

        # Financials
        ipt_amount = round(renewal_premium * IPT_RATE, 2)
        total_renewal_price = round(renewal_premium + ipt_amount + broker_fee, 2)
        commission_amount = round(renewal_premium * commission_rate, 2)

        # NCD update
        if n_at_fault == 0:
            new_ncd = min(prior_ncd + 1, 20)
        else:
            # Reset NCD: lose 2 years per at-fault claim, floor at 0
            new_ncd = max(0, prior_ncd - 2 * n_at_fault)

        # Retention probability
        retention_prob = _compute_retention_probability(
            prior_premium, renewal_premium, prior_ncd, proposer_age, n_at_fault,
        )

        retained = bool(rng.random() < retention_prob)

        out_policy_id.append(policy_id)
        out_quote_id.append(quote_id)
        out_cover_level.append(cover_level)
        out_underwriter.append(underwriter)
        out_prior_premium.append(round(prior_premium, 2))
        out_renewal_premium.append(renewal_premium)
        out_premium_change_pct.append(change_pct)
        out_ipt_amount.append(ipt_amount)
        out_broker_fee.append(broker_fee)
        out_total_renewal_price.append(total_renewal_price)
        out_commission_rate.append(round(commission_rate, 4))
        out_commission_amount.append(commission_amount)
        out_retained.append(retained)
        out_retention_probability.append(round(retention_prob, 4))
        out_ncd_years.append(new_ncd)
        out_prior_ncd_years.append(prior_ncd)
        out_claims_in_year.append(n_claims)
        out_at_fault_claims.append(n_at_fault)
        out_proposer_age.append(proposer_age)
        out_business_type.append("renewal")

    return pa.table({
        "policy_id": pa.array(out_policy_id, type=pa.string()),
        "quote_id": pa.array(out_quote_id, type=pa.string()),
        "cover_level": pa.array(out_cover_level, type=pa.string()),
        "underwriter": pa.array(out_underwriter, type=pa.string()),
        "prior_premium": pa.array(out_prior_premium, type=pa.float32()),
        "renewal_premium": pa.array(out_renewal_premium, type=pa.float32()),
        "premium_change_pct": pa.array(out_premium_change_pct, type=pa.float32()),
        "ipt_amount": pa.array(out_ipt_amount, type=pa.float32()),
        "broker_fee": pa.array(out_broker_fee, type=pa.float32()),
        "total_renewal_price": pa.array(out_total_renewal_price, type=pa.float32()),
        "commission_rate": pa.array(out_commission_rate, type=pa.float32()),
        "commission_amount": pa.array(out_commission_amount, type=pa.float32()),
        "retained": pa.array(out_retained, type=pa.bool_()),
        "retention_probability": pa.array(out_retention_probability, type=pa.float32()),
        "ncd_years": pa.array(out_ncd_years, type=pa.int32()),
        "prior_ncd_years": pa.array(out_prior_ncd_years, type=pa.int32()),
        "claims_in_year": pa.array(out_claims_in_year, type=pa.int32()),
        "at_fault_claims": pa.array(out_at_fault_claims, type=pa.int32()),
        "proposer_age": pa.array(out_proposer_age, type=pa.int32()),
        "business_type": pa.array(out_business_type, type=pa.string()),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate new business conversions and renewal retention outcomes (outputs Parquet)"
    )
    parser.add_argument(
        "--quotes", type=str, required=True,
        help="Path to quotes JSONL file"
    )
    parser.add_argument(
        "--broker-prices", type=str, required=True,
        help="Path to broker prices Parquet file (from generate_broker_prices.py)"
    )
    parser.add_argument(
        "--book", type=str, required=True,
        help="Path to book policies Parquet file (from generate_broker_book.py, _policies.parquet)"
    )
    parser.add_argument(
        "--book-claims", type=str, default=None,
        help="Path to book claims Parquet file (from generate_broker_book.py, _claims.parquet). "
             "If not provided, will be inferred from --book path."
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output path prefix (produces <output>_new_business.parquet and <output>_renewals.parquet)"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel workers for new business processing (default: 1)"
    )
    args = parser.parse_args()

    # Validate inputs
    quotes_path = Path(args.quotes)
    if not quotes_path.exists():
        print(f"Error: quotes file not found: {quotes_path}", file=sys.stderr)
        sys.exit(1)

    prices_path = Path(args.broker_prices)
    if not prices_path.exists():
        print(f"Error: broker prices file not found: {prices_path}", file=sys.stderr)
        sys.exit(1)

    book_path = Path(args.book)
    if not book_path.exists():
        print(f"Error: book file not found: {book_path}", file=sys.stderr)
        sys.exit(1)

    # Infer claims path if not provided
    if args.book_claims:
        claims_path = args.book_claims
    else:
        # If book is book_policies.parquet, try book_claims.parquet
        book_str = str(book_path)
        if "_policies.parquet" in book_str:
            claims_path = book_str.replace("_policies.parquet", "_claims.parquet")
        else:
            claims_path = None

    out_prefix = Path(args.output)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    nb_path = Path(f"{out_prefix}_new_business.parquet")
    ren_path = Path(f"{out_prefix}_renewals.parquet")

    t0 = time.time()

    # ── Phase 1: Renewals ────────────────────────────────────────────────────
    print("Loading book of business...", file=sys.stderr)
    t_book = time.time()
    _load_book(str(book_path), claims_path)
    n_book = len(_SHARED_BOOK["policy_id"])
    n_claims_loaded = sum(v[0] for v in _SHARED_BOOK_CLAIMS.values())
    print(f"  {n_book:,} policies, {n_claims_loaded:,} claims in {time.time() - t_book:.1f}s",
          file=sys.stderr)

    print("Generating renewal outcomes...", file=sys.stderr)
    t_ren = time.time()
    renewal_seed = (args.seed + 1000) if args.seed is not None else None
    renewal_table = _generate_renewals(renewal_seed)

    # Write renewals
    pq.write_table(renewal_table, str(ren_path), compression="zstd", compression_level=3)

    n_retained = sum(1 for v in renewal_table.column("retained").to_pylist() if v)
    retention_rate = n_retained / renewal_table.num_rows * 100 if renewal_table.num_rows > 0 else 0
    ren_mb = ren_path.stat().st_size / (1024 * 1024)
    print(f"  Renewals: {ren_path} ({renewal_table.num_rows:,} rows, {ren_mb:.1f} MB)",
          file=sys.stderr)
    print(f"  Retention: {n_retained:,}/{renewal_table.num_rows:,} = {retention_rate:.1f}%",
          file=sys.stderr)
    print(f"  Done in {time.time() - t_ren:.1f}s", file=sys.stderr)

    # ── Phase 2: New Business ────────────────────────────────────────────────
    print(f"\nCounting quotes in {quotes_path}...", file=sys.stderr)
    t_count = time.time()
    total_lines = 0
    with open(quotes_path, "rb") as f:
        for _ in f:
            total_lines += 1
    print(f"  {total_lines:,} quotes ({time.time() - t_count:.1f}s)", file=sys.stderr)

    print(f"Loading broker prices from {prices_path}...", file=sys.stderr)
    t_prices = time.time()
    _load_broker_prices(str(prices_path))
    print(f"  Loaded {len(_SHARED_BROKER_PRICES):,} priced quotes in {time.time() - t_prices:.1f}s",
          file=sys.stderr)

    # Build worker args
    n_workers = max(1, args.workers)
    chunk_size = total_lines // n_workers
    remainder = total_lines % n_workers

    tmp_dir = tempfile.mkdtemp(prefix="convgen_")
    worker_args = []
    offset = 0
    for i in range(n_workers):
        worker_n = chunk_size + (1 if i < remainder else 0)
        worker_seed = (args.seed + i) if args.seed is not None else None
        tmp_out = os.path.join(tmp_dir, f"nb_{i:03d}.parquet")
        worker_args.append((i, str(quotes_path), offset, worker_n, worker_seed, tmp_out))
        offset += worker_n

    print(f"Generating new business conversions across {n_workers} worker(s)...", file=sys.stderr)
    t_nb = time.time()

    if n_workers > 1:
        ctx = mp.get_context("fork")
        with ctx.Pool(processes=n_workers) as pool:
            completed = 0
            for _ in pool.imap_unordered(_nb_worker, worker_args):
                completed += 1
                elapsed = time.time() - t_nb
                rate = int(completed / n_workers * total_lines / elapsed) if elapsed > 0 else 0
                print(f"  Worker {completed}/{n_workers} done  "
                      f"({elapsed:.0f}s elapsed, ~{rate:,} quotes/sec)",
                      file=sys.stderr)
    else:
        _nb_worker(worker_args[0])

    # Concatenate new business chunks
    print(f"  Concatenating {n_workers} chunks...", file=sys.stderr)
    nb_files = [a[5] for a in worker_args]
    nb_tables = [pq.read_table(f) for f in nb_files]
    nb_combined = pa.concat_tables(nb_tables)

    pq.write_table(nb_combined, str(nb_path), compression="zstd", compression_level=3)

    # Cleanup temp files
    for f in nb_files:
        os.unlink(f)
    os.rmdir(tmp_dir)

    nb_mb = nb_path.stat().st_size / (1024 * 1024)
    nb_conv_rate = nb_combined.num_rows / total_lines * 100 if total_lines > 0 else 0

    elapsed = time.time() - t0
    print(f"\n  New business: {nb_path} ({nb_combined.num_rows:,} rows, {nb_mb:.1f} MB)",
          file=sys.stderr)
    print(f"  NB conversion: {nb_combined.num_rows:,}/{total_lines:,} = {nb_conv_rate:.1f}%",
          file=sys.stderr)
    print(f"\n  Total time: {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
