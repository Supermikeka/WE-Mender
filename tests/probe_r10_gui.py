# -*- coding: utf-8 -*-
"""第 10 轮 GUI 修复验收：H2 忙状态兜底、M1 剪贴板跨进程、M2 刷新保留现场、M4 标题不被覆盖"""
import ctypes, os, subprocess, sys, tempfile, json
TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)
import tkinter as tk
import wemender.repair as repair

TMP = tempfile.mkdtemp(prefix="wedgui10_")
CONTENT = os.path.join(TMP, "content", "431960"); os.makedirs(CONTENT)
repair.running_procs = lambda: []
fails = []

def check(n, c, e=""):
    print(("PASS  " if c else "FAIL  ") + n + ("  " + str(e) if e else ""))
    if not c: fails.append(n)

def mk(cid):
    d = os.path.join(CONTENT, cid); os.makedirs(d)
    json.dump({"title": "测试《%s》" % cid, "file": "w.mp4", "type": "video"},
              open(os.path.join(d, "project.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 256)

for c in ("4000000001", "4000000003", "4000000007"):
    mk(c)
P = {"content": CONTENT, "acf": os.path.join(TMP, "a.acf"),
     "subs": os.path.join(TMP, "s.vdf"), "we": TMP, "errors": [],
     "read_files": [], "libraries": []}
NL = "\r\n"
open(P["acf"], "w", encoding="utf-8", newline="").write(
    '"AppWorkshop"' + NL + "{" + NL + '\t"AppID"\t\t"431960"' + NL
    + '\t"WorkshopItemsInstalled"' + NL + "\t{" + NL
    + '\t\t"4000000001"' + NL + "\t\t{" + NL + '\t\t\t"size"\t\t"256"' + NL
    + '\t\t\t"timeupdated"\t\t"1790000000"' + NL
    + '\t\t\t"manifest"\t\t"111111111111111111"' + NL + "\t\t}" + NL + "\t}" + NL
    + '\t"WorkshopItemDetails"' + NL + "\t{" + NL
    + '\t\t"4000000001"' + NL + "\t\t{" + NL + '\t\t\t"type"\t\t"1"' + NL
    + '\t\t\t"latest_manifest"\t\t"111111111111111111"' + NL
    + '\t\t\t"latest_timeupdated"\t\t"1790000000"' + NL + "\t\t}" + NL + "\t}" + NL + "}" + NL)
open(P["subs"], "w", encoding="utf-8", newline="").write(
    '"subscribedfiles"' + NL + "{" + NL + '\t"appid"\t\t"431960"' + NL
    + '\t"0"' + NL + "\t{" + NL + '\t\t"publishedfileid"\t\t"4000000001"' + NL
    + '\t\t"time_subscribed"\t\t"1792000000"' + NL
    + '\t\t"disabled_locally"\t\t"0"' + NL + "\t}" + NL + "}" + NL)

D = {"steam_running": False, "we_running": False, "booster_running": False,
     "subs_state": "ok", "subs": {"4000000001"}, "subs_total": 1,
     "content_exists": True, "dirs": {"4000000001", "4000000003", "4000000007"},
     "installed_count": 1, "details_count": 1,
     "installed_ids": {"4000000001"}, "details_ids": {"4000000001"},
     "acf_ok": True, "size_on_disk": 256,
     "sub_no_content": [], "dir_no_sub": ["4000000003", "4000000007"],
     "record_no_dir": [], "dir_no_record": ["4000000003", "4000000007"],
     "repairable": [], "repair_reason": "", "orphan_size": 512,
     "content_ok": 3, "bad_project": [], "bad_ref": [], "empty_dirs": [],
     "zip_files": [], "log_problems": [], "cross_skip": None}
issues = []

from we_mender_gui import App, _resolve_fonts
root = tk.Tk(); root.withdraw(); _resolve_fonts(root)
app = App(root)
app.P, app.D = P, D
app._set_view(P, D, issues, None)
app.root.update_idletasks()

print("==== 1. M2：刷新后展开状态与滚动位置保留 ====")
keys = [k for k in app._details if k.startswith("state_")]
check("有差异卡", bool(keys), keys)
k0 = keys[0]
app._toggle_detail(k0)
app.root.update_idletasks()
check("展开成功", app._expanded.get(k0) is True)
app._set_view(P, D, issues, None)      # 模拟哨兵自动刷新
app.root.update_idletasks()
check("刷新后仍保持展开", app._expanded.get(k0) is True, app._expanded)

print("\n==== 2. M4：联网失败不覆盖已缓存的标题 ====")
import we_mender_gui as G
lab = G.CopyEntry(root, "（未下载到本地，名称获取中…）", bg="#191919",
                  fg="#dddddd", font=G.FONTS["ui12"], width=10)
app._title_labels.setdefault(k0, {})["8888888888"] = lab
iid = "8888888888"
if True:
    lab.set_text("已缓存的正确标题")
    app._apply_titles((k0, None, ["9999999999"]))   # 只请求了一个不存在的 id
    app.root.update_idletasks()
    check("缓存标题未被覆盖", lab._text == "已缓存的正确标题", lab._text)
    app._apply_titles((k0, None, [iid]))
    check("真正请求失败的才写失败文案", "获取失败" in lab._text, lab._text)

print("\n==== 3. H2：_handle 抛异常后 busy 必须复位 ====")
app._set_busy(True)
orig = app._set_view
def boom(*a, **k):
    raise RuntimeError("模拟渲染异常")
app._set_view = boom
app.q.put(("diag_done", (P, D, issues, "text", None)))
app._poll()
app.root.update_idletasks()
check("异常后 busy=False", app.busy is False, app.busy)
# PillButton 自绘并拦截了 state，cget("state") 恒为 normal，必须读 _disabled
check("按钮已恢复可点", app.btn_diag._disabled is False, app.btn_diag._disabled)
app._set_view = orig

print("\n==== 4. M6：路径探测失败时状态栏不说“一切正常” ====")
app.P = dict(P, errors=["content 路径未找到"])
app._set_busy(True)
app.q.put(("diag_done", (app.P, D, [], "text", None)))
app._poll()
app.root.update_idletasks()
txt = app.status_lbl.cget("text")
check("状态栏提示结果不完整", "不完整" in txt or "未完成" in txt, txt)
app.P = P

print("\n==== 5. M1：复制内容在进程退出后仍可读 ====")
code = ("import sys; sys.path.insert(0, r'%s');"
        "from wemender.common import copy_to_clipboard;"
        "copy_to_clipboard('SURVIVE-TEST-第10轮')" % TOOL)
subprocess.run([sys.executable, "-c", code], check=False)
u = ctypes.windll.user32
u.OpenClipboard(0); u.GetClipboardData.restype = ctypes.c_void_p
h = u.GetClipboardData(13)  # CF_UNICODETEXT
got = ctypes.wstring_at(h) if h else ""
u.CloseClipboard()
check("子进程退出后剪贴板仍有内容", got == "SURVIVE-TEST-第10轮", repr(got))

root.destroy()
print("\n结果：失败 %d 项" % len(fails))
