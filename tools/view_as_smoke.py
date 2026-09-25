"""View-As 真人交互最短 smoke（两个确定性场景，无随机长局）。

    场景 1：赵云出牌阶段 → 发动技能 → 龙胆 → 点【闪】→ 选目标 → 确认 → 结算
    场景 2：请求【闪】→ 发动技能 → 龙胆 → 点【杀】→ 响应成功

运行：python tools/view_as_smoke.py
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.renderer import Renderer
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import normal_sha, shan, tao


def build(general):
    game = Game(ai_count=3)
    game.scene = "game"
    game.ai_pacing = True
    game.actions.clear()
    game.engine.reset()
    for player in game.players:
        player.hand = []
        player.hp = player.max_hp
        player.alive = True
    game.phase = "play"
    game.current_turn_player = game.player
    game.set_general(game.player, general)
    game.player.hp = game.player.max_hp
    return game


def main():
    pygame.init()
    screen = pygame.display.set_mode((1600, 900))
    renderer = Renderer(screen)
    steps = []

    def frame(count=1, mouse=None):
        for _ in range(count):
            game.update(1 / 60)
            renderer.update(1 / 60)
            renderer.draw(game, mouse)
            pygame.display.flip()

    def click(position):
        frame(1, position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}))
        for event in pygame.event.get():
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                handle_game_click(event.pos, game, renderer)
        frame(1, position)

    def settle(max_frames=400):
        for _ in range(max_frames):
            if not game.busy and game.pending_request is None:
                return True
            frame(1)
        return False

    # ---------- 场景 1：出牌阶段 龙胆 闪→杀 ----------
    game = build("zhaoyun")
    flash = shan()
    game.player.hand = [flash]
    victim = game.players[1]
    victim.hand = []
    frame(1)

    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("未点【龙胆】时点击【闪】不转换",
                  game.pending_target_selection is None
                  and game.pending_view_as is None
                  and flash in game.player.hand))

    click(renderer.skill_bar.button.rect.center)
    steps.append(("点击「发动技能」进入【龙胆】选牌模式",
                  game.pending_view_as is not None
                  and game.pending_view_as.skill_id == "longdan"))

    click(renderer.get_card_rects(game.player.hand)[0].center)
    selection = game.pending_target_selection
    steps.append(("选择【闪】后形成【杀】并进入目标选择",
                  selection is not None
                  and getattr(selection["card"], "_virtual", False)
                  and selection["card"].name == "SHA"))

    click(renderer.table_layout.seat_rects[victim].center)
    settle()
    steps.append(("确认目标后按【杀】结算",
                  flash in game.deck.discard_pile
                  and any("龙胆" in line for line in game.game_log)))

    # ---------- 场景 2：响应 龙胆 杀→闪 ----------
    game = build("zhaoyun")
    sha_card = normal_sha()
    game.player.hand = [sha_card]
    submitted = []
    game.response.request(
        prompt="【杀】：请打出一张【闪】",
        allowed_cards={"SHAN"},
        on_card=lambda index, card, rect: submitted.append(index),
        on_pass=lambda: submitted.append(None),
    )
    frame(1)

    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("未点【龙胆】时【杀】不能当【闪】响应", submitted == []))

    click(renderer.skill_bar.button.rect.center)
    steps.append(("响应阶段也能进入【龙胆】", game.pending_view_as is not None))

    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("选择【杀】后按【闪】完成响应", submitted == [0]))

    pygame.display.quit()

    ok = True
    for label, passed in steps:
        ok = ok and passed
        print("%-4s %s" % ("PASS" if passed else "FAIL", label))
    print("View-As smoke：", "通过" if ok else "失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
