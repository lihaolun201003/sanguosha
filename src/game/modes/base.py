"""Game modes: who plays, how the battle starts, and how it ends.

A mode owns everything that differs between "本地自由混战" and "标准身份局":
allowed player counts, identity assignment, initial setup bonuses, the first
player, death rewards, and the victory condition.  Core flows only ever call
into the mode through this interface; they never branch on identities.
"""

from dataclasses import dataclass, field
from typing import Any, Tuple


@dataclass
class DeathResolution:
    """一次死亡结算后模式给出的结论。

    ``finished`` 为 False 表示对局继续（常见于身份模式里主公还在）。
    ``outcome`` 为 None 表示这次死亡不产生新的对局结果（例如对局早已结束）。
    """

    finished: bool = False
    outcome: Any = None
    winner: Any = None
    reason: str = ""
    message: str = ""
    extra: dict = field(default_factory=dict)


class GameMode:
    """Base mode: free-for-all semantics (last survivor wins).

    子类只需要覆写与自己的规则不同的钩子，其余保持这份默认行为，
    因此新增模式不会影响既有 FFA。
    """

    id = "base"
    name = ""
    description = ""
    # 空元组 = 2～8 人任意；否则只允许列出的总人数。
    allowed_player_counts: Tuple[int, ...] = ()
    uses_identities = False
    # 真人武将选择的候选数量（从可玩武将池中抽取）。
    general_choice_count = 3
    # True 表示本模式会让**多名角色都由本机鼠标操作**，因此界面要把视角
    # （``game.player``）跟着"当前正在决策的人"切换。默认 False：单机永远
    # 是本机真人一个操作者，视角不跟随。
    tracks_operator = False

    def __init__(self, game):
        self.game = game

    # ==================================================
    # 开局
    # ==================================================

    def allowed_counts(self):
        return self.allowed_player_counts

    def allows(self, player_count):
        counts = self.allowed_counts()
        if not counts:
            return 2 <= int(player_count) <= 8
        return int(player_count) in counts

    def prepare_setup(self):
        """分配身份等"选将之前"的准备工作（当前由 ``open_setup`` 取代）。"""

        return None

    def before_reset(self):
        """``Game.reset()`` 的第一步：模式可以在建角色 / 洗牌之前做准备。

        1v1 测试模式在这里播种随机源——**必须早于洗牌**，否则固定种子
        覆盖不到牌堆（洗牌用的是 ``Game.rng``）。
        """

        return None

    def prepare_players(self, players):
        """``Game.reset()`` 建好角色之后：模式可以改名、改控制器类型。

        默认什么都不做（FFA / 身份局的角色布局完全不变）。
        """

        return None

    def open_setup(self):
        """「开始游戏」时切换到哪个开局场景；None = 用通用选将流程。

        返回场景名字符串时，``Game`` 会直接切到那个场景，不再走
        "身份展示 → 候选选将"那套通用流程。
        """

        return None

    def setup_battle(self):
        """开局规则：体力与初始状态的模式修正。"""

        return None

    def first_player(self):
        """首行动角色；默认由调用方（真人）先手。"""

        return self.game.player

    def validate(self):
        """开局前校验人数等配置；返回 (ok, message)。"""

        if not self.allows(len(self.game.players)):
            return False, "当前模式不支持 " + str(len(self.game.players)) + " 人"
        return True, ""

    # ==================================================
    # 每一帧 / 决策归属（默认全部不参与）
    # ==================================================

    def on_frame(self, game):
        """每帧收尾：双边手动模式用它补问"还没被问到的人"并同步视角。"""

        return None

    def operator_target(self, game):
        """界面现在该由谁操作；None = 不跟踪（视角永远在本机真人身上）。"""

        return None

    def present_group(self, engine, request):
        """把一条群体请求（共享无懈阶段）交给有资格的人。

        默认实现：**同时**问所有还有资格的人。同步控制器（本地真人 / AI）可能
        当场就回答、甚至直接锁定本轮，所以每问一个人之后都要重新确认这条请求
        是否还在等待——谁被问、下一轮开不开，都由回答路径自己推进。

        只有双边手动测试（一台机器一个鼠标）需要覆写这一步，见 ``modes/duel.py``。
        """

        for member in request.group_members:
            if request.status != "pending":
                break
            if request.member_status(member) != "pending":
                continue
            controller = engine.game.get_controller(member)
            if controller is None:
                continue
            controller.present(request)
        return None

    # ==================================================
    # 信息
    # ==================================================

    def general_note(self, general):
        """这名武将在本模式下需要提前说明的事（无则空串）。

        例如 1v1 测试模式没有身份，主公技不会激活——这条说明由模式给出，
        界面只负责显示，不认任何具体武将。
        """

        return ""

    def public_identity_of(self, player):
        """该角色**对外公开**的身份（None = 未知 / 无身份）。

        不带任何观众视角：主公身份、已公开的、已阵亡的才返回，其余一律
        None。AI 与 UI 都用它判断"这个人的身份是否已经摆在桌面上"——
        带观众视角会让所有 AI 直接看穿隐藏身份（等于作弊）。
        自己看自己的身份请用 ``visible_identity(player, viewer=player)``。
        """

        from ..identity import visible_identity

        return visible_identity(player, None)

    def identity_label(self, player):
        from ..identity import identity_name

        return identity_name(self.public_identity_of(player))

    def reveal_all(self):
        """对局结束时公开全部身份（供结果界面使用）。"""

        return None

    def result_lines(self):
        """结算面板要列出的信息行；无身份模式返回空。"""

        return ()

    def result_headline(self):
        """结算面板的胜负文案；默认按通用结果推断。"""

        return ""

    def overlay_result_texts(self, game):
        """结算面板的 (标题, 副标题, 颜色)；None = 用通用推断。

        通用推断按"本机真人赢没赢"写文案，而双边手动模式下两边都是本机
        操作者，"你赢了"没有意义——那种模式自己给出中立文案。
        """

        return None

    def restart_battle(self, game):
        """「重新开始」：返回 True 表示模式已经自己重开了这一局。

        默认返回 False，由 ``Game`` 走通用的"回到开局流程"。
        """

        return False

    # ==================================================
    # 死亡与胜负
    # ==================================================

    def on_death(self, dead_player, source):
        """死亡奖惩钩子；默认什么也不做。"""

        return None

    def resolve_death(self, flow):
        """按模式规则给出死亡结算结论。"""

        return self._last_survivor_resolution(flow)

    def _last_survivor_resolution(self, flow):
        from ..engine.state import GameOutcome, GameResult

        game = self.game
        dead_player = flow.dead_player
        already_over = game.game_over
        alive = [player for player in game.players if player.alive]
        winner = alive[0] if len(alive) == 1 else None
        human_eliminated = dead_player is game.player
        no_survivors = not alive

        game.game_over = already_over or human_eliminated or no_survivors or winner is not None
        if game.game_over:
            game.phase = "over"

        if already_over:
            return DeathResolution(
                finished=True, outcome=None, winner=None,
                reason="ALREADY_OVER", message="",
            )

        if winner is not None:
            outcome = (
                GameOutcome.PLAYER_WIN if winner is game.player else GameOutcome.AI_WIN
            )
            if len(game.players) == 2:
                message = "你获胜了！" if winner is game.player else "你阵亡了！"
            else:
                message = "你获胜了！" if winner is game.player else winner.name + " 获胜"
            game.message = message
            game.result = GameResult(
                outcome=outcome, winner=winner, loser=dead_player, reason="LAST_SURVIVOR")
            game.winner = winner
            return DeathResolution(
                finished=True, outcome=outcome, winner=winner,
                reason="LAST_SURVIVOR", message=message,
            )

        if human_eliminated:
            game.message = "你已阵亡 / 游戏失败"
            game.result = GameResult(
                outcome=GameOutcome.HUMAN_ELIMINATED, winner=None,
                loser=dead_player, reason="HUMAN_ELIMINATED")
            game.winner = None
            return DeathResolution(
                finished=True, outcome=GameOutcome.HUMAN_ELIMINATED,
                reason="HUMAN_ELIMINATED", message=game.message,
            )

        if no_survivors:
            game.message = "全场阵亡，无人获胜"
            game.result = GameResult(
                outcome=GameOutcome.NO_SURVIVOR, winner=None,
                loser=dead_player, reason="NO_SURVIVOR")
            game.winner = None
            return DeathResolution(
                finished=True, outcome=GameOutcome.NO_SURVIVOR,
                reason="NO_SURVIVOR", message=game.message,
            )

        game.message = dead_player.name + " 阵亡"
        return DeathResolution(
            finished=False, reason="ELIMINATED", message=game.message)


class GameModeRegistry:
    """Stable id → mode class lookup."""

    def __init__(self):
        self._modes = {}

    def register(self, mode_cls):
        mode_id = getattr(mode_cls, "id", None)
        if not mode_id:
            raise ValueError("GameMode needs a stable id")
        if mode_id in self._modes:
            raise ValueError("game mode already registered: " + mode_id)
        self._modes[mode_id] = mode_cls
        return mode_cls

    def get(self, mode_id):
        return self._modes.get(mode_id)

    def require(self, mode_id):
        mode = self.get(mode_id)
        if mode is None:
            raise KeyError("unknown game mode: " + str(mode_id))
        return mode

    def list_modes(self):
        return tuple(self._modes.values())

    def ids(self):
        return tuple(self._modes)

    def __contains__(self, mode_id):
        return mode_id in self._modes


def create_default_mode_registry():
    from .duel import DuelTestMode
    from .ffa import FreeForAllMode
    from .identity import IdentityMode

    registry = GameModeRegistry()
    registry.register(FreeForAllMode)
    registry.register(IdentityMode)
    registry.register(DuelTestMode)
    return registry
