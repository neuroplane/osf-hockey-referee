#!/usr/bin/env python3
"""API игры «Вброс по борту»: Яндекс OAuth, краудсорсинг вопросов, рейтинг дня.

Конфиг — переменные окружения (см. /opt/quiz-api/.env на сервере):
  YANDEX_CLIENT_ID, YANDEX_CLIENT_SECRET — приложение на oauth.yandex.ru
  DATABASE_URL — postgres DSN (default: postgresql://quiz:quiz@127.0.0.1:5434/quiz)
  BASE_URL — публичный базовый путь API (default: /referee/api)
  SITE_URL — https://sport.tgbsrv.ru
  VOTE_THRESHOLD — порог голосов для approve/reject (default: 5)
"""
import json
import os
import re
import secrets
import time
from contextlib import closing
from datetime import date, timedelta

import httpx
import psycopg
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

CLIENT_ID = os.environ.get("YANDEX_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("YANDEX_CLIENT_SECRET", "")
DSN = os.environ.get("DATABASE_URL", "postgresql://quiz:quiz@127.0.0.1:5434/quiz")
SITE_URL = os.environ.get("SITE_URL", "https://sport.tgbsrv.ru")
BASE_URL = os.environ.get("BASE_URL", "/referee/api")
THRESHOLD = int(os.environ.get("VOTE_THRESHOLD", "5"))
SESSION_TTL = 60 * 60 * 24 * 30  # 30 дней
CALLBACK_URL = f"{SITE_URL}{BASE_URL}/auth/callback"
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
# Директория с data-файлами наборов (quiz/data/*.js) — источник для сидирования банка в БД
QUIZ_DATA_DIR = os.environ.get("QUIZ_DATA_DIR", "/var/www/sport.tgbsrv.ru/referee/quiz/data")

# Шифрование персональных данных (имя, логин) в базе. Ключ — NAME_KEY в .env,
# сгенерировать: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_fernet = Fernet(os.environ["NAME_KEY"].encode()) if os.environ.get("NAME_KEY") else None


def enc(s):
    return _fernet.encrypt(s.encode()).decode() if (_fernet and s) else (s or "")


def dec(s):
    if not _fernet or not s:
        return s or ""
    try:
        return _fernet.decrypt(s.encode()).decode()
    except InvalidToken:
        return s  # легаси-строка в открытом виде


app = FastAPI(docs_url=None, redoc_url=None)


def db():
    return psycopg.connect(DSN, row_factory=dict_row)


def init_db():
    with closing(db()) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            yandex_id TEXT UNIQUE NOT NULL,
            login TEXT,
            name TEXT NOT NULL,
            created BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            expires BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            text TEXT NOT NULL,
            opts TEXT NOT NULL,
            correct INTEGER NOT NULL,
            rule TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            score INTEGER NOT NULL DEFAULT 0,
            created BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS votes (
            user_id INTEGER NOT NULL REFERENCES users(id),
            proposal_id INTEGER NOT NULL REFERENCES proposals(id),
            value INTEGER NOT NULL,
            UNIQUE (user_id, proposal_id)
        );
        CREATE TABLE IF NOT EXISTS daily_scores (
            user_id INTEGER NOT NULL REFERENCES users(id),
            day INTEGER NOT NULL,
            score INTEGER NOT NULL,
            right_cnt INTEGER NOT NULL,
            ts BIGINT NOT NULL,
            UNIQUE (user_id, day)
        );
        CREATE TABLE IF NOT EXISTS daily_answers (
            user_id INTEGER NOT NULL REFERENCES users(id),
            day INTEGER NOT NULL,
            qn INTEGER NOT NULL,
            ok BOOLEAN NOT NULL,
            secs REAL NOT NULL,
            UNIQUE (user_id, day, qn)
        );
        CREATE TABLE IF NOT EXISTS qedits (
            qkey TEXT PRIMARY KEY,
            q_text TEXT,
            opts TEXT NOT NULL,
            correct INTEGER NOT NULL,
            editor INTEGER REFERENCES users(id),
            updated BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ansedits (
            qid INTEGER PRIMARY KEY,
            ans TEXT NOT NULL,
            rule TEXT,
            gold BOOLEAN NOT NULL DEFAULT false,
            editor INTEGER REFERENCES users(id),
            updated BIGINT NOT NULL
        );
        ALTER TABLE ansedits ADD COLUMN IF NOT EXISTS gold BOOLEAN NOT NULL DEFAULT false;
        CREATE TABLE IF NOT EXISTS qsets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            daily BOOLEAN NOT NULL DEFAULT TRUE,
            ord INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS questions (
            qkey TEXT PRIMARY KEY,
            set_id TEXT NOT NULL REFERENCES qsets(id),
            n INTEGER NOT NULL,
            q TEXT NOT NULL,
            qtable TEXT NOT NULL DEFAULT '',
            media TEXT NOT NULL DEFAULT '[]',
            opts TEXT NOT NULL,
            correct INTEGER NOT NULL,
            rule TEXT NOT NULL DEFAULT '',
            edited BOOLEAN NOT NULL DEFAULT FALSE,
            orig_q TEXT, orig_opts TEXT, orig_correct INTEGER,
            editor INTEGER REFERENCES users(id),
            updated BIGINT NOT NULL DEFAULT 0
        );
        """)
        con.commit()


# --- Банк вопросов в БД ---
# q/opts/correct — действующие значения (отдаются клиентам).
# edited=FALSE: это исходник из data-файла, orig_* пустые.
# edited=TRUE: это ручная правка, orig_* хранит исходник для отката.
# Синхронизация при старте: исходник обновляется всегда, правки сохраняются.

def seed_questions():
    import glob
    files = sorted(glob.glob(os.path.join(QUIZ_DATA_DIR, "*.js")))
    if not files:
        return
    now = int(time.time())
    with closing(db()) as con:
        for f in files:
            src = open(f, encoding="utf-8").read()
            for payload in re.findall(r"QUIZ_SETS\.push\((\{.*?\})\)\s*;", src, re.S):
                try:
                    st = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                con.execute(
                    "INSERT INTO qsets (id, title, daily) VALUES (%s,%s,%s) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title, daily=excluded.daily",
                    (st["id"], st["title"], st.get("daily", True)))
                for q in st.get("questions", []):
                    con.execute("""
                        INSERT INTO questions
                            (qkey,set_id,n,q,qtable,media,opts,correct,rule,edited,updated)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s)
                        ON CONFLICT(qkey) DO UPDATE SET
                            set_id=excluded.set_id, n=excluded.n,
                            qtable=excluded.qtable, media=excluded.media, rule=excluded.rule,
                            q       = CASE WHEN questions.edited THEN questions.q       ELSE excluded.q END,
                            opts    = CASE WHEN questions.edited THEN questions.opts    ELSE excluded.opts END,
                            correct = CASE WHEN questions.edited THEN questions.correct ELSE excluded.correct END,
                            orig_q       = CASE WHEN questions.edited THEN excluded.q       ELSE NULL END,
                            orig_opts    = CASE WHEN questions.edited THEN excluded.opts    ELSE NULL END,
                            orig_correct = CASE WHEN questions.edited THEN excluded.correct ELSE NULL END
                        """, (f"{st['id']}:{q['n']}", st["id"], q["n"], q["q"],
                              q.get("table", ""), json.dumps(q.get("media", []), ensure_ascii=False),
                              json.dumps(q["opts"], ensure_ascii=False), q["correct"],
                              q.get("rule", ""), now))
        con.commit()
    print(f"seed_questions: синхронизировано {len(files)} файлов из {QUIZ_DATA_DIR}")


init_db()
seed_questions()


def current_user(req: Request):
    token = req.cookies.get("vb_session")
    if not token:
        return None
    with closing(db()) as con:
        row = con.execute(
            "SELECT u.id, u.name, u.login FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=%s AND s.expires>%s", (token, int(time.time()))).fetchone()
        if row:
            row["name"] = dec(row["name"])
            row["login"] = dec(row["login"])
        return row


@app.get("/me")
def me(req: Request):
    u = current_user(req)
    if not u:
        return {"logged": False, "auth_url": f"{BASE_URL}/auth/yandex"}
    return {"logged": True, "name": u["name"], "login": u["login"],
            "admin": u["id"] in ADMIN_IDS}


@app.get("/auth/yandex")
def auth_yandex():
    if not CLIENT_ID:
        return JSONResponse({"error": "OAuth не настроен на сервере"}, status_code=503)
    return RedirectResponse(
        "https://oauth.yandex.ru/authorize?response_type=code"
        f"&client_id={CLIENT_ID}&redirect_uri={CALLBACK_URL}")


@app.get("/auth/callback")
async def auth_callback(code: str, resp: Response):
    async with httpx.AsyncClient(timeout=15) as cli:
        tok = await cli.post("https://oauth.yandex.ru/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        if tok.status_code != 200:
            return JSONResponse({"error": "обмен кода не удался"}, status_code=502)
        access = tok.json()["access_token"]
        info = await cli.get("https://login.yandex.ru/info",
                             headers={"Authorization": f"OAuth {access}"})
        if info.status_code != 200:
            return JSONResponse({"error": "не удалось получить профиль"}, status_code=502)
        p = info.json()
    yid = str(p["id"])
    name = p.get("real_name") or p.get("display_name") or p.get("login") or "Судья"
    login = p.get("login") or ""
    now = int(time.time())
    with closing(db()) as con:
        con.execute(
            "INSERT INTO users (yandex_id, login, name, created) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(yandex_id) DO UPDATE SET login=excluded.login, name=excluded.name",
            (yid, enc(login), enc(name), now))
        uid = con.execute("SELECT id FROM users WHERE yandex_id=%s", (yid,)).fetchone()["id"]
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions (token, user_id, expires) VALUES (%s,%s,%s)",
                    (token, uid, now + SESSION_TTL))
        con.commit()
    r = RedirectResponse("/referee/quiz/", status_code=303)
    r.set_cookie("vb_session", token, max_age=SESSION_TTL,
                 httponly=True, samesite="lax", secure=True)
    return r


@app.post("/logout")
def logout(req: Request, resp: Response):
    token = req.cookies.get("vb_session")
    if token:
        with closing(db()) as con:
            con.execute("DELETE FROM sessions WHERE token=%s", (token,))
            con.commit()
    r = JSONResponse({"ok": True})
    r.delete_cookie("vb_session")
    return r


class Proposal(BaseModel):
    text: str = Field(min_length=10, max_length=2000)
    opts: list[str]
    correct: int = Field(ge=0, le=3)
    rule: str = Field(min_length=1, max_length=200)


@app.post("/propose")
def propose(body: Proposal, req: Request):
    u = current_user(req)
    if not u:
        return JSONResponse({"error": "нужно войти"}, status_code=401)
    opts = [o.strip() for o in body.opts]
    if len(opts) != 4 or len(set(opts)) != 4 or any(not o or len(o) > 500 for o in opts):
        return JSONResponse({"error": "нужно ровно 4 разных варианта"}, status_code=400)
    if not re.search(r"\d", body.rule):
        return JSONResponse({"error": "нужна ссылка на пункт правил (с номером)"}, status_code=400)
    with closing(db()) as con:
        n = con.execute("SELECT COUNT(*) c FROM proposals WHERE user_id=%s AND status='pending'",
                        (u["id"],)).fetchone()["c"]
        if n >= 10:
            return JSONResponse({"error": "лимит: 10 вопросов на голосовании одновременно"},
                                status_code=429)
        row = con.execute(
            "INSERT INTO proposals (user_id, text, opts, correct, rule, created) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (u["id"], body.text.strip(), json.dumps(opts, ensure_ascii=False),
             body.correct, body.rule.strip(), int(time.time()))).fetchone()
        con.commit()
        return {"ok": True, "id": row["id"]}


@app.get("/proposals")
def proposals(req: Request, status: str = "pending"):
    u = current_user(req)
    if status not in ("pending", "approved", "rejected"):
        status = "pending"
    with closing(db()) as con:
        rows = con.execute(
            "SELECT p.id, p.user_id, p.text, p.opts, p.correct, p.rule, p.score, p.created, "
            "u.name AS author, (SELECT v.value FROM votes v WHERE v.proposal_id=p.id AND v.user_id=%s) AS my "
            "FROM proposals p JOIN users u ON u.id=p.user_id "
            "WHERE p.status=%s ORDER BY p.created DESC LIMIT 100",
            (u["id"] if u else -1, status)).fetchall()
    return {"items": [{
        "id": r["id"], "text": r["text"], "opts": json.loads(r["opts"]),
        "correct": r["correct"], "rule": r["rule"], "score": r["score"],
        "author": dec(r["author"]), "my_vote": r["my"], "mine": bool(u and u["id"] == r["user_id"])
    } for r in rows]}


class Vote(BaseModel):
    proposal_id: int
    value: int = Field(ge=-1, le=1)


@app.post("/vote")
def vote(body: Vote, req: Request):
    u = current_user(req)
    if not u:
        return JSONResponse({"error": "нужно войти"}, status_code=401)
    with closing(db()) as con:
        p = con.execute("SELECT user_id, status FROM proposals WHERE id=%s",
                        (body.proposal_id,)).fetchone()
        if not p or p["status"] != "pending":
            return JSONResponse({"error": "вопрос недоступен для голосования"}, status_code=400)
        if p["user_id"] == u["id"]:
            return JSONResponse({"error": "нельзя голосовать за свой вопрос"}, status_code=400)
        old = con.execute("SELECT value FROM votes WHERE user_id=%s AND proposal_id=%s",
                          (u["id"], body.proposal_id)).fetchone()
        delta = body.value - (old["value"] if old else 0)
        if body.value == 0:
            con.execute("DELETE FROM votes WHERE user_id=%s AND proposal_id=%s",
                        (u["id"], body.proposal_id))
        else:
            con.execute(
                "INSERT INTO votes (user_id, proposal_id, value) VALUES (%s,%s,%s) "
                "ON CONFLICT(user_id, proposal_id) DO UPDATE SET value=excluded.value",
                (u["id"], body.proposal_id, body.value))
        con.execute("UPDATE proposals SET score=score+%s WHERE id=%s", (delta, body.proposal_id))
        score = con.execute("SELECT score FROM proposals WHERE id=%s",
                            (body.proposal_id,)).fetchone()["score"]
        if score >= THRESHOLD:
            con.execute("UPDATE proposals SET status='approved' WHERE id=%s", (body.proposal_id,))
        elif score <= -THRESHOLD:
            con.execute("UPDATE proposals SET status='rejected' WHERE id=%s", (body.proposal_id,))
        con.commit()
    return {"ok": True, "score": score}


class AnswerRec(BaseModel):
    qn: int
    ok: bool
    secs: float = Field(ge=0, le=600)


class DailyScore(BaseModel):
    day: int
    score: int = Field(ge=0, le=2000)
    right: int = Field(ge=0, le=10)
    answers: list[AnswerRec] = []


@app.post("/daily/score")
def daily_score(body: DailyScore, req: Request):
    u = current_user(req)
    if not u:
        return JSONResponse({"error": "нужно войти"}, status_code=401)
    day = today_id()  # день всегда серверный — часы клиента роли не играют
    with closing(db()) as con:
        try:
            con.execute(
                "INSERT INTO daily_scores (user_id, day, score, right_cnt, ts) VALUES (%s,%s,%s,%s,%s)",
                (u["id"], day, body.score, body.right, int(time.time())))
        except psycopg.errors.UniqueViolation:
            return JSONResponse({"error": "результат за сегодня уже записан"}, status_code=409)
        for a in body.answers:
            con.execute(
                "INSERT INTO daily_answers (user_id, day, qn, ok, secs) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (user_id, day, qn) DO NOTHING",
                (u["id"], day, a.qn, a.ok, a.secs))
        con.commit()
    return {"ok": True}


def today_id():
    t = date.today()
    return t.year * 10000 + t.month * 100 + t.day


@app.get("/daily/today")
def daily_today():
    """Серверный день — единственный источник правды для игры дня."""
    return {"day": today_id()}


@app.get("/daily/me")
def daily_me(req: Request):
    u = current_user(req)
    if not u:
        return JSONResponse({"error": "нужно войти"}, status_code=401)
    t = date.today()
    day = t.year * 10000 + t.month * 100 + t.day
    with closing(db()) as con:
        r = con.execute(
            "SELECT score, right_cnt FROM daily_scores WHERE user_id=%s AND day=%s",
            (u["id"], day)).fetchone()
    return {"day": day, "score": r["score"], "right": r["right_cnt"]} if r else {"day": day, "played": False}


@app.get("/daily/leaderboard")
def leaderboard(day: int = 0):
    if not day:
        t = date.today()
        day = t.year * 10000 + t.month * 100 + t.day
    with closing(db()) as con:
        rows = con.execute(
            "SELECT u.name, d.score, d.right_cnt, d.ts FROM daily_scores d "
            "JOIN users u ON u.id=d.user_id WHERE d.day=%s "
            "ORDER BY d.score DESC, d.ts ASC LIMIT 20", (day,)).fetchall()
        total = con.execute("SELECT COUNT(*) c FROM daily_scores WHERE day=%s", (day,)).fetchone()["c"]
    return {"day": day, "players": total,
            "items": [{"name": dec(r["name"]), "score": r["score"], "right": r["right_cnt"]} for r in rows]}


@app.get("/daily/monthly")
def monthly_leaderboard(days: int = 30):
    days = min(max(days, 1), 365)
    since = date.today() - timedelta(days=days - 1)
    since_id = since.year * 10000 + since.month * 100 + since.day
    with closing(db()) as con:
        rows = con.execute(
            "SELECT u.name, COUNT(*) AS days, SUM(d.score) AS total, "
            "SUM(d.right_cnt) AS rights, ROUND(AVG(d.score)) AS avg_score, MAX(d.score) AS best "
            "FROM daily_scores d JOIN users u ON u.id=d.user_id "
            "WHERE d.day >= %s "
            "GROUP BY u.id, u.name ORDER BY total DESC, days DESC, rights DESC LIMIT 100",
            (since_id,)).fetchall()
        players_days = con.execute(
            "SELECT COUNT(DISTINCT user_id) p, COUNT(DISTINCT day) d FROM daily_scores WHERE day >= %s",
            (since_id,)).fetchone()
    return {"days": days, "since": since_id, "players": players_days["p"], "days_with_games": players_days["d"],
            "items": [{**dict(r), "name": dec(r["name"])} for r in rows]}


@app.get("/stats/questions")
def question_stats(day: int = 0):
    """Статистика по вопросам дня: попыток, % верных, среднее время."""
    if not day:
        t = date.today()
        day = t.year * 10000 + t.month * 100 + t.day
    with closing(db()) as con:
        rows = con.execute(
            "SELECT qn, COUNT(*) AS tries, "
            "ROUND(100.0 * SUM(ok::int) / COUNT(*), 1) AS right_pct, "
            "ROUND(AVG(secs)::numeric, 1) AS avg_secs "
            "FROM daily_answers WHERE day=%s GROUP BY qn ORDER BY right_pct ASC", (day,)).fetchall()
    return {"day": day, "items": [dict(r) for r in rows]}


# --- Банк вопросов и редактор ---

@app.get("/questions")
def questions_all():
    """Весь банк для квиз-движка: наборы → вопросы с действующими вариантами."""
    with closing(db()) as con:
        sets = con.execute("SELECT id, title, daily FROM qsets ORDER BY ord, id").fetchall()
        rows = con.execute(
            "SELECT set_id, n, q, qtable, media, opts, correct, rule, edited "
            "FROM questions ORDER BY set_id, n").fetchall()
    by_set = {s["id"]: {"id": s["id"], "title": s["title"], "daily": s["daily"],
                        "questions": []} for s in sets}
    for r in rows:
        if r["set_id"] in by_set:
            by_set[r["set_id"]]["questions"].append({
                "n": r["n"], "q": r["q"], "table": r["qtable"],
                "media": json.loads(r["media"]), "opts": json.loads(r["opts"]),
                "correct": r["correct"], "rule": r["rule"], "edited": r["edited"]})
    return {"sets": [s for s in by_set.values() if s["questions"]]}


class QEdit(BaseModel):
    qkey: str = Field(min_length=1, max_length=40)
    q: str | None = Field(default=None, max_length=2000)
    opts: list[str]
    correct: int = Field(ge=0, le=3)


@app.post("/qedit")
def qedit_save(body: QEdit, req: Request):
    u = current_user(req)
    if not u or u["id"] not in ADMIN_IDS:
        return JSONResponse({"error": "нет прав"}, status_code=403)
    if not re.match(r"^[\w\-]+:\d+$", body.qkey):
        return JSONResponse({"error": "кривой ключ вопроса"}, status_code=400)
    opts = [o.strip() for o in body.opts]
    if len(opts) != 4 or len(set(opts)) != 4 or any(not o or len(o) > 500 for o in opts):
        return JSONResponse({"error": "нужно ровно 4 разных варианта"}, status_code=400)
    q_text = body.q.strip() if body.q else None
    if body.q is not None and len(q_text) < 10:
        return JSONResponse({"error": "текст вопроса слишком короткий"}, status_code=400)
    with closing(db()) as con:
        row = con.execute("SELECT q FROM questions WHERE qkey=%s", (body.qkey,)).fetchone()
        if not row:
            return JSONResponse({"error": "вопрос не найден"}, status_code=404)
        con.execute("""
            UPDATE questions SET
                q=%s, opts=%s, correct=%s, edited=TRUE,
                orig_q       = COALESCE(orig_q, q),
                orig_opts    = COALESCE(orig_opts, opts),
                orig_correct = COALESCE(orig_correct, correct),
                editor=%s, updated=%s
            WHERE qkey=%s""",
            (q_text if q_text else row["q"], json.dumps(opts, ensure_ascii=False),
             body.correct, u["id"], int(time.time()), body.qkey))
        con.commit()
    return {"ok": True}


@app.delete("/qedit/{qkey}")
def qedit_delete(qkey: str, req: Request):
    u = current_user(req)
    if not u or u["id"] not in ADMIN_IDS:
        return JSONResponse({"error": "нет прав"}, status_code=403)
    with closing(db()) as con:
        row = con.execute(
            "SELECT orig_q, orig_opts, orig_correct FROM questions WHERE qkey=%s",
            (qkey,)).fetchone()
        if not row or row["orig_opts"] is None:
            return JSONResponse({"error": "правки нет — вопрос исходный"}, status_code=400)
        con.execute("""
            UPDATE questions SET q=orig_q, opts=orig_opts, correct=orig_correct,
                edited=FALSE, orig_q=NULL, orig_opts=NULL, orig_correct=NULL,
                editor=%s, updated=%s WHERE qkey=%s""",
            (u["id"], int(time.time()), qkey))
        cur = con.execute(
            "SELECT q, opts, correct FROM questions WHERE qkey=%s", (qkey,)).fetchone()
        con.commit()
    return {"ok": True, "q": cur["q"], "opts": json.loads(cur["opts"]),
            "correct": cur["correct"]}


# --- Правки правильных ответов (страница /referee/questions/) ---
# Ключ — id вопроса в базе ФХР (стабилен при вставках, в отличие от № карточки).

@app.get("/aedits")
def aedits_list():
    with closing(db()) as con:
        rows = con.execute("SELECT qid, ans, rule, gold FROM ansedits").fetchall()
    return {"items": {str(r["qid"]): {"ans": r["ans"], "rule": r["rule"] or "",
                                     "gold": r["gold"]} for r in rows}}


class AEdit(BaseModel):
    qid: int
    ans: str = Field(min_length=1, max_length=500)
    rule: str = Field(default="", max_length=200)
    gold: bool = False


@app.post("/aedit")
def aedit_save(body: AEdit, req: Request):
    u = current_user(req)
    if not u or u["id"] not in ADMIN_IDS:
        return JSONResponse({"error": "нет прав"}, status_code=403)
    with closing(db()) as con:
        con.execute(
            "INSERT INTO ansedits (qid, ans, rule, gold, editor, updated) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(qid) DO UPDATE SET ans=excluded.ans, rule=excluded.rule, "
            "gold=excluded.gold, editor=excluded.editor, updated=excluded.updated",
            (body.qid, body.ans.strip(), body.rule.strip(), body.gold,
             u["id"], int(time.time())))
        con.commit()
    return {"ok": True}


@app.delete("/aedit/{qid}")
def aedit_delete(qid: int, req: Request):
    u = current_user(req)
    if not u or u["id"] not in ADMIN_IDS:
        return JSONResponse({"error": "нет прав"}, status_code=403)
    with closing(db()) as con:
        con.execute("DELETE FROM ansedits WHERE qid=%s", (qid,))
        con.commit()
    return {"ok": True}


@app.get("/health")
def health():
    with closing(db()) as con:
        con.execute("SELECT 1")
    return {"ok": True}
