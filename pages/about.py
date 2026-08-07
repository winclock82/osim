import streamlit as st

st.title("About OSIM")
st.caption(
    "Operational Scheduling Information Manager — a smarter way to navigate "
    "batch-job schedules and dependencies."
)

# Quick facts row — every number here reflects the actual implementation.
c1, c2, c3 = st.columns(3)
c1.metric("User Query Modes", "3")
c2.metric("Ingestion Passes", "3")
c3.metric("Fields Auto-Derived / Job", "8")

st.divider()

# ── Project scope ─────────────────────────────────────────────────────────────
st.header("📌 Project Scope")
st.markdown(
    """
    Enterprise batch operations are documented in **Excel operating manuals** — one row per job,
    with the scheduling rules and dependencies written as free-form text. As the number of jobs
    grows into the hundreds, this format makes it slow and error-prone to answer everyday
    operational questions such as *"What runs this weekend?"* or *"What breaks downstream if this
    job fails?"*

    **OSIM** replaces that manual lookup with an AI-assisted knowledge base. It ingests the
    existing Excel manuals **as-is**, uses large language models to convert the free-text
    scheduling rules into structured data, and lets operations staff explore schedules, trace
    dependencies, and ask natural-language questions — all from a single web interface.
    """
)

# ── Objectives ────────────────────────────────────────────────────────────────
st.header("🎯 Objectives")
st.markdown(
    """
    - **Centralise** scattered Excel scheduling data into one searchable, always-available store.
    - **Make dependencies visible** — trace a job's full upstream and downstream chain at a glance.
    - **Lower the barrier to answers** — let non-technical staff query schedules in plain English.
    - **Stay grounded** — every answer is drawn strictly from the uploaded manuals; the assistant
      does not invent operational details.
    - **Respect real-world calendars** — apply public-holiday exclusions and run-date boundaries
      automatically.
    """
)

# ── Features ──────────────────────────────────────────────────────────────────
st.header("🧩 Key Features")

col_admin, col_user = st.columns(2)

with col_admin:
    with st.container(border=True):
        st.subheader("🛠️ For Administrators")
        st.markdown(
            """
            - **Excel ingestion** — upload an operating manual (`.xlsx`); flexible column-alias
              matching handles different manual formats.
            - **LLM field extraction** — free-text scheduling instructions are converted into
              8 structured fields per job (frequency, run days, times, dependencies, and more).
            - **Dependency inheritance** — jobs missing schedule details inherit them from the
              jobs they depend on.
            - **Incremental re-indexing** — a content hash means only new or changed jobs are
              re-processed; removed jobs are pruned.
            - **Store management** — inspect parsed job data, review an indexing history, and
              remove systems on demand.
            """
        )

with col_user:
    with st.container(border=True):
        st.subheader("🔎 For Users")
        st.markdown(
            """
            - **🗓️ Job Schedule Explorer** — pick a date range and see exactly which jobs run,
              with public-holiday and run-date checks applied automatically.
            - **🔗 Job Dependency Explorer** — select a job and view its full upstream and
              downstream dependency trees.
            - **💬 Open Query** — ask free-form questions and get answers grounded in the indexed
              jobs, complete with the source records used.
            """
        )

# ── Data sources ──────────────────────────────────────────────────────────────
st.header("🗂️ Data Sources")
st.markdown(
    """
    | Source | Description | Role in OSIM |
    |---|---|---|
    | **Operating manuals (`.xlsx`)** | The authoritative batch-job records, uploaded by admins. | Primary input — parsed, enriched, and indexed. |
    | **Public-holiday calendar (`holidays.json`)** | Singapore public holidays (source: Ministry of Manpower). | Applied during schedule queries to exclude holiday runs. |
    | **OpenAI models** | GPT-4o, GPT-4o-mini and `text-embedding-3-small`. | Field extraction, embeddings, and question answering. |

    > No job data is bundled with the application. All operational data is supplied at runtime by
    > administrators and held in an in-memory vector store for the duration of the session.
    """
)

# ── Technology ────────────────────────────────────────────────────────────────
st.header("⚙️ Technology Stack")
st.markdown(
    """
    - **Frontend / App** — Streamlit (multi-page)
    - **Retrieval-Augmented Generation** — LangChain + Chroma vector store
    - **Language & Embedding Models** — OpenAI GPT-4o · GPT-4o-mini · text-embedding-3-small
    - **Data Handling** — pandas + openpyxl (Excel parsing)
    - **Security** — bcrypt-hashed credentials with role-based access (admin / user)
    """
)

# ── Team (edit before submission) ─────────────────────────────────────────────
st.header("👥 Project Team")
st.markdown(
    """
    This proof-of-concept was developed as a web-based application based on problem statements from an agency (Project Type 1).

    - **Kenneth NG** — Developer, CPFB
    - **Sandar WIN** — Developer, CPFB 
    - **Dorris TAN** — Developer, CPFB 
    
    """
)

st.divider()
st.caption(
    "OSIM is a proof-of-concept prototype. Job information and AI-generated responses are not "
    "intended for production use — always verify against official system documentation. "
    "See the **Methodology** page for how the application works."
)
