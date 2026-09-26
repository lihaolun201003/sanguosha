"""局内表现速度控件：角落里的一个小 [-] 值 [+] 部件。

它只读写"这台机器自己的表现速度"，**从不触碰规则**：

* 座主（本机权威）读写 ``Game.speed``——它只喂给 ``Game.update`` 里的
  动作队列，规则层读不到它；
* 联网客户端读写只读视图自己的 ``ViewSpeed``；

两者互不通信：速度是**本地 UI 设置**，不进权威状态、不进网络快照、
不进 revision、不进任何 DecisionRequest。房主调慢只让房主自己的动画变慢，
游客那边照旧（反之亦然）。
"""

import pygame

from . import layout
from . import theme
from .widgets import Button, draw_panel


class SpeedControl:
    """Compact speed stepper drawn in the top-left corner of the table."""

    def __init__(self, metrics=None):
        self.minus = Button(pygame.Rect(0, 0, 10, 10), "−", kind="secondary", font="small")
        self.plus = Button(pygame.Rect(0, 0, 10, 10), "＋", kind="secondary", font="small")
        self.rect = pygame.Rect(0, 0, 10, 10)
        self.sync_layout(metrics)

    def sync_layout(self, metrics=None):
        """Recompute for the current screen (called on resize / fullscreen)."""

        if metrics is None:
            metrics = layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.rect = metrics.speed_control
        pad = metrics.px(12)
        button_w = metrics.px(56)
        button_h = metrics.px(32)
        y = self.rect.bottom - button_h - metrics.px(10)
        self.minus.rect = pygame.Rect(self.rect.x + pad, y, button_w, button_h)
        self.plus.rect = pygame.Rect(self.rect.right - pad - button_w, y, button_w, button_h)
        self.metrics = metrics
        return self

    def value_rect(self):
        return pygame.Rect(
            self.minus.rect.right + self.metrics.px(6),
            self.minus.rect.y,
            self.plus.rect.x - self.minus.rect.right - self.metrics.px(12),
            self.minus.rect.height,
        )

    def sync(self, game):
        """Enable/disable the steppers at the ends of the range."""

        self.minus.enabled = game.speed_index() > 0
        self.plus.enabled = game.speed_index() < len(game.SPEED_STEPS) - 1

    #: 档位的中文名字。座主与客户端读的是同一份表，界面文案不会两边不一致。
    LABELS = {0.4: "很慢", 0.55: "慢", 0.75: "稍慢", 1.0: "正常", 1.5: "快", 2.0: "很快"}

    @classmethod
    def label_for(cls, value):
        return cls.LABELS.get(float(value), "")

    def hit(self, position, game):
        self.sync(game)
        if self.minus.enabled and self.minus.contains(position):
            return "slower"
        if self.plus.enabled and self.plus.contains(position):
            return "faster"
        return None

    def draw(self, surface, game, mouse_pos=None):
        metrics = self.metrics
        fonts = metrics.fonts
        self.sync(game)

        draw_panel(
            surface,
            self.rect,
            fill=theme.PANEL_DEEP,
            border=theme.GOLD_DIM,
            border_width=2,
            radius=metrics.px(10),
        )

        label = fonts.get("small").render("动画速度", True, theme.TEXT_DIM)
        surface.blit(label, label.get_rect(midtop=(self.rect.centerx, self.rect.y + metrics.px(6))))

        value_rect = self.value_rect()
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, value_rect, border_radius=metrics.px(6))
        pygame.draw.rect(surface, theme.GOLD_DIM, value_rect, 1, border_radius=metrics.px(6))
        raw_label = game.speed_label
        label_text = self.label_for(getattr(game, "speed", 0) or 0) or raw_label
        value = fonts.get("normal").render(label_text, True, theme.GOLD_BRIGHT)
        surface.blit(value, value.get_rect(center=value_rect.center))

        self.minus.draw(surface, fonts, mouse_pos)
        self.plus.draw(surface, fonts, mouse_pos)
