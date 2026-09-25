from src.card import Card


# ==================================================
# 基本牌
# ==================================================

def create_normal_sha(
    card_color,
    suit=None,
    rank=None
):

    return Card(
        name="SHA",
        category="basic",
        nature="normal",
        card_color=card_color,
        suit=suit,
        rank=rank,
        color=(245, 205, 195),
    )


def create_fire_sha(
    suit=None,
    rank=None
):

    return Card(
        name="SHA",
        category="basic",
        nature="fire",
        card_color="red",
        suit=suit,
        rank=rank,
        color=(245, 150, 110),
    )


def create_thunder_sha(
    suit=None,
    rank=None
):

    return Card(
        name="SHA",
        category="basic",
        nature="thunder",
        card_color="black",
        suit=suit,
        rank=rank,
        color=(195, 175, 245),
    )


def create_shan(
    suit=None,
    rank=None
):

    return Card(
        name="SHAN",
        category="basic",
        color=(205, 225, 250),
        suit=suit,
        rank=rank,
    )


def create_tao(
    suit=None,
    rank=None
):

    return Card(
        name="TAO",
        category="basic",
        color=(210, 245, 215),
        suit=suit,
        rank=rank,
    )


def create_jiu(
    suit=None,
    rank=None
):

    return Card(
        name="JIU",
        category="basic",
        color=(235, 205, 135),
        suit=suit,
        rank=rank,
    )


def trick(name, suit, rank):
    return Card(
        name=name,
        category="trick",
        color=(225, 205, 240),
        suit=suit,
        rank=rank,
    )


TRICK_DEFINITIONS = {
    "WUZHONG": (("heart", "7"), ("heart", "8"), ("heart", "9"), ("heart", "J")),
    "GUOHE": (("spade", "3"), ("spade", "4"), ("spade", "Q"), ("heart", "Q"), ("club", "3"), ("club", "4")),
    "SHUNSHOU": (("spade", "3"), ("spade", "4"), ("spade", "J"), ("diamond", "3"), ("diamond", "4")),
    "JUEDOU": (("spade", "A"), ("club", "A"), ("diamond", "A")),
    "NANMAN": (("spade", "7"), ("spade", "K"), ("club", "7")),
    "WANJIAN": (("heart", "A"),),
    "TAOYUAN": (("heart", "A"),),
    "WUGU": (("heart", "3"), ("heart", "4")),
    "WUXIE": (("spade", "J"), ("spade", "K"), ("club", "Q"), ("club", "K"), ("diamond", "Q"), ("heart", "A"), ("heart", "K")),
    "JIEDAO": (("club", "Q"), ("club", "K")),
    "LEBU": (("spade", "6"), ("heart", "6"), ("club", "6")),
    "SHANDIAN": (("spade", "A"),),
    "HUOGONG": (("heart", "2"), ("heart", "3"), ("diamond", "Q")),
    "TIESUO": (("spade", "J"), ("spade", "Q"), ("club", "10"), ("club", "J"), ("club", "Q"), ("club", "K")),
    "BINGLIANG": (("spade", "10"), ("club", "4")),
}


def create_trick_cards():
    return [
        trick(name, suit, rank)
        for name, definitions in TRICK_DEFINITIONS.items()
        for suit, rank in definitions
    ]


# ==================================================
# 装备牌效果说明
# ==================================================

EQUIPMENT_DESCRIPTIONS = {

    # ==================================================
    # 武器
    # ==================================================

    "ZHUGE": (
        "出牌阶段，你可以使用任意数量的【杀】。"
    ),

    "CIXIONG": (
        "当你使用【杀】指定一名异性角色为目标后，"
        "你可以令其选择一项：弃置一张手牌；"
        "或令你摸一张牌。\n"
        "当前1v1原型中双方默认异性，电脑有手牌时"
        "会弃置；玩家选择弃牌后可再选择具体手牌。"
    ),

    "HANBING": (
        "当你使用【杀】对目标角色造成伤害时，"
        "若该角色有手牌，你可以防止此伤害，"
        "改为依次弃置其至多两张手牌。\n"
        "发动后点击电脑的手牌牌背进行选择。"
    ),

    "QINGGANG": (
        "锁定技，当你使用【杀】指定一名角色"
        "为目标后，无视其防具。"
    ),

    "GUDING": (
        "锁定技，当你使用【杀】对目标角色"
        "造成伤害时，若其没有手牌，"
        "此伤害+1。"
    ),

    "QINGLONG": (
        "当你使用的【杀】被【闪】抵消时，"
        "你可以对相同目标再使用一张【杀】。\n"
        "发动后需要选择具体使用哪一张【杀】。"
    ),

    "ZHANGBA": (
        "你可以将两张手牌当【杀】"
        "使用或打出。\n"
        "当前操作：点击装备区中的【丈八蛇矛】，"
        "然后依次选择两张手牌。"
    ),

    "GUANSHI": (
        "当你使用的【杀】被抵消时，"
        "你可以弃置两张牌，"
        "令此【杀】依然造成伤害。\n"
        "当前规则只允许手动选择两张手牌弃置。"
    ),

    "FANGTIAN": (
        "锁定技，若你使用的【杀】"
        "是你最后的手牌，"
        "则此【杀】可以额外选择"
        "至多两个目标。\n"
        "当前1v1版本会识别并提示触发，"
        "但场上没有可选的额外目标。"
    ),

    "ZHUQUE": (
        "你可以将一张普通【杀】"
        "当火【杀】使用。"
    ),

    "QILIN": (
        "当你使用【杀】对目标角色"
        "造成伤害时，你可以弃置"
        "其装备区里的一张坐骑牌。\n"
        "若有多匹坐骑，需要选择具体弃置哪一匹。"
    ),

    "YINYUEQIANG": (
        "锁定技，你于回合外打出一张黑色花色的牌时，"
        "可以指定你攻击范围内的一名角色：该角色需打出一张【闪】，"
        "否则失去 1 点体力（这不是伤害）。"
    ),

    # ==================================================
    # 防具
    # ==================================================

    "BAGUA": (
        "每当你需要使用或打出【闪】时，"
        "你可以进行一次判定："
        "若判定结果为红色，"
        "视为你使用或打出了一张【闪】。"
    ),

    "RENWANG": (
        "锁定技，黑色普通【杀】对你无效。"
    ),

    "TENGJIA": (
        "锁定技，普通【杀】对你无效；"
        "当你受到火焰伤害时，此伤害+1。\n"
        "以后加入【南蛮入侵】和【万箭齐发】后，"
        "这两种锦囊也将对你无效。"
    ),

    "BAIYIN": (
        "锁定技，当你受到超过1点的伤害时，"
        "将此伤害减少至1点；"
        "当你失去装备区里的【白银狮子】时，"
        "回复1点体力。"
    ),

    # ==================================================
    # +1 马
    # ==================================================

    "JUEYING": (
        "锁定技，其他角色计算与你的距离时+1。"
    ),

    "DILU": (
        "锁定技，其他角色计算与你的距离时+1。"
    ),

    "ZHAOHUANG": (
        "锁定技，其他角色计算与你的距离时+1。"
    ),

    "HUALIU": (
        "锁定技，其他角色计算与你的距离时+1。"
    ),

    # ==================================================
    # -1 马
    # ==================================================

    "CHITU": (
        "锁定技，你计算与其他角色的距离时-1。"
    ),

    "DAWAN": (
        "锁定技，你计算与其他角色的距离时-1。"
    ),

    "ZIXING": (
        "锁定技，你计算与其他角色的距离时-1。"
    ),
}


# ==================================================
# 获取装备说明
# ==================================================

def get_equipment_description(name):

    return EQUIPMENT_DESCRIPTIONS.get(
        name,
        "暂无效果说明。"
    )


# ==================================================
# 装备牌创建函数
# ==================================================

def weapon(
    name,
    attack_range,
    suit=None,
    rank=None
):

    return Card(
        name=name,
        category="equipment",
        subtype="weapon",
        attack_range=attack_range,
        color=(235, 220, 175),
        suit=suit,
        rank=rank,
        description=(
            get_equipment_description(
                name
            )
        ),
    )


def armor(
    name,
    suit=None,
    rank=None
):

    return Card(
        name=name,
        category="equipment",
        subtype="armor",
        color=(205, 215, 230),
        suit=suit,
        rank=rank,
        description=(
            get_equipment_description(
                name
            )
        ),
    )


def defensive_horse(
    name,
    suit=None,
    rank=None
):

    return Card(
        name=name,
        category="equipment",
        subtype="defensive_horse",
        color=(215, 235, 205),
        suit=suit,
        rank=rank,
        description=(
            get_equipment_description(
                name
            )
        ),
    )


def offensive_horse(
    name,
    suit=None,
    rank=None
):

    return Card(
        name=name,
        category="equipment",
        subtype="offensive_horse",
        color=(235, 210, 190),
        suit=suit,
        rank=rank,
        description=(
            get_equipment_description(
                name
            )
        ),
    )


# ==================================================
# 当前基本牌：54
# ==================================================

def create_basic_cards():

    cards = []

    # 当前开发牌堆保留原来的牌数，并从标准牌表中
    # 为每张牌选取对应的真实花色和点数。
    normal_black = [
        ("spade", "7"),
        ("spade", "8"),
        ("spade", "8"),
        ("spade", "9"),
        ("spade", "9"),
        ("spade", "10"),
        ("spade", "10"),
        ("club", "2"),
        ("club", "3"),
        ("club", "4"),
        ("club", "5"),
        ("club", "6"),
        ("club", "7"),
        ("club", "8"),
        ("club", "8"),
        ("club", "9"),
    ]

    normal_red = [
        ("heart", "10"),
        ("heart", "10"),
        ("heart", "J"),
        ("diamond", "6"),
        ("diamond", "7"),
    ]

    fire_sha = [
        ("heart", "4"),
        ("heart", "7"),
        ("heart", "10"),
    ]

    thunder_sha = [
        ("spade", "4"),
        ("spade", "5"),
        ("spade", "6"),
        ("spade", "7"),
        ("spade", "8"),
    ]

    shan = [
        ("heart", "2"),
        ("heart", "2"),
        ("heart", "K"),
        ("diamond", "2"),
        ("diamond", "2"),
        ("diamond", "3"),
        ("diamond", "4"),
        ("diamond", "5"),
        ("diamond", "6"),
        ("diamond", "7"),
        ("diamond", "8"),
        ("diamond", "9"),
        ("diamond", "10"),
        ("diamond", "J"),
    ]

    tao = [
        ("heart", "3"),
        ("heart", "4"),
        ("heart", "6"),
        ("heart", "7"),
        ("heart", "8"),
        ("heart", "9"),
        ("heart", "Q"),
        ("diamond", "Q"),
    ]

    jiu = [
        ("spade", "3"),
        ("club", "9"),
        ("diamond", "9"),
    ]

    cards.extend(
        create_normal_sha(
            "black",
            suit,
            rank
        )
        for suit, rank in normal_black
    )

    cards.extend(
        create_normal_sha(
            "red",
            suit,
            rank
        )
        for suit, rank in normal_red
    )

    cards.extend(
        create_fire_sha(
            suit,
            rank
        )
        for suit, rank in fire_sha
    )

    cards.extend(
        create_thunder_sha(
            suit,
            rank
        )
        for suit, rank in thunder_sha
    )

    cards.extend(
        create_shan(
            suit,
            rank
        )
        for suit, rank in shan
    )

    cards.extend(
        create_tao(
            suit,
            rank
        )
        for suit, rank in tao
    )

    cards.extend(
        create_jiu(
            suit,
            rank
        )
        for suit, rank in jiu
    )

    assert len(cards) == 54

    return cards


# ==================================================
# 身份局 + 军争装备牌
# ==================================================

def create_equipment_cards():

    cards = []

    # ==================================================
    # 武器
    # ==================================================

    # 诸葛连弩 ×2
    cards.append(
        weapon(
            "ZHUGE",
            1,
            "club",
            "A"
        )
    )

    cards.append(
        weapon(
            "ZHUGE",
            1,
            "diamond",
            "A"
        )
    )


    # 雌雄双股剑
    cards.append(
        weapon(
            "CIXIONG",
            2,
            "spade",
            "2"
        )
    )


    # 寒冰剑
    cards.append(
        weapon(
            "HANBING",
            2,
            "spade",
            "2"
        )
    )


    # 青釭剑
    cards.append(
        weapon(
            "QINGGANG",
            2,
            "spade",
            "6"
        )
    )


    # 古锭刀
    cards.append(
        weapon(
            "GUDING",
            2,
            "spade",
            "A"
        )
    )


    # 青龙偃月刀
    cards.append(
        weapon(
            "QINGLONG",
            3,
            "spade",
            "5"
        )
    )


    # 丈八蛇矛
    cards.append(
        weapon(
            "ZHANGBA",
            3,
            "spade",
            "Q"
        )
    )


    # 贯石斧
    cards.append(
        weapon(
            "GUANSHI",
            3,
            "diamond",
            "5"
        )
    )


    # 方天画戟
    cards.append(
        weapon(
            "FANGTIAN",
            4,
            "diamond",
            "Q"
        )
    )


    # 朱雀羽扇
    cards.append(
        weapon(
            "ZHUQUE",
            4,
            "diamond",
            "A"
        )
    )


    # 麒麟弓
    cards.append(
        weapon(
            "QILIN",
            5,
            "heart",
            "5"
        )
    )


    # SP010 银月枪（扩展装备）：武器，方块 Q，攻击范围 3。
    # 它不是标准军争牌堆里的牌，随 SP 卡面一起加入，规则见
    # src/game/equipment_skills/system.py 的「银月枪」分支。
    cards.append(
        weapon(
            "YINYUEQIANG",
            3,
            "diamond",
            "Q"
        )
    )


    # ==================================================
    # 防具
    # ==================================================

    # 八卦阵 ×2
    cards.append(
        armor(
            "BAGUA",
            "spade",
            "2"
        )
    )

    cards.append(
        armor(
            "BAGUA",
            "club",
            "2"
        )
    )


    # 仁王盾
    cards.append(
        armor(
            "RENWANG",
            "club",
            "2"
        )
    )


    # 藤甲 ×2
    cards.append(
        armor(
            "TENGJIA",
            "spade",
            "2"
        )
    )

    cards.append(
        armor(
            "TENGJIA",
            "club",
            "2"
        )
    )


    # 白银狮子
    cards.append(
        armor(
            "BAIYIN",
            "club",
            "A"
        )
    )


    # ==================================================
    # +1 马
    # ==================================================

    cards.append(
        defensive_horse(
            "JUEYING",
            "spade",
            "5"
        )
    )

    cards.append(
        defensive_horse(
            "DILU",
            "club",
            "5"
        )
    )

    cards.append(
        defensive_horse(
            "ZHAOHUANG",
            "heart",
            "K"
        )
    )

    cards.append(
        defensive_horse(
            "HUALIU",
            "diamond",
            "K"
        )
    )


    # ==================================================
    # -1 马
    # ==================================================

    cards.append(
        offensive_horse(
            "CHITU",
            "heart",
            "5"
        )
    )

    cards.append(
        offensive_horse(
            "DAWAN",
            "spade",
            "K"
        )
    )

    cards.append(
        offensive_horse(
            "ZIXING",
            "diamond",
            "K"
        )
    )


    assert len(cards) == 26

    return cards


# ==================================================
# 当前开发牌堆
# ==================================================

def create_development_deck():

    cards = []

    cards.extend(
        create_basic_cards()
    )

    cards.extend(
        create_equipment_cards()
    )

    cards.extend(create_trick_cards())

    assert len(cards) == 129

    return cards


def create_standard_military_deck():
    """Current playable standard + military foundation (no Guozhan cards)."""
    return create_development_deck()
