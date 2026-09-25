"""第二批群势力武将：华佗 / 吕布 / 貂蝉。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESCUE_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
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
    if target is None:
        return False
    game.engine.context.apply(RecoverHpAtom(target, 1))
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
    if not player.hand:
        return False, "需要弃置一张手牌"
    if len(_lijian_males(game, player)) < 2:
        return False, "需要两名男性角色"
    return True, ""


def _lijian_males(game, player):
    return [
        other for other in game.get_alive_players()
        if other is not player and getattr(other, "gender", None) == "male"
    ]


def _activate_lijian(game, player, target=None, cards=None):
    """离间：弃一张牌，令一名男性角色对另一名男性角色发起决斗。

    决斗由通用的【决斗】效果结算（UseCardAction 走的是同一套引擎流程），
    这里不复制决斗的结算代码。
    """

    males = _lijian_males(game, player)
    if len(males) < 2:
        return False
    actor, defender = males[0], males[1]
    if target is not None and target in males:
        actor = target
        defender = next((item for item in males if item is not actor), males[1])

    from src.card import Card
    from src.game.engine import UseCardAction

    duel = Card(name="JUEDOU", category="trick", color=(0, 0, 0))
    duel._virtual = True
    player.skill_state.set("lijian", "used", 1, ResetScope.PHASE)
    game.add_log(
        player.name + " 发动【离间】：" + actor.name + " 与 " + defender.name + " 决斗")
    game.submit_action(UseCardAction(actor, duel, [defender]))
    return True


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
        self.owner.skill_state.set(self.id, "used", 1, ResetScope.TURN)
        context.apply(DrawCardsAtom(self.owner, 1))
        context.state.add_log(self.owner.name + " 发动【闭月】，摸一张牌")


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
        "出牌阶段，你可以弃置一张手牌，令一名已受伤的角色回复 1 点体力。",
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
        "出牌阶段限一次，你可以弃置一张手牌，令两名男性角色进行决斗。",
        can_activate=_can_lijian,
        activate=_activate_lijian,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_lijian_males,
            target_prompt="【离间】：请选择发起决斗的男性角色",
            cost_cards=1,
            cost_prompt="【离间】：请选择一张手牌弃置",
        ),
        tags=("active", "forced_use"),
    ),
    triggered(
        "biyue",
        "闭月",
        "结束阶段开始时，你可以摸一张牌。",
        factory=Biyue,
    ),
)
