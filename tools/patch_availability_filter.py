"""补丁 3：统一的武将可用性过滤（本地 / 随机 / AI / 身份局 / 联机候选池）。

运行方式（在项目根目录）：
    .venv/Scripts/python.exe tools/patch_availability_filter.py
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(relative_path, pairs):
    path = os.path.join(ROOT, *relative_path.split("/"))
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("  跳过（未命中）：" + relative_path + " :: "
                  + old.strip().splitlines()[0][:70])
            continue
        text = text.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8").write(text)
    print("已更新 " + relative_path)


patch("src/game/core.py", [
    # --- 新的统一查询 ---
    (
        """    def general_pool_ids(self):
        \"\"\"当前模式可用的武将池（按注册顺序，稳定）。\"\"\"

        pool = [general_id for general_id in (self.general_pool or ()) if general_id]
        return tuple(pool) if pool else tuple(self.generals.ids())""",
        '''    # ==================================================
    # 武将可用性：**唯一**的判断入口
    #
    # "界面可浏览"与"对局可使用"是两件事：
    #   * 注册表（generals.list_generals）永远列出全部条目，界面照着它显示，
    #     玩家能读技能说明、能看到"为什么不能选"；
    #   * 对局可用的池子只包含"已实现 + 当前模式允许 + 允许随机抽取"的条目。
    # 本地候选、随机按钮、AI 分配、身份局、联机候选池与权威端选将校验
    # 全部走下面这几个方法，任何调用方都不许自己判断 implemented。
    # ==================================================

    def general_available(self, general_id, *, for_random=False):
        """(能不能在本模式里开局, 原因)；`for_random` 时额外要求能进随机池。\"\"\"

        general = self.generals.get(general_id)
        if general is None:
            return False, "武将不存在：" + str(general_id)
        if for_random:
            return general.random_eligible_for(self.mode_id), (
                "" if general.random_eligible_for(self.mode_id)
                else "该武将不允许随机分配。")
        return general.availability_for(self.mode_id)

    def playable_generals(self, *, for_random=False):
        \"\"\"当前模式**对局可用**的武将定义（按注册顺序，稳定）。\"\"\"

        return tuple(
            general for general in self.generals.list_generals()
            if self.general_available(
                general.id, for_random=for_random)[0]
        )

    def playable_general_ids(self, *, for_random=False):
        return tuple(general.id for general in self.playable_generals(for_random=for_random))

    def general_pool_ids(self):
        """当前模式可用的武将池（按注册顺序，稳定）。

        显式指定的 ``general_pool`` 同样要过滤：它是选将 / 联机传进来的，
        里面可能夹着"这条不该在这一局出现"的条目（未实现、只允许在测试模式
        显式选择的形态）。过滤放在这里，随机抽取与 AI 分配就自动安全。
        \"\"\"

        pool = [general_id for general_id in (self.general_pool or ()) if general_id]
        if not pool:
            return self.playable_general_ids(for_random=True)
        return tuple(
            general_id for general_id in pool
            if self.general_available(general_id, for_random=True)[0]
        )''',
    ),
    # --- 选将界面 ---
    (
        """        candidates = tuple(self.general_candidates)
        if not candidates:
            return self.generals.list_generals()
        order = {general_id: index for index, general_id in enumerate(candidates)}
        playable = [general for general in self.generals.list_generals() if general.id in order]
        playable.sort(key=lambda general: order[general.id])
        return tuple(playable)""",
        """        candidates = tuple(self.general_candidates)
        if not candidates:
            # 没有候选池时（旧路径）退回"本模式对局可用"的全部武将，
            # 而不是注册表里的全部条目——否则选将界面会给出未实现的武将。
            return self.playable_generals(for_random=True)
        order = {general_id: index for index, general_id in enumerate(candidates)}
        playable = [
            general for general in self.playable_generals(for_random=True)
            if general.id in order
        ]
        playable.sort(key=lambda general: order[general.id])
        return tuple(playable)""",
    ),
    # --- confirm_general 的隐式池 ---
    (
        """        if not self.general_pool:
            self.general_pool = tuple(self.generals.ids())""",
        """        if not self.general_pool:
            self.general_pool = self.playable_general_ids(for_random=True)""",
    ),
    # --- AI 分配：池子再过滤一次（general_assignments / 联机 picks 可能直接写 id）---
    (
        """        assignments = dict(self.general_assignments if mapping is None else mapping)
        pool = [general_id for general_id in (self.general_pool or ()) if general_id]""",
        """        assignments = dict(self.general_assignments if mapping is None else mapping)
        pool = [general_id for general_id in (self.general_pool or ()) if general_id]
        # 显式指定（选将结果 / 联机 picks / 直通入口）也要过一遍可用性：
        # 一条非法 id 混进来会让整局在绑定技能时炸掉，而不是被安静地跳过。
        pool = [general_id for general_id in pool
                if self.general_available(general_id)[0]]
        assignments = {
            key: value for key, value in assignments.items()
            if value is None or self.general_available(value)[0]
        }""",
    ),
])

# 联机的默认武将池同样走过滤后的入口
patch("src/network/match.py", [(
    """        mode = getattr(self.game, "mode", None)
        if mode is not None and getattr(mode, "uses_identities", False):
            return tuple(self.game.generals.ids())
        return ()""",
    """        mode = getattr(self.game, "mode", None)
        if mode is not None and getattr(mode, "uses_identities", False):
            # 身份局必须发武将；发给玩家与随机分配的池子一律走
            # **同一份可用性过滤**，否则联机侧会把未实现的武将发给真人。
            return self.game.playable_general_ids(for_random=True)
        return ()""",
)])

print("补丁 3 应用完成")
