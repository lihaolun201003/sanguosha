class CardSelectionMixin:

    def start_card_selection(
        self,
        zone,
        candidates,
        number,
        prompt,
        on_complete,
        owner=None,
        request_id=None,
        cancellable=False,
    ):

        candidates = list(candidates)
        self.pending_selection = {
            "zone": zone,
            "owner": owner,
            "candidates": candidates,
            "number": number,
            "prompt": prompt,
            "selected": [],
            "on_complete": on_complete,
            "request_id": request_id,
            # 选满才结束的选择（观星排序一类）也要有"放弃并保持原样"的出口，
            # 否则玩家会被卡在无法取消的选牌里。
            "cancellable": bool(cancellable),
            # 别人手牌的内容是隐藏信息：这些候选在选择界面上只显示牌背。
            "face_down_ids": self._face_down_candidate_ids(owner, candidates),
        }

        self._update_selection_message()

    def _face_down_candidate_ids(self, owner, candidates):
        """候选里必须显示牌背的那些牌（= 别人手牌的内容）。

        装备区的牌、公共牌池（五谷丰登）与判定区都是明置的，照常显示卡面；
        自己的手牌也照常显示。只有"从别人手里拿 / 弃"时，内容才是未知的。
        """

        if owner is None or owner is self.player:
            return set()
        hand = list(getattr(owner, "hand", ()) or ())
        if not hand:
            return set()
        return {
            id(card) for card, _key in candidates
            if any(card is item for item in hand)
        }

    def selection_face_down_ids(self):
        """当前选牌界面里只显示牌背的牌（供 UI 绘制）。"""

        selection = self.pending_selection
        if selection is None:
            return set()
        return set(selection.get("face_down_ids") or ())

    def is_selection_face_down(self, card):
        return id(card) in self.selection_face_down_ids()

    def selection_pool_cards(self):
        """Cards laid out in the public area for the current selection."""

        selection = self.pending_selection
        if selection is None or selection["zone"] != "public_pool":
            return []
        return [card for card, _key in selection["candidates"]]

    def selection_pool_entries(self):
        """[(card, key)] for the public area.

        The key is the equipment slot for cards taken out of another
        character's equipment zone, and must be handed back when the card is
        selected — otherwise a piece of equipment can never be picked.
        """

        selection = self.pending_selection
        if selection is None or selection["zone"] != "public_pool":
            return []
        return list(selection["candidates"])


    def _update_selection_message(self):

        selection = self.pending_selection

        if selection is None:
            return

        remaining = (
            selection["number"]
            - len(selection["selected"])
        )

        self.message = (
            selection["prompt"]
            + "（还需选择 "
            + str(remaining)
            + " 张）"
        )


    def is_selection_candidate(
        self,
        card,
        key=None
    ):

        selection = self.pending_selection

        if selection is None:
            return False

        return any(
            candidate is card
            and candidate_key == key
            for candidate, candidate_key
            in selection["candidates"]
        )


    def is_selection_selected(
        self,
        card,
        key=None
    ):

        selection = self.pending_selection

        if selection is None:
            return False

        return any(
            selected_card is card
            and selected_key == key
            for selected_card, _, selected_key
            in selection["selected"]
        )


    def select_pending_card(
        self,
        card,
        source_rect,
        key=None
    ):

        selection = self.pending_selection

        if selection is None:
            return

        request_id = selection.get("request_id")
        if request_id is not None:
            current = self.pending_request
            if current is None or current.request_id != request_id:
                return

        if not self.is_selection_candidate(
            card,
            key
        ):
            return

        for selected in list(
            selection["selected"]
        ):

            if (
                selected[0] is card
                and selected[2] == key
            ):

                selection["selected"].remove(
                    selected
                )
                self._update_selection_message()
                return

        selection["selected"].append(
            (
                card,
                tuple(source_rect),
                key,
            )
        )

        if (
            len(selection["selected"])
            < selection["number"]
        ):

            self._update_selection_message()
            return

        selected = list(selection["selected"])
        callback = selection["on_complete"]
        self.pending_selection = None
        callback(selected)


    def can_cancel_pending_selection(self):
        """只有「允许选 0 张」的请求可以整单放弃（改判窗口一类）。"""

        selection = self.pending_selection

        if selection is None:
            return False

        request_id = selection.get("request_id")
        if request_id is not None:
            current = self.pending_request
            if current is None or current.request_id != request_id:
                return False

        return selection["number"] <= 0 or bool(selection.get("cancellable"))


    def cancel_pending_selection(self):
        """放弃当前选牌：按空选择回调，语义等同引擎侧的 Pass。"""

        if not self.can_cancel_pending_selection():
            return False

        selection = self.pending_selection
        callback = selection["on_complete"]
        self.pending_selection = None
        callback([])
        return True
