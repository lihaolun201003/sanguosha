"""五人身份局深度胜率：每名武将 × 每个身份 各 1000 局。

与 ``tools/general_winrate.py --mode identity`` 的口径完全一致（胜率 = 该角色
所在阵营获胜），区别只在样本量：那边是"每名武将**出场** 1000 次"，摊到每个
身份只剩约 200 局；这里是"每名武将在**每个身份下** 1000 局"。

因此需要 ``84 名武将 × 1000 局 = 84000 局``。主公 / 忠臣 / 内奸每局各 1 名，
反贼每局 2 名，所以反贼天然拿到 2000 局/将，是别的身份的两倍。

为了让"武将 × 身份"的交叉样本**精确相等**，而不是随机发牌下的 ±3% 波动，
本工具用配额调度：

* 每名武将在每个身份下持有固定数量的 token，逐局发牌、发完即止；
* 身份仍按真实规则**随机打乱后分给座位**——绝不能固定"座位 0 = 主公"，
  否则座次（距离环）会与身份混淆，统计出来的就不是武将强度了；
* 同一局的 5 名武将不重复（与 ``assign_generals`` 的约束一致）。

配额调度与对局结果无关，所以计划可以先在主子进程里一次性算好，再按局号
切块丢给多个进程并行跑——每条样本仍然来自一次真实开局、真实打到底。

用法：

    python tools/general_identity_winrate.py                      # 默认 1000 局/身份/将
    python tools/general_identity_winrate.py --per-identity 200   # 冒烟测试
    python tools/general_identity_winrate.py --jobs 12            # 并行进程数
"""

import argparse
import json
import os
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame                                                    # noqa: E402

from src.game import Game                                        # noqa: E402
from src.game.identity import Identity                           # noqa: E402
from tools.general_winrate import Stats, _all_ai, play           # noqa: E402

SEATS = 5
#: 五人身份局的配比：1 主公 + 1 忠臣 + 2 反贼 + 1 内奸。
LAYOUT = (("lord", 1), ("loyalist", 1), ("rebel", 2), ("renegade", 1))
SIDE_LABELS = {"lord": "主公", "loyalist": "忠臣",
               "rebel": "反贼", "renegade": "内奸"}
SIDE_ORDER = ("lord", "loyalist", "rebel", "renegade")
SIDE_REASONS = Stats.SIDE_REASONS      # 唯一口径来源，和单进程统计共用
WIN_REASON = {"LORD_SIDE_WIN": "主忠", "REBEL_WIN": "反贼",
              "RENEGADE_WIN": "内奸"}


# ==================================================
# 一、配额调度：算出每局发谁、谁是什么身份
# ==================================================

def _take(tokens, used):
    """从池尾取一个没有被本局占用的武将；被挡住的 token 原样放回。

    放回保证 token 总数守恒——所以只要池子按需求配好，每名武将的每个身份
    就**恰好**发够份数，不会因为同局去重而少发谁。
    """

    stash = []
    picked = None
    while tokens:
        candidate = tokens.pop()
        if candidate in used:
            stash.append(candidate)
            continue
        picked = candidate
        break
    tokens.extend(stash)
    return picked


def build_plan(games, pool, per_identity, seed):
    """生成每局计划：``(seed, 座位顺序的身份, 座位顺序的武将)``。

    确定性 + 可复现：同一个 seed 永远得到同一份计划。
    """

    rng = random.Random(seed)
    # 反贼每局 2 名，所以配额也是别的身份的两倍，一局正好用掉 2 个。
    quota = {"lord": per_identity, "loyalist": per_identity,
             "rebel": per_identity * 2, "renegade": per_identity}
    pools = {}
    for identity, count in quota.items():
        tokens = [general_id for general_id in pool for _ in range(count)]
        rng.shuffle(tokens)
        pools[identity] = tokens

    # 座位上的身份：打乱的是"谁坐哪个身份"，不是身份配比本身。
    seat_template = []
    for identity, count in LAYOUT:
        seat_template.extend([identity] * count)

    plan = []
    for index in range(games):
        used = set()
        buckets = {}
        for identity, count in LAYOUT:
            drawn = []
            for _ in range(count):
                general_id = _take(pools[identity], used)
                if general_id is None:
                    raise RuntimeError(
                        "配额不足：第 %d 局抽 %s 时池子已空" % (index, identity))
                drawn.append(general_id)
                used.add(general_id)
            buckets[identity] = drawn
        seats = list(seat_template)
        rng.shuffle(seats)
        for drawn in buckets.values():
            rng.shuffle(drawn)
        picks = [buckets[identity].pop() for identity in seats]
        plan.append((seed + index, tuple(seats), tuple(picks)))
    return plan


def audit_plan(plan, pool, per_identity):
    """自查：每名武将的每个身份是不是恰好发够了份数、有没有一局重复。

    反贼每局 2 名，所以它的预期份数本来就是别的身份的两倍——这不是不均衡。
    """

    expected = {identity: per_identity * count for identity, count in LAYOUT}
    cell = defaultdict(int)
    for _seed, seats, picks in plan:
        if len(set(picks)) != SEATS:
            raise AssertionError("同一局里出现了重复武将：%s" % (picks,))
        if sorted(seats) != sorted(
                identity for identity, count in LAYOUT for _ in range(count)):
            raise AssertionError("身份配比不是 1 主 1 忠 2 反 1 内：%s" % (seats,))
        for identity, general_id in zip(seats, picks):
            if general_id not in pool:
                raise AssertionError("发出了不在池里的武将：%s" % general_id)
            cell[(general_id, identity)] += 1
    for general_id in pool:
        for identity in SIDE_ORDER:
            count = cell.get((general_id, identity), 0)
            if count != expected[identity]:
                raise AssertionError(
                    "配额不均衡：%s 的 %s 发了 %d 局，应为 %d 局"
                    % (general_id, identity, count, expected[identity]))
    return expected


# ==================================================
# 二、跑一局
# ==================================================

def play_planned(item, pool):
    """按计划开一局真实身份局并打到底，返回这一局的原始计数。"""

    seed, seats, picks = item
    game = Game(ai_count=SEATS - 1)
    game.ai_pacing = True
    game.rng.seed(seed)
    game.set_mode("identity")
    # 座位 → 身份、座位 → 武将都显式注入；开局修正（主公 +1 体力上限、
    # 主公先手）仍由模式自己按真实规则执行。
    #
    # 身份必须是 Identity 枚举，不能是它的字符串值：模式找主公用的是
    # `is Identity.LORD`，喂字符串会找不到主公（主公修正与先手一起失效），
    # 而真实流程的 roll_identities() 本来就是枚举。
    game.pending_identities = tuple(Identity(item_) for item_ in seats)
    game.general_pool = pool
    game.general_assignments = {seat: picks[seat] for seat in range(SEATS)}
    game.start_local_battle(SEATS - 1)
    _all_ai(game)

    _winner, reason, steps, stuck = play(game)

    cells = {}
    sides = defaultdict(int)
    for player in game.players:
        # 这里刻意不写"读不到就跳过"：身份或武将读不到说明注入失效了，
        # 跳过只会把统计悄悄变成一堆 0，还不如当场炸掉。
        if player.identity is None:
            raise AssertionError("身份局里出现了没有身份的玩家：座位 %s" % player.seat)
        if not player.general_id:
            raise AssertionError("身份局里出现了没有武将的玩家：座位 %s" % player.seat)
        identity = player.identity.value
        sides[identity] += 1
        # 与官方身份局同口径：该角色所在阵营赢了，就算他赢。
        won = bool(reason and reason in SIDE_REASONS[identity])
        cell = cells.setdefault("%s|%s" % (player.general_id, identity), [0, 0])
        cell[0] += 1
        if won:
            cell[1] += 1

    pygame.display.quit()
    return {"steps": steps, "stuck": bool(stuck), "reason": reason,
            "cells": cells, "sides": dict(sides)}


def self_check(pool, registry):
    """开局自检：注入的身份 / 武将真的生效、主公修正与先手真的落地。

    身份与武将是绕过公开流程直接注入的，这类做法一旦悄悄失效，跑出来的
    不是"有一点偏差"的数字，而是一整批假数据（身份读不到 → 统计全 0），
    所以正式开跑前先验一局。
    """

    seed, seats, picks = build_plan(1, pool, 1, 1)[0]
    game = Game(ai_count=SEATS - 1)
    game.ai_pacing = True
    game.rng.seed(seed)
    game.set_mode("identity")
    game.pending_identities = tuple(Identity(item) for item in seats)
    game.general_pool = pool
    game.general_assignments = {seat: picks[seat] for seat in range(SEATS)}
    game.start_local_battle(SEATS - 1)

    order = sorted(game.players, key=lambda player: player.seat)
    for player in order:
        if not isinstance(player.identity, Identity):
            raise AssertionError("注入的身份没有生效：%r" % (player.identity,))
        if player.identity.value != seats[player.seat]:
            raise AssertionError("座位 %d 的身份是 %s，应为 %s"
                                 % (player.seat, player.identity.value,
                                    seats[player.seat]))
        if player.general_id != picks[player.seat]:
            raise AssertionError("座位 %d 的武将是 %s，应为 %s"
                                 % (player.seat, player.general_id,
                                    picks[player.seat]))

    lord = next((item for item in order if item.identity is Identity.LORD), None)
    if lord is None:
        raise AssertionError("开局里找不到主公")
    general = registry.get(lord.general_id)
    if general is not None and lord.max_hp != general.max_hp + 1:
        raise AssertionError("主公体力上限没有 +1：%d（%s 上限 %d）"
                             % (lord.max_hp, general.name, general.max_hp))
    if game.current_player_id != lord.player_id:
        raise AssertionError("先手不是主公：%s" % game.current_player_id)
    pygame.display.quit()
    return "座位映射 · 主公 +1 体力上限 · 主公先手"


def _empty_total():
    return {"games": 0, "steps": 0, "stuck": 0, "unfinished": 0,
            "sides": defaultdict(int), "cells": {},
            "reasons": defaultdict(int)}


def _merge_into(total, chunk):
    """把一片的原始计数并进总量。"""

    for field in ("games", "steps", "stuck", "unfinished"):
        total[field] += chunk[field]
    for bucket in ("sides", "reasons"):
        for key, count in chunk[bucket].items():
            total[bucket][key] += count
    for key, cell in chunk["cells"].items():
        into = total["cells"].setdefault(key, [0, 0])
        into[0] += cell[0]
        into[1] += cell[1]
    return total


def _worker(payload):
    """子进程：跑完自己那一段计划，回传原始计数（不做任何取舍）。"""

    pool, chunk_index, chunk = payload
    total = _empty_total()
    for item in chunk:
        outcome = play_planned(item, pool)
        total["games"] += 1
        total["steps"] += outcome["steps"]
        if outcome["stuck"]:
            total["stuck"] += 1
        reason = outcome["reason"] or "NONE"
        total["reasons"][reason] += 1
        if reason not in WIN_REASON:
            total["unfinished"] += 1
        for identity, count in outcome["sides"].items():
            total["sides"][identity] += count
        for key, cell in outcome["cells"].items():
            into = total["cells"].setdefault(key, [0, 0])
            into[0] += cell[0]
            into[1] += cell[1]
    return chunk_index, total


# ==================================================
# 三、汇总与输出
# ==================================================

def summarise(total, registry):
    """把原始计数整理成"每名武将 × 每个身份"的明细与各身份的排行榜。"""

    table = {}
    for identity in SIDE_ORDER:
        rows = []
        for key, cell in total["cells"].items():
            general_id, _, name = key.partition("|")
            if name != identity:
                continue
            general = registry.get(general_id)
            rows.append({
                "general_id": general_id,
                "name": general.name if general else general_id,
                "kingdom": general.kingdom_name if general else "",
                "hp": general.max_hp if general else 0,
                "games": cell[0], "won": cell[1],
                "rate": cell[1] / max(1, cell[0]),
            })
        rows.sort(key=lambda row: row["rate"], reverse=True)
        rates = [row["rate"] for row in rows]
        table[identity] = {
            "rows": rows,
            "average": sum(rates) / max(1, len(rates)),
            "median": sorted(rates)[len(rates) // 2] if rates else 0.0,
        }

    # 跨身份总榜：每将 5000 局，权重与身份局真实配比一致（反贼是别的两倍）。
    merged = {}
    for identity in SIDE_ORDER:
        for row in table[identity]["rows"]:
            cell = merged.setdefault(row["general_id"], {
                "general_id": row["general_id"], "name": row["name"],
                "kingdom": row["kingdom"], "hp": row["hp"],
                "games": 0, "won": 0})
            cell["games"] += row["games"]
            cell["won"] += row["won"]
    overall = list(merged.values())
    for cell in overall:
        cell["rate"] = cell["won"] / max(1, cell["games"])
    overall.sort(key=lambda row: row["rate"], reverse=True)
    return table, overall


def report(total, table, overall, *, per_identity, top, bottom):
    games = total["games"]
    print()
    print("=" * 78)
    print("五人身份局深度胜率（每名武将 × 每个身份 %d 局 · 全 AI · 共 %d 局）"
          % (per_identity, games))
    print("=" * 78)
    print("  阵营基准（该身份的全部样本）：")
    for identity in SIDE_ORDER:
        played = total["sides"].get(identity, 0)
        won = sum(cell[1] for key, cell in total["cells"].items()
                  if key.endswith("|" + identity))
        print("    %-4s %6d 局  胜率 %6.2f%%"
              % (SIDE_LABELS[identity], played, won / max(1, played) * 100))
    for identity in SIDE_ORDER:
        cell = table[identity]
        print()
        print("  %s（全场均值 %.2f%% · 中位 %.2f%%）胜率前 %d："
              % (SIDE_LABELS[identity], cell["average"] * 100,
                 cell["median"] * 100, min(top, len(cell["rows"]))))
        for number, row in enumerate(cell["rows"][:top], start=1):
            print("    %3d  %-16s %-2s %5d 局  %5d 胜  %6.2f%%"
                  % (number, row["name"], row["kingdom"], row["games"],
                     row["won"], row["rate"] * 100))
        print("    ... 末 %d：" % min(bottom, len(cell["rows"])))
        for row in cell["rows"][-bottom:]:
            print("    %-16s %-2s %5d 局  %5d 胜  %6.2f%%"
                  % (row["name"], row["kingdom"], row["games"], row["won"],
                     row["rate"] * 100))
    print()
    print("  跨身份总榜前 %d（每将 5000 局）：" % min(top, len(overall)))
    for number, row in enumerate(overall[:top], start=1):
        print("    %3d  %-16s %-2s %5d 局  %6.2f%%"
              % (number, row["name"], row["kingdom"], row["games"],
                 row["rate"] * 100))
    print()
    print("  平均每局 %.0f 个引擎步 · 卡住 %d · 未分胜负 %d"
          % (total["steps"] / max(1, games), total["stuck"], total["unfinished"]))
    print("  胜负原因：", dict(sorted(total["reasons"].items(),
                                     key=lambda item: -item[1])))
    return table, overall


def dump_json(path, total, table, overall, *, per_identity, pool_size):
    payload = {
        "per_identity": per_identity,
        "pool_size": pool_size,
        "games": total["games"],
        "steps": total["steps"],
        "stuck": total["stuck"],
        "unfinished": total["unfinished"],
        "reasons": dict(sorted(total["reasons"].items(),
                               key=lambda item: -item[1])),
        "sides": {
            identity: {
                "games": total["sides"].get(identity, 0),
                "won": sum(cell[1] for key, cell in total["cells"].items()
                           if key.endswith("|" + identity))}
            for identity in SIDE_ORDER},
        "generals": [
            {"general_id": row["general_id"], "name": row["name"],
             "kingdom": row["kingdom"], "identity": identity,
             "games": row["games"], "won": row["won"],
             "winrate": round(row["rate"], 4)}
            for identity in SIDE_ORDER for row in table[identity]["rows"]],
        "overall": [
            {"general_id": row["general_id"], "name": row["name"],
             "kingdom": row["kingdom"], "games": row["games"],
             "won": row["won"], "winrate": round(row["rate"], 4)}
            for row in overall],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print("  写出", path)


def _table(rows, start=1):
    """排行榜表格；``start`` 是首行的名次（末位表接着总排名数下去）。"""

    lines = ["| 排名 | 武将 | 势力 | 局数 | 胜场 | 胜率 |",
             "| ---: | --- | --- | ---: | ---: | ---: |"]
    for number, row in enumerate(rows, start=start):
        lines.append("| %d | %s | %s | %d | %d | %.1f%% |"
                     % (number, row["name"], row["kingdom"], row["games"],
                        row["won"], row["rate"] * 100))
    return lines


def _corr(pairs):
    """皮尔逊相关系数；样本不足或方差为 0 时返回 0。"""

    if len(pairs) < 2:
        return 0.0
    xs = [first for first, _second in pairs]
    ys = [second for _first, second in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = (sum((x - mx) ** 2 for x in xs)
                   * sum((y - my) ** 2 for y in ys)) ** 0.5
    return numerator / denominator if denominator else 0.0


def cross_check(table, overall, path):
    """拿自由混战的胜率对一遍：同一批武将在两套规则下的排名应该一致。

    这是数据的自洽性检查——如果身份局的数字和已有的混战统计毫无关系，
    多半说明身份或武将根本没注入进去。文件不存在就跳过。
    """

    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        ffa = json.load(handle)
    rates = {row["general_id"]: row["winrate"]
             for row in ffa.get("generals", ())}
    by_identity = {identity: {row["general_id"]: row["rate"]
                              for row in table[identity]["rows"]}
                   for identity in SIDE_ORDER}
    common = [row["general_id"] for row in overall if row["general_id"] in rates]
    if len(common) < 2:
        return None
    own = {row["general_id"]: row["rate"] for row in overall}
    report_ = {"pool": len(common),
               "overall": _corr([(own[g], rates[g]) for g in common])}
    for identity in SIDE_ORDER:
        report_[identity] = _corr([(by_identity[identity][g], rates[g])
                                   for g in common if g in by_identity[identity]])
    return report_


def dump_markdown(path, total, table, overall, *, per_identity, pool_size,
                  cross=None, top=40, bottom=5):
    games = total["games"]
    error = (0.25 / per_identity) ** 0.5 * 100
    sections = "三四五六"
    lines = ["# 五人身份局：武将 × 身份 深度胜率", "",
             "工具：`tools/general_identity_winrate.py`。全 AI 自动对战。", "",
             "## 一、口径", "",
             "* **五人身份局**（1 主公 · 1 忠臣 · 2 反贼 · 1 内奸），"
             "五个座位全部由 AI 操作。",
             "* **每名武将在每个身份下 %d 局**：%d 名武将共 **%d 局**。"
             % (per_identity, pool_size, games),
             "  反贼每局 2 名，所以反贼是 **%d 局/将**（别的身份的两倍），"
             "每将总出场 %d 局。" % (per_identity * 2, per_identity * 5),
             "* **胜率口径**：该角色**所在阵营**获胜即算赢——主公/忠臣看主忠胜、"
             "反贼看反贼胜、内奸看内奸胜，与官方身份局胜率同一口径。",
             "* **身份随机分座**：身份牌打乱后分给座位，座次（距离环）与身份不"
             "构成伪相关；武将按配额精确发放，每格样本量完全相等，没有发牌运气。",
             "* **可复现性的边界**：牌堆、判定、技能这些**规则内**的随机全部由固定"
             "种子驱动；但 AI「打破平局」的扰动来自 `AIController` 的 `ai_rng`，"
             "身份模式下它是 `None`（只有 1v1 会把它绑到 `game.rng`），于是退化成"
             "随机播种——同一条命令重跑得到的是**同分布的另一次采样**，逐位结果"
             "不可复现。这不影响统计（扰动对每名武将是对称的），但不要拿两次运行"
             "的逐局结果互相对照。",
             "* **参考噪声**：%d 局下胜率的二项标准误约 **%.1f 个百分点**，"
             "所以差距小于约 %.1f 个百分点的两将，不应据此判定强弱。"
             % (per_identity, error, error * 2), "",
             "## 二、阵营基准", "",
             "| 身份 | 样本局数 | 胜场 | 胜率 |", "| --- | ---: | ---: | ---: |"]
    for identity in SIDE_ORDER:
        played = total["sides"].get(identity, 0)
        won = sum(cell[1] for key, cell in total["cells"].items()
                  if key.endswith("|" + identity))
        lines.append("| %s | %d | %d | %.2f%% |"
                     % (SIDE_LABELS[identity], played, won,
                        won / max(1, played) * 100))
    lines.append("")
    for index, identity in enumerate(SIDE_ORDER):
        cell = table[identity]
        rows = cell["rows"]
        lines.append("## %s、%s（%d 名武将）"
                     % (sections[index], SIDE_LABELS[identity], len(rows)))
        lines.append("")
        lines.append("全场均值 **%.2f%%** · 中位 %.2f%% · 末位 %.2f%%。"
                     % (cell["average"] * 100, cell["median"] * 100,
                        rows[-1]["rate"] * 100 if rows else 0))
        lines.append("")
        lines.extend(_table(rows[:top]))
        if len(rows) > top + bottom:
            lines.append("| … | | | | | |")
        tail = rows[-bottom:] if bottom else []
        lines.extend(_table(tail, start=len(rows) - len(tail) + 1))
        lines.append("")
    lines.append("## 七、跨身份总榜（每将 %d 局）" % (per_identity * 5))
    lines.append("")
    lines.extend(_table(overall))
    lines.append("")
    lines.append("## 八、运行状态")
    lines.append("")
    lines.append("* 共 %d 局，平均每局 %.0f 个引擎步。"
                 % (games, total["steps"] / max(1, games)))
    lines.append("* 卡住 %d 局 · 未分胜负 %d 局。"
                 % (total["stuck"], total["unfinished"]))
    lines.append("* 胜负原因分布：%s" % dict(
        sorted(total["reasons"].items(), key=lambda item: -item[1])))
    lines.append("")
    if cross:
        lines.append("## 九、与自由混战的一致性")
        lines.append("")
        lines.append("拿已有的大样本自由混战（无身份）胜率对照 %d 名武将。同一武将在"
                     "两套规则下名次不同是正常的，但整体应当高度相关——相关性太低"
                     "就说明身份或武将其实没注入进去。" % cross["pool"])
        lines.append("")
        lines.append("| 口径 | 与自由混战胜率的相关系数 |")
        lines.append("| --- | ---: |")
        lines.append("| 跨身份总榜 | %.3f |" % cross["overall"])
        for identity in SIDE_ORDER:
            lines.append("| %s | %.3f |"
                         % (SIDE_LABELS[identity], cross[identity]))
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("  写出", path)


def load_report(path):
    """从已写出的 JSON 重建报告结构——改报告格式时不必重跑几万局。"""

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    table = {}
    for identity in SIDE_ORDER:
        rows = [{"general_id": row["general_id"], "name": row["name"],
                 "kingdom": row["kingdom"], "games": row["games"],
                 "won": row["won"], "rate": row["winrate"]}
                for row in payload["generals"] if row["identity"] == identity]
        rows.sort(key=lambda row: row["rate"], reverse=True)
        rates = [row["rate"] for row in rows]
        table[identity] = {
            "rows": rows,
            "average": sum(rates) / max(1, len(rates)),
            "median": sorted(rates)[len(rates) // 2] if rates else 0.0}
    overall = [{"general_id": row["general_id"], "name": row["name"],
                "kingdom": row["kingdom"], "games": row["games"],
                "won": row["won"], "rate": row["winrate"]}
               for row in payload["overall"]]
    # dump_markdown 只读 total 的这几个字段，按它的形状补齐即可。
    total = {
        "games": payload["games"],
        "steps": payload.get("steps", 0),
        "stuck": payload.get("stuck", 0),
        "unfinished": payload.get("unfinished", 0),
        "reasons": payload.get("reasons", {}),
        "sides": {identity: side["games"]
                  for identity, side in payload["sides"].items()},
        "cells": {"|" + identity: [side["games"], side["won"]]
                  for identity, side in payload["sides"].items()},
    }
    return payload, total, table, overall


# ==================================================
# 四、主流程
# ==================================================

def main():
    parser = argparse.ArgumentParser(description="五人身份局：每名武将 × 每个身份 的胜率")
    parser.add_argument("--per-identity", type=int, default=1000,
                        help="每名武将在每个身份下打多少局")
    parser.add_argument("--jobs", type=int, default=0,
                        help="并行进程数（默认取 CPU 核数的 2/3）")
    parser.add_argument("--seed", type=int, default=880000, help="起始随机种子")
    parser.add_argument("--top", type=int, default=20, help="控制台排行榜条数")
    parser.add_argument("--bottom", type=int, default=5, help="控制台末位条数")
    parser.add_argument("--out", default="docs/reports/general_winrate_identity_deep.json")
    parser.add_argument("--md", default="docs/reports/general_winrate_identity_deep_v01.md")
    parser.add_argument("--render-from", default="",
                        help="不跑对局，直接从已有 JSON 重新生成 Markdown 报告")
    parser.add_argument("--cross-check",
                        default="docs/reports/general_winrate_ffa.json",
                        help="自洽性对照用的自由混战胜率 JSON（不存在就跳过该节）")
    args = parser.parse_args()

    if args.render_from:
        payload, total, table, overall = load_report(args.render_from)
        dump_markdown(args.md, total, table, overall,
                      per_identity=payload["per_identity"],
                      pool_size=payload["pool_size"],
                      cross=cross_check(table, overall, args.cross_check))
        return 0

    pygame.display.init()
    probe = Game()
    registry = probe.generals
    pool = tuple(probe.playable_general_ids(for_random=True))
    del probe

    games = len(pool) * args.per_identity
    jobs = args.jobs or max(1, min(12, (os.cpu_count() or 4) * 2 // 3))
    print("武将池 %d 名 · 每人每身份 %d 局 · 需要 %d 局 · 并行 %d 进程"
          % (len(pool), args.per_identity, games, jobs))

    started = time.time()
    plan = build_plan(games, pool, args.per_identity, args.seed)
    expected = audit_plan(plan, pool, args.per_identity)
    print("  调度自查通过：主公/忠臣/内奸各 %d 局/将、反贼 %d 局/将，"
          "同局无重复武将（%.1f 秒）"
          % (expected["lord"], expected["rebel"], time.time() - started))
    print("  开局自检通过：%s（%.1f 秒）"
          % (self_check(pool, registry), time.time() - started))

    size = (games + jobs - 1) // jobs
    chunks = [(index // size, plan[index:index + size])
              for index in range(0, games, size)]
    print("  切成 %d 片，开始并行推进" % len(chunks))

    total = _empty_total()
    done = 0
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        futures = [executor.submit(_worker, (pool, index, chunk))
                   for index, chunk in chunks]
        for future in as_completed(futures):
            _index, chunk_total = future.result()
            _merge_into(total, chunk_total)
            done += chunk_total["games"]
            print("    ... %d/%d 局（%.0f 秒）"
                  % (done, games, time.time() - started))

    table, overall = summarise(total, registry)
    report(total, table, overall, per_identity=args.per_identity,
           top=args.top, bottom=args.bottom)
    dump_json(args.out, total, table, overall,
              per_identity=args.per_identity, pool_size=len(pool))
    dump_markdown(args.md, total, table, overall,
                  per_identity=args.per_identity, pool_size=len(pool),
                  cross=cross_check(table, overall, args.cross_check))
    print()
    print("总耗时 %.1f 秒（%.1f 分钟）" % (time.time() - started,
                                          (time.time() - started) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
