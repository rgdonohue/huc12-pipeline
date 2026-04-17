#!/usr/bin/env python3
"""
Render a publication-quality static map of HUC-12 watersheds for a state.
Outputs a high-resolution PNG and PDF.

Usage (installed):
    huc12-render --state NM

Usage (from repo root):
    python scripts/map_static.py --state NM --dpi 300
"""

import argparse
import math
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

DATA_PROCESSED = Path("data/processed")
OUTPUT_DIR     = Path("output")

BG_COLOR    = "#F0EDE8"
BORDER_COLOR = "#FFFFFF"
HUC8_COLOR  = "#333333"
TEXT_COLOR  = "#2B2B2B"
FONT_FAMILY = "DejaVu Sans"

HUC4_PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#D3A5C0", "#86BCB6", "#A0CBE8", "#FFBE7D", "#F1CE63",
    "#B6992D", "#499894", "#86BCB6", "#D4A6C8", "#FABFD2",
]


def assign_huc4_colors(gdf: gpd.GeoDataFrame) -> dict:
    huc4_codes = sorted(gdf["huc4"].unique())
    palette = HUC4_PALETTE * math.ceil(len(huc4_codes) / len(HUC4_PALETTE))
    return {code: palette[i] for i, code in enumerate(huc4_codes)}


def dissolve_huc8(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf.dissolve(by="huc8").reset_index()[["huc8", "geometry"]]


def render_map(gdf: gpd.GeoDataFrame, state_abbr: str, dpi: int = 200) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    mpl.rcParams["font.family"] = FONT_FAMILY

    gdf_plot = gdf.to_crs("ESRI:102003")
    huc8_dissolve = dissolve_huc8(gdf_plot)
    color_map = assign_huc4_colors(gdf)
    gdf_plot["color"] = gdf_plot["huc4"].map(color_map)

    bounds   = gdf_plot.total_bounds
    width_m  = bounds[2] - bounds[0]
    height_m = bounds[3] - bounds[1]
    aspect   = width_m / height_m

    fig_w = 12
    fig_h = fig_w / aspect + 2.5
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    gdf_plot.plot(ax=ax, color=gdf_plot["color"], edgecolor=BORDER_COLOR, linewidth=0.15, zorder=2)
    huc8_dissolve.boundary.plot(ax=ax, color=HUC8_COLOR, linewidth=0.8, zorder=3)

    pad_x = width_m * 0.03
    pad_y = height_m * 0.03
    ax.set_xlim(bounds[0] - pad_x, bounds[2] + pad_x)
    ax.set_ylim(bounds[1] - pad_y, bounds[3] + pad_y)
    ax.set_axis_off()

    huc4_codes   = sorted(gdf["huc4"].unique())
    max_legend   = 20
    truncated    = len(huc4_codes) > max_legend
    legend_codes = huc4_codes[:max_legend]

    patches = [
        mpatches.Patch(
            facecolor=color_map[code], edgecolor="#555555", linewidth=0.4,
            label=f"HUC-4 {code}",
        )
        for code in legend_codes
    ]
    if truncated:
        patches.append(mpatches.Patch(
            facecolor="none", edgecolor="none",
            label=f"… +{len(huc4_codes) - max_legend} more HUC-4 subregions",
        ))

    leg = ax.legend(
        handles=patches, title="HUC-4 Subregion", title_fontsize=7, fontsize=6.5,
        loc="lower left", framealpha=0.88, fancybox=False, edgecolor="#AAAAAA",
        ncol=2 if len(legend_codes) > 10 else 1, bbox_to_anchor=(0.01, 0.01),
    )
    leg.get_title().set_color(TEXT_COLOR)
    ax.add_artist(leg)
    ax.legend(
        handles=[mpl.lines.Line2D([], [], color=HUC8_COLOR, linewidth=1.2, label="HUC-8 boundary")],
        fontsize=6.5, loc="lower right", framealpha=0.88, fancybox=False,
        edgecolor="#AAAAAA", bbox_to_anchor=(0.99, 0.01),
    )

    n_huc12    = len(gdf)
    n_huc4     = gdf["huc4"].nunique()
    area_total = gdf["area_sqmi"].sum()

    fig.text(0.5, 0.97, f"HUC-12 Watersheds — {state_abbr}",
             ha="center", va="top", fontsize=15, fontweight="bold", color=TEXT_COLOR)
    fig.text(0.5, 0.935,
             f"{n_huc12:,} HUC-12 subwatersheds  ·  {n_huc4} HUC-4 subregions  ·  "
             f"{area_total:,.0f} sq mi total",
             ha="center", va="top", fontsize=8.5, color="#555555")
    fig.text(0.5, 0.015,
             "Source: USGS Watershed Boundary Dataset (WBD), National Map  ·  "
             "Albers Equal Area projection  ·  Small Batch Maps",
             ha="center", va="bottom", fontsize=6.5, color="#888888", style="italic")

    plt.tight_layout(rect=[0, 0.03, 1, 0.93])

    slug     = state_abbr.lower()
    png_path = OUTPUT_DIR / f"huc12_{slug}.png"
    pdf_path = OUTPUT_DIR / f"huc12_{slug}.pdf"
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor=BG_COLOR)
    fig.savefig(pdf_path, dpi=dpi, bbox_inches="tight", facecolor=BG_COLOR)
    print(f"  PNG → {png_path}")
    print(f"  PDF → {pdf_path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="NM")
    parser.add_argument("--dpi",   default=200, type=int)
    args  = parser.parse_args()
    state = args.state.upper()

    parquet_path = DATA_PROCESSED / f"huc12_{state.lower()}.parquet"
    geojson_path = DATA_PROCESSED / f"huc12_{state.lower()}.geojson"

    if parquet_path.exists():
        print(f"Loading {parquet_path} …")
        gdf = gpd.read_parquet(parquet_path)
    elif geojson_path.exists():
        print(f"Loading {geojson_path} …")
        gdf = gpd.read_file(geojson_path)
    else:
        raise FileNotFoundError(
            f"No processed data for '{state}'. "
            f"Run: huc12-fetch --state {state}"
        )

    print(f"Rendering map for {state} ({len(gdf):,} HUC-12 units) …")
    render_map(gdf, state, dpi=args.dpi)
    print("Done.")


if __name__ == "__main__":
    main()
