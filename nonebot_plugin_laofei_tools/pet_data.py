"""
宠物系统数据管理

存储宠物数据、道具背包、宠物属性计算、业务逻辑等
"""

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

# 数据文件路径
DATA_DIR = Path("data/laofei_tools")
PET_DATA_FILE = DATA_DIR / "pet_data.json"
PET_INVENTORY_FILE = DATA_DIR / "pet_inventory.json"

# ========== 宠物种类定义 ==========
PET_TYPES = {
    "doro": {
        "name": "Doro",
        "luck": 10,
        "force": 10,
        "talent": "欧润橘",
        "talent_desc": "好感提升速度1.2倍",
        "image": "pet-doro.gif",
        "fav_food": "橘子",
    },
    "penguin": {
        "name": "香企鹅",
        "luck": 20,
        "force": 10,
        "talent": "咕咕嘎嘎",
        "talent_desc": "食物恢复比例1.4倍",
        "image": "pet-pengun.gif",
        "fav_food": "汉堡",
    },
    "dog": {
        "name": "刀盾狗",
        "luck": 0,
        "force": 20,
        "talent": "我的刀盾",
        "talent_desc": "PK胜率加5%",
        "image": "pet-dog.gif",
        "fav_food": "骨头",
    },
    "cat": {
        "name": "耄耋",
        "luck": 10,
        "force": 15,
        "talent": "哈气",
        "talent_desc": "散步掉落道具概率加10%",
        "image": "pet-cat.gif",
        "fav_food": "小鱼干",
    },
    "dragon": {
        "name": "奶龙",
        "luck": 15,
        "force": 15,
        "talent": "我是奶龙",
        "talent_desc": "初始体力多20",
        "image": "pet-long.gif",
        "fav_food": "冰淇淋",
    },
}

# ========== 食物定义 ==========
FOODS = {
    "橘子": {"price": 100, "image": "food-orange.png"},
    "汉堡": {"price": 100, "image": "food-hamburger.png"},
    "骨头": {"price": 100, "image": "food-bone.png"},
    "小鱼干": {"price": 100, "image": "food-fish.png"},
    "冰淇淋": {"price": 100, "image": "food-icecream.png"},
}

# ========== 配饰定义 ==========
ACCESSORIES = {
    "小刀": {"force": 10, "luck": 0, "stamina": 0, "price": 500, "special": None, "droppable": True, "image": "accessories-knife.png"},
    "短剑": {"force": 5, "luck": 5, "stamina": 0, "price": 500, "special": None, "droppable": True, "image": "accessories-stiletto.png"},
    "四叶草": {"force": 0, "luck": 20, "stamina": 0, "price": 1000, "special": None, "droppable": True, "image": "accessories-four-leafclover.png"},
    "草帽": {"force": 0, "luck": 0, "stamina": 0, "price": 1000, "special": "pat_bonus_10", "droppable": True, "image": "accessories-hat.png"},
    "滑板车": {"force": 0, "luck": 0, "stamina": 25, "price": 1000, "special": None, "droppable": True, "image": "accessories-scooter.png"},
    "彩虹戒指": {"force": 0, "luck": 50, "stamina": 0, "price": 2500, "special": None, "droppable": False, "image": "accessories-ring.png"},
    "青龙偃月刀": {"force": 30, "luck": 0, "stamina": 0, "price": 5000, "special": None, "droppable": False, "image": "accessories-dragonBlade.png"},
    "超人披风": {"force": 10, "luck": 10, "stamina": 20, "price": 6666, "special": "affection_1.2x", "droppable": False, "image": "accessories-cloak.png"},
}

# ========== 好感度等级经验需求 ==========
AFFECTION_LEVELS = {
    1: 0,
    2: 50,
    3: 120,
    4: 210,
    5: 320,
    6: 450,
    7: 600,
    8: 770,
    9: 960,
    10: 1170,
}

# ========== 宠物等级经验需求 ==========
PET_EXP_PER_LEVEL = 50

# ========== 体力常量 ==========
DEFAULT_STAMINA = 100
DRAGON_STAMINA = 120
MAX_STAMINA = 200


# ========== 宠物数据类 ==========
class PetData:
    """宠物数据"""

    def __init__(self):
        self.pet_type: str = ""           # 宠物种类 key（doro/penguin/dog/cat/dragon）
        self.affection: int = 0           # 好感度点数
        self.exp: int = 0                 # 经验值
        self.stamina: int = 100           # 当前体力
        self.max_stamina: int = 100       # 最大体力（受配饰影响）
        self.base_luck: int = 0           # 基础幸运（种类初始值）
        self.base_force: int = 0          # 基础武力（种类初始值）
        self.accessory: str = ""          # 当前佩戴配饰名称（空字符串表示无）
        self.last_pat_date: str = ""      # 上次抚摸日期 YYYY-MM-DD
        self.last_stamina_date: str = ""  # 上次体力刷新日期


# ========== 背包数据类 ==========
class InventoryData:
    """用户道具背包"""

    def __init__(self):
        self.foods: Dict[str, int] = {}        # 食物名 -> 数量
        self.accessories: Dict[str, int] = {}   # 配饰名 -> 数量


# ========== 全局缓存 ==========
_pet_cache: Dict[str, PetData] = {}  # user_id -> PetData
_inventory_cache: Dict[str, InventoryData] = {}  # user_id -> InventoryData


# ========== 数据持久化函数 ==========

def _ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_pet_data() -> Dict[str, dict]:
    """加载宠物数据 JSON 文件，返回原始字典"""
    _ensure_data_dir()
    if PET_DATA_FILE.exists():
        try:
            with open(PET_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_pet_data():
    """将 _pet_cache 序列化为 JSON 并写入 PET_DATA_FILE"""
    _ensure_data_dir()
    data = {}
    for user_id, pet in _pet_cache.items():
        data[user_id] = {
            "pet_type": pet.pet_type,
            "affection": pet.affection,
            "exp": pet.exp,
            "stamina": pet.stamina,
            "max_stamina": pet.max_stamina,
            "base_luck": pet.base_luck,
            "base_force": pet.base_force,
            "accessory": pet.accessory,
            "last_pat_date": pet.last_pat_date,
            "last_stamina_date": pet.last_stamina_date,
        }
    with open(PET_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_inventory_data() -> Dict[str, dict]:
    """加载背包数据 JSON 文件，返回原始字典"""
    _ensure_data_dir()
    if PET_INVENTORY_FILE.exists():
        try:
            with open(PET_INVENTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_inventory_data():
    """将 _inventory_cache 序列化为 JSON 并写入 PET_INVENTORY_FILE"""
    _ensure_data_dir()
    data = {}
    for user_id, inv in _inventory_cache.items():
        data[user_id] = {
            "foods": inv.foods,
            "accessories": inv.accessories,
        }
    with open(PET_INVENTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def init_pet_data():
    """初始化：从 JSON 文件加载所有宠物数据和背包数据到内存缓存（插件启动时调用）"""
    _ensure_data_dir()

    # 加载宠物数据
    if PET_DATA_FILE.exists():
        try:
            with open(PET_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for user_id, pet_data in data.items():
                pet = PetData()
                pet.pet_type = pet_data.get("pet_type", "")
                pet.affection = pet_data.get("affection", 0)
                pet.exp = pet_data.get("exp", 0)
                pet.stamina = pet_data.get("stamina", 100)
                pet.max_stamina = pet_data.get("max_stamina", 100)
                pet.base_luck = pet_data.get("base_luck", 0)
                pet.base_force = pet_data.get("base_force", 0)
                pet.accessory = pet_data.get("accessory", "")
                pet.last_pat_date = pet_data.get("last_pat_date", "")
                pet.last_stamina_date = pet_data.get("last_stamina_date", "")
                _pet_cache[user_id] = pet
            logger.info(f"[宠物系统] 已加载 {len(_pet_cache)} 位用户宠物数据")
        except Exception as e:
            logger.error(f"[宠物系统] 加载宠物数据失败: {e}")
    else:
        logger.info("[宠物系统] 宠物数据文件不存在，将创建新数据")

    # 加载背包数据
    if PET_INVENTORY_FILE.exists():
        try:
            with open(PET_INVENTORY_FILE, "r", encoding="utf-8") as f:
                inv_data = json.load(f)
            for user_id, inv_raw in inv_data.items():
                inv = InventoryData()
                inv.foods = inv_raw.get("foods", {})
                inv.accessories = inv_raw.get("accessories", {})
                _inventory_cache[user_id] = inv
            logger.info(f"[宠物系统] 已加载 {len(_inventory_cache)} 位用户背包数据")
        except Exception as e:
            logger.error(f"[宠物系统] 加载背包数据失败: {e}")
    else:
        logger.info("[宠物系统] 背包数据文件不存在，将创建新数据")


# ========== 宠物管理函数 ==========

def get_pet(user_id: str) -> Optional[PetData]:
    """获取用户宠物数据，不存在则返回 None"""
    return _pet_cache.get(user_id)


def create_pet(user_id: str, pet_type: str) -> PetData:
    """创建宠物并持久化存储

    根据 PET_TYPES[pet_type] 初始化所有属性。
    奶龙初始体力为 120，其他宠物为 100。
    """
    pet_info = PET_TYPES[pet_type]
    pet = PetData()
    pet.pet_type = pet_type
    pet.base_luck = pet_info["luck"]
    pet.base_force = pet_info["force"]

    # 奶龙初始体力 120，其他 100
    if pet_type == "dragon":
        pet.stamina = DRAGON_STAMINA
        pet.max_stamina = DRAGON_STAMINA
    else:
        pet.stamina = DEFAULT_STAMINA
        pet.max_stamina = DEFAULT_STAMINA

    pet.affection = 0
    pet.exp = 0
    pet.accessory = ""
    pet.last_pat_date = ""
    pet.last_stamina_date = ""

    _pet_cache[user_id] = pet
    _save_pet_data()
    return pet


def save_pet(user_id: str) -> None:
    """保存宠物数据到 JSON 文件"""
    _save_pet_data()


# ========== 背包管理函数 ==========

def get_inventory(user_id: str) -> InventoryData:
    """获取用户背包数据，不存在则自动创建空背包"""
    if user_id not in _inventory_cache:
        _inventory_cache[user_id] = InventoryData()
    return _inventory_cache[user_id]


def add_item(user_id: str, item_type: str, item_name: str, count: int = 1) -> None:
    """向用户背包添加道具

    Args:
        user_id: 用户 ID
        item_type: 道具类型，"food" 或 "accessory"
        item_name: 道具名称
        count: 添加数量，默认 1
    """
    inv = get_inventory(user_id)
    if item_type == "food":
        inv.foods[item_name] = inv.foods.get(item_name, 0) + count
    elif item_type == "accessory":
        inv.accessories[item_name] = inv.accessories.get(item_name, 0) + count
    _save_inventory_data()


def remove_item(user_id: str, item_type: str, item_name: str, count: int = 1) -> bool:
    """从用户背包移除道具

    Args:
        user_id: 用户 ID
        item_type: 道具类型，"food" 或 "accessory"
        item_name: 道具名称
        count: 移除数量，默认 1

    Returns:
        True 移除成功，False 数量不足
    """
    inv = get_inventory(user_id)
    if item_type == "food":
        current = inv.foods.get(item_name, 0)
        if current < count:
            return False
        current -= count
        if current == 0:
            del inv.foods[item_name]
        else:
            inv.foods[item_name] = current
    elif item_type == "accessory":
        current = inv.accessories.get(item_name, 0)
        if current < count:
            return False
        current -= count
        if current == 0:
            del inv.accessories[item_name]
        else:
            inv.accessories[item_name] = current
    else:
        return False
    _save_inventory_data()
    return True


def save_inventory(user_id: str) -> None:
    """保存背包数据到 JSON 文件"""
    _save_inventory_data()


# ========== 配饰管理函数 ==========

def equip_accessory(user_id: str, acc_name: str) -> dict:
    """佩戴配饰

    从背包中取出配饰佩戴到宠物身上。同时只能佩戴 1 个配饰，
    佩戴新配饰时自动卸下旧配饰放回背包。佩戴/卸下时更新 max_stamina。

    Args:
        user_id: 用户 ID
        acc_name: 配饰名称

    Returns:
        dict: {"success": bool, "message": str, "old_accessory": str}
    """
    pet = get_pet(user_id)
    inv = get_inventory(user_id)

    # 检查背包中是否有该配饰
    if inv.accessories.get(acc_name, 0) <= 0:
        return {"success": False, "message": f"背包中没有 {acc_name}"}

    old_accessory = ""

    # 如果已佩戴配饰，先卸下旧配饰放回背包
    if pet.accessory:
        old_accessory = pet.accessory
        add_item(user_id, "accessory", old_accessory)

    # 从背包中移除新配饰
    remove_item(user_id, "accessory", acc_name)

    # 佩戴新配饰
    pet.accessory = acc_name

    # 重新计算 max_stamina：基础体力 + 新配饰体力加成
    base_stamina = DRAGON_STAMINA if pet.pet_type == "dragon" else DEFAULT_STAMINA
    pet.max_stamina = base_stamina + ACCESSORIES[acc_name]["stamina"]

    # 如果当前体力超过新的 max_stamina，则截断
    if pet.stamina > pet.max_stamina:
        pet.stamina = pet.max_stamina

    # 保存数据
    save_pet(user_id)
    save_inventory(user_id)

    return {"success": True, "message": f"成功佩戴 {acc_name}", "old_accessory": old_accessory}


def unequip_accessory(user_id: str) -> dict:
    """卸下配饰

    将当前佩戴的配饰放回背包，重置 max_stamina 为基础值。

    Args:
        user_id: 用户 ID

    Returns:
        dict: {"success": bool, "message": str}
    """
    pet = get_pet(user_id)

    # 检查是否有佩戴配饰
    if not pet.accessory:
        return {"success": False, "message": "当前没有佩戴配饰"}

    old_accessory = pet.accessory

    # 将配饰放回背包
    add_item(user_id, "accessory", old_accessory)

    # 清除配饰
    pet.accessory = ""

    # 重置 max_stamina 为基础值
    base_stamina = DRAGON_STAMINA if pet.pet_type == "dragon" else DEFAULT_STAMINA
    pet.max_stamina = base_stamina

    # 如果当前体力超过新的 max_stamina，则截断
    if pet.stamina > pet.max_stamina:
        pet.stamina = pet.max_stamina

    # 保存数据
    save_pet(user_id)
    save_inventory(user_id)

    return {"success": True, "message": f"已卸下 {old_accessory}"}


# ========== 属性计算函数 ==========

def get_pet_level(exp: int) -> int:
    """根据累计经验计算宠物等级（Lv1 ~ Lv100）

    每级所需经验 = level × PET_EXP_PER_LEVEL（50）
    """
    level = 1
    remaining = exp
    while level < 100:
        needed = level * PET_EXP_PER_LEVEL
        if remaining < needed:
            break
        remaining -= needed
        level += 1
    return level


def get_affection_level(affection: int) -> int:
    """根据好感点数计算好感等级（Lv1 ~ Lv10）

    使用 AFFECTION_LEVELS 阈值表，返回最高满足阈值的等级
    """
    result = 1
    for level, threshold in AFFECTION_LEVELS.items():
        if affection >= threshold:
            result = level
    return result


def get_effective_force(pet: PetData) -> int:
    """计算有效武力 = 基础武力 +（等级 - 1）+ 配饰武力加成"""
    level = get_pet_level(pet.exp)
    level_bonus = level - 1  # 每升1级加1点武力
    acc_bonus = ACCESSORIES.get(pet.accessory, {}).get("force", 0) if pet.accessory else 0
    return pet.base_force + level_bonus + acc_bonus


def get_effective_luck(pet: PetData) -> int:
    """计算有效幸运 = 基础幸运 + 配饰幸运加成（幸运不受等级加成）"""
    acc_bonus = ACCESSORIES.get(pet.accessory, {}).get("luck", 0) if pet.accessory else 0
    return pet.base_luck + acc_bonus


# ========== 体力刷新函数 ==========

def refresh_stamina_if_needed(user_id: str) -> None:
    """每日体力懒加载刷新

    在每次访问宠物数据前调用，检查是否需要刷新体力。
    如果上次刷新日期不是今天，则将体力恢复至 max_stamina。
    """
    pet = get_pet(user_id)
    if pet is None:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    if pet.last_stamina_date != today:
        pet.stamina = pet.max_stamina
        pet.last_stamina_date = today
        save_pet(user_id)


def refresh_all_stamina() -> None:
    """刷新所有宠物体力（供每日定时任务调用）

    遍历所有宠物，将体力恢复至 max_stamina，更新刷新日期，
    最后统一保存一次数据。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    for user_id, pet in _pet_cache.items():
        pet.stamina = pet.max_stamina
        pet.last_stamina_date = today
    _save_pet_data()


# ========== 散步趣味文案 ==========
WALK_NO_DROP_MESSAGES = [
    "{name}走出了六亲不认的步伐，但未发生什么！",
    "{name}在路边发现了一朵小花，开心地闻了闻~",
    "{name}追着蝴蝶跑了一圈，什么都没抓到~",
    "{name}和路边的小鸟对视了一会儿，然后继续散步~",
    "{name}踩到了一个水坑，溅了一身水~",
    "{name}在草地上打了个滚，心情不错~",
]


# ========== 散步逻辑 ==========

def do_walk(user_id: str) -> dict:
    """散步逻辑

    消耗 20 体力，增加 20 经验，按概率掉落道具。

    Args:
        user_id: 用户 ID

    Returns:
        dict: 散步结果，包含 success、stamina/exp 变化、掉落信息等
    """
    # 1. 先刷新体力
    refresh_stamina_if_needed(user_id)

    # 2. 获取宠物
    pet = get_pet(user_id)
    if pet is None:
        return {"success": False, "message": "你还没有领养宠物"}

    # 3. 检查体力
    if pet.stamina < 20:
        return {"success": False, "message": f"宠物体力不足，无法散步（当前体力: {pet.stamina}）"}

    # 记录散步前的状态
    old_stamina = pet.stamina
    old_exp = pet.exp

    # 4. 扣除体力，增加经验
    pet.stamina -= 20
    pet.exp += 20

    # 5. 计算掉落概率
    affection_level = get_affection_level(pet.affection)
    effective_luck = get_effective_luck(pet)
    drop_rate = 5 + affection_level * 1 + effective_luck * 0.2
    if pet.pet_type == "cat":  # 耄耋天赋
        drop_rate += 10

    # 6. 掷骰子判断是否掉落
    dropped = random.random() * 100 < drop_rate

    dropped_item = None
    dropped_item_type = None
    message = None

    if dropped:
        # 7. 构建掉落池：所有食物 + 可掉落配饰
        drop_pool = list(FOODS.keys()) + [
            name for name, info in ACCESSORIES.items() if info["droppable"]
        ]
        dropped_item = random.choice(drop_pool)

        # 判断道具类型
        if dropped_item in FOODS:
            dropped_item_type = "food"
        else:
            dropped_item_type = "accessory"

        # 添加到背包
        add_item(user_id, dropped_item_type, dropped_item)
    else:
        # 8. 未掉落，随机趣味文案
        pet_name = PET_TYPES[pet.pet_type]["name"]
        message = random.choice(WALK_NO_DROP_MESSAGES).format(name=pet_name)

    # 9. 保存宠物数据
    save_pet(user_id)

    # 10. 返回结果
    return {
        "success": True,
        "stamina_before": old_stamina,
        "stamina_after": pet.stamina,
        "exp_before": old_exp,
        "exp_after": pet.exp,
        "dropped": dropped,
        "dropped_item": dropped_item,
        "dropped_item_type": dropped_item_type,
        "message": message,
        "pet_name": PET_TYPES[pet.pet_type]["name"],
    }


# ========== 抚摸逻辑 ==========

def do_pat(user_id: str) -> dict:
    """抚摸宠物逻辑

    每日限一次，增加好感度。Doro 天赋 ×1.2，草帽 +10，超人披风 ×1.2。

    Args:
        user_id: 用户 ID

    Returns:
        dict: 抚摸结果，包含 success、好感度变化等
    """
    # 1. 获取宠物
    pet = get_pet(user_id)
    if pet is None:
        return {"success": False, "message": "你还没有领养宠物"}

    # 2. 检查每日限制
    today = datetime.now().strftime("%Y-%m-%d")
    if pet.last_pat_date == today:
        return {"success": False, "message": "今天已经抚摸过了，明天再来~"}

    # 3. 记录抚摸前好感度
    old_affection = pet.affection

    # 4. 计算好感度增量（按设计文档顺序应用修饰符）
    base_gain = random.randint(5, 20)
    if pet.pet_type == "doro":
        base_gain = int(base_gain * 1.2)
    if pet.accessory == "草帽":
        base_gain += 10
    if pet.accessory == "超人披风":
        base_gain = int(base_gain * 1.2)

    # 5. 增加好感度
    pet.affection += base_gain

    # 6. 更新抚摸日期
    pet.last_pat_date = today

    # 7. 保存数据
    save_pet(user_id)

    # 8. 返回结果
    return {
        "success": True,
        "affection_gain": base_gain,
        "affection_before": old_affection,
        "affection_after": pet.affection,
        "pet_name": PET_TYPES[pet.pet_type]["name"],
    }


# ========== 喂食逻辑 ==========

def do_feed(user_id: str, food_name: str) -> dict:
    """喂食宠物逻辑

    消耗背包中 1 个食物，恢复体力和好感度。
    普通食物 +20 体力 +5 好感；最爱食物额外 +10 体力 +10 好感。
    香企鹅天赋：体力恢复量 ×1.4。体力不超过 max_stamina。

    Args:
        user_id: 用户 ID
        food_name: 食物名称

    Returns:
        dict: 喂食结果，包含 success、体力/好感度变化等
    """
    # 1. 获取宠物
    pet = get_pet(user_id)
    if pet is None:
        return {"success": False, "message": "你还没有领养宠物"}

    # 2. 检查食物是否有效
    if food_name not in FOODS:
        return {"success": False, "message": f"没有这种食物: {food_name}"}

    # 3. 检查背包中是否有该食物
    inv = get_inventory(user_id)
    if food_name not in inv.foods or inv.foods[food_name] <= 0:
        return {"success": False, "message": f"背包中没有 {food_name}"}

    # 4. 从背包中移除 1 个食物
    remove_item(user_id, "food", food_name)

    # 5. 计算体力和好感度增量
    stamina_gain = 20
    affection_gain = 5

    # 检查是否为宠物最爱食物
    pet_info = PET_TYPES[pet.pet_type]
    is_fav = (food_name == pet_info["fav_food"])
    if is_fav:
        stamina_gain += 10   # 最爱额外 +10 体力
        affection_gain += 10  # 最爱额外 +10 好感

    # 香企鹅天赋：体力恢复量 ×1.4
    if pet.pet_type == "penguin":
        stamina_gain = int(stamina_gain * 1.4)

    # 6. 应用体力增量，不超过 max_stamina
    old_stamina = pet.stamina
    pet.stamina = min(pet.stamina + stamina_gain, pet.max_stamina)
    actual_stamina_gain = pet.stamina - old_stamina

    # 7. 应用好感度增量
    old_affection = pet.affection
    pet.affection += affection_gain

    # 8. 保存宠物数据
    save_pet(user_id)

    # 9. 返回结果
    return {
        "success": True,
        "food_name": food_name,
        "is_favorite": is_fav,
        "stamina_gain": actual_stamina_gain,
        "stamina_before": old_stamina,
        "stamina_after": pet.stamina,
        "affection_gain": affection_gain,
        "affection_before": old_affection,
        "affection_after": pet.affection,
        "pet_name": PET_TYPES[pet.pet_type]["name"],
    }


# ========== PK 逻辑 ==========

def do_pk(attacker_id: str, defender_id: str) -> dict:
    """宠物 PK 对战逻辑

    根据双方宠物的武力和幸运计算胜率，随机决定胜负。
    胜利方获得随机 1 个食物奖励。
    双方各扣除 20 点体力。

    Args:
        attacker_id: 发起方用户 ID
        defender_id: 防守方用户 ID

    Returns:
        dict: PK 结果，包含 success、双方属性、胜率、胜负和奖励信息
    """
    # 1. 获取攻击方宠物
    a_pet = get_pet(attacker_id)
    if a_pet is None:
        return {"success": False, "message": "你还没有领养宠物"}

    # 2. 获取防守方宠物
    b_pet = get_pet(defender_id)
    if b_pet is None:
        return {"success": False, "message": "对方还没有领养宠物"}

    # 3. 检查攻击方体力
    if a_pet.stamina < 20:
        return {"success": False, "message": f"你的宠物体力不足（当前体力: {a_pet.stamina}，需要20）"}

    # 4. 检查防守方体力
    if b_pet.stamina < 10:
        return {"success": False, "message": f"对方宠物体力不足，无法PK"}

    # 5. 扣除双方体力
    a_pet.stamina -= 20
    b_pet.stamina -= 10
    save_pet(attacker_id)
    save_pet(defender_id)

    # 5. 计算双方有效属性
    a_force = get_effective_force(a_pet)
    a_luck = get_effective_luck(a_pet)
    b_force = get_effective_force(b_pet)

    # 6. 计算胜率
    win_rate = 50 + (a_force - b_force) * 5 + a_luck / 5 * 5
    if a_pet.pet_type == "dog":  # 刀盾狗天赋
        win_rate += 5
    win_rate = max(5, min(95, win_rate))  # clamp to [5%, 95%]

    # 7. 掷骰子判断胜负
    attacker_won = random.random() * 100 < win_rate

    # 8. 胜利奖励（胜利方获得食物）
    reward_food = random.choice(list(FOODS.keys()))
    if attacker_won:
        add_item(attacker_id, "food", reward_food)
    else:
        add_item(defender_id, "food", reward_food)

    # 9. 返回结果
    return {
        "success": True,
        "attacker_name": PET_TYPES[a_pet.pet_type]["name"],
        "defender_name": PET_TYPES[b_pet.pet_type]["name"],
        "attacker_force": a_force,
        "attacker_luck": a_luck,
        "defender_force": b_force,
        "defender_luck": get_effective_luck(b_pet),
        "win_rate": win_rate,
        "attacker_won": attacker_won,
        "reward_food": reward_food,
        "attacker_stamina": a_pet.stamina,
        "defender_stamina": b_pet.stamina,
    }
