from src.actions import (
    ActionQueue,
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.constants import (
    DISCARD_PILE_RECT,
    DRAW_PILE_RECT,
)

from src.choice import ChoiceSystem
from src.deck import Deck
from src.player import ControllerType, Player
from src.response import ResponseSystem

import random

from .turn import TurnMixin
from .card_action_session import CardActionSessionMixin
from .card_actions import CardActionDiscovery
from .card_selection import CardSelectionMixin
from .basic_cards import BasicCardMixin
from .equipment import EquipmentMixin
from .combat import CombatMixin
from .dying import DyingMixin
from .ai import AIMixin
from .engine import GameContext, GameEngine
from .judge_gate import JudgeGate
from .controllers import AIController, HumanController
from .rules import SeatManager
from .conversion import ConversionRegistry
from .skills import (
    ModifierKind,
    ModifierRegistry,
    SkillManager,
    SkillState,
    create_default_skill_registry,
)
from .available_actions import AvailableActions
from .generals import (
    DRAFT_SIZE,
    GeneralDraft,
    create_default_general_registry,
    resolve_pool,
)
from .invariants import armed_for, assert_card_ownership
from .modes import create_default_mode_registry


class Game(
    CardSelectionMixin,
    CardActionSessionMixin,
    TurnMixin,
    BasicCardMixin,
    EquipmentMixin,
    CombatMixin,
    DyingMixin,
    AIMixin,
):

    def __init__(self, ai_count=1):

        self.scene = "menu"
        self.menu_message = "请选择游戏模式"

        # 统一随机源：选将、洗牌、AI 分配与 AI 决策都走它，测试可以注入
        # 固定种子。**必须在建牌堆之前创建**：Deck 用它洗牌，否则"固定种子"
        # 覆盖不到牌堆（1v1 测试模式的可复现性正是靠这一点）。
        self.rng = random.Random()
        # AI 控制器使用的随机源；None = 每个 AI 自带一个无种子的流（旧行为）。
        # 1v1 测试模式在固定种子时把它指向全局 rng，让 AI 决策也可复现。
        self.ai_rng = None

        self.deck = Deck(self.rng)

        self.ai_count = max(1, min(7, int(ai_count)))
        self.players = []
        self.controllers = {}
        self._create_players()
        self.seats = SeatManager(self)

        self.actions = ActionQueue()

        self.response = ResponseSystem()

        self.choice = ChoiceSystem()

        # Engine V2 starts as a side-by-side adapter.  Existing gameplay still
        # uses the legacy Game object directly; future vertical slices can use
        # context events/atoms without forcing a big-bang rewrite.
        self.context = GameContext(state=self)
        self.engine = GameEngine(self.context)
        # 判定优先闸门：判定没走完之前全场只接受判定输入（见 judge_gate.py）。
        self.judge_gate = JudgeGate(self)

        # ==================================================
        # 武将 / 技能基础设施
        #
        # generals：武将定义表（纯数据）
        # skill_registry：技能定义表（纯数据）
        # skills：把技能绑定到具体角色，并管理生命周期
        # modifiers：持续规则修正（距离 / 手牌上限 / 摸牌数 / 出杀 / 攻击范围）
        # ==================================================

        self.generals = create_default_general_registry()
        self.skill_registry = create_default_skill_registry()
        self.modifiers = ModifierRegistry(self)
        self.conversions = ConversionRegistry(self)
        self.skills = SkillManager(self, self.skill_registry)

        # UI 注入：Renderer 每帧写入当前分辨率下的动画落点与布局度量。
        # 引擎只读它来决定飞牌动画的起止位置，规则不依赖它。
        self.ui_rects = {}
        self.ui_metrics = None

        # 非空时开局按座次分配武将（不重复）；默认留空以保持无武将体验。
        self.general_pool = ()
        self.general_assignments = {}
        # 真人选定的武将（{player_id: general_id}）：单机由选将界面写，
        # 联机由网络桥收集每个真人的选择后写进来，分配时优先采用。
        self.general_picks = {}
        # 1v1 测试模式的开战前设置（双方武将 / 先手 / 操作方式 / 种子）。
        # 单机与联机共用同一个 Game，所以配置挂在 game 上，由模式自己读写。
        self.duel_config = None
        # 选将界面状态（Phase 8）：真人选定的武将 id。
        self.selected_general = None
        # 选将候选（按模式决定数量）；空表示不做限制。
        self.general_candidates = ()
        # 「我的武将池」（本机长期偏好）：**只影响本机真人的候选**。
        # 它是客户端本地设置，不进存档、不进快照、不广播给其他玩家；
        # 空表示"没配置过"→ 候选退回本模式全部可用武将。
        self.favorite_general_ids = ()
        #: 距离修正的重入闸门（见 distance_modifier：防止修正自己再算距离时递归）
        self._distance_query_active = False
        #: 本局候选的生成结果（一次生成、之后只读，见 generals/draft.py）。
        self.general_draft = GeneralDraft(size=DRAFT_SIZE)
        #: 这次候选到底是按偏好池抽的，还是回退到全部可用武将（界面提示用）。
        self.draft_used_favorites = False

        # ==================================================
        # 游戏模式
        #
        # 模式负责：允许人数、身份分配、开局规则、胜负条件、死亡奖惩。
        # 核心流程只通过 self.mode 的钩子调用，不做模式业务特判。
        # ==================================================

        self.modes = create_default_mode_registry()
        self.mode_id = "ffa"
        self.mode = self.modes.require(self.mode_id)(self)
        # 身份展示阶段用的身份（开局重建 players 后再写回）。
        self.pending_identities = ()

        # Card Action Discovery：实体牌"现在能做什么"的唯一查询层。
        self.card_actions = CardActionDiscovery(self)
        self.pending_card_action = None
        self.pending_view_as = None

        # 主动技能的真人输入收集（Phase 8 hotfix）：
        # picker 非空表示正在选择要发动的技能；input 非空表示正在收集参数。
        self.pending_skill_picker = None
        self.pending_skill_input = None

        # 武器与防具效果需要玩家点选具体卡牌时使用。
        self.pending_selection = None
        self.pending_target_selection = None
        self.pending_skill_picker = None
        self.pending_skill_input = None

        # 当前停留在桌面上的牌
        self.table_cards = []

        self.deal_presentation = None
        self.judge_card = None
        self.judge_context = None
        self.processing_zone = []
        self.public_card_pool = []
        self.revealed_card = None
        self.current_turn_player = self.player
        self.turn_phase = None
        self.skipped_phases = set()
        self.active_turn_flow = None
        # 额外回合队列（放权 / 连破）：由 TurnMixin.start_next_turn 消费。
        self.extra_turns = []

        self.phase = "play"

        self.message = ""

        self.game_over = False
        self.result = None
        self.winner = None
        self.game_log = []
        self._stall_frames = 0
        self.speed = self.DEFAULT_SPEED
        # 真实对局开启节奏模式：AI 响应排队出现，玩家能逐个看清。
        # 规则测试保持同步语义，因此这里默认关闭。
        self.ai_pacing = False
        # 牌唯一归属检查（同一张实体牌不许同时属于两个牌区）。
        # None = "跟随全局开关"，**不把默认值冻结下来**，否则
        # invariants.enable_debug() 对已经建好的对局就不起作用了。
        # 显式写 True / False 则永久优先于全局开关。见 src/game/invariants.py。
        self.assert_card_ownership = None
        # 远程真人控制器由网络桥注入（见 start_networked_battle）；没有桥时
        # REMOTE_HUMAN 角色退回 AI，避免无头环境下整局卡死。
        self.remote_controller_factory = None
        # 当前联网对局的标识（网络桥写入；单机为 None）。
        self.match_id = None

        self.reset()

        # 首次启动停留在开始界面；游戏内的重新开始仍由
        # reset() 直接重开单人局。
        self.scene = "menu"


    def _create_players(self):
        self.player = Player(
            "玩家", gender="male", player_id="P0", seat=0,
            controller_type=ControllerType.HUMAN,
        )
        ais = [
            Player(
                "AI " + str(index),
                gender="female" if index % 2 else "male",
                player_id="P" + str(index), seat=index,
                controller_type=ControllerType.AI,
            )
            for index in range(1, self.ai_count + 1)
        ]
        self.players = [self.player] + ais
        for player in self.players:
            # Player 不反向依赖技能包，状态容器由 Game 注入。
            player.skill_state = SkillState()
        # Compatibility façade: the first AI remains available to old 1v1 UI
        # and tests. New rules must use players/seats APIs.
        self.enemy = ais[0]

    # ==================================================
    # 回合状态 façade
    #
    # 真正的存储位置是每个 Player 自己的字段；这些属性只保留给旧 1v1
    # UI 与旧测试使用。多人规则必须直接读 player.sha_used 等实例字段。
    # ==================================================

    @property
    def sha_used(self):
        return self.player.sha_used

    @sha_used.setter
    def sha_used(self, value):
        self.player.sha_used = bool(value)

    @property
    def jiu_used(self):
        return self.player.jiu_used

    @jiu_used.setter
    def jiu_used(self, value):
        self.player.jiu_used = bool(value)

    @property
    def player_wine_buff(self):
        return self.player.wine_buff

    @player_wine_buff.setter
    def player_wine_buff(self, value):
        self.player.wine_buff = bool(value)

    @property
    def wine_sha_required(self):
        return self.player.wine_sha_required

    @wine_sha_required.setter
    def wine_sha_required(self, value):
        self.player.wine_sha_required = bool(value)

    @property
    def enemy_wine_buff(self):
        enemy = self.enemy
        return enemy.wine_buff if enemy is not None else False

    @enemy_wine_buff.setter
    def enemy_wine_buff(self, value):
        enemy = self.enemy
        if enemy is not None:
            enemy.wine_buff = bool(value)

    @property
    def enemy_jiu_used(self):
        enemy = self.enemy
        return enemy.jiu_used if enemy is not None else False

    @enemy_jiu_used.setter
    def enemy_jiu_used(self, value):
        enemy = self.enemy
        if enemy is not None:
            enemy.jiu_used = bool(value)

    def get_player(self, player_id):
        return next((p for p in self.players if p.player_id == player_id), None)

    # ==================================================
    # Controller 边界
    #
    # TurnFlow 只依据 current_player.controller_type 决定 Action 来源：
    # 真人走 Pygame 输入适配器，AI 走本地 AI 控制器。未来的局域网
    # RemoteController 也会挂在这里，不需要改动规则层。
    # ==================================================

    def get_controller(self, player):
        controller = self.controllers.get(player.player_id)
        if controller is None:
            controller = self.create_controller(player)
            self.controllers[player.player_id] = controller
        return controller

    def create_controller(self, player):
        """按角色身份创建默认控制器。

        规则层只认 ``controller_type``，不认"是不是网络玩家"：远程真人走
        网络桥注入的工厂，本地真人走 Pygame 输入适配器，其余交给 AI。
        """

        if player.controller_type is ControllerType.REMOTE_HUMAN:
            factory = self.remote_controller_factory
            if factory is not None:
                return factory(self, player)
            # 没有网络桥（无头测试 / 单机加载到远程座位）：用 AI 顶上，
            # 保证流程能走完，而不是永远等一个不会来的响应。
            return AIController(self, player, rng=self.ai_rng)
        if player.is_human:
            return HumanController(self, player)
        return AIController(self, player, rng=self.ai_rng)

    # ==================================================
    # 当前操作者（本机鼠标现在代表谁）
    #
    # 单机默认就是 self.player 本人，视角永远不动。1v1 测试的**双边手动**
    # 模式里两名角色都由本机操作，因此界面要把视角跟着"正在决策的人"切换
    # —— 手牌、按钮、提示全都挂在 game.player 上，所以切换它就是切换视角。
    #
    # 只有声明 ``tracks_operator`` 的模式才会生效：其它模式调用它不会有
    # 任何影响（FFA / 身份局 / 联机一行行为都不变）。
    # ==================================================

    def set_operator(self, player):
        """直接把视角切到这名角色；模式不跟踪操作者时什么都不做。"""

        if player is None or not getattr(self.mode, "tracks_operator", False):
            return self.player
        if any(item is player for item in self.players):
            self.player = player
        return self.player

    def sync_operator(self):
        """按模式的判断把视角切到"现在该由谁操作"的角色。"""

        if not getattr(self.mode, "tracks_operator", False):
            return self.player
        target = self.mode.operator_target(self)
        if target is not None:
            self.set_operator(target)
        return self.player

    @property
    def local_operator_label(self):
        """界面上"当前操作"的写法：我方 / 对手（＋武将名）；无模式说明时返回空。"""

        mode = getattr(self, "mode", None)
        label = getattr(mode, "side_label", None)
        if not callable(label):
            return ""
        return str(label(self.player))

    @property
    def waiting_for_remote(self):
        """是否有远程真人的决策还没回答（卡死守卫必须因此停手）。"""

        for controller in self.controllers.values():
            if getattr(controller, "waiting", False):
                return True
        return False

    # ==================================================
    # 武将
    #
    # 绑定顺序固定为座次顺序：EventDispatcher 先按 priority 再按注册顺序
    # 分发，因此同一事件上的多个技能拥有稳定、可复现的触发顺序。
    # ==================================================

    def set_general(self, player, general_id):
        general = self.generals.get(general_id) if general_id else None
        if general is None:
            raise KeyError("unknown general: " + str(general_id))
        self.skills.unbind_all(player)
        player.set_general(general)
        self.skills.bind_general(player)
        return general

    def clear_general(self, player):
        self.skills.unbind_all(player)
        player.set_general(None)
        return player

    def assign_generals(self, mapping=None, human_general=None):
        """按座次分配武将：真人用 human_general，AI 从池中不重复抽取。"""

        assignments = dict(self.general_assignments if mapping is None else mapping)
        pool = [general_id for general_id in (self.general_pool or ()) if general_id]
        # 显式指定（选将结果 / 联机 picks / 直通入口）也要过一遍可用性：
        # 一条非法 id 混进来会让整局在绑定技能时炸掉，而不是被安静地跳过。
        pool = [general_id for general_id in pool
                if self.general_available(general_id)[0]]
        assignments = {
            key: value for key, value in assignments.items()
            if value is None or self.general_available(value)[0]
        }
        order = sorted(self.players, key=lambda player: player.seat)

        used = set()
        for player in order:
            general_id = assignments.get(player.seat, assignments.get(player.player_id))
            if general_id:
                used.add(general_id)
        if human_general:
            used.add(human_general)

        assigned = []
        for player in order:
            general_id = assignments.get(player.seat, assignments.get(player.player_id))
            if general_id is None and player.is_human and human_general:
                general_id = human_general
            if general_id is None and pool:
                available = [item for item in pool if item not in used] or pool
                general_id = self.rng.choice(available)
            if general_id:
                used.add(general_id)
                self.set_general(player, general_id)
            assigned.append(player.general_id)
        return tuple(assigned)

    def general_of(self, player):
        return self.generals.get(player.general_id)

    def owner_of_zone(self, container):
        """这个区域列表属于哪名角色（手牌 / 装备 / 判定区 / 武将牌上的牌区）。

        弃牌事件需要知道"这张牌原本是谁的"，而事件发生时牌已经离开原区域，
        所以按**区域对象的身份**反查，而不是按牌反查。
        """

        if container is None:
            return None
        for player in getattr(self, "players", ()) or ():
            if getattr(player, "hand", None) is container:
                return player
            if getattr(player, "judgement_zone", None) is container:
                return player
            for zone in (getattr(player, "placed_cards", None) or {}).values():
                if zone is container:
                    return player
            for equipped in (getattr(player, "equipment", None) or {}).values():
                if equipped is container:
                    return player
        return None

    def owner_of_card(self, card):
        """这张实体牌现在在谁的区域里（不在任何角色区域时返回 None）。"""

        for player in getattr(self, "players", ()) or ():
            if any(item is card for item in getattr(player, "hand", ()) or ()):
                return player
            if any(item is card for item in getattr(player, "judgement_zone", ()) or ()):
                return player
            for zone in (getattr(player, "placed_cards", None) or {}).values():
                if any(item is card for item in zone):
                    return player
            for slot, equipped in (getattr(player, "equipment", None) or {}).items():
                if equipped is card:
                    return player
        return None

    def card_suit(self, card, owner=None):
        """一张牌的**有效花色**：先按拥有者的花色改写能力，再看印刷花色。

        红颜一类"你的黑桃牌视为红桃"的能力在这里统一生效，规则层不认具体
        武将——查询走 modifier，界面与结算读的是同一个结果。
        """

        modifier = self.modifiers.suit_override(card, owner)
        if modifier is not None:
            return modifier
        return getattr(card, "suit", None)


    # ==================================================
    # source card 移动
    #
    # 转化的 source 可能来自手牌，也可能来自装备区（由 Conversion 声明）。
    # 引擎不假设"只有手牌"，移动时按牌真正所在的区域处理。
    # ==================================================

    def source_container(self, player, card):
        """这张实体牌当前属于哪个区域（hand / equipment / None）。"""

        return self.card_actions.zone_of(player, card)

    def move_source_card_to_processing(self, player, card):
        """把 source card 从它所在区域移入处理区。"""

        from .atoms_v2 import MoveCardAtom

        if any(item is card for item in player.hand):
            self.context.apply(MoveCardAtom(
                card, source=player.hand, destination=self.processing_zone))
            return True

        for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
            if player.get_equipment(slot) is card:
                # 装备区的牌要先经过失去装备的规则（枭姬一类）。
                self.remove_equipment_with_effects(player, slot)
                self.processing_zone.append(card)
                return True

        raise ValueError("card is no longer in the player's zones")

    # ==================================================
    # 主动技能：真人输入收集
    #
    # 真人先把目标与费用牌收集齐，再一次性提交 ActivateSkillAction；
    # 因此取消不会写 used 标记，也不会提前弃牌。
    # ==================================================

    def skill_activation_options(self):
        """「发动技能」里能列出的技能：ACTIVE（可发动）+ VIEW_AS（可进入）。

        ACTIVE 与 VIEW_AS 的语义不同（前者执行技能流程，后者进入选牌模式），
        但入口统一，玩家不需要知道区别。
        """

        options = list(self.skills.activatable_skills(self.player))
        for skill_id, allowed, _reason in self.view_as_options():
            if allowed and skill_id not in options:
                options.append(skill_id)
        return options

    def skill_picker_rows(self):
        """Skill Picker 的展示数据：[(skill_id, allowed, reason)]。"""

        rows = []
        for skill_id in self.skills.active_skill_ids(self.player):
            allowed, reason = self.skills.can_activate(self.player, skill_id)
            rows.append((skill_id, allowed, "" if allowed else reason))
        for skill_id, allowed, reason in self.view_as_options():
            if any(item[0] == skill_id for item in rows):
                continue
            rows.append((skill_id, allowed, reason))
        return rows

    def begin_skill_activation(self):
        """ActionBar 的「发动技能」：0 个不可用 / 1 个直接进入 / 多个打开面板。"""

        options = self.skill_activation_options()
        if not options:
            self.message = "当前没有可以发动的技能。"
            return False
        if len(options) == 1:
            return self.start_skill_activation(options[0])
        self.pending_skill_picker = self.skill_picker_rows()
        self.message = "请选择要发动的技能。"
        return True

    def start_skill_activation(self, skill_id):
        definition = self.skill_registry.get(skill_id)
        if definition is not None and definition.is_view_as:
            # 视为技：进入"先选技能、再选牌"的模式，不走 ActivateSkillAction。
            self.pending_skill_picker = None
            if self.begin_view_as(skill_id):
                return True
            return False

        allowed, reason = self.skills.can_activate(self.player, skill_id)
        if not allowed:
            self.message = reason
            self.pending_skill_picker = None
            return False

        definition = self.skill_registry.require(skill_id)
        spec = definition.active_spec
        self.pending_skill_picker = None

        if spec is None or (
            not spec.needs_target
            and not spec.cost_cards
            and not spec.variable_cost
        ):
            return self._submit_skill(skill_id)

        # 目标候选与费用张数走**共同查询**（与 AI / Remote 同一份
        # activation_inputs），本地界面不再自己拼一份。
        inputs = AvailableActions(self).skill_inputs(self.player, skill_id)
        targets = list(inputs.get("targets") or ()) if inputs.get("needs_target") else []
        if inputs.get("needs_target") and not targets:
            self.message = "没有合法目标。"
            return False

        self.pending_skill_input = {
            "skill_id": skill_id,
            "name": definition.name,
            "needs_target": bool(inputs["needs_target"]),
            "target_prompt": spec.target_prompt,
            "cost_cards": int(inputs.get("cost_cards") or 0),
            "cost_prompt": spec.cost_prompt,
            "variable_cost": bool(inputs.get("variable_cost")),
            "max_cost_cards": int(inputs.get("max_cost_cards") or 0),
            "transfer_cards": bool(inputs.get("transfer_cards")),
            # 玩家能自己挑的牌：与引擎校验、远程下发的候选是**同一份判断**
            # （见 activation.cost_candidates），所以界面高亮出来的牌
            # 一定是引擎会接受的牌。
            "cost_candidates": list(inputs.get("cost_candidates") or ()),
            "targets": targets,
            "target": None,
            "cards": [],
        }
        self._update_skill_input_message()
        return True

    def _update_skill_input_message(self):
        """提示文案来自 SkillDef 的 spec，并给出当前选择进度。"""

        state = self.pending_skill_input
        if state is None:
            return
        parts = []
        if state["needs_target"]:
            if state["target"] is None:
                parts.append(state["target_prompt"] or "请选择目标")
            else:
                parts.append("目标：" + state["target"].name)
        if state["cost_cards"] or state.get("variable_cost"):
            chosen = len(state["cards"])
            required = int(state["cost_cards"])
            limit = required or self.skill_cost_limit() or len(self.player.hand)
            if chosen < required:
                parts.append(
                    state["cost_prompt"]
                    or ("请选择 %d 张手牌弃置" % required)
                )
            elif required:
                parts.append(state["cost_prompt"] or "")
            parts.append("已选 %d/%d 张" % (chosen, limit))
        if self.skill_input_ready():
            parts.append("点击「确认发动」结算")
        self.message = "　".join(parts)

    def toggle_skill_target(self, target):
        state = self.pending_skill_input
        if state is None or not state["needs_target"]:
            return False
        if not any(target is candidate for candidate in state["targets"]):
            return False
        state["target"] = None if state["target"] is target else target
        self._update_skill_input_message()
        return True

    def skill_cost_limit(self):
        """这次发动玩家最多能挑几张牌（0 = 这次不需要挑牌）。"""

        state = self.pending_skill_input
        if state is None:
            return 0
        if state.get("variable_cost"):
            cap = int(state.get("max_cost_cards") or 0)
            limit = min(len(self.player.hand), cap) if cap else len(self.player.hand)
            return limit
        return int(state["cost_cards"])

    def select_skill_cost_card(self, card):
        state = self.pending_skill_input
        if state is None:
            return False
        limit = self.skill_cost_limit()
        if not limit:
            return False
        candidates = state.get("cost_candidates")
        if candidates is not None and not any(card is item for item in candidates):
            # 不是这次发动的合法素材：点它什么也不发生，更不替玩家改选。
            self.message = state.get("cost_prompt") or "这张牌不能用于这次发动。"
            return False
        if any(item is card for item in state["cards"]):
            state["cards"].remove(card)
        elif len(state["cards"]) < limit:
            state["cards"].append(card)
        self._update_skill_input_message()
        return True

    def skill_input_ready(self):
        state = self.pending_skill_input
        if state is None:
            return False
        if state["needs_target"] and state["target"] is None:
            return False
        if state.get("variable_cost"):
            # 可变费用（制衡 / 仁德 / 举荐）：至少要选一张，上限由手牌或
            # 规则给出的张数（max_cost_cards）决定。
            if not state["cards"]:
                return False
            return True
        if len(state["cards"]) < state["cost_cards"]:
            return False
        return True

    def confirm_skill_input(self):
        state = self.pending_skill_input
        if state is None:
            return False
        if not self.skill_input_ready():
            self.message = "还没有完成技能所需的选择。"
            return False
        return bool(self._submit_skill(
            state["skill_id"], state["target"], list(state["cards"])))

    def cancel_skill_input(self):
        """取消技能发动：不写 used 标记，也不消耗任何牌。"""

        if self.pending_skill_input is None and self.pending_skill_picker is None:
            return False
        name = self.pending_skill_input["name"] if self.pending_skill_input else ""
        self.pending_skill_input = None
        self.pending_skill_picker = None
        self.message = ("已取消发动【" + name + "】。") if name else "已取消。"
        return True

    def _submit_skill(self, skill_id, target=None, cards=None):
        from .engine import ActivateSkillAction

        result = self.submit_action(ActivateSkillAction(
            self.player, skill_id, target=target, cards=list(cards or ()),
        ))
        if isinstance(result, tuple):
            ok = bool(result[0])
            if not ok and len(result) > 1 and result[1]:
                self.message = str(result[1])
        else:
            ok = bool(result)
        self.pending_skill_input = None
        self.pending_skill_picker = None
        return ok

    # ==================================================
    # 规则查询
    #
    # 技能的持续影响统一从这里取，规则层不直接查技能表。
    # ==================================================

    def distance_modifier(self, source, target):
        """距离修正合计（马术 / 飞影 / 陷阵一类）。

        # 防递归闸门

        某个修正自己的 ``value`` 里再去算距离是完全可能的（"把与某人的距离
        视为 0"这类语义），那样就会 距离 → 修正 → 距离 → … 无限递归，把
        整局打成 RecursionError。这里用一个重入标记：**修正过程中**再算距离
        时，修正合计按 0 处理（也就是"那一次查询看到的是基础距离"），
        于是任何写法都不会无界递归，语义上也正好是"先拿到基数再减掉"。
        """

        if not hasattr(self, "modifiers"):
            return 0
        if self._distance_query_active:
            return 0
        self._distance_query_active = True
        try:
            return (
                self.modifiers.total(ModifierKind.DISTANCE_OUTGOING, source=source, target=target)
                + self.modifiers.total(ModifierKind.DISTANCE_INCOMING, source=source, target=target)
            )
        finally:
            self._distance_query_active = False

    def attack_range_bonus(self, player):
        if not hasattr(self, "modifiers"):
            return 0
        return self.modifiers.total(ModifierKind.ATTACK_RANGE, player=player)

    def armor_card(self, player):
        """这名角色**实际装备着**的防具牌（没有则 None）。"""

        return getattr(player, "get_equipment", lambda slot: None)("armor")

    def virtual_armor(self, player):
        """技能赋予的虚拟防具牌名（八阵：没有防具时视为装备八卦阵）。

        真实防具优先：装备了防具时虚拟防具不生效，这是卡面写明的条件。
        """

        if player is None or self.armor_card(player) is not None:
            return None
        if not hasattr(self, "modifiers"):
            return None
        for modifier in self.modifiers.sorted_for(ModifierKind.VIRTUAL_ARMOR):
            if modifier.owner is not player:
                continue
            if not modifier.matches(self, {"player": player}):
                continue
            value = modifier.value
            return value(self, {"player": player}) if callable(value) else value
        return None

    def trick_source(self, user, card):
        """这张锦囊牌造成伤害时的**真实来源**。

        默认就是使用者；【祸首】一类能力通过 TRICK_SOURCE modifier 改写它
        （"你是任何【南蛮入侵】造成伤害的来源"）。伤害流程统一读这里。
        """

        if not hasattr(self, "modifiers"):
            return user
        query = {"user": user, "card": card}
        for modifier in self.modifiers.sorted_for(ModifierKind.TRICK_SOURCE):
            if modifier.owner is user:
                continue
            if not modifier.matches(self, query):
                continue
            value = modifier.value
            if callable(value):
                if value(self, query):
                    return modifier.owner
            elif value:
                return modifier.owner
        return user

    def trick_distance_limit(self, actor, card):
        """这张锦囊牌对 ``actor`` 的距离上限（None = 由卡牌自身决定）。"""

        base = None
        effects = getattr(getattr(self, "engine", None), "card_effects", None)
        effect = effects.get(card) if (effects is not None and card is not None) else None
        if effect is not None:
            # 读**类属性**上的声明值：走 effect.distance_limit_for 会与本方法
            # 互相递归（它就调用本方法）。
            base = type(effect).distance_limit
        if not hasattr(self, "modifiers") or card is None:
            return base
        query = {"player": actor, "card": card}
        for modifier in self.modifiers.sorted_for(ModifierKind.TRICK_DISTANCE):
            if modifier.owner is not actor:
                continue
            if not modifier.matches(self, query):
                continue
            value = modifier.value
            value = value(self, query) if callable(value) else value
            if value is None:
                continue
            base = int(value) if base is None else max(base, int(value))
        return base

    def rescue_forbidden(self, rescuer, dying_player, card_name="TAO"):
        """濒死救援是否被禁止（完杀一类）。

        返回 True 表示"这名救援者现在不能对这名濒死角色使用这张救援牌"。
        门槛由 modifier 声明（``RESCUE_FORBIDDEN``，归属施加限制的角色），
        救援流程只读结论，不认任何具体武将。
        """

        if not hasattr(self, "modifiers"):
            return False
        query = {"rescuer": rescuer, "dying": dying_player, "card_name": card_name}
        for modifier in self.modifiers.sorted_for(ModifierKind.RESCUE_FORBIDDEN):
            if not modifier.matches(self, query):
                continue
            # 判定体需要知道"是谁在施加限制"（完杀只看自己的回合），
            # 而 modifier 的 value 是共享的可调用对象——由查询在这里补上拥有者。
            scoped = dict(query, owner=modifier.owner)
            value = modifier.value
            if callable(value):
                if value(self, scoped):
                    return True
            elif value:
                return True
        return False

    def has_armor(self, player, name):
        """这名角色现在**等效装备着**哪件防具：真实防具优先，其次是虚拟防具。

        防具效果的判定统一走它，装备控制器与技能都不再直接读 equipment——
        八阵这类"视为装备"的能力才能真的生效。
        """

        armor = self.armor_card(player)
        if armor is not None:
            return getattr(armor, "name", None) == name
        return self.virtual_armor(player) == name

    def hand_limit(self, player):
        base = max(0, player.hp)
        if not hasattr(self, "modifiers"):
            return base
        return max(0, base + self.modifiers.total(ModifierKind.HAND_LIMIT, player=player))

    def draw_count(self, player, base=2):
        if not hasattr(self, "modifiers"):
            return base
        return max(0, base + self.modifiers.total(ModifierKind.DRAW_COUNT, player=player))

    def ignores_trick_range(self, player):
        if not hasattr(self, "modifiers"):
            return False
        return self.modifiers.total(ModifierKind.TRICK_RANGE_IGNORE, player=player) > 0

    def slash_quota(self, player):
        if not hasattr(self, "modifiers"):
            return 0
        return self.modifiers.total(ModifierKind.SLASH_QUOTA, player=player)

    def slash_target_bonus(self, player, card=None):
        """【杀】可额外指定的目标数（天义 +1 / 神戟 至多三名）。

        负值会被夹到 0：技能只声明"多打几个"，不会把一张牌的目标数改没。
        """

        if not hasattr(self, "modifiers"):
            return 0
        return max(0, int(self.modifiers.total(
            ModifierKind.SLASH_TARGETS, player=player, card=card)))

    def category_forbidden(self, player, card):
        """这张牌现在是否被技能锁住（鸡肋：不能使用 / 打出 / 弃置这个类别）。

        锁是**按类别**而不是按牌记的：对方手里原有的牌与新摸到的同类别牌
        一并受限，直到本回合结束由技能自己解除。
        """

        if not hasattr(self, "modifiers") or player is None or card is None:
            return False
        category = getattr(card, "category", None)
        if category is None:
            return False
        query = {"player": player, "card": card, "category": category}
        for modifier in self.modifiers.sorted_for(ModifierKind.CATEGORY_FORBIDDEN):
            if modifier.owner is not player:
                continue
            if not modifier.matches(self, query):
                continue
            value = modifier.value
            value = value(self, query) if callable(value) else value
            if value == category:
                return True
        return False

    def slash_ignores_distance(self, player, card=None):
        """这张【杀】是否无视距离限制（武神）。"""

        if not hasattr(self, "modifiers"):
            return False
        query = {"player": player, "card": card}
        for modifier in self.modifiers.sorted_for(ModifierKind.SLASH_DISTANCE_IGNORE):
            if not modifier.matches(self, query):
                continue
            value = modifier.value
            if callable(value):
                if value(self, query):
                    return True
            elif value:
                return True
        return False

    def slash_forbidden(self, player, card=None):
        """这名角色现在是否被禁止使用【杀】（天义没赢的后果）。"""

        if not hasattr(self, "modifiers"):
            return False
        for modifier in self.modifiers.sorted_for(ModifierKind.SLASH_FORBIDDEN):
            if modifier.owner is not player:
                continue
            if not modifier.matches(self, {"player": player, "card": card}):
                continue
            value = modifier.value
            if callable(value):
                if value(self, {"player": player, "card": card}):
                    return True
            elif int(value or 0):
                return True
        return False

    def response_required_count(self, source, target, card=None):
        """被要求响应时，目标需要交出几张响应牌。

        基础是 1；【无双】这类技能通过 RESPONSE_COUNT modifier 提高它。
        调用方（杀的结算 / 决斗）只需按这个数字循环要牌，不认具体技能。
        """

        if not hasattr(self, "modifiers"):
            return 1
        bonus = self.modifiers.total(
            ModifierKind.RESPONSE_COUNT, source=source, target=target, card=card)
        return max(1, 1 + int(bonus))

    def cannot_respond_to(self, card, target=None):
        """这张牌对 ``target`` 而言是否已被技能判定成"不可被响应"。

        技能在结算完自己的判定后把结论写在**实体牌**上：
        整张牌级别的写 ``_cannot_respond``（铁骑），
        只针对个别目标的写 ``_cannot_respond_targets``（烈弓）。这里只读标记，
        不认具体技能，也不改变普通杀的行为。
        """

        if card is None:
            return False
        marked = getattr(card, "_cannot_respond_targets", None)
        if marked:
            if target is None:
                return True
            if id(target) in marked:
                return True
        return bool(getattr(card, "_cannot_respond", False))

    def lose_hp(self, target, amount, source=None, cause=None):
        """失去体力（不是伤害）：只减体力，不产生伤害事件。

        【苦肉】一类"失去体力"与"受到伤害"在规则上不同：不触发受伤类技能、
        不走防具与伤害加成；但**体力降到 0 依然会进入濒死**，这一点和伤害
        完全一致。返回本次触发的 DyingFlow（没有进入濒死时返回 None），
        调用方可以据此把自己的后续结算挂到濒死之后。
        """

        if target is None or int(amount) <= 0:
            return None
        if not getattr(target, "alive", False) or getattr(target, "hp", 0) <= 0:
            return None

        from .atoms_v2 import LoseHpAtom

        self.engine.context.apply(LoseHpAtom(target, int(amount)))
        if target.hp > 0:
            return None

        from src.game.flows.dying import DyingFlow

        dying = DyingFlow(self.engine, dying_player=target, source=source, cause=cause)
        dying.start()
        return dying

    def damage_dealt_bonus(self, source, target=None, card=None):
        """source 造成伤害时的加成（裸衣一类）。"""

        if not hasattr(self, "modifiers"):
            return 0
        return int(self.modifiers.total(
            ModifierKind.DAMAGE_DEALT, source=source, target=target, card=card))

    def target_forbidden(self, target, source=None, card=None):
        """该角色是否因为技能而不能成为这张牌的目标（空城 / 谦逊）。"""

        if not hasattr(self, "modifiers"):
            return False
        return self.modifiers.total(
            ModifierKind.TARGET_FORBIDDEN,
            source=source, target=target, card=card) > 0

    def get_alive_players(self):
        return self.seats.alive_players_in_order()

    def get_alive_players_in_seat_order(self, start_after=None):
        return self.seats.alive_players_in_order(start_after=start_after)

    def get_next_alive_player(self, player_or_id):
        player = self.get_player(player_or_id) if isinstance(player_or_id, str) else player_or_id
        return self.seats.next_alive_player(player)

    # ==================================================
    # 额外回合（放权 / 连破）
    # ==================================================

    def queue_extra_turn(self, player):
        """给一名角色排一个额外回合；由 ``TurnMixin.start_next_turn`` 消费。"""

        if player is None or not getattr(player, "alive", True):
            return False
        if not isinstance(getattr(self, "extra_turns", None), list):
            self.extra_turns = []
        self.extra_turns.append(player)
        self.add_log("%s 获得一个额外回合" % player.name)
        return True

    def pop_extra_turn(self):
        """取出下一个该执行额外回合的角色；队列空了返回 None。"""

        queue = getattr(self, "extra_turns", None) or []
        while queue:
            player = queue.pop(0)
            if getattr(player, "alive", True) and getattr(player, "hp", 0) > 0:
                return player
        return None

    def add_log(self, text):
        self.game_log.append(str(text))
        del self.game_log[:-8]

    @property
    def current_player_id(self):
        return self.current_turn_player.player_id if self.current_turn_player else None

    @property
    def current_seat(self):
        return self.current_turn_player.seat if self.current_turn_player else None

    def start_single_player(self):

        self.start_local_battle(self.ai_count)

    # ==================================================
    # 游戏模式
    # ==================================================

    def set_mode(self, mode_id):
        """切换模式；人数会被夹到该模式允许的范围。"""

        mode_cls = self.modes.get(mode_id)
        if mode_cls is None:
            return False
        self.mode_id = mode_id
        self.mode = mode_cls(self)
        self.clamp_player_count()
        return True

    def allowed_player_counts(self):
        counts = self.mode.allowed_counts()
        return tuple(counts) if counts else tuple(range(2, 9))

    def total_players(self):
        return self.ai_count + 1

    def clamp_player_count(self):
        """把总人数夹到当前模式允许的范围内。"""

        allowed = self.allowed_player_counts()
        current = self.total_players()
        if current in allowed:
            return current
        target = min(allowed, key=lambda count: (abs(count - current), count))
        self.ai_count = max(1, min(7, target - 1))
        return target

    def adjust_player_count(self, delta):
        """菜单里的 ＋ / −：在模式允许的人数列表上移动一格。"""

        allowed = list(self.allowed_player_counts())
        current = self.total_players()
        if current not in allowed:
            target = min(allowed, key=lambda count: (abs(count - current), count))
        else:
            index = allowed.index(current)
            index = max(0, min(len(allowed) - 1, index + delta))
            target = allowed[index]
        self.ai_count = max(1, min(7, target - 1))
        return self.ai_count

    def can_adjust_player_count(self, delta):
        allowed = list(self.allowed_player_counts())
        current = self.total_players()
        if current not in allowed:
            return True
        index = allowed.index(current)
        return 0 <= index + delta < len(allowed)

    # ==================================================
    # 选将流程
    # ==================================================

    # ==================================================
    # 武将可用性：**唯一**的判断入口
    #
    # "界面可浏览"与"对局可使用"是两件事：
    #   * 注册表（generals.list_generals）永远列出全部条目，界面照着它显示，
    #     玩家能读技能说明、能看到"为什么不能选"；
    #   * 对局可用的池子只包含"已实现 + 当前模式允许 + 允许随机抽取"的条目。
    # 本地候选、随机按钮、AI 分配、身份局、联机候选池与权威端选将校验
    # 全部走下面这几个方法，任何调用方都不许自己判断 implemented。
    # ==================================================

    def general_available(self, general_id, *, for_random=False):
        """(能不能在本模式里开局, 原因)；`for_random` 时额外要求能进随机池。"""

        general = self.generals.get(general_id)
        if general is None:
            return False, "武将不存在：" + str(general_id)
        if for_random:
            return general.random_eligible_for(self.mode_id), (
                "" if general.random_eligible_for(self.mode_id)
                else "该武将不允许随机分配。")
        return general.availability_for(self.mode_id)

    def playable_generals(self, *, for_random=False):
        """当前模式**对局可用**的武将定义（按注册顺序，稳定）。"""

        return tuple(
            general for general in self.generals.list_generals()
            if self.general_available(
                general.id, for_random=for_random)[0]
        )

    def playable_general_ids(self, *, for_random=False):
        return tuple(general.id for general in self.playable_generals(for_random=for_random))

    def general_pool_ids(self):
        """当前模式可用的武将池（按注册顺序，稳定）。

        显式指定的 ``general_pool`` 同样要过滤：它是选将 / 联机传进来的，
        里面可能夹着"这条不该在这一局出现"的条目（未实现、只允许在测试模式
        显式选择的形态）。过滤放在这里，随机抽取与 AI 分配就自动安全。
        """

        pool = [general_id for general_id in (self.general_pool or ()) if general_id]
        if not pool:
            return self.playable_general_ids(for_random=True)
        return tuple(
            general_id for general_id in pool
            if self.general_available(general_id, for_random=True)[0]
        )

    # ---- 「我的武将池」（本机偏好）----

    def set_favorite_generals(self, general_ids):
        """设置「我的武将池」（只影响本机真人的候选，见候选池的说明）。"""

        cleaned = []
        for value in general_ids or ():
            text = str(value or "").strip()
            if text and text not in cleaned:
                cleaned.append(text)
        self.favorite_general_ids = tuple(cleaned)
        return self.favorite_general_ids

    def has_favorite_generals(self):
        return bool(self.favorite_general_ids)

    def candidate_pool_ids(self):
        """本机真人的候选池：偏好优先，不足 5 人时退回本模式全部可用武将。

        **AI 不走这里**：``general_pool_ids()`` 才是全场共用的池子，
        ``assign_generals`` 与随机分配读的都是它。玩家喜欢赵云，不代表
        整桌 AI 也只能用玩家喜欢的武将。
        """

        available = tuple(self.general_pool_ids())
        pool, used_favorites = resolve_pool(
            self.favorite_general_ids, available, registry=self.generals)
        self.draft_used_favorites = used_favorites
        return pool

    def roll_general_candidates(self, count=None):
        """为真人抽取本局候选武将（一次生成，之后由 ``general_draft`` 持有）。

        随机只发生在这里：重绘 / resize / 重连都不会再抽一次——候选是业务
        状态，不是每帧算的表现。
        """

        pool = list(self.candidate_pool_ids())
        if not pool:
            self.general_draft.reset()
            return ()
        size = int(count if count is not None
                   else getattr(self.mode, "general_choice_count", DRAFT_SIZE)
                   or DRAFT_SIZE)
        self.general_draft.size = max(1, size)
        return self.general_draft.roll(
            pool, self.rng, used_favorites=self.draft_used_favorites)

    def selectable_generals(self):
        """选将界面要显示的武将：优先用候选池，没有则退回全部。"""

        candidates = tuple(self.general_candidates)
        if not candidates:
            # 没有候选池时（旧路径）退回"本模式对局可用"的全部武将，
            # 而不是注册表里的全部条目——否则选将界面会给出未实现的武将。
            return self.playable_generals(for_random=True)
        order = {general_id: index for index, general_id in enumerate(candidates)}
        playable = [
            general for general in self.playable_generals(for_random=True)
            if general.id in order
        ]
        playable.sort(key=lambda general: order[general.id])
        return tuple(playable)

    def begin_general_select(self):
        """进入开局流程：先分配身份（身份模式），再进入选将。

        模式可以自己决定开局场景（``open_setup``）：1v1 测试模式直接进
        设置页（双方武将由玩家指定，不走随机三选一），其余模式保持原样。
        """

        self.reset()
        self.selected_general = None
        self.general_candidates = ()
        self.pending_identities = ()
        self.menu_message = ""
        custom_scene = self.mode.open_setup()
        if custom_scene:
            self.scene = custom_scene
            return self.scene
        if self.mode.uses_identities:
            self.pending_identities = self.mode.roll_identities()
            self.mode.apply_identities(self.pending_identities)
            self.scene = "identity_reveal"
        else:
            self.general_candidates = self.roll_general_candidates()
            self.scene = "general_select"
        return self.scene

    def confirm_identity(self):
        """身份展示界面点「继续」：进入选将。"""

        if self.scene != "identity_reveal":
            return False
        self.general_candidates = self.roll_general_candidates()
        self.scene = "general_select"
        return True

    def confirm_general(self, general_id=None):
        """确认武将并开始对局；AI 从同一批武将里不重复分配。"""

        general_id = general_id or self.selected_general
        if general_id is None:
            return False
        if general_id not in self.generals:
            raise KeyError("unknown general: " + str(general_id))
        if not self.general_pool:
            self.general_pool = self.playable_general_ids(for_random=True)
        self.selected_general = general_id
        self.start_local_battle(self.ai_count)
        return True

    # ==================================================
    # 开局规则：单机与联机**共用这一份实现**
    #
    # 身份配比、武将绑定、主公体力上限、先手顺序只在这里写一次。单机与联机的
    # 差别只有"谁控制这个座位"（本地真人 / 远程真人 / AI）；任何一边单独再写
    # 一遍开局，另一边的规则迟早会漏（Phase 11.4.3 之前正是如此）。
    # ==================================================

    def _apply_opening_rules(self, *, identities=None, general_pool=None):
        """身份 → 武将 → 模式开局修正 → 首行动角色；返回首行动角色。"""

        if general_pool is not None:
            self.general_pool = tuple(item for item in general_pool if item)

        if self.mode.uses_identities:
            cards = tuple(
                self.pending_identities if identities is None else identities)
            if cards:
                self.mode.apply_identities(cards)

        # 已经有武将的角色不重抽（reset() 里可能刚分配过），只补齐缺的人。
        # 真人自己选的那一个优先：联机的选将结果存在 general_picks 里，
        # 单机的选将结果存在 selected_general 里，两条路都走同一份分配。
        pool = [item for item in (self.general_pool or ()) if item]
        if pool and any(not player.general_id for player in self.players):
            picks = dict(getattr(self, "general_picks", None) or {})
            self.assign_generals(
                mapping=picks or None,
                human_general=(picks.get(self.player.player_id)
                               or self.selected_general))

        self.mode.setup_battle()
        return self.mode.first_player()

    def start_networked_battle(self, seats, *, first_player=None, general_pool=None,
                               general_picks=None):
        """按座位表建立**房主侧的权威对局**（一步到位版）。

        联机的正式开局走两步（``begin_networked_setup`` + ``finish_networked_setup``），
        因为中间要插"看身份 → 选将"这一段与单机一致的开局流程；这个一步到位的
        入口留给工具与单元测试（它们不需要选将界面）。

        规则与单机**完全同一套**：身份、武将、主公体力上限、先手顺序一步都不少。
        """

        self.begin_networked_setup(
            seats, general_pool=general_pool, general_picks=general_picks)
        return self.finish_networked_setup(first_player=first_player)

    def begin_networked_setup(self, seats, *, general_pool=None, general_picks=None):
        """联机开局第一步：建座位 + 发身份，**不发牌、不开局**。

        ``seats`` 是 ``[(player_id, 名字, seat, controller_type, connection_id)]``：
        房主自己是本地真人，其他电脑是 ``REMOTE_HUMAN``，AI 补位是 ``AI``。

        做完这一步，每个玩家都知道自己的身份（主公公开），但牌堆、武将、
        体力与回合都还没动——正是单机"身份展示 → 选将"之间那个状态。

        ``general_pool`` 留空表示"这一局不指定武将"（工具与单元测试使用的旧
        语义）；真实联机开局由网络桥注入真实武将池（见 ``HostMatch``）。
        """

        self.reset()

        players = []
        for player_id, name, seat, controller_type, connection_id in seats:
            player = Player(
                name,
                gender="male",
                player_id=player_id,
                seat=int(seat),
                controller_type=controller_type,
            )
            player.connection_id = connection_id
            players.append(player)

        self.players = sorted(players, key=lambda player: player.seat)
        self.player = next(
            (player for player in self.players if player.is_human), self.players[0])
        self.enemy = next(
            (player for player in self.players if player is not self.player),
            self.players[0])
        self.seats = SeatManager(self)
        self.controllers.clear()

        # reset() 把技能绑在**旧名单**的角色上：换名单必须先卸干净，否则事件
        # 总线上会同时挂着两批 listener，旧角色早就不在这一局里了。
        self.skills.clear()
        self.modifiers.clear()
        self.conversions.clear()

        # 身份：按座次发放（主公公开）。选将结果由调用方随后给（真人选的那一个）。
        self.selected_general = None
        self.general_candidates = ()
        self.general_picks = dict(general_picks or {})
        self.pending_identities = ()
        self.general_pool = tuple(item for item in (general_pool or ()) if item)
        self._pending_identities = (
            self.mode.roll_identities() if self.mode.uses_identities else ())
        if self._pending_identities:
            self.mode.apply_identities(self._pending_identities)

        # 开局流程的起点与单机一致：先看身份，再选将。
        self.scene = "identity_reveal" if self.mode.uses_identities else "game"
        return self.players

    def finish_networked_setup(self, *, first_player=None, general_picks=None):
        """联机开局第二步：选将结束 → 发牌 → 开局规则 → 首行动。"""

        if general_picks:
            self.general_picks.update(dict(general_picks))

        # 发牌：reset() 是按旧名单发的，这里是新名单的第一次发牌。
        self.deck.reset()
        for player in self.players:
            player.skill_state = SkillState()
            player.draw_cards(self.deck, 4)
        self.deal_presentation = (self.player, list(self.player.hand))

        identities = tuple(getattr(self, "_pending_identities", ()) or ())
        first = self._apply_opening_rules(
            identities=identities, general_pool=self.general_pool)
        self.scene = "game"
        self.start_turn(first_player or first)
        return self.players

    def start_local_battle(self, ai_count):

        self.ai_count = max(1, min(7, int(ai_count)))

        self.reset()
        # 开局规则：先写回身份（身份模式），再应用模式的开局修正
        # （主公体力上限等），最后从模式给出的首行动角色开始。
        first = self._apply_opening_rules()

        self.scene = "game"
        self.start_turn(first)

    def restart_setup(self):
        """「重新开始」：彻底清理本局状态并回到开局流程。"""

        self.pending_identities = ()
        self.selected_general = None
        self.general_candidates = ()
        self.return_to_menu()

    def restart_battle(self):
        """「重新开始」的通用入口：模式可以先自己重开这一局。

        1v1 测试模式用它实现"原配置重开"（**不动**双方武将 / 先手 / 操作方式，
        也不回到菜单），其余模式继续走原来的"回到开局流程"。
        """

        if self.mode.restart_battle(self):
            return True
        self.restart_setup()
        return False

    def return_to_menu(self):
        # 回到菜单意味着重新配置：先清掉上一局的武将池与选将结果，
        # 再 reset，否则 reset 里的自动分配会把旧武将带回新一局。
        # 指定武将的两条通道（general_assignments / general_picks）也一起清：
        # 它们**不会**被 reset 清掉，留着就会让 1v1 测试指定的武将被带进
        # 自由混战 / 身份局，或者反过来把别人的武将塞进测试局。
        self.general_pool = ()
        self.selected_general = None
        self.general_candidates = ()
        self.pending_identities = ()
        self.general_assignments = {}
        self.general_picks = {}
        self.reset()
        self.scene = "menu"
        self.menu_message = "请选择游戏模式与人数"

    def open_multiplayer_menu(self):
        """进入「多人对战」流程（局域网菜单 / 大厅由 UI 层的 LanScene 接管）。

        本局状态先清零：联机与单机共用同一个 Game 实例，带着上一局的角色
        进房间只会在后面同步阶段制造麻烦。
        """

        self.reset()
        self.menu_message = ""
        self.scene = "multiplayer_menu"
        return self.scene


    # ==================================================
    # 是否正在播放动画
    # ==================================================

    @property
    def busy(self):

        return self.actions.busy


    # ==================================================
    # 每一帧更新
    # ==================================================

    STALL_GUARD_FRAMES = 40

    # 节奏档位：越小越慢。所有动画与停顿都按这个倍率缩放。
    # 默认取 0.75（比原先的 1.0 慢一档）：真人不用盯战报也能跟上出牌与结算。
    SPEED_STEPS = (0.4, 0.55, 0.75, 1.0, 1.5, 2.0)
    DEFAULT_SPEED = 0.75

    def update(
        self,
        dt
    ):

        self.actions.update(dt * self.speed)
        self.prune_stale_table_cards()
        # 模式每帧收尾：1v1 测试的**双边手动**模式在这里补问"还没被问到的
        # 那一方"并把视角切给正在决策的人。其它模式是空实现。
        self.mode.on_frame(self)
        self._guard_stalled_turn()
        # 帧边界上的牌唯一归属检查（默认关闭）。遗留的非原子路径
        # （queue_draw_cards / queue_weapon_discards 一类）只在这里被覆盖。
        # 有等待中的请求不算问题：检查只比较牌区容器，与 Pending 无关。
        # 判定统一走 invariants.armed_for（对局显式设置 > 模块开关 > 环境变量）。
        if armed_for(self):
            assert_card_ownership(self)

    # ==================================================
    # 桌面主体卡的兜底清理
    # ==================================================

    def prune_stale_table_cards(self):
        """清掉"这次结算早已结束、却还挂在桌面中央"的牌。

        桌面主体卡（Action Display）由收尾动作负责移除：出牌动画的第二段
        （中央 → 弃牌堆）播完时调 ``remove_table_card``。只要那一段没跑到
        ——流程瞬间结算（例如【铁索连环】重铸）、动画队列被清空、流程被取消
        ——这张牌就会**永远留在桌面中央**。玩家看到的就是"这张牌怎么在上面"。

        判据沿用登记时那条规则（见 ``GameEngine.note_table_card``）：这张牌
        本身、或它的一张实体来源牌仍在处理区 = 这次结算还没结束 = 该显示。
        只在**队列空闲且引擎不在等输入**时清理，所以正在播的动画与正在等的
        响应/判定窗口都不受影响。
        """

        if not self.table_cards:
            return 0
        if self.actions.busy or self.engine.pending.active:
            return 0
        zone = self.processing_zone
        kept = []
        for entry in self.table_cards:
            card = entry[0]
            materials = list(getattr(card, "source_cards", ()) or ()) or [card]
            # "这张牌本身、或它的一张实体来源牌仍在处理区"——判据与登记时
            # （``GameEngine.place_table_card``）逐字一致。
            if any(
                any(material is zoned for zoned in zone)
                for material in materials
            ):
                kept.append(entry)
        removed = len(self.table_cards) - len(kept)
        if removed:
            self.table_cards[:] = kept
        return removed

    # ==================================================
    # 速度（节奏）
    # ==================================================

    def set_speed(self, value):
        """设置节奏倍率，返回实际生效的值。"""

        lowest, highest = self.SPEED_STEPS[0], self.SPEED_STEPS[-1]
        self.speed = max(lowest, min(highest, float(value)))
        return self.speed

    def slower(self):
        index = self.speed_index()
        if index > 0:
            self.speed = self.SPEED_STEPS[index - 1]
        return self.speed

    def faster(self):
        index = self.speed_index()
        if index < len(self.SPEED_STEPS) - 1:
            self.speed = self.SPEED_STEPS[index + 1]
        return self.speed

    def speed_index(self):
        best = 0
        for index, step in enumerate(self.SPEED_STEPS):
            if abs(step - self.speed) < abs(self.SPEED_STEPS[best] - self.speed):
                best = index
        return best

    @property
    def speed_label(self):
        if float(self.speed).is_integer():
            return str(int(self.speed)) + "×"
        return ("%.2f" % self.speed).rstrip("0").rstrip(".") + "×"

    def _guard_stalled_turn(self):
        """兜底：AI 回合停在没有后续动作的状态时把它接回去。

        正常的 AI 回合由 ActionQueue 的连续回调推进。如果某条异常路径
        （例如暂停中的子流程提前结束）把动作链断开，对局会停在这一回合
        不再前进。这里检测「AI 回合 + play/discard + 队列空 + 无任何等待
        输入」持续若干帧，就结束该回合并交给下一个存活角色。
        真人回合与任何等待中的 Pending 都不会被这个守卫影响。
        """

        if self.game_over or self.scene != "game":
            self._stall_frames = 0
            return

        waiting = (
            self.busy
            or self.engine.pending.active
            or self.pending_selection is not None
            or self.pending_target_selection is not None
            or self.pending_view_as is not None
            or self.response.active
            or self.choice.active
            # 远程真人的出牌阶段没有本地交互状态：等待期间绝不能触发
            # "AI 回合卡死"兜底，否则会把别人的回合直接结束掉。
            or self.waiting_for_remote
        )
        current = self.current_turn_player
        stalled = (
            not waiting
            and current is not None
            and not current.is_human
            and current.alive
            and self.phase in ("play", "discard")
        )
        if not stalled:
            self._stall_frames = 0
            return

        self._stall_frames = getattr(self, "_stall_frames", 0) + 1
        if self._stall_frames < self.STALL_GUARD_FRAMES:
            return

        self._stall_frames = 0
        self.add_log("（回合守卫）继续 " + current.name + " 的回合")
        self._finish_ai_turn(current)


    def submit_action(self, action):

        return self.engine.submit(action)


    @property
    def pending_request(self):

        return self.engine.pending.current


    def local_can_play(self):
        """本机玩家现在能不能主动出牌 / 结束回合。

        "轮到我 + 出牌阶段 + 没在播动画"并不够：等待其他玩家响应、等待结算
        （共享无懈阶段就是典型）、锦囊还在结算中时，出牌与结束回合都必须关掉。
        联网客户端的只读视图另有实现（见 ``ui.view_adapter.RemoteGameView``），
        它还要多一条"手里真的有一条未回答的出牌阶段请求"。
        """

        if self.game_over or self.busy:
            return False
        # 判定优先：判定没走完（规则上没走完，或者判定牌还在屏幕中央演着）
        # 都轮不到出牌。见 src/game/judge_gate.py。
        if not self.judge_gate.allows_local_input(self.player):
            return False
        if self.phase != "play" or self.current_turn_player is not self.player:
            return False
        if self.engine.pending.active or self.response.active or self.choice.active:
            return False
        return True


    # ==================================================
    # 重置整个游戏
    # ==================================================

    def reset(self):

        # AI 随机源每局重置：模式可以在 before_reset 里把它的流指向全局 rng
        # （1v1 测试的固定种子），离开那个模式后自动回到"每个 AI 一个流"。
        self.ai_rng = None
        self.mode.before_reset()

        self.actions.clear()

        self.response.clear()

        self.choice.clear()

        self.engine.reset()

        # 技能必须先卸载：否则重建角色后旧 listener 仍挂在事件总线上。
        self.skills.clear()
        self.modifiers.clear()
        self.conversions.clear()

        self._create_players()
        # 模式可以在建好角色后改名 / 改控制器类型（1v1 测试：我方与对手）。
        self.mode.prepare_players(self.players)
        self.seats = SeatManager(self)
        self.controllers.clear()

        self.pending_selection = None
        self.pending_target_selection = None
        self.pending_skill_picker = None
        self.pending_skill_input = None
        self.pending_card_action = None
        self.pending_view_as = None

        # 本局候选（GeneralDraft）属于"一局一份"：重开必须清空，否则上一局的
        # 5 个候选会跟到新一局（那正是"每局重新随机"的反面）。
        self.general_draft.reset()
        self.general_candidates = ()
        self.table_cards.clear()
        self.judge_card = None
        self.processing_zone.clear()
        self.public_card_pool.clear()
        self.revealed_card = None
        self.current_turn_player = self.player
        self.turn_phase = None
        self.skipped_phases.clear()
        self.active_turn_flow = None
        # 额外回合队列（放权 / 连破）：属于"这一局"，重开时必须清空，
        # 否则上一局排进来的额外回合会跑到新一局里。
        self.extra_turns = []

        self.deck.reset()

        for player in self.players:
            player.reset()

        self.phase = "play"

        self.game_over = False
        self.result = None
        self.winner = None
        self.game_log = []
        self._stall_frames = 0
        if not hasattr(self, "speed"):
            self.speed = self.DEFAULT_SPEED

        # ==================================================
        # 双方初始四张
        # ==================================================

        for player in self.players:
            player.draw_cards(self.deck, 4)

        # 表现层提示：开局发牌已经完成（实体牌就在这里），UI 可以据此播放
        # "逐张飞到手牌"的动画。动画不抽牌、不重复、不改变任何规则数据。
        self.deal_presentation = (self.player, list(self.player.hand))

        self.assign_generals(human_general=self.selected_general)

        self.message = (
            "你的回合。"
        )
        # 战报按**真正在跑的模式**报名字：身份局里写"本地自由混战开始"会让人
        # 以为进错了模式（联机身份局尤其明显）。
        self.add_log(str(getattr(self.mode, "name", "") or "对局") + "开始："
                     + str(len(self.players)) + " 人")


    # ==================================================
    # 将牌固定显示在桌面
    # ==================================================

    def add_table_card(
        self,
        card,
        rect="table_card"
    ):
        """把一张牌固定显示在桌面中央（Action Display）。

        ``rect`` 是命名落位（"table_card" / "response_card"）或显式屏幕坐标；
        命名落位由 UI 每帧按当前分辨率解析，所以 resize / F11 后主体卡跟着走。

        同一张牌只保留一条记录：重复登记只更新落位，不会在桌面出现两份。
        """

        entry = (
            card,
            rect if isinstance(rect, str) else tuple(rect),
        )

        for index, item in enumerate(self.table_cards):

            if item[0] is card:

                self.table_cards[index] = entry

                return

        self.table_cards.append(entry)


    # ==================================================
    # 从桌面移除一张牌
    # ==================================================

    def remove_table_card(
        self,
        card
    ):

        for i, item in enumerate(
            self.table_cards
        ):

            if item[0] is card:

                self.table_cards.pop(i)

                return


    # ==================================================
    # 弃牌前清理本次使用的临时状态
    # ==================================================

    def discard_card_with_cleanup(
        self,
        card
    ):

        # ==================================================
        # 例如：
        #
        # 普通杀通过朱雀羽扇
        # 临时变成火杀。
        #
        # 进入弃牌堆时恢复成原来的普通杀，
        # 防止以后洗回牌堆时永久变成火杀。
        # ==================================================

        if hasattr(
            card,
            "_original_nature"
        ):

            card.nature = getattr(
                card,
                "_original_nature"
            )

            delattr(
                card,
                "_original_nature"
            )

        # 丈八蛇矛产生的虚拟杀不是实体牌，
        # 两张素材牌已分别进入弃牌堆。
        if not getattr(
            card,
            "_virtual",
            False
        ):

            self.deck.discard(
                card
            )


    # ==================================================
    # 一张牌进入弃牌堆
    # ==================================================

    def queue_to_discard(
        self,
        card,
        start_rect,
        after=None
    ):

        # ==================================================
        # 先取消桌面固定显示
        # ==================================================

        self.actions.add(

            CallbackAction(

                lambda c=card:
                    self.remove_table_card(c)
            )
        )

        # ==================================================
        # 飞往弃牌堆
        # ==================================================

        self.actions.add(

            MoveCardAction(
                card,
                start_rect,
                DISCARD_PILE_RECT,
                duration=0.30,

                on_finish=(

                    lambda c=card:
                        self.discard_card_with_cleanup(
                            c
                        )
                )
            )
        )

        # ==================================================
        # 后续动作
        # ==================================================

        if after is not None:

            self.actions.add(
                CallbackAction(
                    after
                )
            )


    # ==================================================
    # 动画摸牌
    # ==================================================

    def queue_draw_cards(
        self,
        player,
        number,
        target_rect,
        after=None
    ):

        # 别人的摸牌**不播飞行动画**：牌背在深色桌面上就是一个个浅色方块飞过，
        # 既看不出信息又很吵。直接进手牌，界面上的变化是"手牌 ×N"跳动。
        # 本机真人自己的摸牌保留动画（那是自己的牌，看得见）。
        face_down = player is not self.player

        for _ in range(number):

            card = self.deck.draw()

            if card is None:
                break

            if face_down:
                player.hand.append(card)
                continue

            self.actions.add(

                MoveCardAction(
                    card,
                    DRAW_PILE_RECT,
                    target_rect,
                    duration=0.28,
                    face_down=False,

                    on_finish=(

                        lambda c=card, p=player:
                            p.hand.append(c)
                    )
                )
            )

            self.actions.add(
                WaitAction(
                    0.10
                )
            )

        if after is not None:

            self.actions.add(
                CallbackAction(
                    after
                )
            )
