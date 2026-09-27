"""Presentation System 2.0 真实验收：走真实主循环打一局，抓演出画面与状态。

不是独立渲染工具：屏幕 / Game / Renderer 全部是 ``main.py`` 正在用的那一个
（见 ``src/ui/runtime_hook.py``）。脚本做两件事：

1. **替真人点掉重复劳动**（菜单 → 选将 → 开局），与 ``handplay_scene`` 同一套
   事件注入路径（``pygame.event.post`` → 真实主循环 → ``handle_game_click``）；
2. **在关键演出时刻按快门**，并把表现层状态（演出队列 / 牌移动 / 输入锁 /
   动画补间）记进 JSON，供人工与报告核对。

用法::

    SGS_RUNTIME_SCRIPT=present_acceptance python main.py

环境变量：

* ``SGS_PA_PLAYERS``   身份局人数（默认 5）
* ``SGS_PA_OUT``       截图目录（默认 tools/ui_snapshots/present）
* ``SGS_PA_SECONDS``   最长跑多少秒（默认 240）
* ``SGS_PA_SPEED``     表现速度档（默认 1.5，快一点才能在一局里看完更多场面）
* ``SGS_PA_SEED``      随机种子（默认 20260927）
"""

import json
import os
import random
import time

import pygame

from src.ui import input_lock

SHOTS = {
    # 关键词 → 截图名。关键词在"当前演出 / 面板"的文本里出现就按快门。
    "判定": "judge",
    "跳过": "phase_skip",
    "濒死": "dying",
    "阵亡": "death",
    "受到": "damage",
    "回复": "recover",
    "【": "skill",
}


class Hook:
    """一局自动打 + 按快门 + 记录表现层状态。"""

    SETTLE = 14

    def __init__(self):
        self.ctx = None
        self.frames = 0
        self.stage = 0
        self.timer = 0
        self.pending = None
        self.done_menu = False
        self.started = 0.0
        self.shots = 0
        self.seen = set()
        self.turn_actions = 0
        self.clicks = 0
        self.notes = []
        self.errors = []
        self.trace = []
        self.rng = random.Random(int(os.environ.get("SGS_PA_SEED", "20260927")))
        self.out_dir = os.environ.get("SGS_PA_OUT", "tools/ui_snapshots/present")
        self.max_seconds = float(os.environ.get("SGS_PA_SECONDS", "240"))
        self.last_report = 0.0
        self.peak_pending = 0
        self.skipped = 0
        #: 响应式检查：要依次切到的窗口尺寸（``SGS_PA_SIZE=1280x720,1920x1080``）。
        self.size_jobs = []
        self.size_reports = []
        self.out_of_screen = []
        self.next_size_at = 0

    # ---- 挂钩接口 ----

    def bind_environment(self, env):
        return self

    def bind(self, context):
        self.ctx = context
        return self

    def finish(self):
        self._write_report()
        print("acceptance: 结束 frames=%d shots=%d clicks=%d errors=%d" % (
            self.frames, self.shots, self.clicks, len(self.errors)), flush=True)

    def step(self, dt):
        if self.ctx is None:
            return False
        self.frames += 1
        if self.started == 0.0:
            self.started = time.time()
            self._prepare()
        if time.time() - self.started > self.max_seconds:
            return "quit"
        # 待办动作优先：菜单驱动与自动出牌都靠它排队（否则会被菜单分支吞掉）。
        if self.pending is not None:
            self.timer -= 1
            if self.timer <= 0:
                action, self.pending = self.pending, None
                try:
                    action()
                except Exception as error:             # noqa: BLE001 - 验收工具
                    self.errors.append("action: %s: %s" % (type(error).__name__, error))
            return False
        if not self.done_menu:
            self._drive_menu()
            return False
        try:
            self._play(dt)
        except Exception as error:                     # noqa: BLE001 - 验收工具
            self.errors.append("play: %s: %s" % (type(error).__name__, error))
        return False

    # ---- 准备 ----

    def _prepare(self):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
        except OSError:
            pass
        speed = float(os.environ.get("SGS_PA_SPEED", "1.5"))
        game = self.ctx.game
        # 只动外观设置：速度是本地表现设置（不改规则）。
        try:
            game.speed = speed
        except Exception:                              # noqa: BLE001
            pass
        print("acceptance: 速度=%s 人数=%s 输出=%s" % (
            speed, os.environ.get("SGS_PA_PLAYERS", "-"), self.out_dir), flush=True)

    # ---- 菜单 → 对局 ----

    def _later(self, delay, action):
        self.pending = action
        self.timer = max(1, delay)

    def _click(self, position):
        rect = self.ctx.screen.get_rect()
        x = max(rect.left, min(int(position[0]), rect.right - 1))
        y = max(rect.top, min(int(position[1]), rect.bottom - 1))
        self.clicks += 1
        for event in (
            pygame.event.Event(pygame.MOUSEMOTION,
                               {"pos": (x, y), "rel": (0, 0), "buttons": (0, 0, 0)}),
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (x, y), "button": 1}),
            pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (x, y), "button": 1}),
        ):
            pygame.event.post(event)

    def _drive_menu(self):
        menu = self.ctx.start_menu
        game = self.ctx.game
        # 身份局：这一局要覆盖到身份揭示 / 濒死 / 结算。
        if self.stage == 0 and game.scene == "menu":
            menu.sync_layout(self.ctx.renderer.metrics)
            menu.sync_modes(game.modes.list_modes())
            button = menu.mode_buttons.get("identity")
            if button is None:
                self.errors.append("菜单里找不到身份模式按钮")
                self.done_menu = True
                return
            self.stage = 1
            self._click(button.rect.center)
            self._later(self.SETTLE, self._menu_start)
            return
        if self.stage == 1:
            return
        # 已经进对局（或其它场景）：收工，进入自动出牌阶段。
        if game.scene == "game":
            self.done_menu = True

    def _menu_start(self):
        menu = self.ctx.start_menu
        self._click(menu.start_button.rect.center)
        self._later(self.SETTLE * 2, self._after_start)

    def _after_start(self):
        """开局可能是身份揭示或选将，逐屏点过去。"""

        game = self.ctx.game
        if game.scene == "identity_reveal":
            screen = self.ctx.identity_reveal
            # 全屏场景的按钮在绘制时才布局；这里自己同步一次，保证 rect 可用。
            try:
                screen.sync_layout(renderer.metrics)
            except Exception:                          # noqa: BLE001
                pass
            button = getattr(screen, "continue_button", None)
            if button is not None:
                self._click(button.rect.center)
            self._later(self.SETTLE, self._after_start)
            return
        if game.scene == "general_select":
            screen = self.ctx.general_select
            try:
                screen.sync_layout(renderer.metrics, self._general_choices(game))
            except Exception:                          # noqa: BLE001
                pass
            rects = getattr(screen, "card_rects", None) or ()
            if rects:
                self._click(pygame.Rect(rects[0]).center)
            self._later(self.SETTLE, self._confirm_general)
            return
        if game.scene == "game":
            self.done_menu = True
            self._later(self.SETTLE * 4, self._announce)
            return
        self._later(self.SETTLE, self._after_start)

    def _general_choices(self, game):
        """选将页要用的候选列表（与场景自己绘制时用的是同一份）。"""

        picker = getattr(game, "selectable_generals", None)
        if callable(picker):
            try:
                return list(picker())
            except Exception:                          # noqa: BLE001
                return []
        return []

    def _confirm_general(self):
        screen = self.ctx.general_select
        self._click(screen.confirm_button.rect.center)
        self._later(self.SETTLE * 2, self._after_start)

    def _announce(self):
        game = self.ctx.game
        spec = (os.environ.get("SGS_PA_SIZE") or "").strip()
        if spec:
            for piece in spec.split(","):
                piece = piece.strip()
                if "x" in piece:
                    width, _, height = piece.partition("x")
                    if width.isdigit() and height.isdigit():
                        self.size_jobs.append((int(width), int(height)))
            self.next_size_at = self.frames + 30
        print("acceptance: 就绪 scene=%s phase=%s turn=%s 尺寸任务=%s" % (
            game.scene, game.phase,
            getattr(getattr(game, "current_turn_player", None), "name", "-"),
            self.size_jobs), flush=True)

    # ---- 响应式检查 ----

    def _resize_if_due(self):
        if not self.size_jobs or self.frames < self.next_size_at:
            return
        size = self.size_jobs.pop(0)
        self.next_size_at = self.frames + 60
        try:
            screen = pygame.display.set_mode(size)
            if self.ctx.resync is not None:
                self.ctx.resync(screen)
            self.ctx.screen = screen
        except Exception as error:                     # noqa: BLE001
            self.errors.append("resize %s: %s" % (size, error))
            return
        # 切完尺寸之后让主循环跑几帧（布局重建、补间跟上），再截图取证。
        self._later(20, lambda: self._shoot_size(size))

    def _shoot_size(self, size):
        renderer = self.ctx.renderer
        name = "size_%dx%d" % size
        self._shot(self.ctx.game, renderer, name)
        self._bounds_check(size)

    def _bounds_check(self, size):
        """关键元素是否全部落在屏幕内（响应式验收的硬指标）。"""

        renderer = self.ctx.renderer
        game = self.ctx.game
        metrics = renderer.metrics
        screen = pygame.Rect(0, 0, metrics.screen_w, metrics.screen_h)
        rects = {
            "prompt": metrics.prompt,
            "status": metrics.player_status,
            "hand_area": metrics.hand_area,
            "primary": metrics.primary_button,
            "secondary": metrics.secondary_button,
            "action_card": metrics.action_card_rect,
            "speed": metrics.speed_control,
            "log": metrics.log_rect,
        }
        layout_state = renderer.table_layout
        if layout_state is not None:
            for index, rect in enumerate(layout_state.hand_rects):
                rects["hand%d" % index] = rect
            for player, rect in layout_state.seat_rects.items():
                rects["seat:" + str(getattr(player, "name", "?"))] = rect
        effects = renderer.effects
        if getattr(effects.judge_panel, "active", False):
            rects["judge_panel"] = effects.judge_panel.rect(metrics)
        if effects.skill_banner.timer > 0:
            rect = effects.skill_banner.rect(metrics, game)
            if rect is not None:
                rects["skill_banner"] = rect
        pool = [card for card, _key in renderer.get_pool_entries(game)]
        for index, rect in enumerate(renderer.get_public_card_rects(pool)):
            rects["pool%d" % index] = rect

        outside = []
        for name, rect in rects.items():
            if rect is None:
                continue
            rect = pygame.Rect(rect)
            if not screen.contains(rect):
                outside.append("%s=%s" % (name, tuple(rect)))
        entry = {"size": list(size), "outside": outside, "checked": len(rects)}
        self.size_reports.append(entry)
        if outside:
            self.out_of_screen.extend(outside)
            print("acceptance: 越界 %s → %s" % (size, outside), flush=True)
        else:
            print("acceptance: 尺寸 %s 全部元素在屏内（%d 项）" % (size, len(rects)),
                  flush=True)

    # ---- 自动打一局（全部走真实点击路由）----

    def _play(self, dt):
        ctx = self.ctx
        game = ctx.game
        renderer = ctx.renderer
        if game.scene != "game":
            return
        self._resize_if_due()
        self._snapshot_if_needed(game, renderer)
        self._maybe_skip(game, renderer)
        # 每 6 帧尝试一次操作：给动画留出播放时间（否则一局什么都看不到）。
        if self.frames % 6:
            return
        lock = input_lock.resolve(game, renderer.effects)
        self._trace(lock)
        self._diagnose(game, renderer, lock)
        if lock.layer in ("presentation", "judge", "game_over"):
            return
        if not getattr(game, "local_interaction", True):
            return
        metrics = renderer.metrics
        self.turn_actions += 1
        # 1. 有按钮就点按钮（确认 / 结束回合 / 跳过）
        actions = renderer.actions_for(game)
        for name in ("primary", "secondary"):
            button = actions[name]
            if button.enabled and button.label:
                self._click(pygame.Rect(button.rect).center)
                return
        # 2. 选目标 / 选人
        selection = game.pending_target_selection
        if selection and selection.get("candidates"):
            target = selection["candidates"][0]
            rect = renderer.get_player_panel_rects(game).get(target)
            if rect is not None:
                self._click(rect.center)
                return
        # 3. 选牌：公共池 → 手牌 → 装备
        if game.pending_selection is not None:
            entries = renderer.get_pool_entries(game)
            if entries:
                rects = renderer.get_public_card_rects([card for card, _k in entries])
                if rects:
                    self._click(rects[0].center)
                    return
            rects = renderer.get_card_rects(game.player.hand)
            if rects:
                self._click(self.rng.choice(rects).center)
                return
        # 4. 正常出牌 / 弃牌 / 响应：点一张手牌
        rects = renderer.get_card_rects(game.player.hand)
        if rects:
            self._click(self.rng.choice(rects[:max(1, len(rects))]).center)
            return

    def _diagnose(self, game, renderer, lock):
        """每 3 秒打一行状态：卡住时一眼看出卡在哪一层。"""

        now = time.time()
        if now - self.last_report < 3.0:
            return
        self.last_report = now
        request = getattr(game, "pending_request", None)
        print("acceptance: t=%.0fs phase=%s layer=%s busy=%s pending=%s queue=%d hold=%s" % (
            now - self.started, getattr(game, "phase", "-"), lock.layer,
            getattr(game, "busy", "-"),
            type(request).__name__ if request is not None else "-",
            len(renderer.effects.storyboard.pending),
            renderer.effects.storyboard.holding), flush=True)

    def _maybe_skip(self, game, renderer):
        """演出积压太多时按一次"跳过动画"（等价于玩家按空格）。"""

        board = renderer.effects.storyboard
        self.peak_pending = max(self.peak_pending, len(board.pending))
        if len(board.pending) > 10 or board.fast_forward > 0:
            if renderer.effects.interaction_hold():
                renderer.effects.skip_presentation()
                self.skipped += 1

    # ---- 取证 ----

    def _trace(self, lock):
        try:
            self.trace.append(lock.diagnostics())
        except Exception:                              # noqa: BLE001
            pass
        del self.trace[:-400]

    def _current_show(self, renderer):
        effects = renderer.effects
        parts = []
        panel = effects.judge_panel
        if getattr(panel, "active", False):
            parts.append("判定")
            spec = getattr(panel, "spec", None)
            parts.append(str(getattr(spec, "display_name", "") or ""))
        banner = effects.story_display()
        if banner:
            parts.append(str(banner.get("text", "")))
        turn = effects.turn_display()
        if turn:
            parts.append(str(turn.get("text", "")))
        for item in effects.floats:
            parts.append(str(item.text))
        if effects.skill_banner.timer > 0:
            parts.append("【" + str(effects.skill_banner.skill_name) + "】")
        if effects.identity_flash.timer > 0:
            parts.append("身份" + str(effects.identity_flash.label))
        dying_state = getattr(self.ctx.game, "dying_state", None)
        if callable(dying_state):
            try:
                dying, _rescuer = dying_state()
                if dying is not None:
                    parts.append("濒死")
            except Exception:                          # noqa: BLE001
                pass
        board = effects.storyboard
        if board.current is not None:
            parts.append(str(board.current.describe()))
        return parts

    def _snapshot_if_needed(self, game, renderer):
        if self.shots >= 40:
            return
        parts = self._current_show(renderer)
        text = " ".join(parts)
        key = None
        for token, name in SHOTS.items():
            if token in text and name not in self.seen:
                key = name
                break
        if key is None:
            return
        self.seen.add(key)
        self._shot(game, renderer, key)

    def _shot(self, game, renderer, name):
        try:
            surface = renderer.screen
            path = os.path.join(self.out_dir, "%02d_%s.png" % (self.shots, name))
            pygame.image.save(surface, path)
            self.shots += 1
            print("acceptance: 快门 %s" % path, flush=True)
        except Exception as error:                     # noqa: BLE001
            self.errors.append("shot: %s: %s" % (type(error).__name__, error))

    def _write_report(self):
        ctx = self.ctx
        if ctx is None:
            return
        game = ctx.game
        if getattr(game, "game_over", False) and "result" not in self.seen:
            # 结算画面：所有普通交互关闭 + 全员结果列表（任务 24 的验收点）。
            self.seen.add("result")
            self._shot(game, ctx.renderer, "result")
        effects = ctx.renderer.effects
        report = {
            "frames": self.frames,
            "clicks": self.clicks,
            "shots": self.shots,
            "skipped": self.skipped,
            "peak_pending": self.peak_pending,
            "story_errors": list(getattr(effects, "story_errors", ()))[-5:],
            "client_errors": [e for e in self.errors][-20:],
            "scene": getattr(game, "scene", ""),
            "game_over": bool(getattr(game, "game_over", False)),
            "queue": effects.storyboard.describe(),
            "queue_counts": {
                "pending": len(effects.storyboard.pending),
                "played": effects.storyboard.played,
                "dropped": effects.storyboard.dropped,
            },
            "flights": len(effects.transfers.flights),
            "toasts": effects.toasts.texts(),
            "hand_moving": effects.hand_motion.busy(),
            "speed": getattr(game, "speed", None),
            "kinds_seen": sorted(self.seen),
            "layers_seen": sorted({item.get("layer") for item in self.trace}),
            "log_tail": list(getattr(game, "game_log", ()) or ())[-12:],
            "sizes": self.size_reports,
            "out_of_screen": self.out_of_screen,
        }
        path = os.path.join(self.out_dir, "report.json")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            print("acceptance: 报告 %s" % path, flush=True)
        except OSError as error:
            print("acceptance: 报告写入失败 %s" % error, flush=True)
