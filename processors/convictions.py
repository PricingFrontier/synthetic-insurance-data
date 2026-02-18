"""
Process MoJ motoring convictions data into offence type × demographics distributions.

Input:  data/raw/motoring_convictions_2017_2024.csv
        (pre-filtered from MoJ Outcomes by Offence CSV — motoring rows only)
Output: data/processed/motoring_convictions.parquet
        — offence distribution by age/sex for latest year
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def process() -> pd.DataFrame:
    """Parse motoring convictions CSV → offence type and demographic distributions."""
    fpath = RAW_DIR / "motoring_convictions_2017_2024.csv"
    df = pd.read_csv(fpath, low_memory=False)

    print(f"  {len(df):,} motoring offence rows (2017-2024)")

    # Use latest year only
    latest_year = df["Year"].max()
    df = df[df["Year"] == latest_year].copy()
    print(f"  Filtered to {latest_year}: {len(df):,} rows")

    # Convert count columns to numeric
    for col in ["Proceeded against", "Convicted", "Sentenced"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Clean sex and age
    df["sex"] = df["Sex"].str.extract(r":\s*(.*)", expand=False).str.strip().str.lower()
    df["age_range"] = df["Age Range"].str.extract(r":\s*(.*)", expand=False).str.strip()

    # ── Offence group distribution (convicted) ──
    offence_dist = (
        df.groupby("Offence Group")["Convicted"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    offence_dist.columns = ["offence_group", "convicted"]
    total_convicted = offence_dist["convicted"].sum()
    offence_dist["weight"] = offence_dist["convicted"] / total_convicted

    print(f"\n  Total convicted ({latest_year}): {total_convicted:,}")
    print(f"  Offence group distribution:")
    for _, row in offence_dist.iterrows():
        print(f"    {row['offence_group']}: {row['convicted']:,} ({row['weight']:.1%})")

    # ── Top individual offences ──
    offence_detail = (
        df.groupby("Offence")["Convicted"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    offence_detail.columns = ["offence", "convicted"]
    offence_detail["weight"] = offence_detail["convicted"] / total_convicted

    print(f"\n  Top 15 individual offences:")
    for _, row in offence_detail.head(15).iterrows():
        print(f"    {row['offence'][:60]}: {row['convicted']:,} ({row['weight']:.1%})")

    # ── By sex ──
    by_sex = df.groupby("sex")["Convicted"].sum()
    print(f"\n  By sex: {dict(by_sex)}")

    # ── By age range ──
    by_age = (
        df.groupby("age_range")["Convicted"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    by_age.columns = ["age_range", "convicted"]
    by_age["weight"] = by_age["convicted"] / by_age["convicted"].sum()
    print(f"\n  By age range:")
    for _, row in by_age.iterrows():
        if row["convicted"] > 0:
            print(f"    {row['age_range']}: {row['convicted']:,} ({row['weight']:.1%})")

    # ── Save outputs ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    out_path = OUT_DIR / "motoring_convictions.parquet"
    offence_detail.to_parquet(out_path, index=False)
    print(f"\n  Saved: {out_path}")

    # Also save the full year's data for cross-tabs
    full_out = OUT_DIR / "motoring_convictions_full.parquet"
    df.to_parquet(full_out, index=False)
    print(f"  Saved: {full_out}")

    return offence_detail


if __name__ == "__main__":
    process()
