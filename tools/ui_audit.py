"""UI geometry audit: report overlaps and out-of-bounds rects.

Runs the real layout for every supported resolution × player count and prints
a line per finding, so text/icon collisions and stray click areas can be found
without eyeballing screenshots.

    python tools/ui_audit.py
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.renderer import Renderer
from src.ui import layout
from src.ui.general_select import GeneralSelectScreen
from src.ui.action_picker import CardActionPicker
from src.ui.skill_bar import SkillBar, SkillPicker
from tests.legacy_helpers import canonical_card, equipment, normal_sha


RESOLUTIONS = (
    (1280, 720),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
    (2560, 1440),
)


def build_game(ai_count, *, hand_size=6, dressed=True):
    game = Game(ai_count=ai_count)
    game.start_single_player()
    game.actions.clear()
    for player in game.players:
        player.hand = []
    game.player.hand = [normal_sha() for _ in range(hand_size)]
    game.player.hp = game.player.max_hp
    game.current_turn_player = game.player
    game.phase = "play"
    if dressed:
        for index, player in enumerate(game.players[1:]):
            player.set_equipment(equipment("QINGLONG"))
            player.set_equipment(equipment("BAGUA"))
            player.set_equipment(equipment("CHITU"))
            player.set_equipment(equipment("JUEYING"))
            player.judgement_zone.append(canonical_card("LEBU"))
            player.chained = index % 2 == 0
            player.hand = [normal_sha() for _ in range(3 + index)]
    return game


def audit(game, renderer):
    metrics = renderer.metrics
    table = layout.TableLayout(game, metrics)
    screen = pygame.Rect(0, 0, metrics.screen_w, metrics.screen_h)
    findings = []

    seats = list(table.seat_rects.values())
    for rect in seats:
        if not screen.contains(rect):
            findings.append("座位越界 %s" % (rect,))
    for index, first in enumerate(seats):
        for second in seats[index + 1:]:
            if first.colliderect(second):
                findings.append("座位互相重叠")

    central = metrics.central
    prompt = metrics.prompt
    status = metrics.player_status
    hand = metrics.hand_area
    buttons = [metrics.primary_button, metrics.secondary_button]

    for rect in seats:
        if central.colliderect(rect):
            findings.append("座位压在中央区上")
        if prompt.colliderect(rect):
            findings.append("Prompt 压到座位")
    if prompt.colliderect(status):
        findings.append("Prompt 与真人状态条重叠")
    if status.colliderect(hand):
        findings.append("真人状态条与手牌区重叠")

    for lift in (layout.HOVER_LIFT, layout.SELECTED_LIFT):
        if status.colliderect(hand.move(0, -metrics.px(lift))):
            findings.append("手牌上浮 %d 后压到状态条" % lift)

    for button in buttons:
        if not screen.contains(button):
            findings.append("按钮越界")
        if button.colliderect(hand) or button.colliderect(prompt):
            findings.append("按钮与手牌或 Prompt 重叠")
        for seat in seats:
            if button.colliderect(seat):
                findings.append("按钮与座位重叠")

    if not screen.contains(metrics.speed_control):
        findings.append("节奏控件越界")
    for seat in seats:
        if metrics.speed_control.colliderect(seat):
            findings.append("节奏控件与座位重叠")

    # 组件内部：装备槽必须给图标与文字留出两段空间
    font = metrics.fonts.get("micro")
    for slot, rect in table.player_equipment_rects().items():
        text_space = rect.width - metrics.px(18) - metrics.px(6)
        if text_space < metrics.px(20):
            findings.append("装备槽 %s 文字空间不足" % slot)
        if font.size("青龙偃月刀")[0] > text_space and not rect.width:
            findings.append("装备槽 %s 放不下长名字" % slot)

    # 手牌与真人状态条之间要留出选中上浮的高度
    if metrics.hand_top() - status.bottom < metrics.px(layout.SELECTED_LIFT):
        findings.append("状态条与手牌间距不足以容纳上浮")

    return findings


def audit_extras(game, renderer):
    """选将界面与技能栏的几何检查（Phase 8 新增）。"""

    findings = []
    metrics = renderer.metrics
    screen = pygame.Rect(0, 0, metrics.screen_w, metrics.screen_h)

    picker = GeneralSelectScreen(renderer.screen)
    picker.sync_layout(metrics, game.generals.list_generals())
    for rect in picker.card_rects:
        if not screen.contains(rect):
            findings.append("选将卡片越界")
    for index, first in enumerate(picker.card_rects):
        for second in picker.card_rects[index + 1:]:
            if first.colliderect(second):
                findings.append("选将卡片重叠")
    for button in (picker.confirm_button, picker.random_button, picker.back_button):
        if not screen.contains(button.rect):
            findings.append("选将按钮越界")
        for rect in picker.card_rects:
            if button.rect.colliderect(rect):
                findings.append("选将按钮压到卡片")

    # ---- 技能按钮（ActionBar 第三键）----
    bar = SkillBar()
    bar.sync_layout(metrics)
    button_rect = bar.button.rect
    if not screen.contains(button_rect):
        findings.append("技能按钮越界")
    if button_rect.colliderect(metrics.hand_area):
        findings.append("技能按钮与手牌区重叠")
    if button_rect.colliderect(metrics.prompt):
        findings.append("技能按钮与 Prompt 重叠")
    if button_rect.colliderect(metrics.primary_button):
        findings.append("技能按钮与主按钮重叠")
    if button_rect.colliderect(metrics.secondary_button):
        findings.append("技能按钮与次按钮重叠")

    # ---- Card Action Picker（用牌方式选择，Phase 8 Hotfix 2）----
    for count in (1, 2, 3, 5):
        action_picker = CardActionPicker()
        action_picker.sync_layout(metrics, count)
        if not screen.contains(action_picker.panel_rect):
            findings.append("用牌方式面板越界")
        if not action_picker.panel_rect.contains(action_picker.cancel_button.rect):
            findings.append("用牌方式面板取消按钮越界")
        rows = action_picker.rects
        for row in rows:
            if not action_picker.panel_rect.contains(row):
                findings.append("用牌方式面板行越界")
            if row.colliderect(action_picker.cancel_button.rect):
                findings.append("用牌方式面板行压到取消按钮")
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if first.colliderect(second):
                    findings.append("用牌方式面板行重叠")

    # ---- 技能选择面板（数据驱动，行数可变）----
    for count in (1, 2, 3, 5):
        picker = SkillPicker()
        picker.sync_layout(metrics, count)
        if not screen.contains(picker.panel_rect):
            findings.append("技能面板越界")
        if not picker.panel_rect.contains(picker.cancel_button.rect):
            findings.append("技能面板取消按钮越界")
        rows = picker.rects
        for row in rows:
            if not picker.panel_rect.contains(row):
                findings.append("技能面板行越界")
            if row.colliderect(picker.cancel_button.rect):
                findings.append("技能面板行压到取消按钮")
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if first.colliderect(second):
                    findings.append("技能面板行重叠")

    return findings


def main():
    pygame.init()
    total = 0
    problems = 0
    for size in RESOLUTIONS:
        screen = pygame.display.set_mode(size)
        renderer = Renderer(screen)
        for ai_count in range(1, 8):
            for hand_size in (4, 12, 30):
                game = build_game(ai_count, hand_size=hand_size)
                renderer.draw(game, (0, 0))
                total += 1
                findings = audit(game, renderer) + audit_extras(game, renderer)
                if findings:
                    problems += 1
                    print("FAIL %s / %d 人 / %d 张手牌：%s" % (
                        size, ai_count + 1, hand_size, "；".join(sorted(set(findings)))))
    print("审计完成：%d 组合，%d 处问题" % (total, problems))
    pygame.quit()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
