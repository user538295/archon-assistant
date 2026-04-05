# The optimal local-first RAG stack for Archon

**LanceDB + Docling + ModernBERT + FastMCP is the strongest combination for a production-quality, fully offline RAG MCP server.** This stack delivers native hybrid search without external services, handles every required file format under MIT licensing, and exposes tools to Claude Code with minimal boilerplate. The architecture avoids the dependency bloat of framework-coupled solutions while matching or exceeding their retrieval quality. What follows is a component-by-component breakdown with concrete version numbers, integration patterns, and the reasoning behind each choice.

---

## LanceDB wins the vector store comparison decisively

**LanceDB v0.30.0** is the only local-first vector store that natively combines BM25 keyword search, vector ANN, and built-in rerankers in a single embedded library — no server process, no Docker, no subprocess.

Its hybrid search uses **Tantivy** (a Rust full-text search engine) under the hood, exposed through a single API call: `table.search("query", query_type="hybrid")`. You get BM25 scoring + vector similarity + automatic fusion (RRF, linear combination, or cross-encoder reranking) without managing two separate systems. The full async Python API (`lancedb.connect_async()` → `AsyncTable` → `AsyncHybridQuery`) supports Python **3.10–3.13** natively, with all query types available as async methods.

The **Lance columnar format** provides the best crash safety of any option evaluated. Every write creates an immutable version (append-only, Arrow-based), so an interrupted write simply doesn't commit — previous versions remain intact. Built-in automatic versioning enables rollback to any prior state, and the files-on-disk approach makes backup trivial.

The alternatives fall short in specific ways:

- **ChromaDB** lacks native local hybrid search. Its new sparse vector/BM25 API targets Chroma Cloud; the local `PersistentClient` doesn't fully support it. It also has no native async API, requiring `asyncio.to_thread()` wrappers.
- **Qdrant local mode** caps at roughly **20,000 points** — a hard ceiling that a growing knowledge base will hit. Its async API is excellent, but "local mode" is explicitly positioned for development, not production.
- **Weaviate embedded** downloads a **~100MB Go binary** and runs it as a subprocess via `subprocess.Popen`. Despite the name, it's not truly embedded — you inherit port management, process supervision, and ~200MB memory overhead for the Go server.

For standalone BM25 outside LanceDB, **BM25S** is the clear choice — up to **500× faster than rank_bm25**, with disk-persistent memory-mapped indices, multiple BM25 variants, and active maintenance. rank_bm25 hasn't been updated since 2022 and Whoosh is effectively dead. Tantivy's Python bindings lack pre-built wheels for Python 3.12+. But since LanceDB wraps Tantivy internally, you get Tantivy performance for free.

| Vector store | Native hybrid | Async API | Python 3.12 | Truly embedded | License |
|---|---|---|---|---|---|
| **LanceDB** | ✅ BM25+vector+reranker | ✅ Full | ✅ | ✅ No server | Apache 2.0 |
| ChromaDB | ⚠️ Cloud-focused | ❌ | ✅ | ✅ | Apache 2.0 |
| Qdrant local | ✅ Sparse+dense | ✅ Full | ✅ | ⚠️ 20K limit | Apache 2.0 |
| Weaviate embedded | ✅ Mature | ✅ | ✅ | ❌ Go subprocess | BSD-3 |

---

## ModernBERT is the right embedding model for code-heavy knowledge bases

**`nomic-ai/modernbert-embed-base`** (**139M params, 768 dims, 8,192-token context, Apache 2.0**) is the top pick for mixed code + documentation + prose content. Built on the ModernBERT architecture — which replaces BERT-era limitations with RoPE positional encoding, GeGLU activations, Flash Attention 2, and alternating global/local attention — it achieves measurably better code benchmark scores than older models (CSN: 56.4, SQA: 73.6) while running **4× faster** than DeBERTa-based alternatives. The 8,192-token context handles entire code files and long documentation sections without truncation. Matryoshka representation learning lets you truncate embeddings from 768 → 256 dims with ~98% performance retained, saving storage when needed.

Memory footprint is approximately **500MB**, running comfortably on CPU with sub-second latency for single queries. On GPU, inference is near-instant. For asyncio integration, `sentence-transformers` `.encode()` is synchronous — wrap it in `asyncio.to_thread()` or use a `ThreadPoolExecutor`.

**Runner-up: `BAAI/bge-m3`** (568M params, MIT license) if you want a single model that produces dense, sparse, AND multi-vector (ColBERT-style) representations simultaneously. Its built-in sparse retrieval effectively acts as an embedding-level BM25, which could simplify the pipeline. The trade-off is **~4× higher memory** and slower inference.

**Budget option: `BAAI/bge-base-en-v1.5`** (110M params) delivers strong quality at minimal footprint, with 512-token context and MIT license. Best if RAM is severely constrained.

For optional cloud enhancement, **Voyage code-2** is the standout code-specialized API, and **OpenAI text-embedding-3-small** ($0.02/M tokens) provides a solid general-purpose fallback. Consider ONNX export of the local model for a **2–3× CPU speedup** in production.

---

## BAAI/bge-reranker-v2-m3 matches commercial reranker quality at zero cost

**`BAAI/bge-reranker-v2-m3`** (568M params, Apache 2.0) is the strongest open-weight cross-encoder reranker available. Multiple independent evaluations confirm it matches Cohere Rerank quality. For a realistic scenario of reranking **20 candidates** from hybrid search:

- **CPU latency**: ~8ms per query-document pair × 20 = **~160ms total** — acceptable for a personal knowledge base
- **GPU latency**: **~20ms total** with FP16 (~1.2GB VRAM)
- **Memory**: ~1.2GB in FP16, ~2.2GB in FP32

The recommended retrieval pipeline is: **retrieve top-20 from hybrid search → rerank with bge-reranker-v2-m3 → return top-3 to 5 to the LLM.** Multiple studies confirm reranking improves retrieval quality by **20–48%** over embedding-only search, because cross-encoders examine the full query-document interaction rather than comparing compressed fixed-size vectors. One practitioner reported Hit Rate improving from ~85% to ~93% and MRR from ~78% to ~87% after adding reranking.

For prototyping or CPU-constrained environments, **`cross-encoder/ms-marco-MiniLM-L-6-v2`** (22M params, ~90MB) processes 100 documents in 1.34s on CPU — use it to validate that reranking helps your pipeline before committing to the heavier model.

Cloud reranker APIs worth considering as optional enhancements: **Cohere Rerank 3.5** ($1/1K searches, production-proven) and **Voyage Rerank 2.5** ($0.05/1K queries, lowest latency at ~50ms).

---

## Docling is the universal parser — MarkItDown complements for speed

**Docling (IBM)** is the clear winner for universal document parsing. It handles **all required formats** — PDF, DOCX, PPTX, XLSX, HTML, Markdown, images — with MIT licensing and AI-powered layout analysis (DocLayNet) and table structure recognition (TableFormer). Its own PDF parser (`docling-parse`) avoids the **AGPL licensing trap of PyMuPDF**. Output is a unified `DoclingDocument` that exports to Markdown, HTML, or JSON, making it ideal for downstream chunking. The library is hosted by the LF AI & Data Foundation, has **~27K+ GitHub stars**, and actively ships updates (v2.80.0 as of recent).

**MarkItDown (Microsoft)** (**74K+ GitHub stars**, MIT, v0.1.5) is an excellent lightweight complement for Office formats. Its API is dead simple (`md.convert("file.xlsx")`) and it handles DOCX, XLSX, PPTX, HTML, CSV, and JSON well. However, its **PDF conversion success rate is only ~25%** for complex documents. Use it as a fast path for Office formats; fall back to Docling for PDFs and complex layouts.

For HTML specifically, **trafilatura** achieves **F1 = 0.945** in article extraction benchmarks versus BeautifulSoup's 0.665 — it strips boilerplate (navigation, ads, footers) automatically.

The parsing strategy by format:

| Format | Primary parser | Fallback | Notes |
|---|---|---|---|
| PDF | Docling | pymupdf4llm (if AGPL ok) | Docling avoids AGPL; pymupdf4llm gives best Markdown |
| DOCX | Docling | python-docx + custom conversion | Docling wraps python-docx with structure awareness |
| XLSX/CSV | Docling or MarkItDown | openpyxl + pandas | Convert to Markdown tables with headers in every chunk |
| PPTX | Docling | python-pptx | Slide-level chunking |
| HTML | trafilatura | BeautifulSoup + markdownify | trafilatura for web HTML; BS4 for structured local HTML |
| Markdown | Direct read | — | Parse headers for structure-aware chunking |
| TXT/code | Direct read | — | Keep code blocks intact |

**Avoid Unstructured.io open-source** — their own documentation states it's "not designed for production scenarios" with "significantly decreased performance" compared to their paid API.

---

## Recursive chunking at 512 tokens is the benchmark-validated default

The **FloTorch 2026 benchmark** (50 papers, 905K tokens) found recursive character splitting achieved **69% end-to-end accuracy** — the top score among all strategies tested. Semantic chunking scored only **54%**, producing fragments averaging just 43 tokens that were too short for useful retrieval. Vectara's peer-reviewed **NAACL 2025 study** confirmed that fixed-size/recursive chunking consistently outperformed semantic chunking on realistic document sets.

The recommended configuration: **512 tokens, 50–100 token overlap, structure-aware splitting at Markdown headers**. The pipeline converts everything to Markdown first (via Docling), then applies a two-stage split — first at heading boundaries (`#`, `##`, etc.), then recursive character splitting within sections that exceed 512 tokens. Code blocks and tables are kept intact as single chunks.

**Chonkie** (MIT license) is the best standalone chunking library — 10× lighter than LangChain's text splitters, with recursive, semantic, code-aware, and table-aware chunkers plus a pipeline API:

```python
from chonkie import RecursiveChunker

chunker = RecursiveChunker(
    tokenizer="cl100k_base",
    chunk_size=512,
    chunk_overlap=64,
    recipe="markdown"  # Respects headers and code fences
)
chunks = chunker.chunk(markdown_text)
```

Content-type-specific tuning matters. Tables should never be split across chunks — each logical table becomes one chunk with a descriptive header (source file, sheet name, table description). Spreadsheets are chunked as Markdown tables in groups of 20–50 rows, with column headers repeated in every chunk. Code documentation gets **512–1024 token** chunks to keep function signatures and docstrings together.

---

## FastMCP 3.x is the only serious choice for the MCP server

**FastMCP 3.1.1** powers approximately **70% of MCP servers across all languages**, with ~1M downloads/day and 15K+ GitHub stars. It provides native asyncio, decorator-based tool registration, automatic JSON Schema generation from Python type hints, progress tracking via MCP Context, and all three transports (STDIO, Streamable HTTP, SSE). Python 3.12+ is fully supported.

Building a raw aiohttp JSON-RPC server would require implementing the entire MCP lifecycle (initialize → capability negotiation → tool dispatch → progress → cancellation) from scratch — weeks of work that FastMCP handles in five lines. The official Anthropic MCP Python SDK (`mcp` package v1.7.1) includes a frozen FastMCP 1.0; the standalone FastMCP 3.x has evolved far beyond it.

For **Claude Code integration**, use **STDIO transport** as the primary channel. Claude Code launches the server as a child process — zero network configuration, lowest latency, process-isolated security. Register it via `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "archon-search": {
      "command": "python",
      "args": ["-m", "archon.mcp_server"],
      "env": { "RAG_DB_PATH": "./rag_data" }
    }
  }
}
```

The recommended tool surface area balances completeness with simplicity — expose **7 tools** covering the full RAG lifecycle:

- **`search(query, collection?, top_k?, filters?)`** — hybrid vector+BM25 search with reranking, returning ranked results with text, source path, and relevance score. Hide all hybrid search tuning parameters; the LLM doesn't need to set vector weights.
- **`search_with_context(query, collection?, context_window?)`** — returns surrounding chunks for fuller context (the chunk before and after each match).
- **`ingest_file(path, collection?)`** — ingest a single file, returning `{doc_id, chunks_created, status}`.
- **`ingest_directory(path, glob_pattern?, collection?)`** — bulk ingest with MCP progress notifications via `ctx.report_progress()`.
- **`list_collections()`** — enumerate available collections with document counts.
- **`list_documents(collection?, limit?)`** — list indexed documents with metadata.
- **`delete_document(doc_id, collection?)`** — remove a document and all its chunks.

All tools should be `async def`, return structured dicts (never raise unhandled exceptions), and use `asyncio.to_thread()` for CPU-intensive work (chunking, embedding generation). Log exclusively to stderr — stdout is reserved for JSON-RPC messages in STDIO mode.

---

## The complete recommended stack

Here is the full architecture, with every component chosen for local-first operation, asyncio compatibility, and minimal operational complexity:

| Layer | Component | Version | License | Memory |
|---|---|---|---|---|
| **Vector store + hybrid search** | LanceDB | 0.30.0 | Apache 2.0 | ~50MB + index |
| **Embedding model** | nomic-ai/modernbert-embed-base | — | Apache 2.0 | ~500MB |
| **Reranker** | BAAI/bge-reranker-v2-m3 | — | Apache 2.0 | ~1.2GB (FP16) |
| **Universal parser** | Docling (IBM) | 2.80.0 | MIT | ~200MB + models |
| **Office fast-path** | MarkItDown (Microsoft) | 0.1.5 | MIT | ~20MB |
| **HTML extraction** | trafilatura | ≥1.8.0 | Apache 2.0 | ~30MB |
| **Chunking** | Chonkie | latest | MIT | ~10MB |
| **MCP server** | FastMCP | 3.1.1 | MIT | ~5MB |

**Total baseline memory**: ~2GB for embedding model + reranker loaded simultaneously. Fits comfortably in 4GB RAM. On a machine with a GPU, the reranker drops to ~20ms latency for top-20 reranking.

The retrieval pipeline executes as: **Docling parses → Chonkie chunks at 512 tokens → ModernBERT embeds → LanceDB stores vectors + creates FTS index → hybrid query (BM25 + vector via RRF) retrieves top-20 → bge-reranker-v2-m3 reranks → top-5 returned to Claude Code via FastMCP**.

## Conclusion

Three design decisions dominate the quality of this stack. First, **LanceDB's native hybrid search eliminates the most common RAG failure mode** — pure vector search missing exact keyword matches for function names, error codes, and technical terms. The Tantivy-powered BM25 catches what embeddings miss, and RRF fusion combines both signals without manual weight tuning. Second, **converting everything to Markdown before chunking** (via Docling) creates a uniform intermediate representation where headers, tables, and code blocks are structurally explicit — enabling intelligent splitting that respects document semantics rather than arbitrary character boundaries. Third, **the reranker is not optional for mixed-content knowledge bases** — heterogeneous content (code, prose, tables, spreadsheets) means no single embedding model captures all relevance signals equally well, and a cross-encoder examining the full query-document pair compensates for this systematically. The entire stack runs fully offline, requires zero infrastructure beyond `pip install`, and integrates with Claude Code through a single `.mcp.json` file.