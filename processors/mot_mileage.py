"""
Process MOT test data into vehicle mileage-by-age distributions.

Input:  data/raw/mot/MOT testing data results (2024)/test_result_2024{07,08,09}.csv
Output: data/processed/mot_mileage_by_age.parquet
        — columns: vehicle_age, mean_mileage, median_mileage, std_mileage, count
        data/processed/mot_annual_mileage_by_age.parquet
        — annual mileage estimate by vehicle age
"""

import glob
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"

# Only use complete monthly files (Jul-Sep 2024); Dec is truncated
COMPLETE_MONTHS = ["test_result_202407.csv", "test_result_202408.csv", "test_result_202409.csv"]

KEEP_COLS = ["test_mileage", "make", "model", "fuel_type", "first_use_date", "test_date", "test_result"]


def process() -> pd.DataFrame:
    """Parse MOT test CSVs → mileage distributions by vehicle age."""
    mot_dir = RAW_DIR / "mot" / "MOT testing data results (2024)"

    chunks = []
    for fname in COMPLETE_MONTHS:
        fpath = mot_dir / fname
        if not fpath.exists():
            print(f"  WARNING: {fpath} not found, skipping")
            continue
        print(f"  Reading {fname}...")
        df = pd.read_csv(fpath, usecols=KEEP_COLS, low_memory=False, dtype=str)
        chunks.append(df)

    df = pd.concat(chunks, ignore_index=True)
    print(f"  {len(df):,} total MOT test records (3 months)")

    # Convert types (all read as str to avoid Arrow backend issues)
    df["test_mileage"] = pd.to_numeric(df["test_mileage"], errors="coerce")
    df["first_use_date"] = pd.to_datetime(df["first_use_date"], errors="coerce")
    df["test_date"] = pd.to_datetime(df["test_date"].str[:10], errors="coerce")

    # Filter: valid mileage and dates
    df = df[df["test_mileage"].notna() & (df["test_mileage"] > 0)]
    df = df[df["first_use_date"].notna() & df["test_date"].notna()]

    # Compute vehicle age at test (years)
    df["vehicle_age"] = ((df["test_date"] - df["first_use_date"]).dt.days / 365.25).round(0).astype(int)

    # Filter reasonable values
    df = df[(df["vehicle_age"] >= 3) & (df["vehicle_age"] <= 30)]  # MOT from 3 years
    df = df[(df["test_mileage"] >= 100) & (df["test_mileage"] <= 300_000)]

    print(f"  {len(df):,} records after filtering (age 3-30, mileage 100-300K)")

    # ── Odometer mileage by vehicle age ──
    odo_by_age = df.groupby("vehicle_age")["test_mileage"].agg(
        mean_mileage="mean",
        median_mileage="median",
        std_mileage="std",
        p25_mileage=lambda x: x.quantile(0.25),
        p75_mileage=lambda x: x.quantile(0.75),
        count="count",
    ).reset_index()

    print(f"\n  Odometer mileage by vehicle age:")
    for _, row in odo_by_age.iterrows():
        age = int(row["vehicle_age"])
        if age <= 15 or age % 5 == 0:
            print(f"    Age {age:2d}: median {row['median_mileage']:>8,.0f} mi "
                  f"(IQR {row['p25_mileage']:>7,.0f}–{row['p75_mileage']:>7,.0f}), n={int(row['count']):,}")

    # ── Estimate annual mileage by vehicle age ──
    # Annual mileage ≈ odometer / age (crude but useful)
    df["annual_mileage_est"] = df["test_mileage"] / df["vehicle_age"]
    annual_by_age = df.groupby("vehicle_age")["annual_mileage_est"].agg(
        mean_annual="mean",
        median_annual="median",
        std_annual="std",
    ).reset_index()

    print(f"\n  Estimated annual mileage by vehicle age:")
    for _, row in annual_by_age.iterrows():
        age = int(row["vehicle_age"])
        if age <= 15 or age % 5 == 0:
            print(f"    Age {age:2d}: median {row['median_annual']:>7,.0f} mi/yr")

    # ── Mileage by fuel type ──
    fuel_mileage = df.groupby("fuel_type").agg(
        median_mileage=("test_mileage", "median"),
        median_annual=("annual_mileage_est", "median"),
        count=("test_mileage", "count"),
    ).sort_values("count", ascending=False)

    print(f"\n  Mileage by fuel type:")
    for fuel, row in fuel_mileage.iterrows():
        if row["count"] > 1000:
            print(f"    {fuel}: median odo {row['median_mileage']:>8,.0f}, "
                  f"annual {row['median_annual']:>7,.0f}, n={row['count']:,}")

    # ── Top makes by median mileage ──
    make_mileage = df.groupby("make").agg(
        median_annual=("annual_mileage_est", "median"),
        count=("test_mileage", "count"),
    ).sort_values("count", ascending=False)

    print(f"\n  Top 10 makes by volume — median annual mileage:")
    for make, row in make_mileage.head(10).iterrows():
        print(f"    {make}: {row['median_annual']:>7,.0f} mi/yr (n={row['count']:,})")

    # ── Save ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    out1 = OUT_DIR / "mot_mileage_by_age.parquet"
    odo_by_age.to_parquet(out1, index=False)
    print(f"\n  Saved: {out1}")

    out2 = OUT_DIR / "mot_annual_mileage_by_age.parquet"
    annual_by_age.to_parquet(out2, index=False)
    print(f"  Saved: {out2}")

    return odo_by_age


if __name__ == "__main__":
    process()
