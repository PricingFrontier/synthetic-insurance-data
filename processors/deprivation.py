"""
Process IoD2019 Index of Multiple Deprivation into LSOA-level lookup.

Input:  data/raw/File_1_-_IMD2019_Index_of_Multiple_Deprivation.xlsx
Output: data/processed/imd_by_lsoa.parquet
        — columns: lsoa_code, lsoa_name, imd_rank, imd_decile, imd_score
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def process() -> pd.DataFrame:
    """Parse IoD2019 XLSX into clean LSOA-level IMD table."""
    fpath = RAW_DIR / "File_1_-_IMD2019_Index_of_Multiple_Deprivation.xlsx"
    df = pd.read_excel(fpath, sheet_name="IMD2019")

    print(f"  Columns: {df.columns.tolist()[:10]}")
    print(f"  {len(df):,} LSOAs")

    # Standardise column names
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if "lsoa" in cl and "code" in cl:
            col_map[c] = "lsoa_code"
        elif "lsoa" in cl and "name" in cl:
            col_map[c] = "lsoa_name"
        elif "rank" in cl and "decile" not in cl:
            col_map[c] = "imd_rank"
        elif "decile" in cl:
            col_map[c] = "imd_decile"
        elif "score" in cl:
            col_map[c] = "imd_score"

    df = df.rename(columns=col_map)

    # Keep relevant columns
    keep = [c for c in ["lsoa_code", "lsoa_name", "imd_rank", "imd_decile", "imd_score"] if c in df.columns]
    df = df[keep].copy()

    print(f"  IMD decile distribution:")
    if "imd_decile" in df.columns:
        decile_counts = df["imd_decile"].value_counts().sort_index()
        for d, cnt in decile_counts.items():
            print(f"    Decile {d}: {cnt:,}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "imd_by_lsoa.parquet"
    df.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path}")

    return df


if __name__ == "__main__":
    process()
