"""
NoneBot 指令处理

指令：
    lf搜图 - 引用图片进行以图搜图（群聊可用，需先开启）
    开启lf搜图 - 超级用户开启群聊搜图功能
"""

from typing import List, Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .config import enable_group, is_group_enabled
from .soutubot import get_client


# ========== 搜图指令 ==========
search_image = on_command(
    "lf搜图",
    priority=5,
    block=True,
    force_whitespace=True,
)


@search_image.handle()
async def handle_search_image(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理搜图指令"""
    
    # 1. 私聊不可用
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("搜图功能仅在群聊可用")
        return
    
    # 2. 检查群聊是否开启了功能
    group_id = str(event.group_id)
    if not is_group_enabled(group_id):
        await matcher.finish("搜图功能未开启，请联系超级用户发送「开启lf搜图」")
        return
    
    # 3. 检查是否引用了消息
    if not event.reply:
        await matcher.finish("请引用一张图片后发送「lf搜图」")
        return
    
    # 4. 获取被引用消息中的图片
    image_url: Optional[str] = None
    for seg in event.reply.message:
        if seg.type == "image":
            image_url = seg.data.get("url") or seg.data.get("file")
            break
    
    if not image_url:
        await matcher.finish("引用的消息中没有图片，请引用一张图片后发送「lf搜图」")
        return
    
    # 5. 发送等待提示
    await matcher.send("正在搜索中，请稍候...")
    
    try:
        # 下载图片
        image_data = await download_image(bot, image_url)
        if not image_data:
            await matcher.finish("图片下载失败，请重试")
            return
        
        logger.info(f"图片下载成功，大小: {len(image_data)} bytes")
        
        # 调用搜图 API
        client = await get_client()
        result = await client.search(image_data)
        
        logger.info(f"搜图API返回: {result}")
        
        # 构建并发送合并转发消息
        await send_forward_message(bot, event, result)
        
    except Exception as e:
        logger.exception("搜图失败")
        await matcher.finish(f"搜索失败：{str(e)}")


# ========== 开启功能指令（超级用户） ==========
enable_search = on_command(
    "开启lf搜图",
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@enable_search.handle()
async def handle_enable_search(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """超级用户开启群聊搜图功能"""
    
    # 必须在群聊中使用
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令")
        return
    
    group_id = str(event.group_id)
    
    if is_group_enabled(group_id):
        await matcher.finish("搜图功能已开启")
        return
    
    enable_group(group_id)
    await matcher.finish("✅ 已开启本群搜图功能，现在可以发送「lf搜图」进行搜索了")


# ========== 关闭功能指令（超级用户） ==========
disable_search = on_command(
    "关闭lf搜图",
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@disable_search.handle()
async def handle_disable_search(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """超级用户关闭群聊搜图功能"""
    
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令")
        return
    
    group_id = str(event.group_id)
    
    if not is_group_enabled(group_id):
        await matcher.finish("搜图功能已关闭")
        return
    
    from .config import disable_group
    disable_group(group_id)
    await matcher.finish("❌ 已关闭本群搜图功能")


# ========== 工具函数 ==========

async def download_image(bot: Bot, image_url: str) -> Optional[bytes]:
    """
    下载图片
    
    Args:
        bot: NoneBot 实例
        image_url: 图片 URL 或文件 ID
    
    Returns:
        图片二进制数据，失败返回 None
    """
    import httpx
    
    try:
        # 如果是 HTTP URL，直接下载
        if image_url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                return resp.content
        
        # 否则尝试通过 OneBot API 获取图片
        try:
            file_info = await bot.get_image(file=image_url)
            if file_info and file_info.get("url"):
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(file_info["url"])
                    resp.raise_for_status()
                    return resp.content
        except Exception:
            pass
        
        # 尝试下载文件
        try:
            file_info = await bot.get_file(file_id=image_url)
            if file_info and file_info.get("file"):
                import aiofiles
                async with aiofiles.open(file_info["file"], "rb") as f:
                    return await f.read()
        except Exception:
            pass
        
        return None
        
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
        return None


async def send_forward_message(
    bot: Bot,
    event: GroupMessageEvent,
    result: dict,
) -> None:
    """
    发送合并转发消息
    
    Args:
        bot: NoneBot 实例
        event: 群聊消息事件
        result: API 返回的搜索结果
    """
    data = result.get("data", [])
    execution_time = result.get("executionTime", 0)
    
    if not data:
        await bot.send(event, "Bot酱没有找到任何结果")
        return
    
    # 按相似度排序，取前5条
    sorted_results = sorted(data, key=lambda x: x.get("similarity", 0), reverse=True)[:5]
    
    # 构建转发消息节点
    nodes = []
    
    # 添加标题节点
    header_msg = f"找到 {len(data)} 条结果（显示最相似的 {len(sorted_results)} 条）\n耗时: {execution_time}ms"
    nodes.append({
        "type": "node",
        "data": {
            "name": "搜图Bot酱",
            "uin": str(bot.self_id),
            "content": header_msg,
        }
    })
    
    # 添加每条结果
    for item in sorted_results:
        similarity = item.get("similarity", 0)
        source = item.get("source", "unknown")
        title = item.get("title", "未知标题")
        preview_url = item.get("previewImageUrl", "")
        subject_path = item.get("subjectPath", "")
        
        # 根据 source 拼接链接
        source_base_urls = {
            "nhentai": "https://nhentai.net",
            "ehentai": "https://e-hentai.org",
        }
        base_url = source_base_urls.get(source, "https://soutubot.moe")
        subject_url = f"{base_url}{subject_path}" if subject_path else ""
        
        # 构建文字信息
        info_text = f"【{source}】相似度: {similarity}%\n{title[:100]}"
        if subject_url:
            info_text += f"\n链接: {subject_url}"
        
        # 下载并处理预览图
        image_base64 = ""
        if preview_url:
            try:
                image_base64 = await download_and_blur_image(preview_url, blur_radius=3)
            except Exception as e:
                logger.warning(f"下载或处理预览图失败: {e}")
        
        # 构建消息内容
        if image_base64:
            # 有图片时，使用CQ码发送图片
            content = f"{info_text}\n[CQ:image,file=base64://{image_base64}]"
        else:
            content = info_text
        
        nodes.append({
            "type": "node",
            "data": {
                "name": f"{source} - {similarity}%",
                "uin": str(bot.self_id),
                "content": content,
            }
        })
    
    # 使用 send_group_forward_msg API 发送合并转发消息
    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=nodes
        )
        logger.info("合并转发消息发送成功")
    except Exception as e:
        logger.error(f"发送合并转发消息失败: {e}")
        # 如果合并转发失败，发送文本格式的结果
        text_results = []
        text_results.append(f"找到 {len(data)} 条结果（显示前 {min(len(sorted_results), 5)} 条）\n耗时: {execution_time}ms\n")
        for i, item in enumerate(sorted_results[:5], 1):
            similarity = item.get("similarity", 0)
            source = item.get("source", "unknown")
            title = item.get("title", "未知标题")
            text_results.append(f"{i}. 【{source}】相似度: {similarity}%\n{title[:50]}\n")
        await bot.send(event, "\n".join(text_results))


async def download_and_blur_image(image_url: str, blur_radius: int = 10) -> str:
    """
    下载图片并进行磨砂（模糊）处理
    
    Args:
        image_url: 图片URL
        blur_radius: 模糊半径，默认10
    
    Returns:
        base64编码的图片数据
    """
    import httpx
    from PIL import Image, ImageFilter
    import base64
    from io import BytesIO
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        image_data = resp.content
    
    # 打开图片
    img = Image.open(BytesIO(image_data))
    
    # 转换为RGB模式（处理RGBA等）
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    # 应用高斯模糊（磨砂效果）
    img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    
    # 压缩图片以减小体积
    max_size = 800
    if img.width > max_size or img.height > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    
    # 保存为JPEG并转为base64
    output = BytesIO()
    img.save(output, format="JPEG", quality=85, optimize=True)
    output.seek(0)
    
    return base64.b64encode(output.getvalue()).decode()
# ========== 搜图帮助指令 ==========
search_help_cmd = on_command("搜图帮助", priority=5, block=True, force_whitespace=True)


@search_help_cmd.handle()
async def handle_search_help(matcher: Matcher, event: MessageEvent):
    """处理搜图帮助指令"""
    msg = """搜图功能帮助

【搜图指令】
lf搜图 - 引用图片进行搜索

【管理指令】
开启lf搜图 - 开启搜图功能(超管)
关闭lf搜图 - 关闭搜图功能(超管)

【使用说明】
1. 需要超级用户先发送「开启lf搜图」开启功能
2. 引用一张图片后发送「lf搜图」进行搜索
3. 搜图功能仅在群聊可用"""
    await matcher.finish(msg)