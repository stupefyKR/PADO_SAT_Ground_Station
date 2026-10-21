# 진행 기록

## 현재 상태

- 현재 단계: 단계 1 인터페이스·공통 파일 교환 기준선 완료; 수정된 단계 2(VM 간 통신 기반) 진행 중
- 다음 작업: Linux 서버·가상화 환경이 준비되면 서브시스템별 VM의 실제 연결·격리 시험. F19의 archive 시작 원시데이터 회수를 포함
- 미결 사항: TBD(ICD) 항우연·연세대 연동 포트·프로토콜·프레임, TC 명령·TM 실제 항목, DGS-GS 임시 원시데이터 용량·보관시간·회수 주기; TBD(HMAC) 트랜시버 세부 사양

## 기록

### 2026-10-21 — 단계 2 공통 전송 기반

- 완료: Linux `libgs`에 상호 인증 TLS 길이 접두 프레임, VM 내부 Unix socket JSON 프레임, 호스트 키 고정 SFTP 데이터·매니페스트 업로드/회수 구현. 임시 인증서·SSH 키를 생성하는 통합 시험 추가
- 시험: Windows MSVC Release·ASan CTest 각 1/1, Python 10/10, 비밀정보 검사 통과. [Ubuntu CI 실행 #8](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39561524833)에서 GCC Release·Clang ASan+UBSan CTest 각 3/3, clang-tidy, Python·비밀정보 검사 통과
- 변경 파일: `libgs/include/gs/`, `libgs/src/`, `tests/`, `CMakeLists.txt`, `.github/workflows/ci.yml`, `README.md`, `docs/`
- 결정: 전송 계약과 제한값 → DECISIONS.md #8
- 미완료: 실제 서브시스템별 VM 사이 연결·격리 시험. 사용자는 Linux 서버·가상화 환경이 아직 준비되지 않았다고 확인함
- 다음: 서버 준비 시 실제 VM 검증 수행. 그 전까지 단계 2는 진행 중으로 유지

### 2026-10-21 — 앱 우선 개발 순서 조정

- 결정: 서브시스템별 VM을 단일 Linux 서버에서 실행하는 구조와 관제용 PC → vm-portal 진입 경로를 유지한다. VM 간 통신과 핵심 기능을 먼저 구현하고 ROS·MPS 웹 UI를 마지막 개발 단계로 옮긴다.
- 변경: 개발방향 12장의 단계·완료 기준과 README의 단계 표를 갱신하고 결정 기록 #7을 추가함
- 미결: 하이퍼바이저 제품, 서버 사양, 물리 VLAN·방화벽 장비 및 UFW 적용 방식. 실제 배포·격리 검증은 수정된 단계 7에서 수행
- 다음: 수정된 단계 2의 확정된 VM 간 스트림·파일 교환 계약과 시험 환경 구현

### 2026-10-21

- 완료: 작업 폴더의 `pado-gs/`를 독립 개발 저장소로 구성하고, 11.2절 디렉터리 골격, 기준 문서 2종, `.gitignore`, 커밋 전 비밀정보 검사, CMake 정적 라이브러리 및 CTest 골격 작성
- 시험: Windows MSVC Release 빌드 경고 0, CTest 1/1 통과; MSVC AddressSanitizer 빌드 경고 0, CTest 1/1 통과; 비밀정보 검사 단위 시험 4/4 통과. Linux GNU/Clang 및 UndefinedBehaviorSanitizer 미실행
- 변경 파일: `CMakeLists.txt`, `libgs/`, `tests/`, `tools/check_secrets.py`, `.githooks/`, `docs/`, `README.md`
- 결정: 초기 빌드 확인용 임시 `gs_bootstrap_ready` 심볼과 Windows 빌드 옵션 → `DECISIONS.md` #1
- 문제: MSVC 기본 도구 선택 시 PDB 도구 버전 충돌이 발생하여, 설치된 `14.44.35207` 도구 버전을 빌드 명령에 지정함
- 원격 공개 범위: 첫 푸시 직후 GitHub 익명 API에서 공개 저장소로 확인됨. 사용자 확인을 받아 비공개로 전환했고, 설정 화면의 `currently private` 표시 및 익명 API 404를 확인함. 공개 상태였던 시간 동안 외부 열람 가능성은 배제할 수 없음
- 다음: 단계 1 세부 작업 계획 검토 후 구현 시작

### 2026-10-21 — 단계 1 기준선 구현

- 완료: 원본 `지상국.pdf`를 저장소에 보존하고 N2 표의 21개 데이터 화살표 및 승인된 21개 전송 경로를 `interfaces/n2_chart.json`으로 기록. 원시데이터의 DGS-Archive 시작 SFTP 회수(F19)와 TLE의 FDS 수신을 사용자와 확정
- 완료: 미확정 TC·TM 운용 값은 넣지 않고 버전형 빈 DB·확장 가능한 외피 스키마를 작성. 매니페스트 스키마, 엄격한 C 파서·경로/유형/해시 검증, JSON 로그, 원자적 파일 공개·검증, SHA-256 구현. cJSON v1.7.19 출처·라이선스·해시 고정
- 시험: Windows MSVC Release 경고 0, CTest 1/1 통과; MSVC ASan 경고 0, CTest 1/1 통과; Python 비밀정보·인터페이스 시험 10/10 통과. Linux GNU/Clang·UBSan 및 libFuzzer 실행은 이 환경에서 미수행
- 변경 파일: `interfaces/`, `libgs/`, `third_party/`, `tests/`, `CMakeLists.txt`, `README.md`, 기준 문서·결정 기록
- 결정: 원시데이터 회수 #2, TLE 수신처 #3, TC·TM 유보 #4, JSON·해시 구현 #5
- 문제: 실제 TC·TM ICD 부재로 운용 DB와 항목 스키마는 미확정. SFTP 송수신·inbox 감시·격리·삭제는 서비스 구현 단계에 남음
- 다음: Linux ASan+UBSan 필수 시험 완료 후 단계 2 배포 기반과 `check_flows` 구현

### 2026-10-21 — Linux 검증 경로 추가

- 완료: 사용자 승인으로 GitHub Actions Ubuntu 워크플로를 추가하고 GCC Release, Clang ASan+UBSan, clang-tidy, Python 검사를 등록함. POSIX `lstat` 선언에 필요한 기능 매크로를 명시함
- 시험: Windows MSVC Release·ASan CTest 각 1/1 통과; GitHub Actions 첫 실행 결과 대기
- 변경 파일: `.github/workflows/ci.yml`, `libgs/src/exchange.c`, `docs/DECISIONS.md`, `README.md`, `docs/PROGRESS.md`
- 결정: Linux CI 사용 → DECISIONS.md #6
- 문제: Linux CI 첫 실행 미확인
- 다음: CI 결과를 확인하고 오류를 수정한 뒤 단계 1 완료 판정

### 2026-10-21 — Linux CI 첫 검증

- 완료: 첫 CI 실행에서 GCC Release와 Clang ASan+UBSan 시험 통과. 정적 분석의 `memcpy` 경고 2건을 경계가 드러나는 복사로 수정한 뒤 두 번째 CI 실행에서 전체 성공
- 시험: GitHub Actions `Ground station CI` 실행 #2 성공 (`70ee53e`): GCC Release CTest, Clang ASan+UBSan CTest, clang-tidy, Python·비밀정보 검사 통과
- 변경 파일: `libgs/src/manifest.c`, `.github/workflows/ci.yml`
- 결정: CI가 보고한 checkout v4의 Node.js 20 지원 종료 안내를 없애기 위해 공식 v6.1.0(Node.js 24) 커밋으로 고정 → DECISIONS.md #6 갱신
- 문제: checkout 액션 업데이트 후 재실행 결과 대기
- 다음: 새 액션 버전의 CI를 확인하고 단계 1 완료 상태를 문서에 반영

### 2026-10-21 — 단계 1 완료

- 완료: GitHub Actions 실행 #3(`2110180`)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 검사 전체 성공. Node.js 지원 종료 알림도 제거함
- 시험: Windows MSVC Release·ASan CTest 각 1/1, Python 10/10; [Linux CI 실행 #3](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39551198200) 성공
- 변경 파일: `README.md`, `docs/PROGRESS.md`
- 결정: 미확정 TC·TM 운용 값은 사용자 지시에 따라 미구현으로 유지 → DECISIONS.md #4
- 문제: 외부 ICD, HMAC 사양, DGS-GS 임시 원시데이터 제한값 미확정. 이는 후속 서비스 단계에서 확정할 사항
- 다음: 사용자 요청 시 단계 2 배포 기반 구현
