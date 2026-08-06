import streamlit as st
import pandas as pd
import json
from datetime import date, timedelta
from openai import OpenAI
from auth import is_logged_in, current_user, logout
from store_utils import get_shared_store, get_store_summary

if not is_logged_in() or not current_user().lower().startswith("user"):
    st.warning("Access denied. Please log in with a user account.")
    st.stop()

with st.sidebar:
    if st.button("Logout"):
        logout()
        st.switch_page("pages/home.py")

st.title("User Page")
st.write(f"Welcome, **{current_user()}**!")

st.divider()

shared = get_shared_store()
if shared["vectorstore"] is not None:
    st.subheader("📊 Jobs in Vector Store")
    st.dataframe(get_store_summary(shared["vectorstore"]), hide_index=True, use_container_width=True)
else:
    st.info("No documents indexed yet. Please contact your administrator.")

st.divider()

if "messages" not in st.session_state:
    st.session_state.messages = []

if "current_mode" not in st.session_state:
    st.session_state.current_mode = None

mode = st.selectbox(
    "Select a mode",
    ["🗓️ Job Schedule Explorer", "🔗 Job Dependency Explorer", "💬 Open Query"],
)

if mode != st.session_state.current_mode:
    st.session_state.messages = []
    st.session_state.pop("schedule_df", None)
    st.session_state.pop("schedule_query", None)
    st.session_state.pop("schedule_reasoning", None)
    st.session_state.current_mode = mode

st.subheader(mode)

_MODE_DESCRIPTIONS = {
    "🗓️ Job Schedule Explorer":   "Find all jobs scheduled to run within a selected date range, with public holiday and date boundary checks applied automatically.",
    "🔗 Job Dependency Explorer": "Inspect the upstream and downstream dependencies of any job to understand its execution chain.",
    "💬 Open Query":              "Ask free-form questions about any indexed job — descriptions, run modes, remarks, or anything else in the data.",
}
if mode in _MODE_DESCRIPTIONS:
    st.caption(_MODE_DESCRIPTIONS[mode])

# ── Open Query input ──────────────────────────────────────────────────────────
if mode == "💬 Open Query":
    k = st.number_input("Number of chunks to retrieve", min_value=5, value=5, step=1)
    if st.session_state.get("clear_input"):
        st.session_state.open_query_input = ""
        st.session_state.clear_input = False
    prompt_input = st.text_area("Ask anything about the indexed jobs...", key="open_query_input", height=68)
    prompt = prompt_input if st.button("Send") and prompt_input else None
    if prompt:
        st.session_state.clear_input = True

# ── Job Schedule Explorer ─────────────────────────────────────────────────────
if mode == "🗓️ Job Schedule Explorer":
    vs = shared["vectorstore"]
    if vs is None:
        st.info("No documents indexed yet. Please contact your administrator.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=date.today())
        with col2:
            end_date = st.date_input("End Date", value=date.today())

        if st.button("🔍 Find Scheduled Jobs"):
            if end_date < start_date:
                st.error("End date must be after start date.")
            else:
                with st.spinner("Analysing job schedules..."):
                    store_data = vs.get()
                    all_docs = store_data["documents"]
                    all_metas = store_data["metadatas"]
                    if not all_docs:
                        st.warning("No jobs found in the store.")
                    else:
                        with open("holidays.json", "r") as f:
                            holidays = json.load(f)

                        holiday_lines = "\n".join(
                            f"- {h['date']} ({h['day']}): {h['name']}"
                            for h in holidays.get("public_holidays", [])
                        )
                        holidays_context = (
                            f"Country: {holidays.get('country')}\n"
                            f"Year: {holidays.get('year')}\n"
                            f"Public Holidays:\n{holiday_lines}"
                        )

                        # Python-side date boundary filter before LLM
                        filtered_docs = []
                        date_excluded = []
                        for doc, meta in zip(all_docs, all_metas):
                            try:
                                job = json.loads(doc)
                                m = job.get("metadata", {})
                                frd = m.get("first_run_date")
                                lrd = m.get("last_run_date")
                                first = date.fromisoformat(str(frd)[:10]) if frd and str(frd).strip() not in ("", "None", "nan") else None
                                last = date.fromisoformat(str(lrd)[:10]) if lrd and str(lrd).strip() not in ("", "None", "nan") else None
                                if first and end_date < first:
                                    date_excluded.append((meta.get("job_name"), f"first_run_date {first} is after query end date {end_date}"))
                                    continue
                                if last and start_date > last:
                                    date_excluded.append((meta.get("job_name"), f"last_run_date {last} is before query start date {start_date}"))
                                    continue
                                # Strip date boundary fields so LLM cannot misuse them
                                m.pop("first_run_date", None)
                                m.pop("last_run_date", None)
                                filtered_docs.append(json.dumps(job, ensure_ascii=False))
                            except Exception:
                                filtered_docs.append(doc)

                        jobs_context = "\n\n".join(filtered_docs)

                        system_prompt = (
                            "You are a batch job scheduling assistant. Each job is provided as a JSON object with 'metadata' and 'derived' sections.\n\n"
                            "=== PHASE 1: FREQUENCY FILTER ===\n"
                            "Use derived.frequency to make an initial determination:\n"
                            "- 'daily' → candidate for every day in the range\n"
                            "- 'weekly' → candidate only if the date range contains a day in derived.days_of_week\n"
                            "- 'monthly' → candidate only if the date range contains derived.day_of_month\n"
                            "- 'yearly' → candidate only if the date range contains derived.day_of_month in derived.month\n"
                            "- 'on_request' → exclude unless explicitly triggered\n\n"
                            "=== PHASE 2: WEEKDAY FILTER ===\n"
                            "- If derived.days_of_week is an empty list [] → no weekday restriction, job PASSES this phase automatically\n"
                            "- If derived.days_of_week is non-empty → job PASSES only if the date range contains at least one of those weekdays\n"
                            "- IMPORTANT: read derived.days_of_week directly from the job's JSON. Do NOT infer or assume it is empty if the field is present with values.\n"
                            "- Time constraints in derived.times → ignore, this query filters by date only\n"
                            "- NOTE: public holiday exclusion is handled separately — do NOT apply it here\n\n"
                            "=== OUTPUT RULES ===\n"
                            "- Only output jobs that survive BOTH phases\n"
                            "- Output ONLY a valid JSON object with exactly two keys:\n"
                            "  * 'reasoning': array of objects, one per job, each with:\n"
                            "      - 'job': job_id string\n"
                            "      - 'phase1': one sentence on frequency match (include/exclude and why)\n"
                            "      - 'phase2': one sentence on weekday filter result (state the actual days_of_week value you read), or 'N/A' if excluded in phase 1\n"
                            "      - 'result': 'INCLUDED' or 'EXCLUDED'\n"
                            "  * 'qualifying_jobs': array of job_id strings that are INCLUDED\n"
                            "- Do NOT include any explanation, markdown, or text outside the JSON object"
                        )

                        date_range_days = []
                        d = start_date
                        while d <= end_date:
                            date_range_days.append(d)
                            d += timedelta(days=1)

                        ph_dates = {h["date"] for h in holidays.get("public_holidays", [])}
                        range_ph_dates = {
                            day.strftime("%Y-%m-%d") for day in date_range_days
                            if day.strftime("%Y-%m-%d") in ph_dates
                        }

                        date_facts = "\n".join(
                            f"- {day.strftime('%Y-%m-%d')} is a {day.strftime('%a')}"
                            for day in date_range_days
                        )

                        user_msg = (
                            f"Which jobs are scheduled to run between "
                            f"{start_date.strftime('%a, %d %b %Y')} and "
                            f"{end_date.strftime('%a, %d %b %Y')}?\n\n"
                            f"The queried date range contains these dates:\n{date_facts}\n\n"
                            f"Use ONLY these dates and their 3-letter weekday abbreviations (Mon/Tue/Wed/Thu/Fri/Sat/Sun) "
                            f"when evaluating each job against derived.days_of_week. "
                            f"Do NOT infer or assume any other dates.\n\n"
                            f"=== JOB DATA ===\n{jobs_context}"
                        )
                        try:
                            client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                            response = client.chat.completions.create(
                                model="gpt-4o",
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_msg},
                                ],
                                response_format={"type": "json_object"},
                            )
                            parsed = json.loads(response.choices[0].message.content)
                            qualifying_names = set(parsed.get("qualifying_jobs", []))
                            reasoning = parsed.get("reasoning", [])

                            # Python-side public holiday filter:
                            # If the entire date range consists only of public holidays,
                            # exclude jobs that have exclude_public_holiday=True.
                            # If the range spans multiple days, only exclude if ALL
                            # non-weekend days are public holidays (job has no valid run day).
                            if range_ph_dates:
                                ph_excluded = set()
                                job_lookup = {
                                    meta.get("job_name"): json.loads(doc)
                                    for doc, meta in zip(all_docs, all_metas)
                                    if meta.get("job_name") in qualifying_names
                                }
                                for job_id, job in job_lookup.items():
                                    derived = job.get("derived", {})
                                    if not derived.get("exclude_public_holiday"):
                                        continue
                                    days_of_week = derived.get("days_of_week", [])
                                    # Determine which days in range this job would run on
                                    run_days = [
                                        day for day in date_range_days
                                        if not days_of_week or day.strftime("%a") in days_of_week
                                    ]
                                    # Check if all run days fall on public holidays
                                    all_run_days_are_ph = all(
                                        day.strftime("%Y-%m-%d") in range_ph_dates
                                        for day in run_days
                                    )
                                    if run_days and all_run_days_are_ph:
                                        ph_excluded.add(job_id)
                                        # Update reasoning entry
                                        for entry in reasoning:
                                            if entry.get("job") == job_id:
                                                entry["phase2"] = "EXCLUDED by public holiday filter (all run days in range are public holidays)"
                                                entry["result"] = "EXCLUDED"
                                qualifying_names -= ph_excluded

                            # Merge date boundary exclusions into reasoning
                            for job_name, reason in date_excluded:
                                reasoning.append({
                                    "job": job_name,
                                    "phase1": f"EXCLUDED — {reason}",
                                    "phase2": "N/A",
                                    "result": "EXCLUDED",
                                })

                            if not qualifying_names:
                                st.session_state.schedule_df = pd.DataFrame()
                            else:
                                rows = []
                                for doc, meta in zip(all_docs, all_metas):
                                    if meta.get("job_name") not in qualifying_names:
                                        continue
                                    try:
                                        job = json.loads(doc)
                                        m = job.get("metadata", {})
                                        rows.append({
                                            "Job Name":                m.get("job_name"),
                                            "Title":                   m.get("title_of_run_series"),
                                            "Description":             m.get("description"),
                                            "Frequency":               m.get("job_frequency"),
                                            "Scheduling Instructions": m.get("scheduling_instructions"),
                                            "Volume":                  m.get("estimated_volume_of_records"),
                                            "Est. Run Time (mins)":    m.get("estimated_run_time_minutes"),
                                        })
                                    except Exception:
                                        continue
                                st.session_state.schedule_df = pd.DataFrame(rows)

                            st.session_state.schedule_reasoning = reasoning
                            st.session_state.schedule_query = (
                                f"{start_date.strftime('%A, %d %b %Y')} — {end_date.strftime('%A, %d %b %Y')}"
                            )
                            st.rerun()
                        except Exception as e:
                            import traceback
                            traceback.print_exc()
                            st.error(f"Sorry, I couldn't process your request: {e}")

        if "schedule_df" in st.session_state:
            st.caption(f"Results for: {st.session_state.get('schedule_query', '')}")

            reasoning = st.session_state.pop("schedule_reasoning", [])
            if reasoning:
                with st.expander("🔍 Reasoning Details", expanded=False):
                    for entry in reasoning:
                        icon = "✅" if entry.get("result") == "INCLUDED" else "❌"
                        st.markdown(f"**{icon} {entry.get('job')}**")
                        st.markdown(f"- **Phase 1 (Frequency):** {entry.get('phase1')}")
                        st.markdown(f"- **Phase 2 (Scheduling):** {entry.get('phase2')}")
                        st.divider()

            df = st.session_state.schedule_df
            if df.empty:
                st.info("No jobs are scheduled to run in the selected date range.")
            else:
                st.dataframe(df, hide_index=True, use_container_width=True)

# ── Job Dependency Explorer ───────────────────────────────────────────────────
if mode == "🔗 Job Dependency Explorer":
    vs = shared["vectorstore"]
    if vs is None:
        st.info("No documents indexed yet. Please contact your administrator.")
    else:
        store_data = vs.get()
        all_docs = store_data["documents"]
        all_metas = store_data["metadatas"]

        # Build lookup: job_id -> parsed doc
        job_lookup = {}
        for doc, meta in zip(all_docs, all_metas):
            try:
                job_lookup[meta["job_name"]] = json.loads(doc)
            except Exception:
                continue

        def collect_upstream(job_id: str, visited: set) -> list:
            """Recursively collect upstream dependency tree as list of (depth, job_id, notes)."""
            if job_id in visited:
                return []
            visited.add(job_id)
            results = []
            deps = job_lookup.get(job_id, {}).get("derived", {}).get("dependencies", [])
            for dep in deps:
                notes = job_lookup.get(job_id, {}).get("derived", {}).get("notes")
                results.append((dep, notes))
                results.extend(collect_upstream(dep, visited))
            return results

        def collect_downstream(job_id: str, visited: set) -> list:
            """Recursively collect downstream dependent tree as list of (job_id, notes)."""
            if job_id in visited:
                return []
            visited.add(job_id)
            results = []
            for jid, job in job_lookup.items():
                if job_id in job.get("derived", {}).get("dependencies", []):
                    notes = job.get("derived", {}).get("notes")
                    results.append((jid, notes))
                    results.extend(collect_downstream(jid, visited))
            return results

        def render_tree(job_id: str, job_lookup: dict, direction: str):
            """Render a full dependency tree with indentation using BFS to track depth."""
            from collections import deque
            queue = deque()
            visited = set()

            if direction == "upstream":
                deps = job_lookup.get(job_id, {}).get("derived", {}).get("dependencies", [])
                notes_src = job_lookup.get(job_id, {}).get("derived", {}).get("notes")
                for dep in deps:
                    queue.append((dep, notes_src, 0))
            else:
                for jid, job in job_lookup.items():
                    if job_id in job.get("derived", {}).get("dependencies", []):
                        queue.append((jid, job.get("derived", {}).get("notes"), 0))

            if not queue:
                return False  # nothing to render

            while queue:
                curr_id, notes, depth = queue.popleft()
                if curr_id in visited:
                    st.markdown(f"{'&nbsp;' * depth * 6}↩ `{curr_id}` *(already shown)*", unsafe_allow_html=True)
                    continue
                visited.add(curr_id)
                indent = '&nbsp;' * depth * 6
                prefix = "└─ " if depth > 0 else ""
                label = f"{indent}{prefix}**{curr_id}**"
                if notes:
                    label += f"  —  *{notes}*"
                st.markdown(label, unsafe_allow_html=True)

                # Queue next level
                if direction == "upstream":
                    next_deps = job_lookup.get(curr_id, {}).get("derived", {}).get("dependencies", [])
                    next_notes = job_lookup.get(curr_id, {}).get("derived", {}).get("notes")
                    for dep in next_deps:
                        queue.append((dep, next_notes, depth + 1))
                else:
                    for jid, job in job_lookup.items():
                        if curr_id in job.get("derived", {}).get("dependencies", []):
                            queue.append((jid, job.get("derived", {}).get("notes"), depth + 1))
            return True

        job_ids = sorted(job_lookup.keys())
        selected_job = st.selectbox("Select a Job", job_ids, key="dep_job_select")

        if selected_job and selected_job in job_lookup:
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**⬆️ Depends On** (upstream)")
                if not render_tree(selected_job, job_lookup, "upstream"):
                    st.info("No upstream dependencies.")

            with col2:
                st.markdown("**⬇️ Is Dependency Of** (downstream)")
                if not render_tree(selected_job, job_lookup, "downstream"):
                    st.info("No downstream dependents.")

            with st.expander("📄 Full Job Data", expanded=False):
                st.json(job_lookup[selected_job])

# ── Open Query ────────────────────────────────────────────────────────────────
def render_msg(msg, show_sources=False):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if show_sources and msg["role"] == "assistant" and msg.get("sources"):
            with st.expander("🔍 View Sources"):
                for i, src in enumerate(msg["sources"], 1):
                    st.caption(f"Chunk {i} — {src['job_name']} ({src['system_code']}):")
                    st.code(src["content"], language="json")

if mode == "💬 Open Query" and st.session_state.messages:
    render_msg(st.session_state.messages[-2] if len(st.session_state.messages) >= 2 else st.session_state.messages[-1])
    if len(st.session_state.messages) >= 2:
        render_msg(st.session_state.messages[-1], show_sources=True)

    history = st.session_state.messages[:-2] if len(st.session_state.messages) >= 2 else []
    if history:
        st.divider()
        st.caption("📜 Previous Conversations")
        for msg in history:
            render_msg(msg)

if mode == "💬 Open Query" and prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    vs = shared["vectorstore"]
    if vs is None:
        reply = "No job information is available yet. Please contact your administrator to upload the data."
        with st.chat_message("assistant"):
            st.write(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
    else:
        results = vs.similarity_search(prompt, k=int(k))
        if not results:
            reply = "I'm sorry, I couldn't find any relevant information for your question in the indexed jobs."
            with st.chat_message("assistant"):
                st.write(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
        else:
            context = "\n\n".join(doc.page_content for doc in results)
            system_prompt = (
                "You are a helpful assistant that answers questions strictly based on the job information provided below. "
                "Do not use any external knowledge. If the answer cannot be found in the provided information, "
                "politely say that no relevant information was found.\n"
                "Each job is stored as a JSON object with 'job_id', 'metadata', and 'derived' sections. "
                "When presenting job details, display the relevant job JSON exactly as-is using a code block. "
                "Do not paraphrase, reformat, or summarise the JSON fields.\n\n"
                f"Job Information:\n{context}"
            )
            try:
                client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                messages_with_context = [
                    {"role": "system", "content": system_prompt},
                    *st.session_state.messages,
                ]
                stream = client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages_with_context,
                    stream=True,
                )
                with st.chat_message("assistant"):
                    reply = st.write_stream(stream)
                st.session_state.messages.append({"role": "assistant", "content": reply, "sources": [
                    {"job_name": doc.metadata.get("job_name", "Unknown"), "system_code": doc.metadata.get("system_code", ""), "content": doc.page_content}
                    for doc in results
                ]})
                st.rerun()
            except Exception as e:
                import traceback
                traceback.print_exc()
                fallback = "Sorry, I couldn't process your request. Please try again."
                st.error(fallback)
                st.session_state.messages.append({"role": "assistant", "content": fallback})
