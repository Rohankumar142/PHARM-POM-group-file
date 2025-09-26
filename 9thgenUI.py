# =====================================================
# Imports
# =====================================================
import os, sqlite3, threading, time
from datetime import datetime
import customtkinter as ctk
from tkinter import ttk, messagebox

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
# LED simulation
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
    _blink_groups[key] = {"stop": stop, "pause": pause, "thread": th,
                          "slots": list(slots), "color": color}
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
    ctk.CTkLabel(win, text=f"Blinking: {', '.join(slot_ids_to_labels(slots))}",
                 font=ctk.CTkFont(size=14)).pack(padx=14, pady=(14,6))
    btn = ctk.CTkButton(win, text="⏸ Pause Lights",
                        command=lambda: _toggle_pause(btn, key))
    btn.pack(side="left", padx=12, pady=12)
    ctk.CTkButton(win, text="✅ Done",
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
            rows_count INT,
            cols_count INT
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

def populate_all_slots_from_shelves():
    shelves = db_fetchall("SELECT name, rows_count, cols_count FROM shelves")
    for sname, rows_count, cols_count in shelves:
        for i in range(rows_count):
            row = chr(65+i)
            for col in range(1, cols_count+1):
                db_exec("INSERT INTO slots(shelf,row,col,occupied) VALUES(?,?,?,0)", (sname,row,col))

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
# =====================================================
# Slot assignment wrappers
# =====================================================
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
# Treeview styling
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
                    font=("Segoe UI", 11))
    style.configure("Treeview.Heading",
                    background="#0078d4",
                    foreground="white",
                    font=("Segoe UI Semibold", 12))
    style.map("Treeview", background=[("selected","#cce6ff")])
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("Pharmacy LED System")
        self.geometry("1400x880")
        self.minsize(1200, 720)

        style_treeview()

        # ---------- Top bar ----------
        topbar = ctk.CTkFrame(self, corner_radius=0)
        topbar.pack(side="top", fill="x")
        ctk.CTkLabel(topbar, text="💊 Pharmacy LED System",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=12, pady=8)

        # ---------- Right sidebar ----------
        sidebar = ctk.CTkFrame(self, width=240, corner_radius=0)
        sidebar.pack(side="right", fill="y")
        ctk.CTkLabel(sidebar, text="Tools", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(12, 8))
        ctk.CTkButton(sidebar, text="📚 Shelf Assignment", command=self.open_shelf_assignment).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="⏰ Overdue Meds", command=self.open_overdue_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📝 Previous Actions", command=self.open_actions_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📊 Dashboard", command=self.open_dashboard_tab).pack(padx=10, pady=6, fill="x")

        # ---------- Patients header + controls ----------
        header = ctk.CTkFrame(self, fg_color="#0b5cab")
        header.pack(fill="x", padx=0, pady=(0,6))
        ctk.CTkLabel(header, text="Patients", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=12, pady=8)

        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", padx=10, pady=(0,8))
        ctk.CTkLabel(controls, text="Search:").pack(side="left", padx=(8,6))
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(controls, textvariable=self.search_var, width=240)
        search_entry.pack(side="left", padx=(0,10), ipady=2)
        search_entry.bind("<Return>", lambda e: self.refresh_patient_table())
        ctk.CTkButton(controls, text="🔄 Refresh", command=self.refresh_patient_table).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="➕ Add Patient", fg_color="#2F8B2F",
                      command=self.add_patient_popup).pack(side="right", padx=6)
        ctk.CTkButton(controls, text="🗑️ Delete Patient", fg_color="#D83B01",
                      command=self.delete_selected_patient).pack(side="right", padx=6)

        # ---------- Patients Treeview ----------
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.tree = ttk.Treeview(table_frame, columns=("Name","Address","Date Added","Locations"),
                                 show="headings", selectmode="browse")
        for c in ("Name","Address","Date Added","Locations"):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=220 if c!="Locations" else 420, anchor="w")
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
            # all distinct locations
            slot_rows = db_fetchall("SELECT DISTINCT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
            locs = [slot_id_to_label(s[0]) for s in slot_rows if s and s[0]]
            tag = "odd" if i%2 else "even"
            self.tree.insert("", "end", iid=str(pid),
                             values=(name, addr or "", disp, ", ".join([l for l in locs if l])),
                             tags=(tag,))
    def add_patient_popup(self):
        p = ctk.CTkToplevel(self); p.title("Add Patient"); p.geometry("520x220")
        p.lift(); p.focus_force()
        head = ctk.CTkFrame(p, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Add Patient", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=12, pady=8)
        body = ctk.CTkFrame(p); body.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(body, text="Name").grid(row=0, column=0, sticky="e", padx=8, pady=8)
        name_e = ctk.CTkEntry(body, width=300); name_e.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(body, text="Address").grid(row=1, column=0, sticky="e", padx=8, pady=8)
        addr_e = ctk.CTkEntry(body, width=300); addr_e.grid(row=1, column=1, padx=8, pady=8)

        def save():
            nm = name_e.get().strip()
            if not nm:
                messagebox.showerror("Error","Name required"); return
            db_exec("INSERT INTO patients(name,address,created_at) VALUES(?,?,?)",
                    (nm, addr_e.get().strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            log_action(f"Added patient: {nm}")
            p.destroy(); self.refresh_patient_table()

        ctk.CTkButton(body, text="Save", fg_color="#2F8B2F", command=save).grid(row=2, column=0, columnspan=2, pady=10)

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

    # ---------------- Patient popup ----------------
    def open_patient_popup(self, pid):
        r = db_fetchone("SELECT name,address FROM patients WHERE id=?", (pid,))
        pname, paddr = (r[0], r[1] or "") if r else ("","")
        win = ctk.CTkToplevel(self); win.title(f"Patient — {pname}"); win.geometry("1160x780")
        win.lift(); win.focus_force()

        head = ctk.CTkFrame(win, fg_color="#107c10"); head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Patient — {pname}", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=12, pady=8)

        # Patient info
        info = ctk.CTkFrame(win); info.pack(fill="x", padx=12, pady=(10,6))
        ctk.CTkLabel(info, text="Name").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        name_e = ctk.CTkEntry(info, width=320); name_e.grid(row=0, column=1, padx=6, pady=6); name_e.insert(0, pname)
        ctk.CTkLabel(info, text="Address").grid(row=0, column=2, sticky="e", padx=6, pady=6)
        addr_e = ctk.CTkEntry(info, width=320); addr_e.grid(row=0, column=3, padx=6, pady=6); addr_e.insert(0, paddr)
        ctk.CTkButton(info, text="💾 Save Patient", command=lambda: self._save_patient(pid, name_e, addr_e, win)).grid(row=0, column=4, padx=8)
        ctk.CTkButton(info, text="🔵 Light Up Locations", command=lambda: self._light_up_patient(pid, win)).grid(row=0, column=5, padx=6)
        ctk.CTkButton(info, text="🗑️ Clear All Prescriptions", fg_color="#D83B01",
                      command=lambda: self._clear_all_prescriptions(pid, win)).grid(row=0, column=6, padx=6)

        # Rx Table
        mid = ctk.CTkFrame(win); mid.pack(fill="both", expand=True, padx=12, pady=(6,8))
        cols = ("Medication","Quantity","Date Added","Basket","Location")
        rx = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            rx.heading(c, text=c)
            rx.column(c, width=210 if c!="Location" else 260, anchor="w")
        rx.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=rx.yview)
        sb.pack(side="right", fill="y")
        rx.configure(yscroll=sb.set)

        def populate_rx():
            for r in rx.get_children(): rx.delete(r)
            rows2 = db_fetchall("""SELECT id,medication,quantity,date_added,basket_size,slot_id
                                   FROM prescriptions WHERE patient_id=? ORDER BY id""", (pid,))
            for i,(prid, med, qty, dt, basket, sid) in enumerate(rows2):
                date_disp = ""
                if dt:
                    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
                        try: date_disp = datetime.strptime(dt,fmt).strftime("%m/%d/%Y"); break
                        except: pass
                loc = slot_id_to_label(sid) if sid else ""
                rx.insert("", "end", iid=str(prid),
                          values=(med, qty if qty is not None else "", date_disp, basket or "", loc))
        populate_rx()

        rx.bind("<Double-1>", lambda e: self._edit_prescription_popup(pid, int(rx.selection()[0]), populate_rx))
    # ---------------- Shelf Assignment ----------------
    def open_shelf_assignment(self):
        win = ctk.CTkToplevel(self); win.title("Shelf Assignment"); win.geometry("960x800")
        win.lift(); win.focus_force()
        ctk.CTkLabel(win, text="Shelf Assignment", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)

        cont = ctk.CTkFrame(win); cont.pack(fill="both", expand=True, padx=10, pady=10)
        canvas = ctk.CTkCanvas(cont, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
        yscroll = ttk.Scrollbar(cont, orient="vertical", command=canvas.yview); yscroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=yscroll.set)
        inner = ctk.CTkFrame(canvas); canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        self.letter_widgets = {}
        letters = [chr(i) for i in range(65,91)] + ["Overflow"]
        for i,L in enumerate(letters):
            rowf = ctk.CTkFrame(inner); rowf.grid(row=i, column=0, sticky="w", padx=8, pady=3)
            ctk.CTkLabel(rowf, text=L, width=60, anchor="w").grid(row=0, column=0, padx=6)
            shelf_cb = ctk.CTkComboBox(rowf, values=["","F","L","R"], width=80); shelf_cb.set("")
            lower_e  = ctk.CTkEntry(rowf, width=120); upper_e = ctk.CTkEntry(rowf, width=120)
            shelf_cb.grid(row=0, column=1, padx=10); lower_e.grid(row=0, column=2, padx=10); upper_e.grid(row=0, column=3, padx=10)
            r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (L,))
            if r: shelf_cb.set(r[0] or ""); 
            if r and r[1]: lower_e.insert(0, r[1])
            if r and r[2]: upper_e.insert(0, r[2])
            self.letter_widgets[L] = (shelf_cb, lower_e, upper_e)

        def save_all():
            for L,(s,lo,up) in self.letter_widgets.items():
                S, LO, UP = s.get().strip().upper(), lo.get().strip().upper(), up.get().strip().upper()
                db_exec("INSERT OR REPLACE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                        (L, S, LO, UP))
            log_action("Updated shelf assignments")
            win.destroy()
        ctk.CTkButton(win, text="💾 Save All & Close", command=save_all).pack(pady=10)

    # ---------------- Dashboard ----------------
    def open_dashboard_tab(self):
        win = ctk.CTkToplevel(self); win.title("Dashboard"); win.geometry("1000x720")
        win.lift(); win.focus_force()
        ctk.CTkLabel(win, text="📊 Dashboard", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=12)

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6,4))
        counts = db_fetchall("SELECT basket_size, COUNT(*) FROM prescriptions GROUP BY basket_size")
        sizes = [c[1] for c in counts]; labels = [c[0] for c in counts]
        if sizes:
            ax.pie(sizes, labels=labels, autopct='%1.1f%%')
        else:
            ax.text(0.5,0.5,"No Data", ha="center")
        canvas = FigureCanvasTkAgg(fig, master=win); canvas.draw(); canvas.get_tk_widget().pack()

# =====================================================
# Boot
# =====================================================
if __name__ == "__main__":
    print("Database path:", DB_PATH)
    init_db(reset=True)
    app = App()
    app.mainloop()
