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
                    card, source=owner.hand, destination=self.game.deck.discard_pile))

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
    request = engine.pending.create(
        PendingRequestType.CHOOSE_OPTION, source=source, target=target, prompt=prompt,
        owner_flow=flow, options=tuple(options),
        request_context=dict({"reason": reason}, **(context or {})))
    flow.wait(request)
    engine.present_or_auto_resolve(request)
    return request


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
