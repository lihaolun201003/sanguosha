import pygame

from src.choice import ChoiceOverlay

from src.constants import (
    ENEMY_EQUIPMENT_RECTS,
    FPS,
    HEIGHT,
    PLAYER_EQUIPMENT_RECTS,
    WIDTH,
    MAIN_MENU_RECT,
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
        # 游戏结束
        # ==================================================

        if (
            game.game_over
            and not game.busy
        ):

            restart_rect = (
                renderer.get_restart_rect()
            )


            if restart_rect.collidepoint(
                event.pos
            ):

                game.reset()

            elif pygame.Rect(*MAIN_MENU_RECT).collidepoint(event.pos):

                game.return_to_menu()


            continue


        # ==================================================
        # 动画期间不能操作
        # ==================================================

        if game.busy:

            continue


        # ==================================================
        # 通用二选一
        # ==================================================

        if game.choice.active:

            choice_overlay.handle_click(
                event.pos,
                game.choice
            )

            continue


        # ==================================================
        # 响应阶段
        # ==================================================

        if game.response.active:

            pass_rect = (
                renderer
                .get_pass_response_rect()
            )


            if pass_rect.collidepoint(
                event.pos
            ):

                game.pass_response()

                continue


            card_index = (
                renderer.card_at_position(
                    event.pos,
                    game.player.hand
                )
            )


            if card_index is None:

                continue


            rects = (
                renderer.get_card_rects(
                    game.player.hand
                )
            )


            source_rect = tuple(
                rects[card_index]
            )


            game.respond_with_card(
                card_index,
                source_rect
            )


            continue


        # ==================================================
        # 武器效果的具体选牌阶段
        # ==================================================

        if game.pending_selection is not None:

            zone = game.pending_selection["zone"]

            if zone == "public_pool":
                for card, rect in zip(game.public_card_pool, renderer.get_public_card_rects(game.public_card_pool)):
                    if rect.collidepoint(event.pos):
                        game.select_pending_card(card, tuple(rect))
                        break
                continue

            if zone == "player_hand":

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

            elif zone in ("enemy_hand", "enemy_cards"):

                card_index = renderer.enemy_card_at_position(
                    event.pos,
                    game.enemy.hand
                )

                if card_index is not None:

                    rects = renderer.get_enemy_hand_rects(
                        game.enemy.hand
                    )

                    game.select_pending_card(
                        game.enemy.hand[card_index],
                        tuple(rects[card_index])
                    )

                    continue

                if zone == "enemy_cards":
                    for slot, rect_data in ENEMY_EQUIPMENT_RECTS.items():
                        rect = pygame.Rect(*rect_data)
                        if not rect.collidepoint(event.pos):
                            continue
                        card = game.enemy.get_equipment(slot)
                        if card is not None:
                            game.select_pending_card(card, tuple(rect), key=slot)
                        break

            elif zone == "enemy_equipment":

                for slot, rect_data in (
                    ENEMY_EQUIPMENT_RECTS.items()
                ):

                    rect = pygame.Rect(
                        *rect_data
                    )

                    if not rect.collidepoint(event.pos):
                        continue

                    card = game.enemy.get_equipment(slot)

                    if card is not None:
                        game.select_pending_card(
                            card,
                            tuple(rect),
                            key=slot
                        )

                    break

            continue


        # ==================================================
        # 多人角色目标选择
        # ==================================================
        if game.pending_target_selection is not None:
            if renderer.get_end_turn_rect().collidepoint(event.pos):
                game.confirm_target_selection()
                continue
            target = renderer.player_at_position(event.pos, game)
            if target is not None:
                game.toggle_target_selection(target)
            continue


        # ==================================================
        # 丈八蛇矛选牌：点击手牌选择，点击武器取消
        # ==================================================

        weapon = game.player.get_equipment(
            "weapon"
        )

        weapon_rect = pygame.Rect(
            *PLAYER_EQUIPMENT_RECTS["weapon"]
        )

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
        # 结束回合
        # ==================================================

        end_rect = (
            renderer.get_end_turn_rect()
        )


        if end_rect.collidepoint(
            event.pos
        ):

            game.end_player_turn()

            continue


        # ==================================================
        # 点击手牌
        # ==================================================

        card_index = (
            renderer.card_at_position(
                event.pos,
                game.player.hand
            )
        )


        if card_index is None:

            continue


        rects = (
            renderer.get_card_rects(
                game.player.hand
            )
        )


        source_rect = tuple(
            rects[card_index]
        )


        if game.phase == "play":

            game.player_use_card(
                card_index,
                source_rect
            )


        elif game.phase == "discard":

            game.player_discard(
                card_index,
                source_rect
            )


    # ==================================================
    # 更新
    # ==================================================

    game.update(dt)


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
