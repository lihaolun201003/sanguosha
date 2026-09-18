"""Iterative nullification response chain supporting arbitrary nesting."""

from src.game.engine.flows import Flow
from src.game.engine.pending import PendingRequestType
from src.game.rules import living_players


class WuxieResponseChain(Flow):
    def __init__(self, engine, source, card, targets, on_complete):
        super().__init__(engine.context)
        self.engine = engine
        self.source = source
        self.card = card
        self.targets = list(targets)
        living = living_players(engine.game)
        self.responders = engine.game.seats.alive_players_in_order(start_after=source, include_start=True)
        if not self.responders:
            self.responders = living
        self.current_responder = 0
        self.pass_count = 0
        self.nullified = False
        self.wuxie_count = 0
        self.on_complete = on_complete

    def advance(self, response=None):
        if response is not None:
            if response.card is not None:
                self.nullified = not self.nullified
                self.wuxie_count += 1
                self.pass_count = 0
            else:
                self.pass_count += 1
            self.current_responder = (self.current_responder + 1) % len(self.responders)

        # The user of a trick is not prompted to nullify their own card at the
        # initial level.  Once somebody else has played Wuxie, the source joins
        # the response round normally and may counter that Wuxie.
        while (
            self.responders
            and self.wuxie_count == 0
            and self.responders[self.current_responder] is self.source
            and self.pass_count < len(self.responders)
        ):
            self.pass_count += 1
            self.current_responder = (self.current_responder + 1) % len(self.responders)

        if not self.responders or self.pass_count >= len(self.responders):
            result = self.complete({"nullified": self.nullified})
            self.on_complete(self.nullified)
            return result

        responder = self.responders[self.current_responder]
        if not responder.alive or responder.hp <= 0:
            self.pass_count += 1
            self.current_responder = (self.current_responder + 1) % len(self.responders)
            return self.advance()
        request = self.engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=self.source,
            target=responder,
            prompt="【" + self.card.display_name + "】即将生效：可使用【无懈可击】",
            owner_flow=self,
            allowed_cards={"WUXIE"},
            min_cards=0,
            max_cards=1,
            request_context={
                "reason": "wuxie_chain",
                "card": self.card,
                "nullified": self.nullified,
                "targets": self.targets,
            },
        )
        self.wait(request)
        self.engine.present_or_auto_resolve(request)
        return self.current_result()
