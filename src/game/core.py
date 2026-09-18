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

from .turn import TurnMixin
from .card_selection import CardSelectionMixin
from .basic_cards import BasicCardMixin
from .equipment import EquipmentMixin
from .combat import CombatMixin
from .dying import DyingMixin
from .ai import AIMixin
from .engine import GameContext, GameEngine
from .rules import SeatManager


class Game(
    CardSelectionMixin,
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

        self.deck = Deck()

        self.ai_count = max(1, min(7, int(ai_count)))
        self.players = []
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

        # 丈八蛇矛的玩家选牌状态。
        self.zhangba_selecting = False
        self.zhangba_selected = []

        # 武器与防具效果需要玩家点选具体卡牌时使用。
        self.pending_selection = None
        self.pending_target_selection = None

        # 当前停留在桌面上的牌
        self.table_cards = []
        self.judge_card = None
        self.processing_zone = []
        self.public_card_pool = []
        self.revealed_card = None
        self.current_turn_player = self.player
        self.turn_phase = None
        self.skipped_phases = set()
        self.active_turn_flow = None

        self.phase = "play"

        self.message = ""

        # 玩家本回合状态
        self.sha_used = False
        self.jiu_used = False

        self.player_wine_buff = False

        # 使用酒以后锁定：
        # 下一步必须出杀
        self.wine_sha_required = False

        # AI 酒状态
        self.enemy_wine_buff = False
        self.enemy_jiu_used = False

        self.game_over = False
        self.result = None
        self.winner = None
        self.game_log = []

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
        # Compatibility façade: the first AI remains available to old 1v1 UI
        # and tests. New rules must use players/seats APIs.
        self.enemy = ais[0]

    def get_player(self, player_id):
        return next((p for p in self.players if p.player_id == player_id), None)

    def get_alive_players(self):
        return self.seats.alive_players_in_order()

    def get_alive_players_in_seat_order(self, start_after=None):
        return self.seats.alive_players_in_order(start_after=start_after)

    def get_next_alive_player(self, player_or_id):
        player = self.get_player(player_or_id) if isinstance(player_or_id, str) else player_or_id
        return self.seats.next_alive_player(player)

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

    def start_local_battle(self, ai_count):

        self.ai_count = max(1, min(7, int(ai_count)))

        self.reset()
        self.scene = "game"
        self.start_turn(self.player)


    def show_multiplayer_notice(self):

        self.menu_message = "多人对战正在开发中，暂未开放"

    def return_to_menu(self):
        self.reset()
        self.scene = "menu"
        self.menu_message = "请选择 AI 数量（1～7）"


    # ==================================================
    # 是否正在播放动画
    # ==================================================

    @property
    def busy(self):

        return self.actions.busy


    # ==================================================
    # 每一帧更新
    # ==================================================

    def update(
        self,
        dt
    ):

        self.actions.update(dt)


    def submit_action(self, action):

        return self.engine.submit(action)


    @property
    def pending_request(self):

        return self.engine.pending.current


    # ==================================================
    # 重置整个游戏
    # ==================================================

    def reset(self):

        self.actions.clear()

        self.response.clear()

        self.choice.clear()

        self.engine.reset()

        self._create_players()
        self.seats = SeatManager(self)

        self.zhangba_selecting = False
        self.zhangba_selected = []
        self.pending_selection = None
        self.pending_target_selection = None

        self.table_cards.clear()
        self.judge_card = None
        self.processing_zone.clear()
        self.public_card_pool.clear()
        self.revealed_card = None
        self.current_turn_player = self.player
        self.turn_phase = None
        self.skipped_phases.clear()
        self.active_turn_flow = None

        self.deck.reset()

        for player in self.players:
            player.reset()

        self.phase = "play"

        self.sha_used = False
        self.jiu_used = False

        self.player_wine_buff = False

        self.wine_sha_required = False

        self.enemy_wine_buff = False

        self.enemy_jiu_used = False

        self.game_over = False
        self.result = None
        self.winner = None
        self.game_log = []

        # ==================================================
        # 双方初始四张
        # ==================================================

        for player in self.players:
            player.draw_cards(self.deck, 4)

        self.message = (
            "你的回合。"
        )
        self.add_log("本地自由混战开始：" + str(len(self.players)) + " 人")


    # ==================================================
    # 将牌固定显示在桌面
    # ==================================================

    def add_table_card(
        self,
        card,
        rect
    ):

        self.table_cards.append(
            (
                card,
                tuple(rect),
            )
        )


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

        for _ in range(number):

            card = self.deck.draw()

            if card is None:
                break

            self.actions.add(

                MoveCardAction(
                    card,
                    DRAW_PILE_RECT,
                    target_rect,
                    duration=0.28,

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
