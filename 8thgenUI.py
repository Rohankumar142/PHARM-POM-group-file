# =====================================================
# Pharmacy LED System - 6th Generation UI
# Full Program Part 1 of 4
# =====================================================
# Features included:
# - Patients (search, add, edit, delete, clear prescriptions)
# - Prescription management (add, edit, delete, auto/manual assign, family binning)
# - LED simulation with pause/resume/done
# - Shelf assignment (custom shelves, row/col config)
# - Overdue meds tab with red blinking
# - Previous actions tab
# - Dashboard with charts (prescriptions, overdue, shelf occupancy)
# - CustomTkinter modern UI

import os, sqlite3, threading, time
from datetime import datetime
import customtkinter as ctk
from tkinter import ttk, messagebox
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# =====================================================
# Paths / DB helpers
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pharmacy.db")

def db_connect():
    return sqlite3.connect(DB_PATH, timeout=5)

def db_exec(sql, params=(), commit=True):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if commit: conn.commit()
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

# =====================================================
# Actions log
# =====================================================
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

# =====================================================
# LED simulation (prints with labels, pause/resume/done)
# =====================================================
_blink_groups = {}

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

def open_led_popup(parent, slots, color="yellow", title="LED Control"):
    if not slots:
        messagebox.showinfo("LED", "No slots to blink."); return
    key = start_blink(slots, color)
    win = ctk.CTkToplevel(parent); win.title(title)
    win.geometry("520x160")
    win.lift(); win.attributes("-topmost", True); win.focus_force()
    ctk.CTkLabel(win, text=f"Blinking: {', '.join(slot_ids_to_labels(slots))}",
                 font=ctk.CTkFont(size=17)).pack(padx=14, pady=(14,6))
    btn = ctk.CTkButton(win, text="⏸ Pause Lights", corner_radius=8, fg_color="#0B5CAB",
                        hover_color="#084b8a",
                        command=lambda: _toggle_pause(btn, key))
    btn.pack(side="left", padx=12, pady=12)
    ctk.CTkButton(win, text="✅ Done", corner_radius=8, fg_color="#D83B01",
                  hover_color="#B32F00",
                  command=lambda: (stop_blink(key), win.destroy())).pack(side="right", padx=12, pady=12)

def _toggle_pause(button, key):
    pause_toggle(key)
    if button.cget("text").startswith("⏸"):
        button.configure(text="▶ Resume Lights")
    else:
        button.configure(text="⏸ Pause Lights")

# =====================================================
# DB schema & seed
# =====================================================
def init_db(reset=False):
    with db_connect() as conn:
        cur = conn.cursor()
        if reset:
            for t in ["patients","prescriptions","letter_sections","slots","actions_log","shelves"]:
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
        cur.execute("""CREATE TABLE IF NOT EXISTS shelves(
            name TEXT PRIMARY KEY,
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
    if not db_fetchone("SELECT 1 FROM letter_sections"):
        for L in [chr(i) for i in range(65,91)] + ["Overflow"]:
            db_exec("INSERT OR IGNORE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                    (L, "", "", ""))

# =====================================================
# Pharmacy LED System - 6th Generation UI
# Full Program Part 2 of 4
# =====================================================

# =====================================================
# Slot population
# =====================================================
def populate_slots():
    cnt = db_fetchone("SELECT COUNT(*) FROM slots")
    if cnt and cnt[0] > 0: return
    shelves = db_fetchall("SELECT name,rows,cols FROM shelves")
    for name, rows, cols in shelves:
        for i in range(rows):
            row = chr(65+i)  # A, B, C...
            for col in range(1, cols+1):
                db_exec("INSERT INTO slots(shelf,row,col,occupied) VALUES(?,?,?,0)", (name,row,col))
    print("Slots populated.")

# =====================================================
# Section / Slot helpers
# =====================================================
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
        for r in rows_range(sr, er):
            cstart = sc if r==sr else 1
            cend   = ec if r==er else 100
            for c in range(cstart, cend+1):
                cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?",(shelf,r,c))
                k = cur.fetchone()
                if not k: continue
                sid, occ = k
                if occ == 0:
                    if basket=="large":
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
        for s in slot_ids:
            cur.execute("UPDATE slots SET occupied=1 WHERE id=?", (s,))
        conn.commit()

def mark_slots_free(slot_ids):
    if not slot_ids: return
    with db_connect() as conn:
        cur = conn.cursor()
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

# =====================================================
# Overdue helpers
# =====================================================
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
        if it["days_overdue"] > agg[pid]["oldest"]:
            agg[pid]["oldest"] = it["days_overdue"]
        if it["slot_id"]:
            agg[pid]["slots"].add(it["slot_id"])
    return agg

# =====================================================
# Treeview styling for CustomTkinter container
# =====================================================
def style_treeview():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except:
        pass
    style.configure("Treeview",
                    background="white",
                    fieldbackground="white",
                    rowheight=28,
                    borderwidth=0,
                    font=("Segoe UI", 16))
    style.configure("Treeview.Heading",
                    background="#0078d4",
                    foreground="white",
                    font=("Segoe UI Semibold", 20))
    style.map("Treeview", background=[("selected","#cce6ff")])
# =====================================================
# Pharmacy LED System - 6th Generation UI
# Full Program Part 3 of 4
# =====================================================
# This part defines the App class:
# - Main window (top bar, sidebar, search + sort + filter)
# - Patients table (double-click to open patient popup)
# - Patient popup (edit name/address, add/edit/delete prescriptions)
# - Auto-assign (with family binning chooser), manual assign, LED popups
# - Clear-all prescriptions with LED confirm
# (Shelf Assignment, Dashboard, Overdue, Actions tabs come in Part 4)
# =====================================================

# ---------- utility for last-name-aware sorting ----------
def _last_name_key(fullname: str) -> str:
    if not fullname: return ""
    parts = [p for p in fullname.strip().split() if p]
    return parts[-1].lower() if parts else fullname.lower()

# ---------- UI styling ----------
def _title_bar(frame, text):
    lbl = ctk.CTkLabel(frame, text=text,
                       font=ctk.CTkFont(size=20, weight="bold"))
    lbl.pack(anchor="w", padx=12, pady=8)
    return lbl

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("Pharmacy LED System")
        self.geometry("1380x900")
        self.minsize(1200, 760)

        style_treeview()

        # ========= Top bar =========
        topbar = ctk.CTkFrame(self, corner_radius=0)
        topbar.pack(side="top", fill="x")
        ctk.CTkLabel(topbar, text="💊 Pharmacy LED System",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left", padx=12, pady=8)

        self.theme_mode = ctk.StringVar(value="Dark")
        def toggle_theme():
            mode = "Light" if self.theme_mode.get()=="Dark" else "Dark"
            self.theme_mode.set(mode)
            ctk.set_appearance_mode(mode)
        ctk.CTkButton(topbar, text="🌓 Theme", width=90, command=toggle_theme).pack(side="right", padx=10, pady=8)

        # ========= Right sidebar =========
        sidebar = ctk.CTkFrame(self, width=260, corner_radius=0)
        sidebar.pack(side="right", fill="y")
        ctk.CTkLabel(sidebar, text="Tools", font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(12, 6))
        ctk.CTkButton(sidebar, text="📚 Shelf Assignment", command=self.open_shelf_assignment).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📊 Dashboard", command=self.open_dashboard_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="⏰ Overdue Meds", command=self.open_overdue_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📝 Previous Actions", command=self.open_actions_tab).pack(padx=10, pady=6, fill="x")

        # ========= Header =========
        header = ctk.CTkFrame(self, fg_color="#0b5cab")
        header.pack(fill="x", padx=0, pady=(0,6))
        _title_bar(header, "Patients")

        # ========= Controls: Search + Sort + Filter =========
        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", padx=10, pady=(0,8))

        # Search
        ctk.CTkLabel(controls, text="Search:",
                     font=ctk.CTkFont(size=17)).pack(side="left", padx=(8,6))
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(controls, textvariable=self.search_var, width=300)
        search_entry.pack(side="left", padx=(0,10), ipady=4)
        search_entry.bind("<Return>", lambda e: self.refresh_patient_table())

        # Sort dropdown
        ctk.CTkLabel(controls, text="Sort:",
                     font=ctk.CTkFont(size=17)).pack(side="left", padx=(8,6))
        self.sort_var = ctk.StringVar(value="Last Name A–Z")
        self.sort_cb = ctk.CTkComboBox(
            controls,
            values=[
                "Last Name A–Z",
                "Last Name Z–A",
                "Recently Added (Newest)",
                "Recently Added (Oldest)",
                "Address A–Z"
            ],
            variable=self.sort_var, width=220
        )
        self.sort_cb.pack(side="left", padx=(0,10))
        self.sort_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_patient_table())

        # Filter dropdown
        ctk.CTkLabel(controls, text="Filter:",
                     font=ctk.CTkFont(size=17)).pack(side="left", padx=(8,6))
        self.filter_var = ctk.StringVar(value="All patients")
        self.filter_cb = ctk.CTkComboBox(
            controls,
            values=[
                "All patients",
                "Has prescriptions",
                "Has overdue prescriptions",
                "No prescriptions"
            ],
            variable=self.filter_var, width=240
        )
        self.filter_cb.pack(side="left", padx=(0,10))
        self.filter_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_patient_table())

        # Clear + Refresh + Add/Delete
        ctk.CTkButton(controls, text="❌ Clear Search",
                      command=self._clear_search).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="🔄 Refresh",
                      command=self.refresh_patient_table).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="➕ Add Patient", fg_color="#2F8B2F", hover_color="#277327",
                      command=self.add_patient_popup).pack(side="right", padx=6)
        ctk.CTkButton(controls, text="🗑️ Delete Patient", fg_color="#D83B01", hover_color="#B32F00",
                      command=self.delete_selected_patient).pack(side="right", padx=6)

        # Status label
        self.status_lbl = ctk.CTkLabel(self, text="",
                                       font=ctk.CTkFont(size=16))
        self.status_lbl.pack(anchor="w", padx=14, pady=(0,6))

        # ========= Patients table =========
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.tree = ttk.Treeview(table_frame, columns=("Name","Address","Date Added","Locations"), show="headings", selectmode="browse")
        for c in ("Name","Address","Date Added","Locations"):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=240 if c!="Locations" else 520, anchor="w")
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscroll=sb.set)
        self.tree.tag_configure("odd", background="#f5f9ff")
        self.tree.tag_configure("even", background="#ffffff")
        self.tree.bind("<Double-1>", self.on_patient_double)

        self.refresh_patient_table()

    # ---------------- Search / Sort / Filter logic ----------------
    def _clear_search(self):
        self.search_var.set("")
        self.sort_var.set("Last Name A–Z")
        self.filter_var.set("All patients")
        self.refresh_patient_table()

    def _current_status_text(self, total):
        return f"Showing {total} patients | Sort: {self.sort_var.get()} | Filter: {self.filter_var.get()}"

    def refresh_patient_table(self):
        # Build patient list (with search across name/address/meds)
        q = (self.search_var.get() or "").strip()
        filter_mode = self.filter_var.get()
        sort_mode = self.sort_var.get()

        # Base: fetch patients (id, name, address, created_at)
        patients = db_fetchall("SELECT id,name,address,created_at FROM patients")

        # Preload prescriptions per patient and slot locations
        pres_by_patient = {}
        for pid, med, qty, sid in db_fetchall("SELECT patient_id, medication, quantity, slot_id FROM prescriptions"):
            pres_by_patient.setdefault(pid, []).append((med, qty, sid))

        # Overdue set for filtering if needed
        overdue = get_overdue_prescriptions(14)
        overdue_pids = {o["patient_id"] for o in overdue}

        # Filter by search
        def matches_search(pid, name, address):
            if not q: return True
            qc = q.lower()
            if qc in (name or "").lower(): return True
            if qc in (address or "").lower(): return True
            # meds
            for (med, _qty, _sid) in pres_by_patient.get(pid, []):
                if qc in (med or "").lower():
                    return True
            return False

        # Filter by filter_mode
        def matches_filter(pid):
            if filter_mode == "All patients":
                return True
            elif filter_mode == "Has prescriptions":
                return pid in pres_by_patient and len(pres_by_patient[pid]) > 0
            elif filter_mode == "Has overdue prescriptions":
                return pid in overdue_pids
            elif filter_mode == "No prescriptions":
                return pid not in pres_by_patient or len(pres_by_patient[pid]) == 0
            return True

        # Build rows with locations
        rows = []
        for (pid, name, addr, created) in patients:
            if not matches_search(pid, name, addr): continue
            if not matches_filter(pid): continue
            # Locations
            slot_rows = [sid for (_m, _q, sid) in pres_by_patient.get(pid, []) if sid]
            locs = ", ".join([slot_id_to_label(s) for s in sorted(set(slot_rows)) if s])
            rows.append((pid, name, addr, created, locs))

        # Sort rows
        if sort_mode == "Last Name A–Z":
            rows.sort(key=lambda r: (_last_name_key(r[1]), r[1].lower()))
        elif sort_mode == "Last Name Z–A":
            rows.sort(key=lambda r: (_last_name_key(r[1]), r[1].lower()), reverse=True)
        elif sort_mode == "Recently Added (Newest)":
            rows.sort(key=lambda r: (r[3] or ""), reverse=True)
        elif sort_mode == "Recently Added (Oldest)":
            rows.sort(key=lambda r: (r[3] or ""))
        elif sort_mode == "Address A–Z":
            rows.sort(key=lambda r: (r[2] or "").lower())

        # Populate table
        for r in self.tree.get_children(): self.tree.delete(r)
        for i,(pid, name, addr, created, locs) in enumerate(rows):
            disp_date = ""
            if created:
                for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
                    try:
                        disp_date = datetime.strptime(created, fmt).strftime("%m/%d/%Y")
                        break
                    except: pass
            tag = "odd" if i%2 else "even"
            self.tree.insert("", "end", iid=str(pid), values=(name, addr or "", disp_date, locs or ""), tags=(tag,))
        self.status_lbl.configure(text=self._current_status_text(len(rows)))

    # ---------------- Patient CRUD ----------------
    def add_patient_popup(self):
        p = ctk.CTkToplevel(self); p.title("Add Patient"); p.geometry("560x250")
        p.lift(); p.attributes("-topmost", True); p.focus_force()
        head = ctk.CTkFrame(p, fg_color="#0b5cab"); head.pack(fill="x")
        _title_bar(head, "Add Patient")
        body = ctk.CTkFrame(p); body.pack(fill="both", expand=True, padx=12, pady=10)

        ctk.CTkLabel(body, text="Name", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=8, pady=8)
        name_e = ctk.CTkEntry(body, width=360); name_e.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(body, text="Address", font=ctk.CTkFont(size=17)).grid(row=1, column=0, sticky="e", padx=8, pady=8)
        addr_e = ctk.CTkEntry(body, width=360); addr_e.grid(row=1, column=1, padx=8, pady=8)

        def save():
            nm = name_e.get().strip()
            if not nm:
                messagebox.showerror("Error","Name required"); return
            db_exec("INSERT INTO patients(name,address,created_at) VALUES(?,?,?)",
                    (nm, addr_e.get().strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            log_action(f"Added patient: {nm}")
            p.destroy(); self.refresh_patient_table()

        ctk.CTkButton(body, text="Save", fg_color="#2F8B2F", hover_color="#277327", command=save)\
            .grid(row=2, column=0, columnspan=2, pady=10)

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
        win.geometry("1200x820")
        win.lift(); win.attributes("-topmost", True); win.focus_force()

        head = ctk.CTkFrame(win, fg_color="#107c10"); head.pack(fill="x")
        _title_bar(head, f"Patient — {pname}")

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
            rx.column(c, width=220 if c!="Location" else 300, anchor="w")
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
                warn = self._location_warning_for_patient(pid, sid, loc)
                if warn:
                    loc = loc + f"  —  {warn}"
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

        # Delete prescriptions (button + Delete key)
        def delete_selected_prescriptions():
            sel = rx.selection()
            if not sel:
                messagebox.showinfo("Delete", "No prescription selected."); return
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
        basket_cb = ctk.CTkComboBox(addf, values=["small","large"], width=140)
        basket_cb.set("small"); basket_cb.grid(row=0, column=5, padx=6)
        ctk.CTkButton(addf, text="➕ Add Prescription", fg_color="#2F8B2F", hover_color="#277327",
                      command=lambda: self._add_prescription(pid, addr_e, med_e, qty_e, basket_cb, win, populate_rx))\
            .grid(row=0, column=6, padx=10)
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
        # Determine slots to blink and confirmation
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
        sids = sorted({r[0] for r in rows if r and r[0]})
        if not sids:
            if not messagebox.askyesno("Confirm", f"Clear ALL prescriptions for {name}? (no assigned locations)"):
                return
            db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
            log_action(f"Cleared all prescriptions for: {name}")
            popup.destroy()
            self.refresh_patient_table()
            return

        # Show LED and confirm dialog
        ledw = ctk.CTkToplevel(popup); ledw.title("Confirm Shelf Empty")
        ledw.geometry("560x220"); ledw.lift(); ledw.attributes("-topmost", True); ledw.focus_force()
        ctk.CTkLabel(ledw, text=f"Please verify all bins are empty for {name}.",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(padx=12, pady=(12,6))
        ctk.CTkLabel(ledw, text=f"Blinking: {', '.join(slot_ids_to_labels(sids))}",
                     font=ctk.CTkFont(size=16)).pack(padx=12, pady=(0,6))
        key = start_blink(sids, "blue")

        btns = ctk.CTkFrame(ledw); btns.pack(fill="x", padx=12, pady=12)
        def done():
            stop_blink(key)
            mark_slots_free(sids)
            db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
            log_action(f"Cleared all prescriptions for: {name}")
            ledw.destroy(); popup.destroy()
            self.refresh_patient_table()
        def pause_toggle_ui(b):
            pause_toggle(key)
            if b.cget("text").startswith("⏸"):
                b.configure(text="▶ Resume Lights")
            else:
                b.configure(text="⏸ Pause Lights")

        ctk.CTkButton(btns, text="⏸ Pause Lights", command=lambda b=None: pause_toggle_ui(pause_btn)).pack(side="left", padx=6)
        pause_btn = ctk.CTkButton(btns, text="⏸ Pause Lights", command=lambda: pause_toggle_ui(pause_btn))
        pause_btn.pack_forget()  # replaced above; keep API
        pause_btn = btns.winfo_children()[0]
        ctk.CTkButton(btns, text="✅ Confirm Empty", fg_color="#2F8B2F", hover_color="#277327", command=done).pack(side="right", padx=6)
        ctk.CTkButton(btns, text="Cancel", command=lambda: (stop_blink(key), ledw.destroy())).pack(side="right", padx=6)

    # ---------------- Add prescription (with family binning + auto assign) ----------------
    def _add_prescription(self, pid, addr_e, med_e, qty_e, basket_cb, parent, refresh_cb):
        med = med_e.get().strip(); qty = qty_e.get().strip(); basket = basket_cb.get().strip().lower()
        if not med or not qty or basket not in ("small","large"):
            messagebox.showerror("Error","Enter medication, quantity, and basket size."); return
        qv = int(qty) if qty.isdigit() else qty
        db_exec("""INSERT INTO prescriptions(patient_id,medication,quantity,date_added,basket_size)
                   VALUES(?,?,?,?,?)""",
                (pid, med, qv, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), basket))
        pres_id = db_fetchone("SELECT id FROM prescriptions WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,))[0]
        log_action(f"Added prescription '{med}' for patient_id={pid} (basket={basket})")

        # family/self bin chooser
        addr = addr_e.get().strip()
        fam_candidates = []
        if addr:
            fam_candidates = db_fetchall("""SELECT DISTINCT p.id, p.name, pr.slot_id
                                            FROM patients p
                                            JOIN prescriptions pr ON pr.patient_id = p.id
                                            WHERE p.address=? AND pr.slot_id IS NOT NULL""", (addr,))
        # Remove duplicates and exclude same pres just created
        fam_candidates = [(i, n, s) for (i, n, s) in fam_candidates if s]

        def after_fam_choice(chosen_slot):
            if chosen_slot is not None:
                # Bin together at chosen slot
                db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (chosen_slot, pres_id))
                log_action(f"Binned prescription id={pres_id} at {slot_id_to_label(chosen_slot)}")
                refresh_cb(); self.refresh_patient_table()
                open_led_popup(parent, [chosen_slot], "purple", "Family Bin Location")
                return
            # No family bin -> auto-assign workflow
            self._auto_assign_flow(pid, basket, pres_id, parent, refresh_cb)

        if fam_candidates:
            self._choose_family_bin(parent, fam_candidates, after_fam_choice)
        else:
            self._auto_assign_flow(pid, basket, pres_id, parent, refresh_cb)

    def _choose_family_bin(self, parent, candidates, on_done):
        # candidates: list of (pid, name, slot_id)
        w = ctk.CTkToplevel(parent); w.title("Family / Existing Bin Matches")
        w.geometry("640x360"); w.lift(); w.attributes("-topmost", True); w.focus_force()
        _title_bar(w, "Family or Same-Address Bins")
        ctk.CTkLabel(w, text="Select a bin to share, or click 'Skip' for auto-assign.",
                     font=ctk.CTkFont(size=17)).pack(anchor="w", padx=12, pady=(0,8))

        cols = ("Patient","Location")
        tv = ttk.Treeview(w, columns=cols, show="headings", height=8)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=260 if c=="Patient" else 280, anchor="w")
        tv.pack(fill="both", expand=True, padx=12, pady=10)
        for (pid, name, sid) in candidates:
            tv.insert("", "end", iid=str(sid), values=(name, slot_id_to_label(sid)))
        btns = ctk.CTkFrame(w); btns.pack(fill="x", padx=12, pady=10)
        def choose():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("Pick one", "Select a bin, or click Skip."); return
            sid = int(sel[0])
            w.destroy(); on_done(sid)
        ctk.CTkButton(btns, text="🧺 Bin With Selected", fg_color="#7A3DB8", hover_color="#652f98", command=choose).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Skip (Auto-Assign)", command=lambda: (w.destroy(), on_done(None))).pack(side="right", padx=6)

    # ---------------- Auto-assign (confirm -> blink with control) ----------------
    def _auto_assign_flow(self, pid, basket, pres_id, parent, refresh_cb):
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
        conf.lift(); conf.attributes("-topmost", True); conf.focus_force()
        ctk.CTkLabel(conf, text=f"Proposed Shelf {shelf}: {pos_str}\n(Full: {', '.join(labels)})",
                     font=ctk.CTkFont(size=17)).pack(padx=12, pady=12)
        ctk.CTkButton(conf, text="✅ Confirm", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=lambda: self._confirm_auto(conf, pres_id, slot_ids, labels, parent, refresh_cb)).pack(side="left", padx=10, pady=10)
        ctk.CTkButton(conf, text="✋ Deny / Manual", fg_color="#6B6B6B", hover_color="#585858",
                      command=lambda: (conf.destroy(), self._manual_assign_new(parent, pres_id, refresh_cb))).pack(side="right", padx=10, pady=10)

    def _confirm_auto(self, conf_win, pres_id, slot_ids, labels, parent, refresh_cb):
        mark_slots_occupied(slot_ids)
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_ids[0], pres_id))
        log_action(f"Auto-assigned prescription id={pres_id} to {', '.join(labels)}")
        conf_win.destroy()
        refresh_cb()
        self.refresh_patient_table()
        open_led_popup(parent, slot_ids, "yellow", "Guide to New Slot")

    # ---------------- Manual assign (new) ----------------
    def _manual_assign_new(self, parent, pres_id, refresh_cb):
        m = ctk.CTkToplevel(parent); m.title("Manual Slot Assignment"); m.geometry("460x260")
        m.lift(); m.attributes("-topmost", True); m.focus_force()
        ctk.CTkLabel(m, text="Shelf (name e.g. F/L/R)", font=ctk.CTkFont(size=17)).grid(row=0, column=0, padx=8, pady=8, sticky="e")
        s_e = ctk.CTkEntry(m, width=120); s_e.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(m, text="Row (A–Z)", font=ctk.CTkFont(size=17)).grid(row=1, column=0, padx=8, pady=8, sticky="e")
        r_e = ctk.CTkEntry(m, width=120); r_e.grid(row=1, column=1, padx=8, pady=8)
        ctk.CTkLabel(m, text="Column (1..N)", font=ctk.CTkFont(size=17)).grid(row=2, column=0, padx=8, pady=8, sticky="e")
        c_e = ctk.CTkEntry(m, width=120); c_e.grid(row=2, column=1, padx=8, pady=8)
        def assign():
            s = s_e.get().strip()
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

    # ---------------- Edit prescription popup ----------------
    def _edit_prescription_popup(self, patient_id, pres_id, refresh_cb):
        e = ctk.CTkToplevel(self); e.title("Edit Prescription"); e.geometry("700x520")
        e.lift(); e.attributes("-topmost", True); e.focus_force()

        head = ctk.CTkFrame(e, fg_color="#0b5cab"); head.pack(fill="x")
        _title_bar(head, "Edit Prescription")

        row = db_fetchone("""SELECT medication,quantity,date_added,basket_size,slot_id
                             FROM prescriptions WHERE id=?""", (pres_id,))
        if not row: e.destroy(); return
        med, qty, dt, basket, slot_id = row

        body = ctk.CTkFrame(e); body.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(body, text="Medication", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        med_e = ctk.CTkEntry(body, width=300); med_e.grid(row=0, column=1, padx=6, pady=6); med_e.insert(0, med)
        ctk.CTkLabel(body, text="Quantity", font=ctk.CTkFont(size=17)).grid(row=1, column=0, sticky="e", padx=6, pady=6)
        qty_e = ctk.CTkEntry(body, width=160); qty_e.grid(row=1, column=1, padx=6, pady=6); qty_e.insert(0, str(qty) if qty is not None else "")
        ctk.CTkLabel(body, text="Date (MM/DD/YYYY)", font=ctk.CTkFont(size=17)).grid(row=2, column=0, sticky="e", padx=6, pady=6)
        date_e = ctk.CTkEntry(body, width=180); date_e.grid(row=2, column=1, padx=6, pady=6)
        if dt:
            for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
                try: date_e.insert(0, datetime.strptime(dt,fmt).strftime("%m/%d/%Y")); break
                except: pass
        ctk.CTkLabel(body, text="Basket", font=ctk.CTkFont(size=17)).grid(row=3, column=0, sticky="e", padx=6, pady=6)
        b_cb = ctk.CTkComboBox(body, values=["small","large"], width=140)
        b_cb.set(basket if basket in ("small","large") else "small"); b_cb.grid(row=3, column=1, padx=6, pady=6)

        ctk.CTkLabel(body, text="LED Location (e.g., F-A61 or numeric id)", font=ctk.CTkFont(size=17)).grid(row=4, column=0, sticky="e", padx=6, pady=6)
        loc_e = ctk.CTkEntry(body, width=220); loc_e.grid(row=4, column=1, padx=6, pady=6); loc_e.insert(0, slot_id_to_label(slot_id) if slot_id else "")

        # Automatic assign button (re-balance within its letter section)
        ctk.CTkButton(body, text="Auto Assign", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=lambda: self._reassign_auto_from_edit(patient_id, pres_id, b_cb.get().strip().lower(), e, refresh_cb))\
            .grid(row=5, column=0, columnspan=2, pady=(6,2))

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
        ctk.CTkButton(body, text="Save Changes", fg_color="#2F8B2F", hover_color="#277327", command=save_edit)\
            .grid(row=6, column=0, columnspan=2, pady=12)

    # Re-assign from edit — consolidate toward earliest free in section (or overflow)
    def _reassign_auto_from_edit(self, patient_id, pres_id, basket, parent, refresh_cb):
        fullname = db_fetchone("SELECT name FROM patients WHERE id=?", (patient_id,))[0] or ""
        last = fullname.strip().split()[-1] if fullname.strip() else ""
        letter = last[0].upper() if last else "A"
        res = find_next_available_slot_with_overflow(letter, basket)
        if not res:
            messagebox.showwarning("No Slot","No automatic slot available; please assign manually.")
            return
        slot_ids, shelf = res
        labels = slot_ids_to_labels(slot_ids)
        pos_str = " & ".join([l.split("-")[1] for l in labels])
        conf = ctk.CTkToplevel(parent); conf.title("Confirm New Slot"); conf.geometry("560x200")
        conf.lift(); conf.attributes("-topmost", True); conf.focus_force()
        ctk.CTkLabel(conf, text=f"Proposed Shelf {shelf}: {pos_str}\n(Full: {', '.join(labels)})",
                     font=ctk.CTkFont(size=17)).pack(padx=12, pady=12)

        def do_confirm():
            # free old if any
            old = db_fetchone("SELECT slot_id FROM prescriptions WHERE id=?", (pres_id,))
            if old and old[0]:
                mark_slots_free([old[0]])
            mark_slots_occupied(slot_ids)
            db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_ids[0], pres_id))
            log_action(f"Re-auto-assigned prescription id={pres_id} to {', '.join(labels)}")
            conf.destroy()
            refresh_cb()
            self.refresh_patient_table()
            open_led_popup(parent, slot_ids, "yellow", "Guide to New Slot")

        ctk.CTkButton(conf, text="✅ Confirm", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=do_confirm).pack(side="left", padx=10, pady=10)
        ctk.CTkButton(conf, text="Cancel", command=conf.destroy).pack(side="right", padx=10, pady=10)

    # Location warning string for row in patient popup (wrong section)
    def _location_warning_for_patient(self, pid, slot_id, label_text):
        if not slot_id: return ""
        # Determine patient's letter
        fullname = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0] or ""
        last = fullname.strip().split()[-1] if fullname.strip() else ""
        letter = last[0].upper() if last else "A"
        sec = get_letter_section(letter)
        if not sec:
            # no configured section => no warning
            return ""
        shelf, sr, sc, er, ec = sec
        s, r, c = db_fetchone("SELECT shelf,row,col FROM slots WHERE id=?", (slot_id,))
        # Is it overflow?
        if letter != "O" and label_text and label_text.startswith("O-"):
            # overflow is allowed if section is full; we still warn if section has space
            found = find_next_available_slot_primary(letter, "small")
            if found:  # there is room now, warn to move
                return "(MOVE: better spot available)"
            return ""
        # If slot not within section range, warn
        in_range = False
        for row_char in rows_range(sr, er):
            if r == row_char:
                # compute column bounds for that row
                cstart = sc if row_char == sr else 1
                cend   = ec if row_char == er else 100
                if cstart <= c <= cend and s == shelf:
                    in_range = True
                break
        return "" if in_range else "(CHANGE SHELF LOCATION — WRONG SECTION)"
    # ---------------- Dashboard Tab ----------------
    def open_dashboard_tab(self):
        win = ctk.CTkToplevel(self)
        win.title("📊 Dashboard")
        win.geometry("1200x800")

        head = ctk.CTkFrame(win, fg_color="#0B5CAB")
        head.pack(fill="x")
        ctk.CTkLabel(
            head,
            text="📊 System Dashboard",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=12, pady=8)

        import matplotlib
        matplotlib.use("Agg")  # For backend safety
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        # Data gathering
        patients = db_fetchall("SELECT id,name FROM patients")
        prescriptions = db_fetchall("SELECT basket_size,date_added FROM prescriptions")
        overdue = get_overdue_prescriptions(14)

        # 1. Patient count
        patient_count = len(patients)
        pres_count = len(prescriptions)
        overdue_count = len(overdue)

        # Top stats
        top_stats = ctk.CTkFrame(win)
        top_stats.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(top_stats, text=f"👥 Patients: {patient_count}",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=20)
        ctk.CTkLabel(top_stats, text=f"💊 Prescriptions: {pres_count}",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=20)
        ctk.CTkLabel(top_stats, text=f"⏰ Overdue: {overdue_count}",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=20)

        # 2. Basket size pie chart
        small_count = sum(1 for p in prescriptions if p[0] == "small")
        large_count = sum(1 for p in prescriptions if p[0] == "large")
        fig1, ax1 = plt.subplots(figsize=(3,3))
        ax1.pie([small_count, large_count],
                labels=["Small", "Large"],
                autopct="%1.1f%%",
                colors=["#4CAF50", "#FF9800"])
        ax1.set_title("Basket Sizes")
        canvas1 = FigureCanvasTkAgg(fig1, master=win)
        canvas1.get_tk_widget().pack(side="left", padx=30, pady=20)

        # 3. Prescriptions over time (last 30 days)
        from collections import Counter
        from datetime import timedelta
        now = datetime.now()
        daily_counts = Counter()
        for _, date_added in prescriptions:
            dt = parse_any_date(date_added)
            if dt and (now - dt).days <= 30:
                daily_counts[dt.date()] += 1
        days = [(now - timedelta(days=i)).date() for i in range(29,-1,-1)]
        vals = [daily_counts[d] for d in days]
        fig2, ax2 = plt.subplots(figsize=(5,3))
        ax2.plot(days, vals, marker="o")
        ax2.set_title("Prescriptions Added (30 days)")
        ax2.tick_params(axis="x", rotation=45)
        canvas2 = FigureCanvasTkAgg(fig2, master=win)
        canvas2.get_tk_widget().pack(side="left", padx=30, pady=20)

        # 4. Overdue histogram
        overdue_days = [o["days_overdue"] for o in overdue]
        fig3, ax3 = plt.subplots(figsize=(4,3))
        if overdue_days:
            ax3.hist(overdue_days, bins=10, color="#D83B01")
        ax3.set_title("Overdue Days Distribution")
        canvas3 = FigureCanvasTkAgg(fig3, master=win)
        canvas3.get_tk_widget().pack(side="left", padx=30, pady=20)

    # ---------------- Previous Actions ----------------
    def open_actions_tab(self):
        win = ctk.CTkToplevel(self)
        win.title("Previous Actions")
        win.geometry("1020x660")

        head = ctk.CTkFrame(win, fg_color="#6b6b6b")
        head.pack(fill="x")
        ctk.CTkLabel(
            head,
            text="Previous Actions (Today)",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=12, pady=8)

        tablef = ctk.CTkFrame(win)
        tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Time","Actor","Action")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=160 if c!="Action" else 760, anchor="w")
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

        ctk.CTkButton(
            win,
            text="🔄 Refresh",
            command=lambda: self._refresh_actions(tv)
        ).pack(pady=8)

    def _refresh_actions(self, tv):
        for r in tv.get_children(): tv.delete(r)
        acts = get_todays_actions(300)
        for i,(ts,actor,action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tag = "odd" if i%2 else "even"
            tv.insert("", "end", values=(t,actor,action), tags=(tag,))

# =====================================================
# Boot
# =====================================================
if __name__ == "__main__":
    print("DB:", DB_PATH)
    init_db(reset=False)
    init_actions_table()
    populate_slots()
    app = App()
    app.mainloop()
