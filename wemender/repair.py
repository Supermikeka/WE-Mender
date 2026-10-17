# -*- coding: utf-8 -*-
"""
账本修复引擎（WE-Mender 3.0）

修复目标：在保留账本现有记录的前提下，把「订阅详情 ∩ 健康文件夹」中缺失的
条目补进「已安装记录」段 —— 即：壁纸文件就在硬盘上（且内容健康）、但账本
不记得的条目（Steam 误判全量重下的根源）。同时清理详情段里
`BytesDownloaded/BytesToDownload` 残留行。

安全链：
  1) 前置检查：Steam 与 Wallpaper Engine 必须完全退出
  2) 自动备份账本到 backups/（校验副本一致性，保留最近 N 份）
  3) 重建：补录缺失记录 + 清理下载计数残留，其余内容逐字节不变
  4) 写入前校验：结构性不变量 + 行级差分 + 逐条核对（见 repair_validate）
  5) 原子写入：先写临时文件并 fsync，再一次性替换
  6) 失败兜底：账本疑似受损时自动从备份还原

本模块不删除、不移动、不改动任何壁纸文件。
"""

import datetime
import hashlib
import json
import os
import re
import shutil
from collections import Counter

from wemender.common import (APPID, BASE_DIR, RE_BLOCK, RE_ITEM,
                             RE_LEDGER_BLOCK_SRC, REPAIR_LOG_NAME, dir_size,
                             isdir, isfile, now_str, read_subs_ids,
                             running_procs)

REPAIR_BACKUP_KEEP = 10
RE_LEDGER_BLOCK = re.compile(RE_LEDGER_BLOCK_SRC)


def _read_exact(path):
    """读文件但不做换行转换（保证能按原样精确写回）"""
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _section_span(t, name):
    """
    精确定位账本中某一段的正文范围（花括号配平）。
    返回 (body_start, body_end, header_start)；正文不含两端花括号。
    找不到或配平失败时返回 None。兼容 LF 与 CRLF。
    """
    m = re.search(r'\r?\n\t"' + name + r'"\r?\n\t\{', t)
    if not m:
        return None
    start = m.end()
    depth = 1
    i = start
    while i < len(t) and depth > 0:
        if t[i] == '{':
            depth += 1
        elif t[i] == '}':
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return start, i - 1, m.start()


def _parse_items_precise(raw):
    """解析一段里每个条目的全部字段：id → {字段: 值}"""
    items = {}
    for fid, body in re.findall(RE_BLOCK, raw, re.S):
        items[fid] = dict(re.findall(r'"([A-Za-z_]+)"\s+"([^"]*)"', body))
    return items


def _to_int(s):
    try:
        return int(s)
    except Exception:
        return None


def _folder_healthy(folder):
    """
    文件夹内容健康检查（宁可严格）：
      · project.json 存在且可解析为对象
      · 主文件字段（file）非空
      · 引用的主文件存在，且字节数 > 0
    空目录 / 下载不完整 / 内容损坏的目录都会被判为不健康，
    这类条目绝不能补录成「已安装」（否则 Steam 不再重下、壁纸被永久钉死）。
    返回 (ok, 原因)。
    """
    pj = os.path.join(folder, "project.json")
    if not isfile(pj):
        return False, "缺少 project.json"
    try:
        with open(pj, "r", encoding="utf-8", errors="ignore") as f:
            j = json.load(f)
    except Exception:
        return False, "project.json 无法解析"
    if not isinstance(j, dict):
        return False, "project.json 不是对象结构"
    ref = j.get("file") or ""
    if not isinstance(ref, str) or not ref.strip():
        return False, "主文件字段（file）缺失或为空"
    ref_path = os.path.join(folder, ref)
    if not isfile(ref_path):
        # WE 的实际加载规则：源文件（如 scene.json）缺失时，场景类壁纸
        # 回退加载编译包（scene.pkg）——订阅下载的壁纸通常只含编译包
        import glob as _glob
        if not _glob.glob(os.path.join(folder, "*.pkg")):
            return False, "引用的文件不存在"
    try:
        if isfile(ref_path):
            if os.path.getsize(ref_path) <= 0:
                return False, "引用的文件为 0 字节（下载不完整）"
        else:
            # 回退到了编译包：校验包文件非空
            import glob as _glob
            pkgs = _glob.glob(os.path.join(folder, "*.pkg"))
            if not pkgs or all(os.path.getsize(x) <= 0 for x in pkgs):
                return False, "引用的文件与编译包均为空（下载不完整）"
    except OSError as e:
        return False, "无法读取引用文件大小（%s）" % e
    return True, ""


def repair_plan(P, D):
    """
    判定「可自动修复集合」。自动修复只动账本记录，两个方向：
      · need（补）：(订阅详情 ∩ 硬盘文件夹 − 已安装记录) 中「内容健康」的部分
        —— 类型 3，账本漏登了已经下好的壁纸。
      · need_del（删）：已安装记录里「未订阅 且 硬盘没文件」的部分
        —— 类型 6（残尸），记录指向不存在的文件，最易诱发全量重下。
    不健康的条目（空目录、缺 project.json、引用文件缺失）单独列出（skipped），
    只能由 Steam 重新下载。以调用这一刻的账本原文为准，不依赖旧结论。
    返回 dict(ok, need, need_del, skipped, reason)。
    """
    acf = P.get("acf")
    content = P.get("content")
    if not acf or not isfile(acf):
        return dict(ok=False, need=[], need_del=[], skipped=[], reason="账本文件不可用")
    if not D.get("content_exists") or not content:
        return dict(ok=False, need=[], need_del=[], skipped=[], reason="工坊内容目录不可用")
    if D.get("content_list_failed"):
        return dict(ok=False, need=[], need_del=[], skipped=[],
                    reason="无法读取工坊内容目录列表（权限不足或被占用），为避免误判已中止")
    try:
        raw = _read_exact(acf)
    except Exception as e:
        return dict(ok=False, need=[], need_del=[], skipped=[],
                    reason="读取账本失败：%s" % e)
    di = _section_span(raw, "WorkshopItemDetails")
    ii = _section_span(raw, "WorkshopItemsInstalled")
    if not di:
        return dict(ok=False, need=[], need_del=[], skipped=[],
                    reason="账本里找不到「订阅详情」段")
    if not ii:
        return dict(ok=False, need=[], need_del=[], skipped=[],
                    reason="账本缺少「已安装记录」段（结构异常），无法自动修复；"
                           "请先让 Steam 重建或从备份恢复")
    detail_ids = set(re.findall(RE_ITEM, raw[di[0]:di[1]]))
    inst_ids = set(re.findall(RE_ITEM, raw[ii[0]:ii[1]]))
    dirs_set = set(D.get("dirs") or set())
    subs = D.get("subs")
    subs_state = D.get("subs_state")
    subs_ok = subs_state in ("ok", "empty") and subs is not None
    subs_set = (set(subs) | set(D.get("subs_disabled") or set())) if subs_ok else None
    # 订阅清单「合法为空」是四种订阅态里最不可信的一种：可能是账户切错、
    # 云端刚被清空、或刚好在 Steam 退订的中间态。这种情况只补不删 ——
    # 宁可留一条残记录（下次还能修），也不能把整片正常记录一次删空。
    allow_del = subs_state == "ok"

    # 现读一次订阅清单：D 是排查那一刻的快照，之后用户可能在 Steam 里
    # 订阅/退订过。补、删两个方向都要以「当前清单」为准 ——
    # 否则会补出幽灵安装（刚退订的条目被补记录），或删掉刚重新订阅的记录。
    fresh_subs, fresh_dis, fresh_note = read_subs_ids(P.get("subs"))
    fresh_ok = fresh_subs is not None and not fresh_note
    fresh_total = (len(fresh_subs) + len(fresh_dis)) if fresh_ok else None
    d_total = D.get("subs_total")

    # 「补」方向：已订阅 + 详情有记录 + 文件在 − 已安装记录（类型 3）
    # 订阅侧一律用**现读**清单（不是 D 的快照）：条数变了也照样用现读，
    # 否则用户刚退订的条目会被补成幽灵安装。
    cand = (detail_ids & dirs_set) - inst_ids
    if fresh_ok:
        cand &= (fresh_subs | fresh_dis)
    elif subs_set is not None:
        cand &= subs_set
    candidates = sorted(cand, key=int)
    need, skipped = [], []
    for fid in candidates:
        ok_h, _why = _folder_healthy(os.path.join(content, fid))
        (need if ok_h else skipped).append(fid)

    # 「只删」方向：未订阅 + 有记录 + 缺文件（类型 6）
    # 四道硬守卫，任一不满足就不删：
    #   ① 订阅清单必须读得可靠且非空（否则可能把正常壁纸的记录当垃圾清掉）；
    #   ② 「已订阅但本地禁用」也算订阅 —— D["subs"] 只装未禁用的，必须并入
    #      subs_disabled，否则禁用的壁纸会被误判成"未订阅"。
    #   ③ 多游戏库：其它库里还有这个文件夹，就不是残尸，绝不删。
    #   ④ 下限：删除量达到已安装记录的一半以上，说明「未订阅且无文件」大面积为真，
    #      多半是库路径/账户异常，一律不删（否则会把正常库整段清空）。
    need_del = []
    warn = ""
    if subs_set is not None and allow_del:
        libs = list(P.get("libraries") or [])
        if not libs and content:
            # P["libraries"] 缺省时（测试/演示/将来别的调用方）不能让守卫③静默消失：
            # 至少把主 content 所属的库还原出来，别把"没给库列表"当成"没有其它库"。
            marker = os.path.join("steamapps", "workshop", "content")
            idx = content.lower().find(marker.lower())
            if idx > 0:
                libs = [content[:idx].rstrip("\\/")]
        for iid in sorted(inst_ids, key=int):
            if iid in subs_set or iid in dirs_set:
                continue
            if any(os.path.isdir(os.path.join(lib, "steamapps", "workshop",
                                              "content", APPID, iid)) for lib in libs):
                continue
            need_del.append(iid)
        # 下限守卫分两档：① 删到一条不剩必然产出空段，绝对禁止；
        # ② 库比较大（≥8 条）时删掉超过一半，也按「路径/账户异常」处理。
        # 小库（测试、新账户）不做比例判断，否则正常的个位数修复会被误拦。
        too_many = False
        if need_del:
            too_many = (len(need_del) >= len(inst_ids)
                        or (len(inst_ids) >= 8 and len(need_del) * 2 > len(inst_ids)))
        if too_many:
            warn = ("检出 %d/%d 条记录可删（占比过高），疑似库路径或订阅清单异常，"
                    "本次只补不删；请确认 Steam 库与登录账户后重新排查"
                    % (len(need_del), len(inst_ids)))
            need_del = []

        # ④ 用上面现读的清单确认「未订阅」这个判断没有过期。
        #    最常见的自然操作流：排查 → 报告说某条是残尸 → 用户去 Steam 重新订阅
        #    → 回到仍开着的窗口点「自动修复」。D 是排查那一刻的快照，此时它还说
        #    "未订阅"，照删就会把一条**仍在订阅**的记录删掉（工具还会报"修复成功"）。
        #    只要候选里有任何一条此刻已在订阅中，本次就不删。
        if need_del:
            if not fresh_ok:
                warn = ("无法重新确认订阅清单（%s），本次只补不删；"
                        "请确认 Steam 已同步后重新排查" % (fresh_note or "文件不可读"))
                need_del = []
            else:
                fresh_all = fresh_subs | fresh_dis
                revival = [i for i in need_del if i in fresh_all]
                if revival:
                    warn = ("有 %d 条记录在排查后重新订阅了（%s），本次只补不删；"
                            "请重新排查后再处理残留记录"
                            % (len(revival), "、".join(revival[:3])))
                    need_del = []
                elif d_total is not None and d_total != len(fresh_all):
                    warn = ("订阅清单在排查后发生了变化（排查时 %d 条，现在 %d 条），"
                            "本次只补不删；请重新排查后再处理残留记录"
                            % (d_total, len(fresh_all)))
                    need_del = []
    elif subs_state == "empty" and (inst_ids - dirs_set):
        warn = ("本地订阅清单为空，无法确认这些记录确实已退订，本次只补不删")

    if not need and not need_del:
        if warn:
            reason = warn
        elif candidates:
            reason = ("有 %d 个条目内容不完整（缺 project.json 或引用文件），"
                      "需由 Steam 重新下载，不能自动补录" % len(skipped))
        elif (D.get("record_no_dir") or D.get("dir_no_record")
              or D.get("sub_no_content") or D.get("dir_no_sub")):
            # dir_no_sub 也在此列：库里有未订阅的残留文件夹时，绝不能对用户说
            # "三方一致，无需修复" —— 报告上半部分明明写着"存在 N 个无主文件夹"。
            reason = ("当前问题不在「自动修复」的处理范围内（它只补/删账本记录），"
                      "请按差异卡上的按钮逐项处理")
        else:
            inst_now = set(inst_ids)
            if inst_now == dirs_set == set(subs_set or set()):
                reason = "三方一致，无需修复"
            else:
                reason = ("当前没有可自动修复的条目（自动修复只补/删账本记录），"
                          "请按差异卡逐项处理")
        return dict(ok=False, need=[], need_del=[], skipped=skipped,
                    reason=reason, warn=warn)
    return dict(ok=True, need=need, need_del=need_del, skipped=skipped,
                reason="", warn=warn)


def repair_precheck(P):
    """修复前置检查：Steam 与 Wallpaper Engine 必须完全退出。返回 (ok, 说明)"""
    procs = running_procs()
    if procs is None:
        return False, "进程检测失败，无法确认 Steam / Wallpaper Engine 已退出，为安全起见已中止"
    bad = [p for p in procs if p == "steam.exe" or p.startswith("wallpaper")]
    if bad:
        return False, ("检测到仍在运行：%s。请先完全退出 Steam 和 Wallpaper Engine "
                       "再执行修复（文件被占用时修改会被覆盖甚至损坏）。"
                       "本工具只做提示，不会自动关闭这些程序" % "、".join(bad))
    return True, ""


def repair_backup(P):
    """修复前自动备份账本到 backups/，并校验副本一致；只保留最近 N 份。
    返回 (ok, 备份路径或错误信息)"""
    acf = P.get("acf")
    if not acf or not isfile(acf):
        return False, "账本文件不存在，无法备份"
    bdir = os.path.join(BASE_DIR, "backups")
    try:
        os.makedirs(bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(bdir, "appworkshop_%s.%s.bak" % (APPID, stamp))
        # 同一秒内连点两次会重名；直接 copy2 会覆盖上一份备份，等于毁掉回滚点
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = os.path.join(bdir, "appworkshop_%s.%s_%d.bak" % (APPID, stamp, n))
        shutil.copy2(acf, dst)
        if _md5(acf) != _md5(dst):
            os.remove(dst)
            return False, "备份校验失败（副本与原文件不一致），已中止"
    except Exception as e:
        return False, "备份失败：%s" % e
    try:
        prefix = "appworkshop_%s." % APPID
        baks = sorted(f for f in os.listdir(bdir)
                      if f.startswith(prefix) and f.endswith(".bak"))
        for f in baks[:-REPAIR_BACKUP_KEEP]:
            os.remove(os.path.join(bdir, f))
    except Exception:
        pass
    return True, dst


def repair_validate(orig_text, new_text, content_dir, need_ids):
    """
    写入前的校验（任何一项不过即拒写）：
      · 结构性不变量：花括号配平 / 表头 / 段间连接部 / 文件尾 / 安装段尾空白
      · 详情段：行多重集差分 —— 只允许删除下载计数行，不允许新增
      · 新增条目（need）：版本号与订阅详情一致、体积为正且与实测一致、内容健康
      · 原有条目：仅校验 size / timeupdated / manifest 三个关键字段未被改写
        （不与其硬盘体积做比较 —— 那些条目本次并未改动）
    与手工修复使用的是同一套验收思路，但实现独立于 strip 正则本身。
    """
    probs = []
    need_set = set(need_ids)
    if new_text.count("{") != new_text.count("}"):
        probs.append("花括号不配平")
    o_i = _section_span(orig_text, "WorkshopItemsInstalled")
    n_i = _section_span(new_text, "WorkshopItemsInstalled")
    o_d = _section_span(orig_text, "WorkshopItemDetails")
    n_d = _section_span(new_text, "WorkshopItemDetails")
    if not (o_i and n_i and o_d and n_d):
        probs.append("段落定位失败")
        return False, probs
    nl = "\r\n" if "\r\n" in orig_text else "\n"
    if new_text[:n_i[2]] != orig_text[:o_i[2]]:
        probs.append("表头（安装段之前）被改动")
    if new_text[n_i[1] + 1:n_d[0]] != orig_text[o_i[1] + 1:o_d[0]]:
        probs.append("段落连接部被改动")
    if new_text[n_d[1]:] != orig_text[o_d[1]:]:
        probs.append("文件尾被改动")
    o_body = orig_text[o_i[0]:o_i[1]]
    n_body = new_text[n_i[0]:n_i[1]]
    o_kept = o_body[:len(o_body.rstrip())]
    o_trail = o_body[len(o_body.rstrip()):]
    if not n_body.startswith(o_kept):
        probs.append("安装段原有内容被改动（旧条目必须逐字节保留）")
    added = n_body[len(o_kept):]
    if o_trail and not added.endswith(o_trail):
        probs.append("安装段尾空白被改动")
    added_wo = added[:-len(o_trail)] if o_trail else added

    # 详情段：行多重集差分（独立于 strip 正则）
    # 用 \r?\n 拆行：同一份账本混用 CRLF/LF 时（Steam 偶尔会这样），按全文 nl 拆
    # 会把 CRLF 行的 \r 留在行尾，导致"明明清掉了却说仍有残留"。
    old_c = Counter(re.split(r'\r?\n', orig_text[o_d[0]:o_d[1]]))
    new_c = Counter(re.split(r'\r?\n', new_text[n_d[0]:n_d[1]]))
    removed = old_c - new_c
    added_lines = new_c - old_c
    if added_lines:
        probs.append("详情段出现新增行")
    bad_removed = [ln for ln in removed
                   if not re.fullmatch(r'\t+"Bytes(?:Downloaded|ToDownload)"\t+"-?\d+"', ln)]
    if bad_removed:
        probs.append("详情段删除了非计数行")
    if re.search(r'"Bytes(?:Downloaded|ToDownload)"\s+"-?\d+"', new_text[n_d[0]:n_d[1]]):
        probs.append("详情段仍有下载计数残留")

    inst_new = _parse_items_precise(new_text[n_i[0]:n_i[1]])
    det_new = _parse_items_precise(new_text[n_d[0]:n_d[1]])
    inst_old = _parse_items_precise(orig_text[o_i[0]:o_i[1]])
    expect_n = len(set(inst_old) | need_set)
    if len(inst_new) != expect_n:
        probs.append("已安装条目数 %d，预期 %d" % (len(inst_new), expect_n))

    # 新增块的结构化校验：逐块完全匹配、连续、无重复、无多余内容
    if need_set:
        block_pat = re.compile(
            r'\t\t"(\d+)"' + re.escape(nl) + r'\t\t\{' + re.escape(nl)
            + r'\t\t\t"size"\t\t"(\d+)"' + re.escape(nl)
            + r'\t\t\t"timeupdated"\t\t"(\d+)"' + re.escape(nl)
            + r'\t\t\t"manifest"\t\t"(\d+)"' + re.escape(nl) + r'\t\t\}')
        if not added_wo.startswith(nl):
            probs.append("新增条目分隔异常")
        else:
            rest = added_wo[len(nl):]
            pos = 0
            seen = []
            while pos < len(rest):
                m = block_pat.match(rest, pos)
                if not m:
                    probs.append("新增条目格式异常")
                    break
                seen.append(m)
                pos = m.end()
                if pos < len(rest):
                    if rest.startswith(nl, pos):
                        pos += len(nl)
                    else:
                        probs.append("新增条目分隔异常")
                        break
            got = set(m.group(1) for m in seen)
            if len(seen) != len(need_set) or got != need_set:
                probs.append("新增条目集合与预期不符（%d/%d）" % (len(seen), len(need_set)))

    # 新增条目：字段合法 + 内容健康 + 体积实测
    for fid in sorted(need_set, key=int):
        nkv = inst_new.get(fid)
        if not nkv:
            probs.append("新增条目 %s 缺失" % fid)
            continue
        dkv = det_new.get(fid)
        if not dkv:
            probs.append("条目 %s 不在订阅详情段中" % fid)
            continue
        m_inst = nkv.get("manifest", "")
        m_det = dkv.get("manifest", "")
        m_lat = dkv.get("latest_manifest", m_det)
        if not m_inst or m_inst not in (m_det, m_lat):
            probs.append("条目 %s 版本号异常" % fid)
        expect_tu = dkv.get("timeupdated") or dkv.get("latest_timeupdated") or "0"
        if nkv.get("timeupdated") != expect_tu:
            probs.append("条目 %s 时间戳异常" % fid)
        folder = os.path.join(content_dir, fid)
        ok_h, why = _folder_healthy(folder)
        if not ok_h:
            probs.append("条目 %s 内容不完整（%s）" % (fid, why))
            continue
        errs = []
        real = dir_size(folder, errs)
        if errs:
            probs.append("条目 %s 体积统计失败（文件被占用或权限不足）" % fid)
        else:
            sz = _to_int(nkv.get("size", ""))
            if sz is None or sz <= 0 or sz != real:
                probs.append("条目 %s 体积与实测不符" % fid)
    return (not probs), probs


RE_LEDGER_BLOCK = re.compile(RE_LEDGER_BLOCK_SRC)


def _remove_ledger_blocks(text, del_ids):
    """从账本两段中删除指定 ID 的条目块（连同行首换行一起删）。
    返回 (new_text, removed_count, 错误说明)；removed_count 按 ID 计（两段同 ID 只算一次）。"""
    out = text
    removed_ids = set()
    for seg in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        span = _section_span(out, seg)
        if not span:
            return None, 0, "账本缺少「%s」段（结构异常）" % seg
        body = out[span[0]:span[1]]
        present = set(re.findall(RE_ITEM, body)) & set(del_ids)
        if not present:
            continue
        blocks = [(m.start(), m.end(), m.group(1))
                  for m in RE_LEDGER_BLOCK.finditer(body) if m.group(1) in present]
        if len(blocks) != len(present):
            # 两种失败要分开说：块数与目标数不符通常是「同段出现重复条目」，
            # 此时 present - 块 ID 是空集，按老的写法会报出「0 个目标定位不到」这种空话。
            dup = sorted(i for i, c in Counter(b[2] for b in blocks).items() if c > 1)
            if dup:
                return None, 0, ("段「%s」里 %s 出现了重复条目（同一 ID 多个块），"
                                 "账本结构异常，已中止；请让 Steam 校验或从备份恢复"
                                 % (seg, "、".join(dup)))
            missing = sorted(present - {b[2] for b in blocks})
            return None, 0, ("段「%s」中 %d 个目标无法按标准块格式定位：%s"
                             % (seg, len(missing), "、".join(missing)))
        nb = body
        for s, e, fid in sorted(blocks, reverse=True):
            nb = nb[:s] + nb[e:]
            removed_ids.add(fid)
        out = out[:span[0]] + nb + out[span[1]:]
    return out, len(removed_ids), ""


def _validate_deletions(before, after, del_ids):
    """删除类改动的写入前校验：目标在两段里都消失，段外逐字节不变，
    其余条目可原位还原；且必须真的删掉了东西（空改动不许写盘）。"""
    probs = []
    want = set(del_ids)
    if after == before:
        probs.append("没有发生任何删除（目标已不存在？）")
    if after.count("{") != after.count("}"):
        probs.append("花括号不配平")
    spans_b, spans_a = [], []
    for seg in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        sb = _section_span(before, seg)
        sa = _section_span(after, seg)
        if not (sb and sa):
            probs.append("段「%s」定位失败" % seg)
        spans_b.append(sb)
        spans_a.append(sa)
    # 段外三段（表头 / 两段之间 / 详情段之后）必须逐字节不变 —— 与
    # actions._ledger_validate 同一口径，避免两条删除路径强弱不一
    if spans_b[0] and spans_a[0] and after[:spans_a[0][2]] != before[:spans_b[0][2]]:
        probs.append("表头（安装段之前）被改动")
    if (spans_b[0] and spans_a[0] and spans_b[1] and spans_a[1]
            and after[spans_a[0][1] + 1:spans_a[1][0]]
            != before[spans_b[0][1] + 1:spans_b[1][0]]):
        probs.append("段落连接部被改动")
    if (spans_b[1] and spans_a[1]
            and after[spans_a[1][1]:] != before[spans_b[1][1]:]):
        probs.append("文件尾被改动")
    for seg in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        sb = _section_span(before, seg)
        sa = _section_span(after, seg)
        if not (sb and sa):
            continue
        bb = before[sb[0]:sb[1]]
        ba = after[sa[0]:sa[1]]
        hit = set(re.findall(RE_ITEM, bb)) & want
        if set(re.findall(RE_ITEM, ba)) & want:
            probs.append("段「%s」仍含目标条目" % seg)
        if set(re.findall(RE_ITEM, ba)) != (set(re.findall(RE_ITEM, bb)) - hit):
            probs.append("段「%s」除目标外还有条目增减" % seg)
        # 把删掉的块按原偏移插回去，必须与原文完全一致
        chunks = [(m.start(), bb[m.start():m.end()])
                  for m in RE_LEDGER_BLOCK.finditer(bb) if m.group(1) in hit]
        chk = ba
        for s, ch in sorted(chunks):
            chk = chk[:s] + ch + chk[s:]
        if chk != bb:
            probs.append("段「%s」除目标外还有字节级改动" % seg)
    return (not probs), probs


def repair_apply(P, plan, ui=None):
    """执行重建：补录缺失记录 + 清理下载计数残留。返回 (ok, 说明)"""
    def say(s):
        if ui:
            ui(s)

    acf = P.get("acf")
    content = P.get("content")
    if not acf or not content:
        return False, "关键路径缺失，已中止"
    need = set(plan.get("need") or [])
    say("[修复] 读取账本原文……")
    try:
        orig = _read_exact(acf)
    except Exception as e:
        return False, "读取账本失败：%s" % e
    nl = "\r\n" if "\r\n" in orig else "\n"
    ii = _section_span(orig, "WorkshopItemsInstalled")
    di = _section_span(orig, "WorkshopItemDetails")
    if not (ii and di):
        return False, "账本结构异常（找不到必要段落），已中止"
    installed = _parse_items_precise(orig[ii[0]:ii[1]])
    details = _parse_items_precise(orig[di[0]:di[1]])
    # 计划到执行之间账本可能被外部改写：已经在账本里的条目不要再补一次，
    # 否则同一 ID 会在安装段出现两个块（set/dict 计数看不见，但后续删除会整体中止）。
    stale = set(need) & set(installed)
    if stale:
        need -= stale
        say("[修复] %d 条在计划后被写入账本，已自动跳过：%s"
            % (len(stale), "、".join(sorted(stale, key=int)[:5])))

    # 旧条目原样保留（逐字节不重写），只为 need 条目新增块
    blocks = []
    for fid in sorted(need, key=int):
        dkv = details.get(fid)
        if not dkv:
            return False, "条目 %s 缺少订阅详情，已中止" % fid
        folder = os.path.join(content, fid)
        ok_h, why = _folder_healthy(folder)
        if not ok_h:
            return False, ("条目 %s 内容不完整（%s），已中止 —— "
                           "这类条目应由 Steam 重新下载，不能补录" % (fid, why))
        errs = []
        sz = dir_size(folder, errs)
        if errs:
            return False, "条目 %s 体积统计失败（文件被占用或权限不足），已中止" % fid
        manifest = dkv.get("manifest") or dkv.get("latest_manifest") or ""
        if not manifest:
            return False, "条目 %s 缺少版本号，已中止" % fid
        blocks.append((fid, str(sz),
                       dkv.get("timeupdated") or dkv.get("latest_timeupdated") or "0",
                       manifest))

    o_body = orig[ii[0]:ii[1]]
    o_kept = o_body[:len(o_body.rstrip())]      # 到最后一个 "}" 为止：旧条目原样
    trail = o_body[len(o_body.rstrip()):]       # 通常为 nl + "\t"（闭合花括号前的缩进）
    tpl = ('\t\t"%s"' + nl + "\t\t{" + nl + '\t\t\t"size"\t\t"%s"' + nl
           + '\t\t\t"timeupdated"\t\t"%s"' + nl + '\t\t\t"manifest"\t\t"%s"' + nl
           + "\t\t}")
    new_body = o_kept
    if blocks:
        new_body += nl + nl.join(tpl % b for b in blocks)
    new_body += trail
    new_text = orig[:ii[0]] + new_body + orig[ii[1]:]

    d2 = _section_span(new_text, "WorkshopItemDetails")
    # 行尾不跟全文件的 nl 走：同一份账本混用 CRLF/LF 时（Steam 偶尔会这样），
    # 按 nl 拼的正则清不掉 LF 结尾的那几行，最后会被校验判成"仍有残留"，
    # 用户重试多少次都失败，提示还指向错误的原因。
    strip_pat = r'\t+"Bytes(?:Downloaded|ToDownload)"\t+"-?\d+"[ \t]*\r?\n'
    det_clean, n_strip = re.subn(strip_pat, "", new_text[d2[0]:d2[1]])
    new_text = new_text[:d2[0]] + det_clean + new_text[d2[1]:]
    _acts = []
    if need:
        _acts.append("补录 %d 条" % len(need))
    if n_strip:
        _acts.append("清理下载计数残留 %d 行" % n_strip)
    if not _acts and not (plan.get("need_del") or []):
        # 计划到执行之间账本已被 Steam 更新（要补的都已在账本里、也没有要删的）：
        # 不写盘，也不要把"修复成功："后面接一个空串。
        return False, "没有需要写入的改动（账本可能已被 Steam 更新），本次未改动账本"
    say("[修复] 账本重建完成：%s" % ("，".join(_acts) if _acts else "无需改动安装段"))

    say("[修复] 正在校验（含逐条核对体积，可能需要几秒）……")
    ok, probs = repair_validate(orig, new_text, content, need)
    if not ok:
        say("[修复] 校验未通过：" + "；".join(probs[:6]))
        return False, "写入前校验未通过，已中止"

    # 删除方向（类型 6）：补录结果先过校验，再做删除并单独校验
    need_del = list(plan.get("need_del") or [])
    final_text = new_text
    n_del = 0
    if need_del:
        final_text, n_del, err = _remove_ledger_blocks(new_text, need_del)
        if err:
            return False, "删除残留记录失败：%s" % err
        if not n_del:
            # 计划后被 Steam 改写/手工清理：什么都不删就不写盘，避免「空跑一次修复」
            return False, ("待删记录已不存在（可能已被 Steam 改写），"
                           "未做任何改动；请重新排查后再试")
        ok2, probs2 = _validate_deletions(new_text, final_text, need_del)
        if not ok2:
            say("[修复] 删除校验未通过：" + "；".join(probs2[:6]))
            return False, "写入前校验未通过，已中止"
        say("[修复] 已移除 %d 条残留记录（未订阅且硬盘无文件）" % n_del)

    tmp = acf + ".repair_tmp"
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(final_text)
            f.flush()
            os.fsync(f.fileno())
        if _read_exact(tmp) != final_text:
            raise RuntimeError("临时文件回读不一致")
        os.replace(tmp, acf)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False, "写入失败：%s" % e
    say("[修复] 写入完成 ✓")
    parts = []
    if need:
        parts.append("补录 %d 条记录" % len(need))
    if need_del:
        parts.append("移除 %d 条残留记录" % len(need_del))
    return True, "修复成功：%s，账本已更新" % "、".join(parts)


def _repair_log(P, plan, bak):
    """追加一行修复记录（审计用）"""
    try:
        bdir = os.path.join(BASE_DIR, "backups")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, REPAIR_LOG_NAME), "a", encoding="utf-8") as f:
            f.write("%s  补录 %d 条  移除 %d 条  跳过 %d 条(内容不完整)  备份=%s  账本=%s\n"
                    % (now_str(), len(plan.get("need") or []),
                       len(plan.get("need_del") or []),
                       len(plan.get("skipped") or []),
                       os.path.basename(bak) if bak else "无", P.get("acf")))
    except Exception:
        pass


def repair_run(P, D, ui=None):
    """
    修复总入口：前置检查 → 备份 → 重建 → 校验 → 写入；失败时兜底回滚。
    返回 (ok, 说明, 备份路径)。任何一步不满足即安全中止。
    """
    def say(s):
        if ui:
            ui(s)

    ok, msg = repair_precheck(P)
    if not ok:
        return False, msg, None

    plan = repair_plan(P, D)
    if not plan["ok"]:
        return False, "未执行修复：%s" % plan["reason"], None
    n_add = len(plan.get("need") or [])
    n_del = len(plan.get("need_del") or [])
    if plan.get("warn"):
        say("[修复] ⚠ " + plan["warn"])
    if n_add:
        say("[修复] 待补录 %d 个（文件都在硬盘上、内容健康，只是账本漏登）" % n_add)
    if n_del:
        say("[修复] 待移除 %d 条残留记录（已不订阅、硬盘也没有文件，属高危残尸态）" % n_del)
    if plan.get("skipped"):
        say("[修复] 跳过 %d 个内容不完整的条目（需由 Steam 重新下载）" % len(plan["skipped"]))

    okb, bak = repair_backup(P)
    if not okb:
        return False, bak, None
    say("[修复] 账本已备份 → %s" % bak)

    ok, msg = repair_apply(P, plan, ui=ui)
    if not ok:
        bak_path = bak if isinstance(bak, str) else None
        try:
            raw = _read_exact(P["acf"])
            intact = bool(_section_span(raw, "WorkshopItemsInstalled")) and \
                     bool(_section_span(raw, "WorkshopItemDetails"))
        except Exception:
            intact = False
        if not intact and bak_path:
            try:
                shutil.copy2(bak_path, P["acf"])
                return False, msg + "；账本疑似受损，已从备份自动还原", bak_path
            except Exception as e:
                return False, (msg + "；⚠ 自动还原失败（%s），请手动用备份恢复：%s"
                               % (e, bak_path)), bak_path
        return False, msg + "（账本未被改动）", bak_path
    _repair_log(P, plan, bak)
    return True, msg, bak
