"""
Data acquisition script for synthetic UK motor insurance dataset.

Downloads public datasets used to build realistic distributions:
  1. freMTPL2freq + freMTPL2sev (OpenML) — claim frequency & severity
  2. DfT VEH0120_UK — vehicles by make/model/fuel (37 MB CSV)
  3. DfT VEH0220 — vehicles by make/model/fuel/engine size (38 MB CSV)
  4. DfT VEH1107 — vehicles by body type and years since first use
  5. DfT VEH1103 — vehicles by body type and fuel type over time
  6. DVLA driving licence data — licence holders by age/gender
  7. DfT NTS0201 — licence holders by age and sex (England)
  8. ONS baby names — boys and girls, England & Wales

Usage:
    uv run python acquire_data.py [--dataset NAME] [--all]
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

RAW_DIR = Path(__file__).parent / "data" / "raw"


DATASETS = {
    # ── freMTPL2 (OpenML) ──────────────────────────────────────────────
    "fremtpl2_freq": {
        "url": "https://www.openml.org/data/download/20649148/freMTPL2freq.arff",
        "filename": "freMTPL2freq.arff",
        "description": "French motor TPL claim frequency — 678k policies",
    },
    "fremtpl2_sev": {
        "url": "https://www.openml.org/data/download/20649149/freMTPL2sev.arff",
        "filename": "freMTPL2sev.arff",
        "description": "French motor TPL claim severity",
    },
    # ── DfT Vehicle licensing ──────────────────────────────────────────
    "veh0120_uk": {
        "url": "https://assets.publishing.service.gov.uk/media/6966489999fbdc498faecd9d/df_VEH0120_UK.csv",
        "filename": "df_VEH0120_UK.csv",
        "description": "Licensed vehicles by make/model/fuel, UK quarterly (37 MB)",
    },
    "veh0220": {
        "url": "https://assets.publishing.service.gov.uk/media/68ed09a42adc28a81b4acfec/df_VEH0220.csv",
        "filename": "df_VEH0220.csv",
        "description": "Licensed vehicles by make/model/fuel/engine size, UK annual (38 MB)",
    },
    "veh1107": {
        "url": "https://assets.publishing.service.gov.uk/media/68ecf5a582670806f9d5dfbc/veh1107.ods",
        "filename": "veh1107.ods",
        "description": "Licensed vehicles by body type and years since first use",
    },
    "veh1103": {
        "url": "https://assets.publishing.service.gov.uk/media/696641a696e60a090ce2000a/veh1103.ods",
        "filename": "veh1103.ods",
        "description": "Licensed vehicles by body type and fuel type, quarterly",
    },
    # ── DVLA driving licence data ──────────────────────────────────────
    "dvla_licences": {
        "url": "https://data.dft.gov.uk/driving-licence-data/driving-licence-data-nov-2025.xlsx",
        "filename": "driving-licence-data-nov-2025.xlsx",
        "description": "GB driving licence holders by age, gender, licence type",
    },
    # ── NTS driving licence holders by age/sex ─────────────────────────
    "nts0201": {
        "url": "https://assets.publishing.service.gov.uk/media/66ce14751aaf41b21139cf8e/nts0201.ods",
        "filename": "nts0201.ods",
        "description": "Full car driving licence holders by age and sex, England",
    },
    # ── ONS baby names ─────────────────────────────────────────────────
    # ── MoJ Criminal Justice — motoring offences ──────────────────
    "moj_outcomes_by_offence": {
        "url": "https://assets.publishing.service.gov.uk/media/68878445be2291b14d11affa/Outcomes-by-offence-tool-2017-2024.xlsx",
        "filename": "Outcomes-by-offence-tool-2017-2024.xlsx",
        "description": "MoJ outcomes by offence 2017-2024 (includes motoring convictions)",
    },
    # ── Nomis APS — employment by occupation (national) ────────────
    "nomis_aps_occupation": {
        "url": "https://www.nomisweb.co.uk/api/v01/dataset/NM_218_1.data.csv?geography=2092957697&c_sex=0,1,2&measures=20100&time=latest&select=date_name,c_sex_name,soc2020_full_name,obs_value",
        "filename": "nomis_aps_occupation_national.csv",
        "description": "APS employment by occupation (SOC2020), Great Britain, national level",
    },
    # ── ONS baby names ─────────────────────────────────────────────────
    "ons_baby_names_boys": {
        "url": "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/birthsdeathsandmarriages/livebirths/datasets/babynamesenglandandwalesbabynamesstatisticsboys/2024/boysnames2024.xlsx",
        "filename": "boysnames2024.xlsx",
        "description": "ONS baby names — boys, England & Wales (2024 edition)",
    },
    "ons_baby_names_girls": {
        "url": "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/birthsdeathsandmarriages/livebirths/datasets/babynamesenglandandwalesbabynamesstatisticsgirls/2024/girlsnames2024.xlsx",
        "filename": "girlsnames2024.xlsx",
        "description": "ONS baby names — girls, England & Wales (2024 edition)",
    },
}


def download_file(url: str, dest: Path, description: str) -> bool:
    """Download a file with progress bar. Returns True on success."""
    if dest.exists():
        print(f"  ✓ Already exists: {dest.name}")
        return True

    print(f"  ↓ Downloading: {description}")
    print(f"    {url}")

    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ✗ Failed: {e}")
        return False

    total = int(resp.headers.get("content-length", 0))
    with open(dest, "wb") as f, tqdm(
        total=total, unit="B", unit_scale=True, desc=f"    {dest.name}", leave=True
    ) as bar:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            bar.update(len(chunk))

    size_mb = dest.stat().st_size / 1024 / 1024
    print(f"  ✓ Saved: {dest.name} ({size_mb:.1f} MB)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Download public datasets for synthetic motor insurance data")
    parser.add_argument(
        "--dataset",
        choices=list(DATASETS.keys()),
        nargs="+",
        help="Download specific dataset(s)",
    )
    parser.add_argument("--all", action="store_true", help="Download all datasets")
    parser.add_argument("--list", action="store_true", help="List available datasets")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable datasets:\n")
        for key, info in DATASETS.items():
            print(f"  {key:30s} {info['description']}")
        print()
        return

    if not args.all and not args.dataset:
        parser.print_help()
        print("\nTip: use --all to download everything, or --dataset <name> for specific ones")
        return

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    to_download = DATASETS if args.all else {k: DATASETS[k] for k in args.dataset}

    print(f"\n{'='*60}")
    print(f"Downloading {len(to_download)} dataset(s) to {RAW_DIR}")
    print(f"{'='*60}\n")

    results = {}
    for key, info in to_download.items():
        print(f"\n[{key}]")
        dest = RAW_DIR / info["filename"]
        ok = download_file(info["url"], dest, info["description"])
        results[key] = ok
        if ok:
            time.sleep(0.5)  # polite delay between requests

    # Summary
    print(f"\n{'='*60}")
    print("Summary:")
    print(f"{'='*60}")
    succeeded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    for key, ok in results.items():
        status = "✓" if ok else "✗"
        print(f"  {status} {key}")
    print(f"\n  {succeeded} succeeded, {failed} failed")

    if failed:
        print("\n  Note: some downloads failed. Retry with --dataset <name>")
        print("  URLs may have changed — check gov.uk for updated links")

    # Reminder about manual datasets
    print(f"\n{'='*60}")
    print("MANUAL DOWNLOADS NEEDED:")
    print(f"{'='*60}")
    print("""
  ONS Postcode Directory (ONSPD):
    → Register (free) at https://geoportal.statistics.gov.uk
    → Download ONSPD and place the CSV in data/raw/
    → We need the main ONSPD_NOV_2024_UK.csv (or latest)
    → Key columns: pcd (postcode), oslaua (local authority),
      imd (deprivation rank), ru11ind (urban/rural), rgn (region)
""")


if __name__ == "__main__":
    main()
