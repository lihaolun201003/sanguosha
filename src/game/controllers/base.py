"""决策来源的统一边界：本地真人 / 远程真人 / AI 共用同一个接口。

规则层只通过这里要决策，因此 TurnFlow、ResponseSystem、技能流程里不会出现
"if remote:" 这类特判——身份只决定**谁来回答**，规则只有一套。

两个入口：

* ``present(request)``：引擎的 ``PendingRequest``（响应牌 / 确认 / 选牌 /
  选目标 / 选选项）交给谁回答；
* ``take_turn(on_complete, can_play)``：出牌阶段由谁驱动。

本地真人与 AI 是同步的（调用后立刻有结果），远程真人是异步的（调用后先发
请求，结果由网络回来时再提交），所以 ``asynchronous`` 只描述"要不要等"，
不改变任何规则。
"""

from src.game.available_actions import TargetMode


class PlayerController:
    """一个"决策来源"。"""

    #: True 表示 present() 之后不会立刻有结果（远程真人等网络）。
    asynchronous = False

    def __init__(self, game, player):
        self.game = game
        self.player = player

    # ==================================================
    # 提交
    # ==================================================

    def submit(self, action):
        if action.actor is not self.player:
            raise ValueError("controller cannot submit an action for another player")
        return self.game.submit_action(action)

    # ==================================================
    # 决策入口（子类必须实现）
    # ==================================================

    def present(self, request):
        """回答一个 PendingRequest。同步实现当场提交，异步实现挂起等待。"""

        raise NotImplementedError(type(self).__name__ + " 没有实现 present()")

    def take_turn(self, on_complete, can_play=True):
        """出牌阶段：轮到这名角色时由它驱动，结束时调用 ``on_complete``。"""

        raise NotImplementedError(type(self).__name__ + " 没有实现 take_turn()")

    def notify(self, kind, **payload):
        """状态变化通知（远程真人靠它刷新客户端画面，本地/AI 不需要）。"""

        return None

    def answerer(self, request):
        """这条请求里的"我"是谁。

        单人请求（杀 / 闪、救援、选牌…）的回答者就是 ``target``；共享响应阶段
        （无懈）里一条请求同时问好几个人，``target`` 只是"第一个有资格的人"，
        所以必须用控制器自己的座位判断。
        """

        if getattr(request, "is_group", False):
            return self.player
        return request.target

    def withdraw(self, request, reason=""):
        """引擎收回了对这名玩家的一次询问（例如本轮已经有人打出了无懈）。

        AI 没有界面，什么都不用做；有界面的控制器必须把自己那块面板收掉，
        否则玩家会对着一条已经作废的请求继续操作（"停在询问"的经典来源）。
        """

        return None

    # ==================================================
    # 规则查询（三种决策来源共用；判定本身仍然来自引擎的 CardEffect）
    # ==================================================

    def card_effect(self, card):
        return self.game.engine.card_effects.get(card)

    def can_use(self, card, action):
        """这张牌现在能不能这样用（由牌自己的 CardEffect 判定）。"""

        effect = self.card_effect(card)
        if effect is None:
            return False
        return bool(effect.can_use(self.game, action)[0])

    def legal_targets(self, card):
        """这张牌在当前局面下的**合法目标**（按座次顺序，不做价值排序）。

        目标规则与数量来自 ``AvailableActions`` 的统一查询（里面走
        ``CardEffect.target_rule_for`` / ``target_bounds_for`` 的动态接口），
        所以距离、出杀次数、装备限制、技能改写都只有一份实现。
        兼容包装：调用方可以继续用这个名字，但不再自己算一份规则。
        """

        return list(self.target_profile(card).players)

    def target_limits(self, card):
        """这张牌要选几个目标：``(最少, 最多)``；不需要挑目标时返回 ``(0, 0)``。

        Phase 15A：不再对 MULTIPLE 固定返回 ``(1, 2)``——那会让技能改写过的
        目标上限（天义 / 神戟）在这里被打回旧值。现在统一问动态规则。
        """

        profile = self.target_profile(card)
        if profile.mode != TargetMode.CHOOSE:
            return (0, 0)
        return profile.bounds

    def target_profile(self, card):
        """这张牌的目标结论（候选 / 数量 / 模式）——共同查询的兼容包装。"""

        from src.game.available_actions import AvailableActions

        return AvailableActions(self.game).target_profile(self.player, card=card)

    # ==================================================
    # 弃牌（AI 与远程真人共用；本地真人走鼠标交互）
    # ==================================================

    def discard_to_hand_limit(self):
        """弃到体力上限的兜底实现（AI 会覆写成"丟价值最低的"）。

        远程真人正常会先问客户端要弃哪几张；这个方法只在决策链路已经作废
        （掉线终止等）时兜底，保证流程不会卡在弃牌阶段。
        """

        limit = self.game.hand_limit(self.player)
        surplus = len(self.player.hand) - limit
        if surplus <= 0:
            return []
        return self.discard_cards(list(self.player.hand)[:surplus])

    def discard_cards(self, cards):
        """把指定牌从手牌移到弃牌堆（权威移动，动画由引擎的原子负责）。"""

        from src.game.atoms_v2 import MoveCardAtom

        moved = []
        for card in list(cards):
            if card not in self.player.hand:
                continue
            self.game.engine.context.apply(
                MoveCardAtom(
                    card,
                    source=self.player.hand,
                    destination=self.game.deck.discard_pile,
                )
            )
            moved.append(card)
        return moved
