"""
Generate a synthetic claims dataset from existing quote JSONL files.

Reads quotes, converts a subset to bound policies, assigns exposure,
and generates claims with realistic frequency and severity by peril.

Usage:
    uv run python generate_claims.py --input data/output/quotes/quotes_1k.jsonl
    uv run python generate_claims.py --input data/output/quotes/quotes_10k.jsonl --seed 42 --output data/output/claims/claims.jsonl
    uv run python generate_claims.py --input data/output/quotes/quotes_1k.jsonl --bind-rate 1.0  # convert all quotes
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

from generator.data_loader import DistributionData


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

# ── Mid-term cancellation rate and reasons ────────────────────────────────────
CANCELLATION_RATE = 0.08
CANCELLATION_REASONS = {"sold_vehicle": 0.45, "found_cheaper": 0.30, "other": 0.25}


class ClaimsGenerator:
    """Generate claims experience for a portfolio of quotes."""

    def __init__(self, data: DistributionData, seed: int | None = None):
        self.rng = np.random.default_rng(seed)
        self.data = data
        self._policy_counter = 0
        self._claim_counter = 0

        # Pre-compute peril sampling arrays
        self._peril_names = list(PERIL_WEIGHTS.keys())
        self._peril_probs = np.array(list(PERIL_WEIGHTS.values()))
        self._peril_probs /= self._peril_probs.sum()

    def process_quote(self, quote: dict, bind_rate: float) -> dict | None:
        """Convert a quote to a policy with claims, or None if not bound."""
        # ── Quote-to-bind decision ──
        proposer = quote["proposer"]
        policy = quote["policy_details"]

        # Adjust bind rate by risk profile
        effective_rate = bind_rate
        if policy.get("is_renewal"):
            effective_rate = min(0.95, bind_rate * 10)  # renewals convert much more
        if proposer.get("licence", {}).get("licence_type") == "provisional_uk":
            effective_rate *= 0.5

        if self.rng.random() > effective_rate:
            return None

        # ── Policy ID ──
        self._policy_counter += 1
        policy_id = f"POL-{date.today().year}-{self._policy_counter:09d}"
        quote_id = quote["quote_metadata"]["quote_id"]

        # ── Exposure ──
        exposure = self._gen_exposure()

        # ── Extract risk factors for claim generation ──
        proposer_age = self._calc_age(proposer.get("date_of_birth"))
        proposer_gender = proposer.get("gender", "male")
        vehicle = quote["vehicle"]
        insurance_group = vehicle.get("insurance_group", 20)
        annual_mileage = policy.get("annual_mileage", 8000)
        veh_age = date.today().year - vehicle.get("year_of_manufacture", 2020)

        # ── Generate claims ──
        claims = self._gen_claims(
            exposure=exposure,
            age=proposer_age,
            gender=proposer_gender,
            insurance_group=insurance_group,
            annual_mileage=annual_mileage,
            vehicle_age=veh_age,
        )

        # ── Assign claim IDs ──
        for claim in claims:
            self._claim_counter += 1
            claim["claim_id"] = f"CLM-{date.today().year}-{self._claim_counter:09d}"

        return {
            "policy_id": policy_id,
            "quote_id": quote_id,
            "exposure": round(exposure, 4),
            "proposer_age": proposer_age,
            "proposer_gender": proposer_gender,
            "vehicle_make": vehicle.get("make"),
            "vehicle_model": vehicle.get("model"),
            "vehicle_age": veh_age,
            "insurance_group": insurance_group,
            "annual_mileage": annual_mileage,
            "cover_type": policy.get("cover_type"),
            "ncd_years": policy.get("ncd_years"),
            "region": quote["address"].get("city"),
            "postcode_area": self._postcode_area(quote["address"].get("postcode", "")),
            "num_claims": len(claims),
            "claims": claims,
        }

    def _gen_exposure(self) -> float:
        """Generate earned exposure. Most policies run the full year."""
        if self.rng.random() < CANCELLATION_RATE:
            # Mid-term cancellation: uniform between 1 month and 11 months
            return round(self.rng.uniform(1 / 12, 11 / 12), 4)
        return 1.0

    def _gen_claims(
        self,
        exposure: float,
        age: int,
        gender: str,
        insurance_group: int,
        annual_mileage: int,
        vehicle_age: int,
    ) -> list[dict]:
        """Generate claims for a single policy."""
        # Base annual claim rate from age curve
        base_rate = self.data.get_claim_rate(age)

        # ── Multiplicative adjustments ──
        # Vehicle risk: higher insurance group → more expensive to repair → higher rate
        if insurance_group >= 35:
            veh_factor = 1.3
        elif insurance_group >= 25:
            veh_factor = 1.1
        elif insurance_group <= 10:
            veh_factor = 0.85
        else:
            veh_factor = 1.0

        # Mileage: more miles → more exposure to accidents
        mileage_factor = annual_mileage / 8000

        # Vehicle age: older vehicles slightly higher frequency
        if vehicle_age > 10:
            age_factor = 1.1
        elif vehicle_age < 3:
            age_factor = 0.9
        else:
            age_factor = 1.0

        # Gender: males slightly higher (from STATS19 data)
        gender_factor = 1.05 if gender == "male" else 0.95

        # Expected claims for this policy's exposure period
        annual_rate = base_rate * veh_factor * mileage_factor * age_factor * gender_factor
        expected = annual_rate * exposure

        # Poisson draw
        n_claims = int(self.rng.poisson(expected))
        n_claims = min(n_claims, 8)  # cap at 8 per year

        claims = []
        for _ in range(n_claims):
            peril = self._sample_peril()
            fault = self._sample_fault(peril)
            amount = self._sample_severity(peril)

            claims.append({
                "claim_type": peril,
                "fault_status": fault,
                "amount": round(amount, 2),
            })

        return claims

    def _sample_peril(self) -> str:
        return self.rng.choice(self._peril_names, p=self._peril_probs)

    def _sample_fault(self, peril: str) -> str:
        probs = FAULT_PROBS.get(peril, {"not_at_fault": 1.0})
        keys = list(probs.keys())
        weights = np.array([probs[k] for k in keys])
        weights /= weights.sum()
        return self.rng.choice(keys, p=weights)

    def _sample_severity(self, peril: str) -> float:
        mu, sigma = SEVERITY_PARAMS.get(peril, (7.5, 1.0))
        amount = float(self.rng.lognormal(mu, sigma))
        # Floor at £50 (minimum claim value)
        return max(50.0, amount)

    @staticmethod
    def _calc_age(dob_str: str | None) -> int:
        if not dob_str:
            return 40
        try:
            dob = date.fromisoformat(dob_str)
            today = date.today()
            return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        except (ValueError, TypeError):
            return 40

    @staticmethod
    def _postcode_area(postcode: str) -> str:
        """Extract area code from postcode (e.g. 'BS9 4QT' → 'BS')."""
        pc = postcode.strip().split()[0] if postcode else ""
        return "".join(c for c in pc if c.isalpha())


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
    parser = argparse.ArgumentParser(
        description="Generate a synthetic claims dataset from quote JSONL files"
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="Path to quotes JSONL file"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSONL file path. Prints to stdout if not specified."
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--bind-rate", type=float, default=0.07,
        help="Base quote-to-bind conversion rate (default: 0.07 = 7%%)"
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="Pretty-print JSON output"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Load distribution data (for claim rate curves)
    print("Loading distribution data...", file=sys.stderr)
    t0 = time.time()
    data = DistributionData()
    print(f"  Loaded in {time.time() - t0:.1f}s", file=sys.stderr)

    gen = ClaimsGenerator(data, seed=args.seed)

    # Read quotes
    print(f"Reading quotes from {input_path}...", file=sys.stderr)
    quotes = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                quotes.append(json.loads(line))
    print(f"  Read {len(quotes)} quotes", file=sys.stderr)

    # Generate policies with claims
    print(f"Generating claims (bind rate: {args.bind_rate:.0%})...", file=sys.stderr)
    t1 = time.time()
    policies = []
    for quote in quotes:
        result = gen.process_quote(quote, bind_rate=args.bind_rate)
        if result is not None:
            policies.append(result)

    gen_time = time.time() - t1
    total_claims = sum(p["num_claims"] for p in policies)
    policies_with_claims = sum(1 for p in policies if p["num_claims"] > 0)

    print(f"  {len(policies)} policies bound from {len(quotes)} quotes "
          f"({len(policies) / len(quotes):.1%} conversion)", file=sys.stderr)
    print(f"  {total_claims} claims across {policies_with_claims} policies "
          f"({policies_with_claims / len(policies):.1%} claim rate)" if policies else "",
          file=sys.stderr)
    print(f"  Generated in {gen_time:.2f}s", file=sys.stderr)

    # Output
    indent = 2 if args.pretty else None

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            for p in policies:
                f.write(json.dumps(p, ensure_ascii=False, cls=NumpyEncoder) + "\n")
        size_kb = out_path.stat().st_size / 1024
        print(f"  Saved: {out_path} ({size_kb:.1f} KB)", file=sys.stderr)
    else:
        for p in policies:
            print(json.dumps(p, indent=indent, ensure_ascii=False, cls=NumpyEncoder))


if __name__ == "__main__":
    main()
