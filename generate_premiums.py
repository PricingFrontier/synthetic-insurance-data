"""
Generate competitor premium panels from existing quote JSONL files.

Simulates pricing from 20 different insurers, each with a distinct target
market, rating strategy, and acceptance criteria.  Every quote receives up to
20 premiums, producing a realistic aggregator comparison panel.

Five rating strategies are modelled:
    GLM_MULTIPLICATIVE  - classic base * factor1 * factor2 * ...  (12 insurers)
    RULES_TIERED        - risk-tier lookup then fewer adjustments  (3 insurers)
    TELEMATICS_DISCOUNT - GLM base + tracker/mileage post-adjust  (2 insurers)
    AFFINITY_SCHEME     - occupation-first with flatter other factors (2 insurers)
    SPECIALIST_NICHE    - narrow acceptance, interaction-heavy     (1 insurer)

Output is a wide Parquet file: one row per quote, one column per insurer
(annual premium), with NaN for declined quotes.

Usage:
    uv run python generate_premiums.py --input data/output/quotes/quotes_1k.jsonl --seed 42 --output data/output/competitor_premiums/premiums_1k.parquet
    uv run python generate_premiums.py --input data/output/quotes/quotes_10m.jsonl --seed 42 --workers 20 --output data/output/competitor_premiums/premiums_10m.parquet
"""

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


# ─────────────────────────────────────────────────────────────────────────────
# Interaction system
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Interaction:
    """Multi-condition rating rule.

    conditions: {field_name: (operator, value)}
        Operators: "lt", "gt", "lte", "gte", "eq", "ne", "in", "not_in"
    effect: "decline", "multiply", or "add"
    value: float (multiplier or additive amount; ignored for decline)
    """
    conditions: dict
    effect: str
    value: float = 0.0


def _eval_condition(op: str, actual, threshold) -> bool:
    """Evaluate a single condition."""
    if actual is None:
        return False
    if op == "lt":
        return actual < threshold
    if op == "gt":
        return actual > threshold
    if op == "lte":
        return actual <= threshold
    if op == "gte":
        return actual >= threshold
    if op == "eq":
        return actual == threshold
    if op == "ne":
        return actual != threshold
    if op == "in":
        return actual in threshold
    if op == "not_in":
        return actual not in threshold
    return False


def _eval_interaction(interaction: Interaction, fields: dict) -> bool:
    """Return True if all conditions of an interaction are met."""
    for field_name, (op, threshold) in interaction.conditions.items():
        actual = fields.get(field_name)
        if not _eval_condition(op, actual, threshold):
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Insurer profile
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InsurerProfile:
    name: str
    strategy: str = "glm"  # "glm", "tiered", "telematics", "affinity", "specialist"

    # Base
    base_rate: float = 1000.0
    noise_sigma: float = 0.06

    # Minimum premiums by cover level
    minimum_premiums: dict = field(default_factory=lambda: {
        "comprehensive": 200, "third_party_fire_and_theft": 180, "third_party_only": 150,
    })
    min_premium_young: float = 0       # override for age < 25 (0 = use cover-level default)
    min_premium_business: float = 0    # override for business use

    # Age
    age_curve: str = "standard"   # "standard", "young_friendly", "mature_specialist", "flat_middle"
    young_driver_loading: float = 1.0
    senior_discount: float = 1.0

    # Gender
    female_discount: float = 1.0

    # Marital
    married_discount: float = 1.0

    # Employment & Occupation
    employment_factors: dict = field(default_factory=dict)
    occupation_group_factors: dict = field(default_factory=dict)

    # Licence
    licence_type_factors: dict = field(default_factory=dict)
    licence_years_power: float = 0.5

    # NCD
    ncd_max_discount: float = 0.60
    ncd_protected_bonus: float = 0.0

    # New business / loyalty
    new_business_discount: float = 1.0
    loyalty_discount: float = 1.0

    # Vehicle group
    vehicle_group_power: float = 1.0
    vehicle_group_cap: float = 3.5

    # Vehicle make preferences
    preferred_makes: dict = field(default_factory=dict)

    # Vehicle body type
    body_type_factors: dict = field(default_factory=dict)

    # Fuel
    ev_discount: float = 1.0
    fuel_factors: dict = field(default_factory=dict)

    # Vehicle age
    old_vehicle_loading: float = 1.0
    new_vehicle_discount: float = 1.0
    classic_vehicle_factor: float = 1.0  # for 15+ years

    # Engine / performance
    engine_cc_factors: list = field(default_factory=list)  # [(threshold_cc, factor), ...]

    # Modifications
    modification_loading: float = 1.0

    # Security
    tracker_discount: float = 1.0
    alarm_discount: float = 1.0
    no_security_loading: float = 1.0

    # Overnight location
    overnight_factors: dict = field(default_factory=dict)

    # Mileage
    mileage_power: float = 0.5

    # Cover type
    tpft_discount: float = 0.75
    tpo_discount: float = 0.60

    # Excess
    excess_credit_rate: float = 1.0

    # Area
    urban_loading: float = 1.0
    regional_factors: dict = field(default_factory=dict)  # postcode area -> factor

    # Claims history
    at_fault_loading: float = 0.15
    not_at_fault_loading: float = 0.0
    claim_recency_weight: float = 0.0
    pi_claim_loading: float = 0.0

    # Convictions
    conviction_loading: float = 0.10
    speeding_loading: float = 0.0

    # Additional drivers
    additional_driver_loading: float = 0.0
    young_named_driver_loading: float = 0.0

    # Proposer attributes
    homeowner_discount: float = 1.0
    uk_residency_loading: float = 1.0

    # Business use
    business_use_loading: float = 1.0
    commuting_loading: float = 1.0

    # Decline rules
    min_age: int = 17
    max_age: int = 99
    max_insurance_group: int = 50
    decline_provisional: bool = False
    decline_modified: bool = False
    decline_imported: bool = False
    decline_postcodes: set = field(default_factory=set)
    decline_max_claims: int = 99
    decline_max_convictions: int = 99
    decline_max_points: int = 99
    max_vehicle_age: int = 99
    min_vehicle_value: int = 0
    max_vehicle_value: int = 999999
    decline_business_use: bool = False
    decline_drink_drive: bool = False
    decline_major_conviction: bool = False

    # Interactions (multi-condition rules)
    interactions: list = field(default_factory=list)

    # Tiered strategy fields
    # Each rule: (field, operator, value, tier_label)
    tier_rules: list = field(default_factory=list)
    tier_base_rates: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# 20 insurer profiles
# ─────────────────────────────────────────────────────────────────────────────

INSURERS = [
    # 1. BritSure Direct (GLM) - Big direct, volume player
    InsurerProfile(
        name="BritSure Direct",
        strategy="glm",
        base_rate=1020,
        noise_sigma=0.06,
        young_driver_loading=1.05,
        ncd_max_discount=0.64,
        loyalty_discount=0.93,
        urban_loading=1.10,
        married_discount=0.96,
        homeowner_discount=0.96,
        minimum_premiums={
            "comprehensive": 250,
            "third_party_fire_and_theft": 200,
            "third_party_only": 180,
        },
    ),

    # 2. FirstMile (TELEMATICS) - Young driver telematics specialist
    InsurerProfile(
        name="FirstMile",
        strategy="telematics",
        base_rate=1350,
        noise_sigma=0.07,
        young_driver_loading=0.65,
        senior_discount=1.30,
        female_discount=0.93,
        tracker_discount=0.75,
        no_security_loading=1.15,
        ncd_max_discount=0.52,
        new_business_discount=0.90,
        max_insurance_group=45,
        max_age=70,
        minimum_premiums={
            "comprehensive": 320,
            "third_party_fire_and_theft": 280,
            "third_party_only": 250,
        },
        min_premium_young=350,
        interactions=[
            Interaction(
                conditions={"age": ("lt", 21), "insurance_group": ("gte", 35)},
                effect="decline",
            ),
            Interaction(
                conditions={"age": ("lt", 25), "has_tracker": ("eq", True), "annual_mileage": ("lt", 6000)},
                effect="multiply",
                value=0.80,
            ),
        ],
    ),

    # 3. Evergreen Insurance (GLM) - Over-50s specialist
    InsurerProfile(
        name="Evergreen Insurance",
        strategy="glm",
        base_rate=1200,
        noise_sigma=0.05,
        young_driver_loading=1.50,
        senior_discount=0.82,
        min_age=25,
        married_discount=0.93,
        homeowner_discount=0.90,
        occupation_group_factors={
            "Professional": 0.88, "Management": 0.90, "Elementary": 1.08,
        },
        employment_factors={
            "retired": 0.88, "unemployed": 1.20, "student_full_time": 1.30,
        },
        ncd_max_discount=0.63,
        loyalty_discount=0.92,
        ev_discount=0.92,
        old_vehicle_loading=0.95,
        overnight_factors={
            "garage": 0.88, "driveway": 0.95, "public_road": 1.12,
        },
        decline_provisional=True,
        decline_max_claims=3,
        decline_max_convictions=2,
        decline_drink_drive=True,
        minimum_premiums={
            "comprehensive": 350,
            "third_party_fire_and_theft": 280,
            "third_party_only": 250,
        },
        interactions=[
            Interaction(
                conditions={"marital_status": ("in", {"married", "civil_partnership"}),
                            "is_homeowner": ("eq", True),
                            "ncd_years": ("gte", 9)},
                effect="multiply",
                value=0.88,
            ),
            Interaction(
                conditions={"has_young_named_driver": ("eq", True)},
                effect="multiply",
                value=1.40,
            ),
        ],
    ),

    # 4. QuoteFast (GLM) - Budget aggregator
    InsurerProfile(
        name="QuoteFast",
        strategy="glm",
        base_rate=1150,
        noise_sigma=0.04,
        new_business_discount=0.92,
        loyalty_discount=1.08,
        excess_credit_rate=1.20,
        tpft_discount=0.75,
        tpo_discount=0.60,
        urban_loading=1.08,
        vehicle_group_power=1.10,
        decline_provisional=True,
        minimum_premiums={
            "comprehensive": 220,
            "third_party_fire_and_theft": 180,
            "third_party_only": 160,
        },
    ),

    # 5. Prestige Motor (GLM) - High-net-worth
    InsurerProfile(
        name="Prestige Motor",
        strategy="glm",
        base_rate=1150,
        noise_sigma=0.06,
        vehicle_group_power=0.65,
        ev_discount=0.85,
        old_vehicle_loading=1.15,
        new_vehicle_discount=0.95,
        min_age=21,
        min_vehicle_value=8000,
        preferred_makes={
            "PORSCHE": 0.90, "BMW": 0.93, "MERCEDES": 0.93, "AUDI": 0.95,
            "TESLA": 0.92, "LAND ROVER": 0.95, "JAGUAR": 0.93,
        },
        tracker_discount=0.92,
        alarm_discount=0.95,
        overnight_factors={
            "garage": 0.88, "driveway": 0.95, "public_road": 1.15,
        },
        minimum_premiums={
            "comprehensive": 450,
            "third_party_fire_and_theft": 380,
            "third_party_only": 350,
        },
        min_premium_young=500,
        interactions=[
            Interaction(
                conditions={"insurance_group": ("gte", 40), "overnight_location": ("eq", "garage")},
                effect="multiply",
                value=0.90,
            ),
        ],
    ),

    # 6. CoverCheap (TIERED) - Ultra-budget
    InsurerProfile(
        name="CoverCheap",
        strategy="tiered",
        base_rate=960,  # fallback if no tier matches
        noise_sigma=0.08,
        ncd_max_discount=0.58,
        tpft_discount=0.72,
        tpo_discount=0.58,
        excess_credit_rate=1.25,
        urban_loading=1.22,
        vehicle_group_power=1.20,
        max_insurance_group=40,
        decline_provisional=True,
        decline_modified=True,
        decline_imported=True,
        minimum_premiums={
            "comprehensive": 160,
            "third_party_fire_and_theft": 140,
            "third_party_only": 120,
        },
        tier_rules=[
            ("age", "lt", 21, "young_high_risk"),
            ("age_and_group", "age_lt_25_group_gte_25", None, "young_medium"),
            ("age", "lt", 25, "young_standard"),
            ("age", "gte", 70, "senior"),
            ("insurance_group", "gte", 35, "high_group"),
        ],
        tier_base_rates={
            "young_high_risk": 2800,
            "young_medium": 1900,
            "young_standard": 1500,
            "senior": 1200,
            "high_group": 1400,
            "standard": 960,
        },
    ),

    # 7. CountyWide Mutual (GLM) - Rural
    InsurerProfile(
        name="CountyWide Mutual",
        strategy="glm",
        base_rate=1020,
        noise_sigma=0.05,
        urban_loading=1.40,
        senior_discount=0.88,
        ncd_max_discount=0.62,
        loyalty_discount=0.93,
        ev_discount=0.90,
        regional_factors={
            "E": 1.35, "EC": 1.35, "N": 1.30, "NW": 1.30, "SE": 1.25,
            "W": 1.30, "WC": 1.35, "M": 1.25, "B": 1.20, "L": 1.25,
        },
        minimum_premiums={
            "comprehensive": 250,
            "third_party_fire_and_theft": 200,
            "third_party_only": 180,
        },
    ),

    # 8. Haven Insurance (GLM) - Safety-focused
    InsurerProfile(
        name="Haven Insurance",
        strategy="glm",
        base_rate=1100,
        noise_sigma=0.05,
        female_discount=0.85,
        tracker_discount=0.90,
        alarm_discount=0.95,
        overnight_factors={
            "garage": 0.90, "driveway": 0.95,
        },
        ncd_max_discount=0.62,
        new_business_discount=0.94,
        ev_discount=0.90,
        minimum_premiums={
            "comprehensive": 280,
            "third_party_fire_and_theft": 220,
            "third_party_only": 200,
        },
    ),

    # 9. SmartDrive (GLM) - Digital-first app
    InsurerProfile(
        name="SmartDrive",
        strategy="glm",
        base_rate=1080,
        noise_sigma=0.06,
        young_driver_loading=0.85,
        ncd_max_discount=0.58,
        new_business_discount=0.88,
        excess_credit_rate=1.15,
        vehicle_group_power=1.05,
        urban_loading=1.15,
        minimum_premiums={
            "comprehensive": 220,
            "third_party_fire_and_theft": 180,
            "third_party_only": 160,
        },
    ),

    # 10. National Mutual (GLM) - Renewal retention
    InsurerProfile(
        name="National Mutual",
        strategy="glm",
        base_rate=1200,
        noise_sigma=0.05,
        new_business_discount=1.15,
        loyalty_discount=0.88,
        young_driver_loading=1.10,
        senior_discount=0.90,
        ncd_max_discount=0.65,
        married_discount=0.95,
        homeowner_discount=0.95,
        vehicle_group_power=0.95,
        urban_loading=1.12,
        decline_provisional=True,
        decline_drink_drive=True,
        minimum_premiums={
            "comprehensive": 300,
            "third_party_fire_and_theft": 250,
            "third_party_only": 220,
        },
    ),

    # 11. VoltEdge (GLM) - EV specialist
    InsurerProfile(
        name="VoltEdge",
        strategy="glm",
        base_rate=1300,
        noise_sigma=0.07,
        ev_discount=0.70,
        fuel_factors={
            "petrol": 1.15, "diesel": 1.18, "lpg": 1.20,
        },
        old_vehicle_loading=1.25,
        vehicle_group_power=0.80,
        senior_discount=0.95,
        urban_loading=1.05,
        tracker_discount=0.90,
        max_vehicle_age=20,
        minimum_premiums={
            "comprehensive": 380,
            "third_party_fire_and_theft": 300,
            "third_party_only": 260,
        },
    ),

    # 12. Sterling & Shield (GLM) - Broker-only, quotes anything
    InsurerProfile(
        name="Sterling and Shield",
        strategy="glm",
        base_rate=1350,
        noise_sigma=0.09,
        ncd_max_discount=0.55,
        vehicle_group_power=0.90,
        licence_type_factors={
            "provisional_uk": 1.30, "full_eu": 1.05, "full_international": 1.10,
        },
        excess_credit_rate=0.80,
        max_insurance_group=50,
        minimum_premiums={
            "comprehensive": 400,
            "third_party_fire_and_theft": 350,
            "third_party_only": 300,
        },
    ),

    # 13. FamilyFleet (GLM) - Multi-car
    InsurerProfile(
        name="FamilyFleet",
        strategy="glm",
        base_rate=1040,
        noise_sigma=0.06,
        young_driver_loading=0.90,
        senior_discount=0.93,
        ncd_max_discount=0.58,
        loyalty_discount=0.90,
        new_business_discount=0.92,
        vehicle_group_power=1.05,
        urban_loading=1.12,
        additional_driver_loading=0.03,
        min_age=21,
        minimum_premiums={
            "comprehensive": 200,
            "third_party_fire_and_theft": 170,
            "third_party_only": 150,
        },
    ),

    # 14. FlexCover (GLM) - Low-mileage / temporary
    InsurerProfile(
        name="FlexCover",
        strategy="glm",
        base_rate=1150,
        noise_sigma=0.06,
        mileage_power=0.80,
        excess_credit_rate=1.30,
        ncd_max_discount=0.55,
        young_driver_loading=0.95,
        vehicle_group_power=0.90,
        new_business_discount=0.90,
        urban_loading=1.05,
        minimum_premiums={
            "comprehensive": 250,
            "third_party_fire_and_theft": 200,
            "third_party_only": 180,
        },
        interactions=[
            Interaction(
                conditions={"annual_mileage": ("gt", 15000)},
                effect="multiply",
                value=1.20,
            ),
            Interaction(
                conditions={"annual_mileage": ("lt", 5000)},
                effect="multiply",
                value=0.85,
            ),
        ],
    ),

    # 15. TorqueHouse (SPECIALIST) - Modified vehicle specialist
    InsurerProfile(
        name="TorqueHouse",
        strategy="specialist",
        base_rate=1280,
        noise_sigma=0.06,
        modification_loading=1.05,
        vehicle_group_power=0.75,
        old_vehicle_loading=0.90,
        young_driver_loading=1.10,
        ncd_max_discount=0.55,
        urban_loading=1.15,
        overnight_factors={
            "garage": 0.88,
        },
        min_age=21,
        preferred_makes={
            "SUBARU": 0.92, "HONDA": 0.93, "NISSAN": 0.95, "MAZDA": 0.93,
            "MITSUBISHI": 0.93, "BMW": 0.95, "VOLKSWAGEN": 0.95,
        },
        minimum_premiums={
            "comprehensive": 380,
            "third_party_fire_and_theft": 300,
            "third_party_only": 270,
        },
        min_premium_young=420,
        interactions=[
            Interaction(
                conditions={"age": ("lt", 21), "has_modifications": ("eq", True)},
                effect="decline",
            ),
            Interaction(
                conditions={"engine_cc": ("gt", 3000), "age": ("lt", 25)},
                effect="multiply",
                value=1.30,
            ),
        ],
    ),

    # 16. Heritage Motor (SPECIALIST) - Classic / enthusiast
    InsurerProfile(
        name="Heritage Motor",
        strategy="specialist",
        base_rate=1100,
        noise_sigma=0.06,
        old_vehicle_loading=0.78,
        classic_vehicle_factor=0.75,
        senior_discount=0.85,
        young_driver_loading=1.80,
        vehicle_group_power=0.70,
        urban_loading=1.30,
        ncd_max_discount=0.60,
        overnight_factors={
            "garage": 0.80, "driveway": 0.95, "public_road": 1.25,
        },
        min_age=25,
        max_vehicle_value=150000,
        decline_business_use=True,
        minimum_premiums={
            "comprehensive": 300,
            "third_party_fire_and_theft": 250,
            "third_party_only": 220,
        },
        interactions=[
            Interaction(
                conditions={"vehicle_age": ("lt", 5)},
                effect="multiply",
                value=1.40,
            ),
            Interaction(
                conditions={"annual_mileage": ("gt", 10000)},
                effect="multiply",
                value=1.25,
            ),
        ],
    ),

    # 17. Caledonian (GLM) - Scottish regional
    InsurerProfile(
        name="Caledonian",
        strategy="glm",
        base_rate=1100,
        noise_sigma=0.07,
        urban_loading=1.10,
        regional_factors={
            # Scottish postcodes - discounted
            "EH": 0.88, "G": 0.87, "AB": 0.90, "DD": 0.88, "PH": 0.85,
            "IV": 0.85, "KW": 0.85, "HS": 0.85, "ZE": 0.85, "FK": 0.88,
            "PA": 0.88, "KA": 0.88, "ML": 0.90, "TD": 0.87, "DG": 0.87,
            # London postcodes - loaded
            "E": 1.32, "EC": 1.35, "N": 1.30, "NW": 1.30, "SE": 1.28,
            "SW": 1.28, "W": 1.32, "WC": 1.35,
        },
        senior_discount=0.90,
        loyalty_discount=0.90,
        ev_discount=0.95,
        minimum_premiums={
            "comprehensive": 220,
            "third_party_fire_and_theft": 180,
            "third_party_only": 160,
        },
    ),

    # 18. WorkDrive (GLM) - Commercial crossover
    InsurerProfile(
        name="WorkDrive",
        strategy="glm",
        base_rate=1100,
        noise_sigma=0.06,
        business_use_loading=1.0,
        commuting_loading=1.0,
        vehicle_group_power=0.85,
        ncd_max_discount=0.65,
        young_driver_loading=1.05,
        old_vehicle_loading=0.95,
        new_business_discount=0.92,
        urban_loading=1.18,
        excess_credit_rate=1.10,
        min_age=21,
        body_type_factors={
            "pickup": 0.92, "suv": 1.0, "mpv": 0.95,
        },
        minimum_premiums={
            "comprehensive": 280,
            "third_party_fire_and_theft": 230,
            "third_party_only": 200,
        },
        min_premium_business=300,
    ),

    # 19. Guildline (AFFINITY) - Professional group
    InsurerProfile(
        name="Guildline",
        strategy="affinity",
        base_rate=1080,
        noise_sigma=0.04,
        age_curve="flat_middle",
        occupation_group_factors={
            "Professional": 0.85, "Management": 0.88, "Technical": 0.92,
            "Administrative": 0.96, "Skilled Trades": 1.08, "Care & Leisure": 1.08,
            "Sales & Service": 1.12, "Process & Plant": 1.15, "Elementary": 1.18,
        },
        employment_factors={
            "retired": 0.92, "unemployed": 1.25, "student_full_time": 1.20,
        },
        ncd_max_discount=0.63,
        loyalty_discount=0.92,
        married_discount=0.95,
        homeowner_discount=0.93,
        vehicle_group_power=0.95,
        urban_loading=1.10,
        decline_provisional=True,
        minimum_premiums={
            "comprehensive": 300,
            "third_party_fire_and_theft": 250,
            "third_party_only": 220,
        },
    ),

    # 20. Spark Insurance (GLM) - Disruptor
    InsurerProfile(
        name="Spark Insurance",
        strategy="glm",
        base_rate=1050,
        noise_sigma=0.08,
        ncd_max_discount=0.48,
        new_business_discount=0.85,
        young_driver_loading=0.95,
        senior_discount=1.10,
        ev_discount=0.88,
        urban_loading=1.10,
        licence_type_factors={
            "full_eu": 0.98, "full_international": 1.02,
        },
        uk_residency_loading=1.0,
        minimum_premiums={
            "comprehensive": 200,
            "third_party_fire_and_theft": 170,
            "third_party_only": 150,
        },
    ),
]

# Column names for parquet output (derived dynamically)
INSURER_COLUMNS = [ins.name.lower().replace(" ", "_").replace("&", "and") for ins in INSURERS]
_INS_TO_COL_IDX = {ins.name: i for i, ins in enumerate(INSURERS)}


# ─────────────────────────────────────────────────────────────────────────────
# Age curves
# ─────────────────────────────────────────────────────────────────────────────

def _age_factor_standard(age: int) -> float:
    """Standard S-shaped age curve. High for young, flat mid, rising for elderly."""
    if age < 17:
        return 5.0
    if age <= 19:
        return 3.8 - (age - 17) * 0.30
    if age <= 21:
        return 3.2 - (age - 19) * 0.35
    if age <= 25:
        return 2.5 - (age - 21) * 0.20
    if age <= 30:
        return 1.7 - (age - 25) * 0.10
    if age <= 40:
        return 1.2 - (age - 30) * 0.02
    if age <= 55:
        return 1.0
    if age <= 65:
        return 1.0 + (age - 55) * 0.01
    if age <= 75:
        return 1.1 + (age - 65) * 0.03
    return 1.4 + (age - 75) * 0.04


def _age_factor_young_friendly(age: int) -> float:
    """Flatter young-driver penalty. Peak 2.5 at 17 vs 3.8 standard."""
    if age < 17:
        return 3.5
    if age <= 19:
        return 2.5 - (age - 17) * 0.15
    if age <= 21:
        return 2.2 - (age - 19) * 0.20
    if age <= 25:
        return 1.8 - (age - 21) * 0.15
    if age <= 30:
        return 1.2 - (age - 25) * 0.04
    if age <= 40:
        return 1.0
    if age <= 55:
        return 1.0
    if age <= 65:
        return 1.0 + (age - 55) * 0.01
    if age <= 75:
        return 1.1 + (age - 65) * 0.03
    return 1.4 + (age - 75) * 0.04


def _age_factor_mature_specialist(age: int) -> float:
    """Very flat 30-70, steep penalty <25, steep >80."""
    if age < 17:
        return 6.0
    if age <= 19:
        return 4.5 - (age - 17) * 0.40
    if age <= 21:
        return 3.7 - (age - 19) * 0.45
    if age <= 25:
        return 2.8 - (age - 21) * 0.35
    if age <= 30:
        return 1.4 - (age - 25) * 0.08
    if age <= 70:
        return 1.0
    if age <= 80:
        return 1.0 + (age - 70) * 0.04
    return 1.4 + (age - 80) * 0.06


def _age_factor_flat_middle(age: int) -> float:
    """Flat 25-65 at 1.0, moderate penalty at ends."""
    if age < 17:
        return 3.0
    if age <= 19:
        return 2.5 - (age - 17) * 0.20
    if age <= 21:
        return 2.1 - (age - 19) * 0.25
    if age <= 25:
        return 1.6 - (age - 21) * 0.15
    if age <= 65:
        return 1.0
    if age <= 75:
        return 1.0 + (age - 65) * 0.02
    return 1.2 + (age - 75) * 0.04


_AGE_CURVES = {
    "standard": _age_factor_standard,
    "young_friendly": _age_factor_young_friendly,
    "mature_specialist": _age_factor_mature_specialist,
    "flat_middle": _age_factor_flat_middle,
}


def _age_factor(age: int, curve: str = "standard") -> float:
    return _AGE_CURVES.get(curve, _age_factor_standard)(age)


# ─────────────────────────────────────────────────────────────────────────────
# Core rating factor functions
# ─────────────────────────────────────────────────────────────────────────────

def _ncd_factor(ncd_years: int, max_discount: float) -> float:
    steps = {0: 0.0, 1: 0.30, 2: 0.40, 3: 0.50, 4: 0.55}
    if ncd_years in steps:
        discount = steps[ncd_years]
    else:
        discount = 0.55 + (min(ncd_years, 15) - 5) * ((max_discount - 0.55) / 10)
        discount = min(discount, max_discount)
    return 1.0 - discount


def _vehicle_group_factor(group: int, power: float, cap: float = 3.5) -> float:
    return max(0.6, min((group / 20.0) ** power, cap))


def _mileage_factor(annual_mileage: int, power: float = 0.5) -> float:
    return max(0.70, min((annual_mileage / 8000) ** power, 1.8))


def _excess_credit(voluntary_excess: int, rate: float) -> float:
    if voluntary_excess <= 0:
        return 1.0
    return max(0.80, 1.0 - (voluntary_excess / 2500) * rate)


def _vehicle_age_factor(veh_age: int, old_loading: float, new_discount: float = 1.0,
                         classic_factor: float = 1.0) -> float:
    if veh_age >= 15:
        # Classic: apply classic_vehicle_factor
        return classic_factor if classic_factor != 1.0 else (1.0 + (old_loading - 1.0) * 1.5)
    if veh_age >= 10:
        return old_loading
    if veh_age <= 1:
        return new_discount
    return 1.0


def _marital_factor(marital_status: str, married_discount: float) -> float:
    if married_discount == 1.0:
        return 1.0
    if marital_status in ("married", "civil_partnership", "living_with_partner"):
        return married_discount
    return 1.0


def _employment_factor(employment: str, factors: dict) -> float:
    if not factors:
        return 1.0
    return factors.get(employment, 1.0)


def _occupation_factor(occupation_industry: str, factors: dict) -> float:
    if not factors or not occupation_industry:
        return 1.0
    return factors.get(occupation_industry, 1.0)


def _licence_type_factor(licence_type: str, factors: dict) -> float:
    if not factors:
        return 1.0
    return factors.get(licence_type, 1.0)


def _licence_years_factor(years: int, power: float) -> float:
    """Longer licence = lower premium. Returns multiplier ~0.85 to 1.15."""
    if years <= 0:
        return 1.15
    # Normalise around 10 years
    return max(0.85, min(1.15, (10.0 / max(years, 1)) ** (power * 0.15)))


def _make_factor(make: str, preferred_makes: dict) -> float:
    if not preferred_makes:
        return 1.0
    return preferred_makes.get(make.upper(), 1.0)


def _body_type_factor(body_type: str, factors: dict) -> float:
    if not factors:
        return 1.0
    return factors.get(body_type, 1.0)


def _fuel_factor(fuel: str, fuel_factors: dict, ev_discount: float) -> float:
    """Apply fuel-specific factor. EV discount applied to electric/hybrid fuels."""
    if fuel in ("electric", "plug_in_hybrid", "hybrid_petrol_electric", "hybrid_diesel_electric"):
        return ev_discount
    if fuel_factors:
        return fuel_factors.get(fuel, 1.0)
    return 1.0


def _engine_factor(engine_cc: int, engine_cc_factors: list) -> float:
    """Apply engine size bands. factors is [(threshold_cc, factor), ...] sorted ascending."""
    if not engine_cc_factors or not engine_cc:
        return 1.0
    result = 1.0
    for threshold, factor in sorted(engine_cc_factors):
        if engine_cc >= threshold:
            result = factor
    return result


def _modification_factor(has_mods: bool, loading: float) -> float:
    if has_mods:
        return loading
    return 1.0


def _security_factor(security: dict, tracker_discount: float,
                      alarm_discount: float, no_security_loading: float) -> float:
    """Apply security device factors."""
    factor = 1.0
    tracker = security.get("tracker", "none")
    alarm = security.get("alarm", "none")
    immobiliser = security.get("immobiliser", "none")

    has_tracker = tracker != "none"
    has_alarm = alarm != "none"
    has_immobiliser = immobiliser != "none"

    if has_tracker:
        factor *= tracker_discount
    if has_alarm:
        factor *= alarm_discount

    # No security loading applies only when nothing is fitted
    if not has_tracker and not has_alarm and not has_immobiliser:
        factor *= no_security_loading

    return factor


def _overnight_factor(overnight_location: str, factors: dict) -> float:
    if not factors:
        return 1.0
    return factors.get(overnight_location, 1.0)


def _homeowner_factor(is_homeowner: bool, discount: float) -> float:
    if is_homeowner and discount != 1.0:
        return discount
    return 1.0


def _business_factor(usage: dict, business_loading: float, commuting_loading: float) -> float:
    """Apply usage-based loading for business and commuting."""
    factor = 1.0
    if usage.get("business_use"):
        factor *= business_loading
    if usage.get("commuting") and commuting_loading != 1.0:
        factor *= commuting_loading
    return factor


def _additional_drivers_factor(drivers: list, per_driver_loading: float,
                                young_loading: float) -> float:
    """Loading per additional driver, extra for young named drivers."""
    if not drivers or (per_driver_loading == 0.0 and young_loading == 0.0):
        return 1.0
    n_drivers = len(drivers)
    factor = 1.0 + n_drivers * per_driver_loading

    if young_loading > 0.0:
        for d in drivers:
            d_age = _calc_age(d.get("date_of_birth"))
            if d_age < 21:
                factor += young_loading
    return factor


def _claims_detail_factor(claims: list, at_fault_loading: float,
                           not_at_fault_loading: float, recency_weight: float,
                           pi_loading: float) -> float:
    """Detailed claims loading considering fault, recency, and PI."""
    if not claims:
        return 1.0

    factor = 1.0
    today = date.today()

    for claim in claims:
        fault = claim.get("fault", "at_fault")
        pi = claim.get("personal_injury_claimed", False)

        # Base loading by fault
        if fault == "at_fault":
            load = at_fault_loading
        else:
            load = not_at_fault_loading

        # Recency weighting: more recent claims cost more
        if recency_weight > 0.0:
            try:
                claim_date = date.fromisoformat(claim.get("claim_date", "2020-01-01"))
                days_ago = (today - claim_date).days
                years_ago = days_ago / 365.25
                # Recent claims (< 1 year) get full weight, older claims taper
                recency_mult = 1.0 + recency_weight * max(0.0, 1.0 - years_ago / 5.0)
                load *= recency_mult
            except (ValueError, TypeError):
                pass

        # PI claim additional loading
        if pi and pi_loading > 0.0:
            load += pi_loading

        factor += load

    return factor


def _convictions_detail_factor(convictions: list, loading: float,
                                speeding_loading: float) -> float:
    """Conviction loading with optional speeding-specific surcharge."""
    if not convictions:
        return 1.0

    total_load = 0.0
    for conv in convictions:
        code = conv.get("conviction_code", "")
        if code.startswith("SP") and speeding_loading > 0.0:
            total_load += speeding_loading
        else:
            total_load += loading
    return 1.0 + total_load


def _uk_residency_factor(uk_resident_since_birth: bool, loading: float) -> float:
    if not uk_resident_since_birth and loading != 1.0:
        return loading
    return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Area rating
# ─────────────────────────────────────────────────────────────────────────────

_AREA_BANDS = {
    "E": 5, "EC": 5, "N": 5, "NW": 5, "SE": 5, "SW": 4, "W": 5, "WC": 5,
    "BR": 4, "CR": 4, "DA": 4, "EN": 4, "HA": 4, "IG": 4, "KT": 3, "RM": 4,
    "SM": 3, "TW": 3, "UB": 4, "WD": 3,
    "M": 4, "B": 4, "L": 4, "LS": 3, "S": 3, "NG": 3, "LE": 3,
    "BD": 3, "BL": 3, "OL": 3, "WN": 3, "WA": 3, "WV": 3, "WS": 3,
    "CV": 3, "DE": 3, "ST": 3, "DY": 3,
    "BS": 3, "CF": 3, "NE": 3, "SR": 3, "DH": 3, "TS": 3, "HU": 3,
    "DN": 3, "LN": 2, "PE": 2, "NR": 2, "IP": 2, "CO": 2, "CB": 2,
    "CM": 3, "SS": 3, "ME": 3, "CT": 3, "TN": 2, "BN": 3, "RH": 2,
    "GU": 2, "PO": 3, "SO": 3, "BH": 3, "SP": 2, "BA": 2, "SN": 2,
    "GL": 2, "OX": 2, "RG": 2, "SL": 3, "HP": 2, "MK": 2, "LU": 3,
    "AL": 2, "SG": 2, "HG": 2, "YO": 2, "DL": 2, "CA": 2, "LA": 2,
    "PR": 3, "FY": 3, "BB": 3, "HX": 3, "HD": 3, "WF": 3,
    "EX": 2, "PL": 2, "TQ": 2, "TA": 2, "DT": 2, "TR": 1, "EH": 2,
    "G": 3, "PA": 2, "KA": 2, "ML": 3, "FK": 2, "DD": 2, "PH": 1,
    "AB": 2, "IV": 1, "KW": 1, "HS": 1, "ZE": 1,
    "SA": 2, "LD": 1, "SY": 2, "LL": 2, "CH": 3,
    "BT": 2, "TD": 1, "DG": 1, "HR": 2, "WR": 2, "NN": 2, "TF": 2,
    "CW": 2, "SK": 3, "HF": 2, "LI": 2,
}


def _extract_postcode_area(postcode: str) -> str:
    """Extract the alphabetical area prefix from a UK postcode."""
    pc = postcode.strip().split()[0] if postcode else ""
    return "".join(c for c in pc if c.isalpha())


def _area_factor(postcode: str, urban_loading: float, regional_factors: dict) -> float:
    """Compute area rating factor from postcode, urban loading, and per-insurer regional overrides."""
    area = _extract_postcode_area(postcode)

    # Check insurer-specific regional factors first
    if regional_factors:
        # Try full area match, then single-letter prefix
        rf = regional_factors.get(area)
        if rf is None and len(area) > 1:
            rf = regional_factors.get(area[:1])
        if rf is not None:
            return rf

    # Standard area band approach
    band = _AREA_BANDS.get(area, _AREA_BANDS.get(area[:1] if area else "", 3))
    base = {1: 0.85, 2: 0.95, 3: 1.00, 4: 1.10, 5: 1.25}[band]
    if band >= 4:
        base = 1.0 + (base - 1.0) * urban_loading
    elif band <= 2:
        base = 1.0 - (1.0 - base) * (1.0 / max(urban_loading, 0.5))
    return base


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
# Decline engine
# ─────────────────────────────────────────────────────────────────────────────

_DRINK_DRIVE_CODES = {"DR10", "DR20", "DR30", "DR31", "DR40", "DR50", "DR60",
                      "DR70", "DR80", "DR90"}
_MAJOR_CONVICTION_CODES = {"DD40", "DD60", "DD80", "DD90", "IN10"}


def _check_decline(quote_fields: dict, insurer: InsurerProfile) -> bool:
    """Return True if the quote should be declined by this insurer."""
    age = quote_fields.get("age", 40)
    ins_group = quote_fields.get("insurance_group", 20)
    licence_type = quote_fields.get("licence_type", "full_uk")
    has_mods = quote_fields.get("has_modifications", False)
    is_imported = quote_fields.get("is_imported", False)
    n_claims = quote_fields.get("claim_count", 0)
    n_convictions = quote_fields.get("conviction_count", 0)
    total_points = quote_fields.get("total_points", 0)
    veh_age = quote_fields.get("vehicle_age", 5)
    veh_value = quote_fields.get("vehicle_value", 10000)
    postcode_area = quote_fields.get("postcode_area", "")
    has_business = quote_fields.get("has_business_use", False)
    conviction_codes = quote_fields.get("conviction_codes", set())

    # Single-factor decline rules
    if age < insurer.min_age or age > insurer.max_age:
        return True
    if ins_group > insurer.max_insurance_group:
        return True
    if insurer.decline_provisional and licence_type == "provisional_uk":
        return True
    if insurer.decline_modified and has_mods:
        return True
    if insurer.decline_imported and is_imported:
        return True
    if n_claims > insurer.decline_max_claims:
        return True
    if n_convictions > insurer.decline_max_convictions:
        return True
    if total_points > insurer.decline_max_points:
        return True
    if veh_age > insurer.max_vehicle_age:
        return True
    if veh_value < insurer.min_vehicle_value:
        return True
    if veh_value > insurer.max_vehicle_value:
        return True
    if insurer.decline_business_use and has_business:
        return True
    if insurer.decline_postcodes and postcode_area in insurer.decline_postcodes:
        return True
    if insurer.decline_drink_drive and conviction_codes & _DRINK_DRIVE_CODES:
        return True
    if insurer.decline_major_conviction and conviction_codes & _MAJOR_CONVICTION_CODES:
        return True

    # Interaction-based declines
    for interaction in insurer.interactions:
        if interaction.effect == "decline" and _eval_interaction(interaction, quote_fields):
            return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Minimum premium floor
# ─────────────────────────────────────────────────────────────────────────────

def _apply_minimum_premium(premium: float, cover_type: str, age: int,
                            has_business: bool, insurer: InsurerProfile) -> float:
    """Apply cover-level-aware minimum premium floor."""
    floor = insurer.minimum_premiums.get(cover_type, 200)
    if age < 25 and insurer.min_premium_young > 0:
        floor = max(floor, insurer.min_premium_young)
    if has_business and insurer.min_premium_business > 0:
        floor = max(floor, insurer.min_premium_business)
    return max(floor, premium)


# ─────────────────────────────────────────────────────────────────────────────
# Tiered strategy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_tier(quote_fields: dict, tier_rules: list) -> str:
    """Evaluate tier rules in order, return matching tier label or 'standard'."""
    age = quote_fields.get("age", 40)
    ins_group = quote_fields.get("insurance_group", 20)

    for rule in tier_rules:
        field_name, operator, value, tier_label = rule

        # Special compound rule for age+group
        if field_name == "age_and_group":
            if operator == "age_lt_25_group_gte_25":
                if age < 25 and ins_group >= 25:
                    return tier_label
            continue

        actual = quote_fields.get(field_name, None)
        if actual is None:
            continue

        if operator == "lt" and actual < value:
            return tier_label
        if operator == "gt" and actual > value:
            return tier_label
        if operator == "lte" and actual <= value:
            return tier_label
        if operator == "gte" and actual >= value:
            return tier_label
        if operator == "eq" and actual == value:
            return tier_label

    return "standard"


# ─────────────────────────────────────────────────────────────────────────────
# Extract quote fields into flat dict for rating
# ─────────────────────────────────────────────────────────────────────────────

def _extract_quote_fields(quote: dict) -> dict:
    """Extract and flatten all rating-relevant fields from a quote dict."""
    proposer = quote["proposer"]
    policy = quote["policy_details"]
    vehicle = quote["vehicle"]
    address = quote["address"]
    additional_drivers = quote.get("additional_drivers", [])

    age = _calc_age(proposer.get("date_of_birth"))
    gender = proposer.get("gender", "male")
    marital_status = proposer.get("marital_status", "single")
    employment = proposer.get("employment_status", "employed")
    occupation_industry = proposer.get("primary_occupation_industry")
    is_homeowner = proposer.get("is_homeowner", False)
    uk_resident = proposer.get("uk_resident_since_birth", True)

    licence = proposer.get("licence", {})
    licence_type = licence.get("licence_type", "full_uk")
    licence_years = licence.get("licence_held_years", 5)

    claims = proposer.get("claims", [])
    convictions = proposer.get("convictions", [])
    conviction_codes = {c.get("conviction_code", "") for c in convictions}
    total_points = sum(c.get("points", 0) for c in convictions)

    ncd = policy.get("ncd_years", 0)
    ncd_protected = policy.get("ncd_protected", False)
    cover_type = policy.get("cover_type", "comprehensive")
    vol_excess = policy.get("voluntary_excess", 250)
    annual_mileage = policy.get("annual_mileage", 8000)
    usage = policy.get("usage", {})
    has_business = bool(usage.get("business_use"))
    has_commuting = bool(usage.get("commuting"))

    ins_group = vehicle.get("insurance_group", 20)
    fuel = vehicle.get("fuel_type", "petrol")
    make = vehicle.get("make", "")
    body_type = vehicle.get("body_type", "hatchback")
    engine_cc = vehicle.get("engine_size_cc") or 0
    veh_age = max(0, date.today().year - vehicle.get("year_of_manufacture", 2020))
    veh_value = vehicle.get("estimated_value", 10000)
    has_mods = vehicle.get("has_been_modified", False)
    is_imported = vehicle.get("is_imported", False)
    security = vehicle.get("security", {})
    overnight_location = vehicle.get("overnight_location", "driveway")

    has_tracker = security.get("tracker", "none") != "none"

    postcode = address.get("postcode", "")
    postcode_area = _extract_postcode_area(postcode)

    # Check for young named drivers
    has_young_named_driver = False
    for d in additional_drivers:
        d_age = _calc_age(d.get("date_of_birth"))
        if d_age < 21:
            has_young_named_driver = True
            break

    return {
        "age": age,
        "gender": gender,
        "marital_status": marital_status,
        "employment": employment,
        "occupation_industry": occupation_industry,
        "is_homeowner": is_homeowner,
        "uk_resident_since_birth": uk_resident,
        "licence_type": licence_type,
        "licence_years": licence_years,
        "claims": claims,
        "claim_count": len(claims),
        "convictions": convictions,
        "conviction_count": len(convictions),
        "conviction_codes": conviction_codes,
        "total_points": total_points,
        "ncd_years": ncd,
        "ncd_protected": ncd_protected,
        "cover_type": cover_type,
        "voluntary_excess": vol_excess,
        "annual_mileage": annual_mileage,
        "usage": usage,
        "has_business_use": has_business,
        "has_commuting": has_commuting,
        "insurance_group": ins_group,
        "fuel": fuel,
        "make": make,
        "body_type": body_type,
        "engine_cc": engine_cc,
        "vehicle_age": veh_age,
        "vehicle_value": veh_value,
        "has_modifications": has_mods,
        "is_imported": is_imported,
        "security": security,
        "has_tracker": has_tracker,
        "overnight_location": overnight_location,
        "postcode": postcode,
        "postcode_area": postcode_area,
        "additional_drivers": additional_drivers,
        "has_young_named_driver": has_young_named_driver,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GLM multiplicative strategy (core pricing engine)
# ─────────────────────────────────────────────────────────────────────────────

def _price_glm(f: dict, insurer: InsurerProfile) -> float:
    """Full GLM multiplicative pricing. Used directly by 'glm' strategy and as
    base for telematics/affinity/specialist strategies."""
    premium = insurer.base_rate

    # Age curve with young/senior overlays
    af = _age_factor(f["age"], insurer.age_curve)
    if f["age"] < 25:
        af *= insurer.young_driver_loading
    elif f["age"] >= 65:
        af *= insurer.senior_discount
    premium *= af

    # Gender
    if f["gender"] == "female":
        premium *= insurer.female_discount

    # Marital
    premium *= _marital_factor(f["marital_status"], insurer.married_discount)

    # Employment
    premium *= _employment_factor(f["employment"], insurer.employment_factors)

    # Occupation
    premium *= _occupation_factor(f["occupation_industry"], insurer.occupation_group_factors)

    # Licence type
    premium *= _licence_type_factor(f["licence_type"], insurer.licence_type_factors)

    # Licence years
    premium *= _licence_years_factor(f["licence_years"], insurer.licence_years_power)

    # NCD
    premium *= _ncd_factor(f["ncd_years"], insurer.ncd_max_discount)

    # New business / loyalty
    premium *= insurer.new_business_discount

    # Vehicle group
    premium *= _vehicle_group_factor(f["insurance_group"], insurer.vehicle_group_power,
                                      insurer.vehicle_group_cap)

    # Vehicle make
    premium *= _make_factor(f["make"], insurer.preferred_makes)

    # Body type
    premium *= _body_type_factor(f["body_type"], insurer.body_type_factors)

    # Fuel / EV
    premium *= _fuel_factor(f["fuel"], insurer.fuel_factors, insurer.ev_discount)

    # Engine CC
    premium *= _engine_factor(f["engine_cc"], insurer.engine_cc_factors)

    # Vehicle age
    premium *= _vehicle_age_factor(f["vehicle_age"], insurer.old_vehicle_loading,
                                    insurer.new_vehicle_discount, insurer.classic_vehicle_factor)

    # Modifications
    premium *= _modification_factor(f["has_modifications"], insurer.modification_loading)

    # Security
    premium *= _security_factor(f["security"], insurer.tracker_discount,
                                 insurer.alarm_discount, insurer.no_security_loading)

    # Overnight location
    premium *= _overnight_factor(f["overnight_location"], insurer.overnight_factors)

    # Mileage
    premium *= _mileage_factor(f["annual_mileage"], insurer.mileage_power)

    # Cover type
    if f["cover_type"] == "third_party_fire_and_theft":
        premium *= insurer.tpft_discount
    elif f["cover_type"] == "third_party_only":
        premium *= insurer.tpo_discount

    # Excess credit
    premium *= _excess_credit(f["voluntary_excess"], insurer.excess_credit_rate)

    # Area
    premium *= _area_factor(f["postcode"], insurer.urban_loading, insurer.regional_factors)

    # Business / commuting use
    premium *= _business_factor(f["usage"], insurer.business_use_loading,
                                 insurer.commuting_loading)

    # Claims history (detailed)
    premium *= _claims_detail_factor(f["claims"], insurer.at_fault_loading,
                                      insurer.not_at_fault_loading,
                                      insurer.claim_recency_weight,
                                      insurer.pi_claim_loading)

    # Convictions (detailed)
    premium *= _convictions_detail_factor(f["convictions"], insurer.conviction_loading,
                                           insurer.speeding_loading)

    # Additional drivers
    premium *= _additional_drivers_factor(f["additional_drivers"],
                                           insurer.additional_driver_loading,
                                           insurer.young_named_driver_loading)

    # Homeowner
    premium *= _homeowner_factor(f["is_homeowner"], insurer.homeowner_discount)

    # UK residency
    premium *= _uk_residency_factor(f["uk_resident_since_birth"], insurer.uk_residency_loading)

    return premium


# ─────────────────────────────────────────────────────────────────────────────
# Tiered strategy
# ─────────────────────────────────────────────────────────────────────────────

def _price_tiered(f: dict, insurer: InsurerProfile) -> float:
    """Assign risk tier, look up base rate, apply limited multiplicative factors."""
    tier = _evaluate_tier(f, insurer.tier_rules)
    premium = insurer.tier_base_rates.get(tier, insurer.base_rate)

    # Apply a smaller set of factors (no occupation, marital, homeowner, etc.)
    premium *= _ncd_factor(f["ncd_years"], insurer.ncd_max_discount)
    premium *= _vehicle_group_factor(f["insurance_group"], insurer.vehicle_group_power,
                                      insurer.vehicle_group_cap)
    premium *= _mileage_factor(f["annual_mileage"], insurer.mileage_power)
    premium *= _area_factor(f["postcode"], insurer.urban_loading, insurer.regional_factors)
    premium *= _excess_credit(f["voluntary_excess"], insurer.excess_credit_rate)

    if f["cover_type"] == "third_party_fire_and_theft":
        premium *= insurer.tpft_discount
    elif f["cover_type"] == "third_party_only":
        premium *= insurer.tpo_discount

    # Claims (simplified - just count-based)
    at_fault = sum(1 for c in f["claims"] if c.get("fault") == "at_fault")
    premium *= 1.0 + at_fault * insurer.at_fault_loading

    # Convictions (simplified)
    premium *= 1.0 + f["conviction_count"] * insurer.conviction_loading

    return premium


# ─────────────────────────────────────────────────────────────────────────────
# Telematics strategy
# ─────────────────────────────────────────────────────────────────────────────

def _price_telematics(f: dict, insurer: InsurerProfile) -> float:
    """Run full GLM, then post-process for tracker presence.

    If tracker present: apply deep tracker discount that partially replaces
    young_driver_loading benefit.
    If no tracker: apply no_security_loading surcharge.
    """
    # Run GLM base (which already applied tracker_discount via _security_factor)
    premium = _price_glm(f, insurer)

    # Post-processing adjustment for telematics:
    # The GLM already applied the tracker_discount through _security_factor.
    # For telematics insurers the mileage effect is amplified for low mileage.
    if f["has_tracker"] and f["annual_mileage"] < 6000:
        # Low-mileage telematics bonus on top of GLM
        low_mileage_bonus = 0.95
        premium *= low_mileage_bonus

    return premium


# ─────────────────────────────────────────────────────────────────────────────
# Affinity strategy
# ─────────────────────────────────────────────────────────────────────────────

def _price_affinity(f: dict, insurer: InsurerProfile) -> float:
    """Occupation-first pricing. Occupation factor is the primary driver.
    Other factors still apply but age curve is flatter."""
    # The insurer should have age_curve="flat_middle" set, so _price_glm
    # will already use the flatter age curve. Occupation factors in the
    # profile have large effect. Just run the GLM engine.
    premium = _price_glm(f, insurer)
    return premium


# ─────────────────────────────────────────────────────────────────────────────
# Specialist strategy
# ─────────────────────────────────────────────────────────────────────────────

def _price_specialist(f: dict, insurer: InsurerProfile) -> float:
    """GLM base with interaction-heavy post-processing.
    Many decline checks already handled in _check_decline.
    Apply non-decline interactions here."""
    premium = _price_glm(f, insurer)
    return premium


# ─────────────────────────────────────────────────────────────────────────────
# Strategy dispatch
# ─────────────────────────────────────────────────────────────────────────────

_STRATEGY_DISPATCH = {
    "glm": _price_glm,
    "tiered": _price_tiered,
    "telematics": _price_telematics,
    "affinity": _price_affinity,
    "specialist": _price_specialist,
}


# ─────────────────────────────────────────────────────────────────────────────
# Main pricing function
# ─────────────────────────────────────────────────────────────────────────────

def _price_quote_premium(quote: dict, insurer: InsurerProfile,
                          rng: np.random.Generator, _fields: dict | None = None) -> float:
    """Return annual premium for one insurer, or NaN if declined.

    Accepts an optional pre-extracted _fields dict so field extraction is done
    once per quote rather than once per insurer.
    """
    f = _fields if _fields is not None else _extract_quote_fields(quote)

    # Decline check
    if _check_decline(f, insurer):
        return math.nan

    # Dispatch to strategy
    price_fn = _STRATEGY_DISPATCH.get(insurer.strategy, _price_glm)
    premium = price_fn(f, insurer)

    # Apply non-decline interactions (multiply / add)
    for interaction in insurer.interactions:
        if interaction.effect == "decline":
            continue  # already handled in _check_decline
        if _eval_interaction(interaction, f):
            if interaction.effect == "multiply":
                premium *= interaction.value
            elif interaction.effect == "add":
                premium += interaction.value

    # Random noise
    premium *= float(rng.lognormal(0, insurer.noise_sigma))

    # Apply minimum premium floor
    premium = _apply_minimum_premium(premium, f["cover_type"], f["age"],
                                      f["has_business_use"], insurer)

    return round(premium, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Worker: read a slice of input JSONL, write a parquet chunk
# ─────────────────────────────────────────────────────────────────────────────

def _worker(args: tuple) -> str:
    worker_id, input_path, start_line, n_lines, seed, tmp_file = args

    rng = np.random.default_rng(seed)
    n_ins = len(INSURERS)

    quote_ids = []
    premiums = np.empty((n_lines, n_ins), dtype=np.float32)

    with open(input_path) as fin:
        for _ in range(start_line):
            fin.readline()
        for row in range(n_lines):
            line = fin.readline()
            if not line:
                break
            quote = json.loads(line)
            quote_ids.append(quote["quote_metadata"]["quote_id"])

            # Extract fields once per quote
            fields = _extract_quote_fields(quote)

            for j, insurer in enumerate(INSURERS):
                premiums[row, j] = _price_quote_premium(quote, insurer, rng, _fields=fields)

    # Cheapest per row (ignoring NaN)
    actual_rows = len(quote_ids)
    cheapest = np.nanmin(premiums[:actual_rows], axis=1)

    columns = {"quote_id": pa.array(quote_ids, type=pa.string())}
    for j, col_name in enumerate(INSURER_COLUMNS):
        columns[col_name] = pa.array(premiums[:actual_rows, j], type=pa.float32())
    columns["cheapest"] = pa.array(cheapest, type=pa.float32())

    table = pa.table(columns)
    pq.write_table(table, tmp_file, compression="zstd", compression_level=3)

    return tmp_file


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate competitor premium panels from quote JSONL files (outputs Parquet)"
    )
    parser.add_argument("--input", type=str, required=True, help="Path to quotes JSONL file")
    parser.add_argument("--output", type=str, required=True, help="Output Parquet file path")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Count lines
    print(f"Counting quotes in {input_path}...", file=sys.stderr)
    t0 = time.time()
    total_lines = 0
    with open(input_path, "rb") as f:
        for _ in f:
            total_lines += 1
    print(f"  {total_lines:,} quotes ({time.time() - t0:.1f}s)", file=sys.stderr)

    n_workers = max(1, args.workers)
    chunk_size = total_lines // n_workers
    remainder = total_lines % n_workers

    tmp_dir = tempfile.mkdtemp(prefix="premgen_")
    worker_args = []
    offset = 0
    for i in range(n_workers):
        worker_n = chunk_size + (1 if i < remainder else 0)
        worker_seed = (args.seed + i) if args.seed is not None else None
        tmp_file = os.path.join(tmp_dir, f"chunk_{i:03d}.parquet")
        worker_args.append((i, str(input_path), offset, worker_n, worker_seed, tmp_file))
        offset += worker_n

    print(f"Generating premiums for {len(INSURERS)} insurers across {n_workers} worker(s)...",
          file=sys.stderr)
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
    ordered_files = [a[5] for a in worker_args]
    tables = [pq.read_table(f) for f in ordered_files]
    combined = pa.concat_tables(tables)
    pq.write_table(combined, str(out_path), compression="zstd", compression_level=3)

    # Cleanup
    for f in ordered_files:
        os.unlink(f)
    os.rmdir(tmp_dir)

    elapsed = time.time() - t1
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  Done in {elapsed:.1f}s ({total_lines / elapsed:,.0f} quotes/sec)", file=sys.stderr)
    print(f"  Saved: {out_path} ({size_mb:,.1f} MB)", file=sys.stderr)
    print(f"  Shape: {combined.num_rows:,} rows x {combined.num_columns} cols", file=sys.stderr)
    print(f"  Columns: {combined.column_names}", file=sys.stderr)


if __name__ == "__main__":
    main()
