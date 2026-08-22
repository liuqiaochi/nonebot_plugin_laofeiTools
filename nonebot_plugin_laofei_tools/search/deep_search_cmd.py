"""
lg深度搜图 指令

引用一张图片并发送「lg深度搜图」即可触发，并行调用 IQDB / SauceNAO / ascii2d
三个反搜引擎，结果以「合并转发嵌套合并转发」形式返回：
- 外层：每个服务一个节点；
- 内层：该服务的搜索结果（最多 5 条，有图带图、有链带链）。
"""

import asyncio
from typing import Dict, List

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.matcher import Matcher

from ..common.utils import download_image
from ..config import is_group_enabled
from .deep_search import (
    Ascii2dClient,
    IQDBClient,
    SauceNAOClient,
    SearchResult,
)

# 三个服务及其展示名（顺序即外层合并转发的呈现顺序）
SERVICES = ["IQDB", "SauceNAO", "ascii2d"]
MAX_RESULTS_PER_SERVICE = 5


deep_search_cmd = on_command(
    "lg深度搜图",
    priority=5,
    block=True,
    force_whitespace=True,
)


@deep_search_cmd.handle()
async def handle_deep_search(
    matcher: Matcher,
    bot: Bot,
    event: GroupMessageEvent,
):
    """处理 lg深度搜图 指令"""
    # 1. 私聊不可用
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("深度搜图仅在群聊可用")
        return

    # 2. 检查群聊是否开启搜图功能
    group_id = str(event.group_id)
    if not is_group_enabled(group_id):
        await matcher.finish("搜图功能未开启，请联系超级用户发送「开启lg搜图」")
        return

    # 3. 检查是否引用了图片
    if not event.reply:
        await matcher.finish("请引用一张图片后发送「lg深度搜图」")
        return

    image_url = None
    for seg in event.reply.message:
        if seg.type == "image":
            image_url = seg.data.get("url") or seg.data.get("file")
            break
    if not image_url:
        await matcher.finish("引用的消息中没有图片，请引用一张图片后发送「lg深度搜图」")
        return

    # 4. 下载图片
    await matcher.send("正在深度搜图中（IQDB / SauceNAO / ascii2d）...")
    image_data = await download_image(bot, image_url)
    if not image_data:
        await matcher.finish("图片下载失败，请重试")
        return

    # 5. 并行调用三个服务
    api_key = getattr(get_driver().config, "saucenao_api_key", "")
    try:
        async with (
            IQDBClient() as iqdb,
            SauceNAOClient(api_key) as saucenao,
            Ascii2dClient() as ascii2d,
        ):
            iqdb_res, saucenao_res, ascii2d_res = await asyncio.gather(
                iqdb.search(image_data),
                saucenao.search(image_data),
                ascii2d.search(image_data),
            )
    except Exception as e:
        logger.exception("深度搜图调用失败")
        await matcher.finish(f"深度搜图失败：{e}")
        return

    results_map: Dict[str, List[SearchResult]] = {
        "IQDB": iqdb_res,
        "SauceNAO": saucenao_res,
        "ascii2d": ascii2d_res,
    }

    # 6. 嵌套合并转发返回
    await send_nested_forward(bot, event, results_map)


async def send_nested_forward(
    bot: Bot,
    event: GroupMessageEvent,
    results_map: Dict[str, List[SearchResult]],
) -> None:
    """
    发送嵌套合并转发：
    外层每个服务一个节点，其 content 为内层合并转发（该服务的结果列表）。
    """
    self_id = str(bot.self_id)
    outer_nodes = []

    for svc in SERVICES:
        items = results_map.get(svc, [])[:MAX_RESULTS_PER_SERVICE]
        inner_nodes = _build_service_nodes(self_id, svc, items)
        outer_nodes.append(
            {
                "type": "node",
                "data": {
                    "name": f"深度搜图 · {svc}",
                    "uin": self_id,
                    "content": inner_nodes,  # 嵌套合并转发
                },
            }
        )

    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=outer_nodes,
        )
        logger.info("深度搜图（嵌套合并转发）发送成功")
    except Exception as e:
        logger.error(f"嵌套合并转发失败，降级为纯文本: {e}")
        await _send_fallback(bot, event, results_map)


def _build_service_nodes(self_id: str, svc: str, items: List[SearchResult]) -> List[dict]:
    """构建单个服务的内层合并转发节点（最多 5 条）"""
    if not items:
        return [
            {
                "type": "node",
                "data": {
                    "name": svc,
                    "uin": self_id,
                    "content": f"{svc}：未找到结果",
                },
            }
        ]

    nodes = []
    for idx, r in enumerate(items, 1):
        lines = []
        if r.similarity:
            lines.append(f"【{r.source} · 相似度 {r.similarity}】")
        else:
            lines.append(f"【{r.source}】")
        if r.title:
            lines.append(r.title[:100])
        if r.extra:
            lines.append(r.extra[:150])
        if r.url:
            lines.append(f"链接: {r.url}")
        text = "\n".join(lines)

        # 图片在前、文本在后；无缩略图则仅文本
        content = f"[CQ:image,file={r.thumbnail}]\n{text}" if r.thumbnail else text
        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": f"{svc} #{idx}",
                    "uin": self_id,
                    "content": content,
                },
            }
        )
    return nodes


async def _send_fallback(
    bot: Bot,
    event: GroupMessageEvent,
    results_map: Dict[str, List[SearchResult]],
) -> None:
    """嵌套转发失败时的纯文本降级"""
    blocks = []
    for svc in SERVICES:
        items = results_map.get(svc, [])[:MAX_RESULTS_PER_SERVICE]
        blocks.append(f"===== {svc} =====")
        if not items:
            blocks.append("（未找到结果）")
            continue
        for idx, r in enumerate(items, 1):
            line = f"{idx}. {r.title[:60]}"
            if r.similarity:
                line += f" ({r.similarity})"
            if r.url:
                line += f"\n   {r.url}"
            blocks.append(line)
    await bot.send(event, "\n".join(blocks))
