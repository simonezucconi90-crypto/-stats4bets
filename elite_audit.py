import json
import pandas as pd
import app

df = app.get_matches()
timeline, bets, periods = app.simulate_dynamic_elite(
    df,
    stake=20.0,
    confirmed_only=True,
)

def iso(value):
    if pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()

summary = {
    "matches_database": int(len(df)),
    "timeline_rows": int(len(timeline)),
    "first_state": iso(timeline["Dal"].min()) if not timeline.empty else None,
    "first_confirmed_state": iso(
        timeline.loc[timeline["Consolidata"], "Dal"].min()
    ) if not timeline.empty and timeline["Consolidata"].any() else None,
    "strategies": (
        timeline.loc[timeline["Consolidata"], "Strategia"]
        .astype(str)
        .drop_duplicates()
        .tolist()
        if not timeline.empty else []
    ),
    "bets": int(len(bets)),
    "wins": int((bets["outcome"] == "V").sum()) if not bets.empty else 0,
    "losses": int((bets["outcome"] == "P").sum()) if not bets.empty else 0,
    "profit": round(float(bets["Profitto simulato €"].sum()), 2)
        if not bets.empty else 0.0,
    "roi": round(
        float(bets["Profitto simulato €"].sum())
        / (len(bets) * 20.0) * 100.0,
        2,
    ) if not bets.empty else 0.0,
}

print("ELITE_AUDIT_SUMMARY=" + json.dumps(summary, ensure_ascii=False))
if not periods.empty:
    printable = periods.copy()
    for col in ["Dal", "Al"]:
        printable[col] = pd.to_datetime(
            printable[col], errors="coerce"
        ).dt.strftime("%Y-%m-%d %H:%M")
    print("ELITE_AUDIT_PERIODS=" + printable.to_json(
        orient="records",
        force_ascii=False,
    ))
