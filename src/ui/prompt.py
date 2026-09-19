"""PromptPanel: one place that tells the human what is being asked of them."""

import pygame

from . import layout
from . import theme
from .widgets import draw_panel

PHASE_LABELS = {
    "prepare": "准备阶段",
    "judge": "判定阶段",
    "draw": "摸牌阶段",
    "play": "出牌阶段",
    "discard": "弃牌阶段",
    "finish": "结束阶段",
    "over": "游戏结束",
}

# 内部牌名 → 界面显示名，只用于提示文案。
CARD_LABELS = {
    "SHA": "杀",
    "SHAN": "闪",
    "TAO": "桃",
    "JIU": "酒",
    "WUXIE": "无懈可击",
    "WUZHONG": "无中生有",
    "GUOHE": "过河拆桥",
    "SHUNSHOU": "顺手牵羊",
    "JUEDOU": "决斗",
    "NANMAN": "南蛮入侵",
    "WANJIAN": "万箭齐发",
    "TAOYUAN": "桃园结义",
    "WUGU": "五谷丰登",
    "JIEDAO": "借刀杀人",
    "LEBU": "乐不思蜀",
    "SHANDIAN": "闪电",
    "HUOGONG": "火攻",
    "TIESUO": "铁索连环",
    "BINGLIANG": "兵粮寸断",
}


def card_label(name):
    return CARD_LABELS.get(name, name)


class PromptInfo:
    def __init__(self, kind, title, body="", progress="", accent=theme.GOLD_BRIGHT):
        self.kind = kind
        self.title = title
        self.body = body
        self.progress = progress
        self.accent = accent


def describe(game):
    """Derive the current prompt from live UI state (read-only)."""

    if game.game_over:
        return PromptInfo("over", "对局结束", game.message, accent=theme.GOLD_BRIGHT)

    selection = game.pending_selection
    if selection is not None:
        remaining = max(0, selection["number"] - len(selection["selected"]))
        zone = selection["zone"]
        if zone in ("public_pool", "selection_pool"):
            body = "点击公共区的牌完成选择"
        elif zone in ("hand", "player_hand"):
            body = "点击手牌完成选择"
        else:
            body = "点击装备区完成选择"
        return PromptInfo("select", selection["prompt"], body, "还需选择 %d 张" % remaining, theme.TARGET_BLUE)

    if game.zhangba_selecting:
        return PromptInfo(
            "zhangba",
            "丈八蛇矛",
            "点击两张手牌当【杀】使用，点击武器取消",
            "已选择 %d / 2" % len(game.zhangba_selected),
            theme.GOLD_BRIGHT,
        )

    target_selection = game.pending_target_selection
    if target_selection is not None:
        selected = len(target_selection["selected"])
        maximum = target_selection["maximum"]
        stage = "victim" if target_selection.get("stage") == "victim" else "target"
        if stage == "victim":
            title = "借刀杀人：选择被迫出杀的角色"
        elif maximum == 1:
            title = "请选择目标"
        else:
            title = "请选择 1～%d 个目标" % maximum
        return PromptInfo(
            "target",
            title,
            "点击角色面板选择，点击「确认目标」结算",
            "已选择 %d / %d" % (selected, maximum),
            theme.TARGET_YELLOW,
        )

    if game.choice.active:
        request = game.choice.current
        return PromptInfo("choice", request.title, request.prompt, accent=theme.GOLD_BRIGHT)

    if game.response.active:
        prompt = game.response.current.prompt
        allowed = game.response.current.allowed_cards
        hint = (
            "点击手牌中的【" + "】【".join(card_label(name) for name in sorted(allowed)) + "】"
            if allowed else ""
        )
        return PromptInfo("response", "需要你的响应", prompt, hint, theme.DANGER)

    request = game.pending_request
    if request is not None:
        owner = getattr(request.target, "name", "?")
        if getattr(request.target, "is_human", False):
            return PromptInfo("response", "需要你的响应", request.prompt, accent=theme.DANGER)
        return PromptInfo("waiting", "等待 " + owner + " 响应", request.prompt, accent=theme.TEXT_DIM)

    if game.phase == "play" and game.current_turn_player is game.player and not game.busy:
        return PromptInfo("play", "出牌阶段", "点击手牌使用，或点击「结束回合」", accent=theme.GOLD_BRIGHT)
    if game.phase == "discard" and game.current_turn_player is game.player:
        need = max(0, len(game.player.hand) - game.player.hp)
        return PromptInfo("discard", "弃牌阶段", "请弃置 %d 张手牌" % need, accent=theme.DANGER)

    current = game.current_turn_player.name if game.current_turn_player else "-"
    phase_label = PHASE_LABELS.get(game.phase, game.phase)
    return PromptInfo("info", current + " · " + phase_label, game.message, accent=theme.TEXT_DIM)


def draw(surface, info, rect=None):
    rect = pygame.Rect(rect or layout.PROMPT_RECT)
    if info is None:
        return rect

    draw_panel(
        surface,
        rect,
        fill=theme.PANEL_DEEP,
        border=info.accent if info.kind != "info" else theme.GOLD_DIM,
        border_width=2 if info.kind == "info" else theme.BORDER,
    )

    fonts = theme.fonts()
    title_font = fonts.get("normal")
    title = title_font.render(info.title, True, info.accent)
    surface.blit(title, (rect.x + 14, rect.y + 8))

    body_font = fonts.get("small")
    body = body_font.render(info.body[:46], True, theme.TEXT)
    surface.blit(body, (rect.x + 14, rect.y + 34))

    if info.progress:
        progress_font = fonts.get("small")
        progress = progress_font.render(info.progress, True, theme.TARGET_YELLOW)
        surface.blit(progress, progress.get_rect(midright=(rect.right - 14, rect.centery)))

    return rect
