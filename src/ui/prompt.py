"""PromptPanel: one place that tells the human what is being asked of them."""

import pygame

from . import layout
from . import theme
from .widgets import draw_panel, ellipsize_text

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


def local_can_play(state):
    """本机玩家现在能不能主动出牌 / 结束回合。

    两种状态各有各的实现（都叫 ``local_can_play``）：单机的权威 ``Game`` 看
    引擎有没有别的窗口在等答案；联网客户端的只读视图还要求**手里真的有一条
    尚未回答的出牌阶段决策**——只看"我的回合 + 出牌阶段"会让等待中的玩家
    误以为自己能操作。
    """

    probe = getattr(state, "local_can_play", None)
    if callable(probe):
        return bool(probe())
    return False


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

    picker_options = game.card_action_picker()
    if picker_options:
        sources = picker_options[0].describe_sources()
        return PromptInfo(
            "action",
            "选择操作",
            "这张牌有多个可用方式，请选择一种",
            sources,
            theme.GOLD_BRIGHT,
        )

    view_as = game.pending_view_as
    if view_as is not None:
        verb = "打出" if view_as.context.is_response else "使用"
        return PromptInfo(
            "view_as",
            "【" + view_as.skill_name + "】请选择 %d 张牌，将其当【%s】%s"
            % (view_as.required_source_count,
               _result_display(view_as.result_name), verb),
            "点击手牌选择来源：" + view_as.describe_selected() if view_as.selected_source_cards
            else "点击手牌选择要转化的牌",
            "已选择 %d / %d" % (
                len(view_as.selected_source_cards), view_as.required_source_count),
            theme.TARGET_BLUE,
        )

    card_action = game.pending_card_action
    if card_action is not None and card_action["option"] is not None:
        option = card_action["option"]
        chosen = len(card_action["selected"])
        return PromptInfo(
            "action",
            "【" + (option.skill_name or option.skill_id) + "】将"
            + option.describe_sources() + "当【" + option.result_display + "】使用",
            "请再选择 %d 张牌作为转化来源" % max(0, option.min_sources - chosen)
            if chosen < option.min_sources else "点击「确认使用」结算",
            "已选择 %d / %d 张" % (chosen, option.min_sources),
            theme.TARGET_BLUE,
        )

    skill_input = game.pending_skill_input
    if skill_input is not None:
        need = []
        if skill_input["needs_target"] and skill_input["target"] is None:
            need.append("点击角色面板选择目标")
        if len(skill_input["cards"]) < skill_input["cost_cards"]:
            need.append("点击手牌选择要弃置的牌")
        body = "，".join(need) if need else "点击「确认发动」结算"
        if skill_input["cost_cards"]:
            progress = "已选 %d/%d 张" % (
                len(skill_input["cards"]), skill_input["cost_cards"])
            if skill_input["needs_target"]:
                target_name = (
                    skill_input["target"].name
                    if skill_input["target"] is not None else "未选"
                )
                progress = "目标：" + target_name + "　" + progress
        elif skill_input["needs_target"] and skill_input["target"] is not None:
            progress = "目标：" + skill_input["target"].name
        else:
            progress = ""
        return PromptInfo(
            "skill",
            "发动【" + skill_input["name"] + "】",
            body,
            progress,
            theme.TARGET_BLUE,
        )

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
        origin = _conversion_origin(target_selection)
        return PromptInfo(
            "target",
            title,
            (origin + "；" if origin else "") + "点击角色面板选择，点击「确认目标」结算",
            "已选择 %d / %d" % (selected, maximum),
            theme.TARGET_YELLOW,
        )

    if game.choice.active:
        request = game.choice.current
        return PromptInfo("choice", request.title, request.prompt, accent=theme.GOLD_BRIGHT)

    if game.response.active:
        # 这块面板只有在"确实轮到我回答"时才会被建起来（本地真人由
        # HumanController 建，联网客户端由自己那条决策请求建），所以这里不是
        # "谁在响应"，而是"我现在可以做什么"。
        current = game.response.current
        allowed = sorted(current.allowed_cards or ())
        hint = (
            "点击手牌中的【" + "】【".join(card_label(name) for name in allowed) + "】"
            if allowed else "使用合法响应牌，或点击「不出」"
        )
        body = str(current.prompt or "").strip() or hint
        return PromptInfo("response", _response_title(current), body, hint, theme.DANGER)

    # 共享响应阶段（无懈）等待提示：客户端看房主给的"我自己的状态"，
    # 单机看自己引擎里那条待回答请求的成员状态。
    window = getattr(game, "response_window", None)
    if window:
        return _window_prompt(window, game)

    request = getattr(game, "pending_request", None)
    if request is not None:
        return _waiting_prompt(game, request)

    if local_can_play(game):
        return PromptInfo("play", "出牌阶段", "点击手牌使用，或点击「结束回合」", accent=theme.GOLD_BRIGHT)
    if game.phase == "discard" and game.current_turn_player is game.player:
        need = max(0, len(game.player.hand) - game.player.hp)
        return PromptInfo("discard", "弃牌阶段", "请弃置 %d 张手牌" % need, accent=theme.DANGER)

    current = game.current_turn_player.name if game.current_turn_player else "-"
    phase_label = PHASE_LABELS.get(game.phase, game.phase)
    return PromptInfo("info", current + " · " + phase_label, game.message, accent=theme.TEXT_DIM)


#: 这些牌名在提示里的写法（响应标题用）。
def _response_title(current):
    if getattr(current, "reason", "") == "wuxie_chain":
        return "使用【无懈可击】／不出"
    return "需要你的响应"


def _window_prompt(window, game):
    """共享无懈阶段里"我"该看到的文案（客户端路径）。"""

    status = str((window or {}).get("status") or "")
    window_id = int((window or {}).get("window_id") or 0)
    round_id = int((window or {}).get("round_id") or 0)
    body = str(getattr(game, "message", "") or "【无懈可击】阶段进行中。")
    if status == "passed":
        return PromptInfo(
            "waiting", "已放弃本轮，等待其他玩家响应",
            body, "窗口 %d · 第 %d 轮" % (window_id, round_id) if window_id else "",
            theme.TEXT_DIM)
    if status == "pending":
        # 我有资格但目前手里没有可点的面板：回答已经发出去了，等房主确认。
        return PromptInfo("waiting", "已提交，等待房主确认…", body, "", theme.TEXT_DIM)
    return PromptInfo("waiting", "等待其他玩家响应", body, "", theme.TEXT_DIM)


def _waiting_prompt(game, request):
    """一条"不是我在回答（或者我已经放弃）"的待回答请求。

    判据只有两条：**本机玩家是这条请求的谁**、以及他**现在还轮不轮得到回答**。
    控制器类型（``is_human``）不能用来判断——它说的是"这台机器上坐的真人是谁"，
    既可能是别人，也可能在本机根本不参与这次响应。
    """

    me = getattr(game, "player", None)
    prompt = str(getattr(request, "prompt", "") or "")
    is_group = bool(getattr(request, "is_group", False))
    if is_group and me is not None:
        status = request.member_status(me)
        if status == "pending":
            # 轮得到我，但界面面板还没建起来（极短的一帧）：按"需要响应"显示，
            # 不能给一块空白正文。
            return PromptInfo("response", "需要你的响应", prompt or "请做出响应",
                              accent=theme.DANGER)
        if status == "passed":
            return PromptInfo("waiting", "已放弃本轮，等待其他玩家响应", prompt,
                              accent=theme.TEXT_DIM)
        return PromptInfo("waiting", "等待其他玩家响应", prompt, accent=theme.TEXT_DIM)
    if not is_group and request.target is me:
        return PromptInfo("response", "需要你的响应", prompt or "请做出响应",
                          accent=theme.DANGER)
    owner = getattr(getattr(request, "target", None), "name", "?")
    return PromptInfo("waiting", "等待 " + str(owner) + " 响应", prompt, accent=theme.TEXT_DIM)


def _result_display(name):
    from src.card import display_name_for

    return display_name_for(name)


def _conversion_origin(selection):
    """目标选择阶段标出来源技能（例如【龙胆】将 ♦7【闪】当【杀】使用）。"""

    action = (selection.get("metadata") or {}).get("card_action")
    if action is None or not getattr(action, "is_conversion", False):
        return ""
    return "【%s】将%s当【%s】" % (
        action.skill_name or action.skill_id,
        action.describe_sources(),
        action.result_display,
    )


def draw(surface, info, metrics, rect=None):
    """分层显示：标题 / 说明 / 进度各占一行，不再挤在同一行。"""

    rect = pygame.Rect(rect or metrics.prompt)
    if info is None:
        return rect

    draw_panel(
        surface,
        rect,
        fill=theme.PANEL_DEEP,
        border=info.accent if info.kind != "info" else theme.GOLD_DIM,
        border_width=2 if info.kind == "info" else theme.BORDER,
        radius=theme.RADIUS_PANEL,
    )

    fonts = metrics.fonts
    pad = metrics.px(20)

    title_font = fonts.get("normal")
    title = title_font.render(info.title, True, info.accent)
    surface.blit(title, (rect.x + pad, rect.y + metrics.px(18)))

    body_font = fonts.get("small")
    body_width = rect.width - pad * 2
    if info.progress:
        progress_font = fonts.get("small")
        progress_surface = progress_font.render(info.progress, True, theme.TARGET_YELLOW)
        body_width -= progress_surface.get_width() + metrics.px(24)
        surface.blit(
            progress_surface,
            progress_surface.get_rect(midright=(rect.right - pad, rect.y + metrics.px(54))),
        )

    body_text = ellipsize_text(info.body, body_font, max(1, body_width))
    body = body_font.render(body_text, True, theme.TEXT)
    surface.blit(body, (rect.x + pad, rect.y + metrics.px(50)))

    return rect
