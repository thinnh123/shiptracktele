from datetime import datetime, timedelta, timezone
import random

# ======================
# Dữ liệu mô phỏng (mock)
# ======================

_STATES = [
    {"code": "ready_to_pick", "text": "Đã tạo vận đơn"},
    {"code": "in_transit", "text": "Đang trung chuyển"},
    {"code": "delivery", "text": "Đang giao"},
    {"code": "delivered", "text": "Đã giao"},
]

_CITIES = ["Hà Nội", "Đà Nẵng", "TP.HCM", "Cần Thơ", "Nha Trang"]


def get_tracking(tracking_code: str) -> dict:
    """Trả về dữ liệu giả lập trạng thái đơn hàng (dùng để test offline)."""
    # Giả lập trạng thái ngẫu nhiên dựa vào mã vận đơn
    base = abs(hash(tracking_code)) % 4
    idx = min(base + random.choice([0, 0, 1]), 3)
    state = _STATES[idx]
    location = random.choice(_CITIES)

    now = datetime.now(timezone.utc).astimezone()
    event_time = now - timedelta(minutes=random.randint(5, 180))

    return {
        "latest_event": {
            "code": state["code"],
            "text": state["text"],
            "location": location,
            "time": event_time.isoformat(),
        },
        "carrier": "mock",
        "tracking_code": tracking_code,
    }
