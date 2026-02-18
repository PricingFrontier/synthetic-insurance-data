# Synthetic UK Motor Insurance Data: Methodology & Data Sources

**Version:** 1.0
**Date:** February 2026
**Classification:** Technical Documentation — Actuarial / Pricing

---

## 1. Executive Summary

This document describes the methodology, data sources, and assumptions underpinning a synthetic data generator for UK private motor insurance quote requests. The generator produces realistic, fully structured JSON records that mirror the format and statistical properties of inbound quote submissions received by a UK motor insurer via price comparison websites (aggregators) and direct channels.

Each generated record includes policyholder demographics, vehicle details, claims and convictions history, coverage selections, and address information. The distributions governing each field are calibrated to publicly available official statistics wherever possible, with documented assumptions applied where no public data exists.

The synthetic data is intended for use in:

- Actuarial pricing model development and testing
- Data pipeline and system integration testing
- Training environments and proof-of-concept demonstrations
- Academic research requiring realistic but non-personal motor insurance data

**No real personal data is used or generated.** All records are entirely fictitious.

---

## 2. Design Principles

| Principle | Implementation |
|---|---|
| **Marginal fidelity** | Each field's marginal distribution is calibrated to the best available public data source |
| **Conditional fidelity** | Key correlations between fields (e.g. age → marital status, vehicle value → cover type) are preserved through a directed acyclic generation order |
| **Transparency** | Every distribution is traceable to either a cited public dataset or a documented assumption |
| **Reproducibility** | The generator accepts a random seed; identical seeds produce identical output |
| **Schema conformance** | Output conforms to a formal JSON schema modelling a UK aggregator quote request |

---

## 3. Generation Architecture

Records are generated sequentially following a dependency-respecting order. Each downstream field is conditioned on previously generated upstream fields, preserving realistic correlations.

```
1. Postcode        → region, urban/rural classification, deprivation
2. Proposer        → age, gender, marital status, employment, occupation,
                     licence, name, homeownership
3. Vehicle         → make, model, fuel, age, value, insurance group, security
4. Policy Details  → cover type, excess, NCD, mileage, usage
5. Claims History  → frequency (Poisson), type, fault, severity (log-normal)
6. Convictions     → frequency, offence code, points, penalties
7. Named Drivers   → demographics conditioned on relationship to proposer
8. Add-ons         → selection rates conditioned on vehicle and proposer
9. Quote Metadata  → timestamp, channel, aggregator reference
10. Address        → street, city, derived from postcode
```

---

## 4. Data Sources

### 4.1 Primary Public Datasets

The following official datasets were acquired, processed, and used to calibrate the generator's distributions. All datasets are freely available from UK government or public statistical sources.

#### 4.1.1 ONS Postcode Directory (ONSPD)

| Attribute | Detail |
|---|---|
| **Publisher** | Office for National Statistics (ONS) via the Open Geography Portal |
| **Edition** | November 2024 |
| **URL** | https://geoportal.statistics.gov.uk |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | 1,790,539 live, small-user postcodes in England, Wales, Scotland, and Northern Ireland |
| **Fields extracted** | Postcode (`pcd`), region (`rgn`), local authority (`oslaua`), Lower Super Output Area (`lsoa11`), urban/rural indicator (`ru11ind`), Index of Multiple Deprivation rank and decile, latitude, longitude |
| **Purpose** | Provides the geographic foundation for each record. Postcodes are sampled uniformly, which approximates population weighting given roughly equal residential density per postcode unit. Downstream fields (region, urban/rural, deprivation) are derived directly from ONSPD lookups. |

#### 4.1.2 DVLA Driving Licence Statistics (DRL0101)

| Attribute | Detail |
|---|---|
| **Publisher** | Department for Transport / Driver and Vehicle Licensing Agency |
| **Edition** | 2024 Q2 |
| **URL** | https://www.gov.uk/government/statistical-data-sets/data-tables-drl |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | 86 single-year age bands (ages 15–100), by gender and licence type |
| **Key statistics** | 42.8 million full licence holders; 53.5% male, 46.5% female |
| **Purpose** | Calibrates the joint distribution of proposer age and gender. Full licence holders are used as the sampling frame, reflecting the insured driving population rather than the general population. |

#### 4.1.3 ONS Marital Status Estimates

| Attribute | Detail |
|---|---|
| **Publisher** | Office for National Statistics |
| **Edition** | 2022 (latest available at time of build) |
| **URL** | https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | 150 rows: sex × age group × marital status |
| **Key statistics** | Married 48.8%, single 36.8%, divorced 7.5%, widowed 6.1%, civil partnership 0.7% |
| **Purpose** | Provides conditional marital status distributions given proposer age and sex. The schema category "living with partner" (not an ONS legal status) is introduced synthetically at a 15% substitution rate among single persons aged 20+. |

#### 4.1.4 ONS Annual Population Survey — Occupation (via Nomis)

| Attribute | Detail |
|---|---|
| **Publisher** | Office for National Statistics via Nomis (University of Durham) |
| **Edition** | 2023 |
| **URL** | https://www.nomisweb.co.uk |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | 8,638 SOC 2020 entries (1-digit major groups and 4-digit unit groups) by sex |
| **Purpose** | Provides frequency-weighted occupation sampling for employed and self-employed proposers and named drivers. Occupations are drawn at the 4-digit SOC 2020 unit group level, conditioned on gender. |

#### 4.1.5 ONS Baby Names Statistics

| Attribute | Detail |
|---|---|
| **Publisher** | Office for National Statistics |
| **Edition** | 2023 (published 2024) |
| **URL** | https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/livebirths/datasets/babynamesenglandandwalesbabynamesstatisticsboys (and equivalent for girls) |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | Top 100 boys' names and top 100 girls' names with occurrence counts |
| **Purpose** | Provides frequency-weighted first name sampling by gender. As only the current year's top names are used (rather than historical cohort-specific lists), there is an acknowledged approximation for older birth cohorts. Surnames are drawn from a curated list of 200 common UK surnames with Zipf-weighted frequencies based on published surname frequency analyses. |

#### 4.1.6 DfT Vehicle Licensing Statistics (VEH0120)

| Attribute | Detail |
|---|---|
| **Publisher** | Department for Transport |
| **Edition** | 2024 Q2 |
| **URL** | https://www.gov.uk/government/statistical-data-sets/vehicle-licensing-statistics-data-tables |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | 54,389 make × generic model × model variant × fuel type combinations |
| **Key statistics** | 34.5 million licensed cars. Ford 11.0%, Volkswagen 8.8%, Vauxhall 7.6%. Fuel split: petrol 55%, diesel 30%, hybrid electric 7.6%, battery electric 4.7% |
| **Purpose** | Provides the joint distribution of vehicle make, model, and fuel type. Vehicles are sampled proportionally to their share of the licensed fleet. Fuel type categories are mapped to schema enumerations (petrol, diesel, electric, hybrid_petrol_electric, hybrid_diesel_electric, plug_in_hybrid, lpg, other). |

#### 4.1.7 French Motor Third-Party Liability Dataset (freMTPL2)

| Attribute | Detail |
|---|---|
| **Publisher** | OpenML (originally from the French Federation of Insurers, published via R `CASdatasets` package) |
| **Dataset IDs** | OpenML 41214 (frequency), 41215 (severity) |
| **Licence** | Public domain / academic use |
| **Records used** | 678,013 policies (frequency); 26,639 claims (severity) |
| **Key statistics** | Overall claim rate: 0.1007 per policy-year. Claim severity: mean €2,279, median €1,172. Log-normal fit: μ = 6.85, σ = 1.133 |
| **Purpose** | Calibrates claim frequency by driver age band (e.g. ages 17–20: 0.274/yr; ages 35–40: 0.089/yr) and the claim severity distribution. Although French in origin, the freMTPL2 dataset is the standard actuarial benchmark for motor frequency-severity modelling and provides a realistic shape for the claim rate curve and severity tail. Absolute UK frequency and severity levels are adjusted through the claim type and amount distributions described in Section 5.5. |

#### 4.1.8 Ministry of Justice — Motoring Convictions

| Attribute | Detail |
|---|---|
| **Publisher** | Ministry of Justice |
| **Edition** | 2024 |
| **URL** | https://www.gov.uk/government/statistics/criminal-justice-system-statistics-quarterly |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | Motoring offences filtered from the Outcomes by Offence dataset, with sex and age breakdowns |
| **Key statistics** | Speeding 29.6%, no insurance 17.6%, identity offences 11.1%, drink/drug driving 6.8%. Males approximately 3.5× more likely to have a conviction. |
| **Purpose** | Informs the overall conviction rate, the distribution of offence types, and the demographic skew (age × gender) of motoring convictions. MoJ offence descriptions are mapped to DVLA endorsement codes (e.g. SP30, IN10, DR10, CU80) used in the schema. |

#### 4.1.9 STATS19 Road Casualty Data

| Attribute | Detail |
|---|---|
| **Publisher** | Department for Transport |
| **Edition** | 2023 |
| **URL** | https://www.data.gov.uk/dataset/road-accidents-safety-data |
| **Licence** | Open Government Licence v3.0 |
| **Purpose** | Provides supplementary accident frequency rates by driver age band, used as a cross-check for the claim frequency curve derived from freMTPL2. |

#### 4.1.10 Index of Multiple Deprivation (IoD2019)

| Attribute | Detail |
|---|---|
| **Publisher** | Ministry of Housing, Communities and Local Government |
| **Edition** | 2019 |
| **URL** | https://www.gov.uk/government/statistics/english-indices-of-deprivation-2019 |
| **Licence** | Open Government Licence v3.0 |
| **Records used** | 32,844 Lower Super Output Areas (LSOAs) in England |
| **Purpose** | Provides LSOA-level deprivation rank and decile. Linked to postcodes via the ONSPD LSOA field. Available as an enrichment dimension; not directly used in the generation sampling but stored in the postcode lookup for downstream modelling use. |

#### 4.1.11 DfT/DVSA Anonymised MOT Testing Data

| Attribute | Detail |
|---|---|
| **Publisher** | Department for Transport / Driver and Vehicle Standards Agency |
| **Edition** | 2024 (July–September extract) |
| **URL** | https://www.data.gov.uk/dataset/anonymised-mot-test |
| **Licence** | Open Government Licence v3.0 |
| **Records processed** | Approximately 14 million MOT test results |
| **Key statistics** | Median annual mileage approximately 7,500 miles; declining with vehicle age |
| **Purpose** | Provides empirical odometer readings by vehicle age, from which cumulative mileage distributions and estimated annual mileage curves are derived. These calibrate the `current_mileage` and `annual_mileage` fields, ensuring realistic correlation between vehicle age and distance travelled. |

---

### 4.2 Summary of Data Source Coverage

| Schema Section | Fields informed by public data | Fields based on assumptions |
|---|---|---|
| **Proposer demographics** | Age, gender, marital status, occupation, first name | Title, secondary occupation, homeownership rate, medical condition rate, criminal conviction rate |
| **Licence** | Licence type proportions, age distribution | Pass age distribution, licence restriction rates |
| **Vehicle** | Make, model, fuel type, fleet composition | Body type inference, engine size distribution, BHP estimate, transmission split, value/depreciation, insurance group estimate, security device rates, modification rates |
| **Policy details** | Annual mileage (MOT), claim frequency by age (freMTPL2) | Cover type split, excess choices, NCD build-up, payment frequency, usage proportions, renewal rate, previous insurer market shares |
| **Claims** | Frequency curve, severity distribution | Claim type split, fault proportions, personal injury rate |
| **Convictions** | Offence distribution, demographic skew | Overall conviction rate, points and fine amounts, ban durations |
| **Address** | Postcode, region, urban/rural, deprivation | Street names, house numbers, years at address |
| **Add-ons** | None | All selection rates and sub-level proportions |
| **Quote metadata** | None | Channel market shares, time-of-day distribution, version rates |

---

## 5. Field-Level Methodology

### 5.1 Postcode and Geography

A postcode is sampled uniformly from 1.79 million live small-user UK postcodes in the ONSPD. Uniform sampling over postcodes approximates population-proportional sampling, as residential postcodes serve roughly similar numbers of addresses. The sampled postcode determines region, urban/rural classification, and IMD decile for downstream conditioning.

### 5.2 Proposer Demographics

**Age and gender** are jointly sampled from the DVLA full licence holder distribution (DRL0101). This reflects the insured driving population rather than the general population, naturally capturing the under-representation of the very young and very old.

**Marital status** is sampled conditionally on age and sex from ONS estimates, with the addition of "living with partner" as a synthetic substitution for 15% of single persons aged 20+.

**Employment status** is drawn from age-banded distributions informed by ONS Labour Force Survey data, with categories including employed, self-employed, retired, student, unemployed, house person, and others.

**Occupation** is sampled at the SOC 2020 4-digit unit group level from the Annual Population Survey, conditioned on sex. Occupation is only assigned to employed and self-employed proposers.

**First names** are drawn from ONS baby name frequency tables by sex. **Surnames** are drawn from a curated list of 200 common UK surnames with frequency-decay weighting.

**Title** is determined conditionally on gender and marital status (e.g. married females: Mrs 70%, Ms 18%, Dr 5%, Miss 7%).

**Homeownership** probability varies by age band, calibrated to ONS housing tenure data (e.g. 8% for ages 17–24, rising to 78% for ages 65+).

**Licence details** are generated with type proportions (92% full UK, 4% EU, 3% provisional, 1% international) and licence duration derived from a stochastic pass age (minimum 17, exponentially distributed delay).

### 5.3 Vehicle

A vehicle is sampled from the VEH0120 make × model × fuel distribution, weighted by licensed fleet counts. This ensures the generated vehicle parc mirrors the real UK fleet composition.

**Vehicle age** is drawn from an exponential distribution conditioned on proposer age (younger drivers tend to drive older, less expensive vehicles).

**Body type** is inferred from model name keywords and make-level heuristics. **Transmission** is assigned probabilistically (55% manual, 42% automatic, 3% semi-automatic), with electric vehicles always automatic. **Engine size** is drawn from realistic displacement distributions by fuel type.

**Estimated value** applies a multiplicative depreciation model (approximately 15% per annum from an estimated new price, which varies by manufacturer tier: budget, mainstream, premium, luxury) with random noise.

**Insurance group** (1–50) is estimated from vehicle value, engine size, and fuel type using a simplified mapping function.

**Security devices** (alarm, immobiliser, tracker) are assigned based on vehicle age, reflecting the increasing prevalence of factory-fitted security in newer vehicles and the correlation between tracker installation and high insurance group.

**Mileage** (`current_mileage`) is drawn from the empirical MOT odometer distribution for the vehicle's age, providing a realistic age-mileage curve.

### 5.4 Policy Details

**Cover type** is sampled with probabilities conditioned on vehicle value (lower-value vehicles are more likely to be insured on third-party terms).

**Annual mileage** is derived from MOT annual mileage estimates for the vehicle's age, with adjustments for commuting (+30%), business use (+20%), and urban location (−10%), then rounded to the nearest 1,000 miles.

**No-claims discount** years are correlated with licence tenure and capped at 20. NCD protection is offered to 35% of those with 4+ years NCD.

**Voluntary excess** is drawn from a discrete distribution (£0/£100/£150/£250/£500/£1,000) with age-dependent weighting (younger proposers are more likely to select higher excess to reduce premiums).

### 5.5 Claims History

Claims over a 5-year lookback period are generated using a Poisson process with age-dependent annual rates derived from freMTPL2:

| Age band | Annual claim rate |
|---|---|
| 17–19 | 0.274 |
| 20–24 | 0.157 |
| 25–29 | 0.115 |
| 30–34 | 0.096 |
| 35–39 | 0.089 |
| 40+ | 0.090–0.102 |

**Claim type** is drawn from an assumed distribution (accident 72%, windscreen 10%, theft 6%, vandalism 4%, storm/flood 3%, personal injury 3%, fire 1%, other 1%).

**Fault status** is conditioned on claim type (e.g. accidents: at-fault 55%, not-at-fault 40%, pending 5%; theft: not-at-fault 98%).

**Claim severity** is drawn from a log-normal distribution with type-specific parameters, calibrated to the freMTPL2 severity distribution and adjusted for UK cost levels.

### 5.6 Convictions

Motoring convictions are generated with an age- and gender-dependent base rate (e.g. males under 25: 12% five-year probability; overall: 5%). The number of convictions follows a geometric-like distribution (85% have one, 12% have two, 3% have three).

**Conviction codes** are sampled from a distribution mapped from MoJ offence categories to DVLA endorsement codes:

| Code | Description | Sampling weight |
|---|---|---|
| SP30 | Exceeding speed limit on public road | 40% |
| IN10 | Using vehicle uninsured | 12% |
| SP50 | Exceeding speed limit on motorway | 10% |
| CU80 | Using handheld mobile phone | 8% |
| DR10 | Driving with excess alcohol | 6% |
| MS90 | Failure to give driver identity | 5% |
| CD10 | Driving without due care | 5% |
| Other | Various | 14% |

Points, fines, and disqualification periods are assigned based on the statutory ranges for each offence code.

### 5.7 Additional Named Drivers

The number of additional drivers is sampled from a distribution conditioned on proposer marital status and age (married/partnered proposers are more likely to add a spouse; proposers aged 40+ may add children).

Each named driver's demographics are generated using the same methodology as the proposer, with constraints imposed by the stated relationship (e.g. a spouse is within ±5 years of the proposer's age and shares the surname with 85% probability; a child is aged 17–25 with a higher probability of holding a provisional licence).

### 5.8 Add-ons

Add-on selection is modelled as independent Bernoulli trials with base rates informed by industry estimates:

| Add-on | Base selection rate |
|---|---|
| Breakdown cover | 25% |
| Legal expenses | 20% |
| Motor legal protection | 15% |
| Key cover | 15% |
| Courtesy car | 12% |
| Windscreen cover | 10% |
| Excess protection | 10% |
| No-claims step-back | 8% |
| Personal accident | 5% |
| Personal belongings | 3% |
| Tools in transit | 1% |

Selection rates are adjusted for relevant covariates (e.g. breakdown cover +50% for vehicles over 8 years old; key cover +50% for vehicles valued over £25,000; excess protection +80% for voluntary excess ≥ £500).

### 5.9 Quote Metadata

**Channel** is drawn from estimated aggregator market shares (Compare the Market 30%, MoneySupermarket 25%, Confused.com 20%, GoCompare 15%, direct channels 10%).

**Timestamp** uses a time-of-day distribution peaking in the evening (18:00–21:00) and a quote date that typically precedes the cover start date by 1–30 days.

### 5.10 Address

The address is derived from the sampled postcode. City is selected from a regional lookup table. Street names are drawn from the 50 most common UK street names. House numbers follow a right-skewed distribution; 20% of addresses use named properties.

Years at address is correlated with proposer age and homeownership (homeowners tend to have longer tenure). A previous address is generated when tenure is less than 3 years, with 70% probability of remaining in the same region.

---

## 6. Correlation Structure

The generator preserves the following key correlations through its conditional generation order:

| Upstream field(s) | Downstream field(s) | Mechanism |
|---|---|---|
| Proposer age | Marital status, employment, homeownership, NCD, licence years, medical conditions, claim rate, conviction rate, vehicle age/value | Conditional sampling from age-banded distributions |
| Proposer gender | Occupation, conviction rate, title, first name | Sex-specific sampling weights |
| Proposer age + gender | Additional driver demographics | Relationship-conditioned generation |
| Postcode (urban/rural) | Overnight location, annual mileage | Conditional distributions |
| Vehicle value | Cover type, insurance group | Value-banded probabilities |
| Vehicle age | Security devices, mileage, breakdown cover rate | Age-banded rates and MOT empirical curves |
| Employment status | Commuting, business use, occupation (null if not employed) | Conditional logic |
| Marital status | Number of additional drivers, relationship types | Household composition heuristics |

---

## 7. Validation

A sample of 1,000 records generated with seed 123 was validated against the source distributions:

| Metric | Generated | Reference | Source |
|---|---|---|---|
| Gender split (% male) | 53.8% | 53.5% | DVLA DRL0101 |
| Mean proposer age | 52 | ~50 | DVLA DRL0101 |
| Comprehensive cover | 84.4% | ~85% | ABI market data |
| Fuel: petrol | 55.1% | 55% | DfT VEH0120 |
| Fuel: diesel | 30.4% | 30% | DfT VEH0120 |
| Top make: Ford | 10.4% | 11.0% | DfT VEH0120 |
| Median annual mileage | 8,000 | ~7,500 | MOT data |
| Renewal rate | 59.6% | ~60% | ABI estimates |
| Proposers with 1+ claims | 39.2% | ~33% (5-yr) | freMTPL2 extrapolation |
| Proposers with 1+ convictions | 6.6% | ~5% | MoJ / assumption |
| Married | 55.1% | 48.8% | ONS (note: driving population skews older) |

The slight over-representation of married proposers relative to the general population is expected, as the sampling frame (DVLA licence holders) skews towards the 30–65 age range where marriage prevalence is highest.

---

## 8. Known Limitations

1. **First names are not cohort-specific.** The generator uses the 2023 top-100 name list for all birth cohorts. A 70-year-old proposer may receive a name more common among recent births. Cohort-specific name data could be incorporated from historical ONS publications.

2. **French claims data used as UK proxy.** The freMTPL2 dataset provides the shape of the frequency-severity curve but originates from French motor insurance. The absolute levels may differ from UK experience. UK-specific calibration using ABI aggregate statistics has been applied where possible.

3. **Vehicle body type is inferred heuristically.** In the absence of a comprehensive make-model-body type lookup, body type is inferred from model name keywords and make-level heuristics. Some misclassifications are possible.

4. **Insurance group estimation is approximate.** True insurance groups are determined by the Thatcham Group Rating Panel and are not publicly available at scale. The generator uses a simplified function of vehicle value, engine size, and fuel type.

5. **Add-on selection rates are assumption-based.** No public dataset exists for add-on take-up rates in UK motor insurance. The rates used are informed by industry commentary and insurer publications but are not empirically calibrated.

6. **Occupation granularity.** While SOC 2020 codes are used, the ABI occupation classification used by insurers differs. A mapping from SOC to ABI occupation codes would improve realism for pricing applications.

7. **No spatial correlation of vehicle choice.** Vehicle make/model sampling is independent of postcode. In practice, vehicle choice varies by region and deprivation (e.g. higher EV penetration in affluent areas).

8. **Single-year snapshot.** All distributions reflect a single point in time. Trends (e.g. growing EV share, changing aggregator market shares) are not modelled dynamically.

---

## 9. Technical Specification

| Parameter | Value |
|---|---|
| **Output format** | JSON Lines (`.jsonl`) or JSON array (`.json`) |
| **Schema** | `schemas/motor_quote.schema.json` (281 fields) |
| **Generation speed** | ~1,150 records/second (single-threaded, post-optimisation) |
| **Record size** | ~4 KB per record (uncompressed JSON) |
| **Random seed** | Configurable for full reproducibility |
| **Dependencies** | Python 3.13, NumPy, Pandas (load-time only), PyArrow |
| **Processed data** | 17 Parquet files derived from 11 public datasets (~60 MB total) |

---

## 10. References

1. Office for National Statistics. *ONS Postcode Directory (ONSPD)*, November 2024. Open Geography Portal.
2. Department for Transport. *Driving Licence Statistics (DRL0101)*, 2024 Q2. GOV.UK.
3. Office for National Statistics. *Population Estimates by Marital Status and Age*, 2022. ONS.
4. Office for National Statistics. *Annual Population Survey — Occupation*, 2023. Nomis.
5. Office for National Statistics. *Baby Names, England and Wales*, 2023. ONS.
6. Department for Transport. *Vehicle Licensing Statistics (VEH0120)*, 2024 Q2. GOV.UK.
7. Charpentier, A. (ed.). *Computational Actuarial Science with R* — freMTPL2 dataset. OpenML dataset 41214.
8. Ministry of Justice. *Criminal Justice System Statistics Quarterly — Outcomes by Offence*, 2024. GOV.UK.
9. Department for Transport. *Reported Road Casualties (STATS19)*, 2023. data.gov.uk.
10. Ministry of Housing, Communities and Local Government. *English Indices of Deprivation 2019*. GOV.UK.
11. Driver and Vehicle Standards Agency. *Anonymised MOT Testing Data*, 2024. data.gov.uk.
12. Association of British Insurers. *UK Motor Insurance: Key Facts*, various years. ABI.
13. Financial Conduct Authority. *General Insurance Distribution Chain*, various years. FCA.

---

*Document prepared for actuarial and technical review. All source data is published under the Open Government Licence v3.0 or equivalent open licence unless otherwise stated.*
