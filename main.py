# -*- coding: utf-8 -*-
"""
局域网文件共享工具 - 主入口。
带窗口：设置端口、选择文件夹、显示本机 IP 与访问地址，启动/停止服务。
"""
import os
import socket
import sys
import webbrowser
import subprocess
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import customtkinter as ctk
from pathlib import Path
from typing import Tuple
from PIL import Image

_server_process = None
_log_file_path = None


def get_local_ip():
    from file_server import get_local_ip
    return get_local_ip()


def is_port_in_use(port: int) -> bool:
    """检测端口是否已被占用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.bind(("", port))
            return False
    except OSError:
        return True


def start_server(root_dir: str, port: int):
    global _server_process
    global _log_file_path
    if _server_process is not None and _server_process.poll() is None:
        return False, "服务已在运行"
    env = {**os.environ, "FILE_SHARE_ROOT": os.path.abspath(root_dir), "FILE_SHARE_PORT": str(int(port))}
    try:
        app_dir = _app_dir()
        logs_dir = os.path.join(app_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        _log_file_path = os.path.join(logs_dir, f"server_{port}.log")
        log_f = open(_log_file_path, "a", encoding="utf-8", buffering=1)
        if _is_frozen():
            cmd = [sys.executable, "--server"]
            cwd = app_dir
        else:
            cmd = [sys.executable, "-c", "import os; from file_server import run_server; run_server('0.0.0.0', int(os.environ['FILE_SHARE_PORT']), os.environ['FILE_SHARE_ROOT'])"]
            cwd = os.path.dirname(os.path.abspath(__file__))
        _server_process = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            stdout=log_f,
            stderr=log_f,
        )
    except Exception as e:
        return False, "启动失败: " + str(e)
    return True, None


def check_server_started(port: int) -> Tuple[bool, str]:
    """检查子进程是否仍在运行；若已退出则从日志返回错误信息。"""
    global _server_process
    global _log_file_path
    if _server_process is None:
        return False, "未启动"
    if _server_process.poll() is not None:
        # 进程已退出：从日志里取最后几行作为错误提示
        msg = ""
        if _log_file_path and os.path.exists(_log_file_path):
            try:
                with open(_log_file_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-20:]
                msg = "".join(lines).strip()
            except Exception:
                msg = ""
        if not msg:
            msg = "端口可能被占用或程序异常退出（详见 logs 目录日志）"
        return False, msg
    return True, None


def _can_connect(host: str, port: int, timeout_sec: float = 0.35) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_sec)
            s.connect((host, port))
        return True
    except OSError:
        return False


def stop_server():
    global _server_process
    if _server_process is not None and _server_process.poll() is None:
        try:
            _server_process.terminate()
        except Exception:
            pass
        # 关键：wait 可能超时，必须兜底强杀，否则端口会继续被占用
        try:
            _server_process.wait(timeout=2)
        except Exception:
            try:
                _server_process.kill()
            except Exception:
                pass
            try:
                _server_process.wait(timeout=2)
            except Exception:
                pass
    _server_process = None


def _server_running():
    return _server_process is not None and _server_process.poll() is None


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    app = ctk.CTk()
    app.title("局域网文件共享")
    app.geometry("720x460")
    app.minsize(640, 400)
    # 某些 Windows 环境下窗口会在后台创建但不前置，导致“看起来没出来”
    # 这里在启动后短暂置顶并激活一次，提升可见性（不改变常规交互）
    def _bring_to_front():
        try:
            app.deiconify()
            app.lift()
            app.focus_force()
            app.attributes("-topmost", True)
            app.after(180, lambda: app.attributes("-topmost", False))
        except Exception:
            pass
    app.after(120, _bring_to_front)

    # 变量
    folder_var = ctk.StringVar(value="未选择文件夹")
    port_var = ctk.StringVar(value="81")
    ip_var = ctk.StringVar(value="")
    status_var = ctk.StringVar(value="未启动")
    url_var = ctk.StringVar(value="")
    qr_hint_var = ctk.StringVar(value="启动后生成二维码")

    qr_ctk_image = None  # 保持引用，避免被回收导致不显示

    def refresh_ip():
        ip_var.set(get_local_ip())

    def choose_folder():
        path = ctk.filedialog.askdirectory(title="选择要共享的文件夹")
        if path:
            folder_var.set(path)
            refresh_ip()
            update_url()

    def update_url():
        ip = ip_var.get()
        port = port_var.get().strip() or "81"
        url_var.set(f"http://{ip}:{port}/file")

    def update_qr():
        nonlocal qr_ctk_image
        url = url_var.get().strip()
        if not url:
            qr_label.configure(image=None, text="无地址")
            qr_hint_var.set("启动后生成二维码")
            qr_ctk_image = None
            return
        try:
            import qrcode
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=6,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            img = img.resize((160, 160), Image.Resampling.LANCZOS)
            qr_ctk_image = ctk.CTkImage(light_image=img, dark_image=img, size=(160, 160))
            qr_label.configure(image=qr_ctk_image, text="")
            qr_hint_var.set("扫码打开访问地址")
        except Exception as e:
            qr_label.configure(image=None, text="二维码生成失败")
            qr_hint_var.set(str(e))
            qr_ctk_image = None

    def on_start():
        root_dir = folder_var.get()
        if root_dir == "未选择文件夹" or not root_dir:
            status_var.set("请先选择文件夹")
            return
        if not Path(root_dir).is_dir():
            status_var.set("所选路径不是有效目录")
            return
        try:
            port = int(port_var.get().strip() or "81")
        except ValueError:
            status_var.set("端口请输入数字")
            return
        if port <= 0 or port > 65535:
            status_var.set("端口应在 1～65535 之间")
            return
        if is_port_in_use(port):
            status_var.set("端口 %d 已被占用，请更换端口或关闭占用程序" % port)
            return
        ok, err = start_server(root_dir, port)
        if not ok:
            status_var.set(err or "启动失败")
            return
        set_status("正在启动…", kind="starting")
        btn_start.configure(state="disabled")
        btn_stop.configure(state="normal")
        entry_port.configure(state="disabled")
        btn_folder.configure(state="disabled")
        btn_open.configure(state="disabled")

        def verify_started(attempt: int = 1, max_attempts: int = 18):
            global _server_process
            alive, msg = check_server_started(port)
            if not alive:
                _server_process = None
                set_status("启动失败: " + (msg or "端口可能被占用"), kind="error")
                btn_start.configure(state="normal")
                btn_stop.configure(state="disabled")
                entry_port.configure(state="normal")
                btn_folder.configure(state="normal")
                btn_open.configure(state="disabled")
                return

            # 服务进程还在：轮询端口是否可连接（避免 waitress 启动较慢导致误报）
            ip = ip_var.get().strip() or "127.0.0.1"
            ok_local = _can_connect("127.0.0.1", port)
            ok_lan = (ip != "127.0.0.1") and _can_connect(ip, port)
            if ok_local or ok_lan:
                set_status("服务已启动", kind="running")
                update_url()
                update_qr()
                btn_open.configure(state="normal")
                return

            if attempt >= max_attempts:
                # 进程活着但端口一直不可连：给出明确提示，但不强制判定失败（用户可自行访问验证）
                set_status("服务进程已启动但端口暂不可连接（请稍后重试，或检查防火墙/端口占用）", kind="warning")
                return

            set_status(f"正在启动…({attempt}/{max_attempts})", kind="starting")
            app.after(350, lambda: verify_started(attempt + 1, max_attempts))
            return

        app.after(350, lambda: verify_started(1, 18))

    def on_stop():
        stop_server()
        # 端口释放在 Windows 上可能需要极短时间，给一个明确提示
        set_status("已停止（端口已释放）", kind="stopped")
        qr_label.configure(image=None, text="二维码")
        qr_hint_var.set("启动后生成二维码")
        btn_start.configure(state="normal")
        btn_stop.configure(state="disabled")
        entry_port.configure(state="normal")
        btn_folder.configure(state="normal")
        btn_open.configure(state="disabled")

    def open_browser():
        u = url_var.get()
        if u and _server_running():
            webbrowser.open(u)

    # 布局
    pad = 20
    row = 0

    title = ctk.CTkLabel(app, text="局域网文件共享", font=ctk.CTkFont(size=22, weight="bold"))
    title.grid(row=row, column=0, columnspan=4, pady=(pad, 16), padx=pad, sticky="w")
    row += 1

    ctk.CTkLabel(app, text="共享文件夹:").grid(row=row, column=0, pady=8, padx=(pad, 8), sticky="w")
    ctk.CTkLabel(app, textvariable=folder_var, text_color="gray").grid(row=row, column=1, pady=8, padx=8, sticky="ew")
    btn_folder = ctk.CTkButton(app, text="选择文件夹", width=100, command=choose_folder)
    btn_folder.grid(row=row, column=2, pady=8, padx=(8, pad))
    # 右侧二维码区域（跨多行）
    qr_frame = ctk.CTkFrame(app, corner_radius=12)
    qr_frame.grid(row=row, column=3, rowspan=6, padx=(0, pad), pady=8, sticky="nsew")
    qr_title = ctk.CTkLabel(qr_frame, text="扫码访问", font=ctk.CTkFont(size=14, weight="bold"))
    qr_title.pack(pady=(12, 6), padx=12)
    qr_label = ctk.CTkLabel(qr_frame, text="二维码", width=160, height=160)
    qr_label.pack(pady=(6, 6), padx=12)
    qr_hint = ctk.CTkLabel(qr_frame, textvariable=qr_hint_var, text_color="gray", wraplength=180, justify="center")
    qr_hint.pack(pady=(0, 12), padx=12)
    row += 1

    ctk.CTkLabel(app, text="端口:").grid(row=row, column=0, pady=8, padx=(pad, 8), sticky="w")
    entry_port = ctk.CTkEntry(app, textvariable=port_var, width=80)
    entry_port.grid(row=row, column=1, pady=8, padx=8, sticky="w")
    ctk.CTkLabel(app, text="本机 IP:").grid(row=row, column=1, pady=8, padx=(120, 8), sticky="w")
    ctk.CTkLabel(app, textvariable=ip_var).grid(row=row, column=2, pady=8, padx=(8, pad), sticky="w")
    row += 1

    ctk.CTkLabel(app, text="访问地址:").grid(row=row, column=0, pady=8, padx=(pad, 8), sticky="w")
    ctk.CTkLabel(app, textvariable=url_var, text_color="#6366f1", font=ctk.CTkFont(weight="bold")).grid(row=row, column=1, columnspan=2, pady=8, padx=8, sticky="w")
    row += 1

    def set_status(text: str, kind: str = "info"):
        status_var.set(text)
        # 颜色：未启动/停止=灰，启动中=绿，已启动=绿，警告=黄，错误=红
        color_map = {
            "info": "gray",
            "stopped": "gray",
            "starting": "#22c55e",
            "running": "#22c55e",
            "warning": "#f59e0b",
            "error": "#ef4444",
        }
        try:
            status_label.configure(text_color=color_map.get(kind, "gray"))
        except Exception:
            pass

    ctk.CTkLabel(app, text="状态:").grid(row=row, column=0, pady=8, padx=(pad, 8), sticky="w")
    status_label = ctk.CTkLabel(app, textvariable=status_var, text_color="gray")
    status_label.grid(row=row, column=1, pady=8, padx=8, sticky="w")
    row += 1

    btn_frame = ctk.CTkFrame(app, fg_color="transparent")
    btn_frame.grid(row=row, column=0, columnspan=3, pady=(24, 12), padx=pad, sticky="w")
    btn_start = ctk.CTkButton(btn_frame, text="启动服务", width=120, command=on_start)
    btn_start.pack(side="left", padx=(0, 12))
    btn_stop = ctk.CTkButton(btn_frame, text="停止服务", width=120, command=on_stop, state="disabled")
    btn_stop.pack(side="left", padx=(0, 12))
    btn_open = ctk.CTkButton(btn_frame, text="在浏览器中打开", width=120, command=open_browser, state="disabled")
    btn_open.pack(side="left")
    row += 1

    tip = ctk.CTkLabel(
        app,
        text="同一局域网内的设备可通过上述地址访问、下载和上传文件；\n支持中文路径与中文文件名。",
        font=ctk.CTkFont(size=12),
        text_color="gray",
        justify="left",
    )
    tip.grid(row=row, column=0, columnspan=4, pady=(8, pad), padx=pad, sticky="w")

    app.grid_columnconfigure(1, weight=1)
    app.grid_columnconfigure(3, weight=0, minsize=220)
    refresh_ip()
    update_url()
    set_status("未启动", kind="stopped")

    def on_close():
        # 窗口关闭时也必须停服，避免后台子进程继续占用端口
        try:
            stop_server()
        finally:
            app.destroy()

    app.protocol("WM_DELETE_WINDOW", on_close)
    app.mainloop()


def _is_frozen():
    return getattr(sys, "frozen", False)


def _app_dir():
    """打包后为 exe 所在目录，否则为项目目录。"""
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


if __name__ == "__main__":
    if "--server" in sys.argv:
        # 打包后子进程用「exe --server」启动，仅运行文件服务后退出
        import os as _os
        root = _os.environ.get("FILE_SHARE_ROOT", "")
        port = int(_os.environ.get("FILE_SHARE_PORT", "81"))
        if root and port:
            from file_server import run_server
            run_server("0.0.0.0", port, root)
        sys.exit(0)
    main()
