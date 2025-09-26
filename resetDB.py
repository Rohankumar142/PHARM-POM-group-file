# reset_db.py
import os, sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pharmacy.db")

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print("Deleted old pharmacy.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Patients table
cur.execute("""
CREATE TABLE patients(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    created_at TEXT
)
""")

# Prescriptions table
cur.execute("""
CREATE TABLE prescriptions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INT NOT NULL,
    medication TEXT NOT NULL,
    quantity INT,
    date_added TEXT,
    basket_size TEXT CHECK (basket_size IN ('small','large')),
    slot_id INT
)
""")

# Shelves table (flexible rows/cols per shelf)
cur.execute("""
CREATE TABLE shelves(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    rows_count INT,
    cols_count INT
)
""")

# Slots table (auto-populated when shelves are added)
cur.execute("""
CREATE TABLE slots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shelf TEXT,
    row TEXT,
    col INT,
    occupied INT DEFAULT 0
)
""")

# Letter sections table
cur.execute("""
CREATE TABLE letter_sections(
    letter TEXT PRIMARY KEY,
    shelf TEXT,
    lower_bound TEXT,
    upper_bound TEXT
)
""")

# Actions log
cur.execute("""
CREATE TABLE actions_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    actor TEXT,
    action TEXT
)
""")

conn.commit()
conn.close()
print("✅ New pharmacy.db created with fresh schema")
