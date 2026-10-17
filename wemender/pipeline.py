# -*- coding: utf-8 -*-
"""编排：检查流水线 + 报告落盘（首次排查与修复后复检共用）（WE-Mender 3.0）"""

import os

from wemender.common import BASE_DIR, OUT_NAME
from wemender.checks import CHECKS, MODES, scan_basic
from wemender.report import render
from wemender.repair import repair_plan


def collect_and_render(P, mode_key, mode_name):
    """
    执行指定模式的全部检查并渲染报告文本。
    返回 (D, issues, text)。并在 D 中标注「可自动修复集合」，
    供三账速览与一键修复入口使用。
    """
    P["_checks"] = MODES[mode_key][1]
    D = {}
    issues = []
    try:
        scan_basic(P, D)
    except Exception as e:
        P["errors"].append("基础扫描出错：%s" % e)
    for key in P["_checks"]:
        fn = CHECKS.get(key)
        if not fn:
            continue
        try:
            issues.extend(fn(P, D) or [])
        except Exception as e:
            P["errors"].append("检查模块 %s 出错：%s" % (key, e))
    order = {"严重": 0, "提醒": 1, "提示": 2}
    issues.sort(key=lambda x: order.get(x["level"], 9))
    try:
        plan = repair_plan(P, D)
        # 自动修复的两个方向都要计入：补录漏登的记录 + 移除残留的记录
        D["repairable"] = list(plan.get("need") or []) + list(plan.get("need_del") or [])
        D["repairable_add"] = list(plan.get("need") or [])
        D["repairable_del"] = list(plan.get("need_del") or [])
        D["repair_reason"] = plan.get("reason") or ""
    except Exception as e:
        D["repairable"] = []
        D["repairable_add"] = []
        D["repairable_del"] = []
        D["repair_reason"] = "修复可用性检查出错：%s" % e
    text = render(P, mode_key, mode_name, D, issues)
    return D, issues, text


def write_report(text):
    """把报告写入「排查结果.txt」。成功返回路径；失败返回 None（由调用方提示）"""
    out = os.path.join(BASE_DIR, OUT_NAME)
    try:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return out
    except Exception:
        return None
