import streamlit as st

st.title("Methodology")
st.caption(
    "How OSIM turns Excel operating manuals into a queryable, dependency-aware knowledge base — "
    "the data flows and implementation details behind each use case."
)

st.divider()

# ── Architecture overview ─────────────────────────────────────────────────────
st.header("🏗️ System Architecture")
st.markdown(
    """
    OSIM has two sides that share a single **in-memory Chroma vector store**:

    - **Administrators** upload Excel operating manuals. Each job is enriched by an LLM into
      structured scheduling data and indexed into the store.
    - **Users** query that store through three modes. Retrieval and calendar logic run in Python;
      the LLM is used only for reasoning and natural-language answers, always grounded in the
      retrieved job records.
    """
)

_ARCH_DOT = """
digraph OSIM {
  rankdir=LR;
  bgcolor="transparent";
  node [shape=box, style="rounded,filled", fontname="Helvetica", color="#4A6FA5", fontcolor="#1a1a1a"];
  edge [fontname="Helvetica", fontsize=10, color="#8892a6", fontcolor="#5a6472"];

  admin [label="Admin\\n(uploads .xlsx)", fillcolor="#FFE7C7"];
  user  [label="User\\n(asks questions)", fillcolor="#FFE7C7"];
  app   [label="OSIM\\nStreamlit App", fillcolor="#E6EEFB"];
  llm   [label="OpenAI\\nGPT-4o • GPT-4o-mini\\nEmbeddings", fillcolor="#DFF3E6"];
  store [label="Chroma\\nVector Store\\n(in-memory)", fillcolor="#EFE4FB"];
  hol   [label="holidays.json\\n(SG public holidays)", fillcolor="#FBE1E1"];

  admin -> app   [label="operating manual"];
  app   -> llm   [label="derive + embed"];
  app   -> store [label="index"];
  user  -> app   [label="query"];
  store -> app   [label="retrieve"];
  hol   -> app   [label="holiday context"];
  app   -> llm   [label="reason / answer"];
}
"""
st.graphviz_chart(_ARCH_DOT, use_container_width=True)

st.divider()

# ── Data model ────────────────────────────────────────────────────────────────
st.header("🧱 The Job Document")
st.markdown(
    """
    Every job is stored as a single JSON document with three parts: the original `metadata` from
    the manual, the LLM-`derived` structured schedule, and the `job_id`. Storing the raw and
    derived data together keeps answers **traceable back to the source record**.
    """
)
st.code(
    """{
  "job_id": "DKSD001",
  "metadata": {
    "function_of_srs": "Daily settlement extract",
    "title_of_run_series": "DKS Daily Settlement",
    "job_frequency": "Daily",
    "job_name": "DKSD001",
    "job_run_mode": "Batch",
    "estimated_run_time_minutes": 12,
    "estimated_volume_of_records": "5,000 - 8,000",
    "scheduling_instructions": "Run every business day at 02:00, exclude public holidays",
    "description": "Extracts daily settlement records for downstream reconciliation",
    "priority_level": 2,
    "server_name": "BATCHSVR01",
    "script": "run_dksd001.sh",
    "first_run_date": "2024-01-01",
    "last_run_date": null,
    "remarks": "Feeds DKSW003"
  },
  "derived": {
    "frequency": "daily",
    "days_of_week": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "day_of_month": null,
    "month": null,
    "times": ["02:00"],
    "exclude_public_holiday": true,
    "dependencies": [],
    "notes": null
  }
}""",
    language="json",
)

st.divider()

# ── Use-case flowcharts ───────────────────────────────────────────────────────
st.header("🔄 Process Flows by Use Case")
st.markdown("Each tab shows the end-to-end data flow for one use case.")

tab_ingest, tab_sched, tab_dep, tab_query = st.tabs(
    [
        "① Data Ingestion (Admin)",
        "② Schedule Explorer",
        "③ Dependency Explorer",
        "④ Open Query (RAG)",
    ]
)

# ── Use case 1: ingestion ──
with tab_ingest:
    st.subheader("Data Ingestion Pipeline")
    st.markdown(
        "A three-pass pipeline converts a raw Excel manual into indexed, dependency-aware job "
        "documents."
    )
    _INGEST_DOT = """
    digraph ingest {
      rankdir=TB;
      bgcolor="transparent";
      node [shape=box, style="rounded,filled", fontname="Helvetica", color="#4A6FA5", fontcolor="#1a1a1a", fillcolor="#E6EEFB"];
      edge [fontname="Helvetica", fontsize=10, color="#8892a6", fontcolor="#5a6472"];

      a [label="Admin uploads .xlsx\\n+ enters System Code (e.g. DKS)", fillcolor="#FFE7C7"];
      b [label="Parse Excel (pandas / openpyxl)\\nnormalise headers • resolve column aliases"];
      c [label="Keep rows where Job Name\\nstarts with the System Code"];
      d [label="Pass 1 — Derive  (GPT-4o-mini, batches of 5)\\nfree-text rules -> 8 structured schedule fields"];
      e [label="Pass 2 — Inheritance\\nfill missing fields from upstream dependency"];
      f [label="Pass 3 — Build documents\\njob JSON + row_hash + system_code"];
      g [label="Upsert into Chroma\\nadd new • replace changed (hash) • delete removed"];
      h [label="Store updated\\nsummary table + indexing log", fillcolor="#DFF3E6"];
      err [label="No matching jobs ->\\nprompt to check System Code", shape=note, fillcolor="#FBE1E1"];

      a -> b -> c;
      c -> d [label="matches"];
      c -> err [label="none"];
      d -> e -> f -> g -> h;
    }
    """
    st.graphviz_chart(_INGEST_DOT, use_container_width=True)
    st.markdown(
        """
        **Implementation details**
        - **Column-alias resolution** maps varied manual headings (e.g. *"Est. Run Time (Min)"* vs
          *"Estimated Run Time (minutes)"*) to a canonical field set.
        - **Pass 1 — Derive:** jobs are sent to **GPT-4o-mini** in batches of 5 with a strict JSON
          schema, turning free-text *Job Frequency* and *Scheduling Instructions* into structured
          fields. Trailing `@` markers on dependency IDs are stripped.
        - **Pass 2 — Inheritance:** a job that omits its own schedule (common for dependent jobs)
          inherits `frequency`, run days, times and holiday rules from the job it depends on.
        - **Pass 3 — Incremental upsert:** each job carries a `row_hash`. Unchanged jobs are
          skipped, changed jobs are replaced, and jobs no longer present in the manual are deleted
          — so re-uploading a manual keeps the store in sync without duplicates.
        """
    )

# ── Use case 2: schedule explorer ──
with tab_sched:
    st.subheader("Job Schedule Explorer")
    st.markdown(
        "Given a date range, OSIM combines deterministic Python filtering with LLM reasoning to "
        "decide which jobs actually run."
    )
    _SCHED_DOT = """
    digraph sched {
      rankdir=TB;
      bgcolor="transparent";
      node [shape=box, style="rounded,filled", fontname="Helvetica", color="#4A6FA5", fontcolor="#1a1a1a", fillcolor="#E6EEFB"];
      edge [fontname="Helvetica", fontsize=10, color="#8892a6"];

      a [label="User selects Start + End date", fillcolor="#FFE7C7"];
      b [label="Validate range (end >= start)"];
      c [label="Python pre-filter\\ndrop jobs outside first / last run date"];
      d [label="Strip date-boundary fields\\nso the LLM cannot misuse them"];
      e [label="GPT-4o — 2-phase filter\\nPhase 1: frequency   Phase 2: weekday"];
      f [label="Python post-filter\\npublic-holiday exclusion (holidays.json)"];
      g [label="Qualifying jobs table\\n+ per-job reasoning trace", fillcolor="#DFF3E6"];

      a -> b -> c -> d -> e -> f -> g;
    }
    """
    st.graphviz_chart(_SCHED_DOT, use_container_width=True)
    st.markdown(
        """
        **Implementation details**
        - **Deterministic where it matters:** run-date boundaries and public-holiday exclusions are
          computed in Python, not left to the model. Boundary dates are even stripped from the
          payload so the LLM cannot second-guess them.
        - **LLM for judgement:** **GPT-4o** applies a two-phase filter — *frequency* (daily / weekly
          / monthly / yearly) then *weekday* match — and is given the exact dates and weekdays in
          the range to prevent calendar hallucination.
        - **Explainability:** the model returns a per-job reasoning trace (Phase 1 / Phase 2 /
          result) shown in an expandable panel alongside the results table.
        """
    )

# ── Use case 3: dependency explorer ──
with tab_dep:
    st.subheader("Job Dependency Explorer")
    st.markdown(
        "A purely deterministic feature — no LLM call — that reconstructs dependency chains from "
        "the `derived.dependencies` field."
    )
    _DEP_DOT = """
    digraph dep {
      rankdir=TB;
      bgcolor="transparent";
      node [shape=box, style="rounded,filled", fontname="Helvetica", color="#4A6FA5", fontcolor="#1a1a1a", fillcolor="#E6EEFB"];
      edge [fontname="Helvetica", fontsize=10, color="#8892a6"];

      a [label="User selects a Job", fillcolor="#FFE7C7"];
      b [label="Load all jobs from Chroma\\nbuild job_id -> derived lookup"];
      c [label="Upstream traversal (BFS)\\nfollow derived.dependencies"];
      d [label="Downstream traversal (BFS)\\nfind jobs that depend on it"];
      e [label="Render indented dependency trees\\n+ full job JSON", fillcolor="#DFF3E6"];

      a -> b;
      b -> c;
      b -> d;
      c -> e;
      d -> e;
    }
    """
    st.graphviz_chart(_DEP_DOT, use_container_width=True)
    st.markdown(
        """
        **Implementation details**
        - **Upstream** = the jobs a selected job depends on; **downstream** = the jobs that depend
          on it. Both are walked breadth-first with a visited set to guard against cycles.
        - **Depth-indented rendering** shows the full chain, annotating each edge with any timing
          `notes` (e.g. *"15 mins after DKSD001 completed"*).
        - Because it reads directly from indexed data, results are **exact and reproducible** — no
          model inference involved.
        """
    )

# ── Use case 4: open query ──
with tab_query:
    st.subheader("Open Query (Retrieval-Augmented Generation)")
    st.markdown(
        "A grounded chat interface: relevant jobs are retrieved by semantic search, then GPT-4o "
        "answers strictly from them."
    )
    _RAG_DOT = """
    digraph rag {
      rankdir=TB;
      bgcolor="transparent";
      node [shape=box, style="rounded,filled", fontname="Helvetica", color="#4A6FA5", fontcolor="#1a1a1a", fillcolor="#E6EEFB"];
      edge [fontname="Helvetica", fontsize=10, color="#8892a6"];

      a [label="User asks a free-text question", fillcolor="#FFE7C7"];
      b [label="Embed query • similarity_search(k)\\nChroma returns top-k job chunks"];
      c [label="Build grounded prompt\\ncontext = retrieved job JSON"];
      d [label="GPT-4o (streaming)\\nanswer strictly from context"];
      e [label="Streamed answer + source chunks", fillcolor="#DFF3E6"];

      a -> b -> c -> d -> e;
    }
    """
    st.graphviz_chart(_RAG_DOT, use_container_width=True)
    st.markdown(
        """
        **Implementation details**
        - **Semantic retrieval:** the question is embedded with `text-embedding-3-small` and the
          top-*k* job documents are pulled from Chroma (*k* is user-adjustable).
        - **Grounding guardrail:** the system prompt instructs **GPT-4o** to answer *only* from the
          retrieved records and to say so when the answer is not present — reducing hallucination.
        - **Transparency:** every answer is accompanied by the exact source job records used, and
          conversation history is preserved within the session.
        """
    )

st.divider()

# ── Design decisions ──────────────────────────────────────────────────────────
st.header("🧭 Design Decisions & Guardrails")
st.markdown(
    """
    - **Hybrid Python + LLM logic** — calendar maths, date boundaries and holiday rules are handled
      deterministically in Python; the LLM is reserved for language and judgement tasks. This keeps
      scheduling answers reliable.
    - **Grounded generation** — prompts constrain the model to the retrieved / provided job data,
      and sources are surfaced so answers stay auditable.
    - **Right model for the job** — the cheaper **GPT-4o-mini** handles high-volume field
      extraction; **GPT-4o** handles the more demanding reasoning and Q&A.
    - **Idempotent ingestion** — content hashing makes re-uploading a manual safe and duplicate-free.
    - **Shared in-memory store** — a single cached vector store is shared across sessions, so all
      users see the same indexed data. (As a prototype, the store is not persisted to disk and is
      rebuilt on restart.)
    - **Role-based access** — administrators manage data; users only query it.
    """
)

# ── Models table ──────────────────────────────────────────────────────────────
st.header("🤖 Models Used")
st.markdown(
    """
    | Task | Model | Why |
    |---|---|---|
    | Schedule-field extraction | `gpt-4o-mini` | High volume, structured JSON — fast and cost-efficient. |
    | Schedule reasoning & Open Query | `gpt-4o` | Stronger reasoning for calendar logic and grounded answers. |
    | Document & query embeddings | `text-embedding-3-small` | Efficient semantic retrieval from the vector store. |
    """
)

st.divider()
st.caption(
    "OSIM is a proof-of-concept prototype. AI-generated responses may be inaccurate or incomplete — "
    "always verify critical job information against official system documentation."
)
