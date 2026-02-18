"""Shared constants, lookup tables, and assumption-based distributions."""

# ── Channel distribution (aggregator market shares) ──────────────────────────
CHANNEL_WEIGHTS = {
    "compare_the_market": 0.30,
    "moneysupermarket": 0.25,
    "confused": 0.20,
    "gocompare": 0.15,
    "direct_web": 0.07,
    "direct_phone": 0.02,
    "broker": 0.01,
}

AGGREGATOR_CHANNELS = {"compare_the_market", "moneysupermarket", "confused", "gocompare"}

AGGREGATOR_PREFIX = {
    "compare_the_market": "CTM",
    "moneysupermarket": "MSM",
    "confused": "CON",
    "gocompare": "GCO",
}

# ── Cover type ────────────────────────────────────────────────────────────────
COVER_TYPE_WEIGHTS = {"comprehensive": 0.85, "third_party_fire_and_theft": 0.10, "third_party_only": 0.05}

# ── Payment frequency ────────────────────────────────────────────────────────
PAYMENT_FREQ_WEIGHTS = {"annual": 0.45, "monthly": 0.55}

# ── Voluntary excess options and base weights ────────────────────────────────
VOLUNTARY_EXCESS_OPTIONS = [0, 100, 150, 250, 500, 1000]
VOLUNTARY_EXCESS_BASE_WEIGHTS = [0.15, 0.25, 0.05, 0.35, 0.15, 0.05]

# ── Employment status by age band ────────────────────────────────────────────
# Keys: (min_age, max_age) → {status: weight}
EMPLOYMENT_BY_AGE = {
    (17, 21): {"student_full_time": 0.55, "employed": 0.25, "unemployed": 0.10, "self_employed": 0.03, "student_part_time": 0.05, "house_person": 0.02},
    (21, 25): {"employed": 0.55, "student_full_time": 0.20, "unemployed": 0.08, "self_employed": 0.07, "student_part_time": 0.05, "house_person": 0.03, "not_employed_due_to_disability": 0.02},
    (25, 35): {"employed": 0.65, "self_employed": 0.13, "house_person": 0.08, "unemployed": 0.05, "student_full_time": 0.03, "student_part_time": 0.02, "not_employed_due_to_disability": 0.03, "voluntary_work": 0.01},
    (35, 50): {"employed": 0.65, "self_employed": 0.15, "house_person": 0.07, "unemployed": 0.04, "not_employed_due_to_disability": 0.04, "retired": 0.02, "voluntary_work": 0.02, "student_part_time": 0.01},
    (50, 60): {"employed": 0.55, "self_employed": 0.14, "retired": 0.12, "house_person": 0.06, "not_employed_due_to_disability": 0.06, "unemployed": 0.04, "voluntary_work": 0.03},
    (60, 65): {"retired": 0.40, "employed": 0.35, "self_employed": 0.10, "house_person": 0.05, "not_employed_due_to_disability": 0.05, "voluntary_work": 0.03, "unemployed": 0.02},
    (65, 100): {"retired": 0.82, "employed": 0.06, "self_employed": 0.04, "house_person": 0.03, "not_employed_due_to_disability": 0.03, "voluntary_work": 0.02},
}

# ── Homeownership rate by age ────────────────────────────────────────────────
HOMEOWNER_RATE_BY_AGE = {
    (17, 25): 0.08, (25, 35): 0.35, (35, 45): 0.55,
    (45, 55): 0.68, (55, 65): 0.75, (65, 100): 0.78,
}

# ── Medical conditions rate by age ───────────────────────────────────────────
MEDICAL_RATE_BY_AGE = {
    (17, 30): 0.01, (30, 50): 0.02, (50, 65): 0.05, (65, 100): 0.08,
}

# ── Title conditional on gender + marital status ─────────────────────────────
TITLE_WEIGHTS = {
    ("male", "single"): {"mr": 0.97, "dr": 0.03},
    ("male", "married"): {"mr": 0.97, "dr": 0.03},
    ("male", "divorced"): {"mr": 0.97, "dr": 0.03},
    ("male", "widowed"): {"mr": 0.97, "dr": 0.03},
    ("male", "civil_partnership"): {"mr": 0.97, "dr": 0.03},
    ("male", "separated"): {"mr": 0.97, "dr": 0.03},
    ("male", "living_with_partner"): {"mr": 0.97, "dr": 0.03},
    ("female", "single"): {"miss": 0.45, "ms": 0.45, "dr": 0.05, "mrs": 0.05},
    ("female", "married"): {"mrs": 0.70, "ms": 0.18, "dr": 0.05, "miss": 0.07},
    ("female", "divorced"): {"ms": 0.50, "mrs": 0.30, "miss": 0.13, "dr": 0.07},
    ("female", "widowed"): {"mrs": 0.65, "ms": 0.25, "dr": 0.05, "miss": 0.05},
    ("female", "civil_partnership"): {"ms": 0.50, "mrs": 0.30, "miss": 0.13, "dr": 0.07},
    ("female", "separated"): {"ms": 0.45, "mrs": 0.35, "miss": 0.13, "dr": 0.07},
    ("female", "living_with_partner"): {"ms": 0.40, "miss": 0.35, "mrs": 0.18, "dr": 0.07},
}

# ── Top 200 UK surnames (frequency-weighted) ─────────────────────────────────
UK_SURNAMES = [
    "Smith", "Jones", "Williams", "Taylor", "Brown", "Davies", "Evans", "Wilson",
    "Thomas", "Roberts", "Johnson", "Lewis", "Walker", "Robinson", "Wood", "Thompson",
    "White", "Watson", "Jackson", "Wright", "Green", "Harris", "Cooper", "King",
    "Lee", "Martin", "Clarke", "James", "Morgan", "Hughes", "Edwards", "Hill",
    "Moore", "Clark", "Harrison", "Scott", "Young", "Morris", "Hall", "Ward",
    "Turner", "Carter", "Phillips", "Mitchell", "Patel", "Adams", "Campbell",
    "Anderson", "Allen", "Cook", "Bailey", "Palmer", "Stevens", "Bell", "Collins",
    "Richardson", "Cox", "Howard", "Murphy", "Price", "Bennett", "Griffiths",
    "Kelly", "Simpson", "Marshall", "Russell", "Gray", "Mills", "Murray", "Hunt",
    "Foster", "Webb", "Powell", "Butler", "Barnes", "Holmes", "Owen", "Reid",
    "Fisher", "Ellis", "Chapman", "Dixon", "Gordon", "Knight", "Grant", "Henderson",
    "Ross", "Stone", "Graham", "Ferguson", "Watts", "Rose", "Robertson", "Spencer",
    "Gibson", "Pearson", "Walsh", "Day", "Brooks", "Hamilton", "Harvey", "Hart",
    "Ford", "Fox", "Mason", "Kennedy", "Andrews", "Reynolds", "McDonald", "Tucker",
    "Cameron", "Burke", "Barker", "Holland", "Cole", "Perry", "Shaw", "Long",
    "Sullivan", "Ball", "George", "Harper", "Wells", "Armstrong", "Gardner", "Lane",
    "West", "Lawrence", "May", "Pearce", "Burns", "Carr", "Jenkins", "Hussain",
    "Ali", "Khan", "Ahmed", "Singh", "Begum", "Kaur", "Rahman", "Islam",
    "O'Brien", "O'Connor", "O'Neill", "Ryan", "Byrne", "Doyle", "McCarthy", "Lynch",
    "Doherty", "Quinn", "Gallagher", "Brennan", "Walsh", "Duffy", "Farrell", "Casey",
    "Lowe", "Rice", "Chambers", "Dawson", "Dean", "Cross", "Jordan", "Sharp",
    "Swift", "Todd", "Winter", "Bishop", "Porter", "Hood", "Rowe", "Carpenter",
    "Berry", "Poole", "Howe", "Reeves", "Page", "Francis", "Curtis", "Barber",
    "French", "Bolton", "Fleming", "Norton", "Payne", "Wilkinson", "Davidson", "Crawford",
    "Arnold", "Booth", "Hardy", "Newton", "Lloyd", "Warner", "Nicholson", "Parsons",
]

# Surname weights: roughly Zipf-like decay
_n = len(UK_SURNAMES)
UK_SURNAME_WEIGHTS = [1.0 / (i + 1) ** 0.6 for i in range(_n)]
_sw_total = sum(UK_SURNAME_WEIGHTS)
UK_SURNAME_WEIGHTS = [w / _sw_total for w in UK_SURNAME_WEIGHTS]

# ── Common UK street names ───────────────────────────────────────────────────
UK_STREET_NAMES = [
    "High Street", "Station Road", "Church Lane", "Park Road", "Victoria Road",
    "Manor Road", "Church Street", "London Road", "Green Lane", "The Crescent",
    "Mill Lane", "Springfield Road", "King Street", "Queen Street", "New Road",
    "Grange Road", "Stanley Road", "Main Street", "The Avenue", "School Lane",
    "Meadow Close", "Oak Drive", "Elm Road", "Beech Avenue", "Cedar Close",
    "Willow Way", "Birch Lane", "Maple Drive", "Ash Road", "Pine Close",
    "Richmond Road", "Albert Road", "Windsor Road", "Bridge Street", "Market Street",
    "Chapel Lane", "Orchard Road", "Hillside", "The Green", "Brookside",
    "Rosemary Lane", "Primrose Drive", "Lavender Close", "Hawthorn Avenue", "Poplar Road",
    "Sycamore Drive", "Holly Close", "Ivy Lane", "Jasmine Court", "Heather Way",
]

# ── Previous insurer market shares ───────────────────────────────────────────
PREVIOUS_INSURERS = {
    "Admiral": 0.14, "Direct Line": 0.12, "Aviva": 0.10, "AXA": 0.08,
    "LV=": 0.06, "Churchill": 0.05, "Hastings Direct": 0.05, "esure": 0.04,
    "NFU Mutual": 0.03, "RAC": 0.03, "AA": 0.03, "Saga": 0.02,
    "Zurich": 0.02, "RSA": 0.02, "Allianz": 0.02, "More Than": 0.02,
    "Swinton": 0.02, "Privilege": 0.02, "Co-op Insurance": 0.02,
    "Other": 0.11,
}

# ── DVLA conviction codes mapped from MoJ offence descriptions ───────────────
# conviction_code → (description, min_points, max_points, typical_fine, ban_months_range)
CONVICTION_CODES = {
    "SP30": ("Exceeding statutory speed limit on a public road", 3, 6, 150, None),
    "SP50": ("Exceeding speed limit on a motorway", 3, 6, 150, None),
    "SP10": ("Exceeding goods vehicle speed limits", 3, 6, 100, None),
    "IN10": ("Using a vehicle uninsured against third party risks", 6, 8, 300, None),
    "CU80": ("Using a handheld mobile phone while driving", 6, 6, 200, None),
    "DR10": ("Driving or attempting to drive with alcohol above limit", 3, 11, 500, (12, 36)),
    "DR80": ("Driving or attempting to drive when unfit through drugs", 3, 11, 500, (12, 36)),
    "CD10": ("Driving without due care and attention", 3, 9, 300, None),
    "CD30": ("Driving without due care and attention — causing death", 3, 11, 1000, (12, 60)),
    "DD40": ("Dangerous driving", 3, 11, 1500, (12, 24)),
    "AC10": ("Failing to stop after an accident", 5, 10, 500, None),
    "TS10": ("Failing to comply with traffic light signals", 3, 3, 100, None),
    "MR09": ("Reckless or dangerous driving", 3, 11, 1000, (12, 24)),
    "LC20": ("Driving otherwise than in accordance with a licence", 3, 6, 200, None),
    "MS90": ("Failure to give information as to identity of driver", 6, 6, 300, None),
}

# Weights for sampling conviction codes (from MoJ data mapping)
CONVICTION_CODE_WEIGHTS = {
    "SP30": 0.40, "SP50": 0.10, "SP10": 0.02,
    "IN10": 0.12, "CU80": 0.08, "DR10": 0.06, "DR80": 0.02,
    "CD10": 0.05, "AC10": 0.02, "TS10": 0.03,
    "MS90": 0.05, "LC20": 0.02, "DD40": 0.01, "CD30": 0.01, "MR09": 0.01,
}

# ── Claim type distribution ──────────────────────────────────────────────────
CLAIM_TYPE_WEIGHTS = {
    "accident": 0.72, "windscreen": 0.10, "theft": 0.06,
    "vandalism": 0.04, "storm_flood": 0.03, "personal_injury": 0.03,
    "fire": 0.01, "other": 0.01,
}

# Fault probability by claim type
FAULT_WEIGHTS_BY_TYPE = {
    "accident": {"at_fault": 0.55, "not_at_fault": 0.40, "pending": 0.05},
    "windscreen": {"not_at_fault": 0.95, "at_fault": 0.05},
    "theft": {"not_at_fault": 0.98, "pending": 0.02},
    "vandalism": {"not_at_fault": 0.97, "pending": 0.03},
    "storm_flood": {"not_at_fault": 0.99, "pending": 0.01},
    "fire": {"not_at_fault": 0.95, "pending": 0.05},
    "personal_injury": {"not_at_fault": 0.60, "at_fault": 0.35, "pending": 0.05},
    "other": {"not_at_fault": 0.70, "at_fault": 0.20, "pending": 0.10},
}

# Mean claim amount by type (GBP)
CLAIM_AMOUNT_BY_TYPE = {
    "accident": (2000, 1.0),       # (mean, log_sigma) for log-normal
    "windscreen": (350, 0.3),
    "theft": (5000, 0.8),
    "vandalism": (1200, 0.6),
    "storm_flood": (2500, 0.7),
    "fire": (8000, 1.0),
    "personal_injury": (4000, 1.2),
    "other": (1500, 0.8),
}

# ── Add-on selection rates ───────────────────────────────────────────────────
ADDON_RATES = {
    "breakdown_cover": 0.25,
    "legal_expenses": 0.20,
    "motor_legal_protection": 0.15,
    "key_cover": 0.15,
    "courtesy_car": 0.12,
    "windscreen_cover": 0.10,
    "excess_protection": 0.10,
    "no_claims_step_back": 0.08,
    "personal_accident": 0.05,
    "personal_belongings": 0.03,
    "tools_in_transit": 0.01,
}

BREAKDOWN_LEVELS = {"roadside": 0.40, "national_recovery": 0.35, "european": 0.25}

# ── Vehicle body type → doors/seats ──────────────────────────────────────────
BODY_TYPE_DOORS = {
    "hatchback": {3: 0.20, 5: 0.80},
    "saloon": {4: 1.0},
    "estate": {5: 1.0},
    "suv": {5: 1.0},
    "convertible": {2: 0.80, 3: 0.20},
    "coupe": {2: 0.50, 3: 0.50},
    "mpv": {5: 1.0},
    "pickup": {4: 0.70, 2: 0.30},
    "van": {3: 0.50, 5: 0.50},
    "other": {5: 0.70, 3: 0.30},
}

BODY_TYPE_SEATS = {
    "hatchback": {5: 0.95, 4: 0.05},
    "saloon": {5: 1.0},
    "estate": {5: 0.85, 7: 0.15},
    "suv": {5: 0.75, 7: 0.25},
    "convertible": {2: 0.30, 4: 0.70},
    "coupe": {2: 0.15, 4: 0.85},
    "mpv": {5: 0.30, 7: 0.70},
    "pickup": {5: 0.60, 2: 0.40},
    "van": {2: 0.40, 5: 0.60},
    "other": {5: 0.80, 2: 0.20},
}

# ── Overnight location (conditioned on urban/rural) ─────────────────────────
OVERNIGHT_URBAN = {
    "street_near_home": 0.40, "driveway": 0.25, "garage": 0.08,
    "car_park": 0.15, "private_property": 0.07, "street_away_from_home": 0.05,
}
OVERNIGHT_RURAL = {
    "driveway": 0.45, "garage": 0.25, "street_near_home": 0.15,
    "private_property": 0.08, "car_park": 0.05, "street_away_from_home": 0.02,
}

DAYTIME_COMMUTING = {
    "office_car_park": 0.45, "street_near_work": 0.25,
    "public_car_park": 0.15, "at_home": 0.10, "customers_premises": 0.05,
}
DAYTIME_NO_COMMUTING = {
    "at_home": 0.70, "public_car_park": 0.10, "street_near_work": 0.10,
    "office_car_park": 0.05, "other": 0.05,
}

# ── Vehicle security by age ──────────────────────────────────────────────────
# (alarm, immobiliser) — factory-fitted rates by vehicle age
SECURITY_FACTORY_ALARM_RATE = {
    (0, 5): 0.90, (5, 10): 0.85, (10, 15): 0.75, (15, 20): 0.60, (20, 30): 0.40,
}
SECURITY_FACTORY_IMMOBILISER_RATE = {
    (0, 5): 0.98, (5, 10): 0.97, (10, 15): 0.95, (15, 20): 0.90, (20, 30): 0.80,
}

# ── Modification types ───────────────────────────────────────────────────────
MODIFICATION_TYPES = {
    "Alloy Wheels": 0.35, "Tinted Windows": 0.18, "Exhaust System": 0.10,
    "Body Kit": 0.08, "Engine Tuning": 0.06, "Spoiler": 0.05,
    "Lowered Suspension": 0.05, "Sound System": 0.04, "Dash Cam": 0.04,
    "Parking Sensors": 0.03, "Towbar": 0.02,
}

# ── UK region → city name lookup (simplified) ────────────────────────────────
REGION_CITIES = {
    "London": ["London"],
    "South East": ["Brighton", "Reading", "Southampton", "Oxford", "Canterbury", "Guildford", "Maidstone", "Portsmouth"],
    "South West": ["Bristol", "Plymouth", "Exeter", "Bath", "Bournemouth", "Gloucester", "Swindon", "Taunton"],
    "East of England": ["Norwich", "Cambridge", "Ipswich", "Colchester", "Chelmsford", "Peterborough", "Luton", "Watford"],
    "East Midlands": ["Nottingham", "Leicester", "Derby", "Lincoln", "Northampton", "Mansfield"],
    "West Midlands": ["Birmingham", "Coventry", "Wolverhampton", "Stoke-on-Trent", "Worcester", "Hereford"],
    "Yorkshire and The Humber": ["Leeds", "Sheffield", "Bradford", "York", "Hull", "Doncaster", "Huddersfield"],
    "North West": ["Manchester", "Liverpool", "Preston", "Bolton", "Blackpool", "Chester", "Warrington"],
    "North East": ["Newcastle upon Tyne", "Sunderland", "Durham", "Middlesbrough", "Darlington"],
    "Wales": ["Cardiff", "Swansea", "Newport", "Wrexham", "Bangor", "Aberystwyth"],
    "Scotland": ["Edinburgh", "Glasgow", "Aberdeen", "Dundee", "Inverness", "Stirling", "Perth"],
    "Northern Ireland": ["Belfast", "Derry", "Lisburn", "Newry", "Bangor"],
}

# ── Insurance group rough estimates by vehicle value band ────────────────────
def estimate_insurance_group(value: int, engine_cc: int | None, fuel: str) -> int:
    """Rough insurance group 1-50 based on value + engine + fuel."""
    base = 1
    if value < 5000:
        base = 6
    elif value < 10000:
        base = 12
    elif value < 15000:
        base = 18
    elif value < 20000:
        base = 22
    elif value < 30000:
        base = 28
    elif value < 40000:
        base = 33
    elif value < 50000:
        base = 38
    else:
        base = 43

    # Engine size adjustment
    if engine_cc and engine_cc > 2000:
        base += 3
    elif engine_cc and engine_cc > 1500:
        base += 1

    # EV typically lower group
    if fuel in ("electric", "plug_in_hybrid"):
        base = max(base - 3, 1)

    return min(max(base, 1), 50)
