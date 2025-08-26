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
from streamlit_autorefresh import st_autorefresh
import openai
import random
import os
import time
import json
from collections import defaultdict
from supabase import create_client
from datetime import datetime
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="🎭 Guess the Celebrity", layout="wide")

openai.api_key = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

@st.cache_resource
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

@st.cache_data(ttl=2)
def fetch_leaderboard():
    sb = get_supabase()
    data = sb.table("leaderboard").select("player,score,updated_at").order("score", desc=True).limit(100).execute().data
    if not data:
        return pd.DataFrame(columns=["player","score","updated_at"])
    return pd.DataFrame(data)

def upsert_score(player, score):
    sb = get_supabase()
    sb.table("leaderboard").upsert({"player": player, "score": score, "updated_at": datetime.utcnow().isoformat()}).execute()

def get_player_score_from_db(player):
    sb = get_supabase()
    data = sb.table("leaderboard").select("score").eq("player", player).limit(1).execute().data
    if data:
        return int(data[0]["score"])
    return 0

def select_difficulty_params(level):
    if level == "Easy":
        return {"obscurity_min": 0, "obscurity_max": 3, "hint_density": "high", "style_mystery": "low", "points": 1}
    if level == "Medium":
        return {"obscurity_min": 3, "obscurity_max": 6, "hint_density": "medium", "style_mystery": "medium", "points": 3}
    return {"obscurity_min": 6, "obscurity_max": 9, "hint_density": "low", "style_mystery": "medium", "points": 5}

def llm_json(prompt, temperature=0.6):
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": "Reply only with valid JSON."},
                  {"role": "user", "content": prompt}],
        temperature=temperature
    )
    txt = r.choices[0].message.content.strip()
    try:
        return json.loads(txt)
    except Exception:
        s = txt.strip().strip("`").strip()
        if "[" in s and "]" in s:
            s = s[s.find("["): s.rfind("]")+1]
        return json.loads(s)

def generate_random_celebrities(selected_industries, difficulty):
    params = select_difficulty_params(difficulty)
    joined = ", ".join(selected_industries)
    prompt = (
        f'Return a JSON array of 24 unique celebrity names from only these film industries: {joined}. '
        f'Balance genders. Choose names with an obscurity score from {params["obscurity_min"]} to {params["obscurity_max"]} where 0 is most mainstream and 9 is very niche. '
        f'Output format: ["Name 1","Name 2",...].'
    )
    try:
        arr = llm_json(prompt, temperature=0.5)
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

def generate_sample_questions_llm(celebrity, difficulty):
    params = select_difficulty_params(difficulty)
    prompt = (
        f'Give a JSON array of exactly 3 short, helpful questions a fan might ask to identify the celebrity without revealing the name. '
        f'Celebrity is "{celebrity}". Hint density should be {params["hint_density"]}. Style mystery is {params["style_mystery"]}. '
        f'Output format: ["Q1","Q2","Q3"].'
    )
    try:
        qs = llm_json(prompt, temperature=0.8)
        out = [q for q in qs if isinstance(q, str)]
        return out[:3] if len(out) >= 3 else out
    except Exception:
        bank = [
            "Which role made you a household name",
            "Have you worked with any Oscar winners",
            "Are you known for action or drama"
        ]
        return random.sample(bank, 3)

def generate_congrats_line(celebrity, difficulty):
    sys = f'You are {celebrity}. The fan guessed correctly. Say a one line congrats in your voice. Do not reveal your name.'
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": "Congratulate them in one short line."}],
        temperature=0.8
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

def fetch_wikipedia_thumb(name):
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{name.replace(' ', '%20')}"
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            if "thumbnail" in data and "source" in data["thumbnail"]:
                return data["thumbnail"]["source"]
    except Exception:
        pass
    return None

def style_leaderboard_df(df):
    base = df[["player","score"]].rename(columns={"player":"Player","score":"Score"}).copy()
    base = base.sort_values("Score", ascending=False, kind="mergesort").reset_index(drop=True)
    def color_rows(row):
        i = row.name
        if i == 0:
            return ["background-color: #FFD700"]*len(row)
        if i == 1:
            return ["background-color: #C0C0C0"]*len(row)
        if i == 2:
            return ["background-color: #CD7F32"]*len(row)
        return [""]*len(row)
    return base.style.apply(color_rows, axis=1).hide(axis="index")

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
if "guess_counts" not in st.session_state:
    st.session_state.guess_counts = [0]*6
if "sample_q" not in st.session_state:
    st.session_state.sample_q = {}
if "player_photo" not in st.session_state:
    st.session_state.player_photo = None

st_autorefresh(interval=4000, key="lb_tick")

st.title("🎭 Guess the Celebrity")

if st.session_state.player_name is None:
    name = st.text_input("Enter your name")
    industries = st.multiselect("Choose your industries", ["Hollywood", "Bollywood", "Mollywood"])
    difficulty = st.select_slider("Difficulty", options=["Easy","Medium","Hard"], value="Medium")
    photo = st.file_uploader("Optional: upload your photo for the celebration image", type=["png","jpg","jpeg"])
    start = st.button("Start Game")
    if start and name and industries:
        st.session_state.player_name = name.strip()
        st.session_state.selected_industries = industries
        st.session_state.difficulty = difficulty
        if photo is not None:
            st.session_state.player_photo = photo.read()
        with st.spinner("Picking celebrities"):
            st.session_state.celebrity_rounds = generate_random_celebrities(industries, difficulty)
        st.session_state.all_scores[name] = 0
        upsert_score(name, 0)
        st.rerun()
else:
    left, right = st.columns([1.15, 3.0])
    with left:
        df_lb = fetch_leaderboard()
        if not df_lb.empty:
            styled = style_leaderboard_df(df_lb)
            st.subheader("🏆 Leaderboard")
            st.table(styled)
        else:
            st.info("No scores yet")
    with right:
        topc1, topc2 = st.columns([2,1])
        with topc1:
            st.markdown(f"**Player:** {st.session_state.player_name}")
        with topc2:
            live_score = get_player_score_from_db(st.session_state.player_name)
            st.session_state.all_scores[st.session_state.player_name] = live_score
            st.markdown(f"**Score:** {live_score}")
        tabs = st.tabs([f"Round {i+1}" for i in range(6)])
        for i in range(6):
            with tabs[i]:
                if i >= len(st.session_state.celebrity_rounds):
                    st.warning("Round not available")
                    continue
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
                        time.sleep(0.2)
                    st.info(st.session_state[key_intro])
                    if f"qset_{i}" not in st.session_state.sample_q:
                        st.session_state.sample_q[f"qset_{i}"] = generate_sample_questions_llm(celeb, st.session_state.difficulty)
                    qs = st.session_state.sample_q[f"qset_{i}"]
                    qc1, qc2, qc3 = st.columns(3)
                    if qs:
                        if qc1.button(qs[0], key=f"qbtn1_{i}"):
                            st.session_state[f"prompt_{i}"] = qs[0]
                            st.session_state.sample_q[f"qset_{i}"] = generate_sample_questions_llm(celeb, st.session_state.difficulty)
                        if qc2.button(qs[1], key=f"qbtn2_{i}"):
                            st.session_state[f"prompt_{i}"] = qs[1]
                            st.session_state.sample_q[f"qset_{i}"] = generate_sample_questions_llm(celeb, st.session_state.difficulty)
                        if qc3.button(qs[2], key=f"qbtn3_{i}"):
                            st.session_state[f"prompt_{i}"] = qs[2]
                            st.session_state.sample_q[f"qset_{i}"] = generate_sample_questions_llm(celeb, st.session_state.difficulty)
                    user_prompt = st.text_area("Your message", key=f"prompt_{i}")
                    ask_clicked = st.button("Ask", key=f"ask_{i}")
                    if ask_clicked and user_prompt:
                        with st.spinner("Reply incoming"):
                            try:
                                reply = generate_response(celeb, user_prompt, st.session_state.difficulty)
                                st.success("Celebrity says")
                                st.markdown(reply)
                                st.toast("New message", icon="💬")
                                st.session_state.sample_q[f"qset_{i}"] = generate_sample_questions_llm(celeb, st.session_state.difficulty)
                            except Exception as e:
                                st.error(f"Error: {e}")
                    st.markdown(f"Guesses used: {st.session_state.guess_counts[i]} of 3")
                    if st.session_state.guess_counts[i] >= 3:
                        st.error("No more guesses for this round")
                    else:
                        guess = st.text_input("Your guess", key=f"guess_{i}")
                        if st.button("Submit Guess", key=f"guess_btn_{i}") and guess:
                            st.session_state.guess_counts[i] += 1
                            with st.spinner("Checking"):
                                if check_guess_llm(guess, celeb):
                                    st.success(f"Correct. It was {celeb}")
                                    st.session_state.guessed[i] = True
                                    pts = select_difficulty_params(st.session_state.difficulty)["points"]
                                    new_score = st.session_state.all_scores.get(st.session_state.player_name, 0) + pts
                                    st.session_state.all_scores[st.session_state.player_name] = new_score
                                    upsert_score(st.session_state.player_name, new_score)
                                    fetch_leaderboard.clear()
                                    st.toast(f"+{pts} points", icon="✨")
                                else:
                                    if st.session_state.guess_counts[i] >= 3:
                                        st.error("No more guesses for this round")
                                    else:
                                        st.error("Not quite. Try again")
                else:
                    st.success(f"Already guessed correctly: {celeb}")
                    celeb_img_url = fetch_wikipedia_thumb(celeb)
                    if st.session_state.player_photo:
                        user_img_bytes = st.session_state.player_photo
                    else:
                        ph = requests.get("https://upload.wikimedia.org/wikipedia/commons/9/99/Sample_User_Icon.png", timeout=6)
                        user_img_bytes = ph.content if ph.status_code == 200 else None
                    c1, c2 = st.columns(2)
                    if celeb_img_url:
                        c1.image(celeb_img_url, caption=celeb, use_column_width=True)
                    if user_img_bytes:
                        c2.image(BytesIO(user_img_bytes), caption=st.session_state.player_name, use_column_width=True)
                    try:
                        line = generate_congrats_line(celeb, st.session_state.difficulty)
                        st.markdown(f"**{line}**")
                    except Exception:
                        st.markdown("**Well done**")
        if all(st.session_state.guessed):
            st.balloons()
            st.subheader("You have guessed all celebrities")
            live_score = get_player_score_from_db(st.session_state.player_name)
            st.write(f'Final Score: {live_score} of 6 rounds')
            if st.button("Play Again"):
                for key in ['player_name','selected_industries','celebrity_rounds','guessed','difficulty','guess_counts','sample_q','player_photo']:
                    st.session_state.pop(key, None)
                st.rerun()
