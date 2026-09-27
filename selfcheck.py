# -*- coding: utf-8 -*-
"""自检脚本：验证按键逻辑、音频覆盖播放、全局钩子是否真的在工作。

运行：
    python selfcheck.py             # 全部（会真的出声）
    python selfcheck.py --no-audio  # 跳过会出声的音频测试
"""

import ctypes
import os
import sys
import threading
import time
import unittest
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from audio_player import AudioPlayer                                   # noqa: E402
from key_logic import (KeyLogic, ACTION_PLAY, ACTION_TOGGLE,           # noqa: E402
                       VK_G, VK_CAPITAL, VK_CONTROL, VK_MENU, VK_LSHIFT,
                       VK_SPACE, VK_Z)
import keyhook                                                        # noqa: E402
from keyhook import (InputHook, MOUSEEVENTF_MIDDLEDOWN,                # noqa: E402
                     MOUSEEVENTF_MIDDLEUP, WM_LBUTTONDOWN, WM_LBUTTONUP,
                     WM_MBUTTONDOWN, WM_MBUTTONUP,
                     WM_RBUTTONDOWN, WM_RBUTTONUP)

VK_F = 0x46
VK_LBUTTON = 0x01

SOUNDS = {name: os.path.join(HERE, fn) for name, fn in
          (("g", "g.mp3"), ("capslock", "capslock.mp3"))}


class TestKeyLogic(unittest.TestCase):
    def make(self, enabled=True):
        return KeyLogic(enabled=enabled)

    def test_disabled_does_not_play(self):
        logic = self.make(enabled=False)
        self.assertEqual(logic.handle(VK_G, True), [])
        self.assertEqual(logic.handle(VK_CAPITAL, True), [])

    def test_g_plays_when_enabled(self):
        logic = self.make()
        self.assertEqual(logic.handle(VK_G, True), [(ACTION_PLAY, "g")])

    def test_capslock_plays_when_enabled(self):
        logic = self.make()
        self.assertEqual(logic.handle(VK_CAPITAL, True), [(ACTION_PLAY, "capslock")])

    def test_combo_toggles_and_does_not_play(self):
        logic = self.make(enabled=False)
        logic.handle(VK_MENU, True)
        actions = logic.handle(VK_Z, True)
        self.assertEqual(actions, [(ACTION_TOGGLE, True)])
        self.assertTrue(logic.enabled)
        # 再按一次组合键 -> 关
        logic.handle(VK_Z, False)
        self.assertEqual(logic.handle(VK_Z, True), [(ACTION_TOGGLE, False)])
        self.assertFalse(logic.enabled)

    def test_alt_plus_g_is_not_the_combo(self):
        """组合键已经改成 Alt+Z，Alt+G 不该再切换监听。"""
        logic = self.make(enabled=True)
        logic.handle(VK_MENU, True)
        self.assertEqual(logic.handle(VK_G, True), [(ACTION_PLAY, "g")])
        self.assertTrue(logic.enabled)

    def test_z_alone_does_not_toggle(self):
        """单独按 Z（没有 Alt）不切换监听。"""
        logic = self.make(enabled=True)
        self.assertEqual(logic.handle(VK_Z, True), [])
        self.assertTrue(logic.enabled)

    def test_auto_repeat_counts_once(self):
        logic = self.make()
        self.assertEqual(logic.handle(VK_G, True), [(ACTION_PLAY, "g")])
        # 长按时系统会连续发 KEYDOWN，没有 KEYUP
        self.assertEqual(logic.handle(VK_G, True), [])
        self.assertEqual(logic.handle(VK_G, True), [])
        # 松开后再按才算新的一次
        logic.handle(VK_G, False)
        self.assertEqual(logic.handle(VK_G, True), [(ACTION_PLAY, "g")])

    def test_modifier_alone_does_nothing(self):
        logic = self.make()
        self.assertEqual(logic.handle(VK_CONTROL, True), [])
        self.assertEqual(logic.handle(VK_MENU, True), [])
        self.assertEqual(logic.handle(VK_LSHIFT, True), [])

    def test_capslock_after_combo(self):
        """组合键切换后，不应该留下脏状态。"""
        logic = self.make(enabled=False)
        logic.handle(VK_MENU, True)
        logic.handle(VK_Z, True)              # toggle -> enabled
        logic.handle(VK_Z, False)
        logic.handle(VK_MENU, False)
        # 现在监听开着，单独按 G 应该播放
        self.assertEqual(logic.handle(VK_G, True), [(ACTION_PLAY, "g")])


class TestAudioPlayer(unittest.TestCase):
    def test_covers_previous(self):
        player = AudioPlayer(SOUNDS)
        self.assertTrue(player.play("g"), player.last_error)
        self.assertTrue(player.is_playing())
        # 紧接着播放另一个：前一个应被打断，新音效在播
        self.assertTrue(player.play("capslock"), player.last_error)
        self.assertTrue(player.is_playing())
        player.stop()
        self.assertFalse(player.is_playing())

    def test_missing_name(self):
        player = AudioPlayer(SOUNDS)
        self.assertFalse(player.play("nope"))
        self.assertIsNotNone(player.last_error)

    def test_missing_file(self):
        player = AudioPlayer({"x": os.path.join(HERE, "不存在.mp3")})
        self.assertFalse(player.play("x"))


class TestKeyboardHook(unittest.TestCase):
    """真正装一次全局钩子，并用合成的 Shift 按键确认它收到事件。

    用 Shift 是因为单按 Shift 不会输入任何字符、也没有系统副作用。
    """

    def test_hook_receives_keys(self):
        received = []
        lock = threading.Lock()

        def on_event(vk, is_down):
            with lock:
                received.append((vk, is_down))

        hook = InputHook(on_event)
        self.assertTrue(hook.start_and_wait(), hook.error)
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                           wintypes.DWORD, ctypes.c_void_p]
            user32.keybd_event.restype = None
            user32.keybd_event(0x10, 0, 0, None)          # VK_SHIFT down
            time.sleep(0.05)
            user32.keybd_event(0x10, 0, 0x0002, None)     # VK_SHIFT up
            time.sleep(0.25)
        finally:
            hook.stop()

        with lock:
            vks = {vk for vk, down in received if down}
        self.assertTrue(vks & {0x10, 0xA0}, "钩子没有收到合成的 Shift 按下事件：%r" % (vks,))


class _KeyboardWatcher(object):
    """只旁观、不拦截的键盘钩子，用来检查注入事件的原貌。

    复用 keyhook 里已经绑定好 argtypes 的 user32/kernel32
    （自己 WinDLL 容易漏设 restype，64 位下句柄会被截断，钩子装不上）。
    """

    def __init__(self):
        self.events = []
        self._proc = keyhook.HOOKPROC(self._on_key)
        self._thread = threading.Thread(target=self._run, name="watcher", daemon=True)
        self._ready = threading.Event()
        self._hook = None
        self._thread_id = None

    def _on_key(self, n_code, w_param, l_param):
        if n_code == 0:
            info = ctypes.cast(l_param, ctypes.POINTER(keyhook.KBDLLHOOKSTRUCT)).contents
            self.events.append((int(w_param), int(info.vkCode),
                                int(info.scanCode), int(info.dwExtraInfo or 0)))
        return keyhook._user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self):
        u32, k32 = keyhook._user32, keyhook._kernel32
        self._thread_id = int(k32.GetCurrentThreadId())
        self._hook = u32.SetWindowsHookExW(keyhook.WH_KEYBOARD_LL, self._proc,
                                           k32.GetModuleHandleW(None), 0)
        self._ready.set()
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    def __enter__(self):
        self._thread.start()
        self._ready.wait(2.0)
        return self

    def __exit__(self, *exc_info):
        u32 = keyhook._user32
        if self._thread_id:
            u32.PostThreadMessageW(self._thread_id, keyhook.WM_QUIT, 0, 0)
        if self._thread.is_alive():
            self._thread.join(1.0)
        if self._hook:
            u32.UnhookWindowsHookEx(self._hook)
            self._hook = None


class TestInjectedScancode(unittest.TestCase):
    """注入的按键必须带**硬件扫描码**——DirectInput / Raw Input 的游戏只认扫描码。

    用 LShift 来测，因为单独按 Shift 没有任何副作用。
    """

    def test_lshift_is_injected_with_scancode(self):
        with _KeyboardWatcher() as watcher:
            time.sleep(0.2)
            keyhook.send_key(VK_LSHIFT, True)
            time.sleep(0.15)
            keyhook.send_key(VK_LSHIFT, False)
            time.sleep(0.2)

        injected = [e for e in watcher.events if e[3] == keyhook.INJECT_MAGIC]
        self.assertTrue(injected, "旁观钩子没有收到任何注入的按键事件")
        downs = [e for e in injected if e[0] in (0x0100, 0x0104)]
        self.assertTrue(downs, "没有收到注入的按下事件")
        _message, vk, scan, _extra = downs[0]
        self.assertEqual(scan, 0x2A, "注入的 LShift 没带硬件扫描码 0x2A")
        self.assertEqual(vk, VK_LSHIFT, "注入的 LShift 的虚拟键码不对")

    def test_scancode_lookup(self):
        """会用到的目标键的扫描码（标准 PC 扫描码，与键盘布局无关）。"""
        self.assertEqual(keyhook.scancode_for(VK_LSHIFT), (0x2A, False))
        self.assertEqual(keyhook.scancode_for(VK_SPACE), (0x39, False))
        self.assertEqual(keyhook.scancode_for(VK_F), (0x21, False))


class TestRemap(unittest.TestCase):
    """重映射：按下 G 时系统应该收到 F，松开 G 时 F 也要跟着抬起。"""

    def test_g_is_injected_as_f(self):
        hook = InputHook(lambda vk, is_down: None)
        self.assertTrue(hook.start_and_wait(), hook.error)

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                       wintypes.DWORD, ctypes.c_void_p]
        user32.keybd_event.restype = None
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short

        pressed = released = None
        inject_error = None
        try:
            hook.update_remap({VK_G: VK_F})
            time.sleep(0.05)
            user32.keybd_event(VK_G, 0, 0, None)              # 按 G -> 应变成 F
            time.sleep(0.2)
            pressed = int(user32.GetAsyncKeyState(VK_F))
            user32.keybd_event(VK_G, 0, 0x0002, None)         # 松开 G -> F 跟着抬起
            time.sleep(0.2)
            released = int(user32.GetAsyncKeyState(VK_F))
            inject_error = hook.inject_error
        finally:
            hook.update_remap({})
            hook.stop()

        self.assertIsNone(inject_error, "注入线程抛出异常：%r" % (inject_error,))
        self.assertTrue(pressed & 0x8000, "按下 G 之后没有注入 F")
        self.assertFalse(released & 0x8000, "松开 G 之后 F 没有跟着抬起")


class TestMouseRemap(unittest.TestCase):
    """鼠标重映射：吞掉鼠标按键，改注入键盘键、或另一个鼠标键。"""

    def setUp(self):
        self.hook = InputHook(lambda vk, is_down: None)
        self.assertTrue(self.hook.start_and_wait(), self.hook.error)
        self.assertTrue(self.hook.mouse_installed, self.hook.error)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, wintypes.DWORD,
                                            wintypes.WPARAM]
        self.user32.mouse_event.restype = None
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short

    def tearDown(self):
        self.hook.update_remap()
        self.hook.stop()

    def _press_release(self, down_flag, up_flag, probe_vk):
        """注入一次鼠标按下/抬起，返回目标键在按下、抬起时的状态。"""
        self.user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.2)
        pressed = int(self.user32.GetAsyncKeyState(probe_vk))
        self.user32.mouse_event(up_flag, 0, 0, 0, 0)
        time.sleep(0.2)
        released = int(self.user32.GetAsyncKeyState(probe_vk))
        return pressed, released

    def test_right_button_is_injected_as_space(self):
        """右键 -> Space（用右键测，是怕万一钩子没生效，右键顶多弹个菜单）。"""
        self.hook.update_remap({}, {WM_RBUTTONDOWN: ("key", VK_SPACE),
                                    WM_RBUTTONUP: ("key", VK_SPACE)})
        time.sleep(0.05)
        pressed, released = self._press_release(0x0008, 0x0010, VK_SPACE)
        self.assertTrue(pressed & 0x8000, "按下鼠标右键之后没有注入 Space")
        self.assertFalse(released & 0x8000, "抬起鼠标右键之后 Space 没有跟着抬起")

    def test_middle_button_is_injected_as_left_click(self):
        """中键 -> 鼠标左键，而且注入的左键不会被「左键 -> LShift」再改写一遍。"""
        self.hook.update_remap({}, {WM_LBUTTONDOWN: ("key", VK_LSHIFT),
                                    WM_LBUTTONUP: ("key", VK_LSHIFT),
                                    WM_MBUTTONDOWN: ("mouse", "left"),
                                    WM_MBUTTONUP: ("mouse", "left")})
        time.sleep(0.05)
        pressed, released = self._press_release(MOUSEEVENTF_MIDDLEDOWN,
                                                MOUSEEVENTF_MIDDLEUP, VK_LBUTTON)
        shift = int(self.user32.GetAsyncKeyState(VK_LSHIFT))
        self.assertTrue(pressed & 0x8000, "按下鼠标中键之后没有注入鼠标左键")
        self.assertFalse(released & 0x8000, "抬起鼠标中键之后左键没有跟着抬起")
        self.assertFalse(shift & 0x8000,
                         "注入的左键被「左键 -> LShift」又改写了一遍（防递归标记失效）")


class TestEndToEnd(unittest.TestCase):
    """真正跑起 GUI：安装钩子 -> 合成一次 G -> 检查音效被触发。"""

    def test_gui_plays_on_g(self):
        import tkinter as tk
        import key_listener as kl

        root = tk.Tk()
        root.withdraw()
        app = kl.App(root)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        try:
            app.start()
            self.assertTrue(app.hook.installed, app.hook.error)
            self.assertTrue(app.logic.enabled)

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                           wintypes.DWORD, ctypes.c_void_p]
            user32.keybd_event.restype = None
            user32.keybd_event(VK_G, 0, 0, None)          # 按下 G
            user32.keybd_event(VK_G, 0, 0x0002, None)     # 抬起 G

            deadline = time.time() + 1.5
            while time.time() < deadline and app.last_label.cget("text") == "—":
                root.update()
                time.sleep(0.01)

            self.assertNotEqual(app.last_label.cget("text"), "—")
            self.assertIn("G", app.last_label.cget("text"))

            # 勾选「按键改写」后，钩子应该拿到映射表
            app.remap_var.set(True)
            deadline = time.time() + 1.0
            while time.time() < deadline and not app.hook.remap:
                root.update()
                time.sleep(0.01)
            self.assertEqual(app.hook.remap, kl.REMAP_PAIRS)
            app.remap_var.set(False)

            # 勾选「鼠标改写」后，钩子同样应该拿到映射表
            app.mouse_var.set(True)
            deadline = time.time() + 1.0
            while time.time() < deadline and not app.hook.mouse_remap:
                root.update()
                time.sleep(0.01)
            self.assertEqual(app.hook.mouse_remap, kl.MOUSE_REMAP)
            app.mouse_var.set(False)

            # Alt+Z 应该能关掉监听，再按一次能开回来
            for expected in (False, True):
                user32.keybd_event(VK_MENU, 0, 0, None)
                user32.keybd_event(VK_Z, 0, 0, None)
                user32.keybd_event(VK_Z, 0, 0x0002, None)
                user32.keybd_event(VK_MENU, 0, 0x0002, None)
                deadline = time.time() + 1.0
                while time.time() < deadline and app.logic.enabled is not expected:
                    root.update()
                    time.sleep(0.01)
                self.assertIs(app.logic.enabled, expected,
                              "Alt+Z 没有按预期切换监听（期望 %s）" % expected)
        finally:
            app.on_close()


def main():
    argv = sys.argv[1:]
    if "--no-audio" in argv:
        for name in ("TestAudioPlayer", "TestEndToEnd"):
            globals().pop(name, None)
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == "__main__":
    main()
