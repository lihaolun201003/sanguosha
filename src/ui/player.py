"""Human player area: status strip, equipment, and the fanned hand."""

import pygame

from src.game.engine import UseCardAction
from src.game.rules import TargetRule, target_candidates

from . import cards as card_draw
from . import layout
from . import theme
from .widgets import draw_hp_pips, draw_panel


def selected_hand_card_ids(game):
    """Cards the human has already picked (visual lift only)."""

    ids = set()
    selection = game.pending_selection
    if selection is not None and selection.get("zone") in ("hand", "player_hand"):
        for card, _rect, _key in selection["selected"]:
            ids.add(id(card))
    for card, _rect in getattr(game, "zhangba_selected", ()):
        ids.add(id(card))
    return ids


def playable_hand_indices(game):
    """Read-only playability probe used to grey out unusable cards.

    Returns ``None`` when the UI must not grey anything out (not the human's
    play phase, or a pending request owns the input).  Rule decisions still
    come from the registered CardEffect, never from card names in the UI.
    """

    if game.game_over or game.busy:
        return None
    if game.current_turn_player is not game.player or game.phase != "play":
        return None
    if (
        game.pending_request is not None
        or game.pending_selection is not None
        or game.pending_target_selection is not None
        or game.choice.active
        or game.response.active
    ):
        return None

    actor = game.player
    playable = set()
    for index, card in enumerate(actor.hand):
        effect = game.engine.card_effects.get(card)
        if effect is None:
            continue
        if card.category == "equipment" and game.phase != "play":
            continue
        if _effect_can_be_used(game, effect, card, actor):
            playable.add(index)
    return playable


def _effect_can_be_used(game, effect, card, actor):
    rule = effect.target_rule
    if rule is TargetRule.NO_TARGET:
        return effect.can_use(game, UseCardAction(actor, card, []))[0]
    if rule is TargetRule.SELF:
        targets = [actor] if effect.max_targets >= 1 else []
        return effect.can_use(game, UseCardAction(actor, card, targets))[0]

    candidates = target_candidates(game, actor, rule)
    if rule in (TargetRule.ALL_PLAYERS, TargetRule.ALL_OTHERS):
        return effect.can_use(game, UseCardAction(actor, card, list(candidates)))[0]

    for target in candidates:
        if effect.can_use(game, UseCardAction(actor, card, [target]))[0]:
            return True
    return False


def draw_player_status(surface, game, table_layout, *, flash=0.0, flash_color=theme.DANGER, shake=0):
    fonts = theme.fonts()
    player = game.player
    rect = pygame.Rect(layout.PLAYER_STATUS_RECT)
    if shake:
        rect = rect.move(shake, 0)

    alive = player.alive
    fill = theme.PANEL if alive else theme.PANEL_DEEP
    border = theme.GOLD_BRIGHT if game.current_turn_player is player else theme.GOLD_DIM
    border_width = theme.BORDER_THICK if game.current_turn_player is player else theme.BORDER
    glow = theme.GOLD if game.current_turn_player is player else None

    draw_panel(surface, rect, fill=fill, border=border, border_width=border_width, glow=glow)

    if flash > 0:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*flash_color, int(140 * flash)))
        surface.blit(veil, rect.topleft)

    # 名字 + 座次
    name = fonts.get("normal").render("玩家", True, theme.TEXT if alive else theme.TEXT_MUTED)
    surface.blit(name, (rect.x + 12, rect.y + 4))
    seat = fonts.get("micro").render("座次 0", True, theme.TEXT_MUTED)
    surface.blit(seat, (rect.x + 14 + name.get_width() + 8, rect.y + 13))

    # 血点 + HP 数字
    draw_hp_pips(surface, (rect.x + 18, rect.y + 34), max(0, player.hp), player.max_hp, spacing=14, radius=6)
    hp_text = fonts.get("small").render(str(max(0, player.hp)) + "/" + str(player.max_hp), True, theme.TEXT_DIM)
    surface.blit(hp_text, (rect.x + 22 + player.max_hp * 14 + 8, rect.y + 25))

    # 装备槽
    slot_rects = table_layout.player_equipment_rects()
    for slot, slot_rect in slot_rects.items():
        card = player.get_equipment(slot)
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, slot_rect, border_radius=7)
        pygame.draw.rect(
            surface,
            theme.GOLD_DIM if card is not None else (74, 84, 96),
            slot_rect,
            2,
            border_radius=7,
        )
        card_draw.draw_slot_icon(
            surface,
            (slot_rect.centerx, slot_rect.y + 15),
            slot,
            size=9,
            color=theme.GOLD_BRIGHT if card is not None else (92, 102, 114),
        )
        label = card.display_name if card is not None else "空"
        text = fonts.get("micro").render(label[:5], True, theme.TEXT if card is not None else theme.TEXT_MUTED)
        surface.blit(text, text.get_rect(center=(slot_rect.centerx, slot_rect.bottom - 11)))

    # 判定区与横置
    tag_x = rect.right - 8
    tag_font = fonts.get("micro")
    if player.chained:
        label = tag_font.render("横置", True, theme.CHAIN)
        tag_rect = pygame.Rect(0, 0, label.get_width() + 12, 18)
        tag_rect.topright = (tag_x, rect.y + 8)
        pygame.draw.rect(surface, (46, 58, 86), tag_rect, border_radius=6)
        pygame.draw.rect(surface, theme.CHAIN, tag_rect, 1, border_radius=6)
        surface.blit(label, label.get_rect(center=tag_rect.center))
        tag_x -= tag_rect.width + 6

    if player.judgement_zone:
        from .seats import JUDGE_SHORT

        for card in reversed(player.judgement_zone):
            label = tag_font.render(JUDGE_SHORT.get(card.name, card.display_name[:1]), True, (224, 208, 246))
            tag_rect = pygame.Rect(0, 0, 22, 18)
            tag_rect.topright = (tag_x, rect.y + 8)
            pygame.draw.rect(surface, (58, 44, 74), tag_rect, border_radius=6)
            pygame.draw.rect(surface, (176, 142, 214), tag_rect, 1, border_radius=6)
            surface.blit(label, label.get_rect(center=tag_rect.center))
            tag_x -= tag_rect.width + 6
    else:
        label = tag_font.render("判定区空", True, theme.TEXT_MUTED)
        surface.blit(label, label.get_rect(topright=(rect.right - 10, rect.y + 10)))

    return rect


def draw_hand(surface, game, table_layout, *, playable=None, hover_index=None):
    fonts = theme.fonts()
    hand = list(game.player.hand)
    if not hand:
        text = fonts.get("small").render("没有手牌", True, theme.TEXT_MUTED)
        surface.blit(text, text.get_rect(center=(layout.HAND_AREA.centerx, layout.HAND_TOP + 40)))
        return

    selecting = game.pending_selection
    selection_discard = bool(
        selecting is not None
        and selecting.get("zone") in ("hand", "player_hand")
    )

    for index, card in enumerate(hand):
        rect = table_layout.hand_rect(index)
        if rect is None:
            continue
        selected = index in table_layout.selected_hand_keys
        candidate = False
        if selection_discard and game.is_selection_candidate(card):
            candidate = True
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
        )
    return None


def draw_action_buttons(surface, buttons, mouse_pos, pressed=None):
    fonts = theme.fonts()
    for button in buttons:
        button.draw(surface, fonts, mouse_pos, pressed=button is pressed)
