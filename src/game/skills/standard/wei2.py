"""第二批魏势力武将：甄姬 / 许褚。

与第一批一样，这里只有"技能数据 + 少量钩子"；规则判定仍然全部交给引擎。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..mechanics import ask_confirm, judge
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


class LuoshenFlow(Flow):
    """洛神：可以判定；黑色获得此牌，然后**可以重复此流程**，直到出现红色
    或玩家主动停止。

    官方文本（三国杀 OL / 移动版标准甄姬）：

        准备阶段，你可以进行判定，若结果为黑色，你获得此牌，
        然后你可以重复此流程。

    # 三个不能省的约束

    * **每一次判定都走通用 ``JudgeFlow``**：改判（鬼才 / 鬼道）参与每一次，
      判定牌的去向由 ``card_recipient`` 声明，判定优先级由 ``JudgeGate``
      在每次判定期间各自维持。这里没有任何"自己写一遍判定"的简化。
    * **重复是玩家的选择**：每一次黑色之后都要**重新问一次**，系统绝不
      自动判定到红色为止。早前那版只判定一次，等于替玩家放弃了后续收益。
    * **判定牌直取**：黑色牌在判定收尾时直接进手牌，而不是"先让它进弃牌堆、
      事后从弃牌堆里捞回来"——后者会凭空产生一次弃牌事件，任何监听弃牌的
      技能（固政一类）都会看到一个规则上从未发生过的弃牌。
    """

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.gained = []
        self.rounds = 0
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【洛神】：是否进行判定？", reason="luoshen")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            if response is None or not response.confirmed:
                # 第一次就拒绝：不写 used、不判定、什么都不动。
                return self.complete({"applied": False, "gained": 0})
            self.owner.skill_state.set("luoshen", "used", 1, ResetScope.TURN)
            return self._begin_judge()
        # stage == "again"：上一次判定是黑色，问的是"要不要继续"。
        # 停止权在玩家手里，答"不"就直接收尾。
        if response is None or not response.confirmed:
            return self._finish()
        return self._begin_judge()

    def _begin_judge(self):
        if not list(getattr(self.game.deck, "draw_pile", ()) or ()):
            # 牌堆抽空：流程正常结束，绝不能让判定停在一个永远拿不到牌的循环里。
            self.game.add_log("%s 的【洛神】结束：牌堆已空" % self.owner.name)
            return self._finish()
        self.rounds += 1
        self.stage = "judge"
        flow, result = judge(self.engine, self.owner, "luoshen",
                             card_recipient=self._recipient)
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def _recipient(self, result):
        """只有黑色判定牌归自己；红色照常进弃牌堆。"""

        card = getattr(result, "card", None)
        if card is not None and getattr(card, "card_color", None) == "black":
            return self.owner
        return None

    def _after_judge(self, result):
        card = getattr(result, "card", None)
        black = (card is not None
                 and getattr(card, "card_color", None) == "black")
        if not black:
            if card is not None:
                self.game.add_log(
                    self.owner.name + " 发动【洛神】：判定为红色，结束")
            return self._finish()
        self.gained.append(card)
        self.game.add_log("%s 发动【洛神】，获得判定牌 %s"
                          % (self.owner.name, card.display_name))
        if not list(getattr(self.game.deck, "draw_pile", ()) or ()):
            return self._finish()
        # "然后你可以重复此流程"：把继续与否交回玩家，不自动接着判。
        self.stage = "again"
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【洛神】：判定为黑色，已获得 %d 张。是否继续判定？"
                           % len(self.gained),
                    reason="luoshen_again")
        return self.current_result()

    def _finish(self):
        if self.gained:
            self.game.add_log("%s 的【洛神】结束，共获得 %d 张判定牌"
                              % (self.owner.name, len(self.gained)))
        return self.complete({"applied": bool(self.gained),
                              "gained": len(self.gained)})


class Luoshen(Skill):
    """准备阶段开始时可以判定：黑色则获得该判定牌，且可以重复此流程。

    规则文本以**官方**为准（三国杀 OL / 移动版标准甄姬）。早前把这里当成
    "只判定一次"的经典版，是实现方的判断错误，不是规则版本差异。
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
        LuoshenFlow(context.services["engine"], self.owner).start()


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
        "准备阶段，你可以进行判定：若结果为黑色，你获得此牌，然后你可以重复此流程。",
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
