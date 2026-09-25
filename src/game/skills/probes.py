"""Architecture probes for the Phase 7 skill foundation.

DEVELOPMENT ONLY.  These six skills exist to prove that the hook / modifier /
active-skill paths really work; they are **not** registered in the default
skill registry and therefore never appear in a normal game.  Tests bind them
explicitly.

  A probe_iron_body    受到伤害时伤害 -1            （before hook 修改数值）
  B probe_blood_draw   受到伤害后摸一张              （after hook 触发新 Atom）
  C probe_extra_draw   摸牌阶段摸牌数 +1             （持续 modifier）
  D probe_play_phase   出牌阶段开始时触发            （阶段 hook）
  E probe_nimble       到其他角色距离 -1             （距离 modifier）
  F probe_recycle      出牌阶段限一次：弃 1 摸 1      （主动技能）
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.engine import EventType
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from .definitions import ActiveSkillSpec, ModifierSpec, SkillDef, SkillKind, active, triggered
from .modifiers import ModifierKind
from .state import ResetScope

FLAG = "phase_starts"
USED = "used"


class IronBody(Skill):
    """A：伤害发生前把即将受到的伤害减 1。"""

    id = "probe_iron_body"
    name = "铁骨"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_MODIFY, priority=20),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        return (
            damage is not None
            and damage.target is self.owner
            and damage.amount > 0
        )

    def resolve(self, context, event):
        damage = event.payload["damage"]
        before = damage.amount
        damage.amount = max(0, damage.amount - 1)
        if damage.amount != before:
            damage.effects.append("【铁骨】伤害 -1")


class BloodDraw(Skill):
    """B：受到伤害后摸一张牌（验证 after hook 里嵌套新 Atom）。"""

    id = "probe_blood_draw"
    name = "血偿"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        return (
            damage is not None
            and damage.target is self.owner
            and event.payload.get("amount", 0) > 0
            and self.owner.alive
        )

    def resolve(self, context, event):
        context.apply(DrawCardsAtom(self.owner, 1))
        self.owner.skill_state.add(self.id, "drawn", 1, ResetScope.TURN)


class PlayPhaseSpark(Skill):
    """D：出牌阶段开始时记录一次，用来验证阶段 hook。"""

    id = "probe_play_phase"
    name = "锐意"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START),)

    def can_trigger(self, context, event):
        return (
            event.source is self.owner
            and event.payload.get("phase") is TurnPhase.PLAY
        )

    def resolve(self, context, event):
        self.owner.skill_state.add(self.id, FLAG, 1, ResetScope.TURN)


def _can_recycle(game, player):
    if game.game_over:
        return False, "对局已经结束"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not player.alive:
        return False, "已经阵亡"
    if not player.hand:
        return False, "没有手牌可以弃置"
    if player.skill_state.get("probe_recycle", USED, 0):
        return False, "本回合已经发动过"
    return True, ""


def _activate_recycle(game, player, target=None, cards=None):
    """F：费用牌（一张）由引擎支付，这里只负责摸一张与写标记。"""

    context = game.engine.context
    if not cards:
        return False
    player.skill_state.set("probe_recycle", USED, 1, ResetScope.TURN)
    context.apply(DrawCardsAtom(player, 1))
    game.add_log(player.name + " 发动【回收】")
    return True


PROBE_SKILLS = (
    triggered(
        "probe_iron_body",
        "铁骨",
        "受到伤害时伤害 -1。",
        factory=IronBody,
    ),
    triggered(
        "probe_blood_draw",
        "血偿",
        "受到伤害后摸一张牌。",
        factory=BloodDraw,
    ),
    SkillDef(
        id="probe_extra_draw",
        name="博闻",
        description="摸牌阶段摸牌数 +1。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DRAW_COUNT, value=1, roles=("player",)),
        ),
        tags=("probe",),
    ),
    triggered(
        "probe_play_phase",
        "锐意",
        "出牌阶段开始时触发。",
        factory=PlayPhaseSpark,
    ),
    SkillDef(
        id="probe_nimble",
        name="轻身",
        description="计算到其他角色的距离 -1。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DISTANCE_OUTGOING, value=-1, roles=("source",)),
        ),
        tags=("probe",),
    ),
    active(
        "probe_recycle",
        "回收",
        "出牌阶段限一次，弃置一张手牌并摸一张牌。",
        can_activate=_can_recycle,
        activate=_activate_recycle,
        spec=ActiveSkillSpec(
            cost_cards=1,
            cost_prompt="【回收】：请选择一张手牌弃置",
        ),
    ),
)
