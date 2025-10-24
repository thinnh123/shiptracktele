import os
import requests
from datetime import datetime, timezone


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Thiếu biến môi trường {key} trong .env.")
    return val


def _endpoint() -> str:
    """
    Endpoint minh họa cho ViettelPost. Nhiều đối tác có URL riêng.
    Bạn có thể override bằng VTP_ENDPOINT trong .env.
    """
    return os.getenv("VTP_ENDPOINT", "https://partner.viettelpost.vn/v2/order/tracking")


def _to_latest_event(payload: dict) -> dict:
    """
    Chuyển payload VTP -> latest_event. Tuỳ hợp đồng mà cấu trúc sẽ khác.
    Dưới đây là cách làm an toàn: lấy status, location, time nếu có; thiếu thì fallback.
    """
    data = payload.get("data") or payload  # một số API trả trực tiếp ở root
    status_text = data.get("status_text") or data.get("STATUS") or "Đang xử lý"
    code = data.get("status_code") or data.get("STATUS_CODE") or "TRANSPORT"
    location = data.get("location") or data.get("CURRENT_POST") or ""

    time_str = (
        data.get("time_iso")
        or data.get("UPDATE_TIME")
        or datetime.now(timezone.utc).astimezone().isoformat()
    )
    try:
        _ = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except Exception:
        time_str = datetime.now(timezone.utc).astimezone().isoformat()

    return {
        "code": str(code),
        "text": str(status_text),
        "location": str(location),
        "time": time_str,
    }


def get_tracking(tracking_code: str) -> dict:
    """
    Gọi API ViettelPost (hoặc endpoint đối tác) để lấy trạng thái.
    Cần có: VTP_APP_ID, VTP_TOKEN (tuỳ yêu cầu đối tác).
    Trả về dict:
    {
      "latest_event": {...},
      "carrier": "vtp",
      "tracking_code": tracking_code
    }
    """
    app_id = _require("VTP_APP_ID")
    token = _require("VTP_TOKEN")
    url = _endpoint()

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "x-client-id": app_id,  # hoặc header khác tùy đối tác
    }

    try:
        # Tuỳ tài liệu có thể là GET/POST; ở đây minh hoạ GET.
        resp = requests.get(url, params={"code": tracking_code}, headers=headers, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        # Thử POST nếu GET fail, vì một số triển khai cần POST body JSON
        try:
            resp = requests.post(url, json={"code": tracking_code}, headers=headers, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as e2:
            raise RuntimeError(f"Lỗi gọi VTP API: {e2}") from e2

    latest = _to_latest_event(payload)
    return {"latest_event": latest, "carrier": "vtp", "tracking_code": tracking_code}
