"""身份展示界面：开局分配身份后，明确告诉真人自己是什么身份。

单独一屏而不是一闪而过，也避免和选将界面挤在一起。中央是真实身份牌
（``assets/identities``），没有素材时退回纯文字排版，规则提示照常显示。
"""

import pygame

from . import assets as assets_module
from . import theme
from .widgets import Button, draw_panel

IDENTITY_COLORS = {
    "lord": (214, 176, 92),
    "loyalist": (108, 168, 222),
    "rebel": (206, 96, 88),
    "renegade": (168, 132, 206),
}


class IdentityRevealScreen:
    """一屏展示真人身份牌 + 「继续」按钮。"""

    def __init__(self, screen):
        self.screen = screen
        self.metrics = None
        #: 可选的一行提示（联机开局用它显示"还在等谁"）。为空时不画。
        self.notice = ""
        self.continue_button = Button(pygame.Rect(0, 0, 10, 10), "继续", kind="primary", font="large")
        self.sync_layout(None)

    def sync_layout(self, metrics=None):
        from . import layout

        metrics = metrics or layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics
        width = min(metrics.px(700), int(metrics.screen_w * 0.74))
        height = min(metrics.px(800), int(metrics.screen_h * 0.94))
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        button_w = min(metrics.px(260), width - metrics.px(160))
        button_h = metrics.px(66)
        self.continue_button.rect = pygame.Rect(
            self.panel_rect.centerx - button_w // 2,
            self.panel_rect.bottom - button_h - metrics.px(30),
            button_w,
            button_h,
        )

        # 文字区自下而上排布：先钉住按钮上方的规则说明，再逐行往上排，
        # 身份牌吃掉剩余空间。这样任何分辨率下文字都不会互相压盖。
        button_top = self.continue_button.rect.top
        self.rule_y = button_top - metrics.px(24)
        self.lord_y = self.rule_y - metrics.px(32)
        self.hint_y = self.lord_y - metrics.px(32)
        self.name_y = self.hint_y - metrics.px(54)

        # 身份牌占据标题与身份大字之间的空间；按 420:572 的比例留位。
        art_top = self.panel_rect.y + metrics.px(106)
        art_bottom = self.name_y - metrics.px(52)
        art_height = max(metrics.px(90), art_bottom - art_top)
        art_width = int(art_height * 420.0 / 572.0)
        art_width = min(art_width, width - metrics.px(120))
        art_height = int(art_width * 572.0 / 420.0)
        self.art_rect = pygame.Rect(0, 0, art_width, art_height)
        self.art_rect.midtop = (self.panel_rect.centerx, art_top)
        return self

    def handle_click(self, position, game):
        if self.continue_button.contains(position):
            return "continue"
        return None

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        # 度量变化时先重算布局，保证第一帧就是正确尺寸。
        if metrics is None or self.metrics is not metrics or self.panel_rect.width <= 10:
            self.sync_layout(metrics)
            metrics = self.metrics
        fonts = metrics.fonts
        mouse = pygame.mouse.get_pos()

        panel = self.panel_rect
        draw_panel(self.screen, panel, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=3, radius=metrics.px(22))

        title = fonts.get("hero").render("身 份 揭 晓", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(panel.centerx, panel.y + metrics.px(70))))

        identity = getattr(game.player, "identity", None)
        from ..game.identity import identity_name

        label = identity_name(identity) or "无身份"
        color = IDENTITY_COLORS.get(getattr(identity, "value", ""), theme.TEXT)

        # 只有真人自己的身份牌会被画出来：这里读的是 game.player.identity，
        # 也就是玩家本人的身份，其他角色的隐藏身份不经过本屏。
        self._draw_identity_art(identity, metrics, color)

        name = fonts.get("hero").render(label, True, color)
        self.screen.blit(name, name.get_rect(center=(panel.centerx, self.name_y)))

        hint = fonts.get("normal").render("你的身份： " + label, True, theme.TEXT_DIM)
        self.screen.blit(hint, hint.get_rect(center=(panel.centerx, self.hint_y)))

        lord = game.mode.lord() if hasattr(game.mode, "lord") else None
        if lord is not None:
            lord_text = fonts.get("small").render(
                "主公：" + lord.name, True, theme.GOLD_BRIGHT)
            self.screen.blit(lord_text, lord_text.get_rect(
                center=(panel.centerx, self.lord_y)))

        rule = fonts.get("small").render(
            "主公身份公开，其余角色身份保密；阵亡后会立即公开。", True, theme.TEXT_DIM)
        self.screen.blit(rule, rule.get_rect(center=(panel.centerx, self.rule_y)))

        self.continue_button.draw(self.screen, fonts, mouse)

        if self.notice:
            note_font = fonts.get("small")
            note = note_font.render(self.notice, True, theme.TEXT_DIM)
            self.screen.blit(note, note.get_rect(
                center=(metrics.screen_w // 2, metrics.screen_h - metrics.px(28))))

    def _draw_identity_art(self, identity, metrics, color):
        """中央身份牌；没有素材时画一个同色的程序化牌背占位。"""

        rect = self.art_rect
        registry = assets_module.get_registry()
        asset_id = assets_module.identity_asset_id(identity) if identity is not None else None
        art = None
        if asset_id is not None:
            source = registry.surface(asset_id)
            if source is not None:
                target = assets_module.fit_contain(rect, source.get_size())
                scaled = registry.scaled(asset_id, target.size)
                if scaled is not None:
                    art = (scaled, target)

        if art is not None:
            scaled, target = art
            frame = target.inflate(metrics.px(10), metrics.px(10))
            pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(12))
            pygame.draw.rect(self.screen, theme.GOLD, frame, 2, border_radius=metrics.px(12))
            self.screen.blit(scaled, target.topleft)
            return

        # Fallback：程序绘制的身份牌（色块 + 身份字）。
        from ..game.identity import identity_name

        pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, rect, border_radius=metrics.px(12))
        pygame.draw.rect(self.screen, color, rect, 3, border_radius=metrics.px(12))
        fonts = metrics.fonts
        glyph_text = (identity_name(identity) or "？")[:1]
        glyph = fonts.get("hero").render(glyph_text, True, color)
        self.screen.blit(glyph, glyph.get_rect(center=rect.center))
