"""通用判定展示面板（Judge Presentation Panel）。

所有判定共用这一个面板：延时锦囊、装备技能、武将技能都只是
``judge_presentation.JUDGE_SOURCES`` 里的一条声明，面板本身不认识任何
具体卡名或技能名。

生命周期（全部由 dt 驱动，**绝不 sleep、绝不额外抽牌**）：

    OPEN → SOURCE_HOLD → DRAW_ANIMATION → REVEALED_HOLD
         →（可多次 REPLACEMENT，鬼才一类改判）
         → FINAL_RESULT → OUTCOME_HOLD → FADE_OUT → DONE

判定牌永远来自引擎已经移动过的实体牌（``JudgeResult.card`` /
``JudgeContext.current_card``），面板只表现它，不产生任何牌。
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


class JudgeStage(str, Enum):
    OPEN = "open"
    SOURCE_HOLD = "source_hold"
    DRAW_ANIMATION = "draw_animation"
    REVEALED_HOLD = "revealed_hold"
    REPLACEMENT = "replacement"
    FINAL_RESULT = "final_result"
    OUTCOME_HOLD = "outcome_hold"
    FADE_OUT = "fade_out"
    DONE = "done"


def _timing():
    from .fx import timing

    return timing()


class JudgePanel:
    """一次判定的展示状态机；同一时刻只展示一次判定（引擎也是串行的）。"""

    def __init__(self):
        self.active = False
        self.stage = JudgeStage.DONE
        self.metrics = None
        # 展示数据
        self.reason = ""
        self.spec = None
        self.owner = None
        self.revealed_card = None
        self.final_card = None
        self.previous_card = None       # 最近一次被替换掉的旧判定牌
        self.replacement_history = ()
        self.outcome = None
        self.result = None
        self.replacement_skill_name = ""
        self.replacement_actor = None
        # 节奏
        self.timer = 0.0
        self.hold_elapsed = 0.0
        self.alpha = 255
        self.draw_progress = 0.0        # 0→1 的翻牌动画进度

    # ==================================================
    # 事件入口
    # ==================================================

    def begin(self, result):
        """判定开始并翻开判定牌（JUDGE_REVEALED）。"""

        if result is None:
            return self
        self.active = True
        self.stage = JudgeStage.OPEN
        self.timer = _timing().judge_open
        self.hold_elapsed = 0.0
        self.alpha = 255
        self.draw_progress = 0.0
        self.reason = getattr(result, "reason", "") or ""
        self.spec = getattr(result, "source_spec", None)
        self.owner = getattr(result, "target", None) or getattr(result, "source", None)
        self.revealed_card = getattr(result, "card", None)
        self.final_card = None
        self.previous_card = None
        self.replacement_history = tuple(getattr(result, "replacement_history", ()) or ())
        self.outcome = None
        self.result = None
        self.replacement_skill_name = ""
        self.replacement_actor = None
        return self

    def note_replacement(self, payload, game=None):
        """改判发生：保留面板，换成新的判定牌并标出"被改过"。"""

        if not self.active:
            return self
        new_card = payload.get("new_card")
        old_card = payload.get("old_card")
        if new_card is None:
            return self
        self.previous_card = old_card
        self.revealed_card = new_card
        self.replacement_history = tuple(payload.get("history") or ())
        self.replacement_actor = payload.get("player")
        skill_id = payload.get("skill_id") or ""
        self.replacement_skill_name = self._skill_name(game, skill_id)
        self.stage = JudgeStage.REPLACEMENT
        self.timer = _timing().judge_replacement
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
            self.draw_progress = 0.0 if timing.judge_draw <= 0 else min(
                1.0, 1.0 - max(0.0, self.timer) / timing.judge_draw)
            if self.timer <= 0:
                self.draw_progress = 1.0
                self._enter(JudgeStage.REVEALED_HOLD, timing.judge_revealed_hold)
        elif self.stage is JudgeStage.REPLACEMENT:
            if self.timer <= 0:
                self._enter(JudgeStage.REVEALED_HOLD, timing.judge_revealed_hold)
        elif self.stage is JudgeStage.REVEALED_HOLD:
            if self.result is not None:
                if self.timer <= 0:
                    self._enter(JudgeStage.FINAL_RESULT, timing.judge_final)
            elif self.hold_elapsed > MAX_HOLD:
                # 兜底：判定流程异常结束也不会让面板永远挂着。
                self._enter(JudgeStage.FADE_OUT, timing.judge_fade_out)
            else:
                # 改判窗口开着：保持展示，等引擎给出最终结果。
                self.timer = max(self.timer, timing.judge_revealed_hold)
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

    def _enter(self, stage, duration):
        self.stage = stage
        self.timer = duration
        return self

    # ---- 只读查询（供测试与布局）----

    @property
    def shown_card(self):
        """当前应该展示的判定牌：最终牌优先，其次最新的判定牌。"""

        return self.final_card or self.revealed_card

    @property
    def was_replaced(self):
        return bool(self.replacement_history)

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
        if card is None or self.stage in (JudgeStage.OPEN, JudgeStage.SOURCE_HOLD,
                                          JudgeStage.DRAW_ANIMATION):
            pending = fonts.get("normal").render("判定中……", True, theme.TEXT)
            layer.blit(pending, (rect.x, y))
            if card is not None and self.stage is JudgeStage.DRAW_ANIMATION:
                self._draw_card_flight(layer, card, rect, metrics, y)
            return

        card_top = y
        size = self._fit(metrics, JUDGE_CARD_SIZE)
        target = pygame.Rect(rect.x + metrics.px(6), card_top, size[0], size[1])
        self._draw_judge_card(layer, card, target, metrics)

        text_x = target.right + metrics.px(18)
        text_w = max(metrics.px(120), rect.right - text_x)
        self._draw_judge_text(layer, game, card, text_x, card_top, text_w, metrics)

    def _draw_card_flight(self, layer, card, rect, metrics, top):
        """抽牌动画：把判定牌从右侧外沿滑进来（纯表现，牌早已被引擎抽好）。"""

        size = self._fit(metrics, JUDGE_CARD_SIZE)
        progress = max(0.0, min(1.0, self.draw_progress))
        start_x = rect.right + metrics.px(40)
        target = pygame.Rect(0, 0, size[0], size[1])
        target.topleft = (int(start_x + (rect.x + metrics.px(6) - start_x) * progress), top)
        card_draw.draw_card(layer, card, target, metrics.fonts)

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

        if self.was_replaced:
            # 面板里放不下第二张卡：用一行文字交代"原来翻出的是什么"，
            # 玩家依然能看出判定牌被换过、从什么换成了什么。
            actor = getattr(self.replacement_actor, "name", "")
            skill = self.replacement_skill_name or "改判"
            note = fonts.get("small").render(
                "%s 发动【%s】改判（共 %d 次）" % (actor, skill, len(self.replacement_history)),
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
            title = fonts.get("normal").render(self.outcome.title, True, tone_color)
            layer.blit(title, (x, cursor))
            cursor += title.get_height() + metrics.px(4)
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
