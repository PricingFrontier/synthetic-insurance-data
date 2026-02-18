"""
Process STATS19 road casualty data into accident rate distributions.

Input:  data/raw/dft-road-casualty-statistics-collision-provisional-2025.csv
        data/raw/dft-road-casualty-statistics-vehicle-provisional-2025.csv
        data/raw/dft-road-casualty-statistics-casualty-provisional-2025.csv
Output: data/processed/accident_rates.parquet
        — accident counts by driver age band, vehicle type, severity, region
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def process() -> pd.DataFrame:
    """Parse STATS19 vehicle-level data for accident rate proxies."""
    veh_path = RAW_DIR / "dft-road-casualty-statistics-vehicle-provisional-2025.csv"
    col_path = RAW_DIR / "dft-road-casualty-statistics-collision-provisional-2025.csv"

    veh = pd.read_csv(veh_path, low_memory=False)
    col = pd.read_csv(col_path, low_memory=False)

    print(f"  Vehicles file: {len(veh):,} rows, columns: {veh.columns.tolist()[:10]}...")
    print(f"  Collisions file: {len(col):,} rows, columns: {col.columns.tolist()[:10]}...")

    # Standardise column names to lower
    veh.columns = veh.columns.str.lower().str.strip()
    col.columns = col.columns.str.lower().str.strip()

    # Find age column (varies by year: 'age_of_driver', 'driver_age', etc.)
    age_col = None
    for candidate in ["age_of_driver", "driver_age", "age_band_of_driver"]:
        if candidate in veh.columns:
            age_col = candidate
            break
    if age_col is None:
        print(f"  Available vehicle columns: {veh.columns.tolist()}")
        # Try to find any column with 'age' and 'driver'
        age_cols = [c for c in veh.columns if "age" in c and "driver" in c]
        if age_cols:
            age_col = age_cols[0]
        else:
            age_col = [c for c in veh.columns if "age" in c][0] if any("age" in c for c in veh.columns) else None

    if age_col:
        print(f"  Using age column: {age_col}")
        veh["driver_age"] = pd.to_numeric(veh[age_col], errors="coerce")
    else:
        print("  WARNING: No driver age column found")
        veh["driver_age"] = None

    # Find vehicle type column
    vtype_col = None
    for candidate in ["vehicle_type", "type_of_vehicle"]:
        if candidate in veh.columns:
            vtype_col = candidate
            break

    # Merge collision severity
    severity_col = None
    for candidate in ["accident_severity", "collision_severity", "severity"]:
        if candidate in col.columns:
            severity_col = candidate
            break

    # Find the join key
    join_col = None
    for candidate in ["accident_index", "collision_index", "accident_reference"]:
        if candidate in veh.columns and candidate in col.columns:
            join_col = candidate
            break

    if join_col and severity_col:
        veh = veh.merge(col[[join_col, severity_col]].drop_duplicates(), on=join_col, how="left")
        print(f"  Merged severity from collisions via {join_col}")
    else:
        print(f"  Could not merge severity (join_col={join_col}, severity_col={severity_col})")

    # Create age bands
    if veh["driver_age"].notna().any():
        age_bins = [17, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 100]
        veh["age_band"] = pd.cut(veh["driver_age"], bins=age_bins, right=False)

        # Accident count by age band
        age_acc = veh.groupby("age_band", observed=True).size().reset_index(name="accident_count")
        age_acc["age_band"] = age_acc["age_band"].astype(str)
        total = age_acc["accident_count"].sum()
        age_acc["weight"] = age_acc["accident_count"] / total

        print(f"  Accidents by driver age band:")
        for _, row in age_acc.iterrows():
            print(f"    {row['age_band']}: {row['accident_count']:,} ({row['weight']:.1%})")
    else:
        age_acc = pd.DataFrame()

    # Vehicle type breakdown
    if vtype_col:
        vtype_acc = veh[vtype_col].value_counts().reset_index()
        vtype_acc.columns = ["vehicle_type", "count"]
        print(f"  Top 5 vehicle types in accidents:")
        for _, row in vtype_acc.head(5).iterrows():
            print(f"    {row['vehicle_type']}: {row['count']:,}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "accident_rates.parquet"
    if len(age_acc) > 0:
        age_acc.to_parquet(out_path, index=False)
        print(f"  Saved: {out_path}")
    else:
        print("  WARNING: No age-band data to save")

    return age_acc


if __name__ == "__main__":
    process()
