# MultiTransportTester

TCP / UDP / Redis / Serial 통신을 한 UI에서 빠르게 점검하기 위한 데스크톱 테스트 앱입니다.  
Tkinter 기반 경량 도구로, 연결/송수신/프레이밍/주기 송신/로그 검색까지 한 번에 확인할 수 있습니다.

## 핵심 기능

- 멀티 트랜스포트 지원: `TCP`, `UDP`, `Redis Pub/Sub`, `Serial`
- 공통 메시지 프레이밍:
  - Delimiter 모드 (`LF`, `CRLF`, `CUSTOM HEX`)
  - Fixed Length 모드 (`strict`, `pad`, `truncate`)
- 수동 송신 + 주기 송신(Job) + Heartbeat
- 실시간 로그 뷰어:
  - 검색 및 `Prev/Next` 이동
  - 자동 스크롤 토글
  - Wrap, 복사, 저장
  - 중요 로그 컬러 강조 (`error`, `connect/disconnect`, `ui/setting`)
- 상태바 통계:
  - RX/TX bytes, frames, 속도, 마지막 수신/송신 시각
- 설정 저장/복원 (`settings.json`)
- Windows 단일 실행 파일 빌드(PyInstaller)

## 기술 스택

- Python 3.12
- Tkinter (GUI)
- `redis` (Redis transport)
- `pyserial` (Serial transport)
- `python-osc` (테스트 스크립트에서 사용)
- PyInstaller (배포 빌드)

## 프로젝트 구조

```text
MultiTransportTester/
├─ app.py                         # 메인 UI 진입점
├─ engine.py                      # 백그라운드 asyncio 네트워크 엔진
├─ ui_widgets.py                  # 공통 UI 위젯
├─ ui/
│  └─ transports/
│     ├─ tcp.py                   # TCP 전송 UI
│     ├─ udp.py                   # UDP 전송 UI
│     ├─ redis.py                 # Redis 전송 UI
│     ├─ serial.py                # Serial 전송 UI
│     └─ base.py                  # transport UI 공통 타입/콜백
├─ MultiTransportTester.spec      # PyInstaller onefile 빌드 spec
├─ requirements.txt               # 런타임 의존성
├─ tests/                         # 로컬 검증 스크립트(기본 gitignore)
└─ settings.json                  # 실행 중 생성되는 사용자 설정(기본 gitignore)
```

## 실행 방법 (개발 환경)

PowerShell 기준:

```powershell
cd D:\hong\PythonTest\MultiTransportTester
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\python app.py
```

## UI 빠른 사용법

### 1) 상단 바

- `Transport` 선택: TCP / UDP / REDIS / SERIAL
- `Theme` 선택: light / dark
- `START`: 선택 transport 시작
- `STOP`: 전체 transport/작업 중지
- `APPLY`: 현재 설정을 실행 중 엔진에 반영

### 2) 로그 영역

- 검색: `Search` 입력 시 하이라이트
- 이동: `Prev`, `Next`
- 유틸: `Copy`, `Save`, `Wrap`, `Clear`, `Auto-Scroll`

### 3) 공통 설정

- Message Framing
  - `delimiter` / `fixed`
  - delimiter 종류 + custom hex
  - fixed length 정책(strict/pad/truncate)
- Manual Send
  - UTF-8 또는 HEX payload 수동 송신
- Timer Jobs
  - `sendTimer_1`, `sendTimer_2`, `sendTimer_3`, `heartbeat`
  - interval, payload, HEX 여부 설정 + 즉시 전송 버튼

## 단축키

- `Ctrl + F`: 로그 검색 입력 포커스
- `Enter` (검색창): 다음 검색 결과
- `Shift + Enter` (검색창): 이전 검색 결과
- `F3`: 다음 검색 결과
- `Shift + F3`: 이전 검색 결과
- `Ctrl + L`: 로그 클리어
- `Ctrl + Shift + S`: 로그 파일 저장
- `Ctrl + Shift + C`: 로그 복사

## 빌드 (배포용 EXE)

### one-file (권장)

```powershell
.\venv\Scripts\pip install pyinstaller
.\venv\Scripts\python -m PyInstaller --onefile --windowed --name MultiTransportTester app.py --noconfirm --clean
```

결과물:

- `dist\MultiTransportTester.exe`

### spec 기반 빌드

```powershell
.\venv\Scripts\python -m PyInstaller MultiTransportTester.spec --noconfirm
```

## 테스트

현재 저장소에는 로컬 실행용 테스트 스크립트가 포함되어 있습니다.  
(`tests/`는 기본 `.gitignore` 대상이라 GitHub에 올리지 않도록 설정되어 있습니다.)

### Smoke Test

```powershell
.\venv\Scripts\python tests\smoke_app_runtime.py
```

- UI 생성/바인딩/검색/설정 저장복원/start-stop 흐름 확인

### Integration Test

```powershell
.\venv\Scripts\python tests\integration_transports_runtime.py
```

- TCP/UDP/Redis 실제 송수신 검증
- Serial은 루프백 포트가 있어야 `PASS`, 없으면 `SKIP`

Serial 루프백 포트 지정 예:

```powershell
$env:SERIAL_LOOP_PORT="COM5"
.\venv\Scripts\python tests\integration_transports_runtime.py
```

### Soak Test (장시간)

```powershell
.\venv\Scripts\python tests\soak_runner.py --minutes 10
```

- 연결/해제 반복
- 로그 폭주
- 검색 이동 반복

## 설정 파일 (`settings.json`)

앱 실행 중 UI 상태가 저장되며, 다음 실행 시 자동 복원됩니다.

- transport 종류 및 각 transport 세부 설정
- framing 옵션
- manual send/job/heartbeat 설정
- theme, log wrap 등 UI 옵션

파일은 프로젝트 루트에 생성됩니다.

## GitHub 업로드 가이드

현재 `.gitignore`로 아래 항목이 기본 제외됩니다.

- `venv/`, `__pycache__/`
- `build/`, `dist/`
- `tests/`
- `settings.json`
- IDE/임시 폴더 (`.idea/`, `.vscode/`, `.tmp/`)

즉, 일반적으로 `git add .` 시 실행/개발에 불필요한 산출물은 올라가지 않습니다.  
단, 과거에 이미 tracked 된 파일은 `git rm --cached`로 추적 해제가 필요합니다.

## 트러블슈팅

- `No Python at ...` 오류
  - 깨진 venv 또는 권한/경로 문제일 수 있습니다.
  - venv 재생성:
    ```powershell
    rmdir /s /q venv
    python -m venv venv
    .\venv\Scripts\pip install -r requirements.txt
    ```

- Redis `connect failed`
  - Redis 서버 실행 여부, host/port/db/password 확인

- Serial `open failed`
  - 포트명(COMx), 권한, 장치 연결 상태 확인

## 라이선스

필요 시 프로젝트 정책에 맞는 라이선스 파일(`LICENSE`)을 추가해 사용하세요.
