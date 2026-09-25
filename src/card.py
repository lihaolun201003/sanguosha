from dataclasses import dataclass, field
from itertools import count
from typing import Optional, Tuple


# 内部牌名 → 中文显示名（实体牌与技能生成的虚拟牌共用同一张表）。
BASIC_NAMES = {
    "SHA": "杀",
    "SHAN": "闪",
    "TAO": "桃",
    "JIU": "酒",
}

TRICK_NAMES = {
    "WUZHONG": "无中生有",
    "GUOHE": "过河拆桥",
    "SHUNSHOU": "顺手牵羊",
    "JUEDOU": "决斗",
    "NANMAN": "南蛮入侵",
    "WANJIAN": "万箭齐发",
    "TAOYUAN": "桃园结义",
    "WUGU": "五谷丰登",
    "WUXIE": "无懈可击",
    "JIEDAO": "借刀杀人",
    "LEBU": "乐不思蜀",
    "SHANDIAN": "闪电",
    "HUOGONG": "火攻",
    "TIESUO": "铁索连环",
    "BINGLIANG": "兵粮寸断",
}

EQUIPMENT_NAMES = {
    "ZHUGE": "诸葛连弩",
    "CIXIONG": "雌雄双股剑",
    "HANBING": "寒冰剑",
    "QINGGANG": "青釭剑",
    "GUDING": "古锭刀",
    "QINGLONG": "青龙偃月刀",
    "ZHANGBA": "丈八蛇矛",
    "GUANSHI": "贯石斧",
    "FANGTIAN": "方天画戟",
    "ZHUQUE": "朱雀羽扇",
    "QILIN": "麒麟弓",
    "BAGUA": "八卦阵",
    "RENWANG": "仁王盾",
    "TENGJIA": "藤甲",
    "BAIYIN": "白银狮子",
    "JUEYING": "绝影",
    "DILU": "的卢",
    "ZHAOHUANG": "爪黄飞电",
    "HUALIU": "骅骝",
    "CHITU": "赤兔",
    "DAWAN": "大宛",
    "ZIXING": "紫骍",
}

DISPLAY_NAMES = {}
DISPLAY_NAMES.update(BASIC_NAMES)
DISPLAY_NAMES.update(TRICK_NAMES)
DISPLAY_NAMES.update(EQUIPMENT_NAMES)


def display_name_for(name, nature="normal"):
    """牌名 → 显示名；火杀 / 雷杀按属性区分。"""

    if name == "SHA":
        if nature == "fire":
            return "火杀"
        if nature == "thunder":
            return "雷杀"
        return "杀"
    return DISPLAY_NAMES.get(name, name)


_CARD_IDS = count(1)


@dataclass
class Card:

    id: str = field(default_factory=lambda: "card-" + str(next(_CARD_IDS)), init=False)

    name: str

    category: str

    color: Tuple[int, int, int]

    nature: str = "normal"

    card_color: Optional[str] = None

    subtype: Optional[str] = None

    attack_range: int = 1

    suit: Optional[str] = None

    rank: Optional[str] = None

    description: Optional[str] = None


    def __post_init__(self):

        # 花色决定牌的红黑颜色。保留 card_color 字段，
        # 供仁王盾、八卦阵等现有规则直接使用。
        if self.suit in (
            "heart",
            "diamond",
        ):
            self.card_color = "red"

        elif self.suit in (
            "spade",
            "club",
        ):
            self.card_color = "black"


    @property
    def suit_symbol(self):

        symbols = {
            "spade": "♠",
            "heart": "♥",
            "club": "♣",
            "diamond": "♦",
        }

        return symbols.get(
            self.suit,
            ""
        )


    @property
    def suit_name(self):

        names = {
            "spade": "黑桃",
            "heart": "红桃",
            "club": "梅花",
            "diamond": "方块",
        }

        return names.get(
            self.suit,
            ""
        )


    @property
    def identity_label(self):

        if not self.suit_symbol or not self.rank:
            return ""

        return (
            self.suit_symbol
            + " "
            + str(self.rank)
        )


    @property
    def display_name(self):

        return display_name_for(self.name, self.nature)


    @property
    def is_sha(self):

        return self.name == "SHA"


    @property
    def is_equipment(self):

        return self.category == "equipment"
