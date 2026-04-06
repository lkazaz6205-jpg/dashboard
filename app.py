"""
Streamlit dashboard: historical loader diagnostics, time series, threshold anomalies.

Run from this directory:
  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from anomalies import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    alerts_detail_dataframe,
    anomaly_summary,
    apply_thresholds,
    load_thresholds,
)
from data_loader import (
    discover_diagnostic_xlsx_files,
    discover_fault_history_xlsx_files,
    discover_notification_xlsx_files,
    load_and_concat,
)
from failure_history import batch_fault_hints, load_fault_exports
from notification_catalog import load_notification_workbook, match_notification_row

BASE_DIR = Path(__file__).resolve().parent
# Railway: set DATA_DIR to a volume mount (e.g. /data) if Excel files are not in the image.
DEFAULT_DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
THRESHOLDS_PATH = Path(
    os.environ.get("THRESHOLDS_PATH", str(BASE_DIR / "config" / "thresholds.yaml"))
)


@st.cache_data(show_spinner=True)
def _load_selected_files(file_names: tuple[str, ...], data_dir: str) -> pd.DataFrame:
    d = Path(data_dir)
    paths = [d / name for name in file_names]
    return load_and_concat(paths)


@st.cache_data(show_spinner=False)
def _load_fault_exports_cached(paths_tuple: tuple[str, ...]) -> pd.DataFrame:
    if not paths_tuple:
        return pd.DataFrame(columns=["fault_code", "fault_time", "source_file"])
    return load_fault_exports(list(paths_tuple))


@st.cache_data(show_spinner=False)
def _load_notification_catalog_cached(path: str) -> pd.DataFrame:
    p = Path(path.strip())
    if not path.strip() or not p.is_file():
        return pd.DataFrame()
    return load_notification_workbook(p)


def _build_alerts_display(
    filt: pd.DataFrame,
    thresholds: dict,
    value_col: str,
    faults_df: pd.DataFrame,
    notif_cat: pd.DataFrame,
    lookback_days: int,
) -> tuple[pd.DataFrame, str]:
    """Table of alert rows + short markdown interpretation for Streamlit."""
    sub = filt[filt["severity"] != "normal"].copy()
    if sub.empty:
        return pd.DataFrame(), ""

    alerts = alerts_detail_dataframe(sub, thresholds, value_col=value_col)
    hints = (
        batch_fault_hints(sub, faults_df, lookback_days=lookback_days)
        if not faults_df.empty
        else [None] * len(alerts)
    )
    alerts["related_failure"] = hints

    seuils: list[str] = []
    crits: list[str] = []
    for p in alerts["sensor"]:
        m = match_notification_row(str(p), notif_cat) if not notif_cat.empty else None
        seuils.append(str((m or {}).get("seuil", "") or ""))
        crits.append(str((m or {}).get("criticite", "") or ""))
    alerts["seuil_metier"] = seuils
    alerts["criticite_metier"] = crits
    alerts["threshold_display"] = alerts.apply(
        lambda r: str(r["seuil_metier"]).strip() or r["threshold_yaml"], axis=1
    )

    disp = alerts[
        [
            "date",
            "sensor",
            "value",
            "threshold_display",
            "threshold_yaml",
            "alert_level",
            "criticite_metier",
            "related_failure",
        ]
    ].copy()
    disp.columns = [
        "Date",
        "Capteur",
        "Valeur",
        "Seuil (affichage)",
        "Seuil YAML min/max",
        "Niveau",
        "Criticité (notification)",
        "Défaut passé possible",
    ]

    lines: list[str] = []
    ok_hints = alerts["related_failure"].notna()
    paired = alerts.loc[ok_hints]
    if not paired.empty:
        uniq = paired.drop_duplicates(subset=["sensor", "related_failure"]).head(12)
        for _, r in uniq.iterrows():
            hint = r["related_failure"]
            lines.append(
                f"- **{r['sensor']}** — cette anomalie peut être rapprochée d’un défaut déjà vu : "
                f"« {hint} » (mots communs avec l’historique codes, fenêtre **{lookback_days}** jours "
                "avant la date de l’alerte)."
            )
    if not notif_cat.empty:
        lines.append(
            "- *Seuil (affichage)* reprend le **Seuil** du fichier notification lorsque l’intitulé "
            "correspond au capteur ; sinon on affiche les limites **YAML** utilisées pour le calcul."
        )
    body = "\n".join(lines) if lines else (
        "Aucune alerte dans la fenêtre sélectionnée, ou historique de codes non chargé / sans correspondance textuelle."
    )
    return disp, body


def main() -> None:
    st.set_page_config(
        page_title="Loader digital twin — diagnostics",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("Loader diagnostics dashboard (994 F1)")
    st.caption(
        "Historical diagnostic parameters from Excel exports. "
        "Anomalies use YAML min/max thresholds; optional fault history and notification "
        "workbooks add interpretation."
    )

    with st.sidebar:
        st.header("Data")
        data_dir = st.text_input("Data folder", value=str(DEFAULT_DATA_DIR))

    dpath = Path(data_dir)
    with st.sidebar:
        diag_files = discover_diagnostic_xlsx_files(dpath)
        fault_candidates = discover_fault_history_xlsx_files(dpath)
        notif_candidates = discover_notification_xlsx_files(dpath)

        if not diag_files:
            st.error("No diagnostic .xlsx files in that folder (need Engin / Paramètres Diagnostic).")
            st.stop()

        default_diag = [f.name for f in diag_files]
        selected = st.multiselect(
            "Diagnostic Excel files (time series)",
            options=[f.name for f in diag_files],
            default=default_diag,
            help="Only Caterpillar-style parameter exports. Fault-code files are chosen separately.",
        )
        if not selected:
            st.warning("Select at least one diagnostic file.")
            st.stop()

        st.subheader("Fault history (optional)")
        default_faults = [f.name for f in fault_candidates]
        fault_selected = st.multiselect(
            "Fault / event code exports",
            options=[f.name for f in fault_candidates],
            default=default_faults,
            help="Used to suggest 'related past fault' text when a sensor is in warning/critical.",
        )
        lookback_days = st.slider(
            "Fault match lookback (days)",
            min_value=30,
            max_value=730,
            value=540,
            help="When linking an alert to a past fault, only faults before that alert and within this window are used.",
        )

        st.subheader("Notification seuils (optional)")
        notif_default = str(notif_candidates[0]) if notif_candidates else ""
        notification_path = st.text_input(
            "Notification Excel path",
            value=notif_default,
            help="Workbook with Paramètre + Seuil + Criticité (e.g. notification Alerte Engins).",
        )

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

    fault_paths = tuple(str(dpath / n) for n in sorted(fault_selected) if n)
    faults_df = _load_fault_exports_cached(fault_paths)
    notif_cat = _load_notification_catalog_cached(notification_path.strip())

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

    tab_ts, tab_alerts, tab_anom, tab_sum, tab_help = st.tabs(
        ["Time series", "Alerts & interpretation", "Anomaly map", "Summary", "Architecture & ML notes"]
    )

    with tab_ts:
        if not pick_params:
            st.info("Choose at least one parameter in the sidebar.")
        else:
            sub = filt[filt["Paramètres Diagnostic"].isin(pick_params)]
            if sub.empty:
                st.warning("No rows in the selected date range.")
            else:
                palette = px.colors.qualitative.Plotly
                fig = go.Figure()
                for i, param in enumerate(pick_params):
                    s = sub[sub["Paramètres Diagnostic"] == param]
                    if s.empty:
                        continue
                    color = palette[i % len(palette)]
                    fig.add_trace(
                        go.Scatter(
                            x=s["Heure"],
                            y=s[value_col],
                            mode="lines",
                            name=str(param),
                            line=dict(color=color, width=1.8),
                            legendgroup=str(param),
                        )
                    )
                    bad = s[s["severity"] != "normal"]
                    if not bad.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=bad["Heure"],
                                y=bad[value_col],
                                mode="markers",
                                name=f"{param} (alerte)",
                                marker=dict(
                                    size=11,
                                    color=bad["severity"].map(
                                        {"warning": "#f59e0b", "critical": "#ef4444"}
                                    ),
                                    symbol="circle-open",
                                    line=dict(width=2),
                                ),
                                legendgroup=str(param),
                            )
                        )
                fig.update_layout(
                    title=f"{value_col} dans le temps (marqueurs = avertissement ou critique)",
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=-0.45),
                    margin=dict(b=120),
                )
                st.plotly_chart(fig, width="stretch")

                one = st.selectbox("Vue détail gravité (un seul capteur)", options=[None, *pick_params])
                if one:
                    s1 = sub[sub["Paramètres Diagnostic"] == one]
                    color_map = {"normal": "#94a3b8", "warning": "#f59e0b", "critical": "#ef4444"}
                    fig2 = px.scatter(
                        s1,
                        x="Heure",
                        y=value_col,
                        color="severity",
                        color_discrete_map=color_map,
                        title=f"{one} — gravité",
                    )
                    st.plotly_chart(fig2, width="stretch")

    alerts_table, interpret_md = _build_alerts_display(
        filt, thresholds, value_col, faults_df, notif_cat, lookback_days
    )
    with tab_alerts:
        st.subheader("Table des alertes")
        if alerts_table.empty:
            st.success("Aucun point en avertissement ou critique dans cette fenêtre.")
        else:
            st.caption(
                f"{len(alerts_table):,} lignes — normal / warning / critical viennent du **YAML** ; "
                "la colonne *Défaut passé possible* est une **piste** (similarité de mots, pas un diagnostic)."
            )
            st.dataframe(alerts_table, width="stretch", hide_index=True)

        st.subheader("Interprétation des alertes")
        st.markdown(interpret_md)

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
            st.plotly_chart(fig_b, width="stretch")

    with tab_sum:
        st.subheader("Anomaly counts (non-normal only)")
        summ = anomaly_summary(filt)
        if summ.empty:
            st.success("No warnings or critical points in this window (or no thresholds configured).")
        else:
            st.dataframe(summ, width="stretch", hide_index=True)

        st.subheader("Frequency by day (critical + warning)")
        bad = filt[filt["severity"] != "normal"].copy()
        if not bad.empty:
            bad["day"] = bad["Heure"].dt.date
            daily = bad.groupby("day").size().reset_index(name="events")
            fig_d = px.bar(daily, x="day", y="events", title="Alert events per day")
            st.plotly_chart(fig_d, width="stretch")

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
    XLSX[Séries diag Excel]
    YAML[thresholds.yaml]
    FAULT[Exports codes anomalie]
    NOTIF[Seuils notification]
  end
  subgraph app [App]
    L[Loader / cache]
    A[Moteur seuils YAML]
    H[Historique codes]
    V[Streamlit]
  end
  XLSX --> L
  YAML --> A
  FAULT --> H
  NOTIF --> V
  L --> A
  A --> V
  H --> V
```

1. **Ingest:** `data_loader.py` détecte la ligne d’en-tête et concatène les mois.  
2. **Seuils:** `anomalies.py` classe chaque point (normal / warning / critical) avec le YAML.  
3. **Contexte:** `failure_history.py` et `notification_catalog.py` enrichissent le tableau d’alertes.  
4. **PARETO / réparations** (fichier type chargeuse / D11T) : mise en page narrative — pas encore lue automatiquement ; à intégrer plus tard si vous normalisez les colonnes.

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
