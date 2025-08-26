import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import requests
_orig_Session = requests.Session
class UnsafeSession(_orig_Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verify = False
requests.Session = UnsafeSession

import streamlit as st
import openai
import random
import os
import time
import json
from collections import defaultdict
from supabase import create_client
from datetime import datetime
import pandas as pd

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False

st.set_page_config(page_title="🎭 Guess the Celebrity", layout="wide")

openai.api_key = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

@st.cache_resource
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

@st.cache_data(ttl=3)
def fetch_leaderboard():
    sb = get_supabase()
    data = sb.table("leaderboard").select("*").order("score", desc=True).limit(100).execute().data
    if not data:
        return pd.DataFrame(columns=["player","score","updated_at"])
    return pd.DataFrame(data)

def upsert_score(player, score):
    sb = get_supabase()
    sb.table("leaderboard").upsert({"player": player, "score": score, "updated_at": datetime.utcnow().isoformat()}).execute()

def select_difficulty_params(level):
    if level == "Easy":
        return {"obscurity_min": 0, "obscurity_max": 3, "hint_density": "high", "style_mystery": "low"}
    if level == "Medium":
        return {"obscurity_min": 3, "obscurity_max": 6, "hint_density": "medium", "style_mystery": "medium"}
    return {"obscurity_min": 6, "obscurity_max": 9, "hint_density": "low", "style_mystery": "medium"}

def llm_json(prompt):
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": "Reply only with valid JSON."},
                  {"role": "user", "content": prompt}],
        temperature=0.6
    )
    txt = r.choices[0].message.content.strip()
    try:
        return json.loads(txt)
    except Exception:
        s = txt.strip().strip("`").strip()
        s = s[s.find("[") : s.rfind("]")+1] if "[" in s and "]" in s else s
        return json.loads(s)

def generate_random_celebrities(selected_industries, difficulty):
    params = select_difficulty_params(difficulty)
    joined = ", ".join(selected_industries)
    prompt = (
        f'Return a JSON array of 24 unique celebrity names from only these film industries: {joined}. '
        f'Balance genders. Choose names with an obscurity score from {params["obscurity_min"]} to {params["obscurity_max"]} where 0 is most mainstream and 9 is very niche. '
        f'Output format: ["Name 1","Name 2",...]. No extra keys.'
    )
    try:
        arr = llm_json(prompt)
        pool = [n for n in arr if isinstance(n, str)]
        pool = list(dict.fromkeys(pool))
        random.shuffle(pool)
        return pool[:6] if len(pool) >= 6 else pool
    except Exception:
        fallbacks = ["Shah Rukh Khan", "Emma Watson", "Leonardo DiCaprio", "Fahadh Faasil", "Scarlett Johansson", "Mammootty"]
        return random.sample(fallbacks, 6)

def build_style_tag(celebrity, difficulty):
    params = select_difficulty_params(difficulty)
    return json.dumps({
        "celebrity": celebrity,
        "hint_density": params["hint_density"],
        "style_mystery": params["style_mystery"]
    })

def generate_intro(celebrity, difficulty):
    style = build_style_tag(celebrity, difficulty)
    sys = (
        f'You are {celebrity}. Do not state your name. Speak as you normally would. '
        f'Use real works, roles, co stars, awards, or signature traits as subtle hints. '
        f'Keep it 2 to 4 sentences. '
        f'Adjust hint density and mystery according to this JSON: {style}.'
    )
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": "Introduce yourself to a fan who is trying to guess you."}],
        temperature=0.8
    )
    return r.choices[0].message.content.strip()

def generate_response(celebrity, user_prompt, difficulty):
    style = build_style_tag(celebrity, difficulty)
    sys = (
        f'You are {celebrity}. Stay fully in character. '
        f'Be clear, friendly, and natural. Reference true works and collaborators. '
        f'Never reveal or spell your name or initials. '
        f'Answer the user prompt and optionally offer a subtle hint. '
        f'Adjust hint density and mystery according to: {style}.'
    )
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": user_prompt}],
        temperature=0.9
    )
    return r.choices[0].message.content.strip()

def check_guess_llm(user_input, actual_name):
    p = f"User guess: '{user_input}'. Correct name: '{actual_name}'. Reply exactly yes or no if the guess refers to the correct celebrity, allowing partials and misspellings."
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": "Reply only yes or no."},
                  {"role": "user", "content": p}],
        temperature=0
    )
    return "yes" in r.choices[0].message.content.strip().lower()

if "player_name" not in st.session_state:
    st.session_state.player_name = None
if "selected_industries" not in st.session_state:
    st.session_state.selected_industries = []
if "celebrity_rounds" not in st.session_state:
    st.session_state.celebrity_rounds = []
if "guessed" not in st.session_state:
    st.session_state.guessed = [False]*6
if "all_scores" not in st.session_state:
    st.session_state.all_scores = {}
if "difficulty" not in st.session_state:
    st.session_state.difficulty = "Medium"
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = True

st.title("🎭 Guess the Celebrity")

place = st.radio("Leaderboard position", ["Sidebar","Left column"], horizontal=True, index=0)
st.session_state.auto_refresh = st.toggle("Auto refresh leaderboard", value=True)
if st.session_state.auto_refresh and HAS_AUTOREFRESH:
    st_autorefresh(interval=5000, key="lb_tick")

if st.session_state.player_name is None:
    name = st.text_input("Enter your name")
    industries = st.multiselect("Choose your industries", ["Hollywood", "Bollywood", "Mollywood"])
    difficulty = st.select_slider("Difficulty", options=["Easy","Medium","Hard"], value="Medium")
    start = st.button("Start Game")
    if start and name and industries:
        st.session_state.player_name = name.strip()
        st.session_state.selected_industries = industries
        st.session_state.difficulty = difficulty
        with st.spinner("Picking celebrities"):
            st.session_state.celebrity_rounds = generate_random_celebrities(industries, difficulty)
        st.session_state.all_scores[name] = 0
        upsert_score(name, 0)
        st.toast("Profile created", icon="✅")
        st.rerun()
else:
    if place == "Sidebar":
        df = fetch_leaderboard()
        df_show = df.copy()
        if not df_show.empty:
            df_show["updated_at"] = pd.to_datetime(df_show["updated_at"]).dt.tz_convert(None)
            st.sidebar.subheader("🏆 Leaderboard")
            st.sidebar.dataframe(df_show.rename(columns={"player":"Player","score":"Score","updated_at":"Updated"}), use_container_width=True, height=350)
        else:
            st.sidebar.info("No scores yet")
        st.sidebar.markdown(f'**Player:** {st.session_state.player_name}')
        st.sidebar.markdown(f'**Score:** {st.session_state.all_scores.get(st.session_state.player_name,0)}')
        container = st.container()
        with container:
            tabs = st.tabs([f"Round {i+1}" for i in range(6)])
    else:
        left, right = st.columns([1,3])
        with left:
            df = fetch_leaderboard()
            df_show = df.copy()
            if not df_show.empty:
                df_show["updated_at"] = pd.to_datetime(df_show["updated_at"]).dt.tz_convert(None)
                st.subheader("🏆 Leaderboard")
                st.dataframe(df_show.rename(columns={"player":"Player","score":"Score","updated_at":"Updated"}), use_container_width=True, height=600)
            else:
                st.info("No scores yet")
            st.markdown(f'**Player:** {st.session_state.player_name}')
            st.markdown(f'**Score:** {st.session_state.all_scores.get(st.session_state.player_name,0)}')
        with right:
            tabs = st.tabs([f"Round {i+1}" for i in range(6)])

    for i in range(6):
        with tabs[i]:
            celeb = st.session_state.celebrity_rounds[i]
            if not st.session_state.guessed[i]:
                key_intro = f"intro_{i}"
                if key_intro not in st.session_state:
                    with st.spinner("A mysterious figure steps into the spotlight"):
                        try:
                            intro = generate_intro(celeb, st.session_state.difficulty)
                            st.session_state[key_intro] = intro
                        except Exception:
                            st.session_state[key_intro] = "(Intro unavailable)"
                    time.sleep(0.3)
                st.info(st.session_state[key_intro])

                st.write("Ask questions to guess who this celebrity is")
                user_prompt = st.text_area("Your message", key=f"prompt_{i}")
                ask_clicked = st.button("Ask", key=f"ask_{i}")
                if ask_clicked and user_prompt:
                    with st.spinner("Reply incoming"):
                        try:
                            reply = generate_response(celeb, user_prompt, st.session_state.difficulty)
                            st.success("Celebrity says")
                            st.markdown(reply)
                            st.toast("New message", icon="💬")
                        except Exception as e:
                            st.error(f"Error: {e}")

                guess = st.text_input("Your guess", key=f"guess_{i}")
                if st.button("Submit Guess", key=f"guess_btn_{i}") and guess:
                    with st.spinner("Checking"):
                        if check_guess_llm(guess, celeb):
                            st.success(f"Correct. It was {celeb}")
                            st.session_state.guessed[i] = True
                            st.session_state.all_scores[st.session_state.player_name] = st.session_state.all_scores.get(st.session_state.player_name,0) + 1
                            upsert_score(st.session_state.player_name, st.session_state.all_scores[st.session_state.player_name])
                            fetch_leaderboard.clear()
                            st.toast("+1 point", icon="✨")
                        else:
                            st.error("Not quite. Try again")
            else:
                st.success(f"Already guessed correctly: {celeb}")

    if all(st.session_state.guessed):
        st.balloons()
        st.subheader("You have guessed all celebrities")
        st.write(f'Final Score: {st.session_state.all_scores[st.session_state.player_name]} / 6')
        if st.button("Play Again"):
            for key in ['player_name','selected_industries','celebrity_rounds','guessed','difficulty']:
                st.session_state.pop(key, None)
            st.rerun()
