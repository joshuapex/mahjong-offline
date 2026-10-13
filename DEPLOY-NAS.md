# NAS 简化部署

上传源码 ZIP，在 1Panel 能读取的目录中解压。进入这个目录，在终端执行：

```sh
sh deploy.sh
```

默认访问 `http://NAS局域网IP:18080`。无需创建 `.env`、修改宿主机文件权限或提前打包镜像。首次构建需要 NAS 能访问 Docker 镜像仓库及 Python 依赖源。终端需能操作 NAS 的 Docker 引擎（1Panel 容器应已映射 Docker socket）。在 NAS SSH 中遇到 Docker 权限不足可使用 `sudo sh deploy.sh`。

若 1Panel 创建“路径选择”编排，请选 `compose.nas.yaml`。首次空库会生成随机管理员密码，账号为 `admin`，在容器启动日志中显示一次；登录后修改。重启不会重置已有账号。通过命令脚本创建的编排在不同版本的 1Panel 中可能显示为外部编排，启停可以继续使用脚本。

## 迁移已有数据

在旧网站下载完整数据库备份，上传到解压目录并命名为 `migration.db`，执行：

```sh
sh deploy.sh import ./migration.db
```

脚本停止应用，将备份复制到 Docker 数据卷，检查完整性、保留恢复前快照并恢复，最后重新启动。使用原账号密码登录，登录会话会清除。不会把备份打包进镜像。导入失败时应用保持停止，原备份留在数据卷中，检查错误后重新导入。

## 后续使用

```sh
sh deploy.sh logs    # 查看日志
sh deploy.sh stop    # 停止，保留数据库
sh deploy.sh update  # 更新源码后构建并启动
```

数据保存在 Docker 命名卷 `mahjong-offline-data`，由容器启动时自动处理权限，应用随后以 UID 10001 运行。只修改挂载的数据目录，不跟随符号链接。卷独立于源码目录；更新源码或重建容器不会移除数据。不要删除此数据卷或执行带 `-v` 的 Compose down；备份仍可在网站管理页下载，恢复前快照保留在卷内。

默认是局域网 HTTP，`COOKIE_SECURE=false`。如接入 HTTPS，通过环境变量或可选的 `.env` 设置 `COOKIE_SECURE=true`。端口可通过 `APP_PORT` 修改，如 `APP_PORT=18081 sh deploy.sh`。

## 出站网络与 AI

公网 IPv4 查询优先使用太平洋电脑网 `https://whois.pconline.com.cn/ipJson.jsp`（JSONP）和 `https://ip9.com.cn/get`（嵌套 JSON），再尝试 `https://ipinfo.io/json`、`4.ipw.cn`、ipify 和 AWS。支持 JSON、JSONP 和纯文本格式，JSONP 只解析 JSON，不执行脚本；仅保存有效公网 IPv4、来源及查询时间，不保存地理位置。直接通过 NAS 的 IPv4 网络访问，不经过 AI 代理，避免把代理服务器的出口 IP 当作 NAS 地址。查询失败会列出来源及超时、HTTP 状态或网络错误；国内服务在电脑上可用不代表在 NAS 可用。

网站「项目管理 → LLM 配置」现为 DeepSeek 专用页面，官方基础地址固定为 `https://api.deepseek.com`，通过下拉框选择 Flash 或 V4 Pro 并保存 API 密钥。默认使用非思考模式；可开启深度思考并选择低、高或最高强度。思考模式输出上限为 16384 tokens（含思考），直接回答上限为 4096 tokens，请求超时为 5 分钟。「测试连接」以非思考模式验证当前表单，会产生少量 API 费用，不保存配置；密钥框留空可使用已保存密钥。

若 AI 必须经过代理，在项目 `.env` 加入 `LLM_PROXY_URL=http://代理局域网IP:端口`，然后重建容器使配置生效。支持 HTTP/HTTPS 代理；地址须能从应用容器访问，不能用 `127.0.0.1` 指向另一台电脑。此代理仅用于 AI，不用于公网 IP 查询。默认不读取通用 HTTP_PROXY/HTTPS_PROXY 环境变量。

AI 失败会显示安全的分类提示，例如 HTTP 401（密钥）、402（余额）、404（接口或模型）、429（限流/额度）、网络超时或空正文，不显示供应商原始响应或密钥。保存配置不代表实际调用已通过。

离线镜像更新：在电脑构建并导出后上传 NAS，执行 `docker load -i mahjong-image-1.0.7.tar`。旧 NAS 编排文件若仍使用 1.0.0，先把 `compose.nas.yaml` 的 `image` 改为 `mahjong-offline:1.0.7`，再执行 `docker compose -p mahjong-offline -f compose.nas.yaml up -d --no-build --pull never --force-recreate`。更新时保留原命名卷，不需要重新恢复数据库。不要执行 `deploy.sh`，它会触发构建。

浏览器打开 `http://NAS局域网IP:18080/health`，响应应包含 `"version":"1.0.7"`。LLM 配置页也显示应用版本；静态资源携带版本号且不缓存。若页面仍旧，请强制刷新。公网 IP 按钮立即显示等待状态，失败信息保留在页面，25 秒后结束前端等待。

旧 `compose.yaml` 保留目录挂载方案；简化部署使用独立的 `compose.nas.yaml`，不会自动读取旧宿主机 `data/app.db`。已有旧部署请下载备份后使用 import 命令迁移，避免误认为数据消失。
