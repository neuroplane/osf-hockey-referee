#!/bin/sh
# Сборка и публикация на https://sport.tgbsrv.ru/referee/
# Использование: sh deploy_referee.sh
set -e
cd "$(dirname "$0")"

sh build_html.sh

DEST=/var/www/sport.tgbsrv.ru/referee
ssh -o BatchMode=yes tgbsrv "mkdir -p $DEST/img $DEST/tools $DEST/facts $DEST/addons $DEST/db $DEST/questions $DEST/files $DEST/trainer $DEST/quiz/data"
scp -o BatchMode=yes "ХОККЕЙ ОСФ 26-27.html" "tgbsrv:$DEST/index.html"
scp -o BatchMode=yes facts/index.html "tgbsrv:$DEST/facts/index.html"
scp -o BatchMode=yes tools/rules.css tools/rules.js "tgbsrv:$DEST/tools/"
scp -o BatchMode=yes \
  img/boards-glass-net.png img/face-protection.png img/faceoff-spots.png \
  img/goal-frame.png img/rink-markings.png img/zones.png \
  "tgbsrv:$DEST/img/"

# Дополнительные разделы (всё, что не страница правил)
scp -o BatchMode=yes addons/index.html "tgbsrv:$DEST/addons/index.html"
scp -o BatchMode=yes fhr_questions/index.html "tgbsrv:$DEST/db/index.html"
scp -o BatchMode=yes fhr_questions/questions/index.html "tgbsrv:$DEST/questions/index.html"
scp -o BatchMode=yes fhr_questions/trainer/index.html "tgbsrv:$DEST/trainer/"
scp -o BatchMode=yes \
  quiz/index.html quiz/daily.html quiz/propose.html quiz/vote.html \
  quiz/board.html \
  quiz/engine.css quiz/engine.js \
  "tgbsrv:$DEST/quiz/"
scp -o BatchMode=yes quiz/data/*.js "tgbsrv:$DEST/quiz/data/"

# Бэкенд игры «Вброс по борту» (секреты в /opt/quiz-api/.env — не заливаются)
scp -o BatchMode=yes server/quiz_api.py server/requirements.txt "tgbsrv:/opt/quiz-api/"
ssh -o BatchMode=yes tgbsrv \
  "/opt/quiz-api/venv/bin/pip -q install -r /opt/quiz-api/requirements.txt && systemctl restart quiz-api"
scp -o BatchMode=yes \
  fhr_questions/answers_1_1276.md fhr_questions/top40_rules.md \
  fhr_questions/exam_notes.md fhr_questions/questions.md \
  fhr_questions/questions_deleted.md fhr_questions/questions.json \
  fhr_questions/rules/rules.json fhr_questions/rules/rules_ekp_2022_23.json \
  fhr_questions/rules/rules_iihf_2018_22.json fhr_questions/rules/rules_iihf_2026_27.json \
  "tgbsrv:$DEST/files/"

curl -s -o /dev/null -w "Проверка: %{http_code}\n" https://sport.tgbsrv.ru/referee/
curl -s -o /dev/null -w "Addons:   %{http_code}\n" https://sport.tgbsrv.ru/referee/addons/
curl -s -o /dev/null -w "Quiz:     %{http_code}\n" https://sport.tgbsrv.ru/referee/quiz/
curl -s -o /dev/null -w "Daily:    %{http_code}\n" https://sport.tgbsrv.ru/referee/quiz/daily.html
