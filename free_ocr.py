import io
import re
from typing import Any
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

COLOURS = {"VERDE":"VE","VE":"VE","GIALLO":"GI","GI":"GI","VIOLA":"VI","VI":"VI","ROSSO":"RO","RO":"RO"}
METHODS = {
    "1X2":[r"\b1X2\b"], "Over 1.5":[r"OVER\s*1[.,]5"], "Over 2.5":[r"OVER\s*2[.,]5"],
    "Under 2.5":[r"UNDER\s*2[.,]5"], "Under 3.5":[r"UNDER\s*3[.,]5"],
    "Multigol 1-3":[r"MULTIGOL\s*1\s*[-–]\s*3"], "Multigol 1-4":[r"MULTIGOL\s*1\s*[-–]\s*4"],
    "Formula 4":[r"FORMULA\s*4"], "Easy Over":[r"EASY\s*OVER"], "Super Over":[r"SUPER\s*OVER"],
}

def first_text(text, patterns):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).strip()
    return ""

def first_number(text, patterns):
    try:
        return float(first_text(text, patterns).replace(",", "."))
    except Exception:
        return 0.0

def colour(text, label):
    value = first_text(text, [rf"{label}\s*[:=]?\s*(VE|GI|VI|RO|VERDE|GIALLO|VIOLA|ROSSO)"]).upper()
    return COLOURS.get(value, "")

def read_screenshot(file_bytes: bytes) -> tuple[dict[str, Any], str]:
    image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    w, h = image.size
    if w < 1800:
        factor = min(2.0, 1800 / max(w, 1))
        image = image.resize((int(w * factor), int(h * factor)))

    result, _ = RapidOCR()(np.array(image))
    if not result:
        raise RuntimeError("Nessun testo riconosciuto nello screenshot.")

    lines = [r[1].strip() for r in result if len(r) >= 3 and float(r[2]) >= 0.35]
    full = "\n".join(x for x in lines if x)
    upper = full.upper()
    data = {}

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", full)
    if m:
        d, mo, y = m.groups()
        if len(y) == 2:
            y = "20" + y
        data["date"] = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    m = re.search(r"\b([01]?\d|2[0-3])[:.](\d{2})\b", full)
    if m:
        data["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"

    candidates = [
        line for line in lines
        if re.search(r"[A-Za-zÀ-ÿ]{2,}.*\s[-–]\s.*[A-Za-zÀ-ÿ]{2,}", line)
        and not re.search(r"MULTIGOL|OVER|UNDER", line, re.I)
    ]
    if candidates:
        data["match_name"] = max(candidates, key=len)

    data["league"] = first_text(full, [r"(?:CAMPIONATO|LEAGUE)\s*[:\-]\s*([^\n]+)"])
    data["round_name"] = first_text(full, [r"(?:GIORNATA|ROUND|FASE)\s*[:\-]?\s*([^\n]+)"])
    if re.search(r"OTTIMO\s*1", upper):
        data["selected_by_ale"] = "Ottimo 1"
    elif re.search(r"OTTIMO\s*2", upper):
        data["selected_by_ale"] = "Ottimo 2"

    data["prob_1"] = first_number(full, [r"(?:PROB(?:ABILITÀ)?(?:\s*IA)?\s*1|P\s*1)\s*[:=]?\s*(\d{1,3}[.,]?\d*)"])
    data["prob_x"] = first_number(full, [r"(?:PROB(?:ABILITÀ)?(?:\s*IA)?\s*X|P\s*X)\s*[:=]?\s*(\d{1,3}[.,]?\d*)"])
    data["prob_2"] = first_number(full, [r"(?:PROB(?:ABILITÀ)?(?:\s*IA)?\s*2|P\s*2)\s*[:=]?\s*(\d{1,3}[.,]?\d*)"])
    data["fair_odds"] = first_number(full, [r"(?:QUOTA\s*REALE|QRA)\s*[:=]?\s*(\d+[.,]\d+)"])
    data["opening_odds"] = first_number(full, [r"(?:QUOTA\s*INIZIALE|QI)\s*[:=]?\s*(\d+[.,]\d+)"])
    data["current_odds"] = first_number(full, [r"(?:QUOTA\s*ATTUALE|QA)\s*[:=]?\s*(\d+[.,]\d+)"])

    for field, label in {
        "c_aff":r"C\.?\s*AFF\.?","flbk":r"FLBK","c_fb":r"C\.?\s*FB\.?",
        "qra_qa":r"QRA\s*/\s*QA","qi_qa":r"QI\s*/\s*QA",
        "allibramento_color":r"(?:ALLIBRAMENTO|ALLB)","mtr":r"MTR","scl":r"SCL",
        "cal":r"CAL","status":r"STATUS"
    }.items():
        data[field] = colour(full, label)

    flags = {name:any(re.search(p, upper, re.I) for p in pats) for name,pats in METHODS.items()}
    data["associated_method"] = " | ".join(name for name,present in flags.items() if present)
    data["method_flags"] = flags

    if not data.get("match_name") and not data["associated_method"]:
        raise RuntimeError("Testo letto, ma struttura Stats4Bets non riconosciuta. Usa uno screenshot completo e nitido.")
    return data, full
