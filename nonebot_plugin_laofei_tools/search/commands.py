"""
NoneBot 指令处理

指令：
    lg搜图 - 引用图片进行以图搜图（群聊可用，需先开启）
    开启lg搜图 - 超级用户开启群聊搜图功能
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

from ..config import enable_group, is_group_enabled
from ..common.utils import download_image
from .soutubot import get_client


# ========== 搜图指令 ==========
search_image = on_command(
    "lg搜图",
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
        await matcher.finish("搜图功能未开启，请联系超级用户发送「开启lg搜图」")
        return
    
    # 3. 检查是否引用了消息
    if not event.reply:
        await matcher.finish("请引用一张图片后发送「lg搜图」")
        return
    
    # 4. 获取被引用消息中的图片
    image_url: Optional[str] = None
    for seg in event.reply.message:
        if seg.type == "image":
            image_url = seg.data.get("url") or seg.data.get("file")
            break
    
    if not image_url:
        await matcher.finish("引用的消息中没有图片，请引用一张图片后发送「lg搜图」")
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
    "开启lg搜图",
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
    await matcher.finish("✅ 已开启本群搜图功能，现在可以发送「lg搜图」进行搜索了")


# ========== 关闭功能指令（超级用户） ==========
disable_search = on_command(
    "关闭lg搜图",
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
    
    from ..config import disable_group
    disable_group(group_id)
    await matcher.finish("❌ 已关闭本群搜图功能")


# ========== 工具函数 ==========


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
            "uin": str(bot.selg_id),
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
                "uin": str(bot.selg_id),
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
    
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
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
    import base64
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont

    def _try_load_font(size: int):
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for fp in font_paths:
            try:
                return ImageFont.truetype(fp, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    font_title = _try_load_font(28)
    font_section = _try_load_font(22)
    font_cmd = _try_load_font(20)
    font_desc = _try_load_font(14)

    # 颜色
    BG = (45, 45, 55)
    TITLE_C = (255, 200, 100)
    SECTION_C = (100, 200, 255)
    ADMIN_C = (255, 160, 100)
    TEXT_C = (255, 255, 255)
    DESC_C = (160, 160, 180)
    DIVIDER_C = (80, 80, 95)
    NUM_C = (120, 200, 120)

    width = 520
    padding = 25
    header_h = 60
    section_h = 35
    item_h = 50
    tip_gap = 15

    # 内容
    sections = [
        ("搜图指令", [
            ("lg搜图", "引用图片进行搜索"),
        ]),
        ("管理指令", [
            ("开启lg搜图", "开启搜图功能（超管）"),
            ("关闭lg搜图", "关闭搜图功能（超管）"),
        ], True),
    ]
    tips = [
        "1. 需要超级用户先发送「开启lg搜图」开启功能",
        "2. 引用一张图片后发送「lg搜图」进行搜索",
        "3. 搜图功能仅在群聊可用",
    ]

    # 计算高度
    h = padding + header_h
    for name, items, *rest in sections:
        h += section_h + len(items) * item_h + tip_gap
    h += tip_gap + len(tips) * 22 + padding

    img = Image.new("RGB", (width, h), BG)
    draw = ImageDraw.Draw(img)
    y = padding

    # 标题
    title = "搜图功能帮助"
    t_bbox = draw.textbbox((0, 0), title, font=font_title)
    draw.text(((width - (t_bbox[2] - t_bbox[0])) // 2, y), title, fill=TITLE_C, font=font_title)
    y += header_h

    for section_name, items, *rest in sections:
        is_admin = rest[0] if rest else False
        sc = ADMIN_C if is_admin else SECTION_C
        draw.line([(padding, y - 5), (width - padding, y - 5)], fill=DIVIDER_C, width=1)
        draw.text((padding, y), f"【{section_name}】", fill=sc, font=font_section)
        y += section_h
        for cmd, desc in items:
            draw.text((padding + 10, y), cmd, fill=TEXT_C, font=font_cmd)
            draw.text((padding + 20, y + 25), desc, fill=DESC_C, font=font_desc)
            y += item_h
        y += tip_gap

    # 使用说明
    draw.line([(padding, y - 5), (width - padding, y - 5)], fill=DIVIDER_C, width=1)
    draw.text((padding, y), "【使用说明】", fill=SECTION_C, font=font_section)
    y += section_h
    for tip in tips:
        draw.text((padding + 10, y), tip, fill=NUM_C, font=font_desc)
        y += 22

    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    img_b64 = base64.b64encode(out.getvalue()).decode()
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


# ========== 重启通知指令（超级用户隐藏指令） ==========
restart_notify_cmd = on_command("#准备重启", permission=SUPERUSER, priority=1, block=True, force_whitespace=True)


@restart_notify_cmd.handle()
async def handle_restart_notify(matcher: Matcher, bot: Bot, event: MessageEvent):
    """超级用户发送重启通知到所有群"""
    sent_count = 0
    try:
        group_list = await bot.get_group_list()
        for group in group_list:
            group_id = group.get("group_id")
            if group_id:
                try:
                    await bot.send_group_msg(group_id=group_id, message="重启中～请稍等一分钟！")
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"发送重启通知到群 {group_id} 失败: {e}")
    except Exception as e:
        logger.error(f"获取群列表失败: {e}")
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"获取群列表失败: {e}")
        ]))
        return

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"已向 {sent_count} 个群发送重启通知")
    ]))