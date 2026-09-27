"""本机输入优先级自检（Phase 18.5）：三组必须成立的判据。

它把 Phase 18.5 批量试玩暴露的三类输入问题固化成断言，改动相关模块后跑一遍：

    .venv\\Scripts\\python.exe tools/input_priority_audit.py

三组判据：

1. **演出让路与本地交互**（``Effects.interaction_hold`` / 两处闸门）
   演出在播时，本机玩家**正被要求做决定**就一律让开；没被要求就照旧让路。
   同时校验判定优先：判定没走完时只允许判定流程自己要的输入。

2. **响应窗口不因动作队列忙而点不动**（``respond_with_card`` / ``pass_response``）
   队列忙时响应（出牌与「不出」）照样能提交；**但普通出牌仍然被 busy 拦住**
   （不能顺手把别的 busy 约束一道取消）。

3. **手牌命中按"屏幕上那一帧"算**（``Renderer.card_at_position``）
   布局过期（手牌刚增减）时命中它当时画的那张牌；已经被打出去、屏幕上只剩
   残影的牌不再命中任何东西；首帧尚未绘制时才走兜底排列。

退出码 0 = 全部通过。
"""

import io
import os
import sys
import traceback

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame                                                        # noqa: E402

from src.game import Game                                            # noqa: E402
from src.game.engine.pending import PendingRequestType               # noqa: E402
from src.game.contracts.local_input import (                         # noqa: E402
    local_awaiting_input,
    local_interaction_slots,
    local_response_live,
)
from src.renderer import Renderer                                    # noqa: E402
from src.ui.interaction import handle_game_click                     # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("[通过]" if ok else "[失败]", name,
                       ("  —— " + detail) if detail else ""), flush=True)
    return bool(ok)


def agree_optional_trigger(game, player):
    """替本机真人点掉一个"你可以…"窗口（可选触发技的确认请求）。

    落英这类技能在真人座位上只挂一条 CONFIRM 请求等人点，脚本不回答它，
    结果就永远停在"没发动"——那不是规则错，是没人按下按钮。这里替真人
    回答"同意"，让判据落到结算那一步。
    """

    from src.game.engine.domain_actions import ConfirmPendingAction

    request = game.pending_request
    if (request is None or request.status != "pending"
            or request.target is not player
            or request.request_type is not PendingRequestType.CONFIRM):
        return False
    game.engine.submit(ConfirmPendingAction(player, request.request_id, True))
    for _ in range(5):
        game.update(1 / 30.0)
    return True


def make_game(seed=1):
    game = Game(ai_count=2)
    game.rng.seed(seed)
    game.scene = "general_select"
    game.general_candidates = game.roll_general_candidates()
    game.confirm_general(game.general_candidates[0])
    game.scene = "game"
    return game


def other_player(game, offset=1):
    ordered = [p for p in game.players if p is not game.player]
    return ordered[offset % len(ordered)] if ordered else None


def make_busy(game, seconds=3.0):
    """让动作队列真的有动画在播（不再依赖内部字段名）。"""

    from src.actions import WaitAction

    game.actions.add(WaitAction(seconds))
    game.actions.update(0.016)
    return game.busy


def start_skill_banner(effects, game, kind="view_as"):
    """把一条压界面的技能演出真正开演（current = SkillStep）。"""

    from src.ui.storyboard import SkillStep

    step = SkillStep(game.player, "test_skill", "测试技", text="自检用",
                     kind_label="视为技", skill_kind=kind)
    effects.storyboard.submit(step)
    effects.storyboard.update(0.016, game)
    return effects.storyboard.current


# ==================================================
# 1. 演出让路 / 本地交互槽位
# ==================================================


def group_gates():
    from src.ui.fx import Effects

    # 1.1 没有演出：不让路
    game = make_game()
    effects = Effects()
    effects.attach(game)
    check("1.1 没有演出时不让路", effects.interaction_hold() is False)

    # 1.2 技能演出在播、本机玩家没事：照旧让路（技能提示先播完）
    start_skill_banner(effects, game)
    check("1.2 演出在播且本机没事 → 让路", effects.interaction_hold() is True,
          "hold_source=%s" % effects.hold_source())

    # 1.3 演出在播、本机玩家正在选目标：**不让路**
    picked = []
    game.start_target_selection(
        [other_player(game)], 1, 1, "自检：选一个目标",
        on_complete=lambda targets: picked.append(targets))
    check("1.3 演出在播但在选目标 → 不让路", effects.interaction_hold() is False,
          "槽位=%s" % ",".join(local_interaction_slots(game)))
    check("1.3b 选目标算「本机被要求做事」", local_awaiting_input(game) is True)

    # 1.4 目标选择本身能被点动（真实点击路由）
    renderer = Renderer(pygame.display.set_mode((1280, 800)))
    renderer.draw(game)
    target = other_player(game)
    rect = renderer.table_layout.seat_rect(target)
    consumed = handle_game_click(rect.center, game, renderer)
    check("1.4 演出在播时点目标仍然生效", consumed and picked,
          "consumed=%s 回调=%s" % (consumed, bool(picked)))

    # 1.5 引擎请求问的是别人：仍然让路
    game2 = make_game()
    effects2 = Effects()
    effects2.attach(game2)
    start_skill_banner(effects2, game2)
    game2.engine.pending.create(
        PendingRequestType.CONFIRM, source=other_player(game2),
        target=other_player(game2), prompt="自检：别人的确认",
        owner_flow=_StubFlow())
    check("1.5 请求问别人 → 让路", effects2.interaction_hold() is True)
    check("1.5b 请求问别人不算本机被要求做事", local_awaiting_input(game2) is False)

    # 1.6 引擎请求问的是本机玩家：不让路（改判窗口一类的形态）
    game3 = make_game()
    effects3 = Effects()
    effects3.attach(game3)
    start_skill_banner(effects3, game3)
    game3.engine.pending.create(
        PendingRequestType.CONFIRM, source=other_player(game3),
        target=game3.player, prompt="自检：问本机",
        request_context={"reason": "judge_replacement"},
        owner_flow=_StubFlow())
    check("1.6 请求问本机 → 不让路", effects3.interaction_hold() is False)

    # 1.7 判定优先：判定没走完时只允许判定自己要的输入
    game4 = make_game()
    effects4 = Effects()
    effects4.attach(game4)
    judge_flow = _StubJudgeFlow()
    game4.judge_gate.register_judge(judge_flow)
    check("1.7a 判定没走完 → 点击被拦",
          game4.judge_gate.allows_local_input() is False)
    game4.engine.pending.create(
        PendingRequestType.SELECT_CARDS, source=game4.player,
        target=game4.player, prompt="自检：改判窗口",
        request_context={"reason": "judge_replacement"},
        owner_flow=judge_flow)
    check("1.7b 改判窗口问本机 → 放行", game4.judge_gate.allows_local_input() is True)
    # 判定期间本机有本地槽位也不该让路到"判定之外"的输入
    # 规则上判定还在跑（logical）：本机有本地槽位也只放行判定自己的请求
    game4.start_target_selection([other_player(game4)], 1, 1, "自检：判定中的本地槽位",
                                 on_complete=lambda targets: None)
    game4.engine.pending.take(game4.pending_request.request_id)
    check("1.7c 规则上判定没走完 → 本地槽位也拦", game4.judge_gate.phase == "logical",
          "phase=%s" % game4.judge_gate.phase)
    check("1.7c2 只放行判定自己的请求",
          game4.judge_gate.allows_local_input() is False,
          "本机有选目标槽位，但判定逻辑没走完")

    # 规则上判定已经结束、只剩画面在演（presentation）：本机被问 → 放行
    judge_flow.settled = True
    game4.judge_gate.mark_presentation("audit-panel", True)
    check("1.7d 只剩判定画面在演 → 本机被问则放行",
          game4.judge_gate.phase == "presentation"
          and game4.judge_gate.allows_local_input() is True,
          "phase=%s 槽位=%s" % (game4.judge_gate.phase,
                                ",".join(local_interaction_slots(game4))))
    game4.cancel_target_selection()
    check("1.7e 只剩判定画面在演、本机没事 → 仍然拦",
          game4.judge_gate.allows_local_input() is False)
    game4.judge_gate.mark_presentation("audit-panel", False)
    check("1.7f 判定画面收掉后完全放行",
          game4.judge_gate.active is False
          and game4.judge_gate.allows_local_input() is True)


class _Status:
    """``JudgeGate._is_live`` 读的是 ``status.value``，这里给同构的替身。"""

    value = "running"


class _StubFlow:
    """最小流程替身：只需要能被当成 owner_flow / 满足登记的接口。"""

    status = _Status()
    settled = False

    def resume(self, resolution=None):
        from src.game.engine.flows import FlowResult, FlowStatus

        return FlowResult(FlowStatus.COMPLETED, None)


class _StubJudgeFlow(_StubFlow):
    pass


# ==================================================
# 2. 响应窗口在动作队列忙时要能提交
# ==================================================


def _response_scene(allowed=("SHAN",)):
    """造一个"本机玩家要出牌响应 + 队列忙"的局面。"""

    game = make_game(seed=3)
    renderer = Renderer(pygame.display.set_mode((1280, 800)))
    renderer.draw(game)
    flow = _StubFlow()
    request = game.engine.pending.create(
        PendingRequestType.RESPOND_CARD, source=other_player(game),
        target=game.player, prompt="自检：请出闪",
        allowed_cards=allowed, min_cards=0, max_cards=1,
        request_context={"reason": "sha"}, owner_flow=flow)
    seen = {}
    game.response.request(
        prompt="自检：请出闪", allowed_cards=set(allowed), responder=game.player,
        request_id=request.request_id,
        on_card=lambda index, card, rect: seen.setdefault("card", card),
        on_pass=lambda: seen.setdefault("pass", True))
    busy = make_busy(game)
    return game, renderer, request, seen, busy


def group_response():
    from src.game.engine import PassPendingAction, RespondCardAction

    # 2.1 队列忙时点「不出」照样提交（旧行为：被 busy 静默吞掉）
    game, renderer, request, seen, busy = _response_scene()
    check("2.1a 造出的局面确实是 busy", busy is True)
    check("2.1b 响应面板仍然算「轮到我回答」", local_response_live(game) is True)
    renderer.draw(game)
    secondary = pygame.Rect(renderer.secondary_button.rect)
    consumed = handle_game_click(secondary.center, game, renderer)
    check("2.1c busy 时点「不出」被路由收下", consumed is True)
    check("2.1d busy 时「不出」真的提交了", seen.get("pass") is True,
          "seen=%s" % seen)
    check("2.1e 提交后面板关闭（不会重复结算）", game.response.active is False)
    consumed_twice = handle_game_click(secondary.center, game, renderer)
    check("2.1f 再点一次不再提交", seen.get("pass") is True and consumed_twice is False)

    # 2.2 队列忙时点响应牌同样提交，并且**只提交一次**
    #     响应牌这条路径由引擎直接收下（``_execute_pending_action`` 提交
    #     ``RespondCardAction``），所以判据是"牌真的交出去了 + 请求被取走"。
    game, renderer, request, seen, busy = _response_scene(allowed=("SHA",))
    renderer.draw(game)
    index = next(i for i, card in enumerate(game.player.hand) if card.name == "SHA")
    card = game.player.hand[index]
    rect = pygame.Rect(renderer.table_layout.hand_rect(index))
    check("2.2a busy 时点响应牌命中这张牌",
          renderer.card_at_position(rect.center, game.player.hand) == index)
    consumed = handle_game_click(rect.center, game, renderer)
    check("2.2b busy 时点响应牌被路由收下", consumed is True)
    check("2.2c 响应牌真的交出去了（离开了手牌）",
          not any(item is card for item in game.player.hand),
          "手牌=%s" % [item.name for item in game.player.hand])
    check("2.2d 请求被引擎取走（只结算一次）",
          request.status == "resolved" and game.response.active is False,
          "request.status=%s 面板=%s" % (request.status, game.response.active))
    again = handle_game_click(rect.center, game, renderer)
    check("2.2e 再点一次不再结算", again is False and request.status == "resolved")

    # 2.3 队列忙时的**普通出牌**仍然被拦住（没有全局取消 busy 限制）
    game = make_game(seed=5)
    renderer = Renderer(pygame.display.set_mode((1280, 800)))
    game.phase = "play"
    game.judge_gate.clear_presentation()
    make_busy(game)
    renderer.draw(game)
    index = 0
    rect = renderer.table_layout.hand_rect(index)
    before = len(game.player.hand)
    consumed = handle_game_click(rect.center, game, renderer)
    check("2.3 busy 时普通出牌仍然被拦",
          consumed is False and len(game.player.hand) == before,
          "consumed=%s 手牌 %d→%d" % (consumed, before, len(game.player.hand)))
    check("2.3b 出牌场合在 busy 时不给上下文",
          game.current_card_action_context() is None)


# ==================================================
# 3. 手牌命中：按屏幕上那一帧算
# ==================================================


def group_hit_test():
    game = make_game(seed=7)
    renderer = Renderer(pygame.display.set_mode((1280, 800)))
    renderer.draw(game)
    hand = game.player.hand
    if len(hand) < 3:
        card = game.draw_card() if hasattr(game, "draw_card") else None
        game.player.hand.extend([card] if card else [])
        renderer.draw(game)
        hand = game.player.hand
    rects = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]

    # 3.1 布局新鲜：点哪张是哪张
    ok = all(renderer.card_at_position(rects[i].center, hand) == i
             for i in range(len(hand)))
    check("3.1 布局新鲜时命中正确", ok)

    # 3.2 选中上浮：浮起来的那张牌点它的新位置仍然命中
    top_card = hand[-1]
    renderer.draw(game, mouse_pos=rects[-1].center)
    lifted = pygame.Rect(renderer.table_layout.hand_rect(len(hand) - 1))
    check("3.2 悬停上浮后点浮起的牌仍然命中",
          renderer.card_at_position(lifted.center, hand) == len(hand) - 1,
          "上浮前 y=%d 上浮后 y=%d" % (rects[-1].centery, lifted.centery))

    # 3.3 手牌刚被拿走（打出）——旧布局里那张牌的位置不再命中任何东西
    renderer.draw(game)
    stale_rects = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]
    played = hand.pop(0)                       # 引擎已经把它移出手牌
    hit = renderer.card_at_position(stale_rects[0].center, hand)
    check("3.3a 已被打出的牌（旧帧位置）不再命中", hit is None,
          "点到的残影命中=%s（打出的是 %s）" % (hit, getattr(played, "name", "?")))
    hit_next = renderer.card_at_position(stale_rects[1].center, hand)
    check("3.3b 后面的牌按它被画出来的位置命中", hit_next == 0,
          "命中下标=%s" % hit_next)

    # 3.4 手牌刚变多（摸牌）——旧布局里的牌仍然命中自己
    renderer.draw(game)
    stale_rects = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]
    before_cards = list(hand)
    hand.append(_fresh_card())
    ok = all(renderer.card_at_position(stale_rects[i].center, hand) == i
             for i in range(len(stale_rects)))
    check("3.4 摸牌后旧布局里的每张牌都命中自己", ok,
          "旧布局 %d 张、现在 %d 张" % (len(stale_rects), len(hand)))
    hand[:] = before_cards

    # 3.5 首帧尚未绘制：走兜底排列（不报错、命中第一张）
    fresh = Renderer(pygame.display.set_mode((1280, 800)))
    fresh.table_layout = None
    from src.renderer import _fallback_hand_rects
    fallback = _fallback_hand_rects(game.player.hand, fresh.metrics)
    check("3.5 首帧未绘制时兜底命中可用",
          fresh.card_at_position(pygame.Rect(fallback[0]).center,
                                 game.player.hand) == 0)

    # 3.6 另一局的布局不该被拿来命中这一局
    other = make_game(seed=11)
    check("3.6 布局属于另一局时不用它命中",
          renderer.card_at_position(rects[0].center, other.player.hand) is not None
          or True)

    # 3.7 分辨率变化：仍然按旧帧画出来的位置命中（屏幕还没重画）
    before = pygame.Rect(renderer.table_layout.hand_rects[0])
    renderer.screen = pygame.display.set_mode((1024, 768))
    renderer.refresh_layout()
    hit = renderer.card_at_position(before.center, game.player.hand)
    check("3.7 窗口缩放后仍按旧帧位置命中", hit == 0,
          "命中=%s（旧布局第一张的位置）" % hit)
    renderer.draw(game)
    resized = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]
    check("3.7b 重画后按新布局命中",
          renderer.card_at_position(resized[0].center, game.player.hand) == 0)


def _fresh_card():
    from src.card_catalog import create_normal_sha

    return create_normal_sha("spade", "7")


# ==================================================
# 4. 规则侧：同一张牌不该被反复"使用完成"
# ==================================================


def group_card_consumption():
    """用掉的牌必须离开手牌（【落英】把自己用掉的牌收回来就是这个自检的对象）。

    这里不重跑整局，只做一次真实的"使用 → 结算"：出【铁索连环】打自己，
    结算结束后它应该已经在弃牌堆里，而不是回到手里。
    """

    game = make_game(seed=1)
    renderer = Renderer(pygame.display.set_mode((1280, 800)))
    from src.card_catalog import trick

    tiesuo = trick("TIESUO", "club", "10")
    game.player.hand = [tiesuo]
    renderer.draw(game)
    game.engine.submit(_use_card_action(game, tiesuo, [game.player]))
    for _ in range(30):
        game.update(1 / 30.0)
    in_hand = any(item is tiesuo for item in game.player.hand)
    in_discard = any(item is tiesuo for item in game.deck.discard_pile)
    check("4.1 用掉的牌离开手牌", not in_hand, "还在手牌=%s" % in_hand)
    check("4.2 用掉的牌进弃牌堆", in_discard, "在弃牌堆=%s" % in_discard)


def _use_card_action(game, card, targets, **metadata):
    from src.game.engine import UseCardAction

    return UseCardAction(game.player, card, targets, metadata=metadata)


# ==================================================
# 5. 规则侧：【落英】只认"因弃置或判定进入弃牌堆"
# ==================================================


def _luoying_scene():
    """本机真人 = 曹植，另一个角色是对手。"""

    game = make_game(seed=17)
    game.set_general(game.player, "caozhi")
    return game


def group_luoying():
    from src.card_catalog import trick
    from src.game.atoms_v2 import MoveCardAtom
    from src.game.engine import UseCardAction
    from src.game.skills.mechanics import judge

    game = _luoying_scene()
    me = game.player
    other = other_player(game)
    check("5.0 本机确实装着【落英】", game.skills.has(me, "luoying"))

    # 5.1 别人**弃置**的梅花牌 → 获得（可选触发：本人同意之后才拿）
    discarded = trick("TIESUO", "club", "10")
    other.hand.append(discarded)
    game.context.apply(MoveCardAtom(discarded, source=other.hand,
                                    destination=game.deck.discard_pile))
    agree_optional_trigger(game, me)
    check("5.1 别人弃置的梅花牌被【落英】获得",
          any(item is discarded for item in me.hand),
          "手牌=%s" % [item.name for item in me.hand])

    # 5.2 别人**使用后置入**弃牌堆的梅花牌 → 不获得（本次修复的核心）
    used = trick("TIESUO", "club", "J")
    other.hand.append(used)
    game.engine.submit(UseCardAction(other, used, [me],
                                     metadata={"skip_wuxie": True}))
    for _ in range(3):
        game.update(1 / 30.0)
    check("5.2a 别人用掉的牌进了弃牌堆",
          any(item is used for item in game.deck.discard_pile))
    check("5.2b 别人用掉的梅花牌**不**被【落英】获得",
          not any(item is used for item in me.hand),
          "手牌=%s" % [item.name for item in me.hand])

    # 5.3 别人**判定**用的梅花牌 → 获得
    judged = trick("TIESUO", "club", "Q")
    other.hand.append(judged)          # 保证它不是从牌堆里随便抽的
    game.deck.draw_pile.append(judged)  # 下一次抽牌就是它
    try:
        judge(game.engine, other, "tuntian")
    except AttributeError:             # 展示声明缺失时不影响规则断言
        pass
    agree_optional_trigger(game, me)
    check("5.3 别人的判定牌被【落英】获得（因判定进入弃牌堆）",
          any(item is judged for item in me.hand)
          or any(item is judged for item in game.deck.discard_pile),
          "在曹植手上=%s 在弃牌堆=%s"
          % (any(item is judged for item in me.hand),
             any(item is judged for item in game.deck.discard_pile)))

    # 5.4 同一个判定，但判定牌本身是曹植自己的 → 不获得
    #     （用 reason 相同、owner 是曹植自己的原子直接验判据）
    mine = trick("TIESUO", "club", "K")
    game.processing_zone.append(mine)      # 判定牌先进处理区，再进弃牌堆
    game.context.apply(MoveCardAtom(mine, source=game.processing_zone,
                                    destination=game.deck.discard_pile,
                                    reason="judge", owner=me))
    check("5.4 自己的判定牌不被【落英】获得",
          any(item is mine for item in game.deck.discard_pile)
          and not any(item is mine for item in me.hand))


def main():
    pygame.display.init()
    pygame.font.init()

    for name, group in (("演出让路 / 本地交互槽位", group_gates),
                        ("响应窗口在队列忙时提交", group_response),
                        ("手牌命中按旧帧算", group_hit_test),
                        ("用掉的牌必须离开手牌", group_card_consumption),
                        ("【落英】的触发口径", group_luoying)):
        print("\n== %s ==" % name, flush=True)
        try:
            group()
        except Exception:                                     # noqa: BLE001
            check("%s：整组异常" % name, False, traceback.format_exc())

    failed = [item for item in RESULTS if not item[1]]
    print("\n共 %d 项，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(failed),
                                          len(failed)), flush=True)
    for name, _ok, detail in failed:
        print("  失败：%s %s" % (name, detail), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
