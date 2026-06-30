"""
SQLite 持久化缓存层，用于缓存图片 EXIF 星级评分、元数据和用户自定义文本。

避免每次启动后对每个文件重复调用 exiftool 子进程，
在机械硬盘/SMB 场景下显著降低 I/O 开销。
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from core.logger import logger

# 数据库连接（线程本地存储，确保线程安全）
_local = threading.local()

# 缓存数据库路径，由 core/__init__.py 中的 CACHE_DB_PATH 覆盖
CACHE_DB_PATH = 'config/cache.db'

# 缓存上限 200MB
MAX_CACHE_SIZE = 200 * 1024 * 1024

# 批量写入缓冲区（避免每条记录都触发 fsync）
_BATCH_BUFFER: dict[str, tuple] = {}
_BATCH_LOCK = threading.Lock()
_BATCH_SIZE = 50  # 攒够多少条后批量写入
_BATCH_FLUSH_INTERVAL = 5.0  # 秒，最大缓冲时间
_LAST_FLUSH = time.time()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（自动创建）"""
    conn = getattr(_local, 'conn', None)
    if conn is None:
        _local.conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
        _local.conn.execute('PRAGMA journal_mode=WAL')
        _local.conn.execute('PRAGMA synchronous=NORMAL')
        _local.conn.execute('PRAGMA cache_size=-8000')  # 8MB 缓存
        _local.conn.execute('PRAGMA busy_timeout=5000')
        _local.conn.row_factory = sqlite3.Row
        _init_schema(_local.conn)
    return _local.conn


def _init_schema(conn: sqlite3.Connection):
    """初始化数据库表结构"""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS file_cache (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            rating INTEGER,
            exif_json TEXT,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_file_cache_path ON file_cache(path)
    ''')
    # 用户自定义文本（地点等），独立于文件 mtime，持久保留
    conn.execute('''
        CREATE TABLE IF NOT EXISTS custom_text_cache (
            path TEXT PRIMARY KEY,
            text TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_custom_text_path ON custom_text_cache(path)
    ''')
    conn.commit()


def set_cache_db_path(path: str):
    """设置缓存数据库路径（需在首次调用前设置）"""
    global CACHE_DB_PATH
    CACHE_DB_PATH = path


def get_cached_rating(path: str, mtime_ns: int, size: int) -> int | None:
    """
    从 SQLite 缓存中读取星级评分。

    Args:
        path: 图片文件绝对路径
        mtime_ns: 文件修改时间（纳秒）
        size: 文件大小（字节）

    Returns:
        1-5 的星级评分，未缓存或缓存过期返回 None
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            'SELECT rating, mtime_ns, size FROM file_cache WHERE path = ?',
            (path,)
        ).fetchone()
        if row and row['mtime_ns'] == mtime_ns and row['size'] == size:
            return row['rating']
    except Exception as e:
        logger.debug(f'cache read error for {path}: {e}')
    return None


def get_cached_exif(path: str, mtime_ns: int, size: int) -> dict | None:
    """
    从 SQLite 缓存中读取完整 EXIF 数据。

    Returns:
        EXIF 字典，未命中返回 None
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            'SELECT exif_json, mtime_ns, size FROM file_cache WHERE path = ?',
            (path,)
        ).fetchone()
        if row and row['mtime_ns'] == mtime_ns and row['size'] == size:
            exif_json = row['exif_json']
            if exif_json:
                return json.loads(exif_json)
    except Exception as e:
        logger.debug(f'cache exif read error for {path}: {e}')
    return None


def set_cached(path: str, mtime_ns: int, size: int,
               rating: int | None = None, exif_dict: dict | None = None):
    """
    将星级评分和/或 EXIF 数据写入 SQLite 缓存。

    支持批量写入：先写入内存缓冲区，攒够 _BATCH_SIZE 条后批量刷盘。
    """
    with _BATCH_LOCK:
        _BATCH_BUFFER[path] = (mtime_ns, size, rating, exif_dict)
        if len(_BATCH_BUFFER) >= _BATCH_SIZE or _should_flush():
            _flush_buffer()


def _should_flush() -> bool:
    """检查是否超过缓冲时间"""
    global _LAST_FLUSH
    return time.time() - _LAST_FLUSH > _BATCH_FLUSH_INTERVAL


def flush_cache():
    """强制将缓冲区中的缓存数据写入数据库"""
    with _BATCH_LOCK:
        _flush_buffer()


def _flush_buffer():
    """将缓冲区数据批量写入数据库（需持有 _BATCH_LOCK）"""
    global _BATCH_BUFFER, _LAST_FLUSH
    if not _BATCH_BUFFER:
        return

    try:
        conn = _get_conn()
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        rows = []
        for path, (mtime_ns, size, rating, exif_dict) in _BATCH_BUFFER.items():
            exif_json = json.dumps(exif_dict, ensure_ascii=False) if exif_dict else None
            rows.append((path, mtime_ns, size, rating, exif_json, now))

        conn.executemany(
            '''INSERT OR REPLACE INTO file_cache
               (path, mtime_ns, size, rating, exif_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            rows
        )
        conn.commit()
        logger.debug(f'cache flush: {len(rows)} records written')
    except Exception as e:
        logger.error(f'cache flush error: {e}')
    finally:
        _BATCH_BUFFER.clear()
        _LAST_FLUSH = time.time()


def invalidate_cache(path: str):
    """使指定文件的缓存失效"""
    try:
        conn = _get_conn()
        conn.execute('DELETE FROM file_cache WHERE path = ?', (path,))
        conn.commit()
    except Exception as e:
        logger.debug(f'cache invalidate error for {path}: {e}')


def get_cache_stats() -> dict:
    """获取缓存统计信息"""
    try:
        conn = _get_conn()
        total = conn.execute('SELECT COUNT(*) as cnt FROM file_cache').fetchone()['cnt']
        rated = conn.execute(
            'SELECT COUNT(*) as cnt FROM file_cache WHERE rating IS NOT NULL'
        ).fetchone()['cnt']
        custom = conn.execute(
            'SELECT COUNT(*) as cnt FROM custom_text_cache WHERE text != \'\''
        ).fetchone()['cnt']
        return {'total_entries': total, 'with_rating': rated, 'custom_texts': custom}
    except Exception:
        return {'total_entries': 0, 'with_rating': 0, 'custom_texts': 0}


def get_cache_size_bytes() -> int:
    """获取缓存数据库文件物理大小（字节）"""
    try:
        db_path = Path(CACHE_DB_PATH)
        if db_path.exists():
            # 计算 .db + .db-wal + .db-shm
            total = db_path.stat().st_size
            for suffix in ('-wal', '-shm'):
                p = Path(str(db_path) + suffix)
                if p.exists():
                    total += p.stat().st_size
            return total
    except OSError:
        pass
    return 0


def get_cache_size_mb() -> float:
    """获取缓存数据库文件大小（MB）"""
    return round(get_cache_size_bytes() / (1024 * 1024), 2)


def enforce_cache_size_limit():
    """检查缓存大小，超过上限时删除最旧的记录"""
    size = get_cache_size_bytes()
    if size <= MAX_CACHE_SIZE:
        return

    try:
        conn = _get_conn()
        # 删除最旧的 20% 记录
        total = conn.execute('SELECT COUNT(*) as cnt FROM file_cache').fetchone()['cnt']
        to_delete = max(int(total * 0.2), 100)
        conn.execute('''
            DELETE FROM file_cache WHERE path IN (
                SELECT path FROM file_cache ORDER BY updated_at ASC LIMIT ?
            )
        ''', (to_delete,))
        conn.commit()
        logger.info(f'cache size limit reached ({size/1024/1024:.1f}MB), pruned {to_delete} oldest entries')
    except Exception as e:
        logger.error(f'cache enforce limit error: {e}')


# ---- 自定义文本（地点）缓存 ----

def get_custom_text(path: str) -> str | None:
    """读取指定文件的自定义文本"""
    try:
        conn = _get_conn()
        row = conn.execute(
            'SELECT text FROM custom_text_cache WHERE path = ?',
            (path,)
        ).fetchone()
        return row['text'] if row else None
    except Exception as e:
        logger.debug(f'custom_text read error for {path}: {e}')
    return None


def get_all_custom_texts() -> dict[str, str]:
    """读取所有自定义文本，返回 {path: text}"""
    try:
        conn = _get_conn()
        rows = conn.execute(
            'SELECT path, text FROM custom_text_cache WHERE text != \'\''
        ).fetchall()
        return {row['path']: row['text'] for row in rows}
    except Exception as e:
        logger.debug(f'get_all_custom_texts error: {e}')
    return {}


def set_custom_text(path: str, text: str):
    """保存文件的自定义文本"""
    if not path:
        return
    try:
        conn = _get_conn()
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        if text:
            conn.execute(
                '''INSERT OR REPLACE INTO custom_text_cache (path, text, updated_at)
                   VALUES (?, ?, ?)''',
                (path, text, now)
            )
        else:
            # 空文本则删除记录
            conn.execute('DELETE FROM custom_text_cache WHERE path = ?', (path,))
        conn.commit()
    except Exception as e:
        logger.debug(f'custom_text write error for {path}: {e}')


def batch_set_custom_texts(data: dict[str, str]):
    """批量保存自定义文本"""
    if not data:
        return
    try:
        conn = _get_conn()
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        rows = [(path, text, now) for path, text in data.items() if text]
        empty = [path for path, text in data.items() if not text]
        if rows:
            conn.executemany(
                '''INSERT OR REPLACE INTO custom_text_cache (path, text, updated_at)
                   VALUES (?, ?, ?)''', rows
            )
        if empty:
            conn.executemany(
                'DELETE FROM custom_text_cache WHERE path = ?',
                [(p,) for p in empty]
            )
        conn.commit()
        logger.debug(f'custom_text batch: {len(rows)} saved, {len(empty)} deleted')
    except Exception as e:
        logger.error(f'custom_text batch error: {e}')


def clear_cache():
    """清空所有缓存数据"""
    try:
        conn = _get_conn()
        conn.execute('DELETE FROM file_cache')
        conn.commit()
        # 压缩数据库文件
        conn.execute('VACUUM')
        conn.commit()
        logger.info('file cache cleared and vacuumed')
    except Exception as e:
        logger.error(f'clear_cache error: {e}')


def clear_custom_texts():
    """Clear user custom location texts."""
    try:
        conn = _get_conn()
        conn.execute('DELETE FROM custom_text_cache')
        conn.commit()
        logger.info('custom texts cleared')
    except Exception as e:
        logger.error(f'clear_custom_texts error: {e}')
