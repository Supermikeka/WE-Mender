# -*- coding: utf-8 -*-
"""CopyEntry（Canvas 自绘版）行为验收：不抢焦点 / 单击全选 / 拖选 / 复制 / 省略。"""
import os
import tempfile
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import we_mender_gui as G  # noqa: E402

OUT = tempfile.mkdtemp(prefix="wem_uicopy_")
fails = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(extra) if extra else ""))
    if not cond:
        fails.append(name)


root = tk.Tk()
G._resolve_fonts(root)
root.geometry("520x120+120+120")
root.configure(bg=G.T["CARD"])
root.attributes("-topmost", True)

row = tk.Frame(root, bg=G.T["CARD"])
row.pack(fill="x", padx=8, pady=10)
NAME = "示例壁纸：夜色城市延时（4K 循环动画）"
ID = "3000092946"
PATH = r"C:\SteamLibrary\steamapps\workshop\content\431960\3000092946"

e1 = G.CopyEntry(row, NAME, bg=G.T["CARD"], fg=G.T["BODY"],
                 font=G.FONTS["ui13"], width=1)
e1.pack(fill="x", pady=2)
e2 = G.CopyEntry(row, ID, bg=G.T["CARD"], fg=G.T["FAINT"],
                 font=G.FONTS["latin12"], width=10)
e2.pack(anchor="w")
e3 = G.CopyEntry(row, PATH, bg=G.T["CARD"], fg=G.T["FAINT"],
                 font=G.FONTS["latin12"], width=1)
e3.pack(fill="x", pady=2)
root.update()
root.focus_force()
root.update()

print("==== 1. 不抢焦点 ====")
before = root.focus_get()
e1.event_generate("<Button-1>", x=10, y=5)
e1.event_generate("<ButtonRelease-1>", x=10, y=5)
root.update()
check("单击后焦点仍在原控件", root.focus_get() is before, root.focus_get())
check("控件自身从未获得焦点", root.focus_get() is not e1)

print("\n==== 2. 单击 = 全选 ====")
check("单击后视为已选中", e1.selection_present() is True)
e1._copy()
check("复制的是全文", root.clipboard_get() == NAME, root.clipboard_get())

print("\n==== 3. 拖选一段 ====")
e2.clear_selection()
e2.event_generate("<Button-1>", x=2, y=5)
e2.event_generate("<B1-Motion>", x=40, y=5)
e2.event_generate("<ButtonRelease-1>", x=40, y=5)
root.update()
check("拖动后仍是选中态", e2.selection_present() is True)
e2._copy()
got = root.clipboard_get()
check("复制的是片段而非全文", got != ID and got and ID.startswith(got), (got, ID))

print("\n==== 4. 点别处取消选中 ====")
root.event_generate("<Button-1>", x=500, y=110)
root.update()
app_stub = None
for en in (e1, e2, e3):
    en.clear_selection()
check("清空后不再算选中", not e1.selection_present() and not e2.selection_present())

print("\n==== 5. 超宽 → 中间省略 + 复制仍是全文 ====")
long_path = PATH + r"\scenes\0123456789abcdef\preview\very_long_folder_name_here"
e3.set_text(long_path)
root.update()
check("显示被省略", e3._elided is True, e3._disp)
check("省略后仍保住结尾 ID", e3._disp.endswith(long_path[-8:]), e3._disp)
e3.event_generate("<Button-1>", x=6, y=5)
root.update()
check("省略时单击仍可全选", e3.selection_present() is True)
e3._copy()
check("省略时复制全文（不是半截）", root.clipboard_get() == long_path,
      root.clipboard_get())

print("\n==== 6. 尺寸合理（不撑破父容器）====")
root.update_idletasks()
check("高度接近字体行高", 14 <= e1.winfo_height() <= 24, e1.winfo_height())
check("宽度不超过父容器", e1.winfo_width() <= row.winfo_width(), 
      (e1.winfo_width(), row.winfo_width()))
check("ID 块自然宽度约等于文字宽",
      abs(e2.winfo_width() - (e2._font.measure(ID) + 2)) <= 4,
      (e2.winfo_width(), e2._font.measure(ID)))

# 截图留档（含选中态）
e1.event_generate("<Button-1>", x=6, y=5)
root.update()
time.sleep(0.25)
root.update()
from PIL import ImageGrab  # noqa: E402
x, y = root.winfo_rootx(), root.winfo_rooty()
ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(
    os.path.join(OUT, "copy_widget.png"))

root.destroy()
print("\n结果：失败 %d 项" % len(fails))
for f in fails:
    print("  - " + f)
