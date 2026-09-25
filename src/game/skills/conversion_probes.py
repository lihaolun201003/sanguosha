"""Card Conversion 架构探针（Hotfix 2）。

DEVELOPMENT ONLY。这些 probe 只用于验证统一的 Card Action / Conversion
通路，**不会注册到正式游戏**（正式武将仍然是 11 名）：

  Probe A  probe_black_wuxie  黑色牌 → 【无懈可击】，仅 RESPONSE
  Probe B  probe_red_tao      红色牌 → 【桃】，仅 RESCUE
  Probe C  probe_pair_sha     任意两张手牌 → 【杀】，PLAY（多 source）
  Probe D  probe_gear_sha     装备区的牌 → 【杀】，PLAY（装备来源）
  Probe E  probe_pair_shan    任意两张手牌 → 【闪】，RESPONSE（多 source 响应）

测试通过 ``bind_conversion_probes`` 显式绑定。
"""

from src.game.conversion import (
    EQUIPMENT_ZONE,
    PLAY_CONTEXT,
    RESCUE_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)

from .definitions import SkillDef, SkillKind

WUXIE_PROBE_ID = "probe_black_wuxie"
TAO_PROBE_ID = "probe_red_tao"
PAIR_PROBE_ID = "probe_pair_sha"
GEAR_PROBE_ID = "probe_gear_sha"
PAIR_SHAN_PROBE_ID = "probe_pair_shan"


def is_black_card(card):
    return getattr(card, "card_color", None) == "black"


def is_red_card(card):
    return getattr(card, "card_color", None) == "red"


def any_hand_card(card):
    return True


PROBE_BLACK_WUXIE = SkillDef(
    id=WUXIE_PROBE_ID,
    name="墨守",
    description="你可以将一张黑色牌当【无懈可击】打出。",
    kind=SkillKind.VIEW_AS,
    conversions=(
        CardConversion(
            skill_id=WUXIE_PROBE_ID,
            matches=is_black_card,
            name="WUXIE",
            category="trick",
            contexts=(RESPONSE_CONTEXT,),
        ),
    ),
    tags=("conversion", "probe"),
)


PROBE_RED_TAO = SkillDef(
    id=TAO_PROBE_ID,
    name="舍身",
    description="濒死时，你可以将一张红色牌当【桃】使用。",
    kind=SkillKind.VIEW_AS,
    conversions=(
        CardConversion(
            skill_id=TAO_PROBE_ID,
            matches=is_red_card,
            name="TAO",
            contexts=(RESCUE_CONTEXT,),
        ),
    ),
    tags=("conversion", "probe"),
)


PROBE_PAIR_SHA = SkillDef(
    id=PAIR_PROBE_ID,
    name="双刃",
    description="你可以将任意两张手牌当【杀】使用。",
    kind=SkillKind.VIEW_AS,
    conversions=(
        CardConversion(
            skill_id=PAIR_PROBE_ID,
            matches=any_hand_card,
            name="SHA",
            min_sources=2,
            max_sources=2,
            contexts=(PLAY_CONTEXT,),
        ),
    ),
    tags=("conversion", "probe", "multi-source"),
)


PROBE_GEAR_SHA = SkillDef(
    id=GEAR_PROBE_ID,
    name="卸甲",
    description="你可以将装备区的一张牌当【杀】使用。",
    kind=SkillKind.VIEW_AS,
    conversions=(
        CardConversion(
            skill_id=GEAR_PROBE_ID,
            matches=any_hand_card,
            name="SHA",
            contexts=(PLAY_CONTEXT,),
            source_zones=(EQUIPMENT_ZONE,),
        ),
    ),
    tags=("conversion", "probe", "equipment-source"),
)


PROBE_PAIR_SHAN = SkillDef(
    id=PAIR_SHAN_PROBE_ID,
    name="双盾",
    description="你可以将任意两张手牌当【闪】打出。",
    kind=SkillKind.VIEW_AS,
    conversions=(
        CardConversion(
            skill_id=PAIR_SHAN_PROBE_ID,
            matches=any_hand_card,
            name="SHAN",
            min_sources=2,
            max_sources=2,
            # 响应场合的多来源转化（"丈八蛇矛：两张手牌当【杀】"同一类）：
            # 联机的响应窗口必须能选**两张**实体牌，而不是选中第一张就提交。
            contexts=(RESPONSE_CONTEXT,),
        ),
    ),
    tags=("conversion", "probe", "multi-source", "response"),
)


CONVERSION_PROBES = (
    PROBE_BLACK_WUXIE,
    PROBE_RED_TAO,
    PROBE_PAIR_SHA,
    PROBE_GEAR_SHA,
    PROBE_PAIR_SHAN,
)


def bind_conversion_probes(game, player, *skill_ids):
    """把指定的 probe 绑定到某个角色（测试专用入口）。"""

    wanted = skill_ids or tuple(item.id for item in CONVERSION_PROBES)
    for definition in CONVERSION_PROBES:
        if definition.id not in wanted:
            continue
        if game.skill_registry.get(definition.id) is None:
            game.skill_registry.register(definition)
        game.skills.bind(player, definition.id, definition=definition)
    return player
