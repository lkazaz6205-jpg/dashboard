"""
Threshold-based anomaly labels for loader diagnostic time series.

Compares the chosen value column (default: Valeur moyenne) to YAML limits per parameter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

SEVERITY_NORMAL = "normal"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


def load_thresholds(path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    params = raw.get("parameters") or {}
    return {str(k): dict(v) for k, v in params.items()}


def _warn_band(low: float | None, high: float | None, pct: float) -> tuple[float | None, float | None]:
    if low is None or high is None or high <= low:
        return None, None
    span = high - low
    margin = span * (pct / 100.0)
    return low + margin, high - margin


def classify_row(
    param: str,
    value: float,
    rules: dict[str, Any],
    *,
    default_warn_margin_pct: float,
) -> tuple[str, str | None]:
    """
    Return (severity, reason).

    Rules keys: min, max, warn_low, warn_high, warn_margin_pct (optional override).
    Critical if value < min or value > max (when those bounds are set).
    Warning if outside inner band [warn_low, warn_high] when provided; else inner band
    derived from min/max and warn_margin_pct.
    """
    if pd.isna(value):
        return SEVERITY_NORMAL, None

    r = rules.get(param) or {}
    vmin = r.get("min")
    vmax = r.get("max")
    try:
        vmin_f = float(vmin) if vmin is not None else None
    except (TypeError, ValueError):
        vmin_f = None
    try:
        vmax_f = float(vmax) if vmax is not None else None
    except (TypeError, ValueError):
        vmax_f = None

    if not r:
        return SEVERITY_NORMAL, None

    margin_pct = float(r.get("warn_margin_pct", default_warn_margin_pct))

    wl = r.get("warn_low")
    wh = r.get("warn_high")
    try:
        warn_low = float(wl) if wl is not None else None
    except (TypeError, ValueError):
        warn_low = None
    try:
        warn_high = float(wh) if wh is not None else None
    except (TypeError, ValueError):
        warn_high = None

    if warn_low is None and warn_high is None:
        warn_low, warn_high = _warn_band(vmin_f, vmax_f, margin_pct)

    # Critical: outside hard limits
    if vmin_f is not None and value < vmin_f:
        return SEVERITY_CRITICAL, "below_min"
    if vmax_f is not None and value > vmax_f:
        return SEVERITY_CRITICAL, "above_max"

    # Warning: approaching limits (inner band)
    if warn_low is not None and value < warn_low:
        return SEVERITY_WARNING, "low_band"
    if warn_high is not None and value > warn_high:
        return SEVERITY_WARNING, "high_band"

    return SEVERITY_NORMAL, None


def apply_thresholds(
    df: pd.DataFrame,
    thresholds: dict[str, dict[str, Any]],
    *,
    value_col: str = "Valeur moyenne",
    param_col: str = "Paramètres Diagnostic",
    default_warn_margin_pct: float = 10.0,
) -> pd.DataFrame:
    out = df.copy()
    if not thresholds:
        out["severity"] = SEVERITY_NORMAL
        out["severity_reason"] = pd.NA
        return out

    lim_rows: list[dict[str, Any]] = []
    for param, rule in thresholds.items():
        lim_rows.append(
            {
                param_col: param,
                "_t_min": rule.get("min"),
                "_t_max": rule.get("max"),
                "_w_low": rule.get("warn_low"),
                "_w_high": rule.get("warn_high"),
                "_w_margin_pct": rule.get("warn_margin_pct"),
            }
        )
    lim = pd.DataFrame(lim_rows)
    out = out.merge(lim, on=param_col, how="left")

    v = pd.to_numeric(out[value_col], errors="coerce")
    t_min = pd.to_numeric(out["_t_min"], errors="coerce")
    t_max = pd.to_numeric(out["_t_max"], errors="coerce")
    margin_pct = pd.to_numeric(out["_w_margin_pct"], errors="coerce").fillna(
        float(default_warn_margin_pct)
    ) / 100.0
    span = t_max - t_min
    valid_span = span.notna() & (span > 0)

    w_low_def = t_min + span * margin_pct
    w_high_def = t_max - span * margin_pct
    w_low_def = w_low_def.where(valid_span)
    w_high_def = w_high_def.where(valid_span)

    w_low = pd.to_numeric(out["_w_low"], errors="coerce")
    w_high = pd.to_numeric(out["_w_high"], errors="coerce")
    w_low_eff = w_low.where(w_low.notna(), w_low_def)
    w_high_eff = w_high.where(w_high.notna(), w_high_def)

    crit_low = t_min.notna() & (v < t_min)
    crit_high = t_max.notna() & (v > t_max)
    critical = crit_low | crit_high

    warn_low = ~critical & w_low_eff.notna() & (v < w_low_eff)
    warn_high = ~critical & w_high_eff.notna() & (v > w_high_eff)
    warning = warn_low | warn_high

    sev = np.where(critical, SEVERITY_CRITICAL, np.where(warning, SEVERITY_WARNING, SEVERITY_NORMAL))

    reason = np.full(len(out), None, dtype=object)
    reason = np.where(crit_low, "below_min", reason)
    reason = np.where(crit_high & ~crit_low, "above_max", reason)
    reason = np.where(warn_low & ~critical, "low_band", reason)
    reason = np.where(warn_high & ~critical & ~warn_low, "high_band", reason)

    out["severity"] = sev
    out["severity_reason"] = reason
    out.drop(
        columns=["_t_min", "_t_max", "_w_low", "_w_high", "_w_margin_pct"],
        errors="ignore",
        inplace=True,
    )
    return out


def format_threshold_cell(rules: dict[str, Any] | None) -> str:
    """Human-readable min/max for tables."""
    if not rules:
        return "—"
    vmin = rules.get("min")
    vmax = rules.get("max")
    parts: list[str] = []
    if vmin is not None:
        try:
            parts.append(f"min {float(vmin):g}")
        except (TypeError, ValueError):
            parts.append(f"min {vmin}")
    if vmax is not None:
        try:
            parts.append(f"max {float(vmax):g}")
        except (TypeError, ValueError):
            parts.append(f"max {vmax}")
    return " / ".join(parts) if parts else "—"


def alerts_detail_dataframe(
    df: pd.DataFrame,
    thresholds: dict[str, dict[str, Any]],
    *,
    value_col: str = "Valeur moyenne",
    param_col: str = "Paramètres Diagnostic",
) -> pd.DataFrame:
    """
    One row per anomaly point in df (warning or critical) with threshold text for display.
    """
    if df.empty or "severity" not in df.columns:
        return pd.DataFrame(
            columns=[
                "date",
                "sensor",
                "value",
                "threshold_yaml",
                "alert_level",
                "reason",
            ]
        )
    sub = df[df["severity"] != SEVERITY_NORMAL].copy()
    if sub.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "sensor",
                "value",
                "threshold_yaml",
                "alert_level",
                "reason",
            ]
        )
    rows: list[dict[str, Any]] = []
    for _, row in sub.iterrows():
        p = row[param_col]
        rules = thresholds.get(str(p)) if p is not None and not pd.isna(p) else None
        v = row[value_col]
        try:
            v_disp = float(v) if pd.notna(v) else None
        except (TypeError, ValueError):
            v_disp = None
        rows.append(
            {
                "date": row["Heure"] if "Heure" in sub.columns else row.name,
                "sensor": p,
                "value": v_disp,
                "threshold_yaml": format_threshold_cell(rules),
                "alert_level": row["severity"],
                "reason": row.get("severity_reason", ""),
            }
        )
    return pd.DataFrame(rows)


def anomaly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Count anomalies by parameter and severity (excludes normal)."""
    if df.empty or "severity" not in df.columns:
        return pd.DataFrame(columns=["Paramètres Diagnostic", "severity", "count"])
    sub = df[df["severity"] != SEVERITY_NORMAL]
    if sub.empty:
        return pd.DataFrame(columns=["Paramètres Diagnostic", "severity", "count"])
    g = (
        sub.groupby(["Paramètres Diagnostic", "severity"], observed=True)
        .size()
        .reset_index(name="count")
    )
    return g.sort_values(["count", "Paramètres Diagnostic"], ascending=[False, True])
