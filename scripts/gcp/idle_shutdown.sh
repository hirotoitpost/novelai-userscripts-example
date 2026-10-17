#!/usr/bin/env bash
# 最後に頼まれてから(comfy_auth_proxy.py が書く時刻)指定の分数がたち、ComfyUI に待ちも実行中も無ければ VM を止める。
# 止めた VM は GPU の料金がかからない(ディスクの料金だけ)。comfyui_setup.sh が5分おきに呼ぶ。
#   idle_shutdown.sh <時刻のファイル> <分>
set -euo pipefail
FILE="$1"
MINUTES="$2"
last="$(cat "$FILE" 2>/dev/null || echo 0)"
now="$(date +%s)"
if [ $(( now - ${last%.*} )) -lt $(( MINUTES * 60 )) ]; then
  exit 0
fi
busy="$(curl -fsS http://127.0.0.1:8188/queue \
  | python3 -c 'import json, sys; q = json.load(sys.stdin); print(len(q.get("queue_running", [])) + len(q.get("queue_pending", [])))' \
  || echo 0)"  # ComfyUI が落ちていても止める(GPU の料金がかかり続けないように)
if [ "$busy" = "0" ]; then
  logger "comfy-idle: ${MINUTES}分つかわれていないので VM を止めます"
  shutdown -h now
fi
