#!/usr/bin/env python3
"""
Ping Monitor v1.2 — Gouziotis Kostas
Ελέγχει διαθεσιμότητα hosts με ping, καταγράφει uptime/downtime.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import threading
import time
import csv
import json
import os
import platform
from datetime import datetime

LOG_FILE = "ping_monitor_log.json"
CONFIG_FILE = "ping_monitor_config.json"

def ping_host(addr, count=1, timeout=2):
    """Κάνει ping σε host. Επιστρέφει (online: bool, latency_ms: float|None)."""
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), addr]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), addr]
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": timeout + 1}
        if platform.system().lower() == "windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, **kwargs)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            # Εξαγωγή latency
            latency = None
            for token in output.split():
                for prefix in ("time=", "time<"):
                    if token.lower().startswith(prefix):
                        try:
                            latency = float(token.split("=")[-1].replace("ms","").replace("<","").strip())
                        except:
                            pass
            return True, latency
        return False, None
    except Exception:
        return False, None


class HostEntry:
    def __init__(self, addr):
        self.addr = addr
        self.status = "unknown"   # "up" | "down" | "unknown"
        self.up_since = None
        self.down_since = None
        self.total_uptime = 0.0    # seconds
        self.total_downtime = 0.0  # seconds
        self.checks = 0
        self.success_checks = 0
        self.fail_count = 0        # transitions to offline
        self.last_check = None
        self.last_latency = None

    def uptime_pct(self):
        if self.checks == 0:
            return "—"
        return f"{(self.success_checks / self.checks * 100):.1f}%"

    def total_uptime_str(self):
        return self._fmt(self.total_uptime)

    def total_downtime_str(self):
        return self._fmt(self.total_downtime)

    def live_downtime_str(self):
        """Downtime incl. ongoing offline period. Safe to call from main thread."""
        status = self.status
        down_since = self.down_since
        total = self.total_downtime
        extra = 0.0
        if status == "down" and down_since:
            extra = max(0.0, (datetime.now() - down_since).total_seconds())
        return self._fmt(total + extra)

    def _fmt(self, secs):
        if secs < 60:
            return f"{int(secs)}s"
        elif secs < 3600:
            return f"{int(secs//60)}m {int(secs%60)}s"
        else:
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            return f"{h}h {m}m"


class PingMonitorApp(tk.Tk):
    INTERVALS = [("10 δευτ.", 10), ("30 δευτ.", 30), ("1 λεπτό", 60),
                 ("2 λεπτά", 120), ("5 λεπτά", 300)]
    STATUS_COLORS = {"up": "#3B6D11", "down": "#A32D2D", "unknown": "#888780"}
    STATUS_BG     = {"up": "#EAF3DE", "down": "#FCEBEB",  "unknown": "#F1EFE8"}
    STATUS_LABELS = {"up": "Online", "down": "Offline", "unknown": "Unknown"}

    def __init__(self):
        super().__init__()
        self.title("Ping Monitor v1.2")
        self.geometry("900x640")
        self.minsize(750, 500)
        self.configure(bg="#F8F8F6")

        self.hosts = []          # list of HostEntry
        self.log_entries = []    # list of dicts
        self.running = False
        self._timer = None
        self._lock = threading.Lock()
        self._interval_var = tk.IntVar(value=30)
        self._check_counter = 0

        self._build_ui()
        self._load_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─── UI BUILD ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg="#F8F8F6", pady=10, padx=14)
        top.pack(fill="x")

        tk.Label(top, text="Ping Monitor", font=("Segoe UI", 16, "bold"),
                 bg="#F8F8F6", fg="#1A1A18").pack(side="left")

        self._status_lbl = tk.Label(top, text="● Σταματημένο",
            font=("Segoe UI", 11), bg="#F1EFE8", fg="#5F5E5A",
            padx=10, pady=3, relief="flat")
        self._status_lbl.pack(side="left", padx=14)

        # Add host row
        add_frame = tk.Frame(self, bg="#F8F8F6", padx=14, pady=4)
        add_frame.pack(fill="x")

        self._host_var = tk.StringVar()
        entry = ttk.Entry(add_frame, textvariable=self._host_var, width=36,
                          font=("Segoe UI", 12))
        entry.pack(side="left", padx=(0, 8))
        entry.bind("<Return>", lambda e: self._add_host())
        entry.insert(0, "IP ή hostname...")
        entry.bind("<FocusIn>", lambda e: (entry.delete(0, "end")
                    if self._host_var.get() == "IP ή hostname..." else None))

        ttk.Button(add_frame, text="+ Προσθήκη", command=self._add_host).pack(side="left", padx=(0,14))

        # Interval
        tk.Label(add_frame, text="Interval:", font=("Segoe UI", 11),
                 bg="#F8F8F6", fg="#5F5E5A").pack(side="left")
        cb = ttk.Combobox(add_frame, values=[x[0] for x in self.INTERVALS],
                          width=10, state="readonly", font=("Segoe UI", 11))
        cb.current(1)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", self._on_interval_change)
        self._interval_cb = cb

        # Start/Stop
        self._startstop_btn = ttk.Button(add_frame, text="▶  Εκκίνηση",
                                          command=self._toggle_monitor)
        self._startstop_btn.pack(side="left", padx=8)

        # Stats bar
        stats = tk.Frame(self, bg="#F8F8F6", padx=14, pady=6)
        stats.pack(fill="x")
        self._stat_vars = {}
        self._stat_frames = {}
        for key, label in [("total","Σύνολο hosts"), ("up","Online"),
                            ("down","Offline"), ("checks","Γύροι ελέγχου"), ("fails","Αποτυχίες")]:
            f = tk.Frame(stats, bg="#EEEDE8", bd=0, padx=14, pady=6,
                         relief="flat", highlightthickness=1,
                         highlightbackground="#D3D1C7")
            f.pack(side="left", padx=(0,8))
            tk.Label(f, text=label, font=("Segoe UI", 10),
                     bg="#EEEDE8", fg="#5F5E5A").pack()
            v = tk.StringVar(value="0")
            self._stat_vars[key] = v
            tk.Label(f, textvariable=v, font=("Segoe UI", 16, "bold"),
                     bg="#EEEDE8", fg="#1A1A18").pack()
            self._stat_frames[key] = f

        # Hosts table
        cols_frame = tk.Frame(self, bg="#D3D1C7", padx=14, pady=0)
        cols_frame.pack(fill="x", padx=14, pady=(8,0))
        for col, w in [("Host/IP", 200), ("Status", 80), ("Uptime %", 80),
                       ("Uptime", 90), ("Downtime", 90), ("Latency", 80),
                       ("Τελ. Έλεγχος", 130)]:
            tk.Label(cols_frame, text=col, font=("Segoe UI", 10, "bold"),
                     bg="#D3D1C7", fg="#444441", width=w//8,
                     anchor="w").pack(side="left", padx=4, pady=4)

        self._tree_frame = tk.Frame(self, bg="#F8F8F6")
        self._tree_frame.pack(fill="both", expand=False, padx=14)

        cols = ("host","status","uptime_pct","uptime","downtime","latency","last_check")
        self._tree = ttk.Treeview(self._tree_frame, columns=cols, show="headings",
                                   height=7, selectmode="browse")
        headers = {"host":"Host/IP","status":"Status","uptime_pct":"Uptime %",
                   "uptime":"Uptime","downtime":"Downtime","latency":"Latency",
                   "last_check":"Τελ. Έλεγχος"}
        widths   = {"host":200,"status":80,"uptime_pct":80,"uptime":90,
                    "downtime":90,"latency":80,"last_check":130}
        for c in cols:
            self._tree.heading(c, text=headers[c])
            self._tree.column(c, width=widths[c], anchor="w")

        self._tree.tag_configure("up",   background="#EAF3DE", foreground="#3B6D11")
        self._tree.tag_configure("down", background="#FCEBEB", foreground="#A32D2D")
        self._tree.tag_configure("unknown", background="#F1EFE8", foreground="#5F5E5A")

        scrollbar = ttk.Scrollbar(self._tree_frame, orient="vertical",
                                   command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Context menu
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="Άμεσος έλεγχος", command=self._check_selected_now)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Αφαίρεση host", command=self._remove_selected)
        self._tree.bind("<Button-3>", self._show_ctx)

        # Log section
        log_hdr = tk.Frame(self, bg="#F8F8F6", padx=14, pady=8)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="Log Συμβάντων", font=("Segoe UI", 11, "bold"),
                 bg="#F8F8F6", fg="#444441").pack(side="left")
        ttk.Button(log_hdr, text="↓ Εξαγωγή CSV", command=self._export_csv).pack(side="right")
        ttk.Button(log_hdr, text="Καθαρισμός", command=self._clear_log).pack(side="right", padx=8)

        log_frame = tk.Frame(self, bg="#F8F8F6", padx=14, pady=10)
        log_frame.pack(fill="both", expand=True)

        self._log_text = tk.Text(log_frame, font=("Consolas", 10), height=8,
                                  bg="#1E1E1C", fg="#C8C8C0", insertbackground="white",
                                  relief="flat", state="disabled", wrap="none")
        log_scroll_y = ttk.Scrollbar(log_frame, orient="vertical",
                                      command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scroll_y.set)
        self._log_text.pack(side="left", fill="both", expand=True)
        log_scroll_y.pack(side="right", fill="y")

        self._log_text.tag_configure("up",      foreground="#7EC34C")
        self._log_text.tag_configure("down",    foreground="#E24B4A")
        self._log_text.tag_configure("neutral", foreground="#888780")
        self._log_text.tag_configure("ts",      foreground="#FAC775")

    # ─── HOST MANAGEMENT ───────────────────────────────────────────────────

    def _add_host(self):
        addr = self._host_var.get().strip()
        if not addr or addr == "IP ή hostname...":
            return
        with self._lock:
            if any(h.addr == addr for h in self.hosts):
                messagebox.showinfo("Ήδη υπάρχει", f"Ο host {addr} υπάρχει ήδη.")
                return
            self.hosts.append(HostEntry(addr))
        self._host_var.set("")
        self._refresh_tree()
        self._update_stats()
        self._log(f"Προστέθηκε host: {addr}", "neutral")

    def _remove_selected(self):
        sel = self._tree.selection()
        if not sel:
            return
        addr = self._tree.item(sel[0])["values"][0]
        with self._lock:
            host = next((h for h in self.hosts if h.addr == addr), None)
            if host and host.status == "down" and host.down_since:
                # Finalize current downtime before removal so stats are accurate
                host.total_downtime += (datetime.now() - host.down_since).total_seconds()
                host.down_since = None
            self.hosts = [h for h in self.hosts if h.addr != addr]
        self._refresh_tree()
        self._update_stats()
        self._log(f"Αφαιρέθηκε host: {addr}", "neutral")

    def _show_ctx(self, event):
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._ctx_menu.tk_popup(event.x_root, event.y_root)

    # ─── MONITORING ────────────────────────────────────────────────────────

    def _toggle_monitor(self):
        if self.running:
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self):
        if not self.hosts:
            messagebox.showinfo("Χωρίς hosts", "Πρόσθεσε τουλάχιστον ένα host.")
            return
        self.running = True
        self._startstop_btn.configure(text="■  Διακοπή")
        self._status_lbl.configure(text="● Τρέχει", bg="#EAF3DE", fg="#3B6D11")
        self._log("Εκκίνηση παρακολούθησης.", "neutral")
        self._schedule_checks()

    def _stop_monitor(self):
        self.running = False
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        self._startstop_btn.configure(text="▶  Εκκίνηση")
        self._status_lbl.configure(text="● Σταματημένο", bg="#F1EFE8", fg="#5F5E5A")
        self._log("Παρακολούθηση σταματημένη.", "neutral")

    def _schedule_checks(self):
        if not self.running:
            return
        self._run_checks()
        interval_ms = self._interval_var.get() * 1000
        self._timer = self.after(interval_ms, self._schedule_checks)

    def _run_checks(self):
        self._check_counter += 1
        self._stat_vars["checks"].set(str(self._check_counter))
        with self._lock:
            hosts_snapshot = list(self.hosts)
        if not hosts_snapshot:
            return
        # Barrier: refresh UI only once after ALL hosts finish this round
        remaining = [len(hosts_snapshot)]
        lock = threading.Lock()

        def check_and_notify(host):
            self._check_host_raw(host)
            with lock:
                remaining[0] -= 1
                if remaining[0] == 0:
                    self.after(0, self._refresh_tree)
                    self.after(0, self._update_stats)

        for host in hosts_snapshot:
            threading.Thread(target=check_and_notify, args=(host,),
                             daemon=True).start()

    def _check_selected_now(self):
        sel = self._tree.selection()
        if not sel:
            return
        addr = self._tree.item(sel[0])["values"][0]
        with self._lock:
            host = next((h for h in self.hosts if h.addr == addr), None)
        if host:
            threading.Thread(target=self._check_host, args=(host,), daemon=True).start()

    def _check_host_raw(self, host):
        """Runs in a worker thread. Does NOT schedule UI refresh — the barrier in _run_checks does that."""
        online, latency = ping_host(host.addr)
        now = datetime.now()

        with self._lock:
            prev_status = host.status
            host.last_check = now
            host.last_latency = latency
            host.checks += 1

            if online:
                host.success_checks += 1
                if prev_status in ("down", "unknown"):
                    if prev_status == "down" and host.down_since:
                        delta = (now - host.down_since).total_seconds()
                        host.total_downtime += delta
                    host.up_since = now
                    if prev_status == "down":
                        self.after(0, lambda a=host.addr: self._log(
                            f"✓  {a} επανήλθε ONLINE", "up"))
                host.status = "up"
            else:
                if prev_status == "up":
                    if host.up_since:
                        delta = (now - host.up_since).total_seconds()
                        host.total_uptime += delta
                    host.down_since = now
                    self.after(0, lambda a=host.addr: self._log(
                        f"✗  {a} OFFLINE!", "down"))
                elif prev_status == "unknown":
                    host.down_since = now
                    self.after(0, lambda a=host.addr: self._log(
                        f"✗  {a} δεν αποκρίνεται", "down"))
                if prev_status != "down":
                    host.fail_count += 1
                host.status = "down"

    # Used by "Άμεσος έλεγχος" context menu — does its own refresh
    def _check_host(self, host):
        self._check_host_raw(host)
        self.after(0, self._refresh_tree)
        self.after(0, self._update_stats)

    def _on_interval_change(self, event):
        idx = self._interval_cb.current()
        self._interval_var.set(self.INTERVALS[idx][1])
        if self.running:
            if self._timer:
                self.after_cancel(self._timer)
            interval_ms = self._interval_var.get() * 1000
            self._timer = self.after(interval_ms, self._schedule_checks)

    # ─── TREE REFRESH ──────────────────────────────────────────────────────

    def _refresh_tree(self):
        self._tree.delete(*self._tree.get_children())
        with self._lock:
            hosts_copy = list(self.hosts)
        for h in hosts_copy:
            lat = f"{h.last_latency:.0f}ms" if h.last_latency is not None else "—"
            lc = h.last_check.strftime("%H:%M:%S %d/%m") if h.last_check else "—"
            self._tree.insert("", "end", values=(
                h.addr,
                self.STATUS_LABELS.get(h.status, "—"),
                h.uptime_pct(),
                h.total_uptime_str(),
                h.live_downtime_str(),
                lat,
                lc
            ), tags=(h.status,))

    def _update_stats(self):
        with self._lock:
            total = len(self.hosts)
            up    = sum(1 for h in self.hosts if h.status == "up")
            down  = sum(1 for h in self.hosts if h.status == "down")
            fails = sum(h.fail_count for h in self.hosts)
        self._stat_vars["total"].set(str(total))
        self._stat_vars["up"].set(str(up))
        self._stat_vars["down"].set(str(down))
        self._stat_vars["fails"].set(str(fails))
        # Color fails box red when there are failures
        fail_frame = self._stat_frames.get("fails")
        if fail_frame:
            bg = "#FCEBEB" if fails > 0 else "#EEEDE8"
            for widget in fail_frame.winfo_children():
                widget.configure(bg=bg)
            fail_frame.configure(bg=bg)

    # ─── LOG ───────────────────────────────────────────────────────────────

    def _log(self, msg, tag="neutral"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_entries.append({"time": ts, "msg": msg, "tag": tag,
                                  "date": datetime.now().strftime("%Y-%m-%d")})
        self._log_text.configure(state="normal")
        self._log_text.insert("end", f"[{ts}] ", "ts")
        self._log_text.insert("end", msg + "\n", tag)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
        self._save_log()

    def _clear_log(self):
        self.log_entries.clear()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _save_log(self):
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.log_entries[-500:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Όλα", "*.*")],
            initialfile=f"ping_log_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Ημερομηνία", "Ώρα", "Συμβάν", "Τύπος"])
                for e in self.log_entries:
                    writer.writerow([e.get("date",""), e["time"], e["msg"], e["tag"]])
                writer.writerow([])
                writer.writerow(["Host", "Status", "Uptime %", "Uptime", "Downtime", "Έλεγχοι"])
                with self._lock:
                    for h in self.hosts:
                        writer.writerow([h.addr, h.status, h.uptime_pct(),
                                         h.total_uptime_str(), h.live_downtime_str(),
                                         h.checks])
            messagebox.showinfo("Εξαγωγή", f"Αποθηκεύτηκε:\n{path}")
        except Exception as ex:
            messagebox.showerror("Σφάλμα", str(ex))

    # ─── CONFIG PERSIST ────────────────────────────────────────────────────

    def _save_config(self):
        try:
            cfg = {
                "hosts": [h.addr for h in self.hosts],
                "interval_idx": self._interval_cb.current()
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for addr in cfg.get("hosts", []):
                self.hosts.append(HostEntry(addr))
            idx = cfg.get("interval_idx", 1)
            self._interval_cb.current(idx)
            self._interval_var.set(self.INTERVALS[idx][1])
            self._refresh_tree()
            self._update_stats()
            # Load old log
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding="utf-8") as f:
                    self.log_entries = json.load(f)
                for e in self.log_entries[-50:]:
                    self._log_text.configure(state="normal")
                    self._log_text.insert("end", f"[{e['time']}] ", "ts")
                    self._log_text.insert("end", e["msg"] + "\n", e.get("tag","neutral"))
                    self._log_text.configure(state="disabled")
                self._log_text.see("end")
        except Exception:
            pass

    def _on_close(self):
        self._stop_monitor()
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    app = PingMonitorApp()
    app.mainloop()
