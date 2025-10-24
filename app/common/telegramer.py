# app/common/telegramer.py
import os
import json
import threading
import urllib.parse
import urllib.request

ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TITLE = os.getenv("TELEGRAM_TITLE", "ShipTrack")

_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def _post(text: str, parse_mode: str = "Markdown"):
    if not (ENABLED and BOT_TOKEN and CHAT_ID):
        return False, "disabled_or_missing_env"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(_API, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
            ok = bool(payload.get("ok"))
            return ok, payload
    except Exception as e:
        return False, str(e)

def send_async(text: str, parse_mode: str = "Markdown"):
    # tránh block UI: gửi trong thread nhẹ
    def run():
        _post(text, parse_mode=parse_mode)
    threading.Thread(target=run, daemon=True).start()

def pretty_message(label: str, carrier: str, code: str, status_text: str, location: str, time_iso: str):
    title = f"*{TITLE}*"
    # escape tối thiểu cho Markdown
    def esc(s: str) -> str:
        return s.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    label = esc(label)
    carrier = esc(carrier.upper())
    code = esc(code)
    status_text = esc(status_text or "")
    location = esc(location or "")
    time_iso = esc(time_iso or "")

    lines = [
        f"{title} • Cập nhật trạng thái",
        f"*{label}*  ({carrier})",
        f"`{code}`",
        f"Trạng thái: *{status_text}*",
    ]
    if location:
        lines.append(f"Vị trí: {location}")
    if time_iso:
        lines.append(f"Thời gian: `{time_iso}`")
    return "\n".join(lines)
