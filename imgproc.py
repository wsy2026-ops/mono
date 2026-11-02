import os
import subprocess
import tempfile

try:
    from PIL import Image
except ImportError:
    Image = None

THUMB_DIR = "thumbs"
THUMB_LEVELS = (("s", 240), ("m", 800))
FULL_MAX_SIDE = 1920
MIN_BYTES = 8 * 1024
REJECT_BELOW = (16, 16)


def available():
    return Image is not None


def _ratio_box(im, box):
    w, h = im.size
    tw, th = box
    if w <= tw and h <= th:
        return im
    r = min(tw / float(w), th / float(h))
    return im.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)


def _webp(im):
    try:
        return im.convert("RGB")
    except Exception:
        return im


def _probe(path):
    if Image is None:
        return None
    try:
        with Image.open(path) as im:
            im.load()
            return im.size
    except Exception:
        return None


def _webp_size(path):
    if not shutil_which("cwebp"):
        return None
    try:
        r = subprocess.run(["cwebp", "-quiet", "-nofl", "-m", "4", "-q", "78", path, "-o", os.devnull],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def shutil_which(name):
    import shutil as _sh
    return _sh.which(name)


def process(path, rel_dir=""):
    """上传后处理：压长边 + 转 WebP + 生成两档缩略图。

    永远 fail-open —— Pillow 没装、格式不认识、压完更大，
    一律保留原文件并返回 None，绝不让处理失败变成上传失败。
    """
    if Image is None or not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size < MIN_BYTES:
        return None

    try:
        with Image.open(path) as probe:
            fmt = (probe.format or "").upper()
            w, h = probe.size
    except Exception:
        return None
    if fmt in ("GIF", "BMP"):
        return None
    if w < REJECT_BELOW[0] or h < REJECT_BELOW[1]:
        return None

    out = {"w": w, "h": h, "full": None, "thumbs": {}, "bytes_in": size, "bytes_out": size}
    try:
        stem = os.path.splitext(os.path.basename(path))[0]
        tdir = os.path.join(os.path.dirname(path), THUMB_DIR)
        os.makedirs(tdir, exist_ok=True)

        with Image.open(path) as im:
            im.load()
            base = _ratio_box(im, (FULL_MAX_SIDE, FULL_MAX_SIDE)) if max(im.size) > FULL_MAX_SIDE else im
            cand = os.path.join(os.path.dirname(path), stem + ".cand.webp")
            _webp(base).save(cand, "WEBP", quality=82, method=4)
            cand_size = os.path.getsize(cand)
            if cand_size and cand_size < size:
                final_webp = os.path.join(os.path.dirname(path), stem + ".webp")
                os.replace(cand, final_webp)
                out["full"] = stem + ".webp"
                out["bytes_out"] = cand_size
                src = Image.open(final_webp)
            else:
                os.remove(cand)
                out["bytes_out"] = size
                src = base

            try:
                for tag, box in THUMB_LEVELS:
                    t = _ratio_box(src, (box, box))
                    rel = os.path.join(THUMB_DIR, stem + "_" + tag + ".webp")
                    tp = os.path.join(os.path.dirname(path), rel)
                    _webp(t).save(tp, "WEBP", quality=76, method=4)
                    out["thumbs"][tag] = rel.replace("\\", "/")
                    out[tag + "_w"], out[tag + "_h"] = t.size
            finally:
                if src is not base:
                    src.close()
                for junk in (cand, path + ".orig"):
                    try:
                        if os.path.exists(junk):
                            os.remove(junk)
                    except OSError:
                        pass
        return out
    except Exception:
        return None
