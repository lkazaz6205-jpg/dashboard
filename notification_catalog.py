"""
Load the 'notification / seuils métier' Excel: human-readable parameter + Seuil + Criticité.

We use fuzzy token overlap to attach a notification row to a diagnostic parameter name
(CH994.P1....) for display in the alerts table — the numeric engine can still use YAML.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from failure_history import _param_short_name, _tokens


def load_notification_workbook(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = xl.sheet_names[0]
    df = pd.read_excel(path, sheet_name=sheet, header=1, engine="openpyxl")
    rename = {
        "Equipement": "equipement",
        "Paramètre": "parametre",
        "Seuil": "seuil",
        "Criticité:": "criticite",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "parametre" not in df.columns or "seuil" not in df.columns:
        raise ValueError(f"{path.name}: expected columns Paramètre and Seuil (header row 1).")
    if "equipement" in df.columns:
        df["equipement"] = df["equipement"].ffill()
    df = df[df["parametre"].notna()].copy()
    df["parametre"] = df["parametre"].astype(str).str.strip()
    df["seuil"] = df["seuil"].apply(lambda x: str(x).strip() if pd.notna(x) else "")
    if "criticite" in df.columns:
        df["criticite"] = df["criticite"].astype(str).str.strip()
    df["source_file"] = path.name
    return df.reset_index(drop=True)


def match_notification_row(diagnostic_param: str, catalog: pd.DataFrame) -> dict | None:
    """
    Return the best catalog row as a dict (parametre, seuil, criticite, equipement), or None.
    """
    if catalog.empty:
        return None
    short = _param_short_name(diagnostic_param)
    t_short = _tokens(short)
    t_full = _tokens(diagnostic_param)
    best: tuple[int, int, int, dict] | None = None
    for _, row in catalog.iterrows():
        lab = str(row["parametre"])
        t_lab = _tokens(lab)
        inter_s = len(t_short & t_lab)
        inter_f = len(t_full & t_lab)
        score = max(inter_s, inter_f)
        tie = len(t_lab)  # prefer more specific labels on ties
        pack = {
            "parametre": lab,
            "seuil": row.get("seuil", ""),
            "criticite": row.get("criticite", ""),
            "equipement": row.get("equipement", ""),
        }
        cand = (score, tie, len(lab), pack)
        if score < 2:
            continue
        if best is None or cand[:3] > best[:3]:
            best = cand
    return None if best is None else best[3]
