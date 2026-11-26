"""Generate a synthetic, read-only flashcard database (no real personal data).

Run once at image build time. A fixed random seed makes the database
identical on every server, so every configuration serves the same data.
"""
import random
import sqlite3
import sys

N_DECKS = 500
CARDS_PER_DECK = 100
SEED = 421

WORDS = (
    "cloud compute storage network latency throughput region zone replica "
    "container kernel hypervisor scaling balancer cache queue function api "
    "database index query backup snapshot recovery policy encryption key "
    "tenant billing quota instance volume bucket object block file subnet "
    "gateway firewall certificate token session request response worker"
).split()


def sentence(rng, n_words):
    return " ".join(rng.choice(WORDS) for _ in range(n_words)).capitalize() + "."


def main(path):
    rng = random.Random(SEED)
    con = sqlite3.connect(path)
    con.execute("DROP TABLE IF EXISTS cards")
    con.execute(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, deck_id INTEGER, front TEXT, "
        "back TEXT, ease REAL, interval_days INTEGER, due INTEGER)"
    )
    rows = []
    cid = 1
    for deck in range(1, N_DECKS + 1):
        for _ in range(CARDS_PER_DECK):
            rows.append((
                cid, deck,
                sentence(rng, rng.randint(6, 12)) + " ?",
                " ".join(sentence(rng, rng.randint(8, 14)) for _ in range(3)),
                round(rng.uniform(1.3, 2.8), 2),
                rng.randint(0, 60),
                rng.randint(0, 10_000),
            ))
            cid += 1
    con.executemany("INSERT INTO cards VALUES (?,?,?,?,?,?,?)", rows)
    con.execute("CREATE INDEX idx_deck_due ON cards (deck_id, due)")
    con.commit()
    con.close()
    print(f"wrote {len(rows)} cards to {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cards.db")
