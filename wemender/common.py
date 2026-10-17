# -*- coding: utf-8 -*-
"""通用常量与工具函数（WE-Mender 3.0 模块化组件）"""

import datetime
import os
import re
import subprocess
import sys

APPID = "431960"                 # Wallpaper Engine 创意工坊 AppID（固定常量）

# 账本条目解析正则 —— 全局共用，避免各处写法不一致导致同一份文件得出不同结论。
# 注意：ID 与花括号之间允许任意空白（含不换行），因为 Steam 的写法并不固定。
RE_ITEM = r'"(\d{7,})"\s*\{'
RE_BLOCK = r'"(\d{7,})"\s*\{([^{}]*)\}'
# 账本条目块（含前导换行与缩进）：删条目要连行首换行一起删，否则留空行。
# installed / details 两段的条目都不含嵌套块，所以 [^{}]* 足够。
# 放在 common 里供 repair / actions 共用，避免两条删除路径分叉出两种行为。
RE_LEDGER_BLOCK_SRC = r'\r?\n\t\t"(\d{7,})"\s*\{([^{}]*)\}'
REPAIR_LOG_NAME = "修复记录.log"      # 修复审计日志（backups/ 下）
OUT_NAME = "排查结果.txt"
DEMO_OUT_NAME = "排查结果-示例.txt"


def _base_dir():
    """
    结果文件的输出目录。
    打包成 exe 后定位到 exe 自身所在目录；
    开发模式下跟随入口脚本（we_mender.py / we_mender_gui.py）所在目录，
    而不是本模块所在目录，保证产物位置与 1.x 一致。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    main_file = getattr(sys.modules.get("__main__"), "__file__", None) or sys.argv[0]
    return os.path.dirname(os.path.abspath(main_file))


BASE_DIR = _base_dir()

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ══════════════════════════════════════════════════════════════
#  通用工具
# ══════════════════════════════════════════════════════════════

def norm(p):
    return os.path.normpath(p) if p else p


def isdir(p):
    return bool(p) and os.path.isdir(p)


def isfile(p):
    return bool(p) and os.path.isfile(p)


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def human(n):
    """字节数转可读；None 统一表述为「数据不可用」，与报告其余部分用词保持一致"""
    if n is None:
        return "数据不可用"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.2f %s" % (n, unit)
        n /= 1024.0
    return "%.2f PB" % n


def dir_size(path, errs=None):
    """
    统计目录字节数。不可读的子目录/文件会被记入 errs（若提供），
    避免因权限或占用导致静默低估尺寸。
    """
    total = 0

    def onerr(e):
        if errs is not None:
            errs.append(str(e))

    for root, _, files in os.walk(path, onerror=onerr):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError as e:
                if errs is not None:
                    errs.append("%s（%s）" % (fp, e))
    return total


def cached_dir_size(D, path):
    """带缓存的目录尺寸，避免同一目录在多个检查模块里被重复遍历"""
    cache = D.setdefault("_size_cache", {})
    if path not in cache:
        cache[path] = dir_size(path)
    return cache[path]


def brief(items, limit=10, unit="个", tail=False):
    """
    生成列表摘要。标签里的数量与实际列出的数量严格一致，
    避免出现「说前 10 个却只列了 3 个」这种自相矛盾；
    数量超出上限时明确标注「共 N 个」，不让读者误以为这就是全部。
    """
    items = list(items)
    n = len(items)
    if n == 0:
        return "无"
    if n <= limit:
        return "全部 %d %s：%s" % (n, unit, items)
    shown = items[-limit:] if tail else items[:limit]
    return "%s %d %s（共 %d %s）：%s" % (
        "最后" if tail else "前", limit, unit, n, unit, shown)


def read_text(path, limit=None):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit) if limit else f.read()
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════
#  订阅清单（431960_subscriptions.vdf）解析
#
#  三条路径都要用同一份判定：检查（scan_basic）、单条动作（actions）、
#  自动修复（repair）。以前三处各写一套正则，字段顺序一变就出现
#  「检查说未订阅 / 动作说不可读 / 修复说可删」三种结论 —— 统一到这里。
# ══════════════════════════════════════════════════════════════

RE_SUB_ID = re.compile(r'"publishedfileid"\s+"(\d+)"')
RE_SUB_DISABLED = re.compile(r'"disabled_locally"\s+"1"')


def parse_subs_ids(raw):
    """订阅清单文本 → (subs, disabled, note)。

    逐条扫描，不假设字段顺序（不同 Steam 版本里 time_subscribed /
    disabled_locally 的顺序并不固定）。每条从 publishedfileid 起、到本条
    自己的 `}` 止，在条目内找 disabled_locally。
    note 非空 = 只解析出子集（数量对不上），调用方必须当作「不可信」。
    """
    subs, disabled = set(), set()
    for m in RE_SUB_ID.finditer(raw):
        close = raw.find("}", m.end())
        tail = raw[m.end():close if close != -1 else m.end() + 300]
        (disabled if RE_SUB_DISABLED.search(tail) else subs).add(m.group(1))
    n_raw = raw.count('"publishedfileid"')
    n_got = len(subs) + len(disabled)
    note = ""
    if n_raw != n_got:
        # 0 条也照样报：文件里有 3 处 publishedfileid 却一条都没认出来，
        # 那是"格式认不出"，绝不能当成"合法零订阅"（后者会让每条记录变成残尸）。
        note = "订阅清单里有 %d 条，只解析出 %d 条" % (n_raw, n_got)
    return subs, disabled, note


def read_subs_ids(path):
    """读订阅清单 → (subs, disabled, note)。

    文件不可读 / 内容不是可识别格式时返回 (None, None, 原因)：
    调用方必须把 None 当「不知道」，绝不能当成「零订阅」。
    """
    if not path or not os.path.isfile(path):
        return None, None, "订阅清单文件不存在"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception as e:
        return None, None, "订阅清单无法读取（%s）" % e
    if '"publishedfileid"' not in raw and '"subscribedfiles"' not in raw:
        return None, None, "订阅清单内容不是可识别的格式"
    # 花括号不配平 = 文件被截断；不能当成"合法零订阅"
    if raw.count("{") != raw.count("}"):
        return None, None, "订阅清单不完整（花括号不配平，疑似写入中断）"
    subs, disabled, note = parse_subs_ids(raw)
    return subs, disabled, note


def running_procs():
    """返回正在运行的相关进程名列表；检测失败时返回 None（区别于「一个都没运行」）"""
    names = ["steam.exe", "wallpaper64.exe", "wallpaper32.exe",
             "wallpaperui.exe", "steam++.exe", "watt toolkit.exe"]
    try:
        r = subprocess.run(["tasklist"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return None                      # 命令失败 ≠ 都没运行
        out = r.stdout.decode("gbk", errors="ignore").lower()
    except Exception:
        return None
    if not out.strip():                      # 空输出同样视为检测失败
        return None
    return [n for n in names if n in out]


def tri(flag):
    """三态显示：True/False/None（检测失败）"""
    if flag is None:
        return "检测失败"
    return "是" if flag else "否"


def copy_to_clipboard(text):
    """写系统剪贴板（clip.exe）。返回 True 仅当进程成功退出 ——
    shell=True 时失败不会抛异常，必须看 returncode，否则调用方的"兜底分支"永远进不去。"""
    try:
        r = subprocess.run(["clip"], input=text.encode("utf-16le"), shell=True,
                           timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False
