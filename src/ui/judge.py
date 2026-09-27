"""通用判定展示面板（Judge Presentation Panel）。

所有判定共用这一个面板：延时锦囊、装备技能、武将技能都只是
``judge_presentation.JUDGE_SOURCES`` 里的一条声明，面板本身不认识任何
具体卡名或技能名。

生命周期（全部由 dt 驱动，**绝不 sleep、绝不额外抽牌**）：

    OPEN → SOURCE_HOLD → DRAW_ANIMATION → FLIP → REVEALED_HOLD
         →（可多次 REPLACEMENT，鬼才一类改判）
         → FINAL_RESULT → OUTCOME_HOLD → FADE_OUT → DONE

判定牌永远来自引擎已经移动过的实体牌（``JudgeResult.card`` /
``JudgeContext.current_card``），面板只表现它，不产生任何牌。

判定牌的出场分两步，为的是"翻判定牌"这个动作真的能被看见：

* ``DRAW_ANIMATION``（``draw_progress``）：一张**牌背**从牌堆方向滑进判定区，
  此时绝不能露出正面；
* ``FLIP``（``flip_progress``）：到中央后才翻面——水平方向缩放做伪 3D，
  前半段牌背收窄、后半段换成正面展开，**卡牌中心点全程不动**。

顺序即"先飞入、再翻开"：把翻面拆成独立阶段（而不是塞进 DRAW_ANIMATION 的
后半段）之后，"牌背在飞 / 正在翻 / 已翻开"三件事各有一个可断言的标量，
验收脚本与将来的调试都不用去猜进度落在哪一段。
"""

import pygame

from src.game.judge_presentation import JudgeOutcomeTone, JudgeSourceKind
from enum import Enum

from . import cards as card_draw
from . import theme
from .widgets import ellipsize_text

PANEL_WIDTH = 780
PANEL_HEIGHT = 316
PANEL_PAD = 26
SOURCE_WIDTH = 196
COLUMN_GAP = 26

SOURCE_CARD_SIZE = (140, 196)
JUDGE_CARD_SIZE = (128, 180)

# 判断"面板不会永远挂着"的兜底：真实判定流程早已结束却迟迟没有结果事件。
MAX_HOLD = 20.0

#: 每个阶段的**最短可读时间**（绝对秒数，不再按速度倍率缩小）。
#: 这是"极速档下判定牌也不会只闪 0.05 秒"的落实点：无论本机把动画速度
#: 调到多快，判定来源、判定牌、判定结果都要各自停留到能看清为止。
MIN_STAGE = {
    "open": 0.22,
    "source_hold": 0.42,
    "draw_animation": 0.32,
    "flip": 0.34,
    "revealed_hold": 0.60,
    "replacement": 0.40,
    "final_result": 0.30,
    "outcome_hold": 1.05,
    "fade_out": 0.18,
}

#: 翻牌动画的基准时长（秒，未按速度档缩放）。改判之外的每个阶段的时长都
#: 来自 ``FXTiming``，但那边没有"翻牌"这一项，而本面板又只该管自己的表现；
#: 所以这里取基准值再乘上当前速度档的缩放系数（``FXTiming.scale``），
#: 效果与 ``FXTiming.judge_draw`` 一类完全一致：档位越快翻得越快，
#: 但再快也不会短于 ``MIN_STAGE["flip"]``（极速档下依然看得见翻面）。
FLIP_BASE_SECONDS = 0.62

#: 翻到一半时那条竖线的最小宽度（设计像素）。0 宽会让牌"消失一帧"——
#: 那正是翻牌动画最容易露馅的地方，所以给一个下限。
FLIP_MIN_WIDTH = 3


class JudgeStage(str, Enum):
    OPEN = "open"
    SOURCE_HOLD = "source_hold"
    DRAW_ANIMATION = "draw_animation"
    FLIP = "flip"
    REVEALED_HOLD = "revealed_hold"
    REPLACEMENT = "replacement"
    FINAL_RESULT = "final_result"
    OUTCOME_HOLD = "outcome_hold"
    FADE_OUT = "fade_out"
    DONE = "done"


def _timing():
    from .fx import timing

    return timing()


def _flip_seconds():
    """翻牌这一段该走多久：基准时长 × 当前速度档。"""

    return FLIP_BASE_SECONDS * float(getattr(_timing(), "scale", 1.0))


class JudgePanel:
    """一次判定的展示状态机；同一时刻只展示一次判定（引擎也是串行的）。"""

    #: 判定牌的**入场**阶段：牌还在飞（``DRAW_ANIMATION``）或还在翻（``FLIP``）。
    #: 这段时间里换牌会把入场动画打断，所以改判只能先记账，等入场演完再切
    #: （见 ``note_replacement``）——改判窗口和判定牌是同时出现的，玩家点得
    #: 快就会落在这一段里。
    ENTRANCE_STAGES = (JudgeStage.OPEN, JudgeStage.SOURCE_HOLD,
                       JudgeStage.DRAW_ANIMATION, JudgeStage.FLIP)

    def __init__(self):
        self.active = False
        self.stage = JudgeStage.DONE
        self.metrics = None
        # 展示数据
        self.reason = ""
        self.spec = None
        self.owner = None
        self.revealed_card = None
        self.entrance_card = None       # 入场动画演的那张（第一次翻出的判定牌）
        self.final_card = None
        self.previous_card = None       # 最近一次被替换掉的旧判定牌
        self.deferred_replacement = None   # 入场期间到达、还没换上的改判
        self.replacement_history = ()
        self.outcome = None
        self.result = None
        self.replacement_skill_name = ""
        self.replacement_actor = None
        # 节奏
        self.timer = 0.0
        self.stage_total = 1.0          # 当前阶段的实际时长（进度按它归一）
        self.hold_elapsed = 0.0
        self.alpha = 255
        self.draw_progress = 0.0        # 0→1 的飞入进度（牌背滑进判定区）
        self.flip_progress = 0.0        # 0→1 的翻面进度（0.5 处换面）

    # ==================================================
    # 事件入口
    # ==================================================

    def begin(self, result):
        """判定开始并翻开判定牌（JUDGE_REVEALED）。"""

        if result is None:
            return self
        self.active = True
        self._enter(JudgeStage.OPEN, _timing().judge_open)
        self.hold_elapsed = 0.0
        self.alpha = 255
        self.draw_progress = 0.0
        self.flip_progress = 0.0
        self.reason = getattr(result, "reason", "") or ""
        self.spec = getattr(result, "source_spec", None)
        self.owner = getattr(result, "target", None) or getattr(result, "source", None)
        self.revealed_card = getattr(result, "card", None)
        # 入场演的永远是这一次翻出的判定牌：后来的改判 / 最终结果都不会
        # 改变"玩家看到的是这张牌被翻开"这件事（见 ENTRANCE_STAGES）。
        self.entrance_card = self.revealed_card
        self.final_card = None
        self.previous_card = None
        self.deferred_replacement = None
        self.replacement_history = tuple(getattr(result, "replacement_history", ()) or ())
        self.outcome = None
        self.result = None
        self.replacement_skill_name = ""
        self.replacement_actor = None
        return self

    def note_replacement(self, payload, game=None):
        """改判发生：保留面板，换成新的判定牌并标出"被改过"。

        判定牌还在飞 / 还在翻的时候（``ENTRANCE_STAGES``）不换牌，只把这次
        改判记下来，等入场演完再切到 ``REPLACEMENT``：改判窗口与判定牌同时
        出现，玩家点得快时"第一次判定牌"必须照样被翻出来，不能被改判的牌
        顶掉。规则层与此无关——结果早就在引擎里算完了，这里只是**什么时候
        把哪张牌画出来**。
        """

        if not self.active:
            return self
        new_card = payload.get("new_card")
        old_card = payload.get("old_card")
        if new_card is None:
            return self
        if self.in_entrance:
            self.deferred_replacement = (payload, game)
            return self
        self.previous_card = old_card
        self.revealed_card = new_card
        self.replacement_history = tuple(payload.get("history") or ())
        self.replacement_actor = payload.get("player")
        skill_id = payload.get("skill_id") or ""
        self.replacement_skill_name = self._skill_name(game, skill_id)
        # 改判沿用现有表现：新牌直接以**正面**出现在判定区（不重播翻牌）。
        # 这里只把两个进度收尾，保证"牌一定是全部展开的正面"。
        self.draw_progress = 1.0
        self.flip_progress = 1.0
        self._enter(JudgeStage.REPLACEMENT, _timing().judge_replacement)
        return self

    def finish(self, result):
        """判定锁定最终判定牌（JUDGE_RESULT / JUDGE_FINISHED）。"""

        if result is None:
            return self
        self.result = result
        self.final_card = getattr(result, "card", None)
        self.outcome = getattr(result, "outcome", None)
        self.replacement_history = tuple(getattr(result, "replacement_history", ()) or ())
        return self

    def cancel(self):
        self.active = False
        self.stage = JudgeStage.DONE
        self.result = None
        return self

    def skip(self):
        """跳过演出：把面板推到淡出（**不改判定结果，也不结束判定流程**）。

        只有在规则层已经给出结论（``result`` 到手）时才真的跳：那时玩家已经
        没有必要继续等结论条的停留。反过来，判定还在等改判窗口时只把**本阶段
        的停留**走完——判定牌本来就要留在屏幕上等结果，把它收掉会让玩家
        根本没看见判定牌是什么。
        """

        if not self.active:
            return False
        self.timer = 0.0
        if self.result is None:
            return False
        # 跳过时把两个进度都收尾：牌背 / 半张牌都必须变成完整正面再淡出。
        # 入场期间到达的改判也不需要补演了：最终判定牌（result.card）已经
        # 到手，跳过的正是"看过程"。
        self.deferred_replacement = None
        self.draw_progress = 1.0
        self.flip_progress = 1.0
        self.alpha = 255
        self._hold_stage(JudgeStage.FADE_OUT,
                         min(max(_timing().judge_fade_out, 0.12), 0.25))
        return True

    # ==================================================
    # 推进
    # ==================================================

    def update(self, dt, game=None):
        if not self.active:
            return self
        timing = _timing()
        self.timer -= dt
        self.hold_elapsed += dt

        if self.stage is JudgeStage.OPEN:
            if self.timer <= 0:
                self._enter(JudgeStage.SOURCE_HOLD, timing.judge_source_hold)
        elif self.stage is JudgeStage.SOURCE_HOLD:
            if self.timer <= 0:
                self._enter(JudgeStage.DRAW_ANIMATION, timing.judge_draw)
        elif self.stage is JudgeStage.DRAW_ANIMATION:
            self.draw_progress = self.stage_progress
            if self.timer <= 0:
                # 牌背已经到位，下一段才翻面。
                self.draw_progress = 1.0
                self._enter(JudgeStage.FLIP, _flip_seconds())
        elif self.stage is JudgeStage.FLIP:
            self.flip_progress = self.stage_progress
            if self.timer <= 0:
                self.flip_progress = 1.0
                self._enter(JudgeStage.REVEALED_HOLD, timing.judge_revealed_hold)
                self._flush_deferred_replacement(game)
        elif self.stage is JudgeStage.REPLACEMENT:
            if self.timer <= 0:
                self._enter(JudgeStage.REVEALED_HOLD, timing.judge_revealed_hold)
        elif self.stage is JudgeStage.REVEALED_HOLD:
            if self.result is not None:
                if self.timer <= 0:
                    self._enter(JudgeStage.FINAL_RESULT, timing.judge_final)
            elif self.hold_elapsed > MAX_HOLD and not self._engine_is_judging(game):
                # 兜底：判定流程异常结束也不会让面板永远挂着。
                # 判据必须问引擎——"等得久"不等于"出错了"：改判窗口开着
                # 的时候（司马懿在挑牌 / 真人在想）判定逻辑**确实还在跑**，
                # 那时把面板收掉会变成"判定牌自己消失"。
                self._enter(JudgeStage.FADE_OUT, timing.judge_fade_out)
            else:
                # 改判窗口开着：保持展示，等引擎给出最终结果。
                self.timer = max(self.timer, timing.judge_revealed_hold,
                                 MIN_STAGE["revealed_hold"])
        elif self.stage is JudgeStage.FINAL_RESULT:
            if self.timer <= 0:
                self._enter(JudgeStage.OUTCOME_HOLD, timing.judge_outcome_hold)
        elif self.stage is JudgeStage.OUTCOME_HOLD:
            if self.timer <= 0:
                self._enter(JudgeStage.FADE_OUT, timing.judge_fade_out)
        elif self.stage is JudgeStage.FADE_OUT:
            total = max(1e-6, timing.judge_fade_out)
            self.alpha = int(255 * max(0.0, min(1.0, self.timer / total)))
            if self.timer <= 0:
                self.active = False
                self.stage = JudgeStage.DONE
        return self

    def _flush_deferred_replacement(self, game=None):
        """入场演完：把入场期间到达的改判补上（可能不止一次，见改判连锁）。"""

        while self.deferred_replacement is not None:
            payload, entry_game = self.deferred_replacement
            self.deferred_replacement = None
            self.note_replacement(payload, entry_game if entry_game is not None else game)
        return self

    @staticmethod
    def _engine_is_judging(game):
        """引擎里判定逻辑是不是还没走完（没有 UI / 只读视图时当作"已结束"）。"""

        gate = getattr(game, "judge_gate", None)
        if gate is None:
            return False
        return bool(gate.logical_pending)

    def _enter(self, stage, duration):
        """切到某个阶段：时长取 ``duration`` 与该阶段最短可读时间的较大者。"""

        minimum = MIN_STAGE.get(getattr(stage, "value", str(stage)), 0.0)
        return self._hold_stage(stage, max(float(duration), minimum))

    def _hold_stage(self, stage, seconds):
        """不看 ``MIN_STAGE`` 直接设定阶段与时长（``skip`` 一类收尾路径用）。"""

        self.stage = stage
        self.timer = float(seconds)
        self.stage_total = max(1e-6, self.timer)
        return self

    @property
    def stage_progress(self):
        """当前阶段走了多少（0→1）。

        分母用本阶段**实际**时长 ``stage_total``（已经含 ``MIN_STAGE`` 的下限），
        所以进度不会在极速档下提前冲到 1（那会让翻牌动画"跳帧到结果"）。
        """

        elapsed = self.stage_total - max(0.0, self.timer)
        return max(0.0, min(1.0, elapsed / self.stage_total))

    # ---- 只读查询（供测试与布局）----

    @property
    def shown_card(self):
        """当前应该展示的判定牌：最终牌优先，其次最新的判定牌。"""

        return self.final_card or self.revealed_card

    @property
    def in_entrance(self):
        """判定牌是不是还在入场（飞入 / 翻面）——这段时间不换牌。"""

        return self.stage in self.ENTRANCE_STAGES

    @property
    def was_replaced(self):
        return bool(self.replacement_history)

    @property
    def drawn_face(self):
        """这一帧判定牌画的是哪一面：``"back"`` / ``"front"``；没有牌则 None。

        纯派生量，只有绘制路径与验收脚本读它。它的意义是"演出承诺"：
        ``DRAW_ANIMATION`` 与 ``FLIP`` 前半段必须是 ``"back"``——判定牌的正面
        在翻到一半之前绝不出现。
        """

        if self.shown_card is None or self.stage in (JudgeStage.OPEN,
                                                     JudgeStage.SOURCE_HOLD):
            return None
        if self.stage is JudgeStage.DRAW_ANIMATION:
            return "back"
        if self.stage is JudgeStage.FLIP:
            return "back" if self.flip_progress < 0.5 else "front"
        return "front"

    # ---- 动作节奏：判定展示期间压住后续行动 ----

    @property
    def holds_actions(self):
        """判定还在演的时候，后面的行动必须等它演完。

        "任何判定之后的行动都必须等判定结束才能开始"就是靠这个属性实现的：
        动作队列每次要启动下一个动作前都会问它，True 就先不动（正在播的那一
        个照常跑完）。

        **唯一的例外**：``REVEALED_HOLD`` 阶段且还没有最终判定牌——此刻是
        **引擎在等人**（改判窗口，AI 的改判回答本身排在动作队列里）。这时压住
        队列会让判定永远拿不到最终结果，所以必须放行。
        """

        if not self.active or self.stage is JudgeStage.DONE:
            return False
        if self.stage is JudgeStage.REVEALED_HOLD and self.result is None:
            return False
        return True

    def owned_card_ids(self, game=None):
        """面板正在展示的实体牌（判定牌 + 卡面来源）的稳定身份。

        这些牌由面板独占绘制：判定牌此刻可能已经被引擎真实送进弃牌堆，
        但展示还没结束，所以弃牌堆顶暂时不画它——同一张牌不会同时出现在
        面板与弃牌堆两处。
        """

        if not self.active or self.stage is JudgeStage.DONE:
            return set()
        ids = set()
        card = self.shown_card
        if card is not None:
            ids.add(id(card))
        # 入场期间画的是**第一次翻出的判定牌**：它此刻还没进弃牌堆（改判换下来
        # 的那张才刚进去），所以一并报上去，免得同一张牌在面板与弃牌堆两处都出现。
        if self.in_entrance and self.entrance_card is not None:
            ids.add(id(self.entrance_card))
        source = self._source_card(game)
        if source is not None:
            ids.add(id(source))
        return ids

    @property
    def tone(self):
        outcome = self.outcome
        return getattr(outcome, "tone", None) or JudgeOutcomeTone.NEUTRAL

    def shows_outcome(self):
        return self.outcome is not None and self.stage in (
            JudgeStage.FINAL_RESULT, JudgeStage.OUTCOME_HOLD, JudgeStage.FADE_OUT)

    # ==================================================
    # 布局
    # ==================================================

    def rect(self, metrics):
        """面板矩形（设计坐标经过 metrics 缩放）；供 tooltip 避让使用。"""

        width = min(metrics.px(PANEL_WIDTH), int(metrics.screen_w * 0.72))
        height = min(metrics.px(PANEL_HEIGHT), int(metrics.screen_h * 0.62))
        rect = pygame.Rect(0, 0, width, height)
        # 略高于屏幕中心：下方留给中央阶段条、提示条与手牌。中心取 0.36——
        # 提示条上移（给放大的手牌腾地方）之后，再低一点就会压住它。
        rect.center = (metrics.screen_w // 2, int(metrics.screen_h * 0.36))
        return rect

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, surface, game, metrics):
        if not self.active or self.stage is JudgeStage.DONE:
            return None
        self.metrics = metrics
        panel = self.rect(metrics)
        pad = metrics.px(PANEL_PAD)
        gap = metrics.px(COLUMN_GAP)
        source_w = metrics.px(SOURCE_WIDTH)

        layer = pygame.Surface(panel.size, pygame.SRCALPHA)
        local = layer.get_rect()

        tone_color = theme.judge_tone_color(self.tone)
        pygame.draw.rect(layer, (*theme.PANEL_DEEP, 244), local,
                         border_radius=metrics.px(18))
        pygame.draw.rect(layer, theme.GOLD, local, metrics.px(3),
                         border_radius=metrics.px(18))
        # 结果态：整块面板外圈加一圈语义色，绿 / 红 / 中性一眼可辨。
        if self.shows_outcome():
            pygame.draw.rect(layer, (*tone_color, 220), local.inflate(-metrics.px(8), -metrics.px(8)),
                             metrics.px(2), border_radius=metrics.px(14))

        header = self._header_text()
        fonts = metrics.fonts
        title = fonts.get("normal").render(header, True, theme.GOLD_BRIGHT)
        layer.blit(title, (pad, pad))

        body_top = pad + title.get_height() + metrics.px(10)
        source_rect = pygame.Rect(pad, body_top, source_w, panel.height - body_top - pad)
        self._draw_source(layer, game, source_rect, metrics)

        right_x = pad + source_w + gap
        right_w = panel.width - right_x - pad
        right_rect = pygame.Rect(right_x, body_top, right_w, panel.height - body_top - pad)
        self._draw_judgement(layer, game, right_rect, metrics)

        if self.alpha < 255:
            layer.set_alpha(self.alpha)
        surface.blit(layer, panel.topleft)
        return panel

    # ---- 头部 ----

    def _header_text(self):
        name = getattr(self.owner, "name", "")
        label = getattr(self.spec, "display_name", "") or "判定"
        prefix = (name + " 的 ") if name else ""
        return "%s%s 判定" % (prefix, label)

    # ---- 左列：判定来源 ----

    def _draw_source(self, layer, game, rect, metrics):
        spec = self.spec
        kind = getattr(spec, "kind", JudgeSourceKind.OTHER)
        pad = metrics.px(10)
        caption = metrics.fonts.get("small")

        label = caption.render("判定来源", True, theme.TEXT_DIM)
        layer.blit(label, (rect.x, rect.y))
        box = pygame.Rect(rect.x, rect.y + label.get_height() + metrics.px(6),
                          rect.width, rect.height - label.get_height() - metrics.px(6))

        if kind is JudgeSourceKind.CARD:
            self._draw_card_source(layer, game, box, metrics)
        else:
            self._draw_skill_source(layer, game, box, metrics)
        del pad

    def _draw_card_source(self, layer, game, box, metrics):
        """延时锦囊：优先画判定区里那张真实卡面。"""

        card = self._source_card(game)
        if card is not None:
            size = self._fit(metrics, SOURCE_CARD_SIZE)
            target = pygame.Rect(0, 0, size[0], size[1])
            target.midtop = (box.centerx, box.y + metrics.px(4))
            frame = target.inflate(metrics.px(8), metrics.px(8))
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(8))
            pygame.draw.rect(layer, theme.GOLD_DIM, frame, 2, border_radius=metrics.px(8))
            card_draw.draw_card(layer, card, target, metrics.fonts)
        name = getattr(self.spec, "display_name", "")
        if name:
            font = metrics.fonts.get("small")
            rendered = font.render(name, True, theme.TEXT)
            layer.blit(rendered, rendered.get_rect(
                center=(box.centerx, box.bottom - metrics.px(12))))

    def _draw_skill_source(self, layer, game, box, metrics):
        """技能 / 装备来源：有武将素材就画武将牌，否则画通用技能卡。"""

        skill_name = self._skill_name(game, getattr(self.spec, "skill_id", ""))
        if not skill_name:
            skill_name = getattr(self.spec, "display_name", "") or "技能"
        art = self._general_art(game, box, metrics)
        header = box.height - metrics.px(58)
        if art is not None:
            scaled, target = art
            target.midtop = (box.centerx, box.y + metrics.px(4))
            frame = target.inflate(metrics.px(6), metrics.px(6))
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(6))
            pygame.draw.rect(layer, theme.GOLD_DIM, frame, 2, border_radius=metrics.px(6))
            layer.blit(scaled, target.topleft)
        else:
            # 通用技能卡：没有素材也必须看得见"是谁的技能"。
            plate = pygame.Rect(box.x + metrics.px(6), box.y + metrics.px(4),
                                box.width - metrics.px(12), max(metrics.px(46), header))
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, plate, border_radius=metrics.px(10))
            pygame.draw.rect(layer, theme.GOLD_DIM, plate, 2, border_radius=metrics.px(10))
            tag = metrics.fonts.get("micro").render("技能", True, theme.TEXT_DIM)
            layer.blit(tag, (plate.x + metrics.px(10), plate.y + metrics.px(8)))

        name_font = metrics.fonts.get("small")
        rendered = name_font.render(
            ellipsize_text(skill_name, name_font, box.width - metrics.px(8)),
            True, theme.GOLD_BRIGHT)
        layer.blit(rendered, rendered.get_rect(
            center=(box.centerx, box.bottom - metrics.px(34))))
        kind_label = self._kind_label(game, getattr(self.spec, "skill_id", ""))
        if kind_label:
            small = metrics.fonts.get("micro")
            text = small.render(kind_label, True, theme.TEXT_DIM)
            layer.blit(text, text.get_rect(center=(box.centerx, box.bottom - metrics.px(12))))

    # ---- 右列：判定牌与结果 ----

    def _draw_judgement(self, layer, game, rect, metrics):
        fonts = metrics.fonts
        # 规则文本：判定开始就展示"为什么判定"。
        rule = getattr(self.spec, "rule_text", "") or ""
        y = rect.y
        if rule:
            body = fonts.get("small")
            for line in self._wrap(rule, body, rect.width):
                rendered = body.render(line, True, theme.TEXT_DIM)
                layer.blit(rendered, (rect.x, y))
                y += rendered.get_height() + metrics.px(2)
            y += metrics.px(6)

        card = self.shown_card
        if card is None or self.in_entrance:
            pending = fonts.get("normal").render("判定中……", True, theme.TEXT)
            layer.blit(pending, (rect.x, y))
            # 入场只演**第一次翻出的判定牌**：改判 / 最终结果即使已经到达，
            # 也要等这张牌被翻完再换上（否则"翻判定牌"会被改判的牌顶掉）。
            entrance = self.entrance_card or card
            if entrance is not None and self.stage in (JudgeStage.DRAW_ANIMATION,
                                                       JudgeStage.FLIP):
                self._draw_reveal(layer, entrance, rect, metrics, y)
            return

        card_top = y
        target = self._judge_card_rect(rect, metrics, card_top)
        self._draw_judge_card(layer, card, target, metrics)

        text_x = target.right + metrics.px(18)
        text_w = max(metrics.px(120), rect.right - text_x)
        self._draw_judge_text(layer, game, card, text_x, card_top, text_w, metrics)

    @staticmethod
    def _judge_card_rect(rect, metrics, top):
        """判定牌的**最终**落点：REVEALED_HOLD 与判定的终点位置就是它。

        飞入、翻面、停留三个阶段全用这一个矩形，所以"翻完之后牌在哪、
        多大"与改动前逐像素一致（中心点不位移、宽高就是满值）。
        """

        size = JudgePanel._fit(metrics, JUDGE_CARD_SIZE)
        return pygame.Rect(rect.x + metrics.px(6), top, size[0], size[1])

    def _draw_reveal(self, layer, card, rect, metrics, top):
        """判定牌的出场：先飞入（牌背），再翻面，最后才是正面。"""

        target = self._judge_card_rect(rect, metrics, top)
        if self.stage is JudgeStage.DRAW_ANIMATION:
            self._draw_card_flight(layer, rect, metrics, target)
        else:
            self._draw_card_flip(layer, card, metrics, target)

    def _draw_card_flight(self, layer, rect, metrics, target):
        """飞入：一张**牌背**从右侧外沿滑到判定区（牌早已被引擎抽好）。

        这一阶段绝不能画正面——否则"翻判定牌"就没有任何悬念了。
        """

        progress = max(0.0, min(1.0, self.draw_progress))
        start_x = rect.right + metrics.px(40)
        moving = pygame.Rect(0, 0, target.width, target.height)
        moving.topleft = (int(start_x + (target.x - start_x) * progress), target.y)
        card_draw.draw_card_back(layer, moving)

    def _draw_card_flip(self, layer, card, metrics, target):
        """翻面：只用**水平缩放**做伪 3D（绕 Y 轴转过去的观感）。

        * 0→0.5：牌背，宽度 100% → 最窄（``FLIP_MIN_WIDTH``，不是 0）；
        * 0.5→1：换成**正面**，宽度最窄 → 100%；
        * 两次缩放都以牌的中心为基准，所以牌不会左右乱跳；
        * 高度全程不变，翻完就是与 ``REVEALED_HOLD`` 完全相同的满尺寸正面。
        """

        phase = max(0.0, min(1.0, self.flip_progress))
        face_up = phase >= 0.5
        # 0.5 是"那条竖线"：两侧的宽度都从这里取最小值，中间不会跳。
        ratio = (phase - 0.5) * 2.0 if face_up else 1.0 - phase * 2.0
        width = max(metrics.px(FLIP_MIN_WIDTH), int(round(target.width * ratio)))

        face = pygame.Surface(target.size, pygame.SRCALPHA)
        body = face.get_rect()
        if face_up:
            card_draw.draw_card(face, card, body, metrics.fonts)
        else:
            card_draw.draw_card_back(face, body)
        if width < body.width:
            face = pygame.transform.smoothscale(face, (width, body.height))
        layer.blit(face, face.get_rect(center=target.center))

    def _draw_judge_card(self, layer, card, target, metrics, *, size=None):
        frame = target.inflate(metrics.px(8), metrics.px(8))
        pygame.draw.rect(layer, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(8))
        pygame.draw.rect(layer, theme.GOLD_DIM, frame, 2, border_radius=metrics.px(8))
        card_draw.draw_card(layer, card, target, metrics.fonts)

    def _draw_judge_text(self, layer, game, card, x, y, width, metrics):
        fonts = metrics.fonts
        cursor = y
        name = fonts.get("normal").render(self._card_label(card), True, theme.TEXT)
        layer.blit(name, (x, cursor))
        cursor += name.get_height() + metrics.px(6)

        # 花色 + 点数单独一行（"♥ 7"）：判定牌的关键信息必须一眼就能看到，
        # 默认字体画不出 ♠♥♣♦，这里用符号字体。
        symbol = self._suit_mark(card, metrics)
        if symbol is not None:
            layer.blit(symbol, (x, cursor))
            cursor += symbol.get_height() + metrics.px(6)

        if self.was_replaced:
            # 面板里放不下第二张卡：用一行文字交代"原来翻出的是什么"，
            # 玩家依然能看出判定牌被换过、从什么换成了什么。
            actor = getattr(self.replacement_actor, "name", "")
            # 技能名不带【】（本行只有技能名；卡牌名的【】在别处，照旧保留）。
            action = ("发动%s改判" % self.replacement_skill_name) \
                if self.replacement_skill_name else "改判"
            note = fonts.get("small").render(
                "%s %s（共 %d 次）" % (actor, action, len(self.replacement_history)),
                True, theme.TARGET_BLUE)
            layer.blit(note, (x, cursor))
            cursor += note.get_height() + metrics.px(4)
            if self.previous_card is not None:
                was = fonts.get("small").render(
                    "原判定：" + self._card_label(self.previous_card), True, theme.TEXT_DIM)
                layer.blit(was, (x, cursor))
                cursor += was.get_height() + metrics.px(6)

        if self.shows_outcome() and self.outcome is not None:
            tone_color = theme.judge_tone_color(self.tone)
            # 结果结论是这块面板的答案：单独一条分隔线 + 更大字号，
            # 与"判定牌是什么"分开读。
            line_y = cursor + metrics.px(2)
            pygame.draw.line(layer, theme.GOLD_DIM, (x, line_y), (x + width, line_y), 1)
            cursor = line_y + metrics.px(10)
            title = fonts.get("large").render(self.outcome.title, True, tone_color)
            if title.get_width() > width:
                title = fonts.get("normal").render(self.outcome.title, True, tone_color)
            layer.blit(title, (x, cursor))
            cursor += title.get_height() + metrics.px(6)
            body = fonts.get("small")
            for line in self._wrap(self.outcome.text, body, width):
                rendered = body.render(line, True, theme.TEXT)
                layer.blit(rendered, (x, cursor))
                cursor += rendered.get_height() + metrics.px(2)
        else:
            hint = fonts.get("small").render("等待判定结果……", True, theme.TEXT_DIM)
            layer.blit(hint, (x, cursor))

    # ---- 小工具 ----

    def _source_card(self, game):
        """判定区里对应的那张实体牌（来源是延时锦囊时）。"""

        spec = self.spec
        name = getattr(spec, "card_name", "") or ""
        owner = self.owner
        if not name or owner is None or game is None:
            return None
        for card in getattr(owner, "judgement_zone", ()) or ():
            if getattr(card, "name", None) == name:
                return card
        return None

    def _general_art(self, game, box, metrics):
        """判定角色的武将牌缩略图（有素材才画）。"""

        owner = self.owner
        if game is None or owner is None:
            return None
        general = game.generals.get(getattr(owner, "general_id", None))
        if general is None:
            return None
        from . import assets as assets_module

        source = assets_module.get_registry().surface(
            assets_module.general_asset_id(general.id))
        if source is None:
            return None
        area = pygame.Rect(box.x, box.y, box.width, box.height - metrics.px(58))
        if area.width < 24 or area.height < 24:
            return None
        target = assets_module.fit_contain(area, source.get_size(), align="midtop")
        scaled = assets_module.get_registry().scaled(
            assets_module.general_asset_id(general.id), target.size)
        if scaled is None:
            return None
        return scaled, target

    def _skill_name(self, game, skill_id):
        if game is None or not skill_id:
            return ""
        definition = game.skill_registry.get(skill_id)
        return getattr(definition, "name", "") or ""

    def _kind_label(self, game, skill_id):
        if game is None or not skill_id:
            return ""
        definition = game.skill_registry.get(skill_id)
        kind = getattr(getattr(definition, "kind", None), "value", "")
        return {"active": "主动技", "view_as": "视为技",
                "locked": "锁定技", "passive": "触发技"}.get(kind, "")

    @staticmethod
    def _card_label(card):
        """卡牌的一行字标签：牌名 + 中文花色点数（默认字体画不出 ♠♥♣♦）。"""

        suit = getattr(card, "suit_name", "") or ""
        rank = "" if not suit else str(getattr(card, "rank", "") or "")
        label = getattr(card, "display_name", "") or ""
        return (label + "　" + suit + rank) if suit else label

    @staticmethod
    def _suit_mark(card, metrics):
        """花色符号 + 点数（"♥ 7"）；拿不到花色符号时返回 None。"""

        symbol = getattr(card, "suit_symbol", "") or ""
        rank = str(getattr(card, "rank", "") or "")
        if not symbol or not rank:
            return None
        font = metrics.fonts.suit(max(16, int(metrics.px(30))))
        color = theme.CARD_FACE_RED if getattr(card, "card_color", "") == "red" \
            else theme.CARD_FACE_TEXT
        return font.render("%s %s" % (symbol, rank), True, color)

    @staticmethod
    def _fit(metrics, size):
        width = min(metrics.px(size[0]), metrics.px(PANEL_WIDTH) // 2)
        height = min(metrics.px(size[1]), metrics.px(PANEL_HEIGHT) // 2)
        return (max(1, int(width)), max(1, int(height)))

    @staticmethod
    def _wrap(text, font, max_width):
        lines = []
        current = ""
        for char in str(text):
            if char == "\n":
                lines.append(current)
                current = ""
                continue
            probe = current + char
            if current and font.size(probe)[0] > max_width:
                lines.append(current)
                current = char
            else:
                current = probe
        if current:
            lines.append(current)
        return lines or [""]
