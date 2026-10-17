# -*- coding: utf-8 -*-
"""
动作引擎（WE-Mender 3.0）：八态矩阵中类型 2/4/5/6/7 的可执行操作

覆盖关系：
  类型 2（账实不符：订阅✓账✓盘✗）→ open_item_page() 重下/退订跳转（不自动清账本）
  类型 4（幽灵订阅：订✓账✗盘✗）→ subscription_page_url() 跳转取消（无全自动途径）
  类型 5（幽灵安装：订✗账✓盘✓）→ convert_to_local() / recycle_delete()（自动连带清账本）
  类型 6（账本残尸：订✗账✓盘✗）→ ledger_clear()
  类型 7（孤儿文件夹：订✗账✗盘✓）→ convert_to_local() / recycle_delete()

安全总则：
  · 每个动作执行前重新读取三账分类（不信任界面缓存），只处理仍满足异常条件的 ID
  · 文件只进系统回收站（SHFileOperationW + FOF_ALLOWUNDO），绝不直接删除
  · 账本操作复用 repair 模块安全链：备份 → 重建 → 校验 → 原子写 → 失败回滚
  · Windows API 返回码不可信（实测 ret=2 但成功），一律以事后复查为准
"""

import ctypes
import glob
import json
import os
import re
import shutil
import urllib.parse
import webbrowser
from ctypes import wintypes

from wemender.common import (APPID, BASE_DIR, RE_LEDGER_BLOCK_SRC,
                             REPAIR_LOG_NAME, dir_size, human, isfile,
                             now_str)
from wemender.repair import (_folder_healthy, _md5, _parse_items_precise,
                             _read_exact, _section_span, repair_backup,
                             repair_precheck)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PER_PAGE = 30            # 订阅列表页每页条目上限（Steam 网页端最大值）
STRIP_FIELDS = ("workshopid", "workshopurl")   # 转本地时剥离的「工坊归属」字段

# Steam 内置浏览器打开（登录态跟随客户端；Steam 未运行会被自动拉起）
STEAM_OPEN_PREFIX = "steam://openurl/"
SUB_LIST_URL = ("https://steamcommunity.com/my/myworkshopfiles/?appid=%s"
                "&browsefilter=mysubscriptions&browsesort=mysubscriptions"
                "&numperpage=%d" % (APPID, PER_PAGE))
ITEM_PAGE_URL = ("https://steamcommunity.com/sharedfiles/filedetails/?id=%s")

RE_SUB_ID = re.compile(r'"publishedfileid"\s+"(\d+)"')

# 账本条目块（含前导换行与缩进，删除后不留残行；installed/details 条目均无嵌套块）
# 正则本体在 common 里，与 repair 共用同一份，避免两条删除路径行为分叉
RE_LEDGER_BLOCK = re.compile(RE_LEDGER_BLOCK_SRC)

# ---------------------------------------------------------------------------
# 三账读取与八态分类（动作守卫的数据源）
# ---------------------------------------------------------------------------

def read_subs_map(P):
    """
    订阅清单 → {id: time_subscribed}（含本地已禁用的订阅项）。
    返回 None 表示「无法确认订阅状态」（文件缺失/不可读/格式可疑/**只解析出子集**）——
    破坏性动作的守卫必须据此中止，绝不能把「读不到」当成「零订阅」，也不能拿
    子集去判断「未订阅」（那会把仍在订阅的记录判成残尸）。
    文件正常但确实 0 条订阅时返回 {}（合法空）；是否可信由 check_subs_consistency
    再做一道规模交叉校验。
    """
    subs_path = P.get("subs")
    if not subs_path or not isfile(subs_path):
        return None
    try:
        with open(subs_path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        return None
    if '"publishedfileid"' not in txt and '"subscribedfiles"' not in txt:
        return None
    # 花括号不配平 = 文件被截断（Steam 崩溃/被杀常见）。只剩表头的半截文件
    # 不能当成"合法零订阅"，否则每条记录都会被判成残尸。
    if txt.count("{") != txt.count("}"):
        return None
    # 逐条扫描，不假设字段顺序（与 common.parse_subs_ids 同一套思路）；
    # 这里额外取出 time_subscribed，用于「打开订阅列表」推算页码。
    out = {}
    for m in RE_SUB_ID.finditer(txt):
        close = txt.find("}", m.end())
        block = txt[m.end():close if close != -1 else m.end() + 300]
        mt = re.search(r'"time_subscribed"\s+"(\d+)"', block)
        out[m.group(1)] = int(mt.group(1)) if mt else 0
    if not out and "publishedfileid" in txt:
        # 有"像 ID 字段"的字样却一条也认不出（字段被改坏、拼写异常）：
        # 这是"格式可疑"，不是"合法零订阅"。
        return None
    # 对账：文件里有多少条 publishedfileid，就必须认出多少条。
    # 只认出子集时返回 None —— 宁可让用户重试，也不要用子集做删除判定。
    if len(out) != txt.count('"publishedfileid"'):
        return None
    return out


def check_subs_consistency(P, expected_count=None, ui=None, allow_empty=False):
    """
    破坏性动作的订阅状态校验：
      1) vdf 必须可读（None 即中止）；
      2) 若提供排查时的订阅数，则当前数量必须与之一致（状态已变化就要求重查）；
      3) 清单为空但账本里还有记录 → 默认拒绝（allow_empty=True 表示用户已显式确认
         "确实全部退订了"，由界面在二次确认后传入）。
    返回 (ok, 说明)。
    """
    subs = read_subs_map(P)
    if subs is None:
        return False, ("订阅清单无法读取，无法确认订阅状态——"
                       "为避免误伤正常壁纸已中止。请确认 Steam 已同步后重试")
    if not subs and not allow_empty:
        # 第三道校验：空清单不能与「账本规模」矛盾。
        # Steam 崩溃留下的半截 vdf、被别的工具写坏的文件、切到了另一个账户，
        # 都会表现为"一条订阅都没有"。此时若账本里还躺着安装记录，
        # 那不是"用户全退订了"，而是清单不可信 —— 放行的后果是逐条清空账本。
        inst, _det = read_ledger_ids(P)
        n_inst = len(inst or ())
        if n_inst:
            return False, ("订阅清单是空的，但账本里还有 %d 条安装记录——"
                           "无法确认这些壁纸真的都已退订（可能是登录账户不对，"
                           "或订阅清单被写坏/截断）。\n"
                           "如果你确实刚刚全部退订、就是要清理账本，"
                           "请重新「一键排查」后再执行（届时会二次确认）" % n_inst)
    if expected_count is not None and len(subs) != expected_count:
        if ui:
            ui("[守卫] 订阅数量与最近一次排查不一致（排查时 %d，现在 %d）"
               % (expected_count, len(subs)))
        return False, ("订阅数量与最近一次排查不一致（排查时 %d，现在 %d）——"
                       "状态已变化，请先重新「一键排查」再执行操作"
                       % (expected_count, len(subs)))
    return True, ""


def read_ledger_ids(P):
    """账本两段的条目集合 → (installed_ids, details_ids)。
    账本缺失/读不到时返回 (None, None) —— 与「一条记录都没有」严格区分：
    读不到时说成空集，分类会把每条都判成类型 7（孤儿文件夹），
    用户就可能对着"其实读不到账本"的状态去删文件。"""
    acf = P.get("acf")
    if not acf or not isfile(acf):
        return None, None
    try:
        raw = _read_exact(acf)
    except Exception:
        return None, None
    # 用「数字键 + {」的条目头正则，避免把 manifest 等 18 位数字"值"误当 ID
    ii = _section_span(raw, "WorkshopItemsInstalled")
    di = _section_span(raw, "WorkshopItemDetails")
    if not ii or not di:
        return None, None
    inst = set(re.findall(r'"(\d{7,})"\s*\{', raw[ii[0]:ii[1]]))
    det = set(re.findall(r'"(\d{7,})"\s*\{', raw[di[0]:di[1]]))
    return inst, det


def _exists_in_any_library(P, iid):
    """工坊内容里是否真有这个文件夹（**含其它游戏库**）。

    只看主 content 目录会把「文件搬到别的库」误判成「缺文件」，进而判成类型 6
    （残尸）并允许清账本记录 —— 而自动修复路径对同一份数据是拒绝删除的。
    两条路径必须同口径：其它库里还有这个文件夹，就不是残尸。"""
    content = P.get("content")
    if content and os.path.isdir(os.path.join(content, iid)):
        return True
    for lib in (P.get("libraries") or []):
        p = os.path.join(lib, "steamapps", "workshop", "content", APPID, iid)
        if os.path.isdir(p):
            return True
    return False


def classify_batch(P, iids):
    """
    批量八态分类（动作执行前的守卫）。
    返回 {id: (type_no, 状态说明)}，type_no ∈ 1..8；订阅清单不可读时 type_no
    为 None（说明「订阅清单不可读」）——所有破坏性守卫必须拒绝 None。
    分类基于调用这一刻的账/订阅/硬盘实况，不依赖排查缓存。
    """
    subs = read_subs_map(P)
    inst, det = read_ledger_ids(P)
    out = {}
    for iid in iids:
        if subs is None:
            out[iid] = (None, "订阅清单不可读，无法确认订阅状态")
            continue
        if inst is None:
            out[iid] = (None, "账本不可读，无法确认安装状态")
            continue
        s = iid in subs
        a = iid in inst
        f = _exists_in_any_library(P, iid)
        # 三账直述（面向用户的简短状态，不用方案术语）
        table = {
            (True, True, True): (1, "三账一致"),
            (True, True, False): (2, "已订阅、有记录、缺文件"),
            (True, False, True): (3, "已订阅、缺记录、有文件"),
            (True, False, False): (4, "已订阅、缺记录、缺文件"),
            (False, True, True): (5, "未订阅、有记录、有文件"),
            (False, True, False): (6, "未订阅、有记录、缺文件"),
            (False, False, True): (7, "未订阅、无记录、有文件"),
            (False, False, False): (8, "三账皆无"),
        }
        out[iid] = table[(s, a, f)]
    return out


# ---------------------------------------------------------------------------
# 跳转类（类型 2/4；只打开页面，不写任何本地数据）
# ---------------------------------------------------------------------------

def steam_open(url):
    """用 Steam 客户端内置浏览器打开（失败时回退系统浏览器）。返回 (ok, 说明)"""
    try:
        os.startfile(url)            # noqa: S606 steam:// 协议由 Steam 注册处理
        return True, "已在 Steam 中打开"
    except Exception:
        pass
    try:
        webbrowser.open(url)
        return True, "已用系统浏览器打开（Steam 未响应，请留意登录状态）"
    except Exception as e:
        return False, "打开失败：%s" % e


def subscription_page_url(P, iid):
    """
    打开「我的订阅」列表页并尽量定位到 iid。
    返回 (url, page, position)：page=按订阅日期倒序推算的页码；
    position=该页从上往下第几个（列表默认排序即订阅日期倒序）；
    URL 额外携带 #Subscription<id> 锚点，部分浏览器会自动滚动到位（不保证）。
    """
    subs = read_subs_map(P)
    page, pos, rank = 1, None, None
    if subs and iid in subs:
        newer = sum(1 for t in subs.values() if t > subs[iid])
        rank = newer + 1
        page = (rank - 1) // PER_PAGE + 1
        pos = (rank - 1) % PER_PAGE + 1
    sep = "&" if "?" in SUB_LIST_URL else "?"
    url = (STEAM_OPEN_PREFIX + SUB_LIST_URL + "%sp=%d" % (sep, page)
           + "#Subscription" + iid)
    return url, page, pos, rank


def item_page_url(iid):
    return STEAM_OPEN_PREFIX + (ITEM_PAGE_URL % iid)


# ---------------------------------------------------------------------------
# 类型 7/5：转本地壁纸
# ---------------------------------------------------------------------------

# Windows 保留设备名（作目录名会导致创建失败）
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL",
                     *("COM%d" % i for i in range(1, 10)),
                     *("LPT%d" % i for i in range(1, 10))}


def sanitize_dirname(title, iid):
    """把标题净化成合法 Windows 目录名；保留名加前缀；失败时回退为工坊 ID"""
    bad = r'\/:*?"<>|'
    name = "".join(("_" if ch in bad or ord(ch) < 32 else ch) for ch in (title or ""))
    name = name.strip().strip(".")
    if name.upper().split(".")[0] in _WINDOWS_RESERVED:
        name = "_" + name
    return name or ("wallpaper_%s" % iid)


def _strip_workshop_fields(json_path, strip=True):
    """
    剥离工坊归属字段（workshopid / workshopurl），其余字段逐字不动。
    剥离前先把原文件存为 project.json.bak。
    失败消息区分阶段：备份失败=未做任何改动；写回失败=已回滚；
    回滚也失败=明确警告用户手动恢复。返回 (ok, 说明)。
    """
    if not strip:
        return True, "按设置保留工坊字段"
    try:
        with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception as e:
        return False, "project.json 无法解析（%s），已保留原样" % e
    if not isinstance(data, dict):
        return False, "project.json 不是对象结构，已保留原样"
    hit = [k for k in STRIP_FIELDS if k in data]
    if not hit:
        return True, "无需剥离（本来就没有工坊字段）"
    try:
        shutil.copy2(json_path, json_path + ".bak")
    except Exception as e:
        return False, "创建 .bak 备份失败，未做任何改动：%s" % e
    try:
        for k in hit:
            data.pop(k, None)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent="\t")
        with open(json_path, "r", encoding="utf-8") as f:
            json.load(f)                     # 写后回读校验
    except Exception as e:
        try:
            shutil.copy2(json_path + ".bak", json_path)
        except Exception as e2:
            return False, ("⚠ 剥离字段失败（%s）且回滚失败（%s）——副本的 "
                           "project.json 可能不完整，请用 .bak 手动恢复" % (e, e2))
        return False, "剥离字段失败（已从 .bak 回滚）：%s" % e
    return True, "已剥离 %s（原文件存为 .bak）" % "、".join(hit)


def convert_to_local(P, iid, strip_fields=True, ui=None, expected_sub_count=None):
    """
    类型 5/7 → 转本地壁纸：复制 content/<id> 到 myprojects/<净化标题>/
    （复制而非移动：原件留待用户决定是否删除；副本成功后才动账本）。
    类型 5 会在复制成功后自动清理账本记录（否则 Steam 侧残留脏记录）。
    expected_sub_count：排查时的订阅总数（GUI 传入），执行时数量不一致则中止。
    返回 (ok, 说明)。
    """
    def say(s):
        if ui:
            ui(s)

    ok_c, msg_c = check_subs_consistency(P, expected_sub_count)
    if not ok_c:
        return False, msg_c

    content = P.get("content") or ""
    folder = os.path.join(content, iid) if content else ""
    st_no, st_note = classify_batch(P, [iid]).get(iid, (8, "未知"))
    if st_no not in (5, 7):
        return False, ("守卫拦截：该条目当前是「%s」（类型 %s），"
                       "不属于可转换状态（需为未订阅的孤儿/幽灵安装）"
                       % (st_note, st_no))

    ok_h, why = _folder_healthy(folder)
    if not ok_h:
        return False, "内容不完整（%s），不适合转本地；建议删除或由 Steam 重下" % why

    we = P.get("we")
    if not we:
        return False, "未找到 Wallpaper Engine 安装目录，无法定位 myprojects"
    myproj = os.path.join(we, "projects", "myprojects")
    try:
        os.makedirs(myproj, exist_ok=True)
    except Exception as e:
        return False, "无法创建 myprojects 目录：%s" % e

    title = read_local_title(folder)
    name = sanitize_dirname(title, iid)
    dst = os.path.join(myproj, name)
    if os.path.exists(dst):
        cand = os.path.join(myproj, "%s_%s" % (name, iid))
        if os.path.exists(cand):
            return False, ("myprojects 中已存在同名目录：\n%s\n%s\n"
                           "请手动改名或清理后重试（本次未做任何改动）"
                           % (dst, cand))
        dst = cand

    say("[转本地] 正在复制 %s → %s ……" % (iid, dst))
    existed = os.path.exists(dst)       # 理论上必为 False（上面已排重）
    try:
        shutil.copytree(folder, dst)
    except Exception as e:
        # 中途失败（磁盘满/占用）会留下半成品，仅清理本次新建的目录
        if not existed:
            shutil.rmtree(dst, ignore_errors=True)
        return False, "复制失败（原件未动）：%s" % e

    ok_s, msg_s = _strip_workshop_fields(os.path.join(dst, "project.json"),
                                         strip=strip_fields)
    if not ok_s:
        say("[转本地] ⚠ " + msg_s)          # 剥离失败不回滚副本（副本仍可用）

    if st_no == 5:
        say("[转本地] 该条目账本有记录（类型 5），正在清理账本……")
        ok_lc, msg_lc = ledger_clear(P, [iid], ui=ui)
        if not ok_lc:
            return True, ("本地副本已创建：%s；但账本清理失败（%s）。"
                          "原文件夹暂不要删除，稍后用「清账本记录」处理"
                          % (dst, msg_lc))
    return True, ("转换成功：本地副本位于 %s；%s。"
                  "重启 Wallpaper Engine 后即可在「已安装（本地）」中看到"
                  % (dst, msg_s))


# ---------------------------------------------------------------------------
# 类型 7/5：删除到系统回收站
# ---------------------------------------------------------------------------

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p)]


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def _rb_count(drive):
    """回收站条目计数；查询失败返回 None（不阻塞删除主流程）"""
    try:
        qi = _SHQUERYRBINFO()
        qi.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        ret = ctypes.windll.shell32.SHQueryRecycleBinW(drive, ctypes.byref(qi))
        if ret != 0:
            return None
        return int(qi.i64NumItems)
    except Exception:
        return None


def _sh_recycle(path):
    """删除单个路径到系统回收站。返回 (gone, 说明)——以事后存在性复查为准；
    并用回收站计数前后对比尽量确认「确实进了回收站」而非被永久删除。"""
    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400
    drive = os.path.splitdrive(path)[0] + "\\"
    before = _rb_count(drive)
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = path + "\0"          # 必须双 \0 结尾
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    # 返回码不可靠（实测存在 ret=2 但成功的情况），以复查为准
    if not os.path.exists(path):
        return _recycle_verdict(path, drive, before)
    # 再给一次机会（个别占用是瞬时的）
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if not os.path.exists(path):
        return _recycle_verdict(path, drive, before)
    return False, "文件仍存在（可能被占用：请确认 Wallpaper Engine / 杀软已退出后重试）"


def _recycle_verdict(path, drive, before):
    """文件已消失后的判定：计数增加=确认进回收站；未增加=如实提示无法确认。"""
    after = _rb_count(drive)
    if before is None or after is None:
        return True, "已删除（回收站计数不可查询，无法确认是否可还原）"
    if after >= before + 1:
        return True, "已移入回收站"
    # 计数未增但文件消失：可能回收站被禁用（直接永久删除）或旧项被轮转清理
    return True, ("文件已删除，但未能确认进入回收站（回收站可能被禁用或旧项被"
                  "自动清理）——如需找回请先检查回收站")


def recycle_delete(P, iids, ui=None, expected_sub_count=None):
    """
    类型 5/7 → 把无主文件夹移入系统回收站（可还原，不是直接删除）。
    守卫：重查仍须「未订阅」；订阅状态不可读或数量与排查时不一致则中止；
    类型 5 先清账本（先账本后文件，失败即中止，
    避免「文件没了记录还在」的高危残尸态）。
    返回 (ok, 说明, 成功列表, 失败列表)。
    """
    def say(s):
        if ui:
            ui(s)

    ok_s, msg_s = check_subs_consistency(P, expected_sub_count)
    if not ok_s:
        return False, msg_s, [], []

    content = P.get("content") or ""
    if not content:
        return False, "工坊内容目录不可用", [], []
    cls = classify_batch(P, iids)
    allowed, type5 = [], []
    for iid in iids:
        st, note = cls.get(iid, (8, ""))
        if st not in (5, 7):
            say("[回收站] 跳过 %s（当前状态：%s，类型 %s）" % (iid, note, st))
            continue
        if not os.path.isdir(os.path.join(content, iid)):
            say("[回收站] 跳过 %s（文件夹已不存在）" % iid)
            continue
        allowed.append(iid)
        if st == 5:
            type5.append(iid)
    if not allowed:
        return False, "没有满足条件的条目（须为未订阅的孤儿/幽灵安装）", [], []

    if type5:
        say("[回收站] %d 个条目账本有记录，先清账本再删文件（防残尸）……" % len(type5))
        ok_c, msg_c = ledger_clear(P, type5, ui=ui)
        if not ok_c:
            return False, "账本清理失败（%s），已中止删除以避免残留脏记录" % msg_c, [], []

    done, failed = [], []
    for iid in allowed:
        folder = os.path.join(content, iid)
        errs = []
        size = dir_size(folder, errs)
        say("[回收站] 正在删除 %s（%s）……" % (iid, human(size)))
        gone, msg = _sh_recycle(folder)
        if gone:
            done.append(iid)
            say("[回收站] ✓ %s %s" % (iid, msg))
        else:
            failed.append((iid, msg))
            say("[回收站] ✗ %s %s" % (iid, msg))
    _action_log("回收站删除：成功 %s / 失败 %s"
                % (done or "无", [f[0] for f in failed] or "无"))
    ok = len(done) == len(allowed)
    msg = "已移入回收站 %d/%d 个%s" % (len(done), len(allowed),
                                      "；失败 %d 个" % len(failed) if failed else "")
    return ok, msg, done, failed


# ---------------------------------------------------------------------------
# 类型 5/6：清理账本记录（高危，须退出 Steam；复用 repair 安全链）
# ---------------------------------------------------------------------------

def _ledger_validate(orig, new, targets):
    """
    账本删条目的写入前校验（M3：按段独立）——
    目标在某段存在则该段必须恰好减去它，不存在则该段集合必须原样不变；
    至少一段发生删除才放行。另查：配平 / 段外字节 / 保留条目 kv / 目标残留。
    返回 (ok, 问题列表)。
    """
    probs = []
    tset = set(targets)
    if new.count("{") != new.count("}"):
        probs.append("花括号不配平")
    spans = []
    for seg in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        o_sp = _section_span(orig, seg)
        n_sp = _section_span(new, seg)
        if not (o_sp and n_sp):
            probs.append("段「%s」定位失败" % seg)
            continue
        spans.append((seg, o_sp, n_sp))
    if len(spans) != 2:
        return False, probs
    (seg1, o1, n1), (seg2, o2, n2) = spans
    # 段外字节（表头 / 段间连接部 / 详情段之后）必须逐字节不变
    if new[:n1[2]] != orig[:o1[2]]:
        probs.append("表头被改动")
    if new[n1[1] + 1:n2[0]] != orig[o1[1] + 1:o2[0]]:
        probs.append("段落连接部被改动")
    if new[n2[1]:] != orig[o2[1]:]:
        probs.append("文件尾被改动")

    removed_any = False
    for seg, o_sp, n_sp in spans:
        o_items = _parse_items_precise(orig[o_sp[0]:o_sp[1]])
        n_items = _parse_items_precise(new[n_sp[0]:n_sp[1]])
        gone = set(o_items) - set(n_items)
        expect_gone = set(o_items) & tset
        if gone != expect_gone:
            probs.append("段「%s」删除集合与预期不符（预期 %s，实际 %s）"
                         % (seg, sorted(expect_gone) or "无", sorted(gone) or "无"))
            continue
        added = set(n_items) - set(o_items)
        if added:
            probs.append("段「%s」出现新增条目：%s" % (seg, sorted(added)))
            continue
        if gone:
            removed_any = True
        for fid, kv in o_items.items():
            if fid not in tset and n_items.get(fid) != kv:
                probs.append("保留条目 %s 在段「%s」中被改动" % (fid, seg))
                break
        # 目标不得以任何形态残留在本段（键/值/子串均不允许）
        for t in tset:
            if re.search(r'"%s"' % re.escape(t), new[n_sp[0]:n_sp[1]]):
                probs.append("目标 %s 仍残留在段「%s」中" % (t, seg))
    if not removed_any and not probs:
        probs.append("两段均未发生删除（目标可能不在账本中）")
    return (not probs), probs


def ledger_clear(P, iids, ui=None, allow_empty_subs=False):
    """
    类型 5/6 → 从账本（acf）两段中删除指定条目记录。
    安全链：退出检查 → 订阅守卫 → 备份 → 重建（仅删除目标块，其余逐字节保留）
           → 校验 → 原子写 → 失败自动回滚。
    allow_empty_subs=True 表示用户已在界面上显式确认"订阅清单确实为空"，
    仅用于"全部退订后清理账本"这种合法但少见的流程。
    返回 (ok, 说明)。
    """
    def say(s):
        if ui:
            ui(s)

    ok_p, msg_p = repair_precheck(P)
    if not ok_p:
        return False, msg_p
    # 订阅侧守卫：这条路径以前只靠 classify_batch —— 而"空/截断的 vdf"会被
    # 分类成"未订阅"，于是 78 条记录可以被逐条清空。这里补上与转本地/回收站
    # 同一道 check_subs_consistency（含"空清单 vs 账本规模"交叉校验）。
    ok_s, msg_s = check_subs_consistency(P, allow_empty=allow_empty_subs)
    if not ok_s:
        return False, msg_s
    # 守卫：只允许未订阅的条目（类型 5/6）；订阅中的条目清了会被 Steam 重写
    cls = classify_batch(P, iids)
    targets = []
    for iid in iids:
        st, note = cls.get(iid, (8, ""))
        if st in (5, 6):
            targets.append(iid)
        else:
            say("[账本] 跳过 %s（当前状态：%s，类型 %s —— 订阅中的条目不能清记录）"
                % (iid, note, st))
    if not targets:
        return False, "没有可清理的条目（须为未订阅且有账本记录）"

    acf = P.get("acf")
    if not acf or not isfile(acf):
        return False, "账本文件不可用"

    ok_b, bak = repair_backup(P)
    if not ok_b:
        return False, bak
    say("[账本] 已备份 → %s" % bak)

    try:
        orig = _read_exact(acf)
    except Exception as e:
        return False, "读取账本失败：%s" % e

    tgt = set(targets)
    new_text = orig
    total_removed = 0
    for seg in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        span = _section_span(new_text, seg)
        if not span:
            return False, "账本缺少「%s」段（结构异常），已中止" % seg
        body = new_text[span[0]:span[1]]
        # 与 read_ledger_ids 相同口径：数字键 + { （避免把 size/manifest 等
        # 18 位数字"值"误当成目标记录存在）
        present = set(re.findall(r'"(\d{7,})"\s*\{', body)) & tgt
        if not present:
            continue
        blocks = [(m.start(), m.end(), m.group(1))
                  for m in RE_LEDGER_BLOCK.finditer(body)
                  if m.group(1) in present]
        if len(blocks) != len(present):
            missing = sorted(present - {b[2] for b in blocks})
            return False, ("段「%s」中 %d 个目标无法按标准块格式定位：%s"
                           "（条目缩进可能非标准或结构异常），已中止"
                           % (seg, len(missing), "、".join(missing)))
        new_body = body
        for start, end, _fid in sorted(blocks, reverse=True):
            new_body = new_body[:start] + new_body[end:]
            total_removed += 1
        new_text = new_text[:span[0]] + new_body + new_text[span[1]:]

    if total_removed == 0:
        return False, "账本中未找到目标条目（可能已被清理）"

    # 与自动修复同一条下限约定：大库不许被逐条点到某一段为空。
    # （小库只有一两条记录时，"清完就空"是正常结果，不能拦。）
    for seg in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        sp_o = _section_span(orig, seg)
        sp_n = _section_span(new_text, seg)
        if not (sp_o and sp_n):
            continue
        n_before = len(re.findall(r'"(\d{7,})"\s*\{', orig[sp_o[0]:sp_o[1]]))
        n_after = len(re.findall(r'"(\d{7,})"\s*\{', new_text[sp_n[0]:sp_n[1]]))
        if n_before >= 8 and n_after == 0:
            return False, ("清理后「%s」段会变成空段（这正是本工具要修的故障），已中止；"
                           "如果确实要清空整个账本，请先用备份手动处理" % seg)

    ok_v, probs = _ledger_validate(orig, new_text, tgt)
    if not ok_v:
        say("[账本] 校验未通过：" + "；".join(probs[:6]))
        return False, "写入前校验未通过，已中止（账本未被改动）"

    tmp = acf + ".ledger_tmp"
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape",
                  newline="") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        if _read_exact(tmp) != new_text:
            raise RuntimeError("临时文件回读不一致")
        os.replace(tmp, acf)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False, "写入失败：%s" % e

    say("[账本] 已删除 %d 条记录（%s）✓" % (len(targets), "、".join(targets)))
    _action_log("账本清理：%s  备份=%s" % ("、".join(targets),
                                         os.path.basename(bak)))
    return True, "已清理 %d 条账本记录" % len(targets)


# ---------------------------------------------------------------------------
# 公共小件
# ---------------------------------------------------------------------------

def read_local_title(folder):
    """读取本地工坊文件夹的标题（project.json 的 title 字段），失败返回空串"""
    try:
        with open(os.path.join(folder, "project.json"), "r",
                  encoding="utf-8", errors="ignore") as f:
            j = json.load(f)
        t = j.get("title") or ""
        return t.strip() if isinstance(t, str) else ""
    except Exception:
        return ""


def _action_log(msg):
    """动作审计日志（追加到 backups/修复记录.log）"""
    try:
        bdir = os.path.join(BASE_DIR, "backups")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, REPAIR_LOG_NAME), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (now_str(), msg))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 网络检测（社区可达性）
# ---------------------------------------------------------------------------
def check_network(timeout=4):
    """
    检测 steamcommunity.com 当前是否可达（加速器是否生效）。
    先系统代理后直连（与 _http_steam 同策略）。返回 (ok, 说明)。
    """
    try:
        status, _setc, _body = _http_steam(
            "https://steamcommunity.com/?l=english", timeout=timeout)
        ok = 200 <= status < 400
        return ok, ("可达（HTTP %d）" % status) if ok else \
            ("异常响应（HTTP %d）" % status)
    except Exception as e:
        return False, ("不可达——请开启 Watt Toolkit/Steam++ 等加速器的"
                       "「Steam 社区」加速（%s）" % str(e)[:60])


def _http_steam(uri, data=None, cookie="", timeout=8):
    """带 cookie 的 HTTP（urllib）。先走系统代理，失败自动降级直连。
    两者都失败时抛出的异常会附带网络指引（steamcommunity 需加速器可达）。"""
    import urllib.error
    import urllib.request
    headers = {"User-Agent": "Mozilla/5.0 (WE-Mender)",
               "Referer": "https://steamcommunity.com/"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    if cookie:
        headers["Cookie"] = cookie
    body = (urllib.parse.urlencode(data).encode("utf-8")
            if data is not None else None)
    last_err = None
    for use_proxy in (True, False):
        try:
            handlers = []
            if not use_proxy:
                handlers.append(urllib.request.ProxyHandler({}))
            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(uri, data=body, headers=headers)
            with opener.open(req, timeout=timeout) as r:
                setc = [v for k, v in (r.headers.items())
                        if k.lower() == "set-cookie"]
                return r.status, setc, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            # 服务器有响应（如 401/403 认证拒绝）——与网络不可达区分开
            raise OSError("steamcommunity.com 返回 HTTP %d（请求被服务器拒绝）"
                          % e.code)
        except Exception as e:
            last_err = e
    raise OSError(
        "当前网络无法访问 steamcommunity.com（%s）——请打开 Watt Toolkit/"
        "Steam++ 等加速器，启动「Steam 社区」加速（Hosts 或系统代理模式），"
        "加速生效后重试" % last_err)


