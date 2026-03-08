# 开发文档

## 架构

- **main.py**：CustomTkinter 窗口。选择文件夹、端口后，通过 `subprocess.Popen` 启动子进程执行 `file_server.run_server()`，环境变量 `FILE_SHARE_ROOT`、`FILE_SHARE_PORT` 传入根目录和端口；停止时对子进程 `terminate()` 并 `wait()`。
- **file_server.py**：Flask 应用。`/file`、`/file/` 返回 `templates/index.html`；`/file/api/list` 查询目录列表；`/file/api/download/<path>`、`/file/api/preview/<path>` 使用 **8MB 缓冲区**流式下发文件（`Response(generate(), direct_passthrough=True)`），以跑满局域网带宽；`/file/api/upload` 使用 **16MB 缓冲区**从请求流读入并写入磁盘；`/file/api/delete`（POST，JSON `{path}`）删除文件或递归删除目录；`/file/api/mkdir`（POST，JSON `{path, name}`）在指定父目录下新建文件夹。运行时会优先使用 **waitress**（8 线程、channel_timeout=14400、**max_request_body_size=20GB**）作为 WSGI 服务器，无 waitress 时回退到 Flask 内置多线程服务器。

## 中文路径处理（为什么能工作）

1. **GUI 选目录**：`ctk.filedialog.askdirectory()` 返回的已是 Unicode 字符串（如 `D:\work\厦门`），直接传给子进程环境变量（当前环境为 UTF-8 时即可）。
2. **URL ↔ 本地路径**：列表接口返回的 `path` 使用 `urllib.parse.quote(part, safe="")` 对每一级名称编码；前端请求列表时用 `encodeURIComponent(path)`；下载/预览/上传时 URL 中的路径由 Flask 解码为 Unicode，再经 `unquote(..., encoding="utf-8")` 得到中文等字符，最后用 `Path` 与 `ROOT_DIR` 拼接，在 Windows 上使用 Unicode API 访问文件。
3. **上传文件名**：保留用户提供的文件名（含中文），仅用 `secure_filename` 过滤 `\`、`/` 等危险字符；保存时使用 `pathlib.Path` 与 `str(dest)` 写入，避免编码问题。

## 修改与扩展

- **单文件上传大小**：应用层不限制（`MAX_CONTENT_LENGTH = None`）。**Waitress 默认请求体上限为 1GB**，超过会直接拒绝导致上传失败；已在 `run_server()` 中设置 `max_request_body_size=20*1024**3`（20GB），支持 >1G 大文件。若需更大可改为更大整数或查阅 waitress 文档使用 `0`（部分环境可能异常）。
- **上传/下载缓冲区**：`UPLOAD_BUFFER_SIZE`（32MB）、`DOWNLOAD_BUFFER_SIZE`（16MB）。上传采用**读/写分离**（请求线程读流入队、写线程从队列落盘），重叠网络接收与磁盘写入，减轻“100% 后还要等很久才刷新列表”并提高本机/局域网上传速度；`UPLOAD_QUEUE_SIZE` 为队列块数。
- **上传进度 100% 提示**：前端在进度达约 100% 时显示“已发送完毕，正在等待服务器写入…”，避免用户误以为卡住（服务端仍在从内核缓冲读入并落盘）。
- **预览类型**：在 `templates/index.html` 中调整 `isPreviewable()` 的正则。
- **搜索**：网页端搜索框支持“文件名包含”、“`*.后缀`”与 `*` 通配符匹配，仅过滤当前目录列表，不影响真实文件结构。
- **界面样式**：`templates/index.html` 内联 CSS 变量（`:root`）和表格/按钮样式；GUI 在 `main.py` 中布局与 `ctk.CTk*` 组件。
- **上传错误展示**：上传失败时除状态行外，会在上传区下方显示**持久错误框**（`#uploadErrorBox`），展示服务端返回的 `error` 或友好文案，用户点击「关闭」后隐藏，避免错误一闪而过看不到。
- **删除**：列表每行操作列有「删除」链接，请求 `POST /file/api/delete`（body `{path}`）；删除前弹窗确认，目录会递归删除。关闭预览弹窗时前端会清空 video/audio/img/iframe 的 src 并 pause/load，使浏览器断开预览请求，服务端随即释放文件句柄，避免 Windows 下“文件被占用”导致无法删除，且不影响预览时的流式播放性能。
- **新建文件夹**：页头「新建文件夹」按钮，输入名称后请求 `POST /file/api/mkdir`（body `{path, name}`），成功后刷新当前目录。
- **上传到指定目录**：上传区显示「上传到：当前目录」及「选择目录」；点击「选择目录」打开弹窗，可浏览并选择任意子目录作为上传目标，确认后后续上传将写入该目录；「使用当前目录」恢复为当前浏览目录。
- **上传中取消**：上传进行时显示「取消上传」按钮，点击后中止当前请求并停止队列，状态与错误框显示「已取消上传」。

## 依赖版本

- flask >= 3.0.0
- customtkinter >= 5.2.0
- Pillow >= 10.0.0（customtkinter 可选依赖，用于图标等）
- waitress >= 3.0.0（生产用 WSGI，用于高吞吐；未安装时使用 Flask 内置服务器）
- qrcode >= 7.4.2（GUI 生成访问地址二维码）
