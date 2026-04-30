# Code Review — huc12-pipeline

Review scope: source files, docs, packaging metadata, `index.html`, the included `map.png`, and current generated NM/CO artifacts under `data/processed/` and `output/`. I also ran read-only consistency checks across GeoJSON, GeoParquet, GeoPackage, CSV, HUC-8 dissolves, per-HUC-8 subsets, metadata JSON, and HUC-8 overlaps for NM and CO.

## 1. Critical Issues

1. ✓ **Resolved — ArcGIS JSON fallback can silently corrupt polygon topology.** `_esri_geom_to_geojson()` now converts ArcGIS rings into proper Shapely `Polygon`/`MultiPolygon` geometries by containment depth, preserving holes and multipart shells before returning GeoJSON mappings. The `f=json` fallback remains only as an Esri-aware conversion path after retrying `f=geojson`.

## 2. Moderate Issues

1. ✓ **Resolved — REST fetching has timeouts but no retry/backoff or final count verification.** Fetching now uses a `requests.Session` with `HTTPAdapter`/`Retry` for 429/500/502/503, runs a `returnCountOnly` preflight, and asserts the paginated feature count matches the expected count.

2. ✓ **Resolved — CLI state input is not validated before entering SQL and file paths.** Both CLIs now reject non-`^[A-Z]{2}$` state values and warn explicitly for AK/HI/PR because the area and render projection are CONUS Albers.

3. **Deferred — Download links can drift from the data being displayed.** Not changed in this pass because the requested fix list did not include changing release/local download behavior. This should be handled as a deployment/config decision rather than folded into fetch robustness.

4. ✓ **Resolved — PMTiles generation can leave a partial, web-broken run.** PMTiles are now generated before metadata; metadata includes `pmtiles_huc12` and `pmtiles_huc8`; missing Tippecanoe now raises a clear runtime error instead of silently skipping required web-map assets.

5. ✓ **Resolved — Projects overlay validates fetch failures but not parsed JSON shape or URL safety.** `setupProjectsOverlay()` now requires an array or object-with-`projects` payload, accepts only 12-digit HUC-12 codes, caps accepted project codes at 500, and strips non-HTTP(S) project URLs.

6. ✓ **Resolved — Popup HTML escapes project text but not base WBD attributes.** The HUC-12 popup now escapes WBD string fields before inserting them into `setHTML()`.

7. **Deferred — Static and web HUC-4 color logic are now different.** Not changed in this pass because the requested priority list did not include palette unification, and changing static cartography can affect existing published outputs. Handle as a deliberate cartographic update.

8. **Deferred — Dependencies are not pinned for reproducible publication builds.** Not changed in this pass because adding a lock/constraints workflow affects installation policy and was not in the implementation list.

## 3. Minor / Polish

1. ✓ **Resolved — HUC-8 names are treated as mandatory even though the map can function without them.** `main()` now catches HUC-8 name-fetch failures, logs a warning, and continues with code-only labels.

2. ✓ **Resolved — Invalid-geometry audit file is only written when invalid geometries exist.** `process()` now always writes `huc12_<state>_invalid.csv`; clean runs get a header-only file.

3. ✓ **Resolved — Current generated artifacts are internally consistent, but that is not enforced by the pipeline.** Added `validate_outputs(state)` and a `--validate` fetch flag that checks output file existence, feature counts, HUC-8 subset membership/counts, metadata HUC-8 codes, and PMTiles flags when present.

4. ✓ **Resolved — Static map accepts non-CONUS states despite using CONUS Albers.** Both fetch and render CLIs now warn for AK/HI/PR.

5. **Deferred — CSV output is easy for spreadsheet tools to misinterpret.** Not changed in this pass because it was outside the requested fixes and needs a decision between documentation, quoting conventions, or Excel-specific export behavior.

6. ✓ **Resolved — About modal closes when clicking content without an `id`.** `closeAbout()` now closes on backdrop clicks only by comparing `ev.target` to the overlay element.

7. ✓ **Resolved — Docs contain stale operational details.** `PRD.md` now states that PMTiles is the default web output and that `npx serve . -p 8000` is the success-criteria server command.

8. **Deferred — Static legend is code-only and can be crowded.** Not changed in this pass because it was outside the requested fixes and should be handled together with any future static/web palette unification.

## 4. Strengths Worth Preserving

1. **Core output integrity is strong in current NM/CO artifacts.** Read-only checks found matching feature counts across GeoJSON, GeoParquet, GeoPackage, CSV, and metadata for both states; HUC code lengths and huc2/huc4/huc6/huc8 derivations from huc12 are consistent; all checked HUC-12 and HUC-8 geometries are valid.

2. **Pagination and transfer-limit handling are already directionally correct.** `fetch_huc_layer()` uses `resultOffset`, `resultRecordCount`, `exceededTransferLimit`, a conservative page size, and a polite pause (`huc12_pipeline/fetch.py:133`-`194`). The missing piece is resilience/verification, not a wholesale rewrite.

3. **CRS intent is explicit.** The fetch path requests and stores EPSG:4326 (`huc12_pipeline/fetch.py:162`, `204`), and area/static rendering transformations explicitly use `ESRI:102003` (`huc12_pipeline/fetch.py:235`; `huc12_pipeline/render.py:53`). This is clear and maintainable for the stated CONUS use case.

4. **The HUC-8 product design is useful and coherent.** The pipeline writes state-level HUC-12 data, HUC-8 dissolve data, per-HUC-8 subset GeoJSONs, and metadata size entries in one pass (`huc12_pipeline/fetch.py:275`-`324`). The current generated HUC-8 dissolves are valid and non-overlapping for NM and CO.

5. **The web map has good state-param hygiene.** `?state=` is whitelisted to the known state slugs before it influences local data paths (`index.html:453`-`465`), so arbitrary path traversal through the web URL is not present.

6. **Attribution is present in the main publication surfaces.** Static output cites USGS WBD and projection in the footer (`huc12_pipeline/render.py:119`-`122`), and the web map includes OSM/CARTO plus custom USGS WBD/Small Batch Maps attribution (`index.html:557`, `571`-`575`).
