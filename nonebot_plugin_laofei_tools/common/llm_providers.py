"""
LLM Provider 抽象层

把「AI 对话(ai_chat)」与「图反推(reverse)」两套功能背后的 LLM 服务商抽象出来，
支持按群、按用途切换服务商（当前支持 deepseek / grok）。

设计要点：
- 数据持久化到 data/laofei_tools/llm_provider_groups.json：
    {
      "default": {"ai_chat": "deepseek", "reverse": "deepseek"},
      "groups":  {"<gid>": {"ai_chat": "grok", "reverse": "deepseek"}}
    }
  全局默认写在 default，单个群可 override 某项用途。
- 各服务商运行时配置（api_key / base_url / 模型 / 是否关闭思考模式）从 driver.config 读取。
- DeepSeek 视觉模型必须显式关闭思考模式（extra_body），Grok 无此参数，按服务商分支处理。
"""

import json
from typing import Optional, Tuple

from nonebot import get_driver
from nonebot.log import logger
from openai import OpenAI

from ..config import DATA_DIR, safe_json_save


PROVIDER_FILE = DATA_DIR / "llm_provider_groups.json"

VALID_PROVIDERS = ("deepseek", "grok", "gemini")
VALID_USAGES = ("ai_chat", "reverse")

USAGE_LABELS = {"ai_chat": "对话", "reverse": "图反推"}

DEFAULT_DATA = {
    "default": {"ai_chat": "deepseek", "reverse": "deepseek"},
    "groups": {},
}

_cache: Optional[dict] = None


# ---------------- 持久化 ----------------

def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if PROVIDER_FILE.exists():
        try:
            with open(PROVIDER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("default", dict(DEFAULT_DATA["default"]))
            data.setdefault("groups", {})
            _cache = data
            return _cache
        except Exception as e:
            logger.warning(f"[LLM] llm_provider_groups.json 读取失败，回退默认: {e}")
    _cache = json.loads(json.dumps(DEFAULT_DATA))
    return _cache


def _save(data: dict) -> None:
    global _cache
    _cache = data
    safe_json_save(PROVIDER_FILE, data)


# ---------------- 服务商规格 ----------------

def _provider_spec(provider: str) -> dict:
    """返回服务商运行时规格；未知 provider 抛 ValueError。"""
    try:
        cfg = get_driver().config
    except Exception:
        cfg = None

    if provider == "deepseek":
        return {
            "provider": "deepseek",
            "api_key": (getattr(cfg, "deepseek_api_key", "") or "") if cfg else "",
            "base_url": "https://api.deepseek.com",
            "chat_model": (getattr(cfg, "deepseek_model", "deepseek-flash") or "deepseek-flash") if cfg else "deepseek-flash",
            "vision_model": "deepseek-flash",
            "thinking_disable": True,
            "reasoning_effort": "",
            "label": "DeepSeek",
            "key_env": "DEEPSEEK_API_KEY",
        }
    if provider == "grok":
        return {
            "provider": "grok",
            "api_key": (getattr(cfg, "grok_api_key", "") or "") if cfg else "",
            "base_url": (getattr(cfg, "grok_base_url", "https://api.x.ai/v1") or "https://api.x.ai/v1") if cfg else "https://api.x.ai/v1",
            "chat_model": (getattr(cfg, "grok_model", "grok-4.7") or "grok-4.7") if cfg else "grok-4.7",
            "vision_model": (getattr(cfg, "grok_vision_model", "grok-4.7") or "grok-4.7") if cfg else "grok-4.7",
            "thinking_disable": False,
            "reasoning_effort": (getattr(cfg, "grok_reasoning_effort", "low") or "") if cfg else "low",
            "label": "Grok (xAI)",
            "key_env": "GROK_API_KEY",
        }
    if provider == "gemini":
        chat = (getattr(cfg, "gemini_model", "gemini-3.8-flash") or "gemini-3.8-flash") if cfg else "gemini-3.8-flash"
        return {
            "provider": "gemini",
            "api_key": (getattr(cfg, "gemini_api_key", "") or "") if cfg else "",
            "base_url": (
                getattr(cfg, "gemini_base_url", "https://generativelanguage.googleapis.com/v1beta/openai")
                or "https://generativelanguage.googleapis.com/v1beta/openai"
            ) if cfg else "https://generativelanguage.googleapis.com/v1beta/openai",
            "chat_model": chat,
            # Gemini 同模型原生多模态，视觉直接复用对话模型
            "vision_model": chat,
            "thinking_disable": False,
            "reasoning_effort": "",
            "label": "Gemini (Google)",
            "key_env": "GEMINI_API_KEY",
        }
    raise ValueError(f"未知 LLM 服务商：{provider}（仅支持 {' / '.join(VALID_PROVIDERS)}）")


def provider_label(provider: str) -> str:
    """服务商 key -> 展示名（未知时原样返回）。"""
    try:
        return _provider_spec(provider)["label"]
    except ValueError:
        return provider


# ---------------- 按群/用途解析与设置 ----------------

def resolve_provider(group_id: str, usage: str) -> str:
    """返回某群某用途当前应使用的服务商（default 或 group override）。"""
    if usage not in VALID_USAGES:
        usage = "ai_chat"
    data = _load()
    gid = str(group_id)
    grp = data.get("groups", {}).get(gid)
    if grp and usage in grp and grp[usage] in VALID_PROVIDERS:
        return grp[usage]
    return data.get("default", {}).get(usage, "deepseek")


def get_default_provider(usage: str) -> str:
    return _load().get("default", {}).get(usage, "deepseek")


def set_provider(group_id: str, usage: str, provider: str) -> Tuple[bool, str]:
    """设置某群某用途的服务商。返回 (ok, 提示)。"""
    provider = (provider or "").strip().lower()
    if provider not in VALID_PROVIDERS:
        return False, f"未知服务商，仅支持：{' / '.join(VALID_PROVIDERS)}"
    if usage not in VALID_USAGES:
        return False, f"用途仅支持：{' / '.join(USAGE_LABELS.values())}"
    data = _load()
    gid = str(group_id)
    data.setdefault("groups", {})
    data["groups"].setdefault(gid, {})
    data["groups"][gid][usage] = provider
    _save(data)
    return True, f"已将本群「{USAGE_LABELS[usage]}」模型切换为 {_provider_spec(provider)['label']}"


def reset_provider(group_id: str, usage: str) -> Tuple[bool, str]:
    """重置某群某用途为全局默认。返回 (ok, 提示)。"""
    if usage not in VALID_USAGES:
        return False, f"用途仅支持：{' / '.join(USAGE_LABELS.values())}"
    data = _load()
    gid = str(group_id)
    grp = data.get("groups", {}).get(gid)
    if not grp or usage not in grp:
        return False, f"本群「{USAGE_LABELS[usage]}」未单独设置，已使用全局默认"
    del grp[usage]
    if not grp:
        data["groups"].pop(gid, None)
    _save(data)
    default = data.get("default", {}).get(usage, "deepseek")
    return True, f"已重置本群「{USAGE_LABELS[usage]}」为全局默认（{_provider_spec(default)['label']}）"


# ---------------- 客户端与模型 ----------------

def build_client(group_id: str, usage: str) -> OpenAI:
    """构建对应服务商客户端；若该服务商未配置 key 抛 ValueError（含中文提示）。"""
    provider = resolve_provider(group_id, usage)
    spec = _provider_spec(provider)
    if not spec["api_key"]:
        raise ValueError(
            f"本群「{USAGE_LABELS.get(usage, usage)}」已选择 {spec['label']}，"
            f"但未配置其 API Key。请在 .env 中设置 {spec['key_env']}"
        )
    return OpenAI(api_key=spec["api_key"], base_url=spec["base_url"], timeout=60.0)


def get_chat_model(group_id: str, usage: str) -> str:
    return _provider_spec(resolve_provider(group_id, usage))["chat_model"]


def get_vision_model(group_id: str, usage: str) -> str:
    return _provider_spec(resolve_provider(group_id, usage))["vision_model"]


def get_vision_extra_body(group_id: str, usage: str) -> dict:
    """视觉模型额外参数：DeepSeek 需关闭思考模式；Grok 无需。"""
    spec = _provider_spec(resolve_provider(group_id, usage))
    return {"thinking": {"type": "disabled"}} if spec["thinking_disable"] else {}


# ---------------- Grok 思考强度（提速） ----------------
#
# 官方文档：不同 Grok 模型支持的 reasoning_effort 档位不同，且非推理模型**不接受该参数**
# （发送会直接 400）。这里按模型家族做白名单，宁可少发也不要发错。
#   grok-4.7 / 4.6 : low / medium / high(默认) / xhigh —— 无法关闭思考
#   grok-4.5       : low / medium / high(默认)       —— 无法关闭思考
#   grok-4.3       : none / low(默认) / medium / high —— none 可完全关闭思考（近即时）
#   grok-4.1-fast、grok-4.20 : 非推理模型，不支持该参数

_GROK_EFFORT_BY_FAMILY = (
    (("4.7", "4.6"), ("low", "medium", "high", "xhigh")),
    (("4.5",), ("low", "medium", "high")),
    (("4.3",), ("none", "low", "medium", "high")),
)

# 命中任一标记即视为非推理模型：不发送 reasoning_effort
_GROK_NON_REASONING_MARKS = ("-fast", "4.20", "non-reasoning")

# 视为「不发送」的配置值
_GROK_EFFORT_OFF_VALUES = ("", "off", "disable", "disabled", "0", "false", "no")


def _grok_effort_levels(model: str) -> Tuple[str, ...]:
    """返回该 Grok 模型支持的 reasoning_effort 档位；空元组表示不支持该参数。"""
    m = (model or "").strip().lower()
    if not m:
        return ()
    if any(mark in m for mark in _GROK_NON_REASONING_MARKS):
        return ()
    for names, levels in _GROK_EFFORT_BY_FAMILY:
        if any(n in m for n in names):
            return levels
    return ()


def get_grok_extra_body(group_id: str, usage: str, model: str = "") -> dict:
    """Grok 专属额外参数（reasoning_effort），用于提速；不适用时返回 {}。

    返回的 dict 可直接合并进 OpenAI SDK 的 extra_body：
    - 非 Grok 服务商 → {}
    - 配置为空 / off → {}（保持服务商默认，grok-4.7 默认 high）
    - 非推理模型（grok-4.1-fast / grok-4.20 等）→ {}
    - 档位超出该模型支持范围 → 回落到 low（不在范围内时不发，避免 400）
    """
    spec = _provider_spec(resolve_provider(group_id, usage))
    if spec.get("provider") != "grok":
        return {}
    raw = (spec.get("reasoning_effort") or "").strip().lower()
    if raw in _GROK_EFFORT_OFF_VALUES:
        return {}
    model = model or spec["chat_model"]
    levels = _grok_effort_levels(model)
    if not levels:
        return {}
    if raw not in levels:
        if "low" not in levels:
            logger.debug(f"[LLM] Grok 模型 {model} 不支持 reasoning_effort={raw}，已跳过")
            return {}
        logger.debug(f"[LLM] Grok 模型 {model} 不支持 reasoning_effort={raw}，回落到 low")
        raw = "low"
    return {"reasoning_effort": raw}


# ---------------- 启动校验 ----------------

def check_provider_keys() -> None:
    """bot 启动时校验各服务商 key 配置。DeepSeek 缺失为致命告警，Grok 缺失为可选提示。"""
    for provider in VALID_PROVIDERS:
        spec = _provider_spec(provider)
        if not spec["api_key"]:
            if provider == "deepseek":
                logger.error("=" * 50)
                logger.error(f"❌ {spec['label']} API Key 未配置！AI 对话 / 图反推将不可用。")
                logger.error(f"请在 .env 文件中添加：{spec['key_env']}=...")
                logger.error("=" * 50)
            else:
                logger.warning(
                    f"⚠️ {spec['label']} API Key 未配置（{spec['key_env']}）。"
                    f"若某群切换至 {spec['label']} 会因缺 key 报错，配置后即可使用。"
                )
        else:
            effort = (spec.get("reasoning_effort") or "").strip()
            effort_note = ""
            if spec.get("provider") == "grok":
                levels = _grok_effort_levels(spec["chat_model"])
                if not levels:
                    effort_note = "，思考强度: 该模型非推理/不支持该参数"
                elif effort.lower() in _GROK_EFFORT_OFF_VALUES:
                    effort_note = "，思考强度: 未设置（服务商默认）"
                else:
                    effort_note = f"，思考强度: {effort.lower()}"
            logger.info(
                f"✅ {spec['label']} API Key 已配置"
                f"（endpoint: {spec['base_url']}，对话: {spec['chat_model']}，视觉: {spec['vision_model']}{effort_note}）"
            )

    # 联网能力自检：Grok 联网主通道依赖 Responses API（openai >= 1.66）
    try:
        import openai as _openai_mod
        from openai import OpenAI as _OAI

        _ver = getattr(_openai_mod, "__version__", "?")
        if hasattr(_OAI, "responses"):
            logger.info(f"✅ openai SDK {_ver}：支持 Responses API（Grok 联网主通道可用）")
        else:
            logger.warning(
                f"⚠️ openai SDK {_ver} 不支持 Responses API（需 >= 1.66），"
                "Grok 联网将自动改走「DeepSeek 检索 + Grok 作答」兜底通道；"
                "建议在 bot 环境执行：pip install -U openai"
            )
    except Exception as e:
        logger.warning(f"⚠️ openai SDK 联网能力自检失败: {e}")
