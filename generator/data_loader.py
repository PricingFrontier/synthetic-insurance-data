"""Load all processed parquet files into pre-indexed numpy arrays/dicts.

Every lookup used during generation is a pure numpy or dict operation —
no pandas in the hot path.
"""

from pathlib import Path

import numpy as np
import pandas as pd

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


class DistributionData:
    """Container for all processed distribution tables, pre-indexed for speed."""

    def __init__(self):
        self._load_all()

    # ── Loading & indexing (runs once) ────────────────────────────────────

    def _load_all(self):
        p = PROCESSED_DIR

        # ── Postcodes → column arrays + region index ──
        pc = pd.read_parquet(p / "postcode_lookup.parquet")
        self._pc_n = len(pc)
        self._pc_pcd = pc["pcd"].to_numpy(dtype=str, na_value="")
        self._pc_rgn = pc["rgn_name"].to_numpy(dtype=str, na_value="Unknown")
        self._pc_urban = pc["is_urban"].to_numpy(dtype=bool)
        # Pre-build region → array of row indices for fast same-region sampling
        self._pc_by_region: dict[str, np.ndarray] = {}
        for rgn in np.unique(self._pc_rgn):
            self._pc_by_region[rgn] = np.where(self._pc_rgn == rgn)[0]

        # ── Driver age × gender → numpy arrays ──
        dag = pd.read_parquet(p / "driver_age_gender.parquet")
        total_male = dag["full_male"].sum()
        total_female = dag["full_female"].sum()
        total = total_male + total_female
        self.p_male = float(total_male / total)
        self.ages = dag["age"].to_numpy(dtype=int)
        self.male_age_weights = (dag["full_male"] / total_male).to_numpy(dtype=float)
        self.female_age_weights = (dag["full_female"] / total_female).to_numpy(dtype=float)

        # ── Marital status → nested dict {(sex, age) → (statuses[], weights[])} ──
        ms = pd.read_parquet(p / "marital_status.parquet")
        self._marital_lookup: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
        for sex in ("male", "female"):
            sub = ms[ms["sex"] == sex]
            for _, row in sub.iterrows():
                lo, hi = int(row["age_min"]), int(row["age_max"])
                status = str(row["status"])
                w = float(row["weight"])
                for a in range(lo, hi + 1):
                    key = (sex, a)
                    if key not in self._marital_lookup:
                        self._marital_lookup[key] = ([], [])
                    self._marital_lookup[key][0].append(status)
                    self._marital_lookup[key][1].append(w)
        # Normalise weights and convert to arrays
        for key in self._marital_lookup:
            statuses, weights = self._marital_lookup[key]
            w = np.array(weights, dtype=float)
            s = w.sum()
            if s > 0:
                w /= s
            self._marital_lookup[key] = (np.array(statuses), w)

        # ── Occupation → {sex: (names[], codes[], weights[])} ──
        occ = pd.read_parquet(p / "occupation_dist.parquet")
        occ_unit = occ[occ["soc_level"] == 4]
        self._occ_lookup: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for sex_key in ("male", "female", "all"):
            sub = occ_unit[occ_unit["sex"] == sex_key]
            if len(sub) == 0:
                continue
            names = sub["soc_name"].to_numpy(dtype=str)
            codes = sub["soc_code"].to_numpy(dtype=str)
            w = sub["weight"].to_numpy(dtype=float)
            w = w / w.sum()
            self._occ_lookup[sex_key] = (names, codes, w)

        # ── Baby names → {sex: (names[], weights[])} ──
        nm = pd.read_parquet(p / "baby_names.parquet")
        self._name_lookup: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for sex in ("male", "female"):
            sub = nm[nm["sex"] == sex]
            n = sub["name"].to_numpy(dtype=str)
            c = sub["count"].to_numpy(dtype=float)
            self._name_lookup[sex] = (n, c / c.sum())

        # ── Vehicles → column arrays ──
        veh = pd.read_parquet(p / "vehicle_make_model.parquet")
        self._veh_n = len(veh)
        self._veh_make = veh["make"].to_numpy(dtype=str)
        self._veh_gen_model = veh["gen_model"].to_numpy(dtype=str)
        self._veh_model = veh["model"].to_numpy(dtype=str)
        self._veh_fuel = veh["fuel"].to_numpy(dtype=str)
        self._veh_weights = veh["weight"].to_numpy(dtype=float)

        # ── Claim rate → array indexed by age (0-100) ──
        cf = pd.read_parquet(p / "claim_freq_by_age.parquet")
        self._claim_rate = np.full(101, 0.10, dtype=float)
        for _, row in cf.iterrows():
            band = str(row["age_band"])
            if "[" in band:
                parts = band.strip("[]()").split(",")
                lo, hi = int(parts[0].strip()), int(parts[1].strip())
                for a in range(lo, min(hi, 101)):
                    self._claim_rate[a] = float(row["claim_rate"])

        # ── MOT mileage → dicts keyed by vehicle_age ──
        mot = pd.read_parquet(p / "mot_mileage_by_age.parquet")
        self._mileage: dict[int, tuple[float, float]] = {}
        for _, row in mot.iterrows():
            a = int(row["vehicle_age"])
            self._mileage[a] = (float(row["median_mileage"]), float(row["std_mileage"]))

        mota = pd.read_parquet(p / "mot_annual_mileage_by_age.parquet")
        self._annual_mileage: dict[int, tuple[float, float]] = {}
        for _, row in mota.iterrows():
            a = int(row["vehicle_age"])
            self._annual_mileage[a] = (float(row["median_annual"]), float(row["std_annual"]))

    # ── Sampling methods (all pure numpy/dict, no pandas) ────────────────

    def sample_postcode(self, rng: np.random.Generator) -> dict:
        """Sample a random postcode. Returns a plain dict."""
        idx = rng.integers(0, self._pc_n)
        return {
            "pcd": self._pc_pcd[idx],
            "rgn_name": self._pc_rgn[idx],
            "is_urban": bool(self._pc_urban[idx]),
        }

    def sample_postcode_in_region(self, rng: np.random.Generator, region: str) -> dict:
        """Sample a postcode within a specific region."""
        indices = self._pc_by_region.get(region)
        if indices is None or len(indices) == 0:
            return self.sample_postcode(rng)
        idx = indices[rng.integers(0, len(indices))]
        return {
            "pcd": self._pc_pcd[idx],
            "rgn_name": self._pc_rgn[idx],
            "is_urban": bool(self._pc_urban[idx]),
        }

    def sample_age_gender(self, rng: np.random.Generator) -> tuple[int, str]:
        """Sample (age, gender) from DVLA licence holder distribution."""
        gender = "male" if rng.random() < self.p_male else "female"
        weights = self.male_age_weights if gender == "male" else self.female_age_weights
        age = rng.choice(self.ages, p=weights)
        return int(age), gender

    def sample_marital_status(self, rng: np.random.Generator, age: int, sex: str) -> str:
        """Sample marital status conditioned on age and sex."""
        key = (sex, min(max(age, 16), 100))
        lookup = self._marital_lookup.get(key)
        if lookup is None:
            return "single"
        statuses, weights = lookup
        if weights.sum() == 0:
            return "single"
        status = str(rng.choice(statuses, p=weights))

        # Occasionally add "living_with_partner" (not in ONS data but in schema)
        if status == "single" and age >= 20 and rng.random() < 0.15:
            return "living_with_partner"
        return status

    def sample_occupation(self, rng: np.random.Generator, sex: str) -> tuple[str, str]:
        """Sample (occupation_name, soc_code) by sex."""
        sex_key = sex if sex in self._occ_lookup else "all"
        names, codes, weights = self._occ_lookup[sex_key]
        idx = rng.choice(len(names), p=weights)
        return str(names[idx]), str(codes[idx])

    def sample_first_name(self, rng: np.random.Generator, sex: str) -> str:
        """Sample a first name by sex."""
        names, weights = self._name_lookup[sex]
        return str(rng.choice(names, p=weights))

    def sample_vehicle(self, rng: np.random.Generator) -> dict:
        """Sample a vehicle make/model/fuel combo. Returns a plain dict."""
        idx = rng.choice(self._veh_n, p=self._veh_weights)
        return {
            "make": self._veh_make[idx],
            "gen_model": self._veh_gen_model[idx],
            "model": self._veh_model[idx],
            "fuel": self._veh_fuel[idx],
        }

    def get_claim_rate(self, age: int) -> float:
        """Get annual claim rate for a given driver age."""
        return float(self._claim_rate[min(max(age, 0), 100)])

    def get_mileage_stats(self, vehicle_age: int) -> tuple[float, float]:
        """Get (median_mileage, std_mileage) for a vehicle age."""
        va = max(3, min(30, vehicle_age))
        return self._mileage.get(va, (50000.0, 20000.0))

    def get_annual_mileage_stats(self, vehicle_age: int) -> tuple[float, float]:
        """Get (median_annual, std_annual) for a vehicle age."""
        va = max(3, min(30, vehicle_age))
        return self._annual_mileage.get(va, (7500.0, 4000.0))
