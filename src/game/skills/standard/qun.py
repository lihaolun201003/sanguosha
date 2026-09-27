"""第二批群势力武将：华佗 / 吕布 / 貂蝉。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESCUE_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..mechanics import ask_targets, optional_trigger
from ..definitions import (
    ActiveSkillSpec,
    CostZone,
    ModifierSpec,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


# ==================================================
# 华佗 · 急救 / 青囊
# ==================================================


def _is_red_card(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "red"


def _out_of_turn(game, player):
    """回合外：当前行动者不是自己（或者现在根本不是自己的阶段）。"""

    return game.current_turn_player is not player


def _can_qingnang(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    # 官方是"出牌阶段**限一次**"：少了这条，同一阶段里可以无限次发动
    # （实测：第一次发动后按钮照样能按，体力被反复补满）。
    if player.skill_state.get("qingnang", "used", 0):
        return False, "本阶段已经发动过"
    if not player.hand:
        return False, "需要弃置一张手牌"
    if not _qingnang_targets(game, player):
        return False, "没有已受伤的角色"
    return True, ""


def _qingnang_targets(game, player):
    return [
        other for other in game.get_alive_players()
        if other.hp < other.max_hp
    ]


def _activate_qingnang(game, player, target=None, cards=None):
    """青囊：费用（一张手牌）已由引擎弃置，这里让目标回复 1 点体力。

    "限一次"的标记只在**结算成功之后**写：被拒绝的发动（没有目标、费用
    不合法）或玩家取消根本走不到这里，次数因此不会被白扣。
    """

    if target is None:
        return False
    if not getattr(target, "alive", True):
        return False
    game.engine.context.apply(RecoverHpAtom(target, 1))
    player.skill_state.set("qingnang", "used", 1, ResetScope.PHASE)
    game.add_log(player.name + " 发动【青囊】→ " + target.name + " 回复 1 点体力")
    return True


# ==================================================
# 吕布 · 无双
# ==================================================


def _wushuang_response(game, query):
    """无双：其他角色响应你的【杀】或【决斗】时需要多交一张牌。"""

    source = query.get("source")
    card = query.get("card")
    if card is None:
        return 0
    if getattr(card, "name", None) not in ("SHA", "JUEDOU"):
        return 0
    return 1


# ==================================================
# 貂蝉 · 离间 / 闭月
# ==================================================


def _can_lijian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("lijian", "used", 0):
        return False, "本阶段已经发动过"
    if not _lijian_cost_cards(game, player):
        return False, "需要弃置一张牌"
    if len(_lijian_males(game, player)) < 2:
        return False, "需要两名男性角色"
    return True, ""


def _lijian_cost_cards(game, player):
    """这次发动能支付的牌：手牌 + 装备区（官方是"弃一张牌"，不是"弃一张手牌"）。

    与引擎校验 / 界面高亮读的是**同一份** ``activation.cost_candidates``——
    候选由 ``LIJIAN_SPEC.allowed_zones`` 派生，界面不会多给、引擎也不会少收。
    """

    from src.game.skills.activation import cost_candidates

    return cost_candidates(game, player, LIJIAN_SPEC)


def _lijian_males(game, player, *, exclude=None):
    """离间的目标候选：其他**存活男性**角色（``exclude`` 用于排除发起者）。"""

    return [
        other for other in game.get_alive_players()
        if other is not player and other is not exclude
        and getattr(other, "gender", None) == "male"
    ]


#: 【离间】的输入契约：第一步只定"谁使用这张【决斗】"，第二步（流程里）再定
#: 打向谁。费用是官方文本里的"弃**一张牌**"——手牌与装备区的牌都能支付，
#: 这条声明是候选 / 界面高亮 / 引擎校验的唯一来源（见 activation.cost_candidates）。
LIJIAN_SPEC = ActiveSkillSpec(
    needs_target=True,
    target_candidates=_lijian_males,
    target_prompt="【离间】：请选择发起【决斗】的男性角色",
    cost_cards=1,
    cost_prompt="【离间】：请选择一张牌弃置",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)


def _is_lijian_male(player, target):
    """服务端复核：这名角色现在能不能作为离间的男性角色。"""

    if target is None or target is player:
        return False
    if not getattr(target, "alive", True):
        return False
    return getattr(target, "gender", None) == "male"


def _activate_lijian(game, player, target=None, cards=None):
    """离间：费用已由引擎支付；令玩家选中的男性角色对另一名男性角色使用【决斗】。

    方向：``target``（第一步选出的角色）是**使用这张【决斗】的人**，它的
    目标由流程的第二步单独询问——以前这里直接取"名单里的第一位男性"，
    另一半输入是代码替玩家决定的。
    """

    actor = target
    if not _is_lijian_male(player, actor):
        return False
    # 费用已经付掉了：次数在这里就该记上，第二步只是"打向谁"。
    player.skill_state.set("lijian", "used", 1, ResetScope.PHASE)
    LijianFlow(game.engine, player, actor).start()
    return True


class LijianFlow(Flow):
    """离间第二步：这张【决斗】打向**哪一名**男性角色，由玩家自己选。

    候选仍然由规则层给出（存活、男性、与发起者不同），提交的目标还会被引擎
    按候选名单复核一次；这里再做一遍同样的复核，避免"名单过期"时把非法目标
    当成合法（远程真人、脚本提交的答案都不被信任）。
    """

    def __init__(self, engine, owner, actor):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.actor = actor
        self.stage = "target"

    def begin(self):
        candidates = _lijian_males(self.game, self.owner, exclude=self.actor)
        if not candidates:
            self.game.message = "【离间】：没有另一名男性角色可以作为决斗目标。"
            return self.complete({"applied": False})
        ask_targets(
            self.engine, self, source=self.owner, target=self.owner,
            prompt="【离间】：请选择 %s 这张【决斗】的目标" % self.actor.name,
            reason="lijian", candidates=candidates,
            min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        targets = list(getattr(response, "targets", ()) or ())
        defender = targets[0] if targets else None
        # 两名角色必须互不相同、都是男性、都存活。
        if not _is_lijian_male(self.owner, defender) or defender is self.actor:
            self.game.message = "【离间】：没有选择决斗目标，费用已支付但决斗未发生。"
            self.game.add_log("%s 的【离间】没有选择决斗目标" % self.owner.name)
            return self.complete({"applied": False})
        self._duel(defender)
        return self.complete({"applied": True})

    def _duel(self, defender):
        """这张【决斗】交给引擎的通用结算（使用 / 响应 / 伤害都不在这里复制）。"""

        from src.card import Card
        from src.game.engine import UseCardAction

        duel = Card(name="JUEDOU", category="trick", color=(0, 0, 0))
        duel._virtual = True
        # 先写战报再提交：这条"发动"记录要排在【决斗】自己的结算记录之前。
        self.game.add_log("%s 发动【离间】：%s 对 %s 决斗"
                          % (self.owner.name, self.actor.name, defender.name))
        self.game.submit_action(UseCardAction(self.actor, duel, [defender]))


class Biyue(Skill):
    """结束阶段开始时摸一张牌。"""

    id = "biyue"
    name = "闭月"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") != TurnPhase.FINISH:
            return False
        return not self.owner.skill_state.get(self.id, "used", 0)

    def resolve(self, context, event):
        optional_trigger(
            context, self.owner,
            prompt="【闭月】：是否摸一张牌？", reason="biyue", label="闭月",
            effect=self._draw).start()

    def _draw(self, flow):
        self.owner.skill_state.set(self.id, "used", 1, ResetScope.TURN)
        flow.context.apply(DrawCardsAtom(self.owner, 1))
        flow.game.add_log(self.owner.name + " 发动【闭月】，摸一张牌")
        return True


QUN_SKILLS = (
    SkillDef(
        id="jijiu",
        name="急救",
        description="你的回合外，你可以将一张红色牌当【桃】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="jijiu",
                matches=_is_red_card,
                name="TAO",
                # 濒死救援（rescue）是【急救】最核心的场合：漏掉它会让技能
                # 在最需要的时候完全不可用。
                contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT, RESCUE_CONTEXT),
                available=_out_of_turn,
            ),
        ),
        tags=("conversion",),
    ),
    active(
        "qingnang",
        "青囊",
        "出牌阶段限一次，你可以弃置一张手牌，令一名已受伤的角色回复 1 点体力。",
        can_activate=_can_qingnang,
        activate=_activate_qingnang,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_qingnang_targets,
            target_prompt="【青囊】：请选择一名已受伤的角色",
            cost_cards=1,
            cost_prompt="【青囊】：请选择一张手牌弃置",
        ),
        tags=("active",),
    ),
    SkillDef(
        id="wushuang",
        name="无双",
        description="锁定技，当你使用【杀】或【决斗】时，目标需要连续使用两张【闪】或两张【杀】响应。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.RESPONSE_COUNT,
                value=_wushuang_response,
                roles=("source",),
            ),
        ),
    ),
    active(
        "lijian",
        "离间",
        "出牌阶段限一次，你可以弃一张牌并选择两名男性角色，"
        "令其中一名男性角色视为对另一名男性角色使用一张【决斗】。",
        can_activate=_can_lijian,
        activate=_activate_lijian,
        spec=LIJIAN_SPEC,
        tags=("active", "forced_use"),
    ),
    triggered(
        "biyue",
        "闭月",
        "结束阶段开始时，你可以摸一张牌。",
        factory=Biyue,
    ),
)
