"""First playable standard trick-card effects."""

from src.card import DISPLAY_NAMES
from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom, TransferEquipmentAtom, SetChainedAtom, UnequipAtom
from src.game.engine import Event, EventType, FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.flows.response_requirement import ResponseRequirement
from src.game.rules import DistanceRule, TargetRule

from src.game.engine.flows import Flow

from .base import AuxiliaryInput, CardEffect


class WuzhongEffect(CardEffect):
    card_name = "WUZHONG"
    target_rule = TargetRule.SELF
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def begin(self, flow):
        flow.context.apply(DrawCardsAtom(flow.actor, 2))
        flow.game.message = flow.actor.name + "使用【无中生有】摸两张牌。"
        return flow.finish(cancelled=False)


class _ChooseTargetCardEffect(CardEffect):
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    destination_is_actor = False

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        target = list(action.targets)[0]
        if not target.hand and not any(target.equipment.values()):
            return False, "目标没有可选择的牌。"
        limit = self.distance_limit_for(game, action.actor, action.card)
        if (
            limit is not None
            and not game.ignores_trick_range(action.actor)
            and not DistanceRule.in_range(game, action.actor, target, limit)
        ):
            return False, "目标距离过远。"
        return True, ""

    def begin(self, flow):
        target = flow.targets[0]
        candidates = list(target.hand) + [card for card in target.equipment.values() if card]
        request = flow.engine.pending.create(
            PendingRequestType.SELECT_CARDS,
            source=flow.actor, target=flow.actor,
            prompt="请选择" + target.name + "区域内的一张牌",
            owner_flow=flow, min_cards=1, max_cards=1,
            request_context={"reason": self.card_name.lower(), "candidates": candidates, "zone_owner": target},
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        card = resolution.cards[0]
        target = flow.targets[0]
        source = target.hand
        if not any(item is card for item in source):
            # 装备区的牌统一走 UnequipAtom 离场：失去装备事件由它发出。
            for slot, equipped in target.equipment.items():
                if equipped is card:
                    flow.context.apply(UnequipAtom(target, slot))
                    source = None
                    break
        destination = flow.actor.hand if self.destination_is_actor else flow.game.deck.discard_pile
        flow.context.apply(MoveCardAtom(card, source=source, destination=destination))
        # 展示被拿走 / 被弃置的那张牌并停留片刻，让真人看清发生了什么。
        flow.engine.show_taken_card(
            card, target, flow.actor, to_hand=self.destination_is_actor)
        flow.game.message = flow.actor.name + "使用【" + flow.card.display_name + "】获得一张牌。" if self.destination_is_actor else flow.actor.name + "使用【过河拆桥】弃置一张牌。"
        return flow.finish(cancelled=False)


class GuoheEffect(_ChooseTargetCardEffect):
    card_name = "GUOHE"
    cancellable_by_wuxie = True


class ShunshouEffect(_ChooseTargetCardEffect):
    card_name = "SHUNSHOU"
    destination_is_actor = True
    distance_limit = 1
    cancellable_by_wuxie = True


class DuelEffect(CardEffect):
    """决斗：双方轮流打出【杀】，先交不出来的一方受到 1 点伤害。

    每一轮"需要交出几张【杀】"由**响应者的对手**决定（RESPONSE_COUNT
    modifier，例如无双 = 2）。所以吕布无论主动使用决斗还是被决斗，他的
    对手每轮都要连续打出两张【杀】，而吕布自己每轮仍然只要一张。
    """

    card_name = "JUEDOU"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    can_respond = True
    cancellable_by_wuxie = True

    def begin(self, flow):
        flow.effect_state = {"responder": flow.targets[0], "other": flow.actor}
        return self._open_round(flow)

    def _open_round(self, flow):
        """开始一轮：响应者换人，需求按对手的 modifier 重新计算。"""

        state = flow.effect_state
        responder = state["responder"]
        opponent = state["other"]
        state["requirement"] = ResponseRequirement.for_source(
            flow.game, opponent, responder, flow.card, {"SHA"}, "duel")
        return self._ask(flow)

    def _ask(self, flow):
        requirement = flow.effect_state["requirement"]
        responder = flow.effect_state["responder"]
        request = requirement.create_request(
            flow.engine,
            flow=flow,
            source=flow.actor,
            responder=responder,
            prompt="【决斗】：请打出【杀】，或选择不响应",
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        state = flow.effect_state
        requirement = state["requirement"]
        if resolution.card is not None:
            # 这一张【杀】已经由引擎真实移出手牌：即使下一张交不出来，
            # 也不会退回。
            if not requirement.accept(resolution.card):
                return self._ask(flow)
            state["responder"], state["other"] = state["other"], state["responder"]
            return self._open_round(flow)
        loser = resolution.actor
        winner = state["other"]
        damage = DamageFlow(flow.engine, DamageContext(winner, loser, 1, card=flow.card), on_complete=lambda _: flow.finish(cancelled=False))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            flow.stage = "effect_child"
            return flow.wait(damage)
        return flow.finish(cancelled=False)


class _MassResponseEffect(CardEffect):
    """群体锦囊（南蛮入侵 / 万箭齐发）：逐目标要求响应牌。

    生命周期刻意分成两段，二者不可混用：

    * ``TRICK_NEGATION_WINDOW``——【无懈可击】窗口。它属于**锦囊本身**，
      由 ``UseCardFlow`` 在效果开始之前统一开启一次，所有目标共用同一条
      无懈链（无懈套无懈照常反转）。被抵消则整张牌结束。
    * ``CARD_RESPONSE_REQUIREMENT``——效果开始之后逐个目标要求
      【杀】/【闪】。这一段只推进目标下标，**绝不再打开无懈链**。

    因此这里的 ``cancellable_by_wuxie`` 仍然为真（锦囊本身可被无懈），
    但效果阶段自身不产生任何无懈请求。
    """

    target_rule = TargetRule.ALL_OTHERS
    response_name = None
    cancellable_by_wuxie = True
    sequential_targets = True

    def begin(self, flow):
        flow.effect_state = {"index": 0}
        return self._next(flow)

    def _next(self, flow):
        index = flow.effect_state["index"]
        if index >= len(flow.targets):
            return flow.finish(cancelled=False)
        target = flow.targets[index]
        if not target.alive or target.hp <= 0:
            flow.effect_state["index"] += 1
            return self._next(flow)
        # 免疫类能力（【巨象】【祸首】"【南蛮入侵】对你无效"）：他仍然是这张牌的
        # 目标，但不响应、也不受伤。这类能力以前被写成"不能成为目标"，于是
        # 出牌时算出的目标比校验时多一个，整张牌卡在 validate_targets 上出不来。
        if self._immune(flow, target):
            flow.effect_state["index"] += 1
            return self._next(flow)
        return self._request_response(flow)

    def _immune(self, flow, target):
        """这张牌对这名目标是否无效（走规则层查询，不认具体武将）。"""

        checker = getattr(flow.game, "target_forbidden", None)
        if not callable(checker):
            return False
        return bool(checker(target, source=flow.actor, card=flow.card))

    def _request_response(self, flow):
        target = flow.targets[flow.effect_state["index"]]
        display = DISPLAY_NAMES.get(self.response_name, self.response_name)
        request = flow.engine.pending.create(
            PendingRequestType.RESPOND_CARD, source=flow.actor, target=target,
            prompt="【" + flow.card.display_name + "】：请打出【" + display + "】，或选择不响应",
            owner_flow=flow, allowed_cards={self.response_name}, min_cards=0, max_cards=1,
            request_context={
                "reason": self.card_name.lower(),
                "card": flow.card,
                # 逐目标响应请求：UI 据此一次只展示当前目标的箭头与提示。
                "sequential_targets": True,
            },
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        if resolution.card is not None:
            flow.effect_state["index"] += 1
            return self._next(flow)
        target = resolution.actor
        # 伤害来源走规则层查询：默认是使用这张锦囊的角色，【祸首】一类能力
        # 可以把它改写成别人（"你是任何【南蛮入侵】造成伤害的来源"）。
        damage_source = flow.game.trick_source(flow.actor, flow.card)
        damage = DamageFlow(flow.engine, DamageContext(damage_source, target, 1, card=flow.card), on_complete=lambda _: self._after_damage(flow))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            flow.stage = "effect_child"
            return flow.wait(damage)
        return flow.current_result()

    def _after_damage(self, flow):
        if flow.status is FlowStatus.COMPLETED:
            return flow.current_result()
        flow.effect_state["index"] += 1
        return self._next(flow)


class NanmanEffect(_MassResponseEffect):
    card_name = "NANMAN"
    response_name = "SHA"
    cancellable_by_wuxie = True


class WanjianEffect(_MassResponseEffect):
    card_name = "WANJIAN"
    response_name = "SHAN"
    cancellable_by_wuxie = True


class TaoyuanEffect(CardEffect):
    card_name = "TAOYUAN"
    target_rule = TargetRule.ALL_PLAYERS
    cancellable_by_wuxie = True

    def begin(self, flow):
        for target in flow.targets:
            if target.alive and target.hp > 0:
                flow.context.apply(RecoverHpAtom(target, 1))
        flow.game.message = "【桃园结义】令所有存活角色回复体力。"
        return flow.finish(cancelled=False)


class WuguEffect(CardEffect):
    card_name = "WUGU"
    target_rule = TargetRule.ALL_PLAYERS
    cancellable_by_wuxie = True

    def begin(self, flow):
        pool = flow.game.public_card_pool
        pool.clear()
        for _ in flow.targets:
            card = flow.game.deck.draw()
            if card is not None:
                pool.append(card)
        flow.effect_state = {"order": list(flow.targets), "index": 0}
        return self._choose(flow)

    def _choose(self, flow):
        pool = flow.game.public_card_pool
        index = flow.effect_state["index"]
        order = flow.effect_state["order"]
        while index < len(order) and order[index].hp <= 0:
            index += 1
        flow.effect_state["index"] = index
        if index >= len(order) or not pool:
            for card in list(pool):
                flow.context.apply(MoveCardAtom(card, source=pool, destination=flow.game.deck.discard_pile))
            return flow.finish(cancelled=False)
        selector = order[index]
        request = flow.engine.pending.create(
            PendingRequestType.SELECT_CARDS, source=flow.actor, target=selector,
            prompt="【五谷丰登】：请选择一张公共牌", owner_flow=flow,
            min_cards=1, max_cards=1,
            request_context={"reason": "wugu", "candidates": list(pool), "zone": "public_pool", "zone_owner": selector},
        )
        flow.stage = "effect_waiting"; flow.wait(request); flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        card = resolution.cards[0]
        flow.context.apply(MoveCardAtom(card, source=flow.game.public_card_pool, destination=resolution.actor.hand))
        flow.effect_state["index"] += 1
        return self._choose(flow)


class WuxieEffect(CardEffect):
    card_name = "WUXIE"
    target_rule = TargetRule.NO_TARGET

    def can_use(self, game, action):
        return False, "【无懈可击】只能在锦囊响应链中使用。"


# ==================================================
# 借刀杀人
# ==================================================

def _probe_sha(actor):
    """一张"只为合法性探测"的虚拟【杀】。

    用虚拟牌而不是新造的实体牌：``can_use`` 会检查"这张牌还在不在使用者
    手里"，凭空造一张实体牌永远过不了那道校验。虚拟牌的语义正好是"一张
    还没落到具体实体上的【杀】"，探测"能不能杀到某个人"用它最贴切。
    """

    from src.game.skills.mechanics import virtual_card

    return virtual_card("SHA", actor, (), category="basic")


def can_use_sha_on(game, actor, target):
    """actor 现在能不能对 target 使用一张【杀】。

    走真实的 CardEffect 判定：距离、攻击范围、目标规则、技能限制全部算进去，
    不在这里重写一套。借刀的两处都需要它——第二目标的候选、以及持武器者
    "还有没有合法【杀】可用"。
    """

    probe = _probe_sha(actor)
    effect = game.engine.card_effects.get(probe)
    if effect is None:                                    # pragma: no cover
        return False
    from src.game.engine import UseCardAction

    action = UseCardAction(actor, probe, [target], ignore_usage_limit=True)
    valid, _reason = effect.can_use(game, action)
    return bool(valid)


def jiedao_victim_candidates(game, actor, targets):
    """【借刀杀人】的第二目标：持武器者**真的能杀到**的其他角色。

    候选按规则完整筛选（存活 / 不是持武器者 / 不是使用者 / 在持武器者的
    攻击范围内 / 通过真实的【杀】合法性判定），而不是"所有活人"再在结算时
    拿第一名去试——那样第一个候选不合法就会错误地走到"交武器"。
    """

    if not targets:
        return []
    wielder = list(targets)[0]
    result = []
    for other in game.seats.alive_players_in_order(
            start_after=wielder, include_start=True):
        if other is wielder or other is actor:
            continue
        if not other.alive or other.hp <= 0:
            continue
        if can_use_sha_on(game, wielder, other):
            result.append(other)
    return result


def jiedao_sha_options(game, wielder, victim):
    """持武器者对指定角色可用的**全部**【杀】使用方式（含 View-As 转化）。

    统一走 Card Action Discovery：实体【杀】、火杀 / 雷杀、【武圣】【龙胆】
    一类"当【杀】使用"的转化都在里面。不写"只看 card.name == SHA"那种判断。
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


class JiedaoWielderFlow(Flow):
    """借刀后半段：**持武器者自己**决定"对指定角色使用【杀】"还是"交出武器"。

    三件事必须在玩家手里：

    * 出不出【杀】（不是系统看到有杀就替他出）；
    * 用哪一种【杀】（实体杀 / 火杀 / 武圣 / 龙胆…，伤害属性、素材与触发
      的技能都不一样）；
    * 不使用时要交出的武器，走统一装备离场入口。

    没有合法【杀】时直接交武器，不弹一个只能点"不杀"的假入口。
    """

    SHA = "sha"
    SURRENDER = "surrender"

    def __init__(self, owner_flow, options):
        super().__init__(owner_flow.context)
        self.owner_flow = owner_flow
        self.engine = owner_flow.engine
        self.game = owner_flow.game
        self.user = owner_flow.actor
        self.wielder = owner_flow.targets[0]
        self.victim = owner_flow.action.metadata.get("jiedao_victim")
        self.options = list(options)
        self.stage = "choose"

    # ---- 1. 出杀还是交武器 ----

    def begin(self):
        if self.victim is None or not self.options:
            return self._surrender()
        from src.game.skills.mechanics import ask_option

        ask_option(self.engine, self, source=self.user, target=self.wielder,
                   prompt="【借刀杀人】：对 %s 使用一张【杀】，或交出武器"
                          % self.victim.name,
                   reason="jiedao",
                   options=((self.SHA, "对 %s 使用【杀】" % self.victim.name),
                            (self.SURRENDER, "不使用【杀】，交出武器")))
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "choose":
            option = str(getattr(response, "option", "") or "")
            if option == self.SHA:
                return self._ask_source()
            return self._surrender()
        return self._use_sha(response)

    # ---- 2. 用哪一种【杀】 ----

    def _ask_source(self):
        if len(self.options) == 1:
            return self._submit(self.options[0])
        cards = []
        for option in self.options:
            for card in option.source_cards:
                if not any(card is other for other in cards):
                    cards.append(card)
        if len(cards) <= 1:
            return self._submit(self.options[0])
        from src.game.skills.mechanics import ask_cards

        self.stage = "source"
        ask_cards(self.engine, self, source=self.user, target=self.wielder,
                  prompt="【借刀杀人】：请选择要使用的【杀】",
                  reason="jiedao", candidates=cards, min_cards=1, max_cards=1)
        return self.current_result()

    def _use_sha(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self._surrender()
        chosen = cards[0]
        option = next(
            (item for item in self.options
             if any(card is chosen for card in item.source_cards)), None)
        if option is None:
            # 素材在收集期间被移走：重新校验失败就退回"交武器"，不硬来。
            return self._surrender()
        return self._submit(option)

    # ---- 3. 真正使用（走正常 UseCardFlow）----

    def _submit(self, option):
        actions = self.game.card_actions
        virtual = actions.effective_card(option)
        if (virtual is None or not self.wielder.alive or self.victim is None
                or not self.victim.alive or self.victim.hp <= 0):
            return self._surrender()
        for card in option.source_cards:
            if not any(item is card for item in self.wielder.hand):
                return self._surrender()
        from src.game.engine import UseCardAction

        self.stage = "using"
        self.engine.submit(UseCardAction(
            self.wielder, virtual, [self.victim],
            ignore_usage_limit=True,
            on_complete=lambda _result: self._after_sha(),
        ))
        return self.current_result()

    def _after_sha(self):
        """【杀】按正常流程结算完了（含闪响应 / 伤害 / 濒死）。"""

        self.game.add_log("%s 的【借刀杀人】结算完成" % self.user.name)
        return self.complete({"applied": True, "mode": self.SHA})

    # ---- 4. 交武器 ----

    def _surrender(self):
        weapon = self.wielder.get_equipment("weapon")
        if weapon is None or not self.wielder.alive:
            # 武器在流程中被别的效果移走了：不复制、不凭空造，直接结束。
            self.game.add_log("%s 的武器已经不在装备区，【借刀杀人】结束"
                              % self.wielder.name)
            return self.complete({"applied": True, "mode": "lost"})
        self.context.apply(TransferEquipmentAtom(
            self.wielder, "weapon", self.user.hand))
        self.game.add_log("%s 未使用【杀】，将武器交给 %s"
                          % (self.wielder.name, self.user.name))
        return self.complete({"applied": True, "mode": self.SURRENDER})


class JiedaoEffect(CardEffect):
    card_name = "JIEDAO"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        if list(action.targets)[0].get_equipment("weapon") is None:
            return False, "目标没有武器。"
        return True, ""

    def required_inputs(self, game, actor, targets, card=None):
        # 第二目标在**提交之前**由使用者自己选：它是规则要求的玩家决定
        # （"由你指定的另一名角色"），不是这张牌的牌面目标，所以走附加输入。
        if not targets:
            return ()
        return (AuxiliaryInput(
            key="jiedao_victim",
            prompt="【借刀杀人】：请指定 %s 使用【杀】的目标" % list(targets)[0].name,
            candidates=jiedao_victim_candidates,
        ),)

    def begin(self, flow):
        wielder = flow.targets[0]
        has_victim = "jiedao_victim" in flow.action.metadata
        victim = flow.action.metadata.get("jiedao_victim")
        if not has_victim:
            # 连"第二目标"这个选择都没做过：这次使用不合法（正常路径上它
            # 一定由使用者先选好；走到这里说明是绕过界面的提交）。
            # 静默替他挑一个才是真正的错误。
            flow.game.add_log("【借刀杀人】未指定被杀目标，本次使用无效")
            return flow.finish(cancelled=True)
        if victim is None or not victim.alive or victim.hp <= 0:
            # 收集时选定的第二目标此刻已不在场：无从要求出杀，进入交武器。
            return JiedaoWielderFlow(flow, []).start()
        options = jiedao_sha_options(flow.game, wielder, victim)
        return JiedaoWielderFlow(flow, options).start()


class _DelayedTrickEffect(CardEffect):
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        target = list(action.targets)[0]
        if any(card.name == action.card.name for card in target.judgement_zone):
            return False, "目标判定区已有同名延时锦囊。"
        return True, ""

    def begin(self, flow):
        # 进判定区的是**实体牌**：View-As 用出来的延时锦囊（徐晃【断粮】
        # 把黑色装备牌当【兵粮寸断】）本身是虚拟牌，它在任何区域里都不存在，
        # 直接移它会在原子层抛 "card is no longer in the expected source zone"。
        moved = False
        for card in flow.material_cards:
            if not any(item is card for item in flow.game.processing_zone):
                continue
            flow.context.apply(MoveCardAtom(
                card, source=flow.game.processing_zone,
                destination=flow.targets[0].judgement_zone))
            moved = True
        if not moved:
            # 素材已经不在处理区（被技能取走一类）：这张延时锦囊不落区，
            # 按普通锦囊收尾，不制造半截状态。
            flow.game.add_log("【%s】没有可用的实体牌，未置入判定区。" % flow.card.display_name)
            return flow.finish(cancelled=False)
        flow.keep_processing_card = True
        # 牌已经真实进入判定区：出牌动画留在桌面上的展示副本必须收掉，
        # 否则它会一直停在中央（同一张牌不允许有两个视觉位置）。出牌动画
        # 是异步的（播完才把牌放上桌面），所以等那份副本出现之后再移除。
        from src.actions import CallbackAction

        flow.game.actions.add(
            CallbackAction(lambda placed=flow.card: flow.game.remove_table_card(placed))
        )
        return flow.finish(cancelled=False)


class LebuEffect(_DelayedTrickEffect):
    card_name = "LEBU"


class BingliangEffect(_DelayedTrickEffect):
    card_name = "BINGLIANG"
    distance_limit = 1

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if (
            valid
            and not game.ignores_trick_range(action.actor)
            and not DistanceRule.in_range(game, action.actor, list(action.targets)[0], 1)
        ):
            return False, "目标距离超过 1。"
        return valid, message


class ShandianEffect(_DelayedTrickEffect):
    card_name = "SHANDIAN"
    target_rule = TargetRule.SELF


class HuogongEffect(CardEffect):
    card_name = "HUOGONG"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        return (False, "目标没有手牌。") if valid and not list(action.targets)[0].hand else (valid, message)

    def begin(self, flow):
        target = flow.targets[0]
        request = flow.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=target, prompt="【火攻】：展示一张手牌", owner_flow=flow, min_cards=1, max_cards=1, request_context={"reason": "huogong_reveal", "candidates": list(target.hand), "zone_owner": target})
        flow.effect_state = {}; flow.stage = "effect_waiting"; flow.wait(request); flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        if "suit" not in flow.effect_state:
            revealed = resolution.cards[0]
            flow.effect_state["suit"] = revealed.suit
            flow.game.revealed_card = revealed
            # 展示是**公开信息**：所有人都会看到这张牌（火攻专用界面也靠它
            # 把"对方翻出来的是什么"画出来）。事件只带真实存在的实体牌。
            flow.context.emit(Event(
                EventType.CARD_REVEALED, source=flow.targets[0], target=flow.actor,
                payload={"player": flow.targets[0], "card": revealed,
                         "reason": "huogong", "caster": flow.actor}))
            candidates = [card for card in flow.actor.hand if card.suit == revealed.suit]
            if not candidates:
                flow.game.revealed_card = None
                return flow.finish(cancelled=False)
            request = flow.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.actor, prompt="弃置一张与展示牌同花色的手牌", owner_flow=flow, min_cards=1, max_cards=1, request_context={"reason": "huogong_discard", "candidates": candidates, "zone_owner": flow.actor, "revealed_card": revealed, "revealed_by": flow.targets[0], "caster": flow.actor})
            flow.stage = "effect_waiting"; flow.wait(request); flow.engine.present_or_auto_resolve(request)
            return flow.current_result()
        flow.context.apply(MoveCardAtom(resolution.cards[0], source=flow.actor.hand, destination=flow.game.deck.discard_pile))
        flow.game.revealed_card = None
        damage = DamageFlow(flow.engine, DamageContext(flow.actor, flow.targets[0], 1, nature="fire", card=flow.card), on_complete=lambda _: flow.finish(cancelled=False))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            return flow.wait(damage)
        return flow.current_result()


class TiesuoEffect(CardEffect):
    card_name = "TIESUO"
    target_rule = TargetRule.MULTIPLE
    min_targets = 1
    max_targets = 2
    cancellable_by_wuxie = True
    # 铁索连环可以重铸（置入弃牌堆并摸一张牌）：本地 UI 的"连环／重铸"二选一、
    # AI 的兜底分支、远程控制器下发的重铸选项都读这个声明。
    can_recast = True

    def can_use(self, game, action):
        if action.metadata.get("recast"):
            if not any(card is action.card for card in action.actor.hand):
                return False, "牌不在手牌中。"
            return True, ""
        return super().can_use(game, action)

    def begin(self, flow):
        if flow.action.metadata.get("recast"):
            flow.context.apply(DrawCardsAtom(flow.actor, 1))
            return flow.finish(cancelled=False)
        for target in flow.targets:
            flow.context.apply(SetChainedAtom(target, not target.chained))
            flow.context.emit(__import__("src.game.engine", fromlist=["Event"]).Event(__import__("src.game.engine", fromlist=["EventType"]).EventType.CHAIN_STATE_CHANGED, source=flow.actor, target=target, payload={"chained": target.chained}))
        return flow.finish(cancelled=False)


TRICK_EFFECTS = (
    WuzhongEffect, GuoheEffect, ShunshouEffect, DuelEffect, NanmanEffect,
    WanjianEffect, TaoyuanEffect, WuguEffect, WuxieEffect, JiedaoEffect,
    LebuEffect, ShandianEffect, HuogongEffect, TiesuoEffect, BingliangEffect,
)
