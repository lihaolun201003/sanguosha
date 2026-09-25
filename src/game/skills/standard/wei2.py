"""第二批魏势力武将：甄姬 / 许褚。

与第一批一样，这里只有"技能数据 + 少量钩子"；规则判定仍然全部交给引擎。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType
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
# 甄姬 · 倾国 / 洛神
# ==================================================


def _is_black(card):
    """黑色实体牌（虚拟牌不能作为转换源，避免递归）。"""

    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "black"


def _game_of(context):
    """从 GameContext 里取 game（技能钩子需要查牌堆 / 座次时用）。"""

    engine = getattr(context, "services", {}).get("engine")
    return getattr(engine, "game", None)


class Luoshen(Skill):
    """准备阶段开始时判定：黑色则获得该判定牌。

    第一版只判定一次（获得黑牌），不循环——循环判定需要另一个通用流程，
    留待后续；技能本体与【鬼才】【天妒】的交互走的就是标准判定流程。
    """

    id = "luoshen"
    name = "洛神"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") != TurnPhase.PREPARE:
            return False
        if self.owner.skill_state.get(self.id, "used", 0):
            return False
        game = _game_of(context)
        if game is None:
            return False
        return len(game.deck.draw_pile) > 0

    def resolve(self, context, event):
        from src.game.engine import FlowStatus
        from src.game.flows.judge import JudgeFlow

        engine = context.services["engine"]
        game = engine.game
        self.owner.skill_state.set(self.id, "used", 1, ResetScope.TURN)
        judge = JudgeFlow(engine, self.owner, "luoshen")
        outcome = judge.start()
        if outcome.status is FlowStatus.WAITING:
            judge.on_complete = lambda result: self._after_judge(game, result)
            return
        self._after_judge(game, outcome.value)

    def _after_judge(self, game, result):
        card = getattr(result, "card", None)
        if card is None or not self.owner.alive:
            return
        if getattr(card, "card_color", None) != "black":
            game.add_log(self.owner.name + " 发动【洛神】：判定为红色，结束")
            return
        # 判定牌已在弃牌堆：收回手牌。
        pile = game.deck.discard_pile
        if any(item is card for item in pile):
            game.engine.context.apply(
                MoveCardAtom(card, source=pile, destination=self.owner.hand))
        game.add_log(self.owner.name + " 发动【洛神】，获得判定牌 " + card.display_name)


# ==================================================
# 许褚 · 裸衣
# ==================================================


def _luoyi_modifier(game, query):
    """裸衣：本回合自己造成的【杀】/【决斗】伤害 +1。"""

    source = query.get("source")
    card = query.get("card")
    if source is None or getattr(source, "skill_state", None) is None:
        return 0
    if not source.skill_state.get("luoyi", "active", 0):
        return 0
    if card is None:
        return 0
    return 1 if getattr(card, "name", None) in ("SHA", "JUEDOU") else 0


def _luoyi_draw_modifier(game, query):
    """裸衣：发动后摸牌阶段少摸一张。"""

    player = query.get("player")
    if player is None or getattr(player, "skill_state", None) is None:
        return 0
    return -1 if player.skill_state.get("luoyi", "active", 0) else 0


def _luoyi_can_offer(game, player):
    if game.game_over or not player.alive:
        return False
    return not player.skill_state.get("luoyi", "offered", 0)


def _luoyi_apply(game, player):
    """询问是否发动裸衣。

    返回 False：**不替代**摸牌阶段本身，只把"少摸一张、伤害 +1"两个
    modifier 打开（少摸由 DRAW_COUNT 生效）。
    """

    player.skill_state.set("luoyi", "offered", 1, ResetScope.TURN)
    player.skill_state.set("luoyi", "active", 1, ResetScope.TURN)
    game.add_log(player.name + " 发动【裸衣】：本回合少摸一张牌，伤害 +1")
    return False


WEI_EXTRA_SKILLS = (
    SkillDef(
        id="qingguo",
        name="倾国",
        description="你可以将一张黑色手牌当【闪】使用或打出。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="qingguo",
                matches=_is_black,
                name="SHAN",
                contexts=(RESPONSE_CONTEXT,),
            ),
        ),
        tags=("conversion",),
    ),
    triggered(
        "luoshen",
        "洛神",
        "准备阶段开始时，你可以进行判定：若结果为黑色，你获得此判定牌。",
        factory=Luoshen,
    ),
    SkillDef(
        id="luoyi_boost",
        name="裸衣",
        description="若你发动过【裸衣】，本回合你使用【杀】或【决斗】造成的伤害 +1。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.DAMAGE_DEALT,
                value=_luoyi_modifier,
                roles=("source",),
            ),
            ModifierSpec(
                kind=ModifierKind.DRAW_COUNT,
                value=_luoyi_draw_modifier,
                roles=("player",),
            ),
        ),
    ),
    SkillDef(
        id="luoyi",
        name="裸衣",
        description="摸牌阶段，你可以少摸一张牌：若如此做，本回合你使用【杀】或【决斗】造成的伤害 +1。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【裸衣】：是否少摸一张牌，令本回合伤害 +1？",
            can_offer=_luoyi_can_offer,
            apply=_luoyi_apply,
        ),
    ),
)
