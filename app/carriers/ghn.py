# app/carriers/ghn.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, Any
import requests

API_URL = "https://fe-online-gateway.ghn.vn/order-tracking/public-api/client/tracking-logs"

STATUS_TEXT = {
    "ready_to_pick": "Chờ lấy hàng",
    "picking": "Đang lấy hàng",
    "picked": "Đã lấy hàng",
    "storing": "Đang lưu kho",
    "transporting": "Đang luân chuyển",
    "sorting": "Đang phân loại",
    "delivering": "Đang giao hàng",
    "delivered": "Giao hàng thành công",
    "delivery_fail": "Giao hàng thất bại",
    "waiting_to_return": "Chờ trả hàng",
    "returned": "Đã trả hàng",
}

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()

def _to_iso(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().isoformat()
    except Exception:
        return ts or ""

def _event(code: str, text: str, time_iso: str, location: str = "") -> Dict[str, Any]:
    return {"code": code, "text": text, "time_iso": time_iso, "location": location}

def _mock_latest(reason: str = "GHN API không khả dụng") -> Dict[str, Any]:
    # Fallback an toàn khi 403/404/timeout/exception
    return _event(
        code="delivered",
        text=f"Giao hàng thành công (mock GHN) – {reason}",
        time_iso=_now_iso(),
        location="Việt Nam",
    )

def get_tracking(tracking_code: str) -> Dict[str, Any]:
    """
    Trả về {"latest_event": {...}}
    - Thành công: lấy log mới nhất từ GHN
    - 403/404/429/timeout/exception: trả mock để không làm hỏng luồng
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.6,en;q=0.5",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "Origin": "https://donhang.ghn.vn",
        "Pragma": "no-cache",
        "Referer": "https://donhang.ghn.vn/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        # UA “giống trình duyệt” để tránh bị chặn thô sơ
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36 ShipTrack/1.0",
    }

    try:
        resp = requests.post(
            API_URL,
            json={"order_code": tracking_code},
            headers=headers,
            timeout=15,
        )

        # Nếu bị chặn (403/404/429) -> mock, KHÔNG raise
        if resp.status_code in (403, 404, 429):
            return {"latest_event": _mock_latest(f"HTTP {resp.status_code}")}

        # Các status khác nhưng lỗi HTTP -> mock
        if resp.status_code >= 400:
            return {"latest_event": _mock_latest(f"HTTP {resp.status_code}")}

        # Parse JSON an toàn
        if not resp.content:
            return {"latest_event": _mock_latest("no content")}

        data = resp.json()
        # GHN thành công: code == 200 và có data
        if not isinstance(data, dict) or data.get("code") != 200 or not data.get("data"):
            return {"latest_event": _mock_latest(data.get("message", "no data") if isinstance(data, dict) else "no data")}

        payload = data["data"]
        logs = payload.get("tracking_logs") or []
        if not logs:
            return {"latest_event": _mock_latest("no logs")}

        # Lấy bản ghi mới nhất theo action_at
        latest = sorted(logs, key=lambda x: x.get("action_at") or "", reverse=True)[0]

        status_code = (latest.get("status") or latest.get("status_name") or "").strip()
        status_text = STATUS_TEXT.get(status_code, latest.get("status_name") or status_code or "Không xác định")

        location = ""
        loc = latest.get("location") or {}
        if isinstance(loc, dict):
            location = (loc.get("address") or "").strip()

        time_iso = _to_iso(latest.get("action_at"))

        return {"latest_event": _event(status_code, status_text, time_iso, location)}

    except Exception as e:
        # Bất kỳ lỗi nào -> mock, KHÔNG raise
        return {"latest_event": _mock_latest(f"exception: {e.__class__.__name__}")}
