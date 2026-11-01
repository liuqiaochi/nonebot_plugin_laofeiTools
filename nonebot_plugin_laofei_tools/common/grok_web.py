"""Grok 联网（xAI 官方 hosted tools）封装。

背景（2026-11 官方现状）：
- 旧版 Live Search 已下线 —— 在 /v1/chat/completions 里带 ``search_parameters``
  会被服务端直接拒绝（官方提示 "Live search is deprecated"），表现为 400。
- 现行方式是 **Responses API + 服务端托管工具**：

      POST https://api.x.ai/v1/responses
      {
        "model": "grok-4.7",
        "input": [{"role": "user", "content": "..."}],
        "tools": [{"type": "web_search"}]
      }

  由 Grok 自己在推理过程中决定是否检索、检索几次（``x_search`` 可另行叠加）。

本模块职责：
1. ``to_responses_input()``：把插件内部 Chat Completions 风格的 messages
   （含多模态 text / image_url 段）转换成 Responses API 的 input；
2. ``extract_text()``：统一提取最终答案文本（兼容 SDK 的 ``output_text``
   与原始 ``output`` 结构）；
3. ``web_answer()``：一次联网问答，返回纯文本。失败**直接抛异常**，由调用方决定降级。
"""

from typing import List

from nonebot.log import logger
from openai import OpenAI


def _image_url_of(part: dict) -> str:
    """从 Chat Completions 的 image_url 段里取出 url 字符串。"""
    val = part.get("image_url")
    if isinstance(val, dict):
        val = val.get("url")
    return val if isinstance(val, str) else ""


def to_responses_input(messages) -> List[dict]:
    """Chat Completions messages -> Responses API input 列表。

    - 字符串 content 原样保留（Responses 接受简写形式）
    - 列表 content：text -> input_text，image_url -> input_image
    """
    items: List[dict] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"
        content = msg.get("content")
        if isinstance(content, list):
            parts: List[dict] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    parts.append({"type": "input_text", "text": part.get("text", "")})
                elif ptype == "image_url":
                    url = _image_url_of(part)
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
            items.append({"role": role, "content": parts})
        else:
            items.append({"role": role, "content": content if content is not None else ""})
    return items


def extract_text(resp) -> str:
    """从 Responses 响应中取出最终文本（优先 output_text，其次遍历 output）。"""
    text = getattr(resp, "output_text", None)
    if text:
        return str(text).strip()
    chunks: List[str] = []
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            chunk = getattr(part, "text", None)
            if chunk:
                chunks.append(str(chunk))
    return "".join(chunks).strip()


def web_answer(
    client: OpenAI,
    model: str,
    messages,
    effort: str = "",
    timeout: float = 180.0,
) -> str:
    """调用 Responses API + web_search 工具，返回最终答案文本。

    :param effort: reasoning_effort（low/medium/high/xhigh）；空串表示不指定。
                   若该模型不接受 reasoning 参数，会自动去掉后重试一次。
    :param timeout: 联网 + 推理较慢，默认给足 180s。
    :raises Exception: 两次都失败时抛出，交给调用方降级。
    """
    base_kwargs = dict(
        model=model,
        input=to_responses_input(messages),
        tools=[{"type": "web_search"}],
        timeout=timeout,
    )
    if effort:
        try:
            resp = client.responses.create(**base_kwargs, reasoning={"effort": effort})
            return extract_text(resp)
        except Exception as e:
            logger.warning(f"[Grok联网] reasoning 参数被拒，去掉后重试: {e}")
    resp = client.responses.create(**base_kwargs)
    return extract_text(resp)
