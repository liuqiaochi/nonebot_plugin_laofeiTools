"""
重启功能模块

指令：重启bot（仅超级用户可用）
重启后自动发送通知消息。
"""
from __future__ import annotations

import atexit
import inspect
import json
import os
import signal
import socket
import asyncio
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

if TYPE_CHECKING:
    from uvicorn.server import Server

driver = get_driver()

_RESTART_STATUS_FILE = ".restart_info.json"


# ─────────────────────────── 状态文件 ────────────────────────────

def save_restart_state(bot: Bot, event: MessageEvent, message: str = "✅ 重启成功！") -> None:
    """重启前保存会话信息，重启后由 on_bot_connect 读取回复。"""
    try:
        is_group = hasattr(event, "group_id")
        target_id = str(event.group_id) if is_group else str(event.get_user_id())  # type: ignore[attr-defined]
        info = {
            "bot_id": bot.self_id,
            "target_id": target_id,
            "is_group": is_group,
            "message": message,
        }
        with open(_RESTART_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存重启状态失败: {e}")


# ─────────────────────────── 底层重启 ────────────────────────────

def _do_exec_restart() -> None:
    """用 os.execlp 替换当前进程实现重启（无返回）。"""
    from os import execlp
    from sys import argv

    nb = which("nb")
    py = which("python")

    if nb:
        try:
            execlp(nb, nb, "run")
        except Exception as e:
            logger.warning(f"使用 nb 命令重启失败: {e}")

    if py and argv and Path(argv[0]).exists():
        try:
            execlp(py, py, *argv)
        except Exception as e:
            logger.warning(f"使用当前脚本重启失败: {e}")

    for start_file in ("bot.py", "main.py", "app.py", "run.py"):
        if py and Path(start_file).exists():
            try:
                execlp(py, py, start_file)
            except Exception as e:
                logger.warning(f"使用 {start_file} 重启失败: {e}")

    nbr = which("nbr")
    if nbr:
        try:
            execlp(nbr, nbr, "run")
        except Exception as e:
            logger.warning(f"使用 nbr 重启失败: {e}")

    logger.error("所有重启尝试均失败，请手动重启")
    raise RuntimeError("无法重启")


# ─────────────────────────── uvicorn 工具 ────────────────────────

def _uvicorn_get_server() -> "Server":
    from uvicorn.server import Server

    fis = inspect.getouterframes(inspect.currentframe())
    svrs = (fi.frame.f_locals.get("server", None) for fi in fis[::-1])
    server, *_ = (s for s in svrs if isinstance(s, Server))
    return server


def _uvicorn_get_sockets() -> list[socket.socket]:
    fis = inspect.getouterframes(inspect.currentframe())
    skvars = (fi.frame.f_locals.get("sockets", None) for fi in fis[::-1])
    try:
        valid = [
            s
            for s in skvars
            if isinstance(s, list) and all(isinstance(x, socket.socket) for x in s)
        ]
        return valid[0] if valid else []
    except Exception as e:
        logger.exception(e)
        return []


# ─────────────────────────── 停止 ────────────────────────────────

async def do_stop() -> None:
    """停止 NoneBot（支持 fastapi / quart / none 驱动）。"""
    if "fastapi" in driver.type or "quart" in driver.type:
        _uvicorn_get_server().should_exit = True
    if "none" in driver.type:
        driver.exit()  # type: ignore[attr-defined]


# ─────────────────────────── 重启 ────────────────────────────────

async def _shutdown_with_timeout(server: "Server") -> None:
    try:
        await asyncio.wait_for(
            server.shutdown(_uvicorn_get_sockets()), timeout=5.0
        )
        logger.info("正常关闭完成，执行重启")
    except asyncio.TimeoutError:
        logger.warning("关闭超时，执行强制重启")
        atexit.register(_do_exec_restart)
        os.kill(os.getpid(), signal.SIGTERM)


async def do_restart() -> None:
    """重启 NoneBot（支持 fastapi / quart / none 驱动）。"""
    if "none" in driver.type:
        atexit.register(_do_exec_restart)
        driver.exit()  # type: ignore[attr-defined]
        return

    if "fastapi" in driver.type or "quart" in driver.type:
        try:
            server = _uvicorn_get_server()
            server.should_exit = True
            atexit.register(_do_exec_restart)
            await _shutdown_with_timeout(server)
            _do_exec_restart()
        except Exception as e:
            logger.error(f"重启过程中出现错误: {e}")
            await do_stop()


# ─────────────────────────── 重启后通知 ──────────────────────────

@driver.on_bot_connect
async def _notify_after_restart(bot: Bot) -> None:
    """Bot 连接后检查是否有重启状态文件，有则发送重启成功消息。"""
    if not os.path.exists(_RESTART_STATUS_FILE):
        return
    try:
        with open(_RESTART_STATUS_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
        os.remove(_RESTART_STATUS_FILE)

        if bot.self_id != info.get("bot_id"):
            return

        target_id = info.get("target_id")
        is_group = info.get("is_group")
        msg = info.get("message", "✅ 重启成功！")

        if is_group:
            await bot.send_group_msg(group_id=int(target_id), message=msg)
        else:
            await bot.send_private_msg(user_id=int(target_id), message=msg)
    except Exception:
        if os.path.exists(_RESTART_STATUS_FILE):
            os.remove(_RESTART_STATUS_FILE)


# ─────────────────────────── 重启指令 ────────────────────────────

restart_cmd = on_command(
    "重启bot",
    aliases={"重启Bot", "重启BOT"},
    priority=1,
    block=True,
    permission=SUPERUSER,
)


@restart_cmd.handle()
async def handle_restart(matcher: Matcher, bot: Bot, event: MessageEvent) -> None:
    """处理重启指令（仅超级用户）"""
    await matcher.send(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("重启bot中....."),
    ]))
    save_restart_state(bot, event, "✅ 重启成功！")
    await asyncio.sleep(0.5)  # 等待消息发送完毕
    await do_restart()
