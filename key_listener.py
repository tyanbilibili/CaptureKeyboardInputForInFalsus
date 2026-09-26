# -*- coding: utf-8 -*-
"""监听按键

组合键 Ctrl + Alt + G 开启 / 关闭监听。
监听开启时：
    G         -> 播放 g.mp3
    CapsLock  -> 播放 capslock.mp3
短时间连续按下时，后按下的音效会打断并覆盖前一个。

运行：双击 启动.bat，或在本目录执行  python key_listener.py
"""

import ctypes
import os
import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from audio_player import AudioPlayer                                  # noqa: E402
from key_logic import (KeyLogic, ACTION_TOGGLE, ACTION_PLAY,          # noqa: E402
                       VK_CAPITAL, VK_G, VK_LSHIFT)
from keyhook import KeyboardHook                                      # noqa: E402

# 音效名 -> 文件名
SOUNDS = {
    "g": "g.mp3",
    "capslock": "capslock.mp3",
}
SOUND_LABEL = {
    "g": "G",
    "capslock": "CapsLock",
}

VK_F = 0x46

# 勾选「按键改写」后的映射：CapsLock -> LShift，G -> F
REMAP_PAIRS = {
    VK_CAPITAL: VK_LSHIFT,
    VK_G: VK_F,
}

COMBO_TEXT = "Ctrl + Alt + G"
FONT = "Microsoft YaHei UI"

BG = "#eef0f4"
CARD = "#ffffff"
TEXT = "#1f2430"
MUTED = "#7a8290"
GREEN = "#22c55e"
GRAY = "#aab0ba"

MUTEX_NAME = "Local\\ReasonixKeyListener_{B4E1F0A2-8C3D-4F57-9E6A-2D5C7B1A0F33}"


def enable_dpi_awareness():
    """让窗口在高分屏上不发虚。"""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.WinDLL("user32").SetProcessDPIAware()
    except Exception:
        pass


def acquire_single_instance():
    """保证同时只跑一个实例，返回 True 表示拿到锁。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != 183       # ERROR_ALREADY_EXISTS


class App(object):
    def __init__(self, root):
        self.root = root
        self.audio = AudioPlayer({name: os.path.join(HERE, fn) for name, fn in SOUNDS.items()})
        self.logic = KeyLogic(enabled=True)     # 打开程序即开始监听
        self.events = queue.Queue()
        # 钩子线程只往队列里丢 (vk, is_down)，由主线程统一处理
        self.hook = KeyboardHook(lambda vk, is_down: self.events.put((vk, is_down)))
        self._after_id = None
        self._build_ui()
        self._refresh_status()

    # -- 界面 ---------------------------------------------------------------
    def _build_ui(self):
        root = self.root
        root.title("监听按键")
        root.resizable(False, False)
        root.configure(bg=BG)

        card = tk.Frame(root, bg=CARD, padx=0, pady=0)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        # 状态行
        status = tk.Frame(card, bg=CARD)
        status.pack(fill="x", padx=18, pady=(18, 4))
        self.dot = tk.Canvas(status, width=20, height=20, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_item = self.dot.create_oval(3, 3, 17, 17, fill=GREEN, outline="")
        self.status_label = tk.Label(status, text="监听中", bg=CARD, fg=TEXT,
                                     font=(FONT, 17, "bold"))
        self.status_label.pack(side="left", padx=(8, 0))

        self.hint = tk.Label(card, text="按 %s 可随时开启 / 关闭" % COMBO_TEXT,
                             bg=CARD, fg=MUTED, font=(FONT, 9))
        self.hint.pack(anchor="w", padx=19, pady=(0, 12))

        self._separator(card)

        # 说明表
        info = tk.Frame(card, bg=CARD)
        info.pack(fill="x", padx=18, pady=12)
        self._info_row(info, 0, "开关组合键", COMBO_TEXT)
        self._info_row(info, 1, "G 键音效", "g.mp3")
        self._info_row(info, 2, "CapsLock 音效", "capslock.mp3")

        self._separator(card)

        # 最近触发
        last = tk.Frame(card, bg=CARD)
        last.pack(fill="x", padx=18, pady=11)
        tk.Label(last, text="最近触发", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self.last_label = tk.Label(last, text="—", bg=CARD, fg=TEXT, font=(FONT, 10))
        self.last_label.pack(side="right")

        # 按钮
        self.toggle_btn = tk.Button(card, text="暂停监听", command=self.toggle_manual,
                                    font=(FONT, 11), relief="flat", cursor="hand2",
                                    bg="#2f6df6", fg="white",
                                    activebackground="#2358cc", activeforeground="white",
                                    padx=10, pady=8)
        self.toggle_btn.pack(fill="x", padx=18, pady=(4, 10))

        # 选项
        self.swallow_var = tk.BooleanVar(value=False)
        self.swallow_chk = tk.Checkbutton(card, text="屏蔽 CapsLock 本身的大小写切换",
                                          variable=self.swallow_var, bg=CARD, fg=TEXT,
                                          activebackground=CARD, font=(FONT, 9),
                                          selectcolor=CARD, anchor="w", cursor="hand2")
        self.swallow_chk.pack(fill="x", padx=14, pady=(0, 2))

        self.remap_var = tk.BooleanVar(value=False)
        self.remap_chk = tk.Checkbutton(card, text="按键改写：CapsLock→LShift、G→F",
                                        variable=self.remap_var, bg=CARD, fg=TEXT,
                                        activebackground=CARD, font=(FONT, 9),
                                        selectcolor=CARD, anchor="w", cursor="hand2",
                                        command=self._sync_option_states)
        self.remap_chk.pack(fill="x", padx=14, pady=(0, 2))

        tk.Label(card, text="勾选后按下这两个键，会先把按键改写成目标键再送给系统"
                            "（仅在「监听中」生效）。",
                 bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w", justify="left",
                 wraplength=290).pack(fill="x", padx=19, pady=(0, 14))

    def _separator(self, parent):
        tk.Frame(parent, bg="#e3e6ec", height=1).pack(fill="x", padx=18)

    def _info_row(self, parent, row, title, value):
        parent.columnconfigure(1, weight=1)
        tk.Label(parent, text=title, bg=CARD, fg=MUTED, font=(FONT, 9),
                 anchor="w").grid(row=row, column=0, sticky="w", pady=1)
        tk.Label(parent, text=value, bg=CARD, fg=TEXT, font=(FONT, 9),
                 anchor="e").grid(row=row, column=1, sticky="e", pady=1)

    # -- 状态刷新 -----------------------------------------------------------
    def _refresh_status(self):
        if self.logic.enabled:
            self.dot.itemconfigure(self.dot_item, fill=GREEN)
            self.status_label.configure(text="监听中")
            self.toggle_btn.configure(text="暂停监听")
        else:
            self.dot.itemconfigure(self.dot_item, fill=GRAY)
            self.status_label.configure(text="已暂停")
            self.toggle_btn.configure(text="开始监听")

    def _show_last(self, name, ok):
        stamp = time.strftime("%H:%M:%S")
        text = "%s  %s" % (stamp, SOUND_LABEL.get(name, name))
        if not ok:
            text += "  (播放失败)"
        self.last_label.configure(text=text)

    def _sync_option_states(self):
        """按键改写开启时 CapsLock 已被改写，屏蔽大小写切换就没意义了。"""
        self.swallow_chk.configure(state="disabled" if self.remap_var.get() else "normal")

    # -- 动作 ---------------------------------------------------------------
    def toggle_manual(self):
        self.logic.enabled = not self.logic.enabled
        self._refresh_status()

    def _apply(self, action, payload):
        if action == ACTION_TOGGLE:
            self._refresh_status()
        elif action == ACTION_PLAY:
            self._show_last(payload, self.audio.play(payload))

    def _poll(self):
        handled = False
        while True:
            try:
                vk, is_down = self.events.get_nowait()
            except queue.Empty:
                break
            handled = True
            for action, payload in self.logic.handle(vk, is_down):
                self._apply(action, payload)

        # 让钩子知道现在要不要吞掉 CapsLock、要不要改写按键
        self.hook.swallow_capslock = bool(self.logic.enabled and self.swallow_var.get())
        remap = REMAP_PAIRS if (self.logic.enabled and self.remap_var.get()) else {}
        if remap != self.hook.remap:
            self.hook.update_remap(remap)
        self.audio.cleanup()
        if handled:
            self._refresh_status()
        self._after_id = self.root.after(15, self._poll)

    # -- 生命周期 -----------------------------------------------------------
    def start(self):
        if not self.hook.start_and_wait():
            self.status_label.configure(text="钩子安装失败")
            messagebox.showerror("监听按键", "无法安装键盘钩子：\n%s" % self.hook.error)
            return
        self._poll()

    def on_close(self):
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.audio.stop()
        self.hook.stop()
        self.root.destroy()


def main():
    enable_dpi_awareness()
    smoke = "--smoke" in sys.argv        # 自检用：跑一小会儿后自动退出

    root = tk.Tk()
    root.withdraw()

    if not acquire_single_instance():
        messagebox.showinfo("监听按键", "程序已经在运行了。\n请看任务栏里的窗口。")
        root.destroy()
        return

    root.deiconify()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    _center(root)
    root.after(60, app.start)

    if smoke:
        def report():
            print("hook installed:", app.hook.installed, "| error:", app.hook.error)
            print("status/button  :", app.status_label.cget("text"), "/",
                  app.toggle_btn.cget("text"))
            app.toggle_manual()
            print("after toggle   :", app.status_label.cget("text"), "/",
                  app.toggle_btn.cget("text"))
            app.toggle_manual()

        root.after(900, report)
        root.after(1800, app.on_close)

    root.mainloop()

    if smoke:
        print("SMOKE OK")


def _center(win):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if w <= 1:
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry("+%d+%d" % (x, y))


if __name__ == "__main__":
    main()
