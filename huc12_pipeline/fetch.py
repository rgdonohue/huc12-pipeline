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
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.geometry.polygon import orient
from urllib3.util.retry import Retry

WBD_BASE   = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
OUT_FIELDS = "huc12,name,states,areasqkm,loaddate,shape_Length,shape_Area"

# ~11 cm at the equator — orders of magnitude finer than HUC-12 boundary
# uncertainty, so safe to round. Shrinks downloadable GeoJSON files
# substantially vs. the 15-ish decimals Fiona writes by default.
GEOJSON_COORD_PRECISION = 6

LAYER = {
    "huc2":  1,
    "huc4":  2,
    "huc6":  3,
    "huc8":  4,
    "huc10": 5,
    "huc12": 6,   # target
}

DATA_PROCESSED = Path("data/processed")
STATE_RE = re.compile(r"^[A-Z]{2}$")
NON_CONUS_STATES = {"AK", "HI", "PR"}
RETRY_STATUSES = (429, 500, 502, 503)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=("GET",),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _raise_arcgis_error(data: dict) -> None:
    if "error" not in data:
        return
    err = data["error"]
    message = err.get("message", "ArcGIS REST error")
    details = "; ".join(err.get("details", []))
    raise RuntimeError(f"{message}{': ' + details if details else ''}")


def _request_json(session: requests.Session, url: str, params: dict) -> dict:
    resp = session.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    _raise_arcgis_error(data)
    return data


def _expected_count(session: requests.Session, url: str, where: str) -> int:
    data = _request_json(session, url, {
        "where": where,
        "returnCountOnly": "true",
        "f": "json",
    })
    if "count" not in data:
        raise RuntimeError(f"ArcGIS count response missing 'count': {data}")
    return int(data["count"])


def _close_ring(ring: list) -> list:
    if not ring:
        return ring
    closed = [tuple(pt) for pt in ring]
    if closed[0] != closed[-1]:
        closed.append(closed[0])
    return closed


def _esri_geom_to_geojson(esri_geom: dict) -> dict:
    """Convert ArcGIS polygon rings to valid GeoJSON Polygon/MultiPolygon geometry."""
    rings = esri_geom.get("rings", [])
    if not rings:
        return {"type": "Polygon", "coordinates": []}

    ring_items = []
    for ring in rings:
        closed = _close_ring(ring)
        if len(closed) < 4:
            continue
        poly = Polygon(closed)
        if poly.is_empty or poly.area == 0:
            continue
        ring_items.append({
            "coords": closed,
            "poly": poly,
            "point": poly.representative_point(),
        })

    if not ring_items:
        return {"type": "Polygon", "coordinates": []}

    for item in ring_items:
        containing = [
            other for other in ring_items
            if other is not item
            and other["poly"].area > item["poly"].area
            and other["poly"].contains(item["point"])
        ]
        item["depth"] = len(containing)

    shells = [item for item in ring_items if item["depth"] % 2 == 0]
    holes = [item for item in ring_items if item["depth"] % 2 == 1]

    polygons = []
    for shell in shells:
        shell_holes = []
        for hole in holes:
            if hole["depth"] != shell["depth"] + 1:
                continue
            if shell["poly"].contains(hole["point"]):
                shell_holes.append(hole["coords"])
        polygons.append(orient(Polygon(shell["coords"], shell_holes), sign=1.0))

    geom = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return mapping(geom)


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
    """Convert GeoJSON to PMTiles via Tippecanoe."""
    if not shutil.which("tippecanoe"):
        raise RuntimeError(
            "Tippecanoe is required to generate PMTiles for the web map. "
            "Install it (for example: brew install tippecanoe) or skip the web-map output explicitly in a future CLI mode."
        )
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


def validate_state_arg(state: str) -> str:
    normalized = state.upper()
    if not STATE_RE.fullmatch(normalized):
        raise ValueError(f"--state must be exactly two letters, got {state!r}")
    if normalized in NON_CONUS_STATES:
        print(
            f"Warning: {normalized} is outside the CONUS Albers target area; "
            "area and static-map distortion may be inappropriate.",
            file=sys.stderr,
        )
    return normalized


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_huc8_names(state_abbr: str, page_size: int = 500, pause: float = 0.5) -> dict:
    """
    Fetch HUC-8 code→name mapping from WBD layer 4 for all HUC-8s that
    intersect the given state. Used so the web map can show real basin
    names (Rio Grande, Pecos, San Juan) instead of 8-digit codes.
    """
    url = f"{WBD_BASE}/{LAYER['huc8']}/query"
    session = _build_session()
    where = f"states LIKE '%{state_abbr}%'"
    expected = _expected_count(session, url, where)
    names: dict = {}
    offset = 0
    fetched = 0
    print(f"Fetching HUC-8 names for state={state_abbr} ({expected:,} expected) …")
    while True:
        params = {
            "where":             where,
            "outFields":         "huc8,name",
            "f":                 "json",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
            "returnGeometry":    "false",
        }
        data = _request_json(session, url, params)
        feats = data.get("features", [])
        for f in feats:
            attrs = f.get("attributes", {})
            code = attrs.get("huc8") or attrs.get("HUC8")
            name = attrs.get("name") or attrs.get("Name")
            if code:
                names[code] = name or code
        fetched += len(feats)
        if not feats or not data.get("exceededTransferLimit", False):
            break
        offset += page_size
        time.sleep(pause)
    if fetched != expected:
        raise RuntimeError(
            f"HUC-8 name fetch returned {fetched:,} features, expected {expected:,}."
        )
    print(f"  {len(names)} HUC-8 names")
    return names


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
    session = _build_session()
    where = f"states LIKE '%{state_abbr}%'"
    expected = _expected_count(session, url, where)
    features = []
    offset = 0

    print(f"Fetching layer {layer_idx} for state={state_abbr} ({expected:,} expected) …")

    while True:
        params = {
            "where":             where,
            "outFields":         OUT_FIELDS,
            "outSR":             "4326",
            "f":                 "geojson",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
            "returnGeometry":    "true",
        }

        resp = session.get(url, params=params, timeout=60)

        if resp.status_code == 500:
            print(f"  HTTP 500 on f=geojson (offset={offset}), retrying with Esri JSON conversion …")
            params_retry = {**params, "f": "json"}
            data = _request_json(session, url, params_retry)
            page_features = _parse_esri_features(data)
            exceeded = data.get("exceededTransferLimit", False)
        else:
            resp.raise_for_status()
            data = resp.json()
            _raise_arcgis_error(data)
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

    if len(features) != expected:
        raise RuntimeError(
            f"Layer {layer_idx} fetch returned {len(features):,} features, expected {expected:,}."
        )

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
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    invalid_csv = DATA_PROCESSED / f"huc12_{state_abbr.lower()}_invalid.csv"
    if invalid.any():
        print(f"  Fixing {invalid.sum()} invalid geometries …")
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].make_valid()

    (gdf.loc[invalid, ["huc12", "name"]]
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

def save(gdf: gpd.GeoDataFrame, state_abbr: str, huc8_names: dict | None = None) -> None:
    """Export all output formats: GeoJSON, Parquet, GPKG, CSV, HUC-8 dissolve, PMTiles, meta."""
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    slug = state_abbr.lower()
    huc8_names = huc8_names or {}

    # GeoJSON (coordinate precision trimmed — see GEOJSON_COORD_PRECISION)
    geojson_path = DATA_PROCESSED / f"huc12_{slug}.geojson"
    gdf.to_file(geojson_path, driver="GeoJSON", COORDINATE_PRECISION=GEOJSON_COORD_PRECISION)
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

    # HUC-8 dissolved boundaries (attach name attribute if available)
    huc8_path = DATA_PROCESSED / f"huc8_{slug}.geojson"
    huc8 = gdf.dissolve(by="huc8").reset_index()[["huc8", "geometry"]]
    huc8["name"] = huc8["huc8"].map(lambda c: huc8_names.get(c, ""))
    huc8.to_file(huc8_path, driver="GeoJSON", COORDINATE_PRECISION=GEOJSON_COORD_PRECISION)
    print(f"  Saved HUC-8    → {huc8_path}")

    # Per-HUC-8 subset GeoJSONs — enables "download this basin" without
    # lasso-select or client-side filtering of the full state file.
    per_huc8_sizes: dict = {}
    for huc8_code, group in gdf.groupby("huc8"):
        subset_path = DATA_PROCESSED / f"huc12_{slug}_{huc8_code}.geojson"
        group.to_file(subset_path, driver="GeoJSON", COORDINATE_PRECISION=GEOJSON_COORD_PRECISION)
        per_huc8_sizes[huc8_code] = subset_path.stat().st_size
    print(f"  Saved {len(per_huc8_sizes)} per-HUC-8 GeoJSONs in {DATA_PROCESSED}/")

    # GeoPackage
    gpkg_path = DATA_PROCESSED / f"huc12_{slug}.gpkg"
    gdf.to_file(gpkg_path, driver="GPKG", layer=f"huc12_{slug}")
    size_mb = gpkg_path.stat().st_size / 1_048_576
    print(f"  Saved GPKG     → {gpkg_path}  ({size_mb:.1f} MB)")

    # PMTiles (required by the web map)
    pmtiles_path = DATA_PROCESSED / f"huc12_{slug}.pmtiles"
    _generate_pmtiles(geojson_path, pmtiles_path, "huc12", min_zoom=5)

    huc8_pmtiles_path = DATA_PROCESSED / f"huc8_{slug}.pmtiles"
    _generate_pmtiles(huc8_path, huc8_pmtiles_path, "huc8", min_zoom=4)

    # Metadata JSON (used by web map for color building and download link sizes)
    state_huc8_codes = sorted(gdf["huc8"].unique().tolist())
    meta = {
        "state":      slug.upper(),
        "huc2_codes": sorted(gdf["huc2"].unique().tolist()),
        "huc4_codes": sorted(gdf["huc4"].unique().tolist()),
        "huc8_codes": state_huc8_codes,
        "huc8_names": {c: huc8_names.get(c, "") for c in state_huc8_codes},
        "huc8_count": int(gdf["huc8"].nunique()),
        "huc12_count": int(len(gdf)),
        "area_sqmi":  round(float(gdf["area_sqmi"].sum()), 1),
        "download_sizes": {
            "geojson": geojson_path.stat().st_size,
            "gpkg":    gpkg_path.stat().st_size,
            "csv":     csv_path.stat().st_size,
        },
        "per_huc8_sizes": per_huc8_sizes,
        "pmtiles_huc12": pmtiles_path.exists(),
        "pmtiles_huc8":  huc8_pmtiles_path.exists(),
    }
    meta_path = DATA_PROCESSED / f"huc12_{slug}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"  Saved meta     → {meta_path}")

def validate_outputs(state_abbr: str) -> None:
    """Assert state output feature counts and metadata are internally consistent."""
    slug = state_abbr.lower()
    geojson_path = DATA_PROCESSED / f"huc12_{slug}.geojson"
    parquet_path = DATA_PROCESSED / f"huc12_{slug}.parquet"
    gpkg_path = DATA_PROCESSED / f"huc12_{slug}.gpkg"
    csv_path = DATA_PROCESSED / f"huc12_{slug}_summary.csv"
    huc8_path = DATA_PROCESSED / f"huc8_{slug}.geojson"
    meta_path = DATA_PROCESSED / f"huc12_{slug}_meta.json"
    huc12_pmtiles_path = DATA_PROCESSED / f"huc12_{slug}.pmtiles"
    huc8_pmtiles_path = DATA_PROCESSED / f"huc8_{slug}.pmtiles"

    paths = [
        geojson_path, parquet_path, gpkg_path, csv_path,
        huc8_path, meta_path, huc12_pmtiles_path, huc8_pmtiles_path,
    ]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise AssertionError(f"Missing output files: {', '.join(missing)}")

    geojson = gpd.read_file(geojson_path)
    parquet = gpd.read_parquet(parquet_path)
    gpkg = gpd.read_file(gpkg_path, layer=f"huc12_{slug}")
    csv = pd.read_csv(csv_path, dtype=str)
    huc8 = gpd.read_file(huc8_path)
    meta = json.loads(meta_path.read_text())

    counts = {
        "geojson": len(geojson),
        "parquet": len(parquet),
        "gpkg": len(gpkg),
        "csv": len(csv),
        "meta": int(meta["huc12_count"]),
    }
    if len(set(counts.values())) != 1:
        raise AssertionError(f"HUC-12 count mismatch: {counts}")

    huc8_count = int(meta["huc8_count"])
    if len(huc8) != huc8_count:
        raise AssertionError(f"HUC-8 count mismatch: huc8={len(huc8)}, meta={huc8_count}")

    subset_count = 0
    for path in DATA_PROCESSED.glob(f"huc12_{slug}_[0-9]*.geojson"):
        code = path.stem.split("_")[-1]
        subset = gpd.read_file(path)
        if not (subset["huc8"].astype(str) == code).all():
            raise AssertionError(f"Subset {path} contains features outside HUC-8 {code}")
        subset_count += len(subset)

    if subset_count != counts["geojson"]:
        raise AssertionError(
            f"Per-HUC-8 subset count mismatch: subsets={subset_count}, state={counts['geojson']}"
        )

    if sorted(geojson["huc8"].unique().tolist()) != meta["huc8_codes"]:
        raise AssertionError("Metadata huc8_codes do not match GeoJSON huc8 values")

    if "pmtiles_huc12" in meta and bool(meta["pmtiles_huc12"]) != huc12_pmtiles_path.exists():
        raise AssertionError("Metadata pmtiles_huc12 flag does not match file existence")
    if "pmtiles_huc8" in meta and bool(meta["pmtiles_huc8"]) != huc8_pmtiles_path.exists():
        raise AssertionError("Metadata pmtiles_huc8 flag does not match file existence")

    print(f"  Validated outputs for {state_abbr}: {counts['geojson']:,} HUC-12 units")


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
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate generated output counts and metadata after saving."
    )
    args = parser.parse_args()
    try:
        state = validate_state_arg(args.state)
    except ValueError as exc:
        parser.error(str(exc))

    gdf_raw   = fetch_huc_layer(LAYER["huc12"], state)
    gdf       = process(gdf_raw, state)
    try:
        huc8_name = fetch_huc8_names(state)
    except Exception as exc:
        print(
            f"Warning: could not fetch HUC-8 names for {state}; continuing with code-only labels. {exc}",
            file=sys.stderr,
        )
        huc8_name = {}
    save(gdf, state, huc8_names=huc8_name)
    if args.validate:
        validate_outputs(state)

    print("\nDone. Next steps:")
    print(f"  python scripts/map_static.py --state {state}")
    print("  npx serve . -p 8000  # then open http://localhost:8000/")


if __name__ == "__main__":
    main()
