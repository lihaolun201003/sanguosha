"""客户端牌桌场景：网络生命周期的最后一站 + 把只读视图交给**同一套牌桌 UI**。

Phase 11.4.2 之后，这个模块**不再是第二套游戏界面**。玩家看到的画面、点击
的命中测试、选择与高亮、技能按钮、确认 / 取消，全部由单机那套实现承担：

    绘制          Renderer.draw(view)            ← 与单机同一个类
    布局          layout.LayoutMetrics/TableLayout ← 同一套几何
    点击          ui.interaction.handle_game_click ← 同一个入口
    动作          ui.human_control.HumanController ← 同一个接口
                    └── RemoteHumanController：把动作编码成 Decision Response

它自己只负责四件事：

1. 把房主发来的 ``DecisionRequest`` 翻译成**本地引擎同构的交互槽位**
   （``pending_selection`` / ``pending_target_selection`` / ``pending_skill_input``
   / ``response`` / ``choice``），于是既有绘制与点击路径不需要认识"联机"；
2. 取走房主发来的表现事件、推进本地动画（``ClientPresentation``）；
3. 是 / 否与多选一复用单机的 ``ChoiceOverlay``；
4. 本地提示（房主拒绝 / 等待结算 / 断线原因）与离开信号。

它不会：执行流程、算距离与合法性、摸牌、改体力、改牌的位置。客户端唯一的规则
来源是房主发来的候选列表——请求里没有的牌与角色就点不动。
"""

import pygame

from src.choice import ChoiceOverlay, ChoiceSystem
from src.network.decisions import DecisionKind

from . import theme
from .client_fx import ClientPresentation
from .interaction import handle_game_click
from .remote_control import RemoteHumanController
from .view_adapter import RemoteDecisionState, RemoteGameView
from .widgets import ellipsize_text


def _default_renderer(screen):
    """没有注入 Renderer 时自建一个（测试 / 工具场景）。"""

    from src.renderer import Renderer

    return Renderer(screen)


class RemoteTableScene:
    """客户端的一张牌桌（画 + 操作 + 表现）。"""

    def __init__(self, screen, renderer=None):
        self.screen = screen
        self._owns_renderer = renderer is None
        self.renderer = renderer if renderer is not None else _default_renderer(screen)
        self.view = RemoteGameView(host_player_id="")
        self.presentation = ClientPresentation(self.renderer.effects)
        # 表现层要拿到只读视图才能定位角色 / 牌（动画落点、飘字锚点）。
        self.presentation.bind_view(self.view)
        # 客户端的卡牌移动动画跑在本地队列上（纯表现，与房主无关）。
        self.view.actions = self.presentation.queue
        self.decision = RemoteDecisionState()
        #: 玩家动作的统一出口：与单机 **同一个接口**，只是末端发的是决策响应。
        self.human = RemoteHumanController(
            self.view, self.decision,
            answer=self._answer, notice=self._notice, leave=self._request_leave,
            restart=self._request_restart,
        )
        self.choice = ChoiceSystem()
        self.choice_overlay = ChoiceOverlay(screen)
        self.notice = ""
        self.status = ""
        self.metrics = getattr(renderer, "metrics", None)
        #: 本次点击处理期间的对局（只在 handle_event 内有效）。
        self._active_match = None
        self._request = None
        self._choice_key = None
        self._exit_requested = False
        self._restart_requested = False

    # ==================================================
    # 布局 / 生命周期
    # ==================================================

    def set_screen(self, screen):
        self.screen = screen
        self.choice_overlay.screen = screen
        if self._owns_renderer:
            self.renderer.set_screen(screen)
        return self

    def sync_layout(self, metrics=None):
        self.metrics = metrics or self.renderer.metrics
        self.choice_overlay.sync_layout(self.metrics)
        return self

    def reset(self):
        """回到主菜单 / 换房间：清掉本地表现与选择状态。"""

        self.presentation.reset()
        self.decision.reset(None)
        self.human.request = None
        self.choice.clear()
        self.notice = ""
        self.status = ""
        self._choice_key = None
        self._exit_requested = False
        self._restart_requested = False
        self.view.cards.clear()
        return self

    # ---- 玩家动作的末端（RemoteHumanController 的回调）----

    def _answer(self, result):
        match = self._active_match
        if match is not None:
            match.answer(result)

    def _notice(self, text):
        self.notice = str(text or "")

    def _request_leave(self):
        self._exit_requested = True

    def _request_restart(self):
        """「重新开始」：只发请求，重开由房主统一决定（客户端没有权威 Game）。"""

        self._restart_requested = True
        match = self._active_match
        if match is not None:
            match.request_restart()
        self.notice = "已请求房主重新开始，等待房主…"

    # ==================================================
    # 每帧
    # ==================================================

    def update(self, dt, match, *, advance_effects=False):
        """取走表现事件、刷新只读视图、推进本地动画。

        ``advance_effects`` 默认 False：真实主循环里 ``Renderer.update`` 已经
        推进过 FX，这里再推一次会让动画快一倍。无窗口工具可以传 True。
        """

        if match is None:
            return
        if match.view.revision and match.view.revision != self.view.revision:
            self.view.update(match.view)
        self._sync_overlay(match)
        self.presentation.play(match.take_events())
        self.presentation.queue.update(dt)
        if advance_effects:
            self.presentation.effects.update(dt)

    def _sync_overlay(self, match):
        """把当前的决策请求 + 本地选择同步到只读视图的交互槽位。"""

        request = None
        if match is not None and match.ready and not match.answered:
            request = match.decision
        if request is not None and self.decision.request_id != request.get("request_id"):
            self.decision.reset(request)
        if request is None and not self.decision.empty:
            self.decision.reset(None)
        # 点击路由与绘制读的是同一份状态：这里写进 human，两边永远一致。
        self.human.request = request
        self.view.apply_decision(request, self.decision)
        self._sync_choice(match, request)
        return request

    def _sync_choice(self, match, request):
        """是 / 否 与多选一：复用单机的 ``ChoiceOverlay``（数据同构）。

        * ``CONFIRM``：发动 / 不发动，两个按钮（与单机一字不差）；
        * ``CHOOSE_OPTION``：**任意数量**选项。1 个选项时两个按钮都是它
          （单机对"唯一合法选项"就是这个交互，客户端保持一致），2 个时左右
          分开，3 个以上改竖排列表——绝不出现"选项数量不对就清掉面板、
          流程却没人推进"的死锁。
        """

        kind = None if request is None else request.get("kind")
        if kind not in (DecisionKind.CONFIRM, DecisionKind.CHOOSE_OPTION):
            self.choice.clear()
            self._choice_key = None
            return
        options = [item for item in (request.get("options") or ())
                   if isinstance(item, dict)]
        if not options:
            # 房主没能给出任何选项（老版本房主 / 异常）：不能静默清掉，
            # 否则玩家永远答不了。用提示条说明，等房主重发。
            self.choice.clear()
            self.status = "房主没有给出可选项，正在等待重发…"
            return
        key = (kind, request.get("request_id"))
        if self._choice_key == key:
            return
        self._choice_key = key
        title = "请确认" if kind == DecisionKind.CONFIRM else "请选择"
        prompt = request.get("prompt", "")
        values = [item.get("value") for item in options]
        labels = [str(item.get("label") or "") for item in options]
        if kind == DecisionKind.CONFIRM or len(options) <= 2:
            # 与单机同构：yes / no 两个按钮。只有一个选项时两个按钮都是它
            # ——点哪边都是同一个答案，而不是"面板消失但没有提交"。
            second = values[1] if len(values) > 1 else values[0]
            second_label = labels[1] if len(labels) > 1 else labels[0]
            self.choice.request(
                title, prompt,
                on_yes=lambda value=values[0]: self._submit_option(match, value),
                on_no=lambda value=second: self._submit_option(match, value),
                yes_label=labels[0],
                no_label=second_label)
            return
        self.choice.request(
            title, prompt,
            on_yes=lambda value=values[0]: self._submit_option(match, value),
            on_no=lambda value=values[-1]: self._submit_option(match, value),
            yes_label=labels[0], no_label=labels[-1],
            options=[(label, (lambda value=value: self._submit_option(match, value)))
                     for label, value in zip(labels, values)])

    # ==================================================
    # 交互
    # ==================================================

    def handle_event(self, event, match):
        """返回 ``"leave"`` / ``"handled"`` / ``None``。"""

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return "leave"
        if event.type != pygame.MOUSEBUTTONDOWN or getattr(event, "button", 1) != 1:
            return None if event.type == pygame.MOUSEMOTION else "handled"

        request = self._sync_overlay(match)
        self._active_match = match
        self.human.request = request

        # 模态：是 / 否 与二选一（点面板外一律吞掉）。
        if self.choice.active:
            self.choice_overlay.handle_click(event.pos, self.choice)
            return "handled"

        # 与单机**同一个**点击入口：命中测试 + 语义分支都在那儿，末端动作
        # 由 RemoteHumanController 发回房主。
        handle_game_click(event.pos, self.view, self.renderer, self.human)

        if self._exit_requested:
            self._exit_requested = False
            return "leave"
        return "handled"

    def _submit_choice(self, match, confirm):
        from src.network.decisions import ACTION_SUBMIT, DecisionResult

        if match is not None:
            match.answer(DecisionResult(action=ACTION_SUBMIT, confirm=bool(confirm)))

    def _submit_option(self, match, value):
        """选项值 → 回答。``value`` 必须原样回传房主给的那个值（不重新编号）。"""

        from src.network.decisions import ACTION_SUBMIT, DecisionResult

        if match is not None:
            match.answer(DecisionResult(action=ACTION_SUBMIT, option=value))

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, match, metrics=None):
        metrics = metrics or self.renderer.metrics
        self.metrics = metrics
        # 事件先于绘制到达：这里再同步一次，保证"点得到"与"看得到"一致。
        request = self._sync_overlay(match)
        view = self.view
        if not self.choice.active:
            from . import decision_presentation as presentation

            view.allowed_card_ids = presentation.hand_allowed_ids(
                request, [card.id for card in view.player.hand if card is not None])

        # 1) 牌桌本体 + 提示条 + 固定按钮：全部走既有 Renderer。
        self.renderer.draw(view)

        if match is None or not match.ready:
            return
        self._draw_notice(metrics, match, request)
        self.choice_overlay.draw(self.choice, metrics)

    def _draw_notice(self, metrics, match, request):
        """本地提示（房主拒绝 / 等待结算 / 断线原因）：一行小字，不占主画面。

        画在手牌区下方：提示条上方的空间留给"中央战场 → 提示条"的呼吸位，
        两行提示叠在一起正是之前看起来紧凑的原因之一。
        """

        text = ""
        color = theme.TARGET_YELLOW
        if match.aborted:
            text, color = match.aborted, theme.DANGER
        elif getattr(match, "restarted", False) or self._restart_requested:
            text, color = "已请求重新开始，等待房主…", theme.GOLD_BRIGHT
        elif getattr(match, "reject_reason", ""):
            # 房主拒绝过一条回答：面板已经还给玩家，这里说明为什么。
            text, color = match.reject_reason, theme.DANGER
        elif self.notice:
            text = self.notice
        elif match.answered:
            text, color = "已提交，等待房主结算…", theme.GOLD_BRIGHT
        elif self.status:
            text = self.status
        elif self.decision.notice:
            text = self.decision.notice
        elif request is None and not match.view.players:
            text, color = "正在等待房主的牌局视图…", theme.TEXT_DIM
        if not text:
            return
        font = metrics.fonts.get("normal")
        rendered = font.render(
            ellipsize_text(text, font, metrics.prompt.width), True, color)
        self.screen.blit(rendered, rendered.get_rect(
            midtop=(metrics.prompt.centerx, metrics.hand_area.bottom + metrics.px(10))))
