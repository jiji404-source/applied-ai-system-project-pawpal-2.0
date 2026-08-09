import streamlit as st
from datetime import date, datetime
from pawpal_system import Task, Pet, Owner, Scheduler
from pawpal_ai.llm import ClaudeClient
from pawpal_ai.planner import CarePlanner
from pawpal_ai.retriever import KnowledgeBase


def display_time(hhmm: str) -> str:
    """Render a stored 24-hour 'HH:MM' as a 12-hour clock time, e.g. '8:00 AM'.

    Tasks are stored and scheduled internally as 24-hour HH:MM (Scheduler.sort_by_time
    parses it, and the AI planner's schema requires it) — this only affects what the
    owner sees.
    """
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")


st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="wide")

# Design tokens mirror .streamlit/config.toml's theme (terracotta on warm cream) and
# add the one thing that theme doesn't cover: an earthy sage for "all clear" states,
# so the palette has a calm green alongside its terracotta the way Bond Vet's does.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap');

:root {
    --pp-terracotta: #C65338;
    --pp-terracotta-bg: #F5DFD5;
    --pp-sage: #5B7A52;
    --pp-sage-bg: #E3EBDC;
    --pp-card: #FFFDF9;
    --pp-border: #E7D9C6;
    --pp-text: #0B0A0A;
    --pp-text-muted: #8A7C68;
}

html, body, [class*="css"] {
    font-family: 'Manrope', sans-serif;
}

h1, h2, h3 {
    font-weight: 800;
    color: var(--pp-text);
    letter-spacing: -0.01em;
}

[data-testid="stCaptionContainer"] { color: var(--pp-text-muted); }

/* Hero */
.pp-hero { display: flex; align-items: baseline; gap: 12px; margin-bottom: 2px; }
.pp-hero-accent { width: 44px; height: 6px; background: var(--pp-terracotta); border-radius: 6px; margin: 10px 0 22px 0; }

/* Buttons */
.stButton > button {
    border-radius: 24px;
    font-family: 'Manrope', sans-serif;
    font-weight: 700;
    transition: transform 0.06s ease;
}
.stButton > button:active { transform: scale(0.98); }

/* Sidebar */
[data-testid="stSidebar"] {
    border-right: 1px solid var(--pp-border);
}

/* Tabs */
[data-testid="stTabs"] button {
    font-family: 'Manrope', sans-serif;
    font-weight: 600;
}

/* Metric cards */
[data-testid="stMetric"], .pp-metric-card {
    background-color: var(--pp-card);
    border-radius: 14px;
    padding: 14px 16px;
    border: 1px solid var(--pp-border);
}
.pp-metric-card { margin-bottom: 1rem; }

/* Bordered containers used as task cards */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px !important;
    border-color: var(--pp-border) !important;
    background-color: var(--pp-card);
}

/* Alerts: keep Streamlit's semantic colors, just round them to match the system */
[data-testid="stAlert"] { border-radius: 14px; }

/* Pills */
.pp-pill {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
}
.pp-pill-sage { background: var(--pp-sage-bg); color: var(--pp-sage); }
.pp-pill-terracotta { background: var(--pp-terracotta-bg); color: var(--pp-terracotta); }
.pp-pill-muted { background: #F0EAE0; color: var(--pp-text-muted); }

/* Rule-citation tags */
.pp-tag {
    display: inline-block;
    padding: 2px 9px;
    margin: 2px 4px 2px 0;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
    background: #F0EAE0;
    color: var(--pp-text-muted);
    letter-spacing: 0.01em;
}

/* Task card internals */
.pp-time { font-weight: 800; color: var(--pp-terracotta); min-width: 78px; display: inline-block; }
.pp-desc { font-weight: 600; color: var(--pp-text); }
.pp-meta { color: var(--pp-text-muted); font-size: 0.85rem; }

/* Empty state */
.pp-empty {
    text-align: center;
    padding: 36px 16px;
    color: var(--pp-text-muted);
    background: var(--pp-card);
    border: 1px dashed var(--pp-border);
    border-radius: 16px;
}
.pp-empty-icon { font-size: 2rem; margin-bottom: 6px; }
</style>
""", unsafe_allow_html=True)

if "owner" not in st.session_state:
    st.session_state.owner = Owner("My Owner")

owner = st.session_state.owner
scheduler = Scheduler(owner)

st.markdown('<div class="pp-hero"><h1>🐾 PawPal+</h1></div>', unsafe_allow_html=True)
st.caption("Smart pet care management, with an AI layer that shows its work.")
st.markdown('<div class="pp-hero-accent"></div>', unsafe_allow_html=True)

# ── Sidebar: Owner name + Add Pet ──────────────────────────────────────────
with st.sidebar:
    st.header("Setup")

    st.subheader("Owner")
    new_name = st.text_input("Your name", value=owner.name)
    if st.button("Update name"):
        st.session_state.owner.name = new_name
        st.success(f"Name updated to {new_name}!")

    st.divider()

    st.subheader("Add a Pet")
    pet_name  = st.text_input("Pet name")
    species   = st.selectbox("Species", ["dog", "cat", "rabbit", "bird", "other"])
    breed     = st.text_input("Breed (optional)")
    age       = st.number_input("Age", min_value=0, max_value=30, value=1)
    allergies = st.text_input("Allergies (optional)")
    meds      = st.text_input("Medications (optional)")

    if st.button("Add Pet", type="primary"):
        if pet_name.strip():
            existing = [p.name for p in owner.pets]
            if pet_name in existing:
                st.warning(f"{pet_name} is already added.")
            else:
                owner.add_pet(Pet(pet_name, species, breed, age, allergies, meds))
                st.success(f"Added {pet_name}!")
        else:
            st.error("Please enter a pet name.")

    if owner.pets:
        st.divider()
        st.subheader("Your Pets")
        for p in owner.pets:
            subtitle = p.species + (f" · {p.breed}" if p.breed else "")
            st.markdown(
                f"""<div style="background:var(--pp-card); border:1px solid var(--pp-border);
                border-radius:12px; padding:8px 12px; margin-bottom:8px;">
                <span style="font-weight:700;">🐾 {p.name}</span><br/>
                <span class="pp-meta">{subtitle}</span>
                </div>""",
                unsafe_allow_html=True,
            )

# ── Main area ──────────────────────────────────────────────────────────────
if not owner.pets:
    st.markdown(
        """<div class="pp-empty"><div class="pp-empty-icon">🐾</div>
        Start by adding a pet in the sidebar.</div>""",
        unsafe_allow_html=True,
    )
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(
    ["Add Task", "Today's Schedule", "Manage Tasks", "🤖 AI Care Plan"]
)

# ── Tab 1: Add Task ────────────────────────────────────────────────────────
with tab1:
    st.header("Schedule a Task")

    col1, col2 = st.columns(2)
    with col1:
        selected_pet = st.selectbox("Pet", [p.name for p in owner.pets])
        task_desc    = st.text_input("What needs to happen?", placeholder="e.g. Morning walk")
    with col2:
        # st.time_input has no 12-hour display option in this Streamlit version, so
        # the time is built from three plain selectboxes instead.
        st.write("Time")
        th1, th2, th3 = st.columns(3)
        with th1:
            hour_12 = st.selectbox("Hour", list(range(1, 13)), index=7, label_visibility="collapsed")
        with th2:
            minute = st.selectbox("Minute", ["00", "15", "30", "45"], label_visibility="collapsed")
        with th3:
            period = st.selectbox("AM/PM", ["AM", "PM"], label_visibility="collapsed")
        frequency = st.selectbox("Frequency", ["once", "daily", "weekly"])

    if st.button("Schedule Task", type="primary"):
        if task_desc.strip():
            pet      = next(p for p in owner.pets if p.name == selected_pet)
            hour_24  = (hour_12 % 12) + (12 if period == "PM" else 0)
            time_str = f"{hour_24:02d}:{minute}"
            pet.add_task(Task(task_desc, time_str, frequency, due_date=date.today()))
            st.success(f"Scheduled '{task_desc}' for {selected_pet} at {display_time(time_str)}!")
        else:
            st.error("Please enter a task description.")

# ── Tab 2: Today's Schedule ────────────────────────────────────────────────
with tab2:
    st.header("Today's Schedule")

    conflicts = scheduler.detect_conflicts()
    if conflicts:
        for c in conflicts:
            st.warning(f"⚠️ {c}")
    else:
        st.success("No scheduling conflicts detected.")

    schedule = scheduler.get_daily_schedule()
    if not schedule:
        st.markdown(
            """<div class="pp-empty"><div class="pp-empty-icon">📋</div>
            No tasks scheduled for today. Add some in the "Add Task" tab.</div>""",
            unsafe_allow_html=True,
        )
    else:
        total     = len(scheduler.filter_tasks())
        pending   = len(scheduler.filter_tasks(completed=False))
        done      = len(scheduler.filter_tasks(completed=True))

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Tasks", total)
        m2.metric("Pending",     pending)
        m3.metric("Completed",   done)

        st.divider()
        st.caption(f"{len(schedule)} task(s) today — sorted earliest to latest")

        for t in schedule:
            with st.container(border=True):
                status_pill = (
                    '<span class="pp-pill pp-pill-sage">✓ Done</span>'
                    if t.completed
                    else '<span class="pp-pill pp-pill-terracotta">Pending</span>'
                )
                st.markdown(
                    f"""<span class="pp-time">{display_time(t.time)}</span>
                    &nbsp;&nbsp;<span class="pp-desc">{t.description}</span>
                    &nbsp;&nbsp;<span class="pp-meta">🐾 {t.pet_name} · {t.frequency}</span>
                    &nbsp;&nbsp;{status_pill}""",
                    unsafe_allow_html=True,
                )

# ── Tab 3: Manage Tasks ─────────────────────────────────────────────────────
with tab3:
    st.header("Manage Tasks")

    col1, col2 = st.columns(2)
    with col1:
        filter_pet    = st.selectbox("Filter by pet",    ["All"] + [p.name for p in owner.pets])
    with col2:
        filter_status = st.selectbox("Filter by status", ["All", "Pending", "Done"])

    pet_filter       = None if filter_pet    == "All"     else filter_pet
    completed_filter = None if filter_status == "All"     else (filter_status == "Done")

    filtered = scheduler.sort_by_time(
        scheduler.filter_tasks(pet_name=pet_filter, completed=completed_filter)
    )

    if not filtered:
        st.markdown(
            '<div class="pp-empty"><div class="pp-empty-icon">🔍</div>No tasks match this filter.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"{len(filtered)} task(s) found — sorted by time")
        for i, task in enumerate(filtered):
            with st.container(border=True):
                col_desc, col_btn = st.columns([5, 1])
                with col_desc:
                    st.markdown(
                        f"""<span class="pp-time">{display_time(task.time)}</span>
                        &nbsp;&nbsp;<span class="pp-desc">{task.description}</span>
                        &nbsp;&nbsp;<span class="pp-meta">🐾 {task.pet_name} · {task.frequency}</span>""",
                        unsafe_allow_html=True,
                    )
                with col_btn:
                    if not task.completed:
                        if st.button("✓ Done", key=f"complete_{i}"):
                            pet = next(p for p in owner.pets if p.name == task.pet_name)
                            scheduler.mark_task_complete(task, pet)
                            st.rerun()
                    else:
                        st.markdown(
                            '<span class="pp-pill pp-pill-sage">✓ Done</span>',
                            unsafe_allow_html=True,
                        )

# ── Tab 4: AI Care Plan ─────────────────────────────────────────────────────
with tab4:
    st.header("AI Care Plan")
    st.caption(
        "Claude retrieves vet-care guidance, drafts a schedule, then critiques and "
        "revises its own work before showing it to you."
    )

    @st.cache_resource
    def load_kb():
        """Parse the knowledge base once per session rather than per rerun."""
        return KnowledgeBase.load()

    try:
        kb = load_kb()
        st.caption(f"Knowledge base: {len(kb)} care rules loaded.")
    except (FileNotFoundError, ValueError) as exc:
        st.error(f"Could not load the knowledge base: {exc}")
        st.stop()

    notes = st.text_area(
        "Anything else Claude should know?",
        placeholder="e.g. I leave for work at 8:30 AM and get home at 6:00 PM. "
        "Mochi's allergy pill was prescribed once daily.",
    )

    if st.button("Generate plan", type="primary"):
        planner = CarePlanner(ClaudeClient(), kb=kb)
        with st.spinner("Retrieving guidance, drafting, and self-reviewing…"):
            result = planner.plan_day(owner.pets, notes=notes)

        # Refusals and errors are expected outcomes, not crashes.
        if result.refused:
            st.error(f"🛑 {result.refusal_reason}")
            st.caption(
                "PawPal+ schedules care, it does not give medical advice. "
                "Please contact your veterinarian."
            )
        elif not result.ok:
            st.error(f"Could not generate a plan: {result.error}")
        else:
            plan = result.plan

            c1, c2, c3 = st.columns(3)
            c1.metric("Confidence", f"{result.confidence:.0%}")
            c2.metric("Review rounds", result.rounds)
            with c3:
                st.markdown(
                    '<div class="pp-metric-card">'
                    '<div class="pp-meta" style="margin-bottom:6px;">Reviewer verdict</div>'
                    + (
                        '<span class="pp-pill pp-pill-sage">✓ Approved</span>'
                        if result.approved
                        else '<span class="pp-pill pp-pill-terracotta">Issues open</span>'
                    )
                    + '</div>',
                    unsafe_allow_html=True,
                )
            st.progress(result.confidence)

            if result.confidence < 0.6:
                st.warning(
                    "Low confidence — read this plan critically and check anything "
                    "medication-related with your vet."
                )

            st.info(plan.summary)

            st.subheader("Schedule")
            for t in plan.tasks:
                with st.container(border=True):
                    tags = "".join(f'<span class="pp-tag">{r}</span>' for r in t.cited_rules)
                    st.markdown(
                        f"""<span class="pp-time">{display_time(t.time)}</span>
                        &nbsp;&nbsp;<span class="pp-desc">{t.description}</span>
                        &nbsp;&nbsp;<span class="pp-meta">🐾 {t.pet_name}</span><br/>
                        <span class="pp-meta">{t.rationale}</span><br/>
                        {tags}""",
                        unsafe_allow_html=True,
                    )

            if plan.conflicts_resolved:
                st.subheader("Conflicts resolved")
                for item in plan.conflicts_resolved:
                    st.write(f"- {item}")

            if plan.unmet_constraints:
                st.subheader("Could not be satisfied")
                for item in plan.unmet_constraints:
                    st.warning(item)

            # Show the audit trail — this is what makes the plan checkable.
            with st.expander("How this plan was checked"):
                st.write(
                    f"**Retrieved guidance:** {', '.join(result.retrieved_rule_ids) or 'none'}"
                )
                if result.invalid_citations:
                    st.error(
                        "Claude cited rules that do not exist: "
                        f"{', '.join(result.invalid_citations)}. Confidence was reduced."
                    )
                else:
                    st.success("All cited rules were verified against the knowledge base.")

                if result.residual_conflicts:
                    st.warning(
                        "The conflict detector still sees: "
                        + "; ".join(result.residual_conflicts)
                    )
                else:
                    st.success("No time conflicts remain in the final plan.")

                for i, critique in enumerate(result.critiques, start=1):
                    st.markdown(f"**Review round {i}** — {critique.assessment}")
                    for issue in critique.issues:
                        st.write(
                            f"- `{issue.severity}` {issue.task_reference}: {issue.problem} "
                            f"→ {issue.suggested_fix}"
                        )
