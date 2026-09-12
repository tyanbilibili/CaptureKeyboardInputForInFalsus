# -*- coding: utf-8 -*-
"""按键判定逻辑。

这一层刻意不碰任何 Windows API，只做「原始按键事件 -> 动作」的翻译，
方便单元测试。

- 组合键 Ctrl + Alt + G : 开启 / 关闭监听
- 监听开启时：
    G         -> 播放 g.mp3
    CapsLock  -> 播放 capslock.mp3
- 长按产生的自动重复只算一次。
"""

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_CAPITAL = 0x14       # CapsLock
VK_G = 0x47
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

CTRL_VKS = frozenset((VK_CONTROL, VK_LCONTROL, VK_RCONTROL))
ALT_VKS = frozenset((VK_MENU, VK_LMENU, VK_RMENU))
SHIFT_VKS = frozenset((VK_SHIFT, VK_LSHIFT, VK_RSHIFT))
_MODIFIER_VKS = CTRL_VKS | ALT_VKS | SHIFT_VKS

# 动作种类
ACTION_TOGGLE = "toggle"        # payload: 新的监听状态 (True/False)
ACTION_PLAY = "play"            # payload: 音效名 ("g" / "capslock")


class KeyLogic(object):
    """把 (vk, is_down) 事件流翻译成动作列表。"""

    def __init__(self, enabled=False, combos=None):
        # combos: 组合键里除修饰键外的那个键，默认 {G}
        self.enabled = bool(enabled)
        self.combos = set(combos) if combos else {VK_G}
        self._pressed = set()

    # -- 查询 ---------------------------------------------------------------
    def _ctrl_down(self):
        return any(vk in self._pressed for vk in CTRL_VKS)

    def _alt_down(self):
        return any(vk in self._pressed for vk in ALT_VKS)

    # -- 主入口 -------------------------------------------------------------
    def handle(self, vk, is_down):
        """喂入一个按键事件，返回动作列表（可能为空）。"""
        if is_down:
            return self._on_key_down(vk)
        self._pressed.discard(vk)
        return []

    def _on_key_down(self, vk):
        actions = []
        if vk in self._pressed:
            # 长按自动重复：同一次按下只处理一遍
            return actions
        self._pressed.add(vk)

        if vk in _MODIFIER_VKS:
            # 修饰键本身不触发任何动作，只记录状态
            return actions

        if self._ctrl_down() and self._alt_down() and vk in self.combos:
            self.enabled = not self.enabled
            actions.append((ACTION_TOGGLE, self.enabled))
            return actions

        if not self.enabled:
            return actions

        if vk == VK_G:
            actions.append((ACTION_PLAY, "g"))
        elif vk == VK_CAPITAL:
            actions.append((ACTION_PLAY, "capslock"))
        return actions

    def reset(self):
        """清空「当前按下的键」，用于焦点/会话切换后避免脏状态。"""
        self._pressed.clear()
