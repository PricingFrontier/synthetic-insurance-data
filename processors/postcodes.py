"""
Process ONSPD into a compact postcode lookup table.

Input:  data/raw/onspd/Data/multi_csv/ONSPD_NOV_2024_UK_*.csv
Output: data/processed/postcode_lookup.parquet
        — columns: pcd, pcd_area, rgn, laua, ru11ind, imd, lat, lon, lsoa11
"""

import glob
from pathlib import Path

import pandas as pd
from tqdm import tqdm

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"

# ONSPD region codes → human-readable names
REGION_MAP = {
    "E12000001": "North East",
    "E12000002": "North West",
    "E12000003": "Yorkshire and The Humber",
    "E12000004": "East Midlands",
    "E12000005": "West Midlands",
    "E12000006": "East of England",
    "E12000007": "London",
    "E12000008": "South East",
    "E12000009": "South West",
    "W92000004": "Wales",
    "S92000003": "Scotland",
    "N92000002": "Northern Ireland",
    # ONSPD uses 'not applicable' codes for non-England postcodes
    "S99999999": "Scotland",
    "W99999999": "Wales",
    "N99999999": "Northern Ireland",
}

# Columns we keep from each ONSPD file
KEEP_COLS = ["pcd", "oslaua", "ru11ind", "imd", "lat", "long", "lsoa11", "rgn", "ctry", "doterm"]


def process() -> pd.DataFrame:
    """Read all ONSPD CSVs, filter to live postcodes, return compact lookup."""
    csv_dir = RAW_DIR / "onspd" / "Data" / "multi_csv"
    files = sorted(glob.glob(str(csv_dir / "ONSPD_NOV_2024_UK_*.csv")))
    if not files:
        raise FileNotFoundError(f"No ONSPD CSVs found in {csv_dir}")

    chunks = []
    for f in tqdm(files, desc="Reading ONSPD files"):
        df = pd.read_csv(f, usecols=KEEP_COLS, dtype=str, low_memory=False)
        # Keep only live postcodes (doterm is empty = still active)
        df = df[df["doterm"].isna() | (df["doterm"] == "")]
        df = df.drop(columns=["doterm"])
        chunks.append(df)

    print(f"  Concatenating {len(chunks)} postcode-area files...")
    df = pd.concat(chunks, ignore_index=True)

    # Clean up
    df["pcd"] = df["pcd"].str.strip()
    df["pcd_area"] = df["pcd"].str.extract(r"^([A-Z]{1,2})", expand=False)

    # Map region codes to names
    df["rgn_name"] = df["rgn"].map(REGION_MAP).fillna("Unknown")

    # Convert numeric columns
    df["imd"] = pd.to_numeric(df["imd"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["long"], errors="coerce")
    df = df.drop(columns=["long"])

    # Filter to GB only (exclude Channel Islands, Isle of Man — ctry not E/W/S/N)
    df = df[df["ctry"].isin(["E92000001", "W92000004", "S92000003", "N92000002"])]
    df = df.drop(columns=["ctry"])

    # Drop rows with no lat/lon (PO boxes, etc.)
    df = df.dropna(subset=["lat", "lon"])

    # IMD decile (1=most deprived, 10=least deprived) — ranks go up to ~32,844
    # Only England has IMD in ONSPD; for Scotland/Wales/NI we'll leave as NaN
    max_imd = 32844
    df["imd_decile"] = pd.cut(
        df["imd"], bins=[0] + [max_imd * i / 10 for i in range(1, 11)],
        labels=range(1, 11), include_lowest=True
    ).astype("Int64")

    # Urban/rural classification (simplify)
    # A1/B1/C1/C2 = Urban, D1/D2/E1/E2/F1/F2 = Rural
    urban_codes = {"A1", "B1", "C1", "C2"}
    df["is_urban"] = df["ru11ind"].isin(urban_codes)

    # Select final columns
    df = df[["pcd", "pcd_area", "rgn", "rgn_name", "oslaua", "lsoa11",
             "ru11ind", "is_urban", "imd", "imd_decile", "lat", "lon"]]

    # Compact types
    df["pcd"] = df["pcd"].astype("category")
    df["pcd_area"] = df["pcd_area"].astype("category")
    df["rgn"] = df["rgn"].astype("category")
    df["rgn_name"] = df["rgn_name"].astype("category")

    print(f"  {len(df):,} live postcodes retained")
    print(f"  Regions: {df['rgn_name'].value_counts().to_dict()}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "postcode_lookup.parquet"
    df.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")

    return df


if __name__ == "__main__":
    process()
