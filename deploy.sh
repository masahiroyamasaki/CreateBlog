#!/bin/bash
# ============================================================
# deploy.sh — VPS デプロイスクリプト
# 使い方: bash deploy.sh
# ============================================================

set -euo pipefail

# ---- 設定 (環境に合わせて変更) ----
APP_DIR="/var/www/blog-app"           # VPS上のアプリディレクトリ
BACKUP_BASE="/var/backups/blog-app"   # バックアップ保存先
SERVICE_NAME="gunicorn"               # systemd サービス名
VENV_DIR="$APP_DIR/venv"             # Python仮想環境のパス
KEEP_BACKUPS=10                       # 保持するバックアップ世代数
# ------------------------------------

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="$BACKUP_BASE/$TIMESTAMP"

echo "=========================================="
echo " Deploy: $(date)"
echo "=========================================="

# ---- 1. バックアップ ----
echo "[1/4] バックアップ作成: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# アプリファイル (venv, __pycache__, .git を除く)
rsync -a --exclude='venv/' \
         --exclude='__pycache__/' \
         --exclude='.git/' \
         --exclude='uploads/' \
         "$APP_DIR/" "$BACKUP_DIR/app/"

# データベースを個別コピー (rsync対象外になることがあるため明示)
if [ -f "$APP_DIR/blog.db" ]; then
  cp "$APP_DIR/blog.db" "$BACKUP_DIR/blog_${TIMESTAMP}.db"
  echo "  -> blog.db をバックアップしました"
fi

echo "  -> バックアップ完了"

# ---- 2. git pull ----
echo "[2/4] git pull"
cd "$APP_DIR"
git fetch origin
git pull origin master
echo "  -> pull 完了"

# ---- 3. 依存パッケージ更新 ----
echo "[3/4] pip install"
"$VENV_DIR/bin/pip" install -q -r requirements.txt
echo "  -> pip 完了"

# ---- 4. サービス再起動 ----
echo "[4/4] サービス再起動: $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sleep 2
STATUS=$(sudo systemctl is-active "$SERVICE_NAME")
if [ "$STATUS" = "active" ]; then
  echo "  -> $SERVICE_NAME は正常に起動しました"
else
  echo "  [警告] $SERVICE_NAME のステータス: $STATUS"
  echo "  -> ログ確認: sudo journalctl -u $SERVICE_NAME -n 50"
  exit 1
fi

# ---- 古いバックアップを削除 ----
BACKUP_COUNT=$(ls -1d "$BACKUP_BASE"/[0-9]* 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$KEEP_BACKUPS" ]; then
  echo "古いバックアップを削除 (${KEEP_BACKUPS}世代を保持)"
  ls -1dt "$BACKUP_BASE"/[0-9]* | tail -n +$((KEEP_BACKUPS + 1)) | xargs rm -rf
fi

echo ""
echo "=========================================="
echo " デプロイ完了! バックアップ: $BACKUP_DIR"
echo "=========================================="
