"""Phase 14：规则状态 ↔ 客户端视图一致性（任务 B）。

公共工具在 ``tools/view_consistency.py``。每个场景都同时检查四件事：

1. **房主权威状态**：直接读 ``host.game``（装备槽、手牌、弃牌堆、战报）；
2. **某玩家应见的合法视图**：``expected_for(game, player_id)`` —— 从权威状态
   加 ``visibility`` 规则**独立**算出，刻意不经过 ``build_view``；
3. **客户端真实同步 + 视图适配后的数据**：``client_state(client)`` —— 客户端
   走的是真实 socket（``LanSession.poll`` → ``ClientMatch`` →
   ``RemoteGameView``），不是把 ``build_view`` 的结果塞给它；
4. **当前请求还能不能由正确玩家继续操作**：``decision_is_operable``。

比对只覆盖同一稳定版本的语义字段（血量 / 手牌数量与内容 / 装备 / 判定区 /
身份 / 区域张数），不比动画中间帧与 revision。客户端会先落后几帧，所以用
``agree_or_fail`` 等两端到同一版本，而不是把传输延迟当成不一致。

测试级别：**IN-PROCESS**（真实 socket、真实引擎、真实 Renderer，房主与
客户端在同一个进程里）。真实双机 **PHYSICAL TWO-PC: NOT TESTED**。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_view_consistency_lan``
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from src.game.atoms_v2 import EquipCardAtom
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.invariants import assert_card_ownership
from src.network.decisions import (
    ACTION_SUBMIT,
    ERR_DUPLICATE_ANSWER,
    ERR_OPERATION_FAILED,
    DecisionKind,
    DecisionResult,
)
from src.network.protocol import MessageType
from tests.legacy_helpers import equipment, normal_sha

from tools.lan_playability_sync import UiClient, answer_via_ui, wait_decision
from tools.lan_view_harness import (
    MatchSession,
    clear_windows,
    find_card,
    force_hand,
    give_general,
    option_targets,
    start_remote_turn,
    take_card,
    wait_for,
)
from tools.view_consistency import (
    STATE_OPERABLE,
    agree_or_fail,
    assert_hidden,
    client_state,
    decision_is_operable,
    diff,
    expected_for,
    host_projection,
    pending_responsible,
)

ZHANGBA = "equipment.zhangba"
GUOHE = "GUOHE"


def _ids(cards):
    return [str(card.id) for card in cards]


def _wait(pump, predicate, timeout=10.0):
    return wait_for(pump, predicate, timeout, "")


def raw_answer(client, request_id, card_id):
    """绕过本地面板，把一条回答**直接**投回房主。

    用于模拟"迟到 / 重复的网络回答"：玩家早在面板上点过一次，消息在网络上
    耽搁了一会儿才到。走的是真实上行通道
    （``LanSession.send_to_host`` → 房主 ``_handle_response``），被考验的是
    房主自己的校验与落盘，不是测试自己的判断。
    """

    match = client.match
    return client.session.send_to_host(
        MessageType.DECISION_RESPONSE,
        match_id=match.match_id,
        request_id=int(request_id),
        player_id=match.my_player_id,
        result=DecisionResult(action=ACTION_SUBMIT,
                              card_ids=[card_id]).to_payload(),
    )


class LanViewCase(unittest.TestCase):
    """共用脚手架：开一局、把某个远程玩家推上出牌阶段。"""

    CLIENT_COUNT = 2

    def session(self):
        return MatchSession(client_count=self.CLIENT_COUNT)

    def agree(self, session, client, player_id, *, note="", timeout=8.0, ignore=()):
        """等这份客户端到**房主当前版本**，再与该玩家应见的视图比对。

        Phase 14.1 起这一步带同步检查点：先确认客户端已应用对应 revision，
        再比语义字段（只看字段相等会在客户端落后时提前通过）。
        """

        return agree_or_fail(
            self, session.pump, session.host, client, player_id,
            timeout=timeout, note=note, ignore=ignore)

    def assert_request_ownership(self, session, *, note=""):
        """当前请求必须能由**正确的那一名**玩家继续，而不是所有人都卡着。

        没有待回答的请求时，谁也不该被挂在请求上；有请求时，只有责任人才
        算"可以继续操作"，其他人必须是"不归我"——两边判断反了都会在这里
        现形（前者是卡死，后者是责任算错人）。

        「责任」还分两种情况：引擎在等的请求算，**出牌阶段**这种网络层的
        回合决策（不是引擎 PendingRequest）也算——只按引擎请求判断会把它
        误判成"谁都不用操作"。
        """

        responsible, reason = pending_responsible(session.game)
        registry = getattr(session.host.match, "registry", None)
        for client in session.clients:
            mine = str(client.view.local_player_id)
            result = decision_is_operable(session.host, client)
            expected = bool(responsible and mine in responsible)
            if not expected and registry is not None:
                # 房主侧登记表里有没有为这名玩家打开的网络决策（出牌阶段）。
                expected = registry.current_for(mine) is not None
            # 归属（这条请求是不是他的）与实际能力（现在能不能提交）分开断言：
            # 责任算错人会在这里现形，而"还没送到 / 已提交等确认"不算责任错。
            self.assertEqual(
                result.mine, expected,
                "客户端 %s 的责任归属判断不对：%s（%s；%s）"
                % (mine, result.describe(), note, reason))
            if expected and result.state == STATE_OPERABLE:
                self.assertTrue(result.operable, "可操作状态下 operable 应为真")
        return responsible, reason


# ==================================================
# 场景 A：装备被弃置 / 被取走
# ==================================================


class EquipmentSyncTests(LanViewCase):
    """装备离场之后，房主与每一个客户端看到的是同一件事。

    已有测试（``scenario_client_guohe_equipment``）只验了房主权威那一步与
    隐藏手牌；这里补的是**客户端那一侧真的同步到了**：装备槽空了、弃牌堆
    多了那张牌、手牌张数对得上。
    """

    def test_discarded_equipment_disappears_from_every_client(self):
        with self.session() as session:
            game, pump = session.game, session.pump
            actor = session.remote(0)
            other = session.remote(1)
            victim = game.player
            actor_client = session.client_of(actor)
            other_client = session.client_of(other)

            session.fix_hands(["SHA", "TAO"], keep=actor)
            force_hand(game, actor, [GUOHE, "SHA"])
            force_hand(game, victim, ["SHA", "TAO", "SHAN"])
            game.context.apply(EquipCardAtom(victim, equipment("QINGLONG")))
            weapon = victim.get_equipment("weapon")
            self.assertIsNotNone(weapon, "装置没生效：房主武器槽是空的")

            self.agree(session, actor_client, actor.player_id,
                       note="装备还在时先对齐一次")

            start_remote_turn(session.host, pump)
            ui = UiClient(actor_client)
            kind, how = answer_via_ui(ui, pump, card_name=GUOHE,
                                      target_id=victim.player_id)
            self.assertEqual(kind, DecisionKind.PLAY_PHASE,
                             "远程玩家没能用界面打出【过河拆桥】：" + str(how))

            select = wait_decision(actor_client, pump, DecisionKind.SELECT_CARDS,
                                   timeout=12.0)
            self.assertIsNotNone(select, "没有开出『选择目标区域内的一张牌』")
            self.assertIn(weapon.id,
                          {item.get("card_id") for item in select.get("cards", ())
                           if not item.get("face_down")},
                          "装备区的牌没有作为真实候选下发")
            self.assertTrue(ui.click_pool_card(weapon.id),
                            "客户端界面上点不到那件装备")

            # ---- 1. 房主权威状态 ----
            self.assertTrue(_wait(pump, lambda: victim.get_equipment("weapon") is None),
                            "房主权威：装备槽没有清空")
            self.assertTrue(_wait(
                pump, lambda: any(card is weapon for card in game.deck.discard_pile)),
                "房主权威：被拆掉的装备没有进弃牌堆")
            self.assertNotIn(weapon, victim.hand,
                             "同一张牌不该又回到手牌里")
            self.assertFalse(session.host.match.registry.rejected,
                             "房主拒绝了这条合法响应："
                             + str(session.host.match.registry.rejected[-2:]))

            # ---- 2/3. 两个客户端都和"应见视图"一致 ----
            self.agree(session, actor_client, actor.player_id,
                       note="拆掉装备之后（行动方视角）")
            self.agree(session, other_client, other.player_id,
                       note="拆掉装备之后（旁观方视角）")

            # 客户端侧的显式事实（不只依赖整体比对）
            state = client_state(other_client)
            self.assertEqual(state["players"][victim.player_id]["equipment"], {},
                             "旁观客户端的装备区没有跟着空掉")
            self.assertEqual(state["discard_count"], len(game.deck.discard_pile))

            # ---- 隐藏信息按玩家视角 ----
            assert_hidden(self, other_client, victim.player_id, _ids(victim.hand))
            assert_hidden(self, other_client, actor.player_id, _ids(actor.hand))

            clear_windows(session.host, session.clients, pump)

    def test_taken_equipment_lands_in_the_taker_hand_on_both_sides(self):
        """【顺手牵羊】把装备取走：拿牌的人手牌 +1，原主人装备槽清空。"""

        with self.session() as session:
            game, pump = session.game, session.pump
            actor = session.remote(0)
            victim = game.player
            actor_client = session.client_of(actor)

            session.fix_hands(["SHA", "TAO"], keep=actor)
            force_hand(game, actor, ["SHUNSHOU", "SHA"])
            force_hand(game, victim, ["SHA", "TAO", "SHAN"])
            game.context.apply(EquipCardAtom(victim, equipment("QINGLONG")))
            weapon = victim.get_equipment("weapon")
            hand_before = len(actor.hand)

            start_remote_turn(session.host, pump)
            ui = UiClient(actor_client)
            answer_via_ui(ui, pump, card_name="SHUNSHOU", target_id=victim.player_id)
            select = wait_decision(actor_client, pump, DecisionKind.SELECT_CARDS,
                                   timeout=12.0)
            self.assertIsNotNone(select, "没有开出『选择目标区域内的一张牌』")
            self.assertTrue(ui.click_pool_card(weapon.id),
                            "客户端界面上点不到那件装备")

            # ---- 1. 房主权威状态 ----
            self.assertTrue(_wait(pump,
                                  lambda: victim.get_equipment("weapon") is None),
                            "房主权威：原主人的装备槽没有清空")
            self.assertTrue(_wait(
                pump, lambda: any(card is weapon for card in actor.hand)),
                "房主权威：装备没有进到拿牌人手里")
            self.assertNotIn(weapon, game.deck.discard_pile)
            # 打出一张【顺手牵羊】、又拿回一张装备：手牌张数净不变。
            self.assertEqual(len(actor.hand), hand_before,
                             "拿牌人的手牌张数不对（原来 %d 张）" % hand_before)
            self.assertFalse(any(card.name == "SHUNSHOU" for card in actor.hand),
                             "打出的【顺手牵羊】还留在手里")

            # ---- 2/3. 客户端与应见视图一致 ----
            self.agree(session, actor_client, actor.player_id,
                       note="顺手牵羊拿走装备之后")

            state = client_state(actor_client)
            mine = state["players"][actor.player_id]
            self.assertEqual(mine["hand_count"], len(actor.hand))
            self.assertIn(weapon.id, mine["hand_ids"],
                          "拿牌人自己的客户端看不到刚到手的装备")
            self.assertEqual(state["players"][victim.player_id]["equipment"], {},
                             "原主人的装备区没有清空")

            # ---- 4. 现在还有没有人被卡在等待里 ----
            self.assert_request_ownership(session, note="顺手牵羊结算之后")

            clear_windows(session.host, session.clients, pump)


# ==================================================
# 场景 B：丈八蛇矛转化
# ==================================================


class ZhangbaConversionSyncTests(LanViewCase):
    """丈八蛇矛：「两张手牌当【杀】」的取消与成功，两侧都要对得上。

    已有测试（``test_engine_v2_phase11_7_zhangba_view_as``）验了房主侧的
    ``sha_used`` 与战报；这里补三件：

    * **取消**：没提交就什么都没发生——两张素材还在原位置（客户端与房主一致）；
    * **只结算一次**：两张素材各自只在弃牌堆里出现一次，且同一步里没有
      "既在手牌又在别处"的重复归属（全程开着牌唯一归属检查）；
    * **客户端显示正确**：手牌内容、张数与弃牌堆张数三侧一致。
    """

    def _equip_spear(self, game, player):
        game.context.apply(EquipCardAtom(player, equipment("ZHANGBA")))
        return player.get_equipment("weapon")

    def test_cancelling_keeps_both_materials_in_place(self):
        with self.session() as session:
            game, pump = session.game, session.pump
            remote = session.remote(0)
            client = session.client_of(remote)
            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "TAO"])
            spear = self._equip_spear(game, remote)
            discard_before = len(game.deck.discard_pile)

            start_remote_turn(session.host, pump)
            ui = UiClient(client)
            self.assertTrue(_wait(
                pump, lambda: client.match is not None
                and client.match.decision is not None and not client.match.answered),
                "游客没有收到出牌阶段面板")
            request = client.match.decision
            card, option = find_card(request, skill_id=ZHANGBA)
            self.assertIsNotNone(option, "房主没有下发丈八的转化方式")

            hand_ids = [str(item["card_id"]) for item in request["cards"]
                        if item.get("name") in ("SHA", "TAO")]
            self.assertEqual(len(hand_ids), 2)

            rects = ui.renderer.player_equipment_slot_rects(ui.view)
            ui.click(rects["weapon"].center)
            ui.click_hand_card(hand_ids[0])
            pump(0.4)
            self.assertFalse(client.match.answered, "只选一张就提交了")

            # 再点一次武器 = 取消这次转化发动（与单机同一条路径）。
            ui.click(rects["weapon"].center)
            pump(0.8)

            # ---- 1. 房主权威状态：什么都没动 ----
            self.assertFalse(client.match.answered, "取消之后仍然提交了答案")
            self.assertFalse(remote.sha_used, "取消不该计入出杀次数")
            self.assertEqual(len(remote.hand), 2, "取消不该消耗素材牌")
            self.assertEqual(sorted(_ids(remote.hand)), sorted(hand_ids),
                             "取消后素材牌不在原位置")
            self.assertIs(remote.get_equipment("weapon"), spear,
                          "取消不该动武器")
            self.assertEqual(len(game.deck.discard_pile), discard_before,
                             "取消不该往弃牌堆里放牌")

            # ---- 2/3. 客户端与应见视图一致 ----
            self.agree(session, client, remote.player_id,
                       note="取消丈八转化之后")
            state = client_state(client)
            self.assertEqual(state["players"][remote.player_id]["hand_ids"],
                             tuple(hand_ids),
                             "客户端看到的手牌与取消前不一致")

            clear_windows(session.host, session.clients, pump)

    def test_two_materials_are_settled_exactly_once(self):
        with self.session() as session:
            game, pump = session.game, session.pump
            remote = session.remote(0)
            client = session.client_of(remote)
            # 全程开着牌唯一归属检查：转化结算的每一步都不许出现
            # "同一张实体牌同时属于两个牌区"。
            game.assert_card_ownership = True

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "TAO"])
            self._equip_spear(game, remote)

            start_remote_turn(session.host, pump)
            ui = UiClient(client)
            self.assertTrue(_wait(
                pump, lambda: client.match is not None
                and client.match.decision is not None and not client.match.answered),
                "游客没有收到出牌阶段面板")
            request = client.match.decision
            card, option = find_card(request, skill_id=ZHANGBA)
            self.assertIsNotNone(option)
            hand_ids = [str(item["card_id"]) for item in request["cards"]
                        if item.get("name") in ("SHA", "TAO")]

            rects = ui.renderer.player_equipment_slot_rects(ui.view)
            ui.click(rects["weapon"].center)
            ui.click_hand_card(hand_ids[0])
            pump(0.3)
            ui.click_hand_card(hand_ids[1])
            pump(0.4)

            targets = option_targets(card, option)
            self.assertTrue(targets, "丈八的【杀】没有合法目标")
            ui.click_seat(targets[0])

            self.assertTrue(_wait(
                pump, lambda: client.match.answered or client.match.decision is None,
                timeout=10.0), "选满两张 + 目标之后没有提交")
            answer = (client.match.last_answer or {}).get("result") or {}
            self.assertEqual(len(answer.get("card_ids") or ()), 2,
                             "提交的素材张数不是两张")
            self.assertEqual(answer.get("skill_id"), ZHANGBA)

            # ---- 1. 房主权威状态 ----
            self.assertTrue(_wait(pump, lambda: remote.sha_used),
                            "房主没有把这次出杀记到出杀次数上")
            self.assertTrue(_wait(pump, lambda: not session.host.match.registry.rejected),
                            "房主拒绝了合法出牌："
                            + str(session.host.match.registry.rejected[-2:]))
            pump(0.6)

            for card_id in hand_ids:
                entry = next((card for card in game.deck.discard_pile
                              if str(card.id) == card_id), None)
                self.assertIsNotNone(entry, "素材牌没有进弃牌堆：" + card_id)
                count = sum(1 for item in game.deck.discard_pile
                            if str(item.id) == card_id)
                self.assertEqual(count, 1,
                                 "素材牌在弃牌堆里被结算了 %d 次：%s" % (count, card_id))
                self.assertNotIn(card_id, _ids(remote.hand),
                                 "素材牌还在手里：" + card_id)
            # 两张素材是**两张不同的实体牌**，不是同一张被算了两遍。
            self.assertNotEqual(hand_ids[0], hand_ids[1])
            self.assertTrue(any("丈八蛇矛" in line for line in game.game_log),
                            "房主的战报里没有这次转化")

            # ---- 2/3. 客户端与应见视图一致（含弃牌堆张数） ----
            self.agree(session, client, remote.player_id,
                       note="丈八转化结算之后")
            state = client_state(client)
            self.assertEqual(state["discard_count"], len(game.deck.discard_pile),
                             "客户端看到的弃牌堆张数与房主不一致")
            self.assertEqual(state["players"][remote.player_id]["hand_count"],
                             len(remote.hand))

            clear_windows(session.host, session.clients, pump)


# ==================================================
# 场景 C：悲歌嵌套响应
# ==================================================


class NestedRequestSyncTests(LanViewCase):
    """一次【杀】伤害触发两条【悲歌】，外层请求被内层覆盖。

    两名远程角色都持有【悲歌】：伤害结算后各起一条 ``BeigeFlow``，于是引擎
    的请求栈是 ``[外层(先绑定的那位), 内层(后绑定的那位)]``，栈顶是内层。

    已有测试 ``test_beige_request_stack.py`` 在**单机**下验过"覆盖后恢复"。
    这里补的是真实网络侧的三件事：

    * 被覆盖期间对**外层**的回答必须被拒绝，且不能扣掉任何一张牌；
    * 内层答完之后外层必须**恢复**给正确的那名玩家（他能继续操作）；
    * 他答完之后结算真的收尾，两张弃牌各只发生一次，两侧视图一致。

    级联语义由规则层与 ``GameEngine._drive_pending_front`` 提供，本文件只
    观测，不 mock 任何结算。
    """

    def _beige_pair(self, session):
        """装好两名远程蔡文姬并打一次【杀】伤害，返回 (外层, 内层) 与两侧客户端。"""

        game, pump = session.game, session.pump
        first, second = session.remote(0), session.remote(1)
        first_client = session.client_of(first)
        second_client = session.client_of(second)
        # 先绑定的那位拿外层请求（技能按座次订阅 DAMAGE_SETTLED）。
        give_general(game, first, "caiwenji")
        give_general(game, second, "sp_caiwenji")
        force_hand(game, first, ["SHA", "TAO"])
        force_hand(game, second, ["SHA", "TAO"])
        force_hand(game, game.player, ["SHA", "TAO"])
        pump(0.4)

        DamageFlow(game.engine, DamageContext(
            source=game.player, target=first, amount=1,
            card=normal_sha())).start()

        self.assertTrue(_wait(pump, lambda: len(game.engine.pending._stack) == 2,
                              timeout=10.0),
                        "没有形成「外层 + 内层」的嵌套请求栈："
                        + str([item.request_id
                               for item in game.engine.pending._stack]))
        outer, inner = game.engine.pending._stack
        # 两条请求都要真的下发到各自的客户端，面板才谈得上"点得动"。
        self.assertTrue(_wait(
            pump,
            lambda: all(client.match is not None
                        and client.match.decision is not None
                        and not client.match.answered
                        for client in (first_client, second_client)),
            timeout=12.0),
            "嵌套请求没有下发到客户端："
            + str([(client.view.local_player_id if client.view else "?",
                    (client.match.decision or {}).get("request_id")
                    if client.match else None)
                   for client in (first_client, second_client)]))
        return (outer, inner, first, second, first_client, second_client)

    def test_stale_answer_is_rejected_then_the_owner_finishes_it(self):
        with self.session() as session:
            game, pump = session.game, session.pump
            game.assert_card_ownership = True

            (outer, inner, first, second,
             first_client, second_client) = self._beige_pair(session)

            # ---- 结构：外层属于先绑定的那位，内层在栈顶 ----
            self.assertIs(outer.target, first)
            self.assertIs(inner.target, second)
            self.assertEqual(pending_responsible(game)[0], [second.player_id],
                             "栈顶应当等内层的持有者")
            self.assertNotEqual(outer.request_id, inner.request_id)

            candidates = [str(item["card_id"]) for item in
                          (first_client.match.decision or {}).get("cards", ())]
            self.assertTrue(candidates, "外层持有者的客户端没有候选牌")
            stale_card = candidates[0]

            # ---- 1. 被覆盖期间回答外层：必须被拒绝，且不扣牌 ----
            rejected_before = len(session.host.match.registry.rejected)
            raw_answer(first_client, outer.request_id, stale_card)
            self.assertTrue(_wait(
                pump,
                lambda: len(session.host.match.registry.rejected) > rejected_before,
                timeout=8.0),
                "被覆盖的请求竟然被接受了（房主没有拒绝这条过期回答）")
            rejection = session.host.match.registry.rejected[-1]
            self.assertEqual(rejection.get("player_id"), first.player_id)
            self.assertEqual(int(rejection.get("request_id")), outer.request_id)
            self.assertEqual(rejection.get("code"), ERR_OPERATION_FAILED,
                             "拒绝码不是「校验过了但没能落到游戏上」：" + str(rejection))
            # 关键：这张牌还在原位置（过期回答没有造成任何扣牌）。
            self.assertIn(stale_card, _ids(first.hand),
                          "过期回答把候选牌扣掉了")
            self.assertEqual(len(game.engine.pending._stack), 2,
                             "过期回答改变了请求栈")
            # 客户端也被明确告知，不是静默等待。
            self.assertTrue(_wait(pump, lambda: first_client.match.reject_code),
                            "客户端没有收到拒绝说明（会变成静默等待）")
            self.assertFalse(first_client.match.answered,
                             "被拒绝之后客户端仍然处于已提交状态")

            # ---- 2. 内层答完：外层恢复给正确的那名玩家 ----
            inner_card = [str(item["card_id"]) for item in
                          (second_client.match.decision or {}).get("cards", ())][0]
            answer_via_ui(UiClient(second_client), pump, card_id=inner_card)

            self.assertTrue(_wait(
                pump, lambda: [item.request_id
                               for item in game.engine.pending._stack]
                == [outer.request_id], timeout=12.0),
                "内层答完之后外层没有被恢复："
                + str([item.request_id for item in game.engine.pending._stack]))
            state = decision_is_operable(session.host, first_client)
            self.assertTrue(state.operable,
                            "外层恢复之后正确玩家仍然不能操作：state=%s，%s"
                            % (state.state, state.note))
            self.assertEqual(state.state, STATE_OPERABLE)
            # 责任是唯一的：此刻等的是外层持有者，不是另一个人。
            responsible, _reason = self.assert_request_ownership(
                session, note="内层答完、外层恢复之后")
            self.assertEqual(responsible, [first.player_id],
                             "外层恢复之后房主等错了人")

            # ---- 3. 正确玩家答完：结算收尾，两张弃牌各只发生一次 ----
            discard_before = len(game.deck.discard_pile)
            answer_via_ui(UiClient(first_client), pump, card_id=stale_card)
            self.assertTrue(_wait(pump, lambda: not game.engine.pending.active,
                                  timeout=12.0),
                            "外层回答之后结算没有收尾："
                            + str([item.request_id
                                   for item in game.engine.pending._stack]))
            self.assertNotIn(stale_card, _ids(first.hand), "外层弃的牌还在手里")
            self.assertEqual(
                sum(1 for card in game.deck.discard_pile
                    if str(card.id) == stale_card), 1,
                "外层弃的牌在弃牌堆里出现了不止一次")
            self.assertGreater(len(game.deck.discard_pile), discard_before)
            self.assertTrue(any("悲歌" in line for line in game.game_log),
                            "战报里没有这次【悲歌】：" + str(game.game_log[-4:]))

            # ---- 4. 两侧视图一致 + 全程没有重复归属 ----
            self.agree(session, first_client, first.player_id,
                       note="嵌套悲歌结算之后（外层持有者）")
            self.agree(session, second_client, second.player_id,
                       note="嵌套悲歌结算之后（内层持有者）")
            assert_card_ownership(game)

            clear_windows(session.host, session.clients, pump)

    def test_answering_a_resolved_request_again_is_rejected(self):
        """结算完再补一条重复回答：登记表直接拒（不是"看起来成功"）。"""

        with self.session() as session:
            game, pump = session.game, session.pump
            (outer, inner, first, second,
             first_client, second_client) = self._beige_pair(session)

            first_card = [str(item["card_id"]) for item in
                          (first_client.match.decision or {}).get("cards", ())][0]
            second_card = [str(item["card_id"]) for item in
                           (second_client.match.decision or {}).get("cards", ())][0]

            # 先把栈清干净（内层 → 外层），让两条请求都进入"已解决"。
            answer_via_ui(UiClient(second_client), pump, card_id=second_card)
            self.assertTrue(_wait(
                pump, lambda: len(game.engine.pending._stack) == 1, timeout=12.0),
                "内层没有结算")
            answer_via_ui(UiClient(first_client), pump, card_id=first_card)
            self.assertTrue(_wait(pump, lambda: not game.engine.pending.active,
                                  timeout=12.0), "外层没有结算")

            hand_before = _ids(first.hand)
            discard_before = len(game.deck.discard_pile)
            raw_answer(first_client, outer.request_id, first_card)
            self.assertTrue(_wait(
                pump,
                lambda: any(item.get("code") == ERR_DUPLICATE_ANSWER
                            for item in session.host.match.registry.rejected),
                timeout=8.0),
                "重复回答没有被拒绝："
                + str(session.host.match.registry.rejected[-3:]))
            self.assertEqual(_ids(first.hand), hand_before, "重复回答扣了牌")
            self.assertEqual(len(game.deck.discard_pile), discard_before,
                             "重复回答改了弃牌堆")

            clear_windows(session.host, session.clients, pump)


# ==================================================
# 可见性规则的双向核验
# ==================================================


class VisibilityCrossCheckTests(LanViewCase):
    """同一帧的权威状态，按每个玩家视角各核一遍。

    两侧期望值是**两套独立表述**：``expected_for`` 直接读权威 ``Game`` 加
    ``visibility`` 规则；``host_projection`` 走房主真正下发的 ``build_view``。
    两边对不上就说明可见性规则在其中一处走了样（例如别人的手牌被发出去、
    隐藏身份提前泄底、装备区少了一张）。

    客户端那一侧另外单独查（``client_state``），所以这里不构成"自己跟自己比"。
    """

    def test_every_viewer_sees_exactly_what_the_rules_allow(self):
        with self.session() as session:
            game, pump = session.game, session.pump
            first, second = session.remote(0), session.remote(1)
            session.fix_hands(["SHA"], keep=first)
            force_hand(game, first, ["SHA", "TAO"])
            force_hand(game, second, ["SHAN", "SHAN"])
            game.context.apply(EquipCardAtom(game.player, equipment("QINGLONG")))
            game.context.apply(EquipCardAtom(first, equipment("BAGUA")))
            pump(0.6)

            viewers = [game.player] + [first, second]
            for viewer in viewers:
                expected = expected_for(game, viewer.player_id)
                projection = host_projection(session.host, viewer.player_id)
                problems = diff(expected, projection)
                self.assertEqual(
                    problems, [],
                    "玩家 %s 的权威投影与可见性规则不一致：\n  %s"
                    % (viewer.player_id, "\n  ".join(problems)))

                # 手牌内容只对本人出现：别人那一格必须是"没有内容"。
                for player in viewers:
                    entry = projection["players"][player.player_id]
                    if player is viewer:
                        self.assertEqual(
                            entry["hand_ids"],
                            tuple(str(card.id) for card in player.hand),
                            "本人看不到自己的手牌")
                    else:
                        self.assertIsNone(
                            entry["hand_ids"],
                            "玩家 %s 的投影里出现了 %s 的手牌内容"
                            % (viewer.player_id, player.player_id))
                        self.assertEqual(entry["hand_count"], len(player.hand),
                                         "别人手牌的张数不对")

    def test_remote_clients_match_the_rules_for_their_own_viewpoint(self):
        with self.session() as session:
            game, pump = session.game, session.pump
            first, second = session.remote(0), session.remote(1)
            session.fix_hands(["SHA"], keep=first)
            force_hand(game, first, ["SHA", "TAO"])
            force_hand(game, second, ["SHAN", "SHAN"])
            pump(0.6)

            for player in (first, second):
                client = session.client_of(player)
                self.agree(session, client, player.player_id,
                           note="静态局面下的视角一致性")

            # 两份客户端的手牌**必须不同**：各自只看得见自己那一份。
            first_state = client_state(session.client_of(first))
            second_state = client_state(session.client_of(second))
            self.assertNotEqual(
                first_state["players"][first.player_id]["hand_ids"],
                second_state["players"][first.player_id]["hand_ids"],
                "两份客户端拿到了同一份手牌内容（隐藏信息没有按视角过滤）")
            assert_hidden(self, session.client_of(second), first.player_id,
                          _ids(first.hand))
            assert_hidden(self, session.client_of(first), second.player_id,
                          _ids(second.hand))


    def test_public_pool_is_identical_for_every_viewer(self):
        """【五谷丰登】摊在公共池里的牌是**公开信息**：谁看到的都得是同一份。

        与上面的隐藏手牌正好相反：手牌按视角过滤，公共池必须一致。这一条如果
        被"按视角过滤"过度执行（连公开区域也藏），客户端就会看不到自己该拿的
        那一张——那正是五谷选不了牌的观感。
        """

        with self.session() as session:
            game, pump = session.game, session.pump
            first, second = session.remote(0), session.remote(1)
            session.fix_hands(["SHA"], keep=first)
            force_hand(game, first, ["TAO", "TAO"])
            force_hand(game, second, ["SHAN", "SHAN"])

            # 规则层的真实落点：把两张实体牌从牌堆挪进公共池
            # （【五谷丰登】走的就是 ``public_card_pool``）。
            pool = [take_card(game, "TAO"), take_card(game, "SHAN")]
            game.public_card_pool[:] = pool
            wanted = tuple(str(card.id) for card in pool)
            pump(0.8)

            for player in (first, second):
                client = session.client_of(player)
                self.agree(session, client, player.player_id,
                           note="公共池摊开之后")
                state = client_state(client)
                self.assertEqual(state["public_pool"], wanted,
                                 "玩家 %s 看到的公共池不对" % player.player_id)
                self.assertEqual(state["draw_pile_count"],
                                 len(game.deck.draw_pile),
                                 "牌堆张数不对（两张牌已经摊到公共池）")

            # 公共池两边一致，但两个人的手牌仍然互不可见。
            assert_hidden(self, session.client_of(second), first.player_id,
                          _ids(first.hand))
            self.assertNotEqual(
                client_state(session.client_of(first))["players"][
                    first.player_id]["hand_ids"],
                client_state(session.client_of(second))["players"][
                    first.player_id]["hand_ids"],
                "两份客户端拿到了同一份手牌内容")


if __name__ == "__main__":
    unittest.main()
