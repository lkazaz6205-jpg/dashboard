"""
Load Caterpillar-style fault / anomaly code exports and suggest links to sensor parameters.

Beginner idea: we compare words in the diagnostic parameter name with words in past fault
descriptions. If several words match, we propose: "may be related to [fault text]".
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pandas as pd

# Short French stopwords — keeps matching simple and reduces noise.
_STOP = frozenset(
    "de du des la le les et ou un une au aux en à pour par sur dans est son sa ses ce ces "
    "qui que dont avec sans comme plus moins très".split()
)


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(text: str) -> set[str]:
    t = _strip_accents(str(text).lower())
    for ch in "':;.,\"()[]/\\-":
        t = t.replace(ch, " ")
    out: set[str] = set()
    for w in t.split():
        w = w.strip()
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w)
    return out


def load_fault_code_export(path: str | Path) -> pd.DataFrame:
    """
    Load a 'Code d'anomalie' export (e.g. 994F1 / 994F2 history files).

    Expects row 0 to be the header with at least Code + Date columns.
    """
    path = Path(path)
    df = pd.read_excel(path, sheet_name=0, header=0, engine="openpyxl")
    code_col = "Code d'anomalie"
    date_col = "Date de l'anomalie"
    if code_col not in df.columns or date_col not in df.columns:
        raise ValueError(
            f"{path.name}: missing {code_col!r} or {date_col!r}. "
            "Is this a fault-code history export?"
        )
    out = pd.DataFrame(
        {
            "fault_code": df[code_col].astype(str),
            "fault_time": pd.to_datetime(df[date_col], errors="coerce"),
        }
    )
    if "CID du code d'anomalie" in df.columns:
        out["cid"] = df["CID du code d'anomalie"]
    if "FMI du code d'anomalie" in df.columns:
        out["fmi"] = df["FMI du code d'anomalie"]
    if "Source" in df.columns:
        out["source"] = df["Source"]
    if "Type" in df.columns:
        out["fault_type"] = df["Type"]
    out["source_file"] = path.name
    out = out[out["fault_time"].notna() & (out["fault_code"].str.lower() != "nan")]
    out.sort_values("fault_time", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def load_fault_exports(paths: list[str | Path]) -> pd.DataFrame:
    frames = [load_fault_code_export(p) for p in paths]
    if not frames:
        return pd.DataFrame(columns=["fault_code", "fault_time", "source_file"])
    return pd.concat(frames, ignore_index=True).sort_values("fault_time").reset_index(drop=True)


def _param_short_name(full: str) -> str:
    """CH994.P1.Température X -> Température X"""
    s = str(full)
    for prefix in ("CH994.P1.", "CH994.P2.", "CH994."):
        if s.startswith(prefix):
            return s[len(prefix) :]
    return s


def best_fault_hint(
    diagnostic_param: str,
    faults: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    lookback_days: int = 540,
    min_overlap: int = 2,
) -> str | None:
    """
    Pick one past fault whose text shares enough words with the parameter name.

    Only faults strictly before `as_of` and within lookback_days are considered.
    """
    if faults.empty or pd.isna(as_of):
        return None
    end = pd.Timestamp(as_of)
    start = end - pd.Timedelta(days=lookback_days)
    hist = faults[(faults["fault_time"] >= start) & (faults["fault_time"] < end)]
    if hist.empty:
        return None

    p_tokens = _tokens(_param_short_name(diagnostic_param))
    if len(p_tokens) < 2:
        p_tokens = _tokens(diagnostic_param)
    if not p_tokens:
        return None

    best_score = 0
    best_code: str | None = None
    for code in hist["fault_code"].unique():
        ft = _tokens(code)
        inter = p_tokens & ft
        score = len(inter)
        if score >= min_overlap and score > best_score:
            best_score = score
            best_code = str(code)

    if not best_code:
        return None
    return best_code[:200] + ("…" if len(best_code) > 200 else "")


def batch_fault_hints(
    alert_rows: pd.DataFrame,
    faults: pd.DataFrame,
    *,
    param_col: str = "Paramètres Diagnostic",
    time_col: str = "Heure",
    lookback_days: int = 540,
    min_overlap: int = 2,
) -> list[str | None]:
    """For each row in alert_rows, compute best_fault_hint (vectorized loop; OK for modest sizes)."""
    out: list[str | None] = []
    for _, row in alert_rows.iterrows():
        out.append(
            best_fault_hint(
                str(row[param_col]),
                faults,
                as_of=row[time_col],
                lookback_days=lookback_days,
                min_overlap=min_overlap,
            )
        )
    return out
