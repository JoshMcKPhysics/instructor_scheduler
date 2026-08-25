import calendar
import datetime
from pathlib import Path
import sys

import pandas as pd
import streamlit as st
from streamlit_extras.stylable_container import stylable_container
import json as _json
import io

# Optional supabase client (used when deployed with secrets)
try:
    from supabase import create_client
except Exception:
    create_client = None

# ---------- CONFIG ----------

# For local development: set to True to bypass Streamlit secrets/password checks
# When deployed, set LOCAL_DEV = False and provide secrets in Streamlit Cloud.
LOCAL_DEV = False

# ADMIN_PASSWORD and JOSH_PASSWORD will be read from `st.secrets` when available
ADMIN_PASSWORD = None
JOSH_PASSWORD = None


BASE_DIR = Path(__file__).parent

DATA_FILE = BASE_DIR / "instructor_dates.pkl"
DATA_CSV = BASE_DIR / "data.csv"

DAY_PANEL_HEIGHT = 185


def default_time_range(dt):
    dt = pd.Timestamp(dt)
    # Mon-Thu (0-3)
    if dt.dayofweek < 4:
        return datetime.time(16, 0), datetime.time(20, 0)
    # Friday (4)
    elif dt.dayofweek == 4:
        return datetime.time(17, 0), datetime.time(19, 0)
    # Sat-Sun (5-6)
    else:
        return datetime.time(10, 0), datetime.time(13, 0)


def format_short_time(time_str):
    if not time_str or time_str == "None":
        return "", ""
    try:
        if isinstance(time_str, datetime.time):
            t = time_str
        else:
            t = pd.to_datetime(str(time_str)).time()
            
        hour_str = t.strftime("%I").lstrip("0")
        # Grab just the first letter ('a' or 'p') from am/pm
        ampm = t.strftime("%p").lower()[0]
        
        if t.minute == 0:
            return hour_str, ampm
        else:
            return f"{hour_str}:{t.strftime('%M')}", ampm
    except Exception:
        return str(time_str), ""


# ---------- APP ----------

st.set_page_config(layout="wide")

@st.cache_data
def load_data():
    # Prefer the pickle for speed
    if DATA_FILE.exists():
        df = pd.read_pickle(DATA_FILE).copy()
        df["Date"] = pd.to_datetime(df["Date"])
        return df
    #if 'cover' not in df.columns:
    #    sys.exit()
    # Fallback: if a CSV with signups exists, synthesize a calendar-friendly dataframe
    if DATA_CSV.exists():
        raw = pd.read_csv(DATA_CSV).copy()

        # collect candidate name columns
        if "Name" in raw.columns:
            names = raw["Name"].dropna().astype(str).unique().tolist()
        elif "Name (First + Last)" in raw.columns:
            names = raw["Name (First + Last)"].dropna().astype(str).unique().tolist()
        elif "Email Address" in raw.columns:
            names = raw["Email Address"].dropna().astype(str).unique().tolist()
        else:
            names = []

        if not names:
            names = ["Alice Example", "Bob Example", "Casey Example"]

        start = pd.Timestamp.now().normalize()
        dates = pd.date_range(start, periods=30, freq="D")

        rows = []
        for d in dates:
            for n in names:
                rows.append({
                    "Date": d,
                    "Name": n
                })

        df = pd.DataFrame(rows)
        
        # Save a pickle for faster subsequent runs
        try:
            pd.to_pickle(df, DATA_FILE)
        except Exception:
            pass

        return df

    raise FileNotFoundError(
        f"Data file not found. Expected {DATA_FILE} or {DATA_CSV} in the app folder."
    )

df = load_data()

# ---------- STATE ----------

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

def load_from_db():
    try:
        if create_client is not None:
            supa_url = st.secrets.get("SUPABASE_URL")
            supa_key = st.secrets.get("SUPABASE_KEY")
            if supa_url and supa_key:
                supa = st.session_state.get("supabase")
                if supa is None:
                    supa = create_client(supa_url, supa_key)
                    st.session_state["supabase"] = supa

                res = supa.table("schedule_state").select("selected,assigned_hours,assigned_time_ranges").eq("id", "current").execute()
                data = None
                if hasattr(res, "data"):
                    if res.data:
                        data = res.data[0]
                elif isinstance(res, dict):
                    data = (res.get("data") or [None])[0]

                if data:
                    st.session_state.selected = data.get("selected", {}) or {}
                    raw_hours = data.get("assigned_hours", {}) or {}
                    try:
                        st.session_state.assigned_hours = {k: int(v) for k, v in raw_hours.items()}
                    except Exception:
                        st.session_state.assigned_hours = raw_hours or {}
                        
                    raw_times = data.get("assigned_time_ranges", {}) or {}
                    converted_times = {}
                    for k, v in raw_times.items():
                        try:
                            if isinstance(v, list) and len(v) == 2:
                                t_start = datetime.datetime.strptime(v[0], "%H:%M").time()
                                t_end = datetime.datetime.strptime(v[1], "%H:%M").time()
                                converted_times[k] = (t_start, t_end)
                        except Exception:
                            pass
                    st.session_state.assigned_time_ranges = converted_times
                    return
    except Exception:
        pass

    # Default empty state if Supabase fetch fails or is empty
    st.session_state.selected = {}
    st.session_state.assigned_hours = {}
    st.session_state.assigned_time_ranges = {}

def save_to_db():
    serialized_times = {}
    for k, v in st.session_state.get("assigned_time_ranges", {}).items():
        if isinstance(v, tuple) and len(v) == 2:
            serialized_times[k] = [v[0].strftime("%H:%M"), v[1].strftime("%H:%M")]

    payload = {
        "id": "current",
        "selected": st.session_state.selected,
        "assigned_hours": st.session_state.get("assigned_hours", {}),
        "assigned_time_ranges": serialized_times
    }

    try:
        if create_client is not None:
            supa_url = st.secrets.get("SUPABASE_URL")
            supa_key = st.secrets.get("SUPABASE_KEY")
            if supa_url and supa_key:
                supa = st.session_state.get("supabase")
                if supa is None:
                    supa = create_client(supa_url, supa_key)
                    st.session_state["supabase"] = supa

                supa.table("schedule_state").upsert(payload).execute()
    except Exception:
        pass

if "selected" not in st.session_state:
    load_from_db()


if "editing" not in st.session_state:
    # key of the tile currently being edited (admin only). Not persisted.
    st.session_state.editing = None


if "admin_bypass" not in st.session_state:
    # When True, admins may bypass daily and weekly caps from the Admin Overrides UI
    st.session_state.admin_bypass = False


# ---------- ADMIN AUTHENTICATION ----------

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

if "show_password_input" not in st.session_state:
    st.session_state.show_password_input = False

if LOCAL_DEV:
    # Quick local dev toggle button
    if "local_enable_admin" not in st.session_state:
        st.session_state.local_enable_admin = False

    if st.session_state.local_enable_admin:
        st.session_state.is_admin = True
        if st.button("Disable Admin (local)"):
            st.session_state.local_enable_admin = False
            st.session_state.is_admin = False
            st.rerun()
    else:
        if st.button("Enable Admin (local)"):
            st.session_state.local_enable_admin = True
            st.session_state.is_admin = True
            st.rerun()
else:
    # Production login flow
    if not st.session_state.is_admin:
        if not st.session_state.show_password_input:
            if st.button("Admin Login"):
                st.session_state.show_password_input = True
                st.rerun()
        else:
            col1, col2 = st.columns([3, 1])
            with col1:
                password = st.text_input(
                    "Admin Password",
                    type="password",
                    key="production_admin_pwd"
                )
            with col2:
                if st.button("Cancel"):
                    st.session_state.show_password_input = False
                    st.rerun()

            current_admin_pwd = ADMIN_PASSWORD or st.secrets.get("ADMIN_PASSWORD")
            current_josh_pwd = JOSH_PASSWORD or st.secrets.get("JOSH_PASSWORD")

            if password in {current_admin_pwd, current_josh_pwd}:
                st.session_state.is_admin = True
                st.session_state.show_password_input = False
                st.rerun()
    else:
        if st.button("Log Out Admin"):
            st.session_state.is_admin = False
            st.rerun()

# ---------- RULES ----------

def assignment_hours(dt):
    dt = pd.Timestamp(dt)

    if dt.dayofweek < 4:
        return 4

    if dt.dayofweek >= 5:
        return 3

    return 0

def week_key(dt):
    # Normalize to the Sunday-starting week containing `dt` (Sunday-Saturday)
    d = pd.Timestamp(dt)
    # (day.weekday(): Mon=0..Sun=6) compute days to subtract to get Sunday
    days_to_subtract = (d.weekday() + 1) % 7
    week_start = (d - pd.Timedelta(days=days_to_subtract)).normalize()
    return f"{week_start.date()}"

def weekly_hours(name, day):
    wk = week_key(day)
    total = 0

    for key, selected in st.session_state.selected.items():

        if not selected:
            continue

        d, instructor = key.split("|", 1)

        if instructor != name:
            continue

        if week_key(pd.to_datetime(d)) == wk:
            # prefer stored assigned hours, fallback to rule-based hours
            total += int(
                st.session_state.get("assigned_hours", {}).get(key, assignment_hours(d))
            )

    return total


def weekly_days_assigned(name, day):
    """Count how many active days `name` is assigned within the Sunday-Saturday
    week containing `day`."""
    wk = week_key(day)
    count = 0

    for key, selected in st.session_state.selected.items():
        if not selected:
            continue

        d, instructor = key.split("|", 1)

        if instructor != name:
            continue

        if week_key(pd.to_datetime(d)) == wk:
            # count one per day (assignments are stored per day|instructor)
            count += 1

    return count

def selected_count(day):
    return sum(
        1
        for k, v in st.session_state.selected.items()
        if v and k.startswith(f"{day}|")
    )

def tile_css(bg):
    return f"""
    button {{
        background:{bg} !important;
        color:black !important;
        border:1px solid #999 !important;
        border-radius:6px !important;
        min-height:28px !important;
        height:28px !important;
        font-size:10px !important;
        padding:2px 6px !important;
        text-align:left !important;
    }}
    """


def inject_tile_style(key, bg):
    # Best-effort CSS targeting: Streamlit containers accept a `key` parameter
    # and we attempt to scope styles to that container. This uses attribute
    # selectors that may vary across Streamlit versions; harmless if no match.
    css = f"""
    <style>
    [data-testid="stContainer"][data-key="tile_{key}"] button {{
        background: {bg} !important;
        color: black !important;
        border: 1px solid #999 !important;
        border-radius: 6px !important;
        min-height: 28px !important;
        height: 28px !important;
        font-size: 10px !important;
        padding: 2px 6px !important;
        text-align: left !important;
    }}
    </style>
    """

    st.markdown(css, unsafe_allow_html=True)

# ---------- TOGGLE ----------

def toggle(day, instructor, max_days):

    # Admins only
    if not st.session_state.is_admin:
        st.warning(
            "Admin password required to modify assignments."
        )
        return

    key = f"{day}|{instructor}"

    currently_selected = st.session_state.selected.get(
        key,
        False
    )

    # --------------------
    # Unassign
    # --------------------

    if currently_selected:

        st.session_state.selected[key] = False
        # remove assigned hours for unassigned
        try:
            st.session_state.assigned_hours.pop(key, None)
        except Exception:
            pass

        save_to_db()

        st.rerun()


    current_days = weekly_days_assigned(
        instructor,
        day
    )

    # Block if the instructor already has the maximum allowed days
    # Admins may override this limit only when `admin_bypass` is enabled
    if current_days >= max_days and not (
        st.session_state.get("is_admin") and st.session_state.get("admin_bypass", False)
    ):
        return


    # --------------------
    # Assign
    # --------------------

    st.session_state.selected[key] = True
    # set default hours for the assignment
    try:
        st.session_state.assigned_hours[key] = int(
            st.session_state.assigned_hours.get(
                key,
                assignment_hours(day)
            )
        )
    except Exception:
        st.session_state.assigned_hours[key] = int(assignment_hours(day))

    save_to_db()

    st.rerun()
    
def handle_tile_click(day, instructor, max_days):

    # Admins only
    if not st.session_state.is_admin:
        st.warning(
            "Admin password required to modify assignments."
        )
        return

    key = f"{day}|{instructor}"

    currently_selected = st.session_state.selected.get(key, False)

    # If tile is selected and already being edited, a second click hides the editor and saves
    if currently_selected and st.session_state.get("editing") == key:
        st.session_state.editing = None
        
        # Check if the "Apply to future" checkbox was checked when closing
        apply_future_key = f"future_{key}"
        if st.session_state.get(apply_future_key, False):
            target_dow = pd.Timestamp(day).dayofweek
            current_start_t = st.session_state.get("assigned_time_ranges", {}).get(key, default_time_range(day))[0]
            current_end_t = st.session_state.get("assigned_time_ranges", {}).get(key, default_time_range(day))[1]
            current_hrs = st.session_state.get("assigned_hours", {}).get(key, assignment_hours(day))
            
            master_df = load_data()
            for _, d_row in master_df[master_df["Name"] == instructor].iterrows():
                row_date = pd.Timestamp(d_row["Date"])
                if row_date.dayofweek == target_dow and row_date >= pd.Timestamp(day):
                    future_key = f"{row_date.date()}|{instructor}"
                    st.session_state.selected[future_key] = True
                    if "assigned_time_ranges" not in st.session_state:
                        st.session_state.assigned_time_ranges = {}
                    st.session_state.assigned_time_ranges[future_key] = (current_start_t, current_end_t)
                    st.session_state.assigned_hours[future_key] = current_hrs

            st.session_state[apply_future_key] = False

        save_to_db() 
        st.rerun()

    # If tile is selected but not currently being edited, start editing it.
    if currently_selected and st.session_state.get("editing") != key:
        st.session_state.editing = key
        st.rerun()

    # --------------------
    # Assign (when not currently selected)
    # --------------------

    if not currently_selected:

        current_days = weekly_days_assigned(instructor, day)

        if current_days >= max_days and not (
            st.session_state.get("is_admin") and st.session_state.get("admin_bypass", False)
        ):
            return

        st.session_state.selected[key] = True
        try:
            st.session_state.assigned_hours[key] = int(
                st.session_state.assigned_hours.get(
                    key,
                    assignment_hours(day)
                )
            )
        except Exception:
            st.session_state.assigned_hours[key] = int(assignment_hours(day))

        # open the hours editor for this tile
        st.session_state.editing = key

        save_to_db()
        st.rerun()

# ---------- MONTH ----------

months = sorted(
    df["Date"].dt.to_period("M").unique()
)

# Default to the current month when the app loads
current_period = pd.Timestamp.now().to_period("M")
try:
    default_idx = months.index(current_period)
except ValueError:
    default_idx = 0

month = st.selectbox(
    "Month",
    months,
    index=default_idx,
    format_func=lambda p: p.strftime("%B %Y")
)

month_df = df[
    df["Date"].dt.to_period("M") == month
]
#if 'cover' not in month_df.columns:
#    sys.exit()

st.title(
    f"Instructor Scheduler — {month.strftime('%B %Y')}"
)

# ---------- NAME FILTER ----------
all_names = sorted(df["Name"].unique())
name_options = ["All"] + all_names
# store filter in session_state so other code can read it
name_filter = st.selectbox(
    "Filter by name",
    name_options,
    index=0,
    key="name_filter",
    help="Show only this instructor's tiles (choose All to show everyone)"
)

# ---------- HEADERS ----------
headers = st.columns(7)

for c, d in zip(
    headers,
    ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
):
    c.markdown(f"**{d}**")

cal = calendar.Calendar(firstweekday=6)

# ---------- CALENDAR ----------

for week in cal.monthdatescalendar(
    month.year,
    month.month
):

    cols = st.columns(7)

    for col, day in zip(cols, week):

        with col:

            if day.month != month.month:
                st.empty()
                continue

            st.markdown(
                f"**{day.day}**"
            )

            day_rows = month_df[
                month_df["Date"].dt.date == day
            ].copy()

            # Skip days that have no rows in month_df
            if day_rows.empty:
                continue

            available_names = set(
                day_rows[~day_rows['cover']]["Name"]
            )

            cover_names = set(
                day_rows[day_rows['cover']]['Name']
            )

            assigned_names = set()

            for selection_key, selected in (
                st.session_state.selected.items()
            ):

                if not selected:
                    continue

                d, instructor = (
                    selection_key.split("|", 1)
                )

                if d == str(day):
                    assigned_names.add(
                        instructor
                    )

            # Admins see all available and assigned names; non-admins see only assigned (active) tiles
            if st.session_state.is_admin:
                display_names = (
                    available_names |
                    cover_names |
                    assigned_names
                )
            else:
                display_names = assigned_names

            # Apply the name filter (if not "All") so the calendar shows only that instructor
            filter_name = st.session_state.get("name_filter", "All")
            if filter_name != "All":
                display_names = {n for n in display_names if n == filter_name}

            if not display_names:
                continue

            rows = []

            for instructor in display_names:

                match = day_rows[
                    day_rows["Name"] == instructor
                ]

                if len(match):

                    rows.append(
                        match.iloc[0].to_dict()
                    )

                else:

                    master = (
                        df[
                            df["Name"] == instructor
                        ]
                        .drop_duplicates("Name")
                    )

                    if len(master):

                        info = (
                            master.iloc[0]
                            .to_dict()
                        )

                        info["Date"] = pd.Timestamp(day)

                        rows.append(info)

            display_df = pd.DataFrame(rows)

            if len(display_df) == 0:
                continue

            display_df = pd.DataFrame(rows)

            if len(display_df) == 0:
                continue

            # Sort order: 0 for selected blue tiles, 1 for gray tiles, 2 for yellow cover tiles
            display_df["sort_group"] = display_df["Name"].apply(
                lambda n: (
                    0 if st.session_state.selected.get(f"{day}|{n}", False)
                    else (1 if n not in cover_names else 2)
                )
            )

            display_df = display_df.sort_values(
                ["sort_group", "Name"],
                ascending=[True, True]
            )

            with st.container(
                height=DAY_PANEL_HEIGHT
            ):
                for _, row in display_df.iterrows():
                    instructor = row["Name"]

                    # per-instructor per-week maximum days (if present in data)
                    try:
                        max_days = int(row.get("max_days", 5))
                    except Exception:
                        max_days = 5

                    key = f"{day}|{instructor}"

                    selected = st.session_state.selected.get(key, False)

                    # Only show unselected (gray) tiles to admins
                    if not selected and not st.session_state.is_admin:
                        continue

                    week_days = weekly_days_assigned(instructor, day)

                    # Only disable for non-admins when daily cap or per-week max days reached
                    disabled = (
                        (not st.session_state.is_admin)
                        and (
                            not selected
                            and (
                                week_days >= max_days
                            )
                        )
                    )

                    # Electric blue for selected tiles, grey otherwise
                    bg = ("#00BFFF" if selected else ("#eaeda8" if row['cover'] else "#d9d9d9"))

                    # Only show selected tiles to non-admins; admins see both selected and unselected
                    if not selected and not st.session_state.is_admin:
                        continue

                    week_days = weekly_days_assigned(instructor, day)

                    # Only disable for non-admins when daily cap or per-week max days reached
                    disabled = (
                        (not st.session_state.is_admin)
                        and (
                            not selected
                            and (
                                assigned_today >= 5
                                or week_days >= max_days
                            )
                        )
                    )

                    # Format compact time label inline if selected (using stored or default times)
                    time_suffix = ""
                    if selected:
                        if "assigned_time_ranges" in st.session_state and key in st.session_state.assigned_time_ranges:
                            r_start, r_end = st.session_state.assigned_time_ranges[key]
                        else:
                            r_start, r_end = default_time_range(day)
                            
                        start_h, start_ampm = format_short_time(r_start)
                        end_h, end_ampm = format_short_time(r_end)
                        
                        if start_h and end_h:
                            # If both are in the same half of the day (e.g., both pm), drop the first 'p'
                            if start_ampm == end_ampm:
                                time_suffix = f" ({start_h}-{end_h}{end_ampm})"
                            else:
                                time_suffix = f" ({start_h}{start_ampm}-{end_h}{end_ampm})"

                    label = f"{instructor}{time_suffix}"

                    # Use stylable_container for both admins and non-admins so the appearance is identical
                    with stylable_container(key=f"tile_{key}", css_styles=tile_css(bg)):
                        if st.session_state.is_admin:
                            if st.button(label, key=f"btn_{key}", disabled=disabled, width="stretch"):
                                handle_tile_click(day, instructor, max_days)

                            # If selected and admin and this tile is being edited, allow choosing start/end hours via dropdowns
                            if selected and st.session_state.is_admin and st.session_state.get("editing") == key:
                                default_start_t, default_end_t = default_time_range(day)
                                
                                # Retrieve stored start/end or use defaults
                                stored_range = st.session_state.get("assigned_time_ranges", {}).get(key, (default_start_t, default_end_t))
                                
                                # Generate whole-hour options in 12-hour AM/PM format
                                hour_options = []
                                hour_mapping = {}
                                for h in range(24):
                                    t_obj = datetime.time(h, 0)
                                    # Format as 12h e.g., "4:00 PM"
                                    label_str = t_obj.strftime("%I:00 %p").lstrip("0")
                                    hour_options.append(label_str)
                                    hour_mapping[label_str] = t_obj

                                default_start_label = stored_range[0].strftime("%I:00 %p").lstrip("0")
                                default_end_label = stored_range[1].strftime("%I:00 %p").lstrip("0")
                                
                                if default_start_label not in hour_options:
                                    default_start_label = hour_options[0]
                                if default_end_label not in hour_options:
                                    default_end_label = hour_options[len(hour_options)-1]

                                with st.container():
                                    start_label = st.selectbox(
                                        "Start Time", 
                                        options=hour_options, 
                                        index=hour_options.index(default_start_label),
                                        key=f"start_{key}"
                                    )
                                    end_label = st.selectbox(
                                        "End Time", 
                                        options=hour_options, 
                                        index=hour_options.index(default_end_label),
                                        key=f"end_{key}"
                                    )
                                    
                                    # --- CHECKBOX ---
                                    st.checkbox(
                                        f"Apply to all future {pd.Timestamp(day).strftime('%A')}s", 
                                        value=False, 
                                        key=f"future_{key}"
                                    )

                                    # --- INACTIVE BUTTON ---
                                    if st.button("Inactive", key=f"clear_{key}"):
                                        apply_future_key = f"future_{key}"
                                        propagate_future = st.session_state.get(apply_future_key, False)
                                        
                                        if propagate_future:
                                            target_dow = pd.Timestamp(day).dayofweek
                                            master_df = load_data()
                                            for _, d_row in master_df[master_df["Name"] == instructor].iterrows():
                                                row_date = pd.Timestamp(d_row["Date"])
                                                if row_date.dayofweek == target_dow and row_date >= pd.Timestamp(day):
                                                    future_key = f"{row_date.date()}|{instructor}"
                                                    st.session_state.selected[future_key] = False
                                                    if "assigned_time_ranges" in st.session_state:
                                                        st.session_state.assigned_time_ranges.pop(future_key, None)
                                                    if "assigned_hours" in st.session_state:
                                                        st.session_state.assigned_hours.pop(future_key, None)
                                            st.session_state[apply_future_key] = False
                                        else:
                                            if f"{day}|{instructor}" in st.session_state.selected:
                                                st.session_state.selected[f"{day}|{instructor}"] = False
                                            if "assigned_time_ranges" in st.session_state:
                                                st.session_state.assigned_time_ranges.pop(key, None)
                                            if "assigned_hours" in st.session_state:
                                                st.session_state.assigned_hours.pop(key, None)
                                            if st.session_state.get("editing") == key:
                                                st.session_state.editing = None
                                                
                                        save_to_db()
                                        st.rerun()

                                    # 3. Deactivate / Clear Button
                                    if st.button("Inactive", key=f"clear_{day}_{instructor}"):
                                        if f"{day}|{instructor}" in st.session_state.selected:
                                            del st.session_state.selected[f"{day}|{instructor}"]
                                        
                                        if "assigned_time_ranges" in st.session_state:
                                            st.session_state.assigned_time_ranges.pop(key, None)
                                            
                                        save_to_db()
                                        st.rerun()

                                start_t = hour_mapping[start_label]
                                end_t = hour_mapping[end_label]

                                try:
                                    start_dt = datetime.datetime.combine(datetime.date.today(), start_t)
                                    end_dt = datetime.datetime.combine(datetime.date.today(), end_t)
                                    
                                    diff_seconds = (end_dt - start_dt).total_seconds()
                                    hours_int = max(0, round(diff_seconds / 3600))
                                except Exception:
                                    hours_int = assignment_hours(day)

                                if "assigned_time_ranges" not in st.session_state:
                                    st.session_state.assigned_time_ranges = {}
                                st.session_state.assigned_time_ranges[key] = (start_t, end_t)

                                current_stored_hrs = int(st.session_state.get("assigned_hours", {}).get(key, assignment_hours(day)))
                                if hours_int != current_stored_hrs:
                                    st.session_state.assigned_hours[key] = hours_int
                                    if hours_int == 0:
                                        st.session_state.selected[key] = False
                                        if st.session_state.get("editing") == key:
                                            st.session_state.editing = None
                                    save_to_db()
                                    st.rerun()
                        else:
                            # Render a disabled Streamlit button inside the stylable container so it matches the admin's blue button styling perfectly
                            st.button(label, key=f"btn_{key}", disabled=True, width="stretch")

# ---------- ASSIGNMENTS ----------

with st.expander("Assignments"):

    rows = []

    for key, selected in (
        st.session_state.selected.items()
    ):

        if selected:

            d, instructor = (
                key.split("|", 1)
            )

            rows.append({
                "Date": d,
                "Instructor": instructor
            })

    if rows:

        st.dataframe(
            pd.DataFrame(rows)
            .sort_values(
                ["Date", "Instructor"]
            ),
            width='stretch'
        )

# ---------- SAVE ----------

if st.button("Refresh"):
    load_from_db()
    st.rerun()

st.caption(
    "Selected instructors appear first. "
    "Weekly totals are shown on every tile. "
    ""
)

# ---------- ADMIN OVERRIDES ----------

if st.session_state.is_admin:

    with st.expander("Admin Overrides"):
        # --------------------
        # ADMIN BYPASS (require password to enable)
        # --------------------
        st.subheader("Admin Bypass")

        if st.session_state.get("admin_bypass"):
            st.success("Admin bypass is ENABLED")
            if st.button("Disable Bypass"):
                st.session_state.admin_bypass = False
                st.rerun()
        else:
            if LOCAL_DEV:
                st.info("Local dev: enable bypass without password")
                if st.button("Enable Bypass (local)"):
                    st.session_state.admin_bypass = True
                    st.rerun()
            else:
                bypass_pwd = st.text_input("Admin Password to enable bypass", type="password", key="bypass_pwd")
                if st.button("Enable Bypass"):
                    # Read admin passwords from Streamlit secrets when available
                    ADMIN_PASSWORD = ADMIN_PASSWORD or st.secrets.get("ADMIN_PASSWORD")
                    JOSH_PASSWORD = JOSH_PASSWORD or st.secrets.get("JOSH_PASSWORD")

                    if bypass_pwd in {ADMIN_PASSWORD, JOSH_PASSWORD}:
                        st.session_state.admin_bypass = True
                        st.success("Admin bypass enabled")
                        st.rerun()
                    else:
                        st.error("Invalid admin password")

        override_day = st.date_input(
            "Date"
        )

        all_instructors = sorted(
            df["Name"].unique()
        )

        assigned = sorted([
            name
            for name in all_instructors
            if st.session_state.selected.get(
                f"{override_day}|{name}",
                False
            )
        ])

        # =====================
        # SWAP
        # =====================

        st.subheader(
            "Swap Instructor"
        )

        if assigned:

            old_name = st.selectbox(
                "Replace",
                assigned,
                key=f"swap_old_{override_day}"
            )

            new_name = st.selectbox(
                "With",
                all_instructors,
                key=f"swap_new_{override_day}"
            )

            if st.button(
                "Swap",
                key=f"swap_btn_{override_day}"
            ):

                old_key = f"{override_day}|{old_name}"
                new_key = f"{override_day}|{new_name}"

                # move selection and assigned hours
                st.session_state.selected.pop(old_key, None)
                st.session_state.selected[new_key] = True

                hrs = st.session_state.get("assigned_hours", {}).pop(old_key, None)
                if hrs is None:
                    hrs = assignment_hours(override_day)
                try:
                    st.session_state.assigned_hours[new_key] = int(hrs)
                except Exception:
                    st.session_state.assigned_hours[new_key] = assignment_hours(override_day)

                save_to_db()

                st.toast(f"Replaced {old_name} with {new_name}")

                # if the old assignment was being edited, move the editor to the new key
                if st.session_state.get("editing") == old_key:
                    st.session_state.editing = new_key

                st.rerun()


        # =====================
        # REMOVE
        # =====================

        st.subheader("Remove Instructor Assignment")

        if assigned:

            remove_name = st.selectbox(
                "Assigned Instructor",
                assigned,
                key=f"remove_name_{override_day}"
            )

            if st.button("Remove Assignment", key=f"remove_btn_{override_day}"):
                rem_key = f"{override_day}|{remove_name}"
                st.session_state.selected.pop(rem_key, None)
                st.session_state.assigned_hours.pop(rem_key, None)
                if st.session_state.get("editing") == rem_key:
                    st.session_state.editing = None
                save_to_db()
                st.toast(f"Removed {remove_name}")
                st.rerun()

        st.divider()

        # =====================
        # ADD
        # =====================

        st.subheader(
            "Add Instructor Assignment"
        )

        add_name = st.selectbox(
            "Instructor",
            [
                instructor for instructor
                in all_instructors
                if instructor
                not in assigned
            ],
            key=f"add_name_{override_day}"
        )

        if st.button("Add Assignment", key=f"add_btn_{override_day}"):
            assignment_key = f"{override_day}|{add_name}"
            if not st.session_state.selected.get(assignment_key, False):
                st.session_state.selected[assignment_key] = True
                try:
                    st.session_state.assigned_hours[assignment_key] = int(assignment_hours(override_day))
                except Exception:
                    st.session_state.assigned_hours[assignment_key] = assignment_hours(override_day)
                save_to_db()
                st.toast(f"Added {add_name}")
                # open editor for newly added assignment
                st.session_state.editing = assignment_key
                st.rerun()


    # end of Admin Overrides expander

    # --------------------
    # PAY PERIOD TOTALS (admin-only)
    # --------------------

    st.divider()
    st.header("Pay Period Totals")

    try:
        default_start = month.start_time.date()
        default_end = month.end_time.date()
    except Exception:
        default_start = pd.Timestamp.now().date()
        default_end = pd.Timestamp.now().date()

    range_start = st.date_input("Start date", value=default_start, key="pay_start_global")
    range_end = st.date_input("End date", value=default_end, key="pay_end_global")

    if range_start > range_end:
        st.error("Start date must be on or before end date")
    else:
        if st.button("Compute Totals", key="compute_totals_pay"):
            totals = {name: 0 for name in sorted(df["Name"].unique())}

            for key, hrs in st.session_state.get("assigned_hours", {}).items():
                try:
                    d_str, instr = key.split("|", 1)
                    d = pd.to_datetime(d_str).date()
                except Exception:
                    continue

                if range_start <= d <= range_end:
                    try:
                        totals[instr] = totals.get(instr, 0) + int(hrs)
                    except Exception:
                        pass

            res = (
                pd.DataFrame(
                    [(k, v) for k, v in totals.items()],
                    columns=["Instructor", "Hours"]
                )
                .sort_values("Hours", ascending=False)
                .reset_index(drop=True)
            )

            st.session_state.pay_period_totals = res

        if st.session_state.get("pay_period_totals") is not None:
            st.dataframe(st.session_state.pay_period_totals, width=400)

        # (Remove UI lives inside Admin Overrides)
