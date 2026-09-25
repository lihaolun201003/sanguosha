"""共享的【无懈可击】阶段：一轮问遍所有有资格的人，谁先打出谁锁定本轮。

与旧的"按座次逐人询问"相比，语义上有三处不同（Phase 11.6）：

1. **共享**：每一轮由房主**一次**算出所有有合法响应的人（含锦囊使用者），
   把同一条 ``PendingRequest`` 同时交给他们（``responders``），而不是排队
   一个一个问。"有资格"= ``Card Action Discovery`` 的
   ``respondable_options``——实体【无懈可击】与技能转化一视同仁，而 source
   还没凑齐的转化不算（现在支付不出来）。
2. **抢答**：本轮**第一个**合法打出的【无懈可击】锁定本轮，翻转抵消状态并
   开启下一轮（重新算资格，上一轮放弃过的人照样可以再响应）。本轮所有人都
   放弃 → 阶段结束，按最终抵消状态继续结算。
3. **不问无牌的人**：谁都没有合法响应时根本不开窗口，绝不弹一个只能点
   "不出"的空询问。

窗口号（``window_id``）在整个无懈阶段内稳定，轮次号（``round_id``）每轮 +1；
两者都随请求上下文下发，供网络层与 UI 判断"这条回答属于哪一轮"。
"""

from src.game.engine.flows import Flow
from src.game.engine.pending import PendingRequestType
from src.game.rules import living_players


class WuxieResponseChain(Flow):
    """一张可被无懈的锦囊所拥有的无懈阶段（多轮、同步询问）。"""

    #: 查询"谁能无懈"时要求的逻辑牌名。
    REQUIRED_CARD = "WUXIE"

    def __init__(self, engine, source, card, targets, on_complete):
        super().__init__(engine.context)
        self.engine = engine
        self.source = source
        self.card = card
        self.targets = list(targets)
        self.on_complete = on_complete
        #: 整个无懈阶段的稳定编号（同一个阶段的所有轮次共用）。
        self.window_id = engine.open_response_window()
        #: 轮次号：第一轮从 1 开始，每次有人打出无懈就 +1。
        self.round_id = 0
        self.nullified = False
        self.wuxie_count = 0
        #: 每个成功打出无懈的人（战报 / 测试取证）。
        self.played = []

    # ==================================================
    # 资格：唯一判据来自 Card Action Discovery
    # ==================================================

    def eligible_responders(self):
        """现在**真的**能打出【无懈可击】的存活角色（含锦囊使用者），按座次。"""

        game = self.engine.game
        order = game.seats.alive_players_in_order(
            start_after=self.source, include_start=True)
        if not order:
            order = living_players(game)
        actions = getattr(game, "card_actions", None)
        if actions is None:                          # pragma: no cover - 兜底
            return []
        return [
            player for player in order
            if actions.can_respond(player, allowed_names=(self.REQUIRED_CARD,))
        ]

    # ==================================================
    # 推进
    # ==================================================

    def advance(self, response=None):
        if response is not None:
            if getattr(response, "card", None) is None:
                # 本轮有资格的人全部放弃：阶段结束，按当前抵消状态继续结算。
                return self._finish()
            self.wuxie_count += 1
            self.nullified = not self.nullified
            self.played.append(response.actor)
            self.engine.game.message = (
                getattr(response.actor, "name", "?") + "使用了【无懈可击】。")
        return self._open_round()

    def _open_round(self):
        eligible = self.eligible_responders()
        if not eligible:
            return self._finish()

        self.round_id += 1
        request = self.engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=self.source,
            target=self._round_target(eligible),
            prompt=self.round_prompt(),
            owner_flow=self,
            allowed_cards={self.REQUIRED_CARD},
            min_cards=0,
            max_cards=1,
            responders=tuple(eligible),
            request_context={
                "reason": "wuxie_chain",
                "window_id": self.window_id,
                "round_id": self.round_id,
                "card": self.card,
                "nullified": self.nullified,
                "targets": self.targets,
                "group": True,
            },
        )
        self.wait(request)
        self.engine.present_group(request)
        return self.current_result()

    def _round_target(self, eligible):
        """``target`` 取谁：本机真人成员优先，否则第一个有资格的人。

        共享阶段没有"唯一的回答者"，``responders`` 才是资格集合。这里选本机
        真人（如果他也在这轮里）是为了让**驱动器**（工具脚本 / 脚手架）沿用
        "看 target 就知道要不要替真人回答"的既有写法；引擎自己不依赖它。
        """

        human = getattr(self.engine.game, "player", None)
        for member in eligible:
            if member is human:
                return human
        return eligible[0]

    def round_prompt(self):
        """本轮给玩家的问法：第一轮是"即将生效"，之后说明上一手无懈的结果。"""

        display = self.card.display_name
        if self.round_id <= 1:
            return "【" + display + "】即将生效：可使用【无懈可击】"
        actor = self.played[-1] if self.played else None
        return "%s使用【无懈可击】，【%s】%s：是否继续使用【无懈可击】？" % (
            getattr(actor, "name", "?"), display,
            "被抵消" if self.nullified else "重新生效")

    def _finish(self):
        result = self.complete({
            "nullified": self.nullified,
            "wuxie_count": self.wuxie_count,
        })
        self.on_complete(self.nullified)
        return result
