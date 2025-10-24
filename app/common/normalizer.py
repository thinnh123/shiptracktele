from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any

# ======================
# Các trạng thái chuẩn hóa
# ======================

CREATED = "CREATED"
PICKED_UP = "PICKED_UP"
IN_TRANSIT = "IN_TRANSIT"
OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
DELIVERED = "DELIVERED"
EXCEPTION = "EXCEPTION"


# ======================
# Cấu trúc dữ liệu
# ======================

@dataclass
class UnifiedEvent:
    code: str
    text: str
    location: Optional[str]
    time_iso: str
    raw: Dict[str, Any]


@dataclass
class UnifiedTrack:
    carrier: str
    tracking_code: str
    latest: UnifiedEvent


# ======================
# Mapping trạng thái theo từng hãng
# ======================

_GHN_MAP = {
    "ready_to_pick": PICKED_UP,
    "in_transit": IN_TRANSIT,
    "delivery": OUT_FOR_DELIVERY,
    "delivered": DELIVERED,
    "delivery_fail": EXCEPTION,
}

_SPX_MAP = {
    "PICKED": PICKED_UP,
    "TRANSIT": IN_TRANSIT,
    "OFD": OUT_FOR_DELIVERY,
    "DLV": DELIVERED,
    "FAIL": EXCEPTION,
}

_VTP_MAP = {
    "PICKUP_SUCCESS": PICKED_UP,
    "TRANSPORT": IN_TRANSIT,
    "DELIVERING": OUT_FOR_DELIVERY,
    "DELIVERED": DELIVERED,
    "RETURN": EXCEPTION,
}

CARRIER_MAP = {
    "ghn": _GHN_MAP,
    "spx": _SPX_MAP,
    "vtp": _VTP_MAP,
}


# ======================
# Hàm chuẩn hóa
# ======================

def unify(carrier: str, tracking_code: str, vendor_event: dict) -> UnifiedTrack:
    """
    Chuyển dữ liệu trạng thái của hãng → UnifiedTrack (chuẩn thống nhất).
    vendor_event ví dụ:
    {
        "code": "in_transit",
        "text": "Đang trung chuyển",
        "location": "Kho HCM",
        "time": "2025-10-24T08:30:00+07:00"
    }
    """

    code_raw = vendor_event.get("code") or vendor_event.get("status") or "unknown"
    text = vendor_event.get("text") or vendor_event.get("desc") or code_raw
    location = vendor_event.get("location")
    time_iso = vendor_event.get("time")  # đã là ISO từ layer carrier

    mapped = CARRIER_MAP.get(carrier, {}).get(code_raw, None)
    if mapped:
        code_unified = mapped
    else:
        # fallback: nếu có chữ "fail" thì coi là EXCEPTION, còn lại IN_TRANSIT
        code_unified = EXCEPTION if "fail" in code_raw.lower() else IN_TRANSIT

    evt = UnifiedEvent(
        code=code_unified,
        text=text,
        location=location,
        time_iso=time_iso,
        raw=vendor_event,
    )

    return UnifiedTrack(
        carrier=carrier,
        tracking_code=tracking_code,
        latest=evt,
    )
