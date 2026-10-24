"""Clinic info retrieval (RAG). Clinic knowledge bases are tiny (tens of chunks), so BM25 over
topic + text is fast, deterministic and good enough; see docs/decisions/0004-retrieval.md for
when to switch to pgvector embeddings. Answers are read out verbatim from the clinic's own text,
never generated, so the AI cannot invent a price or an opening time."""

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from callbridge.db.models import KnowledgeChunk

STOP = set("""a an and are at be can could do does for from have how i if in is it me my of on or our please
the there this to us we what when where which will with would you your hi hello yeah um uh just tell know
want like about any get ok okay so""".split())

SYNONYMS = {
    "open": "hours", "opening": "hours", "close": "hours", "closing": "hours", "shut": "hours", "time": "hours",
    "times": "hours", "saturday": "hours", "sunday": "hours", "weekend": "hours",
    "park": "parking", "car": "parking", "drive": "parking", "driving": "parking",
    "much": "price", "cost": "price", "costs": "price", "charge": "price", "pounds": "price", "fee": "price",
    "expensive": "price", "prices": "price",
    "where": "address", "based": "address", "find": "address", "postcode": "address", "located": "address", "located?": "address", "directions": "address",
    "card": "payment", "contactless": "payment", "cash": "payment", "pay": "payment",
    "wheelchair": "accessibility", "disabled": "accessibility", "stairs": "accessibility", "lift": "accessibility",
    "insured": "insurance", "bupa": "insurance", "axa": "insurance", "vitality": "insurance", "aviva": "insurance",
    "wear": "bring", "clothes": "bring",
    "offer": "services", "treat": "services", "treatments": "services", "osteopathy": "services",
    "massage": "services",
}

MIN_SCORE = 1.2


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z']+", text.lower()):
        if w in SYNONYMS:
            out += [SYNONYMS[w], w]
        elif w not in STOP:
            out.append(w)
    return out


@dataclass
class Hit:
    chunk_id: int
    topic: str
    text: str
    score: float


def search(s: Session, clinic_id: int, question: str, k: int = 1) -> list[Hit]:
    chunks = list(s.scalars(select(KnowledgeChunk).where(KnowledgeChunk.clinic_id == clinic_id)))
    if not chunks:
        return []
    docs = [tokens(c.topic) * 3 + tokens(c.text) for c in chunks]
    avg = sum(len(d) for d in docs) / len(docs)
    df = Counter(t for d in docs for t in set(d))
    q = tokens(question)
    hits = []
    for c, d in zip(chunks, docs, strict=False):
        tf = Counter(d)
        score = 0.0
        for t in set(q):
            if t not in tf:
                continue
            idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        topical = set(q) & set(tokens(c.topic))  # must hit the chunk's topic, not just share a word
        if score >= MIN_SCORE and topical:
            hits.append(Hit(c.id, c.topic, c.text, score))
    return sorted(hits, key=lambda h: -h.score)[:k]


def answer(s: Session, clinic_id: int, question: str) -> str | None:
    hits = search(s, clinic_id, question)
    return hits[0].text if hits else None
