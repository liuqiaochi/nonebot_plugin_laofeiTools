"""
soutubot.moe API 封装

逆向分析结果：
- POST /api/search - 上传图片搜索
- GET /api/results/{id} - 获取结果
- X-API-KEY 需要动态生成
"""

import base64
import math
import re
import time
from io import BytesIO
from typing import Optional

import httpx
from PIL import Image

BASE_URL = "https://soutubot.moe"
SEARCH_API = f"{BASE_URL}/api/search"
RESULT_API = f"{BASE_URL}/api/results"

# 默认 User-Agent（用于计算 API Key）
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class SoutubotClient:
    """soutubot.moe 客户端"""

    def __init__(self, user_agent: str = DEFAULT_UA):
        self.user_agent = user_agent
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": BASE_URL,
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def _fetch_global_m(self) -> int:
        """从页面 HTML 获取 window.GLOBAL.m 的值"""
        resp = await self._client.get(BASE_URL)
        resp.raise_for_status()
        
        # 匹配 m: 123456 或 "m":123456
        match = re.search(r'["\']?m["\']?\s*:\s*(\d+)', resp.text)
        if match:
            return int(match.group(1))
        
        # 备用：匹配 window.GLOBAL.m = 123456
        match = re.search(r'window\.GLOBAL\.m\s*=\s*(\d+)', resp.text)
        if match:
            return int(match.group(1))
        
        raise ValueError("无法从页面获取 window.GLOBAL.m")

    def _generate_api_key(self, unix_ts: int, global_m: int) -> str:
        """
        生成 X-API-KEY
        
        算法：base64(unix_ts² + ua_len² + global_m).reverse().replace('=', '')
        """
        ua_len = len(self.user_agent)
        value = int(math.pow(unix_ts, 2)) + int(math.pow(ua_len, 2)) + global_m
        encoded = base64.b64encode(str(value).encode()).decode()
        return encoded[::-1].replace("=", "")

    async def _get_headers(self) -> dict:
        """获取带 X-API-KEY 的请求头（每次都重新获取 global_m）"""
        global_m = await self._fetch_global_m()
        
        unix_ts = int(time.time())
        api_key = self._generate_api_key(unix_ts, global_m)
        
        return {
            "X-API-KEY": api_key,
            "X-Requested-With": "XMLHttpRequest",
        }

    def _compress_image(self, image_data: bytes, max_size: int = 5 * 1024 * 1024) -> bytes:
        """
        压缩图片（soutubot 要求 >5KB 的图片需要压缩）
        
        Args:
            image_data: 原始图片数据
            max_size: 最大字节数（默认5MB）
        
        Returns:
            压缩后的 JPEG 数据
        """
        if len(image_data) <= max_size:
            # 检查是否已经是支持的格式
            try:
                img = Image.open(BytesIO(image_data))
                if img.format in ("JPEG", "PNG", "WEBP"):
                    return image_data
            except Exception:
                pass
        
        # 需要压缩
        img = Image.open(BytesIO(image_data))
        
        # 转换为 RGB（处理 RGBA 等模式）
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        # 限制最大尺寸
        max_width = 2000
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.LANCZOS)
        
        # 压缩输出
        output = BytesIO()
        img.save(output, format="JPEG", quality=90, optimize=True)
        return output.getvalue()

    async def search(
        self,
        image_data: bytes,
        strict_mode: bool = False,
    ) -> dict:
        """
        上传图片搜索
        
        Args:
            image_data: 图片二进制数据
            strict_mode: 是否使用严格模式（factor=1.4，否则1.2）
        
        Returns:
            API 响应数据
        """
        # 压缩图片
        compressed = self._compress_image(image_data)
        
        # 准备请求
        headers = await self._get_headers()
        factor = 1.4 if strict_mode else 1.2
        
        files = {
            "file": ("image.jpg", BytesIO(compressed), "image/jpeg"),
        }
        data = {
            "factor": str(factor),
        }
        
        resp = await self._client.post(
            SEARCH_API,
            headers=headers,
            data=data,
            files=files,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_result(self, result_id: str) -> dict:
        """
        获取历史搜索结果
        
        Args:
            result_id: 搜索结果 ID
        
        Returns:
            API 响应数据
        """
        headers = await self._get_headers()
        resp = await self._client.get(
            f"{RESULT_API}/{result_id}",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        """关闭 HTTP 客户端"""
        await self._client.aclose()


# 单例模式，方便复用
_client_instance: Optional[SoutubotClient] = None


async def get_client() -> SoutubotClient:
    """获取 SoutubotClient 实例"""
    global _client_instance
    if _client_instance is None:
        _client_instance = SoutubotClient()
    return _client_instance
