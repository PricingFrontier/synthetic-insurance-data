# Synthetic UK Motor Insurance Data — Distribution & Assumptions Plan

This document maps every field in the schema to:
- **What distribution/correlation is needed**
- **What public data can inform it** (with specific dataset names)
- **Fallback assumption** where no data source exists

---

## 1. Quote Metadata

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| `quote_id` | Sequential / UUID | N/A — generated | `QUO-YYYY-NNNNNNNNN` format |
| `quote_version` | ~90% version 1, ~8% version 2, ~2% version 3+ | No public data | Assumption: most quotes are first-attempt |
| `quote_timestamp` | Date distribution across year; time-of-day distribution | Google Trends for "car insurance quote" shows seasonal pattern (peaks Jan, Jun-Jul for renewals); GoCompare has published time-of-day stats | Peak hours 18:00-21:00 (evenings), seasonal peak in Q1 and pre-summer. Weekday > weekend ~65/35 |
| `channel` | Categorical: CTM ~30%, MSM ~25%, Confused ~20%, GoCompare ~15%, direct ~10% | FCA General Insurance Distribution report; ABI market share data | Approximate from published aggregator market shares |
| `aggregator_quote_id` | Conditional on channel being an aggregator | N/A — generated | Format per aggregator: CTM-NNNN, GC-NNNN, etc. |

### Key correlations
- `quote_timestamp` ↔ `cover_start_date`: most quotes are 1-30 days before cover start; renewals cluster 21-28 days before expiry

---

## 2. Policy Details

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| `cover_start_date` | Uniform across year with slight Jan/Mar/Sep peaks | No direct public data; inferred from vehicle registration seasonality (DVLA new reg plates Mar & Sep) | Slight peaks around plate-change months |
| `cover_end_date` | Deterministic: start + 12 months | N/A | Always start + 365 days |
| `cover_type` | Comprehensive ~85%, TPFT ~10%, TPO ~5% | ABI motor insurance data; FCA data tables | ABI consistently reports ~85% comprehensive |
| `payment_frequency` | Annual ~45%, Monthly ~55% | ABI / FCA reports on credit take-up | Monthly slightly more common |
| `voluntary_excess` | Discrete: £0, £100, £150, £250, £500, £1000 | No direct public data | £250 most common (~35%), then £100 (~25%), £0 (~15%), £500 (~15%), £1000 (~5%), £150 (~5%). Correlates with age (younger → higher excess to reduce premium) |
| `compulsory_excess` | Depends on driver age + vehicle | Insurer practice | £150 standard; £350+ for under-25s; £100 for over-50s. Vehicle-group dependent |
| `ncd_years` | 0-20, heavily right-skewed for experienced drivers | freMTPL2 (exposure distribution); ABI average NCD stats | Correlated with age/licence years. Median ~5 years. New drivers = 0 |
| `ncd_protected` | Boolean, ~30-40% of those with 4+ years NCD | No public data | 35% if NCD ≥ 4, else 5% |
| `usage.social_domestic_pleasure` | ~98% true | No public data | Near-universal |
| `usage.commuting` | ~65% true | DfT National Travel Survey (commute mode share) | Correlates with employment_status (employed/self-employed → higher) |
| `usage.business_use` | ~8% class_1, ~2% class_2, ~0.5% class_3 | No direct data | Correlates with occupation |
| `annual_mileage` | Log-normal, mean ~7,900, sd ~4,000 | **DfT/DVLA MOT anonymised data** — has actual odometer readings by vehicle age. Also NTS average miles per year | Strong correlation with: commuting (yes → +3000), business use, urban/rural, age |
| `business_mileage` | Conditional on business_use; typically 20-40% of annual | No direct data | Assumption: 20-40% of annual_mileage |
| `is_renewal` | ~60% of quotes are renewals (by volume) | ABI switching stats; FCA renewal data | 60% renewal, 40% new business |
| `previous_insurer` | Categorical: market share weighted | ABI / Statista UK motor insurer market shares | Top 10 insurers cover ~70% of market. Admiral ~14%, Direct Line ~12%, Aviva ~10%, etc. |

### Key correlations
- `annual_mileage` ↔ `usage.commuting` + `usage.business_use` + `address.postcode` (rural → higher)
- `ncd_years` ↔ `proposer.licence.licence_held_years` (NCD ≤ licence years; NCD resets on at-fault claims)
- `voluntary_excess` ↔ `proposer.date_of_birth` (younger drivers choose higher to reduce premium)
- `cover_type` ↔ `vehicle.estimated_value` (low-value cars → more likely TPFT/TPO)
- `payment_frequency` ↔ age + credit profile (younger → more monthly)

---

## 3. Proposer (Policyholder)

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| `title` | Conditional on gender | N/A | Male → Mr (98%), Dr (2%). Female → Mrs (40%), Miss (25%), Ms (30%), Dr (5%). Correlated with marital_status + age |
| `first_name` | Realistic UK names by gender + birth cohort | **ONS baby name statistics** (published yearly since 1996, goes back to 1904) | Sample from ONS name data matching birth year |
| `last_name` | Realistic UK surnames | **ONS surname frequency data** or published lists | Top 1000 UK surnames, frequency-weighted |
| `date_of_birth` | Age distribution of UK drivers 17-90+ | **DfT driving licence statistics** (DVLA data: full licence holders by age band and gender) | Peak density 30-55. Minimum 17. Distribution matches DVLA licence holder age pyramid |
| `gender` | ~52% male, ~48% female among policyholders | DVLA licence holders by gender; ABI policyholder stats | Slight male skew in motor insurance |
| `marital_status` | Age-dependent categorical | **ONS population estimates by marital status and age** | Single dominant <25, married dominant 30-65, widowed increases 70+ |
| `uk_resident_since_birth` | ~85% true | **ONS migration statistics** (% population born in UK by age) | ~85% true; lower for age 25-40 (immigration peak) |
| `uk_resident_since` | Conditional on above; year distribution | ONS migration data by year of arrival | Uniform-ish over last 20 years if not since birth |
| `employment_status` | Age-dependent categorical | **ONS Labour Force Survey** / Annual Population Survey | Employed ~60%, self-employed ~12%, retired ~18% (age-driven), student ~5%, unemployed ~3%, house_person ~2% |
| `primary_occupation` | ~350 common ABI occupation codes | **ONS Standard Occupational Classification** + ABI occupation list; ONS Annual Survey of Hours and Earnings for frequency | Top 50 occupations cover ~60% of workforce. Need realistic frequency distribution |
| `primary_occupation_industry` | Conditional on occupation | ONS SIC codes → occupation mapping | Deterministic from occupation lookup table |
| `secondary_occupation` | ~5% have one | No data | Assumption: 5% chance, drawn from same occupation pool |
| `licence.licence_type` | ~92% full_uk, ~3% provisional, ~4% EU, ~1% international | DVLA licence statistics | Correlated with uk_resident_since_birth |
| `licence.licence_held_years` | Deterministic-ish: age - 17 for full_uk, but capped | DVLA data on age at first licence | Typically age - 17 to age - 20 for those who passed after 17. Some pass later. Distribution: median pass age ~19 |
| `licence.licence_date` | Derived from licence_held_years | N/A | DOB + 17-22 years for most |
| `has_medical_conditions` | ~3-5% | DVLA medical conditions statistics | 4% overall; rises with age (1% under 30, 8% over 65) |
| `medical_condition_details` | Conditional categorical | DVLA medical condition data | Diabetes (~25% of medical), epilepsy (~15%), visual impairment (~15%), heart conditions (~20%) |
| `has_criminal_convictions` | ~2% | MoJ criminal justice statistics | 2% overall; skews younger and male |
| `access_to_other_vehicles` | ~35% | No direct data | Correlates with household size, marital status (married → higher), number of additional drivers |
| `is_homeowner` | ~63% overall, age-dependent | **ONS housing tenure data by age** | 25-34: ~35%, 35-44: ~55%, 45-64: ~72%, 65+: ~78% |
| `is_main_driver` | ~95% true | No data | Assumption: 95%. When false, usually parent insuring child's car |
| `relationship_to_main_driver` | Conditional on is_main_driver=false | No data | Parent (~70%), spouse (~20%), other (~10%) |
| `claims` | See claims section below | freMTPL2 for frequency; ABI for UK severity | ~7% have 1+ claims in last 5 years |
| `convictions` | See convictions section below | MoJ / DVLA conviction stats | ~5% have 1+ convictions in last 5 years |

### Key correlations
- `date_of_birth` ↔ `marital_status`, `employment_status`, `is_homeowner`, `ncd_years`, `licence_held_years`
- `gender` ↔ `title`, `first_name`, `occupation` (some occupations skew heavily)
- `employment_status` ↔ `occupation` (retired → null occupation, student → null, etc.)
- `uk_resident_since_birth` ↔ `licence_type` (non-UK born → higher chance of EU/international licence)
- `has_medical_conditions` ↔ `date_of_birth` (strong age correlation)

---

## 4. Additional Drivers

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| Number of additional drivers | 0, 1, 2, 3, 4 | No public data | ~45% have 0, ~35% have 1, ~15% have 2, ~4% have 3, ~1% have 4. Correlated with marital_status (married → likely 1+) and age (parents add children) |
| `relationship_to_proposer` | Conditional on proposer age | No data | Spouse/partner ~55%, child ~20%, parent ~15%, sibling ~5%, other ~5% |
| All person fields | Same distributions as proposer but conditioned on relationship | Same sources as proposer | Spouse: similar age (±5yr), same address. Child: age 17-25 if proposer 40+. Parent: age +20-30yr |

### Key correlations
- If `relationship_to_proposer` = spouse → similar age, same surname (often), married marital_status
- If child → young (17-25), provisional or recently full licence, single, student or newly employed
- If parent → older (55-80), retired more likely, long licence tenure, high NCD

---

## 5. Claims History

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| Number of claims (5yr) | Poisson-like, mean ~0.08/year → ~0.4 over 5 years | **freMTPL2freq** dataset: ~670k policies with claim counts and exposure. Also **ABI claims statistics** (annual UK motor claims ~2.7m on ~30m policies) | ~67% zero claims in 5yr, ~25% one claim, ~6% two, ~2% three+ |
| `claim_date` | Uniform across the 5-year lookback | N/A | Uniform |
| `claim_type` | Categorical | ABI claims breakdown by type | Accident ~72%, windscreen ~10%, theft ~6%, vandalism ~4%, storm/flood ~3%, fire ~1%, personal injury ~3%, other ~1% |
| `fault` | Conditional on claim_type | ABI fault/non-fault split | Accident: at_fault ~55%, not_at_fault ~40%, pending ~5%. Theft/vandalism/fire: not_at_fault ~95% |
| `status` | ~95% settled, ~5% open | No data | Recent claims (last 6 months) more likely open |
| `amount_paid` | Right-skewed; depends on type | **freMTPL2sev** for severity distribution; ABI average claim costs | Accident: mean ~£3,500, median ~£2,000. Windscreen: mean ~£350. Theft: mean ~£5,000. Log-normal fits well |
| `personal_injury_claimed` | ~15% of accident claims | ABI personal injury stats | 15% of accidents |
| `ncd_affected` | Conditional on fault | N/A — logical | at_fault → true, not_at_fault → false, pending → false |

### Key correlations
- Claim frequency ↔ proposer age (U-shaped: high for young, low for middle-aged, slight rise for elderly)
- Claim frequency ↔ annual_mileage (linear relationship)
- Claim severity ↔ vehicle value (higher value → higher repair cost)
- Claim frequency ↔ postcode area (urban → higher)
- `ncd_years` must be consistent with claims history (at-fault claim resets or reduces NCD)

---

## 6. Convictions

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| Number of convictions (5yr) | ~95% zero, ~4% one, ~1% two+ | **MoJ motoring conviction statistics**; DVLA endorsement data | 5% have any conviction |
| `conviction_code` | Categorical | **DVLA conviction code frequencies** (published by MoJ) | SP30 (speeding 30 limit) ~50%, SP50 (speeding motorway) ~15%, CU80 (using mobile) ~10%, TS10 (no insurance) ~5%, DR10 (drink drive) ~3%, IN10 ~3%, other ~14% |
| `conviction_date` | Uniform over 5yr window | N/A | Uniform |
| `points` | Conditional on code | DVLA — points per offence are fixed by code | SP30: 3pts, DR10: 3-11pts (usually 6), CU80: 6pts, etc. |
| `fine_amount` | Conditional on code | Sentencing Council guidelines | SP30: £100-£200, DR10: £500-£2000, CU80: £200 |
| `ban_months` | Rare except DR offences | MoJ sentencing data | DR10: 12-36 months ban. Others: null usually |
| `is_disqualified` | Conditional on ban_months | Deterministic | True if ban_months > 0 and ban period overlaps current date |

### Key correlations
- Conviction frequency ↔ age (young males highest)
- Conviction frequency ↔ gender (males ~3x more likely)
- DR offences ↔ age band 25-40 peak

---

## 7. Vehicle

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| `registration_number` | Valid UK VRM format by year | DVLA plate format rules (LLNN LLL since 2001) | Generate from age identifier matching year_of_registration |
| `make` | Market-share weighted | **DVLA/DfT vehicle licensing statistics** (VEH0120: vehicles by make); **SMMT new car registration data** | Ford ~12%, Vauxhall ~9%, VW ~8%, BMW ~7%, Audi ~6%, Mercedes ~5%, Toyota ~5%, etc. |
| `model` | Conditional on make | **DfT VEH0120** or **DVLA anonymised MOT data** (has make/model for millions of MOTs) | Top models per make: Ford → Fiesta/Focus/Puma, VW → Golf/Polo/Tiguan, etc. |
| `variant` | Conditional on make+model | MOT data doesn't have this reliably | Build a lookup table of common variants per make/model. Assumption-based |
| `year_of_manufacture` | Vehicle age distribution | **DfT VEH0211** (vehicles by year of first registration); **MOT data** has exact ages | Median vehicle age ~8 years. Right-skewed distribution. New cars (0-3yr) ~25%, 4-7yr ~30%, 8-12yr ~25%, 13+yr ~20% |
| `year_of_registration` | Usually = year_of_manufacture or +1 | Same as above | Same year as manufacture ~90%, +1 year ~10% |
| `body_type` | Conditional on make/model, but also marginal | **SMMT data by body type**; DfT stats | Hatchback ~35%, SUV ~25%, Saloon ~15%, Estate ~10%, MPV ~5%, Coupe ~3%, Convertible ~2%, other ~5%. Trend: SUVs rising |
| `fuel_type` | Year-dependent (EV share growing) | **DfT VEH0203** (vehicles by fuel type); SMMT monthly registrations by fuel | 2024 stock: Petrol ~58%, Diesel ~30%, Hybrid ~7%, BEV ~4%, PHEV ~1%. New registrations: BEV ~20% |
| `transmission` | Make/model dependent; overall trend | **MOT data** or SMMT | Manual ~55%, Automatic ~42%, Semi-auto ~3%. Trend: auto rising. EVs are always auto |
| `engine_size_cc` | Conditional on fuel_type + make/model | MOT data has engine size | Petrol: mode ~1200-1500cc. Diesel: mode ~1600-2000cc. EV: null. Distribution is multimodal (clusters at common sizes) |
| `engine_power_bhp` | Correlated with engine_size + fuel_type | Less publicly available | Rough mapping: 1.0L ~70-100bhp, 1.5L ~100-150bhp, 2.0L ~150-200bhp. EVs: 100-300bhp typically |
| `number_of_doors` | Conditional on body_type | No data | Hatchback: 3 (~20%) or 5 (~80%). Saloon: 4. Estate: 5. SUV: 5. Coupe: 2 or 3. Convertible: 2 |
| `number_of_seats` | Almost always 5 | No data | 5 (~90%), 4 (~3%), 7 (~5% for MPVs/large SUVs), 2 (~2% for sports/coupes) |
| `insurance_group` | Conditional on make/model/variant | **Thatcham / ABI group rating database** — not freely available, but average groups per popular model are published on comparison sites | Build lookup: Fiesta ~8-12, Golf ~14-20, BMW 3 Series ~25-35, etc. Correlates with value + performance |
| `abi_code` | Lookup from make/model/variant | ABI vehicle database — not public | Generate plausible 8-digit codes; or leave null |
| `estimated_value` | Conditional on make/model/year | **Auto Trader / Parkers price guide** (not API-accessible but could scrape ranges); **MOT data + age → depreciation curves** | Depreciation model: new_price × (0.85^age) with floor. New prices from published lists |
| `is_imported` | ~2% of UK vehicles | DfT stats | 2% true |
| `is_right_hand_drive` | ~98% true (imports are sometimes LHD) | N/A | 98% true; 50/50 if imported |
| `has_been_modified` | ~5-8% | No public data | 6% overall; higher for younger males, sports cars |
| `modifications` | Conditional categorical | No data | Alloy wheels (~40% of mods), tinted windows (~20%), exhaust (~10%), body kit (~10%), engine tuning (~5%), other (~15%) |
| `security.alarm` | Conditional on vehicle age/make | Thatcham security ratings; general knowledge | Modern cars (post-2010): factory_fitted ~85%. Older: 60% factory, 10% aftermarket, 30% none |
| `security.immobiliser` | Near-universal post-1998 (UK legal requirement) | Thatcham | factory_fitted ~95% for post-1998 vehicles |
| `security.tracker` | ~5-10% | No public data | 7% overall; higher for high-value vehicles (group 30+) |
| `overnight_location` | Categorical | No direct data; ONS housing type gives proxy (houses → driveway, flats → street) | Driveway ~40%, street_near_home ~30%, garage ~15%, car_park ~10%, other ~5%. Correlates with housing type (postcode proxy) |
| `daytime_location` | Conditional on employment | No data | Employed+commuting: office_car_park ~50%, street_near_work ~30%. Not commuting: at_home ~70% |
| `owner` | ~90% proposer | No data | Proposer ~90%, spouse ~4%, parent ~3%, company ~2%, leasing ~1% |
| `registered_keeper` | ~92% proposer | No data | Usually matches owner |
| `purchase_date` | Correlated with vehicle age + policy start | No data | Typically within last 0-5 years; recent purchases correlated with new business (not renewal) |
| `current_mileage` | Vehicle age × average annual miles | **MOT data** — has odometer readings by vehicle age | ~7,500 miles/year average. Log-normal per year of age. Total = age × annual average with noise |

### Key correlations
- `make` + `model` → determines `body_type`, `fuel_type`, `transmission`, `engine_size_cc`, `insurance_group`, `estimated_value` (these are essentially lookup-driven)
- `year_of_manufacture` ↔ `estimated_value` (depreciation curve)
- `year_of_manufacture` ↔ `fuel_type` (newer → more EV/hybrid)
- `insurance_group` ↔ `estimated_value` + `engine_power_bhp`
- `overnight_location` ↔ `address.postcode` (urban flat → street; suburban house → driveway)
- `has_been_modified` ↔ proposer age + gender (young male → higher chance)
- Vehicle choice ↔ proposer age + occupation (young → Fiesta/Corsa; family 35-50 → SUV/estate; retired → smaller/cheaper)

---

## 8. Address

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| `postcode` | Population-weighted UK postcodes | **ONS Postcode Directory (ONSPD)** — free download, maps every UK postcode to region, LA, IMD, urban/rural, coordinates | Sample postcodes weighted by residential population. ~1.8m live postcodes |
| `city` / `county` / `address_line_1` | Derived from postcode | **ONSPD** gives postcode → region/LA. **OS Open Names** or **Ordnance Survey OpenData** for street names | Lookup from postcode. Street names: top 100 UK street names (High Street, Station Road, etc.) with regional variation |
| `house_number_or_name` | Numeric 1-200, or named | No data | 80% numeric (1-150, right-skewed), 20% named |
| `years_at_address` | Age-dependent, right-skewed | **ONS internal migration data** (moves by age band) | Young (18-25): 1-3yr typical. 30-45: 3-8yr. 55+: 10-20yr. Correlated with is_homeowner |
| `months_at_address` | Uniform 0-11 | N/A | Uniform |
| `previous_address` | Required if years_at_address < 3 | Same sources | Different postcode, same region ~70%, different region ~30% |

### Key correlations
- `postcode` ↔ vehicle choice, annual_mileage, overnight_location, claim frequency
- `years_at_address` ↔ age, is_homeowner
- Region ↔ occupation distribution, insurer market share

---

## 9. Add-ons

| Field | Distribution needed | Data source | Assumption if no data |
|---|---|---|---|
| Selection rates per add-on | Binary per add-on | No public data; occasional insurer reports | breakdown_cover ~25%, legal_expenses ~20%, key_cover ~15%, motor_legal_protection ~15%, courtesy_car ~12%, windscreen_cover ~10%, excess_protection ~10%, personal_accident ~5%, personal_belongings ~3%, tools_in_transit ~1%, no_claims_step_back ~8% |
| `level` (for breakdown) | Conditional on selected | RAC/AA published breakdowns of cover levels | Roadside ~40%, national_recovery ~35%, european ~25% |

### Key correlations
- Breakdown cover ↔ vehicle age (older → more likely)
- Excess protection ↔ voluntary_excess (higher excess → more likely)
- Key cover ↔ vehicle value (expensive → more likely)

---

## 10. Summary: Data Sources to Acquire

### Tier 1 — Free, directly downloadable, high value
| Dataset | Fields it informs | URL / access |
|---|---|---|
| **freMTPL2freq + freMTPL2sev** | Claim frequency, severity, exposure, driver age, vehicle power, region | OpenML (dataset 41214) or R `CASdatasets` |
| **DVLA anonymised MOT data** | Vehicle make/model, age, mileage, fuel type, engine size | data.gov.uk — ~35m records/year |
| **ONS Postcode Directory (ONSPD)** | Postcode → region, IMD, urban/rural, coordinates | geoportal.statistics.gov.uk |
| **DfT vehicle licensing stats (VEH tables)** | Vehicle parc by make, fuel, age, region | gov.uk/government/statistical-data-sets |
| **DVLA driving licence stats** | Licence holders by age, gender | gov.uk |
| **ONS baby names** | Realistic first names by birth year | ons.gov.uk |
| **ONS population estimates by marital status/age** | Marital status distribution | ons.gov.uk |
| **ONS Labour Force Survey** | Employment status, occupation distribution | nomisweb.co.uk |

### Tier 2 — Free but requires some processing
| Dataset | Fields it informs |
|---|---|
| **STATS19 road accident data** | Accident frequency by vehicle type, driver age, region, road type |
| **MoJ motoring conviction statistics** | Conviction code frequencies, demographics |
| **ONS migration statistics** | uk_resident_since_birth rates by age |
| **SMMT registration data** (published summaries) | New car market share by make/fuel/body |

### Tier 3 — Assumption-based (no usable public data)
| Field group | Approach |
|---|---|
| Add-on selection rates | Industry intuition + published articles |
| Voluntary excess choice | Assumed distribution, correlated with age |
| NCD protection take-up | Assumed ~35% for 4+ NCD years |
| Vehicle modifications | Assumed 6% rate with type distribution |
| Security devices (tracker) | Assumed based on vehicle value band |
| Overnight/daytime location | Assumed from postcode urban/rural proxy |
| Occupation detail (variant level) | ABI occupation code list + ONS SOC weights |

---

## 11. Recommended Generation Order

Correlations mean fields can't be generated independently. Recommended DAG:

```
1. postcode (from ONSPD, population-weighted)
   └→ derives: city, county, region, urban_rural, IMD
2. proposer.date_of_birth (from DVLA age distribution)
   └→ proposer.gender
   └→ proposer.marital_status (conditioned on age)
   └→ proposer.employment_status (conditioned on age)
   └→ proposer.occupation (conditioned on employment_status)
   └→ proposer.is_homeowner (conditioned on age)
   └→ proposer.licence (conditioned on age)
   └→ proposer.title (conditioned on gender + marital_status)
   └→ proposer.name (conditioned on gender + birth year)
3. vehicle.make + model (conditioned on proposer age + postcode region)
   └→ vehicle.body_type, fuel_type, transmission, engine_size (lookup)
   └→ vehicle.year_of_manufacture (conditioned on make/model + proposer age)
   └→ vehicle.insurance_group, estimated_value (lookup + depreciation)
   └→ vehicle.security (conditioned on year + value)
   └→ vehicle.overnight_location (conditioned on urban_rural)
4. policy_details (conditioned on proposer + vehicle)
   └→ cover_type (conditioned on vehicle value)
   └→ annual_mileage (conditioned on commuting + urban/rural + age)
   └→ voluntary_excess (conditioned on age)
   └→ ncd_years (conditioned on age + claims)
5. claims + convictions (conditioned on age + gender + mileage + region)
6. additional_drivers (conditioned on proposer age + marital_status)
7. add_ons (conditioned on vehicle + proposer)
8. quote_metadata (timestamp, channel — light dependencies)
9. address details (from postcode + generated street/number)
```
