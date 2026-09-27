"""第二批吴势力武将：孙权 / 吕蒙 / 大乔 / 甘宁 / 陆逊 / 黄盖。

每条技能都只描述"做什么"，具体能不能做、结算成什么，仍由引擎判定。
"""

from src.game.atoms_v2 import (
    DrawCardsAtom,
    LoseHpAtom,
    MoveCardAtom,
    RecoverHpAtom,
)
from src.game.conversion import (
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType
from src.game.engine.pending import PendingRequestType
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..mechanics import optional_trigger
from ..definitions import (
    ActiveSkillSpec,
    CostZone,
    ModifierSpec,
    PhaseReplacement,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


# ==================================================
# 孙权 · 制衡 / 救援
# ==================================================


#: 【制衡】的输入契约：弃**任意张牌**（手牌 + 装备区），然后摸等量的牌。
#: ``allowed_zones`` 是这条规则唯一的一处声明——候选、界面高亮、引擎校验、
#: 远程下发的都从它派生；没有声明区域的技能（例如【举荐】）仍然是原来的
#: "只能用手牌"，不会被这条改动顺带放宽。
ZHI_HENG_SPEC = ActiveSkillSpec(
    variable_cost=True,
    cost_prompt="【制衡】：请选择要弃置的牌",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)


def _zhiheng_cost_candidates(game, player):
    """这次发动可以支付的牌（与引擎校验、界面高亮同一份判断）。"""

    from src.game.skills.activation import cost_candidates

    return cost_candidates(game, player, ZHI_HENG_SPEC)


def _can_zhiheng(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("zhiheng", "used", 0):
        return False, "本阶段已经发动过"
    # 费用是"任意张**牌**"（手牌 + 装备区都算），不是"任意张手牌"：
    # 手牌空、装备区有牌时【制衡】照样能发动。
    if not _zhiheng_cost_candidates(game, player):
        return False, "没有可以弃置的牌"
    return True, ""


def _activate_zhiheng(game, player, target=None, cards=None):
    """费用牌已由引擎弃置；按**实际弃置数量**摸牌。"""

    count = len(cards or ())
    if count <= 0:
        return False
    player.skill_state.set("zhiheng", "used", 1, ResetScope.PHASE)
    game.engine.context.apply(DrawCardsAtom(player, count))
    game.add_log(player.name + " 发动【制衡】：弃置 " + str(count) + " 张牌并摸 " + str(count) + " 张牌")
    return True


class Jiuyuan(Skill):
    """主公技：其他吴势力角色对你使用【桃】时，你额外回复 1 点体力。"""

    id = "jiuyuan"
    name = "救援"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USED),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        card = event.payload.get("card")
        targets = event.payload.get("targets") or ()
        source = event.source
        if card is None or getattr(card, "name", None) != "TAO":
            return False
        if source is None or source is self.owner:
            return False
        if getattr(source, "kingdom", None) != "wu":
            return False
        return any(item is self.owner for item in targets)

    def resolve(self, context, event):
        context.apply(RecoverHpAtom(self.owner, 1))
        context.state.add_log(
            self.owner.name + " 发动【救援】，额外回复 1 点体力")


# ==================================================
# 吕蒙 · 克己
# ==================================================


def _keji_can_offer(game, player):
    if game.game_over or not player.alive:
        return False
    if game.current_turn_player is not player:
        return False
    # 本回合没有使用或打出过【杀】才能发动（引擎维护的 sha_used 标记）。
    return not getattr(player, "sha_used", False)


def _keji_apply(game, player):
    """替代弃牌阶段：本回合没用过【杀】则跳过。"""

    game.add_log(player.name + " 发动【克己】，跳过弃牌阶段")
    return True


# ==================================================
# 大乔 · 国色 / 流离
# ==================================================


def _is_diamond(card):
    """方块实体牌。"""

    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "suit", None) == "diamond"


def flow_of(event):
    """事件里带的 UseCardFlow（BECOME_TARGET 等事件会带上）。"""

    return event.payload.get("flow")


def _game_of(context):
    """从 GameContext 取 game（技能钩子需要查座次 / 攻击范围时用）。"""

    engine = getattr(context, "services", {}).get("engine")
    return getattr(engine, "game", None)


def _liuli_targets(game, source, owner):
    """可以接管这张【杀】的角色：原攻击者攻击范围内的其他角色。"""

    from src.game.rules import DistanceRule

    if source is None:
        return []
    return [
        other for other in game.get_alive_players()
        if other is not owner and other is not source
        and DistanceRule.in_attack_range(game, source, other)
    ]


def _liuli_cost_cards(owner):
    """流离的费用：**一张牌**——手牌或装备区的牌都可以（卡面只说"弃置一张牌"）。"""

    cards = list(getattr(owner, "hand", ()) or ())
    for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
        card = owner.get_equipment(slot)
        if card is not None:
            cards.append(card)
    return cards


class Liuli(Skill):
    """成为【杀】的目标时，弃一张牌把这张【杀】转移给攻击范围内的另一名角色。

    两件事必须在玩家手里，而且**顺序**不能反：

    * 先选"转移给谁"（取消 = 不发动：不弃牌、不改目标）；
    * 再选"弃哪一张牌"——以前固定弃第一张手牌，装备区的牌连碰都碰不到，
      玩家对代价没有任何选择权。

    转移通过修改当前 CardAction 的目标完成，**不会取消原杀再生成一张新的杀**。
    """

    id = "liuli"
    name = "流离"

    def bindings(self):
        return (SkillBinding(EventType.BECOME_TARGET),)

    def can_trigger(self, context, event):
        if event.target is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        if not _liuli_cost_cards(self.owner):
            return False
        game = getattr(flow_of(event), "game", None) or _game_of(context)
        if game is None:
            return False
        return bool(_liuli_targets(game, event.source, self.owner))

    def resolve(self, context, event):
        flow = event.payload.get("flow")
        if flow is None:
            return
        game = getattr(flow, "game", None) or _game_of(context)
        if game is None:
            return
        candidates = _liuli_targets(game, event.source, self.owner)
        if not candidates:
            return

        engine = context.services.get("engine")
        if engine is None:
            return

        # 真人与 AI 走同一条通道：引擎把"是否发动 + 转移给谁"作为一次选目标
        # 请求派给流离的拥有者（min_cards=0 表示可以直接放弃）。这里不判断
        # 谁在回答，也不为真人写专用分支。
        request = engine.pending.create(
            PendingRequestType.SELECT_TARGETS,
            source=flow.actor,
            target=self.owner,
            prompt="【流离】：可弃置一张牌，将此【杀】转移给一名其他角色",
            owner_flow=flow,
            min_cards=0,
            max_cards=1,
            request_context={
                "reason": "liuli",
                "candidates": candidates,
                "card": flow.card,
                "zone_owner": self.owner,
            },
        )
        flow.redirect_resolver = (
            lambda target_flow, resolution:
            self._ask_cost(engine, game, target_flow, resolution))
        # 交由 UseCardFlow 挂起并进入"目标重定向"阶段（通用扩展点）。
        flow.target_redirect = request

    # ---- 第一步之后：选要弃的那张牌 ----

    def _ask_cost(self, engine, game, flow, resolution):
        chosen = list(getattr(resolution, "targets", None) or ())
        if not chosen:
            game.add_log(self.owner.name + " 放弃发动【流离】")
            return True
        new_target = chosen[0]
        if not any(item is new_target for item in _liuli_targets(
                game, flow.actor, self.owner)):
            # 目标在窗口期间已经不再合法：原杀照常结算，不弃牌。
            return True
        cards = _liuli_cost_cards(self.owner)
        if not cards:
            return True
        request = engine.pending.create(
            PendingRequestType.SELECT_CARDS,
            source=flow.actor,
            target=self.owner,
            prompt="【流离】：请选择要弃置的一张牌（手牌或装备牌；可放弃）",
            owner_flow=flow,
            min_cards=0,
            max_cards=1,
            request_context={
                "reason": "liuli",
                "candidates": cards,
                "zone": "hand",
                "zone_owner": self.owner,
                # 支付这一步也可以放弃：放弃 = 不弃牌、不改目标，
                # 原【杀】照常落在自己身上（规则上流离是"可以"）。
                "cancellable": True,
                "redirect_target": new_target,
            },
        )
        flow.redirect_resolver = (
            lambda target_flow, resolution, target=new_target:
            self._apply_cost(game, target_flow, resolution, target))
        flow.target_redirect = request
        # 还要再问一次：保持挂起，等这张牌选定后才改目标。
        return False

    # ---- 第二步：支付费用并改目标 ----

    def _apply_cost(self, game, flow, resolution, new_target):
        cards = list(getattr(resolution, "cards", ()) or ())
        cost = next((card for card in cards if _owns_card(self.owner, card)), None)
        if cost is None:
            game.add_log(self.owner.name + " 没有弃牌，【流离】不生效")
            return True
        self._discard_cost(game, cost)
        if not self.owner.alive:
            return True
        if not any(item is new_target for item in _liuli_targets(
                game, flow.actor, self.owner)):
            return True

        # 直接改当前这次用牌的结算目标：原杀不取消、不重建，
        # 属性 / 酒 / 武器修正等 context 全部原样保留。
        old_targets = list(getattr(flow, "targets", []) or [])
        replaced = [new_target if item is self.owner else item for item in old_targets]
        if not replaced:
            replaced = [new_target]
        flow.targets = replaced
        if getattr(flow, "target", None) is self.owner:
            flow.target = new_target

        self.owner.skill_state.add(self.id, "used", 1, ResetScope.TURN)
        game.add_log(
            self.owner.name + " 发动【流离】：弃置 " + cost.display_name
            + "，将【杀】转移给 " + new_target.name)
        return True

    def _discard_cost(self, game, card):
        """弃掉费用：手牌直接进弃牌堆，装备区的牌走统一离场入口。"""

        if any(item is card for item in getattr(self.owner, "hand", ())):
            game.engine.context.apply(MoveCardAtom(
                card, source=self.owner.hand,
                destination=game.deck.discard_pile))
            return
        for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
            if self.owner.get_equipment(slot) is card:
                from src.game.atoms_v2 import DISCARD_REASON, UnequipAtom

                game.engine.context.apply(UnequipAtom(
                    self.owner, slot, game.deck.discard_pile,
                    reason=DISCARD_REASON))
                return


def _owns_card(player, card):
    if any(item is card for item in getattr(player, "hand", ())):
        return True
    return any(card is equipped
               for equipped in (getattr(player, "equipment", None) or {}).values())


# ==================================================
# 甘宁 · 奇袭
# ==================================================


def _is_black_card(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "black"


# ==================================================
# 陆逊 · 谦逊 / 连营
# ==================================================


def _qianxun_forbidden(game, query):
    """谦逊：不能成为【顺手牵羊】或【乐不思蜀】的目标。"""

    card = query.get("card")
    if card is None:
        return 0
    return 1 if getattr(card, "name", None) in ("SHUNSHOU", "LEBU") else 0


class Lianying(Skill):
    """当你失去最后一张手牌时，摸一张牌。

    监听的是通用的"牌离开某区域"原子，因此弃牌 / 被顺 / 被拆 / 交给他人
    都会正确触发，而不是只在主动出牌时判断。
    """

    id = "lianying"
    name = "连营"

    def bindings(self):
        return (SkillBinding(EventType.ATOM_AFTER),)

    def can_trigger(self, context, event):
        atom = event.payload.get("atom")
        if atom is None or atom.__class__.__name__ != "MoveCardAtom":
            return False
        owner = self.owner
        if not owner.alive or owner.hp <= 0:
            return False
        # 牌是从自己的手牌区出去的，而且出去之后手牌空了。
        if getattr(atom, "source", None) is not owner.hand:
            return False
        return len(owner.hand) == 0

    def resolve(self, context, event):
        # 一次结算只触发一次：搬牌本身会把新摸的牌再搬一次，这里用回合标记去重。
        if self.owner.skill_state.get(self.id, "armed", 0):
            return
        optional_trigger(
            context, self.owner,
            prompt="【连营】：是否摸一张牌？", reason="lianying", label="连营",
            effect=self._draw).start()

    def _draw(self, flow):
        self.owner.skill_state.set(self.id, "armed", 1, ResetScope.PHASE)
        flow.context.apply(DrawCardsAtom(self.owner, 1))
        flow.game.add_log(self.owner.name + " 发动【连营】，摸一张牌")
        return True


# ==================================================
# 黄盖 · 苦肉
# ==================================================


def _can_kurou(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    # 规则文本没有"体力不足"的限制：1 点体力时发动会把自己送进濒死，
    # 那是合法的用法（濒死由通用的 DyingFlow 处理）。
    return True, ""


def _activate_kurou(game, player, target=None, cards=None):
    """苦肉：失去 1 点体力，然后摸两张牌。

    失去体力走 ``Game.lose_hp``（不是伤害）；若因此进入濒死，摸牌挂在濒死
    结算之后——死了就不摸，被救回来才摸，与"失去体力，然后摸牌"的顺序一致。
    """

    player.skill_state.add("kurou", "used", 1, ResetScope.TURN)
    game.add_log(player.name + " 发动【苦肉】：失去 1 点体力，摸两张牌")
    dying = game.lose_hp(player, 1, source=player)
    if dying is None:
        game.engine.context.apply(DrawCardsAtom(player, 2))
        return True

    def _after_dying(_result):
        if player.alive and player.hp > 0:
            game.engine.context.apply(DrawCardsAtom(player, 2))

    dying.on_complete = _after_dying
    return True


WU_EXTRA_SKILLS = (
    active(
        "zhiheng",
        "制衡",
        "出牌阶段限一次，你可以弃置任意数量的牌，然后摸等量的牌。",
        can_activate=_can_zhiheng,
        activate=_activate_zhiheng,
        spec=ZHI_HENG_SPEC,
        tags=("active",),
    ),
    triggered(
        "jiuyuan",
        "救援",
        "主公技，其他吴势力角色对你使用【桃】时，你额外回复 1 点体力。",
        factory=Jiuyuan,
        is_lord_skill=True,
    ),
    SkillDef(
        id="keji",
        name="克己",
        description="若你在出牌阶段没有使用或打出过【杀】，你可以跳过弃牌阶段。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DISCARD,
            prompt="【克己】：是否跳过弃牌阶段？",
            can_offer=_keji_can_offer,
            apply=_keji_apply,
        ),
    ),
    SkillDef(
        id="guose",
        name="国色",
        description="你可以将一张方块牌当【乐不思蜀】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="guose",
                matches=_is_diamond,
                name="LEBU",
                category="trick",
                contexts=(PLAY_CONTEXT,),
            ),
        ),
        tags=("conversion",),
    ),
    triggered(
        "liuli",
        "流离",
        "当你成为【杀】的目标时，你可以弃置一张牌，将此【杀】转移给你攻击范围内的一名其他角色。",
        factory=Liuli,
    ),
    SkillDef(
        id="qixi",
        name="奇袭",
        description="你可以将一张黑色牌当【过河拆桥】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="qixi",
                matches=_is_black_card,
                name="GUOHE",
                category="trick",
                contexts=(PLAY_CONTEXT,),
            ),
        ),
        tags=("conversion",),
    ),
    SkillDef(
        id="qianxun",
        name="谦逊",
        description="锁定技，你不能成为【顺手牵羊】或【乐不思蜀】的目标。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.TARGET_FORBIDDEN,
                value=_qianxun_forbidden,
                roles=("target",),
            ),
        ),
    ),
    triggered(
        "lianying",
        "连营",
        "当你失去最后一张手牌时，你可以摸一张牌。",
        factory=Lianying,
    ),
    active(
        "kurou",
        "苦肉",
        "出牌阶段，你可以失去 1 点体力，然后摸两张牌。",
        can_activate=_can_kurou,
        activate=_activate_kurou,
        spec=ActiveSkillSpec(),
        tags=("active",),
    ),
)
