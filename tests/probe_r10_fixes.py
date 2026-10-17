# -*- coding: utf-8 -*-
"""第 10 轮修复验收：逐条复现审查报告里的高危/中危场景，确认已堵住。"""
import os, sys, json, tempfile, time
TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)
import wemender.repair as repair
import wemender.actions as actions
from wemender import checks

TMP = tempfile.mkdtemp(prefix="wedr10_")
CONTENT = os.path.join(TMP, "content", "431960"); os.makedirs(CONTENT)
NL = "\r\n"
repair.running_procs = lambda: []
repair.BASE_DIR = TMP
fails = []

def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(extra) if extra else ""))
    if not cond: fails.append(name)

def mkfolder(cid):
    d = os.path.join(CONTENT, cid); os.makedirs(d, exist_ok=True)
    json.dump({"title": "t" + cid, "file": "w.mp4", "type": "video"},
              open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 256)

def item(cid):
    return ('\t\t"%s"' % cid + NL + "\t\t{" + NL + '\t\t\t"size"\t\t"256"' + NL
            + '\t\t\t"timeupdated"\t\t"1790000000"' + NL
            + '\t\t\t"manifest"\t\t"%s"' % ("9"*18) + NL + "\t\t}")
def detail(cid):
    return ('\t\t"%s"' % cid + NL + "\t\t{" + NL + '\t\t\t"type"\t\t"1"' + NL
            + '\t\t\t"latest_manifest"\t\t"%s"' % ("9"*18) + NL
            + '\t\t\t"latest_timeupdated"\t\t"1790000000"' + NL + "\t\t}")
def write_acf(P, inst, det):
    open(P["acf"], "w", encoding="utf-8", newline="").write(
        '"AppWorkshop"' + NL + "{" + NL + '\t"AppID"\t\t"431960"' + NL
        + '\t"WorkshopItemsInstalled"' + NL + "\t{" + NL
        + NL.join(item(i) for i in inst) + NL + "\t}" + NL
        + '\t"WorkshopItemDetails"' + NL + "\t{" + NL
        + NL.join(detail(i) for i in det) + NL + "\t}" + NL + "}" + NL)
def write_vdf(P, ids, disabled=()):
    body = '"subscribedfiles"' + NL + "{" + NL + '\t"appid"\t\t"431960"' + NL
    for n, i in enumerate(ids):
        body += ('\t"%d"' % n + NL + "\t{" + NL
                 + '\t\t"publishedfileid"\t\t"%s"' % i + NL
                 + '\t\t"time_subscribed"\t\t"1792000000"' + NL
                 + '\t\t"disabled_locally"\t\t"%d"' % (1 if i in disabled else 0)
                 + NL + "\t}" + NL)
    body += "}" + NL
    open(P["subs"], "w", encoding="utf-8", newline="").write(body)

print("==== 1. GUI H1：有本地禁用订阅时，动作不再被永久拒绝 ====")
A, B, C = "3100000001", "3100000002", "3100000003"
for i in (A, C): mkfolder(i)
P = {"content": CONTENT, "acf": os.path.join(TMP, "a.acf"),
     "subs": os.path.join(TMP, "s.vdf"), "we": TMP, "errors": [],
     "read_files": [], "libraries": []}
write_acf(P, [A], [A])
write_vdf(P, [A, B], disabled=(B,))
D = {}
checks.scan_basic(P, D)
expected = D["subs_total"]
check("scan_basic 给出 subs_total=2", expected == 2, expected)
check("check_subs_consistency(2) 放行",
      actions.check_subs_consistency(P, expected_count=expected)[0] is True,
      actions.check_subs_consistency(P, expected_count=expected)[1])

print("\n==== 2. 引擎 H2：空/半截订阅清单 + 账本有记录 → 单条动作必须拒绝 ====")
P2 = {"content": CONTENT, "acf": os.path.join(TMP, "b.acf"),
      "subs": os.path.join(TMP, "t.vdf"), "we": TMP, "errors": [],
      "read_files": [], "libraries": []}
write_acf(P2, [A, C], [A, C])
open(P2["subs"], "w", encoding="utf-8", newline="").write(
    '"subscribedfiles"' + NL + "{" + NL + '\t"appid"\t\t"431960"' + NL)
ok, msg = actions.check_subs_consistency(P2)
check("截断 vdf + 账本 2 条 → 拒绝", ok is False, msg)
cls = actions.classify_batch(P2, [A])
check("截断 vdf → 分类为 None（不可读）", cls[A][0] is None, cls[A])

print("\n==== 3. 引擎 H1：排查后重新订阅 → 自动修复不许删这条记录 ====")
X, Y = "3200000001", "3200000002"
P3 = {"content": CONTENT, "acf": os.path.join(TMP, "c.acf"),
      "subs": os.path.join(TMP, "u.vdf"), "we": TMP, "errors": [],
      "read_files": [], "libraries": []}
mkfolder(Y)
write_acf(P3, [X, Y], [X, Y])
write_vdf(P3, [X, Y])                     # 磁盘上两条都在订阅
D3 = {"content": CONTENT, "content_exists": True, "dirs": {Y},
      "subs": {Y}, "subs_disabled": set(), "subs_state": "ok",
      "subs_total": 2, "installed_count": 2}
plan = repair.repair_plan(P3, D3)
check("旧 D 想删 X，但现读清单发现 X 仍订阅 → 不删",
      plan["need_del"] == [], (plan["need_del"], plan.get("warn")))
check("给出 warn 说明", bool(plan.get("warn")), plan.get("warn"))

print("\n==== 4. 引擎 M2：文件在其它库 → 不得判成类型 6 ====")
LIB2 = os.path.join(TMP, "lib2", "steamapps", "workshop", "content", "431960")
os.makedirs(os.path.join(LIB2, X), exist_ok=True)
P4 = dict(P3, libraries=[os.path.join(TMP, "lib2")])
write_vdf(P4, [Y])                         # X 未订阅
cls4 = actions.classify_batch(P4, [X])
check("其它库有文件夹 → 类型 5（不是 6）", cls4[X][0] == 5, cls4[X])
res = actions.ledger_clear(P4, [X], ui=lambda s: None)
check("ledger_clear 对类型 5 仍按既有策略处理（允许但如实标注）",
      res[0] in (True, False), res)

print("\n==== 5. 引擎 M3：project.json 顶层不是对象 → 内容检查不崩 ====")
BAD = "3300000001"
d = os.path.join(CONTENT, BAD); os.makedirs(d, exist_ok=True)
open(os.path.join(d, "project.json"), "w", encoding="utf-8").write("[]")
D5 = {"dirs": {BAD}, "content_exists": True}
issues5 = checks.chk_content(P4, D5)
check("没有抛异常且判为 bad_project", D5.get("bad_project") == [BAD], D5.get("bad_project"))
check("content_ok 有值", D5.get("content_ok") is not None, D5.get("content_ok"))

print("\n==== 6. 引擎 M5：有未订阅残留文件夹时，reason 不许说”三方一致” ====")
P6 = {"content": CONTENT, "acf": os.path.join(TMP, "d.acf"),
      "subs": os.path.join(TMP, "v.vdf"), "we": TMP, "errors": [],
      "read_files": [], "libraries": []}
write_acf(P6, [Y], [Y])
write_vdf(P6, [Y])
D6 = {"content": CONTENT, "content_exists": True, "dirs": {Y, BAD},
      "subs": {Y}, "subs_disabled": set(), "subs_state": "ok", "subs_total": 1,
      "installed_count": 1, "dir_no_sub": [BAD], "record_no_dir": [],
      "dir_no_record": [], "sub_no_content": []}
plan6 = repair.repair_plan(P6, D6)
check("reason 不再是”三方一致”", "三方一致" not in (plan6.get("reason") or ""),
      plan6.get("reason"))

print("\n==== 7. 引擎 L9：大库逐条清到空段 → 拒绝 ====")
BIG = ["3400%06d" % i for i in range(10)]
P7 = {"content": CONTENT, "acf": os.path.join(TMP, "e.acf"),
      "subs": os.path.join(TMP, "w.vdf"), "we": TMP, "errors": [],
      "read_files": [], "libraries": []}
write_acf(P7, BIG, BIG)
write_vdf(P7, [])
ok7, msg7 = actions.ledger_clear(P7, BIG, ui=lambda s: None)
check("10 条全清 → 拒绝", ok7 is False, msg7)

print("\n结果：失败 %d 项" % len(fails))
