"""
反向图片搜索：Google Lens 反查图片出处与作者（含 X 原帖）。

链路（只有一条：mac 侧浏览器 sidecar）：
1. QQ 图下载到内存后，压到长边 1600 / JPEG q88；
2. 把字节 POST 给常驻 mac 的 Node 服务（server.mjs 的 /find?max=N）；
3. mac 那边用真实 Chrome 上传 Google Lens、解析结果、按证据强度排序；
4. 本模块把返回的账号列表转成一条短文本（正式模式）或结构化 items（调试模式）。

为什么不在 Ubuntu 上直连：bot 跑在无 GUI 的虚拟机里，起不了浏览器，也就没法
上传 Lens；mac 上有真实浏览器、链路已跑通。做成独立服务还带来进程隔离
（sidecar 崩了不影响 bot）与零移植成本（不用把 JS 重新写一遍，也不用给插件
装 playwright / chromium）。

为什么不再用 SerpApi 兜底（2026-10-31 移除）：
- 免费额度只有 100 次/月/key，热门图被反复查询时会把当月额度打光；
- 它给的 visual_matches 只到"包含此图的页面"，线索比真实浏览器结果少；
- 实测 sidecar 链路稳定后，兜底几乎不会命中，留着只是多一套 key、多一份
  额度计数文件、多一条容易和主路结论打架的代码路径。

配置（全部在 nonebot 配置 / .env）：
- lens_sidecar_url：必需，例 http://192.168.2.45:8787，留空则本功能整体不可用
- lens_sidecar_token：可选，与 sidecar 的 FISH_TOKEN / config.token 一致
- lens_sidecar_timeout / longge_reverse_timeout / longge_reverse_max_results：可选
"""

import base64
import io
import re
import time
from pathlib import Path

import httpx
from PIL import Image

from nonebot import get_driver, logger


try:
    _LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except AttributeError:  # Pillow < 9.1
    _LANCZOS = Image.LANCZOS


# ========== 结果标题与配图 ==========

_IMAGE_DIR = Path(__file__).resolve().parent.parent / "image"
FINDED_IMAGE = _IMAGE_DIR / "zhaodaole.jpeg"

RESULT_TITLE = "🎯 **lg找姬结果**"


def finded_image_path() -> "Path | None":
    """finded 配图的本地绝对路径（文件存在才返回，否则 None）。

    用于 file:// 方式发图 —— 与 pet_commands 发配件/食物图完全同款：
    `MessageSegment.image(f"file://{path}")` + `matcher.finish`。实测宠物动辄
    几 MB 的 PNG / GIF 都走这条路且稳定，是本部署最可靠的发图路径
    （base64 内联在这里反复 retcode=1200，NapCat 解超长 base64 时卡住）。
    """
    return FINDED_IMAGE if FINDED_IMAGE.exists() else None


def finded_base64() -> str:
    """finded 配图的纯 base64 字符串（不带 base64:// 前缀、不带 CQ 包裹）。

    保留给必须内联的场景（合并转发节点等）；正式单条发图优先用
    finded_image_path() 走 file://，更稳。文件缺失或异常时返回空串。
    """
    if not FINDED_IMAGE.exists():
        return ""
    try:
        return base64.b64encode(FINDED_IMAGE.read_bytes()).decode("ascii")
    except Exception:
        return ""


def finded_cq() -> str:
    """finded 配图的字符串 CQ 码（base64:// 内联），给合并转发节点等用。"""
    b64 = finded_base64()
    if not b64:
        return ""
    return f"[CQ:image,file=base64://{b64}]"


# ========== 配置读取 ==========

# 逻辑名 -> (NoneBot 配置项, 默认值)
_CFG_ATTRS = {
    "timeout": ("longge_reverse_timeout", 25),
    "max_results": ("longge_reverse_max_results", 5),
    # mac 侧浏览器 sidecar（项目根目录 server.mjs）
    "sidecar_url": ("lens_sidecar_url", ""),
    "sidecar_token": ("lens_sidecar_token", ""),
    "sidecar_timeout": ("lens_sidecar_timeout", 90),
    # 调试：结果走合并转发展示多项
    "debug_forward": ("longge_reverse_debug_forward", False),
}


def load_config() -> dict:
    """从 NoneBot 配置读取反搜参数，缺失回退默认值（每次调用实时读取）。"""
    cfg = {k: default for k, (_, default) in _CFG_ATTRS.items()}
    try:
        driver_config = get_driver().config
    except Exception:
        driver_config = None
    if driver_config is None:
        driver_config = _Empty()
    for key, (attr, _) in _CFG_ATTRS.items():
        try:
            val = getattr(driver_config, attr, None)
            if val is not None:
                cfg[key] = val
        except Exception:
            pass
    return cfg


class _Empty:
    """driver 不可用时充当占位，getattr 一律返回 None。"""

    def __getattr__(self, _name):
        return None


def engine_status(cfg: dict) -> list:
    """返回引擎可用性清单（启动时打日志用）。现在只有 sidecar 一条路。"""
    if (cfg.get("sidecar_url") or "").strip():
        return ["sidecar(唯一,本机浏览器,不限次数)"]
    return ["-sidecar(未配置 lens_sidecar_url，lg找姬 不可用)"]


# ========== 上传前压缩 ==========

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _make_client(timeout: int) -> httpx.AsyncClient:
    """构造 httpx 客户端。

    trust_env=False：不读系统代理环境变量（走局域网到 mac，不能被代理劫持）
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(float(timeout), connect=10.0),
        headers={"User-Agent": _UA},
        trust_env=False,
        follow_redirects=True,
    )


def shrink_for_upload(
    image_bytes: bytes,
    *,
    max_side: int = 1600,
    quality: int = 88,
) -> bytes:
    """上传前把图压到长边 1600 / JPEG q88。

    QQ 转来的原图常有 8.8MB，直接过局域网再给浏览器上传既慢又易超时；缩到
    ~400KB 后稳定几秒。视觉检索不需要原分辨率，Lens 吃得住。
    """
    if not image_bytes:
        return image_bytes
    try:
        im = Image.open(io.BytesIO(image_bytes))
        im.load()
        w, h = im.size
        scale = min(1.0, float(max_side) / max(w, h))
        if scale < 1.0:
            im = im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                _LANCZOS,
            )
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        return out if out else image_bytes
    except Exception:  # noqa: BLE001 - 缩图失败就用原图，别因此不搜
        return image_bytes


# ---------------------------------------------------------------------------
# mac 侧浏览器 sidecar（项目根目录 server.mjs）
#
# bot 跑在无 GUI 的 Ubuntu 虚拟机里，起不了浏览器，也就没法直连 Google Lens；
# 而 mac 上有真实浏览器、链路已跑通。所以把「鱼鱼找鸡」当独立服务常驻 mac，
# VM 把图片字节 POST 过去取 JSON 结果。
# ---------------------------------------------------------------------------

# Node 侧 matchPlatform 给出的 platform 取值 -> 展示名
_SIDECAR_PLATFORM = {
    "twitter": "X/Twitter",
    "weibo": "微博",
    "bilibili": "B站",
    "instagram": "Instagram",
    "pixiv": "Pixiv",
}
# pixiv 是画师作品号，不是要找的人的社交主页 —— 用户明确要求不考虑。
_SIDECAR_SKIP = {"pixiv"}

# 盗图/镜像站：Lens 会把 sotwe / twstalker 这类推特镜像页也当成来源，
# 从里面抽出来的号往往是不相干的别人。单条结果模式下必须排掉，否则直接报错人。
_SIDECAR_JUNK_HOST_RE = re.compile(
    r"sotwe|twstalker|fapello|xstalker|xcancel|nitter|urlebird|threadreaderapp",
    re.I,
)


async def search_via_sidecar(image_bytes: bytes, cfg: dict) -> dict:
    """调 mac 侧 sidecar 反查。

    返回 {"ok","accounts","error","status","timedOut","failures"}：
    - ok=True 表示 HTTP 链路通了（账号可能为空，失败原因见 status/failures）；
    - ok=False 表示 sidecar 不可用（连不上/超时/HTTP 错误），调用方给「服务不可用」文案。
    - status 由 sidecar 给出三态：ok（有候选）/ empty（引擎跑完但没东西）/ error（引擎全挂）。
      以前只有「有没有 accounts」，把「确实没搜到」和「引擎抛异常」混成一句「被限流」，
      现在据此分文案。
    任何异常都在此吞掉并转成 ok=False，不向上抛。
    """
    url = (cfg.get("sidecar_url") or "").strip().rstrip("/")
    token = (cfg.get("sidecar_token") or "").strip()
    timeout = int(cfg.get("sidecar_timeout") or 90)
    out = {
        "ok": False,
        "accounts": [],
        "error": "",
        "status": "",
        "timedOut": False,
        "failures": [],
    }
    if not url:
        out["error"] = "未配置 lens_sidecar_url"
        return out

    headers = {"Content-Type": "image/jpeg"}
    if token:
        headers["x-fish-token"] = token
    # 过局域网前先压图：QQ 原图常 8MB+，传过去再给浏览器上传会很慢
    payload = shrink_for_upload(image_bytes)
    # 一次多要几个：正式模式只取第一条，但「lg找姬 调试」要逐条列出来对照。
    max_items = max(1, int(cfg.get("max_results") or 5))

    try:
        async with _make_client(timeout) as sess:
            resp = await sess.post(f"{url}/find?max={max_items}", content=payload, headers=headers)
    except Exception as e:  # noqa: BLE001 - sidecar 不可用不算致命
        out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        return out

    if resp.status_code != 200:
        out["error"] = f"HTTP {resp.status_code} {(resp.text or '')[:80]}"
        return out
    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"非 JSON 响应: {str(e)[:60]}"
        return out
    if not data.get("ok"):
        out["error"] = str(data.get("error") or "sidecar 返回 ok=false")[:120]
        return out

    out["ok"] = True
    out["accounts"] = data.get("accounts") or []
    out["status"] = str(data.get("status") or "").strip().lower()
    out["timedOut"] = bool(data.get("timedOut"))
    out["failures"] = [
        f for f in (data.get("failures") or []) if isinstance(f, dict)
    ]
    return out


def _failure_hint(failures: list) -> str:
    """把 sidecar 的失败原因翻成一句人话（给用户看，不暴露内部细节）。

    只看第一条真正的原因：多条失败往往是同一个根因（引擎只有一个）。
    """
    for item in failures or []:
        reason = str((item or {}).get("reason") or "").strip()
        if not reason or reason.startswith("空结果"):
            continue
        low = reason.lower()
        if "google_captcha" in low or "captcha" in low:
            return "谷歌要求人机验证，歇 5–10 分钟再试（同一张图查太勤会这样）"
        if "google_unreachable" in low or "err_" in low:
            return "谷歌连不上，检查 mac 上代理/网络是否正常"
        if "cdp" in low or "devtools" in low or "浏览器" in reason:
            return "mac 上浏览器起不来，重启一下 sidecar 服务"
        if "total_timeout" in low:
            return "这张图查得太慢，超了总时长上限，稍后重试"
        return reason[:60]
    return ""


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _render_one(item: dict, with_image: bool = True) -> str:
    """单个结果渲染成短文本：标题下紧跟 finded.jpg，再列平台/名字/账号。

    with_image=False 时只出纯文字（标题+平台/名字/账号+URL），用于「带图那条
    没发出去」时的兜底，保证作者结论不丢。
    """
    lines = [RESULT_TITLE]
    if with_image:
        img = finded_cq()
        if img:
            lines.append(img)
    lines += [
        "",
        f"🌐 平台：{item['platform']}",
        f"👤 名字：{item['name']}",
        f"🆔 账号：@{item['handle']}",
    ]
    if item.get("url"):
        lines.append(item["url"])
    return "\n".join(lines)


def _sidecar_items(accounts: list) -> list:
    """sidecar 账号 -> 可展示项，保持 sidecar 自己的证据排序。

    sidecar（mac 侧浏览器）那边已经按「这条号出自第几条搜索结果」「是不是被别人
    @ 的图上主角」「是不是卡片文本猜的」排好序了。这里**不能**再按平台权重重排：
    之前重排成 twitter 优先，只要结果里有个无关推特号，就会把真正的 Instagram /
    微博作者挤到后面 —— 单条结果模式于是报错人。现在只做两件事：
    1) 丢掉镜像站（sotwe / twstalker…）的号；
    2) 把 confidence=weak（卡片文本猜的）沉到最后，其余保持原顺序。
    """
    packed = []
    for index, a in enumerate(accounts or []):
        if not isinstance(a, dict):
            continue
        pid = str(a.get("platform") or "").strip().lower()
        platform = _SIDECAR_PLATFORM.get(pid)
        if not platform or pid in _SIDECAR_SKIP:
            continue
        handle = str(a.get("handle") or "").lstrip("@").strip()
        if not handle:
            continue
        url = str(a.get("url") or "")
        if _SIDECAR_JUNK_HOST_RE.search(url):
            continue
        author = str(a.get("authorName") or "").strip()
        weak = str(a.get("confidence") or "").strip().lower() == "weak"
        packed.append(
            (
                1 if weak else 0,
                index,
                {
                    "platform": "Twitter" if platform == "X/Twitter" else platform,
                    "name": author or handle,
                    "handle": handle,
                    "url": url,
                    "score": None,  # sidecar 不给置信度，只给排序
                    "weak": weak,
                    "evidence": [],
                },
            )
        )
    packed.sort(key=lambda x: (x[0], x[1]))
    return [item for _, _, item in packed]


def render_text(detail: dict, with_image: bool = True) -> str:
    """结构化结果 -> 单条文本：只报最可信的一位，弱证据直接判没找到。

    正式模式（不带「调试」）只出一条结论，所以宁可报「没找到」也不能把
    转发号/无关号当答案报出去。with_image=False 用于带图发送失败时的纯文字兜底。
    """
    items = detail.get("items") or []
    if detail.get("ok") and items:
        for it in items:
            if not it.get("weak"):
                return _render_one(it, with_image=with_image)
    return detail.get("message") or "没找到姬 😢"


# ---------------------------------------------------------------------------
# 编排：压缩 -> sidecar（真实浏览器 + Google Lens）-> 文本结果
# ---------------------------------------------------------------------------

async def reverse_search_detail(image_bytes: bytes) -> dict:
    """一次完整的反向图片搜索，返回结构化结果（不拼文本）。

    返回：
        {
          "ok": bool,          # 是否够格判为「找到了」（弱证据时为 False）
          "source": str,       # 固定 "sidecar"
          "items": [...],      # 已按 sidecar 证据顺序排好；每项 platform/name/
                               # handle/url/score/weak/evidence。弱证据也列出来供调试
          "message": str,      # ok=False 时的文案
          "debug": {...},      # 耗时、sidecar 报错、原始账号等
        }

    只有一条路：mac 侧浏览器 sidecar。不可用时不静默降级成别的搜索源，
    而是明确告诉用户"服务不可用"——免得报出一个可信度不明的结果。
    任何环节失败只记日志，不抛出。
    """
    started = time.time()
    cfg = load_config()
    out = {"ok": False, "source": "", "items": [], "message": "", "debug": {}}

    def done(message: str = "") -> dict:
        out["debug"]["elapsed"] = round(time.time() - started, 2)
        if message:
            out["message"] = message
        return out

    if not (cfg.get("sidecar_url") or "").strip():
        return done(
            "找姬服务未配置 😢 请在 .env 填 LENS_SIDECAR_URL（如 http://192.168.2.45:8787）。"
        )

    res = await search_via_sidecar(image_bytes, cfg)
    out["debug"]["sidecar"] = {
        "ok": res["ok"],
        "error": res["error"],
        "status": res["status"],
        "timedOut": res["timedOut"],
        "failures": res["failures"],
        "raw_accounts": len(res["accounts"] or []),
        "raw": res["accounts"],
    }

    if not res["ok"]:
        logger.warning(f"[找姬] sidecar 不可用：{res['error']}")
        return done(f"找姬服务不可用 😢 {res['error']}")

    items = _sidecar_items(res["accounts"])
    if not items:
        # 三态分开说，别把三种情况混成一句「没找到」：
        #   error = 引擎全挂（captcha / 网络 / 浏览器起不来）→ 说清原因，用户能自己处理；
        #   撞总预算 = 引擎还在跑但到点了 → 让用户稍后重试，而不是以为图没出处；
        #   empty = 引擎正常跑完、确实没东西 → 才是「换张更清晰的图」。
        if res["status"] == "error":
            hint = _failure_hint(res["failures"])
            return done(f"搜索失败 😢 {hint}" if hint else "搜索失败 😢 搜索引擎没有正常返回，稍后再试")
        if res["timedOut"]:
            return done("搜索超时 😢 这张图查得太久被掐断了，稍后重试一下")
        return done("没找到姬 😢 换张更清晰的图，或该图全网暂无转载")

    if items[0]["weak"]:
        # 最像的那条是弱证据（卡片文本猜的号）就不敢乱报，但仍把 items 带回去
        # 供「lg找姬 调试」查看。
        out.update(source="sidecar", items=items)
        return done("没找到姬 😢 最像的账号可信度不足，不敢乱报")

    out.update(ok=True, source="sidecar", items=items)
    return done()


async def reverse_search_all(image_bytes: bytes) -> str:
    """一次完整的反向图片搜索，返回可直接发送的单条文本。"""
    return render_text(await reverse_search_detail(image_bytes))
