# 验证记录

日期：2026-10-28。测试环境：macOS、Python 3.12.13、PicImageSearch 3.12.11、Playwright 1.58.0，使用本机已有 Google Chrome。

## 自动化验证

- 设置 `IMAGESEARCH_BROWSER_EXECUTABLE` 后运行完整测试：**41 passed**。
- 不设置浏览器路径时，真实浏览器测试自动跳过，其余测试无需访问外部站点。
- Ruff 检查与格式检查通过；Python 3.10 语法检查、JSON/YAML 解析、Shell 语法检查和 `git diff --check` 通过。
- 核对 Compose 的路径、回环端口映射、Token 健康检查，以及 Docker 镜像与 Playwright Python 包版本一致性。

测试覆盖：五个平台响应解析、SoutuBot 新旧结构和同书去重、AVScan 多帧分组、空结果、字段缺失、验证码、限流、维护状态、缺少 Key、聚合部分失败、超时、图片大小与格式、消息附图/引用/等待、用户与会话隔离、卸载清理、AI JSON 返回和工具注释解析、缩略图失败保留文字。

真实浏览器测试使用本地 HTTP 服务，验证 multipart 图片与搜索参数、浏览器 Cookie 发送、验证状态恢复、新浏览器实例加载持久化 Cookie。另有模拟测试验证取消请求时关闭浏览器页面，以终止浏览器内的上传并释放串行锁。

## AstrBot 接口核对

本地读取 AstrBot **4.29.3** 发布包，核对命令注册、工具注册、事件消息字符串及会话等待接口。使用 AstrBot 实际 `SessionWaiter` 实现验证消息触发和取消后的注册清理；使用其采用的 `docstring-parser` 验证四个工具参数的类型解析。

消息流程自动化测试使用 AstrBot 边界替身，当前未启动完整 AstrBot 或连接 QQ、Telegram 等真实消息适配器。

## 真实上游联调

使用自行生成的蓝色矩形、黄色圆形几何图片上传，未使用真实聊天图片或成人图片。结果只用于验证接口连通与解析，不用于评估检索准确率。

| 来源 | 当次结果 |
| --- | --- |
| SoutuBot 直连 | 请求成功，新版响应可解析并生成结果 |
| AVScan | 请求成功，响应可解析并生成结果 |
| ascii2d | 站点拒绝访问/要求验证，正确返回 `verification_required` |
| Yandex | HTTP 200 返回“service is under construction”页面；已增加维护识别及回归测试，返回 `unavailable` |
| SauceNAO | 没有 API Key，未执行真实搜索；上传格式和 PicImageSearch 解析已用模拟响应验证 |

上游状态会随出口网络和站点策略变化。SoutuBot 浏览器服务已通过本地真实浏览器测试，尚未在受 Cloudflare 挑战的目标出口完成真实人工验证。

## 部署验证边界

当前环境没有可用的 Linux Docker/systemd 运行环境，因此未执行 Linux 镜像构建、容器启动、noVNC 桌面或 systemd 服务启动；相关文件已做静态检查。部署后请按 README 检查 `/healthz`，使用获准上传的图片验证搜索，并确认实际消息适配器的引用图片和缩略图发送行为。
