"""
DeepSeek 联网搜索（自研搜索引擎）

- Search: POST https://api.deepseek.com/v1/tools/search
- Fetch:  POST https://api.deepseek.com/v1/tools/fetch
两者均为原始 JSON API，复用 DEEPSEEK_API_KEY 鉴权（与 AI 对话同 key）。

接入方式：
- 按群开关：超管「开启联网 / 关闭联网」开启后，该群每次 AI 对话前先检索最新资料。
- 单次触发：消息含「联网 / 搜一下 / 搜索 / 查一下」等关键词，本次对话临时联网（无需开群开关）。
- 计费：成功请求 5 元 / 1000 次（2026-11-01 起），失败 / 空结果不计费。
- 失败一律静默降级（返回空串），绝不阻断正常对话。

设计要点：搜索结果仅注入当轮 user 消息，不写入对话历史，避免污染后续轮次上下文。
"""

import json
from typing import List, Optional

from nonebot import get_driver
from nonebot.log import logger

from .data_utils import safe_json_save
from ..config import DATA_DIR

WEBSEARCH_FILE = DATA_DIR / "ds_websearch.json"
_cache: Optional[dict] = None

# 单次触发关键词：消息含其一即临时联网（仅 @bot 对话生效，自由发言仅认群开关）
TRIGGER_KEYWORDS = (
    "联网", "搜一下", "搜搜", "搜索", "查一下", "查最新",
    "最新消息", "最近新闻", "web search", "google",
)

SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"

# 单次搜索返回条数（API 允许 1~50）
DEFAULT_COUNT = 5


# ---------------- 按群开关持久化 ----------------

def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data: dict = {}
    if WEBSEARCH_FILE.exists():
        try:
            data = json.loads(WEBSEARCH_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[联网搜索] {WEBSEARCH_FILE.name} 读取失败，回退默认: {e}")
            data = {}
    data.setdefault("enabled", [])
    _cache = data
    return _cache


def _save() -> None:
    safe_json_save(WEBSEARCH_FILE, _cache)


def is_enabled(group_id: str) -> bool:
    """该群是否开启了「始终联网」（群开关）。"""
    return str(group_id) in _load().get("enabled", [])


def set_enabled(group_id: str, on: bool) -> bool:
    """开启 / 关闭某群联网搜索开关。返回设置后的值。"""
    data = _load()
    s = set(data.get("enabled", []))
    if on:
        s.add(str(group_id))
    else:
        s.discard(str(group_id))
    data["enabled"] = sorted(s)
    _save()
    return on


def should_search(group_id: str, text: str) -> bool:
    """@bot 对话是否应触发联网：群开关开启 或 命中单次触发关键词。"""
    if is_enabled(group_id):
        return True
    t = (text or "").lower()
    return any(k.lower() in t for k in TRIGGER_KEYWORDS)


# ---------------- 搜索调用 ----------------

def _api_key() -> str:
    try:
        return getattr(get_driver().config, "deepseek_api_key", "") or ""
    except Exception:
        return ""


def _format_results(results: List[dict]) -> str:
    """把搜索结果拼成可注入 prompt 的上下文文本。"""
    if not results:
        return ""
    blocks = ["（以下为联网检索到的最新资料，请优先参考，并在回答中标注来源链接）"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        body = r.get("summary") or r.get("snippet") or ""
        blocks.append(f"[{i}] {title}\nURL: {url}\n{body}")
    return "\n\n".join(blocks)


async def search_web(query: str, count: int = DEFAULT_COUNT, need_content: bool = False) -> str:
    """调用 DeepSeek Search API，返回拼好的上下文文本。

    任何异常（无 key / 网络 / 限流 / 非 200）都返回空串，绝不抛出，
    保证联网是「增强」而非「阻断」。
    """
    import httpx

    api_key = _api_key()
    if not api_key:
        logger.warning("[联网搜索] DEEPSEEK_API_KEY 未配置，跳过联网")
        return ""
    payload = {
        "query": (query or "")[:128],
        "count": max(1, min(50, int(count))),
        "need_content": bool(need_content),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                SEARCH_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code != 200:
            logger.warning(f"[联网搜索] HTTP {resp.status_code}: {resp.text[:200]}")
            return ""
        data = resp.json()
        results = data.get("results") or []
        return _format_results(results)
    except Exception as e:
        logger.warning(f"[联网搜索] 调用失败: {e}")
        return ""
