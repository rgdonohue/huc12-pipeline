# CLAUDE.md — huc12-pipeline

> Goals, deliverables, and non-goals live in `PRD.md`. This file is the
> operating manual: state, decisions, file map, punch list, and how to run.

## Project goal
Per-state pipeline that turns a `--state XX` flag into a publication-quality
HUC-12 map (static PNG/PDF + interactive MapLibre web).

## Current state
**Two-state gallery complete (NM + CO, 2026-04-17). All Tier 1, 2, and 3 items done.**

Done (session 1 — core pipeline):
- Layer index dict fixed (was off-by-one, fetched HUC-10 not HUC-12).
- WBD 500 hardened: whitelist `outFields`, 500-record pages, HTTP 500 → retry with `f=json`.
- `exceededTransferLimit` pagination guard added.
- `make_valid()` now writes `huc12_<slug>_invalid.csv` sidecar audit.
- HUC-8 dissolve written to `data/processed/huc8_<slug>.geojson` on every fetch run.
- Both renderers (static + web) now color by **HUC-4** (NM has 19, CO has 17).
- Real `huc8_<slug>.geojson` loaded as a separate MapLibre source.
- Hover working: `promoteId` + `fill-opacity` feature-state expression.
- Web map auto-fits to data bounds via PMTiles header metadata.
- Dead code removed: `load_state_boundary()`, unused imports, `DATA_RAW`, `KEEP_FIELDS`.
- `.gitignore` added.

Done (session 2 — PMTiles, packaging, gallery):
- **PMTiles**: fetch pipeline calls Tippecanoe automatically if available; produces
  `huc12_<slug>.pmtiles` and `huc8_<slug>.pmtiles` (~7 MB each vs ~176 MB GeoJSON).
- **meta.json**: `huc12_<slug>_meta.json` written on every fetch; web map reads it
  to build the HUC-4 color map and stats bar without loading all features.
- **GeoPackage**: `huc12_<slug>.gpkg` written on every fetch.
- **Python package**: logic moved to `huc12_pipeline/` (`fetch.py`, `render.py`);
  scripts are thin shims; `pyproject.toml` with `huc12-fetch` / `huc12-render` entry points.
- **Web map rewritten** for PMTiles vector sources: `source-layer` on all layers,
  `setFeatureState` with `sourceLayer`, `promoteId` instead of `generateId`.
- **Download panel**: static links for GeoJSON, GPKG, CSV with file sizes via HEAD requests.
- **Popup fixes**: close button visible on dark bg, `closeOnClick: true`.
- **Labels toggle removed** (too many features at state zoom).
- **HUC-4 palette extended to 20 colors** in both render.py and index.html.
- **venv recreated** after directory migration broke symlinks (Python 3.14 now).
- **NM + CO** both fully processed: static maps at 300 DPI, PMTiles, meta.json.

Open (nothing — all tiers complete):
- Tier 3 #11 (retry/backoff) still deferred — acceptable for current use.
- Tier 3 #12 (pyogrio) still deferred — fiona works.

## Tech stack and key decisions

| Decision | Choice | One-line rationale |
|---|---|---|
| Data source | USGS National Map WBD MapServer | Authoritative, no API key, public domain. |
| HUC level | Layer **6** (HUC-12) | The PRD's only target; finer levels (14/16) excluded. |
| State filter | server-side `where=states LIKE '%XX%'` | Cheaper than a client-side spatial filter; safe for 2-letter postal codes. |
| Storage CRS | EPSG:4326 | Web map needs lat/lon; GeoJSON spec assumes it. |
| Display CRS | ESRI:102003 (Albers Equal Area, USA Contiguous) | Equal-area for honest sq mi numbers and undistorted print. CONUS only. |
| Palette | 20-color qualitative, cycled by **HUC-4** | NM has 19 HUC-4 codes; CO has 17. 20 colors = no cycling for any CONUS state. |
| Web stack | MapLibre GL JS 4.1.3 + pmtiles@3.2.1 + Carto basemap | No API key; ships in one HTML file; PMTiles eliminates 217 MB GeoJSON load. |
| Tiles | Tippecanoe PMTiles, Z5–auto | 176 MB GeoJSON → 7.4 MB; browser fetches only viewport tiles. |
| Env | per-project `.venv` at `huc12-pipeline/.venv` | Frozen deps, no global pollution. |

WBD layer table (verified 2026-04-16 via `?f=pjson`):
`0=WBDLine, 1=HUC2, 2=HUC4, 3=HUC6, 4=HUC8, 5=HUC10, 6=HUC12, 7=HUC14, 8=HUC16`.

## File map

```
huc12-pipeline/
├── PRD.md                          # what this is and what done means
├── CLAUDE.md                       # this file
├── README.md                       # install + run, for humans
├── pyproject.toml                  # package metadata + huc12-fetch/huc12-render entry points
├── requirements.txt                # pinned-by-floor deps
├── .gitignore
├── .venv/                          # gitignored
├── index.html                      # MapLibre + PMTiles web map (served from this directory)
├── huc12_pipeline/
│   ├── __init__.py
│   ├── fetch.py                    # WBD REST → GeoJSON + Parquet + GPKG + CSV + PMTiles
│   └── render.py                   # matplotlib PNG + PDF
├── scripts/
│   ├── fetch_huc12.py              # thin shim → huc12_pipeline.fetch.main
│   └── map_static.py               # thin shim → huc12_pipeline.render.main
├── data/                           # gitignored, regenerable
│   └── processed/
│       ├── huc12_<xx>.geojson
│       ├── huc12_<xx>.parquet
│       ├── huc12_<xx>.gpkg
│       ├── huc12_<xx>_summary.csv
│       ├── huc12_<xx>_invalid.csv  # empty if all geometries valid
│       ├── huc12_<xx>.pmtiles
│       ├── huc12_<xx>_meta.json
│       ├── huc8_<xx>.geojson
│       └── huc8_<xx>.pmtiles
└── output/                         # gitignored, regenerable
    ├── huc12_<xx>.png
    └── huc12_<xx>.pdf
```

## How to run

All commands assume `cwd = huc12-pipeline/`.

```bash
# One-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# optional — enables `huc12-fetch` / `huc12-render` CLI commands
.venv/bin/pip install -e .

# Each run
.venv/bin/python scripts/fetch_huc12.py --state NM   # fetches + generates PMTiles if tippecanoe available
.venv/bin/python scripts/map_static.py --state NM --dpi 300
npx serve . -p 8000   # then open http://localhost:8000/ — must use this, NOT python3 -m http.server
```

No automated tests. Verification is end-to-end: fetch → render → load web map →
spot-check a few HUC-12 polygons against the National Map Viewer.

## Tiered punch list

### Tier 1 — Must-fix before first run
| # | Problem | Status |
|---|---|---|
| 1 | `LAYER` dict off by one (fetched HUC-10) | **Fixed** |
| 2 | README paths didn't match repo layout | **Fixed** |
| 3 | First-run smoke test (NM end-to-end) | **Done** — 3,227 features, clean run 2026-04-17 |

### Tier 2 — Must-fix before showing anyone (credibility)
| # | Problem | Status |
|---|---|---|
| 4 | Web map's "HUC-8 boundary" was HUC-12 source styled thicker. | **Fixed** — real dissolve, separate `huc8_<slug>.geojson` source. |
| 5 | 12-color palette cycled across ~40 HUC-8 basins; adjacent collisions. | **Fixed** — colored by HUC-4, palette extended to 20. |
| 6 | Hover set `feature-state` but no paint read it; `e.features[0].id` was undefined. | **Fixed** — `promoteId` + feature-state `fill-opacity`. |
| 7 | Web map center/zoom hardcoded to NM. | **Fixed** — `map.fitBounds()` from PMTiles header. |
| 8 | Pagination ignored `exceededTransferLimit`. | **Fixed** — guard added. |
| 9 | `make_valid()` had no audit artifact. | **Fixed** — writes `huc12_<slug>_invalid.csv`. |
| 10 | `load_state_boundary()` was dead code. | **Fixed** — deleted, unused imports also removed. |

### Tier 3 — Done
| # | Problem | Status |
|---|---|---|
| 13 | 217 MB GeoJSON; web blocks on load. | **Fixed** — PMTiles via Tippecanoe. 7.4 MB, viewport-only fetches. |
| — | No installable package / CLI. | **Fixed** — `huc12_pipeline/` package, `pyproject.toml`, entry points. |
| — | Only NM tested. | **Fixed** — CO fetched, rendered, PMTiles generated. |

### Tier 3 — Still deferred
| # | Problem | Fix | Effort |
|---|---|---|---|
| 11 | No retry/backoff on REST requests. | `HTTPAdapter` + `Retry`. | S |
| 12 | `to_file` uses fiona; pyogrio is much faster. | Pass `engine="pyogrio"`. | S |
| 14 | No state-context overlay on static map. | Add light-gray CONUS state outlines. | M |

## Non-goals (this iteration)
See PRD §4. Headlines: no analysis, no AOI clipping, no multi-state, no
non-CONUS area accuracy, no hosting (Netlify/CF Pages is next logical step).

## Review workflow
- **Claude Code (this assistant):** plans tiered work, edits files, runs the
  pipeline, debugs failures. Updates this CLAUDE.md as state changes.
- **Codex CLI (parallel terminal, adversarial):** reviews diffs and the
  current tree against the PRD's success criteria. Returns numbered findings
  by severity (blocker / credibility / minor) with verification commands.
  Treat Codex findings as authoritative for severity tiering — when Codex
  elevates an item from Tier 3 → Tier 2, update the table here.
- **Richard:** decides scope changes, approves Tier 2+ work, reviews final
  outputs visually. Has stated pattern of "letting analysis substitute for
  shipping" — when in doubt, run the pipeline and look at the broken output
  rather than proposing more code review.
