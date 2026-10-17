# -*- coding: utf-8 -*-
"""
WE-Mender 3.0 图形界面（深色 · xAI 风）

- 引擎全部复用 wemender 包（诊断 / 一键修复），本文件只做界面与线程调度
- [一键排查] 只读诊断，生成/更新「排查结果.txt」
- [自动修复] 仅在检出「可自动修复」问题时可用：修复 → 自动复检 → 更新报告
- 修复前自动校验 Steam 与 Wallpaper Engine 已完全退出（不满足会拒绝并提示）
"""

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import urllib.parse
import urllib.request
from tkinter import ttk, messagebox

from wemender import actions
from wemender.common import BASE_DIR, copy_to_clipboard, running_procs
from wemender.checks import MODES
from wemender.paths import resolve_paths
from wemender.pipeline import collect_and_render, write_report
from wemender.repair import repair_run

_TITLES_CACHE = os.path.join(BASE_DIR, "titles_cache.json")

# ══════════════════════════════════════════════════════════════
#  视觉层（xAI 风深色令牌；整块删掉程序照跑）
# ══════════════════════════════════════════════════════════════

T = {
    "BG": "#0a0a0a", "CARD": "#191919", "TABLE": "#141414", "SOFT": "#1a1c20",
    "MID": "#363a3f", "LINE": "#212327", "EDGE_F": "#2b2e33", "EDGE": "#3f3f3f",
    "EDGE_H": "#6b6b6b", "INK": "#ffffff", "BODY": "#dadbdf", "MUTE": "#7d8187",
    "FAINT": "#5a5e63", "ACCENT": "#ff7a17",
}

FONTS = {}


def _resolve_fonts(root):
    """运行时探测字体（必须在建控件前调用）。中文一律走界面字体，负数值=像素字号。"""
    fams = set(tkfont.families(root))

    def pick(*cands):
        for c in cands:
            if c in fams:
                return c
        return "Segoe UI"

    ui = pick("Microsoft YaHei UI", "微软雅黑", "Noto Sans SC", "Segoe UI")
    latin = pick("Cascadia Mono", "Consolas", "Courier New")
    FONTS["ui20"] = (ui, -20)
    FONTS["ui16"] = (ui, -16)
    FONTS["ui14"] = (ui, -14)
    FONTS["ui13"] = (ui, -13)
    FONTS["ui12"] = (ui, -12)
    FONTS["ui11"] = (ui, -11)
    FONTS["latin12"] = (latin, -12)


# ── 单条壁纸行：三账徽标 ────────────────────────────────────────
# 八态 → (订, 记, 盘) 三个布尔位。与 actions.classify_batch 的判定同源，
# 这里只做展示映射，避免再读一遍文件、也保证与分类结果永远一致。
STATE_BITS = {
    1: (1, 1, 1), 2: (1, 1, 0), 3: (1, 0, 1), 4: (1, 0, 0),
    5: (0, 1, 1), 6: (0, 1, 0), 7: (0, 0, 1), 8: (0, 0, 0),
}
# 中间那列的一句话说明，规则：
#   只缺一项 → 说"缺什么"；缺两项 → 说"只剩什么"；三项全缺 → 三账皆无
STATE_HINT = {
    1: "三账一致",
    2: "缺文件",
    3: "缺记录",
    4: "只有订阅",
    5: "缺订阅",
    6: "只有记录",
    7: "只有文件",
    8: "三账皆无",
}

# 卡片标题用：状态短语 + 一句人话说明
STATE_LABEL = {
    2: "文件缺失",
    3: "账本漏登",
    4: "没有下载",
    5: "无主文件",
    6: "残留记录",
    7: "无主文件",
    8: "无残留",
}
STATE_GUIDE = {
    2: "让 Steam 重新下载；长时间拉不到可能该物品已下架",
    3: "可点「自动修复」补录；或让 Steam 校验一次文件",
    4: "去订阅列表取消，或等 Steam 自己下载",
    5: "「清账本记录」清掉残留；文件可「转本地壁纸」保留",
    6: "「清账本记录」清掉残留，否则 Steam 可能整库重下",
    7: "「转本地壁纸」保留，「删除到回收站」清理",
    8: "无需处理",
}
STATE_KIND = {2: "missing", 3: "repair", 4: "missing",
              5: "orphan", 6: "orphan", 7: "orphan", 8: "orphan"}

# 按钮悬停说明：一行短句，出现在按钮下方
BTN_DESC = {
    "open_sub": "打开订阅列表定位",
    "open_item": "打开该壁纸页面",
    "resub": "重新订阅并下载",
    "clear": "删除残留记录",
    "convert": "复制到本地目录",
    "recycle": "移入回收站",
    "open_folder": "打开所在文件夹",
}
_OK_GREEN = "#82c882"

class CopyEntry(tk.Canvas):
    """只读、可选中复制的单行文字（自绘，不是输入框）。

    为什么不用 tk.Entry：Tk 的 Entry 在鼠标按下时会真的抢键盘焦点
    （见 Tcl 绑定 ::tk::EntryButton1 里的 `focus $w`，readonly 也照抢）。
    一旦拿到焦点，Windows 就会接管光标 / 输入法 / 焦点框 —— 在深色界面上
    表现为点击瞬间的"白框"或一闪一闪的竖线。这些都由系统画，改 Tk 的
    配色压不住。所以改成 Canvas 自绘：从不申请焦点，选中色与文字全部自己画，
    系统没有任何插手的机会。

    交互（与"选中复制"的直觉一致）：
      · 单击 = 全选（整行高亮，Ctrl+C 直接复制全文）
      · 拖动 = 选中一段（文字超宽被省略时不支持拖动，自动退回全选）
      · 双击 = 全选；右键 = 复制 / 全选
    接口保持与原 Entry 版一致：set_text / clear_selection / selection_present / _copy。
    """

    SEL_BG = "#264f78"
    SEL_FG = "#ffffff"

    def __init__(self, master, text="", **kw):
        self._bg = kw.pop("bg", T["CARD"])
        self._fg = kw.pop("fg", T["BODY"])
        font = kw.pop("font", FONTS["ui12"])
        width_chars = kw.pop("width", None)
        padx = kw.pop("padx", 0)
        kw.pop("anchor", None)
        self._font = tkfont.Font(root=master, font=font)
        self._lh = self._font.metrics("linespace")
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0,
                         takefocus=0, height=self._lh + 2, cursor="xterm", **kw)
        self._padx = padx
        self._text = text
        self._disp = text
        self._elided = False
        self._sel = None            # (a, b)：原文下标区间；None=未选中
        self._anchor = None
        self._press_x = 0
        self._moved = False
        self._last_layout = None    # 上次重画时的 (可用宽, 高)，用于跳过无谓重画
        # Entry 的 width 是"字符数"，这里沿用：至少这么宽，正文更长时以正文为准
        self._min_w = int(width_chars) * self._font.measure("0") if width_chars else 0
        self._fit_width()
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Button-3>", self._popup)

    # ---------- 尺寸与绘制 ----------

    def _fit_width(self):
        """申请宽度只取「最少字符数」，绝不按全文宽度申请 ——
        否则长路径会把整张卡片撑宽、逼出横向滚动条（Entry 时代 width=1 同理）。
        实际宽度交给父容器的 fill/sticky，正文超宽时自己省略。"""
        want = max(self._min_w, 1) + 2 * self._padx + 2
        self.configure(width=want)

    def _elide(self, text, avail):
        """中间省略：保住开头的盘符与结尾的 ID（尾部通常才是关键信息）"""
        if avail <= 0:
            return ""
        if self._font.measure(text) <= avail:
            return text
        if self._font.measure("…") > avail:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            left = (mid + 1) // 2
            right = mid // 2
            s = text[:left] + "…" + (text[-right:] if right else "")
            if self._font.measure(s) <= avail:
                lo = mid
            else:
                hi = mid - 1
        left = (lo + 1) // 2
        right = lo // 2
        return text[:left] + "…" + (text[-right:] if right else "")

    def _index_at(self, x):
        """鼠标 x → 字符下标（只在未省略时可用）"""
        x = max(0, x - self._padx)
        best, best_d = 0, None
        for i in range(len(self._disp) + 1):
            d = abs(self._font.measure(self._disp[:i]) - x)
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def _redraw(self, force=False):
        """force=False 时只在尺寸变化后重画 —— 窗口拖动时每个控件都会收到
        <Configure>，79 项 × 3 处重复重画没有必要；选中态变化要显式 force。"""
        w = self.winfo_width()
        h = self.winfo_height()
        avail = max(0, w - 2 * self._padx - 2)
        if avail <= 1:
            return
        if not force and (avail, h) == self._last_layout:
            return
        self._last_layout = (avail, h)
        self.delete("all")
        self._disp = self._elide(self._text, avail)
        self._elided = self._disp != self._text
        if self._elided and self._sel and self._sel != (0, len(self._text)):
            # 进入省略态后，片段选中的下标对不上原文（高亮也画不出来）。
            # 不清掉的话会出现"视觉上什么都没选、Ctrl+C 却粘出全文"的矛盾状态。
            self._sel = None
        cy = h / 2.0
        x0 = self._padx + 1
        # 选中区间统一按「原文下标」存；渲染时换算成显示下标。
        # 整行选中用 (0, len(原文)) 这个规范值表示，省略显示时也能一眼认出。
        a = b = 0
        sel = self._sel
        if sel:
            if sel == (0, len(self._text)):
                a, b = 0, len(self._disp)
            elif not self._elided:
                a = max(0, min(sel[0], len(self._disp)))
                b = max(0, min(sel[1], len(self._disp)))
        if b > a:
            xa = x0 + self._font.measure(self._disp[:a])
            xb = x0 + self._font.measure(self._disp[:b])
            self.create_rectangle(xa, 1, xb, self._lh + 1,
                                  fill=self.SEL_BG, outline="")
            self.create_text(x0, cy, text=self._disp, anchor="w",
                             fill=self._fg, font=self._font)
            self.create_text(xa, cy, text=self._disp[a:b], anchor="w",
                             fill=self.SEL_FG, font=self._font)
        else:
            self.create_text(x0, cy, text=self._disp, anchor="w",
                             fill=self._fg, font=self._font)

    # ---------- 选中 ----------

    def set_text(self, text):
        self._text = text
        self._sel = None
        self._fit_width()
        self._redraw(force=True)

    def clear_selection(self):
        if self._sel is not None:
            self._sel = None
            self._redraw(force=True)

    def selection_present(self):
        return bool(self._sel and self._sel[1] > self._sel[0])

    def _select_all(self, _e=None):
        self._sel = (0, len(self._text))
        self._redraw(force=True)
        return "break"

    def _on_press(self, e):
        self._press_x = e.x
        self._moved = False
        if self._elided:
            self._select_all()
            return None
        self._anchor = self._index_at(e.x)
        self._select_all()          # 单击先全选；真拖动时再切成区间
        return None

    def _on_drag(self, e):
        if self._elided or self._anchor is None:
            return None
        if abs(e.x - self._press_x) > 2:
            self._moved = True
        if not self._moved:
            return None
        i = self._index_at(e.x)
        self._sel = (min(self._anchor, i), max(self._anchor, i))
        self._redraw(force=True)
        return None

    def _on_double(self, _e=None):
        self._select_all()
        return "break"

    def _copy(self, _e=None):
        if self.selection_present() and not self._elided:
            txt = self._disp[self._sel[0]:self._sel[1]]
        else:
            txt = self._text          # 省略显示时复制全文，避免粘贴到半截路径
        if not txt:
            return "break"            # 空字段不动作：不能把用户剪贴板清成空串
        # 顺序要紧：先写 Tk 剪贴板（应用内 clipboard_get 立刻可读），
        # 再调 clip.exe 把内容交给系统。反过来的话 Tk 会重新成为剪贴板所有者，
        # 进程一退内容就没了 —— 等于白改。
        try:
            self.clipboard_clear()
            self.clipboard_append(txt)
        except tk.TclError:
            pass
        copy_to_clipboard(txt)
        cb = getattr(self, "on_copy", None)
        if cb:
            try:
                cb(txt)
            except Exception:
                pass
        return "break"

    def _make_menu(self):
        m = tk.Menu(self, tearoff=0, bd=0, bg=T["EDGE_F"], fg=T["BODY"],
                    activebackground=T["MID"], activeforeground=T["INK"])
        m.add_command(label="复制", command=self._copy)
        m.add_command(label="全选", command=self._select_all)
        return m

    def _popup(self, e):
        if not hasattr(self, "_menu"):
            self._menu = self._make_menu()
        try:
            self._menu.tk_popup(e.x_root, e.y_root)
        finally:
            self._menu.grab_release()
        return "break"


def _set_item_title(lab, text):
    """异步拿到壁纸名后回填标题（CopyEntry 有 set_text，换完重新排版）"""
    setter = getattr(lab, "set_text", None)
    try:
        if setter is not None:
            setter(text)
        else:
            lab.configure(text=text)
    except Exception:
        pass        # 视图已重建、控件已销毁：丢弃这次回填即可


def _hex_rgb(h):
    """#rrggbb → (r, g, b)。非标准色值（颜色名、空串）回落为透明黑，
    避免 PillButton 在异常父容器里直接抛 ValueError 把整块界面带崩。"""
    try:
        h = str(h).strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 0, 0, 0


def _cov(d):
    if d <= -0.5:
        return 1.0
    if d >= 0.5:
        return 0.0
    return 0.5 - d


def _mix(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


_PILL_CACHE = {}


def pill_image(w, h, fill, edge, bg):
    """
    零依赖抗锯齿胶囊：按有符号距离场光栅化，颜色混合到已知纯色底上，
    输出不透明 PhotoImage（Tk 自绘圆角无抗锯齿，不能直接用 Oval/Rect）。
    """
    key = (w, h, fill, edge, bg)
    img = _PILL_CACHE.get(key)
    if img is not None:
        return img
    r = (h - 1) / 2.0
    xa, xb = r, w - 1 - r
    cy = (h - 1) / 2.0
    f_rgb, e_rgb, b_rgb = _hex_rgb(fill), _hex_rgb(edge), _hex_rgb(bg)
    rows = []
    for y in range(h):
        qy = y - cy
        cells = []
        for x in range(w):
            if x < xa:
                qx = x - xa
            elif x > xb:
                qx = x - xb
            else:
                qx = 0.0
            d = (qx * qx + qy * qy) ** 0.5 - r
            c_f = _cov(d)
            c_e = _cov(d) - _cov(d + 1)
            col = b_rgb
            if c_f:
                col = _mix(col, f_rgb, c_f)
            if c_e:
                col = _mix(col, e_rgb, c_e)
            cells.append("#%02x%02x%02x" % col)
        rows.append("{" + " ".join(cells) + "}")
    img = tk.PhotoImage(width=w, height=h)
    img.put(" ".join(rows))
    _PILL_CACHE[key] = img
    return img


# variant: (填充, 描边, 文字色, 悬停填充, 禁用填充, 禁用描边, 禁用文字)
VARIANTS = {
    "primary": (T["ACCENT"], T["ACCENT"], "#0f0f0f", "#ff8c3a",
                "#3a2a1a", "#4a3a26", "#a9886a"),
    "normal": (T["SOFT"], T["EDGE"], T["INK"], "#23262b",
               "#161719", "#2a2c2f", T["FAINT"]),
}


class PillButton(tk.Canvas):
    """自绘胶囊按钮；兼容 configure(text=..., state=...) 的调用面。"""

    _text = ""
    _disabled = False
    _bw = 120
    _bh = 36

    def __init__(self, master, text="", command=None, variant="normal",
                 width=None, height=36, **kw):
        f = FONTS["ui13"]
        w = width or (tkfont.Font(root=master, font=f).measure(text) + 52)
        # 胶囊衬底必须与父容器同色，否则圆角外会露出一圈矩形底色（近黑）。
        # 默认跟随父容器；调用方显式传入 bg 时尊重调用方。
        try:
            parent_bg = kw.pop("bg", None) or master.cget("bg")
        except Exception:
            parent_bg = T["BG"]
        super().__init__(master, width=w, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0, **kw)
        self._bw, self._bh = w, height
        self._variant = variant
        self._command = command
        self._disabled = False
        self._hover = False
        self._bg = parent_bg
        self._text = text
        self._img_id = self.create_image(w / 2, height / 2)
        self._txt_id = self.create_text(w / 2, height / 2, text=text, font=f)
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _render(self):
        v = VARIANTS[self._variant]
        if self._disabled:
            fill, edge, ink = v[4], v[5], v[6]
        elif self._hover:
            fill, edge, ink = v[3], v[1], v[2]
        else:
            fill, edge, ink = v[0], v[1], v[2]
        img = pill_image(self._bw, self._bh, fill, edge, self._bg)
        self.itemconfigure(self._img_id, image=img)
        self.itemconfigure(self._txt_id, text=self._text, fill=ink)

    def _on_enter(self, _e):
        if not self._disabled:
            self._hover = True
            self.configure(cursor="hand2")
            self._render()

    def _on_leave(self, _e):
        self._hover = False
        self._render()

    def _on_click(self, _e):
        if not self._disabled and self._command:
            self._command()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        text = kw.pop("text", None)
        state = kw.pop("state", None)
        if text is not None:
            self._text = text
        if state is not None:
            self._disabled = (str(state) == str(tk.DISABLED))
        if text is not None or state is not None:
            self._render()
        if kw:
            super().configure(**kw)

    config = configure


def apply_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", background=T["BG"], foreground=T["BODY"],
                    fieldbackground=T["SOFT"], bordercolor=T["LINE"],
                    lightcolor=T["LINE"], darkcolor=T["LINE"],
                    focuscolor=T["LINE"], troughcolor=T["TABLE"])
    style.configure("Vertical.TScrollbar", background=T["MID"], troughcolor=T["TABLE"],
                    bordercolor=T["TABLE"], arrowcolor=T["MUTE"],
                    lightcolor=T["MID"], darkcolor=T["MID"])
    style.map("Vertical.TScrollbar", background=[("active", T["EDGE_H"])])
    root.configure(bg=T["BG"])


def _set_dark_titlebar(root, _st={"t": 0.0}):
    """深色标题栏（hwnd 必须取 wm_frame()；Map 会连发多次，用 2s 去重）"""
    import time
    now = time.time()
    if now - _st["t"] < 2:
        return
    _st["t"] = now
    try:
        import ctypes
        hwnd = int(root.wm_frame(), 16)
        v = ctypes.c_int(1)
        applied = False
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0:
                applied = True
                break
        if applied:
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  屏幕阅读版视图（三账状态 + 差异对策 + 其他问题）
# ══════════════════════════════════════════════════════════════

# read_local_title 已统一到 wemender.actions.read_local_title（避免双实现漂移）


def _load_title_cache():
    try:
        with open(_TITLES_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_title_cache(d):
    """原子写：多张卡片的后台线程会同时回写，直接 open("w") 可能互相截断，
    留下半截 JSON —— 下次加载静默失败，整份缓存丢失。"""
    try:
        tmp = _TITLES_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _TITLES_CACHE)
    except Exception:
        pass


def fetch_published_titles(ids, timeout=8):
    """
    通过 Steam Web API 获取工坊物品标题（无需登录，可识别下架状态）。
    返回 {id: 标题或 None}；None 表示 Steam 上已不可见（多半已被作者下架）。
    网络异常时抛出异常（由调用方兜底）。
    """
    data = [("itemcount", str(len(ids)))]
    data += [("publishedfileids[%d]" % i, v) for i, v in enumerate(ids)]
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        "https://api.steampowered.com/ISteamRemoteStorage/"
        "GetPublishedFileDetails/v1/",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "ignore"))
    out = {}
    details = (j.get("response") or {}).get("publishedfiledetails") or []
    for it in details:
        pid = str(it.get("publishedfileid", ""))
        if it.get("result") == 1 and it.get("title"):
            out[pid] = str(it["title"]).strip()
        else:
            out[pid] = None
    return out


def build_overview_model(P, D, issues):
    """
    屏幕阅读版数据模型：
      counts: [(标签, 数值, 单位), ...]
      status: (颜色标签, 文案)
      diffs:  [{key, head, guide, kind, items}]（kind ∈ repair/orphan/missing）
      others: [其余问题, ...]
    """
    subs_n = (D.get("subs_total") or len(D.get("subs") or [])
              if D.get("subs_state") in ("ok", "empty") else None)
    inst_n = D.get("installed_count")
    dirs_n = len(D.get("dirs") or []) if D.get("content_exists") else None
    fmt = lambda v: "数据不可用" if v is None else "%d" % v
    counts = [("订阅（目标）", fmt(subs_n), "项"),
              ("安装记录（记忆）", fmt(inst_n), "条"),
              ("硬盘文件（事实）", fmt(dirs_n), "个")]

    need = list(D.get("repairable") or [])
    need_add = list(D.get("repairable_add") or [])
    need_del = list(D.get("repairable_del") or [])
    if need_add or need_del:
        # 两个方向要分别说清楚：只说「安装记录缺失 N 条」会把删除项也算进去，
        # 与实际行为（这些条目根本没有文件）相反。
        bits = []
        if need_add:
            bits.append("补录 %d 条漏登记录（文件都在硬盘上）" % len(need_add))
        if need_del:
            bits.append("移除 %d 条残留记录（已不订阅、文件也没了）" % len(need_del))
        status = ("warn", "⚠ 可自动修复：%s —— 只改账本记录，不碰文件" % "；".join(bits))
    elif inst_n == 0 and dirs_n:
        status = ("warn", "⚠ 安装记录为 0 条 —— 所有已下载的壁纸都会被误判「未安装」")
    elif P.get("errors"):
        status = ("warn", "⚠ 路径探测未完全成功，结果可能不完整")
    elif not issues:
        status = ("ok", "✓ 三方一致，未发现问题")
    else:
        worst = "严重" if any(i["level"] == "严重" for i in issues) else \
                ("提醒" if any(i["level"] == "提醒" for i in issues) else "提示")
        status = ({"严重": "sev", "提醒": "warn", "提示": "sub"}[worst],
                  "⚠ 发现 %d 个问题（最高级别：%s）—— 差异见下，其余见问题清单"
                  % (len(issues), worst))

    diffs = []
    if inst_n == 0 and dirs_n:
        diffs.append(dict(key="wipe", kind="repair",
                          head="安装记录为空：0 条 —— 所有已下载的壁纸都会被误判「未安装」",
                          guide=("本工具可自动修复（点「自动修复」，执行前会自动备份账本）" if need
                                 else "本工具当前无法自动修复，请查看下方问题清单"),
                          items=need))
    else:
        # 按「八态」分组：一张卡 = 一种真实三账状态，标题直接写状态本身，
        # 不再用「有文件、没订阅」这种比对口径（那是两个维度拼出来的说法）
        ids = []
        for k in ("sub_no_content", "record_no_dir", "dir_no_sub", "dir_no_record"):
            for i in (D.get(k) or []):
                if i not in ids:
                    ids.append(i)
        cls = {}
        try:
            if P and ids:
                cls = actions.classify_batch(P, ids)
        except Exception:
            cls = {}
        groups = {}
        for i in ids:
            st = (cls.get(i) or (None,))[0]
            if st and st != 1:
                groups.setdefault(st, []).append(i)
        if not groups and ids:
            groups = {None: ids}        # 分类不可用时退回，至少把差异列出来
        for st in sorted(groups, key=lambda x: (x is None, x or 0)):
            diffs.append(dict(
                key="state_%s" % st, kind=STATE_KIND.get(st, "missing"),
                st=st, items=groups[st],
                head="%d 个" % len(groups[st]),
                guide=STATE_GUIDE.get(st, "展开后按右侧按钮逐项处理")))

    covered = []
    if D.get("dir_no_sub"):
        covered.append("无主文件夹")
    if need or (inst_n == 0 and dirs_n):
        covered += ["未被账本记录", "数量不一致", "安装记录被清空", "两段记录均为空"]
    elif D.get("dir_no_record"):
        covered.append("未被账本记录")
    if D.get("sub_no_content"):
        covered.append("已订阅但硬盘上没有内容")
    if D.get("record_no_dir"):
        covered.append("记录指向不存在")
    others = [i for i in issues if not any(k in i["title"] for k in covered)]
    return dict(counts=counts, status=status, diffs=diffs, others=others)


# ══════════════════════════════════════════════════════════════
#  应用
# ══════════════════════════════════════════════════════════════

class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.P = None
        self.D = None
        self.report_text = ""
        self.refresh_text = ""      # 轻量刷新另存：不与「完整报告」混用
        self.busy = False
        self._net_ok = None         # 社区网络状态：True/False/None(未检测)
        self._net_ts = 0.0          # 上次网络检测时间戳（60 秒周期）
        self._fp = None             # 三账指纹基线（哨兵自动刷新用）
        self._fp_prev = None        # 上一拍基线（自动刷新失败时回滚用）
        self._auto_fails = 0        # 自动刷新连续失败次数（限次重试，避免刷屏）
        self._auto_refresh = False   # 哨兵/手动刷新的 fatal 降级标志
        self._last_was_refresh = False  # 轻量刷新视图标志（影响提示条/脚注）
        self._btn_hint = {"win": None}   # 悬停提示气泡（同一时间最多一个）
        self._action_btns = []      # 动作按钮元数据（网络/凭据灰控 + 悬停提示）
        self._details = {}        # key -> (frame, build_fn)
        self._toggles = {}        # key -> PillButton
        self._expanded = {}       # key -> bool
        self._title_labels = {}   # diff key -> {id: Label}
        self._wrap_labels = []    # [(Label, 右侧预留像素)]：窗口宽度变化时统一重算换行宽
        self._last_wrap_w = 0
        self._copy_entries = []   # 可复制的输入框：点别处要统一取消选中
        self._build()
        self.root.after(80, self._poll)
        self._set_status("就绪。建议先「一键排查」（全程只读）；修复/清账本前需完全退出 Steam 和 WE。")
        self._env_loop()

    # ---------- 界面构建 ----------

    def _build(self):
        root = self.root
        root.title("WE-Mender 3.0 — Wallpaper Engine 工坊账本修复工具")
        root.geometry("1145x892")
        root.minsize(880, 640)
        root.bind("<Configure>", self._rewrap_all, add="+")
        root.bind_all("<Button-1>", self._on_global_click, add="+")
        root.bind_all("<Control-c>", self._on_global_copy, add="+")
        root.bind_all("<Control-C>", self._on_global_copy, add="+")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        head = tk.Frame(root, bg=T["BG"])
        head.pack(fill="x", padx=22, pady=(18, 0))
        tk.Label(head, text="WE-Mender 3.0", bg=T["BG"], fg=T["INK"],
                 font=FONTS["ui20"]).pack(side="left")
        tk.Label(head, text="Wallpaper Engine 创意工坊 · 诊断 + 一键修复",
                 bg=T["BG"], fg=T["MUTE"], font=FONTS["ui12"]).pack(
                     side="left", padx=(12, 0), pady=(6, 0))
        # 右上角状态两块：网络（最右，独立底色）+ 进程（其左）
        self.env_net_lbl = tk.Label(head, text="社区网络 检测中…", bg=T["SOFT"],
                                    fg=T["MUTE"], font=FONTS["ui12"],
                                    highlightthickness=1,
                                    highlightbackground=T["LINE"],
                                    padx=8, pady=3)
        self.env_net_lbl.pack(side="right", pady=(6, 0))
        self.env_lbl = tk.Label(head, text="环境检测中……", bg=T["BG"],
                                fg=T["MUTE"], font=FONTS["ui12"])
        self.env_lbl.pack(side="right", padx=(0, 12), pady=(6, 0))

        bar = tk.Frame(root, bg=T["BG"])
        bar.pack(fill="x", padx=22, pady=(14, 10))
        # 诊断组
        self.btn_diag = PillButton(bar, text="一键排查", variant="primary",
                                   command=self._btn_diag)
        self.btn_diag.pack(side="left")
        self.btn_fix = PillButton(bar, text="自动修复", variant="primary",
                                  command=self._btn_fix)
        self.btn_fix.pack(side="left", padx=(10, 0))
        # 悬停说明：明确它只动账本记录（补/删），不碰文件与订阅
        self.btn_fix.bind(
            "<Enter>",
            lambda e: self._show_tip(
                self.btn_fix,
                "自动修复只动「账本记录」，不碰文件、不改订阅：\n"
                "· 漏登的补上 —— 文件已在硬盘、只是账本没记（可修）\n"
                "· 残留的删掉 —— 已不订阅、文件也没了（残尸态，最易诱发全量重下）\n"
                "可一次处理多个条目；执行前自动备份账本到 backups\\，"
                "需要时可以手工把 .bak 复制回去。\n"
                "需要 Steam 与 Wallpaper Engine 完全退出。"),
            add="+")
        self.btn_fix.bind("<Leave>", self._hide_btn_hint, add="+")
        self.btn_fix.bind("<Button-1>", self._hide_btn_hint, add="+")
        # 分隔线
        tk.Frame(bar, bg=T["LINE"], width=1, height=24).pack(
            side="left", padx=(16, 16), pady=(4, 0))
        # 工具组
        self.btn_refresh = PillButton(bar, text="🔄 刷新", variant="normal",
                                      command=self._btn_refresh)
        self.btn_refresh.pack(side="left")
        self.btn_copy = PillButton(bar, text="复制报告", variant="normal",
                                   command=self._btn_copy)
        self.btn_copy.pack(side="left", padx=(10, 0))
        self.btn_refresh.configure(state=tk.DISABLED)
        self.btn_fix.configure(state=tk.DISABLED)
        self.btn_copy.configure(state=tk.DISABLED)   # 还没有报告可复制

        panel = tk.Frame(root, bg=T["TABLE"], highlightthickness=1,
                         highlightbackground=T["LINE"])
        panel.pack(fill="both", expand=True, padx=22, pady=(0, 10))
        # 可滚动卡片容器（结果区：三账状态 + 差异卡片 + 问题卡片）
        # grid 布局：横滚条用 grid_remove()/grid() 切换——恢复时自动回到原位
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        self.vc = tk.Canvas(panel, bg=T["TABLE"], highlightthickness=0, bd=0,
                            takefocus=0)
        vsb = ttk.Scrollbar(panel, orient="vertical", command=self.vc.yview)
        self.vc.configure(yscrollcommand=vsb.set)
        self.hsb = ttk.Scrollbar(panel, orient="horizontal",
                                 command=self.vc.xview)
        self.vc.configure(xscrollcommand=self._on_xscroll)
        self._hsb_visible = False
        self.vc.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.hsb.grid_remove()   # 默认隐藏；内容超宽时 _on_xscroll 里 grid() 恢复
        self.vbody = tk.Frame(self.vc, bg=T["TABLE"])
        self._vwin = self.vc.create_window(0, 0, window=self.vbody, anchor="nw")
        self.vc.bind("<Configure>", self._sync_scroll)
        self.vc.bind_all("<MouseWheel>",
                         lambda e: self.vc.yview_scroll(int(-e.delta / 120), "units"))
        self.vc.bind_all("<Shift-MouseWheel>",
                         lambda e: self.vc.xview_scroll(int(-e.delta / 120), "units"))

        self.log_txt = tk.Text(root, bg=T["SOFT"], fg=T["MUTE"], font=FONTS["ui12"],
                               height=3, bd=0, padx=14, pady=8, wrap="word",
                               state=tk.DISABLED, highlightthickness=0)
        self.log_txt.pack(fill="x", padx=22)

        tk.Frame(root, bg=T["LINE"], height=1).pack(fill="x", padx=22, pady=(10, 0))
        self.status_lbl = tk.Label(root, text="", bg=T["BG"], fg=T["BODY"],
                                   font=FONTS["ui12"], anchor="w")
        self.status_lbl.pack(fill="x", padx=22, pady=(8, 12))

        self._show_placeholder()

    # ---------- 工具 ----------

    # ---------- 结果视图（可交互卡片：三账状态 / 差异 / 问题） ----------

    def _clear_view(self):
        for w in self.vbody.winfo_children():
            w.destroy()
        self._details.clear()
        self._toggles.clear()
        self._expanded.clear()
        self._hide_btn_hint()   # M2：按钮销毁后 Leave 不触发，气泡必须显式清除
        self._action_btns = []  # 动作按钮元数据（网络/凭据灰控 + 悬停提示）
        self._title_labels = {}
        self._wrap_labels = []
        self._copy_entries = []
        self.vc.yview_moveto(0)

    def _show_placeholder(self):
        self._clear_view()
        tk.Label(self.vbody,
                 text="（尚未排查。点击左上角「一键排查」开始；全程只读，不改动任何文件。）",
                 bg=T["TABLE"], fg=T["MUTE"], font=FONTS["ui13"],
                 anchor="w").pack(fill="x", padx=6, pady=14)

    def _set_view(self, P, D, issues, out):
        """结果区重绘：板块① 三账状态（计数+差异卡）｜板块② 其他问题"""
        # 刷新会整块重建，但用户正在看的东西不该丢：记下展开了哪些卡、滚到哪。
        # 哨兵每 8 秒就可能因三账变化触发一次刷新（下载期间几乎必然），
        # 不还原的话正在看的详情会被收起、视野被弹回顶部，界面等于不可用。
        keep_open = {k for k, v in self._expanded.items() if v}
        try:
            keep_y = self.vc.yview()[0]
        except Exception:
            keep_y = 0.0
        model = build_overview_model(P, D, issues)
        self._clear_view()
        if not keep_open:
            self.vc.yview_moveto(0)   # 重建后回到顶部，避免旧滚动位置与新高度错位

        def _section(title):
            """板块标题：橙色竖线 + 文字"""
            head = tk.Frame(self.vbody, bg=T["TABLE"])
            head.pack(fill="x", padx=4, pady=(14, 4))
            tk.Label(head, text="▍", bg=T["TABLE"], fg=T["ACCENT"],
                     font=FONTS["ui16"]).pack(side="left")
            tk.Label(head, text=title, bg=T["TABLE"], fg=T["INK"],
                     font=FONTS["ui14"]).pack(side="left", padx=(2, 0))

        # ── 轻量刷新模式提示条（其他问题/大小数据以完整排查为准）
        if getattr(self, "_last_was_refresh", False):
            tip = tk.Frame(self.vbody, bg=T["SOFT"], highlightthickness=1,
                           highlightbackground=T["LINE"])
            tip.pack(fill="x", padx=4, pady=(0, 2))
            tk.Label(tip, text="⚡ 当前为轻量刷新视图：仅三账与差异。"
                               "其他问题、大小校验等以「一键排查」完整结果为准。",
                     bg=T["SOFT"], fg=T["MUTE"], font=FONTS["ui12"],
                     anchor="w", justify="left", wraplength=820).pack(
                         fill="x", padx=10, pady=6)

        # ════ 板块① 三账状态 ════
        _section("三账状态")
        top = tk.Frame(self.vbody, bg=T["TABLE"])
        top.pack(fill="x", padx=4, pady=(2, 2))
        row = tk.Frame(top, bg=T["TABLE"])
        row.pack(fill="x")
        for label, val, unit in model["counts"]:
            grp = tk.Frame(row, bg=T["TABLE"])
            grp.pack(side="left", anchor="w", padx=(2, 34))
            tk.Label(grp, text=label, bg=T["TABLE"], fg=T["MUTE"],
                     font=FONTS["ui12"]).pack(anchor="w")
            tk.Label(grp, text="%s %s" % (val, unit), bg=T["TABLE"], fg=T["INK"],
                     font=FONTS["ui20"]).pack(anchor="w")
        tag, stext = model["status"]
        color = {"ok": "#82c882", "warn": T["ACCENT"],
                 "sev": "#e0554a", "sub": T["MUTE"]}.get(tag, T["BODY"])
        tk.Label(top, text=stext, bg=T["TABLE"], fg=color, font=FONTS["ui13"],
                 anchor="w", justify="left", wraplength=820).pack(fill="x", pady=(8, 2))

        # 差异卡片（可展开）
        if model["diffs"]:
            tk.Label(self.vbody, text="差异与对策（点「详细」展开具体条目与操作按钮）",
                     bg=T["TABLE"], fg=T["MUTE"], font=FONTS["ui12"]).pack(
                         anchor="w", padx=4, pady=(10, 2))
            for d in model["diffs"]:
                self._make_diff_card(d)
        else:
            tk.Label(self.vbody, text="三账一致，无差异条目 ✓",
                     bg=T["TABLE"], fg=T["MUTE"], font=FONTS["ui12"]).pack(
                         anchor="w", padx=4, pady=(6, 2))

        # ════ 板块② 其他问题 ════
        _section("其他问题")
        if model["others"]:
            for idx, it in enumerate(model["others"]):
                self._make_issue_card(it, idx)
        elif getattr(self, "_last_was_refresh", False):
            tk.Label(self.vbody,
                     text="轻量刷新仅包含三账与差异检查；内容健康 / 大小校验 / "
                          "日志异常等检查请点「一键排查」查看。",
                     bg=T["CARD"], fg=T["MUTE"], font=FONTS["ui12"],
                     anchor="w", justify="left", wraplength=840).pack(
                         fill="x", padx=4, pady=4)
        else:
            tk.Label(self.vbody, text="没有其他问题 ✓",
                     bg=T["CARD"], fg=T["MUTE"], font=FONTS["ui12"],
                     anchor="w").pack(fill="x", padx=4, pady=4)

        # ── 脚注
        foot = tk.Frame(self.vbody, bg=T["TABLE"])
        foot.pack(fill="x", padx=4, pady=(12, 12))
        if out:
            tk.Label(foot, text="完整报告已保存：%s" % out,
                     bg=T["TABLE"], fg=T["FAINT"], font=FONTS["ui12"],
                     anchor="w", justify="left", wraplength=820).pack(fill="x")
        elif getattr(self, "_last_was_refresh", False):
            tk.Label(foot, text="轻量刷新不更新报告文件（完整报告以最近一次「一键排查」为准）",
                     bg=T["TABLE"], fg=T["FAINT"], font=FONTS["ui12"],
                     anchor="w").pack(fill="x")
        else:
            tk.Label(foot, text="⚠ 报告文件写入失败；「复制报告」仍可复制全文",
                     bg=T["TABLE"], fg=T["FAINT"], font=FONTS["ui12"],
                     anchor="w").pack(fill="x")
        tk.Label(foot,
                 text="「复制报告」= 完整报告全文（含路径与数据），方便粘贴给 AI 工具",
                 bg=T["TABLE"], fg=T["FAINT"], font=FONTS["ui12"],
                 anchor="w").pack(fill="x", pady=(2, 0))

        self._last_wrap_w = 0      # 强制按当前窗口宽重算一遍换行宽度
        self._rewrap_all()
        self.vbody.update_idletasks()
        self._sync_scroll()
        self._restore_view_state(keep_open, keep_y)

    def _restore_view_state(self, keep_open, keep_y):
        """重建后还原展开的卡片与滚动位置（卡片 key 稳定，可精确还原）"""
        for k in keep_open:
            if k in self._details and not self._expanded.get(k):
                try:
                    self._toggle_detail(k)
                except Exception:
                    pass
        if keep_y > 0:
            try:
                self.vbody.update_idletasks()
                self._sync_scroll()
                self.vc.yview_moveto(keep_y)
            except Exception:
                pass

    def _on_xscroll(self, first, last):
        """xscrollcommand 回调：同步滑块比例 + 按内容是否超宽显隐横滚条
        （grid_remove 隐藏，grid() 无参恢复到原 grid 位）"""
        try:
            self.hsb.set(first, last)
            show = float(last) - float(first) < 0.999
            if show and not self._hsb_visible:
                self.hsb.grid()
                self._hsb_visible = True
            elif not show and self._hsb_visible:
                self.hsb.grid_remove()
                self._hsb_visible = False
        except Exception:
            pass

    def _on_global_click(self, event):
        """点空白/别处时，取消其它输入框的选中态（Tk 默认会一直留着）"""
        w = event.widget
        for en in list(self._copy_entries):
            try:
                if en is not w and en.winfo_exists():
                    en.clear_selection()
            except tk.TclError:
                pass

    def _copied(self, txt):
        """复制成功后的统一反馈（状态栏一句话，避免"复制了没反应"）"""
        s = (txt or "").replace("\n", " ")
        self._set_status("✓ 已复制：%s%s" % (s[:40], "…" if len(s) > 40 else ""))

    def _on_global_copy(self, _event=None):
        """Ctrl+C：这些文字框不取焦点（否则 Windows 会画焦点框），
        所以按键到不了控件上；这里从应用层兜住——谁正有选中就复制谁。"""
        for en in list(self._copy_entries):
            try:
                if en.selection_present():
                    en._copy()
                    return "break"
            except tk.TclError:
                continue
        return "break"

    def _rewrap_all(self, _event=None):
        """窗口宽度变化时统一重算说明文字的换行宽度。

        不能挂在卡片自身的 <Configure> 上：卡片宽度是被内容撑开的，内容不缩
        卡片就不缩，Configure 不再触发 → 自锁，窗口缩小时横向滚动条收不掉。
        所以一律以窗口宽为准、全局重排。
        """
        try:
            w = self.root.winfo_width()
        except tk.TclError:
            return
        if w <= 1 or w == self._last_wrap_w:
            return
        self._last_wrap_w = w
        for lab, pad in list(self._wrap_labels):
            try:
                lab.configure(wraplength=max(w - pad, 220))
            except tk.TclError:
                pass

    def _sync_scroll(self, _event=None):
        """滚动同步（几何化，主人定的逻辑）：
        scrollregion 精确 = max(视口, 内容需求尺寸)——内容没超出板块就不产生
        滚动空间，超出才滚且精确等于内容大小；横向同理，超宽时显示横向滚动条。
        内嵌区宽度取 max(视口宽, 内容需求宽)：窄窗口时内容完整展开、横向查看。

        注意：先 width=0 解除上一次的强制宽度，否则 winfo_reqwidth 会返回
        上次设进去的值（自锁），窗口从大改小时横向滚动条永远收不掉。"""
        try:
            self.vc.itemconfigure(self._vwin, width=0)
            self.vbody.update_idletasks()
            cw = max(1, self.vc.winfo_width())
            ch = max(1, self.vc.winfo_height())
            rw = max(cw, self.vbody.winfo_reqwidth())
            rh = self.vbody.winfo_reqheight()
            self.vc.itemconfigure(self._vwin, width=rw)
            self.vc.configure(scrollregion=(0, 0, rw, max(ch, rh)))
            if rh <= ch:
                self.vc.yview_moveto(0)
        except tk.TclError:
            pass

    def _make_diff_card(self, d):
        card = tk.Frame(self.vbody, bg=T["CARD"], highlightthickness=1,
                        highlightbackground=T["LINE"])
        card.pack(fill="x", padx=4, pady=4)
        head = tk.Frame(card, bg=T["CARD"])
        head.pack(fill="x", padx=12, pady=(10, 2))
        det = tk.Frame(card, bg=T["CARD"])
        self._details[d["key"]] = (det, lambda parent, dd=d: self._build_detail(parent, dd))
        if d["items"]:
            btn = PillButton(head, text="详细 ▾", variant="normal", height=26,
                             command=lambda k=d["key"]: self._toggle_detail(k))
            btn.pack(side="right", padx=(8, 0))
            self._toggles[d["key"]] = btn
        # 标题左侧：数量 + 该组的真实三账状态（缺的那项橙色）+ 一句人话说明
        tk.Label(head, text=d.get("head", ""), bg=T["CARD"], fg=T["MUTE"],
                 font=FONTS["ui12"]).pack(side="left")
        if d.get("st"):
            tk.Label(head, text=" ", bg=T["CARD"], font=FONTS["ui12"]).pack(side="left")
            self._state_phrase(head, d["st"])
        label = STATE_LABEL.get(d.get("st"))
        if label:
            tk.Label(head, text=" —— " + label, bg=T["CARD"], fg=T["BODY"],
                     font=FONTS["ui12"]).pack(side="left")
        glab = tk.Label(card, text="→ " + d["guide"], bg=T["CARD"], fg=T["MUTE"],
                        font=FONTS["ui12"], anchor="w", justify="left",
                        wraplength=720)
        glab.pack(fill="x", padx=12, pady=(0, 10))
        self._wrap_labels.append((glab, 200))
        glab.configure(wraplength=max(self.root.winfo_width() - 200, 220))

    def _state_phrase(self, parent, st):
        """把八态写成「有订阅、无记录、有文件」，缺的那一项用橙色标出来"""
        bits = STATE_BITS.get(st)
        for idx, nm in enumerate(("订阅", "记录", "文件")):
            if bits is None:
                txt, fg = "?" + nm, T["MUTE"]
            else:
                txt = ("有" if bits[idx] else "无") + nm
                fg = T["BODY"] if bits[idx] else T["ACCENT"]
            if idx:
                tk.Label(parent, text="、", bg=T["CARD"], fg=T["MUTE"],
                         font=FONTS["ui12"]).pack(side="left")
            tk.Label(parent, text=txt, bg=T["CARD"], fg=fg,
                     font=FONTS["ui12"]).pack(side="left")

    def _make_issue_card(self, it, idx=0):
        card = tk.Frame(self.vbody, bg=T["CARD"], highlightthickness=1,
                        highlightbackground=T["LINE"])
        card.pack(fill="x", padx=4, pady=3)
        head = tk.Frame(card, bg=T["CARD"])
        head.pack(fill="x", padx=12, pady=(8, 2))
        lvl_color = {"严重": "#e0554a", "提醒": T["ACCENT"],
                     "提示": T["MUTE"]}.get(it["level"], T["BODY"])
        tk.Label(head, text="[%s]" % it["level"], bg=T["CARD"], fg=lvl_color,
                 font=FONTS["ui12"]).pack(side="left")
        tlab = tk.Label(head, text=it["title"], bg=T["CARD"], fg=T["BODY"],
                        font=FONTS["ui13"], anchor="w", justify="left",
                        wraplength=700)
        tlab.pack(side="left", fill="x", expand=True, padx=(8, 0))
        # key 必须跨刷新稳定：原来用 id(it)，每次排查 issues 都是新对象 →
        # key 必变 → 用户展开的问题卡每 8 秒就被收起一次。
        key = "issue:%d:%s" % (idx, it["title"])
        det = tk.Frame(card, bg=T["CARD"])
        self._details[key] = (det, lambda parent, ii=it: self._build_issue_detail(parent, ii))
        btn = PillButton(head, text="详细 ▾", variant="normal", height=26,
                         command=lambda k=key: self._toggle_detail(k))
        btn.pack(side="right", padx=(8, 0))
        self._toggles[key] = btn
        if it.get("symptom"):
            slab = tk.Label(card, text=it["symptom"], bg=T["CARD"], fg=T["MUTE"],
                            font=FONTS["ui12"], anchor="w", justify="left",
                            wraplength=700)
            slab.pack(fill="x", padx=12, pady=(0, 8))

            def _wrap2(e, l1=tlab, l2=slab):
                w = max(self.root.winfo_width() - 190, 220)
                l1.configure(wraplength=w)
                l2.configure(wraplength=w)
            self._wrap_labels.append((tlab, 190))
            self._wrap_labels.append((slab, 190))
            _wrap2(None)

    def _toggle_detail(self, key):
        det, build_fn = self._details[key]
        btn = self._toggles.get(key)
        opened = self._expanded.get(key, False)
        if opened:
            det.pack_forget()
        else:
            if not det.winfo_children():
                build_fn(det)
            det.pack(fill="x", padx=12, pady=(0, 10))
        if btn:
            btn.configure(text=("详细 ▾" if opened else "收起 ▴"))
        self._expanded[key] = not opened
        self.vbody.update_idletasks()
        self._sync_scroll()

    def _build_detail(self, parent, d):
        """差异卡片的展开内容：逐条列出 ID / 名称 / 八态标注 + 操作按钮（懒构建）"""
        items = d["items"]
        tk.Label(parent, text="共 %d 项：" % len(items), bg=T["CARD"], fg=T["MUTE"],
                 font=FONTS["ui12"], anchor="w").pack(fill="x", pady=(2, 4))
        content = self.P.get("content") if self.P else None
        # 动作守卫的数据源：构建详情时重查八态（只读，两个小文件）
        cls = {}
        try:
            if self.P:
                cls = actions.classify_batch(self.P, items)
        except Exception:
            cls = {}
        is_orphan = d["kind"] == "orphan"          # 类型 5/7
        need_titles = d.get("st") in (2, 4, 6)     # 缺文件的三类：名称只能联网取
        # 三账操作矩阵：类型 → [(按钮文案, 动作键)]
        ACTIONS_BY_TYPE = {
            # 类型 2 只缺文件、订阅还在：正确的下一步是「让 Steam 重新下载」，
            # 不是退订。原来这里给的是「打开订阅列表」，日志还教用户点 Unsubscribe，
            # 等于把一个还能救的条目主动推成残尸态。
            2: [("打开物品页", "open_item")],
            3: [("打开文件夹", "open_folder")],
            4: [("打开订阅列表", "open_sub")],
            5: [("重新订阅", "resub"), ("清账本记录", "clear"),
                ("转本地壁纸", "convert"), ("删除到回收站", "recycle")],
            6: [("重新订阅", "resub"), ("清账本记录", "clear")],
            7: [("重新订阅", "resub"), ("转本地壁纸", "convert"),
                ("删除到回收站", "recycle"), ("打开文件夹", "open_folder")],
        }
        for iid in items:
            st = cls.get(iid) or (None, "")
            st_no = st[0]
            acts = ACTIONS_BY_TYPE.get(st_no) if st_no else None
            has_folder = content and os.path.isdir(os.path.join(content, iid))
            if acts is None:
                # 状态未知（订阅清单不可读等）：只展示，不给破坏性按钮
                acts = [("打开文件夹", "open_folder")] if has_folder else []
            if acts and has_folder:
                head_text = actions.read_local_title(os.path.join(content, iid)) \
                    or "（无标题）"
                loc_text = os.path.join(content, iid)
            elif acts:
                head_text = "（未下载到本地，名称获取中…）"
                loc_text = ("https://steamcommunity.com/sharedfiles/"
                            "filedetails/?id=%s" % iid)
            else:
                head_text = "（无标题）"
                loc_text = os.path.join(content, iid) if content else ""

            # 单条壁纸：三栏两行
            #   左：壁纸名 / ID · 位置（三处各自可复制）
            #   中：三账徽标 / 一句状态说明（与徽标左对齐）
            #   右：按钮 / 悬停时在下方固定位置显示该按钮的作用
            item = tk.Frame(parent, bg=T["CARD"])
            item.pack(fill="x", pady=(4, 0))
            item.grid_columnconfigure(0, weight=1)

            # 右栏第 2 行：按钮说明（固定宽度，避免文字把列撑变形）
            desc_box = tk.Frame(item, bg=T["CARD"], width=200, height=16)
            desc_box.grid(row=1, column=2, sticky="w", pady=(2, 0))
            desc_box.grid_propagate(False)
            desc_lbl = tk.Label(desc_box, text="", bg=T["CARD"], fg=T["FAINT"],
                                font=FONTS["ui11"], anchor="w")
            desc_lbl.pack(fill="both", expand=True)

            # 右栏第 1 行：按钮（高度与左侧三账徽标对齐）
            btn_row = tk.Frame(item, bg=T["CARD"])
            btn_row.grid(row=0, column=2, sticky="e")

            def _btn(text, cmd, need_net=False, act_key=None):
                b = PillButton(btn_row, text=text, variant="normal", height=20,
                               command=cmd)
                meta = {"b": b, "need_net": need_net}
                if need_net:
                    b.bind("<Enter>",
                           lambda e, m=meta: self._show_btn_hint(m, e), add="+")
                    b.bind("<Leave>", self._hide_btn_hint, add="+")
                    b.bind("<Button-1>", self._hide_btn_hint, add="+")
                d = BTN_DESC.get(act_key)
                if d:
                    b.bind("<Enter>", lambda e, t=d: desc_lbl.configure(text=t), add="+")
                    b.bind("<Leave>", lambda e: desc_lbl.configure(text=""), add="+")
                    b.bind("<Button-1>", lambda e: desc_lbl.configure(text=""), add="+")
                self._action_btns.append(meta)
                self._apply_btn_state(meta)
                b.pack(side="left", padx=(6, 0))

            for text, act in acts:
                if act == "open_sub":
                    _btn(text, lambda i=iid: self._act_open_sub(i), True, act)
                elif act == "open_item":
                    _btn(text, lambda i=iid: self._act_open_item(i), True, act)
                elif act == "resub":
                    _btn(text, lambda i=iid: self._act_resub(i), True, act)
                elif act == "clear":
                    _btn(text, lambda i=iid: self._act_clear(i), False, act)
                elif act == "convert":
                    _btn(text, lambda i=iid, s=st_no: self._act_convert(i, s),
                         False, act)
                elif act == "recycle":
                    _btn(text, lambda i=iid: self._act_recycle(i), False, act)
                elif act == "open_folder":
                    _btn(text, lambda i=iid, c=content:
                         self._open_path(os.path.join(c, i)), False, act)

            # 中栏：徽标在上、说明在下，同一列左对齐
            meta_col = tk.Frame(item, bg=T["CARD"])
            meta_col.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            self._build_state_badges(meta_col, st_no)

            # 左栏第 1 行：壁纸名（可选中复制；过长时横向裁切，选中可滚动查看）
            lab = CopyEntry(item, head_text, bg=T["CARD"], fg=T["BODY"],
                            font=FONTS["ui13"], width=1)
            lab.grid(row=0, column=0, sticky="ew", padx=(0, 12))
            lab.on_copy = self._copied
            self._copy_entries.append(lab)

            # 左栏第 2 行：ID · 位置（两个独立可复制块，中间的点保留）
            prow = tk.Frame(item, bg=T["CARD"])
            prow.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(1, 0))
            id_e = CopyEntry(prow, iid, bg=T["CARD"], fg=T["FAINT"],
                             font=FONTS["latin12"], width=max(len(iid), 6))
            id_e.pack(side="left")
            id_e.on_copy = self._copied
            self._copy_entries.append(id_e)
            tk.Label(prow, text=" · ", bg=T["CARD"], fg=T["FAINT"],
                     font=FONTS["latin12"]).pack(side="left")
            loc_e = CopyEntry(prow, loc_text, bg=T["CARD"], fg=T["FAINT"],
                              font=FONTS["latin12"], width=1)
            loc_e.pack(side="left", fill="x", expand=True)
            loc_e.on_copy = self._copied
            self._copy_entries.append(loc_e)
            if not (acts and has_folder):
                self._title_labels.setdefault(d["key"], {})[iid] = lab
        if need_titles:
            self._fetch_titles(d["key"], list(items))

    def _build_state_badges(self, parent, st_no):
        """三账徽标块：上排 订/记/盘 各带 ✓✗，下排一句该怎么办（整体右对齐）"""
        bits = STATE_BITS.get(st_no)
        row = tk.Frame(parent, bg=T["CARD"])
        row.pack(anchor="e")
        for idx, name in enumerate(("订", "记", "盘")):
            if bits is None:
                txt, fg = name + "?", T["MUTE"]
            elif bits[idx]:
                txt, fg = name + "✓", _OK_GREEN
            else:
                txt, fg = name + "✗", T["ACCENT"]
            tk.Label(row, text=txt, bg=T["SOFT"], fg=fg, font=FONTS["ui11"],
                     padx=4, pady=0, highlightthickness=1,
                     highlightbackground=T["LINE"]).pack(side="left", padx=(0, 3))
        hint = STATE_HINT.get(st_no)
        if hint:
            tk.Label(parent, text=hint, bg=T["CARD"], fg=T["MUTE"],
                     font=FONTS["ui11"]).pack(anchor="w", pady=(2, 0))

    def _build_issue_detail(self, parent, it):
        for name, val in (("原因", it.get("cause")), ("影响", it.get("impact")),
                          ("该找谁", it.get("who")), ("风险", it.get("risk")),
                          ("详情", it.get("detail"))):
            if not val:
                continue
            row = tk.Frame(parent, bg=T["CARD"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name + "：", bg=T["CARD"], fg=T["MUTE"],
                     font=FONTS["ui12"]).pack(side="left", anchor="n")
            vlab = tk.Label(row, text=val, bg=T["CARD"], fg=T["BODY"],
                            font=FONTS["ui12"], anchor="w", justify="left",
                            wraplength=max(self.root.winfo_width() - 200, 220))
            vlab.pack(side="left", fill="x", expand=True)
            # 与卡内其他文字一样参与窗口宽度重排。
            # 预留必须与同卡其它标签一致（190）：90 会让换行宽比可用宽度大 100px，
            # 880 最小宽度下一展开问题卡就撑出横向滚动条、把右侧按钮裁掉。
            self._wrap_labels.append((vlab, 190))

    def _open_path(self, path):
        try:
            os.startfile(path)   # noqa: S606（Windows 资源管理器打开）
        except Exception as e:
            self._log("✗ 打开失败：%s" % e)

    # ---------- 动作（八态 2/4/5/6/7）：全部经守卫重查 + 后台线程 + 复检 ----------

    def _start_action(self, title, fn):
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        self._set_busy(True)
        self._set_status(title)
        self._log("▶ " + title)
        threading.Thread(target=self._work_action, args=(fn,), daemon=True).start()

    def _work_action(self, fn):
        try:
            ok, msg, followup = fn(lambda s: self.q.put(("log", s)))
            self.q.put(("action_done", (ok, msg, followup)))
        except Exception as e:
            self.q.put(("fatal", "操作出错：%s" % e))

    def _expected_subs(self):
        """最近一次排查时的订阅总数（None=未知，动作守卫将跳过数量比对）。

        口径必须与引擎一致：引擎用 read_subs_map 的条数做比对，**含本地已禁用**的
        订阅项；而 D["subs"] 只装未禁用的。用过 len(D["subs"]) 的后果是：用户只要
        在 Steam 里禁用过任意一个订阅，转本地/回收站等动作就会被永久拒绝，提示还
        让用户"重新排查"——但重查得到的还是同一个偏小的数字，永远修不好。"""
        if not self.D or self.D.get("subs_state") != "ok":
            return None
        return self.D.get("subs_total")

    def _act_open_item(self, iid):
        """类型 2（已订阅、有记录、缺文件）：打开物品页，引导重新下载。
        这里**不能**引导退订 —— 退订会把一条只缺文件、还能救的条目变成残尸态。"""
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        if self._net_ok is False:
            self._log("✗ 社区网络不可达（需开启加速器），无法打开物品页。")
            return
        ok, msg = actions.steam_open(actions.item_page_url(iid))
        if not ok:
            self._log("✗ 打开物品页失败：%s" % msg)
            return
        self._log("✓ %s（%s）：\n"
                  "  · 页面里点「订阅/下载」可让 Steam 重新拉取文件；\n"
                  "  · 若页面提示已下架，就只能删掉这条记录、或转本地保存。"
                  % (msg, iid))

    def _act_open_sub(self, iid):
        """类型 4：打开「我的订阅」列表页，带页码定位 + 页内位置 + 锚点尝试"""
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        if self._net_ok is False:
            self._log("✗ 社区网络不可达（需开启加速器），无法打开订阅列表页。")
            return
        url, page, pos, rank = actions.subscription_page_url(self.P or {}, iid)
        title = ""
        try:
            title = _load_title_cache().get(iid) or ""
        except Exception:
            title = ""
        ok, msg = actions.steam_open(url)
        if ok:
            where = ("按订阅日期排序时，目标应在该页从上往下第 %s 个"
                     % pos) if pos else "目标位置未知（订阅清单不可读）"
            tpart = title or iid
            self._log("✓ 已打开订阅列表第 %d 页（目标《%s》订阅名次第 %s）。%s。\n"
                      "  · 若页面排序不是「订阅日期」，先切到该排序再找；\n"
                      "  · URL 已带锚点尝试自动滚动，没滚到就 Ctrl+F 搜索标题；\n"
                      "  · 找到后悬停「Subscribed」→ Unsubscribe，"
                      "完成后点「一键排查」复查。" % (page, tpart, rank, where))
        else:
            self._log("✗ " + msg)


    def _act_convert(self, iid, st_no=None):
        """类型 5/7：复制为本地壁纸（剥离工坊字段 + .bak），成功后询问删原件"""
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        if not self.P:
            self._log("· 请先「一键排查」获取路径信息。")
            return
        content = self.P.get("content") or ""
        if not content:
            self._log("✗ 工坊内容目录不可用，无法转换。")
            return
        if st_no is None:
            try:
                st_no = (actions.classify_batch(self.P, [iid]).get(iid) or (None,))[0]
            except Exception:
                st_no = None
        folder = os.path.join(content, iid)
        title = actions.read_local_title(folder) or iid
        extra = ("· 该条目账本里有记录，成功后会一并清理（需先退出 Steam 与 WE）\n"
                 if st_no == 5 else "")
        if not messagebox.askyesno(
                "转本地壁纸",
                "把该壁纸复制为本地壁纸（原件暂保留）：\n\n%s\n\n"
                "· 副本位置：Wallpaper Engine\\projects\\myprojects\\\n"
                "· 将剥离工坊关联字段（原字段自动备份为 .bak）\n"
                "%s"
                "· 成功后可再决定是否删除原文件夹\n\n确认执行？" % (title, extra)):
            return

        def fn(ui):
            ok, msg = actions.convert_to_local(self.P, iid, True, ui,
                                            expected_sub_count=self._expected_subs())
            return ok, msg, (("recycle_ask", iid) if ok else None)

        self._start_action("[转本地] %s" % iid, fn)

    def _act_recycle(self, iid):
        """类型 5/7：原文件夹移入系统回收站（可还原）"""
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        if not self.P:
            self._log("· 请先「一键排查」获取路径信息。")
            return
        content = self.P.get("content") or ""
        if not content:
            self._log("✗ 工坊内容目录不可用，无法删除。")
            return
        folder = os.path.join(content, iid)
        if not messagebox.askyesno(
                "删除到回收站",
                "把以下文件夹移入系统回收站（可在回收站还原，不是彻底删除）：\n\n"
                "%s\n\n确认执行？（若账本有记录会先清账本再删除）" % folder):
            return

        def fn(ui):
            ok, msg, _done, _failed = actions.recycle_delete(self.P, [iid], ui=ui,
                                                   expected_sub_count=self._expected_subs())
            return ok, msg, None

        self._start_action("[回收站] %s" % iid, fn)

    def _act_clear(self, iid):
        """类型 6（含 5 的单独入口）：删除账本记录（需退出 Steam/WE，自动备份）"""
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        if not self.P:
            self._log("· 请先「一键排查」获取路径信息。")
            return
        if not messagebox.askyesno(
                "清账本记录",
                "将从 Steam 账本中删除该条目的记录：\n\n%s\n\n"
                "⚠ 前提：请先完全退出 Steam 和 Wallpaper Engine！\n"
                "（工具会自动检查；操作前自动备份，校验不过则不写入，"
                "必要时可用备份手工还原）\n\n确认执行？" % iid):
            return
        # 订阅清单为空 + 账本还有记录，是"可能被写坏/账户切错"的高危形态。
        # 引擎默认拒绝；这里给用户一次显式确认的机会（全部退订后清理账本是合法需求）。
        allow_empty = False
        if self.D and self.D.get("subs_state") == "empty":
            allow_empty = messagebox.askyesno(
                "订阅清单是空的",
                "当前订阅清单里一条记录都没有，但账本里仍留有安装记录。\n\n"
                "如果这不是你刚刚主动「全部退订」的结果，可能是登录账户不对、"
                "或订阅清单被写坏/截断 —— 这种情况请选「否」，先确认 Steam 账户。\n\n"
                "确认这些壁纸都已退订，继续清理账本吗？")
            if not allow_empty:
                self._log("· 已取消：订阅清单为空，未清理账本。")
                return

        def fn(ui):
            ok, msg = actions.ledger_clear(self.P, [iid], ui=ui,
                                           allow_empty_subs=allow_empty)
            return ok, msg, None

        self._start_action("[账本] %s" % iid, fn)

    # ---------- 设置页（方案 3：Cookie 凭据，内存暂存，关窗即清） ----------

    def _act_resub(self, iid):
        if self.busy:
            self._log("· 已有操作在进行中，请稍候。")
            return
        """订·重新订阅：跳物品页让用户重新订阅（已确认下架的会先拦截提示）"""
        if self._net_ok is False:
            self._log("✗ 社区网络不可达（需开启加速器），无法打开物品页。")
            return
        cache = _load_title_cache()
        if iid in cache and cache[iid] is None:
            if not messagebox.askyesno(
                    "该物品已下架",
                    "根据之前联网查询，该物品在创意工坊已不可见（被下架/删除），\n"
                    "无法重新订阅。\n\n仍要打开物品页查看吗？"):
                return
        url, _page, _pos, _rank = actions.subscription_page_url(self.P or {}, iid)
        item_url = actions.item_page_url(iid)
        ok, msg = actions.steam_open(item_url)
        if ok:
            self._log("✓ 已打开物品页：请在页面上点「订阅」恢复它"
                      "（若页面是错误页，说明已下架，无法重新订阅）。")
        else:
            self._log("✗ " + msg)

    def _fetch_titles(self, key, ids):
        """缺失/未下载条目的名称：先查本地缓存，其余联网获取（后台线程）"""
        # 已知社区网络不可达时不再发请求：否则每张卡各起一个线程去撞 8 秒超时，
        # 与哨兵的自动刷新叠加后，界面会一阵阵发卡。
        if self._net_ok is False:
            return
        cache = _load_title_cache()
        pending = []
        for iid in ids:
            t = cache.get(iid)
            lab = (self._title_labels.get(key) or {}).get(iid)
            if t and lab:
                _set_item_title(lab, t)
            elif not t:
                pending.append(iid)
        if not pending:
            return

        def work():
            try:
                got = fetch_published_titles(pending)
                mapping = {iid: got.get(iid) for iid in pending}
                upd = {k: v for k, v in mapping.items() if v}
                if upd:
                    c2 = _load_title_cache()
                    c2.update(upd)
                    _save_title_cache(c2)
                self.q.put(("titles", (key, mapping, pending)))
            except Exception:
                self.q.put(("titles", (key, None, pending)))
        threading.Thread(target=work, daemon=True).start()

    def _apply_titles(self, payload):
        try:
            key, mapping, pending = payload
        except Exception:
            key, mapping, pending = payload[0], payload[1], []
        labels = self._title_labels.get(key) or {}
        if mapping is None:
            # 只对**这次真的去联网请求过**的条目写失败文案：
            # 缓存命中、已经填好名字的那些不该被覆盖成"获取失败"。
            for iid in (pending or []):
                lab = labels.get(iid)
                if lab:
                    _set_item_title(lab, "（名称获取失败，可点右侧按钮在浏览器中查看）")
            return
        for iid, t in mapping.items():
            lab = labels.get(iid)
            if not lab:
                continue
            if t:
                _set_item_title(lab, t)
            else:
                _set_item_title(lab, "（Steam 上已不可见：可能已被作者下架）")

    def _log(self, s):
        self.log_txt.configure(state=tk.NORMAL)
        self.log_txt.insert("end", s + "\n")
        if int(self.log_txt.index("end-1c").split(".")[0]) > 200:
            self.log_txt.delete("1.0", "60.0")
        self.log_txt.see("end")
        self.log_txt.configure(state=tk.DISABLED)

    def _set_status(self, s):
        self.status_lbl.configure(text=s)

    def _set_busy(self, busy):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.btn_diag.configure(state=state)
        # 复制只读写剪贴板，不与后台任务冲突：忙时也保持可用。
        # （全库扫大小可能持续几分钟，这期间用户最想做的恰恰是复制上一次的报告。）
        self.btn_copy.configure(state=tk.NORMAL if self.report_text else tk.DISABLED)
        if hasattr(self, "btn_refresh"):
            self.btn_refresh.configure(state=state)
        if busy:
            self.btn_fix.configure(state=tk.DISABLED)
        elif self.D and self.D.get("repairable"):
            self.btn_fix.configure(state=tk.NORMAL)

    def _on_close(self):
        """关闭窗口。后台线程是 daemon，会被强杀，可能留下未完成的临时文件，
        所以忙时先给一句确认——但**必须留一条不用杀进程的出口**：
        以前这里是"只弹提示并 return"，一旦 busy 因异常卡住，窗口就再也关不掉。"""
        if self.busy:
            if not messagebox.askyesno(
                    "操作仍在进行",
                    "后台操作可能还没有完成，现在退出会中断它（已写入的账本有备份，"
                    "不会损坏，但本次操作可能半途而废）。\n\n仍要退出吗？"):
                return
        self.root.destroy()

    # ---------- 动作按钮灰控与悬停提示（网络/凭据） ----------

    def _btn_reasons(self, meta):
        """返回该按钮当前被禁用的原因列表（空 = 可用）"""
        reasons = []
        if meta.get("need_net") and self._net_ok is False:
            reasons.append("需要 Steam 社区网络可达：\n"
                           "请开启 Watt Toolkit/Steam++ 等加速器的「Steam 社区」加速")
        return reasons

    def _apply_btn_state(self, meta):
        try:
            disabled = bool(self._btn_reasons(meta))
            meta["b"].configure(state=tk.DISABLED if disabled else tk.NORMAL)
            # 禁用态用普通光标（可点态由 PillButton hover 设为手型）
            meta["b"].configure(cursor="arrow" if disabled else "hand2")
        except Exception:
            pass

    def _refresh_action_buttons(self):
        """网络/凭据状态变化后，统一重算所有动作按钮的可用性"""
        for meta in list(self._action_btns):
            self._apply_btn_state(meta)
        # 轻微2：禁用→可用翻转时顺手收起可能过期的悬停气泡
        self._hide_btn_hint()

    def _show_tip(self, widget, text, _event=None):
        """通用悬停气泡（自绘按钮没有原生 tooltip，需手动画）"""
        if self._btn_hint.get("win") is not None:
            return
        try:
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            win = tk.Toplevel(self.root)
            win.wm_overrideredirect(True)
            win.wm_geometry("+%d+%d" % (max(0, x - 190), y))
            win.wm_attributes("-topmost", True)
            tk.Label(win, text=text, bg="#2b2e33", fg="#dadbdf",
                     font=FONTS["ui12"], padx=10, pady=6, justify="left",
                     highlightthickness=1, highlightbackground="#6b6b6b").pack()
            self._btn_hint["win"] = win
        except Exception:
            pass

    def _show_btn_hint(self, meta, _event=None):
        """禁用按钮的悬停提醒：动态生成当前缺失条件说明。
        不依赖 cget("state")（PillButton 自绘按钮的 state 被内部拦截，
        cget 恒为 normal），改用实时原因判断。"""
        if self._btn_hint.get("win") is not None:
            return
        try:
            reasons = self._btn_reasons(meta)
            if not reasons:
                return
            x = meta["b"].winfo_rootx() + meta["b"].winfo_width() // 2
            y = meta["b"].winfo_rooty() + meta["b"].winfo_height() + 4
            win = tk.Toplevel(self.root)
            win.wm_overrideredirect(True)
            win.wm_geometry("+%d+%d" % (max(0, x - 160), y))
            win.wm_attributes("-topmost", True)
            tk.Label(win, text="\n".join(reasons), bg="#2b2e33", fg="#dadbdf",
                     font=FONTS["ui12"], padx=10, pady=6, justify="left",
                     highlightthickness=1, highlightbackground="#6b6b6b").pack()
            tip = self._btn_hint
            def _on_close():
                try:
                    win.destroy()
                except Exception:
                    pass
                tip["win"] = None
            win.bind("<Leave>", lambda _e: _on_close())
            tip["win"] = win
        except Exception:
            # 轻微1：气泡可能已创建一半，销毁后再清引用
            self._hide_btn_hint()

    def _hide_btn_hint(self, _event=None):
        win = self._btn_hint.get("win")
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            self._btn_hint["win"] = None

    def _refresh_env(self):
        def work():
            self.q.put(("env", running_procs()))
        threading.Thread(target=work, daemon=True).start()

    def _fingerprint(self):
        """三账指纹：订阅清单/账本 mtime + 工坊目录条目快照（毫秒级，哨兵用）"""
        if not self.P:
            return None
        try:
            parts = []
            for key in ("subs", "acf"):
                p = self.P.get(key)
                parts.append(os.path.getmtime(p) if p and os.path.exists(p) else 0)
            content = self.P.get("content") or ""
            if content and os.path.isdir(content):
                parts.append(tuple(sorted(os.listdir(content))))
            else:
                parts.append(())
            return hash(tuple(parts))
        except Exception:
            return None

    def _sentinel(self):
        """哨兵（随 8 秒心跳跑）：三账指纹变化 → 自动轻量刷新；
        距上次网络检测 ≥60 秒 → 后台重测社区网络。均为毫秒级开销。"""
        # M2：模态对话框（确认框）打开期间顺延——避免对话框下抢 busy/重建视图
        try:
            if self.root.grab_current() is not None:
                return
        except Exception:
            pass
        # 三账指纹（未排查过没有基线；busy 期间顺延）
        if self.P and not self.busy and self._fp is not None:
            fp = self._fingerprint()
            if fp is not None and fp != self._fp:
                self._fp_prev = self._fp   # 留一份，自动刷新失败时回滚基线
                self._fp = fp          # 先更新基线，避免重复触发
                self._log("↻ 检测到订阅/文件发生变化（如手动退订/删除），自动刷新……"
                          "（若刷新失败请手动点[刷新]）")
                self._btn_refresh(auto=True)
        # 网络检测（60 秒周期，后台线程）
        if time.time() - self._net_ts >= 60:
            self._net_ts = time.time()

            def nwork():
                try:
                    self.q.put(("net", actions.check_network(timeout=4)))
                except Exception:
                    pass
            threading.Thread(target=nwork, daemon=True).start()

    def _env_loop(self):
        """每 8 秒心跳：进程状态 + 三账哨兵 + 网络周期检测。
        心跳体异常只记日志，绝不杀死 after 链（否则所有周期功能静默失效）。"""
        try:
            self._refresh_env()
            self._sentinel()
        except Exception as e:
            try:
                self._log("✗ 心跳异常：%s" % e)
            except Exception:
                pass
        try:
            self.root.after(8000, self._env_loop)
        except tk.TclError:
            pass

    def _set_net_label(self):
        """右上角网络块（独立底色）。单独成函数：进程快照与网络结果
        各自独立到达，谁先到都能只刷新自己那一块，不会互相覆盖。"""
        if self._net_ok is True:
            ntext, nfg, nbg = "社区网络 可达", "#82c882", T["SOFT"]
        elif self._net_ok is False:
            ntext, nfg, nbg = "社区网络 不可达（需加速器）", T["ACCENT"], "#2a1d12"
        else:
            ntext, nfg, nbg = "社区网络 未检测", T["MUTE"], T["SOFT"]
        try:
            self.env_net_lbl.configure(text=ntext, fg=nfg, bg=nbg)
        except tk.TclError:
            pass

    def _set_env(self, procs):
        """右上角状态两块：网络块（独立底色）+ 进程块（Steam/WE）"""
        self._set_net_label()
        # 进程块
        if procs is None:
            self.env_lbl.configure(text="进程检测失败", fg=T["MUTE"])
            return
        st = "运行中" if any(p == "steam.exe" for p in procs) else "未运行"
        we = "运行中" if any(p.startswith("wallpaper") for p in procs) else "未运行"
        running = (st == "运行中") or (we == "运行中")
        self.env_lbl.configure(text="Steam %s · WE %s" % (st, we),
                               fg=T["ACCENT"] if running else T["MUTE"])

    # ---------- 按钮 ----------

    def _btn_diag(self):
        if self.busy:
            return
        self._set_busy(True)
        self._last_was_refresh = False     # 主线程写，避免与工作线程竞态
        self._auto_refresh = False
        self._set_status("正在探测路径并检查，请稍候……（全程只读）")
        self._log("▶ 开始排查……")
        threading.Thread(target=self._work_diag, daemon=True).start()

    def _btn_refresh(self, auto=False):
        """轻量刷新：环境+索引+三方比对+网络（跳过大小校验/日志，2~3 秒）"""
        if self.busy:
            return
        if not self.P:
            self._log("· 请先「一键排查」获取路径信息。")
            return
        self._set_busy(True)
        self._auto_refresh = bool(auto)   # M4：auto 路径失败降级为日志，不弹窗
        self._last_was_refresh = True     # M3：轻量刷新不覆盖完整报告文件
        self._set_status("快速刷新中（三账 + 网络）……")
        self._log("▶ %s刷新……" % ("哨兵检测到变化，自动" if auto else "手动"))
        threading.Thread(target=self._work_refresh, daemon=True).start()

    def _work_refresh(self):
        try:
            # 轻微6：网络检测与三账收集并行（坏网时不串行多等 8 秒）
            def nwork():
                try:
                    self.q.put(("net", actions.check_network(timeout=4)))
                except Exception:
                    pass
            threading.Thread(target=nwork, daemon=True).start()
            P = resolve_paths()
            D, issues, text = collect_and_render(P, "1", MODES["1"][0])
            self.q.put(("diag_done", (P, D, issues, text, None)))  # M3：不覆盖完整报告
        except Exception as e:
            self.q.put(("fatal", "刷新出错：%s" % e))

    def _btn_fix(self):
        if self.busy:
            return
        if not (self.D and self.D.get("repairable")):
            self._log("· 当前没有可自动修复的问题（请先「一键排查」）。")
            return
        self._set_busy(True)
        self._last_was_refresh = False     # 修复后的复检是完整排查口径
        self._auto_refresh = False
        self._set_status("正在自动修复（将先检查 Steam / Wallpaper Engine 是否已退出）……")
        self._log("▶ 开始自动修复……")
        threading.Thread(target=self._work_fix, daemon=True).start()

    def _btn_copy(self):
        if not self.report_text:
            self._log("· 还没有完整报告可复制，请先「一键排查」。")
            return
        # 顺序：先 Tk（应用内立即可读）→ 再 clip.exe（系统持有，关窗后仍在）。
        # 反过来会让 Tk 抢回所有权，进程退出后剪贴板就空了。
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.report_text)
        except Exception:
            pass
        ok_os = copy_to_clipboard(self.report_text)
        if ok_os:
            self._log("✓ 报告已复制到剪贴板（关闭本工具后仍可粘贴），可直接交给 AI 工具。")
            self._set_status("✓ 完整报告已复制到剪贴板")
        else:
            self._log("✓ 报告已复制到剪贴板（注意：关闭本工具后内容会消失）。")

    # ---------- 工作线程 ----------

    def _work_diag(self):
        try:
            P = resolve_paths()
            D, issues, text = collect_and_render(P, "0", MODES["0"][0])
            out = write_report(text)
            self.q.put(("diag_done", (P, D, issues, text, out)))
            try:   # 排查时联动一次网络检测（后台）
                self.q.put(("net", actions.check_network(timeout=4)))
            except Exception:
                pass
        except Exception as e:
            self.q.put(("fatal", "排查出错：%s" % e))

    def _work_fix(self):
        try:
            ok, msg, bak = repair_run(
                self.P, self.D, ui=lambda s: self.q.put(("log", s)))
            if bak:
                # 承诺「可回滚」就要给出回滚点在哪，否则用户只能翻目录瞎找
                self.q.put(("log", "备份文件：%s" % bak))
            self.q.put(("fix_done", (ok, msg)))
            if ok:
                self.q.put(("log", "▶ 修复成功，正在按最新状态复检并更新报告……"))
                P2 = resolve_paths()
                D2, issues2, text2 = collect_and_render(P2, "0", MODES["0"][0])
                out2 = write_report(text2)
                self.q.put(("diag_done", (P2, D2, issues2, text2, out2)))
                self.q.put(("log", "✓ 报告已更新。现在可以打开 Steam 了。"))
        except Exception as e:
            self.q.put(("fatal", "修复出错：%s" % e))

    # ---------- 消息泵 ----------

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                try:
                    self._handle(kind, payload)
                except Exception as e:
                    # 单条消息处理出错不拖垮整个消息泵；但**终态消息**必须兜底复位
                    # busy —— 否则一次渲染异常就会让按钮全灰、哨兵停摆、窗口还关不掉，
                    # 用户只能去任务管理器杀进程。
                    self._log("✗ 内部错误：%s\n%s" % (e, traceback.format_exc()))
                    if kind in ("diag_done", "fix_done", "action_done", "fatal"):
                        try:
                            self._set_busy(False)
                        except Exception:
                            pass
        except queue.Empty:
            pass
        finally:
            try:
                self.root.after(80, self._poll)
            except tk.TclError:
                pass

    def _handle(self, kind, payload):
        if kind == "log":
            self._log(str(payload))
        elif kind == "env":
            self._set_env(payload)      # 同时刷新网络块（复用已有 _net_ok）
        elif kind == "net":
            # 网络检测结果（后台线程回传）：(ok, 说明)
            # 修复：此前没有这个分支，消息被静默丢弃 → _net_ok 恒为 None
            # → 右上角永远停在"社区网络 未检测"（灰字），看起来像没显示。
            try:
                ok, _msg = payload
            except Exception:
                return              # 畸形消息：保留上次状态，别把已知结果冲成 None
            if ok is None:
                return
            self._net_ok = bool(ok)
            self._net_ts = time.time()          # 以实际结果时间为准，避免重复检测
            self._set_net_label()
            self._refresh_action_buttons()      # 需要网络的按钮随之解锁/置灰
        elif kind == "titles":
            self._apply_titles(payload)
        elif kind == "diag_done":
            P, D, issues, text, out = payload
            self.P, self.D = P, D
            if getattr(self, "_last_was_refresh", False):
                # 轻量刷新只跑 3 项检查，不能覆盖「完整报告」
                self.refresh_text = text
            else:
                # 完整排查：即使报告写盘失败（out=None），面板里的全文仍是完整口径，
                # 脚注承诺的「仍可复制全文」才成立。
                self.report_text = text
            self._fp = self._fingerprint()   # 记录基线（哨兵刷新的比对基准）
            self._auto_fails = 0             # 刷新成功，重置自动刷新重试计数
            self._set_view(P, D, issues, out)
            self._set_busy(False)
            self._auto_refresh = False      # 正常完成，清除 auto 标志
            if out:
                self._log("✓ 报告已保存：%s" % out)
            elif getattr(self, "_last_was_refresh", False):
                self._log("· 轻量刷新不更新报告文件（完整报告以最近一次「一键排查」为准）")
            else:
                self._log("✗ 报告写入失败（目录只读或被占用），面板内容仍可复制")
            n = len(issues)
            if n == 0 and P.get("errors"):
                # 路径没探全时，"0 个问题"不等于"一切正常"：卡片区与 CLI 都写明了，
                # 状态栏不能给相反的结论。
                self._set_status("排查未完成：路径探测失败，结果不完整（详见报告）")
            else:
                self._set_status("排查完成：%s" % ("未发现问题，一切正常 ✓" if n == 0
                                                  else "发现 %d 个问题（详见报告）" % n))
            repairable = list(D.get("repairable") or [])
            if repairable:
                add_n = len(D.get("repairable_add") or [])
                del_n = len(D.get("repairable_del") or [])
                parts = []
                if add_n:
                    parts.append("补录 %d 条漏登记录" % add_n)
                if del_n:
                    parts.append("移除 %d 条残留记录" % del_n)
                self._log("★ 账本记录可自动处理 %d 条（%s）。可点「自动修复」批量处理。"
                          % (len(repairable), "、".join(parts) or "—"))
            else:
                self._log("· 当前没有可自动修复的问题%s。"
                          % ("（%s）" % D.get("repair_reason") if D.get("repair_reason") else ""))
            self._refresh_env()
        elif kind == "fix_done":
            ok, msg = payload
            if ok:
                self._log("✓ " + msg)
            else:
                # 失败时必须复位忙状态，否则按钮会永久卡死（例：Steam 未退出被拒后）
                self._set_busy(False)
                self._log("✗ " + msg)
        elif kind == "action_done":
            ok, msg, followup = payload
            self._log(("✓ " if ok else "✗ ") + msg)
            # 转本地成功 → 主线程询问是否顺带删除原文件夹（避免线程碰 tkinter）
            if ok and followup and followup[0] == "recycle_ask" and self.P:
                iid = followup[1]
                folder = os.path.join(self.P.get("content") or "", iid)
                if os.path.isdir(folder) and messagebox.askyesno(
                        "删除原文件夹",
                        "本地副本已创建成功。\n是否把工坊目录里的原文件夹也删除到"
                        "回收站？\n\n%s" % folder):
                    def _fn(ui, i=iid):
                        ok2, msg2, _d, _f = actions.recycle_delete(
                            self.P, [i], ui=ui,
                            expected_sub_count=self._expected_subs())
                        return ok2, msg2, None
                    self._set_busy(False)   # S2：先复位再启动（_start_action 有忙检查）
                    self._start_action("[回收站] %s" % iid, _fn)
                    return
            self._set_busy(False)
            if ok:
                self._log("▶ 正在按最新状态复检并更新报告……")
                self._set_busy(True)     # 复检期间保持忙状态，防止并发操作
                self._last_was_refresh = False   # 复检是完整口径，别被当成轻量刷新
                self._auto_refresh = False
                threading.Thread(target=self._work_diag, daemon=True).start()
        elif kind == "fatal":
            self._set_busy(False)
            self._log("✗ " + str(payload))
            if getattr(self, "_auto_refresh", False):
                # M4：哨兵自动刷新失败不弹窗（防错误窗风暴），日志+状态栏指引
                self._auto_refresh = False
                # 触发时基线已经更新过，若不复位，这次变化就永久丢了。
                # 回滚到触发前的基线，并允许最多重试两次，避免持续失败时每 8 秒刷屏。
                self._auto_fails = getattr(self, "_auto_fails", 0) + 1
                if self._auto_fails <= 2 and getattr(self, "_fp_prev", None) is not None:
                    self._fp = self._fp_prev
                self._set_status("自动刷新失败——请手动点[刷新]重试；"
                                 "若持续失败请用「一键排查」")
            else:
                messagebox.showerror("出错", str(payload))


def main():
    # 开启 DPI 感知：避免缩放 >100% 的显示器上被系统位图拉伸（整窗发虚）。
    # 必须在创建 Tk 之前调用；已感知时重复设置无副作用。
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 每显示器感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    autorun = "--autorun" in sys.argv   # 测试/预览辅助：启动后自动执行一次「一键排查」
    root = tk.Tk()
    root.withdraw()
    _resolve_fonts(root)
    apply_theme(root)
    root.deiconify()
    app = App(root)
    if autorun:
        root.after(700, app._btn_diag)
    root.after(60, lambda: _set_dark_titlebar(root))
    root.bind("<Map>", lambda e: _set_dark_titlebar(root))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
