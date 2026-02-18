"""
Core synthetic quote generator.

Generates realistic UK motor insurance comparison site quote requests
by sampling from processed public data distributions with appropriate
correlations between fields.
"""

from datetime import date, datetime, timedelta
import math

import numpy as np

from generator.data_loader import DistributionData
from generator.constants import (
    ADDON_RATES, AGGREGATOR_CHANNELS, AGGREGATOR_PREFIX, BODY_TYPE_DOORS,
    BODY_TYPE_SEATS, BREAKDOWN_LEVELS, CHANNEL_WEIGHTS, CLAIM_AMOUNT_BY_TYPE,
    CLAIM_TYPE_WEIGHTS, CONVICTION_CODE_WEIGHTS, CONVICTION_CODES,
    COVER_TYPE_WEIGHTS, DAYTIME_COMMUTING, DAYTIME_NO_COMMUTING,
    EMPLOYMENT_BY_AGE, FAULT_WEIGHTS_BY_TYPE, HOMEOWNER_RATE_BY_AGE,
    MEDICAL_RATE_BY_AGE, MODIFICATION_TYPES, OVERNIGHT_RURAL, OVERNIGHT_URBAN,
    PAYMENT_FREQ_WEIGHTS, PREVIOUS_INSURERS, REGION_CITIES,
    SECURITY_FACTORY_ALARM_RATE, SECURITY_FACTORY_IMMOBILISER_RATE,
    TITLE_WEIGHTS, UK_STREET_NAMES, UK_SURNAME_WEIGHTS, UK_SURNAMES,
    VOLUNTARY_EXCESS_BASE_WEIGHTS, VOLUNTARY_EXCESS_OPTIONS,
    estimate_insurance_group,
)


class QuoteGenerator:
    """Generate synthetic UK motor insurance quote request JSONs."""

    def __init__(self, seed: int | None = None):
        self.rng = np.random.default_rng(seed)
        self.data = DistributionData()
        self._quote_counter = 0

    # ── Public API ────────────────────────────────────────────────────────

    def generate(self, n: int = 1) -> list[dict]:
        """Generate n quote request dicts."""
        return [self._generate_one() for _ in range(n)]

    def _generate_one(self) -> dict:
        """Generate a single complete quote request."""
        # 1. Sample postcode (determines region, urban/rural, IMD)
        pc = self.data.sample_postcode(self.rng)

        # 2. Generate proposer (age → gender → marital → employment → etc.)
        proposer, proposer_meta = self._gen_proposer(pc)

        # 3. Generate vehicle (conditioned on proposer age + region)
        vehicle = self._gen_vehicle(proposer_meta, pc)

        # 4. Generate policy details (conditioned on proposer + vehicle)
        policy = self._gen_policy(proposer_meta, vehicle, pc)

        # 5. Generate claims & convictions for proposer
        proposer["claims"] = self._gen_claims(proposer_meta)
        proposer["convictions"] = self._gen_convictions(proposer_meta)

        # 6. Additional drivers
        additional_drivers = self._gen_additional_drivers(proposer_meta)

        # 7. Add-ons
        add_ons = self._gen_addons(proposer_meta, vehicle)

        # 8. Quote metadata
        metadata = self._gen_metadata(policy)

        # 9. Address
        address = self._gen_address(pc, proposer_meta)
        prev_address = self._gen_previous_address(pc, address) if address["years_at_address"] < 3 else None

        return {
            "quote_metadata": metadata,
            "policy_details": policy,
            "proposer": proposer,
            "additional_drivers": additional_drivers,
            "vehicle": vehicle,
            "address": address,
            "previous_address": prev_address,
            "add_ons": add_ons,
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _weighted_choice(self, options: dict) -> str:
        """Choose from {option: weight} dict."""
        keys = list(options.keys())
        weights = np.array([options[k] for k in keys], dtype=float)
        weights /= weights.sum()
        return self.rng.choice(keys, p=weights)

    def _age_bracket(self, age: int, brackets: dict):
        """Look up value from {(min, max): value} dict."""
        for (lo, hi), val in brackets.items():
            if lo <= age < hi:
                return val
        # Return last bracket value as fallback
        return list(brackets.values())[-1]

    def _gen_uk_vrm(self, year: int) -> str:
        """Generate a plausible UK VRM for a given registration year."""
        # Post-2001 format: LL NN LLL
        letters = "ABCDEFGHJKLMNOPRSTUVWXYZ"
        area = self.rng.choice(list(letters)) + self.rng.choice(list(letters))

        # Age identifier: Mar-Aug = YY, Sep-Feb = YY+50
        # 2001 Mar = 01/51, 2002 = 02/52, ..., 2024 = 24/74
        base = year - 2000
        if base < 1:
            base = 1
        half = self.rng.choice([0, 50])
        age_id = f"{(base + half) % 100:02d}"

        random_part = "".join(self.rng.choice(list(letters)) for _ in range(3))
        return f"{area}{age_id} {random_part}"

    # ── Proposer ──────────────────────────────────────────────────────────

    def _gen_proposer(self, pc) -> tuple[dict, dict]:
        """Generate proposer fields. Returns (proposer_dict, meta_dict)."""
        age, gender = self.data.sample_age_gender(self.rng)
        # Clamp to driving age
        age = max(17, min(age, 95))
        dob = date.today() - timedelta(days=int(age * 365.25 + self.rng.integers(0, 365)))

        marital = self.data.sample_marital_status(self.rng, age, gender)
        title = self._weighted_choice(
            TITLE_WEIGHTS.get((gender, marital), TITLE_WEIGHTS.get((gender, "single"), {"mr": 1.0}))
        )

        first_name = self.data.sample_first_name(self.rng, gender)
        last_name = self.rng.choice(UK_SURNAMES, p=UK_SURNAME_WEIGHTS)

        # Employment status
        emp_dist = self._age_bracket(age, EMPLOYMENT_BY_AGE)
        employment = self._weighted_choice(emp_dist)

        # Occupation (only if employed/self-employed)
        occupation = None
        occupation_industry = None
        if employment in ("employed", "self_employed"):
            occ_name, soc_code = self.data.sample_occupation(self.rng, gender)
            occupation = occ_name
            # Derive industry from first digit of SOC code
            soc_major = {
                "1": "Management", "2": "Professional", "3": "Technical",
                "4": "Administrative", "5": "Skilled Trades", "6": "Care & Leisure",
                "7": "Sales & Service", "8": "Process & Plant", "9": "Elementary",
            }
            occupation_industry = soc_major.get(soc_code[0], "Other") if soc_code else None

        secondary_occ = None
        if employment in ("employed", "self_employed") and self.rng.random() < 0.05:
            sec_name, _ = self.data.sample_occupation(self.rng, gender)
            secondary_occ = sec_name

        # Licence
        licence_type = "full_uk"
        if age < 20 and self.rng.random() < 0.15:
            licence_type = "provisional_uk"
        elif self.rng.random() < 0.04:
            licence_type = "full_eu"
        elif self.rng.random() < 0.01:
            licence_type = "full_international"

        # Licence held years: age - (17 to 22), but capped
        pass_age = max(17, min(age, 17 + int(self.rng.exponential(2.5))))
        licence_years = max(0, age - pass_age)
        if licence_type == "provisional_uk":
            licence_years = 0
        licence_date = dob + timedelta(days=int(pass_age * 365.25))

        uk_resident = self.rng.random() < (0.92 if age > 40 else 0.85)
        uk_since = None
        if not uk_resident:
            years_in_uk = int(self.rng.uniform(1, min(age - 16, 30)))
            uk_since = (date.today() - timedelta(days=int(years_in_uk * 365.25))).isoformat()
            if self.rng.random() < 0.3:
                licence_type = "full_eu"

        # Medical conditions
        med_rate = self._age_bracket(age, MEDICAL_RATE_BY_AGE)
        has_medical = self.rng.random() < med_rate
        med_details = None
        if has_medical:
            conditions = ["Diabetes - insulin treated", "Epilepsy", "Visual impairment",
                          "Heart condition", "Diabetes - diet controlled", "Sleep apnoea"]
            med_details = self.rng.choice(conditions)

        # Criminal convictions (non-motoring)
        has_criminal = self.rng.random() < (0.03 if gender == "male" and age < 35 else 0.01)

        # Other fields
        is_homeowner = self.rng.random() < self._age_bracket(age, HOMEOWNER_RATE_BY_AGE)
        access_other = self.rng.random() < (0.45 if marital in ("married", "civil_partnership", "living_with_partner") else 0.25)
        is_main_driver = self.rng.random() < 0.95
        rel_main = None
        if not is_main_driver:
            rel_main = self.rng.choice(["parent", "spouse", "other"], p=[0.7, 0.2, 0.1])

        proposer = {
            "title": title,
            "first_name": first_name,
            "last_name": last_name,
            "date_of_birth": dob.isoformat(),
            "gender": gender,
            "marital_status": marital,
            "uk_resident_since_birth": uk_resident,
            "uk_resident_since": uk_since,
            "employment_status": employment,
            "primary_occupation": occupation,
            "primary_occupation_industry": occupation_industry,
            "secondary_occupation": secondary_occ,
            "licence": {
                "licence_type": licence_type,
                "licence_held_years": licence_years,
                "licence_date": licence_date.isoformat() if licence_date <= date.today() else None,
                "additional_licence_restrictions": "automatic only" if self.rng.random() < 0.08 else None,
            },
            "has_medical_conditions": has_medical,
            "medical_condition_details": med_details,
            "has_criminal_convictions": has_criminal,
            "access_to_other_vehicles": access_other,
            "is_homeowner": is_homeowner,
            "is_main_driver": is_main_driver,
            "relationship_to_main_driver": rel_main,
            "claims": [],       # filled later
            "convictions": [],  # filled later
        }

        meta = {"age": age, "gender": gender, "marital": marital,
                "employment": employment, "licence_years": licence_years,
                "is_homeowner": is_homeowner, "dob": dob,
                "last_name": last_name}

        return proposer, meta

    # ── Vehicle ───────────────────────────────────────────────────────────

    def _gen_vehicle(self, proposer_meta: dict, pc) -> dict:
        """Generate vehicle details conditioned on proposer + postcode."""
        veh = self.data.sample_vehicle(self.rng)
        make = veh["make"].title()
        gen_model = veh["gen_model"].title()
        model = veh["model"].title()
        fuel_raw = veh["fuel"].upper()

        # Map fuel to schema enum
        fuel_map = {
            "PETROL": "petrol", "DIESEL": "diesel",
            "BATTERY ELECTRIC": "electric", "ELECTRIC": "electric",
            "HYBRID ELECTRIC (PETROL)": "hybrid_petrol_electric",
            "HYBRID ELECTRIC (DIESEL)": "hybrid_diesel_electric",
            "PLUG-IN HYBRID ELECTRIC (PETROL)": "plug_in_hybrid",
            "PLUG-IN HYBRID ELECTRIC (DIESEL)": "plug_in_hybrid",
            "RANGE EXTENDED ELECTRIC": "electric",
            "GAS": "lpg", "LPG": "lpg",
        }
        fuel = fuel_map.get(fuel_raw, "other")

        # Vehicle age — sample from reasonable distribution conditioned on proposer age
        age = proposer_meta["age"]
        if age < 25:
            # Young drivers: older, cheaper cars (3-12 years)
            veh_age = int(self.rng.exponential(4)) + 3
        elif age < 40:
            veh_age = int(self.rng.exponential(4)) + 1
        elif age < 60:
            veh_age = int(self.rng.exponential(5)) + 1
        else:
            veh_age = int(self.rng.exponential(4)) + 2

        veh_age = max(0, min(veh_age, 25))
        year_mfg = date.today().year - veh_age
        year_reg = year_mfg if self.rng.random() < 0.90 else year_mfg + 1

        # Body type inference (simplified)
        body_keywords = {
            "hatchback": ["hatchback", "hatch"],
            "saloon": ["saloon", "sedan"],
            "estate": ["estate", "touring", "wagon", "avant"],
            "suv": ["suv", "crossover", "x-drive", "4x4", "sport utility"],
            "convertible": ["convertible", "cabriolet", "roadster", "spider", "spyder"],
            "coupe": ["coupe", "coupé"],
            "mpv": ["mpv", "people carrier", "scenic", "touran", "zafira"],
            "pickup": ["pickup", "pick-up", "ranger", "hilux", "navara"],
        }
        body_type = "hatchback"  # default
        model_lower = model.lower()
        for bt, keywords in body_keywords.items():
            if any(kw in model_lower for kw in keywords):
                body_type = bt
                break
        # Heuristic: if make is Land Rover / Range Rover → SUV
        if make.upper() in ("LAND ROVER", "RANGE ROVER", "JEEP"):
            body_type = "suv"

        # Transmission
        if fuel == "electric":
            transmission = "automatic"
        elif self.rng.random() < 0.45:
            transmission = "automatic"
        elif self.rng.random() < 0.05:
            transmission = "semi_automatic"
        else:
            transmission = "manual"

        # Engine size
        engine_cc = None
        if fuel not in ("electric",):
            if fuel == "petrol":
                engine_cc = int(self.rng.choice([999, 1000, 1199, 1242, 1332, 1498, 1596, 1798, 1984, 2000, 2500, 2979, 3000],
                                                 p=[0.08, 0.12, 0.10, 0.10, 0.10, 0.15, 0.10, 0.08, 0.07, 0.04, 0.03, 0.02, 0.01]))
            elif fuel == "diesel":
                engine_cc = int(self.rng.choice([1461, 1499, 1598, 1956, 1968, 1997, 2000, 2143, 2993, 3000],
                                                 p=[0.10, 0.10, 0.12, 0.15, 0.15, 0.12, 0.08, 0.08, 0.05, 0.05]))
            else:
                engine_cc = int(self.rng.choice([1498, 1798, 1984, 2000], p=[0.30, 0.30, 0.20, 0.20]))

        # BHP estimate
        bhp = None
        if engine_cc:
            bhp = int(engine_cc * (0.06 + self.rng.normal(0, 0.01)) + self.rng.normal(0, 10))
            bhp = max(60, min(bhp, 500))
        elif fuel == "electric":
            bhp = int(self.rng.choice([136, 150, 170, 204, 245, 300, 408],
                                       p=[0.15, 0.15, 0.15, 0.20, 0.15, 0.10, 0.10]))

        # Doors and seats
        doors = self._weighted_choice(BODY_TYPE_DOORS.get(body_type, {5: 1.0}))
        seats = self._weighted_choice(BODY_TYPE_SEATS.get(body_type, {5: 1.0}))

        # Estimated value (depreciation curve)
        # Rough new prices by make tier
        tier_prices = {
            "budget": 15000, "mainstream": 25000, "premium": 40000, "luxury": 65000,
        }
        premium_makes = {"BMW", "AUDI", "MERCEDES", "MERCEDES-BENZ", "VOLVO", "LEXUS", "TESLA", "MINI"}
        luxury_makes = {"PORSCHE", "JAGUAR", "LAND ROVER", "RANGE ROVER", "BENTLEY", "ROLLS-ROYCE", "MASERATI", "ASTON MARTIN", "FERRARI", "LAMBORGHINI", "MCLAREN"}
        budget_makes = {"DACIA", "MG", "SUZUKI", "FIAT"}

        make_upper = make.upper()
        if make_upper in luxury_makes:
            new_price = tier_prices["luxury"]
        elif make_upper in premium_makes:
            new_price = tier_prices["premium"]
        elif make_upper in budget_makes:
            new_price = tier_prices["budget"]
        else:
            new_price = tier_prices["mainstream"]

        # Add noise to new price
        new_price = int(new_price * (1 + self.rng.normal(0, 0.15)))
        # Depreciation: ~15% per year, floor at 500
        value = max(500, int(new_price * (0.85 ** veh_age) * (1 + self.rng.normal(0, 0.1))))

        # Insurance group
        ins_group = estimate_insurance_group(value, engine_cc, fuel)

        # Registration number
        reg = self._gen_uk_vrm(year_reg)

        # Security
        alarm_rate = self._age_bracket(veh_age, SECURITY_FACTORY_ALARM_RATE)
        immo_rate = self._age_bracket(veh_age, SECURITY_FACTORY_IMMOBILISER_RATE)

        if self.rng.random() < alarm_rate:
            alarm = "factory_fitted"
        elif self.rng.random() < 0.15:
            alarm = "aftermarket_thatcham"
        else:
            alarm = "none"

        if self.rng.random() < immo_rate:
            immobiliser = "factory_fitted"
        elif self.rng.random() < 0.10:
            immobiliser = "aftermarket_thatcham"
        else:
            immobiliser = "none"

        tracker_rate = 0.15 if ins_group >= 35 else (0.08 if ins_group >= 25 else 0.03)
        if self.rng.random() < tracker_rate:
            tracker = self.rng.choice(["thatcham_cat_5", "thatcham_cat_6", "thatcham_s5"])
        else:
            tracker = "none"

        # Overnight location
        is_urban = bool(pc.get("is_urban", True))
        overnight = self._weighted_choice(OVERNIGHT_URBAN if is_urban else OVERNIGHT_RURAL)

        # Daytime location
        commuting = proposer_meta["employment"] in ("employed", "self_employed")
        daytime = self._weighted_choice(DAYTIME_COMMUTING if commuting else DAYTIME_NO_COMMUTING)

        # Modifications
        mod_rate = 0.12 if proposer_meta["age"] < 30 and proposer_meta["gender"] == "male" else 0.04
        has_mods = self.rng.random() < mod_rate
        modifications = []
        if has_mods:
            n_mods = min(int(self.rng.exponential(1.5)) + 1, 4)
            mod_types = list(MODIFICATION_TYPES.keys())
            mod_weights = np.array(list(MODIFICATION_TYPES.values()))
            mod_weights /= mod_weights.sum()
            chosen = self.rng.choice(mod_types, size=n_mods, replace=False, p=mod_weights)
            modifications = [{"modification_type": m, "details": None} for m in chosen]

        # Owner / keeper
        owner = "proposer" if self.rng.random() < 0.90 else self.rng.choice(
            ["spouse_partner", "parent", "company", "leasing_company"], p=[0.35, 0.30, 0.20, 0.15])
        keeper = owner if self.rng.random() < 0.92 else "proposer"

        # Purchase date
        purchase_years_ago = min(veh_age, max(0, int(self.rng.exponential(3))))
        purchase_date = (date.today() - timedelta(days=int(purchase_years_ago * 365.25 + self.rng.integers(0, 365)))).isoformat()

        # Current mileage from MOT data
        med_mi, std_mi = self.data.get_mileage_stats(max(3, veh_age))
        current_mileage = int(max(100, self.rng.normal(med_mi, std_mi * 0.5)))

        return {
            "registration_number": reg,
            "make": make,
            "model": gen_model,
            "variant": model if model != gen_model else None,
            "year_of_manufacture": year_mfg,
            "year_of_registration": year_reg,
            "body_type": body_type,
            "fuel_type": fuel,
            "transmission": transmission,
            "engine_size_cc": engine_cc,
            "engine_power_bhp": bhp,
            "number_of_doors": int(doors),
            "number_of_seats": int(seats),
            "insurance_group": ins_group,
            "abi_code": None,
            "estimated_value": value,
            "is_imported": self.rng.random() < 0.02,
            "is_right_hand_drive": True if self.rng.random() < 0.98 else False,
            "has_been_modified": has_mods,
            "modifications": modifications,
            "security": {
                "alarm": alarm,
                "immobiliser": immobiliser,
                "tracker": tracker,
            },
            "overnight_location": overnight,
            "daytime_location": daytime,
            "owner": owner,
            "registered_keeper": keeper,
            "purchase_date": purchase_date,
            "current_mileage": current_mileage,
        }

    # ── Policy Details ────────────────────────────────────────────────────

    def _gen_policy(self, proposer_meta: dict, vehicle: dict, pc) -> dict:
        """Generate policy details."""
        age = proposer_meta["age"]

        # Cover start: 1-30 days from now, with some variance
        days_ahead = int(self.rng.exponential(10)) + 1
        days_ahead = max(1, min(days_ahead, 60))
        cover_start = date.today() + timedelta(days=days_ahead)
        cover_end = cover_start + timedelta(days=365)

        # Cover type — lower value vehicles more likely TPFT/TPO
        value = vehicle["estimated_value"]
        if value < 3000:
            ct_weights = {"comprehensive": 0.60, "third_party_fire_and_theft": 0.25, "third_party_only": 0.15}
        elif value < 8000:
            ct_weights = {"comprehensive": 0.80, "third_party_fire_and_theft": 0.13, "third_party_only": 0.07}
        else:
            ct_weights = COVER_TYPE_WEIGHTS
        cover_type = self._weighted_choice(ct_weights)

        # Payment frequency — younger → more monthly
        if age < 30:
            pf_weights = {"annual": 0.30, "monthly": 0.70}
        elif age > 60:
            pf_weights = {"annual": 0.55, "monthly": 0.45}
        else:
            pf_weights = PAYMENT_FREQ_WEIGHTS
        payment = self._weighted_choice(pf_weights)

        # Voluntary excess — younger → higher to reduce premium
        ve_weights = list(VOLUNTARY_EXCESS_BASE_WEIGHTS)
        if age < 25:
            ve_weights[4] *= 2.0  # £500
            ve_weights[5] *= 2.0  # £1000
        elif age > 60:
            ve_weights[0] *= 2.0  # £0
            ve_weights[1] *= 1.5  # £100
        ve_weights = np.array(ve_weights)
        ve_weights /= ve_weights.sum()
        vol_excess = int(self.rng.choice(VOLUNTARY_EXCESS_OPTIONS, p=ve_weights))

        # Compulsory excess
        if age < 21:
            comp_excess = 400
        elif age < 25:
            comp_excess = 300
        elif age > 65:
            comp_excess = 100
        else:
            comp_excess = self.rng.choice([100, 150, 200, 250], p=[0.10, 0.40, 0.30, 0.20])

        # NCD years (correlated with licence years)
        max_ncd = min(proposer_meta["licence_years"], 20)
        # Most drivers build NCD steadily; at-fault claims reset it (handled in claims)
        ncd = max(0, min(int(max_ncd * self.rng.uniform(0.5, 1.0)), 20))

        ncd_protected = False
        if ncd >= 4:
            ncd_protected = self.rng.random() < 0.35

        # Usage
        commuting = proposer_meta["employment"] in ("employed", "self_employed") and self.rng.random() < 0.70
        business = None
        if proposer_meta["employment"] == "self_employed" and self.rng.random() < 0.25:
            business = "class_1"
        elif commuting and self.rng.random() < 0.05:
            business = "class_1"

        # Annual mileage from MOT data + adjustments
        veh_age = max(3, date.today().year - vehicle["year_of_manufacture"])
        med_am, std_am = self.data.get_annual_mileage_stats(min(veh_age, 30))
        annual_mileage = int(max(1000, self.rng.normal(med_am, std_am * 0.3)))
        # Adjust for commuting
        if commuting:
            annual_mileage = int(annual_mileage * 1.3)
        if business:
            annual_mileage = int(annual_mileage * 1.2)
        # Urban → slightly less
        is_urban = pc.get("is_urban", True)
        if is_urban:
            annual_mileage = int(annual_mileage * 0.9)
        # Round to nearest 1000
        annual_mileage = max(1000, round(annual_mileage / 1000) * 1000)

        business_mileage = None
        if business:
            business_mileage = int(annual_mileage * self.rng.uniform(0.15, 0.40))
            business_mileage = round(business_mileage / 500) * 500

        # Renewal
        is_renewal = self.rng.random() < 0.60
        prev_insurer = None
        if is_renewal:
            prev_insurer = self._weighted_choice(PREVIOUS_INSURERS)

        return {
            "policy_number": None,
            "cover_start_date": cover_start.isoformat(),
            "cover_end_date": cover_end.isoformat(),
            "cover_type": cover_type,
            "payment_frequency": payment,
            "voluntary_excess": vol_excess,
            "compulsory_excess": int(comp_excess),
            "ncd_years": ncd,
            "ncd_protected": ncd_protected,
            "usage": {
                "social_domestic_pleasure": True,
                "commuting": commuting,
                "business_use": business,
            },
            "annual_mileage": annual_mileage,
            "business_mileage": business_mileage,
            "is_renewal": is_renewal,
            "previous_insurer": prev_insurer,
        }

    # ── Claims ────────────────────────────────────────────────────────────

    def _gen_claims(self, proposer_meta: dict) -> list[dict]:
        """Generate 5-year claims history."""
        age = proposer_meta["age"]
        annual_rate = self.data.get_claim_rate(age)

        # 5-year expected claims
        expected_5yr = annual_rate * 5
        n_claims = self.rng.poisson(expected_5yr)
        n_claims = min(n_claims, 5)  # cap at 5

        claims = []
        for _ in range(n_claims):
            days_ago = self.rng.integers(30, 5 * 365)
            claim_date = (date.today() - timedelta(days=int(days_ago))).isoformat()

            claim_type = self._weighted_choice(CLAIM_TYPE_WEIGHTS)
            fault = self._weighted_choice(FAULT_WEIGHTS_BY_TYPE.get(claim_type, {"at_fault": 0.5, "not_at_fault": 0.5}))

            # Status
            status = "settled" if days_ago > 180 else ("settled" if self.rng.random() < 0.80 else "open")

            # Amount
            mean_amt, log_sigma = CLAIM_AMOUNT_BY_TYPE.get(claim_type, (2000, 1.0))
            amount = max(0, float(np.round(self.rng.lognormal(math.log(mean_amt), log_sigma), 2)))
            if status == "open" and self.rng.random() < 0.3:
                amount = 0  # notification only

            pi_claimed = claim_type == "accident" and self.rng.random() < 0.15
            ncd_affected = fault == "at_fault"

            claims.append({
                "claim_date": claim_date,
                "claim_type": claim_type,
                "fault": fault,
                "status": status,
                "amount_paid": round(amount, 2),
                "personal_injury_claimed": pi_claimed,
                "ncd_affected": ncd_affected,
            })

        return claims

    # ── Convictions ───────────────────────────────────────────────────────

    def _gen_convictions(self, proposer_meta: dict) -> list[dict]:
        """Generate 5-year motoring convictions."""
        age = proposer_meta["age"]
        gender = proposer_meta["gender"]

        # Base rate ~5% any conviction in 5 years; higher for young males
        base_rate = 0.05
        if age < 25 and gender == "male":
            base_rate = 0.12
        elif age < 25:
            base_rate = 0.07
        elif age < 35 and gender == "male":
            base_rate = 0.08

        has_any = self.rng.random() < base_rate
        if not has_any:
            return []

        n_conv = 1 if self.rng.random() < 0.85 else (2 if self.rng.random() < 0.80 else 3)
        convictions = []
        for _ in range(n_conv):
            code = self._weighted_choice(CONVICTION_CODE_WEIGHTS)
            info = CONVICTION_CODES[code]
            desc, min_pts, max_pts, typical_fine, ban_range = info

            days_ago = self.rng.integers(30, 5 * 365)
            conv_date = (date.today() - timedelta(days=int(days_ago))).isoformat()

            points = self.rng.integers(min_pts, max_pts + 1)
            fine = int(typical_fine * (1 + self.rng.normal(0, 0.3)))
            fine = max(50, fine)

            ban_months = None
            is_disqualified = False
            if ban_range:
                ban_months = self.rng.integers(ban_range[0], ban_range[1] + 1)
                is_disqualified = True

            convictions.append({
                "conviction_code": code,
                "conviction_date": conv_date,
                "points": int(points),
                "fine_amount": fine,
                "ban_months": ban_months,
                "is_disqualified": is_disqualified,
            })

        return convictions

    # ── Additional Drivers ────────────────────────────────────────────────

    def _gen_additional_drivers(self, proposer_meta: dict) -> list[dict]:
        """Generate additional named drivers."""
        age = proposer_meta["age"]
        marital = proposer_meta["marital"]

        # Number of additional drivers
        if marital in ("married", "civil_partnership", "living_with_partner"):
            n_weights = [0.25, 0.45, 0.20, 0.08, 0.02]
        elif age >= 40:
            n_weights = [0.35, 0.35, 0.20, 0.08, 0.02]
        else:
            n_weights = [0.55, 0.30, 0.10, 0.04, 0.01]

        n_drivers = self.rng.choice([0, 1, 2, 3, 4], p=n_weights)
        drivers = []

        for i in range(n_drivers):
            # Relationship determines demographics
            if i == 0 and marital in ("married", "civil_partnership", "living_with_partner"):
                rel = "spouse" if marital == "married" else ("common_law_partner" if marital == "living_with_partner" else "partner")
                d_age = age + self.rng.integers(-5, 6)
                d_gender = "female" if proposer_meta["gender"] == "male" else "male"
                d_marital = marital
            elif age >= 40 and self.rng.random() < 0.40:
                rel = "child"
                d_age = max(17, age - self.rng.integers(18, 28))
                d_gender = self.rng.choice(["male", "female"])
                d_marital = "single"
            elif self.rng.random() < 0.15:
                rel = "parent"
                d_age = min(90, age + self.rng.integers(20, 35))
                d_gender = self.rng.choice(["male", "female"])
                d_marital = self.rng.choice(["married", "widowed"], p=[0.6, 0.4])
            else:
                rel = self.rng.choice(["sibling", "other_family", "other"], p=[0.5, 0.3, 0.2])
                d_age = max(17, age + self.rng.integers(-10, 11))
                d_gender = self.rng.choice(["male", "female"])
                d_marital = self.data.sample_marital_status(self.rng, d_age, d_gender)

            d_age = max(17, min(d_age, 90))
            d_dob = date.today() - timedelta(days=int(d_age * 365.25 + self.rng.integers(0, 365)))

            d_title = self._weighted_choice(
                TITLE_WEIGHTS.get((d_gender, d_marital), TITLE_WEIGHTS.get((d_gender, "single"), {"mr": 1.0}))
            )
            d_first = self.data.sample_first_name(self.rng, d_gender)
            # Spouse/family: often same surname
            if rel in ("spouse", "partner", "common_law_partner", "child"):
                d_last = proposer_meta["last_name"] if self.rng.random() < 0.85 else self.rng.choice(UK_SURNAMES, p=UK_SURNAME_WEIGHTS)
            else:
                d_last = self.rng.choice(UK_SURNAMES, p=UK_SURNAME_WEIGHTS)

            # Employment
            d_emp = self._weighted_choice(self._age_bracket(d_age, EMPLOYMENT_BY_AGE))
            d_occ, d_occ_ind = None, None
            if d_emp in ("employed", "self_employed"):
                occ_name, soc_code = self.data.sample_occupation(self.rng, d_gender)
                d_occ = occ_name
                soc_major = {"1": "Management", "2": "Professional", "3": "Technical",
                             "4": "Administrative", "5": "Skilled Trades", "6": "Care & Leisure",
                             "7": "Sales & Service", "8": "Process & Plant", "9": "Elementary"}
                d_occ_ind = soc_major.get(soc_code[0], "Other") if soc_code else None

            # Licence
            d_pass_age = max(17, min(d_age, 17 + int(self.rng.exponential(2.5))))
            d_lic_years = max(0, d_age - d_pass_age)
            d_lic_type = "full_uk" if self.rng.random() < 0.92 else "provisional_uk"
            if rel == "child" and d_age < 20:
                d_lic_type = "provisional_uk" if self.rng.random() < 0.30 else "full_uk"
            d_lic_date = d_dob + timedelta(days=int(d_pass_age * 365.25))

            d_uk_resident = self.rng.random() < 0.88
            d_uk_since = None
            if not d_uk_resident:
                yrs = int(self.rng.uniform(1, min(d_age - 16, 25)))
                d_uk_since = (date.today() - timedelta(days=int(yrs * 365.25))).isoformat()

            d_med_rate = self._age_bracket(d_age, MEDICAL_RATE_BY_AGE)

            driver = {
                "title": d_title,
                "first_name": d_first,
                "last_name": d_last,
                "date_of_birth": d_dob.isoformat(),
                "gender": d_gender,
                "marital_status": d_marital,
                "relationship_to_proposer": rel,
                "employment_status": d_emp,
                "primary_occupation": d_occ,
                "primary_occupation_industry": d_occ_ind,
                "licence": {
                    "licence_type": d_lic_type,
                    "licence_held_years": d_lic_years,
                    "licence_date": d_lic_date.isoformat() if d_lic_date <= date.today() else None,
                    "additional_licence_restrictions": "automatic only" if self.rng.random() < 0.06 else None,
                },
                "has_medical_conditions": self.rng.random() < d_med_rate,
                "has_criminal_convictions": self.rng.random() < 0.015,
                "access_to_other_vehicles": self.rng.random() < 0.30,
                "uk_resident_since_birth": d_uk_resident,
                "uk_resident_since": d_uk_since,
                "claims": self._gen_claims({"age": d_age, "gender": d_gender}),
                "convictions": self._gen_convictions({"age": d_age, "gender": d_gender}),
            }
            drivers.append(driver)

        return drivers

    # ── Add-ons ───────────────────────────────────────────────────────────

    def _gen_addons(self, proposer_meta: dict, vehicle: dict) -> dict:
        """Generate add-on selections."""
        addons = {}
        veh_age = date.today().year - vehicle["year_of_manufacture"]

        for addon, base_rate in ADDON_RATES.items():
            rate = base_rate
            # Adjustments
            if addon == "breakdown_cover" and veh_age > 8:
                rate *= 1.5
            elif addon == "excess_protection" and vehicle.get("voluntary_excess", 0) >= 500:
                rate *= 1.8
            elif addon == "key_cover" and vehicle["estimated_value"] > 25000:
                rate *= 1.5

            selected = self.rng.random() < min(rate, 0.95)
            level = None
            if selected and addon == "breakdown_cover":
                level = self._weighted_choice(BREAKDOWN_LEVELS)
            elif selected and addon == "no_claims_step_back":
                level = None  # no sub-level

            addons[addon] = {"selected": selected, "level": level}

        return addons

    # ── Quote Metadata ────────────────────────────────────────────────────

    def _gen_metadata(self, policy: dict) -> dict:
        """Generate quote metadata."""
        self._quote_counter += 1
        quote_id = f"QUO-{date.today().year}-{self._quote_counter:09d}"

        # Timestamp: evening peak (18-21), some during day
        hour = self.rng.choice(range(24), p=self._hour_weights())
        minute = self.rng.integers(0, 60)
        second = self.rng.integers(0, 60)

        # Date: same day or slightly before cover start
        cover_start = date.fromisoformat(policy["cover_start_date"])
        days_before = int(self.rng.exponential(7))
        quote_date = cover_start - timedelta(days=days_before)
        if quote_date > date.today():
            quote_date = date.today()

        ts = datetime(quote_date.year, quote_date.month, quote_date.day,
                      hour, minute, second)

        channel = self._weighted_choice(CHANNEL_WEIGHTS)
        agg_id = None
        if channel in AGGREGATOR_CHANNELS:
            prefix = AGGREGATOR_PREFIX[channel]
            agg_id = f"{prefix}-{self.rng.integers(1000000000, 9999999999)}"

        version = 1
        if self.rng.random() < 0.08:
            version = 2
        elif self.rng.random() < 0.02:
            version = 3

        return {
            "quote_id": quote_id,
            "quote_version": version,
            "quote_timestamp": ts.isoformat() + "Z",
            "channel": channel,
            "aggregator_quote_id": agg_id,
        }

    @staticmethod
    def _hour_weights():
        """Time-of-day weights for quote generation (evening peak)."""
        w = [0.5, 0.2, 0.1, 0.1, 0.1, 0.1,     # 00-05
             0.3, 0.8, 1.5, 2.5, 3.0, 3.0,       # 06-11
             3.0, 3.0, 2.5, 2.5, 3.0, 3.5,        # 12-17
             5.0, 6.0, 6.0, 5.5, 4.0, 2.0]        # 18-23
        total = sum(w)
        return [x / total for x in w]

    # ── Address ───────────────────────────────────────────────────────────

    def _gen_address(self, pc, proposer_meta: dict) -> dict:
        """Generate address from sampled postcode."""
        postcode = str(pc["pcd"]).strip()
        # Ensure postcode has correct space (outcode + space + incode)
        if " " not in postcode and len(postcode) >= 5:
            postcode = postcode[:-3] + " " + postcode[-3:]
        region = pc.get("rgn_name", "Unknown")
        cities = REGION_CITIES.get(region, ["Town"])
        city = self.rng.choice(cities)

        street = self.rng.choice(UK_STREET_NAMES)

        # House number (80% numeric, 20% named)
        if self.rng.random() < 0.80:
            house = str(int(self.rng.exponential(30)) + 1)
        else:
            house_names = ["The Willows", "Rose Cottage", "Oaklands", "The Old Rectory",
                           "Hillcrest", "Fairview", "Sunnyside", "The Croft", "Greenacres",
                           "Woodlands", "The Grange", "Ivy House", "Cherry Tree House",
                           "Meadow View", "The Laurels"]
            house = self.rng.choice(house_names)

        # Years at address — correlated with age and homeownership
        age = proposer_meta["age"]
        if proposer_meta["is_homeowner"]:
            years_at = min(age - 17, max(0, int(self.rng.exponential(8))))
        else:
            years_at = max(0, int(self.rng.exponential(3)))
        months_at = self.rng.integers(0, 12) if years_at < 30 else 0

        return {
            "house_number_or_name": house,
            "address_line_1": street,
            "address_line_2": None,
            "city": city,
            "county": None,
            "postcode": postcode,
            "years_at_address": years_at,
            "months_at_address": int(months_at),
        }

    def _gen_previous_address(self, pc, current_address: dict) -> dict:
        """Generate previous address (if at current address < 3 years)."""
        # 70% same region, 30% different
        if self.rng.random() < 0.70:
            region = pc.get("rgn_name", "Unknown")
            prev_pc = self.data.sample_postcode_in_region(self.rng, region)
        else:
            prev_pc = self.data.sample_postcode(self.rng)

        region = prev_pc.get("rgn_name", "Unknown")
        cities = REGION_CITIES.get(region, ["Town"])
        city = self.rng.choice(cities)
        street = self.rng.choice(UK_STREET_NAMES)

        if self.rng.random() < 0.80:
            house = str(int(self.rng.exponential(30)) + 1)
        else:
            house = self.rng.choice(["Flat " + str(self.rng.integers(1, 20)),
                                     str(self.rng.integers(1, 100))])

        return {
            "house_number_or_name": house,
            "address_line_1": street,
            "address_line_2": None,
            "city": city,
            "county": None,
            "postcode": prev_pc["pcd"],
        }
