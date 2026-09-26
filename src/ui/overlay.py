"""Result overlay and modal affordances."""

import pygame

from . import assets as assets_module
from . import cards as card_draw
from . import theme
from .widgets import (
    Button,
    draw_modal_panel,
    draw_modal_veil,
    draw_panel,
    ellipsize_text,
)

WIN_TITLE = "胜 利"
LOSE_TITLE = "战 败"

# 结果卡里的缩略图比例（武将牌 / 身份牌同为 420:572）
THUMB_ASPECT = 420.0 / 572.0


class GameOverOverlay:
    """Formal result screen, always centred on the current screen."""

    def __init__(self, metrics=None):
        self.restart_button = Button(pygame.Rect(0, 0, 180, 52), "重新开始", kind="primary")
        self.menu_button = Button(pygame.Rect(0, 0, 180, 52), "返回主菜单", kind="secondary")
        self.panel_rect = pygame.Rect(0, 0, 460, 300)
        self.layout(metrics)

    def layout(self, metrics=None):
        """(Re)compute size and position for the current screen."""

        from . import layout as layout_module

        metrics = metrics or layout_module.LayoutMetrics(
            layout_module.DESIGN_WIDTH, layout_module.DESIGN_HEIGHT
        )
        width = min(metrics.px(680), int(metrics.screen_w * 0.68))
        height = min(metrics.px(640), int(metrics.screen_h * 0.82))
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        button_w = max(metrics.px(190), width // 3)
        button_h = metrics.px(62)
        gap = metrics.px(24)
        total = button_w * 2 + gap
        start_x = self.panel_rect.centerx - total // 2
        button_y = self.panel_rect.bottom - button_h - metrics.px(30)

        self.restart_button.rect = pygame.Rect(start_x, button_y, button_w, button_h)
        self.menu_button.rect = pygame.Rect(start_x + button_w + gap, button_y, button_w, button_h)
        self.metrics = metrics
        return self

    def restart_rect(self):
        return pygame.Rect(self.restart_button.rect)

    def main_menu_rect(self):
        return pygame.Rect(self.menu_button.rect)

    def result_texts(self, game):
        mode = getattr(game, "mode", None)
        custom = None
        if mode is not None:
            # 模式可以给中立的结算文案（1v1 测试：双边手动模式下"你赢了"
            # 没有意义）。色调用语义字符串返回，颜色仍由这里决定。
            custom = mode.overlay_result_texts(game)
        if custom is not None:
            title, subtitle, tone = custom
            colors = {
                "positive": theme.GOLD_BRIGHT,
                "negative": theme.DANGER,
                "neutral": theme.TEXT_DIM,
            }
            return title, subtitle, colors.get(tone, theme.TEXT_DIM)
        if mode is not None and getattr(mode, "uses_identities", False):
            return self._identity_result_texts(game, mode)

        result = game.result
        winner = game.winner
        if winner is game.player:
            return WIN_TITLE, "最后存活者：玩家", theme.GOLD_BRIGHT
        if result is not None and result.reason == "HUMAN_ELIMINATED":
            return LOSE_TITLE, "你已阵亡", theme.DANGER
        if result is not None and result.reason == "NO_SURVIVOR":
            return LOSE_TITLE, "全场阵亡，无人获胜", theme.TEXT_DIM
        if winner is not None:
            return LOSE_TITLE, "最后存活者：" + winner.name, theme.DANGER
        return LOSE_TITLE, "对局结束", theme.TEXT_DIM

    def _identity_result_texts(self, game, mode):
        from ..game.identity import identity_name

        result = game.result
        human_identity = identity_name(getattr(game.player, "identity", None))
        winner_text = mode.result_headline() or "对局结束"
        if result is not None and result.winner is game.player:
            return WIN_TITLE, winner_text + "　·　你是" + human_identity, theme.GOLD_BRIGHT
        if human_identity:
            return LOSE_TITLE, winner_text + "　·　你是" + human_identity, theme.DANGER
        return LOSE_TITLE, winner_text, theme.TEXT_DIM

    def identity_rows(self, game):
        """结算面板要列出的每位玩家：武将 / （身份）/ 存活情况。

        身份模式用模式自己的 ``result_lines``（含阵营归属）。**非身份模式也
        要有列表**——原来它只显示最后一条战报，玩家看不到"这局都有谁、谁活着"，
        结算信息严重不足。这里按同一张表格的字段从 ``game.players`` 现取，
        没有身份字段就留空，渲染层会自动少画一张身份牌。
        """

        mode = getattr(game, "mode", None)
        if mode is not None and getattr(mode, "uses_identities", False):
            if hasattr(mode, "result_lines"):
                return mode.result_lines()
        rows = []
        for player in getattr(game, "players", ()) or ():
            general = None
            generals = getattr(game, "generals", None)
            if generals is not None:
                general = generals.get(getattr(player, "general_id", None))
            rows.append({
                "name": str(getattr(player, "name", "") or ""),
                "identity": "",
                "general": str(getattr(general, "name", "") or ""),
                "alive": bool(getattr(player, "alive", False) and player.hp > 0),
                "is_human": player is getattr(game, "player", None),
            })
        return tuple(rows)
        return ()

    def handle_click(self, position):
        if self.restart_button.contains(position):
            return "restart"
        if self.menu_button.contains(position):
            return "menu"
        return None

    def draw(self, surface, game, metrics=None, mouse_pos=None):
        metrics = metrics or getattr(self, "metrics", None)
        # 度量变化时重新布局：全屏启动的第一帧也不能沿用旧尺寸。
        if metrics is None or getattr(self, "metrics", None) is not metrics or self.panel_rect.width == 0:
            self.layout(metrics)
            metrics = self.metrics

        # 结算框与其它模态共用同一套遮罩/面板外观（不再单独一档更黑的遮罩）。
        draw_modal_veil(surface, metrics)

        fonts = metrics.fonts
        rect = self.panel_rect
        draw_modal_panel(surface, rect, metrics)

        title, subtitle, color = self.result_texts(game)

        title_font = fonts.get("hero")
        title_surface = title_font.render(title, True, color)
        surface.blit(title_surface, title_surface.get_rect(
            center=(rect.centerx, rect.y + metrics.px(74))))

        line_y = rect.y + metrics.px(122)
        pygame.draw.line(surface, theme.GOLD_DIM,
                         (rect.x + metrics.px(70), line_y),
                         (rect.right - metrics.px(70), line_y), 2)

        subtitle_font = fonts.get("large")
        subtitle_surface = subtitle_font.render(subtitle, True, theme.TEXT)
        surface.blit(subtitle_surface, subtitle_surface.get_rect(
            center=(rect.centerx, rect.y + metrics.px(160))))

        rows = self.identity_rows(game)
        if rows:
            self._draw_identity_rows(surface, rect, metrics, fonts, rows)
        elif game.game_log:
            from .widgets import ellipsize_text

            log_font = fonts.get("small")
            last = ellipsize_text(game.game_log[-1], log_font, rect.width - metrics.px(80))
            surface.blit(log_font.render(last, True, theme.TEXT_DIM), log_font.render(
                last, True, theme.TEXT_DIM).get_rect(center=(rect.centerx, rect.y + metrics.px(262))))

        self.restart_button.draw(surface, fonts, mouse_pos)
        self.menu_button.draw(surface, fonts, mouse_pos)

    def _draw_identity_rows(self, surface, rect, metrics, fonts, rows):
        """身份局结算：每人一张简洁结果卡（武将牌 + 身份牌缩略 + 文字）。"""

        name_font = fonts.get("normal")
        small = fonts.get("small")
        top = rect.y + metrics.px(184)
        bottom = self.restart_button.rect.top - metrics.px(16)
        thumb_height = max(metrics.px(20), min(metrics.px(38), (bottom - top) // max(1, len(rows)) - metrics.px(8)))
        thumb_width = max(12, int(thumb_height * THUMB_ASPECT))
        step = thumb_height + metrics.px(10)
        max_rows = max(1, (bottom - top) // step)
        registry = assets_module.get_registry()

        for index, row in enumerate(rows[:max_rows]):
            center_y = top + index * step + thumb_height // 2
            alive = bool(row.get("alive"))
            color = theme.GOLD_BRIGHT if row.get("is_human") else (
                theme.TEXT if alive else theme.TEXT_DIM)

            general_thumb = pygame.Rect(0, 0, thumb_width, thumb_height)
            identity_thumb = pygame.Rect(0, 0, thumb_width, thumb_height)

            parts = [str(row.get("name", "") or "")]
            if row.get("identity"):
                parts.append(str(row["identity"]))
            if row.get("general"):
                parts.append(str(row["general"]))
            label = "　".join(parts)
            mark = "存活" if alive else "阵亡"
            mark_color = theme.HEAL if alive else theme.DANGER

            label_width = name_font.size(label)[0]
            mark_width = small.size(mark)[0]
            gap = metrics.px(10)
            # 非身份模式没有身份牌：不占位、不画空框，整行左移半张牌宽。
            has_identity = bool(row.get("identity_id") or row.get("identity"))
            thumbs = 2 if has_identity else 1
            total = (thumb_width * thumbs + gap * (thumbs + 1)
                     + label_width + metrics.px(16) + mark_width)
            start_x = max(rect.x + metrics.px(24), rect.centerx - total // 2)

            self._blit_thumb(
                surface, registry,
                assets_module.general_asset_id(row.get("general_id") or ""),
                general_thumb.move(start_x, center_y - thumb_height // 2), metrics,
                alive, label=str(row.get("general", "")))

            text_x = start_x + thumb_width + gap
            if has_identity:
                identity_x = text_x
                self._blit_thumb(
                    surface, registry,
                    assets_module.identity_asset_id(row.get("identity_id") or "")
                    if row.get("identity_id") else None,
                    identity_thumb.move(identity_x, center_y - thumb_height // 2),
                    metrics, alive, label=str(row.get("identity", "")))
                text_x = identity_x + thumb_width + gap
            rendered = name_font.render(
                ellipsize_text(label, name_font, rect.right - text_x - metrics.px(90)),
                True, color)
            surface.blit(rendered, rendered.get_rect(midleft=(text_x, center_y)))

            mark_surface = small.render(mark, True, mark_color)
            surface.blit(mark_surface, mark_surface.get_rect(
                midleft=(text_x + rendered.get_width() + metrics.px(16), center_y)))

    @staticmethod
    def _blit_thumb(surface, registry, asset_id, rect, metrics, alive, label=""):
        """画一个小缩略图。

        没有素材（或没有 asset_id）时**不留空框**：在框里画这个位置代表谁的
        一个字（武将名的首字 / "身份"），否则整行看起来像少了东西。
        """

        if asset_id:
            source = registry.surface(asset_id)
            if source is not None:
                target = assets_module.fit_contain(rect, source.get_size())
                scaled = registry.scaled(asset_id, target.size)
                if scaled is not None:
                    pygame.draw.rect(surface, theme.PANEL_SUNKEN,
                                     target.inflate(metrics.px(4), metrics.px(4)),
                                     border_radius=metrics.px(4))
                    surface.blit(scaled, target.topleft)
                    if not alive:
                        veil = pygame.Surface(target.size, pygame.SRCALPHA)
                        veil.fill((*theme.VEIL, 140))
                        surface.blit(veil, target.topleft)
                    return
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, rect, border_radius=metrics.px(4))
        pygame.draw.rect(surface, theme.GOLD_DIM, rect, 1, border_radius=metrics.px(4))
        # 没有素材也要让人看出这一格代表谁：画一个字，而不是留空框。
        text = str(label or "").strip()
        if text and rect.width >= metrics.px(10) and rect.height >= metrics.px(12):
            font = metrics.fonts.get(theme.FONT_FALLBACK_MICRO)
            glyph = font.render(text[0], True, theme.TEXT_SECONDARY if alive else theme.TEXT_MUTED)
            surface.blit(glyph, glyph.get_rect(center=rect.center))
