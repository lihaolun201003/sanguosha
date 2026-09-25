"""1v1 测试模式：开战前自由指定双方武将，用来快速验证任意武将组合。

它与自由混战 / 身份局**共用同一套规则引擎**（牌堆、技能、装备、结算、控制器
全部复用），差别只有下面这几件模式业务：

* 固定两人，不套用身份局的胜负与主公规则，也没有官方竞技 1v1 的选将、
  替补与专属技能改写——主公技照旧不激活，并在设置页明确写出来；
* 双方武将**由玩家在设置页指定**，不走"随机三选一 + AI 自动分配"；
* 先手可配置（我方 / 对方 / 随机），随机种子可固定；
* 双边手动测试时，视角（``game.player``）跟着"当前正在决策的人"切换，
  两名角色都由本机鼠标操作；
* 胜负 = 最后存活者（中立的"我方 / 对手"文案，不写"你赢了"）。

**重开不留残留**：本模式不订阅任何事件，武将 / 技能 / 标记 / 限定状态全部由
``Game.reset()`` → ``skills.clear()`` 与重建 ``Player`` 清干净，见
``before_reset`` 里对"上一局遗留配置"的清理。
"""

from dataclasses import dataclass

from src.player import ControllerType

from ..engine.state import GameOutcome, GameResult
from .base import DeathResolution, GameMode

#: 座位约定：0 = 我方，1 = 对手（与 ``Game._create_players`` 的顺序一致）。
MY_SEAT = 0
ENEMY_SEAT = 1

SIDE_LABELS = ("我方", "对手")

FIRST_CHOICES = ("me", "enemy", "random")
FIRST_LABELS = (("me", "我方先手"), ("enemy", "对方先手"), ("random", "随机先手"))
CONTROL_CHOICES = ("ai", "manual")
CONTROL_LABELS = (("ai", "玩家对AI"), ("manual", "双边手动测试"))


@dataclass
class DuelConfig:
    """开战前设置。

    它挂在 ``Game`` 上（而不是模式实例上）：模式实例每次 ``set_mode`` 都会重建，
    而"上次双方武将、操作方式与先手"必须在换将、重开、回主菜单之后仍然记得。
    """

    my_general: str = ""
    enemy_general: str = ""
    #: me / enemy / random
    first: str = "me"
    #: ai（我方由玩家、对手由 AI） / manual（两边都由玩家操作）
    control: str = "ai"
    #: 随机种子文本；空 = 每次随机
    seed: str = ""

    @property
    def manual(self):
        return str(self.control) == "manual"

    @property
    def seed_text(self):
        return str(self.seed or "").strip()

    def copy(self):
        return DuelConfig(
            my_general=self.my_general,
            enemy_general=self.enemy_general,
            first=self.first,
            control=self.control,
            seed=self.seed,
        )

    def general_id_of(self, seat):
        return self.my_general if int(seat) == MY_SEAT else self.enemy_general

    def set_general(self, seat, general_id):
        if int(seat) == MY_SEAT:
            self.my_general = str(general_id or "")
        else:
            self.enemy_general = str(general_id or "")
        return self.general_id_of(seat)

    def swap(self):
        self.my_general, self.enemy_general = self.enemy_general, self.my_general
        return self


def config_of(game):
    """这台 ``Game`` 的 1v1 测试配置；第一次访问时创建。"""

    config = getattr(game, "duel_config", None)
    if not isinstance(config, DuelConfig):
        config = DuelConfig()
        game.duel_config = config
    return config


class DuelTestMode(GameMode):
    id = "duel_test"
    name = "1v1 测试"
    description = "两人对战，开战前自由指定双方武将，便于快速复测。"
    allowed_player_counts = (2,)
    uses_identities = False
    # 候选数量不参与：设置页直接从完整武将注册表里选，不受三选一限制。
    general_choice_count = 0
    #: 「开始游戏」进入的场景（设置页）。
    SCENE_SETUP = "duel_setup"

    def __init__(self, game):
        super().__init__(game)
        self._started = False

    # ==================================================
    # 配置
    # ==================================================

    @property
    def config(self):
        return config_of(self.game)

    @property
    def tracks_operator(self):
        """只有双边手动测试才切换视角；玩家对 AI 时视角永远在我方。"""

        return bool(self.config.manual)

    def player_at(self, seat):
        for player in self.game.players:
            if int(getattr(player, "seat", -1)) == int(seat):
                return player
        return None

    def my_player(self):
        return self.player_at(MY_SEAT)

    def enemy_player(self):
        return self.player_at(ENEMY_SEAT)

    def side_label(self, player, *, with_general=True):
        """这名角色在界面上叫什么：「我方」「对手」＋武将名。"""

        seat = int(getattr(player, "seat", MY_SEAT))
        label = SIDE_LABELS[seat] if 0 <= seat < len(SIDE_LABELS) else str(getattr(player, "name", "?"))
        if not with_general:
            return label
        general = self.game.generals.get(getattr(player, "general_id", None))
        if general is None:
            return label
        return label + " · " + general.display_name

    def validate_setup(self):
        """开战前校验：返回 (ok, message)。非法配置一律不许开局。"""

        config = self.config
        for seat, general_id in ((MY_SEAT, config.my_general),
                                 (ENEMY_SEAT, config.enemy_general)):
            label = SIDE_LABELS[seat]
            if not general_id:
                return False, "还没有为「" + label + "」选择武将。"
            general = self.game.generals.get(general_id)
            if general is None:
                return False, "「" + label + "」指定的武将不存在：" + str(general_id)
            ok, reason = general.availability
            if not ok:
                return False, "「" + label + "」的" + general.display_name + "不可开局：" + reason
        return True, ""

    # ==================================================
    # 开局
    # ==================================================

    def open_setup(self):
        """「开始游戏」直接进入设置页：看清楚双方选谁之后才开局。"""

        return self.SCENE_SETUP

    def before_reset(self):
        """洗牌之前：清掉会干扰指定武将的遗留配置，并播种随机源。

        ``general_pool`` / ``general_candidates`` / ``selected_general`` /
        ``general_picks`` / ``general_assignments`` 属于"随机选将"那条路，
        留着它们会让 ``Game.reset()`` 里那次自动分配给双方塞上别人的武将。
        """

        game = self.game
        game.general_pool = ()
        game.general_candidates = ()
        game.selected_general = None
        game.general_picks = {}
        game.general_assignments = {}

        # 固定种子必须**同时**覆盖牌堆洗牌与 AI 决策：牌堆用 game.rng 洗，
        # AI 控制器也用同一个流（见 Game.create_controller 的 ai_rng）。
        game.ai_rng = game.rng
        game.rng.seed(self.config.seed_text or None)

    def prepare_players(self, players):
        """固定两人的身份与控制器：对手是 AI 还是"另一个我"。"""

        config = self.config
        order = sorted(players, key=lambda player: int(getattr(player, "seat", 0)))
        for seat, player in enumerate(order):
            if seat >= len(SIDE_LABELS):
                break
            player.name = SIDE_LABELS[seat]
            if seat == ENEMY_SEAT and not config.manual:
                player.controller_type = ControllerType.AI
            else:
                player.controller_type = ControllerType.HUMAN
        return None

    def setup_battle(self):
        """绑定双方武将；非法配置直接拒绝，绝不悄悄换成别的武将。"""

        ok, message = self.validate_setup()
        if not ok:
            raise ValueError("1v1 测试开局被拒绝：" + message)
        for seat in (MY_SEAT, ENEMY_SEAT):
            general_id = self.config.general_id_of(seat)
            self.game.set_general(self.player_at(seat), general_id)
        self._started = True
        return None

    def first_player(self):
        config = self.config
        if str(config.first) == "enemy":
            return self.enemy_player()
        if str(config.first) == "random":
            return self.game.rng.choice([self.my_player(), self.enemy_player()])
        return self.my_player()

    def restart_battle(self, game):
        """「原配置重开」：清干净重建一局，双方武将、先手与操作方式不变。"""

        game.start_local_battle(1)
        return True

    def start_battle(self):
        """校验设置后开局；返回 (ok, message)。非法配置一律不开局。"""

        ok, message = self.validate_setup()
        if not ok:
            return False, message
        self.game.start_local_battle(1)
        return True, ""

    # ==================================================
    # 每帧：视角切换 + 群体请求补问
    #
    # 双边手动测试只有一个鼠标，所以：
    # 1) 群体请求（共享无懈）改成**按座次逐个问**，而不是同时开两块面板；
    # 2) 谁在决策就把视角切给谁，手牌 / 按钮 / 提示跟着走。
    # ==================================================

    def on_frame(self, game):
        if not self.tracks_operator:
            return None
        request = game.engine.pending.current
        if request is not None and request.is_group and request.status == "pending":
            self.ask_next_group_member(game, request)
        game.sync_operator()
        return None

    def present_group(self, engine, request):
        game = engine.game
        self.ask_next_group_member(game, request)
        return None

    def ask_next_group_member(self, game, request):
        """把群体请求交给下一位还没回答的人；返回是否有人正在被问。"""

        if request is None or request.status != "pending":
            return False
        for member in request.group_members:
            if self._holds_ask(game.get_controller(member)):
                return True                     # 已经有人在看面板：等他
        for member in request.group_members:
            if request.member_status(member) != "pending":
                continue
            controller = game.get_controller(member)
            if controller is None:
                continue
            game.set_operator(member)
            controller.present(request)
            if request.status != "pending":
                return True                     # 本轮已被锁定 / 请求已结束
            if self._holds_ask(controller):
                return True                     # 等这个人的鼠标
        return False

    @staticmethod
    def _holds_ask(controller):
        """这个控制器现在手里有没有一块等人操作的面板 / 一个未回来的网络请求。"""

        if controller is None:
            return False
        holds = getattr(controller, "holds_panel", False)
        if callable(holds):
            holds = holds()
        return bool(holds) or bool(getattr(controller, "waiting", False))

    def operator_target(self, game):
        """界面现在该由谁操作（None = 不跟踪）。"""

        if not self.tracks_operator:
            return None
        request = game.engine.pending.current
        if request is not None and request.status == "pending":
            if request.is_group:
                for member in request.group_members:
                    if self._holds_ask(game.get_controller(member)):
                        return member
                pending = request.pending_members
                if pending:
                    return pending[0]
            elif request.target is not None:
                return request.target
        for system in (game.response, game.choice):
            current = system.current
            owner = getattr(current, "responder", None) if current is not None else None
            if owner is not None:
                return owner
        if game.game_over:
            # 结算界面：视角冻结在最后一位操作者身上，不再跳来跳去。
            return None
        return game.current_turn_player

    # ==================================================
    # 信息
    # ==================================================

    def general_note(self, general):
        """设置页要提前说明的事：本模式没有身份，主公技不会激活。"""

        if general is None:
            return ""
        lord_skills = []
        for skill_id in general.skill_ids:
            definition = self.game.skill_registry.get(skill_id)
            if definition is not None and definition.is_lord_skill:
                lord_skills.append(definition.name)
        if not lord_skills:
            return ""
        return ("主公技【" + "】【".join(lord_skills)
                + "】在 1v1 测试中不激活（本模式没有身份，也不套用主公规则）。")

    def overlay_result_texts(self, game):
        """中立的结算文案：双边手动模式下"你赢了"没有意义。

        色调用语义字符串（positive / negative / neutral）返回，由界面层映射到
        主题色——规则层不认识任何界面颜色。
        """

        winner = game.winner
        if winner is not None:
            return ("对 局 结 束", "获胜：" + self.side_label(winner), "positive")
        return ("对 局 结 束", "全场阵亡，无人获胜", "neutral")

    # ==================================================
    # 胜负：最后存活者
    # ==================================================

    def validate(self):
        ok, message = super().validate()
        if not ok:
            return ok, message
        return self.validate_setup()

    def resolve_death(self, flow):
        game = self.game
        dead_player = flow.dead_player
        already_over = game.game_over
        alive = [player for player in game.players if player.alive]

        if already_over:
            return DeathResolution(
                finished=True, outcome=None, winner=None,
                reason="ALREADY_OVER", message="",
            )

        if not alive:
            game.game_over = True
            game.phase = "over"
            game.message = "全场阵亡，无人获胜"
            game.result = GameResult(
                outcome=GameOutcome.NO_SURVIVOR, winner=None,
                loser=dead_player, reason="NO_SURVIVOR")
            game.winner = None
            return DeathResolution(
                finished=True, outcome=GameOutcome.NO_SURVIVOR,
                reason="NO_SURVIVOR", message=game.message)

        if len(alive) == 1:
            winner = alive[0]
            message = self.side_label(winner) + " 获胜"
            game.game_over = True
            game.phase = "over"
            game.message = message
            game.winner = winner
            game.result = GameResult(
                outcome=(GameOutcome.PLAYER_WIN
                         if winner is self.my_player() else GameOutcome.AI_WIN),
                winner=winner, loser=dead_player, reason="LAST_SURVIVOR")
            game.add_log(message)
            return DeathResolution(
                finished=True, outcome=game.result.outcome, winner=winner,
                reason="LAST_SURVIVOR", message=message,
                extra={"winner_side": int(getattr(winner, "seat", 0))})

        game.message = dead_player.name + " 阵亡"
        return DeathResolution(
            finished=False, reason="ELIMINATED", message=game.message)
