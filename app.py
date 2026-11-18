#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
NEUROLINK CLICKER · 神经连点器
--------------------------------------------------
· 全局录制鼠标 / 键盘动作（F9 开始/结束）
· 按时间轴回放（F10 播放/停止，ESC 急停）
· 倍速、重复次数、循环间隔可调
· 动作时间轴可视化编辑（延迟、排序、删除、插入等待）
· 脚本保存 / 载入
"""
import ctypes
from ctypes import wintypes
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
WEB_INDEX = os.path.join(BASE_DIR, "web", "index.html")

# 内置依赖优先：vendor/ 里随项目携带 pynput，换机器 / 重装 Python 也不用再 pip install
_VENDOR_DIR = os.path.join(BASE_DIR, "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)
os.makedirs(SCRIPTS_DIR, exist_ok=True)


def enable_dpi_awareness():
    """让进程以物理像素工作，保证录制与回放的坐标一致。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


enable_dpi_awareness()


def _msgbox(text, title="NEUROLINK 连点器", flags=0x10):
    """没有控制台时用来把错误摆到用户眼前。"""
    try:
        fn = ctypes.windll.user32.MessageBoxW
        fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        fn(None, text, title, flags)
    except Exception:
        pass


_WINDOW_HWND = {"h": None}


def work_area():
    """主屏可用区域（扣掉任务栏），用于最大化与拖动边界。"""
    try:
        r = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return (0, 0, 1320, 900)


class WindowBridge:
    """无边框窗口的原生操作：拖动、最小化、最大化、关闭。"""

    def __init__(self):
        self._maxed = False
        self._restore = None

    @staticmethod
    def _win():
        try:
            import webview
            return webview.windows[0] if webview.windows else None
        except Exception:
            return None

    def win_pos(self):
        w = self._win()
        if not w:
            return {"x": 0, "y": 0}
        return {"x": int(w.x), "y": int(w.y)}

    @staticmethod
    def _hwnd():
        # 优先用启动时缓存的句柄；没有就按窗口标题找，绕开跨线程取 WinForms 控件属性
        if _WINDOW_HWND.get("h"):
            return _WINDOW_HWND["h"]
        u = ctypes.windll.user32
        try:
            u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            u.FindWindowW.restype = ctypes.c_void_p
            h = u.FindWindowW(None, "NEUROLINK · 连点器")
            if h:
                _WINDOW_HWND["h"] = int(h)
                return int(h)
        except Exception:
            pass
        w = WindowBridge._win()
        if not w:
            return None
        try:
            handle = w.gui.Handle
            h = int(handle.ToInt64()) if hasattr(handle, "ToInt64") else int(handle)
            _WINDOW_HWND["h"] = h
            return h
        except Exception:
            return None

    def win_rect(self):
        """直接读窗口物理矩形，和 SetWindowPos 用同一套坐标，拖动/缩放才不会错位。"""
        hwnd = self._hwnd()
        if not hwnd:
            return {"x": 0, "y": 0, "w": 0, "h": 0}
        try:
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
            return {"x": int(r.left), "y": int(r.top),
                    "w": int(r.right - r.left), "h": int(r.bottom - r.top)}
        except Exception:
            return {"x": 0, "y": 0, "w": 0, "h": 0}

    def win_pos(self):
        r = self.win_rect()
        return {"x": r["x"], "y": r["y"]}

    def set_rect(self, x, y, w, h):
        """一次 SetWindowPos 同时改位置和大小，拖边缩放才不会有二次跳动。"""
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            wa = work_area()
            nw = max(980, min(int(w), wa[2]))
            nh = max(660, min(int(h), wa[3]))
            nx = max(wa[0] - nw + 140, min(int(x), wa[0] + wa[2] - 140))
            ny = max(wa[1], min(int(y), wa[1] + wa[3] - 60))
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd), None, nx, ny, nw, nh,
                0x0004 | 0x0010)   # SWP_NOZORDER | SWP_NOACTIVATE
        except Exception:
            pass
        return True

    def move_to(self, x, y):
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            r = self.win_rect()
            if not r["w"]:
                return False
            wa = work_area()
            # 留出至少 140px 可见宽度、60px 标题区，避免窗口被拖到屏幕外找不回来
            nx = max(wa[0] - r["w"] + 140, min(int(x), wa[0] + wa[2] - 140))
            ny = max(wa[1], min(int(y), wa[1] + wa[3] - 60))
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd), None, nx, ny, 0, 0,
                0x0001 | 0x0004 | 0x0010)   # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        except Exception:
            pass
        return True

    def minimize(self):
        hwnd = self._hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 6)  # SW_MINIMIZE
                return True
            except Exception:
                pass
        w = self._win()
        if w:
            w.minimize()
        return True

    def toggle_maximize(self):
        try:
            r = self.win_rect()
            if not r["w"]:
                return False
            if self._maxed:
                if self._restore:
                    self.set_rect(self._restore[2], self._restore[3],
                                  self._restore[0], self._restore[1])
                self._maxed = False
            else:
                self._restore = (r["w"], r["h"], r["x"], r["y"])
                wa = work_area()
                self.set_rect(wa[0], wa[1], wa[2], wa[3])
                self._maxed = True
        except Exception:
            pass
        return self._maxed

    def quit_app(self):
        w = self._win()
        if w:
            try:
                w.destroy()
                return True
            except Exception:
                pass
        hwnd = self._hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)
                return True
            except Exception:
                pass
        return False


def _style_window(win):
    """无边框窗口：Win11 圆角 + 自己的图标。"""
    try:
        handle = win.gui.Handle
        hwnd = int(handle.ToInt64()) if hasattr(handle, "ToInt64") else int(handle)
        _WINDOW_HWND["h"] = hwnd

        # Win11 圆角（无边框窗口能吃到就有，吃不到就是直角，不影响功能）
        try:
            fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
            fn.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            fn.restype = ctypes.c_int
            value = ctypes.c_int(2)   # DWMWCP_ROUND
            fn(hwnd, 33, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

        # 任务栏和 Alt+Tab 用连点器自己的图标
        icon = os.path.join(BASE_DIR, "icon.ico")
        if os.path.exists(icon):
            load = ctypes.windll.user32.LoadImageW
            load.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            load.restype = ctypes.c_void_p
            hicon = load(None, icon, 1, 0, 0, 0x10 | 0x20)  # IMAGE_ICON, LR_LOADFROMFILE|DEFAULTSIZE
            if hicon:
                send = ctypes.windll.user32.SendMessageW
                send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
                send.restype = ctypes.c_void_p
                send(hwnd, 0x80, 0, hicon)  # WM_SETICON, ICON_SMALL
                send(hwnd, 0x80, 1, hicon)  # WM_SETICON, ICON_BIG
    except Exception:
        pass


def _attach_log():
    """由 pythonw 启动时没有控制台，把输出重定向到日志文件。"""
    try:
        if sys.stdout is not None and sys.stderr is not None:
            return
        f = open(os.path.join(BASE_DIR, "运行日志.txt"), "w", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
    except Exception:
        pass


_attach_log()

from pynput import keyboard, mouse  # noqa: E402

HOT_RECORD = "f9"
HOT_PLAY = "f10"
HOT_STOP = "esc"
CONTROL_KEYS = {HOT_RECORD, HOT_PLAY, HOT_STOP}

BUTTONS = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
    "x1": getattr(mouse.Button, "x1", mouse.Button.left),
    "x2": getattr(mouse.Button, "x2", mouse.Button.right),
}

# ---------------------------------------------------------------- 输入方式
# 光标移动有两种实现：
#   SetCursorPos（pynput 的 position 赋值）—— 桌面/普通软件都认，但不少游戏只读鼠标消息，会忽略它
#   SendInput + MOUSEEVENTF_ABSOLUTE     —— 走的是系统消息队列，兼容性更好
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0


def _virtual_desktop():
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            max(1, u.GetSystemMetrics(78)), max(1, u.GetSystemMetrics(79)))


def send_move_abs(x, y):
    """按虚拟桌面归一化坐标发绝对移动，返回 SendInput 是否成功。"""
    vx, vy, vw, vh = _virtual_desktop()
    inp = _INPUT(type=INPUT_MOUSE)
    inp.u.mi.dx = int(round((x - vx) * 65535.0 / vw))
    inp.u.mi.dy = int(round((y - vy) * 65535.0 / vh))
    inp.u.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def send_move_rel(dx, dy):
    """发相对位移：不带 ABSOLUTE，游戏按 raw input 读到的就是这两个值。"""
    inp = _INPUT(type=INPUT_MOUSE)
    inp.u.mi.dx = int(dx)
    inp.u.mi.dy = int(dy)
    inp.u.mi.dwFlags = MOUSEEVENTF_MOVE
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def mouse_accel_off():
    """临时关掉指针加速，让相对位移 1:1 还原；返回原设置以便恢复。"""
    try:
        arr = (ctypes.c_int * 3)()
        if ctypes.windll.user32.SystemParametersInfoW(0x0004, 0, ctypes.byref(arr), 0):
            old = [int(arr[0]), int(arr[1]), int(arr[2])]
            off = (ctypes.c_int * 3)(0, 0, 0)
            ctypes.windll.user32.SystemParametersInfoW(0x0003, 0, ctypes.byref(off), 0)
            return old
    except Exception:
        pass
    return None


def mouse_accel_restore(old):
    if not old:
        return
    try:
        arr = (ctypes.c_int * 3)(*old)
        ctypes.windll.user32.SystemParametersInfoW(0x0003, 0, ctypes.byref(arr), 0)
    except Exception:
        pass


def time_period_begin():
    """把系统定时精度提到 1ms，密集的相对位移才能按原节奏逐帧推送。"""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


def time_period_end():
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", ctypes.c_ushort), ("usUsage", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint), ("hwndTarget", ctypes.c_void_p)]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", ctypes.c_uint), ("dwSize", ctypes.c_uint),
                ("hDevice", ctypes.c_void_p), ("wParam", ctypes.c_void_p)]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", ctypes.c_ushort), ("usButtonFlags", ctypes.c_ushort),
                ("usButtonData", ctypes.c_ushort), ("ulRawButtons", ctypes.c_uint),
                ("lLastX", ctypes.c_long), ("lLastY", ctypes.c_long),
                ("ulExtraInformation", ctypes.c_uint)]


class _RAWINPUT(ctypes.Structure):
    _fields_ = [("header", _RAWINPUTHEADER), ("mouse", _RAWMOUSE)]


class RawMouse:
    """设备级相对位移监听。游戏把光标锁住以后，只有它能录到真实的视角转动。"""

    def __init__(self):
        self.on_move = None          # 回调 (dx, dy)
        self.last_err = ""
        self._thread = None
        self._hwnd = None
        self._ready = threading.Event()
        self._proc = None

    # ---- 消息回调：只做转发，保证消息泵不阻塞
    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x00FF:        # WM_INPUT
                u = ctypes.windll.user32
                size = ctypes.c_uint(0)
                u.GetRawInputData(lp, 0x10000003, None, ctypes.byref(size),
                                  ctypes.sizeof(_RAWINPUTHEADER))
                if size.value:
                    buf = ctypes.create_string_buffer(size.value)
                    got = u.GetRawInputData(lp, 0x10000003, buf, ctypes.byref(size),
                                            ctypes.sizeof(_RAWINPUTHEADER))
                    if got == size.value:
                        ri = ctypes.cast(buf, ctypes.POINTER(_RAWINPUT)).contents
                        if ri.header.dwType == 0 and self.on_move:
                            self.on_move(ri.mouse.lLastX, ri.mouse.lLastY)
        except Exception:
            pass
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _run(self):
        try:
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            u.GetRawInputData.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_uint), ctypes.c_uint]
            u.GetRawInputData.restype = ctypes.c_uint
            u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            u.SetWindowLongPtrW.restype = ctypes.c_void_p
            u.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RAWINPUTDEVICE),
                                                  ctypes.c_uint, ctypes.c_uint]

            proc_type = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_void_p, ctypes.c_void_p)
            self._proc = proc_type(self._wndproc)

            hinst = k.GetModuleHandleW(None)
            hwnd = u.CreateWindowExW(0, "STATIC", "NEUROLINK-RAW", 0, 0, 0, 0, 0,
                                     ctypes.c_void_p(-3), None, hinst, None)
            if not hwnd:
                self.last_err = "创建监听窗口失败"
                return
            self._hwnd = hwnd
            u.SetWindowLongPtrW(hwnd, -4, ctypes.cast(self._proc, ctypes.c_void_p))
            rid = _RAWINPUTDEVICE(0x01, 0x02, 0x0100, hwnd)   # 鼠标 / RIDEV_INPUTSINK
            if not u.RegisterRawInputDevices(ctypes.byref(rid), 1,
                                             ctypes.sizeof(_RAWINPUTDEVICE)):
                self.last_err = "注册原始输入失败"
                return

            self._ready.set()
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.last_err = str(exc)
        finally:
            self._ready.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._hwnd = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self._hwnd is not None

    def stop(self):
        try:
            if self._hwnd:
                ctypes.windll.user32.PostMessageW(ctypes.c_void_p(self._hwnd), 0x0010, 0, 0)
        except Exception:
            pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def key_to_dict(key):
    """把 pynput 的按键对象转成可序列化的字典。"""
    if isinstance(key, keyboard.Key):
        return {"n": key.name}
    if isinstance(key, keyboard.KeyCode):
        if key.char is not None:
            return {"c": key.char}
        if getattr(key, "vk", None) is not None:
            return {"v": int(key.vk)}
    return {"c": ""}


def dict_to_key(d):
    """字典还原成 pynput 按键对象。"""
    if "n" in d:
        return getattr(keyboard.Key, d["n"])
    if "v" in d:
        return keyboard.KeyCode.from_vk(d["v"])
    ch = d.get("c", "")
    if len(ch) == 1:
        return keyboard.KeyCode.from_char(ch)
    return keyboard.KeyCode.from_char("a")


class Engine(object):
    """录制、回放、时间轴编辑的核心状态机。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.actions = []
        self.rev = 1
        self.delta_from = 0        # 从这个下标之后为“新增”，用于增量同步
        self.mode = "idle"         # idle / rec / play
        self.rec_t0 = 0.0
        self.grace_until = 0.0
        self.speed = 1.0
        self.repeat = 1            # 0 表示无限循环
        self.gap = 1000            # 每轮之间停顿时长(ms)
        self.stop_flag = threading.Event()
        self.progress = {"loop": 0, "index": 0, "elapsed": 0, "duration": 0}
        self.logs = deque(maxlen=80)
        self._scripts_cache = []
        self._scripts_cache_at = 0.0
        self.hits = {"move": 0, "click": 0, "scroll": 0, "key": 0}
        self.mouse_listener = None
        self.key_listener = None
        self.mouse_c = mouse.Controller()
        self.kb_c = keyboard.Controller()
        self.enhanced = True       # 优先用 SendInput 移动，兼容只读鼠标消息的游戏
        self.raw_mode = False      # 视角录制：改用设备级相对位移，适合 3D 游戏里的转头
        self.raw = RawMouse()
        self.raw.on_move = self.on_raw_move
        self._acc_x = 0            # 回放相对位移的累计量，用来决定何时把光标拉回中心
        self._acc_y = 0

    # ---------------------------------------------------------------- 日志
    def log(self, msg, level="info"):
        with self.lock:
            self.logs.append({
                "t": time.strftime("%H:%M:%S"),
                "m": msg,
                "lv": level,
            })

    # ---------------------------------------------------------------- 录制
    def set_raw_mode(self, on):
        """切换"视角录制"：开 = 记设备级相对位移，专门对付会锁光标的 3D 游戏。"""
        on = bool(on)
        with self.lock:
            if self.mode != "idle":
                return False, "请先停止录制或播放"
            self.raw_mode = on
        if on and not self.raw.start():
            with self.lock:
                self.raw_mode = False
            return False, "视角录制不可用：%s" % (self.raw.last_err or "未知原因")
        return True, ""

    def _now_ms(self):
        return (time.perf_counter() - self.rec_t0) * 1000.0

    def _append(self, action):
        action["t"] = int(max(0, self._now_ms()))
        self.actions.append(action)
        self.rev += 1

    def _touch(self, index):
        self.rev += 1
        if index < self.delta_from:
            self.delta_from = max(0, index)

    def start_record(self):
        with self.lock:
            if self.mode == "play":
                self.stop_flag.set()
                self.mode = "idle"
            self.actions = []
            self.rev += 1
            self.delta_from = 0
            self.mode = "rec"
            self.rec_t0 = time.perf_counter()
            self.grace_until = self.rec_t0 + 0.35   # 忽略“点按钮”带来的干扰事件
            self.progress = {"loop": 0, "index": 0, "elapsed": 0, "duration": 0}
            self.log("录制中 · 请开始你的操作", "rec")
            return True

    def stop_record(self):
        with self.lock:
            if self.mode != "rec":
                return False
            dur = int(self._now_ms())
            self.mode = "idle"
            self.log("录制完成 · %d 个动作 · %.1fs" % (len(self.actions), dur / 1000.0), "ok")
            return True

    # ---------------------------------------------------------- 钩子回调
    def on_move(self, x, y):
        self.hits["move"] += 1
        with self.lock:
            if self.mode != "rec" or time.perf_counter() < self.grace_until:
                return
            if self.raw_mode:
                return          # 视角模式下由原始位移接管，绝对坐标只会把视角带宽压扁
            t = int(max(0, self._now_ms()))
            arr = self.actions
            if arr and arr[-1].get("type") == "move":
                last = arr[-1]
                if t - last["t"] < 14 and abs(x - last["x"]) + abs(y - last["y"]) < 8:
                    last["x"], last["y"], last["t"] = int(x), int(y), t
                    self._touch(len(arr) - 1)
                    return
            arr.append({"type": "move", "x": int(x), "y": int(y), "t": t})
            self.rev += 1

    def on_raw_move(self, dx, dy):
        """视角模式：把设备级位移按时间轴记成 moverel，短时间内就近合并，避免动作爆炸。"""
        if not dx and not dy:
            return
        with self.lock:
            if self.mode != "rec" or not self.raw_mode:
                return
            if time.perf_counter() < self.grace_until:
                return
            t = int(max(0, self._now_ms()))
            arr = self.actions
            if arr and arr[-1].get("type") == "moverel" and arr[-1]["t"] == t:
                # 只合并同一毫秒内的多次采样，不压缩时间跨度，否则回放会一瞬间转到位
                arr[-1]["dx"] += int(dx)
                arr[-1]["dy"] += int(dy)
                self._touch(len(arr) - 1)
                return
            arr.append({"type": "moverel", "dx": int(dx), "dy": int(dy), "t": t})
            self.rev += 1

    def on_click(self, x, y, button, pressed):
        self.hits["click"] += 1
        with self.lock:
            if self.mode != "rec" or time.perf_counter() < self.grace_until:
                return
            action = {
                "type": "click", "x": int(x), "y": int(y),
                "b": getattr(button, "name", "left"),
                "d": 1 if pressed else 0,
            }
            if self.raw_mode:
                action["raw"] = 1
            self._append(action)
            return

    def on_scroll(self, x, y, dx, dy):
        self.hits["scroll"] += 1
        with self.lock:
            if self.mode != "rec" or time.perf_counter() < self.grace_until:
                return
            action = {
                "type": "scroll", "x": int(x), "y": int(y),
                "dx": round(float(dx), 2), "dy": round(float(dy), 2),
            }
            if self.raw_mode:
                action["raw"] = 1
            self._append(action)
            return

    def on_key(self, key, pressed):
        name = getattr(key, "name", None)
        if pressed and name in CONTROL_KEYS:
            self.hotkey(name)
            return
        if name in CONTROL_KEYS:
            return
        self.hits["key"] += 1
        with self.lock:
            if self.mode != "rec" or time.perf_counter() < self.grace_until:
                return
            self._append({"type": "key", "k": key_to_dict(key), "d": 1 if pressed else 0})

    # ---------------------------------------------------------------- 热键
    def hotkey(self, name):
        if name == HOT_RECORD:
            if self.mode == "rec":
                self.stop_record()
            else:
                self.start_record()
        elif name == HOT_PLAY:
            if self.mode == "play":
                self.stop()
            else:
                self.start_play()
        elif name == HOT_STOP:
            self.stop()

    # ---------------------------------------------------------------- 播放
    def start_play(self):
        with self.lock:
            if self.mode == "rec":
                self.stop_record()
            if self.mode == "play":
                return False
            if not self.actions:
                self.log("没有可播放的动作，请先录制", "warn")
                return False
            snapshot = [dict(a) for a in self.actions]
            self.mode = "play"
            self.stop_flag.clear()
            self.progress = {
                "loop": 0, "index": 0, "elapsed": 0,
                "duration": int(snapshot[-1].get("t", 0)),
            }
            th = threading.Thread(
                target=self._play_worker,
                args=(snapshot, float(self.speed), int(self.repeat), int(self.gap)),
                daemon=True,
            )
            th.start()
            self.log("播放中 · 倍速 %.2fx · %s" % (
                self.speed, "无限循环" if self.repeat <= 0 else "重复 %d 次" % self.repeat), "play")
            return True

    def stop(self):
        with self.lock:
            self.stop_flag.set()
            if self.mode == "rec":
                self.stop_record()
            if self.mode == "play":
                self.log("已停止", "warn")

    def _move_to(self, x, y):
        """移动到目标点：先发 SendInput 鼠标消息（兼容游戏），再用 SetCursorPos 校正精确落点。"""
        if self.enhanced:
            try:
                if send_move_abs(x, y):
                    self.mouse_c.position = (x, y)
                    return
            except Exception:
                self.enhanced = False
        self.mouse_c.position = (x, y)

    def _recenter(self):
        """把光标拉回屏幕中心，避免相对位移撞到屏幕边缘；SetCursorPos 不产生原始输入事件。"""
        try:
            u = ctypes.windll.user32
            w = u.GetSystemMetrics(0) or 1920
            h = u.GetSystemMetrics(1) or 1080
            u.SetCursorPos(w // 2, h // 2)
        except Exception:
            pass
        self._acc_x = 0
        self._acc_y = 0

    def _ensure_room(self):
        """光标离屏幕边缘太近就先归中，给相对位移留出空间；在中间区域就不动它。"""
        try:
            u = ctypes.windll.user32
            w = u.GetSystemMetrics(0) or 1920
            h = u.GetSystemMetrics(1) or 1080
            p = self._cursor()
            if p["x"] < 160 or p["y"] < 160 or p["x"] > w - 160 or p["y"] > h - 160:
                self._recenter()
        except Exception:
            pass

    def _exec(self, a):
        try:
            kind = a.get("type")
            if kind == "wait":
                return
            if kind == "moverel":
                if not send_move_rel(a.get("dx", 0), a.get("dy", 0)):
                    self.mouse_c.position = (
                        self.mouse_c.position[0] + a.get("dx", 0),
                        self.mouse_c.position[1] + a.get("dy", 0),
                    )
                self._acc_x += abs(a.get("dx", 0))
                self._acc_y += abs(a.get("dy", 0))
                if self._acc_x > 420 or self._acc_y > 320:
                    self._ensure_room()   # 只在光标真的贴近屏幕边缘时才归中，中间区域不动
                    self._acc_x = 0
                    self._acc_y = 0
                return
            if kind == "move":
                self._move_to(a["x"], a["y"])
            elif kind == "click":
                if not a.get("raw"):
                    self._move_to(a["x"], a["y"])
                btn = BUTTONS.get(a.get("b", "left"), mouse.Button.left)
                if a["d"]:
                    self.mouse_c.press(btn)
                else:
                    self.mouse_c.release(btn)
            elif kind == "scroll":
                if not a.get("raw"):
                    self._move_to(a["x"], a["y"])
                self.mouse_c.scroll(a.get("dx", 0), a.get("dy", 0))
            elif kind == "key":
                k = dict_to_key(a["k"])
                if a["d"]:
                    self.kb_c.press(k)
                else:
                    self.kb_c.release(k)
        except Exception as exc:  # 单条失败不影响后续
            self.log("动作执行失败: %s" % exc, "warn")

    def _sleep_until(self, target):
        while True:
            if self.stop_flag.is_set():
                return False
            rem = target - time.perf_counter()
            if rem <= 0:
                return True
            if rem > 0.0035:
                time.sleep(rem - 0.002)    # 先粗睡到离目标 2ms 内
            else:
                time.sleep(0)              # 剩下的交给自旋，避免被调度粒度拉到 15ms

    def _play_worker(self, acts, speed, repeat, gap):
        rounds = 0
        total = len(acts)
        acc_old = None
        if any(a.get("type") == "moverel" for a in acts):
            acc_old = mouse_accel_off()   # 关掉指针加速，相对位移才能 1:1 还原
            self._ensure_room()
        time_period_begin()          # 1ms 定时精度：视角转动才能按原节奏逐帧走
        try:
            while not self.stop_flag.is_set():
                rounds += 1
                with self.lock:
                    self.progress["loop"] = rounds
                    self.progress["index"] = 0
                t_base = time.perf_counter()
                last_ui = 0.0
                for i, a in enumerate(acts):
                    target = t_base + (a.get("t", 0) / 1000.0) / max(speed, 0.01)
                    if not self._sleep_until(target):
                        break
                    self._exec(a)
                    now = time.perf_counter()
                    if now - last_ui > 0.05:    # 高密度动作时降频刷新，别让锁竞争拖慢节奏
                        last_ui = now
                        with self.lock:
                            self.progress["index"] = i + 1
                            self.progress["elapsed"] = int(a.get("t", 0))
                if self.stop_flag.is_set():
                    break
                with self.lock:
                    self.progress["index"] = total
                    self.progress["elapsed"] = self.progress["duration"]
                if repeat > 0 and rounds >= repeat:
                    self.log("播放完成 · 共 %d 轮" % rounds, "ok")
                    break
                if gap > 0 and not self._sleep_until(time.perf_counter() + gap / 1000.0):
                    break
        finally:
            mouse_accel_restore(acc_old)
            time_period_end()
            with self.lock:
                if self.mode == "play":
                    self.mode = "idle"

    # ------------------------------------------------------------ 时间轴编辑
    def _prev_t(self, index):
        return self.actions[index - 1]["t"] if index > 0 else 0

    def _cursor(self):
        """当前光标坐标，供界面在插入移动/点击时预填。"""
        try:
            pos = self.mouse_c.position
            return {"x": int(pos[0]), "y": int(pos[1])}
        except Exception:
            return {"x": 0, "y": 0}

    @staticmethod
    def _clean_action(raw):
        """把界面提交的动作清洗成合法结构，非法时返回 (None, 原因)。"""
        if not isinstance(raw, dict):
            return None, "动作数据格式错误"
        kind = str(raw.get("type", "")).lower()
        if kind not in ("move", "click", "scroll", "key", "wait", "moverel"):
            return None, "不支持的动作类型"

        def as_int(v, lo, hi, dflt=0):
            try:
                return max(lo, min(hi, int(round(float(v)))))
            except Exception:
                return dflt

        a = {"type": kind, "t": 0}
        if kind in ("move", "click", "scroll"):
            a["x"] = as_int(raw.get("x"), 0, 32000)
            a["y"] = as_int(raw.get("y"), 0, 32000)
        if kind == "click":
            btn = str(raw.get("b", "left")).lower()
            a["b"] = btn if btn in BUTTONS else "left"
            a["d"] = 1 if as_int(raw.get("d"), 0, 1, 1) else 0
        if kind == "scroll":
            a["dx"] = as_int(raw.get("dx"), -200, 200, 0)
            a["dy"] = as_int(raw.get("dy"), -200, 200, 0)
        if kind == "moverel":
            a["dx"] = as_int(raw.get("dx"), -20000, 20000, 0)
            a["dy"] = as_int(raw.get("dy"), -20000, 20000, 0)
        if kind == "key":
            k = raw.get("k")
            if not isinstance(k, dict):
                return None, "缺少按键"
            kk = {}
            nm = str(k.get("n", "")).strip()
            if nm:
                if hasattr(keyboard.Key, nm):
                    kk["n"] = nm
                elif len(nm) == 1:
                    kk["c"] = nm
                else:
                    return None, "未知按键名：" + nm
            elif "v" in k:
                kk["v"] = as_int(k["v"], 0, 255, 65)
            elif "c" in k and str(k["c"]):
                kk["c"] = str(k["c"])[:1]
            else:
                return None, "按键为空"
            a["k"] = kk
            a["d"] = 1 if as_int(raw.get("d"), 0, 1, 1) else 0
        return a, ""

    def insert_action(self, index, raw_action, delay=200):
        """在 index 位置插入一条任意动作，后续动作整体顺延 delay 毫秒。"""
        with self.lock:
            if self.mode == "play":
                return False, "播放中无法编辑"
            act, err = self._clean_action(raw_action)
            if not act:
                return False, err
            index = max(0, min(int(index), len(self.actions)))
            delay = max(0, int(delay))
            prev = self._prev_t(index)
            for j in range(index, len(self.actions)):
                self.actions[j]["t"] += delay
            act["t"] = prev + delay
            self.actions.insert(index, act)
            self.rev += 1
            self.delta_from = min(self.delta_from, index)
            return True, ""

    def delete_range(self, index, count):
        """整段删除（用于折叠后的移动轨迹段）。"""
        with self.lock:
            if self.mode == "play":
                return False, "播放中无法编辑"
            if not (0 <= index < len(self.actions)):
                return False, "下标越界"
            cnt = max(1, min(int(count), len(self.actions) - index))
            delta = self.actions[index + cnt - 1]["t"] - self._prev_t(index)
            del self.actions[index:index + cnt]
            for j in range(index, len(self.actions)):
                self.actions[j]["t"] -= delta
                if self.actions[j]["t"] < 0:
                    self.actions[j]["t"] = 0
            self.rev += 1
            self.delta_from = min(self.delta_from, index)
            return True, ""

    def duplicate_range(self, index, count):
        """整段复制：把这段动作整体再接一遍，后续动作顺延。"""
        with self.lock:
            if self.mode == "play":
                return False, "播放中无法编辑"
            if not (0 <= index < len(self.actions)):
                return False, "下标越界"
            cnt = max(1, min(int(count), len(self.actions) - index))
            seg = [dict(a) for a in self.actions[index:index + cnt]]
            span = max(120, (seg[-1]["t"] - seg[0]["t"]) + 80)
            for j in range(index + cnt, len(self.actions)):
                self.actions[j]["t"] += span
            for k, a in enumerate(seg):
                na = dict(a)
                na["t"] = a["t"] + span
                self.actions.insert(index + cnt + k, na)
            self.rev += 1
            self.delta_from = min(self.delta_from, index)
            return True, ""

    def edit_delay(self, index, delay):
        with self.lock:
            if not (0 <= index < len(self.actions)):
                return False, "下标越界"
            if self.mode == "play":
                return False, "播放中无法编辑"
            delay = max(0, int(delay))
            a = self.actions[index]
            old = a["t"]
            new = self._prev_t(index) + delay
            shift = new - old
            for j in range(index, len(self.actions)):
                self.actions[j]["t"] += shift
            self._touch(index)
            return True, ""

    def delete_action(self, index):
        with self.lock:
            if not (0 <= index < len(self.actions)):
                return False, "下标越界"
            if self.mode == "play":
                return False, "播放中无法编辑"
            delta = self.actions[index]["t"] - self._prev_t(index)
            del self.actions[index]
            for j in range(index, len(self.actions)):
                self.actions[j]["t"] -= delta
                if self.actions[j]["t"] < 0:
                    self.actions[j]["t"] = 0
            self.rev += 1
            self.delta_from = min(self.delta_from, index)
            return True, ""

    def clear_actions(self):
        with self.lock:
            if self.mode == "play":
                return False, "播放中无法编辑"
            self.actions = []
            self.rev += 1
            self.delta_from = 0
            return True, ""

    def insert_wait(self, index, delay):
        with self.lock:
            if self.mode == "play":
                return False, "播放中无法编辑"
            index = max(0, min(index, len(self.actions)))
            delay = max(0, int(delay))
            for j in range(index, len(self.actions)):
                self.actions[j]["t"] += delay
            prev = self.actions[index - 1]["t"] if index > 0 else 0
            self.actions.insert(index, {"type": "wait", "t": prev + delay})
            self.rev += 1
            self.delta_from = min(self.delta_from, index)
            return True, ""

    def duplicate_action(self, index):
        with self.lock:
            if not (0 <= index < len(self.actions)):
                return False, "下标越界"
            if self.mode == "play":
                return False, "播放中无法编辑"
            src = dict(self.actions[index])
            delta = max(80, self.actions[index]["t"] - self._prev_t(index))
            for j in range(index + 1, len(self.actions)):
                self.actions[j]["t"] += delta
            src["t"] = self.actions[index]["t"] + delta
            self.actions.insert(index + 1, src)
            self.rev += 1
            self.delta_from = min(self.delta_from, index)
            return True, ""

    def swap_action(self, index, direction):
        with self.lock:
            if self.mode == "play":
                return False, "播放中无法编辑"
            j = index + direction
            if not (0 <= index < len(self.actions)) or not (0 <= j < len(self.actions)):
                return False, "下标越界"
            a, b = self.actions[index], self.actions[j]
            ta, tb = a["t"], b["t"]
            a2 = dict(b)
            a2["t"] = ta
            b2 = dict(a)
            b2["t"] = tb
            self.actions[index], self.actions[j] = a2, b2
            self.rev += 1
            self.delta_from = min(self.delta_from, min(index, j))
            return True, ""

    # ------------------------------------------------------------ 脚本管理
    @staticmethod
    def _safe_name(name):
        name = (name or "").strip()
        name = re.sub(r"[\\/:*?\"<>|]", "_", name)
        name = name[:40]
        return name

    def save_script(self, name):
        name = self._safe_name(name)
        if not name:
            return False, "请输入脚本名称"
        with self.lock:
            data = {
                "name": name,
                "version": 1,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "speed": self.speed,
                "repeat": self.repeat,
                "gap": self.gap,
                "actions": [dict(a) for a in self.actions],
            }
            path = os.path.join(SCRIPTS_DIR, name + ".json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=1)
            self.log("脚本已保存 · %s" % name, "ok")
            self._scripts_cache_at = 0.0
            return True, ""

    def load_script(self, name):
        name = self._safe_name(name)
        path = os.path.join(SCRIPTS_DIR, name + ".json")
        if not os.path.isfile(path):
            return False, "脚本不存在"
        with self.lock:
            if self.mode != "idle":
                return False, "请先停止录制或播放"
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            self.actions = [dict(a) for a in data.get("actions", [])]
            self.speed = float(data.get("speed", 1.0))
            self.repeat = int(data.get("repeat", 1))
            self.gap = int(data.get("gap", 1000))
            self.rev += 1
            self.delta_from = 0
            self.log("已载入脚本 · %s · %d 个动作" % (name, len(self.actions)), "ok")
            return True, ""

    def delete_script(self, name):
        name = self._safe_name(name)
        path = os.path.join(SCRIPTS_DIR, name + ".json")
        if not os.path.isfile(path):
            return False, "脚本不存在"
        os.remove(path)
        self._scripts_cache_at = 0.0
        return True, ""

    def list_scripts(self):
        items = []
        try:
            for fn in sorted(os.listdir(SCRIPTS_DIR)):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(SCRIPTS_DIR, fn)
                try:
                    st = os.stat(path)
                    with open(path, "r", encoding="utf-8") as fp:
                        d = json.load(fp)
                    items.append({
                        "name": fn[:-5],
                        "count": len(d.get("actions", [])),
                        "at": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return items

    # ---------------------------------------------------------------- 状态
    def state(self):
        with self.lock:
            count = len(self.actions)
            duration = int(self.actions[-1]["t"]) if self.actions else 0
            if time.time() - self._scripts_cache_at > 2.0:
                self._scripts_cache = self.list_scripts()
                self._scripts_cache_at = time.time()
            return {
                "ok": True,
                "rev": self.rev,
                "count": count,
                "mode": self.mode,
                "speed": round(self.speed, 2),
                "repeat": self.repeat,
                "gap": self.gap,
                "duration": duration,
                "raw": self.raw_mode,
                "progress": dict(self.progress),
                "logs": list(self.logs)[-10:],
                "scripts": list(self._scripts_cache),
                "diag": {
                    "hits": dict(self.hits),
                    "mouse": bool(self.mouse_listener and self.mouse_listener.running),
                    "key": bool(self.key_listener and self.key_listener.running),
                    "cursor": self._cursor(),
                    "admin": is_admin(),
                },
                "hotkeys": {"record": "F9", "play": "F10", "stop": "ESC"},
            }

    def slice(self, since):
        with self.lock:
            start = max(0, min(int(since), self.delta_from, len(self.actions)))
            return {
                "ok": True,
                "rev": self.rev,
                "start": start,
                "count": len(self.actions),
                "actions": self.actions[start:],
            }


ENGINE = Engine()
PORT_HOLDER = {"port": 0}


class Handler(BaseHTTPRequestHandler):
    server_version = "NeurolinkClicker/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                body = fp.read().encode("utf-8")
        except Exception:
            self._json({"ok": False, "error": "页面文件缺失: %s" % path}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        query = self.path.split("?")[1] if "?" in self.path else ""
        if path in ("/", "/index.html"):
            self._file(WEB_INDEX, "text/html")
            return
        if path == "/api/actions":
            since = 0
            for kv in query.split("&"):
                if kv.startswith("since="):
                    try:
                        since = int(kv[6:])
                    except Exception:
                        since = 0
            self._json(ENGINE.slice(since))
            return
        if path == "/api/state":
            self._json(ENGINE.state())
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()

        if path == "/api/record/start":
            ENGINE.start_record()
            self._json({"ok": True})
        elif path == "/api/record/stop":
            ENGINE.stop_record()
            self._json({"ok": True})
        elif path == "/api/play":
            ok = ENGINE.start_play()
            self._json({"ok": bool(ok), "error": "" if ok else "无法开始播放"})
        elif path == "/api/stop":
            ENGINE.stop()
            self._json({"ok": True})
        elif path == "/api/settings":
            with ENGINE.lock:
                if "speed" in body:
                    ENGINE.speed = max(0.1, min(50.0, float(body["speed"])))
                if "repeat" in body:
                    ENGINE.repeat = max(0, min(99999, int(body["repeat"])))
                if "gap" in body:
                    ENGINE.gap = max(0, min(600000, int(body["gap"])))
            self._json({"ok": True})
        elif path == "/api/actions":
            op = body.get("op")
            if op == "delay":
                ok, err = ENGINE.edit_delay(int(body.get("index", -1)), body.get("delay", 0))
            elif op == "delete":
                ok, err = ENGINE.delete_action(int(body.get("index", -1)))
            elif op == "clear":
                ok, err = ENGINE.clear_actions()
            elif op == "insert_wait":
                ok, err = ENGINE.insert_wait(int(body.get("index", 0)), body.get("delay", 200))
            elif op == "insert":
                ok, err = ENGINE.insert_action(int(body.get("index", 0)), body.get("action"), body.get("delay", 200))
            elif op == "delete_range":
                ok, err = ENGINE.delete_range(int(body.get("index", -1)), body.get("count", 1))
            elif op == "duplicate_range":
                ok, err = ENGINE.duplicate_range(int(body.get("index", -1)), body.get("count", 1))
            elif op == "duplicate":
                ok, err = ENGINE.duplicate_action(int(body.get("index", -1)))
            elif op == "up":
                ok, err = ENGINE.swap_action(int(body.get("index", -1)), -1)
            elif op == "down":
                ok, err = ENGINE.swap_action(int(body.get("index", -1)), 1)
            else:
                ok, err = False, "未知操作"
            if not ok:
                ENGINE.log(err, "warn")
            self._json({"ok": ok, "error": err})
        elif path == "/api/scripts/save":
            ok, err = ENGINE.save_script(body.get("name", ""))
            self._json({"ok": ok, "error": err})
        elif path == "/api/scripts/load":
            ok, err = ENGINE.load_script(body.get("name", ""))
            self._json({"ok": ok, "error": err})
        elif path == "/api/scripts/delete":
            ok, err = ENGINE.delete_script(body.get("name", ""))
            self._json({"ok": ok, "error": err})
        elif path == "/api/raw":
            ok, err = ENGINE.set_raw_mode(bool(body.get("on")))
            if not ok:
                ENGINE.log(err, "warn")
            self._json({"ok": ok, "error": err})
        elif path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
        else:
            self._json({"ok": False, "error": "not found"}, 404)


def pick_port(start=8765, tries=40):
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            try:
                s.close()
            except Exception:
                pass
            continue
    return 0


def existing_instance(start=8765, tries=6):
    """已有实例在跑时返回它的地址与权限，避免重复双击开出多个进程。"""
    for p in range(start, start + tries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", p, timeout=0.25)
            conn.request("GET", "/api/state")
            data = json.loads(conn.getresponse().read().decode("utf-8"))
            conn.close()
            if isinstance(data, dict) and data.get("hotkeys"):
                diag = data.get("diag") or {}
                return {
                    "url": "http://127.0.0.1:%d" % p,
                    "port": p,
                    "admin": bool(diag.get("admin")),
                    "mode": data.get("mode", "idle"),
                }
        except Exception:
            continue
    return None


def _take_over(running):
    """把权限不足的旧实例关掉，让当前的管理员进程接管。"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", running["port"], timeout=1.5)
        conn.request("POST", "/api/shutdown", b"{}", {"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()
    except Exception:
        pass
    for _ in range(20):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", running["port"], timeout=0.25)
            conn.request("GET", "/api/state")
            conn.getresponse()
            conn.close()
            time.sleep(0.2)
        except Exception:
            return True
    return False


def _focus_existing():
    """把已经开着的 NEUROLINK 窗口拉到最前面，成功返回 True。"""
    try:
        user32 = ctypes.windll.user32
        found = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd, _param):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if "NEUROLINK" in buf.value and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(proc(callback), 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def main():
    running = existing_instance()
    if running and "--force" not in sys.argv:
        # 自己是管理员、旧实例不是，且旧实例闲着 → 直接接管，免得看起来提权了实际没有
        if is_admin() and not running.get("admin") and running.get("mode") == "idle":
            print("发现普通权限的旧实例，正在接管…")
            if _take_over(running):
                running = None
        if running:
            print("已有实例在运行：%s" % running["url"])
            if "--no-open" not in sys.argv:
                if not _focus_existing():
                    try:
                        webbrowser.open(running["url"])
                    except Exception:
                        pass
            return
    port = pick_port()
    if not port:
        print("没有可用端口，退出")
        return
    PORT_HOLDER["port"] = port
    engine = ENGINE
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NeuraLink.Clicker")
    except Exception:
        pass
    mouse_listener = mouse.Listener(
        on_move=engine.on_move, on_click=engine.on_click, on_scroll=engine.on_scroll
    )
    key_listener = keyboard.Listener(on_press=lambda k: engine.on_key(k, True),
                                     on_release=lambda k: engine.on_key(k, False))
    mouse_listener.start()
    key_listener.start()
    engine.mouse_listener = mouse_listener
    engine.key_listener = key_listener
    try:
        engine.raw.start()          # 常驻监听原始位移，视角模式录制时才真正使用
    except Exception as exc:
        print("原始输入监听未启动：%s" % exc)
    engine.log("NEUROLINK 已就绪 · F9 录制 · F10 播放 · ESC 急停", "ok")
    if not is_admin():
        engine.log("当前为普通权限 · 若目标程序以管理员运行，模拟输入会被系统拦截，请关闭后重新以管理员身份启动", "warn")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d" % port
    print("=" * 56)
    print("  NEUROLINK CLICKER 正在运行")
    print("  控制台: %s" % url)
    print("  热键  : F9 录制 / F10 播放 / ESC 急停")
    print("  关闭窗口或按 Ctrl+C 退出")
    print("=" * 56)

    def cleanup():
        engine.stop_flag.set()
        try:
            mouse_listener.stop()
            key_listener.stop()
        except Exception:
            pass
        try:
            engine.raw.stop()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass

    want_window = "--browser" not in sys.argv and "--no-open" not in sys.argv
    if want_window:
        try:
            import webview  # noqa: F401
        except Exception as exc:
            print("窗口组件不可用，改用浏览器：%s" % exc)
            want_window = False

    if want_window:
        import webview
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        wa = work_area()
        wx = wa[0] + max(0, (wa[2] - 1320) // 2)
        wy = wa[1] + max(0, (wa[3] - 900) // 2)
        win = webview.create_window(
            "NEUROLINK · 连点器",
            url,
            width=1320,
            height=900,
            x=wx,
            y=wy,
            min_size=(980, 660),
            background_color="#04070b",
            frameless=True,          # 不要系统标题栏，界面自带顶部栏
            easy_drag=False,         # 拖动只认我们自己定义的顶部区域
            js_api=WindowBridge(),
        )
        try:
            webview.start(lambda: _style_window(win))
        except Exception as exc:
            print("窗口异常退出：%s" % exc)
        print("窗口已关闭，程序退出")
        cleanup()
        return

    if "--no-open" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            print(detail)
        except Exception:
            pass
        _msgbox(detail[-900:], "NEUROLINK 启动失败", 0x10)
        sys.exit(1)
