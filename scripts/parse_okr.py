#!/usr/bin/env python3
"""Parse the OKR tracker workbook (Manager sheet) into data/okr-data.json for the web dashboard.

Usage: python3 scripts/parse_okr.py path/to/OKR.xlsx
Writes: data/okr-data.json (relative to repo root)

The sheet mixes clean numeric fields (KR weight, monthly Score 1-4) with free-text
monthly "Actual" notes that embed counts (e.g. "07 CQI-X + 4 PCPA+ 1 VDA6.3").
This script keeps the raw text verbatim for every month (source of truth) and,
for a handful of KRs where the text follows a consistent pattern, also extracts
best-effort numeric counts as a convenience layer for charting. Any month whose
text doesn't match a pattern is left as None (rendered as "-" by the UI) rather
than guessed.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

MONTHS = [
    ("Jan", "L", "M"), ("Feb", "N", "O"), ("Mar", "P", "Q"), ("Apr", "R", "S"),
    ("May", "T", "U"), ("June", "V", "W"), ("July", "X", "Y"), ("Aug", "Z", "AA"),
]
YTD = ("AB", "AC")


def cell(ws, col, row):
    v = ws[f"{col}{row}"].value
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v


def month_series(ws, row):
    out = []
    for label, acol, scol in MONTHS:
        out.append({"month": label, "actual": cell(ws, acol, row), "score": cell(ws, scol, row)})
    return out


def ytd(ws, row):
    acol, scol = YTD
    return {"actual": cell(ws, acol, row), "score": cell(ws, scol, row)}


def num(pattern, text, flags=re.I):
    if not text:
        return None
    m = re.search(pattern, text, flags)
    if not m:
        return None
    for g in m.groups():
        if g is not None:
            try:
                return int(g)
            except ValueError:
                continue
    return None


def parse_audits(text):
    if not text:
        return {"vda": None, "cqi": None, "pcpa": None, "psa": None, "other": None}
    vda = num(r"(\d+)\s*VDA\s*6\.?3", text)
    cqi = num(r"(\d+)\s*CQI", text)
    pcpa = num(r"(\d+)\s*PCPA", text)
    psa = num(r"(\d+)\s*PSA", text)
    other = 0
    for m in re.finditer(r"(\d+)\s+([A-Za-z][A-Za-z \-]{2,30}?Audit)", text):
        other += int(m.group(1))
    return {"vda": vda, "cqi": cqi, "pcpa": pcpa, "psa": psa, "other": other or None}


def parse_kr2_dmn(text):
    if not text:
        return {"actual": None, "target": None}
    nums = [int(n) for n in re.findall(r"(\d+)\s*(?:DMN|Existing|APQP part)", text, re.I)]
    actual = sum(nums) if nums else num(r"(\d+)\s*(?:as\s+)?against\s*(?:the\s*)?target", text)
    target = num(r"target of\s*(\d+)", text)
    return {"actual": actual, "target": target}


def parse_kr7(text):
    if not text:
        return {"runRate": None, "newSourcing": None}
    run_rate = num(r"(\d+)\s*Run\s*@?\s*Rate", text)
    new_sourcing = num(r"New\s+Sourcing[^0-9]{0,15}(\d+)", text)
    return {"runRate": run_rate, "newSourcing": new_sourcing}


def parse_kr8(text):
    if not text:
        return {"ppap": None, "ots": None}
    ppap = num(r"(\d+)\s*PPAP", text)
    ots = num(r"(\d+)\s*OTS", text)
    return {"ppap": ppap, "ots": ots}


def parse_kr9(text):
    if not text:
        return {"ppapApproved": None}
    return {"ppapApproved": num(r"(\d+)\s*PPAP", text)}


def parse_kr10(text):
    if not text:
        return {"runRate": None, "safeLaunchPass": None, "safeLaunchFail": None}
    run_rate = num(r"(\d+)\s*[Rr]un\s*@?\s*[Rr]ate|(\d+)\s*run\s*@\s*rate", text) or num(r"(\d+)\s*[Rr]un\b", text)
    pass_ = num(r"(\d+)\s*parts?\s*successful", text)
    fail = num(r"(\d+)\s*parts?\s*failed|(\d+)\s*part\s*\(", text)
    if fail is None:
        m = re.search(r"(\d+)\s*KTFL\s*[Ss]afe\s*[Ll]aunch\s*fail", text)
        fail = int(m.group(1)) if m else None
    return {"runRate": run_rate, "safeLaunchPass": pass_, "safeLaunchFail": fail}


def parse_kr11(text):
    if not text:
        return {"mqsAudits": None}
    return {"mqsAudits": num(r"(\d+)[\s\-.]*MQS", text)}


def parse_kr14(text):
    if not text:
        return {"count": None}
    return {"count": num(r"(\d+)\s*AI\s*Assistant", text)}


def build_kr(ws, row, kr_id):
    return {
        "id": kr_id,
        "name": cell(ws, "E", row) or kr_id,
        "planText": cell(ws, "F", row),
        "stretchText": cell(ws, "G", row),
        "dept": cell(ws, "H", row),
        "responsible": cell(ws, "I", row),
        "targetDate": cell(ws, "J", row),
        "weight": cell(ws, "D", row),
        "months": month_series(ws, row),
        "ytd": ytd(ws, row),
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "OKR.xlsx"
    repo_root = Path(__file__).resolve().parent.parent
    wb = openpyxl.load_workbook(src, data_only=True)
    ws = wb["Manager"]

    objectives = [
        {"id": 1, "name": "Supplier Mass Production Quality", "row": 4, "krRows": [4, 5, 6, 7, 8]},
        {"id": 2, "name": "Quality Support for New Procurement Items", "row": 9, "krRows": [9, 10, 11, 12, 13, 14]},
        {"id": 3, "name": "Personnel Reserves and Efficiency Enhancement", "row": 15, "krRows": [15]},
        {"id": 4, "name": "EOS", "row": 16, "krRows": [16]},
        {"id": 5, "name": "AI Assistant", "row": 17, "krRows": [17]},
    ]

    out_objectives = []
    for obj in objectives:
        weight = cell(ws, "C", obj["row"])
        krs = []
        for row in obj["krRows"]:
            kr_id = (cell(ws, "E", row) or f"KR{row}").replace(" ", "")
            krs.append(build_kr(ws, row, kr_id))
        out_objectives.append({
            "id": obj["id"], "name": obj["name"], "weight": weight, "krs": krs,
        })

    overall_score_trend = month_series(ws, 18)
    overall_ytd = ytd(ws, 18)

    kr_by_id = {kr["id"]: kr for obj in out_objectives for kr in obj["krs"]}

    def parsed_months(kr, parser):
        rows = []
        for m in kr["months"]:
            rows.append({"month": m["month"], "score": m["score"], "text": m["actual"], **parser(m["actual"])})
        return rows

    def parsed_ytd(kr, parser):
        return {"score": kr["ytd"]["score"], "text": kr["ytd"]["actual"], **parser(kr["ytd"]["actual"])}

    parsed = {
        "audits": {
            "kr": "KR3", "label": kr_by_id["KR3"]["name"],
            "planText": kr_by_id["KR3"]["planText"], "stretchText": kr_by_id["KR3"]["stretchText"],
            "months": parsed_months(kr_by_id["KR3"], parse_audits),
            "ytd": parsed_ytd(kr_by_id["KR3"], parse_audits),
        },
        "supplierComplaints": {
            "kr2": {
                "label": kr_by_id["KR2"]["name"], "planText": kr_by_id["KR2"]["planText"],
                "months": parsed_months(kr_by_id["KR2"], parse_kr2_dmn),
                "ytd": parsed_ytd(kr_by_id["KR2"], parse_kr2_dmn),
            },
            "kr5": {
                "label": kr_by_id["KR5"]["name"], "planText": kr_by_id["KR5"]["planText"],
                "months": [{"month": m["month"], "score": m["score"], "text": m["actual"]} for m in kr_by_id["KR5"]["months"]],
                "ytd": {"score": kr_by_id["KR5"]["ytd"]["score"], "text": kr_by_id["KR5"]["ytd"]["actual"]},
            },
        },
        "apqp": {
            "kr7_kickoffRunRate": {
                "label": kr_by_id["KR7"]["name"], "planText": kr_by_id["KR7"]["planText"],
                "months": parsed_months(kr_by_id["KR7"], parse_kr7),
                "ytd": parsed_ytd(kr_by_id["KR7"], parse_kr7),
            },
            "kr8_ppapOts": {
                "label": kr_by_id["KR8"]["name"], "planText": kr_by_id["KR8"]["planText"],
                "months": parsed_months(kr_by_id["KR8"], parse_kr8),
                "ytd": parsed_ytd(kr_by_id["KR8"], parse_kr8),
            },
            "kr9_ppapFtq": {
                "label": kr_by_id["KR9"]["name"], "planText": kr_by_id["KR9"]["planText"],
                "months": parsed_months(kr_by_id["KR9"], parse_kr9),
                "ytd": parsed_ytd(kr_by_id["KR9"], parse_kr9),
            },
            "kr10_runRateSafeLaunch": {
                "label": kr_by_id["KR10"]["name"], "planText": kr_by_id["KR10"]["planText"],
                "months": parsed_months(kr_by_id["KR10"], parse_kr10),
                "ytd": parsed_ytd(kr_by_id["KR10"], parse_kr10),
            },
            "kr11_mqs": {
                "label": kr_by_id["KR11"]["name"], "planText": kr_by_id["KR11"]["planText"],
                "months": parsed_months(kr_by_id["KR11"], parse_kr11),
                "ytd": parsed_ytd(kr_by_id["KR11"], parse_kr11),
            },
            "sopNote": "SOP (Start of Production) milestones are not tracked as a separate field in the source OKR sheet.",
        },
        "safeLaunch": {
            "kr10": {
                "label": kr_by_id["KR10"]["name"], "planText": kr_by_id["KR10"]["planText"], "stretchText": kr_by_id["KR10"]["stretchText"],
                "months": parsed_months(kr_by_id["KR10"], parse_kr10),
                "ytd": parsed_ytd(kr_by_id["KR10"], parse_kr10),
            },
        },
        "eos": {
            "kr13": {
                "label": kr_by_id["KR13"]["name"], "planText": kr_by_id["KR13"]["planText"], "stretchText": kr_by_id["KR13"]["stretchText"],
                "months": [{"month": m["month"], "score": m["score"], "value": m["actual"] if isinstance(m["actual"], (int, float)) else None, "text": m["actual"] if isinstance(m["actual"], str) else None} for m in kr_by_id["KR13"]["months"]],
                "ytd": {"score": kr_by_id["KR13"]["ytd"]["score"], "value": kr_by_id["KR13"]["ytd"]["actual"] if isinstance(kr_by_id["KR13"]["ytd"]["actual"], (int, float)) else None},
            },
        },
        "aiAssistant": {
            "kr14": {
                "label": kr_by_id["KR14"]["name"], "planText": kr_by_id["KR14"]["planText"], "stretchText": kr_by_id["KR14"]["stretchText"],
                "months": parsed_months(kr_by_id["KR14"], parse_kr14),
                "ytd": parsed_ytd(kr_by_id["KR14"], parse_kr14),
            },
        },
    }

    data = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "year": 2026,
        "months": [m[0] for m in MONTHS],
        "objectives": out_objectives,
        "overallScoreTrend": overall_score_trend,
        "overallYtd": overall_ytd,
        "parsed": parsed,
    }

    out_path = repo_root / "data" / "okr-data.json"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
