# 个人固定镜像发行

源码：[haipengTian/muse2api](https://github.com/haipengTian/muse2api)，基于 [czg86389-hub/muse2api](https://github.com/czg86389-hub/muse2api)，沿用 MIT 许可。

## 1.6.6-custom.1

镜像：`ghcr.io/haipengtian/muse2api:1.6.6-custom.1`，支持 **Linux amd64（x86_64）**。

同步上游 1.6.6（`1cd0168`），包括 1.6.5 的账号独立 HTTP／SOCKS5 代理、隔离下载与视频延迟交付修复，以及 1.6.6 的仅 `hatch_sess` 导入兼容和扩展升级提示。未设置账号代理时沿用原来的直连路径，无需新增配置。

保留个人版固定镜像更新方式、附件清理范围、切会话就绪检查和脱敏诊断。诊断工具同步支持已配置的账号代理，不输出代理密码。

服务升级后，已加载的浏览器扩展不会自动替换。如需使用本次导入修正，下载后台提供的扩展包覆盖原目录，然后在扩展管理页重新加载；个人版旧 1.0.2 与本次上游 1.0.2 的导入条件不同，不能仅凭版本号判断已升级。

此前“输入框出现后消失”的生产问题仍需升级后验证；上游本次更新没有明确修复该问题。

## 1.6.4-custom.3

镜像：`ghcr.io/haipengtian/muse2api:1.6.4-custom.3`，支持 **Linux amd64（x86_64）**。

修正“账号测试通过、生成时找不到聊天输入框”相关的两处页面操作缺陷：
- 清理附件只操作 composer 表单内的附件按钮与文件输入框，找不到 composer 时不操作其他区域；避免原来扫描整页“删除”按钮的误点。
- 切换新会话后等待输入框与网关就绪；等待失败或导航异常时停止发送并报告页面准备失败。保留无需导航的热对话复用。

同时包含扩展 1.0.2 和只读诊断工具。独立 HTTP 保活仍以 `/api/session` 的实际返回为准；本版不把 `unavailable` 改写成保活成功。

回归使用真实 Chromium 的离线页面复现“全局删除按钮移除 composer”，以及页面等待失败、导航失败、热对话复用。真实账号生成需部署后验收。

## 1.6.4-custom.2

镜像：`ghcr.io/haipengtian/muse2api:1.6.4-custom.2`，支持 **Linux amd64（x86_64）**。

`custom.1` 的构建未通过启动检查，未发布镜像；`custom.2` 修正了容器启动时连接重置的检查重试。

- 基于上游 1.6.4；保留接口、账号数据结构、端口 18610 和 `/app/data` 持久化路径。
- `/api/session` 返回 HTTP 200 但会话未分配时，日志增加 `session_probe`：只记录固定白名单状态及已知字段的类型。不记录 Cookie、令牌、VM 标识、地址、错误正文或其他响应内容。未知状态显示 `<other>`，缺失字段显示 `<missing>`。
- 非 JSON 响应只记录 `json_type=non_json`。
- 固定镜像拒绝后台“在线更新”，避免被上游源码覆盖。后续升级请拉取新镜像并重建容器；不要在容器内使用 Git 更新。

这一版提供定位证据，尚未确认或修复服务器会话异常的根因。镜像发布前运行离线回归、隔离 Chromium 测试及服务启动检查；真实账号保活和生成需在服务器更新后验证。

## 扩展 1.0.2：按当前窗口读取 Cookie

扩展现在按当前标签页所属的 Cookie 存储读取，区分普通窗口和无痕窗口；只读取匹配 `https://muse.ai/api/session` 的 Cookie，避免其他子域或无关路径的同名项覆盖。匹配结果按 Chrome 的路径和创建时间顺序保留首项。

将修正版 `extension/` 的完整内容覆盖原扩展目录，在 `chrome://extensions` 点击该扩展的刷新按钮。保持原目录可保留扩展配置。然后在能正常使用 Muse 的同一窗口点“读取并导入”，到账号池对新增条目点一次保活。原账号不必删除。

扩展可以独立更新，无需重启服务或重新发布镜像。已部署的 `1.6.4-custom.2` 镜像内置的下载包仍是 1.0.1，请使用修正版源码目录或单独的 1.0.2 扩展包。

## 生成时报找不到输入框：实时观察页面

已观察到输入框出现后消失时，可自动复现发送前的准备过程，无需手工点生成：

```bash
(set -o pipefail; curl -fsSL --connect-timeout 10 --max-time 60 \
  https://raw.githubusercontent.com/haipengTian/muse2api/main/tools/watch_composer.py \
  | docker exec -i muse2api python - --probe)
```

自动探测使用最近导入的启用账号和独立临时浏览器，跳过 HTTP 续签及 VM 唤醒，先被动观察初始化，再逐步执行切会话、滚动、清附件和聚焦；输入框消失后停止，不输入提示词或提交生成。输出只有阶段、页面状态、运行时错误类别及浏览器既有会话请求的白名单摘要。探测结束释放自身浏览器，不修改账号文件或生产服务配置。

生成请求结束后会销毁其隔离页面，事后检查页面列表为空属于正常清理。需要在生成过程中观察：

```bash
(set -o pipefail; curl -fsSL --connect-timeout 10 --max-time 60 \
  https://raw.githubusercontent.com/haipengTian/muse2api/main/tools/watch_composer.py \
  | docker exec -i muse2api python - --seconds 180)
```

出现“监测已启动”后，在后台点一次“生成图片”，分享报错前后的监测输出。监测持续 180 秒后自动结束。
脚本连接现有浏览器，只记录路由类别、输入框数量、可见性、弹窗等固定状态的变化；不读取 Cookie、不输出页面正文或地址，也不导航、点击或发送内容。脚本退出只断开自身的观察连接。
可直接用于现有个人镜像，无需更新镜像。

## 一次对比服务器 HTTP 与浏览器会话

官网正常、服务器返回 `unavailable` 时，可直接使用容器已保存的账号检查，不需要手工复制 Cookie，也不必先更新镜像。适用于 `1.6.4-custom.2` 及之后的个人镜像：

```bash
(set -o pipefail; curl -fsSL --connect-timeout 10 --max-time 60 \
  https://raw.githubusercontent.com/haipengTian/muse2api/main/tools/diagnose_session.py \
  | docker exec -i muse2api python -)
```

默认选择最近导入的启用账号，输出其账号 ID，以及 HTTP 和独立 Chromium 的脱敏会话状态。临时浏览器使用独立端口、数据目录和配置；检查结束后清理。脚本不写账号文件、不唤醒 VM、不提交生成任务、不重启服务。检查会额外启动一个临时 Chromium，建议在没有生成任务时执行。

输出的 `conclusion`：
- `browser_only`：容器浏览器拿到分配，HTTP 请求未拿到；重点查请求方式和会话续签差异。
- `both_unavailable`：同一份导入 Cookie 的两条容器请求都返回不可用；继续核对导入来源与服务器访问环境。
- `both_assigned`：两条请求当前均拿到分配，回到后台验证真实生成。
- `http_only`：HTTP 分配正常，继续查浏览器加载和 Cookie 注入。
- `inconclusive`：检查未取得足够的会话结果，查看对应的固定错误类型。

分享检查结果即可，不要分享账号数据文件、Cookie 或完整网络请求。需要指定账号时，在命令的 `python -` 后加 `--account 账号ID`。

## 更新已有 Docker Compose 部署

先确认现有容器的 Compose 文件、数据挂载及架构，不要删除容器数据：

```bash
uname -m
docker inspect muse2api --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker inspect muse2api --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
docker inspect muse2api --format '{{range .Mounts}}{{println .Destination .Type .Source}}{{end}}'
```

在**原部署目录**更新原 Compose 的 `muse2api.image` 为上述固定镜像，保留环境变量、端口、数据卷、共享内存等配置。备份原 Compose 和当前镜像引用。等待进行中的生成完成后执行：

```bash
docker compose pull muse2api
docker compose up -d --no-build --no-deps muse2api
curl -fsS http://127.0.0.1:18610/healthz
```

若原部署通过多个 `-f` 文件或其他端口启动，继续使用原参数。不要执行 `down -v`，不要重新初始化账号。回滚时恢复原镜像引用及 Compose 文件，再用相同参数重建服务。原镜像应保留，不要先清理。

更新后，在后台对问题账号点一次保活，再查看：

```bash
docker logs --since 5m muse2api 2>&1 | grep session_probe
```

`session_probe` 只在相关异常分支出现；没有该日志不代表真实生成成功。不要粘贴完整 Cookie、密钥或未脱敏的浏览器网络请求。
