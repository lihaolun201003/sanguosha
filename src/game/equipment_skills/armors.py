def has_armor(player, display_name):
    armor = player.get_equipment("armor")

    if armor is None:
        return False

    return armor.display_name == display_name


def has_bagua(player):
    return has_armor(
        player,
        "八卦阵"
    )


def has_renwang_shield(player):
    return has_armor(
        player,
        "仁王盾"
    )


def has_vine_armor(player):
    return has_armor(
        player,
        "藤甲"
    )


def has_silver_lion(player):
    return has_armor(
        player,
        "白银狮子"
    )


def is_silver_lion(card):
    if card is None:
        return False

    return (
        getattr(card, "display_name", None)
        == "白银狮子"
    )


def is_red_card(card):
    if card is None:
        return False

    return (
        getattr(card, "card_color", None)
        == "red"
    )


def blocking_armor_for_sha(
    defender,
    sha
):
    if has_renwang_shield(defender):
        if (
            sha.nature == "normal"
            and sha.card_color == "black"
        ):
            return "仁王盾"

    if has_vine_armor(defender):
        if sha.nature == "normal":
            return "藤甲"

    return None


def modify_sha_damage(
    defender,
    sha,
    damage
):
    result = damage
    effects = []

    if (
        has_vine_armor(defender)
        and sha.nature == "fire"
    ):
        result += 1
        effects.append("【藤甲】使火焰伤害 +1")

    if (
        has_silver_lion(defender)
        and result > 1
    ):
        result = 1
        effects.append("【白银狮子】将伤害改为 1")

    return result, effects


def sha_ignored_by_armor(
    defender,
    sha
):
    """
    返回：
    True  -> 这张杀被防具直接抵消
    False -> 正常继续结算
    """

    return (
        blocking_armor_for_sha(
            defender,
            sha
        )
        is not None
    )


def modify_damage_by_armor(
    defender,
    damage,
    nature
):
    """
    对最终伤害进行防具修正。
    """

    class DamageSource:
        pass

    source = DamageSource()
    source.nature = nature

    result, _ = modify_sha_damage(
        defender,
        source,
        damage
    )

    return result
