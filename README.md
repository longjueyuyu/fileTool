# 局域网文件共享工具

在局域网内像 nginx 一样通过网页访问指定文件夹，支持浏览、下载、预览/播放和上传；**完整支持 Windows 中文路径与中文文件名**。

## 功能

- **桌面端**：启动时选择文件夹、设置端口，自动显示本机局域网 IP 和访问地址（如 `http://192.168.1.35:81/file`）。
- **扫码访问**：启动成功后自动生成二维码，局域网手机可直接扫码打开访问地址。
- **网页端**：类似 nginx 目录列表，可浏览子目录、下载文件、上传到当前目录；图片/音视频/PDF 支持“预览”在浏览器中打开。
- **中文路径**：从选择目录到 URL 编解码、读写文件，全程使用 UTF-8，避免 Windows 下中文乱码或访问失败。

## 运行方式

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动程序

```bash
python main.py
```

在窗口中：

1. 点击「选择文件夹」选择要共享的目录（如 `D:\work\厦门`）。
2. 如需修改端口，在「端口」一栏输入（默认 81）。
3. 点击「启动服务」。
4. 将显示的「访问地址」发给局域网内其他设备，即可在浏览器中访问、下载和上传。

## 技术说明

- **GUI**：CustomTkinter（深色主题）。
- **HTTP 服务**：Flask，子进程方式启动，便于在界面中“停止服务”时真正结束进程。
- **中文路径**：URL 使用 UTF-8 的 `quote`/`unquote`，本地使用 `pathlib.Path`，不做额外编码转换即可在 Windows 上正确读写中文路径。

## 打包成 exe 发给客户

在项目根目录执行：

```bash
pip install -r requirements-build.txt
pyinstaller main.spec --noconfirm --clean
```

或直接双击 `build_exe.bat`（会先安装依赖再打包）。

打包完成后，在 **`dist/`** 目录下会生成 **「局域网文件共享.exe」**（单文件），将该 exe 发给客户即可，无需安装 Python。客户首次运行后，日志会写在 exe 所在目录下的 `logs/` 文件夹。

## 项目结构

```
file_dir/
├── main.py              # 入口：GUI + 子进程启动/停止服务
├── main.spec            # PyInstaller 打包配置
├── build_exe.bat        # 一键打包脚本
├── file_server.py       # Flask 服务：列表 / 下载 / 预览 / 上传
├── templates/
│   └── index.html       # 网页文件列表与上传界面
├── requirements.txt
├── requirements-build.txt  # 打包用：pyinstaller
├── README.md
└── docs/
    └── DEV.md           # 开发与修改说明
```

## 安全提示

- 仅在可信局域网内使用；服务监听 `0.0.0.0`，同一网段内均可访问。
- 上传会直接写入所选目录，请勿共享系统关键目录。
