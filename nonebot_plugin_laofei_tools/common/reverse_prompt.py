"""
图反推提示词

调用 LLM 视觉模型（按本群「图反推」配置，默认 DeepSeek），从用户发送/引用的图片，或 @群友 的头像反推可用于 NovelAI 的 AI 绘画提示词。
权限：普通用户可用，但仅「已开启 AI 功能（ai_chat 模块）」的群生效；私聊不受群开关限制。
依赖：至少一个 LLM 服务商的 API Key（DeepSeek / Grok，按群配置 llm_provider_groups.json）。
注意：视觉模型与对话模型解耦，由 llm_providers 按本群「图反推」所选服务商动态解析（DeepSeek 视觉固定 deepseek-flash，Grok 取 grok_vision_model）。
"""

import asyncio
import base64
import json
import re
from io import BytesIO

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from PIL import Image

from ..config import (
    is_ai_group_enabled,
    is_reverse_polish_enabled,
    is_novelai_group_enabled,
    enable_reverse_polish,
    disable_reverse_polish,
    get_reverse_prompt_custom,
    set_reverse_prompt_custom,
    reset_reverse_prompt_custom,
)
from .utils import download_image
from . import llm_providers
from .novelai_draw import draw_prompt_to_image

# 视觉模型不再硬编码为固定 slug：由 llm_providers 按本群「图反推」所选服务商动态解析
# （DeepSeek 视觉固定 deepseek-flash；Grok 视觉取 config.grok_vision_model）。
# 见 llm_providers.get_vision_model / get_vision_extra_body。
VISION_MODEL = "deepseek-flash"  # 保留作默认值常量（无群配置时的回退）

# 图反推全局串行队列：同一时刻仅一个任务访问视觉模型，其余并发请求排队（避免打爆 DeepSeek API）
_reverse_lock = asyncio.Lock()

# 反推提示词（按服务商区分）：
# - Grok：含画师串规则（画风 + 画面元素两方面），Grok 对复杂结构化指令遵循度较好；
# - DeepSeek：精简版（仅画面元素顺序），去掉画师串部分，避免 DS 输出跑偏/被截断。
REVERSE_PROMPT_INSTRUCTION_GROK = (
    "请用逗号分隔的英文标签，模仿 Danbooru 风格，输出用于 AI 绘画（NovelAI V5）的提示词。"
    "一、输出结构（最终输出合并为同一段）：输出画面内元素时分为两个方面。"
    "1）画风方面——按图的画风，给出适配的画师串组合，规则："
    "1.画师串最多6个最少1个，格式为「权重::画师名_::」；"
    "2.有且只有一个主画师，权重保持在0.8–0.9；"
    "3.次要画师权重平均值不得高于0.6，单个最大不超过0.8；"
    "4.所有画师串内空格（逗号后的空格除外）均替换为下划线 _；"
    "5.须辅有配合画师串使用以营造画风的提示词；"
    "6.【重要】优先从风格特征匹配度高的画师中选择而不是默认使用高频热门画师"
    "（如wlop、sakimichan、chen_bin、rella、modare、shirogane_hina、krenz、ask、yoneyama_mai等及其近义变体）。"
    "根据画面具体特征（线稿粗细、上色方式、光影处理、五官比例、头发绘制、质感表现、色彩倾向等），"
    "在日韩美中等国家的多个时期与多个流派的画师中选择，避免重复套用。"
    "2）画面元素方面——按以下顺序组织："
    "画质/画图质量词 → 主体/角色 → 角色外貌特征"
    "（若符合外貌特征的角色存在，用「角色名_(出处)」代替外貌描述，含发色、发型）"
    "→ 角色服装 → 表情 → 角色的动作和姿态 → 环境特征 → 构图/镜头。"
    "二、基础规则："
    "1）避免使用重复 tag（包括语义重复）；"
    "2）用半角逗号分隔，保持英文小写 tag 风格，不要翻译成中文，不要新增原图未体现的内容。"
    "只输出优化后的提示词本身，不要任何解释或引号。"
)

# DeepSeek 版反推提示词：只要画面元素（不含画师串）
REVERSE_PROMPT_INSTRUCTION_DS = (
    "请用逗号分隔的英文标签，模仿 Danbooru 风格，输出用于 AI 绘画（NovelAI）的提示词。"
    "一、输出结构按以下顺序组织："
    "画质/画图质量词 → 主体/角色 → 角色外貌特征"
    "（若符合外貌特征的角色存在，用「角色名_(出处)」代替外貌描述，含发色、发型）"
    "→ 角色服装 → 表情 → 角色的动作和姿态 → 环境特征 → 构图/镜头。"
    "二、基础规则："
    "1）避免使用重复 tag（包括语义重复）；"
    "2）用半角逗号分隔，保持英文小写 tag 风格，不要翻译成中文，不要新增原图未体现的内容。"
    "只输出优化后的提示词本身，不要任何解释或引号。"
)

# Gemini 专用图反推提示词（英文；针对 Gemini 强结构化遵循能力定制，分流见 reverse_instruction）
REVERSE_PROMPT_INSTRUCTION_GEMINI = (
    "You are a professional NovelAI prompt generator. Strictly follow the requirements below and output only one single line of the final prompt. Do not include any explanations, numbering, quotes, or extra text.\n"
    "Task: Based on the image, generate a Danbooru-style prompt for NovelAI V5 (English, lowercase, separated by half-width commas).\n"
    "Output structure (merged into one paragraph):\n"
    "1. Style section: Provide a suitable artist string combination (maximum 6). Format must be \"weight::artist_name_::\".\n"
    "- There must be exactly one main artist with a weight between 0.8 and 0.9.\n"
    "- Secondary artists must have an average weight of \u22640.6, with no single one exceeding 0.8.\n"
    "- Replace all spaces in artist names with underscores.\n"
    "- Strictly avoid high-frequency popular artists (such as wlop, sakimichan, chen_bin, rella, modare, shirogane_hina, krenz, ask, yoneyama_mai, etc.). Prioritize lesser-known artists that better match the specific style of the image.\n"
    "- Include a small number of additional style-enhancing tags alongside the artist strings.\n"
    "2. Content section, in this exact order: quality tags \u2192 subject/character \u2192 appearance features \u2192 clothing \u2192 expression \u2192 pose/action \u2192 environment \u2192 composition/camera.\n"
    "- Do not use duplicate tags (including semantically similar ones).\n"
    "- Do not add anything not present in the original image.\n"
    "- If a known character matches the appearance, replace the appearance description with \"character_name_(source)\" (including hair color and hairstyle).\n"
    "Output only the complete prompt itself."
)

# [兼容] 旧名指向 Grok 版；新代码请用 reverse_instruction(group_id) 按服务商取。
REVERSE_PROMPT_INSTRUCTION = REVERSE_PROMPT_INSTRUCTION_GROK


def reverse_instruction(group_id: str = "") -> str:
    """按本群「图反推」所选服务商返回对应提示词。

    优先返回运行时自定义的提示词（见「修改prompt」指令）；未设置则回退写死的默认：
    Grok / Gemini → 带画师串规则的完整版；DeepSeek（及任何非此二者） → 精简版。
    """
    try:
        provider = llm_providers.resolve_provider(str(group_id or ""), "reverse")
    except Exception:
        provider = "deepseek"
    custom = get_reverse_prompt_custom(provider)
    if custom:
        return custom
    if provider == "grok":
        return REVERSE_PROMPT_INSTRUCTION_GROK
    if provider == "gemini":
        return REVERSE_PROMPT_INSTRUCTION_GEMINI
    return REVERSE_PROMPT_INSTRUCTION_DS


# 图反推提示词运行时管理指令支持的「服务商」关键字（ds = deepseek）
_REVERSE_PROMPT_PROVIDER_ALIAS = {
    "ds": "deepseek",
    "deepseek": "deepseek",
    "grok": "grok",
    "gemini": "gemini",
}

# 用于「查看prompt」回显默认提示词（与 reverse_instruction 的回退逻辑保持一致）
_REVERSE_PROMPT_DEFAULT_BY_PROVIDER = {
    "grok": REVERSE_PROMPT_INSTRUCTION_GROK,
    "gemini": REVERSE_PROMPT_INSTRUCTION_GEMINI,
    "deepseek": REVERSE_PROMPT_INSTRUCTION_DS,
}


def _default_prompt_for(provider: str) -> str:
    return _REVERSE_PROMPT_DEFAULT_BY_PROVIDER.get(provider, REVERSE_PROMPT_INSTRUCTION_DS)

# 触发关键字（任意位置出现即可，支持 @群友 前/后、引用等任意消息结构）
REVERSE_KEYWORDS = ("图反推", "反推提示词", "图片反推", "提示词反推")

# 优化触发关键字：消息文本段含其一即对反推结果再做一次 DS 文本模型润色（先跑通，后续再做群开关）
OPTIMIZE_KEYWORDS = ("优化", "美化", "优化一下", "润色")

def _normalize_cmd_text(text: str) -> str:
    """去空白、去 @提及、去首尾标点，便于指令精确匹配。"""
    t = (text or "").strip()
    t = re.sub(r"@[A-Za-z0-9_\u4e00-\u9fff]+", "", t)
    t = t.strip(" 。，、！？!?.,;:…~·\"'「」『』（）()[]【】")
    return t


def _is_exact_command(text: str, keywords) -> bool:
    """文本经归一化后须**精确等于**某个指令关键字（不含任何润色修饰符），杜绝误触发。"""
    return _normalize_cmd_text(text) in keywords

# DS 文本模型（复用 config.deepseek_model，与 AI 聊天同一模型）的润色指令：保留英文 Danbooru tag，只整理不臆造
OPTIMIZE_INSTRUCTION = (
    "你是一名 NovelAI 绘画提示词优化助手。下面是一段由视觉模型从图片反推出的 Danbooru 风格英文提示词。"
    "请在不改变原意与原 tag 的前提下进行润色整理："
    "1) 去除重复 tag；2) 修正明显拼写/语法错误；"
    "3) 按「画质tag → 主体/角色 → 外观(发色发型) → 服装配饰 → 表情 → 动作姿势 → 背景场景 → 构图镜头」顺序整理；"
    "4) 用半角逗号分隔，保持英文小写 tag 风格，不要翻译成中文，不要新增原图未体现的内容；"
    "    5) 如原提示词已较完善则小幅整理即可。只输出优化后的提示词本身，不要任何解释或引号。"
)


def _sanitize_reverse_prompt(text: str) -> str:
    """清洗视觉模型返回的反推提示词，提升跨服务商可用性。

    不同服务商返回格式差异大：DeepSeek 通常直接给干净 tag；Grok（尤其经代理）
    常把结果包在 Markdown 代码块、带入 <thinking> 思考链、或用引号整段包裹。
    这里统一去除这些噪声，但绝不改动真实 tag 内容，确保下游可直接复制到 NovelAI。
    """
    if not text:
        return ""
    # 1) 去除 Markdown 代码围栏（保留内部内容）
    text = re.sub(r"```(?:[a-zA-Z]*\n)?(.*?)```", r"\1", text, flags=re.DOTALL)
    # 2) 去除思考链标签（Grok/xAI 部分模型会把推理写进 content）
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 2.5) 去除行首引导语（如 "Here is the prompt:" / "以下是提示词：" / "prompt:"）
    text = re.sub(
        r"^\s*(?:here is|here's|below is|the following is|the following|sure[,.!]?|"
        r"certainly[,.!]?|以下是|这是|提示词|prompt)[^:\n]*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # 3) 若整段被一对引号包裹则去外层（仅当内部含逗号，避免误伤单 tag）
    stripped = text.strip()
    if (
        len(stripped) >= 2
        and stripped[0] in "\"'「『"
        and stripped[-1] in "\"'」』"
        and "," in stripped
    ):
        stripped = stripped[1:-1].strip()
    # 4) 折叠多余空行与首尾空白
    return re.sub(r"\n{2,}", "\n", stripped).strip()


# ========== 视觉模型返回内容提取（兼容 Gemini 默认 thinking 把结果落在 thought 段）==========
def _message_has_reasoning(message) -> bool:
    """判断视觉模型响应是否包含思考/推理内容。

    DeepSeek 走 ``reasoning_content``；Gemini 在 OpenAI 兼容端点默认开启 thinking 时，
    结果可能落在 ``thought`` / ``reasoning`` 等非标准字段。此处一并检测，避免把
    “思考模式=False” 误判（Gemini 不返回 reasoning_content，旧判断永远为 False）。
    """
    for attr in ("reasoning_content", "thought", "reasoning"):
        val = getattr(message, attr, None)
        if val and str(val).strip():
            return True
    return False


def _extract_vision_text(message) -> str:
    """从视觉模型返回的 message 中提取反推文本。

    content 优先；若为空（Gemini 等模型默认开启 thinking、把结果写进 thought /
    reasoning 段导致 content 为空）则兜底从 reasoning_content / thought 段取，
    避免“请求成功但 content 空”被直接判失败。
    """
    content = getattr(message, "content", None) or ""
    if content.strip():
        return content
    for attr in ("reasoning_content", "thought", "reasoning"):
        val = getattr(message, attr, None)
        if val and str(val).strip():
            logger.warning(
                f"[图反推] message.content 为空，从 message.{attr} 兜底提取反推文本"
            )
            return str(val)
    return ""


# ========== NovelAI 画师串修正（移植自「画师串转换-web-v1.0.html」，仅 NAI 格式）==========
# 图反推结果常含 NovelAI 画师串（weight::artist:name::），LLM 偶尔会写出名字带空格、
# 漏写词末 _、混用 artist: 标记、把整名用 () 包住等问题。这里在发出结果前统一规整为合法
# NAI 格式：名字内空格->_、词末 :: 前强制补 _、整名被 () 包住则去括号、artist: 大小写归一。
# 仅作用于形如 [权重::]::[artist:]名字:: 的画师 token；其余普通 tag 原样保留，不改语义。
# 与 HTML 同构：提取用 _nai_plain（_ -> 空格、去词末空格），写入用 _nai_out_name（空格 -> _、补词末 _），
# 二者互为逆且幂等——已正确的画师串再跑一遍不会被改坏。

def _nai_plain(s: str) -> str:
    """提取 NovelAI 画师名：所有 _ 转为空格，并删去词末空格（与写入规则互逆）。"""
    return str(s).rstrip(" \t").replace("_", " ")


def _nai_out_name(s: str) -> str:
    """写入 NovelAI 画师名：空格 -> _，并在词末 :: 前强制补一个 _（幂等）。"""
    return str(s).rstrip(" \t").replace(" ", "_") + "_"


def _unwrap_paren(s: str) -> str:
    """整个画师名被一对 () 包住时删去括号（可多层）；内部括号不处理。"""
    s = str(s)
    while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        depth = 0
        wrap = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    wrap = False
                    break
        if not wrap or depth != 0:
            break
        inner = s[1:-1].strip()
        if not inner:
            break
        s = inner
    return s


# 画师 token 判定：可选[权重::] + 可选 artist: + 名字 + 闭合 ::（artist: 大小写不敏感）
_NAI_ARTIST_RE = re.compile(
    r"^\s*(?:(\d+(?:\.\d+)?)\s*)?::((?:artist:)?)(.*?)::\s*$",
    re.IGNORECASE,
)


def _correct_one_artist_token(tok: str) -> str:
    """修正单个画师 token。非 NAI 画师串（无闭合 ::）原样返回。"""
    m = _NAI_ARTIST_RE.match(tok)
    if not m:
        return tok
    weight, has_artist, body = m.group(1), m.group(2), m.group(3)
    # 名字 = 闭合 :: 之前的部分；若内部仍混入多余 ::，取最后一个 :: 之前（与 HTML lastIndexOf 一致）
    name = body
    if "::" in name:
        name = name[: name.rfind("::")]
    name = _unwrap_paren(name.strip())
    if not name:
        return tok
    # 先提取(->空格)再写入(->下划线+词末_)，保证幂等
    name = _nai_out_name(_nai_plain(name))
    # 权重校验：须落在 (0.1, 10)，否则视为未给（省略权重前缀）
    if weight:
        try:
            v = float(weight)
            if not (0.1 < v < 10):
                weight = ""
        except ValueError:
            weight = ""
    marker = "artist:" if has_artist else ""
    if weight:
        return f"{weight}::{marker}{name}::"
    return f"::{marker}{name}::"


def correct_nai_artist_strings(prompt: str):
    """修正提示词中所有 NovelAI 画师串；非画师 token 原样保留。

    返回 (修正后文本, 是否发生过修正)。逗号及逗号后的空格原样保留，仅规整画师 token 本身。
    """
    if not prompt:
        return prompt, False
    changed = False
    out = []
    for p in re.split(r"(,\s*)", prompt):
        if p and p[0] == ",":
            out.append(p)  # 分隔符（含其后空格）原样保留
        else:
            fixed = _correct_one_artist_token(p)
            if fixed != p:
                changed = True
            out.append(fixed)
    return "".join(out), changed


# ========== NovelAI 嵌入元数据提取（先读图内自带 prompt/seed，命中则跳过视觉反推）==========
# 依据：NovelAI 官方 novelai-image-metadata 与社区工具印证，NA 生成图会写入嵌入信息，主要有两类：
#  1) 传统 PNG 文本块：Software="NovelAI"、Description=正向提示词、Comment=JSON(uc/seed/steps/scale/sampler...)
#  2) 隐写 LSB：gzip(JSON) 藏进 RGBA alpha 通道最低位（肉眼不可见；QQ 重编码常丢 alpha，暂不实现）
# 关键：必须在 _normalize_image（convert RGB 会丢弃文本块/alpha）之前、用原始下载字节提取。
# 注意：QQ 等平台转发多把图重编码为 JPEG/压缩 PNG，会剥掉文本块与 alpha，此时取不到，自动回退视觉反推。

def _extract_nai_metadata(raw: bytes) -> dict | None:
    """从图片原始字节尝试提取 NovelAI 嵌入元数据（传统 PNG 文本块）。无则返回 None。"""
    try:
        im = Image.open(BytesIO(raw))
        info = im.info or {}
        software = str(info.get("Software", "")).lower()
        description = str(info.get("Description", "")).strip()
        comment = str(info.get("Comment", "")).strip()
        # 宽松判定：Software 标记为 NovelAI，或 Comment 看起来是 JSON，即视为 NA 图
        if not (software == "novelai" or (comment.startswith("{") and "}" in comment)):
            return None
        meta = {"prompt": description}
        if comment.startswith("{"):
            try:
                cj = json.loads(comment)
                neg = cj.get("uc") or cj.get("negative_prompt") or ""
                if neg:
                    meta["negative"] = neg
                for k in ("seed", "steps", "scale", "sampler", "noise_schedule", "width", "height", "model"):
                    if k in cj and cj[k] is not None:
                        meta[k] = cj[k]
            except Exception:
                pass
        if not meta.get("prompt") and not meta.get("negative"):
            return None
        return meta
    except Exception:
        return None


async def _resolve_metadata_prompt(meta: dict, need_optimize: bool, group_id: str = ""):
    """对元数据提取到的提示词做可选润色，返回 (prompt, optimized_flag)。无 prompt 返回 (None, False)。"""
    prompt = (meta.get("prompt") or "").strip()
    if not prompt:
        return None, False
    optimized = False
    if need_optimize:
        try:
            opt = await _optimize_prompt(prompt, group_id)
            if opt:
                prompt = opt
                optimized = True
        except Exception as e:
            logger.error(f"图反推(元数据)优化失败: {e}")
    return prompt, optimized


def _format_metadata_extras(meta: dict) -> str:
    """把元数据中的负面/种子等整理成给人看的信息行（不进入提示词本体）。"""
    lines = []
    if meta.get("negative"):
        lines.append(f"负面提示词：{meta['negative']}")
    for k in ("seed", "steps", "scale", "sampler", "model"):
        if meta.get(k) is not None:
            lines.append(f"{k}：{meta[k]}")
    return "\n".join(lines)


async def _is_reverse_request(event: MessageEvent) -> bool:
    """自定义规则：消息文本归一化后须**精确等于**反推指令（可带 @群友，不含任何修饰符）。

    不再用子串包含匹配，避免「一整句话里有这词就触发」的误触发。
    仍兼容「@群友 图反推」「图反推@群友」；但「图反推 优化」等带润色修饰符的写法不再命中。
    """
    for seg in event.message:
        if seg.type == "text":
            text = seg.data.get("text", "") or ""
            if _is_exact_command(text, REVERSE_KEYWORDS):
                return True
    return False


reverse_prompt_cmd = on_message(
    rule=Rule(_is_reverse_request),
    priority=5,
    block=True,
)


@reverse_prompt_cmd.handle()
async def handle_reverse_prompt(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """从消息内/引用图片，或 @群友 头像反推 AI 绘画提示词（仅已开启 AI 功能的群可用）"""
    # 0. 群聊开关守卫：仅「已开启 AI 功能（ai_chat 模块）」的群可用；私聊放行
    gid = str(event.group_id) if getattr(event, "group_id", None) is not None else ""
    if gid and not is_ai_group_enabled(gid):
        # 统一开关层约定：模块关闭时静默拦截，不回复
        await matcher.finish()
        return

    # 1. 提取图片（优先消息内图片，其次引用图片，最后 @群友 头像）
    image_url, source_desc = _extract_image(event)
    if not image_url:
        await matcher.finish(
            "请发送一张图片、引用含图片的消息，或 @一位群友后发送「图反推」"
        )
        return

    # 检测是否要求 DS 文本模型优化：消息含优化关键字，或本群已开启图反推润色开关（默认关）
    need_optimize = any(kw in _message_text(event) for kw in OPTIMIZE_KEYWORDS)
    if gid and is_reverse_polish_enabled(gid):
        need_optimize = True

    await matcher.send("正在反推提示词，请稍候...")

    # 进入队列：同一时刻仅一个图反推任务访问视觉模型，其余并发请求排队串行
    async with _reverse_lock:
        # 2. 下载图片
        try:
            image_data = await download_image(bot, image_url)
        except Exception as e:
            logger.error(f"图反推图片下载失败: {e}")
            await matcher.finish("图片下载失败，请重试")
            return

        if not image_data:
            await matcher.finish("图片下载失败，请重试")
            return

        # 2.5 先尝试读取 NovelAI 嵌入元数据（图片残留信息）；命中则直接采用，跳过视觉反推省一次 API 调用
        meta = _extract_nai_metadata(image_data)
        if meta and meta.get("prompt"):
            prompt, optimized = await _resolve_metadata_prompt(meta, need_optimize, gid)
            if prompt:
                msg = Message()
                if getattr(event, "message_id", None):
                    msg.append(MessageSegment.reply(event.message_id))
                head = "以下为从图片元数据提取的提示词（NovelAI 原图残留信息），可直接使用"
                if optimized:
                    head += "（已润色）"
                head += "："
                body = prompt
                extras = _format_metadata_extras(meta)
                if extras:
                    body += "\n\n" + extras
                msg.append(MessageSegment.text(f"{head}\n\n{body}"))
                await matcher.finish(msg)
                return

        # 统一转码为 PNG（DeepSeek 明确支持的格式），并校验字节确为可解码图片
        try:
            image_data = _normalize_image(image_data)
        except Exception as e:
            logger.error(f"图反推图片解码失败: {e}")
            await matcher.finish("图片无法解码，请换一张图重试")
            return

        # 3. 调用视觉模型反推提示词（服务商按本群「图反推」设置解析）
        try:
            client = llm_providers.build_client(gid, "reverse")
            vision_model = llm_providers.get_vision_model(gid, "reverse")
            extra_body = llm_providers.get_vision_extra_body(gid, "reverse")
            # Grok 思考强度（提速）：推理模型默认 high，可调低；DeepSeek / 非推理 Grok 自动为空
            extra_body.update(llm_providers.get_grok_extra_body(gid, "reverse", vision_model))
            data_url = f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            # 注意：OpenAI SDK 的 chat.completions.create 是**同步阻塞**调用，
            # 必须放进线程池（asyncio.to_thread）以免冻结整个事件循环；
            # 视觉请求比纯文本慢，且不设 timeout 会永久挂起，故显式 timeout=60。
            # 关键：DeepSeek 视觉模型默认开启“思考模式”，会把整段 max_tokens 预算花在
            # reasoning_content 上导致 content 为空（finish_reason=length）。llm_providers
            # 已按服务商决定是否附加 extra_body 关闭思考模式（DeepSeek 关闭，Grok 不带）。
            create_kwargs = dict(
                model=vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": reverse_instruction(gid)},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                temperature=0.5,
                max_tokens=800,
                timeout=60,
            )
            if extra_body:
                create_kwargs["extra_body"] = extra_body
            response = await asyncio.to_thread(
                client.chat.completions.create,
                **create_kwargs,
            )
            raw = _extract_vision_text(response.choices[0].message)
            prompt = _sanitize_reverse_prompt(raw)
            if prompt and prompt != raw.strip():
                logger.info(
                    f"[图反推] 视觉模型返回已清洗噪声（如代码块/思考链/引号）："
                    f"raw前40={raw[:40]!r}"
                )
        except ValueError as e:
            # 服务商 API Key 未配置
            await matcher.finish(f"图反推配置错误：{e}")
            return
        except Exception as e:
            logger.error(f"图反推调用视觉模型失败: {e}")
            # 直接回显异常类型与信息便于排查，而非一律“请稍后再试”
            await matcher.finish(f"反推失败：{type(e).__name__} - {e}")
            return

        if not prompt:
            # 诊断：记录并向用户回显 finish_reason / 思考模式状态，便于排查
            try:
                choice = response.choices[0]
                fr = getattr(choice, "finish_reason", None)
                has_reason = _message_has_reasoning(choice.message)
                logger.warning(
                    f"图反推返回空内容: model={vision_model}, finish_reason={fr}, "
                    f"有思考内容={has_reason}（多为视觉模型 thinking 把结果写在 thought 段或安全过滤清空 content）"
                )
                detail = f"（finish_reason={fr}，思考模式={has_reason}）"
            except Exception:
                detail = ""
            await matcher.finish(f"未能反推出提示词，请换一张图重试{detail}")
            return

        # 3.5 可选：DS 文本模型润色（消息含优化关键字时触发，仍在串行锁内避免打爆 API）
        optimized_flag = False
        if need_optimize:
            try:
                optimized = await _optimize_prompt(prompt, gid)
                if optimized:
                    prompt = optimized
                    optimized_flag = True
                else:
                    logger.warning("图反推优化返回空，回退原始反推词")
            except Exception as e:
                logger.error(f"图反推优化失败: {e}")
                # 优化失败不阻断主流程，回退原始反推词并提示
                await matcher.send(f"提示词优化失败（{type(e).__name__}），返回原始反推词")

        # 3.6 画师串修正：结果含 NAI 画师串（weight::artist:name::）时统一规整为合法格式
        prompt, artist_fixed = correct_nai_artist_strings(prompt)

        # 4. 返回结果（直接返回文本便于复制）
        msg = Message()
        if getattr(event, "message_id", None):
            msg.append(MessageSegment.reply(event.message_id))
        parts = ["以下为反推提示词"]
        if source_desc:
            parts.append(f"（来自{source_desc}）")
        if optimized_flag:
            parts.append("（已用 DeepSeek 润色）")
        if artist_fixed:
            parts.append("（画师串已自动修正）")
        parts.append("，可直接用于 NovelAI：")
        prefix = "".join(parts)
        msg.append(MessageSegment.text(f"{prefix}\n\n{prompt}"))
        await matcher.finish(msg)


def _extract_image(event: MessageEvent):
    """提取反推目标图片的 url，以及来源说明（用于回复文案）。

    优先级：消息内图片 > 引用消息中的图片 > @群友 头像。

    Returns:
        (image_url, source_desc)：无图片时两者均为空字符串。
    """
    # 1) 消息内图片
    url = _first_image_url(event.message)
    if url:
        return url, ""

    # 2) 引用（回复）消息中的图片
    reply = getattr(event, "reply", None)
    if reply and getattr(reply, "message", None):
        url = _first_image_url(reply.message)
        if url:
            return url, "引用的图片"

    # 3) @群友 头像
    at_qq = _extract_at_qq(event)
    if at_qq:
        return _avatar_url(at_qq), f"QQ {at_qq} 的头像"

    return "", ""


def _first_image_url(segments) -> str:
    """从消息段列表中取第一张图片的 url/file 字段"""
    for seg in segments:
        if seg.type == "image":
            url = seg.data.get("url") or seg.data.get("file")
            if url:
                return url
    return ""


def _extract_at_qq(event: MessageEvent) -> str:
    """从消息中提取第一个 @群友 的 QQ 号（忽略 @all）"""
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq and str(qq) != "all":
                return str(qq)
    return ""


def _avatar_url(qq: str) -> str:
    """根据 QQ 号构造群友头像 URL（spec=640 取最大可用尺寸）"""
    return f"https://q.qlogo.cn/headimg_dl?dst_uin={qq}&spec=640&img_type=jpg"


def _message_text(event: MessageEvent) -> str:
    """拼接消息内所有文本段的纯文本（用于关键字检测，忽略图片/At 等）"""
    return "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )


async def _optimize_prompt(raw: str, group_id: str = "") -> str:
    """用本群「图反推」所选服务商的文本模型对原始反推词做润色整理。

    按 group_id 经 llm_providers 解析服务商与模型；同步阻塞调用必须丢进线程池并设 timeout，
    避免冻结事件循环或永久挂起。返回空字符串表示优化失败/空结果。
    """
    client = llm_providers.build_client(group_id, "reverse")
    model = llm_providers.get_chat_model(group_id, "reverse")
    opt_kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": OPTIMIZE_INSTRUCTION},
            {"role": "user", "content": raw},
        ],
        temperature=0.3,
        max_tokens=800,
        timeout=60,
    )
    # Grok 思考强度（提速）；DeepSeek 与非推理 Grok 模型自动为空
    opt_extra = llm_providers.get_grok_extra_body(group_id, "reverse", model)
    if opt_extra:
        opt_kwargs["extra_body"] = opt_extra
    response = await asyncio.to_thread(
        client.chat.completions.create,
        **opt_kwargs,
    )
    return (response.choices[0].message.content or "").strip()


def _normalize_image(data: bytes, max_side: int = 1280) -> bytes:
    """将任意 PIL 可解码的图片解码并统一重编码为 PNG（DeepSeek 明确支持的格式）。

    作用：
    - 不再依赖扩展名/文件头“猜测” MIME，从根本上避免“实际字节非 webp/png/jpeg/gif
      却标成 image/jpeg 被 DeepSeek 拒图”的问题；
    - 同时校验字节确为可解码图片（若下载到的是错误 HTML 页等，会在这一步抛异常）；
    - 超长边超过 max_side 时等比缩图，降低 base64 体积与视觉 token 消耗。
    """
    img = Image.open(BytesIO(data))
    img = img.convert("RGB")  # 丢弃 alpha / 动画帧，统一为静态 RGB
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.LANCZOS,
        )
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ========== 开启图反推润色 / 关闭图反推润色（超级用户，按群）==========
# 与「开启AI / 关闭AI」同构：仅超管可在群聊中开关，按群记录到 reverse_polish_groups.json。
# 开启后该群图反推自动用 DeepSeek 文本模型润色（默认关；关键字「优化」仍可作临时触发）。
# 注意：不用 on_command（否则命令前缀/开头匹配规则导致超管在群里发「开启图反推润色」静默不触发），
# 改用 on_message + 自定义规则，消息任意文本段含对应短语即触发，与图反推触发方式一致。

async def _is_polish_enable_request(event: MessageEvent) -> bool:
    """消息文本段含「开启图反推润色/优化/美化」其一即视为开启请求"""
    text = "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )
    return any(kw in text for kw in ("开启图反推润色", "开启图反推优化", "开启图反推美化"))


async def _is_polish_disable_request(event: MessageEvent) -> bool:
    """消息文本段含「关闭图反推润色/优化/美化」其一即视为关闭请求"""
    text = "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )
    return any(kw in text for kw in ("关闭图反推润色", "关闭图反推优化", "关闭图反推美化"))


enable_polish_cmd = on_message(
    rule=Rule(_is_polish_enable_request),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@enable_polish_cmd.handle()
async def handle_enable_polish(matcher: Matcher, event: MessageEvent):
    """超级用户开启本群图反推自动润色"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    if is_reverse_polish_enabled(gid):
        await matcher.finish("本群图反推润色已经开启。")
    enable_reverse_polish(gid)
    await matcher.finish(
        "✅ 已开启本群图反推润色：图反推将自动用 DeepSeek 润色提示词"
        "（未开启的群仍可用「优化/美化」关键字临时触发）。"
    )


disable_polish_cmd = on_message(
    rule=Rule(_is_polish_disable_request),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@disable_polish_cmd.handle()
async def handle_disable_polish(matcher: Matcher, event: MessageEvent):
    """超级用户关闭本群图反推自动润色"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    if not is_reverse_polish_enabled(gid):
        await matcher.finish("本群图反推润色已经关闭。")
    disable_reverse_polish(gid)
    await matcher.finish(
        "❌ 已关闭本群图反推润色（仍可用「优化/美化」关键字临时触发）。"
    )


# ========== 反推AI图（图反推 + AI 画图，普通用户可用，需本群同时开启 AI 功能 + AI 画图）==========
# 触发方式与图反推完全一致（消息内图片 / 引用含图消息 / @群友 头像），
# 反推出提示词后再直接调用 NovelAI 出图，返回形式与 ai画图 完全一致（合并转发图片）。
# 双权限守卫：图反推依赖 AI 功能（ai_chat 模块，DeepSeek 视觉），出图依赖 AI 画图（ai_draw 模块，NovelAI），
# 两者均需本群开启，未开启按统一开关层约定静默拦截。

REVERSE_AI_DRAW_KEYWORDS = ("反推AI图", "反推ai图", "反推生图")


async def _is_reverse_ai_draw_request(event: MessageEvent) -> bool:
    """消息文本归一化后须**精确等于**「反推AI图」等指令（不含任何润色修饰符，与图反推一致）。"""
    text = "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )
    return _is_exact_command(text, REVERSE_AI_DRAW_KEYWORDS)


reverse_ai_draw_cmd = on_message(
    rule=Rule(_is_reverse_ai_draw_request),
    priority=5,
    block=True,
)


@reverse_ai_draw_cmd.handle()
async def handle_reverse_ai_draw(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """图反推 + AI 画图：从图片反推提示词后直接调 NovelAI 出图（返回与 ai画图 一致）"""
    # 仅在群聊可用（最终以合并转发形式发图，需 group_id）
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("⚠️ 反推AI图 仅支持在群聊中使用")
        return

    # 群聊双权限守卫：
    # - 图反推阶段依赖 AI 功能（ai_chat 模块，DeepSeek 视觉）；
    # - 出图阶段依赖 AI 画图（ai_draw 模块，NovelAI）。
    # 两者均需本群开启，未开启按统一开关层约定静默拦截。
    gid = str(event.group_id)
    if not is_ai_group_enabled(gid):
        await matcher.finish()
        return
    if not is_novelai_group_enabled(gid):
        await matcher.finish()
        return

    # 1. 提取图片（与图反推同优先级：消息内 > 引用 > @群友 头像）
    image_url, source_desc = _extract_image(event)
    if not image_url:
        await matcher.finish(
            "请发送一张图片、引用含图片的消息，或 @一位群友后发送「反推AI图」"
        )
        return

    # 2. 是否润色（与图反推一致：消息含优化关键字，或本群已开启图反推润色开关）
    need_optimize = any(kw in _message_text(event) for kw in OPTIMIZE_KEYWORDS)
    if is_reverse_polish_enabled(gid):
        need_optimize = True

    await matcher.send("正在反推提示词并使用 NovelAI 生成图片，请稍候...")

    # 3. 反推 + 可选润色（复用图反推的串行锁，避免并发打爆视觉模型）
    async with _reverse_lock:
        try:
            image_data = await download_image(bot, image_url)
        except Exception as e:
            logger.error(f"反推AI图 图片下载失败: {e}")
            await matcher.finish("图片下载失败，请重试")
            return
        if not image_data:
            await matcher.finish("图片下载失败，请重试")
            return

        # 3.0 先尝试读取 NovelAI 嵌入元数据；命中则跳过视觉反推，直接拿原图提示词出图
        meta = _extract_nai_metadata(image_data)
        if meta and meta.get("prompt"):
            prompt, _ = await _resolve_metadata_prompt(meta, need_optimize, gid)
            if prompt:
                prompt, _ = correct_nai_artist_strings(prompt)
                await draw_prompt_to_image(bot, event, matcher, prompt)
                return

        try:
            image_data = _normalize_image(image_data)
        except Exception as e:
            logger.error(f"反推AI图 图片解码失败: {e}")
            await matcher.finish("图片无法解码，请换一张图重试")
            return

        # 3.1 视觉模型反推（服务商按本群「图反推」设置解析）
        try:
            client = llm_providers.build_client(gid, "reverse")
            vision_model = llm_providers.get_vision_model(gid, "reverse")
            extra_body = llm_providers.get_vision_extra_body(gid, "reverse")
            # Grok 思考强度（提速）：推理模型默认 high，可调低；DeepSeek / 非推理 Grok 自动为空
            extra_body.update(llm_providers.get_grok_extra_body(gid, "reverse", vision_model))
            data_url = f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            create_kwargs = dict(
                model=vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": reverse_instruction(gid)},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                temperature=0.5,
                max_tokens=800,
                timeout=60,
            )
            if extra_body:
                create_kwargs["extra_body"] = extra_body
            response = await asyncio.to_thread(
                client.chat.completions.create,
                **create_kwargs,
            )
            raw = _extract_vision_text(response.choices[0].message)
            prompt = _sanitize_reverse_prompt(raw)
            if prompt and prompt != raw.strip():
                logger.info(
                    f"[图反推] 视觉模型返回已清洗噪声（如代码块/思考链/引号）："
                    f"raw前40={raw[:40]!r}"
                )
        except ValueError as e:
            await matcher.finish(f"反推AI图 配置错误：{e}")
            return
        except Exception as e:
            logger.error(f"反推AI图 视觉调用失败: {e}")
            await matcher.finish(f"反推失败：{type(e).__name__} - {e}")
            return

        if not prompt:
            try:
                choice = response.choices[0]
                fr = getattr(choice, "finish_reason", None)
                has_reason = _message_has_reasoning(choice.message)
                detail = f"（finish_reason={fr}，思考模式={has_reason}）"
            except Exception:
                detail = ""
            await matcher.finish(f"未能反推出提示词，请换一张图重试{detail}")
            return

        # 3.2 可选 DS 文本润色（失败不阻断，回退原始反推词）
        if need_optimize:
            try:
                optimized = await _optimize_prompt(prompt, gid)
                if optimized:
                    prompt = optimized
            except Exception as e:
                logger.error(f"反推AI图 优化失败: {e}")
                await matcher.send(f"提示词优化失败（{type(e).__name__}），使用原始反推词出图")

    # 3.3 画师串修正（与图反推文本结果同一套逻辑），规整后再出图
    prompt, _ = correct_nai_artist_strings(prompt)

    # 4. 调 NovelAI 出图并合并转发发送（与 ai画图 一致，图片 + 提示词一起返回）
    await draw_prompt_to_image(bot, event, matcher, prompt)


# ========== 图反推提示词运行时管理（仅超级用户，全局按服务商生效，无需重启 bot）==========
# 默认提示词写死在 REVERSE_PROMPT_INSTRUCTION_*，本组指令允许超管在运行时覆盖/查看/重置，
# 立即持久化到 reverse_prompt_custom.json 并生效，无需改代码重启。
#   - 修改prompt <ds|grok|gemini> <新提示词> ：覆盖该服务商的图反推提示词（全局即时生效）
#   - 查看prompt <ds|grok|gemini>           ：显示当前生效提示词（标注「自定义」或「默认」）
#   - 重置prompt <ds|grok|gemini>           ：清除自定义，回退写死的默认
# 注：提示词为全局（按服务商）配置，影响所有使用该服务商的群，故限超管。


def _match_modify_prompt(text: str) -> tuple[str, str] | None:
    """解析「修改prompt <服务商> <提示词>」。返回 (provider, prompt) 或 None。

    prompt 允许为空（交由调用方判定为用法提示）。服务商非法时 provider="__invalid__"。
    """
    m = re.match(r"^修改prompt\s+(\S+)\s*([\s\S]*)$", text.strip())
    if not m:
        return None
    alias = m.group(1).lower()
    provider = _REVERSE_PROMPT_PROVIDER_ALIAS.get(alias)
    if not provider:
        return ("__invalid__", alias)
    return (provider, m.group(2).strip())


def _match_view_prompt(text: str) -> str | None:
    """解析「查看prompt <服务商>」。返回 provider 或 "__invalid__" 或 None（非本指令）。"""
    m = re.match(r"^查看prompt\s+(\S+)\s*$", text.strip())
    if not m:
        return None
    alias = m.group(1).lower()
    return _REVERSE_PROMPT_PROVIDER_ALIAS.get(alias, "__invalid__")


def _match_reset_prompt(text: str) -> str | None:
    """解析「重置prompt <服务商>」。返回 provider 或 "__invalid__" 或 None（非本指令）。"""
    m = re.match(r"^重置prompt\s+(\S+)\s*$", text.strip())
    if not m:
        return None
    alias = m.group(1).lower()
    return _REVERSE_PROMPT_PROVIDER_ALIAS.get(alias, "__invalid__")


def _is_prompt_manage_request(event: MessageEvent) -> bool:
    """消息文本命中任一图反推提示词管理指令（修改/查看/重置）即触发。"""
    text = _message_text(event).strip()
    return (
        bool(re.match(r"^修改prompt\s+", text))
        or bool(re.match(r"^查看prompt\s+", text))
        or bool(re.match(r"^重置prompt\s+", text))
    )


_PROVIDER_LABEL = {"deepseek": "DS（DeepSeek）", "grok": "Grok", "gemini": "Gemini"}

PROMPT_MANAGE_HELP = (
    "图反推提示词管理指令（仅超管可用）：\n"
    "修改prompt <ds|grok|gemini> <新提示词> —— 覆盖该服务商的图反推提示词（全局即时生效）\n"
    "查看prompt <ds|grok|gemini> —— 查看当前生效的提示词\n"
    "重置prompt <ds|grok|gemini> —— 清除自定义，回退默认写死提示词"
)


prompt_manage_cmd = on_message(
    rule=Rule(_is_prompt_manage_request),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@prompt_manage_cmd.handle()
async def handle_prompt_manage(matcher: Matcher, event: MessageEvent):
    """超管在运行时管理图反推提示词（修改/查看/重置），无需重启 bot。"""
    text = _message_text(event).strip()

    # 1) 修改
    if (mp := _match_modify_prompt(text)) is not None:
        provider, prompt = mp
        if provider == "__invalid__":
            await matcher.finish(
                f"未知服务商「{prompt}」。支持：ds（DeepSeek）、grok、gemini。\n{PROMPT_MANAGE_HELP}"
            )
        if not prompt:
            await matcher.finish(
                f"请提供新提示词。用法：\n修改prompt {provider} <新提示词>\n"
                "（提示词为全局配置，影响所有使用该服务商的群）"
            )
        set_reverse_prompt_custom(provider, prompt)
        await matcher.finish(
            f"✅ 已更新 {_PROVIDER_LABEL.get(provider, provider)} 的图反推提示词，"
            "全局即时生效，下一次图反推即使用新提示词（无需重启）。"
        )

    # 2) 查看
    if (vp := _match_view_prompt(text)) is not None:
        if vp == "__invalid__":
            # 从原文取别名给出更友好提示
            alias = re.match(r"^查看prompt\s+(\S+)", text)
            await matcher.finish(
                f"未知服务商「{(alias.group(1) if alias else '')}」。支持：ds（DeepSeek）、grok、gemini。"
            )
        custom = get_reverse_prompt_custom(vp)
        if custom:
            await matcher.finish(
                f"【{_PROVIDER_LABEL.get(vp, vp)} 图反推提示词 · 自定义】\n\n{custom}"
            )
        await matcher.finish(
            f"【{_PROVIDER_LABEL.get(vp, vp)} 图反推提示词 · 默认（写死）】\n\n{_default_prompt_for(vp)}"
        )

    # 3) 重置
    if (rp := _match_reset_prompt(text)) is not None:
        if rp == "__invalid__":
            alias = re.match(r"^重置prompt\s+(\S+)", text)
            await matcher.finish(
                f"未知服务商「{(alias.group(1) if alias else '')}」。支持：ds（DeepSeek）、grok、gemini。"
            )
        if reset_reverse_prompt_custom(rp):
            await matcher.finish(
                f"✅ 已重置 {_PROVIDER_LABEL.get(rp, rp)} 的图反推提示词为默认（写死）值。"
            )
        await matcher.finish(
            f"{_PROVIDER_LABEL.get(rp, rp)} 当前已是默认提示词，无需重置。"
        )
