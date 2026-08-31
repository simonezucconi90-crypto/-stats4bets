import base64
import io
import json
import os
import sqlite3
import time
import math
import re
import hashlib
from datetime import datetime, timezone
from itertools import combinations

import pandas as pd
import numpy as np
import requests
import streamlit as st

st.set_page_config(page_title="Stats4Bets", page_icon="📊", layout="wide")

METHOD_COLUMNS = {
    "1X2": "flag_1x2",
    "Over 1.5": "flag_over_15",
    "Over 2.5": "flag_over_25",
    "Under 2.5": "flag_under_25",
    "Under 3.5": "flag_under_35",
    "Multigol 1-3": "flag_multigol_13",
    "Multigol 1-4": "flag_multigol_14",
    "Formula 4": "flag_formula4",
    "Easy Over": "flag_easy_over",
    "Super Over": "flag_super_over",
}

BASE_COLUMNS = [
    "id","date","time","league","match_name","round_name","market","pick",
    "selected_by_ale","associated_method","prob_1","prob_x","prob_2",
    "fair_odds","opening_odds","current_odds","c_aff","c_aff_count","flbk","c_fb",
    "qra_qa","qi_qa","allibramento_color","allibramento_value",
    "allibramento_avg","allb","mtr","scl","cal","status","stake",
    "played_odds","outcome","final_score","gross_return","profit"
]
ALL_COLUMNS = BASE_COLUMNS + list(METHOD_COLUMNS.values())

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    date TEXT,
    time TEXT,
    league TEXT,
    match_name TEXT,
    round_name TEXT,
    market TEXT,
    pick TEXT,
    selected_by_ale TEXT,
    associated_method TEXT,
    prob_1 REAL,
    prob_x REAL,
    prob_2 REAL,
    fair_odds REAL,
    opening_odds REAL,
    current_odds REAL,
    c_aff TEXT,
    c_aff_count INTEGER,
    flbk TEXT,
    c_fb TEXT,
    qra_qa TEXT,
    qi_qa TEXT,
    allibramento_color TEXT,
    allibramento_value REAL,
    allibramento_avg REAL,
    allb TEXT,
    mtr TEXT,
    scl TEXT,
    cal TEXT,
    status TEXT,
    stake REAL,
    played_odds REAL,
    outcome TEXT,
    final_score TEXT,
    gross_return REAL,
    profit REAL,
    flag_1x2 INTEGER DEFAULT 0,
    flag_over_15 INTEGER DEFAULT 0,
    flag_over_25 INTEGER DEFAULT 0,
    flag_under_25 INTEGER DEFAULT 0,
    flag_under_35 INTEGER DEFAULT 0,
    flag_multigol_13 INTEGER DEFAULT 0,
    flag_multigol_14 INTEGER DEFAULT 0,
    flag_formula4 INTEGER DEFAULT 0,
    flag_easy_over INTEGER DEFAULT 0,
    flag_super_over INTEGER DEFAULT 0
);
"""

def secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


def github_configured():
    return all([
        secret("GITHUB_TOKEN"),
        secret("GITHUB_OWNER"),
        secret("GITHUB_REPO"),
        secret("GITHUB_WORKFLOW"),
    ])


def github_headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f'Bearer {secret("GITHUB_TOKEN")}',
        "X-GitHub-Api-Version": "2022-11-28",
    }


def trigger_named_workflow(workflow_file):
    owner = secret("GITHUB_OWNER")
    repo = secret("GITHUB_REPO")
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/"
        f"workflows/{workflow_file}/dispatches"
    )
    response = requests.post(
        url,
        headers=github_headers(),
        json={"ref": "main"},
        timeout=30,
    )
    if response.status_code != 204:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(
            f"GitHub ha risposto con errore {response.status_code}: {detail}"
        )


def latest_named_workflow_run(workflow_file):
    owner = secret("GITHUB_OWNER")
    repo = secret("GITHUB_REPO")
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/"
        f"workflows/{workflow_file}/runs"
    )
    response = requests.get(
        url,
        headers=github_headers(),
        params={"branch": "main", "per_page": 1},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Impossibile leggere lo stato del workflow: {response.status_code}"
        )
    runs = response.json().get("workflow_runs", [])
    return runs[0] if runs else None


def run_and_wait_named_workflow(workflow_file, timeout_seconds=150):
    previous = latest_named_workflow_run(workflow_file)
    previous_id = previous.get("id") if previous else None

    trigger_named_workflow(workflow_file)
    start = time.time()

    while time.time() - start < timeout_seconds:
        run = latest_named_workflow_run(workflow_file)
        if run and run.get("id") != previous_id:
            if run.get("status") == "completed":
                return {
                    "ok": run.get("conclusion") == "success",
                    "conclusion": run.get("conclusion"),
                    "url": run.get("html_url", ""),
                }
        time.sleep(5)

    return {"ok": False, "conclusion": "timeout", "url": ""}


def trigger_collector_workflow():
    owner = secret("GITHUB_OWNER")
    repo = secret("GITHUB_REPO")
    workflow = secret("GITHUB_WORKFLOW")
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
    response = requests.post(
        url,
        headers=github_headers(),
        json={"ref": "main"},
        timeout=30,
    )
    if response.status_code != 204:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(
            f"GitHub ha risposto con errore {response.status_code}: {detail}"
        )


def latest_collector_run():
    owner = secret("GITHUB_OWNER")
    repo = secret("GITHUB_REPO")
    workflow = secret("GITHUB_WORKFLOW")
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/runs"
    response = requests.get(
        url,
        headers=github_headers(),
        params={"branch": "main", "per_page": 1},
        timeout=30,
    )
    if response.status_code != 200:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(
            f"Impossibile leggere lo stato del workflow ({response.status_code}): {detail}"
        )
    runs = response.json().get("workflow_runs", [])
    return runs[0] if runs else None


def wait_for_collector(timeout_seconds=150, poll_seconds=5):
    start = time.time()
    initial = latest_collector_run()
    initial_id = initial.get("id") if initial else None

    while time.time() - start < timeout_seconds:
        run = latest_collector_run()
        if run:
            run_id = run.get("id")
            status = run.get("status")
            conclusion = run.get("conclusion")
            if run_id != initial_id or status in {"queued", "in_progress", "completed"}:
                if status == "completed":
                    return {
                        "ok": conclusion == "success",
                        "conclusion": conclusion,
                        "url": run.get("html_url", ""),
                    }
        time.sleep(poll_seconds)

    return {"ok": False, "conclusion": "timeout", "url": ""}


def use_supabase():
    return bool(secret("SUPABASE_URL") and secret("SUPABASE_KEY"))

def supabase_client():
    from supabase import create_client
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY"))

def sqlite_connection():
    con = sqlite3.connect("stats4bets.db")
    con.execute(CREATE_TABLE)
    return con

def storage_label():
    return "Supabase (persistente)" if use_supabase() else "SQLite locale (temporaneo sul cloud)"

def normalize_record(record):
    clean = {}
    for col in ALL_COLUMNS:
        value = record.get(col)
        if pd.isna(value) if not isinstance(value, (list, dict)) else False:
            value = None
        clean[col] = value
    return clean

def get_matches():
    if use_supabase():
        rows = supabase_client().table("matches").select("*").order("date").order("time").order("id").execute().data
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=ALL_COLUMNS)
        for col in ALL_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[ALL_COLUMNS]
    with sqlite_connection() as con:
        return pd.read_sql_query("SELECT * FROM matches ORDER BY date,time,id", con)

def next_id():
    df = get_matches()
    if df.empty:
        return "0001"
    nums = pd.to_numeric(df["id"], errors="coerce").dropna()
    return f"{int(nums.max() if not nums.empty else 0)+1:04d}"

def save_record(record):
    record = normalize_record(record)
    if use_supabase():
        supabase_client().table("matches").insert(record).execute()
    else:
        cols = list(record.keys())
        with sqlite_connection() as con:
            con.execute(
                f"INSERT INTO matches ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                [record[c] for c in cols],
            )
            con.commit()

def update_record(match_id, values):
    # Tiene solo colonne valide e converte tutti i valori in tipi
    # compatibili con JSON/PostgREST/Supabase.
    clean_values = {}

    for key, value in values.items():
        if key not in ALL_COLUMNS:
            continue

        # pd.NA / NaN / NaT -> NULL su Supabase
        try:
            if pd.isna(value):
                value = None
        except Exception:
            pass

        # Tipi NumPy -> tipi Python standard
        if isinstance(value, np.generic):
            value = value.item()

        # Timestamp/date-like -> stringa ISO
        if isinstance(value, (pd.Timestamp, datetime)):
            value = value.isoformat()

        clean_values[key] = value

    if not clean_values:
        return

    if use_supabase():
        (
            supabase_client()
            .table("matches")
            .update(clean_values)
            .eq("id", str(match_id))
            .execute()
        )
    else:
        sets = ",".join([f"{k}=?" for k in clean_values])
        with sqlite_connection() as con:
            con.execute(
                f"UPDATE matches SET {sets} WHERE id=?",
                list(clean_values.values()) + [match_id],
            )
            con.commit()


def delete_record(match_id):
    if use_supabase():
        supabase_client().table("matches").delete().eq("id", match_id).execute()
    else:
        with sqlite_connection() as con:
            con.execute("DELETE FROM matches WHERE id=?", (match_id,))
            con.commit()

def blank_match():
    return {
        "id": next_id(),
        "date": datetime.today().strftime("%Y-%m-%d"),
        "time": "",
        "league": "",
        "match_name": "",
        "round_name": "",
        "market": "1X2",
        "pick": "1",
        "selected_by_ale": "Ottimo 1",
        "associated_method": "",
        "prob_1": 0.0,
        "prob_x": 0.0,
        "prob_2": 0.0,
        "fair_odds": 0.0,
        "opening_odds": 0.0,
        "current_odds": 0.0,
        "c_aff": "",
        "c_aff_count": None,
        "flbk": "",
        "c_fb": "",
        "qra_qa": "",
        "qi_qa": "",
        "allibramento_color": "",
        "allibramento_value": 0.0,
        "allibramento_avg": 0.0,
        "allb": "",
        "mtr": "",
        "scl": "",
        "cal": "",
        "status": "",
        "stake": 20.0,
        "played_odds": 0.0,
        "outcome": None,
        "final_score": None,
        "gross_return": None,
        "profit": None,
        **{col: 0 for col in METHOD_COLUMNS.values()},
    }

def parse_json_text(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def extract_file(uploaded):
    if uploaded.type == "application/pdf":
        raise RuntimeError("L'OCR gratuito legge immagini PNG/JPG/WEBP. Per un PDF salva uno screenshot della pagina.")

    from free_ocr import read_screenshot
    extracted, ocr_text = read_screenshot(uploaded.getvalue())

    data = blank_match()
    data.update(extracted)
    flags = extracted.get("method_flags", {})
    for label, column in METHOD_COLUMNS.items():
        data[column] = 1 if flags.get(label) else 0

    data["stake"] = 20.0
    data["played_odds"] = float(data.get("current_odds") or 0)
    data["_ocr_text"] = ocr_text
    return data

def summary(df):
    if df.empty:
        return dict(total=0,closed=0,wins=0,losses=0,win_rate=0,staked=0,profit=0,roi=0,avg_odds=0)
    closed = df[df["outcome"].isin(["V","P"])].copy()
    staked = pd.to_numeric(closed["stake"], errors="coerce").fillna(0).sum()
    profit = pd.to_numeric(closed["profit"], errors="coerce").fillna(0).sum()
    wins = int((closed["outcome"]=="V").sum())
    return {
        "total": len(df),
        "closed": len(closed),
        "wins": wins,
        "losses": int((closed["outcome"]=="P").sum()),
        "win_rate": wins/len(closed)*100 if len(closed) else 0,
        "staked": float(staked),
        "profit": float(profit),
        "roi": float(profit/staked*100) if staked else 0,
        "avg_odds": float(pd.to_numeric(closed["played_odds"], errors="coerce").mean()) if len(closed) else 0,
    }


def combo_table(df, min_sample=3, max_filters=3):
    closed = df[df["outcome"].isin(["V","P"])].copy()
    if closed.empty:
        return pd.DataFrame()

    dims = {}
    categorical = {
        "ALLB": "allibramento_color",
        "SCL": "scl",
        "MTR": "mtr",
        "C.AFF": "c_aff",
        "FLBK": "flbk",
        "C.FB": "c_fb",
        "QRA/QA": "qra_qa",
        "QI/QA": "qi_qa",
        "CAL": "cal",
        "STATUS": "status",
        "Campionato": "league",
    }

    for label, column in categorical.items():
        if column not in closed.columns:
            continue
        for value in closed[column].dropna().astype(str).unique():
            if value and value.lower() != "nan":
                dims[f"{label}={value}"] = (column, value)

    dims.update({label:(col,1) for label,col in METHOD_COLUMNS.items()})

    results = []
    items = list(dims.items())
    for size in range(1, max_filters + 1):
        for combo in combinations(items, size):
            cols = [spec[0] for _, spec in combo]
            if len(cols) != len(set(cols)):
                continue

            subset = closed.copy()
            for _, (column, value) in combo:
                subset = subset[subset[column] == value]

            if len(subset) < min_sample:
                continue

            s = summary(subset)
            reliability = min(1.0, s["closed"] / max(30, min_sample * 3))
            score = (s["roi"] * reliability) + ((s["profit"] / 20) * reliability)

            results.append({
                "Combinazione": " + ".join(label for label, _ in combo),
                "Partite": s["closed"],
                "Vinte": s["wins"],
                "Perse": s["losses"],
                "Win rate %": round(s["win_rate"], 2),
                "Quota media": round(s["avg_odds"], 2),
                "Profitto €": round(s["profit"], 2),
                "ROI %": round(s["roi"], 2),
                "Affidabilità %": round(reliability * 100, 1),
                "Punteggio": round(score, 2),
            })

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values(
        ["Punteggio", "Profitto €", "ROI %", "Partite"],
        ascending=[False, False, False, False]
    )


def apply_strategy_filters(df, filters, min_odds, max_odds, min_prob, max_prob):
    """
    Filtro manuale del Laboratorio.

    Usa la stessa normalizzazione del motore automatico:
    - stringhe ripulite dagli spazi;
    - quota = current_odds;
    - estremi inclusi.
    """
    filtered = df.copy()

    for column, values in filters.items():
        if values:
            wanted = {str(v).strip() for v in values}
            series = (
                filtered[column]
                .fillna("")
                .astype(str)
                .str.strip()
            )
            filtered = filtered[series.isin(wanted)]

    odds = pd.to_numeric(
        filtered["current_odds"],
        errors="coerce",
    )
    filtered = filtered[
        (odds >= float(min_odds))
        & (odds <= float(max_odds))
    ]

    probs = pd.to_numeric(
        filtered["prob_1"],
        errors="coerce",
    )
    filtered = filtered[
        (probs >= float(min_prob))
        & (probs <= float(max_prob))
    ]

    return filtered


def strategy_dataset_signature(df):
    """
    Firma del database usato dal motore.

    Se cambia una partita, un esito, una quota o un indicatore,
    cambia anche la firma e la classifica viene ricalcolata.
    """
    if df is None or df.empty:
        return "EMPTY"

    columns = [
        c for c in ALL_COLUMNS
        if c in df.columns
    ]

    stable = (
        df[columns]
        .copy()
        .sort_values(
            ["date", "time", "id"],
            na_position="last",
        )
        .fillna("")
        .astype(str)
    )

    raw = stable.to_csv(
        index=False,
        lineterminator="\n",
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def strategy_engine_signature(
    df,
    min_sample,
    max_filters,
    top_n,
    validation_ratio,
):
    raw = "|".join([
        strategy_dataset_signature(df),
        str(int(min_sample)),
        str(int(max_filters)),
        str(int(top_n)),
        f"{float(validation_ratio):.6f}",
    ])

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def apply_generated_strategy_name(df, strategy_name):
    """
    Applica direttamente al database una strategia scritta dal motore,
    per esempio:

        MTR=VE + QI/QA=VE + Quota 1.20-1.70

    Serve a fare in modo che Strategia ufficiale, Classifica e Laboratorio
    lavorino sullo stesso identico insieme di partite.
    """
    if df is None or df.empty or not strategy_name:
        return pd.DataFrame(
            columns=df.columns if df is not None else []
        )

    filtered = add_strategy_derived_columns(df.copy())

    categorical_prefixes = {
        "ALLB colore=": "allibramento_color",
        "ALLB indicatore=": "allb",
        "MTR=": "mtr",
        "SCL=": "scl",
        "CAL=": "cal",
        "STATUS=": "status",
        "C.AFF.=": "c_aff",
        "FLBK=": "flbk",
        "C.FB.=": "c_fb",
        "QRA/QA=": "qra_qa",
        "QI/QA=": "qi_qa",
        "Campionato=": "league",
    }

    conditions = [
        part.strip()
        for part in str(strategy_name).split(" + ")
        if part.strip()
    ]

    for condition in conditions:
        handled = False

        for prefix, column in categorical_prefixes.items():
            if condition.startswith(prefix):
                value = condition[len(prefix):].strip()
                series = (
                    filtered[column]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )
                filtered = filtered[
                    series == value
                ]
                handled = True
                break

        if handled:
            continue

        quota_match = re.fullmatch(
            r"Quota\s+([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)",
            condition,
        )
        if quota_match:
            low = float(quota_match.group(1))
            high = float(quota_match.group(2))
            odds = pd.to_numeric(
                filtered["current_odds"],
                errors="coerce",
            )
            filtered = filtered[
                (odds >= low)
                & (odds <= high)
            ]
            continue

        caff_count = pd.to_numeric(
            filtered["c_aff_count"],
            errors="coerce",
        )

        if condition == "C.AFF. comparazioni ≤200":
            filtered = filtered[caff_count <= 200]
            continue

        if condition == "C.AFF. comparazioni 201-500":
            filtered = filtered[
                (caff_count >= 201)
                & (caff_count <= 500)
            ]
            continue

        if condition == "C.AFF. comparazioni ≥501":
            filtered = filtered[caff_count >= 501]
            continue

        movement = pd.to_numeric(
            filtered["_odds_move_pct"],
            errors="coerce",
        )

        if condition == "Quota scesa >=5%":
            filtered = filtered[movement <= -5.0]
            continue

        if condition == "Quota scesa 2-5%":
            filtered = filtered[
                (movement >= -5.0)
                & (movement <= -2.0)
            ]
            continue

        if condition == "Quota salita 2-5%":
            filtered = filtered[
                (movement >= 2.0)
                & (movement <= 5.0)
            ]
            continue

        if condition == "Quota salita >=5%":
            filtered = filtered[movement >= 5.0]
            continue

        value = pd.to_numeric(
            filtered["_value_vs_fair_pct"],
            errors="coerce",
        )

        if condition == "Value >=2%":
            filtered = filtered[value >= 2.0]
            continue

        if condition == "Value >=5%":
            filtered = filtered[value >= 5.0]
            continue

        if condition == "Value >=10%":
            filtered = filtered[value >= 10.0]
            continue

        if condition == "Value negativo":
            filtered = filtered[value < 0]
            continue

        allb_delta = pd.to_numeric(
            filtered["_allb_delta"],
            errors="coerce",
        )

        if condition == "Allibramento > media":
            filtered = filtered[allb_delta > 0]
            continue

        if condition == "Allibramento < media":
            filtered = filtered[allb_delta < 0]
            continue

    return filtered.copy()


def max_losing_streak(df):
    if df.empty or "outcome" not in df.columns:
        return 0
    streak = 0
    worst = 0
    ordered = df.sort_values(["date", "time", "id"])
    for value in ordered["outcome"].astype(str):
        if value == "P":
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def strategy_statistics(df):
    s = summary(df)
    return {
        **s,
        "max_losing_streak": max_losing_streak(df),
    }


STRATEGY_ODDS_RANGES = [
    (1.20, 1.39), (1.40, 1.49), (1.50, 1.59), (1.60, 1.69),
    (1.70, 1.79), (1.80, 1.99), (2.00, 2.24), (2.25, 2.49),
    (2.50, 2.99), (3.00, 99.00),

    # Range più ampi e sovrapposti
    (1.20, 1.50), (1.20, 1.70), (1.20, 2.00),
    (1.30, 1.60), (1.30, 1.80), (1.30, 2.00),
    (1.40, 1.60), (1.40, 1.80), (1.40, 2.00),
    (1.50, 1.80), (1.50, 2.00), (1.50, 2.20),
    (1.60, 2.00), (1.60, 2.20), (1.70, 2.20),
    (1.80, 2.50),
]


def odds_range_analysis(df):
    rows = []
    odds = pd.to_numeric(df["current_odds"], errors="coerce")

    for low, high in STRATEGY_ODDS_RANGES:
        subset = df[(odds >= low) & (odds <= high)].copy()
        if subset.empty:
            continue

        s = strategy_statistics(subset)
        label = f"{low:.2f}+" if high >= 99 else f"{low:.2f}–{high:.2f}"

        rows.append({
            "Range quota": label,
            "Partite": s["closed"],
            "Vinte": s["wins"],
            "Perse": s["losses"],
            "Win rate %": round(s["win_rate"], 2),
            "Quota media": round(s["avg_odds"], 2),
            "Puntato €": round(s["staked"], 2),
            "Profitto €": round(s["profit"], 2),
            "ROI %": round(s["roi"], 2),
            "Profitto / 100€": round(
                (s["profit"] / s["staked"] * 100) if s["staked"] else 0,
                2,
            ),
            "Max perdite consecutive": s["max_losing_streak"],
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["Profitto €", "ROI %", "Partite"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def add_strategy_derived_columns(df):
    out = df.copy()
    current = pd.to_numeric(out["current_odds"], errors="coerce")
    opening = pd.to_numeric(out["opening_odds"], errors="coerce")
    fair = pd.to_numeric(out["fair_odds"], errors="coerce")
    allb_value = pd.to_numeric(out["allibramento_value"], errors="coerce")
    allb_avg = pd.to_numeric(out["allibramento_avg"], errors="coerce")

    out["_odds_move_pct"] = ((current / opening) - 1.0) * 100.0
    out.loc[(opening <= 0) | opening.isna() | current.isna(), "_odds_move_pct"] = pd.NA

    out["_value_vs_fair_pct"] = ((current / fair) - 1.0) * 100.0
    out.loc[(fair <= 0) | fair.isna() | current.isna(), "_value_vs_fair_pct"] = pd.NA

    out["_allb_delta"] = allb_value - allb_avg
    out.loc[allb_value.isna() | allb_avg.isna(), "_allb_delta"] = pd.NA
    return out



def official_operational_status(df):
    """
    Stato operativo della strategia ufficiale.
    Non cambia la strategia ufficiale: misura solo la salute recente.
    """
    closed = df[df["outcome"].isin(["V", "P"])].copy()
    if closed.empty:
        return {
            "status": "⚪ DATI INSUFFICIENTI",
            "roi20": 0.0,
            "roi50": 0.0,
            "drawdown_eur": 0.0,
            "drawdown_pct": 0.0,
        }

    closed = closed.sort_values(["date", "time", "id"]).copy()

    last20 = closed.tail(20)
    last50 = closed.tail(50)
    roi20 = float(summary(last20)["roi"]) if not last20.empty else 0.0
    roi50 = float(summary(last50)["roi"]) if not last50.empty else 0.0

    profits = pd.to_numeric(closed["profit"], errors="coerce").fillna(0.0)
    cumulative = profits.cumsum()
    peak = float(cumulative.cummax().max()) if not cumulative.empty else 0.0
    current = float(cumulative.iloc[-1]) if not cumulative.empty else 0.0
    drawdown_eur = max(0.0, peak - current)
    drawdown_pct = (drawdown_eur / peak * 100.0) if peak > 0 else 0.0

    # ROSSO solo con deterioramento contemporaneo su breve, medio periodo
    # e drawdown molto importante: evita stop per poche sconfitte casuali.
    if (
        len(closed) >= 50
        and roi20 <= -10.0
        and roi50 <= -5.0
        and drawdown_pct >= 50.0
    ):
        status = "🔴 SOSPENDI"
    elif (
        roi20 < 0.0
        or roi50 < 0.0
        or drawdown_pct >= 35.0
    ):
        status = "🟡 SEGUI CON CAUTELA"
    else:
        status = "🟢 SEGUI"

    return {
        "status": status,
        "roi20": roi20,
        "roi50": roi50,
        "drawdown_eur": drawdown_eur,
        "drawdown_pct": drawdown_pct,
    }

def stability_statistics(df):
    if df.empty:
        return {"positive_blocks":0,"total_blocks":0,"stability_pct":0.0,"worst_block_roi":0.0}

    ordered = df.sort_values(["date", "time", "id"]).copy()
    n = len(ordered)
    if n < 6:
        return {"positive_blocks":0,"total_blocks":0,"stability_pct":0.0,"worst_block_roi":0.0}

    cuts = [0, n // 3, (2 * n) // 3, n]
    rois = []
    for i in range(3):
        block = ordered.iloc[cuts[i]:cuts[i+1]]
        if not block.empty:
            rois.append(summary(block)["roi"])

    if not rois:
        return {"positive_blocks":0,"total_blocks":0,"stability_pct":0.0,"worst_block_roi":0.0}

    positive = sum(1 for roi in rois if roi > 0)
    return {
        "positive_blocks": positive,
        "total_blocks": len(rois),
        "stability_pct": positive / len(rois) * 100.0,
        "worst_block_roi": min(rois),
    }


@st.cache_data(show_spinner=False)
def automatic_strategy_search(
    df,
    min_sample=10,
    max_filters=3,
    top_n=100,
    validation_ratio=0.30,
):
    closed = df[df["outcome"].isin(["V", "P"])].copy()
    if closed.empty:
        return pd.DataFrame(), {}

    closed = add_strategy_derived_columns(closed)
    closed = closed.sort_values(["date", "time", "id"]).copy()
    closed = closed.reset_index(drop=False).rename(columns={"index": "_source_index"})

    baseline_stats = strategy_statistics(closed)
    baseline_roi = baseline_stats["roi"]
    dimensions = {}
    n_rows = len(closed)

    categorical = {
        "ALLB colore": "allibramento_color",
        "ALLB indicatore": "allb",
        "MTR": "mtr",
        "SCL": "scl",
        "CAL": "cal",
        "STATUS": "status",
        "C.AFF.": "c_aff",
        "FLBK": "flbk",
        "C.FB.": "c_fb",
        "QRA/QA": "qra_qa",
        "QI/QA": "qi_qa",
        "Campionato": "league",
    }

    string_cache = {}
    numeric_cache = {}

    for label, column in categorical.items():
        if column not in closed.columns:
            continue

        arr = (
            closed[column]
            .fillna("")
            .astype(str)
            .str.strip()
            .to_numpy()
        )
        string_cache[column] = arr

        for value in pd.unique(arr):
            if value and value.lower() not in {"nan", "none"}:
                mask = arr == value
                if mask.any():
                    dimensions[f"{label}={value}"] = {
                        "family": column,
                        "mask": mask,
                    }

    def num_array(column):
        if column not in numeric_cache:
            numeric_cache[column] = pd.to_numeric(
                closed[column], errors="coerce"
            ).to_numpy()
        return numeric_cache[column]

    # Numero Comparazioni Affini
    caff_count = num_array("c_aff_count")

    mask = (~np.isnan(caff_count)) & (caff_count <= 200)
    if mask.any():
        dimensions["C.AFF. comparazioni ≤200"] = {
            "family": "c_aff_count",
            "mask": mask,
        }

    mask = (
        (~np.isnan(caff_count))
        & (caff_count >= 201)
        & (caff_count <= 500)
    )
    if mask.any():
        dimensions["C.AFF. comparazioni 201-500"] = {
            "family": "c_aff_count",
            "mask": mask,
        }

    mask = (~np.isnan(caff_count)) & (caff_count >= 501)
    if mask.any():
        dimensions["C.AFF. comparazioni ≥501"] = {
            "family": "c_aff_count",
            "mask": mask,
        }

    odds = num_array("current_odds")
    for low, high in STRATEGY_ODDS_RANGES:
        mask = (odds >= low) & (odds <= high)
        if mask.any():
            dimensions[f"Quota {low:.2f}-{high:.2f}"] = {
                "family": "current_odds",
                "mask": mask,
            }

    move = num_array("_odds_move_pct")
    movement_filters = [
        ("Quota scesa >=5%", None, -5.0),
        ("Quota scesa 2-5%", -5.0, -2.0),
        ("Quota salita 2-5%", 2.0, 5.0),
        ("Quota salita >=5%", 5.0, None),
    ]

    for label, low, high in movement_filters:
        mask = np.ones(n_rows, dtype=bool)
        mask &= ~np.isnan(move)
        if low is not None:
            mask &= move >= low
        if high is not None:
            mask &= move <= high

        if mask.any():
            dimensions[label] = {
                "family": "_odds_move_pct",
                "mask": mask,
            }

    value_series = num_array("_value_vs_fair_pct")
    for label, threshold in [
        ("Value >=2%", 2.0),
        ("Value >=5%", 5.0),
        ("Value >=10%", 10.0),
    ]:
        mask = (~np.isnan(value_series)) & (value_series >= threshold)
        if mask.any():
            dimensions[label] = {
                "family": "_value_vs_fair_pct",
                "mask": mask,
            }

    mask = (~np.isnan(value_series)) & (value_series < 0)
    if mask.any():
        dimensions["Value negativo"] = {
            "family": "_value_vs_fair_pct",
            "mask": mask,
        }

    items = list(dimensions.items())
    results = []
    selections = {}

    for size in range(1, max_filters + 1):
        for combo in combinations(items, size):
            labels = [label for label, _ in combo]
            specs = [spec for _, spec in combo]

            families = [spec["family"] for spec in specs]
            if len(families) != len(set(families)):
                continue

            combo_mask = specs[0]["mask"].copy()
            for spec in specs[1:]:
                combo_mask &= spec["mask"]

            if int(combo_mask.sum()) < min_sample:
                continue

            subset = closed.loc[combo_mask].copy()
            n = len(subset)

            validation_n = max(1, int(math.ceil(n * validation_ratio)))
            train_n = n - validation_n
            use_validation = train_n >= 2 and validation_n >= 2

            if use_validation:
                train_df = subset.iloc[:train_n]
                test_df = subset.iloc[train_n:]
                train_stats = strategy_statistics(train_df)
                test_stats = strategy_statistics(test_df)
            else:
                train_stats = strategy_statistics(subset)
                test_stats = {"closed": 0, "roi": 0}

            total_stats = strategy_statistics(subset)
            stability = stability_statistics(subset)
            sample_reliability = min(1.0, n / 100.0)

            if use_validation:
                if train_stats["roi"] > 0 and test_stats["roi"] > 0:
                    validation_factor = 1.0
                elif train_stats["roi"] > 0 and test_stats["roi"] >= 0:
                    validation_factor = 0.80
                elif test_stats["roi"] < 0:
                    validation_factor = 0.35
                else:
                    validation_factor = 0.55
            else:
                validation_factor = 0.50

            streak_penalty = 1 / (
                1 + 0.12 * total_stats["max_losing_streak"]
            )

            stability_factor = (
                0.55 + 0.45 * (stability["stability_pct"] / 100.0)
                if stability["total_blocks"]
                else 0.60
            )

            roi_delta_vs_base = total_stats["roi"] - baseline_roi

            score = (
                total_stats["roi"] * 0.35
                + total_stats["win_rate"] * 0.10
                + (total_stats["profit"] / 20.0) * 0.30
                + roi_delta_vs_base * 0.25
            )
            score *= (
                sample_reliability
                * validation_factor
                * streak_penalty
                * stability_factor
            )

            if n < 30:
                trust_label = "🔴 Esplorativa"
            elif n < 50:
                trust_label = "🟠 Primi segnali"
            elif n < 100:
                trust_label = "🟡 Interessante"
            elif n < 200:
                trust_label = "🟢 Abbastanza solida"
            else:
                trust_label = "🟢🟢 Molto più solida"

            strategy_id = f"S{len(results)+1:05d}"

            results.append({
                "ID": strategy_id,
                "Strategia": " + ".join(labels),
                "Partite": total_stats["closed"],
                "Attendibilità": trust_label,
                "Vinte": total_stats["wins"],
                "Perse": total_stats["losses"],
                "Win rate %": round(total_stats["win_rate"], 2),
                "Quota media": round(total_stats["avg_odds"], 2),
                "Puntato €": round(total_stats["staked"], 2),
                "Profitto €": round(total_stats["profit"], 2),
                "ROI %": round(total_stats["roi"], 2),
                "ROI base %": round(baseline_roi, 2),
                "Δ ROI vs base": round(roi_delta_vs_base, 2),
                "Profitto / 100€": round(
                    (total_stats["profit"] / total_stats["staked"] * 100)
                    if total_stats["staked"] else 0,
                    2,
                ),
                "Max perdite consecutive": total_stats["max_losing_streak"],
                "Blocchi positivi": (
                    f'{stability["positive_blocks"]}/{stability["total_blocks"]}'
                    if stability["total_blocks"] else "n.d."
                ),
                "Stabilità %": round(stability["stability_pct"], 1),
                "Peggior ROI blocco %": (
                    round(stability["worst_block_roi"], 2)
                    if stability["total_blocks"] else None
                ),
                "ROI ricerca %": round(train_stats["roi"], 2),
                "ROI verifica %": (
                    round(test_stats["roi"], 2) if use_validation else None
                ),
                "Partite verifica": (
                    test_stats["closed"] if use_validation else 0
                ),
                "Validata": (
                    "✅"
                    if use_validation
                    and train_stats["roi"] > 0
                    and test_stats["roi"] > 0
                    else "⚠️"
                ),
                "Affidabilità campione %": round(sample_reliability * 100, 1),
                "Punteggio": round(score, 2),
            })

            selections[strategy_id] = subset["_source_index"].tolist()

    if not results:
        return pd.DataFrame(), {}

    table = pd.DataFrame(results).sort_values(
        ["Punteggio", "Profitto €", "ROI %", "Partite"],
        ascending=[False, False, False, False],
    ).head(top_n).reset_index(drop=True)

    valid_ids = set(table["ID"])
    selections = {
        sid: idx
        for sid, idx in selections.items()
        if sid in valid_ids
    }

    return table, selections


STRATEGY_HISTORY_TABLE = "strategy_history"


def strategy_ranking_signature(ranking):
    if ranking is None or ranking.empty:
        return ""

    cols = ["Strategia", "Partite", "ROI %", "Profitto €", "Punteggio"]
    available = [c for c in cols if c in ranking.columns]
    compact = ranking[available].fillna("").astype(str)

    raw = "\n".join(
        "|".join(row)
        for row in compact.to_numpy().tolist()
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def save_strategy_snapshot(ranking):
    if not use_supabase():
        return {
            "ok": False,
            "saved": False,
            "message": "Storico disponibile solo con Supabase.",
        }

    if ranking is None or ranking.empty:
        return {
            "ok": True,
            "saved": False,
            "message": "Classifica vuota: nessuno snapshot salvato.",
        }

    client = supabase_client()
    signature = strategy_ranking_signature(ranking)

    try:
        # Leggiamo più righe perché nella stessa tabella vengono salvati
        # anche gli stati tecnici __SYSTEM_STATE__. Quelli NON devono
        # essere considerati come ultimo snapshot della classifica.
        previous_rows = (
            client.table(STRATEGY_HISTORY_TABLE)
            .select("signature,strategy,captured_at")
            .order("captured_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        return {
            "ok": False,
            "saved": False,
            "message": f"Tabella storico non disponibile. Dettaglio: {exc}",
        }

    previous_signature = ""
    for previous_row in previous_rows:
        strategy_value = str(previous_row.get("strategy") or "")
        if strategy_value.startswith(SYSTEM_STATE_PREFIX):
            continue
        previous_signature = str(previous_row.get("signature") or "")
        break

    if previous_signature == signature:
        return {
            "ok": True,
            "saved": False,
            "message": "Classifica invariata: snapshot non duplicato.",
        }

    captured_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")

    rows = []
    for position, (_, row) in enumerate(ranking.iterrows(), start=1):
        rows.append({
            "snapshot_id": snapshot_id,
            "captured_at": captured_at,
            "signature": signature,
            "rank_position": position,
            "strategy": str(row.get("Strategia", "")),
            "matches_count": int(row.get("Partite", 0) or 0),
            "roi": float(row.get("ROI %", 0) or 0),
            "profit": float(row.get("Profitto €", 0) or 0),
            "score": float(row.get("Punteggio", 0) or 0),
        })

    try:
        client.table(STRATEGY_HISTORY_TABLE).insert(rows).execute()
    except Exception as exc:
        return {
            "ok": False,
            "saved": False,
            "message": f"Errore salvataggio storico: {exc}",
        }

    return {
        "ok": True,
        "saved": True,
        "message": f"Nuovo snapshot salvato: {len(rows)} strategie.",
    }



def ensure_current_strategy_snapshot(ranking):
    """
    Garantisce che la classifica che l'utente sta vedendo adesso
    sia anche l'ultimo vero snapshot nello storico.

    È intenzionalmente idempotente: se la classifica non è cambiata,
    save_strategy_snapshot non crea duplicati.
    """
    if ranking is None or ranking.empty:
        return {
            "ok": True,
            "saved": False,
            "message": "Classifica vuota.",
        }

    return save_strategy_snapshot(ranking)




def fetch_all_strategy_history_rows(page_size=1000):
    """
    Legge TUTTO lo storico strategy_history da Supabase con paginazione.

    Supabase/PostgREST può limitare una singola select a circa 1000 righe.
    Senza paginazione, con 50 strategie per snapshot, dopo circa 20 snapshot
    l'app smette di vedere i nuovi dati. Questa funzione evita il problema
    senza cancellare o modificare lo storico esistente.
    """
    if not use_supabase():
        return []

    client = supabase_client()
    all_rows = []
    offset = 0

    while True:
        try:
            response = (
                client.table(STRATEGY_HISTORY_TABLE)
                .select(
                    "snapshot_id,captured_at,rank_position,"
                    "strategy,matches_count,roi,profit,score,signature"
                )
                .order("captured_at")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = response.data or []
        except Exception:
            return all_rows

        all_rows.extend(batch)

        if len(batch) < page_size:
            break

        offset += page_size

        # Protezione solo contro loop anomali.
        if offset > 200000:
            break

    return all_rows



def strategy_history_summary(current_strategies=None, current_ranking=None):
    """
    Riassunto storico robusto.

    Legge tutto lo storico Supabase e, se la classifica corrente non è ancora
    rappresentata dall'ultimo snapshot persistito, aggiunge in memoria uno
    SNAPSHOT VIRTUALE corrente.

    Questo NON cancella e NON modifica lo storico esistente.
    Serve solo a garantire che:
    - Ultima posizione = posizione realmente visibile adesso;
    - Top 5 % e Top 10 % includano la rilevazione corrente;
    - la Classifica Elite non lavori su uno storico rimasto indietro.
    """
    if not use_supabase():
        return pd.DataFrame(), 0

    rows = fetch_all_strategy_history_rows()
    if rows is None:
        rows = []

    # Escludiamo gli stati tecnici.
    real_rows = [
        r for r in rows
        if not str(r.get("strategy") or "").startswith(SYSTEM_STATE_PREFIX)
    ]

    hist = pd.DataFrame(real_rows)

    if hist.empty:
        hist = pd.DataFrame(columns=[
            "snapshot_id","captured_at","rank_position","strategy",
            "matches_count","roi","profit","score","signature"
        ])

    # -----------------------------------------------------------
    # SNAPSHOT CORRENTE VIRTUALE
    # -----------------------------------------------------------
    virtual_added = False

    if (
        current_ranking is not None
        and isinstance(current_ranking, pd.DataFrame)
        and not current_ranking.empty
    ):
        current_signature = strategy_ranking_signature(current_ranking)

        saved_signatures = set()
        if "signature" in hist.columns and not hist.empty:
            saved_signatures = set(
                hist["signature"]
                .fillna("")
                .astype(str)
                .tolist()
            )

        # Se la classifica corrente non è già realmente salvata,
        # la aggiungiamo SOLO in memoria per i calcoli.
        if current_signature and current_signature not in saved_signatures:
            now = pd.Timestamp.now(tz="UTC")
            virtual_snapshot_id = "__CURRENT_VIRTUAL__"

            virtual_rows = []
            for position, (_, row) in enumerate(
                current_ranking.iterrows(),
                start=1,
            ):
                virtual_rows.append({
                    "snapshot_id": virtual_snapshot_id,
                    "captured_at": now,
                    "rank_position": position,
                    "strategy": str(row.get("Strategia", "")),
                    "matches_count": int(row.get("Partite", 0) or 0),
                    "roi": float(row.get("ROI %", 0) or 0),
                    "profit": float(row.get("Profitto €", 0) or 0),
                    "score": float(row.get("Punteggio", 0) or 0),
                    "signature": current_signature,
                })

            if virtual_rows:
                hist = pd.concat(
                    [hist, pd.DataFrame(virtual_rows)],
                    ignore_index=True,
                )
                virtual_added = True

    if hist.empty:
        return pd.DataFrame(), 0

    hist["captured_at"] = pd.to_datetime(
        hist["captured_at"],
        errors="coerce",
        utc=True,
    )

    for col in [
        "rank_position",
        "matches_count",
        "roi",
        "profit",
        "score",
    ]:
        if col in hist.columns:
            hist[col] = pd.to_numeric(
                hist[col],
                errors="coerce",
            )

    snapshot_meta = (
        hist[["snapshot_id", "captured_at"]]
        .drop_duplicates("snapshot_id")
        .sort_values("captured_at")
        .reset_index(drop=True)
    )

    total_snapshots = len(snapshot_meta)

    if current_strategies:
        wanted = set(str(x) for x in current_strategies)
        strategies = [
            s
            for s in hist["strategy"].dropna().astype(str).unique()
            if s in wanted
        ]
    else:
        strategies = list(
            hist["strategy"].dropna().astype(str).unique()
        )

    output = []

    for strategy in strategies:
        sdf = hist[
            hist["strategy"].astype(str) == strategy
        ].copy()

        if sdf.empty:
            continue

        first_seen = sdf["captured_at"].min()

        eligible = snapshot_meta[
            snapshot_meta["captured_at"] >= first_seen
        ]

        denominator = max(1, len(eligible))

        appearances = int(
            sdf["snapshot_id"].nunique()
        )

        top5 = int(
            sdf.loc[
                sdf["rank_position"] <= 5,
                "snapshot_id",
            ].nunique()
        )

        top10 = int(
            sdf.loc[
                sdf["rank_position"] <= 10,
                "snapshot_id",
            ].nunique()
        )

        latest = (
            sdf.sort_values(
                ["captured_at", "snapshot_id"]
            )
            .iloc[-1]
        )

        output.append({
            "Strategia": strategy,
            "Rilevazioni": appearances,
            "Top 5 %": round(
                top5 / denominator * 100,
                1,
            ),
            "Top 10 %": round(
                top10 / denominator * 100,
                1,
            ),
            "Posizione media": round(
                float(sdf["rank_position"].mean()),
                2,
            ),
            "Ultima posizione": int(
                latest["rank_position"]
            ),
            "Ultimo campione": int(
                latest["matches_count"]
                if pd.notna(latest["matches_count"])
                else 0
            ),
            "Ultimo ROI %": round(
                float(latest["roi"])
                if pd.notna(latest["roi"])
                else 0.0,
                2,
            ),
            "Ultimo punteggio": round(
                float(latest["score"])
                if pd.notna(latest["score"])
                else 0.0,
                2,
            ),
            "_virtual_current": virtual_added,
        })

    if not output:
        return pd.DataFrame(), total_snapshots

    result = pd.DataFrame(output).sort_values(
        [
            "Top 5 %",
            "Top 10 %",
            "Posizione media",
            "Rilevazioni",
        ],
        ascending=[
            False,
            False,
            True,
            False,
        ],
    ).reset_index(drop=True)

    return result, total_snapshots



def definitive_strategy_ranking(ranking, history_table, snapshot_count, selections=None):
    """
    Classifica definitiva con rimozione automatica delle strategie equivalenti.

    Due strategie sono considerate duplicate quando selezionano ESATTAMENTE
    le stesse partite. In quel caso viene tenuta una sola riga:
    - prima si preferisce la strategia con meno filtri, quindi più semplice;
    - a parità di complessità si tiene quella con punteggio definitivo migliore.
    """
    if ranking is None or ranking.empty:
        return pd.DataFrame()

    selections = selections or {}
    current = ranking.copy()

    def norm(series, higher_is_better=True):
        s = pd.to_numeric(series, errors="coerce")
        if s.notna().sum() == 0:
            return pd.Series(50.0, index=series.index)

        lo = float(s.min())
        hi = float(s.max())

        if math.isclose(lo, hi):
            out = pd.Series(50.0, index=series.index)
        else:
            out = (s - lo) / (hi - lo) * 100.0

        if not higher_is_better:
            out = 100.0 - out

        return out.fillna(0.0)

    current["_score_now"] = norm(current["Punteggio"])
    current["_sample"] = pd.to_numeric(
        current["Affidabilità campione %"],
        errors="coerce",
    ).fillna(0).clip(0, 100)

    current["_stability_now"] = pd.to_numeric(
        current["Stabilità %"],
        errors="coerce",
    ).fillna(0).clip(0, 100)

    current["_validation"] = current["Validata"].astype(str).map(
        lambda x: 100.0 if "✅" in x else 35.0
    )

    if history_table is None or history_table.empty:
        current["Rilevazioni"] = 0
        current["Top 5 %"] = 0.0
        current["Top 10 %"] = 0.0
        current["Posizione media"] = np.nan
        current["_history"] = 0.0
    else:
        hist = history_table[
            [
                "Strategia",
                "Rilevazioni",
                "Top 5 %",
                "Top 10 %",
                "Posizione media",
            ]
        ].copy()

        current = current.merge(
            hist,
            on="Strategia",
            how="left",
        )

        current["Rilevazioni"] = pd.to_numeric(
            current["Rilevazioni"],
            errors="coerce",
        ).fillna(0).astype(int)

        current["Top 5 %"] = pd.to_numeric(
            current["Top 5 %"],
            errors="coerce",
        ).fillna(0.0)

        current["Top 10 %"] = pd.to_numeric(
            current["Top 10 %"],
            errors="coerce",
        ).fillna(0.0)

        pos_component = norm(
            current["Posizione media"],
            higher_is_better=False,
        )

        obs_component = (
            current["Rilevazioni"].clip(upper=10)
            / 10.0
            * 100.0
        )

        current["_history"] = (
            current["Top 5 %"] * 0.40
            + current["Top 10 %"] * 0.20
            + pos_component * 0.20
            + obs_component * 0.20
        )

    maturity = min(
        1.0,
        max(0, snapshot_count) / 10.0,
    )
    history_weight = 0.35 * maturity
    current_weight = 1.0 - history_weight

    current["_current_quality"] = (
        current["_score_now"] * 0.45
        + current["_sample"] * 0.20
        + current["_stability_now"] * 0.20
        + current["_validation"] * 0.15
    )

    current["Punteggio definitivo"] = (
        current["_current_quality"] * current_weight
        + current["_history"] * history_weight
    ).round(2)

    def state(row):
        obs = int(row.get("Rilevazioni", 0) or 0)
        sample = int(row.get("Partite", 0) or 0)
        validated = "✅" in str(row.get("Validata", ""))

        if snapshot_count < 3 or obs < 3 or sample < 30:
            return "🔴 Provvisoria"

        if obs < 5 or sample < 50:
            return "🟠 In consolidamento"

        if obs < 10 or sample < 100 or not validated:
            return "🟡 Stabile"

        return "🟢 Molto stabile"

    current["Stato"] = current.apply(
        state,
        axis=1,
    )

    # ---------------------------------------------------------
    # RIMOZIONE STRATEGIE EQUIVALENTI
    # ---------------------------------------------------------
    # La firma è basata sugli indici reali delle partite selezionate.
    # Se due strategie hanno la stessa firma, stanno scegliendo
    # esattamente le stesse partite e quindi una delle due è ridondante.
    def selection_signature(strategy_id):
        selected = selections.get(
            str(strategy_id),
            selections.get(strategy_id, []),
        )

        if selected is None:
            selected = []

        normalized = sorted(
            {str(value) for value in selected}
        )

        if not normalized:
            # Se per qualche motivo non abbiamo gli indici,
            # NON uniamo la strategia ad altre.
            return f"UNIQUE::{strategy_id}"

        raw = "|".join(normalized)
        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    current["_selection_signature"] = current["ID"].map(
        selection_signature
    )

    # Numero di filtri della strategia.
    # Esempio:
    # C.AFF.=VE -> 1
    # C.AFF.=VE + Quota 1.20-1.70 -> 2
    current["_filter_count"] = (
        current["Strategia"]
        .fillna("")
        .astype(str)
        .map(
            lambda value:
            0
            if not value.strip()
            else len(
                [
                    part
                    for part in value.split(" + ")
                    if part.strip()
                ]
            )
        )
    )

    current["_strategy_length"] = (
        current["Strategia"]
        .fillna("")
        .astype(str)
        .str.len()
    )

    group_sizes = (
        current.groupby(
            "_selection_signature"
        )["_selection_signature"]
        .transform("size")
    )

    current["Duplicati rimossi"] = (
        group_sizes - 1
    ).astype(int)

    # Dentro ogni gruppo equivalente teniamo prima la regola più semplice.
    # A parità di numero di filtri scegliamo quella col punteggio migliore.
    current = current.sort_values(
        [
            "_selection_signature",
            "_filter_count",
            "Punteggio definitivo",
            "Punteggio",
            "_strategy_length",
        ],
        ascending=[
            True,
            True,
            False,
            False,
            True,
        ],
    )

    current = current.drop_duplicates(
        subset=["_selection_signature"],
        keep="first",
    ).copy()

    # Solo dopo aver tolto i duplicati rifacciamo la classifica finale.
    current = current.sort_values(
        [
            "Punteggio definitivo",
            "Punteggio",
            "Partite",
        ],
        ascending=[
            False,
            False,
            False,
        ],
    ).reset_index(drop=True)

    current.insert(
        0,
        "Posizione",
        range(1, len(current) + 1),
    )

    keep = [
        "Posizione",
        "Strategia",
        "Stato",
        "Punteggio definitivo",
        "Partite",
        "Vinte",
        "Perse",
        "Win rate %",
        "Quota media",
        "Profitto €",
        "ROI %",
        "Validata",
        "Stabilità %",
        "Rilevazioni",
        "Top 5 %",
        "Top 10 %",
        "Posizione media",
        "Duplicati rimossi",
    ]

    return current[
        [c for c in keep if c in current.columns]
    ]



SYSTEM_STATE_PREFIX = "__SYSTEM_STATE__"
CHALLENGER_REQUIRED_STREAK = 5
CHALLENGER_MIN_SCORE_ADVANTAGE = 3.0


def latest_real_strategy_snapshot_id():
    if not use_supabase():
        return ""
    try:
        rows = (
            supabase_client()
            .table(STRATEGY_HISTORY_TABLE)
            .select("snapshot_id,captured_at,strategy")
            .order("captured_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
    except Exception:
        return ""
    for row in rows:
        strategy = str(row.get("strategy") or "")
        if not strategy.startswith(SYSTEM_STATE_PREFIX):
            return str(row.get("snapshot_id") or "")
    return ""


def load_strategy_follow_state():
    if not use_supabase():
        return None
    try:
        rows = (
            supabase_client()
            .table(STRATEGY_HISTORY_TABLE)
            .select("strategy,captured_at")
            .order("captured_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
    except Exception:
        return None
    for row in rows:
        raw = str(row.get("strategy") or "")
        if raw.startswith(SYSTEM_STATE_PREFIX):
            try:
                state = json.loads(raw[len(SYSTEM_STATE_PREFIX):])
                if isinstance(state, dict):
                    return state
            except Exception:
                pass
    return None


def save_strategy_follow_state(state):
    if not use_supabase() or not isinstance(state, dict):
        return False
    payload = SYSTEM_STATE_PREFIX + json.dumps(
        state, ensure_ascii=False, separators=(",", ":")
    )
    now = datetime.now(timezone.utc)
    row = {
        "snapshot_id": now.strftime("SYSTEM_%Y%m%dT%H%M%S%f"),
        "captured_at": now.isoformat(),
        "signature": "SYSTEM_STATE",
        "rank_position": 0,
        "strategy": payload,
        "matches_count": int(state.get("official_matches", 0) or 0),
        "roi": float(state.get("official_roi", 0) or 0),
        "profit": float(state.get("official_profit", 0) or 0),
        "score": float(state.get("official_score", 0) or 0),
    }
    try:
        supabase_client().table(STRATEGY_HISTORY_TABLE).insert(row).execute()
        return True
    except Exception:
        return False


def _strategy_row_by_name(definitive, strategy_name):
    if definitive is None or definitive.empty or not strategy_name:
        return None
    rows = definitive[
        definitive["Strategia"].astype(str) == str(strategy_name)
    ]
    return None if rows.empty else rows.iloc[0]


def _eligible_initial_official(row):
    if row is None:
        return False
    return (
        int(row.get("Partite", 0) or 0) >= 100
        and int(row.get("Rilevazioni", 0) or 0) >= 10
        and "✅" in str(row.get("Validata", ""))
        and float(row.get("Stabilità %", 0) or 0) >= 66.7
    )


def _eligible_challenger(row):
    if row is None:
        return False
    return (
        int(row.get("Partite", 0) or 0) >= 80
        and int(row.get("Rilevazioni", 0) or 0) >= 5
        and "✅" in str(row.get("Validata", ""))
        and float(row.get("Stabilità %", 0) or 0) >= 66.7
    )


def update_strategy_follow_state(definitive):
    if definitive is None or definitive.empty:
        return None, "Nessuna strategia disponibile."

    latest_snapshot = latest_real_strategy_snapshot_id()
    state = load_strategy_follow_state()

    if not state:
        eligible = definitive[
            definitive.apply(_eligible_initial_official, axis=1)
        ]
        first = eligible.iloc[0] if not eligible.empty else definitive.iloc[0]
        state = {
            "official": str(first["Strategia"]),
            "official_score": float(first.get("Punteggio definitivo", 0) or 0),
            "official_matches": int(first.get("Partite", 0) or 0),
            "official_roi": float(first.get("ROI %", 0) or 0),
            "official_profit": float(first.get("Profitto €", 0) or 0),
            "official_status": str(first.get("Stato", "")),
            "official_consolidated": _eligible_initial_official(first),
            "challenger": "",
            "challenger_streak": 0,
            "last_processed_snapshot": latest_snapshot,
            "switch_count": 0,
        }
        save_strategy_follow_state(state)
        msg = (
            "Strategia ufficiale iniziale consolidata."
            if state["official_consolidated"]
            else "Strategia iniziale impostata, ma ancora in consolidamento."
        )
        return state, msg

    official_name = str(state.get("official") or "")
    official_row = _strategy_row_by_name(definitive, official_name)

    if latest_snapshot and latest_snapshot == str(
        state.get("last_processed_snapshot") or ""
    ):
        return state, "Nessun nuovo snapshot: stato invariato."

    if official_row is not None:
        state["official_score"] = float(
            official_row.get("Punteggio definitivo", 0) or 0
        )
        state["official_matches"] = int(official_row.get("Partite", 0) or 0)
        state["official_roi"] = float(official_row.get("ROI %", 0) or 0)
        state["official_profit"] = float(official_row.get("Profitto €", 0) or 0)
        state["official_status"] = str(official_row.get("Stato", ""))
        if _eligible_initial_official(official_row):
            state["official_consolidated"] = True

    official_score = float(state.get("official_score", 0) or 0)

    challengers = definitive[
        definitive["Strategia"].astype(str) != official_name
    ].copy()
    if not challengers.empty:
        challengers = challengers[
            challengers.apply(_eligible_challenger, axis=1)
        ]

    if not challengers.empty:
        challengers = challengers[
            pd.to_numeric(
                challengers["Punteggio definitivo"], errors="coerce"
            ).fillna(0)
            >= official_score + CHALLENGER_MIN_SCORE_ADVANTAGE
        ]

    if challengers.empty:
        state["challenger"] = ""
        state["challenger_streak"] = 0
        state["last_processed_snapshot"] = latest_snapshot
        save_strategy_follow_state(state)
        return (
            state,
            "Nessuno sfidante supera abbastanza la strategia ufficiale: "
            "continua con quella attuale.",
        )

    best = challengers.sort_values(
        ["Punteggio definitivo", "Rilevazioni", "Partite"],
        ascending=[False, False, False],
    ).iloc[0]

    challenger_name = str(best["Strategia"])

    if challenger_name == str(state.get("challenger") or ""):
        state["challenger_streak"] = int(
            state.get("challenger_streak", 0) or 0
        ) + 1
    else:
        state["challenger"] = challenger_name
        state["challenger_streak"] = 1

    state["last_processed_snapshot"] = latest_snapshot

    if int(state["challenger_streak"]) >= CHALLENGER_REQUIRED_STREAK:
        old_official = official_name
        state["official"] = challenger_name
        state["official_score"] = float(
            best.get("Punteggio definitivo", 0) or 0
        )
        state["official_matches"] = int(best.get("Partite", 0) or 0)
        state["official_roi"] = float(best.get("ROI %", 0) or 0)
        state["official_profit"] = float(best.get("Profitto €", 0) or 0)
        state["official_status"] = str(best.get("Stato", ""))
        state["official_consolidated"] = _eligible_initial_official(best)
        state["challenger"] = ""
        state["challenger_streak"] = 0
        state["switch_count"] = int(state.get("switch_count", 0) or 0) + 1
        save_strategy_follow_state(state)
        return (
            state,
            f"Cambio confermato: {old_official} → {challenger_name}. "
            f"Lo sfidante è rimasto superiore per "
            f"{CHALLENGER_REQUIRED_STREAK} rilevazioni consecutive.",
        )

    save_strategy_follow_state(state)
    return (
        state,
        f"Sfidante in osservazione: {challenger_name} "
        f"({state['challenger_streak']}/"
        f"{CHALLENGER_REQUIRED_STREAK} conferme consecutive).",
    )


def strategy_follow_display_row(definitive, state, closed=None):
    """
    La posizione/punteggio rimane quello della classifica,
    ma Partite, ROI e Profitto vengono sempre ricalcolati
    sul database attuale usando la regola testuale ufficiale.
    """
    if not state:
        return None

    official_name = str(
        state.get("official") or ""
    )

    row = _strategy_row_by_name(
        definitive,
        official_name,
    )

    if row is not None:
        display = row.copy()
    else:
        display = pd.Series({
            "Strategia": official_name,
            "Stato": state.get(
                "official_status",
                "🟡 Monitorata",
            ),
            "Punteggio definitivo": state.get(
                "official_score",
                0,
            ),
            "Partite": state.get(
                "official_matches",
                0,
            ),
            "Profitto €": state.get(
                "official_profit",
                0,
            ),
            "ROI %": state.get(
                "official_roi",
                0,
            ),
        })

    if (
        closed is not None
        and not closed.empty
        and official_name
    ):
        live = apply_generated_strategy_name(
            closed,
            official_name,
        )

        live_stats = strategy_statistics(live)

        display["Partite"] = live_stats["closed"]
        display["Vinte"] = live_stats["wins"]
        display["Perse"] = live_stats["losses"]
        display["Win rate %"] = round(
            live_stats["win_rate"],
            2,
        )
        display["Quota media"] = round(
            live_stats["avg_odds"],
            2,
        )
        display["Profitto €"] = round(
            live_stats["profit"],
            2,
        )
        display["ROI %"] = round(
            live_stats["roi"],
            2,
        )

    return display



ELITE_MIN_MATCHES = 80
ELITE_MIN_OBSERVATIONS = 5
ELITE_RECENT_WINDOW = 50
ELITE_MAX_ROWS = 12

WATCH_MIN_MATCHES = 70
WATCH_MIN_OBSERVATIONS = 5
WATCH_MIN_STABILITY = 66.7
WATCH_MAX_ROWS = 6


def recent_strategy_statistics(df, window=ELITE_RECENT_WINDOW):
    if df is None or df.empty:
        return {
            "recent_matches": 0,
            "recent_profit": 0.0,
            "recent_roi": 0.0,
            "recent_win_rate": 0.0,
            "recent_max_losing_streak": 0,
        }

    ordered = (
        df[df["outcome"].isin(["V", "P"])]
        .sort_values(["date", "time", "id"])
        .tail(window)
        .copy()
    )

    if ordered.empty:
        return {
            "recent_matches": 0,
            "recent_profit": 0.0,
            "recent_roi": 0.0,
            "recent_win_rate": 0.0,
            "recent_max_losing_streak": 0,
        }

    s = strategy_statistics(ordered)

    return {
        "recent_matches": s["closed"],
        "recent_profit": s["profit"],
        "recent_roi": s["roi"],
        "recent_win_rate": s["win_rate"],
        "recent_max_losing_streak": s["max_losing_streak"],
    }


def build_elite_ranking(
    ranking,
    history_table,
    snapshot_count,
    selections,
    closed,
    official_state=None,
):
    """
    Crea la classifica decisionale principale.

    Regole:
    - esclude le strategie provvisorie;
    - richiede campione minimo, storico minimo e validazione positiva;
    - rimuove strategie equivalenti che scelgono le stesse partite;
    - confronta rendimento totale e ultime 50 partite;
    - penalizza perdita di stabilità e serie negative;
    - mantiene sempre visibile la strategia ufficiale, anche se oggi
      non sarebbe entrata nella Top Elite.
    """
    if ranking is None or ranking.empty:
        return pd.DataFrame()

    selections = selections or {}

    # Partiamo dalla classifica definitiva già depurata dai duplicati.
    definitive = definitive_strategy_ranking(
        ranking,
        history_table,
        snapshot_count,
        selections,
    )

    if definitive is None or definitive.empty:
        return pd.DataFrame()

    # Mappa nome strategia -> ID della classifica corrente.
    strategy_to_id = {}
    for _, row in ranking.iterrows():
        strategy_to_id[str(row.get("Strategia", ""))] = str(
            row.get("ID", "")
        )

    # Aggiungiamo metriche mancanti dal ranking originario.
    original_cols = [
        "Strategia",
        "Max perdite consecutive",
        "Peggior ROI blocco %",
        "ROI ricerca %",
        "ROI verifica %",
        "Punteggio",
        "Affidabilità campione %",
    ]
    available = [c for c in original_cols if c in ranking.columns]

    extras = ranking[available].copy()

    elite = definitive.merge(
        extras,
        on="Strategia",
        how="left",
    )

    # Calcolo recente sulle ultime 50 partite DI QUELLA strategia.
    recent_rows = []

    for _, row in elite.iterrows():
        strategy_name = str(row.get("Strategia", ""))
        sid = strategy_to_id.get(strategy_name, "")
        selected_indices = selections.get(sid, [])

        if selected_indices:
            sdf = closed.loc[
                closed.index.intersection(selected_indices)
            ].copy()
        else:
            # Dopo un refresh/deploy la mappa selections può non contenere
            # più l'ID temporaneo della strategia. Non dobbiamo mostrare
            # metriche a zero: ricostruiamo direttamente le partite usando
            # la regola testuale della strategia.
            sdf = apply_generated_strategy_name(
                closed,
                strategy_name,
            )

        rs = recent_strategy_statistics(
            sdf,
            ELITE_RECENT_WINDOW,
        )

        recent_rows.append({
            "Strategia": strategy_name,
            "Partite recenti": rs["recent_matches"],
            "ROI ultime 50 %": round(rs["recent_roi"], 2),
            "Profitto ultime 50 €": round(
                rs["recent_profit"],
                2,
            ),
            "Win rate ultime 50 %": round(
                rs["recent_win_rate"],
                2,
            ),
            "Max perdite ultime 50": int(
                rs["recent_max_losing_streak"]
            ),
        })

    recent_df = pd.DataFrame(recent_rows)

    elite = elite.merge(
        recent_df,
        on="Strategia",
        how="left",
    )

    # --------------------------------------------------------
    # FILTRO ELITE
    # --------------------------------------------------------
    official_name = ""
    if isinstance(official_state, dict):
        official_name = str(
            official_state.get("official") or ""
        )

    elite["_is_official"] = (
        elite["Strategia"].astype(str) == official_name
    )

    elite["Partite"] = pd.to_numeric(
        elite["Partite"],
        errors="coerce",
    ).fillna(0)

    elite["Rilevazioni"] = pd.to_numeric(
        elite["Rilevazioni"],
        errors="coerce",
    ).fillna(0)

    elite["Stabilità %"] = pd.to_numeric(
        elite["Stabilità %"],
        errors="coerce",
    ).fillna(0)

    elite["ROI %"] = pd.to_numeric(
        elite["ROI %"],
        errors="coerce",
    ).fillna(0)

    elite["ROI ultime 50 %"] = pd.to_numeric(
        elite["ROI ultime 50 %"],
        errors="coerce",
    ).fillna(0)

    elite["Max perdite consecutive"] = pd.to_numeric(
        elite.get(
            "Max perdite consecutive",
            pd.Series(0, index=elite.index),
        ),
        errors="coerce",
    ).fillna(0)

    elite["Peggior ROI blocco %"] = pd.to_numeric(
        elite.get(
            "Peggior ROI blocco %",
            pd.Series(0, index=elite.index),
        ),
        errors="coerce",
    ).fillna(0)

    validated_mask = elite["Validata"].astype(str).str.contains(
        "✅",
        regex=False,
    )

    eligible_mask = (
        (elite["Partite"] >= ELITE_MIN_MATCHES)
        & (elite["Rilevazioni"] >= ELITE_MIN_OBSERVATIONS)
        & validated_mask
        & (elite["Stabilità %"] >= 66.7)
        & (~elite["Stato"].astype(str).str.contains("Provvisoria"))
    )

    # La strategia ufficiale rimane comunque nella schermata.
    elite = elite[
        eligible_mask | elite["_is_official"]
    ].copy()

    if elite.empty:
        return pd.DataFrame()

    # --------------------------------------------------------
    # PUNTEGGIO ELITE
    # --------------------------------------------------------
    def normalize_0_100(series, higher_is_better=True):
        s = pd.to_numeric(series, errors="coerce")
        if s.notna().sum() == 0:
            return pd.Series(
                50.0,
                index=series.index,
            )

        lo = float(s.min())
        hi = float(s.max())

        if math.isclose(lo, hi):
            out = pd.Series(
                50.0,
                index=series.index,
            )
        else:
            out = (s - lo) / (hi - lo) * 100.0

        if not higher_is_better:
            out = 100.0 - out

        return out.fillna(0.0)

    total_roi_component = normalize_0_100(
        elite["ROI %"]
    )

    recent_roi_component = normalize_0_100(
        elite["ROI ultime 50 %"]
    )

    sample_component = (
        elite["Partite"].clip(upper=150)
        / 150.0
        * 100.0
    )

    observations_component = (
        elite["Rilevazioni"].clip(upper=15)
        / 15.0
        * 100.0
    )

    top10_component = pd.to_numeric(
        elite["Top 10 %"],
        errors="coerce",
    ).fillna(0).clip(0, 100)

    stability_component = elite[
        "Stabilità %"
    ].clip(0, 100)

    worst_block_component = normalize_0_100(
        elite["Peggior ROI blocco %"]
    )

    streak_component = normalize_0_100(
        elite["Max perdite consecutive"],
        higher_is_better=False,
    )

    definitive_component = normalize_0_100(
        elite["Punteggio definitivo"]
    )

    # Più peso alla solidità che al "colpo" del giorno.
    elite["Punteggio Elite"] = (
        definitive_component * 0.20
        + total_roi_component * 0.15
        + recent_roi_component * 0.20
        + stability_component * 0.15
        + observations_component * 0.10
        + top10_component * 0.08
        + sample_component * 0.05
        + worst_block_component * 0.04
        + streak_component * 0.03
    ).round(2)

    # Stato Elite più leggibile.
    def elite_status(row):
        if bool(row.get("_is_official", False)):
            return "🎯 Ufficiale"

        obs = int(row.get("Rilevazioni", 0) or 0)
        matches = int(row.get("Partite", 0) or 0)
        stability = float(
            row.get("Stabilità %", 0) or 0
        )
        recent_roi = float(
            row.get("ROI ultime 50 %", 0) or 0
        )

        if (
            obs >= 10
            and matches >= 100
            and stability >= 100
            and recent_roi > 0
        ):
            return "🟢 Elite forte"

        if (
            obs >= 8
            and matches >= 90
            and stability >= 66.7
        ):
            return "🟢 Elite"

        return "🟡 Candidata Elite"

    elite["Stato Elite"] = elite.apply(
        elite_status,
        axis=1,
    )

    elite = elite.sort_values(
        [
            "Punteggio Elite",
            "_is_official",
            "Rilevazioni",
            "Partite",
        ],
        ascending=[
            False,
            False,
            False,
            False,
        ],
    ).reset_index(drop=True)

    # Manteniamo poche righe utili.
    if len(elite) > ELITE_MAX_ROWS:
        top = elite.head(
            ELITE_MAX_ROWS
        ).copy()

        # Se l'ufficiale non fosse nelle prime 12,
        # la aggiungiamo comunque.
        if (
            official_name
            and official_name
            not in top["Strategia"].astype(str).tolist()
        ):
            official_rows = elite[
                elite["Strategia"].astype(str)
                == official_name
            ]

            if not official_rows.empty:
                top = pd.concat(
                    [
                        top.head(ELITE_MAX_ROWS - 1),
                        official_rows.head(1),
                    ],
                    ignore_index=True,
                )

        elite = top.copy()

    elite = elite.sort_values(
        [
            "Punteggio Elite",
            "_is_official",
        ],
        ascending=[
            False,
            False,
        ],
    ).reset_index(drop=True)

    elite.insert(
        0,
        "Posizione Elite",
        range(1, len(elite) + 1),
    )

    columns = [
        "Posizione Elite",
        "Strategia",
        "Stato Elite",
        "Punteggio Elite",
        "Partite",
        "Vinte",
        "Perse",
        "Win rate %",
        "Quota media",
        "Profitto €",
        "ROI %",
        "Partite recenti",
        "ROI ultime 50 %",
        "Profitto ultime 50 €",
        "Win rate ultime 50 %",
        "Stabilità %",
        "Peggior ROI blocco %",
        "Max perdite consecutive",
        "Rilevazioni",
        "Top 5 %",
        "Top 10 %",
        "Posizione media",
        "Validata",
    ]

    return elite[
        [c for c in columns if c in elite.columns]
    ]



def build_strong_watchlist(ranking, history_table, snapshot_count, selections, closed, elite_table=None):
    """Solo strategie mature, profittevoli e non ancora Elite."""
    if ranking is None or ranking.empty:
        return pd.DataFrame()

    definitive = definitive_strategy_ranking(
        ranking, history_table, snapshot_count, selections
    )
    if definitive.empty:
        return pd.DataFrame()

    extra_cols = [
        "Strategia", "Max perdite consecutive", "Peggior ROI blocco %",
        "ROI ricerca %", "ROI verifica %", "Punteggio",
        "Affidabilità campione %"
    ]
    extras = ranking[[c for c in extra_cols if c in ranking.columns]].copy()
    watch = definitive.merge(extras, on="Strategia", how="left")

    elite_names = set()
    if elite_table is not None and not elite_table.empty:
        elite_names = set(elite_table["Strategia"].astype(str))
    watch = watch[~watch["Strategia"].astype(str).isin(elite_names)].copy()

    for col in ["Partite","Rilevazioni","Stabilità %","ROI %","Profitto €","Punteggio definitivo"]:
        watch[col] = pd.to_numeric(watch[col], errors="coerce").fillna(0)

    validated = watch["Validata"].astype(str).str.contains("✅", regex=False)
    watch = watch[
        (watch["Partite"] >= WATCH_MIN_MATCHES)
        & (watch["Rilevazioni"] >= WATCH_MIN_OBSERVATIONS)
        & (watch["Stabilità %"] >= WATCH_MIN_STABILITY)
        & (watch["ROI %"] > 0)
        & (watch["Profitto €"] > 0)
        & validated
    ].copy()

    if watch.empty:
        return pd.DataFrame()

    recent = []
    for _, row in watch.iterrows():
        name = str(row["Strategia"])
        sdf = apply_generated_strategy_name(closed, name)
        rs = recent_strategy_statistics(sdf, ELITE_RECENT_WINDOW)
        recent.append({
            "Strategia": name,
            "ROI ultime 50 %": round(rs["recent_roi"], 2),
            "Profitto ultime 50 €": round(rs["recent_profit"], 2),
            "Win rate ultime 50 %": round(rs["recent_win_rate"], 2),
        })

    watch = watch.merge(pd.DataFrame(recent), on="Strategia", how="left")
    watch["_recent_ok"] = (pd.to_numeric(watch["ROI ultime 50 %"], errors="coerce").fillna(0) > 0).astype(int)
    watch = watch.sort_values(
        ["_recent_ok","Punteggio definitivo","Stabilità %","Rilevazioni","Partite"],
        ascending=[False,False,False,False,False]
    ).head(WATCH_MAX_ROWS).reset_index(drop=True)

    # La Classifica Definitiva contiene già una colonna "Posizione".
    # Nella watchlist vogliamo ricalcolare la posizione specifica di questa
    # tabella, quindi rimuoviamo prima l'eventuale colonna preesistente.
    watch = watch.drop(
        columns=["Posizione"],
        errors="ignore",
    )

    watch.insert(
        0,
        "Posizione",
        range(1, len(watch) + 1),
    )
    watch["Stato"] = watch["ROI ultime 50 %"].apply(
        lambda x: "🟢 Vicina all'Elite" if float(x or 0) > 0 else "🟡 Solida, recente da verificare"
    )

    cols = [
        "Posizione","Strategia","Stato","Partite","Vinte","Perse","Win rate %",
        "Quota media","Profitto €","ROI %","ROI ultime 50 %","Profitto ultime 50 €",
        "Win rate ultime 50 %","Stabilità %","Rilevazioni","Top 10 %","Validata",
        "Punteggio definitivo"
    ]
    return watch[[c for c in cols if c in watch.columns]]


def elite_decision_message(elite, official_state):
    if elite is None or elite.empty:
        return ""

    best = elite.iloc[0]
    official_name = ""

    if isinstance(official_state, dict):
        official_name = str(
            official_state.get("official") or ""
        )

    if str(best.get("Strategia", "")) == official_name:
        return (
            "✅ La strategia ufficiale è anche la migliore "
            "della Classifica Elite: nessun motivo per cambiare."
        )

    return (
        f'🧪 Miglior alternativa Elite: '
        f'{best.get("Strategia", "")}. '
        f'Non cambia automaticamente la strategia ufficiale: '
        f'deve superarla secondo le regole delle conferme consecutive.'
    )



def human_strategy_name(strategy_name):
    """Rende leggibili le condizioni dinamiche senza cambiare i nomi interni."""
    name = str(strategy_name or "")
    name = name.replace(
        "Allibramento < media",
        "Allibramento valore < media della partita"
    )
    name = name.replace(
        "Allibramento > media",
        "Allibramento valore > media della partita"
    )
    return name


def add_human_strategy_column(df):
    if df is None or df.empty or "Strategia" not in df.columns:
        return df
    out = df.copy()
    out["Strategia"] = out["Strategia"].map(human_strategy_name)
    return out


def add_allibramento_explanation_columns(df):
    """Aggiunge i valori operativi ALLB per capire il confronto con la media."""
    if df is None or df.empty:
        return df

    out = df.copy()
    allb_value = pd.to_numeric(out.get("allibramento_value"), errors="coerce")
    allb_avg = pd.to_numeric(out.get("allibramento_avg"), errors="coerce")

    out["Valore ALLB"] = allb_value.round(2)
    out["Media ALLB"] = allb_avg.round(2)
    out["Δ ALLB"] = (allb_value - allb_avg).round(2)

    def allb_check(row):
        value = row.get("Valore ALLB")
        avg = row.get("Media ALLB")
        if pd.isna(value) or pd.isna(avg):
            return ""
        if float(value) < float(avg):
            return f"✅ {float(value):.2f} < {float(avg):.2f}"
        if float(value) > float(avg):
            return f"❌ {float(value):.2f} > {float(avg):.2f}"
        return f"➖ {float(value):.2f} = {float(avg):.2f}"

    out["Confronto ALLB"] = out.apply(allb_check, axis=1)
    return out




def monthly_strategy_statistics(df):
    if df is None or df.empty:
        return pd.DataFrame()

    work = df[df["outcome"].isin(["V", "P"])].copy()
    if work.empty:
        return pd.DataFrame()

    work["_date_dt"] = pd.to_datetime(work["date"], errors="coerce")
    work = work[work["_date_dt"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    work["_month"] = work["_date_dt"].dt.to_period("M")
    rows = []
    current_month = pd.Timestamp(datetime.today()).to_period("M")

    for month, mdf in work.groupby("_month", sort=True):
        s = strategy_statistics(mdf)
        month_start = month.to_timestamp()
        rows.append({
            "Mese": month_start.strftime("%m/%Y"),
            "_month_sort": month_start,
            "Stato mese": "🟡 In corso" if month == current_month else "✅ Completo",
            "Partite": s["closed"],
            "Vinte": s["wins"],
            "Perse": s["losses"],
            "Win rate %": round(s["win_rate"], 2),
            "Quota media": round(s["avg_odds"], 2),
            "Puntato €": round(s["staked"], 2),
            "Profitto €": round(s["profit"], 2),
            "ROI %": round(s["roi"], 2),
            "Max perdite consecutive": s["max_losing_streak"],
        })

    result = pd.DataFrame(rows).sort_values("_month_sort").reset_index(drop=True)
    result["Profitto cumulato €"] = pd.to_numeric(
        result["Profitto €"], errors="coerce"
    ).fillna(0).cumsum().round(2)
    return result


def show_monthly_strategy_analysis(selected_df):
    if selected_df is None or selected_df.empty:
        st.info("Nessuna partita conclusa rispetta questa selezione.")
        return

    closed_selected = selected_df[selected_df["outcome"].isin(["V", "P"])].copy()
    if closed_selected.empty:
        st.info("Nessuna partita conclusa rispetta questa selezione.")
        return

    total = strategy_statistics(closed_selected)

    st.markdown("### Totale strategia")
    a,b,c,d = st.columns(4)
    a.metric("Partite", total["closed"])
    b.metric("Vinte", total["wins"])
    c.metric("Perse", total["losses"])
    d.metric("Win rate", f'{total["win_rate"]:.2f}%')

    e,f,g,h = st.columns(4)
    e.metric("Puntato", f'€ {total["staked"]:.2f}')
    f.metric("Profitto", f'€ {total["profit"]:.2f}')
    g.metric("ROI", f'{total["roi"]:.2f}%')
    h.metric("Quota media", f'{total["avg_odds"]:.2f}')

    monthly = monthly_strategy_statistics(closed_selected)
    if monthly.empty:
        st.info("Non riesco a suddividere le partite per mese.")
        return

    st.markdown("### 📅 Risultati mese per mese")
    st.dataframe(
        monthly.drop(columns=["_month_sort"], errors="ignore"),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "🟡 In corso = il mese non è ancora terminato, quindi profitto e ROI "
        "non sono confrontabili alla pari con un mese completo."
    )

    st.markdown("### 💰 Profitto per mese")
    st.bar_chart(monthly.set_index("Mese")[["Profitto €"]])

    st.markdown("### 📈 Profitto cumulato per mese")
    st.line_chart(monthly.set_index("Mese")[["Profitto cumulato €"]])

    best = monthly.sort_values(
        ["Profitto €", "ROI %"], ascending=[False, False]
    ).iloc[0]
    worst = monthly.sort_values(
        ["Profitto €", "ROI %"], ascending=[True, True]
    ).iloc[0]

    c1,c2 = st.columns(2)
    c1.success(
        f'🏆 Miglior mese: {best["Mese"]} — € {float(best["Profitto €"]):.2f} '
        f'— ROI {float(best["ROI %"]):.2f}%'
    )
    c2.warning(
        f'📉 Peggior mese: {worst["Mese"]} — € {float(worst["Profitto €"]):.2f} '
        f'— ROI {float(worst["ROI %"]):.2f}%'
    )


def editor_form(data, prefix):
    left, right = st.columns(2)
    with left:
        data["date"] = st.text_input("Data", str(data.get("date","")), key=f"{prefix}_date")
        data["time"] = st.text_input("Ora", str(data.get("time","")), key=f"{prefix}_time")
        data["league"] = st.text_input("Campionato", str(data.get("league","")), key=f"{prefix}_league")
        data["match_name"] = st.text_input("Partita", str(data.get("match_name","")), key=f"{prefix}_match")
        data["round_name"] = st.text_input("Giornata/Fase", str(data.get("round_name","")), key=f"{prefix}_round")
        data["market"] = st.text_input("Mercato", str(data.get("market","1X2")), key=f"{prefix}_market")
        data["pick"] = st.text_input("Esito giocato", str(data.get("pick","1")), key=f"{prefix}_pick")
        data["selected_by_ale"] = st.text_input("Scelto da Ale", str(data.get("selected_by_ale","Ottimo 1")), key=f"{prefix}_ale")
        data["associated_method"] = st.text_input("Metodo associato", str(data.get("associated_method","")), key=f"{prefix}_associated")
    with right:
        for key,label in [
            ("prob_1","Prob. IA 1"),("prob_x","Prob. IA X"),("prob_2","Prob. IA 2"),
            ("fair_odds","Quota reale"),("opening_odds","Quota iniziale"),
            ("current_odds","Quota attuale"),("allibramento_value","Valore allibramento"),
            ("allibramento_avg","Allibramento medio"),("stake","Puntata (€)"),
            ("played_odds","Quota giocata")
        ]:
            data[key] = st.number_input(label, value=float(data.get(key) or 0), step=0.01, key=f"{prefix}_{key}")
        for key,label in [
            ("c_aff","C. AFF."),("flbk","FLBK"),("c_fb","C. FB."),
            ("qra_qa","QRA/QA"),("qi_qa","QI/QA"),
            ("allibramento_color","Colore allibramento"),("allb","ALLB"),
            ("mtr","MTR"),("scl","SCL"),("cal","CAL"),("status","STATUS")
        ]:
            data[key] = st.text_input(label, str(data.get(key,"")), key=f"{prefix}_{key}")
    st.markdown("#### Metodi associati")
    cols = st.columns(3)
    for i,(label,col) in enumerate(METHOD_COLUMNS.items()):
        data[col] = 1 if cols[i%3].checkbox(label, value=bool(data.get(col,0)), key=f"{prefix}_{col}") else 0
    return data


st.title("📊 Stats4Bets")
st.caption(f"Archivio e analisi partite • Archivio: {storage_label()}")

page = st.sidebar.radio("Menu", [
    "🏠 Home","🎯 Partite da giocare","📅 Analisi mensile","⚡ Inserimento rapido",
    "➕ Nuova partita","📋 Database","🏆 Aggiorna risultato","✏️ Modifica/Elimina",
    "📊 Dashboard","🔎 Analisi filtri","🧪 Laboratorio Strategie",
    "🧠 Trova metodo migliore","📥 Importa/Esporta","⚙️ Configurazione"
])

if page == "⚡ Inserimento rapido":
    st.subheader("⚡ Inserimento rapido")
    st.caption("Compila solo i campi principali. Gli altri sono facoltativi.")
    record = blank_match()
    record["id"] = next_id()
    st.info(f"Nuovo ID automatico: {record['id']}")

    c1, c2 = st.columns(2)
    with c1:
        record["date"] = st.date_input("Data", value=datetime.today().date(), key="quick_date").isoformat()
        record["time"] = st.time_input("Ora", value=datetime.now().replace(second=0, microsecond=0).time(), key="quick_time").strftime("%H:%M")
        record["league"] = st.text_input("Campionato", placeholder="es. Irlanda B", key="quick_league")
        record["match_name"] = st.text_input("Partita", placeholder="Casa - Trasferta", key="quick_match")
        record["selected_by_ale"] = st.selectbox("Metodo principale", ["Ottimo 1", "Ottimo 2", "Altro"], key="quick_selected")
    with c2:
        record["market"] = st.selectbox("Mercato", ["1X2", "Over/Under", "GG/NG", "Altro"], key="quick_market")
        record["pick"] = st.selectbox("Esito giocato", ["1", "X", "2", "1X", "X2", "12", "Over 1.5", "Over 2.5", "Under 3.5", "Altro"], key="quick_pick")
        record["played_odds"] = st.number_input("Quota giocata", min_value=1.00, value=1.40, step=0.01, key="quick_odds")
        record["stake"] = st.number_input("Puntata (€)", min_value=0.0, value=20.0, step=1.0, key="quick_stake")
        record["allibramento_color"] = st.selectbox("Allibramento", ["", "VE", "GI", "VI", "RO"], key="quick_allb")

    st.markdown("### Filtri principali")
    f1, f2, f3 = st.columns(3)
    record["scl"] = f1.selectbox("SCL", ["", "VE", "GI", "VI", "RO"], key="quick_scl")
    record["mtr"] = f2.selectbox("MTR", ["", "VE", "GI", "VI", "RO"], key="quick_mtr")
    record["status"] = f3.selectbox("STATUS", ["", "VE", "GI", "VI", "RO"], key="quick_status")

    st.markdown("### Metodi associati")
    selected_methods=[]
    method_cols=st.columns(3)
    for idx,(label,column) in enumerate(METHOD_COLUMNS.items()):
        checked=method_cols[idx%3].checkbox(label,key=f"quick_{column}")
        record[column]=1 if checked else 0
        if checked: selected_methods.append(label)
    record["associated_method"]=" | ".join(selected_methods)

    with st.expander("Campi avanzati facoltativi"):
        a1,a2=st.columns(2)
        with a1:
            record["prob_1"] = st.number_input("Prob. IA 1", value=0.0, step=0.1, key="quick_prob1")
            record["prob_x"] = st.number_input("Prob. IA X", value=0.0, step=0.1, key="quick_probx")
            record["prob_2"] = st.number_input("Prob. IA 2", value=0.0, step=0.1, key="quick_prob2")
            record["fair_odds"] = st.number_input("Quota reale", value=0.0, step=0.01, key="quick_fair")
            record["opening_odds"] = st.number_input("Quota iniziale", value=0.0, step=0.01, key="quick_open")
            record["current_odds"] = st.number_input("Quota attuale", value=0.0, step=0.01, key="quick_current")
        with a2:
            record["c_aff"] = st.selectbox("C. AFF.", ["", "VE", "GI", "VI", "RO"], key="quick_caff")
            record["flbk"] = st.selectbox("FLBK", ["", "VE", "GI", "VI", "RO"], key="quick_flbk")
            record["c_fb"] = st.selectbox("C. FB.", ["", "VE", "GI", "VI", "RO"], key="quick_cfb")
            record["qra_qa"] = st.selectbox("QRA/QA", ["", "VE", "GI", "VI", "RO"], key="quick_qraqa")
            record["qi_qa"] = st.selectbox("QI/QA", ["", "VE", "GI", "VI", "RO"], key="quick_qiqa")
            record["cal"] = st.selectbox("CAL", ["", "VE", "GI", "VI", "RO"], key="quick_cal")

    if st.button("✅ Salva partita", type="primary", use_container_width=True):
        if not record["match_name"].strip():
            st.error("Inserisci il nome della partita.")
        elif not record["league"].strip():
            st.error("Inserisci il campionato.")
        else:
            record["id"] = next_id()
            insert_match(record)
            st.success(f"Partita salvata con ID {record['id']}.")
            st.balloons()

elif page == "📅 Analisi mensile":
    st.subheader("📅 Analisi mensile strategie")
    st.caption(
        "Scegli una strategia già trovata dal motore oppure costruiscila manualmente. "
        "L'app divide automaticamente profitto, ROI e risultati mese per mese."
    )

    df = get_matches()
    closed = df[df["outcome"].isin(["V", "P"])].copy() if not df.empty else pd.DataFrame(columns=ALL_COLUMNS)

    if closed.empty:
        st.info("Servono partite concluse per fare l'analisi mensile.")
    else:
        strategy_tab, manual_tab = st.tabs([
            "🏆 Strategie trovate",
            "🎛️ Strategia personalizzata",
        ])

        with strategy_tab:
            ranking_monthly = st.session_state.get("strategy_ranking_v2")
            selections_monthly = st.session_state.get("strategy_selections_v2", {})
            official_state_monthly = load_strategy_follow_state()

            available_names = []

            if isinstance(official_state_monthly, dict):
                official_name = str(official_state_monthly.get("official") or "")
                if official_name:
                    available_names.append(("🎯 Ufficiale", official_name))

            if isinstance(ranking_monthly, pd.DataFrame) and not ranking_monthly.empty:
                hist_monthly, snap_monthly = strategy_history_summary(
                    ranking_monthly["Strategia"].tolist(),
                    current_ranking=ranking_monthly,
                )

                definitive_monthly = definitive_strategy_ranking(
                    ranking_monthly,
                    hist_monthly,
                    snap_monthly,
                    selections_monthly,
                )

                elite_monthly = build_elite_ranking(
                    ranking=ranking_monthly,
                    history_table=hist_monthly,
                    snapshot_count=snap_monthly,
                    selections=selections_monthly,
                    closed=closed,
                    official_state=official_state_monthly,
                )

                if not elite_monthly.empty:
                    for _, row in elite_monthly.iterrows():
                        available_names.append(("🏆 Elite", str(row["Strategia"])))

                watch_monthly = build_strong_watchlist(
                    ranking_monthly,
                    hist_monthly,
                    snap_monthly,
                    selections_monthly,
                    closed,
                    elite_monthly,
                )

                if not watch_monthly.empty:
                    for _, row in watch_monthly.iterrows():
                        available_names.append(("👀 Osservazione forte", str(row["Strategia"])))

                if not definitive_monthly.empty:
                    for _, row in definitive_monthly.head(10).iterrows():
                        available_names.append(("📊 Classifica", str(row["Strategia"])))

            seen = set()
            unique_options = []
            for category, name in available_names:
                if name and name not in seen:
                    unique_options.append((category, name))
                    seen.add(name)

            if not unique_options:
                st.info(
                    "Non ci sono ancora strategie disponibili in questa sessione. "
                    "Vai una volta in 'Trova metodo migliore' e premi "
                    "'Cerca migliori strategie', oppure usa la scheda personalizzata."
                )
            else:
                option_labels = [
                    f"{category} — {human_strategy_name(name)}"
                    for category, name in unique_options
                ]
                selected_option = st.selectbox(
                    "Strategia",
                    option_labels,
                    key="monthly_strategy_select",
                )
                idx = option_labels.index(selected_option)
                _, selected_name = unique_options[idx]

                st.info("Analizzo: " + human_strategy_name(selected_name))

                strategy_df = apply_generated_strategy_name(
                    closed,
                    selected_name,
                )
                show_monthly_strategy_analysis(strategy_df)

        with manual_tab:
            st.markdown("### Costruisci la strategia")
            st.caption("Stessi filtri del Laboratorio, ma con analisi mese per mese.")

            def monthly_opts(column):
                values = closed[column].dropna().astype(str).str.strip()
                return sorted(
                    v for v in values.unique()
                    if v and v.lower() not in {"nan", "none"}
                )

            a,b,c = st.columns(3)
            allb_m=a.multiselect("Allibramento",monthly_opts("allibramento_color"),key="monthly_allb")
            mtr_m=b.multiselect("MTR",monthly_opts("mtr"),key="monthly_mtr")
            scl_m=c.multiselect("SCL",monthly_opts("scl"),key="monthly_scl")

            a,b,c = st.columns(3)
            cal_m=a.multiselect("CAL",monthly_opts("cal"),key="monthly_cal")
            caff_m=b.multiselect("C. AFF.",monthly_opts("c_aff"),key="monthly_caff")
            flbk_m=c.multiselect("FLBK",monthly_opts("flbk"),key="monthly_flbk")

            a,b,c = st.columns(3)
            cfb_m=a.multiselect("C. FB.",monthly_opts("c_fb"),key="monthly_cfb")
            qra_m=b.multiselect("QRA/QA",monthly_opts("qra_qa"),key="monthly_qra")
            qi_m=c.multiselect("QI/QA",monthly_opts("qi_qa"),key="monthly_qi")

            a,b = st.columns(2)
            status_m=a.multiselect("STATUS",monthly_opts("status"),key="monthly_status")
            leagues_m=b.multiselect("Campionati",monthly_opts("league"),key="monthly_leagues")

            odds_m=pd.to_numeric(closed["current_odds"],errors="coerce").dropna()
            probs_m=pd.to_numeric(closed["prob_1"],errors="coerce").dropna()

            a,b=st.columns(2)
            min_odds_m=a.number_input("Quota minima",value=float(odds_m.min()) if not odds_m.empty else 1.20,step=0.01,key="monthly_min_odds")
            max_odds_m=b.number_input("Quota massima",value=float(odds_m.max()) if not odds_m.empty else 2.00,step=0.01,key="monthly_max_odds")

            a,b=st.columns(2)
            min_prob_m=a.number_input("Probabilità 1 minima",value=float(probs_m.min()) if not probs_m.empty else 0.0,step=0.5,key="monthly_min_prob")
            max_prob_m=b.number_input("Probabilità 1 massima",value=float(probs_m.max()) if not probs_m.empty else 100.0,step=0.5,key="monthly_max_prob")

            ccvals_m=pd.to_numeric(closed["c_aff_count"],errors="coerce").dropna()
            use_cc_m=st.checkbox("Usa filtro C.AFF. COUNT",value=False,key="monthly_use_cc")

            if ccvals_m.empty:
                min_cc_m=max_cc_m=0
            else:
                a,b=st.columns(2)
                min_cc_m=a.number_input("C.AFF. COUNT minimo",min_value=0,value=int(ccvals_m.min()),step=1,key="monthly_min_cc")
                max_cc_m=b.number_input("C.AFF. COUNT massimo",min_value=0,value=int(ccvals_m.max()),step=1,key="monthly_max_cc")

            methods_m=st.multiselect(
                "Metodi associati richiesti",
                list(METHOD_COLUMNS),
                key="monthly_methods",
            )

            filters_m={
                "allibramento_color":allb_m,
                "mtr":mtr_m,
                "scl":scl_m,
                "cal":cal_m,
                "c_aff":caff_m,
                "flbk":flbk_m,
                "c_fb":cfb_m,
                "qra_qa":qra_m,
                "qi_qa":qi_m,
                "status":status_m,
                "league":leagues_m,
            }

            monthly_filtered=apply_strategy_filters(
                closed,
                filters_m,
                min_odds_m,
                max_odds_m,
                min_prob_m,
                max_prob_m,
            )

            if use_cc_m and not ccvals_m.empty:
                cc_numeric_m=pd.to_numeric(monthly_filtered["c_aff_count"],errors="coerce")
                monthly_filtered=monthly_filtered[
                    (cc_numeric_m>=min_cc_m)&(cc_numeric_m<=max_cc_m)
                ]

            for method in methods_m:
                method_col=METHOD_COLUMNS[method]
                monthly_filtered=monthly_filtered[
                    pd.to_numeric(
                        monthly_filtered[method_col],
                        errors="coerce",
                    ).fillna(0)==1
                ]

            show_monthly_strategy_analysis(monthly_filtered)


elif page == "🎯 Partite da giocare":
    st.subheader("🎯 Partite da giocare")
    st.caption(
        "Qui puoi applicare la strategia ufficiale oppure usare tutti i filtri manuali. "
        "I filtri restano sempre visibili, anche quando non ci sono partite in attesa."
    )

    df = get_matches()

    if df.empty:
        pending = pd.DataFrame(columns=ALL_COLUMNS)
    else:
        pending = df[
            ~df["outcome"].isin(["V", "P"])
        ].copy()

    st.metric("Partite attualmente in attesa", len(pending))

    def popts(col):
        # Le opzioni dei filtri vengono prese dall'intero database,
        # non solo dalle partite in attesa. In questo modo le tendine
        # restano utilizzabili anche quando pending = 0.
        if df.empty or col not in df.columns:
            return []
        vals = (
            df[col]
            .dropna()
            .astype(str)
            .str.strip()
        )
        return sorted(
            v for v in vals.unique()
            if v and v.lower() not in {"nan", "none"}
        )

    st.info(
        "ℹ️ 'Allibramento valore < media della partita' significa che il confronto "
        "è fatto per ogni singola partita: Valore ALLB < Media ALLB. "
        "Nelle tabelle trovi entrambi i valori e il confronto già calcolato."
    )

    auto_tab, manual_tab = st.tabs([
        "🎯 Strategia ufficiale",
        "🎛️ Filtri manuali",
    ])

    with auto_tab:
        state = load_strategy_follow_state()
        official = (
            str(state.get("official") or "")
            if isinstance(state, dict)
            else ""
        )

        closed_now = (
            df[df["outcome"].isin(["V", "P"])].copy()
            if not df.empty
            else pd.DataFrame(columns=ALL_COLUMNS)
        )

        official_hist = (
            apply_generated_strategy_name(
                closed_now,
                official,
            )
            if official
            else pd.DataFrame()
        )

        official_stats = (
            strategy_statistics(official_hist)
            if not official_hist.empty
            else {"closed": 0, "profit": 0.0, "roi": 0.0}
        )

        usable = (
            bool(official)
            and official_stats["closed"] >= ELITE_MIN_MATCHES
            and official_stats["profit"] > 0
            and official_stats["roi"] > 0
        )

        if not official:
            st.warning(
                "Nessuna strategia ufficiale disponibile al momento."
            )
        elif not usable:
            st.warning(
                f"⚠️ Precedente ufficiale: {official}. "
                "Non viene usata automaticamente perché oggi "
                "non supera i controlli minimi."
            )
        else:
            st.success(
                "🎯 Strategia applicata: " + human_strategy_name(official)
            )

            auto = apply_generated_strategy_name(
                pending,
                official,
            )

            st.metric(
                "Partite compatibili con la strategia ufficiale",
                len(auto),
            )

            if auto.empty:
                st.info(
                    "Nessuna partita in attesa rispetta la strategia ufficiale."
                )
            else:
                show = add_allibramento_explanation_columns(auto.copy())
                show["Quota"] = pd.to_numeric(
                    show["current_odds"],
                    errors="coerce",
                ).round(2)

                cols = [
                    "date","time","league","match_name","Quota",
                    "Valore ALLB","Media ALLB","Δ ALLB","Confronto ALLB",
                    "allibramento_color","mtr","scl","cal",
                    "c_aff","c_aff_count","flbk","c_fb",
                    "qra_qa","qi_qa","status"
                ]

                show = show[
                    [c for c in cols if c in show.columns]
                ].rename(columns={
                    "date":"Data",
                    "time":"Ora",
                    "league":"Campionato",
                    "match_name":"Partita",
                    "allibramento_color":"ALLB",
                    "mtr":"MTR",
                    "scl":"SCL",
                    "cal":"CAL",
                    "c_aff":"C.AFF.",
                    "c_aff_count":"C.AFF. COUNT",
                    "flbk":"FLBK",
                    "c_fb":"C.FB.",
                    "qra_qa":"QRA/QA",
                    "qi_qa":"QI/QA",
                    "status":"STATUS",
                })

                st.dataframe(
                    show.sort_values(
                        ["Data","Ora"],
                        ascending=[True, True],
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    with manual_tab:
        st.markdown("### Indicatori")

        a,b,c = st.columns(3)
        allb = a.multiselect(
            "Allibramento",
            popts("allibramento_color"),
            key="play_allb",
        )
        mtr = b.multiselect(
            "MTR",
            popts("mtr"),
            key="play_mtr",
        )
        scl = c.multiselect(
            "SCL",
            popts("scl"),
            key="play_scl",
        )

        a,b,c = st.columns(3)
        cal = a.multiselect(
            "CAL",
            popts("cal"),
            key="play_cal",
        )
        caff = b.multiselect(
            "C. AFF.",
            popts("c_aff"),
            key="play_caff",
        )
        flbk = c.multiselect(
            "FLBK",
            popts("flbk"),
            key="play_flbk",
        )

        a,b,c = st.columns(3)
        cfb = a.multiselect(
            "C. FB.",
            popts("c_fb"),
            key="play_cfb",
        )
        qra = b.multiselect(
            "QRA/QA",
            popts("qra_qa"),
            key="play_qra",
        )
        qi = c.multiselect(
            "QI/QA",
            popts("qi_qa"),
            key="play_qi",
        )

        a,b = st.columns(2)
        status = a.multiselect(
            "STATUS",
            popts("status"),
            key="play_status",
        )
        leagues = b.multiselect(
            "Campionati",
            popts("league"),
            key="play_leagues",
        )

        st.markdown("### Quota e probabilità")

        odds = (
            pd.to_numeric(
                pending["current_odds"],
                errors="coerce",
            ).dropna()
            if not pending.empty
            else pd.Series(dtype=float)
        )

        probs = (
            pd.to_numeric(
                pending["prob_1"],
                errors="coerce",
            ).dropna()
            if not pending.empty
            else pd.Series(dtype=float)
        )

        a,b = st.columns(2)
        min_odds = a.number_input(
            "Quota minima",
            value=float(odds.min())
            if not odds.empty else 1.20,
            step=0.01,
            key="play_min_odds",
        )
        max_odds = b.number_input(
            "Quota massima",
            value=float(odds.max())
            if not odds.empty else 2.00,
            step=0.01,
            key="play_max_odds",
        )

        a,b = st.columns(2)
        min_prob = a.number_input(
            "Probabilità 1 minima",
            value=float(probs.min())
            if not probs.empty else 0.0,
            step=0.5,
            key="play_min_prob",
        )
        max_prob = b.number_input(
            "Probabilità 1 massima",
            value=float(probs.max())
            if not probs.empty else 100.0,
            step=0.5,
            key="play_max_prob",
        )

        st.markdown("### C.AFF. COUNT")

        ccvals = (
            pd.to_numeric(
                pending["c_aff_count"],
                errors="coerce",
            ).dropna()
            if not pending.empty
            else pd.Series(dtype=float)
        )

        use_cc = st.checkbox(
            "Usa filtro C.AFF. COUNT",
            value=False,
            key="play_use_cc",
        )

        if ccvals.empty:
            min_cc = 0
            max_cc = 0
            st.caption(
                "Nessun valore C.AFF. COUNT disponibile nelle partite in attesa."
            )
        else:
            a,b = st.columns(2)
            min_cc = a.number_input(
                "C.AFF. COUNT minimo",
                min_value=0,
                value=int(ccvals.min()),
                step=1,
                key="play_min_cc",
            )
            max_cc = b.number_input(
                "C.AFF. COUNT massimo",
                min_value=0,
                value=int(ccvals.max()),
                step=1,
                key="play_max_cc",
            )

        st.markdown("### Metodi associati")
        methods = st.multiselect(
            "Metodi associati richiesti",
            list(METHOD_COLUMNS),
            key="play_methods",
        )

        filters = {
            "allibramento_color": allb,
            "mtr": mtr,
            "scl": scl,
            "cal": cal,
            "c_aff": caff,
            "flbk": flbk,
            "c_fb": cfb,
            "qra_qa": qra,
            "qi_qa": qi,
            "status": status,
            "league": leagues,
        }

        if pending.empty:
            found = pending.copy()
        else:
            found = apply_strategy_filters(
                pending,
                filters,
                min_odds,
                max_odds,
                min_prob,
                max_prob,
            )

            if use_cc and not ccvals.empty:
                cc = pd.to_numeric(
                    found["c_aff_count"],
                    errors="coerce",
                )
                found = found[
                    (cc >= min_cc)
                    & (cc <= max_cc)
                ]

            for method in methods:
                col = METHOD_COLUMNS[method]
                found = found[
                    pd.to_numeric(
                        found[col],
                        errors="coerce",
                    ).fillna(0) == 1
                ]

        st.markdown("### Risultato filtro")
        st.metric(
            "Partite in attesa trovate",
            len(found),
        )

        if found.empty:
            st.info(
                "Nessuna partita in attesa rispetta tutti i filtri selezionati."
            )
        else:
            show = add_allibramento_explanation_columns(found.copy())
            show["Quota"] = pd.to_numeric(
                show["current_odds"],
                errors="coerce",
            ).round(2)
            show["Prob. 1"] = pd.to_numeric(
                show["prob_1"],
                errors="coerce",
            ).round(1)

            cols = [
                "date","time","league","match_name","Quota","Prob. 1",
                "Valore ALLB","Media ALLB","Δ ALLB","Confronto ALLB",
                "allibramento_color","mtr","scl","cal",
                "c_aff","c_aff_count","flbk","c_fb",
                "qra_qa","qi_qa","status"
            ]

            show = show[
                [c for c in cols if c in show.columns]
            ].rename(columns={
                "date":"Data",
                "time":"Ora",
                "league":"Campionato",
                "match_name":"Partita",
                "allibramento_color":"ALLB",
                "mtr":"MTR",
                "scl":"SCL",
                "cal":"CAL",
                "c_aff":"C.AFF.",
                "c_aff_count":"C.AFF. COUNT",
                "flbk":"FLBK",
                "c_fb":"C.FB.",
                "qra_qa":"QRA/QA",
                "qi_qa":"QI/QA",
                "status":"STATUS",
            })

            st.dataframe(
                show.sort_values(
                    ["Data","Ora"],
                    ascending=[True, True],
                ),
                use_container_width=True,
                hide_index=True,
            )


elif page == "🏠 Home":

    st.subheader("Gestione completa dal telefono")

    st.markdown("### 🤖 Aggiornamento automatico")
    partite_col, risultati_col, refresh_col = st.columns([2, 2, 1])

    with partite_col:
        if not github_configured():
            st.warning("Configura i Secrets GitHub.")
        elif st.button(
            "🔄 Aggiorna partite",
            type="primary",
            use_container_width=True,
        ):
            try:
                trigger_collector_workflow()
                st.success("Raccolta partite avviata.")
                with st.spinner("Attendi circa 1-2 minuti..."):
                    result = wait_for_collector()

                if result["ok"]:
                    st.success("✅ Partite aggiornate.")
                    time.sleep(1)
                    st.rerun()
                elif result["conclusion"] == "timeout":
                    st.warning("Workflow ancora in corso. Ricarica tra un minuto.")
                else:
                    st.error(f'Errore workflow: {result["conclusion"]}')
            except Exception as exc:
                st.error(f"Errore aggiornamento partite: {exc}")

    with risultati_col:
        if not github_configured():
            st.warning("Configura i Secrets GitHub.")
        elif st.button(
            "🏁 Aggiorna risultati e profitti",
            use_container_width=True,
        ):
            try:
                with st.spinner("Cerco risultati e calcolo profitti..."):
                    result = run_and_wait_named_workflow("results-update.yml")

                if result["ok"]:
                    st.success("✅ Risultati e profitti aggiornati.")
                    time.sleep(1)
                    st.rerun()
                elif result["conclusion"] == "timeout":
                    st.warning("Workflow ancora in corso. Ricarica tra un minuto.")
                else:
                    st.error(f'Errore workflow: {result["conclusion"]}')
                    if result.get("url"):
                        st.link_button("Apri dettaglio GitHub", result["url"])
            except Exception as exc:
                st.error(f"Errore aggiornamento risultati: {exc}")

    with refresh_col:
        if st.button("↻ Ricarica", use_container_width=True):
            st.rerun()

    st.caption(
        "La quota current_odds viene congelata al primo inserimento. "
        "Puntata fissa: 20 €."
    )
    c1,c2,c3 = st.columns(3)
    c1.info("➕ Carica screenshot/PDF e salva la partita")
    c2.info("🏆 Aggiorna risultato, ritorno e profitto")
    c3.info("🥇 Cerca i filtri e le combinazioni migliori")
    df = get_matches()
    s = summary(df)

    metrics = st.columns(4)
    metrics[0].metric("🟢 Vinte", s["wins"])
    metrics[1].metric("🔴 Perse", s["losses"])
    metrics[2].metric("💰 Profitto", f'€ {s["profit"]:.2f}')
    metrics[3].metric("📈 ROI", f'{s["roi"]:.2f}%')

    st.markdown("### Ultime partite")
    if df.empty:
        st.info("Nessuna partita salvata.")
    else:
        show = df.copy()
        show["Esito"] = show["outcome"].map({"V": "🟢 V", "P": "🔴 P"}).fillna("⏳ In attesa")
        show["Risultato"] = show["final_score"].fillna("")
        show["Quota"] = pd.to_numeric(show["current_odds"], errors="coerce").round(2)
        show["Profitto €"] = pd.to_numeric(show["profit"], errors="coerce")
        show = show.sort_values(["date", "time", "id"], ascending=[False, False, False])

        display_columns = [
            "date", "time", "match_name", "Quota",
            "Esito", "Risultato", "Profitto €"
        ]
        show = show[display_columns].rename(columns={
            "date": "Data",
            "time": "Ora",
            "match_name": "Partita",
        })

        st.dataframe(
            show.head(20),
            use_container_width=True,
            hide_index=True,
        )

elif page == "➕ Nuova partita":
    st.subheader("➕ Nuova partita")
    uploads = st.file_uploader("Carica uno o più screenshot/PDF", type=["png","jpg","jpeg","webp","pdf"], accept_multiple_files=True)
    if uploads and st.button("🤖 Leggi file"):
        drafts = []
        for file in uploads:
            try:
                with st.spinner(f"Lettura {file.name}..."):
                    drafts.append(extract_file(file))
            except Exception as exc:
                st.error(f"{file.name}: {exc}")
        if drafts:
            st.session_state["drafts"] = drafts
    drafts = st.session_state.get("drafts", [])
    if drafts:
        index = st.selectbox("Partita da controllare", range(len(drafts)), format_func=lambda i: f"{i+1} — {drafts[i].get('match_name') or 'Senza nome'}")
        data = editor_form(drafts[index], f"draft_{index}")
        if drafts[index].get("_ocr_text"):
            with st.expander("Testo letto dall’OCR"):
                st.text(drafts[index]["_ocr_text"])
        drafts[index] = data
        st.session_state["drafts"] = drafts
        if st.button("✅ Salva questa partita", type="primary"):
            data["id"] = next_id()
            save_record(data)
            drafts.pop(index)
            st.session_state["drafts"] = drafts
            st.success(f"Salvata con ID {data['id']}")
            st.rerun()
    elif not uploads:
        st.info("Carica almeno un file. La lettura automatica richiede OPENAI_API_KEY.")

elif page == "📋 Database":
    st.subheader("📋 Tutte le partite")
    df = get_matches()
    if df.empty:
        st.info("Nessuna partita salvata.")
    else:
        search = st.text_input("Cerca ID, partita o campionato")
        show = df.copy()
        if search:
            mask = (
                show["id"].astype(str).str.contains(search, case=False, na=False) |
                show["match_name"].astype(str).str.contains(search, case=False, na=False) |
                show["league"].astype(str).str.contains(search, case=False, na=False)
            )
            show = show[mask]
        show["Esito"] = show["outcome"].map({"V": "🟢 V", "P": "🔴 P"}).fillna("⏳ In attesa")
        show["Risultato"] = show["final_score"].fillna("")
        show["Quota"] = pd.to_numeric(show["current_odds"], errors="coerce").round(2)
        show["Profitto €"] = pd.to_numeric(show["profit"], errors="coerce")

        preferred = [
            "id", "date", "time", "league", "match_name",
            "Quota", "Esito", "Risultato", "Profitto €"
        ]
        remaining = [col for col in show.columns if col not in preferred]
        st.dataframe(
            show[preferred + remaining],
            use_container_width=True,
            hide_index=True,
        )

elif page == "🏆 Aggiorna risultato":
    st.subheader("🏆 Aggiorna risultato")
    df = get_matches()
    open_df = df[~df["outcome"].isin(["V","P"])] if not df.empty else df
    if open_df.empty:
        st.info("Nessuna partita aperta.")
    else:
        labels = {f'{r["id"]} — {r["match_name"]}':r["id"] for _,r in open_df.iterrows()}
        selected = st.selectbox("Partita", list(labels))
        score = st.text_input("Risultato finale", placeholder="es. 2-0")
        outcome = st.radio("Esito", ["V","P"], horizontal=True)
        if st.button("💾 Aggiorna", type="primary"):
            mid = labels[selected]
            row = open_df[open_df["id"]==mid].iloc[0]
            stake = float(row["stake"] or 20)
            odds = float(row["played_odds"] or 0)
            gross = round(stake*odds,2) if outcome=="V" else 0.0
            profit = round(gross-stake,2) if outcome=="V" else -stake
            update_record(mid, {"outcome":outcome,"final_score":score,"gross_return":gross,"profit":profit})
            st.success("Risultato e profitto aggiornati.")
            st.rerun()

elif page == "✏️ Modifica/Elimina":
    st.subheader("✏️ Modifica o elimina")
    df = get_matches()

    if df.empty:
        st.info("Nessuna partita.")
    else:
        labels = {
            f'{r["id"]} — {r["match_name"]}': r["id"]
            for _, r in df.iterrows()
        }

        selected = st.selectbox("Scegli partita", list(labels))
        mid = labels[selected]
        record = df[df["id"] == mid].iloc[0].to_dict()

        edited = editor_form(record, f"edit_{mid}")

        st.markdown("### 🏁 Esito e risultato")

        current_outcome = str(record.get("outcome") or "")
        outcome_options = ["", "V", "P"]
        outcome_index = (
            outcome_options.index(current_outcome)
            if current_outcome in outcome_options
            else 0
        )

        edited_outcome = st.selectbox(
            "Esito finale",
            outcome_options,
            index=outcome_index,
            format_func=lambda x: {
                "": "⏳ In attesa",
                "V": "🟢 Vinta",
                "P": "🔴 Persa",
            }.get(x, x),
            key=f"edit_{mid}_outcome",
        )

        edited_score = st.text_input(
            "Risultato finale",
            value=str(record.get("final_score") or ""),
            key=f"edit_{mid}_final_score",
        )

        stake_value = float(
            edited.get("stake")
            or record.get("stake")
            or 20.0
        )
        odds_value = float(
            edited.get("played_odds")
            or record.get("played_odds")
            or 0.0
        )

        if edited_outcome == "V":
            calculated_gross = round(stake_value * odds_value, 2)
            calculated_profit = round(
                calculated_gross - stake_value,
                2,
            )
        elif edited_outcome == "P":
            calculated_gross = 0.0
            calculated_profit = round(-stake_value, 2)
        else:
            calculated_gross = None
            calculated_profit = None

        if edited_outcome in {"V", "P"}:
            st.info(
                f"Ricalcolo automatico → "
                f"Ritorno lordo: € {calculated_gross:.2f} • "
                f"Profitto: € {calculated_profit:.2f}"
            )
        else:
            st.caption(
                "Se imposti la partita come in attesa, "
                "ritorno lordo e profitto verranno svuotati."
            )

        c1, c2 = st.columns(2)

        if c1.button("💾 Salva modifiche", type="primary"):
            # Per correggere un esito aggiorniamo SOLO i campi necessari.
            # In questo modo Supabase non deve validare nuovamente tutta
            # la riga e non può bloccarsi per un altro campo preesistente.
            result_update = {
                "outcome": edited_outcome if edited_outcome else None,
                "final_score": (
                    edited_score.strip()
                    if edited_score.strip()
                    else None
                ),
                "gross_return": calculated_gross,
                "profit": calculated_profit,
            }

            update_record(
                mid,
                result_update,
            )

            st.success(
                "✅ Esito, risultato e profitto corretti."
            )
            st.rerun()

        if c2.button("🗑️ Elimina partita"):
            delete_record(mid)
            st.success("Partita eliminata.")
            st.rerun()

elif page == "📊 Dashboard":
    st.subheader("📊 Dashboard")

    df = get_matches()
    s = summary(df)

    row1 = st.columns(4)
    row1[0].metric("Partite", s["total"])
    row1[1].metric("Concluse", s["closed"])
    row1[2].metric("🟢 Vinte", s["wins"])
    row1[3].metric("🔴 Perse", s["losses"])

    row2 = st.columns(4)
    row2[0].metric("Win rate", f'{s["win_rate"]:.2f}%')
    row2[1].metric("💰 Profitto", f'€ {s["profit"]:.2f}')
    row2[2].metric("📈 ROI", f'{s["roi"]:.2f}%')
    row2[3].metric("Quota media", f'{s["avg_odds"]:.2f}')

    closed = (
        df[df["outcome"].isin(["V", "P"])].copy()
        if not df.empty
        else df
    )

    if not closed.empty:
        closed["_date_sort"] = pd.to_datetime(
            closed["date"],
            errors="coerce"
        )
        closed["_time_sort"] = closed["time"].fillna("").astype(str)
        closed["_id_sort"] = closed["id"].astype(str)

        closed = closed.sort_values(
            ["_date_sort", "_time_sort", "_id_sort"],
            ascending=[True, True, True],
            na_position="last",
        ).copy()

        closed["profit"] = pd.to_numeric(
            closed["profit"],
            errors="coerce"
        ).fillna(0.0)

        closed["profitto_cumulato"] = (
            closed["profit"].cumsum().round(2)
        )

        closed["Giocata"] = range(1, len(closed) + 1)

        # Media cumulativa della curva del profitto:
        # ogni punto è la media di tutti i punti del profitto cumulato
        # fino a quella giocata.
        closed["Media cumulativa"] = (
            closed["profitto_cumulato"]
            .expanding()
            .mean()
            .round(2)
        )

        st.markdown("### 📈 Andamento profitto cumulato")

        st.line_chart(
            closed.set_index("Giocata")[
                ["profitto_cumulato", "Media cumulativa"]
            ]
        )

        ultimo_profitto = float(
            closed["profitto_cumulato"].iloc[-1]
        )
        profitto_dashboard = round(float(s["profit"]), 2)

        if round(ultimo_profitto, 2) != profitto_dashboard:
            st.error(
                "⚠️ Controllo grafico: il profitto finale del grafico "
                f"({ultimo_profitto:.2f} €) non coincide con quello della "
                f"Dashboard ({profitto_dashboard:.2f} €)."
            )
        else:
            st.caption(
                f"✅ Ultimo punto del grafico = profitto totale: "
                f"{profitto_dashboard:.2f} €"
            )

elif page == "🔎 Analisi filtri":
    st.subheader("🔎 Analisi filtri")
    df = get_matches()
    if df.empty:
        st.info("Nessun dato.")
    else:
        c1,c2,c3 = st.columns(3)
        allb = c1.multiselect("Allibramento", sorted(df["allibramento_color"].dropna().astype(str).unique()))
        scl = c2.multiselect("Scala", sorted(df["scl"].dropna().astype(str).unique()))
        mtr = c3.multiselect("Metrica", sorted(df["mtr"].dropna().astype(str).unique()))
        methods = st.multiselect("Metodi associati", list(METHOD_COLUMNS))
        filtered = df.copy()
        if allb: filtered = filtered[filtered["allibramento_color"].isin(allb)]
        if scl: filtered = filtered[filtered["scl"].isin(scl)]
        if mtr: filtered = filtered[filtered["mtr"].isin(mtr)]
        for method in methods:
            filtered = filtered[filtered[METHOD_COLUMNS[method]]==1]
        s = summary(filtered)
        st.dataframe(pd.DataFrame([{
            "Partite":s["total"],"Concluse":s["closed"],"Vinte":s["wins"],"Perse":s["losses"],
            "Win rate %":round(s["win_rate"],2),"Quota media":round(s["avg_odds"],2),
            "Profitto €":round(s["profit"],2),"ROI %":round(s["roi"],2)
        }]), hide_index=True, use_container_width=True)
        st.dataframe(filtered, hide_index=True, use_container_width=True)


elif page == "🧪 Laboratorio Strategie":
    st.subheader("🧪 Laboratorio Strategie")
    st.caption("Combina più condizioni e verifica profitto, ROI e partite selezionate.")

    df = get_matches()
    closed = df[df["outcome"].isin(["V", "P"])].copy() if not df.empty else df

    if closed.empty:
        st.info("Servono partite concluse per analizzare una strategia.")
    else:
        def opts(column):
            values = closed[column].dropna().astype(str).str.strip()
            return sorted(v for v in values.unique() if v and v.lower() not in {"nan", "none"})

        st.markdown("### Filtri")
        tab1, tab2, tab3 = st.tabs(["Indicatori", "Quota e probabilità", "Contesto"])

        with tab1:
            c1, c2, c3 = st.columns(3)
            allb = c1.multiselect("Allibramento", opts("allibramento_color"))
            mtr = c2.multiselect("MTR", opts("mtr"))
            scl = c3.multiselect("SCL", opts("scl"))

            c4, c5, c6 = st.columns(3)
            cal = c4.multiselect("CAL", opts("cal"))
            caff = c5.multiselect("C. AFF.", opts("c_aff"))
            flbk = c6.multiselect("FLBK", opts("flbk"))

            c7, c8, c9 = st.columns(3)
            cfb = c7.multiselect("C. FB.", opts("c_fb"))
            qra = c8.multiselect("QRA/QA", opts("qra_qa"))
            qi = c9.multiselect("QI/QA", opts("qi_qa"))

            st.markdown("#### Numero comparazioni C. AFF.")

            caff_count_values = pd.to_numeric(
                closed["c_aff_count"],
                errors="coerce"
            ).dropna()

            use_caff_count = st.checkbox(
                "Usa filtro C.AFF. COUNT",
                value=False,
                key="lab_use_caff_count",
            )

            if caff_count_values.empty:
                st.info(
                    "Nel database non ci sono ancora valori C.AFF. COUNT "
                    "utilizzabili per le partite concluse."
                )
                min_caff_count = 0
                max_caff_count = 0
            else:
                cc1, cc2 = st.columns(2)

                min_caff_count = cc1.number_input(
                    "C.AFF. COUNT minimo",
                    min_value=0,
                    value=int(caff_count_values.min()),
                    step=1,
                    key="lab_caff_count_min",
                )

                max_caff_count = cc2.number_input(
                    "C.AFF. COUNT massimo",
                    min_value=0,
                    value=int(caff_count_values.max()),
                    step=1,
                    key="lab_caff_count_max",
                )

        with tab2:
            odds = pd.to_numeric(closed["current_odds"], errors="coerce").dropna()
            probs = pd.to_numeric(closed["prob_1"], errors="coerce").dropna()

            o1, o2 = st.columns(2)
            min_odds = o1.number_input("Quota minima", value=float(odds.min()) if not odds.empty else 1.0, step=0.01)
            max_odds = o2.number_input("Quota massima", value=float(odds.max()) if not odds.empty else 5.0, step=0.01)

            p1, p2 = st.columns(2)
            min_prob = p1.number_input("Probabilità 1 minima", value=float(probs.min()) if not probs.empty else 0.0, step=0.5)
            max_prob = p2.number_input("Probabilità 1 massima", value=float(probs.max()) if not probs.empty else 100.0, step=0.5)

        with tab3:
            leagues = st.multiselect("Campionati", opts("league"))

        filters = {
            "allibramento_color": allb,
            "mtr": mtr,
            "scl": scl,
            "cal": cal,
            "c_aff": caff,
            "flbk": flbk,
            "c_fb": cfb,
            "qra_qa": qra,
            "qi_qa": qi,
            "league": leagues,
        }

        filtered = apply_strategy_filters(
            closed, filters, min_odds, max_odds, min_prob, max_prob
        )

        if use_caff_count and not caff_count_values.empty:
            caff_count_numeric = pd.to_numeric(
                filtered["c_aff_count"],
                errors="coerce"
            )

            filtered = filtered[
                (caff_count_numeric >= min_caff_count)
                & (caff_count_numeric <= max_caff_count)
            ]

        s = summary(filtered)

        st.markdown("### Risultato")
        a, b, c, d = st.columns(4)
        a.metric("Partite", s["closed"])
        b.metric("🟢 Vinte", s["wins"])
        c.metric("🔴 Perse", s["losses"])
        d.metric("Win rate", f'{s["win_rate"]:.2f}%')

        e, f, g, h = st.columns(4)
        e.metric("Puntato", f'€ {s["staked"]:.2f}')
        f.metric("Profitto", f'€ {s["profit"]:.2f}')
        g.metric("ROI", f'{s["roi"]:.2f}%')
        h.metric("Quota media", f'{s["avg_odds"]:.2f}')

        if filtered.empty:
            st.warning("Nessuna partita rispetta tutti i filtri selezionati.")
        else:
            detail = filtered.copy()
            detail["Esito"] = detail["outcome"].map({"V": "🟢 V", "P": "🔴 P"})
            detail["Quota"] = pd.to_numeric(detail["current_odds"], errors="coerce").round(2)
            detail["Prob. 1"] = pd.to_numeric(detail["prob_1"], errors="coerce").round(1)
            detail["Profitto €"] = pd.to_numeric(detail["profit"], errors="coerce").round(2)

            shown = detail[[
                "date", "time", "league", "match_name", "Quota", "Prob. 1",
                "c_aff", "c_aff_count",
                "allibramento_color", "mtr", "scl", "cal", "Esito",
                "final_score", "Profitto €"
            ]].rename(columns={
                "date": "Data",
                "time": "Ora",
                "league": "Campionato",
                "match_name": "Partita",
                "c_aff": "C.AFF.",
                "c_aff_count": "C.AFF. COUNT",
                "allibramento_color": "ALLB",
                "mtr": "MTR",
                "scl": "SCL",
                "cal": "CAL",
                "final_score": "Risultato"
            })

            st.markdown("### Partite selezionate")
            st.dataframe(shown, use_container_width=True, hide_index=True)

            curve = detail.sort_values(["date", "time", "id"]).copy()
            curve["Profitto cumulato"] = pd.to_numeric(
                curve["profit"], errors="coerce"
            ).fillna(0).cumsum()
            curve["Progressivo"] = range(1, len(curve) + 1)
            st.markdown("### Andamento del profitto")
            st.line_chart(curve.set_index("Progressivo")["Profitto cumulato"])


elif page == "🧠 Trova metodo migliore":
    st.subheader("🧠 Motore Strategie V3.1 - Ottimizzato")
    st.caption(
        "Cerca combinazioni tra indicatori, range quota, movimento quota, "
        "value rispetto alla quota reale e allibramento. Confronta ogni "
        "strategia con la base 'gioco tutte' e ne verifica la stabilità nel tempo."
    )

    df = get_matches()
    closed = df[df["outcome"].isin(["V", "P"])].copy() if not df.empty else df

    if closed.empty:
        st.info("Servono partite concluse per cercare strategie.")
    else:
        s1, s2, s3, s4 = st.columns(4)

        default_min = min(10, max(1, len(closed)))
        min_sample = s1.number_input(
            "Campione minimo",
            min_value=1,
            max_value=max(1, len(closed)),
            value=default_min,
            step=1,
        )

        max_filters = s2.selectbox(
            "Filtri massimi",
            [1, 2, 3],
            index=2,
        )

        top_n = s3.selectbox(
            "Strategie da mostrare",
            [20, 50, 100],
            index=1,
        )

        validation_pct = s4.selectbox(
            "Quota dati per verifica",
            [20, 30, 40],
            index=1,
        )

        if len(closed) < 30:
            st.warning(
                f"Hai solo {len(closed)} partite concluse: la classifica è ancora "
                "esplorativa. La validazione diventerà più significativa con più dati."
            )
        else:
            st.info(
                "Il motore usa la parte iniziale dello storico per individuare "
                "la strategia e la parte più recente per verificarla."
            )

        st.markdown("### 📊 Analisi range quote")
        st.caption(
            "Qui vedi chiaramente come rendono le diverse fasce di quota da sole. "
            "Gli stessi range vengono anche combinati automaticamente con gli altri filtri."
        )

        odds_table = odds_range_analysis(closed)
        if not odds_table.empty:
            st.dataframe(
                odds_table,
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("Vedi tutti i range quota provati dal motore"):
            st.write(
                ", ".join(
                    f"{low:.2f}+" if high >= 99 else f"{low:.2f}–{high:.2f}"
                    for low, high in STRATEGY_ODDS_RANGES
                )
            )

        current_engine_signature = strategy_engine_signature(
            closed,
            min_sample=int(min_sample),
            max_filters=int(max_filters),
            top_n=int(top_n),
            validation_ratio=float(validation_pct) / 100.0,
        )

        previous_engine_signature = st.session_state.get(
            "strategy_engine_signature_v3"
        )

        existing_ranking = st.session_state.get(
            "strategy_ranking_v2"
        )

        database_changed = (
            isinstance(existing_ranking, pd.DataFrame)
            and not existing_ranking.empty
            and previous_engine_signature
            and previous_engine_signature
            != current_engine_signature
        )

        manual_search = st.button(
            "🔍 Cerca migliori strategie",
            type="primary",
            use_container_width=True,
        )

        if manual_search or database_changed:
            spinner_text = (
                "Dati cambiati: aggiorno automaticamente strategie e statistiche..."
                if database_changed and not manual_search
                else "Analizzo le combinazioni..."
            )

            with st.spinner(spinner_text):
                ranking, selections = automatic_strategy_search(
                    closed,
                    min_sample=int(min_sample),
                    max_filters=int(max_filters),
                    top_n=int(top_n),
                    validation_ratio=float(validation_pct) / 100.0,
                )

                st.session_state[
                    "strategy_ranking_v2"
                ] = ranking

                st.session_state[
                    "strategy_selections_v2"
                ] = selections

                st.session_state[
                    "strategy_engine_signature_v3"
                ] = current_engine_signature

                history_result = save_strategy_snapshot(
                    ranking
                )

                st.session_state[
                    "strategy_history_result"
                ] = history_result

            if database_changed and not manual_search:
                st.success(
                    "🔄 Classifica aggiornata automaticamente: "
                    "il database o i parametri del motore sono cambiati."
                )

        ranking = st.session_state.get(
            "strategy_ranking_v2"
        )

        selections = st.session_state.get(
            "strategy_selections_v2",
            {},
        )

        if isinstance(ranking, pd.DataFrame) and not ranking.empty:
            with st.expander(
                "🔧 Dettagli tecnici e classifiche complete",
                expanded=False,
            ):
                st.markdown("### 🧪 Classifica completa del motore")

                display_ranking = ranking.drop(columns=["ID"]).copy()
                st.dataframe(
                    display_ranking,
                    use_container_width=True,
                    hide_index=True,
                )

                st.caption(
                    "✅ = ROI positivo sia nella parte di ricerca sia nella parte "
                    "più recente usata per la verifica. ⚠️ = da considerare esplorativa."
                )
                st.caption(
                    "Profitto / 100€ mostra quanto rende la strategia ogni 100 € "
                    "complessivamente puntati: è utile per confrontare strategie "
                    "con profitti simili ma capitale impiegato diverso."
                )
                st.caption(
                    "Δ ROI vs base mostra quanto il filtro migliora o peggiora rispetto "
                    "a giocare tutte le partite. Stabilità controlla il rendimento in "
                    "3 blocchi cronologici separati."
                )

                history_result = st.session_state.get("strategy_history_result")

                if isinstance(history_result, dict):
                    if not history_result.get("ok"):
                        st.warning(
                            "📚 Storico classifica non ancora attivo: "
                            + history_result.get("message", "")
                        )
                    elif history_result.get("saved"):
                        st.success("📚 " + history_result.get("message", ""))

                # Sincronizza lo storico con la classifica realmente
                # visualizzata prima di calcolare Top 5 / Top 10.
                sync_history_result = ensure_current_strategy_snapshot(
                    ranking
                )

                if not sync_history_result.get("ok"):
                    st.warning(
                        "⚠️ Impossibile sincronizzare lo storico: "
                        + sync_history_result.get("message", "")
                    )

                history_table, snapshot_count = strategy_history_summary(
                    ranking["Strategia"].tolist(),
                    current_ranking=ranking,
                )

                try:
                    _all_history_rows = fetch_all_strategy_history_rows()
                    _real_history_rows = [
                        r for r in _all_history_rows
                        if not str(r.get("strategy") or "").startswith(
                            SYSTEM_STATE_PREFIX
                        )
                    ]
                    st.caption(
                        f"📚 Storico caricato: {len(_real_history_rows)} righe reali "
                        f"su {snapshot_count} snapshot distinti."
                    )
                except Exception:
                    pass

                st.markdown("### 🧭 Stabilità della classifica")

                if snapshot_count == 0:
                    st.info(
                        "Lo storico partirà dal primo snapshot salvato. "
                        "Dopo alcuni cambiamenti della classifica inizierai "
                        "a vedere quali strategie rimangono davvero in alto."
                    )
                else:
                    st.caption(
                        f"Snapshot distinti salvati: {snapshot_count}. "
                        "Top 5 % indica in quanti snapshot, dalla prima "
                        "comparsa della strategia, è rimasta nelle prime 5. "
                        "La classifica viene salvata solo quando cambia, "
                        "quindi premere il tasto più volte non altera i dati."
                    )

                    if snapshot_count < 5:
                        st.warning(
                            "Lo storico è ancora molto giovane: con meno "
                            "di 5 snapshot le percentuali sono solo indicative."
                        )

                    if not history_table.empty:
                        current_positions = {
                            str(row["Strategia"]): pos
                            for pos, (_, row) in enumerate(
                                ranking.iterrows(),
                                start=1,
                            )
                        }

                        mismatch_count = 0
                        for _, hist_row in history_table.iterrows():
                            strategy_name = str(hist_row.get("Strategia", ""))
                            current_pos = current_positions.get(strategy_name)
                            last_pos = hist_row.get("Ultima posizione")
                            if (
                                current_pos is not None
                                and pd.notna(last_pos)
                                and int(last_pos) != int(current_pos)
                            ):
                                mismatch_count += 1

                        if mismatch_count > 0:
                            st.error(
                                f"❌ Controllo storico: {mismatch_count} strategie "
                                "non risultano allineate nemmeno dopo il recupero "
                                "dello snapshot corrente."
                            )
                        else:
                            virtual_used = (
                                "_virtual_current" in history_table.columns
                                and history_table["_virtual_current"]
                                .fillna(False)
                                .astype(bool)
                                .any()
                            )

                            if virtual_used:
                                st.info(
                                    "✅ Classifica corrente allineata ai calcoli. "
                                    "Lo snapshot corrente è stato aggiunto in memoria "
                                    "senza modificare né cancellare lo storico salvato."
                                )
                            else:
                                st.success(
                                    "✅ Storico salvato e classifica corrente sincronizzati."
                                )

                        history_display = history_table.drop(
                            columns=["_virtual_current"],
                            errors="ignore",
                        )

                        st.dataframe(
                            history_display.head(20),
                            use_container_width=True,
                            hide_index=True,
                        )

                st.markdown("### 🏆 Classifica Definitiva")
                st.caption(
                    "Questa è la classifica da guardare: combina automaticamente rendimento attuale, "
                    "campione, validazione, stabilità e storico. Il peso dello storico aumenta "
                    "automaticamente man mano che crescono le rilevazioni."
                )

                definitive = definitive_strategy_ranking(
                    ranking,
                    history_table,
                    snapshot_count,
                    selections,
                )

                if not definitive.empty:
                    winner = definitive.iloc[0]
                    st.success(
                        f'🥇 Strategia consigliata adesso: {winner["Strategia"]} '
                        f'— punteggio definitivo {winner["Punteggio definitivo"]:.2f} '
                        f'— {winner["Stato"]}'
                    )
                    duplicates_removed = int(
                        pd.to_numeric(
                            definitive["Duplicati rimossi"],
                            errors="coerce",
                        ).fillna(0).sum()
                    ) if "Duplicati rimossi" in definitive.columns else 0

                    if duplicates_removed > 0:
                        st.caption(
                            f"🧹 Strategie equivalenti eliminate automaticamente: "
                            f"{duplicates_removed}. Ogni riga rimasta seleziona "
                            f"un gruppo realmente diverso di partite."
                        )

                    st.dataframe(
                        definitive,
                        use_container_width=True,
                        hide_index=True,
                    )

                    if snapshot_count < 5:
                        st.info(
                            "La prima posizione è già calcolata automaticamente, ma lo storico è ancora giovane. "
                            "Il peso della stabilità crescerà automaticamente fino a 10 snapshot."
                        )

                st.markdown("### 🎯 Strategia ufficiale da seguire")

                follow_state, follow_message = update_strategy_follow_state(
                    definitive
                )
                official_row = strategy_follow_display_row(
                    definitive,
                    follow_state,
                    closed=closed,
                )

                if follow_state and official_row is not None:
                    consolidated = bool(
                        follow_state.get("official_consolidated", False)
                    )
                    official_currently_usable = (
                        int(official_row.get("Partite", 0) or 0) >= ELITE_MIN_MATCHES
                        and float(official_row.get("ROI %", 0) or 0) > 0
                        and float(official_row.get("Profitto €", 0) or 0) > 0
                    )
                    if consolidated and official_currently_usable:
                        official_badge = "🟢 CONFERMATA"
                        st.success(
                            f'🎯 {official_badge} — '
                            f'{human_strategy_name(follow_state.get("official", ""))}'
                        )
                    else:
                        official_badge = "⚠️ PRECEDENTE UFFICIALE / DA RIVALUTARE"
                        st.warning(
                            f'🎯 {official_badge} — '
                            f'{human_strategy_name(follow_state.get("official", ""))}'
                        )

                    o1, o2, o3, o4 = st.columns(4)
                    o1.metric(
                        "Punteggio",
                        f'{float(official_row.get("Punteggio definitivo", 0) or 0):.2f}',
                    )
                    o2.metric(
                        "Partite",
                        int(official_row.get("Partite", 0) or 0),
                    )
                    o3.metric(
                        "ROI",
                        f'{float(official_row.get("ROI %", 0) or 0):.2f}%',
                    )
                    o4.metric(
                        "Profitto",
                        f'€ {float(official_row.get("Profitto €", 0) or 0):.2f}',
                    )

                    st.caption(
                        "🔄 Partite, ROI e profitto della strategia ufficiale "
                        "sono ricalcolati in tempo reale sul database attuale "
                        "con gli stessi identici filtri usati dal motore."
                    )

                    # Stato operativo: separato dalla scelta della strategia ufficiale.
                    official_name_now = str(
                        follow_state.get("official") or ""
                    )
                    official_selected_now = (
                        apply_generated_strategy_name(
                            closed,
                            official_name_now,
                        )
                        if official_name_now
                        else pd.DataFrame()
                    )
                    health = official_operational_status(
                        official_selected_now
                    )

                    st.markdown("#### 🚦 Stato operativo dell'ufficiale")

                    if health["status"].startswith("🟢"):
                        st.success(
                            "🟢 SEGUI — andamento recente compatibile "
                            "con una strategia ancora sana."
                        )
                    elif health["status"].startswith("🟡"):
                        st.warning(
                            "🟡 SEGUI CON CAUTELA — la strategia resta "
                            "ufficiale, ma l'andamento recente è deteriorato."
                        )
                    elif health["status"].startswith("🔴"):
                        st.error(
                            "🔴 SOSPENDI — deterioramento forte e persistente. "
                            "Non giocare nuove selezioni dell'ufficiale finché "
                            "lo stato operativo non migliora."
                        )
                    else:
                        st.info("⚪ Dati insufficienti per lo stato operativo.")

                    h1, h2, h3, h4 = st.columns(4)
                    h1.metric("ROI ultime 20", f'{health["roi20"]:.2f}%')
                    h2.metric("ROI ultime 50", f'{health["roi50"]:.2f}%')
                    h3.metric(
                        "Drawdown dal massimo",
                        f'€ {health["drawdown_eur"]:.2f}',
                    )
                    h4.metric(
                        "Drawdown %",
                        f'{health["drawdown_pct"]:.1f}%',
                    )

                    with st.expander("ℹ️ Come viene deciso lo stato operativo"):
                        st.write(
                            "🟢 SEGUI: ROI recente non negativo e drawdown sotto il 35%."
                        )
                        st.write(
                            "🟡 SEGUI CON CAUTELA: almeno uno tra ROI ultime 20, "
                            "ROI ultime 50 o drawdown segnala deterioramento."
                        )
                        st.write(
                            "🔴 SOSPENDI: con almeno 50 partite servono insieme "
                            "ROI ultime 20 ≤ -10%, ROI ultime 50 ≤ -5% e "
                            "drawdown dal massimo ≥ 50%."
                        )
                        st.caption(
                            "Lo stato operativo NON sostituisce automaticamente "
                            "la strategia ufficiale e NON modifica lo storico."
                        )

                    challenger = str(follow_state.get("challenger") or "")
                    streak = int(
                        follow_state.get("challenger_streak", 0) or 0
                    )

                    if challenger:
                        st.warning(
                            f"🧪 Miglior sfidante: {challenger} — "
                            f"{streak}/{CHALLENGER_REQUIRED_STREAK} "
                            f"conferme consecutive. NON cambiare ancora strategia."
                        )
                    else:
                        st.info(
                            "✅ Nessuno sfidante ha ancora i requisiti "
                            "per chiedere un cambio."
                        )

                    st.caption(follow_message)
                    st.caption(
                        "Il cambio automatico avviene solo quando uno "
                        "sfidante sufficientemente solido supera l'ufficiale "
                        f"di almeno {CHALLENGER_MIN_SCORE_ADVANTAGE:.0f} punti "
                        f"per {CHALLENGER_REQUIRED_STREAK} rilevazioni consecutive."
                    )

                st.markdown("### 🏆 Classifica Elite — quella da guardare")
                st.caption(
                    "Questa è la classifica decisionale: mostra solo strategie "
                    "già abbastanza solide. Le provvisorie sono escluse, i duplicati "
                    "sono rimossi e il confronto recente usa le ultime "
                    f"{ELITE_RECENT_WINDOW} partite di ciascuna strategia."
                )

                elite_table = build_elite_ranking(
                    ranking=ranking,
                    history_table=history_table,
                    snapshot_count=snapshot_count,
                    selections=selections,
                    closed=closed,
                    official_state=follow_state,
                )

                if elite_table.empty:
                    st.info(
                        "Nessuna strategia ha ancora tutti i requisiti Elite. "
                        "La strategia ufficiale resta comunque monitorata."
                    )
                else:
                    decision_message = elite_decision_message(
                        elite_table,
                        follow_state,
                    )

                    if decision_message.startswith("✅"):
                        st.success(decision_message)
                    else:
                        st.info(decision_message)

                    elite_display = add_human_strategy_column(elite_table)
                    st.dataframe(
                        elite_display,
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.caption(
                        f"Requisiti base Elite: almeno {ELITE_MIN_MATCHES} partite, "
                        f"{ELITE_MIN_OBSERVATIONS} rilevazioni, validazione ✅ "
                        "e stabilità almeno 66,7%. "
                        f"Vengono mostrate al massimo {ELITE_MAX_ROWS} strategie."
                    )

                st.markdown("### 👀 Strategie in osservazione forte — poche ma serie")
                st.caption(
                    "Solo strategie già mature e profittevoli che non sono ancora Elite. "
                    "Le strategie nuove o con pochi dati continuano a essere studiate dal motore, "
                    "ma non vengono mostrate qui."
                )

                strong_watch = build_strong_watchlist(
                    ranking, history_table, snapshot_count, selections, closed, elite_table
                )

                if strong_watch.empty:
                    st.info("Nessun'altra strategia abbastanza consolidata da mostrare adesso.")
                else:
                    strong_watch_display = add_human_strategy_column(strong_watch)
                    st.dataframe(
                        strong_watch_display,
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption(
                        f"Minimo {WATCH_MIN_MATCHES} partite, {WATCH_MIN_OBSERVATIONS} rilevazioni, "
                        f"ROI e profitto positivi, validazione ✅ e stabilità almeno "
                        f"{WATCH_MIN_STABILITY:.1f}%. Massimo {WATCH_MAX_ROWS} strategie."
                    )

            st.markdown("### 🧪 Laboratorio / dettagli")
            with st.expander(
                "Apri classifica completa, strategie provvisorie e dettagli",
                expanded=False,
            ):
                st.caption(
                    "Questa parte serve per ricerca e sperimentazione. "
                    "Per decidere cosa seguire usa la Classifica Elite sopra."
                )

            st.markdown("### 🔎 Apri una strategia")
            strategy_map = {
                f'{row["Strategia"]} | '
                f'ROI {row["ROI %"]:.2f}% | '
                f'€ {row["Profitto €"]:.2f} | '
                f'{int(row["Partite"])} partite': row["ID"]
                for _, row in ranking.iterrows()
            }

            selected_label = st.selectbox(
                "Strategia",
                list(strategy_map.keys()),
                key="strategy_detail_v2",
            )
            strategy_id = strategy_map[selected_label]
            selected_indices = selections.get(strategy_id, [])
            selected_df = closed.loc[
                closed.index.intersection(selected_indices)
            ].copy()

            if not selected_df.empty:
                stats = strategy_statistics(selected_df)

                a, b, c, d = st.columns(4)
                a.metric("Partite", stats["closed"])
                b.metric("🟢 Vinte", stats["wins"])
                c.metric("🔴 Perse", stats["losses"])
                d.metric("Win rate", f'{stats["win_rate"]:.2f}%')

                e, f, g, h = st.columns(4)
                e.metric("Profitto", f'€ {stats["profit"]:.2f}')
                f.metric("ROI", f'{stats["roi"]:.2f}%')
                g.metric("Quota media", f'{stats["avg_odds"]:.2f}')
                h.metric(
                    "Max perdite consecutive",
                    stats["max_losing_streak"],
                )

                detail = selected_df.sort_values(
                    ["date", "time", "id"]
                ).copy()
                detail["Esito"] = detail["outcome"].map(
                    {"V": "🟢 V", "P": "🔴 P"}
                )
                detail["Quota"] = pd.to_numeric(
                    detail["current_odds"], errors="coerce"
                ).round(2)
                detail["Profitto €"] = pd.to_numeric(
                    detail["profit"], errors="coerce"
                ).round(2)

                shown = detail[[
                    "date", "time", "league", "match_name",
                    "Quota", "allibramento_color", "mtr",
                    "scl", "cal", "Esito", "final_score",
                    "Profitto €"
                ]].rename(columns={
                    "date": "Data",
                    "time": "Ora",
                    "league": "Campionato",
                    "match_name": "Partita",
                    "allibramento_color": "ALLB",
                    "mtr": "MTR",
                    "scl": "SCL",
                    "cal": "CAL",
                    "final_score": "Risultato",
                })

                st.dataframe(
                    shown,
                    use_container_width=True,
                    hide_index=True,
                )

                curve = detail.copy()
                curve["Profitto cumulato"] = pd.to_numeric(
                    curve["profit"], errors="coerce"
                ).fillna(0).cumsum()
                curve["Progressivo"] = range(1, len(curve) + 1)

                st.markdown("### 📈 Profitto cumulato")
                st.line_chart(
                    curve.set_index("Progressivo")["Profitto cumulato"]
                )

            st.markdown("### ⚖️ Confronta fino a 3 strategie")
            comparison_options = list(strategy_map.keys())
            chosen = st.multiselect(
                "Seleziona strategie",
                comparison_options,
                max_selections=3,
                key="strategy_compare_v2",
            )

            if chosen:
                compare_rows = []
                for label in chosen:
                    sid = strategy_map[label]
                    idx = selections.get(sid, [])
                    sdf = closed.loc[closed.index.intersection(idx)].copy()
                    ss = strategy_statistics(sdf)
                    compare_rows.append({
                        "Strategia": label.split(" | ")[0],
                        "Partite": ss["closed"],
                        "Vinte": ss["wins"],
                        "Perse": ss["losses"],
                        "Win rate %": round(ss["win_rate"], 2),
                        "Puntato €": round(ss["staked"], 2),
                        "Profitto €": round(ss["profit"], 2),
                        "ROI %": round(ss["roi"], 2),
                        "Profitto / 100€": round(
                            (ss["profit"] / ss["staked"] * 100)
                            if ss["staked"] else 0,
                            2,
                        ),
                        "Quota media": round(ss["avg_odds"], 2),
                        "Max perdite consecutive": ss["max_losing_streak"],
                    })

                st.dataframe(
                    pd.DataFrame(compare_rows),
                    use_container_width=True,
                    hide_index=True,
                )

        elif ranking is not None:
            st.info(
                "Nessuna strategia supera il campione minimo scelto. "
                "Per testare il funzionamento puoi abbassarlo temporaneamente."
            )

elif page == "📥 Importa/Esporta":
    st.subheader("📥 Importa / Esporta")
    uploaded_excel = st.file_uploader("Importa database Excel", type=["xlsx"])
    if uploaded_excel and st.button("Importa Excel"):
        imported = pd.read_excel(uploaded_excel, sheet_name=0)
        rename = {
            "ID":"id","Data":"date","Ora":"time","Campionato":"league","Partita":"match_name",
            "Giornata/Fase":"round_name","Mercato":"market","Esito giocato":"pick",
            "Scelto da Ale":"selected_by_ale","Metodo associato":"associated_method",
            "Prob. IA 1":"prob_1","Prob. IA X":"prob_x","Prob. IA 2":"prob_2",
            "Quota reale":"fair_odds","Quota iniziale":"opening_odds","Quota attuale":"current_odds",
            "C. AFF.":"c_aff","FLBK":"flbk","C. FB.":"c_fb","QRA/QA":"qra_qa","QI/QA":"qi_qa",
            "Colore allibramento":"allibramento_color","Valore allibramento":"allibramento_value",
            "Allibramento medio":"allibramento_avg","ALLB indicatore":"allb","MTR":"mtr","SCL":"scl",
            "CAL":"cal","STATUS":"status","Puntata (€)":"stake","Quota giocata":"played_odds",
            "Esito finale (V/P)":"outcome","Risultato finale":"final_score",
            "Ritorno lordo (€)":"gross_return","Profitto netto (€)":"profit",
            "1X2 presente":"flag_1x2","Over 1.5":"flag_over_15","Over 2.5":"flag_over_25",
            "Under 2.5":"flag_under_25","Under 3.5":"flag_under_35",
            "Multigol 1-3":"flag_multigol_13","Multigol 1-4":"flag_multigol_14",
            "Formula 4":"flag_formula4","Easy Over":"flag_easy_over","Super Over":"flag_super_over"
        }
        imported = imported.rename(columns=rename)
        count = 0
        existing = set(get_matches()["id"].astype(str)) if not get_matches().empty else set()
        for _,row in imported.iterrows():
            record = blank_match()
            for col in ALL_COLUMNS:
                if col in imported.columns:
                    record[col] = row.get(col)
            record["id"] = str(record["id"]).zfill(4)
            for col in METHOD_COLUMNS.values():
                value = record.get(col)
                record[col] = 1 if str(value).upper() in ["1","SI","TRUE"] else 0
            if record["id"] not in existing:
                save_record(record); count += 1
        st.success(f"Importate {count} partite nuove.")
    df = get_matches()
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Scarica CSV",csv,"stats4bets.csv","text/csv")
    out = io.BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as writer:
        df.to_excel(writer,index=False,sheet_name="Database")
        pd.DataFrame([summary(df)]).to_excel(writer,index=False,sheet_name="Statistiche")
    st.download_button("Scarica Excel",out.getvalue(),"stats4bets.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

elif page == "⚙️ Configurazione":
    st.subheader("⚙️ Configurazione")
    st.write("Archivio attuale:", storage_label())
    st.markdown("""
**Per la lettura automatica** aggiungi nelle Secrets:
```toml
OPENAI_API_KEY="..."
OPENAI_MODEL="gpt-4.1-mini"
```

**Per salvare i dati in modo permanente** crea la tabella Supabase usando `supabase_schema.sql`, poi aggiungi:
```toml
SUPABASE_URL="..."
SUPABASE_KEY="..."
```
""")
    if not use_supabase():
        st.warning("Su Streamlit Cloud il database SQLite locale può essere perso dopo riavvii o aggiornamenti. Configura Supabase prima di inserire dati importanti.")
