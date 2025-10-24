import tkinter as tk
from .utils import center_window


class Notifier:
    def __init__(self, root: tk.Tk):
        self.root = root

    def toast(self, title: str, message: str, duration_ms: int = 3500):
        """Hiển thị thông báo popup giữa màn hình trong vài giây."""
        top = tk.Toplevel(self.root)
        top.title(title)
        top.transient(self.root)
        top.resizable(False, False)
        top.attributes('-topmost', True)

        lbl = tk.Label(top, text=message, padx=16, pady=12, justify="left")
        lbl.pack()

        top.update_idletasks()
        center_window(top)  # căn popup ra giữa màn hình

        # Tự đóng sau duration_ms mili-giây
        top.after(duration_ms, top.destroy)
