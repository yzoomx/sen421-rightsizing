"""Benchmark workload for SEN421 right-sizing study.

A small study-card API modeled on a spaced-repetition study app:
  GET  /api/decks/{deck_id}/cards  - read 20 due cards from SQLite (I/O + JSON)
  POST /api/reviews                - grade a typed answer against every card in
                                     the deck (trigram similarity) and reschedule
                                     the card with the SM-2 algorithm (CPU-bound)
  GET  /health                     - health check for the load balancer
  GET  /internal/cpu               - raw /proc/stat counters, used by the
                                     load generator to compute CPU busy and
                                     steal percentages during each test

All data is synthetic (see gen_data.py).
"""
import math
import os
import sqlite3

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/app/cards.db")
app = FastAPI(title="sen421-bench")
_conn = None


def db():
    # One read-only connection per worker process, opened after fork.
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro&immutable=1", uri=True, check_same_thread=False
        )
    return _conn


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/decks/{deck_id}/cards")
async def due_cards(deck_id: int, limit: int = 20):
    limit = max(1, min(limit, 50))
    rows = db().execute(
        "SELECT id, front, back, ease, interval_days, due FROM cards "
        "WHERE deck_id = ? ORDER BY due LIMIT ?",
        (deck_id, limit),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="deck not found")
    cards = []
    for cid, front, back, ease, interval, due in rows:
        # Estimated recall probability (exponential forgetting curve).
        retention = math.exp(-max(interval, 1) / (ease * 10.0))
        cards.append({
            "id": cid, "front": front, "back": back, "ease": ease,
            "interval_days": interval, "due": due,
            "retention": round(retention, 4),
        })
    return {"deck_id": deck_id, "count": len(cards), "cards": cards}


class Review(BaseModel):
    card_id: int
    answer: str


def trigrams(text):
    t = " " + text.lower() + " "
    grams = {}
    for i in range(len(t) - 2):
        g = t[i:i + 3]
        grams[g] = grams.get(g, 0) + 1
    return grams


def cosine(a, b):
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


@app.post("/api/reviews")
async def review(r: Review):
    row = db().execute(
        "SELECT deck_id, back, ease, interval_days FROM cards WHERE id = ?", (r.card_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="card not found")
    deck_id, back, ease, interval = row
    # Grade the typed answer: character-trigram cosine similarity against the
    # correct answer and against every other card in the deck, to detect when
    # the student confused this card with another one (CPU-bound work).
    ans = trigrams(r.answer)
    best_id, best_sim, own_sim = None, -1.0, 0.0
    for cid, other_back in db().execute(
        "SELECT id, back FROM cards WHERE deck_id = ?", (deck_id,)
    ):
        sim = cosine(ans, trigrams(other_back))
        if cid == r.card_id:
            own_sim = sim
        if sim > best_sim:
            best_id, best_sim = cid, sim
    quality = round(own_sim * 5)
    # SM-2 scheduling update.
    if quality < 3:
        new_interval = 1
    else:
        new_interval = 1 if interval == 0 else (6 if interval == 1 else round(interval * ease))
    new_ease = max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
    return {
        "card_id": r.card_id, "similarity": round(own_sim, 4), "quality": quality,
        "confused_with": best_id if best_id != r.card_id else None,
        "new_interval_days": new_interval, "new_ease": round(new_ease, 2),
    }


@app.get("/internal/cpu")
async def cpu():
    # /proc/stat is not namespaced, so inside the container it reports the
    # whole virtual machine. Fields: user nice system idle iowait irq softirq steal
    with open("/proc/stat") as f:
        fields = f.readline().split()[1:9]
    names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
    return {"nproc": os.cpu_count(), **{n: int(v) for n, v in zip(names, fields)}}
