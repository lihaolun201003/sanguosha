"""身份模式的稳定数据：身份枚举、人数配比与可见性规则。

身份只用稳定 id 参与规则判断，中文名只在 UI 显示；隐藏身份的可见性
统一走 ``visible_identity`` 查询，AI 与 Renderer 都不直接读
``player.identity``，避免作弊或提前泄底。
"""

from enum import Enum


class Identity(str, Enum):
    LORD = "lord"           # 主公
    LOYALIST = "loyalist"   # 忠臣
    REBEL = "rebel"         # 反贼
    RENEGADE = "renegade"   # 内奸


IDENTITY_NAMES = {
    Identity.LORD: "主公",
    Identity.LOYALIST: "忠臣",
    Identity.REBEL: "反贼",
    Identity.RENEGADE: "内奸",
}

# 判定阵营用的集合：主忠一方 vs 反贼 vs 内奸。
LORD_SIDE = (Identity.LORD, Identity.LOYALIST)

# 身份模式的确定性人数配比（标准版）。
IDENTITY_DISTRIBUTION = {
    5: (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
    6: (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
    7: (Identity.LORD, Identity.LOYALIST, Identity.LOYALIST, Identity.REBEL,
        Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
    8: (Identity.LORD, Identity.LOYALIST, Identity.LOYALIST, Identity.REBEL,
        Identity.REBEL, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
}

IDENTITY_PLAYER_COUNTS = tuple(sorted(IDENTITY_DISTRIBUTION))


def identity_name(identity):
    if identity is None:
        return ""
    return IDENTITY_NAMES.get(identity, str(identity))


def distribution_for(player_count):
    """返回该人数下的身份列表（未打乱）；不支持的人数返回 None。"""

    return IDENTITY_DISTRIBUTION.get(int(player_count))


def is_lord_side(identity):
    return identity in LORD_SIDE


def visible_identity(player, viewer=None):
    """某观众能看到这个角色的身份；None 表示"未知"。

    规则：
    * 没有身份（FFA）→ None
    * 主公身份开局公开
    * 阵亡角色身份公开
    * 其他情况只有自己知道自己的身份
    """

    identity = getattr(player, "identity", None)
    if identity is None:
        return None
    if identity is Identity.LORD:
        return identity
    if getattr(player, "identity_revealed", False):
        return identity
    if not getattr(player, "alive", True):
        return identity
    if viewer is not None and viewer is player:
        return identity
    return None
