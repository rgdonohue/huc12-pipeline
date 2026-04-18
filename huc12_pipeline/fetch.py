#!/usr/bin/env python3
"""
Download HUC-12 watershed boundaries for a given state from the USGS
Watershed Boundary Dataset (WBD) via the National Map ArcGIS REST API.
Exports GeoJSON, GeoParquet, GeoPackage, CSV, PMTiles, and a metadata JSON.

Usage (installed):
    huc12-fetch --state NM

Usage (from repo root):
    python scripts/fetch_huc12.py --state NM
"""

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import geopandas as gpd
import requests

WBD_BASE   = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
OUT_FIELDS = "huc12,name,states,areasqkm,loaddate,shape_Length,shape_Area"

LAYER = {
    "huc2":  1,
    "huc4":  2,
    "huc6":  3,
    "huc8":  4,
    "huc10": 5,
    "huc12": 6,   # target
}

DATA_PROCESSED = Path("data/processed")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _esri_geom_to_geojson(esri_geom: dict) -> dict:
    rings = esri_geom.get("rings", [])
    if not rings:
        return {"type": "Polygon", "coordinates": []}
    return {"type": "Polygon", "coordinates": rings}


def _parse_esri_features(esri_data: dict) -> list:
    return [
        {
            "type": "Feature",
            "geometry": _esri_geom_to_geojson(feat.get("geometry", {})),
            "properties": feat.get("attributes", {}),
        }
        for feat in esri_data.get("features", [])
    ]


def _generate_pmtiles(
    geojson_path: Path,
    output_path: Path,
    layer_name: str,
    min_zoom: int = 5,
) -> None:
    """Convert GeoJSON to PMTiles via Tippecanoe (skipped if not installed)."""
    if not shutil.which("tippecanoe"):
        print(f"  Skipping PMTiles — tippecanoe not found in PATH")
        return
    cmd = [
        "tippecanoe",
        f"-Z{min_zoom}", "-zg",
        "--drop-densest-as-needed",
        "--no-feature-limit",
        "--quiet",
        "-l", layer_name,
        "-o", str(output_path),
        "--force",
        str(geojson_path),
    ]
    subprocess.run(cmd, check=True)
    size_mb = output_path.stat().st_size / 1_048_576
    print(f"  Saved PMTiles  → {output_path}  ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_huc_layer(
    layer_idx: int,
    state_abbr: str,
    page_size: int = 500,
    pause: float = 0.5,
) -> gpd.GeoDataFrame:
    """
    Paginate through the WBD REST endpoint and return a GeoDataFrame
    for all features whose `states` field contains `state_abbr`.

    Args:
        layer_idx: WBD MapServer layer index (6 = HUC-12).
        state_abbr: Two-letter state abbreviation (e.g. "NM").
        page_size: Records per request (API max is typically 1000).
        pause: Seconds to wait between requests (be polite).

    Returns:
        GeoDataFrame in EPSG:4326.
    """
    url = f"{WBD_BASE}/{layer_idx}/query"
    features = []
    offset = 0

    print(f"Fetching layer {layer_idx} for state={state_abbr} …")

    while True:
        params = {
            "where":             f"states LIKE '%{state_abbr}%'",
            "outFields":         OUT_FIELDS,
            "outSR":             "4326",
            "f":                 "geojson",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
            "returnGeometry":    "true",
        }

        resp = requests.get(url, params=params, timeout=60)

        if resp.status_code == 500:
            print(f"  HTTP 500 on f=geojson (offset={offset}), retrying with f=json …")
            params_retry = {**params, "f": "json"}
            resp = requests.get(url, params=params_retry, timeout=60)
            resp.raise_for_status()
            page_features = _parse_esri_features(resp.json())
            exceeded = resp.json().get("exceededTransferLimit", False)
        else:
            resp.raise_for_status()
            data = resp.json()
            page_features = data.get("features", [])
            exceeded = data.get("exceededTransferLimit", False)

        if not page_features:
            break

        features.extend(page_features)
        print(f"  … fetched {len(features)} features so far (offset={offset})")

        if not exceeded and len(page_features) < page_size:
            break

        offset += page_size
        time.sleep(pause)

    print(f"Total features: {len(features)}")

    if not features:
        raise ValueError(
            f"No HUC-12 features found for state '{state_abbr}'. "
            "Check the abbreviation (e.g. 'NM', 'CO', 'AZ')."
        )

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    gdf.columns = [c.lower() for c in gdf.columns]
    return gdf


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

def process(gdf: gpd.GeoDataFrame, state_abbr: str) -> gpd.GeoDataFrame:
    """Clean and enrich: fix geometries, add hierarchy codes, compute area."""
    print("Processing …")

    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        print(f"  Fixing {invalid.sum()} invalid geometries …")
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].make_valid()
        DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
        invalid_csv = DATA_PROCESSED / f"huc12_{state_abbr.lower()}_invalid.csv"
        (gdf[invalid][["huc12", "name"]]
            .assign(reason="make_valid applied")
            .to_csv(invalid_csv, index=False))
        print(f"  Invalid geometry log → {invalid_csv}")

    gdf = gdf[~gdf.geometry.is_empty].copy()

    gdf["huc8"] = gdf["huc12"].str[:8]
    gdf["huc6"] = gdf["huc12"].str[:6]
    gdf["huc4"] = gdf["huc12"].str[:4]
    gdf["huc2"] = gdf["huc12"].str[:2]

    gdf_proj = gdf.to_crs("ESRI:102003")
    gdf["area_sqmi"] = (gdf_proj.geometry.area / 1_000_000 * 0.386102).round(2)

    gdf = gdf.sort_values("huc12").reset_index(drop=True)

    print(
        f"  {len(gdf)} HUC-12 units | "
        f"{gdf['huc8'].nunique()} HUC-8 parents | "
        f"{gdf['huc4'].nunique()} HUC-4 subregions | "
        f"{gdf['huc2'].nunique()} HUC-2 regions"
    )
    return gdf


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save(gdf: gpd.GeoDataFrame, state_abbr: str) -> None:
    """Export all output formats: GeoJSON, Parquet, GPKG, CSV, HUC-8 dissolve, PMTiles, meta."""
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    slug = state_abbr.lower()

    # GeoJSON
    geojson_path = DATA_PROCESSED / f"huc12_{slug}.geojson"
    gdf.to_file(geojson_path, driver="GeoJSON")
    size_mb = geojson_path.stat().st_size / 1_048_576
    print(f"  Saved GeoJSON  → {geojson_path}  ({size_mb:.1f} MB)")

    # GeoParquet
    parquet_path = DATA_PROCESSED / f"huc12_{slug}.parquet"
    gdf.to_parquet(parquet_path)
    print(f"  Saved Parquet  → {parquet_path}")

    # Summary CSV
    csv_path = DATA_PROCESSED / f"huc12_{slug}_summary.csv"
    gdf.drop(columns=["geometry"]).to_csv(csv_path, index=False)
    print(f"  Saved CSV      → {csv_path}")

    # HUC-8 dissolved boundaries
    huc8_path = DATA_PROCESSED / f"huc8_{slug}.geojson"
    huc8 = gdf.dissolve(by="huc8").reset_index()[["huc8", "geometry"]]
    huc8.to_file(huc8_path, driver="GeoJSON")
    print(f"  Saved HUC-8    → {huc8_path}")

    # GeoPackage
    gpkg_path = DATA_PROCESSED / f"huc12_{slug}.gpkg"
    gdf.to_file(gpkg_path, driver="GPKG", layer=f"huc12_{slug}")
    size_mb = gpkg_path.stat().st_size / 1_048_576
    print(f"  Saved GPKG     → {gpkg_path}  ({size_mb:.1f} MB)")

    # Metadata JSON (used by web map for color building and download link sizes)
    meta = {
        "state":      slug.upper(),
        "huc2_codes": sorted(gdf["huc2"].unique().tolist()),
        "huc4_codes": sorted(gdf["huc4"].unique().tolist()),
        "huc8_count": int(gdf["huc8"].nunique()),
        "huc12_count": int(len(gdf)),
        "area_sqmi":  round(float(gdf["area_sqmi"].sum()), 1),
        "download_sizes": {
            "geojson": geojson_path.stat().st_size,
            "gpkg":    gpkg_path.stat().st_size,
            "csv":     csv_path.stat().st_size,
        },
    }
    meta_path = DATA_PROCESSED / f"huc12_{slug}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"  Saved meta     → {meta_path}")

    # PMTiles (requires tippecanoe; skipped gracefully if not available)
    pmtiles_path = DATA_PROCESSED / f"huc12_{slug}.pmtiles"
    _generate_pmtiles(geojson_path, pmtiles_path, "huc12", min_zoom=5)

    huc8_pmtiles_path = DATA_PROCESSED / f"huc8_{slug}.pmtiles"
    _generate_pmtiles(huc8_path, huc8_pmtiles_path, "huc8", min_zoom=4)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Download USGS HUC-12 data for a US state."
    )
    parser.add_argument(
        "--state", default="NM",
        help="Two-letter state abbreviation (default: NM)"
    )
    args = parser.parse_args()
    state = args.state.upper()

    gdf_raw = fetch_huc_layer(LAYER["huc12"], state)
    gdf     = process(gdf_raw, state)
    save(gdf, state)

    print("\nDone. Next steps:")
    print(f"  python scripts/map_static.py --state {state}")
    print("  python -m http.server 8000  # then open http://localhost:8000/")


if __name__ == "__main__":
    main()
