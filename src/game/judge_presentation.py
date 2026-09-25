"""判定的展示语义：为什么判定、规则是什么、结果对判定角色意味着什么。

规则层在这里声明每一种判定的**来源**与**结果语义**，UI 只消费这些数据。
Renderer / UI 不允许按卡名或技能名硬编码文本与颜色——那是这套表存在的意义。

新增一种判定（新的延时锦囊 / 装备技能 / 武将技能）时，只需要：

    JUDGE_SOURCES["new_reason"] = JudgeSourceSpec(
        reason="new_reason",
        kind=JudgeSourceKind.SKILL,
        display_name="技能名",
        rule_text="一句话规则",
        skill_id="skill_id",
        outcome_of=_outcome_new_reason,
    )

UI 不需要任何改动。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple


class JudgeSourceKind(str, Enum):
    """判定是谁触发的。"""

    CARD = "card"            # 判定区里的延时锦囊实体牌
    EQUIPMENT = "equipment"  # 装备技能（八卦阵一类）
    SKILL = "skill"          # 武将技能（刚烈 / 洛神 / 铁骑一类）
    OTHER = "other"


class JudgeOutcomeTone(str, Enum):
    """结果对**被判定角色**的实际语义，不是牌的颜色。

    POSITIVE 这个判定对他的结果是有利的，NEGATIVE 是不利的，NEUTRAL 是
    中性或未生效。UI 只按 tone 上色，不去猜具体规则。
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class JudgeOutcome:
    tone: JudgeOutcomeTone
    title: str
    text: str


@dataclass(frozen=True)
class JudgeSourceSpec:
    """一种判定的静态声明（来源 + 规则文本 + 结果语义）。"""

    reason: str
    kind: JudgeSourceKind
    display_name: str
    rule_text: str
    card_name: str = ""      # kind == CARD：判定区里对应的实体牌 Card.name
    skill_id: str = ""       # kind == SKILL / EQUIPMENT：对应技能 id
    skill_name: str = ""     # 展示用技能名（技能表里没有时兜底）
    outcome_of: Optional[Callable] = None

    def outcome(self, result, subject=None) -> JudgeOutcome:
        if self.outcome_of is None:
            return JudgeOutcome(JudgeOutcomeTone.NEUTRAL, "判定完成", "")
        return self.outcome_of(result, subject)

    def uses_card(self):
        return self.kind is JudgeSourceKind.CARD


# ==================================================
# 结果语义（与规则层的判定条件保持一致）
#
# 每个 outcome 函数收到的是**真实判定牌（Card 对象）**，读它的
# ``suit`` / ``card_color`` / ``rank``——与规则层判定用的是同一份数据。
# ==================================================

def _rank_value(rank):
    text = str(rank)
    if text.isdigit():
        return int(text)
    return {"A": 1, "J": 11, "Q": 12, "K": 13}.get(text, 0)


def _missing(result):
    """没有判定牌（牌堆抽空）：任何判定都不生效。"""

    return JudgeOutcome(JudgeOutcomeTone.NEUTRAL, "判定未生效", "牌堆里没有可用的判定牌。")


def _outcome_lebu(result, _subject=None):
    if result is None:
        return _missing(result)
    if result.suit == "heart":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "正常进行出牌阶段", "红桃判定通过，本回合照常行动。")
    return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "跳过出牌阶段", "非红桃判定，本回合不能使用牌。")


def _outcome_bingliang(result, _subject=None):
    if result is None:
        return _missing(result)
    if result.suit == "club":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "正常进行摸牌阶段", "梅花判定通过，照常摸牌。")
    return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "跳过摸牌阶段", "非梅花判定，本回合不能摸牌。")


def _outcome_shandian(result, _subject=None):
    if result is None:
        return _missing(result)
    hit = result.suit == "spade" and 2 <= _rank_value(result.rank) <= 9
    if hit:
        return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "闪电命中", "黑桃 2～9 判定命中，受到 3 点雷电伤害。")
    return JudgeOutcome(
        JudgeOutcomeTone.POSITIVE, "闪电未命中",
        "判定未命中，闪电移到下家的判定区。")


def _outcome_bagua(card, _subject=None):
    if card is None:
        return _missing(card)
    if card.card_color == "red":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "八卦阵生效", "红色判定，视为打出一张【闪】。")
    return JudgeOutcome(
        JudgeOutcomeTone.NEUTRAL, "八卦阵未生效",
        "黑色判定，仍需自己打出【闪】。")


def _outcome_ganglie(result, _subject=None):
    # 刚烈是"受伤后的反击"：非红桃才反击成功，所以语义与花色直觉相反。
    if result is None:
        return _missing(result)
    if result.suit != "heart":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "刚烈反击成功",
                            "非红桃判定，伤害来源须弃置两张手牌或受到 1 点伤害。")
    return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "刚烈未反击", "红桃判定，本次反击没有生效。")


def _outcome_luoshen(card, _subject=None):
    if card is None:
        return _missing(card)
    if card.card_color == "black":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "洛神生效", "黑色判定，获得判定牌并可继续发动。")
    return JudgeOutcome(JudgeOutcomeTone.NEUTRAL, "洛神结束", "红色判定，本次洛神结束。")


def _outcome_tieji(card, _subject=None):
    if card is None:
        return _missing(card)
    if card.card_color == "red":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "铁骑命中", "红色判定，目标不能使用【闪】。")
    return JudgeOutcome(JudgeOutcomeTone.NEUTRAL, "铁骑未命中", "非红判定，目标可以正常响应。")


# ==================================================
# 注册表
# ==================================================

JUDGE_SOURCES = {
    "lebu": JudgeSourceSpec(
        reason="lebu", kind=JudgeSourceKind.CARD,
        display_name="乐不思蜀", card_name="LEBU",
        rule_text="判定不为红桃时，跳过该角色的出牌阶段。",
        outcome_of=_outcome_lebu,
    ),
    "bingliang": JudgeSourceSpec(
        reason="bingliang", kind=JudgeSourceKind.CARD,
        display_name="兵粮寸断", card_name="BINGLIANG",
        rule_text="判定不为梅花时，跳过该角色的摸牌阶段。",
        outcome_of=_outcome_bingliang,
    ),
    "shandian": JudgeSourceSpec(
        reason="shandian", kind=JudgeSourceKind.CARD,
        display_name="闪电", card_name="SHANDIAN",
        rule_text="判定为黑桃 2～9 时受到 3 点雷电伤害，否则闪电移到下家。",
        outcome_of=_outcome_shandian,
    ),
    "bagua": JudgeSourceSpec(
        reason="bagua", kind=JudgeSourceKind.EQUIPMENT,
        display_name="八卦阵", skill_id="bagua",
        rule_text="判定为红色时，视为打出一张【闪】。",
        outcome_of=_outcome_bagua,
    ),
    "ganglie": JudgeSourceSpec(
        reason="ganglie", kind=JudgeSourceKind.SKILL,
        display_name="刚烈", skill_id="ganglie",
        rule_text="受到伤害后判定，非红桃时伤害来源须弃两张手牌或受到 1 点伤害。",
        outcome_of=_outcome_ganglie,
    ),
    "luoshen": JudgeSourceSpec(
        reason="luoshen", kind=JudgeSourceKind.SKILL,
        display_name="洛神", skill_id="luoshen",
        rule_text="准备阶段判定：黑色则获得此牌，然后可以重复此流程。",
        outcome_of=_outcome_luoshen,
    ),
    "tieji": JudgeSourceSpec(
        reason="tieji", kind=JudgeSourceKind.SKILL,
        display_name="铁骑", skill_id="tieji",
        rule_text="使用【杀】时判定，红色则目标不能使用【闪】。",
        outcome_of=_outcome_tieji,
    ),
}

# 没有显式声明时的兜底：仍然展示判定牌，只是没有额外的规则文本。
FALLBACK_SPEC = JudgeSourceSpec(
    reason="judge", kind=JudgeSourceKind.OTHER,
    display_name="判定", rule_text="",
    outcome_of=lambda result, _subject=None: (
        _missing(result) if result is None else
        JudgeOutcome(JudgeOutcomeTone.NEUTRAL, "判定完成", "")
    ),
)


def judge_source(reason) -> JudgeSourceSpec:
    """按判定原因取声明；未知原因返回兜底声明（UI 不会崩）。"""

    spec = JUDGE_SOURCES.get(reason)
    if spec is not None:
        return spec
    if not reason:
        return FALLBACK_SPEC
    return JudgeSourceSpec(
        reason=str(reason), kind=FALLBACK_SPEC.kind,
        display_name=str(reason), rule_text="",
        outcome_of=FALLBACK_SPEC.outcome_of,
    )


def judge_tones():
    """全部 tone（便于 UI / 测试遍历）。"""

    return tuple(JudgeOutcomeTone)


def judge_reasons() -> Tuple[str, ...]:
    return tuple(sorted(JUDGE_SOURCES))
