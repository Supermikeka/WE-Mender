# -*- coding: utf-8 -*-
"""第 11 轮复审问题的修复验收"""
import ctypes, os, subprocess, sys, tempfile, json
TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)
fails = []
def check(n, c, e=""):
    print(("PASS  " if c else "FAIL  ") + n + ("  " + str(e) if e else ""))
    if not c: fails.append(n)

def read_clip():
    u = ctypes.windll.user32
    u.OpenClipboard(0)
    u.GetClipboardData.restype = ctypes.c_void_p
    h = u.GetClipboardData(13)
    got = ctypes.wstring_at(h) if h else ""
    u.CloseClipboard()
    return got

print("==== H-1：CopyEntry 复制 → 进程退出后仍在 ====")
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "child_copy.py")], check=False)
check("子进程退出后剪贴板仍有 CopyEntry 的内容",
      read_clip() == "SURVIVE-R11-文本", repr(read_clip()))

print("\n==== M-1：问题卡 key 跨刷新稳定 ====")
import tkinter as tk
import we_mender_gui as G
root = tk.Tk(); root.withdraw(); G._resolve_fonts(root)
app = G.App(root)
D = {"steam_running": False, "we_running": False, "booster_running": False,
     "subs_state": "ok", "subs": {"1"}, "subs_total": 1, "content_exists": True,
     "dirs": {"1"}, "installed_count": 1, "details_count": 1,
     "installed_ids": {"1"}, "details_ids": {"1"}, "acf_ok": True,
     "sub_no_content": [], "dir_no_sub": [], "record_no_dir": [],
     "dir_no_record": [], "repairable": [], "repair_reason": "",
     "content_ok": 1, "bad_project": [], "bad_ref": [], "empty_dirs": [],
     "zip_files": [], "log_problems": [], "cross_skip": None}
issues1 = [{"level": "提醒", "title": "测试问题卡", "symptom": "s",
            "cause": "c" * 60, "impact": "i" * 80, "who": "w", "risk": "r",
            "detail": "，".join("38209%05d" % i for i in range(20))}]
P = {"content": tempfile.mkdtemp(), "errors": []}
app.P, app.D = P, D
app._set_view(P, D, issues1, None)
app.root.update_idletasks()
ikey = [k for k in app._details if k.startswith("issue:")]
check("问题卡有 key", bool(ikey), ikey)
app._toggle_detail(ikey[0]); app.root.update_idletasks()
issues2 = [dict(issues1[0])]        # 模拟下一次排查：全新对象、同标题
app._set_view(P, D, issues2, None)
app.root.update_idletasks()
check("刷新后问题卡仍展开", app._expanded.get(ikey[0]) is True, app._expanded)

print("\n==== M-2：880 宽度下展开问题卡不撑宽 ====")
import wemender.repair as repair
repair.running_procs = lambda: []
app.root.deiconify()
app.root.geometry("880x700")
app.root.update(); app.root.update_idletasks()
for k in list(app._details):
    if k.startswith("issue:") and not app._expanded.get(k):
        app._toggle_detail(k)
app._set_view(P, D, issues2, None)
app.root.update_idletasks()
for k in list(app._details):
    if k.startswith("issue:"):
        app._toggle_detail(k)
app.root.update(); app.root.update_idletasks()
print("   窗口宽 =", app.root.winfo_width(), " canvas =", app.vc.winfo_width())
need = app.vbody.winfo_reqwidth()
canvas = app.vc.winfo_width()
check("内容需求 <= 视口宽", need <= canvas, (need, canvas))
check("横滚条未出现", app._hsb_visible is False, app._hsb_visible)

print("\n==== M-3：写盘失败（out=None）仍可复制完整报告 ====")
app._last_was_refresh = False
app.q.put(("diag_done", (P, D, issues2, "完整报告正文", None)))
app._poll(); app.root.update_idletasks()
check("report_text 已是本次完整结果", app.report_text == "完整报告正文", app.report_text)
check("复制按钮可用", app.btn_copy._disabled is False, app.btn_copy._disabled)
app._last_was_refresh = True
app.q.put(("diag_done", (P, D, issues2, "轻量刷新正文", None)))
app._poll(); app.root.update_idletasks()
check("轻量刷新不覆盖完整报告", app.report_text == "完整报告正文", app.report_text)
check("轻量刷新另存", app.refresh_text == "轻量刷新正文", app.refresh_text)
root.destroy()
print("\n结果：失败 %d 项" % len(fails))

# ---- 追加：空清单 + 大库（≥8 条）必须拒绝清账本 ----
import wemender.actions as A
import wemender.repair as R
TMP2 = tempfile.mkdtemp(prefix="wedbig_")
CONTENT2 = os.path.join(TMP2, "content", "431960"); os.makedirs(CONTENT2)
NL2 = "\r\n"
P2 = {"content": CONTENT2, "acf": os.path.join(TMP2, "a.acf"),
      "subs": os.path.join(TMP2, "s.vdf"), "we": TMP2, "errors": [],
      "read_files": [], "libraries": []}
IDS2 = ["39%08d" % i for i in range(10)]
def blk(c):
    return ('\t\t"%s"' % c + NL2 + "\t\t{" + NL2 + '\t\t\t"size"\t\t"1"' + NL2
            + '\t\t\t"manifest"\t\t"111111111111111111"' + NL2 + "\t\t}")
open(P2["acf"], "w", encoding="utf-8", newline="").write(
    '"AppWorkshop"' + NL2 + "{" + NL2 + '\t"AppID"\t\t"431960"' + NL2
    + '\t"WorkshopItemsInstalled"' + NL2 + "\t{" + NL2
    + NL2.join(blk(i) for i in IDS2) + NL2 + "\t}" + NL2
    + '\t"WorkshopItemDetails"' + NL2 + "\t{" + NL2
    + NL2.join(blk(i) for i in IDS2) + NL2 + "\t}" + NL2 + "}" + NL2)
open(P2["subs"], "w", encoding="utf-8", newline="").write(
    '"subscribedfiles"' + NL2 + "{" + NL2 + '\t"appid"\t\t"431960"' + NL2 + "}" + NL2)
R.running_procs = lambda: []
R.BASE_DIR = TMP2
ok_s, msg_s = A.check_subs_consistency(P2)
check("空清单 + 10 条记录 → 守卫拒绝", ok_s is False, msg_s)
ok_clear, msg_clear = A.ledger_clear(P2, IDS2[:1], ui=lambda s: None)
check("ledger_clear 同场景也拒绝", ok_clear is False, msg_clear)
print("\n追加结果：失败 %d 项" % len(fails))
