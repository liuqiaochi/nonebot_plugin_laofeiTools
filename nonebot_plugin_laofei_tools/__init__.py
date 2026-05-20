"""
龙哥工具箱 - NoneBot 插件

模块：
- common: 积分系统、重启
- pet: 宠物系统
- search: 搜图功能
"""

from nonebot import get_driver, require, get_bots, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.matcher import Matcher

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")

from .common import points_commands, restart
from .pet import pet_commands
from .search import commands
from .config import Config, init_enabled_groups
from .common.points_data import calculate_bank_interest, init_data
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

    calculate_bank_interest()
    logger.info("龙哥工具箱: 银行利息计算完成")


# ========== 定时任务 ==========
from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour=0, minute=0, id="pet_stamina_refresh")
async def daily_stamina_refresh():
    """每天0点刷新所有宠物体力"""
    refresh_all_stamina()
    logger.info("龙哥工具箱: 宠物体力刷新完成")


# ========== lg帮助指令 ==========
lg_help_cmd = on_command("lg帮助", aliases={"龙哥帮助", "lg help"}, priority=5, block=True, force_whitespace=True)


@lg_help_cmd.handle()
async def handle_lg_help(matcher: Matcher, event: MessageEvent):
    """处理lg帮助指令"""
    msg = """龙哥工具箱 - 功能总览

【积分系统】
签到/打卡 - 每日签到获取积分
积分/查积分 - 查看积分信息
抽签/今日运气 - 每日抽签
新手大礼包 - 领取新手大礼包

【搜图功能】
lg搜图 - 引用图片进行搜索
搜图帮助 - 查看搜图帮助

【图片工具】
lg高分 - 引用图片进行高分辨率放大(默认2x)
lg高分 3 - 引用图片进行3x放大(支持2/3/4)

【宠物系统】
我的宠物 - 查看/领养宠物
宠物帮助 - 查看宠物帮助

【管理指令 - 超级用户】
开启lg搜图/关闭lg搜图 - 管理搜图功能
开启积分/关闭积分 - 管理积分系统
重启bot - 重启机器人"""
    await matcher.finish(msg)
