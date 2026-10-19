#!/bin/sh
# Сборка HTML-версии руководства: ХОККЕЙ ОСФ 26-27.html
# В HTML входит только основной текст правил — приложения отрезаются.
# Использование: sh build_html.sh
cd "$(dirname "$0")"

TMP="/tmp/rules_main_only.md"

# вырезаем все приложения и их пункты из оглавления
sed -e '/^# ПРИЛОЖЕНИЕ 1/,$d' \
    -e '/^- \[ПРИЛОЖЕНИЕ/d' \
    "ХОККЕЙ ОСФ 26-27.md" > "$TMP"

pandoc "$TMP" \
  -f gfm -t html5 \
  --template=tools/template.html \
  --toc --toc-depth=1 \
  -M title="Руководство по судейству ОСФ 2026/2027" \
  -o "ХОККЕЙ ОСФ 26-27.html" \
&& sed -i '' 's|<main>|<main><p class="facts-nav"><a href="facts/">Интересные факты →</a></p>|' "ХОККЕЙ ОСФ 26-27.html" \
&& echo "Собрано: ХОККЕЙ ОСФ 26-27.html (без приложений)"

# страница «Интересные факты» — facts/index.html
mkdir -p facts
pandoc "ФАКТЫ.md" \
  -f gfm -t html5 \
  --template=tools/template_facts.html \
  --toc --toc-depth=1 \
  -M title="Интересные факты — Правила хоккея ОСФ" \
  -o "facts/index.html" \
&& echo "Собрано: facts/index.html"
