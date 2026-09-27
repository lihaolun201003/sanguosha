"""轻量信息提示（Toast，Presentation System 2.0）。

**"不是你的回合" / "目标无效" / "等待其他玩家响应" 这一类信息不该用模态框。**
模态框会打断操作流（要点一下才消失），而这几十种提示都是"顺嘴告知一句"。
Toast 从屏幕上方滑入、停留、淡出，不拦任何点击。

# 与其它提示层的分工（不要混用）

    Toast            普通信息，不要求回应，不拦点击        ← 本模块
    StoryBanner      结算**结论**（"跳过出牌阶段"），由演出队列排序
    ModalPanel       必须做选择（选牌 / 选目标 / 重开）
    Tooltip          鼠标悬停才出现，跟着鼠标走

# 合并

同一个 key 的 toast 连续推入时**合并刷新**（"等待其他玩家"这类会在每帧条件
变化时产生，不合并会刷屏）。``key`` 不给就用文案本身当 key。
"""

import pygame

from . import anim_config
from . import theme
from .widgets import ellipsize_text, wrap_text

#: 同时显示的条数上限（多了会挡住牌桌）。
MAX_VISIBLE = 3

TONES = {
    "info": (theme.GOLD_BRIGHT, theme.PANEL_DEEP),
    "warning": (theme.TARGET_YELLOW, (58, 44, 26)),
    "danger": (theme.DANGER, (52, 28, 24)),
    "success": (theme.HEAL, (28, 48, 40)),
    "waiting": (theme.TEXT_DIM, theme.PANEL_DEEP),
}

DEFAULT_DURATION = 1.9


class Toast:
    __slots__ = ("text", "tone", "key", "life", "total", "detail")

    def __init__(self, text, tone="info", key=None, detail="", duration=None):
        self.text = str(text or "")
        self.detail = str(detail or "")
        self.tone = str(tone or "info")
        self.key = key if key is not None else self.text
        self.total = float(DEFAULT_DURATION if duration is None else duration)
        self.life = self.total

    @property
    def alpha(self):
        """进入 25% 淡入、结束 30% 淡出；中间恒为不透明。"""

        ratio = max(0.0, min(1.0, self.life / max(0.01, self.total)))
        if ratio > 0.75:
            return int(255 * (1.0 - ratio) / 0.25)
        if ratio < 0.30:
            return int(255 * ratio / 0.30)
        return 255

    @property
    def offset(self):
        """从上方滑入：剩余时间越多越靠下（已经在位）。"""

        ratio = max(0.0, min(1.0, self.life / max(0.01, self.total)))
        if ratio > 0.75:
            return int(-26 * (ratio - 0.75) / 0.25)
        return 0

    def describe(self):
        return self.text


class ToastManager:
    """屏幕上方的一列轻提示。"""

    def __init__(self):
        self.items = []
        self._index = {}

    # ---- 生产 ----

    def push(self, text, *, tone="info", key=None, detail="", duration=None):
        """推入一条；同 key 的旧条目会被**替换并重新计时**（不刷屏）。"""

        text = str(text or "").strip()
        if not text:
            return None
        toast = Toast(text, tone, key, detail=detail, duration=duration)
        existing = self._index.get(toast.key)
        if existing is not None and existing in self.items:
            existing.text = toast.text
            existing.detail = toast.detail
            existing.tone = toast.tone
            existing.total = toast.total
            existing.life = toast.total
            return existing
        self.items.append(toast)
        self._index[toast.key] = toast
        if len(self.items) > MAX_VISIBLE:
            dropped = self.items[:-MAX_VISIBLE]
            self.items = self.items[-MAX_VISIBLE:]
            for item in dropped:
                self._index.pop(item.key, None)
        return toast

    def dismiss(self, key):
        toast = self._index.pop(key, None)
        if toast is not None and toast in self.items:
            self.items.remove(toast)
        return toast

    # ---- 查询 ----

    @property
    def active(self):
        return bool(self.items)

    def texts(self):
        return [item.text for item in self.items]

    def describe(self):
        return "、".join(self.texts())

    # ---- 推进 ----

    def update(self, dt):
        step = max(0.0, float(dt))
        for item in list(self.items):
            item.life -= step
            if item.life <= 0:
                self.items.remove(item)
                if self._index.get(item.key) is item:
                    self._index.pop(item.key, None)

    def finish_all(self):
        """跳过演出：立刻淡出全部（它们只是提示，没有规则含义）。"""

        for item in self.items:
            item.life = 0.0
        self.update(0.0)

    def clear(self):
        self.items.clear()
        self._index.clear()

    # ---- 绘制 ----

    def draw(self, surface, metrics):
        if not self.items:
            return 0
        fonts = metrics.fonts
        title_font = fonts.get("normal")
        body_font = fonts.get("small")
        max_width = min(metrics.px(680), int(metrics.screen_w * 0.62))
        y = metrics.central.y + metrics.px(2)
        drawn = 0
        for item in self.items[:MAX_VISIBLE]:
            alpha = item.alpha
            if alpha <= 0:
                continue
            color, fill = TONES.get(item.tone, TONES["info"])
            title = title_font.render(
                ellipsize_text(item.text, title_font, max_width), True, color)
            body = None
            if item.detail:
                lines = wrap_text(item.detail, body_font, max_width, max_lines=2)
                if lines:
                    body = [body_font.render(line, True, theme.TEXT) for line in lines]
            width = title.get_width()
            if body:
                width = max(width, max(line.get_width() for line in body))
            height = title.get_height() + (sum(
                line.get_height() + metrics.px(2) for line in body) if body else 0)
            plate = pygame.Surface(
                (width + metrics.px(40), height + metrics.px(18)), pygame.SRCALPHA)
            bounds = plate.get_rect()
            pygame.draw.rect(plate, (*fill, 232), bounds, border_radius=metrics.px(10))
            pygame.draw.rect(plate, (*color, 200), bounds, 2,
                             border_radius=metrics.px(10))
            plate.blit(title, title.get_rect(
                midtop=(bounds.centerx, metrics.px(9))))
            if body:
                offset = metrics.px(9) + title.get_height()
                for line in body:
                    plate.blit(line, line.get_rect(
                        midtop=(bounds.centerx, offset)))
                    offset += line.get_height() + metrics.px(2)
            plate.set_alpha(alpha)
            rect = plate.get_rect(midtop=(metrics.screen_w // 2, y + item.offset))
            surface.blit(plate, rect)
            y = rect.bottom + metrics.px(8)
            drawn += 1
        return drawn


#: 全局一份：供没有 Renderer 引用的调用点（联机场景 / 控制器）推提示。
_global = ToastManager()


def global_manager():
    return _global


def push(text, *, tone="info", key=None, detail="", duration=None):
    """推一条到全局管理器（Renderer 会在每帧合并它，见 ``Renderer.drain_toasts``）。"""

    return _global.push(text, tone=tone, key=key, detail=detail, duration=duration)


def hover_seconds():
    return anim_config.duration("hover")
