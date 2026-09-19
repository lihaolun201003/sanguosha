"""Renderer: coordinates the table, seat cards, prompt, and animations.

All geometry comes from ``src.ui.layout`` so the drawn rect and the clickable
rect are always identical.  Rules are never evaluated here: playability
queries go through the engine's registered CardEffects.
"""

import pygame

from src.ui import cards as card_draw
from src.ui import fx as effects_module
from src.ui import layout, player, prompt, seats, table, theme
from src.ui.overlay import GameOverOverlay
from src.ui.widgets import Button

DEBUG_UI = False


class Renderer:

    def __init__(self, screen):
        self.screen = screen
        fonts = theme.fonts()
        # 兼容旧调用点使用的字体句柄
        self.font = fonts.get("large")
        self.small_font = fonts.get("small")
        self.tiny_font = fonts.get("tiny")
        self.big_font = fonts.get("title")

        self.effects = effects_module.Effects()
        self.result_overlay = GameOverOverlay()

        self.table_layout = None
        self.mouse_pos = None
        self.playable = None
        self._buttons = {}
        self._pressed_action = None
        self._press_origin = None

        self.primary_button = Button(layout.PRIMARY_BUTTON_RECT, "结束回合", kind="danger", font="normal")
        self.secondary_button = Button(layout.SECONDARY_BUTTON_RECT, "取消", kind="ghost", font="small", enabled=False)

    # ==================================================
    # 每帧状态
    # ==================================================

    def begin_frame(self, game, mouse_pos=None):
        if mouse_pos is None:
            mouse_pos = pygame.mouse.get_pos()
        self.mouse_pos = mouse_pos
        self.effects.attach(game)
        self.table_layout = layout.TableLayout(
            game,
            mouse_pos,
            selected_card_ids=player.selected_hand_card_ids(game),
        )
        self.effects.set_layout(self.table_layout)
        self.playable = player.playable_hand_indices(game)
        return self.table_layout

    def update(self, dt):
        self.effects.update(dt)

    def reset_effects(self):
        self.effects.reset()

    # ==================================================
    # 兼容查询接口
    # ==================================================

    def get_card_rects(self, hand, *, hover_index=None):
        """Current-frame hand rects (identical to the drawn ones)."""

        layout_state = self.table_layout
        if layout_state is not None and len(layout_state.hand_rects) == len(hand):
            reference = getattr(layout_state.game, "player", None)
            if reference is not None and reference.hand is hand:
                return [pygame.Rect(rect) for rect in layout_state.hand_rects]
        return _fallback_hand_rects(hand)

    def card_at_position(self, position, hand):
        layout_state = self.table_layout
        if layout_state is not None and len(layout_state.hand_rects) == len(hand):
            reference = getattr(layout_state.game, "player", None)
            if reference is not None and reference.hand is hand:
                return layout_state.hand_index_at(position)
        return _fallback_hit(position, hand)

    def get_public_card_rects(self, cards):
        return [pygame.Rect(rect) for rect in layout.public_rect_list(list(cards))]

    def get_pool_cards(self, game):
        return [card for card, _key in self.get_pool_entries(game)]

    def get_pool_entries(self, game):
        """[(card, key)] shown in the public card area."""

        entries = game.selection_pool_entries()
        if entries:
            return entries
        return [(card, None) for card in game.public_card_pool]

    def get_player_panel_rects(self, game):
        """Seat panels for everyone, including the human status strip."""

        if self.table_layout is not None and self.table_layout.game is game:
            rects = {player_obj: pygame.Rect(rect) for player_obj, rect in self.table_layout.seat_rects.items()}
        else:
            rects = {player_obj: pygame.Rect(rect) for player_obj, rect in layout.TableLayout(game).seat_rects.items()}
        rects[game.player] = pygame.Rect(layout.PLAYER_STATUS_RECT)
        return rects

    def player_at_position(self, position, game):
        if self.table_layout is not None and self.table_layout.game is game:
            return self.table_layout.player_at(position)
        return layout.TableLayout(game).player_at(position)

    def player_equipment_slot_rects(self, game=None):
        """Equipment slots of the human status strip (same rects as drawn)."""

        if self.table_layout is not None and (game is None or self.table_layout.game is game):
            return {slot: pygame.Rect(rect) for slot, rect in self.table_layout.player_equipment_rects().items()}
        return {slot: pygame.Rect(rect) for slot, rect in layout.TableLayout(game).player_equipment_rects().items()}

    def get_end_turn_rect(self):
        return pygame.Rect(self.primary_button.rect)

    def get_pass_response_rect(self):
        return pygame.Rect(self.secondary_button.rect)

    def get_restart_rect(self):
        return self.result_overlay.restart_rect()

    def get_main_menu_rect(self):
        return self.result_overlay.main_menu_rect()

    def get_phase_name(self, phase):
        return prompt.PHASE_LABELS.get(phase, phase)

    # ==================================================
    # 按钮 / 动作
    # ==================================================

    def _button_state(self, game):
        """Decide the two fixed action buttons for the current state."""

        primary_label, primary_enabled, primary_kind = "结束回合", False, "danger"
        primary_action = "end_turn"
        secondary_label, secondary_enabled, secondary_action = "取消", False, "cancel_target"

        if game.game_over:
            primary_enabled = False
            secondary_enabled = False
        elif game.pending_selection is not None:
            primary_label, primary_kind, primary_action = "请选择卡牌", "secondary", "noop"
            primary_enabled = False
            secondary_label, secondary_enabled, secondary_action = "", False, "noop"
        elif game.zhangba_selecting:
            primary_label, primary_kind, primary_action = "确认出杀", "primary", "confirm_zhangba"
            primary_enabled = len(game.zhangba_selected) == 2
            secondary_label, secondary_enabled, secondary_action = "取消丈八", True, "cancel_zhangba"
        elif game.pending_target_selection is not None:
            selection = game.pending_target_selection
            enough = len(selection["selected"]) >= selection["minimum"]
            primary_label, primary_kind, primary_action = "确认目标", "primary", "confirm_target"
            primary_enabled = enough
            secondary_label, secondary_enabled, secondary_action = "取消目标", True, "cancel_target"
        elif game.response.active:
            primary_label, primary_kind, primary_action = "结束回合", "danger", "end_turn"
            primary_enabled = False
            secondary_label, secondary_enabled, secondary_action = "不出", True, "pass_response"
        elif game.choice.active:
            secondary_label, secondary_enabled, secondary_action = "取消", True, "choice_no"
        elif game.current_turn_player is game.player and game.phase == "play" and not game.busy:
            primary_enabled = True
        elif game.current_turn_player is game.player and game.phase == "discard":
            primary_label, primary_kind, primary_action = "结束回合", "danger", "end_turn"
            primary_enabled = False

        return {
            "primary": (primary_label, primary_enabled, primary_kind, primary_action),
            "secondary": (secondary_label, secondary_enabled, secondary_action),
        }

    def actions_for(self, game):
        state = self._button_state(game)
        label, enabled, kind, action = state["primary"]
        self.primary_button.set_label(label)
        self.primary_button.enabled = enabled
        self.primary_button.kind = kind

        label, enabled, action = state["secondary"]
        self.secondary_button.set_label(label)
        self.secondary_button.enabled = enabled
        return {
            "primary": self.primary_button,
            "secondary": self.secondary_button,
            "primary_action": state["primary"][3],
            "secondary_action": state["secondary"][2],
        }

    def hit_action(self, position, game):
        """Map a click to a UI action name, or None."""

        if game.game_over:
            result = self.result_overlay.handle_click(position)
            return result

        actions = self.actions_for(game)
        if actions["primary"].enabled and actions["primary"].contains(position):
            return actions["primary_action"]
        if actions["secondary"].enabled and actions["secondary"].contains(position):
            return actions["secondary_action"]
        return None

    def set_pressed(self, action):
        self._pressed_action = action

    # ==================================================
    # 悬停提示
    # ==================================================

    def get_hovered_card(self, game, mouse_pos):
        layout_state = self.table_layout
        if layout_state is not None and layout_state.game is game:
            index = layout_state.hand_index_at(mouse_pos)
            if index is not None and 0 <= index < len(game.player.hand):
                card = game.player.hand[index]
                if card.category in ("equipment", "trick"):
                    return card
                return None

            for slot, rect in layout_state.player_equipment_rects().items():
                if rect.collidepoint(mouse_pos):
                    return game.player.get_equipment(slot)

            hovered = layout_state.player_at(mouse_pos)
            if hovered is not None and hovered is not game.player:
                for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
                    card = hovered.get_equipment(slot)
                    if card is not None:
                        return card
                return None

        for card, rect_data in reversed(game.table_cards):
            if pygame.Rect(*rect_data).collidepoint(mouse_pos):
                if card.category in ("equipment", "trick"):
                    return card
                return None
        return None

    def get_card_type_text(self, card):
        if card.category == "trick":
            return "锦囊牌"
        if card.category != "equipment":
            return "基本牌"
        names = {
            "weapon": "装备牌 · 武器",
            "armor": "装备牌 · 防具",
            "defensive_horse": "装备牌 · +1马",
            "offensive_horse": "装备牌 · -1马",
        }
        return names.get(card.subtype, "装备牌")

    def wrap_tooltip_text(self, text, font, max_width):
        lines = []
        for paragraph in str(text).split("\n"):
            if paragraph == "":
                lines.append("")
                continue
            current = ""
            for char in paragraph:
                candidate = current + char
                if current and font.size(candidate)[0] > max_width:
                    lines.append(current)
                    current = char
                else:
                    current = candidate
            if current:
                lines.append(current)
        return lines

    # ==================================================
    # 兼容绘制接口（供旧调用点与测试使用）
    # ==================================================

    def draw_card(self, card, rect, highlight=False, candidate=False):
        card_draw.draw_card(
            self.screen,
            card,
            rect,
            theme.fonts(),
            selected=highlight,
            candidate=candidate,
        )

    def draw_card_back(self, rect, highlight=False, candidate=False):
        card_draw.draw_card_back(self.screen, rect, selected=highlight, candidate=candidate)

    def draw_card_tooltip(self, card, mouse_pos):
        if card is None or card.category not in ("equipment", "trick"):
            return
        fonts = theme.fonts()
        padding = 14
        box_width = 344
        text_width = box_width - padding * 2
        title_font = fonts.get("normal")
        body_font = fonts.get("small")

        lines = []
        type_text = self.get_card_type_text(card)
        if type_text:
            lines.append((type_text, body_font, theme.TEXT_DIM))
        if card.category == "equipment" and card.subtype == "weapon":
            lines.append(("攻击范围：" + str(card.attack_range), body_font, theme.GOLD_BRIGHT))
        description = card.description or "效果说明暂未录入。"
        for line in self.wrap_tooltip_text(description, body_font, text_width):
            lines.append((line, body_font, theme.TEXT))

        title_height = title_font.get_linesize()
        body_height = body_font.get_linesize()
        box_height = padding + title_height + 8 + len(lines) * body_height + padding

        screen_rect = self.screen.get_rect()
        x = mouse_pos[0] + 20
        y = mouse_pos[1] + 18
        if x + box_width > screen_rect.width - 8:
            x = mouse_pos[0] - box_width - 20
        if y + box_height > screen_rect.height - 8:
            y = mouse_pos[1] - box_height - 18
        x = max(8, min(x, screen_rect.width - box_width - 8))
        y = max(8, min(y, screen_rect.height - box_height - 8))

        box_rect = pygame.Rect(x, y, box_width, box_height)
        from src.ui.widgets import draw_panel

        draw_panel(self.screen, box_rect, fill=theme.PANEL_DEEP, border=theme.GOLD, border_width=2)

        title_label = card.display_name
        if card.identity_label:
            title_label += "  " + card.suit_name + str(card.rank)
        title = title_font.render(title_label, True, theme.GOLD_BRIGHT)
        self.screen.blit(title, (x + padding, y + padding))

        text_y = y + padding + title_height + 8
        for text, font, color in lines:
            rendered = font.render(text, True, color)
            self.screen.blit(rendered, (x + padding, text_y))
            text_y += body_height

    def draw_moving_card(self, game):
        move = game.actions.current_move()
        if move is None:
            return
        rect = pygame.Rect(*move.current_rect())
        card_draw.draw_card(self.screen, move.card, rect, theme.fonts())

    # ==================================================
    # 总绘制
    # ==================================================

    def draw(self, game, mouse_pos=None):
        table_layout = self.begin_frame(game, mouse_pos)
        fonts = theme.fonts()

        self.screen.blit(theme.table_surface(layout.WIDTH, layout.HEIGHT), (0, 0))

        # 座位
        selection = game.pending_target_selection
        for player_obj, rect in table_layout.seat_rects.items():
            candidate = bool(selection and player_obj in selection["candidates"])
            selected = bool(selection and player_obj in selection["selected"])
            flash, flash_color = self.effects.seat_flash(player_obj)
            seats.draw_seat(
                self.screen,
                player_obj,
                rect,
                is_current=player_obj is game.current_turn_player,
                candidate=candidate,
                selected=selected,
                flash=flash if flash > 0 else None,
                flash_color=flash_color,
                shake=self.effects.seat_shake(player_obj),
            )

        # 中央
        table.draw_piles(
            self.screen,
            game,
            highlight_draw=self.effects.last_draw_pulse > 0,
            highlight_discard=self.effects.last_discard_pulse > 0,
        )
        table.draw_table_cards(self.screen, game)
        pool_entries = self.get_pool_entries(game)
        table.draw_pool(
            self.screen,
            game,
            pool_entries,
            self.get_public_card_rects([card for card, _key in pool_entries]),
            is_candidate=lambda card, key: bool(game.pending_selection and game.is_selection_candidate(card, key)),
            is_selected=lambda card, key: bool(game.pending_selection and game.is_selection_selected(card, key)),
        )
        table.draw_phase_strip(self.screen, game, self.get_phase_name(game.phase))
        table.draw_judge_banner(self.screen, self.effects.judge_display())
        table.draw_turn_banner(self.screen, self.effects.turn_display())
        self.draw_moving_card(game)

        # 真人区域
        player_flash, player_flash_color = self.effects.seat_flash(game.player)
        player.draw_player_status(
            self.screen,
            game,
            table_layout,
            flash=player_flash if player_flash > 0 else 0.0,
            flash_color=player_flash_color,
            shake=self.effects.seat_shake(game.player),
        )
        player.draw_hand(
            self.screen,
            game,
            table_layout,
            playable=self.playable,
            hover_index=table_layout.hand_hover,
        )

        # Prompt + 按钮
        info = prompt.describe(game)
        prompt.draw(self.screen, info)
        actions = self.actions_for(game)
        actions["primary"].draw(self.screen, fonts, self.mouse_pos, pressed=self._pressed_action == actions["primary_action"])
        actions["secondary"].draw(self.screen, fonts, self.mouse_pos, pressed=self._pressed_action == actions["secondary_action"])

        # 浮动文字与日志
        self._draw_floats()
        table.draw_log(self.screen, game)

        if DEBUG_UI:
            self._draw_debug(game, table_layout)

        if game.game_over:
            self.result_overlay.draw(self.screen, game, self.mouse_pos)
        else:
            hovered = self.get_hovered_card(game, self.mouse_pos)
            if hovered is not None:
                self.draw_card_tooltip(hovered, self.mouse_pos)

    def _draw_floats(self):
        fonts = theme.fonts()
        for item in self.effects.floats:
            rendered = fonts.get("normal").render(item.text, True, item.color)
            rendered.set_alpha(item.alpha)
            x, y = item.position
            self.screen.blit(rendered, rendered.get_rect(center=(x, y - item.offset)))

    def _draw_debug(self, game, table_layout):
        fonts = theme.fonts()
        lines = [
            "phase=" + str(game.phase) + " turn=" + str(game.current_player_id),
            "pending=" + (str(game.engine.pending.current.request_type.value) if game.engine.pending.current else "-"),
            "flows=" + str(len(game.engine.active_flows)) + " busy=" + str(game.busy),
        ]
        for index, line in enumerate(lines):
            text = fonts.get("micro").render(line, True, (140, 220, 160))
            self.screen.blit(text, (12, layout.HEIGHT - 60 + index * 16))


# ==================================================
# 无 layout 时的兜底布局（仅在渲染前调用时使用）
# ==================================================

def _fallback_hand_rects(hand):
    count = len(hand)
    if count == 0:
        return []
    width, height = layout.HAND_CARD_SIZE
    step = width
    if count > 1:
        natural = count * width + (count - 1) * 10
        if natural > layout.HAND_AREA.width:
            step = (layout.HAND_AREA.width - width) / (count - 1)
        else:
            step = width + 10
    total = width + step * (count - 1)
    start = layout.HAND_AREA.centerx - total / 2
    return [
        pygame.Rect(round(start + index * step), layout.HAND_TOP, width, height)
        for index in range(count)
    ]


def _fallback_hit(position, hand):
    rects = _fallback_hand_rects(hand)
    for index in range(len(rects) - 1, -1, -1):
        if rects[index].collidepoint(position):
            return index
    return None
