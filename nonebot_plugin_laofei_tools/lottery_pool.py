"""
幸运奖池系统

每小时整点开奖，从1-29的数字中随机一个
用户可以在开奖前花积分押注数字
开奖时从押注的总积分中，按中奖押注积分的比例分给中奖的人
未中奖的积分自动累计到下一轮的基础奖池中
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from .points_data import get_user, save_user

# 数据文件路径
DATA_DIR = Path("data/laofei_tools")
LOTTERY_POOL_FILE = DATA_DIR / "lottery_pool.json"
LOTTERY_BETS_FILE = DATA_DIR / "lottery_bets.json"
LOTTERY_HISTORY_FILE = DATA_DIR / "lottery_history.json"

# 押注范围
BET_NUMBER_MIN = 1
BET_NUMBER_MAX = 29
BET_POINTS_MIN = 10
BET_POINTS_MAX = 500

# 初始奖池金额
INITIAL_POOL_AMOUNT = 1000


def _ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_lottery_pool() -> dict:
    """加载奖池状态"""
    _ensure_data_dir()
    if LOTTERY_POOL_FILE.exists():
        try:
            with open(LOTTERY_POOL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载奖池数据失败: {e}")
            return _get_default_pool()
    return _get_default_pool()


def _save_lottery_pool(data: dict):
    """保存奖池状态"""
    _ensure_data_dir()
    with open(LOTTERY_POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_default_pool() -> dict:
    """获取默认奖池数据"""
    return {
        "current_round": 1,
        "pool_amount": INITIAL_POOL_AMOUNT,
        "base_amount": 0,
        "is_open": False,
        "last_draw_time": None,
    }


def _load_lottery_bets() -> dict:
    """加载押注记录"""
    _ensure_data_dir()
    if LOTTERY_BETS_FILE.exists():
        try:
            with open(LOTTERY_BETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载押注记录失败: {e}")
            return {}
    return {}


def _save_lottery_bets(data: dict):
    """保存押注记录"""
    _ensure_data_dir()
    with open(LOTTERY_BETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_lottery_history() -> list:
    """加载开奖历史"""
    _ensure_data_dir()
    if LOTTERY_HISTORY_FILE.exists():
        try:
            with open(LOTTERY_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载开奖历史失败: {e}")
            return []
    return []


def _save_lottery_history(data: list):
    """保存开奖历史"""
    _ensure_data_dir()
    with open(LOTTERY_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_current_round() -> int:
    """获取当前轮回数"""
    pool_data = _load_lottery_pool()
    return pool_data["current_round"]


def get_pool_status() -> dict:
    """
    获取当前奖池状态
    
    Returns:
        奖池状态字典
    """
    pool_data = _load_lottery_pool()
    current_round = pool_data["current_round"]
    bets_data = _load_lottery_bets()
    round_bets = bets_data.get(str(current_round), [])
    
    # 计算当前轮回的总押注金额
    total_bets = sum(bet["points"] for bet in round_bets)
    
    # 计算下一开奖时间（下一个整点）
    now = datetime.now()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    
    return {
        "current_round": current_round,
        "pool_amount": pool_data["pool_amount"],
        "total_bets": total_bets,
        "bet_count": len(round_bets),
        "next_draw_time": next_hour.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds_until_draw": int((next_hour - now).total_seconds()),
    }


def place_bet(user_id: str, number: int, points: int) -> dict:
    """
    押注
    
    Args:
        user_id: 用户ID
        number: 押注数字（1-29）
        points: 押注积分（10-500）
    
    Returns:
        结果字典 {"success": bool, "message": str}
    """
    # 验证数字范围
    if number < BET_NUMBER_MIN or number > BET_NUMBER_MAX:
        return {
            "success": False,
            "message": f"押注数字必须在{BET_NUMBER_MIN}-{BET_NUMBER_MAX}之间",
        }
    
    # 验证积分范围
    if points < BET_POINTS_MIN or points > BET_POINTS_MAX:
        return {
            "success": False,
            "message": f"押注积分必须在{BET_POINTS_MIN}-{BET_POINTS_MAX}之间",
        }
    
    # 检查用户积分是否足够
    user = get_user(user_id)
    if user.points < points:
        return {
            "success": False,
            "message": f"积分不足，当前积分：{user.points}",
        }
    
    # 获取当前轮回
    pool_data = _load_lottery_pool()
    current_round = pool_data["current_round"]
    
    # 检查是否已经押注（一轮只能押注一次）
    bets_data = _load_lottery_bets()
    round_bets = bets_data.get(str(current_round), [])
    
    for bet in round_bets:
        if bet["user_id"] == user_id:
            return {
                "success": False,
                "message": f"本轮你已经押注过数字 {bet['number']}（{bet['points']}积分），每轮只能押注一次",
            }
    
    # 扣除用户积分
    user.points -= points
    save_user(user_id)
    
    # 添加押注记录
    bet_record = {
        "user_id": user_id,
        "number": number,
        "points": points,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    round_bets.append(bet_record)
    bets_data[str(current_round)] = round_bets
    _save_lottery_bets(bets_data)
    
    # 更新奖池金额
    pool_data["pool_amount"] += points
    _save_lottery_pool(pool_data)
    
    logger.info(f"[幸运奖池] 用户 {user_id} 押注数字 {number}，积分 {points}")
    
    return {
        "success": True,
        "message": f"押注成功！你押注了数字 {number}，消耗 {points} 积分",
    }


def draw_lottery() -> dict:
    """
    开奖
    
    Returns:
        开奖结果字典
    """
    pool_data = _load_lottery_pool()
    current_round = pool_data["current_round"]
    
    # 防止重复开奖
    if pool_data["is_open"]:
        return {
            "success": False,
            "message": "正在开奖中，请稍后再试",
        }
    
    pool_data["is_open"] = True
    _save_lottery_pool(pool_data)
    
    try:
        # 生成中奖数字
        winning_number = random.randint(BET_NUMBER_MIN, BET_NUMBER_MAX)
        
        # 获取当前轮回的押注记录
        bets_data = _load_lottery_bets()
        round_bets = bets_data.get(str(current_round), [])
        
        # 计算总奖池
        total_pool = pool_data["pool_amount"]
        
        # 找出中奖者
        winners = []
        total_winning_points = 0
        
        for bet in round_bets:
            if bet["number"] == winning_number:
                winners.append(bet)
                total_winning_points += bet["points"]
        
        # 分配奖金
        winner_rewards = []
        
        if winners:
            # 有中奖者：按押注积分比例分配
            for winner in winners:
                # 计算该中奖者应得的奖金
                reward = int(total_pool * (winner["points"] / total_winning_points))
                winner_rewards.append({
                    "user_id": winner["user_id"],
                    "bet_number": winner["number"],
                    "bet_points": winner["points"],
                    "reward": reward,
                })
                
                # 发放奖金
                winner_user = get_user(winner["user_id"])
                winner_user.points += reward
                save_user(winner["user_id"])
                
                logger.info(f"[幸运奖池] 用户 {winner['user_id']} 中奖，奖金 {reward}")
            
            # 计算未中奖的积分（如果有小数点误差）
            total_reward = sum(w["reward"] for w in winner_rewards)
            next_round_base = total_pool - total_reward
        else:
            # 无中奖者：全部滚入下一轮
            next_round_base = total_pool
        
        # 记录开奖历史
        history_data = _load_lottery_history()
        history_record = {
            "round": current_round,
            "winning_number": winning_number,
            "total_pool": total_pool,
            "winners": winner_rewards,
            "next_round_base": next_round_base,
            "draw_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        history_data.append(history_record)
        _save_lottery_history(history_data)
        
        # 准备下一轮
        pool_data["current_round"] = current_round + 1
        pool_data["pool_amount"] = next_round_base + INITIAL_POOL_AMOUNT
        pool_data["base_amount"] = next_round_base
        pool_data["is_open"] = False
        pool_data["last_draw_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_lottery_pool(pool_data)
        
        logger.info(f"[幸运奖池] 第 {current_round} 轮开奖，中奖数字 {winning_number}，奖金已发放")
        
        return {
            "success": True,
            "current_round": current_round,
            "winning_number": winning_number,
            "total_pool": total_pool,
            "winners": winner_rewards,
            "next_round_base": next_round_base,
        }
    
    except Exception as e:
        logger.error(f"[幸运奖池] 开奖失败: {e}")
        pool_data["is_open"] = False
        _save_lottery_pool(pool_data)
        return {
            "success": False,
            "message": f"开奖失败: {str(e)}",
        }


def get_user_bet(user_id: str) -> Optional[dict]:
    """
    获取用户当前轮回的押注记录
    
    Returns:
        押注记录字典，如果未押注则返回None
    """
    current_round = get_current_round()
    bets_data = _load_lottery_bets()
    round_bets = bets_data.get(str(current_round), [])
    
    for bet in round_bets:
        if bet["user_id"] == user_id:
            return bet
    
    return None


def get_lottery_history(limit: int = 10) -> list:
    """
    获取开奖历史
    
    Args:
        limit: 返回记录数量
    
    Returns:
        开奖历史列表
    """
    history_data = _load_lottery_history()
    return history_data[-limit:][::-1]  # 返回最近的记录，按时间倒序


def get_round_bets(current_round: Optional[int] = None) -> list:
    """
    获取指定轮回的押注记录
    
    Args:
        current_round: 轮回数，如果为None则获取当前轮回
    
    Returns:
        押注记录列表
    """
    if current_round is None:
        current_round = get_current_round()
    
    bets_data = _load_lottery_bets()
    return bets_data.get(str(current_round), [])
