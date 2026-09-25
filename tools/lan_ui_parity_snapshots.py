"""Phase 11.4 视觉一致性：同一种"出牌阶段"在单机 / LAN 房主 / LAN 客户端下的对照图。

验收原则（§20）：**同一种游戏状态**，三边的桌面结构、提示条、按钮、手牌区、
座位面板应当是同一套；玩家不该只看界面就分辨出"这是单机还是联机"。

    python tools/lan_ui_parity_snapshots.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame                                              # noqa: E402

from lan_view_harness import (                             # noqa: E402
    FRAME,
    MatchSession,
    check,
    force_hand,
    start_remote_turn,
    summary,
    wait_for,
)

from src.game import Game                                   # noqa: E402
from src.renderer import Renderer                           # noqa: E402
from src.ui import prompt as prompt_module                  # noqa: E402
from tests.legacy_helpers import canonical_card, equipment, tao, shan  # noqa: E402

SIZE = (1600, 1000)
FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_snapshots")


def save(screen, name):
    os.makedirs(FOLDER, exist_ok=True)
    path = os.path.join(FOLDER, "phase11_4_" + name + ".png")
    pygame.image.save(screen, path)
    return path


def build_local():
    """单机对照局：出牌阶段、手里有几张典型牌。"""

    game = Game(ai_count=1)
    game.start_single_player()
    game.actions.clear()
    game.player.hand = [canonical_card("SHA"), canonical_card("SHA"), tao(),
                        shan(), canonical_card("GUOHE"), canonical_card("WUXIE")]
    game.player.hp = 3
    game.players[1].hp = 2
    game.players[1].chained = True
    game.players[1].set_equipment(equipment("QINGLONG"))
    game.players[1].set_equipment(equipment("BAGUA"))
    for player in game.players[1:]:
        player.hand = [canonical_card("SHAN") for _ in range(3)]
    game.phase = "play"
    game.current_turn_player = game.player
    game.message = "本地自由混战开始：2 人"
    return game


def main():
    print("=" * 60)
    print("Phase 11.4 视觉一致性对照（单机 / 房主 / 客户端）")
    print("=" * 60)

    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode(SIZE)
    renderer = Renderer(screen)

    # ---- 1) 单机：自己的出牌阶段 ----
    local = build_local()
    for _ in range(6):
        renderer.update(FRAME)
        renderer.draw(local)
    save(screen, "parity1_local_play_phase")
    local_info = prompt_module.describe(local)
    local_actions = renderer.actions_for(local)
    check("单机对照局处于出牌阶段", local_info.title == "出牌阶段", local_info.title)

    # ---- 2) 联机：房主的出牌阶段 + 客户端的出牌阶段 ----
    with MatchSession(client_count=1) as session:
        host, pump = session.host, session.pump
        game = host.game
        remote = session.remote()
        session.fix_hands(["SHA", "SHAN"], keep=remote)
        force_hand(game, game.player, ["SHA", "SHA", "TAO", "GUOHE"])
        force_hand(game, remote, ["SHA", "SHAN", "TAO"])
        game.actions.clear()
        game.phase = "play"
        game.current_turn_player = game.player
        game.message = "联网对局开始"
        screen = pygame.display.set_mode(SIZE)
        host_renderer = Renderer(screen)
        for _ in range(6):
            host_renderer.update(FRAME)
            host_renderer.draw(game)
        save(screen, "parity2_host_play_phase")
        host_info = prompt_module.describe(game)
        check("房主的提示与单机同构（同一份 prompt.describe）",
              host_info.title in ("出牌阶段", "请选择卡牌", "等待 玩家甲 响应"),
              host_info.title)

        # 客户端：轮到远程玩家出牌
        start_remote_turn(host, pump)
        client = session.client_of(remote)
        for _ in range(30):
            client.update_frame(FRAME)
            pump(0.05)
            if client.match.decision is not None:
                break
        client.snapshot("parity3_client_play_phase", prefix="phase11_4_")
        ui_labels = client.scene
        from lan_playability_sync import UiClient
        labels = UiClient(client).labels()
        check("客户端的提示与单机逐字一致",
              labels["prompt"][:2] == (local_info.title, local_info.body),
              "%s vs %s" % (str(labels["prompt"][:2]),
                            str((local_info.title, local_info.body))))
        check("客户端的固定按钮与单机同构",
              labels["primary"][0] in ("结束回合", "请选择卡牌", "确认目标",
                                       "确认使用", "确认发动"),
              str(labels["primary"]))
        check("客户端仍然没有本地规则引擎（只读视图）",
              not getattr(client.scene.view, "local_interaction", True))
        check("客户端画的是同一套交互层",
              bool(getattr(client.scene.view, "interaction_layers", False)))

    print("\n对照图：")
    for name in ("parity1_local_play_phase", "parity2_host_play_phase",
                 "parity3_client_play_phase"):
        print("  tools/ui_snapshots/phase11_4_%s.png" % name)
    return summary("Phase 11.4 视觉一致性")


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
