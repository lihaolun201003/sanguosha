"""Deterministic helpers for observing the current Legacy game behavior."""

import random
from enum import Enum

from src.card_catalog import (
    create_development_deck,
    create_equipment_cards,
    create_fire_sha,
    create_jiu,
    create_normal_sha,
    create_shan,
    create_tao,
    create_thunder_sha,
)
from src.game import Game


TEST_CARD_RECT = (0, 0, 80, 120)


class SettlementStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    WAITING_FOR_PLAYER = "waiting_for_player"


def normal_sha(card_color="black"):
    suit = "spade" if card_color == "black" else "heart"
    return create_normal_sha(card_color, suit=suit, rank="7")


def fire_sha():
    return create_fire_sha(suit="heart", rank="4")


def thunder_sha():
    return create_thunder_sha(suit="spade", rank="4")


def shan():
    return create_shan(suit="diamond", rank="2")


def tao():
    return create_tao(suit="heart", rank="3")


def jiu():
    return create_jiu(suit="spade", rank="3")


def equipment(internal_name):
    """Return a fresh canonical equipment card from the real catalog."""

    for card in create_equipment_cards():
        if card.name == internal_name:
            return card
    raise ValueError("unknown equipment card: " + internal_name)


def canonical_card(internal_name, *, nature=None, card_color=None):
    """Find a fresh matching card through the real development catalog."""

    for card in create_development_deck():
        if card.name != internal_name:
            continue
        if nature is not None and card.nature != nature:
            continue
        if card_color is not None and card.card_color != card_color:
            continue
        return card
    raise ValueError("no canonical card matched the requested properties")


def set_draw_order(game, cards):
    """Set cards in the order Deck.draw() should return them."""

    game.deck.draw_pile = list(reversed(cards))
    game.deck.discard_pile = []


def make_test_game(
    *,
    player_hp=4,
    enemy_hp=4,
    player_hand=(),
    enemy_hand=(),
    player_equipment=(),
    enemy_equipment=(),
    draw_order=(),
):
    """Create a normalized Legacy Game without random observable state."""

    # Game construction shuffles the development deck. Preserve global RNG
    # state so test construction cannot influence later AI decisions.
    random_state = random.getstate()
    random.seed(0)
    try:
        game = Game()
    finally:
        random.setstate(random_state)

    game.actions.clear()
    game.response.clear()
    game.choice.clear()
    game.pending_selection = None
    game.pending_view_as = None
    game.table_cards.clear()

    game.player.reset()
    game.enemy.reset()
    game.player.hp = player_hp
    game.enemy.hp = enemy_hp
    game.player.hand = list(player_hand)
    game.enemy.hand = list(enemy_hand)

    for card in player_equipment:
        game.player.set_equipment(card)
    for card in enemy_equipment:
        game.enemy.set_equipment(card)
    # 直接塞进装备槽等同于"装备在装备区"：装备赋予的技能（丈八蛇矛一类）
    # 也要跟着绑定，否则测试里的玩家与真实对局不是同一个状态。
    from src.game.equipment_skills.granted import sync_equipment_skills

    sync_equipment_skills(game, game.player)
    sync_equipment_skills(game, game.enemy)

    set_draw_order(game, draw_order)

    game.scene = "game"
    game.phase = "play"
    game.message = ""
    game.sha_used = False
    game.jiu_used = False
    game.player_wine_buff = False
    game.wine_sha_required = False
    game.enemy_wine_buff = False
    game.enemy_jiu_used = False
    game.game_over = False

    # Legacy AI currently uses the module-level random generator. Reset it at
    # the scenario boundary so any later AI discard choice is repeatable too.
    random.seed(0)
    return game


def settlement_status(game):
    if game.actions.busy:
        return SettlementStatus.BUSY
    if (
        game.pending_request is not None
        or game.response.active
        or game.choice.active
        or game.pending_selection is not None
        or game.pending_view_as is not None
    ):
        return SettlementStatus.WAITING_FOR_PLAYER
    return SettlementStatus.IDLE


def drain_actions(game, *, max_updates=1000, dt=10_000.0):
    """Advance only queued animations/callbacks, never player input."""

    updates = 0
    while game.actions.busy:
        if updates >= max_updates:
            raise AssertionError(
                "Legacy ActionQueue did not settle after "
                + str(max_updates)
                + " updates"
            )
        game.update(dt)
        updates += 1
    return settlement_status(game)


def play_player_card(game, card):
    for index, held_card in enumerate(game.player.hand):
        if held_card is card:
            game.player_use_card(index, TEST_CARD_RECT)
            return
    raise AssertionError("requested card is not in the player's hand")


def select_card(game, card, *, key=None):
    if game.pending_selection is None:
        raise AssertionError("game is not waiting for a card selection")
    game.select_pending_card(card, TEST_CARD_RECT, key=key)


def assert_card_is_discarded(game, card):
    if not any(discarded is card for discarded in game.deck.discard_pile):
        raise AssertionError(card.display_name + " was not found in discard pile")


def assert_game_settled(game):
    status = settlement_status(game)
    if status is not SettlementStatus.IDLE:
        raise AssertionError("game is not settled: " + status.value)
    if game.actions.current is not None or game.actions.queue:
        raise AssertionError("ActionQueue reports idle but still owns actions")
