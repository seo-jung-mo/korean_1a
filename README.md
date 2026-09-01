# 모모의 한국어 강좌

O2O 한국어 학습 플랫폼의 Streamlit 프로토타입입니다.

## 교재 기준

앱의 진도와 단원 매핑은 누리 세종학당에 공개된 **세종한국어 1A 2022년 개정판**의 공식 목차를 기준으로 합니다. 현재 앱은 입문 및 1~10단원의 제목, 학습 목표, 핵심 문법·기능을 매핑했으며, 교재 원문과 삽화는 복제하지 않고 자체 연습 콘텐츠로 제공합니다.

공식 자료: https://nuri.iksi.or.kr

## 실행

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

주요 화면: 학습자 대시보드, 맞춤 복습, 오프라인 수업 Sync Mode, 과제 제출.

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
