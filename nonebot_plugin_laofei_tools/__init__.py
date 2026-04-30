"""
老肥工具箱 - NoneBot 插件

功能：
- 以图搜图（soutubot.moe）
- 积分系统（签到、银行、抽奖、猜数字等）
- 群聊专用，默认关闭，超级用户可开启
"""

from nonebot import get_driver, require, get_bots
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.log import logger

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")

from . import commands
from . import points_commands
from . import pet_commands
from .config import Config, init_enabled_groups
from .points_data import calculate_bank_interest, init_data
from .pet_data import init_pet_data, refresh_all_stamina
from .lottery_pool import draw_lottery, get_pool_status

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
        十抽 积分 - 消耗10次抽奖机会
        猜数字 积分 - 开始猜数字游戏
        我猜 数字 - 猜数字
#         打劫 @某人 - 打劫他人积分
        抽签/今日运气 - 每日抽签
    
    幸运奖池：
        押注 [数字1-29] [积分10-500] - 押注数字
        奖池 - 查看当前奖池状态
        我的押注 - 查看我的押注记录
        开奖历史 - 查看开奖历史记录
        手动开奖 - 超级用户手动开奖（每2小时自动开奖）
    
    宠物指令：
        我的宠物 - 查看宠物信息/领养宠物
        领养 宠物名 - 领养指定宠物
        宠物散步 - 散步获取经验和道具
        宠物抚摸 - 每日抚摸提升好感度
        宠物喂食 食物名 - 喂食恢复体力和好感度
        宠物pk @某人 - 与他人宠物PK
        宠物商店 - 查看商店商品
        购买 商品名 - 购买商品
        宠物佩戴 配饰名 - 佩戴配饰
        宠物背包 - 查看道具背包
        宠物帮助 - 查看宠物帮助
    
    说明：
        - 搜图功能仅在群聊可用
        - 默认关闭，需超级用户发送「开启lf搜图」开启
        - 幸运奖池每2小时自动开奖（8:00、10:00、12:00、14:00、16:00、18:00、20:00、22:00）
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
    # 加载所有用户数据到缓存（修复重启后数据丢失问题）
    init_data()

    # 加载宠物系统数据
    init_pet_data()
    logger.info("老肥工具箱: 宠物系统数据加载完成")

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


@scheduler.scheduled_job("cron", hour=0, minute=0, id="pet_stamina_refresh")
async def daily_stamina_refresh():
    """每天0点刷新所有宠物体力"""
    refresh_all_stamina()
    logger.info("老肥工具箱: 宠物体力刷新完成")


# ========== 定时任务：每2小时整点幸运奖池开奖（8:00-22:00） ==========
@scheduler.scheduled_job("cron", hour="8-22/2", minute=0, id="lottery_draw")
async def hourly_lottery_draw():
    """每小时整点执行幸运奖池开奖"""
    logger.info("老肥工具箱: 开始执行幸运奖池开奖")
    
    result = draw_lottery()
    
    if result["success"]:
        # 构建中奖信息
        winners_text = ""
        if result["winners"]:
            for winner in result["winners"]:
                winners_text += f"用户 {winner['user_id']}：押注数字 {winner['bet_number']}，获得奖金 {winner['reward']} 积分\n"
        else:
            winners_text = "无人中奖，奖金滚入下一轮"
        
        log_msg = f"""幸运奖池第 {result['current_round']} 轮开奖结果
中奖数字：{result['winning_number']}
总奖池：{result['total_pool']} 积分
中奖者：
{winners_text}
下一轮奖池基数：{result['next_round_base']} 积分"""
        
        logger.info(f"老肥工具箱: 幸运奖池第 {result['current_round']} 轮开奖完成")
        logger.info(f"老肥工具箱: {log_msg}")
        
        # 尝试向所有bot的群聊发送开奖通知
        try:
            bots = get_bots()
            for bot_id, bot in bots.items():
                if isinstance(bot, Bot):
                    # 获取bot加入的群聊列表
                    try:
                        group_list = await bot.get_group_list()
                        for group in group_list:
                            group_id = group["group_id"]
                            # 检查该群聊是否开启了积分系统
                            from .config import is_points_enabled
                            if is_points_enabled(str(group_id)):
                                try:
                                    await bot.send_group_msg(
                                        group_id=group_id,
                                        message=MessageSegment.text(f"🎰 幸运奖池开奖通知\n\n{log_msg}")
                                    )
                                except Exception as e:
                                    logger.warning(f"老肥工具箱: 向群聊 {group_id} 发送开奖通知失败 - {e}")
                    except Exception as e:
                        logger.warning(f"老肥工具箱: 获取群聊列表失败 - {e}")
        except Exception as e:
            logger.warning(f"老肥工具箱: 发送开奖通知失败 - {e}")
    else:
        logger.error(f"老肥工具箱: 幸运奖池开奖失败 - {result.get('message', '未知错误')}")
