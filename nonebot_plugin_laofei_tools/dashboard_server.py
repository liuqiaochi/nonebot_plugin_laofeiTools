#!/usr/bin/env python3
"""
龙哥工具箱 · 数据看板本地服务

作用：把插件存储目录 data/laofei_tools/*.json 实时读出来，
通过 HTTP 接口喂给 dashboard.html，取代原先写死在页面里的静态 DASH_DATA。

为什么需要它：浏览器以 file:// 打开 HTML 时会被 CORS 限制，无法直接 fetch 本地 JSON；
本服务同时托管 dashboard.html 与数据接口，同源访问即可。

数据目录定位：
    与插件 config.DATA_DIR 完全一致——即 bot 运行时真正写入的 data/laofei_tools。
    本脚本从自身所在目录逐级向上，找到第一个真实存在 data/laofei_tools 的目录，
    因此无论插件在项目里嵌套多深（如 src/plugins/...），都能命中 bot 的数据目录。
    若数据目录尚未生成，回退到「插件目录父目录/data/laofei_tools」。
    如需强制指定绝对路径，用 --data-dir。

用法：
    python dashboard_server.py                 # 默认 0.0.0.0:8080，自动使用插件数据目录
    python dashboard_server.py --data-dir /home/laofei/Project/esbot/data/laofei_tools
    python dashboard_server.py --port 9000
    python dashboard_server.py --host 127.0.0.1

接口：
    GET /                       -> dashboard.html
    GET /dashboard.html         -> dashboard.html
    GET /api/data               -> 合并后的全部看板数据（JSON）
    GET /api/raw/<filename>     -> 单个原始存储文件（便于排查）
"""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
# 回退约定：尚未生成数据目录时，按「插件目录父目录/data/laofei_tools」兜底。
DEFAULT_DATA_DIR = (PLUGIN_DIR.parent / "data" / "laofei_tools").resolve()


def find_data_dir(start: Path) -> Path | None:
    """从 start 逐级向上，返回第一个真实存在 data/laofei_tools 的目录（即 bot 写入数据的目录）。"""
    start = start.resolve()
    for d in [start, *start.parents]:
        cand = d / "data" / "laofei_tools"
        if cand.is_dir():
            return cand
    return None

# (看板字段名, 存储文件名) —— 直接把文件内容挂到对应字段下，
# 渲染层读取的仍是原内层 key（如 D.module_switches.switches），无需改动前端。
STORAGE_FILES = [
    ("ai_enabled_groups", "ai_enabled_groups.json"),
    ("daily_activity", "daily_activity.json"),
    ("user_points", "user_points.json"),
    ("pet_data", "pet_data.json"),
    ("pet_inventory", "pet_inventory.json"),
    ("pet_pk_records", "pet_pk_records.json"),
    ("lottery_pool", "lottery_pool.json"),
    ("lottery_history", "lottery_history.json"),
    ("lottery_bets", "lottery_bets.json"),
    ("fishing_records", "fishing_records.json"),
    ("daily_fortune", "daily_fortune.json"),
    ("steal_records", "steal_records.json"),
    ("enabled_groups", "enabled_groups.json"),
    ("novelai_enabled_groups", "novelai_enabled_groups.json"),
    ("points_disabled_groups", "points_disabled_groups.json"),
    ("module_switches", "module_switches.json"),
    ("deepseek_model_groups", "deepseek_model_groups.json"),
    ("novelai_model_groups", "novelai_model_groups.json"),
    ("novelai_user_settings", "novelai_user_settings.json"),
]


def resolve_data_dir(data_dir_arg: str | None) -> Path:
    """数据目录：默认找插件所在项目中真实存在的 data/laofei_tools（bot 写入处）；--data-dir 可显式覆盖。"""
    if data_dir_arg:
        return Path(data_dir_arg).expanduser().resolve()
    found = find_data_dir(PLUGIN_DIR)
    if found:
        return found
    return DEFAULT_DATA_DIR


# 由 main() 在启动前计算并写入，Handler 读取此全局
RESOLVED_DATA_DIR: Path = DEFAULT_DATA_DIR


def load_dashboard_data(data_dir: Path) -> dict:
    """逐文件读取插件存储文件并合并为看板所需结构。缺失/损坏文件自动跳过。"""
    merged: dict = {}
    skipped = []
    for key, filename in STORAGE_FILES:
        fp = data_dir / filename
        if not fp.exists():
            skipped.append(filename)
            continue
        try:
            with fp.open("r", encoding="utf-8") as f:
                merged[key] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            skipped.append(f"{filename}(err:{e})")
    merged["_meta"] = {
        "data_dir": str(data_dir),
        "loaded": [k for k, _ in STORAGE_FILES if k in merged],
        "skipped": skipped,
    }
    return merged


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        data_dir = RESOLVED_DATA_DIR

        if path in ("/", "/dashboard.html"):
            fp = PLUGIN_DIR / "dashboard.html"
            if not fp.exists():
                self._send_json({"error": "dashboard.html not found"}, 404)
                return
            body = fp.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return

        if path == "/api/data":
            self._send_json(load_dashboard_data(data_dir))
            return

        if path.startswith("/api/raw/"):
            filename = path[len("/api/raw/"):]
            # 仅允许访问 data 目录内、以 .json 结尾的文件，防目录穿越
            fp = (data_dir / filename).resolve()
            if not str(fp).startswith(str(data_dir.resolve())) or not filename.endswith(".json"):
                self._send_json({"error": "forbidden"}, 403)
                return
            if not fp.exists():
                self._send_json({"error": "not found"}, 404)
                return
            self._send(200, fp.read_bytes(), "application/json; charset=utf-8")
            return

        self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        # 精简日志，避免刷屏
        return


def main():
    global RESOLVED_DATA_DIR
    parser = argparse.ArgumentParser(description="龙哥工具箱数据看板本地服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="监听端口 (默认 8080)")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="插件存储目录绝对路径，如 /home/laofei/Project/esbot/data/laofei_tools。"
        "缺省使用与 config.DATA_DIR 一致的默认目录。",
    )
    args = parser.parse_args()

    RESOLVED_DATA_DIR = resolve_data_dir(args.data_dir)
    print(f"[dashboard] 数据目录: {RESOLVED_DATA_DIR} (存在: {RESOLVED_DATA_DIR.exists()})")
    if not RESOLVED_DATA_DIR.exists():
        print("[dashboard] ⚠️  数据目录不存在！请通过 --data-dir 指定，"
              "或确认启动目录正确。看板将只显示空数据。")
    print(f"[dashboard] 看板地址: http://{args.host}:{args.port}/")
    print("[dashboard] 按 Ctrl+C 停止")
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        if e.errno in (98, 48):  # Address already in use
            print(f"[dashboard] ❌ 端口 {args.port} 已被占用！"
                  f"请先结束占用进程，或换端口启动：")
            print(f"           python dashboard_server.py --port 8081")
            print(f"           （查找占用：lsof -i:{args.port}  或  pkill -f dashboard_server.py）")
        else:
            print(f"[dashboard] ❌ 启动失败：{e}")
        return
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] 已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
