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

if platform.system() == 'Windows':
    EXIFTOOL_PATH = Path('./exiftool/exiftool.exe')
    ENCODING = 'gbk'
elif shutil.which('exiftool') is not None:
    EXIFTOOL_PATH = shutil.which('exiftool')
    ENCODING = 'utf-8'
else:
    EXIFTOOL_PATH = Path('./exiftool/exiftool')
    ENCODING = 'utf-8'


def get_exif(path) -> dict:
    """
    获取exif信息
    :param path: 照片路径
    :return: exif信息
    """
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
                return exif_dict
        except (FileNotFoundError, subprocess.CalledProcessError, OSError) as e:
            logger.warning(f'exiftool 提取失败，回退到 Pillow: {e}')

    # 兜底：使用 Pillow 提取 EXIF
    try:
        img = Image.open(path)
        raw_exif = img._getexif() or {}
        # EXIF 标签 ID → 名称映射（与 exiftool 输出风格对齐，去掉空格和斜杠）
        EXIF_TAG_MAP = {
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

    return exif_dict


def list_files(path: str, suffixes: set[str], depth: int = 0, max_depth: int = 20):
    """
    使用 pathlib 实现的版本

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
                    'is_file': True
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
