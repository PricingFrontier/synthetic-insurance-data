"""
Process DfT VEH0120 into vehicle make/model/fuel distribution.

Input:  data/raw/df_VEH0120_UK.csv
Output: data/processed/vehicle_make_model.parquet
        — columns: body_type, make, gen_model, model, fuel, count, weight
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def process() -> pd.DataFrame:
    """Parse VEH0120: licensed vehicles by make/model/fuel, latest quarter."""
    fpath = RAW_DIR / "df_VEH0120_UK.csv"
    df = pd.read_csv(fpath, low_memory=False, encoding="latin-1")

    # Keep only Cars, Licensed status, latest quarter
    df = df[df["BodyType"] == "Cars"]
    df = df[df["LicenceStatus"] == "Licensed"]

    # Latest quarter column (first numeric column after metadata)
    quarter_cols = [c for c in df.columns if "Q" in c and c[0].isdigit()]
    latest_q = quarter_cols[0]  # e.g. "2025 Q3"
    print(f"  Using latest quarter: {latest_q}")

    # Select and rename
    result = df[["BodyType", "Make", "GenModel", "Model", "Fuel", latest_q]].copy()
    result.columns = ["body_type", "make", "gen_model", "model", "fuel", "count"]

    # Clean count
    result["count"] = pd.to_numeric(result["count"], errors="coerce").fillna(0).astype(int)

    # Drop zero-count rows
    result = result[result["count"] > 0]

    # Normalise text
    result["make"] = result["make"].str.strip().str.upper()
    result["gen_model"] = result["gen_model"].str.strip().str.upper()
    result["model"] = result["model"].str.strip().str.upper()
    result["fuel"] = result["fuel"].str.strip().str.upper()

    # Aggregate to make level for summary
    make_totals = result.groupby("make")["count"].sum().sort_values(ascending=False)
    grand_total = make_totals.sum()

    # Weight at model level
    result["weight"] = result["count"] / grand_total

    # Also create a make-level summary
    make_df = make_totals.reset_index()
    make_df.columns = ["make", "count"]
    make_df["weight"] = make_df["count"] / grand_total
    make_df = make_df.sort_values("count", ascending=False)

    print(f"  {grand_total:,.0f} total licensed cars")
    print(f"  {result['make'].nunique()} makes, {len(result)} make/model/fuel combos")
    print(f"  Top 10 makes:")
    for _, row in make_df.head(10).iterrows():
        print(f"    {row['make']}: {row['count']:,.0f} ({row['weight']:.1%})")

    # Fuel type breakdown
    fuel_totals = result.groupby("fuel")["count"].sum().sort_values(ascending=False)
    print(f"  Fuel breakdown:")
    for fuel, cnt in fuel_totals.items():
        print(f"    {fuel}: {cnt:,.0f} ({cnt / grand_total:.1%})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    out_path = OUT_DIR / "vehicle_make_model.parquet"
    result.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path}")

    out_make = OUT_DIR / "vehicle_make_summary.parquet"
    make_df.to_parquet(out_make, index=False)
    print(f"  Saved: {out_make}")

    return result


if __name__ == "__main__":
    process()
