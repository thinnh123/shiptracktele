# server/main.py
from __future__ import annotations

import os
import json
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dotenv import load_dotenv

# ----- App modules -----
from app.common import store
from app.common.normalizer import unify

# Carriers
from app.carriers import mock, ghn, spx, vtp, jnt

# =============================
# Config & bootstrap
# =============================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID_DEFAULT = os.getenv("TELEGRAM_CHAT_ID", "").strip()  # optional

SCHED_ENABLED = os.getenv("SCHED_ENABLED", "true").lower() == "true"
SCHED_INTERVAL_SEC = int(os.getenv("SCHED_INTERVAL_SEC", "180"))

# Map tên carrier -> backend module
CARRIERS = {
    "mock": mock,
    "ghn": ghn,
    "spx": spx,
    "vtp": vtp,
    "jnt": jnt,  # cần phone4 khi add/refresh
}

def DEFAULT_CARRIER_FOR_UNIFY(carrier: str) -> str:
    # bộ "unify" dùng chung mapping GHN/SPX/VTP; mock -> dùng GHN cho convenient
    return "ghn" if carrier == "mock" else carrier

app = FastAPI(title="ShipTrack Server")


# Tạo DB nếu chưa có
store.init_db()


# =============================
# Helpers
# =============================
def tg_api(method: str) -> str:
    if not TELEGRAM_BOT_TOKEN:
        return ""
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

def tg_send(chat_id: str | int, text: str, parse_mode: Optional[str] = None) -> None:
    """Gửi tin nhắn Telegram (best effort, không raise)."""
    try:
        import requests
        url = tg_api("sendMessage")
        if not url:
            return
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass


def _get_vendor_event(carrier: str, code: str, jnt_phone4: Optional[str] = None) -> Dict[str, Any]:
    """
    Gọi về backend tương ứng và trả về dict có khóa "latest_event".
    Với J&T phải có phone4.
    """
    carrier = carrier.lower()
    if carrier not in CARRIERS:
        raise ValueError("carrier invalid")

    if carrier == "jnt":
        if not jnt_phone4 or not (jnt_phone4.isdigit() and len(jnt_phone4) == 4):
            raise ValueError("J&T cần cung cấp 4 số cuối điện thoại (phone4).")
        return CARRIERS[carrier].get_tracking(code, jnt_phone4)
    else:
        return CARRIERS[carrier].get_tracking(code)


def parse_add_args(args: List[str]) -> Tuple[str, str, str, Optional[str]]:
    """
    Parse lệnh: /add <tên_đơn> <carrier> <code> [jnt_phone4]

    - Tên đơn có thể có khoảng trắng. Nếu bọc trong dấu " ... " sẽ ưu tiên lấy nguyên cụm đó.
    - Nếu không có dấu ", sẽ tìm token là <carrier> đầu tiên trong args để cắt làm ranh giới.
    Trả về: (label, carrier, code, phone4|None)
    """
    if not args or len(args) < 2:
        raise ValueError("Thiếu tham số. Cú pháp: /add <tên_đơn> <carrier> <code> [jnt_phone4]")

    joined = " ".join(args).strip()
    label: Optional[str] = None
    rest_tokens: List[str] = []

    if joined.startswith('"'):
        # tìm dấu " đóng
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
    "   • Ví dụ: /add \"Áo thun xanh\" ghn GYVBHWD7\n"
    "   • Ví dụ: /add Điện thoại jnt 859627154556 4556\n"
    "/check <code|id>  - xem nhanh trạng thái 1 đơn\n"
    "/refresh <id>     - cập nhật lại 1 đơn\n"
    "/delete <id>      - xóa đơn\n"
)


# =============================
# FastAPI endpoints
# =============================
@app.get("/")
def root():
    return {"message": "ShipTrack server is running"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/shipments")
def list_shipments():
    rows = store.list_shipments()
    # convert sqlite Row / dict to plain dict
    result = []
    for r in rows:
        if hasattr(r, "keys"):
            result.append(dict(r))
        else:
            result.append(r)
    return {"items": result}

@app.post("/shipments")
async def add_shipment(req: Request):
    """
    Body JSON:
      {
        "label": "tên đơn",
        "carrier": "ghn|spx|vtp|jnt|mock",
        "code": "MÃ_VẬN_ĐƠN",
        "jnt_phone4": "4556"   # optional, required if jnt
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


# =============================
# Telegram webhook (optional) & polling
# =============================
def handle_update(update: Dict[str, Any]) -> None:
    """Xử lý 1 update của Telegram (getUpdates hoặc webhook)."""
    try:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
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
                tg_send(chat_id, "Không có đơn.")
                return
            lines = []
            for r in rows:
                d = dict(r) if hasattr(r, "keys") else r
                lines.append(
                    f"[{d['id']}] {d['label']} • {d['carrier'].upper()} • {d['tracking_code']}\n"
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

                tg_send(
                    chat_id,
                    f"✅ Thêm {carrier.upper()} {code}\nTên đơn: {label}\n{unified.latest.text}"
                )
            except Exception as e:
                tg_send(chat_id, f"❌ Lỗi: {e}")
            return

        elif cmd == "/check":
            if not args:
                tg_send(chat_id, "Cú pháp: /check <code|id>")
                return
            key = args[0]
            # tìm theo id hay code
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
            r = dict(row) if hasattr(row, "keys") else row
            tg_send(
                chat_id,
                f"[{r['id']}] {r['label']} • {r['carrier'].upper()} • {r['tracking_code']}\n"
                f"→ {r.get('last_status_text','')} | {r.get('last_checkpoint_time','')} | {r.get('last_location','')}"
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

            r = dict(row)
            carrier = r["carrier"]
            code = r["tracking_code"]

            vendor = _get_vendor_event(carrier, code)
            unified = unify(DEFAULT_CARRIER_FOR_UNIFY(carrier), code, vendor["latest_event"])
            changed = store.update_shipment_from_unified(sid, unified)
            tg_send(chat_id, f"🔄 {r['label']}: {unified.latest.text}\n({unified.latest.time_iso} | {unified.latest.location})")
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
            chat_id = update.get("message", {}).get("chat", {}).get("id") or TELEGRAM_CHAT_ID_DEFAULT
            if chat_id:
                tg_send(chat_id, f"⚠️ Lỗi xử lý update: {e}")
        except Exception:
            pass


@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    upd = await req.json()
    handle_update(upd)
    return {"ok": True}

# Optional: đơn giản polling để test (không cần nếu bạn dùng webhook)
def _poll_telegram_loop():
    if not TELEGRAM_BOT_TOKEN:
        return
    import requests
    offset = 0
    url = tg_api("getUpdates")
    while True:
        try:
            resp = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35)
            data = resp.json()
            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                handle_update(upd)
        except Exception:
            time.sleep(2)

# Start polling thread nếu muốn (bật qua env POLL_TELEGRAM=true)
if os.getenv("POLL_TELEGRAM", "false").lower() == "true":
    threading.Thread(target=_poll_telegram_loop, daemon=True).start()
