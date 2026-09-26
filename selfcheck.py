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
                       VK_G, VK_CAPITAL, VK_CONTROL, VK_MENU, VK_LSHIFT)
from keyhook import KeyboardHook                                       # noqa: E402

VK_F = 0x46

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
        logic.handle(VK_CONTROL, True)
        logic.handle(VK_MENU, True)
        actions = logic.handle(VK_G, True)
        self.assertEqual(actions, [(ACTION_TOGGLE, True)])
        self.assertTrue(logic.enabled)
        # 再按一次组合键 -> 关
        logic.handle(VK_G, False)
        self.assertEqual(logic.handle(VK_G, True), [(ACTION_TOGGLE, False)])
        self.assertFalse(logic.enabled)

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
        """组合键切换后，同一个 G 的 KEYUP 不应造成脏状态。"""
        logic = self.make(enabled=False)
        logic.handle(VK_CONTROL, True)
        logic.handle(VK_MENU, True)
        logic.handle(VK_G, True)              # toggle -> enabled
        logic.handle(VK_G, False)
        logic.handle(VK_MENU, False)
        logic.handle(VK_CONTROL, False)
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

        hook = KeyboardHook(on_event)
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


class TestRemap(unittest.TestCase):
    """重映射：按下 G 时系统应该收到 F，松开 G 时 F 也要跟着抬起。"""

    def test_g_is_injected_as_f(self):
        hook = KeyboardHook(lambda vk, is_down: None)
        self.assertTrue(hook.start_and_wait(), hook.error)

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                       wintypes.DWORD, ctypes.c_void_p]
        user32.keybd_event.restype = None
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short

        pressed = released = None
        try:
            hook.update_remap({VK_G: VK_F})
            time.sleep(0.05)
            user32.keybd_event(VK_G, 0, 0, None)              # 按 G -> 应变成 F
            time.sleep(0.2)
            pressed = int(user32.GetAsyncKeyState(VK_F))
            user32.keybd_event(VK_G, 0, 0x0002, None)         # 松开 G -> F 跟着抬起
            time.sleep(0.2)
            released = int(user32.GetAsyncKeyState(VK_F))
        finally:
            hook.update_remap({})
            hook.stop()

        self.assertTrue(pressed & 0x8000, "按下 G 之后没有注入 F")
        self.assertFalse(released & 0x8000, "松开 G 之后 F 没有跟着抬起")


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
