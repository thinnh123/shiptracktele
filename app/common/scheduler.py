import threading
import time
from typing import Callable


class Scheduler:
    """Chạy một tác vụ định kỳ trên luồng nền (background thread)."""

    def __init__(self, interval_sec: int, task: Callable[[], None]):
        self.interval = interval_sec
        self.task = task
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        """Bắt đầu luồng định kỳ."""
        self._thread.start()

    def stop(self):
        """Dừng luồng định kỳ."""
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self):
        """Vòng lặp chạy định kỳ."""
        while not self._stop.is_set():
            try:
                self.task()
            except Exception as e:
                # Có thể log lỗi vào file sau này nếu cần
                pass

            # Ngủ từng giây nhỏ để có thể dừng nhanh khi cần
            for _ in range(self.interval):
                if self._stop.is_set():
                    return
                time.sleep(1)
