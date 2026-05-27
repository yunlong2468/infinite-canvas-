"""
字形解码器 — 破解字节跳动自定义字体反爬。
原理：将PUA码点的字形与标准中文字体的字形做感知哈希比对，
找到最匹配的汉字，构建 PUA→汉字 映射表。
"""
import io
import json
import os
import re
import struct
import urllib.request
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
import imagehash

# 参考中文字体候选（按优先级，选择匹配度最高的）
_REF_FONT_PATHS = [
    "C:/Windows/Fonts/msyh.ttc",   # 微软雅黑（现代风格，最接近网页字体）
    "C:/Windows/Fonts/simhei.ttf", # 黑体
]

# 常用汉字集（前2500字，覆盖日常阅读的97%）
_COMMON_CHARS = (
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学家所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该铁价严龙飞"
)

# 字体大小和渲染尺寸
_FONT_SIZE = 128
_IMG_SIZE = 128


def _render_glyph_pixels(font_path: str, char: str) -> list:
    """渲染字符为二值像素数组"""
    try:
        font = ImageFont.truetype(font_path, _FONT_SIZE)
    except Exception:
        return None
    img = Image.new("L", (_IMG_SIZE, _IMG_SIZE), 255)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), char, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    if w <= 0 or h <= 0:
        return None
    x = (_IMG_SIZE - w) // 2 - bbox[0]
    y = (_IMG_SIZE - h) // 2 - bbox[1]
    draw.text((x, y), char, fill=0, font=font)
    return [1 if p < 128 else 0 for p in img.getdata()]


def _pixel_similarity(p1: list, p2: list) -> float:
    """像素相似度 0~1"""
    if not p1 or not p2 or len(p1) != len(p2):
        return 0.0
    return sum(1 for a, b in zip(p1, p2) if a == b) / len(p1)


def _render_glyph_hash(font_path: str, char: str) -> imagehash.ImageHash:
    """渲染字符为图像，返回感知哈希（粗筛用）"""
    try:
        font = ImageFont.truetype(font_path, _FONT_SIZE)
    except Exception:
        return None
    img = Image.new("L", (_IMG_SIZE, _IMG_SIZE), 255)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), char, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    if w <= 0 or h <= 0:
        return None
    x = (_IMG_SIZE - w) // 2 - bbox[0]
    y = (_IMG_SIZE - h) // 2 - bbox[1]
    draw.text((x, y), char, fill=0, font=font)
    return imagehash.phash(img, hash_size=16)
    """渲染单个字符为图像，返回其感知哈希"""
    try:
        font = ImageFont.truetype(font_path, _FONT_SIZE)
    except Exception:
        return None
    img = Image.new("L", (_IMG_SIZE, _IMG_SIZE), 255)
    draw = ImageDraw.Draw(img)
    # 居中绘制
    bbox = draw.textbbox((0, 0), char, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (_IMG_SIZE - w) // 2 - bbox[0]
    y = (_IMG_SIZE - h) // 2 - bbox[1]
    draw.text((x, y), char, fill=0, font=font)
    return imagehash.phash(img, hash_size=16)  # 16=256bit


def decode_font(font_url: str, timeout: int = 10) -> dict:
    """下载并解析字体文件，返回 {PUA码点→汉字} 映射表"""
    # 1. 下载字体
    req = urllib.request.Request(font_url, headers={"User-Agent": "Mozilla/5.0"})
    font_data = urllib.request.urlopen(req, timeout=timeout).read()

    # 2. 解析字体
    font = TTFont(io.BytesIO(font_data))
    cmap = font.getBestCmap()

    # 3. 导出为临时TTF供Pillow渲染（fontTools无法直接给Pillow用）
    tmp_path = "C:/temp/_fanqie_decoded.ttf"
    font.flavor = None  # woff2 → ttf
    font.save(tmp_path)

    # 4. 提取PUA字符集
    pua_chars = [chr(cp) for cp in cmap if 0xE000 <= cp <= 0xF8FF]
    print(f"[Decoder] PUA字符数: {len(pua_chars)}", flush=True)

    # 5. 渲染PUA字符（哈希 + 像素数组）
    pua_data = {}  # pua_char → {hash, pixels}
    for ch in pua_chars:
        h = _render_glyph_hash(tmp_path, ch)
        p = _render_glyph_pixels(tmp_path, ch)
        if h is not None and p is not None:
            pua_data[ch] = {"hash": h, "pixels": p}

    # 6. 用多个参考字体尝试匹配，选最佳结果
    best_mapping = {}
    best_score = 0
    for ref_font_path in _REF_FONT_PATHS:
        if not os.path.exists(ref_font_path):
            continue
        # 渲染参考汉字
        ref_data = {}
        for ch in _COMMON_CHARS:
            h = _render_glyph_hash(ref_font_path, ch)
            p = _render_glyph_pixels(ref_font_path, ch)
            if h is not None and p is not None:
                ref_data[ch] = {"hash": h, "pixels": p}

        # 两阶段匹配
        mapping = {}
        TOP_N = 5
        PIXEL_THRESHOLD = 0.55
        for pua_ch, pua_info in pua_data.items():
            candidates = []
            for ref_ch, ref_info in ref_data.items():
                dist = pua_info["hash"] - ref_info["hash"]
                candidates.append((dist, ref_ch))
            candidates.sort(key=lambda x: x[0])
            top5 = candidates[:TOP_N]

            best_char = top5[0][1] if top5 else "?"
            best_sim = 0.0
            for _, ref_ch in top5:
                sim = _pixel_similarity(pua_info["pixels"], ref_data[ref_ch]["pixels"])
                if sim > best_sim:
                    best_sim = sim
                    best_char = ref_ch

            if best_sim >= PIXEL_THRESHOLD or (top5 and top5[0][0] <= 15):
                mapping[pua_ch] = best_char
            else:
                mapping[pua_ch] = "□"

        score = sum(1 for v in mapping.values() if v != "□")
        print(f"[Decoder] 字体{os.path.basename(ref_font_path)}: {score}/{len(mapping)}字", flush=True)
        if score > best_score:
            best_score = score
            best_mapping = mapping

    print(f"[Decoder] 最佳匹配: {best_score}/{len(best_mapping)}字", flush=True)
    return best_mapping


def apply_mapping(html: str, mapping: dict) -> str:
    """将HTML中的PUA字符替换为映射后的汉字"""
    result = []
    for ch in html:
        if ch in mapping:
            result.append(mapping[ch])
        else:
            result.append(ch)
    return "".join(result)


def extract_font_url_from_page(page) -> str:
    """通过CDP找到自定义字体(.woff2)的URL。
    策略：先从HTML提取CSS link → 下载CSS解析@font-face → 兜底用document.fonts。
    （跨域CSS的cssRules不可访问，不能直接从styleSheets取）
    参数 page 是 Playwright 的 Page 对象。
    """
    try:
        # 1. 从页面HTML中提取所有CSS文件链接
        css_links = page.evaluate(
            "JSON.stringify(Array.from(document.querySelectorAll("
            "'link[rel=\"stylesheet\"]')).map(function(l){return l.href}))"
        )
        css_urls = json.loads(css_links) if css_links else []

        # 2. 逐个下载CSS文件，搜索@font-face中的woff2
        for css_url in css_urls:
            if not css_url or '.css' not in css_url.lower():
                continue
            # 补全协议
            if css_url.startswith('//'):
                css_url = 'https:' + css_url
            try:
                req = urllib.request.Request(css_url, headers={"User-Agent": "Mozilla/5.0"})
                css_text = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', errors='replace')
                # 搜索: url("https://...awesome-font...woff2") 或 url(https://...woff2)
                matches = re.findall(
                    r"url\([\"']?(https?://[^\"'\)]*awesome-font[^\"'\)]*\.woff2)[\"']?\)",
                    css_text
                )
                if matches:
                    return matches[0]
                # 搜索: font-family: DNMrHs... 且有 url(...woff2)
                if 'DNMrHs' in css_text or 'font-face' in css_text:
                    matches = re.findall(
                        r"url\([\"']?(https?://[^\"'\)]+\.woff2)[\"']?\)",
                        css_text
                    )
                    if matches:
                        return matches[0]
            except Exception:
                pass

        # 3. 兜底：从HTML文本中搜索
        return ""
    except Exception:
        return ""


def extract_font_url_from_html(html: str) -> str:
    """从HTML文本中提取字体URL（备用方案，外部CSS时无效）"""
    m = re.search(r"url\([\"']?(https?://[^\"'\s)]+\.woff2)[\"']?\)", html)
    if m:
        return m.group(1)
    m = re.search(r"(https?://[^\"'\s]+awesome-font[^\"'\s]+\.woff2)", html)
    if m:
        return m.group(1)
    return ""


if __name__ == "__main__":
    # 独立测试
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if url:
        mapping = decode_font(url, timeout=15)
        print(f"映射表: {len(mapping)} 条")
        # 展示前10条
        for i, (pua, han) in enumerate(mapping.items()):
            if i >= 10:
                break
            print(f"  U+{ord(pua):04X} → {han}")
