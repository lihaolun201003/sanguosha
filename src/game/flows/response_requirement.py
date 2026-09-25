"""一次"需要交出 N 张合法响应牌"的通用状态。

【杀】的目标可能需要连续打出两张【闪】（无双），【决斗】的对手每轮可能
需要连续打出两张【杀】——这类需求的形状完全一样：

    required      本次一共要交出几张
    accepted      已经真实交出几张（每张都走过一次真实的 Card Movement）
    remaining     还差几张（用于提示文案）

规则层只提供数字（``Game.response_required_count`` 读 RESPONSE_COUNT
modifier），效果层按数字循环要牌，**没有任何地方认具体武将或技能**。

逐张结算是有意的：第一张一旦交出就已经真实离开手牌，即使第二张交不出来，
第一张也不会被退回——所以这里只在**牌已经被引擎移动之后**才 ``accept()``。
"""

from src.game.engine.pending import PendingRequestType


class ResponseRequirement:
    """一条"要交出 N 张牌"的需求及其进度。"""

    def __init__(self, required=1, allowed=(), reason="", card=None):
        self.required = max(1, int(required))
        self.allowed = frozenset(allowed)
        self.reason = reason
        self.card = card
        self.accepted_cards = []

    # ---- 构造 ----

    @classmethod
    def for_source(cls, game, source, responder, card, allowed, reason):
        """按规则层的 RESPONSE_COUNT modifier 建需求。

        ``source`` 是**决定需求张数的那一方**：无双持有者在决斗里既可能是
        使用者也可能被决斗，所以调用方传的是"responder 的对手"，而不是
        固定的某一方。
        """

        if game is None:
            required = 1
        else:
            required = game.response_required_count(source, responder, card)
        return cls(required=required, allowed=allowed, reason=reason, card=card)

    # ---- 进度 ----

    @property
    def accepted_count(self):
        return len(self.accepted_cards)

    @property
    def remaining(self):
        return max(1, self.required - self.accepted_count)

    @property
    def satisfied(self):
        return self.accepted_count >= self.required

    def accept(self, card):
        """记下一张**已经真实移动过**的响应牌；返回是否已满足。"""

        if card is not None:
            self.accepted_cards.append(card)
        return self.satisfied

    # ---- 展示与请求 ----

    def prompt(self, base):
        if self.required <= 1:
            return base
        return "%s（还需要 %d 张）" % (base, self.remaining)

    def request_context(self, **extra):
        payload = {
            "reason": self.reason,
            "card": self.card,
            "required": self.required,
            "accepted": self.accepted_count,
        }
        payload.update(extra)
        return payload

    def create_request(self, engine, *, flow, source, responder, prompt):
        """按当前剩余张数创建响应请求（每次只接受一张）。"""

        request = engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=source,
            target=responder,
            prompt=self.prompt(prompt),
            owner_flow=flow,
            allowed_cards=set(self.allowed),
            min_cards=0,
            max_cards=1,
            request_context=self.request_context(),
        )
        return request
