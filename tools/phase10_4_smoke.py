"""Phase 10.4 smoke：五谷真实卡面 / 南蛮与万箭响应链 / 主菜单人数控件。

用法：``python tools/phase10_4_smoke.py``（SDL 使用 dummy 驱动，不弹窗口）。
截图写到 ``tools/ui_snapshots/``，同时打印每一项的 PASS / FAIL。

只做**少量** 4 / 5 / 8 人对局，确认不卡死；不做大规模随机跑局。
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import EventType, UseCardAction
from src.renderer import Renderer
from src.start_menu import StartMenu, centered_text_origin
from src.ui import assets as assets_module
from src.ui import cards as cards_module
from src.ui import layout, table, theme
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import canonical_card, set_draw_order, shan, normal_sha, tao


OUTDIR = os.path.join(os.path.dirname(__file__), "ui_snapshots")
RESOLUTION = (1920, 1080)

RESULTS = []


def check(label, passed, detail=""):
    RESULTS.append((label, bool(passed), detail))
    print("%-4s %s%s" % ("PASS" if passed else "FAIL", label,
                         ("  — " + detail) if detail else ""))
    return passed


def trick(name):
    return canonical_card(name)


def build_game(ai_count, hand=(), hand_size=None):
    game = Game(ai_count=ai_count)
    game.scene = "game"
    game.actions.clear()
    game.engine.reset()
    for player in game.players:
        player.hand = []
        player.hp = player.max_hp
        player.alive = True
    game.phase = "play"
    game.current_turn_player = game.player
    set_draw_order(game, [shan() for _ in range(60)])
    if hand_size:
        hand = list(hand) + [trick("TAO")] * (hand_size - len(hand))
    game.player.hand = list(hand)
    return game


def render(renderer, game, frames=2, mouse=None):
    for _ in range(frames):
        game.update(1 / 60)
        renderer.update(1 / 60)
        renderer.draw(game, mouse)
    pygame.display.flip()


def save(renderer, name):
    path = os.path.join(OUTDIR, name + ".png")
    pygame.image.save(renderer.screen, path)
    return path


# ==================================================
# Smoke 1：五谷丰登
# ==================================================

def smoke_wugu(renderer):
    game = build_game(4, hand=[trick("WUGU")])
    actor = game.player
    targets = game.seats.alive_players_in_order(start_after=actor, include_start=True)
    game.submit_action(UseCardAction(actor, actor.hand[0], targets))
    render(renderer, game, frames=24)

    entries = renderer.get_pool_entries(game)
    rects = renderer.get_public_card_rects([card for card, _key in entries])
    check("五谷：公共牌池已生成", bool(entries) and len(rects) == len(entries),
          "%d 张公共牌" % len(entries))

    registry = assets_module.get_registry()
    art_ok = all(
        cards_module.card_art(card, pygame.Rect(rect), registry) is not None
        for card, rect in zip([c for c, _ in entries], rects))
    check("五谷：每张公共牌都有真实卡面素材", art_ok)

    first = pygame.Rect(rects[0])
    check("五谷：卡面按竖版比例且高于素材最小绘制线",
          first.width >= cards_module.ART_MIN_WIDTH
          and first.height >= cards_module.ART_MIN_HEIGHT
          and abs(first.width / float(first.height) - layout.PUBLIC_POOL_ASPECT) < 0.02,
          "%dx%d" % (first.width, first.height))

    # 真实卡面路径：与强制紧凑（纯文字）排版必须画出不同的像素。
    body = pygame.Rect(0, 0, first.width, first.height)
    art_surface = pygame.Surface(first.size)
    cards_module.draw_card(art_surface, entries[0][0], body, renderer.metrics.fonts,
                           compact=False)
    compact_surface = pygame.Surface(first.size)
    cards_module.draw_card(compact_surface, entries[0][0], body, renderer.metrics.fonts,
                           compact=True)
    different = pygame.image.tobytes(art_surface, "RGB") != pygame.image.tobytes(compact_surface, "RGB")
    check("五谷：公共牌画的是真实卡面而不是纯文字卡", different)

    # hover：鼠标停在第一张上，绘制矩形必须放大并上浮。
    hovered = table.pool_hover_index(rects, first.center)
    lifted = table.pool_hover_rect(first, renderer.metrics)
    check("五谷：hover 命中当前牌并放大上浮",
          hovered == 0 and lifted.width > first.width
          and lifted.height > first.height and lifted.bottom < first.bottom,
          "→ %dx%d" % (lifted.width, lifted.height))
    render(renderer, game, frames=2, mouse=first.center)
    save(renderer, "phase10_4_wugu_hover")

    # 点击选牌：公共池里少一张，手牌里多一张。
    target_card = entries[0][0]
    handle_game_click(first.center, game, renderer)
    render(renderer, game, frames=2)
    in_hand = any(card is target_card for card in game.player.hand)
    check("五谷：点击公共牌完成选择并移出公共池",
          in_hand and all(card is not target_card for card, _ in game.public_card_pool))
    save(renderer, "phase10_4_wugu_selected")
    return game


# ==================================================
# Smoke 2 / 3：南蛮入侵 / 万箭齐发
# ==================================================

def run_mass_trick(renderer, name, response_name, shot):
    game = build_game(4, hand=[trick(name)])
    game.player.hand = [trick(name)]
    for player in game.players[1:]:
        player.hand = [trick(response_name) for _ in range(2)]
    requests = []
    game.context.events.subscribe(
        EventType.PENDING_CREATED, lambda _ctx, event: requests.append(event.payload["request"]))

    targets = game.seats.alive_players_in_order(start_after=game.player)
    game.submit_action(UseCardAction(game.player, game.player.hand[0], targets))
    render(renderer, game, frames=6)
    save(renderer, shot)

    reasons = [(getattr(item, "context", {}) or {}).get("reason") for item in requests]
    wuxie = [index for index, reason in enumerate(reasons) if reason == "wuxie_chain"]
    response = [index for index, reason in enumerate(reasons) if reason == name.lower()]

    check("%s：开了一次【无懈可击】窗口" % name, bool(wuxie) and wuxie == list(range(len(wuxie))),
          "无懈请求 %d 个" % len(wuxie))
    check("%s：逐目标响应请求都在无懈窗口之后" % name,
          bool(response) and min(response) > max(wuxie))
    check("%s：【无懈可击】不再夹在逐目标响应之间" % name,
          all(index > max(wuxie) for index in response))
    allowed = [
        sorted(item.allowed_cards or [])
        for item in requests
        if (getattr(item, "context", {}) or {}).get("reason") == name.lower()
    ]
    check("%s：逐目标响应只允许【%s】" % (name, response_name),
          all(names == [response_name] for names in allowed), str(allowed))
    return game


def smoke_nanman(renderer):
    return run_mass_trick(renderer, "NANMAN", "SHA", "phase10_4_nanman")


def smoke_wanjian(renderer):
    return run_mass_trick(renderer, "WANJIAN", "SHAN", "phase10_4_wanjian")


# ==================================================
# Smoke 4：主菜单人数控件
# ==================================================

def smoke_menu(renderer):
    menu = StartMenu(renderer.screen)
    menu.sync_layout(renderer.metrics)
    game = Game(ai_count=4)
    game.mode_id = "ffa"
    game.ai_count = 4
    game.scene = "menu"

    for mode_id, limits in (("ffa", (2, 8)), ("identity", (5, 8))):
        game.set_mode(mode_id)
        allowed = game.allowed_player_counts()
        check("菜单：%s 人数范围 %s" % (mode_id, limits),
              (allowed[0], allowed[-1]) == limits, str((allowed[0], allowed[-1])))

        counts = []
        for count in range(limits[0], limits[1] + 1):
            game.ai_count = count - 1
            rendered = renderer.metrics.fonts.get("menu_count").render(
                str(game.total_players()), True, theme.GOLD_BRIGHT)
            origin = centered_text_origin(rendered, menu.value_rect)
            ink = rendered.get_bounding_rect().move(origin)
            counts.append((count, ink, origin))
            if mode_id == "ffa" and count == 8:
                render(renderer, game, frames=1)
                menu.draw(game, renderer.metrics)
                pygame.display.flip()
                save(renderer, "phase10_4_menu_ffa_8")
        check("菜单：%s 数字都居中且不越框" % mode_id,
              all(menu.value_rect.contains(ink)
                  and abs(ink.centerx - menu.value_rect.centerx) <= 1
                  and abs(ink.centery - menu.value_rect.centery) <= 1
                  for _count, ink, _origin in counts))

    button_label = renderer.metrics.fonts.get("large").render("开始游戏", True, theme.TEXT)
    mode_label = renderer.metrics.fonts.get("normal").render("自由混战", True, theme.TEXT)
    number = renderer.metrics.fonts.get("menu_count").render("8", True, theme.GOLD_BRIGHT)
    check("菜单：人数数字明显小于主要按钮文字",
          number.get_width() < button_label.get_width()
          and number.get_height() < button_label.get_height()
          and number.get_height() < mode_label.get_height(),
          "数字 %dx%d / 开始游戏 %dx%d" % (
              number.get_width(), number.get_height(),
              button_label.get_width(), button_label.get_height()))

    game.set_mode("identity")
    game.ai_count = 7
    render(renderer, game, frames=1)
    menu.draw(game, renderer.metrics)
    pygame.display.flip()
    save(renderer, "phase10_4_menu_identity_8")

    # 分辨率适配：1280×720 ～ 2560×1440 都不越框。
    ok = True
    for width, height in ((1280, 720), (1366, 768), (1600, 900), (2560, 1440)):
        screen = pygame.display.set_mode((width, height))
        local = Renderer(screen)
        local_menu = StartMenu(screen)
        local_menu.sync_layout(local.metrics)
        local_game = Game(ai_count=7)
        local_game.ai_count = 7
        for count in range(5, 9):
            local_game.ai_count = count - 1
            rendered = local.metrics.fonts.get("menu_count").render(
                str(local_game.total_players()), True, theme.GOLD_BRIGHT)
            ink = rendered.get_bounding_rect().move(
                centered_text_origin(rendered, local_menu.value_rect))
            if not local_menu.value_rect.contains(ink):
                ok = False
    pygame.display.set_mode(RESOLUTION)
    # 分辨率探测换了 display surface：重建一个 Renderer，让后续复用同一尺寸。
    Renderer(pygame.display.get_surface())
    check("菜单：1280×720 ～ 2560×1440 数字都不越框", ok)


# ==================================================
# 少量对局：确认不卡死
# ==================================================

def smoke_games():
    from tools.multiplayer_smoke import run_smoke

    for ai_count in (3, 4, 7):
        _game, result = run_smoke(ai_count=ai_count, verbose=False)
        check("%d 人自由混战不卡死" % (ai_count + 1),
              result.stuck_reason is None and result.error is None,
              "回合=%s 日志=%s" % (result.turns, len(result.log)))


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    pygame.init()
    screen = pygame.display.set_mode(RESOLUTION)
    renderer = Renderer(screen)

    print("=== Smoke 1：五谷丰登 ===")
    smoke_wugu(renderer)
    print("=== Smoke 2：南蛮入侵 ===")
    smoke_nanman(renderer)
    print("=== Smoke 3：万箭齐发 ===")
    smoke_wanjian(renderer)
    print("=== Smoke 4：主菜单 ===")
    smoke_menu(renderer)
    print("=== 少量对局 ===")
    smoke_games()

    pygame.quit()
    failed = [label for label, passed, _detail in RESULTS if not passed]
    print("\nPhase 10.4 smoke：%d 项，%d 项通过" % (len(RESULTS), len(RESULTS) - len(failed)))
    if failed:
        print("未通过：")
        for label in failed:
            print("  - " + label)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
