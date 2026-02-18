"""
Process freMTPL2 into claim frequency and severity distributions.

Input:  data/raw/freMTPL2freq.arff, data/raw/freMTPL2sev.arff
Output: data/processed/claim_frequency.parquet   — policy-level claim counts with risk factors
        data/processed/claim_severity.parquet    — individual claim amounts
        data/processed/claim_freq_by_age.parquet — claim rate by driver age band
"""

from pathlib import Path

import pandas as pd
import numpy as np

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"


def _read_arff(fpath: Path) -> pd.DataFrame:
    """Simple ARFF reader — skip header, read CSV data section."""
    with open(fpath) as f:
        lines = f.readlines()

    # Find @data marker
    data_start = None
    attributes = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith("@attribute"):
            parts = stripped.split()
            attr_name = parts[1]
            attributes.append(attr_name)
        if stripped.lower() == "@data":
            data_start = i + 1
            break

    if data_start is None:
        raise ValueError(f"No @data section found in {fpath}")

    # Read CSV portion
    from io import StringIO
    csv_text = "".join(lines[data_start:])
    df = pd.read_csv(StringIO(csv_text), header=None, names=attributes, quotechar="'")
    return df


def process() -> dict[str, pd.DataFrame]:
    """Process freMTPL2 frequency and severity datasets."""
    # ── Frequency dataset ──
    freq = _read_arff(RAW_DIR / "freMTPL2freq.arff")
    print(f"  freMTPL2freq: {len(freq):,} policies")
    print(f"  Columns: {freq.columns.tolist()}")

    # Clean up
    freq["ClaimNb"] = freq["ClaimNb"].astype(int)
    freq["Exposure"] = freq["Exposure"].astype(float)
    freq["DrivAge"] = freq["DrivAge"].astype(int)
    freq["VehAge"] = freq["VehAge"].astype(int)
    freq["VehPower"] = freq["VehPower"].astype(int)
    freq["BonusMalus"] = freq["BonusMalus"].astype(int)
    freq["Density"] = freq["Density"].astype(int)

    # Claim rate
    total_claims = freq["ClaimNb"].sum()
    total_exposure = freq["Exposure"].sum()
    overall_rate = total_claims / total_exposure
    print(f"  Total claims: {total_claims:,}, Total exposure: {total_exposure:,.1f} years")
    print(f"  Overall claim rate: {overall_rate:.4f} per policy-year")

    # Claim count distribution
    claim_dist = freq["ClaimNb"].value_counts().sort_index()
    print(f"  Claim count distribution:")
    for n_claims, count in claim_dist.items():
        print(f"    {n_claims} claims: {count:,} ({count / len(freq):.1%})")

    # ── Claim rate by driver age band ──
    age_bins = [17, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 100]
    freq["age_band"] = pd.cut(freq["DrivAge"], bins=age_bins, right=False)
    age_stats = freq.groupby("age_band", observed=True).agg(
        policies=("IDpol", "count"),
        exposure=("Exposure", "sum"),
        claims=("ClaimNb", "sum"),
    ).reset_index()
    age_stats["claim_rate"] = age_stats["claims"] / age_stats["exposure"]
    age_stats["age_band"] = age_stats["age_band"].astype(str)

    print(f"  Claim rate by age:")
    for _, row in age_stats.iterrows():
        print(f"    {row['age_band']}: {row['claim_rate']:.4f} ({row['policies']:,} policies)")

    # ── Severity dataset ──
    sev = _read_arff(RAW_DIR / "freMTPL2sev.arff")
    print(f"\n  freMTPL2sev: {len(sev):,} claims")
    sev["ClaimAmount"] = sev["ClaimAmount"].astype(float)

    print(f"  Severity stats:")
    print(f"    Mean:   €{sev['ClaimAmount'].mean():,.0f}")
    print(f"    Median: €{sev['ClaimAmount'].median():,.0f}")
    print(f"    Std:    €{sev['ClaimAmount'].std():,.0f}")
    print(f"    Max:    €{sev['ClaimAmount'].max():,.0f}")

    # Log-normal fit
    log_amounts = np.log(sev["ClaimAmount"][sev["ClaimAmount"] > 0])
    mu, sigma = log_amounts.mean(), log_amounts.std()
    print(f"  Log-normal fit: mu={mu:.3f}, sigma={sigma:.3f}")

    # ── Save outputs ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Drop the interval column before saving (it's saved separately in age_stats)
    freq_save = freq.drop(columns=["age_band"], errors="ignore")
    freq_out = OUT_DIR / "claim_frequency.parquet"
    freq_save.to_parquet(freq_out, index=False)
    print(f"\n  Saved: {freq_out}")

    sev_out = OUT_DIR / "claim_severity.parquet"
    sev.to_parquet(sev_out, index=False)
    print(f"  Saved: {sev_out}")

    age_out = OUT_DIR / "claim_freq_by_age.parquet"
    age_stats.to_parquet(age_out, index=False)
    print(f"  Saved: {age_out}")

    return {"frequency": freq, "severity": sev, "by_age": age_stats}


if __name__ == "__main__":
    process()
