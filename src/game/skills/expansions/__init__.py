"""扩展包技能库（按扩展包分文件）。

与标准版技能库同构：定义仍然是**无拥有者**的数据，绑定由 ``SkillManager``
完成。新增扩展包时只在这里加一行导入，界面与注册表都不需要改。
"""

from .fire import FIRE_SKILLS
from .forest import FOREST_SKILLS
from .god import GOD_SKILLS
from .mountain import MOUNTAIN_SKILLS
from .sp import SP_SKILLS
from .wind import WIND_SKILLS
from .yijiang import YIJIANG_SKILLS

EXPANSION_SKILLS = (WIND_SKILLS + FIRE_SKILLS + FOREST_SKILLS
                    + MOUNTAIN_SKILLS + YIJIANG_SKILLS + GOD_SKILLS
                    + SP_SKILLS)

__all__ = [
    "EXPANSION_SKILLS",
    "FIRE_SKILLS",
    "FOREST_SKILLS",
    "GOD_SKILLS",
    "MOUNTAIN_SKILLS",
    "SP_SKILLS",
    "YIJIANG_SKILLS",
    "WIND_SKILLS",
]
