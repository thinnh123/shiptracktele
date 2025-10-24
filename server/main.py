# server/main.py
from __future__ import annotations

import os
import json
import time
import threading
import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# ===== App modules =====
from app.common import store
from app.common.normalizer import unify
from app.carriers import mock, ghn, spx, vtp, jnt

# ============ ENV ============
load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_NOTIFY_CHAT_ID = os.getenv("TELEGRAM_NOTIFY_CHAT_ID", "").strip()  # nơi bot gửi thông báo tự động
POLL_TELEGRAM = os.getenv("POLL_TELEGRAM", "false").lower() == "true"

# Scheduler
SCHED_ENABLED = os.getenv("SCHED_ENABLED", "true").lower() == "true"
SCHED_INTERVAL_SEC = int(os.getenv("SCHED_INTERVAL_SEC", "180"))

# Map carrier -> backend
CARRIERS = {
    "mock": mock,
    "ghn": ghn,
    "spx": spx,
    "vtp": vtp,
    "jnt": jnt,  # cần 4 số đt khi add/refresh
}

def DEFAULT_CARRIER_FOR_UNIFY(c: str) -> str:
    return "ghn" if c == "mock" else c

# ============ FASTAPI ============
app = FastAPI(title="ShipTrack Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Khởi tạo DB (SQLite local hoặc Neon Postgres tuỳ bạn đã config store.py)
store.init_db()

# ============ HELPERS ============
def tg_api(method: str) -> str:
    if not TELEGRAM_BOT_TOKEN:
        return ""
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

def tg_send(chat_id: str | int, text: str, parse_mode: Optional[str] = None) -> None:
    """Gửi tin nhắn Telegram (best-effort)."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    try:
        import requests
        url = tg_api("sendMessage")
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass

def _get_vendor_event(carrier: str, code: str, jnt_phone4: Optional[str] = None) -> Dict[str, Any]:
    """Gọi backend tương ứng. Trả về dict có 'latest_event'."""
    carrier = carrier.lower()
    if carrier not in CARRIERS:
        raise ValueError("carrier invalid")
    if carrier == "jnt":
        if not jnt_phone4 or not (jnt_phone4.isdigit() and len(jnt_phone4) == 4):
            raise ValueError("J&T cần 4 số cuối điện thoại (phone4).")
        return CARRIERS[carrier].get_tracking(code, jnt_phone4)
    return CARRIERS[carrier].get_tracking(code)

def parse_add_args(args: List[str]) -> Tuple[str, str, str, Optional[str]]:
    """
    /add <tên_đơn> <carrier> <code> [jnt_phone4]
    - Tên đơn có thể có khoảng trắng; có thể bọc trong dấu " ".
    - Nếu không có dấu ", tự tìm token carrier để cắt.
    """
    if not args or len(args) < 2:
        raise ValueError("Thiếu tham số. Cú pháp: /add <tên_đơn> <carrier> <code> [jnt_phone4]")

    joined = " ".join(args).strip()
    label: Optional[str] = None
    rest_tokens: List[str] = []

    if joined.startswith('"'):
        close = joined.find('"', 1)
        if close == -1:
            raise ValueError('Tên đơn dùng dấu " mở nhưng không đóng.')
        label = joined[1:close].strip() or "(Bot)"
        remain = joined[close + 1 :].strip()
        rest_tokens = [t for t in remain.split() if t]
    else:
        carriers = set(CARRIERS.keys())
        idx = None
        for i, tok in enumerate(args):
            if tok.lower() in carriers:
                idx = i
                break
        if idx is None:
            raise ValueError("Không tìm thấy carrier trong lệnh /add.")
        label = " ".join(args[:idx]).strip() or "(Bot)"
        rest_tokens = args[idx:]

    if not rest_tokens:
        raise ValueError("Thiếu carrier và mã vận đơn.")
    carrier = rest_tokens[0].lower()
    if carrier not in CARRIERS:
        raise ValueError("carrier invalid")

    if len(rest_tokens) < 2:
        raise ValueError("Thiếu mã vận đơn.")
    code = rest_tokens[1]

    phone4 = None
    if carrier == "jnt":
        if len(rest_tokens) < 3:
            raise ValueError("J&T cần 4 số cuối điện thoại: /add <tên_đơn> jnt <code> <phone4>")
        phone4 = rest_tokens[2]
        if not (phone4.isdigit() and len(phone4) == 4):
            raise ValueError("phone4 phải là 4 chữ số.")

    return label, carrier, code, phone4

HELP_TEXT = (
    "🚚 *ShipTrack Bot*\n"
    "/list - Xem danh sách đơn\n"
    "/add <tên đơn> <carrier> <code> [jnt_phone4]\n"
    "   • VD: /add \"Áo thun xanh\" ghn GYVBHWD7\n"
    "   • VD: /add Điện thoại jnt 859627154556 4556\n"
    "/check <code|id>  - xem nhanh trạng thái 1 đơn\n"
    "/refresh <id>     - cập nhật 1 đơn\n"
    "/autoon <id>      - bật tự động theo dõi\n"
    "/autooff <id>     - tắt tự động theo dõi\n"
    "/auto             - xem trạng thái auto của các đơn\n"
    "/delete <id>      - xóa đơn\n"
)

# ============ FASTAPI ROUTES ============
@app.get("/")
def root():
    return {"message": "ShipTrack server is running"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/shipments")
def list_shipments():
    rows = store.list_shipments()
    out = []
    for r in rows:
        out.append(dict(r) if hasattr(r, "keys") else r)
    return {"items": out}

@app.post("/shipments")
async def add_shipment_http(req: Request):
    """
    Body JSON:
      {
        "label": "tên đơn",
        "carrier": "ghn|spx|vtp|jnt|mock",
        "code": "MÃ_VẬN_ĐƠN",
        "jnt_phone4": "4556"   # optional, bắt buộc nếu jnt
      }
    """
    data = await req.json()
    label = str(data.get("label") or "(Bot)")
    carrier = str(data.get("carrier") or "ghn").lower()
    code = str(data.get("code") or "").strip()
    jnt_phone4 = data.get("jnt_phone4")

    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)
    if carrier not in CARRIERS:
        return JSONResponse({"error": "carrier invalid"}, status_code=400)

    try:
        vendor = _get_vendor_event(carrier, code, jnt_phone4)
        unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
        store.add_shipment(label, carrier, code, unified)
        return {"ok": True, "added": {"carrier": carrier, "code": code, "label": label}, "status": unified.latest.text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@app.post("/shipments/{sid}/refresh")
async def refresh_one_http(sid: int, req: Request):
    body = await req.json() if req.headers.get("content-type","").startswith("application/json") else {}
    jnt_phone4 = (body or {}).get("jnt_phone4")
    con = store.connect()
    try:
        row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
    finally:
        con.close()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    r = dict(row) if hasattr(row, "keys") else row
    carrier, code = r["carrier"], r["tracking_code"]
    try:
        vendor = _get_vendor_event(carrier, code, jnt_phone4)
        unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
        changed = store.update_shipment_from_unified(sid, unified)
        if changed:
            _notify_update(r["label"], carrier, code, unified)
        return {"ok": True, "changed": bool(changed), "status": unified.latest.text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@app.post("/refresh-all")
def refresh_all_http():
    cnt = refresh_all_job()
    return {"ok": True, "changed": cnt}

# ============ TELEGRAM HANDLER ============
def handle_update(update: Dict[str, Any]) -> None:
    try:
        # Debug nhẹ: print ra log Render nếu cần
        # print("[TG UPDATE]", json.dumps(update, ensure_ascii=False))
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return

        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/start", "/help"):
            tg_send(chat_id, HELP_TEXT, parse_mode="Markdown")
            return

        elif cmd == "/list":
            rows = store.list_shipments()
            if not rows:
                tg_send(chat_id, "Chưa có đơn.")
                return
            lines = []
            for r in rows:
                d = dict(r) if hasattr(r, "keys") else r
                flag = "✅" if d.get("auto_poll", 0) else "⏸"
                lines.append(
                    f"{flag} [{d['id']}] {d['label']} • {d['carrier'].upper()} • {d['tracking_code']}\n"
                    f"→ {d.get('last_status_text','')} | {d.get('last_checkpoint_time','')} | {d.get('last_location','')}"
                )
            tg_send(chat_id, "\n\n".join(lines))
            return

        elif cmd == "/add":
            try:
                if not args:
                    raise ValueError("Cú pháp: /add <tên_đơn> <carrier> <code> [jnt_phone4]")
                label, carrier, code, phone = parse_add_args(args)
                vendor = _get_vendor_event(carrier, code, phone)
                unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
                store.add_shipment(label, carrier, code, unified)
                tg_send(chat_id, f"✅ Đã thêm: *{label}*\nĐVVC: {carrier.upper()} • Mã: `{code}`\n{unified.latest.text}", parse_mode="Markdown")
            except Exception as e:
                tg_send(chat_id, f"❌ Lỗi: {e}")
            return

        elif cmd == "/check":
            if not args:
                tg_send(chat_id, "Cú pháp: /check <code|id>")
                return
            key = args[0]
            con = store.connect()
            try:
                if key.isdigit():
                    row = con.execute("SELECT * FROM shipments WHERE id=?", (int(key),)).fetchone()
                else:
                    row = con.execute("SELECT * FROM shipments WHERE tracking_code=?", (key,)).fetchone()
            finally:
                con.close()
            if not row:
                tg_send(chat_id, "Không tìm thấy đơn.")
                return
            d = dict(row) if hasattr(row, "keys") else row
            tg_send(
                chat_id,
                f"[{d['id']}] {d['label']} • {d['carrier'].upper()} • {d['tracking_code']}\n"
                f"→ {d.get('last_status_text','')} | {d.get('last_checkpoint_time','')} | {d.get('last_location','')}"
            )
            return

        elif cmd == "/refresh":
            if not args or not args[0].isdigit():
                tg_send(chat_id, "Cú pháp: /refresh <id>")
                return
            sid = int(args[0])
            con = store.connect()
            try:
                row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
            finally:
                con.close()
            if not row:
                tg_send(chat_id, "Không tìm thấy đơn.")
                return
            d = dict(row)
            carrier, code = d["carrier"], d["tracking_code"]

            # J&T cần phone4 qua lệnh riêng
            if carrier == "jnt":
                tg_send(chat_id, "J&T cần 4 số ĐT. Dùng: /refresh_jnt <id> <phone4>")
                return

            vendor = _get_vendor_event(carrier, code)
            unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
            changed = store.update_shipment_from_unified(sid, unified)
            if changed:
                _notify_update(d["label"], carrier, code, unified)
            tg_send(chat_id, f"🔄 {d['label']}: {unified.latest.text}\n({unified.latest.time_iso} | {unified.latest.location})")
            return

        elif cmd == "/refresh_jnt":
            # /refresh_jnt <id> <phone4>
            if len(args) < 2 or not args[0].isdigit():
                tg_send(chat_id, "Cú pháp: /refresh_jnt <id> <phone4>")
                return
            sid = int(args[0])
            phone4 = args[1]
            con = store.connect()
            try:
                row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
            finally:
                con.close()
            if not row:
                tg_send(chat_id, "Không tìm thấy đơn.")
                return
            d = dict(row)
            if d["carrier"] != "jnt":
                tg_send(chat_id, "Đơn này không phải J&T.")
                return
            vendor = _get_vendor_event("jnt", d["tracking_code"], phone4)
            unified = unify("jnt", d["tracking_code"], vendor["latest_event"])
            changed = store.update_shipment_from_unified(sid, unified)
            if changed:
                _notify_update(d["label"], "jnt", d["tracking_code"], unified)
            tg_send(chat_id, f"🔄 {d['label']}: {unified.latest.text}\n({unified.latest.time_iso} | {unified.latest.location})")
            return

        elif cmd == "/autoon":
            if not args or not args[0].isdigit():
                tg_send(chat_id, "Cú pháp: /autoon <id>")
                return
            sid = int(args[0])
            con = store.connect()
            try:
                with con:
                    con.execute("UPDATE shipments SET auto_poll=1, updated_at=? WHERE id=?", (store.now_iso(), sid))
            finally:
                con.close()
            tg_send(chat_id, f"✅ Đã bật auto cho đơn #{sid}")
            return

        elif cmd == "/autooff":
            if not args or not args[0].isdigit():
                tg_send(chat_id, "Cú pháp: /autooff <id>")
                return
            sid = int(args[0])
            con = store.connect()
            try:
                with con:
                    con.execute("UPDATE shipments SET auto_poll=0, updated_at=? WHERE id=?", (store.now_iso(), sid))
            finally:
                con.close()
            tg_send(chat_id, f"✅ Đã tắt auto cho đơn #{sid}")
            return

        elif cmd == "/auto":
            rows = store.list_shipments()
            if not rows:
                tg_send(chat_id, "Chưa có đơn.")
                return
            lines = ["⚙️ Trạng thái tự động:"]
            for r in rows:
                d = dict(r) if hasattr(r, "keys") else r
                flag = "✅" if d.get("auto_poll", 0) else "⏸"
                lines.append(f"{flag} [{d['id']}] {d['label']} • {d['carrier'].upper()} • {d['tracking_code']}")
            tg_send(chat_id, "\n".join(lines))
            return

        elif cmd == "/delete":
            if not args or not args[0].isdigit():
                tg_send(chat_id, "Cú pháp: /delete <id>")
                return
            sid = int(args[0])
            store.delete_shipment(sid)
            tg_send(chat_id, f"🗑 Đã xóa đơn #{sid}")
            return

    except Exception as e:
        try:
            chat_id = update.get("message", {}).get("chat", {}).get("id")
            if chat_id:
                tg_send(chat_id, f"⚠️ Lỗi xử lý: {e}")
        except Exception:
            pass

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    upd = await req.json()
    handle_update(upd)
    return {"ok": True}

# ============ AUTO REFRESH + NOTIFY ============
def _fmt_update_msg(label: str, carrier: str, code: str, unified) -> str:
    latest = unified.latest
    when = latest.time_iso or datetime.datetime.now().isoformat()
    loc = latest.location or ""
    txt = latest.text or ""
    carrier_name = carrier.upper()
    return (
        f"📦 *{label}*\n"
        f"ĐVVC: `{carrier_name}` • Mã: `{code}`\n"
        f"Trạng thái mới: *{txt}*\n"
        f"⏱ {when}\n"
        f"📍 {loc}"
    )

def _notify_update(label: str, carrier: str, code: str, unified):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_NOTIFY_CHAT_ID:
        return
    try:
        tg_send(TELEGRAM_NOTIFY_CHAT_ID, _fmt_update_msg(label, carrier, code, unified), parse_mode="Markdown")
    except Exception:
        pass

def _refresh_one_and_maybe_notify(row) -> bool:
    """Refresh 1 shipment, nếu đổi trạng thái -> gửi Telegram. Trả True nếu changed."""
    d = dict(row) if hasattr(row, "keys") else row
    carrier = d["carrier"]
    code = d["tracking_code"]

    # Bỏ qua J&T trong job tự động (vì cần phone4). Có thể mở rộng sau nếu bạn lưu phone4 trong DB.
    if carrier == "jnt":
        return False

    try:
        vendor = _get_vendor_event(carrier, code)
        unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
        changed = store.update_shipment_from_unified(d["id"], unified)
        if changed:
            _notify_update(d["label"], carrier, code, unified)
        return bool(changed)
    except Exception:
        return False

def refresh_all_job() -> int:
    con = store.connect()
    try:
        rows = con.execute("SELECT * FROM shipments WHERE auto_poll=1").fetchall()
    finally:
        con.close()

    changed_cnt = 0
    for r in rows:
        if _refresh_one_and_maybe_notify(r):
            changed_cnt += 1
    return changed_cnt

# Scheduler
if SCHED_ENABLED:
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(refresh_all_job, "interval", seconds=SCHED_INTERVAL_SEC, id="shiptrack_refresh_all")
    scheduler.start()
    print(f"[Scheduler] Auto refresh ON (every {SCHED_INTERVAL_SEC}s)")

# ============ OPTIONAL: POLLING MODE ============
def _poll_telegram_loop():
    """Chỉ dùng khi bạn không set webhook. Đừng chạy đồng thời với webhook."""
    if not TELEGRAM_BOT_TOKEN:
        return
    import requests
    offset = 0
    url = tg_api("getUpdates")
    print("[TG] Polling started.")
    while True:
        try:
            resp = requests.get(url, params={"timeout": 50, "offset": offset}, timeout=55)
            data = resp.json()
            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                handle_update(upd)
        except Exception:
            time.sleep(3)

if POLL_TELEGRAM:
    threading.Thread(target=_poll_telegram_loop, daemon=True).start()

# ============ LOCAL DEV ============
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
