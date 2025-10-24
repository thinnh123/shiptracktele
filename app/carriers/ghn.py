# app/carriers/ghn.py
import requests
from datetime import datetime, timezone

API_URL = "https://fe-online-gateway.ghn.vn/order-tracking/public-api/client/tracking-logs"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
    "Cache-Control": "no-cache",
    "Content-Type": "application/json",
    "Origin": "https://donhang.ghn.vn",
    "Pragma": "no-cache",
    "Referer": "https://donhang.ghn.vn/",
    "Sec-Ch-Ua": "\"Microsoft Edge\";v=\"141\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"141\"",
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": "\"Windows\"",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
}

def _parse_time(s: str | None) -> str:
    if not s:
        return datetime.now(timezone.utc).astimezone().isoformat()
    try:
        # chuẩn hoá 'Z' -> +00:00 nếu có
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().isoformat()
    except Exception:
        return datetime.now(timezone.utc).astimezone().isoformat()

def _latest_from_tracking_logs(data: dict) -> dict:
    """
    data = payload['data'] theo GHN.
    Chọn log mới nhất dựa theo 'action_at' (nếu có), fallback lấy phần tử cuối.
    """
    logs = data.get("tracking_logs") or []
    if not logs:
        return {
            "code": "unknown",
            "text": "Không có dữ liệu tracking",
            "location": "",
            "time": datetime.now(timezone.utc).astimezone().isoformat(),
        }

    # Sắp xếp theo action_at tăng dần, rồi lấy phần tử cuối
    def keyfn(l):  # an toàn nếu thiếu action_at
        t = l.get("action_at") or l.get("updated_date") or l.get("time")
        try:
            return datetime.fromisoformat((t or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min
    logs_sorted = sorted(logs, key=keyfn)
    latest = logs_sorted[-1]

    status = latest.get("status") or latest.get("status_name") or "in_transit"
    desc   = latest.get("status_name") or latest.get("description") or status
    loc    = (latest.get("location") or {}).get("address") or latest.get("location") or ""
    tstr   = latest.get("action_at") or latest.get("updated_date") or latest.get("time")

    return {
        "code": str(status),
        "text": str(desc),
        "location": str(loc),
        "time": _parse_time(tstr),
    }

def get_tracking(tracking_code: str) -> dict:
    """
    Gọi GHN public tracking (POST) với headers đầy đủ như client web.
    Yêu cầu .env: USE_MOCK=false (không cần GHN_TOKEN).
    Trả về:
      {"latest_event": {code,text,location,time}, "carrier":"ghn", "tracking_code":...}
    """
    try:
        resp = requests.post(
            API_URL,
            json={"order_code": tracking_code},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.HTTPError as e:
        # Hiển thị rõ mã lỗi HTTP (ví dụ 404, 403 ...)
        raise RuntimeError(f"Lỗi gọi GHN API: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Lỗi mạng GHN: {e}") from e

    # Theo client web: code == 200 mới là OK
    code = payload.get("code")
    if code != 200:
        msg = payload.get("message") or "Không tìm thấy thông tin đơn hàng"
        raise RuntimeError(f"GHN trả về code {code}: {msg}")

    data = payload.get("data") or {}
    latest = _latest_from_tracking_logs(data)

    return {
        "latest_event": latest,
        "carrier": "ghn",
        "tracking_code": tracking_code,
    }
