"""
Process ONS marital status data into age × sex × status distribution.

Input:  data/raw/maritalstatuslivingarrangements2002to2024englandandwales.xlsx
Output: data/processed/marital_status.parquet
        — columns: sex, age_group, status, count, weight
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"

# Map ONS age groups to (min_age, max_age) for later sampling
AGE_GROUP_MAP = {
    "16 to 19": (16, 19),
    "20 to 24": (20, 24),
    "25 to 29": (25, 29),
    "30 to 34": (30, 34),
    "35 to 39": (35, 39),
    "40 to 44": (40, 44),
    "45 to 49": (45, 49),
    "50 to 54": (50, 54),
    "55 to 59": (55, 59),
    "60 to 64": (60, 64),
    "65 to 69": (65, 69),
    "70 to 74": (70, 74),
    "75 to 79": (75, 79),
    "80 to 84": (80, 84),
    "85 and over": (85, 100),
}

# Simplify marital status categories to match our schema
# Values in the data have trailing spaces and [note N] annotations
# so we match on prefix
STATUS_PREFIX_MAP = {
    "Never married": "single",
    "Married": "married",
    "Civil Partnered": "civil_partnership",
    "Separated": "separated",
    "Divorced": "divorced",
    "Widowed": "widowed",
}


def _parse_sheet(fpath: Path, sheet_name: str, sex_label: str) -> pd.DataFrame:
    """Parse one marital status sheet (All/Males/Females)."""
    df = pd.read_excel(fpath, sheet_name=sheet_name, header=None)

    # Find header row containing "Marital status"
    header_idx = None
    for i, row in df.iterrows():
        vals = [str(v) for v in row.values]
        has_status = any("Marital status" in v for v in vals)
        has_estimate = any("Estimate" in v for v in vals)
        if has_status and has_estimate:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Could not find header row in {sheet_name}")

    data = df.iloc[header_idx + 1:].copy()
    data.columns = [str(c) for c in df.iloc[header_idx].tolist()]

    # We want the latest year's Estimate column (2024)
    # Columns repeat as: 'YYYY Estimate', 'YYYY CV', 'YYYY CI+/-'
    # Find the latest estimate column
    est_cols = [c for c in data.columns if isinstance(c, str) and "Estimate" in c]
    if not est_cols:
        raise ValueError(f"No Estimate columns found in {sheet_name}")
    latest_est = sorted(est_cols)[-1]  # e.g. "2024 Estimate"
    year = latest_est.split()[0]
    print(f"  Using {latest_est} from {sheet_name}")

    # Get marital status and age group columns
    status_col = [c for c in data.columns if isinstance(c, str) and "Marital status" in c][0]
    age_col = [c for c in data.columns if isinstance(c, str) and "Age group" in c][0]

    result = data[[status_col, age_col, latest_est]].copy()
    result.columns = ["status_raw", "age_group", "count"]

    # Map status using prefix matching (ONS data has trailing spaces and [note N] annotations)
    def _map_status(raw: str) -> str | None:
        raw_stripped = str(raw).strip()
        for prefix, mapped in STATUS_PREFIX_MAP.items():
            if raw_stripped.startswith(prefix):
                return mapped
        return None

    result["status"] = result["status_raw"].apply(_map_status)
    result = result.dropna(subset=["status"])
    result = result[result["age_group"].isin(AGE_GROUP_MAP.keys())]
    result["count"] = pd.to_numeric(result["count"], errors="coerce").fillna(0)
    result["sex"] = sex_label
    result["year"] = int(year)

    return result[["sex", "age_group", "status", "count", "year"]]


def process() -> pd.DataFrame:
    """Parse marital status for males and females separately."""
    fpath = RAW_DIR / "maritalstatuslivingarrangements2002to2024englandandwales.xlsx"

    males = _parse_sheet(fpath, "Table_2_Marital_Status_Males", "male")
    females = _parse_sheet(fpath, "Table_3_Marital_Status_Females", "female")
    df = pd.concat([males, females], ignore_index=True)

    # Add age bounds for easier lookup
    df["age_min"] = df["age_group"].map(lambda x: AGE_GROUP_MAP.get(x, (0, 0))[0])
    df["age_max"] = df["age_group"].map(lambda x: AGE_GROUP_MAP.get(x, (0, 0))[1])

    # Compute within-group weights (probability of each status given age+sex)
    df["weight"] = df.groupby(["sex", "age_group"])["count"].transform(
        lambda x: x / x.sum() if x.sum() > 0 else 0
    )

    print(f"  {len(df)} rows (sex × age_group × status)")
    print(f"  Status distribution (all):")
    totals = df.groupby("status")["count"].sum()
    for s, c in (totals / totals.sum() * 100).items():
        print(f"    {s}: {c:.1f}%")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "marital_status.parquet"
    df.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path}")

    return df


if __name__ == "__main__":
    process()
