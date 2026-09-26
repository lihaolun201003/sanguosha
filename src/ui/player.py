"""Human player area: status strip, equipment slots, and the fanned hand."""

import pygame

from src.game.engine import UseCardAction
from src.game.rules import TargetRule, target_candidates

from . import cards as card_draw
from . import layout
from . import theme
from .seats import AVATAR_ASPECT, JUDGE_SHORT, general_art
from .widgets import (
    draw_hp_pips,
    draw_panel,
    draw_state_border,
    draw_state_label,
    ellipsize_text,
)


def selected_hand_card_ids(game):
    """Cards the human has already picked (visual lift only)."""

    ids = set()
    selection = game.pending_selection
    if selection is not None and selection.get("zone") in ("hand", "player_hand"):
        for card, _rect, _key in selection["selected"]:
            ids.add(id(card))
    skill_input = getattr(game, "pending_skill_input", None)
    if skill_input is not None:
        for card in skill_input["cards"]:
            ids.add(id(card))
    source_ids = getattr(game, "card_action_source_ids", None)
    if callable(source_ids):
        ids.update(source_ids())
    view_as_ids = getattr(game, "view_as_source_ids", None)
    if callable(view_as_ids):
        ids.update(view_as_ids())
    return ids


def playable_hand_indices(game):
    """Read-only playability probe used to grey out unusable cards.

    规则判定只来自引擎的 Card Action Discovery：一张实体牌只要还有
    任一可达成动作（正常使用或技能转化，例如【龙胆】把【闪】当【杀】），
    就不能灰掉。Returns ``None`` when the UI must not grey anything out.

    联网客户端没有引擎可查，合法性由**房主**在 DecisionRequest 里给出
    （``network_playable_indices``）——客户端绝不自己算规则。
    """

    if game.game_over or game.busy:
        return None

    remote = getattr(game, "network_playable_indices", None)
    if callable(remote):
        # 联网客户端：可点的手牌完全由房主下发的集合决定，**不查任何规则**。
        # 放在最前面是因为响应窗口（要出【闪】）与选牌窗口都不满足"轮到我在
        # 出牌阶段"这个本地前提，而客户端恰恰在这些窗口里最需要高亮。
        return remote()

    if game.current_turn_player is not game.player or getattr(game, "phase", "") != "play":
        return None
    if (
        game.pending_request is not None
        or game.pending_selection is not None
        or game.pending_target_selection is not None
        or getattr(getattr(game, "choice", None), "active", False)
        or getattr(getattr(game, "response", None), "active", False)
    ):
        return None

    actor = game.player

    # View-As 模式：只有这个技能吃得下的牌才是可选的，其余全部灰掉。
    view_as = getattr(game, "pending_view_as", None)
    if view_as is not None:
        allowed = game.view_as_candidate_ids()
        return {
            index for index, card in enumerate(actor.hand) if id(card) in allowed
        }

    context = game.card_actions.play_context(actor)
    playable = set()
    for index, card in enumerate(actor.hand):
        if game.card_actions.is_operable(actor, card, context):
            playable.add(index)
    return playable


def own_identity_label(game, player):
    """自己的身份文案（身份局之外返回空串）。

    走 ``visible_identity``：房主看到的是权威状态里"自己"的那一格，客户端的
    ``ViewPlayer.identity`` 已经是房主按观众过滤后发过来的值——两条路径都不会
    绕过隐藏规则。
    """

    mode = getattr(game, "mode", None)
    if mode is None or not getattr(mode, "uses_identities", False):
        return ""
    from src.game.identity import identity_name, visible_identity

    return identity_name(visible_identity(player, viewer=player))


def draw_player_status(surface, game, table_layout, *, flash=0.0, flash_color=theme.DANGER, shake=0,
                       source_slots=(), candidate_slots=(), responding=False,
                       alive=None, hp=None):
    """真人状态条。

    ``alive`` / ``hp`` 是**表现值**（允许落后于权威状态，见 ui/storyboard
    的视觉账本）：不传时按角色对象自己的字段画。
    """

    metrics = table_layout.metrics
    fonts = metrics.fonts
    player = game.player
    rect = pygame.Rect(metrics.player_status)
    if shake:
        rect = rect.move(shake, 0)

    alive = bool(player.alive) if alive is None else bool(alive)
    hp = int(max(0, player.hp)) if hp is None else int(max(0, hp))
    fill = theme.PANEL if alive else theme.PANEL_DEEP
    state = theme.resolve_state(
        None if alive else "dead",
        "pending_response" if responding else None,
        "current_turn" if game.current_turn_player is player else None,
    )

    # 面板本体只画底色，边框与发光统一由状态层处理，和座位面板同一套视觉。
    draw_panel(surface, rect, fill=fill, border=theme.GOLD_DIM, border_width=0)
    draw_state_border(surface, rect, state, radius=metrics.px(theme.RADIUS_PANEL))

    if flash > 0:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*flash_color, int(140 * flash)))
        surface.blit(veil, rect.topleft)

    pad = metrics.px(16)
    left = rect.x + pad
    judge_rects = []

    # ---- 武将牌缩略（有素材时占最左一条，其余信息整体右移）----
    # 上下各留 10px：贴到面板边上会显得"牌被框夹住了"。
    general = game.generals.get(player.general_id)
    art_height = rect.height - metrics.px(20)
    art_rect = pygame.Rect(
        left, rect.y + metrics.px(10),
        max(metrics.px(18), int(art_height * AVATAR_ASPECT)), art_height)
    art = general_art(general, art_rect)
    if art is not None:
        scaled, target = art
        frame = target.inflate(metrics.px(4), metrics.px(4))
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(5))
        pygame.draw.rect(surface, theme.GOLD_DIM, frame, max(1, metrics.px(2)),
                         border_radius=metrics.px(5))
        surface.blit(scaled, target.topleft)
        if not alive:
            veil = pygame.Surface(target.size, pygame.SRCALPHA)
            veil.fill((26, 30, 36, 150))
            surface.blit(veil, target.topleft)
        left = target.right + metrics.px(14)

    # ---- 名字 / 座次 ----
    name_font = fonts.get("normal")
    name = name_font.render(getattr(player, "name", "") or "玩家", True,
                            theme.TEXT if alive else theme.TEXT_MUTED)
    surface.blit(name, (left, rect.y + metrics.px(9)))

    seat_font = fonts.get("micro")
    skill_names = []
    for skill_id in game.skills.skill_ids_of(player):
        definition = game.skill_registry.get(skill_id)
        if definition is not None:
            skill_names.append("【" + definition.name + "】")
    # 座次取真实座位号：单机真人永远是 0，联网客户端可能是别的座位。
    seat_text = "座次 " + str(getattr(player, "seat", 0))
    seat_label = seat_text + ("  " + " ".join(skill_names) if skill_names else "")
    seat_x = left + name.get_width() + metrics.px(10)
    # 右边界取装备槽的左边再留 12px：座次（或技能名）顶到装备槽上会显得粘连。
    slots = list(table_layout.player_equipment_rects().values())
    limit_x = min((slot.x for slot in slots), default=rect.right) - metrics.px(12)
    seat_available = max(1, limit_x - seat_x)
    # 放不下就整段不显示技能名：宁可不显示，也不要半截的「【…」。
    if seat_font.size(seat_label)[0] > seat_available:
        seat_label = seat_text
    seat = seat_font.render(ellipsize_text(seat_label, seat_font, seat_available),
                            True, theme.TEXT_MUTED)
    surface.blit(seat, (seat_x, rect.y + metrics.px(23)))

    # ---- 自己的身份（身份局）----
    # 自己的座位面板不经过座位区那条身份显示路径，所以在这里单独画一次。
    # 位置选装备槽以右：名字行的左半边已经被名字、座次与装备槽占满，只有
    # 这里一定放得下。身份只来自可见性查询——房主读权威状态里"自己"那一格，
    # 客户端的 ViewPlayer.identity 已是房主按观众过滤后下发的值。
    identity_label = own_identity_label(game, player)
    if identity_label:
        badge = seat_font.render(identity_label, True, theme.GOLD_BRIGHT)
        anchor = max((slot.right for slot in slots), default=rect.x)
        badge_x = anchor + metrics.px(16)
        if badge_x + badge.get_width() <= rect.right - metrics.px(12):
            slot_rect = max(slots, key=lambda item: item.right) if slots else None
            badge_y = slot_rect.centery if slot_rect is not None else rect.y + metrics.px(20)
            surface.blit(badge, badge.get_rect(midleft=(badge_x, badge_y)))

    # ---- 血点 + 数字 ----
    pip_y = rect.y + metrics.px(55)
    spacing = metrics.px(18)
    radius = max(3, metrics.px(7))
    draw_hp_pips(
        surface, (left + radius, pip_y),
        hp, player.max_hp,
        spacing=spacing, radius=radius,
    )
    hp_font = fonts.get("seat_meta")
    hp_x = left + radius + spacing * player.max_hp + metrics.px(6)
    hp_text = hp_font.render(str(hp) + "/" + str(player.max_hp), True, theme.TEXT_DIM)
    hp_rect = hp_text.get_rect(midleft=(hp_x, pip_y))
    surface.blit(hp_text, hp_rect)

    # ---- 装备槽：有卡面就上真实缩略，没有就退回图标 + 文字 ----
    slot_rects = table_layout.player_equipment_rects()
    slot_font = fonts.get("micro")
    for slot, slot_rect in slot_rects.items():
        card = player.get_equipment(slot)
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, slot_rect, border_radius=metrics.px(8))

        art = card_draw.card_art(card, slot_rect.inflate(-6, -6)) if card is not None else None
        if art is not None:
            scaled, target = art
            surface.blit(scaled, target.topleft)
        else:
            icon_center = (slot_rect.centerx, slot_rect.y + metrics.px(18))
            card_draw.draw_slot_icon(
                surface, icon_center, slot,
                size=max(6, metrics.px(10)),
                color=theme.GOLD_BRIGHT if card is not None else (92, 102, 114),
            )

        border_color = theme.GOLD_DIM if card is not None else (74, 84, 96)
        if slot in source_slots or slot in candidate_slots:
            # 技能转化（View-As / 多 source）的槽位走统一的视觉状态。
            draw_state_border(
                surface, slot_rect,
                "view_as_source" if slot in source_slots else "view_as_candidate",
                radius=metrics.px(8),
            )
        else:
            pygame.draw.rect(
                surface, border_color, slot_rect, max(1, metrics.px(2)),
                border_radius=metrics.px(8),
            )

        label = card.display_name if card is not None else "空"
        available = slot_rect.width - metrics.px(10)
        text = ellipsize_text(label, slot_font, available)
        if art is not None:
            # 卡面缩略铺满槽位，装备名用半透明条压在底部，两者都看得见。
            strip_height = metrics.px(17)
            strip = pygame.Surface(
                (slot_rect.width - metrics.px(8), strip_height), pygame.SRCALPHA)
            pygame.draw.rect(strip, (12, 16, 22, 214), strip.get_rect(),
                             border_radius=metrics.px(4))
            surface.blit(strip, (slot_rect.x + metrics.px(4),
                                 slot_rect.bottom - strip_height - metrics.px(4)))
            rendered = slot_font.render(text, True, theme.TEXT)
            surface.blit(rendered, rendered.get_rect(
                center=(slot_rect.centerx, slot_rect.bottom - strip_height // 2 - metrics.px(4))))
        else:
            rendered = slot_font.render(
                text, True, theme.TEXT if card is not None else theme.TEXT_MUTED)
            surface.blit(rendered, rendered.get_rect(
                midtop=(slot_rect.centerx, slot_rect.y + metrics.px(30))))

    # ---- 右侧状态与判定区（先让开右上角的状态角标）----
    marker = draw_state_label(surface, rect, state, fonts, metrics, inset=metrics.px(7))
    tag_font = fonts.get("micro")
    tag_x = rect.right - pad
    if marker is not None:
        tag_x = min(tag_x, marker.left - metrics.px(10))
    if player.chained:
        label = tag_font.render("横置", True, theme.CHAIN)
        tag_rect = pygame.Rect(0, 0, label.get_width() + metrics.px(14), metrics.px(20))
        tag_rect.topright = (tag_x, rect.y + metrics.px(10))
        pygame.draw.rect(surface, (46, 58, 86), tag_rect, border_radius=metrics.px(6))
        pygame.draw.rect(surface, theme.CHAIN, tag_rect, 1, border_radius=metrics.px(6))
        surface.blit(label, label.get_rect(center=tag_rect.center))
        tag_x -= tag_rect.width + metrics.px(8)

    if player.judgement_zone:
        tag_height = metrics.px(20)
        thumb_width = max(10, int(tag_height * AVATAR_ASPECT))
        for card in reversed(player.judgement_zone):
            label = tag_font.render(JUDGE_SHORT.get(card.name, card.display_name[:1]),
                                    True, (224, 208, 246))
            tag_rect = pygame.Rect(
                0, 0, thumb_width + label.get_width() + metrics.px(12), tag_height)
            tag_rect.topright = (tag_x, rect.y + metrics.px(10))
            judge_rects.append((card, pygame.Rect(tag_rect)))
            pygame.draw.rect(surface, (58, 44, 74), tag_rect, border_radius=metrics.px(6))
            pygame.draw.rect(surface, (176, 142, 214), tag_rect, 1, border_radius=metrics.px(6))

            thumb = pygame.Rect(0, 0, thumb_width, tag_height)
            thumb.midleft = (tag_rect.x + metrics.px(3), tag_rect.centery)
            art = card_draw.card_art(card, thumb)
            if art is not None:
                surface.blit(art[0], art[1].topleft)
            surface.blit(label, label.get_rect(
                midleft=(tag_rect.x + metrics.px(7) + thumb_width, tag_rect.centery)))
            tag_x -= tag_rect.width + metrics.px(8)
    else:
        label = tag_font.render("判定区空", True, theme.TEXT_MUTED)
        surface.blit(label, label.get_rect(topright=(rect.right - pad, rect.y + metrics.px(12))))

    # 判定区标签的真实 rect 回传给 Renderer，用作悬停提示的热区
    # （鼠标不必精确命中 13px 的小缩略图）。
    return rect, judge_rects


def draw_hand(surface, game, table_layout, *, playable=None, hover_index=None,
              skip_card_ids=()):
    metrics = table_layout.metrics
    fonts = metrics.fonts
    hand = list(game.player.hand)
    area = metrics.hand_area

    if not hand:
        text = fonts.get("small").render("没有手牌", True, theme.TEXT_MUTED)
        surface.blit(text, text.get_rect(center=(area.centerx, area.y + metrics.px(52))))
        return

    selection = game.pending_selection
    selection_discard = bool(
        selection is not None
        and selection.get("zone") in ("hand", "player_hand")
    )

    skip = skip_card_ids or ()
    for index, card in enumerate(hand):
        if id(card) in skip:
            # 开局发牌期间：这张牌还在飞向手牌，先不画，避免同一张牌出现两次。
            continue
        rect = table_layout.hand_rect(index)
        if rect is None:
            continue
        selected = index in table_layout.selected_hand_keys
        candidate = selection_discard and game.is_selection_candidate(card)
        disabled = playable is not None and index not in playable and not candidate
        card_draw.draw_card(
            surface,
            card,
            rect,
            fonts,
            selected=selected,
            candidate=candidate,
            disabled=disabled,
            dimmed=disabled,
            hovered=index == hover_index,
        )
    return None
