class ResponseRequest:

    def __init__(
        self,
        prompt,
        allowed_cards,
        on_card=None,
        on_pass=None,
        reason="",
        responder=None
    ):
        self.prompt = prompt
        self.allowed_cards = set(allowed_cards)

        self.on_card = on_card
        self.on_pass = on_pass
        #: 这条响应请求的语义标签（"wuxie_chain" / "sha" / "dying_rescue"…）。
        #: 只给提示层用（例如把共享无懈阶段显示成"使用无懈／不出"），
        #: 不参与任何规则判断。
        self.reason = str(reason or "")
        #: 这块面板**属于谁**（本机这一侧正在回答的角色）。
        #: 单机永远等于本机真人；双边手动测试用它把视角切到回答者身上。
        #: 只用于界面归属，规则判定不看它。
        self.responder = responder


class ResponseSystem:

    def __init__(self):
        self.current = None


    @property
    def active(self):
        return self.current is not None


    def clear(self):
        self.current = None


    def request(
        self,
        prompt,
        allowed_cards,
        on_card,
        on_pass=None,
        reason="",
        responder=None
    ):

        self.current = ResponseRequest(
            prompt,
            allowed_cards,
            on_card,
            on_pass,
            reason=reason,
            responder=responder,
        )


    def can_play(self, card):

        if self.current is None:
            return False

        return (
            card.name
            in self.current.allowed_cards
        )


    def play_card(
        self,
        index,
        card,
        source_rect
    ):

        if not self.can_play(card):
            return False

        request = self.current

        self.current = None

        request.on_card(
            index,
            card,
            source_rect
        )

        return True


    def play_action(
        self,
        index,
        card,
        source_rect,
        effective_card=None
    ):
        """提交一个已经通过 Card Action Discovery 校验的响应动作。

        转换牌（例如【龙胆】把【杀】当【闪】打出）的实体牌名不在
        ``allowed_cards`` 里，因此这里不再重复按牌名过滤：合法性已经由
        Discovery 依据同一个 requirement 判定过。
        """

        if self.current is None:
            return False

        if effective_card is None and not self.can_play(card):
            return False

        request = self.current

        self.current = None

        request.on_card(
            index,
            card,
            source_rect
        )

        return True


    def pass_response(self):

        if self.current is None:
            return False

        request = self.current

        self.current = None

        if request.on_pass is not None:
            request.on_pass()

        return True