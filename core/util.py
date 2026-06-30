import io
import json
import os
import platform
import re
import shutil
import subprocess
import time
from functools import wraps
from pathlib import Path

from PIL import Image
from jinja2 import Template

from core.configs import templates_dir
from core.jinja2renders import (
    auto_logo,
    format_aperture,
    format_focal_length,
    format_iso,
    format_photo_time,
    format_shutter_speed,
    vh,
    vw,
)
from core.logger import logger
from core.cache import get_cached_rating, get_cached_exif, set_cached, flush_cache

if platform.system() == 'Windows':
    EXIFTOOL_PATH = Path('./exiftool/exiftool.exe')
    ENCODING = 'gbk'
elif shutil.which('exiftool') is not None:
    EXIFTOOL_PATH = shutil.which('exiftool')
    ENCODING = 'utf-8'
else:
    EXIFTOOL_PATH = Path('./exiftool/exiftool')
    ENCODING = 'utf-8'

_RATING_CACHE = {}


def _file_stat(path: str) -> tuple[int, int] | None:
    """获取文件的 mtime_ns 和 size，用于缓存键"""
    try:
        st = Path(path).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def get_exif(path) -> dict:
    """
    获取exif信息（优先从 SQLite 缓存读取）
    :param path: 照片路径
    :return: exif信息
    """
    # 优先查 SQLite 缓存
    stat = _file_stat(path)
    if stat:
        cached = get_cached_exif(path, stat[0], stat[1])
        if cached is not None:
            return cached

    exif_dict = {}

    # 优先使用 exiftool，提取更完整
    if EXIFTOOL_PATH and Path(str(EXIFTOOL_PATH)).exists():
        try:
            output_bytes = subprocess.check_output(
                [str(EXIFTOOL_PATH), '-d', '%Y-%m-%d %H:%M:%S%3f%z', path],
                stderr=subprocess.DEVNULL
            )
            output = output_bytes.decode('utf-8', errors='ignore')

            for line in output.splitlines():
                kv_pair = line.split(':', 1)
                if len(kv_pair) < 2:
                    continue
                key = kv_pair[0].strip()
                value = kv_pair[1].strip()
                key = re.sub(r'\s+', '', key)
                key = re.sub(r'/', '', key)
                value_clean = ''.join(c for c in value if ord(c) < 128)
                exif_dict[key] = value_clean

            if exif_dict:
                if stat:
                    set_cached(path, stat[0], stat[1], exif_dict=exif_dict)
                return exif_dict
        except (FileNotFoundError, subprocess.CalledProcessError, OSError) as e:
            logger.warning(f'exiftool 提取失败，回退到 Pillow: {e}')

    # 兜底：使用 Pillow 提取 EXIF
    try:
        with Image.open(path) as img:
            raw_exif = img._getexif() or {}
        # EXIF 标签 ID → 名称映射（与 exiftool 输出风格对齐，去掉空格和斜杠）
        EXIF_TAG_MAP = {
            18246: 'Rating',
            18249: 'RatingPercent',
            271: 'Make',
            272: 'CameraModelName',
            33432: 'Copyright',
            34855: 'ISO',
            36867: 'DateTimeOriginal',
            36868: 'CreateDate',
            37377: 'ShutterSpeedValue',
            37378: 'ApertureValue',
            37380: 'ExposureCompensation',
            37381: 'FNumber',
            37383: 'MeteringMode',
            37385: 'Flash',
            37386: 'FocalLength',
            37520: 'SubSecTimeOriginal',
            37521: 'SubSecTimeDigitized',
            41989: 'FocalLengthIn35mmFormat',
            42036: 'LensModel',
        }
        for tag_id, value in raw_exif.items():
            tag_name = EXIF_TAG_MAP.get(tag_id)
            if tag_name:
                # 处理 IFDRational 等类型
                if hasattr(value, 'numerator') and hasattr(value, 'denominator'):
                    if value.denominator != 0:
                        value = round(value.numerator / value.denominator, 4)
                    else:
                        value = 0
                elif isinstance(value, bytes):
                    value = value.decode('utf-8', errors='ignore').strip('\x00')
                value_clean = str(value)
                # 过滤非 ASCII 字符
                value_clean = ''.join(c for c in value_clean if ord(c) < 128)
                exif_dict[tag_name] = value_clean

        # 格式化 ShutterSpeedValue（APEX → 秒）
        if 'ShutterSpeedValue' in exif_dict and 'ShutterSpeed' not in exif_dict:
            try:
                ssv = float(exif_dict['ShutterSpeedValue'])
                import math
                exposure = math.pow(2, -ssv)
                if exposure >= 1:
                    exif_dict['ShutterSpeed'] = f'{exposure:.1f}'
                else:
                    exif_dict['ShutterSpeed'] = f'1/{round(1/exposure)}'
            except (ValueError, TypeError):
                pass

        # 格式化 ApertureValue（APEX → f/值）
        if 'ApertureValue' in exif_dict:
            try:
                av = float(exif_dict['ApertureValue'])
                import math
                f_number = round(math.pow(2, av / 2), 1)
                exif_dict['ApertureValue'] = str(f_number)
            except (ValueError, TypeError):
                pass

        # 格式化 FocalLength → 带 mm
        if 'FocalLength' in exif_dict and 'FocalLengthIn35mmFormat' not in exif_dict:
            exif_dict['FocalLengthIn35mmFormat'] = exif_dict['FocalLength']

    except Exception as e:
        logger.error(f'get_exif error: {path} : {e}')

    # 回写缓存（即使为空也记录，避免重复解析失败的文件）
    if stat:
        set_cached(path, stat[0], stat[1], exif_dict=exif_dict if exif_dict else None)
    return exif_dict


def normalize_rating(value):
    """Normalize Windows/Lightroom star ratings to 1-5."""
    if value is None:
        return None
    try:
        rating = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None


def normalize_rating_percent(value):
    """Normalize Windows RatingPercent values to 1-5 stars."""
    if value is None:
        return None
    try:
        percent = int(round(float(str(value).strip())))
    except (TypeError, ValueError):
        return None
    if percent <= 0:
        return None
    if percent <= 1:
        return 1
    if percent <= 25:
        return 2
    if percent <= 50:
        return 3
    if percent <= 75:
        return 4
    return 5 if percent <= 100 else None


def _extract_xmp_rating_from_text(text):
    if not text:
        return None
    patterns = [
        r'(?:xmp:)?Rating\s*=\s*["\']([^"\']+)["\']',
        r'<(?:xmp:)?Rating[^>]*>\s*([^<]+)\s*</(?:xmp:)?Rating>',
        r'<[^>]+(?:Rating)[^>]*>\s*([^<]+)\s*</[^>]+>',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            rating = normalize_rating(match.group(1))
            if rating is not None:
                return rating
    return None


def _read_sidecar_xmp_rating(path):
    image_path = Path(path)
    candidates = []
    stem_sidecar = image_path.with_suffix('.xmp')
    suffixed_sidecar = Path(str(image_path) + '.xmp')
    for candidate in (stem_sidecar, suffixed_sidecar):
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding='utf-8', errors='ignore')
            rating = _extract_xmp_rating_from_text(text)
            if rating is not None:
                return rating
        except OSError as e:
            logger.debug(f"读取 XMP sidecar 失败 {candidate}: {e}")
    return None


def _read_embedded_xmp_rating(path):
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        logger.debug(f"读取内嵌 XMP 失败 {path}: {e}")
        return None

    text = data.decode('utf-8', errors='ignore')
    xmp_match = re.search(
        r'(<x:xmpmeta\b.*?</x:xmpmeta>)',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if xmp_match:
        return _extract_xmp_rating_from_text(xmp_match.group(1))
    return _extract_xmp_rating_from_text(text)


def _read_pillow_rating(path):
    try:
        with Image.open(path) as img:
            raw_exif = img.getexif() or {}
            rating = normalize_rating(raw_exif.get(18246))
            if rating is not None:
                return rating
            return normalize_rating_percent(raw_exif.get(18249))
    except Exception as e:
        logger.debug(f"Pillow 读取星级失败 {path}: {e}")
    return None


def _rating_cache_key(path):
    image_path = Path(path)
    try:
        image_stat = image_path.stat()
    except OSError:
        return None

    sidecar_parts = []
    for candidate in (image_path.with_suffix('.xmp'), Path(str(image_path) + '.xmp')):
        try:
            stat = candidate.stat()
            sidecar_parts.append((str(candidate.resolve()), stat.st_mtime_ns, stat.st_size))
        except OSError:
            sidecar_parts.append((str(candidate), None, None))

    return (
        str(image_path.resolve()),
        image_stat.st_mtime_ns,
        image_stat.st_size,
        tuple(sidecar_parts),
    )


def get_image_rating(path):
    """Read Windows/Lightroom 1-5 star rating from EXIF/XMP metadata.

    优先查 SQLite 缓存（基于路径 + mtime + 文件大小），再查内存缓存，
    最后才调用 exiftool/Pillow/XMP 解析。
    """
    # 1. 内存缓存（最快，进程生命周期内有效）
    cache_key = _rating_cache_key(path)
    if cache_key in _RATING_CACHE:
        return _RATING_CACHE[cache_key]

    # 2. SQLite 持久化缓存（跨进程重启有效）
    stat = _file_stat(path)
    if stat:
        cached = get_cached_rating(path, stat[0], stat[1])
        if cached is not None:
            _RATING_CACHE[cache_key] = cached
            return cached

    # 3. 实际解析（exiftool → Pillow → XMP）
    rating = None
    try:
        exif = get_exif(path)
        rating = normalize_rating(exif.get('Rating'))
        if rating is None:
            rating = normalize_rating_percent(exif.get('RatingPercent'))
    except Exception as e:
        logger.debug(f"EXIF 读取星级失败 {path}: {e}")

    if rating is None:
        rating = _read_pillow_rating(path)
    if rating is None:
        rating = _read_embedded_xmp_rating(path)
    if rating is None:
        rating = _read_sidecar_xmp_rating(path)

    # 回写缓存
    if cache_key is not None:
        _RATING_CACHE[cache_key] = rating
    if stat:
        set_cached(path, stat[0], stat[1], rating=rating)

    return rating


def list_children(path: str, suffixes: set[str]):
    """
    列出指定目录下的直接子项（不递归），用于树形懒加载。

    Args:
        path: 要扫描的目录路径
        suffixes: 支持的文件后缀集合

    Returns:
        list[dict]: 子目录和文件列表，目录节点含 has_children 标记
    """
    result = []
    root = Path(path).resolve()

    if not root.exists() or not root.is_dir():
        return result

    try:
        items = list(root.iterdir())
        dirs = sorted(
            [i for i in items if i.is_dir() and not i.name.startswith('.') and not i.is_symlink()],
            key=lambda x: x.name.lower()
        )
        files = sorted(
            [i for i in items if i.is_file() and not i.name.startswith('.') and i.suffix.lower() in suffixes],
            key=lambda x: (x.stat().st_mtime, x.name.lower()),
            reverse=True
        )

        # 目录节点：附带 has_children 标记供前端判断是否可展开
        for item in dirs:
            try:
                sub_items = list(item.iterdir())
                has_kids = any(
                    (s.is_dir() and not s.name.startswith('.') and not s.is_symlink()) or
                    (s.is_file() and not s.name.startswith('.') and s.suffix.lower() in suffixes)
                    for s in sub_items
                )
            except (PermissionError, OSError):
                has_kids = False

            result.append({
                'label': item.name,
                'value': str(item),
                'children': [],
                'has_children': has_kids,
            })

        # 文件节点：附带星级评分
        for item in files:
            result.append({
                'label': item.name,
                'value': str(item),
                'is_file': True,
                'rating': get_image_rating(str(item)),
            })

    except PermissionError:
        logger.debug(f"list_children: 权限不足，跳过 {path}")
    except Exception as e:
        logger.error(f"list_children: 扫描失败 {path}: {e}")

    return result


def list_files(path: str, suffixes: set[str], depth: int = 0, max_depth: int = 20):
    """
    递归扫描目录树（保留向后兼容，新代码优先使用 list_children）。

    Args:
        path: 要扫描的路径
        suffixes: 支持的文件后缀
        depth: 当前递归深度（内部使用）
        max_depth: 最大递归深度，防止无限递归
    """
    result = []
    root = Path(path).resolve()

    if not root.exists():
        return result

    # 防止递归过深
    if depth > max_depth:
        logger.warning(f"list_files: 达到最大递归深度 {max_depth}，跳过 {path}")
        return result

    try:
        # 分离文件夹和文件，分别排序
        items = list(root.iterdir())
        dirs = sorted([i for i in items if i.is_dir()], key=lambda x: x.name.lower(), reverse=True)
        files = sorted([i for i in items if i.is_file()], key=lambda x: (x.stat().st_mtime, x.name.lower()),
                       reverse=True)

        # 先处理文件夹
        for item in dirs:
            if item.name.startswith('.'):
                continue
            # 跳过符号链接，避免无限递归
            if item.is_symlink():
                continue
            children = list_files(str(item), suffixes, depth + 1, max_depth)
            if children:
                result.append({
                    'label': item.name,
                    'value': str(item),
                    'children': children,
                })

        # 再处理文件
        for item in files:
            if item.name.startswith('.'):
                continue
            if item.suffix.lower() in suffixes:
                result.append({
                    'label': item.name,
                    'value': str(item),
                    'is_file': True,
                    'rating': get_image_rating(str(item)),
                })

    except PermissionError:
        logger.debug(f"list_files: 权限不足，跳过 {path}")
    except Exception as e:
        logger.error(f"list_files: 扫描失败 {path}: {e}")

    return result


def log_rt(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()  # 记录开始时间
        result = func(*args, **kwargs)  # 调用被装饰的函数
        end_time = time.time()  # 记录结束时间
        elapsed_time = (end_time - start_time) * 1000  # 计算运行时间

        logger.debug(f"[monitor]api#{func.__name__} cost {elapsed_time:.2f}ms")
        return result

    return wrapper


def convert_heic_to_jpeg(path: str, quality: int = 90) -> io.BytesIO:
    """转换 HEIC 为 JPEG 字节流"""
    with Image.open(path) as img:
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')

        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality)
        buffer.seek(0)
        return buffer


# ==================== 模板管理相关方法 ====================

def get_template_path(template_name: str) -> Path:
    """
    获取模板文件的完整路径

    Args:
        template_name: 模板名称（不含扩展名），如 "standard1"

    Returns:
        模板文件的完整 Path 对象
    """
    return templates_dir / f"{template_name}.json"


def get_template(template_name: str) -> Template:
    """
    读取并解析模板文件为 Jinja2 Template 对象

    Args:
        template_name: 模板名称（不含扩展名），如 "standard1"

    Returns:
        Jinja2 Template 对象，已注册 vh, vw, auto_logo 全局函数
    """
    template_path = get_template_path(template_name)
    with open(template_path, encoding='utf-8') as f:
        template_str = f.read()
    template = Template(template_str)
    template.globals['vh'] = vh
    template.globals['vw'] = vw
    template.globals['auto_logo'] = auto_logo
    template.globals['format_photo_time'] = format_photo_time
    template.globals['format_focal_length'] = format_focal_length
    template.globals['format_shutter_speed'] = format_shutter_speed
    template.globals['format_aperture'] = format_aperture
    template.globals['format_iso'] = format_iso
    return template


def get_template_content(template_name: str) -> str:
    """
    获取模板文件的内容（原始字符串）

    Args:
        template_name: 模板名称（不含扩展名），如 "standard1"

    Returns:
        模板文件的原始内容字符串
    """
    template_path = get_template_path(template_name)
    with open(template_path, encoding='utf-8') as f:
        return f.read()


def save_template(template_name: str, content: str) -> None:
    """
    保存模板文件

    Args:
        template_name: 模板名称（不含扩展名），如 "standard1"
        content: 模板内容（JSON 字符串）
    """
    template_path = get_template_path(template_name)
    # 确保目录存在
    template_path.parent.mkdir(parents=True, exist_ok=True)
    with open(template_path, 'w', encoding='utf-8') as f:
        f.write(content)


def create_template(template_name: str, content: str = '[]') -> None:
    """
    创建新的模板文件

    Args:
        template_name: 模板名称（不含扩展名），如 "my_template"
        content: 模板内容（JSON 字符串），默认为空数组 '[]'

    Raises:
        FileExistsError: 如果模板文件已存在
    """
    template_path = get_template_path(template_name)
    if template_path.exists():
        raise FileExistsError(f"模板 '{template_name}' 已存在")
    save_template(template_name, content)


def list_templates() -> list[str]:
    """
    列出所有可用的模板名称

    Returns:
        模板名称列表（不含扩展名）
    """
    if not templates_dir.exists():
        return []
    return [f.stem for f in templates_dir.glob('*.json')]
