"""
龙哥工具箱 - NoneBot 插件

模块：
- common: 积分系统、重启
- pet: 宠物系统
- search: 搜图功能
"""

from pathlib import Path
import shutil

from nonebot import get_driver, require, get_bots, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.matcher import Matcher

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")

from .common import points_commands, restart, life_utils, ai_chat, rest_mode, qrcode_tool, glossary, novelai_draw
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


@scheduler.scheduled_job("cron", hour=0, minute=0, id="daily_data_backup")
async def daily_data_backup():
    """每天0点备份所有数据文件到 backup 目录"""
    data_dir = Path("data/laofei_tools")
    if not data_dir.exists():
        logger.warning("龙哥工具箱: 数据目录不存在，跳过备份")
        return

    # 按日期创建备份目录
    from datetime import date
    today = date.today().isoformat()
    backup_root = data_dir / "backup"
    backup_dir = backup_root / today
    backup_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in data_dir.iterdir():
        if f.is_file() and f.suffix == ".json":
            try:
                shutil.copy2(f, backup_dir / f.name)
                count += 1
            except Exception as e:
                logger.error(f"龙哥工具箱: 备份 {f.name} 失败: {e}")

    logger.info(f"龙哥工具箱: 每日备份完成，共 {count} 个文件 -> {backup_dir}")

    # 清理超过 30 天的旧备份
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=30)
    for d in backup_root.iterdir():
        if d.is_dir():
            try:
                dir_date = date.fromisoformat(d.name)
                if dir_date < cutoff:
                    shutil.rmtree(d)
                    logger.info(f"龙哥工具箱: 清理旧备份 {d.name}")
            except (ValueError, OSError):
                pass


# ========== lg帮助图片生成 ==========
from .common.help_image import render_help_image


# 帮助分区内容（模块级，供 _generate_help_image 使用）
sections = [
    ("积分系统", [
        ("签到 / 打卡", "每日签到获取积分"),
        ("积分 / 查积分", "查看积分信息"),
        ("抽签 / 今日运气", "每日抽签（撞大运概率2%）"),
        ("新手大礼包", "领取新手大礼包"),
    ]),
    ("搜图功能", [
        ("搜图帮助", "查看搜图功能帮助"),
    ]),
    ("生活工具", [
        ("lg天气 城市", "查询天气（支持今天/明天/后天）"),
        ("lg换算 金额 来源 目标", "汇率换算（如 lg换算 100 人民币 美元）"),
    ]),
    ("AI 对话", [
        ("ai帮助", "查看 AI 对话帮助"),
    ]),
    ("宠物系统", [
        ("宠物帮助", "查看宠物系统帮助"),
    ]),
    ("名词查询", [
        ("名词解释 <名词>", "解释术语用处与获取（如 名词解释 好感）"),
    ]),
    ("AI 画图", [
        ("ai画图帮助", "查看 AI 画图帮助"),
    ]),
    ("二维码工具", [
        ("二维码识别", "引用/发送含二维码的图片后发此指令，识别图中二维码内容"),
        ("生成二维码", "引用一条文本后发此指令，或直接发「生成二维码 内容」，生成二维码图片"),
    ]),
    ("管理指令", [
        ("开启/关闭 lg搜图", "管理群聊搜图功能"),
        ("开启/关闭 积分", "管理群聊积分系统"),
        ("开启AI / 关闭AI", "管理群聊 AI 功能"),
        ("AI拉黑 / AI解除", "管理 AI 黑名单"),
        ("lg公告", "发布插件更新公告"),
        ("重启bot", "重启机器人"),
    ], True),
]


def _generate_help_image() -> str:
    """生成 lg帮助 图片（样式与 ai画图帮助 等一致）"""
    return render_help_image("龙哥工具箱", sections, footer=f"v{__version__}")


# ========== lg帮助指令 ==========
lg_help_cmd = on_command("lg帮助", aliases={"龙哥帮助", "lg help"}, priority=5, block=True, force_whitespace=True)


@lg_help_cmd.handle()
async def handle_lg_help(matcher: Matcher, event: MessageEvent):
    """处理lg帮助指令"""
    img_b64 = _generate_help_image()
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))
