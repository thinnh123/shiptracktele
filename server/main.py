# server/main.py
from __future__ import annotations
import os
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# --- add project root to sys.path so we can import app/* ---
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# ---- load envs ----
load_dotenv()
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
USE_MOCK = os.getenv("USE_MOCK", "false").lower() == "true"
SCHED_ENABLED = os.getenv("SCHED_ENABLED", "true").lower() == "true"

# ---- imports from your desktop app code ----
from app.common import store
from app.common.normalizer import unify
from app.common.telegramer import send_async as tg_send, pretty_message as tg_msg
from app.carriers import mock, ghn, spx, vtp, jnt

# init DB
store.init_db()

# map carriers
CARRIERS = {
    "mock": mock,
    "ghn": ghn,
    "spx": spx,
    "vtp": vtp,
    "jnt": jnt,
}
DEFAULT_CARRIER_FOR_UNIFY = lambda c: c if c != "mock" else "ghn"

# ---------- FastAPI app ----------
app = FastAPI(title="ShipTrack Server", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "ShipTrack server is running"}

@app.get("/health")
def health():
    return {"ok": True}


# ---------- Schemas ----------
class CreateReq(BaseModel):
    label: str
    carrier: str
    code: str
    jnt_phone4: Optional[str] = None
    auto_poll: bool = True


# ---------- Helpers ----------
def _get_vendor_event(carrier: str, code: str, jnt_phone4: Optional[str] = None) -> Dict[str, Any]:
    backend = CARRIERS.get(carrier)
    if not backend:
        raise HTTPException(400, "carrier invalid")
    if carrier == "jnt":
        phone = (jnt_phone4 or "").strip()
        if not (phone.isdigit() and len(phone) == 4):
            raise HTTPException(400, "jnt_phone4 (4 digits) required for J&T")
        vendor = backend.get_tracking(code, phone)
    else:
        vendor = backend.get_tracking(code)
    return vendor

def _notify(label: str, carrier: str, code: str, unified):
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        text = tg_msg(
            label, carrier, code,
            unified.latest.text, unified.latest.location, unified.latest.time_iso
        )
        tg_send(text)
    except Exception:
        pass


# ---------- API ----------
@app.get("/shipments")
def list_shipments():
    return [dict(r) for r in store.list_shipments()]

@app.post("/shipments")
def add_shipment(req: CreateReq):
    carrier = req.carrier.lower()
    if USE_MOCK and carrier != "mock":
        carrier = "mock"

    vendor = _get_vendor_event(carrier, req.code, req.jnt_phone4)
    unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), req.code, vendor["latest_event"])
    # thêm vào DB
    store.add_shipment(req.label or "(Không tên)", carrier, req.code, unified)
    # set auto_poll nếu có cột auto_poll
    try:
        con = store.connect()
        with con:
            con.execute("UPDATE shipments SET auto_poll=? WHERE tracking_code=?", (1 if req.auto_poll else 0, req.code))
        con.close()
    except Exception:
        pass

    _notify(req.label or "(Không tên)", carrier, req.code, unified)
    return {"ok": True}

@app.delete("/shipments/{sid}")
def delete_shipment(sid: int):
    store.delete_shipment(sid)
    return {"ok": True}

@app.post("/shipments/{sid}/refresh")
def refresh_one(sid: int, jnt_phone4: Optional[str] = Query(default=None)):
    con = store.connect()
    row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "not found")

    carrier = row["carrier"]
    code = row["tracking_code"]
    vendor = _get_vendor_event(carrier, code, jnt_phone4)
    unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
    changed = store.update_shipment_from_unified(sid, unified)
    if changed:
        _notify(row["label"], carrier, code, unified)
    return {"ok": True, "changed": bool(changed)}

@app.post("/refresh-all")
def refresh_all():
    cnt = 0
    con = store.connect()
    rows = con.execute("SELECT * FROM shipments WHERE auto_poll=1").fetchall()
    con.close()
    for r in rows:
        try:
            # Bỏ qua J&T khi không có phone (vì server chưa lưu phone 4 số)
            if r["carrier"] == "jnt":
                continue
            vendor = _get_vendor_event(r["carrier"], r["tracking_code"])
            unified = unify(DEFAULT_CARRIER_FOR_UNIFY(r["carrier"]), r["tracking_code"], vendor["latest_event"])
            changed = store.update_shipment_from_unified(r["id"], unified)
            if changed:
                _notify(r["label"], r["carrier"], r["tracking_code"], unified)
                cnt += 1
        except Exception:
            continue
    return {"ok": True, "updated_changed": cnt}


# ---------- Scheduler 24/7 (auto refresh mỗi 3 phút) ----------
from apscheduler.schedulers.background import BackgroundScheduler

def _refresh_all_job():
    try:
        refresh_all()
    except Exception:
        pass

if SCHED_ENABLED:
    scheduler = BackgroundScheduler()
    scheduler.add_job(_refresh_all_job, "interval", minutes=3, id="refresh_all_job")
    scheduler.start()
    print("[Scheduler] Auto refresh is ON (every 3 min)")
else:
    print("[Scheduler] Auto refresh is OFF")


# ---------- local dev entry ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
