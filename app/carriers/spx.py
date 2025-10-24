import requests
from datetime import datetime, timezone

BASE_URL = "https://spx.vn/shipment/order/open/order/get_order_info"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    # cookie thường không bắt buộc với endpoint open/, thêm nhẹ để giống browser
    "cookie": "spx_token=0; spx_sid=0; login_status=true; nss_sys_type=true; nss_cid=VN",
    "dnt": "1",
    "priority": "u=1, i",
    "referer": "https://spx.vn/track",
    "sec-ch-ua": "\"Not?A_Brand\";v=\"99\", \"Chromium\";v=\"131\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "sec-gpc": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


def _parse_unix(ts) -> str:
    """SPX trả actual_time là UNIX seconds. Trả ISO local nếu có, nếu không thì now()."""
    if ts is None:
        return datetime.now(timezone.utc).astimezone().isoformat()
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().isoformat()
    except Exception:
        return datetime.now(timezone.utc).astimezone().isoformat()


def _classify_code(name_or_code: str) -> str:
    """
    Map về các mã mà normalizer.py đã hỗ trợ cho SPX:
      PICKED / TRANSIT / OFD / DLV / FAIL
    """
    if not name_or_code:
        return "TRANSIT"
    s = name_or_code.lower()
    if "delivered" in s or "đã giao" in s or s == "dlv":
        return "DLV"
    if "out for delivery" in s or "đang giao" in s or s == "ofd":
        return "OFD"
    if "in transit" in s or "vận chuyển" in s or "sorting" in s or "transport" in s or s == "transit":
        return "TRANSIT"
    if "pickup" in s or "picked" in s or "collected" in s or "đã lấy hàng" in s:
        return "PICKED"
    if "unsuccessful" in s or "fail" in s or "failed" in s or "return" in s:
        return "FAIL"
    return "TRANSIT"


def _latest_event_from_payload(payload: dict) -> dict:
    """
    Lấy event mới nhất từ response SPX:
      payload -> data -> sls_tracking_info -> records[]
    Event gồm: code/text/location/time
    """
    data = (payload or {}).get("data") or {}
    ti = data.get("sls_tracking_info") or {}

    records = ti.get("records") or []
    if not records:
        # fallback nếu không có record nào
        return {
            "code": "TRANSIT",
            "text": "Không có dữ liệu tracking",
            "location": "",
            "time": datetime.now(timezone.utc).astimezone().isoformat(),
        }

    # Lọc các record hiển thị (display_flag=1) rồi sort theo actual_time giảm dần
    visible = [r for r in records if r.get("display_flag", 1) == 1]
    src = visible if visible else records
    src.sort(key=lambda r: r.get("actual_time") or 0, reverse=True)

    latest = src[0]

    # Text/Location/Time
    status_name = latest.get("tracking_name") or latest.get("milestone_name") or latest.get("tracking_code") or "In transit"
    desc = latest.get("buyer_description") or latest.get("description") or status_name

    loc = ""
    cur_loc = latest.get("current_location") or {}
    next_loc = latest.get("next_location") or {}
    loc = cur_loc.get("full_address") or cur_loc.get("location_name") or next_loc.get("location_name") or ""

    time_iso = _parse_unix(latest.get("actual_time"))

    # Code (để normalizer map đúng nhóm trạng thái)
    code = _classify_code(status_name)

    return {
        "code": code,
        "text": str(desc),
        "location": str(loc),
        "time": time_iso,
    }


def get_tracking(tracking_code: str) -> dict:
    """
    Gọi SPX open API, kiểm tra retcode/message, bóc latest_event.
    Trả về:
      {"latest_event": {...}, "carrier": "spx", "tracking_code": tracking_code}
    """
    params = {"spx_tn": tracking_code, "language_code": "vi"}
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except requests.HTTPError as e:
        # ví dụ 403/404 -> báo lỗi để UI hiển thị
        raise RuntimeError(f"Lỗi gọi SPX API: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Lỗi mạng SPX: {e}") from e

    # Kiểm tra theo NodeJS của bạn: retcode == 0 và message == "success"
    if payload.get("retcode") != 0 or payload.get("message") != "success":
        detail = payload.get("detail") or payload.get("message") or "Không tìm thấy đơn"
        raise RuntimeError(f"SPX trả về lỗi: {detail}")

    latest = _latest_event_from_payload(payload)
    return {"latest_event": latest, "carrier": "spx", "tracking_code": tracking_code}
