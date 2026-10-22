# GitHub Actions 部署到绿联 NAS

## 已确认的目标

- 仓库：joshuapex/mahjong-offline；触发分支：production。
- Runner 名称/标签：ugreen-nas；Linux x64；以 zweimal 运行。
- Compose 项目：mahjong-offline；服务：mahjong。
- NAS 配置：/volume1/docker/1panel/opt/1panel/docker/compose/mahjong-offline/compose.nas.yaml。
- 数据卷：mahjong-offline-data，挂载到 /app/data。
- 当前镜像引用：mahjong-offline:1.0.9；端口默认 18080。

## 启用

把本 PR 合并到 production 时会触发首次部署，以后每次推送 production 都会触发。
草稿 PR 和其他分支的推送不会触发本工作流。不配置 pull_request 或 pull_request_target 触发器。
Runner 有操作 NAS Docker 的权限，仅让可信维护者修改并推送 production。

无需配置 NAS SSH 密码、PAT 或公网端口。GitHub 自动提供只读 GITHUB_TOKEN 拉取代码。
GitHub 仓库 Actions 页面可查看 Deploy production to UGREEN NAS 的日志和运行摘要。

## 每次部署做什么

1. 在 Runner 工作目录拉取触发此次运行的提交。
2. 检查现有服务和数据卷；配置缺失、容器未运行或数据卷不符时停止。
3. 构建带提交和运行编号标签的镜像。在构建失败时保留运行中的服务。
4. 在临时容器内运行 unittest；只挂载 tests，不挂载生产数据库，不接入网络。测试失败时停止。
5. 保留原镜像为 mahjong-offline:before-运行ID-重试序号。
6. 使用 SQLite 在线 backup API 创建一致性快照，并检查完整性。快照位于数据卷内
   /app/data/backups/before-actions-运行ID-重试序号.db。备份失败时不切换镜像。
7. 将构建好的镜像关联到 NAS Compose 当前使用的镜像引用，再重建 mahjong 并等待健康检查。
8. 验证容器 healthy 且镜像提交标签与本次提交一致。替换容器期间有短暂中断。

为兼容现有 1Panel 编排，不自动修改 NAS 的 Compose、.env 或其源码。
因此 mahjong-offline:1.0.9 会作为现有部署的可变引用；它不能代表代码版本。
精确构建保留在 mahjong-offline:git-提交SHA-运行ID-重试序号，
容器镜像的 org.opencontainers.image.revision 标签记录实际提交。
现有 Compose 中的端口、管理员配置、COOKIE_SECURE 等继续生效。
仓库 Compose 的新环境变量不会自动应用到 NAS；需要时手动更新 NAS 的 Compose。
不要在 1Panel 使用旧源码重新构建，否则会覆盖部署镜像。

## 网络与失败处理

Runner 连通 GitHub 不代表 Docker Hub 或 Python 依赖源也能连通。
Docker build 首次需要下载 python:3.13-slim 和 requirements.lock 中的依赖。
Runner 代理配置不会自动替 Docker daemon 或构建阶段配置代理。
如果卡在 checkout、拉基础镜像或 pip 安装，请提供对应步骤的报错（不要提供令牌）。

健康检查失败时工作流标红，并保留旧镜像和数据库备份，不会自动回滚数据库。
镜像回滚不能撤销数据库迁移；先检查兼容性，必要时按 DEPLOY-NAS.md 恢复备份。
在确认数据库兼容后，可把运行摘要中的 previous image 重新标记为 NAS Compose 使用的镜像，
然后在上述部署目录执行：
docker compose -p mahjong-offline -f compose.nas.yaml up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 180 mahjong

数据卷中的快照不能替代异机备份。工作流不删除镜像、备份或数据卷；
镜像和快照会累计占用空间，应定期保留近期有效版本并清理已确认不需要的版本。
不要执行 docker compose down -v。

## 验证状态

创建时已静态检查工作流与目标配置。当前工具环境不能运行本地 shell/Docker，
因此尚未实际执行构建、unittest 或 NAS 部署，首次合并后的运行才是端到端验证。
浏览器端测试不包含在本工作流中。
