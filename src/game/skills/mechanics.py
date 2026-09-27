"""扩展包技能共用的通用机制。

这里放的都是**规则层**原语：拼点、翻面、觉醒、限定技、标记、技能获得 /
失去、额外回合、伤害转移、武将牌上的牌区。它们不认识任何具体武将——
武将技能只是在自己的实现里调用这些函数，从而和多来源转化、Modifier、
PendingRequest 一样共用同一套规则。

设计约定
--------
* 所有函数都接受 ``game`` 作为第一个参数，**绝不读 ``game.player``**。
  双边手动测试里 ``game.player`` 只是"本机鼠标现在代表谁"，真正的技能
  拥有者、事件来源、行动者、请求目标必须由调用方显式传入。
* 会改变流程走向的动作（拼点、看牌、选目标）一律返回一个可恢复的
  ``Flow``，由调用方 ``wait`` 住；纯状态变更（加标记、翻面）同步完成。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom

from ..engine.events import Event, EventType
# 直接取子模块里的 Flow：``..engine`` 包本身会在导入链中途被牵进来
# （engine → card_effects → flows → turn → skills → … → 本模块），
# 从子模块导入不受那条链的初始化顺序影响。
from ..engine.flows import Flow
from ..engine.pending import PendingRequestType


# ==================================================
# 标记
#
# 标记存在 ``player.marks`` 上（不是 skill_state）：很多标记**别人也要读**
# ——武魂要看"谁持有最多梦魇标记"、暴虐要看谁造成了伤害。
# ==================================================

def marks_of(player, skill_id):
    return dict((getattr(player, "marks", None) or {}).get(skill_id) or {})


def mark_count(player, skill_id, name="default"):
    if player is None:
        return 0
    getter = getattr(player, "mark", None)
    if callable(getter):
        return int(getter(skill_id, name) or 0)
    return int(((getattr(player, "marks", None) or {}).get(skill_id) or {}).get(name, 0) or 0)


def add_mark(game, player, skill_id, amount=1, name="default", *, log_name=""):
    """加一个标记。

    默认**不写战报**：标记会随每一次伤害反复变化（暴怒 / 忍），写进去会
    把只有 8 条的即时战报刷没。需要留痕的技能自己传 ``log_name``。
    """

    if player is None:
        return 0
    total = player.add_mark(skill_id, name, amount)
    if game is not None and amount and log_name:
        game.add_log("%s 获得 %d 个「%s」标记（共 %d 个）"
                     % (player.name, int(amount), log_name, total))
    if game is not None and amount:
        # 只读通知：界面据此在角色面板 / 技能栏上显示标记数量。
        game.context.emit(Event(
            EventType.SKILL_TRIGGERED, source=player,
            payload={"skill_id": skill_id, "skill_name": log_name or skill_id,
                     "marks": True, "mark": name, "total": total},
        ))
    return total


def remove_mark(game, player, skill_id, amount=1, name="default"):
    if player is None:
        return 0
    current = mark_count(player, skill_id, name)
    remaining = max(0, current - int(amount))
    player.set_mark(skill_id, name, remaining)
    return remaining


# ==================================================
# 翻面
#
# 背面朝上 = 跳过自己的下一个回合（回合开始时翻回正面，本回合不进行）。
# ==================================================

def set_face_up(game, player, face_up, *, reason=""):
    if player is None:
        return False
    before = bool(getattr(player, "face_up", True))
    player.face_up = bool(face_up)
    if before == player.face_up:
        return False
    game.context.emit(Event(
        EventType.CHAIN_STATE_CHANGED, source=player, target=player,
        payload={"face_up": player.face_up, "reason": reason},
    ))
    game.add_log("%s 的武将牌%s" % (player.name, "翻回正面" if player.face_up else "翻至背面"))
    return True


def flip_player(game, player, *, reason=""):
    """把武将牌翻到另一面（曹仁据守 / 曹丕放逐 / 徐盛破军…）。"""

    return set_face_up(game, player, not bool(getattr(player, "face_up", True)),
                       reason=reason)


def is_flipped(player):
    return not bool(getattr(player, "face_up", True))


# ==================================================
# 限定技 / 觉醒技
#
# 两者都是"一局只能用一次"，区别只在触发方式：限定技玩家主动选，觉醒技
# 由条件强制触发。共用同一份"已用过"记录，存在 skill_state 里，
# scope 用 PERSISTENT（重开时会随 skills.clear() 一起清掉）。
# ==================================================

def limited_used(player, skill_id):
    state = getattr(player, "skill_state", None)
    if state is None:
        return False
    return bool(state.get(skill_id, "limited_used", 0))


def consume_limited(game, player, skill_id, *, note=""):
    state = getattr(player, "skill_state", None)
    if state is not None:
        state.set(skill_id, "limited_used", 1)
    if note:
        game.add_log("%s 发动限定技【%s】" % (player.name, note))
    return True


def awaken_used(player, skill_id):
    return limited_used(player, skill_id)


def awaken(game, player, skill_id, *, max_hp_delta=0, gain=(), recover=0,
           draw=0, name="", note=""):
    """执行一次觉醒：减体力上限 → 回复体力 → 摸牌 → 永久获得技能。

    顺序按卡面：先减上限、再回复 / 摸牌，最后获得技能。体力上限的削减
    不会把当前体力抬上去，但也不会因为上限跌破当前体力而额外扣血——
    这与"失去体力上限"的官方口径一致（体力上限减少，体力跟着不超过上限）。
    """

    state = getattr(player, "skill_state", None)
    if state is not None:
        state.set(skill_id, "limited_used", 1)

    if max_hp_delta:
        player.max_hp = max(0, int(player.max_hp) + int(max_hp_delta))
        if player.hp > player.max_hp:
            player.hp = player.max_hp
    if recover:
        game.context.apply(RecoverHpAtom(player, int(recover)))
    if draw:
        game.context.apply(DrawCardsAtom(player, int(draw)))
    for skill_id_to_gain in tuple(gain):
        gain_skill(game, player, skill_id_to_gain)

    label = name or skill_id
    game.add_log("%s 觉醒，发动【%s】" % (player.name, label))
    if note:
        game.message = player.name + " 的【" + label + "】：" + note
    return True


# ==================================================
# 技能的获得 / 失去
# ==================================================

def gain_skill(game, player, skill_id):
    """永久获得一个技能（觉醒技 / 化身 / 伪帝 / 极略）。

    幂等：已经拥有时直接返回 False，不会重复绑定监听。
    """

    manager = game.skills
    if manager.has(player, skill_id):
        return False
    definition = manager.registry.get(skill_id)
    if definition is None:
        raise KeyError("unknown skill: " + str(skill_id))
    manager.bind(player, skill_id)
    game.add_log("%s 获得技能【%s】" % (player.name, definition.name))
    return True


def lose_skill(game, player, skill_id):
    if not game.skills.has(player, skill_id):
        return False
    definition = game.skills.registry.get(skill_id)
    game.skills.unbind(player, skill_id)
    game.add_log("%s 失去技能【%s】" % (player.name, getattr(definition, "name", skill_id)))
    return True


def lose_all_skills(game, player, *, reason=""):
    """失去当前的所有技能（断肠 / 挥泪一类）。

    技能状态与 modifier 会由 ``SkillManager.unbind_all`` 一并清掉，
    因此"失去技能后能力立刻失效"是自然结果，不需要技能自己再撤一遍。
    """

    count = game.skills.unbind_all(player)
    if count:
        game.add_log("%s 失去全部技能%s" % (player.name, ("（" + reason + "）") if reason else ""))
    return count


def general_skill_ids(game, player):
    """该角色**武将牌上印的**技能 id（不含后来获得的）。"""

    general = game.generals.get(getattr(player, "general_id", None))
    return tuple(getattr(general, "skill_ids", ()) or ())


# ==================================================
# 额外回合
# ==================================================

def grant_extra_turn(game, player):
    """给一名角色一个额外回合（放权 / 连破）。"""

    queue_adder = getattr(game, "queue_extra_turn", None)
    if callable(queue_adder):
        return queue_adder(player)
    if player is None or not getattr(player, "alive", True):
        return False
    queue = getattr(game, "extra_turns", None)
    if queue is None:
        queue = []
        game.extra_turns = queue
    queue.append(player)
    game.add_log("%s 获得一个额外回合" % player.name)
    return True


def pop_extra_turn(game):
    popper = getattr(game, "pop_extra_turn", None)
    if callable(popper):
        return popper()
    queue = getattr(game, "extra_turns", None)
    if not queue:
        return None
    while queue:
        player = queue.pop(0)
        if getattr(player, "alive", True):
            return player
    return None


# ==================================================
# 拼点
#
# 双方各以一张手牌暗出，同时亮出后比点数，点数大者赢；点数相同算发起者
# **没赢**（"若你赢 / 若你没赢"这类判据全部按这个口径）。
# A = 1（最小），K = 13（最大）。
# ==================================================

RANK_VALUES = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
    "9": 9, "10": 10, "J": 11, "Q": 12, "K": 13,
}


def rank_value(card):
    rank = getattr(card, "rank", None)
    if rank is None:
        return 0
    text = str(rank).upper()
    if text in RANK_VALUES:
        return RANK_VALUES[text]
    try:
        return int(text)
    except ValueError:
        return 0


class PindianResult:
    """一次拼点的结果。``winner`` 为 None 表示发起者没赢（平点也算没赢）。"""

    __slots__ = ("initiator", "target", "initiator_card", "target_card",
                 "initiator_value", "target_value", "cancelled")

    def __init__(self, initiator, target, initiator_card=None, target_card=None,
                 cancelled=False):
        self.initiator = initiator
        self.target = target
        self.initiator_card = initiator_card
        self.target_card = target_card
        self.initiator_value = rank_value(initiator_card)
        self.target_value = rank_value(target_card)
        self.cancelled = bool(cancelled)

    @property
    def initiator_wins(self):
        if self.cancelled:
            return False
        return self.initiator_value > self.target_value

    @property
    def winner(self):
        return self.initiator if self.initiator_wins else self.target

    @property
    def loser(self):
        return self.target if self.initiator_wins else self.initiator

    def describe(self):
        if self.cancelled:
            return "拼点未能进行"
        reveal = {"initiator": self.initiator_card, "target": self.target_card}
        from src.card import display_name_for

        def _label(card):
            identity = getattr(card, "identity_label", "") or ""
            name = display_name_for(getattr(card, "name", ""), getattr(card, "nature", "normal"))
            return (identity + "【" + name + "】") if identity else ("【" + name + "】")

        return "%s 的 %s 对 %s 的 %s（%s %s）" % (
            self.initiator.name, _label(reveal["initiator"]),
            self.target.name, _label(reveal["target"]),
            "发起者赢" if self.initiator_wins else "发起者没赢",
            str(self.initiator_value) + ":" + str(self.target_value),
        )


def pindian_possible(initiator, target):
    """双方都还有手牌才能拼点。"""

    if initiator is None or target is None:
        return False
    if initiator is target:
        return False
    if not getattr(initiator, "alive", True) or not getattr(target, "alive", True):
        return False
    return bool(getattr(initiator, "hand", ())) and bool(getattr(target, "hand", ()))


class PindianFlow:
    """可恢复的拼点流程：先发起者暗出，再目标暗出，然后一起亮出比点。

    它不是 ``Flow`` 的子类，而是被技能当成"子流程"直接 ``start()``：
    内部自己管理两次 PendingRequest，全部走 ``owner_flow`` 这一套，
    因此远端真人 / AI / 双边手动三种回答来源完全一致。
    """

    def __init__(self, engine, initiator, target, *, reason="pindian",
                 on_complete=None, forced_initiator_card=None):
        self.engine = engine
        self.game = engine.game
        self.initiator = initiator
        self.target = target
        self.reason = reason
        self.on_complete = on_complete
        self.forced_initiator_card = forced_initiator_card
        self.stage = "initiator"
        self.result = None
        self._initiator_card = None
        self._request = None

    # ---- 入口 ----

    def start(self):
        if not pindian_possible(self.initiator, self.target):
            return self._finish(PindianResult(self.initiator, self.target, cancelled=True))
        return self._ask_initiator()

    def _ask_initiator(self):
        if self.forced_initiator_card is not None:
            self._initiator_card = self.forced_initiator_card
            return self._ask_target()
        self._request = self._create_request(self.initiator, self.initiator,
                                             "【拼点】：请选择一张手牌暗出")
        self.stage = "initiator"
        self.engine.present_or_auto_resolve(self._request)
        return None

    def _ask_target(self):
        if not getattr(self.target, "hand", ()):
            return self._finish(PindianResult(self.initiator, self.target, cancelled=True))
        self._request = self._create_request(self.initiator, self.target,
                                             "【拼点】：请选择一张手牌暗出")
        self.stage = "target"
        self.engine.present_or_auto_resolve(self._request)
        return None

    def _create_request(self, source, responder, prompt):
        return self.engine.pending.create(
            PendingRequestType.SELECT_CARDS,
            source=source,
            target=responder,
            prompt=prompt,
            owner_flow=self,
            min_cards=1,
            max_cards=1,
            request_context={
                "reason": "pindian",
                "candidates": list(responder.hand),
                "zone": "hand",
                "zone_owner": responder,
                "pindian_initiator": self.initiator,
                "pindian_target": self.target,
            },
        )

    # ---- Flow 协议（PendingRequest.owner_flow 会调用 resume）----

    @property
    def status(self):
        from ..engine.flows import FlowStatus

        return FlowStatus.COMPLETED if self.result is not None else FlowStatus.WAITING

    @property
    def pending_request(self):
        return self._request

    @property
    def result_value(self):
        return self.result

    def resume(self, response):
        from ..engine.flows import FlowResult, FlowStatus

        if self.result is not None:
            return FlowResult(FlowStatus.COMPLETED, self.result)
        cards = list(getattr(response, "cards", ()) or ())
        card = cards[0] if cards else None
        if card is None and getattr(response, "card", None) is not None:
            card = response.card
        self._request = None

        if self.stage == "initiator":
            if card is None or not self._owns(self.initiator, card):
                return self._finish(PindianResult(self.initiator, self.target, cancelled=True))
            self._initiator_card = card
            self._ask_target()
            if self.result is not None:
                return FlowResult(FlowStatus.COMPLETED, self.result)
            return FlowResult(FlowStatus.WAITING, self._request)

        # stage == "target"
        if card is None or not self._owns(self.target, card):
            # 目标是"暗出"了一张已经不在手里的牌：拼点不成立。按 cancelled 收尾，
            # 双方都不弃牌——发起者暗出的那张必须退回。以前这里把发起者的牌照样
            # 弃掉、还把点数比出来当"正常结果"，于是技能白付了代价却按胜/负结算。
            return self._finish(PindianResult(self.initiator, self.target, cancelled=True))
        return self._finish(PindianResult(
            self.initiator, self.target, self._initiator_card, card))

    def current_result(self):
        from ..engine.flows import FlowResult, FlowStatus

        return FlowResult(self.status, self.result)

    def _owns(self, player, card):
        return any(item is card for item in getattr(player, "hand", ()))

    # ---- 收尾 ----

    def _finish(self, result):
        from ..engine.flows import FlowResult, FlowStatus

        # 两张拼点牌一起进弃牌堆；牌已经不在手上的（被打断）跳过。
        for owner, card in ((self.initiator, result.initiator_card),
                            (self.target, result.target_card)):
            if card is None:
                continue
            if any(item is card for item in getattr(owner, "hand", ())):
                self.engine.context.apply(MoveCardAtom(
                    card, source=owner.hand, destination=self.game.deck.discard_pile,
                    # 拼点牌进弃牌堆既不是弃置也不是判定（官方 FAQ：拼点不能
                    # 触发【落英】一类时机）。
                    reason="pindian", owner=owner))

        self.result = result
        if not result.cancelled:
            self.game.add_log("拼点：" + result.describe())
            self.game.message = "拼点结果：" + ("发起者赢" if result.initiator_wins else "发起者没赢")
        # 拼点流程不是 ``Flow`` 的子类（它自己管两次 PendingRequest），
        # 因此没有基类那个幂等入口，只能按原样触发一次。
        if self.on_complete is not None:
            self.on_complete(result)
        return FlowResult(FlowStatus.COMPLETED, result)


def start_pindian(engine, initiator, target, *, reason="pindian", on_complete=None,
                  forced_initiator_card=None):
    """发起一次拼点；返回 ``PindianFlow``（可能同步结束，也可能等待回答）。"""

    flow = PindianFlow(engine, initiator, target, reason=reason,
                       on_complete=on_complete,
                       forced_initiator_card=forced_initiator_card)
    flow.start()
    return flow


# ==================================================
# 判定 / 伤害的通用小工具
# ==================================================

def judge(engine, owner, reason, on_complete=None, card_recipient=None):
    """发起一次判定；返回 (flow, result)。``result`` 为 None 表示还在改判窗口。

    ``card_recipient`` 声明"这张判定牌最终归谁"（``callable(result) -> player``，
    返回 None 表示照常进弃牌堆）。取到的一定是**最终生效**的那张判定牌：
    中间被改判换掉的旧牌早已离开处理区。
    """

    from ..flows.judge import JudgeFlow

    flow = JudgeFlow(engine, owner, reason, on_complete=on_complete,
                     card_recipient=card_recipient)
    outcome = flow.start()
    from ..engine.flows import FlowStatus

    if outcome.status is FlowStatus.WAITING:
        return flow, None
    return flow, outcome.value


def discard_card(game, player, card, *, zone="hand"):
    """把一张牌从某个区域移入弃牌堆（调用方保证它确实在那里）。"""

    container = {
        "hand": getattr(player, "hand", None),
        "judgement": getattr(player, "judgement_zone", None),
    }.get(zone)
    if container is None or not any(item is card for item in container):
        return False
    game.engine.context.apply(MoveCardAtom(
        card, source=container, destination=game.deck.discard_pile))
    return True


def discard_hand_cards(game, player, cards):
    count = 0
    for card in list(cards or ()):
        if discard_card(game, player, card):
            count += 1
    return count


def lose_hp(game, player, amount, *, source=None, reason=""):
    """失去体力（**不是**伤害）：不触发受伤类技能，也不进入伤害结算。"""

    getter = getattr(game, "lose_hp", None)
    if callable(getter):
        return getter(player, amount, source=source, cause=reason)
    from src.game.atoms_v2 import LoseHpAtom

    result = game.engine.context.apply(LoseHpAtom(player, int(amount)))
    game.add_log("%s 失去 %d 点体力%s" % (
        player.name, int(amount), ("（" + reason + "）") if reason else ""))
    return result


def is_wounded(player):
    return int(getattr(player, "hp", 0)) < int(getattr(player, "max_hp", 0))


def lost_hp(player):
    return max(0, int(getattr(player, "max_hp", 0)) - int(getattr(player, "hp", 0)))


# ==================================================
# 性别 / 势力（化身后会变，因此只能走查询，不能读武将定义）
# ==================================================

def set_forbidden_category(game, applier, target, category, *, skill_id="jilei"):
    """让 ``target`` 直到本回合结束不能使用 / 打出 / 弃置 ``category`` 类别的牌。

    实现是一条挂在**被限制者**身上的 modifier（按类别查询），``applier``
    记在 modifier 上，供回合结束时统一回收。使用、打出与强制弃牌三条路径
    都读同一个查询，因此不会出现"某一条路漏了"的半截限制。
    """

    if game is None or target is None or not category:
        return None
    from .modifiers import Modifier, ModifierKind

    modifier = Modifier(
        kind=ModifierKind.CATEGORY_FORBIDDEN,
        value=category,
        owner=target,
        skill_id=skill_id,
    )
    modifier.applier = applier
    game.modifiers.register(modifier)
    return modifier


def clear_forbidden_categories(game, applier):
    """回收某个角色施加的全部牌类别锁定（回合结束时调用）。"""

    from .modifiers import ModifierKind

    registry = getattr(game, "modifiers", None)
    if registry is None:
        return 0
    removed = 0
    for kind, items in list(registry._items.items()):
        kept = [
            item for item in items
            if not (getattr(item, "applier", None) is applier
                    and item.kind is ModifierKind.CATEGORY_FORBIDDEN)
        ]
        removed += len(items) - len(kept)
        if kept:
            registry._items[kind] = kept
        else:
            del registry._items[kind]
    return removed


def gender_of(player):
    return getattr(player, "gender", None)


def kingdom_of(player):
    return getattr(player, "kingdom", None)


def set_gender(game, player, gender):
    if getattr(player, "gender", None) == gender:
        return False
    player.gender = gender
    return True


def set_kingdom(game, player, kingdom):
    if getattr(player, "kingdom", None) == kingdom:
        return False
    player.kingdom = kingdom
    return True


# ==================================================
# 通用询问（技能在事件回调里向玩家要一个决策）
#
# 全部走 PendingRequest：本地真人、远程真人、AI 三种回答来源共用一条路径，
# 引擎的校验也只有一个入口。技能只要 ``flow.wait`` 住请求、把后续结算写进
# 自己的 resume 分支即可。
# ==================================================

def ask_confirm(engine, flow, *, source, target, prompt, reason, context=None):
    request = engine.pending.create(
        PendingRequestType.CONFIRM, source=source, target=target, prompt=prompt,
        owner_flow=flow, request_context=dict({"reason": reason}, **(context or {})))
    flow.wait(request)
    engine.present_or_auto_resolve(request)
    return request


def ask_cards(engine, flow, *, source, target, prompt, reason, candidates,
              min_cards=1, max_cards=1, context=None, zone="hand"):
    request = engine.pending.create(
        PendingRequestType.SELECT_CARDS, source=source, target=target, prompt=prompt,
        owner_flow=flow, min_cards=min_cards, max_cards=max_cards,
        request_context=dict({
            "reason": reason,
            "candidates": list(candidates),
            "zone": zone,
            "zone_owner": target,
        }, **(context or {})))
    flow.wait(request)
    engine.present_or_auto_resolve(request)
    return request


def ask_option(engine, flow, *, source, target, prompt, reason, options, context=None):
    """让玩家从若干选项里选一个。

    ``options`` 支持两种写法：

        纯值         ("draw", "discard")                 —— 值本身就是给玩家看的
        (值, 文案)   (("discard", "弃置两张手牌"), ...)   —— 值给引擎，文案给玩家

    规范化之后 ``PendingRequest.options`` **只存值**，文案放进
    ``context["option_labels"]``。这样引擎校验（选项是否合法）、AI 决策、
    本地按钮文案、远程下发的 label 四个消费点读的是同一份值，而给玩家看的
    字面量只有一个来源——之前 (值, 文案) 会被整只元组当成选项，按钮上直接
    显示 "('discard', '弃置两张手牌')"。
    """

    values = []
    labels = {}
    for item in options:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            value, label = item
            values.append(value)
            labels[str(value)] = str(label)
        else:
            values.append(item)
    request_context = {"reason": reason}
    if labels:
        request_context["option_labels"] = labels
    request_context.update(context or {})
    request = engine.pending.create(
        PendingRequestType.CHOOSE_OPTION, source=source, target=target, prompt=prompt,
        owner_flow=flow, options=tuple(values),
        request_context=request_context)
    flow.wait(request)
    engine.present_or_auto_resolve(request)
    return request


def option_label(request, value):
    """一个选项给玩家看的文案（没有声明文案时就用值本身）。"""

    labels = request.context.get("option_labels") or {}
    return str(labels.get(str(value), value))


def ask_targets(engine, flow, *, source, target, prompt, reason, candidates,
                min_targets=1, max_targets=1, context=None):
    request = engine.pending.create(
        PendingRequestType.SELECT_TARGETS, source=source, target=target, prompt=prompt,
        owner_flow=flow, min_cards=min_targets, max_cards=max_targets,
        request_context=dict({
            "reason": reason,
            "candidates": list(candidates),
        }, **(context or {})))
    flow.wait(request)
    engine.present_or_auto_resolve(request)
    return request


# ==================================================
# 视为使用一张牌
# ==================================================

def virtual_card(name, owner, sources=(), *, category="basic", nature="normal",
                 subtype=None):
    """凭技能凭空造一张虚拟牌（视为使用 / 打出）。"""

    from ..conversion import VirtualCard

    return VirtualCard(
        name=name, source_cards=tuple(sources), skill_id="", owner=owner,
        category=category, subtype=subtype, nature=nature,
    )


def use_virtual(game, actor, name, *, sources=(), targets=(), nature="normal",
                category="basic", ignore_usage_limit=True, on_complete=None,
                skip_wuxie=False):
    """视为使用一张牌（神速 / 旋风 / 激将 / 离间一类）。

    走的是与真实出牌完全相同的 ``UseCardAction``：距离、目标合法性、无懈、
    响应、结算全部由通用流程负责，技能不复制任何结算代码。
    """

    from ..engine.domain_actions import UseCardAction

    card = virtual_card(name, actor, sources, category=category, nature=nature)
    metadata = {}
    if skip_wuxie:
        metadata["skip_wuxie"] = True
    action = UseCardAction(
        actor, card, list(targets),
        ignore_usage_limit=ignore_usage_limit,
        on_complete=on_complete,
        metadata=metadata,
    )
    return game.engine.submit(action)


#: "从某名角色的区域里拿一张牌"包含哪些区域。
TAKE_ZONES = ("hand", "equipment", "judgement")


def zone_take_options(player, zones=TAKE_ZONES):
    """"从这名角色的区域里拿一张牌"的可选项（值, 文案）。

    手牌是**暗牌**：不列出具体牌名，只给一个"一张手牌（随机）"的选项，
    由 ``take_card_from_zone`` 按隐藏信息规则随机取（见 random_hand_card）。
    装备区与判定区是公开信息，逐张列出，让玩家自己决定拿哪一张。
    """

    options = []
    if "hand" in zones and getattr(player, "hand", None):
        options.append(("hand", "一张手牌（随机）"))
    if "equipment" in zones:
        for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
            card = player.get_equipment(slot)
            if card is not None:
                options.append(("equip:" + slot,
                                "装备区的【%s】" % getattr(card, "display_name", "装备")))
    if "judgement" in zones:
        for index, card in enumerate(
                list(getattr(player, "judgement_zone", ()) or ())):
            options.append(("judge:%d" % index,
                            "判定区的【%s】" % getattr(card, "display_name", "牌")))
    return options


def take_card_from_zone(context, game, taker, player, option, *,
                        zones=TAKE_ZONES):
    """按选项把 ``player`` 的一张牌移进 ``taker`` 手里；返回实际拿到的牌。

    区域已经被清空 / 选项失效时返回 None（不硬来、不凭空造牌）。
    移动全部走统一原子：装备离场会照常发"失去装备"事件。
    """

    if player is None or taker is None or not option:
        return None
    if not getattr(player, "alive", True) or not getattr(taker, "alive", True):
        return None
    if option == "hand":
        if "hand" not in zones:
            return None
        card = random_hand_card(game, player)
        if card is None:
            return None
        context.apply(MoveCardAtom(
            card, source=player.hand, destination=taker.hand))
        return card
    if option.startswith("equip:"):
        if "equipment" not in zones:
            return None
        slot = option.split(":", 1)[1]
        card = player.get_equipment(slot)
        if card is None:
            return None
        from src.game.atoms_v2 import TAKE_REASON, UnequipAtom

        # 装备被拿走了：对原拥有者来说是**失去牌**（进的是 taker 的手牌，
        # 不是弃牌堆），所以按 "lose" 声明原因——【屯田】一类"失去牌"的技能
        # 靠这条通知工作。不声明原因时 UnequipAtom 不发任何牌移动通知。
        context.apply(UnequipAtom(
            player, slot, taker.hand, reason=TAKE_REASON))
        return card
    if option.startswith("judge:"):
        if "judgement" not in zones:
            return None
        try:
            index = int(option.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
        zone = list(getattr(player, "judgement_zone", ()) or ())
        if index < 0 or index >= len(zone):
            return None
        card = zone[index]
        context.apply(MoveCardAtom(
            card, source=player.judgement_zone, destination=taker.hand))
        return card
    return None


def is_non_delay_trick(card):
    """这张牌是不是**非延时**类锦囊（集智一类技能的判据）。

    卡牌目录里延时锦囊的 ``category`` 也是 ``trick``（这是有意的：【帷幕】
    "不能成为黑色锦囊牌的目标"必须覆盖【乐不思蜀】），所以"非延时"只能按
    牌名判断，不能按类别判断。
    """

    if getattr(card, "category", None) != "trick":
        return False
    return getattr(card, "name", None) not in ("LEBU", "BINGLIANG", "SHANDIAN")


class OptionalTriggerFlow(Flow):
    """通用的"你可以…"窗口：先问一句，同意后才执行效果。

    非锁定技与锁定技的唯一区别就是"这一次要不要发动"。``effect`` 在玩家
    答复之后被调用一次（参数是流程自己，用 ``flow.context`` 应用原子），
    返回真值表示确实结算了；**拒绝时什么都不做**——不写状态、不动牌。

    以前一批非锁定技（天妒 / 遗计 / 奸雄 / 破军 / 连营 / 闭月 / 激昂 /
    落英 / 集智…）的描述里写着"可以"，``resolve`` 里却直接结算，玩家没有
    任何拒绝的机会：不想发动的技能照样替他发动。
    """

    def __init__(self, engine, owner, *, prompt, reason, effect, label=""):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.prompt = prompt
        self.reason = reason
        self.effect = effect
        self.label = label
        self.applied = False

    def begin(self):
        if self.owner is None or not getattr(self.owner, "alive", True):
            return self.complete({"applied": False})
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt=self.prompt, reason=self.reason)
        return self.current_result()

    def advance(self, response=None):
        if response is None or not response.confirmed:
            if self.label:
                self.game.add_log("%s 放弃发动【%s】" % (self.owner.name, self.label))
            return self.complete({"applied": False})
        self.applied = bool(self.effect(self))
        return self.complete({"applied": self.applied})


def optional_trigger(context, owner, *, prompt, reason, effect, label=""):
    """从 ``Skill.resolve`` 里起一个"你可以…"窗口的便捷入口（返回流程，未启动）。

    调用方照常 ``.start()``——与 ``Skill.resolve`` 里其它流程的写法一致。
    """

    return OptionalTriggerFlow(
        context.services["engine"], owner, prompt=prompt, reason=reason,
        effect=effect, label=label)


def random_hand_card(game, player):
    """从一名角色的手牌里随机取一张（**暗牌取牌的统一样式**）。

    ``Player.hand`` 是列表，取首元素会让"获得一张手牌"变成可预测的行为——
    对手能记住自己手牌的顺序，等于提前知道自己会丢哪张。真实对局里这张牌
    是未知的，所以用对局的随机源抽。
    """

    hand = list(getattr(player, "hand", ()) or ())
    if not hand:
        return None
    rng = getattr(game, "rng", None)
    if rng is None:                                      # pragma: no cover - 防御
        return hand[0]
    return hand[rng.randrange(len(hand))]


def sha_use_options(game, wielder, victim):
    """``wielder`` 现在能对 ``victim`` 使用的**全部**【杀】使用方式。

    规则上"令某人对某人使用一张【杀】"的技能（借刀杀人 / 乱武）都必须先问
    "他到底能不能用、能用哪一张"，而不是"手里有没有名叫【杀】的牌"：火杀 /
    雷杀、【武圣】【龙胆】一类转化、酒 / 武器 / 技能改写的攻击范围全部要在
    候选里体现。统一走 Card Action Discovery，本函数不重写任何一条判断。
    """

    actions = getattr(game, "card_actions", None)
    if actions is None:                                   # pragma: no cover
        return []
    context = actions.play_context(wielder)
    result = []
    seen = set()
    for card in list(getattr(wielder, "hand", ()) or ()):
        for option in actions.actions_for_card(wielder, card, context):
            if option.result_name != "SHA" or not option.complete or not option.enabled:
                continue
            if not context.allows(option.result_name):
                continue
            virtual = actions.effective_card(option)
            if virtual is None:
                continue
            from src.game.engine import UseCardAction

            effect = game.engine.card_effects.get(virtual)
            if effect is None:
                continue
            probe = UseCardAction(wielder, virtual, [victim],
                                  ignore_usage_limit=True)
            valid, _reason = effect.can_use(game, probe)
            if not valid:
                continue
            token = (option.action_id,
                     tuple(id(item) for item in option.source_cards))
            if token in seen:
                continue
            seen.add(token)
            result.append(option)
    return result


def attack_range_targets(game, player, *, include_self=False):
    """玩家攻击范围内的其他存活角色（按座次）。"""

    from ..rules import DistanceRule

    result = []
    for other in game.seats.alive_players_in_order(start_after=player):
        if other is player and not include_self:
            continue
        if other is not player and DistanceRule.in_attack_range(game, player, other):
            result.append(other)
    return result


def other_alive_players(game, player):
    return [other for other in game.get_alive_players() if other is not player]


def same_kingdom_players(game, player, kingdom):
    return [
        other for other in game.get_alive_players()
        if other is not player and getattr(other, "kingdom", None) == kingdom
    ]


def is_out_of_turn(game, player):
    """现在是不是"这名角色的回合外"（判定时机条件时统一用它）。"""

    return game.current_turn_player is not player


def hand_cards(player, predicate=None):
    cards = [card for card in getattr(player, "hand", ())
             if not getattr(card, "is_virtual", False)]
    if predicate is None:
        return cards
    return [card for card in cards if predicate(card)]


def effective_suit(game, card, owner):
    """一张牌对 ``owner`` 的有效花色（红颜一类花色改写统一走这里）。"""

    query = getattr(game, "card_suit", None)
    if callable(query):
        return query(card, owner)
    return getattr(card, "suit", None)


def effective_color(game, card, owner):
    suit = effective_suit(game, card, owner)
    if suit in ("heart", "diamond"):
        return "red"
    if suit in ("spade", "club"):
        return "black"
    return getattr(card, "card_color", None)
