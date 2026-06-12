"""
龙哥工具箱 - NoneBot 插件

模块：
- common: 积分系统、重启
- pet: 宠物系统
- search: 搜图功能
"""

import base64
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from nonebot import get_driver, require, get_bots, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.matcher import Matcher

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")

from .common import points_commands, restart, life_utils, ai_chat
from .pet import pet_commands
from .search import commands
from .config import Config, init_enabled_groups
from .common.points_data import init_data
from .pet.pet_data import init_pet_data, refresh_all_stamina
from .common.lottery_pool import draw_lottery, get_pool_status

__version__ = "0.3.0"

__plugin_meta__ = PluginMetadata(
    name="龙哥工具箱",
    description="实用工具合集，支持以图搜图、积分系统等功能",
    usage="""
    lg帮助 - 查看所有功能
    搜图帮助 - 查看搜图帮助
    宠物帮助 - 查看宠物帮助
    """,
    type="application",
    homepage="https://github.com/liuqiaochi/nonebot_plugin_longgeTools",
    supported_adapters={"~onebot.v11"},
    config=Config,
)

# 获取配置并初始化
driver = get_driver()


@driver.on_startup
async def init_config():
    """插件启动时初始化配置"""
    init_data()
    init_pet_data()
    logger.info("龙哥工具箱: 宠物系统数据加载完成")

    config = driver.config
    default_groups = getattr(config, "longge_search_enabled_groups", set())
    if default_groups:
        init_enabled_groups(set(str(g) for g in default_groups))
        logger.info(f"龙哥工具箱: 已加载 {len(default_groups)} 个默认开启的群聊")


# ========== 定时任务 ==========
from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour=0, minute=0, id="pet_stamina_refresh")
async def daily_stamina_refresh():
    """每天0点刷新所有宠物体力"""
    refresh_all_stamina()
    logger.info("龙哥工具箱: 宠物体力刷新完成")


# ========== lg帮助图片生成 ==========

# 颜色定义
_BG_COLOR = (45, 45, 55)
_TEXT_COLOR = (255, 255, 255)
_TITLE_COLOR = (255, 200, 100)
_SECTION_COLOR = (100, 200, 255)
_DESC_COLOR = (160, 160, 180)
_DIVIDER_COLOR = (80, 80, 95)
_ADMIN_COLOR = (255, 160, 100)


def _try_load_font(size: int):
    """尝试加载中文字体"""
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


def _generate_help_image() -> str:
    """生成 lg帮助 图片，返回 base64 PNG"""
    font_title = _try_load_font(30)
    font_section = _try_load_font(22)
    font_cmd = _try_load_font(20)
    font_desc = _try_load_font(14)

    width = 520
    padding = 25
    header_height = 70
    section_gap = 15
    item_height = 50

    # 帮助内容定义
    sections = [
        ("积分系统", [
            ("签到 / 打卡", "每日签到获取积分"),
            ("积分 / 查积分", "查看积分信息"),
            ("抽签 / 今日运气", "每日抽签（撞大运概率2%）"),
            ("新手大礼包", "领取新手大礼包"),
        ]),
        ("搜图功能", [
            ("lg搜图", "引用图片进行搜索"),
            ("搜图帮助", "查看搜图帮助"),
        ]),
        ("生活工具", [
            ("lg天气 城市", "查询天气（支持今天/明天/后天）"),
            ("lg换算 金额 来源 目标", "汇率换算（如 lg换算 100 人民币 美元）"),
        ]),
        ("AI 对话", [
            ("lg问 / lgai", "基于 DeepSeek 的 AI 问答（如 lg问 今天天气怎么样）"),
            ("lg清记忆", "清除当前对话历史"),
        ]),
        ("宠物系统", [
            ("我的宠物", "查看或领养宠物"),
            ("宠物帮助", "查看宠物系统帮助"),
        ]),
        ("管理指令", [
            ("开启/关闭 lg搜图", "管理群聊搜图功能"),
            ("开启/关闭 积分", "管理群聊积分系统"),
            ("lg公告", "发布插件更新公告"),
            ("重启bot", "重启机器人"),
        ], True),
    ]

    # 计算总高度
    total_height = padding + header_height
    for name, items, *rest in sections:
        is_admin = rest[0] if rest else False
        total_height += 35 + len(items) * item_height + section_gap
    total_height += padding

    img = Image.new("RGB", (width, total_height), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = padding

    # 标题
    title = "龙哥工具箱"
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    title_w = title_bbox[2] - title_bbox[0]
    draw.text(((width - title_w) // 2, y), title, fill=_TITLE_COLOR, font=font_title)
    y += header_height

    for section_name, items, *rest in sections:
        is_admin = rest[0] if rest else False
        section_color = _ADMIN_COLOR if is_admin else _SECTION_COLOR

        # 分隔线
        draw.line([(padding, y - 5), (width - padding, y - 5)], fill=_DIVIDER_COLOR, width=1)

        # 分区标题
        draw.text((padding, y), f"【{section_name}】", fill=section_color, font=font_section)
        y += 35

        for cmd, desc in items:
            draw.text((padding + 10, y), cmd, fill=_TEXT_COLOR, font=font_cmd)
            draw.text((padding + 20, y + 25), desc, fill=_DESC_COLOR, font=font_desc)
            y += item_height

        y += section_gap

    # 底部版本号
    version_text = f"v{__version__}"
    ver_bbox = draw.textbbox((0, 0), version_text, font=font_desc)
    ver_w = ver_bbox[2] - ver_bbox[0]
    draw.text(((width - ver_w) // 2, total_height - padding - 5), version_text, fill=(100, 100, 110), font=font_desc)

    # 输出为base64
    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()


# ========== lg帮助指令 ==========
lg_help_cmd = on_command("lg帮助", aliases={"龙哥帮助", "lg help"}, priority=5, block=True, force_whitespace=True)


@lg_help_cmd.handle()
async def handle_lg_help(matcher: Matcher, event: MessageEvent):
    """处理lg帮助指令"""
    img_b64 = _generate_help_image()
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))
