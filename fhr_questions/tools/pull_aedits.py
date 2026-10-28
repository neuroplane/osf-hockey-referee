#!/usr/bin/env python3
"""Выгружает онлайн-правки ответов (POST /referee/api/aedit) в aedits.json.

Правки хранятся в Postgres (таблица ansedits) и мержатся на странице при
загрузке. Этот скрипт материализует их в fhr_questions/aedits.json, чтобы
gen_answers.py подхватывал их при перегенерации (answers.md, quiz/trainer).
Запуск: python3 pull_aedits.py [url]
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = sys.argv[1] if len(sys.argv) > 1 else "https://sport.tgbsrv.ru/referee/api/aedits"

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
items = json.load(urllib.request.urlopen(req, timeout=30))["items"]
out = ROOT / "aedits.json"
if items:
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                   encoding="utf-8")
elif out.exists():
    out.unlink()
print(f"правок: {len(items)} -> {out}" if items else "правок нет")
