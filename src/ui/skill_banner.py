"""技能发动提示（Skill Banner）。

技能**真正参与一次结算**的时候，只写一条战报是不够的：玩家要能一眼看到
"谁的哪个技能、什么类型、规则是什么"。这个面板就是那条演出。

数据来源与判定面板同源：技能名 / 类型 / 说明全部来自 ``SkillDef``
（``game.skill_registry``）。联网客户端的技能表是本地的，所以房主只需要
下发 ``skill_id``——类型与说明不会为了让玩家看得懂而多走一趟网络。

只表现，不参与任何规则：

* 面板不认识任何具体技能，不按技能名分支；
* 停留时长由 ``PresentationQueue`` 决定（跟随本机的动画速度），
  面板自己只负责"淡入 → 停留 → 淡出"；
* 玩家不需要点确定，也不会捕获点击。
"""

import pygame

from . import theme
from .widgets import ellipsize_text

#: 设计像素下的面板尺寸（实际会按屏幕缩放，并保证不溢出）。
PANEL_WIDTH = 900
PANEL_PAD = 24
ART_WIDTH = 176
ART_HEIGHT = 246
COLUMN_GAP = 26
MAX_TEXT_WIDTH = PANEL_WIDTH - PANEL_PAD * 2 - ART_WIDTH - COLUMN_GAP

#: 淡入 / 淡出占整个停留时间的比例（不需要额外参数，越短越干脆）。
FADE_IN_RATIO = 0.18
FADE_OUT_RATIO = 0.22

KIND_TONES = {
    "主动技": theme.GOLD_BRIGHT,
    "视为技": theme.TARGET_BLUE,
    "锁定技": theme.TARGET_YELLOW,
    "触发技": theme.RESPONDING,
}


class SkillBanner:
    """一次技能发动提示的展示状态（同一时刻只展示一条：队列保证了顺序）。"""

    def __init__(self):
        self.active = False
        self.timer = 0.0
        self.total = 0.0
        self.alpha = 255
        self.player = None
        self.skill_id = ""
        self.skill_name = ""
        self.kind_label = ""
        self.text = ""
        self.targets = ()
        #: 最近一次算出来的面板矩形（tooltip 避让用）。
        self._rect = None

    # ==================================================
    # 事件入口
    # ==================================================

    def show(self, player, skill_name, *, skill_id="", kind_label="", text="",
             targets=(), duration=2.0):
        if player is None or not skill_name:
            return self
        self.active = True
        self.total = max(0.4, float(duration))
        self.timer = self.total
        self.alpha = 255
        self.player = player
        self.skill_id = str(skill_id or "")
        self.skill_name = str(skill_name)
        self.kind_label = str(kind_label or "")
        self.text = str(text or "")
        self.targets = tuple(targets or ())
        return self

    def cancel(self):
        self.active = False
        self.timer = 0.0
        return self

    def update(self, dt):
        if not self.active:
            return self
        self.timer -= dt
        if self.timer <= 0:
            self.active = False
            self.timer = 0.0
            return self
        self.alpha = self._alpha_for(self.timer)
        return self

    def _alpha_for(self, remaining):
        fade_in = max(0.05, self.total * FADE_IN_RATIO)
        fade_out = max(0.05, self.total * FADE_OUT_RATIO)
        elapsed = self.total - remaining
        if elapsed < fade_in:
            return int(255 * (elapsed / fade_in))
        if remaining < fade_out:
            return int(255 * (remaining / fade_out))
        return 255

    # ==================================================
    # 布局
    # ==================================================

    def _wrap(self, text, font, max_width):
        lines = []
        current = ""
        for char in str(text):
            if char == "\n":
                lines.append(current)
                current = ""
                continue
            probe = current + char
            if current and font.size(probe)[0] > max_width:
                lines.append(current)
                current = char
            else:
                current = probe
        if current:
            lines.append(current)
        return lines or [""]

    def rect(self, metrics, game=None):
        """面板矩形（设计坐标经 metrics 缩放；高度按真实文字行数算）。"""

        width = min(metrics.px(PANEL_WIDTH), int(metrics.screen_w * 0.86))
        pad = metrics.px(PANEL_PAD)
        name_font = metrics.fonts.get("large")
        body_font = metrics.fonts.get("small")
        text_w = min(metrics.px(MAX_TEXT_WIDTH), width - pad * 2 - metrics.px(ART_WIDTH)
                     - metrics.px(COLUMN_GAP))
        lines = self._wrap(self.text, body_font, text_w) if self.text else []
        art_h = metrics.px(ART_HEIGHT)
        text_h = name_font.get_height() + metrics.px(10) + \
            metrics.px(30) + metrics.px(10) + \
            sum(line and (body_font.get_height() + metrics.px(4)) or 0 for line in lines)
        height = max(art_h, text_h) + pad * 2 + metrics.px(16)
        height = min(height, int(metrics.screen_h * 0.72))
        rect = pygame.Rect(0, 0, width, height)
        # 顶部居中偏下：不压住回合横幅与中央战场，也不压住手牌。
        rect.midtop = (metrics.screen_w // 2, int(metrics.screen_h * 0.16))
        return rect

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, surface, game, metrics):
        if not self.active or self.player is None:
            return None
        panel = self.rect(metrics, game)
        self._rect = panel
        pad = metrics.px(PANEL_PAD)
        gap = metrics.px(COLUMN_GAP)
        art_w = metrics.px(ART_WIDTH)

        layer = pygame.Surface(panel.size, pygame.SRCALPHA)
        local = layer.get_rect()
        pygame.draw.rect(layer, (*theme.PANEL_DEEP, 244), local,
                         border_radius=metrics.px(16))
        pygame.draw.rect(layer, theme.GOLD, local, metrics.px(3),
                         border_radius=metrics.px(16))
        tone = KIND_TONES.get(self.kind_label, theme.GOLD_BRIGHT)
        pygame.draw.rect(layer, (*tone, 150), local.inflate(-metrics.px(8), -metrics.px(8)),
                         metrics.px(2), border_radius=metrics.px(12))

        body_top = pad
        art_rect = pygame.Rect(pad, body_top, art_w, panel.height - pad * 2)
        self._draw_art(layer, game, art_rect, metrics)

        text_x = pad + art_w + gap
        text_w = panel.width - text_x - pad
        self._draw_text(layer, text_x, body_top, text_w, metrics, tone)

        if self.alpha < 255:
            layer.set_alpha(self.alpha)
        surface.blit(layer, panel.topleft)
        return panel

    # ---- 左列：武将卡 + 玩家名 ----

    def _draw_art(self, layer, game, rect, metrics):
        art_h = rect.height - metrics.px(38)
        art_rect = pygame.Rect(rect.x, rect.y, rect.width, max(24, art_h))
        art = self._general_art(game, art_rect, metrics)
        if art is not None:
            scaled, target = art
            frame = target.inflate(metrics.px(6), metrics.px(6))
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(8))
            pygame.draw.rect(layer, theme.GOLD_DIM, frame, 2, border_radius=metrics.px(8))
            layer.blit(scaled, target.topleft)
        else:
            # 没有武将素材也必须看得出"是谁"，画一块通用武将牌。
            plate = pygame.Rect(rect.x, rect.y, rect.width, art_rect.height)
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, plate, border_radius=metrics.px(10))
            pygame.draw.rect(layer, theme.GOLD_DIM, plate, 2, border_radius=metrics.px(10))
            tag = metrics.fonts.get("small").render("武将", True, theme.TEXT_DIM)
            layer.blit(tag, tag.get_rect(center=plate.center))
        name_font = metrics.fonts.get("normal")
        name = getattr(self.player, "name", "") or ""
        rendered = name_font.render(
            ellipsize_text(name, name_font, rect.width), True, theme.TEXT)
        layer.blit(rendered, rendered.get_rect(
            midtop=(rect.centerx, rect.bottom - metrics.px(30))))

    def _general_art(self, game, area, metrics):
        """武将牌缩略图（有素材才画，与判定面板同一套取图方式）。"""

        general_id = getattr(self.player, "general_id", None)
        if game is None or not general_id:
            return None
        from . import assets as assets_module

        registry = assets_module.get_registry()
        asset_id = assets_module.general_asset_id(general_id)
        source = registry.surface(asset_id)
        if source is None or area.width < 24 or area.height < 24:
            return None
        target = assets_module.fit_contain(area, source.get_size(), align="midtop")
        scaled = registry.scaled(asset_id, target.size)
        if scaled is None:
            return None
        return scaled, target

    # ---- 右列：技能名 + 类型 + 说明 ----

    def _draw_text(self, layer, x, y, width, metrics, tone):
        fonts = metrics.fonts
        cursor = y
        name_font = fonts.get("large")
        name = name_font.render("【%s】" % self.skill_name, True, theme.GOLD_BRIGHT)
        layer.blit(name, (x, cursor))
        cursor += name.get_height() + metrics.px(8)

        if self.kind_label:
            chip_font = fonts.get("small")
            chip_text = chip_font.render(self.kind_label, True, theme.INK)
            chip = pygame.Rect(x, cursor, chip_text.get_width() + metrics.px(20),
                               chip_text.get_height() + metrics.px(6))
            pygame.draw.rect(layer, tone, chip, border_radius=metrics.px(8))
            layer.blit(chip_text, chip_text.get_rect(center=chip.center))
            cursor += chip.height + metrics.px(10)

        if not self.text:
            return
        body = fonts.get("small")
        for line in self._wrap(self.text, body, width):
            rendered = body.render(line, True, theme.TEXT)
            if cursor + rendered.get_height() > layer.get_height() - metrics.px(10):
                break
            layer.blit(rendered, (x, cursor))
            cursor += rendered.get_height() + metrics.px(4)
