<div align="center">

# Synthetic UK Motor Insurance Data Generator

**Realistic synthetic quote request data for actuarial pricing, system testing, and research**

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/licence-MIT-green.svg)](LICENSE)

</div>

---

## Overview

A synthetic data generator that produces realistic UK private motor insurance quote requests — the kind received by insurers via price comparison websites (Compare the Market, MoneySupermarket, Confused.com, GoCompare).

The pipeline generates the full lifecycle: **quote requests → competitor premium panels → position-based policy conversion → claims**. Each quote is a fully structured JSON containing **policyholder demographics, vehicle details, claims and convictions history, coverage selections, named drivers, add-ons, and address information** — calibrated to official UK public statistics wherever possible.

**No real personal data is used or produced.** All records are entirely fictitious.

### Key Features

- **~1,150 quotes/second** single-threaded generation
- **281-field JSON schema** modelling a real aggregator quote request
- **10-insurer competitor premium panel** with distinct rating algorithms
- **Position-based conversion model** — conversion probability driven by price rank, gap to cheapest, brand noise, and demographics
- **Claims generation** with peril-specific frequency, severity, and fault attribution
- **11 public datasets** used to calibrate distributions
- **Conditional correlations** preserved (age → marital status, vehicle value → cover type, etc.)
- **Fully reproducible** via configurable random seed
- **Documented methodology** suitable for actuarial review

---

## Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager

### 1. Install dependencies

```bash
uv sync
```

### 2. Acquire public data

```bash
uv run python acquire_data.py
```

This downloads ~10 public datasets from GOV.UK, ONS, and OpenML. The ONS Postcode Directory (ONSPD) must be downloaded manually — see the instructions printed by the script.

### 3. Process raw data into distributions

```bash
uv run python process_data.py
```

Produces Parquet lookup tables in `data/processed/`.

### 4. Generate synthetic quotes

```bash
# Generate 10,000 quotes as JSONL
uv run python generate.py --n 10000 --seed 42 --output data/output/quotes/quotes_10k.jsonl

# Generate as JSON array
uv run python generate.py --n 1000 --format json --output data/output/quotes/quotes.json

# Preview a single quote
uv run python generate.py --n 1 --pretty
```

### 5. Generate competitor premium panels

Simulates pricing from 10 insurers, each with a distinct target market and rating algorithm. Every quote receives up to 10 premiums (NaN for declined quotes), producing a realistic aggregator comparison panel.

```bash
uv run python generate_premiums.py \
  --input data/output/quotes/quotes_10k.jsonl \
  --seed 42 \
  --output data/output/competitor_premiums/premiums_10k.parquet
```

### 6. Generate policies and claims

Converts quotes to bound policies using a position-based conversion model, then generates claims with peril-specific frequency and severity.

```bash
uv run python generate_claims.py \
  --input data/output/quotes/quotes_10k.jsonl \
  --premiums data/output/competitor_premiums/premiums_10k.parquet \
  --seed 42 \
  --output data/output/claims/britsure
```

Produces `britsure_policies.parquet` and `britsure_claims.parquet`.

---

## Project Structure

```
├── acquire_data.py          # Downloads public datasets
├── process_data.py          # Processes raw data into distribution tables
├── generate.py              # CLI entry point for quote generation
├── generate_premiums.py     # Generates competitor premium panels
├── generate_claims.py       # Converts quotes → policies → claims
├── generator/
│   ├── core.py              # Main quote generation logic
│   ├── data_loader.py       # Pre-indexed data loading (NumPy/dict)
│   └── constants.py         # Lookup tables and assumptions
├── processors/              # One processor per data source
│   ├── postcodes.py         #   ONSPD → postcode lookup
│   ├── driver_demographics.py #   DVLA → age × gender
│   ├── marital_status.py    #   ONS → age × sex × status
│   ├── occupation.py        #   Nomis APS → SOC2020
│   ├── names.py             #   ONS baby names
│   ├── vehicles.py          #   DfT VEH0120 → make/model/fuel
│   ├── claims.py            #   freMTPL2 → frequency & severity
│   ├── convictions.py       #   MoJ → motoring offences
│   ├── accidents.py         #   STATS19 → accident rates
│   ├── deprivation.py       #   IoD2019 → IMD by LSOA
│   └── mot_mileage.py       #   MOT data → mileage curves
├── schemas/
│   ├── motor_quote.schema.json  # Formal JSON schema
│   └── example_quote.json       # Example output
├── docs/
│   ├── methodology.md           # Full methodology (markdown)
│   ├── methodology_report.html  # Formatted report (printable)
│   └── synthetic_data_plan.md   # Field-level distribution plan
└── data/                        # (gitignored)
    ├── raw/                     #   Downloaded source files
    ├── processed/               #   Parquet distribution tables
    └── output/
        ├── quotes/              #   Generated quote JSONL files
        ├── competitor_premiums/ #   Premium panels (Parquet)
        └── claims/              #   Policies & claims (Parquet)
```

---

## Data Sources

All distributions are calibrated to publicly available official statistics. The table below summarises the 11 primary datasets used.

| # | Dataset | Publisher | Fields Informed | Key Statistics |
|---|---------|-----------|-----------------|----------------|
| 1 | **ONS Postcode Directory** (ONSPD) | ONS | Postcode, region, urban/rural, IMD | 1.79M live UK postcodes |
| 2 | **Driving Licence Statistics** (DRL0101) | DfT / DVLA | Proposer age, gender | 42.8M licence holders; 53.5% male |
| 3 | **Marital Status Estimates** | ONS | Marital status by age × sex | Married 48.8%, single 36.8% |
| 4 | **Annual Population Survey** | ONS / Nomis | Occupation (SOC 2020) | 8,638 unit groups by sex |
| 5 | **Baby Names Statistics** | ONS | First names by gender | Top 100 boys + 100 girls |
| 6 | **Vehicle Licensing Statistics** (VEH0120) | DfT | Make, model, fuel type | 54K combos; 34.5M cars |
| 7 | **freMTPL2** (frequency + severity) | OpenML | Claim rates, severity | 678K policies; log-normal severity |
| 8 | **Motoring Convictions** | MoJ | Conviction codes, demographics | Speeding 29.6%, males 3.5× more likely |
| 9 | **STATS19 Road Casualties** | DfT | Accident rates by age | Cross-check for claim frequency |
| 10 | **Index of Multiple Deprivation** | MHCLG | Deprivation by LSOA | 32,844 English LSOAs |
| 11 | **Anonymised MOT Data** | DVSA | Mileage by vehicle age | ~14M tests; median 7,500 mi/yr |

All government datasets are published under the **Open Government Licence v3.0**.

### Assumption-Based Fields

Where no public data exists, documented assumptions are used for: add-on selection rates, voluntary excess choice, NCD protection take-up, vehicle security/modifications, overnight location, and quote metadata (channel shares, time-of-day). Full details are in [`docs/methodology.md`](docs/methodology.md).

---

## Methodology Summary

The pipeline follows a dependency-respecting order across four stages:

```
1. Quotes:    Postcode → Demographics → Vehicle → Policy → Claims history → Add-ons
2. Premiums:  Quote risk factors → 10 insurer rating algorithms → premium panel
3. Policies:  Premium panel + quote factors → position-based conversion → bound policies
4. Claims:    Bound policy + exposure → Poisson frequency → log-normal severity by peril
```

### Quote Generation

| Component | Approach |
|-----------|----------|
| **Proposer age/gender** | Jointly sampled from DVLA full licence holder distribution |
| **Marital status** | Conditional on age × sex from ONS estimates |
| **Occupation** | SOC 2020 4-digit unit groups, frequency-weighted by sex |
| **Vehicle** | Sampled from VEH0120 fleet distribution; age conditioned on proposer |
| **Value & insurance group** | Depreciation model by manufacturer tier; group estimated from value + engine |
| **Annual mileage** | MOT empirical curves, adjusted for commuting, business use, urban/rural |
| **Convictions** | Age- and gender-dependent rates; MoJ offence distribution → DVLA codes |
| **Named drivers** | Demographics conditioned on relationship (spouse, child, parent) |
| **Add-ons** | Independent Bernoulli trials with covariate adjustments |

All quotes are new business (aggregator channel) — renewals are not modelled in this pipeline.

### Competitor Premium Panel

Ten fictional insurers, each with a distinct rating algorithm and target market:

| Insurer | Character | Base Rate |
|---------|-----------|-----------|
| **BritSure Direct** | Balanced mid-market, slight urban loading | £1,080 |
| **FirstMile** | Young driver specialist, penalises seniors | £1,350 |
| **Evergreen Insurance** | Over-25s only, loyalty-focused, EV-friendly | £1,200 |
| **QuoteFast** | Aggressive new business, penalises renewals | £1,020 |
| **Prestige Motor** | High-group vehicles, EV discount | £1,150 |
| **CoverCheap** | Low base, high urban/vehicle loading, declines provisionals | £950 |
| **CountryWide** | Rural specialist, heavy urban loading | £1,100 |
| **Haven Insurance** | Female discount, balanced approach | £1,100 |
| **SmartDrive** | Young driver lean, tech-focused | £1,080 |
| **National Mutual** | Loyalty book, expensive for new business | £1,200 |

Each premium is calculated from shared rating curves (age, NCD, vehicle group, mileage, area, excess) modified by insurer-specific loadings, plus log-normal noise (σ = 0.04–0.08) to simulate individual underwriter variation.

### Position-Based Conversion Model

Quotes convert to BritSure policies based on competitive position — not a flat bind rate. The model combines nine factors:

| # | Factor | Effect |
|---|--------|--------|
| 1 | **Price rank** | Rank 1: 35% base, rank 2: 18%, rank 3: 10%, steep falloff |
| 2 | **Ratio to cheapest** | Exponential decay (e^−5x) as price exceeds cheapest |
| 3 | **Gap advantage** | Being much cheaper than 2nd place boosts conversion (up to +50%) |
| 4 | **Competitor count** | Fewer rivals quoting → higher win rate (+20% if ≤6 competitors) |
| 5 | **Premium level** | Budget buyers (<£400) more price-sensitive; high-value (>£1,500) more brand-driven |
| 6 | **Driver age** | Under-25s price-driven (rank >2 penalised); over-60s favour brand |
| 7 | **Provisional licence** | 50% conversion penalty |
| 8 | **Brand noise** | Per-quote log-normal random affinity (σ=0.15) — some customers just prefer BritSure |
| 9 | **Market purchase rate** | 65% of people who quote actually buy from anyone |

This produces an overall conversion rate of ~4%, with ~65% of policies won from rank 1 and a realistic long tail through ranks 2–4. Inception premium is BritSure's own quoted price (not the market cheapest).

### Claims Generation

| Component | Approach |
|-----------|----------|
| **Claim frequency** | Poisson process; age-dependent base rate × vehicle group × mileage × vehicle age × gender |
| **Peril mix** | Accidental damage 40%, TPP 25%, windscreen 15%, theft 8%, TPBI 5%, vandalism 3%, storm/flood 3%, fire 1% |
| **Severity** | Log-normal per peril (e.g. accidental damage μ=7.5/σ=0.8 ≈ £2,500 mean) |
| **Fault** | Peril-specific: AD 55% at-fault; windscreen/theft/weather always not-at-fault |
| **Cancellation** | 8% mid-term cancellation rate, reducing exposure proportionally |

### Validation (1,000 records, seed 123)

| Metric | Generated | Reference |
|--------|-----------|-----------|
| Male % | 53.8% | 53.5% (DVLA) |
| Comprehensive cover | 84.4% | ~85% (ABI) |
| Petrol % | 55.1% | 55% (VEH0120) |
| Top make: Ford | 10.4% | 11.0% (VEH0120) |

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/methodology.md`](docs/methodology.md) | Full methodology with field-level detail |
| [`docs/methodology_report.html`](docs/methodology_report.html) | Formatted report for sharing (open in browser, print to PDF) |
| [`docs/synthetic_data_plan.md`](docs/synthetic_data_plan.md) | Original distribution planning document |
| [`schemas/motor_quote.schema.json`](schemas/motor_quote.schema.json) | Formal JSON schema (281 fields) |
| [`schemas/example_quote.json`](schemas/example_quote.json) | Example generated record |

---

## Known Limitations

1. **First names not cohort-specific** — 2023 top-100 names used for all birth years
2. **French claims data as UK proxy** — freMTPL2 provides curve shape; UK levels adjusted
3. **Insurance groups approximate** — Thatcham data not publicly available at scale
4. **Add-on rates assumption-based** — no public dataset exists
5. **No spatial vehicle correlation** — vehicle choice independent of postcode
6. **Single-year snapshot** — trends (EV growth, market shifts) not dynamically modelled
7. **Aggregator-only quotes** — all quotes are new business; renewal/direct channel not modelled
8. **Competitor rating algorithms simplified** — real insurers use hundreds of rating factors; these use ~12 each
9. **Conversion model does not include channel/time effects** — time-of-day, day-of-week, and channel-specific behaviour not modelled

See [`docs/methodology.md`](docs/methodology.md) §8 for the complete list.

---

## Licence

This project is provided for research, development, and testing purposes. All source data is published under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) or equivalent open licence unless otherwise stated.
