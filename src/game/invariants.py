"""实体牌唯一归属不变量：同一张实体牌不能同时属于多个牌区。

一张实体牌（``Card`` 实例）在任意时刻只能待在一个**真实牌区**里。出现
"既在 P0 的手牌、又在弃牌堆"这种状态时，规则结算与逐玩家视图会同时拿到
两份互相矛盾的真相，而且往往要等到很后面的某个技能才炸出来——排查成本极高。
本模块把这类错误在**发生的那一步**就报出来。

扫描的真实牌区（规则层的容器本身）
================================

===========================  ==========================================
位置                          含义
===========================  ==========================================
牌堆 / 弃牌堆                 ``game.deck.draw_pile`` / ``discard_pile``
处理区                        ``game.processing_zone``（结算中的牌）
公共牌池                      ``game.public_card_pool``（【五谷丰登】）
``P?.手牌``                   ``player.hand``
``P?.判定区``                 ``player.judgement_zone``（延时锦囊）
``P?.装备(<slot>)``           ``player.equipment``（单值槽位字典）
``P?.置牌区(<zone>)``         ``player.placed_cards``（【屯田】的田等）
===========================  ==========================================

**不算牌区**（因此不参与检查）：
日志、动画队列、``table_cards`` 桌面展示副本、``judge_card`` / ``revealed_card``
单张引用、选牌候选（``pending_selection`` / ``pending_view_as`` /
``pending_card_action`` / ``PendingRequest.context["candidates"]``）、技能自己
的 ``skill_state`` 引用。这些都是"指向某张牌的引用"，牌的真实归属仍在上面那
些容器里；把它们也算成牌区只会制造误报。

虚拟牌与素材牌
==============

【武圣】【龙胆】【丈八蛇矛】这类视为技造出的是 ``VirtualCard``（``_virtual``
为真），它**从不进入任何牌区**：真正被移动的是``source_cards`` 里的实体素材牌。
因此本模块只扫实体牌，虚拟牌既不算牌区归属、也不会与自己的素材牌被误判成
重复。反过来，若实体素材牌同时出现在两个牌区，照样会被查出来。

检查边界
========

只在**一次完整移动 / 结算结束**的稳定边界上检查：

* ``Atom`` 执行完毕（含 ``ATOM_AFTER`` 事件订阅者的改动）之后；
* 测试或调试代码显式调用 ``assert_card_ownership(game)`` 时。

绝不检查"已移出、尚未移入"的中间步骤（例如
``MoveCardAtom(source=processing_zone, destination=None)`` 与随后的
``EquipCardAtom`` 之间、或者动画飞行途中）——那时牌本来就不在任何牌区里，
它只是**暂时无归属**，不是重复归属。本模块只报"同时属于 2 个及以上牌区"，
不报"一张牌都不属于"，所以这类中间态天然不会误报。

等待玩家响应同样是合法的稳定状态：检查只比较牌区容器，不要求 Pending 清空。

启用方式
========

默认**关闭**，普通对局不受任何影响。三个入口的优先级是
**对局显式设置 > 模块临时强制 > 环境变量**，判定集中在 ``armed_for``：

* 测试（只影响这一局）：``game.assert_card_ownership = True`` / ``False``；
* 临时会话（影响**所有**对局，含已经建好的）：``invariants.enable_debug()`` /
  ``disable_debug()``，用完 ``restore_debug(previous)`` 还原；
* 调试（进程级）：环境变量 ``SANGUOSHA_ASSERT_CARD_OWNERSHIP=1``。

``Game`` 自己**不把默认值冻结下来**（属性留成 ``None`` = "跟随全局"），
所以 ``enable_debug()`` 对已经创建的对局同样生效——这是 Phase 14.1 问题 3
的修复点。关闭时不做任何牌区扫描，边界上只有一次 ``armed_for`` 判定。

未覆盖
======

本模块只做**重复归属**检测。牌数守恒 / 丢牌检查暂未实现：实体牌在若干遗留
动画路径里会先移出原区域、等动画播完才追加到目的地（如
``Game.queue_draw_cards`` / ``queue_weapon_discards``），这期间它合法地不在
任何牌区，因此"总张数必须等于牌堆规模"这条断言会稳定误报。详见交付报告。
"""

import os
from dataclasses import dataclass, field

#: 显式调试模式的环境变量开关。
ENV_VAR = "SANGUOSHA_ASSERT_CARD_OWNERSHIP"

_TRUTHY = frozenset({"1", "true", "yes", "on", "y", "t"})

#: 模块级强制开关（``enable_debug`` / ``disable_debug``）；None = 不强制。
_FORCED = None

#: 环境变量在导入时求值一次：进程启动后设置它就来不及了，测试请用
#: ``game.assert_card_ownership = True``。
_ENV_ENABLED = bool(os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY)


def enable_debug():
    """显式开启检查（临时排查用）；返回先前的强制值。"""

    global _FORCED
    previous = _FORCED
    _FORCED = True
    return previous


def disable_debug():
    """显式关闭检查；返回先前的强制值。"""

    global _FORCED
    previous = _FORCED
    _FORCED = False
    return previous


def restore_debug(previous):
    """还原 ``enable_debug`` / ``disable_debug`` 返回的强制值。"""

    global _FORCED
    _FORCED = previous


def armed_for(state):
    """统一开关判定：**唯一入口**，原子边界与帧边界都只问它。

    优先级：**对局显式设置 > 模块临时强制 > 环境变量**。

    * **对局显式设置**（``game.assert_card_ownership`` 是 ``True`` / ``False``）
      永远说了算。这一条不能反过来：``disable_debug()`` 若压掉"测试里明确按局
      打开"的检查，用例会在该报错的时候静默通过——那正是 Phase 14.1 问题 3。
    * 对局**没有**显式设置（属性是 ``None``，"跟随全局"）时才看模块开关，
      再退到环境变量。于是 ``enable_debug()`` 能影响**已经建好**的对局。
    """

    explicit = getattr(state, "assert_card_ownership", None)
    if explicit is not None:
        return bool(explicit)
    if _FORCED is not None:
        return _FORCED
    return _ENV_ENABLED


def debug_default():
    """没有任何显式设置时，新对局的默认值（模块强制 > 环境变量）。

    注意它**不是**"冻结在构造那一刻的取值"：``Game`` 把属性留成 ``None``，
    每次判定都重新问一次，模块开关因此对已创建的对局同样有效。
    """

    if _FORCED is not None:
        return _FORCED
    return _ENV_ENABLED


def effective(state):
    """``state`` 上**当前生效**的开关值（给日志 / 测试 / 报告看的只读结果）。"""

    return bool(armed_for(state))



# ==================================================
# 牌区枚举
# ==================================================


def _player_label(player):
    player_id = getattr(player, "player_id", None)
    if player_id not in (None, ""):
        return str(player_id)
    name = getattr(player, "name", None)
    return str(name) if name not in (None, "") else "?"


def _deck_of(game):
    deck = getattr(game, "deck", None)
    if deck is None:
        return ()
    return (("牌堆", getattr(deck, "draw_pile", None)),
            ("弃牌堆", getattr(deck, "discard_pile", None)))


def zone_entries(game):
    """遍历所有真实牌区，产出 ``(位置名, 牌)``。

    只产出**实体牌**：虚拟牌（``_virtual``）不属于任何牌区，跳过。区域名带
    角色前缀（``P0.手牌``），因为重复归属的报错必须能直接定位到人。
    """

    for label, container in _deck_of(game):
        for card in container or ():
            yield label, card

    for label, container in (("处理区", getattr(game, "processing_zone", None)),
                             ("公共牌池", getattr(game, "public_card_pool", None))):
        for card in container or ():
            yield label, card

    for player in getattr(game, "players", ()) or ():
        who = _player_label(player)
        for card in getattr(player, "hand", None) or ():
            yield who + ".手牌", card
        for card in getattr(player, "judgement_zone", None) or ():
            yield who + ".判定区", card
        for slot, card in (getattr(player, "equipment", None) or {}).items():
            if card is not None:
                yield who + ".装备(" + str(slot) + ")", card
        for zone, pile in (getattr(player, "placed_cards", None) or {}).items():
            for card in pile or ():
                yield who + ".置牌区(" + str(zone) + ")", card


def _is_virtual(card):
    return bool(getattr(card, "_virtual", False)
                or getattr(card, "is_virtual", False))


def card_label(card):
    """一张牌的调试标识：``card-12(杀)``。

    ``Card.id`` 是进程内自增的实体牌标识；虚拟牌（由技能现场构造）没有它，
    退化成对象地址，保证报错信息里始终能区分两张不同的牌。
    """

    card_id = getattr(card, "id", None)
    if card_id in (None, ""):
        card_id = "object@%x" % id(card)
    name = getattr(card, "display_name", None) or getattr(card, "name", None)
    if name in (None, ""):
        return str(card_id)
    return "%s(%s)" % (card_id, name)


# ==================================================
# 检查
# ==================================================


@dataclass
class OwnershipConflict:
    """一张实体牌同时出现在多个牌区。"""

    label: str
    locations: tuple = ()
    cards: tuple = field(default=(), repr=False)

    def describe(self):
        return " %s ×%d: %s" % (self.label, len(self.locations),
                                " / ".join(self.locations))


@dataclass
class OwnershipReport:
    """一次扫描的结果。``ok`` 为假时 ``conflicts`` 非空。"""

    scanned: int = 0
    conflicts: tuple = ()

    @property
    def ok(self):
        return not self.conflicts

    def describe(self):
        if self.ok:
            return "[牌唯一归属不变量] 正常：%d 张实体牌各归一个牌区" % self.scanned
        lines = ["[牌唯一归属不变量] 检测到重复卡牌:"]
        lines.extend(conflict.describe() for conflict in self.conflicts)
        return "\n".join(lines)


def find_duplicate_ownership(game):
    """找出同时属于多个牌区的实体牌；返回 ``OwnershipReport``。"""

    seen = {}
    order = []
    scanned = 0
    for label, card in zone_entries(game):
        if card is None or _is_virtual(card):
            continue
        scanned += 1
        bucket = seen.get(id(card))
        if bucket is None:
            bucket = [card_label(card), []]
            seen[id(card)] = bucket
            order.append(id(card))
        bucket[1].append(label)

    conflicts = tuple(
        OwnershipConflict(seen[key][0], tuple(seen[key][1]))
        for key in order
        if len(seen[key][1]) > 1
    )
    return OwnershipReport(scanned=scanned, conflicts=conflicts)


class CardOwnershipError(AssertionError):
    """重复归属的失败信号（继承 AssertionError，测试里可直接断言）。"""


def assert_card_ownership(game):
    """扫描全部牌区；发现重复归属就抛 ``CardOwnershipError``。

    返回扫描报告，调用方可以据此断言牌数等次要事实。
    """

    report = find_duplicate_ownership(game)
    if not report.ok:
        raise CardOwnershipError(report.describe())
    return report


def check_after_atom(state):
    """``Atom`` 稳定边界上的检查入口（引擎按需调用）。"""

    if armed_for(state):
        assert_card_ownership(state)
