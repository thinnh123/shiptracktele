import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from dotenv import load_dotenv
import csv

from common import store
from common.normalizer import unify
from common.notifier import Notifier
from common.scheduler import Scheduler

# Carriers
from carriers import mock, ghn, spx, vtp, jnt

# =============================
#  Cấu hình ban đầu
# =============================
load_dotenv()
USE_MOCK = os.getenv("USE_MOCK", "true").lower() == "true"

CARRIER_BACKENDS = {
    "mock": mock,
    "ghn": ghn,
    "spx": spx,
    "vtp": vtp,
    "jnt": jnt,  # J&T Express
}

DEFAULT_CARRIER = "mock" if USE_MOCK else "ghn"


# =============================
#  Ứng dụng chính
# =============================
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ShipTrack – Theo dõi đơn hàng")
        self.notifier = Notifier(root)

        # Cache 4 số cuối SĐT cho J&T theo mã (chỉ lưu trong phiên)
        self.jnt_phone_cache: dict[str, str] = {}

        store.init_db()
        self._build_ui()
        self._load_table()

        # Scheduler tự động refresh mỗi 3 phút
        self.scheduler = Scheduler(180, self.refresh_auto)
        self.scheduler.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -----------------------------
    # UI
    # -----------------------------
    def _build_ui(self):
        frm_top = ttk.Frame(self.root, padding=8)
        frm_top.pack(fill="x")

        ttk.Label(frm_top, text="Tên gợi nhớ").grid(row=0, column=0, sticky="w")
        self.ent_label = ttk.Entry(frm_top, width=28)
        self.ent_label.grid(row=1, column=0, padx=(0, 8))

        ttk.Label(frm_top, text="Mã vận đơn").grid(row=0, column=1, sticky="w")
        self.ent_code = ttk.Entry(frm_top, width=28)
        self.ent_code.grid(row=1, column=1, padx=(0, 8))

        ttk.Label(frm_top, text="Hãng").grid(row=0, column=2, sticky="w")
        self.cmb_carrier = ttk.Combobox(
            frm_top, values=["mock", "ghn", "spx", "vtp", "jnt"], width=10
        )
        self.cmb_carrier.set(DEFAULT_CARRIER)
        self.cmb_carrier.grid(row=1, column=2, padx=(0, 8))

        self.btn_add = ttk.Button(frm_top, text="Thêm", command=self.on_add)
        self.btn_add.grid(row=1, column=3, padx=(0, 8))

        self.btn_refresh_all = ttk.Button(
            frm_top, text="Cập nhật tất cả", command=self.on_refresh_all
        )
        self.btn_refresh_all.grid(row=1, column=4)

        # Enter để thêm nhanh
        self.root.bind("<Return>", lambda e: self.on_add())

        # Bảng
        cols = ("label", "carrier", "code", "status", "time", "location", "auto")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", height=16)
        for c, w in zip(cols, [170, 70, 140, 220, 180, 220, 60]):
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)

        # Double-click mở sửa
        self.tree.bind("<Double-1>", self.on_edit_dialog)

        # Context menu (chuột phải)
        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="Xem chi tiết", command=self.on_view_detail)
        self.ctx_menu.add_command(label="Chỉnh sửa đơn", command=self.on_edit_dialog)
        self.tree.bind("<Button-3>", self.on_context_menu)          # Windows/Linux
        self.tree.bind("<Control-Button-1>", self.on_context_menu)  # dự phòng

        # Thanh dưới
        frm_bot = ttk.Frame(self.root, padding=8)
        frm_bot.pack(fill="x")
        ttk.Button(frm_bot, text="Cập nhật dòng chọn", command=self.on_refresh_selected).pack(side="left")
        ttk.Button(frm_bot, text="Xoá dòng chọn", command=self.on_delete_selected).pack(side="left", padx=8)
        ttk.Button(frm_bot, text="Xuất CSV", command=self.export_csv).pack(side="left")
        ttk.Button(frm_bot, text="Nhập CSV", command=self.import_csv).pack(side="left", padx=8)

    # -----------------------------
    # Tải dữ liệu vào bảng
    # -----------------------------
    def _load_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for row in store.list_shipments():
            self.tree.insert(
                "",
                "end",
                iid=row["id"],
                values=(
                    row["label"],
                    row["carrier"],
                    row["tracking_code"],
                    row["last_status_text"],
                    row["last_checkpoint_time"],
                    row["last_location"],
                    "✓" if row["auto_poll"] else "",
                ),
            )

    # -----------------------------
    # Chuột phải
    # -----------------------------
    def on_context_menu(self, event):
        rowid = self.tree.identify_row(event.y)
        if rowid:
            self.tree.selection_set(rowid)
            try:
                self.ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.ctx_menu.grab_release()

    # -----------------------------
    # Thêm đơn
    # -----------------------------
    def on_add(self):
        label = self.ent_label.get().strip() or "(Không tên)"
        code = self.ent_code.get().strip()
        carrier = self.cmb_carrier.get().strip()
        if not code:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng nhập mã vận đơn")
            return

        phone = None
        if carrier == "jnt":
            phone = self.jnt_phone_cache.get(code)
            if not phone:
                phone = simpledialog.askstring(
                    "J&T Express",
                    "Nhập 4 số cuối số điện thoại để tra cứu:",
                    parent=self.root,
                )
            if not phone or not phone.isdigit() or len(phone) != 4:
                messagebox.showinfo("Thiếu SĐT", "Bạn cần nhập đúng 4 số cuối SĐT cho J&T.")
                return
            self.jnt_phone_cache[code] = phone

        self._fetch_and_add(label, carrier, code, phone)

    def _fetch_and_add(self, label, carrier, code, phone=None):
        def work():
            try:
                backend = CARRIER_BACKENDS[carrier]
                if carrier == "jnt":
                    vendor = backend.get_tracking(code, phone or "")
                else:
                    vendor = backend.get_tracking(code)

                unified = unify(
                    carrier if carrier != "mock" else "ghn",
                    code,
                    vendor["latest_event"],
                )
                store.add_shipment(label, carrier, code, unified)
                self.root.after(
                    0,
                    lambda: (
                        self._load_table(),
                        self.notifier.toast("Thêm thành công", f"Đã thêm: {label}"),
                    ),
                )
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda m=err: messagebox.showerror("Lỗi", m))
        threading.Thread(target=work, daemon=True).start()

    # -----------------------------
    # Cập nhật
    # -----------------------------
    def on_refresh_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Chọn dòng", "Hãy chọn một đơn để cập nhật")
            return
        self._refresh_one(int(sel[0]))

    def on_refresh_all(self):
        for iid in self.tree.get_children():
            self._refresh_one(int(iid))

    def _refresh_one(self, shipment_id: int):
        def start_work(phone_for_jnt=None):
            def work():
                try:
                    con = store.connect()
                    row = con.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
                    con.close()

                    carrier = row["carrier"]
                    code = row["tracking_code"]
                    backend = CARRIER_BACKENDS[carrier]

                    if carrier == "jnt":
                        vendor = backend.get_tracking(code, phone_for_jnt or "")
                    else:
                        vendor = backend.get_tracking(code)

                    unified = unify(
                        carrier if carrier != "mock" else "ghn",
                        code,
                        vendor["latest_event"],
                    )
                    changed = store.update_shipment_from_unified(shipment_id, unified)

                    def ui():
                        self._load_table()
                        if changed:
                            self.notifier.toast(
                                "Trạng thái mới",
                                f"{row['label']} → {unified.latest.text} ({unified.latest.location})\n{unified.latest.time_iso}",
                            )
                    self.root.after(0, ui)
                except Exception as e:
                    err = str(e)
                    self.root.after(0, lambda m=err: messagebox.showerror("Lỗi", m))
            threading.Thread(target=work, daemon=True).start()

        # Hỏi phone cho J&T (trên main thread)
        con = store.connect()
        row = con.execute("SELECT carrier, tracking_code FROM shipments WHERE id=?", (shipment_id,)).fetchone()
        con.close()
        carrier = row["carrier"]
        code = row["tracking_code"]

        if carrier == "jnt":
            phone = self.jnt_phone_cache.get(code)
            if phone and phone.isdigit() and len(phone) == 4:
                start_work(phone)
                return

            def ask_and_go():
                p = simpledialog.askstring(
                    "J&T Express",
                    f"Nhập 4 số cuối SĐT cho {code}:",
                    parent=self.root,
                )
                if not p or not p.isdigit() or len(p) != 4:
                    messagebox.showinfo("Thiếu SĐT", "Không có 4 số cuối SĐT nên không thể cập nhật đơn J&T này.")
                    return
                self.jnt_phone_cache[code] = p
                start_work(p)
            self.root.after(0, ask_and_go)
            return

        start_work()

    # -----------------------------
    # Xem chi tiết
    # -----------------------------
    def on_view_detail(self, event=None):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Chọn dòng", "Hãy chọn một đơn để xem chi tiết")
            return
        sid = int(sel[0])

        con = store.connect()
        row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        con.close()
        if not row:
            messagebox.showerror("Lỗi", "Không tìm thấy dữ liệu đơn hàng.")
            return

        # sqlite3.Row -> dict (và thay None = "")
        r = {k: (row[k] if row[k] is not None else "") for k in row.keys()}

        jnt_phone = ""
        if r["carrier"] == "jnt":
            jnt_phone = getattr(self, "jnt_phone_cache", {}).get(r["tracking_code"], "")

        detail = []
        detail.append(f"ID: {r['id']}")
        detail.append(f"Label: {r['label']}")
        detail.append(f"Carrier: {r['carrier']}")
        detail.append(f"Tracking code: {r['tracking_code']}")
        if jnt_phone:
            detail.append(f"J&T 4 số SĐT: {jnt_phone}")
        detail.append(f"Last status code: {r['last_status_code']}")
        detail.append(f"Last status text: {r['last_status_text']}")
        detail.append(f"Last checkpoint time: {r['last_checkpoint_time']}")
        detail.append(f"Last location: {r['last_location']}")
        detail.append(f"Auto poll: {'Có' if r['auto_poll'] else 'Không'}")
        detail.append(f"Created at: {r['created_at']}")
        detail.append(f"Updated at: {r['updated_at']}")
        text_all = "\n".join(detail)

        top = tk.Toplevel(self.root)
        top.title("Chi tiết đơn hàng")
        top.transient(self.root)
        top.resizable(True, True)

        txt = tk.Text(top, wrap="word", height=18, width=100)
        txt.insert("1.0", text_all)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        frm_btn = ttk.Frame(top)
        frm_btn.pack(fill="x", pady=8, padx=10)

        def copy_all():
            self.root.clipboard_clear()
            self.root.clipboard_append(text_all)
            self.notifier.toast("Đã sao chép", "Thông tin chi tiết đã copy vào clipboard", 2000)

        ttk.Button(frm_btn, text="Sao chép", command=copy_all).pack(side="left")
        ttk.Button(
            frm_btn, text="Cập nhật trạng thái",
            command=lambda: (top.destroy(), self.on_refresh_selected())
        ).pack(side="left", padx=8)
        ttk.Button(frm_btn, text="Đóng", command=top.destroy).pack(side="right")

        from common.utils import center_window
        center_window(top)

    # -----------------------------
    # Chỉnh sửa đơn (sửa tên, mã, hãng, auto)
    # -----------------------------
    def on_edit_dialog(self, event=None):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Chọn dòng", "Hãy chọn một đơn để chỉnh sửa")
            return
        sid = int(sel[0])
        con = store.connect()
        row = con.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        con.close()

        top = tk.Toplevel(self.root)
        top.title("Chỉnh sửa đơn")
        top.transient(self.root)
        top.resizable(False, False)

        ttk.Label(top, text="Tên gợi nhớ").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 0))
        ent_label = ttk.Entry(top, width=32)
        ent_label.insert(0, row["label"])
        ent_label.grid(row=1, column=0, padx=8, pady=(0, 8))

        ttk.Label(top, text="Mã vận đơn").grid(row=2, column=0, sticky="w", padx=8)
        ent_code = ttk.Entry(top, width=32)
        ent_code.insert(0, row["tracking_code"])
        ent_code.grid(row=3, column=0, padx=8, pady=(0, 8))

        ttk.Label(top, text="Hãng").grid(row=4, column=0, sticky="w", padx=8)
        cmb_carrier = ttk.Combobox(top, values=["mock", "ghn", "spx", "vtp", "jnt"], width=10)
        cmb_carrier.set(row["carrier"])
        cmb_carrier.grid(row=5, column=0, padx=8, pady=(0, 8))

        ttk.Label(top, text="Tự động cập nhật").grid(row=6, column=0, sticky="w", padx=8)
        var_auto = tk.BooleanVar(value=bool(row["auto_poll"]))
        ttk.Checkbutton(top, variable=var_auto).grid(row=7, column=0, sticky="w", padx=8, pady=(0, 8))

        ent_jnt = None
        if row["carrier"] == "jnt":
            ttk.Label(top, text="(J&T) 4 số cuối SĐT (tùy chọn – chỉ lưu trong phiên)").grid(row=8, column=0, sticky="w", padx=8)
            ent_jnt = ttk.Entry(top, width=10)
            old_phone = self.jnt_phone_cache.get(row["tracking_code"], "")
            if old_phone:
                ent_jnt.insert(0, old_phone)
            ent_jnt.grid(row=9, column=0, sticky="w", padx=8, pady=(0, 8))

        def save():
            new_label = ent_label.get().strip() or "(Không tên)"
            new_code = ent_code.get().strip()
            new_carrier = cmb_carrier.get().strip()
            auto = 1 if var_auto.get() else 0

            if not new_code:
                messagebox.showwarning("Thiếu dữ liệu", "Mã vận đơn không được trống.")
                return

            con = store.connect()
            with con:
                con.execute(
                    "UPDATE shipments SET label=?, carrier=?, tracking_code=?, auto_poll=?, updated_at=? WHERE id=?",
                    (new_label, new_carrier, new_code, auto, store.now_iso(), sid),
                )
            con.close()

            if ent_jnt:
                phone = ent_jnt.get().strip()
                if phone and phone.isdigit() and len(phone) == 4:
                    self.jnt_phone_cache[new_code] = phone
            if row["tracking_code"] != new_code:
                self.jnt_phone_cache.pop(row["tracking_code"], None)

            self._load_table()
            top.destroy()

        ttk.Button(top, text="Lưu", command=save).grid(row=10, column=0, padx=8, pady=8, sticky="e")
        top.bind("<Return>", lambda e: save())

        from common.utils import center_window
        center_window(top)

    # -----------------------------
    # Xoá / CSV
    # -----------------------------
    def on_delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        sid = int(sel[0])
        if messagebox.askyesno("Xoá", "Bạn có chắc muốn xoá đơn này?"):
            store.delete_shipment(sid)
            self._load_table()

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = store.list_shipments()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["label","carrier","tracking_code","last_status_code","last_status_text","last_checkpoint_time","last_location","auto_poll"])
            for r in rows:
                w.writerow([r["label"],r["carrier"],r["tracking_code"],r["last_status_code"],r["last_status_text"],r["last_checkpoint_time"],r["last_location"],r["auto_poll"]])
        messagebox.showinfo("Xuất CSV", "Đã xuất thành công")

    def import_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not path:
            return
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for row in rd:
                try:
                    con = store.connect()
                    with con:
                        con.execute(
                            "INSERT OR IGNORE INTO shipments(label, carrier, tracking_code, last_status_code, last_status_text, last_checkpoint_time, last_location, auto_poll, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                row.get("label") or "(Không tên)",
                                row.get("carrier") or DEFAULT_CARRIER,
                                row.get("tracking_code"),
                                row.get("last_status_code") or "",
                                row.get("last_status_text") or "",
                                row.get("last_checkpoint_time") or "",
                                row.get("last_location") or "",
                                int(row.get("auto_poll") or 1),
                                store.now_iso(),
                                store.now_iso(),
                            ),
                        )
                    count += 1
                except Exception:
                    pass
        self._load_table()
        messagebox.showinfo("Nhập CSV", f"Đã nhập {count} dòng")

    # -----------------------------
    # Auto refresh & đóng app
    # -----------------------------
    def refresh_auto(self):
        con = store.connect()
        rows = con.execute("SELECT id FROM shipments WHERE auto_poll=1").fetchall()
        con.close()
        for r in rows:
            self._refresh_one(int(r["id"]))

    def on_close(self):
        try:
            self.scheduler.stop()
        except Exception:
            pass
        self.root.destroy()


# =============================
#  Run app
# =============================
if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
