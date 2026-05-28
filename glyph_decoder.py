"""
字形解码器 — 破解字节跳动自定义字体反爬。
双模式：PaddleOCR API（优先）→ 感知哈希像素比对（兜底）。
将PUA码点的字形识别为对应汉字，构建 PUA→汉字 映射表。
"""
import io
import json
import os
import re
import struct
import sys
import time
import urllib.request
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
import imagehash

# ===== PaddleOCR API 配置 =====
PADDLEOCR_URL = os.environ.get("PADDLEOCR_API_URL", "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs")
PADDLEOCR_TOKEN = os.environ.get("PADDLEOCR_TOKEN", "")
PADDLEOCR_MODEL = "PaddleOCR-VL-1.5"

# 批次参数（每次 API 调用处理的字符数）
OCR_CHARS_PER_ROW = 10          # 每行字符数
OCR_ROWS_PER_BATCH = 6          # 每批次行数
OCR_CHARS_PER_BATCH = OCR_CHARS_PER_ROW * OCR_ROWS_PER_BATCH  # 60
OCR_CELL_SIZE = 80              # 单元格像素
OCR_FONT_SIZE = 48              # 渲染字号
OCR_PADDING = 16                # 单元格间距

# 参考中文字体候选（phash 兜底模式用）
_REF_FONT_PATHS = [
    "C:/Windows/Fonts/msyh.ttc",   # 微软雅黑（现代风格，最接近网页字体）
    "C:/Windows/Fonts/simhei.ttf", # 黑体
]

# 常用汉字集（前2500字，覆盖日常阅读的97%）
_COMMON_CHARS = (
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学家所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该铁价严龙飞"
)

# 字体大小和渲染尺寸（phash 兜底用）
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
    return imagehash.phash(img, hash_size=16)  # 16=256bit


# requests 为OCR API调用所需（可选依赖，仅使用OCR模式时需要）
try:
    import requests as _requests_module
except ImportError:
    _requests_module = None


def _create_batch_grid_image(pua_chars: list, font_path: str, batch_idx: int,
                             chars_per_row: int = None, rows_per_batch: int = None,
                             cell_size: int = None, font_size: int = None,
                             padding: int = None) -> tuple:
    """
    创建PUA字符网格图片，供PaddleOCR批量识别。
    返回 (图片路径, 该批次的PUA字符列表)。
    """
    chars_per_row = chars_per_row or OCR_CHARS_PER_ROW
    rows_per_batch = rows_per_batch or OCR_ROWS_PER_BATCH
    cell_size = cell_size or OCR_CELL_SIZE
    font_size = font_size or OCR_FONT_SIZE
    padding = padding or OCR_PADDING

    chars_per_batch = chars_per_row * rows_per_batch
    start = batch_idx * chars_per_batch
    batch_chars = pua_chars[start:start + chars_per_batch]

    if not batch_chars:
        return None, []

    cols = min(chars_per_row, len(batch_chars))
    rows = (len(batch_chars) + cols - 1) // cols

    img_w = cols * (cell_size + padding) + padding
    img_h = rows * (cell_size + padding) + padding

    img = Image.new("L", (img_w, img_h), 255)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        return None, batch_chars

    for i, ch in enumerate(batch_chars):
        row = i // cols
        col = i % cols
        cx = padding + col * (cell_size + padding) + cell_size // 2
        cy = padding + row * (cell_size + padding) + cell_size // 2

        bbox = draw.textbbox((0, 0), ch, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        if tw <= 0 or th <= 0:
            continue
        tx = cx - tw // 2 - bbox[0]
        ty = cy - th // 2 - bbox[1]
        draw.text((tx, ty), ch, fill=0, font=font)

    tmp_path = f"C:/temp/_ocr_batch_{batch_idx}.png"
    os.makedirs("C:/temp", exist_ok=True)
    img.save(tmp_path)
    return tmp_path, batch_chars


def _submit_ocr_job(image_path: str, token: str = None) -> str:
    """提交OCR任务，返回 jobId"""
    token = token or PADDLEOCR_TOKEN
    headers = {"Authorization": f"bearer {token}"}

    data = {
        "model": PADDLEOCR_MODEL,
        "optionalPayload": json.dumps({
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        })
    }

    with open(image_path, "rb") as f:
        resp = _requests_module.post(PADDLEOCR_URL, headers=headers, data=data,
                                 files={"file": f}, timeout=30)

    if resp.status_code != 200:
        raise Exception(f"OCR提交失败 HTTP {resp.status_code}: {resp.text[:200]}")

    job_id = resp.json()["data"]["jobId"]
    return job_id


def _poll_ocr_job(job_id: str, token: str = None, timeout: int = 120) -> str:
    """轮询OCR任务直到完成，返回 resultUrl.jsonUrl"""
    token = token or PADDLEOCR_TOKEN
    headers = {"Authorization": f"bearer {token}"}
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = _requests_module.get(f"{PADDLEOCR_URL}/{job_id}", headers=headers, timeout=15)
        if resp.status_code != 200:
            time.sleep(3)
            continue

        data = resp.json()["data"]
        state = data["state"]

        if state == "done":
            return data["resultUrl"]["jsonUrl"]
        elif state == "failed":
            raise Exception(f"OCR任务失败: {data.get('errorMsg', '未知错误')}")
        else:
            # pending / running
            time.sleep(3)

    raise Exception(f"OCR任务超时 ({timeout}s)")


def _download_ocr_text(jsonl_url: str) -> str:
    """下载OCR结果JSONL，提取所有识别文本"""
    resp = _requests_module.get(jsonl_url, timeout=30)
    resp.raise_for_status()

    all_text = []
    for line in resp.text.strip().split('\n'):
        if not line.strip():
            continue
        try:
            result = json.loads(line)["result"]
            for res in result.get("layoutParsingResults", []):
                md_text = res.get("markdown", {}).get("text", "")
                if md_text:
                    all_text.append(md_text)
        except (json.JSONDecodeError, KeyError):
            pass

    return "\n".join(all_text)


def decode_font_via_ocr(font_url: str, token: str = None, timeout: int = 180) -> dict:
    """
    PaddleOCR API 解码模式。
    将PUA字符分批渲染为网格图片 → 提交OCR识别 → 构建映射表。
    返回 {PUA码点→汉字} 映射表。
    """
    token = token or PADDLEOCR_TOKEN
    if not token:
        print("[OCR] 未配置 PADDLEOCR_TOKEN，跳过OCR模式", file=sys.stderr, flush=True)
        return {}

    if _requests_module is None:
        print("[OCR] 缺少 requests 库，请执行 pip install requests", file=sys.stderr, flush=True)
        return {}

    # 1. 下载字体
    req = urllib.request.Request(font_url, headers={"User-Agent": "Mozilla/5.0"})
    font_data = urllib.request.urlopen(req, timeout=timeout).read()

    # 2. 解析字体
    font = TTFont(io.BytesIO(font_data))
    cmap = font.getBestCmap()

    # 3. 导出为临时TTF供Pillow渲染
    tmp_path = "C:/temp/_fanqie_decoded.ttf"
    os.makedirs("C:/temp", exist_ok=True)
    font.flavor = None
    font.save(tmp_path)

    # 4. 提取PUA字符集
    pua_chars = [chr(cp) for cp in cmap if 0xE000 <= cp <= 0xF8FF]
    total = len(pua_chars)
    print(f"[OCR] PUA字符数: {total}", file=sys.stderr, flush=True)

    if total == 0:
        return {}

    # 5. 分批创建网格图片并OCR识别
    batch_count = (total + OCR_CHARS_PER_BATCH - 1) // OCR_CHARS_PER_BATCH
    mapping = {}
    decoded = 0
    failed = 0

    for batch_idx in range(batch_count):
        img_path, batch_chars = _create_batch_grid_image(
            pua_chars, tmp_path, batch_idx
        )
        if not img_path or not batch_chars:
            continue

        try:
            print(f"[OCR] 批次 {batch_idx+1}/{batch_count}: "
                  f"{len(batch_chars)}字, 提交中...", file=sys.stderr, flush=True)

            job_id = _submit_ocr_job(img_path, token)
            print(f"[OCR] jobId={job_id[:12]}..., 等待结果...", file=sys.stderr, flush=True)

            jsonl_url = _poll_ocr_job(job_id, token, timeout=timeout)
            ocr_text = _download_ocr_text(jsonl_url)

            # 清洗：保留中文字符 + 英文大小写 + 数字
            cleaned = re.sub(r'[^一-鿿A-Za-z0-9]', '', ocr_text)
            print(f"[OCR] 识别到 {len(cleaned)} 个字符（含英文数字）, "
                  f"预期 {len(batch_chars)} 个", file=sys.stderr, flush=True)
            # 调试：输出前20个识别字符
            if len(cleaned) > 0:
                sample = cleaned[:20] if len(cleaned) >= 20 else cleaned
                print(f"[OCR] 样本: {sample}", file=sys.stderr, flush=True)

            # 按位置映射
            for i, ch in enumerate(cleaned):
                if i < len(batch_chars):
                    mapping[batch_chars[i]] = ch
                    decoded += 1

            # 未被识别的字符标记为失败
            for i in range(len(cleaned), len(batch_chars)):
                mapping[batch_chars[i]] = "□"
                failed += 1

        except Exception as e:
            print(f"[OCR] 批次 {batch_idx+1} 失败: {e}", file=sys.stderr, flush=True)
            for ch in batch_chars:
                mapping[ch] = "□"
                failed += 1
        finally:
            # 清理临时图片
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception:
                pass

    print(f"[OCR] 解码完成: {decoded}/{total}字 ({failed}失败)",
          file=sys.stderr, flush=True)
    return mapping


# ============================================================
#  phash 像素比对模式（兜底，无网络依赖）
# ============================================================

def decode_font_phash(font_url: str, timeout: int = 10) -> dict:
    """下载并解析字体文件，通过感知哈希+像素比对返回 {PUA码点→汉字} 映射表"""
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


# ============================================================
#  统一入口（自动选择OCR → phash兜底）
# ============================================================

def decode_font(font_url: str, timeout: int = 10, token: str = None) -> dict:
    """
    解码字体，自动选择最佳模式：
    1. 如果配置了 PADDLEOCR_TOKEN → 优先OCR模式（高精度）
    2. 否则 → phash像素比对（本地兜底）
    返回 {PUA码点→汉字} 映射表
    """
    token = token or PADDLEOCR_TOKEN

    if token:
        print("[Decoder] 使用 PaddleOCR 模式", file=sys.stderr, flush=True)
        mapping = decode_font_via_ocr(font_url, token=token, timeout=max(timeout, 180))
        if mapping and sum(1 for v in mapping.values() if v != "□") > 0:
            return mapping
        print("[Decoder] OCR模式失败或无结果，回退到phash模式", file=sys.stderr, flush=True)

    print("[Decoder] 使用 phash 像素比对模式", file=sys.stderr, flush=True)
    return decode_font_phash(font_url, timeout=timeout)


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
