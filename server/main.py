from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import requests

# Load biến môi trường
load_dotenv()

# ============================
#   Khởi tạo app FastAPI
# ============================
app = FastAPI(title="ShipTrack Server")

# Cho phép CORS (nếu bạn muốn gọi API từ web app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================
#   Các route cơ bản
# ============================
@app.get("/")
def root():
    """Trang gốc để kiểm tra server"""
    return {"message": "ShipTrack server is running"}

@app.get("/health")
def health():
    """Kiểm tra tình trạng hệ thống"""
    return {"ok": True}


# ============================
#   API ví dụ: cập nhật trạng thái đơn hàng
# ============================
@app.get("/refresh")
def refresh():
    """Ví dụ API test refresh"""
    return {"message": "Đã chạy cập nhật thủ công!"}


# ============================
#   Telegram Notifier (sẽ dùng sau)
# ============================
def send_telegram_message(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"

    if not enabled or not token or not chat_id:
        print("[Telegram] Chưa bật hoặc chưa cấu hình.")
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
        print("[Telegram] Gửi thông báo thành công.")
    except Exception as e:
        print(f"[Telegram] Lỗi: {e}")


# ============================
#   Scheduler auto refresh
# ============================
def auto_refresh_job():
    print("[Job] Đang chạy auto refresh đơn hàng...")
    # Ở đây bạn có thể gọi API GHN/SPX/J&T để cập nhật
    # Ví dụ gửi thông báo Telegram khi chạy
    send_telegram_message("🚚 ShipTrack: Đang tự động cập nhật đơn hàng!")


ENABLE_SCHED = os.getenv("SCHED_ENABLED", "true").lower() == "true"

if ENABLE_SCHED:
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_refresh_job, "interval", minutes=3, id="auto_refresh")
    scheduler.start()
    print("[Scheduler] Bật chế độ auto refresh mỗi 3 phút.")
else:
    print("[Scheduler] Đang tắt auto refresh.")


# ============================
#   Chạy app cục bộ (nếu cần test)
# ============================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
