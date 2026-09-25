"""1v1 测试模式的运行期验收脚本：**在真实主循环里**走一遍并截图。

    SDL_VIDEODRIVER=dummy SGS_RUNTIME_SCRIPT=duel_capture \
        SGS_DUEL_SCENARIO=manual SGS_DUEL_OUT=tools/ui_snapshots/duel_manual \
        .venv/Scripts/python.exe main.py

它与 ``runtime_capture`` 同一套路（见 ``src/ui/runtime_hook.py``）：不是自己搭
一套渲染循环，而是接管 ``main.py`` 正在用的那组真实对象，按真人会做的动作
（点主菜单模式按钮 → 点开始游戏 → 在设置页里搜武将 / 点武将牌 / 切操作方式
→ 开始对战 → 点结束回合 → 打出一张杀 …）逐步推进，每到一个里程碑存一张 PNG。

关键点：所有操作都走**真实入口**——主菜单走 ``StartMenu.handle_click``，设置页走
``DuelSetupScreen.handle_event``，牌桌走 ``ui.interaction.handle_game_click``，
与真人鼠标点下去是同一条路径。
"""

import os

import pygame

from src.ui.interaction import handle_game_click

#: 截图前要求画面连续稳定的帧数。
STABLE_FRAMES = 20
#: 每一步最多等多少帧（超时后跳过，不把整个录制挂死）。
STEP_TIMEOUT = 1200


def _event(kind, **payload):
    return pygame.event.Event(kind, payload)


class Step:
    """录制脚本里的一步：``label`` 进日志，``run`` 返回 True 表示这一步完成。"""

    def __init__(self, label, run):
        self.label = label
        self.run = run


class Hook:

    def __init__(self):
        self.ctx = None
        self.out = "tools/ui_snapshots/duel"
        self.scenario = "manual"
        self.size = None
        self.records = []
        self._steps = []
        self._index = 0
        self._frames = 0
        self._step_frames = 0

    # ==================================================
    # 生命周期
    # ==================================================

    def bind_environment(self, env):
        self.out = str(env.get("SGS_DUEL_OUT") or self.out)
        self.scenario = str(env.get("SGS_DUEL_SCENARIO") or self.scenario)
        size = str(env.get("SGS_DUEL_SIZE") or "")
        if "x" in size:
            self.size = tuple(int(part) for part in size.lower().split("x"))
        os.makedirs(os.path.dirname(os.path.abspath(self.out)), exist_ok=True)
        return self

    def bind(self, context):
        self.ctx = context
        if self.size:
            surface = pygame.display.set_mode(self.size)
            context.screen = surface
            self.ctx.hud.screen = surface
            self.ctx.settings.screen = surface
            context.resync(surface)
        # 让布局度量与**真正要保存的那块表面**一致（dummy 驱动会把窗口尺寸
        # 夹到桌面尺寸，度量若停在别的尺寸上，截出来的画面就不是真人看到的）。
        self.ctx.renderer.set_screen(self.ctx.screen)
        self._steps = self._build_steps()
        return self

    def step(self, dt):
        self._frames += 1
        if self._index >= len(self._steps):
            return True
        step = self._steps[self._index]
        if self._step_frames == 0:
            self.records.append("步骤：" + step.label)
        self._step_frames += 1
        try:
            done = bool(step.run(self._step_frames))
        except Exception as error:                  # 单步出问题不拖垮整个录制
            self.records.append("步骤「%s」异常：%r" % (step.label, error))
            done = True
        if done or self._step_frames > STEP_TIMEOUT:
            if not done:
                self.records.append("步骤「%s」超时（跳过）" % step.label)
            self._index += 1
            self._step_frames = 0
        return False

    def finish(self):
        for line in self.records:
            print("[duel_capture]", line)

    # ==================================================
    # 原语
    # ==================================================

    def _click(self, position):
        """按当前场景走真实点击入口。"""

        game = self.ctx.game
        position = (int(position[0]), int(position[1]))
        if game.scene == "menu":
            return self.ctx.start_menu.handle_click(position, game)
        if game.scene == "duel_setup":
            action = self.ctx.settings.handle_event(
                _event(pygame.MOUSEBUTTONDOWN, pos=position, button=1), game)
            if action == "start":
                self.ctx.settings.start_battle(game)
            elif action == "back":
                game.return_to_menu()
            return action
        action = self.ctx.hud.handle_click(position, game)
        if action:
            self.ctx.hud.run_action(action, game)
            return action
        if game.busy:
            # 动画播放中的点击本来就会被牌桌忽略（handle_game_click 的第一道
            # 闸门），验收脚本不必再制造这种无效点击。
            return None
        return handle_game_click(position, game, self.ctx.renderer)

    def _dump(self, label):
        """状态快照（验收脚本诊断用）。"""

        game = self.ctx.game
        stack = game.engine.pending.stack
        renderer = self.ctx.renderer
        table = getattr(renderer, "table_layout", None)
        hand_rects = getattr(table, "hand_rects", None) if table is not None else None
        self.records.append(
            "状态[%s] 场景=%s 操作=%s 阶段=%s busy=%s 响应=%s 手牌=%s 尺寸=%s 手牌顶=%s "
            "请求栈=%s 战报=%s" % (
                label, game.scene, game.local_operator_label, game.phase,
                game.busy,
                (sorted(game.response.current.allowed_cards)
                 if game.response.current else "-"),
                [len(p.hand) for p in game.players],
                (self.ctx.screen.get_size(), renderer.metrics.screen_w,
                 renderer.metrics.screen_h),
                (tuple(hand_rects[0]) if hand_rects else "-"),
                [(request.request_id, request.request_type.value, request.status,
                  getattr(request.target, "name", "-"),
                  sorted(request.member_state.values()))
                 for request in stack],
                game.game_log[-2:],
            ))

    def _shot(self, tag):
        path = "%s_%s.png" % (self.out, tag)
        pygame.image.save(self.ctx.screen, path)
        self.records.append("截图：" + path)
        self._dump(tag)
        return path

    def _wait_stable(self, frames=STABLE_FRAMES):
        return frames > 0 and self._step_frames >= frames

    def _search_pick(self, seat, general_id, name, frames):
        """搜索武将名 → 点第一张结果（真实的搜索框 + 网格点击）。"""

        settings = self.ctx.settings
        game = self.ctx.game
        if frames == 1:
            settings.search_field.set_text(name)
            settings.on_filter_changed(game)
            return False
        if frames == 2:
            index = next((i for i, general in enumerate(settings.cards)
                          if general.id == general_id), None)
            if index is None:
                self.records.append("搜索「%s」没找到 %s" % (name, general_id))
                return True
            settings.set_active_seat(seat)
            self._click(settings.card_rects[index].center)
            self.records.append("选将：%s = %s" % (
                "我方" if seat == 0 else "对手", settings.cards[index].name))
            return False
        settings.search_field.set_text("")
        settings.on_filter_changed(game)
        return True

    # ==================================================
    # 录制脚本
    # ==================================================

    def _build_steps(self):
        steps = [
            Step("主菜单：选中「1v1 测试」→ 点开始游戏", self._menu_enter),
            Step("截图：设置页（未选择）", self._shot_step("01_setup_empty")),
            Step("设置页：搜索「关羽」并指定给我方",
                 lambda frames: self._search_pick(0, "guanyu", "关羽", frames)),
            Step("设置页：点击「对手」面板切换选将目标", self._click_panel(1)),
            Step("设置页：搜索「吕布」并指定给对手",
                 lambda frames: self._search_pick(1, "lvbu", "吕布", frames)),
            Step("设置页：操作方式 = " + ("双边手动测试" if self.scenario == "manual"
                                          else "玩家对AI"),
                 self._choice_step("control",
                                   "manual" if self.scenario == "manual" else "ai")),
            Step("设置页：先手 = 我方", self._choice_step("first", "me")),
            Step("设置页：固定随机种子 20240925", self._seed_step("20240925")),
            Step("截图：设置页（双方已选好）", self._shot_step("02_setup_ready")),
            Step("设置页：点「开始对战」", self._start_step),
            Step("截图：对局中（我方回合，双边手动）", self._shot_step("03_battle_my_turn")),
            Step("牌桌：结束我方回合（超上限先弃牌）", self._end_turn_step),
            Step("截图：对局中（对方回合，手牌与提示已切换）",
                 self._shot_step("04_battle_next_turn")),
            Step("牌桌：结束对方回合，操作权交回我方", self._end_turn_step),
            Step("截图：对局中（回合轮转后）", self._shot_step("05_battle_cycle")),
        ]
        if self.scenario == "manual":
            steps += [
                Step("准备：给我方一张【杀】、对手一张【闪】",
                     lambda frames: self._prepare_card_step({0: "SHA", 1: "SHAN"}, frames)),
                Step("牌桌：等到我方出牌阶段", lambda frames: self._wait_play_step(0, frames)),
                Step("牌桌：我方打出【杀】", lambda frames: self._play_card_step("SHA", frames)),
                Step("截图：对手的【闪】响应窗口（视角已切到对手）",
                     self._shot_step("06_manual_shan_request")),
                Step("牌桌：对手点手牌打出【闪】", self._respond_step),
                Step("截图：闪结算之后的牌桌", self._shot_step("07_manual_after_shan")),
                Step("准备：给我方一张【过河拆桥】、对手一张【无懈可击】",
                     lambda frames: self._prepare_card_step(
                         {0: "GUOHE", 1: "WUXIE"}, frames)),
                Step("牌桌：等到我方出牌阶段", lambda frames: self._wait_play_step(0, frames)),
                Step("牌桌：我方使用【过河拆桥】，触发共享无懈阶段",
                     lambda frames: self._play_card_step("GUOHE", frames)),
                Step("截图：共享无懈阶段（对手被依次问到）",
                     self._shot_step("08_manual_wuxie_window")),
                Step("牌桌：对手打出【无懈可击】", self._respond_step),
                Step("截图：无懈结算之后的牌桌", self._shot_step("09_manual_after_wuxie")),
                Step("牌桌控制条：点「原配置重开」",
                     lambda frames: self._hud_step("restart", frames)),
                Step("截图：原配置重开（双方武将不变、状态干净）",
                     self._shot_step("10_manual_restart")),
            ]
        steps.append(Step("结束对局：让对手阵亡，看结算面板", self._finish_step))
        return steps

    # ---- 各步骤实现 ----

    def _menu_enter(self, frames):
        menu = self.ctx.start_menu
        if frames == 1:
            self._click(menu.mode_button("duel_test").rect.center)
            return False
        if frames == 2:
            self._click(menu.start_button.rect.center)
            self.records.append("场景：" + str(self.ctx.game.scene))
            return True
        return False

    def _shot_step(self, tag):
        def run(frames):
            if not self._wait_stable():
                return False
            self._shot(tag)
            return True
        return run

    def _click_panel(self, seat):
        def run(frames):
            if frames < 2:
                return False
            rect = self.ctx.settings.side_panels[seat]
            self._click(rect.center)
            return True
        return run

    def _choice_step(self, field, value):
        def run(frames):
            buttons = (self.ctx.settings.control_buttons if field == "control"
                       else self.ctx.settings.first_buttons)
            self._click(buttons[value].rect.center)
            return getattr(self.ctx.game.duel_config, field) == value
        return run

    def _seed_step(self, seed):
        def run(frames):
            settings = self.ctx.settings
            if frames == 1:
                self._click(settings.seed_field.box_rect.center)
                return False
            if frames == 2:
                settings.seed_field.set_text(seed)
                settings.seed_field.blur()
                self.records.append("种子输入框 = " + settings.seed_field.text)
                return True
            return False
        return run

    def _start_step(self, frames):
        settings = self.ctx.settings
        action = self._click(settings.start_button.rect.center)
        self.records.append("开始对战 → " + str(action)
                            + "，配置：" + str(self.ctx.game.duel_config))
        return self.ctx.game.scene == "game"

    # ---- 牌桌上的通用动作 ----

    def _finish_turn_once(self, game, renderer):
        """替当前操作者做一次"没有决策含量"的收尾：弃牌，或结束回合。"""

        if game.game_over or game.busy:
            return False
        if game.engine.pending.active or game.response.active or game.choice.active:
            return False
        if game.phase == "discard" and game.current_turn_player is game.player:
            surplus = len(game.player.hand) - game.hand_limit(game.player)
            if surplus > 0:
                rects = renderer.get_card_rects(game.player.hand)
                if rects:
                    self._click(rects[-1].center)
                    return True
        if renderer.hit_action(renderer.primary_button.rect.center, game) == "end_turn":
            self._click(renderer.primary_button.rect.center)
            return True
        return False

    def _end_turn_step(self, frames):
        """结束当前操作者的整段回合：弃牌 → 结束回合 → 直到换人。"""

        game = self.ctx.game
        renderer = self.ctx.renderer
        if game.game_over:
            return True
        if frames == 1:
            self._turn_pid = game.current_turn_player.player_id
            self.records.append("当前操作：" + str(game.local_operator_label)
                                + "，手牌 " + str(len(game.player.hand))
                                + " 张，阶段 " + str(game.phase))
        if game.current_turn_player.player_id != self._turn_pid:
            self.records.append("回合已交给 " + game.current_turn_player.name
                                + "，当前操作：" + str(game.local_operator_label))
            return True
        self._finish_turn_once(game, renderer)
        return False

    def _wait_play_step(self, seat, frames):
        """推进到指定座位可以出牌为止；别人的回合先替他们收尾。"""

        game = self.ctx.game
        renderer = self.ctx.renderer
        if game.game_over:
            return True
        if game.local_can_play() and int(game.player.seat) == seat:
            self.records.append("轮到 " + game.player.name + " 出牌，手牌 "
                                + str(len(game.player.hand)) + " 张："
                                + "/".join(getattr(card, "name", "?")
                                           for card in game.player.hand))
            return True
        self._finish_turn_once(game, renderer)
        return False

    def _prepare_card_step(self, plan, frames):
        """把指定牌从牌堆挪进某人手里（验收脚本的准备动作，不绕过任何规则）。"""

        if frames > 1:
            return True
        game = self.ctx.game
        for seat, card_name in plan.items():
            player = next((item for item in game.players
                           if int(getattr(item, "seat", 0)) == seat), None)
            if player is None:
                continue
            found = self._grab_card(game, card_name, player)
            self.records.append("准备：%s 获得【%s】 %s" % (
                player.name, card_name, "成功" if found else "失败（牌堆里没有）"))
        return True

    def _grab_card(self, game, card_name, player):
        for card in list(game.deck.draw_pile):
            if getattr(card, "name", "") == card_name:
                game.deck.draw_pile.remove(card)
                player.hand.append(card)
                return True
        for other in game.players:
            if other is player:
                continue
            for card in list(other.hand):
                if getattr(card, "name", "") == card_name:
                    other.hand.remove(card)
                    player.hand.append(card)
                    return True
        return False

    def _play_card_step(self, card_name, frames):
        """在当前操作者手上找一张指定牌打出去（真实的点牌 → 选方式 → 点目标）。"""

        game = self.ctx.game
        renderer = self.ctx.renderer
        if game.game_over:
            return True
        if game.engine.pending.active or game.response.active or game.choice.active:
            self.records.append("【%s】已经打出去了：%s" % (card_name, game.message))
            return True
        if game.pending_target_selection is not None:
            return self._target_step(game, renderer)
        if not game.local_can_play():
            return False
        hand = list(game.player.hand)
        index = next((i for i, card in enumerate(hand)
                      if getattr(card, "name", "") == card_name), None)
        if index is None:
            self.records.append("当前操作者手上没有【%s】，跳过" % card_name)
            return True
        rects = renderer.get_card_rects(game.player.hand)
        self.records.append("出牌：%s 使用【%s】" % (game.player.name, card_name))
        self._click(rects[index].center)
        options = game.card_action_picker()
        if options:
            renderer.action_picker.sync_layout(renderer.metrics, len(options))
            self._click(renderer.action_picker.rects[0].center)
        if game.pending_target_selection is not None:
            return self._target_step(game, renderer)
        return True

    def _target_step(self, game, renderer):
        selection = game.pending_target_selection
        if selection is None:
            return True
        if not selection["selected"]:
            opponent = next((player for player in game.players
                             if player is not game.player and player.alive), None)
            if opponent is None:
                return True
            seat = renderer.table_layout.seat_rect(opponent)
            if seat is not None:
                self._click(seat.center)
            return False
        if renderer.hit_action(renderer.primary_button.rect.center, game) == "confirm_target":
            self._click(renderer.primary_button.rect.center)
        return True

    def _respond_step(self, frames):
        """有人正在被要求响应时，用他手牌里的合法响应牌打出去。

        关键点：

        * **动画没播完时点牌是无效的**（``handle_game_click`` 在 busy 时直接
          返回），所以必须等到不忙再点；
        * 无懈阶段可以连着开好几轮，所以第一次打出响应牌、之后被再问就点
          「不出」，直到窗口整个关掉才算这一步做完。
        """

        game = self.ctx.game
        renderer = self.ctx.renderer
        if frames == 1:
            self._answered = 0
        if game.game_over:
            return True
        current = game.response.current
        if current is None:
            return True
        if self._answered >= 1:
            if game.busy:
                return False
            # 又轮到他（无懈链的新一轮）：不再反无懈，点「不出」收尾。
            self.records.append("再次被问，点「不出」（视角=%s）"
                                % game.local_operator_label)
            self._click(renderer.secondary_button.rect.center)
            self._answered += 1
            if self._answered > 8:
                self.records.append("无懈链太长，跳过")
                return True
            return False
        if game.busy:
            return False
        allowed = set(current.allowed_cards or ())
        hand = list(game.player.hand)
        index = next((i for i, card in enumerate(hand)
                      if getattr(card, "name", "") in allowed), None)
        if index is None:
            self.records.append("当前操作者手上没有可响应的牌，改点「不出」")
            self._click(renderer.secondary_button.rect.center)
            self._answered += 1
            return False
        rects = renderer.get_card_rects(game.player.hand)
        self.records.append(
            "响应：%s 打出【%s】（视角=%s，手牌 %d 张）" % (
                game.player.name, getattr(hand[index], "name", "?"),
                game.local_operator_label, len(game.player.hand)))
        self._click(rects[index].center)
        self._answered += 1
        return False

    def _hud_step(self, action, frames):
        game = self.ctx.game
        hud = self.ctx.hud
        if frames == 1:
            self.records.append("控制条点击：" + action
                                + "｜重开前武将 " + str([p.general_id for p in game.players]))
            return False
        if frames == 2:
            button = {"restart": hud.restart_button, "setup": hud.setup_button,
                      "menu": hud.menu_button}[action]
            self._click(button.rect.center)
            return False
        if action == "restart":
            if game.scene != "game" or game.game_over:
                return False
            self.records.append("重开后武将 " + str([p.general_id for p in game.players])
                                + "｜配置 " + str(game.duel_config))
            return True
        return True

    def _finish_step(self, frames):
        game = self.ctx.game
        renderer = self.ctx.renderer
        if not game.game_over:
            # 濒死求桃会开响应窗口：让当前操作者点「不出」，直到对局结束。
            if game.response.current is not None:
                self._click(renderer.secondary_button.rect.center)
                return False
            if frames % 30 == 1:
                target = next((player for player in game.players
                               if int(getattr(player, "seat", 0)) == 1), None)
                if target is not None and target.alive:
                    game.lose_hp(target, int(target.hp) + 1)
            return False
        if not self._wait_stable():
            return False
        self._shot("11_duel_over")
        self.records.append("结算：" + str(game.message)
                            + "｜result = " + str(game.result))
        return True
