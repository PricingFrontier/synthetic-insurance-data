"""
Process DVLA driving licence data into age × gender distribution.

Input:  data/raw/driving-licence-data-nov-2025.xlsx  (sheet DRL0101)
Output: data/processed/driver_age_gender.parquet
        — columns: age, male_full, female_full, male_prov, female_prov, total
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def process() -> pd.DataFrame:
    """Parse DRL0101 sheet: licence holders by single year of age and sex."""
    fpath = RAW_DIR / "driving-licence-data-nov-2025.xlsx"
    df = pd.read_excel(fpath, sheet_name="DRL0101- November 2025", header=None)

    # Find the header row (contains "Provisional Licences - Male")
    header_idx = None
    for i, row in df.iterrows():
        vals = [str(v) for v in row.values]
        if any("Provisional Licences - Male" in v for v in vals):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row in DRL0101")

    # Next row should be "Age" label, data starts after that
    data_start = header_idx + 2  # skip header + "Age" label row
    headers = ["age", "prov_male", "prov_female", "prov_total",
               "full_male", "full_female", "full_total"]

    data = df.iloc[data_start:].copy()
    data.columns = range(len(data.columns))
    data = data.iloc[:, :7]
    data.columns = headers

    # Convert age to numeric, drop non-numeric rows (totals, notes)
    data["age"] = pd.to_numeric(data["age"], errors="coerce")
    data = data.dropna(subset=["age"])
    data["age"] = data["age"].astype(int)

    # Filter to reasonable driving ages
    data = data[(data["age"] >= 15) & (data["age"] <= 100)]

    # Convert counts to numeric
    for col in headers[1:]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0).astype(int)

    # Add weight column (probability of a random licence holder being this age/gender)
    total_full = data["full_total"].sum()
    data["weight_full"] = data["full_total"] / total_full

    data = data.reset_index(drop=True)

    print(f"  {len(data)} age bands (15-{data['age'].max()})")
    print(f"  Total full licence holders: {total_full:,.0f}")
    print(f"  Male %: {data['full_male'].sum() / total_full * 100:.1f}%")
    print(f"  Female %: {data['full_female'].sum() / total_full * 100:.1f}%")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "driver_age_gender.parquet"
    data.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path}")

    return data


if __name__ == "__main__":
    process()
