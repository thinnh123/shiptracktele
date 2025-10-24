import requests
from datetime import datetime, timezone

API_URL = "https://tramavandon.com/api/jtexpress.php"

HEADERS = {
    "accept": "*/*",
    "accept-language": "vi,en-US;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "origin": "https://tramavandon.com",
    "pragma": "no-cache",
    "referer": "https://tramavandon.com/jtexpress/?v=250918",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    ),
    "x-requested-with": "XMLHttpRequest",
}

def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()

def _extract_status(desc: str) -> str:
    if not desc:
        return "in_transit"
    s = desc.lower()
    if "ký nhận" in s or "đã giao" in s:
        return "delivered"
    if "đang giao" in s:
        return "delivering"
    if "chuyển đến" in s or "đang chuyển" in s:
        return "transporting"
    if "nhận hàng" in s:
        return "received"
    return "in_transit"

def _extract_location(desc: str) -> str:
    if not desc:
        return ""
    import re
    m = re.search(r"\(([^)]+)\)", desc)
    return m.group(1) if m else ""

def _latest_event(events: list) -> dict:
    if not events:
        return {"code": "in_transit", "text": "Không có dữ liệu", "location": "", "time": _now_iso()}
    # dữ liệu có cặp date + time: "YYYY-MM-DD" + "HH:MM:SS"
    def key(ev):
        try:
            return datetime.fromisoformat(f"{ev.get('date')}T{ev.get('time')}")
        except Exception:
            return datetime.min
    events_sorted = sorted(events, key=key, reverse=True)
    latest = events_sorted[0]
    desc = latest.get("description") or ""
    code = _extract_status(desc)
    loc = _extract_location(desc)
    t = f"{latest.get('date','')} {latest.get('time','')}".strip()
    try:
        t_iso = datetime.fromisoformat(t.replace(" ", "T")).astimezone(timezone.utc).isoformat()
    except Exception:
        t_iso = _now_iso()
    return {"code": code, "text": desc, "location": loc, "time": t_iso}

def get_tracking(tracking_code: str, tracking_phone: str = "") -> dict:
    """
    Gọi API J&T thông qua tramavandon.com.
    tracking_phone: 4 số cuối SĐT (bắt buộc, dạng string).
    """
    data = {"tracking_id": tracking_code, "tracking_phone": tracking_phone}
    try:
        resp = requests.post(API_URL, data=data, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except requests.HTTPError as e:
        raise RuntimeError(f"Lỗi HTTP J&T API: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Lỗi mạng J&T API: {e}") from e

    # Dạng lỗi đặc thù
    if isinstance(payload, dict) and payload.get("errors") == -1:
        msg = payload.get("message") or "Lỗi từ API J&T Express"
        raise RuntimeError(msg)
    if isinstance(payload, list) and not payload:
        raise RuntimeError("Không tìm thấy đơn hoặc sai 4 số cuối điện thoại.")

    events = payload if isinstance(payload, list) else []
    latest = _latest_event(events)
    return {"latest_event": latest, "carrier": "jnt", "tracking_code": tracking_code}
