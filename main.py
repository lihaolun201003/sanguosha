import pygame

from src.choice import ChoiceOverlay

from src.constants import (
    FPS,
    HEIGHT,
    WIDTH,
)

from src.game import Game
from src.renderer import Renderer
from src.start_menu import StartMenu


pygame.init()


screen = pygame.display.set_mode(
    (
        WIDTH,
        HEIGHT
    )
)

pygame.display.set_caption(
    "三国杀"
)


clock = pygame.time.Clock()


game = Game()


renderer = Renderer(
    screen
)


choice_overlay = ChoiceOverlay(
    screen,
    renderer.small_font,
    renderer.tiny_font
)


start_menu = StartMenu(
    screen,
    renderer.big_font,
    renderer.small_font,
    renderer.tiny_font
)


def _public_index_at(position, renderer, cards):
    for index, rect in enumerate(renderer.get_public_card_rects(cards)):
        if rect.collidepoint(position):
            return index
    return None


def run_action(action):
    """UI 动作 → 既有 Game 入口，规则仍然只由引擎决定。"""

    if action == "end_turn":
        game.end_player_turn()
    elif action == "confirm_target":
        game.confirm_target_selection()
    elif action == "cancel_target":
        game.cancel_target_selection()
    elif action == "pass_response":
        game.pass_response()
    elif action == "choice_no":
        game.choice.choose_no()
    elif action == "confirm_zhangba":
        game.commit_player_zhangba()
    elif action == "cancel_zhangba":
        game.cancel_player_zhangba()
    elif action == "restart":
        renderer.reset_effects()
        game.reset()
    elif action == "menu":
        renderer.reset_effects()
        game.return_to_menu()


running = True


while running:

    dt = (
        clock.tick(FPS)
        / 1000.0
    )


    # ==================================================
    # 事件
    # ==================================================

    for event in pygame.event.get():

        if event.type == pygame.QUIT:

            running = False

            continue


        if (
            event.type
            != pygame.MOUSEBUTTONDOWN
        ):

            continue


        if event.button != 1:

            continue


        # ==================================================
        # 开始界面
        # ==================================================

        if game.scene == "menu":

            menu_action = start_menu.handle_click(
                event.pos,
                game
            )

            if menu_action == "exit":

                running = False

            continue


        # ==================================================
        # 二选一弹窗优先，出现时拦截其它点击
        # ==================================================

        if game.choice.active:

            choice_overlay.handle_click(
                event.pos,
                game.choice
            )

            continue


        # ==================================================
        # 固定操作按钮：结束回合 / 确认目标 / 不出 / 结算按钮
        # ==================================================

        action = renderer.hit_action(event.pos, game)

        if action is not None:
            run_action(action)
            continue


        if game.game_over or game.busy:

            continue


        # ==================================================
        # 选牌阶段（五谷公共牌 / 手牌 / 装备区）
        # ==================================================

        if game.pending_selection is not None:

            zone = game.pending_selection["zone"]

            if zone in ("public_pool", "selection_pool"):
                # 候选可能来自其它角色的装备区，必须把装备槽 key 一起回传给引擎，
                # 否则装备牌永远选不中。
                entries = renderer.get_pool_entries(game)
                cards = [card for card, _key in entries]
                index = _public_index_at(event.pos, renderer, cards)
                if index is not None:
                    rects = renderer.get_public_card_rects(cards)
                    game.select_pending_card(
                        entries[index][0],
                        tuple(rects[index]),
                        key=entries[index][1],
                    )
                continue

            if zone in ("hand", "player_hand"):

                card_index = renderer.card_at_position(
                    event.pos,
                    game.player.hand
                )

                if card_index is not None:

                    rects = renderer.get_card_rects(
                        game.player.hand
                    )

                    game.select_pending_card(
                        game.player.hand[card_index],
                        tuple(rects[card_index])
                    )

                continue

            if zone in ("player_equipment",):

                for slot, rect in renderer.player_equipment_slot_rects(game).items():

                    if not rect.collidepoint(event.pos):
                        continue

                    card = game.player.get_equipment(slot)

                    if card is not None:
                        game.select_pending_card(card, tuple(rect), key=slot)

                    break

            continue


        # ==================================================
        # 多人角色目标选择
        # ==================================================

        if game.pending_target_selection is not None:

            target = renderer.player_at_position(event.pos, game)

            if target is not None:
                game.toggle_target_selection(target)

            continue


        # ==================================================
        # 丈八蛇矛选牌：点击手牌选择，点击武器取消
        # ==================================================

        weapon = game.player.get_equipment("weapon")

        weapon_rect = renderer.player_equipment_slot_rects(game)["weapon"]

        if game.zhangba_selecting:

            if weapon_rect.collidepoint(event.pos):

                game.cancel_player_zhangba()
                continue

            card_index = renderer.card_at_position(
                event.pos,
                game.player.hand
            )

            if card_index is not None:

                rects = renderer.get_card_rects(
                    game.player.hand
                )

                game.toggle_player_zhangba_card(
                    card_index,
                    tuple(rects[card_index])
                )

            continue


        # ==================================================
        # 点击已装备的丈八蛇矛
        # ==================================================

        if (
            weapon is not None
            and weapon.name == "ZHANGBA"
            and weapon_rect.collidepoint(event.pos)
        ):

            game.try_player_zhangba()

            continue


        # ==================================================
        # 响应阶段：点击手牌打出响应牌
        # ==================================================

        card_index = renderer.card_at_position(
            event.pos,
            game.player.hand
        )

        if card_index is None:

            continue


        rects = renderer.get_card_rects(
            game.player.hand
        )

        source_rect = tuple(
            rects[card_index]
        )

        if game.response.active:

            game.respond_with_card(
                card_index,
                source_rect
            )

            continue


        # ==================================================
        # 点击手牌
        # ==================================================

        if game.phase == "play" and game.current_turn_player is game.player:

            game.player_use_card(
                card_index,
                source_rect
            )


        elif game.phase == "discard" and game.current_turn_player is game.player:

            game.player_discard(
                card_index,
                source_rect
            )


    # ==================================================
    # 更新
    # ==================================================

    game.update(dt)

    renderer.update(dt)


    # ==================================================
    # 绘制
    # ==================================================

    if game.scene == "menu":

        start_menu.draw(game)

    else:

        renderer.draw(game)


    # ==================================================
    # 二选一界面最后绘制
    #
    # 这样会覆盖在正常游戏界面最上层。
    # ==================================================

    choice_overlay.draw(
        game.choice
    )


    pygame.display.flip()


pygame.quit()
