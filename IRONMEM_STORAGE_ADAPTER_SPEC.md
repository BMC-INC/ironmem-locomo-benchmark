# SPEC — IronMem Multi-Engine Storage Adapters (paper addition #4)

> Status: **DRAFT for decision** — spec-first by design. Do **not** start coding until the §1 strategic fork is decided; the wrong abstraction here is expensive to unwind.
> Grounded in *"Are We Ready For An Agent-Native Memory System?"* (arXiv 2606.24775), module **M1** (Memory Representation & Storage): the strongest of its 12 systems use **heterogeneous multi-engine storage** (vector + graph + SQL). IronMem today owns a single SQLite/Postgres store and is not designed to sit on top of Zep / Mem0 / Letta.

---

## 1. The strategic fork (decide this first)

This addition is not primarily an engineering task — it forces a product-identity choice. Two coherent directions; pick one before any trait is written:

| | **A. IronMem as governance shim** | **B. IronMem as a system with pluggable storage** |
|---|---|---|
| Identity | A governance layer that wraps *someone else's* M1 backend (Zep/Mem0/Letta keep the vectors/graph; IronMem owns consent, trust, ledger, tombstones, namespace authority) | A standalone memory system whose *internal* storage engine is swappable (SQLite today, a vector or graph engine tomorrow) |
| Sells as | "Drop IronMem in front of the memory you already run and it becomes governed" | "IronMem, now backed by the engine of your choice" |
| Hard problem | **Governance over a store you don't fully control** (see §4) | Re-homing IronMem's own queries onto a new engine without losing the funnel gains |
| Effort | High (adapter + enforcement reconciliation per backend) | High (port retrieval/rerank/graph SQL to a trait, keep parity) |

**Recommendation: A (governance shim).** It is the defensible, paper-aligned position — none of the 12 systems do governance, so wrapping them is pure differentiation rather than re-competing on storage. B re-opens a storage race IronMem doesn't need to run. The rest of this spec assumes **A**; if we pick **B**, §3's trait is reusable but §4 (enforcement) mostly falls away.

**Non-goals (either direction):** building a new vector or graph *engine*; replacing the benchmark store before the path-to-70 work lands; supporting every one of the 12 systems (prove two, generalize later).

---

## 2. What "done" means (acceptance)

A single `StorageBackend` trait, with **two** working reference adapters and a governance-conformance test that passes against both:

1. **Vector adapter (Mem0-style):** writes/reads memory content + embeddings to an external vector store; IronMem still owns governance metadata + ledger locally.
2. **Graph adapter (Zep/Graphiti-style):** writes/reads entities + edges to an external graph store; one-hop bridge retrieval still works.
3. **Governance conformance suite** (new): the same ~12 invariants — consent gate, namespace isolation, tombstone-hides-from-retrieval, ledger hash-chain continuity, trust-tier priority — pass against *each* adapter, not just the native store. This suite is the real deliverable; the adapters are how we prove it generalizes.

---

## 3. Proposed trait (sketch — not final until §1/§4 resolved)

```rust
/// A pluggable memory substrate. IronMem composes governance ON TOP of this;
/// the backend is responsible only for durable store + recall of content,
/// embeddings, and (optionally) graph edges. Everything governance-bearing
/// (ledger, tombstones, consent, namespace authority) stays in IronMem.
#[async_trait]
pub trait StorageBackend: Send + Sync {
    fn capabilities(&self) -> BackendCaps; // {vector, graph, fulltext, native_namespacing}

    // Content + embedding
    async fn put_memory(&self, rec: &BackendMemory) -> Result<BackendId>;
    async fn get_memory(&self, id: &BackendId) -> Result<Option<BackendMemory>>;
    async fn vector_search(&self, ns: &Namespace, q: &Embedding, k: usize) -> Result<Vec<Candidate>>;
    async fn fulltext_search(&self, ns: &Namespace, q: &str, k: usize) -> Result<Vec<Candidate>>;

    // Graph (optional — gate on capabilities().graph)
    async fn put_edge(&self, e: &BackendEdge) -> Result<()>;
    async fn neighbors(&self, ns: &Namespace, entity: &str, hops: u8) -> Result<Vec<BackendEdge>>;

    // Lifecycle the governance layer drives
    async fn hide(&self, id: &BackendId, reason: TombstoneReason) -> Result<()>; // governed delete
    async fn purge(&self, id: &BackendId) -> Result<()>;                          // hard delete (forget)
}
```

IronMem's existing `retrieval.rs` (hybrid + RRF + rerank) is rewritten to consume `Candidate`s from `vector_search`/`fulltext_search`/`neighbors` instead of direct SQL. The native SQLite store becomes the **default adapter** implementing this same trait — proving the abstraction by dogfooding before any external backend.

---

## 4. The crux risk: governance over a store you don't control

IronMem's guarantees today assume it *is* the store: the ledger hash-chain, tombstone-on-delete, and namespace isolation are enforced because every write goes through one code path. Wrapping an external backend breaks that assumption. The spec must answer:

- **Enforcement vs. observation.** Can the adapter *prevent* a non-consented PHI write reaching the backend, or only *record* that it happened? Governance that can only observe is weaker — and we must say so, not paper over it.
- **Tombstone honesty.** A governed delete must make a memory un-retrievable. If the backend has no soft-delete, `hide()` must filter at query time *and* we must document that the bytes persist in the backend until `purge()`.
- **Ledger ↔ backend divergence.** If IronMem records a write in its ledger but the backend write fails (or vice versa), the audit trail lies. Needs a write-ordering + reconciliation rule (ledger-after-ack, periodic divergence check).
- **Namespace authority.** Some backends namespace natively (collections/indexes); some don't. The adapter must map IronMem namespaces to whatever isolation the backend offers, and the conformance suite must prove cross-namespace leakage is impossible.

**These four are why this is spec-first.** They are governance-correctness questions, and getting the trait shape wrong means re-deriving them per adapter.

---

## 5. Phased plan

1. **P0 — Decide §1 fork.** (Owner: James.) Blocks everything.
2. **P1 — Default adapter.** Refactor the native SQLite store behind `StorageBackend`; retrieval consumes the trait. Gate: full funnel + benchmark parity vs. the pre-refactor number (no regression). This de-risks the abstraction with zero external dependency.
3. **P2 — Governance conformance suite.** Port the relevant subset of the 163 governance tests to run against *any* `StorageBackend`. Gate: native adapter passes 100%.
4. **P3 — Vector adapter** (one backend, e.g. Mem0/Qdrant-style). Gate: conformance suite passes; vector recall within noise of native.
5. **P4 — Graph adapter** (Zep/Graphiti-style). Gate: conformance suite passes; one-hop multi-hop retrieval works.

**Stop after P2 if the §4 enforcement answers come back "observe-only"** for the candidate backends — at that point the honest product claim changes, and that's a decision, not a bug.

---

## 6. Why this waited (and the others didn't)

#3 (cost) and #5 (temporal trust) are additive changes to code IronMem already owns — safe to build and measure now. #4 redefines IronMem's relationship to its own storage and carries the four correctness risks in §4. Building the trait before §1/§4 are settled would mean shipping an abstraction we'd rebuild. Spec-first here is the cheaper path, not the slower one.
