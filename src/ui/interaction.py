"""一局游戏内的点击路由（Local 与 Remote 共用同一份）。

这里是从 ``main.py`` 抽出来的唯一一份点击分发逻辑：真实事件循环、单机牌桌、
联网客户端牌桌与 dummy 测试都调用同一个 ``handle_game_click``，因此"测试里
点得到"就等价于"真人点得到"，也不会出现两套路由各自演化的问题。

分两层，界限很清楚：

1. **命中测试**：鼠标落在哪张手牌 / 哪个角色 / 哪个装备槽 / 哪个公共牌位上
   （几何一律问 ``Renderer``，与绘制用的 rect 是同一批对象）；
2. **语义分支**：当前这一下点击在"选牌 / 选目标 / 发动技能 / 视为技 / 出牌 /
   弃牌 / 响应"里属于哪一种——判断依据全部是**同构的交互槽位**
   （``pending_view_as`` / ``pending_card_action`` / ``pending_skill_input`` /
   ``pending_selection`` / ``pending_target_selection`` / ``response`` / ``phase``），
   单机的权威 ``Game`` 与客户端的只读视图提供的是同一组字段。

末端的"真的去做"统一交给 ``HumanController``（见 ``ui.human_control``）：
单机把它转给本机权威 ``Game``，联网客户端把它转成 ``DecisionResponse`` 发给
房主。**点击路由本身一行都不区分 Local / Remote。**
"""

from src.game.equipment_skills.granted import granted_skill_id

from . import prompt
from .human_control import LocalHumanController


def _grants_current_view_as(game, card):
    """这张装备牌是不是"正在发动的那个视为技"的来源（丈八蛇矛一类）。"""

    skill_id = granted_skill_id(getattr(card, "name", None))
    session = game.pending_view_as
    return bool(skill_id) and session is not None and session.skill_id == skill_id


def _public_index_at(position, renderer, cards):
    for index, rect in enumerate(renderer.get_public_card_rects(cards)):
        if rect.collidepoint(position):
            return index
    return None


def _hand_rect(renderer, game, index):
    rects = renderer.get_card_rects(game.player.hand)
    if index < len(rects):
        return tuple(rects[index])
    return None


def _equipment_action_usable(game, actor, card):
    """装备区里的这张牌现在能不能作为素材：走共同动作查询。"""

    from src.game.available_actions import AvailableActions

    return AvailableActions(game).assemblable(actor, card)


def _card_action_context(game):
    """本机权威才有 Card Action Session；只读视图没有（返回 None）。"""

    query = getattr(game, "current_card_action_context", None)
    return query() if callable(query) else None


def _in_interactive_slot(game):
    """玩家现在是不是正处在一个"自己挑牌 / 挑目标"的交互槽位里。

    这些槽位里的点击属于**玩家自己的操作**（丈八蛇矛选来源牌、五谷选牌、
    技能选目标…），与动作队列此刻在播什么无关。
    """

    return any((
        game.pending_view_as is not None,
        game.pending_card_action is not None,
        game.pending_skill_input is not None,
        game.pending_selection is not None,
        game.pending_target_selection is not None,
    ))


def run_action(action, game, renderer, human=None):
    """UI 动作 → HumanController（默认是"本机权威"那一个）。"""

    human = human or LocalHumanController(game)
    return human.run_action(action, renderer)


def handle_game_click(position, game, renderer, human=None):
    """处理一次游戏内左键点击；返回是否被消费。

    ``human`` 缺省时按"本机就是权威"处理（单机 / 房主 / 旧调用点）。
    """

    human = human or LocalHumanController(game)

    # 判定优先：判定没走完（规则上没走完，或判定牌还在屏幕中央展示）时，
    # 判定面板就是牌桌最高层级——它捕获所有点击。唯一的例外是"这条判定
    # 请求问的正是本机玩家"（司马懿的改判窗口之类），那属于判定流程自己
    # 要求的输入，继续往下走正常的选择路径。
    gate = getattr(game, "judge_gate", None)
    if gate is not None and not gate.allows_local_input():
        # hover 之类的视觉效果保留，但这一下不产生任何 Gameplay Action。
        return False

    # 结算界面 / 节奏控件 / 技能选择面板 / 技能按钮 / 固定按钮
    action = renderer.hit_action(position, game)
    if action is not None:
        human.run_action(action, renderer)
        return True

    if game.game_over or (game.busy and not _in_interactive_slot(game)):
        # 动画 / 结算进行中时的乱点一律吞掉；但玩家**已经在**自己的交互槽位里
        # 挑牌时不能吞——上一名 AI 的动作队列余波（还在播的动画、等待条）会
        # 让 busy 一直是 True，点来源牌没反应，界面就永远停在"已选择 0/2"。
        return False

    # ---- 命中测试（只问几何，不看规则）----

    hand_index = renderer.card_at_position(position, game.player.hand)
    hand_card = (game.player.hand[hand_index]
                 if hand_index is not None and hand_index < len(game.player.hand)
                 else None)
    slot_rects = renderer.player_equipment_slot_rects(game)
    hit_slot = next((slot for slot, rect in slot_rects.items()
                     if rect.collidepoint(position)), None)
    hit_player = renderer.player_at_position(position, game)

    # View-As 选牌阶段：只允许点合法 source 牌，其余点击吞掉。
    if game.pending_view_as is not None:
        if hand_card is not None:
            human.toggle_card_source(hand_card, _hand_rect(renderer, game, hand_index))
        elif hit_slot is not None:
            card = game.player.get_equipment(hit_slot)
            if card is not None and _grants_current_view_as(game, card):
                # 再点一次发动它的那件装备 = 取消，与旧手感一致。
                human.run_action("view_as_cancel", renderer)
            elif card is not None:
                human.toggle_card_source(card, tuple(slot_rects[hit_slot]))
        return True

    # 多 source 转换的收集阶段：点手牌 / 装备牌继续选 source，
    # 确认与取消由固定按钮负责，其余点击一律吞掉。
    state = game.pending_card_action
    if state is not None and not state["picker"]:
        if hand_card is not None:
            human.toggle_card_source(hand_card, _hand_rect(renderer, game, hand_index))
        elif hit_slot is not None:
            card = game.player.get_equipment(hit_slot)
            if card is not None:
                human.toggle_card_source(card, tuple(slot_rects[hit_slot]))
        return True

    # 主动技能输入：点击角色选目标，点击手牌选费用牌；
    # 确认 / 取消由固定按钮负责，所以这里把其余点击都吃掉。
    state = game.pending_skill_input
    if state is not None:
        if state["cost_cards"] and hand_card is not None:
            human.select_skill_cost_card(
                hand_card, _hand_rect(renderer, game, hand_index))
            return True
        if hit_player is not None:
            human.toggle_target(hit_player)
        return True

    # 选牌阶段（五谷公共牌 / 手牌 / 装备区）
    if game.pending_selection is not None:
        zone = game.pending_selection["zone"]

        if zone in ("public_pool", "selection_pool"):
            # 候选可能来自其它角色的装备区，必须把装备槽 key 一起回传，
            # 否则装备牌永远选不中。
            entries = renderer.get_pool_entries(game)
            cards = [card for card, _key in entries]
            index = _public_index_at(position, renderer, cards)
            if index is not None:
                rects = renderer.get_public_card_rects(cards)
                human.select_card(entries[index][0], tuple(rects[index]),
                                  key=entries[index][1], zone=zone)
            return True

        if zone in ("hand", "player_hand"):
            if hand_card is not None:
                human.select_card(hand_card, _hand_rect(renderer, game, hand_index),
                                  zone=zone)
            return True

        if zone in ("player_equipment",):
            for slot, rect in slot_rects.items():
                if not rect.collidepoint(position):
                    continue
                card = game.player.get_equipment(slot)
                if card is not None:
                    human.select_card(card, tuple(rect), key=slot, zone=zone)
                break

        return True

    # 多人角色目标选择
    if game.pending_target_selection is not None:
        if hit_player is not None:
            human.toggle_target(hit_player)
        return True

    weapon = game.player.get_equipment("weapon")
    weapon_rect = slot_rects["weapon"]

    # 点击已装备的武器：进入它赋予的视为技（丈八蛇矛 = 两张手牌当【杀】）。
    # 单机与联机走同一个入口，区别只在 Controller（见 ui.human_control）。
    if (weapon is not None and granted_skill_id(getattr(weapon, "name", None))
            and weapon_rect.collidepoint(position)):
        human.try_zhangba()
        return True

    # 装备区的牌也可能被技能当作 source（是否允许由 Conversion 自己声明）
    context = _card_action_context(game)
    if context is not None:
        zones = game.card_actions.source_zones_in_use(context.actor)
        if "equipment" in zones and hit_slot is not None:
            card = game.player.get_equipment(hit_slot)
            if card is not None and _equipment_action_usable(
                    game, context.actor, card):
                human.begin_card_action(card, tuple(slot_rects[hit_slot]))
            return True

    if hand_card is None:
        return False

    source_rect = _hand_rect(renderer, game, hand_index)

    # 响应阶段：点击手牌打出响应牌
    if game.response.active:
        human.respond_with_card(hand_card, hand_index, source_rect)
        return True

    # 点击手牌出牌 / 弃牌：开放条件与提示、按钮完全一致——等待别人响应、
    # 等待房主结算、锦囊尚未结算时点手牌什么都不会发生。
    if prompt.local_can_play(game):
        human.use_card(hand_card, hand_index, source_rect)
        return True

    if game.phase == "discard" and game.current_turn_player is game.player:
        human.discard_card(hand_card, hand_index, source_rect)
        return True

    return False
