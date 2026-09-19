"""Headless multiplayer smoke runner.

Drives a full free-for-all game with no Pygame window: the human seat is
answered by a scripted policy, every AI seat is answered by the engine's
own controller bridge.  Used both as a diagnostic tool and as the backing
implementation of the long-game regression tests.
"""

import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.constants import PLAYER_HAND_SOURCE_RECT
from src.game import Game
from src.game.engine import (
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
)


MAX_STEPS = 6000


class SmokeResult:
    def __init__(self):
        self.turns = 0
        self.deaths = 0
        self.game_over = False
        self.winner = None
        self.stuck_reason = None
        self.stuck_state = None
        self.error = None
        self.log = []

    def __repr__(self):
        return (
            "SmokeResult(turns=%s, deaths=%s, over=%s, winner=%s, survivors=%s, stuck=%s, error=%s)"
            % (self.turns, self.deaths, self.game_over, getattr(self.winner, "name", None),
               getattr(self, "survivors", None), self.stuck_reason, self.error)
        )


def _human_respond(game, request):
    """Scripted stand-in for the human seat."""
    controller = getattr(request.target, "controller_type", None)
    assert getattr(controller, "value", controller) == "human", "not a human request"

    reason = request.context.get("reason")
    if request.request_type.value == "respond_card":
        allowed = list(request.allowed_cards)
        if reason == "dying_rescue" and "TAO" in allowed:
            card = next((c for c in request.target.hand if c.name == "TAO"), None)
            if card is not None:
                game.submit_action(RespondCardAction(request.target, request.request_id, card, None))
                return
        if reason == "wuxie_chain":
            game.submit_action(PassPendingAction(request.target, request.request_id))
            return
        card = next((c for c in request.target.hand if c.name in allowed), None)
        if card is not None:
            game.submit_action(RespondCardAction(request.target, request.request_id, card, None))
        else:
            game.submit_action(PassPendingAction(request.target, request.request_id))
        return

    if request.request_type.value == "confirm":
        game.submit_action(ConfirmPendingAction(request.target, request.request_id, False))
        return

    if request.request_type.value == "choose_option":
        game.submit_action(ChooseOptionAction(request.target, request.request_id, request.options[0]))
        return

    if request.request_type.value == "select_cards":
        candidates = list(request.context.get("candidates", ()))
        picked = candidates[:max(1, request.min_cards)]
        if not picked:
            game.submit_action(PassPendingAction(request.target, request.request_id))
            return
        game.submit_action(SelectCardsAction(request.target, request.request_id, picked))
        return

    raise AssertionError("unhandled request type " + request.request_type.value)


def _use_card(game, card):
    """Click a card for the human seat the way the Pygame UI would.

    Returns True only when the card actually left the hand, so the scripted
    player can tell a rejected use from a successful one.
    """

    if not any(item is card for item in game.player.hand):
        return False
    index = next(
        position
        for position, item in enumerate(game.player.hand)
        if item is card
    )
    game.player_use_card(index, PLAYER_HAND_SOURCE_RECT)
    selection = game.pending_target_selection
    if selection is not None:
        candidates = list(selection.get("candidates", ()))
        need = max(1, int(selection.get("minimum", 1) or 1))
        for target in candidates[:need]:
            game.toggle_target_selection(target)
        if game.pending_target_selection is not None:
            game.confirm_target_selection()
    return not any(item is card for item in game.player.hand)


def _human_play_phase(game, rng):
    """A reasonable human stand-in: heal, equip, attack the weakest, then pass."""

    human = game.player
    phase = game.phase
    if phase == "discard":
        game.player_discard(len(human.hand) - 1, PLAYER_HAND_SOURCE_RECT)
        return
    if phase != "play":
        return

    # 受伤时补血
    if human.hp < human.max_hp:
        tao = next((card for card in human.hand if card.name == "TAO"), None)
        if tao is not None and _use_card(game, tao):
            if game.pending_request is not None or game.phase != "play":
                return

    # 装备空槽
    for card in list(human.hand):
        if card.category != "equipment":
            continue
        if human.get_equipment(card.subtype) is None and _use_card(game, card):
            if game.pending_request is not None or game.phase != "play":
                return

    # 用杀攻击其他角色（被规则拒绝时继续走后面的分支）
    if not human.sha_used:
        sha = next((card for card in human.hand if card.name == "SHA"), None)
        enemies = [
            player
            for player in game.get_alive_players()
            if player is not human
        ]
        if sha is not None and enemies and _use_card(game, sha):
            return

    # 其他锦囊
    for card in list(human.hand):
        if card.category != "trick" or card.name in ("WUXIE", "TIESUO"):
            continue
        if _use_card(game, card):
            return

    game.end_player_turn()


def describe_state(game):
    """Dump enough engine state to diagnose a stuck game."""

    flows = []
    for flow in game.engine.active_flows:
        flows.append(
            "%s(status=%s, stage=%s)" % (
                type(flow).__name__,
                getattr(getattr(flow, "status", None), "value", None),
                getattr(flow, "stage", None),
            )
        )
    turn_flow = getattr(game, "active_turn_flow", None)
    turn_info = None
    if turn_flow is not None:
        turn_info = {
            "status": getattr(turn_flow.status, "value", None),
            "player": getattr(getattr(turn_flow, "player", None), "name", None),
            "phase_index": getattr(turn_flow, "_phase_index", None),
            "paused_child": type(getattr(turn_flow, "_paused_child", None)).__name__,
            "phase_control_skipped": sorted(
                getattr(getattr(turn_flow, "phase_control", None), "_skipped", set())
            ),
        }
    return {
        "active_flows": flows,
        "turn_flow": turn_info,
        "current_player": game.current_player_id,
        "phase": game.phase,
        "turn_phase": getattr(getattr(game, "turn_phase", None), "value", None),
        "busy": game.busy,
        "pending_stack": [
            (request.request_type.value, getattr(request.target, "name", None))
            for request in game.engine.pending.stack
        ],
        "actions_queued": len(game.actions.queue),
        "action_current": type(game.actions.current).__name__ if game.actions.current else None,
        "players": [
            (player.name, player.hp, player.alive, player.chained, len(player.hand))
            for player in game.players
        ],
    }


def run_smoke(ai_count=3, seed=7, max_steps=MAX_STEPS, verbose=False, deck_seed=None):
    pygame.init()
    pygame.display.set_mode((320, 240))

    result = SmokeResult()
    rng = random.Random(seed)
    if deck_seed is not None:
        # 固定牌堆洗牌，便于复现偶发卡死。
        random.seed(deck_seed)
    game = Game(ai_count=ai_count)
    game.start_local_battle(ai_count)

    steps = 0
    idle = 0
    last_signature = None
    last_turn_player = None
    alive_flags = tuple(player.alive for player in game.players)

    try:
        while steps < max_steps:
            steps += 1
            game.update(0.05)

            if game.current_turn_player is not last_turn_player:
                last_turn_player = game.current_turn_player
                result.turns += 1

            current_flags = tuple(player.alive for player in game.players)
            if current_flags != alive_flags:
                result.deaths += sum(
                    1
                    for before, after in zip(alive_flags, current_flags)
                    if before and not after
                )
                alive_flags = current_flags

            signature = (
                game.current_player_id,
                game.phase,
                len(game.engine.pending.stack),
                game.engine.pending.current.request_id if game.engine.pending.current else None,
                game.busy,
                sum(p.hp for p in game.players),
                current_flags,
                len(game.deck.draw_pile),
            )
            if signature == last_signature:
                idle += 1
            else:
                idle = 0
                last_signature = signature

            if game.game_over and not game.busy:
                result.game_over = True
                result.winner = game.winner
                break

            if game.busy:
                continue

            request = game.pending_request
            if request is not None:
                controller = getattr(request.target, "controller_type", None)
                if getattr(controller, "value", controller) == "ai":
                    game.engine.present_or_auto_resolve(request)
                else:
                    _human_respond(game, request)
                idle = 0
                continue

            if game.current_turn_player is game.player and game.phase in ("play", "discard"):
                _human_play_phase(game, rng)
                idle = 0
                continue

            if idle > 400:
                result.stuck_reason = "no progress for 400 steps; " + repr(signature)
                result.stuck_state = describe_state(game)
                break
    except Exception as error:  # pragma: no cover - diagnostic only
        import traceback
        result.error = traceback.format_exc()
        result.stuck_reason = result.stuck_reason or "exception"
        result.stuck_state = describe_state(game)
    finally:
        result.log = list(game.game_log)
        result.survivors = [
            player.name for player in game.players if player.alive
        ]

    if verbose:
        print("\n".join(result.log))
    return game, result


if __name__ == "__main__":
    import sys

    counts = [int(arg) for arg in sys.argv[1:]] or [1, 3, 4, 7]
    for count in counts:
        game, res = run_smoke(ai_count=count, verbose=False)
        print("AI=%s -> %s" % (count, res))
        print("  日志尾部:", " | ".join(res.log[-6:]))
