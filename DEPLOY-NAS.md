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

旧 `compose.yaml` 保留目录挂载方案；简化部署使用独立的 `compose.nas.yaml`，不会自动读取旧宿主机 `data/app.db`。已有旧部署请下载备份后使用 import 命令迁移，避免误认为数据消失。
