"""
Generate a demand model dataset from broker panel premiums, prices, and conversions.

Reads:
  - Panel premiums Parquet (5 UW x 3 tiers — the full quote_id universe)
  - Broker prices Parquet (selected UW premiums + commissions per tier)
  - NB conversions Parquet (which quotes converted, at which cover level)
  - Renewals Parquet (renewal outcomes for the prior-year book)

Outputs a single Parquet file suitable for GLM/GBM demand modelling:
  - One row per quote (new business) — sold=True/False
  - One row per renewal invite — sold=retained

Usage:
    uv run python generate_broker_demand_model.py \\
        --panel-premiums data/output/broker/quotes_broker_1m_premiums.parquet \\
        --broker-prices data/output/broker/broker_prices_1m.parquet \\
        --nb-conversions data/output/broker/conversions_1m_new_business.parquet \\
        --renewals data/output/broker/conversions_1m_renewals.parquet \\
        --output data/output/broker/demand_model_1m.parquet
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from broker_data.config import TIER_NAMES


# ─────────────────────────────────────────────────────────────────────────────
# New business rows
# ─────────────────────────────────────────────────────────────────────────────

def _build_new_business(
    panel_path: str,
    prices_path: str,
    nb_path: str,
) -> pa.Table:
    """Build new business demand rows: one per quote_id from panel premiums."""

    print("  Reading panel premiums...", file=sys.stderr)
    t0 = time.time()
    panel = pq.read_table(panel_path)
    panel_quote_ids = panel.column("quote_id").to_pylist()
    n_panel = len(panel_quote_ids)
    print(f"    {n_panel:,} quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    # ── Load broker prices ───────────────────────────────────────────────────
    print("  Reading broker prices...", file=sys.stderr)
    t0 = time.time()
    prices = pq.read_table(prices_path)
    price_quote_ids = prices.column("quote_id").to_pylist()

    # Build lookup: quote_id -> row index in prices table
    price_idx_map = {qid: idx for idx, qid in enumerate(price_quote_ids)}

    # Extract per-tier arrays from broker prices
    tier_premium_arrays = {}
    tier_commission_arrays = {}
    for tier_name in TIER_NAMES:
        tier_premium_arrays[tier_name] = prices.column(
            f"{tier_name}_customer_premium"
        ).to_numpy(zero_copy_only=False).astype(np.float32)
        tier_commission_arrays[tier_name] = prices.column(
            f"{tier_name}_commission_amount"
        ).to_numpy(zero_copy_only=False).astype(np.float32)

    quotes_available_arr = prices.column("quotes_available").to_numpy(
        zero_copy_only=False
    ).astype(np.int32)
    print(f"    {len(price_quote_ids):,} priced quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    # ── Load NB conversions ──────────────────────────────────────────────────
    print("  Reading NB conversions...", file=sys.stderr)
    t0 = time.time()
    nb = pq.read_table(nb_path)
    nb_quote_ids = nb.column("quote_id").to_pylist()
    nb_cover_levels = nb.column("cover_level").to_pylist()

    # Build lookup: quote_id -> cover_level (first match wins if duplicates)
    nb_sold_map = {}
    for qid, cl in zip(nb_quote_ids, nb_cover_levels):
        if qid not in nb_sold_map:
            nb_sold_map[qid] = cl
    print(f"    {len(nb_sold_map):,} converted quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    # ── Build output arrays ──────────────────────────────────────────────────
    print(f"  Building {n_panel:,} NB rows...", file=sys.stderr)
    t0 = time.time()

    out_quote_id = panel_quote_ids
    out_business_type = ["new_business"] * n_panel
    out_sold = [False] * n_panel
    out_sold_cover_level = [""] * n_panel

    out_gold_net = np.full(n_panel, np.nan, dtype=np.float32)
    out_gold_comm = np.full(n_panel, np.nan, dtype=np.float32)
    out_silver_net = np.full(n_panel, np.nan, dtype=np.float32)
    out_silver_comm = np.full(n_panel, np.nan, dtype=np.float32)
    out_bronze_net = np.full(n_panel, np.nan, dtype=np.float32)
    out_bronze_comm = np.full(n_panel, np.nan, dtype=np.float32)
    out_quotes_available = np.zeros(n_panel, dtype=np.int32)

    # Map tier names to output arrays for cleaner access
    net_arrays = {"gold": out_gold_net, "silver": out_silver_net, "bronze": out_bronze_net}
    comm_arrays = {"gold": out_gold_comm, "silver": out_silver_comm, "bronze": out_bronze_comm}

    for i, qid in enumerate(panel_quote_ids):
        # Prices lookup
        pidx = price_idx_map.get(qid)
        if pidx is not None:
            for tier_name in TIER_NAMES:
                net_arrays[tier_name][i] = tier_premium_arrays[tier_name][pidx]
                comm_arrays[tier_name][i] = tier_commission_arrays[tier_name][pidx]
            out_quotes_available[i] = quotes_available_arr[pidx]

        # Sold lookup
        sold_cl = nb_sold_map.get(qid)
        if sold_cl is not None:
            out_sold[i] = True
            out_sold_cover_level[i] = sold_cl

    print(f"    Done ({time.time() - t0:.1f}s)", file=sys.stderr)

    return pa.table({
        "quote_id": pa.array(out_quote_id, type=pa.string()),
        "business_type": pa.array(out_business_type, type=pa.string()),
        "sold": pa.array(out_sold, type=pa.bool_()),
        "sold_cover_level": pa.array(out_sold_cover_level, type=pa.string()),
        "gold_net_premium": pa.array(out_gold_net, type=pa.float32()),
        "gold_broker_commission": pa.array(out_gold_comm, type=pa.float32()),
        "silver_net_premium": pa.array(out_silver_net, type=pa.float32()),
        "silver_broker_commission": pa.array(out_silver_comm, type=pa.float32()),
        "bronze_net_premium": pa.array(out_bronze_net, type=pa.float32()),
        "bronze_broker_commission": pa.array(out_bronze_comm, type=pa.float32()),
        "quotes_available": pa.array(out_quotes_available, type=pa.int32()),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Renewal rows
# ─────────────────────────────────────────────────────────────────────────────

def _build_renewals(renewals_path: str) -> pa.Table:
    """Build renewal demand rows from the renewals Parquet."""

    print("  Reading renewals...", file=sys.stderr)
    t0 = time.time()
    ren = pq.read_table(renewals_path)
    n_ren = ren.num_rows
    print(f"    {n_ren:,} renewal rows ({time.time() - t0:.1f}s)", file=sys.stderr)

    print(f"  Building {n_ren:,} renewal rows...", file=sys.stderr)
    t0 = time.time()

    quote_ids = ren.column("quote_id").to_pylist()
    cover_levels = ren.column("cover_level").to_pylist()
    retained_arr = ren.column("retained").to_pylist()
    renewal_premiums = ren.column("renewal_premium").to_numpy(zero_copy_only=False).astype(np.float32)
    commission_amounts = ren.column("commission_amount").to_numpy(zero_copy_only=False).astype(np.float32)

    out_quote_id = quote_ids
    out_business_type = ["renewal"] * n_ren
    out_sold = [bool(r) for r in retained_arr]
    out_sold_cover_level = cover_levels  # renewals always have a cover level

    out_gold_net = np.full(n_ren, np.nan, dtype=np.float32)
    out_gold_comm = np.full(n_ren, np.nan, dtype=np.float32)
    out_silver_net = np.full(n_ren, np.nan, dtype=np.float32)
    out_silver_comm = np.full(n_ren, np.nan, dtype=np.float32)
    out_bronze_net = np.full(n_ren, np.nan, dtype=np.float32)
    out_bronze_comm = np.full(n_ren, np.nan, dtype=np.float32)
    out_quotes_available = np.ones(n_ren, dtype=np.int32)

    # Map tier names to output arrays
    net_arrays = {"gold": out_gold_net, "silver": out_silver_net, "bronze": out_bronze_net}
    comm_arrays = {"gold": out_gold_comm, "silver": out_silver_comm, "bronze": out_bronze_comm}

    for i in range(n_ren):
        cl = cover_levels[i]
        if cl in net_arrays:
            net_arrays[cl][i] = renewal_premiums[i]
            comm_arrays[cl][i] = commission_amounts[i]

    print(f"    Done ({time.time() - t0:.1f}s)", file=sys.stderr)

    return pa.table({
        "quote_id": pa.array(out_quote_id, type=pa.string()),
        "business_type": pa.array(out_business_type, type=pa.string()),
        "sold": pa.array(out_sold, type=pa.bool_()),
        "sold_cover_level": pa.array(out_sold_cover_level, type=pa.string()),
        "gold_net_premium": pa.array(out_gold_net, type=pa.float32()),
        "gold_broker_commission": pa.array(out_gold_comm, type=pa.float32()),
        "silver_net_premium": pa.array(out_silver_net, type=pa.float32()),
        "silver_broker_commission": pa.array(out_silver_comm, type=pa.float32()),
        "bronze_net_premium": pa.array(out_bronze_net, type=pa.float32()),
        "bronze_broker_commission": pa.array(out_bronze_comm, type=pa.float32()),
        "quotes_available": pa.array(out_quotes_available, type=pa.int32()),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate demand model dataset from broker panel data (outputs Parquet)"
    )
    parser.add_argument(
        "--panel-premiums", type=str, required=True,
        help="Path to panel premiums Parquet (from generate_broker_panel_premiums.py, "
             "_premiums.parquet companion)",
    )
    parser.add_argument(
        "--broker-prices", type=str, required=True,
        help="Path to broker prices Parquet (from generate_broker_prices.py)",
    )
    parser.add_argument(
        "--nb-conversions", type=str, required=True,
        help="Path to new business conversions Parquet "
             "(from generate_broker_conversions.py, _new_business.parquet)",
    )
    parser.add_argument(
        "--renewals", type=str, required=True,
        help="Path to renewals Parquet (from generate_broker_conversions.py, _renewals.parquet)",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output Parquet file path for the demand model dataset",
    )
    args = parser.parse_args()

    # Validate inputs
    for label, path_str in [
        ("panel premiums", args.panel_premiums),
        ("broker prices", args.broker_prices),
        ("NB conversions", args.nb_conversions),
        ("renewals", args.renewals),
    ]:
        if not Path(path_str).exists():
            print(f"Error: {label} file not found: {path_str}", file=sys.stderr)
            sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t_total = time.time()

    # ── New business ─────────────────────────────────────────────────────────
    print("Building new business rows...", file=sys.stderr)
    nb_table = _build_new_business(args.panel_premiums, args.broker_prices, args.nb_conversions)

    nb_sold = sum(1 for v in nb_table.column("sold").to_pylist() if v)
    print(f"  NB: {nb_table.num_rows:,} rows, {nb_sold:,} sold "
          f"({nb_sold / nb_table.num_rows * 100:.1f}%)", file=sys.stderr)

    # ── Renewals ─────────────────────────────────────────────────────────────
    print("\nBuilding renewal rows...", file=sys.stderr)
    ren_table = _build_renewals(args.renewals)

    ren_sold = sum(1 for v in ren_table.column("sold").to_pylist() if v)
    print(f"  Renewals: {ren_table.num_rows:,} rows, {ren_sold:,} retained "
          f"({ren_sold / ren_table.num_rows * 100:.1f}%)", file=sys.stderr)

    # ── Combine and write ────────────────────────────────────────────────────
    print("\nCombining and writing output...", file=sys.stderr)
    combined = pa.concat_tables([nb_table, ren_table])
    pq.write_table(combined, str(out_path), compression="zstd", compression_level=3)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    elapsed = time.time() - t_total
    print(f"  Saved: {out_path} ({size_mb:,.1f} MB)", file=sys.stderr)
    print(f"  Shape: {combined.num_rows:,} rows x {combined.num_columns} cols", file=sys.stderr)
    print(f"  Columns: {combined.column_names}", file=sys.stderr)
    print(f"  Total time: {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
