import random

from src.card_catalog import (
    create_standard_military_deck,
)


class Deck:

    def __init__(self, rng=None):

        # 抽牌堆
        self.draw_pile = []

        # 弃牌堆
        self.discard_pile = []

        # 洗牌用的随机源。Game 把自己的 rng 传进来，于是"固定种子"能覆盖到
        # 牌堆；不传时保持旧行为（自带一个无种子的流）。
        self.rng = rng if rng is not None else random.Random()

        self.reset()


    # ==================================================
    # 新牌局
    # ==================================================

    def reset(self):

        self.draw_pile = (
            create_standard_military_deck()
        )

        self.discard_pile = []

        self.rng.shuffle(
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

        self.rng.shuffle(
            self.draw_pile
        )
