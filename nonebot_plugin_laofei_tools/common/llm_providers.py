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

import asyncio
import base64
import json
import time
from typing import Optional, Tuple

import httpx
from nonebot import get_driver
from nonebot.log import logger
from openai import OpenAI

from ..config import DATA_DIR, safe_json_save


PROVIDER_FILE = DATA_DIR / "llm_provider_groups.json"
MODEL_CACHE_FILE = DATA_DIR / "llm_model_cache.json"
# 可用模型列表缓存有效期（秒）。模型变动不频繁，24h 足够；可用「模型帮助 刷新」强制更新。
MODEL_CACHE_TTL = 24 * 3600

VALID_PROVIDERS = ("deepseek", "grok", "gemini")
VALID_USAGES = ("ai_chat", "reverse")

# 各服务商「常见可切换模型名」示例（仅作帮助展示用；最终以端点实际支持为准）。
# 默认模型会从 driver.config 动态读取，这里只列几个常见选项，帮助用户知道能填什么。
KNOWN_MODELS = {
    "deepseek": ["deepseek-chat", "deepseek-reasoner", "deepseek-flash (视觉/图反推)"],
    "grok": ["grok-4.7", "grok-4.6", "grok-4.5", "grok-4.3", "grok-4.1-fast", "grok-4.20"],
    "gemini": ["gemini-4-argon", "gemini-3-flash", "gemini-3-pro", "gemini-2.5-flash", "gemini-2.5-pro"],
}

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
        chat = (getattr(cfg, "gemini_model", "gemini-4-argon") or "gemini-4-argon") if cfg else "gemini-4-argon"
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


def format_model_help(live_models: dict = None) -> str:
    """生成「LLM 模型切换帮助」文本。

    live_models: 可选 {provider: [模型名,...]}，来自端点实时拉取；
                 为 None 或某服务商未取到时，回退 KNOWN_MODELS 示例并标注。
    默认模型从 driver.config 动态读取，始终与实际配置一致。
    """
    live = live_models or {}
    lines = [
        "📋 LLM 模型切换帮助",
        "可切换服务商（同服务商内可指定具体模型名，无需改 config / 重启 bot）：",
    ]
    for p in VALID_PROVIDERS:
        try:
            spec = _provider_spec(p)
        except ValueError:
            continue
        models = live.get(p)
        if models:
            src = ""
        else:
            models = KNOWN_MODELS.get(p, [])
            src = "（示例，实时列表未取到时显示）"
        # 展示裁剪：避免单服务商模型过多撑爆消息
        cap = 40
        if len(models) > cap:
            shown = models[:cap]
            tail = f" …等共 {len(models)} 个"
        else:
            shown = models
            tail = ""
        ex = "、".join(shown) + tail
        lines.append(f"· {spec['label']}：默认 {spec['chat_model']}")
        lines.append(f"   可切换模型{src}：{ex}")
    lines.append("")
    lines.append("指令（切换仅超管；查看群成员可用）：")
    lines.append("· 对话模型 <服务商> [模型名]   例：对话模型 gemini  /  对话模型 grok grok-4.7")
    lines.append("· 反推模型 <服务商> [模型名]   例：反推模型 grok  /  反推模型 deepseek deepseek-chat")
    lines.append("· 重置对话模型  /  重置反推模型   （回退全局默认）")
    lines.append("· 模型设置 / 查看模型            （看本群当前配置）")
    lines.append("· 模型帮助 [刷新]               （本说明，实时模型列表）")
    lines.append("")
    lines.append("说明：不写模型名则用服务商默认；写具体名即覆盖（须为该服务商端点支持的模型）。")
    return "\n".join(lines)


# ---------------- 实时可用模型列表（带缓存） ----------------

def _fetch_models_openai(base_url: str, api_key: str) -> list:
    """用 OpenAI SDK 列出模型（deepseek / grok 通用）。返回模型 id 列表。"""
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=20.0, max_retries=2)
    resp = client.models.list()
    return [m.id for m in resp.data]


def _fetch_models_gemini(api_key: str) -> list:
    """Gemini 原生列出模型接口，仅保留支持 generateContent 的 gemini 系列。"""
    import httpx

    url = "https://generativelanguage.googleapis.com/v1beta/models"
    r = httpx.get(url, params={"key": api_key}, timeout=20.0)
    r.raise_for_status()
    data = r.json()
    out = []
    for m in data.get("models", []):
        methods = m.get("supportedGenerationMethods", [])
        name = m.get("name", "")
        if "generateContent" in methods and "gemini" in name.lower():
            out.append(name.split("/")[-1])
    return out


def _filter_models(provider: str, models: list) -> list:
    """剔除不属于该服务商家族的模型（embedding / 非对话等）。"""
    kw = {"deepseek": "deepseek", "grok": "grok", "gemini": "gemini"}.get(provider, "")
    if not kw:
        return models
    return [m for m in models if kw in m.lower()]


def _fetch_models_for(provider: str) -> list:
    """同步拉取某服务商可用模型（失败返回 []）。"""
    spec = _provider_spec(provider)
    if not spec["api_key"]:
        return []
    try:
        if provider == "gemini":
            raw = _fetch_models_gemini(spec["api_key"])
        else:
            raw = _fetch_models_openai(spec["base_url"], spec["api_key"])
        return _filter_models(provider, raw)
    except Exception as e:
        logger.warning(f"[LLM] 拉取 {spec['label']} 可用模型失败: {e}")
        return []


def _load_model_cache() -> dict:
    if MODEL_CACHE_FILE.exists():
        try:
            with open(MODEL_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_model_cache(cache: dict) -> None:
    safe_json_save(MODEL_CACHE_FILE, cache)


def get_available_models(provider: str, force_refresh: bool = False) -> list:
    """获取某服务商可用模型（同步，带 TTL 缓存）。

    - 缓存命中且在有效期内直接返回；
    - 未命中或 force_refresh 时实时拉取并写缓存；
    - 拉取失败：回退到（可能过期的）缓存，再不行回退 KNOWN_MODELS 示例。
    """
    cache = _load_model_cache()
    now = time.time()
    entry = cache.get(provider)
    if not force_refresh and entry and now - entry.get("ts", 0) < MODEL_CACHE_TTL:
        return entry.get("models", [])
    models = _fetch_models_for(provider)
    if models:
        cache[provider] = {"ts": now, "models": models}
        _save_model_cache(cache)
        return models
    if entry and entry.get("models"):
        return entry["models"]
    return KNOWN_MODELS.get(provider, [])


async def get_available_models_async(provider: str, force_refresh: bool = False) -> list:
    return await asyncio.to_thread(get_available_models, provider, force_refresh)


async def get_all_available_models(force_refresh: bool = False) -> dict:
    """并发拉取三家可用模型（各自独立失败不影响其余）。"""
    tasks = {p: asyncio.create_task(get_available_models_async(p, force_refresh)) for p in VALID_PROVIDERS}
    out = {}
    for p, t in tasks.items():
        try:
            out[p] = await t
        except Exception:
            out[p] = KNOWN_MODELS.get(p, [])
    return out


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


def set_provider(group_id: str, usage: str, provider: str, model: str = None) -> Tuple[bool, str]:
    """设置某群某用途的服务商与可选具体模型名。返回 (ok, 提示)。

    model 语义：
      - None：仅设置/保留服务商，不动模型名 override
      - ""  ：清除该用途的模型名 override（回落服务商默认模型）
      - 非空：设置模型名 override（同时设置服务商）
    """
    provider = (provider or "").strip().lower()
    if provider not in VALID_PROVIDERS:
        return False, f"未知服务商，仅支持：{' / '.join(VALID_PROVIDERS)}"
    if usage not in VALID_USAGES:
        return False, f"用途仅支持：{' / '.join(USAGE_LABELS.values())}"
    data = _load()
    gid = str(group_id)
    data.setdefault("groups", {})
    data["groups"].setdefault(gid, {})
    grp = data["groups"][gid]
    grp[usage] = provider

    warn = ""
    if model is not None:
        model = (model or "").strip()
        if model:
            kw_map = {"gemini": "gemini", "grok": "grok", "deepseek": "deepseek"}
            kw = kw_map.get(provider, "")
            if kw and kw not in model.lower():
                warn = "（⚠️ 模型名似乎不属于该服务商，请确认端点支持此模型）"
            grp[f"{usage}_model"] = model
        else:
            grp.pop(f"{usage}_model", None)

    _save(data)
    label = _provider_spec(provider)["label"]
    if model is None:
        return True, f"已将本群「{USAGE_LABELS[usage]}」切换为 {label}"
    if model == "":
        return True, f"已将本群「{USAGE_LABELS[usage]}」切换为 {label}（使用该服务商默认模型）"
    return True, f"已将本群「{USAGE_LABELS[usage]}」切换为 {label}（模型：{model}）{warn}"


def reset_provider(group_id: str, usage: str) -> Tuple[bool, str]:
    """重置某群某用途为全局默认（同时清除模型名 override）。返回 (ok, 提示)。"""
    if usage not in VALID_USAGES:
        return False, f"用途仅支持：{' / '.join(USAGE_LABELS.values())}"
    data = _load()
    gid = str(group_id)
    grp = data.get("groups", {}).get(gid)
    if not grp or (usage not in grp and f"{usage}_model" not in grp):
        return False, f"本群「{USAGE_LABELS[usage]}」未单独设置，已使用全局默认"
    grp.pop(usage, None)
    grp.pop(f"{usage}_model", None)
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
    # Gemini 走 Google 共享集群，偶发 503 高负载；多给一次重试自愈（其余服务商保持默认 2 次）
    max_retries = 3 if provider == "gemini" else 2
    return OpenAI(api_key=spec["api_key"], base_url=spec["base_url"], timeout=60.0, max_retries=max_retries)


def _model_override(group_id: str, usage: str) -> str:
    """返回本群某用途的具体模型名 override（若有），否则空串。

    存储结构：llm_provider_groups.json 的 groups[<gid>][f"{usage}_model"]。
    与服务商(provider)字段并列，互不影响；不写则回落服务商默认模型。
    """
    data = _load()
    grp = data.get("groups", {}).get(str(group_id))
    if not grp:
        return ""
    return (grp.get(f"{usage}_model") or "").strip()


def get_chat_model(group_id: str, usage: str) -> str:
    override = _model_override(group_id, usage)
    if override:
        return override
    return _provider_spec(resolve_provider(group_id, usage))["chat_model"]


def get_vision_model(group_id: str, usage: str) -> str:
    override = _model_override(group_id, usage)
    if override:
        return override
    return _provider_spec(resolve_provider(group_id, usage))["vision_model"]


def get_vision_extra_body(group_id: str, usage: str) -> dict:
    """视觉模型额外参数：DeepSeek 需关闭思考模式；Gemini 默认开启 thinking 且无法关闭，
    显式降到最低推理预算以减少 thought 噪声、省 token；Grok 无需。

    注意：Gemini 的 reasoning_effort 仅支持 low/medium/high（minimal 仅 Gemini 3 Flash /
    3.1 Flash-Lite 支持，4 系传 minimal 会 400）。这里统一用 medium（平衡档，全系列支持）。
    """
    provider = resolve_provider(group_id, usage)
    spec = _provider_spec(provider)
    if spec["thinking_disable"]:
        return {"thinking": {"type": "disabled"}}
    if provider == "gemini":
        return {"reasoning_effort": "medium"}
    return {}


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


# ---------------- Gemini 原生反推（关闭安全过滤） ----------------
#
# 为什么需要单独走原生接口：
#   图反推走 OpenAI 兼容端点（generativelanguage.googleapis.com/v1beta/openai）时，
#   该端点**不支持 safetySettings**，Gemini 对二次元/同人图极易触发 SAFETY 过滤，
#   返回 200 但 content 被清空（finish_reason 映射为 None / 日志"来源: none"），表现为"没结果"。
#   改用原生 Generative Language API（v1beta/models/{model}:generateContent）并显式设置
#   safetySettings 全类别 BLOCK_NONE，可规避这类误杀（合法、公开、稳定）。
#   注意：思考（thinking）若开启会把结果写进 thought 段、content 为空，故对非必要思考的
#   模型显式 thinkingBudget=0，保证文本落在 content。

GEMINI_HARM_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
)

# 仅对这些（支持 thinking 的）模型显式关闭思考，避免旧模型因不识别 thinkingConfig 报错
_GEMINI_REASONING_MARKS = ("2.5", "3", "4")


def gemini_native_reverse(gid: str, image_data: bytes, instruction: str, mime: str = "image/png") -> dict:
    """Gemini 原生 generateContent 调用，专用于图反推。

    返回 dict：
      {provider, model, text, source, finish_reason, has_reasoning, safety_blocked}
      - text: 提取到的反推文本（已拼接，未 sanitize）
      - source: "content" / "thought" / "none"
      - safety_blocked: 因安全策略整体被拦（极少，BLOCK_NONE 下基本不会）
    异常：
      - ValueError：未配置 Gemini API Key（交给调用方走"配置错误"分支）
      - 其余（网络/HTTP 错误）：原样抛出，由调用方走"反推失败"分支
    """
    spec = _provider_spec("gemini")
    if not spec["api_key"]:
        raise ValueError(f"未配置 {spec['key_env']}")
    model = get_vision_model(gid, "reverse")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    gen_config: dict = {"temperature": 0.5, "maxOutputTokens": 800}
    if any(mark in model.lower() for mark in _GEMINI_REASONING_MARKS):
        gen_config["thinkingConfig"] = {"thinkingBudget": 0}

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_data).decode()}},
                ],
            }
        ],
        "generationConfig": gen_config,
        "safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in GEMINI_HARM_CATEGORIES],
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, params={"key": spec["api_key"]}, json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:500]
        except Exception:
            pass
        raise RuntimeError(f"Gemini 原生接口返回 {e.response.status_code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Gemini 原生接口请求失败: {e}")

    candidates = data.get("candidates", [])
    if not candidates:
        block = data.get("promptFeedback", {}).get("blockReason")
        return {
            "provider": "gemini", "model": model, "text": "",
            "source": "none", "finish_reason": None,
            "has_reasoning": False, "safety_blocked": bool(block),
        }

    cand = candidates[0]
    parts = cand.get("content", {}).get("parts", [])
    text_parts = [p.get("text", "") for p in parts if p.get("text") and not p.get("thought")]
    thought_parts = [p.get("text", "") for p in parts if p.get("text") and p.get("thought")]
    text = "\n".join(text_parts).strip()
    source = "content" if text else "none"
    if not text and thought_parts:
        text = "\n".join(thought_parts).strip()
        source = "thought"
    return {
        "provider": "gemini", "model": model, "text": text,
        "source": source, "finish_reason": cand.get("finishReason"),
        "has_reasoning": bool(thought_parts), "safety_blocked": False,
    }


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
