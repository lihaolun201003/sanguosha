"""批量试玩（soak）：用**真实点击路由**把一局局打完，只记录问题、不修。

和 ``auto_play.py`` 的区别：

* ``auto_play.py`` 在真实窗口里跑一局，用来肉眼验收；
* 本脚本在 SDL dummy 驱动下**批量**跑很多局，用来找"偶发但会卡住玩家"的问题。

两条路径的交互层**完全一样**：点击都走 ``ui.interaction.handle_game_click``
（``main.py`` 处理鼠标点击时的唯一入口），区别只是显示驱动与窗口尺寸。
渲染用的是真实 ``Renderer``——点击命中依赖它构建的 ``table_layout``，所以
每帧都要真画。

记录下来但不修的问题：

* ``点击无效``：投了点击、局面没有任何变化（附带"被谁挡住"的诊断）
* ``卡住``：连续多帧没有任何变化，且不是在等 AI
* ``异常``：任意 traceback
* ``僵局``：跑到帧数上限仍未结束

用法::

    .venv\\Scripts\\python.exe tools/soak_play.py [局数] [起始种子] [输出文件]
"""

import io
import json
import os
import sys
import time
import traceback
from collections import Counter

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame                                                        # noqa: E402

from src.game import Game                                            # noqa: E402
from src.renderer import Renderer                                    # noqa: E402
from src.ui import player as ui_player                               # noqa: E402
from src.choice import ChoiceOverlay                                # noqa: E402
from src.ui.interaction import _in_interactive_slot, handle_game_click  # noqa: E402

SIZE = (1280, 800)
FRAME = 1.0 / 30.0
#: 一局的帧数上限（30fps × 200 秒；卡住不动的局不必陪它耗到底）
MAX_FRAMES = 6000
#: 连续多少帧没有任何变化就认为"卡住"
STUCK_FRAMES = 75
#: 连续多少帧推不动就判定僵局（只在不是等别人时才判）
DEADLOCK_FRAMES = 240
#: 等别人（AI 动画 / 结算）超过这么久也放弃这一局
WAIT_FRAMES = 900


class Player:
    """一个只会做"显然该做的事"的玩家：看局面 → 点最该点的地方。"""

    #: 点一次之后等几帧再决策（让状态更新，也避免同一动作连点）
    COOLDOWN = 3

    def __init__(self, screen):
        self.screen = screen
        self.problems = []
        self.seen_problems = set()
        self.clicks = 0
        self.ineffective = 0
        self.frames = 0
        self.last_sig = None
        self.same = 0
        self.cooldown = 0
        self.stalled = False

    # ---- 主循环 ----

    def play(self, seed):
        game, renderer = self._setup(seed)
        self.game = game
        self.renderer = renderer
        while self.frames < MAX_FRAMES:
            # 主循环的三步缺一不可（main.py 也是这个顺序）：
            #   game.update    推进引擎与动作队列
            #   renderer.update 推进表现层（演出队列 / FX）——**漏了它，演出队列
            #                   永不消费、演出闸门一直开着、动作队列被压住，
            #                   整局看起来就是"卡死"。这是脚本第一版踩的坑。
            #   renderer.draw  构建布局（点击命中依赖它）
            game.update(FRAME)
            renderer.update(FRAME)
            renderer.draw(game)
            self.choice_overlay.sync_layout(renderer.metrics)
            self.frames += 1
            if game.game_over or self.stalled:
                break
            if self._handle_choice():
                continue
            self._observe_and_act()
        return self._result(seed)

    # ---- 构造一局 ----

    def _setup(self, seed):
        game = Game(ai_count=2)
        game.rng.seed(seed)
        renderer = Renderer(self.screen)
        # 二选一浮层由主循环单独持有（main.py 也是这么接的），不在这里接
        # 就会卡在"浮层挡着、点哪都不动"。
        overlay = ChoiceOverlay(self.screen)
        overlay.metrics = renderer.metrics
        self.choice_overlay = overlay
        game.scene = "general_select"
        game.general_candidates = game.roll_general_candidates()
        if not game.general_candidates:
            raise RuntimeError("没有候选武将")
        game.confirm_general(game.general_candidates[0])
        game.scene = "game"
        return game, renderer

    # ---- 观察 + 决策 ----

    def _sig(self):
        game = self.game
        hand = getattr(game.player, "hand", ())
        selection = getattr(game, "pending_selection", None) or {}
        targets = getattr(game, "pending_target_selection", None) or {}
        request = getattr(game, "pending_request", None)
        # 指纹必须覆盖**所有会影响界面**的状态：漏一项就会把"状态确实变了"
        # 误判成"点击无效"。实测漏过两类：二选一浮层（点手牌→弹确认框）
        # 与目标候选列表（点手牌→进入选目标）。
        return (
            str(game.phase), len(hand), int(game.player.hp),
            str(getattr(game.current_turn_player, "name", "")),
            len(selection.get("selected") or ()),
            len(selection.get("candidates") or ()),
            len(targets.get("selected") or ()),
            len(targets.get("candidates") or ()),
            getattr(request, "request_id", 0),
            bool(getattr(game.response, "active", False)),
            bool(getattr(game.choice, "active", False)),
            bool(getattr(game, "pending_skill_input", None)),
            bool(getattr(game, "pending_card_action", None)),
            bool(getattr(game, "pending_skill_picker", None)),
            bool(getattr(game, "pending_view_as", None)),
            str(getattr(game, "message", "") or ""),
        )

    def _handle_choice(self):
        """二选一浮层：与主循环一样单独拦截点击。"""

        if not self.game.choice.active:
            return False
        overlay = self.choice_overlay
        overlay.sync_layout(self.game.metrics if hasattr(self.game, "metrics")
                            else self.renderer.metrics,
                            len(getattr(self.game.choice.current, "options", ()) or ()))
        before = self._sig()
        if self.cooldown > 0:
            self.cooldown -= 1
            return True
        point = None
        if overlay.yes_rect.width > 10:
            point = overlay.yes_rect.center
        elif overlay.option_rects:
            point = pygame.Rect(overlay.option_rects[0]).center
        if point is None:
            return True
        self.clicks += 1
        try:
            overlay.handle_click((int(point[0]), int(point[1])), self.game.choice)
        except Exception:                                 # noqa: BLE001
            self._record("异常(选项)", "choice", traceback.format_exc(limit=4))
        if self._sig() == before:
            self.ineffective += 1
            self._record("点击无效(选项)", "choice",
                         "二选一点 %s 无变化 phase=%s pending=%s"
                         % (tuple(int(v) for v in point), self.game.phase,
                            self._pending_desc()))
        self.cooldown = self.COOLDOWN
        return True

    def _observe_and_act(self):
        if self.cooldown > 0:
            self.cooldown -= 1
            # 冷却期间仍要更新"卡住"计数，否则会把等待当成卡死。
            if self._sig() == self.last_sig:
                self.same += 1
            else:
                self.same = 0
            self.last_sig = self._sig()
            return
        sig = self._sig()
        if sig == self.last_sig:
            self.same += 1
        else:
            self.same = 0
        self.last_sig = sig
        if self.same and self.same % STUCK_FRAMES == 0:
            if not self._waiting_for_others():
                self._record("卡住", "stuck",
                             "连续 %d 帧无变化 phase=%s pending=%s 手牌=%d"
                             % (self.same, self.game.phase, self._pending_desc(),
                                len(self.game.player.hand)))
        if self.same >= DEADLOCK_FRAMES:
            # 等别人和真的卡住必须分开：等 AI 的动作队列播完可能很久，那不算
            # 僵局（第一版没分开，把正常等待误判成了死局）。
            if self._waiting_for_others():
                if self.same >= WAIT_FRAMES:
                    self._record("等待超时", "wait",
                                 "等别人 %d 帧仍未推进 phase=%s pending=%s busy=%s"
                                 % (self.same, self.game.phase,
                                    self._pending_desc(), self.game.busy))
                    self.stalled = True
                return True
            self._record("僵局", "deadlock",
                         "连续 %d 帧推不动（不是在等别人）phase=%s pending=%s 手牌=%d"
                         % (self.same, self.game.phase, self._pending_desc(),
                            len(self.game.player.hand)))
            self.stalled = True
            return True
        action = self._decide()
        if action is None:
            return
        self._click(action)
        self.cooldown = self.COOLDOWN

    def _waiting_for_others(self):
        game = self.game
        if game.pending_request is not None:
            return False
        if game.current_turn_player is not game.player:
            return True
        return bool(game.busy)

    def _pending_desc(self):
        game = self.game
        bits = []
        request = getattr(game, "pending_request", None)
        if request is not None:
            bits.append("request=%s/%s" % (
                request.request_type.value, request.context.get("reason")))
        for name in ("pending_selection", "pending_target_selection",
                     "pending_skill_input", "pending_card_action",
                     "pending_skill_picker", "pending_view_as"):
            if getattr(game, name, None):
                bits.append(name.replace("pending_", ""))
        if getattr(game.response, "active", False):
            bits.append("response")
        if getattr(game.choice, "active", False):
            bits.append("choice")
        return "+".join(bits) or "-"

    # ---- 决策 ----

    def _decide(self):
        game = self.game
        renderer = self.renderer
        if renderer.table_layout is None or renderer.table_layout.game is not game:
            return None

        if getattr(game, "pending_skill_picker", None):
            rects = list(getattr(renderer.skill_picker, "rects", ()) or ())
            if rects:
                return ("选技能", pygame.Rect(rects[0]).center)

        if getattr(game, "pending_card_action", None):
            rects = list(getattr(renderer.action_picker, "rects", ()) or ())
            if rects:
                return ("选出牌方式", pygame.Rect(rects[0]).center)

        if getattr(game.choice, "active", False):
            # 二选一浮层由 Renderer 持有；不接它就会落在"结束回合"上死循环
            # （浮层挡着，按钮点不动）。实测踩过这个坑。
            overlay = getattr(renderer, "choice_overlay", None)
            if overlay is not None and overlay.yes_rect.width > 10:
                return ("确认选项", overlay.yes_rect.center)
            return None

        if getattr(game.response, "active", False):
            usable = self._usable()
            if usable:
                return ("响应出牌", self._point_of(sorted(usable)[0]))
            return ("不出", pygame.Rect(renderer.secondary_button.rect).center)

        if getattr(game, "pending_selection", None) is not None:
            return self._decide_selection()

        if getattr(game, "pending_target_selection", None) is not None:
            return self._decide_targets()

        if getattr(game, "pending_skill_input", None) is not None:
            return self._decide_skill_input()

        if (game.current_turn_player is game.player and game.phase == "play"
                and not game.busy):
            return self._decide_play()

        if (game.current_turn_player is game.player and game.phase == "discard"
                and not game.busy):
            return self._decide_discard()

        return None

    def _usable(self):
        game = self.game
        usable = ui_player.respondable_hand_indices(game)
        if usable is None:
            usable = ui_player.playable_hand_indices(game)
        return set(usable or ())

    def _point_of(self, index):
        rect = self.renderer.table_layout.hand_rect(index)
        return pygame.Rect(rect).center if rect is not None else None

    def _decide_selection(self):
        game = self.game
        selection = game.pending_selection
        need = int(selection.get("number") or 0)
        picked = len(selection.get("selected") or ())
        if picked < max(1, need):
            chosen = {id(card) for card, _r, _k in selection.get("selected", ())}
            for card in [c for c, _k in selection.get("candidates", ())]:
                if id(card) in chosen:
                    continue
                point = self._card_point(card)
                if point is not None:
                    return ("选牌", point)
        return None

    def _decide_targets(self):
        selection = self.game.pending_target_selection
        picked = len(selection.get("selected") or ())
        minimum = int(selection.get("minimum") or 1)
        if picked < minimum:
            for candidate in selection.get("candidates", ()):
                if candidate in selection.get("selected", ()):
                    continue
                rect = self._seat_point(candidate)
                if rect is not None:
                    return ("选目标", rect)
            return None
        return ("确认目标", pygame.Rect(self.renderer.primary_button.rect).center)

    def _seat_point(self, player):
        """这个角色在屏幕上的可点位置。

        **真人自己不在 ``seat_rects`` 里**——对手走座位面板、自己走底部状态条，
        所以"目标是自己"必须单独处理，否则会退化成"点不动"。
        """

        rect = self.renderer.table_layout.seat_rect(player)
        if rect is not None:
            return pygame.Rect(rect).center
        if player is self.game.player:
            status = self.renderer.metrics.player_status
            return pygame.Rect(status).center
        return None

    def _decide_skill_input(self):
        game = self.game
        renderer = self.renderer
        info = game.pending_skill_input
        view_as = getattr(game, "pending_view_as", None)
        if view_as:
            point = self._card_point(view_as[0])
            if point is not None:
                return ("技能来源牌", point)
        targets = list(info.get("targets") or ())
        if targets and info.get("target") is None:
            point = self._seat_point(targets[0])
            if point is not None:
                return ("技能目标", point)
        if renderer.primary_button.enabled:
            return ("确认发动技能", pygame.Rect(renderer.primary_button.rect).center)
        return None

    def _decide_play(self):
        game = self.game
        renderer = self.renderer
        usable = self._usable()
        if not usable:
            return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)
        point = self._point_of(sorted(usable)[0])
        if point is None:
            return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)
        return ("出牌", point)

    def _decide_discard(self):
        game = self.game
        renderer = self.renderer
        need = len(game.player.hand) - game.hand_limit(game.player)
        if need > 0:
            rect = renderer.table_layout.hand_rect(len(game.player.hand) - 1)
            if rect is not None:
                return ("弃牌", pygame.Rect(rect).center)
        return ("结束回合", pygame.Rect(renderer.primary_button.rect).center)

    def _card_point(self, card):
        """牌在屏幕上的可点位置（先手牌、再公共池——池子按整池排布算）。"""

        game = self.game
        renderer = self.renderer
        for index, item in enumerate(game.player.hand):
            if item is card:
                rect = renderer.table_layout.hand_rect(index)
                if rect is not None:
                    return pygame.Rect(rect).center
        try:
            entries = renderer.get_pool_entries(game)
            cards = [item for item, _k in entries]
            rects = renderer.get_public_card_rects(cards)
        except Exception:                                 # noqa: BLE001
            return None
        for slot, (item, _k) in enumerate(entries):
            if item is card and slot < len(rects):
                return pygame.Rect(rects[slot]).center
        return None

    # ---- 点击 + 验证 ----

    def _record(self, kind, label, detail):
        """同类同因的问题只记一次（去重），计数另外统计。"""

        key = (kind, label, detail[:120])
        if key in self.seen_problems:
            return
        self.seen_problems.add(key)
        self.problems.append({"kind": kind, "label": label, "detail": detail})

    def _click(self, action):
        label, point = action
        if point is None:
            return
        before = self._sig()
        self.clicks += 1
        try:
            consumed = handle_game_click((int(point[0]), int(point[1])),
                                        self.game, self.renderer)
        except Exception:                                 # noqa: BLE001
            self._record("异常(点击)", label, traceback.format_exc(limit=4))
            return
        after = self._sig()
        if after == before:
            self.ineffective += 1
            self._record("点击无效", label,
                         "%s 点 %s 无变化 consumed=%s phase=%s pending=%s 原因=%s"
                         % (label, tuple(int(v) for v in point), consumed,
                            self.game.phase, self._pending_desc(),
                            self._why(point)))

    def _why(self, point):
        renderer = self.renderer
        game = self.game
        bits = ["hold=%s" % ("yes" if renderer.effects.interaction_hold() else "no"),
                "busy=%s" % game.busy,
                "slot=%s" % _in_interactive_slot(game)]
        hand = game.player.hand
        index = renderer.card_at_position((int(point[0]), int(point[1])), hand)
        bits.append("hand_hit=%s/%d" % (index, len(hand)))
        bits.append("hand_rects=%d" % len(renderer.table_layout.hand_rects))
        return " ".join(bits)

    # ---- 收尾 ----

    def _result(self, seed):
        game = self.game
        return {
            "seed": seed,
            "frames": self.frames,
            "clicks": self.clicks,
            "ineffective": self.ineffective,
            "over": bool(game.game_over),
            "stalled": bool(self.stalled),
            "phase_end": str(game.phase),
            "winner": str(getattr(game.winner, "name", "") or ""),
            "survivors": sum(1 for p in game.players if p.alive),
            "problems": self.problems,
        }


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    out = sys.argv[3] if len(sys.argv) > 3 else "docs/reports/assets/phase18_5_ui/soak.json"
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode(SIZE)
    rounds = []
    started = time.time()
    for offset in range(count):
        seed = start + offset
        player = Player(screen)
        try:
            record = player.play(seed)
        except Exception:                                 # noqa: BLE001
            record = {"seed": seed, "over": False, "crashed": True,
                      "problems": [{"kind": "异常(整局)",
                                    "detail": traceback.format_exc(limit=6)}],
                      "clicks": 0, "ineffective": 0, "frames": player.frames}
        rounds.append(record)
        if (offset + 1) % 10 == 0:
            print("  完成 %d/%d（%.0f 秒）" % (offset + 1, count, time.time() - started),
                  flush=True)
    kinds = Counter()
    for item in rounds:
        for problem in item.get("problems", ()):
            kinds[problem["kind"]] += 1
    summary = {
        "rounds": len(rounds),
        "seconds": round(time.time() - started, 1),
        "total_clicks": sum(r.get("clicks", 0) for r in rounds),
        "total_ineffective": sum(r.get("ineffective", 0) for r in rounds),
        "finished": sum(1 for r in rounds if r.get("over")),
        "crashed": sum(1 for r in rounds if r.get("crashed")),
        "problem_kinds": dict(kinds),
    }
    target = os.path.join(ROOT, out)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with io.open(target, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rounds": rounds}, handle,
                  ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("写出", target, flush=True)


if __name__ == "__main__":
    main()
