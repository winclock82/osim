import hashlib
import json
import pandas as pd
import streamlit as st
from io import BytesIO
from openai import OpenAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from auth import is_logged_in, current_user, logout
from store_utils import get_shared_store, get_store_summary


def _row_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


_DERIVE_SYSTEM_PROMPT = """
You are a scheduling data extraction assistant. Given job scheduling fields, return ONLY a JSON object with this structure:
{
  "frequency": "<daily|weekly|monthly|yearly|on_request>",
  "days_of_week": ["<Mon|Tue|Wed|Thu|Fri|Sat|Sun>"],
  "day_of_month": <integer or null>,
  "month": <integer 1-12 or null>,
  "times": ["<HH:MM in 24h>"],
  "exclude_public_holiday": <true/false>,
  "dependencies": ["<job_id>"],
  "notes": "<dependency timing or other scheduling notes, or null>"
}
Rules:
- Infer all fields from the provided job_frequency and scheduling_instructions.
- frequency: normalise to one of daily/weekly/monthly/yearly/on_request.
- days_of_week: list only the days the job actually runs; [] if not day-specific.
- day_of_month: integer day number for monthly jobs, null otherwise.
- month: integer month number for yearly jobs, null otherwise.
- times: all run times in 24h HH:MM format; [] if unspecified.
- exclude_public_holiday: true if scheduling instructions exclude public holidays.
- dependencies: job IDs this job must wait for before running; [] if none. Strip any trailing '@' from job IDs (e.g. DKSW003@ → DKSW003).
- notes: free-text for dependency timing (e.g. "15 mins after DKSD001 completed") or null.
- Use null for missing scalar values, [] for missing list values.
- Output ONLY the JSON object, no markdown, no explanation.
"""

_METADATA_COLUMN_ALIASES = {
    "function_of_srs":             ["Function of SRS", "Function No"],
    "title_of_run_series":         ["Title of Run Series"],
    "job_frequency":               ["Job Frequency"],
    "job_name":                    ["Job Name"],
    "job_run_mode":                ["Job Run Mode"],
    "estimated_run_time_minutes":  ["Estimated Run Time (minutes)", "Est. Run Time (Min)", "Est. Run Time (minutes)"],
    "estimated_volume_of_records": ["Estimated Volume of records to be processed", "Est. Volume (Avg - Max)", "Estimated Volume of Records"],
    "scheduling_instructions":     ["Scheduling Instructions"],
    "description":                 ["Description of the batch job", "Description of Job"],
    "priority_level":              ["Problem Ticket Management Priority Level", "Priority Level"],
    "server_name":                 ["Server Name", "Server / Workstation Name"],
    "script":                      ["Script"],
    "first_run_date":              ["First Run Date", "First Run or effective date"],
    "last_run_date":               ["Last Run Date"],
    "remarks":                     ["Remarks"],
}

_INT_FIELDS  = {"estimated_run_time_minutes", "priority_level"}
_DATE_FIELDS = {"first_run_date", "last_run_date"}

_BATCH_SIZE = 5


def _resolve_column_map(columns: list[str]) -> dict[str, str]:
    """For each metadata key, find the first alias that exists in the sheet's columns."""
    col_set = set(columns)
    resolved = {}
    for key, aliases in _METADATA_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in col_set:
                resolved[key] = alias
                break
    return resolved


def _build_metadata(row: pd.Series, col_map: dict[str, str]) -> dict:
    meta = {}
    for key in _METADATA_COLUMN_ALIASES:
        col = col_map.get(key)
        val = row.get(col) if col else None
        is_null = pd.isna(val) if not isinstance(val, str) else not str(val).strip()
        if is_null:
            meta[key] = None
        elif key in _INT_FIELDS:
            try:
                meta[key] = int(float(val))
            except (ValueError, TypeError):
                meta[key] = None
        elif key in _DATE_FIELDS:
            meta[key] = str(val).strip()
        else:
            meta[key] = str(val).strip()
    return meta


def _derive_batch_with_llm(rows: list[tuple[str, pd.Series]], col_map: dict[str, str], client: OpenAI) -> list[dict]:
    freq_col  = col_map.get("job_frequency", "Job Frequency")
    sched_col = col_map.get("scheduling_instructions", "Scheduling Instructions")
    numbered = "\n\n".join(
        f"[{i+1}] job_frequency: {row.get(freq_col, '')} | "
        f"scheduling_instructions: {row.get(sched_col, '')}"
        for i, (_, row) in enumerate(rows)
    )
    batch_prompt = (
        f"Derive scheduling fields for the following {len(rows)} jobs. "
        f"Return a JSON object with a single key 'jobs' containing an array of {len(rows)} derived objects "
        f"in the same order as the input.\n\n{numbered}"
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _DERIVE_SYSTEM_PROMPT},
            {"role": "user", "content": batch_prompt},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)["jobs"]


def _apply_inheritance(derived_map: dict) -> dict:
    for _ in range(len(derived_map)):
        changed = False
        for derived in derived_map.values():
            deps = derived.get("dependencies", [])
            dep_sources = [derived_map[d] for d in deps if d in derived_map]
            if not dep_sources:
                continue
            src = dep_sources[0]

            for field, empty in [
                ("frequency", None),
                ("days_of_week", []),
                ("day_of_month", None),
                ("month", None),
                ("exclude_public_holiday", None),
            ]:
                if derived.get(field) in (None, empty):
                    val = src.get(field)
                    if val not in (None, empty):
                        derived[field] = val
                        changed = True

            if not derived.get("times") and src.get("times"):
                derived["times"] = src["times"]
                changed = True

    return derived_map


def collect_valid_rows(file, system_code: str) -> tuple[list[tuple[str, pd.Series]], dict[str, str]]:
    df = pd.read_excel(BytesIO(file.read()), engine="openpyxl")
    df = df.dropna(how="all").reset_index(drop=True)
    # Normalise column names: collapse internal whitespace/newlines, strip edges
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    col_map = _resolve_column_map(list(df.columns))
    job_name_col = col_map.get("job_name", "Job Name")
    rows = []
    for _, row in df.iterrows():
        job_name = str(row.get(job_name_col, "")).strip()
        if not job_name or job_name.lower() == "nan":
            continue
        if not job_name.upper().startswith(system_code):
            continue
        rows.append((job_name, row))
    return rows, col_map


def build_vectorstore(docs: list[Document]) -> Chroma:
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=st.secrets["OPENAI_API_KEY"],
    )
    return Chroma.from_documents(docs, embedding=embeddings)


def upsert_documents(vs: Chroma, new_docs: list[Document], system_code: str, all_incoming_job_names: set, is_last_batch: bool):
    new_by_job = {doc.metadata["job_name"]: doc for doc in new_docs}

    existing_ids, existing_metas = [], []
    for job_name in new_by_job:
        result = vs.get(where={"job_name": job_name})
        existing_ids.extend(result["ids"])
        existing_metas.extend(result["metadatas"])

    old_by_job = {
        meta["job_name"]: {"id": doc_id, "row_hash": meta["row_hash"]}
        for doc_id, meta in zip(existing_ids, existing_metas)
    }

    to_delete, to_add = [], []
    for job_name, new_doc in new_by_job.items():
        if job_name not in old_by_job:
            to_add.append(new_doc)
        elif new_doc.metadata["row_hash"] != old_by_job[job_name]["row_hash"]:
            to_delete.append(old_by_job[job_name]["id"])
            to_add.append(new_doc)

    if is_last_batch:
        system_existing = vs.get(where={"system_code": system_code})
        system_job_names_in_store = {meta["job_name"] for meta in system_existing["metadatas"]}
        for job_name in system_job_names_in_store - all_incoming_job_names:
            result = vs.get(where={"job_name": job_name})
            to_delete.extend(result["ids"])

    if to_delete:
        vs.delete(ids=to_delete)
    if to_add:
        vs.add_documents(to_add)

    return len(to_delete), len(to_add)


# --- Page access control ---
if not is_logged_in() or not current_user().lower().startswith("admin"):
    st.warning("Access denied. Please log in with an admin account.")
    st.stop()

with st.sidebar:
    if st.button("Logout"):
        logout()
        st.switch_page("pages/home.py")


# --- Page UI ---
st.title("Admin Page")
st.write(f"Welcome, **{current_user()}**!")

st.divider()

shared = get_shared_store()
st.subheader("📊 Jobs in Vector Store")
summary_placeholder = st.empty()


def render_summary():
    vs = shared["vectorstore"]
    if vs is None:
        summary_placeholder.info("No documents indexed yet. Upload an operating manual below to get started.")
        return
    df = get_store_summary(vs)
    df.insert(0, "Remove", False)
    with summary_placeholder.container():
        edited = st.data_editor(df, hide_index=True, use_container_width=True, key="summary_editor")
        if st.button("🗑️ Remove Selected", key="remove_selected"):
            to_remove = edited[edited["Remove"] == True]["System Code"].tolist()
            if not to_remove:
                st.warning("No systems selected.")
            else:
                if "index_log" not in st.session_state:
                    st.session_state.index_log = []
                for code in to_remove:
                    result = vs.get(where={"system_code": code})
                    if result["ids"]:
                        count = len(result["ids"])
                        vs.delete(ids=result["ids"])
                        st.session_state.index_log.append(f"🗑️ {code} — {count} job(s) removed")
                if not vs.get()["ids"]:
                    shared["vectorstore"] = None
                st.rerun()


def refresh_summary():
    """Lightweight read-only update used during indexing to avoid duplicate widget keys."""
    vs = shared["vectorstore"]
    if vs is None:
        summary_placeholder.info("No documents indexed yet. Upload an operating manual below to get started.")
    else:
        summary_placeholder.dataframe(get_store_summary(vs), hide_index=True, use_container_width=True)


render_summary()

st.divider()

if "clear_system_code" not in st.session_state:
    st.session_state.clear_system_code = False

if st.session_state.clear_system_code:
    st.session_state.clear_system_code = False
    st.session_state.system_code_input = ""

system_code = st.text_input("🔑 System Code (e.g. DKS)", key="system_code_input").strip().upper()

uploaded_file = st.file_uploader("📄 Upload an Operating Manual (.xlsx)", type=["xlsx"])

if uploaded_file:
    st.success(f"Loaded: {uploaded_file.name}")

    if st.button("Index Document"):
        if not system_code:
            st.error("Please enter a System Code before indexing.")
        else:
            if "index_log" not in st.session_state:
                st.session_state.index_log = []

            valid_rows, col_map = collect_valid_rows(uploaded_file, system_code)
            if not valid_rows:
                st.error(f"No jobs found with prefix '{system_code}'. Please check the system code and try again.")
            else:
                client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                total = len(valid_rows)
                all_incoming_names = {job_name.rstrip("@") for job_name, _ in valid_rows}

                # --- Pass 1: derive all jobs in batches ---
                progress = st.progress(0, text="Deriving scheduling fields...")
                derived_map = {}  # {clean_job_name: derived_dict}
                row_map = {}      # {clean_job_name: pd.Series}

                for i in range(0, total, _BATCH_SIZE):
                    batch = valid_rows[i : i + _BATCH_SIZE]
                    derived_batch = _derive_batch_with_llm(batch, col_map, client)
                    for (job_name, row), derived in zip(batch, derived_batch):
                        clean_job_name = job_name.rstrip("@")
                        derived_map[clean_job_name] = derived
                        row_map[clean_job_name] = row
                    progress.progress(
                        min(i + _BATCH_SIZE, total) / total,
                        text=f"Derived {min(i + _BATCH_SIZE, total)}/{total} jobs..."
                    )

                # --- Pass 2: inherit missing fields from dependencies ---
                progress.progress(1.0, text="Applying inheritance...")
                derived_map = _apply_inheritance(derived_map)

                # --- Pass 3: build documents and index in batches ---
                total_added = total_deleted = 0
                job_names = list(derived_map.keys())

                for i in range(0, total, _BATCH_SIZE):
                    batch_names = job_names[i : i + _BATCH_SIZE]
                    is_last = (i + _BATCH_SIZE) >= total
                    batch_docs = []

                    for job_name in batch_names:
                        row = row_map[job_name]
                        raw_text = " | ".join(f"{col}: {val}" for col, val in row.items())
                        doc_content = json.dumps({
                            "job_id": job_name,
                            "metadata": _build_metadata(row, col_map),
                            "derived": derived_map[job_name],
                        }, ensure_ascii=False)
                        batch_docs.append(Document(
                            page_content=doc_content,
                            metadata={
                                "job_name": job_name,
                                "row_hash": _row_hash(raw_text),
                                "system_code": system_code,
                            }
                        ))

                    if shared["vectorstore"] is None:
                        shared["vectorstore"] = build_vectorstore(batch_docs)
                        total_added += len(batch_docs)
                    else:
                        deleted, added = upsert_documents(shared["vectorstore"], batch_docs, system_code, all_incoming_names, is_last)
                        total_deleted += deleted
                        total_added += added

                    progress.progress(
                        min(i + _BATCH_SIZE, total) / total,
                        text=f"Indexed {min(i + _BATCH_SIZE, total)}/{total} jobs..."
                    )
                    refresh_summary()

                progress.empty()
                msg = f"✅ {uploaded_file.name} ({system_code}) — {total_added} job(s) added/updated, {total_deleted} job(s) removed"
                st.session_state.index_log.append(msg)
                st.session_state.clear_system_code = True
                st.rerun()
else:
    st.info("Upload operating manuals to enable jobs analysis Q&A.")

st.divider()

# Collapsable panel to inspect parsed job data in the vector store
if shared["vectorstore"] is not None:
    all_docs = shared["vectorstore"].get()
    system_codes = sorted({m.get("system_code", "") for m in all_docs["metadatas"]})
    with st.expander("🔎 View Parsed Job Data", expanded=False):
        filter_code = st.selectbox("Filter by System Code", ["All"] + system_codes, key="view_system_filter")
        for doc, meta in zip(all_docs["documents"], all_docs["metadatas"]):
            if filter_code != "All" and meta.get("system_code") != filter_code:
                continue
            with st.expander(f"📄 {meta.get('job_name', 'Unknown')} ({meta.get('system_code', '')})", expanded=False):
                try:
                    st.json(json.loads(doc))
                except Exception:
                    st.code(doc)

st.divider()

# Display a persistent log of all indexing actions performed in this session
if "index_log" in st.session_state and st.session_state.index_log:
    st.subheader("📋 Indexing History")
    for entry in st.session_state.index_log:
        st.write(entry)
