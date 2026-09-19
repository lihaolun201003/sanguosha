class CardSelectionMixin:

    def start_card_selection(
        self,
        zone,
        candidates,
        number,
        prompt,
        on_complete,
        owner=None,
    ):

        self.pending_selection = {
            "zone": zone,
            "owner": owner,
            "candidates": list(candidates),
            "number": number,
            "prompt": prompt,
            "selected": [],
            "on_complete": on_complete,
        }

        self._update_selection_message()

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
