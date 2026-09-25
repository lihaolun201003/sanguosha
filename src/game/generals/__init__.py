"""Generals: data-only character definitions and their registry."""

from .catalog import (
    CAOCAO,
    DA_QIAO,
    DIAO_CHAN,
    GAN_NING,
    GUANYU,
    GUOJIA,
    HUA_TUO,
    HUANGYUEYING,
    HUANG_GAI,
    LIUBEI,
    LU_XUN,
    LV_BU,
    LV_MENG,
    MA_CHAO,
    SECOND_ROSTER_GENERALS,
    SIMAYI,
    STANDARD_GENERALS,
    SUNSHANGXIANG,
    SUN_QUAN,
    XIAHOUDUN,
    XU_CHU,
    ZHANGLIAO,
    ZHANGFEI,
    ZHAOYUN,
    ZHEN_JI,
    ZHOUYU,
    ZHUGE_LIANG,
)
from .definitions import KINGDOMS, GeneralDef
from .expansions import EXPANSION_GENERALS
from .registry import GeneralRegistry


def create_default_general_registry():
    registry = GeneralRegistry()
    registry.register_all(STANDARD_GENERALS)
    # 扩展包（风 / 火 / 林 / 山 / 神 / 一将成名 / SP）与标准版同一个注册表：
    # 界面、筛选、可用性判定全部从注册表推导，新增包不需要改任何 UI。
    registry.register_all(EXPANSION_GENERALS)
    return registry


__all__ = [
    "CAOCAO",
    "DA_QIAO",
    "DIAO_CHAN",
    "EXPANSION_GENERALS",
    "GAN_NING",
    "HUA_TUO",
    "HUANG_GAI",
    "LIUBEI",
    "LU_XUN",
    "LV_BU",
    "LV_MENG",
    "MA_CHAO",
    "SECOND_ROSTER_GENERALS",
    "SUN_QUAN",
    "XU_CHU",
    "ZHEN_JI",
    "ZHUGE_LIANG",
    "GeneralDef",
    "GeneralRegistry",
    "GUANYU",
    "GUOJIA",
    "HUANGYUEYING",
    "KINGDOMS",
    "SIMAYI",
    "SUNSHANGXIANG",
    "ZHANGLIAO",
    "ZHAOYUN",
    "ZHOUYU",
    "STANDARD_GENERALS",
    "XIAHOUDUN",
    "ZHANGFEI",
    "create_default_general_registry",
]
