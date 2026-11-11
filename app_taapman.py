import importlib.metadata
import json
import os
import platform
import random
import re
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd
import streamlit as st

PROJECT_NAME = "Sheetkaksh Control System"
# The local model that Taapman AI uses. Change it here, or set the TAAPMAN_MODEL environment variable.
OLLAMA_MODEL = os.environ.get("TAAPMAN_MODEL", "llama3.2")

st.set_page_config(page_title=PROJECT_NAME, page_icon="🌡️", layout="wide")
st.markdown(
    """
    <style>
    /* Hide Streamlit's own Deploy button and its menu. The button that reopens the left panel stays visible.
       To get them back, delete the next line. */
    [data-testid='stAppDeployButton'], [data-testid='stMainMenu'] {display: none;}
    /* The three-dot button shows only the dots, without the small arrow. */
    .st-key-menu_wrap [data-testid='stPopoverButton'] [data-testid='stIconMaterial'] {display: none;}
    .st-key-menu_wrap [data-testid='stPopoverButton'] p {font-size: 1.6rem; font-weight: 700; line-height: 1;}
    .st-key-menu_wrap {display: flex; justify-content: flex-end; padding-top: 0.6rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Chamber limits. The checks are done in plain Python code (never by an AI model),
# so a reading is always compared with the limits correctly.
# ---------------------------------------------------------------------------

# DEMO limits only. Real limits must be approved by the cold-store operator.
DEFAULT_LIMITS = {
    "Chamber 1": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 65.0, "h_max": 80.0},
    "Chamber 2": {"produce": "Demo produce", "t_min": 2.0, "t_max": 6.0, "h_min": 60.0, "h_max": 75.0},
    "Chamber 3": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 65.0, "h_max": 80.0},
    "Chamber 4": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 60.0, "h_max": 75.0},
    "Chamber 5": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.5, "h_min": 60.0, "h_max": 75.0},
}
# A chamber that appears in the live data without saved limits starts with these demo limits.
GENERIC_LIMITS = {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 60.0, "h_max": 75.0}

if "limits" not in st.session_state:
    st.session_state["limits"] = {name: dict(lim) for name, lim in DEFAULT_LIMITS.items()}
LIMITS = st.session_state["limits"]


LIMIT_WIDGET_KEY = {"t_min": "tmin", "t_max": "tmax", "h_min": "hmin", "h_max": "hmax"}
# Limit changes asked for in the Taapman AI chat are applied here, before the left panel is drawn,
# so the manual-settings boxes show the new numbers.
for _chamber, _key, _value in st.session_state.pop("pending_limits", []):
    LIMITS[_chamber][_key] = _value
    st.session_state["limits_version"] = st.session_state.get("limits_version", 0) + 1   # new box keys: the boxes show the new numbers


# ---------------------------------------------------------------------------
# Left panel: sensor connection and manual settings (the Taapman AI chat is added at the bottom, at the end of this file)
# ---------------------------------------------------------------------------

st.sidebar.header("Sensor connection")
mode = st.sidebar.radio("Data source", ["Demo data", "Wi-Fi sensor (HTTP API)"])
if mode == "Wi-Fi sensor (HTTP API)":
    sensor_url = st.sidebar.text_input(
        "Sensor API URL",
        placeholder="http://192.168.1.50/api/readings",
        help="Sensor/ESP32 ka Wi-Fi IP aur readings endpoint daalein.",
    )
    refresh_seconds = st.sidebar.slider("Refresh interval (seconds)", 1, 30, 3)
else:
    sensor_url = ""
    refresh_seconds = 3

st.sidebar.divider()
manual_settings = st.sidebar.toggle(
    "Manual temperature and humidity settings",
    key="manual_settings",
    help="Open the chamber limits and set temperature and humidity by hand.",
)
if manual_settings:
    with st.sidebar.container(border=True):
        st.markdown("**Chamber limits (set by operator)**")
        st.caption("Demo values. Real limits must be approved by the cold-store operator.")
        live_names = [row["Chamber"] for row in st.session_state.get("current_chambers", [])]
        for name in live_names or list(DEFAULT_LIMITS):
            lim = LIMITS.setdefault(name, dict(GENERIC_LIMITS))
            st.markdown(f"**{name}**")
            lim["produce"] = st.text_input("Produce label", lim["produce"], key=f"{name}_produce")
            col_a, col_b = st.columns(2)
            lim["t_min"] = col_a.number_input("Temp min (°C)", value=lim["t_min"], step=0.5, key=f"{name}_tmin_{st.session_state.get('limits_version', 0)}")
            lim["t_max"] = col_b.number_input("Temp max (°C)", value=lim["t_max"], step=0.5, key=f"{name}_tmax_{st.session_state.get('limits_version', 0)}")
            lim["h_min"] = col_a.number_input("Humidity min (%)", value=lim["h_min"], step=1.0, key=f"{name}_hmin_{st.session_state.get('limits_version', 0)}")
            lim["h_max"] = col_b.number_input("Humidity max (%)", value=lim["h_max"], step=1.0, key=f"{name}_hmax_{st.session_state.get('limits_version', 0)}")
            if lim["t_min"] >= lim["t_max"] or lim["h_min"] >= lim["h_max"]:
                st.warning(f"{name}: each minimum must be lower than its maximum.")


def check_chamber(row, limits):
    """Compare one chamber's reading with its limits. Returns (status, list of problems)."""
    lim = limits.get(row["Chamber"])
    if lim is None:
        return "NO LIMITS", ["No approved limits are set for this chamber"]
    temp = round(float(row["Temperature (°C)"]), 1)
    hum = round(float(row["Humidity (%)"]), 1)
    problems = []
    if temp > lim["t_max"]:
        problems.append(f"Temperature {temp} °C is ABOVE the maximum {lim['t_max']} °C")
    if temp < lim["t_min"]:
        problems.append(f"Temperature {temp} °C is BELOW the minimum {lim['t_min']} °C")
    if hum > lim["h_max"]:
        problems.append(f"Humidity {hum} % is ABOVE the maximum {lim['h_max']} %")
    if hum < lim["h_min"]:
        problems.append(f"Humidity {hum} % is BELOW the minimum {lim['h_min']} %")
    return ("ALERT" if problems else "OK"), problems


def num(value, digits=1):
    """Round a reading for display, whatever the sensor sends."""
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Alerts. One alert per chamber stays active until that chamber's reading is back
# inside its limits. Each new alert opens a popup that describes it.
# ---------------------------------------------------------------------------

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def update_alerts(statuses):
    """Raise an alert when a chamber goes out of range, and resolve it when it is back in range.
    Returns the alerts raised in this refresh."""
    alerts = st.session_state.setdefault("alerts", [])
    now = datetime.now().strftime(TIME_FORMAT)
    active = {a["Chamber"]: a for a in alerts if a["Resolved"] is None}
    raised = []
    for chamber, (status, problems) in statuses.items():
        if status == "ALERT":
            if chamber not in active:
                alert = {"Chamber": chamber, "Problem": "; ".join(problems), "Raised": now, "Resolved": None, "Ended because": ""}
                alerts.append(alert)
                raised.append(alert)
        elif chamber in active:
            active[chamber]["Resolved"] = now
            active[chamber]["Ended because"] = "Reading back inside limits"
    for chamber, alert in active.items():
        if chamber not in statuses:
            alert["Resolved"] = now
            alert["Ended because"] = "Chamber no longer in the live data"
    return raised


def elapsed(since_text):
    """How long ago a time text like '2026-11-06 17:42:10' was, for example '3 min 20 s'."""
    try:
        seconds = int((datetime.now() - datetime.strptime(since_text, TIME_FORMAT)).total_seconds())
    except ValueError:
        return ""
    minutes, secs = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


@st.dialog("🚨 Chamber alert")
def alert_popup(new_chambers):
    """Popup with the description of every active alert. The ones that just appeared are marked NEW."""
    statuses = st.session_state.get("chamber_status", {})
    active = [a for a in st.session_state.get("alerts", []) if a["Resolved"] is None]
    for alert in active:
        problem_now = "; ".join(statuses.get(alert["Chamber"], ("", []))[1]) or alert["Problem"]
        tag = "NEW  |  " if alert["Chamber"] in new_chambers else ""
        st.error(f"**{tag}{alert['Chamber']}**\n\n{problem_now}")
        st.caption(f"Raised at {alert['Raised']}")
    st.write(
        "Staff should inspect the chamber and record what they did in the Staff action log. "
        "The alert stays listed under Active alerts until the reading is back inside its limits."
    )
    if st.button("Close", key="close_alert_popup", width="stretch"):
        st.rerun()


def render_alerts_section():
    """Active alerts (until resolved) and, below them, the resolved ones. Shown above the staff log."""
    st.subheader("🚨 Active alerts")
    alerts = st.session_state.get("alerts", [])
    statuses = st.session_state.get("chamber_status", {})
    active = [a for a in alerts if a["Resolved"] is None]
    if active:
        st.caption("An alert stays here until the chamber's reading is back inside its limits.")
        for alert in active:
            problem_now = "; ".join(statuses.get(alert["Chamber"], ("", []))[1]) or "No reading right now"
            st.error(f"**{alert['Chamber']}**: {problem_now}\n\nRaised at {alert['Raised']}  |  active for {elapsed(alert['Raised'])}")
    else:
        st.success("✅ No active alerts. All chambers are within their limits.")
    resolved = [a for a in alerts if a["Resolved"] is not None]
    if resolved:
        with st.expander(f"Resolved alerts ({len(resolved)})"):
            st.dataframe(pd.DataFrame(resolved[::-1]), width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Taapman AI. The model never reads numbers itself and never compares them:
# it calls these tools, which return data that Python has already checked.
# ---------------------------------------------------------------------------

def _find_chamber(text):
    """Match 'Chamber 4', 'chamber4' or '4' to a chamber name in the live data."""
    rows = st.session_state.get("current_chambers", [])
    wanted = str(text).strip().lower()
    digits = "".join(ch for ch in wanted if ch.isdigit())
    for row in rows:
        if row["Chamber"].lower() == wanted:
            return row["Chamber"]
    for row in rows:
        if digits and "".join(ch for ch in row["Chamber"] if ch.isdigit()) == digits:
            return row["Chamber"]
    return None


def tool_list_chambers() -> dict:
    """List all chambers and the produce label set for each."""
    rows = st.session_state.get("current_chambers", [])
    if not rows:
        return {"error": "No live chamber data right now."}
    return {"chambers": [{"chamber": r["Chamber"], "produce": LIMITS.get(r["Chamber"], {}).get("produce", "not set")} for r in rows]}


def tool_get_chamber_status(chamber: str) -> dict:
    """Get the latest reading, approved limits, status (OK or ALERT) and problems of one chamber.

    Args:
        chamber: The chamber name or number, for example "Chamber 4" or "4".
    """
    name = _find_chamber(chamber)
    if name is None:
        return {"error": f"No chamber matches '{chamber}'. To check every chamber use get_all_alerts."}
    row = next(r for r in st.session_state["current_chambers"] if r["Chamber"] == name)
    status, problems = st.session_state["chamber_status"][name]
    return {
        "chamber": name,
        "status": status,
        "problems": problems,
        "latest_reading": {
            "temperature_c": row["Temperature (°C)"],
            "humidity_percent": row["Humidity (%)"],
            "moisture_percent": row["Moisture (%)"],
        },
        "approved_limits": LIMITS.get(name),
    }


def tool_get_all_alerts() -> dict:
    """Check every chamber at once and list which chambers have problems."""
    statuses = st.session_state.get("chamber_status")
    if not statuses:
        return {"error": "No live chamber data right now."}
    with_problems = [{"chamber": c, "problems": p} for c, (s, p) in statuses.items() if s == "ALERT"]
    return {"chambers_checked": len(statuses), "chambers_with_problems": with_problems}


def tool_get_produce_limits(produce: str) -> dict:
    """Get the approved storage limits for a produce from the chamber settings.

    Args:
        produce: The produce name, for example "mango" or "apple".
    """
    words = produce.lower().split()
    found = []
    for chamber, lim in LIMITS.items():
        label = lim["produce"].strip().lower()
        if label and any(w == label or w.startswith(label) for w in words):
            found.append({
                "chamber": chamber,
                "temp_min_c": lim["t_min"], "temp_max_c": lim["t_max"],
                "humidity_min_percent": lim["h_min"], "humidity_max_percent": lim["h_max"],
            })
    if found:
        return {"produce": produce, "limits_by_chamber": found}
    return {"error": "No approved limits are stored for " + produce + ". Tell the user you do not have approved limits for it."}


def limit_request(user_text):
    """Understand a chat request to change temperature or humidity limits, for example
    'set chamber 2 max temperature to 6' or 'change humidity of chamber 3 between 60 and 70'.
    Done in plain code, with checks, never by the AI model.
    Returns None if the text is not such a request, else (reply, changes)."""
    low = user_text.lower()
    if not re.search(r"\b(set|change|make|update|adjust|lower|raise|increase|decrease|reduce|modify)\b", low):
        return None
    has_t = bool(re.search(r"\b(temp|temperature)\b", low))
    has_h = bool(re.search(r"\b(humidity|rh)\b", low))
    if not (has_t or has_h):
        return None
    example = "Example: set chamber 2 max temperature to 6, or: change chamber 3 humidity between 60 and 70."
    if has_t and has_h:
        return "Please change temperature and humidity one at a time. " + example, []
    kind = "t" if has_t else "h"
    label, unit, low_ok, high_ok = ("Temperature", "°C", -10.0, 40.0) if kind == "t" else ("Humidity", "%", 0.0, 100.0)

    live = [r["Chamber"] for r in st.session_state.get("current_chambers", [])] or list(LIMITS)
    if re.search(r"\ball\b", low) and "chamber" in low:
        targets = live
    else:
        wanted = re.search(r"chamber\s*(\d+)", low)
        if not wanted:
            return "Which chamber? " + example, []
        name = _find_chamber(wanted.group(1))
        if name is None:
            return f"I could not find Chamber {wanted.group(1)}. Chambers connected: {', '.join(live)}.", []
        targets = [name]

    rest = re.sub(r"chambers?\s*\d+", " ", low)
    numbers = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", rest)]
    min_pos = [m.start() for m in re.finditer(r"\b(min|minimum|lowest)\b", rest)]
    max_pos = [m.start() for m in re.finditer(r"\b(max|maximum|highest|upper)\b", rest)]
    new_min = new_max = None
    if len(numbers) == 2 and not (min_pos and max_pos):
        new_min, new_max = sorted(numbers)
    elif len(numbers) == 2:
        first, second = numbers
        if min_pos[0] < max_pos[0]:
            new_min, new_max = first, second
        else:
            new_max, new_min = first, second
    elif len(numbers) == 1 and bool(min_pos) != bool(max_pos):
        if min_pos:
            new_min = numbers[0]
        else:
            new_max = numbers[0]
    else:
        return "I need the new number(s) and whether it is the minimum or maximum. " + example, []

    changes, lines = [], []
    for name in targets:
        old = LIMITS.setdefault(name, dict(GENERIC_LIMITS))
        lo = new_min if new_min is not None else old[f"{kind}_min"]
        hi = new_max if new_max is not None else old[f"{kind}_max"]
        for value in (lo, hi):
            if not low_ok <= value <= high_ok:
                return f"{label} must be between {low_ok:g} and {high_ok:g} {unit}. Nothing was changed.", []
        if lo >= hi:
            return f"{name}: the minimum ({lo:g}) must be lower than the maximum ({hi:g}). Nothing was changed.", []
        changes += [(name, f"{kind}_min", lo), (name, f"{kind}_max", hi)]
        lines.append(f"{name}: {label.lower()} limits are now {lo:g} to {hi:g} {unit} (were {old[f'{kind}_min']:g} to {old[f'{kind}_max']:g}).")
    reply = ("Done. " + " ".join(lines) +
             " The OK or ALERT checks use these limits from now on. This changes the monitoring limits in the app; "
             "it does not move the physical cold-room controls.")
    return reply, changes


TAAPMAN_TOOLS = {f.__name__: f for f in [tool_list_chambers, tool_get_chamber_status, tool_get_all_alerts, tool_get_produce_limits]}

TAAPMAN_SYSTEM = (
    "You are Taapman AI, the assistant of the Sheetkaksh cold-storage monitoring kit. "
    "You help staff understand temperature and relative humidity in multi-chamber cold storage. "
    "Use simple, short answers. "
    "For one chamber, use tool_get_chamber_status. To check every chamber or find problems, use tool_get_all_alerts. "
    "For the limits of a produce, use tool_get_produce_limits. To list chambers, use tool_list_chambers. "
    "NEVER state a storage temperature or humidity from your own knowledge. "
    "If a tool returns an error or no limits, say: I do not have approved limits for that; please check with the operator. "
    "Trust the status and problems in tool results; do not compare numbers yourself. "
    "When you give limits, say they are the approved limits set for that chamber. "
    "If a chamber has a problem, say what is wrong and suggest what staff should inspect, such as the door seal, fans or sensor. "
    "You recommend only; staff or the existing cold-room controls change the cold-room settings. Requests to change the temperature or humidity limits are handled by the app itself, not by you. "
    "You cannot change any chamber setting, temperature, humidity, fan or refrigeration. If staff ask you to set or change anything, say that you cannot change chamber settings, that you only monitor and recommend, and that staff must adjust the controls themselves. Then give that chamber's current reading and one recommended check. "
    "Do not write poems, jokes or code. Stay on cold-storage topics."
)


def ask_taapman(user_text, model):
    """Send a question to Ollama, run the tools it asks for, and return (answer, tools used)."""
    # Limit changes are handled in code (with checks), without asking the model.
    request = limit_request(user_text)
    if request is not None:
        reply, changes = request
        if changes:
            st.session_state["pending_limits"] = changes
        return reply, (["set_limits"] if changes else [])
    # Safety rule in code: other requests to change a setting are refused without asking the model.
    lowered = user_text.lower().strip()
    if lowered.startswith("please "):
        lowered = lowered[7:]
    command_words = ("set ", "change ", "turn ", "switch ", "increase ", "decrease ", "lower ", "raise ", "reduce ", "adjust ", "start ", "stop ")
    if lowered.startswith(command_words):
        return ("I can't change chamber settings. I only monitor and recommend. "
                "Staff must adjust the controls themselves. "
                "Ask me for a chamber's status and I'll tell you what to check."), []
    import ollama

    convo = st.session_state.setdefault("taapman_convo", [{"role": "system", "content": TAAPMAN_SYSTEM}])
    convo.append({"role": "user", "content": user_text})
    used = []
    for _ in range(5):
        reply = ollama.chat(model=model, messages=convo, tools=list(TAAPMAN_TOOLS.values()), options={"temperature": 0})
        convo.append(reply.message)
        if not reply.message.tool_calls:
            return reply.message.content or "", used
        for call in reply.message.tool_calls:
            fn = TAAPMAN_TOOLS.get(call.function.name)
            try:
                result = fn(**call.function.arguments) if fn else {"error": "Unknown tool"}
            except TypeError:
                result = {"error": "Wrong arguments for this tool. Try again."}
            used.append(call.function.name.replace("tool_", ""))
            convo.append({"role": "tool", "tool_name": call.function.name, "content": str(result)})
    return "I could not finish that. Please ask again in a simpler way.", used


def render_chat_section():
    """Taapman AI chat. It sits at the bottom of the left panel and is always available."""
    st.divider()
    head, clear = st.columns([3, 2])
    head.markdown("#### 💬 Taapman AI")
    if clear.button("Clear chat", key="clear_chat", width="stretch"):
        st.session_state["taapman_chat"] = []
        st.session_state.pop("taapman_convo", None)
    st.caption("Ask about your chambers. Answers come from the live readings and the limits. Taapman AI only recommends; staff act.")
    history = st.session_state.setdefault("taapman_chat", [])
    messages = st.container(height=330)
    question = st.chat_input("Ask Taapman AI, for example: Which chamber has a problem?")
    with messages:
        if not history and not question:
            st.caption("No messages yet. Try: Which chamber has a problem?")
        for message in history:
            with st.chat_message(message["role"]):
                st.write(message["content"])
                if message.get("tools"):
                    st.caption("Checked: " + ", ".join(message["tools"]))
        if question:
            history.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.write(question)
            with st.chat_message("assistant"):
                with st.spinner("Taapman AI is thinking..."):
                    used = []
                    try:
                        answer, used = ask_taapman(question, OLLAMA_MODEL)
                    except ImportError:
                        answer = "The ollama package is not installed. In the terminal run: python -m pip install ollama"
                    except Exception as exc:
                        answer = f"Taapman AI could not answer. Check that the Ollama app is running and the model is downloaded (ollama pull {OLLAMA_MODEL}). Details: {exc}"
                st.write(answer)
                if used:
                    st.caption("Checked: " + ", ".join(used))
            history.append({"role": "assistant", "content": answer, "tools": used})
            if st.session_state.get("pending_limits"):
                st.rerun()   # redraw so the left panel and the cards use the new limits


# ---------------------------------------------------------------------------
# Project details (the floating panel opened from the three-dot menu)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=20, show_spinner=False)
def ollama_info():
    """Ask the local Ollama server for its version and for details of the model Taapman AI uses."""
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").replace("0.0.0.0", "127.0.0.1")
    if not host.startswith("http"):
        host = "http://" + host
    info = {"server": None, "model": None, "error": None}
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/version", timeout=2) as response:
            info["server"] = json.loads(response.read().decode("utf-8")).get("version")
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=2) as response:
            models = json.loads(response.read().decode("utf-8")).get("models", [])
        for m in models:
            if m.get("name") in (OLLAMA_MODEL, OLLAMA_MODEL + ":latest"):
                info["model"] = m
    except Exception as exc:
        info["error"] = str(exc)
    return info


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def render_details():
    st.subheader("Details")
    st.markdown(f"**{PROJECT_NAME}**")
    st.write(
        "Sheetkaksh is a retrofit monitoring system for existing cold stores of fruits and vegetables. "
        "Sensors in each chamber send temperature and humidity readings through a controller to a dashboard and app, "
        "which raise an alert when a reading leaves its set limits. Staff inspect the chamber and record what they did."
    )
    st.markdown(
        "**Configurations:** Basic (monitoring), Smart (monitoring plus a control-interface prototype, only where the "
        "existing plant supports it) and AI Premium (adds camera-based produce identification, still experimental)."
    )
    st.markdown("**Programme:** BIRAC E-YUVA proposal (draft), 12-month prototype and validation plan.")

    st.markdown("##### Taapman AI")
    info = ollama_info()
    model = info["model"] or {}
    details = model.get("details", {})
    ai_rows = [
        {"Item": "Assistant", "Value": "Taapman AI, runs locally on this computer through Ollama"},
        {"Item": "Language model", "Value": OLLAMA_MODEL},
        {"Item": "Model version, size and format", "Value": (
            f"{model.get('name', OLLAMA_MODEL)}: {details.get('parameter_size', '?')} parameters, "
            f"{details.get('quantization_level', '?')}, family {details.get('family', '?')}"
            if model else "Not found in Ollama. Run: ollama pull " + OLLAMA_MODEL)},
        {"Item": "Ollama server version", "Value": info["server"] or "Ollama is not running"},
        {"Item": "Ollama Python library", "Value": package_version("ollama")},
        {"Item": "How it answers", "Value": "From live readings and operator limits through tools. Python code decides OK or ALERT; the model only explains."},
        {"Item": "What it may do", "Value": "Monitor and recommend. On request in the chat it can change the temperature and humidity limits (checked by code); it cannot move physical cold-room controls."},
    ]
    st.dataframe(pd.DataFrame(ai_rows), hide_index=True, width="stretch")
    if info["error"]:
        st.caption(f"Could not reach Ollama: {info['error']}")
    st.button("Refresh details", key="refresh_details", on_click=ollama_info.clear)

    st.markdown("##### Software")
    st.dataframe(pd.DataFrame([
        {"Item": "Python", "Value": platform.python_version()},
        {"Item": "Streamlit", "Value": st.__version__},
        {"Item": "pandas", "Value": package_version("pandas")},
        {"Item": "Operating system", "Value": f"{platform.system()} {platform.release()}"},
    ]), hide_index=True, width="stretch")

    st.markdown("##### Status")
    st.markdown(
        "- Running on demo data; real sensor input uses the Wi-Fi sensor option in the left panel.\n"
        "- Limits are demo values until the cold-store operator approves real ones.\n"
        "- Every new alert opens a popup, and stays under Active alerts until the reading is back inside its limits.\n"
        "- Readings, alerts and the staff log are kept only while the app is open (no database yet).\n"
        "- Photo-based produce identification is not in this version and will be added later."
    )


# ---------------------------------------------------------------------------
# Live data
# ---------------------------------------------------------------------------

def get_demo_data():
    base = [
        {"Chamber": "Chamber 1", "Temperature (°C)": 4.2, "Moisture (%)": 65, "Humidity (%)": 72, "Shelf Life (days)": 18},
        {"Chamber": "Chamber 2", "Temperature (°C)": 5.1, "Moisture (%)": 61, "Humidity (%)": 68, "Shelf Life (days)": 16},
        {"Chamber": "Chamber 3", "Temperature (°C)": 3.8, "Moisture (%)": 70, "Humidity (%)": 75, "Shelf Life (days)": 21},
        {"Chamber": "Chamber 4", "Temperature (°C)": 6.0, "Moisture (%)": 58, "Humidity (%)": 63, "Shelf Life (days)": 13},
        {"Chamber": "Chamber 5", "Temperature (°C)": 4.7, "Moisture (%)": 67, "Humidity (%)": 70, "Shelf Life (days)": 19},
    ]
    for chamber in base:
        chamber["Temperature (°C)"] = round(chamber["Temperature (°C)"] + random.uniform(-0.2, 0.2), 1)
        chamber["Moisture (%)"] = round(chamber["Moisture (%)"] + random.uniform(-1, 1), 1)
        chamber["Humidity (%)"] = round(chamber["Humidity (%)"] + random.uniform(-1, 1), 1)
    return base


def get_sensor_data(url):
    if not url.strip():
        raise ValueError("Sidebar mein sensor ka API URL daalein.")
    request = urllib.request.Request(url.strip(), headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=4) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload["chambers"] if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("API JSON mein chambers ki list nahi mili.")
    required = ["Chamber", "Temperature (°C)", "Moisture (%)", "Humidity (%)", "Shelf Life (days)"]
    for i, row in enumerate(rows, start=1):
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"Chamber {i} ke data mein fields missing hain: {', '.join(missing)}")
    return rows


@st.fragment(run_every=refresh_seconds)
def show_dashboard():
    rows = None
    feed_note = ""
    if mode == "Demo data":
        rows = get_demo_data()
        feed_note = "Demo feed chal raha hai; values har refresh par thodi badalti hain."
    else:
        try:
            rows = get_sensor_data(sensor_url)
            feed_note = "Wi-Fi sensor se data aa raha hai."
        except (ValueError, KeyError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            st.error(f"Sensor data nahi mil raha: {exc}")
            st.caption("Sensor aur computer same Wi-Fi par hone chahiye; URL aur JSON format bhi check karein.")

    raised = []
    if rows is not None:
        st.session_state["current_chambers"] = rows
        df = pd.DataFrame(rows)

        # A chamber that shows up in the live data always gets limits, so every connected chamber is checked.
        for row in rows:
            LIMITS.setdefault(row["Chamber"], dict(GENERIC_LIMITS))

        # Check every chamber against its limits (done in code), then raise or resolve alerts.
        statuses = {row["Chamber"]: check_chamber(row, LIMITS) for row in rows}
        st.session_state["chamber_status"] = statuses
        raised = update_alerts(statuses)

        # Temperature and humidity of every chamber, directly below the title.
        per_row = 5   # as many cards as there are chambers, five to a row
        for start in range(0, len(df), per_row):
            cards = st.columns(per_row)
            for card, (_, row) in zip(cards, df.iloc[start:start + per_row].iterrows()):
                with card:
                    st.subheader(str(row["Chamber"]))
                    status = statuses[row["Chamber"]][0]
                    if status == "ALERT":
                        st.error("🚨 ALERT")
                    elif status == "OK":
                        st.success("✅ OK")
                    else:
                        st.warning("No limits set")
                    st.caption(LIMITS.get(row["Chamber"], {}).get("produce", ""))
                    st.metric("Temperature", f'{num(row["Temperature (°C)"])} °C')
                    st.metric("Moisture", f'{num(row["Moisture (%)"])} %')
                    st.metric("Humidity", f'{num(row["Humidity (%)"])} %')
                    st.metric("Shelf life", f'{row["Shelf Life (days)"]} days')

        st.divider()
        in_alert = sum(1 for status, _ in statuses.values() if status == "ALERT")
        top_a, top_b, top_c = st.columns(3)
        top_a.metric("Chambers connected", len(rows))
        top_b.metric("Chambers in alert", in_alert)
        top_c.metric("Data source", "Demo data" if mode == "Demo data" else "Wi-Fi sensor")
        st.caption(feed_note)

        st.subheader("Sabhi chambers ki readings")
        df["Status"] = df["Chamber"].map(lambda chamber: statuses[chamber][0])
        st.dataframe(df, width="stretch", hide_index=True)
        left, right = st.columns(2)
        with left:
            st.subheader("Temperature comparison")
            st.bar_chart(df.set_index("Chamber")["Temperature (°C)"])
        with right:
            st.subheader("Humidity comparison")
            st.bar_chart(df.set_index("Chamber")["Humidity (%)"])

    st.divider()
    render_alerts_section()

    # Every new alert opens a popup that describes it.
    if raised:
        alert_popup({alert["Chamber"] for alert in raised})


# ---------------------------------------------------------------------------
# Page: title with the three-dot menu, live dashboard, staff log, and the chat in the left panel
# ---------------------------------------------------------------------------

title_col, menu_col = st.columns([12, 1])
title_col.title(PROJECT_NAME)
with menu_col:
    with st.container(key="menu_wrap"):
        with st.popover("⋮", help="Project details"):
            render_details()

show_dashboard()

st.divider()
st.header("📝 Staff action log")
st.caption("Staff inspect the room and record what they did. The kit monitors and reports; staff or the existing cold-room controls act.")
chamber_names = [c["Chamber"] for c in st.session_state.get("current_chambers", [])] or list(DEFAULT_LIMITS)
with st.form("action_form", clear_on_submit=True):
    action_chamber = st.selectbox("Chamber", chamber_names)
    staff_name = st.text_input("Staff name")
    action_note = st.text_area("What did you check or do?")
    saved = st.form_submit_button("Save action")
if saved:
    if not action_note.strip():
        st.warning("Please write what you checked or did.")
    else:
        st.session_state.setdefault("action_log", []).append({
            "Time": datetime.now().strftime(TIME_FORMAT),
            "Chamber": action_chamber,
            "Staff": staff_name.strip() or "Not given",
            "Action": action_note.strip(),
        })
        st.success("Action saved.")
action_log = st.session_state.get("action_log", [])
if action_log:
    action_df = pd.DataFrame(action_log[::-1])
    st.dataframe(action_df, width="stretch", hide_index=True)
    st.download_button("Download log (CSV)", action_df.to_csv(index=False).encode("utf-8"), "action_log.csv", "text/csv")
else:
    st.caption("No actions recorded yet.")

# Taapman AI chat: always available, at the bottom of the left panel.
with st.sidebar:
    render_chat_section()