"""联网客户端的人：把**同一批 UI 动作**翻译成 Decision Response（Phase 11.4.2）。

它和 ``LocalHumanController`` 实现的是同一个接口（``ui.human_control``），
所以点击路由、选牌、选目标、技能按钮、确认 / 取消全部跑同一份代码：

    ui.interaction.handle_game_click
                │  同一个命中测试 + 同一套语义分支
      HumanController
        ├── LocalHumanController  → Game.player_use_card(...)
        └── RemoteHumanController → DecisionResult(ACTION_SUBMIT, ...) → 房主

区别只在**末端**：客户端没有规则引擎，能做什么由房主的 ``DecisionRequest``
决定（候选牌、可点角色、可发动技能、可用方式都在请求里），所以这里只做两件事：

1. 维护"我选了什么"（``RemoteDecisionState``：牌 / 目标 / **用哪种方式** /
   技能费用）；
2. 把选择编码成 ``DecisionResult`` 发回房主，由房主用真实规则校验。

Phase 11.5 补齐的三件事：

* **选方式**：一张牌有多种用法时，点手牌列出候选方式（复用单机的"选择操作"
  面板）；玩家点了第 N 种，客户端记下**房主的 action_id**并回传，而不是
  默默用第一种。未知动作一律忽略并给提示，绝不落进默认提交。
* **多来源方式**：一个方式要几张实体牌由房主下发（``min_sources`` /
  ``max_sources``）。不够就不能提交，够了才进下一步（选目标或直接提交）。
* **提交 ≠ 完成**：这里只负责发出去；是否被接受由房主回 ACK / REJECT，
  ``RemoteTableScene`` 会在被拒时把面板还给玩家。

本模块不含任何规则：不算距离、不判合法性、不摸牌、不改牌的位置。
"""

from src.network.decisions import (
    ACTION_CANCEL,
    ACTION_END_PHASE,
    ACTION_PASS,
    ACTION_SKILL,
    ACTION_SUBMIT,
    DecisionKind,
    DecisionResult,
)

from src.game.equipment_skills.granted import granted_skill_id

from . import decision_presentation as presentation
from .human_control import HumanController


class RemoteHumanController(HumanController):
    """客户端侧的一名远程真人。"""

    local_interaction = False

    def __init__(self, view, state, *, answer, notice=None, leave=None, restart=None):
        #: 只读视图（``RemoteGameView``，原地更新，对象身份稳定）。
        self.view = view
        #: "我选了什么"（``RemoteDecisionState``）。
        self.state = state
        #: 把一条回答发给房主。
        self.answer = answer
        #: 本地提示（"已提交，等待房主结算…"一类）。
        self.notice = notice or (lambda text: None)
        #: 请求退出联机对局（投降二次确认之后）。
        self.leave = leave or (lambda: None)
        #: 「重新开始」：只发请求，重开由房主决定。
        self.restart = restart or (lambda: None)
        #: 房主当前发来的决策 payload（每帧由牌桌场景写入）。
        self.request = None

    # ==================================================
    # 固定按钮 / 面板动作
    # ==================================================

    def run_action(self, action, renderer):
        if action == "surrender":
            if renderer.request_surrender():
                self.leave()
            else:
                self.notice("再点一次「投降」返回主菜单")
            return None

        # ---- 结算界面：本地按钮，不发决策 ----
        if action == "menu":
            self.leave()
            return None
        if action == "restart":
            self.restart()
            return None

        if action in ("end_turn", None):
            if action is None:
                return None
            if not self._play_phase_open():
                # 只读视图里的"我的回合 + 出牌阶段"不等于"现在可以操作"：
                # 房主可能正在等别人响应、正在结算，或者面板还没发过来。
                self.state.notice = "现在还不能结束回合，请等房主刷新面板"
                return None
            return self._answer(DecisionResult(action=ACTION_END_PHASE))

        if action == "confirm_target":
            return self._submit()

        if action in ("confirm_card_action", "confirm_skill"):
            if self.state.configuring_skill:
                return self._submit_skill()
            return self._submit()

        if action == "pass_response":
            return self._answer(DecisionResult(action=ACTION_PASS))

        if action == "pass_selection":
            return self._cancel()

        if action == "cancel_target":
            if self.state.kind == DecisionKind.SELECT_TARGETS:
                return self._cancel()
            self.state.targets = []
            return None

        if action in ("action_cancel", "cancel_skill"):
            if action == "cancel_skill":
                self._reset_skill()
            self.state.clear_selection()
            return None

        if action in ("skill_info", "swallow", "noop"):
            # 展开说明 / 面板外的点击：什么都不做，也什么都不提交。
            return None

        if action == "cancel":
            # 技能选择面板的取消：与「取消发动」同一语义。
            self._reset_skill()
            self.state.clear_selection()
            return None

        if action == "choice_no":
            # 选择框自己处理点击（ChoiceOverlay），这里只是兜底。
            return None

        if isinstance(action, tuple) and action:
            if action[0] in ("skill", "activate_skill"):
                return self.activate_skill(action[1])
            if action[0] == "action":
                return self.choose_way(action[1])
            if action[0] == "view_as":
                # 视为技在客户端是"这张牌的一种用法"，不需要单独入口。
                self.notice("请直接选择要转化的牌")
                return None

        # 未知动作：**绝不**当成"默认提交"。只记提示，等玩家点对地方。
        self._unknown_action(action)
        return None

    def _unknown_action(self, action):
        self.state.notice = "这个操作在当前界面里没有对应动作"
        self.notice("已忽略一个未知操作：" + repr(action))

    # ==================================================
    # 选择使用方式（"选择操作"面板）
    # ==================================================

    def choose_way(self, action_id):
        """玩家在"选择操作"面板里挑了一种用法。

        ``action_id`` 是房主下发的字符串 id。校验它在**当前这份请求**里确实
        存在、而且可用；然后记下它，并按这种方式决定下一步：
        来源牌没选够 → 留在选牌状态；够且需要目标 → 进目标选择；否则可直接提交。
        """

        state = self.state
        request = self.request
        if request is None:
            return None
        entry = presentation.play_card_entry(request, state.cards)
        option = presentation.option_by_action(entry, action_id)
        if option is None:
            state.notice = "这个用法现在不适用，请重新选择"
            return None
        if not option.get("enabled", True):
            state.notice = str(option.get("disabled_reason") or "现在不能这样使用")
            return None
        index = presentation.option_index(entry, action_id)
        state.choose_way(option, index)
        state.targets = []
        state.notice = ""
        return self._after_way_chosen(entry, option)

    def _after_way_chosen(self, entry, option):
        """选完方式的下一步：够来源 → 选目标 / 提交；不够 → 继续选来源。"""

        state = self.state
        ready = presentation.play_sources_ready(entry, option, state.cards)
        if not ready:
            state.notice = presentation.sources_needed_text(option, state.cards)
            return None
        return self._maybe_auto_submit(option)

    def _maybe_auto_submit(self, option):
        """来源齐了：需要目标就进目标选择，不需要就直接提交。

        与单机一致（丈八类"选完第二张即结算"）：不需要目标的牌不需要再点一次
        确认，少一次多余点击。
        """

        state = self.state
        if int(option.get("max_targets") or 0) > 0:
            # 目标由玩家在座位上点（渲染层会按这份 option 高亮）。
            state.notice = ""
            return None
        if self.state.kind == DecisionKind.SELECT_CARDS:
            return None
        return self._submit()

    # ==================================================
    # 点牌：客户端只有一个动作——"选中 / 取消这张牌"
    # ==================================================

    def use_card(self, card, index, rect):
        return self._tap_card(card)

    def discard_card(self, card, index, rect):
        return self._tap_card(card)

    def respond_with_card(self, card, index, rect):
        return self._tap_card(card)

    def select_card(self, card, rect, key=None, zone=""):
        return self._tap_card(card)

    def toggle_card_source(self, card, rect):
        return self._tap_card(card)

    def select_skill_cost_card(self, card, rect):
        return self._tap_card(card)

    def begin_card_action(self, card, rect):
        return self._tap_card(card)

    def try_zhangba(self):
        """点击自己装备的丈八蛇矛：在房主下发的候选里选中"两张手牌当【杀】"。

        客户端不认识丈八的规则——它只是把"这件武器赋予的技能"记成本次要用的
        用法（``skill_id`` 来自点击的那件武器），然后照常选满两张手牌，最后
        由房主用引擎自己的规则复核。再点一次武器 = 取消已选的方式。
        """

        state = self.state
        weapon = self.view.player.get_equipment("weapon")
        skill_id = granted_skill_id(getattr(weapon, "name", None))
        if skill_id is None:
            return None
        entry, option = self._way_option_for_skill(skill_id)
        if option is None:
            state.notice = "现在不能发动【丈八蛇矛】"
            self.notice(state.notice)
            return None
        if state.way_chosen and str(state.action_id) == str(option.get("action_id") or ""):
            state.clear_selection()
            self.notice("已取消【丈八蛇矛】")
            return None
        state.cards = []
        state.targets = []
        state.choose_way(option, presentation.option_index(entry, option.get("action_id")))
        state.notice = presentation.sources_needed_text(option, state.cards) \
            or "请选择两张手牌"
        self.notice(state.notice)
        return None

    def _way_option_for_skill(self, skill_id):
        """房主下发的候选里某个技能的一种用法：``(entry, option)``。

        找不到就返回 ``(None, None)``——客户端不会自己造方式，也不会猜。
        """

        request = self.request
        if not isinstance(request, dict):
            return None, None
        wanted = str(skill_id or "")
        for entry in presentation.entries(request):
            for option in entry.get("options") or ():
                if not isinstance(option, dict):
                    continue
                if str(option.get("skill_id") or "") != wanted:
                    continue
                if not option.get("enabled", True):
                    continue
                return entry, option
        return None, None

    def _tap_card(self, card):
        card_id = str(getattr(card, "id", "") or "")
        if not card_id:
            return None
        return self._toggle_card(card_id)

    def _toggle_card(self, card_id):
        """点一张牌：按当前决策类型决定它算"要用的牌"还是"技能费用牌"。"""

        state = self.state
        view = self.view

        if state.configuring_skill:
            skill_input = view.pending_skill_input
            if skill_input is None:
                return None
            limit = skill_input["variable_cost"] and len(view.player.hand) \
                or int(skill_input["cost_cards"])
            if card_id not in {card.id for card in view.player.hand}:
                state.notice = "技能费用只能从自己的手牌里选"
                return None
            if not limit:
                return None
            if card_id in state.skill_cards:
                state.skill_cards.remove(card_id)
            elif len(state.skill_cards) < limit:
                state.skill_cards.append(card_id)
            state.notice = ""
            return None

        request = self.request or {}
        kind = request.get("kind")
        if kind == DecisionKind.PLAY_PHASE:
            return self._tap_play_card(card_id)
        if kind == DecisionKind.RESPOND_CARD:
            return self._tap_response_card(card_id)

        entry = presentation.play_card_entry(request, (card_id,))
        allowed = self._allowed_card_ids()
        if entry is not None and kind not in (
                DecisionKind.SELECT_CARDS, DecisionKind.SELECT_TARGETS):
            # 出牌阶段之外仍带"方式"的请求（新类型的用牌请求）：走同一条链路。
            return self._tap_play_card(card_id)
        if card_id not in (allowed or set()):
            state.notice = "这张牌现在不能选"
            return None
        return self._toggle_candidate(card_id)

    def _tap_play_card(self, card_id):
        """出牌阶段点一张牌：选 / 取消这张牌，并准备好它的使用方式。"""

        state = self.state
        request = self.request or {}
        entry = presentation.play_card_entry(request, (card_id,))
        allowed = self._allowed_card_ids()
        if entry is None or (allowed is not None and card_id not in allowed):
            state.notice = "这张牌房主没有列为现在可用"
            return None
        if card_id in state.cards:
            state.cards.remove(card_id)
            if not state.cards:
                state.way_chosen = False
                state.action_id = ""
                state.result_name = ""
            state.targets = []
            state.notice = ""
            return None
        if state.cards and state.way_chosen:
            # 已经选好方式、正在收集来源：继续加牌，加满 min_sources 才提交。
            return self._collect_source(card_id, entry)
        limit = max([presentation.play_source_limit(entry, item)
                     for item in presentation.play_options(entry)] or [1])
        if len(state.cards) >= limit:
            state.cards = state.cards[-(limit - 1):] if limit > 1 else []
        state.cards.append(card_id)
        state.targets = []
        state.notice = ""
        options = presentation.play_options(entry)
        preset = presentation.option_by_action(entry, state.action_id) \
            if state.way_chosen else None
        if preset is not None and preset.get("enabled", True):
            # 玩家已经选定了用法（例如点了自己装备的丈八蛇矛）：直接按它继续
            # 收集来源，不再要求他在"选择操作"面板里重复挑一次。
            return self._after_way_chosen(entry, preset)
        if len(options) == 1:
            # 只有一种用法：直接按它往下走（单机同序）。
            state.choose_way(options[0], 0)
            return self._after_way_chosen(entry, options[0])
        # 多种用法：弹出"选择操作"面板（渲染层读 way_chosen 决定弹不弹）。
        state.way_chosen = False
        state.action_id = ""
        state.result_name = ""
        if len(state.cards) > 1:
            state.notice = "请先选择使用方式"
        return None

    def _collect_source(self, card_id, entry):
        """多来源方式：继续往里加牌（超过上限先顶掉最早的一张）。"""

        state = self.state
        option = presentation.option_by_action(entry, state.action_id) \
            or presentation.play_option(entry, state.option_index)
        if option is None:
            state.notice = "这张牌现在不能这样使用"
            return None
        limit = presentation.play_source_limit(entry, option)
        if card_id in state.cards:
            state.cards.remove(card_id)
            state.notice = presentation.sources_needed_text(option, state.cards)
            return None
        if len(state.cards) >= limit:
            state.cards = state.cards[-(limit - 1):]
        state.cards.append(card_id)
        state.notice = ""
        return self._after_way_chosen(entry, option)

    def _tap_response_card(self, card_id):
        """响应窗口点一张牌。

        与出牌阶段同一套逻辑，唯一区别是"响应的实体牌可以不止一张"（两张手牌
        当【杀】打出）：第一张选中后**不能**直接提交，要继续选到该方式的
        ``min_sources`` 为止。
        """

        state = self.state
        request = self.request or {}
        entry = presentation.play_card_entry(request, (card_id,))
        allowed = self._allowed_card_ids()
        if entry is None or (allowed is not None and card_id not in allowed):
            state.notice = "这张牌不能用于当前响应"
            return None
        options = presentation.play_options(entry)
        if not options:
            state.notice = "这张牌现在不能用于当前响应"
            return None
        if card_id in state.cards:
            state.cards.remove(card_id)
            if not state.cards:
                state.way_chosen = False
                state.action_id = ""
                state.result_name = ""
            state.notice = ""
            return None
        if state.cards and state.way_chosen:
            return self._collect_source(card_id, entry)
        # 新选中的牌：先按它的第一种方式准备，再由 _apply_response_way 决定
        # 是否需要玩家自己挑（多方式时渲染层会弹"选择操作"面板）。
        state.cards = [card_id]
        state.targets = []
        preset = presentation.option_by_action(entry, state.action_id) \
            if state.way_chosen else None
        if preset is not None and preset.get("enabled", True):
            # 已经选定的用法（点武器选中的丈八蛇矛）：直接继续收集来源。
            state.notice = ""
            return self._after_way_chosen(entry, preset)
        if len(options) == 1:
            state.choose_way(options[0], 0)
            return self._after_way_chosen(entry, options[0])
        state.way_chosen = False
        state.action_id = ""
        state.result_name = ""
        state.notice = "这张牌有多种响应方式，请选择一种"
        return None

    def _toggle_candidate(self, card_id):
        """``SELECT_CARDS``（五谷公共牌 / 弃牌 / 拿牌…）的选中与提交。"""

        state = self.state
        if card_id in state.cards:
            state.cards.remove(card_id)
            state.notice = ""
            return None
        selection = self.view.pending_selection
        maximum = int((selection or {}).get("maximum") or 1) or 1
        if len(state.cards) >= maximum:
            state.cards = state.cards[-(maximum - 1):] if maximum > 1 else []
        state.cards.append(card_id)
        state.notice = ""
        # 与单机一致：选满"这次要选的张数"立刻生效，不再要求按确认。
        minimum = int((selection or {}).get("minimum") or maximum)
        if len(state.cards) >= minimum and maximum == minimum:
            return self._submit()
        return None

    # ==================================================
    # 点人
    # ==================================================

    def toggle_target(self, player):
        state = self.state
        player_id = str(getattr(player, "player_id", ""))
        if state.configuring_skill:
            state.skill_target = "" if state.skill_target == player_id else player_id
            state.notice = ""
            return None
        selection = self.view.pending_target_selection
        if player_id not in {item for item in state.targets} \
                and selection is not None \
                and len(state.targets) >= int(selection["maximum"]):
            state.notice = "最多只能选 %d 个目标" % int(selection["maximum"])
            return None
        if player_id in state.targets:
            state.targets.remove(player_id)
        else:
            state.targets.append(player_id)
        state.notice = ""
        if selection is not None and selection["maximum"] == 1 \
                and len(state.targets) == 1:
            # 与单机一致：只选一个目标时点一下就走（响应窗口没有目标，
            # 上面的 selection 为 None，不会走到这里）。
            return self._submit()
        return None

    # ==================================================
    # 技能
    # ==================================================

    def activate_skill(self, skill_id):
        state = self.state
        skill_id = str(skill_id or "")
        if state.skill_id == skill_id and state.configuring_skill:
            # 再点一次 = 取消发动
            self._reset_skill()
            return None
        entry = presentation.skill_entry(self.request, skill_id)
        if entry is None:
            definition = self.view.skill_registry.get(skill_id)
            if definition is not None and getattr(definition, "is_view_as", False):
                # 视为技（武圣 / 装备赋予的丈八蛇矛…）在客户端是"牌的一种
                # 用法"，不是一条可以单独回答的决策：给出可操作的指引。
                state.notice = "【%s】请点击武器或直接选择要转化的牌" % definition.name
                return None
            state.notice = "房主没有列出发动【" + skill_id + "】"
            return None
        if not entry.get("enabled", True):
            state.notice = str(entry.get("disabled_reason") or "现在不能发动")
            return None
        state.skill_id = skill_id
        state.skill_cards = []
        state.skill_target = ""
        state.cards = []
        state.targets = []
        state.action_id = ""
        state.result_name = ""
        state.way_chosen = False
        state.notice = ""
        # 不需要任何输入的技能（苦肉一类）：与单机一样按下即发动。
        if not entry.get("needs_target") and not entry.get("cost_cards") \
                and not entry.get("variable_cost"):
            return self._submit_skill()
        return None

    def _reset_skill(self):
        state = self.state
        state.skill_id = ""
        state.skill_cards = []
        state.skill_target = ""
        state.notice = ""

    # ==================================================
    # 提交
    # ==================================================

    def _play_phase_open(self):
        """我手里有没有一条**尚未回答**的出牌阶段决策（操作权限的唯一依据）。"""

        request = self.request
        if not isinstance(request, dict):
            return False
        return str(request.get("kind") or "") == DecisionKind.PLAY_PHASE

    def _answer(self, result):
        self.answer(result)
        return result

    def _submit(self):
        request = self.request
        if request is None:
            return None
        state = self.state
        kind = request.get("kind")

        if kind == DecisionKind.SELECT_CARDS:
            count = len(state.cards)
            selection = self.view.pending_selection or {}
            minimum = int(selection.get("minimum") or 0)
            maximum = int(selection.get("maximum") or minimum)
            if not minimum <= count <= maximum:
                state.notice = "请选择 %d～%d 张牌" % (minimum, maximum)
                return None
            return self._answer(DecisionResult(action=ACTION_SUBMIT,
                                               card_ids=state.cards))

        if kind == DecisionKind.SELECT_TARGETS:
            selection = self.view.pending_target_selection or {}
            minimum = int(selection.get("minimum") or 0)
            maximum = int(selection.get("maximum") or minimum)
            if not minimum <= len(state.targets) <= maximum:
                state.notice = "请选择 %d～%d 个目标" % (minimum, maximum)
                return None
            return self._answer(DecisionResult(action=ACTION_SUBMIT,
                                               target_ids=state.targets))

        if kind == DecisionKind.RESPOND_CARD:
            entry = presentation.play_card_entry(request, state.cards)
            option = self._current_option(entry)
            if option is None:
                state.notice = "先选一张牌，或点「不出」"
                return None
            if not presentation.play_sources_ready(entry, option, state.cards):
                state.notice = "这次需要先选满 %d 张牌" % int(
                    option.get("min_sources") or 1)
                return None
            return self._answer(DecisionResult(
                action=ACTION_SUBMIT, card_ids=state.cards,
                skill_id=str(option.get("skill_id") or ""),
                action_id=str(option.get("action_id") or ""),
                result_name=str(option.get("result_name") or "")))

        if kind == DecisionKind.PLAY_PHASE:
            return self._submit_play()

        if kind == DecisionKind.CHOOSE_OPTION:
            value = request.get("options") or []
            state.notice = "请在上方选择一个选项"
            return None if value else None

        if kind == DecisionKind.CONFIRM:
            state.notice = "请选择「发动」或「不发动」"
            return None
        return None

    def _submit_play(self):
        request = self.request
        state = self.state
        if not state.cards:
            state.notice = "先选一张牌，或点「结束回合」"
            return None
        entry = presentation.play_card_entry(request, state.cards)
        option = self._current_option(entry)
        if entry is None or option is None:
            state.notice = "这张牌现在不能使用，请重新选择"
            return None
        if not presentation.play_sources_ready(entry, option, state.cards):
            state.notice = "这次需要先选满 %d 张牌" % int(
                option.get("min_sources") or 1)
            return None
        minimum = int(option.get("min_targets") or 0)
        maximum = int(option.get("max_targets") or 0)
        if not minimum <= len(state.targets) <= maximum:
            state.notice = "这张牌需要选 %d～%d 个目标" % (minimum, maximum)
            return None
        return self._answer(DecisionResult(
            action=ACTION_SUBMIT, card_ids=state.cards,
            target_ids=state.targets,
            skill_id=str(option.get("skill_id") or ""),
            action_id=str(option.get("action_id") or ""),
            result_name=str(option.get("result_name") or "")))

    def _current_option(self, entry):
        """当前选定的方式：优先按 action_id 精确匹配，其次按下标兜底。

        玩家还没挑过方式时返回 None——调用方据此给出"请先选择使用方式"，
        而不是默默按第一种方式提交（那正是"点了第二种却按第一种结算"的根因）。
        """

        state = self.state
        option = presentation.option_by_action(entry, state.action_id)
        if option is not None and option.get("enabled", True):
            return option
        if state.way_chosen:
            return None
        options = presentation.play_options(entry)
        if len(options) == 1:
            return options[0]
        return None

    def _submit_skill(self):
        request = self.request
        state = self.state
        if request is None:
            return None
        entry = presentation.skill_entry(request, state.skill_id)
        if entry is None:
            return None
        if int(entry.get("cost_cards") or 0) and len(state.skill_cards) != int(entry["cost_cards"]):
            state.notice = "这个技能需要 %d 张费用牌" % int(entry["cost_cards"])
            return None
        if entry.get("variable_cost") and not state.skill_cards:
            state.notice = "至少选择一张牌"
            return None
        if entry.get("needs_target") and not state.skill_target:
            state.notice = "先点一个角色作为目标"
            return None
        return self._answer(DecisionResult(
            action=ACTION_SKILL, skill_id=state.skill_id,
            card_ids=state.skill_cards,
            target_ids=[state.skill_target] if state.skill_target else []))

    def _cancel(self):
        kind = (self.request or {}).get("kind")
        if kind == DecisionKind.PLAY_PHASE:
            return self._answer(DecisionResult(action=ACTION_END_PHASE))
        if kind == DecisionKind.RESPOND_CARD:
            return self._answer(DecisionResult(action=ACTION_PASS))
        return self._answer(DecisionResult(action=ACTION_CANCEL))

    # ==================================================
    # 工具
    # ==================================================

    def _allowed_card_ids(self):
        """本次请求允许的牌 id（手牌 / 装备 / 公共区共用同一个集合）。"""

        allowed = {
            str(item.get("card_id") or "")
            for item in presentation.entries(self.request)
            if item.get("card_id")
        }
        if not allowed:
            return None
        return allowed

    def cleanup(self):
        self.request = None
        self.state.notice = ""
