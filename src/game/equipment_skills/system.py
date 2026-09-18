"""Engine V2 equipment skills and resumable activation controller."""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom
from src.game.engine.domain_actions import UseCardAction
from src.game.engine.events import EventType
from src.game.engine.skills import Skill, SkillBinding
from src.game.engine.pending import PendingRequestType
from src.game.flows.judge import JudgeFlow
from src.game.rules import ArmorRule


def _equipment(player, slot, name):
    card = player.get_equipment(slot)
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
        )

    def resolve(self, context, event):
        if event.name is EventType.CARD_EFFECT_BEFORE:
            self._block_sha(event)
        elif event.name is EventType.DAMAGE_MODIFY:
            self._modify_damage(event)
        elif event.name is EventType.EQUIPMENT_LOST:
            card = event.payload.get("card")
            if card is not None and card.name == "BAIYIN" and event.target.hp < event.target.max_hp:
                result = context.apply(RecoverHpAtom(event.target, 1))
                event.payload["healed"] = result.data["amount"] > 0

    def _block_sha(self, event):
        card = event.payload.get("card")
        if card is None or card.name != "SHA" or not ArmorRule.is_effective(event.source, event.target, card):
            return
        armor = event.target.get_equipment("armor")
        if armor is None:
            return
        if armor.name == "RENWANG" and card.nature == "normal" and card.card_color == "black":
            event.payload["blocked_by"] = "仁王盾"
            event.cancel()
        elif armor.name == "TENGJIA" and card.nature == "normal":
            event.payload["blocked_by"] = "藤甲"
            event.cancel()

    def _modify_damage(self, event):
        damage = event.payload.get("damage")
        if damage is None:
            return
        source, target = damage.source, damage.target
        if damage.card is not None and damage.card.name == "SHA" and _equipment(source, "weapon", "GUDING") and not target.hand:
            damage.amount += 1
            damage.effects.append("【古锭刀】使伤害 +1")
        if not ArmorRule.is_effective(source, target, damage.card):
            if target.get_equipment("armor") is not None:
                damage.effects.append("【青釭剑】无视防具")
            return
        if _equipment(target, "armor", "TENGJIA") and damage.nature == "fire":
            damage.amount += 1
            damage.effects.append("【藤甲】使火焰伤害 +1")
        if _equipment(target, "armor", "BAIYIN") and damage.amount > 1:
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
        if _equipment(flow.target, "armor", "BAGUA") and ArmorRule.is_effective(flow.actor, flow.target, flow.card):
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

    def resume_sha(self, flow, resolution):
        if flow.stage == "bagua_confirm":
            if resolution.confirmed:
                result = JudgeFlow(self.engine, flow.target, "bagua").start().value
                if result is not None and result.color == "red":
                    flow.game.message = flow.target.name + "的【八卦阵】判定成功，视为使用【闪】。"
                    return flow.finish(cancelled=True)
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
                    flow.target.remove_equipment(slot); break
            flow.context.apply(MoveCardAtom(card, destination=flow.game.deck.discard_pile))
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
