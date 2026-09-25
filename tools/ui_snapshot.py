"""Render representative UI states to PNG for visual inspection.

Run with ``python tools/ui_snapshot.py [outdir]``.  SDL uses the dummy driver,
so no window opens.
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.choice import ChoiceOverlay
from src.game import Game
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui import layout

# 视觉验收尺寸（可通过命令行覆盖）
DEFAULT_SIZE = (1920, 1080)
from tests.legacy_helpers import canonical_card, equipment, normal_sha, set_draw_order, shan, tao


def make_screen(size=DEFAULT_SIZE):
    pygame.init()
    return pygame.display.set_mode(size)


def build_game(ai_count, hand_size=4, seed=3):
    import random

    random.seed(seed)
    game = Game(ai_count=ai_count)
    game.start_single_player()
    game.actions.clear()
    deck_cards = []
    for _ in range(hand_size):
        deck_cards.append(canonical_card("SHA"))
    hand = [canonical_card("SHA") for _ in range(2)]
    hand.append(tao())
    hand.append(shan())
    hand.append(canonical_card("GUOHE"))
    hand.append(canonical_card("WUXIE"))
    while len(hand) < hand_size:
        hand.append(canonical_card("NANMAN"))
    game.player.hand = hand[:hand_size]
    game.player.hp = 3
    game.players[1].hp = 2
    game.players[1].chained = True
    game.players[1].set_equipment(equipment("QINGLONG"))
    game.players[1].set_equipment(equipment("BAGUA"))
    game.players[1].judgement_zone.append(canonical_card("LEBU"))
    if len(game.players) > 2:
        game.players[2].set_equipment(equipment("CHITU"))
        game.players[2].set_equipment(equipment("JUEYING"))
        game.players[2].judgement_zone.append(canonical_card("SHANDIAN"))
    if len(game.players) > 3:
        game.players[3].alive = False
        game.players[3].hp = 0
    for player in game.players[1:]:
        player.hand = [canonical_card("SHAN") for _ in range(3)]
    set_draw_order(game, [canonical_card("SHA") for _ in range(20)])
    return game


def snapshot(name, game, renderer, outdir, *, frames=30):
    screen = renderer.screen
    for _ in range(frames):
        game.update(1 / 60)
        renderer.update(1 / 60)
        renderer.draw(game)
    path = os.path.join(outdir, name + ".png")
    pygame.image.save(screen, path)
    return path


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "tools/ui_snapshots"
    size = DEFAULT_SIZE
    if len(sys.argv) > 3:
        size = (int(sys.argv[2]), int(sys.argv[3]))
    os.makedirs(outdir, exist_ok=True)
    screen = make_screen(size)
    renderer = Renderer(screen)
    produced = []

    # 开始菜单
    menu = StartMenu(screen)
    menu.sync_layout(renderer.metrics)
    menu_game = Game(ai_count=4)
    menu_game.ai_count = 4
    menu.draw(menu_game, renderer.metrics)
    path = os.path.join(outdir, "00_menu.png")
    pygame.image.save(screen, path)
    produced.append(path)

    # 2 / 5 / 8 人
    for ai_count in (1, 4, 7):
        game = build_game(ai_count)
        produced.append(snapshot("table_%02d_players" % (ai_count + 1), game, renderer, outdir))

    # 大量手牌 + 目标选择
    game = build_game(4, hand_size=18)
    game.phase = "play"
    game.current_turn_player = game.player
    game.player_use_card(0, tuple(renderer.get_card_rects(game.player.hand)[0]))
    produced.append(snapshot("hand_18_with_targeting", game, renderer, outdir))

    # 响应 Pending（出闪）
    game = build_game(3)
    game.response.request(
        prompt="【杀】：请打出一张【闪】",
        allowed_cards={"SHAN"},
        on_card=lambda *args, **kwargs: None,
        on_pass=lambda: None,
    )
    produced.append(snapshot("prompt_response", game, renderer, outdir))

    # 五谷公共牌
    game = build_game(3)
    game.public_card_pool = [tao(), normal_sha(), shan(), canonical_card("LEBU")]
    for index, card in enumerate(game.public_card_pool):
        game.start_card_selection(
            zone="public_pool",
            candidates=[(item, None) for item in game.public_card_pool],
            number=1,
            prompt="【五谷丰登】：请选择一张公共牌",
            on_complete=lambda selected: None,
            owner=game.player,
        )
        break
    produced.append(snapshot("public_pool", game, renderer, outdir))

    # 二选一
    game = build_game(2)
    game.choice.request(title="铁索连环", prompt="选择使用【铁索连环】或重铸摸一张牌",
                        yes_label="连环", no_label="重铸", on_yes=lambda: None, on_no=lambda: None)
    overlay = ChoiceOverlay(screen)
    overlay.sync_layout(renderer.metrics)
    renderer.draw(game)
    overlay.draw(game.choice, renderer.metrics)
    path = os.path.join(outdir, "choice_modal.png")
    pygame.image.save(screen, path)
    produced.append(path)

    # 结算
    game = build_game(3)
    game.game_over = True
    game.message = "你已阵亡 / 游戏失败"
    game.add_log("玩家 阵亡")
    produced.append(snapshot("game_over", game, renderer, outdir))

    for path in produced:
        print("wrote", path)
    pygame.quit()


if __name__ == "__main__":
    main()
