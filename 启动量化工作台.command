#!/bin/bash
# 备用启动方式（会开一个终端窗口）。一般直接双击「量化工作台.app」即可。
cd "$(dirname "$0")" || exit 1
PY="/Users/admin/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
echo "启动中… 浏览器会自动打开 http://127.0.0.1:8770/ ，关闭此窗口即停止服务。"
exec "$PY" app/server.py --port 8770
