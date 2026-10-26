import json
import random
from datetime import datetime
import urllib.error
import urllib.request

import pandas as pd
import streamlit as st

st.set_page_config(page_title="5 Chamber Dashboard", page_icon="🌡️", layout="wide")
st.title("🌡️ 5 Chamber Monitoring Dashboard")
st.caption("Temperature, moisture, humidity aur estimated shelf life")

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


# ---------------------------------------------------------------------------
# Taapman additions: chamber limits, OK/ALERT checks, alert history, staff log.
# The checks are done in plain Python code (never by an AI model), so a reading
# is always compared with the limits correctly.
# ---------------------------------------------------------------------------

# DEMO limits only. Real limits must be approved by the cold-store operator.
DEFAULT_LIMITS = {
    "Chamber 1": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 65.0, "h_max": 80.0},
    "Chamber 2": {"produce": "Demo produce", "t_min": 2.0, "t_max": 6.0, "h_min": 60.0, "h_max": 75.0},
    "Chamber 3": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 65.0, "h_max": 80.0},
    "Chamber 4": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.0, "h_min": 60.0, "h_max": 75.0},
    "Chamber 5": {"produce": "Demo produce", "t_min": 2.0, "t_max": 5.5, "h_min": 60.0, "h_max": 75.0},
}

LIMITS = {}
with st.sidebar.expander("Chamber limits (set by operator)"):
    st.caption("Demo values. Real limits must be approved by the cold-store operator.")
    for name, d in DEFAULT_LIMITS.items():
        st.markdown(f"**{name}**")
        produce = st.text_input("Produce label", d["produce"], key=f"{name}_produce")
        col_a, col_b = st.columns(2)
        t_min = col_a.number_input("Temp min (°C)", value=d["t_min"], step=0.5, key=f"{name}_tmin")
        t_max = col_b.number_input("Temp max (°C)", value=d["t_max"], step=0.5, key=f"{name}_tmax")
        h_min = col_a.number_input("Humidity min (%)", value=d["h_min"], step=1.0, key=f"{name}_hmin")
        h_max = col_b.number_input("Humidity max (%)", value=d["h_max"], step=1.0, key=f"{name}_hmax")
        LIMITS[name] = {"produce": produce, "t_min": t_min, "t_max": t_max, "h_min": h_min, "h_max": h_max}


def check_chamber(row, limits):
    """Compare one chamber's reading with its limits. Returns (status, list of problems)."""
    lim = limits.get(row["Chamber"])
    if lim is None:
        return "NO LIMITS", ["No approved limits are set for this chamber"]
    temp = float(row["Temperature (°C)"])
    hum = float(row["Humidity (%)"])
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


def update_alert_history(statuses):
    """Record a new line when a chamber goes from not-ALERT to ALERT."""
    previous = st.session_state.setdefault("last_status", {})
    history = st.session_state.setdefault("alert_history", [])
    for chamber, (status, problems) in statuses.items():
        if status == "ALERT" and previous.get(chamber) != "ALERT":
            history.append({
                "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Chamber": chamber,
                "Problem": "; ".join(problems),
            })
        previous[chamber] = status


        # ---------------------------------------------------------------------------
# Taapman AI chat. The model never reads numbers itself and never compares them:
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
    "You recommend only; staff or the existing cold-room controls change the settings. "
        "You cannot change any chamber setting, temperature, humidity, fan or refrigeration. If staff ask you to set or change anything, say that you cannot change chamber settings, that you only monitor and recommend, and that staff must adjust the controls themselves. Then give that chamber's current reading and one recommended check. "
    "Do not write poems, jokes or code. Stay on cold-storage topics."
)


def ask_taapman(user_text, model):
    """Send a question to Ollama, run the tools it asks for, and return (answer, tools used)."""
    import ollama

    convo = st.session_state.setdefault("taapman_convo", [{"role": "system", "content": TAAPMAN_SYSTEM}])
    convo.append({"role": "user", "content": user_text})
    used = []
    for _ in range(5):
        reply = ollama.chat(model=model, messages=convo, tools=list(TAAPMAN_TOOLS.values()))
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
    if mode == "Demo data":
        rows = get_demo_data()
        st.info("Demo feed chal raha hai; values har refresh par thodi badalti hain.")
    else:
        try:
            rows = get_sensor_data(sensor_url)
            st.success("Wi-Fi sensor se data aa raha hai.")
        except (ValueError, KeyError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            st.error(f"Sensor data nahi mil raha: {exc}")
            st.caption("Sensor aur computer same Wi-Fi par hone chahiye; URL aur JSON format bhi check karein.")
            return

    st.session_state["current_chambers"] = rows
    df = pd.DataFrame(rows)

    # Taapman: check every chamber against its limits (done in code).
    statuses = {row["Chamber"]: check_chamber(row, LIMITS) for row in rows}
    st.session_state["chamber_status"] = statuses
    update_alert_history(statuses)
    alerts = {chamber: problems for chamber, (status, problems) in statuses.items() if status == "ALERT"}
    if alerts:
        st.error(f"🚨 {len(alerts)} chamber(s) need attention. Staff should inspect and record their action below.")
        for chamber, problems in alerts.items():
            st.write(f"**{chamber}**: " + "; ".join(problems))
    else:
        st.success("✅ All chambers are within their limits.")

    cards = st.columns(len(df))
    for i, row in df.iterrows():
        with cards[i]:
            st.subheader(str(row["Chamber"]))
            status = statuses[row["Chamber"]][0]
            if status == "ALERT":
                st.error("🚨 ALERT")
            elif status == "OK":
                st.success("✅ OK")
            else:
                st.warning("No limits set")
            st.caption(LIMITS.get(row["Chamber"], {}).get("produce", ""))
            st.metric("Temperature", f'{row["Temperature (°C)"]} °C')
            st.metric("Moisture", f'{row["Moisture (%)"]} %')
            st.metric("Humidity", f'{row["Humidity (%)"]} %')
            st.metric("Shelf life", f'{row["Shelf Life (days)"]} days')

    st.divider()
    st.subheader("Sabhi chambers ki readings")
    df["Status"] = df["Chamber"].map(lambda chamber: statuses[chamber][0])
    st.dataframe(df, use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        st.subheader("Temperature comparison")
        st.bar_chart(df.set_index("Chamber")["Temperature (°C)"])
    with right:
        st.subheader("Humidity comparison")
        st.bar_chart(df.set_index("Chamber")["Humidity (%)"])

    st.subheader("Alert history")
    history = st.session_state.get("alert_history", [])
    if history:
        st.dataframe(pd.DataFrame(history[::-1]), use_container_width=True, hide_index=True)
    else:
        st.caption("No alerts so far in this session.")


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
            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Chamber": action_chamber,
            "Staff": staff_name.strip() or "Not given",
            "Action": action_note.strip(),
        })
        st.success("Action saved.")
action_log = st.session_state.get("action_log", [])
if action_log:
    action_df = pd.DataFrame(action_log[::-1])
    st.dataframe(action_df, use_container_width=True, hide_index=True)
    st.download_button("Download log (CSV)", action_df.to_csv(index=False).encode("utf-8"), "action_log.csv", "text/csv")
else:
    st.caption("No actions recorded yet.")

st.divider()
st.header("💬 Taapman AI")
st.caption("Ask about your chambers in plain words. Taapman AI answers from the live readings and the limits above, and only recommends: staff act.")

ollama_model = st.sidebar.text_input("Ollama model for Taapman AI", "llama3.2")
if st.sidebar.button("Clear Taapman chat"):
    st.session_state["taapman_chat"] = []
    st.session_state.pop("taapman_convo", None)

chat_history = st.session_state.setdefault("taapman_chat", [])
for message in chat_history:
    with st.chat_message(message["role"]):
        st.write(message["content"])
        if message.get("tools"):
            st.caption("Checked: " + ", ".join(message["tools"]))

question = st.chat_input("Ask Taapman AI, for example: Which chamber has a problem?")
if question:
    chat_history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Taapman AI is thinking..."):
            used = []
            try:
                answer, used = ask_taapman(question, ollama_model.strip() or "llama3.2")
            except ImportError:
                answer = "The ollama package is not installed. In the terminal run: python -m pip install ollama"
            except Exception as exc:
                answer = f"Taapman AI could not answer. Check that the Ollama app is running and the model is downloaded (ollama pull llama3.2). Details: {exc}"
        st.write(answer)
        if used:
            st.caption("Checked: " + ", ".join(used))
    chat_history.append({"role": "assistant", "content": answer, "tools": used})
    
st.divider()
st.header("🥦 Vegetable AI Detection")
st.write("Vegetable ki photo upload karo; AI naam, dikhne wali freshness aur storage guidance estimate karega.")
api_key = st.sidebar.text_input("Gemini API key (photo analysis ke liye)", type="password")
photo = st.file_uploader("Vegetable photo upload karo", type=["jpg", "jpeg", "png", "webp"])

if photo:
    st.image(photo, caption="Uploaded vegetable photo", width=360)
    if st.button("🔍 Photo analyze karo", type="primary"):
        if not api_key.strip():
            st.warning("Pehle sidebar mein Gemini API key daalo. API key Google AI Studio se milti hai.")
        else:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key.strip())
                chamber_data = st.session_state.get("current_chambers", [])
                chamber_context = [
                    {"Chamber": chamber["Chamber"], "Temperature (°C)": chamber["Temperature (°C)"]}
                    for chamber in chamber_data
                ]
                prompt = f"""Analyze the uploaded vegetable photo. Return ONLY valid JSON with these keys:
vegetable_name (string; identify the visible vegetable, or 'Unclear'),
freshness (one of 'Fresh-looking', 'Use soon', 'Spoiled-looking', 'Unclear'),
visual_observations (short string describing visible signs only),
recommended_temp_min_c (number; lower end of practical storage range in Celsius),
recommended_temp_max_c (number; upper end of practical storage range in Celsius),
estimated_shelf_life_days (integer or null; approximate shelf life at ideal storage temperature),
chamber_estimates (array with one item for EACH supplied chamber; each item has Chamber, estimated_shelf_life_days at that chamber's supplied current temperature, and short note),
storage_tip (short practical tip).
Current chamber readings from the sensors: {json.dumps(chamber_context)}
Use the provided chamber temperatures exactly; do not invent readings. Shelf-life values are rough estimates and may depend on variety, harvest date, handling, and storage conditions. If the image or readings do not support an estimate, use null. Do not claim certainty about internal quality or food safety from a photo."""
                image_part = types.Part.from_bytes(data=photo.getvalue(), mime_type=photo.type)
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=[image_part, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                result = json.loads(response.text)
                st.subheader(f"Pehchaan: {result.get('vegetable_name', 'Unclear')}")
                st.metric("Photo ke hisaab se freshness", result.get("freshness", "Unclear"))
                temp_min = result.get("recommended_temp_min_c")
                temp_max = result.get("recommended_temp_max_c")
                days = result.get("estimated_shelf_life_days")
                c1, c2 = st.columns(2)
                c1.metric("Ideal storage temperature", f"{temp_min} to {temp_max} °C" if temp_min is not None and temp_max is not None else "Unclear")
                c2.metric("Estimated shelf life at ideal temperature", f"{days} days" if days is not None else "Estimate unavailable")
                chamber_estimates = {
                    item.get("Chamber"): item
                    for item in result.get("chamber_estimates", [])
                    if isinstance(item, dict)
                }
                if chamber_data:
                    comparison = []
                    for chamber in chamber_data:
                        actual = float(chamber["Temperature (°C)"])
                        estimate = chamber_estimates.get(chamber["Chamber"], {})
                        if temp_min is not None and temp_max is not None and float(temp_min) <= actual <= float(temp_max):
                            status = "✅ Suitable"
                        elif temp_min is not None and actual < float(temp_min):
                            status = "❄️ Too cold"
                        elif temp_max is not None and actual > float(temp_max):
                            status = "🔥 Too warm"
                        else:
                            status = "Range unavailable"
                        comparison.append({
                            "Chamber": chamber["Chamber"],
                            "Current temperature (°C)": actual,
                            "Vegetable storage status": status,
                            "Estimated shelf life (days)": estimate.get("estimated_shelf_life_days"),
                            "AI note": estimate.get("note", ""),
                        })
                    st.subheader("Har chamber ke temperature par estimate")
                    st.dataframe(pd.DataFrame(comparison), use_container_width=True, hide_index=True)
                st.write("**Visible observations:**", result.get("visual_observations", "Not available"))
                st.write("**Storage tip:**", result.get("storage_tip", "Not available"))
                st.caption("AI ka freshness aur shelf-life result photo se kiya gaya approximate estimate hai; ye food-safety test nahi hai.")
            except ImportError:
                st.error("AI package install nahi hai. VS Code terminal mein `python -m pip install google-genai` chalao, phir app rerun karo.")
            except Exception as exc:
                st.error(f"Photo analyze nahi ho payi: {exc}")
else:
    st.info("Analysis shuru karne ke liye vegetable ki photo upload karo.")