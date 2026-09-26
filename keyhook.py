# -*- coding: utf-8 -*-
"""Windows 全局低级键盘钩子（WH_KEYBOARD_LL），纯 ctypes，无第三方依赖。

在后台线程里安装钩子并跑自己的消息循环；回调只做最轻的事
（把 ``(vkCode, is_down)`` 交给 on_event），再立刻返回。

除了「旁听」按键，还支持两件事（都由主线程通过属性/方法设置）：

- ``swallow_capslock``：吞掉 CapsLock，不让系统切换大小写
- ``update_remap()``：按键重映射——吞掉源按键，再用 SendInput 注入目标按键
"""

import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

VK_CAPITAL = 0x14

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

# 给本程序注入的按键打个记号；钩子里看到就原样放行，避免自我递归
INJECT_MAGIC = 0x524D4150            # 'RMAP'

_DOWN_MSGS = (WM_KEYDOWN, WM_SYSKEYDOWN)
_UP_MSGS = (WM_KEYUP, WM_SYSKEYUP)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = wintypes.LPARAM
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def send_key(vk, is_down):
    """用 SendInput 注入一次按键（带魔数标记，方便钩子识别并放行）。"""
    events = (INPUT * 1)()
    events[0].type = INPUT_KEYBOARD
    events[0].ki.wVk = vk
    events[0].ki.wScan = 0
    events[0].ki.dwFlags = 0 if is_down else KEYEVENTF_KEYUP
    events[0].ki.time = 0
    events[0].ki.dwExtraInfo = INJECT_MAGIC
    return _user32.SendInput(1, events, ctypes.sizeof(INPUT))


class KeyboardHook(threading.Thread):
    """全局键盘钩子线程。

    参数 ``on_event(vk, is_down)`` 在钩子线程里被调用，必须非常快，
    通常只是往 queue 里塞一个元组。它拿到的是**改写前**的原始按键，
    所以重映射不会影响本程序自己的组合键判断。
    """

    def __init__(self, on_event):
        super(KeyboardHook, self).__init__(name="keyboard-hook", daemon=True)
        self._on_event = on_event
        self._proc = None          # 必须保持引用，否则回调会被 GC 掉
        self._hook = None
        self._thread_id = None
        self._ready = threading.Event()

        self.installed = False
        self.error = None
        # 由主线程设置：True 时吞掉 CapsLock，不让系统切换大小写
        self.swallow_capslock = False
        # 由主线程设置：{源 vk: 目标 vk}，整体替换，读的时候天然是原子的
        self.remap = {}
        # 当前「已注入按下、还没注入抬起」的 {源 vk: 目标 vk}
        self._injected = {}
        self._inject_lock = threading.Lock()

    # -- 生命周期 -----------------------------------------------------------
    def start_and_wait(self, timeout=3.0):
        """启动并等待钩子装好，返回是否成功。"""
        super(KeyboardHook, self).start()
        self._ready.wait(timeout)
        return self.installed

    def run(self):
        self._thread_id = int(_kernel32.GetCurrentThreadId())
        self._proc = HOOKPROC(self._callback)

        module = _kernel32.GetModuleHandleW(None)
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, module, 0)
        if not self._hook:
            self.error = "SetWindowsHookExW 失败（错误码 %d）" % ctypes.get_last_error()
            self.installed = False
            self._ready.set()
            return

        self.installed = True
        self._ready.set()

        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self, timeout=2.0):
        """退出消息循环并卸载钩子。"""
        self.update_remap({})          # 万一还有键「按着」，先补发抬起
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self.is_alive():
            self.join(timeout)
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self.installed = False

    # -- 重映射 -------------------------------------------------------------
    def update_remap(self, mapping):
        """更新重映射表。

        对「不再被映射、但注入的按下还没抬起」的键补发一次抬起，
        否则中途取消勾选会把那个键永久卡在按下状态。
        """
        new = dict(mapping or {})
        with self._inject_lock:
            for source in list(self._injected):
                target = self._injected[source]
                if new.get(source) != target:
                    send_key(target, False)
                    del self._injected[source]
        self.remap = new

    # -- 回调 ---------------------------------------------------------------
    def _callback(self, n_code, w_param, l_param):
        # n_code < 0 时必须原样传递，不处理
        if n_code == 0:
            message = int(w_param)
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents

            # 自己注入的按键：直接放行，避免无限递归
            if int(info.dwExtraInfo or 0) == INJECT_MAGIC:
                return _user32.CallNextHookEx(None, n_code, w_param, l_param)

            vk = int(info.vkCode)
            if message in _DOWN_MSGS or message in _UP_MSGS:
                is_down = message in _DOWN_MSGS
                # 先按「改写前」的原始按键通知上层（音效、组合键都看这个）
                self._on_event(vk, is_down)

                target = self.remap.get(vk)
                if target is not None:
                    self._inject(vk, target, is_down)
                    return 1                      # 吞掉原始按键
                if vk == VK_CAPITAL and self.swallow_capslock:
                    return 1

        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _inject(self, source, target, is_down):
        send_key(target, is_down)
        with self._inject_lock:
            if is_down:
                self._injected[source] = target
            else:
                self._injected.pop(source, None)
