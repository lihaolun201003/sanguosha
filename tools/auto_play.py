"""自动真人：在**真实窗口**里用真实鼠标事件玩完整局，并报告交互问题。

与 ``ui_scenes.py`` 的区别：那个是"按剧本走固定几步"，这个是一个**会玩的
玩家**——它每一帧观察局面（相当于玩家看到的东西），决定要做什么，把动作
转成**真实坐标点击**投进真实事件队列，然后**验证点击是否生效**。

它能抓到的问题类型：

* **点了没反应**：投了点击，局面没有任何变化；
* **该有的元素看不见**：提示条说"请出【桃】"，但手上没有任何可点的牌；
* **视觉与语义不符**：牌显示成可出（绿边）但点了没反应，或反之；
* **卡住**：连续多步没有推进（可能有面板挡着、或状态机漏了一条路径）。

用法::

    set SGS_RUNTIME_SCRIPT=auto_play
    .venv\\Scripts\\python.exe main.py

输出：``docs/reports/assets/phase18_5_ui/auto_play.json``（逐步日志 + 问题清单）。
"""

import json
import os
import time

import pygame

#: 同时推进的两条线：我方与 AI。跑满这么多步或对局结束就收工。
MAX_STEPS = 400
#: 点一次之后等多少帧再观察（真实窗口 60fps，20 帧≈0.33 秒）
SETTLE = 20


def _slot_of(ctx):
    """当前是不是"玩家自己的交互槽位"（与 handle_game_click 同一判据）。"""

    from src.ui.interaction import _in_interactive_slot

    try:
        return _in_interactive_slot(ctx.game)
    except Exception:                                     # noqa: BLE001
        return "?"


class Hook:
    def __init__(self):
        self.ctx = None
        self.stage = 0
        self.timer = 0
        self.pending = None
        self.steps = []
        self.problems = []
        self.done = False
        self.last_sig = None
        self.same_sig_count = 0
        self.step_count = 0
        self.started = time.time()
        self.out = ""
        self.tries_on_sig = 0

    # ---- 生命周期 ----

    def bind_environment(self, env):
        self.out = (env.get("SGS_AUTOPLAY_OUT")
                    or "docs/reports/assets/phase18_5_ui/auto_play.json").strip()
        return self

    def bind(self, context):
        self.ctx = context
        return self

    def finish(self):
        self._write()

    def _write(self):
        payload = {
            "steps": self.steps,
            "problems": self.problems,
            "elapsed_s": round(time.time() - self.started, 1),
        }
        target = os.path.join(self.ctx_root(), self.out) if self.ctx else self.out
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            print("写出", target, flush=True)
        except Exception as error:                        # noqa: BLE001
            print("写日志失败:", error, flush=True)
        for item in self.problems:
            print("  !! [%s] %s" % (item["kind"], item["detail"]), flush=True)

    def ctx_root(self):
        # tools/auto_play.py → 仓库根（两层 dirname）
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ---- 每帧 ----

    def step(self, dt):
        if self.ctx is None or self.done:
            return False
        try:
            return self._step(dt)
        except Exception:                                # noqa: BLE001
            import traceback

            self.problems.append({"kind": "脚本异常",
                                  "detail": traceback.format_exc(limit=6)})
            self.done = True
            return "quit"

    def _step(self, dt):
        if self.pending is not None:
            self.timer -= 1
            if self.timer <= 0:
                action, self.pending = self.pending, None
                action()
            return False
        if self.stage == 0:
            self._boot_menu()
            return False
        if self.step_count >= MAX_STEPS or self.ctx.game.game_over:
            self.done = True
            return "quit"
        self._tick()

    # ==================================================
    # 启动：把窗口送到牌桌（这几步是重复劳动）
    # ==================================================

    def _click(self, position):
        rect = self.ctx.screen.get_rect()
        x = max(rect.left, min(int(position[0]), rect.right - 1))
        y = max(rect.top, min(int(position[1]), rect.bottom - 1))
        for event in (
            pygame.event.Event(pygame.MOUSEMOTION,
                               {"pos": (x, y), "rel": (0, 0), "buttons": (0, 0, 0)}),
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (x, y), "button": 1}),
            pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (x, y), "button": 1}),
        ):
            pygame.event.post(event)
        return (x, y)

    def _later(self, delay, action):
        self.pending = action
        self.timer = max(1, delay)

    def _boot_menu(self):
        menu = self.ctx.start_menu
        game = self.ctx.game
        menu.sync_layout(self.ctx.renderer.metrics)
        menu.sync_modes(game.modes.list_modes())
        ffa = menu.mode_buttons.get("ffa")
        if ffa is None:
            return
        self.stage = 1
        self._click(ffa.rect.center)
        self._later(SETTLE, self._boot_2)

    def _boot_2(self):
        self._click(self.ctx.start_menu.start_button.rect.center)
        self._later(SETTLE * 2, self._boot_3)

    def _boot_3(self):
        cards = getattr(self.ctx.general_select, "card_rects", None)
        if cards:
            self._click(pygame.Rect(cards[0]).center)
        self._later(SETTLE, self._boot_4)

    def _boot_4(self):
        self._click(self.ctx.general_select.confirm_button.rect.center)
        self._later(SETTLE * 2, self._boot_5)

    def _boot_5(self):
        self._later(SETTLE * 3, self._started)

    def _started(self):
        self.stage = 2
        self._log("开局完成", "scene=%s" % self.ctx.game.scene)

    # ==================================================
    # 玩游戏
    # ==================================================

    def _sig(self):
        """当前局面的"指纹"：用来判断上一次点击有没有生效。"""

        game = self.ctx.game
        hand = getattr(game.player, "hand", ())
        selection = getattr(game, "pending_selection", None) or {}
        targets = getattr(game, "pending_target_selection", None) or {}
        request = getattr(game, "pending_request", None)
        return (
            str(game.scene), str(game.phase),
            len(hand), int(game.player.hp),
            str(getattr(game.current_turn_player, "name", "")),
            len(selection.get("selected") or ()),
            len(selection.get("candidates") or ()),
            len(targets.get("selected") or ()),
            getattr(request, "request_id", 0),
            str(getattr(game.response, "active", False)),
            bool(getattr(game, "pending_skill_input", None)),
            bool(getattr(game, "pending_card_action", None)),
            bool(getattr(game, "pending_skill_picker", None)),
        )

    def _tick(self):
        if self.pending is not None:
            return
        game = self.ctx.game
        sig = self._sig()
        if sig == self.last_sig:
            self.same_sig_count += 1
        else:
            self.same_sig_count = 0
            self.tries_on_sig = 0
        self.last_sig = sig
        if self.same_sig_count == 90 and self.stage == 2:
            # 连续 1.5 秒没有任何变化：要么在等 AI，要么卡住了。
            waiting = self._is_waiting_for_others()
            if not waiting:
                self.problems.append({
                    "kind": "疑似卡住",
                    "detail": "连续 1.5 秒局面无变化，phase=%s pending=%s 手牌=%d"
                              % (game.phase, self._pending_desc(), len(game.player.hand)),
                })
            self.same_sig_count = 0
        action = self._decide()
        if action is None:
            return
        self.step_count += 1
        self._perform(action)

    def _pending_desc(self):
        game = self.ctx.game
        bits = []
        request = getattr(game, "pending_request", None)
        if request is not None:
            bits.append("request=%s/%s" % (
                request.request_type.value, request.context.get("reason")))
        if getattr(game, "pending_selection", None):
            bits.append("selection")
        if getattr(game, "pending_target_selection", None):
            bits.append("targets")
        if getattr(game.response, "active", False):
            bits.append("response")
        if getattr(game.choice, "active", False):
            bits.append("choice")
        if getattr(game, "pending_skill_input", None):
            bits.append("skill_input")
        if getattr(game, "pending_card_action", None):
            bits.append("card_action")
        if getattr(game, "pending_skill_picker", None):
            bits.append("skill_picker")
        return "+".join(bits) or "-"

    def _is_waiting_for_others(self):
        game = self.ctx.game
        if game.pending_request is not None:
            return False
        if game.current_turn_player is not game.player:
            return True
        if game.busy:
            return True
        return False

    # ---- 决策（像人一样：看界面提示 + 看哪里能点）----

    def _decide(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        if game.game_over:
            return None
        if renderer.table_layout is None or renderer.table_layout.game is not game:
            return None

        # 1) 技能选择面板（模态）：点第一个技能
        if getattr(game, "pending_skill_picker", None):
            rects = list(getattr(renderer.skill_picker, "rects", ()) or ())
            if rects:
                return ("选技能", pygame.Rect(rects[0]).center)

        # 2) 出牌方式面板：点第一个方式
        if getattr(game, "pending_card_action", None):
            rects = list(getattr(renderer.action_picker, "rects", ()) or ())
            if rects:
                return ("选出牌方式", pygame.Rect(rects[0]).center)

        # 3) 二选一（选择浮层）
        if getattr(game.choice, "active", False):
            overlay = self.ctx.choice_overlay
            if overlay is not None and overlay.yes_rect.width > 10:
                return ("确认选项", tuple(overlay.yes_rect.center))

        # 4) 响应窗口：优先出能响应的牌，否则"不出"
        if getattr(game.response, "active", False):
            usable = self._usable_hand_indices()
            if usable:
                rect = renderer.table_layout.hand_rect(sorted(usable)[0])
                if rect is not None:
                    return ("响应出牌", rect.center)
            # 没有可用的牌：点"不出"
            return ("不出", pygame.Rect(renderer.secondary_button.rect).center)

        # 5) 选牌窗口（弃牌 / 制衡 / 五谷 / 火攻…）
        if getattr(game, "pending_selection", None) is not None:
            return self._decide_selection()

        # 6) 选目标窗口
        if getattr(game, "pending_target_selection", None) is not None:
            return self._decide_targets()

        # 7) 技能输入（需要选来源牌 / 目标）
        if getattr(game, "pending_skill_input", None) is not None:
            return self._decide_skill_input()

        # 8) 我的出牌阶段
        if (game.current_turn_player is game.player and game.phase == "play"
                and not game.busy):
            return self._decide_play()

        # 9) 我的弃牌阶段
        if (game.current_turn_player is game.player and game.phase == "discard"
                and not game.busy):
            return self._decide_discard()

        return None

    def _usable_hand_indices(self):
        """界面上显示成"可以用"的手牌（读的是渲染层拿去高亮的那个集合）。"""

        renderer = self.ctx.renderer
        game = self.ctx.game
        from src.ui import player as ui_player

        usable = ui_player.respondable_hand_indices(game)
        if usable is None:
            usable = ui_player.playable_hand_indices(game)
        return set(usable or ())

    def _decide_selection(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        selection = game.pending_selection
        need = int(selection.get("number") or 0)
        picked = len(selection.get("selected") or ())
        candidates = [card for card, _key in selection.get("candidates", ())]
        if picked < max(1, need) and candidates:
            selected_cards = {id(card)
                              for card, _rect, _key in selection.get("selected", ())}
            pending = [card for card in candidates if id(card) not in selected_cards]
            if pending:
                rect = self._card_click_point(pending[0])
                if rect is not None:
                    return ("选牌", rect)
        # 选够了：选牌窗口是"点中即选"，没有确认按钮；若还需要选择但
        # 找不到可点的牌，就报告出来（这本身就是界面问题）。
        if picked < max(1, need):
            return None
        return None

    def _decide_targets(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        selection = game.pending_target_selection
        picked = len(selection.get("selected") or ())
        minimum = int(selection.get("minimum") or 1)
        if picked < minimum:
            for candidate in selection.get("candidates", ()):
                if candidate in selection.get("selected", ()):
                    continue
                rect = renderer.table_layout.seat_rect(candidate)
                if rect is not None:
                    return ("选目标", pygame.Rect(rect).center)
            return None
        return ("确认目标", pygame.Rect(renderer.primary_button.rect).center)

    def _decide_skill_input(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        info = game.pending_skill_input
        # 先点来源牌（如果有候选）
        candidates = list(getattr(game, "pending_view_as", None) or ()) or []
        if candidates:
            card = candidates[0]
            rect = renderer.table_layout.hand_rect(self._hand_index(card))
            if rect is not None:
                return ("技能来源牌", rect.center)
        # 再点目标
        targets = list(info.get("targets") or ())
        if targets and info.get("target") is None:
            rect = renderer.table_layout.seat_rect(targets[0])
            if rect is not None:
                return ("技能目标", pygame.Rect(rect).center)
        return ("确认发动技能", pygame.Rect(renderer.primary_button.rect).center)

    def _decide_play(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        usable = self._usable_hand_indices()
        if not usable:
            # 没有能出的牌：结束回合
            return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)
        index = sorted(usable)[0]
        rect = renderer.table_layout.hand_rect(index)
        if rect is None:
            return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)
        # 已经点了牌、正在选目标：交给选目标分支
        if getattr(game, "pending_target_selection", None) is not None:
            return None
        self._current_play_index = index
        return ("出牌", rect.center)

    def _decide_discard(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        need = len(game.player.hand) - game.hand_limit(game.player)
        if need > 0:
            rect = renderer.table_layout.hand_rect(len(game.player.hand) - 1)
            if rect is not None:
                return ("弃牌", rect.center)
        return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)

    def _card_click_point(self, card):
        """这张牌在屏幕上的可点位置：先找手牌，再找公共牌池。

        等于玩家"在哪儿看见它"——手牌区找不到就去公共牌区找，两处都没有
        就返回 None（说明界面没把这张候选牌画出来，本身就是个问题）。

        注意：``get_public_card_rects`` 是**按传入列表的数量与顺序**散开居中
        排布的，传单张牌会算成"只有它一张时居中"的位置。必须先把整池的牌传
        进去、再取第 i 个——这与 ``interaction.handle_game_click`` 里命中测试
        的取法一致，否则算出的点和真正能点的区域差半张牌。
        """

        renderer = self.ctx.renderer
        index = self._hand_index(card)
        if index >= 0:
            rect = renderer.table_layout.hand_rect(index)
            if rect is not None:
                return tuple(pygame.Rect(rect).center)
        try:
            entries = renderer.get_pool_entries(self.ctx.game)
            cards = [item for item, _key in entries]
            rects = renderer.get_public_card_rects(cards)
        except Exception:                                 # noqa: BLE001
            return None
        for slot, (item, _key) in enumerate(entries):
            if item is card and slot < len(rects):
                return tuple(pygame.Rect(rects[slot]).center)
        return None

    def _hand_index(self, card):
        for index, item in enumerate(self.ctx.game.player.hand):
            if item is card:
                return index
        return -1

    # ---- 执行 + 验证 ----

    def _perform(self, action):
        label, position = action
        if position is None:
            self.problems.append({"kind": "无法定位元素", "detail": label})
            self._later(SETTLE, lambda: None)
            return
        before = self._sig()
        point = self._click(position)
        self._log(label, "点 %s at %s" % (label, point))

        def verify():
            after = self._sig()
            changed = after != before
            self.steps[-1]["changed"] = changed
            if not changed:
                self.tries_on_sig += 1
                if self.tries_on_sig <= 2:
                    self.steps[-1]["why"] = self._why_swallowed(point)
                    self.problems.append({
                        "kind": "点了没反应",
                        "detail": "%s 点 (%d,%d) 之后局面无变化（phase=%s pending=%s 原因=%s）"
                                  % (label, point[0], point[1],
                                     self.ctx.game.phase, self._pending_desc(),
                                     self.steps[-1]["why"]),
                    })

        self._later(SETTLE, verify)

    def _blockers(self):
        """点击可能被谁吞掉（诊断用）。

        ``handle_game_click`` 的前两道闸门都会被静默吞掉点击。把它们记下来，
        才能区分"点了没反应"是游戏缺陷还是"演出正在让路"。
        """

        game = self.ctx.game
        effects = self.ctx.renderer.effects
        gate = getattr(game, "presentation_gate", None)
        bits = []
        try:
            if effects.interaction_hold():
                if effects.storyboard.holds_interaction():
                    step = effects.storyboard.current
                    bits.append("hold:step=%s" % getattr(step, "kind", "?"))
                if gate is not None and gate.holds_local_input():
                    bits.append("hold:gate=%s" % "/".join(gate.kinds() or ("?",)))
        except Exception:                                 # noqa: BLE001
            bits.append("hold:?")
        if game.busy:
            bits.append("busy")
        if not game.judge_gate.allows_local_input():
            bits.append("judge=%s" % game.judge_gate.phase)
        return ",".join(bits) or "-"

    def _why_swallowed(self, point):
        """点击落到了哪里、被谁挡住（诊断用，只读）。"""

        game = self.ctx.game
        renderer = self.ctx.renderer
        bits = []
        try:
            bits.append("hold=%s" % (
                "step:" + str(getattr(self.ctx.renderer.effects.storyboard.current, "kind", "?"))
                if self.ctx.renderer.effects.interaction_hold() else "no"))
        except Exception:                                 # noqa: BLE001
            bits.append("hold=?")
        bits.append("busy=%s" % game.busy)
        try:
            actions = renderer.actions_for(game)
            for key in ("primary", "secondary"):
                item = actions.get(key)
                if item is None:
                    continue
                bits.append("%s(%s,en=%s,%s)" % (
                    key, getattr(item, "action", "?"), getattr(item, "enabled", "?"),
                    "hit" if getattr(item, "contains", lambda p: False)(point) else "miss"))
        except Exception as error:                        # noqa: BLE001
            bits.append("actions=%s" % error)
        try:
            bits.append("hit_action=%s" % renderer.hit_action(point, game))
        except Exception as error:                        # noqa: BLE001
            bits.append("hit_action=%s" % error)
        bits.append("slot=%s" % _slot_of(self.ctx))
        return " ".join(bits)

    def _log(self, label, detail=""):
        game = self.ctx.game
        self.steps.append({
            "n": len(self.steps) + 1,
            "label": label,
            "detail": detail,
            "phase": str(game.phase),
            "turn": str(getattr(game.current_turn_player, "name", "")),
            "hand": len(getattr(game.player, "hand", ())),
            "hp": int(getattr(game.player, "hp", 0)),
            "pending": self._pending_desc(),
            "blockers": self._blockers(),
            "changed": None,
        })
