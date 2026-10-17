# -*- coding: utf-8 -*-
"""
WE-Mender — Wallpaper Engine 创意工坊故障排查工具（v2 · CLI 入口）

用法
  python we_mender.py            交互式选择排查模式
  python we_mender.py --mode 0   直接指定模式（0=一键排查）
  python we_mender.py --demo     用模拟数据生成示例结果（用于查看报告结构）
"""

import os
import sys

from wemender.common import BASE_DIR, DEMO_OUT_NAME, copy_to_clipboard
from wemender.paths import resolve_paths
from wemender.checks import MODES
from wemender.report import render
from wemender.pipeline import collect_and_render, write_report
from wemender.repair import repair_run

# ══════════════════════════════════════════════════════════════
#  演示数据（--demo，用于查看报告结构）
# ══════════════════════════════════════════════════════════════

def demo_run():
    P = {
        "steam": r"C:\Program Files (x86)\Steam",
        "libraries": [r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"],
        "we": r"D:\SteamLibrary\steamapps\common\wallpaper_engine",
        "account": "123456789",
        "accounts": ["123456789"],
        "content": r"D:\SteamLibrary\steamapps\workshop\content\431960",
        "subs": r"C:\Program Files (x86)\Steam\userdata\123456789\ugc\431960_subscriptions.vdf",
        "acf": r"D:\SteamLibrary\steamapps\workshop\appworkshop_431960.acf",
        "appmanifest": r"D:\SteamLibrary\steamapps\appmanifest_431960.acf",
        "content_log": r"C:\Program Files (x86)\Steam\logs\content_log.txt",
        "we_config": r"D:\SteamLibrary\steamapps\common\wallpaper_engine\config.json",
        "errors": [],
        "read_files": [
            r"D:\SteamLibrary\steamapps\workshop\appworkshop_431960.acf",
            r"C:\Program Files (x86)\Steam\userdata\123456789\ugc\431960_subscriptions.vdf",
            r"C:\Program Files (x86)\Steam\logs\content_log.txt",
        ],
        "_checks": ["env", "index", "cross", "content", "size", "archive", "logs"],
    }
    subs_ids = ["1000%05d" % i for i in range(67)]
    dir_ids = ["1000%05d" % i for i in range(66)] + ["2000%05d" % i for i in range(337)]
    D = {
        "steam_running": False, "we_running": False, "booster_running": True,
        "installed_count": 0, "details_count": 64,
        "size_on_disk": 0,
        "subs_state": "ok", "content_exists": True,
        "repairable": ["1000%05d" % i for i in range(64)],   # 演示：64 条可自动修复
        "repair_reason": "",
        "subs": set(subs_ids),                 # 67 项
        "dirs": set(dir_ids),                  # 403 个文件夹
        "sub_no_content": [], "dir_no_sub": ["2000%05d" % i for i in range(337)],
        "record_no_dir": [], "dir_no_record": [],
        "orphan_size": 236760000000,
        "content_ok": 386, "bad_project": [], "bad_ref": [],
        "size_mismatch": [], "size_total_record": 0,
        "archive_hits": [(8459123416, "2000%05d" % 1, "sample.mp4.zip")],
        "archive_total": 8459123416, "empty_dirs": [],
        "log_hits": [
            "[2026-10-15 16:59:15] HTTP (CDN,33) - .../depot/431960/manifest/... - manifest request received 0 (Invalid) HTTP response",
            "[2026-10-15 16:59:31] HTTP (CDN,29) - .../depot/431960/manifest/... - manifest request received 0 (Invalid) HTTP response",
        ],
    }
    issues = [
        dict(level="严重", title="安装记录被清空",
             symptom="壁纸全部显示「未安装」或提示「下载失败，请重新下载」；创意工坊里壁纸大小显示 0",
             cause="账本文件里「已安装记录」这一段是空的（0 条），而订阅详情段有 64 条",
             impact="所有已订阅壁纸都无法被软件识别和使用，但硬盘上的壁纸文件其实还在",
             who="AI 工具（Steam 官方手段无法修复这一层，实测验证过）",
             risk="中 · 动手前必须备份账本文件",
             detail="这是一个典型的「本地记录丢失」，不是壁纸文件丢失"),
        dict(level="提醒", title="存在 337 个无主文件夹（220.48 GB）",
             symptom="暂时可能没有直接表现，但这是问题反复发作的根源",
             cause="这些文件夹不在当前订阅列表里（多半是以前取消订阅后残留的），"
                   "但 Steam 账本上已经没有它们，属于「账本为空 + 硬盘有货」的不一致状态",
             impact="Steam 每次做完整检查时，遇到这种不一致就可能把安装记录写成空表，导致刚修好的问题再次复发",
             who="AI 工具",
             risk="低 · 只需要移动，不需要删除，随时可以移回",
             detail="前 10 个（共 337 个）：['1000000001', '1000000002', '1000000003', '1000000004', "
                    "'1000000005', '1000000006', '1000000007', '1000000008', '1000000009', '1000000010']"),
        dict(level="提示", title="日志里有 2 条网络类重试记录",
             symptom="一般无影响",
             cause="Steam 请求内容清单时出现瞬时失败（received 0 / Invalid），随后自动重试成功",
             impact="属于网络抖动，Steam 通常能自行恢复，一般无需处理",
             who="无需处理", risk="低",
             detail="全部 2 条：manifest request received 0 (Invalid) HTTP response ｜ "
                    "manifest request received 0 (Invalid) HTTP response"),
    ]
    text = render(P, "0", MODES["0"][0], D, issues, demo=True)
    out = os.path.join(BASE_DIR, DEMO_OUT_NAME)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("已生成示例结果：%s" % out)
    return out

# ══════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════

def ask_mode():
    print()
    print("=" * 52)
    print("  WE-Mender  Wallpaper Engine 故障排查工具")
    print("=" * 52)
    print()
    print("  请选择要排查的问题：")
    print()
    for k in ["0", "1", "2", "3", "4"]:
        print("    [%s] %s%s" % (k, MODES[k][0], "   ← 推荐，最全面" if k == "0" else ""))
    print()
    try:
        s = input("  请输入数字 [0]：").strip() or "0"
    except (EOFError, KeyboardInterrupt):
        s = "0"
    if s not in MODES:
        print("  输入无效，按 [0] 一键排查处理。")
        s = "0"
    return s


def _print_summary(P, issues, out, ok_clip):
    """终端摘要（首次排查使用；修复后复检另行输出）"""
    print()
    print("=" * 52)
    if P.get("errors"):
        print("  ⚠ 路径探测未完全成功，结果可能不完整：")
        for e in P["errors"]:
            print("    · %s" % e)
        print()
    if not issues:
        if P.get("errors"):
            print("  结论：未能完成完整排查（探测失败），不等于一切正常")
        else:
            print("  结论：未发现问题，一切正常 ✓")
    else:
        print("  结论：发现 %d 个问题" % len(issues))
        for i in issues:
            print("    [%s] %s" % (i["level"], i["title"]))
    print("=" * 52)
    print()
    print("  结果已保存：%s" % out)
    print("  %s" % ("✓ 内容已复制到剪贴板，可直接粘贴给 AI 工具"
                    if ok_clip else "· 剪贴板写入失败，请手动打开上面的文件复制"))
    print()
    if any("备份" in i["risk"] for i in issues):
        print("  ⚠ 本次检测到需要直接修改本地文件的问题，建议先做好备份再动手。")
        print()


def _pause():
    try:
        input("  按回车键退出……")
    except (EOFError, KeyboardInterrupt):
        pass


def main():
    args = sys.argv[1:]
    if "--demo" in args:
        demo_run()
        return 0

    mode_key = "0"
    if "--mode" in args:
        i = args.index("--mode")
        if i + 1 < len(args) and args[i + 1] in MODES:
            mode_key = args[i + 1]
        else:
            print("  ⚠ --mode 参数无效，已按 [0] 一键排查处理。"
                  "可选值：%s" % "、".join(sorted(MODES)))
    else:
        mode_key = ask_mode()

    account = None
    if "--account" in args:
        i = args.index("--account")
        nxt = args[i + 1] if i + 1 < len(args) else None
        if nxt and not nxt.startswith("-"):
            account = nxt
        else:
            print("  ⚠ --account 后面缺少账户 ID，已忽略该参数。")

    mode_name, _ = MODES[mode_key]

    print()
    print("正在探测路径并检查，请稍候……")
    P = resolve_paths(account=account)
    D, issues, text = collect_and_render(P, mode_key, mode_name)

    out = write_report(text)
    if not out:
        print()
        print("  ✗ 结果文件写入失败。")
        print("    可能原因：目录只读、磁盘已满、或被其他程序占用。")
        print("    请把本工具放到有写入权限的目录后重试。")
        _pause()
        return 1

    ok = copy_to_clipboard(text)
    _print_summary(P, issues, out, ok)

    # ── v2 新增：检测到可自动修复的问题时，提供一键修复入口
    n_add = len(D.get("repairable_add") or [])
    n_del = len(D.get("repairable_del") or [])
    if D.get("repairable"):
        parts = []
        if n_add:
            parts.append("补录 %d 条漏登的安装记录" % n_add)
        if n_del:
            parts.append("移除 %d 条已退订且没有文件的残留记录" % n_del)
        print("  ★ 检测到可自动修复的问题：%s。" % "，".join(parts))
        print("    自动修复只改账本记录，不碰壁纸文件、不改订阅关系。")
        print("    修复前会自动备份账本；修复需要完全退出 Steam 和 Wallpaper Engine。")
        print()
        try:
            ans = input("  输入 y 回车立即自动修复，直接回车跳过：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans == "y":
            print()
            ok2, msg, bak2 = repair_run(P, D, ui=lambda s: print("  " + s))
            print()
            print("  " + ("✓ " if ok2 else "✗ ") + msg)
            if bak2:
                print("    备份文件：%s（需要时可手工复制回账本）" % bak2)
            if ok2:
                print()
                print("  正在按修复后的状态重新检查，并更新报告……")
                P2 = resolve_paths(account=account)
                _D2, issues2, text2 = collect_and_render(P2, mode_key, mode_name)
                out2 = write_report(text2)
                print()
                if not issues2:
                    print("  复检结论：未发现问题，三方一致 ✓")
                else:
                    print("  复检结论：仍有 %d 个问题（详见报告）" % len(issues2))
                if out2:
                    print("  报告已更新：%s" % out2)
                print("  现在可以打开 Steam 了。")
            _pause()
            return 0

    _pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
