"""吴势力武将技能：周瑜 / 孙尚香。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom
from src.game.engine import EventType, Flow, FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.engine.skills import Skill, SkillBinding
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.rules import TurnPhase

from ..definitions import ActiveSkillSpec, ModifierSpec, SkillDef, SkillKind, active, triggered
from ..modifiers import ModifierKind
from ..state import ResetScope

SUITS = ("spade", "heart", "club", "diamond")
SUIT_NAMES = {"spade": "黑桃", "heart": "红桃", "club": "梅花", "diamond": "方块"}


# ==================================================
# 周瑜 · 英姿 / 反间
# ==================================================


def _can_fanjian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("fanjian", "used", 0):
        return False, "本阶段已经发动过"
    if not player.hand:
        return False, "没有可以展示的手牌"
    if not any(other is not player and other.alive and other.hp > 0 for other in game.get_alive_players()):
        return False, "没有合法目标"
    return True, ""


def _activate_fanjian(game, player, target=None, cards=None):
    """参数由引擎校验齐备后才调用；这里不再自行挑选目标。"""

    from .fanjian import FanjianFlow

    if target is None:
        return False
    player.skill_state.set("fanjian", "used", 1, ResetScope.PHASE)
    game.add_log(player.name + " 发动【反间】→ " + target.name)
    FanjianFlow(game.engine, player, target).start()
    return True


# ==================================================
# 孙尚香 · 枭姬 / 结姻
# ==================================================


class Xiaoji(Skill):
    """失去装备区里的一张牌后摸两张牌。"""

    id = "xiaoji"
    name = "枭姬"

    def bindings(self):
        return (SkillBinding(EventType.EQUIPMENT_LOST),)

    def can_trigger(self, context, event):
        if event.target is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        return card is not None

    def resolve(self, context, event):
        context.apply(DrawCardsAtom(self.owner, 2))
        self.owner.skill_state.add(self.id, "drawn", 2, ResetScope.TURN)
        context.state.add_log(self.owner.name + " 发动【枭姬】，摸两张牌")


def _can_jieyin(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("jieyin", "used", 0):
        return False, "本阶段已经发动过"
    if len(player.hand) < 2:
        return False, "需要至少两张手牌"
    if player.hp >= player.max_hp:
        return False, "你的体力已满"
    if not _jieyin_targets(game, player):
        return False, "没有已受伤的男性角色"
    return True, ""


def _jieyin_targets(game, player):
    return [
        other for other in game.get_alive_players()
        if other is not player and other.gender == "male" and other.hp < other.max_hp
    ]


def _activate_jieyin(game, player, target=None, cards=None):
    """费用牌已由引擎支付，这里只负责回复体力与写入 used 标记。"""

    if target is None:
        return False

    context = game.engine.context
    context.apply(RecoverHpAtom(player, 1))
    context.apply(RecoverHpAtom(target, 1))
    player.skill_state.set("jieyin", "used", 1, ResetScope.PHASE)
    game.add_log(player.name + " 发动【结姻】→ " + target.name)
    return True


WU_SKILLS = (
    SkillDef(
        id="yingzi",
        name="英姿",
        description="锁定技，摸牌阶段你额外摸一张牌。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DRAW_COUNT, value=1, roles=("player",)),
        ),
    ),
    active(
        "fanjian",
        "反间",
        "出牌阶段限一次，令一名其他角色选择一种花色，然后你展示一张手牌并交给该角色；"
        "若该角色没有弃置一张与此牌花色相同的手牌，则受到 1 点伤害。",
        can_activate=_can_fanjian,
        activate=_activate_fanjian,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_prompt="【反间】：请选择一名其他角色",
        ),
        tags=("active",),
    ),
    triggered(
        "xiaoji",
        "枭姬",
        "当你失去装备区里的一张牌后，你可以摸两张牌。",
        factory=Xiaoji,
    ),
    active(
        "jieyin",
        "结姻",
        "出牌阶段限一次，你可以弃置两张手牌并选择一名已受伤的男性角色，你与其各回复 1 点体力。",
        can_activate=_can_jieyin,
        activate=_activate_jieyin,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_jieyin_targets,
            target_prompt="【结姻】：请选择一名已受伤的男性角色",
            cost_cards=2,
            cost_prompt="【结姻】：请选择两张手牌弃置",
        ),
        tags=("active",),
    ),
)
