import json
import random
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
    cards = st.columns(len(df))
    for i, row in df.iterrows():
        with cards[i]:
            st.subheader(str(row["Chamber"]))
            st.metric("Temperature", f'{row["Temperature (°C)"]} °C')
            st.metric("Moisture", f'{row["Moisture (%)"]} %')
            st.metric("Humidity", f'{row["Humidity (%)"]} %')
            st.metric("Shelf life", f'{row["Shelf Life (days)"]} days')

    st.divider()
    st.subheader("Sabhi chambers ki readings")
    st.dataframe(df, use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        st.subheader("Temperature comparison")
        st.bar_chart(df.set_index("Chamber")["Temperature (°C)"])
    with right:
        st.subheader("Humidity comparison")
        st.bar_chart(df.set_index("Chamber")["Humidity (%)"])


show_dashboard()

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



