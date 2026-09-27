"""批量试玩（soak）：用**真实点击路由**把一局局打完，并按"谁在等谁"分类记录。

和 ``auto_play.py`` 的区别：

* ``auto_play.py`` 在真实窗口里跑一局，用来肉眼验收；
* 本脚本在 SDL dummy 驱动下**批量**跑很多局，用来找"偶发但会卡住玩家"的问题。

两条路径的交互层**完全一样**：点击都走 ``ui.interaction.handle_game_click``
（``main.py`` 处理鼠标点击时的唯一入口），区别只是显示驱动与窗口尺寸。
渲染用的是真实 ``Renderer``——点击命中依赖它构建的 ``table_layout``，所以
每帧都要真画。

# 统计口径（改过一次，报告必须照这里的定义写）

一局跑完只回答两件事：**局面推进了没有**、**点击有没有被吃**。两者不是一个
东西，必须分开记：

    clicks              投出去的总点击数
    effective           点完之后局面状态变了
    consumed_no_effect  点击被路由**收下**（``consumed=True``）但局面没变。
                        "收下"只表示这一下没被闸门拦住；**规则可能已经拒绝
                        了它**（引擎把拒绝写进 game.message 并把动作取消）。
                        所以这类必须单独列，不能算成"有效"。
    blocked             连路由都没进（``consumed=False``），按原因分四类：
        blocked.hold        被演出让路拦下。本机玩家正被问时出现 = **缺陷**；
                            本机没事可做时出现 = 正常（演出还在演）。
        blocked.judge       被 JudgeGate 拦下（判定期间只允许判定自己的输入）
        blocked.busy        动作队列还在播动画时的乱点
        blocked.no_hit      点在了没有东西可点的地方（**策略问题**，不是缺陷）

每局还记录等待分类（帧数），把"正常等待"从"卡住"里分出来：

    waits.judge_logical       规则上判定没走完
    waits.judge_presentation  判定牌还在屏幕中央演
    waits.storyboard          演出队列在播 / 有积压
    waits.presentation_gate   重要演出让路（规则推进在等它演完）
    waits.actions_busy        动作队列在播动画
    waits.others_turn         轮到别人（AI 在决策 / 结算）
    waits.local_input         本机玩家正被要求回答（这时候**必须能点动**）

停止原因只有一个判据：**本机没事可做时绝不判死局**。等别人 / 等演出都算
"正常等待"，只有连续等过头（``WAIT_FRAMES``）才结束，并写成"等待超时"。

用法::

    .venv\\Scripts\\python.exe tools/soak_play.py [局数] [起始种子] [输出文件]

    # 单跑一局（看得见逐步日志）
    .venv\\Scripts\\python.exe tools/soak_play.py 1 7 .cache/soak_one.json
"""

import io
import json
import os
import sys
import time
import traceback
from collections import Counter

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame                                                        # noqa: E402

from src.game import Game                                            # noqa: E402
from src.game.contracts.local_input import local_interaction_slots   # noqa: E402
from src.renderer import Renderer                                    # noqa: E402
from src.ui import player as ui_player                               # noqa: E402
from src.choice import ChoiceOverlay                                # noqa: E402
from src.ui.interaction import _in_interactive_slot, handle_game_click  # noqa: E402

SIZE = (1280, 800)
FRAME = 1.0 / 30.0
#: 一局的帧数上限（30fps × 300 秒）。**这不是产品指标**：放大上限后，
#: 原先"跑满帧数"的局都能正常打完（实测 6180～8425 帧），所以它衡量的是
#: "这一局的演出节奏有多长"，不是"卡住了"。``SOAK_MAX_FRAMES`` 可覆盖。
MAX_FRAMES = int(os.environ.get("SOAK_MAX_FRAMES") or 9000)
#: 连续多少帧没有任何变化就在报告里记一条"无变化"（仅在等别人时也会记）
STUCK_FRAMES = 75
#: 本机玩家正被要求回答时，连续这么多下点击都没效果 → 死局
DEADLOCK_CLICKS = 10
#: 连续这么多帧推不动，且**本机没事可做、也没有在等** → 死局
DEADLOCK_FRAMES = 240
#: 一直在等（演出 / 别人 / 判定）超过这么久 → 记"等待超时"并结束这一局
WAIT_FRAMES = 900
#: 同一张实体牌被"使用完成"这么多次 → 规则没收走牌（死循环的典型形态）
REUSE_LIMIT = 4


class Player:
    """一个只会做"显然该做的事"的玩家：看局面 → 点最该点的地方。"""

    #: 点一次之后等几帧再决策（让状态更新，也避免同一动作连点）
    COOLDOWN = 3

    def __init__(self, screen):
        self.screen = screen
        self.problems = []
        self.seen_problems = set()
        self.clicks = 0
        self.effective = 0
        self.consumed_no_effect = 0
        self.blocked = Counter()
        self.waits = Counter()
        self.asked_frames = 0
        self.bad_clicks = 0
        #: "本机被问"与"无事可做"各自连续了多少帧。它们与"状态多久没变化"
        #: （``same``）不是一回事：演出一结束，``same`` 可能已经几百帧，
        #: 拿它判死局会在演出刚结束的那一帧误杀（实测踩过）。
        self.asked_run = 0
        self.idle_run = 0
        self.max_lag = 0
        self.frames = 0
        self.last_sig = None
        self.same = 0
        self.cooldown = 0
        self.stalled = False
        self.end_reason = ""
        self.end_detail = ""
        self.used_cards = Counter()
        self.reuse_flagged = False
        #: 逐帧日志（``SOAK_TRACE=1``）：调试"为什么这局没打完"用。
        #: 同一局面下"点过但没反应"的动作（label, point）→ 当时的局面指纹。
        self.failed_actions = {}
        self.trace = bool(os.environ.get("SOAK_TRACE"))
        self.trace_from = int(os.environ.get("SOAK_TRACE_FROM") or 0)
        self._trace_prev = None

    # ---- 主循环 ----

    def play(self, seed):
        game, renderer = self._setup(seed)
        self.game = game
        self.renderer = renderer
        self._watch_card_use()
        while self.frames < MAX_FRAMES:
            # 主循环的三步缺一不可（main.py 也是这个顺序）：
            #   game.update    推进引擎与动作队列
            #   renderer.update 推进表现层（演出队列 / FX）——**漏了它，演出队列
            #                   永不消费、演出闸门一直开着、动作队列被压住，
            #                   整局看起来就是"卡死"。这是脚本第一版踩的坑。
            #   renderer.draw  构建布局（点击命中依赖它）
            game.update(FRAME)
            renderer.update(FRAME)
            renderer.draw(game)
            self.choice_overlay.sync_layout(renderer.metrics)
            self.frames += 1
            self._track()
            if game.game_over:
                self.end_reason = "finished"
                break
            if self.stalled:
                break
            if self._handle_choice():
                continue
            self._observe_and_act()
        if not self.end_reason:
            self.end_reason = "frame_cap"
            self.end_detail = "跑满 %d 帧仍未结束" % MAX_FRAMES
            self._record("帧数上限", "frame_cap", self.end_detail)
        return self._result(seed)

    # ---- 构造一局 ----

    def _setup(self, seed):
        game = Game(ai_count=2)
        game.rng.seed(seed)
        renderer = Renderer(self.screen)
        # 二选一浮层由主循环单独持有（main.py 也是这么接的），不在这里接
        # 就会卡在"浮层挡着、点哪都不动"。
        overlay = ChoiceOverlay(self.screen)
        overlay.metrics = renderer.metrics
        self.choice_overlay = overlay
        game.scene = "general_select"
        game.general_candidates = game.roll_general_candidates()
        if not game.general_candidates:
            raise RuntimeError("没有候选武将")
        game.confirm_general(game.general_candidates[0])
        game.scene = "game"
        return game, renderer

    def _watch_card_use(self):
        """同一张实体牌被反复"使用完成" → 规则没收走它（死循环的形态之一）。

        真实案例：曹植【落英】把自己刚用掉的梅花牌收回手里，于是【铁索连环】
        可以无限次使用，整局永远打不完。这种局面靠点击统计看不出来（每次都
        "有效"），只能盯牌。
        """

        from src.game.engine import EventType

        def on_finished(_ctx, event):
            card = event.payload.get("card")
            if card is None:
                return
            if getattr(card, "category", "") == "equipment":
                # 装备牌本来就会在牌局里易主（装备 → 被【顺手牵羊】拿走 →
                # 别人再装备），同一个对象被"使用"多次是正常的。
                return
            key = id(card)
            self.used_cards[key] += 1
            name = getattr(card, "display_name", "?")
            if self.used_cards[key] >= REUSE_LIMIT and not self.reuse_flagged:
                self.reuse_flagged = True
                self._record(
                    "同一张牌被反复使用", "card_reuse",
                    "【%s】已经「使用完成」%d 次仍在牌局里流转（结算没有把它收走）"
                    % (name, self.used_cards[key]))

        dispatcher = getattr(getattr(self.game, "context", None), "events", None)
        if dispatcher is not None:
            dispatcher.subscribe(EventType.CARD_USE_FINISHED, on_finished,
                                 owner=self)

    # ---- 观察 + 分类 ----

    def _sig(self):
        game = self.game
        hand = getattr(game.player, "hand", ())
        selection = getattr(game, "pending_selection", None) or {}
        targets = getattr(game, "pending_target_selection", None) or {}
        request = getattr(game, "pending_request", None)
        # 指纹必须覆盖**所有会影响界面**的状态：漏一项就会把"状态确实变了"
        # 误判成"点击无效"。实测漏过两类：二选一浮层（点手牌→弹确认框）
        # 与目标候选列表（点手牌→进入选目标）。
        return (
            str(game.phase), len(hand), int(game.player.hp),
            str(getattr(game.current_turn_player, "name", "")),
            len(selection.get("selected") or ()),
            len(selection.get("candidates") or ()),
            len(targets.get("selected") or ()),
            len(targets.get("candidates") or ()),
            getattr(request, "request_id", 0),
            bool(getattr(game.response, "active", False)),
            bool(getattr(game.choice, "active", False)),
            bool(getattr(game, "pending_skill_input", None)),
            bool(getattr(game, "pending_card_action", None)),
            bool(getattr(game, "pending_skill_picker", None)),
            bool(getattr(game, "pending_view_as", None)),
            str(getattr(game, "message", "") or ""),
        )

    def _local_asked(self):
        """本机玩家此刻是不是正被要求回答（引擎请求或本地交互槽位）。"""

        return bool(local_interaction_slots(self.game))

    def _local_asked_desc(self):
        return "+".join(local_interaction_slots(self.game)) or "-"

    def _unanswerable_request(self):
        """有没有一条**谁也答不了**的请求（候选为空却要求选至少一张）。

        这种请求是规则/流程缺陷的形态：AI 与真人都无法回答（没有可选项），
        局面只能停在原地。实测两例：【火攻】的目标在结算前失去全部手牌、
        【顺手牵羊】的目标区域在结算前被清空。它们现在都在效果入口就作罢，
        这里留一条常驻探测，将来再冒出同类形状能立刻看见。
        """

        request = getattr(self.game, "pending_request", None)
        if request is None:
            return None
        try:
            from src.game.contracts.local_input import request_targets_player

            if request_targets_player(request, self.game.player):
                return None                       # 问的是本机玩家：那是该我自己答
        except Exception:                                 # noqa: BLE001
            pass
        context = getattr(request, "context", None) or {}
        if "candidates" not in context:
            return None
        if int(getattr(request, "min_cards", 0) or 0) < 1:
            return None
        if list(context.get("candidates") or ()):
            return None
        return "%s/%s（target=%s）" % (
            request.request_type.value, context.get("reason"),
            getattr(getattr(request, "target", None), "name", "?"))

    def _wait_reason(self):
        """本机**不是**被问的那一方时，这一帧在等什么（空串 = 没在等）。"""

        game = self.game
        bits = []
        gate = getattr(game, "judge_gate", None)
        if gate is not None and gate.active:
            bits.append("judge_logical" if not gate.presentation_active
                        else "judge_presentation")
        effects = getattr(self.renderer, "effects", None)
        if effects is not None:
            board = getattr(effects, "storyboard", None)
            if board is not None and board.busy:
                bits.append("storyboard")
                self.max_lag = max(self.max_lag, board.lag)
            block = getattr(game, "presentation_gate", None)
            if block is not None and block.presenting:
                bits.append("presentation_gate")
        if game.busy:
            bits.append("actions_busy")
        if game.current_turn_player is not game.player:
            bits.append("others_turn")
        if not bits and game.pending_request is not None:
            bits.append("pending_other")
        return "+".join(bits)

    def _track(self):
        """每帧记一次等待分类——报告里的"正常等待 vs 卡住"就靠它。"""

        asked = self._local_asked()
        if asked:
            self.asked_frames += 1
            self.asked_run += 1
            self.idle_run = 0
            self.waits["local_input"] += 1
            reason = ""
        else:
            self.asked_run = 0
            reason = self._wait_reason()
            if reason:
                for name in reason.split("+"):
                    self.waits[name] += 1
            else:
                self.idle_run += 1
        if self.trace and self.frames >= self.trace_from:
            sig = self._sig()
            if sig != self._trace_prev or self.frames % 30 == 0:
                self._trace_prev = sig
                print("%5d phase=%-8s asked=%-24s wait=%-40s pending=%s busy=%d "
                      "turn=%s 手牌=%d" % (
                          self.frames, self.game.phase, self._local_asked_desc(),
                          reason or "-", self._pending_desc(),
                          int(bool(self.game.busy)),
                          getattr(self.game.current_turn_player, "name", "?"),
                          len(self.game.player.hand)), flush=True)

    def _handle_choice(self):
        """二选一浮层：与主循环一样单独拦截点击。"""

        if not self.game.choice.active:
            return False
        overlay = self.choice_overlay
        overlay.sync_layout(self.game.metrics if hasattr(self.game, "metrics")
                            else self.renderer.metrics,
                            len(getattr(self.game.choice.current, "options", ()) or ()))
        before = self._sig()
        if self.cooldown > 0:
            self.cooldown -= 1
            return True
        point = None
        if overlay.yes_rect.width > 10:
            point = overlay.yes_rect.center
        elif overlay.option_rects:
            point = pygame.Rect(overlay.option_rects[0]).center
        if point is None:
            return True
        self.clicks += 1
        try:
            overlay.handle_click((int(point[0]), int(point[1])), self.game.choice)
        except Exception:                                 # noqa: BLE001
            self._record("异常(选项)", "choice", traceback.format_exc(limit=4))
        if self._sig() == before:
            self.consumed_no_effect += 1
            self._record("点击无效(选项)", "choice",
                         "二选一点 %s 无变化 phase=%s pending=%s"
                         % (tuple(int(v) for v in point), self.game.phase,
                            self._pending_desc()))
        else:
            self.effective += 1
            self.bad_clicks = 0
        self.cooldown = self.COOLDOWN
        return True

    def _observe_and_act(self):
        if self.cooldown > 0:
            self.cooldown -= 1
            # 冷却期间仍要更新"无变化"计数，否则会把等待当成卡死。
            self._note_sig()
            return
        self._note_sig()

        asked = self._local_asked()
        wait = self._wait_reason()
        if not asked and wait:
            # 不是该我回答、又有东西在跑（演出 / 别人 / 判定）：**等**。
            # 这段期间不点击、不判僵局——"演出还在演"不是卡住。
            stuck_request = self._unanswerable_request()
            if stuck_request is not None:
                self._record("无人能回答的请求", "unanswerable",
                             "有一条谁也答不了的请求：%s（候选为空）phase=%s"
                             % (stuck_request, self.game.phase))
            if self.same and self.same % STUCK_FRAMES == 0:
                self._record("等待中无变化", "no_change",
                             "连续 %d 帧无变化（在等 %s）phase=%s pending=%s 手牌=%d"
                             % (self.same, wait, self.game.phase,
                                self._pending_desc(), len(self.game.player.hand)))
            lag = self._storyboard_lag()
            # 上限随**当前**演出积压增长：队列里压着几十条演出时，光是把它
            # 播完就要几十秒，那不是卡住（每条约 2 秒）。
            ceiling = WAIT_FRAMES + lag * 60
            if self.same >= ceiling:
                self.end_reason = "wait_timeout"
                self.end_detail = "等待 %s 超过 %d 帧仍未推进" % (wait, self.same)
                self._record("等待超时", "wait_timeout",
                             self.end_detail + " phase=%s pending=%s 演出积压=%d"
                             % (self.game.phase, self._pending_desc(), lag))
                self.stalled = True
            return
        # 本机被问（或被轮到自己）时推不动才叫死局。
        if asked:
            if (self.asked_run >= DEADLOCK_FRAMES
                    and self.bad_clicks >= DEADLOCK_CLICKS):
                self.end_reason = "deadlock_local"
                self.end_detail = (
                    "本机正被要求回答（%s）但连续 %d 帧 / %d 次点击都推不动"
                    % (self._local_asked_desc(), self.asked_run, self.bad_clicks))
                self._record("死局(本机被问但点不动)", "deadlock_local",
                             self.end_detail + " phase=%s pending=%s 原因=%s"
                             % (self.game.phase, self._pending_desc(),
                                self._blocked_reason(None)))
                self.stalled = True
                return
        elif (self.idle_run >= DEADLOCK_FRAMES
                and self.bad_clicks >= DEADLOCK_CLICKS):
            # 既没被问、也没在等：轮到自己却什么都做不了 → 死局。
            self.end_reason = "deadlock_idle"
            self.end_detail = ("连续 %d 帧既没有待处理状态也没有任何推进，"
                               "%d 次点击都没效果 phase=%s 手牌=%d"
                               % (self.idle_run, self.bad_clicks, self.game.phase,
                                  len(self.game.player.hand)))
            self._record("死局(无事可等也推不动)", "deadlock_idle",
                         self.end_detail + " pending=%s" % self._pending_desc())
            self.stalled = True
            return
        action = self._decide()
        if action is None:
            return
        self._check_cost_invariant()
        self._click(action)
        self.cooldown = self.COOLDOWN

    def _check_cost_invariant(self):
        """常驻不变量：一次技能费用里不允许出现同一张实体牌两次。

        引擎自己会拦（``skills/activation.py`` 的 preflight 按实体身份去重，
        重复费用返回规则原因且不移动任何牌），这里记的是**界面是怎么凑出
        这份费用的**——真出现时能直接定位到 seed / 技能 / 牌 id。

        只读、无副作用：不修界面状态，也不替脚本改选。
        """

        info = getattr(self.game, "pending_skill_input", None)
        if not info:
            return
        seen = set()
        for card in info.get("cards") or ():
            key = id(card)
            if key in seen:
                self._record(
                    "不变量(技能费用重复)", "duplicate_cost_card",
                    "技能=%s 牌=%s id=%s cards=%s"
                    % (info.get("name"), getattr(card, "name", "?"),
                       getattr(card, "id", "?"),
                       [getattr(item, "id", "?") for item in info.get("cards") or ()]))
                return
            seen.add(key)

    def _note_sig(self):
        sig = self._sig()
        if sig == self.last_sig:
            self.same += 1
        else:
            self.same = 0
        self.last_sig = sig

    def _storyboard_lag(self):
        effects = getattr(self.renderer, "effects", None)
        board = getattr(effects, "storyboard", None)
        return int(getattr(board, "lag", 0) or 0) if board is not None else 0

    def _pending_desc(self):
        game = self.game
        bits = []
        request = getattr(game, "pending_request", None)
        if request is not None:
            bits.append("request=%s/%s" % (
                request.request_type.value, request.context.get("reason")))
        for name in ("pending_selection", "pending_target_selection",
                     "pending_skill_input", "pending_card_action",
                     "pending_skill_picker", "pending_view_as"):
            if getattr(game, name, None):
                bits.append(name.replace("pending_", ""))
        if getattr(game.response, "active", False):
            bits.append("response")
        if getattr(game.choice, "active", False):
            bits.append("choice")
        return "+".join(bits) or "-"

    def _blocked_reason(self, point):
        """这一下点击**没进路由**的原因（判据与 handle_game_click 的顺序一致）。"""

        game = self.game
        renderer = self.renderer
        if game.game_over:
            return "game_over"
        gate = getattr(game, "judge_gate", None)
        if gate is not None and not gate.allows_local_input():
            return "judge=%s" % gate.phase
        effects = getattr(renderer, "effects", None)
        if effects is not None and effects.interaction_hold():
            board = getattr(effects, "storyboard", None)
            which = "storyboard" if (board is not None and board.holds_interaction()) else "gate"
            return "hold(%s)" % which
        if game.busy and not _in_interactive_slot(game):
            return "busy"
        return "no_hit"

    # ---- 决策 ----

    def _decide(self):
        game = self.game
        renderer = self.renderer
        if renderer.table_layout is None or renderer.table_layout.game is not game:
            return None

        if getattr(game, "pending_skill_picker", None):
            rects = list(getattr(renderer.skill_picker, "rects", ()) or ())
            if rects:
                return ("选技能", pygame.Rect(rects[0]).center)

        if getattr(game, "pending_card_action", None):
            rects = list(getattr(renderer.action_picker, "rects", ()) or ())
            if rects:
                return ("选出牌方式", pygame.Rect(rects[0]).center)

        if getattr(game.choice, "active", False):
            # 二选一浮层由 Renderer 持有；不接它就会落在"结束回合"上死循环
            # （浮层挡着，按钮点不动）。实测踩过这个坑。
            overlay = getattr(renderer, "choice_overlay", None)
            if overlay is not None and overlay.yes_rect.width > 10:
                return ("确认选项", overlay.yes_rect.center)
            return None

        if getattr(game.response, "active", False):
            usable = self._usable()
            if usable:
                # 先点"直接能打出去"的那张；只能靠技能转化来响应的（龙魂 /
            # 武圣一类）改点技能栏——真人也是这么做的，引擎的提示会告诉
                # 玩家"需要在「发动技能」里选择后才能使用"。
                for index in sorted(usable):
                    card = game.player.hand[index]
                    if self._directly_usable(card):
                        return ("响应出牌", self._point_of(index))
                point = self._skill_bar_point()
                if point is not None:
                    return ("发动技能响应", point)
            return ("不出", pygame.Rect(renderer.secondary_button.rect).center)

        if getattr(game, "pending_selection", None) is not None:
            return self._decide_selection()

        if getattr(game, "pending_target_selection", None) is not None:
            return self._decide_targets()

        if getattr(game, "pending_view_as", None) is not None:
            return self._decide_view_as()

        if getattr(game, "pending_skill_input", None) is not None:
            return self._decide_skill_input()

        if (game.current_turn_player is game.player and game.phase == "play"
                and not game.busy):
            return self._decide_play()

        if (game.current_turn_player is game.player and game.phase == "discard"
                and not game.busy):
            return self._decide_discard()

        return None

    def _usable(self):
        game = self.game
        usable = ui_player.respondable_hand_indices(game)
        if usable is None:
            usable = ui_player.playable_hand_indices(game)
        return set(usable or ())

    def _point_of(self, index):
        rect = self.renderer.table_layout.hand_rect(index)
        return pygame.Rect(rect).center if rect is not None else None

    def _decide_selection(self):
        game = self.game
        selection = game.pending_selection
        need = int(selection.get("number") or 0)
        picked = len(selection.get("selected") or ())
        # 候选画在哪里由 zone 决定：池子里的候选点池子那一份（即使它同时也
        # 是本机的手牌——【补益】展示自己的手牌时两者是同一张牌）。
        prefer_pool = str(selection.get("zone") or "") in ("public_pool", "selection_pool")
        if picked < max(1, need):
            chosen = {id(card) for card, _r, _k in selection.get("selected", ())}
            for card in [c for c, _k in selection.get("candidates", ())]:
                if id(card) in chosen:
                    continue
                point = self._card_point(card, prefer_pool=prefer_pool)
                if point is not None and not self._failed("选牌", point):
                    return ("选牌", point)
        return None

    def _decide_targets(self):
        selection = self.game.pending_target_selection
        picked = len(selection.get("selected") or ())
        minimum = int(selection.get("minimum") or 1)
        if picked < minimum:
            for candidate in selection.get("candidates", ()):
                if candidate in selection.get("selected", ()):
                    continue
                rect = self._seat_point(candidate)
                if rect is not None:
                    return ("选目标", rect)
            return None
        return ("确认目标", pygame.Rect(self.renderer.primary_button.rect).center)

    def _seat_point(self, player):
        """这个角色在屏幕上的可点位置。

        **真人自己不在 ``seat_rects`` 里**——对手走座位面板、自己走底部状态条，
        所以"目标是自己"必须单独处理，否则会退化成"点不动"。
        """

        rect = self.renderer.table_layout.seat_rect(player)
        if rect is not None:
            return pygame.Rect(rect).center
        if player is self.game.player:
            status = self.renderer.metrics.player_status
            return pygame.Rect(status).center
        return None

    def _decide_skill_input(self):
        game = self.game
        renderer = self.renderer
        info = game.pending_skill_input
        view_as = getattr(game, "pending_view_as", None)
        if view_as:
            point = self._card_point(view_as[0])
            if point is not None:
                return ("技能来源牌", point)
        # 费用牌：一张一张点候选（手牌或装备区的牌，走与真人同一条点击路由）。
        # 以前这里不点费用，所以带费用的主动技在试玩里**从来没被发动过**——
        # 制衡 / 举荐这类"弃牌换收益"的路径一直没被跑到。
        need = self._cost_need(info)
        if len(info.get("cards") or ()) < need:
            for card in info.get("cost_candidates") or ():
                if any(item is card for item in info.get("cards") or ()):
                    continue
                point = self._card_point(card, prefer_pool=False)
                if point is not None and not self._failed("技能费用牌", point):
                    return ("技能费用牌", point)
        targets = list(info.get("targets") or ())
        if targets and info.get("target") is None:
            point = self._seat_point(targets[0])
            if point is not None:
                return ("技能目标", point)
        if renderer.primary_button.enabled:
            return ("确认发动技能", pygame.Rect(renderer.primary_button.rect).center)
        return None

    @staticmethod
    def _cost_need(info):
        """这次发动准备挑几张费用牌（脚本自己的策略，不是规则）。

        规则上限由 ``max_cost_cards`` 给出；不可变的费用按张数凑齐即可。
        试玩只挑**够用的最小张数**（最多两张）：弃光手牌会让这一局没得打，
        那不是要找的问题。
        """

        cost = int(info.get("cost_cards") or 0)
        if not info.get("variable_cost"):
            return cost
        cap = int(info.get("max_cost_cards") or 0)
        available = len(info.get("cost_candidates") or ())
        limit = min(available, cap) if cap else available
        return max(1, min(limit, 2))

    def _decide_play(self):
        game = self.game
        renderer = self.renderer
        end_turn = ("结束回合", pygame.Rect(renderer.primary_button.rect).center)
        usable = self._usable()
        if not usable:
            return end_turn
        # 先点**直接能用**的牌：只能靠技能转化的牌（丈八蛇矛一类）点了不会
        # 有反应，引擎只会提示"需要在「发动技能」里选择后使用"。
        for index in sorted(usable):
            card = game.player.hand[index]
            if self._directly_usable(card):
                point = self._point_of(index)
                if point is not None:
                    return ("出牌", point)
        # 手上全是转化牌：点已装备的、赋予视为技的武器（丈八蛇矛 = 两张手牌当杀）
        point = self._granted_weapon_point()
        if point is not None and not self._failed("发动武器技能", point):
            return ("发动武器技能", point)
        return end_turn

    def _directly_usable(self, card):
        """这张牌现在"普通点击"就能用吗（不含技能转化）。

        响应窗口里同样成立：查询用的是**当前场合**的上下文（出牌 / 响应），
        所以"这张【杀】能不能当【闪】打出去"这种问题由引擎回答，脚本不猜。
        """

        game = self.game
        try:
            context = game.current_card_action_context(allow_busy=True)
            if context is None:
                return False
            return bool(game.card_action_options(card, context, allow_busy=True))
        except Exception:                                 # noqa: BLE001
            return False

    def _skill_bar_point(self):
        """技能栏里一个**现在真能发动**的技能按钮位置（没有则 None）。"""

        bar = getattr(self.renderer, "skill_bar", None)
        if bar is None:
            return None
        try:
            bar.sync(self.game)
        except Exception:                                 # noqa: BLE001
            return None
        for index, rect in enumerate(bar.rects):
            if index >= len(bar.skills):
                break
            if bar.skills[index].id in bar.enabled_ids:
                return pygame.Rect(rect).center
        return None

    def _granted_weapon_point(self):
        """装备区里**现在真能发动**的视为技装备的位置（没有则 None）。

        能不能发动由引擎回答（``game.view_as_options``）——手上只有 1 张牌时
        丈八蛇矛发动不了，脚本再点一百次也不会有反应（实测踩过这个循环）。
        """

        from src.game.equipment_skills.granted import granted_skill_id

        game = self.game
        allowed_ids = set()
        try:
            for skill_id, allowed, _reason in game.view_as_options():
                if allowed:
                    allowed_ids.add(skill_id)
        except Exception:                                 # noqa: BLE001
            return None
        rects = self.renderer.player_equipment_slot_rects(game)
        for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
            card = game.player.get_equipment(slot)
            skill_id = granted_skill_id(getattr(card, "name", None))
            if skill_id is None or skill_id not in allowed_ids:
                continue
            rect = rects.get(slot)
            if rect is not None:
                return pygame.Rect(rect).center
        return None

    # ---- "刚才那一下没用"的记忆（只在同一局面下生效）----
    #
    # 一个动作点下去没反应时，同一局面下再点同一个地方仍然不会有反应。
    # 没有这份记忆，脚本就会原地复读（实测：【丈八蛇矛】凑不出 2 张牌时
    # 反复点武器 58 次，"死局"其实是脚本自己造的）。

    def _context_key(self):
        return (str(self.game.phase), len(self.game.player.hand),
                self._pending_desc(), str(self.game.current_turn_player.name
                                          if self.game.current_turn_player else ""))

    def _failed(self, label, point):
        entry = self.failed_actions.get((label, tuple(int(v) for v in point)))
        return entry is not None and entry == self._context_key()

    def _remember_failure(self, label, point):
        self.failed_actions[(label, tuple(int(v) for v in point))] = self._context_key()

    def _decide_view_as(self):
        """视为技正在选来源牌：一张一张点（收齐后自动进入下一步）。

        来源已经选齐、但这条组合用不了（"本回合已经出过【杀】"一类）时，
        引擎会把原因写在提示里并把界面留在原地——此时**只有「取消技能」能
        退出**。脚本不点它就会永远停在这里（实测：6000 帧只点了 8 下）。
        """

        game = self.game
        session = game.pending_view_as
        chosen = {id(card) for card in session.selected_source_cards}
        for option in session.candidates:
            for card in option.source_cards:
                if id(card) in chosen:
                    continue
                point = self._card_point(card)
                if point is not None and not self._failed("视为技来源牌", point):
                    return ("视为技来源牌", point)
        return ("取消技能",
                pygame.Rect(self.renderer.secondary_button.rect).center)

    def _decide_discard(self):
        game = self.game
        renderer = self.renderer
        need = len(game.player.hand) - game.hand_limit(game.player)
        if need > 0:
            rect = renderer.table_layout.hand_rect(len(game.player.hand) - 1)
            if rect is not None:
                return ("弃牌", pygame.Rect(rect).center)
        return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)

    def _card_point(self, card, *, prefer_pool=False):
        """牌在屏幕上的可点位置（池子按整池排布算）。

        ``prefer_pool=True`` 时先找池子：选牌界面的候选画在池子里，点手牌
        上那一份在部分调用点上不会生效。
        """

        game = self.game
        renderer = self.renderer
        if prefer_pool:
            point = self._pool_point(card)
            if point is not None:
                return point
        for index, item in enumerate(game.player.hand):
            if item is card:
                rect = renderer.table_layout.hand_rect(index)
                if rect is not None:
                    return pygame.Rect(rect).center
        return self._pool_point(card)

    def _pool_point(self, card):
        """这张牌在公共牌池里的位置（不在池子里则 None）。"""

        game = self.game
        renderer = self.renderer
        try:
            entries = renderer.get_pool_entries(game)
            cards = [item for item, _k in entries]
            rects = renderer.get_public_card_rects(cards)
        except Exception:                                 # noqa: BLE001
            return None
        for slot, (item, _k) in enumerate(entries):
            if item is card and slot < len(rects):
                return pygame.Rect(rects[slot]).center
        return None

    # ---- 点击 + 验证 ----

    def _record(self, kind, label, detail):
        """同类同因的问题只记一次（去重），计数另外统计。"""

        key = (kind, label, detail[:120])
        if key in self.seen_problems:
            return
        self.seen_problems.add(key)
        self.problems.append({"kind": kind, "label": label, "detail": detail})

    def _click(self, action):
        label, point = action
        if point is None:
            return
        before = self._sig()
        asked = self._local_asked()
        self.clicks += 1
        try:
            consumed = handle_game_click((int(point[0]), int(point[1])),
                                        self.game, self.renderer)
        except Exception:                                 # noqa: BLE001
            self._record("异常(点击)", label, traceback.format_exc(limit=4))
            return
        after = self._sig()
        if after != before:
            self.effective += 1
            self.bad_clicks = 0
            return
        self.bad_clicks += 1
        self._remember_failure(label, point)
        if not consumed:
            reason = self._blocked_reason(point)
            key = reason.split("=")[0].split("(")[0]
            self.blocked[key] += 1
            if asked:
                # 本机正被要求回答，点击却被拦住 —— 这是缺陷，不是策略问题。
                self._record("点击被拦(本机被问)", label,
                             "%s 点 %s consumed=False 原因=%s 被问=%s phase=%s pending=%s"
                             % (label, tuple(int(v) for v in point), reason,
                                self._local_asked_desc(), self.game.phase,
                                self._pending_desc()))
        else:
            self.consumed_no_effect += 1
            self._record("点击被收下但没效果", label,
                         "%s 点 %s consumed=True 但局面没变 phase=%s pending=%s 被问=%s 引擎提示=%s"
                         % (label, tuple(int(v) for v in point), self.game.phase,
                            self._pending_desc(), self._local_asked_desc(),
                            str(getattr(self.game, "message", "") or "")[:60]))

    # ---- 收尾 ----

    def _result(self, seed):
        game = self.game
        return {
            "seed": seed,
            "frames": self.frames,
            "clicks": self.clicks,
            "effective": self.effective,
            "consumed_no_effect": self.consumed_no_effect,
            "blocked": dict(self.blocked),
            "ineffective": self.consumed_no_effect,   # 旧字段名，含义同上
            "waits": dict(self.waits),
            "max_storyboard_lag": self.max_lag,
            "over": bool(game.game_over),
            "stalled": bool(self.stalled),
            "end_reason": self.end_reason,
            "end_detail": self.end_detail,
            "phase_end": str(game.phase),
            "winner": str(getattr(game.winner, "name", "") or ""),
            "survivors": sum(1 for p in game.players if p.alive),
            "problems": self.problems,
        }


def classify(record):
    """这一局为什么没打完（报告口径）。

    product_input   本机被问却点不动 / 点击被收下但没效果 → 输入链路缺陷
    product_rules   同一张牌被反复使用（结算没收走牌）→ 规则缺陷
    normal_wait     一直在等演出 / 等别人，等到上限 —— 属于节奏问题
    strategy        点了没有可点的地方（脚本自己点错）
    deadlock_idle   既没被问也没在等，却什么都推不动
    frame_cap       跑满帧数上限仍未结束
    finished        分出胜负
    """

    if record.get("over"):
        return "finished"
    reason = record.get("end_reason") or ""
    kinds = {item.get("label") for item in record.get("problems", ())}
    if "card_reuse" in kinds or "unanswerable" in kinds:
        return "product_rules"
    if "deadlock_local" in kinds or "点击被收下但没效果" in kinds:
        return "product_input"
    if reason in ("deadlock_local", "deadlock_idle"):
        return "product_input" if reason == "deadlock_local" else "deadlock_idle"
    if reason == "wait_timeout":
        if record.get("blocked", {}).get("no_hit", 0) > record.get("clicks", 0) * 0.5:
            return "strategy"
        return "normal_wait"
    if reason == "frame_cap":
        return "frame_cap"
    return reason or "unknown"


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    out = sys.argv[3] if len(sys.argv) > 3 else "docs/reports/assets/phase18_5_ui/soak.json"
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode(SIZE)
    rounds = []
    started = time.time()
    for offset in range(count):
        seed = start + offset
        player = Player(screen)
        try:
            record = player.play(seed)
        except Exception:                                 # noqa: BLE001
            record = {"seed": seed, "over": False, "crashed": True,
                      "end_reason": "crash", "end_detail": traceback.format_exc(limit=6),
                      "problems": [{"kind": "异常(整局)", "label": "crash",
                                    "detail": traceback.format_exc(limit=6)}],
                      "clicks": 0, "ineffective": 0, "blocks": {},
                      "frames": player.frames}
        record["outcome"] = classify(record)
        rounds.append(record)
        if (offset + 1) % 10 == 0:
            print("  完成 %d/%d（%.0f 秒）" % (offset + 1, count, time.time() - started),
                  flush=True)
    kinds = Counter()
    outcomes = Counter()
    blocked = Counter()
    for item in rounds:
        for problem in item.get("problems", ()):
            kinds[problem["kind"]] += 1
        outcomes[item.get("outcome", "?")] += 1
        for name, number in (item.get("blocked") or {}).items():
            blocked[name] += number
    summary = {
        "rounds": len(rounds),
        "seconds": round(time.time() - started, 1),
        "frames": sum(r.get("frames", 0) for r in rounds),
        "total_clicks": sum(r.get("clicks", 0) for r in rounds),
        "total_effective": sum(r.get("effective", 0) for r in rounds),
        "total_consumed_no_effect": sum(r.get("consumed_no_effect", 0) for r in rounds),
        "total_blocked": sum(blocked.values()),
        "blocked_by": dict(blocked),
        "finished": sum(1 for r in rounds if r.get("over")),
        "stalled": sum(1 for r in rounds if r.get("stalled")),
        "crashed": sum(1 for r in rounds if r.get("crashed")),
        "outcomes": dict(outcomes),
        "problem_kinds": dict(kinds),
    }
    target = os.path.join(ROOT, out)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with io.open(target, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rounds": rounds}, handle,
                  ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("写出", target, flush=True)


if __name__ == "__main__":
    main()
