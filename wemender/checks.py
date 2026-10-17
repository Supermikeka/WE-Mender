# -*- coding: utf-8 -*-
"""检查模块与注册表（WE-Mender 3.0 模块化组件）"""

import datetime
import os
import re

from wemender.common import (APPID, RE_BLOCK, RE_ITEM, brief, cached_dir_size,
                             human, isdir, isfile, parse_subs_ids, read_text,
                             running_procs)

# ══════════════════════════════════════════════════════════════
#  检查模块
# ══════════════════════════════════════════════════════════════

def chk_env(P, D):
    """环境检查"""
    procs = running_procs()
    if procs is None:                      # 检测失败，三项都标记为未知
        D["procs"] = None
        D["steam_running"] = D["we_running"] = D["booster_running"] = None
        return []
    D["procs"] = procs
    D["steam_running"] = any("steam.exe" == p for p in procs)
    D["we_running"] = any(p.startswith("wallpaper") for p in procs)
    D["booster_running"] = any("steam++" in p or "watt" in p for p in procs)
    return []


def chk_index(P, D):
    """索引完整性：账本文件能否解析、两段条目数、安装记录是否为空"""
    issues = []
    acf = P.get("acf")
    if not isfile(acf):
        D["acf_ok"] = False
        # 明确标记「不可用」（None），而不是留下不存在的键让下游误取空集
        D["installed_ids"] = None
        D["details_ids"] = None
        D["installed_count"] = None
        D["details_count"] = None
        D["size_on_disk"] = None
        issues.append(dict(
            level="严重", title="找不到创意工坊账本文件",
            symptom="Steam 无法判断任何壁纸的安装状态",
            cause="账本文件 appworkshop_%s.acf 不存在" % APPID,
            impact="所有创意工坊壁纸都会显示为未安装",
            who="AI 工具", risk="中 · 动手前必须备份",
            detail="预期路径：%s" % acf))
        return issues

    P["read_files"].append(acf)
    raw = read_text(acf)
    D["acf_ok"] = True

    def unreadable(title, cause):
        """账本文件存在但读不出内容：标记为不可用，且不谎称「记录被清空」"""
        D["installed_ids"] = D["details_ids"] = None
        D["installed_count"] = D["details_count"] = None
        D["size_on_disk"] = None
        issues.append(dict(
            level="严重", title=title,
            symptom="Steam 无法判断任何壁纸的安装状态",
            cause=cause, impact="所有创意工坊壁纸都会显示为未安装",
            who="AI 工具（从备份恢复，或让 Steam 重建）", risk="中 · 动手前必须备份",
            detail="文件路径：%s" % acf))

    if not raw.strip():
        return issues + [dict(
            level="严重", title="账本文件是空的",
            symptom="Steam 无法判断任何壁纸的安装状态",
            cause="账本文件大小为 0 或内容为空，属于文件损坏",
            impact="所有创意工坊壁纸都会显示为未安装",
            who="AI 工具（从备份恢复，或让 Steam 重建）", risk="中 · 动手前必须备份",
            detail="文件路径：%s" % acf)]

    if '"WorkshopItemsInstalled"' not in raw and '"WorkshopItemDetails"' not in raw:
        unreadable("账本文件内容无法识别",
                   "文件存在，但里面找不到任何可识别的记录段（格式异常或已损坏）")
        return issues

    if '"WorkshopItemsInstalled"' in raw:
        inst_b = raw.split('"WorkshopItemsInstalled"', 1)[1]
        inst_b = inst_b.split('"WorkshopItemDetails"')[0] if '"WorkshopItemDetails"' in inst_b else inst_b
    else:
        inst_b = ""
    det_b = raw.split('"WorkshopItemDetails"', 1)[1] if '"WorkshopItemDetails"' in raw else ""

    inst = set(re.findall(RE_ITEM, inst_b))
    det = set(re.findall(RE_ITEM, det_b))
    D["installed_ids"] = inst
    D["details_ids"] = det
    D["installed_count"] = len(inst)
    D["details_count"] = len(det)

    m = re.search(r'"SizeOnDisk"\s+"(\d+)"', raw)
    D["size_on_disk"] = int(m.group(1)) if m else None

    if len(inst) == 0 and len(det) == 0:
        issues.append(dict(
            level="严重", title="账本两段记录均为空",
            symptom="壁纸全部显示「未安装」；创意工坊里壁纸大小显示 0",
            cause="「已安装记录」和「订阅详情」两段都是空的，账本整体失效",
            impact="所有已订阅壁纸都无法被识别，但硬盘上的壁纸文件通常还在",
            who="AI 工具（从备份恢复，或让 Steam 重新同步）",
            risk="中 · 动手前必须备份账本文件",
            detail="文件路径：%s" % acf))
    elif len(inst) == 0:
        issues.append(dict(
            level="严重", title="安装记录被清空",
            symptom="壁纸全部显示「未安装」或提示「下载失败，请重新下载」；创意工坊里壁纸大小显示 0",
            cause="账本文件里「已安装记录」这一段是空的（0 条），而订阅详情段有 %d 条" % len(det),
            impact="所有已订阅壁纸都无法被软件识别和使用，但硬盘上的壁纸文件其实还在",
            who="AI 工具（Steam 官方手段无法修复这一层，实测验证过）",
            risk="中 · 动手前必须备份账本文件",
            detail="这是一个典型的「本地记录丢失」，不是壁纸文件丢失"))
    elif len(inst) != len(det):
        gap = len(det) - len(inst)
        issues.append(dict(
            level=("严重" if gap >= 10 else "提醒"),
            title="安装记录与订阅详情数量不一致",
            symptom="部分壁纸可能显示异常",
            cause="已安装记录 %d 条，订阅详情 %d 条" % (len(inst), len(det)),
            impact=("缺口较大，Steam 可能把这些壁纸误判为未安装并要求重新下载"
                    if gap >= 10 else "数量对不上的那部分壁纸可能无法正常识别"),
            who="AI 工具（本工具可自动补录缺失记录）", risk="低 · 先备份再核对",
            detail=brief(sorted(inst ^ det), 20, "条")))
    return issues


def chk_cross(P, D):
    """三方比对：订阅账 × 安装账 × 硬盘账"""
    issues = []
    # 「本地已禁用」仍然属于订阅（只是在本机关掉了），必须并入订阅侧：
    # 否则这些壁纸会被判成「无主文件」，界面还会给出「转本地/删除」按钮，
    # 而动作层的守卫会拒绝执行 —— 同一份数据两套口径，用户只会觉得工具坏了。
    subs = (D.get("subs") or set()) | (D.get("subs_disabled") or set())
    dirs = D.get("dirs") or set()
    content = P.get("content")

    # 保护：工坊内容目录不存在时，无法做任何「硬盘侧」的比对。
    # 否则 dirs 恒为空集，会把全部订阅误报成「已订阅但硬盘上没有内容」。
    if not D.get("content_exists"):
        issues.append(dict(
            level="严重", title="找不到工坊内容目录，无法进行三方比对",
            symptom="无法判断壁纸文件是否还在",
            cause="所有游戏库中都没有找到 %s 目录" % os.path.join("steamapps", "workshop", "content", APPID),
            impact="跳过本次比对，避免把全部订阅误报成「硬盘上没有内容」",
            who="官方（确认 Steam 库设置）或 AI 工具（协助定位）", risk="低",
            detail="预期位置：%s" % (content or "路径未探测到")))
        D["sub_no_content"] = D["dir_no_sub"] = None
        D["record_no_dir"] = D["dir_no_record"] = None
        D["orphan_size"] = None
        D["cross_skip"] = "工坊内容目录不存在"
        return issues

    # 保护：订阅列表读不到 / 只解析出一部分时，绝不做「孤儿」判定，
    # 否则会把整个目录误判成无主文件（用子集去判，漏掉的那些都会变成"未订阅"）
    state = D.get("subs_state", "missing")
    if not subs or state == "invalid":
        if state == "empty":
            # 文件在、解析也成功，只是确实一条订阅都没有 —— 这是有效状态，不是故障
            D["sub_no_content"] = []
            D["dir_no_sub"] = sorted(dirs)
            D["orphan_size"] = None
            D["record_no_dir"] = D["dir_no_record"] = None
            D["cross_skip"] = "订阅列表为空，无从比对"
            if dirs:
                issues.append(dict(
                    level="提醒", title="当前没有任何创意工坊订阅，但硬盘上有 %d 个文件夹" % len(dirs),
                    symptom="软件里不会出现任何创意工坊壁纸",
                    cause="订阅列表存在且可正常解析，里面一条订阅记录都没有",
                    impact="硬盘上这些文件夹不属于任何订阅，无法被软件识别",
                    who="用户确认（是否已全部取消订阅）", risk="低",
                    detail="如果这不是你主动取消的结果，请检查是否登录了错误的 Steam 账户"))
            return issues
        invalid = (state == "invalid")
        note = D.get("subs_parse_note")
        issues.append(dict(
            level="严重",
            title="订阅清单只解析出一部分，无法进行三方比对" if (invalid and note)
                  else ("订阅列表内容无法识别，无法进行三方比对" if invalid
                        else "读不到订阅列表，无法进行三方比对"),
            symptom="无法判断哪些壁纸还在订阅中",
            cause=(note + "，可能有条目字段格式不同；为避免把仍在订阅的壁纸当成"
                   "「已退订」，本次不做判定：%s" % (P.get("subs") or "路径未探测到"))
                  if (invalid and note)
                  else (("订阅列表文件存在，但内容不是可识别的格式"
                         "（可能已损坏，或编码异常）：%s" % (P.get("subs") or "路径未探测到")) if invalid
                        else ("订阅列表文件不存在：%s" % (P.get("subs") or "路径未探测到"))),
            impact="跳过本次比对。请先确认 Steam 已正常登录过该账户",
            who="官方（先让 Steam 正常启动并同步一次订阅）", risk="低",
            detail="为避免误判，本项检查已中止，不会把硬盘上的文件夹当成无主文件"))
        # 全部标记为「不可用」，避免在报告里显示成「无」（那是假数据）
        D["sub_no_content"] = D["dir_no_sub"] = None
        D["record_no_dir"] = D["dir_no_record"] = None
        D["orphan_size"] = None
        D["cross_skip"] = "订阅列表损坏" if invalid else "订阅列表文件不存在"
        return issues

    # 订阅账 × 硬盘账：这两项与账本无关，始终可比
    D["sub_no_content"] = sorted(subs - dirs)
    D["dir_no_sub"] = sorted(dirs - subs)

    # 安装账 × 硬盘账：依赖 installed_ids。
    # 若安装记录不可用（账本文件缺失、或本次模式未跑 index），
    # 绝不能拿空集去比对 —— 否则会把全部文件夹误判成「未被账本记录」。
    inst = D.get("installed_ids")
    if inst is None:
        D["record_no_dir"] = None
        D["dir_no_record"] = None
        issues.append(dict(
            level="提醒", title="安装记录不可用，三方比对只完成了一部分",
            symptom="无法判断「账本记录」与「硬盘文件」是否一一对应",
            cause="账本文件缺失或无法解析，导致「已安装记录」读取不到",
            impact="本次只完成了「订阅列表 × 硬盘文件」的比对；"
                   "「安装记录 × 硬盘文件」这一路已跳过，以免把全部文件夹误判成无主文件",
            who="AI 工具（先恢复账本文件，再重新排查）", risk="低",
            detail="这是保护性跳过，不代表硬盘上有异常文件"))
    else:
        D["record_no_dir"] = sorted(inst - dirs)
        if not inst:
            # 安装记录整体为空时，dir_no_record 必然等于全部文件夹，
            # 这与 chk_index 报的「安装记录被清空」是同源问题，此处不再重复报告
            D["dir_no_record"] = None
            D["dir_no_record_note"] = "已由「安装记录被清空」涵盖，不重复列出"
        else:
            D["dir_no_record"] = sorted(dirs - inst)

    if D["dir_no_sub"]:
        n = len(D["dir_no_sub"])
        sz = sum(cached_dir_size(D, os.path.join(content, d)) for d in D["dir_no_sub"])
        D["orphan_size"] = sz
        issues.append(dict(
            level="提醒" if n < 50 else "严重",
            title="存在 %d 个无主文件夹（%s）" % (n, human(sz)),
            symptom="暂时可能没有直接表现，但这是问题反复发作的根源",
            cause="这些文件夹不在当前订阅列表里（多半是以前取消订阅后残留的），"
                  "但 Steam 账本上已经没有它们，属于「账本为空 + 硬盘有货」的不一致状态",
            impact="Steam 每次做完整检查时，遇到这种不一致就可能把安装记录写成空表，"
                   "导致刚修好的问题再次复发",
            who="AI 工具",
            risk="低 · 只需要移动，不需要删除，随时可以移回",
            detail=brief(D["dir_no_sub"], 10)))
    else:
        D["orphan_size"] = 0

    if D["record_no_dir"]:
        issues.append(dict(
            level="严重", title="有 %d 条记录指向不存在的文件夹" % len(D["record_no_dir"]),
            symptom="对应壁纸显示异常或提示需要重新下载",
            cause="账本上记录了这些壁纸已安装，但硬盘上找不到对应文件夹",
            impact="这部分壁纸不可用，需要重新下载或修正记录",
            who="官方（重新下载）或 AI 工具（修正记录）", risk="低",
            detail=brief(D["record_no_dir"], 20)))

    # 去重：孤儿（未订阅）已在「无主文件夹」里报过，不再重复算作「缺记录」
    _orphans = set(D.get("dir_no_sub") or [])
    _rec_only = [f for f in (D["dir_no_record"] or []) if f not in _orphans]
    if _rec_only:
        issues.append(dict(
            level="提醒", title="有 %d 个文件夹未被账本记录" % len(_rec_only),
            symptom="这些壁纸不会出现在软件里",
            cause="硬盘上有文件夹，但账本里没有对应记录",
            impact="这些壁纸无法被识别",
            who="AI 工具", risk="低 · 先备份再处理",
            detail=brief(_rec_only, 10)))

    # 去重：同一 ID 若已因「有记录没文件」报过（类型 2），不再重复算作「已订阅没内容」
    _rec_missing = set(D.get("record_no_dir") or [])
    _sub_only = [i for i in (D["sub_no_content"] or []) if i not in _rec_missing]
    if _sub_only:
        issues.append(dict(
            level="提醒", title="有 %d 项已订阅但硬盘上没有内容" % len(_sub_only),
            symptom="这些壁纸不会出现在软件里",
            cause="订阅关系存在，但本地没有下载到文件。可能是尚未下载，也可能是该物品已被作者删除或下架",
            impact="对应壁纸不可用",
            who="官方（触发下载）或 AI 工具（判断物品是否已下架）", risk="低",
            detail=brief(_sub_only, 20, "项")))
    return issues


def chk_content(P, D):
    """内容健康：每个壁纸文件夹是否有 project.json，引用的文件是否存在"""
    issues = []
    content = P.get("content")
    if not isdir(content):
        D["content_ok"] = None          # 明确标记不可用，避免显示成「0 个」
        D["bad_project"] = None
        D["bad_ref"] = None
        return issues

    import json
    bad_proj, bad_ref, ok_cnt = [], [], 0
    for d in sorted(D.get("dirs", set())):
        p = os.path.join(content, d)
        pj = os.path.join(p, "project.json")
        if not isfile(pj):
            bad_proj.append(d)
            continue
        try:
            j = json.loads(read_text(pj))
        except Exception:
            bad_proj.append(d)
            continue
        if not isinstance(j, dict):
            # 合法 JSON 但不是对象（[] / "x" / 123）：下载不完整时的典型产物。
            # 这里若不拦，j.get 会抛 AttributeError，整个「内容健康」检查集体失效。
            bad_proj.append(d)
            continue
        ref = j.get("file", "")
        if not isinstance(ref, str) or not ref.strip():
            # 与 repair._folder_healthy 同口径：主文件字段缺失/为空 = 不健康。
            # 以前这里算"健康"，于是同一份报告会说"内容全部正常"、
            # 同时自动修复又跳过它（内容不完整），用户无从判断。
            bad_ref.append("%s → （project.json 未写主文件字段）" % d)
            continue
        ref_path = os.path.join(p, ref)
        if not isfile(ref_path):
            # WE 的实际加载规则：file 指向的源文件（如 scene.json）缺失时，
            # 场景类壁纸会回退加载编译包（scene.pkg）——订阅下载的壁纸通常
            # 只含编译包，属正常形态，不算损坏
            import glob as _glob
            if not _glob.glob(os.path.join(p, "*.pkg")):
                bad_ref.append("%s → %s" % (d, ref))
            else:
                ok_cnt += 1
        else:
            try:
                if os.path.getsize(ref_path) <= 0:
                    bad_ref.append("%s → %s（0 字节）" % (d, ref))
                    continue
            except OSError:
                bad_ref.append("%s → %s（无法读取）" % (d, ref))
                continue
            ok_cnt += 1

    D["bad_project"] = bad_proj
    D["bad_ref"] = bad_ref
    D["content_ok"] = ok_cnt

    if bad_proj:
        issues.append(dict(
            level="提醒", title="有 %d 个壁纸文件夹缺少或损坏 project.json" % len(bad_proj),
            symptom="这些壁纸无法被软件加载",
            cause="文件夹里没有 project.json，或文件无法解析（多为下载未完成）",
            impact="对应壁纸不可用，通常需要重新下载",
            who="官方（重新下载）", risk="低",
            detail=brief(bad_proj, 20)))

    if bad_ref:
        issues.append(dict(
            level="严重", title="有 %d 个壁纸引用的主文件缺失且无编译包" % len(bad_ref),
            symptom="壁纸显示为黑屏或加载失败",
            cause="project.json 里指向的主文件在文件夹里找不到，也没有可回退的编译包（多为下载未完成）",
            impact="这些壁纸无法正常播放",
            who="官方（重新下载）", risk="低",
            detail=brief(bad_ref, 20, "项")))
    return issues


def chk_size(P, D):
    """大小校验：账本记录的 size vs 硬盘实际大小"""
    issues = []
    acf = P.get("acf")
    content = P.get("content")
    if not isfile(acf) or not isdir(content):
        D["size_mismatch"] = None       # 明确标记不可用，避免显示成「0 项」
        D["size_total_record"] = None
        return issues

    raw = read_text(acf)
    inst_b = raw.split('"WorkshopItemsInstalled"', 1)[1] if '"WorkshopItemsInstalled"' in raw else ""
    if '"WorkshopItemDetails"' in inst_b:
        inst_b = inst_b.split('"WorkshopItemDetails"')[0]

    mismatch, total_rec = [], 0
    for fid, body in re.findall(RE_BLOCK, inst_b, re.S):
        m = re.search(r'"size"\s+"(\d+)"', body)
        rec = int(m.group(1)) if m else 0
        total_rec += rec
        p = os.path.join(content, fid)
        if not isdir(p):
            continue
        actual = cached_dir_size(D, p)
        if abs(actual - rec) > 50 * 1024 * 1024:
            mismatch.append((fid, rec, actual))

    D["size_mismatch"] = mismatch
    D["size_total_record"] = total_rec

    if mismatch:
        issues.append(dict(
            level="提示", title="有 %d 项的大小记录与硬盘实际不符" % len(mismatch),
            symptom="一般无直接影响",
            cause="账本记录的字节数与文件夹实际占用不一致，通常是文件夹里多了非壁纸文件"
                  "（例如自己放进去的压缩包），或者下载不完整",
            impact="仅影响创意工坊页显示的大小数字，不影响壁纸使用",
            who="无需处理，或由 AI 工具协助核对", risk="低",
            detail=brief(["%s 记录 %s / 实际 %s" % (f, human(r), human(a))
                          for f, r, a in mismatch], 8, "项")))
    return issues


def chk_archive(P, D):
    """冗余文件扫描"""
    issues = []
    content = P.get("content")
    if not isdir(content):
        D["archive_hits"] = None        # 明确标记不可用
        D["empty_dirs"] = None
        D["archive_total"] = None
        return issues

    exts = (".zip", ".rar", ".7z", ".tar", ".gz", ".001", ".part")
    hits, empty = [], []
    for d in sorted(D.get("dirs", set())):
        p = os.path.join(content, d)
        try:
            entries = os.listdir(p)
        except OSError:
            continue
        if not entries:
            empty.append(d)
            continue
        for f in entries:
            if f.lower().endswith(exts):
                fp = os.path.join(p, f)
                try:
                    hits.append((os.path.getsize(fp), d, f))
                except OSError:
                    pass

    hits.sort(reverse=True)
    D["archive_hits"] = hits
    D["empty_dirs"] = empty
    D["archive_total"] = sum(h[0] for h in hits)

    if hits:
        issues.append(dict(
            level="提示", title="发现 %d 个压缩包类文件（%s）" % (len(hits), human(D["archive_total"])),
            symptom="无直接表现",
            cause="这些不是 Steam 下载的壁纸文件，多半是自己下载的原始资源包或备份",
            impact="占用磁盘空间；也可能让账本记录的大小与实际不符",
            who="用户自行判断是否删除", risk="低 · 删除前请确认不再需要",
            detail=brief(["%s (%s)" % (f, human(s)) for s, _, f in hits], 5)))
    if empty:
        issues.append(dict(
            level="提醒", title="有 %d 个空文件夹" % len(empty),
            symptom="无直接表现",
            cause="下载中断或手动清理留下的空目录",
            impact="基本无影响，可清理",
            who="用户或 AI 工具", risk="低",
            detail=brief(empty, 20)))
    return issues


def chk_logs(P, D):
    """日志扫描：最近 24 小时内的异常"""
    issues = []
    log = P.get("content_log")
    if not isfile(log):
        D["log_hits"] = None            # 明确标记不可用，避免显示成「无异常」
        return issues
    P["read_files"].append(log)

    raw = read_text(log)
    lines = raw.splitlines()
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)

    # 只扫最近 N 行以控制耗时，但「全部解析、不提前 break」——
    # 一旦日志时间戳乱序，提前 break 会静默漏掉后面真正的新记录。
    MAX_SCAN = 30000
    truncated = len(lines) > MAX_SCAN
    scan = lines[-MAX_SCAN:] if truncated else lines

    hits = []
    for ln in scan:
        m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", ln)
        if not m:
            continue
        try:
            t = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if t < cutoff or APPID not in ln:
            continue
        low = ln.lower()
        # 排除误报：Steam 日志里大量正常行也含 error/fail 字样
        if "no error" in low or "result no error" in low:
            continue
        if "authenticatedepotid" in low and "success" in low:
            continue
        if "manifest request received 200" in low:
            continue
        if "connection has been idle" in low or "closing connection" in low:
            continue
        if "scheduler update" in low or "scheduler finished" in low:
            continue
        if re.search(r"failed to|failure|corrupt|missing|invalid|cannot|unable|error:", low):
            hits.append(ln.strip())

    D["log_hits_total"] = len(hits)     # 命中总数（标题用它）
    D["log_hits"] = hits[-15:]          # 详细数据只保留最后 15 条
    D["log_scan_truncated"] = truncated
    if hits:
        net_only = all(("http (" in h.lower()) or ("manifest request" in h.lower()) for h in hits)
        if net_only:
            issues.append(dict(
                level="提示", title="日志里有 %d 条网络类重试记录" % len(hits),
                symptom="一般无影响",
                cause="Steam 请求内容清单时出现瞬时失败（received 0 / Invalid），随后自动重试成功",
                impact="属于网络抖动，Steam 通常能自行恢复，一般无需处理",
                who="无需处理",
                risk="低",
                detail=brief([h.split("] ", 1)[-1][:80] for h in hits], 3, "条", tail=True)))
        else:
            issues.append(dict(
                level="提醒", title="日志里发现 %d 条相关异常" % len(hits),
                symptom="可能对应某些壁纸加载异常",
                cause="Steam 内容日志中与创意工坊相关的报错",
                impact="需要结合具体报错判断",
                who="AI 工具", risk="低",
                detail=brief([h.split("] ", 1)[-1][:90] for h in hits], 5, "条", tail=True)))
    return issues


def scan_basic(P, D):
    """
    基础扫描：硬盘文件夹列表 + 订阅列表。
    这两项是其他所有检查的共同依赖，且开销很小，因此无论选哪种模式都会执行，
    避免下游模块拿不到数据时输出 0 之类的假数据。
    """
    content = P.get("content")
    D["dirs"] = set()          # 只收「纯数字命名」的目录 = 真正的工坊内容
    D["non_id_dirs"] = set()   # 非数字命名的目录 = 不是 Steam 工坊内容，单独提示
    D["content_exists"] = isdir(content)
    D["content_list_failed"] = False
    if D["content_exists"]:
        try:
            for d in os.listdir(content):
                if not isdir(os.path.join(content, d)):
                    continue
                (D["dirs"] if d.isdigit() else D["non_id_dirs"]).add(d)
        except OSError:
            D["content_list_failed"] = True

    # 订阅列表：三态之外再区分「格式无法识别」，避免把损坏文件当成「真的没订阅」
    #   missing = 文件不存在   invalid = 文件存在但格式不认   empty = 格式正常但没订阅   ok = 正常
    D["subs"] = set()
    D["subs_disabled"] = set()       # 本地已禁用的订阅项
    D["subs_state"] = "missing"
    D["subs_total"] = 0              # 订阅总数（含本地已禁用）
    subs_f = P.get("subs")
    if isfile(subs_f):
        if subs_f not in P["read_files"]:
            P["read_files"].append(subs_f)
        raw = read_text(subs_f)
        if '"publishedfileid"' not in raw and '"subscribedfiles"' not in raw:
            D["subs_state"] = "invalid"
        elif raw.count("{") != raw.count("}"):
            # 花括号不配平 = 文件被截断（Steam 崩溃/被杀）。不能当成"合法零订阅"，
            # 否则每组检查都会说"用户全退订了"，与账本/硬盘规模矛盾。
            D["subs_state"] = "invalid"
            D["subs_parse_note"] = "订阅清单不完整（花括号不配平，疑似写入中断）"
        else:
            # 解析口径统一在 common.parse_subs_ids（逐条扫描，不假设字段顺序），
            # 检查 / 单条动作 / 自动修复三条路径共用同一份判定。
            subs, disabled, note = parse_subs_ids(raw)
            D["subs"], D["subs_disabled"] = subs, disabled
            # 对账：文件里有多少条 publishedfileid，就必须认出多少条。
            # 只认出子集时（字段顺序/字段增删导致漏解析）不能判 ok ——
            # 否则漏掉的条目会被自动修复当成「未订阅」，把正常记录删掉。
            D["subs_total"] = len(subs) + len(disabled)
            if note:
                # 有 publishedfileid 字样却认不出（或只认出一部分）→ 格式不可信。
                # 必须先判 note 再判 0：否则"一条都没认出来"会被当成合法零订阅。
                D["subs_state"] = "invalid"
                D["subs_parse_note"] = note
            elif not D["subs_total"]:
                D["subs_state"] = "empty"
            else:
                D["subs_state"] = "ok"
    return []


CHECKS = {
    "env": chk_env,
    "index": chk_index,
    "cross": chk_cross,
    "content": chk_content,
    "size": chk_size,
    "archive": chk_archive,
    "logs": chk_logs,
}

MODES = {
    "0": ("一键排查（全部检查）", ["env", "index", "cross", "content", "size", "archive", "logs"]),
    "1": ("壁纸不显示 / 提示下载失败", ["env", "index", "cross"]),
    "2": ("新订阅了但点击没反应", ["env", "index", "cross", "logs"]),
    "3": ("创意工坊里壁纸大小显示 0", ["env", "index", "size"]),
    "4": ("壁纸文件不见了 / 被误删", ["env", "index", "cross", "content", "size"]),
}

CHECK_NAMES = {
    "env": "运行环境",
    "index": "索引状态",
    "cross": "三方比对",
    "content": "内容健康",
    "size": "大小校验",
    "archive": "冗余与残留",
    "logs": "日志异常",
}

# 问题关键词 → 修复步骤。按顺序匹配，命中即生成一步；
# 覆盖全部已知问题类型，避免「检出问题却没有对应修复步骤」。
