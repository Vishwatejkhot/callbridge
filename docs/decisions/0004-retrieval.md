# 0004: Clinic info retrieval: BM25 now, pgvector when it pays

**Status:** accepted

A clinic's knowledge base is about 10 to 40 short chunks (hours, prices, parking…). BM25 with a small
synonym map, plus a rule that the question must hit the chunk's *topic*, is deterministic, needs no
embedding calls (saves latency) and is easy to test. Answers are read out verbatim from the clinic's
own text, so prices and times are never paraphrased. If nothing matches: "I'm not sure, let me take a
message."

**Revisit when:** a clinic's FAQ passes about 100 chunks, or `faq_unknown` failures show paraphrases BM25
misses. The upgrade path is a `vector(1024)` column on `knowledge_chunks` (pgvector is already enabled by
`000_roles.sql`) and hybrid scoring, keeping the topic gate and the verbatim answers.
