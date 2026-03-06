# MultiTransportTester

TCP / UDP / Redis / Serial 통신을 한 UI에서 빠르게 점검하기 위한 데스크톱 테스트 앱입니다.  
Tkinter 기반 경량 도구로, 연결/송수신/프레이밍/주기 송신/로그 검색까지 한 번에 확인할 수 있습니다.

## Features

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

## Requirements

- Python 3.12
- Tkinter (GUI)
  
  redis>=5.0,<6.0
  pyserial>=3.5,<4.0
  python-osc>=1.8,<2.0
  
- PyInstaller (배포 빌드)

## Project Structure

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

## Getting Started (Development)

PowerShell 기준:

```powershell
cd D:\hong\PythonTest\MultiTransportTester
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\python app.py
```

## Quick UI Guide

### 1) Top Bar

- `Transport` 선택: TCP / UDP / REDIS / SERIAL
- `Theme` 선택: light / dark
- `START`: 선택 transport 시작
- `STOP`: 전체 transport/작업 중지
- `APPLY`: 현재 설정을 실행 중 엔진에 반영

### 2) Log Panel

- 검색: `Search` 입력 시 하이라이트
- 이동: `Prev`, `Next`
- 유틸: `Copy`, `Save`, `Wrap`, `Clear`, `Auto-Scroll`

### 3) Shared Settings

- Message Framing
  - `delimiter` / `fixed`
  - delimiter 종류 + custom hex
  - fixed length 정책(strict/pad/truncate)
- Manual Send
  - UTF-8 또는 HEX payload 수동 송신
- Timer Jobs
  - `sendTimer_1`, `sendTimer_2`, `sendTimer_3`, `heartbeat`
  - interval, payload, HEX 여부 설정 + 즉시 전송 버튼

## Keyboard Shortcuts

- `Ctrl + F`: 로그 검색 입력 포커스
- `Enter` (검색창): 다음 검색 결과
- `Shift + Enter` (검색창): 이전 검색 결과
- `F3`: 다음 검색 결과
- `Shift + F3`: 이전 검색 결과
- `Ctrl + L`: 로그 클리어
- `Ctrl + Shift + S`: 로그 파일 저장
- `Ctrl + Shift + C`: 로그 복사

## Build (Windows EXE)

### One-file Build (Recommended)

```powershell
.\venv\Scripts\pip install pyinstaller
.\venv\Scripts\python -m PyInstaller --onefile --windowed --name MultiTransportTester app.py --noconfirm --clean
```

Result:

- `dist\MultiTransportTester.exe`

### Build from Spec

```powershell
.\venv\Scripts\python -m PyInstaller MultiTransportTester.spec --noconfirm
```

## Configuration File (`settings.json`)

앱 실행 중 UI 상태가 저장되며, 다음 실행 시 자동 복원됩니다.

- transport 종류 및 각 transport 세부 설정
- framing 옵션
- manual send/job/heartbeat 설정
- theme, log wrap 등 UI 옵션

파일은 프로젝트 루트에 생성됩니다.


## Troubleshooting


- Redis `connect failed`
  - Redis 서버 실행 여부, host/port/db/password 확인

- Serial `open failed`
  - 포트명(COMx), 권한, 장치 연결 상태 확인

## License

This project is licensed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
