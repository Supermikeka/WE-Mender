# -*- coding: utf-8 -*-
"""探针：核对交接文档声称已修的守卫，在当前 repair.py 里是否真的存在。"""
import os
import sys
import json
import tempfile

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)

import wemender.repair as repair          # noqa: E402
from wemender import checks               # noqa: E402

TMP = tempfile.mkdtemp(prefix="wedprobe_")
CONTENT = os.path.join(TMP, "content", "431960")
os.makedirs(CONTENT, exist_ok=True)
NL = "\r\n"


def item(cid, size="512", manifest="1" * 18):
    return ('\t\t"%s"' % cid + NL + "\t\t{" + NL
            + '\t\t\t"size"\t\t"%s"' % size + NL
            + '\t\t\t"timeupdated"\t\t"1790000000"' + NL
            + '\t\t\t"manifest"\t\t"%s"' % manifest + NL + "\t\t}")


def detail(cid, manifest="1" * 18):
    return ('\t\t"%s"' % cid + NL + "\t\t{" + NL + '\t\t\t"type"\t\t"1"' + NL
            + '\t\t\t"latest_manifest"\t\t"%s"' % manifest + NL
            + '\t\t\t"latest_timeupdated"\t\t"1790000000"' + NL + "\t\t}")


def acf_text(inst, det):
    return ('"AppWorkshop"' + NL + "{" + NL + '\t"AppID"\t\t"431960"' + NL
            + '\t"WorkshopItemsInstalled"' + NL + "\t{" + NL
            + NL.join(inst) + NL + "\t}" + NL
            + '\t"WorkshopItemDetails"' + NL + "\t{" + NL
            + NL.join(det) + NL + "\t}" + NL + "}" + NL)


fails = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(extra) if extra else ""))
    if not cond:
        fails.append(name)


IDS = ["30000000%02d" % i for i in range(1, 6)]

print("==== 1. subs_state=empty（合法空订阅）+ 硬盘无文件 ====")
P = {"content": CONTENT, "acf": os.path.join(TMP, "a.acf"),
     "subs": os.path.join(TMP, "subs.vdf"), "we": TMP, "errors": [],
     "read_files": []}
open(P["acf"], "w", encoding="utf-8", newline="").write(
    acf_text([item(i) for i in IDS], [detail(i) for i in IDS]))
D_empty = {"content": CONTENT, "content_exists": True, "dirs": set(),
           "subs": set(), "subs_disabled": set(), "subs_state": "empty"}
plan = repair.repair_plan(P, D_empty)
check("empty 时不许删（交接文档声称已加）", plan["need_del"] == [], plan["need_del"])
check("empty 时 plan 应拒绝或只补", plan["ok"] is False or not plan["need_del"],
      dict(ok=plan["ok"], reason=plan["reason"]))

print("\n==== 2. 订阅清单部分解析（5 条只认出 2 条）====")
vdf = ('"subscribedfiles"' + NL + "{" + NL + '\t"appid"\t\t"431960"' + NL)
for n, i in enumerate(IDS):
    if n < 2:
        vdf += ('\t"%d"' % n + NL + "\t{" + NL
                + '\t\t"publishedfileid"\t\t"%s"' % i + NL
                + '\t\t"time_subscribed"\t\t"1792000000"' + NL
                + '\t\t"disabled_locally"\t\t"0"' + NL + "\t}" + NL)
    else:
        # 字段顺序不同：主正则认不出，兜底正则能认
        vdf += ('\t"%d"' % n + NL + "\t{" + NL
                + '\t\t"disabled_locally"\t\t"0"' + NL
                + '\t\t"publishedfileid"\t\t"%s"' % i + NL
                + '\t\t"time_subscribed"\t\t"1792000000"' + NL + "\t}" + NL)
vdf += "}" + NL
open(P["subs"], "w", encoding="utf-8", newline="").write(vdf)
D2 = {}
checks.scan_basic(P, D2)
print("   parsed subs=%d disabled=%d state=%s"
      % (len(D2["subs"]), len(D2["subs_disabled"]), D2["subs_state"]))
check("5 条全认出（否则部分收缩）", len(D2["subs"]) + len(D2["subs_disabled"]) == 5,
      sorted(D2["subs"] | D2["subs_disabled"]))
check("认不全时 state 不得为 ok", D2["subs_state"] != "ok" or
      len(D2["subs"]) + len(D2["subs_disabled"]) == 5, D2["subs_state"])

print("\n==== 3. _validate_deletions 段外断言 ====")
orig = open(P["acf"], "r", encoding="utf-8", newline="").read()
after = orig.replace('"AppID"\t\t"431960"', '"AppID"\t\t"999999"')
ok3, probs3 = repair._validate_deletions(orig, after, [IDS[0]])
check("段外被改（AppID）应判失败", ok3 is False, probs3)

print("\n==== 4. 删除目标已不存在 ====")
ok4, n4, err4 = repair._remove_ledger_blocks(orig, ["3999999999"])
check("目标不存在时不报错（现状）", err4 == "", err4 or "(空)")
check("目标不存在时 n_del=0", n4 == 0, n4)
ok4b, probs4b = repair._validate_deletions(orig, orig, ["3999999999"])
check("什么都没删时校验应判失败（否则会空写账本）", ok4b is False, probs4b)

print("\n==== 5. 重复 ID 块的报错文案 ====")
dup = acf_text([item(IDS[0]), item(IDS[0])], [detail(IDS[1])])
ok5, n5, err5 = repair._remove_ledger_blocks(dup, [IDS[0]])
check("重复块应中止", ok5 is None, err5)
check("报错文案非空且说明重复", bool(err5) and "0 个" not in err5, err5)

print("\n==== 6. 备份名同一秒冲突 ====")
repair.running_procs = lambda: []
repair.BASE_DIR = TMP
ok6a, bak6a = repair.repair_backup(P)
ok6b, bak6b = repair.repair_backup(P)
check("同一秒内两次备份不覆盖", bak6a != bak6b, (bak6a, bak6b))

print("\n==== 7. 删除下限守卫（大库删一半以上要拦）====")
BIG = ["3000010%02d" % i for i in range(1, 11)]
P7 = dict(P, acf=os.path.join(TMP, "b.acf"))
open(P7["acf"], "w", encoding="utf-8", newline="").write(
    acf_text([item(i) for i in BIG], [detail(i) for i in BIG]))
D7 = {"content": CONTENT, "content_exists": True, "dirs": set(),
      "subs": set(BIG[:4]), "subs_disabled": set(), "subs_state": "ok"}
plan7 = repair.repair_plan(P7, D7)
check("10 条里 6 条可删 → 拦下", plan7["need_del"] == [], plan7["need_del"])
check("拦下时给出 warn", "占比过高" in (plan7.get("warn") or ""), plan7.get("warn"))
D7b = dict(D7, subs=set(BIG[:5]))
plan7b = repair.repair_plan(P7, D7b)
check("10 条里 5 条可删 → 放行", len(plan7b["need_del"]) == 5, plan7b["need_del"])

print("\n结果：失败 %d 项" % len(fails))
for f in fails:
    print("  - " + f)
