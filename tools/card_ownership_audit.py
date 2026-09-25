"""批量对局审计：多种子整局跑，全程开启牌唯一归属检查。

用途是**找误报 / 找真 bug**，不是交付测试；测试里用的是同一个驱动的精简版
（见 tests/test_card_ownership_invariants.py 的 FullBattleAuditTests）。

运行：SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/card_ownership_audit.py
"""

import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.game import Game
from src.game.engine.events import EventType
from src.game.invariants import CardOwnershipError, assert_card_ownership
from src.player import ControllerType

MODES = ("ffa", "identity")
SEEDS = (1, 7, 13, 29, 101, 202, 404, 777, 1337, 20240925)
FRAME_CAP = 30000


def run_one(mode, seed, ai_count=4, pacing=True):
    game = Game(ai_count=ai_count)
    game.assert_card_ownership = True
    game.set_mode(mode)
    game.rng.seed(seed)
    game.start_local_battle(ai_count)
    # 把本机真人换成 AI：否则真人对局永远停在"等你出牌"。
    game.player.controller_type = ControllerType.AI
    game.controllers.clear()
    game.ai_pacing = pacing

    # 数一下这一局到底发生了多少次状态变更（每个原子边界都查过一次）。
    # 战报条数不能用：``Game.add_log`` 只保留最后 8 条。
    atoms = []

    def count_atom(_context, _event):
        atoms.append(1)

    game.context.events.subscribe(EventType.ATOM_AFTER, count_atom)

    frames = 0
    while frames < FRAME_CAP and not game.game_over:
        game.update(1 / 60)
        frames += 1
    report = assert_card_ownership(game)
    return {
        "mode": mode,
        "seed": seed,
        "frames": frames,
        "over": game.game_over,
        "atoms": len(atoms),
        "dead": sum(1 for player in game.players if not player.alive),
        "discard": len(game.deck.discard_pile),
        "scanned": report.scanned,
    }


def main():
    failures = []
    stats = []
    started = time.time()
    for mode in MODES:
        for seed in SEEDS:
            try:
                stats.append(run_one(mode, seed))
            except CardOwnershipError as error:
                failures.append((mode, seed, str(error)))
                print("!! %s/seed=%s 触发" % (mode, seed))
                print(error)
    elapsed = time.time() - started
    print("=" * 60)
    print("跑完 %d 局，用时 %.1fs" % (len(stats), elapsed))
    print("状态变更（原子边界检查）总次数 = %d" % sum(item["atoms"] for item in stats))
    print("已分胜负 = %d / %d" % (sum(1 for item in stats if item["over"]), len(stats)))
    print("阵亡角色合计 = %d" % sum(item["dead"] for item in stats))
    print("弃牌堆合计 = %d 张" % sum(item["discard"] for item in stats))
    print("帧数区间 = %d .. %d" % (min(item["frames"] for item in stats),
                                  max(item["frames"] for item in stats)))
    if failures:
        print("发现 %d 处重复归属：" % len(failures))
        for mode, seed, text in failures:
            print("  %s/seed=%s: %s" % (mode, seed, text))
        return 1
    print("没有发现重复归属。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
