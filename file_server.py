# -*- coding: utf-8 -*-
"""
局域网文件服务模块。
提供指定目录的浏览、下载、上传，正确处理 Windows 中文路径。
上传/下载使用大缓冲区流式传输，充分利用局域网带宽。
"""
import logging
import mimetypes
import os
import shutil
import sys
import threading
import traceback
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import quote as url_quote, unquote
from flask import Flask, request, Response, jsonify, abort
from werkzeug.utils import secure_filename

logger = logging.getLogger("file_share")
if not logger.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

# 传输缓冲区大小：大块 I/O 减少系统调用，跑满局域网带宽（千兆约 125MB/s）
DOWNLOAD_BUFFER_SIZE = 16 * 1024 * 1024   # 16MB 下载
UPLOAD_BUFFER_SIZE = 32 * 1024 * 1024     # 32MB 上传单块；读/写分离后可与落盘并行拉满带宽
UPLOAD_QUEUE_SIZE = 4                      # 读线程与写线程之间缓冲块数，重叠 I/O
TEXT_PREVIEW_MAX_BYTES = 512 * 1024       # 512KB 文本预览上限，避免大文件卡死

# 确保控制台与文件路径使用 UTF-8（Windows 中文路径）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = None  # 不限制单文件大小，支持几十 GB 视频/游戏压缩包等


@app.errorhandler(500)
def handle_500(e):
    """API 返回 500 时统一为 JSON，便于前端显示；同时打日志。"""
    tb = traceback.format_exc()
    logger.error("Server error: %s\n%s", e, tb)
    err_msg = str(e) if e else "服务器内部错误"
    if "413" in err_msg or "Request Entity Too Large" in err_msg:
        err_msg = "请求体过大，请检查代理或浏览器限制；本服务不限制大小。"
    if "No space left" in err_msg or "errno 28" in err_msg:
        err_msg = "磁盘空间不足（系统临时目录或目标盘已满），请清理后重试。"
    return jsonify({"error": err_msg}), 500


# 由 GUI 启动时注入
ROOT_DIR: Path = None


def set_root_dir(path: str):
    """设置共享根目录（支持中文路径）。"""
    global ROOT_DIR
    ROOT_DIR = Path(path).resolve()


def _safe_relative_path(url_path: str) -> Path:
    """
    将 URL 路径解码为相对路径并做安全校验，防止目录穿越。
    使用 UTF-8 解码以支持中文等字符。
    """
    if not url_path or url_path == "/":
        return Path(".")
    # 去掉前导斜杠并解码
    decoded = unquote(url_path.lstrip("/"), encoding="utf-8")
    # 替换 URL 中的 / 为当前系统的路径分隔
    parts = decoded.replace("/", os.sep).split(os.sep)
    resolved = Path(".")
    for p in parts:
        if not p or p == ".":
            continue
        if p == "..":
            resolved = resolved.parent
            if resolved == Path(".") or ".." in str(resolved):
                abort(403)
            continue
        resolved = resolved / p
    # 最终必须在根目录下
    try:
        resolved.resolve().relative_to(Path(".").resolve())
    except ValueError:
        abort(403)
    return resolved


def _path_to_url_segment(path: Path) -> str:
    """将本地路径转为 URL 路径段（UTF-8 编码）。"""
    from urllib.parse import quote
    parts = path.parts
    return "/".join(quote(p, safe="") for p in parts)


def _template_dir() -> Path:
    """打包为 exe 时模板在 sys._MEIPASS，否则在 file_server 同目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "templates"
    return Path(__file__).resolve().parent / "templates"


@app.route("/file")
@app.route("/file/")
def index():
    """文件列表首页由前端路由处理，这里返回 HTML。"""
    from flask import send_from_directory
    return send_from_directory(
        _template_dir(),
        "index.html",
        mimetype="text/html; charset=utf-8",
    )


@app.route("/file/api/list")
def list_dir():
    """列出目录内容，支持中文路径。用 scandir 减少 stat 调用，加快大目录刷新。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        return jsonify({"error": "未设置共享目录"}), 500
    raw_path = request.args.get("path", "")
    rel = _safe_relative_path(raw_path)
    full = (ROOT_DIR / rel).resolve()
    if not str(full).startswith(str(ROOT_DIR.resolve())):
        abort(403)
    if not full.exists():
        return jsonify({"error": "路径不存在"}), 404
    if not full.is_dir():
        return jsonify({"error": "不是目录"}), 400
    entries = []
    try:
        with os.scandir(full) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir is None:
                        is_dir = Path(entry.path).is_dir()
                    st = entry.stat(follow_symlinks=False)
                    name = entry.name
                    suffix = Path(name).suffix
                    rel_child = rel / name
                    entries.append({
                        "name": name,
                        "path": _path_to_url_segment(rel_child),
                        "path_decoded": rel_child.as_posix(),
                        "dir": is_dir,
                        "type": "文件夹" if is_dir else (suffix[1:].upper() if suffix else "无后缀"),
                        "size": st.st_size if not is_dir else None,
                        "ctime": getattr(st, "st_ctime", None),
                        "mtime": st.st_mtime,
                    })
                except OSError:
                    continue
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"path": raw_path, "entries": entries})


def _parse_range(range_header: str, total: int):
    """
    解析 Range 请求。
    返回：
    - (start, end) 或 [(start,end), ...]：单段/多段 bytes=...
    - "invalid"：Range 头存在但格式不支持
    - None：没有 Range 头
    """
    if not range_header:
        return None
    h = range_header.strip()
    if not h.lower().startswith("bytes="):
        return "invalid"
    spec = h[6:].strip()
    if "-" not in spec:
        return "invalid"

    def parse_one(part: str):
        part = part.strip()
        if "-" not in part:
            return None
        a, _, b = part.partition("-")
        if a == "" and b == "":
            return None
        try:
            if a == "" and b != "":
                # suffix range: bytes=-500 取最后 500 字节
                suffix = int(b)
                if suffix <= 0:
                    return None
                start = max(total - suffix, 0)
                end = total - 1
                return (start, end)
            start = int(a) if a else 0
            end = int(b) if b else total - 1
            if start < 0:
                return None
            if start >= total:
                return None
            if end >= total:
                end = total - 1
            if start > end:
                return None
            return (start, end)
        except ValueError:
            return None

    if "," in spec:
        parts = [p for p in spec.split(",") if p.strip()]
        # 浏览器拖拽进度时通常是 2 段左右；过多段直接判 invalid
        if len(parts) > 6:
            return "invalid"
        ranges = []
        for p in parts:
            r = parse_one(p)
            if r is None:
                return "invalid"
            ranges.append(r)
        return ranges

    r = parse_one(spec)
    return r if r is not None else "invalid"


def _content_type_for(full: Path) -> str:
    # Windows 某些环境下 mimetypes 对 mp4/webm 识别不稳定，补充常用映射
    mimetypes.add_type("video/mp4", ".mp4")
    mimetypes.add_type("video/webm", ".webm")
    mimetypes.add_type("audio/mpeg", ".mp3")
    mimetypes.add_type("audio/wav", ".wav")
    mimetypes.add_type("application/pdf", ".pdf")
    mt, _ = mimetypes.guess_type(str(full))
    return mt or "application/octet-stream"


def _content_disposition(disp: str, filename: str) -> str:
    # 同时提供 filename 与 filename*，避免部分浏览器兼容问题
    safe_ascii = filename.encode("ascii", "ignore").decode("ascii") or "download"
    return f'{disp}; filename="{safe_ascii}"; filename*=UTF-8\'\'{url_quote(filename, safe="")}'


def _send_file_with_range(full: Path, disp: str):
    """发送文件，支持 Range（单段/多段），用于视频拖拽进度等场景；大缓冲区以跑满局域网带宽。"""
    size = full.stat().st_size
    range_header = request.headers.get("Range") or ""
    range_spec = _parse_range(range_header, size)

    if range_spec == "invalid":
        res = Response(status=416)
        res.headers["Content-Range"] = f"bytes */{size}"
        res.headers["Accept-Ranges"] = "bytes"
        res.headers["Content-Type"] = _content_type_for(full)
        res.headers["Content-Disposition"] = _content_disposition(disp, full.name)
        return res

    if range_spec is None:
        start, end = 0, size - 1
        status = 200
        content_length = size
    elif isinstance(range_spec, list):
        # 多段 Range：返回 multipart/byteranges
        boundary = "RANGE_%s" % os.urandom(8).hex()
        ct = _content_type_for(full)

        def generate_multi():
            with open(full, "rb") as f:
                for (start, end) in range_spec:
                    yield (f"--{boundary}\r\n").encode("utf-8")
                    yield (f"Content-Type: {ct}\r\n").encode("utf-8")
                    yield (f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n").encode("utf-8")
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        read_size = min(DOWNLOAD_BUFFER_SIZE, remaining)
                        chunk = f.read(read_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
                    yield b"\r\n"
                yield (f"--{boundary}--\r\n").encode("utf-8")

        res = Response(generate_multi(), direct_passthrough=True, status=206)
        res.headers["Accept-Ranges"] = "bytes"
        res.headers["Content-Type"] = f"multipart/byteranges; boundary={boundary}"
        res.headers["Content-Disposition"] = _content_disposition(disp, full.name)
        return res
    else:
        start, end = range_spec
        status = 206
        content_length = end - start + 1

    def generate():
        with open(full, "rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                read_size = min(DOWNLOAD_BUFFER_SIZE, remaining)
                chunk = f.read(read_size)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    res = Response(generate(), direct_passthrough=True, status=status)
    res.headers["Accept-Ranges"] = "bytes"
    res.headers["Content-Type"] = _content_type_for(full)
    res.headers["Content-Disposition"] = _content_disposition(disp, full.name)
    res.headers["Content-Length"] = str(content_length)
    if status == 206:
        res.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return res


@app.route("/file/api/download/<path:subpath>")
def download(subpath):
    """下载文件（触发保存），支持中文路径。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        abort(500)
    rel = _safe_relative_path(subpath)
    full = (ROOT_DIR / rel).resolve()
    if not str(full).startswith(str(ROOT_DIR.resolve())) or not full.is_file():
        abort(404)
    return _send_file_with_range(full, disp="attachment")


@app.route("/file/api/preview/<path:subpath>")
def preview(subpath):
    """预览/播放文件（浏览器内嵌显示），支持 Range 请求以便视频/音频直接播放。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        abort(500)
    rel = _safe_relative_path(subpath)
    full = (ROOT_DIR / rel).resolve()
    if not str(full).startswith(str(ROOT_DIR.resolve())) or not full.is_file():
        abort(404)
    return _send_file_with_range(full, disp="inline")


@app.route("/file/api/upload", methods=["POST"])
def upload():
    """上传文件到当前目录（支持中文路径）；大文件流式写入，带日志与明确错误。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        return jsonify({"error": "未设置共享目录"}), 500
    raw_path = request.form.get("path", "")
    rel = _safe_relative_path(raw_path)
    full = (ROOT_DIR / rel).resolve()
    if not str(full).startswith(str(ROOT_DIR.resolve())):
        return jsonify({"error": "非法路径"}), 403
    if not full.exists() or not full.is_dir():
        return jsonify({"error": "目标目录不存在"}), 400
    if "file" not in request.files:
        return jsonify({"error": "没有选择文件"}), 400
    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"error": "未选择文件"}), 400
    name = f.filename
    if "\\" in name or "/" in name or name in (".", ".."):
        name = secure_filename(f.filename) or "upload"
    dest = full / name
    dest_tmp = full / (name + ".tmp")
    content_length = request.content_length
    logger.info("Upload start: name=%s path=%s content_length=%s dest=%s", name, raw_path, content_length, dest)

    def _upload_writer(q, out, written_ref, exc_ref):
        try:
            while True:
                chunk = q.get()
                if chunk is None:
                    return
                out.write(chunk)
                written_ref[0] += len(chunk)
        except Exception as e:
            exc_ref[0] = e

    def _is_client_abort(exc):
        if exc is None:
            return False
        if isinstance(exc, (ConnectionError, BrokenPipeError, ConnectionResetError)):
            return True
        if getattr(exc, "errno", None) in (10053, 10054, 104):  # Windows: 连接中止/重置; Linux: ECONNRESET
            return True
        msg = (str(exc) or "").lower()
        return "connection" in msg or "disconnect" in msg or "abort" in msg or "reset" in msg

    def _remove_partial():
        if dest_tmp.exists():
            try:
                dest_tmp.unlink()
                logger.info("Removed partial/temp file: %s", dest_tmp)
            except OSError:
                pass

    try:
        written_ref = [0]
        exc_ref = [None]
        last_log_mb = 0
        with open(dest_tmp, "wb") as out:
            q = Queue(maxsize=UPLOAD_QUEUE_SIZE)
            t = threading.Thread(target=_upload_writer, args=(q, out, written_ref, exc_ref))
            t.start()
            try:
                while True:
                    chunk = f.stream.read(UPLOAD_BUFFER_SIZE)
                    if not chunk:
                        break
                    q.put(chunk)
                    written_mb = written_ref[0] // (1024 * 1024)
                    if written_mb >= last_log_mb + 100:
                        logger.info("Upload progress: %s MB written", written_mb)
                        last_log_mb = written_mb
            finally:
                q.put(None)
                t.join()
        if exc_ref[0]:
            raise exc_ref[0]
        written = written_ref[0]
        dest_tmp.replace(dest)
        logger.info("Upload done: name=%s size=%s bytes (temp cleared)", name, written)
        return jsonify({"ok": True, "name": name})
    except OSError as e:
        _remove_partial()
        err = str(e)
        if getattr(e, "errno", None) == 28 or "No space left" in err or "errno 28" in err:
            err = "磁盘空间不足（系统临时目录或目标盘已满），请清理后重试。"
        else:
            err = "写入失败: " + err
        logger.exception("Upload OSError: %s", e)
        return jsonify({"error": err}), 500
    except Exception as e:
        _remove_partial()
        logger.exception("Upload exception: %s", e)
        return jsonify({"error": "上传异常: " + str(e)}), 500


@app.route("/file/api/delete", methods=["POST"])
def delete_path():
    """删除指定路径下的文件或目录（目录会递归删除）。请求体 JSON：{"path": "相对路径"}。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        return jsonify({"error": "未设置共享目录"}), 500
    data = request.get_json(silent=True) or {}
    raw_path = data.get("path", "")
    rel = _safe_relative_path(raw_path)
    if rel == Path(".") or str(rel) == ".":
        return jsonify({"error": "不能删除根目录"}), 403
    full = (ROOT_DIR / rel).resolve()
    if not str(full).startswith(str(ROOT_DIR.resolve())):
        return jsonify({"error": "非法路径"}), 403
    if not full.exists():
        return jsonify({"error": "路径不存在"}), 404
    try:
        if full.is_file():
            os.remove(full)
        elif full.is_dir():
            shutil.rmtree(full)
        else:
            return jsonify({"error": "不支持的类型"}), 400
        return jsonify({"ok": True})
    except OSError as e:
        logger.exception("Delete failed: %s", e)
        err = str(e)
        if "Permission denied" in err or "Access is denied" in err:
            err = "无权限删除（文件可能被占用或只读）"
        return jsonify({"error": err}), 500


@app.route("/file/api/mkdir", methods=["POST"])
def mkdir():
    """在指定父目录下新建文件夹。请求体 JSON：{"path": "父目录相对路径", "name": "文件夹名"}。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        return jsonify({"error": "未设置共享目录"}), 500
    data = request.get_json(silent=True) or {}
    raw_path = data.get("path", "")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "文件夹名不能为空"}), 400
    if "/" in name or "\\" in name or name in (".", ".."):
        return jsonify({"error": "文件夹名不能包含 / \\ 或 . .."}), 400
    rel = _safe_relative_path(raw_path)
    full_parent = (ROOT_DIR / rel).resolve()
    if not str(full_parent).startswith(str(ROOT_DIR.resolve())):
        return jsonify({"error": "非法路径"}), 403
    if not full_parent.exists() or not full_parent.is_dir():
        return jsonify({"error": "父目录不存在"}), 404
    new_dir = full_parent / name
    if new_dir.exists():
        return jsonify({"error": "已存在同名文件或文件夹"}), 400
    try:
        new_dir.mkdir(parents=False)
        return jsonify({"ok": True, "path": _path_to_url_segment(rel / name)})
    except OSError as e:
        logger.exception("Mkdir failed: %s", e)
        return jsonify({"error": "创建失败: " + str(e)}), 500


def _decode_text(data: bytes):
    """
    按固定顺序尝试解码，返回 (text, encoding_used)。
    不做“猜测”，只做确定性的尝试。
    """
    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16le", "utf-16be", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)"


@app.route("/file/api/text/<path:subpath>")
def text_preview(subpath):
    """文本预览：返回 UTF 文本内容（有上限），用于前端弹窗预览 txt/log/json 等。"""
    if ROOT_DIR is None or not ROOT_DIR.is_dir():
        abort(500)
    rel = _safe_relative_path(subpath)
    full = (ROOT_DIR / rel).resolve()
    if not str(full).startswith(str(ROOT_DIR.resolve())) or not full.is_file():
        abort(404)
    size = full.stat().st_size
    with open(full, "rb") as f:
        data = f.read(TEXT_PREVIEW_MAX_BYTES + 1)
    truncated = len(data) > TEXT_PREVIEW_MAX_BYTES
    if truncated:
        data = data[:TEXT_PREVIEW_MAX_BYTES]
    text, enc = _decode_text(data)
    return jsonify({
        "name": full.name,
        "encoding": enc,
        "truncated": truncated,
        "size": size,
        "text": text,
    })


def get_local_ip():
    """获取本机局域网 IP（优先返回 192.168/10/172 私网地址，避免 198.18 等虚拟网段）。"""
    import socket

    candidates = []
    # 1. 通过 UDP “拨号”方式获取首选出口 IP（可能是 VPN/虚拟网卡）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        candidates.append(ip)
    except Exception:
        pass

    # 2. 枚举主机名解析出的所有 IPv4，补充候选
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for _fam, _socktype, _proto, _canonname, sockaddr in infos:
            ip = sockaddr[0]
            if ip not in candidates:
                candidates.append(ip)
    except Exception:
        pass

    def _is_private(ip: str) -> bool:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        if ip.startswith("10."):
            return True
        if ip.startswith("192.168."):
            return True
        if ip.startswith("172."):
            try:
                second = int(parts[1])
            except ValueError:
                return False
            return 16 <= second <= 31
        return False

    def _is_valid(ip: str) -> bool:
        if ip.startswith("127."):
            return False
        if ip.startswith("169.254."):
            return False
        # 198.18/198.19 常用于测试/虚拟适配器，避免展示给用户
        if ip.startswith("198.18.") or ip.startswith("198.19."):
            return False
        if ip.startswith("0.") or ip.startswith("255."):
            return False
        return True

    # 3. 优先选择“私网 + 合法”的地址（如 192.168.2.x / 10.x / 172.16-31.x）
    for ip in candidates:
        if _is_private(ip) and _is_valid(ip):
            return ip

    # 4. 退而求其次：在候选中挑一个“合法但非回环/链路本地”的地址
    for ip in candidates:
        if _is_valid(ip):
            return ip

    # 5. 最后兜底：按主机名解析或返回 127.0.0.1
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if _is_valid(ip):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def run_server(host: str, port: int, root_dir: str):
    """在指定 host/port 运行，共享 root_dir。优先使用 waitress 以跑满局域网带宽。"""
    set_root_dir(root_dir)
    try:
        import waitress
        # 大 socket 发送/接收缓冲，配合应用层 16MB 缓冲跑满千兆 LAN
        waitress.serve(
            app,
            host=host,
            port=port,
            threads=8,
            channel_timeout=14400,   # 4 小时，支持几十 GB 大文件上传不断开
            max_request_body_size=20 * 1024**3,  # 20GB（默认 1GB 会拒绝 >1G 上传；0 在部分环境可能异常，用明确大值）
            send_bytes=256 * 1024,
            recv_bytes=512 * 1024,   # 512KB 每次 recv，大文件上传更吃满带宽
        )
    except ImportError:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
