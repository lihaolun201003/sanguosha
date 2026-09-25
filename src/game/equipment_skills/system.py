"""Engine V2 equipment skills and resumable activation controller."""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom, UnequipAtom
from src.game.engine.domain_actions import UseCardAction
from src.game.engine.events import EventType
from src.game.engine.flows import FlowStatus
from src.game.engine.skills import Skill, SkillBinding
from src.game.engine.pending import PendingRequestType
from src.game.flows.judge import JudgeFlow
from src.game.rules import ArmorRule


def _equipment(player, slot, name):
    card = player.get_equipment(slot)
    return card is not None and card.name == name


def _armor(game, player, name):
    """防具判定统一走 ``Game.has_armor``：真实防具优先，其次是【八阵】一类
    技能赋予的虚拟防具。直接读 ``equipment`` 会让"视为装备八卦阵"永不生效。"""

    query = getattr(game, "has_armor", None)
    if callable(query):
        return bool(query(player, name))
    card = player.get_equipment("armor")
    return card is not None and card.name == name


class EquipmentEventSkill(Skill):
    """Locked equipment hooks; optional skills are handled by the controller."""

    skill_id = "equipment.locked_rules"
    name = "装备锁定技"

    def bindings(self):
        return (
            SkillBinding(EventType.CARD_EFFECT_BEFORE, priority=100),
            SkillBinding(EventType.DAMAGE_MODIFY, priority=10),
            SkillBinding(EventType.EQUIPMENT_LOST, priority=10),
            SkillBinding(EventType.CARD_RESPONDED, priority=30),
        )

    def resolve(self, context, event):
        if event.name is EventType.CARD_EFFECT_BEFORE:
            self._block_sha(context, event)
        elif event.name is EventType.DAMAGE_MODIFY:
            self._modify_damage(context, event)
        elif event.name is EventType.EQUIPMENT_LOST:
            card = event.payload.get("card")
            if card is not None and card.name == "BAIYIN" and event.target.hp < event.target.max_hp:
                result = context.apply(RecoverHpAtom(event.target, 1))
                event.payload["healed"] = result.data["amount"] > 0
        elif event.name is EventType.CARD_RESPONDED:
            self._yinyueqiang(context, event)

    # ---- 银月枪 ----
    #
    # 卡面：「在自己回合外，若打出一张黑色花色的牌，可立即指定攻击范围内的
    # 一名玩家出一张[闪]，否则减一点体力。」
    #
    # 两个必须核实的点都落在实现里：
    #   * 时机是**回合外 + 打出**（响应窗口），不是"使用"——因此订阅的是
    #     CARD_RESPONDED 而不是 CARD_USED；
    #   * 「减一点体力」是**失去体力**，不走伤害流程：不触发受伤类技能、
    #     不吃防具与伤害加成，但体力降到 0 依然进入濒死。

    def _yinyueqiang(self, context, event):
        actor = event.payload.get("actor")
        card = event.payload.get("card")
        if actor is None or card is None:
            return
        if not _equipment(actor, "weapon", "YINYUEQIANG"):
            return
        if getattr(card, "card_color", None) != "black":
            return
        game = context.state
        if game.current_turn_player is actor:
            return                      # 只在回合外
        candidates = self._yinyue_targets(game, actor)
        if not candidates:
            return
        # 引擎从 context 取：这个技能实例由装备控制器统一安装，本身没有
        # engine 字段，直接 self.engine 会在事件分发里抛 AttributeError，
        # 把整局拖垮（回合外打出黑牌就会走到这里）。
        YinyueqiangFlow(context.services["engine"], actor, candidates).start()

    @staticmethod
    def _yinyue_targets(game, actor):
        from src.game.rules import DistanceRule

        return [
            other for other in game.seats.alive_players_in_order(start_after=actor)
            if other is not actor and DistanceRule.in_attack_range(game, actor, other)
        ]

    def _block_sha(self, context, event):
        card = event.payload.get("card")
        if card is None or card.name != "SHA" or not ArmorRule.is_effective(event.source, event.target, card):
            return
        game = context.state
        if _armor(game, event.target, "RENWANG") and card.nature == "normal" and card.card_color == "black":
            event.payload["blocked_by"] = "仁王盾"
            event.cancel()
        elif _armor(game, event.target, "TENGJIA") and card.nature == "normal":
            event.payload["blocked_by"] = "藤甲"
            event.cancel()

    def _modify_damage(self, context, event):
        damage = event.payload.get("damage")
        if damage is None:
            return
        game = context.state
        source, target = damage.source, damage.target
        if damage.card is not None and damage.card.name == "SHA" and _equipment(source, "weapon", "GUDING") and not target.hand:
            damage.amount += 1
            damage.effects.append("【古锭刀】使伤害 +1")
        if getattr(damage, "ignore_armor", False) or not ArmorRule.is_effective(
                source, target, damage.card):
            if game.armor_card(target) is not None or game.virtual_armor(target):
                if not getattr(damage, "ignore_armor", False):
                    damage.effects.append("【青釭剑】无视防具")
            return
        if _armor(game, target, "TENGJIA") and damage.nature == "fire":
            damage.amount += 1
            damage.effects.append("【藤甲】使火焰伤害 +1")
        if _armor(game, target, "BAIYIN") and damage.amount > 1:
            damage.amount = 1
            damage.effects.append("【白银狮子】将伤害改为 1")


class EquipmentSkillController:
    SKILL_IDS = {
        "ZHUGE": "equipment.zhuge", "CIXIONG": "equipment.cixiong",
        "HANBING": "equipment.hanbing", "QINGGANG": "equipment.qinggang",
        "GUDING": "equipment.guding", "QINGLONG": "equipment.qinglong",
        "ZHANGBA": "equipment.zhangba", "GUANSHI": "equipment.guanshi",
        "FANGTIAN": "equipment.fangtian", "ZHUQUE": "equipment.zhuque",
        "QILIN": "equipment.qilin", "BAGUA": "equipment.bagua",
        "RENWANG": "equipment.renwang", "TENGJIA": "equipment.tengjia",
        "BAIYIN": "equipment.baiyin", "HORSE_DISTANCE": "equipment.horse_distance",
    }

    def __init__(self, engine):
        self.engine = engine
        self.locked = EquipmentEventSkill()
        self.locked.install(engine.context)

    def before_sha_response(self, flow):
        # Weapon triggers below belong to the process of *using Sha*.
        # Responding with Sha to Duel/Nanman never enters this hook, and the
        # explicit guard prevents future non-Sha CardEffects from reusing it
        # accidentally (especially Cixiong Twin Swords).
        if flow.card is None or flow.card.name != "SHA":
            return False
        if _armor(self.engine.game, flow.target, "BAGUA") and ArmorRule.is_effective(flow.actor, flow.target, flow.card):
            request = self.engine.pending.create(
                PendingRequestType.CONFIRM, source=flow.actor, target=flow.target,
                prompt="是否发动【八卦阵】？", owner_flow=flow,
                request_context={"reason": "bagua"},
            )
            flow.stage = "bagua_confirm"
            flow.wait(request)
            self.engine.present_or_auto_resolve(request)
            return True
        if _equipment(flow.actor, "weapon", "CIXIONG") and flow.actor.gender != flow.target.gender:
            options = ("discard", "draw") if flow.target.hand else ("draw",)
            request = self.engine.pending.create(
                PendingRequestType.CHOOSE_OPTION, source=flow.actor, target=flow.target,
                prompt="【雌雄双股剑】：弃一张手牌，或令对方摸一张", owner_flow=flow,
                options=options, request_context={"reason": "cixiong"},
            )
            flow.stage = "cixiong_option"
            flow.wait(request)
            self.engine.present_or_auto_resolve(request)
            return True
        return False

    def _resume_bagua(self, flow, result):
        """改判窗口结束（或从未打开）后，按最终判定牌结算八卦阵。"""

        if flow.status in (FlowStatus.COMPLETED, FlowStatus.CANCELLED):
            return flow.current_result()
        flow.pending_request = None
        flow.status = FlowStatus.RUNNING
        if result is not None and result.color == "red":
            flow.game.message = flow.target.name + "的【八卦阵】判定成功，视为使用【闪】。"
            return flow.finish(cancelled=True)
        return flow.effect.request_shan(flow)

    def resume_sha(self, flow, resolution):
        if flow.stage == "bagua_confirm":
            if resolution.confirmed:
                judge = JudgeFlow(self.engine, flow.target, "bagua")
                outcome = judge.start()
                if outcome.status is FlowStatus.WAITING:
                    # 判定进入改判窗口：先挂起本次【杀】，等改判结束后再按
                    # 最终判定牌决定八卦阵是否生效。
                    judge.on_complete = lambda result: self._resume_bagua(flow, result)
                    flow.wait(judge)
                    return flow.current_result()
                return self._resume_bagua(flow, outcome.value)
            return flow.effect.request_shan(flow)
        if flow.stage == "cixiong_option":
            if resolution.option == "draw":
                flow.context.apply(DrawCardsAtom(flow.actor, 1))
                return flow.effect.request_shan(flow)
            candidates = list(flow.target.hand)
            request = self.engine.pending.create(
                PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.target,
                prompt="请选择一张手牌弃置", owner_flow=flow, min_cards=1, max_cards=1,
                request_context={"reason": "cixiong_discard", "candidates": candidates, "zone_owner": flow.target},
            )
            flow.stage = "cixiong_select"
            flow.wait(request); self.engine.present_or_auto_resolve(request)
            return flow.current_result()
        if flow.stage == "cixiong_select":
            flow.context.apply(MoveCardAtom(resolution.cards[0], source=flow.target.hand, destination=flow.game.deck.discard_pile))
            return flow.effect.request_shan(flow)
        if flow.stage == "qinglong_confirm":
            if not resolution.confirmed:
                return flow.finish(cancelled=True)
            candidates = [card for card in flow.actor.hand if card.name == "SHA"]
            request = self.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.actor, prompt="请选择追杀使用的【杀】", owner_flow=flow, min_cards=1, max_cards=1, request_context={"reason": "qinglong", "candidates": candidates, "zone_owner": flow.actor})
            flow.stage = "qinglong_select"; flow.wait(request); self.engine.present_or_auto_resolve(request)
            return flow.current_result()
        if flow.stage == "qinglong_select":
            card = resolution.cards[0]
            flow.wait({"reason": "qinglong_child"})
            self.engine.submit(UseCardAction(flow.actor, card, [flow.target], ignore_usage_limit=True, on_complete=lambda _: flow.finish(cancelled=False)))
            return flow.current_result()
        if flow.stage == "guanshi_confirm":
            if not resolution.confirmed:
                return flow.finish(cancelled=True)
            request = self.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.actor, prompt="请选择两张手牌弃置", owner_flow=flow, min_cards=2, max_cards=2, request_context={"reason": "guanshi", "candidates": list(flow.actor.hand), "zone_owner": flow.actor})
            flow.stage = "guanshi_select"; flow.wait(request); self.engine.present_or_auto_resolve(request)
            return flow.current_result()
        if flow.stage == "guanshi_select":
            for card in resolution.cards:
                flow.context.apply(MoveCardAtom(card, source=flow.actor.hand, destination=flow.game.deck.discard_pile))
            return flow.start_damage()
        if flow.stage == "hanbing_confirm":
            if not resolution.confirmed:
                return self._start_raw_damage(flow)
            count = min(2, len(flow.target.hand))
            request = self.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.actor, prompt="请选择目标至多两张手牌弃置", owner_flow=flow, min_cards=count, max_cards=count, request_context={"reason": "hanbing", "candidates": list(flow.target.hand), "zone_owner": flow.target})
            flow.stage = "hanbing_select"; flow.wait(request); self.engine.present_or_auto_resolve(request)
            return flow.current_result()
        if flow.stage == "hanbing_select":
            for card in resolution.cards:
                flow.context.apply(MoveCardAtom(card, source=flow.target.hand, destination=flow.game.deck.discard_pile))
            return flow.finish(cancelled=False)
        if flow.stage == "qilin_confirm":
            if not resolution.confirmed:
                return flow.finish(cancelled=False)
            horses = [card for slot, card in flow.target.equipment.items() if "horse" in slot and card]
            request = self.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.actor, prompt="请选择一匹坐骑弃置", owner_flow=flow, min_cards=1, max_cards=1, request_context={"reason": "qilin", "candidates": horses, "zone_owner": flow.target})
            flow.stage = "qilin_select"; flow.wait(request); self.engine.present_or_auto_resolve(request)
            return flow.current_result()
        if flow.stage == "qilin_select":
            card = resolution.cards[0]
            for slot, current in flow.target.equipment.items():
                if current is card:
                    flow.context.apply(UnequipAtom(flow.target, slot, flow.game.deck.discard_pile))
                    break
            return flow.finish(cancelled=False)
        return None

    def on_sha_dodged(self, flow):
        if _equipment(flow.actor, "weapon", "GUANSHI") and len(flow.actor.hand) >= 2:
            return self._confirm(flow, "guanshi_confirm", "是否发动【贯石斧】弃置两张手牌令杀命中？")
        if _equipment(flow.actor, "weapon", "QINGLONG") and any(card.name == "SHA" for card in flow.actor.hand):
            return self._confirm(flow, "qinglong_confirm", "是否发动【青龙偃月刀】继续出杀？")
        return None

    def before_sha_damage(self, flow):
        if _equipment(flow.actor, "weapon", "HANBING") and flow.target.hand:
            return self._confirm(flow, "hanbing_confirm", "是否发动【寒冰剑】防止伤害并弃置目标手牌？")
        return None

    def after_sha_damage(self, flow):
        horses = [card for slot, card in flow.target.equipment.items() if "horse" in slot and card]
        if _equipment(flow.actor, "weapon", "QILIN") and horses:
            return self._confirm(flow, "qilin_confirm", "是否发动【麒麟弓】弃置目标坐骑？")
        return None

    def _confirm(self, flow, stage, prompt):
        request = self.engine.pending.create(PendingRequestType.CONFIRM, source=flow.actor, target=flow.actor, prompt=prompt, owner_flow=flow, request_context={"reason": stage})
        flow.stage = stage; flow.wait(request); self.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def _start_raw_damage(self, flow):
        from src.game.flows.damage import DamageContext, DamageFlow
        flow.stage = "waiting_for_damage"
        child = DamageFlow(self.engine, DamageContext(flow.actor, flow.target, flow.base_damage, getattr(flow.card, "nature", "normal"), flow.card), on_complete=flow._after_damage)
        result = child.start()
        return flow.current_result() if flow.status.value in ("completed", "cancelled") else result


class YinyueqiangFlow:
    """银月枪：指定攻击范围内的一名角色打出一张【闪】，否则其失去 1 点体力。

    这是装备的**可选**效果，因此先问持有者要不要发动；目标答不出【闪】时
    由规则层扣体力（``Game.lose_hp``），不走伤害流程。
    """

    def __init__(self, owner_controller, actor, candidates):
        self.controller = owner_controller
        self.game = owner_controller.game
        self.actor = actor
        self.candidates = list(candidates)
        self.target = None
        self.stage = "confirm"

    @property
    def pending_request(self):
        return self._request

    @property
    def status(self):
        from src.game.engine.flows import FlowStatus

        return FlowStatus.WAITING

    def start(self):
        from src.game.engine.pending import PendingRequestType

        engine = self.controller
        game = self.game
        self._request = engine.pending.create(
            PendingRequestType.CONFIRM,
            source=self.actor, target=self.actor,
            prompt="【银月枪】：是否指定攻击范围内的一名角色出【闪】？",
            owner_flow=self,
            request_context={"reason": "yinyueqiang_confirm"},
        )
        engine.present_or_auto_resolve(self._request)
        return None

    def current_result(self):
        from src.game.engine.flows import FlowResult, FlowStatus

        return FlowResult(FlowStatus.WAITING, None)

    def resume(self, response):
        from src.game.engine.flows import FlowResult, FlowStatus
        from src.game.engine.pending import PendingRequestType

        if self.stage == "confirm":
            if response is None or not response.confirmed or not self.candidates:
                return FlowResult(FlowStatus.COMPLETED, None)
            self.stage = "target"
            self._request = self.controller.pending.create(
                PendingRequestType.SELECT_TARGETS,
                source=self.actor, target=self.actor,
                prompt="【银月枪】：请选择目标",
                owner_flow=self,
                min_cards=1, max_cards=1,
                request_context={
                    "reason": "yinyueqiang_target",
                    "candidates": list(self.candidates),
                },
            )
            self.controller.present_or_auto_resolve(self._request)
            return FlowResult(FlowStatus.WAITING, None)

        if self.stage == "shan":
            return self.advance(response)
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return FlowResult(FlowStatus.COMPLETED, None)
        return self._ask_shan(targets[0])

    def _ask_shan(self, target):
        from src.game.engine.flows import FlowResult, FlowStatus
        from src.game.engine.pending import PendingRequestType

        if not self.controller.game.card_actions.can_respond(target, allowed_names=("SHAN",)):
            return self._punish(target)
        self.target = target
        self.stage = "shan"
        self._request = self.controller.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=self.actor, target=target,
            prompt="【银月枪】：请打出一张【闪】，否则失去 1 点体力",
            owner_flow=self,
            allowed_cards=frozenset(("SHAN",)),
            request_context={"reason": "yinyueqiang_shan"},
        )
        self.controller.present_or_auto_resolve(self._request)
        return FlowResult(FlowStatus.WAITING, None)

    def _punish(self, target):
        from src.game.engine.flows import FlowResult, FlowStatus

        self.game.lose_hp(target, 1, source=self.actor, cause="银月枪")
        self.game.add_log("【银月枪】：%s 未能打出【闪】，失去 1 点体力" % target.name)
        return FlowResult(FlowStatus.COMPLETED, None)

    def advance(self, response=None):
        if self.stage == "shan":
            if response is not None and getattr(response, "card", None) is not None:
                self.game.add_log("【银月枪】：%s 打出了【闪】" % self.target.name)
                from src.game.engine.flows import FlowResult, FlowStatus

                return FlowResult(FlowStatus.COMPLETED, None)
            return self._punish(self.target)
        return self.resume(response)
