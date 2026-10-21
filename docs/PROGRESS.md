# 진행 기록

## 현재 상태

- 현재 단계: 단계 1 인터페이스·공통 파일 교환, 단계 2 공통 통신, 단계 3 시험용 TM 수신, 단계 4-1~4-5 MPS 비운용 기준선 구현
- 다음 작업: 단계 5 TC 경로의 비운용 경계와 실제 ICD 준비. 운용 명령 항목·FDS 파일·Mission Request 본문 형식은 확정 뒤 연결
- 미결 사항: TBD(ICD) 항우연·연세대 연동 포트·프로토콜·프레임, TC 명령·TM 실제 항목, DGS-GS 임시 원시데이터 용량·보관시간·회수 주기; TBD(HMAC) 트랜시버 세부 사양

## 기록

### 2026-10-21 — 단계 4-4·4-5 비운용 승인·ROS 검증·반입 기준선

- 구현: 최신 초안·패스·DB 버전·충돌 결과에 결합한 별도 lead 시험 승인, `TEST_ONLY_SEQUENCE`와 SHA-256 매니페스트 출력, ROS 별도 파서의 파일 쌍·의미 재검증. SDC→DGS-Archive→MPS 요청·업링크 매니페스트 체인, 반영·반려 판정, 독립 해시·목적·버전·FlatSat 기록 ID·역할 검사
- 시험: 승인 전·오래된 승인·자가 승인·재인증 누락·운용 출력명·해시 변조·매니페스트를 다시 만든 의미 변조·출처/버전 불일치·FlatSat 기록 누락·반입 후 파일 변조 거부. Windows Release·ASan CTest 각각 4/4, Python 10/10 통과. Linux CI 결과는 확인 후 갱신
- 경계: 실제 TC ICD·명령 DB, FDS Contact Schedule·Mission Request 본문·업링크 정책, 인증/FlatSat 증거 서비스는 아직 미정. 현재 ROS 연결은 로컬 시험 파일 쌍이며 운용 TC 경로를 열지 않음. 전체 단계 4의 운용 완료는 이 계약들이 확정된 후 별도 판정
- 결정: DECISIONS.md #13

### 2026-10-21 — 단계 4-2·4-3 MPS 초안과 충돌 분석 기준선

- 구현: 패스 시간창과 현 비운용 명령 DB 버전에 결합된 초안의 추가·교체·삭제, 불변 리비전 이력, 기대 리비전 기반 동시 수정 거부. 시간창·실행 중첩·자원·사전 조건·업링크 데이터량·명령 수의 여섯 규칙을 각각 함수로 구현
- 경계: 모든 초안과 분석은 비운용. 명령 속성과 분석 프로파일은 `interfaces/mps/test_contract.md`의 합성 시험 계약이며 실제 TC 명령·전송률·자원 비트를 뜻하지 않음. 현재 이력은 프로세스 메모리에만 저장하고 승인, 파일 출력, ROS 전달과 송출은 없음
- 시험: Windows MSVC Release·ASan CTest 각 3/3, Python 10/10, 비밀정보 검사 98개 파일 통과. [Ubuntu CI 실행 #20](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39611582454)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 결정: 비운용 계획·분석 속성 계약과 영속 저장 보류 → DECISIONS.md #12
- 다음: 단계 4-4. 실제 TC ICD와 명령 DB 해시 계약이 확정되기 전에는 운용 TC 시퀀스의 승인·출력을 완료 판정하지 않음

### 2026-10-21 — 단계 4 세분화와 4-1 MPS 입력 기준선

- 분할: 단계 4를 4-1 입력·카탈로그, 4-2 초안·버전, 4-3 충돌 분석, 4-4 승인·출력·ROS 연동, 4-5 Mission Request·업링크 반입으로 나눔. 웹 UI는 기존 단계 9에 유지
- 구현: `services/mps/core/inputs.c`에 현재 비운용 명령 DB의 정확한 봉투 판독과 내부 패스 목록 모델 추가. 운용 활성화, 미정 명령 항목, 중복 JSON 키, 길이 초과를 거부하고 패스 ID·AOS/LOS·여유 시간·목록 상한을 검사함
- 범위: 명령 DB는 계속 `operational: false`, 실제 TC 명령 0건. FDS Contact Schedule 파일 형식·출처 검증이 미정이라 내부 모델만 제공하며 외부 파일 수신, 시퀀스 승인·출력과 TC 송출 경로는 없음
- 시험: Windows MSVC Release·ASan CTest 각 2/2, Python 10/10, 비밀정보 검사 통과. [Ubuntu CI 실행 #16](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39574837655)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 기존 격리 컨테이너 시험 전체 성공. MPS JSON 파서의 libFuzzer 대상 등록, 퍼징 실행은 단계 8
- 결정: 단계 구분과 외부 입력 미정 처리 → DECISIONS.md #11
- 다음: 단계 4-2의 초안 편집·버전 이력

### 2026-10-21 — 단계 3 시험용 TM 수신 경로 완료

- 구현: `fake_kari`의 길이 접두 시험 프레임을 DGS-GS가 입력받아 길이·동기값·버전·종류·합성 SCID·CRC를 검증. 잘못된 입력은 사유 태그와 함께 비공개 파일에 격리하고 정상 프레임만 ROS가 시작한 상호 인증 TLS 연결로 전달. ROS는 프레임을 다시 검증한 후 원본 바이트를 기록하고 SHA-256을 포함한 JSON 이벤트를 남김
- 범위: 실행 파일은 `--sim-only`를 요구하며 시험용 규격은 `interfaces/frames/simulator.md`에 한정. 실제 외부 지상국 접속, 운용 TM DB와 파라미터 해석, 원시데이터 보관 정책은 미구현·미정
- 시험: 정상 프레임 2건 ROS 기록, CRC·SCID·동기값·길이 오류 5건 DGS-GS 격리, 개인 파일 권한을 통합 시험으로 검증. [Ubuntu CI 실행 #13](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39567480530)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 결정: 합성 프레임과 운영 ICD 분리 → DECISIONS.md #10
- 남은 검증: 실제 항우연·연세대 프레임의 수신·해석은 서명된 ICD 확보 후. 서브시스템별 VM 배포·UFW는 단계 7
- 다음: 단계 4 MPS 핵심 기능

### 2026-10-21 — 단계 2 개발용 컨테이너 네트워크 검증

- 환경: 사용자가 제공한 VMware Ubuntu 26.04 VM 한 대의 Docker 29.6.1. 최종 서브시스템별 VM 구조를 대체하지 않는 개발용 격리 네트워크로 사용
- 구현: `tests/container/`에 Ubuntu 24.04 시험 이미지, 서로 다른 컨테이너 IP의 mTLS 왕복·상대 IP SAN 거부, SSH 호스트 키 고정 SFTP ACK 업로드·F19 원시데이터 회수·오류 거부, 연결되지 않은 Docker 네트워크의 접속 거부 시험 추가. 비밀 키·인증서는 실행 시 임시 생성
- 시험: VM 시험 컨테이너에서 GCC Release CTest 3/3, Clang ASan+UBSan CTest 3/3 통과. 컨테이너 간 mTLS·SFTP·격리 시험 통과. 단계 2 개발 기준선 완료
- 남은 검증: 실제 서브시스템별 VM 네트워크, UFW, 호스트·장비 격리와 서비스 배포는 단계 7에서 수행
- 다음: 단계 3 모의 TM 수신 경로

### 2026-10-21 — 단계 2 공통 전송 기반

- 완료: Linux `libgs`에 상호 인증 TLS 길이 접두 프레임, VM 내부 Unix socket JSON 프레임, 호스트 키 고정 SFTP 데이터·매니페스트 업로드/회수 구현. 임시 인증서·SSH 키를 생성하는 통합 시험 추가
- 시험: Windows MSVC Release·ASan CTest 각 1/1, Python 10/10, 비밀정보 검사 통과. [Ubuntu CI 실행 #9](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39561766519)에서 GCC Release·Clang ASan+UBSan CTest 각 3/3, clang-tidy, Python·비밀정보 검사 통과
- 변경 파일: `libgs/include/gs/`, `libgs/src/`, `tests/`, `CMakeLists.txt`, `.github/workflows/ci.yml`, `README.md`, `docs/`
- 결정: 전송 계약과 제한값 → DECISIONS.md #8
- 당시 미완료: 실제 서브시스템별 VM 사이 연결·격리 시험. 이후 제공된 VMware VM의 Docker 격리 네트워크에서 개발용 통신 시험을 수행함
- 다음: 실제 VM 배포 검증은 단계 7에서 수행

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
