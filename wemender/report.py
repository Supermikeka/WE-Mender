# -*- coding: utf-8 -*-
"""报告渲染与修复建议文案（WE-Mender 3.0 模块化组件）"""

import os

from wemender.common import brief, human, isdir, isfile, now_str, tri
from wemender.checks import CHECK_NAMES
from wemender.digest import build_digest

STEP_RULES = [
    ("无主文件夹", "隔离无主文件夹（低风险，可回滚）", [
        "把不在订阅列表里的文件夹整体移动到独立目录，不要删除。",
        "移动后硬盘上的壁纸文件依然存在，随时可以移回。"]),
    ("安装记录被清空", "重建安装记录（本工具可直接自动修复）", [
        "用「订阅详情」段里现存的版本号，配合硬盘文件的实际大小，",
        "把丢失的「已安装记录」段重新写回去（本工具「自动修复」可一键完成）；",
        "这样可以让已下载的壁纸直接恢复，避免全量重新下载。"]),
    ("账本两段记录均为空", "重建账本（中风险，务必先完成备份）", [
        "账本整体失效，优先从备份恢复。",
        "没有备份时，可删除账本文件让 Steam 重新生成，再重新排查。"]),
    ("账本文件是空的", "恢复账本文件（中风险）", [
        "从备份恢复账本文件。",
        "没有备份时，可删除该文件让 Steam 重新生成，再重新排查。"]),
    ("账本文件内容无法识别", "恢复账本文件（中风险）", [
        "从备份恢复。没有备份时，可删除该文件让 Steam 重新生成。"]),
    ("找不到创意工坊账本文件", "确认安装与库设置（官方）", [
        "确认 Wallpaper Engine 已正确安装，且工坊目录在预期位置。",
        "必要时在 Steam 里对 Wallpaper Engine 执行一次「验证文件完整性」。"]),
    ("找不到工坊内容目录", "确认 Steam 库设置（官方）", [
        "检查 Steam 的库文件夹设置，确认 Wallpaper Engine 的工坊内容目录存在。"]),
    ("记录指向不存在的文件夹", "重新下载缺失内容（官方）", [
        "这类内容只能由 Steam 重新下载，可先触发一次下载。",
        "若始终拉不到，可能是物品已下架，可考虑转成本地壁纸。"]),
    ("数量不一致", "补全安装记录（本工具可自动修复）", [
        "首选：用本工具「自动修复」按订阅详情与硬盘文件补全缺失记录；",
        "备用：在 Steam 里触发一次内容校验或下载，让账本补齐记录。"]),
    ("文件夹未被账本记录", "补全账本记录（推荐：本工具自动修复）", [
        "首选：用本工具「自动修复」直接补全记录（不用重新下载，有备份可回滚）；",
        "备用：在 Steam 里触发一次内容校验或下载，让账本补齐记录。"]),
    ("已订阅但硬盘上没有内容", "处理未下载的订阅项（官方）", [
        "这类内容只能由 Steam 下载。可先在 Steam 里触发一次下载；",
        "如果始终拉不到，可能是物品已被作者删除或下架，可考虑转成本地壁纸使用。"]),
    ("没有任何创意工坊订阅", "确认账户是否正确", [
        "确认登录的是常用账户；若不是，切换账户后重新排查。"]),
    ("缺少或损坏 project.json", "重新下载不完整内容（官方）", [
        "这些文件夹内容不完整，需要由 Steam 重新下载。"]),
    ("引用的主文件缺失", "重新下载缺失文件（官方）", [
        "壁纸文件缺失，需要由 Steam 重新下载。"]),
    ("大小记录与硬盘实际不符", "核对文件夹内的多余文件（低风险）", [
        "检查这些文件夹里是否有多余文件（例如自己放进去的压缩包）。",
        "清理多余文件后，记录与实际大小就会一致。"]),
    ("压缩包类文件", "自行决定是否清理（低风险）", [
        "这些不是 Steam 下载的文件，确认不再需要后可自行删除。"]),
    ("空文件夹", "清理空目录（低风险）", [
        "空目录可直接删除，不影响壁纸。"]),
    ("网络类重试记录", "一般无需处理", [
        "属于瞬时网络抖动，Steam 通常会自动重试成功。"]),
    ("读不到订阅列表", "先让 Steam 同步订阅（官方）", [
        "启动 Steam 并确认已登录正确账户，让它同步一次订阅列表。"]),
    ("订阅列表内容无法识别", "检查订阅文件（中风险）", [
        "订阅文件可能已损坏，可删除后让 Steam 重新生成。"]),
    ("只解析出一部分", "先让 Steam 同步订阅（官方）", [
        "订阅清单里有个别条目格式不同，工具无法确认完整订阅集合。",
        "启动 Steam 并确认登录的是常用账户，让它同步一次订阅后重新排查；",
        "在此之前不要用「自动修复」删记录，以免把仍在订阅的条目当成已退订。"]),
    ("安装记录不可用", "先恢复账本文件", [
        "账本文件缺失或不可读，先恢复它再重新排查。"]),
]

EXTRA_ASK = {
    "1": "请重点判断「安装记录」是否丢失，并给出保住本地已下载文件、避免全量重新下载的修复方案。",
    "2": "请重点判断订阅状态机是否卡住，解释为什么订阅成功却不触发下载。",
    "3": "请重点解释该大小字段的来源，以及它在本机当前状态下说明了什么。",
    "4": "请重点区分「文件被误删」与「索引记录丢失」，并给出正确的恢复顺序。",
}

# ══════════════════════════════════════════════════════════════
#  报告渲染
# ══════════════════════════════════════════════════════════════

SEP = "=" * 72
SUB = "-" * 72


def render(P, mode_key, mode_name, D, issues, demo=False):
    L = []
    A = L.append

    checks = set(P.get("_checks", []))       # 本次实际执行了哪些检查（全程共用）
    ran = [CHECK_NAMES.get(k, k) for k in P.get("_checks", [])]
    A(SEP)
    A("  Wallpaper Engine 排查结果（WE-Mender 3.0）")
    A("  生成时间：%s" % now_str())
    A("  排查模式：%s" % mode_name)
    A("  本次检查：%s" % ("、".join(ran) if ran else "（无）"))
    A("  未检查项：%s" % ("、".join(v for k, v in CHECK_NAMES.items()
                                   if k not in P.get("_checks", [])) or "无"))
    A(SEP)
    A("")

    # ── 〇、三账速览（v2 新增：一屏看懂 订阅/记录/硬盘 三方状态与对策）
    A("【三账速览】")
    A(SUB)
    for _ln in build_digest(P, D):
        A(_ln)
    A("")

    # ── 一、分析结果（人直接看）
    A("【分析结果】")
    A(SUB)
    probe_failed = bool(P.get("errors"))
    if probe_failed:
        A("  ⚠ 本次路径探测未完全成功，排查结果可能不完整：")
        for e in P["errors"]:
            A("      · %s" % e)
        A("      在补齐这些路径之前，请不要把下面的结论当成「没有问题」。")
        A("")
    if not issues:
        if probe_failed:
            A("  结论：未能完成完整排查（探测失败），因此无法判断是否正常")
            A("        这不等于「一切正常」，请先解决上面的探测问题。")
        else:
            A("  结论：未发现问题，一切正常 ✓")
            A("")
            # 只有在「三方比对」确实跑过、且三方数据都可用时，才有资格断言「三方一致」
            cross_ran = "cross" in checks
            if cross_ran and D.get("installed_count") is not None and D.get("subs_state") == "ok":
                A("  订阅 %d 项 · 账本记录 %d 条 · 硬盘文件夹 %d 个，三方完全一致。"
                  % (D.get("subs_total") or len(D.get("subs") or []),
                     D["installed_count"], len(D.get("dirs") or [])))
            else:
                A("  订阅 %d 项 · 硬盘文件夹 %d 个。"
                  % (D.get("subs_total") or len(D.get("subs") or []),
                     len(D.get("dirs") or [])))
                if not cross_ran:
                    A("  （本次未做三方比对，未检查项见报告开头）")
                elif D.get("installed_count") is None:
                    A("  （账本记录不可用，无法确认三方是否一致）")
                else:
                    A("  （订阅列表状态异常，无法确认三方是否一致）")
    else:
        worst = "严重" if any(i["level"] == "严重" for i in issues) else \
                ("提醒" if any(i["level"] == "提醒" for i in issues) else "提示")
        A("  结论：发现 %d 个问题，最高级别：%s" % (len(issues), worst))
        A("")
        for i, it in enumerate(issues, 1):
            A("  [%s] %d. %s" % (it["level"], i, it["title"]))
            A("      表现：%s" % it["symptom"])
            A("      原因：%s" % it["cause"])
            A("      影响：%s" % it["impact"])
            A("      该找谁：%s" % it["who"])
            A("      风险：%s" % it["risk"])
            if it.get("detail"):
                A("      详情：%s" % it["detail"])
            A("")
    A("")

    # ── 二、修复建议
    A("【修复建议】")
    A(SUB)
    if not issues and probe_failed:
        A("  本次没有检出具体问题，但路径探测失败，排查并不完整。")
        A("  建议先解决上面【分析结果】里列出的探测问题，再重新运行一次。")
    elif not issues:
        A("  无需修复。建议定期运行本工具，及早发现「无主文件夹」堆积等隐患。")
    else:
        need_backup = [i for i in issues if "备份" in i["risk"]]
        A("  建议按以下顺序处理（风险从低到高）：")
        A("")
        step = 1
        A("  第 %d 步：先做备份（零风险，纯复制）" % step)
        A("      只需要备份下面这几个小文件，把它们复制到其他盘即可：")
        A("")
        bak = [
            ("必", "安装记录(账本)", P.get("acf"), "修复时唯一会被改动的文件"),
            ("必", "订阅列表", P.get("subs"), "判断哪些壁纸该存在，修复的依据"),
            ("选", "游戏清单", P.get("appmanifest"), "记录游戏本体安装状态"),
            ("选", "WE 配置文件", P.get("we_config"), "壁纸引擎的个性化设置"),
        ]
        for tag, name, path, why in bak:
            if path and isfile(path):
                try:
                    sz = human(os.path.getsize(path))
                except OSError:
                    sz = "?"
                A("        [%s] %-14s %-10s %s" % (tag, name, sz, path))
                A("            %s" % why)
            else:
                A("        [%s] %-14s （未找到，跳过）" % (tag, name))
        A("")
        A("      ✗ 不要备份「工坊内容目录」—— 那是壁纸本体，动辄上百 GB，"
          "修复过程不会改动它，复制它纯属浪费时间。")
        A("      ⚠ 备份前请先完全退出 Steam 和 Wallpaper Engine，否则文件可能被占用。")
        step += 1
        A("")
        titles = [i["title"] for i in issues]
        emitted = set()
        matched_any = False
        for kw, stitle, lines in STEP_RULES:
            if stitle in emitted:
                continue
            if any(kw in t for t in titles):
                matched_any = True
                emitted.add(stitle)
                A("  第 %d 步：%s" % (step, stitle))
                for ln in lines:
                    A("      %s" % ln)
                step += 1
                A("")
        if not matched_any:
            # 兜底：万一出现未登记的问题类型，也要给出可执行指引
            A("  第 %d 步：按上面【分析结果】的问题清单逐项处理" % step)
            A("      每个问题都标明了「该找谁」和「风险」，按其指示操作即可。")
            A("")
        A("  ⚠ 以上涉及直接读写本地文件的操作，请交给具备文件访问权限的 AI 工具执行。")
        if need_backup:
            A("  ⚠ 标有「必须备份」的问题，动手前一定要先完成备份。")
    A("")

    # ── 三、涉及的文件路径
    A("【涉及的文件路径】")
    A(SUB)
    A("  以下路径均已自动探测确认，AI 工具可直接使用，无需再次查找。")
    A("")

    def path_or_missing_pre(p):
        return p or "（未找到）"

    A("  ◆ 程序与配置")
    A("      Steam 安装目录 : %s" % path_or_missing_pre(P.get("steam")))
    A("      游戏库目录     : %s" % ("；".join(P.get("libraries") or []) or "（未找到）"))
    A("      WE 程序目录    : %s" % (P.get("we") or "（未找到）"))
    A("      账户 ID        : %s" % (P.get("account") or "（未找到）"))
    if len(P.get("accounts") or []) > 1:
        A("      ⚠ 检测到多个 Steam 账户：%s" % "、".join(P["accounts"]))
        A("        本次使用的是 %s。若不对，请用 --account <ID> 指定。" % P.get("account"))
    A("")

    def path_or_missing(p, must_dir=False, must_file=False):
        """路径显示：不存在时明确标注，避免看起来像真实存在的路径"""
        if not p:
            return "（未找到）"
        ok = isdir(p) if must_dir else (isfile(p) if must_file else os.path.exists(p))
        return p if ok else "%s   ← 该路径当前不存在" % p

    A("  ◆ 核心数据文件（★ = 修复时会改动的文件，务必先备份）")
    A("      ★ 安装记录(账本) : %s" % path_or_missing(P.get("acf"), must_file=True))
    A("        订阅列表       : %s" % path_or_missing(P.get("subs"), must_file=True))
    A("        游戏清单       : %s" % path_or_missing(P.get("appmanifest"), must_file=True))
    A("        WE 配置文件    : %s" % path_or_missing(P.get("we_config"), must_file=True))
    A("        创意工坊日志   : %s" % path_or_missing(P.get("content_log"), must_file=True))
    A("        工坊内容目录   : %s" % path_or_missing(P.get("content"), must_dir=True))
    A("            ↑ 壁纸本体所在目录。修复过程不会改动它，也不需要备份（体积很大）。")
    A("")
    A("  ◆ 本次排查读取过的文件")
    rf = P.get("read_files") or []
    if rf:
        for f in dict.fromkeys(rf):
            A("      %s" % f)
    else:
        A("      （无）")
    A("")

    # ── 四、详细数据
    A("【详细数据】")
    A(SUB)
    def section(title, ran, body):
        """小节输出：检查没跑过就明确写「未检查」，绝不输出默认值冒充真实数据"""
        A("  ■ %s" % title)
        if ran:
            body()
        else:
            A("      （本次排查模式未包含此项检查）")
        A("")

    def cnt(v, unit="个"):
        """数量显示：None 表示数据不可用，绝不显示成 0"""
        return "数据不可用" if v is None else "%d %s" % (len(v), unit)

    section("运行环境", "env" in checks, lambda: (
        A("      Steam 运行中    : %s" % tri(D.get("steam_running"))),
        A("      WE 运行中       : %s" % tri(D.get("we_running"))),
        A("      加速器运行中    : %s" % (
            "检测失败" if D.get("booster_running") is None else
            ("是（可能干扰 Steam 校验，排查时建议退出）" if D.get("booster_running") else "否"))),
    ))

    # 基础数据来自 scan_basic，与具体检查模块无关，始终可用
    def _basic():
        if D.get("content_exists"):
            A("      工坊文件夹数    : %s" % cnt(D.get("dirs")))
        else:
            A("      工坊文件夹数    : 数据不可用（工坊内容目录不存在）")
        st = D.get("subs_state")
        if st == "ok":
            A("      订阅列表总数    : %d 项" % (D.get("subs_total")
                                                or len(D.get("subs") or [])))
        elif st == "empty":
            A("      订阅列表总数    : 0 项（文件存在，但没有任何订阅记录）")
        else:
            A("      订阅列表总数    : 数据不可用（%s）"
              % ("文件存在但内容无法识别" if st == "invalid"
                 else "订阅列表文件不存在"))
        if D.get("non_id_dirs"):
            A("      非工坊命名的目录: %s" % brief(sorted(D["non_id_dirs"]), 10))
            A("          ↑ 这些不是 Steam 下载的内容（工坊目录都是纯数字 ID）")

    section("基础数据", True, _basic)

    section("索引状态", "index" in checks, lambda: (
        A("      安装记录条目    : %s" % (
            "数据不可用" if D.get("installed_count") is None else "%d 条" % D["installed_count"])),
        A("      订阅详情条目    : %s" % (
            "数据不可用" if D.get("details_count") is None else "%d 条" % D["details_count"])),
        A("      账本记录总大小  : %s" % human(D.get("size_on_disk"))),
    ))

    def _skip(field):
        """未比对的统一措辞，并带上具体原因（不再一律说「安装记录不可用」）"""
        if D.get(field) is not None:
            return None
        return "本次未比对（%s）" % (D.get("cross_skip") or "数据不可用")

    def _orphan():
        n = D.get("dir_no_sub")
        s = _skip("dir_no_sub")
        if s:
            return s
        if not n:
            return "0 个"
        sz = D.get("orphan_size")
        return "%d 个%s" % (len(n), "（合计 %s）" % human(sz) if sz is not None else "")

    def _body_cross():
        A("      订阅了但无内容  : %s" % (
            _skip("sub_no_content") or brief(D.get("sub_no_content") or [], 10, "项")))
        A("      有内容但未订阅  : %s" % _orphan())
        A("      有记录但无文件  : %s" % (
            _skip("record_no_dir") or brief(D.get("record_no_dir") or [], 10, "条")))
        A("      有文件但无记录  : %s" % (
            _skip("dir_no_record")
            or D.get("dir_no_record_note")
            or brief(D.get("dir_no_record") or [], 10)))

    section("三方比对（订阅账 × 安装账 × 硬盘账）", "cross" in checks, _body_cross)
    section("内容健康", "content" in checks, lambda: (
        A("      project.json 正常 : %s" % (
            "数据不可用（内容目录不存在）" if D.get("content_ok") is None
            else "%d 个" % D["content_ok"])),
        A("      缺失或损坏        : %s" % (
            "数据不可用" if D.get("bad_project") is None
            else brief(D.get("bad_project") or [], 10))),
        A("      引用文件缺失      : %s" % (
            "数据不可用" if D.get("bad_ref") is None
            else brief(D.get("bad_ref") or [], 10, "项"))),
    ))

    section("大小校验", "size" in checks, lambda: (
        A("      记录合计        : %s" % human(D.get("size_total_record"))),
        A("      与硬盘不符项数  : %s" % (
            "数据不可用（账本或内容目录缺失）" if D.get("size_mismatch") is None
            else "%d 项" % len(D["size_mismatch"]))),
    ))

    section("冗余与残留", "archive" in checks, lambda: (
        A("      压缩包类文件    : %s" % (
            "数据不可用（内容目录不存在）" if D.get("archive_hits") is None
            else "%d 个 / %s" % (len(D["archive_hits"]), human(D.get("archive_total") or 0)))),
        A("      空文件夹        : %s" % (
            "数据不可用" if D.get("empty_dirs") is None
            else "%d 个" % len(D["empty_dirs"]))),
    ))

    def _logs():
        hits = D.get("log_hits")
        if hits is None:
            A("      数据不可用（日志文件不存在）")
            return
        total = D.get("log_hits_total", len(hits))
        if total == 0:
            A("      无")
            return
        note = "（日志过大，仅扫描了最近 30000 行）" if D.get("log_scan_truncated") else ""
        if total <= len(hits):
            A("      共 %d 条，全部列出 %s" % (total, note))
        else:
            A("      共 %d 条，以下为最后 %d 条 %s" % (total, len(hits), note))
        for h in hits:
            A("      %s" % h[:150])

    section("最近 24 小时日志异常", "logs" in checks, _logs)

    if P.get("errors"):
        A("  ■ 探测过程中的提示")
        for e in P["errors"]:
            A("      · %s" % e)
        A("")

    # ── 五、给 AI 的说明
    A("【给 AI 的说明】")
    A(SUB)
    A("  本文件由 WE-Mender 3.0 自动生成。诊断全程只读；「自动修复」仅在主动触发时执行，")
    A("  修复前自动备份账本、失败自动回滚，全程不改动壁纸文件。")
    A("  如果你具备文件访问与命令执行权限，请直接基于以上路径和数据给出可执行的修复步骤；")
    A("  如果不具备，请只做分析与思路说明，不要虚构操作结果。")
    A("")
    A("  排查提示：")
    A("    · 本工具涉及的判断逻辑：订阅账 × 安装账 × 硬盘账 三方比对，对不上的地方即故障点")
    A("    · Steam 官方手段（验证文件完整性）只校验游戏本体，不会触碰创意工坊账本")
    A("    · 动手修改任何文件前，请先完成备份")
    if mode_key in EXTRA_ASK:
        A("    · 针对本次现象：%s" % EXTRA_ASK[mode_key])
    A("")
    A(SEP)
    return "\n".join(L)
