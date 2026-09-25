"""General registry: stable id → GeneralDef lookup."""

from typing import Dict

from .definitions import GeneralDef


class GeneralRegistry:
    def __init__(self):
        self._generals: Dict[str, GeneralDef] = {}

    def register(self, general):
        if not isinstance(general, GeneralDef):
            raise TypeError("GeneralRegistry.register expects a GeneralDef")
        if general.id in self._generals:
            raise ValueError("general already registered: " + general.id)
        self._generals[general.id] = general
        return general

    def register_all(self, generals):
        for general in generals:
            self.register(general)
        return self

    def get(self, general_id):
        if general_id is None:
            return None
        return self._generals.get(general_id)

    def require(self, general_id):
        general = self.get(general_id)
        if general is None:
            raise KeyError("unknown general: " + str(general_id))
        return general

    def list_generals(self):
        return tuple(self._generals.values())

    def ids(self):
        return tuple(self._generals)

    def by_kingdom(self, kingdom):
        return tuple(general for general in self._generals.values() if general.kingdom == kingdom)

    def by_pack(self, pack):
        return tuple(general for general in self._generals.values() if general.pack == pack)

    def packs(self):
        """出现过的扩展包（按注册顺序，稳定）——筛选按钮直接用它。"""

        seen = []
        for general in self._generals.values():
            if general.pack not in seen:
                seen.append(general.pack)
        return tuple(seen)

    def labelled_names(self):
        """id → 带版本标签的展示名。

        只有**同名**的武将才需要版本标签（"同名不同版本需要明确标签"）；
        名字唯一的武将保持原名，界面上的名字不会被无意义的后缀污染。
        """

        counts = {}
        for general in self._generals.values():
            counts[general.name] = counts.get(general.name, 0) + 1
        labels = {}
        for general_id, general in self._generals.items():
            if counts.get(general.name, 0) > 1:
                suffix = general.version or general.pack
                labels[general_id] = general.name + "（" + suffix + "）"
            else:
                labels[general_id] = general.name
        return labels

    def __contains__(self, general_id):
        return general_id in self._generals

    def __len__(self):
        return len(self._generals)
