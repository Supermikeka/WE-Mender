# -*- coding: utf-8 -*-
"""三账速览：订阅（目标）/ 安装记录（记忆）/ 硬盘文件（事实）（WE-Mender 3.0 新增）"""


def build_digest(P, D):
    """
    生成「三账速览」文本行列表：一屏看懂三方状态，每类差异附一行对策；
    可自动修复的项会明确标注。数据不可用时如实标注，绝不编造 0。
    """
    L = []

    def line(s=""):
        L.append("  " + s if s else "")

    # 订阅数用 subs_total（含本地已禁用），与 report.py 的"订阅列表总数"同口径；
    # 用 len(D["subs"]) 会少算禁用项，同一份报告出现 76 与 77 两个数。
    subs_n = (D.get("subs_total") or len(D.get("subs") or [])
              if D.get("subs_state") in ("ok", "empty") else None)
    inst_n = D.get("installed_count")
    dirs_n = len(D.get("dirs") or []) if D.get("content_exists") else None
    fmt = lambda v: "数据不可用" if v is None else "%d" % v
    line("订阅（目标）%s 项 ｜ 安装记录（记忆）%s 条 ｜ 硬盘文件（事实）%s 个"
         % (fmt(subs_n), fmt(inst_n), fmt(dirs_n)))

    if subs_n is None or inst_n is None or dirs_n is None:
        line("（部分数据不可用，完整情况请见下方【分析结果】）")
        return L

    need = list(D.get("repairable_add") if D.get("repairable_add") is not None
                else (D.get("repairable") or []))
    need_del = set(D.get("repairable_del") or [])
    dir_no_record = D.get("dir_no_record")
    record_no_dir = D.get("record_no_dir")
    sub_no_content = D.get("sub_no_content")
    dir_no_sub = D.get("dir_no_sub")
    bullets = 0
    L.append("")

    # 头号故障：安装记录为空（此时上游会把 dir_no_record 置 None，须单独分支）
    rec_n = 0
    if inst_n == 0 and dirs_n > 0:
        bullets += 1
        line("· 安装记录为空：0 条 —— 所有已下载的壁纸都会被误判「未安装」")
        if need:
            line("    → 本工具可自动修复（点「自动修复」，有备份、可回滚）")
        else:
            line("    → 本工具当前无法自动修复，请查看下方【分析结果】与【修复建议】")
    else:
        if need:
            # 可自动修复的（订阅详情 ∩ 硬盘 − 已安装记录）
            rec_n = len(need)
        elif dir_no_record:
            # 剔除「没订阅」的孤儿后，剩下的才是真正「订阅了却没记录」的条目
            orphans = set(dir_no_sub or [])
            rest = [f for f in dir_no_record if f not in orphans]
            rec_n = len(rest)
        if rec_n:
            bullets += 1
            extra = (len(dir_no_record) - rec_n
                     if (dir_no_record and len(dir_no_record) > rec_n) else 0)
            line("· 有文件、缺记录：%d 个%s —— Steam 会误判「从未安装」，要求全部重新下载"
                 % (rec_n, "（另有 %d 个无主文件夹未计入）" % extra if extra else ""))
            if need:
                line("    → 本工具可自动修复（点「自动修复」，执行前自动备份账本）")
            else:
                line("    → 建议在 Steam 里触发一次内容校验或下载，让账本补齐记录")

    if record_no_dir:
        bullets += 1
        can_del = [i for i in record_no_dir if i in need_del]
        line("· 有记录、没文件：%d 条 —— 对应壁纸不可用" % len(record_no_dir))
        if can_del:
            line("    → 其中 %d 条已不订阅（残留记录），可点「自动修复」批量移除"
                 % len(can_del))
        rest = len(record_no_dir) - len(can_del)
        if rest:
            line("    → 其余 %d 条未处理（仍在订阅，或文件在其它游戏库），"
                 "需让 Steam 重新下载；长时间拉不到可能该物品已下架" % rest)

    if sub_no_content:
        bullets += 1
        line("· 已订阅、没文件：%d 个 —— 壁纸文件没找到" % len(sub_no_content))
        line("    → 让 Steam 正常下载即可；长时间拉不到可能是已被作者下架")

    if dir_no_sub:
        bullets += 1
        line("· 有文件、没订阅：%d 个 —— 无主文件夹，不会触发下载" % len(dir_no_sub))
        line("    → 不影响使用，无需处理")

    if bullets == 0:
        line("三方一致，无需处理。")
    return L
