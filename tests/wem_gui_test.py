# -*- coding: utf-8 -*-
"""GUI 冒烟测试：虚拟 Tk 构建 App + 注入模拟诊断数据渲染全部卡片（不弹窗）"""
import os, sys, tempfile

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)

import tkinter as tk
import wemender.repair as repair
import wemender.actions as actions

TMP = tempfile.mkdtemp(prefix="wedgui_")
CONTENT = os.path.join(TMP, "content", "431960")
os.makedirs(CONTENT, exist_ok=True)
repair.running_procs = lambda: []

# 造 4 类问题条目的假数据
def mk(cid, ws=True):
    d = os.path.join(CONTENT, cid)
    os.makedirs(d, exist_ok=True)
    pj = {"title": "测试《%s》" % cid, "file": "w.mp4", "type": "video"}
    if ws:
        pj["workshopid"] = cid
    json.dump(pj, open(os.path.join(d, "project.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 512)

import json
for cid in ("3000000001", "3000000003", "3000000007"):
    mk(cid)

P = {"content": CONTENT,
     "acf": os.path.join(TMP, "appworkshop_431960.acf"),
     "subs": os.path.join(TMP, "subs.vdf"),
     "we": os.path.join(TMP, "we"),
     "errors": []}
open(P["acf"], "w", encoding="utf-8", newline="").write(
    '"AppWorkshop"\r\n{\r\n\t"AppID"\t\t"431960"\r\n'
    '\t"WorkshopItemsInstalled"\r\n\t{\r\n'
    '\t\t"3000000001"\r\n\t\t{\r\n\t\t\t"size"\t\t"512"\r\n'
    '\t\t\t"timeupdated"\t\t"1790000000"\r\n\t\t\t"manifest"\t\t"123456789012345678"\r\n\t\t}\r\n\t}\r\n'
    '\t"WorkshopItemDetails"\r\n\t{\r\n\t\t"3000000001"\r\n\t\t{\r\n'
    '\t\t\t"type"\t\t"1"\r\n\t\t\t"latest_manifest"\t\t"123456789012345678"\r\n'
    '\t\t\t"latest_timeupdated"\t\t"1790000000"\r\n\t\t}\r\n\t}\r\n}\r\n')
open(P["subs"], "w", encoding="utf-8").write(
    '"subscribedfiles"\r\n{\r\n\t"appid"\t\t"431960"\r\n'
    '\t"time_last_updated"\t\t"1792149355"\r\n'
    '\t"0"\r\n\t{\r\n\t\t"publishedfileid"\t\t"3000000001"\r\n'
    '\t\t"time_subscribed"\t\t"1792000000"\r\n\t\t"disabled_locally"\t\t"0"\r\n\t}\r\n'
    '\t"1"\r\n\t{\r\n\t\t"publishedfileid"\t\t"3000000002"\r\n'
    '\t\t"time_subscribed"\t\t"1792000001"\r\n\t\t"disabled_locally"\t\t"0"\r\n\t}\r\n}\r\n')

D = {"steam_running": False, "we_running": False, "booster_running": False,
     "subs_state": "ok", "subs": ["3000000001", "3000000002"],
     "content_exists": True, "dirs": ["3000000001", "3000000003", "3000000007"],
     "installed_count": 1, "details_count": 1, "installed_ids": {"3000000001"},
     "details_ids": {"3000000001"}, "acf_ok": True, "size_on_disk": 512,
     "sub_no_content": ["3000000002"],          # 类型 4
     "dir_no_sub": ["3000000003", "3000000007"],  # 类型 5/7 混合
     "record_no_dir": ["3000000002"],           # 类型 2（订阅中）
     "dir_no_record": ["3000000003", "3000000007"],
     "repairable": [], "repair_reason": "", "orphan_size": 1024,
     "content_ok": 3, "bad_project": [], "bad_ref": [], "empty_dirs": [],
     "zip_files": [], "log_problems": [], "cross_skip": None}

issues = [{"level": "提醒", "title": "测试问题卡", "symptom": "sym",
           "cause": "c", "impact": "i", "who": "w", "risk": "r", "detail": "d"}]

from we_mender_gui import App, _resolve_fonts

root = tk.Tk()
root.withdraw()                     # 不显示窗口
_resolve_fonts(root)                # GUI 在 main() 中做的字体探测，测试需手动补
app = App(root)

# 模拟排查完成：渲染全部卡片
app.P, app.D = P, D
app._set_view(P, D, issues, None)
app.root.update_idletasks()
n_cards = len(app._details)
print("构建卡片数：", n_cards)

# 逐个展开差异卡详情（触发 _build_detail 各分支 + 按钮创建）
for key in list(app._details.keys()):
    try:
        app._toggle_detail(key)
        app.root.update_idletasks()
    except Exception as e:
        print("FAIL 展开卡片失败:", key, "→", e)
        sys.exit(1)
print("全部卡片展开 OK（%d 张，含详情构建）" % len(app._details))

# 验证孤儿卡的按钮存在（PillButton 是 Canvas 子类，按类名识别）
btn_texts = []
def walk(w):
    for c in w.winfo_children():
        if type(c).__name__ == "PillButton":
            btn_texts.append(c._text)
        walk(c)
walk(root)
import collections
cnt = collections.Counter(btn_texts)
print("按钮分布：", dict(cnt))
expect = {"🔄 刷新": 1, "打开订阅列表": 1,
          "重新订阅": 2, "转本地壁纸": 2,
          "删除到回收站": 2, "打开文件夹": 2, "清账本记录": 0}
ok = all(cnt.get(k, 0) == v for k, v in expect.items())
print("按钮分布校验：", "PASS" if ok else "FAIL 期望 %s" % expect)

# ---- PillButton 背景与父容器一致性（椭圆按钮黑底修复验证）----
mism = []
def walk2(w):
    for c in w.winfo_children():
        if type(c).__name__ == "PillButton":
            try:
                pb, cb = str(c.cget("bg")).lower(), str(c.master.cget("bg")).lower()
                if pb != cb:
                    mism.append((c._text, pb, cb))
            except Exception as e:
                mism.append((c._text, "ERR", str(e)))
        walk2(c)
walk2(root)
color_ok = not mism
print("按钮底色一致性：", "PASS（%d 个按钮全部融入父容器）" % len(btn_texts)
      if color_ok else "FAIL %s" % mism)
ok = ok and color_ok

root.destroy()
print("==== GUI 冒烟：%s ====" % ("全部通过 ✓" if ok and n_cards >= 3 else "有问题 ✗"))
sys.exit(0 if ok else 1)

# ---- 追加：PillButton 背景与父容器一致性（修复椭圆按钮黑底）----
import tkinter as tk2
mism = []
def walk2(w):
    for c in w.winfo_children():
        if type(c).__name__ == "PillButton":
            try:
                pb, cb = c.cget("bg"), c.master.cget("bg")
                if str(pb).lower() != str(cb).lower():
                    mism.append((c._text, pb, cb))
            except Exception as e:
                mism.append((c._text, "ERR", str(e)))
        walk2(c)
walk2(root2 := None or None)
