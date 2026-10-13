#!/bin/sh
set -eu
cd "$(dirname "$0")"

compose() {
    docker compose -p mahjong-offline -f compose.nas.yaml "$@"
}

show_address() {
    address="$(compose port mahjong 8080 | head -n 1)"
    printf '访问 http://NAS局域网IP:%s\n' "${address##*:}"
}

docker compose version >/dev/null
case "${1:-start}" in
    start|update)
        compose build
        compose up -d --no-build --pull never --wait --wait-timeout 120
        printf '\n启动完成。'
        show_address
        printf '空数据库首次启动的账号密码见下方日志；已有账号不会重置。\n'
        compose logs --tail 30 mahjong
        ;;
    import)
        backup="${2:-./migration.db}"
        if [ ! -f "$backup" ]; then
            printf '备份不存在：%s\n' "$backup" >&2
            exit 1
        fi
        compose build
        compose stop mahjong
        compose create --no-build mahjong
        compose cp "$backup" mahjong:/app/data/migration.db
        compose run --rm -T --no-deps mahjong python -m app.manage restore /app/data/migration.db --service-stopped
        compose up -d --no-build --pull never --wait --wait-timeout 120
        printf '\n迁移完成，使用原账号密码登录。'
        show_address
        ;;
    logs)
        compose logs --tail 100 mahjong
        ;;
    stop)
        compose stop mahjong
        ;;
    *)
        printf '用法：sh deploy.sh [start|update|import 备份文件|logs|stop]\n' >&2
        exit 2
        ;;
esac
