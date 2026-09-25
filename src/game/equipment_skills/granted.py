"""装备赋予的技能：装备在身上的时候才存在（丈八蛇矛）。

装备牌的规则有两种实现方式：

* **锁定效果**（青釭剑无视防具、藤甲火焰伤害 +1…）：由 ``EquipmentEventSkill``
  与各 Flow 直接读装备判断，装备在就生效；
* **赋予技能**（丈八蛇矛「你可以将两张手牌当【杀】使用或打出」）：装备进入
  装备区时给持有者绑定一个技能定义，离开装备区时解绑。

走第二种的装备**不需要为它另写一套交互**：绑定之后它和武将技能在同一个
``SkillManager`` 里，转换注册（``ConversionRegistry``）、技能栏、视为技入口
（``Game.begin_view_as``）与响应候选（``usable_options`` / ``respondable_options``）
全部自动成立——单机、联机与响应窗口用的是同一套规则。
"""

from ..conversion import HAND_ZONE, PLAY_CONTEXT, RESPONSE_CONTEXT, CardConversion
from ..skills.definitions import SkillDef, SkillKind

#: 丈八蛇矛的技能 id：装备技用自己的命名空间，避免与武将技能撞名。
ZHANGBA_SKILL_ID = "equipment.zhangba"


def _is_hand_card(card):
    """任意一张实体手牌（虚拟牌不能再作转换来源，避免递归）。"""

    return not getattr(card, "is_virtual", False)


ZHANGBA_SKILL = SkillDef(
    id=ZHANGBA_SKILL_ID,
    name="丈八蛇矛",
    description="你可以将两张手牌当【杀】使用或打出。",
    kind=SkillKind.VIEW_AS,
    conversions=(
        CardConversion(
            skill_id=ZHANGBA_SKILL_ID,
            matches=_is_hand_card,
            name="SHA",
            # 恰好两张手牌：min = max = 2。区域限定手牌，装备区的牌不算。
            min_sources=2,
            max_sources=2,
            source_zones=(HAND_ZONE,),
            # 使用（出牌阶段的杀）与打出（决斗 / 南蛮的响应）都成立。
            contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
        ),
    ),
    tags=("equipment", "conversion"),
)

#: 正式技能表要包含的定义：装备技与武将技在同一个表里按 id 查找。
EQUIPMENT_GRANTED_SKILLS = (ZHANGBA_SKILL,)

#: 装备牌名 → 它赋予持有者的技能 id。名字用 ``card_catalog`` 里的短名。
GRANTED_BY_EQUIPMENT = (
    ("ZHANGBA", ZHANGBA_SKILL_ID),
)


def granted_skill_id(card_name):
    """一张装备牌赋予的技能 id（不是赋予技能的装备则返回 None）。"""

    for equip_name, skill_id in GRANTED_BY_EQUIPMENT:
        if equip_name == card_name:
            return skill_id
    return None


def equipment_skill_state(player):
    """持有者**现在**由装备获得的技能 id 集合（装备区的实际内容为准）。"""

    result = set()
    for card in (getattr(player, "equipment", None) or {}).values():
        if card is None:
            continue
        skill_id = granted_skill_id(getattr(card, "name", None))
        if skill_id:
            result.add(skill_id)
    return result


def sync_equipment_skills(game, player):
    """把"装备赋予的技能"收敛到玩家身上（幂等，可重复调用）。

    装备进 / 出装备区时都要调用。这里不看"装备了 / 卸下了"这类增量事件，
    而是每次按当前装备重算：换装、被拆、被顺、死亡清理无论走哪条路径，
    结果都一样，不会漏绑也不会重复绑。

    返回本次发生的 ``(动作, 技能 id)`` 列表，供测试与诊断使用。
    """

    manager = getattr(game, "skills", None)
    bind = getattr(manager, "bind", None)
    unbind = getattr(manager, "unbind", None)
    if manager is None or bind is None or unbind is None or player is None:
        return ()

    wanted = equipment_skill_state(player)
    changed = []
    for _equip_name, skill_id in GRANTED_BY_EQUIPMENT:
        bound = skill_id in manager.skill_ids_of(player)
        if skill_id in wanted and not bound:
            bind(player, skill_id)
            changed.append(("bind", skill_id))
        elif bound and skill_id not in wanted:
            unbind(player, skill_id)
            changed.append(("unbind", skill_id))
    return tuple(changed)
