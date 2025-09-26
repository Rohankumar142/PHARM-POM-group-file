# DATABASE+UI.py — updated with search, shelf Save All, better auto-assign, human slot labels, LED control
import os
import sqlite3
import threading
import time
from datetime import datetime
from tkinter import *
from tkinter import ttk, messagebox

# -------------------------
# Persistent DB path (next to this script)
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "pharmacy.db")

# -------------------------
# Utility: safe DB helpers
# -------------------------
def db_connect():
    return sqlite3.connect(DB, timeout=5)

def db_exec(sql, params=(), commit=True):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if commit:
            conn.commit()
        return cur

def db_fetchall(sql, params=()):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

def db_fetchone(sql, params=()):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

# -------------------------
# LED simulation (pause/resume + stop)
# -------------------------
_blink_controllers = {}  # key: group_key (int), value: {"stop": Event, "pause": Event, "thread": Thread, "slots": [ids], "color": str}

def _blink_worker(group_key):
    ctrl = _blink_controllers[group_key]
    stop_ev = ctrl["stop"]
    pause_ev = ctrl["pause"]
    slots = ctrl["slots"]
    color = ctrl["color"]
    while not stop_ev.is_set():
        if not pause_ev.is_set():
            print(f"[LED] ON slots {slots} color={color}")
            time.sleep(0.5)
            print(f"[LED] OFF slots {slots}")
            time.sleep(0.5)
        else:
            time.sleep(0.2)

def start_blink(slots, color="yellow"):
    if not slots:
        return None
    group_key = int(slots[0])  # group by first id
    stop_blink(slots)  # stop if already blinking
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    th = threading.Thread(target=_blink_worker, args=(group_key,), daemon=True)
    _blink_controllers[group_key] = {"stop": stop_ev, "pause": pause_ev, "thread": th, "slots": list(slots), "color": color}
    th.start()
    return group_key

def toggle_pause(group_key):
    ctrl = _blink_controllers.get(group_key)
    if not ctrl:
        return
    pause_ev = ctrl["pause"]
    if pause_ev.is_set():
        pause_ev.clear()
        print("[LED] Resume blinking")
    else:
        pause_ev.set()
        print("[LED] Pause blinking")

def stop_blink(slots_or_group):
    if isinstance(slots_or_group, int):
        ctrl = _blink_controllers.pop(slots_or_group, None)
        if ctrl:
            ctrl["stop"].set()
            print(f"[LED] stop blink group {slots_or_group}")
        return
    if not slots_or_group:
        return
    group_key = int(slots_or_group[0])
    ctrl = _blink_controllers.pop(group_key, None)
    if ctrl:
        ctrl["stop"].set()
        print(f"[LED] stop blink slots {slots_or_group}")

def open_led_control_window(parent, slot_ids, color="yellow"):
    group_key = start_blink(slot_ids, color=color)
    win = Toplevel(parent)
    win.title("LED Control")
    Label(win, text=f"Blinking slots {slot_ids} ({color})").pack(padx=8, pady=8)

    paused = {"val": False}
    def on_toggle():
        toggle_pause(group_key)
        paused["val"] = not paused["val"]
        btn_pause.config(text=("Resume Lights" if paused["val"] else "Pause Lights"))

    def on_done():
        stop_blink(group_key)
        win.destroy()

    btn_pause = Button(win, text="Pause Lights", command=on_toggle)
    btn_pause.pack(side=LEFT, padx=8, pady=8)
    Button(win, text="Done", command=on_done).pack(side=RIGHT, padx=8, pady=8)

# -------------------------
# DB initialization (no reset on startup)
# -------------------------
def init_db(reset=False):
    with db_connect() as conn:
        cur = conn.cursor()
        if reset:
            cur.execute("DROP TABLE IF EXISTS prescriptions")
            cur.execute("DROP TABLE IF EXISTS patients")
            cur.execute("DROP TABLE IF EXISTS letter_sections")
            cur.execute("DROP TABLE IF EXISTS slots")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                medication TEXT NOT NULL,
                quantity INTEGER,
                date_added TEXT,
                basket_size TEXT CHECK (basket_size IN ('small','large')),
                slot_id INTEGER,
                FOREIGN KEY(patient_id) REFERENCES patients(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS letter_sections (
                letter TEXT PRIMARY KEY,
                shelf TEXT,
                lower_bound TEXT,
                upper_bound TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shelf TEXT,
                row TEXT,
                col INTEGER,
                occupied INTEGER DEFAULT 0
            )
        """)
        conn.commit()

    existing = db_fetchall("SELECT letter FROM letter_sections")
    if not existing:
        letters = [chr(i) for i in range(ord('A'), ord('Z')+1)] + ["Overflow"]
        for L in letters:
            db_exec("INSERT OR IGNORE INTO letter_sections (letter, shelf, lower_bound, upper_bound) VALUES (?,?,?,?)",
                    (L, "", "", ""))

def populate_slots():
    cnt = db_fetchone("SELECT COUNT(*) FROM slots")
    if cnt and cnt[0] > 0:
        return
    shelves = ["F","L","R","O"]  # 'O' exists in DB but we won't auto-use it unless section is full
    with db_connect() as conn:
        cur = conn.cursor()
        for shelf in shelves:
            for i in range(26):
                row = chr(ord('A') + i)
                for col in range(1, 101):
                    cur.execute("INSERT INTO slots (shelf,row,col,occupied) VALUES (?,?,?,0)", (shelf, row, col))
        conn.commit()
    print("Slots populated.")

# -------------------------
# Slot/section helpers
# -------------------------
def parse_bound(b):
    if not b:
        return None
    b = b.strip().upper()
    if len(b) < 2:
        return None
    row = b[0]
    try:
        col = int(b[1:])
    except:
        return None
    if row < 'A' or row > 'Z' or col < 1 or col > 100:
        return None
    return row, col

def rows_range(start_row, end_row):
    return [chr(i) for i in range(ord(start_row), ord(end_row)+1)]

def get_letter_section(letter):
    r = db_fetchone("SELECT shelf, lower_bound, upper_bound FROM letter_sections WHERE letter=?", (letter,))
    if not r:
        return None
    shelf, lower, upper = r
    pl = parse_bound(lower) if lower else None
    pu = parse_bound(upper) if upper else None
    if not pl or not pu or not shelf:
        return None
    sr, sc = pl
    er, ec = pu
    return shelf, sr, sc, er, ec

def find_next_available_slot_in_section(shelf, sr, sc, er, ec, basket_size):
    with db_connect() as conn:
        cur = conn.cursor()
        for row in rows_range(sr, er):
            for col in range(sc, ec+1):
                cur.execute("SELECT id, occupied FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))
                r = cur.fetchone()
                if not r: 
                    continue
                sid, occ = r
                if occ == 0:
                    if basket_size == "large":
                        cur.execute("SELECT id, occupied FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col+1))
                        r2 = cur.fetchone()
                        if r2 and r2[1] == 0:
                            return [sid, r2[0]], shelf
                    else:
                        return [sid], shelf
    return None

def find_next_available_slot_with_overflow(letter, basket_size):
    # 1) Try the letter section. If not configured, return None (force manual assignment).
    sec = get_letter_section(letter)
    if sec:
        shelf, sr, sc, er, ec = sec
        res = find_next_available_slot_in_section(shelf, sr, sc, er, ec, basket_size)
        if res:
            return res
        # 2) Optional overflow ONLY if section exists but is full.
        #    If you don't want overflow at all, comment out the block below.
        with db_connect() as conn:
            cur = conn.cursor()
            for i in range(26):
                row = chr(ord('A') + i)
                for col in range(1, 101):
                    cur.execute("SELECT id, occupied FROM slots WHERE shelf='O' AND row=? AND col=?", (row, col))
                    r = cur.fetchone()
                    if not r:
                        continue
                    sid, occ = r
                    if occ == 0:
                        if basket_size == "large":
                            cur.execute("SELECT id, occupied FROM slots WHERE shelf='O' AND row=? AND col=?", (row, col+1))
                            r2 = cur.fetchone()
                            if r2 and r2[1] == 0:
                                return ([sid, r2[0]], "O")
                        else:
                            return ([sid], "O")
    # If no section configured at all → caller should prompt manual assignment.
    return None

def mark_slots_occupied(sids):
    with db_connect() as conn:
        cur = conn.cursor()
        for s in sids:
            cur.execute("UPDATE slots SET occupied=1 WHERE id=?", (s,))
        conn.commit()

def mark_slots_free(sids):
    with db_connect() as conn:
        cur = conn.cursor()
        for s in sids:
            cur.execute("UPDATE slots SET occupied=0 WHERE id=?", (s,))
        conn.commit()

def get_slot_by_position(shelf, row, col):
    return db_fetchone("SELECT id, occupied FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))

def slot_id_to_label(slot_id):
    r = db_fetchone("SELECT shelf, row, col FROM slots WHERE id=?", (slot_id,))
    if not r:
        return ""
    shelf, row, col = r
    return f"{shelf}-{row}{col}"

def slot_ids_to_label_list(slot_ids):
    labels = []
    for sid in slot_ids:
        lbl = slot_id_to_label(sid)
        if lbl:
            labels.append(lbl)
    return labels

def parse_location_label_to_slot_id(text):
    """
    Accepts 'F-A61', 'F A61', 'F-A-61' (we'll normalize), or a raw numeric slot id.
    Returns (slot_id) or None if invalid.
    """
    if not text:
        return None
    s = text.strip().upper().replace(" ", "").replace("--", "-")
    # If numeric, treat as direct slot id
    if s.isdigit():
        try:
            sid = int(s)
            ok = db_fetchone("SELECT id FROM slots WHERE id=?", (sid,))
            return sid if ok else None
        except:
            return None
    # Normalize common patterns: 'F-A61' -> shelf='F', row='A', col=61
    if "-" in s:
        parts = s.split("-")
        # 'F-A61' or 'F','A61'
        if len(parts) == 2:
            shelf = parts[0]
            rowcol = parts[1]
            if len(shelf) == 1 and len(rowcol) >= 2:
                row = rowcol[0]
                try:
                    col = int(rowcol[1:])
                except:
                    return None
                rec = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))
                return rec[0] if rec else None
        # 'F-A-61'
        if len(parts) == 3:
            shelf, row, coltxt = parts
            if len(shelf) == 1 and len(row) == 1:
                try:
                    col = int(coltxt)
                except:
                    return None
                rec = db_fetchone("SELECT id FROM slots WHERE shelf=? AND row=? AND col=?", (shelf, row, col))
                return rec[0] if rec else None
    return None

# -------------------------
# Family lookup
# -------------------------
def get_family_existing_slot(address):
    if not address:
        return None
    r = db_fetchone("""
        SELECT p.id, pr.slot_id
        FROM patients p
        JOIN prescriptions pr ON p.id = pr.patient_id
        WHERE p.address = ? AND pr.slot_id IS NOT NULL
        LIMIT 1
    """, (address,))
    return r  # (patient_id, slot_id) or None

# -------------------------
# UI: main app
# -------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Pharmacy LED System")
        root.geometry("1200x700")

        # Sidebar (right)
        side = Frame(root, width=280, bg="#f0f0f0")
        side.pack(side=RIGHT, fill=Y)
        Label(side, text="Shelf Assignment", font=("Arial", 12, "bold"), bg="#f0f0f0").pack(pady=10)
        Button(side, text="Open Shelf Assignment", command=self.open_shelf_assignment).pack(pady=6, padx=8, fill=X)

        # Top controls
        top = Frame(root)
        top.pack(side=TOP, fill=X, padx=6, pady=6)
        Label(top, text="Search:").pack(side=LEFT)
        self.search_var = StringVar()
        ent = Entry(top, textvariable=self.search_var)
        ent.pack(side=LEFT, padx=6)
        ent.bind("<Return>", lambda e: self.refresh_patient_table())
        Button(top, text="Refresh", command=self.refresh_patient_table).pack(side=LEFT, padx=6)
        Button(top, text="Add Patient", command=self.add_patient_popup).pack(side=RIGHT, padx=6)
        Button(top, text="Delete Patient", command=self.delete_selected_patient).pack(side=RIGHT, padx=6)

        # Patient table
        self.pt = ttk.Treeview(root, columns=("Name", "Address", "Date Added", "Location"), show="headings")
        self.pt.heading("Name", text="Name")
        self.pt.heading("Address", text="Address")
        self.pt.heading("Date Added", text="Date Added")
        self.pt.heading("Location", text="LED Location")
        self.pt.pack(fill=BOTH, expand=True, padx=6, pady=6)
        self.pt.bind("<Double-1>", self.on_patient_double)

        self.refresh_patient_table()

    def refresh_patient_table(self):
        for r in self.pt.get_children():
            self.pt.delete(r)
        q = self.search_var.get().strip()
        params = ()
        sql = "SELECT id, name, address, created_at FROM patients"
        if q:
            sql += " WHERE name LIKE ? OR address LIKE ?"
            wildcard = f"%{q}%"
            params = (wildcard, wildcard)
        sql += " ORDER BY name"
        rows = db_fetchall(sql, params)
        for pid, name, address, created_at in rows:
            date_display = ""
            if created_at:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(created_at, fmt)
                        date_display = dt.strftime("%m/%d/%Y")
                        break
                    except:
                        pass
            s = db_fetchone("SELECT slot_id FROM prescriptions WHERE patient_id=? AND slot_id IS NOT NULL LIMIT 1", (pid,))
            loc = slot_id_to_label(s[0]) if (s and s[0]) else ""
            self.pt.insert("", END, iid=str(pid), values=(name, address if address else "", date_display, loc))

    def add_patient_popup(self):
        p = Toplevel(self.root)
        p.title("Add Patient")
        Label(p, text="Name").grid(row=0, column=0, padx=8, pady=6)
        name_e = Entry(p, width=40); name_e.grid(row=0, column=1, padx=8, pady=6)
        Label(p, text="Address").grid(row=1, column=0, padx=8, pady=6)
        addr_e = Entry(p, width=40); addr_e.grid(row=1, column=1, padx=8, pady=6)
        def save():
            name = name_e.get().strip()
            addr = addr_e.get().strip()
            if not name:
                messagebox.showerror("Error","Name required")
                return
            db_exec("INSERT INTO patients (name,address,created_at) VALUES (?,?,?)",
                    (name, addr, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            p.destroy()
            self.refresh_patient_table()
        Button(p, text="Save", command=save).grid(row=2, column=0, columnspan=2, pady=8)

    def delete_selected_patient(self):
        sel = self.pt.selection()
        if not sel:
            return
        pid = int(sel[0])
        if not messagebox.askyesno("Confirm delete","Delete patient and all prescriptions?"):
            return
        rows = db_fetchall("SELECT slot_id FROM prescriptions WHERE patient_id=?", (pid,))
        sids = [r[0] for r in rows if r[0]]
        if sids:
            mark_slots_free(sids)
        db_exec("DELETE FROM prescriptions WHERE patient_id=?", (pid,))
        db_exec("DELETE FROM patients WHERE id=?", (pid,))
        self.refresh_patient_table()

    def on_patient_double(self, event):
        sel = self.pt.selection()
        if not sel:
            return
        pid = int(sel[0])
        self.open_patient_popup(pid)

    # -------------------------
    # Patient popup (edit + prescriptions excel-style)
    # -------------------------
    def open_patient_popup(self, pid):
        p = Toplevel(self.root)
        p.title("Patient - Edit & Prescriptions")
        p.geometry("980x660")

        topf = Frame(p)
        topf.pack(fill=X, padx=8, pady=6)
        Label(topf, text="Name:").grid(row=0, column=0, sticky=W)
        name_e = Entry(topf, width=40); name_e.grid(row=0, column=1, padx=6)
        Label(topf, text="Address:").grid(row=0, column=2, sticky=W)
        addr_e = Entry(topf, width=40); addr_e.grid(row=0, column=3, padx=6)

        row = db_fetchone("SELECT name,address FROM patients WHERE id=?", (pid,))
        if row:
            name_e.insert(0, row[0])
            addr_e.insert(0, row[1] if row[1] else "")

        def save_patient():
            db_exec("UPDATE patients SET name=?, address=? WHERE id=?", (name_e.get().strip(), addr_e.get().strip(), pid))
            self.refresh_patient_table()
            p.destroy()

        Button(topf, text="Save Patient", command=save_patient).grid(row=0, column=4, padx=6)

        # Prescriptions table
        pres_frame = Frame(p)
        pres_frame.pack(fill=BOTH, expand=True, padx=8, pady=6)
        pres_tree = ttk.Treeview(pres_frame, columns=("Medication","Quantity","Date Added","Basket Size","Location"), show="headings")
        for col in ("Medication","Quantity","Date Added","Basket Size","Location"):
            pres_tree.heading(col, text=col)
            pres_tree.column(col, width=170)
        pres_tree.pack(fill=BOTH, expand=True, side=LEFT)
        scrollbar = Scrollbar(pres_frame, orient=VERTICAL, command=pres_tree.yview)
        pres_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)

        def refresh_pres_tree():
            for r in pres_tree.get_children():
                pres_tree.delete(r)
            rows2 = db_fetchall("SELECT id, medication, quantity, date_added, basket_size, slot_id FROM prescriptions WHERE patient_id=? ORDER BY id", (pid,))
            for pres_id, med, qty, date_added, basket_size, slot_id in rows2:
                date_display = ""
                if date_added:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(date_added, fmt)
                            date_display = dt.strftime("%m/%d/%Y")
                            break
                        except:
                            pass
                loc = slot_id_to_label(slot_id) if slot_id else ""
                pres_tree.insert("", END, iid=str(pres_id), values=(med, qty if qty is not None else "", date_display, basket_size if basket_size else "", loc))

        refresh_pres_tree()

        # double click to edit prescription
        def on_pres_double(e):
            sel = pres_tree.selection()
            if not sel:
                return
            pres_id = int(sel[0])
            self.open_edit_prescription_popup(pid, pres_id, refresh_pres_tree)
        pres_tree.bind("<Double-1>", on_pres_double)

        # Add prescription (controls + button on the same row)
        addf = Frame(p)
        addf.pack(fill=X, padx=8, pady=6)
        Label(addf, text="Medication").grid(row=0, column=0, sticky=E)
        med_e = Entry(addf, width=30); med_e.grid(row=0, column=1, padx=6, sticky=W)
        Label(addf, text="Quantity").grid(row=0, column=2, sticky=E)
        qty_e = Entry(addf, width=8); qty_e.grid(row=0, column=3, padx=6, sticky=W)
        Label(addf, text="Basket Size").grid(row=0, column=4, sticky=E)
        basket_cb = ttk.Combobox(addf, values=["small","large"], width=8); basket_cb.grid(row=0, column=5, padx=6, sticky=W)
        basket_cb.set("small")
        Button(addf, text="Add Prescription", command=lambda: self.add_prescription_action(pid, addr_e, med_e, qty_e, basket_cb, p, refresh_pres_tree)).grid(row=0, column=6, padx=10, sticky=W)

    def add_prescription_action(self, pid, addr_entry, med_e, qty_e, basket_cb, parent_window, refresh_cb):
        med = med_e.get().strip()
        qty = qty_e.get().strip()
        basket = basket_cb.get().strip().lower()
        if not med or not qty or basket not in ("small","large"):
            messagebox.showerror("Error", "Enter medication, quantity, and basket size (small/large).")
            return
        db_exec("INSERT INTO prescriptions (patient_id, medication, quantity, date_added, basket_size) VALUES (?,?,?,?,?)",
                (pid, med, int(qty) if qty.isdigit() else qty, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), basket))
        pres_row = db_fetchone("SELECT id FROM prescriptions WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,))
        pres_id = pres_row[0] if pres_row else None

        # FAMILY MATCH
        address = addr_entry.get().strip()
        family = get_family_existing_slot(address) if address else None

        def auto_assign():
            fullname = db_fetchone("SELECT name FROM patients WHERE id=?", (pid,))[0] or ""
            last = fullname.strip().split()[-1] if fullname.strip() else ""
            letter = last[0].upper() if last else "A"
            res = find_next_available_slot_with_overflow(letter, basket)
            if not res:
                messagebox.showwarning("No slot available", "No automatic slot found or letter section not configured; please assign manually.")
                self.manual_assign_popup_for_new(parent_window, pres_id, after_assign=lambda slots: (refresh_cb(), open_led_control_window(parent_window, slots, color="yellow")))
                return
            slot_ids, shelf = res
            pos_labels = slot_ids_to_label_list(slot_ids)
            # Confirm window (no blinking yet)
            conf = Toplevel(parent_window); conf.title("Confirm Slot Assignment")
            just_positions = " & ".join([lbl.split('-')[1] for lbl in pos_labels])  # e.g., A1 & A2
            Label(conf, text=f"Proposed location on Shelf {shelf}: {just_positions}\n(Full: {', '.join(pos_labels)})").pack(padx=8, pady=8)

            def confirm_assign():
                mark_slots_occupied(slot_ids)
                db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_ids[0], pres_id))
                conf.destroy()
                refresh_cb()
                open_led_control_window(parent_window, slot_ids, color="yellow")

            def deny_assign():
                conf.destroy()
                self.manual_assign_popup_for_new(parent_window, pres_id, after_assign=lambda slots: (refresh_cb(), open_led_control_window(parent_window, slots, color="yellow")))

            Button(conf, text="Confirm", command=confirm_assign).pack(side=LEFT, padx=8, pady=8)
            Button(conf, text="Deny / Manual", command=deny_assign).pack(side=RIGHT, padx=8, pady=8)

        if family:
            fam_pid, fam_slot = family
            fp = Toplevel(parent_window); fp.title("Family Match")
            fam_label = slot_id_to_label(fam_slot)
            Label(fp, text=f"Patient at same address found (id {fam_pid}) at {fam_label}").pack(padx=8, pady=8)

            def bin_together():
                db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (fam_slot, pres_id))
                fp.destroy()
                refresh_cb()
                open_led_control_window(parent_window, [fam_slot], color="purple")

            def fam_cancel():
                fp.destroy()
                auto_assign()

            Button(fp, text="Bin Together", command=bin_together).pack(side=LEFT, padx=8, pady=8)
            Button(fp, text="Cancel", command=fam_cancel).pack(side=RIGHT, padx=8, pady=8)
        else:
            auto_assign()

    # Manual assignment for a new prescription, then optional LED control via callback
    def manual_assign_popup_for_new(self, parent_window, pres_id, after_assign=None):
        m = Toplevel(parent_window); m.title("Manual Slot Assignment")
        Label(m, text="Shelf (F/L/R/O)").grid(row=0, column=0)
        shelf_e = Entry(m); shelf_e.grid(row=0, column=1)
        Label(m, text="Row (A-Z)").grid(row=1, column=0)
        row_e = Entry(m); row_e.grid(row=1, column=1)
        Label(m, text="Column (1-100)").grid(row=2, column=0)
        col_e = Entry(m); col_e.grid(row=2, column=1)
        def assign():
            shelf = shelf_e.get().strip().upper()
            row = row_e.get().strip().upper()
            try:
                col = int(col_e.get().strip())
            except:
                messagebox.showerror("Error","Invalid column")
                return
            r = get_slot_by_position(shelf, row, col)
            if not r:
                messagebox.showerror("Error","Slot does not exist")
                return
            slot_id, occupied = r
            if occupied:
                messagebox.showerror("Error","Slot already occupied; choose another")
                return
            db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (slot_id, pres_id))
            db_exec("UPDATE slots SET occupied=1 WHERE id=?", (slot_id,))
            m.destroy()
            if after_assign:
                after_assign([slot_id])
        Button(m, text="Assign", command=assign).grid(row=3, column=0, columnspan=2, pady=8)

    # Edit prescription popup (accepts human label for LED location)
    def open_edit_prescription_popup(self, patient_id, pres_id, refresh_cb):
        e = Toplevel(self.root)
        e.title("Edit Prescription")
        e.geometry("460x320")
        row = db_fetchone("SELECT medication, quantity, date_added, basket_size, slot_id FROM prescriptions WHERE id=?", (pres_id,))
        if not row:
            e.destroy(); return
        med, qty, date_added, basket, slot_id = row
        Label(e, text="Medication:").grid(row=0, column=0, sticky=W, padx=8, pady=6)
        med_e = Entry(e, width=30); med_e.grid(row=0, column=1, padx=8, pady=6); med_e.insert(0, med)
        Label(e, text="Quantity:").grid(row=1, column=0, sticky=W, padx=8, pady=6)
        qty_e = Entry(e, width=10); qty_e.grid(row=1, column=1, padx=8, pady=6); qty_e.insert(0, str(qty) if qty is not None else "")
        Label(e, text="Date (MM/DD/YYYY):").grid(row=2, column=0, sticky=W, padx=8, pady=6)
        date_e = Entry(e, width=15); date_e.grid(row=2, column=1, padx=8, pady=6)
        if date_added:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(date_added, fmt)
                    date_e.insert(0, dt.strftime("%m/%d/%Y"))
                    break
                except:
                    pass
        Label(e, text="Basket Size:").grid(row=3, column=0, sticky=W, padx=8, pady=6)
        basket_cb = ttk.Combobox(e, values=["small","large"], width=10); basket_cb.grid(row=3, column=1, padx=8, pady=6)
        basket_cb.set(basket if basket in ("small","large") else "small")

        Label(e, text="LED Location (e.g., F-A61 or slot id):").grid(row=4, column=0, sticky=W, padx=8, pady=6)
        loc_default = slot_id_to_label(slot_id) if slot_id else ""
        slot_e = Entry(e, width=14); slot_e.grid(row=4, column=1, padx=8, pady=6)
        slot_e.insert(0, loc_default)

        def save_changes():
            # manual slot change (accept label or numeric)
            new_loc = slot_e.get().strip()
            new_slot_id = None
            if new_loc:
                cand = parse_location_label_to_slot_id(new_loc)
                if cand is None:
                    messagebox.showerror("Error", "Invalid LED location format. Use like F-A61 or a numeric slot id.")
                    return
                new_slot_id = cand

            if new_slot_id and new_slot_id != slot_id:
                if not messagebox.askyesno("Confirm manual slot change",
                                           "You are manually changing the LED slot. Continue?"):
                    return
                r = db_fetchone("SELECT occupied FROM slots WHERE id=?", (new_slot_id,))
                if not r:
                    messagebox.showerror("Error","Slot id does not exist")
                    return
                if r[0] == 1:
                    messagebox.showerror("Error","Slot already occupied")
                    return
                if slot_id:
                    mark_slots_free([slot_id])
                mark_slots_occupied([new_slot_id])
                db_exec("UPDATE prescriptions SET slot_id=? WHERE id=?", (new_slot_id, pres_id))

            # other fields
            new_med = med_e.get().strip()
            new_qty = qty_e.get().strip()
            new_basket = basket_cb.get().strip().lower()
            new_date_text = date_e.get().strip()
            if new_date_text:
                try:
                    dt = datetime.strptime(new_date_text, "%m/%d/%Y")
                    db_date = dt.strftime("%Y-%m-%d")
                except:
                    messagebox.showerror("Error","Date must be MM/DD/YYYY")
                    return
            else:
                db_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db_exec("UPDATE prescriptions SET medication=?, quantity=?, date_added=?, basket_size=? WHERE id=?",
                    (new_med, int(new_qty) if new_qty.isdigit() else new_qty, db_date, new_basket, pres_id))
            e.destroy()
            refresh_cb()

        Button(e, text="Save Changes", command=save_changes).grid(row=5, column=0, columnspan=2, pady=10)

    # -------------------------
    # Shelf assignment (scrollable + single Save All at sticky footer)
    # -------------------------
    def open_shelf_assignment(self):
        popup = Toplevel(self.root)
        popup.title("Shelf Assignment")
        popup.geometry("760x760")

        # Top instructions
        Label(popup, text="Assign letter ranges to shelves. Use bounds like A1 and B23.", font=("Arial", 10, "bold")).pack(anchor="w", padx=10, pady=6)

        # Scrollable area
        container = Frame(popup)
        container.pack(fill=BOTH, expand=True)
        canvas = Canvas(container)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        yscroll = Scrollbar(container, orient=VERTICAL, command=canvas.yview)
        yscroll.pack(side=RIGHT, fill=Y)
        canvas.configure(yscrollcommand=yscroll.set)
        inner = Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=inner, anchor="nw")

        header = ["Letter","Shelf (F/L/R)","Lower Bound (A1)","Upper Bound (D20)"]
        for col, h in enumerate(header):
            Label(inner, text=h, font=("Arial",10,"bold")).grid(row=0, column=col, padx=6, pady=4, sticky="w")

        letters = [chr(i) for i in range(ord('A'), ord('Z')+1)] + ["Overflow"]
        self.letter_widgets = {}

        for i, L in enumerate(letters, start=1):
            Label(inner, text=L).grid(row=i, column=0, padx=6, pady=3, sticky="w")
            shelf_cb = ttk.Combobox(inner, values=["F","L","R"], width=6)
            shelf_cb.grid(row=i, column=1, padx=6, sticky="w")
            lower_e = Entry(inner, width=12); lower_e.grid(row=i, column=2, padx=6, sticky="w")
            upper_e = Entry(inner, width=12); upper_e.grid(row=i, column=3, padx=6, sticky="w")

            r = db_fetchone("SELECT shelf, lower_bound, upper_bound FROM letter_sections WHERE letter=?", (L,))
            if r:
                shelf_cb.set(r[0] if r[0] else "")
                if r[1]: lower_e.insert(0, r[1])
                if r[2]: upper_e.insert(0, r[2])

            self.letter_widgets[L] = (shelf_cb, lower_e, upper_e)

        # Sticky footer with Save All
        footer = Frame(popup, bd=1, relief=RAISED)
        footer.pack(fill=X)
        def save_all():
            for L, widgets in self.letter_widgets.items():
                s_cb, lo_e, up_e = widgets
                s = s_cb.get().strip().upper()
                lo = lo_e.get().strip().upper()
                up = up_e.get().strip().upper()
                if s and s not in ("F","L","R"):
                    messagebox.showerror("Error", f"Invalid shelf for {L}")
                    return
                if lo and not parse_bound(lo):
                    messagebox.showerror("Error", f"Lower bound invalid for {L}: {lo}")
                    return
                if up and not parse_bound(up):
                    messagebox.showerror("Error", f"Upper bound invalid for {L}: {up}")
                    return
                db_exec("INSERT OR REPLACE INTO letter_sections (letter, shelf, lower_bound, upper_bound) VALUES (?,?,?,?)",
                        (L, s, lo, up))
            popup.destroy()
        Button(footer, text="Save All & Close", command=save_all).pack(pady=8)

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    print(f"Using database at: {DB}")
    init_db(reset=False)   # keep data between runs
    populate_slots()
    root = Tk()
    app = App(root)
    root.mainloop()
