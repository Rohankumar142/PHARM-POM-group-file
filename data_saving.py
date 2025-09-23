import tkinter as tk

root= tk.Tk()
root.title('Data Entry and Save')
root.geometry('500x500')

entry_data= tk.StringVar()
entry_box= tk.Entry(root, textvariable=entry_data, width=40, justify="left")
entry_box.pack(pady=10, anchor="w", padx=10)

def save_data():
    data_to_save= text=entry_data.get()
    data_to_save_box= tk.Label(root,text=entry_data.get(), justify='left',bg='white')
    data_to_save_box.pack(anchor='w',padx=10)
    if data_to_save:
        with open('saved_data.text','a') as f:
            f.write(data_to_save +"/n")
        entry_data.set('')
        print(data_to_save)
    else:
        print('No data to save')

save_button= tk.Button(root, text='Save Data', command=save_data,justify='left')
save_button.pack(pady=5, anchor='w', padx=10)

root.mainloop()