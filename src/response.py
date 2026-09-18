class ResponseRequest:

    def __init__(
        self,
        prompt,
        allowed_cards,
        on_card,
        on_pass=None
    ):
        self.prompt = prompt
        self.allowed_cards = set(allowed_cards)

        self.on_card = on_card
        self.on_pass = on_pass


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
        on_pass=None
    ):

        self.current = ResponseRequest(
            prompt,
            allowed_cards,
            on_card,
            on_pass
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


    def pass_response(self):

        if self.current is None:
            return False

        request = self.current

        self.current = None

        if request.on_pass is not None:
            request.on_pass()

        return True