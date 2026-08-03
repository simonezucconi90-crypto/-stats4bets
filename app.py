
import base64, io, json, os, sqlite3
from datetime import datetime
from itertools import combinations

import pandas as pd
import streamlit as st
from openai import OpenAI

DB = "stats4bets.db"
METHODS = {
    "1X2":"flag_1x2","Over 1.5":"flag_over_15","Over 2.5":"flag_over_25",
    "Under 2.5":"flag_under_25","Under 3.5":"flag_under_35",
    "Multigol 1-3":"flag_multigol_13","Multigol 1-4":"flag_multigol_14",
    "Formula 4":"flag_formula4","Easy Over":"flag_easy_over","Super Over":"flag_super_over"
}
COLS = ["id","date","time","league","match","round","market","pick","selected_by_ale",
"associated_method","prob_1","prob_x","prob_2","fair_odds","opening_odds","current_odds",
"c_aff","flbk","c_fb","qra_qa","qi_qa","allibramento_color","allibramento_value",
"allibramento_avg","allb","mtr","scl","cal","status","stake","played_odds","outcome",
"final_score","gross_return","profit"] + list(METHODS.values())

def con():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS matches(
    id TEXT PRIMARY KEY,date TEXT,time TEXT,league TEXT,match TEXT,round TEXT,market TEXT,pick TEXT,
    selected_by_ale TEXT,associated_method TEXT,prob_1 REAL,prob_x REAL,prob_2 REAL,
    fair_odds REAL,opening_odds REAL,current_odds REAL,c_aff TEXT,flbk TEXT,c_fb TEXT,qra_qa TEXT,
    qi_qa TEXT,allibramento_color TEXT,allibramento_value REAL,allibramento_avg REAL,
    allb TEXT,mtr TEXT,scl TEXT,cal TEXT,status TEXT,stake REAL,played_odds REAL,
    outcome TEXT,final_score TEXT,gross_return REAL,profit REAL,
    flag_1x2 INTEGER,flag_over_15 INTEGER,flag_over_25 INTEGER,flag_under_25 INTEGER,
    flag_under_35 INTEGER,flag_multigol_13 INTEGER,flag_multigol_14 INTEGER,
    flag_formula4 INTEGER,flag_easy_over INTEGER,flag_super_over INTEGER)""")
    return c

def dataframe():
    c=con()
    df=pd.read_sql_query("SELECT * FROM matches ORDER BY date,time,id",c)
    c.close()
    return df

def next_id():
    c=con(); x=c.execute("SELECT MAX(CAST(id AS INTEGER)) FROM matches").fetchone()[0]; c.close()
    return f"{(x or 0)+1:04d}"

def save_match(d):
    d["id"]=next_id()
    for name,col in METHODS.items():
        d[col]=1 if d.get("method_flags",{}).get(name) else 0
    row=[d.get(k) for k in COLS]
    c=con()
    c.execute(f"INSERT INTO matches ({','.join(COLS)}) VALUES ({','.join(['?']*len(COLS))})",row)
    c.commit(); c.close()
    return d["id"]

def update_result(mid,outcome,score):
    c=con()
    row=c.execute("SELECT stake,played_odds FROM matches WHERE id=?",(mid,)).fetchone()
    if not row: raise ValueError("ID non trovato")
    stake,odds=row
    gross=stake*odds if outcome=="V" else 0
    profit=gross-stake if outcome=="V" else -stake
    c.execute("UPDATE matches SET outcome=?,final_score=?,gross_return=?,profit=? WHERE id=?",
              (outcome,score,gross,profit,mid))
    c.commit(); c.close()

def blank():
    return {"date":datetime.today().date().isoformat(),"time":"","league":"","match":"","round":"",
    "market":"1X2","pick":"1","selected_by_ale":"Ottimo 1","associated_method":"",
    "prob_1":0.0,"prob_x":0.0,"prob_2":0.0,"fair_odds":0.0,"opening_odds":0.0,"current_odds":0.0,
    "c_aff":"","flbk":"","c_fb":"","qra_qa":"","qi_qa":"","allibramento_color":"",
    "allibramento_value":0.0,"allibramento_avg":0.0,"allb":"","mtr":"","scl":"","cal":"","status":"",
    "stake":20.0,"played_odds":0.0,"method_flags":{}}

def extract(upload):
    if not os.getenv("OPENAI_API_KEY"): return blank()
    client=OpenAI()
    raw=upload.getvalue(); mime=upload.type or "image/png"
    if mime=="application/pdf":
        f=client.files.create(file=(upload.name,raw,mime),purpose="user_data")
        item={"type":"input_file","file_id":f.id}
    else:
        item={"type":"input_image","image_url":f"data:{mime};base64,{base64.b64encode(raw).decode()}"}
    prompt="""Estrai da questa schermata Stats4Bets/SuperFoglio i dati della partita.
    Percentuali come 61.6, non 0.616. Colori solo VE,GI,VI,RO.
    Restituisci JSON con: date YYYY-MM-DD,time,league,match,round,market,pick,selected_by_ale,
    associated_method,prob_1,prob_x,prob_2,fair_odds,opening_odds,current_odds,c_aff,flbk,c_fb,
    qra_qa,qi_qa,allibramento_color,allibramento_value,allibramento_avg,allb,mtr,scl,cal,status,
    method_flags. Nei method_flags usa come chiavi esatte: 1X2, Over 1.5, Over 2.5, Under 2.5,
    Under 3.5, Multigol 1-3, Multigol 1-4, Formula 4, Easy Over, Super Over."""
    r=client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-4.1-mini"),
      input=[{"role":"user","content":[{"type":"input_text","text":prompt},item]}])
    text=r.output_text.strip()
    if text.startswith("```"): text=text.split("```")[1].replace("json","",1).strip()
    d=blank(); d.update(json.loads(text)); d["stake"]=20.0; d["played_odds"]=d.get("current_odds") or 0
    return d

def stats(df):
    closed=df[df.outcome.isin(["V","P"])] if not df.empty else df
    staked=closed.stake.fillna(0).sum() if not closed.empty else 0
    profit=closed.profit.fillna(0).sum() if not closed.empty else 0
    wins=(closed.outcome=="V").sum() if not closed.empty else 0
    return {"Partite":len(df),"Concluse":len(closed),"Vinte":int(wins),
            "Perse":int((closed.outcome=="P").sum()) if not closed.empty else 0,
            "Win rate %":round(wins/len(closed)*100,2) if len(closed) else 0,
            "Profitto €":round(profit,2),"ROI %":round(profit/staked*100,2) if staked else 0,
            "Quota media":round(closed.played_odds.mean(),2) if len(closed) else 0}

def top(df,min_sample,max_filters):
    closed=df[df.outcome.isin(["V","P"])]
    dims={"ALLB VE":("allibramento_color","VE"),"ALLB GI":("allibramento_color","GI"),
    "ALLB VI":("allibramento_color","VI"),"ALLB RO":("allibramento_color","RO"),
    "SCL VE":("scl","VE"),"SCL GI":("scl","GI"),"MTR VE":("mtr","VE"),"MTR GI":("mtr","GI")}
    dims.update({name:(col,1) for name,col in METHODS.items()})
    out=[]
    for n in range(1,max_filters+1):
        for combo in combinations(dims.items(),n):
            cols=[x[1][0] for x in combo]
            if len(cols)!=len(set(cols)): continue
            sub=closed.copy()
            for _,(col,val) in combo: sub=sub[sub[col]==val]
            if len(sub)<min_sample: continue
            s=stats(sub); s["Combinazione"]=" + ".join(x[0] for x in combo); out.append(s)
    return pd.DataFrame(out).sort_values(["Profitto €","ROI %"],ascending=False) if out else pd.DataFrame()

st.set_page_config(page_title="Stats4Bets",page_icon="📊",layout="wide")
st.title("📊 Stats4Bets Tracker")
page=st.sidebar.radio("Menu",["➕ Nuova partita","🏆 Aggiorna risultato","📊 Dashboard","🔎 Analisi","🥇 Combinazioni","📥 Esporta"])

if page=="➕ Nuova partita":
    f=st.file_uploader("Screenshot o PDF",type=["png","jpg","jpeg","webp","pdf"])
    if f:
        with st.spinner("Lettura..."): d=extract(f)
        c1,c2=st.columns(2)
        with c1:
            for k,l in [("date","Data"),("time","Ora"),("league","Campionato"),("match","Partita"),
                        ("round","Giornata/Fase"),("market","Mercato"),("pick","Esito"),
                        ("selected_by_ale","Scelto da Ale"),("associated_method","Metodo associato")]:
                d[k]=st.text_input(l,str(d.get(k,"")))
        with c2:
            for k,l in [("prob_1","Prob. IA 1"),("prob_x","Prob. IA X"),("prob_2","Prob. IA 2"),
                        ("fair_odds","Quota reale"),("opening_odds","Quota iniziale"),
                        ("current_odds","Quota attuale"),("allibramento_value","Valore allibramento"),
                        ("allibramento_avg","Allibramento medio"),("stake","Puntata"),("played_odds","Quota giocata")]:
                d[k]=st.number_input(l,value=float(d.get(k) or 0),step=.01)
            for k,l in [("c_aff","C. AFF."),("flbk","FLBK"),("c_fb","C. FB."),("qra_qa","QRA/QA"),
                        ("qi_qa","QI/QA"),("allibramento_color","Colore allibramento"),
                        ("allb","ALLB"),("mtr","MTR"),("scl","SCL"),("cal","CAL"),("status","STATUS")]:
                d[k]=st.text_input(l,d.get(k,""))
        st.markdown("### Metodi")
        flags=d.get("method_flags",{})
        cs=st.columns(5)
        for i,m in enumerate(METHODS): flags[m]=cs[i%5].checkbox(m,value=bool(flags.get(m)))
        d["method_flags"]=flags
        if st.button("✅ Salva",type="primary"):
            st.success(f"Salvata con ID {save_match(d)}")

elif page=="🏆 Aggiorna risultato":
    df=dataframe(); open_df=df[~df.outcome.isin(["V","P"])] if not df.empty else df
    if open_df.empty: st.info("Nessuna partita aperta")
    else:
        label=st.selectbox("Partita",[f'{r.id} — {r.match}' for _,r in open_df.iterrows()])
        mid=label.split(" — ")[0]
        score=st.text_input("Risultato finale")
        outcome=st.radio("Esito",["V","P"],horizontal=True)
        if st.button("Aggiorna",type="primary"):
            update_result(mid,outcome,score); st.success("Aggiornata")

elif page=="📊 Dashboard":
    df=dataframe(); s=stats(df)
    cols=st.columns(4)
    for i,(k,v) in enumerate(s.items()): cols[i%4].metric(k,v)
    st.dataframe(df,use_container_width=True,hide_index=True)

elif page=="🔎 Analisi":
    df=dataframe()
    a=st.multiselect("Allibramento",sorted(df.allibramento_color.dropna().unique()) if not df.empty else [])
    s=st.multiselect("Scala",sorted(df.scl.dropna().unique()) if not df.empty else [])
    methods=st.multiselect("Metodi",list(METHODS.keys()))
    sub=df.copy()
    if a: sub=sub[sub.allibramento_color.isin(a)]
    if s: sub=sub[sub.scl.isin(s)]
    for m in methods: sub=sub[sub[METHODS[m]]==1]
    st.dataframe(pd.DataFrame([stats(sub)]),hide_index=True)
    st.dataframe(sub,use_container_width=True,hide_index=True)

elif page=="🥇 Combinazioni":
    df=dataframe()
    n=st.slider("Campione minimo",1,100,5)
    m=st.slider("Filtri combinati",1,3,2)
    st.dataframe(top(df,n,m),use_container_width=True,hide_index=True)

elif page=="📥 Esporta":
    df=dataframe()
    st.download_button("CSV",df.to_csv(index=False).encode("utf-8-sig"),"stats4bets.csv")
    b=io.BytesIO()
    with pd.ExcelWriter(b,engine="openpyxl") as w:
        df.to_excel(w,index=False,sheet_name="Database")
        pd.DataFrame([stats(df)]).to_excel(w,index=False,sheet_name="Statistiche")
    st.download_button("Excel",b.getvalue(),"stats4bets.xlsx")
