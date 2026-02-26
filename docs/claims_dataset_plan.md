# Synthetic UK Motor Insurance Claims Dataset — Design Plan

This document outlines the approach for generating a synthetic claims dataset that sits alongside the existing quote dataset. The goal is to produce a realistic portfolio of **written policies with attached claims experience** that can be used for actuarial pricing model development, reserving exercises, and claims analytics.

---

## 1. Conceptual Overview

The claims dataset pipeline has three stages:

```
Quote Dataset  →  Written Policies  →  Claims Experience
(existing)        (quote-to-bind)      (frequency × severity)
```

1. **Quote-to-bind conversion**: A subset of generated quotes are selected as "bound" policies, with realistic conversion rates varying by risk profile
2. **Exposure calculation**: Each policy has an earned exposure period
3. **Claims generation**: Claims are attached to policies using frequency models conditioned on risk factors, with severity drawn from calibrated distributions

---

## 2. Output Schema

### 2.1 Policy Record (one row per policy)

| Field | Type | Description |
|---|---|---|
| `policy_id` | string | Unique policy identifier (POL-YYYY-NNNNNNNNN) |
| `quote_id` | string | Link back to originating quote |
| `inception_date` | date | Policy start date (= quote cover_start_date) |
| `expiry_date` | date | Policy end date (inception + 365 days) |
| `earned_exposure` | float | Earned proportion (0.0–1.0), typically 1.0 for full year |
| `mid_term_cancellation` | boolean | Whether policy was cancelled early |
| `cancellation_date` | date | If cancelled, the date |
| `cancellation_reason` | string | sold_vehicle / found_cheaper / other |
| `annual_premium` | float | Written premium (generated, not from quote) |
| `ipt_amount` | float | Insurance Premium Tax (12% of premium) |
| `commission_rate` | float | Aggregator/broker commission % |
| `renewal_invited` | boolean | Whether renewal was offered at expiry |
| `renewed` | boolean | Whether policyholder renewed |
| _All risk factors_ | various | Copied from quote: proposer age, gender, vehicle, postcode, NCD, cover type, mileage, etc. |

### 2.2 Claim Record (one row per claim, linked to policy)

| Field | Type | Description |
|---|---|---|
| `claim_id` | string | Unique claim identifier (CLM-YYYY-NNNNNNNNN) |
| `policy_id` | string | Link to policy |
| `notification_date` | date | Date claim was reported |
| `incident_date` | date | Date of loss event |
| `reporting_delay_days` | int | notification_date − incident_date |
| `peril` | string | accidental_damage / third_party_property / third_party_bi / theft / windscreen / fire / storm_flood / vandalism |
| `fault_status` | string | at_fault / not_at_fault / split_liability / pending |
| `claim_status` | string | open / settled / repudiated / withdrawn |
| `settlement_date` | date | If settled, the date |
| `development_months` | int | Months from incident to settlement |
| `incurred_total` | float | Total incurred (paid + reserve) |
| `paid_amount` | float | Amount paid to date |
| `reserve_amount` | float | Outstanding reserve |
| `excess_applied` | float | Policyholder excess deducted |
| `recovery_amount` | float | Third-party / subrogation recovery |
| `net_incurred` | float | incurred_total − recovery_amount |
| `has_personal_injury` | boolean | Whether PI element exists |
| `pi_incurred` | float | PI component of incurred |
| `pi_tariff_band` | string | If OIC-type claim: minor / moderate / serious |
| `claimant_type` | string | policyholder / third_party / passenger |
| `ncd_impact` | string | reduced / protected / not_affected |
| `fraud_indicator` | boolean | Synthetic fraud flag (~5–7%) |
| `total_loss` | boolean | Vehicle written off |

### 2.3 Development Triangle View (derived)

A triangulated view of claims by:
- **Origin period** (accident quarter)
- **Development period** (months since accident quarter)
- **Paid / incurred / count**

This is derived from the claim records, not generated separately.

---

## 3. Data Sources & Calibration

### 3.1 Existing Sources (already acquired)

| Source | What it calibrates |
|---|---|
| **freMTPL2freq** | Claim frequency by age, vehicle age, bonus-malus, region. Base rate ~10% |
| **freMTPL2sev** | Claim severity distribution. Log-normal: μ=6.85, σ=1.133 |
| **STATS19 accident rates** | Cross-check frequency by driver age band |
| **MoJ convictions** | Conviction frequency (input to pricing, not directly claims) |
| **MOT mileage** | Annual mileage by vehicle age (exposure-mileage adjustment) |

### 3.2 New Sources (added to acquire_data.py)

| Source | What it calibrates | Key fields |
|---|---|---|
| **STATS19 Collisions (2024)** | Accident severity, road type, weather, speed limit, junction, time-of-day, urban/rural | Severity (fatal/serious/slight), road_type, weather, light_conditions, speed_limit |
| **STATS19 Vehicles (2024)** | Vehicle involvement by type, age, manoeuvre; driver age × sex | vehicle_type, vehicle_age, driver_age, driver_sex, vehicle_manoeuvre, skidding, first_point_of_impact |
| **STATS19 Casualties (2024)** | Casualty severity, pedestrian/cyclist/occupant, age × sex | casualty_severity, casualty_class, casualty_age, casualty_sex |
| **FCA GI Value Measures** | Insurer-level claims frequency, average claim cost, loss ratio, complaints ratio | Claims frequency per policy, average claim cost, loss ratio by insurer and product |
| **OIC Monthly Data** | PI claim volumes, representation types, tariff bands, settlement patterns, exceptional injuries | Monthly claim counts, settled vs open, injury types, tariff amounts |
| **Police Recorded Crime** | Vehicle theft rates by police force area (maps to regions) | Offence counts for theft of/from vehicles by area |

### 3.3 Assumption-Based Parameters

| Parameter | Assumption | Basis |
|---|---|---|
| Quote-to-bind rate | 5–8% overall; varies by channel and price rank | Industry estimates; FCA data |
| Mid-term cancellation rate | ~8% of policies | ABI / industry |
| Renewal rate | ~70% of non-cancelled policies | ABI switching data |
| Premium generation | GLM-style: base rate × age factor × vehicle factor × area factor × NCD discount | Standard actuarial pricing structure |
| Claim reporting delay | 70% day 0–7, 15% day 8–30, 10% day 31–90, 5% day 91+ | Industry benchmarks |
| Settlement duration | Varies by peril: windscreen ~14 days, AD ~60 days, theft ~45 days, PI ~180–540 days | Industry benchmarks; OIC data |
| Recovery rate | ~35% of not-at-fault AD claims; ~0% for at-fault | Industry practice |
| Fraud rate | ~5–7% of claims flagged | IFB estimates |
| Total loss threshold | Vehicle value < repair cost × 1.1; ~5% of AD claims | Industry practice |

---

## 4. Generation Methodology

### 4.1 Quote-to-Bind Conversion

Not all quotes convert to policies. Conversion depends on:

| Factor | Effect on conversion |
|---|---|
| **Channel** | Direct: ~15%, Aggregator: ~5% (more shopping around) |
| **Proposer age** | Under-25: lower conversion (price-sensitive), 35–55: higher |
| **Cover type** | Comprehensive: slightly higher conversion |
| **Is renewal** | Renewal: ~70% conversion (inertia), New business: ~5% |
| **NCD years** | Higher NCD → more likely to buy (invested in discount) |

Base conversion model:
```
P(bind) = logistic(β₀ + β_channel + β_age + β_renewal + β_ncd + noise)
```

### 4.2 Premium Generation

Since the quote dataset doesn't include premiums (it models inbound requests), we generate premiums for bound policies:

```
Premium = base_rate × age_factor × vehicle_group_factor × area_factor
          × NCD_discount × cover_factor × excess_credit × mileage_factor
          × commission_loading × IPT
```

Calibrated so that:
- Average comprehensive premium ≈ £600 (ABI 2024 average)
- Young driver surcharge: 17–21 ≈ 3–5× base
- NCD discount: 0yr = 0%, 1yr = 30%, 2yr = 40%, 3yr = 50%, 4yr = 55%, 5+yr = 60–65%
- Area loading: urban +20–40%, high-theft areas +10–25%

The FCA GI Value Measures data provides insurer-level loss ratios to validate overall calibration.

### 4.3 Claim Frequency Model

Claims are generated per policy using a Poisson process:

```
E[claims] = exposure × base_rate(age) × vehicle_factor × area_factor × mileage_factor
```

Where:
- **base_rate(age)** from freMTPL2 age curve (adjusted for UK)
- **vehicle_factor** from insurance group / vehicle age
- **area_factor** from STATS19 regional accident rates + police theft data
- **mileage_factor** proportional to annual mileage / 8000

#### Frequency by peril

Total frequency is decomposed into perils using ABI-calibrated splits:

| Peril | % of claims (count) | % of claims (cost) |
|---|---|---|
| Accidental damage (own) | 40% | 30% |
| Third-party property damage | 25% | 20% |
| Third-party bodily injury | 5% | 25% |
| Windscreen | 15% | 3% |
| Theft | 8% | 12% |
| Storm / flood | 3% | 5% |
| Fire | 1% | 2% |
| Vandalism | 3% | 3% |

### 4.4 Claim Severity Model

Each claim's incurred amount is drawn from a peril-specific severity distribution:

| Peril | Distribution | Parameters (approx) | Mean | Median |
|---|---|---|---|---|
| Accidental damage | Log-normal | μ=7.5, σ=0.8 | £2,500 | £1,800 |
| TP property damage | Log-normal | μ=7.3, σ=0.9 | £2,200 | £1,500 |
| TP bodily injury (minor) | OIC tariff + log-normal | Tariff £240–£4,215 + special damages | £3,000 | £1,500 |
| TP bodily injury (serious) | Log-normal | μ=9.5, σ=1.5 | £30,000 | £13,000 |
| Windscreen | Gamma | shape=4, scale=100 | £400 | £350 |
| Theft | Log-normal | μ=8.5, σ=1.0 | £7,000 | £4,900 |
| Storm / flood | Log-normal | μ=7.8, σ=1.0 | £3,500 | £2,400 |
| Fire | Log-normal | μ=8.8, σ=0.8 | £8,500 | £6,600 |
| Vandalism | Log-normal | μ=6.5, σ=0.7 | £900 | £650 |

The freMTPL2sev distribution is used as the primary calibration for overall severity, with peril-specific adjustments.

**Large losses** (>£100K) are modelled separately: ~0.1% of claims, driven by serious PI and total-loss events. These dominate the severity tail and are critical for reserving.

### 4.5 Claim Development

Claims do not settle instantly. The development pattern determines how incurred amounts evolve:

#### Reporting delay (incident → notification)

| Delay band | Probability |
|---|---|
| 0–1 days | 55% |
| 2–7 days | 15% |
| 8–30 days | 15% |
| 31–90 days | 10% |
| 91–365 days | 4% |
| 365+ days | 1% |

Windscreen and own-damage claims report faster; PI claims report slower.

#### Settlement duration (notification → settlement)

| Peril | Median settlement (months) | 90th percentile |
|---|---|---|
| Windscreen | 0.5 | 1 |
| Accidental damage | 2 | 6 |
| TP property | 3 | 9 |
| Theft | 1.5 | 4 |
| TP bodily injury (minor) | 6 | 18 |
| TP bodily injury (serious) | 18 | 48 |

#### Development factors

For triangulation, cumulative development factors (link ratios) are applied:

| Dev month | Paid % of ultimate |
|---|---|
| 0–3 | 30% |
| 3–6 | 55% |
| 6–12 | 75% |
| 12–24 | 90% |
| 24–36 | 96% |
| 36–48 | 99% |
| 48+ | 100% |

PI claims develop much slower (50% paid at 12 months, 80% at 36 months).

### 4.6 Fault and Recovery

| Peril | At-fault % | Not-at-fault % | Split % |
|---|---|---|---|
| Accidental damage | 55% | 35% | 10% |
| TP property | 50% | 40% | 10% |
| TP bodily injury | 45% | 45% | 10% |
| Theft | 0% | 100% | 0% |
| Windscreen | N/A (own damage) | N/A | N/A |
| Fire | 0% | 100% | 0% |
| Vandalism | 0% | 100% | 0% |

**Recoveries**: Not-at-fault claims recover ~60–80% of outlay from the third party's insurer. Average recovery ~35% of gross incurred for not-at-fault AD claims.

### 4.7 NCD Impact

| Scenario | NCD change |
|---|---|
| At-fault claim, NCD not protected | Reduce NCD by 2 years (min 0) |
| At-fault claim, NCD protected | No change (step-back protection) |
| Not-at-fault claim | No change |
| Windscreen only | No change |
| 2+ at-fault claims, protected | Reduce by 1 year (protection typically covers 1 claim) |

### 4.8 Fraud Indicators

~5–7% of claims receive a synthetic fraud indicator, with higher rates for:
- PI claims on minor collisions (+3×)
- Cash settlements (+2×)
- Claims in first 90 days of policy (+1.5×)
- Urban areas (+1.3×)
- Male under-30 proposers (+1.2×)

---

## 5. STATS19 Processing Plan

### 5.1 Collisions Table

Extract from `dft-road-casualty-statistics-collision-2024.csv`:

| Output | Method |
|---|---|
| Accident severity distribution | Count by accident_severity (fatal/serious/slight) |
| Accidents by road type | Count by road_type (motorway, A, B, minor, etc.) |
| Accidents by speed limit | Distribution across 20/30/40/50/60/70 limits |
| Accidents by weather | Count by weather_conditions |
| Accidents by light conditions | Daylight vs dark |
| Accidents by urban/rural | Count by urban_or_rural_area |
| Time-of-day pattern | Hourly distribution |
| Day-of-week pattern | Weekday vs weekend |
| Monthly/seasonal pattern | Claims seasonality curve |

### 5.2 Vehicles Table

Extract from `dft-road-casualty-statistics-vehicle-2024.csv`:

| Output | Method |
|---|---|
| Driver age × accident involvement | Accident rate by age_band_of_driver |
| Driver sex × accidents | Male/female ratio |
| Vehicle age × accidents | Rate by age_of_vehicle |
| Vehicle manoeuvre at accident | Distribution of vehicle_manoeuvre |
| Point of impact | Distribution of first_point_of_impact |
| Skidding/overturning rate | By road surface and weather |
| Vehicle type × accidents | Car/motorcycle/van/HGV split |

### 5.3 Casualties Table

Extract from `dft-road-casualty-statistics-casualty-2024.csv`:

| Output | Method |
|---|---|
| Casualty severity by class | fatal/serious/slight for driver/passenger/pedestrian |
| PI severity distribution | Maps to OIC tariff bands |
| Casualty age distribution | Informs PI claim demographics |
| Casualties per accident | Average number (drives PI cost multiplier) |

---

## 6. FCA Value Measures Processing Plan

Extract from `gi-value-measures-data-2024.xlsx`:

| Output | Method |
|---|---|
| Claims frequency by product | Private car comprehensive, TPFT, TPO |
| Average claim cost by product | Validates our severity distributions |
| Loss ratio by insurer | Calibrates premium vs claims relationship |
| Claims acceptance rate | % of notified claims that are paid |

---

## 7. OIC Data Processing Plan

Extract from monthly Excel files:

| Output | Method |
|---|---|
| Monthly PI claim volumes | Trend and seasonality |
| Injury type distribution | Whiplash / soft tissue / other |
| Tariff band distribution | Minor / moderate (maps to severity) |
| Representation type | Litigant in person vs solicitor (affects costs) |
| Settlement rate and duration | Time to settle by type |
| Exceptional injury rate | % exceeding tariff (higher value) |

---

## 8. Recommended Implementation Order

```
Phase 1: Process new data sources
  ├── 1a. STATS19 full processor (collisions + vehicles + casualties)
  ├── 1b. FCA Value Measures processor
  ├── 1c. OIC data processor
  └── 1d. Police crime vehicle theft processor

Phase 2: Policy generator
  ├── 2a. Quote-to-bind conversion model
  ├── 2b. Premium model (GLM-style multiplicative)
  ├── 2c. Mid-term cancellation model
  └── 2d. Renewal model

Phase 3: Claims generator
  ├── 3a. Frequency model (Poisson, age × vehicle × area × mileage)
  ├── 3b. Peril allocation
  ├── 3c. Severity model (log-normal per peril)
  ├── 3d. Fault and recovery model
  ├── 3e. PI sub-model (OIC tariff calibrated)
  ├── 3f. Development pattern generator
  ├── 3g. Fraud indicator model
  └── 3h. NCD impact calculator

Phase 4: Outputs
  ├── 4a. Policy flat file (Parquet + CSV)
  ├── 4b. Claims flat file (Parquet + CSV)
  ├── 4c. Development triangle builder
  └── 4d. Summary statistics and validation report
```

---

## 9. Validation Targets

| Metric | Target | Source |
|---|---|---|
| Overall claims frequency | ~25% of policies have 1+ claim/year | ABI 2024 (including windscreen) |
| Average claim cost (all perils) | ~£3,500 | ABI 2024 (£11.7bn / ~3.3m claims) |
| Windscreen % of claims | ~15% by count | Industry |
| Theft % of claims | ~8% by count | Industry |
| PI % of claims | ~5% by count, ~25% by cost | ABI / OIC |
| Loss ratio | ~75–85% (recent years elevated) | FCA Value Measures |
| Average premium | ~£600 | ABI Premium Tracker 2024 |
| Fraud indicator rate | ~5–7% | IFB |
| Large loss (>£100K) rate | ~0.1% of claims | Industry |

---

## 10. Dataset Sizes

| Scenario | Quotes | Policies | Claims | Approximate disk |
|---|---|---|---|---|
| Small (dev/testing) | 10,000 | 700 | 200 | ~5 MB |
| Medium (model build) | 100,000 | 7,000 | 2,000 | ~50 MB |
| Large (full exercise) | 1,000,000 | 70,000 | 20,000 | ~500 MB |

The 7% conversion rate with ~25% claim frequency gives roughly 1 claim per 50 quotes.
