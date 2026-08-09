import streamlit as st
from datetime import date
from pawpal_system import Task, Pet, Owner, Scheduler
from pawpal_ai.llm import ClaudeClient
from pawpal_ai.planner import CarePlanner
from pawpal_ai.retriever import KnowledgeBase

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Manrope', sans-serif;
}

h1, h2, h3 {
    font-weight: 800;
    color: #0B0A0A;
}

/* Rounded primary buttons */
.stButton > button {
    border-radius: 24px;
    font-family: 'Manrope', sans-serif;
    font-weight: 700;
}

/* Sidebar styling */
[data-testid="stSidebar"] {
    background-color: #F2E7DF;
    border-right: 1px solid #e0d6cb;
}

/* Tab styling */
[data-testid="stTabs"] button {
    font-family: 'Manrope', sans-serif;
    font-weight: 600;
}

/* Metric cards */
[data-testid="stMetric"] {
    background-color: #FDFDFD;
    border-radius: 12px;
    padding: 12px;
    border: 1px solid #e0d6cb;
}
</style>
""", unsafe_allow_html=True)

if "owner" not in st.session_state:
    st.session_state.owner = Owner("My Owner")

owner = st.session_state.owner
scheduler = Scheduler(owner)

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="wide")
st.title("🐾 PawPal+")
st.caption("Smart pet care management system")

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
            st.write(f"**{p.name}** — {p.species}" + (f", {p.breed}" if p.breed else ""))

# ── Main area ──────────────────────────────────────────────────────────────
if not owner.pets:
    st.info("Start by adding a pet in the sidebar.")
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
        task_time  = st.time_input("Time")
        frequency  = st.selectbox("Frequency", ["once", "daily", "weekly"])

    if st.button("Schedule Task", type="primary"):
        if task_desc.strip():
            pet      = next(p for p in owner.pets if p.name == selected_pet)
            time_str = task_time.strftime("%H:%M")
            pet.add_task(Task(task_desc, time_str, frequency, due_date=date.today()))
            st.success(f"Scheduled '{task_desc}' for {selected_pet} at {time_str}!")
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
        st.info("No tasks scheduled for today. Add some in the 'Add Task' tab.")
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
        st.table([
            {
                "Time":      t.time,
                "Pet":       t.pet_name,
                "Task":      t.description,
                "Frequency": t.frequency,
                "Status":    "✅ Done" if t.completed else "🕐 Pending",
            }
            for t in schedule
        ])

# ── Tab 3: Manage Tasks ────────────────────────────────────────────────────
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
        st.info("No tasks match this filter.")
    else:
        st.caption(f"{len(filtered)} task(s) found — sorted by time")
        for i, task in enumerate(filtered):
            col_btn, col_time, col_pet, col_desc, col_freq = st.columns([1, 1, 1, 3, 1])
            with col_time:
                st.write(f"🕐 **{task.time}**")
            with col_pet:
                st.write(f"🐾 {task.pet_name}")
            with col_desc:
                st.write(task.description)
            with col_freq:
                st.caption(task.frequency)
            with col_btn:
                if not task.completed:
                    if st.button("✓ Done", key=f"complete_{i}"):
                        pet = next(p for p in owner.pets if p.name == task.pet_name)
                        scheduler.mark_task_complete(task, pet)
                        st.rerun()
                else:
                    st.success("✅ Done")

# ── Tab 4: AI Care Plan ────────────────────────────────────────────────────
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
        placeholder="e.g. I leave for work at 08:30 and get home at 18:00. "
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
            c3.metric("Reviewer verdict", "Approved" if result.approved else "Issues open")

            if result.confidence < 0.6:
                st.warning(
                    "Low confidence — read this plan critically and check anything "
                    "medication-related with your vet."
                )

            st.info(plan.summary)

            st.subheader("Schedule")
            st.table(
                [
                    {
                        "Time": t.time,
                        "Pet": t.pet_name,
                        "Task": t.description,
                        "Why": t.rationale,
                        "Rules": ", ".join(t.cited_rules) or "—",
                    }
                    for t in plan.tasks
                ]
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