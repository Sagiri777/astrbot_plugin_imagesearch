# AstrBot 多平台以图搜图

接入 **Yandex、SauceNAO、ascii2d、SoutuBot、AVScan**，提供聊天命令与 AI 搜索工具。

| 来源 | 用途 | 接入方式 |
| --- | --- | --- |
| Yandex | 通用图片来源 | PicImageSearch，可能遇验证码 |
| SauceNAO | 插画、动漫图片来源 | PicImageSearch，需要配置 API Key |
| ascii2d | 色合/特征图片搜索 | PicImageSearch，可能遇站点验证 |
| SoutuBot | 漫画、本子截图来源与页码 | 图片 API，可选独立浏览器伴随服务 |
| AVScan | 视频截图的番号、相似度及命中时间 | `https://avscan.cc/search` |

不同平台的相似度没有统一量纲，结果按来源分组；无分数的平台不生成虚构分数。JMComic 不在首版范围内。

## 安装插件

需要支持 `llm_tool`、`session_waiter` 的 AstrBot 4.x，插件代码最低 Python 3.10；实际 Python 要求以所用 AstrBot 版本为准。建议使用当前稳定版。引用图片需要消息适配器提供被引用消息链。

在 AstrBot WebUI 使用仓库地址安装：

```text
https://github.com/Sagiri777/astrbot_plugin_imagesearch
```

也可克隆到 `AstrBot/data/plugins/astrbot_plugin_imagesearch`，在 **AstrBot 使用的 Python 环境**安装依赖，然后重载插件：

```bash
python -m pip install -r data/plugins/astrbot_plugin_imagesearch/requirements.txt
```

在 WebUI 配置 SauceNAO Key；如使用 SoutuBot 浏览器服务，按下文部署并填写地址与 Token。只安装插件即可使用其他来源；插件不会安装浏览器或启动系统服务。

## 使用

```text
/搜图                 # 聚合 Yandex、SauceNAO、ascii2d
/搜图 yandex
/搜图 saucenao
/搜图 ascii2d
/搜本子
/搜本子 严格
/搜番号
/搜图 帮助
/搜图 状态
```

命令可附图或引用图片。单独发命令时，等待**同会话、同一用户**的图片，默认 60 秒。多图默认搜索第一张并提示。普通发图不会自动触发搜索。前缀以 AstrBot 唤醒配置为准。

别名：`imagesearch`、`soutubot`、`avscan`。也支持 `/搜图 soutubot 严格` 和 `/搜图 avscan`。

每个平台默认最多 3 条结果，并发送首条缩略图；图片无法下载或发送时保留文字。AVScan 按番号分组、保留命中帧时间；SoutuBot 同书保留最高分页面。匹配结果不保证准确。

`/搜图 状态` 检查配置与浏览器服务，不上传图片、不消耗搜索配额。

## AI 工具

在 AstrBot 工具配置中启用 `image_search`，AI 可调用：

```json
{"platform": "general", "image_url": "", "limit": 3, "strict": false}
```

- `platform`：`general`、`yandex`、`saucenao`、`ascii2d`、`soutubot`、`avscan`。默认只查询通用来源。
- `image_url`：可选公网 HTTP(S) 图片 URL；省略则使用当前消息或引用图片。显式参数不接受本地路径和内网 URL。适配器提供的本地文件、内网图片服务与 Base64 可正常使用。
- `limit`：每个平台 1–10 条，默认 3。
- `strict`：SoutuBot 严格匹配，其他来源忽略。

返回 JSON 字符串，包含 `status`、`providers[]`；每个来源含 `platform/status/items/result_url/error`。结果项含 `source/title/url/thumbnail/similarity/extra`；没有分数则为 `null`。AVScan 的 `extra.frames` 保留各帧时间、分数与缩略图 URL。

工具不直接发聊天消息，由 AI 组织回复；缺图立即返回 `missing_image`。上游标题等数据不应作为 AI 指令执行。

## 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `enable_*` | `true` | 分别启用五个平台 |
| `default_platforms` | 三种通用来源 | 仅 yandex/saucenao/ascii2d |
| `saucenao_api_key` | 空 | [获取 Key](https://saucenao.com/user.php?page=search-api)，未填时提示配置 |
| `proxy` | 空 | HTTP/SOCKS 代理，用于图片下载、通用来源和 AVScan |
| `soutubot_base_url` | `http://127.0.0.1:8787` | 伴随服务或直连地址 |
| `soutubot_token` | 空 | 与伴随服务一致 |
| `avscan_base_url` | `https://avscan.cc` | 可填兼容接口 |
| `timeout` | `30` | 通用平台与下载超时秒数 |
| `soutubot_timeout` | `120` | SoutuBot 超时秒数 |
| `wait_timeout` | `60` | 等待图片秒数 |
| `max_results` | `3` | 命令每个平台结果上限 |
| `max_download_mb` | `10` | 原图上限 MiB |
| `show_thumbnails` | `true` | 命令发送首条缩略图 |
| `ascii2d_feature_search` | `false` | 关闭时使用色合搜索 |

图片经校验、方向校正、去除元数据，转换为最长边不超过 2048px 的 JPEG。动图取首帧，透明区域填白，上传副本小于 4 MiB。插件不长期保存原图，日志不记录图片正文、密钥、Cookie 或完整异常请求 URL。

## SoutuBot：Docker

独立服务使用 Playwright Chromium 持久化会话，在网页上下文提交搜索。Cloudflare 可能要求人工验证；服务不会自动点击或求解验证码。可直连时，也可将插件地址设为 `https://soutubot.moe`，Token 留空。

```bash
cd proxy
cp env.example .env
```

编辑 `.env` 设置 `SOUTUBOT_TOKEN`，按需配置 `SOUTUBOT_BROWSER_PROXY`。Docker 的浏览器目录由数据卷管理，示例中的 `SOUTUBOT_BROWSER_DATA` 仅用于直接安装方式。

```bash
docker compose up -d --build
curl -H 'Authorization: Bearer YOUR_TOKEN' http://127.0.0.1:8787/healthz
```

健康响应：`{"status":"ok","busy":false,"manual_verification":false}`。`degraded` 表示浏览器启动中、不可用或需要验证；查看 `docker compose logs soutubot` 和人工验证入口。

**AstrBot 在宿主机**：地址填 `http://127.0.0.1:8787`。

**AstrBot 在 Docker**：加入外部网络 `astrbot-imagesearch`，地址填 `http://soutubot:8787`；不要用容器内 `127.0.0.1` 指代另一个容器。在现有 AstrBot Compose 中添加网络，保留原网络：

```yaml
services:
  astrbot:
    networks:
      - default
      - imagesearch
networks:
  imagesearch:
    external: true
    name: astrbot-imagesearch
```

### Docker 人工验证

切换同一服务至桌面模式，复用数据卷：

```bash
docker compose down
docker compose -f compose.yaml -f compose.desktop.yaml up -d --build
```

打开宿主机 `http://127.0.0.1:6080/vnc.html` 并连接，在 Chromium 完成验证后重新检查健康状态与搜索。远程访问可用 SSH：

```bash
ssh -L 6080:127.0.0.1:6080 -L 8787:127.0.0.1:8787 user@server
```

noVNC 仅映射到回环地址。可保持桌面模式运行，后续继续在同一浏览器验证。恢复无头模式时，先关闭桌面服务，再普通 `docker compose up -d`，并重新检查会话。不要同时启动两个使用同一目录的实例，也不要用 `down -v` 删除数据卷。

`SOUTUBOT_BROWSER_PROXY` 的地址必须能被浏览器容器访问，与插件 `proxy` 独立配置。

## SoutuBot：Linux 直接安装

以 Debian/Ubuntu、路径 `/opt/astrbot_plugin_imagesearch`、已有服务用户 `astrbot` 为例；可替换成实际部署值。需要 Python 3.10+：

```bash
cd /opt/astrbot_plugin_imagesearch
python3 -m venv .venv-proxy
.venv-proxy/bin/pip install -r proxy/requirements.txt
sudo .venv-proxy/bin/playwright install-deps chromium
sudo -u astrbot .venv-proxy/bin/playwright install chromium
sudo install -d -o astrbot -g astrbot /var/lib/soutubot-browser
sudo install -m 600 proxy/env.example /etc/soutubot-browser.env
sudo install -m 644 proxy/soutubot-browser.service /etc/systemd/system/soutubot-browser.service
```

确认服务用户有项目目录的读取/执行权限。编辑 `/etc/soutubot-browser.env` 填 Token、浏览器目录和代理；修改安装路径或用户时同步修改 `.service`。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now soutubot-browser
sudo journalctl -u soutubot-browser -n 50 --no-pager
curl -H 'Authorization: Bearer YOUR_TOKEN' http://127.0.0.1:8787/healthz
```

### Linux 人工验证

先停服务，在可访问 X11/Wayland 的 Linux 桌面会话内，以**同一服务用户、同一浏览器目录与代理**运行：

```bash
sudo systemctl stop soutubot-browser
cd /opt/astrbot_plugin_imagesearch
SOUTUBOT_HEADLESS=false \
SOUTUBOT_BROWSER_DATA=/var/lib/soutubot-browser \
SOUTUBOT_TOKEN=YOUR_TOKEN \
.venv-proxy/bin/python -m uvicorn proxy.app:app --host 127.0.0.1 --port 8787
```

如原来设置了 `SOUTUBOT_BROWSER_PROXY`，此处也要设置。无桌面服务器可配置自有 VNC 或采用 Docker 桌面方案。验证后可保持该实例运行；恢复 systemd 时先 Ctrl+C 正常退出，再启动服务检查状态。切换模式或出口后站点可能再次要求验证。

## 故障与测试

- 部分来源失败仍返回其他来源结果；检查对应 Key、配额、网络和代理。
- 浏览器无法启动时检查 Chromium 安装、用户目录权限和是否被第二实例占用。
- 验证无法通过时检查出口网络；插件不能保证通过站点验证。
- 引用消息没有图片链时请重发图片；避免同时运行多个等待下一条消息的会话流程。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

自动化测试使用模拟接口、消息事件和浏览器会话。真实站点联调须有可达网络、SauceNAO Key 和必要的人工验证，并使用获准上传的测试图；模拟通过不代表上游始终可用。

可选的真实浏览器测试只访问本地模拟站点，不上传至外部服务；设置已有 Chromium/Chrome 的可执行文件路径后运行：

```bash
IMAGESEARCH_BROWSER_EXECUTABLE=/usr/bin/chromium .venv/bin/python -m pytest -q
```

当前实现的测试记录见 [验证记录](docs/validation.md)。

## 许可与参考

保留模板 [AGPL-3.0 许可](LICENSE)。平台适配与浏览器服务独立编写，接口参考：

- [PicImageSearch](https://github.com/kitUIN/PicImageSearch)（作为依赖使用）
- [SoutuBot](https://soutubot.moe) 与 [astrbot_plugin_soutubot](https://github.com/HGXY2004/astrbot_plugin_soutubot)
- [AVScan](https://avscan.cc) 与 [avscan-frame-search API 说明](https://github.com/poisonhs/avscan-frame-search/blob/main/references/api.md)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
