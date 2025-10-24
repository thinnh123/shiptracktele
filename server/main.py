# server/main.py
from __future__ import annotations
import os, sys, pathlib, threading, time, json, urllib.request, urllib.parse
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# -------------------- PATH SETUP --------------------
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.common import store
from app.common.normalizer import unify
from app.common.telegramer import send_async as tg_send, pretty_message as tg_msg
from app.carriers import mock, ghn, spx, vtp, jnt

# -------------------- ENV --------------------
load_dotenv()
USE_MOCK = os.getenv("USE_MOCK", "false").lower() == "true"
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_POLLING = os.getenv("TELEGRAM_POLLING", "false").lower() == "true"
TELEGRAM_ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
SCHED_ENABLED = os.getenv("SCHED_ENABLED", "true").lower() == "true"

store.init_db()
CARRIERS = {"mock": mock, "ghn": ghn, "spx": spx, "vtp": vtp, "jnt": jnt}
DEFAULT_CARRIER_FOR_UNIFY = lambda c: c if c != "mock" else "ghn"

# -------------------- FASTAPI --------------------
app = FastAPI(title="ShipTrack Server", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root(): return {"message": "ShipTrack server is running"}
@app.get("/health")
def health(): return {"ok": True}

class CreateReq(BaseModel):
    label: str
    carrier: str
    code: str
    jnt_phone4: Optional[str] = None
    auto_poll: bool = True

def _get_vendor_event(carrier: str, code: str, jnt_phone4: Optional[str] = None) -> Dict[str, Any]:
    backend = CARRIERS.get(carrier)
    if not backend: raise HTTPException(400, "carrier invalid")
    if carrier == "jnt":
        phone = (jnt_phone4 or "").strip()
        if not (phone.isdigit() and len(phone) == 4):
            raise HTTPException(400, "jnt_phone4 required for J&T")
        vendor = backend.get_tracking(code, phone)
    else: vendor = backend.get_tracking(code)
    return vendor

def _notify(label: str, carrier: str, code: str, unified):
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        text = tg_msg(label, carrier, code, unified.latest.text, unified.latest.location, unified.latest.time_iso)
        tg_send(text)
    except: pass

@app.get("/shipments")
def list_shipments(): return [dict(r) for r in store.list_shipments()]

@app.post("/shipments")
def add_shipment(req: CreateReq):
    carrier = req.carrier.lower()
    if USE_MOCK and carrier != "mock": carrier = "mock"
    vendor = _get_vendor_event(carrier, req.code, req.jnt_phone4)
    unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), req.code, vendor["latest_event"])
    store.add_shipment(req.label or "(Không tên)", carrier, req.code, unified)
    try:
        con = store.connect()
        with con:
            con.execute("UPDATE shipments SET auto_poll=? WHERE tracking_code=?", (1 if req.auto_poll else 0, req.code))
        con.close()
    except: pass
    _notify(req.label or "(Không tên)", carrier, req.code, unified)
    return {"ok": True}

@app.delete("/shipments/{sid}")
def delete_shipment(sid: int):
    store.delete_shipment(sid); return {"ok": True}

@app.post("/shipments/{sid}/refresh")
def refresh_one(sid: int, jnt_phone4: Optional[str] = Query(default=None)):
    con = store.connect(); row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone(); con.close()
    if not row: raise HTTPException(404, "not found")
    carrier, code = row["carrier"], row["tracking_code"]
    vendor = _get_vendor_event(carrier, code, jnt_phone4)
    unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
    changed = store.update_shipment_from_unified(sid, unified)
    if changed: _notify(row["label"], carrier, code, unified)
    return {"ok": True, "changed": bool(changed)}

@app.post("/refresh-all")
def refresh_all():
    cnt = 0
    con = store.connect(); rows = con.execute("SELECT * FROM shipments WHERE auto_poll=1").fetchall(); con.close()
    for r in rows:
        try:
            if r["carrier"] == "jnt": continue
            vendor = _get_vendor_event(r["carrier"], r["tracking_code"])
            unified = unify(DEFAULT_CARRIER_FOR_UNIFY(r["carrier"]), r["tracking_code"], vendor["latest_event"])
            changed = store.update_shipment_from_unified(r["id"], unified)
            if changed: _notify(r["label"], r["carrier"], r["tracking_code"], unified); cnt += 1
        except: continue
    return {"ok": True, "updated_changed": cnt}

# -------------------- AUTO REFRESH --------------------
from apscheduler.schedulers.background import BackgroundScheduler
def _refresh_all_job():
    try: refresh_all()
    except Exception as e: print("[Scheduler] Error:", e)
if SCHED_ENABLED:
    scheduler = BackgroundScheduler(); scheduler.add_job(_refresh_all_job, "interval", minutes=3, id="refresh_all_job")
    scheduler.start(); print("[Scheduler] Auto refresh ON")

# -------------------- TELEGRAM BOT --------------------
def tg_api(method: str, data: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN: return {"ok": False}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read().decode())

def tg_send(chat_id: str | int, text: str) -> None:
    try: tg_api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    except: pass

def _fmt_row(r): return f"[{r['id']}] {r['label']} • {r['carrier'].upper()} • {r['tracking_code']} • {r['last_status_text']}"

HELP_TEXT = (
    "🚚 *ShipTrack Bot*\n"
    "/list - Xem danh sách đơn\n"
    "/add <carrier> <code> [jnt_phone4]\n"
    "/check <code|id>\n"
    "/refresh <id>\n"
    "/refresh_jnt <id> <phone4>\n"
    "/delete <id>\n"
)

def handle_update(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id = str(msg["chat"]["id"]); text = (msg.get("text") or "").strip()
    if TELEGRAM_ALLOWED_CHAT_ID and chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        tg_send(chat_id, "❌ Bot riêng tư."); return
    parts = text.split(); cmd = parts[0].lower(); args = parts[1:]

    if cmd in ("/start", "/help"): tg_send(chat_id, HELP_TEXT)
    elif cmd == "/list":
        rows = store.list_shipments()
        tg_send(chat_id, "📦\n" + "\n".join(_fmt_row(r) for r in rows) if rows else "Không có đơn.")
    elif cmd == "/add":
        if len(args) < 2: tg_send(chat_id, "Cú pháp: /add <carrier> <code> [phone4]"); return
        c, code = args[0], args[1]; phone = args[2] if len(args) > 2 else None
        try:
            vendor = _get_vendor_event(c, code, phone)
            unified = unify(DEFAULT_CARRIER_FOR_UNIFY(c), code, vendor["latest_event"])
            store.add_shipment("(Bot)", c, code, unified)
            tg_send(chat_id, f"✅ Thêm {c.upper()} {code}\n{unified.latest.text}")
        except Exception as e: tg_send(chat_id, f"❌ Lỗi: {e}")
    elif cmd == "/check":
        if not args: tg_send(chat_id, "Cú pháp: /check <mã|id>"); return
        k = args[0]; con = store.connect()
        row = con.execute("SELECT * FROM shipments WHERE id=?",(int(k),)).fetchone() if k.isdigit() else con.execute("SELECT * FROM shipments WHERE tracking_code=?",(k,)).fetchone()
        con.close(); tg_send(chat_id, _fmt_row(row) if row else "Không thấy đơn.")
    elif cmd == "/refresh":
        if not args or not args[0].isdigit(): tg_send(chat_id, "Cú pháp: /refresh <id>"); return
        sid=int(args[0]); con=store.connect(); row=con.execute("SELECT * FROM shipments WHERE id=?",(sid,)).fetchone(); con.close()
        if not row: tg_send(chat_id,"Không thấy đơn."); return
        if row["carrier"]=="jnt": tg_send(chat_id,"Dùng /refresh_jnt <id> <phone4>"); return
        vendor=_get_vendor_event(row["carrier"],row["tracking_code"])
        unified=unify(DEFAULT_CARRIER_FOR_UNIFY(row["carrier"]),row["tracking_code"],vendor["latest_event"])
        changed=store.update_shipment_from_unified(sid,unified)
        tg_send(chat_id,f"✅ {unified.latest.text} ({'changed' if changed else 'no change'})")
    elif cmd == "/refresh_jnt":
        if len(args)<2: tg_send(chat_id,"/refresh_jnt <id> <phone4>"); return
        sid,phone=int(args[0]),args[1]
        con=store.connect(); row=con.execute("SELECT * FROM shipments WHERE id=?",(sid,)).fetchone(); con.close()
        if not row: tg_send(chat_id,"Không thấy đơn."); return
        vendor=_get_vendor_event("jnt",row["tracking_code"],phone)
        unified=unify("jnt",row["tracking_code"],vendor["latest_event"])
        changed=store.update_shipment_from_unified(sid,unified)
        tg_send(chat_id,f"✅ J&T: {unified.latest.text}")
    elif cmd == "/delete":
        if not args: tg_send(chat_id,"/delete <id>"); return
        store.delete_shipment(int(args[0])); tg_send(chat_id,"Đã xoá.")
    else: tg_send(chat_id,"Gõ /help để xem lệnh.")

def polling_loop():
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN: return
    print("[TG] Polling started."); offset=None
    while True:
        try:
            url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params={"timeout":50,"allowed_updates":json.dumps(["message","edited_message"])}
            if offset: params["offset"]=offset
            full=url+"?"+urllib.parse.urlencode(params)
            with urllib.request.urlopen(full,timeout=60) as r: data=json.loads(r.read().decode())
            if data.get("ok"):
                for upd in data.get("result",[]): offset=upd["update_id"]+1; handle_update(upd)
        except Exception as e: print("[TG poll]",e); time.sleep(3)

if TELEGRAM_POLLING:
    threading.Thread(target=polling_loop, daemon=True).start()
    print("[TG] Polling thread started.")

# -------------------- LOCAL DEV --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
