import ast
import json
import os
import types
from datetime import datetime, timezone

import numpy as np
import pandas as pd


APP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "strategy_latest.json",
)


def load_engine_module():
    """Carica il motore da app.py senza eseguire l'interfaccia Streamlit."""
    with open(APP_PATH, "r", encoding="utf-8") as handle:
        source = handle.read()

    tree = ast.parse(source, filename=APP_PATH)
    allowed = (
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )
    nodes = [
        node
        for node in tree.body
        if isinstance(node, allowed)
        and int(getattr(node, "lineno", 0) or 0) < 3980
    ]
    module = types.ModuleType("stats4bets_strategy_engine")
    module.__file__ = APP_PATH
    exec(
        compile(
            ast.Module(body=nodes, type_ignores=[]),
            APP_PATH,
            "exec",
        ),
        module.__dict__,
    )
    return module


def json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    raise TypeError(f"Tipo non serializzabile: {type(value)!r}")


def normalized_records(frame):
    records = []
    for row in frame.to_dict(orient="records"):
        clean = {}
        for key, value in row.items():
            if value is None:
                clean[key] = None
            elif isinstance(value, float) and np.isnan(value):
                clean[key] = None
            elif isinstance(value, np.generic):
                clean[key] = value.item()
            else:
                clean[key] = value
        records.append(clean)
    return records


def main():
    engine = load_engine_module()
    matches = engine.get_matches()
    closed = matches[matches["outcome"].isin(["V", "P"])].copy()
    if closed.empty:
        raise RuntimeError("Nessuna partita conclusa disponibile.")

    min_sample = min(10, max(1, len(closed)))
    max_filters = 3
    top_n = 50
    validation_ratio = 0.30

    engine_signature = engine.strategy_engine_signature(
        closed,
        min_sample=min_sample,
        max_filters=max_filters,
        top_n=top_n,
        validation_ratio=validation_ratio,
    )
    ranking, selections = engine.automatic_strategy_search(
        closed,
        min_sample=min_sample,
        max_filters=max_filters,
        top_n=top_n,
        validation_ratio=validation_ratio,
    )
    if ranking is None or ranking.empty:
        raise RuntimeError("Il motore non ha prodotto strategie valide.")

    history_result = engine.save_strategy_snapshot(ranking)
    history_table, snapshot_count = engine.strategy_history_summary(
        ranking["Strategia"].tolist(),
        current_ranking=ranking,
    )
    definitive = engine.definitive_strategy_ranking(
        ranking,
        history_table,
        snapshot_count,
        selections,
    )
    required_matches = engine.elite_required_matches(len(closed))
    follow_state, follow_message = engine.update_strategy_follow_state(
        definitive,
        min_matches=required_matches,
        closed=closed,
    )
    official_row = engine.strategy_follow_display_row(
        definitive,
        follow_state,
        closed=closed,
    )

    operational_label = "⚪ NESSUNA UFFICIALE"
    if isinstance(follow_state, dict) and official_row is not None:
        official_name = str(follow_state.get("official") or "")
        official_selected = engine.apply_generated_strategy_name(
            closed,
            official_name,
        )
        health = engine.official_operational_status(official_selected)
        usable = (
            int(official_row.get("Partite", 0) or 0) >= required_matches
            and float(official_row.get("ROI %", 0) or 0) > 0
            and float(official_row.get("Profitto €", 0) or 0) > 0
        )
        if not usable or str(health["status"]).startswith("🔴"):
            operational_label = "⛔ NON DA SEGUIRE"
        elif str(health["status"]).startswith("🟡"):
            operational_label = "⚠️ DA SEGUIRE CON CAUTELA"
        elif str(health["status"]).startswith("🟢"):
            operational_label = "✅ DA SEGUIRE"
        else:
            operational_label = "⚪ DATI INSUFFICIENTI"

        active = operational_label in {
            "✅ DA SEGUIRE",
            "⚠️ DA SEGUIRE CON CAUTELA",
        }
        if (
            follow_state.get("operational_label") != operational_label
            or follow_state.get("operational_active") is not active
        ):
            follow_state = dict(follow_state)
            follow_state["operational_label"] = operational_label
            follow_state["operational_active"] = active
            engine.save_strategy_follow_state(follow_state)

    serializable_selections = {
        str(key): [int(value) for value in (values or [])]
        for key, values in (selections or {}).items()
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine_signature": engine_signature,
        "closed_matches": int(len(closed)),
        "snapshot_count": int(snapshot_count),
        "history_result": history_result,
        "follow_message": follow_message,
        "operational_label": operational_label,
        "ranking": normalized_records(ranking),
        "selections": serializable_selections,
    }

    temporary_path = OUTPUT_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            default=json_default,
        )
        handle.write("\n")
    os.replace(temporary_path, OUTPUT_PATH)

    print(
        "Strategie aggiornate: "
        f"{len(ranking)} analizzate, "
        f"{len(closed)} partite concluse, "
        f"stato {operational_label}."
    )


if __name__ == "__main__":
    main()
