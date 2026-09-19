"""Phase 5 smoke scenarios A-F from the stage brief.

Each scenario drives the real engine (no Pygame window) and prints the
observable evidence for the report.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import PassPendingAction, RespondCardAction, UseCardAction
from src.game.flows import DamageContext, DamageFlow
from tests.legacy_helpers import canonical_card, normal_sha, shan, tao


def make_game(ai_count):
    game = Game(ai_count=ai_count)
    game.scene = "game"
    game.actions.clear()
    game.engine.reset()
    for player in game.players:
        player.reset()
    game.phase = "play"
    game.current_turn_player = game.player
    return game


def trick(name):
    return canonical_card(name)


def drive_ai_requests(game, reporter):
    """Answer every AI-owned Pending through the engine bridge."""

    guard = 0
    while game.pending_request is not None:
        request = game.pending_request
        if getattr(request.target.controller_type, "value", "") != "ai":
            return request
        reporter.append("AI 响应：" + request.target.name + " ← " + request.prompt[:24])
        game.engine.present_or_auto_resolve(request)
        guard += 1
        if guard > 200:
            raise AssertionError("AI 响应链没有收敛")
    return None


def scenario_a():
    game = make_game(3)
    card = trick("NANMAN")
    game.player.hand = [card]
    for player in game.players[1:]:
        player.hand = []
    targets = game.seats.alive_players_in_order(start_after=game.player)
    game.submit_action(UseCardAction(game.player, card, targets))
    notes = []
    while game.pending_request is not None:
        drive_ai_requests(game, notes)
    return {
        "目标顺序": [player.name for player in targets],
        "各自体力": [player.hp for player in targets],
        "AI 响应次数": len(notes),
        "通过": [player.hp for player in targets] == [3, 3, 3],
    }


def scenario_b():
    from tests.legacy_helpers import set_draw_order

    game = make_game(4)
    card = trick("WUGU")
    game.player.hand = [card]
    for player in game.players[1:]:
        player.hand = []
    set_draw_order(game, [tao(), normal_sha(), tao(), normal_sha(), tao()])
    targets = game.seats.alive_players_in_order(start_after=game.player, include_start=True)
    game.submit_action(UseCardAction(game.player, card, targets))
    pool_after_reveal = len(game.public_card_pool)

    from src.game.engine import SelectCardsAction

    first = game.pending_request
    game.submit_action(SelectCardsAction(first.target, first.request_id, [game.public_card_pool[0]]))
    while game.pending_request is not None:
        drive_ai_requests(game, [])
    return {
        "翻牌数": pool_after_reveal,
        "结算后公共区": len(game.public_card_pool),
        "各人手牌": [len(player.hand) for player in game.players],
        "通过": pool_after_reveal == 5 and not game.public_card_pool
        and all(len(player.hand) == 1 for player in game.players),
    }


def scenario_c():
    game = make_game(4)
    shooter = game.players[2]
    card = trick("WANJIAN")
    shooter.hand = [card]
    game.player.hand = [shan()]
    for player in game.players[1:]:
        player.hand = []
    shooter.hand = [card]
    targets = [player for player in game.seats.alive_players_in_order(start_after=shooter) if player is not shooter]
    game.submit_action(UseCardAction(shooter, card, targets))
    human_request = None
    if game.pending_request is not None and game.pending_request.target is game.player:
        human_request = game.pending_request.prompt
        game.submit_action(PassPendingAction(game.player, game.pending_request.request_id))
    while game.pending_request is not None:
        drive_ai_requests(game, [])
    return {
        "真人 Pending": human_request,
        "真人是否掉血": game.player.hp,
        "其余角色体力": [player.hp for player in game.players[1:]],
        "通过": human_request is not None and game.player.hp == 3
        and all(player.hp in (3, 4) for player in game.players[1:]),
    }


def scenario_d():
    game = make_game(3)
    victim = game.players[3]
    victim.hp = 0
    game.player.hand = [tao()]
    flow_notes = []
    DamageFlow(
        game.engine,
        DamageContext(game.player, victim, 1, card=normal_sha()),
    ).start()
    human_request = None
    if game.pending_request is not None and game.pending_request.target is game.player:
        human_request = game.pending_request.prompt
    while game.pending_request is not None and game.pending_request.target is game.player:
        break
    drive_ai_requests(game, flow_notes)
    if game.pending_request is not None and game.pending_request.target is game.player:
        human_request = game.pending_request.prompt
        game.submit_action(PassPendingAction(game.player, game.pending_request.request_id))
    while game.pending_request is not None:
        drive_ai_requests(game, flow_notes)
    return {
        "真人 Pending": human_request,
        "濒死者存活": victim.alive,
        "游戏是否继续": not game.game_over,
        "通过": human_request is not None and "救援" in human_request
        and not victim.alive and not game.game_over,
    }


def scenario_e():
    game = make_game(3)
    chained = game.players[1:4]
    for player in chained:
        player.chained = True
    middle = game.players[2]
    middle.hp = 1
    DamageFlow(
        game.engine,
        DamageContext(game.player, chained[0], 1, nature="fire", card=normal_sha()),
    ).start()
    while game.pending_request is not None:
        drive_ai_requests(game, [])
    return {
        "传播后各人横置": [player.chained for player in chained],
        "中间角色": (middle.name, middle.alive),
        "其他人是否继续传播": [player.hp for player in (chained[0], chained[2])],
        "通过": (not chained[0].chained and not chained[2].chained
                 and not middle.alive and chained[2].hp == 3),
    }


def scenario_f():
    game = make_game(3)
    current = game.players[1]
    game.current_turn_player = current
    game.start_turn(current)
    game.actions.clear()
    # 清空手牌，确保这名角色既不能自救也没有人能用桃救他。
    for player in game.players:
        player.hand = []
    DamageFlow(
        game.engine,
        DamageContext(game.player, current, 4, card=normal_sha()),
    ).start()
    while game.pending_request is not None:
        drive_ai_requests(game, [])
    return {
        "阵亡者": current.name,
        "当前回合角色": game.current_turn_player.name if game.current_turn_player else None,
        "游戏结束": game.game_over,
        "通过": not current.alive and not game.game_over
        and game.current_turn_player is not current,
    }


SCENARIOS = {
    "A 南蛮入侵（1 真人 + 3 AI）": scenario_a,
    "B 五谷丰登（1 真人 + 4 AI）": scenario_b,
    "C 万箭齐发（AI2 发起）": scenario_c,
    "D AI3 濒死求桃": scenario_d,
    "E 连环火焰伤害传播": scenario_e,
    "F 当前行动角色阵亡": scenario_f,
}


def main():
    pygame.init()
    pygame.display.set_mode((320, 240))
    passed = 0
    for name, scenario in SCENARIOS.items():
        try:
            result = scenario()
        except Exception as error:  # pragma: no cover - diagnostic
            import traceback
            result = {"通过": False, "异常": traceback.format_exc().strip().splitlines()[-1]}
        ok = result.pop("通过")
        passed += 1 if ok else 0
        print(("PASS" if ok else "FAIL"), name, result)
    print("场景通过 %d/%d" % (passed, len(SCENARIOS)))
    pygame.quit()
    return passed == len(SCENARIOS)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
