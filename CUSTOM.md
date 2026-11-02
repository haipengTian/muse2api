# 个人固定镜像发行

源码：[haipengTian/muse2api](https://github.com/haipengTian/muse2api)，基于 [czg86389-hub/muse2api](https://github.com/czg86389-hub/muse2api)，沿用 MIT 许可。

## 1.6.4-custom.1

镜像：`ghcr.io/haipengtian/muse2api:1.6.4-custom.1`，支持 **Linux amd64（x86_64）**。

- 基于上游 1.6.4；保留接口、账号数据结构、端口 18610 和 `/app/data` 持久化路径。
- `/api/session` 返回 HTTP 200 但会话未分配时，日志增加 `session_probe`：只记录固定白名单状态及已知字段的类型。不记录 Cookie、令牌、VM 标识、地址、错误正文或其他响应内容。未知状态显示 `<other>`，缺失字段显示 `<missing>`。
- 非 JSON 响应只记录 `json_type=non_json`。
- 固定镜像拒绝后台“在线更新”，避免被上游源码覆盖。后续升级请拉取新镜像并重建容器；不要在容器内使用 Git 更新。

这一版提供定位证据，尚未确认或修复服务器会话异常的根因。镜像发布前运行离线回归、隔离 Chromium 测试及服务启动检查；真实账号保活和生成需在服务器更新后验证。

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
