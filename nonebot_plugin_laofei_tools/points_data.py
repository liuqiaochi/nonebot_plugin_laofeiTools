"""
积分系统数据管理

存储用户积分、经验、签到记录、银行数据等
"""

import asyncio
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

# 数据文件路径
DATA_DIR = Path("data/laofei_tools")
USER_DATA_FILE = DATA_DIR / "user_points.json"
BANK_DATA_FILE = DATA_DIR / "bank_data.json"
GUESS_GAME_FILE = DATA_DIR / "guess_games.json"
GAME_LIMIT_FILE = DATA_DIR / "game_limits.json"
PK_SESSION_FILE = DATA_DIR / "pk_sessions.json"

# 每日游戏次数上限
DAILY_GAME_LIMIT = 10


def _ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ========== 等级系统 ==========
# 等级经验需求：每级需要 100 * level 经验
LEVEL_TITLES = {
    1: "初出茅庐",
    2: "小试牛刀",
    3: "渐入佳境",
    4: "崭露头角",
    5: "出类拔萃",
    6: "炉火纯青",
    7: "登峰造极",
    8: "登堂入室",
    9: "一代宗师",
    10: "超凡入圣",
}


def get_level_title(level: int) -> str:
    """获取等级称号"""
    if level >= 10:
        return "超凡入圣"
    return LEVEL_TITLES.get(level, "初出茅庐")


def calculate_level(exp: int) -> int:
    """根据经验计算等级"""
    level = 1
    while True:
        exp_needed = 100 * level
        if exp < exp_needed:
            break
        exp -= exp_needed
        level += 1
    return level


def get_exp_progress(exp: int) -> tuple[int, int, int]:
    """获取经验进度 (当前等级, 当前经验, 升级所需经验)"""
    level = calculate_level(exp)
    # 计算当前等级已消耗的经验
    consumed_exp = sum(100 * i for i in range(1, level))
    current_exp = exp - consumed_exp
    exp_needed = 100 * level
    return level, current_exp, exp_needed


# ========== 用户数据 ==========
class UserData:
    """用户数据"""
    
    def __init__(self):
        self.points: int = 0  # 积分
        self.exp: int = 0  # 经验
        self.bank_points: int = 0  # 银行积分
        self.total_sign_days: int = 0  # 累计签到天数
        self.continuous_sign_days: int = 0  # 连续签到天数
        self.last_sign_date: str = ""  # 上次签到日期 YYYY-MM-DD
        self.bank_interest_hidden: float = 0.0  # 银行隐藏利息累计


# 全局用户数据缓存
_user_data: Dict[str, UserData] = {}  # user_id -> UserData
_bank_last_interest_date: str = ""  # 上次计算银行利息的日期


def _load_user_data() -> Dict[str, dict]:
    """加载用户数据"""
    _ensure_data_dir()
    if USER_DATA_FILE.exists():
        try:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_user_data():
    """保存用户数据"""
    _ensure_data_dir()
    data = {}
    for user_id, user in _user_data.items():
        data[user_id] = {
            "points": user.points,
            "exp": user.exp,
            "bank_points": user.bank_points,
            "total_sign_days": user.total_sign_days,
            "continuous_sign_days": user.continuous_sign_days,
            "last_sign_date": user.last_sign_date,
            "bank_interest_hidden": user.bank_interest_hidden,
        }
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_bank_data() -> dict:
    """加载银行数据"""
    _ensure_data_dir()
    if BANK_DATA_FILE.exists():
        try:
            with open(BANK_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_bank_data(data: dict):
    """保存银行数据"""
    _ensure_data_dir()
    with open(BANK_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user(user_id: str) -> UserData:
    """获取用户数据，不存在则创建"""
    if user_id not in _user_data:
        # 尝试从文件加载
        data = _load_user_data()
        if user_id in data:
            user = UserData()
            user.points = data[user_id].get("points", 0)
            user.exp = data[user_id].get("exp", 0)
            user.bank_points = data[user_id].get("bank_points", 0)
            user.total_sign_days = data[user_id].get("total_sign_days", 0)
            user.continuous_sign_days = data[user_id].get("continuous_sign_days", 0)
            user.last_sign_date = data[user_id].get("last_sign_date", "")
            user.bank_interest_hidden = data[user_id].get("bank_interest_hidden", 0.0)
            _user_data[user_id] = user
        else:
            _user_data[user_id] = UserData()
    return _user_data[user_id]


def reload_user(user_id: str) -> UserData:
    """从文件重新加载用户数据"""
    data = _load_user_data()
    if user_id in data:
        user = UserData()
        user.points = data[user_id].get("points", 0)
        user.exp = data[user_id].get("exp", 0)
        user.bank_points = data[user_id].get("bank_points", 0)
        user.total_sign_days = data[user_id].get("total_sign_days", 0)
        user.continuous_sign_days = data[user_id].get("continuous_sign_days", 0)
        user.last_sign_date = data[user_id].get("last_sign_date", "")
        user.bank_interest_hidden = data[user_id].get("bank_interest_hidden", 0.0)
        _user_data[user_id] = user
    else:
        _user_data[user_id] = UserData()
    return _user_data[user_id]


def save_user(user_id: str):
    """保存单个用户数据"""
    _save_user_data()


# ========== 猜数字游戏 ==========
class GuessGame:
    """猜数字游戏"""
    
    def __init__(self):
        self.target: int = 0  # 目标数字
        self.chances: int = 6  # 剩余机会
        self.bet: int = 0  # 下注积分
        self.is_active: bool = True


_guess_games: Dict[str, GuessGame] = {}  # user_id -> GuessGame


def _load_guess_games() -> Dict[str, dict]:
    """加载猜数字游戏数据"""
    _ensure_data_dir()
    if GUESS_GAME_FILE.exists():
        try:
            with open(GUESS_GAME_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_guess_games():
    """保存猜数字游戏数据"""
    _ensure_data_dir()
    data = {}
    for user_id, game in _guess_games.items():
        if game.is_active:
            data[user_id] = {
                "target": game.target,
                "chances": game.chances,
                "bet": game.bet,
                "is_active": game.is_active,
            }
    with open(GUESS_GAME_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_guess_game(user_id: str) -> Optional[GuessGame]:
    """获取用户的猜数字游戏"""
    if user_id in _guess_games and _guess_games[user_id].is_active:
        return _guess_games[user_id]
    
    # 尝试从文件加载
    data = _load_guess_games()
    if user_id in data and data[user_id].get("is_active"):
        game = GuessGame()
        game.target = data[user_id]["target"]
        game.chances = data[user_id]["chances"]
        game.bet = data[user_id]["bet"]
        game.is_active = data[user_id]["is_active"]
        _guess_games[user_id] = game
        return game
    
    return None


def start_guess_game(user_id: str, bet: int) -> GuessGame:
    """开始猜数字游戏"""
    game = GuessGame()
    game.target = random.randint(1, 100)
    game.chances = 6
    game.bet = bet
    game.is_active = True
    _guess_games[user_id] = game
    _save_guess_games()
    return game


def end_guess_game(user_id: str):
    """结束猜数字游戏"""
    if user_id in _guess_games:
        _guess_games[user_id].is_active = False
    _save_guess_games()


# ========== 签到逻辑 ==========
def do_sign(user_id: str) -> dict:
    """
    执行签到
    
    Returns:
        签到结果字典
    """
    user = get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 检查是否已签到
    if user.last_sign_date == today:
        return {
            "success": False,
            "message": "今日已签到",
        }
    
    # 计算连续签到
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if user.last_sign_date == yesterday:
        user.continuous_sign_days += 1
    else:
        user.continuous_sign_days = 1
    
    user.total_sign_days += 1
    user.last_sign_date = today
    
    # 计算等级
    level = calculate_level(user.exp)
    
    # 计算积分: 15基础 + 1*等级 + 连续签到天数(上限10) + 幸运积分(0-20)
    continuous_bonus = min(user.continuous_sign_days, 10)
    lucky_bonus = random.randint(0, 20)
    points_gained = 15 + level + continuous_bonus + lucky_bonus
    
    # 计算经验: 获得积分 + 随机经验(30-50)
    exp_gained = points_gained + random.randint(30, 50)
    
    user.points += points_gained
    user.exp += exp_gained
    
    # 保存数据
    save_user(user_id)
    
    # 计算新等级
    new_level = calculate_level(user.exp)
    
    return {
        "success": True,
        "points_gained": points_gained,
        "exp_gained": exp_gained,
        "level": new_level,
        "points": user.points,
        "total_sign_days": user.total_sign_days,
        "continuous_sign_days": user.continuous_sign_days,
        "date": today,
    }


def get_user_info(user_id: str) -> dict:
    """获取用户信息"""
    user = get_user(user_id)
    level, current_exp, exp_needed = get_exp_progress(user.exp)
    today = datetime.now().strftime("%Y-%m-%d")
    signed_today = user.last_sign_date == today
    
    return {
        "level": level,
        "title": get_level_title(level),
        "current_exp": current_exp,
        "exp_needed": exp_needed,
        "points": user.points,
        "bank_points": user.bank_points,
        "total_sign_days": user.total_sign_days,
        "continuous_sign_days": user.continuous_sign_days,
        "signed_today": signed_today,
    }


# ========== 银行利息 ==========
def calculate_bank_interest():
    """计算银行利息（每天调用一次）"""
    global _bank_last_interest_date
    today = datetime.now().strftime("%Y-%m-%d")
    
    if _bank_last_interest_date == today:
        return
    
    _bank_last_interest_date = today
    
    # 先保存缓存中的数据到文件
    if _user_data:
        _save_user_data()
    
    # 加载所有用户数据
    data = _load_user_data()
    
    for user_id, user_data in data.items():
        bank_points = user_data.get("bank_points", 0)
        if bank_points > 0:
            # 利息 = 银行积分 * 0.01 (每天1%)
            interest = bank_points * 0.01
            hidden_interest = user_data.get("bank_interest_hidden", 0.0)
            total_hidden = hidden_interest + interest
            
            # 利息大于1时计入积分
            if total_hidden >= 1:
                user_data["bank_points"] = bank_points + int(total_hidden)
                user_data["bank_interest_hidden"] = total_hidden - int(total_hidden)
            else:
                user_data["bank_interest_hidden"] = total_hidden
    
    # 保存数据
    _ensure_data_dir()
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # 更新缓存中的银行积分
    for user_id, user_data in data.items():
        if user_id in _user_data:
            _user_data[user_id].bank_points = user_data.get("bank_points", 0)
            _user_data[user_id].bank_interest_hidden = user_data.get("bank_interest_hidden", 0.0)


# ========== 每日游戏次数限制 ==========
def _load_game_limits() -> dict:
    """加载每日游戏次数数据"""
    _ensure_data_dir()
    if GAME_LIMIT_FILE.exists():
        try:
            with open(GAME_LIMIT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_game_limits(data: dict):
    """保存每日游戏次数数据"""
    _ensure_data_dir()
    with open(GAME_LIMIT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_game_remaining(user_id: str, game_type: str) -> int:
    """
    获取用户今日某游戏的剩余次数。
    game_type: 'lottery' | 'guess'
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = _load_game_limits()
    used = data.get(user_id, {}).get(game_type, {}).get(today, 0)
    return max(0, DAILY_GAME_LIMIT - used)


def consume_game_count(user_id: str, game_type: str) -> int:
    """
    消耗一次游戏次数，返回剩余次数。
    game_type: 'lottery' | 'guess'
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = _load_game_limits()
    user_rec = data.setdefault(user_id, {})
    game_rec = user_rec.setdefault(game_type, {})
    game_rec[today] = game_rec.get(today, 0) + 1
    _save_game_limits(data)
    return max(0, DAILY_GAME_LIMIT - game_rec[today])


# ========== PK 对战会话 ==========
class PKSession:
    """PK 对战会话"""

    def __init__(self):
        self.inviter_id: str = ""       # 发起人 user_id
        self.invitee_id: str = ""       # 被邀请人 user_id
        self.bet: int = 0               # 双方下注积分
        self.group_id: str = ""         # 所在群组
        self.bot_message_id: Optional[int] = None  # 机器人发出的邀请消息 ID（用于 emoji 回应）
        self.cancel_task: Optional[asyncio.Task] = None  # 超时取消任务（内存专用，不持久化）


# 内存中维护的 PK 会话：key = invitee_id，保证每人同时只能被一个人邀请
_pk_sessions: Dict[str, PKSession] = {}


def get_pk_session_by_invitee(invitee_id: str) -> Optional[PKSession]:
    """通过被邀请人 ID 获取待确认 PK 会话"""
    return _pk_sessions.get(invitee_id)


def get_pk_session_by_inviter(inviter_id: str) -> Optional[PKSession]:
    """通过发起人 ID 获取待确认 PK 会话（防止重复发起）"""
    for session in _pk_sessions.values():
        if session.inviter_id == inviter_id:
            return session
    return None


def get_pk_session_by_bot_msg(message_id: int) -> Optional[PKSession]:
    """通过机器人发出的邀请消息 ID 查询 PK 会话（emoji 回应用）"""
    for session in _pk_sessions.values():
        if session.bot_message_id == message_id:
            return session
    return None


def create_pk_session(inviter_id: str, invitee_id: str, bet: int, group_id: str) -> PKSession:
    """创建 PK 会话"""
    session = PKSession()
    session.inviter_id = inviter_id
    session.invitee_id = invitee_id
    session.bet = bet
    session.group_id = group_id
    _pk_sessions[invitee_id] = session
    return session


def remove_pk_session(invitee_id: str):
    """移除 PK 会话"""
    session = _pk_sessions.pop(invitee_id, None)
    if session and session.cancel_task and not session.cancel_task.done():
        session.cancel_task.cancel()
    return session