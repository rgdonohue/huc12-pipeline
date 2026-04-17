# HUC-12 Pipeline — PRD

**Project name:** huc12-pipeline
**Author:** Richard / Small Batch Maps
**Status:** Scaffolding → first run cleanup
**Last updated:** 2026-04-16

---

## 1. Problem statement

Pulling and rendering USGS HUC-12 subwatershed boundaries for a single state shouldn't take a day. The USGS WBD MapServer is paginated, the GeoJSON outputs need cleanup, and the cartography needs a few decisions made up front (HUC-8 boundary dissolve, palette, projection). This is the shared toolkit so I never have to re-solve those problems for the next state.

---

## 2. Audience

- **Primary:** me, the next time I want a HUC-12 map of a US state.
- **Secondary:** future Small Batch Maps clients who ask for a watershed map of a specific state.
- **Tertiary:** anyone on GitHub who wants the same outputs without paying for ArcGIS.

---

## 3. Deliverables

The pipeline, given `--state XX`, produces:

1. `data/processed/huc12_<xx>.geojson` — HUC-12 polygons in EPSG:4326 with full HUC hierarchy (huc2…huc12), area in sq mi, source state list.
2. `data/processed/huc12_<xx>.parquet` — same content, GeoParquet for fast re-read.
3. `data/processed/huc12_<xx>_summary.csv` — attribute table (no geometry).
4. `data/processed/huc8_<xx>.geojson` — real HUC-8 dissolve for boundary overlays.
5. `output/huc12_<xx>.png` and `.pdf` — publication-quality static map, colored by HUC-4 parent (so adjacent basins don't share fills), HUC-8 boundary overlay.
6. `index.html` — MapLibre interactive map, auto-fit to data bounds, real HUC-8 overlay, working hover, click popups.

---

## 4. Non-goals

- **Not** an analytical tool. No upstream/downstream traversal, no AOI clipping by polygon, no fire/landcover joins. All of that lives in `santa-clara-watershed/` or future per-study projects.
- **Not** a multi-state or national tool. One state per run.
- **Not** HUC-10, HUC-14, or HUC-16. Layer 6 (HUC-12) only.
- **Not** AK/HI/PR–accurate. Albers Equal Area is CONUS-tuned; areas for non-CONUS states will be wrong.
- **Not** a hosted service. Local pipeline → local outputs.
- **Not** PMTiles by default. Documented as a follow-on for big states.

---

## 5. Success criteria

v1 ships when:

1. `python scripts/fetch_huc12.py --state NM` runs cleanly end-to-end and produces all four data files (including HUC-8 dissolve).
2. `python scripts/map_static.py --state NM` produces a PNG/PDF with no color collisions on adjacent basins, no fake legend entries, no clipped titles.
3. `python -m http.server 8000` from the project root → `http://localhost:8000/` loads the web map, auto-fits, real HUC-8 overlay visible, hover does something visible, click popup populated.
4. The same three commands work for one additional state (CO or AZ) without editing source — only `--state` changes.
5. Total wall-clock from `git clone` to a finished NM map is under five minutes (excluding `pip install`).

---

## 6. Open questions

- **Q1.** Big-state performance: do we add a default `geometryPrecision` parameter or a CLI `--simplify` flag, or just document PMTiles?
- **Q2.** Should `requirements.txt` move to `pyproject.toml` + uv/pdm? Cleaner but more setup.
- **Q3.** Does the static map deserve a context layer (state borders, neighboring states) for v1, or is that scope creep?
- **Q4.** Failure mode for invalid geometries: silent `make_valid()` (current), error out, or write a sidecar audit CSV (Codex's preference)?
