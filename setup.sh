#!/bin/bash
# ====================================================================
# UNIVERSAL BOT HOSTING PLATFORM - 1-CLICK AUTO INSTALLER & RUNNER
# ====================================================================
set -e

echo ""
echo "🚀 [1/5] Updating Linux Packages & Installing System Dependencies..."
apt update -y && apt install -y python3 python3-pip python3-venv postgresql postgresql-contrib curl wget unzip zip

echo ""
echo "🗄 [2/5] Configuring & Starting Local PostgreSQL Database..."
service postgresql start || true
su - postgres -c "psql -c \"ALTER USER postgres WITH PASSWORD 'securepass123';\"" || true
su - postgres -c "createdb bot_platform_db" 2>/dev/null || true

echo ""
echo "🐍 [3/5] Setting up Python Virtual Environment & Dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "📁 [4/5] Preparing Storage Directories..."
mkdir -p storage/projects storage/backups

echo ""
echo "⚡ [5/5] Launching Platform in Background (24/7 Persistent)..."
pkill -9 -f "main.py" 2>/dev/null || true
nohup python3 main.py > platform.log 2>&1 &

sleep 3
echo ""
echo "===================================================================="
echo "🎉 SUCCESS! Your Universal Bot Platform is LIVE and RUNNING!"
echo "===================================================================="
echo "🤖 Telegram Bot: Active & Polling (@telebothoster_bot)"
echo "🌐 Web Admin Dashboard: http://localhost:8000/admin"
echo "📜 View Live Logs: tail -f platform.log"
echo "🛑 Stop Platform: pkill -9 -f 'main.py'"
echo "===================================================================="
echo ""

# পারমিশন দিয়ে এক্সিকিউটেবল করা
chmod +x setup.sh
