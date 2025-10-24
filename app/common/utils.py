import tkinter as tk


def center_window(win: tk.Toplevel | tk.Tk):
    """Đưa cửa sổ (popup hoặc chính) ra giữa màn hình."""
    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = int((sw - w) / 2)
    y = int((sh - h) / 3)
    win.geometry(f"{w}x{h}+{x}+{y}")
