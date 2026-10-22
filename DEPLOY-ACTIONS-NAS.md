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
面向 production 的 PR 只在 GitHub 托管机器上构建和测试；deploy 明确只允许 production 的 push 事件。
不使用 pull_request_target。PR 不运行 NAS 任务。
Runner 有操作 NAS Docker 的权限，仅让可信维护者修改并推送 production。

无需配置 NAS SSH 密码、PAT 或公网端口。GitHub 自动提供只读 GITHUB_TOKEN 拉取代码。
GitHub 仓库 Actions 页面可查看 Deploy production to UGREEN NAS 的日志和运行摘要。

## 每次部署做什么

1. GitHub 托管的 Ubuntu x64 机器拉取触发提交，构建 linux/amd64 镜像。
2. 云端临时容器运行现有 unittest，只有测试文件挂载，不挂载生产数据且禁用测试网络。
3. 测试通过后 docker save 并 gzip 压缩镜像，以 Actions Artifact 保存 3 天；计算 SHA256。
4. NAS 检查现有服务和数据卷，再按上游构建输出的 Artifact ID 下载镜像，校验 SHA256 后 docker load。
   同时检查镜像架构和提交标签。仅重跑失败的 deploy 任务也沿用上游成功构建输出，不依赖新的 attempt 编号。
5. 保留原镜像为 mahjong-offline:before-运行ID-重试序号。
6. 使用 SQLite 在线 backup API 创建一致性快照，并检查完整性。快照位于数据卷内
   /app/data/backups/before-actions-运行ID-重试序号.db。备份失败时不切换镜像。
7. 将构建好的镜像关联到 NAS Compose 当前使用的镜像引用，再重建 mahjong 并等待健康检查。
8. 验证容器 healthy 且镜像提交标签与本次提交一致。替换容器期间有短暂中断。
9. 成功或失败后清理本次下载的压缩文件。取消或主机异常时可能需手动清理残留临时目录。

为兼容现有 1Panel 编排，不自动修改 NAS 的 Compose、.env 或其源码。
因此 mahjong-offline:1.0.9 会作为现有部署的可变引用；它不能代表代码版本。
精确构建保留在 mahjong-offline:git-提交SHA-运行ID-重试序号，
容器镜像的 org.opencontainers.image.revision 标签记录实际提交。
现有 Compose 中的端口、管理员配置、COOKIE_SECURE 等继续生效。
仓库 Compose 的新环境变量不会自动应用到 NAS；需要时手动更新 NAS 的 Compose。
不要在 1Panel 使用旧源码重新构建，否则会覆盖部署镜像。

## 网络与失败处理

Docker Hub 和 Python 依赖下载都在 GitHub 云端进行，NAS 不再执行 docker build 或 docker pull。
NAS 仍需连通 GitHub Actions 的 Artifact 下载服务及其重定向存储域名，仅能打开 github.com 不一定足够。
下载速度取决于 NAS 出站网络；若超时，需要为 Runner 配置可用代理或放行相应服务。
不需要 Docker Hub/GHCR 账号、PAT、新的 Secrets 或额外公网端口。
checkout、upload-artifact、download-artifact 均固定到已核实的 v6 提交，使用 Node.js 24。
Artifact 过期后不能只重跑 deploy，需要 Re-run all jobs 重新构建并上传。
GitHub 日志中的 build 为云端构建，deploy 为 NAS 下载和部署；提供失败步骤的日志即可定位问题。

健康检查失败时工作流标红，并保留旧镜像和数据库备份，不会自动回滚数据库。
镜像回滚不能撤销数据库迁移；先检查兼容性，必要时按 DEPLOY-NAS.md 恢复备份。
在确认数据库兼容后，可把运行摘要中的 previous image 重新标记为 NAS Compose 使用的镜像，
然后在上述部署目录执行：
docker compose -p mahjong-offline -f compose.nas.yaml up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 180 mahjong

数据卷中的快照不能替代异机备份。工作流不删除镜像、备份或数据卷；
镜像和快照会累计占用空间，应定期保留近期有效版本并清理已确认不需要的版本。
不要执行 docker compose down -v。

## 验证状态

工作流与目标配置已静态核对。当前工具环境不能运行本地 shell/Docker，
通过 PR 的 build 任务验证实际构建、unittest 和 Artifact 上传；请以该次 Actions 结果为准。
NAS 的下载、备份和更新仍需合并后首次 production 推送进行端到端验证。
浏览器端测试不包含在本工作流中。
