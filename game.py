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
from supabase import create_client
from datetime import datetime
import pandas as pd

st.set_page_config(page_title="🎭 Guess the Celebrity", layout="wide")
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] { background: #ffffff !important; color: #111827 !important; }
[data-testid="stHeader"] { background: #ffffff !important; }
[data-testid="stSidebar"] { background: #ffffff !important; }
div.stMarkdown, .stText, .stButton, .stDataFrame, .stTable, .stSelectbox, .stMultiSelect, .stRadio, .stTextInput, .stFileUploader, .stSlider { color: #111827 !important; }
</style>
""", unsafe_allow_html=True)

openai.api_key = os.environ.get("OPENAI_API_KEY","")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL","gpt-4o")

def safe_create_client():
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None

@st.cache_resource
def get_supabase():
    return safe_create_client()

def upsert_score(player, points_to_add):
    try:
        sb = get_supabase()
        if not sb:
            return

        # fetch current score case-insensitive
        data = sb.table("leaderboard").select("score,player")\
                   .ilike("player", player).limit(1).execute().data

        if data:
            current_score = int(data[0].get("score", 0))
            db_name = data[0].get("player")  # keep the stored name spelling
            new_score = current_score + int(points_to_add)
            sb.table("leaderboard").update(
                {"score": new_score, "updated_at": datetime.utcnow().isoformat()}
            ).ilike("player", player).execute()
        else:
            # new player, insert fresh
            sb.table("leaderboard").insert({
                "player": player,
                "score": int(points_to_add),
                "updated_at": datetime.utcnow().isoformat()
            }).execute()
    except Exception:
        pass


def get_player_score_from_db(player):
    try:
        sb = get_supabase()
        if not sb:
            return 0
        data = sb.table("leaderboard").select("score").eq("player", player).limit(1).execute().data
        if data:
            return int(data[0].get("score", 0))
    except Exception:
        pass
    return 0

def fetch_leaderboard_df():
    try:
        sb = get_supabase()
        if not sb:
            return pd.DataFrame(columns=["player","score"])
        data = sb.table("leaderboard").select("player,score").order("score", desc=True).limit(100).execute().data
        if not data:
            return pd.DataFrame(columns=["player","score"])
        df = pd.DataFrame(data)
        df = df[["player","score"]].copy()
        df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0).astype(int)
        df = df.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame(columns=["player","score"])

def supabase_realtime_leaderboard_widget():
    try:
        url = st.secrets.get("SUPABASE_URL","")
        anon = st.secrets.get("SUPABASE_ANON","")
        if not url or not anon:
            return False
        html = f"""
        <html>
        <head>
        <script src="https://unpkg.com/@supabase/supabase-js@2"></script>
        <style>
          body {{ margin:0; font-family:system-ui, Arial; background:#ffffff; color:#111827; }}
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
              rows.sort((a,b) => (b.score||0) - (a.score||0));
              rows.forEach((r,i) => {{
                const tr = document.createElement('tr');
                if (i===0) tr.className = 'gold';
                else if (i===1) tr.className = 'silver';
                else if (i===2) tr.className = 'bronze';
                tr.innerHTML = `<td>${{r.player||''}}</td><td>${{r.score||0}}</td>`;
                tbody.appendChild(tr);
              }});
            }}
            load();
            client.channel('lb_changes').on('postgres_changes', {{ event: '*', schema: 'public', table: 'leaderboard' }}, payload => load()).subscribe();
          </script>
        </body>
        </html>
        """
        components.html(html, height=420, scrolling=False)
        return True
    except Exception:
        return False

def supabase_realtime_player_score_widget(player_name):
    try:
        url = st.secrets.get("SUPABASE_URL","")
        anon = st.secrets.get("SUPABASE_ANON","")
        if not url or not anon or not player_name:
            return False
        html = f"""
        <html>
        <head>
        <script src="https://unpkg.com/@supabase/supabase-js@2"></script>
        <style>
          body {{ margin:0; font-family:system-ui, Arial; background:#ffffff; color:#111827; }}
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
            client.channel('me_changes').on('postgres_changes', {{ event: '*', schema: 'public', table: 'leaderboard', filter: `player=eq.${{player}}` }}, payload => load()).subscribe();
          </script>
        </body>
        </html>
        """
        components.html(html, height=32, scrolling=False)
        return True
    except Exception:
        return False

def llm_json(prompt, temperature=0.6):
    try:
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
    except Exception:
        return []

def select_difficulty_params(level):
    if level == "Easy":
        return {"obscurity_min": 0, "obscurity_max": 3, "points": 1}
    if level == "Medium":
        return {"obscurity_min": 3, "obscurity_max": 6, "points": 3}
    return {"obscurity_min": 6, "obscurity_max": 9, "points": 5}

def generate_random_celebrities(selected_industries, difficulty):
    try:
        params = select_difficulty_params(difficulty)
        joined = ", ".join(selected_industries)
        prompt = (
            f'Return a JSON array of 24 unique celebrity names from only these film industries: {joined}. '
            f'Balance genders. Choose names with an obscurity score from {params["obscurity_min"]} to {params["obscurity_max"]}. '
            f'Output format: ["Name 1","Name 2", "..."].'
        )
        arr = llm_json(prompt, temperature=0.5)
        pool = [n for n in arr if isinstance(n, str) and n.strip()]
        pool = list(dict.fromkeys(pool))
        random.shuffle(pool)
        if len(pool) >= 6:
            return pool[:6]
    except Exception:
        pass
    fallbacks = ["Shah Rukh Khan", "Emma Watson", "Leonardo DiCaprio", "Fahadh Faasil", "Scarlett Johansson", "Mammootty"]
    random.shuffle(fallbacks)
    return fallbacks[:6]

def generate_intro(celebrity):
    try:
        sys = f'You are {celebrity}. Do not state your name. Speak naturally for 2 to 4 sentences and give subtle hints without names.'
        r = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": "Introduce yourself to a fan who is trying to guess you."}],
            temperature=0.8
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return "(Intro unavailable)"

def generate_response(celebrity, user_prompt):
    try:
        sys = f'You are {celebrity}. Stay in character. Be clear and friendly. Do not reveal your name.'
        r = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": user_prompt}],
            temperature=0.9
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return "Unable to answer now"

def generate_generic_questions(prev_used):
    prompt = (
        "Return a JSON array of exactly 5 short generic questions a fan could ask any film celebrity to identify them without revealing the name. "
        "Do not reference specific people or works by name. Output format: [\"Q1\",\"Q2\",\"Q3\",\"Q4\",\"Q5\"]."
    )
    qs = llm_json(prompt, temperature=0.9)
    qs = [q for q in qs if isinstance(q, str) and q.strip()]
    fallback = [
        "Are you known more for serious roles or lighter roles",
        "Have you worked in both television and films",
        "Have you performed in more than one language",
        "Do you often collaborate with the same directors",
        "Have you done voice acting for animation",
        "Do you take on physically demanding roles",
        "Are you known for a signature on screen style",
        "Have you tried directing or producing",
        "Do you prefer ensemble casts or solo lead roles"
    ]
    random.shuffle(fallback)
    for q in fallback:
        if len(qs) >= 7:
            break
        if q not in qs:
            qs.append(q)
    out = []
    random.shuffle(qs)
    for q in qs:
        if q not in prev_used and q not in out:
            out.append(q)
        if len(out) == 3:
            break
    while len(out) < 3:
        out.append(f"Do you enjoy roles that challenge you creatively {len(out)+1}")
    return out[:3]

def generate_congrats_line_named(celebrity):
    try:
        sys = f'You are {celebrity}. The fan guessed correctly. Say a short one line congrats and you may confirm your name.'
        r = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": "One line only."}],
            temperature=0.8
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return f"Well done. I am {celebrity}."

def check_guess_llm(user_input, actual_name):
    try:
        p = f"User guess: '{user_input}'. Correct name: '{actual_name}'. Reply exactly yes or no if the guess refers to the correct celebrity, allowing partials and misspellings."
        r = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": "Reply only yes or no."},
                      {"role": "user", "content": p}],
            temperature=0
        )
        return "yes" in r.choices[0].message.content.strip().lower()
    except Exception:
        return False

if "player_name" not in st.session_state:
    st.session_state.player_name = None
if "selected_industries" not in st.session_state:
    st.session_state.selected_industries = []
if "celebrity_rounds" not in st.session_state:
    st.session_state.celebrity_rounds = []
if "guessed" not in st.session_state:
    st.session_state.guessed = [False]*6
if "locked" not in st.session_state:
    st.session_state.locked = [False]*6
if "all_scores" not in st.session_state:
    st.session_state.all_scores = {}
if "difficulty" not in st.session_state:
    st.session_state.difficulty = "Medium"
if "guess_counts" not in st.session_state:
    st.session_state.guess_counts = [0]*6
if "used_generic_qs" not in st.session_state:
    st.session_state.used_generic_qs = set()

st.title("🎭 Guess the Celebrity")

if st.session_state.player_name is None:
    try:
        name = st.text_input("Enter your name")
        industries = st.multiselect("Choose your industries", ["Hollywood", "Bollywood", "Mollywood"])
        difficulty = st.select_slider("Difficulty", options=["Easy","Medium","Hard"], value="Medium")
        start = st.button("Start Game")
        if start and name and industries:
            st.session_state.player_name = name.strip()
            st.session_state.selected_industries = industries
            st.session_state.difficulty = difficulty
            st.session_state.celebrity_rounds = generate_random_celebrities(industries, difficulty)
            st.session_state.all_scores[name] = 0
            upsert_score(name, 0)
            st.rerun()
    except Exception:
        st.error("Unable to start. Try again.")
else:
    try:
        left, right = st.columns([1.15, 3.0])
        with left:
            ok = supabase_realtime_leaderboard_widget()
            if not ok:
                df_lb = fetch_leaderboard_df()
                st.subheader("🏆 Leaderboard")
                if not df_lb.empty:
                    df_show = df_lb.rename(columns={"player":"Player","score":"Score"})
                    st.dataframe(df_show, use_container_width=True, height=420)
                else:
                    st.info("No scores yet")
        with right:
            topc1, topc2 = st.columns([2,1])
            with topc1:
                st.markdown(f"**Player:** {st.session_state.player_name}")
            with topc2:
                ok_me = supabase_realtime_player_score_widget(st.session_state.player_name)
                if not ok_me:
                    live_score = get_player_score_from_db(st.session_state.player_name)
                    st.markdown(f"**Score:** {live_score}")

            all_attempted = all(g or l for g, l in zip(st.session_state.guessed, st.session_state.locked))
            if all_attempted:
                st.subheader("Nice game")
                st.write("All rounds attempted. Watch the leaderboard on the left.")
            else:
                tabs = st.tabs([f"Round {i+1}" for i in range(6)])
                for i in range(6):
                    with tabs[i]:
                        if i >= len(st.session_state.celebrity_rounds):
                            st.warning("Round not available")
                            continue
                        celeb = st.session_state.celebrity_rounds[i]

                        if st.session_state.locked[i]:
                            st.error(f"Better luck next time. It was {celeb}.")
                            continue

                        if st.session_state.guessed[i]:
                            st.success(f"Already guessed correctly. It was {celeb}.")
                            continue

                        key_intro = f"intro_{i}"
                        if key_intro not in st.session_state:
                            st.session_state[key_intro] = generate_intro(celeb)
                        st.info(st.session_state[key_intro])

                        qkey = f"qset_{i}"
                        if qkey not in st.session_state:
                            st.session_state[qkey] = generate_generic_questions(st.session_state.used_generic_qs)
                        qs = st.session_state.get(qkey, [])
                        if not isinstance(qs, list):
                            qs = []
                        while len(qs) < 3:
                            qs.append(f"Do you enjoy roles that challenge you creatively {len(qs)+1}")

                        try:
                            ccols = st.columns(3)
                            for idx in range(3):
                                label = qs[idx]
                                if ccols[idx].button(label, key=f"qbtn_{i}_{idx}"):
                                    st.session_state[f"prompt_{i}"] = label
                                    st.session_state.used_generic_qs.update(qs)
                                    st.session_state[qkey] = generate_generic_questions(st.session_state.used_generic_qs)
                        except Exception:
                            pass

                        user_prompt = st.text_area("Your message", key=f"prompt_{i}")
                        if st.button("Ask", key=f"ask_{i}") and user_prompt:
                            reply = generate_response(celeb, user_prompt)
                            st.success("Celebrity says")
                            st.markdown(reply)
                            st.session_state.used_generic_qs.update(qs)
                            st.session_state[qkey] = generate_generic_questions(st.session_state.used_generic_qs)

                        st.markdown(f"Guesses used: {st.session_state.guess_counts[i]} of 3")
                        if st.session_state.guess_counts[i] >= 3:
                            st.session_state.locked[i] = True
                            st.error(f"Better luck next time. It was {celeb}.")
                            continue

                        guess = st.text_input("Your guess", key=f"guess_{i}")
                        if st.button("Submit Guess", key=f"guess_btn_{i}") and guess:
                            st.session_state.guess_counts[i] += 1
                            okg = check_guess_llm(guess, celeb)
                            if okg:
                                st.success(f"Correct. It was {celeb}.")
                                st.session_state.guessed[i] = True
                                pts = select_difficulty_params(st.session_state.difficulty)["points"]
                                current = get_player_score_from_db(st.session_state.player_name)
                                new_score = current + pts
                                upsert_score(st.session_state.player_name, new_score)
                                try:
                                    line = generate_congrats_line_named(celeb)
                                    st.markdown(f"**{line}**")
                                except Exception:
                                    st.markdown(f"**Well done. I am {celeb}.**")
                            else:
                                if st.session_state.guess_counts[i] >= 3:
                                    st.session_state.locked[i] = True
                                    st.error(f"Better luck next time. It was {celeb}.")
                                else:
                                    st.error("Not quite. Try again")

            all_attempted = all(g or l for g, l in zip(st.session_state.guessed, st.session_state.locked))
            if all_attempted:
                st.balloons()
                live_score = get_player_score_from_db(st.session_state.player_name)
                st.subheader("You have attempted all rounds")
                st.write(f"Final Score: {live_score} of 6 rounds")
                if st.button("Play Again"):
                    for key in ['player_name','selected_industries','celebrity_rounds','guessed','locked','difficulty','guess_counts','used_generic_qs']:
                        st.session_state.pop(key, None)
                    st.rerun()
    except Exception:
        st.error("Unexpected error. Try again.")

