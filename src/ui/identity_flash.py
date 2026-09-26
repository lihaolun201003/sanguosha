"""身份揭示演出（第二十二章）。

身份模式下角色阵亡时，他的身份从"暗"变"明"。以前这件事只体现为座位副标题
里多两个字，玩家根本注意不到；这里给它一次正式的演出：

    武将牌 + 玩家名  →  身份翻开（淡入 + 轻微上浮）  →  停留  →  淡出

演出时长走 ``FXTiming.story_identity``，与其它演出同一套速度档位。
"""

import pygame

from . import assets as assets_module
from . import theme
from .widgets import ellipsize_text

PANEL_WIDTH = 560
PANEL_HEIGHT = 300
ART_WIDTH = 132
ART_HEIGHT = 186
FADE_IN_RATIO = 0.20
FADE_OUT_RATIO = 0.24

#: 身份 → 语义色。用规则层的身份名（"主公"/"忠臣"/"反贼"/"内奸"），
#: 界面不自己判断谁和谁一伙。
IDENTITY_TONES = {
    "主公": theme.GOLD_BRIGHT,
    "忠臣": theme.JADE_BRIGHT,
    "反贼": theme.DANGER,
    "内奸": theme.CHAIN_BRIGHT,
}


def identity_tone(label):
    return IDENTITY_TONES.get(str(label or ""), theme.GOLD_BRIGHT)


class IdentityFlash:
    """一次"身份翻开"演出的展示状态（同一时刻只有一条）。"""

    def __init__(self):
        self.active = False
        self.timer = 0.0
        self.total = 0.0
        self.alpha = 255
        self.player = None
        self.label = ""
        self.general = None

    def show(self, player, label, *, general=None, duration=2.0):
        if player is None or not label:
            return self
        self.active = True
        self.total = max(0.5, float(duration))
        self.timer = self.total
        self.alpha = 255
        self.player = player
        self.label = str(label)
        self.general = general
        return self

    def cancel(self):
        self.active = False
        self.timer = 0.0
        return self

    def update(self, dt, speed=1.0):
        if not self.active:
            return self
        self.timer -= dt * max(0.05, float(speed or 1.0))
        if self.timer <= 0:
            self.active = False
            self.timer = 0.0
            self.alpha = 0
            return self
        remaining = self.timer / self.total
        fade_out = FADE_OUT_RATIO
        fade_in = FADE_IN_RATIO
        if remaining > 1.0 - fade_in:
            ratio = (1.0 - remaining) / fade_in
        elif remaining < fade_out:
            ratio = remaining / fade_out
        else:
            ratio = 1.0
        self.alpha = int(255 * max(0.0, min(1.0, ratio)))
        return self

    # ---- 几何 ----

    def rect(self, metrics):
        width = min(metrics.px(PANEL_WIDTH), int(metrics.screen_w * 0.5))
        height = min(metrics.px(PANEL_HEIGHT), int(metrics.screen_h * 0.46))
        rect = pygame.Rect(0, 0, width, height)
        # 居中：身份揭示是全屏级事件，放在中央最醒目的位置。
        rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)
        return rect

    # ---- 绘制 ----

    def draw(self, surface, metrics):
        if not self.active or self.player is None:
            return None
        panel = self.rect(metrics)
        tone = identity_tone(self.label)
        pad = metrics.px(theme.MODAL_PADDING)

        layer = pygame.Surface(panel.size, pygame.SRCALPHA)
        local = layer.get_rect()
        pygame.draw.rect(layer, (*theme.PANEL_DEEP, 244), local,
                         border_radius=metrics.px(theme.RADIUS_LARGE))
        pygame.draw.rect(layer, theme.MODAL_BORDER, local, metrics.px(theme.BORDER_MODAL),
                         border_radius=metrics.px(theme.RADIUS_LARGE))
        pygame.draw.rect(layer, (*tone, 170),
                         local.inflate(-metrics.px(10), -metrics.px(10)),
                         metrics.px(theme.BORDER_THIN),
                         border_radius=metrics.px(theme.RADIUS_LARGE - 4))

        # 左：武将牌缩略
        art_rect = pygame.Rect(pad, pad, metrics.px(ART_WIDTH), metrics.px(ART_HEIGHT))
        art_rect.centery = local.centery
        art = self._general_art(art_rect, metrics)
        if art is not None:
            scaled, target = art
            frame = target.inflate(metrics.px(6), metrics.px(6))
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, frame,
                             border_radius=metrics.px(theme.RADIUS_SMALL))
            pygame.draw.rect(layer, theme.GOLD_DIM, frame, 2,
                             border_radius=metrics.px(theme.RADIUS_SMALL))
            layer.blit(scaled, target.topleft)
        else:
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, art_rect,
                             border_radius=metrics.px(theme.RADIUS_SMALL))
            pygame.draw.rect(layer, theme.GOLD_DIM, art_rect, 2,
                             border_radius=metrics.px(theme.RADIUS_SMALL))
            # 没有素材也要说明"这是谁的武将牌"：优先画武将名，连武将都没有
            # 才退回一个中性占位。
            name_font = metrics.fonts.get(theme.FONT_SECTION)
            general_name = str(getattr(self.general, "name", "") or "")
            text = name_font.render(general_name or "武将", True,
                                    theme.TEXT_SECONDARY if general_name
                                    else theme.TEXT_MUTED)
            wrapped = None
            if general_name and name_font.size(general_name)[0] > art_rect.width - 8:
                from .widgets import wrap_text

                lines = wrap_text(general_name, name_font, art_rect.width - 8,
                                  max_lines=2)
                step = name_font.get_height() + 2
                top = art_rect.centery - (len(lines) * step) // 2
                wrapped = [(line, top + index * step) for index, line in enumerate(lines)]
            if wrapped:
                for line, y in wrapped:
                    rendered = name_font.render(line, True, theme.TEXT_SECONDARY)
                    layer.blit(rendered, rendered.get_rect(
                        center=(art_rect.centerx, y + name_font.get_height() // 2)))
            else:
                layer.blit(text, text.get_rect(center=art_rect.center))

        # 右：标题 + 身份大字
        x = art_rect.right + metrics.px(24)
        room = local.right - pad - x
        head_font = metrics.fonts.get(theme.FONT_SECTION)
        head = ellipsize_text(
            str(getattr(self.player, "name", "") or "") + "　阵亡", head_font, room)
        layer.blit(head_font.render(head, True, theme.TEXT_SECONDARY),
                   (x, local.centery - metrics.px(52)))

        big_font = metrics.fonts.get(theme.FONT_TITLE)
        big = big_font.render("【" + self.label + "】", True, tone)
        layer.blit(big, big.get_rect(midleft=(x, local.centery + metrics.px(4))))

        note_font = metrics.fonts.get(theme.FONT_SMALL)
        note = note_font.render("身份已公开", True, theme.TEXT_MUTED)
        layer.blit(note, note.get_rect(midleft=(x, local.centery + metrics.px(56))))

        if self.alpha < 255:
            layer.set_alpha(self.alpha)
        surface.blit(layer, panel.topleft)
        return panel

    def _general_art(self, rect, metrics):
        """武将牌缩略图（与技能提示面板同一套取图方式：没有素材就不画）。"""

        general_id = getattr(self.player, "general_id", "")
        if not general_id or rect.width < 24 or rect.height < 24:
            return None
        registry = assets_module.get_registry()
        asset_id = assets_module.general_asset_id(general_id)
        source = registry.surface(asset_id)
        if source is None:
            return None
        target = assets_module.fit_contain(rect, source.get_size(), align="midtop")
        scaled = registry.scaled(asset_id, target.size)
        if scaled is None:
            return None
        return scaled, target
