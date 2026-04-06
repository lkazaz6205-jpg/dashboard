"""
Load Caterpillar-style diagnostic Excel exports (long format: one row per parameter per timestamp).

Some exports use row 0 as the table header; others insert metadata and put the real header on row 9
(0-based index 8). We detect the header row by scanning for the Engin + Paramètres Diagnostic columns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

EXPECTED_COLS = {"Engin", "Heure", "Paramètres Diagnostic", "Valeur moyenne"}


def discover_xlsx_files(data_dir: Path, *, pattern: str = "*.xlsx") -> list[Path]:
    """Return sorted Excel files under data_dir (non-recursive)."""
    if not data_dir.is_dir():
        return []
    return sorted(data_dir.glob(pattern))


def _detect_header_row(path: Path, *, max_scan_rows: int = 40) -> int:
    peek = pd.read_excel(
        path,
        sheet_name=0,
        header=None,
        nrows=max_scan_rows,
        engine="openpyxl",
    )
    for i in range(len(peek)):
        row = peek.iloc[i]
        c0 = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        c1 = str(row.iloc[1]) if len(row) > 1 and pd.notna(row.iloc[1]) else ""
        if c0 == "Engin" and "Paramètres" in c1:
            return i
    return 0


def load_diagnostic_xlsx(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    header = _detect_header_row(path)
    df = pd.read_excel(path, sheet_name=0, header=header, engine="openpyxl")
    missing = EXPECTED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"{path.name}: after header row {header}, missing columns {missing}. "
            "Is this a diagnostic parameter export?"
        )
    df = df.copy()
    df["_source_file"] = path.name
    df["Heure"] = pd.to_datetime(df["Heure"], errors="coerce")
    for col in ("Valeur minimale", "Valeur moyenne", "Valeur maximale"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_and_concat(paths: Iterable[str | Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for p in paths:
        p = Path(p)
        try:
            frames.append(load_diagnostic_xlsx(p))
        except Exception as exc:  # noqa: BLE001 — surface in UI
            raise RuntimeError(f"Failed to load {p.name}: {exc}") from exc
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out.sort_values("Heure", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out
