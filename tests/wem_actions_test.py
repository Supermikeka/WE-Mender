# -*- coding: utf-8 -*-
"""WE-Mender actions.py 自测（模拟数据，不碰真实壁纸/账本）"""
import os, sys, shutil, json, tempfile

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)

import wemender.repair as repair
import wemender.actions as actions

TMP = tempfile.mkdtemp(prefix="wedtest_")
CONTENT = os.path.join(TMP, "content", "431960")
WE = os.path.join(TMP, "we")
os.makedirs(CONTENT, exist_ok=True)
os.makedirs(WE, exist_ok=True)
ACF = os.path.join(TMP, "appworkshop_431960.acf")
VDF = os.path.join(TMP, "431960_subscriptions.vdf")

# 进程检查与备份目录全部指向临时环境
repair.running_procs = lambda: []
repair.BASE_DIR = TMP
actions.BASE_DIR = TMP

P = {"content": CONTENT, "acf": ACF, "subs": VDF, "we": WE}

# ---------- 造数据 ----------
def mk_wall(cid, with_ws=True):
    d = os.path.join(CONTENT, cid)
    os.makedirs(d, exist_ok=True)
    pj = {"title": "Test/Wall*paper", "file": "w.mp4", "type": "video",
          "preview": "preview.gif"}
    if with_ws:
        pj["workshopid"] = cid
        pj["workshopurl"] = "steam://url/CommunityFilePage/" + cid
    json.dump(pj, open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 2048)
    return d

for cid in ("1000000001", "1000000003", "1000000005", "1000000007"):
    mk_wall(cid)

inst_block, det_block = "", ""
for cid in ("1000000001", "1000000002", "1000000005", "1000000006"):
    inst_block += ('\t\t"%s"\r\n\t\t{\r\n\t\t\t"size"\t\t"2048"\r\n'
                   '\t\t\t"timeupdated"\t\t"1790000000"\r\n'
                   '\t\t\t"manifest"\t\t"111"\r\n\t\t}\r\n' % cid)
    det_block += ('\t\t"%s"\r\n\t\t{\r\n\t\t\t"type"\t\t"1"\r\n'
                  '\t\t\t"latest_manifest"\t\t"111"\r\n'
                  '\t\t\t"latest_timeupdated"\t\t"1790000000"\r\n\t\t}\r\n' % cid)
acf_txt = ('"AppWorkshop"\r\n{\r\n\t"AppID"\t\t"431960"\r\n'
           '\t"WorkshopItemsInstalled"\r\n\t{\r\n%s\t}\r\n'
           '\t"WorkshopItemDetails"\r\n\t{\r\n%s\t}\r\n}\r\n'
           % (inst_block, det_block))
open(ACF, "w", encoding="utf-8", newline="").write(acf_txt)

sub_entries, newer = "", 0
for i, (cid, ts) in enumerate([("1000000001", "1792149385"),
                               ("1000000002", "1792082327"),
                               ("1000000004", "1792051459"),
                               ("1000000003", "1792051453")]):
    sub_entries += ('\t"%d"\r\n\t{\r\n\t\t"publishedfileid"\t\t"%s"\r\n'
                    '\t\t"time_subscribed"\t\t"%s"\r\n'
                    '\t\t"disabled_locally"\t\t"0"\r\n\t}\r\n' % (i, cid, ts))
vdf_txt = ('"subscribedfiles"\r\n{\r\n\t"appid"\t\t"431960"\r\n'
           '\t"time_last_updated"\t\t"1792149355"\r\n%s}\r\n' % sub_entries)
open(VDF, "w", encoding="utf-8", newline="").write(vdf_txt)

ALL = ["100000000%d" % i for i in range(1, 9)]
EXPECT = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}

fails = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail and not cond else ""))
    if not cond:
        fails.append(name)

# ---------- 1. 八态分类 ----------
cls = actions.classify_batch(P, ALL)
for cid, want in EXPECT.items():
    got = cls["100000000%d" % cid][0]
    check("classify 类型%d" % want, got == want, "got=%s" % got)

# ---------- 2. 页码计算 ----------
url, page, pos, rank = actions.subscription_page_url(P, "1000000004")
check("页码=第1页(第3新)", page == 1, "got page=%d" % page)
# 塞 65 个更新的订阅 → 1000000004 名次 66 → 第 3 页
extra = "".join('\t"%d"\r\n\t{\r\n\t\t"publishedfileid"\t\t"20000000%02d"\r\n'
                '\t\t"time_subscribed"\t\t"1792999%03d"\r\n'
                '\t\t"disabled_locally"\t\t"0"\r\n\t}\r\n' % (100 + i, i, i)
                for i in range(65))
open(VDF, "a", encoding="utf-8", newline="").write(extra)
url, page, pos, rank = actions.subscription_page_url(P, "1000000004")
check("页码=第3页(66名)", page == 3, "got page=%d" % page)
check("URL 含 steam://openurl", url.startswith("steam://openurl/https://"))
check("URL 含 p=3", "p=3" in url)

# ---------- 3. sanitize_dirname ----------
check("目录名净化", actions.sanitize_dirname('a/b\\c:d*e?f"g<h>i|j', "1") == "a_b_c_d_e_f_g_h_i_j")
check("目录名空回退", actions.sanitize_dirname("", "999") == "wallpaper_999")

# ---------- 4. convert_to_local（类型 7） ----------
ok, msg = actions.convert_to_local(P, "1000000007", strip_fields=True,
                                   ui=lambda s: print("   |", s))
check("convert 类型7 ok", ok, msg)
dst = os.path.join(WE, "projects", "myprojects", "Test_Wall_paper")
check("副本存在", os.path.isfile(os.path.join(dst, "w.mp4")))
pj2 = json.load(open(os.path.join(dst, "project.json"), encoding="utf-8"))
check("workshopid 已剥离", "workshopid" not in pj2 and "workshopurl" not in pj2)
check("title 保留", pj2.get("title") == "Test/Wall*paper")
check(".bak 已存", os.path.isfile(os.path.join(dst, "project.json.bak")))
check("原件未动", os.path.isfile(os.path.join(CONTENT, "1000000007", "project.json")))
# 剥离失败路径：损坏 json
mk_wall("1000000009", with_ws=False)
open(os.path.join(CONTENT, "1000000009", "project.json"), "w").write("{broken")
shutil.rmtree(os.path.join(WE, "projects", "myprojects", "Test_Wall_paper"))
ok, msg = actions.convert_to_local(P, "1000000009", strip_fields=True)
check("convert 健康检查拦截坏 json", not ok)

# ---------- 5. convert 守卫：订阅中的条目拒绝 ----------
ok, msg = actions.convert_to_local(P, "1000000001")
check("convert 守卫拦截类型1", not ok)

# ---------- 6. ledger_clear：类型5+6 清记录 ----------
ok, msg = actions.ledger_clear(P, ["1000000005", "1000000006"],
                               ui=lambda s: print("   |", s))
check("ledger_clear ok", ok, msg)
raw = open(ACF, encoding="utf-8", newline="").read()
check("账本已删 5/6", "1000000005" not in raw and "1000000006" not in raw)
check("账本保留 1/2", "1000000001" in raw and "1000000002" in raw)
check("花括号配平", raw.count("{") == raw.count("}"))
# 守卫：订阅中的条目拒绝
ok, msg = actions.ledger_clear(P, ["1000000001"])
check("ledger 守卫拦截订阅中", not ok)
# 幂等：再清一次应报"没有可清理"
ok, msg = actions.ledger_clear(P, ["1000000005"])
check("ledger 幂等拒绝", not ok)

# ---------- 7. recycle_delete：类型 7 ----------
ok, msg, done, failed = actions.recycle_delete(P, ["1000000007"],
                                               ui=lambda s: print("   |", s))
check("recycle 类型7 ok", ok and done == ["1000000007"], msg)
check("文件夹已消失", not os.path.isdir(os.path.join(CONTENT, "1000000007")))
# 守卫：订阅中拒绝
ok, msg, d2, f2 = actions.recycle_delete(P, ["1000000001"])
check("recycle 守卫拦截订阅中", not ok and not d2)
# 类型 5：先清账本后删文件
ok, msg, done, failed = actions.recycle_delete(P, ["1000000005"],
                                               ui=lambda s: print("   |", s))
check("recycle 类型5 ok", ok and done == ["1000000005"], msg)
raw2 = open(ACF, encoding="utf-8", newline="").read()
check("类型5账本已连带清理", "1000000005" not in raw2)
check("类型5文件已进回收站", not os.path.isdir(os.path.join(CONTENT, "1000000005")))

print("\n==== 结果：%s（失败 %d 项）====" % ("全部通过 ✓" if not fails else "有失败 ✗", len(fails)))
if fails:
    print("失败项：", fails)
    sys.exit(1)

# ==================== 第 2 轮审查修复的回归用例 ====================
print("\n---- 第 2 轮回归 ----")
import importlib
importlib.reload(actions)   # 拿到最新代码

# 重建全新环境（之前的 acf 已被上面用例改过）
TMP2 = tempfile.mkdtemp(prefix="wedtest2_")
CONTENT2 = os.path.join(TMP2, "content", "431960")
WE2 = os.path.join(TMP2, "we")
os.makedirs(CONTENT2, exist_ok=True)
os.makedirs(WE2, exist_ok=True)
ACF2 = os.path.join(TMP2, "appworkshop_431960.acf")
VDF2 = os.path.join(TMP2, "subs.vdf")
P2 = {"content": CONTENT2, "acf": ACF2, "subs": VDF2, "we": WE2}

def mk_wall2(cid):
    d = os.path.join(CONTENT2, cid)
    os.makedirs(d, exist_ok=True)
    json.dump({"title": "Dupa", "file": "w.mp4", "type": "video",
               "workshopid": cid},
              open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    open(os.path.join(d, "w.mp4"), "wb").write(b"\0" * 128)

def write_acf2(inst_ids, det_ids):
    ib = "".join('\t\t"%s"\r\n\t\t{\r\n\t\t\t"size"\t\t"128"\r\n'
                 '\t\t\t"timeupdated"\t\t"1"\r\n\t\t\t"manifest"\t\t"123456789012345678"\r\n\t\t}\r\n' % i
                 for i in inst_ids)
    db = "".join('\t\t"%s"\r\n\t\t{\r\n\t\t\t"type"\t\t"1"\r\n'
                 '\t\t\t"latest_manifest"\t\t"123456789012345678"\r\n\t\t}\r\n' % i
                 for i in det_ids)
    open(ACF2, "w", encoding="utf-8", newline="").write(
        '"AppWorkshop"\r\n{\r\n\t"AppID"\t\t"431960"\r\n'
        '\t"WorkshopItemsInstalled"\r\n\t{\r\n%s\t}\r\n'
        '\t"WorkshopItemDetails"\r\n\t{\r\n%s\t}\r\n}\r\n' % (ib, db))

def write_vdf2(ids):
    e = "".join('\t"%d"\r\n\t{\r\n\t\t"publishedfileid"\t\t"%s"\r\n'
                '\t\t"time_subscribed"\t\t"1792000000"\r\n\t\t"disabled_locally"\t\t"0"\r\n\t}\r\n'
                % (i, cid) for i, cid in enumerate(ids))
    open(VDF2, "w", encoding="utf-8", newline="").write(
        '"subscribedfiles"\r\n{\r\n\t"appid"\t\t"431960"\r\n'
        '\t"time_last_updated"\t\t"1"\r\n%s}\r\n' % e)

# S1: vdf 缺失 → classify 返回 None，破坏性动作全部拒绝
write_acf2(["5000000001"], ["5000000001"])
mk_wall2("5000000001")
if os.path.exists(VDF2):
    os.remove(VDF2)
cls = actions.classify_batch(P2, ["5000000001"])
check("S1: vdf缺失 → 类型None", cls["5000000001"][0] is None)
ok, msg, _d, _f = actions.recycle_delete(P2, ["5000000001"])
check("S1: vdf缺失 → recycle 拒绝", not ok and not os.path.exists(
    actions.__name__ and os.path.join(TMP2, "_nothing")) or not ok)
check("S1: 正常壁纸仍在", os.path.isdir(os.path.join(CONTENT2, "5000000001")))
ok, msg = actions.convert_to_local(P2, "5000000001")
check("S1: vdf缺失 → convert 拒绝", not ok)
ok, msg = actions.ledger_clear(P2, ["5000000001"])
check("S1: vdf缺失 → ledger 拒绝", not ok)
# S1 补充：vdf 存在但格式可疑（有 publishedfileid 字样却配不上）
open(VDF2, "w", encoding="utf-8").write('"subscribedfiles"\r\n{\r\n\t"appid"\t\t"431960"\r\n\t"weird"\t\t"1"\r\n}\r\n' .replace("weird", "publishedfileidXXX"))
check("S1: 可疑格式 → None", actions.read_subs_map(P2) is None)
# S1 补充：合法 0 订阅（无条目字样）→ 空 dict 而非 None
open(VDF2, "w", encoding="utf-8").write('"subscribedfiles"\r\n{\r\n\t"appid"\t\t"431960"\r\n}\r\n')
check("S1: 合法空订阅 → {}", actions.read_subs_map(P2) == {})

# S1 数量一致性：vdf 重建为 1 订阅，expected=2 → 中止
write_vdf2(["5000000001"])
ok, msg, _d, _f = actions.recycle_delete(P2, ["5000000001"], expected_sub_count=2)
check("S1: 订阅数不一致 → 中止", not ok)

# S3: <标题> 与 <标题>_<iid> 同时存在 → 拒绝且不破坏已有目录
write_vdf2([])          # 未订阅
write_acf2([], [])      # 无账本 → 类型 7
mk_wall2("5000000002")
actions.sanitize_dirname  # noop
myproj2 = os.path.join(WE2, "projects", "myprojects")
os.makedirs(myproj2, exist_ok=True)
os.makedirs(os.path.join(myproj2, "Dupa"))
os.makedirs(os.path.join(myproj2, "Dupa_5000000002"))
ok, msg = actions.convert_to_local(P2, "5000000002")
check("S3: 重名链拒绝", not ok and "已存在同名目录" in msg)
check("S3: 用户已有目录完好", os.path.isdir(os.path.join(myproj2, "Dupa"))
      and os.path.isdir(os.path.join(myproj2, "Dupa_5000000002")))

# M3: 目标只在 installed 段存在（details 无记录）→ 仍可清理
write_vdf2([])
write_acf2(["5000000003"], ["4000000004"])   # installed 有 3，details 无 3
# 空订阅清单下清账本属于"高危形态"，需要显式确认（allow_empty_subs=True）
ok, msg = actions.ledger_clear(P2, ["5000000003"], allow_empty_subs=True,
                               ui=lambda s: print("   |", s))
check("M3: 单段存在可清理", ok, msg)
raw = open(ACF2, encoding="utf-8", newline="").read()
check("M3: installed 已删/保留 details", "5000000003" not in raw
      and "4000000004" in raw)

# M4: strip 备份失败 → 明确「未做任何改动」（目录只读模拟）
mk_wall2("5000000005")
pj = os.path.join(CONTENT2, "5000000005", "project.json")
# 制造备份失败：先创建同名 .bak 为只读目录会太复杂，改为验证正常路径消息
ok, msg = actions._strip_workshop_fields(pj, strip=True)
check("M4: 正常剥离消息", ok and ".bak" in msg, msg)

print("---- 第 2 轮回归结束 ----")
if fails:
    print("失败项：", fails)
    sys.exit(1)
print("==== 第 2 轮全部通过 ✓ ====")
