ZHU_GE_CROSSBOW = "诸葛连弩"
ZHUQUE_FAN = "朱雀羽扇"
QINGGANG_SWORD = "青釭剑"
GUDING_BLADE = "古锭刀"
QINGLONG_BLADE = "青龙偃月刀"
STONE_AXE = "贯石斧"
ICE_SWORD = "寒冰剑"
QILIN_BOW = "麒麟弓"
ZHANGBA_SPEAR = "丈八蛇矛"
GENDER_SWORDS = "雌雄双股剑"
FANGTIAN_HALBERD = "方天画戟"


# ==================================================
# 是否装备指定武器
# ==================================================

def has_weapon(
    player,
    display_name
):

    weapon = player.get_equipment(
        "weapon"
    )

    if weapon is None:
        return False

    return (
        getattr(
            weapon,
            "display_name",
            None
        )
        == display_name
    )


# ==================================================
# 当前武器名称
# ==================================================

def weapon_name(player):

    weapon = player.get_equipment(
        "weapon"
    )

    if weapon is None:
        return None

    return getattr(
        weapon,
        "display_name",
        None
    )


# ==================================================
# 诸葛连弩
# ==================================================

def has_zhuge_crossbow(player):

    return has_weapon(
        player,
        ZHU_GE_CROSSBOW
    )


# ==================================================
# 朱雀羽扇
# ==================================================

def has_zhuque_fan(player):

    return has_weapon(
        player,
        ZHUQUE_FAN
    )


# ==================================================
# 青釭剑
# ==================================================

def has_qinggang_sword(player):

    return has_weapon(
        player,
        QINGGANG_SWORD
    )


# ==================================================
# 古锭刀
# ==================================================

def has_guding_blade(player):

    return has_weapon(
        player,
        GUDING_BLADE
    )


def has_qinglong_blade(player):

    return has_weapon(
        player,
        QINGLONG_BLADE
    )


def has_stone_axe(player):

    return has_weapon(
        player,
        STONE_AXE
    )


def has_ice_sword(player):

    return has_weapon(
        player,
        ICE_SWORD
    )


def has_qilin_bow(player):

    return has_weapon(
        player,
        QILIN_BOW
    )


def has_zhangba_spear(player):

    return has_weapon(
        player,
        ZHANGBA_SPEAR
    )


def has_gender_swords(player):

    return has_weapon(
        player,
        GENDER_SWORDS
    )


def has_fangtian_halberd(player):

    return has_weapon(
        player,
        FANGTIAN_HALBERD
    )
