"""
Process ONS baby names into name frequency tables by sex.

Input:  data/raw/boysnames2024.xlsx, data/raw/girlsnames2024.xlsx
Output: data/processed/baby_names.parquet
        — columns: sex, name, count, rank
"""

from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def _parse_names_sheet(fpath: Path, sex: str) -> pd.DataFrame:
    """Parse an ONS baby names Excel file. Table_1 = top 100 for latest year."""
    df = pd.read_excel(fpath, sheet_name="Table_1", header=None)

    # Find the header row containing "Rank" and "Name"
    header_idx = None
    for i, row in df.iterrows():
        vals = [str(v).strip() for v in row.values]
        if "Rank" in vals and "Name" in vals:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Could not find header row in {fpath}")

    data = df.iloc[header_idx + 1:].copy()
    data.columns = df.iloc[header_idx].tolist()

    # Keep Rank, Name, Count
    data = data[["Rank", "Name", "Count"]].copy()
    data.columns = ["rank", "name", "count"]

    # Clean
    data["rank"] = pd.to_numeric(data["rank"], errors="coerce")
    data["count"] = pd.to_numeric(data["count"], errors="coerce")
    data = data.dropna(subset=["rank", "name", "count"])
    data["rank"] = data["rank"].astype(int)
    data["count"] = data["count"].astype(int)
    data["name"] = data["name"].str.strip().str.title()
    data["sex"] = sex

    return data[["sex", "name", "count", "rank"]]


def process() -> pd.DataFrame:
    """Parse boys and girls name files, combine into single table."""
    boys = _parse_names_sheet(RAW_DIR / "boysnames2024.xlsx", "male")
    girls = _parse_names_sheet(RAW_DIR / "girlsnames2024.xlsx", "female")

    df = pd.concat([boys, girls], ignore_index=True)

    # Add weight within each sex
    df["weight"] = df.groupby("sex")["count"].transform(lambda x: x / x.sum())

    print(f"  {len(boys)} boy names, {len(girls)} girl names")
    print(f"  Top 5 boys:  {', '.join(boys.nsmallest(5, 'rank')['name'].tolist())}")
    print(f"  Top 5 girls: {', '.join(girls.nsmallest(5, 'rank')['name'].tolist())}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "baby_names.parquet"
    df.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path}")

    return df


if __name__ == "__main__":
    process()
