# ================================
# Pharmacy LED System - Part 1
# Imports, DB Helpers, LED Simulation
# ================================

import os, sqlite3, threading, time
from datetime import datetime
import customtkinter as ctk
from tkinter import ttk, messagebox
import matplotlib
matplotlib.use("Agg")   # prevent crashes if GUI not ready
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ----------------
# DB Setup
# ----------------
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

# ----------------
# Actions Log
# ----------------
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
# --- PATCH 1: Ensure all popup windows are always on top ---
def make_topmost(win):
    """Force popup window to appear above its parent and take focus."""
    win.lift()
    win.focus_force()
    win.grab_set()        # locks input until closed
    win.attributes("-topmost", True)
    win.after_idle(lambda: win.attributes("-topmost", False))
# --- END PATCH 1 ---

# ----------------
# LED Simulation
# ----------------
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
            time.sleep(0.25); continue
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
    win.geometry("520x160"); win.lift(); win.attributes("-topmost", True)
    make_topmost(win)
    ctk.CTkLabel(win, text=f"Blinking: {', '.join(slot_ids_to_labels(slots))}",
                 font=ctk.CTkFont(size=16)).pack(padx=14, pady=(14,6))
    btn = ctk.CTkButton(win, text="⏸ Pause Lights", width=140,
                        command=lambda: _toggle_pause(btn, key))
    btn.pack(side="left", padx=12, pady=12)
    ctk.CTkButton(win, text="✅ Done", width=120,
                  command=lambda: (stop_blink(key), win.destroy())
    ).pack(side="right", padx=12, pady=12)

def _toggle_pause(button, key):
    pause_toggle(key)
    if button.cget("text").startswith("⏸"):
        button.configure(text="▶ Resume Lights")
    else:
        button.configure(text="⏸ Pause Lights")

# ----------------
# DB Schema Init
# ----------------
def init_db():
    with db_connect() as conn:
        cur = conn.cursor()
        # Ensure all main tables exist
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
        cur.execute("""CREATE TABLE IF NOT EXISTS shelves(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
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
        cur.execute("""CREATE TABLE IF NOT EXISTS letter_sections(
            letter TEXT PRIMARY KEY,
            shelf TEXT,
            lower_bound TEXT,
            upper_bound TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS actions_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            actor TEXT,
            action TEXT
        )""")
        conn.commit()
# ================================
# Pharmacy LED System - Part 2
# Slot & Section Utilities, Overdue Logic
# ================================

# ----- Basic helpers for rows/cols/labels -----
def rows_range(start_row, end_row):
    return [chr(i) for i in range(ord(start_row), ord(end_row) + 1)]

def parse_bound(bound_text):
    """Parse 'A1' -> ('A', 1). Returns None if invalid."""
    if not bound_text:
        return None
    s = bound_text.strip().upper()
    if len(s) < 2:
        return None
    row = s[0]
    if not row.isalpha():
        return None
    try:
        col = int(s[1:])
    except Exception:
        return None
    if not (row >= 'A' and row <= 'Z'):
        return None
    if col <= 0:
        return None
    return (row, col)

def slot_id_to_tuple(slot_id):
    r = db_fetchone("SELECT shelf,row,col FROM slots WHERE id=?", (slot_id,))
    if not r: return None
    return (r[0], r[1], r[2])  # (shelf, row, col)

def label_to_slot_id(label):
    """
    Accepts:
      - 'F-A61'
      - 'F-A-61'
      - numeric id '1234'
    """
    if not label: return None
    s = label.strip().upper().replace("--", "-").replace(" ", "")
    if s.isdigit():
        row = db_fetchone("SELECT id FROM slots WHERE id=?", (int(s),))
        return int(s) if row else None
    parts = s.split("-")
    if len(parts) == 2:
        shelf, rowcol = parts
        if len(rowcol) >= 2:
            row = rowcol[0]
            try: col = int(rowcol[1:])
            except: return None
            r = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?",
                            (shelf, row, col))
            return r[0] if r else None
    if len(parts) == 3:
        shelf, row, coltxt = parts
        try: col = int(coltxt)
        except: return None
        r = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?",
                        (shelf, row, col))
        return r[0] if r else None
    return None

# ----- Shelves & slots population -----
def populate_slots_for_shelf(shelf_name, rows_count, cols_count):
    """
    Ensure slots exist for a shelf with given rows/cols. Rows are A.. up to rows_count.
    """
    existing = db_fetchone("SELECT COUNT(*) FROM slots WHERE shelf=?", (shelf_name,))
    if existing and existing[0] > 0:
        return  # already populated
    for i in range(rows_count):
        row_letter = chr(ord('A') + i)
        for col in range(1, cols_count + 1):
            db_exec("INSERT INTO slots(shelf,row,col,occupied) VALUES(?,?,?,0)",
                    (shelf_name, row_letter, col))

def populate_all_slots_from_shelves():
    """
    Populate slots for all shelves that don't have slots yet.
    """
    shelves = db_fetchall("SELECT name, rows_count, cols_count FROM shelves")
    for name, rows_count, cols_count in shelves:
        populate_slots_for_shelf(name, rows_count or 26, cols_count or 100)

# ----- Letter sections -----
def get_letter_section(letter):
    r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (letter,))
    if not r: return None
    shelf, lower, upper = r
    if not shelf or not lower or not upper:
        return None
    lo = parse_bound(lower)
    up = parse_bound(upper)
    if not lo or not up:
        return None
    return (shelf, lo[0], lo[1], up[0], up[1])  # (shelf, start_row, start_col, end_row, end_col)

def get_overflow_section():
    return get_letter_section("Overflow")

# ----- Slot occupancy helpers -----
def mark_slots_occupied(slot_ids):
    if not slot_ids: return
    with db_connect() as conn:
        cur = conn.cursor()
        for sid in slot_ids:
            cur.execute("UPDATE slots SET occupied=1 WHERE id=?", (sid,))
        conn.commit()

def mark_slots_free(slot_ids):
    if not slot_ids: return
    with db_connect() as conn:
        cur = conn.cursor()
        for sid in slot_ids:
            cur.execute("UPDATE slots SET occupied=0 WHERE id=?", (sid,))
        conn.commit()

def get_slot_by_position(shelf, row, col):
    return db_fetchone("SELECT id, occupied FROM slots WHERE shelf=? AND row=? AND col=?",
                       (shelf, row, col))

# ----- Section search -----
def find_slot_in_section(shelf_name, start_row, start_col, end_row, end_col, basket_size):
    """
    Iterate in order: rows start_row..end_row, columns start_col..end_col (progressive).
    Returns ([slot_ids], shelf_name) for small (1 slot) or large (2 consecutive columns) basket.
    """
    rows = rows_range(start_row, end_row)
    with db_connect() as conn:
        cur = conn.cursor()
        for r in rows:
            c_start = start_col if r == start_row else 1
            c_end = end_col if r == end_row else 10_000  # safe cap; real limit enforced by actual rows
            # Limit columns to what's in the DB for that shelf/row
            max_col_row = db_fetchone("SELECT MAX(col) FROM slots WHERE shelf=? AND row=?", (shelf_name, r))
            if not max_col_row or not max_col_row[0]:
                continue
            c_end = min(c_end, max_col_row[0])

            for c in range(c_start, c_end + 1):
                cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?",
                            (shelf_name, r, c))
                row1 = cur.fetchone()
                if not row1: 
                    continue
                sid, occ = row1
                if occ != 0:
                    continue
                if basket_size == "large":
                    # need c+1 free in same row
                    cur.execute("SELECT id,occupied FROM slots WHERE shelf=? AND row=? AND col=?",
                                (shelf_name, r, c+1))
                    row2 = cur.fetchone()
                    if row2 and row2[1] == 0:
                        return ([sid, row2[0]], shelf_name)
                else:
                    return ([sid], shelf_name)
    return None

def find_next_available_slot_primary(letter, basket_size):
    sec = get_letter_section(letter)
    if not sec:
        return None
    shelf, sr, sc, er, ec = sec
    return find_slot_in_section(shelf, sr, sc, er, ec, basket_size)

def find_next_available_slot_with_overflow(letter, basket_size):
    res = find_next_available_slot_primary(letter, basket_size)
    if res:
        return res
    over = get_overflow_section()
    if over:
        shelf, sr, sc, er, ec = over
        res2 = find_slot_in_section(shelf, sr, sc, er, ec, basket_size)
        if res2:
            return res2
    return None

# ----- Patient letter & wrong-section checks -----
def get_patient_letter(patient_id):
    name = db_fetchone("SELECT name FROM patients WHERE id=?", (patient_id,))
    if not name or not name[0]:
        return "A"
    last = name[0].strip().split()[-1]
    return last[0].upper() if last else "A"

def is_slot_in_letter_section(letter, slot_id):
    """Overflow exempt; returns True if slot is within the letter's section bounds."""
    if letter == "Overflow":
        return True
    sec = get_letter_section(letter)
    if not sec:
        return True  # if no defined section, don't flag as wrong
    shelf, sr, sc, er, ec = sec
    slot = slot_id_to_tuple(slot_id)
    if not slot:
        return True
    sname, row, col = slot
    if sname != shelf:
        return False
    if row < sr or row > er:
        return False
    # Compare columns within row range
    # If rows differ, columns can be anywhere within prior logic; but keep it simple:
    if row == sr and col < sc:
        return False
    if row == er and col > ec:
        return False
    return True

def format_slot_label_for_patient(patient_id, slot_id):
    """Return 'F-A12' or 'F-A12 (⚠ Wrong Section — Move)'."""
    if not slot_id:
        return ""
    lbl = slot_id_to_label(slot_id)
    letter = get_patient_letter(patient_id)
    if letter == "Overflow":
        return lbl
    if not is_slot_in_letter_section(letter, slot_id):
        return f"{lbl} (⚠ Wrong Section — Move)"
    return lbl

# ----- Family bins by address -----
def find_family_bins_by_address(address):
    """
    Returns list of (patient_id, patient_name, slot_id) for any existing slot tied to this address.
    """
    if not address:
        return []
    rows = db_fetchall("""
        SELECT DISTINCT p.id, p.name, pr.slot_id
        FROM patients p
        JOIN prescriptions pr ON pr.patient_id = p.id
        WHERE p.address = ? AND pr.slot_id IS NOT NULL
    """, (address,))
    # Deduplicate by slot_id preference
    out = []
    seen = set()
    for pid, name, slot_id in rows:
        if not slot_id or slot_id in seen:
            continue
        seen.add(slot_id)
        out.append((pid, name, slot_id))
    return out

# ----- Overdue logic -----
def parse_any_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def get_overdue_prescriptions(min_days_over=14):
    rows = db_fetchall("""
        SELECT pr.id, pr.patient_id, pr.medication, pr.quantity, pr.date_added, pr.slot_id,
               p.name, p.address
        FROM prescriptions pr
        JOIN patients p ON p.id = pr.patient_id
    """)
    now = datetime.now()
    out = []
    for pr_id, pid, med, qty, dtxt, slot_id, name, address in rows:
        dt = parse_any_date(dtxt)
        if not dt:
            continue
        days = (now - dt).days
        if days >= min_days_over:
            out.append({
                "prescription_id": pr_id,
                "patient_id": pid,
                "name": name,
                "address": address,
                "medication": med,
                "quantity": qty,
                "days_overdue": days,
                "slot_id": slot_id
            })
    return out

def aggregate_overdue_by_patient(items):
    agg = {}
    for it in items:
        pid = it["patient_id"]
        if pid not in agg:
            agg[pid] = {
                "name": it["name"], "address": it["address"],
                "count": 0, "oldest": 0, "slots": set()
            }
        agg[pid]["count"] += 1
        if it["days_overdue"] > agg[pid]["oldest"]:
            agg[pid]["oldest"] = it["days_overdue"]
        if it["slot_id"]:
            agg[pid]["slots"].add(it["slot_id"])
    return agg
# ================================
# Pharmacy LED System - Part 3
# Core Logic (Auto-Assign, Family Bin, Manual Assign, Clear-All)
# ================================

def auto_assign_for_patient(pid, basket_size):
    """
    Returns (slot_ids, shelf_name) or None.
    Prefers letter's section; falls back to Overflow if configured.
    """
    letter = get_patient_letter(pid)
    res = find_next_available_slot_with_overflow(letter, basket_size)
    return res

def confirm_auto_assign_popup(parent, presc_id, slot_ids, shelf_name, refresh_cb):
    labels = slot_ids_to_labels(slot_ids)
    short = " & ".join([l.split("-")[1] for l in labels])
    win = ctk.CTkToplevel(parent); win.title("Confirm Slot")
    win.geometry("520x180"); win.lift(); win.attributes("-topmost", True)
    make_topmost(win)
    ctk.CTkLabel(win, text=f"Proposed Shelf {shelf_name}: {short}\n(Full: {', '.join(labels)})",
                 font=ctk.CTkFont(size=17)).pack(padx=12, pady=12)
    def accept():
        mark_slots_occupied(slot_ids)
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_ids[0], presc_id))
        log_action(f"Auto-assigned prescription id={presc_id} to {', '.join(labels)}")
        win.destroy()
        refresh_cb()
        open_led_popup(parent, slot_ids, "yellow", "Guide to New Slot")
    def deny():
        win.destroy()
        manual_assign_popup(parent, presc_id, refresh_cb)
    btnf = ctk.CTkFrame(win); btnf.pack(pady=10)
    ctk.CTkButton(btnf, text="✅ Confirm", width=120, command=accept).pack(side="left", padx=8)
    ctk.CTkButton(btnf, text="✋ Deny / Manual", width=140, command=deny).pack(side="left", padx=8)

def manual_assign_popup(parent, presc_id, refresh_cb):
    m = ctk.CTkToplevel(parent); m.title("Manual Slot Assignment")
    m.geometry("420x260"); m.lift(); m.attributes("-topmost", True)
    make_topmost(m)
    ctk.CTkLabel(m, text="Shelf (e.g., F/L/R)", font=ctk.CTkFont(size=17)).grid(row=0, column=0, padx=8, pady=8, sticky="e")
    s_e = ctk.CTkEntry(m, width=80); s_e.grid(row=0, column=1, padx=8, pady=8)
    ctk.CTkLabel(m, text="Row (A–Z)", font=ctk.CTkFont(size=17)).grid(row=1, column=0, padx=8, pady=8, sticky="e")
    r_e = ctk.CTkEntry(m, width=80); r_e.grid(row=1, column=1, padx=8, pady=8)
    ctk.CTkLabel(m, text="Column (1–N)", font=ctk.CTkFont(size=17)).grid(row=2, column=0, padx=8, pady=8, sticky="e")
    c_e = ctk.CTkEntry(m, width=100); c_e.grid(row=2, column=1, padx=8, pady=8)
    def assign():
        s = s_e.get().strip().upper()
        row = r_e.get().strip().upper()
        try:
            col = int(c_e.get().strip())
        except:
            messagebox.showerror("Error", "Invalid column"); return
        rec = get_slot_by_position(s, row, col)
        if not rec:
            messagebox.showerror("Error", "Slot does not exist"); return
        slot_id, occ = rec
        if occ:
            messagebox.showerror("Error", "Slot already occupied"); return
        mark_slots_occupied([slot_id])
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_id, presc_id))
        log_action(f"Manually assigned prescription id={presc_id} to {slot_id_to_label(slot_id)}")
        m.destroy(); refresh_cb()
        open_led_popup(parent, [slot_id], "yellow", "Guide to New Slot")
    ctk.CTkButton(m, text="Assign", width=120, command=assign).grid(row=3, column=0, columnspan=2, pady=12)

def try_family_bin_popup(parent, patient_id, address, presc_id, refresh_cb, on_no_family):
    """
    If address matches existing bins, show multi-choice popup allowing to bin with any.
    Else -> call on_no_family().
    """
    choices = find_family_bins_by_address(address)
    # Remove any bins that belong to this same prescription (none yet) but keep same-patient bins.
    choices = [(pid, name, sid) for (pid, name, sid) in choices if sid is not None]
    if not choices:
        on_no_family(); return

    win = ctk.CTkToplevel(parent); win.title("Family Match")
    win.geometry("560x260"); win.lift(); win.attributes("-topmost", True)
    make_topmost(win)
    ctk.CTkLabel(win, text="Same address found. Select a bin to share:", font=ctk.CTkFont(size=17)).pack(padx=12, pady=8)

    listf = ctk.CTkFrame(win); listf.pack(fill="both", expand=True, padx=12, pady=8)
    tv = ttk.Treeview(listf, columns=("Name","Location"), show="headings", height=6)
    tv.heading("Name", text="Name"); tv.heading("Location", text="Location")
    tv.column("Name", width=260, anchor="w"); tv.column("Location", width=200, anchor="w")
    tv.pack(fill="both", expand=True)

    for (pid, name, sid) in choices:
        tv.insert("", "end", iid=str(sid), values=(name, slot_id_to_label(sid)))

    def bin_with_selected():
        sel = tv.selection()
        if not sel:
            messagebox.showinfo("Select", "Choose a bin to share."); return
        sid = int(sel[0])
        db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (sid, presc_id))
        log_action(f"Binned prescription id={presc_id} with family at {slot_id_to_label(sid)}")
        win.destroy()
        refresh_cb()
        open_led_popup(parent, [sid], "purple", "Family Bin Location")

    btnf = ctk.CTkFrame(win); btnf.pack(pady=8)
    ctk.CTkButton(btnf, text="🧺 Bin With Selected", width=160, command=bin_with_selected).pack(side="left", padx=8)
    ctk.CTkButton(btnf, text="Skip", width=120, command=lambda: (win.destroy(), on_no_family())).pack(side="left", padx=8)

def clear_all_prescriptions_with_led(parent, patient_id, on_done_refresh):
    rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (patient_id,))
    slots = sorted(list({r[0] for r in rows if r and r[0]}))
    if not slots:
        if messagebox.askyesno("Confirm", "No slots assigned. Clear all prescriptions anyway?"):
            db_exec("DELETE FROM prescriptions WHERE patient_id=?", (patient_id,))
            log_action(f"Cleared all prescriptions for patient_id={patient_id}")
            on_done_refresh()
        return

    # Blink and confirm
    key = start_blink(slots, "red")
    win = ctk.CTkToplevel(parent); win.title("Confirm Clear All")
    win.geometry("560x200"); win.lift(); win.attributes("-topmost", True)
    make_topmost(win)
    ctk.CTkLabel(win, text="Please confirm all indicated bins are EMPTY.\nThen click Confirm to clear all prescriptions.",
                 font=ctk.CTkFont(size=17)).pack(padx=12, pady=12)

    def confirm():
        stop_blink(key)
        # Free slots & delete prescriptions
        mark_slots_free(slots)
        db_exec("DELETE FROM prescriptions WHERE patient_id=?", (patient_id,))
        log_action(f"Cleared all prescriptions for patient_id={patient_id} (verified empty bins)")
        win.destroy()
        on_done_refresh()

    def cancel():
        stop_blink(key)
        win.destroy()

    btnf = ctk.CTkFrame(win); btnf.pack(pady=10)
    ctk.CTkButton(btnf, text="✅ Confirm & Clear", width=160, command=confirm).pack(side="left", padx=8)
    ctk.CTkButton(btnf, text="❌ Cancel", width=120, command=cancel).pack(side="left", padx=8)

def auto_reassign_in_section(parent, presc_id, refresh_cb):
    """Used in Edit Prescription popup → Automatic Assign (consolidate within letter section)."""
    row = db_fetchone("SELECT patient_id, basket_size FROM prescriptions WHERE id=?", (presc_id,))
    if not row:
        return
    pid, basket = row
    res = auto_assign_for_patient(pid, basket or "small")
    if not res:
        messagebox.showwarning("No Slot", "No available slot found (including overflow).")
        return
    slot_ids, shelf = res
    confirm_auto_assign_popup(parent, presc_id, slot_ids, shelf, refresh_cb)
# ================================
# Pharmacy LED System - Part 4
# UI Foundations (App Shell, Styling, Sidebar, Search/Sort)
# ================================

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
                    font=("Segoe UI", 16))
    style.configure("Treeview.Heading",
                    background="#0b5cab",
                    foreground="white",
                    font=("Segoe UI Semibold", 20))
    style.map("Treeview", background=[("selected", "#cfe8ff")])

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("Pharmacy LED System")
        self.geometry("1400x900")
        self.minsize(1280, 780)

        style_treeview()

        # ---------- Top bar ----------
        topbar = ctk.CTkFrame(self, corner_radius=0)
        topbar.pack(side="top", fill="x")
        ctk.CTkLabel(topbar, text="💊 Pharmacy LED System",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left", padx=12, pady=8)
        self.theme_mode = ctk.StringVar(value="Dark")
        def toggle_theme():
            mode = "Light" if self.theme_mode.get() == "Dark" else "Dark"
            self.theme_mode.set(mode)
            ctk.set_appearance_mode(mode)
        ctk.CTkButton(topbar, text="🌓 Theme", width=100, command=toggle_theme).pack(side="right", padx=10, pady=8)

        # ---------- Right sidebar ----------
        sidebar = ctk.CTkFrame(self, width=240, corner_radius=0)
        sidebar.pack(side="right", fill="y")
        ctk.CTkLabel(sidebar, text="Tools", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(12, 8))
        ctk.CTkButton(sidebar, text="📚 Shelf Assignment", command=self.open_shelf_assignment).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="⏰ Overdue Meds", command=self.open_overdue_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📊 Dashboard", command=self.open_dashboard_tab).pack(padx=10, pady=6, fill="x")
        ctk.CTkButton(sidebar, text="📝 Previous Actions", command=self.open_actions_tab).pack(padx=10, pady=6, fill="x")

        # ---------- Patients header + controls ----------
        header = ctk.CTkFrame(self, fg_color="#0b5cab")
        header.pack(fill="x", padx=0, pady=(0,6))
        ctk.CTkLabel(header, text="Patients", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", padx=10, pady=(0,8))
        ctk.CTkLabel(controls, text="Search:", font=ctk.CTkFont(size=17)).pack(side="left", padx=(8,6))
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(controls, textvariable=self.search_var, width=260)
        search_entry.pack(side="left", padx=(0,10))
        search_entry.bind("<Return>", lambda e: self.refresh_patient_table())

        # Sorting/filtering dropdown
        ctk.CTkLabel(controls, text="Sort:", font=ctk.CTkFont(size=17)).pack(side="left", padx=(8,6))
        self.sort_var = ctk.StringVar(value="Name A→Z (Last Initial)")
        sort_options = ["Name A→Z (Last Initial)", "Recently Added", "Address A→Z"]
        sort_menu = ctk.CTkComboBox(controls, values=sort_options, variable=self.sort_var, width=240)
        sort_menu.pack(side="left", padx=6)

        ctk.CTkButton(controls, text="🔎 Apply", command=self.refresh_patient_table).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="🔄 Refresh", command=self.refresh_patient_table).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="➕ Add Patient", fg_color="#2F8B2F",
                      hover_color="#277327", command=self.add_patient_popup).pack(side="right", padx=6)
        ctk.CTkButton(controls, text="🗑️ Delete Patient", fg_color="#D83B01",
                      hover_color="#B32F00", command=self.delete_selected_patient).pack(side="right", padx=6)

        # ---------- Patients table ----------
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

    # ----- Patients main -----
    def refresh_patient_table(self):
        for r in self.tree.get_children():
            self.tree.delete(r)

        q = (self.search_var.get() or "").strip()
        base_sql = """
            SELECT p.id, p.name, p.address, p.created_at
            FROM patients p
        """
        params = []
        where = []
        if q:
            # match on name or address or medication
            base_sql += " LEFT JOIN prescriptions pr ON pr.patient_id = p.id "
            where.append("(p.name LIKE ? OR p.address LIKE ? OR pr.medication LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

        if where:
            base_sql += " WHERE " + " AND ".join(where)

        # Sorting
        sort = self.sort_var.get()
        if sort == "Recently Added":
            base_sql += " ORDER BY p.created_at DESC"
        elif sort == "Address A→Z":
            base_sql += " ORDER BY p.address COLLATE NOCASE ASC, p.name COLLATE NOCASE ASC"
        else:
            # Name A→Z by last initial
            base_sql += " ORDER BY p.name COLLATE NOCASE ASC"

        rows = db_fetchall(base_sql, tuple(params))

        for i, (pid, name, addr, created) in enumerate(rows):
            date_disp = ""
            if created:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        date_disp = datetime.strptime(created, fmt).strftime("%m/%d/%Y")
                        break
                    except:
                        pass
            # all distinct locations
            slot_rows = db_fetchall(
                "SELECT DISTINCT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,)
            )
            locs = []
            for s in slot_rows:
                sid = s[0]
                if sid:
                    locs.append(format_slot_label_for_patient(pid, sid))
            tag = "odd" if i % 2 else "even"
            self.tree.insert("", "end", iid=str(pid),
                             values=(name, addr or "", date_disp, ", ".join([l for l in locs if l])),
                             tags=(tag,))

    def add_patient_popup(self):
        p = ctk.CTkToplevel(self); p.title("Add Patient")
        p.geometry("560x240"); p.lift(); p.attributes("-topmost", True)
        make_topmost(p)
        head = ctk.CTkFrame(p, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Add Patient", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        body = ctk.CTkFrame(p); body.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(body, text="Name", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=8, pady=8)
        name_e = ctk.CTkEntry(body, width=320); name_e.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(body, text="Address", font=ctk.CTkFont(size=17)).grid(row=1, column=0, sticky="e", padx=8, pady=8)
        addr_e = ctk.CTkEntry(body, width=320); addr_e.grid(row=1, column=1, padx=8, pady=8)
        def save():
            nm = name_e.get().strip()
            if not nm:
                messagebox.showerror("Error", "Name required"); return
            db_exec("INSERT INTO patients(name,address,created_at) VALUES(?,?,?)",
                    (nm, addr_e.get().strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            log_action(f"Added patient: {nm}")
            p.destroy(); self.refresh_patient_table()
        ctk.CTkButton(body, text="Save", width=120, fg_color="#2F8B2F", hover_color="#277327",
                      command=save).grid(row=2, column=0, columnspan=2, pady=10)

    def delete_selected_patient(self):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        name = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0]
        if not messagebox.askyesno("Confirm", f"Delete {name} and ALL prescriptions?"):
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
        if not sel: return
        pid = int(sel[0])
        self.open_patient_popup(pid)
# ================================
# Pharmacy LED System - Part 5
# Tabs & Popups (Patients Popup, Shelf Assignment, Overdue, Dashboard, Actions)
# ================================

    # ---------- Patient Popup ----------
    def open_patient_popup(self, pid):
        row = db_fetchone("SELECT name,address FROM patients WHERE id=?", (pid,))
        pname, paddr = (row[0], row[1] or "") if row else ("","")
        win = ctk.CTkToplevel(self); win.title(f"Patient — {pname}")
        win.geometry("1200x820"); win.lift(); win.attributes("-topmost", True)
        make_topmost(win)

        head = ctk.CTkFrame(win, fg_color="#107c10"); head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Patient — {pname}", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        info = ctk.CTkFrame(win); info.pack(fill="x", padx=12, pady=(10,6))
        ctk.CTkLabel(info, text="Name", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        name_e = ctk.CTkEntry(info, width=360); name_e.grid(row=0, column=1, padx=6, pady=6); name_e.insert(0, pname)
        ctk.CTkLabel(info, text="Address", font=ctk.CTkFont(size=17)).grid(row=0, column=2, sticky="e", padx=6, pady=6)
        addr_e = ctk.CTkEntry(info, width=360); addr_e.grid(row=0, column=3, padx=6, pady=6); addr_e.insert(0, paddr)
        ctk.CTkButton(info, text="💾 Save Patient", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=lambda: self._save_patient(pid, name_e.get().strip(), addr_e.get().strip(), win)).grid(row=0, column=4, padx=8)
        ctk.CTkButton(info, text="🔵 Light Up Locations",
                      command=lambda: self._light_up_patient(pid, win)).grid(row=0, column=5, padx=6)
        ctk.CTkButton(info, text="🗑️ Clear All Prescriptions", fg_color="#D83B01", hover_color="#B32F00",
                      command=lambda: clear_all_prescriptions_with_led(win, pid, self.refresh_patient_table)).grid(row=0, column=6, padx=6)

        # Rx Table
        mid = ctk.CTkFrame(win); mid.pack(fill="both", expand=True, padx=12, pady=(6,8))
        cols = ("Medication","Quantity","Date Added","Basket","Location")
        rx = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            rx.heading(c, text=c)
            rx.column(c, width=230 if c!="Location" else 300, anchor="w")
        rx.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=rx.yview)
        sb.pack(side="right", fill="y")
        rx.configure(yscroll=sb.set)
        rx.tag_configure("odd", background="#f5f9ff")
        rx.tag_configure("even", background="#ffffff")

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
                loc = format_slot_label_for_patient(pid, sid) if sid else ""
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
        med_e = ctk.CTkEntry(addf, width=220); med_e.grid(row=0, column=1, padx=6)
        ctk.CTkLabel(addf, text="Quantity", font=ctk.CTkFont(size=17)).grid(row=0, column=2, sticky="e", padx=6)
        qty_e = ctk.CTkEntry(addf, width=100); qty_e.grid(row=0, column=3, padx=6)
        ctk.CTkLabel(addf, text="Basket", font=ctk.CTkFont(size=17)).grid(row=0, column=4, sticky="e", padx=6)
        basket_cb = ctk.CTkComboBox(addf, values=["small","large"], width=120)
        basket_cb.set("small"); basket_cb.grid(row=0, column=5, padx=6)
        ctk.CTkButton(addf, text="➕ Add Prescription", fg_color="#2F8B2F", hover_color="#277327",
                      command=lambda: self._add_prescription(pid, addr_e.get().strip(), med_e, qty_e, basket_cb, win, populate_rx)
                      ).grid(row=0, column=6, padx=10)
        ctk.CTkButton(addf, text="🗑️ Delete Selected", fg_color="#D83B01", hover_color="#B32F00",
                      command=delete_selected_prescriptions).grid(row=0, column=7, padx=6)

    def _save_patient(self, pid, name, addr, popup):
        db_exec("UPDATE patients SET name=?,address=? WHERE id=?", (name, addr, pid))
        log_action(f"Updated patient: {name}")
        popup.destroy()
        self.refresh_patient_table()

    def _light_up_patient(self, pid, parent):
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL", (pid,))
        slots = list({r[0] for r in rows if r and r[0]})
        if not slots:
            messagebox.showinfo("LED","No assigned locations for this patient."); return
        open_led_popup(parent, slots, "blue", "Patient Locations")

    def _add_prescription(self, pid, address, med_e, qty_e, basket_cb, parent, refresh_cb):
        med = med_e.get().strip()
        qty = qty_e.get().strip()
        basket = (basket_cb.get() or "small").strip().lower()
        if not med or not qty or basket not in ("small","large"):
            messagebox.showerror("Error","Enter medication, quantity, and basket size."); return
        qv = int(qty) if qty.isdigit() else qty

        db_exec("""INSERT INTO prescriptions(patient_id,medication,quantity,date_added,basket_size)
                   VALUES(?,?,?,?,?)""", (pid, med, qv, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), basket))
        pres_id = db_fetchone("SELECT id FROM prescriptions WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,))[0]
        log_action(f"Added prescription '{med}' for patient_id={pid} (basket={basket})")

        # 1) Try family bin options if any
        def after_no_family():
            # 2) Else auto-assign
            res = auto_assign_for_patient(pid, basket)
            if not res:
                messagebox.showwarning("No Slot","No automatic slot available; please assign manually.")
                return manual_assign_popup(parent, pres_id, lambda: (refresh_cb(), self.refresh_patient_table()))
            slot_ids, shelf = res
            confirm_auto_assign_popup(parent, pres_id, slot_ids, shelf, lambda: (refresh_cb(), self.refresh_patient_table()))

        try_family_bin_popup(parent, pid, address, pres_id,
                             lambda: (refresh_cb(), self.refresh_patient_table()),
                             on_no_family=after_no_family)

        med_e.delete(0,"end"); qty_e.delete(0,"end")

    # ----- Edit Prescription -----
    def _edit_prescription_popup(self, patient_id, pres_id, refresh_cb):
        e = ctk.CTkToplevel(self); e.title("Edit Prescription")
        e.geometry("640x520"); e.lift(); e.attributes("-topmost", True)
        make_topmost(e)
        head = ctk.CTkFrame(e, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Edit Prescription", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        row = db_fetchone("""SELECT medication,quantity,date_added,basket_size,slot_id
                             FROM prescriptions WHERE id=?""", (pres_id,))
        if not row: e.destroy(); return
        med, qty, dt, basket, slot_id = row

        body = ctk.CTkFrame(e); body.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(body, text="Medication", font=ctk.CTkFont(size=17)).grid(row=0, column=0, sticky="e", padx=6, pady=6)
        med_e = ctk.CTkEntry(body, width=300); med_e.grid(row=0, column=1, padx=6, pady=6); med_e.insert(0, med)
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
        loc_e = ctk.CTkEntry(body, width=200); loc_e.grid(row=4, column=1, padx=6, pady=6)
        loc_e.insert(0, slot_id_to_label(slot_id) if slot_id else "")

        def save_edit():
            new_med = med_e.get().strip()
            new_qty = qty_e.get().strip()
            new_b = b_cb.get().strip().lower()
            dtxt = date_e.get().strip()
            if dtxt:
                try:
                    d = datetime.strptime(dtxt, "%m/%d/%Y").strftime("%Y-%m-%d")
                except:
                    messagebox.showerror("Error","Date must be MM/DD/YYYY"); return
            else:
                d = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_loc = loc_e.get().strip()
            new_sid = label_to_slot_id(new_loc) if new_loc else None
            if new_sid and new_sid != slot_id:
                if not messagebox.askyesno("Confirm","Manually change LED slot? (occupied check enforced)"):
                    return
                occ = db_fetchone("SELECT occupied FROM slots WHERE id=?", (new_sid,))
                if not occ: messagebox.showerror("Error","Slot not found"); return
                if occ[0] == 1: messagebox.showerror("Error","Slot already occupied"); return
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

        def auto_assign_here():
            auto_reassign_in_section(self, pres_id, lambda: (refresh_cb(), self.refresh_patient_table()))

        btnf = ctk.CTkFrame(body); btnf.grid(row=5, column=0, columnspan=2, pady=12)
        ctk.CTkButton(btnf, text="💾 Save Changes", width=150, fg_color="#0B5CAB", hover_color="#084b8a",
                      command=save_edit).pack(side="left", padx=8)
        ctk.CTkButton(btnf, text="🤖 Automatic Assign", width=180,
                      command=auto_assign_here).pack(side="left", padx=8)

    # ---------- Shelf Assignment ----------
    def open_shelf_assignment(self):
        win = ctk.CTkToplevel(self); win.title("Shelf Assignment")
        win.geometry("1200x860"); win.lift(); win.attributes("-topmost", True)
        make_topmost(win)

        head = ctk.CTkFrame(win, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Shelf Assignment", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        container = ctk.CTkFrame(win); container.pack(fill="both", expand=True, padx=10, pady=10)
        container.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(1, weight=1)

        # Left: Letter sections A..Z + Overflow
        left = ctk.CTkFrame(container)
        left.grid(row=0, column=0, sticky="nsew", padx=(0,5))

        ctk.CTkLabel(left, text="Letter Sections (Bounds like A1..D20)", font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=10, pady=6)

        letters_frame = ctk.CTkFrame(left)
        letters_frame.pack(fill="both", expand=True, padx=6, pady=6)

        # Scrollable canvas for many rows
        canvas = ctk.CTkCanvas(letters_frame, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        yscroll = ttk.Scrollbar(letters_frame, orient="vertical", command=canvas.yview)
        yscroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=yscroll.set)
        inner = ctk.CTkFrame(canvas)
        inner_id = canvas.create_window((0,0), window=inner, anchor="nw")
        def _on_conf(_):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_conf)

        # Table header
        hdr = ctk.CTkFrame(inner); hdr.grid(row=0, column=0, sticky="ew", padx=6, pady=(6,2))
        for i, htxt in enumerate(("Letter","Shelf","Lower","Upper")):
            ctk.CTkLabel(hdr, text=htxt, font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=i, padx=10, sticky="w")

        # Load shelves list
        shelves = [r[0] for r in db_fetchall("SELECT name FROM shelves ORDER BY name")]
        shelf_values = [""] + shelves

        self.letter_widgets = {}
        letters = [chr(i) for i in range(65, 91)] + ["Overflow"]
        # Ensure entries exist for each letter in DB
        for L in letters:
            if not db_fetchone("SELECT 1 FROM letter_sections WHERE letter=?", (L,)):
                db_exec("INSERT INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                        (L, "", "", ""))

        for i, L in enumerate(letters, start=1):
            rowf = ctk.CTkFrame(inner); rowf.grid(row=i, column=0, sticky="ew", padx=6, pady=3)
            ctk.CTkLabel(rowf, text=L, width=70, anchor="w", font=ctk.CTkFont(size=17)).grid(row=0, column=0, padx=6)
            s_cb = ctk.CTkComboBox(rowf, values=shelf_values, width=120)
            lo_e = ctk.CTkEntry(rowf, width=120)
            up_e = ctk.CTkEntry(rowf, width=120)
            s_cb.grid(row=0, column=1, padx=6); lo_e.grid(row=0, column=2, padx=6); up_e.grid(row=0, column=3, padx=6)

            r = db_fetchone("SELECT shelf,lower_bound,upper_bound FROM letter_sections WHERE letter=?", (L,))
            if r:
                s_cb.set(r[0] or ""); 
                if r[1]: lo_e.insert(0, r[1])
                if r[2]: up_e.insert(0, r[2])

            self.letter_widgets[L] = (s_cb, lo_e, up_e)

        def save_all():
            for L,(s, lo, up) in self.letter_widgets.items():
                S, LO, UP = (s.get() or "").strip().upper(), lo.get().strip().upper(), up.get().strip().upper()
                if S and not db_fetchone("SELECT 1 FROM shelves WHERE name=?", (S,)):
                    messagebox.showerror("Error", f"Shelf '{S}' does not exist for letter {L}."); return
                if LO and not parse_bound(LO):
                    messagebox.showerror("Error", f"Lower bound invalid for {L}: {LO}"); return
                if UP and not parse_bound(UP):
                    messagebox.showerror("Error", f"Upper bound invalid for {L}: {UP}"); return
                db_exec("INSERT OR REPLACE INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                        (L, S, LO, UP))
            log_action("Updated letter sections")
            win.destroy()

        ctk.CTkButton(left, text="💾 Save All & Close", fg_color="#0B5CAB", hover_color="#084b8a",
                      command=save_all).pack(padx=8, pady=8, anchor="e")

        # Right: Shelf settings (add/edit/delete)
        right = ctk.CTkFrame(container)
        right.grid(row=0, column=1, sticky="nsew", padx=(5,0))
        ctk.CTkLabel(right, text="Shelf Settings", font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=10, pady=6)

        tablef = ctk.CTkFrame(right); tablef.pack(fill="both", expand=True, padx=8, pady=6)
        tv = ttk.Treeview(tablef, columns=("Name","Rows","Cols","Used%"), show="headings", height=14)
        for c in ("Name","Rows","Cols","Used%"):
            tv.heading(c, text=c)
            tv.column(c, width=120 if c!="Name" else 160, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)

        def refresh_shelves_tv():
            for r in tv.get_children(): tv.delete(r)
            rows = db_fetchall("SELECT name, rows_count, cols_count FROM shelves ORDER BY name")
            for name, rc, cc in rows:
                used = db_fetchone("SELECT COUNT(*) FROM slots WHERE shelf=? AND occupied=1", (name,))[0]
                total = db_fetchone("SELECT COUNT(*) FROM slots WHERE shelf=?", (name,))[0]
                pct = f"{int(100*used/total)}%" if total else "0%"
                tv.insert("", "end", iid=name, values=(name, rc or 0, cc or 0, pct))
        refresh_shelves_tv()

        # Add / Edit / Delete forms
        form = ctk.CTkFrame(right); form.pack(fill="x", padx=8, pady=6)
        name_e = ctk.CTkEntry(form, placeholder_text="Shelf name (e.g., F)", width=120)
        rows_e = ctk.CTkEntry(form, placeholder_text="Rows (e.g., 26)", width=120)
        cols_e = ctk.CTkEntry(form, placeholder_text="Cols (e.g., 100)", width=120)
        name_e.grid(row=0, column=0, padx=6, pady=6)
        rows_e.grid(row=0, column=1, padx=6, pady=6)
        cols_e.grid(row=0, column=2, padx=6, pady=6)

        def add_shelf():
            name = (name_e.get() or "").strip().upper()
            try:
                rows_count = int(rows_e.get().strip())
                cols_count = int(cols_e.get().strip())
            except:
                messagebox.showerror("Error","Rows/Cols must be integers."); return
            if not name:
                messagebox.showerror("Error","Shelf name required."); return
            if db_fetchone("SELECT 1 FROM shelves WHERE name=?", (name,)):
                messagebox.showerror("Error","Shelf already exists."); return
            db_exec("INSERT INTO shelves(name,rows_count,cols_count) VALUES(?,?,?)",
                    (name, rows_count, cols_count))
            populate_slots_for_shelf(name, rows_count, cols_count)
            log_action(f"Added shelf {name} ({rows_count} rows, {cols_count} cols)")
            refresh_shelves_tv()

        def edit_shelf():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("Select","Choose a shelf to edit."); return
            name = sel[0]
            try:
                rows_count = int(rows_e.get().strip())
                cols_count = int(cols_e.get().strip())
            except:
                messagebox.showerror("Error","Rows/Cols must be integers."); return
            # For simplicity: update metadata; do not shrink slots already existing
            db_exec("UPDATE shelves SET rows_count=?, cols_count=? WHERE name=?",
                    (rows_count, cols_count, name))
            # Only populate missing slots (no delete)
            populate_slots_for_shelf(name, rows_count, cols_count)
            log_action(f"Edited shelf {name} -> rows={rows_count}, cols={cols_count}")
            refresh_shelves_tv()

        def delete_shelf():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("Select","Choose a shelf to delete."); return
            name = sel[0]
            occ = db_fetchone("SELECT COUNT(*) FROM slots WHERE shelf=? AND occupied=1", (name,))[0]
            if occ > 0:
                messagebox.showerror("Blocked", "Shelf has occupied slots. Move/clear first."); return
            # Also ensure no letter sections reference this shelf
            refs = db_fetchone("SELECT COUNT(*) FROM letter_sections WHERE shelf=?", (name,))[0]
            if refs > 0:
                messagebox.showerror("Blocked", "Letter sections reference this shelf. Clear them first."); return
            if not messagebox.askyesno("Confirm", f"Delete shelf {name}? This deletes its slots."):
                return
            db_exec("DELETE FROM slots WHERE shelf=?", (name,))
            db_exec("DELETE FROM shelves WHERE name=?", (name,))
            log_action(f"Deleted shelf {name}")
            refresh_shelves_tv()

        ctk.CTkButton(right, text="➕ Add Shelf", fg_color="#2F8B2F", hover_color="#277327",
                      command=add_shelf).pack(padx=8, pady=4, anchor="w")
        ctk.CTkButton(right, text="✏️ Edit Shelf", command=edit_shelf).pack(padx=8, pady=4, anchor="w")
        ctk.CTkButton(right, text="🗑️ Delete Shelf", fg_color="#D83B01", hover_color="#B32F00",
                      command=delete_shelf).pack(padx=8, pady=4, anchor="w")

    # ---------- Overdue Tab ----------
    def open_overdue_tab(self):
        # Ask threshold
        win = ctk.CTkToplevel(self); win.title("Overdue Medications")
        win.geometry("1120x780"); win.lift(); win.attributes("-topmost", True)
        make_topmost(win)

        head = ctk.CTkFrame(win, fg_color="#c50f1f"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Overdue Medications", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        top = ctk.CTkFrame(win); top.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(top, text="Show ≥ days:", font=ctk.CTkFont(size=17)).pack(side="left", padx=6)
        days_var = ctk.StringVar(value="14")
        d_entry = ctk.CTkEntry(top, textvariable=days_var, width=80); d_entry.pack(side="left", padx=6)
        def refresh_list():
            try:
                th = int(days_var.get().strip())
            except:
                th = 14
            data = get_overdue_prescriptions(th)
            agg = aggregate_overdue_by_patient(data)
            for r in tv.get_children(): tv.delete(r)
            for i,(pid, v) in enumerate(agg.items()):
                locs = ", ".join(slot_ids_to_labels(sorted(list(v["slots"]))))
                tag = "odd" if i%2 else "even"
                tv.insert("", "end", iid=str(pid),
                          values=(v["name"], v["address"] or "", v["count"], v["oldest"], locs),
                          tags=(tag,))
            cnt.configure(text=f"Total overdue prescriptions: {len(data)}")
            # store current data on win for light-up
            win._overdue_data = data

        ctk.CTkButton(top, text="Apply", command=refresh_list).pack(side="left", padx=6)
        ctk.CTkButton(top, text="🔴 Light Up Overdue",
                      command=lambda: self._light_up_overdue(win)).pack(side="left", padx=8)
        cnt = ctk.CTkLabel(top, text="Total overdue prescriptions: 0", font=ctk.CTkFont(size=17))
        cnt.pack(side="right", padx=6)

        tablef = ctk.CTkFrame(win); tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Patient","Address","# Overdue","Oldest (days)","Locations")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=220 if c!="Locations" else 380, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        tv.tag_configure("odd", background="#f5f9ff")
        tv.tag_configure("even", background="#ffffff")
        tv.bind("<Double-1>", lambda e: self._open_overdue_patient_detail(tv))

        # initial load
        refresh_list()

    def _light_up_overdue(self, parent_win):
        data = getattr(parent_win, "_overdue_data", None)
        if not data:
            messagebox.showinfo("LED","No overdue data loaded."); return
        slots = sorted(list({it["slot_id"] for it in data if it["slot_id"]}))
        if not slots:
            messagebox.showinfo("LED","No overdue items with assigned slots."); return
        open_led_popup(parent_win, slots, "red", "Overdue — LED")

    def _open_overdue_patient_detail(self, tree):
        sel = tree.selection()
        if not sel: return
        pid = int(sel[0])
        items = [o for o in get_overdue_prescriptions(0) if o["patient_id"] == pid]
        if not items:
            messagebox.showinfo("Overdue","No overdue items (refresh?)"); return
        win = ctk.CTkToplevel(self); win.title("Overdue Details"); win.geometry("900x520")
        make_topmost(win)
        win.lift(); win.attributes("-topmost", True)
        head = ctk.CTkFrame(win, fg_color="#c50f1f"); head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Overdue — {items[0]['name']}", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        top = ctk.CTkFrame(win); top.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(top, text="🔴 Light Up These",
                      command=lambda: open_led_popup(win, sorted(list({it['slot_id'] for it in items if it['slot_id']})),
                                                    "red", "Overdue — Selected")
                      ).pack(side="left")
        tablef = ctk.CTkFrame(win); tablef.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("Medication","Quantity","Days Overdue","Location")
        tv = ttk.Treeview(tablef, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=220 if c!="Location" else 260, anchor="w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tablef, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscroll=sb.set)
        for it in items:
            loc = slot_id_to_label(it["slot_id"]) if it["slot_id"] else ""
            tv.insert("", "end", values=(it["medication"], it["quantity"], it["days_overdue"], loc))

    # ---------- Dashboard ----------
    def open_dashboard_tab(self):
        win = ctk.CTkToplevel(self); win.title("Dashboard")
        win.geometry("1180x860"); win.lift(); win.attributes("-topmost", True)
        make_topmost(win)
        head = ctk.CTkFrame(win, fg_color="#0b5cab"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Dashboard", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)

        kpi = ctk.CTkFrame(win); kpi.pack(fill="x", padx=12, pady=8)
        def get_counts():
            total_pat = db_fetchone("SELECT COUNT(*) FROM patients")[0]
            total_rx  = db_fetchone("SELECT COUNT(*) FROM prescriptions")[0]
            overdue   = len(get_overdue_prescriptions(14))
            wrong     = 0
            rows = db_fetchall("SELECT id,patient_id FROM prescriptions WHERE slot_id IS NOT NULL")
            for rid, pid in rows:
                sid = db_fetchone("SELECT slot_id FROM prescriptions WHERE id=?", (rid,))[0]
                if sid and not is_slot_in_letter_section(get_patient_letter(pid), sid):
                    wrong += 1
            return total_pat, total_rx, overdue, wrong

        p, r, o, w = get_counts()
        ctk.CTkLabel(kpi, text=f"Patients: {p}", font=ctk.CTkFont(size=17)).pack(side="left", padx=12)
        ctk.CTkLabel(kpi, text=f"Prescriptions: {r}", font=ctk.CTkFont(size=17)).pack(side="left", padx=12)
        ctk.CTkLabel(kpi, text=f"Overdue (≥14d): {o}", font=ctk.CTkFont(size=17)).pack(side="left", padx=12)
        ctk.CTkLabel(kpi, text=f"Wrong-section: {w}", font=ctk.CTkFont(size=17)).pack(side="left", padx=12)

        # Charts placeholder (no heavy matplotlib rendering for now)
        charts = ctk.CTkFrame(win); charts.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(charts, text="(Charts would render here — trend of adds, basket split, shelf usage, top meds)",
                     font=ctk.CTkFont(size=17)).pack(pady=12)

    # ---------- Previous Actions ----------
    def open_actions_tab(self):
        win = ctk.CTkToplevel(self); win.title("Previous Actions")
        win.geometry("1020x660"); win.lift(); win.attributes("-topmost", True)
        make_topmost(win)
        head = ctk.CTkFrame(win, fg_color="#6b6b6b"); head.pack(fill="x")
        ctk.CTkLabel(head, text="Previous Actions (Today)", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=12, pady=8)
        tablef = ctk.CTkFrame(win); tablef.pack(fill="both", expand=True, padx=10, pady=10)
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
        ctk.CTkButton(win, text="🔄 Refresh",
                      command=lambda: self._refresh_actions(tv)).pack(pady=8)

    def _refresh_actions(self, tv):
        for r in tv.get_children(): tv.delete(r)
        acts = get_todays_actions(300)
        for i,(ts,actor,action) in enumerate(acts):
            t = ts.split(" ")[1] if " " in ts else ts
            tag = "odd" if i%2 else "even"
            tv.insert("", "end", values=(t,actor,action), tags=(tag,))
# ================================
# Pharmacy LED System - Part 6
# Boot
# ================================

def seed_letter_sections_if_missing():
    # Ensure A..Z + Overflow exist
    letters = [chr(i) for i in range(65,91)] + ["Overflow"]
    for L in letters:
        if not db_fetchone("SELECT 1 FROM letter_sections WHERE letter=?", (L,)):
            db_exec("INSERT INTO letter_sections(letter,shelf,lower_bound,upper_bound) VALUES(?,?,?,?)",
                    (L, "", "", ""))

if __name__ == "__main__":
    print("Database path:", DB_PATH)
    init_db()
    init_actions_table()
    seed_letter_sections_if_missing()
    # Do not auto-populate slots; user will add/edit shelves and slots will populate accordingly
    app = App()
    app.mainloop()
