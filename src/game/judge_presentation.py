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
        return JudgeOutcome(
            JudgeOutcomeTone.POSITIVE, "判定失败 · 乐不思蜀无效",
            "红桃判定通过，正常进入出牌阶段。")
    return JudgeOutcome(
        JudgeOutcomeTone.NEGATIVE, "判定成功 · 跳过出牌阶段",
        "非红桃判定，本回合不能使用牌。")


def _outcome_bingliang(result, _subject=None):
    if result is None:
        return _missing(result)
    if result.suit == "club":
        return JudgeOutcome(
            JudgeOutcomeTone.POSITIVE, "判定失败 · 兵粮寸断无效",
            "梅花判定通过，正常进入摸牌阶段。")
    return JudgeOutcome(
        JudgeOutcomeTone.NEGATIVE, "判定成功 · 跳过摸牌阶段",
        "非梅花判定，本回合不能摸牌。")


def _outcome_shandian(result, _subject=None):
    if result is None:
        return _missing(result)
    hit = result.suit == "spade" and 2 <= _rank_value(result.rank) <= 9
    if hit:
        return JudgeOutcome(
            JudgeOutcomeTone.NEGATIVE, "判定成功 · 闪电命中",
            "黑桃 2～9 判定命中，受到 3 点雷电伤害。")
    return JudgeOutcome(
        JudgeOutcomeTone.POSITIVE, "判定失败 · 闪电未命中",
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


def _outcome_beige(result, _subject=None):
    """悲歌：判定的是**受伤的那名角色**，结果影响伤害来源。"""

    if result is None:
        return _missing(result)
    suit = getattr(result, "suit", None)
    table = {
        "heart": ("悲歌 · 红桃：受伤者回复 1 点体力", "令伤害来源回复 1 点体力。"),
        "diamond": ("悲歌 · 方块：受伤者摸两张牌", "令伤害来源弃置两张牌。"),
        "club": ("悲歌 · 梅花：伤害来源弃两张牌", "受伤者摸两张牌。"),
        "spade": ("悲歌 · 黑桃：伤害来源翻面", "伤害来源的武将牌翻面。"),
    }
    title, text = table.get(suit, ("悲歌 · 判定完成", ""))
    return JudgeOutcome(JudgeOutcomeTone.POSITIVE, title, text)


def _outcome_wuhun(result, _subject=None):
    """武魂：判定【桃】/【桃园结义】则免于死亡。"""

    if result is None:
        return _missing(result)
    card = getattr(result, "card", None)
    name = getattr(card, "name", None) if card is not None else None
    if name in ("TAO", "TAOYUAN"):
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "武魂 · 免于死亡",
                            "判定为【%s】，本次死亡被免除。"
                            % (getattr(card, "display_name", "桃")))
    return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "武魂 · 立即死亡",
                        "判定不是【桃】，目标立即死亡。")


def _outcome_tuntian(result, _subject=None):
    """屯田：判定不为红桃则把判定牌置于武将牌上（「田」）。"""

    if result is None:
        return _missing(result)
    card = getattr(result, "card", None)
    if getattr(card, "suit", None) != "heart":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "屯田 · 获得判定牌",
                            "非红桃判定，此牌置于武将牌上作为「田」。")
    return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "屯田 · 红桃无效",
                        "红桃判定，本次不获得「田」。")


def _outcome_shuangxiong(result, _subject=None):
    """双雄：获得判定牌，本回合可将**异色**手牌当【决斗】。"""

    if result is None:
        return _missing(result)
    card = getattr(result, "card", None)
    color = "红色" if getattr(card, "card_color", None) == "red" else "黑色"
    return JudgeOutcome(
        JudgeOutcomeTone.POSITIVE, "双雄 · 获得判定牌",
        "判定为%s，本回合可将一张%s手牌当【决斗】使用。"
        % (color, "黑色" if color == "红色" else "红色"))


def _outcome_baonue(result, _subject=None):
    """暴虐：黑色判定回复 1 点体力。"""

    if result is None:
        return _missing(result)
    if getattr(result, "color", None) == "black":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "暴虐 · 回复 1 点体力",
                            "黑色判定，回复 1 点体力。")
    return JudgeOutcome(JudgeOutcomeTone.NEUTRAL, "暴虐 · 判定未生效",
                        "非黑色判定，不回复体力。")


def _outcome_leiji(result, _subject=None):
    """雷击：黑桃判定则对目标造成 2 点雷电伤害（或失去 2 点体力）。"""

    if result is None:
        return _missing(result)
    if getattr(result, "suit", None) == "spade":
        return JudgeOutcome(JudgeOutcomeTone.POSITIVE, "雷击 · 命中",
                            "黑桃判定，目标受到 2 点雷电伤害。")
    return JudgeOutcome(JudgeOutcomeTone.NEGATIVE, "雷击 · 未命中",
                        "非黑桃判定，本次雷击无效。")


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
    # ---- 扩展包武将的判定 ----
    #
    # 每一种判定都必须有"结果结算画面"（只显示判定牌、不说结论，玩家看不出
    # 刚才发生了什么）。新增判定在这里加一条即可，UI 一行都不用改。
    "beige": JudgeSourceSpec(
        reason="beige", kind=JudgeSourceKind.SKILL,
        display_name="悲歌", skill_id="beige",
        rule_text="受伤角色判定：红桃回复 1 点体力，方块摸两张牌，"
                  "黑桃令伤害来源翻面，梅花令伤害来源弃两张牌。",
        outcome_of=_outcome_beige,
    ),
    "wuhun": JudgeSourceSpec(
        reason="wuhun", kind=JudgeSourceKind.SKILL,
        display_name="武魂", skill_id="wuhun",
        rule_text="判定为【桃】或【桃园结义】则免于死亡，否则立即死亡。",
        outcome_of=_outcome_wuhun,
    ),
    "tuntian": JudgeSourceSpec(
        reason="tuntian", kind=JudgeSourceKind.SKILL,
        display_name="屯田", skill_id="tuntian",
        rule_text="于回合外失去牌后判定：非红桃则获得此判定牌作为「田」。",
        outcome_of=_outcome_tuntian,
    ),
    "shuangxiong": JudgeSourceSpec(
        reason="shuangxiong", kind=JudgeSourceKind.SKILL,
        display_name="双雄", skill_id="shuangxiong",
        rule_text="摸牌阶段改为判定：获得此判定牌，本回合可将异色手牌当【决斗】。",
        outcome_of=_outcome_shuangxiong,
    ),
    "baonue": JudgeSourceSpec(
        reason="baonue", kind=JudgeSourceKind.SKILL,
        display_name="暴虐", skill_id="baonue",
        rule_text="受到伤害后判定：黑色则回复 1 点体力。",
        outcome_of=_outcome_baonue,
    ),
    "leiji": JudgeSourceSpec(
        reason="leiji", kind=JudgeSourceKind.SKILL,
        display_name="雷击", skill_id="leiji",
        rule_text="使用或打出【闪】时判定：黑桃则对一名角色造成 2 点雷电伤害。",
        outcome_of=_outcome_leiji,
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
