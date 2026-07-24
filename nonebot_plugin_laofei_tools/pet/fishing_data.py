"""
宠物钓鱼系统 - 数据层

鱼类定义、概率、用户钓鱼记录持久化
"""

import json
import random
from pathlib import Path
from typing import Optional

from nonebot.log import logger

from ..common.data_utils import safe_json_save

DATA_DIR = Path("data/laofei_tools")
FISHING_DATA_FILE = DATA_DIR / "fishing_records.json"

# ========== 鱼类定义 ==========
# rarity 枚举: "legendary" | "super_rare" | "rare" | "common"

LEGENDARY_FISH = {
    "whale": {
        "id": "whale",
        "name": "鲸鱼",
        "aliases": [],
        "rarity": "legendary",
        "rarity_cn": "稀世罕见",
        "min_price": 400,
        "max_price": 500,
    },
}

SUPER_RARE_FISH = {
    "coelacanth": {
        "id": "coelacanth",
        "name": "矛尾鱼",
        "aliases": ["腔棘鱼"],
        "rarity": "super_rare",
        "rarity_cn": "超级稀有",
        "min_price": 250,
        "max_price": 300,
    },
    "chinese_sturgeon": {
        "id": "chinese_sturgeon",
        "name": "中华鲟",
        "aliases": [],
        "rarity": "super_rare",
        "rarity_cn": "超级稀有",
        "min_price": 250,
        "max_price": 300,
    },
    "napoleon_wrasse": {
        "id": "napoleon_wrasse",
        "name": "波纹唇鱼",
        "aliases": ["苏眉鱼"],
        "rarity": "super_rare",
        "rarity_cn": "超级稀有",
        "min_price": 250,
        "max_price": 300,
    },
    "arowana_red": {
        "id": "arowana_red",
        "name": "红龙鱼",
        "aliases": [],
        "rarity": "super_rare",
        "rarity_cn": "超级稀有",
        "min_price": 250,
        "max_price": 300,
    },
    "arapaima": {
        "id": "arapaima",
        "name": "巨骨舌鱼",
        "aliases": ["象鱼"],
        "rarity": "super_rare",
        "rarity_cn": "超级稀有",
        "min_price": 250,
        "max_price": 300,
    },
}

RARE_FISH = {
    "bluefin_tuna": {
        "id": "bluefin_tuna",
        "name": "蓝鳍金枪鱼",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "oarfish": {
        "id": "oarfish",
        "name": "皇带鱼",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "anglerfish": {
        "id": "anglerfish",
        "name": "深海𩽾𩾌鱼",
        "aliases": ["𩽾𩾌鱼", "安康鱼"],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "electric_eel": {
        "id": "electric_eel",
        "name": "电鳗",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "archerfish": {
        "id": "archerfish",
        "name": "射水鱼",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "seahorse": {
        "id": "seahorse",
        "name": "海马",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "stonefish": {
        "id": "stonefish",
        "name": "石头鱼",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "mudskipper": {
        "id": "mudskipper",
        "name": "弹涂鱼",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "manta_ray": {
        "id": "manta_ray",
        "name": "蝠鲼",
        "aliases": ["魔鬼鱼"],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
    "sunfish": {
        "id": "sunfish",
        "name": "翻车鱼",
        "aliases": [],
        "rarity": "rare",
        "rarity_cn": "稀有",
        "min_price": 100,
        "max_price": 200,
    },
}

COMMON_FISH = {
    "carp": {
        "id": "carp",
        "name": "鲤鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "crucian_carp": {
        "id": "crucian_carp",
        "name": "鲫鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "grass_carp": {
        "id": "grass_carp",
        "name": "草鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "silver_carp": {
        "id": "silver_carp",
        "name": "鲢鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "bighead_carp": {
        "id": "bighead_carp",
        "name": "鳙鱼",
        "aliases": ["胖头鱼"],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "catfish": {
        "id": "catfish",
        "name": "鲶鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "tilapia": {
        "id": "tilapia",
        "name": "罗非鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "hairtail": {
        "id": "hairtail",
        "name": "带鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "yellow_croaker": {
        "id": "yellow_croaker",
        "name": "黄花鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "sea_bass": {
        "id": "sea_bass",
        "name": "鲈鱼",
        "aliases": ["海鲈"],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "salmon": {
        "id": "salmon",
        "name": "大西洋鲑",
        "aliases": ["三文鱼"],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "cod": {
        "id": "cod",
        "name": "大西洋鳕鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "sardine": {
        "id": "sardine",
        "name": "沙丁鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "saury": {
        "id": "saury",
        "name": "秋刀鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "mackerel": {
        "id": "mackerel",
        "name": "马鲛鱼",
        "aliases": ["鲅鱼"],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "loach": {
        "id": "loach",
        "name": "泥鳅",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "swamp_eel": {
        "id": "swamp_eel",
        "name": "黄鳝",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "snakehead": {
        "id": "snakehead",
        "name": "乌鳢",
        "aliases": ["黑鱼"],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "goldfish": {
        "id": "goldfish",
        "name": "金鱼",
        "aliases": [],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
    "bream": {
        "id": "bream",
        "name": "鳊鱼",
        "aliases": ["武昌鱼"],
        "rarity": "common",
        "rarity_cn": "普通",
        "min_price": 20,
        "max_price": 50,
    },
}

# 合并所有鱼（按稀有度层级排列，供图鉴使用）
ALL_FISH = {}
ALL_FISH.update(LEGENDARY_FISH)
ALL_FISH.update(SUPER_RARE_FISH)
ALL_FISH.update(RARE_FISH)
ALL_FISH.update(COMMON_FISH)

# 稀有度 ID 到中文映射
RARITY_CN_MAP = {
    "legendary": "稀世罕见",
    "super_rare": "超级稀有",
    "rare": "稀有",
    "common": "普通",
}

# 按稀有度分组（保持定义顺序）
FISH_BY_RARITY: dict[str, list[dict]] = {
    "legendary": list(LEGENDARY_FISH.values()),
    "super_rare": list(SUPER_RARE_FISH.values()),
    "rare": list(RARE_FISH.values()),
    "common": list(COMMON_FISH.values()),
}

# 钓鱼概率配置
FISHING_PROB = {
    "legendary": 0.01,   # 1%
    "super_rare": 0.04,  # 4%
    "rare": 0.20,        # 20%
    "common": 0.75,      # 75%
}

# 延迟配置（秒）
FISHING_DELAY = {
    "legendary": (7.0, 9.0),
    "super_rare": (5.0, 6.0),
    "rare": (3.0, 5.0),
    "common": (1.0, 3.0),
}

# 每次钓鱼消耗体力
FISHING_STAMINA_COST = 10

# 内存缓存
_fishing_cache: dict[str, dict] = {}
_cache_loaded = False


def _ensure_loaded() -> None:
    """确保数据已加载"""
    global _cache_loaded, _fishing_cache
    if _cache_loaded:
        return
    try:
        if FISHING_DATA_FILE.exists():
            with open(FISHING_DATA_FILE, "r", encoding="utf-8") as f:
                _fishing_cache = json.load(f)
        else:
            _fishing_cache = {}
    except Exception as e:
        logger.error(f"[钓鱼] 加载钓鱼记录失败: {e}")
        _fishing_cache = {}
    _cache_loaded = True


def _save() -> None:
    """保存钓鱼记录"""
    safe_json_save(FISHING_DATA_FILE, _fishing_cache, cache_is_empty=(not _cache_loaded))


def get_user_record(user_id: str) -> dict:
    """获取用户钓鱼记录，不存在则创建空记录"""
    _ensure_loaded()
    if user_id not in _fishing_cache:
        _fishing_cache[user_id] = {"caught": [], "inventory": {}}
    return _fishing_cache[user_id]


def add_caught_fish(user_id: str, fish_id: str) -> None:
    """记录钓到一条鱼（图鉴 + 背包各 +1）"""
    record = get_user_record(user_id)
    # 图鉴
    if fish_id not in record["caught"]:
        record["caught"].append(fish_id)
    # 背包
    record["inventory"][fish_id] = record["inventory"].get(fish_id, 0) + 1
    _save()


def remove_fish(user_id: str, fish_id: str, count: int = 1) -> bool:
    """从背包移除鱼（不影响图鉴），返回是否成功"""
    record = get_user_record(user_id)
    current = record["inventory"].get(fish_id, 0)
    if current < count:
        return False
    record["inventory"][fish_id] = current - count
    if record["inventory"][fish_id] <= 0:
        del record["inventory"][fish_id]
    _save()
    return True


def get_fish_info(fish_id: str) -> Optional[dict]:
    """根据 ID 获取鱼的定义"""
    return ALL_FISH.get(fish_id)


def get_fish_by_name(name: str) -> Optional[dict]:
    """根据名称或别名查找鱼（用于出售指令）"""
    name_lower = name.strip()
    for fish in ALL_FISH.values():
        if fish["name"] == name_lower:
            return fish
        for alias in fish.get("aliases", []):
            if alias == name_lower:
                return fish
    return None


def get_caught_fish_ids(user_id: str) -> list[str]:
    """获取用户图鉴中已钓到的鱼 ID 列表"""
    record = get_user_record(user_id)
    return record.get("caught", [])


def get_inventory(user_id: str) -> dict[str, int]:
    """获取用户钓鱼箱，返回 {fish_id: count}"""
    record = get_user_record(user_id)
    return record.get("inventory", {})


def roll_fish() -> dict:
    """根据概率随机钓一条鱼，返回鱼的定义 dict"""
    roll = random.random()
    cumulative = 0
    for rarity_key in ["legendary", "super_rare", "rare"]:
        cumulative += FISHING_PROB[rarity_key]
        if roll < cumulative:
            pool = list(FISH_BY_RARITY[rarity_key])
            return random.choice(pool)
    # 剩下的都是 common
    return random.choice(list(COMMON_FISH.values()))


def get_fishing_delay(rarity: str) -> float:
    """根据稀有度返回钓鱼等待时间（秒）"""
    low, high = FISHING_DELAY.get(rarity, (1.0, 3.0))
    return random.uniform(low, high)


def get_sell_price(fish: dict) -> int:
    """根据鱼的售价范围随机返回一个售价"""
    return random.randint(fish["min_price"], fish["max_price"])
