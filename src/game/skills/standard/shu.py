"""蜀势力武将技能：张飞 / 黄月英 / 关羽 / 赵云。"""

from src.card import mark_card_flag
from src.game.atoms_v2 import DrawCardsAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType, FlowStatus
from src.game.engine.skills import Skill, SkillBinding
from src.game.flows.judge import JudgeFlow

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
# 张飞 · 咆哮
# ==================================================


class Paoxiao(Skill):
    """锁定技，出牌阶段使用【杀】无次数限制（纯 modifier，无事件监听）。"""

    id = "paoxiao"
    name = "咆哮"

    def bindings(self):
        return ()

    def resolve(self, context, event):  # pragma: no cover - 锁定技不响应事件
        return


# ==================================================
# 黄月英 · 集智 / 奇才
# ==================================================


class Jizhi(Skill):
    """使用锦囊牌时摸一张牌。"""

    id = "jizhi"
    name = "集智"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USED),)

    def can_trigger(self, context, event):
        if event.source is not self.owner:
            return False
        card = event.payload.get("card")
        return card is not None and getattr(card, "category", None) == "trick"

    def resolve(self, context, event):
        context.apply(DrawCardsAtom(self.owner, 1))
        self.owner.skill_state.add(self.id, "drawn", 1, ResetScope.TURN)


# ==================================================
# 关羽 · 武圣
# ==================================================


def _is_red(card):
    """红色实体牌（虚拟牌不能作为转换源，避免递归）。"""

    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "red"


# ==================================================
# 赵云 · 龙胆
# ==================================================


def _is_sha(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "name", None) == "SHA"


def _is_shan(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "name", None) == "SHAN"


SHU_SKILLS = (
    SkillDef(
        id="paoxiao",
        name="咆哮",
        description="锁定技，出牌阶段你使用【杀】无次数限制。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.SLASH_QUOTA, value=99, roles=("player",)),
        ),
    ),
    SkillDef(
        id="jizhi",
        name="集智",
        description="当你使用一张锦囊牌时，你可以摸一张牌。",
        kind=SkillKind.PASSIVE,
        factory=Jizhi,
    ),
    SkillDef(
        id="qicai",
        name="奇才",
        description="锁定技，你使用锦囊牌无距离限制。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TRICK_RANGE_IGNORE, value=1, roles=("player",)),
        ),
    ),
    SkillDef(
        id="wusheng",
        name="武圣",
        description="你可以将一张红色牌当【杀】使用或打出。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="wusheng",
                matches=_is_red,
                name="SHA",
                contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
            ),
        ),
        tags=("conversion",),
    ),
    SkillDef(
        id="longdan",
        name="龙胆",
        description="你可以将【杀】当【闪】、【闪】当【杀】使用或打出。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="longdan",
                matches=_is_sha,
                name="SHAN",
                contexts=(RESPONSE_CONTEXT,),
            ),
            CardConversion(
                skill_id="longdan",
                matches=_is_shan,
                name="SHA",
                contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
            ),
        ),
        tags=("conversion",),
    ),
)


# ==================================================
# 刘备 · 仁德 / 激将
# ==================================================


def lord_skills_enabled(game, player):
    """主公技的唯一启用条件：身份模式下该角色是主公。

    FFA 没有主公，因此即使选了刘备 / 孙权也不会获得主公技。
    """

    mode = getattr(game, "mode", None)
    if mode is None or not getattr(mode, "uses_identities", False):
        return False
    from src.game.identity import Identity

    return getattr(player, "identity", None) is Identity.LORD


def _can_rende(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not player.hand:
        return False, "没有可以交给他人的手牌"
    if not _rende_targets(game, player):
        return False, "没有其他存活角色"
    return True, ""


def _rende_targets(game, player):
    return [
        other for other in game.get_alive_players()
        if other is not player and getattr(other, "alive", True)
    ]


def _activate_rende(game, player, target=None, cards=None):
    """牌已经由引擎转交给目标（transfer_cards），这里只记录。"""

    if target is None or not cards:
        return False
    game.add_log(
        player.name + " 发动【仁德】→ " + target.name
        + "（交给 " + str(len(cards)) + " 张牌）")
    return True


def _can_jijiang(game, player):
    if not lord_skills_enabled(game, player):
        return False, "只有主公可以发动"
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.sha_used and not game.can_use_unlimited_sha(player):
        return False, "本回合已经使用过【杀】"
    if not _jijiang_helpers(game, player):
        return False, "没有能替你出【杀】的蜀势力角色"
    return True, ""


def _jijiang_helpers(game, player):
    """其他蜀势力、手牌里有【杀】的存活角色（按座次）。"""

    return [
        other for other in game.seats.alive_players_in_order(
            start_after=player, include_start=False)
        if getattr(other, "kingdom", None) == "shu" and any(
            getattr(card, "name", None) == "SHA" for card in other.hand)
    ]


def _activate_jijiang(game, player, target=None, cards=None):
    """激将：请一位蜀势力同伴替你打出【杀】。

    实现上借用这位同伴手牌里的一张【杀】，作为刘备使用的一张【杀】结算；
    走的是通用的"他人代为响应"路径，没有为刘备写专用的结算代码。
    """

    helpers = _jijiang_helpers(game, player)
    if not helpers:
        return False
    helper = target if target in helpers else helpers[0]
    sha = next(
        (card for card in helper.hand if getattr(card, "name", None) == "SHA"), None)
    if sha is None:
        return False

    from src.game.atoms_v2 import MoveCardAtom
    from src.game.engine import UseCardAction

    # 同伴把这张【杀】交给刘备，再由刘备正常使用它。
    context = game.engine.context
    context.apply(MoveCardAtom(sha, source=helper.hand, destination=player.hand))
    game.add_log(player.name + " 发动【激将】，" + helper.name + " 替他打出【杀】")
    player.skill_state.add("jijiang", "used", 1, ResetScope.TURN)
    game.submit_action(UseCardAction(player, sha, list(_jijiang_targets(game, player)) or None))
    return True


def _jijiang_targets(game, player):
    """激将用出去的这张【杀】的合法目标（由规则层给出）。"""

    from src.game.rules import DistanceRule

    return [
        other for other in game.get_alive_players()
        if other is not player and DistanceRule.in_attack_range(game, player, other)
    ]


# ==================================================
# 诸葛亮 · 观星 / 空城
# ==================================================


def _empty_hand(game, query):
    """空城：没有手牌时不能成为【杀】或【决斗】的目标。"""

    target = query.get("target")
    card = query.get("card")
    if target is None or card is None:
        return 0
    if getattr(target, "hand", None):
        return 0
    return 1 if getattr(card, "name", None) in ("SHA", "JUEDOU") else 0


def _can_guanxing(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "prepare":
        return False, "只能在你的准备阶段发动"
    if player.skill_state.get("guanxing", "used", 0):
        return False, "本回合已经发动过"
    if len(game.deck.draw_pile) < 1:
        return False, "牌堆没有牌"
    return True, ""


def _activate_guanxing(game, player, target=None, cards=None):
    """观星：查看牌堆顶 N 张，并按玩家选择的顺序放回牌堆顶。

    牌**从未离开牌堆**：这里只记录查看的牌并打开排序通道，真正的顺序由
    玩家点选的先后决定（先点的更靠上）。放弃排序即保持原序，不丢牌。
    """

    alive = len([p for p in game.get_alive_players() if getattr(p, "alive", True)])
    count = max(1, min(5, alive))
    pile = game.deck.draw_pile
    if not pile:
        return False
    count = min(count, len(pile))
    viewed = list(pile[-count:])
    player.skill_state.set("guanxing", "used", 1, ResetScope.TURN)
    names = "、".join(card.display_name for card in viewed)
    game.add_log(player.name + " 发动【观星】，查看牌堆顶 " + str(count) + " 张：" + names)
    game.start_card_selection(
        zone="public_pool",
        candidates=[(card, None) for card in viewed],
        number=count,
        prompt="【观星】：请按放回牌堆顶的顺序依次选择（先选的在上）",
        on_complete=lambda ordered: _apply_guanxing(game, viewed, ordered),
        owner=player,
        cancellable=True,
    )
    return True


def _apply_guanxing(game, viewed, ordered):
    """把查看过的牌按玩家选择顺序放回牌堆顶。

    ``draw_pile[-1]`` 是下一个被摸到的牌，所以"第一个选的"要落在尾部：
    ``pile[-n:] = reversed(ordered)``。``ordered`` 为空（玩家放弃排序）
    表示保持原序，不做任何移动。
    """

    chosen = [card for card, _rect, _key in (ordered or ())]
    pile = game.deck.draw_pile
    count = len(viewed)
    if not chosen or count == 0 or len(pile) < count:
        game.message = "【观星】：保持原顺序。"
        game.add_log(game.current_turn_player.name + " 的【观星】保持原顺序")
        return
    if len(chosen) != count:
        return
    # 牌堆在这一轮里被改动过（理论上不会发生）：保持原样，不冒风险重排。
    if any(not any(card is item for item in pile) for card in viewed):
        return
    pile[-count:] = list(reversed(chosen))
    game.message = "【观星】：已按选择的顺序放回牌堆顶。"
    game.add_log(game.current_turn_player.name + " 的【观星】调整了牌堆顶顺序")


# ==================================================
# 马超 · 马术 / 铁骑
# ==================================================


class Tieji(Skill):
    """使用【杀】指定目标后判定：红色则该目标不能使用【闪】。"""

    id = "tieji"
    name = "铁骑"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USED),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        return bool(
            card is not None
            and getattr(card, "name", None) == "SHA"
            and event.payload.get("targets")
        )

    def resolve(self, context, event):
        engine = context.services["engine"]
        card = event.payload["card"]
        judge = JudgeFlow(engine, self.owner, "tieji")
        outcome = judge.start()
        if outcome.status is FlowStatus.WAITING:
            judge.on_complete = lambda result: self._after_judge(card, result)
            return
        self._after_judge(card, outcome.value)

    def _after_judge(self, card, result):
        if result is None:
            return
        # 判定结果的颜色读 JudgeResult 的真实字段（card_color 是实体牌的字段名，
        # 判定结果上叫 color）。
        if getattr(result, "color", None) != "red":
            return
        # 此【杀】不可被响应：标记写在实体牌上，由杀的结算统一读取
        # （Game.cannot_respond_to），这里不复制响应流程。
        mark_card_flag(card, "_cannot_respond", True)
        self.owner.skill_state.add(self.id, "hit", 1, ResetScope.TURN)


SHU_EXTRA_SKILLS = (
    active(
        "rende",
        "仁德",
        "出牌阶段，你可以将任意数量的手牌交给一名其他角色。",
        can_activate=_can_rende,
        activate=_activate_rende,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_rende_targets,
            target_prompt="【仁德】：请选择一名其他角色",
            variable_cost=True,
            transfer_cards=True,
            cost_prompt="【仁德】：请选择要交给他人的手牌",
        ),
        tags=("active", "card_transfer"),
    ),
    active(
        "jijiang",
        "激将",
        "主公技，出牌阶段你可以请其他蜀势力角色替你打出【杀】。",
        can_activate=_can_jijiang,
        activate=_activate_jijiang,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_jijiang_helpers,
            target_prompt="【激将】：请选择替你出【杀】的蜀势力角色",
        ),
        tags=("active", "lord"),
        is_lord_skill=True,
    ),
    active(
        "guanxing",
        "观星",
        "准备阶段开始时，你可以查看牌堆顶的若干张牌（数量为存活角色数，至多五张），"
        "并将它们以任意顺序放回牌堆顶。",
        can_activate=_can_guanxing,
        activate=_activate_guanxing,
        spec=ActiveSkillSpec(),
        tags=("active",),
        needs_local_ui=True,       # 排序仍走本地选牌通道，见 SkillDef.needs_local_ui
    ),
    SkillDef(
        id="kongcheng",
        name="空城",
        description="锁定技，若你没有手牌，你不能成为【杀】或【决斗】的目标。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.TARGET_FORBIDDEN,
                value=_empty_hand,
                roles=("target",),
            ),
        ),
    ),
    SkillDef(
        id="mashu",
        name="马术",
        description="锁定技，你计算与其他角色的距离时减一。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.DISTANCE_OUTGOING, value=-1, roles=("source",)),
        ),
    ),
    triggered(
        "tieji",
        "铁骑",
        "当你使用【杀】指定目标后，你可以进行判定：若结果为红色，该角色不能使用【闪】。",
        factory=Tieji,
    ),
)

SHU_SKILLS = SHU_SKILLS + SHU_EXTRA_SKILLS
