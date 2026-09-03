import random
import json
import html
import os
import re
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st
from PIL import Image, ImageDraw, ImageOps


@st.cache_data(show_spinner=False)
def fit_image_to_canvas(image_path, canvas_size=(180, 140), image_size=None, vertical_alignment="center"):
    """Fit an image on a transparent canvas without distortion."""
    image_size = image_size or canvas_size
    with Image.open(image_path) as source:
        fitted = ImageOps.contain(source.convert("RGBA"), image_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    offset_y = canvas_size[1] - fitted.height if vertical_alignment == "bottom" else (canvas_size[1] - fitted.height) // 2
    offset = ((canvas_size[0] - fitted.width) // 2, offset_y)
    canvas.paste(fitted, offset, fitted)
    return canvas


@st.cache_data(show_spinner=False)
def crop_image_region(image_path, crop_box):
    """Return one clearly framed learning item cropped from a composite illustration."""
    with Image.open(image_path) as source:
        cropped = source.convert("RGBA").crop(crop_box)
    corner_mask = Image.new("L", cropped.size, 0)
    ImageDraw.Draw(corner_mask).rounded_rectangle(
        (0, 0, cropped.width - 1, cropped.height - 1),
        radius=max(12, min(cropped.size) // 12),
        fill=255,
    )
    cropped.putalpha(corner_mask)
    return cropped


def render_unit1_study_image(image_name, canvas_size=(180, 180), image_size=(165, 170)):
    """Render Unit 1 learning illustrations on one bottom-aligned visual canvas."""
    image_path = Path(__file__).with_name("assets") / "people" / image_name
    st.image(
        fit_image_to_canvas(
            image_path,
            canvas_size=canvas_size,
            image_size=image_size,
            vertical_alignment="bottom",
        ),
        width=canvas_size[0],
    )


def clear_session_state_key(key):
    """Remove transient feedback before Streamlit performs its normal widget rerun."""
    st.session_state.pop(key, None)


def navigate_to_page(page_name):
    """Change the sidebar page safely before its navigation widget is rendered."""
    st.session_state.page_nav = page_name


def set_session_state_value(key, value=True):
    """Set a navigation flag during the widget callback phase."""
    st.session_state[key] = value


def complete_vocabulary_review(unit_number):
    """Complete the first review step once per unit and award its XP."""
    review_key = f"review_vocab_done_{unit_number}"
    if not st.session_state.get(review_key, False):
        st.session_state[review_key] = True
        st.session_state.daily_tasks["vocab"] = True
        st.session_state.total_xp = st.session_state.get("total_xp", 0) + 5
        save_progress()


def complete_activity2(unit_number):
    """Complete a unit once and show its result beside the submission control."""
    completion_key = f"unit_completed_{unit_number}"
    if not st.session_state.get(completion_key, False):
        st.session_state[completion_key] = True
        st.session_state.total_xp = st.session_state.get("total_xp", 0) + 20
        save_progress()
    st.session_state[f"activity2_submission_notice_{unit_number}"] = True


def restart_review_routine(unit_number):
    """Re-open the optional three-step review without removing completion history."""
    for step in ("vocab", "grammar", "sentence"):
        st.session_state.pop(f"review_{step}_done_{unit_number}", None)
    if st.session_state.get("builder_unit_number") == unit_number:
        st.session_state.builder_index = 0
        st.session_state.builder_answer = []
        st.session_state.builder_completed = False
        st.session_state.builder_finished = False


def format_korean_phone_input(widget_key):
    """Format a Korean phone number after entry without keeping non-digits."""
    digits = re.sub(r"\D", "", st.session_state.get(widget_key, ""))[:11]
    if len(digits) <= 3:
        formatted = digits
    elif len(digits) <= 7:
        formatted = f"{digits[:3]}-{digits[3:]}"
    else:
        formatted = f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"
    st.session_state[widget_key] = formatted


TEXTBOOK_TITLE = "세종한국어 1A"
TEXTBOOK_EDITION = "2022년 개정판"
TEXTBOOK_SOURCE = "누리 세종학당 · 세종학당재단"

# 전체 단원을 빠르게 검수할 때는 기본값 1을 사용합니다. 최종 운영에서는
# KOREAN_APP_REVIEW_MODE=0으로 실행하면 순차 잠금이 적용됩니다.
REVIEW_MODE = os.getenv("KOREAN_APP_REVIEW_MODE", "1") == "1"
ACTIVE_REFERENCE_IMAGE = None

# Required completion standard for every unit:
# 1) Graded choices start unselected (`index=None`).
# 2) Check buttons stay disabled until every required response is present.
# 3) A step is complete only after all of its reading, speaking, and quiz activities pass.
# Preserve Korean target forms and examples even when interface guidance is translated.


def get_unit_completion_steps(unit_number):
    """Return valid sequential completion states without treating free access as completion."""
    vocab_done = bool(st.session_state.get(f"vocab_done_{unit_number}", False))
    grammar1_done = vocab_done and bool(st.session_state.get(f"grammar1_done_{unit_number}", False))
    grammar2_done = grammar1_done and bool(st.session_state.get(f"grammar2_done_{unit_number}", False))
    activity1_done = grammar2_done and bool(st.session_state.get(f"activity1_completed_{unit_number}", False))
    unit_done = activity1_done and bool(st.session_state.get(f"unit_completed_{unit_number}", False))
    return vocab_done, grammar1_done, grammar2_done, activity1_done, unit_done

TEXTBOOK_UNITS = [
    {"number": 0, "title": "입문 · 한글을 배워요", "goal": "한글 자모와 기본 음절을 읽고 쓸 수 있어요.", "grammar": "한글 자모 · 음절 읽기", "functions": "인사와 교실 표현 익히기"},
    {"number": 1, "title": "안녕하세요? 저는 안나예요", "goal": "이름과 국적, 직업을 소개할 수 있어요.", "grammar": "이에요/예요 · 은/는", "functions": "인사하기 · 자기소개하기"},
    {"number": 2, "title": "전화번호가 뭐예요?", "goal": "전화번호와 숫자를 묻고 답할 수 있어요.", "grammar": "이/가 · 숫자", "functions": "전화번호 묻고 답하기"},
    {"number": 3, "title": "제 가방은 책상 옆에 있어요", "goal": "물건을 가리키고 사물의 위치를 말할 수 있어요.", "grammar": "이/그/저 · 에 있다/없다", "functions": "물건 가리키기 · 위치 말하기"},
    {"number": 4, "title": "한국어를 공부해요", "goal": "일상적인 활동과 공부를 말할 수 있어요.", "grammar": "-아요/어요 · 을/를", "functions": "일상생활 말하기"},
    {"number": 5, "title": "빵하고 우유를 사요", "goal": "가는 장소와 사는 물건을 말할 수 있어요.", "grammar": "에 가다 · 하고", "functions": "장소 말하기 · 물건 사기"},
    {"number": 6, "title": "사과 다섯 개 주세요", "goal": "수량과 단위 명사를 사용해 주문하고 공손하게 요청할 수 있어요.", "grammar": "단위 명사 · -(으)세요", "functions": "수량 묻고 답하기 · 주문하고 요청하기"},
    {"number": 7, "title": "일곱 시에 시작해요", "goal": "날짜와 시간, 일정을 말할 수 있어요.", "grammar": "에 · 몇 시예요?", "functions": "날짜·시간 묻고 답하기 · 일정 말하기"},
    {"number": 8, "title": "날씨가 더워요?", "goal": "날씨와 상태를 긍정·부정으로 설명할 수 있어요.", "grammar": "안 · ㅂ 불규칙", "functions": "날씨 묻고 답하기 · 상태 설명하기"},
    {"number": 9, "title": "공원에서 산책했어요", "goal": "장소에서 한 과거의 일을 말할 수 있어요.", "grammar": "에서 · -았어요/-었어요", "functions": "과거 활동 말하기"},
    {"number": 10, "title": "우리 같이 놀이공원에 갈까요?", "goal": "함께 할 일을 제안하고 약속할 수 있어요.", "grammar": "-(으)ㄹ까요? · -(으)러 가다", "functions": "제안하기 · 약속 정하기"},
]

TEXTBOOK_QUESTION_BANK = {
    1: [
        {"sentence": "저__ 안나예요.", "options": ["은", "는", "이", "가"], "answer": "는", "skill": "은/는", "explanation": "‘저’의 마지막 글자에는 받침이 없어요. 그래서 ‘는’을 사용해요."},
        {"sentence": "저는 안나__.", "options": ["이에요", "예요", "을", "를"], "answer": "예요", "skill": "이에요/예요", "explanation": "이름의 마지막 글자 ‘나’에는 받침이 없어요. 그래서 ‘예요’를 사용해요."},
        {"sentence": "저는 학생__.", "options": ["이에요", "예요", "은", "는"], "answer": "이에요", "skill": "이에요/예요", "explanation": "‘학생’의 마지막 글자 ‘생’에는 ㅇ 받침이 있어요. 그래서 ‘이에요’를 사용해요."},
        {"sentence": "마이클__ 미국 사람이에요.", "options": ["은", "는", "이", "가"], "answer": "은", "skill": "은/는", "explanation": "이름의 마지막 글자 ‘클’에는 ㄹ 받침이 있어요. 그래서 ‘은’을 사용해요."},
        {"sentence": "안나__ 한국 사람이에요.", "options": ["은", "는", "이에요", "예요"], "answer": "는", "skill": "은/는", "explanation": "이름의 마지막 글자 ‘나’에는 받침이 없어요. 그래서 ‘는’을 사용해요."},
        {"sentence": "저는 선생님__.", "options": ["이에요", "예요", "은", "는"], "answer": "이에요", "skill": "이에요/예요", "explanation": "‘선생님’의 마지막 글자 ‘님’에는 ㅁ 받침이 있어요. 그래서 ‘이에요’를 사용해요."},
    ],
    2: [
        {"sentence": "전화번호__ 뭐예요?", "options": ["은", "는", "이", "가"], "answer": "가", "skill": "이/가", "explanation": "‘전화번호가 뭐예요?’는 정보를 물을 때 사용하는 표현이에요."},
        {"sentence": "이름__ 뭐예요?", "options": ["이", "가", "을", "를"], "answer": "이", "skill": "이/가", "explanation": "받침이 있는 ‘이름’ 뒤에는 ‘이’를 사용해요."},
        {"sentence": "교실 번호__ 몇 번이에요?", "options": ["이", "가", "은", "는"], "answer": "가", "skill": "이/가", "explanation": "받침이 없는 ‘번호’ 뒤에는 ‘가’를 사용해요."},
        {"sentence": "민수 씨__ 전화해요.", "options": ["이", "가", "을", "를"], "answer": "가", "skill": "이/가", "explanation": "전화하는 사람이 민수 씨이므로 주어를 나타내는 ‘가’를 사용해요."},
        {"sentence": "저 사람은 학생__ 아니에요.", "options": ["이", "가", "을", "를"], "answer": "이", "skill": "이/가 아니에요", "explanation": "받침이 있는 ‘학생’ 뒤에는 ‘이 아니에요’를 사용해요."},
        {"sentence": "이것은 컴퓨터__ 아니에요.", "options": ["이", "가", "은", "는"], "answer": "가", "skill": "이/가 아니에요", "explanation": "받침이 없는 ‘컴퓨터’ 뒤에는 ‘가 아니에요’를 사용해요."},
    ],
    3: [
        {"sentence": "내 손에 있는 __ 책은 한국어 책이에요.", "options": ["이", "그", "저", "의"], "answer": "이", "skill": "이/그/저", "explanation": "말하는 사람에게 가까운 물건은 ‘이’를 사용해요."},
        {"sentence": "듣는 사람 앞에 있는 __ 가방은 누구의 가방이에요?", "options": ["이", "그", "저", "에"], "answer": "그", "skill": "이/그/저", "explanation": "듣는 사람에게 가까운 물건은 ‘그’를 사용해요."},
        {"sentence": "두 사람에게서 멀리 있는 __ 시계를 보세요.", "options": ["이", "그", "저", "를"], "answer": "저", "skill": "이/그/저", "explanation": "말하는 사람과 듣는 사람 모두에게 먼 물건은 ‘저’를 사용해요."},
        {"sentence": "책이 책상 위__ 있어요.", "options": ["에", "에서", "을", "를"], "answer": "에", "skill": "에 있다/없다", "explanation": "물건이 존재하는 장소 뒤에는 ‘에’를 사용해요."},
        {"sentence": "교실에 시계가 __.", "options": ["있어요", "없어요", "읽어요", "마셔요"], "answer": "있어요", "skill": "에 있다/없다", "explanation": "교실에 시계가 보이므로 ‘있어요’라고 말해요."},
        {"sentence": "가방이 책상 위에 __. 의자 아래에 있어요.", "options": ["있어요", "없어요", "공부해요", "만나요"], "answer": "없어요", "skill": "에 있다/없다", "explanation": "가방은 의자 아래에 있으므로 책상 위에는 ‘없어요’라고 말해요."},
    ],
    4: [
        {"sentence": "저는 한국어__ 공부해요.", "options": ["은", "는", "을", "를"], "answer": "를", "skill": "을/를", "explanation": "공부하는 대상을 나타낼 때 목적격 조사 ‘를’을 사용해요."},
        {"sentence": "마리는 책__ 읽어요.", "options": ["을", "를", "이", "가"], "answer": "을", "skill": "을/를", "explanation": "받침이 있는 ‘책’ 뒤에는 ‘을’을 사용해요."},
        {"sentence": "주노는 영화__ 봐요.", "options": ["을", "를", "은", "는"], "answer": "를", "skill": "을/를", "explanation": "받침이 없는 ‘영화’ 뒤에는 ‘를’을 사용해요."},
        {"sentence": "저는 아침을 먹__.", "options": ["아요", "어요", "해요", "예요"], "answer": "어요", "skill": "-아요/어요", "explanation": "‘먹다’는 ‘먹어요’로 바뀌어요."},
        {"sentence": "수지는 영화를 __.", "options": ["봐요", "보어요", "보해요", "봐아요"], "answer": "봐요", "skill": "-아요/어요", "explanation": "‘보다’에 ‘-아요’를 붙인 ‘보아요’가 줄어서 ‘봐요’가 돼요."},
        {"sentence": "저는 한국어를 공부__.", "options": ["아요", "어요", "해요", "예요"], "answer": "해요", "skill": "-아요/어요", "explanation": "‘공부하다’는 ‘공부해요’로 바뀌어요."},
    ],
    5: [
        {"sentence": "빵__ 우유를 사요.", "options": ["하고", "에", "에서", "이"], "answer": "하고", "skill": "하고", "explanation": "명사를 나열할 때 ‘하고’를 사용할 수 있어요."},
        {"sentence": "저는 마트__ 가요.", "options": ["에", "를", "하고", "이"], "answer": "에", "skill": "에 가다", "explanation": "이동하는 목적지 ‘마트’ 뒤에는 ‘에’를 사용해요."},
    ],
    6: [
        {"sentence": "사과 __ 개 주세요.", "options": ["다섯", "오", "다섯에", "오에"], "answer": "다섯", "skill": "고유어 수", "explanation": "개수를 셀 때 고유어 수 ‘다섯’을 사용해요."},
        {"sentence": "물 한 __ 주세요.", "options": ["개", "명", "병", "권"], "answer": "병", "skill": "단위 명사", "explanation": "병에 담긴 물은 ‘한 병’이라고 세어요."},
    ],
    7: [
        {"sentence": "수업은 일곱 시__ 시작해요.", "options": ["에", "에서", "을", "는"], "answer": "에", "skill": "시간의 에", "explanation": "시간을 나타낼 때 ‘시간에’를 사용해요."},
        {"sentence": "지금 몇 __예요?", "options": ["시", "명", "개", "권"], "answer": "시", "skill": "시간 표현", "explanation": "시간을 물을 때 ‘몇 시예요?’라고 말해요."},
    ],
    8: [
        {"sentence": "오늘 날씨가 더__?", "options": ["워요", "어요", "아요", "이에요"], "answer": "워요", "skill": "형용사 활용", "explanation": "‘덥다’는 ‘더워요’로 활용해요."},
        {"sentence": "날씨가 아주 추__.", "options": ["워요", "어요", "아요", "예요"], "answer": "워요", "skill": "-아요/어요", "explanation": "‘춥다’는 ‘추워요’로 활용해요."},
    ],
    9: [
        {"sentence": "공원__ 산책했어요.", "options": ["에", "에서", "을", "는"], "answer": "에서", "skill": "에/에서", "explanation": "행동이 일어나는 장소에는 ‘에서’를 사용해요."},
        {"sentence": "어제 영화를 보__.", "options": ["았어요", "었어요", "아요", "어요"], "answer": "았어요", "skill": "과거형", "explanation": "‘보다’의 과거형은 ‘봤어요’예요."},
    ],
    10: [
        {"sentence": "우리 같이 놀이공원에 갈__?", "options": ["까요", "어요", "았어요", "게요"], "answer": "까요", "skill": "-(으)ㄹ까요?", "explanation": "같이 하자고 제안할 때 ‘-(으)ㄹ까요?’를 사용해요."},
        {"sentence": "주말에 친구를 만나__ 가요.", "options": ["러", "으러", "에서", "하고"], "answer": "러", "skill": "-(으)러 가다", "explanation": "‘만나다’처럼 받침이 없는 동사에는 ‘-러 가다’를 사용해요."},
    ],
}

# 세종한국어 1A의 단원별 문법 목표에 맞춘 자체 제작 순차 연습 문항입니다.
# 교재의 문장을 복제하지 않고 같은 초급 어휘와 기능을 새로운 맥락에 적용합니다.
GRAMMAR1_SEQUENCE_BANK = {
    1: [
        ("저는 민수__." , ["예요", "이에요", "은", "는"], "예요", "받침이 없는 이름 뒤에는 ‘예요’를 사용해요."),
        ("저는 학생__." , ["예요", "이에요", "이", "가"], "이에요", "받침이 있는 명사 뒤에는 ‘이에요’를 사용해요."),
        ("마리아 씨는 의사__." , ["예요", "이에요", "를", "에"], "예요", "‘의사’는 받침이 없으므로 ‘예요’가 자연스러워요."),
        ("제 친구는 선생님__." , ["예요", "이에요", "하고", "에서"], "이에요", "‘선생님’은 받침이 있으므로 ‘이에요’를 사용해요."),
    ],
    2: [
        ("전화번호__ 뭐예요?", ["이", "가", "을", "를"], "가", "‘전화번호’는 받침이 없으므로 ‘가’를 사용해요."),
        ("교실 번호__ 몇 번이에요?", ["이", "가", "은", "는"], "가", "‘번호’는 받침이 없으므로 ‘가’를 사용해요."),
        ("민수 씨__ 전화해요.", ["이", "가", "을", "를"], "가", "‘민수 씨’가 행동의 주체이므로 ‘가’를 사용해요."),
        ("수진__ 제 번호를 알아요.", ["이", "가", "에", "에서"], "이", "받침이 있는 ‘수진’ 뒤에는 ‘이’를 사용해요."),
    ],
    3: [
        ("내 손에 있는 __ 책은 한국어 책이에요.", ["이", "그", "저", "의"], "이", "말하는 사람에게 가까운 물건은 ‘이’를 사용해요."),
        ("듣는 사람 앞에 있는 __ 가방은 누구의 가방이에요?", ["이", "그", "저", "에"], "그", "듣는 사람에게 가까운 물건은 ‘그’를 사용해요."),
        ("두 사람에게서 멀리 있는 __ 시계를 보세요.", ["이", "그", "저", "를"], "저", "말하는 사람과 듣는 사람 모두에게 먼 물건은 ‘저’를 사용해요."),
        ("제가 들고 있는 __ 필통은 제 필통이에요.", ["이", "그", "저", "에"], "이", "말하는 사람 가까이에 있는 필통이므로 ‘이’를 사용해요."),
    ],
    4: [
        ("저는 아침을 __.", ["먹어요", "먹아요", "먹해요", "먹예요"], "먹어요", "‘먹다’는 어간의 모음이 ㅓ이므로 ‘먹어요’가 돼요."),
        ("주노는 영화를 __.", ["봐요", "보어요", "보해요", "보예요"], "봐요", "‘보다’에 ‘-아요’를 붙인 ‘보아요’가 줄어서 ‘봐요’가 돼요."),
        ("오늘 친구를 __.", ["만나요", "만나어요", "만나해요", "만나예요"], "만나요", "‘만나다’의 ‘아’ 모음 뒤에는 ‘-아요’가 붙어서 ‘만나요’가 돼요."),
        ("마리는 한국어를 __.", ["공부해요", "공부아요", "공부어요", "공부예요"], "공부해요", "‘공부하다’는 ‘공부해요’로 바뀌어요."),
    ],
    5: [
        ("마리 씨는 영화관__ 가요.", ["에", "를", "하고", "이"], "에", "이동하는 목적지 뒤에는 ‘에’를 사용해요."),
        ("주노 씨는 식당__ 가요.", ["에", "에서", "를", "하고"], "에", "가는 장소인 ‘식당’ 뒤에 ‘에’를 붙여요."),
        ("안나 씨는 학교__ 가요.", ["에", "가", "를", "하고"], "에", "목적지인 ‘학교’ 뒤에는 ‘에’를 사용해요."),
        ("유진 씨는 회사__ 가요.", ["에", "에서", "를", "는"], "에", "‘회사에 가요’처럼 장소와 ‘가다’를 연결해요."),
    ],
    6: [
        ("학생이 두 __ 있어요.", ["명", "개", "마리", "권"], "명", "사람을 셀 때는 단위 명사 ‘명’을 사용해요."),
        ("책이 여섯 __ 있어요.", ["권", "병", "잔", "살"], "권", "책을 셀 때는 단위 명사 ‘권’을 사용해요."),
        ("물 세 __ 주세요.", ["병", "명", "마리", "장"], "병", "병에 든 음료를 셀 때는 ‘병’을 사용해요."),
        ("고양이 한 __가 있어요.", ["마리", "개", "명", "권"], "마리", "동물을 셀 때는 단위 명사 ‘마리’를 사용해요."),
    ],
    7: [
        ("수업은 아홉 시__ 시작해요.", ["에", "에서", "를", "하고"], "에", "동작이 일어나는 시간을 나타낼 때 ‘에’를 사용해요."),
        ("저는 열두 시__ 점심을 먹어요.", ["에", "에서", "이", "가"], "에", "구체적인 시각 뒤에는 ‘에’를 사용해요."),
        ("저녁 일곱 시__ 친구를 만나요.", ["에", "를", "하고", "에서"], "에", "약속 시간을 말할 때도 ‘에’를 사용해요."),
        ("영화가 세 시__ 끝나요.", ["에", "에서", "은", "는"], "에", "끝나는 시각을 나타낼 때 ‘에’를 사용해요."),
    ],
    8: [
        ("오늘 날씨가 더워__." , ["요", "어요", "아요", "예요"], "요", "‘더워요’처럼 ㅂ이 바뀌어 활용해요."),
        ("겨울에는 날씨가 추워__." , ["요", "아요", "이에요", "를"], "요", "‘춥다’는 ‘추워요’로 활용해요."),
        ("하늘이 아주 맑__." , ["아요", "어요", "예요", "해요"], "아요", "‘맑다’는 밝은 모음과 결합해 ‘맑아요’가 돼요."),
        ("오늘은 비가 많이 와__." , ["요", "아요", "이에요", "에서"], "요", "‘오다’는 ‘와요’로 활용해요."),
    ],
    9: [
        ("공원__ 산책했어요.", ["에서", "에", "를", "하고"], "에서", "행동이 이루어진 장소에는 ‘에서’를 사용해요."),
        ("도서관__ 공부했어요.", ["에서", "에", "이", "가"], "에서", "공부한 장소를 나타낼 때 ‘에서’를 사용해요."),
        ("친구하고 식당__ 밥을 먹었어요.", ["에서", "에", "를", "은"], "에서", "먹는 행동이 일어난 장소에는 ‘에서’를 사용해요."),
        ("주말에 집__ 영화를 봤어요.", ["에서", "에", "하고", "가"], "에서", "영화를 본 장소를 ‘에서’로 표시해요."),
    ],
    10: [
        ("우리 같이 공원에 갈__?", ["까요", "어요", "았어요", "이에요"], "까요", "함께할 일을 제안할 때 ‘-(으)ㄹ까요?’를 사용해요."),
        ("주말에 영화를 볼__?", ["까요", "예요", "에서", "하고"], "까요", "받침이 있는 동사에는 ‘-을까요?’가 결합해요."),
        ("같이 점심을 먹을__?", ["까요", "아요", "어요", "에요"], "까요", "상대방에게 함께 먹자고 제안하는 표현이에요."),
        ("토요일에 친구를 만날__?", ["까요", "에서", "하고", "였어요"], "까요", "함께할 약속을 정할 때 ‘-ㄹ까요?’를 사용해요."),
    ],
}

TEXTBOOK_SENTENCE_MISSIONS = {
    1: [
        (["저는", "안나예요"], "저는 안나예요"),
        (["저는", "학생이에요"], "저는 학생이에요"),
        (["안녕하세요", "저는", "안나예요"], "안녕하세요 저는 안나예요"),
        (["저는", "한국", "사람이에요"], "저는 한국 사람이에요"),
        (["마이클은", "미국", "사람이에요"], "마이클은 미국 사람이에요"),
        (["저는", "회사원이에요"], "저는 회사원이에요"),
    ],
    2: [
        (["전화번호가", "뭐예요"], "전화번호가 뭐예요"),
        (["제", "전화번호는", "010-1234-5678이에요"], "제 전화번호는 010-1234-5678이에요"),
        (["친구의", "전화번호가", "뭐예요"], "친구의 전화번호가 뭐예요"),
        (["제", "번호는", "010-9876-5432예요"], "제 번호는 010-9876-5432예요"),
    ],
    3: [
        (["가방은", "책상", "옆에", "있어요"], "가방은 책상 옆에 있어요"),
        (["이것은", "제", "책이에요"], "이것은 제 책이에요"),
        (["휴대전화는", "가방", "안에", "있어요"], "휴대전화는 가방 안에 있어요"),
        (["책상", "위에", "책이", "있어요"], "책상 위에 책이 있어요"),
    ],
    4: [
        (["저는", "한국어를", "공부해요"], "저는 한국어를 공부해요"),
        (["매일", "책을", "읽어요"], "매일 책을 읽어요"),
        (["친구는", "음악을", "들어요"], "친구는 음악을 들어요"),
        (["저는", "매일", "운동해요"], "저는 매일 운동해요"),
    ],
    5: [
        (["빵하고", "우유를", "사요"], "빵하고 우유를 사요"),
        (["저는", "마트에", "가요"], "저는 마트에 가요"),
        (["사과하고", "바나나를", "사요"], "사과하고 바나나를 사요"),
        (["수지는", "백화점에", "가요"], "수지는 백화점에 가요"),
    ],
    6: [
        (["사과", "다섯", "개", "주세요"], "사과 다섯 개 주세요"),
        (["물", "한", "병", "주세요"], "물 한 병 주세요"),
        (["귤", "세", "개", "주세요"], "귤 세 개 주세요"),
        (["커피", "두", "잔", "주세요"], "커피 두 잔 주세요"),
    ],
    7: [
        (["수업은", "일곱", "시에", "시작해요"], "수업은 일곱 시에 시작해요"),
        (["지금", "몇", "시예요"], "지금 몇 시예요"),
        (["아홉", "시에", "만나요"], "아홉 시에 만나요"),
        (["영화는", "세", "시에", "시작해요"], "영화는 세 시에 시작해요"),
    ],
    8: [
        (["오늘", "날씨가", "더워요"], "오늘 날씨가 더워요"),
        (["겨울은", "날씨가", "추워요"], "겨울은 날씨가 추워요"),
        (["오늘은", "날씨가", "좋아요"], "오늘은 날씨가 좋아요"),
        (["서울은", "날씨가", "어때요"], "서울은 날씨가 어때요"),
    ],
    9: [
        (["공원에서", "산책했어요"], "공원에서 산책했어요"),
        (["어제", "영화를", "봤어요"], "어제 영화를 봤어요"),
        (["주말에", "친구와", "축구했어요"], "주말에 친구와 축구했어요"),
        (["어제", "도서관에서", "공부했어요"], "어제 도서관에서 공부했어요"),
    ],
    10: [
        (["우리", "같이", "갈까요"], "우리 같이 갈까요"),
        (["친구를", "만나러", "가요"], "친구를 만나러 가요"),
        (["주말에", "공원에", "갈까요"], "주말에 공원에 갈까요"),
        (["같이", "점심을", "먹으러", "가요"], "같이 점심을 먹으러 가요"),
    ],
}

TEXTBOOK_VOCABULARY = {
    1: [
        ("한국", "Korea"),
        ("캐나다", "Canada"),
        ("베트남", "Vietnam"),
        ("미국", "the United States"),
        ("프랑스", "France"),
        ("태국", "Thailand"),
        ("인도네시아", "Indonesia"),
        ("중국", "China"),
        ("일본", "Japan"),
        ("러시아", "Russia"),
        ("케냐", "Kenya"),
        ("회사원", "office worker"),
        ("대학생", "university student"),
        ("의사", "doctor"),
        ("경찰", "police officer"),
        ("선생님", "teacher"),
        ("가수", "singer"),
        ("요리사", "cook"),
    ],
    2: [("전화번호", "phone number"), ("숫자", "number"), ("친구", "friend")],
    3: [
        ("책", "book"), ("책상", "desk"), ("의자", "chair"),
        ("가방", "bag"), ("필통", "pencil case"), ("시계", "clock"),
        ("위", "on / above"), ("아래", "under / below"), ("앞", "in front of"),
        ("뒤", "behind"), ("옆", "beside"), ("오른쪽", "right side"),
        ("왼쪽", "left side"), ("사이", "between"), ("안", "inside"), ("밖", "outside"),
    ],
    4: [
        ("먹어요", "eat"), ("읽어요", "read"), ("봐요", "watch / see"),
        ("마셔요", "drink"), ("들어요", "listen"), ("만나요", "meet"),
        ("자요", "sleep"), ("일해요", "work"), ("요리해요", "cook"),
        ("공부해요", "study"),
    ],
    5: [
        ("학교", "school"), ("회사", "office"), ("식당", "restaurant"),
        ("카페", "cafe"), ("공원", "park"), ("마트", "supermarket"),
        ("빵", "bread"), ("라면", "ramen"), ("과일", "fruit"),
        ("커피", "coffee"), ("차", "tea"), ("우유", "milk"),
        ("과자", "snack / cookie"), ("아이스크림", "ice cream"),
    ],
    6: [
        ("하나", "one"), ("둘", "two"), ("셋", "three"), ("넷", "four"),
        ("다섯", "five"), ("여섯", "six"), ("일곱", "seven"), ("여덟", "eight"),
        ("아홉", "nine"), ("열", "ten"), ("스물", "twenty"), ("서른", "thirty"),
        ("마흔", "forty"), ("쉰", "fifty"), ("예순", "sixty"), ("일흔", "seventy"),
        ("여든", "eighty"), ("아흔", "ninety"), ("백", "one hundred"),
    ],
    7: [("월요일", "Monday"), ("화요일", "Tuesday"), ("수요일", "Wednesday"), ("목요일", "Thursday"), ("금요일", "Friday"), ("토요일", "Saturday"), ("일요일", "Sunday"), ("오늘", "today"), ("내일", "tomorrow"), ("시", "o'clock / hour"), ("분", "minute"), ("시작하다", "to start"), ("만나다", "to meet"), ("수업", "class")],
    8: [("맑아요", "sunny"), ("흐려요", "cloudy"), ("비가 와요", "raining"), ("눈이 와요", "snowing"), ("바람이 불어요", "windy"), ("따뜻해요", "warm"), ("더워요", "hot"), ("시원해요", "cool"), ("쌀쌀해요", "chilly"), ("추워요", "cold"), ("봄", "spring"), ("여름", "summer"), ("가을", "autumn"), ("겨울", "winter"), ("날씨", "weather")],
    9: [("공원", "park"), ("도서관", "library"), ("식당", "restaurant"), ("집", "home"), ("헬스장", "gym"), ("백화점", "department store"), ("산책했어요", "took a walk"), ("공부했어요", "studied"), ("밥을 먹었어요", "ate a meal"), ("영화를 봤어요", "watched a movie"), ("운동했어요", "exercised"), ("쇼핑했어요", "went shopping"), ("어제", "yesterday"), ("주말", "weekend")],
    10: [("같이", "together"), ("주말", "weekend"), ("놀이공원", "amusement park"), ("영화관", "cinema"), ("식당", "restaurant"), ("공원", "park"), ("카페", "cafe"), ("도서관", "library"), ("갈까요?", "shall we go?"), ("만날까요?", "shall we meet?"), ("영화를 보러 가요", "go to watch a movie"), ("밥을 먹으러 가요", "go to eat"), ("산책하러 가요", "go for a walk"), ("커피를 마시러 가요", "go to drink coffee"), ("공부하러 가요", "go to study")],
}

VOCABULARY_EXAMPLES = {
    1: {
        "이름": "제 이름은 안나예요.",
        "나라": "어느 나라 사람이에요?",
        "한국": "저는 한국 사람이에요.",
        "캐나다": "마이클은 캐나다 사람이에요.",
        "베트남": "투이는 베트남 사람이에요.",
        "미국": "마이클은 미국 사람이에요.",
        "프랑스": "마리는 프랑스 사람이에요.",
        "태국": "나리는 태국 사람이에요.",
        "인도네시아": "디아는 인도네시아 사람이에요.",
        "중국": "리 씨는 중국 사람이에요.",
        "일본": "사토 씨는 일본 사람이에요.",
        "러시아": "안톤은 러시아 사람이에요.",
        "케냐": "조이는 케냐 사람이에요.",
        "학생": "저는 학생이에요.",
        "대학생": "제 친구는 대학생이에요.",
        "의사": "수진 씨는 의사예요.",
        "경찰": "민수 씨는 경찰이에요.",
        "선생님": "저는 선생님이에요.",
        "회사원": "저는 회사원이에요.",
        "가수": "유나 씨는 가수예요.",
        "요리사": "웨이 씨는 요리사예요.",
    },
    3: {
        "책": "책이 책상 위에 있어요.",
        "책상": "책상이 교실에 있어요.",
        "의자": "의자가 책상 옆에 있어요.",
        "가방": "가방이 의자 아래에 있어요.",
        "필통": "필통이 책 옆에 있어요.",
        "시계": "시계가 교실 벽에 있어요.",
        "위": "책이 책상 위에 있어요.",
        "아래": "가방이 의자 아래에 있어요.",
        "앞": "의자가 책상 앞에 있어요.",
        "뒤": "우산이 문 뒤에 있어요.",
        "옆": "필통이 책 옆에 있어요.",
        "오른쪽": "시계가 칠판 오른쪽에 있어요.",
        "왼쪽": "문이 칠판 왼쪽에 있어요.",
        "사이": "의자가 책상과 문 사이에 있어요.",
        "안": "연필이 필통 안에 있어요.",
        "밖": "가방이 교실 밖에 있어요.",
    },
    4: {
        "먹어요": "저는 아침을 먹어요.",
        "읽어요": "마리는 책을 읽어요.",
        "봐요": "주노는 영화를 봐요.",
        "마셔요": "저는 물을 마셔요.",
        "들어요": "유진은 음악을 들어요.",
        "만나요": "오늘 친구를 만나요.",
        "자요": "밤에 자요.",
        "일해요": "회사에서 일해요.",
        "요리해요": "집에서 불고기를 요리해요.",
        "공부해요": "세종학당에서 한국어를 공부해요.",
    },
    5: {
        "학교": "학교에 가요.", "회사": "회사에 가요.", "식당": "식당에 가요.",
        "카페": "카페에 가요.", "공원": "공원에 가요.", "마트": "마트에 가요.",
        "빵": "빵을 사요.", "라면": "라면을 먹어요.", "과일": "과일을 사요.",
        "커피": "커피를 마셔요.", "차": "차를 마셔요.", "우유": "우유를 마셔요.",
        "과자": "과자를 사요.", "아이스크림": "아이스크림을 먹어요.",
    },
    7: {
        "월요일": "월요일에 한국어 수업이 있어요.", "화요일": "화요일에 친구를 만나요.", "수요일": "수요일에 요리해요.",
        "목요일": "목요일에 세종학당에 가요.", "금요일": "금요일에 영화를 봐요.", "토요일": "토요일에 쉬어요.",
        "일요일": "일요일에 가족을 만나요.", "오늘": "오늘은 수요일이에요.", "내일": "내일은 목요일이에요.",
        "시": "수업은 일곱 시에 시작해요.", "분": "수업은 십 분 후에 시작해요.", "시작하다": "수업이 아홉 시에 시작해요.",
        "만나다": "주말에 친구를 만나요.", "수업": "한국어 수업이 있어요.",
    },
    8: {
        "맑아요": "오늘은 날씨가 맑아요.", "흐려요": "하늘이 흐려요.", "비가 와요": "오늘 비가 와요.", "눈이 와요": "겨울에 눈이 와요.",
        "따뜻해요": "봄 날씨가 따뜻해요.", "더워요": "여름 날씨가 더워요.", "시원해요": "가을 날씨가 시원해요.", "쌀쌀해요": "오늘은 날씨가 쌀쌀해요.", "추워요": "겨울 날씨가 추워요.",
        "바람이 불어요": "오늘은 바람이 불어요.", "봄": "봄은 따뜻해요.", "여름": "여름은 더워요.", "가을": "가을은 시원해요.", "겨울": "겨울은 추워요.", "날씨": "오늘 날씨가 좋아요.",
    },
    9: {
        "공원": "어제 공원에서 산책했어요.", "도서관": "어제 도서관에서 공부했어요.",
        "식당": "식당에서 밥을 먹었어요.", "집": "집에서 영화를 봤어요.",
        "헬스장": "헬스장에서 운동했어요.", "백화점": "백화점에서 쇼핑했어요.",
        "산책했어요": "어제 공원에서 산책했어요.", "공부했어요": "어제 도서관에서 공부했어요.",
        "밥을 먹었어요": "어제 식당에서 밥을 먹었어요.", "영화를 봤어요": "어제 집에서 영화를 봤어요.",
        "운동했어요": "어제 헬스장에서 운동했어요.", "쇼핑했어요": "어제 백화점에서 쇼핑했어요.",
        "어제": "어제 공원에서 산책했어요.", "주말": "주말에 집에서 영화를 봤어요.",
    },
    10: {
        "같이": "우리 주말에 같이 만날까요?", "주말": "주말에 놀이공원에 갈까요?",
        "놀이공원": "같이 놀이공원에 갈까요?", "영화관": "영화관에 영화를 보러 가요.",
        "식당": "식당에 밥을 먹으러 가요.", "공원": "공원에 산책하러 가요.",
        "카페": "카페에 커피를 마시러 가요.", "도서관": "도서관에 공부하러 가요.",
        "갈까요?": "우리 같이 갈까요?", "만날까요?": "토요일에 만날까요?",
        "영화를 보러 가요": "친구와 영화를 보러 가요.", "밥을 먹으러 가요": "점심을 먹으러 식당에 가요.",
        "산책하러 가요": "공원에 산책하러 가요.", "커피를 마시러 가요": "카페에 커피를 마시러 가요.",
        "공부하러 가요": "도서관에 공부하러 가요.",
    },
    6: {
        "하나": "사과 한 개 주세요.", "둘": "지우개 두 개 주세요.",
        "셋": "연필 세 자루 주세요.", "넷": "책 네 권이 있어요.",
        "다섯": "사과 다섯 개 주세요.", "여섯": "연필이 여섯 자루 있어요.",
        "일곱": "학생이 일곱 명 있어요.", "여덟": "고양이가 여덟 마리 있어요.",
        "아홉": "물 아홉 병 주세요.", "열": "달걀 열 개 주세요.",
        "스물": "학생이 스무 명 있어요.", "서른": "책이 서른 권 있어요.",
        "마흔": "사과가 마흔 개 있어요.", "쉰": "연필이 쉰 자루 있어요.",
        "예순": "의자가 예순 개 있어요.", "일흔": "학생이 일흔 명 있어요.",
        "여든": "책이 여든 권 있어요.", "아흔": "병이 아흔 개 있어요.",
        "백": "사과가 백 개 있어요.",
    },
}

LESSON_SECTION_CONTENT = {
    1: {"grammar1": "이에요/예요", "grammar2": "은/는", "example": "저는 안나예요. 저는 학생이에요.", "activity1": "이름·나라·직업 카드로 자기소개하기", "activity2": "나의 이름·나라·직업을 넣어 30초 자기소개하기"},
    2: {"grammar1": "이/가", "grammar2": "이/가 아니에요", "example": "전화번호가 뭐예요?", "activity1": "전화번호부에서 번호를 확인하고 대화 완성하기", "activity2": "연습용 이름·전화번호·이메일 입력하기"},
    3: {"grammar1": "이/그/저", "grammar2": "에 있다/없다", "example": "이 책은 책상 위에 있어요.", "activity1": "교실 그림에서 물건 위치 찾기", "activity2": "물건의 주인과 위치 확인하기"},
    4: {"grammar1": "-아요/어요", "grammar2": "을/를", "example": "저는 한국어를 공부해요.", "activity1": "오늘 하는 일을 선택해 대화 완성하기", "activity2": "문자 메시지를 읽고 오늘 한 일 쓰기"},
    5: {"grammar1": "에 가다", "grammar2": "하고", "example": "마트에 가요. 빵하고 우유를 사요.", "activity1": "마트에서 사는 물건 말하기", "activity2": "백화점에서 사는 물건 쓰기"},
    6: {"grammar1": "단위 명사", "grammar2": "-(으)세요", "example": "사과 다섯 개 주세요.", "activity1": "과일 가게에서 수량과 가격 확인하기", "activity2": "편의점에서 살 물건과 수량 쓰기"},
    7: {"grammar1": "에", "grammar2": "몇 시예요?", "example": "수업은 일곱 시에 시작해요.", "activity1": "날짜와 요일 확인하기", "activity2": "나의 하루 시간표 말하기"},
    8: {"grammar1": "안", "grammar2": "ㅂ 불규칙", "example": "오늘은 안 더워요. 겨울은 추워요.", "activity1": "도시와 날씨를 선택해 대화 완성하기", "activity2": "고향의 계절과 날씨 문장 완성하기"},
    9: {"grammar1": "에서", "grammar2": "-았어요/-었어요", "example": "공원에서 산책했어요.", "activity1": "장소와 과거 활동 연결하기", "activity2": "어제 한 일을 세 문장으로 회상하기"},
    10: {"grammar1": "-(으)ㄹ까요?", "grammar2": "-(으)러 가다", "example": "우리 같이 갈까요?", "activity1": "주말 계획 카드로 제안 만들기", "activity2": "장소와 시간을 선택해 약속 대화 완성하기"},
}

GRAMMAR_RULES = {
    "이에요/예요": "명사 뒤에 붙여 ‘무엇이다’라고 말해요. 단어 전체가 아니라 마지막 글자를 봐요. 마지막 글자에 받침이 있으면 ‘이에요’, 없으면 ‘예요’를 사용해요.",
    "은/는": "말할 대상을 소개하거나 비교할 때 사용해요. 단어의 마지막 글자에 받침이 있으면 ‘은’, 없으면 ‘는’을 사용해요.",
    "이/가": "문장에서 주어를 나타내요. ‘전화번호가 뭐예요?’처럼 새 정보를 물을 때 자주 사용해요.",
    "이/가 아니에요": "사람이나 사물이 아니라고 정정할 때 사용해요. 앞 명사에 받침이 있으면 ‘이 아니에요’, 없으면 ‘가 아니에요’를 사용해요.",
    "이/그/저": "‘이’는 말하는 사람에게 가까이 있는 사람이나 물건, ‘그’는 듣는 사람에게 가까이 있는 사람이나 물건, ‘저’는 두 사람 모두에게서 멀리 있는 사람이나 물건을 가리킬 때 사용해요.",
    "에 있다/없다": "사물이나 사람이 있는 장소를 말할 때 장소 뒤에 ‘에’를 사용해요.",
    "을/를": "동작의 대상을 나타내요. 받침이 있으면 ‘을’, 받침이 없으면 ‘를’을 사용해요.",
    "-아요/어요": "동사를 해요체로 말할 때 사용해요. 어간의 마지막 모음이 ‘아’ 또는 ‘오’ 계열이면 ‘-아요’, 그 밖에는 ‘-어요’를 붙이고, ‘하다’는 ‘해요’가 돼요.",
    "에 가다": "이동하는 목적지 뒤에 ‘에’를 붙여 ‘어디에 가요?’라고 묻고 ‘장소에 가요’라고 대답해요.",
    "에": "시간이나 날짜 뒤에 ‘에’를 붙여 언제 하는 일인지 말해요. 예: 수요일에 수업이 있어요.",
    "몇 시예요?": "현재 시각이나 일정 시간을 물을 때 사용해요. ‘몇 시예요?’라고 묻고 ‘일곱 시예요’처럼 대답해요.",
    "하고": "두 개 이상의 명사나 사람을 나란히 연결할 때 사용해요. 예: 빵하고 우유를 사요.",
    "단위 명사": "사람이나 물건의 수를 셀 때 알맞은 단위 명사를 사용해요. 물건은 ‘개’, 사람은 ‘명’, 동물은 ‘마리’, 음료는 ‘잔·병’, 책은 ‘권’을 사용해요.",
    "안": "동사나 형용사 앞에 ‘안’을 놓아 하지 않거나 그렇지 않다는 뜻을 나타내요. 예: 오늘은 안 더워요.",
    "ㅂ 불규칙": "일부 형용사의 어간 끝 ‘ㅂ’은 모음으로 시작하는 어미 앞에서 ‘우’로 바뀌어요. 예: 춥다 → 추워요, 덥다 → 더워요.",
    "에서": "행동이 일어난 장소 뒤에 붙여요. ‘공원에서 산책했어요’처럼 장소와 행동을 연결해요.",
    "-았어요/-었어요": "이미 끝난 일을 말할 때 사용하는 과거 표현이에요. ‘산책해요’는 ‘산책했어요’, ‘봐요’는 ‘봤어요’가 돼요.",
    "-(으)ㄹ까요?": "상대방에게 함께할 일을 제안하거나 의견을 물을 때 사용해요. 받침이 없으면 ‘-ㄹ까요?’, 받침이 있으면 ‘-을까요?’를 붙여요.",
    "-(으)러 가다": "어떤 행동을 하려는 목적을 말할 때 사용해요. 받침이 없거나 ㄹ 받침이면 ‘-러’, 그 밖의 받침이 있으면 ‘-으러’를 붙이고 ‘가요/와요’와 함께 써요.",
    "-(으)세요": "상대방에게 공손하게 어떤 행동을 요청하거나 명령할 때 사용해요. 동사 어간에 받침이 있으면 ‘-으세요’, 받침이 없으면 ‘-세요’를 붙여요.",
}

UNIT_GOALS_EN = {
    1: "Greet someone and introduce your name, nationality, and occupation.",
    2: "Ask for and give names, phone numbers, and contact information.",
    3: "Ask and answer where people and objects are.",
    4: "Talk about everyday actions and what you do today.",
    5: "Talk about places you go and things you like or buy.",
    6: "Count people and objects and make polite requests.",
    7: "Ask and answer about days and times.",
    8: "Describe weather and conditions.",
    9: "Talk about places and activities in the past.",
    10: "Make suggestions and arrange plans together.",
}

GRAMMAR_RULES_EN = {
    "이에요/예요": "Use 이에요 after a noun ending in a consonant and 예요 after a noun ending in a vowel.",
    "은/는": "Use 은/는 after a noun to introduce or contrast the topic.",
    "이/가": "Use 이/가 to mark the subject, especially when asking about or presenting new information.",
    "이/가 아니에요": "Use 이/가 아니에요 to say that someone or something is not the stated noun.",
    "이/그/저": "Use 이 near the speaker, 그 near the listener, and 저 for something far from both.",
    "에 있다/없다": "Use 에 with 있어요/없어요 to say where a person or object is or is not.",
    "을/를": "Use 을/를 after the object of an action.",
    "-아요/어요": "Use -아요/어요 to make a polite present-tense form.",
    "에 가다": "Use 에 after a destination with 가요.",
    "에": "Use 에 after a day, date, or time to say when something happens.",
    "몇 시예요?": "Use 몇 시예요? to ask the time.",
    "하고": "Use 하고 between nouns to mean ‘and’.",
    "단위 명사": "Use the appropriate counter when counting people, objects, animals, drinks, or books.",
    "안": "Place 안 before a verb or adjective to make a negative statement.",
    "ㅂ 불규칙": "Some stems ending in ㅂ change ㅂ to 우/오 before a vowel.",
    "에서": "Use 에서 after the place where an action happens.",
    "-았어요/-었어요": "Use -았어요/-었어요 to talk about a completed past action.",
    "-(으)ㄹ까요?": "Use -(으)ㄹ까요? to suggest doing something together or ask for an opinion.",
    "-(으)러 가다": "Use -(으)러 가요 to express the purpose of going somewhere.",
    "-(으)세요": "Use -(으)세요 to make a polite request or instruction.",
}

GRAMMAR_HIGHLIGHT_PATTERNS = {
    1: (r"이에요|예요", r"(?<=[가-힣A-Za-z])(?:은|는)(?=\s|[.,?!]|$)"),
    2: (r"(?<=[가-힣A-Za-z0-9])(?:이|가)(?=\s|[.,?!]|$)", r"(?:이|가) 아니에요|아니에요"),
    3: (r"(?<![가-힣])(?:이|그|저)(?=\s)", r"에 (?:있어요|없어요)|있어요|없어요"),
    4: (r"(?:아|어|해)요", r"(?<=[가-힣A-Za-z])(?:을|를)(?=\s|[.,?!]|$)"),
    5: (r"에 가요", r"하고"),
    6: (r"(?<=[가-힣0-9\s])(?:개|명|마리|잔|병|권)(?=\s|[.,?!]|$)", r"(?:으세요|세요|주세요)"),
    7: (r"(?<=[가-힣0-9])에(?=\s|[.,?!]|$)", r"몇 시|시예요"),
    8: (r"(?<![가-힣])안(?=\s)", r"(?:추워요|더워요|어려워요|매워요|무거워요|가벼워요)"),
    9: (r"에서", r"(?:았어요|었어요|했어요|봤어요)"),
    10: (r"(?:을까요|ㄹ까요)", r"(?:으러|러) 가요"),
}


UNIT2_NON_PARTICLE_WORD_ENDINGS = ("같이", "없이", "사이", "하와이", "웨이")
UNIT2_META_SUBJECTS = {
    "받침이",
    "모음이",
    "문법이",
    "조사가",
    "표현이",
    "설명이",
    "정답이",
    "선택이",
    "학습이",
}


def is_grammar_highlight_candidate(source, unit_number, match):
    """Reject syllables that resemble grammar forms but are part of ordinary words."""
    if unit_number != 2 or match.lastgroup != "grammar1":
        return True

    word_match = re.search(r"[가-힣]+$", source[:match.end()])
    if not word_match:
        return True
    word = word_match.group(0)
    if word in UNIT2_META_SUBJECTS:
        return False
    if word.endswith(UNIT2_NON_PARTICLE_WORD_ENDINGS):
        return False
    if word in {"십이", "십이십이"}:
        return False
    return True


def highlight_learning_text(text, unit_number, escape=True):
    """Highlight only the target grammar forms, never the whole sentence."""
    source = html.escape(str(text)) if escape else str(text)
    grammar1_pattern, grammar2_pattern = GRAMMAR_HIGHLIGHT_PATTERNS.get(unit_number, (r"(?!x)x", r"(?!x)x"))
    combined = re.compile(f"(?P<grammar2>{grammar2_pattern})|(?P<grammar1>{grammar1_pattern})")

    def replace(match):
        if not is_grammar_highlight_candidate(source, unit_number, match):
            return match.group(0)
        css_class = "grammar-two" if match.lastgroup == "grammar2" else "grammar-one"
        return f'<span class="{css_class}">{match.group(0)}</span>'

    return combined.sub(replace, source)


def highlight_learning_markdown(text, unit_number):
    """Apply the same grammar colors using Streamlit's Markdown color syntax."""
    source = str(text)
    protected_colors = []

    def protect_color(match):
        protected_colors.append(match.group(0))
        return f"@@EXISTING_COLOR_{len(protected_colors) - 1}@@"

    # Preserve dialogue-speaker colors and any intentional emphasis already present.
    source = re.sub(r":[a-z]+\[[^\]]*\]", protect_color, source)
    grammar1_pattern, grammar2_pattern = GRAMMAR_HIGHLIGHT_PATTERNS.get(unit_number, (r"(?!x)x", r"(?!x)x"))
    combined = re.compile(f"(?P<grammar2>{grammar2_pattern})|(?P<grammar1>{grammar1_pattern})")

    def replace(match):
        if not is_grammar_highlight_candidate(source, unit_number, match):
            return match.group(0)
        color = "blue" if match.lastgroup == "grammar2" else "orange"
        return f":{color}[**{match.group(0)}**]"

    source = combined.sub(replace, source)
    for index, colored_text in enumerate(protected_colors):
        source = source.replace(f"@@EXISTING_COLOR_{index}@@", colored_text)
    return source


def _current_learning_unit():
    return st.session_state.get("selected_unit_number", 1)


def english_support_enabled():
    return st.session_state.get("app_language", "한국어") == "English"


INTERFACE_TRANSLATIONS = {
    "日本語": {
        "Choose": "選択してください", "Check answers": "答えを確認", "Completed. Keep going!": "完了しました。続けましょう！",
        "Correct. Well done!": "正解です。よくできました！", "Read the Korean example sentences aloud": "韓国語の例文を声に出して読んでください",
        "Read the key Korean dialogues first.": "まず韓国語の重要な会話を読んでください。", "Key Korean dialogue and usage rules": "重要な韓国語会話と使い方のルール",
        "Look at the picture and choose the correct Korean expression": "絵を見て正しい韓国語表現を選んでください", "Complete each Korean dialogue and read it aloud": "韓国語の会話を完成させ、声に出して読んでください",
        "What is in the classroom?": "教室には何がありますか？", "Where is it?": "どこにありますか？", "Select each Korean classroom word and read its example sentence aloud.": "韓国語の教室の単語を選び、例文を声に出して読んでください。",
    },
    "中文": {
        "Choose": "请选择", "Check answers": "检查答案", "Completed. Keep going!": "完成了。继续加油！",
        "Correct. Well done!": "正确！做得很好！", "Read the Korean example sentences aloud": "请大声朗读韩语例句",
        "Read the key Korean dialogues first.": "请先朗读重点韩语对话。", "Key Korean dialogue and usage rules": "重点韩语对话和使用规则",
        "Look at the picture and choose the correct Korean expression": "看图选择正确的韩语表达", "Complete each Korean dialogue and read it aloud": "完成韩语对话并大声朗读",
        "What is in the classroom?": "教室里有什么？", "Where is it?": "在哪里？", "Select each Korean classroom word and read its example sentence aloud.": "选择每个韩语教室词语并大声朗读例句。",
    },
    "Tiếng Việt": {
        "Choose": "Hãy chọn", "Check answers": "Kiểm tra đáp án", "Completed. Keep going!": "Đã hoàn thành. Hãy tiếp tục!",
        "Correct. Well done!": "Chính xác. Làm tốt lắm!", "Read the Korean example sentences aloud": "Hãy đọc to các câu ví dụ tiếng Hàn",
        "Read the key Korean dialogues first.": "Trước tiên hãy đọc các đoạn hội thoại tiếng Hàn trọng tâm.", "Key Korean dialogue and usage rules": "Hội thoại tiếng Hàn trọng tâm và quy tắc sử dụng",
        "Look at the picture and choose the correct Korean expression": "Hãy xem tranh và chọn cách diễn đạt tiếng Hàn đúng", "Complete each Korean dialogue and read it aloud": "Hoàn thành hội thoại tiếng Hàn và đọc to",
        "What is in the classroom?": "Trong lớp học có gì?", "Where is it?": "Nó ở đâu?", "Select each Korean classroom word and read its example sentence aloud.": "Chọn từng từ tiếng Hàn về lớp học và đọc to câu ví dụ.",
    },
}

# Unit 4–6 guidance translations (Korean practice content remains unchanged).
INTERFACE_TRANSLATIONS["日本語"].update({
    "Key Korean dialogue and conjugation rules": "重要な韓国語会話と活用ルール", "What are you doing today?": "今日は何をしますか？",
    "Choose the matching Korean verb": "合う韓国語の動詞を選んでください", "Activity to practice": "練習する活動",
    "Activity 1 · The Market": "活動1 · 市場", "Activity 2 · Department Store Shopping": "活動2 · デパートで買い物",
    "Where are you going? What are you buying?": "どこへ行きますか？何を買いますか？", "Review Korean numbers and counters, then read them aloud": "韓国語の数字と助数詞を復習し、声に出して読んでください",
})
INTERFACE_TRANSLATIONS["中文"].update({
    "Key Korean dialogue and conjugation rules": "重点韩语对话和活用规则", "What are you doing today?": "今天做什么？",
    "Choose the matching Korean verb": "请选择相应的韩语动词", "Activity to practice": "练习活动",
    "Activity 1 · The Market": "活动1 · 市场", "Activity 2 · Department Store Shopping": "活动2 · 百货商店购物",
    "Where are you going? What are you buying?": "你要去哪里？买什么？", "Review Korean numbers and counters, then read them aloud": "复习韩语数字和量词，并大声朗读",
})
INTERFACE_TRANSLATIONS["Tiếng Việt"].update({
    "Key Korean dialogue and conjugation rules": "Hội thoại tiếng Hàn trọng tâm và quy tắc chia động từ", "What are you doing today?": "Hôm nay bạn làm gì?",
    "Choose the matching Korean verb": "Hãy chọn động từ tiếng Hàn phù hợp", "Activity to practice": "Hoạt động luyện tập",
    "Activity 1 · The Market": "Hoạt động 1 · Chợ", "Activity 2 · Department Store Shopping": "Hoạt động 2 · Mua sắm ở cửa hàng bách hóa",
    "Where are you going? What are you buying?": "Bạn đi đâu? Bạn mua gì?", "Review Korean numbers and counters, then read them aloud": "Ôn tập số và đơn vị đếm tiếng Hàn, sau đó đọc to",
})

# Additional learner-interface languages.
for _language, _translations in {
    "नेपाली": {"Choose": "छान्नुहोस्", "Check answers": "उत्तर जाँच्नुहोस्", "Completed. Keep going!": "पूरा भयो। अगाडि बढ्नुहोस्!", "Correct. Well done!": "सही छ। राम्रो गर्नुभयो!", "Read the Korean example sentences aloud": "कोरियाली उदाहरण वाक्यहरू ठूलो स्वरमा पढ्नुहोस्"},
    "Монгол": {"Choose": "Сонгоно уу", "Check answers": "Хариултыг шалгах", "Completed. Keep going!": "Дууслаа. Үргэлжлүүлээрэй!", "Correct. Well done!": "Зөв байна. Сайн ажиллалаа!", "Read the Korean example sentences aloud": "Солонгос жишээ өгүүлбэрүүдийг чангаар уншаарай"},
    "ไทย": {"Choose": "เลือก", "Check answers": "ตรวจคำตอบ", "Completed. Keep going!": "เสร็จแล้ว พยายามต่อไป!", "Correct. Well done!": "ถูกต้อง ทำได้ดีมาก!", "Read the Korean example sentences aloud": "อ่านประโยคตัวอย่างภาษาเกาหลีออกเสียง"},
    "O‘zbekcha": {"Choose": "Tanlang", "Check answers": "Javoblarni tekshirish", "Completed. Keep going!": "Tugallandi. Davom eting!", "Correct. Well done!": "To‘g‘ri. Juda yaxshi!", "Read the Korean example sentences aloud": "Koreyscha namunaviy gaplarni ovoz chiqarib o‘qing"},
    "မြန်မာ": {"Choose": "ရွေးပါ", "Check answers": "အဖြေများကို စစ်ဆေးပါ", "Completed. Keep going!": "ပြီးပါပြီ။ ဆက်လက်ကြိုးစားပါ!", "Correct. Well done!": "မှန်ပါတယ်။ ကောင်းပါတယ်!", "Read the Korean example sentences aloud": "ကိုရီးယား ဥပမာဝါကျများကို အသံထွက်ဖတ်ပါ"},
    "বাংলা": {"Choose": "নির্বাচন করুন", "Check answers": "উত্তর যাচাই করুন", "Completed. Keep going!": "সম্পন্ন হয়েছে। চালিয়ে যান!", "Correct. Well done!": "সঠিক। খুব ভালো!", "Read the Korean example sentences aloud": "কোরিয়ান উদাহরণ বাক্যগুলো জোরে পড়ুন"},
}.items():
    INTERFACE_TRANSLATIONS[_language] = _translations

_additional_guidance = {
    "Read the key Korean dialogues first.": {
        "नेपाली": "पहिले मुख्य कोरियाली संवादहरू पढ्नुहोस्।", "Монгол": "Эхлээд солонгос хэлний гол харилцан яриаг уншаарай.", "ไทย": "อ่านบทสนทนาภาษาเกาหลีที่สำคัญก่อน", "O‘zbekcha": "Avval asosiy koreyscha dialoglarni o‘qing.", "မြန်မာ": "အဓိက ကိုရီးယား စကားပြောများကို ဦးစွာဖတ်ပါ။", "বাংলা": "প্রথমে মূল কোরিয়ান সংলাপগুলো পড়ুন।",
    },
    "Choose answers": {
        "नेपाली": "उत्तरहरू छान्नुहोस्", "Монгол": "Хариултаа сонгоно уу", "ไทย": "เลือกคำตอบ", "O‘zbekcha": "Javoblarni tanlang", "မြန်မာ": "အဖြေများကို ရွေးပါ", "বাংলা": "উত্তর নির্বাচন করুন",
    },
    "Read the Korean dialogue aloud twice, then choose the answers.": {
        "नेपाली": "कोरियाली संवाद दुई पटक ठूलो स्वरमा पढेर उत्तर छान्नुहोस्।", "Монгол": "Солонгос харилцан яриаг хоёр удаа чангаар уншаад хариултаа сонгоно уу.", "ไทย": "อ่านบทสนทนาภาษาเกาหลีออกเสียงสองครั้ง แล้วเลือกคำตอบ", "O‘zbekcha": "Koreyscha dialogni ikki marta ovoz chiqarib o‘qing va javoblarni tanlang.", "မြန်မာ": "ကိုရီးယား စကားပြောကို နှစ်ကြိမ် အသံထွက်ဖတ်ပြီး အဖြေရွေးပါ။", "বাংলা": "কোরিয়ান সংলাপটি দুবার জোরে পড়ে উত্তর বেছে নিন।",
    },
    "Read the completed Korean dialogue aloud twice.": {
        "नेपाली": "पूरा भएको कोरियाली संवाद दुई पटक ठूलो स्वरमा पढ्नुहोस्।", "Монгол": "Бүрэн болсон солонгос харилцан яриаг хоёр удаа чангаар уншаарай.", "ไทย": "อ่านบทสนทนาภาษาเกาหลีที่เสร็จแล้วออกเสียงสองครั้ง", "O‘zbekcha": "Tugallangan koreyscha dialogni ikki marta ovoz chiqarib o‘qing.", "မြန်မာ": "ပြီးစီးသော ကိုရီးယား စကားပြောကို နှစ်ကြိမ် အသံထွက်ဖတ်ပါ။", "বাংলা": "সম্পূর্ণ কোরিয়ান সংলাপটি দুবার জোরে পড়ুন।",
    },
    "Choose the correct Korean sentence": {
        "नेपाली": "सही कोरियाली वाक्य छान्नुहोस्", "Монгол": "Зөв солонгос өгүүлбэрийг сонгоно уу", "ไทย": "เลือกประโยคภาษาเกาหลีที่ถูกต้อง", "O‘zbekcha": "To‘g‘ri koreyscha gapni tanlang", "မြန်မာ": "မှန်ကန်သော ကိုရီးယား ဝါကျကို ရွေးပါ", "বাংলা": "সঠিক কোরিয়ান বাক্যটি বেছে নিন",
    },
    "Read the Korean example sentences aloud": {
        "नेपाली": "कोरियाली उदाहरण वाक्यहरू ठूलो स्वरमा पढ्नुहोस्", "Монгол": "Солонгос жишээ өгүүлбэрүүдийг чангаар уншаарай", "ไทย": "อ่านประโยคตัวอย่างภาษาเกาหลีออกเสียง", "O‘zbekcha": "Koreyscha namunaviy gaplarni ovoz chiqarib o‘qing", "မြန်မာ": "ကိုရီးယား ဥပမာဝါကျများကို အသံထွက်ဖတ်ပါ", "বাংলা": "কোরিয়ান উদাহরণ বাক্যগুলো জোরে পড়ুন",
    },
    "Look at the picture and choose the correct Korean expression": {
        "नेपाली": "चित्र हेरेर सही कोरियाली अभिव्यक्ति छान्नुहोस्", "Монгол": "Зургийг хараад зөв солонгос хэллэгийг сонгоно уу", "ไทย": "ดูภาพแล้วเลือกสำนวนภาษาเกาหลีที่ถูกต้อง", "O‘zbekcha": "Rasmga qarab to‘g‘ri koreyscha iborani tanlang", "မြန်မာ": "ပုံကိုကြည့်ပြီး မှန်ကန်သော ကိုရီးယား စကားရပ်ကို ရွေးပါ", "বাংলা": "ছবিটি দেখে সঠিক কোরিয়ান অভিব্যক্তি বেছে নিন",
    },
}
for _english_key, _language_values in _additional_guidance.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

_core_guidance = {
    "### 1. Read each place’s phone number and answer the question": {
        "日本語": "### 1. それぞれの場所の電話番号を読み、質問に答えてください", "中文": "### 1. 朗读各地点的电话号码并回答问题", "Tiếng Việt": "### 1. Đọc số điện thoại của từng địa điểm và trả lời câu hỏi",
    },
    "Check the place name and phone number on each sign, then choose the correct number.": {
        "日本語": "看板の場所名と電話番号を確認し、正しい番号を選んでください。", "中文": "请确认每个牌子上的地点名称和电话号码，然后选择正确的号码。", "Tiếng Việt": "Hãy kiểm tra tên địa điểm và số điện thoại trên từng biển, rồi chọn số đúng.",
    },
    "Check Activity 1 answers": {"日本語": "活動1の答えを確認", "中文": "检查活动1答案", "Tiếng Việt": "Kiểm tra đáp án Hoạt động 1"},
    "Close feedback": {"日本語": "フィードバックを閉じる", "中文": "关闭反馈", "Tiếng Việt": "Đóng phản hồi"},
    "Read in Korean": {"日本語": "韓国語で読む", "中文": "用韩语朗读", "Tiếng Việt": "Đọc bằng tiếng Hàn"},
    "### 1. Check what is in the classroom": {"日本語": "### 1. 教室に何があるか確認してください", "中文": "### 1. 确认教室里有什么", "Tiếng Việt": "### 1. Hãy kiểm tra trong lớp học có gì"},
    "Find the people and objects in the picture, then choose the correct Korean answer.": {"日本語": "絵の中の人や物を探し、正しい韓国語の答えを選んでください。", "中文": "找出图片中的人物和物品，然后选择正确的韩语答案。", "Tiếng Việt": "Hãy tìm người và đồ vật trong tranh, rồi chọn câu trả lời tiếng Hàn đúng."},
    "### 2. Choose the sentence that matches each location": {"日本語": "### 2. それぞれの位置に合う文を選んでください", "中文": "### 2. 选择与各位置相符的句子", "Tiếng Việt": "### 2. Hãy chọn câu phù hợp với từng vị trí"},
    "### 1. Look at the picture and choose the correct Korean sentence": {"日本語": "### 1. 絵を見て正しい韓国語の文を選んでください", "中文": "### 1. 看图选择正确的韩语句子", "Tiếng Việt": "### 1. Hãy xem tranh và chọn câu tiếng Hàn đúng"},
    "Choose the sentence that uses 을/를 correctly for the pictured object.": {"日本語": "絵の物に合う 을/를 の正しい文を選んでください。", "中文": "请选择对图片物品正确使用 을/를 的句子。", "Tiếng Việt": "Hãy chọn câu sử dụng 을/를 đúng với đồ vật trong tranh."},
    "### 2. Look at the picture and complete each Korean dialogue": {"日本語": "### 2. 絵を見て韓国語の会話を完成してください", "中文": "### 2. 看图完成韩语对话", "Tiếng Việt": "### 2. Hãy xem tranh và hoàn thành hội thoại tiếng Hàn"},
    "Identify the object of the action and choose 을 or 를.": {"日本語": "動作の対象を確認し、을 または 를 を選んでください。", "中文": "确认动作的对象并选择 을 或 를。", "Tiếng Việt": "Hãy xác định đối tượng của hành động và chọn 을 hoặc 를."},
    "Key Korean dialogue and usage rules": {"日本語": "重要な韓国語会話と使い方のルール", "中文": "重点韩语对话和使用规则", "Tiếng Việt": "Hội thoại tiếng Hàn trọng tâm và quy tắc sử dụng"},
}
for _english_key, _language_values in _core_guidance.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

_lesson_path_guidance = {
    "Follow the five steps in order. Complete Vocabulary & expressions before moving to Grammar 1.": {
        "日本語": "5つのステップを順番に進めてください。語彙と表現を終えてから文法1へ進みます。", "中文": "请按顺序完成五个步骤。完成词汇和表达后再进入语法1。", "Tiếng Việt": "Hãy thực hiện lần lượt 5 bước. Hoàn thành Từ vựng & Biểu đạt trước khi chuyển sang Ngữ pháp 1。",
    },
    "Unit {number} · 5-step lesson": {
        "日本語": "ユニット {number} · 5ステップのレッスン", "中文": "第 {number} 单元 · 五步课程", "Tiếng Việt": "Bài {number} · Bài học 5 bước",
    },
    "Learn the key words": {"日本語": "重要な語彙を学ぶ", "中文": "学习核心词汇", "Tiếng Việt": "Học từ vựng trọng tâm"},
    "Use the forms in context": {"日本語": "文脈で形を使う", "中文": "在语境中使用形式", "Tiếng Việt": "Sử dụng mẫu câu trong ngữ cảnh"},
    "Create your own response": {"日本語": "自分の答えを作る", "中文": "创建自己的回答", "Tiếng Việt": "Tạo câu trả lời của bạn"},
    "Vocabulary & expressions": {"日本語": "語彙と表現", "中文": "词汇和表达", "Tiếng Việt": "Từ vựng & Biểu đạt"},
    "Grammar 1": {"日本語": "文法1", "中文": "语法1", "Tiếng Việt": "Ngữ pháp 1"},
    "Grammar 2": {"日本語": "文法2", "中文": "语法2", "Tiếng Việt": "Ngữ pháp 2"},
    "Activity 1": {"日本語": "活動1", "中文": "活动1", "Tiếng Việt": "Hoạt động 1"},
    "Activity 2": {"日本語": "活動2", "中文": "活动2", "Tiếng Việt": "Hoạt động 2"},
}
for _english_key, _language_values in _lesson_path_guidance.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

_units46_guidance = {
    "### 1. What are you doing today?": {"日本語": "### 1. 今日は何をしますか？", "中文": "### 1. 今天做什么？", "Tiếng Việt": "### 1. Hôm nay bạn làm gì?"},
    "Look at the picture, select each Korean verb, and read its example sentence aloud.": {"日本語": "絵を見て韓国語の動詞を一つずつ選び、例文を声に出して読んでください。", "中文": "看图逐个选择韩语动词，并大声朗读例句。", "Tiếng Việt": "Hãy xem tranh, chọn từng động từ tiếng Hàn và đọc to câu ví dụ."},
    "### 3. Look at each picture and answer 무엇을 해요?": {"日本語": "### 3. 絵を見て 무엇을 해요? に答えてください", "中文": "### 3. 看图回答 무엇을 해요?", "Tiếng Việt": "### 3. Hãy xem tranh và trả lời 무엇을 해요?"},
    "Identify each action and choose the correct Korean sentence.": {"日本語": "それぞれの動作を確認し、正しい韓国語の文を選んでください。", "中文": "确认每个动作并选择正确的韩语句子。", "Tiếng Việt": "Hãy xác định từng hành động và chọn câu tiếng Hàn đúng."},
    "### 1. Look at each picture and complete the Korean dialogue": {"日本語": "### 1. それぞれの絵を見て韓国語の会話を完成してください", "中文": "### 1. 看图完成韩语对话", "Tiếng Việt": "### 1. Hãy xem từng tranh và hoàn thành hội thoại tiếng Hàn"},
    "Choose the correct -아요/어요 form for the action in each picture.": {"日本語": "各絵の動作に合う正しい -아요/어요 形を選んでください。", "中文": "请选择每幅图中动作正确的 -아요/어요 形式。", "Tiếng Việt": "Hãy chọn dạng -아요/어요 đúng cho hành động trong mỗi tranh."},
    "### 2. Choose an activity, complete the Korean dialogue, and read it aloud": {"日本語": "### 2. 活動を選び、韓国語の会話を完成して声に出して読んでください", "中文": "### 2. 选择活动，完成韩语对话并大声朗读", "Tiếng Việt": "### 2. Hãy chọn hoạt động, hoàn thành hội thoại tiếng Hàn và đọc to"},
    "### 1. Read the Korean dialogue and find what they are discussing": {"日本語": "### 1. 韓国語の会話を読み、何について話しているか確認してください", "中文": "### 1. 阅读韩语对话并找出他们讨论的内容", "Tiếng Việt": "### 1. Đọc hội thoại tiếng Hàn và tìm xem họ đang nói về điều gì"},
    "### 1. Read the Korean shopping dialogue and identify each purchase": {"日本語": "### 1. 韓国語の買い物会話を読み、それぞれの購入品を確認してください", "中文": "### 1. 阅读韩语购物对话并确认各自购买的物品", "Tiếng Việt": "### 1. Đọc hội thoại mua sắm tiếng Hàn và xác định từng món đồ mua"},
    "### 2. You are going to a department store. What will you buy?": {"日本語": "### 2. デパートへ行きます。何を買いますか？", "中文": "### 2. 你要去百货商店。你要买什么？", "Tiếng Việt": "### 2. Bạn sẽ đi cửa hàng bách hóa. Bạn sẽ mua gì?"},
}
for _english_key, _language_values in _units46_guidance.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

_units710_guidance = {
    "### 1. Review the Korean dates and weekdays, then read them aloud": {"日本語": "### 1. 韓国語の日付と曜日を復習し、声に出して読んでください", "中文": "### 1. 复习韩语日期和星期，并大声朗读", "Tiếng Việt": "### 1. Ôn ngày tháng và thứ bằng tiếng Hàn, sau đó đọc to"},
    "### 2. Choose the correct Korean weekday for each blank": {"日本語": "### 2. 空欄に入る正しい韓国語の曜日を選んでください", "中文": "### 2. 请选择每个空格中正确的韩语星期", "Tiếng Việt": "### 2. Hãy chọn thứ tiếng Hàn đúng cho mỗi chỗ trống"},
    "### 1. Read the Korean weather dialogue and answer": {"日本語": "### 1. 韓国語の天気会話を読み、質問に答えてください", "中文": "### 1. 阅读韩语天气对话并回答问题", "Tiếng Việt": "### 1. Đọc hội thoại thời tiết tiếng Hàn và trả lời câu hỏi"},
    "### 2. Choose a region and weather, then read the Korean dialogue aloud": {"日本語": "### 2. 地域と天気を選び、韓国語の会話を声に出して読んでください", "中文": "### 2. 选择地区和天气，并大声朗读韩语对话", "Tiếng Việt": "### 2. Hãy chọn khu vực và thời tiết, rồi đọc to hội thoại tiếng Hàn"},
    "### 1. Read Suji’s Korean description of Jeju’s four seasons": {"日本語": "### 1. スジさんの済州の四季についての韓国語説明を読んでください", "中文": "### 1. 阅读秀智关于济州四季的韩语介绍", "Tiếng Việt": "### 1. Đọc phần giới thiệu tiếng Hàn của Suji về bốn mùa ở Jeju"},
    "### 1. Review the Korean places and past-activity expressions, then read them aloud": {"日本語": "### 1. 韓国語の場所と過去の活動表現を復習し、声に出して読んでください", "中文": "### 1. 复习韩语地点和过去活动表达，并大声朗读", "Tiếng Việt": "### 1. Ôn các địa điểm và cách diễn đạt hoạt động đã làm bằng tiếng Hàn, sau đó đọc to"},
    "### 2. Choose a place and activity, then read the Korean dialogue aloud": {"日本語": "### 2. 場所と活動を選び、韓国語の会話を声に出して読んでください", "中文": "### 2. 选择地点和活动，并大声朗读韩语对话", "Tiếng Việt": "### 2. Hãy chọn địa điểm và hoạt động, rồi đọc to hội thoại tiếng Hàn"},
    "### 1. Complete each Korean sentence with the correct past-tense form": {"日本語": "### 1. 正しい過去形を選び、韓国語の文を完成してください", "中文": "### 1. 选择正确的过去时形式完成韩语句子", "Tiếng Việt": "### 1. Hãy chọn dạng quá khứ đúng để hoàn thành câu tiếng Hàn"},
    "### 2. Choose a present expression, change it to the past, and read it aloud": {"日本語": "### 2. 現在の表現を選び、過去形に変えて声に出して読んでください", "中文": "### 2. 选择现在时表达，改成过去时并大声朗读", "Tiếng Việt": "### 2. Hãy chọn cách diễn đạt hiện tại, đổi sang quá khứ và đọc to"},
    "### 2. Choose a weekday, times, activities, and feeling to complete your Korean weekend record": {"日本語": "### 2. 曜日・時間・活動・気持ちを選び、韓国語の週末記録を完成してください", "中文": "### 2. 选择星期、时间、活动和感受，完成你的韩语周末记录", "Tiếng Việt": "### 2. Hãy chọn thứ, thời gian, hoạt động và cảm xúc để hoàn thành nhật ký cuối tuần bằng tiếng Hàn"},
}
for _english_key, _language_values in _units710_guidance.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

_remaining_guidance = {
    "### 1. Review the Korean dates and weekdays, then read them aloud": {
        "नेपाली": "### 1. कोरियाली मिति र बारहरू दोहोर्याएर ठूलो स्वरमा पढ्नुहोस्", "Монгол": "### 1. Солонгос огноо, гаригуудыг давтаад чангаар уншаарай", "ไทย": "### 1. ทบทวนวันที่และวันในสัปดาห์ภาษาเกาหลี แล้วอ่านออกเสียง", "O‘zbekcha": "### 1. Koreyscha sana va hafta kunlarini takrorlab, ovoz chiqarib o‘qing", "မြန်မာ": "### 1. ကိုရီးယား ရက်စွဲနှင့် အပတ်ရက်များကို ပြန်လည်လေ့လာပြီး အသံထွက်ဖတ်ပါ", "বাংলা": "### 1. কোরিয়ান তারিখ ও সপ্তাহের দিনগুলো অনুশীলন করে জোরে পড়ুন",
    },
    "### 1. Read the Korean weather dialogue and answer": {
        "नेपाली": "### 1. कोरियाली मौसम संवाद पढेर प्रश्नको उत्तर दिनुहोस्", "Монгол": "### 1. Солонгос цаг агаарын харилцан яриаг уншаад асуултад хариулаарай", "ไทย": "### 1. อ่านบทสนทนาสภาพอากาศภาษาเกาหลีแล้วตอบคำถาม", "O‘zbekcha": "### 1. Koreyscha ob-havo dialogini o‘qib, savollarga javob bering", "မြန်မာ": "### 1. ကိုရီးယား ရာသီဥတု စကားပြောကိုဖတ်ပြီး မေးခွန်းများဖြေပါ", "বাংলা": "### 1. কোরিয়ান আবহাওয়ার সংলাপ পড়ে প্রশ্নের উত্তর দিন",
    },
    "### 1. Review the Korean places and past-activity expressions, then read them aloud": {
        "नेपाली": "### 1. कोरियाली ठाउँ र विगतका गतिविधिका अभिव्यक्तिहरू दोहोर्याएर पढ्नुहोस्", "Монгол": "### 1. Солонгос газрын болон өнгөрсөн үйл ажиллагааны хэллэгийг давтаад чангаар уншаарай", "ไทย": "### 1. ทบทวนคำศัพท์สถานที่และกิจกรรมในอดีตภาษาเกาหลี แล้วอ่านออกเสียง", "O‘zbekcha": "### 1. Koreyscha joy va o‘tgan faoliyat iboralarini takrorlab, ovoz chiqarib o‘qing", "မြန်မာ": "### 1. ကိုရီးယား နေရာနှင့် အတိတ်လုပ်ဆောင်ချက် စကားရပ်များကို လေ့လာပြီး အသံထွက်ဖတ်ပါ", "বাংলা": "### 1. কোরিয়ান স্থান ও অতীত কার্যকলাপের অভিব্যক্তি অনুশীলন করে জোরে পড়ুন",
    },
    "### 1. Complete each Korean sentence with the correct past-tense form": {
        "नेपाली": "### 1. सही भूतकालको रूप छानेर प्रत्येक कोरियाली वाक्य पूरा गर्नुहोस्", "Монгол": "### 1. Зөв өнгөрсөн цагийн хэлбэрийг сонгон солонгос өгүүлбэрийг гүйцээгээрэй", "ไทย": "### 1. เลือกรูปอดีตกาลที่ถูกต้องเพื่อเติมประโยคภาษาเกาหลี", "O‘zbekcha": "### 1. To‘g‘ri o‘tgan zamon shaklini tanlab, koreyscha gaplarni to‘ldiring", "မြန်မာ": "### 1. မှန်ကန်သော အတိတ်ကာလပုံစံကို ရွေးပြီး ကိုရီးယား ဝါကျများကို ဖြည့်ပါ", "বাংলা": "### 1. সঠিক অতীতকালের রূপ বেছে নিয়ে কোরিয়ান বাক্য সম্পূর্ণ করুন",
    },
    "### 2. Choose a weekday, times, activities, and feeling to complete your Korean weekend record": {
        "नेपाली": "### 2. बार, समय, गतिविधि र भावना छानेर आफ्नो कोरियाली सप्ताहन्त रेकर्ड पूरा गर्नुहोस्", "Монгол": "### 2. Гариг, цаг, үйл ажиллагаа, мэдрэмжээ сонгон солонгос амралтын өдрийн тэмдэглэлээ гүйцээгээрэй", "ไทย": "### 2. เลือกวัน เวลา กิจกรรม และความรู้สึกเพื่อเติมบันทึกสุดสัปดาห์ภาษาเกาหลี", "O‘zbekcha": "### 2. Hafta kuni, vaqt, faoliyat va kayfiyatni tanlab koreyscha dam olish kunlari qaydingizni to‘ldiring", "မြန်မာ": "### 2. အပတ်ရက်၊ အချိန်၊ လုပ်ဆောင်ချက်နှင့် ခံစားချက်ကို ရွေးပြီး ကိုရီးယား အပတ်ကုန်မှတ်တမ်းကို ဖြည့်ပါ", "বাংলা": "### 2. সপ্তাহের দিন, সময়, কার্যকলাপ ও অনুভূতি বেছে নিয়ে কোরিয়ান উইকএন্ড রেকর্ড সম্পূর্ণ করুন",
    },
}
for _english_key, _language_values in _remaining_guidance.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

_stage_labels = {
    "Vocabulary & expressions": {"日本語": "語彙と表現", "中文": "词汇和表达", "Tiếng Việt": "Từ vựng & Biểu đạt", "नेपाली": "शब्दावली र अभिव्यक्ति", "Монгол": "Үг хэллэг", "ไทย": "คำศัพท์และสำนวน", "O‘zbekcha": "Lug‘at va iboralar", "မြန်မာ": "ဝေါဟာရနှင့် စကားရပ်များ", "বাংলা": "শব্দভাণ্ডার ও অভিব্যক্তি"},
    "Grammar 1": {"日本語": "文法1", "中文": "语法1", "Tiếng Việt": "Ngữ pháp 1", "नेपाली": "व्याकरण १", "Монгол": "Дүрэм 1", "ไทย": "ไวยากรณ์ 1", "O‘zbekcha": "Grammatika 1", "မြန်မာ": "သဒ္ဒါ ၁", "বাংলা": "ব্যাকরণ ১"},
    "Grammar 2": {"日本語": "文法2", "中文": "语法2", "Tiếng Việt": "Ngữ pháp 2", "नेपाली": "व्याकरण २", "Монгол": "Дүрэм 2", "ไทย": "ไวยากรณ์ 2", "O‘zbekcha": "Grammatika 2", "မြန်မာ": "သဒ္ဒါ ၂", "বাংলা": "ব্যাকরণ ২"},
    "Activity 1": {"日本語": "活動1", "中文": "活动1", "Tiếng Việt": "Hoạt động 1", "नेपाली": "गतिविधि १", "Монгол": "Үйл ажиллагаа 1", "ไทย": "กิจกรรม 1", "O‘zbekcha": "1-faoliyat", "မြန်မာ": "လုပ်ဆောင်ချက် ၁", "বাংলা": "কার্যকলাপ ১"},
    "Activity 2": {"日本語": "活動2", "中文": "活动2", "Tiếng Việt": "Hoạt động 2", "नेपाली": "गतिविधि २", "Монгол": "Үйл ажиллагаа 2", "ไทย": "กิจกรรม 2", "O‘zbekcha": "2-faoliyat", "မြန်မာ": "လုပ်ဆောင်ချက် ၂", "বাংলা": "কার্যকলাপ ২"},
    "Follow the five steps in order. Complete Vocabulary & expressions before moving to Grammar 1.": {"日本語": "5つのステップを順番に進めてください。語彙と表現を終えてから文法1へ進みます。", "中文": "请按顺序完成五个步骤。完成词汇和表达后再进入语法1。", "Tiếng Việt": "Hãy thực hiện lần lượt 5 bước. Hoàn thành Từ vựng & Biểu đạt trước khi chuyển sang Ngữ pháp 1.", "नेपाली": "पाँच चरण क्रमैसँग पूरा गर्नुहोस्। शब्दावली र अभिव्यक्ति सकेपछि व्याकरण १ मा जानुहोस्।", "Монгол": "Таван алхмыг дарааллаар нь хийгээрэй. Үг хэллэгийг дуусгасны дараа Дүрэм 1 рүү орно.", "ไทย": "ทำตามทั้งห้าขั้นตอนตามลำดับ เมื่อเสร็จคำศัพท์และสำนวนแล้วจึงไปไวยากรณ์ 1", "O‘zbekcha": "Besh bosqichni tartib bilan bajaring. Lug‘at va iboralarni tugatgach, Grammatika 1 ga o‘ting.", "မြန်မာ": "အဆင့်ငါးဆင့်ကို အစဉ်လိုက်လုပ်ပါ။ ဝေါဟာရနှင့် စကားရပ်များပြီးမှ သဒ္ဒါ ၁ သို့ သွားပါ။", "বাংলা": "পাঁচটি ধাপ ক্রমানুসারে করুন। শব্দভাণ্ডার ও অভিব্যক্তি শেষ করে ব্যাকরণ ১-এ যান।"},
}
for _english_key, _language_values in _stage_labels.items():
    for _language, _value in _language_values.items():
        INTERFACE_TRANSLATIONS.setdefault(_language, {})[_english_key] = _value

# Unit 7–10 guidance translations.
INTERFACE_TRANSLATIONS["日本語"].update({
    "Review the Korean dates and weekdays, then read them aloud": "韓国語の日付と曜日を復習し、声に出して読んでください",
    "Choose the correct Korean weekday for each blank": "空欄に入る正しい韓国語の曜日を選んでください", "Activity 1 · Weather in Seoul and Busan": "活動1 · ソウルと釜山の天気",
    "Activity 2 · Jeju’s Four Seasons": "活動2 · 済州の四季", "Activity 1 · Yesterday’s Activities": "活動1 · 昨日の活動",
    "Activity 2 · My weekend-plan card": "活動2 · 私の週末予定カード", "Activity 1 · Weekend suggestion": "活動1 · 週末の提案",
    "Read the weekend-plan dialogue and choose the correct answers.": "週末の予定の会話を読み、正しい答えを選んでください。",
})
INTERFACE_TRANSLATIONS["中文"].update({
    "Review the Korean dates and weekdays, then read them aloud": "复习韩语日期和星期，并大声朗读",
    "Choose the correct Korean weekday for each blank": "请选择每个空格中正确的韩语星期", "Activity 1 · Weather in Seoul and Busan": "活动1 · 首尔和釜山的天气",
    "Activity 2 · Jeju’s Four Seasons": "活动2 · 济州岛的四季", "Activity 1 · Yesterday’s Activities": "活动1 · 昨天做的事",
    "Activity 2 · My weekend-plan card": "活动2 · 我的周末计划卡", "Activity 1 · Weekend suggestion": "活动1 · 周末提议",
    "Read the weekend-plan dialogue and choose the correct answers.": "阅读周末计划对话并选择正确答案。",
})
INTERFACE_TRANSLATIONS["Tiếng Việt"].update({
    "Review the Korean dates and weekdays, then read them aloud": "Ôn ngày tháng và thứ bằng tiếng Hàn, sau đó đọc to",
    "Choose the correct Korean weekday for each blank": "Hãy chọn thứ tiếng Hàn đúng cho mỗi chỗ trống", "Activity 1 · Weather in Seoul and Busan": "Hoạt động 1 · Thời tiết ở Seoul và Busan",
    "Activity 2 · Jeju’s Four Seasons": "Hoạt động 2 · Bốn mùa ở Jeju", "Activity 1 · Yesterday’s Activities": "Hoạt động 1 · Hoạt động hôm qua",
    "Activity 2 · My weekend-plan card": "Hoạt động 2 · Thẻ kế hoạch cuối tuần của tôi", "Activity 1 · Weekend suggestion": "Hoạt động 1 · Đề xuất cuối tuần",
    "Read the weekend-plan dialogue and choose the correct answers.": "Đọc hội thoại về kế hoạch cuối tuần và chọn đáp án đúng.",
})


def interface_text(korean, english):
    """Translate interface guidance while keeping Korean learning content intact."""
    language = st.session_state.get("app_language", "한국어")
    if language == "한국어":
        return korean
    if language == "English":
        return english
    # Never expose an untranslated English fallback in a non-English learner UI.
    # Until a phrase is reviewed by a human translator, keep the Korean source
    # visible so learners are not given misleading or mixed-language guidance.
    return INTERFACE_TRANSLATIONS.get(language, {}).get(english, korean)


def add_english_support(body, message_type="info"):
    """Replace interface guidance with English while preserving Korean practice output."""
    text = str(body)
    language = st.session_state.get("app_language", "한국어")
    if language == "한국어":
        return text

    # English receives the adaptive guidance below. Other languages keep the
    # Korean source until a reviewed translation is available, avoiding an
    # unexpected English-only message inside a learner's chosen UI.
    if language != "English":
        return text

    for grammar, korean_rule in GRAMMAR_RULES.items():
        if text.strip() == korean_rule:
            return GRAMMAR_RULES_EN[grammar]

    if message_type == "success":
        if "정답" in text or "맞아요" in text:
            return "Correct. Well done!"
        if "완료" in text or "완성" in text or "제출" in text:
            return "Completed. Keep going!"
        return text
    elif message_type in {"warning", "error"}:
        if "정답" in text or "오답" in text or "아니에요" in text:
            return "Check the explanation and try again."
        return "Please review the required information and try again."
    elif "소리 내어" in text or "읽" in text:
        return "Read the Korean sentence aloud and focus on the highlighted form."
    elif "그림" in text:
        return "Look at the picture and use the Korean information to answer."
    elif "선택" in text or "고르" in text:
        return "Choose the answer that best completes the Korean sentence."
    elif "입력" in text or "작성" in text:
        return "Enter the requested information in Korean."
    elif "완료" in text:
        return "Complete the required learning steps to continue."
    return text


def render_learning_info(body, icon=None, **kwargs):
    supported = add_english_support(body, "info")
    st.info(highlight_learning_markdown(supported, _current_learning_unit()), icon=icon, **kwargs)


def render_learning_success(body, icon=None, **kwargs):
    supported = add_english_support(body, "success")
    st.success(highlight_learning_markdown(supported, _current_learning_unit()), icon=icon, **kwargs)


def render_learning_warning(body, icon=None, **kwargs):
    supported = add_english_support(body, "warning")
    st.warning(highlight_learning_markdown(supported, _current_learning_unit()), icon=icon, **kwargs)


def render_learning_error(body, icon=None, **kwargs):
    supported = add_english_support(body, "error")
    st.error(highlight_learning_markdown(supported, _current_learning_unit()), icon=icon, **kwargs)


def render_learning_markdown(body, **kwargs):
    st.markdown(highlight_learning_markdown(body, _current_learning_unit()), **kwargs)


st.set_page_config(
    page_title="모모의 한국어 강좌",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)


DB_PATH = Path(__file__).with_name("gravity_korean.db")


def subject_particle(value):
    """Return 이/가 for the final Korean syllable in a displayed choice."""
    for character in reversed(str(value).strip()):
        if "가" <= character <= "힣":
            has_final_consonant = (ord(character) - ord("가")) % 28 != 0
            return "이" if has_final_consonant else "가"
    return "가"


def object_particle(value):
    """Return 을/를 for the final Korean syllable in a displayed choice."""
    return "을" if subject_particle(value) == "이" else "를"


def check_grammar2_sentence(unit_number, sentence):
    """Check whether a learner-created sentence contains the unit's target form."""
    text = sentence.strip()
    checks = {
        1: (bool(re.search(r"[가-힣]+[은는](?:\s|$)", text)), "소개할 대상 뒤에 ‘은/는’을 넣어 보세요. 예: 저는 학생이에요."),
        2: (any(char.isdigit() for char in text) or any(word in text for word in ["일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]), "전화번호나 숫자를 넣어 문장을 만들어 보세요."),
        3: (("에" in text) and any(word in text for word in ["있어요", "없어요"]), "장소 뒤에 ‘에’를 쓰고 ‘있어요/없어요’로 끝내 보세요. 예: 책이 책상 위에 있어요."),
        4: (any(ending in text for ending in ["아요", "어요", "해요", "여요", "와요", "워요"]), "동작을 ‘-아요/어요’ 형태로 끝내 보세요. 예: 한국어를 공부해요."),
        5: ("하고" in text, "두 명사 사이에 ‘하고’를 넣어 보세요. 예: 빵하고 우유를 사요."),
        6: (text.endswith(("세요", "으세요")), "동사를 ‘-(으)세요’ 형태로 끝내 보세요. 예: 책을 읽으세요."),
        7: ("시" in text and ("예요" in text or "이에요" in text), "‘몇 시예요?’ 또는 시각을 묻고 답하는 문장을 만들어 보세요."),
        8: (any(word in text for word in ["더워", "추워", "따뜻", "시원", "맑", "흐리", "좋아", "나빠", "비가", "눈이"]), "날씨를 나타내는 형용사를 넣어 보세요. 예: 오늘은 날씨가 더워요."),
        9: (any(ending in text for ending in ["았어요", "었어요", "했어요", "봤어요", "갔어요", "왔어요"]), "지난 일을 ‘-았어요/-었어요’로 표현해 보세요."),
        10: (("러 " in text or "으러 " in text) and any(verb in text for verb in ["가요", "와요", "갑니다", "옵니다"]), "목적을 나타내는 ‘-(으)러’와 이동 동사 ‘가다/오다’를 함께 사용해 보세요."),
    }
    passed, hint = checks.get(unit_number, (len(text) >= 5, "조금 더 긴 문장으로 작성해 보세요."))
    if passed:
        success_feedback = {
            1: "‘은/는’을 사용해 소개할 대상을 주제로 잘 나타냈어요. ‘저는 / 학생은’처럼 조사 앞 명사의 받침에 따라 은과 는이 달라지는지 확인하며 읽어 보세요.",
            2: "숫자를 사용해 전화번호나 번호 정보를 표현했어요. 숫자를 한 자리씩 끊어 또렷하게 읽어 보세요.",
            3: "장소 뒤에 ‘에’를 쓰고 ‘있어요/없어요’로 위치와 존재를 잘 표현했어요.",
            4: "동사를 ‘-아요/어요’ 형태로 알맞게 마무리했어요. 문장 끝의 ‘요’까지 자연스럽게 이어서 읽어 보세요.",
            5: "‘하고’를 사용해 두 명사를 자연스럽게 연결했어요. ‘빵하고 우유’처럼 연결한 말을 한 덩어리로 읽어 보세요.",
            6: "‘-(으)세요’를 사용해 공손한 요청 문장을 만들었어요. 받침 유무에 따른 형태를 확인하며 읽어 보세요.",
            7: "‘시’를 사용해 시간을 묻거나 답하는 문장을 만들었어요. 시각 부분을 또렷하게 강조해 읽어 보세요.",
            8: "날씨를 나타내는 표현을 사용해 상태를 설명했어요. ‘더워요/추워요’의 ‘워요’를 자연스럽게 이어 읽어 보세요.",
            9: "‘-았어요/-었어요’ 형태로 과거에 한 일을 표현했어요. 현재가 아닌 지난 일임을 생각하며 문장 끝을 확인해 보세요.",
            10: "‘-(으)러 가다/오다’를 사용해 이동 목적을 표현했어요. ‘공부하러 가요’처럼 목적과 이동 동사를 함께 묶어 읽어 보세요.",
        }
        detail = success_feedback.get(unit_number, "이번 단원의 목표 문법을 문장에 알맞게 사용했어요.")
        return True, f"작성한 문장: {text}\n\n문법 확인: {detail}"
    return False, hint


def complete_activity1_practice(unit_number):
    """Finish guided practice and move the same stage to challenge mode."""
    st.session_state[f"activity1_practice_completed_{unit_number}"] = True
    st.session_state[f"activity_mode_{unit_number}"] = "도전 모드 · 힌트 없이"


def get_config_value(name, default=None):
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def transcribe_audio(audio_file):
    """Transcribe Korean audio with OpenAI when OPENAI_API_KEY is configured."""
    api_key = get_config_value("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY가 설정되지 않았습니다. 샘플 분석을 사용할 수 있습니다."
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        audio_file.seek(0)
        safe_name = f"gravity_audio{Path(audio_file.name).suffix.lower() or '.wav'}"
        response = client.audio.transcriptions.create(
            model=get_config_value("OPENAI_TRANSCRIBE_MODEL", "gpt-transcribe"),
            file=(safe_name, audio_file.getvalue(), audio_file.type or "application/octet-stream"),
            language="ko",
            prompt="한국어 학습자의 발음 연습입니다. 한국어 문장을 정확히 받아 적으세요.",
        )
        return response.text, None
    except Exception as error:
        return None, f"음성 분석에 실패했습니다: {error}"


def firebase_configured():
    return bool(get_config_value("FIREBASE_DATABASE_URL"))


def firebase_request(path, method="GET", payload=None):
    """Small Firebase Realtime Database REST adapter; no service account in source."""
    if not firebase_configured():
        return None
    base = get_config_value("FIREBASE_DATABASE_URL").rstrip("/")
    auth_token = get_config_value("FIREBASE_AUTH_TOKEN")
    query = {"auth": auth_token} if auth_token else {}
    url = f"{base}/{path.strip('/')}.json"
    if query:
        url += "?" + urlencode(query)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except Exception:
        return None


def publish_sync_state(submissions):
    state = {"mission": "sentence-build", "submissions": submissions, "updated_at": datetime.now().isoformat()}
    return firebase_request("gravity_korean/classroom", method="PUT", payload=state)


def read_sync_state():
    return firebase_request("gravity_korean/classroom")


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_storage():
    with get_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS learner_progress (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                streak INTEGER NOT NULL DEFAULT 1,
                completed_days INTEGER NOT NULL DEFAULT 0,
                last_completed_date TEXT,
                total_xp INTEGER NOT NULL DEFAULT 0,
                total_correct INTEGER NOT NULL DEFAULT 0,
                total_answered INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_sessions (
                session_date TEXT PRIMARY KEY,
                completed INTEGER NOT NULL DEFAULT 0,
                correct INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                wrong_skills TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_unit_sessions (
                session_date TEXT NOT NULL,
                unit_number INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                correct INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                wrong_skills TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (session_date, unit_number)
            )
            """
        )
        connection.execute("INSERT OR IGNORE INTO learner_progress (id) VALUES (1)")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(learner_progress)")}
        if "unlocked_stage" not in columns:
            connection.execute("ALTER TABLE learner_progress ADD COLUMN unlocked_stage INTEGER NOT NULL DEFAULT 1")


def load_progress():
    initialize_storage()
    with get_db() as connection:
        row = connection.execute("SELECT * FROM learner_progress WHERE id = 1").fetchone()
    return dict(row)


def get_weekly_completed_days():
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    with get_db() as connection:
        row = connection.execute(
            "SELECT COUNT(DISTINCT session_date) AS count FROM daily_unit_sessions WHERE completed = 1 AND session_date >= ?",
            (monday.isoformat(),),
        ).fetchone()
    return row["count"]


def has_completed_session_on_date(session_date):
    """Return whether any unit practice was already completed on this calendar date."""
    with get_db() as connection:
        row = connection.execute(
            "SELECT 1 FROM daily_unit_sessions WHERE session_date = ? AND completed = 1 LIMIT 1",
            (session_date,),
        ).fetchone()
    return row is not None


def load_daily_session(session_date, unit_number):
    with get_db() as connection:
        row = connection.execute(
            "SELECT * FROM daily_unit_sessions WHERE session_date = ? AND unit_number = ?",
            (session_date, unit_number),
        ).fetchone()
    return dict(row) if row else None


def save_progress():
    with get_db() as connection:
        connection.execute(
            """
            UPDATE learner_progress
            SET streak = ?, completed_days = ?, last_completed_date = ?,
                total_xp = ?, total_correct = ?, total_answered = ?, unlocked_stage = ?
            WHERE id = 1
            """ ,
            (
                st.session_state.get("streak", 1),
                st.session_state.get("completed_days", 0),
                st.session_state.get("last_completed_date"),
                st.session_state.get("total_xp", 0),
                st.session_state.get("total_correct", 0),
                st.session_state.get("total_answered", 0),
                st.session_state.get("unlocked_stage", 1),
            ),
        )


def save_daily_session(session_date, unit_number, completed=False):
    skills = ",".join(st.session_state.get("practice_wrong_skills", []))
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO daily_unit_sessions (session_date, unit_number, completed, correct, total, xp, wrong_skills)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_date, unit_number) DO UPDATE SET
                completed = excluded.completed,
                correct = excluded.correct,
                total = excluded.total,
                xp = excluded.xp,
                wrong_skills = excluded.wrong_skills
            """,
            (
                session_date,
                unit_number,
                int(completed),
                st.session_state.get("practice_correct", 0),
                len(st.session_state.get("practice_questions", [])),
                st.session_state.get("practice_xp", 0),
                skills,
            ),
        )


if "progress_loaded" not in st.session_state:
    stored_progress = load_progress()
    st.session_state.update(stored_progress)
    st.session_state.progress_loaded = True
st.session_state.setdefault("daily_tasks", {"vocab": False, "grammar": False, "speaking": False})
st.session_state.setdefault("app_language", "한국어")
st.session_state.setdefault("app_theme", "검은색")
if st.session_state.get("grammar1_sequence_version") != 2:
    for unit_number in range(1, 11):
        st.session_state.pop(f"grammar1_done_{unit_number}", None)
        st.session_state.pop(f"grammar1_index_{unit_number}", None)
        st.session_state.pop(f"grammar1_result_{unit_number}", None)
    st.session_state.grammar1_sequence_version = 2
if st.session_state.get("stage_validation_version") != 3:
    for unit_number in range(1, 11):
        st.session_state.pop(f"grammar2_done_{unit_number}", None)
        st.session_state.pop(f"activity1_completed_{unit_number}", None)
        st.session_state.pop(f"unit_completed_{unit_number}", None)
        st.session_state.pop(f"activity1_practice_completed_{unit_number}", None)
    st.session_state.stage_validation_version = 3


def pronunciation_mission():
    st.markdown(f'<div class="eyebrow">{interface_text("맞춤 학습 · 발음 연습", "Personal practice · pronunciation lab")}</div><h1>받침 발음을 <span class="lime">깨워볼까요?</span></h1><p class="sub">오늘의 목표는 “한국어”를 자연스럽게 말하는 것입니다. 음성 파일을 올리면 발음 피드백과 다음 단계가 열립니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    if st.session_state.get("unlocked_stage", 1) >= 2:
        st.success("받침 발음 미션을 완료했습니다. 다음 단계 ‘카페에서 주문하기’가 해제되었습니다.")
    left, right = st.columns([1.2, 1])
    with left:
        st.markdown('<div class="card"><div class="eyebrow">Mission 01 · batchim</div><h2>따라 말해 보세요</h2><div style="font-size:32px;font-weight:800;margin:25px 0">한국어</div><p class="sub">천천히 녹음해도 괜찮아요. ㄴ 받침과 모음 연결에 집중해 보세요.</p></div>', unsafe_allow_html=True)
        audio = st.file_uploader("음성 파일 업로드", type=["wav", "mp3", "m4a"], key="pronunciation_audio")
        run_demo = st.button("샘플 발음 분석 실행", type="primary", width="stretch")
        if audio or run_demo:
            st.audio(audio if audio else None, format="audio/wav") if audio else None
            st.markdown('<div class="card"><div class="eyebrow">AI pronunciation preview</div><h2>정확도 <span class="lime">88%</span></h2><div class="progress"><div style="width:88%"></div></div><p class="sub">“한”의 받침은 좋아요. “국어”로 넘어갈 때 소리를 조금 더 부드럽게 이어 보세요.</p><span class="pill active">ㄴ 받침</span><span class="pill">연음</span></div>', unsafe_allow_html=True)
            if st.session_state.get("unlocked_stage", 1) < 2 and st.button("미션 완료하고 다음 단계 열기 →", type="primary", key="unlock_pronunciation"):
                st.session_state.unlocked_stage = 2
                st.session_state.total_xp = st.session_state.get("total_xp", 0) + 30
                save_progress()
                st.rerun()
    with right:
        metric("PRONUNCIATION", "88%", "샘플 분석 결과", "lime")
        metric("NEXT UNLOCK", "A2 · 02", "카페에서 주문하기")
        st.markdown('<div class="card"><div class="eyebrow">How it works</div><h3>중력처럼 쌓이는 학습</h3><p class="sub">발음 미션을 완료하면 다음 회화 미션이 자동으로 해제됩니다. 작은 성공을 이어 가세요.</p></div>', unsafe_allow_html=True)


def sentence_builder():
    missions = [
        (["저는", "카페에서", "아메리카노를", "주문해요"], "저는 카페에서 아메리카노를 주문해요"),
        (["오늘은", "친구와", "도서관에서", "공부해요"], "오늘은 친구와 도서관에서 공부해요"),
        (["주말에", "공원에서", "사진을", "찍어요"], "주말에 공원에서 사진을 찍어요"),
        (["저는", "아침마다", "한국어를", "공부해요"], "저는 아침마다 한국어를 공부해요"),
        (["동생이", "방에서", "음악을", "들어요"], "동생이 방에서 음악을 들어요"),
        (["오늘", "친구에게", "메시지를", "보내요"], "오늘 친구에게 메시지를 보내요"),
        (["식당에서", "비빔밥을", "주문하고", "싶어요"], "식당에서 비빔밥을 주문하고 싶어요"),
        (["저녁에", "가족과", "영화를", "봐요"], "저녁에 가족과 영화를 봐요"),
    ]
    completed_days = get_weekly_completed_days()
    selected_unit_number = st.session_state.get("selected_unit_number", 1)
    current_unit = TEXTBOOK_UNITS[selected_unit_number]
    missions = TEXTBOOK_SENTENCE_MISSIONS[current_unit["number"]]
    today_key = datetime.now().date().isoformat()
    if (
        st.session_state.get("builder_date") != today_key
        or st.session_state.get("builder_unit_number") != current_unit["number"]
    ):
        st.session_state.builder_date = today_key
        st.session_state.builder_unit_number = current_unit["number"]
        st.session_state.builder_index = 0
        st.session_state.builder_offset = datetime.now().date().toordinal() % len(missions)
        st.session_state.builder_answer = []
        st.session_state.builder_completed = False
        st.session_state.builder_finished = False
    st.session_state.setdefault("builder_finished", False)
    words, answer = missions[(st.session_state.builder_offset + st.session_state.builder_index) % len(missions)]
    st.markdown(f'<div class="eyebrow interactive-grammar-eyebrow">{interface_text("문장 조합 연습", "Interactive grammar")} · {TEXTBOOK_TITLE} · {current_unit["number"]}단원</div><h1>단어를 끌어당겨 <span class="lime">문장을 완성하세요.</span></h1><p class="sub">{current_unit["title"]}의 핵심 문장 구조를 연습합니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    completed_sentence_count = len(missions) if st.session_state.builder_finished else min(
        st.session_state.builder_index + (1 if st.session_state.builder_completed else 0), len(missions)
    )
    with st.container(border=True):
        st.markdown(
            f'<div class="eyebrow">{interface_text("미션", "Mission")} {st.session_state.builder_index + 1:02d} · {interface_text("문장 조합", "sentence build")}</div>'
            f'<h2>“{current_unit["goal"]}” <span class="sentence-progress-count">({completed_sentence_count}/{len(missions)})</span></h2>',
            unsafe_allow_html=True,
        )
        current = " ".join(st.session_state.builder_answer) or "단어를 아래에서 선택하세요"
        st.markdown(f'<div style="min-height:58px;padding:16px;background:#202020;border:1px dashed #555;border-radius:12px;font-size:19px">{current}</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        cols = st.columns(len(words))
        for position, word in enumerate(words):
            with cols[position]:
                if word not in st.session_state.builder_answer and st.button(word, key=f"builder_word_{st.session_state.builder_index}_{position}", width="stretch", disabled=st.session_state.builder_finished):
                    st.session_state.builder_answer.append(word)
                    st.rerun()
        a, b, c = st.columns(3)
        with a:
            if st.button("선택 지우고 다시 하기", key="builder_reset", width="stretch"):
                st.session_state.builder_answer = []
                st.rerun()
        with b:
            if st.button(
                "문장 제출 완료 ✓" if st.session_state.builder_completed or st.session_state.builder_finished else "문장 제출하기  →",
                key="builder_submit",
                type="secondary" if st.session_state.builder_completed or st.session_state.builder_finished else "primary",
                width="stretch",
                disabled=st.session_state.builder_completed or st.session_state.builder_finished,
            ):
                if " ".join(st.session_state.builder_answer) == answer:
                    if st.session_state.builder_index + 1 >= len(missions):
                        st.session_state.builder_finished = True
                        st.session_state[f"review_sentence_done_{current_unit['number']}"] = True
                        st.session_state.unlocked_stage = max(st.session_state.get("unlocked_stage", 1), 3)
                        st.session_state.total_xp = st.session_state.get("total_xp", 0) + 40
                        save_progress()
                        st.success("모든 문장 조합을 완료했어요! +40 XP", icon=":material/celebration:")
                    else:
                        st.session_state.builder_completed = True
                        st.session_state.total_xp = st.session_state.get("total_xp", 0) + 40
                        save_progress()
                        st.success("정답입니다! +40 XP · 다음 문장이 열렸어요.")
                    st.rerun()
                else:
                    st.warning(
                        f"순서가 달라요. ① 왼쪽의 ‘선택 지우고 다시 하기’를 누르세요. "
                        f"② ‘{words[0]}’부터 시작해 단어를 문장 순서대로 다시 선택하세요. "
                        "③ 완성한 뒤 ‘문장 제출하기’를 누르세요.",
                        icon=":material/replay:",
                    )
        with c:
            if st.session_state.builder_completed and st.button("다음 문장으로  →", key="builder_next", type="primary", width="stretch"):
                st.session_state.builder_index += 1
                st.session_state.builder_answer = []
                st.session_state.builder_completed = False
                st.rerun()
    if st.session_state.builder_finished:
        st.info(f"{len(missions)}개 문장을 모두 완성했습니다. 오늘의 문장 조합 학습이 끝났어요.", icon=":material/check_circle:")
        st.button("학습 홈으로 돌아가기", type="primary", on_click=navigate_to_page, args=("내 학습",))


def sync_mode():
    if "sync_submissions" not in st.session_state:
        st.session_state.sync_submissions = 12
    st.markdown('<div class="eyebrow">Offline · live classroom</div><h1>Sync Mode <span class="lime">↗</span></h1><p class="sub">강사가 보낸 미션을 모든 학습자 화면에 동시에 표시하는 수업용 모드입니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2 = st.columns([1.35, 1])
    with c1:
        st.markdown('<div class="card"><div class="eyebrow">Live mission · sentence drop</div><h2>오늘의 문장 조립</h2><p class="sub">아래 단어를 올바른 순서로 배치해 보세요.</p><div style="margin:24px 0 13px"><span class="word">저는</span><span class="word">한국어를</span><span class="word">배워요</span></div><p class="tiny">제출하면 강사 화면의 참여 현황에 즉시 반영됩니다.</p></div>', unsafe_allow_html=True)
        if st.button("정답 제출  →", type="primary", key="sync_submit", width="stretch"):
            st.session_state.sync_submissions = min(18, st.session_state.sync_submissions + 1)
            st.success("제출되었습니다. 강사 화면에 반영되었습니다.")
    with c2:
        st.markdown('<div class="card"><div class="eyebrow">Instructor control</div><h3>현재 진행 상황</h3>', unsafe_allow_html=True)
        participation = st.session_state.sync_submissions / 18
        st.progress(participation)
        st.markdown(f'<div style="display:flex;justify-content:space-between"><span class="tiny">18명 중 {st.session_state.sync_submissions}명 참여</span><span class="tiny lime">{round(participation * 100)}%</span></div><br>', unsafe_allow_html=True)
        st.markdown('<div class="lesson-row"><span>정답 제출</span><b class="lime">12명</b></div><div class="lesson-row"><span>도움말 사용</span><b>4명</b></div><div class="lesson-row"><span>대기 중</span><b class="coral">2명</b></div></div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    a, b, c = st.columns(3)
    with a: metric("PRONUNCIATION", "92%", "평균 정확도", "lime")
    with b: metric("SENTENCE BUILD", f"{st.session_state.sync_submissions} / 18", "완료한 학습자")
    with c: metric("CLASS ENERGY", "HIGH", "참여도 상승 중", "coral")


def _discard_theme_css(*args, **kwargs):
    """Keep retired theme rules out of the rendered page during rollback."""


def inject_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap');
        :root { --ink:#f3f0ea; --muted:#9c9a9a; --panel:#151515; --line:#2b2b2b; --lime:#c9f66d; --coral:#ff806b; }
        .stApp { background:#0d0d0d; color:var(--ink); font-family:'Manrope', sans-serif; }
        [data-testid="stMainBlockContainer"] { padding-top:2rem !important; }
        [data-testid="stSidebar"] { background:#111; border-right:1px solid #252525; }
        [data-testid="stSidebar"] > div:first-child { padding-top:.75rem; }
        .brand { display:flex; align-items:center; gap:6px; font-size:20px; font-weight:800; letter-spacing:-.7px; margin:-22px 0 3rem; }
        .brand-mark { color:var(--lime); }
        .brand .instructor-name {
            color:var(--lime);
        }
        .brand .course-name { color:var(--ink); }
        .eyebrow { color:var(--lime); font:500 12px 'DM Mono', monospace; letter-spacing:1.2px; text-transform:uppercase; }
        .interactive-grammar-eyebrow { line-height:1.5; }
        .interactive-grammar-eyebrow + h1 { margin-top:12px !important; }
        h1 { font-size:36px !important; line-height:1.12 !important; letter-spacing:-1.7px !important; margin:7px 0 13px !important; }
        h2 { font-size:26px !important; letter-spacing:-.8px !important; }
        h3 { font-size:20px !important; letter-spacing:-.3px !important; }
        .sub { color:var(--muted); font-size:16px; line-height:1.7; max-width:680px; }
        .lesson-path-copy { max-width:none; }
        .unit-learning-title { font-size:30px; font-weight:850; line-height:1.2; letter-spacing:-1.2px; margin:2px 0 10px; }
        .unit-learning-title span { color:var(--lime); }
        .unit-learning-intro { color:#c8c6c1; font-size:15px; font-weight:500; line-height:1.6; margin-bottom:18px; }
        .unit-step-summary { color:#d9f59a; font-size:14px; font-weight:600; line-height:1.45; letter-spacing:-.3px; white-space:nowrap; }
        .sentence-progress-count { color:var(--lime); font-size:18px; font-weight:800; letter-spacing:-.4px; white-space:nowrap; }
        div[class*="st-key-unit2_sino_number_"] button {
            border-color:#82c91e !important; color:#b7ef58 !important; background:rgba(130,201,30,.10) !important;
        }
        div[class*="st-key-unit2_sino_number_"] button:hover { background:rgba(130,201,30,.22) !important; }
        div[class*="st-key-unit2_sino_number_"] button[data-testid="stBaseButton-primary"] {
            background:#82c91e !important; color:#111 !important; font-weight:800 !important;
        }
        div[class*="st-key-unit2_number_example_"] button {
            border-color:#ff7a59 !important; color:#ff9a80 !important; background:rgba(255,122,89,.10) !important;
        }
        div[class*="st-key-unit2_number_example_"] button:hover { background:rgba(255,122,89,.22) !important; }
        div[class*="st-key-unit2_number_example_"] button[data-testid="stBaseButton-primary"] {
            background:#ff7a59 !important; color:#111 !important; font-weight:800 !important;
        }
        .card { background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:22px; height:100%; }
        .metric-label { color:var(--muted); font:11px 'DM Mono',monospace; text-transform:uppercase; letter-spacing:1px; }
        .metric-value { font-size:31px; font-weight:800; margin-top:8px; letter-spacing:-1.5px; }
        .metric-note { color:#aaa; font-size:12px; margin-top:4px; }
        .lime { color:var(--lime); } .coral { color:var(--coral); }
        .grammar-one { color:#ff9b72 !important; font-weight:850; }
        .grammar-two { color:#78aef8 !important; font-weight:850; }
        .grammar-feedback { padding:10px 12px; margin:7px 0; border-left:3px solid #78aef8; background:#172033; border-radius:7px; line-height:1.7; }
        .progress { height:6px; background:#292929; border-radius:9px; overflow:hidden; margin:17px 0 5px; }
        .progress > div { height:100%; background:var(--lime); border-radius:9px; }
        .pill { display:inline-block; border:1px solid #3e3e3e; border-radius:20px; color:#bdbdbd; padding:5px 10px; font-size:11px; margin-right:5px; }
        .pill.active { color:#111; background:var(--lime); border-color:var(--lime); }
        .word { display:inline-block; background:#242424; border:1px solid #444; border-radius:9px; padding:10px 13px; margin:4px 3px; font-weight:600; }
        .word.answer { background:#29361b; border-color:var(--lime); color:var(--lime); }
        .reading-line { padding:12px 14px; margin:7px 0; border-left:3px solid #333; border-radius:8px; color:#767676; background:#1c1c1c; transition:all .2s ease; }
        .reading-line.active { border-left-color:var(--lime); color:#f4f4f4; background:#29361b; box-shadow:0 0 0 1px rgba(197,255,77,.18); }
        .reading-line.done { border-left-color:#718e36; color:#b9c899; background:#222a1b; }
        .learning-hint { color:#9ca7b5; font-size:14px; line-height:1.65; padding:10px 12px; margin-bottom:14px; border-left:3px solid #596575; background:#1d232b; border-radius:7px; }
        .learning-lock { color:#ffc0b5; font-size:14px; line-height:1.65; padding:10px 12px; border-left:3px solid #d16b5c; background:#2b2020; border-radius:7px; }
        .learning-lock b { color:#ff927f; margin-right:6px; }
        .lesson-row { display:flex; align-items:center; justify-content:space-between; padding:15px 0; border-bottom:1px solid #292929; }
        .lesson-row:last-child { border-bottom:0; }
        .lesson-icon { width:38px; height:38px; display:inline-flex; align-items:center; justify-content:center; background:#242424; border-radius:11px; margin-right:12px; }
        .tiny { color:#a3a3a3; font-size:14px; }
        div[data-testid="stButton"] button { border-radius:10px; border:1px solid #3d3d3d; background:#202020; color:#f4f4f4; font-size:16px; font-weight:600; min-height:44px; }
        div[data-testid="stButton"] button:hover { border-color:var(--lime); color:var(--lime); }
        div[data-testid="stButton"] button[kind="primary"] { background:var(--lime); color:#111; border:0; }
        [class*="st-key-vocab_navigation"] button { background:#15191e !important; border:1px solid #596575 !important; color:#c8d0da !important; min-height:34px !important; font-size:13px !important; }
        [class*="st-key-vocab_navigation"] button:hover:not(:disabled) { border-color:#aeb9c8 !important; color:#ffffff !important; }
        div.st-key-selected_unit_label div[data-baseweb="select"] > div,
        div.st-key-selected_unit_label div[data-baseweb="select"] > div:focus,
        div.st-key-selected_unit_label div[data-baseweb="select"] > div:focus-within {
            border-color:#3d3d3d !important;
            box-shadow:none !important;
            outline:none !important;
        }
        div.st-key-selected_unit_label { margin-bottom:10px; }
        .stProgress > div > div > div > div { background:var(--lime); }
        .stRadio label, .stCheckbox label { color:#d8d8d8 !important; }
        [class*="st-key-grammar_quiz_choice_"] [role="radiogroup"] { column-gap:36px !important; row-gap:12px !important; }
        @media (max-width: 760px) {
            [data-testid="stMainBlockContainer"] { padding:1rem .75rem 2rem !important; }
            h1 { font-size:30px !important; letter-spacing:-1px !important; }
            h2 { font-size:23px !important; }
            h3 { font-size:19px !important; }
            .interactive-grammar-eyebrow + h1 { margin-top:9px !important; }
            .unit-learning-title { font-size:26px; }
            .unit-step-summary { white-space:normal; font-size:14px; }
            div[data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; }
            div[data-testid="column"] { min-width:min(100%, 250px) !important; flex:1 1 250px !important; }
            div[data-testid="stTabs"] [data-baseweb="tab-list"] { overflow-x:auto; flex-wrap:nowrap; }
            div[data-testid="stTabs"] [role="tab"] { min-width:max-content; }
            img { max-width:100%; height:auto; }
            div[class*="st-key-unit3_"][class*="_pair"],
            div[class*="st-key-unit4_"][class*="_pair"],
            div[class*="st-key-unit3_"][class*="_reference"],
            div[class*="st-key-unit4_"][class*="_reference"] { min-height:0 !important; }
            div[class*="st-key-unit3_"][class*="_pair"] [data-testid="stHorizontalBlock"],
            div[class*="st-key-unit4_"][class*="_pair"] [data-testid="stHorizontalBlock"],
            div[class*="st-key-unit3_"][class*="_reference"] [data-testid="stHorizontalBlock"],
            div[class*="st-key-unit4_"][class*="_reference"] [data-testid="stHorizontalBlock"] {
                display:grid !important; grid-template-columns:minmax(0, 1fr) !important;
                gap:12px !important;
            }
            div[class*="st-key-unit3_"][class*="_pair"] [data-testid="stElementContainer"],
            div[class*="st-key-unit4_"][class*="_pair"] [data-testid="stElementContainer"],
            div[class*="st-key-unit3_"][class*="_reference"] [data-testid="stElementContainer"],
            div[class*="st-key-unit4_"][class*="_reference"] [data-testid="stElementContainer"] {
                position:static !important; inset:auto !important;
                width:auto !important; height:auto !important;
            }
            div[class*="st-key-unit3_"][class*="_pair"] [data-testid="stColumn"],
            div[class*="st-key-unit4_"][class*="_pair"] [data-testid="stColumn"],
            div[class*="st-key-unit3_"][class*="_reference"] [data-testid="stColumn"],
            div[class*="st-key-unit4_"][class*="_reference"] [data-testid="stColumn"] {
                width:100% !important; min-width:0 !important; height:auto !important;
            }
        }
        </style>
        """, unsafe_allow_html=True,
    )
    themes = {
        "검은색": {"app": "#000000", "sidebar": "#090909", "panel": "#151515", "line": "#2b2b2b", "ink": "#f3f0ea", "muted": "#9c9a9a", "accent": "#c9f66d", "button": "#202020", "grammar1": "#ff9b72", "grammar2": "#78aef8", "feedback": "#172033", "step": "#d9f59a", "completed": "#ffffff", "selected_bg": "#263217", "select_border": "#737373"},
        "남색": {"app": "#0a1020", "sidebar": "#0d1528", "panel": "#121d33", "line": "#263858", "ink": "#f2f6ff", "muted": "#9cabc4", "accent": "#76d7ff", "button": "#172642", "grammar1": "#ff9b72", "grammar2": "#8bbcff", "feedback": "#17233b", "step": "#bdeeff", "completed": "#ffffff", "selected_bg": "#173452", "select_border": "#60799f"},
        "밝은색": {"app": "#f6f3ed", "sidebar": "#eee9df", "panel": "#ffffff", "line": "#d8d1c5", "ink": "#191919", "muted": "#6f6b65", "accent": "#527a00", "button": "#ffffff", "grammar1": "#ff9b72", "grammar2": "#78aef8", "feedback": "#172033", "step": "#d9f59a", "completed": "#ffffff", "selected_bg": "#202020", "select_border": "#999187"},
    }
    theme = themes.get(st.session_state.get("app_theme", "검은색"), themes["검은색"])
    _discard_theme_css(
        f"""
        <style>
        :root {{
            --ink:{theme['ink']}; --muted:{theme['muted']}; --panel:{theme['panel']};
            --line:{theme['line']}; --lime:{theme['accent']};
            --grammar-one:{theme['grammar1']}; --grammar-two:{theme['grammar2']};
            --feedback-bg:{theme['feedback']}; --step-summary:{theme['step']};
            --completed-tab:{theme['completed']};
        }}
        .stApp {{ background:{theme['app']}; color:{theme['ink']}; }}
        [data-testid="stSidebar"] {{ background:{theme['sidebar']}; border-right-color:{theme['line']}; }}
        [data-testid="stSidebar"] {{
            --background-color:{theme['sidebar']};
            --secondary-background-color:{theme['selected_bg']};
            --text-color:{theme['ink']};
            --primary-color:{theme['accent']};
        }}
        div[data-testid="stButton"] button {{ background:{theme['button']}; color:{theme['ink']}; border-color:{theme['line']}; }}
        .stRadio label, .stCheckbox label {{ color:{theme['ink']} !important; }}
        .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
        .stApp [data-testid="stMarkdownContainer"] p,
        .stApp [data-testid="stMarkdownContainer"] li,
        .stApp [data-testid="stCaptionContainer"],
        .stApp [data-testid="stWidgetLabel"] p,
        .stApp [data-baseweb="tab"] {{ color:{theme['ink']}; }}
        .stApp [data-testid="stCaptionContainer"] {{ color:{theme['muted']} !important; }}
        .stApp input, .stApp textarea,
        .stApp [data-baseweb="select"] > div,
        .stApp [data-baseweb="select"] input {{ color:{theme['ink']} !important; }}
        .stApp [data-baseweb="select"] svg {{ fill:{theme['ink']} !important; }}
        div[data-testid="stButton"] button p {{ color:{theme['ink']}; }}
        div[data-testid="stButton"] button[kind="primary"] p {{ color:#111; }}
        [data-testid="stSidebar"] div.st-key-app_language div[data-baseweb="select"] > div,
        [data-testid="stSidebar"] div.st-key-app_theme div[data-baseweb="select"] > div,
        [data-testid="stSidebar"] div.st-key-selected_unit_label div[data-baseweb="select"] > div {{
            background:{theme['selected_bg']} !important;
            border:2px solid {theme['accent']} !important;
            color:{theme['ink']} !important;
            font-weight:750 !important;
            box-shadow:0 0 0 2px color-mix(in srgb, {theme['accent']} 18%, transparent) !important;
        }}
        [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"],
        [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [role="combobox"] {{
            background:{theme['selected_bg']} !important;
            background-color:{theme['selected_bg']} !important;
            background-image:none !important;
            border-color:{theme['accent']} !important;
            color:{theme['ink']} !important;
            opacity:1 !important;
        }}
        [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div > div,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [role="combobox"] > div {{
            background:transparent !important;
            background-color:transparent !important;
            color:{theme['ink']} !important;
        }}
        [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] div,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] button,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] [aria-hidden="true"] {{
            background:transparent !important;
            background-color:transparent !important;
            border-color:transparent !important;
            color:{theme['ink']} !important;
        }}
        [data-testid="stSidebar"] [data-testid="stSelectbox"] input,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] input:read-only {{
            background:{theme['selected_bg']} !important;
            background-color:{theme['selected_bg']} !important;
            color:{theme['ink']} !important;
            -webkit-text-fill-color:{theme['ink']} !important;
            caret-color:{theme['ink']} !important;
            opacity:1 !important;
        }}
        [data-testid="stSidebar"] [data-testid="stSelectbox"] svg {{
            fill:{theme['ink']} !important;
            color:{theme['ink']} !important;
            stroke:{theme['ink']} !important;
            opacity:1 !important;
        }}
        [data-testid="stSidebar"] [data-testid="stSelectbox"] svg path {{
            fill:{theme['ink']} !important;
            stroke:{theme['ink']} !important;
        }}
        [data-testid="stSidebar"] div.st-key-app_language div[data-baseweb="select"] > div > div,
        [data-testid="stSidebar"] div.st-key-app_theme div[data-baseweb="select"] > div > div,
        [data-testid="stSidebar"] div.st-key-selected_unit_label div[data-baseweb="select"] > div > div,
        [data-testid="stSidebar"] div.st-key-app_language div[data-baseweb="select"] input,
        [data-testid="stSidebar"] div.st-key-app_theme div[data-baseweb="select"] input,
        [data-testid="stSidebar"] div.st-key-selected_unit_label div[data-baseweb="select"] input {{
            background:transparent !important;
            color:{theme['ink']} !important;
            -webkit-text-fill-color:{theme['ink']} !important;
            font-weight:750 !important;
        }}
        [data-testid="stSidebar"] div.st-key-app_language div[data-baseweb="select"] span,
        [data-testid="stSidebar"] div.st-key-app_theme div[data-baseweb="select"] span,
        [data-testid="stSidebar"] div.st-key-selected_unit_label div[data-baseweb="select"] span {{
            color:{theme['ink']} !important;
        }}
        [data-testid="stSidebar"] div.st-key-app_language div[data-baseweb="select"] > div:hover,
        [data-testid="stSidebar"] div.st-key-app_theme div[data-baseweb="select"] > div:hover,
        [data-testid="stSidebar"] div.st-key-selected_unit_label div[data-baseweb="select"] > div:hover {{
            border-color:{theme['accent']} !important;
            filter:brightness(.97);
        }}
        [data-baseweb="popover"],
        [data-baseweb="popover"] > div,
        [data-baseweb="popover"] [role="listbox"] {{
            background:{theme['panel']} !important;
            color:{theme['ink']} !important;
        }}
        [data-baseweb="popover"] [role="option"] {{
            background:{theme['panel']} !important;
            color:{theme['ink']} !important;
        }}
        [data-baseweb="popover"] [role="option"]:hover {{
            background:color-mix(in srgb, {theme['selected_bg']} 65%, {theme['panel']}) !important;
        }}
        [data-baseweb="popover"] [role="option"][aria-selected="true"] {{
            background:{theme['selected_bg']} !important;
            color:{theme['ink']} !important;
            font-weight:800 !important;
        }}
        [data-testid="stHeader"], .stAppHeader,
        [data-testid="stToolbar"], [data-testid="stStatusWidget"] {{
            background:{theme['app']} !important;
            color:{theme['ink']} !important;
        }}
        [data-testid="stHeader"] button,
        [data-testid="stToolbar"] button {{
            color:{theme['ink']} !important;
            background:transparent !important;
        }}
        [data-testid="stHeader"] button:hover,
        [data-testid="stToolbar"] button:hover {{
            background:{theme['selected_bg']} !important;
        }}
        [data-testid="stHeader"] svg,
        [data-testid="stToolbar"] svg,
        [data-testid="stStatusWidget"] svg {{
            fill:{theme['ink']} !important;
            color:{theme['ink']} !important;
        }}
        [data-testid="stDecoration"] {{ background:{theme['accent']} !important; }}
        [data-testid="stSidebar"] div.st-key-page_nav [role="radiogroup"] label {{
            border-left:4px solid transparent;
            border-radius:8px;
            padding:.42rem .55rem;
        }}
        [data-testid="stSidebar"] div.st-key-page_nav [role="radiogroup"] label:hover {{
            background:color-mix(in srgb, {theme['selected_bg']} 55%, transparent);
        }}
        [data-testid="stSidebar"] div.st-key-page_nav [role="radiogroup"] label:has(input:checked) {{
            background:{theme['selected_bg']} !important;
            border-left-color:{theme['accent']} !important;
        }}
        [data-testid="stSidebar"] div.st-key-page_nav [role="radiogroup"] label:has(input:checked) p {{
            color:{theme['accent']} !important;
            font-weight:850 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        :root {{ --ink:{theme['ink']}; --muted:{theme['muted']}; --panel:{theme['panel']}; --line:{theme['line']}; --lime:{theme['accent']}; }}
        .stApp {{ background:{theme['app']}; color:{theme['ink']}; }}
        [data-testid="stSidebar"] {{ background:{theme['sidebar']}; border-right-color:{theme['line']}; }}
        div[data-testid="stButton"] button {{ background:{theme['button']}; color:{theme['ink']}; border-color:{theme['line']}; }}
        .stRadio label, .stCheckbox label {{ color:{theme['ink']} !important; }}
        [data-testid="stSidebar"] div[data-baseweb="select"] {{
            border-color:{theme['select_border']} !important;
            outline:1px solid {theme['select_border']} !important;
            outline-offset:1px !important;
            border-radius:9px !important;
            box-shadow:0 0 0 1px color-mix(in srgb, {theme['select_border']} 30%, transparent) !important;
        }}
        [data-testid="stSidebar"] div[data-baseweb="select"]:hover,
        [data-testid="stSidebar"] div[data-baseweb="select"]:focus-within {{
            border-color:{theme['accent']} !important;
            outline-color:{theme['accent']} !important;
            box-shadow:0 0 0 1px {theme['accent']} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def metric(label, value, note, color=""):
    st.markdown(f'<div class="card"><div class="metric-label">{label}</div><div class="metric-value {color}">{value}</div><div class="metric-note">{note}</div></div>', unsafe_allow_html=True)


def sync_mode():
    st.markdown('<div class="eyebrow">Offline · live classroom</div><h1>Sync Mode <span class="lime">↗</span></h1><p class="sub">강사 화면에서 보낸 신호가 교실의 모든 학습자 화면을 동시에 바꿉니다. 오늘은 문장 조립 미션입니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2 = st.columns([1.35, 1])
    with c1:
        st.markdown('<div class="card"><div class="eyebrow">Live mission · 03:42 left</div><h2>오늘의 문장 조립</h2><p class="sub">주어진 단어를 올바른 순서로 배치해 보세요.</p><div style="margin:24px 0 13px"><span class="word answer">저는</span><span class="word answer">한국어를</span><span class="word answer">배워요</span></div><p class="tiny">정답을 제출하면 강사 화면에 즉시 반영됩니다.</p></div>', unsafe_allow_html=True)
        st.button("정답 제출  →", type="primary", width="stretch")
    with c2:
        st.markdown('<div class="card"><div class="eyebrow">Instructor control</div><h3>현재 진행 상황</h3>', unsafe_allow_html=True)
        st.progress(0.68)
        st.markdown('<div style="display:flex;justify-content:space-between"><span class="tiny">18명 참여 중</span><span class="tiny lime">68%</span></div><br>', unsafe_allow_html=True)
        st.markdown('<div class="lesson-row"><span>정답 제출</span><b class="lime">12명</b></div><div class="lesson-row"><span>도움말 사용</span><b>4명</b></div><div class="lesson-row"><span>대기 중</span><b class="coral">2명</b></div></div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="eyebrow">Instant feedback board</div><h2>수업 현황 보드</h2>', unsafe_allow_html=True)
    a, b, c = st.columns(3)
    with a: metric("PRONUNCIATION", "92%", "평균 정확도", "lime")
    with b: metric("SENTENCE BUILD", "14 / 18", "완료한 학습자")
    with c: metric("CLASS ENERGY", "HIGH", "참여도 상승 중", "coral")


def dashboard():
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today = datetime.now()
    weekday = weekdays[today.weekday()]
    streak = st.session_state.get("streak", 1)
    completed_days = get_weekly_completed_days()
    weekly_percent = round(completed_days / 6 * 100)
    st.markdown(f'<div class="eyebrow">{interface_text("좋은 아침이에요, 미나 · ", "Good morning, Mina · ")}{weekday}</div><h1>오늘도 가볍게, <span class="lime">한 걸음 더.</span></h1><p class="sub">한국어는 중력처럼 쌓입니다. 오늘의 작은 연습이 내일의 자연스러운 문장이 됩니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    x, y, z = st.columns(3)
    with x: metric(interface_text("연속 학습", "CURRENT STREAK"), f"{streak:02d} " + interface_text("일", "day"), "오늘부터 시작해요", "lime")
    with y: metric(interface_text("주간 목표", "WEEKLY GOAL"), f"{weekly_percent}%", f"{completed_days}일 / 6일 학습 완료")
    with z: metric(interface_text("나의 레벨", "MY LEVEL"), "A2", "다음 레벨까지 240 XP")
    st.markdown("<br>", unsafe_allow_html=True)
    left, right = st.columns([1.25, 1])
    with left:
        if st.button(interface_text("다음 · 받침 발음 훈련  →", "NEXT · 받침 발음 훈련  →"), type="primary", key="next_lesson", width="stretch"):
            st.session_state.go_practice = True
            st.rerun()
        st.markdown(f'<div class="card"><div class="eyebrow">{interface_text("계속 학습하기", "Continue learning")}</div><h2>오늘의 10분 루틴</h2><div class="lesson-row"><span><span class="lesson-icon">◉</span><b>받침 발음 훈련</b><br><span class="tiny">발음 · 5분</span></span></div><div class="lesson-row"><span><span class="lesson-icon">✦</span><b>카페에서 주문하기</b><br><span class="tiny">회화 · 5분</span></span><span class="tiny">{interface_text("잠금 해제", "Unlock")}</span></div></div>', unsafe_allow_html=True)
        if st.button(interface_text("학습 시작하기  →", "Start learning  →"), type="primary", width="stretch"):
            st.session_state.go_practice = True
            st.rerun()
    with right:
        st.markdown(f'<div class="card"><div class="eyebrow">{interface_text("맞춤 학습 분석", "Personal insight")}</div><h2>이번 주의 발견</h2><p class="sub">조사 <b class="lime">‘은/는’</b>과 <b class="lime">‘이/가’</b>를 바꿔 쓰는 실수가 지난주보다 31% 줄었어요.</p><div class="progress"><div style="width:69%"></div></div><div style="display:flex;justify-content:space-between"><span class="tiny">정확도 향상</span><span class="tiny lime">+31%</span></div><br><span class="pill">맞춤 복습 6개</span><span class="pill">{interface_text("문법", "A2 grammar")}</span></div>', unsafe_allow_html=True)


def practice():
    st.markdown(f'<div class="eyebrow">{interface_text("맞춤 학습 · A2 문법", "Personal practice · A2 grammar")}</div><h1>문장 감각을 <span class="lime">깨워볼까요?</span></h1><p class="sub">최근 자주 틀린 조사 구분을 연습합니다. 정답보다 문장의 느낌에 먼저 집중해 보세요.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown('<div class="eyebrow">Question 03 / 06</div><h2>빈칸에 알맞은 조사를 골라 보세요.</h2><h2 style="margin-top:32px">오늘<span class="coral">▯</span> 날씨가 정말 좋아요.</h2>', unsafe_allow_html=True)
        choice = st.radio("조사 선택", ["은", "는", "이", "가"], horizontal=True, label_visibility="collapsed")
        if st.button("확인하기", type="primary"):
            if choice == "은": st.success("정답이에요! ‘오늘은’은 화제를 꺼낼 때 자연스럽습니다.")
            else: st.info("거의 다 왔어요. ‘오늘은’처럼 화제를 소개할 때는 ‘은’을 써요.")
    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1: metric("TODAY'S XP", "+40 XP", "정답 2개 연속", "lime")
    with c2: metric("ACCURACY", "83%", "지난 연습보다 +8%")
    with c3: metric("REVIEW QUEUE", "06", "남은 맞춤 문제", "coral")


def assignments():
    st.markdown(f'<div class="eyebrow">{interface_text("과제 · 강사 안내", "Assignments · from your instructor")}</div><h1>이번 주 <span class="lime">미션</span></h1><p class="sub">수업에서 배운 표현을 실제 장면에 연결해 보세요.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="card"><div class="lesson-row"><span><span class="lesson-icon">◌</span><b>카페 주문 롤플레이</b><br><span class="tiny">회화 녹음 · 마감 08.28</span></span><span class="pill active">IN PROGRESS</span></div><p class="sub">“아이스 아메리카노 한 잔 주세요.”를 자연스럽게 말하고 30초 음성으로 제출하세요.</p></div>', unsafe_allow_html=True)
    st.file_uploader("음성 파일 업로드", type=["mp3", "wav", "m4a"])
    if st.button("과제 제출하기  →", type="primary", width="stretch"): st.success("제출 준비가 완료되었습니다. 강사 피드백을 기다려 주세요.")


def practice():
    questions = [
        {
            "sentence": "오늘__ 날씨가 정말 좋아요.",
            "options": ["은", "는", "이", "가"],
            "answer": "은",
            "skill": "은/는",
            "explanation": "‘오늘은’은 오늘이라는 화제를 소개할 때 자연스럽습니다.",
        },
        {
            "sentence": "저__ 학생이에요.",
            "options": ["은", "는", "이", "가"],
            "answer": "는",
            "skill": "은/는",
            "explanation": "‘저는’은 자신을 화제로 소개할 때 사용합니다.",
        },
        {
            "sentence": "친구__ 내일 와요.",
            "options": ["은", "는", "이", "가"],
            "answer": "가",
            "skill": "이/가",
            "explanation": "‘친구가’는 행동의 주체를 나타냅니다.",
        },
        {
            "sentence": "김밥__ 먹어요.",
            "options": ["은", "는", "을", "를"],
            "answer": "을",
            "skill": "을/를",
            "explanation": "받침이 있는 ‘김밥’ 뒤에는 목적격 조사 ‘을’을 씁니다.",
        },
        {
            "sentence": "학교__ 가요.",
            "options": ["에", "에서", "은", "는"],
            "answer": "에",
            "skill": "에/에서",
            "explanation": "이동 목적지를 나타낼 때 ‘학교에’를 사용합니다.",
        },
        {
            "sentence": "도서관__ 공부해요.",
            "options": ["에", "에서", "이", "가"],
            "answer": "에서",
            "skill": "에/에서",
            "explanation": "행동이 일어나는 장소에는 ‘도서관에서’를 사용합니다.",
        },
    ]
    completed_days = get_weekly_completed_days()
    selected_unit_number = st.session_state.get("selected_unit_number", 1)
    current_unit = TEXTBOOK_UNITS[selected_unit_number]
    questions = TEXTBOOK_QUESTION_BANK[current_unit["number"]]
    today_key = datetime.now().date().isoformat()
    force_restart = st.session_state.pop("force_practice_restart", False)
    if (
        st.session_state.get("practice_date") != today_key
        or st.session_state.get("practice_unit_number") != current_unit["number"]
        or force_restart
    ):
        shuffled = questions.copy()
        random.Random(datetime.now().date().toordinal()).shuffle(shuffled)
        saved_session = None if force_restart else load_daily_session(today_key, current_unit["number"])
        st.session_state.practice_date = today_key
        st.session_state.practice_unit_number = current_unit["number"]
        st.session_state.practice_questions = shuffled
        st.session_state.practice_index = len(shuffled) if saved_session and saved_session["completed"] else 0
        st.session_state.practice_correct = saved_session["correct"] if saved_session else 0
        st.session_state.practice_xp = saved_session["xp"] if saved_session else 0
        st.session_state.practice_result = None
        st.session_state.practice_completed = bool(saved_session and saved_session["completed"])
        st.session_state.practice_wrong_skills = saved_session["wrong_skills"].split(",") if saved_session and saved_session["wrong_skills"] else []
        st.session_state.today_recorded = st.session_state.practice_completed or force_restart

    questions = st.session_state.practice_questions
    index = st.session_state.practice_index
    total = len(questions)
    if index >= total:
        st.session_state.practice_completed = True

    st.markdown(f'<div class="eyebrow">{interface_text("맞춤 학습", "Personal practice")} · {TEXTBOOK_TITLE} · {current_unit["number"]}단원</div><h1>문장 감각을 <span class="lime">깨워볼까요?</span></h1><p class="sub">{current_unit["title"]}의 핵심 문법과 기능을 연습합니다. 정답을 제출하면 바로 다음 단계로 이동합니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    if st.session_state.practice_completed:
        if not st.session_state.get("today_recorded") and not has_completed_session_on_date(today_key):
            st.session_state.completed_days = st.session_state.get("completed_days", 0) + 1
            yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
            if st.session_state.get("last_completed_date") == yesterday:
                st.session_state.streak = st.session_state.get("streak", 0) + 1
            else:
                st.session_state.streak = 1
            st.session_state.last_completed_date = today_key
            st.session_state.today_recorded = True
        st.session_state.daily_tasks["grammar"] = True
        st.session_state[f"review_grammar_done_{current_unit['number']}"] = True
        save_daily_session(today_key, current_unit["number"], completed=True)
        save_progress()
        st.success(f"오늘의 맞춤 학습을 완료했습니다! {st.session_state.practice_correct} / {total} 정답 · +{st.session_state.practice_xp} XP")
        st.markdown('<div class="card"><div class="eyebrow">Daily summary</div><h2>오늘의 학습 리포트</h2><p class="sub">틀린 문법 유형을 기록했어요. 설명을 다시 읽고 오늘 문제를 한 번 더 풀어 보세요.</p></div>', unsafe_allow_html=True)
        st.space("small")
        learning_button, builder_button, restart_button = st.columns(3)
        with learning_button:
            st.button("선택 단원 학습으로", key="practice_go_learning", icon=":material/menu_book:", width="stretch", on_click=navigate_to_page, args=("내 학습",))
        with builder_button:
            st.button("문장 조합으로", key="practice_go_builder", icon=":material/extension:", width="stretch", on_click=navigate_to_page, args=("문장 조합",))
        with restart_button:
            if st.button("오늘 문제 다시 풀기", key="restart_practice", icon=":material/replay:", width="stretch"):
                st.session_state.force_practice_restart = True
                st.rerun()
    else:
        question = questions[index]
        st.markdown(f'<div class="eyebrow">Question {index + 1:02d} / {total:02d} · {question["skill"]}</div>', unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<h2>빈칸에 알맞은 말을 골라 보세요.</h2>', unsafe_allow_html=True)
            st.markdown(f'<h2 style="margin-top:32px">{question["sentence"].replace("__", "<span class=\"coral\">▯</span>")}</h2>', unsafe_allow_html=True)
            choice = st.radio("답 선택", question["options"], horizontal=True, key=f"practice_choice_{index}", label_visibility="collapsed")
            result = st.session_state.practice_result
            completed_sentence = question["sentence"].replace("__", question["answer"])
            if result is None:
                if st.button("확인하기", type="primary", key=f"check_{index}"):
                    is_correct = choice == question["answer"]
                    st.session_state.practice_result = {"correct": is_correct, "choice": choice}
                    if is_correct:
                        st.session_state.practice_correct += 1
                        st.session_state.practice_xp += 20
                    else:
                        st.session_state.practice_wrong_skills.append(question["skill"])
                    st.session_state.total_answered = st.session_state.get("total_answered", 0) + 1
                    st.session_state.total_correct = st.session_state.get("total_correct", 0) + int(is_correct)
                    st.session_state.total_xp = st.session_state.get("total_xp", 0) + (20 if is_correct else 0)
                    save_daily_session(today_key, current_unit["number"])
                    save_progress()
                    st.rerun()
            else:
                if result["correct"]:
                    st.success(f"정답입니다! +20 XP\n\n완성 문장: {completed_sentence}")
                    st.info(f"왜 정답일까요? {question['explanation']}", icon=":material/lightbulb:")
                    if st.button("다음 문제  →", type="primary", key=f"next_{index}"):
                        st.session_state.practice_index += 1
                        st.session_state.practice_result = None
                        st.rerun()
                else:
                    st.info(f"선택한 답: ‘{result['choice']}’\n\n정답: ‘{question['answer']}’\n\n완성 문장: {completed_sentence}\n\n{question['explanation']}", icon=":material/lightbulb:")
                    if st.button("설명 확인 후 다시 풀기", type="primary", key=f"retry_{index}"):
                        st.session_state.practice_result = None
                        st.rerun()
        st.progress(index / total)

    completed = st.session_state.practice_correct
    answered = st.session_state.practice_index if not st.session_state.practice_completed else total
    c1, c2, c3 = st.columns(3)
    with c1: metric("TODAY'S XP", f"+{st.session_state.practice_xp} XP", f"{completed}개 정답", "lime")
    with c2: metric("ACCURACY", f"{round(completed / answered * 100) if answered else 0}%", f"{answered} / {total}개 진행")
    with c3: metric("REVIEW QUEUE", f"{len(st.session_state.practice_wrong_skills):02d}", "오답 유형 맞춤 복습", "coral")


def pronunciation_mission():
    target = "한국어"
    st.markdown(f'<div class="eyebrow">{interface_text("맞춤 학습 · 발음 연습", "Personal practice · pronunciation lab")}</div><h1>받침 발음을 <span class="lime">깨워볼까요?</span></h1><p class="sub">음성 파일을 올리면 OpenAI 음성 인식으로 실제 발화 결과를 확인합니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    left, right = st.columns([1.2, 1])
    with left:
        st.markdown(f'<div class="card"><div class="eyebrow">Mission 01 · batchim</div><h2>따라 말해 보세요</h2><div style="font-size:32px;font-weight:800;margin:25px 0">{target}</div><p class="sub">“한”의 ㄴ 받침과 “국어”로 이어지는 소리에 집중해 보세요.</p></div>', unsafe_allow_html=True)
        audio = st.file_uploader("음성 파일 업로드", type=["wav", "mp3", "m4a", "webm"], key="pronunciation_audio_real")
        if audio:
            st.audio(audio.getvalue(), format=audio.type or "audio/wav")
            if st.button("OpenAI로 발음 분석하기", type="primary", width="stretch"):
                transcript, error = transcribe_audio(audio)
                if error:
                    st.warning(error)
                else:
                    normalized_target = target.replace(" ", "")
                    normalized_transcript = transcript.replace(" ", "")
                    score = 100 if normalized_transcript == normalized_target else (82 if target in transcript else 60)
                    st.session_state.pronunciation_result = {"transcript": transcript, "score": score}
                    st.session_state.daily_tasks["speaking"] = True
                    st.rerun()
        result = st.session_state.get("pronunciation_result")
        if result:
            st.markdown(f'<div class="card"><div class="eyebrow">OpenAI transcription result</div><h2>일치도 <span class="lime">{result["score"]}%</span></h2><div class="progress"><div style="width:{result["score"]}%"></div></div><p class="sub">인식된 문장: <b>{result["transcript"]}</b></p><p class="tiny">현재 점수는 발화 내용 일치도입니다. 음소 단위 발음 평가는 별도 음성 분석 모델을 추가할 수 있습니다.</p></div>', unsafe_allow_html=True)
            if st.session_state.get("unlocked_stage", 1) < 2 and st.button("미션 완료하고 다음 단계 열기 →", type="primary", key="unlock_pronunciation_real"):
                st.session_state.unlocked_stage = 2
                st.session_state.total_xp = st.session_state.get("total_xp", 0) + 30
                save_progress()
                st.rerun()
    with right:
        metric("PRONUNCIATION", f"{result['score']}%" if result else "—", "실제 음성 인식 일치도", "lime")
        metric("NEXT UNLOCK", "A2 · 02", "카페에서 주문하기")
        st.markdown('<div class="card"><div class="eyebrow">API status</div><h3>분석 연결 상태</h3><p class="sub">{}</p></div>'.format("OpenAI 연결 준비 완료" if get_config_value("OPENAI_API_KEY") else "OPENAI_API_KEY 설정 필요"), unsafe_allow_html=True)


def sync_mode():
    local_submissions = st.session_state.get("sync_submissions", 12)
    remote_state = read_sync_state()
    if remote_state and isinstance(remote_state, dict):
        local_submissions = int(remote_state.get("submissions", local_submissions))
        st.session_state.sync_submissions = local_submissions
    st.markdown('<div class="eyebrow">Offline · live classroom</div><h1>Sync Mode <span class="lime">↗</span></h1><p class="sub">Firebase가 연결되면 강사와 학습자 기기의 제출 현황을 공유합니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2 = st.columns([1.35, 1])
    with c1:
        st.markdown('<div class="card"><div class="eyebrow">Live mission · sentence drop</div><h2>오늘의 문장 조합</h2><p class="sub">아래 단어를 조합해 올바른 문장을 만들어 보세요.</p><div style="margin:24px 0 13px"><span class="word">저는</span><span class="word">한국어를</span><span class="word">배워요</span></div></div>', unsafe_allow_html=True)
        if st.button("정답 제출 →", type="primary", key="sync_submit_real", width="stretch"):
            st.session_state.sync_submissions = min(18, local_submissions + 1)
            publish_sync_state(st.session_state.sync_submissions)
            st.success("제출되었습니다. Firebase 연결 시 다른 화면에도 반영됩니다.")
    with c2:
        participation = local_submissions / 18
        st.markdown('<div class="card"><div class="eyebrow">Instructor control</div><h3>현재 진행 상황</h3>', unsafe_allow_html=True)
        st.progress(participation)
        st.markdown(f'<div style="display:flex;justify-content:space-between"><span class="tiny">18명 중 {local_submissions}명 참여</span><span class="tiny lime">{round(participation * 100)}%</span></div><br><span class="pill active">{"FIREBASE CONNECTED" if firebase_configured() else "LOCAL DEMO"}</span></div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    a, b, c = st.columns(3)
    with a: metric("PRONUNCIATION", "92%", "평균 정확도", "lime")
    with b: metric("SENTENCE BUILD", f"{local_submissions} / 18", "완료한 학습자")
    with c: metric("CLASS ENERGY", "HIGH", "참여도 상승 중", "coral")


def teacher_dashboard():
    students = [
        {"학습자": "Mina", "레벨": "A2", "오늘": "완료", "정확도": "92%", "상태": "안정"},
        {"학습자": "Jisoo", "레벨": "A1", "오늘": "진행 중", "정확도": "76%", "상태": "도움 필요"},
        {"학습자": "Daniel", "레벨": "A2", "오늘": "미시작", "정확도": "—", "상태": "대기"},
        {"학습자": "Yuna", "레벨": "A2", "오늘": "완료", "정확도": "88%", "상태": "안정"},
        {"학습자": "Leo", "레벨": "A1", "오늘": "진행 중", "정확도": "64%", "상태": "도움 필요"},
    ]
    st.markdown('<div class="eyebrow">Instructor workspace · class 01</div><h1>수업을 한눈에, <span class="lime">다음 행동까지.</span></h1><p class="sub">학습자의 막힘을 빠르게 발견하고, 다음 미션을 배포하는 강사용 운영 화면입니다.</p>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    a, b, c = st.columns(3)
    with a: metric("TODAY ACTIVE", "18 / 24", "75% 참여 중", "lime")
    with b: metric("NEEDS ATTENTION", "02", "조사·받침 복습 추천", "coral")
    with c: metric("CLASS ACCURACY", "84%", "지난 수업보다 +6%")
    st.markdown("<br>", unsafe_allow_html=True)
    left, right = st.columns([1.25, 1])
    with left:
        st.markdown('<div class="card"><div class="eyebrow">Live class control</div><h2>다음 미션 배포</h2><p class="sub">모든 학습자 화면에 표시할 활동을 선택하세요.</p>', unsafe_allow_html=True)
        mission = st.selectbox("미션", ["문장 조합 · 카페 주문", "받침 발음 · 한국어", "조사 복습 · 은/는"], label_visibility="collapsed")
        if st.button("교실에 미션 보내기  →", type="primary", width="stretch"):
            st.session_state.teacher_mission = mission
            st.session_state.sync_submissions = 0
            publish_sync_state(0)
            st.success(f"‘{mission}’ 미션을 배포했습니다.")
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        mission_label = st.session_state.get("teacher_mission", "아직 배포된 미션 없음")
        st.markdown(f'<div class="card"><div class="eyebrow">Current mission</div><h2>{mission_label}</h2><div class="progress"><div style="width:67%"></div></div><div style="display:flex;justify-content:space-between"><span class="tiny">제출 16 / 24</span><span class="tiny lime">67%</span></div><br><span class="pill active">LIVE</span></div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="eyebrow">Learner signals</div><h2>학습자 현황</h2>', unsafe_allow_html=True)
    st.dataframe(students, width="stretch", hide_index=True)
    st.caption("‘도움 필요’ 학습자는 맞춤 복습 큐와 발음 미션을 먼저 확인해 주세요.")


def learner_level(total_xp):
    """Turn accumulated practice into a friendly, visible learner level."""
    levels = [
        (0, interface_text("새싹 학습자", "New Learner"), interface_text("첫 문장을 만들어 보는 단계", "Build your first Korean sentence")),
        (100, interface_text("꾸준한 탐험가", "Steady Explorer"), interface_text("짧은 문장을 스스로 조립하는 단계", "Build short Korean sentences independently")),
        (250, interface_text("대화 준비생", "Conversation Starter"), interface_text("일상 표현을 연결하는 단계", "Connect expressions for everyday conversations")),
        (500, interface_text("한국어 메이커", "Korean Communicator"), interface_text("배운 표현을 실제 상황에 쓰는 단계", "Use learned expressions in real situations")),
    ]
    current = levels[0]
    next_level = None
    for level in levels:
        if total_xp >= level[0]:
            current = level
        elif next_level is None:
            next_level = level
    if next_level is None:
        return current[1], current[2], 1.0, None
    progress = (total_xp - current[0]) / max(1, next_level[0] - current[0])
    return current[1], current[2], min(1.0, progress), next_level


def render_motivation_header(progress, total_xp, streak, completed_days):
    """A compact motivation layer that gives the learner a reason to return today."""
    level_name, level_note, level_progress, next_level = learner_level(total_xp)
    st.markdown(interface_text("## 오늘의 한국어 루틴", "## Today’s Korean Routine"))
    st.caption(interface_text("길게 공부하지 않아도 괜찮아요. 오늘은 10분, 작은 성공 세 번이면 충분합니다.", "A long study session is not necessary. Ten minutes and three small wins are enough for today."))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(interface_text("오늘의 목표", "Today’s Goal"), f"{completed_days}/3", interface_text("미션 완료", "Missions completed"))
    with c2:
        st.metric(interface_text("연속 학습", "Study Streak"), interface_text(f"{streak}일", f"{streak} days"), interface_text("기록을 이어 가요", "Keep your streak going"))
    with c3:
        st.metric(interface_text("나의 XP", "My XP"), f"{total_xp} XP", interface_text("학습할수록 쌓여요", "Earn more as you learn"))
    with c4:
        st.metric(interface_text("현재 레벨", "Current Level"), level_name, level_note)
    st.progress(level_progress, text=f"{interface_text('다음 레벨 진행도', 'Progress to next level')} {int(level_progress * 100)}%")
    if next_level:
        st.caption(interface_text(f"다음 레벨까지 {next_level[0] - total_xp} XP 남았어요. 지금 한 문제만 더 풀어 볼까요?", f"You need {next_level[0] - total_xp} more XP to reach the next level. Try one more question now."))
    else:
        st.caption(interface_text("최고 레벨에 도달했어요. 이제 배운 표현으로 나만의 문장을 만들어 보세요.", "You reached the highest level. Now create your own Korean sentences with the expressions you learned."))


def render_daily_quest():
    """Show three concrete actions instead of an abstract progress dashboard."""
    st.markdown("### 오늘의 빠른 연습")
    st.caption("단원 전체 학습 전에 부담 없이 하는 10분 핵심 연습입니다. 단원 학습은 아래 5단계 순서로 진행합니다.")
    tasks = [
        ("vocab", "워밍업", "핵심 어휘 3개 소리 내어 읽기", "+5 XP"),
        ("grammar", "연습", "오늘의 문법 문제 3개 풀기", "+20 XP"),
        ("speaking", "사용", "배운 표현으로 한 문장 말해 보기", "+25 XP"),
    ]
    cols = st.columns(3)
    for column, (key, label, description, reward) in zip(cols, tasks):
        with column:
            done = st.session_state.daily_tasks.get(key, False)
            with st.container(border=True):
                if done:
                    st.badge("완료", color="green")
                else:
                    st.badge("추천", color="blue")
                st.markdown(f"#### {label}")
                st.write(description)
                st.caption(reward)
                if not done and st.button("시작하기", key=f"quest_{key}", type="primary" if key == "grammar" else "secondary"):
                    if key == "vocab":
                        st.session_state.daily_tasks[key] = True
                        st.session_state.total_xp = st.session_state.get("total_xp", 0) + 5
                        st.session_state[f"vocab_rewarded_{st.session_state.get('selected_unit_number', 1)}"] = True
                        save_progress()
                        st.toast("워밍업 완료! +5 XP", icon=":material/star:")
                        st.rerun()
                    elif key == "speaking":
                        st.session_state.go_pronunciation = True
                        st.rerun()
                    else:
                        st.session_state.go_practice = True
                        st.rerun()
                elif done:
                    st.caption("오늘 완료했어요")


def render_unit_learning_order(current_unit):
    """Make the intended unit sequence visible before learners start clicking around."""
    unit_number = current_unit["number"]
    section = LESSON_SECTION_CONTENT[unit_number]
    english = english_support_enabled()
    language = st.session_state.get("app_language", "한국어")
    unit_step_summaries = {
        1: ("나라·직업 익히기", "이에요·예요 익히기", "은·는 익히기", "인사 정보 확인하기", "자기소개 말하기"),
        2: ("숫자·전화번호 읽기", "이·가 익히기", "아니에요 익히기", "전화번호 확인하기", "연락처 묻고 쓰기"),
        3: ("물건·위치 어휘 익히기", "이·그·저 익히기", "에 있다·없다 익히기", "물건 위치 찾기", "주인·위치 확인하기"),
        4: ("활동 어휘 익히기", "-아요·어요 익히기", "을·를 익히기", "오늘 한 일 묻고 답하기", "문자 읽고 오늘 일 쓰기"),
        5: ("장소·식품 어휘 익히기", "에 가다 익히기", "하고 익히기", "마트 대화하기", "백화점 쇼핑 쓰기"),
        6: ("고유어 수 익히기", "단위 명사 익히기", "-(으)세요 익히기", "과일 가게 대화하기", "편의점 목록 쓰기"),
        7: ("시간 어휘 익히기", "시간의 에 익히기", "몇 시 표현 익히기", "시계·활동 연결하기", "시간표 말하기"),
        8: ("날씨 어휘 익히기", "안 부정 표현 익히기", "ㅂ 불규칙 익히기", "날씨 표현 연결하기", "오늘 날씨 말하기"),
        9: ("장소·활동 익히기", "에서 익히기", "과거 표현 익히기", "과거 활동 연결하기", "어제 일 말하기"),
        10: ("제안 어휘 익히기", "을까요 익히기", "으러 가다 익히기", "제안 표현 확인하기", "약속 정해 말하기"),
    }
    summaries = unit_step_summaries[unit_number]
    if english:
        summaries = (
            "Learn the key words",
            f"Practice {section['grammar1']}",
            f"Practice {section['grammar2']}",
            "Use the forms in context",
            "Create your own response",
        )
    elif language != "한국어":
        summaries = tuple(
            interface_text(korean_summary, english_summary)
            for korean_summary, english_summary in zip(
                summaries,
                ("Learn the key words", f"Practice {section['grammar1']}", f"Practice {section['grammar2']}", "Use the forms in context", "Create your own response"),
            )
        )
    intro = interface_text(
        "아래 5단계를 순서대로 진행하세요. 1단계의 어휘와 표현을 마친 뒤 2단계로 넘어갑니다.",
        "Follow the five steps in order. Complete Vocabulary & expressions before moving to Grammar 1.",
    )
    title_by_language = {
        "日本語": f"ユニット {current_unit['number']} · 5ステップのレッスン",
        "中文": f"第 {current_unit['number']} 单元 · 五步课程",
        "Tiếng Việt": f"Bài {current_unit['number']} · Bài học 5 bước",
    }
    learning_title = title_by_language.get(language, f"Unit {current_unit['number']} · 5-step lesson" if english else f"{current_unit['number']}단원 · 단원 학습 5단계")
    st.markdown(
        f'<div class="unit-learning-title"><span>{learning_title}</span></div>'
        f'<div class="unit-learning-intro">{intro}</div>',
        unsafe_allow_html=True,
    )
    steps = [
        ("1", "어휘와 표현", summaries[0]),
        ("2", f"문법 1 · {section['grammar1']}", summaries[1]),
        ("3", f"문법 2 · {section['grammar2']}", summaries[2]),
        ("4", "활동 1", summaries[3]),
        ("5", "활동 2", summaries[4]),
    ]
    columns = st.columns(5)
    for column, (number, title, description) in zip(columns, steps):
        with column:
            with st.container(border=True, height=150):
                stage_label = f"Step {number}" if english else (f"Bước {number}" if language == "Tiếng Việt" else f"{number}단계")
                base_title = title.split(" · ", 1)[0]
                english_title = {
                    "어휘와 표현": "Vocabulary & expressions",
                    "문법 1": "Grammar 1",
                    "문법 2": "Grammar 2",
                    "활동 1": "Activity 1",
                    "활동 2": "Activity 2",
                }[base_title]
                if english:
                    grammar_name = title.split(" · ", 1)[1] if " · " in title else ""
                    title = f"{english_title} · {grammar_name}" if grammar_name else english_title
                elif language != "한국어":
                    grammar_name = title.split(" · ", 1)[1] if " · " in title else ""
                    localized_base = interface_text(base_title, english_title)
                    title = f"{localized_base} · {grammar_name}" if grammar_name else localized_base
                st.markdown(f"### {stage_label}")
                st.markdown(f"**{title}**")
                st.markdown(f'<div class="unit-step-summary">{description}</div>', unsafe_allow_html=True)
    st.space("small")


def render_unit2_place_phone_quiz():
    """Render the place-phone quiz without rerunning the full lesson page."""
    st.markdown(interface_text("**1. 다음 장소의 전화번호를 읽고 물음에 답해 보세요.**", "**1. Read each place’s phone number and answer the question**"))
    st.caption(interface_text("장소 이름과 안내판의 전화번호를 확인한 뒤 알맞은 번호를 선택하세요.", "Check the place name and phone number on each sign, then choose the correct number."))
    place_phone_cards = [
        ("한국 카페", "cafe-building-4f.png", "02-1512-8942", "공이-일오일이-팔구사이", (230, 170)),
        ("세종학당", "sejong-institute-building.png", "02-3276-0700", "공이-삼이칠육-공칠공공", (230, 170)),
    ]
    place_phone_columns = st.columns(2)
    place_phone_answers = []
    unit2_place_choose_label = interface_text("선택하세요", "Choose")
    for index, (place_name, image_name, correct_phone, phone_reading, image_size) in enumerate(place_phone_cards):
        with place_phone_columns[index]:
            with st.container(border=True):
                st.image(
                    fit_image_to_canvas(
                        Path(__file__).with_name("assets") / "people" / image_name,
                        canvas_size=(260, 190),
                        image_size=image_size,
                    ),
                    width=260,
                )
                st.markdown(f"**{place_name}**")
                render_learning_info(f"{interface_text('안내 전화', 'Information line')} · {correct_phone}", icon=":material/call:")
                st.caption(f"{interface_text('한국어로 읽기', 'Read in Korean')} · {phone_reading}")
            selected_phone = st.selectbox(
                interface_text(f"{place_name} 전화번호는 몇 번이에요?", f"What is the phone number for {place_name}?"),
                [unit2_place_choose_label] + [card[2] for card in place_phone_cards],
                key=f"unit2_activity2_place_phone_{index}",
            )
            place_phone_answers.append(selected_phone)

    place_answers_ready = all(answer != unit2_place_choose_label for answer in place_phone_answers)
    place_results = st.session_state.get("unit2_activity2_place_results")
    place_button_columns = st.columns([1, 1, 2])
    with place_button_columns[0]:
        if st.button(interface_text("1번 답 확인", "Check Activity 1 answers"), key="unit2_activity2_place_check", type="primary", disabled=not place_answers_ready, width="stretch"):
            st.session_state["unit2_activity2_place_results"] = [
                selected == place_phone_cards[index][2]
                for index, selected in enumerate(place_phone_answers)
            ]
            place_results = st.session_state["unit2_activity2_place_results"]
    with place_button_columns[1]:
        if place_results is not None:
            st.button(interface_text("확인 닫기", "Close feedback"), key="unit2_activity2_place_result_close", type="secondary", width="stretch", on_click=clear_session_state_key, args=("unit2_activity2_place_results",))

    if place_results is not None:
        if all(place_results):
            st.session_state["unit2_activity2_place_passed"] = True
            render_learning_success(interface_text("두 문제 모두 정답이에요.", "Both answers are correct."), icon=":material/check_circle:")
        else:
            st.session_state["unit2_activity2_place_passed"] = False
            incorrect_places = [
                place_phone_cards[index]
                for index, is_correct in enumerate(place_results)
                if not is_correct
            ]
            places_to_review = ", ".join(place[0] for place in incorrect_places)
            render_learning_warning(interface_text("다시 확인할 장소: " + places_to_review, "Check these places again: " + places_to_review))
            for place_name, _, correct_phone, phone_reading, _ in incorrect_places:
                render_learning_info(f"{place_name} {interface_text('정답', 'answer')}: {correct_phone} ({phone_reading})")

    return bool(
        place_results is not None
        and len(place_results) == len(place_phone_answers)
        and all(
            selected == place_phone_cards[index][2]
            for index, selected in enumerate(place_phone_answers)
        )
    )


def render_learning_lock(message):
    st.markdown(f"<div class='learning-lock'><b>{interface_text('잠금', 'Locked')}</b>{message}</div>", unsafe_allow_html=True)


def remember_reference_image(image_name):
    """Remember the current unit image so every related question can reopen it in place."""
    global ACTIVE_REFERENCE_IMAGE
    image_path = Path(__file__).with_name("assets") / "units" / image_name
    ACTIVE_REFERENCE_IMAGE = str(image_path)
    return image_path


def render_unit_visual(unit_number, image_name, caption):
    """Show an original unit illustration in a consistent, readable canvas."""
    st.image(remember_reference_image(image_name), width="stretch")
    st.caption(caption)


def render_choice_set(
    unit_number,
    stage,
    questions,
    completion_key=None,
    dialogue_layout=False,
    scroll_below_picture=False,
    hide_reference_button=False,
    question_images=None,
):
    """Render beginner-friendly selection questions and store completion after a perfect check."""
    selections = []
    question_area = (
        st.container(height=360, border=True, key=f"unit{unit_number}_{stage}_question_scroll")
        if scroll_below_picture
        else nullcontext()
    )
    with question_area:
        for index, (prompt, options, answer, hint) in enumerate(questions):
            if ACTIVE_REFERENCE_IMAGE and not scroll_below_picture and not hide_reference_button:
                with st.popover(interface_text(f"{index + 1}번 참고 그림 다시 보기", f"View reference picture for Question {index + 1}"), width="stretch"):
                    st.image(ACTIVE_REFERENCE_IMAGE, width="stretch")
                    st.caption(interface_text("그림을 확인한 뒤 창을 닫으면 현재 문제 위치에서 계속할 수 있어요.", "Close the picture after checking it to continue from this question."))
            question_content = nullcontext()
            if question_images:
                with st.container(border=True):
                    image_column, content_column = st.columns([1, 1.6], vertical_alignment="center")
                    with image_column:
                        st.image(question_images[index], width=200)
                    question_content = content_column
            with question_content:
                if dialogue_layout:
                    dialogue_lines = "<br>".join(html.escape(line) for line in prompt.splitlines())
                    st.markdown(
                        f'<div style="display:flex;gap:.25rem;align-items:flex-start;margin:.65rem 0 .35rem;font-size:.875rem;font-weight:400">'
                        f'<div style="flex:0 0 auto;font-weight:400">{index + 1})</div>'
                        f'<div style="flex:1 1 auto;font-weight:400;line-height:1.75">{dialogue_lines}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                selections.append(
                    st.selectbox(
                        f"{index + 1}) {prompt}" if not dialogue_layout else interface_text(f"{index + 1}번 답 선택", f"Choose answer {index + 1}"),
                        options,
                        index=None,
                        placeholder=interface_text("선택하세요", "Choose"),
                        key=f"unit{unit_number}_{stage}_choice_{index}",
                        label_visibility="visible" if not dialogue_layout else "collapsed",
                    )
                )
    ready = all(selection is not None for selection in selections)
    result_key = f"unit{unit_number}_{stage}_result"
    checked_key = f"unit{unit_number}_{stage}_checked_selections"
    feedback_key = f"unit{unit_number}_{stage}_feedback_visible"
    results = st.session_state.get(result_key)
    results_are_current = st.session_state.get(checked_key) == selections
    if results is not None and not results_are_current:
        st.session_state[feedback_key] = False
    feedback_visible = bool(st.session_state.get(feedback_key, False)) and results_are_current
    if st.button(
        interface_text("설명 닫기", "Hide explanation")
        if feedback_visible
        else interface_text("정답 확인", "Check answers"),
        key=f"unit{unit_number}_{stage}_check",
        type="primary",
        disabled=not ready,
    ):
        if feedback_visible:
            st.session_state[feedback_key] = False
        else:
            results = [selection == question[2] for selection, question in zip(selections, questions)]
            st.session_state[result_key] = results
            st.session_state[checked_key] = list(selections)
            st.session_state[feedback_key] = True
            if all(results) and completion_key:
                st.session_state[completion_key] = True
        st.rerun()
    results = st.session_state.get(result_key)
    results_are_current = st.session_state.get(checked_key) == selections
    feedback_visible = bool(st.session_state.get(feedback_key, False)) and results_are_current
    if results is not None and results_are_current and feedback_visible:
        if all(results):
            render_learning_success(interface_text(f"{len(results)}문제를 모두 정확하게 풀었어요!", f"You answered all {len(results)} questions correctly!"), icon=":material/check_circle:")
        else:
            render_learning_warning(interface_text(f"{sum(results)}/{len(results)}개가 맞아요. 설명을 읽고 다시 선택해 보세요.", f"{sum(results)}/{len(results)} are correct. Read the explanations and try again."))
        for index, (is_correct, question) in enumerate(zip(results, questions)):
            if not is_correct:
                feedback = highlight_learning_text(
                    interface_text(f"{index + 1}번 정답: {question[2]} · {question[3]}", f"Question {index + 1}: {question[2]} · {question[3]}"), unit_number
                )
                st.markdown(f'<div class="grammar-feedback">{feedback}</div>', unsafe_allow_html=True)
    elif completion_key:
        st.session_state.pop(completion_key, None)
    return bool(results) and results_are_current and all(results)


def render_picture_word_cards(
    unit_number,
    stage,
    questions,
    question_images,
    dialogue_layout=False,
    single_image_width=120,
):
    """Render compact picture questions with independent check-and-close feedback."""
    completed_questions = []
    for index, ((prompt, options, answer, hint), question_image) in enumerate(zip(questions, question_images)):
        choice_key = f"unit{unit_number}_{stage}_choice_{index}"
        checked_key = f"unit{unit_number}_{stage}_card_checked_{index}"
        result_key = f"unit{unit_number}_{stage}_card_result_{index}"
        feedback_key = f"unit{unit_number}_{stage}_card_feedback_{index}"
        with st.container(border=True):
            paired_images = isinstance(question_image, (list, tuple))
            column_widths = [0.75, 2.25] if paired_images else (
                [0.65, 2.35] if single_image_width > 120 else [0.45, 2.55]
            )
            image_column, question_column = st.columns(column_widths, gap="small", vertical_alignment="center")
            with image_column:
                if isinstance(question_image, str):
                    st.markdown(
                        f'<div style="font-size:4.5rem;line-height:1;text-align:center;padding:.5rem 0">{html.escape(question_image)}</div>',
                        unsafe_allow_html=True,
                    )
                elif paired_images:
                    paired_image_columns = st.columns(len(question_image), gap="small")
                    for paired_column, paired_image in zip(paired_image_columns, question_image):
                        with paired_column:
                            st.image(paired_image, width=90)
                else:
                    st.image(question_image, width=single_image_width)
            with question_column:
                if dialogue_layout:
                    dialogue_lines = "<br>".join(html.escape(line) for line in prompt.splitlines())
                    st.markdown(
                        f'<div style="display:flex;gap:.25rem;align-items:flex-start;margin:.2rem 0 .35rem;font-size:.9rem">'
                        f'<div style="flex:0 0 auto">{index + 1})</div>'
                        f'<div style="flex:1 1 auto;line-height:1.7">{dialogue_lines}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                choice = st.selectbox(
                    f"{index + 1}) {prompt}" if not dialogue_layout else interface_text(f"{index + 1}번 대화의 알맞은 답", f"Choose the answer for dialogue {index + 1}"),
                    options,
                    index=None,
                    placeholder=interface_text("선택하세요", "Choose"),
                    key=choice_key,
                    label_visibility="visible" if not dialogue_layout else "collapsed",
                )
                feedback_current = st.session_state.get(checked_key) == choice
                if not feedback_current:
                    st.session_state[feedback_key] = False
                feedback_visible = bool(st.session_state.get(feedback_key, False)) and feedback_current
                if st.button(
                    interface_text("설명 닫기", "Hide explanation")
                    if feedback_visible
                    else interface_text("정답 확인", "Check answer"),
                    key=f"unit{unit_number}_{stage}_card_check_{index}",
                    type="primary",
                    disabled=choice is None,
                ):
                    if feedback_visible:
                        st.session_state[feedback_key] = False
                    else:
                        st.session_state[checked_key] = choice
                        st.session_state[result_key] = choice == answer
                        st.session_state[feedback_key] = True
                    st.rerun()
                feedback_current = st.session_state.get(checked_key) == choice
                feedback_visible = bool(st.session_state.get(feedback_key, False)) and feedback_current
                if feedback_visible:
                    if st.session_state.get(result_key, False):
                        render_learning_success(interface_text("정답이에요!", "Correct!"), icon=":material/check_circle:")
                    else:
                        render_learning_warning(
                            interface_text(f"다시 생각해 보세요. {hint}", f"Try again. {hint}"),
                            icon=":material/lightbulb:",
                        )
        completed_questions.append(
            st.session_state.get(result_key, False)
            and st.session_state.get(checked_key) == choice
        )
    if all(completed_questions):
        render_learning_success(
            interface_text(f"{len(questions)}개 그림에 알맞은 말을 모두 찾았어요!", f"You matched all {len(questions)} pictures correctly!"),
            icon=":material/celebration:",
        )
    return all(completed_questions)


def render_unit3_grammar2():
    st.markdown(interface_text("**핵심 대화를 먼저 읽어 보세요.**", "**Read the key Korean dialogues first.**"))
    dialogue_columns = st.columns(2)
    with dialogue_columns[0]:
        render_learning_info("가: 책이 어디에 있어요?\n\n나: 책상 위에 있어요.", icon=":material/forum:")
    with dialogue_columns[1]:
        render_learning_info("가: 유진 씨가 집에 있어요?\n\n나: 아니요. 집에 없어요. 교실에 있어요.", icon=":material/forum:")
    presence_questions = [
        ("컴퓨터가 교실에 있어요?", ["네, 교실에 있어요.", "아니요, 교실에 없어요."], "네, 교실에 있어요.", "노트북 컴퓨터가 왼쪽 책상 위에 있어요."),
        ("가방이 교실에 있어요?", ["네, 교실에 있어요.", "아니요, 교실에 없어요."], "네, 교실에 있어요.", "빨간 가방이 의자 옆에 있어요."),
        ("시계가 교실에 있어요?", ["네, 교실에 있어요.", "아니요, 교실에 없어요."], "네, 교실에 있어요.", "둥근 시계가 칠판 위에 있어요."),
        ("피아노가 교실에 있어요?", ["네, 교실에 있어요.", "아니요, 교실에 없어요."], "아니요, 교실에 없어요.", "그림에는 피아노가 없어요."),
        ("유진 씨가 교실에 있어요?", ["네, 교실에 있어요.", "아니요, 교실에 없어요."], "네, 교실에 있어요.", "유진 씨가 칠판 앞에 서 있어요."),
    ]
    location_questions = [
        ("시계가 어디에 있어요?", ["칠판 위에 있어요.", "책상 아래에 있어요.", "가방 안에 있어요."], "칠판 위에 있어요.", "시계는 칠판 위쪽 벽에 있어요."),
        ("가방이 어디에 있어요?", ["의자 옆에 있어요.", "칠판 위에 있어요.", "책장 안에 있어요."], "의자 옆에 있어요.", "빨간 가방은 오른쪽 의자 옆에 있어요."),
        ("책이 어디에 있어요?", ["학생 책상 위에 있어요.", "의자 아래에 있어요.", "창문 밖에 있어요."], "학생 책상 위에 있어요.", "초록색 책은 오른쪽 학생 책상 위에 있어요."),
        ("유진 씨가 어디에 있어요?", ["칠판 앞에 있어요.", "책상 아래에 있어요.", "교실 밖에 있어요."], "칠판 앞에 있어요.", "유진 씨는 교실 안, 칠판 앞에 서 있어요."),
        ("컴퓨터가 어디에 있어요?", ["왼쪽 책상 위에 있어요.", "의자 아래에 있어요.", "칠판 뒤에 있어요."], "왼쪽 책상 위에 있어요.", "노트북 컴퓨터는 교실 왼쪽 책상 위에 있어요."),
    ]
    st.markdown(
        """
        <style>
        div.st-key-unit3_grammar2_reference { margin-top:-16px; }
        div.st-key-unit3_grammar2_reference [data-testid="stHorizontalBlock"] {
            display: grid;
            grid-template-columns: 500px 500px;
            gap: 16px !important;
            align-items: start;
        }
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:first-child {
            width: 500px !important;
            min-width: 500px !important;
            height: 334px !important;
        }
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2) {
            width: 500px !important;
            min-width: 500px !important;
            height: 334px !important;
        }
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2)
        [data-testid="stVerticalBlock"] {
            gap: 14px !important;
        }
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2) {
            position: relative;
        }
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2)
        [data-testid="stElementContainer"]:has(.unit3-person-label) {
            position: relative !important;
            left: auto;
            bottom: auto;
            top: -3mm !important;
            width: 100%;
        }
        div.st-key-unit3_grammar2_reference .unit3-person-label {
            margin: 8px 0 0;
            font-weight: 700;
        }
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlertContentSuccess"] p,
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlertContentWarning"] p,
        div.st-key-unit3_grammar2_reference [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlertContentInfo"] p {
            white-space: nowrap;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit3_grammar2_reference"):
        picture_column, guide_column = st.columns([1, 1.4], vertical_alignment="top")
        with picture_column:
            st.image(
                fit_image_to_canvas(
                    remember_reference_image("unit3-grammar2-classroom.png"),
                    canvas_size=(500, 334), image_size=(500, 334),
                ),
                width=500,
            )
        with guide_column:
            render_learning_success(interface_text("**있어요:** 사람이나 물건이 그 장소에 있어요.  \n예: 교실에 컴퓨터가 있어요.", "**있어요:** Shows that a person or object is in a place.  \nExample: 교실에 컴퓨터가 있어요."))
            render_learning_warning(interface_text("**없어요:** 사람이나 물건이 그 장소에 없어요.  \n예: 교실에 피아노가 없어요.", "**없어요:** Shows that a person or object is not in a place.  \nExample: 교실에 피아노가 없어요."))
            render_learning_info(interface_text("**어디에 있어요?:** 장소를 물을 때 사용해요.  \n예: 가방이 어디에 있어요?", "**어디에 있어요?:** Use this to ask where someone or something is.  \nExample: 가방이 어디에 있어요?"))
            st.markdown('<div class="unit3-person-label">그림 속 사람 · 유진 씨</div>', unsafe_allow_html=True)

    activity1_column, activity2_column = st.columns(2, gap="large", vertical_alignment="top")
    with activity1_column:
        st.markdown(interface_text("### 1. 교실에 무엇이 있는지 확인하세요.", "### 1. Check what is in the classroom"))
        st.caption(interface_text("그림에서 사람과 물건을 찾아 알맞은 대답을 선택하세요.", "Find the people and objects in the picture, then choose the correct Korean answer."))
        presence_done = render_choice_set(3, "grammar2_presence", presence_questions, scroll_below_picture=True)
    with activity2_column:
        st.markdown(interface_text("### 2. 그림을 보고 물건과 사람의 위치에 알맞은 문장을 선택해 보세요.", "### 2. Choose the sentence that matches each location"))
        st.caption(interface_text("왼쪽 그림을 보고 ‘어디에 있어요?’에 알맞은 위치 문장으로 대답하세요.", "Use the picture to answer 어디에 있어요? with the correct Korean location sentence."))
        location_done = render_choice_set(3, "grammar2_location", location_questions, scroll_below_picture=True)
    if presence_done and location_done:
        st.session_state["grammar2_done_3"] = True
        render_learning_success(interface_text("문법 2의 1번과 2번 활동을 모두 완료했어요!", "You completed both Grammar 2 activities!"), icon=":material/check_circle:")
    else:
        st.session_state.pop("grammar2_done_3", None)


def render_unit3_grammar1_intro():
    """Teach 이/그/저 with one compact distance scene and two guided activities."""
    render_learning_info(interface_text(
        "‘이’는 말하는 사람에게 가까이 있는 사람이나 물건을 가리켜요.\n\n‘그’는 듣는 사람에게 가까이 있는 사람이나 물건을 가리켜요.\n\n‘저’는 두 사람 모두에게서 멀리 있는 사람이나 물건을 가리켜요.",
        "**이** points to something near the speaker.\n\n**그** points to something near the listener.\n\n**저** points to something far from both people."),
        icon=":material/school:",
    )
    st.markdown(interface_text("**대화로 먼저 익혀 보세요**", "**Learn the pattern through these Korean dialogues**"))
    dialogue_examples = st.columns(2)
    with dialogue_examples[0]:
        render_learning_info("가: 이 책은 누구 책이에요?\n\n나: 제 책이에요.", icon=":material/forum:")
    with dialogue_examples[1]:
        render_learning_info("가: 저 의자는 교실 의자예요?\n\n나: 네, 교실 의자예요.", icon=":material/forum:")
    st.space("small")
    st.markdown(
        """
        <style>
        div.st-key-unit3_grammar1_rule_pair { margin-top:-16px; }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stHorizontalBlock"] {
            display: block !important;
            position: relative;
            min-height: 334px;
        }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:first-child {
            width: 500px !important;
            min-width: 500px !important;
        }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:first-child
        [data-testid="stImage"] img {
            width: 500px !important;
            height: 334px !important;
            object-fit: contain;
            display: block;
        }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:nth-child(2)
        {
            position: absolute !important;
            left: 516px;
            top: 0;
            width: 500px !important;
            min-width: 500px !important;
            height: 334px !important;
        }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:nth-child(2)
        [data-testid="stVerticalBlock"] {
            height: 334px !important;
            justify-content: space-between !important;
        }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlert"] {
            width: 500px !important;
        }
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlertContentSuccess"] p,
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlertContentInfo"] p,
        div.st-key-unit3_grammar1_rule_pair [data-testid="stColumn"]:nth-child(2)
        [data-testid="stAlertContentWarning"] p {
            white-space: normal;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit3_grammar1_rule_pair"):
        picture_column, rule_column = st.columns([1, 1.4], vertical_alignment="center")
        with picture_column:
            st.image(
                fit_image_to_canvas(
                    remember_reference_image("unit3-demonstratives.png"),
                    canvas_size=(500, 334), image_size=(500, 334),
                ),
                width=500,
            )
        with rule_column:
            render_learning_success("**‘이’는 말하는 사람에게 가까이**\n\n여자 가까이 → 이 책")
            render_learning_info("**‘그’는 듣는 사람에게 가까이**\n\n남자 가까이 → 그 가방")
            render_learning_warning("**‘저’는 두 사람 모두에게서 멀리**\n\n두 사람에게서 멀리 → 저 의자")
    st.markdown(interface_text("### 1. 그림을 보고 알맞은 말을 선택해 보세요.", "### 1. Look at the picture and choose the correct Korean expression"))
    first_questions = [
        ("여자가 들고 있는 책", ["이 책", "그 책", "저 책"], "이 책", "책은 말하는 여자 가까이에 있어요."),
        ("남자 옆에 있는 가방", ["이 가방", "그 가방", "저 가방"], "그 가방", "가방은 듣는 남자 가까이에 있어요."),
        ("두 사람에게서 멀리 있는 의자", ["이 의자", "그 의자", "저 의자"], "저 의자", "의자는 두 사람 모두에게 멀리 있어요."),
    ]
    first_done = render_choice_set(3, "grammar1_picture", first_questions, hide_reference_button=True)
    st.divider()
    st.markdown(interface_text("### 2. 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Complete each Korean dialogue and read it aloud"))
    render_learning_info("가: 이 책은 누구 책이에요?\n\n나: 제 책이에요.", icon=":material/forum:")
    second_questions = [
        ("가: ___ 책은 마리 씨 책이에요? (말하는 마리 가까이)  \n나: 네, 제 책이에요.", ["이", "그", "저"], "이", "말하는 사람인 마리 가까이에 있으므로 ‘이’를 사용해요."),
        ("가: ___ 가방은 주노 씨 가방이에요? (말을 듣는 주노 가까이)  \n나: 네, 제 가방이에요.", ["이", "그", "저"], "그", "듣는 사람인 주노 가까이에 있으므로 ‘그’를 사용해요."),
        ("가: ___ 의자는 교실 의자예요? (두 사람에게서 멀리)  \n나: 네, 교실 의자예요.", ["이", "그", "저"], "저", "두 사람 모두에게 먼 의자이므로 ‘저’를 사용해요."),
    ]
    second_done = render_choice_set(3, "grammar1_dialogue_v2", second_questions, dialogue_layout=True, hide_reference_button=True)
    if first_done and second_done:
        st.session_state["unit3_grammar1_activities_completed"] = True
        render_learning_success(interface_text("문법 1의 1번과 2번 활동을 모두 완료했어요!", "You completed both Grammar 1 activities!"), icon=":material/check_circle:")
    else:
        st.session_state.pop("unit3_grammar1_activities_completed", None)
    st.divider()


def render_unit3_vocabulary_dialogue():
    """Finish Unit 3 vocabulary with a scaffolded picture-dialogue check."""
    st.divider()
    st.markdown(interface_text("### 3. 그림을 보고 대화를 완성해 보세요.", "### 3. Look at the picture and complete each Korean dialogue"))
    st.caption(interface_text("그림의 물건과 위치를 확인하고 알맞은 대답을 선택하세요. 완성한 대화를 소리 내어 읽어 보세요.", "Check each object and its location, choose the correct Korean answer, and read the completed dialogues aloud."))
    st.markdown(
        """
        <style>
        div.st-key-unit3_vocab_dialogue_pair { margin-top:-16px; }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stHorizontalBlock"] {
            display: grid;
            grid-template-columns: 500px 500px;
            gap: 10mm;
            align-items: start;
            justify-content: start;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stColumn"] {
            width: 500px !important;
            min-width: 500px !important;
            height: 334px !important;
            min-height: 334px !important;
            max-height: 334px !important;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stColumn"]
        > [data-testid="stVerticalBlock"] {
            height: 334px !important;
            min-height: 334px !important;
            max-height: 334px !important;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stColumn"]
        [data-testid="stElementContainer"] {
            height: 334px !important;
            min-height: 334px !important;
            max-height: 334px !important;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stImage"],
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stImage"] img {
            width: 500px !important;
            height: 334px !important;
            min-height: 334px !important;
            max-height: 334px !important;
            object-fit: cover;
            display: block;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stAlert"],
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stAlertContainer"] {
            box-sizing: border-box;
            width: 500px !important;
            height: 334px !important;
            min-height: 334px !important;
            max-height: 334px !important;
            margin: 0 !important;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stAlert"] {
            display: flex !important;
            align-items: flex-start;
        }
        div.st-key-unit3_vocab_dialogue_pair [data-testid="stAlertContainer"] {
            flex: 1 1 280px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit3_vocab_dialogue_pair"):
        picture_column, dialogue_column = st.columns(2, gap=None, vertical_alignment="top")
        with picture_column:
            st.image(
                fit_image_to_canvas(
                    remember_reference_image("unit3-classroom-locations.png"),
                    canvas_size=(500, 334),
                    image_size=(500, 334),
                ),
                width=500,
            )
        with dialogue_column:
            render_learning_info(
                "**보기**\n\n"
                "가: 필통이 어디에 있어요?\n\n"
                "나: 필통이 책 옆에 있어요.",
                icon=":material/forum:",
            )
    questions = [
        ("가: 책이 어디에 있어요?\n\n나: ______", ["책상 위에 있어요.", "의자 아래에 있어요.", "교실 밖에 있어요."], "책상 위에 있어요.", "초록색 책은 책상 위에 있어요."),
        ("가: 가방이 책상 위에 있어요?\n\n나: ______", ["네, 책상 위에 있어요.", "아니요, 책상 위에 없어요. 의자 아래에 있어요.", "아니요, 교실에 시계가 없어요."], "아니요, 책상 위에 없어요. 의자 아래에 있어요.", "파란색 가방은 책상 위가 아니라 의자 아래에 있어요."),
        ("가: 교실에 시계가 있어요?\n\n나: ______", ["네, 시계가 있어요.", "아니요, 시계가 없어요.", "네, 가방이 있어요."], "네, 시계가 있어요.", "둥근 시계가 책상 위쪽 벽에 있어요."),
    ]
    return render_choice_set(3, "vocab_dialogue", questions, hide_reference_button=True)


def render_unit3_vocabulary_panel(vocabulary_words, active_index, example_sentence, selected_word, selected_meaning):
    """Keep the reference scene visible beside Unit 3 words and examples."""
    object_selectors = ",".join(
        f"div.st-key-vocab_select_3_{index} button" for index in range(6)
    )
    position_selectors = ",".join(
        f"div.st-key-vocab_select_3_{index} button" for index in range(6, len(vocabulary_words))
    )
    object_hover_selectors = object_selectors.replace(" button", " button:hover")
    position_hover_selectors = position_selectors.replace(" button", " button:hover")
    active_selector = f"div.st-key-vocab_select_3_{active_index} button"
    active_color = "#82c91e" if active_index < 6 else "#ff7a59"
    st.markdown(
        f"""
        <style>
        div.st-key-unit3_vocab_reference {{ position:sticky; top:1rem; }}
        .unit3-selected-example-heading {{
            margin-top:28px;
            margin-bottom:-2px;
            font-weight:700;
        }}
        div.st-key-vocab_flip_3 button {{
            width:calc(100% - 8px) !important;
            height:2.5rem !important;
            min-height:2.5rem !important;
            box-sizing:border-box !important;
        }}
        div.st-key-unit3_meaning_text button,
        div.st-key-unit3_meaning_text button:disabled {{
            height:2.5rem !important;
            min-height:2.5rem !important;
            box-sizing:border-box !important;
            justify-content:flex-start !important;
            text-align:left !important;
            border-color:#82c91e !important;
            background:#82c91e !important;
            color:#ffffff !important;
            opacity:1 !important;
            font-weight:700 !important;
        }}
        div.st-key-unit3_meaning_text button *,
        div.st-key-unit3_meaning_text button:disabled * {{
            color:#ffffff !important;
            text-align:left !important;
        }}
        div.st-key-unit3_meaning_text button > div,
        div.st-key-unit3_meaning_text button [data-testid="stMarkdownContainer"],
        div.st-key-unit3_meaning_text button p {{
            width:100% !important;
            justify-content:flex-start !important;
            text-align:left !important;
            margin-left:0 !important;
            margin-right:0 !important;
        }}
        {object_selectors} {{
            border-color:#82c91e !important; color:#b7ef58 !important;
            background:rgba(130,201,30,.10) !important;
        }}
        {position_selectors} {{
            border-color:#ff7a59 !important; color:#ff9a80 !important;
            background:rgba(255,122,89,.10) !important;
        }}
        {object_hover_selectors} {{ background:rgba(130,201,30,.22) !important; }}
        {position_hover_selectors} {{ background:rgba(255,122,89,.22) !important; }}
        {active_selector} {{
            background:{active_color} !important; border-color:{active_color} !important;
            color:#111111 !important; font-weight:800 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    picture_column, learning_column = st.columns([0.9, 1.35], gap="large")
    with picture_column:
        with st.container(border=True, key="unit3_vocab_reference"):
            st.image(
                fit_image_to_canvas(
                    remember_reference_image("unit3-classroom-locations.png"),
                    canvas_size=(500, 334),
                    image_size=(500, 334),
                ),
                width="stretch",
            )
    with learning_column:
        st.markdown(interface_text("### 1. 교실에 무엇이 있어요?", "### 1. What is in the classroom?"))
        st.caption(interface_text("교실 물건을 하나씩 누르고 예문을 소리 내어 읽어 보세요.", "Select each Korean classroom word and read its example sentence aloud."))
        object_columns = st.columns(6)
        for column, index in zip(object_columns, range(6)):
            with column:
                st.button(
                    vocabulary_words[index], key=f"vocab_select_3_{index}",
                    type="primary" if index == active_index else "secondary",
                    on_click=select_vocabulary, args=(3, index), width="stretch",
                )
        st.markdown(
            f'<div class="unit3-selected-example-heading">{interface_text("선택한 단어와 예문", "Selected word and example")}</div>',
            unsafe_allow_html=True,
        )
        render_vocabulary_example(example_sentence, color="coral" if active_index >= 6 else "lime")
        revealed_key = "vocab_revealed_3"
        meaning_button_column, meaning_text_column = st.columns([1, 3], gap=None, vertical_alignment="center")
        with meaning_button_column:
            if st.button(interface_text("뜻 보기 / 뜻 가리기", "Show / hide meaning"), key="vocab_flip_3", width="stretch"):
                st.session_state[revealed_key] = not st.session_state.get(revealed_key, False)
                st.rerun()
        with meaning_text_column:
            if st.session_state.get(revealed_key, False):
                st.button(
                    f"{selected_word} = {selected_meaning}",
                    key="unit3_meaning_text",
                    disabled=True,
                    width="stretch",
                )
    st.markdown(interface_text("### 2. 어디에 있어요?", "### 2. Where is it?"))
    st.caption(interface_text("위치 표현을 하나씩 누르고 예문을 소리 내어 읽어 보세요.", "Select each Korean location expression and read its example sentence aloud."))
    position_indices = list(range(6, len(vocabulary_words)))
    position_columns = st.columns(len(position_indices))
    for column, index in zip(position_columns, position_indices):
        with column:
            st.button(
                vocabulary_words[index], key=f"vocab_select_3_{index}",
                type="primary" if index == active_index else "secondary",
                on_click=select_vocabulary, args=(3, index), width="stretch",
            )


def render_unit4_grammar2():
    render_learning_info(interface_text(
        "행동의 대상이 되는 명사 뒤에 ‘을/를’을 붙여 말해요. 받침이 있으면 ‘을’, 받침이 없으면 ‘를’을 사용해요.",
        "Add 을/를 after the noun that receives the action. Use 을 after a final consonant and 를 after a vowel."),
        icon=":material/school:",
    )
    st.markdown(interface_text("### 핵심 대화와 사용 규칙", "### Key Korean dialogue and usage rules"))
    st.markdown(
        """
        <style>
        div.st-key-unit4_grammar2_reference_pair { margin-top:-16px; }
        .unit4-grammar2-reference-box {
            width:500px; height:334px; box-sizing:border-box;
            display:flex; flex-direction:column; justify-content:center;
            padding:24px; border:1px solid #334155; border-radius:10px;
            background:#172033; line-height:1.7;
        }
        .unit4-grammar2-reference-box p { margin:7px 0; }
        .unit4-grammar2-reference-box .speaker-a { color:#ff9b72; }
        .unit4-grammar2-reference-box .speaker-b { color:#78aef8; }
        .unit4-grammar2-reference-box .rule { color:#dbe7f5; }
        .unit4-grammar2-reference-box .speaker-b + .rule { margin-top:28px; }
        div.st-key-unit4_grammar2_reference_pair { position:relative; min-height:334px; }
        div.st-key-unit4_grammar2_reference_pair
        [data-testid="stElementContainer"]:has(.unit4-grammar2-reference-box) {
            position:absolute; left:calc(500px + 16px); top:0;
            width:500px; height:334px; z-index:1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit4_grammar2_reference_pair"):
        st.image(
            fit_image_to_canvas(
                remember_reference_image("unit4-study-movie.png"),
                canvas_size=(500, 334), image_size=(500, 334),
            ), width=500,
        )
        st.markdown(
            """
            <div class="unit4-grammar2-reference-box">
                <p class="speaker-a"><b>가:</b> 오늘 무엇을 해요?</p>
                <p class="speaker-b"><b>나:</b> 책을 읽어요.</p>
                <p class="speaker-a"><b>가:</b> 안나 씨는 한국 영화를 좋아해요?</p>
                <p class="speaker-b"><b>나:</b> 네. 저는 한국 영화를 좋아해요.</p>
                <p class="rule"><b>__FINAL_RULE__:</b> 책 → 책을 읽어요.</p>
                <p class="rule"><b>__VOWEL_RULE__:</b> 영화 → 영화를 봐요.</p>
            </div>
            """.replace("__FINAL_RULE__", interface_text("받침 있음 → 을", "Final consonant → 을")).replace(
                "__VOWEL_RULE__", interface_text("받침 없음 → 를", "No final consonant → 를")
            ),
            unsafe_allow_html=True,
        )

    st.markdown(interface_text("### 예시 대화를 먼저 읽어 보세요.", "### Read the example Korean dialogues first"))
    st.caption(interface_text("받침이 있는 말에는 ‘을’, 받침이 없는 말에는 ‘를’을 붙이는 방법을 대화로 확인하세요.", "Use the dialogues to compare 을 after a final consonant and 를 after a vowel."))
    example_columns = st.columns(2)
    with example_columns[0]:
        render_learning_info(
            ":orange[가: 무엇을 좋아해요?]\n\n"
            ":blue[나: 꽃을 좋아해요.]\n\n"
            "꽃 + 을 → 꽃을",
            icon=":material/forum:",
        )
    with example_columns[1]:
        render_learning_info(
            ":orange[가: 무엇을 좋아해요?]\n\n"
            ":blue[나: 커피를 좋아해요.]\n\n"
            "커피 + 를 → 커피를",
            icon=":material/forum:",
        )

    st.divider()
    st.markdown(interface_text("### 1. 그림을 보고 빈칸에 알맞은 문장을 선택해 보세요.", "### 1. Look at the picture and choose the correct Korean sentence"))
    st.caption(interface_text("그림의 대상을 확인하고 ‘을’ 또는 ‘를’이 올바르게 들어간 문장을 선택하세요.", "Identify the pictured object and choose the sentence that uses 을 or 를 correctly."))
    preference_questions = [
        ("꽃 그림: 무엇을 좋아해요?", ["꽃을 좋아해요.", "꽃를 좋아해요.", "꽃이 좋아해요."], "꽃을 좋아해요.", "‘꽃’은 받침이 있으므로 ‘을’을 사용해요."),
        ("커피 그림: 무엇을 좋아해요?", ["커피를 좋아해요.", "커피을 좋아해요.", "커피가 좋아해요."], "커피를 좋아해요.", "‘커피’는 받침이 없으므로 ‘를’을 사용해요."),
        ("음악 그림: 무엇을 좋아해요?", ["음악을 좋아해요.", "음악를 좋아해요.", "음악에 좋아해요."], "음악을 좋아해요.", "‘음악’은 받침이 있으므로 ‘을’을 사용해요."),
        ("불고기 그림: 무엇을 좋아해요?", ["불고기를 좋아해요.", "불고기을 좋아해요.", "불고기가 좋아해요."], "불고기를 좋아해요.", "‘불고기’는 받침이 없으므로 ‘를’을 사용해요."),
    ]
    unit4_particle_image = Path(__file__).with_name("assets") / "units" / "unit4-particle-objects-v2.png"
    preference_images = [
        crop_image_region(unit4_particle_image, (20, 20, 617, 617)),
        crop_image_region(unit4_particle_image, (638, 20, 1234, 617)),
        crop_image_region(unit4_particle_image, (20, 638, 617, 1234)),
        crop_image_region(unit4_particle_image, (638, 638, 1234, 1234)),
    ]
    preference_done = render_picture_word_cards(
        4,
        "grammar2_preference",
        preference_questions,
        preference_images,
        single_image_width=180,
    )

    st.divider()
    st.markdown(interface_text("### 2. 대화를 읽고 빈칸에 알맞은 ‘을/를’을 선택해 보세요.", "### 2. Read each dialogue and choose the correct 을/를 for the blank"))
    st.caption(interface_text("행동의 대상이 되는 말의 받침을 확인하세요.", "Check whether the object noun has a final consonant."))
    dialogue_questions = [
        ("가: 지금 무엇을 해요?\n나: 옷__ 사요.", ["을", "를", "이"], "을", "‘옷’은 받침이 있으므로 ‘을’을 붙여요."),
        ("가: 지금 음악을 들어요?\n나: 네. 음악__ 들어요.", ["을", "를", "가"], "을", "‘음악’은 받침이 있으므로 ‘을’을 붙여요."),
        ("가: 오늘 일해요?\n나: 아니요. 한국어__ 공부해요.", ["을", "를", "에"], "를", "‘한국어’는 받침이 없으므로 ‘를’을 붙여요."),
        ("가: 지금 무엇을 해요?\n나: 영화__ 봐요.", ["을", "를", "는"], "를", "‘영화’는 받침이 없으므로 ‘를’을 붙여요."),
    ]
    dialogue_done = render_choice_set(4, "grammar2_dialogue", dialogue_questions, dialogue_layout=True, scroll_below_picture=True)
    if preference_done and dialogue_done:
        st.session_state["grammar2_done_4"] = True
        render_learning_success(interface_text("문법 2의 1번과 2번 활동을 모두 완료했어요!", "You completed both Grammar 2 activities!"), icon=":material/celebration:")
    else:
        st.session_state.pop("grammar2_done_4", None)
        if preference_done and not dialogue_done:
            st.caption(interface_text("2번 그림 대화 네 문제까지 모두 맞히면 문법 2가 완료됩니다.", "Answer all four picture-dialogue questions in Activity 2 correctly to finish Grammar 2."))


def render_unit4_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    """Rebuild Unit 4 vocabulary around the textbook's three basic-verb tasks."""
    st.markdown(interface_text("### 1. 오늘 무엇을 해요?", "### 1. What are you doing today?"))
    picture_column, word_column = st.columns([0.82, 1.18], gap="small", vertical_alignment="top")
    with picture_column:
        st.image(
            fit_image_to_canvas(
                remember_reference_image("unit4-action-grid.png"),
                canvas_size=(500, 334), image_size=(500, 334),
            ),
            width="stretch",
        )
    with word_column:
        for start in range(0, len(vocabulary_words), 5):
            columns = st.columns(min(5, len(vocabulary_words) - start))
            for column, index in zip(columns, range(start, min(start + 5, len(vocabulary_words)))):
                with column:
                    st.button(
                        vocabulary_words[index], key=f"vocab_select_4_{index}",
                        type="primary" if index == active_index else "secondary",
                        on_click=select_vocabulary, args=(4, index), width="stretch",
                    )
        st.markdown(interface_text("**선택한 동사의 예문**", "**Example for the selected Korean verb**"))
        render_vocabulary_example(example_sentence, color="lime")

    st.divider()
    st.markdown(interface_text("### 2. 알맞은 것을 선택해 보세요.", "### 2. Choose the matching Korean verb"))
    matching_questions = [
        ("책", ["읽어요", "봐요", "만나요"], "읽어요", "책은 ‘읽어요’와 연결해요."),
        ("영화", ["마셔요", "읽어요", "봐요"], "봐요", "영화는 ‘봐요’와 연결해요."),
        ("친구", ["만나요", "자요", "요리해요"], "만나요", "친구는 ‘만나요’와 연결해요."),
        ("한국어", ["공부해요", "들어요", "먹어요"], "공부해요", "한국어는 ‘공부해요’와 연결해요."),
    ]
    matching_done = render_choice_set(4, "vocab_matching", matching_questions, hide_reference_button=True)

    st.divider()
    st.markdown(interface_text("### 3. 그림을 보고 무엇을 해요? 대답해 보세요.", "### 3. Look at each picture and answer 무엇을 해요?"))
    st.caption(interface_text("왼쪽 그림에서 네 사람의 동작을 확인하고 알맞은 문장을 선택하세요.", "Look at the four people in the picture on the left and choose the correct Korean sentence for each action."))
    picture_questions = [
        ("왼쪽 그림의 위 왼쪽 사람은 무엇을 해요?", ["밥을 먹어요.", "책을 읽어요.", "물을 마셔요."], "밥을 먹어요.", "식탁에서 밥을 먹고 있어요."),
        ("왼쪽 그림의 위 오른쪽 사람은 무엇을 해요?", ["책을 읽어요.", "영화를 봐요.", "친구를 만나요."], "책을 읽어요.", "손에 펼친 책이 있어요."),
        ("왼쪽 그림의 아래 왼쪽 사람은 무엇을 해요?", ["물을 마셔요.", "음악을 들어요.", "요리해요."], "물을 마셔요.", "잔의 물을 마시고 있어요."),
        ("왼쪽 그림의 아래 오른쪽 사람은 무엇을 해요?", ["한국어를 공부해요.", "자요.", "친구를 만나요."], "한국어를 공부해요.", "책상에서 교재를 공부하고 있어요."),
    ]
    picture_column, question_column = st.columns([0.82, 1.18], gap="small", vertical_alignment="top")
    with picture_column:
        st.image(
            fit_image_to_canvas(
                remember_reference_image("unit4-action-grid.png"),
                canvas_size=(500, 334),
                image_size=(500, 334),
            ),
            width="stretch",
        )
    with question_column:
        picture_done = render_choice_set(
            4,
            "vocab_picture",
            picture_questions,
            scroll_below_picture=True,
            hide_reference_button=True,
        )
    return matching_done and picture_done


def render_unit4_grammar1_intro():
    render_learning_info(interface_text(
        "동사를 공손한 현재형으로 바꾸어 오늘 하는 일을 묻고 답해요. ‘아’ 또는 ‘오’ 계열 모음 뒤에는 ‘-아요’, 그 밖에는 ‘-어요’를 사용하고 ‘하다’는 ‘해요’가 돼요.",
        "Use the polite present tense to ask and answer about current activities. Use -아요 with verb stems whose last vowel is 아 or 오, -어요 with other vowels, and change 하다 to 해요."),
        icon=":material/school:",
    )
    st.markdown(interface_text("### 핵심 대화와 활용 규칙", "### Key Korean dialogue and conjugation rules"))
    st.markdown(
        """
        <style>
        div.st-key-unit4_grammar1_reference_pair { margin-top:-16px; }
        .unit4-grammar1-reference-box {
            width: 500px;
            height: 334px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 24px;
            border: 1px solid #334155;
            border-radius: 10px;
            background: #172033;
            line-height: 1.65;
        }
        .unit4-grammar1-reference-box p { margin: 5px 0; }
        .unit4-grammar1-reference-box .speaker-a { color: #ff9b72; }
        .unit4-grammar1-reference-box .speaker-b { color: #78aef8; }
        .unit4-grammar1-reference-box .rule { color: #dbe7f5; }
        .unit4-grammar1-reference-box .speaker-b + .rule { margin-top: 22px; }
        div.st-key-unit4_grammar1_reference_pair {
            position: relative;
            min-height: 334px;
        }
        div.st-key-unit4_grammar1_reference_pair
        [data-testid="stElementContainer"]:has(.unit4-grammar1-reference-box) {
            position: absolute;
            left: calc(500px + 16px);
            top: 0;
            width: 500px;
            height: 334px;
            z-index: 1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit4_grammar1_reference_pair"):
        st.image(
            fit_image_to_canvas(
                remember_reference_image("unit4-action-grid.png"),
                canvas_size=(500, 334), image_size=(500, 334),
            ), width=500,
        )
        st.markdown(
            """
            <div class="unit4-grammar1-reference-box">
                <p class="speaker-a"><b>가:</b> 주노 씨는 오늘 무엇을 해요?</p>
                <p class="speaker-b"><b>나:</b> 오늘 친구를 만나요.</p>
                <p class="speaker-a"><b>가:</b> 유진 씨, 불고기 맛있어요?</p>
                <p class="speaker-b"><b>나:</b> 네. 정말 맛있어요.</p>
                <p class="rule"><b>__AYO_RULE__:</b> 만나다 → 만나요</p>
                <p class="rule"><b>__EOYO_RULE__:</b> 먹다 → 먹어요</p>
                <p class="rule"><b>__HAEYO_RULE__:</b> 공부하다 → 공부해요</p>
                <p class="rule"><b>__SHORT_RULE__:</b> 보다 → 보아요 → 봐요</p>
            </div>
            """.replace("__AYO_RULE__", interface_text("‘아’ 또는 ‘오’ 계열 → -아요", "Last vowel 아 or 오 → -아요")).replace(
                "__EOYO_RULE__", interface_text("그 밖의 모음 → -어요", "Other vowels → -어요")
            ).replace("__HAEYO_RULE__", interface_text("하다 → 해요", "하다 changes to 해요")).replace(
                "__SHORT_RULE__", interface_text("줄어드는 말", "Contracted form")
            ),
            unsafe_allow_html=True,
        )

    st.divider()
    st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at each picture and complete the Korean dialogue"))
    st.caption(interface_text("각 그림의 동작을 보며 동사를 알맞은 ‘-아요/어요’ 형태로 선택하세요.", "Choose the correct -아요/어요 form for the action in each picture."))
    unit4_action_image = Path(__file__).with_name("assets") / "units" / "unit4-action-grid.png"
    unit4_movie_image = Path(__file__).with_name("assets") / "units" / "unit4-study-movie.png"
    unit7_schedule_image = Path(__file__).with_name("assets") / "units" / "unit7-daily-schedule.png"
    grammar1_question_images = [
        crop_image_region(unit4_action_image, (780, 15, 1525, 505)),
        crop_image_region(unit4_movie_image, (770, 220, 1530, 930)),
        crop_image_region(unit4_action_image, (780, 520, 1525, 1015)),
        crop_image_region(unit7_schedule_image, (455, 635, 585, 745)),
    ]
    picture_questions = [
        ("가: 지금 무엇을 해요?\n나: 책을 ______.", ["읽어요", "읽아요", "읽해요"], "읽어요", "‘읽다’는 ‘읽어요’로 바뀌어요."),
        ("가: 지금 무엇을 해요?\n나: 영화를 ______.", ["봐요", "보어요", "보해요"], "봐요", "‘보아요’가 줄어서 ‘봐요’가 돼요."),
        ("가: 지금 공부해요?\n나: 네. 한국어를 ______.", ["공부해요", "공부아요", "공부어요"], "공부해요", "‘공부하다’는 ‘공부해요’로 바뀌어요."),
        ("가: 지금 일해요?\n나: 아니요. ______.", ["자요", "자어요", "자해요"], "자요", "‘자다’의 ‘아’ 모음 뒤에는 ‘-아요’가 붙어서 ‘자요’가 돼요."),
    ]
    picture_done = render_picture_word_cards(
        4,
        "grammar1_picture",
        picture_questions,
        grammar1_question_images,
        dialogue_layout=True,
        single_image_width=180,
    )
    if picture_done:
        st.session_state["unit4_grammar1_picture_completed"] = True
    else:
        st.session_state.pop("unit4_grammar1_picture_completed", None)

    st.divider()
    st.markdown(interface_text("### 2. 할 일을 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose an activity, complete the Korean dialogue, and read it aloud"))
    st.caption(interface_text("할 일을 하나 고르고 완성된 대화를 소리 내어 두 번 읽으세요.", "Choose one activity and read the completed Korean dialogue aloud twice."))
    speaking_actions = {
        "영화를 보다": "영화를 봐요",
        "책을 읽다": "책을 읽어요",
        "한국어를 공부하다": "한국어를 공부해요",
        "음악을 듣다": "음악을 들어요",
        "친구를 만나다": "친구를 만나요",
        "일하다": "일해요",
    }
    selected_action = st.selectbox(interface_text("오늘 할 일", "Activity to practice"), list(speaking_actions), key="unit4_grammar1_speaking_action")
    render_learning_info(
        ":orange[가: 오늘 무엇을 해요?]\n\n"
        f":blue[나: {speaking_actions[selected_action]}.]",
        icon=":material/record_voice_over:",
    )
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit4_grammar1_speaking_check", type="primary"):
        st.session_state["unit4_grammar1_speaking_completed"] = True
        st.session_state["unit4_grammar1_checked_action"] = selected_action
    speaking_done = (
        st.session_state.get("unit4_grammar1_speaking_completed", False)
        and st.session_state.get("unit4_grammar1_checked_action") == selected_action
    )
    if speaking_done:
        render_learning_success(interface_text("2번 말하기 연습을 완료했어요.", "You completed the speaking practice."), icon=":material/check_circle:")
    else:
        st.session_state.pop("unit4_grammar1_speaking_completed", None)
    st.divider()


def render_unit3_activity1():
    st.subheader(interface_text("활동 1 · 친구의 가방", "Activity 1 · A Friend’s Bag"))
    st.markdown(
        """
        <style>
        div.st-key-unit3_activity1_pair { margin-top:-16px; }
        .unit3-activity-dialogue-box {
            width: 500px;
            max-width: 100%;
            height: 334px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            padding: 24px;
            border: 1px solid #334155;
            border-radius: 10px;
            background: #172033;
            line-height: 1.7;
        }
        .unit3-activity-dialogue-box p {
            width: 100%;
            margin: 7px 0;
        }
        .unit3-activity-dialogue-box .speaker-a { color: #ff9b72; }
        .unit3-activity-dialogue-box .speaker-b { color: #78aef8; }
        div.st-key-unit3_activity1_pair {
            position: relative;
            min-height: 334px;
        }
        div.st-key-unit3_activity1_pair [data-testid="stElementContainer"]:has(.unit3-activity-dialogue-box) {
            position: absolute;
            left: calc(500px + 16px);
            top: 0;
            width: 500px;
            z-index: 1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit3_activity1_pair"):
        st.image(
            fit_image_to_canvas(
                remember_reference_image("unit3-people-belongings.png"),
                canvas_size=(500, 334), image_size=(500, 334),
            ),
            width=500,
        )
        st.markdown(
            """
            <div class="unit3-activity-dialogue-box">
                <p class="speaker-a">안나: 이 가방이 유진 씨 가방이에요?</p>
                <p class="speaker-b">유진: 아니요. 제 가방은 책상 옆에 있어요.</p>
                <p class="speaker-a">안나: 그럼 누구 가방이에요?</p>
                <p class="speaker-b">유진: 그 가방은 마리 씨 가방이에요.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown(interface_text("### 1. 대화를 읽고 물음에 답해 보세요.", "### 1. Read the Korean dialogue and answer the questions"))
    st.caption(interface_text("안나 씨와 유진 씨의 대화를 천천히 두 번 읽고 답을 선택하세요.", "Read Anna and Yujin’s Korean dialogue aloud twice, then choose the answers."))
    reading_questions = [
        ("이 가방은 누구 가방이에요?", ["마리 씨 가방이에요.", "유진 씨 가방이에요.", "안나 씨 가방이에요."], "마리 씨 가방이에요.", "유진 씨가 ‘그 가방은 마리 씨 가방이에요’라고 말했어요."),
        ("유진 씨 가방은 어디에 있어요?", ["책상 옆에 있어요.", "의자 아래에 있어요.", "필통 안에 있어요."], "책상 옆에 있어요.", "유진 씨가 ‘제 가방은 책상 옆에 있어요’라고 말했어요."),
    ]
    reading_done = render_choice_set(3, "activity1_reading", reading_questions, hide_reference_button=True)

    st.divider()
    st.markdown(interface_text("### 2. 표에서 한 줄을 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose one row, complete the Korean dialogue, and read it aloud"))
    st.caption(interface_text("표에서 한 줄을 고르면 그 정보로 대화가 완성됩니다. 완성된 대화를 소리 내어 두 번 읽어 보세요.", "Choose a row to build the dialogue, then read the completed Korean dialogue aloud twice."))
    dialogue_rows = {
        "1) 마리 · 책": ("마리", "책", "책상 위", "유진"),
        "2) 안나 · 펜": ("안나", "펜", "필통 안", "주노"),
        "3) 재민 · 우산": ("재민", "우산", "의자 옆", "수지"),
    }
    st.table([
        {"이름": name, "물건": item, "위치": location, "누구": owner}
        for name, item, location, owner in dialogue_rows.values()
    ])
    selected_row = st.selectbox(interface_text("연습할 대화", "Dialogue to practice"), list(dialogue_rows), key="unit3_activity1_dialogue_row")
    name, item, location, owner = dialogue_rows[selected_row]
    render_learning_info(
        f":orange[가: 이 {item}이 {name} 씨 {item}이에요?]\n\n"
        f":blue[나: 아니요. 제 {item}은 {location}에 있어요.]\n\n"
        f":orange[가: 그럼 누구 {item}이에요?]\n\n"
        f":blue[나: 그 {item}은 {owner} 씨 {item}이에요.]",
        icon=":material/record_voice_over:",
    )
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit3_activity1_dialogue_done", type="primary"):
        st.session_state["unit3_activity1_dialogue_completed"] = True
        st.session_state["unit3_activity1_checked_row"] = selected_row
    dialogue_done = (
        st.session_state.get("unit3_activity1_dialogue_completed", False)
        and st.session_state.get("unit3_activity1_checked_row") == selected_row
    )
    if dialogue_done:
        render_learning_success(interface_text("2번 말하기 연습을 완료했어요.", "You completed the speaking practice."), icon=":material/check_circle:")
    if reading_done and dialogue_done:
        st.session_state["activity1_completed_3"] = True
        render_learning_success(interface_text("친구의 가방 1번과 2번 활동을 모두 완료했어요!", "You completed both A Friend’s Bag activities!"), icon=":material/celebration:")
    else:
        st.session_state.pop("activity1_completed_3", None)


def render_unit4_activity1():
    st.subheader(interface_text("활동 1 · 재민 씨와 마리 씨의 오늘", "Activity 1 · Jaemin and Mari’s Day"))
    st.markdown(interface_text("### 1. 두 사람의 대화를 읽고 물음에 답해 보세요.", "### 1. Read the Korean dialogue and answer the questions"))
    st.markdown(
        """
        <style>
        div.st-key-unit4_activity1_reference_pair { margin-top:-16px; }
        div.st-key-unit4_activity1_reference_pair [data-testid="stHorizontalBlock"] {
            display:grid;
            grid-template-columns:500px 500px;
            gap:16px !important;
            align-items:start;
            justify-content:start;
        }
        div.st-key-unit4_activity1_reference_pair [data-testid="stColumn"] {
            width:500px !important;
            min-width:500px !important;
            height:334px !important;
        }
        div.st-key-unit4_activity1_reference_pair [data-testid="stImage"],
        div.st-key-unit4_activity1_reference_pair [data-testid="stImage"] img {
            width:500px !important;
            height:334px !important;
            object-fit:cover;
            display:block;
        }
        div.st-key-unit4_activity1_reference_pair [data-testid="stAlert"],
        div.st-key-unit4_activity1_reference_pair [data-testid="stAlertContainer"] {
            box-sizing:border-box;
            width:500px !important;
            height:334px !important;
            min-height:334px !important;
            max-height:334px !important;
            margin:0 !important;
        }
        div.st-key-unit4_activity1_reference_pair [data-testid="stAlert"] {
            display:flex !important;
            align-items:center;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit4_activity1_reference_pair"):
        scene_column, dialogue_column = st.columns(2, gap=None, vertical_alignment="top")
        with scene_column:
            st.image(
                fit_image_to_canvas(
                    remember_reference_image("unit4-study-movie.png"),
                    canvas_size=(500, 334), image_size=(500, 334),
                ), width=500,
            )
        with dialogue_column:
            render_learning_info(
                ":orange[재민: 마리 씨, 오늘 뭐 해요?]\n\n"
                ":blue[마리: 한국어를 공부해요.]\n\n"
                ":orange[마리: 재민 씨는 뭐 해요?]\n\n"
                ":blue[재민: 저는 친구를 만나요. 한국 영화를 봐요.]",
                icon=":material/forum:",
            )
    reading_questions = [
        ("마리 씨는 오늘 무엇을 해요?", ["한국어를 공부해요.", "친구를 만나요.", "영화를 봐요."], "한국어를 공부해요.", "마리가 ‘한국어를 공부해요’라고 말했어요."),
        ("재민 씨는 누구를 만나요?", ["친구를 만나요.", "마리 씨를 만나요.", "선생님을 만나요."], "친구를 만나요.", "재민이 ‘저는 친구를 만나요’라고 말했어요."),
    ]
    reading_done = render_choice_set(4, "activity1_reading", reading_questions, hide_reference_button=True)

    st.divider()
    st.markdown(interface_text("### 2. 사람과 할 일을 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a person and activity, then read the completed Korean dialogue aloud"))
    st.caption(interface_text("표에서 이름과 할 일을 고르면 교재의 말하기 대화가 완성됩니다.", "Choose a name and activity to build the speaking dialogue."))
    activity_rows = {
        "안나 · 영화를 보다": ("안나", "영화를 봐요"),
        "주노 · 공부하다": ("주노", "공부해요"),
        "재민 · 친구를 만나다": ("재민", "친구를 만나요"),
        "수지 · 책을 읽다": ("수지", "책을 읽어요"),
        "유진 · 게임을 하다": ("유진", "게임을 해요"),
        "마리 · 일하다": ("마리", "일해요"),
    }
    selected_row = st.selectbox(interface_text("연습할 사람과 할 일", "Person and activity to practice"), list(activity_rows), key="unit4_activity1_row")
    person, action = activity_rows[selected_row]
    render_learning_info(
        f":orange[가: {person} 씨, 오늘 뭐 해요?]\n\n"
        f":blue[나: 저는 {action}.]\n\n"
        ":orange[가: 그래요? 저는 한국어를 공부해요.]",
        icon=":material/record_voice_over:",
    )
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit4_activity1_speaking_done", type="primary"):
        st.session_state["unit4_activity1_speaking_completed"] = True
        st.session_state["unit4_activity1_checked_row"] = selected_row
    speaking_done = (
        st.session_state.get("unit4_activity1_speaking_completed", False)
        and st.session_state.get("unit4_activity1_checked_row") == selected_row
    )
    if speaking_done:
        render_learning_success(interface_text("완성한 대화를 소리 내어 읽는 연습을 완료했어요.", "You completed the read-aloud practice."), icon=":material/check_circle:")
    else:
        st.session_state.pop("unit4_activity1_speaking_completed", None)
    if reading_done and speaking_done:
        st.session_state["activity1_completed_4"] = True
        render_learning_success(interface_text("활동 1의 읽기와 말하기를 모두 완료했어요!", "You completed both the reading and speaking activities!"), icon=":material/celebration:")
    else:
        st.session_state.pop("activity1_completed_4", None)


def render_unit3_activity2():
    st.subheader(interface_text("활동 2 · 주노 씨의 방", "Activity 2 · Juno’s Room"))
    st.markdown(
        """
        <style>
        div.st-key-unit3_activity2_pair { margin-top:-16px; }
        .unit3-activity-room-box {
            width: 500px;
            max-width: 100%;
            height: 334px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 24px;
            border: 1px solid #334155;
            border-radius: 10px;
            background: #172033;
            line-height: 1.7;
        }
        .unit3-activity-room-box p { margin: 7px 0; }
        div.st-key-unit3_activity2_pair {
            position: relative;
            min-height: 334px;
        }
        div.st-key-unit3_activity2_pair [data-testid="stElementContainer"]:has(.unit3-activity-room-box) {
            position: absolute;
            left: calc(500px + 16px);
            top: 0;
            width: 500px;
            z-index: 1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit3_activity2_pair"):
        st.image(
            fit_image_to_canvas(
                remember_reference_image("unit3-juno-room.png"),
                canvas_size=(500, 334), image_size=(500, 334),
            ),
            width=500,
        )
        st.markdown(
            """
            <div class="unit3-activity-room-box">
                <h4>주노 씨의 방</h4>
                <p>제 방이에요. 침대가 있어요.</p>
                <p>침대 옆에 책상이 있어요.</p>
                <p>책상 위에 컴퓨터와 가방이 있어요.</p>
                <p>책은 침대 옆에 있어요.</p>
                <p>시계는 없어요.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown(interface_text("### 1. 주노 씨 방의 글을 읽고 물음에 답해 보세요.", "### 1. Read the Korean description of Juno’s room and answer"))
    st.caption(interface_text("그림을 살펴보고 소개문을 두 번 읽은 뒤 아래 두 문제를 푸세요.", "Study the picture, read the Korean description aloud twice, and answer both questions."))
    reading_questions = [
        ("침대 옆에 무엇이 있어요?", ["책이 있어요.", "가방이 있어요.", "시계가 있어요."], "책이 있어요.", "책 두 권이 침대 옆 작은 장 위에 있어요."),
        ("책상 위에 무엇이 있어요?", ["가방하고 컴퓨터가 있어요.", "책하고 시계가 있어요.", "침대하고 의자가 있어요."], "가방하고 컴퓨터가 있어요.", "소개 글에서 가방과 컴퓨터가 책상 위에 있다고 했어요."),
    ]
    reading_done = render_choice_set(3, "activity2_reading", reading_questions, hide_reference_button=True)

    st.divider()
    st.markdown(interface_text("### 2. 여러분 방에 무엇이 있어요? 그림을 그리고 써 보세요.", "### 2. What is in your room? Draw and write"))
    st.caption(interface_text("왼쪽에는 방 그림을 준비하고, 오른쪽에는 방에 있는 물건 두 개와 위치를 적어 보세요.", "Optionally add a room picture, then enter two objects and their locations in Korean."))
    drawing_column, writing_column = st.columns(2, gap="large", vertical_alignment="top")
    with drawing_column:
        with st.container(border=True, height=420):
            st.markdown(interface_text("#### 나의 방 그림", "#### My room picture"))
            st.caption(interface_text("종이에 방을 그린 뒤 사진을 올릴 수 있어요. 그림 파일은 선택 사항입니다.", "You may draw your room on paper and upload a photo. The picture is optional."))
            st.file_uploader(
                interface_text("방 그림 또는 사진", "Room drawing or photo"),
                type=["png", "jpg", "jpeg"],
                key="unit3_activity2_room_drawing",
                label_visibility="collapsed",
            )
            render_learning_info(interface_text("그림에 침대·책상·의자·책·가방 등의 위치를 표시해 보세요.", "Show the locations of items such as 침대, 책상, 의자, 책, and 가방 in your picture."), icon=":material/draw:")
    with writing_column:
        with st.container(border=True, height=420):
            st.markdown(interface_text("#### 나의 방 소개", "#### My room description"))
            first_item = st.text_input(interface_text("첫 번째 물건", "First object (in Korean)"), placeholder=interface_text("예: 침대", "Example: 침대"), key="unit3_activity2_first_item")
            first_location = st.text_input(interface_text("첫 번째 물건의 위치", "First object’s location (in Korean)"), placeholder=interface_text("예: 창문 옆", "Example: 창문 옆"), key="unit3_activity2_first_location")
            second_item = st.text_input(interface_text("두 번째 물건", "Second object (in Korean)"), placeholder=interface_text("예: 책", "Example: 책"), key="unit3_activity2_second_item")
            second_location = st.text_input(interface_text("두 번째 물건의 위치", "Second object’s location (in Korean)"), placeholder=interface_text("예: 책상 위", "Example: 책상 위"), key="unit3_activity2_second_location")
    writing_ready = all(value.strip() for value in [first_item, first_location, second_item, second_location])
    room_description = ""
    if writing_ready:
        first_particle = subject_particle(first_item.strip())
        second_particle = subject_particle(second_item.strip())
        room_description = (
            f"제 방이에요. {first_location.strip()}에 {first_item.strip()}{first_particle} 있어요. "
            f"{second_location.strip()}에 {second_item.strip()}{second_particle} 있어요."
        )
        render_learning_success(room_description, icon=":material/edit_note:")
    room_read_aloud = st.checkbox(
        interface_text("완성한 방 소개를 소리 내어 읽었어요", "I read my completed Korean room description aloud"),
        key="unit3_activity2_room_read_aloud",
        disabled=not writing_ready,
        on_change=set_session_state_value,
        args=("unit3_activity2_checked_description", room_description),
    )
    room_read_aloud_current = (
        room_read_aloud
        and st.session_state.get("unit3_activity2_checked_description") == room_description
    )
    if not reading_done:
        st.caption(interface_text("먼저 1번의 두 문제를 모두 맞혀 주세요.", "First, answer both questions in Activity 1 correctly."))
    elif not writing_ready:
        st.caption(interface_text("2번의 물건과 위치를 모두 입력하면 활동 2를 제출할 수 있어요.", "Enter both objects and locations to continue."))
    elif not room_read_aloud_current:
        st.caption(interface_text("완성한 한국어 방 소개를 소리 내어 읽고 확인해 주세요.", "Read your completed Korean room description aloud, then check the box."))
    return room_description if reading_done and writing_ready and room_read_aloud_current else ""


def render_unit4_activity2():
    st.subheader(interface_text("활동 2 · 유진 씨와 안나 씨의 오늘", "Activity 2 · Yujin and Anna’s Day"))
    render_learning_info(interface_text(
        "문자 메시지에서 두 사람의 장소와 지금 하는 일을 확인한 뒤, 여러분의 오늘을 세 문장으로 써 보세요.",
        "Find each person’s location and activity in the Korean messages, then write three Korean sentences about your day."),
        icon=":material/school:",
    )
    st.markdown(interface_text("### 1. 문자 메시지를 읽고 물음에 답해 보세요.", "### 1. Read the Korean messages and answer the questions"))
    st.caption(interface_text("교재의 휴대전화 화면처럼 말풍선을 위에서 아래로 차례대로 읽으세요.", "Read the message bubbles from top to bottom."))
    st.markdown(
        """
        <style>
        .unit4-activity2-reference {
            display:grid;
            grid-template-columns:500px 500px;
            gap:16px;
            justify-content:start;
            align-items:start;
        }
        .unit4-activity2-phone,
        .unit4-activity2-guide {
            width:500px;
            height:334px;
            box-sizing:border-box;
            border:1px solid #334155;
            border-radius:10px;
        }
        .unit4-activity2-phone {
            padding:18px 46px;
            background:#111827;
        }
        .unit4-activity2-phone-screen {
            height:100%;
            box-sizing:border-box;
            padding:16px 18px;
            border:5px solid #0b0f16;
            border-radius:28px;
            background:#eef3f8;
            color:#18212b;
        }
        .unit4-activity2-phone-title {
            margin-bottom:12px;
            text-align:center;
            font-weight:800;
        }
        .unit4-activity2-bubble {
            width:fit-content;
            max-width:82%;
            margin:7px 0;
            padding:8px 12px;
            border-radius:14px;
            line-height:1.35;
        }
        .unit4-activity2-bubble.anna { margin-left:auto; background:#69c6df; }
        .unit4-activity2-bubble.yujin { background:#d7dde5; }
        .unit4-activity2-guide {
            display:flex;
            flex-direction:column;
            justify-content:center;
            padding:28px;
            background:#172033;
            color:#dbe7f5;
            line-height:1.75;
        }
        .unit4-activity2-guide h4 { margin:0 0 18px; }
        .unit4-activity2-guide p { margin:8px 0; }
        </style>
        <div class="unit4-activity2-reference">
            <div class="unit4-activity2-phone">
                <div class="unit4-activity2-phone-screen">
                    <div class="unit4-activity2-phone-title">안나 · 유진</div>
                    <div class="unit4-activity2-bubble anna">유진 씨, 오늘 뭐 해요?</div>
                    <div class="unit4-activity2-bubble yujin">지금 공원에 있어요. 운동해요.</div>
                    <div class="unit4-activity2-bubble yujin">안나 씨는 뭐 해요?</div>
                    <div class="unit4-activity2-bubble anna">저는 집에 있어요. 영화를 봐요.</div>
                </div>
            </div>
            <div class="unit4-activity2-guide">
                <h4>__GUIDE_HEADING__</h4>
                <p><b>안나:</b> 집에 있어요. 영화를 봐요.</p>
                <p><b>유진:</b> 공원에 있어요. 운동해요.</p>
                <p>__GUIDE_RULE__</p>
            </div>
        </div>
        """.replace("__GUIDE_HEADING__", interface_text("문자에서 확인할 내용", "What to find in the messages")).replace(
            "__GUIDE_RULE__",
            interface_text("장소에는 <b>에 있어요</b>, 행동의 대상에는 <b>을/를</b>을 사용해요.", "Use <b>에 있어요</b> for a location and <b>을/를</b> for the object of an action."),
        ),
        unsafe_allow_html=True,
    )
    message_questions = [
        ("안나 씨는 어디에 있어요?", ["집에 있어요.", "공원에 있어요.", "학교에 있어요."], "집에 있어요.", "안나의 첫 메시지에 ‘지금 집에 있어요’라고 했어요."),
        ("유진 씨는 지금 뭐 해요?", ["운동해요.", "영화를 봐요.", "공부해요."], "운동해요.", "유진의 마지막 메시지에 ‘운동해요’라고 했어요."),
    ]
    message_done = render_choice_set(4, "activity2_message", message_questions, hide_reference_button=True)

    st.divider()
    st.markdown(interface_text("### 2. 여러분은 오늘 무엇을 해요? 써 보세요.", "### 2. What are you doing today? Write three Korean sentences"))
    st.caption(interface_text("교재의 공책 활동처럼 장소 한 곳과 오늘 하는 일 두 가지를 골라 세 문장으로 완성하세요.", "Choose one location and two activities, then complete the three Korean sentences."))
    render_learning_info(
        interface_text("**쓰기 순서**", "**Writing order**") + "\n\n"
        "① 저는 지금 ___에 있어요.\n\n"
        "② 오늘 ___을/를 해요.\n\n"
        "③ 그리고 ___을/를 해요.",
        icon=":material/edit_note:",
    )
    writing_columns = st.columns(3)
    unit4_choose_label = interface_text("선택하세요", "Choose")
    with writing_columns[0]:
        place = st.text_input(interface_text("① 지금 있는 곳", "① Current location (in Korean)"), placeholder=interface_text("예: 집", "Example: 집"), key="unit4_activity2_place")
    with writing_columns[1]:
        first_action = st.selectbox(interface_text("② 첫 번째 할 일", "② First activity"), [unit4_choose_label, "한국어를 공부해요", "책을 읽어요", "영화를 봐요", "친구를 만나요", "운동해요", "일해요"], key="unit4_activity2_first_action")
    with writing_columns[2]:
        second_action = st.selectbox(interface_text("③ 두 번째 할 일", "③ Second activity"), [unit4_choose_label, "밥을 먹어요", "음악을 들어요", "요리해요", "게임을 해요", "자요"], key="unit4_activity2_second_action")
    writing_ready = bool(place.strip()) and first_action != unit4_choose_label and second_action != unit4_choose_label
    response = ""
    if writing_ready:
        response = f"저는 지금 {place.strip()}에 있어요. 오늘 {first_action.rstrip('.')}. 그리고 {second_action.rstrip('.')}."
        render_learning_success(response, icon=":material/edit_note:")
    response_read_aloud = st.checkbox(
        interface_text("완성한 세 문장을 소리 내어 읽었어요", "I read my three completed Korean sentences aloud"),
        key="unit4_activity2_response_read_aloud",
        disabled=not writing_ready,
        on_change=set_session_state_value,
        args=("unit4_activity2_checked_response", response),
    )
    response_read_aloud_current = response_read_aloud and st.session_state.get("unit4_activity2_checked_response") == response
    if not message_done:
        st.caption(interface_text("먼저 1번 문자 메시지의 두 문제를 모두 맞혀 주세요.", "First, answer both message questions correctly."))
    elif not writing_ready:
        st.caption(interface_text("장소와 두 가지 할 일을 모두 입력하면 활동 2를 완료할 수 있어요.", "Enter one location and two activities to continue."))
    elif not response_read_aloud_current:
        st.caption(interface_text("완성한 한국어 세 문장을 소리 내어 읽고 확인해 주세요.", "Read the three completed Korean sentences aloud, then check the box."))
    return response if message_done and writing_ready and response_read_aloud_current else ""


def render_unit5_reference(image_name, box_class, box_html, horizontal_crop=None):
    """Show a responsive reference pair and remember its image for nearby questions."""
    global ACTIVE_REFERENCE_IMAGE
    key = f"unit5_{box_class}_pair"
    if english_support_enabled():
        for korean_label, english_label in {
            "장소와 식품": "Places and foods",
            "장소:": "Places:",
            "식품:": "Foods:",
            "묻기:": "Ask:",
            "형태:": "Pattern:",
            "누가 무엇을 사는지 확인한 뒤 여러분의 쇼핑 계획을 써 보세요.": "After identifying each purchase, write your own shopping plan.",
        }.items():
            box_html = box_html.replace(korean_label, english_label)
    st.markdown(
        f"""
        <style>
        .{box_class} {{
            width:500px; height:334px; box-sizing:border-box; display:flex;
            flex-direction:column; justify-content:center; padding:26px;
            border:1px solid #334155; border-radius:10px; background:#172033;
            color:#dbe7f5; line-height:1.7;
        }}
        .{box_class} p {{ margin:7px 0; }}
        div.st-key-{key} {{ width:1016px; max-width:100%; margin-top:-16px; }}
        div.st-key-{key} [data-testid="stHorizontalBlock"] {{
            display:grid; grid-template-columns:500px 500px;
            align-items:start; gap:16px !important;
        }}
        div.st-key-{key} [data-testid="stColumn"] {{
            width:500px !important; min-width:500px !important; height:334px !important;
        }}
        div.st-key-{key} [data-testid="stColumn"]:first-child {{
            position:sticky; top:1rem; align-self:flex-start;
        }}
        div.st-key-{key} [data-testid="stImage"],
        div.st-key-{key} [data-testid="stImage"] img {{
            width:500px !important; height:334px !important; object-fit:contain; display:block;
        }}
        @media (max-width:760px) {{
            div.st-key-{key} {{ width:100%; }}
            div.st-key-{key} [data-testid="stHorizontalBlock"] {{
                display:grid; grid-template-columns:minmax(0, 1fr); gap:12px !important;
            }}
            div.st-key-{key} [data-testid="stColumn"] {{
                width:100% !important; min-width:0 !important; height:auto !important;
            }}
            div.st-key-{key} [data-testid="stColumn"]:first-child {{ position:static; }}
            .{box_class} {{ width:100%; height:auto; min-height:0; aspect-ratio:500/334; padding:18px; }}
            div.st-key-{key} [data-testid="stImage"],
            div.st-key-{key} [data-testid="stImage"] img {{
                width:100% !important; height:auto !important; aspect-ratio:500/334;
                object-fit:contain;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key=key):
        image_path = Path(__file__).with_name("assets") / "units" / image_name
        ACTIVE_REFERENCE_IMAGE = str(image_path)
        if horizontal_crop is None:
            reference_image = fit_image_to_canvas(
                image_path, canvas_size=(500, 334), image_size=(500, 334)
            )
        else:
            with Image.open(image_path) as source:
                rgba_source = source.convert("RGBA")
                left_ratio, right_ratio = horizontal_crop
                cropped_source = rgba_source.crop((
                    round(rgba_source.width * left_ratio), 0,
                    round(rgba_source.width * right_ratio), rgba_source.height,
                ))
                fitted_crop = ImageOps.contain(
                    cropped_source, (500, 334), Image.Resampling.LANCZOS
                )
                reference_image = Image.new("RGBA", (500, 334), (23, 32, 51, 255))
                crop_offset = (
                    (500 - fitted_crop.width) // 2,
                    (334 - fitted_crop.height) // 2,
                )
                reference_image.paste(fitted_crop, crop_offset, fitted_crop)
        image_column, guide_column = st.columns(2, gap="small", vertical_alignment="top")
        with image_column:
            st.image(reference_image, width="stretch")
        with guide_column:
            unit_number = st.session_state.get("selected_unit_number", 5)
            highlighted_html = highlight_learning_text(box_html, unit_number, escape=False)
            st.markdown(f'<div class="{box_class}">{highlighted_html}</div>', unsafe_allow_html=True)


def render_unit5_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    st.markdown(interface_text("### 1. 어디에 자주 가요?", "### 1. Where do you often go?"))
    st.caption(interface_text("장소와 식품 그림을 보며 낱말을 하나씩 누르고 예문을 읽어 보세요.", "Select each Korean place and food word, then read its example sentence aloud."))
    render_unit5_reference(
        "unit5-places-foods.png", "unit5-vocab-guide",
        "<h4>장소와 식품</h4>"
        "<p><b>장소:</b> 학교, 회사, 식당, 카페, 공원, 마트</p>"
        "<p><b>식품:</b> 빵, 라면, 과일, 커피, 차, 우유, 과자, 아이스크림</p>"
        "<p><b>묻기:</b> 어디에 가요? / 무엇을 좋아해요?</p>",
    )
    st.markdown(interface_text("**장소**", "**Places**"))
    place_columns = st.columns(6)
    for column, index in zip(place_columns, range(6)):
        with column:
            st.button(vocabulary_words[index], key=f"vocab_select_5_{index}",
                      type="primary" if index == active_index else "secondary",
                      on_click=select_vocabulary, args=(5, index), width="stretch")
    st.markdown(interface_text("**식품**", "**Foods**"))
    for start in (6, 10):
        columns = st.columns(4)
        for column, index in zip(columns, range(start, min(start + 4, len(vocabulary_words)))):
            with column:
                st.button(vocabulary_words[index], key=f"vocab_select_5_{index}",
                          type="primary" if index == active_index else "secondary",
                          on_click=select_vocabulary, args=(5, index), width="stretch")
    render_vocabulary_example(example_sentence, color="lime" if active_index < 6 else "coral")
    st.divider()
    st.markdown(interface_text("### 2. 그림에 알맞은 말을 선택해 보세요.", "### 2. Choose the correct Korean word for each picture"))
    unit5_vocab_image = Path(__file__).with_name("assets") / "units" / "unit5-places-foods.png"
    question_images = [
        crop_image_region(unit5_vocab_image, (42, 160, 262, 535)),
        crop_image_region(unit5_vocab_image, (1052, 160, 1260, 535)),
        crop_image_region(unit5_vocab_image, (42, 600, 222, 875)),
        crop_image_region(unit5_vocab_image, (1080, 600, 1198, 875)),
    ]
    questions = [
        (interface_text("이곳은 어디예요?", "Where is this place?"), ["학교", "회사", "마트"], "학교", "학생들이 공부하는 장소예요."),
        (interface_text("이곳은 어디예요?", "Where is this place?"), ["공원", "카페", "식당"], "공원", "산책하고 쉬는 장소예요."),
        (interface_text("이것은 무엇이에요?", "What is this?"), ["빵", "과자", "라면"], "빵", "식빵 모양을 확인하세요."),
        (interface_text("이것은 무엇이에요?", "What is this?"), ["우유", "커피", "차"], "우유", "흰 우유가 든 병이에요."),
    ]
    picture_done = render_picture_word_cards(5, "vocab_picture", questions, question_images)
    st.divider()
    st.markdown(interface_text("### 3. 그림을 보고 대화를 완성해 보세요.", "### 3. Look at the picture and complete each Korean dialogue"))
    st.caption(interface_text("장소와 식품을 함께 확인하고 알맞은 두 문장을 선택하세요.", "Identify the place and food together, then choose the correct two-sentence Korean dialogue."))
    dialogue_question_images = [
        (
            crop_image_region(unit5_vocab_image, (542, 160, 765, 535)),
            crop_image_region(unit5_vocab_image, (248, 600, 425, 875)),
        ),
        (
            crop_image_region(unit5_vocab_image, (800, 160, 1018, 535)),
            crop_image_region(unit5_vocab_image, (880, 600, 1048, 875)),
        ),
        (
            crop_image_region(unit5_vocab_image, (1052, 160, 1260, 535)),
            crop_image_region(unit5_vocab_image, (1222, 600, 1358, 875)),
        ),
        (
            crop_image_region(unit5_vocab_image, (1290, 160, 1500, 535)),
            crop_image_region(unit5_vocab_image, (1080, 600, 1198, 875)),
        ),
    ]
    dialogue_questions = [
        ("가: 여기는 어디예요?\n나: ______\n가: 무엇을 먹어요?\n나: ______", [
            "식당이에요. 라면을 먹어요.", "카페예요. 우유를 사요.", "공원이에요. 커피를 마셔요."
        ], "식당이에요. 라면을 먹어요.", "라면을 먹는 장소는 식당이에요."),
        ("가: 여기는 어디예요?\n나: ______\n가: 무엇을 마셔요?\n나: ______", [
            "카페예요. 차를 마셔요.", "마트예요. 과자를 먹어요.", "학교예요. 라면을 먹어요."
        ], "카페예요. 차를 마셔요.", "카페에서 차를 마셔요."),
        ("가: 여기는 어디예요?\n나: ______\n가: 무엇을 먹어요?\n나: ______", [
            "공원이에요. 과자를 먹어요.", "회사예요. 우유를 사요.", "식당이에요. 커피를 마셔요."
        ], "공원이에요. 과자를 먹어요.", "공원에서 과자를 먹는 장면이에요."),
        ("가: 여기는 어디예요?\n나: ______\n가: 무엇을 사요?\n나: ______", [
            "마트예요. 우유를 사요.", "카페예요. 과일을 먹어요.", "학교예요. 차를 마셔요."
        ], "마트예요. 우유를 사요.", "마트에서 우유를 사요."),
    ]
    dialogue_done = render_picture_word_cards(
        5,
        "vocab_dialogue",
        dialogue_questions,
        dialogue_question_images,
        dialogue_layout=True,
    )
    return picture_done and dialogue_done


def render_unit5_grammar1_intro():
    render_learning_info(interface_text("이동하는 목적지 뒤에 ‘에’를 붙여 ‘어디에 가요?’라고 묻고 ‘장소에 가요’라고 대답해요.", "Add 에 after a destination: ask 어디에 가요? and answer 장소에 가요."), icon=":material/school:")
    render_unit5_reference(
        "unit5-places-foods.png", "unit5-grammar1-guide",
        "<p><b>가:</b> 마리 씨, 어디에 가요?</p><p><b>나:</b> 영화관에 가요.</p>"
        "<p><b>가:</b> 재민 씨, 집에 가요?</p><p><b>나:</b> 아니요. 백화점에 가요.</p>"
        "<p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>장소 + 에 가요</b></p>",
    )
    st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at the picture and complete the Korean dialogue"))
    st.caption(interface_text("각 사람의 목적지를 그림에서 확인하고 ‘장소에 가요’로 대답하세요.", "Identify each destination in the picture and answer with 장소에 가요."))
    questions = [
        ("마리 씨는 어디에 가요?", ["공원에 가요.", "공원을 가요.", "공원하고 가요."], "공원에 가요.", "가는 장소 뒤에는 ‘에’를 붙여요."),
        ("재민 씨는 어디에 가요?", ["회사에 가요.", "회사를 가요.", "회사하고 가요."], "회사에 가요.", "‘회사에 가요’가 맞아요."),
        ("안나 씨는 학교에 가요?", ["아니요. 마트에 가요.", "네. 마트를 가요.", "아니요. 마트하고 가요."], "아니요. 마트에 가요.", "목적지 ‘마트’ 뒤에 ‘에’를 붙여요."),
        ("유진 씨는 회사에 가요?", ["아니요. 카페에 가요.", "네. 카페를 가요.", "아니요. 카페하고 가요."], "아니요. 카페에 가요.", "‘카페에 가요’라고 말해요."),
    ]
    destination_image = Path(__file__).with_name("assets") / "units" / "unit5-destinations-v2.png"
    destination_images = [
        crop_image_region(destination_image, (0, 0, 622, 622)),
        crop_image_region(destination_image, (632, 0, 1254, 622)),
        crop_image_region(destination_image, (0, 632, 622, 1254)),
        crop_image_region(destination_image, (632, 632, 1254, 1254)),
    ]
    picture_done = render_picture_word_cards(
        5,
        "grammar1_picture",
        questions,
        destination_images,
        single_image_width=180,
    )
    st.divider()
    st.markdown(interface_text("### 2. 어디에 가요? 무엇을 해요?", "### 2. Where are you going? What are you doing?"))
    destination = st.selectbox(interface_text("가는 장소", "Destination"), ["세종학당", "공원", "식당", "카페", "마트"], key="unit5_g1_destination")
    action = {"세종학당":"한국어를 공부해요", "공원":"운동해요", "식당":"밥을 먹어요", "카페":"커피를 마셔요", "마트":"장을 봐요"}[destination]
    render_learning_info(f":orange[가: 어디에 가요?]\n\n:blue[나: {destination}에 가요.]\n\n:orange[가: 뭐 해요?]\n\n:blue[나: {action}.]", icon=":material/forum:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit5_g1_speaking_done", type="primary"):
        st.session_state["unit5_grammar1_speaking_completed"] = True
        st.session_state["unit5_g1_checked_destination"] = destination
    speaking_done = st.session_state.get("unit5_grammar1_speaking_completed", False) and st.session_state.get("unit5_g1_checked_destination") == destination
    if not speaking_done:
        st.session_state.pop("unit5_grammar1_speaking_completed", None)
    if picture_done and speaking_done:
        st.session_state["unit5_grammar1_activities_completed"] = True
    else:
        st.session_state.pop("unit5_grammar1_activities_completed", None)


def render_unit5_grammar2():
    render_learning_info(interface_text("두 개 이상의 명사나 사람을 나란히 연결할 때 ‘하고’를 사용해요.", "Use 하고 to connect two or more nouns or people."), icon=":material/school:")
    render_unit5_reference(
        "unit5-department-store-v2.png", "unit5-grammar2-guide",
        "<p><b>가:</b> 수지 씨는 뭘 사요?</p><p><b>나:</b> 신발하고 옷을 사요.</p>"
        "<p><b>가:</b> 유진 씨는 뭘 사요?</p><p><b>나:</b> 화장품하고 가방을 사요.</p>"
        "<p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>명사 + 하고 + 명사</b></p>",
    )
    st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at the picture and complete the Korean dialogue"))
    st.caption(interface_text("각 그림에 함께 있는 두 대상이나 사람을 ‘하고’로 연결하세요.", "Connect the two pictured items, places, or people with 하고."))
    questions = [
        ("뭘 먹어요?", ["케이크하고 빵을 먹어요.", "케이크에 빵을 먹어요.", "케이크를 빵을 먹어요."], "케이크하고 빵을 먹어요.", "두 음식을 ‘하고’로 연결해요."),
        ("뭘 마셔요?", ["우유하고 차를 마셔요.", "우유에 차를 마셔요.", "우유를 차를 마셔요."], "우유하고 차를 마셔요.", "우유와 차를 ‘하고’로 연결해요."),
        ("어디에 가요?", ["영화관하고 카페에 가요.", "영화관에 카페를 가요.", "영화관를 카페에 가요."], "영화관하고 카페에 가요.", "두 장소를 ‘하고’로 연결해요."),
        ("누구를 만나요?", ["선생님하고 수지를 만나요.", "선생님에 수지를 만나요.", "선생님을 수지를 만나요."], "선생님하고 수지를 만나요.", "두 사람을 ‘하고’로 연결해요."),
    ]
    hago_image = Path(__file__).with_name("assets") / "units" / "unit5-hago-pairs-v2.png"
    hago_images = [
        crop_image_region(hago_image, (0, 0, 622, 622)),
        crop_image_region(hago_image, (632, 0, 1254, 622)),
        crop_image_region(hago_image, (0, 632, 622, 1254)),
        crop_image_region(hago_image, (632, 632, 1254, 1254)),
    ]
    first_done = render_picture_word_cards(
        5,
        "grammar2_picture",
        questions,
        hago_images,
        single_image_width=180,
    )
    st.divider()
    st.markdown(interface_text("### 2. 우리 교실에 무엇이 있어요? 누가 있어요?", "### 2. What and who are in our classroom?"))
    item1 = st.selectbox(interface_text("첫 번째 물건", "First object"), ["시계", "칠판", "책상", "컴퓨터"], key="unit5_g2_item1")
    item2 = st.selectbox(interface_text("두 번째 물건", "Second object"), ["칠판", "의자", "책", "가방"], key="unit5_g2_item2")
    item2_particle = subject_particle(item2)
    render_learning_info(f"교실에 {item1}하고 {item2}{item2_particle} 있어요.", icon=":material/forum:")
    if st.button(interface_text("2번 문장을 소리 내어 읽었어요", "I read the Activity 2 sentence aloud"), key="unit5_g2_speaking_done", type="primary"):
        st.session_state["unit5_grammar2_speaking_completed"] = True
        st.session_state["unit5_g2_checked_items"] = (item1, item2)
    speaking_done = st.session_state.get("unit5_grammar2_speaking_completed", False) and st.session_state.get("unit5_g2_checked_items") == (item1, item2)
    if not speaking_done:
        st.session_state.pop("unit5_grammar2_speaking_completed", None)
    if first_done and speaking_done:
        st.session_state["grammar2_done_5"] = True
        render_learning_success(interface_text("문법 2의 1번과 2번 활동을 모두 완료했어요!", "You completed both Grammar 2 activities!"), icon=":material/check_circle:")
    else:
        st.session_state.pop("grammar2_done_5", None)


def render_unit5_activity1():
    st.subheader(interface_text("활동 1 · 마트", "Activity 1 · The Market"))
    st.markdown(interface_text("### 1. 안나 씨와 주노 씨가 이야기해요. 두 사람은 무슨 이야기를 할까요?", "### 1. Read the Korean dialogue and find what they are discussing"))
    st.caption(interface_text("대화를 천천히 읽고 주노 씨가 가는 장소와 사는 물건을 확인하세요.", "Read the dialogue slowly and find Juno’s destination and purchases."))
    render_unit5_reference(
        "unit5-mart-v2.png", "unit5-activity1-guide",
        "<p><b>안나:</b> 주노 씨, 어디에 가요?</p><p><b>주노:</b> 마트에 가요.</p>"
        "<p><b>안나:</b> 뭘 사요?</p><p><b>주노:</b> 빵하고 우유를 사요.</p>",
    )
    questions = [
        ("주노 씨는 어디에 가요?", ["마트에 가요.", "공원에 가요.", "학교에 가요."], "마트에 가요.", "주노가 ‘마트에 가요’라고 말했어요."),
        ("주노 씨는 무엇을 사요?", ["빵하고 우유를 사요.", "옷하고 신발을 사요.", "라면하고 김밥을 먹어요."], "빵하고 우유를 사요.", "마트에서 빵과 우유를 사요."),
    ]
    reading_done = render_choice_set(5, "activity1_reading", questions, hide_reference_button=True)
    st.markdown(interface_text("### 2. 어디에 가요? 무엇을 사요?", "### 2. Where are you going? What are you buying?"))
    place = st.selectbox(interface_text("장소", "Place"), ["식당", "백화점", "학교", "마트"], key="unit5_a1_place")
    items = {"식당":"라면하고 김밥을 먹어요", "백화점":"옷하고 신발을 사요", "학교":"한국어하고 영어를 공부해요", "마트":"빵하고 우유를 사요"}[place]
    render_learning_info(f"가: 어디에 가요?\n\n나: {place}에 가요.\n\n가: 뭘 해요?\n\n나: {items}.", icon=":material/record_voice_over:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit5_a1_speaking_done", type="primary"):
        st.session_state["unit5_activity1_speaking_completed"] = True
        st.session_state["unit5_a1_checked_place"] = place
    speaking_done = st.session_state.get("unit5_activity1_speaking_completed", False) and st.session_state.get("unit5_a1_checked_place") == place
    if not speaking_done:
        st.session_state.pop("unit5_activity1_speaking_completed", None)
    if reading_done and speaking_done:
        st.session_state["activity1_completed_5"] = True
    else:
        st.session_state.pop("activity1_completed_5", None)


def render_unit5_activity2():
    st.subheader(interface_text("활동 2 · 백화점 쇼핑", "Activity 2 · Department Store Shopping"))
    st.markdown(interface_text("### 1. 수지 씨와 유진 씨가 백화점에서 쇼핑을 해요. 두 사람은 무엇을 살까요?", "### 1. Read the Korean shopping dialogue and identify each purchase"))
    st.caption(interface_text("두 사람의 쇼핑 내용을 읽고 수지 씨와 유진 씨가 사는 물건을 각각 확인하세요.", "Read the dialogue and identify what Suji and Yujin buy."))
    render_unit5_reference(
        "unit5-department-store-v2.png", "unit5-activity2-guide",
        "<p><b>수지:</b> 신발하고 옷을 사요.</p><p><b>유진:</b> 화장품하고 가방을 사요.</p>"
        "<p>누가 무엇을 사는지 확인한 뒤 여러분의 쇼핑 계획을 써 보세요.</p>",
    )
    questions = [
        ("수지 씨는 무엇을 사요?", ["신발하고 옷을 사요.", "화장품하고 가방을 사요.", "빵하고 우유를 사요."], "신발하고 옷을 사요.", "수지는 신발과 옷을 사요."),
        ("유진 씨는 무엇을 사요?", ["화장품하고 가방을 사요.", "신발하고 옷을 사요.", "과일하고 우유를 사요."], "화장품하고 가방을 사요.", "유진은 화장품과 가방을 사요."),
    ]
    reading_done = render_choice_set(5, "activity2_reading", questions, hide_reference_button=True)
    st.markdown(interface_text("### 2. 여러분은 백화점에 가요. 무엇을 사요? 써 보세요.", "### 2. You are going to a department store. What will you buy?"))
    first = st.selectbox(interface_text("첫 번째 물건", "First item"), ["옷", "신발", "가방", "화장품", "모자"], key="unit5_a2_first")
    second = st.selectbox(interface_text("두 번째 물건", "Second item"), ["신발", "옷", "가방", "화장품", "모자"], key="unit5_a2_second")
    second_object_particle = "을" if subject_particle(second) == "이" else "를"
    response = f"저는 백화점에 가요. {first}하고 {second}{second_object_particle} 사요."
    render_learning_success(response, icon=":material/edit_note:")
    response_read_aloud = st.checkbox(
        interface_text("완성한 쇼핑 문장을 소리 내어 읽었어요", "I read my completed Korean shopping sentence aloud"),
        key="unit5_a2_response_read_aloud",
        disabled=not (reading_done and first != second),
        on_change=set_session_state_value,
        args=("unit5_a2_checked_response", response),
    )
    response_read_aloud_current = response_read_aloud and st.session_state.get("unit5_a2_checked_response") == response
    if not reading_done:
        st.caption(interface_text("먼저 1번의 두 문제를 모두 맞혀 주세요.", "First, answer both questions in Activity 1 correctly."))
    elif first == second:
        st.caption(interface_text("서로 다른 물건 두 개를 선택해 주세요.", "Choose two different items."))
    elif not response_read_aloud_current:
        st.caption(interface_text("완성한 한국어 쇼핑 문장을 소리 내어 읽고 확인해 주세요.", "Read your completed Korean shopping sentence aloud, then check the box."))
    return response if reading_done and first != second and response_read_aloud_current else ""


def render_unit6_reference(image_name, box_class, box_html, guide_width=500):
    """Show a responsive reference pair and remember its image for nearby questions."""
    global ACTIVE_REFERENCE_IMAGE
    key = f"unit6_{box_class}_pair"
    if english_support_enabled():
        for korean_label, english_label in {
            "형태:": "Pattern:",
            "받침 있음:": "Final consonant:",
            "받침 없음:": "No final consonant:",
            "특별한 형태:": "Special form:",
            "그림을 잘 보세요.": "Study the picture carefully.",
            "물건에 맞는 단위 명사를 골라 말해 보세요.": "Choose the counter that matches each object and say it aloud.",
            "날씨:": "Weather:",
            "상태:": "Conditions:",
            "그림을 보며 표현을 소리 내어 읽어 보세요.": "Look at the picture and read the Korean expressions aloud.",
        }.items():
            box_html = box_html.replace(korean_label, english_label)
    st.markdown(
        f"""
        <style>
        .{box_class} {{
            width:500px; height:334px; box-sizing:border-box; display:flex;
            flex-direction:column; justify-content:center; padding:26px;
            border:1px solid #334155; border-radius:10px; background:#172033;
            color:#dbe7f5; line-height:1.65;
        }}
        .{box_class} p {{ margin:6px 0; }}
        div.st-key-{key} {{ width:1016px; max-width:100%; margin-top:-16px; }}
        div.st-key-{key} [data-testid="stHorizontalBlock"] {{
            display:grid; grid-template-columns:500px 500px;
            gap:16px !important; align-items:start; justify-content:start;
        }}
        div.st-key-{key} [data-testid="stColumn"] {{
            width:500px !important; min-width:500px !important; height:334px !important;
        }}
        div.st-key-{key} [data-testid="stColumn"]:first-child {{
            position:sticky; top:1rem; align-self:flex-start;
        }}
        div.st-key-{key} [data-testid="stImage"],
        div.st-key-{key} [data-testid="stImage"] img {{
            width:500px !important; height:334px !important;
            object-fit:cover; display:block;
        }}
        @media (max-width:760px) {{
            div.st-key-{key} {{ width:100%; }}
            div.st-key-{key} [data-testid="stHorizontalBlock"] {{
                display:grid; grid-template-columns:minmax(0, 1fr); gap:12px !important;
            }}
            div.st-key-{key} [data-testid="stColumn"] {{
                width:100% !important; min-width:0 !important; height:auto !important;
            }}
            div.st-key-{key} [data-testid="stColumn"]:first-child {{ position:static; }}
            .{box_class} {{ width:100%; height:auto; min-height:0; aspect-ratio:500/334; padding:18px; }}
            div.st-key-{key} [data-testid="stImage"],
            div.st-key-{key} [data-testid="stImage"] img {{
                width:100% !important; height:auto !important; object-fit:contain;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key=key):
        image_column, guide_column = st.columns(2, gap=None, vertical_alignment="top")
        with image_column:
            image_path = Path(__file__).with_name("assets") / "units" / image_name
            ACTIVE_REFERENCE_IMAGE = str(image_path)
            st.image(fit_image_to_canvas(image_path, canvas_size=(500, 334), image_size=(500, 334)), width="stretch")
        with guide_column:
            unit_number = st.session_state.get("selected_unit_number", 6)
            highlighted_html = highlight_learning_text(box_html, unit_number, escape=False)
            st.markdown(f'<div class="{box_class}">{highlighted_html}</div>', unsafe_allow_html=True)


def render_unit6_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    st.markdown(interface_text("### 1. 숫자와 단위 명사를 확인하고 소리 내어 읽어 보세요.", "### 1. Review Korean numbers and counters, then read them aloud"))
    render_unit6_reference(
        "unit6-quantity-grid.png", "unit6-vocab-guide",
        "<p><b>1–10:</b> 하나(한), 둘(두), 셋(세), 넷(네), 다섯, 여섯, 일곱, 여덟, 아홉, 열</p>"
        "<p><b>20–100:</b> 스물(스무), 서른, 마흔, 쉰, 예순, 일흔, 여든, 아흔, 백</p>"
        "<p><b>예:</b> 사과 한 개 / 연필 두 자루 / 책 세 권 / 학생 네 명</p>",
    )
    for start in range(0, len(vocabulary_words), 5):
        columns = st.columns(min(5, len(vocabulary_words) - start))
        for column, index in zip(columns, range(start, min(start + 5, len(vocabulary_words)))):
            with column:
                st.button(vocabulary_words[index], key=f"vocab_select_6_{index}",
                          type="primary" if index == active_index else "secondary",
                          on_click=select_vocabulary, args=(6, index), width="stretch")
    render_vocabulary_example(example_sentence, color="lime")
    st.divider()
    st.markdown(interface_text("### 2. 그림 속 물건은 몇 개 있어요? 알맞은 것을 선택해 보세요.", "### 2. How many objects are in the picture? Choose the correct Korean expression"))
    st.caption(interface_text("왼쪽 그림의 물건을 하나씩 세고, 물건에 맞는 단위 명사까지 확인하세요.", "Count the objects in each picture and choose the matching Korean counter expression."))
    count_questions = [
        ("사과", ["열 개", "여섯 개", "다섯 개"], "열 개", "사과는 모두 열 개예요."),
        ("연필", ["여섯 자루", "네 자루", "열 자루"], "여섯 자루", "연필은 모두 여섯 자루예요."),
        ("책", ["열 권", "다섯 권", "여덟 권"], "열 권", "책은 모두 열 권이에요."),
        ("우유", ["열 개", "일곱 개", "아홉 개"], "열 개", "우유는 모두 열 개예요."),
    ]
    quantity_image = Path(__file__).with_name("assets") / "units" / "unit6-quantity-grid.png"
    count_images = [
        crop_image_region(quantity_image, (18, 24, 378, 494)),
        crop_image_region(quantity_image, (18, 510, 378, 994)),
        crop_image_region(quantity_image, (1150, 24, 1518, 494)),
        crop_image_region(quantity_image, (1150, 510, 1518, 994)),
    ]
    count_done = render_picture_word_cards(
        6, "vocab_count", count_questions, count_images, single_image_width=180
    )
    st.divider()
    st.markdown(interface_text("### 3. 그림을 보고 대화를 완성해 보세요.", "### 3. Look at the picture and complete each Korean dialogue"))
    st.caption(interface_text("왼쪽 그림의 물건 수를 세고 알맞은 고유어 수 표현을 선택하세요.", "Count the objects on the left and choose the correct native Korean number expression."))
    dialogues = [
        ("가: 뭘 사요?\n나: 펜을 ______ 사요.", ["다섯 개", "다섯 명", "오 개"], "다섯 개", "펜은 다섯 개를 사요."),
        ("가: 뭘 사요?\n나: 지우개를 ______ 사요.", ["두 개", "둘 개", "이 명"], "두 개", "단위 명사 앞에서 ‘둘’은 ‘두’가 돼요."),
        ("가: 뭘 사요?\n나: 라면을 ______ 사요.", ["네 개", "넷 개", "사 개"], "네 개", "단위 명사 앞에서 ‘넷’은 ‘네’가 돼요."),
        ("가: 뭘 사요?\n나: 달걀을 ______ 사요.", ["열 개", "십 명", "열 권"], "열 개", "달걀은 ‘개’로 세어요."),
    ]
    shopping_quantity_image = Path(__file__).with_name("assets") / "units" / "unit6-shopping-quantities-v2.png"
    dialogue_images = [
        crop_image_region(shopping_quantity_image, (0, 0, 622, 622)),
        crop_image_region(shopping_quantity_image, (632, 0, 1254, 622)),
        crop_image_region(shopping_quantity_image, (0, 632, 622, 1254)),
        crop_image_region(shopping_quantity_image, (632, 632, 1254, 1254)),
    ]
    dialogue_done = render_picture_word_cards(
        6,
        "vocab_dialogue",
        dialogues,
        dialogue_images,
        dialogue_layout=True,
        single_image_width=180,
    )
    return count_done and dialogue_done


def render_unit6_grammar1_intro():
    render_learning_info(interface_text("사람이나 물건을 셀 때 대상에 맞는 단위 명사를 사용해요.", "Use a Korean counter that matches the person or object you are counting."), icon=":material/school:")
    render_unit6_reference(
        "unit6-counter-dialogue.png", "unit6-grammar1-guide",
        "<p><b style='color:#ff9b72'>가:</b> 뭘 사요?　<b style='color:#78aef8'>나:</b> 빵을 한 개 사요.</p>"
        "<p><b style='color:#ff9b72'>가:</b> 학생이 몇 명 있어요?　<b style='color:#78aef8'>나:</b> 두 명 있어요.</p>"
        "<p><b>형태:</b> <b style='color:#b7ef58'>고유어 수 + 단위 명사</b></p>"
        "<p><b>개:</b> 물건을 셀 때 사용해요. 예: 빵을 한 개 사요.</p><p><b>명:</b> 사람을 셀 때 사용해요. 예: 학생이 두 명 있어요.</p>"
        "<p><b>마리</b> 동물　<b>잔·병</b> 음료　<b>권</b> 책　<b>장</b> 종이　<b>살</b> 나이</p>",
    )
    st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at each picture and complete the Korean dialogue"))
    st.caption(interface_text("물건을 세고 ‘개·권·병·자루’ 중 알맞은 단위 명사가 들어간 대답을 선택하세요.", "Count each object and choose the answer with the correct Korean counter."))
    questions = [
        ("사과가 몇 개 있어요?", ["열 개 있어요.", "여섯 개 있어요.", "다섯 개 있어요."], "열 개 있어요.", "그림의 사과는 열 개이고, 물건은 ‘개’로 세어요."),
        ("책이 몇 권 있어요?", ["열 권 있어요.", "다섯 권 있어요.", "여덟 권 있어요."], "열 권 있어요.", "그림의 책은 열 권이고, 책은 ‘권’으로 세어요."),
        ("물이 몇 병 있어요?", ["열 병 있어요.", "네 병 있어요.", "여섯 병 있어요."], "열 병 있어요.", "그림의 물병은 열 병이고, 병에 든 물은 ‘병’으로 세어요."),
        ("연필이 몇 자루 있어요?", ["여섯 자루 있어요.", "열 자루 있어요.", "두 자루 있어요."], "여섯 자루 있어요.", "그림의 연필은 여섯 자루이고, 연필은 ‘자루’로 세어요."),
    ]
    quantity_image = Path(__file__).with_name("assets") / "units" / "unit6-quantity-grid.png"
    grammar1_images = [
        crop_image_region(quantity_image, (18, 24, 378, 494)),
        crop_image_region(quantity_image, (1150, 24, 1518, 494)),
        crop_image_region(quantity_image, (782, 24, 1134, 494)),
        crop_image_region(quantity_image, (18, 510, 378, 994)),
    ]
    picture_done = render_picture_word_cards(
        6, "grammar1_picture", questions, grammar1_images, single_image_width=180
    )
    st.divider()
    st.markdown(interface_text("### 2. 교실 물건과 수량을 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a classroom object and quantity, then read the Korean dialogue aloud"))
    item = st.selectbox(interface_text("교실 물건", "Classroom object"), ["책상", "의자", "컴퓨터", "시계"], key="unit6_g1_item")
    count = st.selectbox(interface_text("수량", "Quantity"), ["한 개", "두 개", "세 개", "네 개", "다섯 개"], key="unit6_g1_count")
    item_particle = subject_particle(item)
    render_learning_info(f"가: 교실에 {item}{item_particle} 몇 개 있어요?\n\n나: {item}{item_particle} {count} 있어요.", icon=":material/forum:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit6_g1_speaking_done", type="primary"):
        st.session_state["unit6_grammar1_speaking_completed"] = True
        st.session_state["unit6_g1_checked_choice"] = (item, count)
    speaking_done = st.session_state.get("unit6_grammar1_speaking_completed", False) and st.session_state.get("unit6_g1_checked_choice") == (item, count)
    if not speaking_done:
        st.session_state.pop("unit6_grammar1_speaking_completed", None)
    done = picture_done and speaking_done
    if done:
        st.session_state["unit6_grammar1_activities_completed"] = True
    else:
        st.session_state.pop("unit6_grammar1_activities_completed", None)


def render_unit6_grammar2():
    render_learning_info(interface_text("상대방에게 공손하게 행동을 요청할 때 동사에 ‘-(으)세요’를 붙여요.", "Add -(으)세요 to a verb to make a polite Korean request."), icon=":material/school:")
    render_unit6_reference(
        "unit6-euseyo-actions-v2.png", "unit6-grammar2-guide",
        "<p><b>형태:</b> <b style='color:#b7ef58'>동사 + -(으)세요</b></p>"
        "<p><b>위 왼쪽 · 받침 있음:</b> 앉다 → <b style='color:#78aef8'>앉으세요</b>.</p>"
        "<p><b>위 오른쪽 · 특별한 형태:</b> 듣다 → <b style='color:#78aef8'>들으세요</b>.</p>"
        "<p><b>아래 왼쪽 · 하다:</b> 대답하다 → <b style='color:#78aef8'>대답하세요</b>.</p>"
        "<p><b>아래 오른쪽 · 받침 없음:</b> 펴다 → <b style='color:#78aef8'>펴세요</b>.</p>",
    )
    st.markdown(interface_text("### 1. 문장에 알맞은 요청 표현을 선택해 보세요.", "### 1. Choose the correct Korean request expression"))
    st.caption(interface_text("위 그림을 왼쪽 위부터 차례로 보며 상황에 알맞은 공손한 요청을 선택하세요.", "Read the four pictures from the top left and choose the polite request for each situation."))
    questions = [
        ("의자에", ["앉으세요.", "읽으세요.", "타세요."], "앉으세요.", "의자에는 ‘앉으세요’가 자연스러워요."),
        ("선생님 말을", ["들으세요.", "오세요.", "주세요."], "들으세요.", "말이나 음악은 ‘들으세요’라고 해요."),
        ("질문에", ["대답하세요.", "앉으세요.", "타세요."], "대답하세요.", "질문에는 ‘대답하세요’라고 해요."),
        ("책을", ["펴세요.", "오세요.", "주세요."], "펴세요.", "책을 펼치라는 요청은 ‘책을 펴세요’예요."),
    ]
    first_done = render_choice_set(6, "grammar2_connect", questions, hide_reference_button=True)
    st.divider()
    st.markdown(interface_text("### 2. 그림을 보고 문장을 완성해 보세요.", "### 2. Look at the picture and complete each Korean sentence"))
    forms = [
        ("책을 ______. (읽다)", ["읽으세요", "읽세요", "읽어요세요"], "읽으세요", "받침이 있으므로 ‘-으세요’를 붙여요."),
        ("물 한 병 ______. (주다)", ["주세요", "주으세요", "줘요세요"], "주세요", "‘주다’는 ‘주세요’가 돼요."),
        ("여기로 ______. (오다)", ["오세요", "오으세요", "와요세요"], "오세요", "받침이 없으므로 ‘-세요’를 붙여요."),
        ("버스를 ______. (타다)", ["타세요", "타으세요", "타요세요"], "타세요", "받침이 없으므로 ‘-세요’를 붙여요."),
    ]
    request_image = Path(__file__).with_name("assets") / "units" / "unit6-request-actions-v2.png"
    request_images = [
        crop_image_region(request_image, (0, 0, 622, 622)),
        crop_image_region(request_image, (632, 0, 1254, 622)),
        crop_image_region(request_image, (0, 632, 622, 1254)),
        crop_image_region(request_image, (632, 632, 1254, 1254)),
    ]
    second_done = render_picture_word_cards(
        6, "grammar2_forms", forms, request_images, single_image_width=180
    )
    if first_done and second_done:
        st.session_state["grammar2_done_6"] = True
        render_learning_success(interface_text("문법 2의 1번과 2번을 모두 완료했어요.", "You completed both Grammar 2 activities."), icon=":material/check_circle:")
    else:
        st.session_state.pop("grammar2_done_6", None)


def render_unit6_activity1():
    st.subheader(interface_text("활동 1 · 과일 가게", "Activity 1 · Fruit Shop"))
    st.markdown(interface_text("### 1. 안나 씨가 과일 가게에 가요. 무슨 이야기를 할까요?", "### 1. Read the Korean fruit-shop dialogue and answer"))
    st.caption(interface_text("그림과 대화를 함께 보고 안나 씨가 사는 과일의 수량과 전체 가격을 확인하세요.", "Use the picture and dialogue to identify the fruit quantity and total price."))
    render_unit6_reference(
        "unit6-fruit-shop.png", "unit6-activity1-guide",
        "<p><b>주인:</b> 어서 오세요. 무엇을 드릴까요?</p><p><b>안나:</b> 사과 얼마예요?</p>"
        "<p><b>주인:</b> 한 개에 2,000원이에요.</p><p><b>안나:</b> 사과 다섯 개 주세요.</p>"
        "<p><b>주인:</b> 네, 모두 10,000원이에요.</p>",
    )
    reading_done = render_choice_set(6, "activity1_reading", [
        ("안나 씨는 무엇을 사요?", ["사과 다섯 개", "배 다섯 개", "사과 두 개"], "사과 다섯 개", "안나 씨는 사과 다섯 개를 사요."),
        ("모두 얼마예요?", ["10,000원", "2,000원", "5,000원"], "10,000원", "2,000원짜리 사과 다섯 개는 10,000원이에요."),
    ], hide_reference_button=True)
    st.markdown(interface_text("### 2. 무엇을 사요? 과일과 수량을 정하고 대화해 보세요.", "### 2. Choose a fruit and quantity, then practice the Korean dialogue"))
    fruit = st.selectbox(interface_text("과일", "Fruit"), ["사과", "배", "귤", "복숭아"], key="unit6_a1_fruit")
    quantity = st.selectbox(interface_text("수량", "Quantity"), ["한 개", "두 개", "세 개", "네 개", "다섯 개"], key="unit6_a1_quantity")
    render_learning_info(f"주인: 무엇을 드릴까요?\n\n나: {fruit} {quantity} 주세요.", icon=":material/record_voice_over:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit6_a1_speaking_done", type="primary"):
        st.session_state["unit6_activity1_speaking_completed"] = True
        st.session_state["unit6_a1_checked_choice"] = (fruit, quantity)
    speaking_done = st.session_state.get("unit6_activity1_speaking_completed", False) and st.session_state.get("unit6_a1_checked_choice") == (fruit, quantity)
    if not speaking_done:
        st.session_state.pop("unit6_activity1_speaking_completed", None)
    if reading_done and speaking_done:
        st.session_state["activity1_completed_6"] = True
    else:
        st.session_state.pop("activity1_completed_6", None)


def render_unit6_activity2():
    st.subheader(interface_text("활동 2 · 편의점", "Activity 2 · Convenience Store"))
    st.markdown(interface_text("### 1. 유진 씨가 편의점에 가요. 무엇을 몇 개 살까요?", "### 1. Read the Korean convenience-store dialogue and identify the quantities"))
    st.caption(interface_text("그림과 대화를 함께 보고 물건별 수량과 전체 가격을 확인하세요.", "Use the picture and dialogue to identify each quantity and the total price."))
    render_unit6_reference(
        "unit6-convenience-store.png", "unit6-activity2-guide",
        "<p><b>유진:</b> 지우개 두 개하고 칫솔 다섯 개 주세요.</p>"
        "<p><b>직원:</b> 네. 더 필요하세요?</p><p><b>유진:</b> 아이스크림 한 개도 주세요.</p>"
        "<p><b>직원:</b> 모두 24,000원이에요.</p>",
    )
    reading_done = render_choice_set(6, "activity2_reading", [
        ("유진 씨는 무엇을 몇 개 사요?", ["지우개 두 개, 칫솔 다섯 개, 아이스크림 한 개", "지우개 다섯 개, 칫솔 두 개", "칫솔 한 개, 아이스크림 다섯 개"], "지우개 두 개, 칫솔 다섯 개, 아이스크림 한 개", "그림과 대화의 세 물건과 수량을 확인하세요."),
        ("모두 얼마예요?", ["24,000원", "10,000원", "20,000원"], "24,000원", "직원이 모두 24,000원이라고 말했어요."),
    ], hide_reference_button=True)
    st.markdown(interface_text("### 2. 여러분이 편의점에 가요. 무엇을 몇 개 사요? 써 보세요.", "### 2. You are going to a convenience store. What will you buy?"))
    unit6_choose_label = interface_text("선택하세요", "Choose")
    options = [unit6_choose_label, "지우개", "칫솔", "아이스크림", "물", "과자", "우유"]
    cols = st.columns(3)
    selected = []
    for index, column in enumerate(cols):
        with column:
            item = st.selectbox(interface_text(f"{index + 1}번째 물건", f"Item {index + 1}"), options, key=f"unit6_a2_item_{index}")
            count = st.selectbox(interface_text(f"{index + 1}번째 수량", f"Quantity {index + 1}"), ["한 개", "두 개", "세 개", "네 개", "다섯 개"], key=f"unit6_a2_count_{index}")
            selected.append((item, count))
    ready = all(item != unit6_choose_label for item, _ in selected) and len({item for item, _ in selected}) == 3
    response = ""
    if ready:
        response = f"저는 편의점에서 {selected[0][0]} {selected[0][1]}하고 {selected[1][0]} {selected[1][1]}, {selected[2][0]} {selected[2][1]}를 사요."
        render_learning_success(response, icon=":material/edit_note:")
    else:
        st.caption(interface_text("서로 다른 물건 세 가지와 수량을 모두 선택해 주세요.", "Choose three different objects and a quantity for each."))
    response_read_aloud = st.checkbox(
        interface_text("완성한 수량 문장을 소리 내어 읽었어요", "I read my completed Korean quantity sentence aloud"),
        key="unit6_a2_response_read_aloud",
        disabled=not (reading_done and ready),
        on_change=set_session_state_value,
        args=("unit6_a2_checked_response", response),
    )
    response_read_aloud_current = response_read_aloud and st.session_state.get("unit6_a2_checked_response") == response
    return response if reading_done and ready and response_read_aloud_current else ""


def render_unit8_grammar1():
    render_learning_info(interface_text("동사나 형용사 앞에 ‘안’을 넣어 하지 않거나 그렇지 않다고 말해요.", "Place 안 before a verb or adjective to say that something is not done or is not so."), icon=":material/school:")
    render_unit6_reference("unit8-an-negative.png", "unit8-grammar1-guide", "<p><b>가:</b> 서울은 날씨가 좋아요?</p><p><b>나:</b> 아니요. <b style='color:#78aef8'>안</b> 좋아요. 비가 와요.</p><p><b>가:</b> 오늘 운동해요?</p><p><b>나:</b> 아니요. 운동 <b style='color:#78aef8'>안</b> 해요.</p><p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>안 + 동사/형용사</b></p>")
    st.markdown(interface_text("### 1. 다음 예와 같이 빈칸에 알맞은 표현을 선택해 대화를 완성해 보세요.", "### 1. Complete each Korean dialogue by choosing the correct expression"))
    render_learning_info(
        "**예**\n\n가: 오늘 날씨가 추워요?\n\n나: 아니요. 오늘은 :orange[**안**] 추워요.",
        icon=":material/lightbulb:",
    )
    qs=[("오늘 영화를 봐요?\n아니요. 영화를 ___ 봐요.",["안","못","아주"],"안","‘안’은 동사 앞에 와요."),("주말에 공원에 가요?\n아니요. ___ 가요.",["안","잘","매우"],"안","하지 않는다는 뜻이에요."),("오늘 바빠요?\n아니요. ___ 바빠요.",["안","못","정말"],"안","형용사 앞에도 ‘안’을 써요."),("일요일에 쇼핑해요?\n아니요. 쇼핑 ___ 해요.",["안","더","아주"],"안","동사 앞에 ‘안’을 넣어요.")]
    first=render_choice_set(8,"grammar1_an",qs)
    st.markdown(interface_text("### 2. 다음 예를 보고 ‘안’을 사용해 대화를 만들어 보세요.", "### 2. Use 안 to build a Korean dialogue"))
    st.markdown(
        "| 질문 | 대답 |\n"
        "|---|---|\n"
        "| 오늘 운동해요? | 아니요. 오늘 운동 :orange[**안**] 해요. |\n"
        "| 주말에 영화를 봐요? | 아니요. 주말에 영화를 :orange[**안**] 봐요. |"
    )
    action = st.selectbox(
        interface_text("활동을 선택하세요.", "Activity to practice"),
        ["운동해요", "영화를 봐요", "쇼핑해요", "요리해요"],
        key="unit8_g1_action",
    )
    negative_action = {
        "운동해요": "운동 :orange[**안**] 해요",
        "영화를 봐요": "영화를 :orange[**안**] 봐요",
        "쇼핑해요": "쇼핑 :orange[**안**] 해요",
        "요리해요": "요리 :orange[**안**] 해요",
    }[action]
    render_learning_info(
        f"가: 오늘 {action}?\n\n나: 아니요. 오늘 {negative_action}.",
        icon=":material/forum:",
    )
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"),key="unit8_g1_speaking",type="primary"): st.session_state["unit8_g1_speaking_done"]=True; st.session_state["unit8_g1_checked_action"]=action
    speaking_done=st.session_state.get("unit8_g1_speaking_done",False) and st.session_state.get("unit8_g1_checked_action")==action
    if not speaking_done: st.session_state.pop("unit8_g1_speaking_done",None)
    if first and speaking_done: st.session_state["unit8_grammar1_activities_completed"]=True
    else: st.session_state.pop("unit8_grammar1_activities_completed",None)


def render_unit8_grammar2():
    render_learning_info(interface_text("ㅂ 받침으로 끝나는 일부 형용사는 모음으로 시작하는 어미 앞에서 ㅂ이 우/오로 바뀌어요.", "Some adjectives ending in ㅂ change ㅂ to 우 or 오 before a vowel-ending form."), icon=":material/school:")
    render_unit6_reference("unit8-b-irregular.png", "unit8-grammar2-guide", "<p><b>가:</b> 날씨가 어때요?</p><p><b>나:</b> 좀 <b style='color:#78aef8'>추워요</b>.</p><p><b>가:</b> 가방이 무거워요?</p><p><b>나:</b> 아니요. <b style='color:#78aef8'>가벼워요</b>.</p><p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>춥다 → 추워요, 무겁다 → 무거워요</b></p>")
    st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at the picture and complete each Korean dialogue"))
    qs=[("한국은 겨울 날씨가 어때요?\n좀 ___.",["추워요","춥어요","추어요"],"추워요","춥다 → 추워요로 바뀌어요."),("공부가 어려워요?\n네. 좀 ___.",["어려워요","어렵어요","어려어요"],"어려워요","어렵다 → 어려워요로 바뀌어요."),("김치가 어때요?\n맛있어요. 그런데 좀 ___.",["매워요","맵어요","매어요"],"매워요","맵다 → 매워요로 바뀌어요."),("가방이 무거워요?\n네, 아주 ___.",["무거워요","무겁어요","무거어요"],"무거워요","무겁다 → 무거워요로 바뀌어요.")]
    st.markdown(
        """
        <style>
        div.st-key-unit8_grammar2_exercise_pair {
            margin-top:-16px;
        }
        div.st-key-unit8_grammar2_exercise_pair [data-testid="stHorizontalBlock"] {
            display:grid; grid-template-columns:500px minmax(0, 1fr);
            gap:16px !important; align-items:start;
        }
        div.st-key-unit8_grammar2_exercise_pair [data-testid="stColumn"]:first-child {
            width:500px !important; min-width:500px !important;
        }
        @media (max-width:900px) {
            div.st-key-unit8_grammar2_exercise_pair [data-testid="stHorizontalBlock"] {
                grid-template-columns:minmax(0, 1fr);
            }
            div.st-key-unit8_grammar2_exercise_pair [data-testid="stColumn"]:first-child {
                width:100% !important; min-width:0 !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="unit8_grammar2_exercise_pair"):
        picture_column, question_column = st.columns(2, gap=None, vertical_alignment="top")
        with picture_column:
            st.image(
                Path(__file__).with_name("assets") / "units" / "unit8-b-irregular-exercises.png",
                width="stretch",
            )
            st.caption(interface_text("왼쪽 위부터 1번, 2번, 3번, 4번 순서예요.", "The pictures run in order from top left: 1, 2, 3, 4."))
        with question_column:
            first=render_choice_set(8,"grammar2_b",qs)
    st.markdown(interface_text("### 2. 대상과 알맞은 상태를 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a subject and condition, then read the Korean dialogue aloud"))
    st.markdown(
        """
        <table style="width:760px;max-width:100%">
            <thead><tr><th>대상</th><th>질문</th><th>대답</th></tr></thead>
            <tbody>
                <tr><td>날씨</td><td>지금 날씨가 어때요?</td><td>좀 <b style="color:#ff9b72">추워요</b>.</td></tr>
                <tr><td>가방</td><td>이 가방이 어때요?</td><td>아주 <b style="color:#ff9b72">무거워요</b>.</td></tr>
            </tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )
    subject = st.selectbox(
        interface_text("무엇의 상태를 말할까요?", "What condition will you describe?"),
        ["날씨", "가방"],
        key="unit8_g2_subject",
    )
    state_options = {
        "날씨": ["추워요", "더워요"],
        "가방": ["무거워요", "가벼워요"],
    }
    state = st.selectbox(
        interface_text("알맞은 상태를 선택하세요.", "Choose the condition"),
        state_options[subject],
        key="unit8_g2_state",
    )
    question = "지금 날씨가 어때요?" if subject == "날씨" else "이 가방이 어때요?"
    degree = "좀" if subject == "날씨" else "아주"
    render_learning_info(
        f"가: {question}\n\n나: {degree} :orange[**{state}**].",
        icon=":material/forum:",
    )
    if st.button(interface_text("2번 대화를 소리 내어 읽었어요", "I read the Activity 2 dialogue aloud"),key="unit8_g2_speaking",type="primary"): st.session_state["unit8_g2_speaking_done"]=True; st.session_state["unit8_g2_checked_choice"]=(subject,state)
    speaking_done=st.session_state.get("unit8_g2_speaking_done",False) and st.session_state.get("unit8_g2_checked_choice")== (subject,state)
    if not speaking_done: st.session_state.pop("unit8_g2_speaking_done",None)
    if first and speaking_done: st.session_state["grammar2_done_8"]=True
    else: st.session_state.pop("grammar2_done_8",None)


def render_unit8_activity1():
    st.subheader(interface_text("활동 1 · 서울과 부산의 날씨", "Activity 1 · Weather in Seoul and Busan"))
    st.markdown(interface_text("### 1. 민호 씨와 지은 씨가 날씨 이야기를 해요. 두 사람은 무슨 이야기를 할까요?", "### 1. Read the Korean weather dialogue and answer"))
    render_unit6_reference("unit8-seoul-busan.png", "unit8-activity1-guide", "<p><b>민호:</b> 지은 씨, 잘 지내요? 부산은 날씨가 더워요?</p><p><b>지은:</b> 네. 잘 지내요. 부산은 정말 더워요. 서울은 어때요?</p><p><b>민호:</b> 서울은 안 더워요. 요즘 비가 자주 와요.</p><p><b>지은:</b> 그래요? 부산은 비가 안 와요.</p>")
    first=render_choice_set(8,"activity1_weather",[("민호 씨는 지금 어디에 있어요?",["서울","부산","제주도"],"서울","그림의 왼쪽은 서울이에요."),("서울의 날씨는 어때요?",["비가 자주 와요.","정말 더워요.","눈이 와요."],"비가 자주 와요.","서울은 비가 오는 장면이에요.")])
    st.markdown(interface_text("### 2. 지역과 날씨를 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a region and weather, then read the Korean dialogue aloud"))
    render_learning_info(
        "**예**\n\n가: 서울은 날씨가 어때요?\n\n나: 서울은 :orange[**비가 자주 와요**].",
        icon=":material/lightbulb:",
    )
    region = st.selectbox(interface_text("지역", "Region"),["서울","부산","하와이","시드니"],key="unit8_a1_city")
    weather = st.selectbox(interface_text("날씨", "Weather"),["비가 자주 와요","더워요","안 추워요","쌀쌀해요","추워요"],key="unit8_a1_weather")
    region_topic = "은" if subject_particle(region) == "이" else "는"
    render_learning_info(
        f"가: {region}{region_topic} 날씨가 어때요?\n\n나: {region}{region_topic} :orange[**{weather}**].",
        icon=":material/forum:",
    )
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"),key="unit8_a1_speaking",type="primary"): st.session_state["unit8_a1_speaking_done"]=True; st.session_state["unit8_a1_checked_choice"]=(region,weather)
    speaking_done=st.session_state.get("unit8_a1_speaking_done",False) and st.session_state.get("unit8_a1_checked_choice")== (region,weather)
    if not speaking_done: st.session_state.pop("unit8_a1_speaking_done",None)
    if first and speaking_done: st.session_state["activity1_completed_8"]=True
    else: st.session_state.pop("activity1_completed_8",None)


def render_unit8_activity2():
    st.subheader(interface_text("활동 2 · 제주도의 사계절", "Activity 2 · Jeju’s Four Seasons"))
    st.markdown(interface_text("### 1. 수지 씨가 제주도의 사계절을 소개해요. 제주도는 어떤 곳일까요?", "### 1. Read Suji’s Korean description of Jeju’s four seasons"))
    render_unit6_reference(
        "unit8-jeju-seasons.png",
        "unit8-activity2-guide",
        "<p><b>예:</b> 제 고향은 제주도예요.</p>"
        "<p><b>봄:</b> 아주 <b style='color:#78aef8'>따뜻해요</b>. 꽃이 예뻐요.</p>"
        "<p><b>여름:</b> <b style='color:#78aef8'>더워요</b>. 바다가 아주 시원해요.</p>"
        "<p><b>가을:</b> <b style='color:#78aef8'>시원해요</b>.</p>"
        "<p><b>겨울:</b> 많이 <b style='color:#78aef8'>추워요</b>.</p>",
    )
    first=render_choice_set(8,"activity2_jeju",[("제주도의 봄은 어때요?",["따뜻해요","추워요","더워요"],"따뜻해요","봄은 따뜻해요."),("제주도의 여름은 어때요?",["더워요","쌀쌀해요","추워요"],"더워요","여름은 더워요."),("제주도의 가을은 어때요?",["시원해요","더워요","추워요"],"시원해요","가을은 시원해요."),("제주도의 겨울은 어때요?",["추워요","시원해요","따뜻해요"],"추워요","겨울은 추워요.")])
    st.markdown(interface_text("### 2. 고향을 입력하고 계절과 날씨를 선택해 소개 문장을 완성한 뒤 소리 내어 읽어 보세요.", "### 2. Enter your hometown, choose a season and weather, and read the Korean introduction aloud"))
    render_learning_info(
        "**예**\n\n제 고향은 부산이에요. 여름은 :orange[**더워요**].",
        icon=":material/lightbulb:",
    )
    place_column, season_column, weather_column = st.columns(3, gap="small")
    with place_column:
        place = st.text_input(interface_text("고향", "Hometown (in Korean)"),placeholder=interface_text("예: 부산", "Example: 부산"),key="unit8_a2_place")
    with season_column:
        season = st.selectbox(interface_text("계절", "Season"),["봄","여름","가을","겨울"],key="unit8_a2_season")
    with weather_column:
        weather = st.selectbox(interface_text("날씨", "Weather"),["따뜻해요","더워요","시원해요","추워요"],key="unit8_a2_weather")
    place_name = place.strip()
    place_ending = "이에요" if place_name and subject_particle(place_name) == "이" else "예요"
    response=f"제 고향은 {place_name}{place_ending}. {season}은 {weather}." if place_name else ""
    if response:
        render_learning_success(
            f"제 고향은 {place_name}{place_ending}. {season}은 :orange[**{weather}**].",
            icon=":material/edit_note:",
        )
    response_read_aloud=st.checkbox(interface_text("완성한 고향·계절 문장을 소리 내어 읽었어요", "I read my completed Korean hometown-and-season sentence aloud"), key="unit8_a2_response_read_aloud", disabled=not (first and response), on_change=set_session_state_value, args=("unit8_a2_checked_response", response))
    return response if first and response and response_read_aloud and st.session_state.get("unit8_a2_checked_response")==response else ""


def render_unit8_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    st.markdown(interface_text("### 1. 알고 있는 날씨 표현에 ✓ 표시를 해 보세요. 표현을 소리 내어 읽어 보세요.", "### 1. Select the Korean weather expressions you know and read them aloud"))
    render_unit6_reference("unit8-weather-seasons.png", "unit8-vocab-guide", "<p><b>날씨:</b> 맑아요, 흐려요, 비가 와요, 눈이 와요, 바람이 불어요</p><p><b>상태:</b> 따뜻해요, 더워요, 시원해요, 쌀쌀해요, 추워요</p><p>그림을 보며 표현을 소리 내어 읽어 보세요.</p>")
    for start in range(0,len(vocabulary_words),5):
        cols=st.columns(min(5,len(vocabulary_words)-start))
        for col,index in zip(cols,range(start,min(start+5,len(vocabulary_words)))):
            with col: st.button(vocabulary_words[index],key=f"vocab_select_8_{index}",type="primary" if index==active_index else "secondary",on_click=select_vocabulary,args=(8,index),width="stretch")
    render_vocabulary_example(example_sentence,color="lime")
    st.divider(); st.markdown(interface_text("### 2. 한국에는 사계절이 있어요. 계절에 알맞은 날씨 표현을 선택해 보세요.", "### 2. Korea has four seasons. Choose the weather expression that matches each season"))
    seasons=[("봄",["따뜻해요","추워요","눈이 와요"],"따뜻해요","봄은 따뜻해요."),("여름",["더워요","쌀쌀해요","눈이 와요"],"더워요","여름은 더워요."),("가을",["시원해요","더워요","추워요"],"시원해요","가을은 시원해요."),("겨울",["추워요","따뜻해요","비가 와요"],"추워요","겨울은 추워요.")]
    second=render_choice_set(8,"vocab_season",seasons)
    st.divider(); st.markdown(interface_text("### 3. 대화를 읽고 빈칸에 알맞은 날씨 표현을 선택해 보세요.", "### 3. Read the Korean dialogue and choose the correct weather expression"))
    third=render_choice_set(8,"vocab_weather_dialogue",[("가: 이번 주말에 날씨가 어때요?\n나: 날씨가 ___, 따뜻해요.",["맑아요","추워요","눈이 와요"],"맑아요","날씨와 상태를 함께 말해요."),("가: 겨울 날씨가 어때요?\n나: 아주 ___.",["추워요","더워요","시원해요"],"추워요","겨울은 추워요."),("가: 비가 와요?\n나: 아니요. 날씨가 ___.",["맑아요","비가 와요","눈이 와요"],"맑아요","비가 오지 않고 맑은 날씨예요.")],dialogue_layout=True)
    return second and third


UNIT9_PLACE_ACTIONS = {
    "공원": ["산책했어요", "운동했어요"],
    "도서관": ["공부했어요", "책을 읽었어요"],
    "식당": ["밥을 먹었어요"],
    "집": ["영화를 봤어요", "쉬었어요"],
    "헬스장": ["운동했어요"],
    "백화점": ["쇼핑했어요"],
}


def render_unit9_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    st.markdown(interface_text("### 1. 장소와 지난 활동 표현을 확인하고 소리 내어 읽어 보세요.", "### 1. Review the Korean places and past-activity expressions, then read them aloud"))
    render_unit6_reference(
        "unit9-weekend-activities.png",
        "unit9-vocab-guide",
        "<p><b>장소:</b> 공원, 도서관, 식당, 집, 헬스장, 백화점</p>"
        "<p><b>지난 활동:</b> 산책했어요, 공부했어요, 밥을 먹었어요, 영화를 봤어요, 운동했어요, 쇼핑했어요</p>"
        "<p><b>시간:</b> 어제, 주말</p>",
    )
    for start in range(0, len(vocabulary_words), 5):
        columns = st.columns(min(5, len(vocabulary_words) - start))
        for column, index in zip(columns, range(start, min(start + 5, len(vocabulary_words)))):
            with column:
                st.button(vocabulary_words[index], key=f"vocab_select_9_{index}", type="primary" if index == active_index else "secondary", on_click=select_vocabulary, args=(9, index), width="stretch")
    render_learning_info(f"**예문:** :green[**{example_sentence}**]", icon=":material/menu_book:")
    st.markdown(interface_text("### 2. 그림을 보고 장소에 알맞은 지난 활동을 선택해 보세요.", "### 2. Look at each picture and choose the matching Korean past activity"))
    visual_places = [
        ("🌳", "공원", "산책하는 곳"),
        ("📚", "도서관", "책을 읽고 공부하는 곳"),
        ("🍚", "식당", "밥을 먹는 곳"),
        ("🛍️", "백화점", "물건을 사는 곳"),
    ]
    visual_columns = st.columns(4, gap="medium")
    for column, (symbol, place_name, cue) in zip(visual_columns, visual_places):
        with column:
            with st.container(border=True, height=150):
                st.markdown(
                    f"<div style='text-align:center;font-size:2.8rem;line-height:1.15'>{symbol}</div>"
                    f"<div style='text-align:center;font-size:1.05rem;font-weight:800;margin-top:8px'>{place_name}</div>"
                    f"<div style='text-align:center;color:#aebbd0;font-size:.86rem;margin-top:5px'>{cue}</div>",
                    unsafe_allow_html=True,
                )
    st.caption(interface_text("위의 장소 그림을 확인한 뒤, 각 장소에서 한 활동을 선택하세요.", "Check the place pictures, then choose the activity done at each place."))
    second = render_choice_set(9, "vocab_place_action", [
        ("공원", ["산책했어요", "공부했어요", "쇼핑했어요"], "산책했어요", "공원에서 산책했어요."),
        ("도서관", ["운동했어요", "공부했어요", "밥을 먹었어요"], "공부했어요", "도서관에서 공부했어요."),
        ("식당", ["밥을 먹었어요", "영화를 봤어요", "산책했어요"], "밥을 먹었어요", "식당에서 밥을 먹었어요."),
        ("백화점", ["쇼핑했어요", "공부했어요", "운동했어요"], "쇼핑했어요", "백화점에서 쇼핑했어요."),
    ])
    st.markdown(interface_text("### 3. 예를 읽고 빈칸에 알맞은 표현을 선택해 대화를 완성해 보세요.", "### 3. Read the example and complete each Korean past-tense dialogue"))
    render_learning_info(
        "**예**\n\n가: 어제 어디에서 뭐 했어요?\n\n"
        "나: 도서관:orange[**에서**] 공부:orange[**했어요**].",
        icon=":material/chat:",
    )
    dialogue_steps = [
        ("🕒", "시간", "어제·주말"),
        ("📍", "장소", "어디에서"),
        ("✅", "지난 활동", "무엇을 했어요"),
    ]
    step_columns = st.columns(3, gap="medium")
    for column, (symbol, label, phrase) in zip(step_columns, dialogue_steps):
        with column:
            with st.container(border=True, height=112):
                st.markdown(
                    f"<div style='display:flex;align-items:center;justify-content:center;gap:9px;font-size:1.7rem'>{symbol}"
                    f"<b style='font-size:1rem'>{label}</b></div>"
                    f"<div style='text-align:center;color:#aebbd0;margin-top:10px'>{phrase}</div>",
                    unsafe_allow_html=True,
                )
    st.caption(interface_text("시간 표현을 확인하고, 장소에는 ‘에서’, 끝난 활동에는 과거 표현을 사용하세요.", "Use 에서 for the activity location and past-tense forms for completed actions."))
    third = render_choice_set(9, "vocab_past_dialogue", [
        ("가: 어제 뭐 했어요?\n나: 공원에서 ___.", ["산책했어요", "산책해요", "산책할까요"], "산책했어요", "어제 한 일이므로 ‘산책했어요’가 알맞아요."),
        ("가: 주말에 어디에서 공부했어요?\n나: ___에서 공부했어요.", ["도서관", "식당", "백화점"], "도서관", "공부하는 장소는 도서관이 자연스러워요."),
        ("가: 어제 집에서 뭐 했어요?\n나: 영화를 ___.", ["봤어요", "봐요", "봐았어요"], "봤어요", "‘보다’의 과거형은 ‘봤어요’예요."),
    ], dialogue_layout=True)
    return second and third


def render_unit9_grammar1():
    render_learning_info(interface_text("행동이 일어난 장소 뒤에 ‘에서’를 붙여요.", "Add 에서 after the place where an activity happened."), icon=":material/school:")
    render_unit6_reference(
        "unit9-weekend-activities.png", "unit9-grammar1-guide",
        "<p><b>가:</b> 어제 어디에서 산책했어요?</p><p><b>나:</b> 공원<b style='color:#78aef8'>에서</b> 산책했어요.</p>"
        "<p><b>가:</b> 어디에서 공부했어요?</p><p><b>나:</b> 도서관<b style='color:#78aef8'>에서</b> 공부했어요.</p>"
        "<p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>장소 + 에서 + 행동</b></p>",
    )
    st.markdown(interface_text("### 1. ‘에서’가 쓰인 예를 읽고 형태를 확인해 보세요.", "### 1. Read the examples and notice how 에서 is used"))
    visual_examples = [
        ("🌳", "공원", "🚶", "산책했어요"),
        ("📚", "도서관", "✏️", "공부했어요"),
    ]
    example_columns = st.columns(2, gap="medium")
    for column, (place_symbol, place, action_symbol, action) in zip(example_columns, visual_examples):
        with column:
            with st.container(border=True, height=158):
                st.markdown(
                    "<div style='display:flex;align-items:center;justify-content:center;gap:10px;margin-top:4px'>"
                    f"<div style='text-align:center'><div style='font-size:2rem'>{place_symbol}</div><b>{place}</b></div>"
                    "<div style='font-size:1.25rem;color:#8fa4c2'>+</div>"
                    "<div style='padding:7px 12px;border-radius:10px;background:#173f65;color:#78aef8;font-weight:850'>에서</div>"
                    "<div style='font-size:1.25rem;color:#8fa4c2'>+</div>"
                    f"<div style='text-align:center'><div style='font-size:2rem'>{action_symbol}</div><b>{action}</b></div>"
                    "</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='text-align:center;margin-top:13px;font-weight:800'>{place}<span style='color:#78aef8'>에서</span> {action}</div>",
                    unsafe_allow_html=True,
                )
    render_learning_info("**예**\n\n가: 어제 어디에서 산책했어요?\n\n나: 공원:orange[**에서**] 산책했어요.", icon=":material/chat:")

    st.markdown(interface_text("### 2. 장소와 활동을 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a place and activity, then read the Korean dialogue aloud"))
    place_symbols = {"공원": "🌳", "도서관": "📚", "식당": "🍚", "집": "🏠", "헬스장": "🏋️", "백화점": "🛍️"}
    action_symbols = {"산책했어요": "🚶", "운동했어요": "🏃", "공부했어요": "✏️", "책을 읽었어요": "📖", "밥을 먹었어요": "🥄", "영화를 봤어요": "🎬", "쉬었어요": "☕", "쇼핑했어요": "🛒"}
    choice_column, preview_column = st.columns([1, 1.45], gap="medium", vertical_alignment="center")
    with choice_column:
        place = st.selectbox("① 장소를 선택하세요", list(UNIT9_PLACE_ACTIONS), key="unit9_g1_place")
        action = st.selectbox("② 활동을 선택하세요", UNIT9_PLACE_ACTIONS[place], key="unit9_g1_action")
    with preview_column:
        with st.container(border=True, height=184):
            st.markdown("<div style='text-align:center;color:#aebbd0;font-size:.86rem;font-weight:750'>나의 장면</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='display:flex;align-items:center;justify-content:center;gap:22px;margin-top:12px'>"
                f"<div style='text-align:center'><div style='font-size:2.6rem'>{place_symbols[place]}</div><b>{place}</b></div>"
                "<div style='font-size:1.7rem;color:#78aef8'>→</div>"
                f"<div style='text-align:center'><div style='font-size:2.6rem'>{action_symbols[action]}</div><b>{action}</b></div>"
                "</div>"
                f"<div style='text-align:center;margin-top:14px;font-size:1.05rem;font-weight:850'>{place}<span style='color:#78aef8'>에서</span> {action}</div>",
                unsafe_allow_html=True,
            )
    render_learning_info(f"가: 어제 어디에서 무엇을 했어요?\n\n나: {place}:orange[**에서**] {action}", icon=":material/record_voice_over:")
    st.caption(interface_text("① 질문을 읽어요.  ② ‘장소 + 에서’를 말해요.  ③ 선택한 활동으로 대답을 마쳐요.", "1. Read the question. 2. Say 장소 + 에서. 3. Finish the answer with the selected activity."))
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit9_g1_speaking", type="primary"):
        st.session_state["unit9_g1_speaking_done"] = True
        st.session_state["unit9_g1_checked_choice"] = (place, action)
    speaking_done = st.session_state.get("unit9_g1_speaking_done", False) and st.session_state.get("unit9_g1_checked_choice") == (place, action)
    if not speaking_done:
        st.session_state.pop("unit9_g1_speaking_done", None)
    if speaking_done:
        st.session_state["unit9_grammar1_activities_completed"] = True
    else:
        st.session_state.pop("unit9_grammar1_activities_completed", None)


def render_unit9_grammar2():
    render_learning_info(interface_text("어제나 지난 주말에 한 일을 말할 때 동사를 과거형으로 바꿔요.", "Change the verb to the past tense when talking about yesterday or last weekend."), icon=":material/school:")
    render_unit6_reference(
        "unit9-weekend-activities.png", "unit9-grammar2-guide",
        "<p><b>산책해요</b> → <b style='color:#78aef8'>산책했어요</b></p><p><b>공부해요</b> → <b style='color:#78aef8'>공부했어요</b></p>"
        "<p><b>먹어요</b> → <b style='color:#78aef8'>먹었어요</b></p><p><b>봐요</b> → <b style='color:#78aef8'>봤어요</b></p>"
        "<p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>동사 + -았어요/-었어요</b></p>",
    )
    st.markdown(interface_text("### 1. 빈칸에 알맞은 과거 표현을 선택해 문장을 완성해 보세요.", "### 1. Complete each Korean sentence with the correct past-tense form"))
    rule_cards = [
        ("하다", "했어요", "공부하다 → 공부했어요", "#78aef8"),
        ("ㅏ · ㅗ", "았어요", "보다 → 봤어요", "#f2a65a"),
        ("그 밖의 모음", "었어요", "먹다 → 먹었어요", "#74c69d"),
    ]
    rule_columns = st.columns(3, gap="medium")
    for column, (condition, ending, example, color) in zip(rule_columns, rule_cards):
        with column:
            with st.container(border=True, height=144):
                st.markdown(
                    f"<div style='text-align:center;color:#aebbd0;font-size:.83rem'>동사의 변화</div>"
                    f"<div style='display:flex;align-items:center;justify-content:center;gap:8px;margin-top:10px'>"
                    f"<b>{condition}</b><span style='color:#8fa4c2'>→</span>"
                    f"<b style='color:{color};font-size:1.1rem'>{ending}</b></div>"
                    f"<div style='text-align:center;margin-top:13px;font-weight:750'>{example}</div>",
                    unsafe_allow_html=True,
                )
    render_learning_info(
        "**예**  오늘 공원에서 산책해요. → 어제 공원에서 :orange[**산책했어요**].",
        icon=":material/timeline:",
    )
    first = render_choice_set(9, "grammar2_past", [
        ("어제 공원에서 ___.", ["산책했어요", "산책해요", "산책할까요"], "산책했어요", "어제 한 일이므로 과거형을 사용해요."),
        ("도서관에서 ___.", ["공부했어요", "공부해요", "공부하어요"], "공부했어요", "‘공부해요’의 과거형은 ‘공부했어요’예요."),
        ("식당에서 밥을 ___.", ["먹었어요", "먹어요", "먹았어요"], "먹었어요", "‘먹다’의 과거형은 ‘먹었어요’예요."),
        ("집에서 영화를 ___.", ["봤어요", "봐요", "봐았어요"], "봤어요", "‘보다’의 과거형은 ‘봤어요’예요."),
    ])
    st.markdown(interface_text("### 2. 현재 표현을 선택해 과거 문장으로 바꾸고 소리 내어 읽어 보세요.", "### 2. Choose a present expression, change it to the past, and read it aloud"))
    past_forms = {"산책해요": "산책했어요", "공부해요": "공부했어요", "밥을 먹어요": "밥을 먹었어요", "영화를 봐요": "영화를 봤어요", "운동해요": "운동했어요", "쇼핑해요": "쇼핑했어요"}
    activity_symbols = {"산책해요": "🚶", "공부해요": "✏️", "밥을 먹어요": "🥄", "영화를 봐요": "🎬", "운동해요": "🏃", "쇼핑해요": "🛒"}
    selector_column, timeline_column = st.columns([1, 1.6], gap="medium", vertical_alignment="center")
    with selector_column:
        present = st.selectbox(interface_text("바꿀 현재 표현", "Present expression"), list(past_forms), key="unit9_g2_present")
        st.caption(interface_text("표현을 바꾸어 보며 현재형과 과거형을 비교하세요.", "Compare the present and past forms as you change the expression."))
    with timeline_column:
        with st.container(border=True, height=168):
            st.markdown(
                f"<div style='display:flex;align-items:center;justify-content:center;gap:18px;margin-top:10px'>"
                f"<div style='text-align:center'><div style='font-size:2.3rem'>{activity_symbols[present]}</div>"
                f"<div style='color:#aebbd0;font-size:.82rem'>오늘</div><b>{present}</b></div>"
                "<div style='text-align:center'><div style='font-size:1.8rem;color:#78aef8'>→</div>"
                "<div style='font-size:.78rem;color:#aebbd0'>시간이 지났어요</div></div>"
                f"<div style='text-align:center'><div style='font-size:2.3rem'>✅</div>"
                f"<div style='color:#aebbd0;font-size:.82rem'>어제</div><b style='color:#f2a65a'>{past_forms[present]}</b></div>"
                "</div>",
                unsafe_allow_html=True,
            )
    render_learning_info(f"어제 :orange[**{past_forms[present]}**]", icon=":material/history:")
    st.caption(interface_text("‘오늘’ 문장과 ‘어제’ 문장을 번갈아 읽으며 달라진 부분을 확인하세요.", "Read the 오늘 and 어제 sentences in turn and notice what changes."))
    if st.button(interface_text("2번 문장을 두 번 읽었어요", "I read the Activity 2 sentences aloud twice"), key="unit9_g2_speaking", type="primary"):
        st.session_state["unit9_g2_speaking_done"] = True
        st.session_state["unit9_g2_checked_present"] = present
    speaking_done = st.session_state.get("unit9_g2_speaking_done", False) and st.session_state.get("unit9_g2_checked_present") == present
    if not speaking_done:
        st.session_state.pop("unit9_g2_speaking_done", None)
    if first and speaking_done:
        st.session_state["grammar2_done_9"] = True
    else:
        st.session_state.pop("grammar2_done_9", None)


def render_unit9_activity1():
    st.subheader(interface_text("활동 1 · 어제 한 일", "Activity 1 · Yesterday’s Activities"))
    st.markdown(interface_text("### 1. 예시 대화를 읽고 알맞은 답을 선택해 보세요.", "### 1. Read the Korean example dialogue and choose the answers"))
    render_unit6_reference(
        "unit9-weekend-activities.png", "unit9-activity1-guide",
        "<p><b>민호:</b> 어제 뭐 했어요?</p><p><b>지은:</b> 공원에서 산책했어요. 민호 씨는요?</p>"
        "<p><b>민호:</b> 도서관에서 공부했어요. 지은 씨는 그다음에 뭐 했어요?</p>"
        "<p><b>지은:</b> 집에서 영화를 봤어요.</p><p><b>민호:</b> 영화가 재미있었어요?</p>"
        "<p><b>지은:</b> 네, 아주 재미있었어요.</p>",
    )
    person_columns = st.columns(3, gap="medium")
    dialogue_clues = [
        ("👩", "지은", "🌳 공원", "🚶 산책했어요"),
        ("👨", "민호", "📚 도서관", "✏️ 공부했어요"),
        ("👩", "지은 · 그다음", "🏠 집", "🎬 영화를 봤어요"),
    ]
    for column, (person_symbol, name, place_clue, action_clue) in zip(person_columns, dialogue_clues):
        with column:
            with st.container(border=True, height=126):
                st.markdown(
                    f"<div style='display:flex;align-items:center;justify-content:center;gap:12px'>"
                    f"<span style='font-size:2.3rem'>{person_symbol}</span><b style='font-size:1.05rem'>{name}</b></div>"
                    f"<div style='display:flex;justify-content:center;gap:18px;margin-top:12px'>"
                    f"<span>{place_clue}</span><span style='color:#78aef8'>→</span><b>{action_clue}</b></div>",
                    unsafe_allow_html=True,
                )
    st.caption(interface_text("사람마다 ‘어디에서’와 ‘무엇을 했는지’를 짝지어 보세요.", "Match each person with where they were and what they did."))
    first = render_choice_set(9, "activity1_reading", [
        ("지은 씨는 어디에서 산책했어요?", ["공원", "도서관", "식당"], "공원", "지은 씨는 공원에서 산책했어요."),
        ("민호 씨는 도서관에서 무엇을 했어요?", ["공부했어요", "산책했어요", "쇼핑했어요"], "공부했어요", "민호 씨는 도서관에서 공부했어요."),
        ("지은 씨는 그다음에 어디에 갔어요?", ["집", "도서관", "백화점"], "집", "지은 씨는 그다음에 집에 갔어요."),
        ("지은 씨는 집에서 무엇을 했어요?", ["영화를 봤어요", "공부했어요", "운동했어요"], "영화를 봤어요", "지은 씨는 집에서 영화를 봤어요."),
    ])
    st.markdown(interface_text("### 2. 장소와 활동을 선택해 나의 대화를 완성해 보세요.", "### 2. Choose a place and activity to complete your Korean dialogue"))
    place_symbols = {"공원": "🌳", "도서관": "📚", "식당": "🍚", "집": "🏠", "헬스장": "🏋️", "백화점": "🛍️"}
    action_symbols = {"산책했어요": "🚶", "운동했어요": "🏃", "공부했어요": "✏️", "책을 읽었어요": "📖", "밥을 먹었어요": "🥄", "영화를 봤어요": "🎬", "쉬었어요": "☕", "쇼핑했어요": "🛒"}
    choice_column, memory_column = st.columns([1, 1.45], gap="medium", vertical_alignment="center")
    with choice_column:
        place = st.selectbox("① 내가 간 장소", list(UNIT9_PLACE_ACTIONS), key="unit9_a1_place")
        action = st.selectbox("② 내가 한 활동", UNIT9_PLACE_ACTIONS[place], key="unit9_a1_action")
    with memory_column:
        with st.container(border=True, height=184):
            st.markdown("<div style='text-align:center;color:#aebbd0;font-size:.85rem;font-weight:750'>나의 어제 카드</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div style='display:flex;align-items:center;justify-content:center;gap:22px;margin-top:13px'>"
                f"<div style='text-align:center'><div style='font-size:2.7rem'>{place_symbols[place]}</div><b>{place}</b></div>"
                "<div style='font-size:1.7rem;color:#78aef8'>+</div>"
                f"<div style='text-align:center'><div style='font-size:2.7rem'>{action_symbols[action]}</div><b>{action}</b></div></div>"
                f"<div style='text-align:center;margin-top:14px;font-weight:850'>어제 {place}<span style='color:#78aef8'>에서</span> {action}</div>",
                unsafe_allow_html=True,
            )
    render_learning_info(
        f"가: 어제 어디:orange[**에**] 갔어요?\n\n"
        f"나: {place}:orange[**에**] 갔어요.\n\n"
        f"가: {place}:orange[**에서**] 뭐 했어요?\n\n"
        f"나: {action}",
        icon=":material/forum:",
    )
    st.caption(interface_text("‘에’는 간 장소, ‘에서’는 활동한 장소 뒤에 붙어요.", "Use 에 for the destination and 에서 for the place where the activity happened."))
    st.caption(interface_text("카드를 보지 않고 한 번, 카드를 보며 한 번 대답해 보세요.", "Answer once without the card, then once while looking at it."))
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"), key="unit9_a1_speaking", type="primary"):
        st.session_state["unit9_a1_speaking_done"] = True
        st.session_state["unit9_a1_checked_choice"] = (place, action)
    speaking_done = st.session_state.get("unit9_a1_speaking_done", False) and st.session_state.get("unit9_a1_checked_choice") == (place, action)
    if not speaking_done:
        st.session_state.pop("unit9_a1_speaking_done", None)
    if first and speaking_done:
        st.session_state["activity1_completed_9"] = True
    else:
        st.session_state.pop("activity1_completed_9", None)


def render_unit9_activity2():
    st.subheader(interface_text("활동 2 · 나의 주말 기록", "Activity 2 · My Weekend Record"))
    st.markdown(interface_text("### 1. 다음 예를 읽고 네 문장의 순서를 확인해 보세요.", "### 1. Read the example and notice the four-sentence order"))
    render_unit6_reference(
        "unit9-weekend-activities.png", "unit9-activity2-guide",
        "<p><b>예:</b> 어제는 토요일이었어요.</p><p>오전에는 공원에서 산책했어요.</p>"
        "<p>오후에는 집에서 영화를 봤어요.</p><p>정말 즐거웠어요.</p>",
    )
    record_steps = [("1", "언제?", "요일"), ("2", "오전에는?", "첫 번째 활동"), ("3", "오후에는?", "두 번째 활동"), ("4", "어땠어요?", "나의 느낌")]
    record_columns = st.columns(4, gap="medium")
    for column, (number, question, answer_type) in zip(record_columns, record_steps):
        with column:
            with st.container(border=True, height=112):
                st.markdown(
                    f"<div style='display:flex;align-items:center;justify-content:center;gap:9px'>"
                    f"<span style='display:inline-flex;width:25px;height:25px;border-radius:50%;background:#173f65;color:#78aef8;align-items:center;justify-content:center;font-weight:850'>{number}</span>"
                    f"<b>{question}</b></div><div style='text-align:center;color:#aebbd0;margin-top:12px'>{answer_type}</div>",
                    unsafe_allow_html=True,
                )
    first = render_choice_set(9, "activity2_order", [
        ("첫 문장에는 무엇을 써요?", ["요일", "장소", "사람 이름"], "요일", "먼저 언제 있었던 일인지 써요."),
        ("둘째와 셋째 문장에는 무엇을 써요?", ["장소와 활동", "전화번호", "나라와 직업"], "장소와 활동", "장소에서 한 일을 차례로 써요."),
        ("마지막 문장에는 무엇을 써요?", ["나의 느낌", "전화번호", "오늘 날짜"], "나의 느낌", "마지막에는 주말이 어땠는지 느낌을 써요."),
    ])
    st.markdown(interface_text("### 2. 요일·시간·활동·느낌을 선택해 주말 기록을 완성해 보세요.", "### 2. Choose a weekday, times, activities, and feeling to complete your Korean weekend record"))
    day = st.selectbox(interface_text("요일", "Weekday"), ["토요일", "일요일"], key="unit9_a2_day")
    row1, row2 = st.columns(2, gap="medium")
    with row1:
        time1 = st.selectbox(interface_text("첫 번째 시간", "First time"), ["오전", "오후", "저녁"], key="unit9_a2_time1")
        place1 = st.selectbox(interface_text("첫 번째 장소", "First place"), list(UNIT9_PLACE_ACTIONS), key="unit9_a2_place1")
        action1 = st.selectbox(interface_text("첫 번째 활동", "First activity"), UNIT9_PLACE_ACTIONS[place1], key="unit9_a2_action1")
    with row2:
        time2 = st.selectbox(interface_text("두 번째 시간", "Second time"), ["오후", "저녁", "오전"], key="unit9_a2_time2")
        place2 = st.selectbox(interface_text("두 번째 장소", "Second place"), list(UNIT9_PLACE_ACTIONS), index=3, key="unit9_a2_place2")
        action2 = st.selectbox(interface_text("두 번째 활동", "Second activity"), UNIT9_PLACE_ACTIONS[place2], key="unit9_a2_action2")
    feeling = st.selectbox(interface_text("마지막 느낌", "Final feeling"), ["정말 즐거웠어요", "아주 재미있었어요", "조금 피곤했어요", "기분이 좋았어요"], key="unit9_a2_feeling")
    place_symbols = {"공원": "🌳", "도서관": "📚", "식당": "🍚", "집": "🏠", "헬스장": "🏋️", "백화점": "🛍️"}
    action_symbols = {"산책했어요": "🚶", "운동했어요": "🏃", "공부했어요": "✏️", "책을 읽었어요": "📖", "밥을 먹었어요": "🥄", "영화를 봤어요": "🎬", "쉬었어요": "☕", "쇼핑했어요": "🛒"}
    st.markdown("#### 나의 주말 타임라인")
    timeline_columns = st.columns([1, 0.18, 1, 0.18, 1], gap="small", vertical_alignment="center")
    timeline_items = [
        (timeline_columns[0], "📅", day, "어제"),
        (timeline_columns[2], place_symbols[place1], f"{time1} · {place1}", action_symbols[action1] + " " + action1),
        (timeline_columns[4], place_symbols[place2], f"{time2} · {place2}", action_symbols[action2] + " " + action2),
    ]
    for column, symbol, title, detail in timeline_items:
        with column:
            with st.container(border=True, height=146):
                st.markdown(
                    f"<div style='text-align:center;font-size:2.35rem'>{symbol}</div>"
                    f"<div style='text-align:center;font-weight:850;margin-top:5px'>{title}</div>"
                    f"<div style='text-align:center;color:#aebbd0;font-size:.86rem;margin-top:7px'>{detail}</div>",
                    unsafe_allow_html=True,
                )
    for arrow_column in (timeline_columns[1], timeline_columns[3]):
        with arrow_column:
            st.markdown("<div style='text-align:center;color:#78aef8;font-size:1.7rem;font-weight:850'>→</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='display:flex;align-items:center;justify-content:center;gap:12px;padding:12px 16px;border:1px solid #31445f;border-radius:12px;margin-top:10px'>"
        f"<span style='font-size:1.8rem'>💭</span><span style='color:#aebbd0'>주말의 느낌:</span>"
        f"<b style='color:#f2a65a'>{feeling}</b></div>",
        unsafe_allow_html=True,
    )
    response = f"어제는 {day}이었어요.\n{time1}에는 {place1}에서 {action1}.\n{time2}에는 {place2}에서 {action2}.\n{feeling}."
    render_learning_success(response, icon=":material/edit_note:")
    if place1 == place2 or time1 == time2:
        render_learning_warning(interface_text("서로 다른 시간과 장소를 선택하면 시간의 흐름이 잘 보이는 기록을 완성할 수 있어요.", "Choose different times and places so the sequence is clear."), icon=":material/lightbulb:")
        return ""
    st.caption(interface_text("타임라인을 왼쪽에서 오른쪽으로 보고, 마지막 느낌까지 네 문장을 이어서 읽어 보세요.", "Read the four Korean sentences from left to right, including the final feeling."))
    response_read_aloud = st.checkbox(
        interface_text("완성한 주말 기록을 소리 내어 읽었어요", "I read my completed Korean weekend record aloud"),
        key="unit9_a2_response_read_aloud",
        disabled=not first,
        on_change=set_session_state_value,
        args=("unit9_a2_checked_response", response),
    )
    return response if first and response_read_aloud and st.session_state.get("unit9_a2_checked_response") == response else ""


UNIT10_DESTINATION_PURPOSES = {
    "놀이공원": ["놀이기구를 타러 가요"], "영화관": ["영화를 보러 가요"],
    "식당": ["밥을 먹으러 가요"], "공원": ["산책하러 가요", "운동하러 가요"],
    "카페": ["커피를 마시러 가요", "친구를 만나러 가요"], "도서관": ["공부하러 가요", "책을 읽으러 가요"],
}


def render_unit10_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    st.markdown(interface_text("### 1. 주말 약속에 사용하는 장소와 표현을 확인하고 소리 내어 읽어 보세요.", "### 1. Review places and expressions for weekend plans, then read them aloud."))
    render_unit6_reference(
        "unit10-weekend-plans.png", "unit10-vocab-guide",
        "<p><b>장소:</b> 놀이공원, 영화관, 식당, 공원, 카페, 도서관</p>"
        "<p><b>제안:</b> 같이 갈까요? 토요일에 만날까요?</p>"
        "<p><b>목적:</b> 영화를 보러 가요, 밥을 먹으러 가요, 산책하러 가요, 공부하러 가요</p>",
        guide_width=760,
    )
    for start in range(0, len(vocabulary_words), 5):
        columns = st.columns(min(5, len(vocabulary_words) - start))
        for column, index in zip(columns, range(start, min(start + 5, len(vocabulary_words)))):
            with column:
                st.button(vocabulary_words[index], key=f"vocab_select_10_{index}", type="primary" if index == active_index else "secondary", on_click=select_vocabulary, args=(10, index), width="stretch")
    render_learning_info(f"**예문:** :green[**{example_sentence}**]", icon=":material/menu_book:")
    st.markdown(interface_text("### 2. 장소 그림에 알맞은 목적을 선택해 보세요.", "### 2. Look at the place pictures and choose the correct purpose."))
    cards = [("🎬", "영화관"), ("🍚", "식당"), ("🌳", "공원"), ("📚", "도서관")]
    columns = st.columns(4, gap="medium")
    for column, (symbol, label) in zip(columns, cards):
        with column:
            with st.container(border=True, height=112):
                st.markdown(f"<div style='text-align:center;font-size:2.5rem'>{symbol}</div><div style='text-align:center;font-weight:850;margin-top:7px'>{label}</div>", unsafe_allow_html=True)
    second = render_choice_set(10, "vocab_purpose", [
        ("영화관", ["영화를 보러 가요", "밥을 먹으러 가요", "공부하러 가요"], "영화를 보러 가요", "영화관에는 영화를 보러 가요."),
        ("식당", ["밥을 먹으러 가요", "산책하러 가요", "영화를 보러 가요"], "밥을 먹으러 가요", "식당에는 밥을 먹으러 가요."),
        ("공원", ["산책하러 가요", "공부하러 가요", "쇼핑하러 가요"], "산책하러 가요", "공원에는 산책하러 가요."),
        ("도서관", ["공부하러 가요", "커피를 마시러 가요", "밥을 먹으러 가요"], "공부하러 가요", "도서관에는 공부하러 가요."),
    ])
    st.markdown(interface_text("### 3. 예를 읽고 주말 약속 대화를 완성해 보세요.", "### 3. Read the example and complete the weekend-plan dialogues."))
    render_learning_info("**예**\n\n가: 주말에 같이 영화를 :orange[**볼까요?**]\n\n나: 좋아요. 영화관에 영화를 :orange[**보러 가요.**]", icon=":material/chat:")
    third = render_choice_set(10, "vocab_plan_dialogue", [
        ("가: 토요일에 같이 놀이공원에 ___?", ["갈까요", "갔어요", "가러 가요"], "갈까요", "함께 가자고 제안하므로 ‘갈까요?’가 알맞아요."),
        ("가: 영화관에 왜 가요?\n나: 영화를 ___ 가요.", ["보러", "볼까요", "봤어요"], "보러", "영화를 보는 목적이므로 ‘보러 가요’를 사용해요."),
        ("가: 몇 시에 만날까요?\n나: 두 시에 ___.", ["만나요", "먹으러", "봤어요"], "만나요", "약속 시간을 정할 때 ‘두 시에 만나요’라고 대답해요."),
    ], dialogue_layout=True)
    return second and third


def render_unit10_grammar1():
    render_learning_info(interface_text("상대방에게 함께할 일을 제안할 때 ‘-(으)ㄹ까요?’를 사용해요.", "Use ‘-(으)ㄹ까요?’ to suggest doing something together."), icon=":material/school:")
    render_unit6_reference(
        "unit10-weekend-plans.png", "unit10-grammar1-guide",
        "<p><b>가:</b> 주말에 같이 놀이공원에 <b style='color:#78aef8'>갈까요?</b></p><p><b>나:</b> 네, 좋아요.</p>"
        "<p><b>가:</b> 같이 점심을 <b style='color:#78aef8'>먹을까요?</b></p><p><b>나:</b> 네, 같이 먹어요.</p>"
        "<p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>받침 없음 + ㄹ까요? / 받침 있음 + 을까요?</b></p>",
    )
    st.markdown(interface_text("### 1. 동사의 받침을 보고 알맞은 제안 표현을 확인해 보세요.", "### 1. Check the suggestion form based on the verb ending."))
    examples = [("🎡", "가다", "받침 없음", "갈까요?"), ("🍚", "먹다", "받침 있음", "먹을까요?")]
    columns = st.columns(2, gap="medium")
    for column, (symbol, verb, rule, result) in zip(columns, examples):
        with column:
            with st.container(border=True, height=154):
                st.markdown(f"<div style='text-align:center;font-size:2.1rem'>{symbol}</div><div style='text-align:center;margin-top:6px'><b>{verb}</b> <span style='color:#8fa4c2'>· {rule}</span></div><div style='text-align:center;color:#78aef8;font-size:1.15rem;font-weight:850;margin-top:10px'>→ {result}</div>", unsafe_allow_html=True)
    first = render_choice_set(10, "grammar1_suggestion", [
        ("우리 같이 공원에 ___?", ["갈까요", "가을까요", "갔어요"], "갈까요", "‘가다’는 받침이 없어서 ‘갈까요?’가 돼요."),
        ("같이 점심을 ___?", ["먹을까요", "먹ㄹ까요", "먹었어요"], "먹을까요", "‘먹다’는 받침이 있어서 ‘먹을까요?’가 돼요."),
        ("주말에 영화를 ___?", ["볼까요", "보을까요", "봤어요"], "볼까요", "‘보다’는 받침이 없어서 ‘볼까요?’가 돼요."),
    ])
    st.markdown(interface_text("### 2. 요일과 활동을 선택해 나의 제안 카드를 만들어 보세요.", "### 2. Choose a day and activity to make your own suggestion card."))
    suggestion_forms = {"놀이공원에 가다": "놀이공원에 갈까요?", "영화를 보다": "영화를 볼까요?", "점심을 먹다": "점심을 먹을까요?", "공원에서 산책하다": "공원에서 산책할까요?"}
    left, right = st.columns([1, 1.45], gap="medium", vertical_alignment="center")
    with left:
        day = st.selectbox("① 약속 요일", ["토요일", "일요일"], key="unit10_g1_day")
        base = st.selectbox("② 함께할 일", list(suggestion_forms), key="unit10_g1_base")
    with right:
        with st.container(border=True, height=174):
            st.markdown("<div style='text-align:center;font-size:2.4rem'>💌</div><div style='text-align:center;color:#aebbd0;margin-top:5px'>나의 주말 제안</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center;font-size:1.12rem;font-weight:850;margin-top:14px'>{day}에 같이 <span style='color:#f2a65a'>{suggestion_forms[base]}</span></div>", unsafe_allow_html=True)
    render_learning_info(f"가: {day}에 같이 :orange[**{suggestion_forms[base]}**]\n\n나: 네, 좋아요!", icon=":material/forum:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read dialogue 2 aloud twice"), key="unit10_g1_speaking", type="primary"):
        st.session_state["unit10_g1_speaking_done"] = True
    if first and st.session_state.get("unit10_g1_speaking_done", False):
        st.session_state["unit10_grammar1_activities_completed"] = True


def render_unit10_grammar2():
    render_learning_info(interface_text("어디에 가는 목적을 말할 때 ‘-(으)러 가요’를 사용해요.", "Use ‘-(으)러 가요’ to say where you are going and for what purpose."), icon=":material/school:")
    render_unit6_reference(
        "unit10-weekend-plans.png", "unit10-grammar2-guide",
        "<p><b>가:</b> 영화관에 왜 가요?</p><p><b>나:</b> 영화를 <b style='color:#78aef8'>보러 가요.</b></p>"
        "<p><b>가:</b> 식당에 왜 가요?</p><p><b>나:</b> 밥을 <b style='color:#78aef8'>먹으러 가요.</b></p>"
        "<p style='margin-top:16px'><b>형태:</b> <b style='color:#b7ef58'>목적 + -(으)러 + 가요/와요</b></p>",
    )
    st.markdown(interface_text("### 1. 받침을 확인하고 알맞은 목적 표현을 선택해 보세요.", "### 1. Check the verb ending and choose the correct purpose expression."))
    question_columns = st.columns(2, gap="medium")
    with question_columns[0]:
        with st.container(border=True, height=142):
            st.markdown("<div style='text-align:center;font-size:2rem'>📍</div><div style='text-align:center;font-weight:850'>어디에 가요?</div><div style='text-align:center;color:#78aef8;margin-top:10px'>영화관에 가요.</div>", unsafe_allow_html=True)
    with question_columns[1]:
        with st.container(border=True, height=142):
            st.markdown("<div style='text-align:center;font-size:2rem'>🎯</div><div style='text-align:center;font-weight:850'>왜 가요?</div><div style='text-align:center;color:#f2a65a;margin-top:10px'>영화를 보러 가요.</div>", unsafe_allow_html=True)
    st.caption(interface_text("‘어디’에는 목적지를, ‘왜’에는 하려는 행동을 대답해요.", "Answer with a destination for ‘where’ and an action for ‘why’."))
    rule_columns = st.columns(3, gap="medium")
    rules = [("보다", "받침 없음", "보러 가요"), ("먹다", "받침 있음", "먹으러 가요"), ("공부하다", "하다", "공부하러 가요")]
    for column, (verb, rule, result) in zip(rule_columns, rules):
        with column:
            with st.container(border=True, height=132):
                st.markdown(f"<div style='text-align:center'><b>{verb}</b><div style='color:#aebbd0;font-size:.83rem;margin-top:5px'>{rule}</div><div style='color:#78aef8;font-weight:850;margin-top:13px'>→ {result}</div></div>", unsafe_allow_html=True)
    first = render_choice_set(10, "grammar2_purpose", [
        ("영화관에 영화를 ___ 가요.", ["보러", "보으러", "볼까요"], "보러", "‘보다’는 받침이 없어서 ‘보러 가요’가 돼요."),
        ("식당에 밥을 ___ 가요.", ["먹으러", "먹러", "먹을까요"], "먹으러", "‘먹다’는 받침이 있어서 ‘먹으러 가요’가 돼요."),
        ("도서관에 공부___ 가요.", ["하러", "해으러", "할까요"], "하러", "‘공부하다’는 ‘공부하러 가요’가 돼요."),
    ])
    st.markdown(interface_text("### 2. 목적지를 선택해 이동 목적 문장을 완성해 보세요.", "### 2. Choose a destination and complete the purpose sentence."))
    symbols = {"놀이공원": "🎡", "영화관": "🎬", "식당": "🍚", "공원": "🌳", "카페": "☕", "도서관": "📚"}
    left, right = st.columns([1, 1.5], gap="medium", vertical_alignment="center")
    with left:
        destination = st.selectbox("① 목적지", list(UNIT10_DESTINATION_PURPOSES), key="unit10_g2_destination")
        purpose = st.selectbox("② 목적", UNIT10_DESTINATION_PURPOSES[destination], key="unit10_g2_purpose")
    with right:
        with st.container(border=True, height=174):
            st.markdown(f"<div style='display:flex;align-items:center;justify-content:center;gap:20px;margin-top:15px'><div style='text-align:center'><div style='font-size:2.6rem'>🚶</div><b>출발</b></div><div style='color:#78aef8;font-size:1.8rem'>→</div><div style='text-align:center'><div style='font-size:2.6rem'>{symbols[destination]}</div><b>{destination}</b></div></div><div style='text-align:center;color:#f2a65a;font-weight:850;margin-top:14px'>{purpose}</div>", unsafe_allow_html=True)
    render_learning_info(f"저는 {destination}:orange[**에**] {purpose[:-2]} :orange[**가요.**]", icon=":material/directions_walk:")
    if st.button(interface_text("2번 문장을 두 번 읽었어요", "I read sentence 2 aloud twice"), key="unit10_g2_speaking", type="primary"):
        st.session_state["unit10_g2_speaking_done"] = True
    if first and st.session_state.get("unit10_g2_speaking_done", False):
        st.session_state["grammar2_done_10"] = True


def render_unit10_activity1():
    st.subheader(interface_text("활동 1 · 주말 제안", "Activity 1 · Weekend suggestion"))
    st.markdown(interface_text("### 1. 주말 약속 대화를 읽고 알맞은 답을 선택해 보세요.", "### 1. Read the weekend-plan dialogue and choose the correct answers."))
    render_unit6_reference("unit10-weekend-plans.png", "unit10-activity1-guide", "<p><b>수진:</b> 민호 씨, 토요일에 같이 영화를 볼까요?</p><p><b>민호:</b> 네, 좋아요. 몇 시에 만날까요?</p><p><b>수진:</b> 두 시에 만나요.</p><p><b>민호:</b> 어디에서 만날까요?</p><p><b>수진:</b> 영화관 앞에서 만나요.</p>")
    summary_columns = st.columns(3, gap="medium")
    for column, (symbol, label, value) in zip(summary_columns, [("📅", "언제", "토요일 두 시"), ("🎬", "무엇", "영화를 봐요"), ("📍", "어디", "영화관 앞")]):
        with column:
            with st.container(border=True, height=118):
                st.markdown(f"<div style='text-align:center;font-size:2rem'>{symbol}</div><div style='text-align:center;color:#aebbd0;font-size:.82rem'>{label}</div><div style='text-align:center;font-weight:850;margin-top:5px'>{value}</div>", unsafe_allow_html=True)
    first = render_choice_set(10, "activity1_reading", [
        ("두 사람은 언제 만나요?", ["토요일 두 시", "일요일 두 시", "토요일 세 시"], "토요일 두 시", "토요일 두 시에 만나요."),
        ("두 사람은 무엇을 해요?", ["영화를 봐요", "점심을 먹어요", "공부해요"], "영화를 봐요", "같이 영화를 보기로 했어요."),
        ("두 사람은 어디에서 만나요?", ["영화관 앞", "도서관 앞", "공원"], "영화관 앞", "영화관 앞에서 만나요."),
    ])
    st.markdown(interface_text("### 2. 약속 정보를 선택해 나의 제안 대화를 만들어 보세요.", "### 2. Choose the details to create your own suggestion dialogue."))
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1: day = st.selectbox("요일", ["토요일", "일요일"], key="unit10_a1_day")
    with c2: time = st.selectbox("시간", ["한 시", "두 시", "세 시", "네 시"], key="unit10_a1_time")
    with c3: plan = st.selectbox("함께할 일", ["영화를 볼까요?", "점심을 먹을까요?", "공원에서 산책할까요?", "놀이공원에 갈까요?"], key="unit10_a1_plan")
    reaction = st.radio("제안에 어떻게 대답할까요?", ["네, 좋아요!", "미안해요. 그날은 바빠요."], key="unit10_a1_reaction", horizontal=True)
    with st.container(border=True):
        reaction_symbol = "😊" if reaction == "네, 좋아요!" else "🙏"
        st.markdown(f"<div style='text-align:center;font-size:2.3rem'>{reaction_symbol}</div><div style='text-align:center;color:#aebbd0'>나의 대답</div><div style='text-align:center;color:#f2a65a;font-weight:850;margin-top:8px'>{reaction}</div>", unsafe_allow_html=True)
    if reaction == "네, 좋아요!":
        render_learning_info(f"가: {day}에 같이 :orange[**{plan}**]\n\n나: {reaction} 몇 시에 만날까요?\n\n가: {time}에 만나요.", icon=":material/event_available:")
    else:
        render_learning_info(f"가: {day}에 같이 :orange[**{plan}**]\n\n나: {reaction}\n\n가: 네, 다음에 같이 가요.", icon=":material/event_busy:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read dialogue 2 aloud twice"), key="unit10_a1_speaking", type="primary"):
        st.session_state["unit10_a1_speaking_done"] = True
    if first and st.session_state.get("unit10_a1_speaking_done", False):
        st.session_state["activity1_completed_10"] = True


def render_unit10_activity2():
    st.subheader(interface_text("활동 2 · 나의 주말 약속 카드", "Activity 2 · My weekend-plan card"))
    st.markdown(interface_text("### 1. 약속 카드에 필요한 네 가지 정보를 확인해 보세요.", "### 1. Check the four pieces of information needed for the plan card."))
    steps = [("📅", "언제", "요일"), ("🕒", "몇 시", "시간"), ("📍", "어디", "장소"), ("🎯", "왜", "목적")]
    columns = st.columns(4, gap="medium")
    for column, (symbol, question, answer) in zip(columns, steps):
        with column:
            with st.container(border=True, height=118):
                st.markdown(f"<div style='text-align:center;font-size:2rem'>{symbol}</div><div style='text-align:center;font-weight:850'>{question}</div><div style='text-align:center;color:#aebbd0;font-size:.84rem;margin-top:5px'>{answer}</div>", unsafe_allow_html=True)
    first = render_choice_set(10, "activity2_structure", [("‘왜 가요?’에 알맞은 대답은 무엇이에요?", ["영화를 보러 가요", "토요일에 가요", "두 시에 가요"], "영화를 보러 가요", "‘왜’에는 목적을 대답해요."), ("약속을 제안하는 문장은 무엇이에요?", ["같이 갈까요?", "어제 갔어요.", "날씨가 좋아요."], "같이 갈까요?", "‘-(으)ㄹ까요?’로 함께할 일을 제안해요.")])
    st.markdown(interface_text("### 2. 정보를 선택해 나의 약속 카드를 완성해 보세요.", "### 2. Choose the details to complete your weekend-plan card."))
    left, right = st.columns(2, gap="medium")
    with left:
        day = st.selectbox("요일", ["토요일", "일요일"], key="unit10_a2_day")
        time = st.selectbox("시간", ["한 시", "두 시", "세 시", "네 시", "다섯 시"], key="unit10_a2_time")
    with right:
        place = st.selectbox("장소", list(UNIT10_DESTINATION_PURPOSES), key="unit10_a2_place")
        purpose = st.selectbox("목적", UNIT10_DESTINATION_PURPOSES[place], key="unit10_a2_purpose")
    meeting_point = st.selectbox("만나는 위치", [f"{place} 앞", f"{place} 입구"], key="unit10_a2_meeting_point")
    symbols = {"놀이공원": "🎡", "영화관": "🎬", "식당": "🍚", "공원": "🌳", "카페": "☕", "도서관": "📚"}
    with st.container(border=True):
        st.markdown(f"<div style='text-align:center;font-size:2.7rem'>{symbols[place]}</div><div style='text-align:center;color:#78aef8;font-weight:850'>MY WEEKEND PLAN</div><div style='display:flex;justify-content:center;gap:28px;margin-top:12px'><b>📅 {day}</b><b>🕒 {time}</b><b>📍 {meeting_point}</b></div><div style='text-align:center;color:#f2a65a;font-weight:850;margin-top:14px'>🎯 {purpose}</div>", unsafe_allow_html=True)
    response = f"{day}에 같이 {place}에 갈까요?\n좋아요. {place}에 {purpose}\n{time}에 {meeting_point}에서 만나요."
    render_learning_success(response, icon=":material/calendar_month:")
    st.caption(interface_text("약속 카드를 보고 세 문장을 자연스럽게 이어서 읽어 보세요.", "Read the three Korean sentences on your weekend-plan card aloud."))
    return response if first else ""


def render_unit7_vocabulary_panel(vocabulary_words, active_index, example_sentence):
    st.markdown(interface_text("### 1. 날짜와 요일을 확인하고 소리 내어 읽어 보세요.", "### 1. Review the Korean dates and weekdays, then read them aloud"))
    render_unit6_reference("unit7-calendar.png", "unit7-vocab-guide", "<p><b>요일:</b> 월요일, 화요일, 수요일, 목요일, 금요일, 토요일, 일요일</p><p><b>날짜:</b> 3월 5일, 10월 22일처럼 말해요.</p><p><b>예:</b> 오늘은 수요일이에요.</p>")
    for start in range(0, len(vocabulary_words), 5):
        columns = st.columns(min(5, len(vocabulary_words)-start))
        for col, index in zip(columns, range(start, min(start+5, len(vocabulary_words)))):
            with col:
                st.button(vocabulary_words[index], key=f"vocab_select_7_{index}", type="primary" if index == active_index else "secondary", on_click=select_vocabulary, args=(7,index), width="stretch")
    render_vocabulary_example(example_sentence, color="lime")
    st.divider(); st.markdown(interface_text("### 2. 빈칸에 들어갈 요일을 선택해 보세요.", "### 2. Choose the correct Korean weekday for each blank"))
    st.caption(interface_text("요일 카드를 왼쪽부터 읽으며 오늘과 다음 날의 관계를 확인하세요.", "Read the weekday cards from left to right and identify the following day."))
    weekday_labels = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    weekday_colors = ["#ef6b63", "#ef8d3c", "#e8b43e", "#64a85b", "#4f91d1", "#6574c4", "#9a67b2"]
    weekday_cards = "".join(
        f"<div style='flex:1;min-width:82px;padding:12px 7px;border-radius:10px;background:{color}20;border:1px solid {color};text-align:center;font-weight:850'>{day}</div>"
        for day, color in zip(weekday_labels, weekday_colors)
    )
    st.markdown(f"<div style='display:flex;flex-wrap:wrap;gap:8px;margin:.4rem 0 1rem'>{weekday_cards}</div>", unsafe_allow_html=True)
    q = [("월요일 다음은 ___이에요.",["화요일","수요일","일요일"],"화요일","월요일 다음 날은 화요일이에요."),("금요일 다음은 ___이에요.",["토요일","목요일","월요일"],"토요일","금요일 다음 날은 토요일이에요."),("오늘이 수요일이면 내일은 ___이에요.",["목요일","화요일","금요일"],"목요일","수요일 다음 날은 목요일이에요.")]
    done2 = render_choice_set(7,"vocab_weekday",q, hide_reference_button=True)
    st.divider(); st.markdown(interface_text("### 3. 표에서 생일을 확인하고 빈칸에 알맞은 날짜를 선택해 보세요.", "### 3. Check the table and complete each Korean birthday dialogue"))
    st.caption(interface_text("이름과 생일을 확인한 뒤 알맞은 날짜로 대화를 완성하세요.", "Find each birthday in the table, then complete the dialogue."))
    q3 = [
        ("가: 수지 씨, 생일이 언제예요?\n나: ___이에요.", ["10월 5일", "1월 16일", "6월 22일"], "10월 5일", "표에서 수지의 생일을 찾아요."),
        ("가: 안나 씨, 생일이 언제예요?\n나: ___이에요.", ["10월 5일", "1월 16일", "6월 22일"], "1월 16일", "표에서 안나의 생일을 찾아요."),
        ("가: 재민 씨, 생일이 언제예요?\n나: ___이에요.", ["10월 5일", "1월 16일", "6월 22일"], "6월 22일", "표에서 재민의 생일을 찾아요."),
        ("가: 생일이 언제예요?\n나: ___이에요.", ["10월 5일이에요.", "일곱 시예요.", "수요일이에요."], "10월 5일이에요.", "생일은 월과 일을 함께 말해요."),
    ]
    birthday_picture, birthday_question = st.columns([0.8, 1.2], gap="small", vertical_alignment="top")
    with birthday_picture:
        st.image(Path(__file__).with_name("assets") / "units" / "unit7-birthday.png", width="stretch")
        st.table([
            {"이름": "수지", "생일": "10월 5일"},
            {"이름": "안나", "생일": "1월 16일"},
            {"이름": "재민", "생일": "6월 22일"},
        ])
    with birthday_question:
        dialogue_done = render_choice_set(
            7, "vocab_date_dialogue", q3, dialogue_layout=True, hide_reference_button=True
        )
    return done2 and dialogue_done


def render_unit7_grammar1():
    render_learning_info(interface_text("시간이나 날짜 뒤에 ‘에’를 붙여 언제 하는 일인지 말해요.", "Add 에 after a time or date to say when something happens."), icon=":material/school:")
    render_unit6_reference("unit7-calendar.png","unit7-grammar1-guide","<p><b>형태:</b> <b style='color:#b7ef58'>시간/날짜 + 에</b></p><p>수업은 일곱 시에 시작해요.</p><p>수요일에 한국어 수업이 있어요.</p>")
    st.markdown(interface_text("### 1. 문장을 읽고 빈칸에 알맞은 ‘에’를 선택해 보세요.", "### 1. Read each sentence and choose 에 for the blank"))
    st.caption(interface_text("시간·요일·날짜 뒤에 붙는 조사를 확인하세요. 이 활동은 그림을 보지 않고 문장 형태에 집중해요.", "Focus on the particle used after a time, weekday, or date."))
    qs=[("수업은 일곱 시__ 시작해요.",["에","에서","을"],"에","시간 뒤에는 ‘에’를 사용해요."),("수요일__ 친구를 만나요.",["에","에서","를"],"에","요일 뒤에도 ‘에’를 사용해요."),("3월 5일__ 생일 파티를 해요.",["에","은","을"],"에","날짜 뒤에는 ‘에’를 사용해요."),("금요일__ 영화가 있어요.",["에","를","에서"],"에","요일 뒤에는 ‘에’가 와요.")]
    first=render_choice_set(7,"grammar1_time",qs, hide_reference_button=True)
    st.markdown(interface_text("### 2. 요일과 시간을 선택해 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a weekday and time, then read the Korean dialogue aloud"))
    day=st.selectbox(interface_text("요일", "Weekday"),["월요일","화요일","수요일","목요일","금요일"],key="unit7_g1_day"); hour=st.selectbox(interface_text("시간", "Time"),["아홉 시","열 시","일곱 시"],key="unit7_g1_hour")
    render_learning_info(f"가: 언제 만나요?\n\n나: {day} {hour}에 만나요.",icon=":material/forum:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"),key="unit7_g1_speaking",type="primary"): st.session_state["unit7_g1_speaking_done"]=True; st.session_state["unit7_g1_checked_choice"]=(day,hour)
    speaking_done = st.session_state.get("unit7_g1_speaking_done",False) and st.session_state.get("unit7_g1_checked_choice")== (day,hour)
    if not speaking_done: st.session_state.pop("unit7_g1_speaking_done",None)
    if first and speaking_done: st.session_state["unit7_grammar1_activities_completed"]=True
    else: st.session_state.pop("unit7_grammar1_activities_completed",None)


def render_unit7_grammar2():
    render_learning_info(interface_text("시간을 물을 때 ‘몇 시예요?’라고 말하고, 시와 분을 함께 대답해요.", "Ask 몇 시예요? to find out the time, then answer with the hour and minutes."),icon=":material/school:")
    render_unit6_reference("unit7-time-lunch.png","unit7-grammar2-guide","<p><b>가:</b> 지금 몇 시예요?</p><p><b>나:</b> 일곱 시 삼십 분이에요.</p><p><b>가:</b> 언제 점심을 먹어요?</p><p><b>나:</b> 열두 시에 점심을 먹어요.</p>")
    st.markdown(interface_text("### 1. 알맞은 시간 표현을 선택해 보세요.", "### 1. Choose the correct Korean time expression"))
    st.caption(interface_text("위 그림은 7시 30분과 12시의 예시예요. 아래에서는 시간 표현의 형태를 확인하세요.", "The picture shows 7:30 and 12:00; the questions below check the form of Korean time expressions."))
    a=render_choice_set(7,"grammar2_clock",[("지금 몇 ___예요?",["시","명","월"],"시","시간을 물을 때는 ‘몇 시예요?’라고 해요."),("일곱 시 삼십 ___이에요.",["분","일","월"],"분","분은 시각의 분을 나타내요."),("수업은 아홉 ___에 시작해요.",["시","분","요일"],"시","시 뒤에 ‘에’를 붙여요.")], hide_reference_button=True)
    st.markdown(interface_text("### 2. 오늘의 일정을 말해 보세요.", "### 2. Say today’s schedule in Korean"))
    event_sentences={"한국어 수업":"한국어 수업이 있어요.","친구 만나기":"친구를 만나요.","점심 먹기":"점심을 먹어요.","운동하기":"운동해요."}
    event=st.selectbox(interface_text("활동", "Activity"),list(event_sentences),key="unit7_g2_event"); t=st.selectbox(interface_text("시간", "Time"),["아홉 시","열두 시","세 시"],key="unit7_g2_time")
    render_learning_info(f"오늘 {t}에 {event_sentences[event]}",icon=":material/forum:")
    if st.button(interface_text("2번 문장을 소리 내어 읽었어요", "I read the Activity 2 sentence aloud"),key="unit7_g2_speaking",type="primary"): st.session_state["unit7_g2_speaking_done"]=True; st.session_state["unit7_g2_checked_choice"]=(event,t)
    speaking_done = st.session_state.get("unit7_g2_speaking_done",False) and st.session_state.get("unit7_g2_checked_choice")== (event,t)
    if not speaking_done: st.session_state.pop("unit7_g2_speaking_done",None)
    if a and speaking_done: st.session_state["grammar2_done_7"]=True
    else: st.session_state.pop("grammar2_done_7",None)


def render_unit7_activity1():
    st.subheader(interface_text("활동 1 · 세종학당 수업", "Activity 1 · Sejong Korean Class")); st.markdown(interface_text("### 1. 재민 씨와 안나 씨가 세종학당 수업 이야기를 해요. 무슨 이야기를 할까요?", "### 1. Read the Korean dialogue about class and answer"))
    st.caption(interface_text("왼쪽은 대화 상황이고, 오른쪽 일정 카드에서 요일과 시간을 정확히 확인하세요.", "The picture shows the situation; use the schedule card to confirm the exact day and time."))
    activity_picture, activity_question = st.columns([0.9, 1.1], gap="small", vertical_alignment="top")
    with activity_picture:
        st.image(
            crop_image_region(
                Path(__file__).with_name("assets") / "units" / "unit7-classroom-calendar.png",
                (0, 250, 1536, 1024),
            ),
            width="stretch",
        )
    with activity_question:
        render_learning_info("**세종학당 수업 일정**\n\n📅 목요일\n\n🕒 저녁 일곱 시", icon=":material/event:")
        st.markdown("**재민:** 안나 씨, 언제 세종학당에 가요?  \n**안나:** 목요일에 가요.  \n**재민:** 수업은 몇 시에 시작해요?  \n**안나:** 저녁 일곱 시에 시작해요.")
        done=render_choice_set(7,"activity1_schedule",[("안나 씨는 언제 세종학당에 가요?",["목요일","금요일","일요일"],"목요일","일정 카드에서 요일을 확인해요."),("수업은 몇 시에 시작해요?",["저녁 일곱 시","아침 아홉 시","열두 시"],"저녁 일곱 시","일정 카드와 대화에서 시간을 확인해요.")], hide_reference_button=True)
    st.markdown(interface_text("### 2. 언제, 몇 시에 수업을 해요?", "### 2. Choose when the class is and read the Korean dialogue aloud")); day=st.selectbox(interface_text("요일", "Weekday"),["월요일","수요일","목요일","금요일"],key="unit7_a1_day"); tm=st.selectbox(interface_text("시간", "Time"),["아침 아홉 시","오후 세 시","저녁 일곱 시"],key="unit7_a1_time"); render_learning_info(f"{day} {tm}에 수업을 해요.",icon=":material/record_voice_over:")
    if st.button(interface_text("2번 대화를 두 번 읽었어요", "I read the Activity 2 dialogue aloud twice"),key="unit7_a1_speaking",type="primary"): st.session_state["unit7_a1_speaking_done"]=True; st.session_state["unit7_a1_checked_choice"]=(day,tm)
    speaking_done=st.session_state.get("unit7_a1_speaking_done",False) and st.session_state.get("unit7_a1_checked_choice")== (day,tm)
    if not speaking_done: st.session_state.pop("unit7_a1_speaking_done",None)
    if done and speaking_done: st.session_state["activity1_completed_7"]=True
    else: st.session_state.pop("activity1_completed_7",None)


def render_unit7_activity2():
    st.subheader(interface_text("활동 2 · 주노 씨의 하루", "Activity 2 · Juno’s Day")); st.markdown(interface_text("### 1. 주노 씨의 하루를 읽고 언제 무엇을 하는지 찾아보세요.", "### 1. Read Juno’s Korean schedule and find when he does each activity"))
    st.caption(interface_text("왼쪽 그림의 네 장면을 위에서 아래로 보고, 오른쪽에서 정확한 시간과 활동을 짝지으세요.", "Read the four scenes from top to bottom and match each activity with its exact time on the right."))
    day_picture, day_question = st.columns([0.9, 1.1], gap="small", vertical_alignment="top")
    with day_picture:
        st.image(
            fit_image_to_canvas(Path(__file__).with_name("assets") / "units" / "unit7-daily-scenes-v2.png", canvas_size=(500, 334), image_size=(500, 334)),
            width="stretch",
        )
    with day_question:
        st.markdown(
            "<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px'>"
            "<div style='padding:11px;border:1px solid #334155;border-radius:10px'><b>① 여섯 시</b><br>일어나요.</div>"
            "<div style='padding:11px;border:1px solid #334155;border-radius:10px'><b>② 일곱 시</b><br>아침을 먹어요.</div>"
            "<div style='padding:11px;border:1px solid #334155;border-radius:10px'><b>③ 열 시</b><br>회의를 해요.</div>"
            "<div style='padding:11px;border:1px solid #334155;border-radius:10px'><b>④ 열두 시</b><br>점심을 먹어요.</div>"
            "</div>", unsafe_allow_html=True,
        )
        reading=render_choice_set(7,"activity2_day",[("주노 씨는 몇 시에 일어나요?",["여섯 시","일곱 시","열 시"],"여섯 시","하루 일정 카드의 첫 번째 장면을 확인해요."),("주노 씨는 열두 시에 무엇을 해요?",["점심을 먹어요.","회의를 해요.","일어나요."],"점심을 먹어요.","시간과 활동을 함께 확인해요.")], hide_reference_button=True)
    st.markdown(interface_text("### 2. 여러분의 하루를 써 보세요.", "### 2. Write your daily schedule in Korean")); rows=[]
    for i in range(3):
        c=st.columns(2); 
        with c[0]: tm=st.selectbox(interface_text(f"{i+1}번째 시간", f"Time {i+1}"),[interface_text("선택하세요", "Choose"),"아침 일곱 시","오전 아홉 시","점심 열두 시","저녁 일곱 시"],key=f"unit7_a2_time_{i}")
        with c[1]: act=st.selectbox(interface_text(f"{i+1}번째 활동", f"Activity {i+1}"),[interface_text("선택하세요", "Choose"),"아침을 먹어요","한국어를 공부해요","친구를 만나요","운동해요"],key=f"unit7_a2_act_{i}")
        rows.append((tm,act))
    choose_label=interface_text("선택하세요", "Choose"); ready=all(t!=choose_label and a!=choose_label for t,a in rows); response="" if not ready else " ".join(f"{t}에 {a}." for t,a in rows)
    if response: render_learning_success(response,icon=":material/edit_note:")
    response_read_aloud=st.checkbox(interface_text("완성한 하루 일정을 소리 내어 읽었어요", "I read my completed Korean schedule aloud"), key="unit7_a2_response_read_aloud", disabled=not (reading and ready), on_change=set_session_state_value, args=("unit7_a2_checked_response", response))
    return response if reading and ready and response_read_aloud and st.session_state.get("unit7_a2_checked_response")==response else ""


def select_vocabulary(unit_number, index):
    st.session_state[f"vocab_index_{unit_number}"] = index
    st.session_state[f"vocab_revealed_{unit_number}"] = False
    st.session_state.pop(f"vocab_category_{unit_number}", None)
    read_key = f"vocab_read_cards_{unit_number}"
    selected_words = set(st.session_state.get(read_key, []))
    selected_words.add(index)
    st.session_state[read_key] = sorted(selected_words)
    if len(selected_words) >= len(TEXTBOOK_VOCABULARY[unit_number]):
        st.session_state[f"vocab_done_{unit_number}"] = True


def select_vocabulary_category(unit_number, category):
    """Show the question expression that introduces a vocabulary category."""
    st.session_state[f"vocab_category_{unit_number}"] = category
    st.session_state[f"vocab_revealed_{unit_number}"] = False


def render_vocabulary_example(sentence, color="lime"):
    """Highlight only the current unit's target forms inside a vocabulary example."""
    unit_number = st.session_state.get("selected_unit_number", 1)
    highlighted = highlight_learning_text(sentence, unit_number)
    st.markdown(
        f'<div class="vocabulary-example">{highlighted}</div>',
        unsafe_allow_html=True,
    )


def render_unit1_picture_dialogue():
    """A four-card, scaffolded dialogue mission for Unit 1 vocabulary."""
    cards = [
        {
            "image": "student.png",
            "question": "학생이에요?",
            "options": ["네, 학생이에요.", "아니요, 회사원이에요."],
            "answer": "네, 학생이에요.",
            "hint": interface_text("책과 가방을 보고 직업을 생각해 보세요.", "Look at the books and bag, then think about the person's occupation."),
        },
        {
            "image": "vietnam.png",
            "question": "한국 사람이에요?",
            "options": ["네, 한국 사람이에요.", "아니요, 베트남 사람이에요."],
            "answer": "아니요, 베트남 사람이에요.",
            "hint": interface_text("인물이 들고 있는 국기를 살펴보세요.", "Look closely at the flag the person is holding."),
        },
        {
            "image": "cook.png",
            "question": "선생님이에요?",
            "options": ["네, 선생님이에요.", "아니요, 요리사예요."],
            "answer": "아니요, 요리사예요.",
            "hint": interface_text("모자와 접시가 어떤 직업을 나타내는지 생각해 보세요.", "Think about the occupation represented by the hat and plate."),
        },
        {"image": "mystery.png", "question": "어느 나라 사람이고, 직업이 뭐예요?"},
    ]
    index_key = "unit1_picture_card_index"
    result_key = "unit1_picture_card_correct"
    checked_choice_key = "unit1_picture_card_checked_choice"
    feedback_key = "unit1_picture_card_feedback_visible"
    st.session_state.setdefault(index_key, 0)
    card_index = min(st.session_state[index_key], len(cards) - 1)
    card = cards[card_index]
    st.markdown(interface_text("### 3. 그림을 보고 대화를 완성해 보세요", "### 3. Look at the picture and complete the dialogue"))
    st.caption(interface_text("그림의 단서를 보고 알맞은 대답을 고르세요. 마지막 카드에서는 새로운 인물을 직접 만듭니다.", "Use the picture clues to choose the correct answer. On the final card, create a new person."))
    st.progress((card_index + 1) / len(cards), text=f"{interface_text('인물 카드', 'Person card')} {card_index + 1}/{len(cards)}")
    image_column, dialogue_column = st.columns([0.65, 2.35], gap="small", vertical_alignment="center")
    with image_column:
        st.image(Path(__file__).with_name("assets") / "people" / card["image"], width=200)
    with dialogue_column:
        st.markdown(f"**가:** {card['question']}")
        if card_index < 3:
            choice = st.radio(
                interface_text("나의 대답", "My answer"),
                card["options"],
                key=f"unit1_picture_choice_{card_index}",
                index=None,
                label_visibility="collapsed",
            )
            result_is_current = st.session_state.get(checked_choice_key) == choice
            if not result_is_current:
                st.session_state[feedback_key] = False
            feedback_visible = bool(st.session_state.get(feedback_key, False)) and result_is_current
            if st.button(
                interface_text("설명 닫기", "Hide explanation")
                if feedback_visible
                else interface_text("대답 확인", "Check answer"),
                key=f"unit1_picture_check_{card_index}",
                type="primary",
                disabled=choice is None,
            ):
                if feedback_visible:
                    st.session_state[feedback_key] = False
                else:
                    st.session_state[result_key] = choice == card["answer"]
                    st.session_state[checked_choice_key] = choice
                    st.session_state[feedback_key] = True
                st.rerun()
            result_is_current = st.session_state.get(checked_choice_key) == choice
            feedback_visible = bool(st.session_state.get(feedback_key, False)) and result_is_current
            if feedback_visible and st.session_state.get(result_key) is True:
                render_learning_success(interface_text("맞아요! 완성한 문장을 소리 내어 읽어 보세요.", "Correct! Read the completed Korean sentence aloud."), icon=":material/check_circle:")
                render_vocabulary_example(card["answer"])
                if st.button(interface_text("다음 인물 →", "Next person →"), key=f"unit1_picture_next_{card_index}"):
                    st.session_state[index_key] = card_index + 1
                    st.session_state.pop(result_key, None)
                    st.session_state.pop(checked_choice_key, None)
                    st.session_state.pop(feedback_key, None)
                    st.rerun()
            elif feedback_visible and st.session_state.get(result_key) is False:
                render_learning_warning(card["hint"], icon=":material/lightbulb:")
        else:
            mystery_country = st.selectbox(interface_text("나라 선택", "Choose a country"), ["캐나다", "베트남", "미국", "프랑스", "태국", "인도네시아", "중국", "일본", "러시아", "케냐"], key="unit1_mystery_country")
            mystery_job = st.selectbox(interface_text("직업 선택", "Choose an occupation"), ["회사원", "대학생", "의사", "경찰", "선생님", "가수", "요리사"], key="unit1_mystery_job")
            job_ending = "이에요" if subject_particle(mystery_job) == "이" else "예요"
            mystery_answer = f"저는 {mystery_country} 사람이에요. {mystery_job}{job_ending}."
            render_vocabulary_example(mystery_answer)
            if st.button(interface_text("대화 완성", "Complete dialogue"), key="unit1_picture_finish", type="primary"):
                st.session_state.unit1_picture_dialogue_done = True
            if st.session_state.get("unit1_picture_dialogue_done"):
                render_learning_success(interface_text("네 장의 인물카드 대화를 모두 완성했어요!", "You completed all four person-card dialogues!"), icon=":material/celebration:")
                if st.button(interface_text("처음부터 다시 하기", "Start again"), key="unit1_picture_reset"):
                    st.session_state[index_key] = 0
                    st.session_state.pop(result_key, None)
                    st.session_state.pop(checked_choice_key, None)
                    st.session_state.pop(feedback_key, None)
                    st.session_state.unit1_picture_dialogue_done = False
                    st.rerun()


def dashboard():
    units = TEXTBOOK_UNITS[1:]
    book = TEXTBOOK_TITLE
    level = "초급 1"
    if "daily_tasks" not in st.session_state:
        st.session_state.daily_tasks = {"vocab": False, "grammar": False, "speaking": False}
    completed_days = get_weekly_completed_days()
    selected_unit_number = st.session_state.get("selected_unit_number", 1)
    current_unit = TEXTBOOK_UNITS[selected_unit_number]
    unit_index = selected_unit_number - 1
    unit_name = current_unit["title"]
    current_vocabulary = TEXTBOOK_VOCABULARY[current_unit["number"]]
    vocab_done = st.session_state.get(f"vocab_done_{current_unit['number']}", False)
    goal_support = UNIT_GOALS_EN[current_unit["number"]] if english_support_enabled() else current_unit["goal"]
    unit_heading = (
        f'Ready to <span class="lime">start Unit {current_unit["number"]}?</span>'
        if english_support_enabled()
        else f'{current_unit["number"]}단원 학습을 <span class="lime">시작해 볼까요?</span>'
    )
    current_unit_label = interface_text(f'{current_unit["number"]}단원', f'Unit {current_unit["number"]}')
    st.markdown(
        f'<div class="eyebrow">Unit learning · {book} · {current_unit_label}</div>'
        f'<h1>{unit_heading}</h1>'
        f'<p class="sub">{goal_support}</p>',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)
    render_unit_learning_order(current_unit)
    if REVIEW_MODE:
        render_learning_info(
            interface_text(
                "현재 점검 모드입니다. 1~5단계를 자유롭게 이동할 수 있지만, 완료 표시와 진행률은 실제 완료 결과만 반영합니다.",
                "Review mode is active. You may move freely through Steps 1–5, but completion marks and progress reflect only completed work.",
            ),
            icon=":material/visibility:",
        )
    completion_steps = get_unit_completion_steps(current_unit["number"])
    unit_progress = sum(completion_steps) / len(completion_steps)
    vocabulary_mastery = 1.0 if completion_steps[0] else 0.0
    grammar_mastery = (int(completion_steps[1]) + int(completion_steps[2])) / 2
    speaking_mastery = (int(completion_steps[3]) + int(completion_steps[4])) / 2
    total_xp = st.session_state.get("total_xp", 0)
    streak = st.session_state.get("streak", 1)
    with st.expander(interface_text("학습 현황과 다음 추천 보기", "View Learning Progress and Next Recommendation"), expanded=False):
        render_motivation_header(unit_progress, total_xp, streak, sum(st.session_state.daily_tasks.values()))
        st.markdown(interface_text("### 나에게 맞는 다음 한 걸음", "### Your Recommended Next Step"))
        if not st.session_state.get("practice_completed"):
            render_learning_info(interface_text("지금은 문법 연습이 가장 효과적이에요. 조사 한 문제를 풀고 오늘의 흐름을 이어 가 보세요.", "Grammar practice is the best next step. Answer one particle question to continue today’s learning."), icon=":material/lightbulb:")
        elif not st.session_state.get("pronunciation_result"):
            render_learning_info(interface_text("문법을 잘 풀었어요. 이제 방금 배운 표현을 소리 내어 말하면 기억이 더 오래 남습니다.", "Good grammar practice. Now say the Korean expression aloud to help you remember it longer."), icon=":material/record_voice_over:")
        else:
            render_learning_success(interface_text("오늘의 핵심 흐름을 완료했어요. 다음에는 나만의 문장으로 레벨업해 보세요.", "You completed today’s core learning flow. Create your own Korean sentence next."), icon=":material/celebration:")
    st.space("small")
    left, right = st.columns([1.25, 1])
    with left:
        unit_label = current_unit_label
        progress_label = interface_text("단원 진행률", "Unit Progress")
        function_label = interface_text("핵심 기능", "Key Functions")
        function_support_en = {
            1: "Greeting · Self-introduction",
            2: "Asking for and giving phone numbers",
        }
        function_support = function_support_en.get(current_unit["number"], current_unit["functions"]) if english_support_enabled() else current_unit["functions"]
        grammar_label = interface_text("문법", "Grammar")
        mapping_label = interface_text("교재 매핑", "Textbook Mapping")
        mapping_value = interface_text("공식 목차 기준", "Based on the official contents")
        st.markdown(f'<div class="card"><div class="eyebrow">Textbook path · {book} · {TEXTBOOK_EDITION}</div><h2>{unit_label} · {unit_name}</h2><p class="sub">{goal_support}</p><div class="progress"><div style="width:{int(unit_progress * 100)}%"></div></div><div style="display:flex;justify-content:space-between"><span class="tiny">{progress_label}</span><span class="tiny lime">{int(unit_progress * 100)}%</span></div><div class="lesson-row"><span>{function_label}</span><b>{function_support}</b></div><div class="lesson-row"><span>{grammar_label}</span><b>{current_unit["grammar"]}</b></div><div class="lesson-row"><span>{mapping_label}</span><b class="lime">{mapping_value}</b></div></div>', unsafe_allow_html=True)
        st.caption(interface_text("아래의 1~5단계 탭에서 현재 학습 단계를 선택하세요.", "Choose your current learning stage from Steps 1–5 below."))
    with right:
        skill_heading = interface_text("실력 상태", "Skill Progress")
        skill_copy = interface_text("교재 진도와 별도로 계산되는 개인별 상태입니다.", "Your individual skill progress, calculated separately from textbook completion.")
        st.markdown(f'<div class="card"><div class="eyebrow">Skill snapshot</div><h2>{skill_heading}</h2><p class="tiny">{skill_copy}</p>', unsafe_allow_html=True)
        skill_rows = [(interface_text("어휘", "Vocabulary"), vocabulary_mastery), (interface_text("문법", "Grammar"), grammar_mastery), (interface_text("말하기", "Speaking"), speaking_mastery)]
        for label, value in skill_rows:
            st.markdown(f'<div style="display:flex;justify-content:space-between;margin-top:14px"><span>{label}</span><span class="tiny lime">{int(value * 100)}%</span></div><div class="progress"><div style="width:{int(value * 100)}%"></div></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    section = LESSON_SECTION_CONTENT[current_unit["number"]]
    if current_unit["number"] == 1:
        render_learning_info(interface_text("단원 도입: 먼저 대화 모델을 3회 읽어 보세요.", "Unit introduction: First, read the model dialogue three times."), icon=":material/play_circle:")
        st.caption(interface_text("상황과 핵심 문장을 먼저 살펴본 뒤, 아래의 1단계 어휘와 표현으로 이어집니다.", "Review the situation and key sentences first, then continue to Step 1: Vocabulary and Expressions."))
        st.markdown(interface_text("### 단원 도입 · 대화 모델 3회 읽기", "### Unit introduction · Read the model dialogue three times"))
        st.caption(interface_text("자기소개 흐름에 따라 인사 → 이름 → 나라 → 직업 순서로 말해 보세요.", "Introduce yourself in this order: greeting → name → country → occupation."))
        dialogue, profile = st.columns(2)
        with dialogue:
            with st.container(border=True):
                st.markdown(interface_text("**대화 모델 · 3회 반복 읽기**", "**Model dialogue · Read three times**"))
                reading_lines = [
                    "A: 안녕하세요?",
                    "B: 안녕하세요? 저는 안나예요.",
                    "A: 안나 씨는 어느 나라 사람이에요?",
                    "B: 저는 한국 사람이에요. 학생이에요.",
                ]
                round_key = "unit1_read_round"
                line_key = "unit1_read_line"
                st.session_state.setdefault(round_key, 0)
                st.session_state.setdefault(line_key, 0)
                current_round = st.session_state[round_key]
                current_line = st.session_state[line_key]
                if current_round < 3:
                    total_reads = len(reading_lines) * 3
                    completed_reads = current_round * len(reading_lines) + current_line
                    st.progress(completed_reads / total_reads, text=f"{interface_text('읽기 진행', 'Reading progress')} · {interface_text('회차', 'Round')} {current_round + 1} · {completed_reads}/{total_reads}")
                    for index, line in enumerate(reading_lines):
                        if index < current_line:
                            style = "done"
                            marker = "✓"
                        elif index == current_line:
                            style = "active"
                            marker = "●"
                        else:
                            style = ""
                            marker = "○"
                        st.markdown(f"<div class='reading-line {style}'>{marker}&nbsp;&nbsp;{line}</div>", unsafe_allow_html=True)
                    st.caption(interface_text(f"{current_round + 1}회차 · 위에서 강조된 한 줄을 소리 내어 읽은 뒤 버튼을 누르세요.", f"Round {current_round + 1} · Read the highlighted Korean line aloud, then press the button."))
                    if st.button(interface_text("이 문장 읽었어요 →", "I read this sentence →"), key=f"unit1_read_{current_round}_{current_line}", type="primary"):
                        if current_line + 1 >= len(reading_lines):
                            st.session_state[round_key] += 1
                            st.session_state[line_key] = 0
                        else:
                            st.session_state[line_key] += 1
                        st.rerun()
                else:
                    render_learning_success(interface_text("대화 3회 읽기를 완료했어요.", "You completed three readings of the dialogue."), icon=":material/check_circle:")
                    completion_hint = interface_text("읽기 단계가 끝났습니다. 오른쪽에서 이름·나라·직업을 바꿔 자기소개를 완성하세요.", "The reading stage is complete. On the right, change the name, country, and occupation to complete the self-introduction.")
                    st.markdown(f"<div class='learning-hint'>{completion_hint}</div>", unsafe_allow_html=True)
        with profile:
            with st.container(border=True):
                st.markdown(interface_text("**단원 도입 · 자기소개 순서**", "**Unit introduction · Self-introduction order**"))
                render_learning_info(interface_text("인사 → 이름 → 나라 → 직업", "Greeting → Name → Country → Occupation"), icon=":material/format_list_numbered:")
                st.caption(interface_text("직접 자기소개를 만드는 활동은 5단계에서 한 번만 진행합니다.", "You will create your own self-introduction once in Step 5."))
        st.space("medium")
    if current_unit["number"] in (2, 3, 4):
        st.space("medium")
    path_support = (
        "Vocabulary → Grammar 1 → Grammar 2 → Activity 1 → Activity 2"
        if english_support_enabled()
        else "어휘와 표현 → 문법 1 → 문법 2 → 활동 1 → 활동 2 순서로 학습합니다."
    )
    path_heading = f'Learn “{current_unit["title"]}”' if english_support_enabled() else f'{current_unit["title"]} 쉽게 익히기'
    st.markdown(f'<div class="eyebrow">Unit learning path · {current_unit_label}</div><h2>{path_heading}</h2><p class="sub lesson-path-copy">{path_support}</p>', unsafe_allow_html=True)
    vocab_done = st.session_state.get(f"vocab_done_{current_unit['number']}", False)
    grammar1_done = st.session_state.get(f"grammar1_done_{current_unit['number']}", False)
    grammar2_done = st.session_state.get(f"grammar2_done_{current_unit['number']}", False)
    activity1_done = st.session_state.get(f"activity1_completed_{current_unit['number']}", False)
    unit_completed = st.session_state.get(f"unit_completed_{current_unit['number']}", False)
    # 앞 단계가 끝나지 않았다면 과거 세션에 남은 뒤 단계 완료값을 무효화합니다.
    if not vocab_done:
        grammar1_done = False
        grammar2_done = False
        activity1_done = False
        unit_completed = False
        st.session_state.pop(f"grammar1_done_{current_unit['number']}", None)
    elif not grammar1_done:
        grammar2_done = False
        activity1_done = False
        unit_completed = False
        st.session_state.pop(f"grammar2_done_{current_unit['number']}", None)
    elif not grammar2_done:
        activity1_done = False
        unit_completed = False
        st.session_state.pop(f"activity1_completed_{current_unit['number']}", None)
    elif not activity1_done:
        unit_completed = False
        st.session_state.pop(f"unit_completed_{current_unit['number']}", None)
    # 점검 모드는 화면 접근만 열고, 완료 판정은 위의 순차 상태를 그대로 사용합니다.
    grammar1_unlocked = REVIEW_MODE or vocab_done
    grammar2_unlocked = REVIEW_MODE or grammar1_done
    activity1_unlocked = REVIEW_MODE or grammar2_done
    activity2_unlocked = REVIEW_MODE or activity1_done
    step1_done = vocab_done
    completed_steps = [step1_done, grammar1_done, grammar2_done, activity1_done, unit_completed]
    tab_names = (
        ["● Vocabulary & Expressions   ", "● Grammar 1   ", "● Grammar 2   ", "● Activity 1   ", "● Activity 2"]
        if english_support_enabled()
        else ["● 어휘와 표현   ", "● 문법 1   ", "● 문법 2   ", "● 활동 1   ", "● 활동 2"]
    )
    tab_labels = [f"{name} {'✓' if completed else ''}" for name, completed in zip(tab_names, completed_steps)]
    if not step1_done:
        active_step = 0
    elif not grammar1_done:
        active_step = 1
    elif not grammar2_done:
        active_step = 2
    elif not activity1_done:
        active_step = 3
    elif not unit_completed:
        active_step = 4
    else:
        active_step = None
    completed_css = ",\n".join(
        f'div[data-testid="stTabs"] [data-baseweb="tab-list"] [role="tab"]:nth-of-type({index + 1}), div[data-testid="stTabs"] [data-baseweb="tab-list"] [role="tab"]:nth-of-type({index + 1}) *'
        for index, completed in enumerate(completed_steps) if completed
    )
    completed_rule = f"{completed_css} {{ color:#ffffff !important; font-weight:700 !important; }}" if completed_css else ""
    active_number = active_step + 1 if active_step is not None else None
    active_rule = (
        f'div[data-testid="stTabs"] [data-baseweb="tab-list"] [role="tab"]:nth-of-type({active_number}), '
        f'div[data-testid="stTabs"] [data-baseweb="tab-list"] [role="tab"]:nth-of-type({active_number}) * '
        "{ color:#ff5f52 !important; font-weight:800 !important; }"
        if active_number is not None else ""
    )
    st.markdown(
        f"""
        <style>
        {completed_rule}
        {active_rule}
        </style>
        """,
        unsafe_allow_html=True,
    )
    vocab_tab, grammar1_tab, grammar2_tab, activity1_tab, activity2_tab = st.tabs(
        tab_labels,
        default=tab_labels[active_step if active_step is not None else 4],
    )
    with vocab_tab:
        with st.container(border=True):
            if current_unit["number"] == 1:
                st.subheader(interface_text("어휘와 표현 · 나라와 직업", "Vocabulary and Expressions · Countries and Occupations"))
            elif current_unit["number"] == 2:
                st.subheader(interface_text("어휘와 표현 · 한자어 수", "Vocabulary and Expressions · Sino-Korean Numbers"))
            elif current_unit["number"] == 3:
                st.subheader(interface_text("어휘와 표현 · 물건과 위치", "Vocabulary and Expressions · Objects and Locations"))
            elif current_unit["number"] == 4:
                st.subheader(interface_text("어휘와 표현 · 기본 동작", "Vocabulary and Expressions · Basic Actions"))
            elif current_unit["number"] == 5:
                st.subheader(interface_text("어휘와 표현 · 장소와 식품", "Vocabulary and Expressions · Places and Foods"))
            elif current_unit["number"] == 6:
                st.subheader(interface_text("어휘와 표현 · 고유어 수와 수량", "Vocabulary and Expressions · Korean Numbers and Counters"))
            elif current_unit["number"] == 7:
                st.subheader(interface_text("어휘와 표현 · 날짜와 요일", "Vocabulary and Expressions · Dates and Weekdays"))
            elif current_unit["number"] == 8:
                st.subheader(interface_text("어휘와 표현 · 날씨와 계절", "Vocabulary and Expressions · Weather and Seasons"))
            elif current_unit["number"] == 9:
                st.subheader(interface_text("어휘와 표현 · 장소와 지난 활동", "Vocabulary and Expressions · Places and Past Activities"))
            else:
                st.subheader("핵심 어휘를 먼저 소리 내어 읽어요")
            vocabulary_guidance = interface_text("아래 단어와 예문을 소리 내어 읽어 보세요. 궁금한 뜻은 ‘뜻 보기’를 눌러 확인하세요.", "Read the Korean words and example sentences below aloud. Select ‘Show meaning’ whenever you need help.") if current_unit["number"] in (1, 2, 3, 4, 5, 6, 7, 8, 9) else "아래 단어와 예문을 소리 내어 읽어 보세요. 궁금한 뜻은 ‘뜻 보기’를 눌러 확인하세요."
            if current_unit["number"] == 4:
                st.caption(interface_text("그림을 보고 기본 동사를 하나씩 누르세요. 선택한 예문을 소리 내어 읽어 보세요.", "Look at the picture and select each basic Korean verb. Read the selected example sentence aloud."))
            else:
                st.caption(vocabulary_guidance)
            if current_unit["number"] == 2:
                st.markdown(interface_text("### 1. 숫자를 소리 내어 읽어 보세요.", "### 1. Read the Korean numbers aloud"))
                sino_numbers = [
                    ("0", "영/공"), ("1", "일"), ("2", "이"), ("3", "삼"), ("4", "사"),
                    ("5", "오"), ("6", "육"), ("7", "칠"), ("8", "팔"), ("9", "구"), ("10", "십"),
                    ("11", "십일"), ("12", "십이"), ("13", "십삼"), ("14", "십사"), ("15", "십오"),
                    ("16", "십육"), ("17", "십칠"), ("18", "십팔"), ("19", "십구"), ("20", "이십"),
                    ("30", "삼십"), ("40", "사십"), ("50", "오십"), ("60", "육십"),
                    ("70", "칠십"), ("80", "팔십"), ("90", "구십"), ("100", "백"), ("1000", "천"),
                ]
                selected_sino_number = st.session_state.get("unit2_selected_sino_number")
                for start in range(0, len(sino_numbers), 10):
                    if start:
                        st.space("small")
                    number_columns = st.columns(min(10, len(sino_numbers) - start))
                    for column, (number, reading) in zip(number_columns, sino_numbers[start:start + 10]):
                        with column:
                            if st.button(
                                number,
                                key=f"unit2_sino_number_{number}",
                                type="primary" if selected_sino_number == number else "secondary",
                                width="stretch",
                            ):
                                if selected_sino_number == number:
                                    st.session_state.pop("unit2_selected_sino_number", None)
                                    st.session_state.pop("unit2_selected_sino_reading", None)
                                else:
                                    st.session_state["unit2_selected_sino_number"] = number
                                    st.session_state["unit2_selected_sino_reading"] = reading
                                st.session_state.pop("unit2_selected_number_example", None)
                                st.session_state.pop("unit2_selected_number_example_reading", None)
                                st.rerun()
                            st.markdown(f"<div style='text-align:center;font-size:.84rem'>{reading}</div>", unsafe_allow_html=True)
                if selected_sino_number:
                    render_learning_success(f"{selected_sino_number} = {st.session_state.get('unit2_selected_sino_reading')}", icon=":material/volume_up:")
                st.space("small")
                st.markdown(interface_text("### 2. 숫자가 들어간 정보를 읽어 보세요.", "### 2. Read information containing Korean numbers"))
                unit2_number_examples = [
                    ("4층", "사층"),
                    ("5월 3일", "오월 삼일"),
                    ("35쪽", "삼십오쪽"),
                    ("320번", "삼백이십번"),
                    ("405호", "사백오호"),
                    ("800원", "팔백원"),
                ]
                selected_number_example = st.session_state.get("unit2_selected_number_example")
                number_examples = st.columns(len(unit2_number_examples))
                for column, (expression, reading) in zip(number_examples, unit2_number_examples):
                    with column:
                        if st.button(
                            expression,
                            key=f"unit2_number_example_{expression}",
                            type="primary" if selected_number_example == expression else "secondary",
                            width="stretch",
                        ):
                            st.session_state.pop("unit2_selected_sino_number", None)
                            st.session_state.pop("unit2_selected_sino_reading", None)
                            if selected_number_example == expression:
                                st.session_state.pop("unit2_selected_number_example", None)
                                st.session_state.pop("unit2_selected_number_example_reading", None)
                            else:
                                st.session_state["unit2_selected_number_example"] = expression
                                st.session_state["unit2_selected_number_example_reading"] = reading
                            st.rerun()
                if selected_number_example:
                    render_learning_success(
                        f"{selected_number_example} = {st.session_state.get('unit2_selected_number_example_reading')}",
                        icon=":material/volume_up:",
                    )
                st.space("small")
                st.markdown(interface_text("### 3. 숫자를 넣어 대화를 완성해 보세요.", "### 3. Complete each dialogue with the correct number"))
                st.caption(interface_text("그림에 보이는 숫자를 읽고 알맞은 대답을 선택하세요.", "Read the number in each picture and choose the correct Korean answer."))
                unit2_choose_label = interface_text("선택하세요", "Choose")
                unit2_visual_questions = [
                    ("버스", "몇 번이에요?", [unit2_choose_label, "140번", "204번", "678번"], "140번", "백사십번", "bus"),
                    ("달력", "몇 월이에요?", [unit2_choose_label, "3월", "5월", "8월"], "5월", "오월", "calendar"),
                    ("가격표", "얼마예요?", [unit2_choose_label, "500원", "800원", "1,000원"], "800원", "팔백원", "price"),
                    ("방", "몇 호예요?", [unit2_choose_label, "320호", "405호", "508호"], "405호", "사백오호", "room"),
                ]
                unit2_visual_results = []
                visual_question_columns = st.columns(2)
                for index, (label, question, options, correct, correct_reading, visual_type) in enumerate(unit2_visual_questions):
                    with visual_question_columns[index % 2]:
                        with st.container(border=True, height=390):
                            if visual_type == "bus":
                                st.image(Path(__file__).with_name("assets") / "people" / "bus-140.png", width=230)
                            elif visual_type == "calendar":
                                st.image(Path(__file__).with_name("assets") / "people" / "calendar-may.png", width=170)
                            elif visual_type == "price":
                                st.image(Path(__file__).with_name("assets") / "people" / "banknote-800.png", width=230)
                            else:
                                st.image(Path(__file__).with_name("assets") / "people" / "room-405-sign.png", width=230)
                            selected_answer = st.selectbox(question, options, key=f"unit2_visual_number_{index}")
                            unit2_visual_results.append(selected_answer == correct)
                            if selected_answer != unit2_choose_label:
                                correct_ending = "이에요" if subject_particle(correct) == "이" else "예요"
                                if selected_answer == correct:
                                    render_learning_success(f"가: {question}\n\n나: {correct_reading}{correct_ending}.")
                                else:
                                    render_learning_warning(interface_text(f"그림의 숫자를 다시 확인해 보세요. 정답은 {correct_reading}{correct_ending}.", f"Check the number in the picture again. The correct Korean answer is {correct_reading}{correct_ending}."))
                unit2_number_tasks_done = all(unit2_visual_results)
                if unit2_number_tasks_done:
                    render_learning_success(interface_text("네 개의 숫자 대화를 모두 정확하게 완성했어요.", "You completed all four number dialogues correctly."), icon=":material/check_circle:")
                st.divider()
            vocabulary_words = [word for word, _ in current_vocabulary]
            vocab_index_key = f"vocab_index_{current_unit['number']}"
            vocab_read_key = f"vocab_read_cards_{current_unit['number']}"
            st.session_state.setdefault(vocab_index_key, 0)
            st.session_state.setdefault(vocab_read_key, [])
            pending_vocab_index = st.session_state.pop(f"vocab_pending_index_{current_unit['number']}", None)
            if pending_vocab_index is not None:
                st.session_state[vocab_index_key] = pending_vocab_index
            active_index = st.session_state[vocab_index_key]
            read_cards = {index for index in st.session_state[vocab_read_key] if 0 <= index < len(vocabulary_words)}
            st.session_state[vocab_read_key] = sorted(read_cards)
            selected_word = vocabulary_words[active_index]
            selected_meaning = dict(current_vocabulary)[selected_word]
            selected_category = st.session_state.get(f"vocab_category_{current_unit['number']}")
            category_examples = {"나라": "어느 나라 사람이에요?", "직업": "직업이 뭐예요?"}
            example_sentence = category_examples.get(
                selected_category,
                VOCABULARY_EXAMPLES.get(current_unit["number"], {}).get(selected_word, f"{selected_word}{object_particle(selected_word)} 말해 보세요."),
            )
            st.markdown(
                """
                <style>
                .vocabulary-example {
                    font-size:1.12rem;
                    font-weight:700;
                    line-height:1.8;
                    margin-bottom:18px;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(f"**{interface_text('오늘의 핵심 어휘', 'Today’s Key Vocabulary')} ({active_index + 1}/{len(vocabulary_words)})**")
            if current_unit["number"] == 1:
                country_count = 11
                country_selectors = ",".join(
                    ["div.st-key-vocab_category_country_1 button"]
                    + [f"div.st-key-vocab_select_1_{index} button" for index in range(country_count)]
                )
                job_selectors = ",".join(
                    ["div.st-key-vocab_category_job_1 button"]
                    + [f"div.st-key-vocab_select_1_{index} button" for index in range(country_count, len(vocabulary_words))]
                )
                country_hover_selectors = country_selectors.replace(" button", " button:hover")
                job_hover_selectors = job_selectors.replace(" button", " button:hover")
                if selected_category == "나라":
                    active_selector = "div.st-key-vocab_category_country_1 button"
                    active_color = "#82c91e"
                elif selected_category == "직업":
                    active_selector = "div.st-key-vocab_category_job_1 button"
                    active_color = "#ff7a59"
                else:
                    active_selector = f"div.st-key-vocab_select_1_{active_index} button"
                    active_color = "#82c91e" if active_index < country_count else "#ff7a59"
                st.markdown(
                    f"""
                    <style>
                    {country_selectors} {{
                        border-color: #82c91e !important;
                        color: #b7ef58 !important;
                        background: rgba(130, 201, 30, .10) !important;
                    }}
                    {job_selectors} {{
                        border-color: #ff7a59 !important;
                        color: #ff9a80 !important;
                        background: rgba(255, 122, 89, .10) !important;
                    }}
                    {country_hover_selectors} {{ background: rgba(130, 201, 30, .22) !important; }}
                    {job_hover_selectors} {{ background: rgba(255, 122, 89, .22) !important; }}
                    {active_selector} {{
                        background: {active_color} !important;
                        border-color: {active_color} !important;
                        color: #111111 !important;
                        font-weight: 800 !important;
                    }}
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown("### 1. 어느 나라 사람이에요?")
                country_heading, country_example = st.columns([1, 10], vertical_alignment="center")
                with country_heading:
                    st.button(
                        "나라",
                        key="vocab_category_country_1",
                        type="primary" if selected_category == "나라" else "secondary",
                        on_click=select_vocabulary_category,
                        args=(current_unit["number"], "나라"),
                        width="stretch",
                    )
                with country_example:
                    if selected_category == "나라" or (selected_category is None and active_index < country_count):
                        render_vocabulary_example(example_sentence, color="lime")
                country_columns = st.columns(country_count)
                for column, index in zip(country_columns, range(country_count)):
                    with column:
                        st.button(
                            vocabulary_words[index],
                            key=f"vocab_select_{current_unit['number']}_{index}",
                            type="primary" if selected_category is None and index == active_index else "secondary",
                            on_click=select_vocabulary,
                            args=(current_unit["number"], index),
                            width="stretch",
                        )
                st.space("small")
                st.markdown("### 2. 직업이 뭐예요?")
                job_heading, job_example = st.columns([1, 10], vertical_alignment="center")
                with job_heading:
                    st.button(
                        "직업",
                        key="vocab_category_job_1",
                        type="primary" if selected_category == "직업" else "secondary",
                        on_click=select_vocabulary_category,
                        args=(current_unit["number"], "직업"),
                        width="stretch",
                    )
                with job_example:
                    if selected_category == "직업" or (selected_category is None and active_index >= country_count):
                        render_vocabulary_example(example_sentence, color="coral")
                job_columns = st.columns(len(vocabulary_words) - country_count)
                for column, index in zip(job_columns, range(country_count, len(vocabulary_words))):
                    with column:
                        st.button(
                            vocabulary_words[index],
                            key=f"vocab_select_{current_unit['number']}_{index}",
                            type="primary" if selected_category is None and index == active_index else "secondary",
                            on_click=select_vocabulary,
                            args=(current_unit["number"], index),
                            width="stretch",
                        )
            elif current_unit["number"] == 3:
                render_unit3_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence, selected_word, selected_meaning
                )
            elif current_unit["number"] == 4:
                unit4_vocabulary_tasks_done = render_unit4_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            elif current_unit["number"] == 5:
                unit5_vocabulary_tasks_done = render_unit5_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            elif current_unit["number"] == 6:
                unit6_vocabulary_tasks_done = render_unit6_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            elif current_unit["number"] == 7:
                unit7_vocabulary_tasks_done = render_unit7_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            elif current_unit["number"] == 8:
                unit8_vocabulary_tasks_done = render_unit8_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            elif current_unit["number"] == 9:
                unit9_vocabulary_tasks_done = render_unit9_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            elif current_unit["number"] == 10:
                unit10_vocabulary_tasks_done = render_unit10_vocabulary_panel(
                    vocabulary_words, active_index, example_sentence
                )
            else:
                # 긴 어휘 목록도 작은 화면에서 읽기 쉽도록 다섯 칸씩 자동 줄바꿈합니다.
                word_columns = [column for start in range(0, len(vocabulary_words), 5) for column in st.columns(min(5, len(vocabulary_words) - start))]
                for column, (index, word) in zip(word_columns, enumerate(vocabulary_words)):
                    with column:
                        st.button(
                            word,
                            key=f"vocab_select_{current_unit['number']}_{index}",
                            type="primary" if index == active_index else "secondary",
                            on_click=select_vocabulary,
                            args=(current_unit["number"], index),
                            width="stretch",
                        )
            active_index = st.session_state[vocab_index_key]
            selected_word = vocabulary_words[active_index]
            selected_meaning = dict(current_vocabulary)[selected_word]
            if current_unit["number"] not in (1, 3, 4, 5, 6, 7, 8, 9, 10):
                render_vocabulary_example(example_sentence)
            revealed_key = f"vocab_revealed_{current_unit['number']}"
            revealed = st.session_state.get(revealed_key, False)
            all_vocabulary_read = len(read_cards) == len(vocabulary_words)
            if current_unit["number"] != 3:
                meaning_button = interface_text("뜻 보기 / 뜻 가리기", "Show / Hide meaning") if current_unit["number"] in (1, 2) else "뜻 보기 / 뜻 가리기"
                if st.button(meaning_button, key=f"vocab_flip_{current_unit['number']}"):
                    st.session_state[revealed_key] = not revealed
                    st.rerun()
                if st.session_state.get(revealed_key, False):
                    render_learning_success(f"{selected_word} = {selected_meaning}")
            unit3_dialogue_done = True
            if current_unit["number"] == 3:
                unit3_dialogue_done = render_unit3_vocabulary_dialogue()
            if current_unit["number"] == 1:
                st.divider()
                render_unit1_picture_dialogue()
            unit1_tasks_done = st.session_state.get("unit1_picture_dialogue_done", False) if current_unit["number"] == 1 else True
            unit1_core_vocab_ready = all_vocabulary_read and unit1_tasks_done if current_unit["number"] == 1 else True
            unit1_required_tasks_done = (
                unit1_core_vocab_ready
                and st.session_state.get("unit1_read_round", 0) >= 3
                and st.session_state.get("unit1_intro_done", False)
            ) if current_unit["number"] == 1 else True
            unit2_tasks_done = unit2_number_tasks_done if current_unit["number"] == 2 else True
            unit4_tasks_done = unit4_vocabulary_tasks_done if current_unit["number"] == 4 else True
            unit5_tasks_done = unit5_vocabulary_tasks_done if current_unit["number"] == 5 else True
            unit6_tasks_done = unit6_vocabulary_tasks_done if current_unit["number"] == 6 else True
            unit7_tasks_done = unit7_vocabulary_tasks_done if current_unit["number"] == 7 else True
            unit8_tasks_done = unit8_vocabulary_tasks_done if current_unit["number"] == 8 else True
            unit9_tasks_done = unit9_vocabulary_tasks_done if current_unit["number"] == 9 else True
            unit10_tasks_done = unit10_vocabulary_tasks_done if current_unit["number"] == 10 else True
            vocabulary_stage_ready = all_vocabulary_read and unit1_required_tasks_done and unit2_tasks_done and unit3_dialogue_done and unit4_tasks_done and unit5_tasks_done and unit6_tasks_done and unit7_tasks_done and unit8_tasks_done and unit9_tasks_done and unit10_tasks_done
            if vocabulary_stage_ready:
                vocab_done = True
                st.session_state[f"vocab_done_{current_unit['number']}"] = True
                if current_unit["number"] == 2:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 어휘와 숫자 대화를 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} vocabulary items and number dialogues."), icon=":material/check_circle:")
                elif current_unit["number"] == 3:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 어휘와 그림 대화를 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} vocabulary items and picture dialogues."), icon=":material/check_circle:")
                elif current_unit["number"] == 4:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 기본 동사와 연결·그림 활동을 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} basic verbs and both practice activities."), icon=":material/check_circle:")
                elif current_unit["number"] == 5:
                    render_learning_success(f"✓ {len(vocabulary_words)}개 장소·식품 어휘와 그림 활동을 모두 완료했어요.", icon=":material/check_circle:")
                elif current_unit["number"] == 6:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 고유어 수와 수량 활동을 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} Korean numbers and quantity activities."), icon=":material/check_circle:")
                elif current_unit["number"] == 7:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 날짜·요일 어휘와 대화 활동을 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} date and weekday words and dialogues."), icon=":material/check_circle:")
                elif current_unit["number"] == 8:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 날씨·계절 어휘와 그림 활동을 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} weather and season words and picture activities."), icon=":material/check_circle:")
                elif current_unit["number"] == 9:
                    render_learning_success(interface_text(f"✓ {len(vocabulary_words)}개 장소·과거 활동 어휘와 그림 활동을 모두 완료했어요.", f"✓ You completed all {len(vocabulary_words)} place and past-activity words and picture activities."), icon=":material/check_circle:")
                elif current_unit["number"] == 9:
                    render_learning_success(f"✓ {len(vocabulary_words)}개 장소·지난 활동 표현과 그림 활동을 모두 완료했어요.", icon=":material/check_circle:")
                elif current_unit["number"] == 10:
                    render_learning_success(f"✓ {len(vocabulary_words)}개 주말 제안·이동 목적 표현과 그림 활동을 모두 완료했어요.", icon=":material/check_circle:")
                else:
                    render_learning_success(f"✓ {len(vocabulary_words)}개 어휘를 모두 확인했어요.", icon=":material/check_circle:")
            else:
                if current_unit["number"] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
                    st.session_state.pop(f"vocab_done_{current_unit['number']}", None)
                    vocab_done = False
                    if current_unit["number"] == 1 and all_vocabulary_read and not unit1_tasks_done:
                        st.caption(interface_text("네 장의 인물 카드 대화를 모두 완료해 주세요.", "Complete all four person-card dialogues."))
                    elif current_unit["number"] == 1 and unit1_core_vocab_ready and st.session_state.get("unit1_read_round", 0) < 3:
                        st.caption(interface_text("대화 모델을 3회 읽어야 어휘와 표현 단계가 끝납니다.", "Read the model dialogue three times to continue."))
                    elif current_unit["number"] == 1 and unit1_core_vocab_ready and not st.session_state.get("unit1_intro_done", False):
                        st.caption(interface_text("자기소개 카드를 완성해야 어휘와 표현 단계가 끝납니다.", "Complete the self-introduction card to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 2 and all_vocabulary_read and not unit2_tasks_done:
                        st.caption(interface_text("마지막으로 네 개의 숫자 대화를 모두 맞히면 어휘와 표현 단계가 끝납니다.", "Answer all four number dialogues correctly to complete Vocabulary and Expressions."))
                    if current_unit["number"] == 3 and all_vocabulary_read and not unit3_dialogue_done:
                        st.caption(interface_text("마지막으로 3번 그림 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.", "Answer all three picture-dialogue questions correctly to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 4 and all_vocabulary_read and not unit4_tasks_done:
                        st.caption(interface_text("2번 연결 활동과 3번 그림 활동을 모두 맞히면 어휘와 표현 단계가 완료됩니다.", "Answer all questions in Activities 2 and 3 correctly to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 5 and all_vocabulary_read and not unit5_tasks_done:
                        st.caption("2번 그림 선택과 3번 그림 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.")
                    if current_unit["number"] == 6 and all_vocabulary_read and not unit6_tasks_done:
                        st.caption(interface_text("2번 수량 선택과 3번 그림 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.", "Answer all quantity and picture-dialogue questions correctly to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 7 and all_vocabulary_read and not unit7_tasks_done:
                        st.caption(interface_text("2번 요일 선택과 3번 날짜 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.", "Answer all weekday and date-dialogue questions correctly to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 8 and all_vocabulary_read and not unit8_tasks_done:
                        st.caption(interface_text("2번 계절 연결과 3번 날씨 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.", "Answer all season and weather-dialogue questions correctly to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 9 and all_vocabulary_read and not unit9_tasks_done:
                        st.caption(interface_text("2번 장소 연결과 3번 과거 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.", "Answer all place and past-activity questions correctly to finish Vocabulary and Expressions."))
                    if current_unit["number"] == 9 and all_vocabulary_read and not unit9_tasks_done:
                        st.caption("2번 장소와 활동 연결, 3번 과거 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.")
                    if current_unit["number"] == 10 and all_vocabulary_read and not unit10_tasks_done:
                        st.caption("2번 장소와 목적 연결, 3번 주말 약속 대화를 모두 맞히면 어휘와 표현 단계가 완료됩니다.")
                else:
                    vocab_done = st.session_state.get(f"vocab_done_{current_unit['number']}", False)
            if vocab_done:
                reward_key = f"vocab_rewarded_{current_unit['number']}"
                if not st.session_state.get(reward_key, False):
                    st.session_state.daily_tasks["vocab"] = True
                    st.session_state.total_xp = st.session_state.get("total_xp", 0) + 5
                    st.session_state[reward_key] = True
                    save_progress()
                    st.toast("어휘 미션 완료! +5 XP", icon=":material/star:")
                st.caption("어휘 워밍업을 완료했어요. 2단계 문법 1로 이동합니다.")
    if False and current_unit["number"] == 1:
        st.space("medium")
        st.markdown("### 1단계 어휘와 표현 · 활동 2~3")
        st.caption("활동 1 핵심 어휘 읽기에 이어, 활동 2 대화 모델 읽기와 활동 3 자기소개 완성까지 진행합니다. 모두 끝내면 1단계가 완료됩니다.")
        dialogue, profile = st.columns(2)
        with dialogue:
            with st.container(border=True):
                st.markdown("**대화 모델 · 3회 반복 읽기**")
                reading_lines = [
                    "A: 안녕하세요?",
                    "B: 안녕하세요? 저는 안나예요.",
                    "A: 안나 씨는 어느 나라 사람이에요?",
                    "B: 저는 한국 사람이에요. 학생이에요.",
                ]
                round_key = "unit1_read_round"
                line_key = "unit1_read_line"
                st.session_state.setdefault(round_key, 0)
                st.session_state.setdefault(line_key, 0)
                current_round = st.session_state[round_key]
                current_line = st.session_state[line_key]
                total_reads = len(reading_lines) * 3
                completed_reads = min(total_reads, current_round * len(reading_lines) + current_line)
                st.progress(completed_reads / total_reads, text=interface_text(f"읽기 진행 {min(current_round + 1, 3)}회차 · {completed_reads}/{total_reads}", f"Reading progress · Round {min(current_round + 1, 3)} · {completed_reads}/{total_reads}"))
                for index, line in enumerate(reading_lines):
                    if current_round >= 3 or index < current_line:
                        style, marker = "done", "✓"
                    elif index == current_line:
                        style, marker = "active", "●"
                    else:
                        style, marker = "", "○"
                    st.markdown(f"<div class='reading-line {style}'>{marker}&nbsp;&nbsp;{line}</div>", unsafe_allow_html=True)
                if current_round < 3:
                    st.caption(interface_text(f"{current_round + 1}회차 · 강조된 한 줄을 소리 내어 읽은 뒤 버튼을 누르세요.", f"Round {current_round + 1} · Read the highlighted Korean line aloud, then press the button."))
                    if not unit1_core_vocab_ready:
                        render_learning_lock(interface_text("먼저 위의 어휘 카드를 모두 읽고 완료 표시를 해 주세요.", "First, read and complete all the vocabulary cards above."))
                    if st.button(interface_text("이 문장 읽었어요 →", "I read this sentence →"), key=f"unit1_read_{current_round}_{current_line}", type="primary", disabled=not unit1_core_vocab_ready):
                        if current_line + 1 >= len(reading_lines):
                            st.session_state[round_key] += 1
                            st.session_state[line_key] = 0
                        else:
                            st.session_state[line_key] += 1
                        st.rerun()
                else:
                    render_learning_success(interface_text("대화를 3번 읽었어요! 이제 자기소개를 완성해 보세요.", "You read the dialogue three times! Now complete your self-introduction."), icon=":material/celebration:")
                    if st.button(interface_text("다시 3번 읽기", "Read three times again"), key="unit1_read_reset"):
                        st.session_state[round_key] = 0
                        st.session_state[line_key] = 0
                        st.rerun()
        with profile:
            with st.container(border=True):
                st.markdown(interface_text("**나의 자기소개 카드**", "**My Self-Introduction Card**"))
                if not unit1_core_vocab_ready:
                    render_learning_lock(interface_text("먼저 위의 어휘와 표현을 완료해야 열립니다.", "Complete the vocabulary and expressions above to unlock this card."))
                elif current_round < 3:
                    render_learning_lock(interface_text("먼저 왼쪽 대화 모델을 3회 읽어야 열립니다.", "Read the model dialogue on the left three times to unlock this card."))
                else:
                    self_name = st.text_input(interface_text("이름", "Name"), placeholder=interface_text("예: 마리아", "Example: Maria"), key="unit1_self_name")
                    self_country = st.selectbox(interface_text("나라", "Country"), ["한국", "캐나다", "베트남", "미국", "프랑스", "태국", "인도네시아", "중국", "일본", "러시아", "케냐", "브라질"], key="unit1_self_country")
                    self_job = st.selectbox(interface_text("직업", "Occupation"), ["회사원", "대학생", "의사", "경찰", "선생님", "가수", "요리사"], key="unit1_self_job")
                    if st.button(interface_text("내 소개 완성하기", "Complete my introduction"), key="unit1_make_intro", type="primary"):
                        if self_name.strip():
                            self_name_ending = "이에요" if subject_particle(self_name.strip()) == "이" else "예요"
                            self_job_ending = "이에요" if subject_particle(self_job) == "이" else "예요"
                            st.session_state.unit1_intro = f"안녕하세요? 저는 {self_name.strip()}{self_name_ending}. {self_country} 사람이에요. {self_job}{self_job_ending}."
                            st.session_state.unit1_intro_done = True
                            st.rerun()
                        else:
                            render_learning_warning(interface_text("먼저 이름을 입력해 주세요.", "Enter your name first."), icon=":material/edit:")
                    if st.session_state.get("unit1_intro"):
                        render_learning_success(st.session_state.unit1_intro, icon=":material/chat:")
                        introduction_hint = interface_text("완성한 자기소개를 천천히 소리 내어 읽어 보세요. 이름·나라·직업을 바꾸어 2번 더 연습해도 좋아요.", "Read your completed Korean introduction aloud slowly. Practice two more times by changing the name, country, and occupation.")
                        st.markdown(f"<div class='learning-hint'>{introduction_hint}</div>", unsafe_allow_html=True)
                        if st.button(interface_text("자기소개 다시 작성", "Write my introduction again"), key="unit1_intro_reset"):
                            st.session_state.pop("unit1_intro", None)
                            st.session_state.unit1_intro_done = False
                            st.rerun()
        st.space("medium")
        st.markdown(interface_text("### 오늘의 핵심 정리", "### Today’s Key Review"))
        render_learning_info(interface_text("문법 학습으로 넘어가기 전에, 오늘 배운 표현을 한 번 더 확인해 보세요.", "Review today’s Korean expressions once more before moving on to grammar."), icon=":material/format_list_bulleted:")
        summary_cols = st.columns(3)
        summaries = [
            ("이에요", interface_text("마지막 글자에 받침이 있는 명사 뒤", "Use after a noun ending in a final consonant"), "학생이에요"),
            ("예요", interface_text("마지막 글자에 받침이 없는 명사 뒤", "Use after a noun ending in a vowel"), "안나예요"),
            ("은/는", interface_text("명사를 주제로 말할 때 · 받침이 있으면 은, 없으면 는", "Marks the topic · 은 after a final consonant, 는 after a vowel"), "저는 안나예요"),
        ]
        for column, (form, rule, example) in zip(summary_cols, summaries):
            with column:
                with st.container(border=True):
                    st.markdown(f"#### {form}")
                    st.caption(rule)
                    st.write(example)
    with grammar1_tab:
        with st.container(border=True):
            if current_unit["number"] == 1:
                st.subheader(interface_text("문법 1 · 이에요/예요", "Grammar 1 · 이에요/예요"))
            elif current_unit["number"] == 2:
                st.subheader(interface_text("문법 1 · 이/가", "Grammar 1 · 이/가"))
            elif current_unit["number"] == 3:
                st.subheader(interface_text("문법 1 · 이/그/저", "Grammar 1 · 이/그/저"))
            elif current_unit["number"] == 4:
                st.subheader(interface_text("문법 1 · -아요/어요", "Grammar 1 · -아요/어요"))
            elif current_unit["number"] == 5:
                st.subheader("문법 1 · 에 가다")
            elif current_unit["number"] == 6:
                st.subheader(interface_text("문법 1 · 단위 명사", "Grammar 1 · Counters"))
            elif current_unit["number"] == 7:
                st.subheader(interface_text("문법 1 · 에", "Grammar 1 · 에"))
            elif current_unit["number"] == 8:
                st.subheader(interface_text("문법 1 · 안", "Grammar 1 · 안"))
            elif current_unit["number"] == 9:
                st.subheader(interface_text("문법 1 · 에서", "Grammar 1 · 에서"))
            elif current_unit["number"] == 10:
                st.subheader("문법 1 · -(으)ㄹ까요?")
            else:
                st.subheader(section["grammar1"])
            if current_unit["number"] == 1:
                st.caption(interface_text("2단계 · 명사 뒤에 붙여 사람이나 사물을 설명해요.", "Step 2 · Use these forms after a noun to identify or describe a person or thing."))
                ending_columns = st.columns(2)
                with ending_columns[0]:
                    render_learning_info("**받침 있음 → 이에요**\n\n회사원 → 회사원이에요.\n\n책 → 책이에요.", icon=":material/spellcheck:")
                with ending_columns[1]:
                    render_learning_info("**받침 없음 → 예요**\n\n의사 → 의사예요.\n\n모자 → 모자예요.", icon=":material/spellcheck:")
                st.space("small")
                st.markdown(interface_text("**대화로 먼저 익혀 보세요**", "**Start with the model dialogues**"))
                dialogue_columns = st.columns(2)
                with dialogue_columns[0]:
                    with st.container(border=True, height=250, key="unit1_grammar_dialogue_g1_office"):
                        office_image, office_dialogue = st.columns([1.5, 2.5], gap=None, vertical_alignment="center")
                        with office_image:
                            render_unit1_study_image("office-worker.png")
                        with office_dialogue:
                            render_learning_markdown("가: 회사원이에요?  \n\n나: 네. 회사원이에요.")
                with dialogue_columns[1]:
                    with st.container(border=True, height=250, key="unit1_grammar_dialogue_g1_hat"):
                        hat_image, hat_dialogue = st.columns([1.5, 2.5], gap=None, vertical_alignment="center")
                        with hat_image:
                            render_unit1_study_image("hat.png")
                        with hat_dialogue:
                            render_learning_markdown("가: 모자예요?  \n\n나: 네. 모자예요.")
                st.divider()
            if current_unit["number"] == 2:
                render_learning_info(
                    interface_text("명사 뒤에서 문장의 주어를 나타내요. 받침이 있으면 ‘이’, 없으면 ‘가’를 사용해요.", "Use 이/가 after a noun to mark the subject. Use 이 after a final consonant and 가 after a vowel."),
                    icon=":material/school:",
                )
                particle_columns = st.columns(2)
                with particle_columns[0]:
                    render_learning_info(interface_text("**받침 있음 → 이**\n\n이름 → 이름이 뭐예요?", "**Final consonant → 이**\n\n이름 → 이름이 뭐예요?"), icon=":material/spellcheck:")
                with particle_columns[1]:
                    render_learning_info(interface_text("**받침 없음 → 가**\n\n전화번호 → 전화번호가 뭐예요?", "**No final consonant → 가**\n\n전화번호 → 전화번호가 뭐예요?"), icon=":material/spellcheck:")
                st.space("small")
                st.markdown(interface_text("**대화로 먼저 익혀 보세요**", "**Start with the model dialogues**"))
                unit2_g1_dialogues = st.columns(2)
                with unit2_g1_dialogues[0]:
                    with st.container(border=True, height=210):
                        dialogue_image, dialogue_text = st.columns([1, 2.2], gap=None, vertical_alignment="center")
                        with dialogue_image:
                            st.image(
                                fit_image_to_canvas(
                                    Path(__file__).with_name("assets") / "people" / "younger-sister.png",
                                    canvas_size=(110, 145),
                                    image_size=(95, 130),
                                ),
                                width=110,
                            )
                        with dialogue_text:
                            render_learning_markdown("가: 이름이 뭐예요?  \n나: 마리예요.")
                with unit2_g1_dialogues[1]:
                    with st.container(border=True, height=210):
                        dialogue_image, dialogue_text = st.columns([1, 2.2], gap=None, vertical_alignment="center")
                        with dialogue_image:
                            st.image(
                                fit_image_to_canvas(
                                    Path(__file__).with_name("assets") / "people" / "phone-person.png",
                                    canvas_size=(110, 145),
                                    image_size=(95, 130),
                                ),
                                width=110,
                            )
                        with dialogue_text:
                            render_learning_markdown("가: 전화번호가 뭐예요?  \n나: 010-1213-7505예요.")
                st.space("small")
                st.markdown(interface_text("**1. 그림을 보고 질문에 알맞은 대답을 선택해 보세요.**", "**1. Look at each picture and choose the correct answer**"))
                st.caption(interface_text("그림 속 사람의 이름과 전화번호를 보고 알맞은 대답을 골라 보세요.", "Use the name and information in each picture to choose the correct Korean answer."))
                unit2_g1_picture_exercises = [
                    ("마리", "마리 씨가 누구예요?", "제가 마리예요.", "younger-sister.png", ["제가 마리예요.", "제가 유진이에요.", "제가 주노예요."]),
                    ("주노 씨 동생", "주노 씨 동생이 누구예요?", "제가 주노 씨 동생이에요.", "student.png", ["제가 주노 씨 동생이에요.", "제가 마리 씨 친구예요.", "제가 요리사예요."]),
                    ("마리 씨 친구", "마리 씨 친구가 누구예요?", "제가 마리 씨 친구예요.", "vietnam.png", ["제가 마리 씨 친구예요.", "제가 주노 씨 동생이에요.", "제가 요리사예요."]),
                    ("요리사", "요리사가 누구예요?", "제가 요리사예요.", "cook.png", ["제가 요리사예요.", "제가 마리예요.", "제가 주노 씨 동생이에요."]),
                ]
                unit2_g1_picture_answers = []
                for index, (label, question, answer, image_name, choices) in enumerate(unit2_g1_picture_exercises):
                    with st.container(border=True):
                        picture_col, answer_col = st.columns([1, 2.5], vertical_alignment="center")
                        with picture_col:
                            st.image(
                                fit_image_to_canvas(
                                    Path(__file__).with_name("assets") / "people" / image_name,
                                    canvas_size=(140, 155),
                                    image_size=(100, 120) if index == 0 else (125, 145),
                                ),
                                width=140,
                            )
                            st.caption(label)
                        with answer_col:
                            st.markdown(f"**가:** {question}")
                            selected = st.radio("대답", choices, key=f"unit2_g1_picture_answer_{index}", horizontal=True, index=None, label_visibility="collapsed")
                            unit2_g1_picture_answers.append(selected == answer)
                unit2_g1_picture_ready = all(
                    st.session_state.get(f"unit2_g1_picture_answer_{index}") is not None
                    for index in range(len(unit2_g1_picture_exercises))
                )
                unit2_g1_current_answers = [
                    st.session_state.get(f"unit2_g1_picture_answer_{index}")
                    for index in range(len(unit2_g1_picture_exercises))
                ]
                unit2_g1_feedback_current = (
                    st.session_state.get("unit2_g1_picture_checked_answers") == unit2_g1_current_answers
                )
                if not unit2_g1_feedback_current:
                    st.session_state["unit2_g1_picture_feedback_visible"] = False
                unit2_g1_feedback_visible = (
                    st.session_state.get("unit2_g1_picture_feedback_visible", False)
                    and unit2_g1_feedback_current
                )
                if st.button(
                    interface_text("1번 설명 닫기", "Hide Activity 1 explanation")
                    if unit2_g1_feedback_visible
                    else interface_text("1번 정답 확인", "Check Activity 1 answers"),
                    key="unit2_g1_picture_check",
                    type="primary",
                    disabled=not unit2_g1_picture_ready,
                ):
                    if unit2_g1_feedback_visible:
                        st.session_state["unit2_g1_picture_feedback_visible"] = False
                    else:
                        st.session_state["unit2_g1_picture_checked_answers"] = unit2_g1_current_answers
                        st.session_state["unit2_g1_picture_feedback_visible"] = True
                        st.session_state["unit2_g1_picture_passed"] = all(unit2_g1_picture_answers)
                    st.rerun()
                if unit2_g1_feedback_visible:
                    if all(unit2_g1_picture_answers):
                        render_learning_success(interface_text("네 대화를 모두 정확하게 완성했어요.", "You completed all four dialogues correctly."), icon=":material/check_circle:")
                    else:
                        render_learning_warning(interface_text(f"{sum(unit2_g1_picture_answers)}/4개가 맞아요. 그림의 이름과 정보를 다시 확인해 보세요.", f"{sum(unit2_g1_picture_answers)}/4 are correct. Check the names and information in the pictures again."))
                st.divider()
            if current_unit["number"] == 3:
                render_unit3_grammar1_intro()
            if current_unit["number"] == 4:
                render_unit4_grammar1_intro()
                st.markdown(interface_text("### 문법 1 마무리 확인", "### Final Grammar 1 check"))
                st.caption(interface_text("앞에서 익힌 활용을 한 문장씩 확인하세요. 네 문장을 모두 맞히면 완료할 수 있어요.", "Check the conjugations one sentence at a time. Answer all four correctly to continue."))
            if current_unit["number"] == 5:
                render_unit5_grammar1_intro()
                st.markdown("### 문법 1 마무리 확인")
                st.caption("앞에서 익힌 ‘장소에 가요’를 네 문장으로 확인하세요.")
            if current_unit["number"] == 6:
                render_unit6_grammar1_intro()
                st.markdown("### 문법 1 마무리 확인")
                st.caption("앞에서 익힌 단위 명사를 네 문장으로 확인하세요.")
            if current_unit["number"] == 7:
                render_unit7_grammar1()
                st.markdown(interface_text("### 문법 1 마무리 확인", "### Final Grammar 1 check"))
                st.caption(interface_text("앞에서 익힌 시간·날짜의 ‘에’를 네 문장으로 확인하세요.", "Check 에 after time and date expressions in four sentences."))
            if current_unit["number"] == 8:
                render_unit8_grammar1()
                st.markdown(interface_text("### 문법 1 마무리 확인", "### Final Grammar 1 check"))
                st.caption(interface_text("앞에서 익힌 ‘안’ 표현을 네 문장으로 확인하세요.", "Check the 안 pattern in four Korean sentences."))
            if current_unit["number"] == 9:
                render_unit9_grammar1()
                st.markdown(interface_text("### 문법 1 마무리 확인", "### Final Grammar 1 check"))
                st.caption(interface_text("앞에서 익힌 장소의 ‘에서’를 네 문장으로 확인하세요.", "Check 에서 after activity locations in four Korean sentences."))
            if current_unit["number"] == 10:
                render_unit10_grammar1()
                st.markdown("### 문법 1 마무리 확인")
                st.caption("앞에서 익힌 ‘-(으)ㄹ까요?’ 제안 표현을 네 문장으로 확인하세요.")
            grammar_questions = GRAMMAR1_SEQUENCE_BANK[current_unit["number"]]
            grammar_index_key = f"grammar1_index_{current_unit['number']}"
            grammar_result_key = f"grammar1_result_{current_unit['number']}"
            st.session_state.setdefault(grammar_index_key, 0)
            # 1단원은 교재의 그림 활동 1·2가 문법 1 탭에서 바로 보이도록 엽니다.
            if current_unit["number"] == 1 and not st.session_state.get("unit1_textbook_activities_opened", False):
                st.session_state[grammar_index_key] = len(grammar_questions) - 1
                st.session_state[grammar_result_key] = True
                st.session_state["unit1_textbook_activities_opened"] = True
            grammar_index = min(st.session_state[grammar_index_key], len(grammar_questions) - 1)
            sentence, options, answer, explanation = grammar_questions[grammar_index]
            if current_unit["number"] != 1:
                visible_sentence = sentence.replace("__", "＿＿＿＿")
                st.progress((grammar_index + 1) / len(grammar_questions), text=f"{interface_text('문법 1 연습', 'Grammar 1 practice')} {grammar_index + 1}/{len(grammar_questions)}")
                st.caption(interface_text("한 문장씩 정답을 확인하고 다음 문장으로 넘어가세요.", "Check each answer before moving to the next Korean sentence."))
                st.markdown(f"### {visible_sentence}")
                st.caption(f"{interface_text('완성 문장 예시', 'Completed sentence example')}: {section['example']}")
                grammar_choice = st.radio(
                    interface_text("빈칸에 알맞은 말을 선택하세요", "Choose the correct Korean expression for the blank"),
                    options,
                    key=f"grammar_quiz_choice_{current_unit['number']}_{grammar_index}",
                    horizontal=True,
                    disabled=not grammar1_unlocked,
                    index=None,
                    label_visibility="collapsed",
                )
                if not grammar1_unlocked:
                    render_learning_lock(interface_text("먼저 1단계 어휘와 표현을 완료해 주세요.", "Complete Step 1: Vocabulary and Expressions first."))
                grammar_checked_key = f"grammar1_checked_choice_{current_unit['number']}_{grammar_index}"
                grammar_feedback_key = f"grammar1_feedback_visible_{current_unit['number']}_{grammar_index}"
                grammar_feedback_current = st.session_state.get(grammar_checked_key) == grammar_choice
                if not grammar_feedback_current:
                    st.session_state[grammar_feedback_key] = False
                grammar_feedback_visible = st.session_state.get(grammar_feedback_key, False) and grammar_feedback_current
                grammar_rule = GRAMMAR_RULES.get(section["grammar1"], "이 표현은 문장에서 어떤 역할을 하는지 예문과 함께 확인해 보세요.")
                unit2_explanations_en = {
                    "‘전화번호’는 받침이 없으므로 ‘가’를 사용해요.": "전화번호 has no final consonant, so use 가.",
                    "‘번호’는 받침이 없으므로 ‘가’를 사용해요.": "번호 has no final consonant, so use 가.",
                    "‘민수 씨’가 행동의 주체이므로 ‘가’를 사용해요.": "민수 씨 is the subject performing the action, so use 가.",
                    "받침이 있는 ‘수진’ 뒤에는 ‘이’를 사용해요.": "수진 ends in a final consonant, so use 이.",
                }
                explanation_support = unit2_explanations_en.get(explanation, explanation) if english_support_enabled() and current_unit["number"] == 2 else explanation
                if st.button(
                    interface_text("설명 닫기", "Hide explanation")
                    if grammar_feedback_visible
                    else interface_text("정답 확인", "Check answer"),
                    key=f"grammar_check_{current_unit['number']}_{grammar_index}",
                    disabled=not grammar1_unlocked or grammar_choice is None,
                ):
                    if grammar_feedback_visible:
                        st.session_state[grammar_feedback_key] = False
                    else:
                        st.session_state[grammar_checked_key] = grammar_choice
                        st.session_state[grammar_result_key] = grammar_choice == answer
                        st.session_state[grammar_feedback_key] = True
                    st.rerun()
                if grammar_feedback_visible:
                    if grammar_choice == answer:
                        render_learning_success(interface_text(f"정답이에요! ‘{grammar_choice}’가 맞아요.", f"Correct! ‘{grammar_choice}’ is the right answer."))
                        render_learning_info(interface_text(f"왜 정답일까요? {explanation}", f"Why? {explanation_support}"), icon=":material/lightbulb:")
                    else:
                        completed_sentence = sentence.replace("__", answer)
                        render_learning_warning(interface_text("아직 정답이 아니에요. 설명을 읽고 다시 선택해 보세요.", "Not correct yet. Read the explanation and choose again."))
                        choice_particle = subject_particle(grammar_choice)
                        render_learning_info(interface_text(f"왜 ‘{grammar_choice}’{choice_particle} 아닐까요? {explanation}", f"Why not ‘{grammar_choice}’? {explanation_support}"), icon=":material/lightbulb:")
                        st.markdown(f"**{interface_text('다시 완성해 보세요', 'Complete it again')}:** {completed_sentence}")
                    with st.expander(interface_text("문법 원리 보기", "View grammar rule"), icon=":material/school:"):
                        st.write(GRAMMAR_RULES_EN.get(section["grammar1"], grammar_rule) if english_support_enabled() else grammar_rule)
                        st.caption(interface_text("초급에서는 규칙을 짧게 익히고, 같은 형태의 문장을 여러 번 소리 내어 말해 보세요.", "Learn the rule briefly, then say several Korean sentences with the same pattern aloud."))
            grammar_result_is_current = (
                st.session_state.get(grammar_result_key, False)
                and st.session_state.get(f"grammar1_checked_choice_{current_unit['number']}_{grammar_index}") == grammar_choice
            ) if current_unit["number"] != 1 else st.session_state.get(grammar_result_key, False)
            if grammar_result_is_current:
                if grammar_index < len(grammar_questions) - 1:
                    if st.button(interface_text("다음 문장 →", "Next sentence →"), key=f"grammar_next_{current_unit['number']}_{grammar_index}", type="primary"):
                        st.session_state[grammar_index_key] = grammar_index + 1
                        st.session_state[grammar_result_key] = False
                        st.rerun()
                else:
                    if current_unit["number"] != 1:
                        render_learning_success(interface_text("문법 1의 네 문장을 모두 정확하게 풀었어요.", "You answered all four Grammar 1 sentences correctly."), icon=":material/check_circle:")
                    if current_unit["number"] == 1:
                        st.divider()
                        st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at each picture and complete the dialogue"))
                        st.caption(interface_text("그림과 질문을 확인한 뒤 ‘이에요/예요’를 사용해 대답하세요.", "Check the picture and question, then answer using 이에요 or 예요."))
                        picture_exercises = [
                            ("한복", "hanbok.png", "한복이에요?", ["네. 한복이에요.", "네. 한복예요."], "네. 한복이에요.", interface_text("‘한복’의 마지막 글자 ‘복’에는 ㄱ 받침이 있으므로 ‘이에요’를 사용해요.", "The final syllable 복 in 한복 has the final consonant ㄱ, so use 이에요.")),
                            ("커피", "coffee.png", "커피예요?", ["네. 커피이에요.", "네. 커피예요."], "네. 커피예요.", interface_text("‘커피’의 마지막 글자 ‘피’에는 받침이 없으므로 ‘예요’를 사용해요.", "The final syllable 피 in 커피 has no final consonant, so use 예요.")),
                            ("동생", "younger-sister.png", "언니예요?", ["아니요. 동생이에요.", "네. 언니예요."], "아니요. 동생이에요.", interface_text("그림은 언니가 아니라 동생이에요. ‘동생’의 ‘생’에는 ㅇ 받침이 있어서 ‘이에요’를 사용해요.", "The picture shows 동생, not 언니. The final syllable 생 has the final consonant ㅇ, so use 이에요.")),
                            ("미국 사람", "american.png", "프랑스 사람이에요?", ["네. 프랑스 사람이에요.", "아니요. 미국 사람이에요."], "아니요. 미국 사람이에요.", interface_text("그림은 프랑스 사람이 아니라 미국 사람이에요. ‘사람’의 ‘람’에는 ㅁ 받침이 있어서 ‘이에요’를 사용해요.", "The picture shows 미국 사람, not 프랑스 사람. The final syllable 람 has the final consonant ㅁ, so use 이에요.")),
                        ]
                        picture_answers = []
                        selected_picture_answers = []
                        for index, (picture_label, image_name, question, choices, correct_answer, explanation) in enumerate(picture_exercises):
                            with st.container(border=True):
                                picture_column, dialogue_column = st.columns([1, 3], gap="small", vertical_alignment="center")
                                with picture_column:
                                    render_unit1_study_image(image_name)
                                    st.markdown(
                                        f"<div style='width:180px;text-align:right;margin-top:-1.15rem;padding-right:.2rem;box-sizing:border-box;line-height:1;font-size:.9rem;font-weight:750;color:#d9e2ef'>{picture_label}</div>",
                                        unsafe_allow_html=True,
                                    )
                                with dialogue_column:
                                    st.markdown(f"**가:** {question}")
                                    selected_answer = st.radio(
                                        f"{index + 1}번 대답",
                                        choices,
                                        key=f"unit1_grammar1_picture_{index}",
                                        horizontal=True,
                                        index=None,
                                        label_visibility="collapsed",
                                    )
                                    picture_answers.append(selected_answer == correct_answer)
                                    selected_picture_answers.append(selected_answer)
                        picture_feedback_visible = bool(st.session_state.get("unit1_grammar1_picture_feedback"))
                        picture_feedback_button = interface_text("1번 활동 피드백 접기", "Hide Activity 1 feedback") if picture_feedback_visible else interface_text("1번 활동 정답 확인", "Check Activity 1 answers")
                        picture_answers_ready = all(answer is not None for answer in selected_picture_answers)
                        if st.button(picture_feedback_button, key="unit1_grammar1_picture_check", type="primary", disabled=not picture_answers_ready and not picture_feedback_visible):
                            if picture_feedback_visible:
                                st.session_state.pop("unit1_grammar1_picture_feedback", None)
                            else:
                                st.session_state["unit1_grammar1_picture_feedback"] = selected_picture_answers
                                if all(picture_answers):
                                    st.session_state["unit1_grammar1_picture_passed"] = True
                                    render_learning_success(interface_text("네 개의 대화를 모두 정확하게 완성했어요!", "You completed all four dialogues correctly!"), icon=":material/check_circle:")
                                else:
                                    st.session_state["unit1_grammar1_picture_passed"] = False
                                    render_learning_warning(interface_text(f"{sum(picture_answers)}/4개가 맞아요. 아래에서 문항별 설명을 확인해 보세요.", f"{sum(picture_answers)}/4 are correct. Review the feedback for each question below."))
                            st.rerun()
                        saved_picture_feedback = st.session_state.get("unit1_grammar1_picture_feedback")
                        if saved_picture_feedback:
                            st.markdown(interface_text("#### 문항별 피드백", "#### Feedback by Question"))
                            for index, (exercise, selected_answer) in enumerate(zip(picture_exercises, saved_picture_feedback)):
                                picture_label, image_name, question, choices, correct_answer, explanation = exercise
                                if selected_answer == correct_answer:
                                    feedback = interface_text(f"{index + 1}번 · {picture_label}: ‘{selected_answer}’ — 정답이에요. {explanation}", f"Question {index + 1} · {picture_label}: ‘{selected_answer}’ is correct. {explanation}")
                                    render_learning_success(feedback)
                                else:
                                    feedback = interface_text(f"{index + 1}번 · {picture_label}: 선택한 답은 ‘{selected_answer}’예요. 정답은 ‘{correct_answer}’입니다. {explanation}", f"Question {index + 1} · {picture_label}: You chose ‘{selected_answer}’. The correct answer is ‘{correct_answer}’. {explanation}")
                                    render_learning_error(feedback)

                        st.divider()
                        st.markdown(interface_text("### 2. 사진을 보고 이름과 나라를 입력해 대화를 완성해 보세요.", "### 2. Look at the photo and complete the dialogue with a name and country"))
                        st.caption(interface_text("이름과 나라를 선택하면 알맞은 소개 대화가 완성됩니다. 완성된 대화를 소리 내어 읽어 보세요.", "Enter a name and choose a country, then read the completed Korean dialogue aloud."))
                        friend_photo, friend_form = st.columns([1, 2], vertical_alignment="center")
                        with friend_photo:
                            render_unit1_study_image("vietnam.png", canvas_size=(220, 220), image_size=(205, 210))
                        with friend_form:
                            exercise_friend_name = st.text_input(interface_text("친구 이름", "Friend’s name"), placeholder=interface_text("예: 민", "Example: Min"), key="unit1_grammar1_friend_name")
                            exercise_friend_country = st.selectbox(
                                interface_text("친구의 나라", "Friend’s country"),
                                ["태국", "베트남", "한국", "미국", "프랑스", "중국", "일본"],
                                key="unit1_grammar1_friend_country",
                            )
                        if exercise_friend_name.strip():
                            name_ending = "이에요" if subject_particle(exercise_friend_name.strip()) == "이" else "예요"
                            render_learning_success(
                                f"가: 누구예요?\n\n나: {exercise_friend_name.strip()}{name_ending}. 제 친구예요.\n\n가: 한국 사람이에요?\n\n나: 아니요. {exercise_friend_country} 사람이에요.",
                                icon=":material/forum:",
                            )
                        st.divider()
                        final_sentence, final_options, final_answer, final_explanation = grammar_questions[-1]
                        final_options = [option for option in ["이에요", "예요"] if option in final_options] + [
                            option for option in final_options if option not in {"이에요", "예요"}
                        ]
                        st.progress(1.0, text=f"{interface_text('문법 1 연습', 'Grammar 1 practice')} {len(grammar_questions)}/{len(grammar_questions)}")
                        st.caption(interface_text("한 문장씩 정답을 확인하고 다음 문장으로 넘어가세요.", "Check each answer before moving to the next Korean sentence."))
                        st.markdown(f"### {final_sentence.replace('__', '＿＿＿＿')}")
                        st.caption(f"{interface_text('완성 문장 예시', 'Completed sentence example')}: {section['example']}")
                        unit1_final_choice = st.radio(
                            "빈칸에 알맞은 말을 선택하세요",
                            final_options,
                            key="unit1_grammar1_final_choice",
                            horizontal=True,
                            index=None,
                            label_visibility="collapsed",
                        )
                        if st.button(interface_text("정답 확인", "Check answer"), key="unit1_grammar1_final_check", disabled=unit1_final_choice is None):
                            st.session_state["unit1_grammar1_final_checked_choice"] = unit1_final_choice
                            final_explanation_support = interface_text(final_explanation, "선생님 ends in the final consonant ㅁ, so use 이에요.")
                            if unit1_final_choice == final_answer:
                                st.session_state["unit1_grammar1_final_passed"] = True
                                render_learning_success(interface_text(f"정답이에요! ‘{unit1_final_choice}’가 맞아요.", f"Correct! ‘{unit1_final_choice}’ is the right answer."))
                                render_learning_info(interface_text(f"왜 정답일까요? {final_explanation}", f"Why? {final_explanation_support}"), icon=":material/lightbulb:")
                            else:
                                st.session_state["unit1_grammar1_final_passed"] = False
                                render_learning_warning(interface_text(f"정답은 ‘{final_answer}’입니다.", f"The correct answer is ‘{final_answer}’."))
                                render_learning_info(final_explanation_support, icon=":material/lightbulb:")
                        unit1_picture_answers_current = (
                            st.session_state.get("unit1_grammar1_picture_feedback") == selected_picture_answers
                        )
                        unit1_final_answer_current = st.session_state.get("unit1_grammar1_final_checked_choice") == unit1_final_choice
                        unit1_grammar1_ready = (
                            st.session_state.get("unit1_grammar1_picture_passed", False)
                            and unit1_picture_answers_current
                            and bool(exercise_friend_name.strip())
                            and st.session_state.get("unit1_grammar1_final_passed", False)
                            and unit1_final_answer_current
                        )
                        st.checkbox(
                            interface_text("그림 대화, 친구 소개와 마지막 문법 확인을 모두 마쳤어요", "I completed the picture dialogues, friend introduction, and final grammar check"),
                            key="grammar1_done_1",
                            disabled=not unit1_grammar1_ready,
                        )
                        if not unit1_grammar1_ready:
                            st.caption(interface_text("위의 세 활동을 모두 완료하면 문법 1 완료 표시가 열립니다.", "Complete all three activities above to finish Grammar 1."))
                    else:
                        if current_unit["number"] == 2:
                            unit2_g1_current_answers = [
                                st.session_state.get(f"unit2_g1_picture_answer_{index}")
                                for index in range(4)
                            ]
                            unit2_grammar1_ready = (
                                st.session_state.get("unit2_g1_picture_passed", False)
                                and st.session_state.get("unit2_g1_picture_checked_answers") == unit2_g1_current_answers
                            )
                            if not unit2_grammar1_ready:
                                st.session_state.pop("grammar1_done_2", None)
                            st.checkbox(
                                interface_text("그림 대화와 네 문장 확인을 모두 마쳤어요", "I completed the picture dialogues and all four Grammar 1 sentences"),
                                key="grammar1_done_2",
                                disabled=not unit2_grammar1_ready,
                            )
                            if not unit2_grammar1_ready:
                                st.caption(interface_text("먼저 그림 대화 네 문제를 모두 맞혀 주세요.", "Answer all four picture-dialogue questions correctly first."))
                        elif current_unit["number"] == 4:
                            unit4_picture_done = st.session_state.get("unit4_grammar1_picture_completed", False)
                            unit4_speaking_done = st.session_state.get("unit4_grammar1_speaking_completed", False)
                            unit4_sequence_done = grammar_index == len(grammar_questions) - 1 and grammar_result_is_current
                            unit4_grammar1_ready = unit4_picture_done and unit4_speaking_done and unit4_sequence_done
                            if not unit4_grammar1_ready:
                                st.session_state.pop("grammar1_done_4", None)
                            st.checkbox(
                                interface_text("그림 대화와 말하기, 네 문장 확인을 모두 마쳤어요", "I completed the picture dialogues, speaking practice, and four-sentence check"),
                                key="grammar1_done_4",
                                disabled=not unit4_grammar1_ready,
                            )
                            if not unit4_picture_done:
                                st.caption(interface_text("먼저 1번 그림 대화 네 문제를 모두 맞혀 주세요.", "First, answer all four picture-dialogue questions correctly."))
                            elif not unit4_speaking_done:
                                st.caption(interface_text("2번 대화를 두 번 읽으면 문법 1을 완료할 수 있어요.", "Read the Activity 2 Korean dialogue aloud twice to finish Grammar 1."))
                            elif not unit4_sequence_done:
                                st.caption(interface_text("마무리 확인의 네 문장을 모두 맞혀 주세요.", "Answer all four sentences in the final check correctly."))
                        elif current_unit["number"] == 3:
                            unit3_activities_done = st.session_state.get("unit3_grammar1_activities_completed", False)
                            if not unit3_activities_done:
                                st.session_state.pop("grammar1_done_3", None)
                            st.checkbox(
                                interface_text("두 활동의 여섯 문제를 모두 확인하고 문법 1을 마쳤어요", "I checked all six questions in both activities and finished Grammar 1"),
                                key="grammar1_done_3",
                                disabled=not unit3_activities_done,
                            )
                            if not unit3_activities_done:
                                st.caption(interface_text("먼저 1번과 2번 활동을 모두 정확하게 완료해 주세요.", "Complete both activities correctly before finishing Grammar 1."))
                        elif current_unit["number"] == 5:
                            unit5_activities_done = st.session_state.get("unit5_grammar1_activities_completed", False)
                            unit5_sequence_done = grammar_index == len(grammar_questions) - 1 and grammar_result_is_current
                            unit5_activities_done = unit5_activities_done and unit5_sequence_done
                            if not unit5_activities_done:
                                st.session_state.pop("grammar1_done_5", None)
                            st.checkbox(
                                "1번과 2번 활동, 네 문장 확인을 모두 마쳤어요",
                                key="grammar1_done_5",
                                disabled=not unit5_activities_done,
                            )
                            if not unit5_activities_done:
                                st.caption(interface_text("먼저 1번 그림 문제와 2번 말하기, 마무리 네 문장을 완료해 주세요.", "Complete the picture questions, speaking practice, and final four-sentence check first."))
                        elif current_unit["number"] == 6:
                            unit6_activities_done = st.session_state.get("unit6_grammar1_activities_completed", False)
                            unit6_sequence_done = grammar_index == len(grammar_questions) - 1 and grammar_result_is_current
                            unit6_activities_done = unit6_activities_done and unit6_sequence_done
                            if not unit6_activities_done:
                                st.session_state.pop("grammar1_done_6", None)
                            st.checkbox(
                                interface_text("1번과 2번 활동, 네 문장 확인을 모두 마쳤어요", "I completed both activities and checked the four Korean sentences"),
                                key="grammar1_done_6",
                                disabled=not unit6_activities_done,
                            )
                            if not unit6_activities_done:
                                st.caption(interface_text("먼저 1번 단위 명사 문제와 2번 말하기, 마무리 네 문장을 완료해 주세요.", "Complete the counter questions, speaking practice, and final four-sentence check first."))
                        elif current_unit["number"] in (7, 8, 9, 10):
                            unit_number = current_unit["number"]
                            custom_activities_done = st.session_state.get(
                                f"unit{unit_number}_grammar1_activities_completed", False
                            )
                            if unit_number in (7, 8, 9):
                                custom_activities_done = custom_activities_done and grammar_index == len(grammar_questions) - 1 and grammar_result_is_current
                            if not custom_activities_done:
                                st.session_state.pop(f"grammar1_done_{unit_number}", None)
                            st.checkbox(
                                "맞춤 활동과 네 문장 확인을 모두 마쳤어요",
                                key=f"grammar1_done_{unit_number}",
                                disabled=not custom_activities_done,
                            )
                            if not custom_activities_done:
                                st.caption(interface_text("먼저 이 단원의 맞춤 선택·말하기 활동과 마무리 네 문장을 완료해 주세요.", "Complete this unit’s guided selection, speaking practice, and final four-sentence check first."))
                        else:
                            st.checkbox(interface_text("네 문장을 소리 내어 읽고 문법 1을 마쳤어요", "I read the four Korean sentences aloud and finished Grammar 1"), key=f"grammar1_done_{current_unit['number']}")
            else:
                st.caption(interface_text("정답을 맞히면 다음 문장 버튼이 열립니다.", "Answer correctly to unlock the next sentence."))
    with grammar2_tab:
        with st.container(border=True):
            if current_unit["number"] == 1:
                st.subheader(interface_text("문법 2 · 은/는", "Grammar 2 · 은/는"))
            elif current_unit["number"] == 2:
                st.subheader(interface_text("문법 2 · 이/가 아니에요", "Grammar 2 · 이/가 아니에요"))
            elif current_unit["number"] == 3:
                st.subheader(interface_text("문법 2 · 에 있다/없다", "Grammar 2 · 에 있다/없다"))
            elif current_unit["number"] == 4:
                st.subheader(interface_text("문법 2 · 을/를", "Grammar 2 · 을/를"))
            elif current_unit["number"] == 5:
                st.subheader("문법 2 · 하고")
            elif current_unit["number"] == 6:
                st.subheader(interface_text("문법 2 · -(으)세요", "Grammar 2 · -(으)세요"))
            elif current_unit["number"] == 7:
                st.subheader(interface_text("문법 2 · 몇 시예요?", "Grammar 2 · 몇 시예요?"))
            elif current_unit["number"] == 8:
                st.subheader(interface_text("문법 2 · ㅂ 불규칙", "Grammar 2 · ㅂ irregular verbs"))
            elif current_unit["number"] == 9:
                st.subheader(interface_text("문법 2 · -았어요/-었어요", "Grammar 2 · -았어요/-었어요"))
            elif current_unit["number"] == 10:
                st.subheader("문법 2 · -(으)러 가다")
            else:
                st.markdown(f"<div class='eyebrow'>Grammar 2 · 오늘의 핵심</div><h2>{section['grammar2']}</h2>", unsafe_allow_html=True)
            grammar2_rule = GRAMMAR_RULES.get(section["grammar2"], "이 표현은 문장에서 어떤 역할을 하는지 예문과 함께 확인해 보세요.")
            if current_unit["number"] not in (4, 5, 6, 7, 8, 9, 10):
                render_learning_info(grammar2_rule, icon=":material/school:")
            if current_unit["number"] == 2:
                negative_columns = st.columns(2)
                with negative_columns[0]:
                    render_learning_info(interface_text("**받침 있음 → 이 아니에요**\n\n학생 → 학생이 아니에요.", "**Final consonant → 이 아니에요**\n\n학생 → 학생이 아니에요."), icon=":material/spellcheck:")
                with negative_columns[1]:
                    render_learning_info(interface_text("**받침 없음 → 가 아니에요**\n\n친구 → 친구가 아니에요.", "**No final consonant → 가 아니에요**\n\n친구 → 친구가 아니에요."), icon=":material/spellcheck:")
                st.space("small")
                st.markdown(interface_text("**핵심 예문**", "**Key Examples**"))
                example_columns = st.columns(2)
                with example_columns[0]:
                    with st.container(border=True):
                        st.image(Path(__file__).with_name("assets") / "people" / "brothers.png", width=180)
                        render_learning_markdown("**가:** 동생이에요?  \n**나:** 아니요. 동생이 아니에요. 형이에요.")
                with example_columns[1]:
                    with st.container(border=True):
                        st.image(Path(__file__).with_name("assets") / "people" / "classrooms-203-204.png", width=180)
                        render_learning_markdown("**가:** 교실이 203호예요?  \n**나:** 아니요. 203호가 아니에요. 204호예요.")
            if current_unit["number"] == 1:
                st.caption(interface_text("명사 뒤에 붙여 이야기의 주제를 나타내요.", "Use 은/는 after a noun to mark the topic of the sentence."))
                particle_columns = st.columns(2)
                with particle_columns[0]:
                    render_learning_info("**받침 있음 → 은**\n\n동생 → 동생은 대학생이에요.\n\n아버지 → 아버지는 요리사예요.", icon=":material/person:")
                with particle_columns[1]:
                    render_learning_info("**받침 없음 → 는**\n\n저 → 저는 한국 사람이에요.\n\n마리 → 마리는 회사원이에요.", icon=":material/person:")
                st.space("small")
                st.markdown(interface_text("**대화로 먼저 익혀 보세요**", "**Start with the model dialogues**"))
                grammar2_dialogues = st.columns(2)
                with grammar2_dialogues[0]:
                    with st.container(border=True, height=250, key="unit1_grammar_dialogue_g2_student"):
                        student_image, student_dialogue = st.columns([1.5, 2.5], gap=None, vertical_alignment="center")
                        with student_image:
                            render_unit1_study_image("student.png")
                        with student_dialogue:
                            render_learning_markdown("가: 우진 씨 동생은 대학생이에요?  \n\n나: 네. 제 동생은 대학생이에요.")
                with grammar2_dialogues[1]:
                    with st.container(border=True, height=250, key="unit1_grammar_dialogue_g2_cook"):
                        cook_image, cook_dialogue = st.columns([1.5, 2.5], gap=None, vertical_alignment="center")
                        with cook_image:
                            render_unit1_study_image("cook.png")
                        with cook_dialogue:
                            render_learning_markdown("가: 아버지는 요리사예요?  \n\n나: 네. 아버지는 요리사예요.")
                st.divider()
                st.markdown(interface_text("### 1. 그림을 보고 대화를 완성해 보세요.", "### 1. Look at each picture and complete the dialogue"))
                st.caption(interface_text("인물과 직업을 확인하고 ‘은/는’을 사용해 알맞은 문장을 선택하세요.", "Check the person and occupation, then choose the correct Korean sentence using 은 or 는."))
                people_questions = [
                    ("저", "office-worker.png", "회사원", "저는 회사원이에요.", interface_text("‘저’는 받침이 없으므로 ‘는’을 사용해요.", "저 has no final consonant, so use 는.")),
                    ("동생", "teacher.png", "선생님", "동생은 선생님이에요.", interface_text("‘동생’의 마지막 글자 ‘생’에는 ㅇ 받침이 있으므로 ‘은’을 사용해요.", "The final syllable 생 in 동생 has the final consonant ㅇ, so use 은.")),
                    ("어머니", "doctor.png", "의사", "어머니는 의사예요.", interface_text("‘어머니’는 받침이 없으므로 ‘는’을 사용해요.", "어머니 has no final consonant, so use 는.")),
                    ("형", "singer.png", "가수", "형은 가수예요.", interface_text("‘형’에는 ㅇ 받침이 있으므로 ‘은’을 사용해요.", "형 has the final consonant ㅇ, so use 은.")),
                ]
                unit1_g2_answers = []
                selected_g2_answers = []
                for index, (person, image_name, job, answer_sentence, answer_explanation) in enumerate(people_questions):
                    with st.container(border=True):
                        question_column, answer_column = st.columns([1, 3], gap="small", vertical_alignment="center")
                        with question_column:
                            render_unit1_study_image(image_name)
                            st.markdown(f"<div style='width:180px;text-align:right;margin-top:-1.15rem;font-weight:750'>{person} · {job}</div>", unsafe_allow_html=True)
                        with answer_column:
                            if "은" in answer_sentence:
                                neun_sentence = answer_sentence.replace("은", "는", 1)
                                eun_sentence = answer_sentence
                            else:
                                neun_sentence = answer_sentence
                                eun_sentence = answer_sentence.replace("는", "은", 1)
                            choice = st.radio(
                                f"{person}의 소개",
                                [neun_sentence, eun_sentence],
                                key=f"unit1_grammar2_choice_{index}",
                                horizontal=True,
                                disabled=not grammar2_unlocked,
                                index=None,
                                label_visibility="collapsed",
                            )
                            unit1_g2_answers.append(choice == answer_sentence)
                            selected_g2_answers.append(choice)
                saved_g2_feedback = st.session_state.get("unit1_grammar2_feedback")
                grammar2_button_columns = st.columns([1, 1, 2])
                with grammar2_button_columns[0]:
                    unit1_g2_answers_ready = all(answer is not None for answer in selected_g2_answers)
                    if st.button(interface_text("네 문장 확인", "Check four sentences"), key="unit1_grammar2_check", type="primary", disabled=not grammar2_unlocked or not unit1_g2_answers_ready, width="stretch"):
                        st.session_state["unit1_grammar2_feedback"] = selected_g2_answers
                        saved_g2_feedback = selected_g2_answers
                        st.session_state["unit1_grammar2_quiz_passed"] = all(unit1_g2_answers)
                with grammar2_button_columns[1]:
                    if saved_g2_feedback:
                        st.button(interface_text("확인 닫기", "Close feedback"), key="unit1_grammar2_feedback_close", type="secondary", width="stretch", on_click=clear_session_state_key, args=("unit1_grammar2_feedback",))
                if saved_g2_feedback:
                    if all(unit1_g2_answers):
                        render_learning_success(interface_text("네 문장을 모두 정확히 완성했어요!", "You completed all four sentences correctly!"), icon=":material/check_circle:")
                    else:
                        render_learning_warning(interface_text(f"{sum(unit1_g2_answers)}/4개가 맞아요. 받침이 있으면 ‘은’, 없으면 ‘는’을 사용해 보세요.", f"{sum(unit1_g2_answers)}/4 are correct. Use 은 after a final consonant and 는 after a vowel."))
                    st.markdown(interface_text("#### 문항별 피드백", "#### Feedback by Question"))
                    for index, (question, selected_answer) in enumerate(zip(people_questions, saved_g2_feedback)):
                        person, image_name, job, answer_sentence, answer_explanation = question
                        if selected_answer == answer_sentence:
                            feedback = interface_text(f"{index + 1}번 · {person}: 정답이에요. {answer_explanation}", f"Question {index + 1} · {person}: Correct. {answer_explanation}")
                            render_learning_success(feedback)
                        else:
                            feedback = interface_text(f"{index + 1}번 · {person}: 정답은 ‘{answer_sentence}’입니다. {answer_explanation}", f"Question {index + 1} · {person}: The correct answer is ‘{answer_sentence}’. {answer_explanation}")
                            render_learning_error(feedback)
                st.divider()
                st.markdown(interface_text("### 2. 사진을 보면서 친구를 소개해 보세요.", "### 2. Look at the photo and introduce your friend"))
                st.caption(interface_text("친구의 이름과 직업을 넣어 자연스럽게 소개해 보세요.", "Enter your friend’s name and occupation to create a natural Korean introduction."))
                friend_photo, friend_form = st.columns([1, 2], vertical_alignment="center")
                with friend_photo:
                    render_unit1_study_image("vietnam.png", canvas_size=(220, 220), image_size=(205, 210))
                with friend_form:
                    friend_name = st.text_input(interface_text("친구 이름", "Friend’s name"), placeholder=interface_text("예: 마리", "Example: Mari"), key="unit1_grammar2_friend_name")
                    friend_job = st.selectbox(interface_text("친구 직업", "Friend’s occupation"), ["회사원", "대학생", "선생님", "의사", "요리사", "가수"], key="unit1_grammar2_friend_job")
                friend_particle = "은" if friend_name.strip() and subject_particle(friend_name.strip()) == "이" else "는"
                job_ending = "이에요" if subject_particle(friend_job) == "이" else "예요"
                friend_sentence = f"{friend_name.strip()}{friend_particle} 제 친구예요. {friend_name.strip()}{friend_particle} {friend_job}{job_ending}." if friend_name.strip() else ""
                if friend_sentence:
                    render_learning_success(friend_sentence, icon=":material/groups:")
                unit1_g2_answers_current = saved_g2_feedback == selected_g2_answers
                unit1_g2_ready = (
                    st.session_state.get("unit1_grammar2_quiz_passed", False)
                    and unit1_g2_answers_current
                    and bool(friend_sentence)
                )
                st.checkbox(interface_text("완성한 친구 소개를 소리 내어 읽고 3단계를 마쳤어요", "I read the completed Korean introduction aloud and finished Step 3"), key=f"grammar2_done_{current_unit['number']}", disabled=not unit1_g2_ready)
                if not st.session_state.get("unit1_grammar2_quiz_passed", False) or not unit1_g2_answers_current:
                    st.caption(interface_text("먼저 네 문장을 모두 맞히면 친구 소개 활동을 완료할 수 있습니다.", "Answer all four sentences correctly before completing the friend-introduction activity."))
            elif current_unit["number"] == 2:
                st.space("small")
                st.markdown(interface_text("**1. 질문을 보고 알맞은 대답을 선택하세요.**", "**1. Read each question and choose the correct Korean answer**"))
                unit2_negative_questions = [
                    ("선생님이에요?", ["아니요. 선생님이 아니에요. 학생이에요.", "아니요. 선생님가 아니에요."], "아니요. 선생님이 아니에요. 학생이에요."),
                    ("컴퓨터예요?", ["아니요. 컴퓨터가 아니에요. 텔레비전이에요.", "아니요. 컴퓨터이 아니에요."], "아니요. 컴퓨터가 아니에요. 텔레비전이에요."),
                    ("영화관이 7층이에요?", ["아니요. 7층이 아니에요. 8층이에요.", "네. 7층이에요."], "아니요. 7층이 아니에요. 8층이에요."),
                    ("여자예요?", ["아니요. 여자가 아니에요. 남자예요.", "네. 여자예요."], "아니요. 여자가 아니에요. 남자예요."),
                ]
                unit2_negative_results = []
                negative_images = ["student.png", "television.png", "cinema-8f.png", "male-friend.png"]
                # 서로 다른 가로세로 비율에서도 실제 그림 면적이 비슷하게 보이도록 맞춥니다.
                negative_image_sizes = [(110, 110), (134, 89), (134, 89), (89, 134)]
                for index, (question, choices, correct) in enumerate(unit2_negative_questions):
                    with st.container(border=True):
                        image_col, question_col = st.columns([0.8, 3.2], vertical_alignment="center")
                        with image_col:
                            st.image(
                                fit_image_to_canvas(
                                    Path(__file__).with_name("assets") / "people" / negative_images[index],
                                    image_size=negative_image_sizes[index],
                                ),
                                width=180,
                            )
                        with question_col:
                            st.markdown(f"**가:** {question}")
                            selected = st.radio("나의 대답", choices, key=f"unit2_negative_{index}", horizontal=True, index=None, label_visibility="collapsed")
                        unit2_negative_results.append(selected == correct)
                unit2_negative_ready = all(
                    st.session_state.get(f"unit2_negative_{index}") is not None
                    for index in range(len(unit2_negative_questions))
                )
                if st.button(interface_text("네 문장 확인", "Check four sentences"), key="unit2_negative_check", type="primary", disabled=not unit2_negative_ready):
                    if all(unit2_negative_results):
                        st.session_state["unit2_negative_quiz_passed"] = True
                        render_learning_success(interface_text("네 문장을 모두 정확하게 완성했어요!", "You completed all four sentences correctly!"), icon=":material/check_circle:")
                    else:
                        st.session_state["unit2_negative_quiz_passed"] = False
                        incorrect_indices = [
                            index for index, is_correct in enumerate(unit2_negative_results) if not is_correct
                        ]
                        incorrect_labels = ", ".join(interface_text(f"{index + 1}번", f"Question {index + 1}") for index in incorrect_indices)
                        render_learning_warning(
                            interface_text(f"{sum(unit2_negative_results)}/4개가 맞아요. 틀린 문항은 {incorrect_labels}이에요.", f"{sum(unit2_negative_results)}/4 are correct. Review: {incorrect_labels}.")
                        )
                        for index in incorrect_indices:
                            correct_answer = unit2_negative_questions[index][2]
                            render_learning_info(interface_text(f"{index + 1}번 정답: {correct_answer}", f"Question {index + 1} answer: {correct_answer}"))
                st.divider()
                st.markdown(interface_text("### 2. 그림을 보고 ‘아니에요’가 들어간 알맞은 대답을 선택해 보세요.", "### 2. Look at each picture and choose the correct answer using 아니에요"))
                st.caption(interface_text("그림 속 사람이나 물건을 확인하고 보기에서 알맞은 대답을 선택하세요.", "Identify the person or object, then choose the correct Korean answer."))
                negative_dialogue_cards = [
                    ("학생", "student.png", "주노 씨는 회사원이에요?", "아니요, 회사원이 아니에요. 학생이에요."),
                    ("텔레비전", "television.png", "컴퓨터예요?", "아니요, 컴퓨터가 아니에요. 텔레비전이에요."),
                    ("남자", "male-friend.png", "여자예요?", "아니요, 여자가 아니에요. 남자예요."),
                ]
                negative_dialogue_image_sizes = [(110, 110), (134, 89), (89, 134)]
                negative_dialogue_columns = st.columns(3)
                unit2_dialogue_results = []
                for index, (label, image_name, question, correct_answer) in enumerate(negative_dialogue_cards):
                    with negative_dialogue_columns[index]:
                        with st.container(border=True):
                            st.image(
                                fit_image_to_canvas(
                                    Path(__file__).with_name("assets") / "people" / image_name,
                                    image_size=negative_dialogue_image_sizes[index],
                                ),
                                width=180,
                            )
                            st.caption(label)
                            st.markdown(f"**가:** {question}")
                            selected_answer = st.radio("대답", [correct_answer, "네, 맞아요."], key=f"unit2_negative_dialogue_{index}", index=None, label_visibility="collapsed")
                            unit2_dialogue_results.append(selected_answer == correct_answer)
                            if selected_answer is None:
                                st.caption(interface_text("대답을 선택해 주세요.", "Choose an answer."))
                            elif selected_answer == correct_answer:
                                render_learning_success(interface_text("정답이에요.", "Correct."), icon=":material/check_circle:")
                            else:
                                render_learning_warning(interface_text("오답이에요.", f"Try again. The correct answer is ‘{correct_answer}’."), icon=":material/cancel:")
                unit2_grammar2_ready = st.session_state.get("unit2_negative_quiz_passed", False) and all(unit2_dialogue_results)
                st.checkbox(
                    interface_text("두 활동의 일곱 문장을 모두 확인하고 문법 2를 마쳤어요", "I checked all seven Korean sentences and finished Grammar 2"),
                    key="grammar2_done_2",
                    disabled=not unit2_grammar2_ready,
                )
                if not unit2_grammar2_ready:
                    st.caption(interface_text("1번의 네 문장과 2번의 세 대화를 모두 맞히면 완료 표시가 열립니다.", "Answer all four sentences in Activity 1 and all three dialogues in Activity 2 correctly to finish Grammar 2."))
            elif current_unit["number"] == 3:
                render_unit3_grammar2()
            elif current_unit["number"] == 4:
                render_unit4_grammar2()
            elif current_unit["number"] == 5:
                render_unit5_grammar2()
            elif current_unit["number"] == 6:
                render_unit6_grammar2()
            elif current_unit["number"] == 7:
                render_unit7_grammar2()
            elif current_unit["number"] == 8:
                render_unit8_grammar2()
            elif current_unit["number"] == 9:
                render_unit9_grammar2()
            elif current_unit["number"] == 10:
                render_unit10_grammar2()
            else:
                grammar_sentence = st.text_input("내 문장 한 줄 만들기", placeholder=section["example"], key=f"grammar_sentence_{current_unit['number']}", disabled=not grammar2_unlocked)
                checked_sentence_key = f"grammar2_checked_sentence_{current_unit['number']}"
                feedback_key = f"grammar2_feedback_{current_unit['number']}"
                sentence_verified = bool(grammar_sentence.strip()) and st.session_state.get(checked_sentence_key) == grammar_sentence.strip()
                feedback_state = st.session_state.get(feedback_key)
                grammar_save_columns = st.columns([1, 1, 2])
                with grammar_save_columns[0]:
                    if st.button("정답 및 문법 확인", key=f"grammar_save_{current_unit['number']}", type="primary", disabled=not grammar2_unlocked or not grammar_sentence.strip(), width="stretch"):
                        passed, feedback = check_grammar2_sentence(current_unit["number"], grammar_sentence)
                        st.session_state[feedback_key] = (passed, feedback, grammar_sentence.strip())
                        feedback_state = st.session_state[feedback_key]
                        if passed:
                            st.session_state[checked_sentence_key] = grammar_sentence.strip()
                        else:
                            st.session_state.pop(checked_sentence_key, None)
                with grammar_save_columns[1]:
                    if feedback_state and feedback_state[2] == grammar_sentence.strip():
                        st.button("확인 닫기", key=f"grammar2_feedback_close_{current_unit['number']}", type="secondary", width="stretch", on_click=clear_session_state_key, args=(feedback_key,))
                if feedback_state and feedback_state[2] == grammar_sentence.strip():
                    if feedback_state[0]:
                        render_learning_success(feedback_state[1], icon=":material/check_circle:")
                    else:
                        render_learning_warning(feedback_state[1], icon=":material/rate_review:")
                if grammar2_unlocked and not grammar_sentence.strip():
                    st.caption("문장을 입력하면 확인 버튼이 활성화됩니다.")
                elif grammar2_unlocked and not sentence_verified:
                    st.caption("문법 확인을 통과해야 완료 표시를 할 수 있습니다.")
                st.checkbox("확인한 문장을 소리 내어 읽고 문법 2를 마쳤어요", key=f"grammar2_done_{current_unit['number']}", disabled=not grammar2_unlocked or not sentence_verified)
    with activity1_tab:
        with st.container(border=True):
            if current_unit["number"] not in (3, 4, 5, 6, 7, 8, 9, 10):
                activity1_heading = interface_text("활동 1 · 친구의 전화번호", "Activity 1 · A Friend’s Phone Number") if current_unit["number"] == 2 else interface_text("활동 1 · 인사", "Activity 1 · Greetings")
                st.subheader(activity1_heading)
            if current_unit["number"] == 1:
                st.markdown(interface_text("### 1. 안나 씨와 주노 씨의 인사를 읽고 정보를 찾아보세요.", "### 1. Read Anna and Juno’s greeting and find the information"))
                st.caption(interface_text("어휘에서 익힌 직업과 문법의 ‘이에요/예요’를 생각하며 읽어 보세요.", "As you read, focus on the occupations and the grammar forms 이에요/예요."))
                greeting_people, greeting_dialogue = st.columns([1, 2], vertical_alignment="center")
                with greeting_people:
                    person_columns = st.columns(2)
                    with person_columns[0]:
                        st.image(Path(__file__).with_name("assets") / "people" / "younger-sister.png", width=145)
                        st.caption("안나")
                    with person_columns[1]:
                        st.image(Path(__file__).with_name("assets") / "people" / "american.png", width=145)
                        st.caption("주노")
                with greeting_dialogue:
                    render_learning_info(
                        ":orange[안나: 안녕하세요? 저는 안나예요.]\n\n:blue[주노: 안녕하세요? 저는 주노예요.]\n\n:orange[안나: 주노 씨는 학생이에요?]\n\n:blue[주노: 네. 학생이에요. 안나 씨는요?]\n\n:orange[안나: 저는 회사원이에요.]",
                        icon=":material/forum:",
                    )
                question_columns = st.columns(2)
                unit1_choose_label = interface_text("선택하세요", "Choose")
                with question_columns[0]:
                    anna_job = st.selectbox(interface_text("안나 씨의 직업은 무엇이에요?", "What is Anna’s occupation?"), [unit1_choose_label, "학생", "회사원", "선생님"], key="unit1_activity1_anna_job")
                with question_columns[1]:
                    juno_job = st.selectbox(interface_text("주노 씨의 직업은 무엇이에요?", "What is Juno’s occupation?"), [unit1_choose_label, "학생", "회사원", "요리사"], key="unit1_activity1_juno_job")
                greeting_answered = anna_job != unit1_choose_label and juno_job != unit1_choose_label
                checked_greeting = st.session_state.get("unit1_activity1_greeting_checked")
                greeting_button_columns = st.columns([1, 1, 2])
                with greeting_button_columns[0]:
                    if st.button(interface_text("1번 답 확인", "Check Activity 1 answers"), key="unit1_activity1_greeting_check", type="primary", disabled=not greeting_answered, width="stretch"):
                        st.session_state["unit1_activity1_greeting_checked"] = (anna_job, juno_job)
                        checked_greeting = (anna_job, juno_job)
                with greeting_button_columns[1]:
                    if checked_greeting:
                        st.button(interface_text("확인 닫기", "Close feedback"), key="unit1_activity1_greeting_result_close", type="secondary", width="stretch", on_click=clear_session_state_key, args=("unit1_activity1_greeting_checked",))
                if checked_greeting:
                    anna_correct = checked_greeting[0] == "회사원"
                    juno_correct = checked_greeting[1] == "학생"
                    if anna_correct and juno_correct:
                        render_learning_success(interface_text("정답이에요. 안나는 회사원이고 주노는 학생이에요.", "Correct. Anna is an office worker, and Juno is a student."), icon=":material/check_circle:")
                    else:
                        render_learning_warning(interface_text(f"{int(anna_correct) + int(juno_correct)}/2개가 맞아요. ‘저는 회사원이에요’는 안나, ‘네. 학생이에요’는 주노의 말이에요.", f"{int(anna_correct) + int(juno_correct)}/2 are correct. Anna says ‘저는 회사원이에요,’ and Juno says ‘네. 학생이에요.’"))

                st.divider()
                st.markdown(interface_text("### 2. 이름과 직업을 선택해 인사 대화를 완성하고 소리 내어 읽어 보세요.", "### 2. Choose a name and occupation, then read the completed greeting aloud"))
                st.caption(interface_text("선택한 정보로 앞에서 배운 인사 대화가 완성됩니다.", "Your selections will complete the Korean greeting dialogue you practiced earlier."))
                friend_roster = {
                    "서유리": "대학생", "이서준": "의사", "마리": "회사원",
                    "박지유": "선생님", "김진우": "경찰", "웨이": "요리사",
                }
                role_columns = st.columns(2)
                with role_columns[0]:
                    selected_friend_name = st.selectbox(interface_text("이름", "Name"), list(friend_roster), key="unit1_activity1_friend_name")
                with role_columns[1]:
                    selected_friend_job = st.selectbox(
                        interface_text("직업", "Occupation"),
                        ["대학생", "의사", "회사원", "선생님", "경찰", "요리사"],
                        index=["대학생", "의사", "회사원", "선생님", "경찰", "요리사"].index(friend_roster[selected_friend_name]),
                        key="unit1_activity1_friend_job",
                    )
                friend_job_ending = "이에요" if subject_particle(selected_friend_job) == "이" else "예요"
                friend_name_ending = "이에요" if subject_particle(selected_friend_name) == "이" else "예요"
                render_learning_success(
                    f":orange[{selected_friend_name}: 안녕하세요? 저는 {selected_friend_name}{friend_name_ending}. {selected_friend_job}{friend_job_ending}.]",
                    icon=":material/record_voice_over:",
                )
                unit1_activity1_part1_passed = checked_greeting == ("회사원", "학생")
                unit1_activity1_dialogue_read = st.checkbox(
                    interface_text("완성한 인사 대화를 소리 내어 읽었어요", "I read the completed Korean greeting aloud"),
                    key="unit1_activity1_dialogue_read",
                    disabled=not unit1_activity1_part1_passed,
                )
                if unit1_activity1_part1_passed and unit1_activity1_dialogue_read:
                    st.session_state["activity1_completed_1"] = True
                else:
                    st.session_state.pop("activity1_completed_1", None)
                    st.caption(interface_text("정보 찾기와 인사 대화 읽기를 모두 완료해야 4단계가 끝납니다.", "Complete the information check and read the greeting aloud to finish Step 4."))
            elif current_unit["number"] == 2:
                st.space("small")
                st.markdown(interface_text("**1. 재민 씨와 안나 씨의 대화를 읽고 전화번호를 확인하세요.**", "**1. Read Jaemin and Anna’s dialogue and check the phone number**"))
                st.caption(interface_text("대화를 읽고 두 사람이 이야기하는 내용과 안나 씨의 전화번호를 찾아보세요.", "Identify what they are discussing and find Anna’s phone number."))
                activity1_picture, activity1_dialogue = st.columns([1, 2.7], vertical_alignment="center")
                with activity1_picture:
                    activity1_images = st.columns(2, gap="small")
                    with activity1_images[0]:
                        st.image(
                            fit_image_to_canvas(
                                Path(__file__).with_name("assets") / "people" / "phone-person.png",
                                canvas_size=(100, 150),
                                image_size=(90, 135),
                            ),
                            width=100,
                        )
                        st.caption("안나")
                    with activity1_images[1]:
                        st.image(
                            fit_image_to_canvas(
                                Path(__file__).with_name("assets") / "people" / "phone-keypad.png",
                                canvas_size=(100, 150),
                                image_size=(100, 67),
                            ),
                            width=100,
                        )
                with activity1_dialogue:
                    render_learning_info(
                        ":orange[재민: 안나 씨, 전화번호가 뭐예요?]\n\n"
                        ":blue[안나: 제 전화번호는 010-1359-6783이에요.]\n\n"
                        ":orange[재민: 010-1359-6784, 맞아요?]\n\n"
                        ":blue[안나: 6784가 아니에요. 6783이에요.]",
                        icon=":material/phone_in_talk:",
                    )
                activity1_answer_columns = st.columns(2)
                unit2_activity_choose_label = interface_text("선택하세요", "Choose")
                with activity1_answer_columns[0]:
                    conversation_topic = st.selectbox(
                        interface_text("1) 두 사람은 무엇을 묻고 답해요?", "1) What are they asking and answering about?"),
                        [unit2_activity_choose_label, "전화번호", "직업", "교실 위치"],
                        key="unit2_activity1_topic",
                    )
                with activity1_answer_columns[1]:
                    phone_answer = st.selectbox(
                        interface_text("2) 안나 씨의 전화번호는 몇 번이에요?", "2) What is Anna’s phone number?"),
                        [unit2_activity_choose_label, "010-1359-6783", "010-1359-6784"],
                        key="unit2_activity1_phone",
                    )
                activity1_answers_ready = conversation_topic != unit2_activity_choose_label and phone_answer != unit2_activity_choose_label
                activity1_check_result = st.session_state.get("unit2_activity1_check_result")
                activity1_check_columns = st.columns([1, 1, 2])
                with activity1_check_columns[0]:
                    if st.button(interface_text("1번 답 확인", "Check Activity 1 answers"), key="unit2_activity1_check", type="primary", disabled=not activity1_answers_ready, width="stretch"):
                        st.session_state["unit2_activity1_check_result"] = {
                            "topic": conversation_topic,
                            "phone": phone_answer,
                            "topic_correct": conversation_topic == "전화번호",
                            "phone_correct": phone_answer == "010-1359-6783",
                        }
                        activity1_check_result = st.session_state["unit2_activity1_check_result"]
                with activity1_check_columns[1]:
                    if activity1_check_result:
                        st.button(interface_text("확인 닫기", "Close feedback"), key="unit2_activity1_result_close", type="secondary", width="stretch", on_click=clear_session_state_key, args=("unit2_activity1_check_result",))
                if (
                    activity1_check_result
                    and activity1_check_result["topic"] == conversation_topic
                    and activity1_check_result["phone"] == phone_answer
                ):
                    topic_correct = activity1_check_result["topic_correct"]
                    phone_correct = activity1_check_result["phone_correct"]
                    if topic_correct:
                        render_learning_success(interface_text("1번 정답이에요. 정답: 전화번호", "Question 1 is correct: 전화번호."), icon=":material/check_circle:")
                    else:
                        render_learning_warning(interface_text("1번 오답이에요. 정답: 전화번호", "Question 1 is incorrect. The answer is 전화번호."), icon=":material/cancel:")
                    if phone_correct:
                        render_learning_success(interface_text("2번 정답이에요. 정답: 010-1359-6783", "Question 2 is correct: 010-1359-6783."), icon=":material/check_circle:")
                    else:
                        render_learning_warning(interface_text("2번 오답이에요. 정답: 010-1359-6783", "Question 2 is incorrect. The answer is 010-1359-6783."), icon=":material/cancel:")
                    st.session_state["unit2_activity1_part1_passed"] = topic_correct and phone_correct

                st.divider()
                st.markdown(interface_text("**2. 전화번호부에서 사람을 선택해 번호 대화를 완성해 보세요.**", "**2. Choose a person from the phonebook and complete the phone-number dialogue**"))
                st.caption(interface_text("사람을 선택한 뒤 완성된 질문과 대답을 소리 내어 읽고 번호를 확인하세요.", "Choose a person, read the completed Korean dialogue aloud, and verify the number."))
                friend_phone_roster = {
                    "주노": "010-1640-2953",
                    "유진": "010-1562-9122",
                    "마리": "010-1214-7406",
                    "서유리": "010-2387-6145",
                    "이서준": "010-4721-8306",
                }
                phonebook_column, speaking_guide_column = st.columns([1, 2])
                with phonebook_column:
                    st.markdown(interface_text("**친구 전화번호부**", "**Friends’ Phonebook**"))
                    st.table(
                        [
                            {interface_text("이름", "Name"): name, interface_text("전화번호", "Phone number"): phone}
                            for name, phone in friend_phone_roster.items()
                        ]
                    )
                with speaking_guide_column:
                    st.markdown(interface_text("**대화 순서**", "**Dialogue Steps**"))
                    render_learning_info(
                        interface_text(
                            "1. 전화번호부에서 확인할 사람을 선택해요.\n\n2. 전화번호부의 번호를 말해요.\n\n3. 들은 번호가 맞는지 확인해요.\n\n4. ‘맞아요’ 또는 ‘아니에요’로 대답해요.",
                            "1. Choose a person from the phonebook.\n\n2. Say the phone number aloud in Korean.\n\n3. Check whether the number you heard is correct.\n\n4. Answer with 맞아요 or 아니에요.",
                        ),
                        icon=":material/forum:",
                    )
                st.space("small")
                phone_friend = st.selectbox(interface_text("번호를 확인할 사람", "Person whose number you will check"), list(friend_phone_roster), key="unit2_activity1_friend")
                correct_friend_phone = friend_phone_roster[phone_friend]
                incorrect_friend_phone = f"{correct_friend_phone[:-1]}{(int(correct_friend_phone[-1]) + 1) % 10}"
                heard_phone = st.radio(
                    interface_text("재민이 들은 전화번호", "Phone number Jaemin heard"),
                    [correct_friend_phone, incorrect_friend_phone],
                    key="unit2_activity1_heard_phone",
                    horizontal=True,
                )
                if heard_phone == correct_friend_phone:
                    correct_confirmation = "네, 맞아요."
                    confirmation_choices = ["네, 맞아요.", f"아니요. {heard_phone[-4:]}가 아니에요."]
                else:
                    correct_confirmation = f"아니요. {heard_phone[-4:]}가 아니에요. {correct_friend_phone[-4:]}이에요."
                    confirmation_choices = ["네, 맞아요.", correct_confirmation]
                render_learning_info(
                    f":orange[재민: {phone_friend} 씨, 전화번호가 뭐예요?]\n\n"
                    f":blue[{phone_friend}: {correct_friend_phone}이에요.]\n\n"
                    f":orange[재민: {heard_phone}, 맞아요?]\n\n"
                    f":blue[{phone_friend}: ＿＿＿＿＿＿＿＿＿＿]",
                    icon=":material/record_voice_over:",
                )
                confirmation_answer = st.radio(
                    interface_text("빈칸에 들어갈 알맞은 대답", "Choose the correct Korean answer for the blank"),
                    confirmation_choices,
                    key="unit2_activity1_confirmation",
                    index=None,
                )
                confirmation_result = st.session_state.get("unit2_activity1_confirmation_result")
                confirmation_button_columns = st.columns([1, 1, 2])
                with confirmation_button_columns[0]:
                    if st.button(interface_text("2번 답 확인", "Check Activity 2 answer"), key="unit2_activity1_confirmation_check", type="primary", width="stretch", disabled=confirmation_answer is None):
                        confirmation_correct = confirmation_answer == correct_confirmation
                        st.session_state["unit2_activity1_confirmation_result"] = {
                            "friend": phone_friend,
                            "heard_phone": heard_phone,
                            "answer": confirmation_answer,
                            "correct": confirmation_correct,
                        }
                        confirmation_result = st.session_state["unit2_activity1_confirmation_result"]
                        if confirmation_correct:
                            st.session_state["unit2_activity1_part2_passed"] = True
                        else:
                            st.session_state["unit2_activity1_part2_passed"] = False
                with confirmation_button_columns[1]:
                    if confirmation_result:
                        st.button(interface_text("확인 닫기", "Close feedback"), key="unit2_activity1_confirmation_result_close", type="secondary", width="stretch", on_click=clear_session_state_key, args=("unit2_activity1_confirmation_result",))
                if (
                    confirmation_result
                    and confirmation_result["friend"] == phone_friend
                    and confirmation_result["heard_phone"] == heard_phone
                    and confirmation_result["answer"] == confirmation_answer
                ):
                    if confirmation_result["correct"]:
                        render_learning_success(interface_text("정답이에요.", "Correct."), icon=":material/check_circle:")
                    else:
                        render_learning_warning(interface_text("오답이에요.", f"Try again. The correct Korean answer is ‘{correct_confirmation}’."), icon=":material/cancel:")
                unit2_activity1_part1_current = bool(
                    activity1_check_result
                    and activity1_check_result["topic"] == conversation_topic
                    and activity1_check_result["phone"] == phone_answer
                )
                unit2_activity1_part2_current = bool(
                    confirmation_result
                    and confirmation_result["friend"] == phone_friend
                    and confirmation_result["heard_phone"] == heard_phone
                    and confirmation_result["answer"] == confirmation_answer
                )
                unit2_activity1_ready = (
                    st.session_state.get("unit2_activity1_part1_passed", False)
                    and unit2_activity1_part1_current
                    and st.session_state.get("unit2_activity1_part2_passed", False)
                    and unit2_activity1_part2_current
                )
                if unit2_activity1_ready:
                    st.session_state["activity1_completed_2"] = True
                else:
                    st.session_state.pop("activity1_completed_2", None)
                    st.caption(interface_text("두 전화번호 활동을 모두 정확하게 완료해야 4단계가 끝납니다.", "Complete both phone-number activities correctly to finish Step 4."))
            elif current_unit["number"] == 3:
                render_unit3_activity1()
            elif current_unit["number"] == 4:
                render_unit4_activity1()
            elif current_unit["number"] == 5:
                render_unit5_activity1()
            elif current_unit["number"] == 6:
                render_unit6_activity1()
            elif current_unit["number"] == 7:
                render_unit7_activity1()
            elif current_unit["number"] == 8:
                render_unit8_activity1()
            elif current_unit["number"] == 9:
                render_unit9_activity1()
            elif current_unit["number"] == 10:
                render_unit10_activity1()
            else:
                st.markdown(f"**활동:** {section['activity1']}")
                response = st.text_input("활동에서 만든 문장 또는 답", key=f"activity1_text_{current_unit['number']}", disabled=not activity1_unlocked)
                if st.button("활동 1 완료", key=f"activity1_done_{current_unit['number']}", disabled=not activity1_unlocked or not response.strip()):
                    st.session_state[f"activity1_completed_{current_unit['number']}"] = True
                    st.rerun()
    with activity2_tab:
        with st.container(border=True):
            if current_unit["number"] not in (3, 4, 5, 6, 7, 8, 9, 10):
                activity2_heading = interface_text("활동 2 · 전화번호", "Activity 2 · Phone Numbers") if current_unit["number"] == 2 else interface_text("활동 2 · 자기소개", "Activity 2 · Self-Introduction")
                st.subheader(activity2_heading)
            if current_unit["number"] == 1:
                st.markdown(interface_text("### 1. 두 사람의 자기소개를 읽고 이름과 직업을 찾아보세요.", "### 1. Read the two self-introductions and find each name and occupation"))
                st.caption(interface_text("어휘와 문법 1·2에서 배운 이름 → 나라 → 직업 순서를 확인하세요.", "Check the sequence you learned: name → country → occupation."))
                introduction_columns = st.columns(2)
                introductions = [
                    ("cook.png", "웨이", "안녕하세요?  \n저는 웨이예요.  \n저는 중국 사람이에요.  \n요리사예요."),
                    ("female-singer.png", "유나", "안녕하세요?  \n저는 유나예요.  \n저는 한국 사람이에요.  \n가수예요."),
                ]
                st.markdown(
                    """
                    <style>
                    div[class*="st-key-unit1_activity2_profile_"] { height:220px; }
                    @media (max-width:760px) {
                        div[class*="st-key-unit1_activity2_profile_"] [data-testid="stHorizontalBlock"] {
                            display:grid !important; grid-template-columns:minmax(110px, 1fr) minmax(0, 2fr) !important;
                            gap:10px !important; align-items:center !important;
                        }
            div[class*="st-key-unit1_activity2_profile_"] [data-testid="stColumn"] {
                            min-width:0 !important; width:100% !important; flex:none !important;
            }
            div[class*="st-key-unit1_grammar_dialogue_"] [data-testid="stHorizontalBlock"] {
                display:grid !important; grid-template-columns:minmax(150px, 1.5fr) minmax(0, 2.5fr) !important;
                gap:8px !important; align-items:center !important;
            }
            div[class*="st-key-unit1_grammar_dialogue_"] [data-testid="stColumn"] {
                min-width:0 !important; width:100% !important; height:auto !important; flex:none !important;
            }
                    }
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
                for index, (column, (image_name, name, introduction)) in enumerate(zip(introduction_columns, introductions)):
                    with column:
                        with st.container(border=True, height=220, key=f"unit1_activity2_profile_{index}"):
                            image_column, text_column = st.columns([1, 2], vertical_alignment="center")
                            with image_column:
                                profile_image_width = 150 if index == 0 else 130
                                st.image(
                                    Path(__file__).with_name("assets") / "people" / image_name,
                                    width=profile_image_width,
                                )
                            with text_column:
                                st.markdown(introduction)
                answer_columns = st.columns(2)
                unit1_profile_choose_label = interface_text("선택하세요", "Choose")
                with answer_columns[0]:
                    wei_name = st.text_input(interface_text("첫 번째 사람의 이름", "First person’s name"), placeholder=interface_text("이름", "Name"), key="unit1_activity2_wei_name")
                    wei_job = st.selectbox(interface_text("첫 번째 사람의 직업", "First person’s occupation"), [unit1_profile_choose_label, "회사원", "가수", "요리사"], key="unit1_activity2_wei_job")
                with answer_columns[1]:
                    yuna_name = st.text_input(interface_text("두 번째 사람의 이름", "Second person’s name"), placeholder=interface_text("이름", "Name"), key="unit1_activity2_yuna_name")
                    yuna_job = st.selectbox(interface_text("두 번째 사람의 직업", "Second person’s occupation"), [unit1_profile_choose_label, "의사", "가수", "요리사"], key="unit1_activity2_yuna_job")
                profile_answers_ready = all([wei_name.strip(), yuna_name.strip(), wei_job != unit1_profile_choose_label, yuna_job != unit1_profile_choose_label])
                checked_profile_answers = st.session_state.get("unit1_activity2_profile_answers")
                profile_button_columns = st.columns([1, 1, 2])
                with profile_button_columns[0]:
                    if st.button(interface_text("1번 답 확인", "Check Activity 1 answers"), key="unit1_activity2_profile_check", type="primary", disabled=not profile_answers_ready, width="stretch"):
                        checked_profile_answers = {
                            "웨이 이름": wei_name.strip(),
                            "웨이 직업": wei_job,
                            "유나 이름": yuna_name.strip(),
                            "유나 직업": yuna_job,
                        }
                        st.session_state["unit1_activity2_profile_answers"] = checked_profile_answers
                with profile_button_columns[1]:
                    if checked_profile_answers:
                        st.button(interface_text("확인 닫기", "Close feedback"), key="unit1_activity2_profile_result_close", type="secondary", width="stretch", on_click=clear_session_state_key, args=("unit1_activity2_profile_answers",))
                if checked_profile_answers:
                    profile_feedback = [
                        (interface_text("웨이의 이름", "Wei’s name"), checked_profile_answers["웨이 이름"], "웨이", interface_text("‘저는 웨이예요’에서 ‘저는’ 뒤의 말을 찾으면 돼요.", "Find the word after 저는 in ‘저는 웨이예요.’")),
                        (interface_text("웨이의 직업", "Wei’s occupation"), checked_profile_answers["웨이 직업"], "요리사", interface_text("웨이의 소개 마지막 문장 ‘요리사예요’에 직업이 나와요.", "The occupation appears in Wei’s final sentence, ‘요리사예요.’")),
                        (interface_text("유나의 이름", "Yuna’s name"), checked_profile_answers["유나 이름"], "유나", interface_text("‘저는 유나예요’에서 ‘저는’ 뒤의 말을 찾으면 돼요.", "Find the word after 저는 in ‘저는 유나예요.’")),
                        (interface_text("유나의 직업", "Yuna’s occupation"), checked_profile_answers["유나 직업"], "가수", interface_text("유나의 소개 마지막 문장 ‘가수예요’에 직업이 나와요.", "The occupation appears in Yuna’s final sentence, ‘가수예요.’")),
                    ]
                    profile_score = sum(selected == correct for _, selected, correct, _ in profile_feedback)
                    if profile_score == 4:
                        st.session_state["unit1_activity2_profile_passed"] = True
                        render_learning_success(interface_text("이름과 직업을 모두 정확하게 찾았어요!", "You found both names and occupations correctly!"), icon=":material/check_circle:")
                    else:
                        st.session_state["unit1_activity2_profile_passed"] = False
                        render_learning_warning(interface_text(f"{profile_score}/4개가 맞아요. 아래에서 어떤 답을 다시 확인해야 하는지 살펴보세요.", f"{profile_score}/4 are correct. Review which answers need another look below."))
                    st.markdown(interface_text("#### 문항별 피드백", "#### Feedback by Question"))
                    for label, selected, correct, explanation in profile_feedback:
                        if selected == correct:
                            feedback = interface_text(f"{label}: ‘{selected}’ — 정답이에요. {explanation}", f"{label}: ‘{selected}’ — Correct. {explanation}")
                            render_learning_success(feedback)
                        else:
                            feedback = interface_text(f"{label}: 입력한 답은 ‘{selected}’예요. 정답은 ‘{correct}’입니다. {explanation}", f"{label}: You entered ‘{selected}’. The correct answer is ‘{correct}’. {explanation}")
                            render_learning_error(feedback)

                st.divider()
                st.markdown(interface_text("### 2. 여러분을 소개하는 글을 써 보세요.", "### 2. Write your own self-introduction"))
                st.caption(interface_text(
                    "이름·나라·직업을 넣고 ‘이에요/예요’와 ‘은/는’을 사용해 자기소개를 완성하세요.\n\n예) 저는 홍길동이에요. 저는 한국 사람이에요. 저는 회사원이에요.",
                    "Add your name, country, and occupation. Use 이에요/예요 and 은/는 to complete your Korean introduction.\n\nExample: 저는 홍길동이에요. 저는 한국 사람이에요. 저는 회사원이에요.",
                ))
                intro_columns = st.columns(3)
                with intro_columns[0]:
                    intro_name = st.text_input(interface_text("① 이름", "① Name"), placeholder=interface_text("예: 마리아", "Example: Maria"), key="unit1_final_name", disabled=not activity2_unlocked)
                with intro_columns[1]:
                    intro_country = st.selectbox(interface_text("② 나라", "② Country"), ["한국", "캐나다", "베트남", "미국", "프랑스", "태국", "인도네시아", "중국", "일본", "러시아", "케냐"], key="unit1_final_country", disabled=not activity2_unlocked)
                with intro_columns[2]:
                    intro_job = st.selectbox(interface_text("③ 직업", "③ Occupation"), ["회사원", "대학생", "의사", "경찰", "선생님", "가수", "요리사"], key="unit1_final_job", disabled=not activity2_unlocked)
                name_ending = "이에요" if intro_name.strip() and subject_particle(intro_name) == "이" else "예요"
                introduction_response = f"안녕하세요? 저는 {intro_name.strip()}{name_ending}. 저는 {intro_country} 사람이에요. {intro_job}{'이에요' if subject_particle(intro_job) == '이' else '예요'}." if intro_name.strip() else ""
                current_profile_answers = {
                    "웨이 이름": wei_name.strip(),
                    "웨이 직업": wei_job,
                    "유나 이름": yuna_name.strip(),
                    "유나 직업": yuna_job,
                }
                unit1_profile_check_current = checked_profile_answers == current_profile_answers
                unit1_profile_ready = st.session_state.get("unit1_activity2_profile_passed", False) and unit1_profile_check_current
                response = introduction_response if unit1_profile_ready else ""
                if introduction_response:
                    render_learning_success(introduction_response, icon=":material/record_voice_over:")
                    st.caption(interface_text("쓰기·말하기 점검: 문장을 확인한 뒤 천천히 소리 내어 읽어 보세요.", "Writing and speaking check: Review your Korean sentences, then read them aloud slowly."))
                if introduction_response and not unit1_profile_ready:
                    st.caption(interface_text("먼저 1번 자기소개 독해 문제를 모두 맞혀 주세요.", "Answer all self-introduction reading questions in Activity 1 correctly before submitting."))
            elif current_unit["number"] == 2:
                st.space("small")
                unit2_place_quiz_current = render_unit2_place_phone_quiz()

                st.divider()
                st.space("small")
                st.markdown(interface_text("**2. 연습용 이름·전화번호·이메일 주소를 입력해 보세요.**", "**2. Enter a practice name, phone number, and email address**"))
                st.caption(interface_text("가상 연락처 한 줄을 모두 입력하면 제출할 수 있습니다. 더 연습하고 싶으면 세 줄까지 작성하세요.", "Complete one fictional contact to submit. You may enter up to three contacts for extra practice."))
                render_learning_info(
                    interface_text(
                        "개인정보 보호를 위해 실제 연락처 대신 연습용 가상 정보를 사용하세요.\n\n예: 마리 · 010-1234-5678 · mari@example.com\n\n이메일은 @example.com이 고정되어 있으므로 @ 앞부분만 입력하세요.",
                        "Use fictional information instead of real contact details to protect your privacy.\n\nExample: 마리 · 010-1234-5678 · mari@example.com\n\nThe domain @example.com is fixed, so enter only the part before @.",
                    ),
                    icon=":material/privacy_tip:",
                )
                contact_header = st.columns([1, 1.25, 1.6])
                with contact_header[0]:
                    st.markdown(interface_text("**이름**", "**Name**"))
                with contact_header[1]:
                    st.markdown(interface_text("**전화번호**", "**Phone Number**"))
                with contact_header[2]:
                    st.markdown(interface_text("**이메일 주소**", "**Email Address**"))
                contact_rows = []
                for index in range(3):
                    contact_columns = st.columns([1, 1.25, 1.6])
                    with contact_columns[0]:
                        contact_name = st.text_input(
                            f"{index + 1}번 이름",
                            placeholder=interface_text("예: 마리", "Example: Mari"),
                            key=f"unit2_contact_name_{index}",
                            label_visibility="collapsed",
                        )
                    with contact_columns[1]:
                        phone_key = f"unit2_contact_phone_{index}"
                        contact_phone = st.text_input(
                            f"{index + 1}번 전화번호",
                            placeholder="010-0000-0000",
                            key=phone_key,
                            label_visibility="collapsed",
                            max_chars=13,
                            on_change=format_korean_phone_input,
                            args=(phone_key,),
                        )
                    with contact_columns[2]:
                        email_name_column, email_domain_column = st.columns([1.15, 1], gap="small", vertical_alignment="center")
                        with email_name_column:
                            contact_email_name = st.text_input(
                                f"{index + 1}번 이메일 아이디",
                                placeholder="mari",
                                key=f"unit2_contact_email_name_{index}",
                                label_visibility="collapsed",
                            )
                        with email_domain_column:
                            st.markdown("**@example.com**")
                        contact_email = f"{contact_email_name.strip()}@example.com" if contact_email_name.strip() else ""
                    contact_rows.append((contact_name.strip(), contact_phone.strip(), contact_email.strip()))
                completed_contacts = [row for row in contact_rows if all(row)]
                incomplete_contacts = [row for row in contact_rows if any(row) and not all(row)]
                if incomplete_contacts:
                    st.caption(interface_text("작성 중인 줄의 이름·전화번호·이메일 주소를 모두 입력해 주세요.", "Complete the name, phone number, and email address in every row you started."))
                contact_response = "\n".join(" · ".join(row) for row in completed_contacts) if completed_contacts and not incomplete_contacts else ""
                if contact_response:
                    render_learning_success(contact_response, icon=":material/contact_phone:")
                unit2_activity2_ready = (
                    bool(contact_response)
                    and st.session_state.get("unit2_activity2_place_passed", False)
                    and unit2_place_quiz_current
                )
                response = contact_response if unit2_activity2_ready else ""
                if contact_response and not unit2_activity2_ready:
                    st.caption(interface_text("먼저 1번 장소 전화번호 두 문제를 모두 맞혀 주세요.", "Answer both place phone-number questions correctly before submitting."))
            elif current_unit["number"] == 3:
                response = render_unit3_activity2()
            elif current_unit["number"] == 4:
                response = render_unit4_activity2()
            elif current_unit["number"] == 5:
                response = render_unit5_activity2()
            elif current_unit["number"] == 6:
                response = render_unit6_activity2()
            elif current_unit["number"] == 7:
                response = render_unit7_activity2()
            elif current_unit["number"] == 8:
                response = render_unit8_activity2()
            elif current_unit["number"] == 9:
                response = render_unit9_activity2()
            elif current_unit["number"] == 10:
                response = render_unit10_activity2()
            else:
                st.markdown(f"**미션:** {section['activity2']}")
                response = st.text_area("나의 문장 또는 활동 메모", key=f"activity2_text_{current_unit['number']}", height=90, disabled=not activity2_unlocked)
            if activity2_unlocked and not response.strip():
                if current_unit["number"] == 2:
                    st.caption(interface_text("연습용 연락처 한 줄의 이름·전화번호·이메일 주소를 모두 입력하면 제출 버튼이 활성화됩니다.", "Complete one practice contact with a name, phone number, and email address to enable submission."))
                else:
                    st.caption(interface_text("문장이나 활동 메모를 작성하면 제출 버튼이 활성화됩니다.", "Write a sentence or activity note to enable the submit button."))
            activity2_completed = st.session_state.get(f"unit_completed_{current_unit['number']}", False)
            st.button(
                interface_text("활동 2 제출 완료 ✓", "Activity 2 submitted ✓") if activity2_completed else interface_text("활동 2 제출", "Submit Activity 2"),
                key=f"activity2_submit_{current_unit['number']}",
                type="secondary" if activity2_completed else "primary",
                disabled=activity2_completed or not activity2_unlocked or not response.strip(),
                on_click=complete_activity2,
                args=(current_unit["number"],),
            )
            if st.session_state.get(f"activity2_submission_notice_{current_unit['number']}", False):
                render_learning_success(interface_text("제출했어요. 오늘의 5단계 학습을 완료했습니다! +20 XP", "Submitted. You completed today’s five learning steps! +20 XP"), icon=":material/check_circle:")
    st.space("medium")
    st.markdown(interface_text("## 단원 핵심 정리", "## Unit Review"))
    st.caption(interface_text(
        "1~5단계에서 배운 두 문법과 대표 문장을 마지막으로 확인하세요.",
        "Review the two grammar points and the key sentence from Steps 1–5.",
    ))
    summary_columns = st.columns(2)
    for grammar_index, (column, grammar_name) in enumerate(zip(summary_columns, (section["grammar1"], section["grammar2"]))):
        with column:
            with st.container(border=True):
                grammar_class = "grammar-one" if grammar_index == 0 else "grammar-two"
                st.markdown(f'<h3 class="{grammar_class}">{html.escape(grammar_name)}</h3>', unsafe_allow_html=True)
                grammar_summary_rule = GRAMMAR_RULES.get(grammar_name, "대표 예문과 활동에서 사용 방법을 다시 확인해 보세요.")
                st.write(GRAMMAR_RULES_EN.get(grammar_name, grammar_summary_rule) if english_support_enabled() else grammar_summary_rule)
    highlighted_example = highlight_learning_text(section["example"], current_unit["number"])
    st.markdown(
        f'<div class="grammar-feedback"><b>{interface_text("대표 문장", "Key sentence")}</b><br>{highlighted_example}</div>',
        unsafe_allow_html=True,
    )
    st.space("medium")
    review_heading = interface_text("오늘의 3단계 복습 루틴", "Today’s 3-Step Review")
    review_description = interface_text(
        f'이것은 단원 학습 5단계와 별개의 복습 과정입니다. {current_unit["number"]}단원 5단계 학습을 모두 마친 뒤, 배운 내용을 짧게 다시 연습합니다.',
        f'This review is separate from the five unit-learning steps. After completing all five steps in Unit {current_unit["number"]}, briefly practice what you have learned.',
    )
    st.markdown(f'<div class="eyebrow">After unit completion · review</div><h2>{review_heading}</h2><p class="sub">{review_description}</p>', unsafe_allow_html=True)
    review_vocab_done = st.session_state.get(f"review_vocab_done_{current_unit['number']}", False)
    review_grammar_done = st.session_state.get(f"review_grammar_done_{current_unit['number']}", False)
    review_sentence_done = st.session_state.get(f"review_sentence_done_{current_unit['number']}", False)
    review_unlocked = REVIEW_MODE or unit_completed
    if not review_unlocked:
        render_learning_info(interface_text("단원 학습 5단계를 완료하면 복습 루틴이 열립니다.", "Complete all five unit-learning steps to unlock this review."), icon=":material/lock:")
    if review_vocab_done and review_grammar_done and review_sentence_done:
        st.caption(interface_text("오늘의 3단계 복습 루틴을 모두 완료했어요. 다시 할 필요가 없습니다.", "You completed today’s 3-step review. You do not need to repeat it."))
        st.button(interface_text("복습 다시 시작", "Restart review"), type="secondary", on_click=restart_review_routine, args=(current_unit["number"],))
    else:
        st.caption(interface_text("연두색 버튼이 지금 할 차례입니다. 어휘 → 문법 → 문장 조합 순서로 진행하세요.", "The light-green button shows your current step. Continue in this order: Vocabulary → Grammar → Sentence Building."))
    r1, r2, r3 = st.columns(3)
    with r1:
        review_vocabulary_by_unit = {
            1: [("회사원", "office worker"), ("대학생", "university student"), ("의사", "doctor")],
        }
        review_vocabulary = review_vocabulary_by_unit.get(current_unit["number"], current_vocabulary[:3])
        with st.container(border=True):
            st.markdown('<div class="eyebrow">01 · Warm up</div>', unsafe_allow_html=True)
            st.markdown(interface_text("### 오늘의 어휘 3개", "### Today’s 3 Vocabulary Words"))
            st.caption(interface_text("단어를 두 번씩 소리 내어 읽고 체크하세요.", "Read each Korean word aloud twice, then check it off."))
            vocabulary_learned_checks = []
            for word, _ in review_vocabulary:
                vocabulary_learned_checks.append(
                    st.checkbox(
                        word,
                        key=f"review_vocab_read_{current_unit['number']}_{word}",
                        value=review_vocab_done,
                        disabled=review_vocab_done or not review_unlocked,
                    )
                )
        vocabulary_review_ready = all(vocabulary_learned_checks)
        st.button(
            interface_text("어휘 복습 완료 ✓", "Vocabulary review complete ✓") if review_vocab_done else (interface_text("어휘 복습 완료하기", "Complete vocabulary review") if vocabulary_review_ready else interface_text("3개를 읽으면 완료 가능", "Read all 3 words to complete")),
            key="task_vocab",
            type="primary" if vocabulary_review_ready and not review_vocab_done else "secondary",
            disabled=review_vocab_done or not vocabulary_review_ready or not review_unlocked,
            width="stretch",
            on_click=complete_vocabulary_review,
            args=(current_unit["number"],),
        )
    with r2:
        grammar_review_title = interface_text("맞춤 문법 5분", "5-Minute Grammar Practice")
        grammar_review_copy = interface_text("현재 단원의 핵심 문법 6문제를 연습해요.", "Practice six questions on this unit’s key grammar.")
        st.markdown(f'<div class="card"><div class="eyebrow">02 · Practice</div><h3>{grammar_review_title}</h3><p class="tiny">{grammar_review_copy}</p></div>', unsafe_allow_html=True)
        st.button(
            interface_text("문법 복습 완료 ✓", "Grammar review complete ✓") if review_grammar_done else (interface_text("문법 복습 시작", "Start grammar review") if review_vocab_done else interface_text("어휘 복습 후 시작", "Complete vocabulary first")),
            key="task_grammar",
            type="primary" if review_vocab_done and not review_grammar_done else "secondary",
            disabled=not review_vocab_done or review_grammar_done,
            width="stretch",
            on_click=set_session_state_value,
            args=("go_practice",),
        )
    with r3:
        sentence_review_title = interface_text("문장 조합", "Sentence Building")
        sentence_review_copy = interface_text("오늘 배운 단어로 한 문장을 완성해요.", "Build one sentence using the Korean words you learned today.")
        st.markdown(f'<div class="card"><div class="eyebrow">03 · Build</div><h3>{sentence_review_title}</h3><p class="tiny">{sentence_review_copy}</p></div>', unsafe_allow_html=True)
        st.button(
            interface_text("문장 조합 완료 ✓", "Sentence building complete ✓") if review_sentence_done else (interface_text("문장 조합 시작", "Start sentence building") if review_grammar_done else interface_text("문법 복습 후 시작", "Complete grammar first")),
            key="task_sentence",
            type="primary" if review_grammar_done and not review_sentence_done else "secondary",
            disabled=not review_grammar_done or review_sentence_done,
            width="stretch",
            on_click=set_session_state_value,
            args=("go_builder",),
        )
    st.caption(f"교재 기준: {TEXTBOOK_SOURCE} · {TEXTBOOK_EDITION}. 원문 문장과 삽화는 복제하지 않고 자체 연습 콘텐츠로 제공합니다.")
    with st.expander("세종한국어 1A 전체 단원 매핑 보기"):
        for unit in TEXTBOOK_UNITS:
            unit_label = f"{unit['number']}단원" if unit["number"] else "입문"
            vocabulary = TEXTBOOK_VOCABULARY.get(unit["number"], [])
            vocabulary_text = ", ".join(f"{word}({meaning})" for word, meaning in vocabulary) or "자모·기본 음절"
            st.markdown(
                f"**{unit_label} · {unit['title'].split(' · ', 1)[-1]}**  "
                f"\n학습 목표: {unit['goal']}  "
                f"\n문법: {unit['grammar']} · 기능: {unit['functions']}  "
                f"\n핵심 어휘: {vocabulary_text}"
            )


inject_css()

if st.session_state.pop("go_practice", False):
    st.session_state.page_nav = "맞춤 복습"
if st.session_state.pop("go_pronunciation", False):
    st.session_state.page_nav = "발음 미션"
if st.session_state.pop("go_builder", False):
    st.session_state.page_nav = "문장 조합"

with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">✦</span><span class="instructor-name">모모</span><span class="course-name">의 한국어 강좌</span></div>', unsafe_allow_html=True)
    language = st.selectbox(
        "언어 / Language",
        ["한국어", "English", "日本語", "中文", "Tiếng Việt", "नेपाली", "Монгол", "ไทย", "O‘zbekcha", "မြန်မာ", "বাংলা"],
        key="app_language",
        help="Select the language for interface guidance. Korean learning content stays in Korean.",
    )
    theme_labels = {
        "한국어": {"title": "화면 색상", "black": "검은색", "nav": "메뉴", "unit": "학습 단원", "home": "첫 화면으로"},
        "English": {"title": "Color theme", "black": "Black", "nav": "Menu", "unit": "Lesson unit", "home": "Home"},
        "日本語": {"title": "画面の色", "black": "黒", "nav": "メニュー", "unit": "学習単元", "home": "ホーム"},
        "中文": {"title": "界面颜色", "black": "黑色", "nav": "菜单", "unit": "学习单元", "home": "首页"},
        "Tiếng Việt": {"title": "Màu giao diện", "black": "Đen", "nav": "Menu", "unit": "Bài học", "home": "Trang chủ"},
        "नेपाली": {"title": "स्क्रिन रङ", "black": "कालो", "nav": "मेनु", "unit": "पाठ एकाइ", "home": "गृहपृष्ठ"},
        "Монгол": {"title": "Дэлгэцийн өнгө", "black": "Хар", "nav": "Цэс", "unit": "Хичээлийн нэгж", "home": "Нүүр"},
        "ไทย": {"title": "สีหน้าจอ", "black": "ดำ", "nav": "เมนู", "unit": "บทเรียน", "home": "หน้าหลัก"},
        "O‘zbekcha": {"title": "Ekran rangi", "black": "Qora", "nav": "Menyu", "unit": "Dars bo‘limi", "home": "Bosh sahifa"},
        "မြန်မာ": {"title": "မျက်နှာပြင်အရောင်", "black": "အနက်", "nav": "မီနူး", "unit": "သင်ခန်းစာ", "home": "ပင်မစာမျက်နှာ"},
        "বাংলা": {"title": "স্ক্রিনের রং", "black": "কালো", "nav": "মেনু", "unit": "পাঠ ইউনিট", "home": "হোম"},
    }
    # New interface languages use the English navigation labels until their
    # unit-by-unit guidance translations are added.
    ui = theme_labels.get(language, theme_labels["English"])
    st.selectbox(
        ui["title"],
        ["검은색", "남색", "밝은색"],
        key="app_theme",
        format_func=lambda value: {
            "검은색": ui["black"],
            "남색": {"English": "Navy", "日本語": "紺", "中文": "藏青", "Tiếng Việt": "Xanh navy", "नेपाली": "गाढा निलो", "Монгол": "Хөх", "ไทย": "กรมท่า", "O‘zbekcha": "To‘q ko‘k", "မြန်မာ": "အပြာရင့်", "বাংলা": "নেভি"}.get(language, "남색"),
            "밝은색": {"English": "Light", "日本語": "明るい", "中文": "浅色", "Tiếng Việt": "Sáng", "नेपाली": "उज्यालो", "Монгол": "Цайвар", "ไทย": "สว่าง", "O‘zbekcha": "Ochiq", "မြန်မာ": "အလင်း", "বাংলা": "হালকা"}.get(language, "밝은색"),
        }[value],
    )
    st.divider()
    if st.button(ui["home"], icon=":material/home:", key="go_home"):
        st.session_state.page_nav = "내 학습"
        st.rerun()
    unit_options = [f"{unit['number']}단원 · {unit['title'].split(' · ', 1)[-1]}" for unit in TEXTBOOK_UNITS[1:]]
    selected_unit_label = st.selectbox(ui["unit"], unit_options, index=st.session_state.get("selected_unit_number", 1) - 1, key="selected_unit_label")
    st.session_state.selected_unit_number = int(selected_unit_label.split("단원", 1)[0])
    pages = ["내 학습", "맞춤 복습", "발음 미션", "문장 조합", "수업 Sync Mode", "강사 대시보드", "과제함"]
    page_names_en = {"내 학습": "My learning", "맞춤 복습": "Personal review", "발음 미션": "Pronunciation", "문장 조합": "Sentence builder", "수업 Sync Mode": "Class Sync Mode", "강사 대시보드": "Instructor dashboard", "과제함": "Assignments"}
    page_names = {
        "日本語": ["マイラーニング", "個人復習", "発音ミッション", "文づくり", "クラス同期", "講師ダッシュボード", "課題"],
        "中文": ["我的学习", "个人复习", "发音任务", "句子练习", "课堂同步", "教师面板", "作业"],
        "Tiếng Việt": ["Học tập của tôi", "Ôn tập cá nhân", "Nhiệm vụ phát âm", "Tạo câu", "Đồng bộ lớp học", "Bảng giáo viên", "Bài tập"],
        "नेपाली": ["मेरो सिकाइ", "व्यक्तिगत पुनरावृत्ति", "उच्चारण मिशन", "वाक्य निर्माण", "कक्षा समन्वय", "शिक्षक ड्यासबोर्ड", "असाइनमेन्ट"],
        "Монгол": ["Миний суралцахуй", "Хувийн давтлага", "Дуудлагын даалгавар", "Өгүүлбэр бүтээх", "Ангийн синк", "Багшийн самбар", "Даалгавар"],
        "ไทย": ["การเรียนของฉัน", "ทบทวนส่วนตัว", "ภารกิจการออกเสียง", "สร้างประโยค", "ซิงค์ชั้นเรียน", "แดชบอร์ดผู้สอน", "งานที่มอบหมาย"],
        "O‘zbekcha": ["Mening o‘qishim", "Shaxsiy takrorlash", "Talaffuz topshirig‘i", "Gap tuzish", "Sinf sinxroni", "O‘qituvchi paneli", "Topshiriqlar"],
        "မြန်မာ": ["ကျွန်ုပ်၏ သင်ယူမှု", "ကိုယ်ပိုင် ပြန်လည်လေ့ကျင့်ခြင်း", "အသံထွက် မစ်ရှင်", "ဝါကျတည်ဆောက်ခြင်း", "အတန်း Sync", "ဆရာ Dashboard", "တာဝန်များ"],
        "বাংলা": ["আমার শেখা", "ব্যক্তিগত পুনরাবৃত্তি", "উচ্চারণ মিশন", "বাক্য তৈরি", "ক্লাস সিঙ্ক", "শিক্ষক ড্যাশবোর্ড", "অ্যাসাইনমেন্ট"],
    }
    localized_pages = dict(zip(pages, page_names.get(language, [page_names_en[p] for p in pages]) if language != "한국어" else pages))
    page = st.radio(ui["nav"], pages, key="page_nav", label_visibility="collapsed", format_func=lambda value: page_names_en[value] if language == "English" else localized_pages[value])
    st.markdown(f"<br><br><div class='tiny'>{interface_text('학습 진행', 'YOUR PROGRESS')}</div>", unsafe_allow_html=True)
    st.progress(0.18)
    st.markdown("<span class='tiny'>세종한국어 1A · 18% 진행</span>", unsafe_allow_html=True)
    st.markdown("<br><br><span class='tiny'>© 2026 모모의 한국어 강좌</span>", unsafe_allow_html=True)

if page == "내 학습": dashboard()
elif page == "맞춤 복습": practice()
elif page == "발음 미션": pronunciation_mission()
elif page == "문장 조합": sentence_builder()
elif page == "수업 Sync Mode": sync_mode()
elif page == "강사 대시보드": teacher_dashboard()
else: assignments()
