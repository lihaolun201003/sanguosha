"""标准版武将技能库（按势力分文件）。

第一批：
    wei.py     曹操 / 司马懿 / 郭嘉 / 张辽 / 夏侯惇
    shu.py     张飞 / 黄月英 / 关羽 / 赵云（+ 第二批的刘备 / 诸葛亮 / 马超）
    wu.py      周瑜 / 孙尚香

第二批（Phase 11）：
    wei2.py    甄姬 / 许褚
    wu2.py     孙权 / 吕蒙 / 大乔 / 甘宁 / 陆逊 / 黄盖
    qun.py     华佗 / 吕布 / 貂蝉
"""

from .qun import QUN_SKILLS
from .shu import SHU_SKILLS
from .wei import WEI_SKILLS
from .wei2 import WEI_EXTRA_SKILLS
from .wu import WU_SKILLS
from .wu2 import WU_EXTRA_SKILLS

# 同一势力的两批技能合并成一份：注册表和选将池都不需要区分批次。
WEI_SKILLS = WEI_SKILLS + WEI_EXTRA_SKILLS
WU_SKILLS = WU_SKILLS + WU_EXTRA_SKILLS

STANDARD_SKILLS = WEI_SKILLS + SHU_SKILLS + WU_SKILLS + QUN_SKILLS

__all__ = [
    "QUN_SKILLS",
    "SHU_SKILLS",
    "STANDARD_SKILLS",
    "WEI_SKILLS",
    "WU_SKILLS",
]
