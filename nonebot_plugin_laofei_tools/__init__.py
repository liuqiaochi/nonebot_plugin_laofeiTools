"""
老肥工具箱 - NoneBot 插件

功能：
- 以图搜图（soutubot.moe）
- 群聊专用，默认关闭，超级用户可开启
"""

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_localstore")

from . import commands
from .config import Config, init_enabled_groups

__version__ = "0.1.0"

__plugin_meta__ = PluginMetadata(
    name="老肥工具箱",
    description="实用工具合集，支持以图搜图等功能",
    usage="""
    指令：
        开启lf搜图 - 超级用户开启群聊搜图功能
        lf搜图 - 引用图片进行搜索（需先开启）
        关闭lf搜图 - 超级用户关闭群聊搜图功能
    
    说明：
        - 搜图功能仅在群聊可用
        - 默认关闭，需超级用户发送「开启lf搜图」开启
    """,
    type="application",
    homepage="https://github.com/yourname/nonebot-plugin-laofei-tools",
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
        from nonebot.log import logger
        logger.info(f"老肥工具箱: 已加载 {len(default_groups)} 个默认开启的群聊")
