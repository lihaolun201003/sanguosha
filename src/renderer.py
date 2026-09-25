"""Renderer: coordinates the table, seat cards, prompt, and animations.

All geometry comes from ``src.ui.layout`` so the drawn rect and the clickable
rect are always identical — including after a fullscreen toggle or a resize.
Rules are never evaluated here: playability queries go through the engine's
registered CardEffects.
"""

import pygame

from src.ui import cards as card_draw
from src.ui import fx as effects_module
from src.game.conversion import EQUIPMENT_ZONE
from src.game.identity import identity_name, visible_identity
from src.ui import layout, player, prompt, seats, table, theme, tooltip
from src.ui.overlay import GameOverOverlay
from src.ui.action_picker import CardActionPicker
from src.ui.skill_bar import SkillBar, SkillPicker
from src.ui.speed import SpeedControl
from src.ui.widgets import Button, place_tooltip

DEBUG_UI = False


def hand_limit_of(game, player):
    """手牌上限：房主侧问规则层，只读视图没有这条规则就退回体力。

    联网客户端是**只读视图**（没有第二个 Game，也不该有），所以不能直接调
    ``game.hand_limit()``——那会在客户端渲染这一帧时抛异常。"按体力"是与
    提示条同口径的近似，仅用于判断"弃牌阶段能不能结束回合"这个按钮可用性。
    """

    limit = getattr(game, "hand_limit", None)
    if callable(limit):
        try:
            return int(limit(player))
        except (AttributeError, TypeError, ValueError):    # pragma: no cover - 兜底
            pass
    return max(0, int(getattr(player, "hp", 0) or 0))


class Renderer:

    def __init__(self, screen):
        self.screen = screen
        self.effects = effects_module.Effects()
        self.result_overlay = GameOverOverlay()
        self.speed_control = SpeedControl()
        self.skill_bar = SkillBar()
        self.skill_picker = SkillPicker()
        self.action_picker = CardActionPicker()

        self.metrics = layout.LayoutMetrics(*screen.get_size())
        self.table_layout = None
        self.mouse_pos = None
        self.playable = None
        self._pressed_action = None
        self._layout_size = None
        self._judge_hover_rects = []

        self.primary_button = Button(self.metrics.primary_button, "结束回合", kind="danger", font="normal")
        self.secondary_button = Button(self.metrics.secondary_button, "取消", kind="ghost", font="small", enabled=False)
        # 投降：常驻按钮，第一次点击只是"上膛"，再点一次才回主界面。
        self.surrender_button = Button(self.metrics.surrender_button, "投降", kind="ghost", font="tiny")
        self._surrender_armed = 0.0
        self._sync_layout(self.metrics)

    # ==================================================
    # 屏幕尺寸
    # ==================================================

    def _sync_layout(self, metrics):
        """Rebuild every screen-space rect after a resize / fullscreen toggle."""

        self.metrics = metrics
        self.primary_button.set_rect(metrics.primary_button)
        self.secondary_button.set_rect(metrics.secondary_button)
        self.surrender_button.set_rect(metrics.surrender_button)
        self.result_overlay.layout(metrics)
        self.speed_control.sync_layout(metrics)
        self.skill_bar.sync_layout(metrics, len(self.skill_bar.skills))
        self.skill_picker.sync_layout(metrics)
        self.action_picker.sync_layout(metrics)
        self._layout_size = (metrics.screen_w, metrics.screen_h)
        # 引擎的动画落点（含中央出牌 anchor）立刻跟上新分辨率：F11 / resize
        # 之后同一帧就落到新位置，不会残留上一套屏幕坐标。
        game = getattr(self, "game", None)
        if game is not None:
            game.ui_rects = metrics.animation_rects()
            game.ui_metrics = metrics

    def set_screen(self, screen):
        """Swap the drawing target after a fullscreen toggle."""

        self.screen = screen
        self._layout_size = None
        self.refresh_layout()
        return self.metrics

    def refresh_layout(self):
        """Public hook for a window-size change (F11 / resize)."""

        size = self.screen.get_size()
        if size != self._layout_size:
            self._sync_layout(layout.LayoutMetrics(*size))
        return self.metrics

    # ---- 兼容字体句柄（跟随当前分辨率）----

    @property
    def font(self):
        return self.metrics.fonts.get("large")

    @property
    def small_font(self):
        return self.metrics.fonts.get("small")

    @property
    def tiny_font(self):
        return self.metrics.fonts.get("tiny")

    @property
    def big_font(self):
        return self.metrics.fonts.get("title")

    # ==================================================
    # 每帧状态
    # ==================================================

    def begin_frame(self, game, mouse_pos=None):
        if mouse_pos is None:
            mouse_pos = pygame.mouse.get_pos()
        metrics = self.refresh_layout()
        self.game = game
        self.mouse_pos = mouse_pos
        self.effects.attach(game)
        self.table_layout = layout.TableLayout(
            game,
            metrics,
            mouse_pos,
            selected_card_ids=player.selected_hand_card_ids(game),
        )
        self.effects.set_layout(self.table_layout)
        self.playable = player.playable_hand_indices(game)

        # 引擎动画落点跟随当前布局（不再依赖固定的 1000×700 坐标）。
        game.ui_rects = self.table_layout.animation_rects()
        game.ui_metrics = metrics
        # 判定展示期间压住动作队列：这一帧的绘制与这一帧的 update 谁先谁后
        # 都不影响结果（update 里也会同步一次）。
        self.effects.sync_action_gate()

        # 开局发牌：引擎已经发好牌，这里只登记"逐张飞到手牌"的表现动画。
        pending_deal = getattr(game, "deal_presentation", None)
        if pending_deal:
            game.deal_presentation = None
            owner, cards = pending_deal
            if cards:
                start = metrics.to_screen(layout.DRAW_PILE_RECT).center
                hand_rect = metrics.hand_area
                self.effects.queue_deal(
                    owner, cards, start=start,
                    end=(hand_rect.centerx, hand_rect.y))
        return self.table_layout

    def update(self, dt):
        self.effects.update(dt)
        if self._surrender_armed > 0:
            self._surrender_armed = max(0.0, self._surrender_armed - dt)

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
        return _fallback_hand_rects(hand, self.metrics)

    def card_at_position(self, position, hand):
        layout_state = self.table_layout
        if layout_state is not None and len(layout_state.hand_rects) == len(hand):
            reference = getattr(layout_state.game, "player", None)
            if reference is not None and reference.hand is hand:
                return layout_state.hand_index_at(position)
        return _fallback_hit(position, hand, self.metrics)

    def get_public_card_rects(self, cards):
        return [pygame.Rect(rect) for rect in layout.public_rect_list(list(cards), self.metrics)]

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
            rects = {
                player_obj: pygame.Rect(rect)
                for player_obj, rect in layout.TableLayout(game, self.metrics).seat_rects.items()
            }
        rects[game.player] = pygame.Rect(self.metrics.player_status)
        return rects

    def player_at_position(self, position, game):
        if self.table_layout is not None and self.table_layout.game is game:
            return self.table_layout.player_at(position)
        return layout.TableLayout(game, self.metrics).player_at(position)

    def player_equipment_slot_rects(self, game=None):
        """Equipment slots of the human status strip (same rects as drawn)."""

        if self.table_layout is not None and (game is None or self.table_layout.game is game):
            return {slot: pygame.Rect(rect) for slot, rect in self.table_layout.player_equipment_rects().items()}
        return {
            slot: pygame.Rect(rect)
            for slot, rect in layout.TableLayout(game, self.metrics).player_equipment_rects().items()
        }

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
        elif game.card_action_picker():
            primary_label, primary_kind, primary_action = "请选择操作", "secondary", "noop"
            primary_enabled = False
            secondary_label, secondary_enabled, secondary_action = "取消", True, "action_cancel"
        elif game.pending_card_action is not None:
            primary_label, primary_kind, primary_action = "确认使用", "primary", "confirm_card_action"
            primary_enabled = game.card_action_ready()
            secondary_label, secondary_enabled, secondary_action = "取消选择", True, "action_cancel"
        elif game.pending_view_as is not None:
            primary_label, primary_kind, primary_action = "请选择来源牌", "secondary", "noop"
            primary_enabled = False
            secondary_label, secondary_enabled, secondary_action = "取消技能", True, "view_as_cancel"
        elif game.pending_skill_input is not None:
            primary_label, primary_kind, primary_action = "确认发动", "primary", "confirm_skill"
            primary_enabled = game.skill_input_ready()
            secondary_label, secondary_enabled, secondary_action = "取消发动", True, "cancel_skill"
        elif game.pending_selection is not None:
            primary_label, primary_kind, primary_action = "请选择卡牌", "secondary", "noop"
            primary_enabled = False
            if game.can_cancel_pending_selection():
                # 允许选 0 张的请求（判定改判窗口）：可以不选牌直接跳过。
                secondary_label, secondary_enabled, secondary_action = "跳过", True, "pass_selection"
            else:
                secondary_label, secondary_enabled, secondary_action = "", False, "noop"
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
        elif game.current_turn_player is game.player and game.phase == "play" \
                and prompt.local_can_play(game):
            # 出牌与结束回合共用同一个开放条件：等待其他玩家响应、等待房主结算、
            # 锦囊尚未结算时一律关闭（鼠标点击与按钮同源，见 ui.interaction）。
            #
            # 这里**不**再叠引擎的 can_end_play_phase：那个查询回答的是"现在
            # 允许结束出牌阶段吗"（酒锁定一类），而按下这个按钮还会触发"手牌
            # 超上限 → 切到弃牌阶段"的转移。按它灰掉按钮会让超上限的玩家连
            # 进入弃牌阶段的入口都没有（1v1 手动模式的实测回归）。
            primary_enabled = True
        elif game.current_turn_player is game.player and game.phase == "discard":
            primary_label, primary_kind, primary_action = "结束回合", "danger", "end_turn"
            # 出牌阶段被跳过时回合直接停在弃牌阶段：手牌不超上限就该能结束
            # 回合（超过上限必须先弃牌）。远程真人与 AI 都由控制器收尾，
            # 只有本地这条路径会停在这里。
            primary_enabled = (
                not game.busy
                and len(game.player.hand) <= hand_limit_of(game, game.player)
            )

        return {
            "primary": (primary_label, primary_enabled, primary_kind, primary_action),
            "secondary": (secondary_label, secondary_enabled, secondary_action),
        }

    def actions_for(self, game):
        # 投降按钮：二次确认期间改文案，避免误触直接丢局。
        self.surrender_button.set_label("确认？" if self._surrender_armed > 0 else "投降")
        self.surrender_button.enabled = not game.game_over
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
            return self.result_overlay.handle_click(position)

        # 判定优先：判定面板是牌桌最高层级，它捕获牌桌点击。唯一的例外是
        # "当前这条判定请求问的正是本机玩家"（改判窗口的「跳过」按钮之类），
        # 那属于判定流程自己要求的输入。见 src/game/judge_gate.py。
        gate = getattr(game, "judge_gate", None)
        if gate is not None and not gate.allows_local_input():
            return None

        # 用牌方式选择面板（Card Action Picker）与技能选择面板都是模态的。
        if game.card_action_picker():
            return self.action_picker.hit(position, game)

        picker = game.pending_skill_picker
        if picker:
            self.skill_picker.sync_layout(self.metrics, len(picker))
            return self.skill_picker.hit(position, game)

        skill_hit = self.skill_bar.hit(position, game)
        if skill_hit is not None:
            return skill_hit

        actions = self.actions_for(game)
        if actions["primary"].enabled and actions["primary"].contains(position):
            return actions["primary_action"]
        if actions["secondary"].enabled and actions["secondary"].contains(position):
            return actions["secondary_action"]
        if self.surrender_button.enabled and self.surrender_button.contains(position):
            return "surrender"
        return None

    def set_pressed(self, action):
        self._pressed_action = action

    def request_surrender(self):
        """投降按钮被点击：第一次只是上膛，再点一次才真的回主界面。"""

        if self._surrender_armed > 0:
            self._surrender_armed = 0.0
            return True
        self._surrender_armed = 3.0
        return False

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

            # 装备槽：热区比可视槽位略大，不必精确命中图标。
            slot_pad = self.metrics.px(6)
            for slot, rect in layout_state.player_equipment_rects().items():
                if rect.inflate(slot_pad, slot_pad).collidepoint(mouse_pos):
                    card = game.player.get_equipment(slot)
                    if card is not None:
                        return card

            # 判定区标签：热区比 13px 的小缩略图宽一圈。
            judge_pad_x = self.metrics.px(8)
            judge_pad_y = self.metrics.px(10)
            for card, rect in self._judge_hover_rects:
                if rect.inflate(judge_pad_x, judge_pad_y).collidepoint(mouse_pos):
                    return card

            hovered = layout_state.player_at(mouse_pos)
            if hovered is not None and hovered is not game.player:
                # 精确命中：鼠标指着哪个装备槽就显示哪件装备。
                # （以前固定返回第一件有牌的装备，于是武器总是挡住防具。）
                seat_rect = layout_state.seat_rect(hovered)
                if seat_rect is not None:
                    slots = seats.equipment_slot_rects(
                        seat_rect, self.metrics,
                        shift=self.effects.seat_shake(hovered))
                    for slot, slot_rect in slots.items():
                        if slot_rect.collidepoint(mouse_pos):
                            return hovered.get_equipment(slot)
                return None

        for card, rect_data in reversed(game.table_cards):
            if layout.resolve_card_rect(self.metrics, rect_data).collidepoint(mouse_pos):
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
        """按真实渲染宽度换行（中文字符宽度只能靠 font.size 测量）。"""

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
            self.metrics.fonts,
            selected=highlight,
            candidate=candidate,
        )

    def draw_card_back(self, rect, highlight=False, candidate=False):
        card_draw.draw_card_back(self.screen, rect, selected=highlight, candidate=candidate)

    def draw_card_tooltip(self, card, mouse_pos):
        if card is None or card.category not in ("equipment", "trick"):
            return
        metrics = self.metrics
        fonts = metrics.fonts
        padding = metrics.px(18)
        box_width = min(metrics.px(700), int(metrics.screen_w * 0.46))

        # 左侧真实卡面预览：小卡看不清的插画与牌名在这里放大显示，
        # 花色点数仍以 Card 数据为准（画在卡面之上）。
        art_height = metrics.px(196)
        art_width = metrics.px(144)
        art = card_draw.card_art(card, pygame.Rect(0, 0, art_width, art_height))
        art_block = 0 if art is None else art[1].width + metrics.px(14)

        text_width = box_width - padding * 2 - art_block
        title_font = fonts.get("normal")
        body_font = fonts.get("small")
        line_height = body_font.get_linesize() + metrics.px(4)

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
        text_height = metrics.px(10) + len(lines) * line_height
        box_height = max(
            padding + title_height + text_height + padding,
            art_height + padding * 2,
        )

        screen_rect = self.screen.get_rect()
        anchor = pygame.Rect(
            mouse_pos[0] - metrics.px(12), mouse_pos[1] - metrics.px(12),
            metrics.px(24), metrics.px(24))
        box_rect = place_tooltip(
            anchor, (box_width, box_height), screen_rect,
            avoid=self._tooltip_avoid_rects(metrics),
        )
        x, y = box_rect.x, box_rect.y
        from src.ui.widgets import draw_panel

        draw_panel(self.screen, box_rect, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=2, radius=metrics.px(12))

        text_x = x + padding
        if art is not None:
            scaled, target = art
            art_origin = pygame.Rect(
                x + padding, y + padding + max(0, (art_height - target.height) // 2),
                target.width, target.height,
            )
            frame = art_origin.inflate(metrics.px(8), metrics.px(8))
            pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(8))
            pygame.draw.rect(self.screen, theme.GOLD_DIM, frame, 2, border_radius=metrics.px(8))
            self.screen.blit(scaled, art_origin.topleft)
            text_x = x + padding + art_block

        title_label = card.display_name
        if card.identity_label:
            title_label += "  " + card.suit_name + str(card.rank)
        title = title_font.render(title_label, True, theme.GOLD_BRIGHT)
        self.screen.blit(title, (text_x, y + padding))

        text_y = y + padding + title_height + metrics.px(10)
        for text, font, color in lines:
            self.screen.blit(font.render(text, True, color), (text_x, text_y))
            text_y += line_height

    def draw_text_tooltip(self, text, mouse_pos):
        """技能说明等纯文本提示（沿用卡牌提示的排版规则）。"""

        metrics = self.metrics
        fonts = metrics.fonts
        padding = metrics.px(18)
        box_width = min(metrics.px(560), int(metrics.screen_w * 0.42))
        body_font = fonts.get("small")
        line_height = body_font.get_linesize() + metrics.px(4)
        lines = self.wrap_tooltip_text(text, body_font, box_width - padding * 2)

        box_height = padding * 2 + len(lines) * line_height
        screen_rect = self.screen.get_rect()
        anchor = pygame.Rect(
            mouse_pos[0] - metrics.px(12), mouse_pos[1] - metrics.px(12),
            metrics.px(24), metrics.px(24))
        box_rect = place_tooltip(
            anchor, (box_width, box_height), screen_rect,
            avoid=self._tooltip_avoid_rects(metrics),
        )
        x, y = box_rect.x, box_rect.y
        from src.ui.widgets import draw_panel

        draw_panel(self.screen, box_rect, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=2, radius=metrics.px(12))
        text_y = y + padding
        for line in lines:
            self.screen.blit(body_font.render(line, True, theme.TEXT), (x + padding, text_y))
            text_y += line_height

    def draw_moving_card(self, game):
        queue = getattr(game, "actions", None)
        move = queue.current_move() if queue is not None else None
        if move is None:
            return
        rect = pygame.Rect(*move.current_rect())
        # 飞行中的牌按"这一趟该不该被看见"来画：别人的摸牌、内容未知的牌
        # 一律画牌背。之前无条件画牌面，结果是别人的摸牌动画把牌面（客户端
        # 只有占位牌，即一片空白）直接飞过屏幕。
        if getattr(move, "face_down", False) or getattr(move.card, "face_down", False):
            card_draw.draw_card_back(self.screen, rect)
            return
        card_draw.draw_card(self.screen, move.card, rect, self.metrics.fonts)

    # ==================================================
    # 总绘制
    # ==================================================

    def responding_player(self, game):
        """当前正在等待响应的角色（没有则 None）。

        来源是引擎的 Pending：谁被派了请求，谁就该被高亮。旧的响应系统
        （真人出【闪】/【无懈】的按钮）只服务真人，同样算在内。
        """

        request = game.pending_request
        if request is not None:
            return request.target
        if game.response.active:
            return game.player
        return None

    @staticmethod
    def local_interaction(game):
        """本地可交互（权威 Game）还是只读视图（联网客户端）。

        联网客户端只有一份只读视图：它能画，但不能算规则、不能提交动作。
        所以所有"本地交互专用"的层（按钮 / 技能点击 / 距离提示 / 出牌选择）
        在这一处统一关掉，而不是在每张图里散落 ``if network: ...``。
        """

        return bool(getattr(game, "local_interaction", True))

    @staticmethod
    def interaction_layers(game):
        """要不要画"提示条 / 固定按钮 / 选择面板"这一层。

        单机与联网客户端都要画（客户端的选择由房主的决策请求驱动，槽位形状与
        本地引擎完全同构）；只有纯观看用的只读视图才不画。它与
        ``local_interaction`` 的区别是：后者还代表"能算规则、能提交动作"，
        联网客户端没有这个能力，但**必须有同样的界面**。
        """

        if Renderer.local_interaction(game):
            return True
        return bool(getattr(game, "interaction_layers", False))

    @staticmethod
    def distance_to(game, target):
        """真人到某角色的规则距离——只问规则层，UI 不自己算。

        返回 ``None`` 表示当前不可达（目标不在存活座次环里）。
        """

        if not Renderer.local_interaction(game):
            # 客户端不算距离：合法目标由房主的 DecisionRequest 给出。
            return None

        from src.game.rules import DistanceRule

        if target is game.player:
            return None
        value = DistanceRule.distance(game, game.player, target)
        return value if value < 999 else None

    def draw(self, game, mouse_pos=None):
        table_layout = self.begin_frame(game, mouse_pos)
        metrics = table_layout.metrics

        # 这一帧的视觉所有权：由移动动画 / 发牌飞行 / 判定面板独占表现的牌，
        # 静态区域一律不重复画。所有会画完整卡面的区域共用这**同一个集合**，
        # 不各自写排除条件。
        owned_ids = self.effects.owned_card_ids()

        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))

        # 座位
        selection = game.pending_target_selection
        skill_input = game.pending_skill_input
        responding = self.responding_player(game)
        hovered_player = (
            table_layout.player_at(self.mouse_pos)
            if self.mouse_pos is not None else None
        )
        in_target_mode = bool(selection) or bool(
            skill_input is not None and skill_input["needs_target"])
        for player_obj, rect in table_layout.seat_rects.items():
            candidate = bool(selection and player_obj in selection["candidates"])
            selected = bool(selection and player_obj in selection["selected"])
            if skill_input is not None and skill_input["needs_target"]:
                if any(player_obj is item for item in skill_input["targets"]):
                    candidate = True
                if skill_input["target"] is player_obj:
                    selected = True
            flash, flash_color = self.effects.seat_flash(player_obj)
            identity = ""
            mode = getattr(game, "mode", None)
            if mode is not None and getattr(mode, "uses_identities", False):
                # 只画"这名观众有权看到的"身份：主公与已阵亡的公开身份，
                # 加上**自己**的身份。客户端视图里的 identity 已经按观众
                # 过滤过，所以这一行走的仍是可见性查询，不是直读底牌。
                identity = identity_name(visible_identity(
                    player_obj, viewer=getattr(game, "player", None)))
            # 距离提示只在"正在选目标"或"鼠标停在某人身上"时出现，不常驻占屏。
            distance_hint = None
            if player_obj.alive and (in_target_mode or player_obj is hovered_player):
                distance_hint = self.distance_to(game, player_obj)
            seats.draw_seat(
                self.screen,
                player_obj,
                rect,
                metrics=metrics,
                general=game.generals.get(player_obj.general_id),
                identity=identity,
                is_current=player_obj is game.current_turn_player,
                is_responding=player_obj is responding,
                hovered=player_obj is hovered_player,
                in_target_mode=in_target_mode,
                distance_hint=distance_hint,
                candidate=candidate,
                selected=selected,
                flash=flash if flash > 0 else None,
                flash_color=flash_color,
                shake=self.effects.seat_shake(player_obj),
            )

        # 中央
        table.draw_piles(
            self.screen, game, metrics,
            highlight_draw=self.effects.last_draw_pulse > 0,
            highlight_discard=self.effects.last_discard_pulse > 0,
            hover=(table.pile_at_position(self.mouse_pos, metrics)
                   if self.mouse_pos is not None else None),
            hidden_ids=owned_ids,
        )
        table.draw_table_cards(self.screen, game, metrics, hidden_ids=owned_ids)
        pool_entries = self.get_pool_entries(game)
        table.draw_pool(
            self.screen,
            game,
            pool_entries,
            self.get_public_card_rects([card for card, _key in pool_entries]),
            is_candidate=lambda card, key: bool(game.pending_selection and game.is_selection_candidate(card, key)),
            is_selected=lambda card, key: bool(game.pending_selection and game.is_selection_selected(card, key)),
            metrics=metrics,
            mouse_pos=self.mouse_pos,
            hidden_ids=owned_ids,
            # 顺手牵羊 / 过河拆桥看到的对方手牌：只画牌背，不泄露内容。
            face_down_ids=game.selection_face_down_ids(),
        )
        table.draw_phase_strip(self.screen, game, metrics, self.get_phase_name(game.phase))
        table.draw_turn_banner(self.screen, metrics, self.effects.turn_display())
        self.draw_moving_card(game)

        # 真人区域
        player_flash, player_flash_color = self.effects.seat_flash(game.player)
        source_slots, candidate_slots = self._equipment_source_slots(game)
        _, self._judge_hover_rects = player.draw_player_status(
            self.screen,
            game,
            table_layout,
            flash=player_flash if player_flash > 0 else 0.0,
            flash_color=player_flash_color,
            shake=self.effects.seat_shake(game.player),
            source_slots=source_slots,
            candidate_slots=candidate_slots,
            responding=game.player is responding,
        )
        player.draw_hand(
            self.screen,
            game,
            table_layout,
            playable=self.playable,
            hover_index=table_layout.hand_hover,
            # 开局发牌飞行中的牌 + 正在被移动动画表现的牌，手牌区都不再画。
            skip_card_ids=set(self.effects.dealing_card_ids(game.player)) | owned_ids,
        )

        # Prompt + 按钮。文案与可用性都来自引擎自己的查询，所以单机与联网
        # 客户端画出来的是同一个界面（客户端的交互槽位由房主的决策请求填）。
        if self.interaction_layers(game):
            info = prompt.describe(game)
            prompt.draw(self.screen, info, metrics)
            actions = self.actions_for(game)
            pressed = pygame.mouse.get_pressed()[0]
            actions["primary"].draw(
                self.screen, metrics.fonts, self.mouse_pos,
                pressed=pressed and actions["primary"].hovered(self.mouse_pos),
            )
            actions["secondary"].draw(
                self.screen, metrics.fonts, self.mouse_pos,
                pressed=pressed and actions["secondary"].hovered(self.mouse_pos),
            )
            self.surrender_button.draw(
                self.screen, metrics.fonts, self.mouse_pos,
                pressed=pressed and self.surrender_button.hovered(self.mouse_pos),
            )

        # 浮动文字、战报、节奏控件
        self._draw_floats(metrics)
        table.draw_log(self.screen, game, metrics)
        self.skill_bar.draw(self.screen, game, self.mouse_pos)
        if self.interaction_layers(game):
            self.skill_picker.draw(self.screen, game, self.mouse_pos)
            self.action_picker.draw(self.screen, game, self.mouse_pos)

        # ---- FX overlay ----
        # 指向箭头与动作横幅单独成层：高于所有常规面板（座位 / 卡牌 / 按钮 /
        # 战报），只让真正的顶层提示与模态盖住它们。
        table.draw_deal_flights(
            self.screen, self.effects.deal_flights, metrics)
        table.draw_action_arrows(
            self.screen, self.effects.arrows, table_layout, metrics)
        table.draw_action_banner(
            self.screen, metrics, self.effects.action_display())

        # 悬停提示最后画，保证盖在其他面板之上。
        self._draw_general_tooltip(game, metrics)

        if DEBUG_UI:
            self._draw_debug(game, metrics)

        if game.game_over:
            self.result_overlay.draw(self.screen, game, metrics, self.mouse_pos)
        else:
            skill_tip = (
                None
                if (game.pending_skill_input or game.pending_skill_picker)
                else self.skill_bar.tooltip(game, self.mouse_pos)
            )
            hovered = self.get_hovered_card(game, self.mouse_pos)
            if skill_tip is not None:
                # 提示放在按钮左侧，避免压住三键本身。
                anchor = self.skill_bar.button.rect
                self.draw_text_tooltip(
                    skill_tip,
                    (anchor.x - self.metrics.px(600), anchor.y - self.metrics.px(20)),
                )
            elif hovered is not None:
                self.draw_card_tooltip(hovered, self.mouse_pos)

        # 判定展示面板是战场最高层级：任何 tooltip / 横幅都不许盖住它。
        self.effects.judge_panel.draw(self.screen, game, metrics)

    def _tooltip_avoid_rects(self, metrics):
        """提示框要尽量避开的区域：座位面板、真人手牌区、提示条、按钮区。

        这样 AI 装备 Tooltip 不会压在 SeatPanel 上，也不会盖住手牌；
        中央出牌位与判定面板展示期间同样列入避让，普通提示不会遮住当前
        关键事件。
        """

        rects = [metrics.player_status, metrics.hand_area, metrics.prompt,
                 metrics.primary_button, metrics.secondary_button,
                 metrics.action_display_rect]
        if self.table_layout is not None:
            rects.extend(self.table_layout.seat_rects.values())
        panel = self.effects.judge_panel
        if panel.active:
            rects.append(panel.rect(metrics))
        return rects

    def _equipment_source_slots(self, game):
        """装备区里被选为 source / 可以当 source 的槽位（由 Discovery 决定）。"""

        if not self.local_interaction(game):
            # 客户端没有 Card Action Session：来源高亮由客户端的决策面板表达。
            return set(), set()

        selected = set()
        state = game.pending_card_action
        if state is not None:
            for card in state["selected"]:
                for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
                    if game.player.get_equipment(slot) is card:
                        selected.add(slot)

        candidates = set()
        context = game.current_card_action_context()
        if context is not None:
            zones = game.card_actions.source_zones_in_use(context.actor)
            if EQUIPMENT_ZONE in zones:
                for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
                    card = game.player.get_equipment(slot)
                    if card is None:
                        continue
                    if game.card_actions.is_operable(context.actor, card, context):
                        candidates.add(slot)
        return selected, candidates

    def _draw_floats(self, metrics):
        fonts = metrics.fonts
        for item in self.effects.floats:
            rendered = fonts.get("normal").render(item.text, True, item.color)
            rendered.set_alpha(item.alpha)
            x, y = item.position
            self.screen.blit(rendered, rendered.get_rect(center=(x, y - item.offset)))

    def _draw_general_tooltip(self, game, metrics):
        """鼠标停在某个角色的**武将牌**上时，摊开他的技能。

        只在武将牌那一小块上触发：装备格上悬停要看的是装备效果（由
        ``get_hovered_card`` 负责），两者位置不同、提示互斥。真人的面板很大，
        仍然只在鼠标不在手牌上时显示。
        """

        position = self.mouse_pos
        if position is None or game.game_over:
            return False
        hovered = self.player_at_position(position, game)
        if hovered is None:
            return False
        if hovered is game.player:
            # 真人面板很大，鼠标在手牌上时不要弹提示，免得挡住出牌。
            if self.player_hand_hover(game) is not None:
                return False
        else:
            # 对手座位：只有鼠标落在武将牌上才弹，装备格/血量/判定区都不弹。
            seat_rect = None
            if self.table_layout is not None and self.table_layout.game is game:
                seat_rect = self.table_layout.seat_rect(hovered)
            if seat_rect is None:
                return False
            avatar = seats.avatar_rect(
                seat_rect, metrics, shift=self.effects.seat_shake(hovered))
            if not avatar.collidepoint(position):
                return False
        return tooltip.draw_general_tooltip(
            self.screen, game, hovered, position, metrics)

    def player_hand_hover(self, game):
        """鼠标是否正停在自己的某张手牌上。"""

        if self.table_layout is None or self.table_layout.game is not game:
            return None
        return self.table_layout.hand_hover

    def _draw_debug(self, game, metrics):
        engine = getattr(game, "engine", None)
        fonts = metrics.fonts
        lines = [
            "screen=%dx%d scale=%.3f" % (metrics.screen_w, metrics.screen_h, metrics.scale),
            "phase=" + str(game.phase) + " turn=" + str(game.current_player_id),
            "pending=" + (str(engine.pending.current.request_type.value)
                          if engine is not None and engine.pending.current else "-"),
            "flows=" + str(len(engine.active_flows)) if engine is not None else "flows=-",
            "busy=" + str(game.busy),
        ]
        for index, line in enumerate(lines):
            text = fonts.get("micro").render(line, True, (140, 220, 160))
            self.screen.blit(text, (metrics.px(12), metrics.screen_h - metrics.px(90) + index * metrics.px(18)))


# ==================================================
# 无 layout 时的兜底布局（仅在渲染前调用时使用）
# ==================================================

def _fallback_hand_rects(hand, metrics=None):
    metrics = metrics or layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
    count = len(hand)
    if count == 0:
        return []

    area = metrics.hand_area
    width, height = metrics.hand_card_size()
    top = metrics.hand_top()
    gap = metrics.px(12)

    if count <= 1:
        step = width
    else:
        natural = count * (width + gap) - gap
        step = (width + gap) if natural <= area.width else (area.width - width) / float(count - 1)
    total = width + step * (count - 1)
    start = area.centerx - total / 2.0
    return [
        pygame.Rect(round(start + index * step), top, width, height)
        for index in range(count)
    ]


def _fallback_hit(position, hand, metrics=None):
    rects = _fallback_hand_rects(hand, metrics)
    for index in range(len(rects) - 1, -1, -1):
        if rects[index].collidepoint(position):
            return index
    return None
