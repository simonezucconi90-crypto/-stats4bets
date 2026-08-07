import base64
import io
import json
import os
import sqlite3
import time
import math
from datetime import datetime
from itertools import combinations

import pandas as pd
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
    "fair_odds","opening_odds","current_odds","c_aff","flbk","c_fb",
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


def require_login():
    password = secret("APP_PASSWORD")
    if not password:
        st.warning("Configura APP_PASSWORD nelle Secrets di Streamlit.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("🔐 Stats4Bets")
    entered = st.text_input("Password dell'app", type="password")
    if st.button("Entra", type="primary"):
        if entered == password:
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Password errata.")
    st.stop()

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
    values = {k: v for k, v in values.items() if k in ALL_COLUMNS}
    if use_supabase():
        supabase_client().table("matches").update(values).eq("id", match_id).execute()
    else:
        sets = ",".join([f"{k}=?" for k in values])
        with sqlite_connection() as con:
            con.execute(
                f"UPDATE matches SET {sets} WHERE id=?",
                list(values.values()) + [match_id],
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
    filtered = df.copy()
    for column, values in filters.items():
        if values:
            filtered = filtered[filtered[column].astype(str).isin(values)]
    odds = pd.to_numeric(filtered["current_odds"], errors="coerce")
    filtered = filtered[(odds >= min_odds) & (odds <= max_odds)]

    probs = pd.to_numeric(filtered["prob_1"], errors="coerce")
    filtered = filtered[(probs >= min_prob) & (probs <= max_prob)]
    return filtered




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

    closed = closed.sort_values(["date", "time", "id"]).copy()

    dimensions = {}

    categorical = {
        "ALLB": "allibramento_color",
        "MTR": "mtr",
        "SCL": "scl",
        "CAL": "cal",
        "C.AFF.": "c_aff",
        "FLBK": "flbk",
        "C.FB.": "c_fb",
        "QRA/QA": "qra_qa",
        "QI/QA": "qi_qa",
        "Campionato": "league",
    }

    for label, column in categorical.items():
        if column not in closed.columns:
            continue
        values = (
            closed[column]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
        )
        for value in values:
            if value and value.lower() not in {"nan", "none"}:
                dimensions[f"{label}={value}"] = {
                    "family": column,
                    "column": column,
                    "kind": "equals",
                    "value": value,
                }

    odds = pd.to_numeric(closed["current_odds"], errors="coerce")
    odds_bins = [
        (1.20, 1.39),
        (1.40, 1.49),
        (1.50, 1.59),
        (1.60, 1.69),
        (1.70, 1.79),
        (1.80, 1.99),
        (2.00, 2.24),
        (2.25, 2.49),
        (2.50, 2.99),
        (3.00, 99.00),
    ]
    for low, high in odds_bins:
        if ((odds >= low) & (odds <= high)).any():
            dimensions[f"Quota {low:.2f}-{high:.2f}"] = {
                "family": "current_odds",
                "column": "current_odds",
                "kind": "range",
                "low": low,
                "high": high,
            }

    prob = pd.to_numeric(closed["prob_1"], errors="coerce")
    for threshold in [50, 55, 60, 65, 70, 75, 80]:
        if (prob >= threshold).any():
            dimensions[f"Prob.1≥{threshold}%"] = {
                "family": "prob_1",
                "column": "prob_1",
                "kind": "minimum",
                "value": threshold,
            }

    items = list(dimensions.items())
    results = []
    selections = {}

    for size in range(1, max_filters + 1):
        for combo in combinations(items, size):
            labels = [label for label, _ in combo]
            specs = [spec for _, spec in combo]

            # Do not combine two filters from the same family
            # (e.g. two different odds bands or two thresholds of prob_1).
            families = [spec["family"] for spec in specs]
            if len(families) != len(set(families)):
                continue

            subset = closed.copy()

            for spec in specs:
                column = spec["column"]

                if spec["kind"] == "equals":
                    subset = subset[
                        subset[column].astype(str) == str(spec["value"])
                    ]

                elif spec["kind"] == "range":
                    values = pd.to_numeric(
                        subset[column], errors="coerce"
                    )
                    subset = subset[
                        (values >= spec["low"])
                        & (values <= spec["high"])
                    ]

                elif spec["kind"] == "minimum":
                    values = pd.to_numeric(
                        subset[column], errors="coerce"
                    )
                    subset = subset[values >= spec["value"]]

            if len(subset) < min_sample:
                continue

            subset = subset.sort_values(["date", "time", "id"]).copy()
            n = len(subset)

            # Chronological validation: earlier data for discovery,
            # most recent data for out-of-sample check.
            validation_n = max(1, int(math.ceil(n * validation_ratio)))
            train_n = n - validation_n

            # Need at least 2 observations in each part for a meaningful split.
            use_validation = train_n >= 2 and validation_n >= 2

            if use_validation:
                train_df = subset.iloc[:train_n]
                test_df = subset.iloc[train_n:]
                train_stats = strategy_statistics(train_df)
                test_stats = strategy_statistics(test_df)
            else:
                train_df = subset
                test_df = subset.iloc[0:0]
                train_stats = strategy_statistics(train_df)
                test_stats = {
                    "total": 0, "closed": 0, "wins": 0, "losses": 0,
                    "win_rate": 0, "staked": 0, "profit": 0, "roi": 0,
                    "avg_odds": 0, "max_losing_streak": 0,
                }

            total_stats = strategy_statistics(subset)

            # Reliability grows with sample size, reaching 100% around 100 matches.
            sample_reliability = min(1.0, n / 100.0)

            # Validation component: positive recent/out-of-sample performance
            # is rewarded; negative performance is penalized.
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

            # Penalize long losing streaks.
            streak_penalty = 1 / (1 + 0.12 * total_stats["max_losing_streak"])

            # Balanced score. Profit remains in euros and ROI is percentage.
            score = (
                total_stats["roi"] * 0.45
                + total_stats["win_rate"] * 0.15
                + (total_stats["profit"] / 20.0) * 0.40
            )
            score *= sample_reliability
            score *= validation_factor
            score *= streak_penalty

            strategy_id = f"S{len(results)+1:05d}"
            strategy_name = " + ".join(labels)

            results.append({
                "ID": strategy_id,
                "Strategia": strategy_name,
                "Partite": total_stats["closed"],
                "Vinte": total_stats["wins"],
                "Perse": total_stats["losses"],
                "Win rate %": round(total_stats["win_rate"], 2),
                "Quota media": round(total_stats["avg_odds"], 2),
                "Profitto €": round(total_stats["profit"], 2),
                "ROI %": round(total_stats["roi"], 2),
                "Max perdite consecutive": total_stats["max_losing_streak"],
                "ROI ricerca %": round(train_stats["roi"], 2),
                "ROI verifica %": round(test_stats["roi"], 2) if use_validation else None,
                "Partite verifica": test_stats["closed"] if use_validation else 0,
                "Validata": "✅" if use_validation and train_stats["roi"] > 0 and test_stats["roi"] > 0 else "⚠️",
                "Affidabilità campione %": round(sample_reliability * 100, 1),
                "Punteggio": round(score, 2),
            })

            selections[strategy_id] = subset.index.tolist()

    if not results:
        return pd.DataFrame(), {}

    table = pd.DataFrame(results).sort_values(
        ["Punteggio", "Profitto €", "ROI %", "Partite"],
        ascending=[False, False, False, False],
    ).head(top_n)

    valid_ids = set(table["ID"])
    selections = {
        strategy_id: indices
        for strategy_id, indices in selections.items()
        if strategy_id in valid_ids
    }

    return table.reset_index(drop=True), selections



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

require_login()

st.title("📊 Stats4Bets")
st.caption(f"Archivio e analisi partite • Archivio: {storage_label()}")

page = st.sidebar.radio("Menu", [
    "🏠 Home","⚡ Inserimento rapido","➕ Nuova partita","📋 Database","🏆 Aggiorna risultato",
    "✏️ Modifica/Elimina","📊 Dashboard","🔎 Analisi filtri",
    "🧪 Laboratorio Strategie","🧠 Trova metodo migliore",
    "📥 Importa/Esporta","⚙️ Configurazione"
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
        labels = {f'{r["id"]} — {r["match_name"]}':r["id"] for _,r in df.iterrows()}
        selected = st.selectbox("Scegli partita", list(labels))
        mid = labels[selected]
        record = df[df["id"]==mid].iloc[0].to_dict()
        edited = editor_form(record, f"edit_{mid}")
        c1,c2 = st.columns(2)
        if c1.button("💾 Salva modifiche", type="primary"):
            edited.pop("id",None)
            update_record(mid, edited)
            st.success("Modifiche salvate.")
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
    closed = df[df["outcome"].isin(["V","P"])].copy() if not df.empty else df
    if not closed.empty:
        # Il profitto cumulato deve seguire l'ordine reale delle giocate.
        # Prima ordiniamo cronologicamente, poi facciamo la somma cumulata.
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

        closed["profitto_cumulato"] = closed["profit"].cumsum().round(2)

        # Etichetta progressiva leggibile e stabile.
        closed["Giocata"] = range(1, len(closed) + 1)

        st.markdown("### 📈 Andamento profitto cumulato")
        st.line_chart(
            closed.set_index("Giocata")["profitto_cumulato"]
        )

        ultimo_profitto = float(closed["profitto_cumulato"].iloc[-1])
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
                "allibramento_color", "mtr", "scl", "cal", "Esito",
                "final_score", "Profitto €"
            ]].rename(columns={
                "date": "Data", "time": "Ora", "league": "Campionato",
                "match_name": "Partita", "allibramento_color": "ALLB",
                "mtr": "MTR", "scl": "SCL", "cal": "CAL",
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
    st.subheader("🧠 Motore Strategie V2")
    st.caption(
        "Cerca automaticamente le combinazioni migliori e verifica "
        "se reggono anche sulla parte più recente dello storico."
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

        if st.button(
            "🔍 Cerca migliori strategie",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Analizzo le combinazioni..."):
                ranking, selections = automatic_strategy_search(
                    closed,
                    min_sample=int(min_sample),
                    max_filters=int(max_filters),
                    top_n=int(top_n),
                    validation_ratio=float(validation_pct) / 100.0,
                )
                st.session_state["strategy_ranking_v2"] = ranking
                st.session_state["strategy_selections_v2"] = selections

        ranking = st.session_state.get("strategy_ranking_v2")
        selections = st.session_state.get("strategy_selections_v2", {})

        if isinstance(ranking, pd.DataFrame) and not ranking.empty:
            st.markdown("### 🏆 Classifica")

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
                        "Profitto €": round(ss["profit"], 2),
                        "ROI %": round(ss["roi"], 2),
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
