"""周瑜 · 反间的分步结算。

规则实现口径（见 docs/rules/phase_8_general_rules_reference.md）：
周瑜展示一张手牌交给目标；目标若弃置一张同花色手牌则免疫，否则受到 1 点伤害。
「目标声明花色」这一步不影响结算结果，本项目第一版省略。
"""

from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import Flow, FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.flows.damage import DamageContext, DamageFlow


class FanjianFlow(Flow):
    def __init__(self, engine, source, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.source = source
        self.target = target
        self.stage = "choose_card"

    def advance(self, response=None):
        if self.stage == "choose_card":
            if response is not None:
                cards = list(response.cards or ())
                if not cards:
                    return self.complete({"cancelled": True})
                return self._resolve(cards[0])
            request = self.engine.pending.create(
                PendingRequestType.SELECT_CARDS,
                source=self.source,
                target=self.source,
                prompt="【反间】：请选择一张手牌交给 " + self.target.name,
                owner_flow=self,
                min_cards=1,
                max_cards=1,
                request_context={
                    "reason": "fanjian",
                    "candidates": list(self.source.hand),
                    "zone": "hand",
                    "zone_owner": self.source,
                },
            )
            self.wait(request)
            self.engine.present_or_auto_resolve(request)
            return self.current_result()
        raise RuntimeError("FanjianFlow cannot advance from stage " + self.stage)

    def _resolve(self, card):
        context = self.context
        context.apply(MoveCardAtom(card, source=self.source.hand, destination=self.target.hand))

        same_suit = [
            held for held in self.target.hand
            if held is not card and held.suit == card.suit
        ]
        if same_suit:
            context.apply(MoveCardAtom(
                same_suit[0],
                source=self.target.hand,
                destination=self.game.deck.discard_pile,
            ))
            self.game.add_log(self.target.name + " 弃置一张同花色手牌，抵消【反间】")
        else:
            self.game.add_log(self.target.name + " 没有同花色手牌，受到【反间】伤害")
            DamageFlow(
                self.engine,
                DamageContext(self.source, self.target, 1, card=card),
            ).start()
        return self.complete({"card": card})
