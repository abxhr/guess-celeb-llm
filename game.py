# ─── 0) Disable SSL cert checks globally (testing) ───────────────
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
# ────────────────────────────────────────────────────────────────────────────

import streamlit as st
import openai
import random
import os
from collections import defaultdict
import time

# ─── Config ───────────────────────────────────────────────────────
st.set_page_config(page_title="🎭 Guess the Celebrity", layout="centered")
openai.api_key = os.environ["OPENAI_API_KEY"]

# ─── Initialize State ─────────────────────────────────────────────
if 'player_name' not in st.session_state:
    st.session_state.player_name = None
if 'selected_industries' not in st.session_state:
    st.session_state.selected_industries = []
if 'celebrity_rounds' not in st.session_state:
    st.session_state.celebrity_rounds = []
if 'current_round' not in st.session_state:
    st.session_state.current_round = 0
if 'guessed' not in st.session_state:
    st.session_state.guessed = [False]*6
if 'scores' not in st.session_state:
    st.session_state.scores = defaultdict(int)
if 'all_scores' not in st.session_state:
    st.session_state.all_scores = {}

# ─── Functions ────────────────────────────────────────────────────
def generate_random_celebrities(selected_industries):
    joined = ", ".join(selected_industries)
    system_prompt = f"Give me 10 randomly selected famous celebrities only from the following movie industries: {joined}. Include a mix of male and female. Reply only as a Python list with names."
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": system_prompt}
        ]
    )
    text = response.choices[0].message.content.strip()
    print(text)
    try:
        result = eval(text)
        print(result)
        return random.sample(result, 6) if len(result) >= 6 else result
    except:
        return random.sample(["Shah Rukh Khan", "Emma Watson", "Leonardo DiCaprio", "Fahadh Faasil", "Scarlett Johansson", "Mammootty"], 6)


def generate_intro(celebrity):
    system_prompt = f"""
        You are roleplaying as the celebrity {celebrity}. Introduce yourself **to a fan** in a short, fun message.

        ⚠️ DO NOT mention or spell your name.

        ✅ DO:
        - Show your personality and speaking style.
        - Mention a **famous role**, **movie**, **co-star**, **award**, or **signature trait** as a **subtle hint**.
        - Use phrases or references fans might recognize (like “That dream-sharing movie…” instead of "Inception").
        - Keep it engaging and natural, like the celeb is casually talking to a fan who’s trying to guess who they are.

        ❌ DON’T:
        - Be too mysterious or poetic.
        - Be generic (“Hey everyone! I’m so happy to be here.”).
        - Say your name or obvious details.

        Reply with just the introduction. Length: 2–4 sentences.
    """
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Begin your intro now."}
        ]
    )
    return response.choices[0].message.content.strip()


def generate_sample_questions():
    all_questions = [
        "Have you won any major awards?",
        "Do you act in action movies?",
        "Are you known for comedy?",
        "Do you sing too?",
        "Have you worked in TV and film?",
        "Do you play serious roles often?",
        "Are you active on social media?",
        "Have you directed any projects?",
        "Do you usually play lead roles?",
        "Have you done voice acting?",
        "Do kids know who you are?",
        "Have you acted in sci-fi films?",
        "Do you dance in your movies?",
        "Have you done any theatre work?",
        "Do you speak more than one language?",
        "Have you been in a blockbuster?",
        "Have you been in a Netflix show?",
        "Do you often work with the same co-stars?",
        "Do you act in romantic films?",
        "Have you done any biopics?",
        "Are you known for a signature look?",
        "Have you appeared in commercials?",
        "Do you have a famous catchphrase?",
        "Are you often interviewed on talk shows?",
        "Do you work behind the camera too?"
    ]
    return random.sample(all_questions, 3)


def generate_response(celebrity, user_prompt):
    system_prompt = f"""
    You are roleplaying as the celebrity {celebrity}. Stay in character but be clear, helpful, and friendly.

    Instructions:
    - Speak naturally, like the real {celebrity} would in an interview or casual fan chat
    - Do NOT be overly poetic or mysterious
    - Refer to your actual work, awards, co-stars, or famous traits
    - If asked your name, dodge the question lightly but stay in character
    - Occasionally offer subtle, useful hints
    - Avoid dramatic monologues or riddles
    - Never say your name, spelling, or initials directly
    """
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    return response.choices[0].message.content.strip()

def check_guess_llm(user_input, actual_name):
    prompt = f"The user entered the guess: '{user_input}'. The correct celebrity is '{actual_name}'. Does the guess refer to the correct celebrity even if it's a partial, misspelled, or informal version? Reply only 'yes' or 'no'."
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that checks fuzzy name matches."},
            {"role": "user", "content": prompt}
        ]
    )
    return "yes" in response.choices[0].message.content.strip().lower()

# ─── Main UI ──────────────────────────────────────────────────────
st.title("🎭 Guess the Celebrity")

# ─── Login Page ───────────────────────────────────────────────────
if st.session_state.player_name is None:
    name = st.text_input("Enter your name to start:")
    industries = st.multiselect("Choose your industries:", ["Hollywood", "Bollywood", "Mollywood"])
    if st.button("Start Game") and name and industries:
        st.session_state.player_name = name
        st.session_state.selected_industries = industries
        with st.spinner("Picking celebrities..."):
            st.session_state.celebrity_rounds = generate_random_celebrities(industries)
        st.session_state.all_scores[name] = 0
        st.rerun()

elif st.sidebar.button("🏆 View Leaderboard"):
    st.subheader("🏆 Leaderboard")
    if st.session_state.all_scores:
        sorted_scores = sorted(st.session_state.all_scores.items(), key=lambda x: x[1], reverse=True)
        for name, score in sorted_scores:
            st.write(f"**{name}** — {score} points")
    else:
        st.info("No scores yet!")
else:
    st.sidebar.markdown(f"**Player:** {st.session_state.player_name}")
    st.sidebar.markdown(f"**Score:** {st.session_state.all_scores[st.session_state.player_name]}")

    tabs = st.tabs([f"Round {i+1}" for i in range(6)])

    for i in range(6):
        with tabs[i]:
            celeb = st.session_state.celebrity_rounds[i]

            if not st.session_state.guessed[i]:
                if f"intro_shown_{i}" not in st.session_state:
                    with st.spinner("A mysterious figure steps into the spotlight..."):
                        try:
                            intro = generate_intro(celeb)
                            st.session_state[f"intro_shown_{i}"] = intro
                        except Exception as e:
                            intro = "(Intro unavailable.)"
                            st.session_state[f"intro_shown_{i}"] = intro
                    time.sleep(1)
                st.info(st.session_state[f"intro_shown_{i}"])

                st.write(f"🕵️ Ask questions to guess who this celebrity is.")

                qkey = f"sample_q_{i}"
                if qkey not in st.session_state or st.session_state.get(f"refresh_q_{i}", True):
                    st.session_state[qkey] = generate_sample_questions()
                    st.session_state[f"refresh_q_{i}"] = False

                sample_questions = st.session_state[qkey]
                st.markdown("💬 **Click a question below or type your own:**")

                cols = st.columns(3)
                for idx, q in enumerate(sample_questions):
                    if cols[idx].button(q, key=f"btn_{i}_{idx}"):
                        st.session_state[f"prompt_{i}"] = q
                        st.session_state[f"ask_trigger_{i}"] = True

                user_prompt = st.text_area("Your message to the celebrity:", key=f"prompt_{i}")
                ask_clicked = st.button("Ask", key=f"ask_{i}") or st.session_state.get(f"ask_trigger_{i}", False)

                if ask_clicked and user_prompt:
                    st.session_state[f"ask_trigger_{i}"] = False
                    with st.spinner("The celebrity is thinking..."):
                        try:
                            reply = generate_response(celeb, user_prompt)
                            st.success("Celebrity's reply:")
                            st.markdown(reply)
                            st.session_state[qkey] = generate_sample_questions()
                        except Exception as e:
                            st.error(f"Error: {e}")

                guess = st.text_input("Who do you think it is?", key=f"guess_{i}")
                if st.button("Submit Guess", key=f"guess_btn_{i}") and guess:
                    with st.spinner("Evaluating your guess..."):
                        if check_guess_llm(guess, celeb):
                            st.success(f"🎉 Correct! It was {celeb}!")
                            st.session_state.guessed[i] = True
                            st.session_state.all_scores[st.session_state.player_name] += 1
                        else:
                            st.error("❌ Not quite. Try asking more questions!")
            else:
                st.success(f"✅ Already guessed correctly: {celeb}")

    if all(st.session_state.guessed):
        st.balloons()
        st.subheader(f"🎉 You've guessed all celebrities!")
        st.write(f"Final Score: {st.session_state.all_scores[st.session_state.player_name]} / 6")
        if st.button("Play Again"):
            for key in ['player_name', 'selected_industries', 'celebrity_rounds', 'current_round', 'guessed']:
                st.session_state.pop(key, None)
            st.rerun()