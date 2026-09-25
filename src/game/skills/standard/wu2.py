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

from ..definitions import (
    ActiveSkillSpec,
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


def _can_zhiheng(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("zhiheng", "used", 0):
        return False, "本阶段已经发动过"
    if not player.hand:
        return False, "没有可以弃置的手牌"
    return True, ""


def _activate_zhiheng(game, player, target=None, cards=None):
    """费用牌已由引擎弃置；按弃置数量摸牌。"""

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


class Liuli(Skill):
    """成为【杀】的目标时，弃一张牌把这张【杀】转移给攻击范围内的另一名角色。

    第一版自动选择最合适的目标（按座次取第一个合法角色）并弃一张手牌；
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
        if not self.owner.hand:
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
        flow.redirect_resolver = lambda target_flow, resolution: self._apply(
            game, target_flow, resolution)
        # 交由 UseCardFlow 挂起并进入"目标重定向"阶段（通用扩展点）。
        flow.target_redirect = request

    def _apply(self, game, flow, resolution):
        """窗口结论落地：放弃则原样继续；选了目标才支付代价并改目标。"""

        chosen = list(getattr(resolution, "targets", None) or ())
        if not chosen:
            game.add_log(self.owner.name + " 放弃发动【流离】")
            return True

        new_target = chosen[0]
        cost = next(iter(self.owner.hand), None)
        if cost is None or not self.owner.alive:
            return True
        if not any(item is new_target for item in _liuli_targets(
                game, flow.actor, self.owner)):
            return True

        game.engine.context.apply(MoveCardAtom(
            cost, source=self.owner.hand, destination=game.deck.discard_pile))

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
        self.owner.skill_state.set(self.id, "armed", 1, ResetScope.PHASE)
        context.apply(DrawCardsAtom(self.owner, 1))
        context.state.add_log(self.owner.name + " 发动【连营】，摸一张牌")


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
        spec=ActiveSkillSpec(
            variable_cost=True,
            cost_prompt="【制衡】：请选择要弃置的牌",
        ),
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
