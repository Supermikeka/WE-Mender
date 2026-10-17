# -*- coding: utf-8 -*-
"""自动修复「双向」回归：类型3补录 + 类型6删记录，互不干扰，且守卫有效"""
import os, sys, json, tempfile, shutil

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)

import wemender.repair as repair
repair.BASE_DIR = tempfile.gettempdir()   # 备份/日志一律进临时区，绝不写进项目目录
from wemender.repair import repair_plan, repair_run
from wemender.actions import read_ledger_ids, classify_batch

TMP = tempfile.mkdtemp(prefix="wedrep_")
CONTENT = os.path.join(TMP, "content", "431960")
os.makedirs(CONTENT, exist_ok=True)
repair.running_procs = lambda: []

# A = 类型3（订阅✓ 记录✗ 文件✓）→ 应补录
# B = 类型6（订阅✗ 记录✓ 文件✗）→ 应删记录
# C = 类型1（三账齐）        → 不得改动
A, B, C = "3000000011", "3000000012", "3000000013"

def mkfolder(cid):
    d = os.path.join(CONTENT, cid)
    os.makedirs(d, exist_ok=True)
    json.dump({"title": "t%s" % cid, "file": "w.mp4", "type": "video"},
              open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 512)

mkfolder(A)
mkfolder(C)

NL = "\r\n"
def item(cid, size, manifest):
    return ('\t\t"%s"' % cid + NL + "\t\t{" + NL + '\t\t\t"size"\t\t"%s"' % size + NL
            + '\t\t\t"timeupdated"\t\t"1790000000"' + NL
            + '\t\t\t"manifest"\t\t"%s"' % manifest + NL + "\t\t}")

def detail(cid, manifest):
    return ('\t\t"%s"' % cid + NL + "\t\t{" + NL + '\t\t\t"type"\t\t"1"' + NL
            + '\t\t\t"latest_manifest"\t\t"%s"' % manifest + NL
            + '\t\t\t"latest_timeupdated"\t\t"1790000000"' + NL + "\t\t}")

def build_acf():
    inst = [item(C, 512, "111111111111111111"),
            item(B, 512, "222222222222222222")]
    det = [detail(C, "111111111111111111"),
           detail(A, "333333333333333333")]
    return ('"AppWorkshop"' + NL + "{" + NL + '\t"AppID"\t\t"431960"' + NL
            + '\t"WorkshopItemsInstalled"' + NL + "\t{" + NL
            + NL.join(inst) + NL + "\t}" + NL
            + '\t"WorkshopItemDetails"' + NL + "\t{" + NL
            + NL.join(det) + NL + "\t}" + NL + "}" + NL)

P = {"content": CONTENT,
     "acf": os.path.join(TMP, "appworkshop_431960.acf"),
     "subs": os.path.join(TMP, "subs.vdf"),
     "we": TMP, "errors": []}
open(P["acf"], "w", encoding="utf-8", newline="").write(build_acf())

def write_vdf(ids):
    body = ('"subscribedfiles"' + NL + "{" + NL + '\t"appid"\t\t"431960"' + NL
            + '\t"time_last_updated"\t\t"1792149355"' + NL)
    for n, i in enumerate(ids):
        body += ('\t"%d"' % n + NL + "\t{" + NL
                 + '\t\t"publishedfileid"\t\t"%s"' % i + NL
                 + '\t\t"time_subscribed"\t\t"1792000000"' + NL
                 + '\t\t"disabled_locally"\t\t"0"' + NL + "\t}" + NL)
    body += "}" + NL
    open(P["subs"], "w", encoding="utf-8", newline="").write(body)

write_vdf([A, C])

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(extra) if extra else ""))
    if not cond:
        fails.append(name)

print("==== 1. 分类基线 ====")
check("A 是类型3", classify_batch(P, [A])[A][0] == 3, classify_batch(P, [A])[A])
check("B 是类型6", classify_batch(P, [B])[B][0] == 6, classify_batch(P, [B])[B])
check("C 是类型1", classify_batch(P, [C])[C][0] == 1, classify_batch(P, [C])[C])

print("\n==== 2. 修复计划双向判定 ====")
D = {"content": CONTENT, "content_exists": True,
     "dirs": {A, C}, "subs": {A, C}, "subs_state": "ok"}
plan = repair_plan(P, D)
check("need 只含 A", plan["need"] == [A], plan["need"])
check("need_del 只含 B", plan["need_del"] == [B], plan["need_del"])
check("ok=True", plan["ok"] is True, plan)

print("\n==== 3. 守卫：订阅清单不可读时不许删 ====")
D2 = dict(D, subs_state="missing", subs=None)
plan2 = repair_plan(P, D2)
check("subs_state=missing → need_del 为空", plan2["need_del"] == [], plan2["need_del"])
check("仍可补录 A", plan2["need"] == [A], plan2["need"])

print("\n==== 3b. 守卫：本地禁用的订阅项不许删 ====")
D3 = dict(D, subs={A, C}, subs_disabled={B})
plan3b = repair_plan(P, D3)
check("B 在 subs_disabled → 不进 need_del", plan3b["need_del"] == [], plan3b["need_del"])

print("\n==== 3c. 守卫：多游戏库里还有文件的不许删 ====")
LIB2 = os.path.join(TMP, "lib2")
os.makedirs(os.path.join(LIB2, "steamapps", "workshop", "content", "431960", B),
            exist_ok=True)
P_lib = dict(P, libraries=[LIB2])
plan3c = repair_plan(P_lib, D)
check("其它库有 B 的文件夹 → 不进 need_del", plan3c["need_del"] == [], plan3c["need_del"])
os.rmdir(os.path.join(LIB2, "steamapps", "workshop", "content", "431960", B))

print("\n==== 3d. 守卫：未订阅的条目不许被补成幽灵安装 ====")
# 夹具要让 vdf 与 D 一致（A 确实不在订阅里）——repair_plan 现在会现读 vdf，
# 若 vdf 里还写着 A 已订阅，这条用例就测不到"未订阅不补"了。
write_vdf([C])
D4 = dict(D, subs={C}, subs_disabled=set(), subs_total=1)
plan3d = repair_plan(P, D4)
check("A 未订阅 → 不进 need（不补记录）", plan3d["need"] == [], plan3d["need"])
write_vdf([A, C])          # 还原，供后续执行修复用

print("\n==== 4. 执行修复 ====")
orig = open(P["acf"], "r", encoding="utf-8", newline="").read()
ok, msg, bak = repair_run(P, D, ui=lambda s: None)
check("repair_run 成功", ok is True, msg)
check("返回了备份路径", bool(bak) and os.path.isfile(bak), bak)

inst, det = read_ledger_ids(P)
check("A 已补进 installed", A in inst, sorted(inst))
check("B 已从 installed 移除", B not in inst, sorted(inst))
check("B 已从 details 移除", B not in det, sorted(det))
check("C 原样保留", C in inst and C in det, (sorted(inst), sorted(det)))
check("A 已在 details 中（原本就有）", A in det)

new = open(P["acf"], "r", encoding="utf-8", newline="").read()
check("花括号配平", new.count("{") == new.count("}"), (new.count("{"), new.count("}")))
check("文件尾未变", new.rstrip().endswith("}"))
check("AppID 未变", '"AppID"\t\t"431960"' in new)
check("B 的 manifest 已彻底消失", "222222222222222222" not in new)
check("C 的 size/manifest 未被改写",
      '"size"\t\t"512"' in new and "111111111111111111" in new)

print("\n==== 5. 复检：状态归位 ====")
check("A → 类型1", classify_batch(P, [A])[A][0] == 1, classify_batch(P, [A])[A])
check("B → 类型8（三账皆无）", classify_batch(P, [B])[B][0] == 8, classify_batch(P, [B])[B])
check("C → 类型1", classify_batch(P, [C])[C][0] == 1, classify_batch(P, [C])[C])

print("\n==== 6. 幂等：再修一次应无事可做 ====")
plan3 = repair_plan(P, D)
check("无可修复项", plan3["ok"] is False and not plan3["need"] and not plan3["need_del"], plan3)
check("理由不再是「三方一致」的误报",
      plan3["reason"] == "三方一致，无需修复", plan3["reason"])

shutil.rmtree(TMP, ignore_errors=True)
print("\n==== 结果：%s（失败 %d 项）====" % ("全部通过 ✓" if not fails else "有失败 ✗", len(fails)))
sys.exit(0 if not fails else 1)
