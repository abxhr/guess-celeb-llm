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
import streamlit.components.v1 as components
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
        f'Never reveal or spell your name or initials unless asked to reveal after guesses are over. '
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

def generate_generic_questions(prev_used=set()):
    prompt = (
        "Return a JSON array of exactly 3 short generic questions a fan could ask any film celebrity to identify them without revealing the name. "
        "Do not reference specific people, works, dates, places, or awards by name. "
        "Questions must be distinct from each other and phrased simply. "
        "Output format: [\"Q1\",\"Q2\",\"Q3\"]."
    )
    try:
        qs = llm_json(prompt, temperature=0.9)
        out = [q for q in qs if isinstance(q, str) and q.strip()]
        out = [q for q in out if q not in prev_used]
        if len(out) < 3:
            bank = [
                "Are you known more for serious roles or lighter roles",
                "Have you worked across both television and films",
                "Have you performed in more than one language",
                "Do you often collaborate with the same directors",
                "Have you done voice acting for animated projects",
                "Do you take on physically demanding roles",
                "Are you known for a signature style on screen",
                "Have you tried directing or producing as well",
                "Do you prefer ensemble casts or solo lead roles"
            ]
            random.shuffle(bank)
            for q in bank:
                if len(out) >= 3:
                    break
                if q not in prev_used and q not in out:
                    out.append(q)
        return out[:3]
    except Exception:
        bank = [
            "Are you known more for serious roles or lighter roles",
            "Have you worked across both television and films",
            "Have you performed in more than one language",
            "Do you often collaborate with the same directors",
            "Have you done voice acting for animated projects",
            "Do you take on physically demanding roles"
        ]
        random.shuffle(bank)
        return bank[:3]

def generate_congrats_line(celebrity, difficulty):
    sys = f'You are {celebrity}. The fan guessed correctly. Say a one line congrats in your voice and you may confirm your name.'
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": "Congratulate them in one short line."}],
        temperature=0.8
    )
    return r.choices[0].message.content.strip()

def generate_reveal_line(celebrity):
    sys = f'You are {celebrity}. The fan is out of guesses. Reveal your name in a natural short line in your voice.'
    r = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": sys}],
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
            if "thumbnail" in data and data["thumbnail"].get("source"):
                return data["thumbnail"]["source"]
            if "originalimage" in data and data["originalimage"].get("source"):
                return data["originalimage"]["source"]
    except Exception:
        pass
    return "https://upload.wikimedia.org/wikipedia/commons/6/65/No-Image-Placeholder.svg"

def supabase_realtime_leaderboard_widget():
    url = st.secrets["SUPABASE_URL"]
    anon = st.secrets["SUPABASE_ANON"]
    html = f"""
    <html>
    <head>
    <script src="https://unpkg.com/@supabase/supabase-js@2"></script>
    <style>
      body {{ margin:0; font-family:system-ui, Arial; }}
      .wrap {{ padding:8px }}
      table {{ width:100%; border-collapse:collapse; font-size:14px }}
      th, td {{ text-align:left; padding:8px }}
      th {{ border-bottom:1px solid #e5e7eb }}
      tr:nth-child(even) {{ background:#fafafa }}
      tr.gold td {{ background:#FFD700 !important }}
      tr.silver td {{ background:#C0C0C0 !important }}
      tr.bronze td {{ background:#CD7F32 !important }}
      .title {{ font-weight:600; margin-bottom:6px }}
    </style>
    </head>
    <body>
      <div class="wrap">
        <div class="title">🏆 Leaderboard</div>
        <table id="lb">
          <thead><tr><th>Player</th><th>Score</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <script>
        const client = supabase.createClient("{url}", "{anon}");
        async function load() {{
          const {{ data, error }} = await client.from('leaderboard').select('player,score').order('score', {{ ascending: false }}).limit(100);
          if (error) return;
          render(data || []);
        }}
        function render(rows) {{
          const tbody = document.querySelector('#lb tbody');
          tbody.innerHTML = '';
          rows.sort((a,b) => b.score - a.score);
          rows.forEach((r,i) => {{
            const tr = document.createElement('tr');
            if (i===0) tr.className = 'gold';
            else if (i===1) tr.className = 'silver';
            else if (i===2) tr.className = 'bronze';
            tr.innerHTML = `<td>${{r.player}}</td><td>${{r.score}}</td>`;
            tbody.appendChild(tr);
          }});
        }}
        load();
        client.channel('lb-changes').on('postgres_changes', {{ event: '*', schema: 'public', table: 'leaderboard' }}, payload => load()).subscribe();
      </script>
    </body>
    </html>
    """
    components.html(html, height=420, scrolling=False)

def supabase_realtime_player_score_widget(player_name):
    url = st.secrets["SUPABASE_URL"]
    anon = st.secrets["SUPABASE_ANON"]
    html = f"""
    <html>
    <head>
    <script src="https://unpkg.com/@supabase/supabase-js@2"></script>
    <style>
      body {{ margin:0; font-family:system-ui, Arial; }}
      .wrap {{ text-align:right; padding:6px 0 }}
      .label {{ color:#6b7280; margin-right:10px }}
      .val {{ font-weight:700 }}
    </style>
    </head>
    <body>
      <div class="wrap"><span class="label">Score:</span><span id="score" class="val">0</span></div>
      <script>
        const client = supabase.createClient("{url}", "{anon}");
        const player = {json.dumps(player_name)};
        async function load() {{
          const {{ data, error }} = await client.from('leaderboard').select('score').eq('player', player).limit(1).maybeSingle();
          if (error) return;
          document.querySelector('#score').textContent = data && data.score != null ? data.score : 0;
        }}
        load();
        client.channel('me-changes')
          .on('postgres_changes', {{ event: '*', schema: 'public', table: 'leaderboard', filter: `player=eq.${{player}}` }}, payload => load())
          .subscribe();
      </script>
    </body>
    </html>
    """
    components.html(html, height=32, scrolling=False)

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
if "player_photo" not in st.session_state:
    st.session_state.player_photo = None
if "used_generic_qs" not in st.session_state:
    st.session_state.used_generic_qs = set()

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
        supabase_realtime_leaderboard_widget()
    with right:
        topc1, topc2 = st.columns([2,1])
        with topc1:
            st.markdown(f"**Player:** {st.session_state.player_name}")
        with topc2:
            supabase_realtime_player_score_widget(st.session_state.player_name)
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

                    if f"qset_{i}" not in st.session_state:
                        st.session_state[f"qset_{i}"] = generate_generic_questions(st.session_state.used_generic_qs)
                    qs = st.session_state[f"qset_{i}"]
                    qc1, qc2, qc3 = st.columns(3)
                    if qs:
                        if qc1.button(qs[0], key=f"qbtn1_{i}"):
                            st.session_state[f"prompt_{i}"] = qs[0]
                            st.session_state.used_generic_qs.update(qs)
                            st.session_state[f"qset_{i}"] = generate_generic_questions(st.session_state.used_generic_qs)
                        if qc2.button(qs[1], key=f"qbtn2_{i}"):
                            st.session_state[f"prompt_{i}"] = qs[1]
                            st.session_state.used_generic_qs.update(qs)
                            st.session_state[f"qset_{i}"] = generate_generic_questions(st.session_state.used_generic_qs)
                        if qc3.button(qs[2], key=f"qbtn3_{i}"):
                            st.session_state[f"prompt_{i}"] = qs[2]
                            st.session_state.used_generic_qs.update(qs)
                            st.session_state[f"qset_{i}"] = generate_generic_questions(st.session_state.used_generic_qs)

                    user_prompt = st.text_area("Your message", key=f"prompt_{i}")
                    ask_clicked = st.button("Ask", key=f"ask_{i}")
                    if ask_clicked and user_prompt:
                        with st.spinner("Reply incoming"):
                            try:
                                reply = generate_response(celeb, user_prompt, st.session_state.difficulty)
                                st.success("Celebrity says")
                                st.markdown(reply)
                                st.toast("New message", icon="💬")
                                st.session_state.used_generic_qs.update(qs)
                                st.session_state[f"qset_{i}"] = generate_generic_questions(st.session_state.used_generic_qs)
                            except Exception as e:
                                st.error(f"Error: {e}")

                    st.markdown(f"Guesses used: {st.session_state.guess_counts[i]} of 3")
                    if st.session_state.guess_counts[i] >= 3:
                        line = generate_reveal_line(celeb)
                        st.error(line)
                    else:
                        guess = st.text_input("Your guess", key=f"guess_{i}")
                        if st.button("Submit Guess", key=f"guess_btn_{i}") and guess:
                            st.session_state.guess_counts[i] += 1
                            with st.spinner("Checking"):
                                if check_guess_llm(guess, celeb):
                                    st.success(f"Correct. It was {celeb}")
                                    st.session_state.guessed[i] = True
                                    pts = select_difficulty_params(st.session_state.difficulty)["points"]
                                    new_score = get_player_score_from_db(st.session_state.player_name) + pts
                                    upsert_score(st.session_state.player_name, new_score)
                                    st.toast(f"+{pts} points", icon="✨")
                                else:
                                    if st.session_state.guess_counts[i] >= 3:
                                        line = generate_reveal_line(celeb)
                                        st.error(line)
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
                        st.markdown(f"**Well done. I am {celeb}.**")

        if all(st.session_state.guessed):
            st.balloons()
            live_score = get_player_score_from_db(st.session_state.player_name)
            st.subheader("You have guessed all celebrities")
            st.write(f'Final Score: {live_score} of 6 rounds')
            if st.button("Play Again"):
                for key in ['player_name','selected_industries','celebrity_rounds','guessed','difficulty','guess_counts','player_photo','used_generic_qs']:
                    st.session_state.pop(key, None)
                st.rerun()
