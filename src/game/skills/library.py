"""标准武将技能的聚合出口。

技能实现按势力拆分在 ``standard/`` 下；这里只做统一导出，方便旧调用点继续
使用 ``from src.game.skills.library import STANDARD_SKILLS``。
"""

from .standard import SHU_SKILLS, STANDARD_SKILLS, WEI_SKILLS, WU_SKILLS


def build_standard_skill_defs():
    return STANDARD_SKILLS


__all__ = [
    "SHU_SKILLS",
    "STANDARD_SKILLS",
    "WEI_SKILLS",
    "WU_SKILLS",
    "build_standard_skill_defs",
]
