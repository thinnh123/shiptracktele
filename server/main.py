# server/main.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import os

# Thêm path để import app/
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.common import store
from app.common.normalizer import unify
from app.common.telegramer import send_async as tg_send, pretty_message as tg_msg
from app.carriers import mock, ghn, spx, vtp, jnt

# Khởi tạo DB
store.init_db()

CARRIERS = {"mock": mock, "ghn": ghn, "spx": spx, "vtp": vtp, "jnt": jnt}

app = FastAPI(title="ShipTrack API", version="1.0.0")


class CreateReq(BaseModel):
    label: str
    carrier: str
    code: str
    jnt_phone4: Optional[str] = None
    auto_poll: bool = True


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/shipments")
def list_shipments():
    rows = store.list_shipments()
    return [dict(r) for r in rows]


@app.post("/shipments")
def add_shipment(req: CreateReq):
    carrier = req.carrier.lower()
    if carrier not in CARRIERS:
        raise HTTPException(400, detail="carrier invalid")

    backend = CARRIERS[carrier]

    # Gọi API theo hãng
    if carrier == "jnt":
        phone = (req.jnt_phone4 or "").strip()
        if not (phone.isdigit() and len(phone) == 4):
            raise HTTPException(400, detail="jnt_phone4 (4 số cuối) required for J&T")
        vendor = backend.get_tracking(req.code, phone)
    else:
        vendor = backend.get_tracking(req.code)

    unified = unify(carrier if carrier != "mock" else "ghn",
                    req.code, vendor["latest_event"])
    store.add_shipment(req.label or "(Không tên)",
                       carrier, req.code, unified,
                       auto_poll=1 if req.auto_poll else 0)

    # Gửi telegram thông báo trạng thái hiện tại (optional)
    try:
        text = tg_msg(req.label or "(Không tên)", carrier, req.code,
                      unified.latest.text, unified.latest.location, unified.latest.time_iso)
        tg_send(text)
    except Exception:
        pass

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
        raise HTTPException(404, detail="not found")

    carrier = row["carrier"]
    code = row["tracking_code"]
    backend = CARRIERS.get(carrier)
    if not backend:
        raise HTTPException(400, detail="carrier invalid")

    try:
        if carrier == "jnt":
            phone = (jnt_phone4 or "").strip()
            if not (phone.isdigit() and len(phone) == 4):
                raise HTTPException(400, detail="jnt_phone4 required for J&T")
            vendor = backend.get_tracking(code, phone)
        else:
            vendor = backend.get_tracking(code)

        unified = unify(carrier if carrier != "mock" else "ghn",
                        code, vendor["latest_event"])
        changed = store.update_shipment_from_unified(sid, unified)

        # gửi telegram nếu có thay đổi
        if changed:
            try:
                text = tg_msg(row["label"], carrier, code,
                              unified.latest.text, unified.latest.location, unified.latest.time_iso)
                tg_send(text)
            except Exception:
                pass

        return {"ok": True, "changed": bool(changed)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# -----------------------------
# Auto refresh nền bằng APScheduler
# -----------------------------
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

def refresh_all_job():
    try:
        con = store.connect()
        rows = con.execute("SELECT id, label, carrier, tracking_code FROM shipments WHERE auto_poll=1").fetchall()
        con.close()
        for r in rows:
            try:
                # J&T cần phone — ở web API này, job nền bỏ qua J&T (hoặc bạn có thể thêm bảng phone)
                if r["carrier"] == "jnt":
                    continue
                backend = CARRIERS[r["carrier"]]
                vendor = backend.get_tracking(r["tracking_code"])
                unified = unify(r["carrier"] if r["carrier"] != "mock" else "ghn",
                                r["tracking_code"], vendor["latest_event"])
                changed = store.update_shipment_from_unified(r["id"], unified)
                if changed:
                    text = tg_msg(r["label"], r["carrier"], r["tracking_code"],
                                  unified.latest.text, unified.latest.location, unified.latest.time_iso)
                    tg_send(text)
            except Exception:
                continue
    except Exception:
        pass

scheduler = BackgroundScheduler(timezone="Asia/Ho_Chi_Minh")
scheduler.add_job(refresh_all_job, "interval", minutes=3, id="refresh_all")
scheduler.start()

# Uvicorn entrypoint: uvicorn server.main:app --host 0.0.0.0 --port 8000
