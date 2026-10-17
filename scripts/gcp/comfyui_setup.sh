#!/usr/bin/env bash
# GCP の GPU VM(Deep Learning VM のイメージ。NVIDIA のドライバ入り)で、ComfyUI と Wan 2.2 を用意して常駐させる。
# VM の起動スクリプト(--metadata-from-file startup-script=...)に指定する。2回目以降の起動では、入れ直さずに起動だけする。
#
# メタデータ(gcloud の --metadata)で渡すもの:
#   comfy-token   合言葉(アプリの VIDEO_GEN_TOKEN と同じ。16文字以上)  ※必須
#   idle-minutes  この分数、頼まれなかったら VM を止める(既定 30。0 で止めない)
#   tunnel        cloudflared(既定。URL はシリアルポートの出力に出る)か none(Tailscale などで自分でつなぐ)
#   comfy-ref     ComfyUI の版(git のタグ・コミット。例 v0.3.60)。版が変わるとノードの入力名が変わることがあるので、
#                 動いた版を入れておく(無ければ最新)
#   cloudflared-version  cloudflared の版(例 2025.9.1。無ければ最新)
#
# 詳しくは docs/video_remote_jp.md。
set -euo pipefail

meta() { curl -fsS -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null || true; }
TOKEN="$(meta comfy-token)"
IDLE_MINUTES="$(meta idle-minutes)"; IDLE_MINUTES="${IDLE_MINUTES:-30}"
TUNNEL="$(meta tunnel)"; TUNNEL="${TUNNEL:-cloudflared}"
COMFY_REF="$(meta comfy-ref)"
CLOUDFLARED_VERSION="$(meta cloudflared-version)"
REPO_RAW="https://raw.githubusercontent.com/hirotoitpost/novelai-userscripts-example/main"
ROOT=/opt/comfy
HF=https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files

if [ "${#TOKEN}" -lt 16 ]; then
  echo "comfy-token(16文字以上)をメタデータに入れてください。" >&2
  exit 1
fi

if [ ! -f "$ROOT/.installed" ]; then
  apt-get update && apt-get install -y --no-install-recommends git python3-venv aria2
  mkdir -p "$ROOT"
  git clone https://github.com/comfyanonymous/ComfyUI "$ROOT/ComfyUI"
  if [ -n "$COMFY_REF" ]; then
    git -C "$ROOT/ComfyUI" checkout "$COMFY_REF"
  fi
  echo "ComfyUI: $(git -C "$ROOT/ComfyUI" describe --tags --always)" | tee /dev/ttyS0
  python3 -m venv "$ROOT/venv"
  "$ROOT/venv/bin/pip" install --upgrade pip
  "$ROOT/venv/bin/pip" install -r "$ROOT/ComfyUI/requirements.txt"
  M="$ROOT/ComfyUI/models"
  aria2c -x 8 -d "$M/diffusion_models" "$HF/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
  aria2c -x 8 -d "$M/diffusion_models" "$HF/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
  aria2c -x 8 -d "$M/text_encoders" "$HF/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  aria2c -x 8 -d "$M/vae" "$HF/vae/wan_2.1_vae.safetensors"
  curl -fsSL "$REPO_RAW/scripts/comfy_auth_proxy.py" -o "$ROOT/comfy_auth_proxy.py"
  curl -fsSL "$REPO_RAW/scripts/gcp/idle_shutdown.sh" -o "$ROOT/idle_shutdown.sh"
  chmod +x "$ROOT/idle_shutdown.sh"
  if [ "$TUNNEL" = "cloudflared" ]; then
    if [ -n "$CLOUDFLARED_VERSION" ]; then
      CF_URL="https://github.com/cloudflare/cloudflared/releases/download/$CLOUDFLARED_VERSION/cloudflared-linux-amd64"
    else
      CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    fi
    curl -fsSL "$CF_URL" -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
  fi
  touch "$ROOT/.installed"
fi

# 合言葉は root だけが読めるファイルに置き、systemd の EnvironmentFile で渡す(ExecStart に書くと ps・systemctl cat で見える)
install -m 600 /dev/null "$ROOT/proxy.env"
printf 'COMFY_TOKEN=%s\n' "$TOKEN" > "$ROOT/proxy.env"

# ComfyUI は VM の中だけで待つ(外から直接は入れない)。外へは合言葉の中継(8189)だけを出す
cat > /etc/systemd/system/comfyui.service <<EOF
[Unit]
Description=ComfyUI
After=network-online.target
[Service]
WorkingDirectory=$ROOT/ComfyUI
ExecStart=$ROOT/venv/bin/python main.py --listen 127.0.0.1 --port 8188
Restart=always
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/comfy-proxy.service <<EOF
[Unit]
Description=ComfyUI auth proxy
After=comfyui.service
[Service]
EnvironmentFile=$ROOT/proxy.env
ExecStart=$ROOT/venv/bin/python $ROOT/comfy_auth_proxy.py --listen 127.0.0.1:8189 --activity-file $ROOT/last_request
Restart=always
[Install]
WantedBy=multi-user.target
EOF
date +%s > "$ROOT/last_request"
systemctl daemon-reload
systemctl enable --now comfyui comfy-proxy

if [ "$TUNNEL" = "cloudflared" ]; then
  # URL は起動のたびに変わる。シリアルポートの出力に出るので、gcloud compute instances get-serial-port-output で見る
  cat > /etc/systemd/system/comfy-tunnel.service <<EOF
[Unit]
Description=cloudflared quick tunnel for ComfyUI
After=comfy-proxy.service
[Service]
ExecStart=/bin/sh -c '/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8189 2>&1 | tee /dev/ttyS0'
Restart=always
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now comfy-tunnel
fi

if [ "$IDLE_MINUTES" != "0" ]; then
  cat > /etc/systemd/system/comfy-idle.service <<EOF
[Unit]
Description=Stop the VM when ComfyUI is idle
[Service]
Type=oneshot
ExecStart=$ROOT/idle_shutdown.sh $ROOT/last_request $IDLE_MINUTES
EOF
  cat > /etc/systemd/system/comfy-idle.timer <<EOF
[Unit]
Description=Check ComfyUI idle every 5 minutes
[Timer]
OnBootSec=10min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now comfy-idle.timer
fi
