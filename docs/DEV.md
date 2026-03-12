# 开发文档

## 架构

- **main.py**：CustomTkinter 窗口。选择文件夹、端口后，通过 `subprocess.Popen` 启动子进程执行 `file_server.run_server()`，环境变量 `FILE_SHARE_ROOT`、`FILE_SHARE_PORT` 传入根目录和端口；停止时对子进程 `terminate()` 并 `wait()`。
- **file_server.py**：Flask 应用。`/file`、`/file/` 返回 `templates/index.html`；`/file/api/list` 查询目录列表；`/file/api/download/<path>`、`/file/api/preview/<path>` 使用 **8MB 缓冲区**流式下发文件（`Response(generate(), direct_passthrough=True)`），以跑满局域网带宽；`/file/api/upload` 使用 **16MB 缓冲区**从请求流读入并写入磁盘；`/file/api/delete`（POST，JSON `{path}`）删除文件或递归删除目录；`/file/api/mkdir`（POST，JSON `{path, name}`）在指定父目录下新建文件夹。运行时会优先使用 **waitress**（默认 **32 线程**，可用环境变量 `FILE_SHARE_THREADS` 覆盖）、channel_timeout=14400、**max_request_body_size=20GB**）作为 WSGI 服务器，无 waitress 时回退到 Flask 内置多线程服务器。

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
- **同名文件处理（类 Windows 复制）**：上传时若目标目录已存在同名文件，服务端返回 `409` 且 `error: "file_exists"`；前端弹出「文件已存在」对话框，用户可选择：**覆盖**（带 `overwrite=1` 重试）、**跳过**（跳过该文件继续下一个）、**自动重命名**（带 `rename=1` 重试，服务端保存为 `文件名_YYYYMMDDHHMMSSmmm.后缀`，若仍冲突则加 `_2`、`_3`…）。弹窗 UI：警示图标、pill 展示文件名与目标目录、覆盖=警示按钮、跳过=次要、自动重命名=主按钮；点击遮罩视为跳过。
- **刷新列表**：页头搜索框右侧提供「刷新」按钮（强制拉取最新目录列表）。移动端支持“下拉刷新”：当页面滚动到顶部时向下滑动，提示条显示“下拉刷新/松手刷新”，松手后触发刷新，交互参考抖音顶部下拉刷新，兼顾美观与易用。
- **多人实时提醒（按目录）**：服务端提供 SSE 接口 `GET /file/api/events`。当任意用户上传、删除或新建文件夹成功后，会向所有已打开页面的客户端推送事件：
  - `type=refresh`
  - `dir`：发生变化的目录（`''` 表示根目录）
  - `action`：`upload | delete | mkdir`
  - `name`：变化的文件/文件夹名
  - `message`：用于页面轻提示的文案

  前端仅当 `dir` 与当前正在浏览的目录一致时自动刷新列表；若是其他目录发生变化，则仅提示“哪个目录更新了”，不打断当前浏览。

- **批量上传体验优化（为什么这样做）**：上传是逐文件请求，服务端会逐文件广播事件以保证可靠性；前端对同目录的 `refresh` 做合并/节流（例如 800ms 内多次更新只刷新一次），避免别的用户页面在批量上传图片时不停刷新造成卡顿与打扰，同时保证更新停止后会自动刷新到最新。

- **SSE 与 Waitress**：Waitress 会按 `send_bytes` 缓冲输出；已将 `send_bytes=4096`，且每条 SSE 消息（connected/refresh/ping）凑满 4KB（用注释行填充），以便流式响应及时发出；SSE 连接改为页面 `load` 后再建立，避免浏览器因长连接 pending 导致标签页一直显示“加载中”。

- **首屏加载**：去掉对 Google Fonts 的依赖，`body` 使用系统字体栈（如 PingFang SC、Microsoft YaHei），在无外网或弱网环境下首屏不再长时间卡在“加载中…”。

## 依赖版本

- flask >= 3.0.0
- customtkinter >= 5.2.0
- Pillow >= 10.0.0（customtkinter 可选依赖，用于图标等）
- waitress >= 3.0.0（生产用 WSGI，用于高吞吐；未安装时使用 Flask 内置服务器）
- qrcode >= 7.4.2（GUI 生成访问地址二维码）
