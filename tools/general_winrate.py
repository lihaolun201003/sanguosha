"""武将胜率统计：自由混战（无身份）与 1v1，全 AI 自动对战。

用法：

    python tools/general_winrate.py --games 5000           # 自由混战 5000 局
    python tools/general_winrate.py --mode duel --games 5000
    python tools/general_winrate.py --mode both --games 2000 --players 5
    python tools/general_winrate.py --mode ffa --players 8 --games 3000 --top 20

统计口径（重要，不然数字会被误读）：

* **所有座位都是 AI**（包括"本机真人"那一席），同一套 AI 策略、同一个
  ``Game`` 实现，所以差异只来自武将本身与牌运；
* 武将**随机分配**（``general_pool`` 交给 ``assign_generals``），同一局不重复，
  所以每名武将的出场次数大致相等（默认 5 人局：每局 5 名武将各计 1 次出场）；
* 胜负只认"最后一个存活者"（``reason == "LAST_SURVIVOR"``）。这是自由混战与
  1v1 共用的结算；
* 为了让"本机真人阵亡"不提前终止统计（那是界面语义，不是规则语义），模拟里把
  ``game.player`` 置空——引擎不会因此改变任何规则判定，只是不再把某一个座位
  当成"玩家"来做提前结算。

输出：每名武将的出场数、胜场、胜率、相对基准（1/人数）的倍数，以及前若干名
排行榜；同时把完整数据写成 JSON。
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import defaultdict

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame                                                    # noqa: E402

from src.game import Game                                        # noqa: E402
from src.game.modes.duel import ENEMY_SEAT, MY_SEAT, config_of   # noqa: E402
from src.player import ControllerType                            # noqa: E402

MAX_STEPS = 40000
STEP_DT = 0.2
IDLE_LIMIT = 900


# ==================================================
# 一局：自由混战
# ==================================================

def build_ffa(player_count, seed, assignment=None, mode="ffa"):
    """自由混战 / 身份局：全员 AI + 随机武将。

    ``assignment`` 给定时按座次指定武将（``--per-general`` 的均衡发牌用），
    否则由 ``assign_generals`` 从池子里随机不重复地分配。
    ``mode="identity"`` 时按身份局开局（含主公 / 阵营胜负），用来和官方
    身份局胜率数据对照。
    """

    game = Game(ai_count=player_count - 1)
    game.ai_pacing = True
    game.rng.seed(seed)
    game.set_mode(mode)
    if mode == "identity":
        # 身份由本局自己抽；主公体力上限等开局修正在 start_local_battle 里。
        game.pending_identities = game.mode.roll_identities()
    game.general_pool = tuple(game.playable_general_ids(for_random=True))
    if assignment:
        game.general_assignments = dict(assignment)
    game.start_local_battle(player_count - 1)
    _all_ai(game)
    return game


# ==================================================
# 一局：1v1
# ==================================================

def build_duel(seed, my_general, enemy_general, first="random"):
    """1v1：双方武将显式指定（模式要求），双方都由 AI 操作。"""

    game = Game(ai_count=1)
    game.ai_pacing = True
    game.set_mode("duel_test")
    config = config_of(game)
    config.my_general = my_general
    config.enemy_general = enemy_general
    config.first = first
    config.control = "ai"
    config.seed = ""
    game.rng.seed(seed)
    ok, message = game.mode.start_battle()
    if not ok:
        raise RuntimeError("1v1 开局失败：" + message)
    _all_ai(game)
    return game


def _all_ai(game):
    """每个座位都交给 AI，并把"本机真人"这一席取消。

    ``game.player`` 决定"玩家阵亡就提前结算"这条界面语义；模拟里所有座位都是
    AI，任何一个人的死亡都不该终止这一局，所以把它置空——引擎只在
    结算 / 提示文案里读它，规则判定一行都不依赖。
    """

    for player in game.players:
        player.controller_type = ControllerType.AI
    game.controllers.clear()
    game.player = None
    return game


# ==================================================
# 推进一局
# ==================================================

def play(game, *, max_steps=MAX_STEPS, verbose=False):
    """跑到分出胜负；返回 (winner, reason, steps, stuck)。"""

    steps = idle = 0
    last_signature = None
    while steps < max_steps:
        steps += 1
        try:
            game.update(STEP_DT)
        except RecursionError as error:                    # noqa: BLE001
            if verbose:
                import traceback

                print("    异常局现场：")
                print(traceback.format_exc(limit=12))
            return None, "ERROR:" + type(error).__name__, steps, False
        except Exception as error:                        # noqa: BLE001
            if verbose:
                import traceback

                print("    异常局现场：")
                print(traceback.format_exc(limit=12))
            return None, "ERROR:" + type(error).__name__, steps, False
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
            game.engine.present_or_auto_resolve(request)
            idle = 0
            continue
        if idle > IDLE_LIMIT:
            if verbose:
                print("    卡住时的局面：%s / %s / 存活 %s"
                      % (game.current_player_id, game.phase,
                         [player.alive for player in game.players]))
            return None, "STUCK", steps, True
    if not game.game_over and verbose:
        print("    未结束：%s / %s / 存活 %s / busy=%s / pending=%s"
              % (game.current_player_id, game.phase,
                 [player.alive for player in game.players], game.busy,
                 getattr(game.pending_request, "request_type", None)))
    reason = getattr(getattr(game, "result", None), "reason", None)
    winner = getattr(game, "winner", None)
    return winner, reason or "UNFINISHED", steps, False


# ==================================================
# 统计
# ==================================================

class Stats:
    def __init__(self):
        self.played = defaultdict(int)
        self.won = defaultdict(int)
        #: 身份局：identity["gid|identity"] = {"games": …, "won": …}
        #: （won = 该角色所在阵营获胜，与官方身份局胜率同一口径）
        self.identity = defaultdict(lambda: {"games": 0, "won": 0})
        self.side = defaultdict(lambda: {"games": 0, "won": 0})
        #: 同场成对：pairs[a][b] = {"games": 同场局数, "won": a 在其中赢的局数}。
        #: 自由混战没有"一对一"，所以"a 对 b 的胜率"只能这样定义：
        #: **两人同场时 a 的胜率**（同场对照）。
        self.pairs = defaultdict(lambda: defaultdict(lambda: {"games": 0, "won": 0}))
        self.first_seen = defaultdict(int)      # 先手次数（1v1 关心）
        self.first_won = defaultdict(int)
        self.games = 0
        self.unfinished = 0
        self.stuck = 0
        self.errors = defaultdict(int)
        self.steps = 0

    def note_game(self, generals, winner_general, reason, steps, *, first_general=""):
        self.games += 1
        self.steps += steps
        roster = [item for item in generals if item]
        for general_id in roster:
            self.played[general_id] += 1
        # 同场成对统计（双向各记一条）
        if len(roster) > 1:
            for index, first in enumerate(roster):
                for second in roster[index + 1:]:
                    cell = self.pairs[first][second]
                    cell["games"] += 1
                    if winner_general == first:
                        cell["won"] += 1
                    mirror = self.pairs[second][first]
                    mirror["games"] += 1
                    if winner_general == second:
                        mirror["won"] += 1
        if winner_general:
            self.won[winner_general] += 1
        else:
            self.unfinished += 1
            if reason.startswith("ERROR"):
                self.errors[reason] += 1
            elif reason == "STUCK":
                self.stuck += 1
        if first_general:
            self.first_seen[first_general] += 1
            if first_general == winner_general:
                self.first_won[first_general] += 1

    #: 阵营 → 该阵营获胜时的 result.reason
    SIDE_REASONS = {
        "lord": ("LORD_SIDE_WIN",), "loyalist": ("LORD_SIDE_WIN",),
        "rebel": ("REBEL_WIN",), "renegade": ("RENEGADE_WIN",),
    }

    def note_identity(self, player, reason):
        """身份局：记录"这名角色（这个武将、这个身份）的阵营是否获胜"。"""

        identity = getattr(getattr(player, "identity", None), "value", None)
        general_id = getattr(player, "general_id", None)
        if identity is None or not general_id:
            return
        won = bool(reason and reason in self.SIDE_REASONS.get(identity, ()))
        cell = self.identity["%s|%s" % (general_id, identity)]
        cell["games"] += 1
        if won:
            cell["won"] += 1
        side = self.side[identity]
        side["games"] += 1
        if won:
            side["won"] += 1

    def rate(self, general_id):
        played = self.played[general_id]
        return (self.won[general_id] / played) if played else 0.0


def run_ffa(games, players, seed_base, *, progress=0, verbose=False,
            pool=None, balanced=False, mode="ffa"):
    """跑若干局自由混战。

    ``balanced=True`` 时按"洗牌发牌"的方式分配武将：把整张武将表洗匀后
    逐局发出去，发完再洗 —— 这样每名武将的出场次数几乎完全相等（随机分配
    会有 ±30% 的运气差异），适合"每个角色都给足样本"的统计。
    """

    stats = Stats()
    deck = []
    picker = random.Random(seed_base ^ 0x51F7)

    def take(count):
        if not balanced or not pool:
            return None
        while len(deck) < count:
            chunk = list(pool)
            picker.shuffle(chunk)
            deck.extend(chunk)
        picked = deck[:count]
        del deck[:count]
        order = sorted(picked)
        picker.shuffle(order)
        return {seat: general_id for seat, general_id in enumerate(order)}

    for index in range(games):
        seed = seed_base + index
        game = build_ffa(players, seed, take(players), mode=mode)
        generals = [player.general_id for player in game.players]
        if verbose:
            print("  局 %d seed=%d 武将=%s" % (index + 1, seed, generals))
        winner, reason, steps, _stuck = play(game, verbose=verbose)
        stats.note_game(generals, getattr(winner, "general_id", None),
                        reason, steps)
        if mode == "identity":
            for player in game.players:
                stats.note_identity(player, reason)
        if progress and (index + 1) % progress == 0:
            print("    ... %d/%d" % (index + 1, games))
        pygame.display.quit()
    return stats


def run_duel(games, seed_base, pool, *, progress=0, verbose=False,
             balanced=False):
    """1v1：每局随机抽两名不同武将（对手随机），双方策略相同。

    ``balanced=True`` 时同样按"洗牌发牌"配对，保证每名武将出场次数相等。
    """

    picker = random.Random(seed_base ^ 0x5EED)
    deck = []

    def take_pair():
        if not balanced:
            return picker.sample(list(pool), 2)
        while len(deck) < 2:
            chunk = list(pool)
            picker.shuffle(chunk)
            deck.extend(chunk)
        picked = [deck.pop(), deck.pop()]
        return picked[0], picked[1]

    stats = Stats()
    for index in range(games):
        seed = seed_base + index
        first_id, second_id = take_pair()
        game = build_duel(seed, first_id, second_id)
        roster = {int(player.seat): player.general_id for player in game.players}
        winner, reason, steps, _stuck = play(game, verbose=verbose)
        winner_id = getattr(winner, "general_id", None)
        stats.note_game([roster.get(MY_SEAT), roster.get(ENEMY_SEAT)], winner_id,
                        reason, steps, first_general=roster.get(MY_SEAT))
        if progress and (index + 1) % progress == 0:
            print("    ... %d/%d" % (index + 1, games))
        pygame.display.quit()
    return stats


# ==================================================
# 输出
# ==================================================

def games_for_per_general(per_general, pool_size, seats):
    """让**每名**武将至少出场 ``per_general`` 次所需的局数。"""

    if per_general <= 0 or seats <= 0:
        return 0
    return int(math.ceil(per_general * pool_size / float(seats)))


SIDE_LABELS = {"lord": "主公", "loyalist": "忠臣", "rebel": "反贼",
               "renegade": "内奸"}


def report_identity(stats, registry, *, players, top=15):
    """身份局口径：先给阵营基准，再给"每个武将在每个身份下"的胜率。"""

    print()
    print("=" * 78)
    print("身份局（%d 人 · 全 AI · %d 局）——与官方身份局胜率同口径"
          % (players, stats.games))
    print("=" * 78)
    print("  阵营基准：")
    for identity, label in SIDE_LABELS.items():
        cell = stats.side.get(identity)
        if not cell or not cell["games"]:
            continue
        print("    %-4s %5d 局  胜率 %6.2f%%"
              % (label, cell["games"], cell["won"] / cell["games"] * 100))
    for identity, label in SIDE_LABELS.items():
        rows = []
        for key, cell in stats.identity.items():
            general_id, _, name = key.partition("|")
            if name != identity or cell["games"] < 20:
                continue
            rows.append((cell["won"] / cell["games"], general_id, cell))
        if not rows:
            continue
        rows.sort(reverse=True)
        print()
        print("  %s 身份（出场 ≥20 局）胜率前 %d 名："
              % (label, min(top, len(rows))))
        for rank, (rate, general_id, cell) in enumerate(rows[:top], start=1):
            general = registry.get(general_id)
            print("    %3d  %-18s %5d 局  %5d 胜  %6.2f%%"
                  % (rank, general.name if general else general_id,
                     cell["games"], cell["won"], rate * 100))
    return stats


def dump_identity(path, stats, registry):
    rows = []
    for key, cell in sorted(stats.identity.items()):
        general_id, _, identity = key.partition("|")
        general = registry.get(general_id)
        rows.append({
            "general_id": general_id,
            "name": general.name if general is not None else general_id,
            "identity": identity,
            "games": cell["games"],
            "won": cell["won"],
            "winrate": round(cell["won"] / max(1, cell["games"]), 4),
        })
    sides = {identity: {"games": cell["games"], "won": cell["won"]}
             for identity, cell in stats.side.items()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"games": stats.games, "sides": sides, "generals": rows},
                  handle, ensure_ascii=False, indent=2)
    print("  写出", path)


def report(stats, registry, *, title, players, top, baseline=None):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    ranked = sorted(
        stats.played,
        key=lambda general_id: (-stats.rate(general_id), -stats.played[general_id]))
    base = baseline if baseline is not None else (1.0 / max(1, players))
    print("  基准胜率 = 1/%d = %.2f%%（完全公平时每个人都该接近它）" % (players, base * 100))
    print()
    print("  排名  武将                势力  体力  出场  胜场   胜率    相对基准")
    print("  " + "-" * 72)
    for rank, general_id in enumerate(ranked[:top], start=1):
        general = registry.get(general_id)
        name = general.name if general is not None else general_id
        kingdom = general.kingdom_name if general is not None else "?"
        hp = general.max_hp if general is not None else 0
        rate = stats.rate(general_id)
        print("  %4d  %-18s  %-4s  %3d  %5d  %5d  %6.2f%%  %5.2fx"
              % (rank, name, kingdom, hp, stats.played[general_id],
                 stats.won[general_id], rate * 100, rate / base if base else 0))
    print()
    print("  完成 %d 局，未计入胜负：超时 %d · 卡住 %d · 引擎异常 %d"
          % (stats.games, stats.unfinished, stats.stuck, sum(stats.errors.values())))
    if stats.errors:
        for reason, count in sorted(stats.errors.items(), key=lambda item: -item[1])[:5]:
            print("    %-28s %d" % (reason, count))
    print("  平均每局 %.0f 个引擎步" % (stats.steps / max(1, stats.games)))
    if stats.played:
        print("  出场次数：最少 %d 次 / 最多 %d 次 / 共 %d 名武将"
              % (min(stats.played.values()), max(stats.played.values()),
                 len(stats.played)))
    return ranked


def _path_for(out, block, mode):
    """单个模式的输出文件名；一次跑两种模式时各自带模式后缀，不会互相覆盖。"""

    if mode == block:
        return out
    return out[:-5] + "_" + block + ".json" if out.endswith(".json") else out + "_" + block


def dump_pairs(path, stats, registry):
    """自由混战的"同场成对"胜率：a 与 b 同场时 a 的胜率。"""

    rows = []
    for general_id, stats_row in sorted(
            stats.played.items(),
            key=lambda item: -(stats.won[item[0]] / max(1, item[1]))):
        general = registry.get(general_id)
        versus = {}
        for opponent, cell in stats.pairs.get(general_id, {}).items():
            versus[opponent] = {
                "games": cell["games"],
                "won": cell["won"],
                "winrate": round(cell["won"] / max(1, cell["games"]), 4),
            }
        rows.append({
            "general_id": general_id,
            "name": general.name if general is not None else general_id,
            "kingdom": general.kingdom_name if general is not None else "",
            "played": stats.played[general_id],
            "won": stats.won[general_id],
            "winrate": round(stats.rate(general_id), 4),
            "vs": versus,
        })
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"games": stats.games, "generals": rows}, handle,
                  ensure_ascii=False, indent=2)
    print("  写出", path)
    return rows


def dump(path, stats, registry, *, extra=None):
    payload = {
        "games": stats.games,
        "unfinished": stats.unfinished,
        "stuck": stats.stuck,
        "errors": dict(stats.errors),
        "generals": [
            {
                "general_id": general_id,
                "name": (registry.get(general_id).name
                         if registry.get(general_id) is not None else general_id),
                "kingdom": (registry.get(general_id).kingdom_name
                            if registry.get(general_id) is not None else ""),
                "max_hp": (registry.get(general_id).max_hp
                           if registry.get(general_id) is not None else 0),
                "played": stats.played[general_id],
                "won": stats.won[general_id],
                "winrate": round(stats.rate(general_id), 4),
            }
            for general_id in sorted(stats.played,
                                     key=lambda item: -stats.rate(item))
        ],
    }
    if extra:
        payload.update(extra)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print("  写出", path)


def run_duel_matrix(pool, registry, *, per_pair=12, seed_base=910000, verbose=False):
    """1v1 全对局矩阵：每一对武将各打 ``per_pair`` 局（两边轮流坐 0 号席）。

    ``per_pair=12`` 时，每名武将会出场 83 对 × 12 局 = 996 局——正好接近
    "每个角色 1000 局"，同时给出"谁打得过谁"的完整对位表。
    """

    ids = list(pool)
    per_side = max(1, int(per_pair) // 2)
    head = {general_id: {} for general_id in ids}
    total = {general_id: {"won": 0, "played": 0} for general_id in ids}
    games = 0
    seed = seed_base
    for index, first_id in enumerate(ids):
        for second_id in ids[index + 1:]:
            for game_index in range(per_pair):
                a, b = (first_id, second_id) if game_index % 2 == 0 else (second_id, first_id)
                game = build_duel(seed, a, b)
                seed += 1
                winner, reason, steps, _stuck = play(game, verbose=verbose)
                winner_id = getattr(winner, "general_id", None)
                games += 1
                for general_id in (a, b):
                    total[general_id]["played"] += 1
                if winner_id:
                    total[winner_id]["won"] += 1
                cell = head[a].setdefault(b, {"won": 0, "played": 0})
                cell["played"] += 1
                if winner_id == a:
                    cell["won"] += 1
                mirror = head[b].setdefault(a, {"won": 0, "played": 0})
                mirror["played"] += 1
                if winner_id == b:
                    mirror["won"] += 1
                pygame.display.quit()
        if (index + 1) % 10 == 0:
            print("    ... 已完成 %d/%d 名武将作为主视角（累计 %d 局）"
                  % (index + 1, len(ids), games), flush=True)
    del per_side
    return {"games": games, "head_to_head": head, "totals": total}


def write_matrix(payload, registry, path):
    rows = []
    for general_id, stats in sorted(
            payload["totals"].items(),
            key=lambda item: -(item[1]["won"] / max(1, item[1]["played"]))):
        general = registry.get(general_id)
        rows.append({
            "general_id": general_id,
            "name": general.name if general is not None else general_id,
            "kingdom": general.kingdom_name if general is not None else "",
            "max_hp": general.max_hp if general is not None else 0,
            "played": stats["played"],
            "won": stats["won"],
            "winrate": round(stats["won"] / max(1, stats["played"]), 4),
            "vs": {
                opponent: {
                    "won": cell["won"],
                    "played": cell["played"],
                    "winrate": round(cell["won"] / max(1, cell["played"]), 4),
                }
                for opponent, cell in payload["head_to_head"][general_id].items()
            },
        })
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"games": payload["games"], "generals": rows}, handle,
                  ensure_ascii=False, indent=2)
    print("  写出", path)
    return rows


def main():
    parser = argparse.ArgumentParser(description="武将胜率统计（全 AI 自动对战）")
    parser.add_argument("--mode", choices=("ffa", "duel", "both", "identity"),
                        default="both")
    parser.add_argument("--games", type=int, default=5000, help="每种模式各多少局")
    parser.add_argument("--matrix", type=int, default=0,
                        help="1v1 全对局矩阵：每一对武将打多少局（0 = 不跑）")
    parser.add_argument("--per-general", type=int, default=0,
                        help="改成：每名武将至少出场多少次（覆盖 --games）")
    parser.add_argument("--players", type=int, default=5, help="自由混战人数")
    parser.add_argument("--seed", type=int, default=424242, help="起始随机种子")
    parser.add_argument("--top", type=int, default=15, help="排行榜条数")
    parser.add_argument("--out", default="docs/reports/general_winrate.json")
    parser.add_argument("--progress", type=int, default=1000, help="每多少局打点")
    parser.add_argument("--verbose", action="store_true", help="打印异常 / 卡住局的现场")
    args = parser.parse_args()

    pygame.display.init()
    game = Game()
    registry = game.generals
    pool = tuple(game.playable_general_ids(for_random=True))
    if args.per_general:
        args.games = games_for_per_general(args.per_general, len(pool),
                                           2 if args.mode == "duel" else args.players)
        print("目标：每名武将至少 %d 局 → 需要 %d 局/模式"
              % (args.per_general, args.games))
    print("可用武将 %d 名 · 模式 %s · 每种 %d 局 · 随机种子 %d"
          % (len(pool), args.mode, args.games, args.seed))
    started = time.time()
    extra = {"pool_size": len(pool), "players": args.players}

    if args.mode in ("ffa", "both", "identity"):
        is_identity = args.mode == "identity"
        stats = run_ffa(args.games, args.players, args.seed,
                        progress=args.progress, verbose=args.verbose,
                        pool=pool, balanced=bool(args.per_general),
                        mode="identity" if is_identity else "ffa")
        if is_identity:
            report_identity(stats, registry, players=args.players,
                            top=args.top)
            dump_identity(_path_for(args.out, "identity", args.mode), stats,
                          registry)
            print()
            print("总耗时 %.1f 秒" % (time.time() - started))
            return 0
        report(stats, registry, title="自由混战（无身份 · %d 人 · %d 局 · 全 AI）"
               % (args.players, args.games), players=args.players, top=args.top)
        dump(_path_for(args.out, "ffa", args.mode), stats, registry, extra=extra)
        dump_pairs(_path_for(args.out, "ffa", args.mode).replace(
            ".json", "_matrix.json"), stats, registry)

    if args.mode in ("duel", "both"):
        stats = run_duel(args.games, args.seed + 7_000_000, pool,
                         progress=args.progress, verbose=args.verbose,
                         balanced=bool(args.per_general))
        report(stats, registry, title="1v1（%d 局 · 双方 AI · 随机配对）" % args.games,
               players=2, top=args.top)
        dump(_path_for(args.out, "duel", args.mode), stats, registry, extra=extra)

    if args.matrix:
        per = int(args.matrix)
        pairings = len(pool) * (len(pool) - 1) // 2
        print()
        print("1v1 全对局矩阵：%d 对 × %d 局 = %d 局（每名武将约 %d 局）"
              % (pairings, per, pairings * per, pairings * per * 2 // len(pool)))
        payload = run_duel_matrix(pool, registry, per_pair=per,
                                  verbose=args.verbose)
        rows = write_matrix(payload, registry,
                            args.out.replace(".json", "_duel_matrix.json"))
        print()
        print("=" * 78)
        print("1v1 总胜率（每名武将 %d 局，对上全部对手）" % (rows[0]["played"],))
        print("=" * 78)
        for rank, row in enumerate(rows[:args.top], start=1):
            print("  %4d  %-18s %-4s  %4d 局  %4d 胜  %6.2f%%"
                  % (rank, row["name"], row["kingdom"], row["played"],
                     row["won"], row["winrate"] * 100))
        print("  ...")
        for row in rows[-3:]:
            print("  %4s  %-18s %-4s  %4d 局  %4d 胜  %6.2f%%"
                  % ("末", row["name"], row["kingdom"], row["played"],
                     row["won"], row["winrate"] * 100))

    print()
    print("总耗时 %.1f 秒" % (time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
