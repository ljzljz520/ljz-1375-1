"""资源文件处理：上传保存、最小缩略图派生（纯标准库 PNG 编码）。"""
from __future__ import annotations

import base64
import binascii
import struct
import zlib
from pathlib import Path

from . import config

_TOMBSTONE = b"RETRACTED\n"


def save_raw(kind: str, entity_id: int, filename: str, data: bytes) -> str:
    d = config.ASSET_DIR / kind / str(entity_id)
    d.mkdir(parents=True, exist_ok=True)
    safe = Path(filename).name.replace("..", "_") or "asset.bin"
    p = d / safe
    p.write_bytes(data)
    # 逻辑路径相对“资产根”，不含 assets/ 前缀；发布器统一映射到站点 assets/ 下
    return str(p.relative_to(config.ASSET_DIR))


def abs_path(rel: str | None) -> Path | None:
    if not rel:
        return None
    # 逻辑路径相对资产根；兼容历史上可能带 assets/ 前缀的数据
    if rel.startswith("assets/"):
        return config.DATA_DIR / rel
    return config.ASSET_DIR / rel


def derive_thumbnail(src_rel: str | None, photo_id: int, size: int = 16) -> str | None:
    """为照片生成一张占位缩略图（不依赖第三方图像库）。

    真实系统应解码原图；此处生成确定性 16x16 PNG。缩略图是“派生资源”，
    与原件一样登记，撤稿时一并纳入任务。
    """
    if not src_rel:
        return None
    out_dir = config.ASSET_DIR / "photos" / str(photo_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "thumb.png"
    out.write_bytes(_placeholder_png(size))
    return str(out.relative_to(config.ASSET_DIR))


def _placeholder_png(size: int) -> bytes:
    # 灰度渐变占位
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0
        for x in range(size):
            v = (x * 17 + y * 13) % 256
            raw += bytes((v, v, v))
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def write_tombstone(path: Path) -> None:
    """撤稿资源用墓碑替换（保留存在性，便于离线页显示“已撤稿”而非坏链）。"""
    try:
        path.write_bytes(_TOMBSTONE)
    except OSError:
        pass


def decode_upload(b64_or_raw: str, content_transfer: str) -> bytes:
    if content_transfer == "base64":
        return base64.b64decode(b64_or_raw)
    if isinstance(b64_or_raw, str):
        return b64_or_raw.encode("latin-1", errors="replace")
    return bytes(b64_or_raw)
