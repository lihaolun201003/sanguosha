"""把 1v1 测试的两块界面渲染成 1920×1080 的 PNG（离屏，不弹窗口）。

    SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/duel_snapshot.py

与 ``tools/ui_snapshot.py`` 同一套路：自己搭一套 Game + Renderer 离屏渲染，
用来出"看清楚"的大图（真实操作流程的验收截图由 ``tools/duel_capture.py``
挂在真实主循环上拍）。
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.modes.duel import config_of
from src.renderer import Renderer
from src.ui.duel_hud import DuelHud
from src.ui.duel_setup import DuelSetupScreen
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import canonical_card

SIZE = (1920, 1080)
OUTDIR = os.path.join("tools", "ui_snapshots")


def make_game(my="guanyu", enemy="lvbu", first="me", control="manual", seed="20240925"):
    game = Game()
    game.set_mode("duel_test")
    config = config_of(game)
    config.my_general = my
    config.enemy_general = enemy
    config.first = first
    config.control = control
    config.seed = seed
    game.begin_general_select()
    return game


def frames(game, renderer, count=1):
    for _ in range(count):
        game.update(1 / 60)
        renderer.update(1 / 60)
        renderer.draw(game)


def save(screen, name):
    path = os.path.join(OUTDIR, name + ".png")
    pygame.image.save(screen, path)
    print("saved", path, screen.get_size())
    return path


def shot_setup(screen, renderer, *, chosen):
    game = make_game()
    setup = DuelSetupScreen(screen)
    setup.sync_layout(renderer.metrics, game)
    setup.sync_cards(game)
    if chosen:
        setup.set_active_seat(0)
        setup.choose_general(game, game.generals.get("guanyu"))
        setup.set_active_seat(1)
        setup.choose_general(game, game.generals.get("lvbu"))
        setup.seed_field.set_text("20240925")
        setup.set_active_seat(0)
    setup.draw(game, renderer.metrics)
    return game, setup


def shot_battle(screen, renderer, *, request_window):
    game = make_game()
    hud = DuelHud(screen)
    hud.sync_layout(renderer.metrics)
    game.start_local_battle(1)
    renderer.begin_frame(game)
    frames(game, renderer, 120)

    if request_window:
        game.players[0].hand = [canonical_card("SHA")]
        game.players[1].hand = [canonical_card("SHAN")]
        rect = renderer.get_card_rects(game.player.hand)[0]
        handle_game_click(rect.center, game, renderer)
        frames(game, renderer, 40)

    renderer.draw(game)
    hud.draw(game, renderer.metrics)
    return game


def main():
    pygame.init()
    screen = pygame.display.set_mode(SIZE)
    renderer = Renderer(screen)
    metrics = renderer.metrics
    print("canvas", metrics.screen_w, metrics.screen_h)

    shot_setup(screen, renderer, chosen=False)
    save(screen, "duel_1_setup_empty")

    shot_setup(screen, renderer, chosen=True)
    save(screen, "duel_2_setup_chosen")

    game = shot_battle(screen, renderer, request_window=False)
    save(screen, "duel_3_battle")

    game = shot_battle(screen, renderer, request_window=True)
    save(screen, "duel_4_response")
    return 0


if __name__ == "__main__":
    sys.exit(main())
