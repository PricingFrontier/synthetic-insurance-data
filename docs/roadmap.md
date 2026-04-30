# Synthetic Insurance Data Roadmap
## Making the Data Showcase Every Feature of [Haute](https://github.com/PricingFrontier/haute)

*Compiled from analysis by 30 specialist agents examining every aspect of the current codebase, the haute pricing engine, and actuarial best practices.*

---

## Executive Summary

The current synthetic data generator is already **production-quality** for UK private motor insurance: 281-field JSON schema, 20-insurer competitive panel, position-based conversion, and Poisson/log-normal claims -- all calibrated to 11 UK public datasets. However, to become **the definitive companion dataset for haute**, it needs work across four dimensions:

1. **Close critical data gaps** that prevent haute features from being demonstrated
2. **Add new insurance lines** (home, commercial, pet, travel) for multi-product demos
3. **Improve realism** in demographics, vehicles, claims, and geography
4. **Ship tutorials and tooling** that get users from zero to pricing model in 5 minutes

The roadmap is organised into **5 phases** spanning roughly 6-9 months of development.

---

## Phase 1: Foundation (Critical -- Unblocks Everything Else)

These items are blocking. Without them, haute's core features cannot be demonstrated on the synthetic data.

### 1.1 Flat Modelling Table (`generate_modelling_data.py`)
**Priority: P0 | Effort: Medium | Unblocks: GLM, CatBoost, AvE, all diagnostics**

The single biggest gap. Currently, risk factors live only in nested JSONL (40GB for 10M records), while claims are in a separate Parquet with only `policy_id` and `inception_premium`. A researcher cannot fit a GLM without parsing JSON, flattening 281 fields, and joining across files.

Create a new script that produces `modelling_table.parquet` with one row per policy:
- All ~35 rating factors as flat columns (age, gender, vehicle_group, area_band, mileage, NCD, cover_type, etc.)
- `earned_exposure` (currently generated but discarded in `_gen_exposure()`)
- `claim_count`, `total_incurred`, `pure_premium` (aggregated from claims)
- `cv_fold` (1-5, stratified), `dataset_split` (train/validation/test)
- `inception_quarter` for temporal splits

### 1.2 Known DGP Parameters (`config/true_dgp.json`)
**Priority: P0 | Effort: Low | Unblocks: GLM coefficient validation, tutorial "aha moments"**

Externalise the true data-generating process parameters (frequency relativities, severity parameters, interaction multipliers) into a JSON config file. This is the "answer key" that lets users verify their fitted model recovers the known structure.

```json
{
  "frequency_relativities": {
    "age_band": {"17-20": 2.80, "21-24": 1.85, "25-29": 1.25, "30-39": 1.00, ...},
    "vehicle_group_band": {"1-10": 0.80, "11-20": 1.00, "21-30": 1.15, ...},
    "area_band": {"1": 0.75, "2": 0.90, "3": 1.00, "4": 1.15, "5": 1.35}
  },
  "severity_params": { ... },
  "interactions": { "age_x_vehicle_group": { ... } }
}
```

### 1.3 Embed Explicit Multiplicative DGP in Claims
**Priority: P0 | Effort: Medium | Unblocks: GLM training, AvE diagnostics, model comparison**

Replace the ad-hoc claims frequency model (freMTPL2 age lookup + flat factors) with an explicit multiplicative GLM structure using the parameters from `true_dgp.json`. This ensures:
- GLM coefficients are **recoverable** from the data
- Age, vehicle group, area, mileage, and NCD all have clean, documented effects
- Two interaction terms are embedded (age x vehicle_group, area x overnight_location)
- Over-dispersion is introduced via gamma-Poisson mixture (dispersion ~1.3-1.8 by segment)

### 1.4 Persist Exposure and Policy-Level Aggregates
**Priority: P0 | Effort: Low | Unblocks: Poisson offset, Tweedie, all frequency models**

Modify `generate_claims.py` to write `earned_exposure`, `claim_count`, `total_incurred` to the policies Parquet. Make cancellation rate age-dependent (young: 12%, mid: 8%, senior: 5%) rather than flat 8%.

### 1.5 Pricing Decomposition
**Priority: P0 | Effort: Medium | Unblocks: Execution tracing, regulatory compliance, submodel composition**

Modify `_price_glm()` in `generate_premiums.py` to return and persist the factor-by-factor trace alongside the final premium. Each BritSure quote should have: base_rate, age_factor, ncd_factor, vehicle_group_factor, area_factor, mileage_factor, excess_credit, claims_factor, conviction_factor, final_premium.

---

## Phase 2: Haute Feature Showcase

### 2.1 Rating Table Showcase Data
**Effort: Medium**

The 20 insurer profiles already use multiplicative factors. Extract and document:
- **8 one-way tables**: age (14 bands), NCD (8 bands), vehicle group (10 bands), area (5 bands), cover type (3), excess (6), overnight location (5), mileage (8 bands)
- **6 two-way tables**: age x vehicle_group (most important -- add super-additive interaction for age<25 AND group>=30), area x cover_type, NCD x claims, age x mileage, vehicle_group x fuel_type, overnight x area
- **3 three-way tables**: age x vehicle_group x cover_type, area x NCD x mileage, age x overnight x vehicle_group
- Demonstrate all 4 combination operations: MULTIPLY (standard GLM), ADD (claims surcharge GBP), MIN (premium cap), MAX (minimum premium floor)

### 2.2 Banding Demonstrations
**Effort: Low**

Document optimal band boundaries aligned with existing rating factor breakpoints:
- 7 continuous variables with 6-8 bands each (age, value, engine_cc, mileage, vehicle_age, licence_years, NCD)
- 5 categorical consolidations: occupation (200+ -> 10 groups), vehicle make (50+ -> 5 tiers), postcode area (120+ -> 5 bands), employment status (11 -> 4), overnight location (6 -> 3)
- Multi-factor banding: age x gender (8 combined levels), vehicle_age x value (12 levels)

### 2.3 CatBoost vs GLM Comparison Data
**Effort: Medium**

Embed patterns in the DGP that CatBoost captures but GLMs miss:
- Non-linear age spike at 22 (university leavers)
- Threshold effects at vehicle group 25 and 35
- Mileage saturation beyond 15,000 miles
- 5 high-cardinality categoricals: postcode_area (~120), vehicle_make (~50), make_model (~500), occupation (~300), previous_insurer (~20)
- 3-5 noise features that should rank near zero

Expected CatBoost improvement: 6-10% lower deviance than best GLM.

### 2.4 Price Optimization & Efficient Frontier Data
**Effort: High**

Create `generate_optimization.py` producing:
- 8 customer segments with distinct elasticities (young urban male: -0.4, senior rural: -0.10)
- Parametric logit demand model replacing the ad-hoc conversion model
- 2,000 pricing scenarios with segment-level rate changes
- Pareto frontier with 25-35 non-dominated points
- 6 named strategies: max profit, profit focus, balanced, growth tilt, volume push, max volume
- Constraints: loss ratio <=65%, volume >=95%, max price change +/-15%

### 2.5 Impact Analysis Data (5 Pricing Versions)
**Effort: Medium**

Define 5 progressive pricing versions for BritSure:
- **v1**: Current baseline (blunt age/NCD/area/group)
- **v2**: Age curve reshape (steeper young, gentle senior discount)
- **v3**: Add occupation rating factor (unwinding cross-subsidy)
- **v4**: Vehicle sophistication (tracker/EV/overnight/vehicle_age factors)
- **v5 staging**: Interaction rules (young+high_group=decline, married+homeowner+9NCD=loyalty)

Each version generates a wide Parquet with per-quote premiums from all 5 versions plus segment labels, expected loss costs, and competitive position. Ship 6 canonical smoke test quotes (young high-risk, standard mid-aged, elderly, high-value Porsche, EV Tesla, provisional).

### 2.6 AvE Diagnostics with Deliberate Misfit
**Effort: Medium**

Embed specific deviations between the DGP and BritSure's pricing model:
- Young drivers 17-19: DGP 15% hotter than pricing (A/E > 1.10)
- EVs: DGP severity 25% higher than model expects (battery costs)
- Urban postcodes: DGP theft frequency rising faster than area factors
- High mileage (>15k): DGP uses steeper elasticity (0.65 vs model's 0.50)
- Recent claimants: 25% frequency uplift vs flat 15% loading

Target diagnostics: Gini 0.12-0.18 (frequency), 0.06-0.12 (severity). Clear double-lift chart separation across deciles.

### 2.7 Submodel Composition Architecture
**Effort: Medium**

10 submodels composing into final premium:
Frequency -> Severity -> Burning Cost -> Large Loss Loading -> Reinsurance Recovery -> Expense Loading -> Commission -> NCD -> Conversion/Demand -> Lapse

Each submodel exposes primary output + drill-down intermediates. Premium waterfall traceable from £1,080 base rate down to £250 final premium through each multiplicative/additive step.

### 2.8 Monte Carlo Scenario Expansion
**Effort: Medium**

1,000 simulations per risk using existing peril parameters:
- Stochastic variables: claim count (Poisson), severity (log-normal/Pareto), large losses (compound Poisson), catastrophe events (2.5/year windstorm rate)
- Output: VaR(99.5%), TVaR(99.5%), P(zero claims), aggregate loss distribution
- Reinsurance testing: XoL retention 500K, limit 5M

### 2.9 Deployment Test Fixtures
**Effort: Low**

8 canonical test quotes (JSON) with expected premium ranges and decline/accept decisions:
- TC-01: Young/high-risk/provisional (£3,800-6,200, 8-12 declines)
- TC-02: Standard mid-aged (£290-450, 0 declines)
- TC-03: Elderly 78yo (£280-420, 1 decline)
- TC-04: High-value Porsche (£650-1,100, 2-3 declines)
- TC-05: Electric Tesla (£420-680, VoltEdge cheapest)
- TC-06: Provisional licence (£2,200-3,500, 9-11 declines)
- TC-07: Multiple named drivers (£280-430)
- TC-08: Heavy claims + convictions (£850-1,400)

Plus 3-tier smoke test structure, approval gate workflow, and CI/CD artifacts.

---

## Phase 3: Realism Improvements

### 3.1 UK Demographics (High Impact, Low-Medium Effort)

| Gap | Current | Fix | Impact |
|-----|---------|-----|--------|
| Age x occupation | Independent | Age-gate occupation sampling (block 18yo CEO) | Very high |
| Birth-cohort names | 2024 top-100 for all ages | ONS historical names by decade (1930-2024) | Very high (immediately visible) |
| IMD x vehicle value | IMD loaded but unused | Expose imd_decile, multiply vehicle value (0.55x-1.60x) | Very high |
| Occupation x vehicle | Independent | Make affinity by SOC major group | High |
| Cohabitation by age | Flat 15% | Age-graded (12% at 20, 25% at 25-29, 5% at 55+) | Medium |

### 3.2 Vehicle Data (High Impact, Medium Effort)

| Gap | Fix | Impact |
|-----|-----|--------|
| Insurance group accuracy | Make/model lookup table (Ford Fiesta=6-16, Tesla Model 3=46-49) | Critical (cascades to all premiums) |
| Fuel x vehicle age | Constrain EVs to post-2013, diesel declining post-2017 | High |
| Make x owner age | Age affinity matrix (young->Corsa/Fiesta, middle->BMW/Audi) | High |
| ADAS features | New boolean fields by year/tier (AEB mandatory post-Jul 2024) | High |
| EV-specific fields | Battery capacity, range, charging, replacement cost | Medium |
| Telematics data | Driving scores, time-of-day, mileage verification (45% of young drivers) | Medium |
| Engine x make tier | Budget cars shouldn't get 3000cc engines | Medium |

### 3.3 Claims Realism (High Impact, High Effort)

| Gap | Fix | Impact |
|-----|-----|--------|
| French data for UK claims | UK-calibrated base rates (0.28 for age 17-20, 0.10 for 30-40) | Very high |
| No development patterns | Add loss_date, notification_date, settlement_date, status | Very high (enables reserving) |
| Single log-normal severity | Spliced log-normal + Pareto tail for large losses | High |
| No bodily injury detail | Whiplash tariff (post-2021 reform), serious/catastrophic/fatal sub-types | High |
| No fraud indicators | 4% fraud rate with detectable red flags (early policy, night incidents) | Medium |
| No third-party claims | TP property, TP BI, credit hire (~50% of claim cost) | Medium |
| No recovery/subrogation | 75% success rate on not-at-fault, salvage, excess recovery | Medium |

### 3.4 Geographic Patterns (Medium Effort)

- Area-level theft, accident, flood risk variation (not just 5 bands)
- Postcode x vehicle type, postcode x parking, postcode x commuting
- Urban vs rural beyond boolean (continuous density)
- Regional market differences (Scotland different legal system)

### 3.5 Temporal Trends (Medium Effort)

- Multi-year data (3-5 year inception window) for temporal train/test splits
- Claims frequency trends: declining due to ADAS
- Severity inflation: parts +5-8%, labour +3-4%, whiplash reform impact
- Seasonal patterns: January quote peak, November theft peak
- Premium cycle: soft market -> hard market (~5-7 year cycle)

### 3.6 Exposure & Earned Premium (Medium Effort)

- 5 policy types: full annual (92%), mid-term cancellation (8% with reason-specific timing), MTA (15%), short-term (3%), monthly rolling (5%)
- Earned vs written premium with 1/365ths pro-rata earning
- Inception date seasonality (December peak, July trough)
- Development triangles: accident year, policy year, calendar year
- IBNR with reporting delays (TP BI: up to 6 years) and case reserve under-estimation

### 3.7 NCD Improvements (Low Effort)

- Refine step ladder: 0/30/35/40/45/50/53/55/57/60% (currently too aggressive at years 2-3)
- Graduate NCD protection take-up by NCD level (not flat 35%)
- Generate NCD before claims to ensure consistency
- Multi-year NCD transition trajectories for submodel demo

### 3.8 Conversion/Demand Improvements (Medium Effort)

- Segment-level price elasticity (young: -0.4, senior: -0.10)
- Non-price conversion factors (brand trust, policy features)
- Renewal vs new business dynamics with price walking data
- Time-to-purchase patterns
- FCA pricing remedies impact

---

## Phase 4: New Lines of Business

Each new line enables haute's multi-product pipeline and demonstrates that the framework is product-agnostic.

### 4.1 Home/Property Insurance
**Effort: High**

New generator covering buildings + contents with:
- Risk factors: property type, construction, year built, bedrooms, rebuild/contents value, security, flood zone, subsidence risk
- Perils: escape of water (35%), storm (20%), theft (15%), accidental damage (12%), subsidence (5%), fire (3%)
- Pricing structure: buildings + contents + combined, with area-specific factors
- UK data sources: VOA council tax bands, Environment Agency flood maps, BGS subsidence data

### 4.2 Pet Insurance
**Effort: Medium**

- Risk factors: species, breed, age, neutered status, pre-existing conditions
- Coverage: accident only, time-limited, maximum benefit, lifetime
- Breed-specific patterns: brachycephalic respiratory, large breed joint issues
- Very different risk profile from motor -- demonstrates haute flexibility

### 4.3 Commercial Motor
**Effort: Medium**

- Entity types: sole trader, SME fleet, haulage, taxi
- Fleet structures: 1-5, 5-20, 20-100 vehicles
- Vehicle types: vans, HGVs, taxis, minibuses
- Different pricing: fleet discount, industry SIC code, driver management

### 4.4 Travel Insurance
**Effort: Low-Medium**

- Trip types: single, annual, backpacker, cruise, ski, business
- Coverage: medical, cancellation, baggage, personal liability
- Destination risk zones: Europe, USA, worldwide
- Short duration, destination-based risk -- very different from motor

---

## Phase 5: Documentation, Tooling & Publishing

### 5.1 Tutorial Notebooks (7 core + 3 appendix)

| # | Title | Time | Difficulty |
|---|-------|------|-----------|
| 01 | Getting Started: Load Data into Haute | 15 min | Beginner |
| 02 | Build Your First GLM Frequency Model | 30 min | Beginner |
| 03 | Build a CatBoost Severity Model | 30 min | Intermediate |
| 04 | Create a Complete Pricing Pipeline | 45 min | Intermediate |
| 05 | Optimise Prices for Profit | 45 min | Advanced |
| 06 | Deploy Your Model to Production | 30 min | Advanced |
| 07 | Multi-Line Pricing: Motor + Home + Pet | 45 min | Advanced |
| A1 | Data Validation Report | -- | Reference |
| A2 | Explore Competitor Panel | -- | Reference |
| A3 | Conversion Model Deep Dive | -- | Reference |

### 5.2 Data Dictionary
Complete field reference for all output files: quotes (281 fields), premiums (21 columns), policies, claims. Every column documented with type, example, distribution, and source.

### 5.3 Validation Report
Statistical validation comparing generated distributions to reference data. KS tests, chi-squared tests, correlation checks, plausibility tests. Regenerable from notebook A1.

### 5.4 Quickstart Guide
Single-page, copy-paste guide: clone -> install -> generate 10K quotes -> flatten -> fit GLM in haute -> score a new quote. Under 5 minutes.

### 5.5 Example Haute Pipeline Configs
7 YAML files loadable directly by haute: frequency GLM, severity CatBoost, complete motor pipeline, feature engineering, expense loadings, price optimisation, multi-line bundle.

### 5.6 Parquet Schema Optimisation
- Switch date columns from string to `pa.date32()`
- Dictionary-encode low-cardinality strings (cover_type, fuel_type, body_type, etc.)
- Add Parquet metadata (generator version, seed, data role)
- Create `quotes_flat.parquet` eliminating JSON parsing from the hot path
- Zstd level 3 compression, 100K row groups, sorted for predicate pushdown

### 5.7 Benchmark Publishing
- Flat CSV/Parquet files suitable for academic use
- Pre-split train/test with documented methodology
- Croissant/datapackage.json metadata for machine discovery
- DOI via Zenodo, BibTeX citation entry
- Model card showing GLM/CatBoost produce realistic results on the data

### 5.8 Regulatory Compliance Data
- **Solvency II**: Pricing decomposition (risk premium -> expenses -> profit -> capital -> reinsurance -> levies -> GWP), stress testing scenarios (10 defined), SCR summary
- **IFRS 17**: Premium components (expected claims PV, risk adjustment, CSM), cohort grouping by profitability/quarter
- **FCA Consumer Duty**: Fairness metrics, vulnerability flags, counterfactual pairs for bias detection, price walking data via renewal chains

---

## Phase Summary

| Phase | Focus | Key Deliverables | Effort |
|-------|-------|-----------------|--------|
| **1** | Foundation | Flat modelling table, DGP config, exposure persistence, pricing decomposition | 4-6 weeks |
| **2** | Haute Features | Rating tables, banding, CatBoost data, optimization, impact analysis, AvE, submodels, Monte Carlo, test fixtures | 6-8 weeks |
| **3** | Realism | Demographics, vehicles, claims, geography, trends, exposure, NCD, demand | 8-12 weeks |
| **4** | New Lines | Home, pet, commercial, travel insurance generators | 6-10 weeks |
| **5** | Docs & Tooling | Tutorials, data dictionary, validation, quickstart, Parquet optimization, benchmark publishing, regulatory data | 4-6 weeks |

Phases 1-2 are sequential (2 depends on 1). Phases 3-5 can be parallelised. Total: ~6-9 months for the complete vision, with Phase 1 deliverables available in ~4-6 weeks.

---

## Demo Scenarios

Five end-to-end scenarios showcase haute's complete feature set:

1. **New Analyst Onboarding** (10K quotes, 15 min): Load data -> fit GLM -> view rating tables -> AvE diagnostics -> trace a single quote
2. **Annual Rate Review** (2x 100K quotes, 30 min): Drift detection via AvE -> refit GLM + CatBoost -> impact analysis -> deploy v2
3. **Launch New Product** (1M quotes, 45 min): Competitive analysis -> reverse-engineer FirstMile -> build telematics product -> efficient frontier -> simulate launch
4. **Regulatory Audit** (100K quotes, 30 min): Model inventory -> factor explainability waterfall -> protected characteristic analysis -> proxy discrimination test -> audit trail
5. **Price Optimization Sprint** (1M quotes, 45 min): Validate model -> build demand model -> identify optimization segments -> efficient frontier -> simulate impact -> deploy

---

## Data Size Recommendations

| Tier | Quotes | Policies | Claims | Disk | Use Case |
|------|--------|----------|--------|------|----------|
| Micro | 1,000 | ~50 | ~5 | ~500 KB | Unit tests, CI |
| Small | 10,000 | ~500 | ~50 | ~5 MB | Notebook demos |
| Medium | 100,000 | ~5,000 | ~500 | ~50 MB | Full model builds |
| Standard | 1,000,000 | ~50,000 | ~5,000 | ~500 MB | Optimization, competition analysis |
| Large | 10,000,000 | ~500,000 | ~50,000 | ~5 GB | GPU acceleration, stress testing |

Ship the repo with a pre-generated 10K dataset (committed to git). Provide generation commands for larger sizes.
