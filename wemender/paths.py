# -*- coding: utf-8 -*-
"""路径自动探测（WE-Mender 3.0 模块化组件）"""

import os
import re

from wemender.common import APPID, isdir, isfile, norm, read_text

# ══════════════════════════════════════════════════════════════
#  路径自动探测
# ══════════════════════════════════════════════════════════════

def detect_steam():
    """定位 Steam 安装目录：优先注册表，其次常见路径"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = winreg.QueryValueEx(k, "SteamPath")[0]
            if isfile(os.path.join(p, "steam.exe")):
                return norm(p)
    except Exception:
        pass
    guesses = [
        r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
        r"D:\Steam", r"D:\steam", r"E:\Steam", r"E:\steam",
        r"F:\Steam", r"F:\steam",
    ]
    for g in guesses:
        if isfile(os.path.join(g, "steam.exe")):
            return norm(g)
    return None


def detect_libraries(steam):
    """
    从 libraryfolders.vdf 读出全部游戏库路径，兼容两种格式：
      新格式  "path"  "D:\\SteamLibrary"
      旧格式  "1"     "D:\\SteamLibrary"     （键是序号）
    """
    libs, seen = [], set()

    def add(p):
        p = norm(p.replace("\\\\", "\\"))
        k = p.lower().rstrip("\\")
        if p and k not in seen and isdir(p):
            seen.add(k)
            libs.append(p)

    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf") if steam else None
    if isfile(vdf):
        raw = read_text(vdf)
        for m in re.findall(r'"path"\s+"([^"]+)"', raw):          # 新格式
            add(m)
        for m in re.findall(r'"\d+"\s+"([A-Za-z]:[^"]+)"', raw):  # 旧格式
            add(m)
    if steam:
        add(steam)
    return libs


def detect_we(libs):
    """在各库中定位 Wallpaper Engine 安装目录，返回 (we_path, lib_path)"""
    for lib in libs:
        p = os.path.join(lib, "steamapps", "common", "wallpaper_engine")
        if isdir(p):
            return p, lib
    return None, None


def detect_accounts(steam):
    """列出 userdata 下的账户 ID；读取失败返回 None（区别于「一个账户都没有」）"""
    ud = os.path.join(steam, "userdata")
    if not isdir(ud):
        return []
    try:
        return sorted([d for d in os.listdir(ud) if d.isdigit() and d != "0"])
    except OSError:
        return None


def resolve_paths(account=None):
    """
    探测并返回全部关键路径。
    返回 dict，字段缺失时为 None，不影响后续检查继续执行。
    """
    P = {"errors": [], "read_files": []}

    P["steam"] = detect_steam()
    if not P["steam"]:
        P["errors"].append("未能定位 Steam 安装目录")

    P["libraries"] = detect_libraries(P["steam"]) if P["steam"] else []
    if P["steam"] and not isfile(os.path.join(P["steam"], "steamapps", "libraryfolders.vdf")):
        P["errors"].append("未能读取游戏库列表（libraryfolders.vdf 不存在）")
    elif not P["libraries"]:
        P["errors"].append("未能读取游戏库列表（libraryfolders.vdf 无法解析）")

    P["we"], P["we_lib"] = detect_we(P["libraries"])
    if not P["we"]:
        P["errors"].append("未能定位 Wallpaper Engine 安装目录")

    accounts = detect_accounts(P["steam"]) if P["steam"] else []
    P["accounts"] = accounts or []
    if accounts is None:
        P["errors"].append("读取 userdata 目录失败（权限不足？）")
    P["account"] = account or (P["accounts"][0] if P["accounts"] else None)
    if not P["account"]:
        P["errors"].append("未能识别 Steam 账户 ID")

    # 工坊内容目录：在所有库里找
    P["content"] = None
    P["content_found"] = False       # 标记「真的找到了」而非「拼了个路径」
    for lib in P["libraries"]:
        c = os.path.join(lib, "steamapps", "workshop", "content", APPID)
        if isdir(c):
            P["content"] = c
            P["content_lib"] = lib
            P["content_found"] = True
            break
    if not P["content"] and P["we_lib"]:
        # 找不到时给出推测路径供人工核对，但不标记为已找到
        P["content"] = os.path.join(P["we_lib"], "steamapps", "workshop", "content", APPID)

    # 账本 / 清单：跟随内容目录所在库
    lib_for_data = P.get("content_lib") or P["we_lib"] or (P["libraries"][0] if P["libraries"] else None)
    if lib_for_data:
        P["acf"] = os.path.join(lib_for_data, "steamapps", "workshop", "appworkshop_%s.acf" % APPID)
        P["appmanifest"] = os.path.join(lib_for_data, "steamapps", "appmanifest_%s.acf" % APPID)
    else:
        P["acf"] = P["appmanifest"] = None

    if P["steam"] and P["account"]:
        P["subs"] = os.path.join(P["steam"], "userdata", P["account"], "ugc",
                                 "%s_subscriptions.vdf" % APPID)
        P["logs_dir"] = os.path.join(P["steam"], "logs")
    else:
        P["subs"] = P["logs_dir"] = None

    P["content_log"] = os.path.join(P["logs_dir"], "content_log.txt") if P["logs_dir"] else None
    P["we_config"] = os.path.join(P["we"], "config.json") if P["we"] else None
    return P
