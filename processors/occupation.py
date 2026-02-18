"""
Process Nomis APS occupation data into SOC2020 frequency table.

Input:  data/raw/nomis_aps_occupation_national.csv
Output: data/processed/occupation_dist.parquet
        — columns: soc_code, soc_name, soc_level, sex, count, weight
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def process() -> pd.DataFrame:
    """Parse Nomis APS occupation CSV → occupation frequency by sex."""
    fpath = RAW_DIR / "nomis_aps_occupation_national.csv"
    df = pd.read_csv(fpath)

    # Rename columns
    df.columns = ["date", "sex", "soc_full", "value"]

    # Parse SOC code and name from soc_full (format: "1111 : Chief executives...")
    df = df[df["soc_full"].str.contains(":", na=False)].copy()
    df["soc_code"] = df["soc_full"].str.extract(r"^(\d+)", expand=False)
    df["soc_name"] = df["soc_full"].str.extract(r":\s*(.*)", expand=False).str.strip()
    df["soc_level"] = df["soc_code"].str.len()  # 1=major, 2=sub-major, 3=minor, 4=unit

    # Convert value to numeric
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    # Keep only 4-digit (unit group) codes for detailed distribution
    # Also keep 1-digit (major group) for high-level grouping
    df_unit = df[df["soc_level"] == 4].copy()
    df_major = df[df["soc_level"] == 1].copy()

    # Map sex labels
    sex_map = {"All persons": "all", "Male": "male", "Female": "female"}
    df_unit["sex"] = df_unit["sex"].map(sex_map)
    df_major["sex"] = df_major["sex"].map(sex_map)

    # For each sex, compute weights
    for subset in [df_unit, df_major]:
        for sex in ["all", "male", "female"]:
            mask = subset["sex"] == sex
            total = subset.loc[mask, "value"].sum()
            if total > 0:
                subset.loc[mask, "weight"] = subset.loc[mask, "value"] / total

    # Combine
    result = pd.concat([df_major, df_unit], ignore_index=True)
    result = result[["soc_code", "soc_name", "soc_level", "sex", "value", "weight"]].copy()
    result = result.rename(columns={"value": "count"})
    result = result.dropna(subset=["count"])

    # Filter to rows where count > 0 and weight > 0
    result = result[result["count"] > 0]

    print(f"  {len(result)} occupation × sex rows")
    print(f"  {len(result[result['soc_level'] == 4])} unit-group (4-digit) entries")
    print(f"  {len(result[result['soc_level'] == 1])} major-group (1-digit) entries")

    # Show top 10 occupations
    top10 = result[(result["sex"] == "all") & (result["soc_level"] == 4)].nlargest(10, "count")
    print(f"  Top 10 occupations (all persons):")
    for _, row in top10.iterrows():
        print(f"    {row['soc_code']} {row['soc_name']}: {row['count']:,.0f} ({row['weight']:.1%})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "occupation_dist.parquet"
    result.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path}")

    return result


if __name__ == "__main__":
    process()
