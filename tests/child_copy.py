import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tkinter as tk
import we_mender_gui as G
root = tk.Tk(); root.withdraw(); G._resolve_fonts(root)
e = G.CopyEntry(root, "SURVIVE-R11-文本", bg="#191919", fg="#dddddd",
                font=G.FONTS["ui12"], width=10)
e._select_all()
e._copy()
root.update()
root.destroy()
