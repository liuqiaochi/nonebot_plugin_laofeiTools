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


def responses_available(client) -> bool:
    """当前 openai SDK 是否支持 Responses API（需 openai >= 1.66）。"""
    try:
        res = getattr(client, "responses", None)
        return res is not None and callable(getattr(res, "create", None))
    except Exception:
        return False


# 开启「联网」且对话模型为 Grok 时注入的系统提示：
# 引导它真的去用 web_search，而不是凭旧知识回一句「不知道」。
WEB_HINT = (
    "本群已开启联网搜索。凡涉及时效性信息（天气、新闻、价格、股价、赛事、"
    "新版本发布、近期事件等），你必须先使用 web_search 工具检索核实后再作答，"
    "并在回答中标注来源链接；不要仅凭训练数据猜测，也不要用「不知道」或"
    "让对方自己去查的方式推脱。"
)

# 通道 B（DS 检索 + Grok 作答）注入的系统提示
CONTEXT_SYSTEM = (
    "以下是刚刚为你实时联网检索到的最新资料，请优先依据这些资料回答用户问题；"
    "资料与问题无关的部分请忽略。回答末尾用「来源：<标题> <链接>」列出你实际引用的条目。"
)


def answer_with_context(
    client: OpenAI,
    model: str,
    messages,
    ctx: str,
    extra: dict = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> str:
    """兜底通道：把外部检索到的资料注入上下文，让 Grok 据此作答（不依赖 Responses API）。

    :param extra: 需并入 extra_body 的额外参数（如 reasoning_effort 提速）。
    """
    msgs = list(messages or []) + [
        {"role": "system", "content": f"{CONTEXT_SYSTEM}\n\n{ctx}"}
    ]
    kwargs = dict(model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens)
    if extra:
        kwargs["extra_body"] = dict(extra)
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def looks_ungrounded(reply: str) -> bool:
    """粗略判断回答是否「没拿到联网资料」而推脱（用于自动升级到通道 B）。

    判定：既没有任何链接，又出现典型的「查不到/不知道」措辞。
    """
    text = reply or ""
    if "http" in text or "www." in text:
        return False
    cues = (
        "不知道", "无法确定", "无法获取", "无法查询", "查不到", "没有实时",
        "不能获取", "无法知道", "不清楚", "暂无实时", "无法为你",
    )
    return any(c in text for c in cues)


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
