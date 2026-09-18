import random

from src.card_catalog import (
    create_standard_military_deck,
)


class Deck:

    def __init__(self):

        # 抽牌堆
        self.draw_pile = []

        # 弃牌堆
        self.discard_pile = []

        self.reset()


    # ==================================================
    # 新牌局
    # ==================================================

    def reset(self):

        self.draw_pile = (
            create_standard_military_deck()
        )

        self.discard_pile = []

        random.shuffle(
            self.draw_pile
        )


    # ==================================================
    # 抽牌
    # ==================================================

    def draw(self):

        # 抽牌堆空
        if not self.draw_pile:

            self.reshuffle_discard_pile()


        # 弃牌堆也没有
        if not self.draw_pile:

            return None


        return self.draw_pile.pop()


    # ==================================================
    # 弃牌
    # ==================================================

    def discard(self, card):

        if card is None:
            return

        self.discard_pile.append(
            card
        )


    # ==================================================
    # 弃牌堆重新洗成抽牌堆
    # ==================================================

    def reshuffle_discard_pile(self):

        if not self.discard_pile:
            return


        self.draw_pile = (
            self.discard_pile.copy()
        )

        self.discard_pile.clear()

        random.shuffle(
            self.draw_pile
        )
