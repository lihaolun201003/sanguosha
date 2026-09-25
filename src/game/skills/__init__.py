"""Skill foundation: definitions, registry, binding, modifiers, and state.

Layout:

    definitions.py   SkillDef / ModifierSpec（技能是什么）
    registry.py      SkillRegistry（定义表）+ SkillManager（按玩家绑定）
    modifiers.py     持续规则修正（距离 / 手牌上限 / 摸牌数 / 出杀 / 攻击范围）
    state.py         按技能命名空间保存的标记与计数
    library.py       正式武将技能（咆哮 / 集智 / 刚烈）
    probes.py        架构探针技能（仅测试使用，不进入默认注册表）
"""

from .definitions import ModifierSpec, SkillDef, SkillKind, active, triggered
from .library import STANDARD_SKILLS, build_standard_skill_defs
from .modifiers import Modifier, ModifierKind, ModifierRegistry
from .registry import SkillManager, SkillRegistry
from .state import ResetScope, SkillState


def create_default_skill_registry():
    """注册表：正式武将技能 + 装备赋予的技能；探针技能需要显式注册。

    装备赋予的技能（丈八蛇矛一类）也在这张表里，区别只在**绑定时机**：
    武将技在选将时绑定，装备技在装备进入装备区时绑定
    （见 ``equipment_skills.granted.sync_equipment_skills``）。
    """

    from ..equipment_skills.granted import EQUIPMENT_GRANTED_SKILLS
    from .expansions import EXPANSION_SKILLS

    registry = SkillRegistry()
    registry.register_all(STANDARD_SKILLS)
    # 扩展包（风 / 火 / 林 / 山 / 神 / 一将成名 / SP）与标准版同一张表：
    # 武将只声明 skill_ids，选将时由 SkillManager 统一解析。
    registry.register_all(EXPANSION_SKILLS)
    registry.register_all(EQUIPMENT_GRANTED_SKILLS)
    return registry


def create_probe_skill_registry():
    """测试用注册表：正式技能 + 架构探针。"""

    from .probes import PROBE_SKILLS

    registry = create_default_skill_registry()
    registry.register_all(PROBE_SKILLS)
    return registry


__all__ = [
    "Modifier",
    "ModifierKind",
    "ModifierRegistry",
    "ModifierSpec",
    "ResetScope",
    "SkillDef",
    "SkillKind",
    "SkillManager",
    "SkillRegistry",
    "SkillState",
    "STANDARD_SKILLS",
    "active",
    "build_standard_skill_defs",
    "create_default_skill_registry",
    "create_probe_skill_registry",
    "triggered",
]
