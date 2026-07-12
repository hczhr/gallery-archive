# Gallery

Gallery 是一个运行在 fnOS 上的本地图库，后端用 Rust 编写，以 fnOS 原生 FPK 包形式分发（无需 Docker）。它索引接入的媒体目录，按画师划分图库，并在每位画师下按标签与角色整理图片、视频和源文件。

## 功能

- **浏览与预览**：支持图片、视频（可预览并直接播放）、源文件、文本（`.txt`/`.md`/`.html`）以及压缩包的查看。
- **整理方式**：图库按画师划分（文件夹即画师）。选定一位画师后，可在其图库内按标签与角色浏览和筛选。
- **角色识别**：默认开启 AI 角色识别（基于 OpenVINO 在 GPU 上运行，亦支持 CPU）。画师识别默认关闭——文件夹本身已按画师划分，无需额外识别。
- **查重与路径监测**：基于内容哈希检测重复文件；监测媒体路径是否失效，对同画师范围内的明确移动自动重新关联，无法确定时标记出来待人工确认。
- **归档计划**：可将散落的内容归拢为归档计划，确认后再执行（执行前会先创建备份）。自动执行默认关闭，仅在开启开关且一次完整扫描成功完成后触发。
- **索引与文件归属**：索引存于 SQLite。媒体文件始终位于你授权的 fnOS 媒体目录内；整理与归档只在这些目录内移动文件，不会搬到授权范围之外。

服务默认监听 `8899` 端口，**不提供内置登录认证**。请仅在可信局域网内使用；如需暴露到公网，应在 fnOS 或反向代理层先加上认证。标签与画师搜索支持拼音。

## 安装

从 [Releases](https://github.com/hczhr/gallery-archive/releases) 下载编译好的 FPK，通过 fnOS 应用安装器安装。当前版本包名：

```text
gallery_1.0.175_x86_64.fpk
```

源码仓库**不包含**编译好的二进制、模型、数据库与缓存，这些需从 Releases 获取或自行构建。

## 构建

一键构建（先编译 Rust 运行时，再打包 FPK）：

```powershell
python tools/build_release.py --fnpack .\output\fnpack\fnpack-1.2.1-windows-amd64.exe
```

仅编译 Rust 运行时：

```powershell
cargo build --release --locked --manifest-path rust/gallery_accel/Cargo.toml
```

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `rust/gallery_accel/` | Rust 运行时与 Axum API |
| `app/static/` | 网页界面 |
| `fnpack/` | fnOS 安装包配置与启动脚本 |
| `tools/` | 构建 Rust、打包 FPK、生成公开源码树的工具 |
