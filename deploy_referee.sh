#!/bin/sh
# Сборка и публикация на https://sport.tgbsrv.ru/referee/
# Использование: sh deploy_referee.sh
set -e
cd "$(dirname "$0")"

sh build_html.sh

DEST=/var/www/sport.tgbsrv.ru/referee
ssh -o BatchMode=yes tgbsrv "mkdir -p $DEST/img $DEST/tools $DEST/facts"
scp -o BatchMode=yes "ХОККЕЙ ОСФ 26-27.html" "tgbsrv:$DEST/index.html"
scp -o BatchMode=yes facts/index.html "tgbsrv:$DEST/facts/index.html"
scp -o BatchMode=yes tools/rules.css tools/rules.js "tgbsrv:$DEST/tools/"
scp -o BatchMode=yes \
  img/boards-glass-net.png img/face-protection.png img/faceoff-spots.png \
  img/goal-frame.png img/rink-markings.png img/zones.png \
  "tgbsrv:$DEST/img/"

curl -s -o /dev/null -w "Проверка: %{http_code}\n" https://sport.tgbsrv.ru/referee/
