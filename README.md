# 모모의 한국어 강좌

O2O 한국어 학습 플랫폼의 Streamlit 프로토타입입니다.

## 교재 기준

앱의 진도와 단원 매핑은 누리 세종학당에 공개된 **세종한국어 1A 2022년 개정판**의 공식 목차를 기준으로 합니다. 현재 앱은 입문 및 1~10단원의 제목, 학습 목표, 핵심 문법·기능을 매핑했으며, 교재 원문과 삽화는 복제하지 않고 자체 연습 콘텐츠로 제공합니다.

공식 자료: https://nuri.iksi.or.kr

## 실행

Windows에서는 `실행.bat`를 더블 클릭하세요. 사용 가능한 가상 환경(`.venv-local`, `.venv-runtime`, `.venv` 순서)을 확인하고 서버를 시작한 뒤 기본 브라우저에서 http://127.0.0.1:8501 을 엽니다. 이미 서버가 실행 중이면 브라우저만 엽니다.

사용 중에는 실행 창을 열어 두고, 종료하려면 실행 창에서 `Ctrl+C`를 누르세요.

수동 실행 또는 최초 의존성 설치:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

주요 화면: 학습자 대시보드, 맞춤 복습, 오프라인 수업 Sync Mode, 과제 제출.

단원별 완료 기록과 진행 중인 답안은 `gravity_korean.db`에 자동 저장됩니다. 다른 단원으로 이동하거나 재접속해도 이어서 학습할 수 있으며, 복습 중 답을 바꿔도 이미 완료한 단계는 유지됩니다. 현재 앱은 이 PC의 하나의 학습 기록을 사용합니다.

저장 기능을 적용하기 전에 이미 사라진 과거 단계별 기록은 복원할 수 없습니다. 기존 XP와 일별 학습 기록은 유지됩니다.

진도 저장 검증: `.venv-local\Scripts\python.exe -m unittest discover -s tools -p test_lesson_progress.py -v`

## 실제 음성 분석·실시간 Sync 연결

OpenAI 음성 인식 기능을 사용하려면 실행 전에 API 키를 환경 변수로 설정합니다.

```powershell
$env:OPENAI_API_KEY = "여기에_OpenAI_API_키"
```

Firebase Realtime Database를 연결할 때는 데이터베이스 주소와 인증 토큰도 설정합니다.

```powershell
$env:FIREBASE_DATABASE_URL = "https://프로젝트-default-rtdb.firebaseio.com"
$env:FIREBASE_AUTH_TOKEN = "Firebase_인증_토큰"
```

키가 없으면 발음 화면은 안내 상태로 표시되고, Sync Mode는 로컬 데모로 동작합니다. 키를 코드에 직접 저장하지 마세요.

PowerShell 환경 변수 설정이 어려우면 `.streamlit/secrets.toml.example`을 복사해 `.streamlit/secrets.toml`로 만든 뒤 값을 입력해도 됩니다. `secrets.toml`은 자동으로 Git 제외 처리됩니다.
