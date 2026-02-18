<div align="center">

# Synthetic UK Motor Insurance Data Generator

**Realistic synthetic quote request data for actuarial pricing, system testing, and research**

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/licence-MIT-green.svg)](LICENSE)

</div>

---

## Overview

A synthetic data generator that produces realistic UK private motor insurance quote requests — the kind received by insurers via price comparison websites (Compare the Market, MoneySupermarket, Confused.com, GoCompare) and direct channels.

Each record is a fully structured JSON containing **policyholder demographics, vehicle details, claims and convictions history, coverage selections, named drivers, add-ons, and address information** — calibrated to official UK public statistics wherever possible.

**No real personal data is used or produced.** All records are entirely fictitious.

### Key Features

- **~1,150 quotes/second** single-threaded generation
- **281-field JSON schema** modelling a real aggregator quote request
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
uv run python generate.py --n 10000 --seed 42 --output data/output/quotes.jsonl

# Generate as JSON array
uv run python generate.py --n 1000 --format json --output data/output/quotes.json

# Preview a single quote
uv run python generate.py --n 1 --pretty
```

---

## Project Structure

```
├── acquire_data.py          # Downloads public datasets
├── process_data.py          # Processes raw data into distribution tables
├── generate.py              # CLI entry point for generation
├── generator/
│   ├── core.py              # Main generation logic
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
    └── output/                  #   Generated quotes
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

Records follow a **dependency-respecting generation order** that preserves realistic correlations:

```
Postcode → Proposer demographics → Vehicle → Policy details
    → Claims & convictions → Additional drivers → Add-ons
        → Quote metadata → Address
```

| Component | Approach |
|-----------|----------|
| **Proposer age/gender** | Jointly sampled from DVLA full licence holder distribution |
| **Marital status** | Conditional on age × sex from ONS estimates |
| **Occupation** | SOC 2020 4-digit unit groups, frequency-weighted by sex |
| **Vehicle** | Sampled from VEH0120 fleet distribution; age conditioned on proposer |
| **Value & insurance group** | Depreciation model by manufacturer tier; group estimated from value + engine |
| **Annual mileage** | MOT empirical curves, adjusted for commuting, business use, urban/rural |
| **Claim frequency** | Poisson process with age-dependent rates from freMTPL2 |
| **Claim severity** | Log-normal (μ=6.85, σ=1.133), type-specific adjustments |
| **Convictions** | Age- and gender-dependent rates; MoJ offence distribution → DVLA codes |
| **Named drivers** | Demographics conditioned on relationship (spouse, child, parent) |
| **Add-ons** | Independent Bernoulli trials with covariate adjustments |

### Validation (1,000 records, seed 123)

| Metric | Generated | Reference |
|--------|-----------|-----------|
| Male % | 53.8% | 53.5% (DVLA) |
| Comprehensive cover | 84.4% | ~85% (ABI) |
| Petrol % | 55.1% | 55% (VEH0120) |
| Top make: Ford | 10.4% | 11.0% (VEH0120) |
| Renewal rate | 59.6% | ~60% (ABI) |

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

See [`docs/methodology.md`](docs/methodology.md) §8 for the complete list.

---

## Licence

This project is provided for research, development, and testing purposes. All source data is published under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) or equivalent open licence unless otherwise stated.
