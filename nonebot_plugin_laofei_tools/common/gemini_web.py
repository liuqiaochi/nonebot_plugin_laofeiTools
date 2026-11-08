"""
Gemini 原生 Google Search grounding（AI 对话联网）

当某群「对话」服务商切到 Gemini 且开启联网时，走本模块：
调用 Gemini 原生 generateContent REST API 并挂载 googleSearch tool，
由 Gemini 自己决定检索并实时回答（一步到位，无需像 DS 那样先答后判）。

注意：Gemini 的 OpenAI 兼容层（llm_providers.build_client 用的 base_url）不支持
grounding，故这里走原生 REST，与 OpenAI 兼容路径是两条独立通道。
输入 messages 为 OpenAI 格式（system/user/assistant，user 可含 image_url），
本模块负责转换为 Gemini 原生格式。

若线上实测 googleSearch 返回 400，可尝试把 tools 里的 googleSearch 换成
googleSearchRetrieval（不同 API 版本字段名略有差异）。
"""

import re

import httpx
from nonebot import get_driver
from nonebot.log import logger

from . import llm_providers

_GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)


def _data_url_to_inline(data_url: str):
    """data URL -> (base64_str, mime_type)，解析失败返回 (None, None)。"""
    m = _DATA_URL_RE.match(data_url or "")
    if not m:
        return None, None
    return m.group(2), m.group(1)


def _text_of(content):
    if isinstance(content, str):
        return content
    return "".join(
        p.get("text", "") for p in content
        if isinstance(p, dict) and p.get("type") == "text"
    )


def _parts_of(content):
    """OpenAI content -> Gemini parts 列表（文本 / 内联图片）。"""
    if isinstance(content, str):
        return [{"text": content}] if content.strip() else []
    parts = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            t = p.get("text", "")
            if t.strip():
                parts.append({"text": t})
        elif p.get("type") == "image_url":
            url = (p.get("image_url") or {}).get("url", "")
            b64, mime = _data_url_to_inline(url)
            if b64:
                parts.append({"inline_data": {"mime_type": mime or "image/png", "data": b64}})
    return parts


def _clean_contents(contents):
    """Gemini 要求 contents 以 user 开头、user/model 交替、无空 parts。"""
    out = [c for c in contents if c.get("parts")]
    while out and out[0].get("role") != "user":
        out.pop(0)
    merged = []
    for c in out:
        if merged and merged[-1]["role"] == c["role"]:
            continue  # 连续同角色在 Gemini 非法：丢弃重复项，保留首个
        merged.append(c)
    return merged


def _to_gemini(messages):
    """OpenAI 格式 messages -> (system_text, contents)。"""
    sys_text = ""
    contents = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            t = _text_of(content)
            if t.strip():
                sys_text += t + "\n"
            continue
        grole = "user" if role == "user" else "model"
        parts = _parts_of(content)
        if parts:
            contents.append({"role": grole, "parts": parts})
    return sys_text.strip(), _clean_contents(contents)


async def gemini_grounded_reply(
    group_id: str,
    messages: list,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    timeout: float = 60.0,
) -> str:
    """Gemini 原生 grounding：带 Google Search 实时联网回答。

    返回模型文本；任何异常（缺 key / 网络 / 解析）都向上抛出，
    由调用方（ai_chat）捕获后降级（保留普通回复或提示）。
    """
    cfg = get_driver().config
    api_key = (getattr(cfg, "gemini_api_key", "") or "") if cfg else ""
    if not api_key:
        raise ValueError(
            "未配置 GEMINI_API_KEY，无法使用 Gemini 原生联网（请在 .env 设置 GEMINI_API_KEY）"
        )

    model = llm_providers.get_chat_model(group_id or "", "ai_chat")
    sys_text, contents = _to_gemini(messages)
    if not contents:
        raise ValueError("Gemini grounding：无有效对话内容")

    body = {
        "contents": contents,
        "tools": [{"googleSearch": {}}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if sys_text:
        body["systemInstruction"] = {"parts": [{"text": sys_text}]}

    url = _GEMINI_GENERATE_URL.format(model=model) + "?key=" + api_key
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url, json=body, headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.text[:300]
        except Exception:
            pass
        raise ValueError(f"Gemini grounding HTTP {e.response.status_code}: {detail}")
    except Exception as e:
        raise ValueError(f"Gemini grounding 请求失败: {e}")

    try:
        parts = data["candidates"][0]["content"]["parts"]
        # gemini-3.x 默认开启思考，回复 parts 会夹带 thought:true 的思考段，
        # 必须过滤掉，否则 AI 会把内部思考内容一起当成回答发出来。
        text = "".join(
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and not p.get("thought")
        ).strip()
        if not text:
            raise ValueError("Gemini grounding：模型返回为空")
        return text
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Gemini grounding 响应解析失败: {e} | {str(data)[:200]}")
