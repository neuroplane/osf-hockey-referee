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
from datetime import date

import httpx
import psycopg
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
        """)
        con.commit()


init_db()


def current_user(req: Request):
    token = req.cookies.get("vb_session")
    if not token:
        return None
    with closing(db()) as con:
        row = con.execute(
            "SELECT u.id, u.name, u.login FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=%s AND s.expires>%s", (token, int(time.time()))).fetchone()
        return row


@app.get("/me")
def me(req: Request):
    u = current_user(req)
    if not u:
        return {"logged": False, "auth_url": f"{BASE_URL}/auth/yandex"}
    return {"logged": True, "name": u["name"], "login": u["login"]}


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
            (yid, login, name, now))
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
        "author": r["author"], "my_vote": r["my"], "mine": bool(u and u["id"] == r["user_id"])
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
    today = date.today()
    today_id = today.year * 10000 + today.month * 100 + today.day
    if body.day != today_id:
        return JSONResponse({"error": "очки принимаются только за сегодня"}, status_code=400)
    with closing(db()) as con:
        try:
            con.execute(
                "INSERT INTO daily_scores (user_id, day, score, right_cnt, ts) VALUES (%s,%s,%s,%s,%s)",
                (u["id"], body.day, body.score, body.right, int(time.time())))
        except psycopg.errors.UniqueViolation:
            return JSONResponse({"error": "результат за сегодня уже записан"}, status_code=409)
        for a in body.answers:
            con.execute(
                "INSERT INTO daily_answers (user_id, day, qn, ok, secs) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (user_id, day, qn) DO NOTHING",
                (u["id"], body.day, a.qn, a.ok, a.secs))
        con.commit()
    return {"ok": True}


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
            "items": [{"name": r["name"], "score": r["score"], "right": r["right_cnt"]} for r in rows]}


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


@app.get("/health")
def health():
    with closing(db()) as con:
        con.execute("SELECT 1")
    return {"ok": True}
