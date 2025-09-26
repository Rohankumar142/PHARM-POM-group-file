import sqlite3
import tkinter as tk
from tkinter import messagebox

# --- Database setup ---
conn = sqlite3.connect("pharmacy.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
)
""")
conn.commit()

# --- Functions ---
def add_patient():
    name = entry_name.get().strip()
    if name == "":
        messagebox.showwarning("Input Error", "Patient name cannot be empty.")
        return
    cursor.execute("INSERT INTO patients (name) VALUES (?)", (name,))
    conn.commit()
    entry_name.delete(0, tk.END)
    list_patients()

def list_patients():
    cursor.execute("SELECT id, name FROM patients")
    patients = cursor.fetchall()
    listbox_patients.delete(0, tk.END)
    for patient in patients:
        listbox_patients.insert(tk.END, f"{patient[0]}: {patient[1]}")

# --- Tkinter UI ---
root = tk.Tk()
root.title("Pharmacy Patient Manager (Proof of Concept)")

# Entry to add patient
frame_add = tk.Frame(root)
frame_add.pack(pady=10)

tk.Label(frame_add, text="Patient Name:").pack(side=tk.LEFT)
entry_name = tk.Entry(frame_add)
entry_name.pack(side=tk.LEFT, padx=5)
tk.Button(frame_add, text="Add Patient", command=add_patient).pack(side=tk.LEFT)

# Listbox to show patients
frame_list = tk.Frame(root)
frame_list.pack(pady=10)

listbox_patients = tk.Listbox(frame_list, width=40)
listbox_patients.pack()

# Initialize list
list_patients()

root.mainloop()

# Close database when done
conn.close()