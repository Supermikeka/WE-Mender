# -*- coding: utf-8 -*-
"""终验2b：展开卡片后验证 need_net 按钮的灰控 + 网络块三态（冻结哨兵排除干扰）"""
import os, sys, json, tempfile, time
TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)
import tkinter as tk
import wemender.repair as repair

TMP = tempfile.mkdtemp(prefix="wednet_")
CONTENT = os.path.join(TMP, "content", "431960")
os.makedirs(CONTENT, exist_ok=True)
repair.running_procs = lambda: []
for cid in ("3000000001", "3000000003", "3000000007"):
    d = os.path.join(CONTENT, cid)
    os.makedirs(d, exist_ok=True)
    json.dump({"title": "测试%s" % cid, "file": "w.mp4", "type": "video",
               "workshopid": cid},
              open(os.path.join(d, "project.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 512)

P = {"content": CONTENT, "acf": os.path.join(TMP, "appworkshop_431960.acf"),
     "subs": os.path.join(TMP, "subs.vdf"), "we": os.path.join(TMP, "we"),
     "errors": []}
open(P["acf"], "w", encoding="utf-8", newline="").write(
    '"AppWorkshop"\r\n{\r\n\t"AppID"\t\t"431960"\r\n'
    '\t"WorkshopItemsInstalled"\r\n\t{\r\n\t\t"3000000001"\r\n\t\t{\r\n'
    '\t\t\t"size"\t\t"512"\r\n\t\t\t"timeupdated"\t\t"1790000000"\r\n'
    '\t\t\t"manifest"\t\t"123456789012345678"\r\n\t\t}\r\n\t}\r\n'
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
     "sub_no_content": ["3000000002"], "dir_no_sub": ["3000000003", "3000000007"],
     "record_no_dir": ["3000000002"], "dir_no_record": ["3000000003", "3000000007"],
     "repairable": [], "repair_reason": "", "orphan_size": 1024,
     "content_ok": 3, "bad_project": [], "bad_ref": [], "empty_dirs": [],
     "zip_files": [], "log_problems": [], "cross_skip": None}
issues = [{"level": "提醒", "title": "测试问题卡", "symptom": "sym", "cause": "c",
           "impact": "i", "who": "w", "risk": "r", "detail": "d"}]

import we_mender_gui as G
root = tk.Tk()
root.withdraw()
G._resolve_fonts(root)
G.apply_theme(root)
root.deiconify()
app = G.App(root)
app._net_ts = time.time() + 3600      # 冻结哨兵，排除真实检测干扰
out = []


def gate(tag):
    need = [m for m in app._action_btns if m.get("need_net")]
    dis = [m for m in need if app._btn_reasons(m)]
    out.append("[%-14s] _net_ok=%-5r need_net=%d 禁用=%d ｜ 块文本=%r" % (
        tag, app._net_ok, len(need), len(dis), app.env_net_lbl.cget("text")))
    return len(need), len(dis)


def go():
    app.P, app.D = P, D
    app._set_view(P, D, issues, None)
    app.root.update_idletasks()
    for k in list(app._details.keys()):
        app._toggle_detail(k)
    app.root.update_idletasks()
    root.after(300, s1)


def s1():
    n, d = gate("未检测(None)")
    out.append("      按钮文字: %s" % sorted(
        {m["b"]._text for m in app._action_btns if m.get("need_net")}))
    app.q.put(("net", (False, "不可达")))
    root.after(500, s2)


def s2():
    n, d = gate("不可达(False)")
    out.append("      fg=%r bg=%r" % (app.env_net_lbl.cget("fg"),
                                     app.env_net_lbl.cget("bg")))
    out.append("      禁用原因样例: %r" % (
        app._btn_reasons([m for m in app._action_btns if m.get("need_net")][0])[0][:40]
        if n else None))
    app.q.put(("net", (True, "可达（HTTP 200）")))
    root.after(500, s3)


def s3():
    n, d = gate("可达(True)")
    out.append("      fg=%r" % (app.env_net_lbl.cget("fg"),))
    app.q.put(("net", "畸形"))
    root.after(500, s4)


def s4():
    gate("畸形(应保持可达)")
    app.q.put(("net", (1, "int")))
    root.after(500, s5)


def s5():
    gate("int 1(应True)")
    import tempfile
    out_path = os.path.join(tempfile.gettempdir(), "wem_diag_net.out")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    root.destroy()


root.after(300, go)
root.mainloop()
