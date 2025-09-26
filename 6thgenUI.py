# PHARMACY_APP.py
# Pharmacy LED System — CustomTkinter, Dynamic Shelf Settings, Dashboard, and Full Workflow
# ----------------------------------------------------------------------------------------
# WHAT’S INCLUDED
# - Patients main table (Excel-like) with search, add, delete (double-click to open patient popup)
# - Patient popup: edit name/address, add/edit prescriptions, auto-assign (with family bin), manual assign (validated),
#   LED blink popup (pause/resume/done), delete selected prescriptions (button + Delete key), clear all prescriptions
# - Shelf Assignment tab: LEFT = letter sections (A..Z + Overflow); RIGHT = Shelf Settings (add/delete shelves; Save & Rebuild Slots)
# - Overdue tab: aggregated view + per-patient detail + blink
# - Previous Actions tab: last 300 actions today
# - Dashboard tab: KPI cards, 3 charts (Rx per day, Overdue trend, Shelf utilization), recent actions, quick actions
# - Font sizes: Labels 17, Table text 16, Headers 20
# - Popups prioritized: transient, topmost momentarily, grab_set; nested popups raised correctly
# - DB safe commits (timeout, context managers), idempotent schema init, dynamic slot rebuild

import os, sqlite3, threading, time, math, json, random
from datetime import datetime, timedelta
import customtkinter as ctk
from tkinter import ttk, messagebox

# Charts
import matplotlib
matplotlib.use("Agg")  # headless backend for figure generation
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# =========================
# Paths / DB helpers
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pharmacy.db")

def db_connect():
    return sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)  # autocommit mode

def db_exec(sql, params=()):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(sql, params)
        conn.commit()
        return cur

def db_fetchone(sql, params=()):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

def db_fetchall(sql, params=()):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

# =========================
# Actions log
# =========================
def init_actions_table():
    db_exec("""CREATE TABLE IF NOT EXISTS actions_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        actor TEXT,
        action TEXT
    )""")

def log_action(action, actor="system"):
    db_exec("INSERT INTO actions_log(ts,actor,action) VALUES(?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor, action))

def get_todays_actions(limit=300):
    today = datetime.now().strftime("%Y-%m-%d")
    return db_fetchall("""SELECT ts,actor,action
                          FROM actions_log
                          WHERE ts LIKE ?
                          ORDER BY id DESC
                          LIMIT ?""", (today+"%", limit))

# =========================
# LED simulation (with UI popup)
# =========================
_blink_groups = {}  # group_key -> {stop,pause,thread,slots,color}

def slot_id_to_label(slot_id):
    r = db_fetchone("SELECT shelf,row,col FROM slots WHERE id=?", (slot_id,))
    if not r: return ""
    s, rrow, c = r
    return f"{s}-{rrow}{c}"

def slot_ids_to_labels(slot_ids):
    return [slot_id_to_label(s) for s in slot_ids if s]

def _blink_worker(group_key):
    ctrl = _blink_groups[group_key]
    stop, pause = ctrl["stop"], ctrl["pause"]
    labels = slot_ids_to_labels(ctrl["slots"])
    color = ctrl["color"]
    while not stop.is_set():
        if pause.is_set():
            time.sleep(0.2); continue
        print(f"[LED] ON {labels} ({color})")
        time.sleep(0.5)
        print(f"[LED] OFF {labels}")
        time.sleep(0.5)

def start_blink(slots, color="yellow"):
    if not slots: return None
    key = int(slots[0])
    stop_blink(key)
    stop = threading.Event(); pause = threading.Event()
    th = threading.Thread(target=_blink_worker, args=(key,), daemon=True)
    _blink_groups[key] = {"stop": stop, "pause": pause, "thread": th, "slots": list(slots), "color": color}
    th.start()
    return key

def pause_toggle(group_key):
    ctrl = _blink_groups.get(group_key)
    if not ctrl: return
    if ctrl["pause"].is_set():
        ctrl["pause"].clear(); print("[LED] resume")
    else:
        ctrl["pause"].set(); print("[LED] pause")

def stop_blink(slots_or_key):
    if slots_or_key is None: return
    if isinstance(slots_or_key, int):
        ctrl = _blink_groups.pop(slots_or_key, None)
        if ctrl: ctrl["stop"].set()
        return
    key = int(slots_or_key[0])
    ctrl = _blink_groups.pop(key, None)
    if ctrl: ctrl["stop"].set()

def _raise_and_grab(win, parent=None):
    try:
        if parent: win.transient(parent)
        win.lift(); win.attributes("-topmost", True); win.update(); win.attributes("-topmost", False)
        win.grab_set()
    except: pass

def open_led_popup(parent, slots, color="yellow", title="LED Control"):
    if not slots:
        messagebox.showinfo("LED", "No slots to blink."); return
    key = start_blink(slots, color)
    win = ctk.CTkToplevel(parent); win.title(title)
    win.geometry("560x180")
    _raise_and_grab(win, parent)
    ctk.CTkLabel(win, text=f"Blinking: {', '.join(slot_ids_to_labels(slots))}",
                 font=ctk.CTkFont(size=17)).pack(padx=14, pady=(14,6))
    def _toggle(btn):
        pause_toggle(key)
        if btn.cget("text").startswith("⏸"):
            btn.configure(text="▶ Resume Lights")
        else:
            btn.configure(text="⏸ Pause Lights")
    btn = ctk.CTkButton(win, text="⏸ Pause Lights", corner_radius=10, fg_color="#0B5CAB",
                        hover_color="#084b8a", command=lambda: _toggle(btn))
    btn.pack(side="left", padx=12, pady=12)
    ctk.CTkButton(win, text="✅ Done", corner_radius=10, fg_color="#D83B01",
                  hover_color="#B32F00",
                  command=lambda: (stop_blink(key), win.destroy())).pack(side="right", padx=12, pady=12)

# =========================
# DB schema & dynamic shelves/slots
# =========================
def init_db(reset=False):
    with db_connect() as conn:
        cur = conn.cursor()
        if reset:
            for t in ["patients","prescriptions","letter_sections","shelf_settings","slots","actions_log"]:
                cur.execute(f"DROP TABLE IF EXISTS {t}")

        cur.execute("""CREATE TABLE IF NOT EXISTS patients(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            created_at TEXT
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS prescriptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INT NOT NULL,
            medication TEXT NOT NULL,
            quantity INT,
            date_added TEXT,
            basket_size TEXT CHECK (basket_size IN ('small','large')),
            slot_id INT
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS letter_sections(
            letter TEXT PRIMARY KEY,
            shelf TEXT,
            lower_bound TEXT,
            upper_bound TEXT
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS shelf_settings(
            shelf TEXT PRIMARY KEY,
            rows INT,
            cols INT
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS slots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shelf TEXT,
            row TEXT,
            col INT,
            occupied INT DEFAULT 0
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS actions_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            actor TEXT,
            action TEXT
        )""")
        conn.commit()

    # Seed shelves if missing (F/L/R, 26x100)
    if not db_fetchone("SELECT 1 FROM shelf_settings"):
        for s in ["F","L","R"]:
            db_exec("INSERT OR IGNORE INTO shelf_settings(shelf,rows,cols) VALUES(?,?,?)", (s, 26, 100))

    # Seed letter sections if missing (A-Z + Overflow)
    if not db_fetchone("SELECT 1 FROM letter_sections"):
        for L in [chr(i) for i in range(65,91)] + ["Overflow"]:
            db_exec("INSERT OR IGNORE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                    (L, "", "", ""))
        # A few sample defaults
        db_exec("UPDATE letter_sections SET shelf='F',lower_bound='A1',upper_bound='D20' WHERE letter='A'")
        db_exec("UPDATE letter_sections SET shelf='L',lower_bound='E1',upper_bound='H30' WHERE letter='B'")
        db_exec("UPDATE letter_sections SET shelf='R',lower_bound='J1',upper_bound='M15' WHERE letter='C'")

def rebuild_slots_from_settings():
    # Rebuild slots from shelf_settings entirely
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute("DELETE FROM slots")
        shelf_rows = cur.execute("SELECT shelf, rows, cols FROM shelf_settings").fetchall()
        for s, rcount, ccount in shelf_rows:
            rcount = max(1, int(rcount or 1))
            ccount = max(1, int(ccount or 1))
            for i in range(rcount):
                row_letter = chr(65 + i) if 0 <= i < 26 else chr(65 + (i % 26))  # cap to letters; can extend later
                for c in range(1, ccount+1):
                    cur.execute("INSERT INTO slots(shelf,row,col,occupied) VALUES(?,?,?,0)", (s, row_letter, c))
        conn.commit()
    print("[SLOTS] Rebuilt from shelf_settings")

def ensure_slots_exist():
    cnt = db_fetchone("SELECT COUNT(*) FROM slots")
    if not cnt or cnt[0] == 0:
        rebuild_slots_from_settings()

# =========================
# Section / Slot helpers
# =========================
def parse_bound(b):
    if not b: return None
    b = b.strip().upper()
    if len(b) < 2: return None
    row = b[0]; n = b[1:]
    if not row.isalpha() or not n.isdigit(): return None
    col = int(n)
    if col < 1: return None
    return (row, col)

def rows_range(a, b):
    return [chr(i) for i in range(ord(a), ord(b)+1)]

def get_letter_section(letter):
    r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (letter,))
    if not r: return None
    shelf, lo, up = r
    plo, pup = parse_bound(lo), parse_bound(up)
    if not shelf or not plo or not pup: return None
    return shelf, plo[0], plo[1], pup[0], pup[1]

def get_slot_by_position(shelf, row, col):
    return db_fetchone("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?",
                       (shelf,row,col))

def find_slot_in_section(shelf, sr, sc, er, ec, basket):
    with db_connect() as conn:
        cur = conn.cursor()
        # Determine real row bounds from shelf_settings
        rs = db_fetchone("SELECT rows, cols FROM shelf_settings WHERE shelf=?", (shelf,))
        if not rs: return None
        rows_count, cols_count = int(rs[0] or 26), int(rs[1] or 100)
        valid_rows = [chr(65+i) for i in range(rows_count)]
        def clamp_row(r): return r if r in valid_rows else (valid_rows[-1] if r > valid_rows[-1] else valid_rows[0])

        sr = clamp_row(sr); er = clamp_row(er)
        def col_in_bounds(c): return 1 <= c <= cols_count

        for r in rows_range(sr, er):
            cstart = sc if r==sr else 1
            cend   = ec if r==er else cols_count
            cstart = max(1, cstart); cend = min(cols_count, cend)
            for c in range(cstart, cend+1):
                cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?",(shelf,r,c))
                k = cur.fetchone()
                if not k: continue
                sid, occ = k
                if occ == 0:
                    if basket=="large":
                        if col_in_bounds(c+1):
                            cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?",(shelf,r,c+1))
                            k2 = cur.fetchone()
                            if k2 and k2[1]==0:
                                return [sid, k2[0]], shelf
                    else:
                        return [sid], shelf
    return None

def find_next_available_slot_primary(letter, basket):
    sec = get_letter_section(letter)
    if not sec: return None
    shelf, sr, sc, er, ec = sec
    return find_slot_in_section(shelf, sr, sc, er, ec, basket)

def find_next_available_slot_with_overflow(letter, basket):
    res = find_next_available_slot_primary(letter, basket)
    if res: return res
    of = get_letter_section("Overflow")
    if of:
        shelf, sr, sc, er, ec = of
        res2 = find_slot_in_section(shelf, sr, sc, er, ec, basket)
        if res2: return res2
    return None

def mark_slots_occupied(slot_ids):
    if not slot_ids: return
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        for s in slot_ids:
            cur.execute("UPDATE slots SET occupied=1 WHERE id=?", (s,))
        conn.commit()

def mark_slots_free(slot_ids):
    if not slot_ids: return
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        for s in slot_ids:
            cur.execute("UPDATE slots SET occupied=0 WHERE id=?", (s,))
        conn.commit()

def parse_location_label_to_slot_id(txt):
    if not txt: return None
    s = txt.strip().upper().replace(" ", "").replace("--","-")
    if s.isdigit():
        r = db_fetchone("SELECT id FROM slots WHERE id=?", (int(s),))
        return int(s) if r else None
    parts = s.split("-")
    if len(parts)==2:
        shelf, rowcol = parts
        if len(shelf)==1 and len(rowcol)>=2:
            row = rowcol[0]
            if not row.isalpha(): return None
            try: col = int(rowcol[1:])
            except: return None
            r = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?", (shelf,row,col))
            return r[0] if r else None
    if len(parts)==3:
        shelf, row, coltxt = parts
        if len(shelf)==1 and len(row)==1:
            try: col = int(coltxt)
            except: return None
            r = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?", (shelf,row,col))
            return r[0] if r else None
    return None

# =========================
# Overdue helpers
# =========================
def parse_any_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
        try: return datetime.strptime(s, fmt)
        except: pass
    return None

def get_overdue_prescriptions(days=14):
    rows = db_fetchall("""SELECT pr.id, pr.patient_id, pr.medication, pr.quantity, pr.date_added, pr.slot_id,
                                 p.name, p.address
                          FROM prescriptions pr
                          JOIN patients p ON p.id = pr.patient_id""")
    now = datetime.now()
    out = []
    for pr_id, pid, med, qty, date_added, slot_id, name, address in rows:
        dt = parse_any_date(date_added)
        if not dt: continue
        diff = (now - dt).days
        if diff > days:
            out.append({
                "prescription_id": pr_id,
                "patient_id": pid,
                "name": name, "address": address,
                "medication": med, "quantity": qty,
                "days_overdue": diff - days,
                "slot_id": slot_id
            })
    return out

def aggregate_overdue_by_patient(items):
    agg = {}
    for it in items:
        pid = it["patient_id"]
        if pid not in agg:
            agg[pid] = {"name": it["name"], "address": it["address"], "count": 0, "oldest": 0, "slots": set()}
        agg[pid]["count"] += 1
        agg[pid]["oldest"] = max(agg[pid]["oldest"], it["days_overdue"])
        if it["slot_id"]:
            agg[pid]["slots"].add(it["slot_id"])
    return agg

# =========================
# Treeview styling
# =========================
def style_treeview():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except:
        pass
    style.configure("Treeview",
                    background="white",
                    fieldbackground="white",
                    rowheight=30,
                    borderwidth=0,
                    font=("Segoe UI", 16))
    style.configure("Treeview.Heading",
                    background="#0078d4",
                    foreground="white",
                    font=("Segoe UI Semibold", 20))
    style.map("Treeview", background=[("selected","#cce6ff")])

# =========================
# Dashboard data helpers
# =========================
def count_patients():
    r = db_fetchone("SELECT COUNT(*) FROM patients")
    return r[0] if r else 0

def count_prescriptions():
    r = db_fetchone("SELECT COUNT(*) FROM prescriptions")
    return r[0] if r else 0

def count_overdue():
    return len(get_overdue_prescriptions(14))

def slots_usage():
    r1 = db_fetchone("SELECT COUNT(*) FROM slots")
    r2 = db_fetchone("SELECT COUNT(*) FROM slots WHERE occupied=1")
    total = r1[0] if r1 else 0
    used = r2[0] if r2 else 0
    free = max(0, total - used)
    pct = (used/total*100) if total else 0
    return total, used, free, pct

def prescriptions_per_day(last_n=30):
    cutoff = datetime.now() - timedelta(days=last_n-1)
    rows = db_fetchall("SELECT date_added FROM prescriptions")
    counter = {}
    for d in rows:
        dt = parse_any_date(d[0])
        if not dt: continue
        if dt.date() < cutoff.date(): continue
        key = dt.strftime("%Y-%m-%d")
        counter[key] = counter.get(key, 0) + 1
    xs = [(cutoff + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(last_n)]
    ys = [counter.get(x, 0) for x in xs]
    return xs, ys

def overdue_trend(last_n=30):
    xs = [(datetime.now() - timedelta(days=last_n-1-i)).strftime("%Y-%m-%d") for i in range(last_n)]
    rows = db_fetchall("SELECT date_added FROM prescriptions")
    # naive trend: count items older than 14 days each day snapshot
    counts = []
    for i, day in enumerate(xs):
        snap = datetime.strptime(day, "%Y-%m-%d")
        c = 0
        for (d,) in rows:
            dt = parse_any_date(d)
            if not dt: continue
            if (snap - dt).days > 14:
                c += 1
        counts.append(c)
    return xs, counts

def shelf_utilization():
    shelves = db_fetchall("SELECT shelf FROM shelf_settings ORDER BY shelf")
    out = []
    for (s,) in shelves:
        total = db_fetchone("SELECT COUNT(*) FROM slots WHERE shelf=?", (s,))[0]
        used  = db_fetchone("SELECT COUNT(*) FROM slots WHERE shelf=? AND occupied=1", (s,))[0]
        out.append((s, total, used))
    return out

# =========================
# App UI (CustomTkinter)
# =========================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("Pharmacy LED System")
        self.geometry("1500x900")
        self.minsize(1280, 760)

        style_treeview()

        # ---------- Top bar ----------
        topbar = ctk.CTkFrame(self, corner_radius=0)
        topbar.pack(side="top", fill="x")
        ctk.CTkLabel(topbar, text="💊 Pharmacy LED System",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left", padx=12, pady=8)

        self.theme_mode = ctk.StringVar(value="Dark")
        def toggle_theme():
            mode = "Light" if self.theme_mode.get()=="Dark" else "Dark"
            self.theme_mode.set(mode)
            ctk.set_appearance_mode(mode)
        ctk.CTkButton(topbar, text="🌓 Theme", width=96, command=toggle_theme).pack(side="right", padx=10, pady=8)

        # ---------- Right sidebar ----------
        sidebar = ctk.CTkFrame(self, width=260, corner_radius=0)
        sidebar.pack(side="right", fill="y")
        ctk.CTkLabel(sidebar, text="Tools", font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(12, 8))
        ctk.CTkButton(sidebar, text="📚 Shelf Assignment", command=self.open_shelf_assignment).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="⏰ Overdue Meds", command=self.open_overdue_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📝 Previous Actions", command=self.open_actions_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📈 Dashboard", command=self.open_dashboard).pack(padx=10, pady=6, fill="x")

        # ---------- Patients header + controls ----------
        header = ctk.CTkFrame(self, fg_color="#0b5cab")
        header.pack(fill="x", padx=0, pady=(0,6))
        ctk.CTkLabel(header, text="Patients", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", padx=10, pady=(0,8))
        ctk.CTkLabel(controls, text="Search:", font=ctk.CTkFont(size=17)).pack(side="left", padx=(8,6))
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(controls, textvariable=self.search_var, width=280)
        search_entry.pack(side="left", padx=(0,10), ipady=4)
        search_entry.bind("<Return>", lambda e: self.refresh_patient_table())
        ctk.CTkButton(controls, text="🔄 Refresh", command=self.refresh_patient_table).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="➕ Add Patient", fg_color="#2F8B2F", hover_color="#277327",
                      command=self.add_patient_popup).pack(side="right", padx=6)
        ctk.CTkButton(controls, text="🗑️ Delete Patient", fg_color="#D83B01", hover_color="#B32F00",
                      command=self.delete_selected_patient).pack(side="right", padx=6)

        # ---------- Patients Treeview ----------
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.tree = ttk.Treeview(table_frame, columns=("Name","Address","Date Added","Locations"),
                                 show="headings", selectmode="browse")
        for c in ("Name","Address","Date Added","Locations"):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=260 if c!="Locations" else 520, anchor="w")
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscroll=sb.set)
        self.tree.tag_configure("odd", background="#f5f9ff")
        self.tree.tag_configure("even", background="#ffffff")
        self.tree.bind("<Double-1>", self.on_patient_double)

        self.refresh_patient_table()

    # ---------------- Patients main ----------------
    def refresh_patient_table(self):
        for r in self.tree.get_children(): self.tree.delete(r)
        q = self.search_var.get().strip()
        sql = "SELECT id,name,address,created_at FROM patients"
        params = ()
        if q:
            sql += " WHERE name LIKE ? OR address LIKE ?"
            params = (f"%{q}%", f"%{q}%")
        sql += " ORDER BY name"
        rows = db_fetchall(sql, params)
        for i,(pid,name,addr,created) in enumerate(rows):
            disp = ""
            if created:
                for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
                    try:
                        disp = datetime.strptime(created, fmt).strftime("%m/%d/%Y")
                        break
                    except: pass
            slot_rows = db_fetchall("SELECT DISTINCT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
            locs = [slot_id_to_label(s[0]) for s in slot_rows if s and s[0]]
            tag = "odd" if i%2 else "even"
            self.tree.insert("", "end", iid=str(pid),
                             values=(name, addr or "", disp, ", ".join([l for l in locs if l])),
                             tags=(tag,))

    def add_patient_popup(self):
        p = ctk.CTkToplevel(self); p.title("Add Patient"); p.geometry("560x240")
        _raise_and_grab(p, self)
        head = ctk.CTkFrame(p, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Add Patient", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        body = ctk.CTkFrame(p); body.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(body, text="Name", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=8, pady=8)
        name_e = ctk.CTkEntry(body, width=340); name_e.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(body, text="Address", font=ctk.CTkFont(size=17)).grid(row=1, column=0, sticky="e", padx=8, pady=8)
        addr_e = ctk.CTkEntry(body, width=340); addr_e.grid(row=1, column=1, padx=8, pady=8)
        def save():
            nm = name_e.get().strip()
            if not nm:
                messagebox.showerror("Error","Name required"); return
            db_exec("INSERT INTO patients(name,address,created_at) VALUES(?,?,?)",
                    (nm, addr_e.get().strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            log_action(f"Added patient: {nm}")
            p.destroy(); self.refresh_patient_table()
        ctk.CTkButton(body, text="Save", fg_color="#2F8B2F", hover_color="#277327", command=save).grid(row=2, column=0, columnspan=2, pady=12)

    def delete_selected_patient(self):
        sel = self.tree.selection()
        if not sel: return
        pid = int(sel[0])
        name = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0]
        if not messagebox.askyesno("Confirm", f"Delete {name} and ALL prescriptions?"): return
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=?", (pid,))
        sids = [r[0] for r in rows if r and r[0]]
        if sids: mark_slots_free(sids)
        db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
        db_exec("DELETE FROM patients WHERE id=?", (pid,))
        log_action(f"Deleted patient: {name}")
        self.refresh_patient_table()

    def on_patient_double(self, _event):
        sel = self.tree.selection()
        if not sel: return
        pid = int(sel[0])
        self.open_patient_popup(pid)

    # ---------------- Patient popup (edit + Rx) ----------------
    def open_patient_popup(self, pid):
        r = db_fetchone("SELECT name,address FROM patients WHERE id=?", (pid,))
        pname, paddr = (r[0], r[1] or "") if r else ("","")
        win = ctk.CTkToplevel(self); win.title(f"Patient — {pname}")
        win.geometry("1220x820"); _raise_and_grab(win, self)

        head = ctk.CTkFrame(win, fg_color="#107c10"); head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Patient — {pname}", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        info = ctk.CTkFrame(win); info.pack(fill="x", padx=12, pady=(10,6))
        ctk.CTkLabel(info, text="Name", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        name_e = ctk.CTkEntry(info, width=360); name_e.grid(row=0, column=1, padx=6, pady=6); name_e.insert(0, pname)
        ctk.CTkLabel(info, text="Address", font=ctk.CTkFont(size=17)).grid(row=0, column=2, sticky="e", padx=6, pady=6)
        addr_e = ctk.CTkEntry(info, width=360); addr_e.grid(row=0, column=3, padx=6, pady=6); addr_e.insert(0, paddr)
        ctk.CTkButton(info, text="💾 Save Patient", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=lambda: self._save_patient(pid, name_e, addr_e, win)).grid(row=0, column=4, padx=8)
        ctk.CTkButton(info, text="🔵 Light Up Locations", command=lambda: self._light_up_patient(pid, win)).grid(row=0, column=5, padx=6)
        ctk.CTkButton(info, text="🗑️ Clear All Prescriptions", fg_color="#D83B01", hover_color="#B32F00",
                      command=lambda: self._clear_all_prescriptions(pid, win)).grid(row=0, column=6, padx=6)

        # Rx Table
        mid = ctk.CTkFrame(win); mid.pack(fill="both", expand=True, padx=12, pady=(6,8))
        cols = ("Medication","Quantity","Date Added","Basket","Location")
        rx = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            rx.heading(c, text=c)
            rx.column(c, width=230 if c!="Location" else 280, anchor="w")
        rx.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=rx.yview)
        sb.pack(side="right", fill="y")
        rx.configure(yscroll=sb.set)
        rx.tag_configure("odd", background="#f5f9ff")
        rx.tag_configure("even", background="#ffffff")

        def populate_rx():
            for r in rx.get_children(): rx.delete(r)
            rows2 = db_fetchall("""SELECT id,medication,quantity,date_added,basket_size,slot_id
                                   FROM prescriptions
                                   WHERE patient_id=? ORDER BY id""", (pid,))
            for i,(prid, med, qty, dt, basket, sid) in enumerate(rows2):
                date_disp = ""
                if dt:
                    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
                        try: date_disp = datetime.strptime(dt,fmt).strftime("%m/%d/%Y"); break
                        except: pass
                loc = slot_id_to_label(sid) if sid else ""
                tag = "odd" if i%2 else "even"
                rx.insert("", "end", iid=str(prid),
                          values=(med, qty if qty is not None else "", date_disp, basket or "", loc),
                          tags=(tag,))
        populate_rx()

        def on_rx_double(_e):
            sel = rx.selection()
            if not sel: return
            prid = int(sel[0])
            self._edit_prescription_popup(pid, prid, populate_rx)
        rx.bind("<Double-1>", on_rx_double)

        def delete_selected_prescriptions():
            sel = rx.selection()
            if not sel:
                messagebox.showinfo("Delete", "No prescription selected."); return
            # Keep this popup front-most:
            win.lift(); win.attributes("-topmost", True); win.update(); win.attributes("-topmost", False)
            if not messagebox.askyesno("Confirm", f"Delete {len(sel)} prescription(s)?"):
                return
            for item in sel:
                prid = int(item)
                row = db_fetchone("SELECT medication, slot_id FROM prescriptions WHERE id=?", (prid,))
                if not row: continue
                med, sid = row
                if sid: mark_slots_free([sid])
                db_exec("DELETE FROM prescriptions WHERE id=?", (prid,))
                log_action(f"Deleted prescription '{med}' for patient_id={pid}")
            populate_rx()
            self.refresh_patient_table()

        def on_key(e):
            if e.keysym in ("Delete","BackSpace"):
                delete_selected_prescriptions()
        rx.bind("<Key>", on_key)

        # Add Rx row
        addf = ctk.CTkFrame(win); addf.pack(fill="x", padx=12, pady=(4,12))
        ctk.CTkLabel(addf, text="Medication", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=6)
        med_e = ctk.CTkEntry(addf, width=240); med_e.grid(row=0, column=1, padx=6)
        ctk.CTkLabel(addf, text="Quantity", font=ctk.CTkFont(size=17)).grid(row=0, column=2, sticky="e", padx=6)
        qty_e = ctk.CTkEntry(addf, width=120); qty_e.grid(row=0, column=3, padx=6)
        ctk.CTkLabel(addf, text="Basket", font=ctk.CTkFont(size=17)).grid(row=0, column=4, sticky="e", padx=6)
        basket_cb = ctk.CTkComboBox(addf, values=["small","large"], width=120)
        basket_cb.set("small"); basket_cb.grid(row=0, column=5, padx=6)
        ctk.CTkButton(addf, text="➕ Add Prescription", fg_color="#2F8B2F", hover_color="#277327",
                      command=lambda: self._add_prescription(pid, addr_e, med_e, qty_e, basket_cb, win, populate_rx)).grid(row=0, column=6, padx=10)
        ctk.CTkButton(addf, text="🗑️ Delete Selected", fg_color="#D83B01", hover_color="#B32F00",
                      command=delete_selected_prescriptions).grid(row=0, column=7, padx=6)

    def _save_patient(self, pid, name_e, addr_e, popup):
        nm, ad = name_e.get().strip(), addr_e.get().strip()
        db_exec("UPDATE patients SET name=?,address=? WHERE id=?", (nm, ad, pid))
        log_action(f"Updated patient: {nm}")
        popup.destroy()
        self.refresh_patient_table()

    def _light_up_patient(self, pid, parent):
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
        slots = list({r[0] for r in rows if r and r[0]})
        if not slots:
            messagebox.showinfo("LED","No assigned locations for this patient."); return
        open_led_popup(parent, slots, "blue", "Patient Locations")

    def _clear_all_prescriptions(self, pid, popup):
        name = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0]
        if not messagebox.askyesno("Confirm", f"Clear ALL prescriptions for {name}?"): return
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
        sids = [r[0] for r in rows if r and r[0]]
        if sids: mark_slots_free(sids)
        db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
        log_action(f"Cleared all prescriptions for: {name}")
        popup.destroy()
        self.refresh_patient_table()

    def _add_prescription(self, pid, addr_e, med_e, qty_e, basket_cb, parent, refresh_cb):
        med = med_e.get().strip(); qty = qty_e.get().strip(); basket = basket_cb.get().strip().lower()
        if not med or not qty or basket not in ("small","large"):
            messagebox.showerror("Error","Enter medication, quantity, and basket size."); return
        qv = int(qty) if qty.isdigit() else qty
        db_exec("""INSERT INTO prescriptions(patient_id,medication,quantity,date_added,basket_size)
                   VALUES(?,?,?,?,?)""", (pid, med, qv, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), basket))
        pres_id = db_fetchone("SELECT id FROM prescriptions WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,))[0]
        log_action(f"Added prescription '{med}' for patient_id={pid} (basket={basket})")

        # family match?
        fam = None
        addr = addr_e.get().strip()
        if addr:
            fam = db_fetchone("""SELECT p.id, pr.slot_id
                                 FROM patients p JOIN prescriptions pr ON p.id=pr.patient_id
                                 WHERE p.address=? AND pr.slot_id IS NOT NULL
                                 LIMIT 1""", (addr,))
        def auto_assign():
            fullname = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0] or ""
            last = fullname.strip().split()[-1] if fullname.strip() else ""
            letter = last[0].upper() if last else "A"
            res = find_next_available_slot_with_overflow(letter, basket)
            if not res:
                messagebox.showwarning("No Slot","No automatic slot available; please assign manually.")
                return self._manual_assign_new(parent, pres_id, refresh_cb)
            slot_ids, shelf = res
            labels = slot_ids_to_labels(slot_ids)
            pos_str = " & ".join([l.split("-")[1] for l in labels])
            conf = ctk.CTkToplevel(parent); conf.title("Confirm Slot"); conf.geometry("560x200")
            _raise_and_grab(conf, parent)
            ctk.CTkLabel(conf, text=f"Proposed Shelf {shelf}: {pos_str}\n(Full: {', '.join(labels)})",
                         font=ctk.CTkFont(size=17)).pack(padx=12, pady=12)
            ctk.CTkButton(conf, text="✅ Confirm", fg_color="#0B5CAB", hover_color="#084b8a",
                          command=lambda: self._confirm_auto(conf, pres_id, slot_ids, labels, parent, refresh_cb)).pack(side="left", padx=10, pady=10)
            ctk.CTkButton(conf, text="✋ Deny / Manual", fg_color="#6B6B6B", hover_color="#585858",
                          command=lambda: (conf.destroy(), self._manual_assign_new(parent, pres_id, refresh_cb))).pack(side="right", padx=10, pady=10)

        if fam:
            fam_pid, fam_slot = fam
            fam_label = slot_id_to_label(fam_slot)
            fp = ctk.CTkToplevel(parent); fp.title("Family Match"); fp.geometry("600x200")
            _raise_and_grab(fp, parent)
            ctk.CTkLabel(fp, text=f"Same address found. Existing bin at {fam_label}.\nBin together?",
                         font=ctk.CTkFont(size=17)).pack(padx=12, pady=12)
            ctk.CTkButton(fp, text="🧺 Bin Together", fg_color="#7A3DB8", hover_color="#652f98",
                          command=lambda: self._bin_together(fp, pres_id, fam_slot, fam_label, parent, refresh_cb)).pack(side="left", padx=10, pady=10)
            ctk.CTkButton(fp, text="Cancel", command=lambda: (fp.destroy(), auto_assign())).pack(side="right", padx=10, pady=10)
        else:
            auto_assign()

    def _confirm_auto(self, conf_win, pres_id, slot_ids, labels, parent, refresh_cb):
        mark_slots_occupied(slot_ids)
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_ids[0], pres_id))
        log_action(f"Auto-assigned prescription id={pres_id} to {', '.join(labels)}")
        conf_win.destroy()
        refresh_cb()
        self.refresh_patient_table()
        open_led_popup(parent, slot_ids, "yellow", "Guide to New Slot")

    def _bin_together(self, fp, pres_id, fam_slot, fam_label, parent, refresh_cb):
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (fam_slot, pres_id))
        log_action(f"Binned prescription id={pres_id} with family at {fam_label}")
        fp.destroy()
        refresh_cb()
        self.refresh_patient_table()
        open_led_popup(parent, [fam_slot], "purple", "Family Bin Location")

    def _manual_assign_new(self, parent, pres_id, refresh_cb):
        m = ctk.CTkToplevel(parent); m.title("Manual Slot Assignment"); m.geometry("460x280")
        _raise_and_grab(m, parent)
        ctk.CTkLabel(m, text="Shelf (e.g., F)", font=ctk.CTkFont(size=17)).grid(row=0, column=0, padx=8, pady=8, sticky="e")
        s_e = ctk.CTkEntry(m, width=100); s_e.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(m, text="Row (A–Z)", font=ctk.CTkFont(size=17)).grid(row=1, column=0, padx=8, pady=8, sticky="e")
        r_e = ctk.CTkEntry(m, width=100); r_e.grid(row=1, column=1, padx=8, pady=8)
        ctk.CTkLabel(m, text="Column (1–N)", font=ctk.CTkFont(size=17)).grid(row=2, column=0, padx=8, pady=8, sticky="e")
        c_e = ctk.CTkEntry(m, width=100); c_e.grid(row=2, column=1, padx=8, pady=8)
        def assign():
            s = s_e.get().strip().upper()
            row = r_e.get().strip().upper()
            try: col = int(c_e.get().strip())
            except: messagebox.showerror("Error","Invalid column"); return
            rec = get_slot_by_position(s, row, col)
            if not rec: messagebox.showerror("Error","Slot does not exist"); return
            slot_id, occ = rec
            if occ: messagebox.showerror("Error","Slot already occupied"); return
            mark_slots_occupied([slot_id])
            db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_id, pres_id))
            log_action(f"Manually assigned prescription id={pres_id} to {slot_id_to_label(slot_id)}")
            m.destroy(); refresh_cb(); self.refresh_patient_table()
            open_led_popup(parent, [slot_id], "yellow", "Guide to New Slot")
        ctk.CTkButton(m, text="Assign", fg_color="#0B5CAB", hover_color="#084b8a", command=assign).grid(row=3, column=0, columnspan=2, pady=12)

    def _edit_prescription_popup(self, patient_id, pres_id, refresh_cb):
        e = ctk.CTkToplevel(self); e.title("Edit Prescription"); e.geometry("680x520")
        _raise_and_grab(e, self)
        head = ctk.CTkFrame(e, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Edit Prescription", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        row = db_fetchone("""SELECT medication,quantity,date_added,basket_size,slot_id
                             FROM prescriptions WHERE id=?""", (pres_id,))
        if not row: e.destroy(); return
        med, qty, dt, basket, slot_id = row

        body = ctk.CTkFrame(e); body.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(body, text="Medication", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        med_e = ctk.CTkEntry(body, width=280); med_e.grid(row=0, column=1, padx=6, pady=6); med_e.insert(0, med)
        ctk.CTkLabel(body, text="Quantity", font=ctk.CTkFont(size=17)).grid(row=1, column=0, sticky="e", padx=6, pady=6)
        qty_e = ctk.CTkEntry(body, width=140); qty_e.grid(row=1, column=1, padx=6, pady=6); qty_e.insert(0, str(qty) if qty is not None else "")
        ctk.CTkLabel(body, text="Date (MM/DD/YYYY)", font=ctk.CTkFont(size=17)).grid(row=2, column=0, sticky="e", padx=6, pady=6)
        date_e = ctk.CTkEntry(body, width=180); date_e.grid(row=2, column=1, padx=6, pady=6)
        if dt:
            for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
                try: date_e.insert(0, datetime.strptime(dt,fmt).strftime("%m/%d/%Y")); break
                except: pass
        ctk.CTkLabel(body, text="Basket", font=ctk.CTkFont(size=17)).grid(row=3, column=0, sticky="e", padx=6, pady=6)
        b_cb = ctk.CTkComboBox(body, values=["small","large"], width=140)
        b_cb.set(basket if basket in ("small","large") else "small"); b_cb.grid(row=3, column=1, padx=6, pady=6)
        ctk.CTkLabel(body, text="LED Location (F-A61 or id)", font=ctk.CTkFont(size=17)).grid(row=4, column=0, sticky="e", padx=6, pady=6)
        loc_e = ctk.CTkEntry(body, width=200); loc_e.grid(row=4, column=1, padx=6, pady=6); loc_e.insert(0, slot_id_to_label(slot_id) if slot_id else "")

        def save_edit():
            new_med = med_e.get().strip()
            new_qty = qty_e.get().strip()
            new_b = b_cb.get().strip().lower()
            dtxt = date_e.get().strip()
            if dtxt:
                try: d = datetime.strptime(dtxt, "%m/%d/%Y").strftime("%Y-%m-%d")
                except: messagebox.showerror("Error","Date must be MM/DD/YYYY"); return
            else:
                d = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_loc = loc_e.get().strip()
            if new_loc and (parse_location_label_to_slot_id(new_loc) is None):
                messagebox.showerror("Error","Invalid LED location. Use F-A61 or numeric id."); return
            new_sid = parse_location_label_to_slot_id(new_loc) if new_loc else None
            if new_sid and new_sid != slot_id:
                # keep this popup front-most:
                e.lift(); e.attributes("-topmost", True); e.update(); e.attributes("-topmost", False)
                if not messagebox.askyesno("Confirm","Manually change LED slot?"): return
                r = db_fetchone("SELECT occupied FROM slots WHERE id=?", (new_sid,))
                if not r: messagebox.showerror("Error","Slot not found"); return
                if r[0]==1: messagebox.showerror("Error","Slot already occupied"); return
                if slot_id: mark_slots_free([slot_id])
                mark_slots_occupied([new_sid])
                db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (new_sid, pres_id))
                log_action(f"Changed prescription id={pres_id} location to {slot_id_to_label(new_sid)}")
            db_exec("""UPDATE prescriptions
                       SET medication=?, quantity=?, date_added=?, basket_size=?
                       WHERE id=?""",
                    (new_med, int(new_qty) if new_qty.isdigit() else new_qty, d, new_b, pres_id))
            log_action(f"Edited prescription id={pres_id} (med='{new_med}', qty='{new_qty}', basket='{new_b}')")
            e.destroy(); refresh_cb(); self.refresh_patient_table()
        ctk.CTkButton(body, text="Save Changes", fg_color="#0B5CAB", hover_color="#084b8a", command=save_edit).grid(row=5, column=0, columnspan=2, pady=12)

    # ---------------- Shelf Assignment (left: letters, right: shelf settings) ----------------
    def open_shelf_assignment(self):
        win = ctk.CTkToplevel(self); win.title("Shelf Assignment"); win.geometry("1260x860")
        _raise_and_grab(win, self)
        head = ctk.CTkFrame(win, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Shelf Assignment", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        ctk.CTkLabel(win, text="Left: Assign letter ranges. Right: Manage shelves (name, rows, cols) and rebuild slots.",
                     font=ctk.CTkFont(size=17)).pack(anchor="w", padx=12, pady=(8,6))

        content = ctk.CTkFrame(win); content.pack(fill="both", expand=True, padx=10, pady=10)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)

        # LEFT: Letter Sections
        left_frame = ctk.CTkFrame(content)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0,8))

        lf_head = ctk.CTkFrame(left_frame); lf_head.pack(fill="x", padx=6, pady=(6,2))
        for i, htxt in enumerate(("Letter","Shelf (F/L/R/…)", "Lower (A1)","Upper (Z13)")):
            ctk.CTkLabel(lf_head, text=htxt, font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=i, padx=8, pady=6, sticky="w")

        canvas = ctk.CTkCanvas(left_frame, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        yscroll = ttk.Scrollbar(left_frame, orient="vertical", command=canvas.yview)
        yscroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=yscroll.set)
        inner = ctk.CTkFrame(canvas)
        inner_id = canvas.create_window((0,0), window=inner, anchor="nw")
        def _on_conf(_):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_conf)

        self.letter_widgets = {}
        letters = [chr(i) for i in range(65,91)] + ["Overflow"]
        shelf_choices = [""] + [r[0] for r in db_fetchall("SELECT shelf FROM shelf_settings ORDER BY shelf")]
        for i,L in enumerate(letters, start=1):
            rowf = ctk.CTkFrame(inner); rowf.grid(row=i, column=0, sticky="w", padx=6, pady=3)
            ctk.CTkLabel(rowf, text=L, width=60, anchor="w", font=ctk.CTkFont(size=17)).grid(row=0, column=0, padx=6)
            shelf_cb = ctk.CTkComboBox(rowf, values=shelf_choices, width=120); shelf_cb.set("")
            lower_e  = ctk.CTkEntry(rowf, width=120)
            upper_e  = ctk.CTkEntry(rowf, width=120)
            shelf_cb.grid(row=0, column=1, padx=8); lower_e.grid(row=0, column=2, padx=8); upper_e.grid(row=0, column=3, padx=8)
            r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (L,))
            if r:
                shelf_cb.set(r[0] or "")
                if r[1]: lower_e.insert(0, r[1])
                if r[2]: upper_e.insert(0, r[2])
            self.letter_widgets[L] = (shelf_cb, lower_e, upper_e)

        def save_letters():
            for L,(s,lo,up) in self.letter_widgets.items():
                S, LO, UP = s.get().strip().upper(), lo.get().strip().upper(), up.get().strip().upper()
                if S and not db_fetchone("SELECT 1 FROM shelf_settings WHERE shelf=?", (S,)):
                    messagebox.showerror("Error", f"Unknown shelf '{S}' for {L}"); return
                if LO and not parse_bound(LO):
                    messagebox.showerror("Error", f"Lower bound invalid for {L}: {LO}"); return
                if UP and not parse_bound(UP):
                    messagebox.showerror("Error", f"Upper bound invalid for {L}: {UP}"); return
                db_exec("INSERT OR REPLACE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                        (L, S, LO, UP))
            log_action("Updated letter sections")
            messagebox.showinfo("Saved", "Letter sections saved.")

        ctk.CTkButton(left_frame, text="💾 Save Letter Sections", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=save_letters).pack(pady=8)

        # RIGHT: Shelf Settings
        right_frame = ctk.CTkFrame(content)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(8,0))
        ctk.CTkLabel(right_frame, text="Shelf Settings", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=10, pady=(10,6))

        # Table-like container
        rf_header = ctk.CTkFrame(right_frame); rf_header.pack(fill="x", padx=10, pady=(6,2))
        for i, htxt in enumerate(("Shelf","Rows","Cols","")):
            ctk.CTkLabel(rf_header, text=htxt, font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=i, padx=8, pady=6, sticky="w")

        rf_canvas = ctk.CTkCanvas(right_frame, highlightthickness=0, height=500)
        rf_canvas.pack(side="left", fill="both", expand=True, padx=(10,0), pady=6)
        rf_scroll = ttk.Scrollbar(right_frame, orient="vertical", command=rf_canvas.yview)
        rf_scroll.pack(side="right", fill="y", padx=(0,10), pady=6)
        rf_canvas.configure(yscrollcommand=rf_scroll.set)
        rf_inner = ctk.CTkFrame(rf_canvas)
        rf_canvas.create_window((0,0), window=rf_inner, anchor="nw")
        rf_inner.bind("<Configure>", lambda e: rf_canvas.configure(scrollregion=rf_canvas.bbox("all")))

        self.shelf_rows_widgets = []  # list of (shelf_entry, rows_entry, cols_entry, delete_button)
        def reload_shelves():
            for child in rf_inner.winfo_children():
                child.destroy()
            self.shelf_rows_widgets.clear()
            data = db_fetchall("SELECT shelf,rows,cols FROM shelf_settings ORDER BY shelf")
            for i,(S,R,C) in enumerate(data):
                rowf = ctk.CTkFrame(rf_inner); rowf.grid(row=i, column=0, sticky="ew", padx=6, pady=3)
                sh_e = ctk.CTkEntry(rowf, width=80); sh_e.grid(row=0, column=0, padx=6); sh_e.insert(0, S)
                r_e  = ctk.CTkEntry(rowf, width=80); r_e.grid(row=0, column=1, padx=6); r_e.insert(0, str(R))
                c_e  = ctk.CTkEntry(rowf, width=80); c_e.grid(row=0, column=2, padx=6); c_e.insert(0, str(C))
                def make_del(sname=S):
                    return ctk.CTkButton(rowf, text="🗑️", width=36, fg_color="#D83B01", hover_color="#B32F00",
                                         command=lambda: self._delete_shelf_row(sname, reload_shelves))
                del_b = make_del()
                del_b.grid(row=0, column=3, padx=6)
                self.shelf_rows_widgets.append((sh_e,r_e,c_e,del_b))
        reload_shelves()

        def add_shelf_row():
            idx = len(self.shelf_rows_widgets)
            rowf = ctk.CTkFrame(rf_inner); rowf.grid(row=idx, column=0, sticky="ew", padx=6, pady=3)
            sh_e = ctk.CTkEntry(rowf, width=80); sh_e.grid(row=0, column=0, padx=6)
            r_e  = ctk.CTkEntry(rowf, width=80); r_e.grid(row=0, column=1, padx=6)
            c_e  = ctk.CTkEntry(rowf, width=80); c_e.grid(row=0, column=2, padx=6)
            del_b = ctk.CTkButton(rowf, text="🗑️", width=36, fg_color="#D83B01", hover_color="#B32F00",
                                  command=lambda: (rowf.destroy(), self.shelf_rows_widgets.remove((sh_e,r_e,c_e,del_b))))
            del_b.grid(row=0, column=3, padx=6)
            self.shelf_rows_widgets.append((sh_e,r_e,c_e,del_b))

        def save_and_rebuild():
            # Validate & save shelf_settings, then rebuild slots
            seen = set()
            rows = []
            for (sh_e,r_e,c_e,_) in self.shelf_rows_widgets:
                S = sh_e.get().strip().upper()
                if not S: continue
                if S in seen:
                    messagebox.showerror("Error", f"Duplicate shelf '{S}'"); return
                seen.add(S)
                try:
                    R = int(r_e.get().strip())
                    C = int(c_e.get().strip())
                except:
                    messagebox.showerror("Error", f"Rows/Cols must be integers for shelf '{S}'"); return
                if R < 1 or C < 1:
                    messagebox.showerror("Error", f"Rows/Cols must be >=1 for shelf '{S}'"); return
                rows.append((S,R,C))
            # Save settings (replace all)
            with db_connect() as conn:
                cur = conn.cursor()
                cur.execute("BEGIN")
                cur.execute("DELETE FROM shelf_settings")
                for S,R,C in rows:
                    cur.execute("INSERT INTO shelf_settings(shelf,rows,cols) VALUES(?,?,?)", (S,R,C))
                conn.commit()
            # Rebuild slots
            rebuild_slots_from_settings()
            log_action("Updated shelf settings and rebuilt slots")
            # Refresh left shelf dropdowns
            for L,(s,lo,up) in self.letter_widgets.items():
                s.configure(values=[""] + [r[0] for r in db_fetchall("SELECT shelf FROM shelf_settings ORDER BY shelf")])
            messagebox.showinfo("Done", "Shelf settings saved and slots rebuilt.")
            win.destroy()

        btns = ctk.CTkFrame(right_frame); btns.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(btns, text="➕ Add Shelf", command=add_shelf_row).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="💾 Save & Rebuild Slots", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=save_and_rebuild).pack(side="right", padx=6)

    def _delete_shelf_row(self, shelf_name, reload_fn):
        # Remove from DB + refresh panel
        if not messagebox.askyesno("Confirm", f"Delete shelf '{shelf_name}' from settings?"):
            return
        db_exec("DELETE FROM shelf_settings WHERE shelf=?", (shelf_name,))
        reload_fn()

    # ---------------- Overdue Tab ----------------
    def open_overdue_tab(self):
        data = get_overdue_prescriptions(14)
        agg = aggregate_overdue_by_patient(data)

        win = ctk.CTkToplevel(self); win.title("Overdue Medications"); win.geometry("1180x820")
        _raise_and_grab(win, self)
        head = ctk.CTkFrame(win, fg_color="#c50f1f"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Overdue Medications", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        top = ctk.CTkFrame(win); top.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(top, text="🔴 Light Up Overdue", fg_color="#D83B01", hover_color="#B32F00",
                      command=lambda: self._light_up_overdue(data, win)).pack(side="left", padx=6)
        cnt = ctk.CTkLabel(top, text=f"Total overdue prescriptions: {len(data)}", font=ctk.CTkFont(size=17))
        cnt.pack(side="right", padx=6)

        tablef = ctk.CTkFrame(win); tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Patient","Address","# Overdue","Oldest (days)","Locations")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=240 if c!="Locations" else 420, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        tv.tag_configure("odd", background="#f5f9ff")
        tv.tag_configure("even", background="#ffffff")

        for i,(pid, v) in enumerate(agg.items()):
            locs = ", ".join(slot_ids_to_labels(sorted(list(v["slots"]))))
            tag = "odd" if i%2 else "even"
            tv.insert("", "end", iid=str(pid),
                      values=(v["name"], v["address"] or "", v["count"], v["oldest"], locs),
                      tags=(tag,))
        tv.bind("<Double-1>", lambda e: self._open_overdue_patient_detail(tv))

    def _open_overdue_patient_detail(self, tree):
        sel = tree.selection()
        if not sel: return
        pid = int(sel[0])
        items = [o for o in get_overdue_prescriptions(14) if o["patient_id"] == pid]
        if not items:
            messagebox.showinfo("Overdue","No overdue items (refresh?)"); return
        win = ctk.CTkToplevel(self); win.title("Overdue Details"); win.geometry("960x560")
        _raise_and_grab(win, self)
        head = ctk.CTkFrame(win, fg_color="#c50f1f"); head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Overdue — {items[0]['name']}", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        top = ctk.CTkFrame(win); top.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(top, text="🔴 Light Up These", fg_color="#D83B01", hover_color="#B32F00",
                      command=lambda: self._light_up_overdue(items, win)).pack(side="left")
        tablef = ctk.CTkFrame(win); tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Medication","Quantity","Days Overdue","Location")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=240 if c!="Location" else 280, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        for it in items:
            loc = slot_id_to_label(it["slot_id"]) if it["slot_id"] else ""
            tv.insert("", "end", values=(it["medication"], it["quantity"], it["days_overdue"], loc))

    def _light_up_overdue(self, items, parent):
        slots = sorted(list({it["slot_id"] for it in items if it["slot_id"]}))
        if not slots:
            messagebox.showinfo("LED","No overdue items with assigned slots."); return
        open_led_popup(parent, slots, "red", "Overdue — LED")

    # ---------------- Previous Actions ----------------
    def open_actions_tab(self):
        win = ctk.CTkToplevel(self); win.title("Previous Actions"); win.geometry("1100x700")
        _raise_and_grab(win, self)
        head = ctk.CTkFrame(win, fg_color="#6b6b6b"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Previous Actions (Today)", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        tablef = ctk.CTkFrame(win); tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Time","Actor","Action")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=200 if c!="Action" else 820, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        tv.tag_configure("odd", background="#f5f9ff")
        tv.tag_configure("even", background="#ffffff")
        acts = get_todays_actions(300)
        for i,(ts,actor,action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tag = "odd" if i%2 else "even"
            tv.insert("", "end", values=(t,actor,action), tags=(tag,))
        ctk.CTkButton(win, text="🔄 Refresh",
                      command=lambda: self._refresh_actions(tv)).pack(pady=8)

    def _refresh_actions(self, tv):
        for r in tv.get_children(): tv.delete(r)
        acts = get_todays_actions(300)
        for i,(ts,actor,action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tag = "odd" if i%2 else "even"
            tv.insert("", "end", values=(t,actor,action), tags=(tag,))

    # ---------------- Dashboard ----------------
    def open_dashboard(self):
        win = ctk.CTkToplevel(self); win.title("Dashboard"); win.geometry("1300x900")
        _raise_and_grab(win, self)

        # KPI cards
        cards = ctk.CTkFrame(win); cards.pack(fill="x", padx=10, pady=8)
        def kpi_box(parent, title, value, color):
            f = ctk.CTkFrame(parent); f.pack(side="left", expand=True, fill="x", padx=8)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=10, pady=(10,4))
            lab = ctk.CTkLabel(f, text=str(value), font=ctk.CTkFont(size=28, weight="bold"), text_color=color)
            lab.pack(anchor="w", padx=10, pady=(0,10))
            return lab

        total_pat = count_patients()
        total_rx = count_prescriptions()
        total, used, free, pct = slots_usage()
        overdue_count = count_overdue()

        k1 = kpi_box(cards, "Total Patients", total_pat, "#24a148")
        k2 = kpi_box(cards, "Total Prescriptions", total_rx, "#0b5cab")
        k3 = kpi_box(cards, "Overdue Prescriptions", overdue_count, "#c50f1f")
        k4 = kpi_box(cards, "Slots Used / Free", f"{used}/{free} ({pct:.1f}%)", "#d17b00")

        # Charts area
        charts = ctk.CTkFrame(win); charts.pack(fill="both", expand=True, padx=10, pady=8)
        charts.grid_columnconfigure(0, weight=1)
        charts.grid_columnconfigure(1, weight=1)
        charts.grid_rowconfigure(0, weight=1)

        # Chart 1: Prescriptions per day (last 30)
        fig1 = Figure(figsize=(6,3), dpi=100)
        ax1 = fig1.add_subplot(111)
        xs, ys = prescriptions_per_day(30)
        ax1.bar(range(len(xs)), ys)
        ax1.set_title("Prescriptions per day (last 30)", fontsize=12)
        ax1.set_xticks(range(0, len(xs), 5))
        ax1.set_xticklabels([xs[i][5:] for i in range(0, len(xs), 5)], rotation=0, fontsize=8)
        ax1.set_ylabel("Count")
        can1 = FigureCanvasTkAgg(fig1, charts); can1.draw()
        can1.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        # Chart 2: Overdue trend (last 30)
        fig2 = Figure(figsize=(6,3), dpi=100)
        ax2 = fig2.add_subplot(111)
        xs2, ys2 = overdue_trend(30)
        ax2.plot(range(len(xs2)), ys2)
        ax2.set_title("Overdue count trend (last 30)", fontsize=12)
        ax2.set_xticks(range(0, len(xs2), 5))
        ax2.set_xticklabels([xs2[i][5:] for i in range(0, len(xs2), 5)], rotation=0, fontsize=8)
        ax2.set_ylabel("Count")
        can2 = FigureCanvasTkAgg(fig2, charts); can2.draw()
        can2.get_tk_widget().grid(row=0, column=1, sticky="nsew", padx=6, pady=6)

        # Chart 3: Shelf utilization
        fig3 = Figure(figsize=(12,3.2), dpi=100)
        ax3 = fig3.add_subplot(111)
        util = shelf_utilization()
        names = [u[0] for u in util]
        useds = [u[2] for u in util]
        totals = [u[1] for u in util]
        ax3.bar(range(len(names)), totals, label="Total", alpha=0.4)
        ax3.bar(range(len(names)), useds, label="Used")
        ax3.set_title("Shelf utilization", fontsize=12)
        ax3.set_xticks(range(len(names)))
        ax3.set_xticklabels(names, fontsize=10)
        ax3.set_ylabel("Slots")
        ax3.legend()
        can3 = FigureCanvasTkAgg(fig3, win); can3.draw()
        can3.get_tk_widget().pack(fill="x", padx=16, pady=(2,10))

        # Recent actions + quick actions
        bottom = ctk.CTkFrame(win); bottom.pack(fill="both", expand=True, padx=10, pady=8)
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bottom, text="Recent Actions (Today)", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(bottom, text="Quick Actions", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        # Recent actions table
        actf = ctk.CTkFrame(bottom); actf.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)
        cols = ("Time","Actor","Action")
        tv = ttk.Treeview(actf, columns=cols, show="headings", height=8)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=160 if c!="Action" else 600, anchor="w")
        tv.pack(fill="both", expand=True)
        acts = get_todays_actions(20)
        for i,(ts,actor,action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tv.insert("", "end", values=(t, actor, action))

        # Quick actions
        qa = ctk.CTkFrame(bottom); qa.grid(row=1, column=1, sticky="nsew", padx=8, pady=6)
        ctk.CTkButton(qa, text="🔴 Blink All Overdue", fg_color="#D83B01", hover_color="#B32F00",
                      command=lambda: self._light_up_overdue(get_overdue_prescriptions(14), win)).pack(fill="x", padx=6, pady=6)
        ctk.CTkButton(qa, text="🔄 Refresh Dashboard",
                      command=lambda: (win.destroy(), self.open_dashboard())).pack(fill="x", padx=6, pady=6)

# =========================
# Boot
# =========================
if __name__ == "__main__":
    print("DB:", DB_PATH)
    init_db(reset=False)
    init_actions_table()
    ensure_slots_exist()
    app = App()
    app.mainloop()
