#!/usr/bin/env python3
"""Слияние ручных вариантов ответов в quiz/data/fhr_active.js.

manual_options.json: { "№": ["неправильный1","неправильный2","неправильный3"] }
Правильный ответ берётся из текущего fhr_active.js и вставляется на
позицию (n % 4) — детерминированно, чтобы перегенерация не меняла раскладку.
Запуск: python3 fhr_questions/tools/apply_options.py
"""
import json, re, sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
DATA = f"{ROOT}/quiz/data/fhr_active.js"
MANUAL = f"{ROOT}/fhr_questions/manual_options.json"


def load_set(path):
    s = open(path, encoding="utf-8").read()
    m = re.search(r"window\.QUIZ_SETS\.push\((\{.*\})\)\s*;?\s*$", s, re.S)
    return json.loads(m.group(1))


def main():
    d = load_set(DATA)
    try:
        manual = json.load(open(MANUAL, encoding="utf-8"))
    except FileNotFoundError:
        manual = {}
    applied = skipped = 0
    for q in d["questions"]:
        key = str(q["n"])
        if key not in manual:
            continue
        wrongs = manual[key]
        if len(wrongs) != 3:
            print(f"!! {key}: {len(wrongs)} дистракторов, пропуск", file=sys.stderr)
            skipped += 1
            continue
        correct_text = q["opts"][q["correct"]]
        if correct_text in wrongs:
            print(f"!! {key}: правильный среди дистракторов, пропуск", file=sys.stderr)
            skipped += 1
            continue
        pos = q["n"] % 4
        opts = list(wrongs)
        opts.insert(pos, correct_text)
        q["opts"] = opts
        q["correct"] = pos
        applied += 1
    out = (
        "window.QUIZ_SETS=window.QUIZ_SETS||[];"
        f"window.QUIZ_SETS.push({json.dumps(d, ensure_ascii=False)});"
    )
    open(DATA, "w", encoding="utf-8").write(out)
    print(f"применено: {applied}, пропущено: {skipped}, всего вопросов: {len(d['questions'])}")


if __name__ == "__main__":
    main()
