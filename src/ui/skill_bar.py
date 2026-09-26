"""Skill UI: 真人技能区（真实技能名 + 说明）、技能选择面板。

技能区完全数据驱动：技能列表、名字、描述、类型全部来自 ``SkillDef``，
UI 不认识任何具体武将或技能。加武将 / 改技能说明都不需要动这里。
"""

import pygame

from . import layout, theme
from .widgets import Button, draw_panel, draw_state_border, ellipsize_text

PICKER_WIDTH = 820
PICKER_ROW_HEIGHT = 92
PICKER_HEADER = 96
PICKER_FOOTER = 84

# SkillKind → 中文类型名（只用于说明面板）。
KIND_LABELS = {
    "active": "主动技",
    "view_as": "视为技",
    "locked": "锁定技",
    "passive": "触发技",
}

SKILL_BUTTON_HEIGHT = 44
SKILL_BUTTON_GAP = 8


class SkillBar:
    """真人技能区：每个技能一个按钮，显示**真实技能名**。

    状态区分（全部来自 SkillDef 与引擎查询，不猜）：

    * ``主动技 / 视为技``：当前可发动时高亮可点；不可发动时变暗但仍可见。
    * ``锁定技 / 触发技``：只作为"查看说明"的入口，不伪装成发动按钮。
    * 点击任何技能名都会展开该技能的说明（名字 / 类型 / 描述）。
    """

    def __init__(self):
        self.skills = []          # [SkillDef]
        self.rects = []           # [(skill_id, pygame.Rect)]
        self.enabled_ids = set()  # 当前真的可发动的技能
        self.blocked_reasons = {}  # skill_id -> 不能发动的原因
        #: 已经**点下去**的技能（View-As / 主动技交互中）：只用于按钮的
        #: 选中视觉，与"鼠标正停在哪个技能上"完全分开——技能说明只跟 hover。
        self.selected_skill_id = None
        self.metrics = None
        self.reason = ""
        self.sync_layout(None, 0)

    # ---- 布局 ----

    def sync_layout(self, metrics=None, count=None):
        self.metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        if count is not None:
            self._layout(count)
        return self

    def _layout(self, count):
        metrics = self.metrics
        anchor = metrics.primary_button
        height = metrics.px(SKILL_BUTTON_HEIGHT)
        gap = metrics.px(SKILL_BUTTON_GAP)
        count = max(0, int(count))
        self.rects = []
        if not count:
            return
        total = count * height + (count - 1) * gap
        top = anchor.y - metrics.px(14) - total
        for index in range(count):
            rect = pygame.Rect(
                anchor.x, top + index * (height + gap), anchor.width, height)
            self.rects.append(rect)

    # ---- 状态同步（每帧由 Renderer 调用）----

    def sync(self, game):
        """按当前武将的真实技能列表刷新按钮与可发动状态。"""

        player = game.player
        definitions = []
        if player is not None:
            # 数据来源是引擎的绑定表（武将技能 + 装备技能 + 任何额外绑定），
            # UI 不维护自己的技能名单。
            for skill_id in game.skills.skill_ids_of(player):
                definition = game.skill_registry.get(skill_id)
                if definition is not None:
                    definitions.append(definition)

        if [item.id for item in definitions] != [item.id for item in self.skills]:
            self.skills = definitions
            if self.selected_skill_id and self.selected_skill_id not in [
                    item.id for item in definitions]:
                self.selected_skill_id = None
            self.sync_layout(self.metrics, len(definitions))

        # 联网客户端：技能名照常显示（武将技能是公开信息），但"能不能发动"
        # 一律由房主的 DecisionRequest 决定，客户端不自己判定。
        readonly = not getattr(game, "local_interaction", True)
        context_ok = False if readonly else (
            game.current_card_action_context() is not None)
        busy = bool(game.busy)
        blocked_by_pending = (
            game.pending_skill_input is not None
            or game.pending_view_as is not None
            or bool(game.pending_skill_picker)
        )
        self.enabled_ids = set()
        self.blocked_reasons = {}
        self.available = []
        for definition in definitions:
            if not (definition.is_active or definition.is_view_as):
                continue
            if readonly:
                # 联网客户端：技能名照常显示，"能不能按"由房主的决策请求决定
                # （``RemoteGameView.skill_activation_state``）。房主说能按，
                # 按钮就亮——客户端一条规则都不自己算。
                resolver = getattr(game, "skill_activation_state", None)
                allowed, reason = (
                    resolver(definition.id) if callable(resolver)
                    else (False, "由房主判定"))
                self.available.append((definition.id, bool(allowed), str(reason)))
                if allowed:
                    self.enabled_ids.add(definition.id)
                else:
                    self.blocked_reasons[definition.id] = str(reason) or "当前无法发动"
                continue
            allowed, reason = self._evaluate(game, definition)
            self.available.append((definition.id, allowed, reason))
            if allowed and context_ok and not busy and not blocked_by_pending:
                self.enabled_ids.add(definition.id)
            else:
                self.blocked_reasons[definition.id] = reason or "当前无法发动"
        self.reason = ""
        if self.available and not any(item[1] for item in self.available):
            self.reason = self.available[0][2] or ""
        # 兼容旧调用点（tooltip / 测试）：
        self.button.enabled = bool(self.enabled_ids)
        return self

    @staticmethod
    def _evaluate(game, definition):
        if definition.is_active:
            allowed, reason = game.skills.can_activate(game.player, definition.id)
            return bool(allowed), str(reason)
        # 视为技：查 view_as_options（纯查询，不会进入选牌模式）。
        try:
            for skill_id, allowed, reason in game.view_as_options():
                if skill_id == definition.id:
                    return bool(allowed), str(reason or "")
        except Exception:  # pragma: no cover - 防御：查询失败就当不可发动
            return False, ""
        return False, ""

    # ---- 交互 ----

    def rect_for(self, skill_id):
        """某个技能按钮的 rect（供调用方定位；不存在返回 None）。"""

        for index, definition in enumerate(self.skills):
            if definition.id == skill_id and index < len(self.rects):
                return pygame.Rect(self.rects[index])
        return None

    def skill_at(self, position):
        for index, rect in enumerate(self.rects):
            if rect.collidepoint(position) and index < len(self.skills):
                return self.skills[index]
        return None

    def hit(self, position, game):
        """返回要执行的动作。

        技能说明**只由鼠标 hover 决定**（见 ``tooltip``）：点击不再"展开"
        任何常驻文案——鼠标一离开按钮，说明就消失。锁定技 / 触发技没有可
        执行的动作，点击它们什么也不发生（hover 仍然能看到说明）。
        """

        self.sync(game)
        definition = self.skill_at(position)
        if definition is None:
            return None
        if definition.id in self.enabled_ids:
            # 主动技与视为技共用一个入口：start_skill_activation 自己会区分
            # （视为技进入"先点技能再选牌"模式）。
            self.selected_skill_id = definition.id
            return ("skill", definition.id)
        return None

    def _describe(self, game, definition):
        """一个技能的说明文本（类型 / 描述 / 当前不可发动的原因）。"""

        kind = KIND_LABELS.get(getattr(definition.kind, "value", ""), "技能")
        if definition.is_lord_skill:
            kind += " · 主公技"
        reason = self.blocked_reasons.get(definition.id)
        lines = ["【%s】  %s" % (definition.name, kind), definition.description or "暂无说明。"]
        if reason:
            lines.append("当前无法发动：" + reason)
        return "\n".join(lines)

    def skill_at_position(self, mouse_pos):
        """鼠标现在停在哪个技能按钮上（不在技能上就是 None）。"""

        if mouse_pos is None:
            return None
        return self.skill_at(mouse_pos)

    def tooltip(self, game, mouse_pos=None):
        """悬停提示：**只有**鼠标停在技能按钮上时才有内容。

        点击技能（进入 View-As / 主动技交互）**不会**让说明常驻——"已经
        选了它"与"正在看它的说明"是两件事：前者是游戏交互状态
        （``selected_skill_id``，只影响按钮的选中视觉），后者只由鼠标位置
        决定。鼠标离开按钮、停在手牌 / 座位 / 技能栏空白处，说明立刻消失。
        """

        self.sync(game)
        definition = self.skill_at_position(mouse_pos)
        if definition is None:
            return None
        return self._describe(game, definition)

    # ---- 绘制 ----

    def draw(self, surface, game, mouse_pos=None):
        self.sync(game)
        if not self.rects:
            return
        fonts = self.metrics.fonts

        for index, definition in enumerate(self.skills):
            if index >= len(self.rects):
                break
            rect = self.rects[index]
            enabled = definition.id in self.enabled_ids
            viewing = self.selected_skill_id == definition.id
            hovered = mouse_pos is not None and rect.collidepoint(mouse_pos)

            actionable = definition.is_active or definition.is_view_as
            if actionable and enabled:
                fill = (150, 116, 46)
                border = theme.GOLD_BRIGHT
                text_color = (252, 244, 222)
            elif actionable:
                # 存在但当前不可发动：变暗，不假装可用。
                fill = (48, 58, 70)
                border = (96, 108, 122)
                text_color = theme.DISABLED_TEXT
            else:
                # 锁定技 / 触发技：只作为查看说明的入口。
                fill = (38, 50, 64)
                border = theme.GOLD_DIM
                text_color = theme.TEXT_DIM

            pygame.draw.rect(surface, (8, 11, 15), rect.move(0, 3),
                             border_radius=theme.RADIUS_BUTTON)
            pygame.draw.rect(surface, fill, rect, border_radius=theme.RADIUS_BUTTON)
            pygame.draw.rect(surface, border, rect, 2,
                             border_radius=theme.RADIUS_BUTTON)

            label = ellipsize_text(
                "【%s】" % definition.name, fonts.get("normal"),
                rect.width - self.metrics.px(18))
            rendered = fonts.get("normal").render(label, True, text_color)
            surface.blit(rendered, rendered.get_rect(center=rect.center))

            if actionable and enabled:
                # 可发动：金色底 + "playable" 描边，靠按钮本身说话。
                draw_state_border(surface, rect, "playable",
                                  radius=self.metrics.px(theme.RADIUS_BUTTON),
                                  alpha=175)
            elif hovered or viewing:
                draw_state_border(surface, rect, "hover",
                                  radius=self.metrics.px(theme.RADIUS_BUTTON),
                                  alpha=90)

    # ---- 兼容旧接口 ----

    def __getattr__(self, name):
        if name == "button":
            # 老代码（含既有测试）会读 skill_bar.button 当作"发动技能"入口。
            # 这里指向**第一个当前可发动的技能**，保持旧语义；没有可发动的
            # 技能时指向第一个技能但标记为不可用。
            stub = Button(pygame.Rect(0, 0, 10, 10), "技能",
                          kind="secondary", font="normal")
            index = 0
            for position, definition in enumerate(self.skills):
                if definition.id in self.enabled_ids:
                    index = position
                    break
            if index < len(self.rects):
                stub.set_rect(self.rects[index])
            stub.enabled = bool(self.enabled_ids)
            # 不缓存：兼容入口必须始终反映最新状态（技能列表 / 可发动性 /
            # 分辨率都会变，缓存一份过期 rect 会让调用方点到空气）。
            return stub
        raise AttributeError(name)



class SkillPicker:
    """多个主动技能时的统一选择面板（完全数据驱动）。"""

    def __init__(self):
        self.rects = []
        self.cancel_button = Button(pygame.Rect(0, 0, 10, 10), "取消", kind="ghost", font="normal")
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.metrics = None
        self.sync_layout(None)

    def sync_layout(self, metrics=None, count=1):
        self.metrics = metrics or layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        metrics = self.metrics
        width = min(metrics.px(PICKER_WIDTH), int(metrics.screen_w * 0.6))
        rows = max(1, count)
        height = metrics.px(PICKER_HEADER + PICKER_ROW_HEIGHT * rows + PICKER_FOOTER)
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        self.rects = []
        for index in range(count):
            self.rects.append(pygame.Rect(
                self.panel_rect.x + metrics.px(40),
                self.panel_rect.y + metrics.px(PICKER_HEADER) + index * metrics.px(PICKER_ROW_HEIGHT),
                self.panel_rect.width - metrics.px(80),
                metrics.px(PICKER_ROW_HEIGHT - 16),
            ))
        self.cancel_button.rect = pygame.Rect(
            self.panel_rect.centerx - metrics.px(90),
            self.panel_rect.bottom - metrics.px(66),
            metrics.px(180),
            metrics.px(50),
        )
        return self

    def hit(self, position, game):
        """返回 ("skill", skill_id) / "cancel" / None（面板打开时吞掉其它点击）。"""

        picker = game.pending_skill_picker
        if not picker:
            return None
        self.sync_layout(self.metrics, len(picker))
        for (skill_id, _allowed, _reason), rect in zip(picker, self.rects):
            if rect.collidepoint(position):
                return ("skill", skill_id)
        if self.cancel_button.contains(position):
            return "cancel"
        return "swallow"

    def draw(self, surface, game, mouse_pos=None):
        picker = game.pending_skill_picker
        if not picker:
            return
        self.sync_layout(self.metrics, len(picker))
        metrics = self.metrics
        fonts = metrics.fonts

        veil = pygame.Surface((metrics.screen_w, metrics.screen_h), pygame.SRCALPHA)
        veil.fill((6, 9, 13, 168))
        surface.blit(veil, (0, 0))

        draw_panel(surface, self.panel_rect, fill=theme.PANEL, border=theme.GOLD,
                   border_width=theme.BORDER_THICK, radius=metrics.px(16))

        title = fonts.get("large").render("选择要发动的技能", True, theme.GOLD_BRIGHT)
        surface.blit(title, title.get_rect(
            center=(self.panel_rect.centerx, self.panel_rect.y + metrics.px(48))))

        for (skill_id, allowed, reason), rect in zip(picker, self.rects):
            definition = game.skill_registry.get(skill_id)
            if definition is None:
                continue
            self._draw_row(surface, definition, rect, allowed, reason, mouse_pos, metrics)

        self.cancel_button.draw(surface, fonts, mouse_pos)

    def _draw_row(self, surface, definition, rect, allowed, reason, mouse_pos, metrics):
        fonts = metrics.fonts
        hovered = allowed and rect.collidepoint(mouse_pos)
        fill = theme.PANEL_ALT if hovered else theme.PANEL_DEEP
        border = theme.TARGET_BLUE if allowed else (86, 92, 100)
        draw_panel(surface, rect, fill=fill, border=border,
                   border_width=theme.BORDER, radius=metrics.px(10))

        pad = metrics.px(18)
        name_color = theme.TEXT if allowed else theme.TEXT_MUTED
        name = fonts.get("normal").render("【" + definition.name + "】", True, name_color)
        surface.blit(name, (rect.x + pad, rect.y + metrics.px(10)))

        desc_font = fonts.get("small")
        info = definition.description if allowed else (reason or "当前不可发动")
        text = ellipsize_text(info, desc_font, rect.width - pad * 2)
        surface.blit(desc_font.render(text, True, theme.TEXT_DIM), (rect.x + pad, rect.y + metrics.px(44)))
