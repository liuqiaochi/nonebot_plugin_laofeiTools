"""
名词解释模块

提供「名词解释 <名词>」指令，解释插件内各系统术语的「用处」与「获取/来源」。
术语涵盖：宠物属性、宠物玩法、钓鱼系统、积分系统、特殊道具、通用功能。

数据基于 pet_data.py / fishing_data.py / points_data.py / pet.md 的实际逻辑整理。
"""

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

# ============================================================
# 名词词典
# 每个条目字段：
#   category : 所属分类（用于无参数时分组展示）
#   aliases  : 可触发本条目解释的别名（小写匹配，含拉丁字母时忽略大小写）
#   usage    : 用处 / 作用（列表）
#   acquire  : 获取 / 来源（列表，可选）
# ============================================================

_GLOSSARY = {
    # ---------------- 宠物属性 ----------------
    "等级": {
        "category": "宠物属性",
        "aliases": ["宠物等级", "lv", "level"],
        "usage": [
            "宠物等级的基底，每升 1 级所需经验 = 当前等级 × 50（Lv1–Lv100）。",
            "每升 1 级 +1 武力（有效武力 = 基础武力 + (等级−1) + 配饰武力）。",
            "决定最大血量 = 200 + (等级−1) × 100，再叠加配饰血量百分比。",
        ],
        "acquire": [
            "通过散步（+20 经验/次）、打工、PK 胜利累积经验自动升级。",
        ],
    },
    "武力": {
        "category": "宠物属性",
        "aliases": ["攻击", "攻击力", "force"],
        "usage": [
            "决定 PK 实际伤害：伤害 = 有效武力 + (有效武力 × 好感等级) // 100。",
            "刀盾狗天赋在 PK 时额外 +5 武力。",
        ],
        "acquire": [
            "宠物种类初始值（刀盾狗 20 最高）。",
            "每升 1 级自动 +1。",
            "佩戴配饰：小刀 +10、青龙偃月刀 +30、宇宙魔方 +30。",
        ],
    },
    "幸运": {
        "category": "宠物属性",
        "aliases": ["luck", "运气"],
        "usage": [
            "散步掉率 = 5% + 好感等级 × 1% + 有效幸运 × 0.2%（耄耋天赋额外 +10%）。",
            "PK 中幸运高的一方先出手；相同则随机。",
        ],
        "acquire": [
            "宠物种类初始值（香企鹅 20 最高）。",
            "佩戴配饰：四叶草 +20、彩虹戒指 +50、宇宙魔方 +30。",
        ],
    },
    "好感": {
        "category": "宠物属性",
        "aliases": ["好感度", "affection"],
        "usage": [
            "由好感点数换算为等级 Lv1–Lv20，是全局增益放大器。",
            "每级好感 +1% PK 伤害。",
            "每级好感 +2% 收益，作用于散步经验、打工积分。",
            "好感等级本身计入散步掉率公式。",
        ],
        "acquire": [
            "每日抚摸 5–20 点（草帽 +10、Doro 天赋 ×1.2、超人披风 ×1.2），每天限 1 次。",
            "喂食：普通食物 +5，宠物口粮 +20，最爱食物额外 +10。",
        ],
    },
    "血量": {
        "category": "宠物属性",
        "aliases": ["hp", "生命", "生命值"],
        "usage": [
            "PK 中按对方武力值扣除，血量 < 0 判负。",
            "上限 = 200 + (等级−1) × 100，再叠加配饰血量百分比（如宇宙魔方 +5%）。",
        ],
        "acquire": [
            "随等级提升自动增加。",
            "佩戴配饰（宇宙魔方）提供血量百分比加成。",
        ],
    },
    "体力": {
        "category": "宠物属性",
        "aliases": ["宠物体力", "stamina", "精力"],
        "usage": [
            "散步 / 打工 / PK 消耗体力，归零后无法继续对应玩法。",
            "每日 0 点自动刷新为满值。",
        ],
        "acquire": [
            "每日 0 点刷新。",
            "喂食恢复：普通食物 +20，宠物口粮 +50，最爱食物额外 +10。",
            "奶龙天赋初始体力额外 +20。",
        ],
    },
    "经验": {
        "category": "宠物属性",
        "aliases": ["exp", "宠物经验"],
        "usage": [
            "累计经验决定宠物等级，等级越高武力与血量越强。",
        ],
        "acquire": [
            "散步 +20 经验/次、打工、PK 胜利均可获得。",
        ],
    },
    "天赋": {
        "category": "宠物属性",
        "aliases": ["被动", "talent"],
        "usage": [
            "每个宠物种类固定的被动能力：",
            "Doro（哦润橘）：好感提升速度 ×1.2。",
            "香企鹅（咕咕嘎嘎）：食物恢复比例 ×1.4。",
            "刀盾狗（我的刀盾）：PK 胜率额外 +5% 武力。",
            "耄耋（哈气）：散步掉落道具概率 +10%。",
            "奶龙（我是奶龙）：初始体力 +20。",
        ],
        "acquire": [
            "领养宠物时按种类固定获得，不可更换。",
        ],
    },
    "配饰": {
        "category": "宠物属性",
        "aliases": ["装备", "accessory", "饰品"],
        "usage": [
            "宠物同时只能佩戴 1 个配饰。",
            "提供武力 / 幸运 / 体力 / 血量百分比等加成。",
        ],
        "acquire": [
            "散步有概率掉落「可掉落」类配饰（小刀、短剑、四叶草、草帽、滑板车）。",
            "不可掉落的特殊配饰（彩虹戒指、青龙偃月刀、超人披风、宇宙魔方）仅在宠物商店购买。",
        ],
    },
    "食物": {
        "category": "宠物属性",
        "aliases": ["宠物食物", "food"],
        "usage": [
            "喂食宠物恢复体力并增加好感。",
            "不同食物恢复量不同：普通食物 +20 体力 +5 好感，宠物口粮 +50 体力 +20 好感。",
        ],
        "acquire": [
            "散步掉落、PK 胜利奖励、宠物商店购买。",
        ],
    },

    # ---------------- 宠物玩法 ----------------
    "散步": {
        "category": "宠物玩法",
        "aliases": ["宠物散步", "溜达", "出门"],
        "usage": [
            "扣除 20 体力，触发随机事件，可能掉落食物 / 配饰。",
            "每次固定 +20 经验。",
            "掉率 = 5% + 好感等级×1% + 幸运×0.2%（耄耋天赋 +10%）。",
        ],
        "acquire": [
            "发送「宠物散步」指令进行。",
        ],
    },
    "抚摸": {
        "category": "宠物玩法",
        "aliases": ["宠物抚摸", "摸宠物", "疼爱宠物"],
        "usage": [
            "增加宠物好感 5–20 点（受天赋/配饰影响）。",
            "每天限 1 次。",
        ],
        "acquire": [
            "发送「宠物抚摸」指令进行。",
        ],
    },
    "喂食": {
        "category": "宠物玩法",
        "aliases": ["宠物喂食", "喂养", "投喂"],
        "usage": [
            "消耗背包中食物，恢复宠物体力并增加好感。",
            "喂最爱食物额外 +10 体力、+10 好感。",
        ],
        "acquire": [
            "发送「宠物喂食 <食物名>」进行。",
        ],
    },
    "宠物PK": {
        "category": "宠物玩法",
        "aliases": ["pk", "宠物pk", "对战", "宠物对战"],
        "usage": [
            "与 @他人宠物 进行回合制对战：幸运高者先出手，按武力值互相扣血，血量先归零者负。",
            "胜利可获得一个随机食物。",
        ],
        "acquire": [
            "发送「宠物pk @某人」进行（消耗体力）。",
        ],
    },
    "打工": {
        "category": "宠物玩法",
        "aliases": ["宠物打工", "宠物工作"],
        "usage": [
            "消耗体力为主人赚取积分，收益受好感等级加成（每级 +2%）。",
        ],
        "acquire": [
            "发送「宠物打工」或「快速打工」进行。",
        ],
    },
    "偷取": {
        "category": "宠物玩法",
        "aliases": ["飞龙探云手", "宠物偷窃", "我偷"],
        "usage": [
            "有概率偷取他人宠物的道具（食物 / 配饰）。",
        ],
        "acquire": [
            "发送「飞龙探云手 @某人」进行。",
        ],
    },
    "领养": {
        "category": "宠物玩法",
        "aliases": ["宠物领养", "领养宠物", "我的宠物"],
        "usage": [
            "首次从 5 种宠物（Doro / 香企鹅 / 刀盾狗 / 耄耋 / 奶龙）中选择 1 只。",
            "「我的宠物」查看已领养宠物的状态。",
        ],
        "acquire": [
            "发送「领养」并选择种类；未领养时发送「我的宠物」也会引导领养。",
        ],
    },
    "背包": {
        "category": "宠物玩法",
        "aliases": ["宠物背包", "仓库"],
        "usage": [
            "查看已拥有的食物与配饰库存。",
        ],
        "acquire": [
            "发送「宠物背包」查看。",
        ],
    },

    # ---------------- 钓鱼系统 ----------------
    "钓鱼": {
        "category": "钓鱼系统",
        "aliases": ["宠物钓鱼", "去钓鱼"],
        "usage": [
            "每次消耗 10 点体力，按稀有度概率获得鱼（普通 75% / 稀有 20% / 超级稀有 5% / 传说极低）。",
            "越稀有的鱼等待时间越长以彰显难度。",
        ],
        "acquire": [
            "发送钓鱼相关指令进行（消耗钓鱼体力）。",
        ],
    },
    "图鉴": {
        "category": "钓鱼系统",
        "aliases": ["钓鱼图鉴", "鱼图鉴", "鱼类图鉴"],
        "usage": [
            "查看已钓上过的鱼，展示已拥有与未拥有。",
            "未获得的鱼显示问号占位图，但保留真实名字，价格遮挡为 ???。",
        ],
        "acquire": [
            "发送「钓鱼图鉴」查看。",
        ],
    },
    "钓鱼箱": {
        "category": "钓鱼系统",
        "aliases": ["鱼箱", "钓鱼背包"],
        "usage": [
            "展示已钓上来的鱼，可全部售出换取积分。",
            "出售不影响图鉴（图鉴记录是否曾钓到）。",
        ],
        "acquire": [
            "发送「钓鱼箱」查看并出售。",
        ],
    },
    "稀有度": {
        "category": "钓鱼系统",
        "aliases": ["鱼稀有度", "鱼类稀有度"],
        "usage": [
            "传说（稀世罕见，如鲸鱼）：概率最低，售价最高。",
            "超级稀有（如矛尾鱼、中华鲟）：约 5%。",
            "稀有（如蓝鳍金枪鱼、海马）：约 20%。",
            "普通（如鲤鱼、鲫鱼）：约 75%，售价 20–50 积分。",
        ],
        "acquire": [
            "由钓鱼概率随机决定，越稀有越难钓到。",
        ],
    },
    "钓鱼体力": {
        "category": "钓鱼系统",
        "aliases": ["钓鱼精力"],
        "usage": [
            "钓鱼每次消耗 10 点，归零后无法继续钓鱼。",
        ],
        "acquire": [
            "每日 0 点刷新。",
        ],
    },

    # ---------------- 积分系统 ----------------
    "积分": {
        "category": "积分系统",
        "aliases": ["点", "积分值", "points"],
        "usage": [
            "插件内通用货币，用于宠物商店购买食物 / 配饰。",
            "查看发送「积分」。",
        ],
        "acquire": [
            "每日签到、宠物打工、他人转账、各类活动获得。",
        ],
    },
    "签到": {
        "category": "积分系统",
        "aliases": ["打卡", "积分签到", "日常签到"],
        "usage": [
            "每日签到获取积分，连续签到通常有额外奖励。",
        ],
        "acquire": [
            "发送「签到」每日领取（每日 1 次）。",
        ],
    },
    "抽签": {
        "category": "积分系统",
        "aliases": ["今日运气", "今日抽签", "今日气运", "运气"],
        "usage": [
            "每日抽签决定当日运势，有极低概率（约 2%）撞大运获得额外奖励。",
        ],
        "acquire": [
            "发送「抽签」每日 1 次。",
        ],
    },
    "新手大礼包": {
        "category": "积分系统",
        "aliases": ["大礼包", "新手礼包"],
        "usage": [
            "新用户专属一次性奖励，领取后获得初始积分等。",
        ],
        "acquire": [
            "发送「新手大礼包」领取（仅 1 次）。",
        ],
    },

    # ---------------- 特殊道具 ----------------
    "宇宙魔方": {
        "category": "特殊道具",
        "aliases": ["魔方", "accessories-cube"],
        "usage": [
            "特殊配饰：武力 +30、幸运 +30、体力 +30、血量 +5%。",
            "售价 9999 积分。",
        ],
        "acquire": [
            "仅宠物商店购买，不可通过掉落获得。",
        ],
    },
    "宠物口粮": {
        "category": "特殊道具",
        "aliases": ["口粮", "food-pet"],
        "usage": [
            "宠物食物：喂食 +50 体力、+20 好感。",
            "售价 300 积分。",
        ],
        "acquire": [
            "仅宠物商店购买，不可通过掉落获得。",
        ],
    },
    "彩虹戒指": {
        "category": "特殊道具",
        "aliases": ["戒指"],
        "usage": [
            "特殊配饰：幸运 +50。",
            "售价 2500 积分。",
        ],
        "acquire": [
            "仅宠物商店购买，不可掉落。",
        ],
    },
    "青龙偃月刀": {
        "category": "特殊道具",
        "aliases": ["青龙刀", "偃月刀"],
        "usage": [
            "特殊配饰：武力 +30。",
            "售价 5000 积分。",
        ],
        "acquire": [
            "仅宠物商店购买，不可掉落。",
        ],
    },
    "超人披风": {
        "category": "特殊道具",
        "aliases": ["披风", "cloak"],
        "usage": [
            "特殊配饰：武力 +10、幸运 +10、体力 +20、好感提升速度 ×1.2。",
            "售价 6666 积分。",
        ],
        "acquire": [
            "仅宠物商店购买，不可掉落。",
        ],
    },

    # ---------------- 通用功能 ----------------
    "AI对话": {
        "category": "通用功能",
        "aliases": ["ai", "人工智能", "deepseek", "机器人问答"],
        "usage": [
            "在群内 @机器人 + 问题，基于 DeepSeek 进行 AI 问答。",
            "需群内先由管理员「开启AI」。",
        ],
        "acquire": [
            "群内 @机器人 提问即可使用（需开启 AI 功能）。",
        ],
    },
    "记忆": {
        "category": "通用功能",
        "aliases": ["ai记忆", "对话记忆", "聊天记忆"],
        "usage": [
            "AI 对话会保留当前会话历史以保持上下文连贯。",
            "发送「lg清记忆」清除当前对话历史。",
        ],
        "acquire": [
            "每次 AI 对话自动累积，无需手动获取。",
        ],
    },
    "搜图": {
        "category": "通用功能",
        "aliases": ["lg搜图", "以图搜图", "图片搜索"],
        "usage": [
            "引用一张图片并发送「lg搜图」，基于 soutubot 以图搜图返回相似图片。",
            "需群内先由管理员「开启lg搜图」。",
        ],
        "acquire": [
            "引用图片 + 发送「lg搜图」即可使用（需开启搜图功能）。",
        ],
    },
}


def _build_index():
    """构建 名称/别名 -> 条目key 的反查索引（小写）"""
    index = {}
    for key, entry in _GLOSSARY.items():
        index[key.lower()] = key
        for alias in entry.get("aliases", []):
            index[alias.lower()] = key
    return index


_LOOKUP = _build_index()


def search_term(text: str):
    """根据输入文本查找名词条目，命中返回 (key, entry)，否则返回 None"""
    if not text:
        return None
    t = text.strip().lower()
    if t in _LOOKUP:
        key = _LOOKUP[t]
        return key, _GLOSSARY[key]
    # 模糊：输入是否包含某别名/名称
    for key, entry in _GLOSSARY.items():
        if t in key.lower() or key.lower() in t:
            return key, entry
        for alias in entry.get("aliases", []):
            if t in alias.lower():
                return key, entry
    return None


def list_by_category() -> str:
    """无参数时，按分类列出所有可用名词"""
    cats = {}
    for key, entry in _GLOSSARY.items():
        cats.setdefault(entry["category"], []).append(key)
    lines = ["【名词解释】可用名词如下，发送「名词解释 <名词>」查看详情："]
    for cat, names in cats.items():
        lines.append(f"▸ {cat}：{' / '.join(names)}")
    return "\n".join(lines)


def format_entry(key: str, entry: dict) -> str:
    """格式化单条名词解释"""
    lines = [f"【名词解释】{key}", f"类别：{entry['category']}"]
    lines.append("用处：")
    for u in entry.get("usage", []):
        lines.append(f"  · {u}")
    if entry.get("acquire"):
        lines.append("获取 / 来源：")
        for a in entry["acquire"]:
            lines.append(f"  · {a}")
    return "\n".join(lines)


# ========== 指令注册 ==========
glossary_cmd = on_command(
    "名词解释",
    aliases={"解释", "名词", "lg解释"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@glossary_cmd.handle()
async def handle_glossary(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理名词解释指令"""
    term = args.extract_plain_text().strip()

    if not term:
        await matcher.finish(list_by_category())
        return

    result = search_term(term)
    if result is None:
        tip = (
            f"未找到「{term}」的解释。\n"
            + list_by_category()
        )
        await matcher.finish(tip)
        return

    key, entry = result
    await matcher.finish(format_entry(key, entry))
