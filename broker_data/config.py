"""
Broker panel configuration for synthetic insurance data generation.

Defines 5 underwriter profiles, 3 cover levels (Gold/Silver/Bronze),
commission structures, and broker fees for the brokerage scenario.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Allow import from project root (parent of broker_data/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_premiums import InsurerProfile, Interaction


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

IPT_RATE = 0.12  # Insurance Premium Tax (12%)


# ─────────────────────────────────────────────────────────────────────────────
# Cover Levels
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CoverLevel:
    name: str
    voluntary_excess: int
    compulsory_excess: int
    courtesy_car: str              # "like_for_like", "small_car", "none"
    breakdown_cover: str           # "european", "national", "roadside"
    included_addons: list          # e.g. ["legal_expenses", "windscreen", ...]
    premium_multiplier: float
    description: str


COVER_LEVELS = {
    "gold": CoverLevel(
        name="Gold",
        voluntary_excess=0,
        compulsory_excess=100,
        courtesy_car="like_for_like",
        breakdown_cover="european",
        included_addons=[
            "legal_expenses",
            "windscreen",
            "key_cover",
            "personal_belongings",
            "motor_prosecution_defence",
            "no_claims_step_back_protection",
        ],
        premium_multiplier=1.20,
        description="Comprehensive cover with zero voluntary excess, like-for-like "
                    "courtesy car, European breakdown, and all add-ons included.",
    ),
    "silver": CoverLevel(
        name="Silver",
        voluntary_excess=250,
        compulsory_excess=200,
        courtesy_car="small_car",
        breakdown_cover="national",
        included_addons=[
            "legal_expenses",
            "windscreen",
        ],
        premium_multiplier=1.00,
        description="Comprehensive cover with moderate excess, small courtesy car, "
                    "national breakdown, and legal expenses plus windscreen cover.",
    ),
    "bronze": CoverLevel(
        name="Bronze",
        voluntary_excess=500,
        compulsory_excess=350,
        courtesy_car="none",
        breakdown_cover="roadside",
        included_addons=[],
        premium_multiplier=0.85,
        description="Comprehensive cover with higher excess, roadside-only breakdown, "
                    "and no add-ons. Lowest-cost option.",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Underwriter Profile (wraps InsurerProfile with broker-specific fields)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UnderwriterProfile:
    insurer: InsurerProfile

    # Commission by cover level
    gold_commission_rate: float = 0.15
    silver_commission_rate: float = 0.13
    bronze_commission_rate: float = 0.11

    # Fixed fee per policy (some underwriters pay this on top of commission)
    fixed_fee_per_policy: float = 0.0

    # Cover level multiplier overrides (if different from default)
    gold_multiplier: float = 1.20
    silver_multiplier: float = 1.00
    bronze_multiplier: float = 0.85

    def commission_rate(self, tier: str) -> float:
        """Return commission rate for a given tier name."""
        return {
            "gold": self.gold_commission_rate,
            "silver": self.silver_commission_rate,
            "bronze": self.bronze_commission_rate,
        }[tier]

    def tier_multiplier(self, tier: str) -> float:
        """Return premium multiplier for a given tier name."""
        return {
            "gold": self.gold_multiplier,
            "silver": self.silver_multiplier,
            "bronze": self.bronze_multiplier,
        }[tier]


# ─────────────────────────────────────────────────────────────────────────────
# 5 Panel Underwriters
# ─────────────────────────────────────────────────────────────────────────────

PANEL_UNDERWRITERS = [

    # ── 1. Athena Underwriting ───────────────────────────────────────────────
    # Mainstream GLM volume writer. Competitive on ages 30-55, groups 1-30.
    UnderwriterProfile(
        insurer=InsurerProfile(
            name="Athena Underwriting",
            strategy="glm",
            base_rate=1020,
            noise_sigma=0.06,

            # Age – standard curve, slight young loading
            age_curve="standard",
            young_driver_loading=1.10,
            senior_discount=0.97,

            # Gender / marital
            female_discount=0.97,
            married_discount=0.95,

            # Employment
            employment_factors={
                "employed": 0.96,
                "self_employed": 1.02,
                "retired": 0.94,
                "unemployed": 1.15,
                "student_full_time": 1.12,
                "student_part_time": 1.08,
            },
            occupation_group_factors={
                "Professional": 0.90,
                "Management": 0.92,
                "Skilled Trades": 0.97,
                "Elementary": 1.06,
                "Sales": 1.02,
            },

            # Licence
            licence_type_factors={
                "full_uk": 1.00,
                "provisional": 1.45,
                "eu": 1.10,
                "international": 1.15,
            },
            licence_years_power=0.50,

            # NCD
            ncd_max_discount=0.60,
            ncd_protected_bonus=0.02,

            # New biz / loyalty
            new_business_discount=0.95,
            loyalty_discount=0.97,

            # Vehicle group – standard power, competitive on lower groups
            vehicle_group_power=1.00,
            vehicle_group_cap=3.5,

            # Vehicle make preferences (slight)
            preferred_makes={
                "VOLKSWAGEN": 0.97,
                "FORD": 0.97,
                "VAUXHALL": 0.97,
                "TOYOTA": 0.96,
                "HONDA": 0.97,
            },

            # Body type
            body_type_factors={
                "hatchback": 0.96,
                "saloon": 0.98,
                "estate": 0.99,
                "suv": 1.04,
                "convertible": 1.10,
                "coupe": 1.08,
            },

            # Fuel
            ev_discount=0.95,
            fuel_factors={
                "petrol": 1.00,
                "diesel": 1.02,
                "electric": 0.95,
                "hybrid": 0.97,
            },

            # Vehicle age
            old_vehicle_loading=1.05,
            new_vehicle_discount=0.97,
            classic_vehicle_factor=1.10,

            # Engine
            engine_cc_factors=[
                (1200, 0.95),
                (1600, 1.00),
                (2000, 1.08),
                (3000, 1.18),
            ],

            # Modifications & security
            modification_loading=1.20,
            tracker_discount=0.95,
            alarm_discount=0.97,
            no_security_loading=1.05,

            # Overnight location
            overnight_factors={
                "garage": 0.90,
                "driveway": 0.96,
                "public_road": 1.08,
                "car_park": 1.05,
            },

            # Mileage
            mileage_power=0.50,

            # Cover type
            tpft_discount=0.78,
            tpo_discount=0.62,

            # Excess
            excess_credit_rate=1.00,

            # Area
            urban_loading=1.08,
            regional_factors={
                "E": 1.12, "EC": 1.15, "N": 1.10, "NW": 1.08,
                "SE": 1.06, "SW": 1.04, "W": 1.10, "WC": 1.14,
                "M": 1.06, "B": 1.04, "L": 1.05,
            },

            # Claims
            at_fault_loading=0.15,
            not_at_fault_loading=0.03,
            claim_recency_weight=0.02,
            pi_claim_loading=0.08,

            # Convictions
            conviction_loading=0.10,
            speeding_loading=0.04,

            # Additional drivers
            additional_driver_loading=0.04,
            young_named_driver_loading=0.12,

            # Proposer
            homeowner_discount=0.96,
            uk_residency_loading=1.05,

            # Business use
            business_use_loading=1.12,
            commuting_loading=1.04,

            # Decline rules – moderate
            min_age=17,
            max_age=85,
            max_insurance_group=45,
            decline_provisional=False,
            decline_modified=False,
            decline_imported=False,
            decline_max_claims=4,
            decline_max_convictions=3,
            decline_max_points=12,
            max_vehicle_age=20,
            min_vehicle_value=0,
            max_vehicle_value=75000,
            decline_drink_drive=True,
            decline_major_conviction=True,

            # Minimum premiums
            minimum_premiums={
                "comprehensive": 240,
                "third_party_fire_and_theft": 200,
                "third_party_only": 175,
            },
            min_premium_young=300,
            min_premium_business=280,

            # Interactions
            interactions=[
                # Sweet spot: ages 30-55 with clean NCD on lower groups
                Interaction(
                    conditions={
                        "age": ("gte", 30),
                        "insurance_group": ("lte", 30),
                        "ncd_years": ("gte", 5),
                    },
                    effect="multiply",
                    value=0.90,
                ),
                # Penalise high-group young drivers
                Interaction(
                    conditions={
                        "age": ("lt", 25),
                        "insurance_group": ("gte", 35),
                    },
                    effect="multiply",
                    value=1.30,
                ),
                # Reward homeowners with long NCD
                Interaction(
                    conditions={
                        "is_homeowner": ("eq", True),
                        "ncd_years": ("gte", 7),
                    },
                    effect="multiply",
                    value=0.94,
                ),
            ],
        ),
        gold_commission_rate=0.13,
        silver_commission_rate=0.125,
        bronze_commission_rate=0.11,
        fixed_fee_per_policy=0.0,
        gold_multiplier=1.20,
        silver_multiplier=1.00,
        bronze_multiplier=0.85,
    ),

    # ── 2. Vanguard Specialty ────────────────────────────────────────────────
    # Young / new driver specialist. Lower young-driver loading, higher base.
    UnderwriterProfile(
        insurer=InsurerProfile(
            name="Vanguard Specialty",
            strategy="glm",
            base_rate=1100,
            noise_sigma=0.07,

            # Age – young-friendly curve
            age_curve="young_friendly",
            young_driver_loading=0.85,
            senior_discount=1.15,   # not competitive on older drivers

            # Gender / marital
            female_discount=0.95,
            married_discount=0.98,

            # Employment
            employment_factors={
                "employed": 0.98,
                "self_employed": 1.05,
                "retired": 1.10,
                "unemployed": 1.08,
                "student_full_time": 0.95,
                "student_part_time": 0.97,
            },
            occupation_group_factors={
                "Professional": 0.95,
                "Management": 0.96,
                "Elementary": 1.02,
            },

            # Licence
            licence_type_factors={
                "full_uk": 1.00,
                "provisional": 1.15,  # much friendlier to provisional
                "eu": 1.08,
                "international": 1.12,
            },
            licence_years_power=0.35,  # less penalty for short licence

            # NCD – less aggressive discount (younger drivers have less NCD)
            ncd_max_discount=0.55,
            ncd_protected_bonus=0.01,

            # New biz
            new_business_discount=0.92,
            loyalty_discount=1.00,

            # Vehicle group – moderate
            vehicle_group_power=0.90,
            vehicle_group_cap=3.0,

            preferred_makes={
                "VAUXHALL": 0.95, "FORD": 0.95, "FIAT": 0.96,
                "SEAT": 0.96, "CITROEN": 0.97, "PEUGEOT": 0.97,
            },

            body_type_factors={
                "hatchback": 0.92,
                "saloon": 0.98,
                "estate": 1.02,
                "suv": 1.08,
                "convertible": 1.15,
                "coupe": 1.12,
            },

            # Fuel
            ev_discount=0.93,
            fuel_factors={
                "petrol": 1.00,
                "diesel": 1.03,
                "electric": 0.93,
                "hybrid": 0.96,
            },

            # Vehicle age – prefers younger, cheaper cars
            old_vehicle_loading=1.10,
            new_vehicle_discount=0.95,
            classic_vehicle_factor=1.25,

            engine_cc_factors=[
                (1200, 0.90),
                (1600, 1.00),
                (2000, 1.15),
                (3000, 1.35),
            ],

            # Modifications & security
            modification_loading=1.30,
            tracker_discount=0.88,  # strong telematics-style discount
            alarm_discount=0.95,
            no_security_loading=1.08,

            overnight_factors={
                "garage": 0.90,
                "driveway": 0.95,
                "public_road": 1.06,
                "car_park": 1.04,
            },

            mileage_power=0.45,

            tpft_discount=0.76,
            tpo_discount=0.60,

            excess_credit_rate=1.05,

            urban_loading=1.05,
            regional_factors={
                "E": 1.08, "EC": 1.10, "N": 1.06, "NW": 1.05,
                "SE": 1.04, "SW": 1.02, "W": 1.06, "WC": 1.09,
                "M": 1.04, "L": 1.03,
            },

            # Claims – more tolerant of young drivers' first claims
            at_fault_loading=0.12,
            not_at_fault_loading=0.02,
            claim_recency_weight=0.01,
            pi_claim_loading=0.06,

            conviction_loading=0.08,
            speeding_loading=0.03,

            additional_driver_loading=0.03,
            young_named_driver_loading=0.06,

            homeowner_discount=0.98,
            uk_residency_loading=1.04,

            business_use_loading=1.15,
            commuting_loading=1.03,

            # Decline rules – specialises in 17-45
            min_age=17,
            max_age=45,
            max_insurance_group=40,
            decline_provisional=False,
            decline_modified=False,
            decline_imported=False,
            decline_max_claims=3,
            decline_max_convictions=3,
            decline_max_points=9,
            max_vehicle_age=15,
            min_vehicle_value=0,
            max_vehicle_value=50000,
            decline_drink_drive=True,
            decline_major_conviction=True,

            minimum_premiums={
                "comprehensive": 280,
                "third_party_fire_and_theft": 240,
                "third_party_only": 210,
            },
            min_premium_young=250,
            min_premium_business=320,

            interactions=[
                # Core sweet spot: young driver with tracker and low mileage
                Interaction(
                    conditions={
                        "age": ("lt", 25),
                        "has_tracker": ("eq", True),
                        "annual_mileage": ("lt", 8000),
                    },
                    effect="multiply",
                    value=0.78,
                ),
                # New/young driver with clean record
                Interaction(
                    conditions={
                        "age": ("lt", 30),
                        "claim_count": ("eq", 0),
                        "conviction_count": ("eq", 0),
                    },
                    effect="multiply",
                    value=0.88,
                ),
                # Decline older drivers in high groups
                Interaction(
                    conditions={
                        "age": ("gte", 40),
                        "insurance_group": ("gte", 35),
                    },
                    effect="multiply",
                    value=1.25,
                ),
                # Hatchback discount for under-25s
                Interaction(
                    conditions={
                        "age": ("lt", 25),
                        "body_type": ("eq", "hatchback"),
                        "insurance_group": ("lte", 20),
                    },
                    effect="multiply",
                    value=0.90,
                ),
            ],
        ),
        gold_commission_rate=0.16,
        silver_commission_rate=0.15,
        bronze_commission_rate=0.14,
        fixed_fee_per_policy=0.0,
        gold_multiplier=1.22,
        silver_multiplier=1.00,
        bronze_multiplier=0.84,
    ),

    # ── 3. Meridian Mutual ───────────────────────────────────────────────────
    # Over-50s and rural specialist. Strong NCD, low commission + fixed fee.
    UnderwriterProfile(
        insurer=InsurerProfile(
            name="Meridian Mutual",
            strategy="glm",
            base_rate=980,
            noise_sigma=0.05,

            # Age – mature specialist curve
            age_curve="mature_specialist",
            young_driver_loading=1.45,
            senior_discount=0.85,

            # Gender / marital
            female_discount=0.96,
            married_discount=0.92,

            # Employment
            employment_factors={
                "employed": 0.96,
                "self_employed": 1.00,
                "retired": 0.88,
                "unemployed": 1.25,
                "student_full_time": 1.35,
                "student_part_time": 1.20,
            },
            occupation_group_factors={
                "Professional": 0.88,
                "Management": 0.90,
                "Skilled Trades": 0.95,
                "Elementary": 1.08,
                "Sales": 1.04,
            },

            # Licence
            licence_type_factors={
                "full_uk": 1.00,
                "provisional": 1.50,
                "eu": 1.12,
                "international": 1.18,
            },
            licence_years_power=0.55,

            # NCD – very strong discount for long NCD holders
            ncd_max_discount=0.65,
            ncd_protected_bonus=0.03,

            new_business_discount=0.97,
            loyalty_discount=0.90,  # strong loyalty discount

            # Vehicle group
            vehicle_group_power=1.05,
            vehicle_group_cap=3.5,

            preferred_makes={
                "TOYOTA": 0.93, "HONDA": 0.94, "MAZDA": 0.95,
                "SKODA": 0.95, "VOLVO": 0.94, "SUBARU": 0.96,
            },

            body_type_factors={
                "hatchback": 0.95,
                "saloon": 0.94,
                "estate": 0.93,
                "suv": 1.02,
                "convertible": 1.06,
                "coupe": 1.08,
            },

            ev_discount=0.92,
            fuel_factors={
                "petrol": 1.00,
                "diesel": 1.01,
                "electric": 0.92,
                "hybrid": 0.95,
            },

            old_vehicle_loading=0.98,  # tolerant of older vehicles
            new_vehicle_discount=0.98,
            classic_vehicle_factor=0.95,  # actually likes classics

            engine_cc_factors=[
                (1200, 0.93),
                (1600, 1.00),
                (2000, 1.06),
                (3000, 1.14),
            ],

            modification_loading=1.25,
            tracker_discount=0.93,
            alarm_discount=0.95,
            no_security_loading=1.10,

            overnight_factors={
                "garage": 0.85,
                "driveway": 0.92,
                "public_road": 1.12,
                "car_park": 1.08,
            },

            mileage_power=0.55,

            tpft_discount=0.77,
            tpo_discount=0.62,

            excess_credit_rate=0.95,

            # Rural-friendly – lower urban loading
            urban_loading=1.12,
            regional_factors={
                "E": 1.14, "EC": 1.18, "N": 1.12, "NW": 1.10,
                "SE": 1.08, "SW": 0.95, "W": 1.10, "WC": 1.16,
                "M": 1.08, "B": 1.06, "L": 1.07,
                "EX": 0.93, "PL": 0.93, "TR": 0.92,  # rural SW discounts
                "LL": 0.94, "SY": 0.94, "SA": 0.94,  # rural Wales
                "YO": 0.95, "HG": 0.94, "DL": 0.95,  # rural N. England
            },

            # Claims – strict
            at_fault_loading=0.18,
            not_at_fault_loading=0.04,
            claim_recency_weight=0.03,
            pi_claim_loading=0.10,

            conviction_loading=0.12,
            speeding_loading=0.05,

            additional_driver_loading=0.05,
            young_named_driver_loading=0.18,

            homeowner_discount=0.92,
            uk_residency_loading=1.08,

            business_use_loading=1.10,
            commuting_loading=1.03,

            # Decline rules – no young drivers
            min_age=30,
            max_age=99,
            max_insurance_group=50,
            decline_provisional=True,
            decline_modified=True,
            decline_imported=False,
            decline_max_claims=3,
            decline_max_convictions=2,
            decline_max_points=9,
            max_vehicle_age=25,
            min_vehicle_value=0,
            max_vehicle_value=80000,
            decline_drink_drive=True,
            decline_major_conviction=True,

            minimum_premiums={
                "comprehensive": 200,
                "third_party_fire_and_theft": 170,
                "third_party_only": 150,
            },
            min_premium_young=350,
            min_premium_business=250,

            interactions=[
                # Core sweet spot: mature, married, homeowner, long NCD
                Interaction(
                    conditions={
                        "age": ("gte", 50),
                        "marital_status": ("in", {"married", "civil_partnership"}),
                        "is_homeowner": ("eq", True),
                        "ncd_years": ("gte", 9),
                    },
                    effect="multiply",
                    value=0.82,
                ),
                # Rural garage = extra discount
                Interaction(
                    conditions={
                        "overnight_location": ("eq", "garage"),
                        "annual_mileage": ("lt", 8000),
                    },
                    effect="multiply",
                    value=0.92,
                ),
                # Penalise young named drivers heavily
                Interaction(
                    conditions={
                        "has_young_named_driver": ("eq", True),
                    },
                    effect="multiply",
                    value=1.35,
                ),
                # Classic vehicle enthusiast discount
                Interaction(
                    conditions={
                        "vehicle_age": ("gte", 15),
                        "annual_mileage": ("lt", 5000),
                        "overnight_location": ("eq", "garage"),
                    },
                    effect="multiply",
                    value=0.85,
                ),
            ],
        ),
        gold_commission_rate=0.10,
        silver_commission_rate=0.095,
        bronze_commission_rate=0.09,
        fixed_fee_per_policy=25.0,
        gold_multiplier=1.18,
        silver_multiplier=1.00,
        bronze_multiplier=0.86,
    ),

    # ── 4. Pinnacle Cover ────────────────────────────────────────────────────
    # High-value / performance vehicle specialist. Prefers groups 25+.
    UnderwriterProfile(
        insurer=InsurerProfile(
            name="Pinnacle Cover",
            strategy="glm",
            base_rate=1150,
            noise_sigma=0.06,

            # Age – standard curve
            age_curve="standard",
            young_driver_loading=1.20,
            senior_discount=0.95,

            # Gender / marital
            female_discount=0.97,
            married_discount=0.95,

            # Employment
            employment_factors={
                "employed": 0.95,
                "self_employed": 0.97,
                "retired": 0.96,
                "unemployed": 1.20,
                "student_full_time": 1.25,
                "student_part_time": 1.15,
            },
            occupation_group_factors={
                "Professional": 0.88,
                "Management": 0.88,
                "Skilled Trades": 1.00,
                "Elementary": 1.10,
                "Sales": 0.98,
            },

            # Licence
            licence_type_factors={
                "full_uk": 1.00,
                "provisional": 1.50,
                "eu": 1.08,
                "international": 1.10,
            },
            licence_years_power=0.50,

            # NCD
            ncd_max_discount=0.58,
            ncd_protected_bonus=0.02,

            new_business_discount=0.96,
            loyalty_discount=0.94,

            # Vehicle group – flatter power curve (less penalty for high groups)
            vehicle_group_power=0.80,
            vehicle_group_cap=3.0,

            preferred_makes={
                "BMW": 0.90, "MERCEDES": 0.90, "AUDI": 0.91,
                "PORSCHE": 0.88, "JAGUAR": 0.91, "LAND ROVER": 0.92,
                "TESLA": 0.90, "LEXUS": 0.92, "VOLVO": 0.94,
                "ALFA ROMEO": 0.93,
            },

            body_type_factors={
                "hatchback": 1.05,
                "saloon": 0.95,
                "estate": 0.97,
                "suv": 0.94,
                "convertible": 0.96,
                "coupe": 0.95,
            },

            ev_discount=0.90,
            fuel_factors={
                "petrol": 1.00,
                "diesel": 1.01,
                "electric": 0.90,
                "hybrid": 0.94,
            },

            old_vehicle_loading=1.15,
            new_vehicle_discount=0.92,
            classic_vehicle_factor=1.05,

            engine_cc_factors=[
                (1200, 1.05),   # not competitive on small engines
                (1600, 1.00),
                (2000, 0.98),   # sweet spot
                (3000, 1.04),
            ],

            modification_loading=1.15,   # more tolerant of mods
            tracker_discount=0.90,
            alarm_discount=0.93,
            no_security_loading=1.15,

            overnight_factors={
                "garage": 0.85,
                "driveway": 0.93,
                "public_road": 1.18,
                "car_park": 1.10,
            },

            mileage_power=0.50,

            tpft_discount=0.80,
            tpo_discount=0.65,

            excess_credit_rate=0.90,

            urban_loading=1.10,
            regional_factors={
                "E": 1.10, "EC": 1.14, "N": 1.08, "NW": 1.06,
                "SE": 1.05, "SW": 1.02, "W": 1.08, "WC": 1.12,
                "M": 1.04, "B": 1.03, "L": 1.04,
                "GU": 0.97, "RG": 0.97, "KT": 0.98,  # Surrey/Berks affluent
            },

            # Claims
            at_fault_loading=0.16,
            not_at_fault_loading=0.03,
            claim_recency_weight=0.02,
            pi_claim_loading=0.09,

            conviction_loading=0.12,
            speeding_loading=0.05,

            additional_driver_loading=0.05,
            young_named_driver_loading=0.15,

            homeowner_discount=0.93,
            uk_residency_loading=1.06,

            business_use_loading=1.08,
            commuting_loading=1.02,

            # Decline rules – high-value focus
            min_age=21,
            max_age=80,
            max_insurance_group=50,
            decline_provisional=True,
            decline_modified=False,
            decline_imported=False,
            decline_max_claims=3,
            decline_max_convictions=2,
            decline_max_points=9,
            max_vehicle_age=18,
            min_vehicle_value=15000,
            max_vehicle_value=150000,
            decline_drink_drive=True,
            decline_major_conviction=True,

            minimum_premiums={
                "comprehensive": 400,
                "third_party_fire_and_theft": 350,
                "third_party_only": 300,
            },
            min_premium_young=500,
            min_premium_business=450,

            interactions=[
                # Sweet spot: premium vehicle, experienced driver, garaged
                Interaction(
                    conditions={
                        "insurance_group": ("gte", 25),
                        "age": ("gte", 30),
                        "overnight_location": ("eq", "garage"),
                    },
                    effect="multiply",
                    value=0.85,
                ),
                # Preferred make + tracker discount
                Interaction(
                    conditions={
                        "insurance_group": ("gte", 30),
                        "has_tracker": ("eq", True),
                    },
                    effect="multiply",
                    value=0.88,
                ),
                # Penalise low-value vehicles (not their target)
                Interaction(
                    conditions={
                        "vehicle_value": ("lt", 20000),
                        "insurance_group": ("lt", 20),
                    },
                    effect="multiply",
                    value=1.20,
                ),
                # Young driver + high group = decline
                Interaction(
                    conditions={
                        "age": ("lt", 25),
                        "insurance_group": ("gte", 40),
                    },
                    effect="decline",
                ),
            ],
        ),
        gold_commission_rate=0.14,
        silver_commission_rate=0.12,
        bronze_commission_rate=0.10,
        fixed_fee_per_policy=0.0,
        gold_multiplier=1.22,
        silver_multiplier=1.00,
        bronze_multiplier=0.84,
    ),

    # ── 5. Sentinel Insurance ────────────────────────────────────────────────
    # Broad acceptance, rarely declines, but expensive. The safety-net writer.
    UnderwriterProfile(
        insurer=InsurerProfile(
            name="Sentinel Insurance",
            strategy="glm",
            base_rate=1350,
            noise_sigma=0.08,

            # Age – flat middle curve
            age_curve="flat_middle",
            young_driver_loading=1.00,
            senior_discount=1.00,

            # Gender / marital – almost no discrimination
            female_discount=0.99,
            married_discount=0.98,

            # Employment
            employment_factors={
                "employed": 0.99,
                "self_employed": 1.01,
                "retired": 0.98,
                "unemployed": 1.05,
                "student_full_time": 1.03,
                "student_part_time": 1.02,
            },
            occupation_group_factors={
                "Professional": 0.97,
                "Management": 0.97,
                "Elementary": 1.02,
            },

            # Licence – very tolerant
            licence_type_factors={
                "full_uk": 1.00,
                "provisional": 1.20,
                "eu": 1.05,
                "international": 1.08,
            },
            licence_years_power=0.30,

            # NCD – moderate
            ncd_max_discount=0.55,
            ncd_protected_bonus=0.01,

            new_business_discount=0.98,
            loyalty_discount=0.98,

            # Vehicle group – gentle curve
            vehicle_group_power=0.85,
            vehicle_group_cap=4.0,

            preferred_makes={},  # no preferences

            body_type_factors={
                "hatchback": 0.98,
                "saloon": 0.99,
                "estate": 1.00,
                "suv": 1.03,
                "convertible": 1.05,
                "coupe": 1.04,
            },

            ev_discount=0.97,
            fuel_factors={
                "petrol": 1.00,
                "diesel": 1.01,
                "electric": 0.97,
                "hybrid": 0.98,
            },

            old_vehicle_loading=1.03,
            new_vehicle_discount=0.99,
            classic_vehicle_factor=1.05,

            engine_cc_factors=[
                (1200, 0.97),
                (1600, 1.00),
                (2000, 1.04),
                (3000, 1.08),
            ],

            modification_loading=1.10,  # tolerant of mods
            tracker_discount=0.96,
            alarm_discount=0.98,
            no_security_loading=1.03,

            overnight_factors={
                "garage": 0.95,
                "driveway": 0.98,
                "public_road": 1.04,
                "car_park": 1.02,
            },

            mileage_power=0.40,

            tpft_discount=0.78,
            tpo_discount=0.63,

            excess_credit_rate=1.00,

            urban_loading=1.04,
            regional_factors={
                "E": 1.04, "EC": 1.06, "N": 1.03, "NW": 1.03,
                "SE": 1.02, "SW": 1.01, "W": 1.03, "WC": 1.05,
            },

            # Claims – very tolerant
            at_fault_loading=0.10,
            not_at_fault_loading=0.02,
            claim_recency_weight=0.01,
            pi_claim_loading=0.05,

            conviction_loading=0.06,
            speeding_loading=0.02,

            additional_driver_loading=0.02,
            young_named_driver_loading=0.05,

            homeowner_discount=0.98,
            uk_residency_loading=1.02,

            business_use_loading=1.05,
            commuting_loading=1.02,

            # Decline rules – accepts almost everyone
            min_age=17,
            max_age=99,
            max_insurance_group=50,
            decline_provisional=False,
            decline_modified=False,
            decline_imported=False,
            decline_max_claims=99,
            decline_max_convictions=99,
            decline_max_points=99,
            max_vehicle_age=99,
            min_vehicle_value=0,
            max_vehicle_value=999999,
            decline_drink_drive=False,
            decline_major_conviction=False,

            minimum_premiums={
                "comprehensive": 350,
                "third_party_fire_and_theft": 300,
                "third_party_only": 270,
            },
            min_premium_young=400,
            min_premium_business=380,

            interactions=[
                # Slight discount for very clean risks (reward, not sweet-spot)
                Interaction(
                    conditions={
                        "claim_count": ("eq", 0),
                        "conviction_count": ("eq", 0),
                        "ncd_years": ("gte", 5),
                    },
                    effect="multiply",
                    value=0.92,
                ),
                # Modest surcharge for non-standard risks they still accept
                Interaction(
                    conditions={
                        "claim_count": ("gte", 3),
                        "conviction_count": ("gte", 2),
                    },
                    effect="multiply",
                    value=1.15,
                ),
            ],
        ),
        gold_commission_rate=0.175,
        silver_commission_rate=0.16,
        bronze_commission_rate=0.145,
        fixed_fee_per_policy=0.0,
        gold_multiplier=1.20,
        silver_multiplier=1.00,
        bronze_multiplier=0.85,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Lookup helpers
# ─────────────────────────────────────────────────────────────────────────────

PANEL_UNDERWRITER_MAP = {uw.insurer.name: uw for uw in PANEL_UNDERWRITERS}

UNDERWRITER_COLUMNS = [
    uw.insurer.name.lower().replace(" ", "_") for uw in PANEL_UNDERWRITERS
]

TIER_NAMES = ["gold", "silver", "bronze"]


# ─────────────────────────────────────────────────────────────────────────────
# Broker Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BrokerConfig:
    name: str = "Acme Brokers"
    broker_fee: float = 35.0            # arrangement fee charged to customer
    cancellation_fee: float = 50.0      # fee if customer cancels mid-term
    credit_apr: float = 14.9            # APR for monthly payment option
    monthly_admin_fee: float = 0.0      # per-month admin charge


DEFAULT_BROKER = BrokerConfig()
