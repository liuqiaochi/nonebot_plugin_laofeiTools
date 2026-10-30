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

VALID_PROVIDERS = ("deepseek", "grok")
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
            "api_key": (getattr(cfg, "deepseek_api_key", "") or "") if cfg else "",
            "base_url": "https://api.deepseek.com",
            "chat_model": (getattr(cfg, "deepseek_model", "deepseek-flash") or "deepseek-flash") if cfg else "deepseek-flash",
            "vision_model": "deepseek-flash",
            "thinking_disable": True,
            "label": "DeepSeek",
            "key_env": "DEEPSEEK_API_KEY",
        }
    if provider == "grok":
        return {
            "api_key": (getattr(cfg, "grok_api_key", "") or "") if cfg else "",
            "base_url": "https://api.x.ai/v1",
            "chat_model": (getattr(cfg, "grok_model", "grok-4.7") or "grok-4.7") if cfg else "grok-4.7",
            "vision_model": (getattr(cfg, "grok_vision_model", "grok-4.7") or "grok-4.7") if cfg else "grok-4.7",
            "thinking_disable": False,
            "label": "Grok (xAI)",
            "key_env": "GROK_API_KEY",
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
            logger.info(
                f"✅ {spec['label']} API Key 已配置"
                f"（对话: {spec['chat_model']}，视觉: {spec['vision_model']}）"
            )
