"""
插件配置
"""

import json
import os
from pathlib import Path
from typing import Set

from nonebot import get_driver
from pydantic import BaseModel, validator

import re

from .common.data_utils import safe_json_save


def _coerce_serpapi_keys(v):
    """把 serpapi_keys 配置规整为「去空、去重」的字符串列表。

    支持多种形式：Python 列表 / JSON 数组字符串 / 逗号或空白分隔字符串 /
    单个字符串。任何解析异常都回退为空列表，绝不让 bot 启动崩溃。
    """
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            items = [str(x).strip() for x in parsed]
        else:
            items = re.split(r"[,\s]+", s)
        out = []
        for x in items:
            x = (x or "").strip()
            if x and x not in out:
                out.append(x)
        return out
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            x = (x or "").strip()
            if x and x not in out:
                out.append(x)
        return out
    s = str(v).strip()
    return [s] if s else []


class Config(BaseModel):
    """插件配置类"""

    # 超级用户列表（开启/关闭功能的权限）
    longge_superusers: Set[str] = set()

    # 默认开启搜图的群聊（为空表示默认关闭）
    longge_search_enabled_groups: Set[str] = set()

    # DeepSeek AI 配置
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"

    # Grok (xAI) AI 配置（可选；启用后可在群内切换对话/图反推到 Grok）
    grok_api_key: str = ""
    grok_model: str = "grok-4.7"
    grok_vision_model: str = "grok-4.7"
    # Grok 接入地址（OpenAI 兼容）。默认官方 api.x.ai。
    # 若使用第三方代理/中转：其下发的 key 通常以 grok_ 等非 xai- 前缀开头，
    # 务必把本项改为该代理给出的 base_url，否则官方端点会拒绝 key。
    grok_base_url: str = "https://api.x.ai/v1"

    # NovelAI 画图配置
    novelai_api_key: str = ""
    novelai_model: str = "nai-diffusion-4-5-curated"

    # ---- 反向图片搜索（lg找鸡）配置，均为可选 ----
    # 2026-10-30 起只走 Google Lens（经 SerpApi），已移除 Yandex/SauceNAO/ascii2d/GCV。
    # 2026-10-31 起改为双路：mac 侧浏览器 sidecar 主路 + SerpApi 兜底，见下方注释。
    longge_reverse_timeout: int = 25        # 单次请求超时（秒）
    longge_reverse_max_results: int = 5     # 返回的作者候选数量上限

    # ---- 主路：mac 侧浏览器 sidecar（tools/fish_sidecar/server.mjs）----
    # bot 跑在无 GUI 的 Ubuntu 虚拟机里，直连 Google Lens 需要真实浏览器，
    # 所以把「鱼鱼找鸡」作为独立服务常驻在 mac 上，VM 通过 HTTP 把图片字节
    # POST 过去取结果。不限 SerpApi 的 100 次/月额度；mac 关机/超时则自动降级。
    lens_sidecar_url: str = ""              # 例 http://192.168.2.45:8787 ；留空=不启用主路
    lens_sidecar_token: str = ""            # 与 sidecar 的 FISH_TOKEN / config.token 一致；留空=不鉴权
    lens_sidecar_timeout: int = 90          # 主路整体超时（秒），浏览器链路比纯 HTTP 慢
    # SerpApi key 数组（https://serpapi.com，免费 100 次/月/key）。
    # 每 key 每月自动计数，满 100 自动切下一个；当月全部用完则暂停本功能，次月 1 号重置。
    # 配置方式（.env）：SERPAPI_KEYS=["k1","k2"] 或 逗号分隔 SERPAPI_KEYS=k1,k2
    # 兼容旧单 key：若 serpapi_keys 为空但 serpapi_key 有值，则视为单元素数组。
    serpapi_keys: list = []
    serpapi_key: str = ""                   # 旧单 key 兼容（可留空）
    lens_proxy: str = ""                    # 访问 Google Lens / 图床的代理；大陆机器直连 Google 多半不通，需填如 http://127.0.0.1:7890

    @validator("serpapi_keys", pre=True)
    def _norm_serpapi_keys(cls, v):
        return _coerce_serpapi_keys(v)


# 数据文件路径（锚定项目根目录，不依赖运行时 CWD，避免覆盖文件后数据丢失）
DATA_DIR = Path("data/laofei_tools")
DATA_FILE = DATA_DIR / "enabled_groups.json"
POINTS_DISABLED_FILE = DATA_DIR / "points_disabled_groups.json"
AI_ENABLED_FILE = DATA_DIR / "ai_enabled_groups.json"
AI_BLACKLIST_FILE = DATA_DIR / "ai_blacklist.json"


def _ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_enabled_groups() -> Set[str]:
    """从文件加载已开启的群聊列表"""
    _ensure_data_dir()
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("enabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_enabled_groups(groups: Set[str]):
    """安全保存已开启的群聊列表到文件"""
    safe_json_save(DATA_FILE, {"enabled_groups": list(groups)})


# ========== 积分系统开关 ==========

def _load_points_disabled_groups() -> Set[str]:
    """从文件加载已关闭积分系统的群聊列表"""
    _ensure_data_dir()
    if POINTS_DISABLED_FILE.exists():
        try:
            with open(POINTS_DISABLED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("disabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_points_disabled_groups(groups: Set[str]):
    """安全保存已关闭积分系统的群聊列表到文件"""
    safe_json_save(POINTS_DISABLED_FILE, {"disabled_groups": list(groups)})


# 运行时状态存储（从文件加载）
_enabled_groups: Set[str] = _load_enabled_groups()
_points_disabled_groups: Set[str] = _load_points_disabled_groups()


# 注：搜图 / 积分的「查询-开启-关闭」函数已统一到文件末尾的「兼容层」，
# 由统一模块开关层（FEATURE_MODULES / is_module_enabled）集中管理。
# 此处仅保留旧数据文件的加载函数，供首次启动迁移使用。


def init_enabled_groups(default_groups: Set[str]) -> None:
    """初始化默认开启的群聊（仅在数据文件不存在时生效）

    同时把结果同步进统一模块开关层（search 模块），并遵循一条原则：
    仅当统一层「尚未显式记录」该群 search 状态时才写入，避免覆盖超管
    后续通过「关闭 搜图功能」所做的设置。
    """
    global _enabled_groups
    # 如果文件已存在，不覆盖已有数据
    if DATA_FILE.exists():
        _enabled_groups = _load_enabled_groups()
    else:
        _enabled_groups = set(default_groups)
        _save_enabled_groups(_enabled_groups)

    changed = False
    for g in _enabled_groups:
        g = str(g)
        if "search" not in _module_switches.get(g, {}):
            _module_switches.setdefault(g, {})["search"] = True
            changed = True
    if changed:
        _save_module_switches(_module_switches)


# ========== AI 功能群聊开关 ==========

_ai_enabled_groups: Set[str] = set()
_ai_blacklist: Set[str] = set()


def _load_ai_enabled_groups() -> Set[str]:
    _ensure_data_dir()
    if AI_ENABLED_FILE.exists():
        try:
            with open(AI_ENABLED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("enabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_ai_enabled_groups(groups: Set[str]):
    safe_json_save(AI_ENABLED_FILE, {"enabled_groups": list(groups)})


def _load_ai_blacklist() -> Set[str]:
    _ensure_data_dir()
    if AI_BLACKLIST_FILE.exists():
        try:
            with open(AI_BLACKLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("blacklist", []))
        except Exception:
            return set()
    return set()


def _save_ai_blacklist(blacklist: Set[str]):
    safe_json_save(AI_BLACKLIST_FILE, {"blacklist": list(blacklist)})


# 初始化
_ai_enabled_groups = _load_ai_enabled_groups()
_ai_blacklist = _load_ai_blacklist()


# 注：AI 对话的「查询-开启-关闭」函数已统一到文件末尾的「兼容层」，
# 由统一模块开关层（ai_chat 模块）集中管理。此处保留旧文件的加载函数供迁移使用。


def is_ai_blacklisted(user_id: str) -> bool:
    """检查用户是否在 AI 黑名单中"""
    return user_id in _ai_blacklist


def add_ai_blacklist(user_id: str) -> None:
    """将用户加入 AI 黑名单"""
    _ai_blacklist.add(user_id)
    _save_ai_blacklist(_ai_blacklist)


def remove_ai_blacklist(user_id: str) -> None:
    """将用户移出 AI 黑名单"""
    _ai_blacklist.discard(user_id)
    _save_ai_blacklist(_ai_blacklist)


# ========== NovelAI 画图群聊开关（默认关闭） ==========

NOVELAI_ENABLED_FILE = DATA_DIR / "novelai_enabled_groups.json"

_novelai_enabled_groups: Set[str] = set()


def _load_novelai_enabled_groups() -> Set[str]:
    _ensure_data_dir()
    if NOVELAI_ENABLED_FILE.exists():
        try:
            with open(NOVELAI_ENABLED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("enabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_novelai_enabled_groups(groups: Set[str]):
    safe_json_save(NOVELAI_ENABLED_FILE, {"enabled_groups": list(groups)})


_novelai_enabled_groups = _load_novelai_enabled_groups()


# 注：NovelAI 画图的「查询-开启-关闭」函数已统一到文件末尾的「兼容层」，
# 由统一模块开关层（ai_draw 模块）集中管理。此处保留旧文件的加载函数供迁移使用。


# ========== 图反推润色群聊开关（默认关闭） ==========
# 与 AI 功能群开关（ai_enabled_groups.json）同构，独立 JSON 文件，按群记录是否自动润色。
# 默认关闭；开启后该群图反推会自动用 DeepSeek 文本模型润色（关键字「优化」仍可作为临时触发）。

REVERSE_POLISH_FILE = DATA_DIR / "reverse_polish_groups.json"

_reverse_polish_groups: Set[str] = set()


def _load_reverse_polish_groups() -> Set[str]:
    _ensure_data_dir()
    if REVERSE_POLISH_FILE.exists():
        try:
            with open(REVERSE_POLISH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("enabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_reverse_polish_groups(groups: Set[str]):
    safe_json_save(REVERSE_POLISH_FILE, {"enabled_groups": list(groups)})


_reverse_polish_groups = _load_reverse_polish_groups()


def is_reverse_polish_enabled(group_id: str) -> bool:
    """本群是否开启图反推自动润色（默认关）"""
    return group_id in _reverse_polish_groups


def enable_reverse_polish(group_id: str) -> None:
    _reverse_polish_groups.add(group_id)
    _save_reverse_polish_groups(_reverse_polish_groups)


def disable_reverse_polish(group_id: str) -> None:
    _reverse_polish_groups.discard(group_id)
    _save_reverse_polish_groups(_reverse_polish_groups)


# ========== AI 自由发言群聊开关（默认关闭，依赖 AI 对话已开启） ==========
# 与 AI 功能群开关同构，独立 JSON 文件，按群记录是否开启自由发言模式。
# 自由发言模式下：无需 @，bot 基于最近 200 条群聊上下文自动接话。
# 仅在「该群已开启 AI 对话」时有效；若 AI 对话被关闭，自由发言随之失效。

AI_FREESPEAK_FILE = DATA_DIR / "ai_freespeak_groups.json"

_ai_freespeak_groups: Set[str] = set()


def _load_ai_freespeak_groups() -> Set[str]:
    _ensure_data_dir()
    if AI_FREESPEAK_FILE.exists():
        try:
            with open(AI_FREESPEAK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("enabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_ai_freespeak_groups(groups: Set[str]):
    safe_json_save(AI_FREESPEAK_FILE, {"enabled_groups": list(groups)})


_ai_freespeak_groups = _load_ai_freespeak_groups()


def ai_freespeak_is_on(group_id: str) -> bool:
    """本群是否记录为已开启自由发言（不检查 AI 对话依赖，用于开关指令判断）"""
    return str(group_id) in _ai_freespeak_groups


def is_ai_freespeak_enabled(group_id: str) -> bool:
    """本群是否真正可用自由发言：已开启 且 该群已开启 AI 对话（级联依赖）"""
    gid = str(group_id)
    if gid not in _ai_freespeak_groups:
        return False
    return is_ai_group_enabled(gid)


def enable_ai_freespeak(group_id: str) -> None:
    _ai_freespeak_groups.add(str(group_id))
    _save_ai_freespeak_groups(_ai_freespeak_groups)


def disable_ai_freespeak(group_id: str) -> None:
    _ai_freespeak_groups.discard(str(group_id))
    _save_ai_freespeak_groups(_ai_freespeak_groups)


# ========== NovelAI 画图模型（群级，默认回退到 config.novelai_model） ==========

NOVELAI_MODEL_FILE = DATA_DIR / "novelai_model_groups.json"

_novelai_model_groups: dict = {}


def _load_novelai_model_groups() -> dict:
    _ensure_data_dir()
    if NOVELAI_MODEL_FILE.exists():
        try:
            with open(NOVELAI_MODEL_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("model_groups", {})
        except Exception:
            return {}
    return {}


def _save_novelai_model_groups(data: dict):
    safe_json_save(NOVELAI_MODEL_FILE, {"model_groups": data})


_novelai_model_groups = _load_novelai_model_groups()


def get_novelai_model(group_id: str) -> str:
    """返回本群当前模型（空字符串表示使用 config 默认）"""
    return _novelai_model_groups.get(group_id, "")


def set_novelai_model(group_id: str, model: str) -> None:
    """设置本群当前模型（传入原始模型名）"""
    _novelai_model_groups[group_id] = model
    _save_novelai_model_groups(_novelai_model_groups)


# ========== 统一功能模块开关（单一数据源） ==========
# 8 个功能模块，每个群独立开关；缺失时用 default；依赖链在查询时级联推导。
FEATURE_MODULES = {
    "points":  {"name": "积分系统", "default": True,  "requires": None},
    "pet":     {"name": "宠物系统", "default": True,  "requires": "points"},
    "fishing": {"name": "钓鱼系统", "default": True,  "requires": "pet"},
    "qrcode":  {"name": "二维码工具", "default": True, "requires": None},
    "life":    {"name": "生活工具", "default": True,  "requires": None},
    "search":  {"name": "搜图功能", "default": False, "requires": None},
    "ai_chat": {"name": "AI对话",  "default": False, "requires": None},
    "ai_draw": {"name": "AI画图",  "default": False, "requires": None},
}

# 模块展示顺序 / 序号（1-based）：图片列表与「开启 3」序号解析共用此唯一顺序
MODULE_ORDER = ["points", "pet", "fishing", "qrcode", "life", "search", "ai_chat", "ai_draw"]

# 模块名 / 常用别名 -> 模块 key（指令层负责去除「开启/关闭」前缀后再查）
_MODULE_KEY_ALIASES: dict = {}
for _k, _v in FEATURE_MODULES.items():
    _MODULE_KEY_ALIASES[_k] = _k
    _MODULE_KEY_ALIASES[_v["name"]] = _k
_MODULE_KEY_ALIASES.update({
    "积分": "points", "宠物": "pet", "钓鱼": "fishing", "二维码": "qrcode",
    "生活": "life", "搜图": "search", "ai": "ai_chat", "aichat": "ai_chat",
    "画图": "ai_draw", "ai画图": "ai_draw", "ai生图": "ai_draw",
    "ai绘画": "ai_draw", "ai绘图": "ai_draw", "aidraw": "ai_draw",
})

MODULE_SWITCH_FILE = DATA_DIR / "module_switches.json"
_module_switches: dict = {}


def _load_module_switches() -> dict:
    _ensure_data_dir()
    if MODULE_SWITCH_FILE.exists():
        try:
            with open(MODULE_SWITCH_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("switches", {})
        except Exception:
            return {}
    return {}


def _save_module_switches(data: dict) -> None:
    safe_json_save(MODULE_SWITCH_FILE, {"switches": data})


def _migrate_module_switches() -> None:
    """首次启动：从旧的四套分散开关文件迁移到统一开关层。

    仅记录「非默认」状态；默认开启的模块（points/pet/fishing/qrcode/life）
    不写入，查询时回退默认。已存在 module_switches.json 则跳过。
    """
    if MODULE_SWITCH_FILE.exists():
        return
    switches: dict = {}
    for g in _points_disabled_groups:      # 积分：默认开，关的群记 False
        switches.setdefault(str(g), {})["points"] = False
    for g in _enabled_groups:              # 搜图：默认关，开的群记 True
        switches.setdefault(str(g), {})["search"] = True
    for g in _ai_enabled_groups:           # AI 对话：默认关，开的群记 True
        switches.setdefault(str(g), {})["ai_chat"] = True
    for g in _novelai_enabled_groups:      # AI 画图：默认关，开的群记 True
        switches.setdefault(str(g), {})["ai_draw"] = True
    _save_module_switches(switches)


_module_switches = _load_module_switches()
_migrate_module_switches()
_module_switches = _load_module_switches()  # 重新加载（含迁移结果）


def resolve_module_key(text: str):
    """将模块名 / 别名 / 序号（1-based）解析为模块 key；无法识别返回 None"""
    if not text:
        return None
    t = text.strip()
    if t.isdigit():
        n = int(t)
        if 1 <= n <= len(MODULE_ORDER):
            return MODULE_ORDER[n - 1]
        return None
    return _MODULE_KEY_ALIASES.get(t)


def get_module_switches(group_id: str) -> dict:
    """返回本群全部模块开关状态（用 default 填充缺失）"""
    group_id = str(group_id)
    stored = _module_switches.get(group_id, {})
    return {k: stored.get(k, meta["default"]) for k, meta in FEATURE_MODULES.items()}


def _effective_enabled(group_id: str, module: str) -> bool:
    """模块有效开启 = 自身开 且 依赖链全部有效开启（级联推导）"""
    sw = get_module_switches(group_id)
    if not sw.get(module, FEATURE_MODULES[module]["default"]):
        return False
    req = FEATURE_MODULES[module]["requires"]
    return _effective_enabled(group_id, req) if req else True


def is_module_enabled(group_id: str, module: str) -> bool:
    """查询某群某模块是否真正可用（含依赖级联）"""
    return _effective_enabled(str(group_id), module)


def set_module_enabled(group_id: str, module: str, enabled: bool):
    """设置模块开关。返回 (ok, msg)。

    enabled=True 时沿依赖链检查祖先是否已开启，否则拒绝并提示缺哪个。
    """
    group_id = str(group_id)
    if module not in FEATURE_MODULES:
        return False, "未知模块"
    if enabled:
        req = FEATURE_MODULES[module]["requires"]
        if req and not is_module_enabled(group_id, req):
            # 沿链找「自身开关为关」的第一个祖先（真正的根因）
            cur = req
            missing = req
            while cur:
                if not get_module_switches(group_id).get(cur, FEATURE_MODULES[cur]["default"]):
                    missing = cur
                    break
                cur = FEATURE_MODULES[cur]["requires"]
            return False, (
                f"开启{FEATURE_MODULES[module]['name']}需先开启"
                f"{FEATURE_MODULES[missing]['name']}"
                f"（超管发送「开启 {FEATURE_MODULES[missing]['name']}」）"
            )
    _module_switches.setdefault(group_id, {})[module] = enabled
    _save_module_switches(_module_switches)
    return True, f"{'✅ 已开启' if enabled else '❌ 已关闭'}{FEATURE_MODULES[module]['name']}"


# ---- 兼容层：旧分散开关函数委托统一模块开关（保证已有群状态与旧命令不丢） ----
def is_points_enabled(group_id: str) -> bool:
    return is_module_enabled(group_id, "points")

def enable_points(group_id: str) -> None:
    set_module_enabled(group_id, "points", True)

def disable_points(group_id: str) -> None:
    set_module_enabled(group_id, "points", False)

def is_group_enabled(group_id: str) -> bool:
    return is_module_enabled(group_id, "search")

def enable_group(group_id: str) -> None:
    set_module_enabled(group_id, "search", True)

def disable_group(group_id: str) -> None:
    set_module_enabled(group_id, "search", False)

def is_ai_group_enabled(group_id: str) -> bool:
    return is_module_enabled(group_id, "ai_chat")

def enable_ai_group(group_id: str) -> None:
    set_module_enabled(group_id, "ai_chat", True)

def disable_ai_group(group_id: str) -> None:
    set_module_enabled(group_id, "ai_chat", False)
    # 关闭 AI 对话时，连带关闭自由发言（其依赖 AI 对话），避免留下失效的开启态
    if ai_freespeak_is_on(group_id):
        disable_ai_freespeak(group_id)

def is_novelai_group_enabled(group_id: str) -> bool:
    return is_module_enabled(group_id, "ai_draw")

def enable_novelai_group(group_id: str) -> None:
    set_module_enabled(group_id, "ai_draw", True)

def disable_novelai_group(group_id: str) -> None:
    set_module_enabled(group_id, "ai_draw", False)


