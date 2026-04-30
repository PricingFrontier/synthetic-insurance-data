"""
Generate optimised broker prices from panel premiums.

Reads the panel Parquet (5 underwriters x 3 cover levels), applies commission
optimisation, and outputs customer-facing prices with full revenue breakdown.

Usage:
    uv run python generate_broker_prices.py --input data/output/broker/panel_premiums_1m.parquet --strategy balanced --output data/output/broker/broker_prices_1m.parquet
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from broker_data.config import (
    PANEL_UNDERWRITERS,
    UNDERWRITER_COLUMNS,
    TIER_NAMES,
    IPT_RATE,
    DEFAULT_BROKER,
)


N_UW = len(PANEL_UNDERWRITERS)
N_TIERS = len(TIER_NAMES)

# Underwriter display names (for the selected_underwriter output column)
UW_NAMES = [uw.insurer.name for uw in PANEL_UNDERWRITERS]


# ─────────────────────────────────────────────────────────────────────────────
# Strategy implementations
# ─────────────────────────────────────────────────────────────────────────────

def _select_cheapest(
    premiums: list[float],
    commissions: list[float],
    broker_fee_share: float,
) -> int:
    """Select the underwriter with the lowest total customer price."""
    best_idx = -1
    best_price = np.inf
    for i, prem in enumerate(premiums):
        total = prem + prem * IPT_RATE + broker_fee_share
        if total < best_price:
            best_price = total
            best_idx = i
    return best_idx


def _select_max_commission(
    premiums: list[float],
    commissions: list[float],
    broker_fee_share: float,
) -> int:
    """Select highest commission, constrained to within 15% of cheapest total price."""
    # Find cheapest total price
    totals = [p + p * IPT_RATE + broker_fee_share for p in premiums]
    cheapest_total = min(totals)
    threshold = cheapest_total * 1.15

    best_idx = -1
    best_comm = -np.inf
    for i, (total, comm) in enumerate(zip(totals, commissions)):
        if total <= threshold and comm > best_comm:
            best_comm = comm
            best_idx = i

    # Fallback: if nothing within threshold (shouldn't happen since cheapest
    # is always within threshold), pick cheapest
    if best_idx == -1:
        best_idx = int(np.argmin(totals))

    return best_idx


def _select_balanced(
    premiums: list[float],
    commissions: list[float],
    broker_fee_share: float,
) -> int:
    """Score = 0.6 * (1 - price_rank_norm) + 0.4 * commission_rank_norm."""
    n = len(premiums)
    if n == 1:
        return 0

    # Total prices for ranking
    totals = [p + p * IPT_RATE + broker_fee_share for p in premiums]

    # Rank by total price (1 = cheapest)
    price_order = np.argsort(totals)
    price_ranks = np.empty(n, dtype=np.float64)
    for rank, idx in enumerate(price_order):
        price_ranks[idx] = rank + 1  # 1-based

    # Rank by commission (1 = lowest commission)
    comm_order = np.argsort(commissions)
    comm_ranks = np.empty(n, dtype=np.float64)
    for rank, idx in enumerate(comm_order):
        comm_ranks[idx] = rank + 1  # 1-based

    denom = n - 1  # guaranteed > 0 since n >= 2

    best_idx = -1
    best_score = -np.inf
    for i in range(n):
        price_norm = (price_ranks[i] - 1) / denom
        comm_norm = (comm_ranks[i] - 1) / denom
        score = 0.6 * (1.0 - price_norm) + 0.4 * comm_norm
        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx


STRATEGIES = {
    "cheapest": _select_cheapest,
    "max_commission": _select_max_commission,
    "balanced": _select_balanced,
}


# ─────────────────────────────────────────────────────────────────────────────
# Processing
# ─────────────────────────────────────────────────────────────────────────────

def process(input_path: str, output_path: str, strategy: str, seed: int | None) -> None:
    """Read panel premiums, apply strategy, write broker prices."""

    select_fn = STRATEGIES[strategy]
    broker_fee = DEFAULT_BROKER.broker_fee
    broker_fee_share = broker_fee / 3.0
    credit_apr = DEFAULT_BROKER.credit_apr

    print(f"Reading {input_path}...", file=sys.stderr)
    t0 = time.time()
    table = pq.read_table(input_path)
    n_rows = table.num_rows
    print(f"  {n_rows:,} rows in {time.time() - t0:.1f}s", file=sys.stderr)

    # Extract quote_id
    quote_ids = table.column("quote_id").to_pylist()

    # Load premium and commission arrays per UW x tier into a dict for fast access
    # Shape: (n_rows, N_UW, N_TIERS) for both premiums and commissions
    prem_arrays = {}   # (uw_idx, tier_idx) -> np.array(n_rows)
    comm_arrays = {}
    for uw_idx, uw_col in enumerate(UNDERWRITER_COLUMNS):
        for tier_idx, tier_name in enumerate(TIER_NAMES):
            prem_col = f"{uw_col}_{tier_name}"
            comm_col = f"{uw_col}_{tier_name}_commission"
            prem_arrays[(uw_idx, tier_idx)] = table.column(prem_col).to_numpy(zero_copy_only=False).astype(np.float64)
            comm_arrays[(uw_idx, tier_idx)] = table.column(comm_col).to_numpy(zero_copy_only=False).astype(np.float64)

    # Pre-allocate output arrays
    # Per tier: customer_premium, ipt, broker_fee, total_price, monthly_price,
    #           selected_underwriter (index), commission_rate, commission_amount, net_to_underwriter
    out_premium = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_ipt = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_fee = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_total = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_monthly = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_uw_idx = np.full((n_rows, N_TIERS), -1, dtype=np.int32)
    out_comm_rate = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_comm_amt = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)
    out_net = np.full((n_rows, N_TIERS), np.nan, dtype=np.float64)

    out_total_revenue = np.full(n_rows, np.nan, dtype=np.float64)
    out_quotes_available = np.zeros(n_rows, dtype=np.int32)

    # Build commission rate lookup: (uw_idx, tier_name) -> rate
    comm_rate_lookup = {}
    for uw_idx, uw in enumerate(PANEL_UNDERWRITERS):
        for tier_name in TIER_NAMES:
            comm_rate_lookup[(uw_idx, tier_name)] = uw.commission_rate(tier_name)

    print(f"Applying '{strategy}' strategy...", file=sys.stderr)
    t1 = time.time()

    monthly_factor = (1.0 + credit_apr / 100.0) / 12.0

    for row in range(n_rows):
        # Track which UWs quoted at least 1 tier (for quotes_available)
        uw_quoted = set()

        for tier_idx, tier_name in enumerate(TIER_NAMES):
            # Collect valid (uw_local_idx, premium, commission) tuples
            candidates_uw = []
            candidates_prem = []
            candidates_comm = []

            for uw_idx in range(N_UW):
                prem = prem_arrays[(uw_idx, tier_idx)][row]
                if np.isnan(prem):
                    continue
                comm = comm_arrays[(uw_idx, tier_idx)][row]
                candidates_uw.append(uw_idx)
                candidates_prem.append(prem)
                candidates_comm.append(comm)
                uw_quoted.add(uw_idx)

            if not candidates_uw:
                # No underwriter quoted for this tier — fields stay NaN
                continue

            # Apply strategy
            local_idx = select_fn(candidates_prem, candidates_comm, broker_fee_share)
            chosen_uw = candidates_uw[local_idx]
            chosen_prem = candidates_prem[local_idx]
            chosen_comm = candidates_comm[local_idx]
            chosen_rate = comm_rate_lookup[(chosen_uw, tier_name)]

            ipt = round(chosen_prem * IPT_RATE, 2)
            total = round(chosen_prem + ipt + broker_fee_share, 2)
            monthly = round(total * monthly_factor, 2)
            net = round(chosen_prem - chosen_comm, 2)

            out_premium[row, tier_idx] = chosen_prem
            out_ipt[row, tier_idx] = ipt
            out_fee[row, tier_idx] = broker_fee_share
            out_total[row, tier_idx] = total
            out_monthly[row, tier_idx] = monthly
            out_uw_idx[row, tier_idx] = chosen_uw
            out_comm_rate[row, tier_idx] = chosen_rate
            out_comm_amt[row, tier_idx] = chosen_comm
            out_net[row, tier_idx] = net

        out_quotes_available[row] = len(uw_quoted)

        # Total broker revenue = sum of commissions across tiers + broker_fee
        tier_comms = out_comm_amt[row]
        valid_comms = tier_comms[~np.isnan(tier_comms)]
        if len(valid_comms) > 0:
            out_total_revenue[row] = round(float(valid_comms.sum()) + broker_fee, 2)

    elapsed = time.time() - t1
    print(f"  Processed {n_rows:,} rows in {elapsed:.1f}s "
          f"({n_rows / elapsed:,.0f} rows/sec)", file=sys.stderr)

    # Build output table
    print("Writing output...", file=sys.stderr)
    t2 = time.time()

    columns = {"quote_id": pa.array(quote_ids, type=pa.string())}

    for tier_idx, tier_name in enumerate(TIER_NAMES):
        columns[f"{tier_name}_customer_premium"] = pa.array(out_premium[:, tier_idx], type=pa.float32())
        columns[f"{tier_name}_ipt"] = pa.array(out_ipt[:, tier_idx], type=pa.float32())
        columns[f"{tier_name}_broker_fee"] = pa.array(out_fee[:, tier_idx], type=pa.float32())
        columns[f"{tier_name}_total_price"] = pa.array(out_total[:, tier_idx], type=pa.float32())
        columns[f"{tier_name}_monthly_price"] = pa.array(out_monthly[:, tier_idx], type=pa.float32())

        # Selected underwriter names
        uw_names = []
        for row in range(n_rows):
            idx = out_uw_idx[row, tier_idx]
            uw_names.append(UW_NAMES[idx] if idx >= 0 else "")
        columns[f"{tier_name}_selected_underwriter"] = pa.array(uw_names, type=pa.string())

        columns[f"{tier_name}_commission_rate"] = pa.array(out_comm_rate[:, tier_idx], type=pa.float32())
        columns[f"{tier_name}_commission_amount"] = pa.array(out_comm_amt[:, tier_idx], type=pa.float32())
        columns[f"{tier_name}_net_to_underwriter"] = pa.array(out_net[:, tier_idx], type=pa.float32())

    columns["total_broker_revenue"] = pa.array(out_total_revenue, type=pa.float32())
    columns["strategy"] = pa.array([strategy] * n_rows, type=pa.string())
    columns["quotes_available"] = pa.array(out_quotes_available, type=pa.int32())

    out_table = pa.table(columns)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(out_table, str(out_path), compression="zstd", compression_level=3)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  Saved: {out_path} ({size_mb:,.1f} MB)", file=sys.stderr)
    print(f"  Shape: {out_table.num_rows:,} rows x {out_table.num_columns} cols", file=sys.stderr)
    print(f"  Total time: {time.time() - t0:.1f}s", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate optimised broker prices from panel premiums (outputs Parquet)"
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="Path to panel premiums Parquet file (from generate_broker_panel_premiums.py)",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output Parquet file path",
    )
    parser.add_argument(
        "--strategy", type=str, default="balanced",
        choices=["cheapest", "max_commission", "balanced"],
        help="Commission optimisation strategy (default: balanced)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed (reserved for future stochastic strategies)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    process(str(input_path), args.output, args.strategy, args.seed)


if __name__ == "__main__":
    main()
