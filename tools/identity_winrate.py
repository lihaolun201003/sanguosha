"""白板身份局胜率统计：无武将技能，纯看身份逻辑下的四方胜率。

用法：

    python tools/identity_winrate.py                # 5/6/7/8 人各 100 局
    python tools/identity_winrate.py --games 200
    python tools/identity_winrate.py --players 8 --games 300

全部座位由 AI 控制（真人也交给 AI），并且**不分配任何武将**——所以技能
完全不参与，统计出来的差异只来自身份立场逻辑（stance_bias）与卡牌运气。
"""

import argparse
import os
import random
import sys
import time
from collections import Counter

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from src.game import Game
from src.game.identity import Identity, identity_name
from src.player import ControllerType
from tools.multiplayer_smoke import human_respond

IDENTITIES = (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE)

# 每种身份对应的胜利原因（用于把"谁赢了"翻译成阵营胜率）。
WIN_REASON = {
    "LORD_SIDE_WIN": "主忠",
    "REBEL_WIN": "反贼",
    "RENEGADE_WIN": "内奸",
}

MAX_STEPS = 20000
STEP_DT = 0.2            # 加速推进：只压缩动画停顿，不改变任何规则
IDLE_LIMIT = 800


class Result:

    def __init__(self):
        self.reason = None
        self.stuck = False
        self.error = None
        self.turns = 0
        self.survivors = Counter()      # 身份 -> 存活人数
        self.roster = Counter()         # 身份 -> 该局人数


def build_game(player_count, seed):
    game = Game(ai_count=player_count - 1)
    game.ai_pacing = True
    game.rng.seed(seed)
    game.set_mode("identity")
    game.pending_identities = game.mode.roll_identities()
    game.start_local_battle(player_count - 1)
    # 真人座位同样交给 AI：统计的是身份逻辑，不是某一次真人操作。
    game.player.controller_type = ControllerType.AI
    game.controllers.pop(game.player.player_id, None)
    return game


def assert_blank(game):
    """白板校验：任何一个人都不该带武将或技能。"""

    for player in game.players:
        if player.general_id:
            raise AssertionError("白板局里出现了武将：" + player.name)
        if game.skills.skill_ids_of(player):
            raise AssertionError("白板局里出现了技能：" + player.name)


def play(player_count, seed):
    result = Result()
    game = build_game(player_count, seed)
    assert_blank(game)
    result.roster.update(
        getattr(player, "identity", None) for player in game.players)

    # 回合数按真实事件计数（v2 回合流程不写"当前回合"日志）。
    from src.game.engine import EventType

    turns = {"count": 0}

    def count_turn(_context, _event):
        turns["count"] += 1

    game.context.events.subscribe(EventType.TURN_START, count_turn, owner="winrate")

    steps = idle = 0
    last_signature = None
    while steps < MAX_STEPS:
        steps += 1
        try:
            game.update(STEP_DT)
        except Exception as error:      # 引擎侧偶发异常：不当作正常结束
            result.error = type(error).__name__ + ": " + str(error)
            break
        signature = (
            game.current_player_id, game.phase, game.busy,
            sum(player.hp for player in game.players),
            tuple(player.alive for player in game.players),
        )
        idle = idle + 1 if signature == last_signature else 0
        last_signature = signature
        if game.game_over and not game.busy:
            break
        if game.busy:
            continue
        request = game.pending_request
        if request is not None:
            try:
                controller = getattr(request.target.controller_type, "value", None)
                if controller == "ai":
                    game.engine.present_or_auto_resolve(request)
                else:
                    human_respond(game, request)
            except Exception as error:
                result.error = type(error).__name__ + ": " + str(error)
                break
            idle = 0
            continue
        if idle > IDLE_LIMIT:
            result.stuck = True
            break

    result.reason = getattr(getattr(game, "result", None), "reason", None)
    result.turns = turns["count"]
    for player in game.players:
        identity = getattr(player, "identity", None)
        if identity is not None and player.alive:
            result.survivors[identity] += 1
    pygame.display.quit()
    return result


def run_block(player_count, games, seed_base, verbose):
    wins = Counter()
    survival = Counter()
    roster = Counter()
    turns = []
    stuck = errors = unfinished = 0
    for index in range(games):
        result = play(player_count, seed_base + index)
        roster.update(result.roster)
        survival.update(result.survivors)
        turns.append(result.turns)
        if result.error:
            errors += 1
            if verbose:
                print("    seed %d 异常：%s" % (seed_base + index, result.error))
        elif result.stuck:
            stuck += 1
        elif result.reason in WIN_REASON:
            wins[WIN_REASON[result.reason]] += 1
        else:
            unfinished += 1
    return {
        "count": games,
        "wins": wins,
        "survival": survival,
        "roster": roster,
        "turns": turns,
        "stuck": stuck,
        "errors": errors,
        "unfinished": unfinished,
    }


def report(player_count, block):
    games = block["count"]
    print()
    print("=" * 78)
    print("%d 人身份局（白板 · 全 AI · %d 局）" % (player_count, games))
    print("=" * 78)
    print("  阵营胜率：")
    for side in ("主忠", "反贼", "内奸"):
        won = block["wins"].get(side, 0)
        print("    %-4s %3d 局  %6.1f%%" % (side, won, won / games * 100))
    print("  各身份生存率（每局存活的该身份人数 / 该身份总人数）：")
    for identity in IDENTITIES:
        total = block["roster"].get(identity, 0)
        alive = block["survival"].get(identity, 0)
        if not total:
            continue
        print("    %-4s 存活 %4d / %4d  %6.1f%%"
              % (identity_name(identity), alive, total, alive / total * 100))
    average_turns = sum(block["turns"]) / max(1, len(block["turns"]))
    print("  平均局时：%.1f 个回合" % average_turns)
    if block["unfinished"] or block["stuck"] or block["errors"]:
        print("  未计入胜负：超时 %d · 卡住 %d · 引擎异常 %d"
              % (block["unfinished"], block["stuck"], block["errors"]))


def main():
    parser = argparse.ArgumentParser(description="白板身份局胜率统计")
    parser.add_argument("--players", type=int, nargs="+", default=[5, 6, 7, 8],
                        help="要统计的人数（身份模式支持 5~8）")
    parser.add_argument("--games", type=int, default=100, help="每种人数的局数")
    parser.add_argument("--seed", type=int, default=900000, help="起始随机种子")
    parser.add_argument("--verbose", action="store_true", help="打印异常局")
    parser.add_argument("--no-stance", action="store_true",
                        help="对照组：关闭身份立场逻辑（只剩血量/手牌打分）")
    args = parser.parse_args()

    if args.no_stance:
        from src.game.controllers.ai import AIController

        AIController.stance_bias = lambda self, target, card=None: 0.0
        AIController._holds_back = lambda self: False
        AIController._shields_the_lord = lambda self, affected: False

    pygame.init()
    started = time.time()
    summary = []
    for offset, player_count in enumerate(args.players):
        block = run_block(
            player_count, args.games, args.seed + offset * 100000, args.verbose)
        report(player_count, block)
        summary.append((player_count, block))
    print()
    print("总耗时 %.1f 秒" % (time.time() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
