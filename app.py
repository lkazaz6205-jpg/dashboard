"""
Streamlit dashboard: historical loader diagnostics, time series, threshold anomalies.

Run from this directory:
  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from anomalies import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    anomaly_summary,
    apply_thresholds,
    load_thresholds,
)
from data_loader import discover_xlsx_files, load_and_concat

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR
THRESHOLDS_PATH = BASE_DIR / "config" / "thresholds.yaml"


@st.cache_data(show_spinner=True)
def _load_selected_files(file_names: tuple[str, ...], data_dir: str) -> pd.DataFrame:
    d = Path(data_dir)
    paths = [d / name for name in file_names]
    return load_and_concat(paths)


def main() -> None:
    st.set_page_config(
        page_title="Loader digital twin — diagnostics",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("Loader diagnostics dashboard (994 F1)")
    st.caption(
        "Historical diagnostic parameters from Excel exports. "
        "Anomalies use configurable min/max thresholds (manufacturer-style)."
    )

    with st.sidebar:
        st.header("Data")
        data_dir = st.text_input("Data folder", value=str(DEFAULT_DATA_DIR))
        files = discover_xlsx_files(Path(data_dir))
        if not files:
            st.error("No .xlsx files in that folder.")
            st.stop()

        default_pick = [f.name for f in files if not f.name.startswith("~$")]
        selected = st.multiselect(
            "Excel files to load",
            options=[f.name for f in files],
            default=default_pick,
            help="Large files take a moment on first load (cached afterward).",
        )
        if not selected:
            st.warning("Select at least one file.")
            st.stop()

        thresholds_path = st.text_input("Thresholds YAML", value=str(THRESHOLDS_PATH))
        value_col = st.selectbox(
            "Value column for charts & limits",
            options=["Valeur moyenne", "Valeur maximale", "Valeur minimale"],
            index=0,
        )
        warn_margin = st.slider(
            "Default warning margin (% of min–max span)",
            min_value=1,
            max_value=40,
            value=10,
            help="Used when warn_low / warn_high are not set for a parameter.",
        )

    try:
        df = _load_selected_files(tuple(sorted(selected)), data_dir)
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    if df.empty:
        st.error("Loaded dataframe is empty.")
        st.stop()

    thresholds = load_thresholds(thresholds_path)
    scored = apply_thresholds(
        df,
        thresholds,
        value_col=value_col,
        default_warn_margin_pct=float(warn_margin),
    )

    params = sorted(scored["Paramètres Diagnostic"].dropna().unique().tolist())
    t_min = scored["Heure"].min()
    t_max = scored["Heure"].max()

    with st.sidebar:
        st.header("Filters")
        dr = st.date_input(
            "Date range (UTC)",
            value=(t_min.date(), t_max.date()),
            min_value=t_min.date(),
            max_value=t_max.date(),
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            d0, d1 = dr
        else:
            d0 = d1 = dr
        pick_params = st.multiselect("Parameters to plot", options=params, default=params[:4])

    mask_time = (scored["Heure"].dt.date >= d0) & (scored["Heure"].dt.date <= d1)
    filt = scored.loc[mask_time].copy()

    # KPI row
    n_crit = int((filt["severity"] == SEVERITY_CRITICAL).sum())
    n_warn = int((filt["severity"] == SEVERITY_WARNING).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows (filtered)", f"{len(filt):,}")
    c2.metric("Critical points", f"{n_crit:,}")
    c3.metric("Warning points", f"{n_warn:,}")
    c4.metric("Parameters", len(params))

    tab_ts, tab_anom, tab_sum, tab_help = st.tabs(
        ["Time series", "Anomaly map", "Summary", "Architecture & ML notes"]
    )

    with tab_ts:
        if not pick_params:
            st.info("Choose at least one parameter in the sidebar.")
        else:
            sub = filt[filt["Paramètres Diagnostic"].isin(pick_params)]
            if sub.empty:
                st.warning("No rows in the selected date range.")
            else:
                fig = px.line(
                    sub,
                    x="Heure",
                    y=value_col,
                    color="Paramètres Diagnostic",
                    markers=False,
                    title=f"{value_col} over time",
                )
                fig.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=-0.4))
                st.plotly_chart(fig, use_container_width=True)

                # Optional: show severity as scatter overlay for one parameter
                one = st.selectbox("Highlight severity (single parameter)", options=[None, *pick_params])
                if one:
                    s1 = sub[sub["Paramètres Diagnostic"] == one]
                    color_map = {"normal": "#94a3b8", "warning": "#f59e0b", "critical": "#ef4444"}
                    fig2 = px.scatter(
                        s1,
                        x="Heure",
                        y=value_col,
                        color="severity",
                        color_discrete_map=color_map,
                        title=f"{one} — severity",
                    )
                    st.plotly_chart(fig2, use_container_width=True)

    with tab_anom:
        st.subheader("Points by severity (filtered window)")
        agg = filt.groupby(["Paramètres Diagnostic", "severity"], observed=True).size().reset_index(name="n")
        if agg.empty:
            st.warning("No data.")
        else:
            fig_b = px.bar(
                agg,
                x="Paramètres Diagnostic",
                y="n",
                color="severity",
                barmode="group",
                color_discrete_map={
                    "normal": "#94a3b8",
                    "warning": "#f59e0b",
                    "critical": "#ef4444",
                },
            )
            fig_b.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig_b, use_container_width=True)

    with tab_sum:
        st.subheader("Anomaly counts (non-normal only)")
        summ = anomaly_summary(filt)
        if summ.empty:
            st.success("No warnings or critical points in this window (or no thresholds configured).")
        else:
            st.dataframe(summ, use_container_width=True, hide_index=True)

        st.subheader("Frequency by day (critical + warning)")
        bad = filt[filt["severity"] != "normal"].copy()
        if not bad.empty:
            bad["day"] = bad["Heure"].dt.date
            daily = bad.groupby("day").size().reset_index(name="events")
            fig_d = px.bar(daily, x="day", y="events", title="Alert events per day")
            st.plotly_chart(fig_d, use_container_width=True)

    with tab_help:
        st.markdown(
            """
### Tech stack (simple Python path)

| Piece | Role |
|-------|------|
| **Streamlit** | UI, filters, layout — fast to iterate for one machine. |
| **Pandas** | Load Excel → tidy table, filter by time and parameter. |
| **Plotly** | Interactive time series and bar charts (zoom, hover). |
| **PyYAML** | Thresholds in `config/thresholds.yaml` (no code change to tune limits). |

**Alternative:** **Dash** (Flask + React callbacks) gives finer multi-chart control; for a single-operator dashboard, Streamlit is usually quicker.

---

### Architecture (conceptual)

```mermaid
flowchart LR
  subgraph sources [Sources]
    XLSX[Excel exports]
    YAML[thresholds.yaml]
  end
  subgraph app [App]
    L[Loader / cache]
    A[Threshold engine]
    V[Streamlit views]
  end
  XLSX --> L
  YAML --> A
  L --> A
  A --> V
```

1. **Ingest:** `data_loader.py` normalizes different export layouts and concatenates months.  
2. **Rules:** `anomalies.py` labels each row using per-parameter min/max and warning bands.  
3. **Presentation:** `app.py` filters, plots, and aggregates.

Later, swap step 2 for an **ML scoring** column (keep the same UI by merging `severity_ml` next to `severity`).

---

### Deep learning ideas (later, offline first)

1. **LSTM or temporal CNN** on sliding windows of all sensors (multivariate). Train on “mostly normal” months; flag timesteps with high prediction error. Good when seasons and workload shift slowly.

2. **Autoencoder** on scaled feature vectors (one row = all channels at time *t*). Reconstruction error → anomaly score. Simple and strong baseline for multivariate correlation.

3. **Isolation Forest / robust z-score** as a lightweight bridge before deep models (often enough with clean features).

Start with **saved Parquet** slices of your 6 months and a single train script; only then wire scores back into this dashboard.

            """
        )


if __name__ == "__main__":
    main()
