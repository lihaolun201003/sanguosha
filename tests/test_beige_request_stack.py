"""Regression for a covered human Beige card request in identity games."""

import unittest

from src.card_catalog import create_development_deck
from src.game.core import Game
from src.game.flows.damage import DamageContext, DamageFlow
from tests.legacy_helpers import normal_sha, shan, tao


class BeigeRequestStackTests(unittest.TestCase):
    def test_ai_beige_covers_then_restores_human_card_selection(self):
        game = Game(ai_count=4)
        game.set_mode("identity")
        game.ai_pacing = True
        game.start_local_battle(4)
        game.actions.clear()
        game.set_general(game.player, "caiwenji")
        game.set_general(game.players[2], "sp_caiwenji")
        for index in (1, 3, 4):
            game.set_general(game.players[index], "guanyu")

        diamond = next(card for card in create_development_deck()
                       if card.suit == "diamond")
        game.player.hand = [diamond]
        game.players[2].hand = [shan()]
        # 牌堆多备几张：悲歌的判定与"方块摸两张"都会抽牌，牌堆一旦抽空就会
        # 把弃牌堆（含刚弃掉的那张 diamond）重洗回来，最后那条"diamond 已经
        # 不在手里"的断言就会随机失败——那是牌堆太小的假象，不是规则问题。
        game.deck.draw_pile = ([tao(), shan(), tao()]
                               + [shan() for _ in range(20)])

        DamageFlow(game.engine, DamageContext(
            source=game.players[1], target=game.player, amount=1,
            card=normal_sha())).start()

        outer, inner = game.engine.pending._stack
        self.assertEqual(outer.context["reason"], "beige")
        self.assertEqual(inner.context["reason"], "beige")
        self.assertIs(game.pending_request, inner)
        self.assertIsNone(game.pending_selection)

        # Even a stale panel delivered by a delayed UI event cannot answer
        # the covered request or crash the main loop.
        game.get_controller(game.player).present(outer)
        game.select_pending_card(diamond, (0, 0, 80, 120))
        self.assertIs(game.pending_request, inner)
        self.assertIn(diamond, game.player.hand)
        game.pending_selection = None

        for _ in range(100):
            if game.pending_request is outer and game.pending_selection is not None:
                break
            game.update(1)
        else:
            self.fail("human Beige request was not restored after AI Beige")

        self.assertEqual(game.pending_selection["request_id"], outer.request_id)
        game.select_pending_card(diamond, (0, 0, 80, 120))
        self.assertIsNone(game.pending_request)
        self.assertNotIn(diamond, game.player.hand)


if __name__ == "__main__":
    unittest.main()
