"""Phase 11.5 追加：指向箭头走线 / 节奏，以及"判定演完才继续"的门控。

三条玩家反馈对应三组断言：

1. **箭头太乱、有些太短** —— 上下相邻的两个面板之间以前只有 40 来像素的直连线，
   顶端座位 → 真人状态条则会画一条穿过中央出牌位的直线。现在同一列 / 同一行的
   两个面板一律走"侧面/上下走廊"的折线（向下走右侧走廊、向上走左侧走廊），
   路径更长、方向一眼可见。
2. **箭头速度太快** —— 入场从 0.30 放慢到 0.60（按档位缩放），而且箭头是
   **沿路径长出来**的（``TargetArrow.progress``），不再是整体一闪淡入。
3. **判定之后的行动必须等判定结束** —— 判定面板展示期间动作队列被压住
   （``ActionQueue.hold`` ← ``JudgePanel.holds_actions``），且**改判窗口期间必须
   放行**（那时是引擎在等人，AI 的改判回答本身排在动作队列里，压住会死锁）。
"""

import math
import unittest

import pygame

from src.actions import ActionQueue, CallbackAction


def _metrics():
    from src.ui import layout as layout_module

    return layout_module.LayoutMetrics(
        layout_module.DESIGN_WIDTH, layout_module.DESIGN_HEIGHT)


def _path_xy(path):
    return [(int(x), int(y)) for x, y in path]


class ArrowRoutingTests(unittest.TestCase):
    """走线：同一列/同一行的两个面板走走廊折线，斜对角保持直线。"""

    def setUp(self):
        from src.ui import table as table_module

        self.table = table_module
        self.metrics = _metrics()

    def _path(self, source, target):
        return self.table.arrow_path(source, target, self.metrics)

    def test_stacked_panels_go_out_right_then_down_then_left(self):
        """上下两个面板：向右 → 向下 → 向左（玩家明确要求的形状）。"""

        upper = pygame.Rect(16, 176, 264, 158)      # 左侧栏上面那个
        lower = pygame.Rect(16, 376, 264, 158)      # 左侧栏下面那个
        path = self._path(upper, lower)
        self.assertIsNotNone(path, "上下两个面板必须走折线，而不是一条短直线")
        self.assertGreaterEqual(len(path), 4)
        points = _path_xy(path)
        start, out, corner, end = points[0], points[1], points[2], points[-1]
        self.assertGreater(out[0], start[0], "第一步要向右出去")
        self.assertGreater(corner[1], out[1], "第二步要向下走")
        self.assertLess(end[0], corner[0], "最后一步要向左进入目标面板")
        # 长度要够看：相邻两个面板之间直连只有 40 来像素、看不出指向，
        # 折线至少要比"中心直连"更长（这里实测约 1.5 倍）。
        straight = abs(lower.centery - upper.centery)
        self.assertGreater(self._length(points), straight * 1.3)

    def test_stacked_panels_mirror_on_the_right_side(self):
        """右侧栏的上下两个面板镜像走线：向左 → 向下 → 向右。"""

        upper = pygame.Rect(1320, 176, 264, 158)
        lower = pygame.Rect(1320, 376, 264, 158)
        points = _path_xy(self._path(upper, lower))
        self.assertLess(points[1][0], points[0][0], "第一步要向左出去")
        self.assertGreater(points[2][1], points[1][1], "第二步要向下走")
        self.assertGreater(points[-1][0], points[2][0], "最后一步向右进入目标")

    def test_top_seat_to_human_routes_around_the_table(self):
        """顶端座位 → 真人状态条：不再穿过中央出牌位。"""

        top = pygame.Rect(680, 18, 240, 152)
        strip = self.metrics.player_status
        points = _path_xy(self._path(top, strip))
        self.assertIsNotNone(points)
        self.assertGreater(points[1][0], points[0][0], "先向右")
        self.assertGreater(points[-1][1], points[1][1], "再向下进真人条")
        # 折线不能横穿中央出牌位（当前动作卡所在的矩形）。
        card = self.metrics.action_card_rect
        self.assertFalse(self._crosses(points, card),
                         "箭头折线不应当横穿中央出牌位")

    def test_upward_route_uses_the_other_side(self):
        """真人条 → 顶端座位：镜像到左侧走廊，一上一下不重叠。"""

        top = pygame.Rect(680, 18, 240, 152)
        strip = self.metrics.player_status
        down = _path_xy(self._path(top, strip))
        up = _path_xy(self._path(strip, top))
        self.assertLess(up[0][0], down[0][0], "两个方向各走一边（上左 / 下右）")
        self.assertLess(up[-1][1], up[0][1], "整体是往上走的")
        self.assertLess(abs(up[-1][1] - top.centery), 10,
                        "最后要进到顶端座位那一行")

    def test_same_row_panels_use_the_table_corridor(self):
        """左右两个面板（同一行）：走桌面内侧走廊，不贴着面板边缘的窄缝。"""

        left = pygame.Rect(16, 176, 264, 158)
        right = pygame.Rect(1320, 176, 264, 158)
        points = _path_xy(self._path(left, right))
        self.assertIsNotNone(points)
        central = self.metrics.central
        for x, y in points:
            self.assertGreater(y, central.top, "走廊必须在桌面内侧")
            self.assertLess(y, central.bottom)
            self.assertFalse(self.metrics.action_card_rect.collidepoint(x, y),
                             "走廊不应当压在中央出牌位上")

    def test_diagonal_pairs_still_use_a_straight_line(self):
        """斜对角：保持直线（两端裁到面板边缘），不强行折线。"""

        left_upper = pygame.Rect(16, 176, 264, 158)
        right_lower = pygame.Rect(1320, 376, 264, 158)
        self.assertIsNone(self._path(left_upper, right_lower))

    def test_arrow_grows_along_the_path(self):
        """入场阶段沿路径长出来（progress 0→1），不再整体一闪。"""

        from src.ui import fx as fx_module

        self.assertGreaterEqual(fx_module.timing().arrow_enter, 0.45,
                                "箭头入场要放慢（玩家反馈：太快看不清）")
        arrow = fx_module.TargetArrow(object(), object(), (255, 255, 255), 2.0)
        self.assertEqual(arrow.progress, 0.0)
        arrow.update(fx_module.timing().arrow_enter * 0.5)
        half = arrow.progress
        self.assertTrue(0.0 < half < 1.0, "中途应当只画出一部分")
        arrow.update(fx_module.timing().arrow_enter)
        self.assertEqual(arrow.progress, 1.0)

        points = _path_xy(self.table.truncate_path(
            [(0, 0), (100, 0), (100, 100)], half))
        self.assertGreaterEqual(len(points), 2)
        self.assertLess(self._length(points), 200,
                        "半个进度只能画出一部分路径")
        self.assertGreaterEqual(len(_path_xy(self.table.truncate_path(
            [(0, 0), (100, 0), (100, 100)], 0.0))), 0)

    @staticmethod
    def _length(points):
        total = 0.0
        for index in range(len(points) - 1):
            total += math.hypot(points[index + 1][0] - points[index][0],
                                points[index + 1][1] - points[index][1])
        return total

    def _crosses(self, points, rect):
        """折线是否穿过了这个矩形（用采样点近似，够用）。"""

        for index in range(len(points) - 1):
            (x1, y1), (x2, y2) = points[index], points[index + 1]
            steps = max(2, int(math.hypot(x2 - x1, y2 - y1) // 6))
            for step in range(steps + 1):
                t = step / steps
                x = x1 + (x2 - x1) * t
                y = y1 + (y2 - y1) * t
                if rect.inflate(-8, -8).collidepoint(x, y):
                    return True
        return False


class JudgeActionGateTests(unittest.TestCase):
    """"判定之后的行动必须等判定结束"——既不能提前，也不能压死。"""

    def _panel(self):
        from src.ui.judge import JudgePanel, JudgeStage

        return JudgePanel(), JudgeStage

    def test_queue_holds_while_gated(self):
        queue = ActionQueue()
        ran = []
        queue.add(CallbackAction(lambda: ran.append("a")))
        queue.hold = lambda: True
        queue.update(1.0)
        self.assertEqual(ran, [], "被压住时不该开始任何动作")
        self.assertTrue(queue.held())
        self.assertTrue(queue.busy, "压住期间队列仍然算忙（守卫不能误判卡死）")
        queue.hold = lambda: False
        queue.update(1.0)
        self.assertEqual(ran, ["a"], "放开之后要接着跑")

    def test_current_action_is_not_interrupted(self):
        """压住的是"下一个动作"，正在播的那个照常跑完。"""

        from src.actions import WaitAction

        queue = ActionQueue()
        queue.add(WaitAction(0.5))
        queue.update(0.1)
        self.assertIsNotNone(queue.current)
        queue.hold = lambda: True
        queue.update(0.2)
        self.assertIsNotNone(queue.current, "正在播的动作不能被压住")

    def test_no_gate_without_ui(self):
        """没有 UI（无头演算）时队列不受影响。"""

        queue = ActionQueue()
        ran = []
        queue.add(CallbackAction(lambda: ran.append("x")))
        queue.update(1.0)
        self.assertEqual(ran, ["x"])

    def test_panel_holds_actions_until_it_ends(self):
        panel, stage = self._panel()
        self.assertFalse(panel.holds_actions, "没判定时不压")
        panel.begin(_JudgeStub("lebu", None, None, None))
        self.assertTrue(panel.holds_actions, "判定一开始就压住")
        # 一路推到"最终判定牌锁定"，每一步都应当压住队列。
        # （REVEALED_HOLD 且还没有最终结果时例外——那是引擎在等改判回答。）
        guard = 0
        while panel.stage is not stage.REVEALED_HOLD and guard < 20:
            self.assertTrue(panel.holds_actions, "推进之前：当前阶段必须压住")
            panel.update(1.0, None)
            guard += 1
        self.assertIs(panel.stage, stage.REVEALED_HOLD)
        panel.finish(_JudgeStub("lebu", None, None, None))
        for _ in range(12):
            if not panel.active:
                break
            panel.update(1.0, None)
            if panel.active:
                self.assertTrue(panel.holds_actions, "演完之前一直压住")
        self.assertFalse(panel.active)
        self.assertFalse(panel.holds_actions, "判定演完就不压了")

    def test_replacement_window_releases_the_queue(self):
        """改判窗口（还没有最终判定牌）必须放行，否则会把判定压死。"""

        panel, stage = self._panel()
        panel.begin(_JudgeStub("lebu", None, None, None))
        while panel.stage is not stage.REVEALED_HOLD:
            panel.update(0.05, None)
        self.assertIsNone(panel.result)
        self.assertFalse(panel.holds_actions,
                         "引擎在等改判回答时必须放行（AI 的回答排在动作队列里）")
        panel.finish(_JudgeStub("lebu", None, None, None))
        self.assertTrue(panel.holds_actions, "最终判定牌锁定之后继续压住")

    def test_effects_installs_the_gate(self):
        """表现层把门控装到当前这份动作队列上（单机 / 房主 / 游客同一函数）。"""

        from src.renderer import Renderer

        pygame.display.init()
        pygame.font.init()
        screen = pygame.display.set_mode((1600, 1000))
        renderer = Renderer(screen)

        class FakeGame:
            def __init__(self):
                self.actions = ActionQueue()

        game = FakeGame()
        renderer.effects.game = game
        renderer.effects.update(0.016)
        self.assertIs(game.actions.hold, renderer.effects._action_gate,
                      "表现层每帧把门控装上，队列才认得出判定在演")
        renderer.effects.judge_panel.begin(_JudgeStub("lebu", None, None, None))
        queue = game.actions
        queue.add(CallbackAction(lambda: None))
        self.assertTrue(queue.held(), "判定在演时队列被压住")


class JudgeGateIntegrationTests(unittest.TestCase):
    """真引擎 + 真渲染：判定期间队列真的停住，判定演完又真的继续（不死锁）。"""

    @classmethod
    def setUpClass(cls):
        pygame.display.init()
        pygame.font.init()

    def setUp(self):
        from src.renderer import Renderer

        self.screen = pygame.display.set_mode((1600, 900))
        self.renderer = Renderer(self.screen)

    def _game(self):
        from src.game import Game

        game = Game(ai_count=2)
        game.ai_pacing = True
        game.scene = "game"
        game.start_single_player()
        game.actions.clear()
        self.renderer.draw(game)          # 一帧真实绘制：装上判定门控
        game.actions.clear()
        return game

    def _start_judge(self, game):
        from src.game.flows import JudgeFlow
        from tests.legacy_helpers import canonical_card, set_draw_order

        player = game.players[1]
        set_draw_order(game, [canonical_card("SHA")])
        return JudgeFlow(game.engine, player, "lebu").start()

    def test_actions_wait_for_the_judge_and_then_continue(self):
        game = self._game()
        ran = []
        self._start_judge(game)
        panel = self.renderer.effects.judge_panel
        self.assertTrue(panel.active, "判定一开始面板就应当在场")
        game.actions.add(CallbackAction(lambda: ran.append("after")))
        # 判定演到一半：队列必须还压着（后续行动不许开始）。
        for _ in range(30):
            game.update(0.05)
            self.renderer.effects.update(0.05)
            if panel.active:
                self.assertEqual(ran, [], "判定还在演，后面的行动不该开始")
        # 一直推到面板演完：队列放开，行动接上（不死锁）。
        for _ in range(600):
            game.update(0.05)
            self.renderer.effects.update(0.05)
            if ran:
                break
        self.assertEqual(ran, ["after"], "判定演完之后行动必须继续")
        self.assertFalse(panel.active)

    def test_the_judge_panel_always_ends(self):
        """判定面板必须自己演完：压住队列不能变成永久停住。"""

        game = self._game()
        self._start_judge(game)
        panel = self.renderer.effects.judge_panel
        frames = 0
        while panel.active and frames < 600:
            game.update(0.05)
            self.renderer.effects.update(0.05)
            frames += 1
        self.assertFalse(panel.active, "判定面板必须自己演完（不许一直挂着）")
        self.assertFalse(game.actions.held(), "演完之后队列要放开")


def panel_timing():
    from src.ui.fx import timing

    return timing()


class _JudgeStub:
    """判定结果的最小替身（面板只读这几个字段）。"""

    def __init__(self, reason, card, target, outcome):
        self.reason = reason
        self.card = card
        self.target = target
        self.outcome = outcome
        self.source_spec = None
        self.replacement_history = ()


if __name__ == "__main__":
    unittest.main()
