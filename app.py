import base64
import io
import json
import os
import sqlite3
from datetime import datetime
from itertools import combinations

import pandas as pd
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
    api_key = secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Manca OPENAI_API_KEY nelle Secrets di Streamlit.")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    raw = uploaded.getvalue()
    mime = uploaded.type or "image/png"
    if mime == "application/pdf":
        file_obj = client.files.create(
            file=(uploaded.name, raw, mime),
            purpose="user_data"
        )
        file_item = {"type": "input_file", "file_id": file_obj.id}
    else:
        encoded = base64.b64encode(raw).decode("utf-8")
        file_item = {
            "type": "input_image",
            "image_url": f"data:{mime};base64,{encoded}",
        }

    prompt = """
Leggi la schermata Stats4Bets/SuperFoglio e restituisci SOLO JSON valido.
Campi:
date YYYY-MM-DD, time, league, match_name, round_name, market, pick,
selected_by_ale, associated_method, prob_1, prob_x, prob_2,
fair_odds, opening_odds, current_odds, c_aff, flbk, c_fb, qra_qa,
qi_qa, allibramento_color, allibramento_value, allibramento_avg,
allb, mtr, scl, cal, status, method_flags.

Regole:
- percentuali come 61.6 e non 0.616;
- colori solo VE, GI, VI, RO;
- associated_method separato con " | ";
- method_flags deve contenere esattamente:
  1X2, Over 1.5, Over 2.5, Under 2.5, Under 3.5,
  Multigol 1-3, Multigol 1-4, Formula 4, Easy Over, Super Over;
- non inventare valori illeggibili.
"""
    response = client.responses.create(
        model=secret("OPENAI_MODEL", "gpt-4.1-mini"),
        input=[{
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}, file_item],
        }],
    )
    extracted = parse_json_text(response.output_text)
    data = blank_match()
    data.update(extracted)
    flags = extracted.get("method_flags", {})
    for label, col in METHOD_COLUMNS.items():
        data[col] = 1 if flags.get(label) else 0
    data["played_odds"] = float(data.get("current_odds") or 0)
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
    dims = {
        "ALLB VE":("allibramento_color","VE"),
        "ALLB GI":("allibramento_color","GI"),
        "ALLB VI":("allibramento_color","VI"),
        "ALLB RO":("allibramento_color","RO"),
        "SCL VE":("scl","VE"), "SCL GI":("scl","GI"),
        "MTR VE":("mtr","VE"), "MTR GI":("mtr","GI"),
    }
    dims.update({label:(col,1) for label,col in METHOD_COLUMNS.items()})
    results = []
    items = list(dims.items())
    for size in range(1, max_filters+1):
        for combo in combinations(items,size):
            cols = [spec[0] for _,spec in combo]
            if len(cols) != len(set(cols)):
                continue
            sub = closed.copy()
            for _,(col,val) in combo:
                sub = sub[sub[col] == val]
            if len(sub) < min_sample:
                continue
            s = summary(sub)
            results.append({
                "Combinazione":" + ".join(label for label,_ in combo),
                "Partite":s["closed"],"Vinte":s["wins"],
                "Win rate %":round(s["win_rate"],2),
                "Quota media":round(s["avg_odds"],2),
                "Profitto €":round(s["profit"],2),
                "ROI %":round(s["roi"],2),
            })
    return pd.DataFrame(results).sort_values(["Profitto €","ROI %","Partite"], ascending=[False,False,False]) if results else pd.DataFrame()

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
    "🏠 Home","➕ Nuova partita","📋 Database","🏆 Aggiorna risultato",
    "✏️ Modifica/Elimina","📊 Dashboard","🔎 Analisi filtri",
    "🥇 Migliori combinazioni","📥 Importa/Esporta","⚙️ Configurazione"
])

if page == "🏠 Home":
    st.subheader("Gestione completa dal telefono")
    c1,c2,c3 = st.columns(3)
    c1.info("➕ Carica screenshot/PDF e salva la partita")
    c2.info("🏆 Aggiorna risultato, ritorno e profitto")
    c3.info("🥇 Cerca i filtri e le combinazioni migliori")
    df = get_matches()
    s = summary(df)
    metrics = st.columns(4)
    metrics[0].metric("Partite", s["total"])
    metrics[1].metric("Concluse", s["closed"])
    metrics[2].metric("Profitto", f'€ {s["profit"]:.2f}')
    metrics[3].metric("ROI", f'{s["roi"]:.2f}%')

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
        st.dataframe(show, use_container_width=True, hide_index=True)

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
    row1[0].metric("Partite",s["total"]); row1[1].metric("Concluse",s["closed"])
    row1[2].metric("Vinte",s["wins"]); row1[3].metric("Perse",s["losses"])
    row2 = st.columns(4)
    row2[0].metric("Win rate",f'{s["win_rate"]:.2f}%')
    row2[1].metric("Profitto",f'€ {s["profit"]:.2f}')
    row2[2].metric("ROI",f'{s["roi"]:.2f}%')
    row2[3].metric("Quota media",f'{s["avg_odds"]:.2f}')
    closed = df[df["outcome"].isin(["V","P"])].copy() if not df.empty else df
    if not closed.empty:
        closed["profitto_cumulato"] = pd.to_numeric(closed["profit"], errors="coerce").fillna(0).cumsum()
        st.line_chart(closed.set_index("id")["profitto_cumulato"])

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

elif page == "🥇 Migliori combinazioni":
    st.subheader("🥇 Migliori combinazioni")
    df = get_matches()
    minimum = st.slider("Campione minimo",1,100,3)
    maximum = st.slider("Numero massimo filtri",1,3,2)
    result = combo_table(df,minimum,maximum)
    if result.empty:
        st.info("Servono più partite concluse o un campione minimo più basso.")
    else:
        st.dataframe(result, hide_index=True, use_container_width=True)

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
