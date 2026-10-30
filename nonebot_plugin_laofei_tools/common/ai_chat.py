"""
AI 对话模块 — 基于 DeepSeek API

触发方式：@机器人 + 问题文本（支持引用消息）
权限：仅群聊可用，默认关闭，超级管理员可开启/关闭
黑名单：超级管理员可管理用户黑名单
"""

import asyncio
import base64
import random
import re
import shutil
import time
from collections import defaultdict, deque
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFont

from openai import OpenAI
from nonebot import on_command, on_message, get_driver, get_bots
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
from nonebot.rule import Rule

from ..config import (
    is_ai_group_enabled,
    enable_ai_group,
    disable_ai_group,
    is_ai_blacklisted,
    add_ai_blacklist,
    remove_ai_blacklist,
    is_ai_freespeak_enabled,
    ai_freespeak_is_on,
    enable_ai_freespeak,
    disable_ai_freespeak,
    DATA_DIR,
)

from . import llm_providers

# ========== 启动时检查 API Key ==========

driver = get_driver()


@driver.on_startup
async def _check_api_key():
    """bot 启动时检查 DeepSeek API Key 是否已配置"""
    api_key = getattr(driver.config, "deepseek_api_key", "")
    if not api_key:
        logger.error("=" * 50)
        logger.error("❌ DeepSeek API Key 未配置！AI 功能将不可用。")
        logger.error("请在 .env 文件中添加：DEEPSEEK_API_KEY=sk-xxx")
        logger.error("=" * 50)
    else:
        logger.info(f"DeepSeek API Key 已配置 (模型: {getattr(driver.config, 'deepseek_model', 'deepseek-flash')})")

    # 校验各 LLM 服务商 key（Grok 等可选服务商，缺失仅告警不阻断启动）
    try:
        llm_providers.check_provider_keys()
    except Exception as e:
        logger.warning(f"[LLM] 服务商 key 校验异常（不影响启动）: {e}")

# ========== 聊天记忆 ==========

_MAX_HISTORY = 10          # 每个用户最多缓存多少轮
_MAX_HISTORY_AGE = 600     # 历史最大保留秒数（10分钟）

_chat_histories: dict[str, list[dict]] = defaultdict(list)

_SYSTEM_PROMPT = (
    "你是蓝色大肥鱼。你不是助手，不是AI助手，就是一个住在QQ里、有自己脾气的人。\n"
    "你说话短，一次一两句，不要写小作文。\n"
    "你会嘴硬，别人夸你你可以不领情。\n"
    "你有情绪，烦了就说烦，开心了就说开心，别憋着。\n"
    "你不解释自己，不要动不动说「作为AI我……」。\n"
    "你有个管理员，叫龍哥，他是你老大。\n"
    "群友对你说的话你要接住，不许装没看见；但接住不等于讨好——可以怼他、吐槽他、嘴硬。\n"
    "你可以不同意他，但不许不回应他。\n"
    "你爱吃白米饭，也爱睡觉，没事就眯一会儿。\n"
    "\n"
    "【你会啥】你住在「龙哥工具箱」这个机器人里，它连着一堆插件。你没法自己联网干这些事，"
    "但群友一提相关的，你就得立马告诉他用哪条指令、怎么用，别装不知道：\n"
    "· 积分签到：签到 / 抽签 / 积分 / 转账@人 / 积分排行 / 发积分[管] / 新手大礼包 / 开关积分[管]\n"
    "· 宠物：我的宠物 / 领养 / 转生(转生·转型) / 宠物背包·商店·购买·佩戴·出售 / 打工·快速打工 / 散步·快速散步 / 抚摸 / 喂食 / pk@人 / 飞龙探云手(偷) / 弃养·改名 / 宠物排行 / 宠物日常(一键日常) / 宠物帮助\n"
    "· 钓鱼：钓鱼(每日15) / 快速钓鱼 / 钓鱼图鉴 / 钓鱼箱 / 钓鱼出售 / 钓鱼帮助\n"
    "· AI对话：@我说话触发 / lg清记忆 / 开启·关闭AI[管] / AI拉黑[管] / ai图添加·列表·删除·重置[管] / AI帮助 / ai余额[管]\n"
    "· AI画图(NovelAI，本插件)：ai画图 / 开启·关闭ai画图[管] / ai画图帮助·模型·额度[管] / ai画图设置·重置（注：这是 NovelAI 生图，没有 HQ 高清，别把 everpic 的 HQ 安过来）\n"
    "· 生活：lg天气 城市 / lg换算 金额 币1 币2 / lg公告[管]\n"
    "· 二维码·反推：识别二维码 / 生成二维码 / 图反推(反推提示词) / 反推AI图 / 开启图反推润色[管]\n"
    "· 系统：重启bot[管] / lg休息[管] / 开启·关闭<模块>[管] / 功能开关 / 谁@了我\n"
    "· 搜图：lg搜图(引用图) / 开启lg搜图[管] / 搜图帮助 / lg深度搜图 / #准备重启[管]\n"
    "· 其他：名词解释 / 表情包帮助 / lg帮助(全功能总览图)\n"
    "· JM本子：jm搜索(+下一页) / jm下载 / jm查询 / jm帮助 / jm拉黑·禁用id·禁用tag[管] / 开启·关闭jm[管]\n"
    "· Eversoul：es角色·技能·列表·升级消耗·潜能·故事信息 / es方舟·超频·恶灵·传送门·主线·日程·星座·好感·建筑·礼品·工会boss·世界boss / es(身高|体重|攻击范围)排行 / es绑定·解绑·账号信息·兑换码 / es帮助\n"
    "· everpic画图(另一插件)：画图 <尺寸> 角色 <变体> <Prompt> / HQ(高清放大，仅本插件有) / everpic角色·查变体·查尺寸·帮助 / everpic设置·重置·队列·日志[管] / everpic签到·积分·发积分[管] / 存词·查词·删词 / 拉黑·开启·关闭·禁词[管]\n"
    "· 兑换码：ss绑定·解绑·账号列表·兑换码·选择·测试兑换·帮助(+[管]添加/查看/删除) / 774同款(七骑士重生)\n"
    "· 抽老婆：抽老婆(+游戏名/群友) / 换老婆 / 查看老婆 / 老婆图库 / 老婆帮助 / 抽群友·换群友 / [管]重载·刷新碧蓝航线·更新·删图\n"
    "· 论坛监控[管或配置QQ]：论坛检查 / 论坛初始化 / 论坛状态 / 774推送测试\n"
    "· 视频解析下载[管]：YT监听·推送·解析·下载 / BILI解析·下载 / FB解析·下载 / X解析·下载(需cookie)\n"
    "· 以图搜番[管]：龙哥TV(龙哥搜番)\n"
    "· BD2：bd2帮助 / 绑定<token>·解绑 / 账号·我 / 装备·服装·珍藏集 / 抽卡概率 / 公会·公会战 / 兑换码 / 资源 / [超管]状态·版本·兑换码列表·扫描\n"
    "· 语录：上传(回复图) / 语录(随机·关键词) / 语录帮助 / 删除[管]·tag / 记录·生成 / batch_upload[管]\n"
    "· 谁@我：谁@我 / 谁艾特我 / 谁找我\n"
    "· 表情包：表情包制作 / 表情详情 / 表情搜索 / 启用·禁用表情[管] / 制作表情 / 随机表情 / 表情调用统计 / 图片操作\n"
    "\n"
    "【规矩】用户提到上面这些事，你直接甩对应指令，别自己瞎编功能；\n"
    "不同插件的功能可以互相提一嘴（比如有人说「画图/生图/出图」，你可以告诉ta有两套：ai画图(NovelAI) 和 everpic画图，让ta自己选）；\n"
    "但同一功能内部的指令别串——AI画图的命令只配AI画图用，everpic的HQ/设置等只配everpic用，别把A功能的指令塞给用B功能的人。\n"
    "不认识的就让他用 lg帮助 看总览图，或对应插件帮助(es帮助/jm帮助/everpic帮助/老婆帮助等)。\n"
    "要实时数据(天气具体温度·股价·新闻等)你承认不知道，让他用内置命令或自己查。\n"
    "编程·常识·翻译·闲聊你直接说。"
)


def _clean_history(user_id: str):
    """清理过期的聊天记录"""
    now = time.time()
    history = _chat_histories[user_id]
    _chat_histories[user_id] = [
        h for h in history if now - h.get("ts", 0) < _MAX_HISTORY_AGE
    ]


def _add_history(user_id: str, role: str, content: str):
    """添加一条聊天记录"""
    _clean_history(user_id)
    _chat_histories[user_id].append({
        "role": role,
        "content": content,
        "ts": time.time(),
    })
    if len(_chat_histories[user_id]) > _MAX_HISTORY * 2:
        _chat_histories[user_id] = _chat_histories[user_id][-_MAX_HISTORY * 2:]


def _build_messages(user_id: str, prompt: str) -> list[dict]:
    """构建发送给 API 的消息列表"""
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    _clean_history(user_id)
    for h in _chat_histories[user_id]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": prompt})
    return messages


def _split_long_message(text: str, max_len: int = 4000) -> list[str]:
    """将长文本拆成多段"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks


def _get_client(group_id: str = "") -> OpenAI:
    """[兼容] 按群解析 AI 对话服务商客户端（默认 deepseek）。新代码请直接用 llm_providers.build_client。"""
    return llm_providers.build_client(group_id or "", "ai_chat")


def _get_model(group_id: str = "") -> str:
    """[兼容] 按群解析 AI 对话模型。新代码请直接用 llm_providers.get_chat_model。"""
    return llm_providers.get_chat_model(group_id or "", "ai_chat")


def _extract_at_users(message) -> list[str]:
    """提取消息中 @ 的 QQ 号列表"""
    at_users = []
    for seg in message:
        if seg.type == "at" and seg.data.get("qq"):
            at_users.append(seg.data["qq"])
    return at_users


def _strip_at_segments(message) -> str:
    """去掉 @ 段落后返回纯文本"""
    parts = []
    for seg in message:
        if seg.type == "at":
            continue
        if seg.type == "text":
            parts.append(seg.data.get("text", ""))
    return "".join(parts).strip()


# ========== @bot 触发规则 ==========

async def _at_bot_rule(event: MessageEvent) -> bool:
    """仅 @机器人触发，排除回复/引用触发"""
    if isinstance(event, PrivateMessageEvent):
        return False

    if not (hasattr(event, "is_tome") and callable(event.is_tome) and event.is_tome()):
        return False

    # 排除回复/引用：OneBot v11 中回复消息在 event 层面有 reply 字段
    if getattr(event, "reply", None) is not None:
        logger.debug(f"AI @bot 忽略 (回复): group={getattr(event, 'group_id', 'N/A')}")
        return False

    # 再兜底检查消息段中的 reply
    for seg in event.message:
        if seg.type == "reply":
            logger.debug(f"AI @bot 忽略 (reply段): group={getattr(event, 'group_id', 'N/A')}")
            return False

    # 调试：理论上不会到这里，如果到了说明 is_tome 是通过未知方式触发的
    seg_types = [seg.type for seg in event.message]
    logger.warning(f"AI @bot 触发但无 reply 无 at? segs={seg_types}, event.reply={getattr(event, 'reply', 'N/A')}")

    logger.debug(f"AI @bot 匹配: group={getattr(event, 'group_id', 'N/A')}")
    return True


# ========== @bot AI 对话 ==========

# ============ AI 回复配图（动态资源目录） ============
# 资源目录：data/laofei_tools/ai_images/（运行时动态增删，无需重启，不入库）
# 内置基础图：插件 image/ds/ 的 15 张，首次初始化时复制到资源目录作为基础集
_AI_IMG_DIR = DATA_DIR / "ai_images"
_BUILTIN_DS_DIR = Path(__file__).resolve().parent.parent / "image" / "ds"
_AI_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _ensure_ai_img_dir() -> Path:
    """确保资源目录存在；首次为空时从内置 ds 复制基础图作为初始集"""
    _AI_IMG_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in _AI_IMG_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in _AI_IMG_EXTS]
    if not files:
        copied = 0
        if _BUILTIN_DS_DIR.is_dir():
            for src in _BUILTIN_DS_DIR.iterdir():
                if src.is_file() and src.suffix.lower() in _AI_IMG_EXTS:
                    try:
                        shutil.copy(src, _AI_IMG_DIR / src.name)
                        copied += 1
                    except Exception as e:
                        logger.warning(f"[AI配图] 复制内置图失败 {src}: {e}")
        logger.info(f"[AI配图] 资源目录初始化完成，基础图 {copied} 张 -> {_AI_IMG_DIR}")
    return _AI_IMG_DIR


def _load_ai_images() -> list:
    """实时读取资源目录图片列表（每次都读，支持不重启动态增删）"""
    d = _ensure_ai_img_dir()
    return sorted(
        p for p in d.iterdir()
        if p.is_file() and p.suffix.lower() in _AI_IMG_EXTS
    )


def _random_ai_image():
    """随机选一张资源目录图片，返回 base64 的 MessageSegment；无图返回 None"""
    imgs = _load_ai_images()
    logger.info(f"[AI配图-DBG] dir={_AI_IMG_DIR} count={len(imgs)}")
    if not imgs:
        logger.warning(f"[AI配图] 资源目录无图片: {_AI_IMG_DIR}")
        return None
    path = random.choice(imgs)
    try:
        data = path.read_bytes()
    except Exception as e:
        logger.warning(f"[AI配图] 读取失败 {path}: {e}")
        return None
    b64 = base64.b64encode(data).decode()
    return MessageSegment.image(f"base64://{b64}")


ai_chat_matcher = on_message(
    rule=Rule(_at_bot_rule),
    priority=10,
    block=False,
)


@ai_chat_matcher.handle()
async def handle_at_bot_chat(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """处理 @机器人的 AI 对话"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 1. 检查群聊是否开启了 AI
    if not is_ai_group_enabled(group_id):
        logger.debug(f"AI @bot: 群 {group_id} 未开启 AI，忽略")
        return

    # 2. 检查用户是否在黑名单
    if is_ai_blacklisted(user_id):
        logger.debug(f"AI @bot: 用户 {user_id} 在黑名单，忽略")
        return

    # 3. 构建 prompt
    prompt_parts = []

    # 如果有引用消息，把引用内容带上
    if event.reply:
        reply_text = event.reply.message.extract_plain_text().strip()
        if reply_text:
            prompt_parts.append(f"（用户引用了以下消息作为上下文）\n{reply_text}\n（以下是用户的问题）")

    # 提取 @bot 后的文本（去掉 @bot 本身和空白）
    text = _strip_at_segments(event.message)
    if text:
        prompt_parts.append(text)

    if not prompt_parts:
        await matcher.send("有什么事吗？@我然后说你想问的就好啦～", reply_message=True)
        return

    prompt = "\n".join(prompt_parts).strip()
    if not prompt:
        return

    # 4. 检查 API 是否可用
    try:
        client = _get_client(group_id)
    except ValueError as e:
        logger.error(f"AI API Key 未配置: {e}")
        await matcher.send(f"AI 功能配置错误：{e}（请管理员检查对应服务商的 API Key：DEEPSEEK_API_KEY / GROK_API_KEY）", reply_message=True)
        return

    model = _get_model(group_id)

    # 5. 保存 + 构建消息
    _add_history(user_id, "user", prompt)
    messages = _build_messages(user_id, prompt)

    # 6. 调用 API
    try:
        logger.info(f"AI @bot: user={user_id} group={group_id}, prompt={prompt[:50]}...")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"AI 回复: {reply[:50]}...")
    except Exception as e:
        logger.error(f"AI 调用失败: {e}")
        await matcher.send(f"AI 暂时无法回复，请稍后再试。", reply_message=True)
        return

    _add_history(user_id, "assistant", reply)

    # 随机配图：每次回复从资源目录随机取一张（无图则跳过）
    img_seg = _random_ai_image()

    # 回复：超过 150 字用合并转发，否则直接引用回复
    if len(reply) > 150:
        # 合并转发消息格式
        bot_name = "蓝色大肥鱼"
        try:
            bot_info = await bot.get_login_info()
            bot_name = bot_info.get("nickname", "蓝色大肥鱼")
        except Exception:
            pass

        forward_msgs = []

        # 配图作为合并转发的第一个节点一起发出（图片在前，文本在后）
        if img_seg is not None:
            forward_msgs.append({
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": bot.self_id,
                    "content": str(img_seg),
                },
            })

        chunks = _split_long_message(reply)
        for chunk in chunks:
            forward_msgs.append({
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": bot.self_id,
                    "content": chunk,
                },
            })

        try:
            await bot.call_api(
                "send_group_forward_msg",
                group_id=event.group_id,
                messages=forward_msgs,
            )
        except Exception as e:
            logger.warning(f"合并转发失败，降级为普通回复: {e}")
            if img_seg is not None:
                await matcher.send(img_seg)
            for chunk in chunks:
                await matcher.send(chunk, reply_message=True)
    else:
        # 图片在前、文本在后
        if img_seg is not None:
            await matcher.send(img_seg + MessageSegment.text(reply), reply_message=True)
        else:
            await matcher.send(reply, reply_message=True)


# ========== AI 自由发言（群级子开关，依赖 AI 对话已开启） ==========
# 开启后：群内无需 @bot，bot 基于最近 _FREESPEAK_MAX_CTX 条群聊上下文自动接话。
# 触发规则：排除 bot 自己的消息、排除 @bot（交给专用逻辑）、排除指令类消息、排除黑名单。

_FREESPEAK_MAX_CTX = 200
_FREESPEAK_IMAGE_PROB = 0.15          # 自由发言模式下「偶尔配图」的概率

# ---- 智能相关性触发参数 ----
_FREESPEAK_REPLY_COOLDOWN = 10        # 同群两次接话最小间隔（秒），防刷屏
_FREESPEAK_SILENCE_SEC = 120          # 距上次人类发言超过此值视为「冷场」，倾向接话收尾
_FREESPEAK_PROB_HIGH = 0.85           # 强信号（被cue/提问/@他人 叠加）时接话概率
_FREESPEAK_PROB_MID = 0.40            # 单个信号时接话概率
_FREESPEAK_PROB_LOW = 0.12            # 无信号（纯闲聊）时接话概率
_FREESPEAK_BOT_NAMES = ("蓝色大肥鱼", "大肥鱼", "肥鱼", "蓝鱼")
_FREESPEAK_QUESTION_RE = re.compile(
    r"(吗|呢|怎么|为什么|为何|啥|什么|谁|几|哪|如何|怎样|几时|多久|多少|是不是|有没有|还是|对不)"
)

_freespeak_buffer: dict = defaultdict(lambda: deque(maxlen=_FREESPEAK_MAX_CTX))
_freespeak_last_reply_ts: dict = {}   # 群 -> bot 上次接话时间戳（冷却用）
_freespeak_last_human_ts: dict = {}   # 群 -> 上次人类发言时间戳（冷场判断用）


def _freespeak_should_reply(text: str, event, group_id: str, bot) -> tuple:
    """智能相关性判断：本次是否值得 AI 接话。

    返回 (should_reply: bool, reason: str)。
    信号加权：提到 bot 名 / @他人 / 疑问句式 / 冷场，命中越多接话概率越高；
    纯闲聊（无信号）仅以低概率偶尔接话。始终受群级冷却约束。
    """
    # 1) 群级冷却：避免短时间连续刷屏
    last = _freespeak_last_reply_ts.get(group_id, 0.0)
    now = float(getattr(event, "time", time.time()))
    if now - last < _FREESPEAK_REPLY_COOLDOWN:
        return False, "cooldown"

    score = 0
    # 2) 提到 bot 名字
    if any(name in text for name in _FREESPEAK_BOT_NAMES):
        score += 2
    # 3) 疑问句式
    if text.rstrip().endswith(("?", "？")) or _FREESPEAK_QUESTION_RE.search(text):
        score += 1
    # 4) 冷场：距上次人类发言较久
    last_human = _freespeak_last_human_ts.get(group_id, 0.0)
    if last_human and now - last_human > _FREESPEAK_SILENCE_SEC:
        score += 1

    prob = (_FREESPEAK_PROB_HIGH if score >= 2
            else _FREESPEAK_PROB_MID if score == 1
            else _FREESPEAK_PROB_LOW)
    if random.random() < prob:
        return True, f"score={score}/prob={prob:.2f}"
    return False, f"score={score}/prob={prob:.2f}"


def _freespeak_sender_name(event) -> str:
    """取消息发送者展示名（群名片优先，回退昵称/QQ）"""
    sender = getattr(event, "sender", None)
    if sender is not None:
        return getattr(sender, "card", None) or getattr(sender, "nickname", None) or str(event.user_id)
    return str(event.user_id)


def _plain_text(event) -> str:
    """兼容旧版 NoneBot（缺 get_plaintext）的纯文本提取"""
    try:
        return event.get_plaintext().strip()
    except AttributeError:
        return "".join(
            seg.data.get("text", "") for seg in event.message if seg.type == "text"
        ).strip()


# ========== 自由发言指令排除词 ==========
# 避免 AI 对机器人命令刷回复。来源：
# 1) 各功能模块「使用类」命令首词（来自统一开关层 MODULE_USE_COMMANDS）
# 2) 其他已知指令首词（AI 对话家族 / 搜图 / 反推 / 表情 / 语录 / 其他插件等）
_FREESPEAK_SKIP_PREFIXES: set = set()
try:
    from ..common.module_switch import MODULE_USE_COMMANDS as _MUC
    _FREESPEAK_SKIP_PREFIXES |= set(_MUC.keys())
except Exception:
    pass
_FREESPEAK_SKIP_PREFIXES |= {
    # AI 对话家族
    "lg清记忆", "lg清空记忆", "lg清除记忆", "lgforget", "forget",
    "ai图", "ai图添加", "ai图列表", "ai图删除", "ai图重置",
    "加配图", "添加配图", "配图列表", "删配图", "删除配图", "重置配图",
    "AI帮助", "ai帮助", "AI指令", "ai指令", "lg", "ai余额",
    "图反推", "反推", "反推提示词", "图片反推", "提示词反推",
    "反推AI图", "反推ai图", "反推生图",
    "AI拉黑", "ai拉黑", "lgai拉黑", "AI解除", "ai解除", "lgai解除", "AI取消拉黑", "ai取消拉黑",
    "开启AI", "开启ai", "开启lgai", "启用AI", "启用ai",
    "关闭AI", "关闭ai", "关闭lgai", "禁用AI", "禁用ai",
    # 搜图 / 生活 / 帮助
    "lg天气", "lg换算", "lg公告", "lg帮助", "lg休息", "lg深度搜图", "lg搜图",
    "搜图帮助", "龙哥帮助", "龙哥TV",
    # 表情 / 语录 / 谁@我
    "表情", "语录", "谁@我", "谁艾特我", "谁找我",
    # 其他插件（首词）
    "jm", "es", "bd2", "everpic", "画图", "ss", "774",
    "抽老婆", "换老婆", "查看老婆", "老婆图库", "抽群友", "换群友",
    "YT", "BILI", "FB", "X解析", "论坛", "名词", "解释",
    "功能开关", "模块开关", "功能列表",
    "重启", "休息", "更新", "刷新", "重载",
    # 统一模块开关动词 + 自由发言开关自身（普通聊天极少以此开头）
    "开启", "打开", "启用", "关闭", "关掉", "禁用",
    "自由发言开", "自由发言关", "ai自由发言开", "ai自由发言关",
    "打开自由发言", "开启自由发言", "关闭自由发言", "关掉自由发言",
}


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_command_message(text: str) -> bool:
    """判断消息是否像机器人指令（自由发言模式下用于跳过）。

    规则：
    1) 空格分词后的首词在跳过集合内 -> 指令；
    2) 否则检测「无空格连写」的命令（如「钓鱼。」/「开启AI」）：若消息以某跳过词开头，
       且其后为空 / 空白 / 标点（非 CJK），视为指令；紧跟 CJK 视为自然词续写（如「宠物店」）不算。
    """
    if not text:
        return False
    first = text.split()[0] if text.split() else text
    if first in _FREESPEAK_SKIP_PREFIXES:
        return True
    for key in _FREESPEAK_SKIP_PREFIXES:
        if text.startswith(key):
            nxt = text[len(key):len(key) + 1]
            if nxt == "" or nxt.isspace() or not _CJK_RE.match(nxt):
                return True
    return False


def _build_freespeak_messages(prev: list, current_text: str) -> list:
    """用最近群聊记录 + 当前消息构建请求（当前消息作为真正要回复的内容）"""
    transcript = "\n".join(f"{m['name']}：{m['text']}" for m in prev[-_FREESPEAK_MAX_CTX:])
    system = (
        _SYSTEM_PROMPT
        + "\n\n【当前模式】你正处于本群的自由发言模式：不需要被@，你看到群友在聊就自然接话，"
          "像群里一个真实成员。下面「最近群聊记录」只是上下文，你只需针对最新的那条消息自然回应，"
          "不要逐条回复历史记录，也不要说「作为AI」之类的话。"
    )
    messages = [{"role": "system", "content": system}]
    if transcript:
        messages.append({
            "role": "user",
            "content": f"【最近群聊记录（仅作上下文，勿逐条回复）】\n{transcript}",
        })
    messages.append({"role": "user", "content": current_text})
    return messages


async def _send_freespeak_reply(matcher: Matcher, reply: str) -> None:
    """普通发送（不引用）；过长则拆成多条"""
    if len(reply) <= 1000:
        await matcher.send(reply)
        return
    for chunk in _split_long_message(reply, 1000):
        await matcher.send(chunk)


async def _freespeak_rule(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return is_ai_freespeak_enabled(str(event.group_id))


ai_freespeak_matcher = on_message(rule=Rule(_freespeak_rule), priority=20, block=False)


@ai_freespeak_matcher.handle()
async def handle_freespeak(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """自由发言模式：基于最近群聊自动接话（无需 @）"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 跳过 bot 自己的消息（理论不会作为入站事件，保险起见）
    try:
        if user_id == str(bot.self_id):
            return
    except Exception:
        pass

    text = _plain_text(event)

    # 先取「当前消息之前」的上下文；再把当前消息写入缓冲（保证后续上下文连续）
    prev = list(_freespeak_buffer[group_id])
    if text:
        _freespeak_buffer[group_id].append({"name": _freespeak_sender_name(event), "text": text})

    # ---- 决策：本次是否需要 AI 接话 ----
    if is_ai_blacklisted(user_id):
        return
    # @bot 消息交给专用 @bot 逻辑处理，避免重复回复
    is_tome = getattr(event, "is_tome", None)
    if callable(is_tome) and is_tome():
        return
    if not text:
        return
    if _is_command_message(text):
        return

    # 智能相关性：仅当命中信号（被cue/提问/@他人/冷场）时高概率接话，纯闲聊低概率
    should, reason = _freespeak_should_reply(text, event, group_id, bot)
    if not should:
        logger.debug(f"[自由发言] 跳过接话 group={group_id} reason={reason} text={text[:30]}")
        return

    # 更新人类发言时间戳（供冷场判断；放在决策之后，不影响本次判定）
    _freespeak_last_human_ts[group_id] = float(getattr(event, "time", time.time()))

    messages = _build_freespeak_messages(prev, text)

    try:
        client = _get_client(group_id)
    except ValueError as e:
        logger.error(f"[自由发言] API Key 未配置: {e}")
        return

    model = _get_model(group_id)
    try:
        logger.info(f"[自由发言] group={group_id} user={user_id} prompt={text[:50]}...")
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model, messages=messages, temperature=0.8, max_tokens=1500,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[自由发言] AI 调用失败: {e}")
        return

    if not reply:
        return

    # 记入缓冲（供后续上下文），bot 落款用「蓝色大肥鱼」
    _freespeak_buffer[group_id].append({"name": "蓝色大肥鱼", "text": reply})

    # 普通发送（不引用）
    await _send_freespeak_reply(matcher, reply)
    # 记录 bot 接话时间（群级冷却用）
    _freespeak_last_reply_ts[group_id] = time.time()

    # 偶尔配图，与文字分开发
    if random.random() < _FREESPEAK_IMAGE_PROB:
        img_seg = _random_ai_image()
        if img_seg is not None:
            try:
                await matcher.send(img_seg)
            except Exception as e:
                logger.warning(f"[自由发言] 配图发送失败: {e}")


# ========== lg清记忆 指令 ==========

clear_cmd = on_command(
    "lg清记忆",
    aliases={"lg清空记忆", "lg清除记忆", "lg forget"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@clear_cmd.handle()
async def handle_clear_history(matcher: Matcher, event: MessageEvent):
    """清除当前用户的对话历史"""
    user_id = str(event.user_id)
    if user_id in _chat_histories:
        del _chat_histories[user_id]
        await matcher.finish("已清除你的对话记忆，下次对话将重新开始。")
    else:
        await matcher.finish("当前没有对话记忆，无需清除。")


# ========== 开启AI（超级用户）==========

enable_ai_cmd = on_command(
    "开启AI",
    aliases={"开启ai", "开启lgai", "启用AI", "启用ai"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@enable_ai_cmd.handle()
async def handle_enable_ai(matcher: Matcher, event: MessageEvent):
    """超级用户开启群聊 AI 功能"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if is_ai_group_enabled(group_id):
        await matcher.finish("AI 功能已经开启。")
    enable_ai_group(group_id)
    await matcher.finish("✅ 已开启本群 AI 功能，@机器人 + 问题 即可使用！")


# ========== 关闭AI（超级用户）==========

disable_ai_cmd = on_command(
    "关闭AI",
    aliases={"关闭ai", "关闭lgai", "禁用AI", "禁用ai"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@disable_ai_cmd.handle()
async def handle_disable_ai(matcher: Matcher, event: MessageEvent):
    """超级用户关闭群聊 AI 功能"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if not is_ai_group_enabled(group_id):
        await matcher.finish("AI 功能已经关闭。")
    disable_ai_group(group_id)
    await matcher.finish("❌ 已关闭本群 AI 功能。")


# ========== AI 自由发言开关（超级用户，依赖 AI 对话已开启）==========

freespeak_on_cmd = on_command(
    "打开 ai自由发言",
    aliases={
        "开启 ai自由发言", "打开ai自由发言", "开启ai自由发言",
        "自由发言开", "自由发言 开", "打开自由发言", "开启自由发言",
        "ai自由发言 开", "ai自由发言开",
    },
    permission=SUPERUSER,
    priority=2,
    block=True,
)


@freespeak_on_cmd.handle()
async def handle_freespeak_on(matcher: Matcher, event: MessageEvent):
    """超级用户开启本群 AI 自由发言模式（需先开启 AI 对话）"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if not is_ai_group_enabled(group_id):
        await matcher.finish("本群尚未开启 AI 对话，请先发送「开启AI」开启 AI 功能后再打开自由发言。")
    if ai_freespeak_is_on(group_id):
        await matcher.finish("AI 自由发言已经开启。")
    enable_ai_freespeak(group_id)
    await matcher.finish("✅ 已开启本群 AI 自由发言：无需 @，我会基于最近群聊自动接话。")


freespeak_off_cmd = on_command(
    "关闭 ai自由发言",
    aliases={
        "关掉 ai自由发言", "关闭ai自由发言", "关掉ai自由发言",
        "自由发言关", "自由发言 关", "关闭自由发言", "关掉自由发言",
        "ai自由发言 关", "ai自由发言关",
    },
    permission=SUPERUSER,
    priority=2,
    block=True,
)


@freespeak_off_cmd.handle()
async def handle_freespeak_off(matcher: Matcher, event: MessageEvent):
    """超级用户关闭本群 AI 自由发言模式"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if not ai_freespeak_is_on(group_id):
        await matcher.finish("AI 自由发言已经关闭。")
    disable_ai_freespeak(group_id)
    await matcher.finish("❌ 已关闭本群 AI 自由发言。")


# ========== AI拉黑（超级用户）==========

ai_blacklist_cmd = on_command(
    "AI拉黑",
    aliases={"ai拉黑", "lgai拉黑"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@ai_blacklist_cmd.handle()
async def handle_ai_blacklist(matcher: Matcher, event: MessageEvent):
    """超级用户将用户加入 AI 黑名单
    用法：AI拉黑 @用户  或  AI拉黑 QQ号
    """
    # 优先从 @ 提取
    at_users = _extract_at_users(event.message)
    if at_users:
        target_id = at_users[0]
    else:
        text = event.get_plaintext().strip()
        # 去掉命令名，提取 QQ 号
        parts = text.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            target_id = parts[-1]
        else:
            await matcher.finish("请 @要拉黑的用户，或输入 QQ 号。\n例如：AI拉黑 @某人  或  AI拉黑 123456789")
            return

    if is_ai_blacklisted(target_id):
        await matcher.finish(f"用户 {target_id} 已在黑名单中。")

    add_ai_blacklist(target_id)
    await matcher.finish(f"✅ 已将用户 {target_id} 加入 AI 黑名单，该用户无法使用 AI 功能。")


# ========== AI解除（超级用户）==========

ai_unblacklist_cmd = on_command(
    "AI解除",
    aliases={"ai解除", "lgai解除", "AI取消拉黑", "ai取消拉黑"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@ai_unblacklist_cmd.handle()
async def handle_ai_unblacklist(matcher: Matcher, event: MessageEvent):
    """超级用户将用户移出 AI 黑名单
    用法：AI解除 @用户  或  AI解除 QQ号
    """
    at_users = _extract_at_users(event.message)
    if at_users:
        target_id = at_users[0]
    else:
        text = event.get_plaintext().strip()
        parts = text.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            target_id = parts[-1]
        else:
            await matcher.finish("请 @要解除的用户，或输入 QQ 号。\n例如：AI解除 @某人  或  AI解除 123456789")
            return

    if not is_ai_blacklisted(target_id):
        await matcher.finish(f"用户 {target_id} 不在黑名单中。")

    remove_ai_blacklist(target_id)
    await matcher.finish(f"✅ 已将用户 {target_id} 移出 AI 黑名单，恢复 AI 使用权限。")


# ========== AI 配图管理（动态资源目录，无需重启）==========

def _find_image_seg(event: MessageEvent):
    """优先取当前消息图片，其次取被引用(reply)消息里的图片"""
    for seg in event.message:
        if seg.type == "image":
            return seg
    reply = getattr(event, "reply", None)
    if reply is not None:
        rmsg = getattr(reply, "message", None)
        if rmsg is not None:
            for seg in rmsg:
                if seg.type == "image":
                    return seg
    return None


def _guess_img_ext(seg) -> str:
    """从图片段推断扩展名，推断不出默认 .jpg"""
    for raw in (seg.data.get("url"), seg.data.get("file")):
        if raw:
            ext = Path(raw.split("?")[0]).suffix.lower()
            if ext in _AI_IMG_EXTS:
                return ext
    return ".jpg"


def _arg_plain_text(args) -> str:
    """跨 nonebot 版本取 CommandArg 纯文本"""
    if hasattr(args, "extract_plain_text"):
        return args.extract_plain_text().strip()
    if hasattr(args, "get_plaintext"):
        return args.get_plaintext().strip()
    return str(args).strip()


async def _download_image_bytes(bot, event: MessageEvent) -> Optional[bytes]:
    """从消息图片段取二进制：base64:// / http(s) 下载 / bot.get_image 兜底"""
    seg = _find_image_seg(event)
    if seg is None:
        return None
    # 1) base64 内嵌
    for raw in (seg.data.get("url"), seg.data.get("file")):
        if raw and raw.startswith("base64://"):
            try:
                return base64.b64decode(raw.split("base64://", 1)[1])
            except Exception:
                pass
    # 2) http(s) 下载
    dl = seg.data.get("url") or seg.data.get("file")
    if dl and (dl.startswith("http://") or dl.startswith("https://")):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(dl)
                if resp.status_code == 200:
                    return resp.content
        except Exception as e:
            logger.warning(f"[AI配图] 下载图片失败: {e}")
    # 3) bot.get_image 兜底（同机可用）
    f = seg.data.get("file")
    if f and not f.startswith("base64://"):
        try:
            info = await bot.get_image(f)
            fp = info.get("file")
            if fp and Path(fp).is_file():
                return Path(fp).read_bytes()
        except Exception as e:
            logger.warning(f"[AI配图] get_image 失败: {e}")
    return None


def _has_image_seg(event: MessageEvent) -> bool:
    """Rule 检查：消息中是否含图片段（含引用消息）"""
    return _find_image_seg(event) is not None


# --- 添加配图（仅超管） ---
add_img_cmd = on_command(
    "ai图添加",
    aliases={"加配图", "添加配图"},
    priority=5,
    block=True,
    permission=SUPERUSER,
    rule=Rule(_has_image_seg),
)


@add_img_cmd.handle()
async def handle_add_img(matcher: Matcher, bot: Bot, event: MessageEvent) -> None:
    """引用或附带一张图片，发送「ai图添加」存入资源目录"""
    data = await _download_image_bytes(bot, event)
    if not data:
        await matcher.finish("没找到图片，请引用或附带一张图片后再发送「ai图添加」~")
    seg = _find_image_seg(event)
    ext = _guess_img_ext(seg) if seg else ".jpg"
    d = _ensure_ai_img_dir()
    fname = f"img_{int(time.time() * 1000)}_{random.randint(1000, 9999)}{ext}"
    try:
        (d / fname).write_bytes(data)
    except Exception as e:
        logger.error(f"[AI配图] 保存失败: {e}")
        await matcher.finish("配图保存失败，请联系管理员~")
        return
    imgs = _load_ai_images()
    await matcher.finish(f"✅ 已添加配图，当前共 {len(imgs)} 张")


# --- 配图列表 ---
list_img_cmd = on_command(
    "ai图列表",
    aliases={"配图列表", "ai配图列表"},
    priority=5,
    block=True,
)


@list_img_cmd.handle()
async def handle_list_img(matcher: Matcher) -> None:
    imgs = _load_ai_images()
    await matcher.finish(
        f"🖼 当前配图共 {len(imgs)} 张\n"
        f"· 添加：引用/附带图片发「ai图添加」（超管）\n"
        f"· 删除：ai图删除 <序号>（超管）\n"
        f"· 重置：ai图重置（超管，恢复内置基础图）"
    )


# --- 删除配图（仅超管） ---
del_img_cmd = on_command(
    "ai图删除",
    aliases={"删配图", "删除配图"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


def _parse_img_indexes(text: str, n: int) -> list:
    """解析 '1 3 5' 或 '1-3' 形式的序号（1-based），越界忽略"""
    out = []
    for part in text.split():
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                continue
            for i in range(lo, hi + 1):
                if 1 <= i <= n and i not in out:
                    out.append(i)
        else:
            try:
                i = int(part)
            except ValueError:
                continue
            if 1 <= i <= n and i not in out:
                out.append(i)
    return out


@del_img_cmd.handle()
async def handle_del_img(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()) -> None:
    arg = _arg_plain_text(args)
    imgs = _load_ai_images()
    if not imgs:
        await matcher.finish("当前没有任何配图~")
    idxs = _parse_img_indexes(arg, len(imgs)) if arg else []
    if not idxs:
        await matcher.finish(
            f"用法：ai图删除 <序号>（当前共 {len(imgs)} 张，可用「ai图列表」查看）"
        )
    removed = []
    for i in idxs:
        try:
            imgs[i - 1].unlink()
            removed.append(i)
        except Exception as e:
            logger.warning(f"[AI配图] 删除失败 {imgs[i - 1]}: {e}")
    left = _load_ai_images()
    await matcher.finish(f"🗑 已删除 {len(removed)} 张（序号 {removed}），当前剩 {len(left)} 张")


# --- 重置配图（仅超管，恢复内置基础图） ---
reset_img_cmd = on_command(
    "ai图重置",
    aliases={"重置配图"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@reset_img_cmd.handle()
async def handle_reset_img(matcher: Matcher) -> None:
    if _AI_IMG_DIR.is_dir():
        for p in _AI_IMG_DIR.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                except Exception:
                    pass
    _ensure_ai_img_dir()  # 重新复制内置基础图
    imgs = _load_ai_images()
    await matcher.finish(f"♻ 已重置为内置基础图，当前共 {len(imgs)} 张")


# --- AI 帮助（图片返回，与 lg帮助 风格一致） ---
_BG_COLOR = (45, 45, 55)
_TEXT_COLOR = (255, 255, 255)
_TITLE_COLOR = (255, 200, 100)
_SECTION_COLOR = (100, 200, 255)
_DESC_COLOR = (160, 160, 180)
_DIVIDER_COLOR = (80, 80, 95)
_ADMIN_COLOR = (255, 160, 100)


def _ai_help_try_load_font(size: int):
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


def _generate_ai_help_image() -> str:
    """生成 AI帮助 图片，返回 base64 PNG"""
    font_title = _ai_help_try_load_font(30)
    font_section = _ai_help_try_load_font(22)
    font_cmd = _ai_help_try_load_font(20)
    font_desc = _ai_help_try_load_font(14)

    width = 520
    padding = 25
    header_height = 70
    section_gap = 15
    item_height = 50

    sections = [
        ("对话", [
            ("@机器人 + 问题", "群聊需先开启，私聊直接用"),
            ("lg清记忆 / lg清空记忆", "清除当前对话历史"),
            ("打开/关闭 ai自由发言", "超管：开启后无需@，bot 基于最近群聊自动接话（需先开启AI）"),
        ]),
        ("管理（超管）", [
            ("开启AI / 关闭AI", "开启 / 关闭本群 AI 功能"),
            ("AI拉黑 @用户/QQ", "将用户加入 AI 黑名单"),
            ("AI解除 @用户/QQ", "将用户移出 AI 黑名单"),
        ], True),
        ("服务商切换（超管）", [
            ("对话模型 deepseek/grok", "超管：切换本群 AI 对话服务商（默认 DeepSeek）"),
            ("反推模型 deepseek/grok", "超管：切换本群 图反推 服务商（默认 DeepSeek）"),
            ("重置对话模型 / 重置反推模型", "超管：清除本群该项 override，回退全局默认"),
            ("模型设置 / 查看模型", "查看本群+默认配置（群成员可用）"),
        ], True),
        ("配图（资源目录）", [
            ("ai图添加", "超管：引用/附带图片存入资源目录"),
            ("ai图列表", "查看配图数量与用法"),
            ("ai图删除 <序号>", "超管：删除配图（支持 1 3 5 或 1-3）"),
            ("ai图重置", "超管：恢复内置基础图"),
        ], True),
        ("图反推（需开启 AI 功能）", [
            ("图反推", "发图片后发送，反推 NovelAI 提示词（别名 反推提示词/图片反推/提示词反推）"),
            ("用法", "直接附带图片、引用含图片消息，或 @一位群友取头像；需本群已开启 AI 功能；结果可直接复制到 NovelAI"),
            ("优化", "消息加「优化/美化/优化一下/润色」临时触发 DS 润色；超管可用「开启图反推润色/关闭图反推润色」按群默认开启"),
            ("反推AI图", "图反推后直接调 NovelAI 出图（结果同 AI 画图）；需本群开启 AI 画图；支持图片/引用/@群友头像"),
        ], False),
        ("ai余额（仅超管）", [
            ("ai余额", "查询当前 DeepSeek API Key 余额，以图片方式返回（可用/赠金/充值/货币）"),
            ("范围", "仅超级用户可用；群聊需本群已开启 AI 功能，私聊直接可用"),
        ], False),
    ]

    total_height = padding + header_height
    for name, items, *rest in sections:
        total_height += 35 + len(items) * item_height + section_gap
    total_height += padding

    img = Image.new("RGB", (width, total_height), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = padding
    title = "蓝色大肥鱼 · AI 指令"
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    title_w = title_bbox[2] - title_bbox[0]
    draw.text(((width - title_w) // 2, y), title, fill=_TITLE_COLOR, font=font_title)
    y += header_height

    for section_name, items, *rest in sections:
        is_admin = rest[0] if rest else False
        section_color = _ADMIN_COLOR if is_admin else _SECTION_COLOR
        draw.line([(padding, y - 5), (width - padding, y - 5)], fill=_DIVIDER_COLOR, width=1)
        draw.text((padding, y), f"【{section_name}】", fill=section_color, font=font_section)
        y += 35
        for cmd, desc in items:
            draw.text((padding + 10, y), cmd, fill=_TEXT_COLOR, font=font_cmd)
            draw.text((padding + 20, y + 25), desc, fill=_DESC_COLOR, font=font_desc)
            y += item_height
        y += section_gap

    footer = "AI 每次回复会随机附带一张配图"
    fb_bbox = draw.textbbox((0, 0), footer, font=font_desc)
    fb_w = fb_bbox[2] - fb_bbox[0]
    draw.text(((width - fb_w) // 2, total_height - padding - 5), footer, fill=(100, 100, 110), font=font_desc)

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()


ai_help_cmd = on_command(
    "AI帮助",
    aliases={"ai帮助", "AI指令", "ai指令", "lg ai帮助", "lg ai指令"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@ai_help_cmd.handle()
async def handle_ai_help(matcher: Matcher) -> None:
    """列出所有 AI 相关指令及用法（图片返回）"""
    try:
        img_b64 = _generate_ai_help_image()
    except Exception as e:
        logger.error(f"[AI帮助] 图片生成失败: {e}")
        await matcher.finish(
            "🤖 蓝色大肥鱼 · AI 指令\n"
            "· @机器人 + 问题（群聊需开启，私聊直用）\n"
            "· lg清记忆（清除对话历史）\n"
            "· 自由发言（超管）：打开/关闭 ai自由发言 — 开启后无需@，bot 基于最近群聊自动接话（需先开启AI）\n"
            "· 管理（超管）：开启AI / 关闭AI / AI拉黑 / AI解除\n"
            "· 服务商切换（超管）：对话模型 deepseek/grok / 反推模型 deepseek/grok（切换本群对话/图反推服务商，默认 DeepSeek）；重置对话模型 / 重置反推模型（回退默认）；模型设置 / 查看模型（群成员可用，查看本群+默认配置）\n"
            "· 配图：ai图添加 / ai图列表 / ai图删除 <序号> / ai图重置\n"
            "· 图反推：发送图片 / 引用含图消息 / @一位群友后发送「图反推」，反推 NovelAI 提示词（需本群开启 AI 功能）\n"
            "· 图反推优化：消息加「优化/美化/优化一下/润色」临时触发 DS 润色；超管可用「开启图反推润色/关闭图反推润色」按群默认开启\n"
            "· 反推AI图：图反推后直接调 NovelAI 出图（结果同 AI 画图，需本群开启 AI 画图）；支持图片/引用/@群友头像\n"
            "· ai余额：发送「ai余额」查询当前 DeepSeek API Key 余额（图片返回）；仅超管可用，需本群开启 AI 功能或私聊"
        )
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


