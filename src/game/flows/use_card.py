"""Generic card-use flow backed by the CardEffectRegistry."""

from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import Event, EventType, Flow, FlowResult, FlowStatus


class UseCardFlow(Flow):
    def __init__(self, engine, action, effect):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.action = action
        self.effect = effect
        self.actor = action.actor
        self.targets = list(action.targets)
        self.target = self.targets[0] if self.targets else None
        self.card = action.card
        self.stage = "ready"
        self.base_damage = int(action.metadata.get("base_damage", 1))
        self.extra_targets = list(action.metadata.get("extra_targets", self.targets[1:]))
        self.cancelled = False
        self.keep_processing_card = False
        #: CARD_USE_BEFORE 的 Event 对象：子流程（技能窗口）结束后还要读它的
        #: cancelled 标记，所以必须留到那一批结算走完。
        self._before_event = None

    def advance(self, response=None):
        if self.stage == "ready":
            valid, message = self.effect.can_use(self.game, self.action)
            if not valid:
                self.game.message = message
                return self.cancel(message)

            self._before_event = self.context.emit(
                Event(
                    EventType.CARD_USE_BEFORE,
                    source=self.actor,
                    target=self.target,
                    payload={"card": self.card, "flow": self},
                )
            )
            self.stage = "before"
            # 使用前技能（无谋一类）可能开一个要回答的窗口：先让它们答完，
            # 再决定这张牌是否还成立。
            guard = self.guard_child_flows()
            if guard is not None:
                return guard
        if self.stage == "before":
            if self._before_event is not None and self._before_event.cancelled:
                return self.cancel("card use cancelled")
            self._begin_use()
            self.stage = "used"
            # 使用时 / 成为目标时技能（铁骑判定、流离转移）在这批事件里开窗口。
            guard = self.guard_child_flows()
            if guard is not None:
                return guard
        if self.stage == "used":
            # 目标确定后，技能可能要求先解决一个"目标重定向"窗口（【流离】
            # 一类）：此时把本次用牌挂起，等窗口给出结论再决定打谁。
            # 这是通用扩展点，这里不认识任何具体技能。
            redirect = getattr(self, "target_redirect", None)
            if redirect is not None:
                self.target_redirect = None
                self.stage = "redirect"
                self.wait(redirect)
                self.engine.present_or_auto_resolve(redirect)
                return self.current_result()

            if self.action.metadata.get("zhuque_fire") and self.card.name == "SHA" and self.card.nature == "normal":
                # 朱雀羽扇把这张【杀】当火【杀】使用：只在本张牌结算期间改属性，
                # 结算结束必须还原，否则这张实体牌进弃牌堆后仍然是火杀。
                self.card._original_nature = self.card.nature
                self.card.nature = "fire"

            self.game.add_log(self.actor.name + " 使用【" + self.card.display_name + "】" +
                              ((" → " + "、".join(target.name for target in self.targets)) if self.targets else ""))

            # 【无懈可击】窗口只属于锦囊本身：整张牌在这里开一次，所有目标
            # 共用同一条无懈链。窗口关闭后，效果阶段（例如南蛮 / 万箭逐个
            # 目标要求【杀】/【闪】）绝不再重新打开无懈链。
            if self.effect.cancellable_by_wuxie and not self.action.metadata.get("skip_wuxie"):
                from .wuxie import WuxieResponseChain
                self.stage = "wuxie"
                chain = WuxieResponseChain(
                    self.engine,
                    self.actor,
                    self.card,
                    self.targets,
                    self._after_wuxie,
                )
                self.wait(chain)
                chain.start()
                return self.current_result()

            self.stage = "effect"
            return self.effect.begin(self)

        if self.stage == "effect_prelude":
            # 效果开始前的技能窗口（啖酪一类）结束：回到效果前奏继续。
            return self.effect.resume_after_child(self, response)

        if self.stage in {
            "waiting_for_shan", "effect_waiting", "bagua_confirm",
            "qinglong_confirm", "qinglong_select", "guanshi_confirm",
            "guanshi_select", "hanbing_confirm", "hanbing_select",
            "cixiong_option", "cixiong_select", "qilin_confirm", "qilin_select",
        }:
            return self.effect.resume(self, response)

        if self.stage == "redirect":
            # 目标重定向窗口给出了结论：交给登记它的技能去改目标，
            # 然后照常进入效果阶段（原杀不取消、不重建）。
            resolver = getattr(self, "redirect_resolver", None)
            self.redirect_resolver = None
            keep_going = True
            if resolver is not None:
                keep_going = resolver(self, response) is not False
            if not keep_going:
                return self.current_result()
            self.stage = "effect"
            return self.effect.begin(self)

        if self.stage == "waiting_for_damage":
            return FlowResult(self.status, self.result)

        raise RuntimeError("UseCardFlow cannot advance from stage " + self.stage)

    def _begin_use(self):
        """使用动作落地：移动实体牌 + 发出"已使用 / 成为目标"事件。"""

        for material in self.action.metadata.get("materials", ()):
            self.context.apply(
                MoveCardAtom(
                    material,
                    source=self.actor.hand,
                    destination=self.game.deck.discard_pile,
                )
            )

        # 技能转化出来的虚拟牌：真正移动的是它的实体源牌。
        material_sources = self.material_cards
        if material_sources and material_sources != [self.card]:
            for source_card in material_sources:
                self.game.move_source_card_to_processing(self.actor, source_card)
        elif not getattr(self.card, "_virtual", False):
            self.game.move_source_card_to_processing(self.actor, self.card)
        self.engine.animate_card_use(self.action)

        if self.card.name == "SHA":
            self.actor.sha_used = True
            if self.actor.wine_buff:
                self.base_damage += 1
                self.actor.wine_buff = False
            self.actor.wine_sha_required = False
            self.game.message = (
                ("你使用了【" if self.actor.is_human else self.actor.name + "使用了【")
                + self.card.display_name + "】。"
            )

        self.context.emit(Event(EventType.CARD_USED, source=self.actor, payload={
            "card": self.card,
            "flow": self,
            "targets": self.targets,
            # 逐目标响应型效果：箭头由每个目标的响应请求分别驱动，
            # FX 不在这里一次性铺满全场。
            "sequential_targets": bool(getattr(self.effect, "sequential_targets", False)),
        }))
        for target in self.targets:
            for event_type in (EventType.TARGET_SELECTED, EventType.BECOME_TARGET):
                self.context.emit(Event(event_type, source=self.actor, target=target, payload={"card": self.card, "flow": self}))

    def _after_wuxie(self, nullified):
        if self.status in (FlowStatus.COMPLETED, FlowStatus.CANCELLED):
            return self.current_result()
        self.status = FlowStatus.RUNNING
        self.pending_request = None
        if nullified:
            self.game.message = "【" + self.card.display_name + "】被【无懈可击】抵消。"
            return self.finish(cancelled=True)
        self.stage = "effect"
        return self.effect.begin(self)

    @property
    def material_cards(self):
        """这次使用真正动过的**实体牌**。

        View-As（武圣 / 断粮一类）用的是虚拟牌，它在任何区域里都不存在——
        进处理区、进判定区、进弃牌堆的都是它的**实体素材**。任何需要"移动
        这次打出的牌"的组件都必须走这个查询，直接动 ``self.card`` 会在
        虚拟牌上抛 "card is no longer in the expected source zone"。
        """

        sources = list(getattr(self.card, "source_cards", ()) or ())
        return sources or [self.card]

    def emit_card_event(self, event_type, target=None, **payload):
        return self.context.emit(
            Event(event_type, source=self.actor, target=target, payload={"card": self.card, "flow": self, **payload})
        )

    def start_damage(self):
        from .damage import DamageContext, DamageFlow

        equipment_result = self.engine.equipment.before_sha_damage(self)
        if equipment_result is not None:
            return equipment_result
        self.stage = "waiting_for_damage"
        damage = DamageContext(
            source=self.actor,
            target=self.target,
            amount=self.base_damage,
            nature=getattr(self.card, "nature", "normal"),
            card=self.card,
        )
        child = DamageFlow(
            self.engine,
            damage,
            on_complete=self._after_damage,
        )
        child_result = child.start()
        if child_result.status is FlowStatus.WAITING:
            return self.wait(child)
        return FlowResult(self.status, self.result)

    def _after_damage(self, _result):
        if self.status not in (FlowStatus.COMPLETED, FlowStatus.CANCELLED):
            post = self.engine.equipment.after_sha_damage(self)
            if post is None:
                self.finish(cancelled=False)

    def finish(self, *, cancelled):
        if self.status is FlowStatus.COMPLETED:
            return FlowResult(self.status, self.result)
        # 朱雀羽扇改过的属性在结算结束时还原：实体牌回到弃牌堆后仍是普通【杀】。
        original_nature = getattr(self.card, "_original_nature", None)
        if original_nature is not None:
            self.card.nature = original_nature
            self.card._original_nature = None
        material_sources = self.material_cards
        self.cancelled = cancelled
        self.context.emit(
            Event(
                EventType.CARD_EFFECT_AFTER,
                source=self.actor,
                target=self.target,
                payload={
                    "card": self.card,
                    "flow": self,
                    "cancelled": cancelled,
                },
            )
        )
        if self.keep_processing_card:
            # 装备牌进了装备槽：桌面副本由 EquipEffect 自己收尾。
            pass
        elif material_sources:
            for source_card in material_sources:
                if not any(item is source_card for item in self.game.processing_zone):
                    # 已被技能取走（奸雄 / 天妒一类）：不再移动，但要收掉桌面副本。
                    self.game.remove_table_card(source_card)
                    continue
                self.context.apply(MoveCardAtom(
                    source_card,
                    source=self.game.processing_zone,
                    destination=self.game.deck.discard_pile,
                ))
            # 出牌动画展示的是虚拟牌本身，它没有归属，直接收掉。
            self.game.remove_table_card(self.card)
        elif getattr(self.card, "_virtual", False):
            self.game.remove_table_card(self.card)
        else:
            self.engine.discard_processing_card(self.card)
        self.context.emit(
            Event(
                EventType.CARD_USE_FINISHED,
                source=self.actor,
                target=self.target,
                payload={
                    "card": self.card,
                    "flow": self,
                    "cancelled": cancelled,
                },
            )
        )
        result = self.complete(
            {"card": self.card, "cancelled": cancelled}
        )
        return result

    def on_settled(self, result):
        # 用牌动作的回调只在这里接：complete() 被子流程挡住时那次不算数，
        # 否则 AI 会在技能窗口还没答完时就接着出下一张牌。
        if self.action.on_complete is not None and not self.game.game_over:
            self.action.on_complete(result)
