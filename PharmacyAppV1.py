# DATABASE+UI.py — Full modernized Pharmacy LED system (CustomTkinter)
# -------------------------------------------------------------------
# Major features:
# - Patients main table (search/add/delete/refresh, shows ALL locations)
# - Double-click patient -> popup to edit name/address + manage prescriptions
# - Add Rx: family-bin check (purple), auto-assign in letter section (yellow),
#           confirm/deny with manual assign, LED control popup (pause/resume/done)
# - Edit Rx popup (med/qty/date/basket/location with warnings & occupancy checks)
# - Delete Rx (multi-select + Delete/Backspace)
# - Clear ALL Rx for a patient (frees slots)
# - Shelf Assignment (A..Z + Overflow) Save All & Close (validated bounds)
# - Overdue tab (14+ days) with per-patient details + Light Up
# - Previous Actions tab (last 300 today)
# - Modal popups with focus and topmost
# - Fonts: labels=17, table=16, headers=20; zebra rows; color-coded headers
# - SQLite DB persisted next to this script. Slots populated if empty.

import os
import sqlite3
import threading
import time
from datetime import datetime

import customtkinter as ctk
from tkinter import ttk, messagebox

# =====================================================
# Paths / DB helpers
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pharmacy.db")

def db_connect():
    # Timeout avoids "database is locked" under quick repeat actions.
    conn = sqlite3.connect(DB_PATH, timeout=5)
    return conn

def db_exec(sql, params=(), commit=True):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if commit:
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

# =====================================================
# Fonts & styling
# =====================================================
FONT_LABEL_SIZE = 17
FONT_TABLE_SIZE = 16
FONT_HEADER_SIZE = 20

def style_treeview():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Treeview",
        background="white",
        fieldbackground="white",
        rowheight=30,
        font=("Segoe UI", FONT_TABLE_SIZE),
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background="#0078d4",
        foreground="white",
        font=("Segoe UI Semibold", FONT_HEADER_SIZE),
    )
    style.map("Treeview", background=[("selected", "#cce6ff")])

# =====================================================
# Actions log
# =====================================================
def init_actions_table():
    db_exec(
        """CREATE TABLE IF NOT EXISTS actions_log(
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           ts TEXT,
           actor TEXT,
           action TEXT
        )"""
    )

def log_action(action, actor="system"):
    db_exec(
        "INSERT INTO actions_log(ts,actor,action) VALUES(?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor, action),
    )

def get_todays_actions(limit=300):
    today = datetime.now().strftime("%Y-%m-%d")
    return db_fetchall(
        """SELECT ts,actor,action
           FROM actions_log
           WHERE ts LIKE ?
           ORDER BY id DESC
           LIMIT ?""",
        (today + "%", limit),
    )

# =====================================================
# LED simulation (prints with labels, pause/resume/done)
# =====================================================
_blink_groups = {}  # key -> {stop,pause,thread,slots,color}

def slot_id_to_label(slot_id):
    r = db_fetchone("SELECT shelf,row,col FROM slots WHERE id=?", (slot_id,))
    if not r:
        return ""
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
            time.sleep(0.2)
            continue
        print(f"[LED] ON {labels} ({color})")
        time.sleep(0.5)
        print(f"[LED] OFF {labels}")
        time.sleep(0.5)

def start_blink(slots, color="yellow"):
    """slots: list of slot_ids. Returns a group key for control."""
    if not slots:
        return None
    key = int(slots[0])
    stop_blink(key)
    stop = threading.Event()
    pause = threading.Event()
    _blink_groups[key] = {"stop": stop, "pause": pause, "thread": None, "slots": list(slots), "color": color}
    th = threading.Thread(target=_blink_worker, args=(key,), daemon=True)
    _blink_groups[key]["thread"] = th
    th.start()
    return key

def pause_toggle(group_key):
    ctrl = _blink_groups.get(group_key)
    if not ctrl:
        return
    if ctrl["pause"].is_set():
        ctrl["pause"].clear()
        print("[LED] resume")
    else:
        ctrl["pause"].set()
        print("[LED] pause")

def stop_blink(slots_or_key):
    if slots_or_key is None:
        return
    if isinstance(slots_or_key, int):
        ctrl = _blink_groups.pop(slots_or_key, None)
        if ctrl:
            ctrl["stop"].set()
        return
    # list of slots
    key = int(slots_or_key[0])
    ctrl = _blink_groups.pop(key, None)
    if ctrl:
        ctrl["stop"].set()

def make_modal(win, parent=None):
    # Make any Toplevel modal & topmost relative to parent
    if parent is not None:
        try:
            win.transient(parent)
        except Exception:
            pass
    try:
        win.lift()
        win.focus_force()
        win.grab_set()
    except Exception:
        pass

def open_led_popup(parent, slots, color="yellow", title="LED Control"):
    if not slots:
        messagebox.showinfo("LED", "No slots to blink.", parent=parent)
        return
    key = start_blink(slots, color)
    win = ctk.CTkToplevel(parent)
    win.title(title)
    win.geometry("540x180")
    make_modal(win, parent)

    header = ctk.CTkFrame(win, fg_color=("#0b5cab" if color != "red" else "#c50f1f"))
    header.pack(fill="x")
    ctk.CTkLabel(
        header,
        text=title,
        font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold"),
    ).pack(anchor="w", padx=12, pady=8)

    body = ctk.CTkFrame(win)
    body.pack(fill="both", expand=True, padx=12, pady=12)

    ctk.CTkLabel(
        body,
        text=f"Blinking: {', '.join(slot_ids_to_labels(slots))}",
        font=ctk.CTkFont(size=FONT_LABEL_SIZE),
    ).pack(pady=(6, 12))

    # Pause/resume toggle
    def toggle():
        pause_toggle(key)
        if btn_pause.cget("text").startswith("⏸"):
            btn_pause.configure(text="▶ Resume Lights")
        else:
            btn_pause.configure(text="⏸ Pause Lights")

    btn_row = ctk.CTkFrame(body)
    btn_row.pack(fill="x", pady=6)
    btn_pause = ctk.CTkButton(
        btn_row,
        text="⏸ Pause Lights",
        corner_radius=8,
        fg_color="#0B5CAB",
        hover_color="#084b8a",
        command=toggle,
    )
    btn_pause.pack(side="left", padx=6)

    ctk.CTkButton(
        btn_row,
        text="✅ Done",
        corner_radius=8,
        fg_color="#D83B01",
        hover_color="#B32F00",
        command=lambda: (stop_blink(key), win.destroy()),
    ).pack(side="right", padx=6)

# =====================================================
# DB schema & seed
# =====================================================
def init_db(reset=False):
    with db_connect() as conn:
        cur = conn.cursor()
        if reset:
            for t in ["patients", "prescriptions", "letter_sections", "slots", "actions_log"]:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS patients(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL,
               address TEXT,
               created_at TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS prescriptions(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               patient_id INT NOT NULL,
               medication TEXT NOT NULL,
               quantity INT,
               date_added TEXT,
               basket_size TEXT CHECK (basket_size IN ('small','large')),
               slot_id INT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS letter_sections(
               letter TEXT PRIMARY KEY,
               shelf TEXT,
               lower_bound TEXT,
               upper_bound TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS slots(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               shelf TEXT,
               row TEXT,
               col INT,
               occupied INT DEFAULT 0
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS actions_log(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts TEXT,
               actor TEXT,
               action TEXT
            )"""
        )
        conn.commit()

    # Seed letter sections with blank + a few examples if empty
    if not db_fetchone("SELECT 1 FROM letter_sections", ()):
        for L in [chr(i) for i in range(65, 91)] + ["Overflow"]:
            db_exec(
                "INSERT OR IGNORE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                (L, "", "", ""),
            )
        # sample defaults (editable in Shelf Assignment)
        db_exec("UPDATE letter_sections SET shelf='F',lower_bound='A1',upper_bound='D20' WHERE letter='A'")
        db_exec("UPDATE letter_sections SET shelf='L',lower_bound='E1',upper_bound='H30' WHERE letter='B'")
        db_exec("UPDATE letter_sections SET shelf='R',lower_bound='J1',upper_bound='M15' WHERE letter='C'")

def populate_slots():
    cnt = db_fetchone("SELECT COUNT(*) FROM slots", ())[0]
    if cnt and cnt > 0:
        return
    # 3 shelves × rows A-Z × cols 1..100
    for shelf in ["F", "L", "R"]:
        for i in range(26):
            row = chr(65 + i)
            for col in range(1, 101):
                db_exec("INSERT INTO slots(shelf,row,col,occupied) VALUES(?,?,?,0)", (shelf, row, col))
    print("Slots populated.")

# =====================================================
# Section / Slot helpers
# =====================================================
def parse_bound(b):
    """Returns (row_char, col_int) or None if invalid."""
    if not b:
        return None
    b = b.strip().upper()
    if len(b) < 2:
        return None
    row = b[0]
    n = b[1:]
    if not row.isalpha() or not n.isdigit():
        return None
    col = int(n)
    if not (1 <= col <= 100) or not ("A" <= row <= "Z"):
        return None
    return (row, col)

def rows_range(a, b):
    return [chr(i) for i in range(ord(a), ord(b) + 1)]

def get_letter_section(letter):
    r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (letter,))
    if not r:
        return None
    shelf, lo, up = r
    plo, pup = parse_bound(lo), parse_bound(up)
    if not shelf or not plo or not pup:
        return None
    return shelf, plo[0], plo[1], pup[0], pup[1]

def get_slot_by_position(shelf, row, col):
    return db_fetchone("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))

def find_slot_in_section(shelf, sr, sc, er, ec, basket):
    # scan rows then cols; if large basket, need adjacent col free
    with db_connect() as conn:
        cur = conn.cursor()
        for r in rows_range(sr, er):
            cstart = sc if r == sr else 1
            cend = ec if r == er else 100
            for c in range(cstart, cend + 1):
                cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, r, c))
                k = cur.fetchone()
                if not k:
                    continue
                sid, occ = k
                if occ == 0:
                    if basket == "large":
                        cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, r, c + 1))
                        k2 = cur.fetchone()
                        if k2 and k2[1] == 0:
                            return [sid, k2[0]], shelf
                    else:
                        return [sid], shelf
    return None

def find_next_available_slot_primary(letter, basket):
    sec = get_letter_section(letter)
    if not sec:
        return None
    shelf, sr, sc, er, ec = sec
    return find_slot_in_section(shelf, sr, sc, er, ec, basket)

def find_next_available_slot_with_overflow(letter, basket):
    res = find_next_available_slot_primary(letter, basket)
    if res:
        return res
    of = get_letter_section("Overflow")
    if of:
        shelf, sr, sc, er, ec = of
        res2 = find_slot_in_section(shelf, sr, sc, er, ec, basket)
        if res2:
            return res2
    return None

def mark_slots_occupied(slot_ids):
    if not slot_ids:
        return
    with db_connect() as conn:
        cur = conn.cursor()
        for s in slot_ids:
            cur.execute("UPDATE slots SET occupied=1 WHERE id=?", (s,))
        conn.commit()

def mark_slots_free(slot_ids):
    if not slot_ids:
        return
    with db_connect() as conn:
        cur = conn.cursor()
        for s in slot_ids:
            cur.execute("UPDATE slots SET occupied=0 WHERE id=?", (s,))
        conn.commit()

def parse_location_label_to_slot_id(txt):
    """Accepts 'F-A61' OR 'F-A-61' OR numeric slot id. Returns slot_id or None."""
    if not txt:
        return None
    s = txt.strip().upper().replace(" ", "").replace("--", "-")
    if s.isdigit():
        r = db_fetchone("SELECT id FROM slots WHERE id=?", (int(s),))
        return int(s) if r else None
    parts = s.split("-")
    if len(parts) == 2:
        shelf, rowcol = parts
        if len(shelf) == 1 and len(rowcol) >= 2:
            row = rowcol[0]
            if not row.isalpha():
                return None
            try:
                col = int(rowcol[1:])
            except Exception:
                return None
            r = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))
            return r[0] if r else None
    if len(parts) == 3:
        shelf, row, coltxt = parts
        if len(shelf) == 1 and len(row) == 1:
            try:
                col = int(coltxt)
            except Exception:
                return None
            r = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))
            return r[0] if r else None
    return None

# =====================================================
# Overdue helpers
# =====================================================
def parse_any_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def get_overdue_prescriptions(days=14):
    rows = db_fetchall(
        """SELECT pr.id, pr.patient_id, pr.medication, pr.quantity, pr.date_added, pr.slot_id,
                  p.name, p.address
           FROM prescriptions pr
           JOIN patients p ON p.id = pr.patient_id"""
    )
    now = datetime.now()
    out = []
    for pr_id, pid, med, qty, date_added, slot_id, name, address in rows:
        dt = parse_any_date(date_added)
        if not dt:
            continue
        diff = (now - dt).days
        if diff > days:
            out.append(
                {
                    "prescription_id": pr_id,
                    "patient_id": pid,
                    "name": name,
                    "address": address,
                    "medication": med,
                    "quantity": qty,
                    "days_overdue": diff - days,
                    "slot_id": slot_id,
                }
            )
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
# App UI (CustomTkinter)
# =====================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")   # "Light" or "Dark"
        ctk.set_default_color_theme("blue")

        self.title("Pharmacy LED System")
        self.geometry("1380x880")
        self.minsize(1240, 760)

        style_treeview()

        # ---------- Top bar ----------
        topbar = ctk.CTkFrame(self, corner_radius=0)
        topbar.pack(side="top", fill="x")
        ctk.CTkLabel(
            topbar,
            text="💊 Pharmacy LED System",
            font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold"),
        ).pack(side="left", padx=12, pady=8)

        self.theme_mode = ctk.StringVar(value="Dark")

        def toggle_theme():
            mode = "Light" if self.theme_mode.get() == "Dark" else "Dark"
            self.theme_mode.set(mode)
            ctk.set_appearance_mode(mode)

        ctk.CTkButton(topbar, text="🌓 Theme", width=100, command=toggle_theme).pack(side="right", padx=10, pady=8)

        # ---------- Right sidebar ----------
        sidebar = ctk.CTkFrame(self, width=240, corner_radius=0)
        sidebar.pack(side="right", fill="y")
        ctk.CTkLabel(sidebar, text="Tools", font=ctk.CTkFont(size=FONT_LABEL_SIZE, weight="bold")).pack(pady=(12, 8))
        ctk.CTkButton(sidebar, text="📚 Shelf Assignment", command=self.open_shelf_assignment).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="⏰ Overdue Meds", command=self.open_overdue_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📝 Previous Actions", command=self.open_actions_tab).pack(padx=10, pady=6, fill="x")

        # ---------- Patients header + controls ----------
        header = ctk.CTkFrame(self, fg_color="#0b5cab")
        header.pack(fill="x", padx=0, pady=(0, 6))
        ctk.CTkLabel(header, text="Patients", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(anchor="w", padx=12, pady=8)

        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(controls, text="Search:", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).pack(side="left", padx=(8, 6))
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(controls, textvariable=self.search_var, width=260)
        search_entry.pack(side="left", padx=(0, 10), ipady=2)
        search_entry.bind("<Return>", lambda e: self.refresh_patient_table())
        ctk.CTkButton(controls, text="🔄 Refresh", command=self.refresh_patient_table).pack(side="left", padx=6)
        ctk.CTkButton(
            controls, text="➕ Add Patient", fg_color="#2F8B2F", hover_color="#277327", command=self.add_patient_popup
        ).pack(side="right", padx=6)
        ctk.CTkButton(
            controls, text="🗑️ Delete Patient", fg_color="#D83B01", hover_color="#B32F00", command=self.delete_selected_patient
        ).pack(side="right", padx=6)

        # ---------- Patients Treeview ----------
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tree = ttk.Treeview(
            table_frame,
            columns=("Name", "Address", "Date Added", "Locations"),
            show="headings",
            selectmode="browse",
        )
        for c in ("Name", "Address", "Date Added", "Locations"):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=220 if c != "Locations" else 420, anchor="w")
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
        for r in self.tree.get_children():
            self.tree.delete(r)
        q = self.search_var.get().strip()
        sql = "SELECT id,name,address,created_at FROM patients"
        params = ()
        if q:
            sql += " WHERE name LIKE ? OR address LIKE ?"
            params = (f"%{q}%", f"%{q}%")
        sql += " ORDER BY name"
        rows = db_fetchall(sql, params)
        for i, (pid, name, addr, created) in enumerate(rows):
            disp = ""
            if created:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        disp = datetime.strptime(created, fmt).strftime("%m/%d/%Y")
                        break
                    except Exception:
                        pass
            # collect all distinct locations
            slot_rows = db_fetchall(
                "SELECT DISTINCT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,)
            )
            locs = [slot_id_to_label(s[0]) for s in slot_rows if s and s[0]]
            tag = "odd" if i % 2 else "even"
            self.tree.insert(
                "",
                "end",
                iid=str(pid),
                values=(name, addr or "", disp, ", ".join([l for l in locs if l])),
                tags=(tag,),
            )

    def add_patient_popup(self):
        p = ctk.CTkToplevel(self)
        p.title("Add Patient")
        p.geometry("560x240")
        make_modal(p, self)

        head = ctk.CTkFrame(p, fg_color="#0b5cab")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Add Patient", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        body = ctk.CTkFrame(p)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        ctk.CTkLabel(body, text="Name", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=0, sticky="e", padx=8, pady=8)
        name_e = ctk.CTkEntry(body, width=320)
        name_e.grid(row=0, column=1, padx=8, pady=8)

        ctk.CTkLabel(body, text="Address", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=1, column=0, sticky="e", padx=8, pady=8)
        addr_e = ctk.CTkEntry(body, width=320)
        addr_e.grid(row=1, column=1, padx=8, pady=8)

        def save():
            nm = name_e.get().strip()
            if not nm:
                messagebox.showerror("Error", "Name required", parent=p)
                return
            db_exec(
                "INSERT INTO patients(name,address,created_at) VALUES(?,?,?)",
                (nm, addr_e.get().strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            log_action(f"Added patient: {nm}")
            p.destroy()
            self.refresh_patient_table()

        ctk.CTkButton(body, text="Save", fg_color="#2F8B2F", hover_color="#277327", command=save).grid(
            row=2, column=0, columnspan=2, pady=10
        )

    def delete_selected_patient(self):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        row = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))
        name = row[0] if row else "patient"
        if not messagebox.askyesno("Confirm", f"Delete {name} and ALL prescriptions?", parent=self):
            return
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=?", (pid,))
        sids = [r[0] for r in rows if r and r[0]]
        if sids:
            mark_slots_free(sids)
        db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
        db_exec("DELETE FROM patients WHERE id=?", (pid,))
        log_action(f"Deleted patient: {name}")
        self.refresh_patient_table()

    def on_patient_double(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        self.open_patient_popup(pid)

    # ---------------- Patient popup (edit + Rx) ----------------
    def open_patient_popup(self, pid):
        r = db_fetchone("SELECT name,address FROM patients WHERE id=?", (pid,))
        pname, paddr = (r[0], r[1] or "") if r else ("", "")

        win = ctk.CTkToplevel(self)
        win.title(f"Patient — {pname}")
        win.geometry("1180x800")
        make_modal(win, self)

        head = ctk.CTkFrame(win, fg_color="#107c10")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Patient — {pname}", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        info = ctk.CTkFrame(win)
        info.pack(fill="x", padx=12, pady=(10, 6))

        ctk.CTkLabel(info, text="Name", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        name_e = ctk.CTkEntry(info, width=320)
        name_e.grid(row=0, column=1, padx=6, pady=6)
        name_e.insert(0, pname)

        ctk.CTkLabel(info, text="Address", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=2, sticky="e", padx=6, pady=6)
        addr_e = ctk.CTkEntry(info, width=320)
        addr_e.grid(row=0, column=3, padx=6, pady=6)
        addr_e.insert(0, paddr)

        ctk.CTkButton(
            info,
            text="💾 Save Patient",
            fg_color="#0B5CAB",
            hover_color="#084b8a",
            command=lambda: self._save_patient(pid, name_e, addr_e, win),
        ).grid(row=0, column=4, padx=8)

        ctk.CTkButton(
            info,
            text="🔵 Light Up Locations",
            command=lambda: self._light_up_patient(pid, win),
        ).grid(row=0, column=5, padx=6)

        ctk.CTkButton(
            info,
            text="🗑️ Clear All Prescriptions",
            fg_color="#D83B01",
            hover_color="#B32F00",
            command=lambda: self._clear_all_prescriptions(pid, win),
        ).grid(row=0, column=6, padx=6)

        # Rx Table
        mid = ctk.CTkFrame(win)
        mid.pack(fill="both", expand=True, padx=12, pady=(6, 8))
        cols = ("Medication", "Quantity", "Date Added", "Basket", "Location")
        rx = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            rx.heading(c, text=c)
            rx.column(c, width=220 if c != "Location" else 260, anchor="w")
        rx.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=rx.yview)
        sb.pack(side="right", fill="y")
        rx.configure(yscroll=sb.set)
        rx.tag_configure("odd", background="#f5f9ff")
        rx.tag_configure("even", background="#ffffff")

        def populate_rx():
            for r in rx.get_children():
                rx.delete(r)
            rows2 = db_fetchall(
                """SELECT id,medication,quantity,date_added,basket_size,slot_id
                   FROM prescriptions
                   WHERE patient_id=? ORDER BY id""",
                (pid,),
            )
            for i, (prid, med, qty, dt, basket, sid) in enumerate(rows2):
                date_disp = ""
                if dt:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            date_disp = datetime.strptime(dt, fmt).strftime("%m/%d/%Y")
                            break
                        except Exception:
                            pass
                loc = slot_id_to_label(sid) if sid else ""
                tag = "odd" if i % 2 else "even"
                rx.insert(
                    "",
                    "end",
                    iid=str(prid),
                    values=(med, qty if qty is not None else "", date_disp, basket or "", loc),
                    tags=(tag,),
                )

        populate_rx()

        def on_rx_double(_e):
            sel = rx.selection()
            if not sel:
                return
            prid = int(sel[0])
            self._edit_prescription_popup(pid, prid, populate_rx)
        rx.bind("<Double-1>", on_rx_double)

        # Delete prescriptions (button + Delete key)
        def delete_selected_prescriptions():
            sel = rx.selection()
            if not sel:
                messagebox.showinfo("Delete", "No prescription selected.", parent=win)
                return
            if not messagebox.askyesno("Confirm", f"Delete {len(sel)} prescription(s)?", parent=win):
                return
            for item in sel:
                prid = int(item)
                row = db_fetchone("SELECT medication, slot_id FROM prescriptions WHERE id=?", (prid,))
                if not row:
                    continue
                med, sid = row
                if sid:
                    mark_slots_free([sid])
                db_exec("DELETE FROM prescriptions WHERE id=?", (prid,))
                log_action(f"Deleted prescription '{med}' for patient_id={pid}")
            populate_rx()
            self.refresh_patient_table()

        def on_key(e):
            if e.keysym in ("Delete", "BackSpace"):
                delete_selected_prescriptions()
        rx.bind("<Key>", on_key)

        # Add Rx row
        addf = ctk.CTkFrame(win)
        addf.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkLabel(addf, text="Medication", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=0, sticky="e", padx=6)
        med_e = ctk.CTkEntry(addf, width=240)
        med_e.grid(row=0, column=1, padx=6)
        ctk.CTkLabel(addf, text="Quantity", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=2, sticky="e", padx=6)
        qty_e = ctk.CTkEntry(addf, width=120)
        qty_e.grid(row=0, column=3, padx=6)
        ctk.CTkLabel(addf, text="Basket", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=4, sticky="e", padx=6)
        basket_cb = ctk.CTkComboBox(addf, values=["small", "large"], width=130)
        basket_cb.set("small")
        basket_cb.grid(row=0, column=5, padx=6)
        ctk.CTkButton(
            addf,
            text="➕ Add Prescription",
            fg_color="#2F8B2F",
            hover_color="#277327",
            command=lambda: self._add_prescription(pid, addr_e, med_e, qty_e, basket_cb, win, populate_rx),
        ).grid(row=0, column=6, padx=10)
        ctk.CTkButton(
            addf, text="🗑️ Delete Selected", fg_color="#D83B01", hover_color="#B32F00", command=delete_selected_prescriptions
        ).grid(row=0, column=7, padx=6)

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
            messagebox.showinfo("LED", "No assigned locations for this patient.", parent=parent)
            return
        open_led_popup(parent, slots, "blue", "Patient Locations")

    def _clear_all_prescriptions(self, pid, popup):
        name = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0]
        if not messagebox.askyesno("Confirm", f"Clear ALL prescriptions for {name}?", parent=popup):
            return
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
        sids = [r[0] for r in rows if r and r[0]]
        if sids:
            mark_slots_free(sids)
        db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
        log_action(f"Cleared all prescriptions for: {name}")
        popup.destroy()
        self.refresh_patient_table()

    def _add_prescription(self, pid, addr_e, med_e, qty_e, basket_cb, parent, refresh_cb):
        med = med_e.get().strip()
        qty = qty_e.get().strip()
        basket = basket_cb.get().strip().lower()
        if not med or not qty or basket not in ("small", "large"):
            messagebox.showerror("Error", "Enter medication, quantity, and basket size.", parent=parent)
            return
        qv = int(qty) if qty.isdigit() else qty
        db_exec(
            """INSERT INTO prescriptions(patient_id,medication,quantity,date_added,basket_size)
               VALUES(?,?,?,?,?)""",
            (pid, med, qv, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), basket),
        )
        pres_id = db_fetchone("SELECT id FROM prescriptions WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,))[0]
        log_action(f"Added prescription '{med}' for patient_id={pid} (basket={basket})")

        # family match?
        fam = None
        addr = addr_e.get().strip()
        if addr:
            fam = db_fetchone(
                """SELECT p.id, pr.slot_id
                   FROM patients p JOIN prescriptions pr ON p.id=pr.patient_id
                   WHERE p.address=? AND pr.slot_id IS NOT NULL
                   LIMIT 1""",
                (addr,),
            )

        def auto_assign():
            fullname = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0] or ""
            parts = fullname.strip().split()
            last = parts[-1] if parts else ""
            letter = last[0].upper() if last else "A"
            res = find_next_available_slot_with_overflow(letter, basket)
            if not res:
                messagebox.showwarning("No Slot", "No automatic slot available; please assign manually.", parent=parent)
                return self._manual_assign_new(parent, pres_id, refresh_cb)
            slot_ids, shelf = res
            labels = slot_ids_to_labels(slot_ids)
            # Example: "Proposed Shelf F: A1 (& A2)" with full labels below
            pos_compact = " & ".join([l.split("-")[1] for l in labels])

            conf = ctk.CTkToplevel(parent)
            conf.title("Confirm Slot")
            conf.geometry("560x200")
            make_modal(conf, parent)

            header = ctk.CTkFrame(conf, fg_color="#0b5cab")
            header.pack(fill="x")
            ctk.CTkLabel(header, text="Confirm Auto-Assignment", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
                anchor="w", padx=12, pady=8
            )

            body = ctk.CTkFrame(conf)
            body.pack(fill="both", expand=True, padx=12, pady=12)
            ctk.CTkLabel(
                body,
                text=f"Proposed Shelf {shelf}: {pos_compact}\n(Full: {', '.join(labels)})",
                font=ctk.CTkFont(size=FONT_LABEL_SIZE),
            ).pack(pady=(0, 10))

            btns = ctk.CTkFrame(body)
            btns.pack(fill="x")
            ctk.CTkButton(
                btns,
                text="✅ Confirm",
                fg_color="#2F8B2F",
                hover_color="#277327",
                command=lambda: self._confirm_auto(conf, pres_id, slot_ids, labels, parent, refresh_cb),
            ).pack(side="left", padx=6)
            ctk.CTkButton(
                btns,
                text="✋ Deny / Manual",
                fg_color="#6B6B6B",
                hover_color="#585858",
                command=lambda: (conf.destroy(), self._manual_assign_new(parent, pres_id, refresh_cb)),
            ).pack(side="right", padx=6)

        if fam:
            fam_pid, fam_slot = fam
            fam_label = slot_id_to_label(fam_slot)

            fp = ctk.CTkToplevel(parent)
            fp.title("Family Match")
            fp.geometry("560x190")
            make_modal(fp, parent)

            header = ctk.CTkFrame(fp, fg_color="#7A3DB8")
            header.pack(fill="x")
            ctk.CTkLabel(header, text="Family Bin Suggestion", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
                anchor="w", padx=12, pady=8
            )

            body = ctk.CTkFrame(fp)
            body.pack(fill="both", expand=True, padx=12, pady=12)
            ctk.CTkLabel(
                body,
                text=f"Same address found. Existing bin at {fam_label}.\nBin together?",
                font=ctk.CTkFont(size=FONT_LABEL_SIZE),
            ).pack(pady=(0, 10))

            btns = ctk.CTkFrame(body)
            btns.pack(fill="x")
            ctk.CTkButton(
                btns,
                text="🧺 Bin Together",
                fg_color="#7A3DB8",
                hover_color="#652f98",
                command=lambda: self._bin_together(fp, pres_id, fam_slot, fam_label, parent, refresh_cb),
            ).pack(side="left", padx=6)
            ctk.CTkButton(btns, text="Cancel", command=lambda: (fp.destroy(), auto_assign())).pack(side="right", padx=6)
        else:
            auto_assign()

    def _confirm_auto(self, conf_win, pres_id, slot_ids, labels, parent, refresh_cb):
        mark_slots_occupied(slot_ids)
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_ids[0], pres_id))
        log_action(f"Auto-assigned prescription id={pres_id} to {', '.join(labels)}")
        conf_win.destroy()
        refresh_cb()
        self.refresh_patient_table()
        # After confirm, show LED popup
        open_led_popup(parent, slot_ids, "yellow", "Guide to New Slot")

    def _bin_together(self, fp, pres_id, fam_slot, fam_label, parent, refresh_cb):
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (fam_slot, pres_id))
        log_action(f"Binned prescription id={pres_id} with family at {fam_label}")
        fp.destroy()
        refresh_cb()
        self.refresh_patient_table()
        # Blink after bin confirmation
        open_led_popup(parent, [fam_slot], "purple", "Family Bin Location")

    def _manual_assign_new(self, parent, pres_id, refresh_cb):
        m = ctk.CTkToplevel(parent)
        m.title("Manual Slot Assignment")
        m.geometry("520x280")
        make_modal(m, parent)

        header = ctk.CTkFrame(m, fg_color="#0b5cab")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Manual Slot Assignment", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        body = ctk.CTkFrame(m)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        ctk.CTkLabel(body, text="Shelf (F/L/R)", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=0, padx=8, pady=8, sticky="e")
        s_e = ctk.CTkEntry(body, width=100)
        s_e.grid(row=0, column=1, padx=8, pady=8)

        ctk.CTkLabel(body, text="Row (A–Z)", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=1, column=0, padx=8, pady=8, sticky="e")
        r_e = ctk.CTkEntry(body, width=100)
        r_e.grid(row=1, column=1, padx=8, pady=8)

        ctk.CTkLabel(body, text="Column (1–100)", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=2, column=0, padx=8, pady=8, sticky="e")
        c_e = ctk.CTkEntry(body, width=100)
        c_e.grid(row=2, column=1, padx=8, pady=8)

        def assign():
            s = s_e.get().strip().upper()
            row = r_e.get().strip().upper()
            try:
                col = int(c_e.get().strip())
            except Exception:
                messagebox.showerror("Error", "Invalid column", parent=m)
                return
            rec = get_slot_by_position(s, row, col)
            if not rec:
                messagebox.showerror("Error", "Slot does not exist", parent=m)
                return
            slot_id, occ = rec
            if occ:
                messagebox.showerror("Error", "Slot already occupied", parent=m)
                return
            mark_slots_occupied([slot_id])
            db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_id, pres_id))
            log_action(f"Manually assigned prescription id={pres_id} to {slot_id_to_label(slot_id)}")
            m.destroy()
            refresh_cb()
            self.refresh_patient_table()
            open_led_popup(parent, [slot_id], "yellow", "Guide to New Slot")

        ctk.CTkButton(body, text="Assign", fg_color="#0B5CAB", hover_color="#084b8a", command=assign).grid(
            row=3, column=0, columnspan=2, pady=12
        )

    def _edit_prescription_popup(self, patient_id, pres_id, refresh_cb):
        e = ctk.CTkToplevel(self)
        e.title("Edit Prescription")
        e.geometry("640x520")
        make_modal(e, self)

        head = ctk.CTkFrame(e, fg_color="#0b5cab")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Edit Prescription", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        row = db_fetchone(
            """SELECT medication,quantity,date_added,basket_size,slot_id
               FROM prescriptions WHERE id=?""",
            (pres_id,),
        )
        if not row:
            e.destroy()
            return
        med, qty, dt, basket, slot_id = row

        body = ctk.CTkFrame(e)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        ctk.CTkLabel(body, text="Medication", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        med_e = ctk.CTkEntry(body, width=300)
        med_e.grid(row=0, column=1, padx=6, pady=6)
        med_e.insert(0, med)

        ctk.CTkLabel(body, text="Quantity", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=1, column=0, sticky="e", padx=6, pady=6)
        qty_e = ctk.CTkEntry(body, width=140)
        qty_e.grid(row=1, column=1, padx=6, pady=6)
        qty_e.insert(0, str(qty) if qty is not None else "")

        ctk.CTkLabel(body, text="Date (MM/DD/YYYY)", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=2, column=0, sticky="e", padx=6, pady=6)
        date_e = ctk.CTkEntry(body, width=170)
        date_e.grid(row=2, column=1, padx=6, pady=6)
        if dt:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    date_e.insert(0, datetime.strptime(dt, fmt).strftime("%m/%d/%Y"))
                    break
                except Exception:
                    pass

        ctk.CTkLabel(body, text="Basket", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(row=3, column=0, sticky="e", padx=6, pady=6)
        b_cb = ctk.CTkComboBox(body, values=["small", "large"], width=140)
        b_cb.set(basket if basket in ("small", "large") else "small")
        b_cb.grid(row=3, column=1, padx=6, pady=6)

        ctk.CTkLabel(body, text="LED Location (F-A61 or id)", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(
            row=4, column=0, sticky="e", padx=6, pady=6
        )
        loc_e = ctk.CTkEntry(body, width=170)
        loc_e.grid(row=4, column=1, padx=6, pady=6)
        loc_e.insert(0, slot_id_to_label(slot_id) if slot_id else "")

        def save_edit():
            new_med = med_e.get().strip()
            new_qty = qty_e.get().strip()
            new_b = b_cb.get().strip().lower()
            dtxt = date_e.get().strip()
            if dtxt:
                try:
                    d = datetime.strptime(dtxt, "%m/%d/%Y").strftime("%Y-%m-%d")
                except Exception:
                    messagebox.showerror("Error", "Date must be MM/DD/YYYY", parent=e)
                    return
            else:
                d = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_loc = loc_e.get().strip()
            if new_loc and (parse_location_label_to_slot_id(new_loc) is None):
                messagebox.showerror("Error", "Invalid LED location. Use F-A61 or numeric id.", parent=e)
                return
            new_sid = parse_location_label_to_slot_id(new_loc) if new_loc else None
            if new_sid and new_sid != slot_id:
                if not messagebox.askyesno("Confirm", "Manually change LED slot?", parent=e):
                    return
                r = db_fetchone("SELECT occupied FROM slots WHERE id=?", (new_sid,))
                if not r:
                    messagebox.showerror("Error", "Slot not found", parent=e)
                    return
                if r[0] == 1:
                    messagebox.showerror("Error", "Slot already occupied", parent=e)
                    return
                if slot_id:
                    mark_slots_free([slot_id])
                mark_slots_occupied([new_sid])
                db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (new_sid, pres_id))
                log_action(f"Changed prescription id={pres_id} location to {slot_id_to_label(new_sid)}")
            db_exec(
                """UPDATE prescriptions
                   SET medication=?, quantity=?, date_added=?, basket_size=?
                   WHERE id=?""",
                (new_med, int(new_qty) if new_qty.isdigit() else new_qty, d, new_b, pres_id),
            )
            log_action(f"Edited prescription id={pres_id} (med='{new_med}', qty='{new_qty}', basket='{new_b}')")
            e.destroy()
            refresh_cb()
            self.refresh_patient_table()

        ctk.CTkButton(body, text="Save Changes", fg_color="#0B5CAB", hover_color="#084b8a", command=save_edit).grid(
            row=5, column=0, columnspan=2, pady=12
        )

    # ---------------- Shelf Assignment ----------------
    def open_shelf_assignment(self):
        win = ctk.CTkToplevel(self)
        win.title("Shelf Assignment")
        win.geometry("940x900")
        make_modal(win, self)

        head = ctk.CTkFrame(win, fg_color="#f7630c")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Shelf Assignment", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )
        ctk.CTkLabel(
            win,
            text="Assign ranges per letter. Bounds like A1 and Z13. Overflow is a letter section too.",
            font=ctk.CTkFont(size=FONT_LABEL_SIZE),
        ).pack(anchor="w", padx=12, pady=(8, 6))

        cont = ctk.CTkFrame(win)
        cont.pack(fill="both", expand=True, padx=10, pady=10)
        canvas = ctk.CTkCanvas(cont, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        yscroll = ttk.Scrollbar(cont, orient="vertical", command=canvas.yview)
        yscroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=yscroll.set)
        inner = ctk.CTkFrame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_conf(_):
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _on_conf)

        # headers
        hrow = ctk.CTkFrame(inner)
        hrow.grid(row=0, column=0, columnspan=4, sticky="ew", padx=8, pady=(6, 2))
        for i, htxt in enumerate(("Letter", "Shelf (F/L/R)", "Lower (A1)", "Upper (Z13)")):
            ctk.CTkLabel(hrow, text=htxt, font=ctk.CTkFont(size=FONT_LABEL_SIZE, weight="bold")).grid(
                row=0, column=i, padx=10, pady=6, sticky="w"
            )

        self.letter_widgets = {}
        letters = [chr(i) for i in range(65, 91)] + ["Overflow"]
        for i, L in enumerate(letters, start=1):
            rowf = ctk.CTkFrame(inner)
            rowf.grid(row=i, column=0, sticky="w", padx=8, pady=3)
            ctk.CTkLabel(rowf, text=L, width=60, anchor="w", font=ctk.CTkFont(size=FONT_LABEL_SIZE)).grid(
                row=0, column=0, padx=6
            )
            shelf_cb = ctk.CTkComboBox(rowf, values=["", "F", "L", "R"], width=100)
            shelf_cb.set("")
            lower_e = ctk.CTkEntry(rowf, width=140)
            upper_e = ctk.CTkEntry(rowf, width=140)
            shelf_cb.grid(row=0, column=1, padx=10)
            lower_e.grid(row=0, column=2, padx=10)
            upper_e.grid(row=0, column=3, padx=10)
            r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (L,))
            if r:
                shelf_cb.set(r[0] or "")
                if r[1]:
                    lower_e.insert(0, r[1])
                if r[2]:
                    upper_e.insert(0, r[2])
            self.letter_widgets[L] = (shelf_cb, lower_e, upper_e)

        foot = ctk.CTkFrame(win)
        foot.pack(fill="x", padx=10, pady=10)

        def save_all():
            for L, (s, lo, up) in self.letter_widgets.items():
                S, LO, UP = s.get().strip().upper(), lo.get().strip().upper(), up.get().strip().upper()
                if S and S not in ("F", "L", "R"):
                    messagebox.showerror("Error", f"Invalid shelf for {L}", parent=win)
                    return
                if LO and not parse_bound(LO):
                    messagebox.showerror("Error", f"Lower bound invalid for {L}: {LO}", parent=win)
                    return
                if UP and not parse_bound(UP):
                    messagebox.showerror("Error", f"Upper bound invalid for {L}: {UP}", parent=win)
                    return
                db_exec(
                    "INSERT OR REPLACE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                    (L, S, LO, UP),
                )
            log_action("Updated shelf assignments")
            win.destroy()

        ctk.CTkButton(foot, text="💾 Save All & Close", fg_color="#0B5CAB", hover_color="#084b8a", command=save_all).pack(
            side="right"
        )

    # ---------------- Overdue Tab ----------------
    def open_overdue_tab(self):
        data = get_overdue_prescriptions(14)
        agg = aggregate_overdue_by_patient(data)

        win = ctk.CTkToplevel(self)
        win.title("Overdue Medications")
        win.geometry("1140x820")
        make_modal(win, self)

        head = ctk.CTkFrame(win, fg_color="#c50f1f")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Overdue Medications", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        top = ctk.CTkFrame(win)
        top.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(
            top,
            text="🔴 Light Up Overdue",
            fg_color="#D83B01",
            hover_color="#B32F00",
            command=lambda: self._light_up_overdue(data, win),
        ).pack(side="left", padx=6)
        cnt = ctk.CTkLabel(top, text=f"Total overdue prescriptions: {len(data)}", font=ctk.CTkFont(size=FONT_LABEL_SIZE))
        cnt.pack(side="right", padx=6)

        tablef = ctk.CTkFrame(win)
        tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Patient", "Address", "# Overdue", "Oldest (days)", "Locations")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=220 if c != "Locations" else 420, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        tv.tag_configure("odd", background="#f5f9ff")
        tv.tag_configure("even", background="#ffffff")

        for i, (pid, v) in enumerate(agg.items()):
            locs = ", ".join(slot_ids_to_labels(sorted(list(v["slots"]))))
            tag = "odd" if i % 2 else "even"
            tv.insert(
                "",
                "end",
                iid=str(pid),
                values=(v["name"], v["address"] or "", v["count"], v["oldest"], locs),
                tags=(tag,),
            )
        tv.bind("<Double-1>", lambda e: self._open_overdue_patient_detail(tv))

    def _open_overdue_patient_detail(self, tree):
        sel = tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        items = [o for o in get_overdue_prescriptions(14) if o["patient_id"] == pid]
        if not items:
            messagebox.showinfo("Overdue", "No overdue items (refresh?)", parent=self)
            return

        win = ctk.CTkToplevel(self)
        win.title("Overdue Details")
        win.geometry("940x560")
        make_modal(win, self)

        head = ctk.CTkFrame(win, fg_color="#c50f1f")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Overdue — {items[0]['name']}", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        top = ctk.CTkFrame(win)
        top.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(
            top,
            text="🔴 Light Up These",
            fg_color="#D83B01",
            hover_color="#B32F00",
            command=lambda: self._light_up_overdue(items, win),
        ).pack(side="left")

        tablef = ctk.CTkFrame(win)
        tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Medication", "Quantity", "Days Overdue", "Location")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=220 if c != "Location" else 260, anchor="w")
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
            messagebox.showinfo("LED", "No overdue items with assigned slots.", parent=parent)
            return
        open_led_popup(parent, slots, "red", "Overdue — LED")

    # ---------------- Previous Actions ----------------
    def open_actions_tab(self):
        win = ctk.CTkToplevel(self)
        win.title("Previous Actions")
        win.geometry("1040x680")
        make_modal(win, self)

        head = ctk.CTkFrame(win, fg_color="#6b6b6b")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Previous Actions (Today)", font=ctk.CTkFont(size=FONT_HEADER_SIZE, weight="bold")).pack(
            anchor="w", padx=12, pady=8
        )

        tablef = ctk.CTkFrame(win)
        tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Time", "Actor", "Action")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=160 if c != "Action" else 760, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        tv.tag_configure("odd", background="#f5f9ff")
        tv.tag_configure("even", background="#ffffff")

        acts = get_todays_actions(300)
        for i, (ts, actor, action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tag = "odd" if i % 2 else "even"
            tv.insert("", "end", values=(t, actor, action), tags=(tag,))

        ctk.CTkButton(win, text="🔄 Refresh", command=lambda: self._refresh_actions(tv)).pack(pady=8)

    def _refresh_actions(self, tv):
        for r in tv.get_children():
            tv.delete(r)
        acts = get_todays_actions(300)
        for i, (ts, actor, action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tag = "odd" if i % 2 else "even"
            tv.insert("", "end", values=(t, actor, action), tags=(tag,))

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
