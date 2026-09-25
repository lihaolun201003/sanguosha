"""CardActionPicker：一张 / 一组实体牌有多个用途时，让玩家自己选。

与 SkillPicker（选择要发动的 ACTIVE 技能）语义不同，因此是两个组件；
它们复用同一套 Widgets 与视觉规范。面板内容完全来自
``CardActionOption``：label / detail / enabled / disabled_reason。
"""

import pygame

from . import layout, theme
from .widgets import Button, draw_panel, ellipsize_text

PICKER_WIDTH = 880
PICKER_ROW_HEIGHT = 96
PICKER_HEADER = 100
PICKER_FOOTER = 88


class CardActionPicker:

    def __init__(self):
        self.rects = []
        self.cancel_button = Button(pygame.Rect(0, 0, 10, 10), "取消", kind="ghost", font="normal")
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.metrics = None
        self.sync_layout(None, 1)

    # ---- 布局 ----

    def sync_layout(self, metrics=None, count=1):
        self.metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        metrics = self.metrics
        width = min(metrics.px(PICKER_WIDTH), int(metrics.screen_w * 0.62))
        rows = max(1, count)
        height = metrics.px(PICKER_HEADER + PICKER_ROW_HEIGHT * rows + PICKER_FOOTER)
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        self.rects = []
        for index in range(count):
            self.rects.append(pygame.Rect(
                self.panel_rect.x + metrics.px(36),
                self.panel_rect.y + metrics.px(PICKER_HEADER)
                + index * metrics.px(PICKER_ROW_HEIGHT),
                self.panel_rect.width - metrics.px(72),
                metrics.px(PICKER_ROW_HEIGHT - 16),
            ))
        self.cancel_button.rect = pygame.Rect(
            self.panel_rect.centerx - metrics.px(90),
            self.panel_rect.bottom - metrics.px(68),
            metrics.px(180),
            metrics.px(50),
        )
        return self

    # ---- 命中 ----

    def hit(self, position, game):
        """返回 ("action", action_id) / "action_cancel" / "swallow"。"""

        options = game.card_action_picker()
        if not options:
            return None
        self.sync_layout(self.metrics, len(options))
        for option, rect in zip(options, self.rects):
            if rect.collidepoint(position) and option.enabled:
                return ("action", option.action_id)
        if self.cancel_button.contains(position):
            # 与固定按钮的「取消选择」使用同一个动作名，避免与技能面板的
            # cancel（取消技能发动）混在一起。
            return "action_cancel"
        return "swallow"

    # ---- 绘制 ----

    def draw(self, surface, game, mouse_pos=None):
        options = game.card_action_picker()
        if not options:
            return
        self.sync_layout(self.metrics, len(options))
        metrics = self.metrics
        fonts = metrics.fonts

        veil = pygame.Surface((metrics.screen_w, metrics.screen_h), pygame.SRCALPHA)
        veil.fill((6, 9, 13, 172))
        surface.blit(veil, (0, 0))

        draw_panel(surface, self.panel_rect, fill=theme.PANEL, border=theme.GOLD,
                   border_width=theme.BORDER_THICK, radius=metrics.px(16))

        title = fonts.get("large").render("选择操作", True, theme.GOLD_BRIGHT)
        surface.blit(title, title.get_rect(
            center=(self.panel_rect.centerx, self.panel_rect.y + metrics.px(46))))

        subtitle = options[0].describe_sources()
        if subtitle:
            label = fonts.get("small").render(subtitle, True, theme.TEXT_DIM)
            surface.blit(label, label.get_rect(
                center=(self.panel_rect.centerx, self.panel_rect.y + metrics.px(78))))

        for option, rect in zip(options, self.rects):
            self._draw_row(surface, option, rect, mouse_pos, metrics)

        self.cancel_button.draw(surface, fonts, mouse_pos)

    def _draw_row(self, surface, option, rect, mouse_pos, metrics):
        fonts = metrics.fonts
        hovered = option.enabled and rect.collidepoint(mouse_pos)
        fill = theme.PANEL_ALT if hovered else theme.PANEL_DEEP
        if not option.enabled:
            border = (86, 92, 100)
        elif option.is_conversion:
            border = theme.TARGET_BLUE
        else:
            border = theme.GOLD_DIM
        draw_panel(surface, rect, fill=fill, border=border,
                   border_width=theme.BORDER, radius=metrics.px(10))

        pad = metrics.px(18)
        name_color = theme.TEXT if option.enabled else theme.TEXT_MUTED
        title = fonts.get("normal").render(option.label, True, name_color)
        surface.blit(title, (rect.x + pad, rect.y + metrics.px(12)))

        info = option.detail or ("当前不可用" if not option.enabled else "")
        if info:
            color = theme.DANGER if not option.enabled else theme.TEXT_DIM
            text = ellipsize_text(info, fonts.get("small"), rect.width - pad * 2)
            surface.blit(
                fonts.get("small").render(text, True, color),
                (rect.x + pad, rect.y + metrics.px(48)),
            )
