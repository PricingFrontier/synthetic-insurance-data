"""
Generate a synthetic claims dataset from existing quote JSONL files.

Reads quotes, converts a subset to bound policies, assigns exposure,
and generates claims with realistic frequency and severity by peril.

Conversion uses a position-based model: probability of binding depends on
BritSure's price rank, ratio to cheapest, gap advantage, premium level,
renewal status, driver age, and per-quote brand-preference noise.

Output is two Parquet files:
  - <output>_policies.parquet: one row per bound policy (ids, dates, premium)
  - <output>_claims.parquet:   one row per claim linked to policy_id

Usage:
    uv run python generate_claims.py --input data/output/quotes/quotes_1k.jsonl --premiums data/output/competitor_premiums/premiums_1k.parquet --output data/output/claims/claims
    uv run python generate_claims.py --input data/output/quotes/quotes_10m.jsonl --premiums data/output/competitor_premiums/premiums_10m.parquet --seed 42 --workers 20 --output data/output/claims/britsure
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


# ── Peril distribution (count-based, from ABI / plan doc) ────────────────────
PERIL_WEIGHTS = {
    "accidental_damage":        0.40,
    "third_party_property":     0.25,
    "third_party_bodily_injury": 0.05,
    "windscreen":               0.15,
    "theft":                    0.08,
    "storm_flood":              0.03,
    "fire":                     0.01,
    "vandalism":                0.03,
}

# ── Severity parameters per peril (log-normal μ, σ) ──────────────────────────
SEVERITY_PARAMS = {
    "accidental_damage":        (7.5,  0.8),   # mean ~£2,500
    "third_party_property":     (7.3,  0.9),   # mean ~£2,200
    "third_party_bodily_injury": (8.5, 1.3),   # mean ~£8,000 (blended minor+serious)
    "windscreen":               (5.9,  0.3),   # mean ~£400
    "theft":                    (8.5,  1.0),   # mean ~£7,000
    "storm_flood":              (7.8,  1.0),   # mean ~£3,500
    "fire":                     (8.8,  0.8),   # mean ~£8,500
    "vandalism":                (6.5,  0.7),   # mean ~£900
}

# ── Fault probabilities per peril ─────────────────────────────────────────────
FAULT_PROBS = {
    "accidental_damage":        {"at_fault": 0.55, "not_at_fault": 0.35, "split_liability": 0.10},
    "third_party_property":     {"at_fault": 0.50, "not_at_fault": 0.40, "split_liability": 0.10},
    "third_party_bodily_injury": {"at_fault": 0.45, "not_at_fault": 0.45, "split_liability": 0.10},
    "windscreen":               {"not_at_fault": 1.00},
    "theft":                    {"not_at_fault": 1.00},
    "storm_flood":              {"not_at_fault": 1.00},
    "fire":                     {"not_at_fault": 1.00},
    "vandalism":                {"not_at_fault": 1.00},
}

# ── Mid-term cancellation rate ──────────────────────────────────────────────
CANCELLATION_RATE = 0.08


# ─────────────────────────────────────────────────────────────────────────────
# Shared data (loaded once in parent, inherited via fork COW)
# ─────────────────────────────────────────────────────────────────────────────
_SHARED_DATA = None
_SHARED_PREMIUMS = None  # dict: quote_id -> (britsure_prem, rank, ratio_to_cheapest, gap_to_next_pct, n_competitors, cheapest_prem)

def _load_shared_data():
    global _SHARED_DATA
    from generator.data_loader import DistributionData
    _SHARED_DATA = DistributionData()

def _load_premiums(premiums_path: str):
    """Load competitor premiums and precompute conversion context per quote.

    For each quote stores a tuple:
      (britsure_premium, rank, ratio_to_cheapest, gap_to_next_pct,
       n_competitors, cheapest_premium)
    """
    global _SHARED_PREMIUMS
    table = pq.read_table(premiums_path)
    quote_ids = table.column("quote_id").to_pylist()

    # All insurer columns (everything except quote_id and cheapest)
    ins_cols = [c for c in table.column_names if c not in ("quote_id", "cheapest")]
    premiums_matrix = np.column_stack([
        table.column(c).to_numpy(zero_copy_only=False) for c in ins_cols
    ])  # shape (N, n_insurers)

    brit_idx = ins_cols.index("britsure_direct")
    brit_premiums = premiums_matrix[:, brit_idx]

    _SHARED_PREMIUMS = {}
    for i, qid in enumerate(quote_ids):
        brit_prem = brit_premiums[i]
        if math.isnan(brit_prem):
            continue  # BritSure declined this quote

        row = premiums_matrix[i]
        valid = row[~np.isnan(row)]
        n_competitors = len(valid)
        sorted_valid = np.sort(valid)

        cheapest = sorted_valid[0]
        # Rank = number of competitors strictly cheaper + 1
        rank = int((valid < brit_prem).sum()) + 1

        ratio_to_cheapest = brit_prem / cheapest if cheapest > 0 else 1.0

        # Gap to next competitor (how far ahead/behind of nearest rival)
        if rank == 1 and len(sorted_valid) > 1:
            gap_to_next_pct = (sorted_valid[1] - brit_prem) / brit_prem
        else:
            gap_to_next_pct = 0.0

        _SHARED_PREMIUMS[qid] = (
            float(brit_prem), rank, float(ratio_to_cheapest),
            float(gap_to_next_pct), n_competitors, float(cheapest),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Position-based conversion model
# ─────────────────────────────────────────────────────────────────────────────

# Market-wide: ~65% of people who quote actually buy from someone
_MARKET_PURCHASE_RATE = 0.65

def _conversion_probability(
    rank: int,
    ratio_to_cheapest: float,
    gap_to_next_pct: float,
    n_competitors: int,
    cheapest_premium: float,
    age: int,
    licence_type: str,
    brand_noise: float,
) -> float:
    """Compute probability that this aggregator quote converts to a BritSure policy.

    Factors modelled:
      1. Price rank           – cheapest wins most often, steep falloff
      2. Ratio to cheapest    – exponential decay as price exceeds cheapest
      3. Gap advantage        – big lead over 2nd place boosts conversion
      4. Competitor count     – fewer rivals = higher win rate
      5. Premium level        – cheap policies → price-sensitive buyers
      6. Age/price sensitivity – young = price-driven, old = brand-driven
      7. Provisional licence   – less likely to actually purchase
      8. Brand noise          – per-quote random affinity for BritSure
      9. Market purchase rate – not everyone who quotes actually buys
    """
    # 1. Base probability from rank (conditional on the customer buying)
    rank_base = {
        1: 0.28, 2: 0.16, 3: 0.10, 4: 0.065, 5: 0.042,
        6: 0.028, 7: 0.019, 8: 0.013, 9: 0.009, 10: 0.006,
    }
    # fallback for rank > 10: max(0.002, 0.006 * (0.72 ** (rank - 10)))
    prob = rank_base.get(rank, max(0.002, 0.006 * (0.72 ** (rank - 10))))

    # 2. Ratio-to-cheapest penalty (even rank 1 has ratio ≥ 1.0)
    if ratio_to_cheapest > 1.0:
        excess = ratio_to_cheapest - 1.0
        # At 5% over: ×0.78,  10% over: ×0.61,  20% over: ×0.37,  50% over: ×0.08
        prob *= math.exp(-5.0 * excess)

    # 3. Gap advantage when cheapest — big lead means customer stops looking
    if rank == 1 and gap_to_next_pct > 0:
        # 10% cheaper than 2nd → 20% bonus; capped at +50%
        prob *= 1.0 + min(gap_to_next_pct * 2.0, 0.50)

    # 4. Fewer competitors quoting → less choice → higher win rate
    if n_competitors <= 8:
        prob *= 1.25
    elif n_competitors <= 12:
        prob *= 1.15
    elif n_competitors <= 15:
        prob *= 1.05
    # 16+ competitors: no bonus (the normal case)

    # 5. Premium-level price sensitivity
    if cheapest_premium < 400:
        # Budget buyers: rank matters more, non-cheapest penalised
        if rank > 1:
            prob *= 0.80
        else:
            prob *= 1.10
    elif cheapest_premium > 1500:
        # High-value buyers: brand/service matters, less sensitive to rank
        if rank <= 3:
            prob *= 1.15

    # 6. Brand-preference noise (log-normal centred on 1.0)
    #    Some customers just like BritSure; others don't
    prob *= brand_noise

    # 7. Age-based price sensitivity
    if age < 25:
        # Young drivers are very price-conscious
        if rank > 2:
            prob *= 0.70
    elif age > 60:
        # Older drivers value brand recognition
        if rank <= 3:
            prob *= 1.10

    # 8. Provisional licence holders less likely to purchase
    if licence_type == "provisional_uk":
        prob *= 0.50

    # 9. Market non-purchase rate
    prob *= _MARKET_PURCHASE_RATE

    return min(max(prob, 0.001), 0.92)


# ─────────────────────────────────────────────────────────────────────────────
# Claims generation logic
# ─────────────────────────────────────────────────────────────────────────────

def _calc_age(dob_str: str | None) -> int:
    if not dob_str:
        return 40
    try:
        dob = date.fromisoformat(dob_str)
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except (ValueError, TypeError):
        return 40


def _gen_exposure(rng: np.random.Generator) -> float:
    if rng.random() < CANCELLATION_RATE:
        return round(rng.uniform(1 / 12, 11 / 12), 4)
    return 1.0


def _gen_claims(
    rng: np.random.Generator,
    data,
    peril_names: list[str],
    peril_probs: np.ndarray,
    exposure: float,
    age: int,
    gender: str,
    insurance_group: int,
    annual_mileage: int,
    vehicle_age: int,
) -> list[tuple[str, str, float]]:
    """Generate claims for a single policy. Returns list of (peril, fault, amount)."""
    base_rate = data.get_claim_rate(age)

    # Vehicle risk
    if insurance_group >= 35:
        veh_factor = 1.3
    elif insurance_group >= 25:
        veh_factor = 1.1
    elif insurance_group <= 10:
        veh_factor = 0.85
    else:
        veh_factor = 1.0

    mileage_factor = annual_mileage / 8000

    if vehicle_age > 10:
        age_factor = 1.1
    elif vehicle_age < 3:
        age_factor = 0.9
    else:
        age_factor = 1.0

    gender_factor = 1.05 if gender == "male" else 0.95

    annual_rate = base_rate * veh_factor * mileage_factor * age_factor * gender_factor
    expected = annual_rate * exposure

    n_claims = min(int(rng.poisson(expected)), 8)

    claims = []
    for _ in range(n_claims):
        peril = rng.choice(peril_names, p=peril_probs)
        # Fault
        probs = FAULT_PROBS.get(peril, {"not_at_fault": 1.0})
        keys = list(probs.keys())
        weights = np.array([probs[k] for k in keys])
        weights /= weights.sum()
        fault = rng.choice(keys, p=weights)
        # Severity
        mu, sigma = SEVERITY_PARAMS.get(peril, (7.5, 1.0))
        amount = max(50.0, float(rng.lognormal(mu, sigma)))
        claims.append((peril, fault, round(amount, 2)))

    return claims


# ─────────────────────────────────────────────────────────────────────────────
# Worker: read a slice of input JSONL, write two parquet chunks
# ─────────────────────────────────────────────────────────────────────────────

def _worker(args: tuple) -> tuple[str, str]:
    worker_id, input_path, start_line, n_lines, seed, tmp_pol, tmp_clm = args

    data = _SHARED_DATA
    premiums = _SHARED_PREMIUMS
    rng = np.random.default_rng(seed)

    peril_names = list(PERIL_WEIGHTS.keys())
    peril_probs = np.array(list(PERIL_WEIGHTS.values()))
    peril_probs /= peril_probs.sum()

    # Policy columns
    pol_policy_id = []
    pol_quote_id = []
    pol_quote_date = []
    pol_sale_date = []
    pol_policy_start_date = []
    pol_inception_premium = []

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
            quote_id = quote["quote_metadata"]["quote_id"]

            # Look up precomputed conversion context
            ctx = premiums.get(quote_id) if premiums else None
            if ctx is None:
                continue  # BritSure declined or missing

            brit_prem, rank, ratio_to_cheapest, gap_to_next_pct, n_competitors, cheapest_prem = ctx

            age = _calc_age(proposer.get("date_of_birth"))
            licence_type = proposer.get("licence", {}).get("licence_type", "full_uk")

            # Per-quote brand-preference noise (log-normal, mean=1.0, sd~0.15)
            brand_noise = float(rng.lognormal(0, 0.15))

            conv_prob = _conversion_probability(
                rank, ratio_to_cheapest, gap_to_next_pct,
                n_competitors, cheapest_prem,
                age, licence_type, brand_noise,
            )

            if rng.random() > conv_prob:
                continue

            policy_counter += 1
            pid = f"POL-{worker_id:03d}-{policy_counter:09d}"

            # Extract dates
            quote_ts = quote["quote_metadata"].get("quote_timestamp", "")
            quote_date_str = quote_ts[:10] if quote_ts else None
            policy_start_str = policy.get("cover_start_date")

            # Generate sale date: between quote date and policy start
            sale_date_str = quote_date_str
            if quote_date_str and policy_start_str:
                q_date = date.fromisoformat(quote_date_str)
                p_start = date.fromisoformat(policy_start_str)
                gap_days = max(0, (p_start - q_date).days)
                sale_offset = int(rng.uniform(0, max(1, gap_days + 1)))
                sale_date_str = (q_date + timedelta(days=sale_offset)).isoformat()

            # Risk factors for claims generation
            vehicle = quote["vehicle"]
            gender = proposer.get("gender", "male")
            ins_group = vehicle.get("insurance_group", 20)
            annual_mileage = policy.get("annual_mileage", 8000)
            veh_age = date.today().year - vehicle.get("year_of_manufacture", 2020)
            exposure = _gen_exposure(rng)

            claims = _gen_claims(
                rng, data, peril_names, peril_probs,
                exposure, age, gender, ins_group, annual_mileage, veh_age,
            )

            pol_policy_id.append(pid)
            pol_quote_id.append(quote_id)
            pol_quote_date.append(quote_date_str)
            pol_sale_date.append(sale_date_str)
            pol_policy_start_date.append(policy_start_str)
            pol_inception_premium.append(round(brit_prem, 2))

            for peril, fault, amount in claims:
                claim_counter += 1
                clm_claim_id.append(f"CLM-{worker_id:03d}-{claim_counter:09d}")
                clm_policy_id.append(pid)
                clm_claim_type.append(peril)
                clm_fault_status.append(fault)
                clm_amount.append(amount)

    # Write policy chunk
    pol_table = pa.table({
        "policy_id": pa.array(pol_policy_id, type=pa.string()),
        "quote_id": pa.array(pol_quote_id, type=pa.string()),
        "quote_date": pa.array(pol_quote_date, type=pa.string()),
        "sale_date": pa.array(pol_sale_date, type=pa.string()),
        "policy_start_date": pa.array(pol_policy_start_date, type=pa.string()),
        "inception_premium": pa.array(pol_inception_premium, type=pa.float32()),
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
        description="Generate a synthetic claims dataset from quote JSONL files (outputs Parquet)"
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="Path to quotes JSONL file"
    )
    parser.add_argument(
        "--premiums", type=str, required=True,
        help="Path to competitor premiums Parquet file (from generate_premiums.py)"
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

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
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
    print(f"Counting quotes in {input_path}...", file=sys.stderr)
    t0 = time.time()
    total_lines = 0
    with open(input_path, "rb") as f:
        for _ in f:
            total_lines += 1
    print(f"  {total_lines:,} quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    # Load shared data
    print("Loading distribution data...", file=sys.stderr)
    _load_shared_data()
    print(f"  Loaded in {time.time() - t0:.1f}s", file=sys.stderr)

    # Load premiums lookup
    print(f"Loading premiums from {premiums_path}...", file=sys.stderr)
    t_prem = time.time()
    _load_premiums(str(premiums_path))
    print(f"  Loaded {len(_SHARED_PREMIUMS):,} premiums in {time.time() - t_prem:.1f}s", file=sys.stderr)

    # Build worker args
    n_workers = max(1, args.workers)
    chunk_size = total_lines // n_workers
    remainder = total_lines % n_workers

    tmp_dir = tempfile.mkdtemp(prefix="claimgen_")
    worker_args = []
    offset = 0
    for i in range(n_workers):
        worker_n = chunk_size + (1 if i < remainder else 0)
        worker_seed = (args.seed + i) if args.seed is not None else None
        tmp_pol = os.path.join(tmp_dir, f"pol_{i:03d}.parquet")
        tmp_clm = os.path.join(tmp_dir, f"clm_{i:03d}.parquet")
        worker_args.append((i, str(input_path), offset, worker_n, worker_seed, tmp_pol, tmp_clm))
        offset += worker_n

    print(f"Generating claims across {n_workers} worker(s) (position-based conversion)...", file=sys.stderr)
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


if __name__ == "__main__":
    main()
