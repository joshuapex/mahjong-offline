# 版本号

格式为 `年.月日.当天提交序号`，例如 `26.1022.1` 表示 2026 年 10 月 22 日的第 1 次提交。

- 年取后两位；月、日各占两位，例如 1 月 5 日为 `0105`。
- 日期按北京时间（Asia/Shanghai）计算；同一天提交序号从 1 递增，换日重置为 1。
- 自动读取当前 Git HEAD 的提交时间，统计其第一父链上同一天的提交数。合并提交算一次，分支内被合并的提交不重复计数；使用提交时间而非作者时间。
- 无需手动修改版本号或安装 Git hook。提交后刷新首页即可读取新版本；未提交的修改不增加序号。切换分支或回退提交时版本也跟随 HEAD。
- 需要完整 Git 历史；浅克隆请先执行 `git fetch --unshallow`。本地源码运行需安装 Git。

登录页、桌面侧栏和手机首页顶部共用后端版本号，`/health`、`X-App-Version` 响应头和首页静态资源查询参数也使用这一值。

版本号在本机计算，Docker 只接收 `app/version.txt`。`.dockerignore` 排除 `.git`，仓库历史不会进入构建上下文或镜像，镜像也不安装 Git。

- 从 Git 仓库构建：执行 `python build.py`；NAS 配置使用 `python build.py -f compose.nas.yaml`。脚本先生成版本文件，再运行 Docker Compose 构建。
- `sh deploy.sh start/update/import` 在 Git 仓库中也会先用本机 Python 3 生成版本，再构建。
- 手动使用 `docker build` 或 `docker compose build` 时，先执行 `python -m app.version` 更新版本文件。
- `python package.py` 自动将最新版本写入源码包中的 `app/version.txt`。源码包部署直接沿用该版本，NAS 无需 Git 或 Python。

发布新提交后需重新打包或构建并部署，运行中的镜像不会自行升级。

请通过 `python package.py` 分发源码包。直接复制源码或使用 Git 的源码下载 ZIP 不含提交历史，无法自动计算提交序号；这种情况下先在原仓库运行 `python -m app.version`，再复制源码，以保留正确版本。
