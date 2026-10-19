#!/bin/sh
# Автокоммит всех изменений и пуш в GitHub.
# Использование: sh push.sh [сообщение коммита]
set -e
cd "$(dirname "$0")"

git add -A
if git diff --cached --quiet; then
  echo "Нет изменений — пушить нечего"
  exit 0
fi

MSG="${1:-Обновление $(date '+%Y-%m-%d %H:%M')}"
git commit -m "$MSG"
git push
echo "Запушено: $MSG"
