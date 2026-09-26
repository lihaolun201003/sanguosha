"""本机偏好（长期配置）：目前是「我的武将池」。

# 它不是什么

* **不是**某一局的游戏状态：不挂 ``Game``、不进存档、不进 revision / 快照 /
  DecisionRequest，也**不会**被 LAN 广播给其他玩家；
* **不是**武将数据：这里只保存 canonical general id（``"zhaoyun"`` 这种），
  绝不复制 ``GeneralDef`` / 技能描述——那些永远从 ``GeneralRegistry`` 读。

# 存哪儿

仓库根目录的 ``user_preferences.json``（已 gitignore）。文件不存在、内容损坏、
字段类型不对，一律**退回默认值**并继续运行——偏好坏了不能让游戏起不来。

# 默认值

从未配置过 → ``favorite_generals`` 为 ``None``（"还没配置"），由
``game.generals.draft`` 解释成"当前所有可用武将"。
一旦玩家手动保存过，就尊重这份配置：之后新增武将**不会**自动塞进池子。
"""

import json
import os
import tempfile

#: 配置文件位置（仓库根目录）。
FILE_NAME = "user_preferences.json"

#: 这份配置的版本号：以后格式变了可以据此迁移。
SCHEMA_VERSION = 1

#: 认识的顶层字段（未知字段原样保留，不丢别人的数据）。
_KNOWN_KEYS = ("version", "favorite_generals")


def project_root():
    """仓库根目录（本文件 → src/settings/ → src/ → 仓库根）。"""

    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


#: 环境变量：把偏好文件指到别处（UI 验收 / 探针用，避免动到玩家的真实配置）。
PATH_ENV = "SGS_PREFERENCES"


def default_path():
    override = (os.environ.get(PATH_ENV) or "").strip()
    if override:
        return override
    return os.path.join(project_root(), FILE_NAME)


def _clean_ids(values):
    """一串候选 id → 去重、去空、保序的字符串元组。

    只做**格式**层面的整理：id 是否存在由调用方拿 ``GeneralRegistry`` 校验
    （``game.generals.draft``）。这样即使配置里留着已被移除的武将，也只是
    被过滤掉，不会让任何一次读取抛异常。
    """

    result = []
    for value in values or ():
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


class Preferences:
    """本机偏好读写（纯数据，不 import 任何 UI / 引擎模块）。"""

    def __init__(self, path=None):
        self.path = path or default_path()
        self.data = {}
        self.load_error = ""
        self.save_error = ""
        self.load()

    # ==================================================
    # 读写
    # ==================================================

    def load(self):
        """读配置文件；任何异常都退回空配置（并记下原因）。"""

        self.data = {}
        self.load_error = ""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return self
        except (OSError, ValueError) as error:
            # 损坏的文件不该让游戏起不来：记下原因、按默认值跑。
            self.load_error = "%s: %s" % (type(error).__name__, error)
            return self
        if isinstance(payload, dict):
            self.data = payload
        else:
            self.load_error = "配置文件顶层不是对象"
        return self

    def save(self):
        """写回磁盘（先写临时文件再替换，避免写一半把配置写坏）。"""

        self.save_error = ""
        payload = dict(self.data)
        payload["version"] = SCHEMA_VERSION
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, dir=directory,
                prefix=".prefs-", suffix=".tmp")
            try:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.replace(handle.name, self.path)
        except OSError as error:
            self.save_error = "%s: %s" % (type(error).__name__, error)
            return False
        return True

    # ==================================================
    # 通用字段
    # ==================================================

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        return value

    # ==================================================
    # 我的武将池
    # ==================================================

    def favorite_general_ids(self):
        """配置里的武将池（原样，含可能已失效的 id）。

        返回空元组表示"没有配置过"，与"配置成空"是两件事——但两者在
        ``draft.resolve_pool`` 里都会被解释成"退回全部可用武将"，所以不会
        出现"没法开局"的状态。
        """

        return _clean_ids(self.data.get("favorite_generals") or ())

    def has_favorite_generals(self):
        """玩家是否**明确保存过**自己的武将池。"""

        raw = self.data.get("favorite_generals")
        return isinstance(raw, (list, tuple)) and bool(raw)

    def set_favorite_general_ids(self, ids, *, persist=True):
        """写入武将池（去重、保序）；``persist=True`` 时立刻落盘。"""

        cleaned = _clean_ids(ids)
        self.data["favorite_generals"] = list(cleaned)
        if persist:
            self.save()
        return cleaned

    def clear(self):
        self.data = {}
        return self


#: 进程内共享的一份（main.py 启动时建一次；测试可以自己 new 一个）。
_shared = None


def shared():
    """全局偏好对象（首次访问时从磁盘加载）。"""

    global _shared
    if _shared is None:
        _shared = Preferences()
    return _shared


def set_shared(preferences):
    """替换全局偏好（测试 / 探针用）。"""

    global _shared
    _shared = preferences
    return _shared
