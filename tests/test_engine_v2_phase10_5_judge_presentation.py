"""Phase 10.5：战场可读性 + 通用判定展示（Judge Presentation）。

分组：

    A  判定语义       JUDGE_SOURCES 的 tone / kind / 规则文本
    B  判定流程       JudgeResult 携带 spec 与 outcome，改判更新最终牌
    C  判定面板       非阻塞生命周期、改判表现、不额外抽牌、分辨率适配
    D  高亮优先级     response > legal > hover > current turn，View-As 只在模式内高亮
    E  技能栏         真实技能名 / 类型 / 说明，锁定与触发技不隐藏
    F  Tooltip        通用 placement 的几何规则

全部确定性：不依赖随机数、不做像素截图比较、不写任何文件。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import EventType, SelectCardsAction, UseCardAction
from src.game.flows import JudgeFlow
from src.game.judge_presentation import (
    JUDGE_SOURCES,
    JudgeOutcomeTone,
    JudgeSourceKind,
    judge_source,
)
from src.renderer import Renderer
from src.ui import layout as layout_module
from src.ui import player as player_module
from src.ui import theme
from src.ui.judge import JudgePanel, JudgeStage
from src.ui.skill_bar import SkillBar
from src.ui.widgets import place_tooltip
from tests.legacy_helpers import canonical_card, normal_sha, set_draw_order, shan, tao

RESOLUTIONS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440))

ALL_REASONS = ("lebu", "bingliang", "shandian", "bagua", "ganglie", "luoshen", "tieji")

BLACK_SEVEN = "spade"      # normal_sha() 是黑桃 7
RED_THREE = "heart"        # tao() 是红桃 3


class _JudgeCard:
    """判定牌替身：语义只依赖花色 / 颜色 / 点数。"""

    def __init__(self, suit, color, rank):
        self.suit = suit
        # 只保留真实 Card 暴露的字段：判定语义必须能只用真实数据判断。
        self.card_color = color
        self.rank = rank


def outcome(reason, card):
    return judge_source(reason).outcome(card, None)


class Phase105TestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1600, 900))

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count=3):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        return game

    # ---- 判定脚手架 ----

    def watch_judge(self, game):
        """订阅判定事件，返回 (revealed, results, replacements) 三个列表。"""

        revealed, results, replaced = [], [], []
        game.context.events.subscribe(
            EventType.JUDGE_REVEALED, lambda _c, e: revealed.append(e.payload["result"]))
        game.context.events.subscribe(
            EventType.JUDGE_RESULT, lambda _c, e: results.append(e.payload["result"]))
        game.context.events.subscribe(
            EventType.JUDGE_REPLACED, lambda _c, e: replaced.append(e.payload))
        return revealed, results, replaced

    def start_judge(self, game, player, reason, draw_card):
        """抽一张指定判定牌并跑判定；返回 JudgeFlow。"""

        set_draw_order(game, [draw_card])
        flow = JudgeFlow(game.engine, player, reason)
        flow.start()
        return flow


# ==================================================
# A 判定语义
# ==================================================

class JudgeSemanticsTests(Phase105TestCase):

    def test_every_judgement_reason_has_a_declaration(self):
        for reason in ALL_REASONS:
            spec = judge_source(reason)
            with self.subTest(reason=reason):
                self.assertEqual(spec.reason, reason)
                self.assertTrue(spec.display_name)
                self.assertTrue(spec.rule_text, "规则文本不能为空")
                self.assertIn(spec.kind, tuple(JudgeSourceKind))

    def test_source_kinds_match_where_the_judgement_comes_from(self):
        card_reasons = sorted(
            reason for reason in ALL_REASONS
            if JUDGE_SOURCES[reason].kind is JudgeSourceKind.CARD)
        self.assertEqual(card_reasons, ["bingliang", "lebu", "shandian"])
        self.assertIs(JUDGE_SOURCES["bagua"].kind, JudgeSourceKind.EQUIPMENT)
        self.assertIs(JUDGE_SOURCES["ganglie"].kind, JudgeSourceKind.SKILL)
        # 卡牌来源必须带 Card.name，UI 才能去判定区找到那张实体牌。
        for reason in card_reasons:
            self.assertTrue(JUDGE_SOURCES[reason].card_name)
        for reason in ("bagua", "ganglie", "luoshen", "tieji"):
            self.assertTrue(JUDGE_SOURCES[reason].skill_id)

    def test_lebu_tone_follows_heart(self):
        self.assertIs(outcome("lebu", _JudgeCard("heart", "red", "3")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("lebu", _JudgeCard("spade", "black", "7")).tone,
                      JudgeOutcomeTone.NEGATIVE)

    def test_bingliang_tone_follows_club(self):
        self.assertIs(outcome("bingliang", _JudgeCard("club", "black", "7")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("bingliang", _JudgeCard("heart", "red", "7")).tone,
                      JudgeOutcomeTone.NEGATIVE)

    def test_shandian_is_negative_only_when_it_hits(self):
        self.assertIs(outcome("shandian", _JudgeCard("spade", "black", "7")).tone,
                      JudgeOutcomeTone.NEGATIVE)
        # 黑桃但点数不在 2～9：未命中。
        self.assertIs(outcome("shandian", _JudgeCard("spade", "black", "K")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("shandian", _JudgeCard("heart", "red", "5")).tone,
                      JudgeOutcomeTone.POSITIVE)

    def test_bagua_tone_follows_colour(self):
        self.assertIs(outcome("bagua", _JudgeCard("heart", "red", "5")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("bagua", _JudgeCard("spade", "black", "5")).tone,
                      JudgeOutcomeTone.NEUTRAL)

    def test_ganglie_tone_is_inverted_because_it_is_a_counter(self):
        # 刚烈是受伤后的反击：非红桃才成功，语义与花色直觉相反。
        self.assertIs(outcome("ganglie", _JudgeCard("spade", "black", "5")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("ganglie", _JudgeCard("heart", "red", "5")).tone,
                      JudgeOutcomeTone.NEGATIVE)

    def test_skill_judgements_cover_their_positive_cases(self):
        self.assertIs(outcome("luoshen", _JudgeCard("spade", "black", "5")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("tieji", _JudgeCard("diamond", "red", "9")).tone,
                      JudgeOutcomeTone.POSITIVE)
        self.assertIs(outcome("tieji", _JudgeCard("spade", "black", "9")).tone,
                      JudgeOutcomeTone.NEUTRAL)

    def test_missing_or_unknown_judgement_is_neutral_and_safe(self):
        for reason in ALL_REASONS:
            with self.subTest(reason=reason):
                self.assertIs(outcome(reason, None).tone, JudgeOutcomeTone.NEUTRAL)
        spec = judge_source("no_such_reason")
        self.assertIs(spec.kind, JudgeSourceKind.OTHER)
        self.assertIs(spec.outcome(None, None).tone, JudgeOutcomeTone.NEUTRAL)

    def test_tone_colours_are_distinct_and_shared_by_the_ui(self):
        colors = {theme.judge_tone_color(tone) for tone in JudgeOutcomeTone}
        self.assertEqual(len(colors), 3)
        self.assertEqual(theme.judge_tone_color(JudgeOutcomeTone.POSITIVE),
                         theme.JUDGE_POSITIVE)
        self.assertEqual(theme.judge_tone_color(JudgeOutcomeTone.NEGATIVE),
                         theme.JUDGE_NEGATIVE)
        self.assertEqual(theme.judge_tone_color("nope"), theme.JUDGE_NEUTRAL)


# ==================================================
# B 判定流程
# ==================================================

class JudgeFlowTests(Phase105TestCase):

    def test_revealed_result_carries_the_source_but_not_the_outcome(self):
        game = self.make_game(2)
        victim = game.players[1]
        revealed, _results, _replaced = self.watch_judge(game)
        self.start_judge(game, victim, "bagua", normal_sha())
        self.assertTrue(revealed)
        self.assertEqual(revealed[0].source_spec.reason, "bagua")
        self.assertIsNone(revealed[0].outcome, "翻开时不应该剧透结果")

    def test_final_result_carries_the_outcome(self):
        game = self.make_game(2)
        victim = game.players[1]
        _revealed, results, _replaced = self.watch_judge(game)
        self.start_judge(game, victim, "bagua", normal_sha())
        self.assertTrue(results)
        self.assertIs(results[-1].tone, JudgeOutcomeTone.NEUTRAL)   # 黑桃
        self.assertTrue(results[-1].outcome.title)

    def test_judge_draws_exactly_one_card(self):
        game = self.make_game(2)
        victim = game.players[1]
        set_draw_order(game, [normal_sha() for _ in range(6)])
        before = len(game.deck.draw_pile)
        JudgeFlow(game.engine, victim, "lebu").start()
        self.assertEqual(len(game.deck.draw_pile), before - 1, "判定只能抽一张牌")

    def test_judge_card_moves_through_the_real_zones(self):
        game = self.make_game(2)
        victim = game.players[1]
        card = normal_sha()
        set_draw_order(game, [card])
        JudgeFlow(game.engine, victim, "lebu").start()
        # 判定牌经处理区，最终落到弃牌堆（天妒一类会在这之前取走）。
        self.assertTrue(
            any(item is card for item in game.deck.discard_pile)
            or any(item is card for item in game.processing_zone))
        self.assertFalse(any(item is card for item in game.deck.draw_pile))

    def test_replacement_updates_the_final_card_and_keeps_the_original(self):
        game = self.make_game(3)
        # 用真人当被判定者：改判窗口会挂起等我们回答，测试才是确定的。
        victim = game.player
        game.set_general(victim, "simayi")
        victim.judgement_zone.append(canonical_card("LEBU"))
        replacement = tao()
        victim.hand = [replacement]
        flow = self.start_judge(game, victim, "lebu", normal_sha())

        self.assertIsNotNone(game.pending_request, "判定不利时鬼才应当拿到改判窗口")
        game.engine.submit(SelectCardsAction(
            victim, game.pending_request.request_id, [replacement]))

        # 判定收尾后 game.judge_context 会被清空，改判记录挂在流程上。
        context = flow.judge_context
        self.assertTrue(context.replaced)
        self.assertEqual(len(context.replacement_history), 1)
        player, skill_id, old_card, new_card = context.replacement_history[0]
        self.assertIs(player, victim)
        self.assertEqual(skill_id, "guicai")
        self.assertEqual(old_card.suit, BLACK_SEVEN)
        self.assertEqual(new_card.suit, RED_THREE)
        # 原判定牌仍可追溯。
        self.assertIs(context.original_card, old_card)
        self.assertIs(context.current_card, new_card)

    def test_judge_replaced_event_fires_once_per_swap(self):
        game = self.make_game(3)
        victim = game.player
        game.set_general(victim, "simayi")
        replacement = tao()
        victim.hand = [replacement]
        _revealed, _results, replaced = self.watch_judge(game)
        self.start_judge(game, victim, "lebu", normal_sha())
        game.engine.submit(SelectCardsAction(
            victim, game.pending_request.request_id, [replacement]))

        self.assertEqual(len(replaced), 1)
        payload = replaced[0]
        self.assertEqual(payload["skill_id"], "guicai")
        self.assertEqual(payload["old_card"].suit, BLACK_SEVEN)
        self.assertEqual(payload["new_card"].suit, RED_THREE)
        self.assertEqual(len(payload["history"]), 1)

    def test_final_result_after_replacement_uses_the_new_card(self):
        game = self.make_game(3)
        victim = game.player
        game.set_general(victim, "simayi")
        victim.judgement_zone.append(canonical_card("LEBU"))
        replacement = tao()
        victim.hand = [replacement]
        _revealed, results, _replaced = self.watch_judge(game)
        flow = self.start_judge(game, victim, "lebu", normal_sha())
        if flow.status.value == "waiting":
            game.engine.submit(SelectCardsAction(
                victim, game.pending_request.request_id, [replacement]))
            result = flow.result
        else:
            result = results[-1]
        self.assertIsNotNone(result)
        self.assertEqual(result.card.suit, RED_THREE)
        self.assertTrue(result.replaced)
        self.assertIs(result.tone, JudgeOutcomeTone.POSITIVE)
        self.assertEqual(len(result.replacement_history), 1)

    def test_flow_clears_the_live_judge_state_when_it_finishes(self):
        game = self.make_game(2)
        victim = game.players[1]
        self.start_judge(game, victim, "lebu", normal_sha())
        self.assertIsNone(game.judge_context)
        self.assertIsNone(game.judge_card)


# ==================================================
# C 判定面板
# ==================================================

class JudgePanelTests(Phase105TestCase):

    def revealed_panel(self, game, player, reason, draw_card):
        revealed, results, replaced = self.watch_judge(game)
        self.start_judge(game, player, reason, draw_card)
        panel = JudgePanel().begin(revealed[-1])
        return panel, revealed, results, replaced

    def test_panel_activates_with_the_source_and_rule_text(self):
        game = self.make_game(2)
        victim = game.players[1]
        victim.judgement_zone.append(canonical_card("LEBU"))
        panel, _revealed, _results, _replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        self.assertTrue(panel.active)
        self.assertEqual(panel.stage, JudgeStage.OPEN)
        self.assertEqual(panel.spec.display_name, "乐不思蜀")
        self.assertTrue(panel.spec.rule_text)
        self.assertIsNotNone(panel.shown_card)
        self.assertIs(panel.owner, victim)

    def test_panel_stage_order_is_deterministic(self):
        game = self.make_game(2)
        victim = game.players[1]
        panel, _revealed, results, _replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        panel.finish(results[-1])
        order = []
        for _ in range(2000):
            panel.update(1 / 60, game)
            if not order or order[-1] is not panel.stage:
                order.append(panel.stage)
            if not panel.active:
                break
        self.assertIs(order[0], JudgeStage.OPEN)
        for earlier, later in ((JudgeStage.DRAW_ANIMATION, JudgeStage.REVEALED_HOLD),
                               (JudgeStage.REVEALED_HOLD, JudgeStage.FINAL_RESULT),
                               (JudgeStage.FINAL_RESULT, JudgeStage.OUTCOME_HOLD),
                               (JudgeStage.OUTCOME_HOLD, JudgeStage.FADE_OUT)):
            self.assertLess(order.index(earlier), order.index(later))
        self.assertIs(order[-1], JudgeStage.DONE)
        self.assertFalse(panel.active)

    def test_panel_waits_for_the_result_during_the_replacement_window(self):
        """没有最终结果时面板不许自己消失，否则改判过程就看不见了。"""

        game = self.make_game(2)
        victim = game.players[1]
        panel, _revealed, _results, _replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        for _ in range(600):          # 10 秒
            panel.update(1 / 60, game)
        self.assertTrue(panel.active)
        self.assertIs(panel.stage, JudgeStage.REVEALED_HOLD)

    def test_panel_swaps_the_card_on_replacement_without_restarting(self):
        game = self.make_game(3)
        victim = game.player
        game.set_general(victim, "simayi")
        replacement = tao()
        victim.hand = [replacement]
        panel, _revealed, _results, replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        original = panel.shown_card
        self.assertEqual(original.suit, BLACK_SEVEN)

        game.engine.submit(SelectCardsAction(
            victim, game.pending_request.request_id, [replacement]))
        history = replaced[0]["history"]
        panel.note_replacement(
            {"new_card": replacement, "old_card": original, "history": history,
             "player": victim, "skill_id": "guicai"}, game)

        self.assertTrue(panel.active, "改判不能把面板重开或关掉")
        self.assertIs(panel.stage, JudgeStage.REPLACEMENT)
        self.assertIs(panel.shown_card, replacement)
        self.assertIs(panel.previous_card, original)
        self.assertEqual(panel.replacement_history, history)
        self.assertEqual(panel.replacement_skill_name, "鬼才")
        self.assertTrue(panel.was_replaced)

    def test_panel_shows_the_outcome_only_after_the_final_card_locks(self):
        game = self.make_game(2)
        victim = game.players[1]
        panel, _revealed, results, _replaced = self.revealed_panel(
            game, victim, "bagua", normal_sha())
        self.assertIsNone(panel.outcome)
        self.assertFalse(panel.shows_outcome())
        panel.finish(results[-1])
        self.assertIsNotNone(panel.outcome)
        self.assertIs(panel.tone, JudgeOutcomeTone.NEUTRAL)

    def test_panel_fades_out_and_makes_itself_inactive(self):
        game = self.make_game(2)
        victim = game.players[1]
        panel, _revealed, results, _replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        panel.finish(results[-1])
        for _ in range(2000):
            panel.update(1 / 60, game)
            if not panel.active:
                break
        self.assertFalse(panel.active)
        self.assertIs(panel.stage, JudgeStage.DONE)
        self.assertIsNone(
            panel.draw(self.screen, game, layout_module.LayoutMetrics(1600, 900)))

    def test_panel_never_moves_a_card_itself(self):
        """面板只读引擎已经移动过的牌：跑再久也不能改变任何牌区。"""

        game = self.make_game(2)
        victim = game.players[1]
        victim.judgement_zone.append(canonical_card("LEBU"))
        panel, _revealed, results, _replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        panel.finish(results[-1])
        before = (len(game.deck.draw_pile), len(game.deck.discard_pile),
                  len(game.processing_zone), len(victim.hand))
        for _ in range(2000):
            panel.update(1 / 60, game)
            if not panel.active:
                break
        after = (len(game.deck.draw_pile), len(game.deck.discard_pile),
                 len(game.processing_zone), len(victim.hand))
        self.assertEqual(before, after)

    def test_panel_rect_keeps_clear_of_prompt_and_hand_area(self):
        panel = JudgePanel()
        for width, height in RESOLUTIONS:
            metrics = layout_module.LayoutMetrics(width, height)
            rect = panel.rect(metrics)
            screen = pygame.Rect(0, 0, width, height)
            with self.subTest(resolution=(width, height)):
                self.assertTrue(screen.contains(rect), "判定面板越出屏幕")
                self.assertFalse(rect.colliderect(metrics.prompt), "面板压住了提示条")
                self.assertLessEqual(rect.bottom, metrics.hand_area.top)
                self.assertGreaterEqual(rect.top, 0)

    def test_panel_is_reserved_by_the_tooltip_avoid_list(self):
        game = self.make_game(2)
        victim = game.players[1]
        victim.judgement_zone.append(canonical_card("LEBU"))
        renderer = Renderer(self.screen)
        panel, _revealed, _results, _replaced = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        renderer.effects.judge_panel = panel
        avoid = renderer._tooltip_avoid_rects(renderer.metrics)
        self.assertIn(panel.rect(renderer.metrics), avoid)

    def test_panel_draw_covers_the_whole_lifecycle_without_crashing(self):
        game = self.make_game(2)
        victim = game.players[1]
        victim.judgement_zone.append(canonical_card("LEBU"))
        renderer = Renderer(self.screen)
        renderer.effects.judge_panel, _r, results, _x = self.revealed_panel(
            game, victim, "lebu", normal_sha())
        renderer.effects.judge_panel.finish(results[-1])
        for _ in range(2000):
            renderer.effects.judge_panel.update(1 / 60, game)
            renderer.draw(game)
            if not renderer.effects.judge_panel.active:
                break
        self.assertFalse(renderer.effects.judge_panel.active)


class NestedJudgeWindowTests(Phase105TestCase):
    """技能在出牌事件里同步启动的判定窗口，压在父流程请求下面也要能被驱动。

    铁骑在 ``CARD_USED`` 回调里判定：判定窗口先入栈，随后杀的「求闪」请求压
    在它上面。父请求解决后，窗口必须被重新驱动 —— 否则谁也不会再去处理它，
    整局卡在"等待判定结果……"上（用户报的界面下不去）。
    """

    def _battle(self):
        game = self.make_game(3)
        # 节奏模式（真实对局）：AI 的响应会排队，请求因此会停留在栈里 ——
        # 判定窗口正是这样被父流程的请求压在下面的。
        game.ai_pacing = True
        game.start_local_battle(3)
        game.set_general(game.players[1], "machao")     # 铁骑：出杀时判定
        game.set_general(game.players[2], "simayi")     # 鬼才：改判者
        for player in game.players:
            player.hand = [normal_sha(), tao(), tao()]
            player.hp = player.max_hp
        set_draw_order(game, [normal_sha() for _ in range(20)])
        return game

    def test_replacement_window_survives_being_stacked_under_a_request(self):
        game = self._battle()
        attacker, target = game.players[1], game.players[2]
        sha = attacker.hand[0]

        game.submit_action(UseCardAction(attacker, sha, [target]))

        # 全程不手动驱动任何请求：真实对局里请求在创建时由引擎驱动一次，
        # 之后的推进完全依赖引擎自己（这正是被修掉的那条路径）。
        for _ in range(400):
            game.update(0.05)
            if game.pending_request is None and not game.busy:
                break

        self.assertIsNone(
            game.pending_request,
            "判定窗口被压在父请求下且没人驱动，会一直卡在这里")
        self.assertIsNone(getattr(game, "judge_context", None), "判定必须已经收尾")
        self.assertFalse(
            any(item is sha for item in game.processing_zone),
            "【杀】应该已经离开处理区")


# ==================================================
# D 高亮优先级
# ==================================================
class HighlightPriorityTests(Phase105TestCase):

    def test_priority_order_matches_the_battlefield_rules(self):
        # selected > 正在响应 > 合法目标 > hover > 当前回合
        self.assertEqual(theme.resolve_state("selected_target", "pending_response"),
                         "selected_target")
        self.assertEqual(theme.resolve_state("pending_response", "valid_target"),
                         "pending_response")
        self.assertEqual(theme.resolve_state("valid_target", "hover"), "valid_target")
        self.assertEqual(theme.resolve_state("hover", "current_turn"), "hover")
        self.assertEqual(theme.resolve_state("current_turn", "none"), "current_turn")

    def test_selected_beats_everything_except_death(self):
        self.assertEqual(
            theme.resolve_state("hover", "valid_target", "selected_target"),
            "selected_target")
        self.assertEqual(theme.resolve_state("selected", "dead"), "dead")

    def test_illegal_target_is_weaker_than_the_current_turn(self):
        self.assertEqual(theme.resolve_state("invalid_target", "current_turn"),
                         "current_turn")

    def test_every_visual_state_has_a_priority(self):
        for name in theme.VISUAL_STATES:
            with self.subTest(state=name):
                self.assertIn(name, theme.STATE_PRIORITY)

    def test_view_as_candidates_only_exist_inside_the_mode(self):
        game = self.make_game(2)
        game.set_general(game.player, "zhaoyun")
        game.player.hand = [shan(), tao()]
        game.current_turn_player = game.player
        game.phase = "play"

        # 未点技能：没有任何 source 候选，UI 也就不会画转化高亮。
        self.assertFalse(game.view_as_candidate_ids())
        playable = player_module.playable_hand_indices(game)
        self.assertTrue(playable, "出牌阶段【闪】应当是可操作牌（龙胆可转化）")

        # 点【龙胆】进入选牌模式：【闪】成为合法来源。
        self.assertTrue(game.start_skill_activation("longdan"))
        candidates = game.view_as_candidate_ids()
        self.assertTrue(candidates, "进入 View-As 后必须有可高亮的来源牌")
        indices = player_module.playable_hand_indices(game)
        self.assertEqual(len(indices), len(candidates))
        for index in indices:
            self.assertIn(id(game.player.hand[index]), candidates)

    def test_engine_reports_the_responding_player(self):
        game = self.make_game(2)
        renderer = Renderer(self.screen)
        game.response.request("【杀】：请打出一张【闪】", {"SHAN"},
                              on_card=lambda *a, **k: None, on_pass=lambda: None)
        self.assertIs(renderer.responding_player(game), game.player)
        game.response.clear()
        self.assertIsNone(renderer.responding_player(game))


# ==================================================
# E 技能栏
# ==================================================

class SkillBarTests(Phase105TestCase):

    def bar_for(self, game, general_id):
        game.set_general(game.player, general_id)
        metrics = layout_module.LayoutMetrics(1600, 900)
        bar = SkillBar()
        bar.sync_layout(metrics, 0)
        bar.sync(game)
        return bar, metrics

    def test_skill_bar_shows_the_real_skill_names(self):
        game = self.make_game(2)
        bar, _metrics = self.bar_for(game, "zhouyu")
        self.assertEqual([definition.name for definition in bar.skills],
                         ["英姿", "反间"])
        self.assertEqual(len(bar.rects), 2)

    def test_locked_and_triggered_skills_stay_visible(self):
        game = self.make_game(2)
        bar, _metrics = self.bar_for(game, "guojia")
        kinds = {definition.name: definition.kind.value for definition in bar.skills}
        self.assertEqual(kinds, {"天妒": "passive", "遗计": "passive"})
        self.assertEqual(len(bar.rects), len(bar.skills), "非主动技也要占按钮位")
        self.assertFalse(bar.enabled_ids, "触发技不能被当成可发动按钮")

    def test_active_skill_is_disabled_when_it_cannot_be_used(self):
        game = self.make_game(2)
        bar, _metrics = self.bar_for(game, "zhouyu")
        # 不是自己的出牌阶段：反间不可发动，但按钮仍然在。
        self.assertNotIn("fanjian", bar.enabled_ids)
        self.assertIn("fanjian", bar.blocked_reasons)
        self.assertEqual(len(bar.rects), 2)

    def test_view_as_skill_is_listed_and_activatable(self):
        game = self.make_game(2)
        game.player.hand = [shan()]
        bar, _metrics = self.bar_for(game, "zhaoyun")
        self.assertEqual([definition.name for definition in bar.skills], ["龙胆"])
        self.assertIn("longdan", bar.enabled_ids)

    def test_skill_info_reuses_the_skilldef_description(self):
        game = self.make_game(2)
        bar, _metrics = self.bar_for(game, "zhouyu")
        bar.info_skill_id = "fanjian"
        text = bar.info_text(game)
        self.assertIn("反间", text)
        self.assertIn(game.skill_registry.get("fanjian").description, text)

    def test_multi_skill_general_gets_one_button_per_skill(self):
        game = self.make_game(2)
        bar, _metrics = self.bar_for(game, "sunshangxiang")
        self.assertEqual([definition.name for definition in bar.skills],
                         ["结姻", "枭姬"])
        rects = [pygame.Rect(rect) for rect in bar.rects]
        self.assertEqual(len(rects), 2)
        self.assertFalse(rects[0].colliderect(rects[1]), "技能按钮不能互相重叠")

    def test_skill_buttons_stay_inside_the_screen_at_every_resolution(self):
        for width, height in RESOLUTIONS:
            game = self.make_game(2)
            game.set_general(game.player, "zhouyu")
            metrics = layout_module.LayoutMetrics(width, height)
            bar = SkillBar()
            bar.sync_layout(metrics, 0)
            bar.sync(game)
            screen = pygame.Rect(0, 0, width, height)
            with self.subTest(resolution=(width, height)):
                for rect in bar.rects:
                    self.assertTrue(screen.contains(rect), "技能按钮越出屏幕")


# ==================================================
# F Tooltip placement
# ==================================================

class TooltipPlacementTests(Phase105TestCase):

    VIEWPORT = pygame.Rect(0, 0, 1600, 900)

    def test_prefers_the_right_side_when_there_is_room(self):
        anchor = pygame.Rect(200, 200, 80, 60)
        rect = place_tooltip(anchor, (300, 200), self.VIEWPORT)
        self.assertGreaterEqual(rect.left, anchor.right)
        self.assertTrue(self.VIEWPORT.contains(rect))

    def test_flips_to_the_left_when_the_right_side_is_tight(self):
        anchor = pygame.Rect(1500, 200, 80, 60)
        rect = place_tooltip(anchor, (300, 200), self.VIEWPORT)
        self.assertLessEqual(rect.right, anchor.left)
        self.assertTrue(self.VIEWPORT.contains(rect))

    def test_falls_back_below_or_above_when_neither_side_fits(self):
        anchor = pygame.Rect(60, 400, 1480, 80)
        rect = place_tooltip(anchor, (300, 200), self.VIEWPORT)
        self.assertTrue(self.VIEWPORT.contains(rect))
        self.assertFalse(rect.colliderect(anchor), "工具栏不能压住 anchor")

    def test_avoid_areas_are_respected_when_a_free_slot_exists(self):
        anchor = pygame.Rect(200, 200, 80, 60)
        avoid = [pygame.Rect(300, 180, 400, 300)]
        rect = place_tooltip(anchor, (260, 180), self.VIEWPORT, avoid=avoid)
        self.assertTrue(self.VIEWPORT.contains(rect))
        self.assertFalse(rect.colliderect(avoid[0]))

    def test_never_leaves_the_viewport(self):
        for anchor_xy in ((0, 0), (1580, 880), (800, 450), (10, 880)):
            anchor = pygame.Rect(anchor_xy[0], anchor_xy[1], 24, 24)
            rect = place_tooltip(anchor, (320, 220), self.VIEWPORT)
            with self.subTest(anchor=anchor_xy):
                self.assertTrue(self.VIEWPORT.contains(rect), "提示框越出视口")

    def test_renderer_reserves_seats_prompt_hand_and_the_judge_panel(self):
        game = self.make_game(3)
        renderer = Renderer(self.screen)
        renderer.draw(game)
        avoid = renderer._tooltip_avoid_rects(renderer.metrics)
        self.assertIn(renderer.metrics.prompt, avoid)
        self.assertIn(renderer.metrics.hand_area, avoid)
        for rect in renderer.table_layout.seat_rects.values():
            self.assertIn(rect, avoid)


if __name__ == "__main__":
    unittest.main()
