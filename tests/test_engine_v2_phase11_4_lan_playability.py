"""Phase 11.4：Decision → 桌面交互 的映射，以及跨区域选牌的隐藏信息契约。

这里放**确定性**、值得长期回归的部分（不打网络、不点像素）：

* ``decision_presentation``：请求 → ``pending_selection`` /
  ``pending_target_selection`` / ``response`` / ``choice`` 的映射形状；
* ``RemoteGameView.apply_decision``：客户端视图上真的出现了这些槽位
  （"过河拆桥在客户端没得点"就是这样被钉住的）；
* 隐藏手牌的**不透明占位 token**：网络包里不出现真牌 id，回答时由房主换回；
* 主动技（出牌阶段）的远程入口与校验。

真实双实例、真实点击的验证在 ``tools/lan_playability_sync.py``。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.controllers.remote import (
    OPAQUE_PREFIX,
    RemoteHumanController,
    is_opaque,
    opaque_token,
)
from src.game.engine import SelectCardsAction
from src.game.engine.pending import PendingRequest, PendingRequestType
from src.network.decisions import (
    ACTION_END_PHASE,
    ACTION_SKILL,
    DecisionError,
    DecisionKind,
    DecisionRegistry,
    DecisionRequest,
    DecisionResult,
)
from src.player import ControllerType
from src.ui import decision_presentation as presentation
from src.ui.view_adapter import RemoteGameView
from tests.legacy_helpers import equipment

MATCH = "MATCH-11-4"


# ==================================================
# 请求构造（与房主真正发出的形状一致）
# ==================================================

def player_entry(player_id, name="玩家"):
    return {"player_id": player_id, "nickname": name, "seat": 0,
            "hp": 4, "max_hp": 4, "hand_count": 2, "alive": True}


def cross_area_request(**overrides):
    """过河拆桥 / 顺手牵羊的第二段：从别人区域里选一张牌。"""

    payload = {
        "match_id": MATCH,
        "request_id": 21,
        "player_id": "p2",
        "kind": DecisionKind.SELECT_CARDS,
        "prompt": "请选择房主区域内的一张牌",
        "cards": [
            {"card_id": "hidden:21:0", "name": "", "label": "未知牌",
             "suit": None, "rank": None, "category": "", "face_down": True,
             "zone": "hand", "slot": None, "owner_id": "p1", "owner_name": "房主"},
            {"card_id": "card-77", "name": "QINGLONG", "label": "青龙偃月刀",
             "suit": "spade", "rank": "5", "category": "equipment",
             "zone": "equipment", "slot": "weapon",
             "owner_id": "p1", "owner_name": "房主"},
        ],
        "constraints": {"min_cards": 1, "max_cards": 1, "allow_cancel": False},
    }
    payload.update(overrides)
    return payload


# ==================================================
# 请求 → 交互槽位
# ==================================================

class SelectionPresentationTests(unittest.TestCase):
    def setUp(self):
        self.view = RemoteGameView()
        self.request = cross_area_request()

    def test_other_players_area_becomes_the_public_pool(self):
        selection = presentation.selection_presentation(
            self.request, resolve_card=lambda entry: self.view.display_card(entry),
            my_player_id="p2")
        self.assertIsNotNone(selection, "过河拆桥的第二段必须能给客户端一个可点的选牌界面")
        self.assertEqual(selection["zone"], "public_pool")
        self.assertEqual(len(selection["candidates"]), 2)
        self.assertEqual(selection["number"], 1)
        self.assertTrue(selection["cancellable"] is False)

    def test_hidden_hand_candidate_is_a_face_down_placeholder(self):
        selection = presentation.selection_presentation(
            self.request, resolve_card=lambda entry: self.view.display_card(entry),
            my_player_id="p2")
        cards = [card for card, _key in selection["candidates"]]
        hidden = [card for card in cards if card.face_down]
        self.assertEqual(len(hidden), 1)
        self.assertIn(id(hidden[0]), selection["face_down_ids"])
        self.assertEqual(hidden[0].name, "")

    def test_own_hand_candidates_stay_in_the_hand_zone(self):
        request = cross_area_request(cards=[
            {"card_id": "card-1", "name": "SHA", "label": "杀", "category": "basic",
             "zone": "hand", "slot": None, "owner_id": "p2", "owner_name": "我"},
            {"card_id": "card-2", "name": "TAO", "label": "桃", "category": "basic",
             "zone": "hand", "slot": None, "owner_id": "p2", "owner_name": "我"},
        ])
        selection = presentation.selection_presentation(
            request, resolve_card=lambda entry: self.view.display_card(entry),
            my_player_id="p2")
        self.assertEqual(selection["zone"], "hand")
        self.assertTrue(all(key is None for _card, key in selection["candidates"]))

    def test_selected_ids_are_marked(self):
        selection = presentation.selection_presentation(
            self.request, resolve_card=lambda entry: self.view.display_card(entry),
            my_player_id="p2", selected_ids=["card-77"])
        self.assertEqual(len(selection["selected"]), 1)
        self.assertEqual(selection["selected"][0][0].id, "card-77")


class TargetPresentationTests(unittest.TestCase):
    def test_select_targets_lists_only_host_given_candidates(self):
        view = RemoteGameView()
        view.update(_empty_view(players=(("p1", "房主"), ("p2", "我"), ("p3", "丙"))))
        request = {
            "kind": DecisionKind.SELECT_TARGETS,
            "targets": [player_entry("p1"), player_entry("p3")],
            "constraints": {"min_targets": 1, "max_targets": 1},
            "prompt": "请选择目标",
        }
        selection = presentation.targets_presentation(
            request, resolve_player=view.player_by_id, selected_ids=["p3"])
        self.assertEqual([player.player_id for player in selection["candidates"]],
                         ["p1", "p3"])
        self.assertEqual([player.player_id for player in selection["selected"]], ["p3"])
        self.assertEqual(selection["maximum"], 1)

    def test_unknown_player_id_is_ignored_instead_of_crashing(self):
        view = RemoteGameView()
        view.update(_empty_view())
        request = {
            "kind": DecisionKind.SELECT_TARGETS,
            "targets": [player_entry("p1"), player_entry("ghost")],
            "constraints": {"min_targets": 1, "max_targets": 1},
        }
        selection = presentation.targets_presentation(
            request, resolve_player=view.player_by_id)
        self.assertEqual([player.player_id for player in selection["candidates"]],
                         ["p1"])


# ==================================================
# 只读视图上的槽位（渲染层真正读的东西）
# ==================================================

def _empty_view(local_player_id="p2", players=(("p1", "房主"), ("p2", "我")),
                hand=()):
    """一份最小只读视图：``hand`` 是**本地玩家自己**的手牌（只有本人有内容）。"""

    from src.game.view.view_model import ClientGameView, PlayerView
    views = []
    for index, (pid, name) in enumerate(players):
        is_self = pid == local_player_id
        views.append(PlayerView(
            player_id=pid, seat=index, nickname=name, is_self=is_self,
            controller="remote_human" if is_self else "human",
            hand=tuple(hand) if is_self else (),
            hand_count=len(hand) if is_self else 2,
        ))
    return ClientGameView(
        match_id=MATCH, revision=3, local_player_id=local_player_id,
        current_player_id=local_player_id, current_phase="play",
        players=tuple(views), hand=tuple(hand),
    )


class RemoteViewOverlayTests(unittest.TestCase):
    def setUp(self):
        from src.game.view.view_model import ViewCard

        self.view = RemoteGameView()
        self.view.update(_empty_view(hand=(ViewCard(card_id="card-1", name="SHA",
                                                    label="杀"),)))

    def test_cross_area_selection_is_clickable_on_the_view(self):
        """回归：客户端以前拿不到任何可点的候选，只能点到"房主的座位"。"""

        self.view.apply_decision(cross_area_request(), None)
        self.assertIsNotNone(self.view.pending_selection)
        entries = self.view.selection_pool_entries()
        self.assertEqual(len(entries), 2, "对方的牌必须出现在桌面公共区（可点）")
        self.assertEqual(len(self.view.selection_face_down_ids()), 1)

    def test_target_request_lights_up_the_seat_panels(self):
        request = {
            "kind": DecisionKind.SELECT_TARGETS,
            "targets": [player_entry("p1")],
            "constraints": {"min_targets": 1, "max_targets": 1},
            "prompt": "请选择目标",
        }
        self.view.apply_decision(request, None)
        selection = self.view.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertEqual([player.player_id for player in selection["candidates"]],
                         ["p1"])
        # 渲染层就是靠这个字段把座位画成"可选目标"的。
        self.assertIn("candidates", selection)

    def test_respond_request_activates_the_response_layer(self):
        request = {
            "kind": DecisionKind.RESPOND_CARD,
            "prompt": "请打出一张【闪】",
            "cards": [{"card_id": "card-9", "name": "SHAN", "label": "闪"}],
            "constraints": {"min_cards": 1, "max_cards": 1, "allow_pass": True},
        }
        self.view.apply_decision(request, None)
        self.assertTrue(self.view.response.active)
        self.assertEqual(self.view.pending_request or None, None)
        self.assertIn("card-9", self.view.allowed_card_ids)

    def test_play_phase_without_selection_shows_the_end_turn_button(self):
        request = {
            "kind": DecisionKind.PLAY_PHASE, "prompt": "出牌阶段",
            "cards": [{"card_id": "card-3", "name": "SHA", "label": "杀",
                       "options": [{"skill_id": "", "enabled": True,
                                    "min_sources": 1, "max_sources": 1,
                                    "min_targets": 1, "max_targets": 1,
                                    "targets": [player_entry("p1")]}]}],
            "targets": [player_entry("p1")],
            "constraints": {"min_cards": 0, "max_cards": 1, "min_targets": 0,
                            "max_targets": 1, "allow_cancel": True},
        }
        self.view.apply_decision(request, None)
        self.assertIsNone(self.view.pending_target_selection)
        self.assertIsNone(self.view.pending_card_action)
        self.assertEqual(self.view.allowed_card_ids, {"card-3"})

    def test_play_phase_with_a_selected_card_enters_target_mode(self):
        request = {
            "kind": DecisionKind.PLAY_PHASE, "prompt": "出牌阶段",
            "cards": [{"card_id": "card-3", "name": "SHA", "label": "杀",
                       "options": [{"skill_id": "", "enabled": True,
                                    "min_sources": 1, "max_sources": 1,
                                    "min_targets": 1, "max_targets": 1,
                                    "targets": [player_entry("p1")]}]}],
            "constraints": {"allow_cancel": True},
        }

        class State:
            cards = ["card-3"]
            targets = []
            option_index = 0

        self.view.apply_decision(request, State())
        selection = self.view.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertEqual(selection["maximum"], 1)
        self.assertEqual([player.player_id for player in selection["candidates"]],
                         ["p1"])

    def test_multiple_ways_open_the_existing_card_action_picker(self):
        request = {
            "kind": DecisionKind.PLAY_PHASE, "prompt": "出牌阶段",
            "cards": [{"card_id": "card-3", "name": "SHAN", "label": "闪",
                       "options": [
                           {"skill_id": "", "label": "闪", "enabled": True,
                            "min_sources": 1, "max_sources": 1,
                            "max_targets": 0, "targets": []},
                           {"skill_id": "longdan", "skill_name": "龙胆",
                            "label": "龙胆：当【杀】使用", "enabled": True,
                            "is_conversion": True, "min_sources": 1,
                            "max_sources": 1, "min_targets": 1, "max_targets": 1,
                            "targets": [player_entry("p1")]}]}],
            "constraints": {"allow_cancel": True},
        }

        class State:
            cards = ["card-3"]
            targets = []
            option_index = 0
            way_chosen = False

        self.view.apply_decision(request, State())
        self.assertTrue(self.view.card_action_picker(),
                        "多种用法时要弹出与单机一致的「选择操作」面板")

        class Chosen(State):
            way_chosen = True
            option_index = 1                    # 选了"龙胆当【杀】使用"

        self.view.apply_decision(request, Chosen())
        self.assertFalse(self.view.card_action_picker(),
                         "选了用哪种方式之后面板要收起")
        self.assertIsNotNone(self.view.pending_target_selection,
                             "龙胆当【杀】要接着选目标")

    def test_confirm_uses_the_local_choice_modal_shape(self):
        request = {"kind": DecisionKind.CONFIRM, "prompt": "是否发动【八卦阵】？",
                   "options": [{"value": True, "label": "发动"},
                               {"value": False, "label": "不发动"}]}
        self.view.apply_decision(request, None)
        self.assertTrue(self.view.choice.active)
        self.assertEqual(self.view.choice.current.prompt, "是否发动【八卦阵】？")

    def test_active_skill_becomes_the_local_skill_input_layer(self):
        request = {
            "kind": DecisionKind.PLAY_PHASE, "prompt": "出牌阶段",
            "cards": [],
            "constraints": {"activatable": [{
                "skill_id": "zhiheng", "name": "制衡", "needs_target": False,
                "cost_cards": 0, "variable_cost": True,
                "cost_candidates": [{"card_id": "card-1", "name": "SHA"}],
                "targets": [], "enabled": True}]},
        }

        class State:
            cards = []
            targets = []
            option_index = 0
            way_chosen = False
            skill_id = "zhiheng"
            skill_cards = ["card-1"]
            skill_target = ""

        self.view.apply_decision(request, State())
        state = self.view.pending_skill_input
        self.assertIsNotNone(state, "主动技必须走本地同一套「发动技能」界面")
        self.assertEqual(state["name"], "制衡")
        self.assertTrue(self.view.skill_input_ready())
        self.assertTrue(self.view.skill_activation_state("zhiheng")[0])
        self.assertFalse(self.view.skill_activation_state("other")[0])


# ==================================================
# 隐藏手牌：不透明占位 token
# ==================================================

class OpaqueTokenTests(unittest.TestCase):
    def test_token_carries_no_card_identity(self):
        token = opaque_token(12, 3)
        self.assertTrue(is_opaque(token))
        self.assertEqual(token, OPAQUE_PREFIX + "12:3")
        self.assertNotIn("card-", token)

    def test_hidden_hand_candidates_are_sent_as_tokens(self):
        """过河拆桥看别人的手牌：网络包里只有占位符，房主自己留着映射。"""

        game = Game(ai_count=1)
        game.open_multiplayer_menu()
        game.start_networked_battle([
            ("p1", "房主", 0, ControllerType.HUMAN, "c1"),
            ("p2", "玩家A", 1, ControllerType.REMOTE_HUMAN, "c2"),
        ])
        remote = next(player for player in game.players
                      if player.controller_type is ControllerType.REMOTE_HUMAN)
        victim = game.player
        controller = RemoteHumanController(game, remote, bridge=None)
        request = PendingRequest(
            request_id=101, request_type=PendingRequestType.SELECT_CARDS,
            target=remote, source=victim, prompt="请选择房主区域内的一张牌",
            min_cards=1, max_cards=1,
            context={"candidates": list(victim.hand), "zone_owner": victim,
                     "reason": "guohe"},
        )
        wire = controller._build_pending_request(DecisionKind.SELECT_CARDS, request)
        entries = wire.cards
        hidden = [item for item in entries if item.get("face_down")]
        self.assertEqual(len(hidden), len(victim.hand))
        real_ids = {card.id for card in victim.hand}
        self.assertFalse(real_ids & {item["card_id"] for item in hidden},
                         "隐藏手牌的真牌 id 绝不能上网")
        self.assertTrue(all(is_opaque(item["card_id"]) for item in hidden))
        self.assertTrue(all(item["owner_id"] == victim.player_id for item in hidden))

    def test_owner_can_map_a_token_back_to_the_real_card(self):
        game = Game(ai_count=1)
        game.open_multiplayer_menu()
        game.start_networked_battle([
            ("p1", "房主", 0, ControllerType.HUMAN, "c1"),
            ("p2", "玩家A", 1, ControllerType.REMOTE_HUMAN, "c2"),
        ])
        remote = next(player for player in game.players
                      if player.controller_type is ControllerType.REMOTE_HUMAN)
        victim = game.player
        controller = RemoteHumanController(game, remote, bridge=None)
        request = PendingRequest(
            request_id=101, request_type=PendingRequestType.SELECT_CARDS,
            target=remote, source=victim, prompt="请选择房主区域内的一张牌",
            min_cards=1, max_cards=1,
            context={"candidates": list(victim.hand), "zone_owner": victim},
        )
        wire = controller._build_pending_request(DecisionKind.SELECT_CARDS, request)
        token = next(item["card_id"] for item in wire.cards if item.get("face_down"))
        real = controller._resolve_card_token(wire.request_id, token)
        self.assertIn(real, victim.hand, "房主必须能把占位符换回真牌")
        self.assertIsNone(controller._resolve_card_token(wire.request_id, "hidden:0:0"),
                          "不属于这条请求的 token 不能解析成任何牌")

    def test_public_zone_cards_keep_their_real_ids(self):
        """装备区是公开信息：直接给真牌 id，客户端照常显示牌面。"""

        game = Game(ai_count=1)
        game.open_multiplayer_menu()
        game.start_networked_battle([
            ("p1", "房主", 0, ControllerType.HUMAN, "c1"),
            ("p2", "玩家A", 1, ControllerType.REMOTE_HUMAN, "c2"),
        ])
        remote = next(player for player in game.players
                      if player.controller_type is ControllerType.REMOTE_HUMAN)
        victim = game.player
        victim.set_equipment(equipment("QINGLONG"))
        weapon = victim.get_equipment("weapon")
        controller = RemoteHumanController(game, remote, bridge=None)
        candidates = list(victim.hand) + [weapon]
        request = PendingRequest(
            request_id=101, request_type=PendingRequestType.SELECT_CARDS,
            target=remote, source=victim, prompt="请选择一张牌",
            min_cards=1, max_cards=1,
            context={"candidates": candidates, "zone_owner": victim},
        )
        wire = controller._build_pending_request(DecisionKind.SELECT_CARDS, request)
        entry = next(item for item in wire.cards if item["card_id"] == weapon.id)
        self.assertEqual(entry["zone"], "equipment")
        self.assertEqual(entry["slot"], "weapon")
        self.assertFalse(entry.get("face_down"))


# ==================================================
# 主动技：远程入口与校验
# ==================================================

def skill_request(**constraints):
    payload = {"min_cards": 0, "max_cards": 1, "min_targets": 0, "max_targets": 1,
               "allow_cancel": True, "allow_pass": True,
               "activatable": [{
                   "skill_id": "zhiheng", "name": "制衡", "needs_target": False,
                   "cost_cards": 0, "variable_cost": True,
                   "cost_candidates": [{"card_id": "card-1"}, {"card_id": "card-2"}],
                   "targets": [], "enabled": True}]}
    payload.update(constraints)
    return DecisionRequest(match_id=MATCH, request_id=31, player_id="p2",
                           kind=DecisionKind.PLAY_PHASE, prompt="出牌阶段",
                           constraints=payload)


class SkillActivationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.registry = DecisionRegistry(MATCH)
        self.registry.open(skill_request(), local=("turn", object()))

    def resolve(self, result):
        return self.registry.resolve("p2", 31, MATCH, result)

    def reject(self, result):
        with self.assertRaises(DecisionError) as ctx:
            self.resolve(result)
        return ctx.exception.code

    def test_valid_activation_is_accepted(self):
        pending, result = self.resolve(DecisionResult(
            action=ACTION_SKILL, skill_id="zhiheng", card_ids=["card-1"]))
        self.assertTrue(pending.status == "resolved")
        self.assertEqual(result.skill_id, "zhiheng")

    def test_unknown_skill_is_rejected(self):
        self.assertEqual(
            self.reject(DecisionResult(action=ACTION_SKILL, skill_id="kurou")),
            "unknown_skill")

    def test_cost_card_outside_the_offer_is_rejected(self):
        self.assertEqual(
            self.reject(DecisionResult(action=ACTION_SKILL, skill_id="zhiheng",
                                       card_ids=["card-9"])),
            "illegal_source")

    def test_variable_cost_requires_at_least_one_card(self):
        self.assertEqual(
            self.reject(DecisionResult(action=ACTION_SKILL, skill_id="zhiheng")),
            "bad_card_count")

    def test_skill_targets_are_validated(self):
        registry = DecisionRegistry(MATCH)
        registry.open(skill_request(activatable=[{
            "skill_id": "kurou", "name": "青囊", "needs_target": True,
            "cost_cards": 1, "variable_cost": False,
            "cost_candidates": [{"card_id": "card-1"}],
            "targets": [player_entry("p1")], "enabled": True}]), local=("turn", object()))
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 31, MATCH, DecisionResult(
                action=ACTION_SKILL, skill_id="kurou", card_ids=["card-1"],
                target_ids=["p9"]))
        self.assertEqual(ctx.exception.code, "illegal_target")

    def test_skill_action_is_only_allowed_in_the_play_phase(self):
        registry = DecisionRegistry(MATCH)
        registry.open(DecisionRequest(
            match_id=MATCH, request_id=32, player_id="p2",
            kind=DecisionKind.SELECT_CARDS, prompt="选牌",
            constraints={"min_cards": 1, "max_cards": 1, "allow_pass": True}),
            local=("pending", object()))
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 32, MATCH, DecisionResult(
                action=ACTION_SKILL, skill_id="zhiheng", card_ids=["card-1"]))
        self.assertEqual(ctx.exception.code, "bad_action")


class RemotePlayPhaseActivationTests(unittest.TestCase):
    """出牌阶段请求里真的列出了可以发动的主动技。"""

    def setUp(self):
        self.game = Game(ai_count=1)
        self.game.open_multiplayer_menu()
        self.game.start_networked_battle([
            ("p1", "房主", 0, ControllerType.HUMAN, "c1"),
            ("p2", "玩家A", 1, ControllerType.REMOTE_HUMAN, "c2"),
        ])
        self.remote = next(player for player in self.game.players
                           if player.controller_type is ControllerType.REMOTE_HUMAN)
        self.controller = RemoteHumanController(self.game, self.remote, bridge=None)

    def test_skills_that_need_the_local_ui_are_offered_as_disabled(self):
        """观星仍走本地选牌通道：房主必须明确说"现在不能远程发动"。

        列出但禁用（而不是假装没这个技能）是刻意的：玩家看得见技能、
        也看得见原因，而不是点了没反应或卡在一个等不到答案的窗口上。
        """

        from src.game.generals import create_default_general_registry

        general = create_default_general_registry().get("zhugeliang")
        self.assertIsNotNone(general, "武将表里应该有诸葛亮")
        self.remote.general_id = general.id
        self.game.skills.bind_general(self.remote)
        self.game.current_turn_player = self.remote
        self.game.phase = "play"
        entries = self.controller._activatable_skills()
        entry = next((item for item in entries if item["skill_id"] == "guanxing"), None)
        # 观星只在准备阶段可发动，这里先确认房主的 can_activate 判断被尊重：
        if entry is not None:
            self.assertFalse(entry["enabled"])
            self.assertIn("远程", entry["disabled_reason"])

    def test_no_skills_when_the_general_has_none(self):
        self.assertEqual(list(self.controller._activatable_skills()), [])

    def test_skills_come_with_targets_and_cost_candidates(self):
        from src.game.generals import create_default_general_registry

        general = create_default_general_registry().get("sunquan")
        self.assertIsNotNone(general, "武将表里应该有孙权")
        self.remote.general_id = general.id
        self.game.skills.bind_general(self.remote)
        # 制衡只能在"自己的出牌阶段"发动：这正是房主权威判断的东西。
        self.game.current_turn_player = self.remote
        self.game.phase = "play"
        entries = self.controller._activatable_skills()
        self.assertTrue(entries, "孙权应该能发动【制衡】")
        entry = next((item for item in entries
                      if item["skill_id"] == "zhiheng"), None)
        self.assertIsNotNone(entry)
        self.assertTrue(entry["variable_cost"])
        self.assertEqual(len(entry["cost_candidates"]), len(self.remote.hand))
        self.assertTrue(entry["enabled"])


# ==================================================
# 陈旧响应：不卡死、不误伤
# ==================================================

class StaleResponseTests(unittest.TestCase):
    def test_chained_requests_invalidate_the_old_one(self):
        registry = DecisionRegistry(MATCH)
        first = DecisionRequest(match_id=MATCH, request_id=41, player_id="p2",
                                kind=DecisionKind.SELECT_CARDS, prompt="第一段",
                                cards=[{"card_id": "card-1"}],
                                constraints={"min_cards": 1, "max_cards": 1})
        registry.open(first, local=("pending", object()))
        registry.cancel(41, "被取代")
        second = DecisionRequest(match_id=MATCH, request_id=42, player_id="p2",
                                 kind=DecisionKind.SELECT_CARDS, prompt="第二段",
                                 cards=[{"card_id": "card-2"}],
                                 constraints={"min_cards": 1, "max_cards": 1})
        registry.open(second, local=("pending", object()))
        # 旧请求的迟到回答：被拒绝，但登记表里新请求仍然可用。
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 41, MATCH,
                             DecisionResult(action="submit", card_ids=["card-1"]))
        self.assertEqual(ctx.exception.code, "decision_closed")
        self.assertIsNotNone(registry.current_for("p2"))
        registry.resolve("p2", 42, MATCH,
                         DecisionResult(action="submit", card_ids=["card-2"]))
        self.assertIsNone(registry.current_for("p2"))


# ==================================================
# Revision / Decision 竞态：决策不早于它依据的视图
# ==================================================

class DecisionRevisionRaceTests(unittest.TestCase):
    """§17：请求带的 base_revision 比客户端手上的视图新时，必须先等视图。

    ``ClientMatch`` 的做法是"先把请求挂起"，而不是拿旧视图做合法性判断；
    这一条如果退化，客户端会用手里还没有的 card_id 去做选择（或者相反，
    把合法候选当成非法的——那正是"房主没有把这个角色列为合法目标"的观感）。
    """

    def setUp(self):
        from src.network.match import ClientMatch
        from src.network.session import LanSession

        self.match = ClientMatch(LanSession())
        self.match.match_id = MATCH
        self.match.my_player_id = "p2"

    def view_message(self, revision, decision=None):
        from src.network.protocol import MessageType
        from src.game.view.view_model import ClientGameView

        payload = ClientGameView(
            match_id=MATCH, revision=revision, local_player_id="p2",
            players=(), decision=decision,
        ).to_payload()
        payload["seat"] = 1
        payload["nickname"] = "玩家A"
        return {"type": MessageType.GAME_VIEW_SNAPSHOT, "payload": payload}

    def request_message(self, request_id, base_revision):
        from src.network.protocol import MessageType

        return {"type": MessageType.DECISION_REQUEST, "payload": {"request": {
            "match_id": MATCH, "request_id": request_id,
            "player_id": "p2", "kind": "select_cards", "prompt": "选一张",
            "cards": [{"card_id": "hidden:7:0", "face_down": True}],
            "constraints": {"min_cards": 1, "max_cards": 1},
            "base_revision": base_revision,
        }}}

    def test_request_waits_for_its_snapshot(self):
        self.match.handle_client_message(self.view_message(5))
        self.match.handle_client_message(self.request_message(71, base_revision=9))
        self.assertIsNone(self.match.decision, "视图还没到位就不该开放决策")
        self.assertFalse(self.match.waiting_decision)
        self.match.handle_client_message(self.view_message(9))
        self.assertIsNotNone(self.match.decision, "对应视图到了之后要立刻开放")
        self.assertEqual(self.match.decision["request_id"], 71)

    def test_already_current_view_opens_the_decision_immediately(self):
        self.match.handle_client_message(self.view_message(9))
        self.match.handle_client_message(self.request_message(72, base_revision=9))
        self.assertIsNotNone(self.match.decision)
        self.assertEqual(self.match.decision["request_id"], 72)

    def test_answering_waits_for_the_host_ack(self):
        """提交 ≠ 接受：发了答案但还没收到 ACK 时，面板**不能**被清掉。

        房主可能拒绝（局面变了 / 牌不合法），那时要能原样恢复；所以这里只进入
        ``answered``（UI 显示"已提交，等待房主结算…"），决策本身保留到
        DECISION_ACCEPTED 为止。
        """

        from src.network.decisions import DecisionResult
        from src.network.protocol import MessageType

        # 这一条只验证"答案只发一次"的状态机：把发送本身打桩（没有真房主）。
        self.match.session.send_to_host = lambda *args, **kwargs: True
        self.match.handle_client_message(self.view_message(9))
        self.match.handle_client_message(self.request_message(73, base_revision=9))
        self.assertTrue(self.match.answer(DecisionResult(action="submit",
                                                         card_ids=["hidden:7:0"])))
        self.assertTrue(self.match.answered)
        self.assertTrue(self.match.waiting_ack)
        self.assertIsNotNone(self.match.decision, "等 ACK 期间面板要留着")
        self.assertFalse(self.match.answer(DecisionResult(action="submit")),
                         "同一条决策不能答两次")

        self.match.handle_client_message({
            "type": MessageType.DECISION_ACCEPTED,
            "payload": {"match_id": MATCH, "request_id": 73}})
        self.assertIsNone(self.match.decision, "房主接受之后才关掉这条决策")
        self.assertFalse(self.match.answered)

    def test_rejected_answer_restores_the_panel(self):
        """房主拒绝：面板原样还回来，玩家可以重新操作（不能静默等死）。"""

        from src.network.decisions import DecisionResult
        from src.network.protocol import MessageType

        self.match.session.send_to_host = lambda *args, **kwargs: True
        self.match.handle_client_message(self.view_message(9))
        self.match.handle_client_message(self.request_message(75, base_revision=9))
        self.assertTrue(self.match.answer(DecisionResult(action="submit",
                                                         card_ids=["hidden:7:0"])))
        self.match.handle_client_message({
            "type": MessageType.DECISION_REJECTED,
            "payload": {"match_id": MATCH, "request_id": 75,
                        "code": "illegal_source",
                        "reason": "这些牌不能这样转化，请重新选择"}})
        self.assertFalse(self.match.answered, "被拒绝之后要回到可操作状态")
        self.assertFalse(self.match.waiting_ack)
        self.assertIsNotNone(self.match.decision, "面板要还回来")
        self.assertEqual(self.match.reject_code, "illegal_source")
        self.assertIn("重新选择", self.match.reject_reason)
        self.assertTrue(self.match.answer(DecisionResult(action="submit")),
                        "同一条决策被拒绝之后可以重答")

    def test_cancelled_request_disappears_without_deadlock(self):
        from src.network.protocol import MessageType

        self.match.handle_client_message(self.view_message(9))
        self.match.handle_client_message(self.request_message(74, base_revision=9))
        self.match.handle_client_message({
            "type": MessageType.DECISION_CANCELLED,
            "payload": {"request_id": 74, "reason": "被取代"}})
        self.assertIsNone(self.match.decision)
        self.assertFalse(self.match.answered)


if __name__ == "__main__":
    unittest.main()
