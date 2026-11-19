"""JomiGuard minimal backend: upload -> records -> rule engine (D1, D3) -> findings + brief.
Run:  pip install -r requirements.txt && uvicorn main:app --reload   ->  http://localhost:8000
"""
import json, uuid
from pathlib import Path
from typing import List
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="JomiGuard")
UPLOADS = Path("uploads"); UPLOADS.mkdir(exist_ok=True)

# Sample case (same as the UI). Real OCR/extraction would produce records like these.
SAMPLE = [
    {"type": "khatian", "id": "RS 218", "date": "1984-01-01", "seller": "", "buyer": "Md. Mozammel Hoque", "dag": "245", "area": 6.0, "cite": "RS Khatian 218 p.112"},
    {"type": "heba", "id": "#882", "date": "1999-01-01", "seller": "Md. Mozammel Hoque", "buyer": "Rohima Khatun", "dag": "245", "area": 3.5, "cite": "Heba #882 · 1999"},
    {"type": "deed", "id": "#4102", "date": "2018-03-14", "seller": "Md. Mozammel Hoque", "buyer": "Jahangir Alam", "dag": "245", "area": 5.0, "cite": "Deed #4102 p.1 l.14"},
    {"type": "deed", "id": "#6377", "date": "2019-10-29", "seller": "Md. Mozammel Hoque", "buyer": "Parvez Hossain", "dag": "245", "area": 5.0, "cite": "Deed #6377 p.2 l.7"},
]
REQUIRED = {"type", "id", "date", "dag", "area"}
CASES = {"sample": {"records": SAMPLE}}


def run_rules(records):
    """Deterministic checks. Output wording is 'risk indicator', never a legal finding."""
    findings = []
    for dag in sorted({r["dag"] for r in records}):
        rs = sorted((r for r in records if r["dag"] == dag), key=lambda r: r["date"])
        deeds = [r for r in rs if r["type"] == "deed"]
        # D1: same seller signs 2+ sale deeds on the same dag
        sellers = {}
        for d in deeds:
            sellers.setdefault(d.get("seller", ""), []).append(d)
        for seller, ds in sellers.items():
            if len(ds) >= 2:
                parts = ", ".join(f"Deed {d['id']} ({d['date'][:4]}, {d['area']:.2f} katha to {d.get('buyer','?')})" for d in ds)
                findings.append({"id": "D1", "t": "Second deed on the same dag", "b": "একই দাগে দ্বিতীয় দলিল",
                                 "s": f"{seller} signed {len(ds)} sale deeds on Dag {dag}: {parts}.",
                                 "c": [d["cite"] if d.get("cite") else f"Deed {d['id']}" for d in ds]})
        # D3: a transfer exceeds the balance still held (khatian area minus earlier transfers)
        kh = next((r for r in rs if r["type"] == "khatian"), None)
        if kh:
            bal, over, cites = kh["area"], [], [kh.get("cite") or f"Khatian {kh['id']}"]
            for r in rs:
                if r["type"] in ("heba", "deed"):
                    if r["area"] > bal + 1e-9:
                        over.append(f"{r['type'].title()} {r['id']} conveys {r['area']:.2f} katha vs {bal:.2f} remaining")
                        cites.append(r.get("cite") or f"{r['type']} {r['id']}")
                    bal = max(bal - r["area"], 0)
            if over:
                findings.append({"id": "D3", "t": "Sold area exceeds recorded holding", "b": "রেকর্ডের অতিরিক্ত হস্তান্তর",
                                 "s": f"Recorded holding on Dag {dag} is {kh['area']:.2f} katha. " + "; ".join(over) + ".",
                                 "c": cites[:3]})
    return findings


def get_case(cid):
    if cid not in CASES:
        raise HTTPException(404, "case not found")
    return CASES[cid]


@app.post("/api/cases")
async def create_case(files: List[UploadFile] = File(...)):
    """Accepts any files (saved to ./uploads). .json files are read as structured records;
    with no .json, the sample records are used (OCR is not included in this minimal version)."""
    cid, records = uuid.uuid4().hex[:8], []
    folder = UPLOADS / cid; folder.mkdir()
    for f in files:
        data = await f.read()
        (folder / Path(f.filename).name).write_bytes(data)
        if f.filename.lower().endswith(".json"):
            try:
                obj = json.loads(data)
            except ValueError:
                raise HTTPException(422, f"{f.filename}: invalid JSON")
            for r in (obj if isinstance(obj, list) else [obj]):
                if not isinstance(r, dict) or not REQUIRED <= r.keys():
                    raise HTTPException(422, f"{f.filename}: each record needs {sorted(REQUIRED)}")
                records.append(r)
    CASES[cid] = {"records": records or SAMPLE}
    return {"id": cid, "records": len(CASES[cid]["records"]), "used_sample": not records}


@app.get("/api/cases/{cid}/findings")
def findings(cid: str):
    recs = get_case(cid)["records"]
    return {"case": cid, "records": len(recs), "findings": run_rules(recs)}


@app.get("/api/cases/{cid}/brief", response_class=PlainTextResponse)
def brief(cid: str):
    fs = run_rules(get_case(cid)["records"])
    md = [f"# Legal evidence summary — case {cid}", "",
          "_Decision support for your lawyer, not a legal verdict._", ""]
    for i, f in enumerate(fs, 1):
        md += [f"## {i}. {f['id']}: {f['t']} — needs lawyer review", f["s"], "Citations: " + "; ".join(f["c"]), ""]
    md.append("A lawyer must sign off before any payment.")
    return PlainTextResponse("\n".join(md), headers={"Content-Disposition": f'attachment; filename="brief-{cid}.md"'})


app.mount("/", StaticFiles(directory="static", html=True), name="static")
