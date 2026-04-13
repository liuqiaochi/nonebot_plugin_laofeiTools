"""
老肥工具箱 - NoneBot 插件

功能：
- 以图搜图（soutubot.moe）
- 积分系统（签到、银行、抽奖、猜数字等）
- 群聊专用，默认关闭，超级用户可开启
"""

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata
from nonebot.log import logger

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")

from . import commands
from . import points_commands
from .config import Config, init_enabled_groups
from .points_data import calculate_bank_interest

__version__ = "0.2.0"

__plugin_meta__ = PluginMetadata(
    name="老肥工具箱",
    description="实用工具合集，支持以图搜图、积分系统等功能",
    usage="""
    搜图指令：
        开启lf搜图 - 超级用户开启群聊搜图功能
        lf搜图 - 引用图片进行搜索（需先开启）
        关闭lf搜图 - 超级用户关闭群聊搜图功能
    
    积分指令：
        签到/打卡 - 每日签到获取积分
        积分/查积分/我的积分 - 查看积分信息
        转账 积分 @某人 - 转账给他人
        存入银行 积分 - 存入银行
        取出银行 积分 - 取出银行
        抢银行 - 抢银行获取积分
        抽奖 积分 - 消耗积分抽奖
        猜数字 积分 - 开始猜数字游戏
        我猜 数字 - 猜数字
        打劫 @某人 - 打劫他人积分
    
    说明：
        - 搜图功能仅在群聊可用
        - 默认关闭，需超级用户发送「开启lf搜图」开启
    """,
    type="application",
    homepage="https://github.com/liuqiaochi/nonebot_plugin_laofeiTools",
    supported_adapters={"~onebot.v11"},
    config=Config,
)

# 获取配置并初始化
driver = get_driver()


@driver.on_startup
async def init_config():
    """插件启动时初始化配置"""
    config = driver.config
    
    # 从配置中读取默认开启的群聊
    default_groups = getattr(config, "laofei_search_enabled_groups", set())
    if default_groups:
        init_enabled_groups(set(str(g) for g in default_groups))
        logger.info(f"老肥工具箱: 已加载 {len(default_groups)} 个默认开启的群聊")
    
    # 启动时计算一次银行利息（如果今天还没计算）
    calculate_bank_interest()
    logger.info("老肥工具箱: 银行利息计算完成")


# ========== 定时任务：每天0点计算银行利息 ==========
from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour=0, minute=0, id="bank_interest")
async def daily_bank_interest():
    """每天0点计算银行利息"""
    calculate_bank_interest()
    logger.info("老肥工具箱: 每日银行利息计算完成")
