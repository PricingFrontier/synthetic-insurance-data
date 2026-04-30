"""
Generate monthly instalment schedules from converted policies.

Reads new business conversions and renewal retention outcomes, then expands
each converted policy into a payment schedule: a single row for annual payers,
or 12 rows (deposit + 11 monthly payments) for monthly payers.

Monthly payers incur a credit charge (APR-based interest) spread across
payments 2-12, and are subject to a stochastic payment failure model.

Usage:
    uv run python generate_broker_instalments.py \\
        --new-business data/output/broker/conversions_new_business.parquet \\
        --renewals data/output/broker/conversions_renewals.parquet \\
        --seed 42 \\
        --output data/output/broker/instalments.parquet
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from broker_data.config import DEFAULT_BROKER


# ─────────────────────────────────────────────────────────────────────────────
# Date arithmetic
# ─────────────────────────────────────────────────────────────────────────────

def _add_months(d: date, months: int) -> date:
    """Add *months* calendar months to *d*, clamping day to month end."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, max_day))


# ─────────────────────────────────────────────────────────────────────────────
# Payment method selection
# ─────────────────────────────────────────────────────────────────────────────

def _annual_probability(age: int) -> float:
    """Return probability of choosing annual payment based on proposer age."""
    if age >= 50:
        return 0.55
    if age < 25:
        return 0.35
    return 0.45


# ─────────────────────────────────────────────────────────────────────────────
# Payment failure model
# ─────────────────────────────────────────────────────────────────────────────

def _failure_rate(age: int, total_price: float) -> float:
    """Per-payment failure probability for monthly instalments (payments 2-12)."""
    if age < 25:
        rate = 0.04
    elif age >= 50:
        rate = 0.01
    else:
        rate = 0.02

    if total_price > 2000:
        rate += 0.01
    elif total_price < 500:
        rate -= 0.005

    return max(rate, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Core expansion
# ─────────────────────────────────────────────────────────────────────────────

def _build_instalments(
    policy_ids: list,
    business_types: list,
    total_prices: list,
    start_dates: list,
    cover_levels: list,
    underwriters: list,
    proposer_ages: list,
    rng: np.random.Generator,
) -> dict[str, list]:
    """Expand a list of policies into instalment rows.

    Returns a dict of column-name -> list ready for ``pa.table()``.
    """
    apr = DEFAULT_BROKER.credit_apr
    admin_fee = DEFAULT_BROKER.monthly_admin_fee

    # Pre-allocate output lists
    out = {
        "policy_id": [],
        "business_type": [],
        "payment_frequency": [],
        "payment_number": [],
        "payment_due_date": [],
        "amount": [],
        "deposit_amount": [],
        "credit_charge": [],
        "admin_fee": [],
        "cumulative_paid": [],
        "payment_status": [],
        "cover_level": [],
        "underwriter": [],
        "total_annual_price": [],
        "proposer_age": [],
    }

    n_policies = len(policy_ids)

    for i in range(n_policies):
        pid = policy_ids[i]
        btype = business_types[i]
        tp = float(total_prices[i])
        age = int(proposer_ages[i])
        cl = cover_levels[i]
        uw = underwriters[i]

        # Parse start date
        sd_raw = start_dates[i]
        if isinstance(sd_raw, str):
            start_dt = date.fromisoformat(sd_raw)
        else:
            # numpy datetime64 or similar
            start_dt = date.fromisoformat(str(sd_raw)[:10])

        # ── Payment method ───────────────────────────────────────────────
        is_annual = rng.random() < _annual_probability(age)

        if is_annual:
            # Single annual payment
            out["policy_id"].append(pid)
            out["business_type"].append(btype)
            out["payment_frequency"].append("annual")
            out["payment_number"].append(1)
            out["payment_due_date"].append(start_dt.isoformat())
            out["amount"].append(round(tp, 2))
            out["deposit_amount"].append(0.0)
            out["credit_charge"].append(0.0)
            out["admin_fee"].append(0.0)
            out["cumulative_paid"].append(round(tp, 2))
            out["payment_status"].append("paid")
            out["cover_level"].append(cl)
            out["underwriter"].append(uw)
            out["total_annual_price"].append(round(tp, 2))
            out["proposer_age"].append(age)
            continue

        # ── Monthly payment schedule ─────────────────────────────────────
        deposit = tp * 0.15
        financed = tp - deposit
        monthly_amount = financed * (1 + apr / 100) / 11
        credit_per_payment = monthly_amount - financed / 11

        fail_rate = _failure_rate(age, tp)

        cumulative = 0.0
        cancelled = False
        prior_failed = False  # whether the previous payment failed

        for pn in range(1, 13):
            due_date = _add_months(start_dt, pn - 1)

            if pn == 1:
                # Deposit payment
                amt = round(deposit, 2)
                dep_amt = round(deposit, 2)
                cc = 0.0
                af = 0.0
                status = "paid"
                cumulative += amt
            else:
                # Monthly instalment
                amt = round(monthly_amount + admin_fee, 2)
                dep_amt = 0.0
                cc = round(credit_per_payment, 2)
                af = round(admin_fee, 2)

                if cancelled:
                    status = "cancelled"
                    # Don't add to cumulative for cancelled payments
                elif prior_failed:
                    # Previous month failed — 70% recover (pay both
                    # the missed payment and the current one), 30% cancel
                    if rng.random() < 0.70:
                        status = "paid_late"
                        cumulative += amt * 2  # recover missed + current
                    else:
                        status = "cancelled"
                        cancelled = True
                    prior_failed = False
                elif rng.random() < fail_rate:
                    status = "failed"
                    prior_failed = True
                    # Failed payment — not added to cumulative yet
                else:
                    status = "paid"
                    cumulative += amt

            out["policy_id"].append(pid)
            out["business_type"].append(btype)
            out["payment_frequency"].append("monthly")
            out["payment_number"].append(pn)
            out["payment_due_date"].append(due_date.isoformat())
            out["amount"].append(amt)
            out["deposit_amount"].append(dep_amt)
            out["credit_charge"].append(cc)
            out["admin_fee"].append(af)
            out["cumulative_paid"].append(round(cumulative, 2))
            out["payment_status"].append(status)
            out["cover_level"].append(cl)
            out["underwriter"].append(uw)
            out["total_annual_price"].append(round(tp, 2))
            out["proposer_age"].append(age)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Input loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_new_business(path: str) -> tuple[list, list, list, list, list, list, list]:
    """Load NB conversions and return parallel column lists."""
    table = pq.read_table(path)

    policy_ids = table.column("policy_id").to_pylist()
    total_prices = table.column("total_price").to_pylist()
    start_dates = table.column("policy_start_date").to_pylist()
    cover_levels = table.column("cover_level").to_pylist()
    underwriters = table.column("selected_underwriter").to_pylist()
    proposer_ages = table.column("proposer_age").to_pylist()
    business_types = ["new_business"] * len(policy_ids)

    return policy_ids, business_types, total_prices, start_dates, cover_levels, underwriters, proposer_ages


def _load_renewals(path: str) -> tuple[list, list, list, list, list, list]:
    """Load renewal conversions (retained only) and return parallel column lists.

    .. note:: This function is **unused** — ``_load_renewals_with_dates`` is
       called by ``main()`` instead, because it also resolves start dates.
       Kept for reference / simpler call-sites that don't need dates.
    """
    table = pq.read_table(path)

    # Filter to retained policies only
    retained_col = table.column("retained").to_pylist()
    mask = [bool(r) for r in retained_col]

    policy_ids = []
    total_prices = []
    cover_levels = []
    underwriters = []
    proposer_ages = []

    pid_col = table.column("policy_id").to_pylist()
    tp_col = table.column("total_renewal_price").to_pylist()
    cl_col = table.column("cover_level").to_pylist()
    uw_col = table.column("underwriter").to_pylist()
    age_col = table.column("proposer_age").to_pylist()

    for i, keep in enumerate(mask):
        if keep:
            policy_ids.append(pid_col[i])
            total_prices.append(tp_col[i])
            cover_levels.append(cl_col[i])
            underwriters.append(uw_col[i])
            proposer_ages.append(age_col[i])

    business_types = ["renewal"] * len(policy_ids)

    return policy_ids, business_types, total_prices, cover_levels, underwriters, proposer_ages


def _load_renewals_with_dates(
    renewals_path: str,
    book_path: str | None,
) -> tuple[list, list, list, list, list, list, list]:
    """Load retained renewals and resolve policy start dates.

    If *book_path* is provided, reads it to look up the original
    ``policy_start_date`` and advances it by one year for the renewal term.
    Otherwise falls back to a default date.
    """
    table = pq.read_table(renewals_path)

    retained_col = table.column("retained").to_pylist()
    pid_col = table.column("policy_id").to_pylist()
    tp_col = table.column("total_renewal_price").to_pylist()
    cl_col = table.column("cover_level").to_pylist()
    uw_col = table.column("underwriter").to_pylist()
    age_col = table.column("proposer_age").to_pylist()

    # Build book start-date lookup if available
    book_dates: dict[str, str] = {}
    if book_path and Path(book_path).exists():
        book_table = pq.read_table(book_path, columns=["policy_id", "policy_start_date"])
        bk_pids = book_table.column("policy_id").to_pylist()
        bk_dates = book_table.column("policy_start_date").to_pylist()
        for pid, sd in zip(bk_pids, bk_dates):
            book_dates[pid] = sd

    policy_ids = []
    business_types = []
    total_prices = []
    start_dates = []
    cover_levels = []
    underwriters = []
    proposer_ages = []

    for i, keep in enumerate(retained_col):
        if not keep:
            continue

        pid = pid_col[i]
        policy_ids.append(pid)
        business_types.append("renewal")
        total_prices.append(tp_col[i])
        cover_levels.append(cl_col[i])
        underwriters.append(uw_col[i])
        proposer_ages.append(age_col[i])

        # Renewal start = original start + 1 year
        orig_sd = book_dates.get(pid)
        if orig_sd:
            orig_dt = date.fromisoformat(str(orig_sd)[:10])
            renewal_dt = _add_months(orig_dt, 12)
            start_dates.append(renewal_dt.isoformat())
        else:
            # Fallback — mid-year default
            start_dates.append("2025-06-01")

    return policy_ids, business_types, total_prices, start_dates, cover_levels, underwriters, proposer_ages


# ─────────────────────────────────────────────────────────────────────────────
# Output schema
# ─────────────────────────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = pa.schema([
    ("policy_id", pa.string()),
    ("business_type", pa.string()),
    ("payment_frequency", pa.string()),
    ("payment_number", pa.int32()),
    ("payment_due_date", pa.string()),
    ("amount", pa.float32()),
    ("deposit_amount", pa.float32()),
    ("credit_charge", pa.float32()),
    ("admin_fee", pa.float32()),
    ("cumulative_paid", pa.float32()),
    ("payment_status", pa.string()),
    ("cover_level", pa.string()),
    ("underwriter", pa.string()),
    ("total_annual_price", pa.float32()),
    ("proposer_age", pa.int32()),
])


def _to_table(data: dict[str, list]) -> pa.Table:
    """Convert output dict to a typed PyArrow table."""
    return pa.table(
        {
            "policy_id": pa.array(data["policy_id"], type=pa.string()),
            "business_type": pa.array(data["business_type"], type=pa.string()),
            "payment_frequency": pa.array(data["payment_frequency"], type=pa.string()),
            "payment_number": pa.array(data["payment_number"], type=pa.int32()),
            "payment_due_date": pa.array(data["payment_due_date"], type=pa.string()),
            "amount": pa.array(data["amount"], type=pa.float32()),
            "deposit_amount": pa.array(data["deposit_amount"], type=pa.float32()),
            "credit_charge": pa.array(data["credit_charge"], type=pa.float32()),
            "admin_fee": pa.array(data["admin_fee"], type=pa.float32()),
            "cumulative_paid": pa.array(data["cumulative_paid"], type=pa.float32()),
            "payment_status": pa.array(data["payment_status"], type=pa.string()),
            "cover_level": pa.array(data["cover_level"], type=pa.string()),
            "underwriter": pa.array(data["underwriter"], type=pa.string()),
            "total_annual_price": pa.array(data["total_annual_price"], type=pa.float32()),
            "proposer_age": pa.array(data["proposer_age"], type=pa.int32()),
        },
        schema=_OUTPUT_SCHEMA,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate instalment payment schedules from converted policies"
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
        "--book", type=str, default=None,
        help="Path to book policies Parquet (used to resolve renewal start dates). "
             "If not provided, will be inferred from --renewals path."
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output Parquet path for instalment schedule"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility"
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

    # Infer book path if not provided
    book_path = args.book
    if book_path is None:
        # Convention: conversions live alongside the book
        ren_dir = ren_path.parent
        candidate = ren_dir / "book_policies.parquet"
        if candidate.exists():
            book_path = str(candidate)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    t0 = time.time()

    # ── Load new business ────────────────────────────────────────────────────
    print("Loading new business conversions...", file=sys.stderr)
    nb_pids, nb_btypes, nb_prices, nb_dates, nb_covers, nb_uws, nb_ages = \
        _load_new_business(str(nb_path))
    print(f"  {len(nb_pids):,} new business policies", file=sys.stderr)

    # ── Load renewals ────────────────────────────────────────────────────────
    print("Loading renewal conversions (retained only)...", file=sys.stderr)
    ren_pids, ren_btypes, ren_prices, ren_dates, ren_covers, ren_uws, ren_ages = \
        _load_renewals_with_dates(str(ren_path), book_path)
    print(f"  {len(ren_pids):,} retained renewal policies", file=sys.stderr)

    # ── Union ────────────────────────────────────────────────────────────────
    all_pids = nb_pids + ren_pids
    all_btypes = nb_btypes + ren_btypes
    all_prices = nb_prices + ren_prices
    all_dates = nb_dates + ren_dates
    all_covers = nb_covers + ren_covers
    all_uws = nb_uws + ren_uws
    all_ages = nb_ages + ren_ages

    n_total = len(all_pids)
    print(f"\nGenerating instalment schedules for {n_total:,} policies...", file=sys.stderr)

    # ── Build instalment rows ────────────────────────────────────────────────
    data = _build_instalments(
        all_pids, all_btypes, all_prices, all_dates,
        all_covers, all_uws, all_ages, rng,
    )

    n_rows = len(data["policy_id"])

    # ── Write output ─────────────────────────────────────────────────────────
    table = _to_table(data)
    pq.write_table(table, str(out_path), compression="zstd", compression_level=3)

    elapsed = time.time() - t0
    mb = out_path.stat().st_size / (1024 * 1024)

    # ── Summary stats ────────────────────────────────────────────────────────
    freq_col = data["payment_frequency"]
    status_col = data["payment_status"]

    n_annual_policies = sum(1 for f in freq_col if f == "annual")
    n_monthly_policies = n_total - n_annual_policies
    n_failed = sum(1 for s in status_col if s == "failed")
    n_late = sum(1 for s in status_col if s == "paid_late")
    n_cancelled = sum(1 for s in status_col if s == "cancelled")

    print(f"\n  Output: {out_path} ({n_rows:,} rows, {mb:.1f} MB)", file=sys.stderr)
    print(f"  Policies: {n_total:,} ({n_annual_policies:,} annual, "
          f"{n_monthly_policies:,} monthly)", file=sys.stderr)
    print(f"  Payment statuses: {n_failed:,} failed, {n_late:,} paid_late, "
          f"{n_cancelled:,} cancelled", file=sys.stderr)
    print(f"  Time: {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
