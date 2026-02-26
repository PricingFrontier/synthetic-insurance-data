"""
Generate competitor premium panels from existing quote JSONL files.

Simulates pricing from 10 different insurers, each with a distinct target
market and rating algorithm.  Every quote receives 10 premiums, producing
a realistic aggregator comparison panel.

Usage:
    uv run python generate_premiums.py --input data/output/quotes/quotes_1k.jsonl --seed 42 --output data/output/competitor_premiums/premiums_1k.jsonl
    uv run python generate_premiums.py --input data/output/quotes/quotes_10k.jsonl --seed 42 --pretty
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# Insurer profiles
# ─────────────────────────────────────────────────────────────────────────────
# Each insurer has:
#   - A base rate (average annual premium for a 40-yr-old, comp, 5 NCD, grp 20)
#   - Rating factor relativities that differ from the "market" to reflect
#     their target segment
#   - A decline function (returns True if the insurer won't quote)
#   - A noise_sigma controlling random variation (competitive noise)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class InsurerProfile:
    name: str
    base_rate: float                    # £ annual premium at reference risk
    noise_sigma: float = 0.06          # log-normal noise (competitive jitter)

    # ── Age relativities (multiplier vs age 40 baseline) ──
    age_factors: dict = field(default_factory=dict)

    # ── Segment loadings / discounts (multipliers) ──
    young_driver_loading: float = 1.0   # extra on top of age curve for <25
    senior_discount: float = 1.0        # for 65+
    female_discount: float = 1.0        # vs male
    ncd_max_discount: float = 0.60      # max NCD discount (at 9+ years)
    new_business_discount: float = 1.0  # for non-renewals
    loyalty_discount: float = 1.0       # for renewals

    # ── Vehicle ──
    vehicle_group_power: float = 1.0    # how steeply they rate on ins group
    ev_discount: float = 1.0            # for electric/hybrid
    old_vehicle_loading: float = 1.0    # for vehicles 10+ years

    # ── Cover / excess ──
    tpft_discount: float = 0.75        # TPFT vs comp multiplier
    tpo_discount: float = 0.60         # TPO vs comp multiplier
    excess_credit_rate: float = 1.0     # how much excess reduces premium

    # ── Area ──
    urban_loading: float = 1.0

    # ── Decline rules ──
    min_age: int = 17
    max_age: int = 99
    max_insurance_group: int = 50
    decline_provisional: bool = False


# ── The 10 insurers ──────────────────────────────────────────────────────────

INSURERS = [
    # 1. Large direct — broad book, competitive on mid-market
    InsurerProfile(
        name="BritSure Direct",
        base_rate=1080,
        noise_sigma=0.06,
        young_driver_loading=1.05,
        ncd_max_discount=0.62,
        loyalty_discount=0.95,
        vehicle_group_power=1.0,
        urban_loading=1.10,
    ),

    # 2. Young driver specialist — low base for <30, expensive for older
    InsurerProfile(
        name="FirstMile",
        base_rate=1350,
        noise_sigma=0.07,
        young_driver_loading=0.65,       # aggressive discount for young
        senior_discount=1.30,            # loads up older drivers
        female_discount=0.93,
        ncd_max_discount=0.52,           # less generous NCD (short histories)
        new_business_discount=0.90,
        vehicle_group_power=0.85,        # lenient on high groups
        max_insurance_group=45,
    ),

    # 3. Mature driver specialist — targets 45+
    InsurerProfile(
        name="Evergreen Insurance",
        base_rate=1200,
        noise_sigma=0.05,
        young_driver_loading=1.50,       # expensive for young
        senior_discount=0.82,            # deep discount for 65+
        ncd_max_discount=0.63,
        loyalty_discount=0.92,
        ev_discount=0.92,
        old_vehicle_loading=0.95,
        min_age=25,                      # declines under 25
    ),

    # 4. Aggregator volume player — tight pricing, thin margins
    InsurerProfile(
        name="QuoteFast",
        base_rate=1020,
        noise_sigma=0.04,               # very consistent pricing
        young_driver_loading=1.0,
        ncd_max_discount=0.60,
        new_business_discount=0.85,      # aggressive on new biz
        loyalty_discount=1.08,           # less competitive at renewal
        excess_credit_rate=1.2,          # rewards high excess
        urban_loading=1.08,
    ),

    # 5. Premium / high-value specialist — targets expensive cars
    InsurerProfile(
        name="Prestige Motor",
        base_rate=1150,
        noise_sigma=0.06,
        young_driver_loading=1.15,
        vehicle_group_power=0.65,        # very flat on high groups
        ev_discount=0.85,
        old_vehicle_loading=1.15,
        tpft_discount=0.80,
        urban_loading=1.05,              # less area-sensitive
        min_age=21,
    ),

    # 6. Budget insurer — cheapest base, loads heavily on risk
    InsurerProfile(
        name="CoverCheap",
        base_rate=950,
        noise_sigma=0.08,               # more volatile pricing
        young_driver_loading=1.20,
        ncd_max_discount=0.58,
        new_business_discount=0.88,
        vehicle_group_power=1.20,        # steep on high groups
        old_vehicle_loading=1.12,
        tpft_discount=0.70,
        tpo_discount=0.55,
        urban_loading=1.22,
        max_insurance_group=40,          # declines high groups
        decline_provisional=True,
    ),

    # 7. Rural / low-mileage specialist
    InsurerProfile(
        name="CountryWide",
        base_rate=1100,
        noise_sigma=0.05,
        senior_discount=0.88,
        ncd_max_discount=0.62,
        loyalty_discount=0.93,
        urban_loading=1.40,              # very expensive in cities
        ev_discount=0.90,
    ),

    # 8. Female-focused brand — competitive for women
    InsurerProfile(
        name="Haven Insurance",
        base_rate=1100,
        noise_sigma=0.05,
        female_discount=0.85,            # strong female discount
        young_driver_loading=1.0,
        ncd_max_discount=0.62,
        new_business_discount=0.94,
        ev_discount=0.90,
        urban_loading=1.10,
    ),

    # 9. Telematics-friendly — rewards low mileage + good postcode
    InsurerProfile(
        name="SmartDrive",
        base_rate=1080,
        noise_sigma=0.06,
        young_driver_loading=0.85,       # telematics offsets young risk
        ncd_max_discount=0.58,
        new_business_discount=0.88,
        vehicle_group_power=1.05,
        urban_loading=1.15,
        excess_credit_rate=1.15,
    ),

    # 10. Mutual / legacy insurer — loyal book, expensive for new biz
    InsurerProfile(
        name="National Mutual",
        base_rate=1200,
        noise_sigma=0.05,
        young_driver_loading=1.10,
        senior_discount=0.90,
        ncd_max_discount=0.65,
        new_business_discount=1.15,      # expensive for switchers
        loyalty_discount=0.88,           # strong renewal discount
        vehicle_group_power=0.95,
        urban_loading=1.12,
        decline_provisional=True,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Market-wide rating curves (shared baseline that insurers deviate from)
# ─────────────────────────────────────────────────────────────────────────────

def _age_factor(age: int) -> float:
    """Market-average age relativity curve. Reference age = 40 → 1.0."""
    if age < 17:
        return 5.0
    if age <= 19:
        return 3.8 - (age - 17) * 0.30        # 3.8 → 3.2
    if age <= 21:
        return 3.2 - (age - 19) * 0.35        # 3.2 → 2.5
    if age <= 25:
        return 2.5 - (age - 21) * 0.20        # 2.5 → 1.7
    if age <= 30:
        return 1.7 - (age - 25) * 0.10        # 1.7 → 1.2
    if age <= 40:
        return 1.2 - (age - 30) * 0.02        # 1.2 → 1.0
    if age <= 55:
        return 1.0                              # flat
    if age <= 65:
        return 1.0 + (age - 55) * 0.01        # 1.0 → 1.1
    if age <= 75:
        return 1.1 + (age - 65) * 0.03        # 1.1 → 1.4
    return 1.4 + (age - 75) * 0.04            # 1.4 → rising


def _ncd_factor(ncd_years: int, max_discount: float) -> float:
    """NCD discount factor. Returns multiplier 0.35–1.0."""
    # Standard step-back: 0→0%, 1→30%, 2→40%, 3→50%, 4→55%, 5+→60–65%
    steps = {0: 0.0, 1: 0.30, 2: 0.40, 3: 0.50, 4: 0.55}
    if ncd_years in steps:
        discount = steps[ncd_years]
    else:
        # 5+ years: scale toward max_discount
        discount = 0.55 + (min(ncd_years, 15) - 5) * ((max_discount - 0.55) / 10)
        discount = min(discount, max_discount)
    return 1.0 - discount


def _vehicle_group_factor(group: int, power: float) -> float:
    """Insurance group relativity. Group 20 = 1.0 reference."""
    # Exponential scaling centred on group 20
    raw = (group / 20.0) ** power
    return max(0.6, min(raw, 3.5))


def _mileage_factor(annual_mileage: int) -> float:
    """Mileage relativity. 8000 mi = 1.0 reference."""
    return max(0.75, min((annual_mileage / 8000) ** 0.5, 1.6))


def _excess_credit(voluntary_excess: int, rate: float) -> float:
    """Voluntary excess credit. Higher excess → lower premium."""
    # £0 → 1.0, £250 → ~0.95, £500 → ~0.90, £1000 → ~0.83
    if voluntary_excess <= 0:
        return 1.0
    credit = (voluntary_excess / 2500) * rate
    return max(0.80, 1.0 - credit)


def _vehicle_age_factor(veh_age: int, loading: float) -> float:
    """Vehicle age loading for old cars (repair parts, security)."""
    if veh_age >= 15:
        return 1.0 + (loading - 1.0) * 1.5
    if veh_age >= 10:
        return loading
    return 1.0


# Postcode area → broad area band (1=low risk, 5=high risk)
_AREA_BANDS = {
    # High risk urban
    "E": 5, "EC": 5, "N": 5, "NW": 5, "SE": 5, "SW": 4, "W": 5, "WC": 5,
    "BR": 4, "CR": 4, "DA": 4, "EN": 4, "HA": 4, "IG": 4, "KT": 3, "RM": 4,
    "SM": 3, "TW": 3, "UB": 4, "WD": 3,
    "M": 4, "B": 4, "L": 4, "LS": 3, "S": 3, "NG": 3, "LE": 3,
    "BD": 3, "BL": 3, "OL": 3, "WN": 3, "WA": 3, "WV": 3, "WS": 3,
    "CV": 3, "DE": 3, "ST": 3, "DY": 3,
    # Medium
    "BS": 3, "CF": 3, "NE": 3, "SR": 3, "DH": 3, "TS": 3, "HU": 3,
    "DN": 3, "LN": 2, "PE": 2, "NR": 2, "IP": 2, "CO": 2, "CB": 2,
    "CM": 3, "SS": 3, "ME": 3, "CT": 3, "TN": 2, "BN": 3, "RH": 2,
    "GU": 2, "PO": 3, "SO": 3, "BH": 3, "SP": 2, "BA": 2, "SN": 2,
    "GL": 2, "OX": 2, "RG": 2, "SL": 3, "HP": 2, "MK": 2, "LU": 3,
    "AL": 2, "SG": 2, "HG": 2, "YO": 2, "DL": 2, "CA": 2, "LA": 2,
    "PR": 3, "FY": 3, "BB": 3, "HX": 3, "HD": 3, "WF": 3, "HG": 2,
    # Low risk rural
    "EX": 2, "PL": 2, "TQ": 2, "TA": 2, "DT": 2, "TR": 1, "EH": 2,
    "G": 3, "PA": 2, "KA": 2, "ML": 3, "FK": 2, "DD": 2, "PH": 1,
    "AB": 2, "IV": 1, "KW": 1, "HS": 1, "ZE": 1,
    "SA": 2, "LD": 1, "SY": 2, "LL": 2, "CH": 3,
    "BT": 2, "TD": 1, "DG": 1, "HR": 2, "WR": 2, "NN": 2, "TF": 2,
    "CW": 2, "SK": 3, "HF": 2, "LI": 2,
}


def _area_factor(postcode: str, urban_loading: float) -> float:
    """Area rating factor from postcode area."""
    pc = postcode.strip().split()[0] if postcode else ""
    area = "".join(c for c in pc if c.isalpha())

    # Try full area code first, then first letter
    band = _AREA_BANDS.get(area, _AREA_BANDS.get(area[:1] if area else "", 3))

    # Band 1=0.85, 2=0.95, 3=1.0, 4=1.10, 5=1.25
    base = {1: 0.85, 2: 0.95, 3: 1.00, 4: 1.10, 5: 1.25}[band]

    # Scale by insurer's urban sensitivity
    if band >= 4:
        base = 1.0 + (base - 1.0) * urban_loading
    elif band <= 2:
        base = 1.0 - (1.0 - base) * (1.0 / max(urban_loading, 0.5))

    return base


# ─────────────────────────────────────────────────────────────────────────────
# Premium calculation
# ─────────────────────────────────────────────────────────────────────────────

def price_quote(
    quote: dict,
    insurer: InsurerProfile,
    rng: np.random.Generator,
) -> dict | None:
    """Calculate a premium for a single quote from one insurer.

    Returns a dict with the premium breakdown, or None if declined.
    """
    proposer = quote["proposer"]
    policy = quote["policy_details"]
    vehicle = quote["vehicle"]
    address = quote["address"]

    # ── Extract risk factors ──
    age = _calc_age(proposer.get("date_of_birth"))
    gender = proposer.get("gender", "male")
    ncd = policy.get("ncd_years", 0)
    cover = policy.get("cover_type", "comprehensive")
    vol_excess = policy.get("voluntary_excess", 250)
    annual_mileage = policy.get("annual_mileage", 8000)
    is_renewal = policy.get("is_renewal", False)
    ins_group = vehicle.get("insurance_group", 20)
    fuel = vehicle.get("fuel_type", "petrol")
    veh_age = date.today().year - vehicle.get("year_of_manufacture", 2020)
    postcode = address.get("postcode", "")
    licence_type = proposer.get("licence", {}).get("licence_type", "full_uk")
    claims_count = len(proposer.get("claims", []))
    convictions_count = len(proposer.get("convictions", []))

    # ── Decline checks ──
    if age < insurer.min_age or age > insurer.max_age:
        return None
    if ins_group > insurer.max_insurance_group:
        return None
    if insurer.decline_provisional and licence_type == "provisional_uk":
        return None

    # ── Build premium multiplicatively ──
    premium = insurer.base_rate

    # Age
    af = _age_factor(age)
    if age < 25:
        af *= insurer.young_driver_loading
    elif age >= 65:
        af *= insurer.senior_discount
    premium *= af

    # Gender
    if gender == "female":
        premium *= insurer.female_discount

    # NCD
    premium *= _ncd_factor(ncd, insurer.ncd_max_discount)

    # Vehicle group
    premium *= _vehicle_group_factor(ins_group, insurer.vehicle_group_power)

    # Vehicle age
    premium *= _vehicle_age_factor(veh_age, insurer.old_vehicle_loading)

    # EV / hybrid discount
    if fuel in ("electric", "plug_in_hybrid", "hybrid_petrol_electric", "hybrid_diesel_electric"):
        premium *= insurer.ev_discount

    # Mileage
    premium *= _mileage_factor(annual_mileage)

    # Cover type
    if cover == "third_party_fire_and_theft":
        premium *= insurer.tpft_discount
    elif cover == "third_party_only":
        premium *= insurer.tpo_discount

    # Voluntary excess credit
    premium *= _excess_credit(vol_excess, insurer.excess_credit_rate)

    # Area
    premium *= _area_factor(postcode, insurer.urban_loading)

    # New business vs renewal
    if is_renewal:
        premium *= insurer.loyalty_discount
    else:
        premium *= insurer.new_business_discount

    # Claims loading: +15% per at-fault claim in last 5 years
    at_fault = sum(
        1 for c in proposer.get("claims", [])
        if c.get("fault") == "at_fault"
    )
    premium *= 1.0 + at_fault * 0.15

    # Convictions loading: +10% per conviction
    premium *= 1.0 + convictions_count * 0.10

    # ── Random competitive noise (log-normal jitter) ──
    noise = float(rng.lognormal(0, insurer.noise_sigma))
    premium *= noise

    # ── Floor and round ──
    premium = max(200.0, premium)
    premium = round(premium, 2)

    # IPT (12%)
    ipt = round(premium * 0.12, 2)

    return {
        "insurer": insurer.name,
        "annual_premium": premium,
        "ipt": ipt,
        "total_payable": round(premium + ipt, 2),
        "declined": False,
    }


def _calc_age(dob_str: str | None) -> int:
    if not dob_str:
        return 40
    try:
        dob = date.fromisoformat(dob_str)
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except (ValueError, TypeError):
        return 40


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return super().default(obj)


def main():
    parser = argparse.ArgumentParser(
        description="Generate competitor premium panels from quote JSONL files"
    )
    parser.add_argument("--input", type=str, required=True, help="Path to quotes JSONL file")
    parser.add_argument("--output", type=str, default=None, help="Output JSONL file path")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    rng = np.random.default_rng(args.seed)

    # Read quotes
    print(f"Reading quotes from {input_path}...", file=sys.stderr)
    quotes = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                quotes.append(json.loads(line))
    print(f"  Read {len(quotes)} quotes", file=sys.stderr)

    # Generate premiums
    print(f"Generating premiums from {len(INSURERS)} insurers...", file=sys.stderr)
    t0 = time.time()

    results = []
    decline_counts = {ins.name: 0 for ins in INSURERS}

    for quote in quotes:
        quote_id = quote["quote_metadata"]["quote_id"]
        premiums = []

        for insurer in INSURERS:
            result = price_quote(quote, insurer, rng)
            if result is None:
                decline_counts[insurer.name] += 1
                premiums.append({
                    "insurer": insurer.name,
                    "annual_premium": None,
                    "ipt": None,
                    "total_payable": None,
                    "declined": True,
                })
            else:
                premiums.append(result)

        results.append({
            "quote_id": quote_id,
            "num_quotes": sum(1 for p in premiums if not p["declined"]),
            "cheapest": min(
                (p["total_payable"] for p in premiums if not p["declined"]),
                default=None,
            ),
            "premiums": premiums,
        })

    gen_time = time.time() - t0

    # Stats
    all_premiums = [
        p["total_payable"] for r in results for p in r["premiums"]
        if not p["declined"]
    ]
    avg_premium = sum(all_premiums) / len(all_premiums) if all_premiums else 0
    avg_cheapest = (
        sum(r["cheapest"] for r in results if r["cheapest"]) /
        sum(1 for r in results if r["cheapest"])
    ) if results else 0

    print(f"  Generated in {gen_time:.2f}s", file=sys.stderr)
    print(f"  Avg premium (all):      £{avg_premium:,.0f}", file=sys.stderr)
    print(f"  Avg cheapest per quote: £{avg_cheapest:,.0f}", file=sys.stderr)
    print(f"  Decline rates:", file=sys.stderr)
    for ins in INSURERS:
        rate = decline_counts[ins.name] / len(quotes) * 100
        if rate > 0:
            print(f"    {ins.name:25s} {rate:.1f}%", file=sys.stderr)

    # Output
    indent = 2 if args.pretty else None

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False, cls=NumpyEncoder) + "\n")
        size_kb = out_path.stat().st_size / 1024
        print(f"  Saved: {out_path} ({size_kb:.1f} KB)", file=sys.stderr)
    else:
        for r in results:
            print(json.dumps(r, indent=indent, ensure_ascii=False, cls=NumpyEncoder))


if __name__ == "__main__":
    main()
