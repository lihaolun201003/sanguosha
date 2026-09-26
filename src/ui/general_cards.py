"""武将卡的公共绘制：立绘 / 姓名 / 势力 / 体力 / 技能，两屏共用。

「我的武将池」与「选择你的武将」两屏画的是同一批东西（``GeneralDef`` +
``SkillDef``），差别只在布局。所以卡面与详情都放在这里，两屏各自只负责
"摆在哪里"——颜色 / 字体 / 素材一律来自 theme 与 AssetRegistry，不认识
任何具体武将。

数据永远来自注册表：新增武将不需要改这里一行。
"""

import pygame

from . import assets as assets_module
from . import theme
from .widgets import wrap_text

#: 势力 → 主色（只影响色条与角标，不改文字色）。
KINGDOM_TONES = {
    "wei": (86, 116, 178),
    "shu": (176, 84, 72),
    "wu": (72, 152, 118),
    "qun": (140, 132, 108),
    "god": (168, 132, 78),
}

#: 技能类型 → 中文（与技能栏同一份口径）。
SKILL_KIND_LABELS = {
    "active": "主动技",
    "view_as": "视为技",
    "locked": "锁定技",
    "passive": "触发技",
}


def kingdom_tone(general):
    return KINGDOM_TONES.get(getattr(general, "kingdom", ""), theme.BRONZE)


def kingdom_label(general):
    return getattr(general, "kingdom_name", "") or str(
        getattr(general, "kingdom", "") or "")


def skill_kind_label(definition):
    return SKILL_KIND_LABELS.get(
        getattr(getattr(definition, "kind", None), "value", ""), "")


def general_art(general, area, metrics):
    """武将立绘：有卡面素材就按比例放，没有时返回 ``None``。

    返回 ``(scaled, target_rect)``；``area`` 是给定的可用区域。
    """

    if general is None or area.width < 16 or area.height < 16:
        return None
    asset_id = assets_module.general_asset_id(general.id)
    registry = assets_module.get_registry()
    source = registry.surface(asset_id)
    if source is None:
        return None
    target = assets_module.fit_contain(area, source.get_size(), align="midtop")
    scaled = registry.scaled(asset_id, target.size)
    if scaled is None:
        return None
    return scaled, target


def draw_portrait(surface, general, rect, metrics, *, framed=True):
    """画一张武将立绘；没有素材时退回"程序绘制头像圆 + 姓氏"。"""

    rect = pygame.Rect(rect)
    art = general_art(general, rect, metrics)
    if art is not None:
        scaled, target = art
        if framed:
            frame = target.inflate(metrics.px(6), metrics.px(6))
            pygame.draw.rect(surface, theme.PANEL_WARM_SUNKEN, frame,
                             border_radius=metrics.px(8))
            pygame.draw.rect(surface, theme.BRONZE_DIM, frame, 1,
                             border_radius=metrics.px(8))
        surface.blit(scaled, target.topleft)
        return target

    tone = kingdom_tone(general)
    size = max(metrics.px(28), min(rect.width, rect.height) - metrics.px(16))
    center = (rect.centerx, rect.y + size // 2 + metrics.px(6))
    pygame.draw.circle(surface, theme.PANEL_WARM_SUNKEN, center, size // 2)
    pygame.draw.circle(surface, tone, center, size // 2, max(2, metrics.px(3)))
    initial = metrics.fonts.get("large").render(
        str(getattr(general, "name", "?"))[:1], True, theme.TEXT_WARM)
    surface.blit(initial, initial.get_rect(center=center))
    return pygame.Rect(center[0] - size // 2, center[1] - size // 2, size, size)


def draw_hp_pips(surface, general, origin, metrics, *, font=None, color=None):
    """在 ``origin``（左边中点）画体力点 + 数值，返回占用宽度。"""

    font = font or metrics.fonts.get("micro")
    color = color or theme.TEXT_WARM_DIM
    max_hp = max(0, int(getattr(general, "max_hp", 0) or 0))
    radius = max(3, metrics.px(5))
    spacing = metrics.px(13)
    for index in range(max_hp):
        center = (origin[0] + radius + index * spacing, origin[1])
        pygame.draw.circle(surface, theme.HEAL, center, radius)
    text = font.render("体力 %d" % max_hp, True, color)
    text_x = origin[0] + max_hp * spacing + (metrics.px(6) if max_hp else 0)
    surface.blit(text, (text_x, origin[1] - text.get_height() // 2))
    return text_x + text.get_width() - origin[0]


def draw_skill_list(surface, general, game, rect, metrics, *, name_font=None,
                    body_font=None, title_color=None):
    """在 ``rect`` 里自上而下画这名武将的全部技能（名称 + 类型 + 说明）。

    说明文本来自 ``SkillDef.description``（与技能栏同一份），这里不复制、
    不改写。高度不够时截断（``wrap_text(max_lines=...)``），绝不画到面板外。
    """

    rect = pygame.Rect(rect)
    if general is None or rect.height <= 0 or rect.width <= 0:
        return 0
    name_font = name_font or metrics.fonts.get("normal")
    body_font = body_font or metrics.fonts.get("small")
    title_color = title_color or theme.BRONZE_BRIGHT

    registry = getattr(game, "skill_registry", None)
    cursor = rect.y
    drawn = 0
    for skill_id in getattr(general, "skill_ids", ()) or ():
        definition = registry.get(skill_id) if registry is not None else None
        if definition is None:
            continue
        head = name_font.render(
            "【%s】" % getattr(definition, "name", skill_id), True, title_color)
        if cursor + head.get_height() > rect.bottom:
            break
        surface.blit(head, (rect.x, cursor))
        kind_text = SKILL_KIND_LABELS.get(
            getattr(getattr(definition, "kind", None), "value", ""), "")
        if kind_text:
            chip = body_font.render(kind_text, True, theme.TEXT_WARM_MUTED)
            surface.blit(chip, (rect.right - chip.get_width(),
                                cursor + (head.get_height() - chip.get_height()) // 2))
        cursor += head.get_height() + metrics.px(4)

        body = getattr(definition, "description", "") or ""
        if body:
            remaining = max(1, (rect.bottom - cursor) // max(1, body_font.get_linesize()))
            lines = wrap_text(body, body_font, rect.width - metrics.px(6),
                              max_lines=remaining)
            for line in lines:
                rendered = body_font.render(line, True, theme.TEXT_WARM_DIM)
                if cursor + rendered.get_height() > rect.bottom:
                    break
                surface.blit(rendered, (rect.x + metrics.px(6), cursor))
                cursor += rendered.get_height() + metrics.px(1)
            cursor += metrics.px(6)
        drawn += 1
    return drawn
