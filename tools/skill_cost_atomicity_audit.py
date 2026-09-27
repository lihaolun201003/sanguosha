"""技能费用的引擎边界自检：非法提交必须在任何状态变化之前被拒绝。

    .venv\\Scripts\\python.exe tools/skill_cost_atomicity_audit.py

为什么要有这个工具：``ActivateSkillAction`` 是**引擎入口**，不只服务真人界面。
LAN 客户端、脚本、批量试玩、未来的 replay 都能直接构造它，所以"界面上点不出
非法组合"不是安全边界。这里绕过所有界面，直接往引擎提交非法 payload，验收两件
事：

1. **不抛未捕获异常**——无效客户端输入不能把房主引擎打崩；
2. **不产生任何状态变化**——手牌 / 装备区 / 弃牌堆 / 技能次数 / 标记一个都不许动。

历史缺陷（本次修复的对象）：同一张实体牌在费用里出现两次时，"先全部定位、
再全部支付"两次都能定位成功（两次问的都是**同一张还在手牌里的牌**），于是
第一次支付真的把它弃掉、第二次抛 ``ValueError``：玩家白丢一张牌，技能没发动，
次数也没消耗。修法是支付前按**实体身份**去重（见 ``activation.plan_activation``）。

退出码 0 = 全部通过。
"""

import io
import os
import sys
import traceback

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.card import Card                                              # noqa: E402
from src.game import Game                                              # noqa: E402
from src.game.engine import ActivateSkillAction                        # noqa: E402
from src.game.skills.mechanics import add_mark, mark_count             # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("[通过]" if ok else "[失败]", name,
                       ("  —— " + detail) if detail else ""), flush=True)
    return bool(ok)


# ==================================================
# 局面
# ==================================================

def card(name, suit="spade", rank="7", **kwargs):
    category = kwargs.pop("category", None)
    if category is None:
        if name in ("SHA", "SHAN", "TAO", "JIU"):
            category = "basic"
        elif name in ("SHANDIAN", "LEBU", "BINGLIANG"):
            category = "delayed_trick"
        elif name in ("ZHUGE", "BAGUA", "ZHANGBA", "CIXIONG"):
            category = "equipment"
        else:
            category = "trick"
    subtype = None
    for slot, names in (("weapon", ("ZHUGE", "ZHANGBA", "CIXIONG")),
                        ("armor", ("BAGUA",))):
        if name in names:
            subtype = slot
    return Card(name=name, category=category, color=(200, 190, 160), suit=suit,
                rank=rank, nature="normal", subtype=subtype)


def make_game(general="sunquan", seed=1, others=2):
    game = Game(ai_count=others)
    game.rng.seed(seed)
    game.scene = "general_select"
    game.general_candidates = game.roll_general_candidates()
    game.confirm_general(game.general_candidates[0])
    game.scene = "game"
    game.set_general(game.player, general)
    game.phase = "play"
    game.current_turn_player = game.player
    for player in game.players:
        if player is not game.player:
            player.hand[:] = []
    return game


def fingerprint(game, player):
    """这名角色与牌堆的状态指纹：用于断言"一个字节都没变"。"""

    return (
        tuple(id(item) for item in player.hand),
        tuple(sorted((slot, id(item)) for slot, item in player.equipment.items()
                     if item is not None)),
        len(game.deck.discard_pile),
        tuple(sorted((key, str(value)) for key, value in
                     player.skill_state.snapshot().items())),
    )


def submit_expecting_refusal(game, action, label):
    """提交一个应当被规则拒绝的 action；返回 (ok, detail)。"""

    player = action.actor
    before = fingerprint(game, player)
    try:
        result = game.engine.submit(action)
    except Exception as error:                                        # noqa: BLE001
        return False, "抛出未捕获异常 %s: %s" % (type(error).__name__, error)
    after = fingerprint(game, player)
    ok, message = (result if isinstance(result, tuple) else (bool(result), ""))
    if ok:
        return False, "竟然被接受了"
    if before != after:
        return False, "被拒绝了但状态变了：%s -> %s" % (before, after)
    return True, "拒绝原因=%s" % (message or game.message)


# ==================================================
# 1. 制衡：重复 / stale / 区域 / 数量
# ==================================================

def group_zhiheng():
    # 1.1 手牌里同一张【杀】提交两次
    game = make_game()
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[sha, sha]),
        "duplicate hand card")
    check("1.1 同一张手牌提交两次 → 拒绝且不扣牌", ok, detail)

    # 1.2 装备区同一张【诸葛连弩】提交两次
    game = make_game()
    player = game.player
    crossbow = card("ZHUGE", "club", "A")
    player.equipment["weapon"] = crossbow
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[crossbow, crossbow]),
        "duplicate equipment")
    check("1.2 同一张装备提交两次 → 拒绝且不卸装", ok, detail)

    # 1.3 混选里夹一个重复：整次失败，两张都留在原区域
    game = make_game()
    player = game.player
    sha = card("SHA")
    crossbow = card("ZHUGE", "club", "A")
    player.hand[:] = [sha]
    player.equipment["weapon"] = crossbow
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[sha, crossbow, sha]),
        "mixed with duplicate")
    check("1.3 手牌 + 装备混选里含重复 → 整次拒绝，两张都不动", ok, detail)
    check("1.3b 那张【杀】仍在手牌、那张装备仍在槽里",
          any(item is sha for item in player.hand)
          and player.get_equipment("weapon") is crossbow,
          "hand=%s weapon=%s" % ([item.name for item in player.hand],
                                 player.get_equipment("weapon")))

    # 1.4 合法混选仍然成功（正面对照）
    game = make_game()
    player = game.player
    sha = card("SHA")
    crossbow = card("ZHUGE", "club", "A")
    player.hand[:] = [sha]
    player.equipment["weapon"] = crossbow
    game.deck.draw_pile.extend([card("TAO"), card("TAO")])
    ok = game.engine.submit(ActivateSkillAction(
        player, "zhiheng", cards=[sha, crossbow]))
    check("1.4 手牌 + 装备各一张（不重复）→ 正常发动",
          bool(ok[0] if isinstance(ok, tuple) else ok)
          and not any(item is sha for item in player.hand)
          and player.get_equipment("weapon") is None,
          "hand=%s weapon=%s" % ([item.name for item in player.hand],
                                 player.get_equipment("weapon")))

    # 1.5 stale：提交时那张牌已经不在原区域
    game = make_game()
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    crossbow = card("ZHUGE", "club", "A")
    player.equipment["weapon"] = crossbow
    player.equipment["weapon"] = None          # 状态变化：装备已经走了
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[sha, crossbow]),
        "stale equipment")
    check("1.5 stale 费用（装备已被拿走）→ 拒绝，手牌不动", ok, detail)

    # 1.6 没有牌（可变费用至少一张）
    game = make_game()
    player = game.player
    player.hand[:] = []
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[]), "zero cards")
    check("1.6 手里没牌、一张都不选 → 拒绝", ok, detail)

    # 1.6b 手里有牌却提交空费用：数量下限的判据（技能可用，是费用不合法）
    game = make_game()
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[]),
        "empty cost with cards in hand")
    check("1.6b 手里有牌但提交空费用 → 拒绝（至少一张）", ok, detail)

    # 1.7 费用牌不是实体牌（None 混进来）
    game = make_game()
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[sha, None]),
        "None card")
    check("1.7 费用里混入非牌对象 → 拒绝且不扣牌", ok, detail)

    # 1.8 两个对象、同一个牌 id（反序列化 / 恶意 payload 的形态）：
    #     两张"牌"都在手牌里、牌 id 相同 → 按实体身份判定为重复。
    game = make_game()
    player = game.player
    sha = card("SHA")
    clone = card("SHA")
    clone.id = sha.id
    player.hand[:] = [sha, clone]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "zhiheng", cards=[sha, clone]),
        "cloned identity")
    check("1.8 两个对象、同一个牌 id → 按实体身份判定为重复", ok, detail)


# ==================================================
# 2. 举荐：上限 / 目标 / 次数
# ==================================================

def group_jujian():
    # 2.1 超过"至多三张"
    game = make_game(general="xushu", seed=3)
    player = game.player
    cards = [card("SHA"), card("SHAN"), card("TAO"), card("JIU")]
    player.hand[:] = list(cards)
    target = [item for item in game.players if item is not player][0]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "jujian", target=target, cards=cards),
        "too many cards")
    check("2.1 举荐提交 4 张（规则上限 3）→ 拒绝且不弃牌", ok, detail)

    # 2.2 没有目标
    game = make_game(general="xushu", seed=3)
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "jujian", target=None, cards=[sha]),
        "no target")
    check("2.2 举荐不带目标 → 拒绝", ok, detail)

    # 2.3 目标非法（不在候选里）
    game = make_game(general="xushu", seed=3)
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "jujian", target=player, cards=[sha]),
        "self target")
    check("2.3 举荐把自己当目标 → 拒绝", ok, detail)

    # 2.4 本回合已经发动过
    game = make_game(general="xushu", seed=3)
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    player.skill_state.set("jujian", "used", 1)
    target = [item for item in game.players if item is not player][0]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "jujian", target=target, cards=[sha]),
        "already used")
    check("2.4 举荐本回合已发动过 → 拒绝", ok, detail)


# ==================================================
# 3. 授予型（黄天形态）：费用区域与目标
# ==================================================

def group_grant_shape():
    """授予型（黄天）形态的引擎入口：费用校验不能因为"技能属于别人"而绕过。

    这里手动建立授予关系（不等身份模式），重点是把**费用**打到这条入口上：
    重复费用 / 非候选牌同样只能被拒绝，且不扣牌、不写次数。
    """

    # 3.1 没有授予关系时提交：拒绝（引擎不会因为 skill_id 特殊就放行）
    game = make_game(seed=5)
    player = game.player
    other = [item for item in game.players if item is not player][0]
    sha = card("SHA")
    player.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "huangtian", target=other,
                                  cards=[sha, sha]),
        "no grant relation")
    check("3.1 没有授予关系 → 拒绝且不扣牌", ok, detail)

    # 3.2 真的授予关系（群势力角色的出牌阶段 + 持有者在场）：
    #     重复费用同样被拒绝
    game = make_game(general="lvbu", seed=11)
    actor = game.player
    owner = [item for item in game.players if item is not actor][0]
    game.skills.bind(owner, "huangtian")
    shan = card("SHAN", "diamond", "2")
    actor.hand[:] = [shan]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(actor, "huangtian", target=owner,
                                  cards=[shan, shan]),
        "granted duplicate with real relation")
    check("3.2 真授予关系下重复费用 → 拒绝且【闪】还在手里", ok, detail)

    # 3.3 候选之外的费用（黄天只吃【闪】/【闪电】）
    game = make_game(general="lvbu", seed=11)
    actor = game.player
    owner = [item for item in game.players if item is not actor][0]
    game.skills.bind(owner, "huangtian")
    sha = card("SHA")
    actor.hand[:] = [sha]
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(actor, "huangtian", target=owner,
                                  cards=[sha]),
        "granted illegal candidate")
    check("3.3 授予型技能提交非候选牌（【杀】）→ 拒绝", ok, detail)


# ==================================================
# 4. 标记不被偷偷消耗
# ==================================================

def group_marks():
    """非法提交不得消耗任何资源（【极略】的「忍」标记）。"""

    game = make_game(general="shen_simayi", seed=7)
    player = game.player
    from src.game.skills.mechanics import gain_skill

    gain_skill(game, player, "jilue")
    add_mark(game, player, "renjie", 3, "ren")
    sha = card("SHA")
    player.hand[:] = [sha]
    before_marks = mark_count(player, "renjie", "ren")
    ok, detail = submit_expecting_refusal(
        game, ActivateSkillAction(player, "jilue", cards=[sha, sha]),
        "jilue duplicate")
    check("4.1 极略·制衡：重复费用 → 拒绝", ok, detail)
    check("4.1b 「忍」标记一枚都没扣",
          mark_count(player, "renjie", "ren") == before_marks,
          "marks=%d（原 %d）" % (mark_count(player, "renjie", "ren"), before_marks))

    # 4.2 合法的极略·制衡仍然扣标记并结算
    game = make_game(general="shen_simayi", seed=7)
    player = game.player
    gain_skill(game, player, "jilue")
    add_mark(game, player, "renjie", 3, "ren")
    sha = card("SHA")
    player.hand[:] = [sha]
    game.deck.draw_pile.append(card("TAO"))
    ok = game.engine.submit(ActivateSkillAction(player, "jilue", cards=[sha]))
    check("4.2 合法的极略·制衡 → 扣一枚「忍」并摸一张",
          bool(ok[0] if isinstance(ok, tuple) else ok)
          and mark_count(player, "renjie", "ren") == 2
          and len(player.hand) == 1,
          "marks=%d hand=%d" % (mark_count(player, "renjie", "ren"),
                                len(player.hand)))

    # 4.3 房主侧入口：远程玩家提交的重复 card_id 最终落在
    #     ``RemoteHumanController._activate_skill`` → ``game.skills.activate``。
    #     协议层只查"牌在房主下发的候选里"，重复 id 两次都在候选里，所以它
    #     会通过协议层——拦住它的是引擎这一层（这条断言走的就是那一层）。
    game = make_game(seed=13)
    player = game.player
    sha = card("SHA")
    player.hand[:] = [sha]
    before = fingerprint(game, player)
    ok, message = game.skills.activate(player, "zhiheng", cards=[sha, sha])
    check("4.3 房主侧 skills.activate 同样拒绝重复费用（远程提交的落地入口）",
          ok is False and fingerprint(game, player) == before,
          "ok=%s message=%s" % (ok, message))


# ==================================================
# 5. 未知技能 id：这是编程错误，仍然抛
# ==================================================

def group_unknown_skill():
    game = make_game(seed=9)
    player = game.player
    try:
        game.engine.submit(ActivateSkillAction(player, "no_such_skill"))
    except ValueError:
        check("5.1 不存在的技能 id 仍然抛 ValueError（编程错误，不是玩家输入）",
              True)
        return
    except Exception as error:                                        # noqa: BLE001
        check("5.1 不存在的技能 id 仍然抛 ValueError", False,
              "抛的是 %s" % type(error).__name__)
        return
    check("5.1 不存在的技能 id 仍然抛 ValueError", False, "没有抛")


def main():
    groups = (
        ("制衡：重复 / stale / 数量", group_zhiheng),
        ("举荐：上限 / 目标 / 次数", group_jujian),
        ("授予型：入口校验", group_grant_shape),
        ("资源标记不被偷偷消耗", group_marks),
        ("未知技能 id", group_unknown_skill),
    )
    for name, group in groups:
        print("\n== %s ==" % name, flush=True)
        try:
            group()
        except Exception:                                             # noqa: BLE001
            traceback.print_exc()
            check(name + "（整组）", False, "抛出未捕获异常")

    failed = [item for item in RESULTS if not item[1]]
    print("\n" + "=" * 68)
    print("共 %d 项，通过 %d，失败 %d" % (len(RESULTS),
                                         len(RESULTS) - len(failed), len(failed)))
    for name, _ok, detail in failed:
        print("  失败：%s  %s" % (name, detail))
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
