# 진행 기록

## 2026-11-04 — TC·TM 통합 검증 환경

- 같은 지상국 이미지에 검증·운용별 로그인/API 주소, 쿠키, 계정·데이터 볼륨, 설정, 내부망·방화벽 체인을 분리했다. 검증 환경은 기존 가상 PADO-SAT만 로그인 후 활성화하며 ISS 생성기를 제외한다. 운용 환경은 가상 위성·합성 입력 생성기를 제외하고 실제 입력을 기다리는 FDS 전파 프로세스를 유지한다.
- MPS/ROS의 `Validation Packets`에서 현재 인자가 정의된 TC 36종의 53개 대표·경계 시험 값과 TM 요청 1개를 예약한다. DGS의 실제 소켓 송신 원문·시각과 가상 위성 ACK를 별도로 기록한다. 인자 ICD 미정 22종은 목록으로 남긴다.
- 정상 7,000개·손상 2,500개·정상 급변 500개를 섞은 TM 10,000개를 기존 가상 위성→DGS 수신 경로로 전송하고 정상 필드·거부·누락·오수락·오거부·센서 이벤트를 비교한다. 손상 19분류와 일반 수신의 공통 GNSS·자세 검사를 포함한다. 큰 유한 GNSS 값이 제곱 연산 예외를 일으키지 않도록 벡터 norm 계산도 보완했다.
- Linux 실제 Unix TC 브리지·가상 위성·DGS 소켓 왕복에서 TC 54개 ACK, TM 정상 파싱 7,500개·손상 거부 2,500개·급변 이벤트 검출을 확인했다. 로그인 활성화·API 인증/CSRF·환경 간 세션 차단·두 환경 방화벽 공존·이전 비콘/TC/TM 흐름도 회귀 시험했다. 잘못된 파싱 결과를 주입하면 PASS가 아닌 FAIL이 되는 역방향 시험을 추가했다.
- VM 배포와 브라우저 표시 확인은 아직 수행하지 않았다. 실제 Docker Compose 정규화 검사는 CI에 추가했다. 절차·범위·API·데이터셋 생성법은 [TC·TM 통합 검증](TC_TM/VALIDATION.md)에 정리했다. FSW 물리 동작·비행용 RF 프레임·실제 송신 승인 검증은 이 시험의 판정 범위가 아니다.
- 시험 결과: Windows 전체 395개 중 326개 통과·69개 환경 조건으로 제외, Linux 관련 93개 중 90개 통과·3개 제외. 추가 실패 판정 주입·설정 고정·화면 이력 회귀 시험도 통과했다. `node --check`, 변경 내용 공백 검사, staged 비밀정보 검사 통과.

## 현재 상태

- 현재 단계: 단계 1–8 개발 기준선 완료, 단계 9 비운용 웹 경계 진행 중. 로컬 화면 시험 기본 궤도는 고도 약 500 km·LTAN 약 12시의 **가상 PADO-SAT SSO TLE**다(2026-10-26 변경). 가상 위성은 -X Nadir로 5초마다 EPS/AOCS/GNSS 초안 TM PDU를 생성·저장하고 연세대 UHF PASS에서 비콘 및 TC 요청에 따른 저장분을 전송하며 파싱 결과가 ROS 시험 스냅샷·N11 시험 파일 쌍·FDS GNSS 우선 예측으로 이어진다(결정 #38). MPS의 저장된 읽기 전용 시험 시퀀스를 ROS에 전달·예약하고 가상 위성의 `COMMAND_RESULT` TM을 확인하는 별도 왕복 경로를 추가했다(결정 #39). FDS의 N08 지향·패스 요청은 별도 로컬 시험 출고함에만 게시하고, ROS의 가상 ACK/EPS 시험 SOH는 MPS의 읽기 전용 피드백으로 보인다. ROS는 FDS N13의 두 지상국 예측 PASS 시각을 표시한다. 별도 실제 입력 경계에는 FDS N05 인증 SFTP pull·N11 수신함 검증·반복 계산·로컬 N13 게시와 MPS C 검증·웹 읽기가 있다. FDS는 기동을 입력받지 않는 궤도 예측 전용이다. 실제 VM·독립 백업·복원·FlatSat 적용은 모든 개발 후 배포 수락 절차로 이관. NAS는 별도 후속 과제로 제외(결정 #19)
- 다음 작업: 개발 중에는 가상 위성 TM·TC 왕복과 FDS·MPS·ROS 화면의 새 변경을 Linux CI에서 계속 검증한다. 모든 개발이 끝난 뒤 VMware VM의 `./pado-gs.sh start`로 가상 TM→ROS·N11→FDS와 MPS→ROS→가상 위성 ACK·ORBIT/MPS 화면을 통합 확인한다. 운용 전환 작업은 별개로 CS/CDHS 패킷 ID·필드·비콘·인증·ACK·상태값과 OEM719A→OBC 실제 TM 매핑을 검토한다. 실제 컨택 검증 단계에서 안테나 측량 좌표·고각 기준을, Hz 도플러 사용 전 RF 주파수를 정한다.
- 미결 사항: 실제 GNSS OBC TM 필드·상태 플래그, 컨택 검증용 연세대/항우연 안테나 측량 좌표·고각 기준, Hz 도플러용 할당 RF 주파수, HPOP 정밀도 목표와 참조 궤도, 항우연·연세대 연동 포트·프로토콜·프레임, N08 실제 안테나 명령·수락 계약, N10 운용 파일쌍·수락 ACK, N13 `ORBITAL_EVENT` 정의·소비 정책, TC 인증·ACK·비콘 실제 바이트 배치와 센서 보정/한계값, DGS-GS 임시 원시데이터 용량·보관시간·회수 주기. 예비 질량과 링크 버짓은 현재 작업의 선행 조건이 아니다. 남은 실제 ICD 증거는 [검토·인계표](TC_TM/REVIEW_GATES.md)에 구분했다.

## 기록

### 2026-10-27 — VM MPS 명령 DB 권한 수정

- 후속 VM 기동에서는 DB `stat`부터 접근이 거부돼 상위 디렉터리 탐색 권한도 함께 고정했다. 이미지에 경로를 먼저 만들고 디렉터리 `0755`·파일 `0444`를 별도로 적용한다. 시작 실패 시에는 정리 전에 실패 단계·최근 로그·health 오류를 출력하도록 보완했다.
- 비특권 Linux 사용자의 디렉터리 탐색 회귀 2개와 시작·실패 진단·정리 시험 20개를 통과했다. CI 이미지 시험은 실제 UID 10001에서 부모 디렉터리 탐색과 파일 읽기·C 판독을 함께 확인한다.
- 실제 VM에서 시험 카탈로그 58종은 읽히지만 C 코어 명령 DB 판독이 거부되는 문제를 확인했다. 이미지 DB 권한이 `0664`였고, 동일 파일은 C 보안 검사를 통과하는 `0444`·`0644`와 달리 그룹 쓰기 가능 상태에서 거부됐다.
- Docker COPY 단계에서 명령 DB·시험 카탈로그를 `0444`로 고정했다. C의 입력 권한 검사는 유지하고, 두 Compose의 MPS healthcheck가 소켓뿐 아니라 실제 DB 판독도 확인하도록 바꿨다. 운영자 계정·예약·TM 볼륨은 변경하지 않는다.

### 2026-10-27 — 장비 TM·비콘·원문 아카이브와 ROS 종합 관제

- 제공된 비콘·HK 슬라이드를 반영해 13개 계측 TM 계열과 12개 장비 그룹의 전체 HK 시험 자료를 생성한다. 정상 비콘은 기본 10초 주기의 상태·식별 묶음이며, LEOP P-P 식별 파형은 별도 참고 프로파일이다. 실제 장비 바이너리·전환 조건은 제공 자료에 맞춰 후속 연결한다.
- 수신 원문과 파싱 값을 영속 아카이브에 보관하고, 지상국 저장 확인을 받은 PDU만 위성에서 삭제한다. ROS를 종합 관제 / 수신 TM / TC 준비·예약으로 재구성했다. 포트·Docker 네트워크·DGS 단일 외부 접점은 그대로 유지한다.
- Linux 예약 통합 시험에서 256개 저장 PDU의 수신 확인, 비콘·ACK 포함 263개 기록, 20개 센서 이력, GNSS N11·MPS 피드백, 전체 HK 재조립과 RAW·파싱 값 일치를 확인했다. 20개 센서의 1시간·각 721개 표본은 기존 1 MiB 스냅샷 상한 안에 들어갔다. 자세한 계약은 [장비 TM 안내](SIM_TELEMETRY_PROFILE.md)에 있다.
- Windows 회귀 시험 263개 중 215개 통과·환경 의존 48개 제외. 별도 Linux 수신·아카이브·교신 시험 33개와 HTTP·receipt 왕복 3개를 통과했다. 브라우저 GPU 표시·실제 VM Docker 실행·RF 검증은 이번 결과에 포함하지 않는다.

### 2026-10-27 — 비콘과 TC 요청 기반 저장 TM 수신

- MPS 브라우저의 송신 허용 목록에서 빠진 `hk_dump`를 연결하고, 전체 저장분 인자 `0/0`과 기본 실행 AOS + 10초를 표시했다. 실제 Linux HTTP·Unix socket 통합 시험으로 MPS PASS 조회·저장·전달→ROS 예약 자동 송신→위성 ACK→48개 TM 수신→ROS 그래프·N11·MPS 피드백을 확인했다. 합성 단기 PASS를 사용했으며 실제 VM·FDS 기하·RF 판정은 포함하지 않는다. 회귀 시험은 241개 중 195개 통과, Windows 환경 의존 46개 제외다. 상세 결과와 재현 명령은 [TC·TM 안내](SIM_TC_TM.md)에 남겼다.
- 가상 위성은 계측·−X Nadir 자세·GNSS 생성과 24시간 저장을 유지하며, 교신 중에는 기본적으로 비콘만 보낸다. `hk_dump` TC를 수신하면 요청 수락 ACK 뒤 요청 시점의 저장분을 전송한다. 새 표본은 다음 요청까지 보관하고, LOS·접속 종료·재시작 뒤에는 새 요청이 필요하다.
- 명령 처리기는 이름별 등록 구조로 분리했다. 길이·CRC·시각·중복·인자를 검증하며, 지원하지 않는 명령과 처리기 오류는 거절한다. 기존 조회 명령은 수락 응답만 돌려준다.
- ROS에 비콘 상태·즉시 저장 TM 요청·요청 결과 기록을 연결했다. 로그인·역할·CSRF와 기존 비공개 Unix socket·내부 TCP 경계를 사용하며 호스트 포트·외부 접속 경로를 추가하지 않는다. 비콘과 과거 센서 이력의 시각·순번을 분리했다. 상세 동작과 확장 방법은 [SIM_TC_TM.md](SIM_TC_TM.md)에 기록했다.
- 검증: 로컬 회귀 시험 239개 중 194개 통과, 환경 의존 45개 제외. 별도로 실제 소켓의 비콘→TC→ACK→저장 TM→ROS/N11 왕복과 새 HTTP API 시험을 통과했다. JS·CI 셸 구문과 공백 검사 통과. Docker 컨테이너 실행·실제 VM 웹 표시는 이번 로컬 검증에 포함하지 않는다.

### 2026-10-26 — 시뮬레이션 LTAN 12시

- 사용자 변경 요청에 따라 약 500 km SSO 시뮬레이션의 LTAN을 12시로 바꿨다. 실제 남→북 적도 통과의 UTC와 ITRF 경도에서 구한 지방 평균 태양시는 기준 시점 12.000000h, 72시간 뒤 11.999664h였다.
- 이전 06시 GNSS가 새 FDS 예측에 선택되거나 저장 TM이 섞이지 않게 `ltan12-v1`의 실행·N11·위성 저장 파일을 사용한다. 기존 자료는 그대로 보존한다. 시뮬레이터는 새 truth의 시나리오 ID가 일치한 뒤 TM 생성을 시작한다.
- 자세는 기체 -X Nadir, +Z 진행 방향이다. 미교신 중 5초마다 만든 TM은 24시간 한도로 저장하고 PASS에서 원래 측정 시각 순으로 전송한다.

### 2026-10-24 — 최초 개발방향 누락 경계 보강

- 최초 저장소 문서와 현행 개발방향을 [요구 추적표](INITIAL_REQUIREMENTS_REVIEW.md)로 대조했다. 8 VM·VLAN·NAS·운용 HTTPS 등은 이후 사용자 결정으로 바뀐 전제이며, 누락 기능으로 다시 열지 않는다.
- FDS는 이미 계산한 두 지상국의 PASS와 10초 지향값에서 N08 `PASS_REQUEST`·`ANTENNA_POINTING` 초안 본문/매니페스트를 만든다. N13 시간창·지상국·각도·품질 표식을 재검증하고 N13/N15 전송 출고함과 다른 로컬 출고함에만 둔다. 실제 DGS-GS 전송·안테나 구동은 차단한다.
- ROS의 검증된 `SIM ONLY` ACK와 EPS 시험 스냅샷 일부를 인증된 Portal의 고정 GET을 거쳐 MPS 화면에서 읽는다. ROS→MPS 신규 Docker 연결과 저장소 쓰기 권한은 없다. 실제 N10 SFTP 파일쌍·수신 수락은 아직 없다. ROS에는 FDS N13과 ORBIT 시간창을 대조한 두 지상국 AOS/LOS 시험 카운트다운을 추가했다. 예측 가시구간과 실제 RF 링크 상태를 구별한다.
- 로컬 Compose 정책은 예상 밖 서비스/네트워크 소속과 호스트·간접 bind mount를 거부한다. 개발 TLS 구성의 지정된 읽기 전용 마운트만 예외로 둔다. 이 변경의 Python 단위·정책 시험은 Windows에서 통과했으나, Docker가 없는 현재 PC에서는 Compose 실파일·컨테이너 시험과 Java/Maven FDS 시험을 실행하지 못했다. Linux CI 및 최종 VM에서 이어서 확인한다.

### 2026-10-24 — FDS Linux CI 의존성 확인 방식 조정

- FDS 궤도 시험 작업이 Ubuntu 패키지 미러의 Java·Maven 설치 단계에서 지연돼, 워크플로가 실행 환경에 이미 있는 `java`·`javac`·`mvn`·`unzip`·OpenSSL 헤더를 먼저 확인하도록 바꿨다. 물리 자료의 고정 해시 검사와 FDS 시험은 그대로 실행한다. 변경 후 CI 결과는 별도 확인한다.

### 2026-10-24 — ROS 시험 TM 추세 60분 이력

- `sim-ground`는 그래프에 설정된 센서의 원본 TM PDU 계열만 최근 UTC 60분·계열별 최대 721개로 메모리에 유지해 5분·30분·1시간 화면 범위를 실제 수신 표본으로 채운다. 자세는 최신 표본을 따로 유지하며, GNSS가 그래프 센서로 설정되지 않았다면 이력 메모리에는 넣지 않고 기존 N11 브리지는 유지한다.
- 게시 스냅샷의 1 MiB 상한과 ROS API 재검증은 유지한다. 시작 직후에는 수신한 만큼만 표시하고 수신 프로세스가 재시작하면 새 표본부터 이력을 다시 쌓는다. 5초 간격 표본 721개, 오래된 표본 제거, 자료 공백·재시작과 자세 최신성을 시험했다. 이는 시험 화면 이력이며 운용 장기 보존이 아니다.

### 2026-10-23 — MPS→ROS→가상 위성 TC/ACK 시험 왕복

- MPS는 저장된 미래 연세대 UHF PASS 시퀀스의 일정 해시·리비전·명령 인자를 재검증해 포털을 통해 ROS의 별도 `SIM ONLY` 저장소로 넘긴다. ROS는 읽기 전용 초안 명령을 AOS+오프셋 시각에 예약하고, 가상 위성용 응용 TC PDU만 비공개 Unix socket→Docker 내부 TCP 연결로 전달한다. 가상 위성의 `COMMAND_RESULT` TM에서 명령 ID·순번·결과를 검사한 뒤 ROS 화면에 예약 상태와 ACK를 보여준다.
- 기존 파일 쌍 가져오기 및 `DRAFT_ONLY` 예약은 자동 전송되지 않는다. 새 경로도 실제 승인·서명·RF 송출이나 운용 TC 게이트와 연결되지 않는다. 예정 시각을 놓치거나 결과를 확인할 수 없을 때 자동 재전송하지 않는다.
- Ubuntu CI에 ROS API 이미지에서 비공개 소켓을 통한 `mm_get_mode` TC/ACK와, 미래 PASS 시험 초안을 ROS DB에 예약한 뒤 상주 dispatcher가 시각 도래 시 이를 전송하고 `ACKED`·명령 ID·순번을 기록하는 컨테이너 통합 시험을 추가했다. 실제 VM에서의 관제 PC 화면 확인은 최종 배포 수락에 남긴다.
- 첫 왕복 구현의 [Ubuntu CI 실행 #39972520577](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39972520577)은 Linux 빌드·시험과 FDS 궤도 시험 모두 통과했다. 동일 시각의 여러 단계는 단계 ID 순서로 처리하도록 후속 수정했다.

### 2026-10-23 — MPS 시험 초안의 PASS 연속성

- 반복 FDS 예측에서 N13 해시와 순번형 PASS ID가 바뀌어도 같은 지상국의 AOS·LOS가 각각 5분 이내이고 접촉 시간이 절반 이상 겹치면 저장된 후보와 시퀀스를 현재 PASS에 표시한다. 다음 저장 시 초안의 리비전을 유지하며 최신 일정 해시와 PASS ID로 갱신한다. 오래된 클라이언트 해시 요청은 계속 거부한다.
- 다른 지상국, 인접하지만 겹치지 않는 PASS, 예측 구간에 잘린 PASS에는 초안을 자동 연결하지 않는다. 새 PASS가 짧아져 단계 오프셋이 범위를 벗어나면 초안을 계속 보여주되 재검토·수정 전 저장을 거부한다. MPS 화면의 선택 PASS도 ID만 보지 않고 같은 접촉인지 확인한다.

### 2026-10-23 — 가상 위성 TM→ROS/N11→FDS 비운용 연결

- 가상 위성은 TLE에서만 계산한 FDS truth 궤도를 읽고 5초마다 `EPS_PDU1`·`AOCS_STATE`·`GNSS_PVT` 초안 응용 PDU를 만든다. `sim-ground`는 Docker 내부 TCP 9200에서 길이 접두 프레임과 PDU 구조·시각·순번·값을 검증한다. 이 TCP는 호스트에 게시되지 않고 RF·CSP·AX100 장비 경로가 아니다.
- 검증된 EPS 전압·온도 및 AOCS 자세는 명시적 시험 한계와 함께 ROS 스냅샷으로 게시한다. GNSS는 첫 수신 및 이후 5분 간격으로 별도 N11 시험 본문·매니페스트가 되며 FDS가 재검증한다. 신선한 GNSS는 예비 수치 전파에 우선 사용하고, 없거나 72시간 이상 낡으면 합성 TLE/SGP4로 돌아간다. GNSS 기반 운영자 화면을 위성 truth에 되먹이지 않는다.
- FDS가 긴 TLE truth 계산 중 새 GNSS를 받아도 운영자 예측의 시작 시각을 새로 잡아 최신 GNSS가 미래 시각으로 오인되지 않게 했다. 검증된 N11 스냅샷도 현재 입력을 보존하며 최대 288개로 제한하고, 자세 각속도는 표시 쿼터니안과 같은 nadir 프레임의 시간 변화에서 계산한다.
- 동일 흐름의 오프라인 연결 점검 도구 `tests/run_synthetic_demo_smoke.py`를 추가했다. 실제 VM의 Compose 서비스, Docker 볼륨 소유권, 내부 통신과 브라우저 화면까지 포함한 통합 확인은 별도로 수행한다. 이후 MPS 일정 갱신 시 같은 PASS의 초안을 이어받는 보완을 적용했다.

### 2026-10-23 — 가상 PADO-SAT SSO를 기본 화면 시험 궤도로 선택

- 고도 약 500 km, LTAN 약 06시의 가상 SSO TLE를 `tools/build_synthetic_demo.py`에서 생성하고, 로컬 스택의 `fds-synthetic-demo`가 기존 FDS SGP4로 최대 72시간 예측한 결과를 ORBIT와 MPS에 게시하는 방향으로 전환했다. TLE epoch와 궤도 위상은 반복 실행 시각에 맞춰 갱신한다. 이 궤도는 실제 PADO-SAT 궤도결정 결과가 아니다.
- 실제 N05 TLE·N11 GNSS 입력과 합성 화면 시험은 분리한다. 기존 사용자 제공 ISS 고정 TLE는 별도 선택형 과거 시험 자료로 유지한다. 연세대·항우연 안테나 좌표, 최소 고각과 RF 예시는 계속 시험용이다.
- 초기 구현에서 N13 일정 해시가 다시 계산되면 MPS 시험 초안이 현재 목록에서 빠질 수 있었다. 위의 PASS 연속성 보완을 적용했다. 가상 TM 연결은 위의 시험 경로로 한정한다.

### 2026-10-23 — 단계 9 ROS·MPS 화면 구조 합의와 정리

- 사용자와 ROS `현황·경보 / TM·자세 / TC 준비·예약` 탭, MPS `PASS 선택 → 시험 TC 후보 구성 → 계획 검토` 한 화면 흐름에 합의했다. 이때의 화면 정리 범위는 기존 시험 기능과 상태 표시였다. 이후 명령 인자·AOS 기준 시각 오프셋 저장과 명시적 MPS→ROS `SIM ONLY` 전달·예약·가상 위성 ACK까지 확장했다. 현재 화면 기준은 [STAGE9_UI.md](STAGE9_UI.md)에 기록했다.
- ROS는 최신 시험 표본의 한계 초과와 선택 구간의 과거 이벤트를 분리한다. 게시 자료가 5분 이상 오래되거나 조회 실패하면 현재 경보 상태를 판정 불가로 표시한다. 현황의 초과 센서를 선택하면 TM 그래프로 이동한다.
- MPS는 두 지상국의 예측 PASS를 보여주되 UHF TC 초안은 연세대 PASS에서만 구성하도록 화면·저장 API를 함께 제한한다. 검토 영역은 후보 순서와 저장 리비전을 표시한다. 대전 S-band payload 수신은 현 범위 밖이다.
- 화면 자체의 브라우저 시각 점검은 브라우저 도구가 로컬 `file:` URL을 보안 정책으로 차단해 수행하지 못했다. HTML 구조, JS 구문, 기능 시험으로 검증하고 실제 화면 배치는 후속 환경에서 확인한다.

### 2026-10-23 — FDS 예측 전용 범위와 ROS 시험 TM 게시 연결

- FDS의 기동 입력을 허용하지 않는 설정 검사를 확인하고 산출물에 `maneuver_model=NONE`을 표시했다. 궤도 기동 계획·추력·기동 결과 계산은 범위 밖이다.
- 초안 TM PDU와 명시적 시험 한계표로 만든 스냅샷을 원자적으로 게시한다. ROS API는 파일 경로·유형·크기·스키마·시각·센서·쿼터니안을 재검증하고 로그인한 포털에만 읽기 전용으로 제공한다. 화면은 보이는 동안 5초마다 갱신하며 오래된 시험 표본과 연결 실패를 구별한다. 수동 파일·합성 예시도 별도로 유지한다.
- 실제 위성 TM 수신, 센서 보정·한계와 운용 경보는 CS/CDHS ICD와 검증 자료가 없어 연결하지 않는다. 개발 Compose에는 ROS API의 시험 파일 읽기 전용 마운트만 추가했다.
- 로컬 Python 시험 86개 통과(Windows에서 Linux C·OpenSSL 의존 12개 건너뜀), JS 구문 검사와 Git 공백 검사 통과. 인증된 포털→ROS 시험 스냅샷 통합 시험은 Linux CI에서 실행한다.

### 2026-10-23 — TC/TM 문서 정리와 비운용 연결 확대

- 네 그림과 한국어 설계·출처·미결 정리를 `docs/TC_TM/`에 함께 저장했다. PDR의 예비 질량·링크 버짓·고각 가정은 현재 TC/TM 설계와 FDS 기본 전파에 필요하지 않아 운용 설정에 반영하지 않는다.
- 초안 `GNSS_PVT` PDU 1개를 검사해 N11 본문·매니페스트로 게시하는 오프라인 브리지를 추가했다. 기존 N11 수신함의 SHA-256·유형·경로 검사를 통과하고 잘못된 CRC·fix를 거부한다. 실제 RF/CSP/OBC 수신 어댑터는 아니다.
- 초안 TM PDU와 **명시적으로 제공한 시험 한계표**로 ROS 화면의 `TEST_ONLY_TM_SNAPSHOT`을 만드는 도구를 추가했다. 한계표 없이 센서 임계값을 추정하지 않는다. MPS 웹은 초안 명령 58종을 조회하고, 필드가 정의된 36종의 이름/순서를 N13 PASS별 시험 후보에 저장한다. 운용 명령 DB·인자 입력·충돌·승인·송신은 계속 차단한다.
- 검증된 N13/N15 출고함의 세 파일 쌍을 목적지별 호스트키 고정 SFTP로 반복 전송하는 실행기를 추가했다. 실패 쌍은 재시도하고 성공 쌍은 목적지와 본문·매니페스트 해시에 묶인 로컬 **전송** receipt로 중복 호출을 피한다. 실제 수신측 ACK와 주소·계정·호스트키는 배포 입력으로 남긴다.

### 2026-10-23 — PDR 기반 UHF TC/TM 응용 패킷 초안

- 발표자료 65–69·73쪽, 별첨 27–30·47쪽, 제안서 181–184·194–197쪽에서 CS/CDHS 명령 함수·HK/비콘·UHF 링크 요구를 대조했다. 근거 필드 목록, 출처 해시 및 비행 규격과 다른 제안값을 `interfaces/pado_packets/`에 기록했다.
- 비운용 카탈로그에 TC 함수 58개, TM 계열 16개를 등록했다. 응용 PDU 200바이트 제한, 버전·종류·ID·순번·UTC·길이·초안 비트·CRC-32를 정의하고, 확정 근거가 부족한 명령 22개의 조립을 막았다. 2,034바이트 전체 HK는 불투명한 160바이트 청크로 분할·재조립하고 레코드 전체 CRC·완전성을 검사한다. 오프라인 코덱은 길이·CRC·예약 시각·필드 타입·TLE 체크섬을 검증한다.
- `GNSS_PVT` 초안의 ECEF 3축 위치·속도를 검증해 기존 FDS N11 본문으로 만드는 변환 함수를 추가했다. 실제 OEM719A 로그/위성 OBC TM은 아직 연결하지 않았으며 S-band payload 데이터 수신도 구현하지 않았다. 기존 운용 TC/TM DB와 게이트는 비활성으로 유지한다.

### 2026-10-23 — FDS N05/N11 수신 스냅샷 경계

- N05는 기존 호스트 키 고정·키 인증 C SFTP pull 도구를 실행해 검증된 파일 쌍만 가져온다. N11은 별도 SFTP push 수신함의 매니페스트로 완료된 정규화 GNSS 파일 쌍을 감시한다. 둘 다 경로·유형·해시·크기 확인 후 불변 디렉터리에 보관하고 원자적 `current.txt`로 FDS 입력을 교체한다.
- 새로 완료된 N11 쌍이 손상되면 `blocked.txt`로 계산 입력을 차단한다. 업로드 중 `.partial` 파일은 무시한다. N05 상대가 일시적으로 닿지 않으면 마지막으로 검증한 입력을 유지하며 FDS의 72시간 유효성 검사에 맡긴다. ISS 고정 시험 자료와 경로를 공유하지 않는다.
- 이 코드는 N11 원시 TM 해독기나 SFTP 서버 계정 구성, 실제 외부 DGS-NET 연결을 제공하지 않는다. 해당 항목과 현장 시험은 ICD·배포 설정이 준비된 뒤 남는다.
- 임시 키 전용 SFTP 서버를 사용하는 Linux 통합 시험에 N05 인증 다운로드→입력 스냅샷, N11 SFTP 업로드→입력 스냅샷, 잘못된 호스트 키와 전송 후 변조 거부를 추가했다. [Ubuntu CI #83](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39935358397)의 `linux-build-and-test`는 통과했다. 같은 실행의 FDS 궤도 작업은 시험용 GNSS 본문과 매니페스트를 서로 다른 폴더에 써서 실패했으며 그 시험 경로를 수정했다. 공개 NovAtel OEM719 `BESTXYZ`와 CubeADCS ICD를 다시 대조했으나, PADO-SAT OBC 다운링크 TM 매핑은 여전히 미정이다.

### 2026-10-23 — FDS N13 일정의 MPS 웹 읽기 연결

- FDS 반복 계산 후 N13 파일 쌍을 실행별 불변 디렉터리에 게시하고 현재 실행 ID를 원자적으로 교체한다. 기존 ISS 시험 화면의 별도 데이터와 섞지 않는다.
- 네트워크 없는 MPS C 서비스는 매 조회마다 N13 본문·manifest를 다시 읽고 SHA-256, 지상국, 패스 시간창을 검증한다. API는 검증된 최대 64개 패스를 포털로 전달하고 화면은 연세대 UHF·항우연 S-band 패스의 AOS/LOS·최대 고각·상세 정보를 표시한다. 자료가 없거나 손상됐을 때는 각각 입력 대기와 503으로 구별한다.
- 로그인한 planner/operator/lead는 패스별 시험용 후보 ID와 순서를 별도 SQLite 초안에 저장한다. N13 해시와 시간창, 예상 리비전을 매 저장마다 대조하고 변경 시 거부한다. auditor는 조회만 가능하다. 포털 세션·CSRF·내부 mTLS를 적용했다.
- 실제 TC 카탈로그·명령 파라미터·계획 승인·출력은 계속 차단한다. 로컬 게시와 읽기 경계는 실제 SFTP 상대 연결이나 운용 일정 유효성 판정의 대체재가 아니다.

### 2026-10-23 — ROS 시험 TM·자세·TC 예약 초안

- ROS에 시험 정규화 TM 파일/합성 표본의 센서 목록, 구간별 그래프와 한계 초과 목록, 쿼터니안으로 회전한 사각 기둥 자세 뷰를 추가했다. 실제 TM 패킷·센서 ICD와 기준 좌표계는 미정이며 CAD 메시 어댑터는 향후 확장점만 마련했다.
- MPS `TEST_ONLY_SEQUENCE` 본문과 매니페스트의 SHA-256·비운용 표시·PASS/단계 구조를 검사해 ROS의 별도 SQLite 초안 저장소에 등록한다. 로그인·CSRF·포털→ROS mTLS를 통과한 사용자만 단계별 패스 내부 예약 초안을 저장·취소한다. 예약은 타이머·승인·signer·TC 게이트·송출과 연결되지 않는다.
- MPS 웹의 오래된 FDS 형식 미정 문구를 현재 상태인 N13 파일 파서 구현·서비스 연결 대기로 바로잡았다. 실제 N13 서비스 반입·MPS 후보 영속화와 TC 명령 DB는 다음 개발 범위로 남는다.
- 검증: Python 단위 시험 54개 통과(호스트에 Linux C 코어가 없어 관련 9개 건너뜀), JS 구문 검사 및 `git diff --check` 통과. Linux TLS/컨테이너 통합 CI는 푸시 후 확인한다.

### 2026-10-23 — FDS N13 일정의 MPS 수신·TC 후보 구성

- FDS 반복 계산 성공 시 N13/N15 3개 파일 쌍을 검증해 고유 이름의 로컬 출고함으로 원자적 게시한다. Linux SFTP 단발 송수신 CLI가 기존 N05/N13/N15 경로 제한을 사용한다. 실제 상대 주소·인증과 반복 전송 호출은 배포에서 연결한다.
- MPS C 코어가 FDS `contact_schedule.csv` 또는 고유 이름의 출고함 파일과 매니페스트의 해시·경로·형식·시간·안테나를 검증해 두 지상국 PASS를 시간순으로 가져온다. 실제 Java FDS가 만든 TLE·GNSS 3일치 시험 일정도 로컬에서 읽었다.
- PASS별 순서 있는 TC 후보 ID를 비운용 리비전으로 편집한다. FDS 일정 해시 또는 PASS 시간창이 바뀌면 이전 계획 수정을 거부한다. 확정 TC 코드·파라미터·충돌 판정·승인·송출, 영속 저장과 웹 편집 API는 아직 연결하지 않는다.

### 2026-10-23 — FDS 로컬 입력 반복 계산 경계

- 별도 실행기가 N05 TLE와 정규화된 N11 GNSS의 매니페스트 경로·SHA-256·크기를 재검증하고 스냅샷을 만든 뒤 FDS를 계산한다. 입력 변경 시 또는 기본 15분마다 재계산하도록 폴링할 수 있다. 기본 게시 경로는 기존 ISS 시험 화면과 분리했다(결정 #26).
- 로컬 통합 시험에서 시험 N05 쌍으로 TLE SGP4를 계산·게시하고, 유효한 N11 GNSS 쌍을 함께 주었을 때 GNSS 예비 수치 전파를 우선 선택했다. N11 상태를 73시간 전으로 바꾸면 TLE로 전환했다. 각각 별도 시험 게시 경로만 사용했다. 실제 SFTP 수신, 운용 TM 해독, 안테나 실측값은 아직 연결되지 않았다.

### 2026-10-23 — FDS 기반 위성 위치 웹 화면

- 참고 추적 사이트의 3D 지구·시간 재생·컨택 표시를 확인했다. 참조 사이트에 표시된 Esri·Maxar 지구 이미지는 복제하지 않고 NASA Blue Marble 원본을 출처와 함께 로컬 자산으로 사용했다(결정 #24).
- FDS가 동일한 전파 결과에서 60초 ITRF/ECEF 위치·위경도·태양 방향과 두 안테나의 PASS 경계를 `orbit_view.json`으로 기록한다. 로그인한 포털의 `/orbit`은 3D 지구, 주야간, 2D 지상궤적, 향후 3주기 궤도선, 두 지상국 컨택과 다음 패스를 표시한다. 브라우저는 전파를 재실행하지 않고 표본을 보간한다.
- 게시 도구는 입력·예측의 현재 유효 기간과 FDS 결과 출처를 확인하고 파일을 원자적으로 교체한다. 실제 PADO-SAT TLE와 실측 안테나 위치, 자동 FDS 실행·게시가 없으므로 현재 운용 궤도는 아직 표시할 수 없다.

### 2026-10-23 — 고정 ISS TLE 화면 시험

- 사용자 제공 ISS (ZARYA) TLE 두 줄을 별도 시험 파일에 원문 그대로 저장하고 길이·체크섬을 확인했다. 기존 FDS SGP4 계산으로 72시간 `orbit_view.json`을 만들고 시험 포털에 게시했다. 실제 PADO-SAT N05/N11 입력 경로와 분리했다(결정 #25).
- `/orbit`의 재생·타임라인·2D 전환을 제거했다. 현재 UTC의 3D 지구와 2D ground track을 한 화면에 동시에 표시하고, 연세대/대전 지상국에는 현재 또는 다음 PASS의 polar 경로를 그린다. 화면은 서버 결과를 60초마다 확인한다.
- 안테나 좌표·마스크·주파수는 시험용 근삿값/예시값으로 명시했다. ISS TLE epoch 이후 72시간을 넘기면 시험 현재 위치가 숨겨진다. 실제 PADO-SAT TLE/GNSS 자동 수신과 FDS 재계산·게시 및 실측 좌표 연결은 후속 과제다.

### 2026-10-23 — FDS 궤도 계산 코어 우선 개발

- 사용자 결정: GNSS TM이 유효하고 계획 시작 시점 기준 72시간 미만이면 GNSS 기반 예비 수치 전파를 사용하고, 그동안 수신되지 않았으면 TLE SGP4를 백업으로 사용한다. 연세대 이과대학 UHF와 대전 항우연 S-band 각 1기의 패스·도플러·궤도력을 최대 72시간 계산한다. 주파수는 아직 미정이므로 설정·예시 표시로 분리한다(결정 #23).
- OEM719 `BESTXYZ`와 CubeADCS 공개 ICD를 확인해 위치·속도는 ECEF 계열 벡터이고 자세 쿼터니안은 별도 자료로 취급한다. 실제 TM 필드·전달 방식은 미정이므로 엄격한 시험용 정규화 GNSS 상태만 수용한다.
- Java 17/Orekit 13.1.9 오프라인 코어와 Maven 빌드, UTC·ITRF/GCRF 변환, 지구 중력장+태양·달 중력의 예비 수치 전파, TLE SGP4, 두 안테나의 AOS/LOS·최고 고각·10초 도플러·60초 궤도력과 N2 유형별 매니페스트를 구현했다. 힘 모델·정밀도 목표가 없으므로 운용 HPOP 결과로 승인하지 않는다.
- 로컬 확인: 공식 Orekit 물리 자료와 공개 Vallado TLE의 3일 TEME 위치 벡터 비교, 입력 오류·72시간 소스 전환·두 안테나 산출물·SHA-256 매니페스트 시험 통과. TLE와 합성 GNSS 상태 각각 72시간 계산 시 궤도력 4321개 샘플을 생성했다. Linux CI 결과는 별도 확인한다.

### 2026-10-23 — 단계 9-2 상주 C 읽기 서비스 경계

- ROS·MPS API가 요청마다 C 프로브를 실행하던 경로를 별도 상주 C 프로세스와 전용 Unix 소켓으로 전환했다. 각각의 C 프로세스는 시뮬레이터 기록 또는 비운용 명령 DB를 매 요청마다 재검증한다. 소켓은 UID 10001 전용이며 읽기 서비스는 네트워크 인터페이스 없이 실행한다.
- API는 제한 시간·프레임 크기·응답 스키마를 검사한다. 손상 원본·서비스 중단은 정상 자료로 바꾸지 않고 오류로 표시한다. 현재 C 프로세스는 읽기 어댑터이며 실제 ROS 수신기·MPS 계획 실행기나 TM 센서 처리 기능은 아니다(결정 #22).
- 검증: 로컬 Python 시험 39개 통과(환경 의존 7개 건너뜀). [Ubuntu CI #51](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39903848929)에서 GCC Release, Clang ASan+UBSan, 상주 C 소켓·HTTPS 포털 통합, 정적 분석·퍼징·기존 격리 시험, Stage 9 이미지 빌드와 ROS/MPS API 컨테이너에서 각 C 읽기 서비스 소켓 실제 조회가 모두 성공.

### 2026-10-23 — 단계 9-1 비운용 C 코어 읽기 연결

- ROS API는 단계 3 시뮬레이터 기록을 C 프레임 검증기로 확인해 시험 프레임 수와 마지막 순번을 반환한다. MPS 명령 목록 API는 기존 C 명령 DB 파서가 확인한 버전과 빈 목록을 반환한다. 기록 부재는 0건, 손상 파일이나 잘못된 명령 DB는 실패로 처리한다.
- 다단계 컨테이너 빌드에서 C 프로브를 컴파일하고 ROS 기록 볼륨을 API에 읽기 전용으로 연결했다. 현재 Compose에는 해당 볼륨에 기록하는 ROS 코어 서비스가 없다. TC/TM·FDS ICD, 계획 영속 저장, 관제 PC 접속 시험은 여전히 미완료이며 단계 9 전체 완료로 판정하지 않는다(결정 #21).
- 검증: 로컬 Python 38개 시험 중 환경 의존 6개를 제외하고 통과. [Ubuntu CI #47](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39902404739)에서 GCC Release, Clang ASan+UBSan, C 프로브 유효·변조 거부 및 포털 통합 시험, 정적 분석, 기존 퍼징·격리 시험, Stage 9 컨테이너 빌드 전체 성공.


### 2026-10-23 — 단계 9 포털·ROS/MPS 웹 경계 개발 진행

- Argon2id 계정, lead TOTP, 재사용 거부, 잠금, 유휴·절대 세션 만료, HttpOnly/Secure/SameSite 쿠키와 CSRF 로그아웃을 구현했다. 초기 계정이나 비밀번호는 저장소에 두지 않는다.
- 별도 ROS/MPS HTTPS API는 포털의 mTLS 클라이언트 인증서를 요구한다. 포털은 로그인한 사용자·역할만 내부로 전달한다. 개발용 Compose에서 백엔드는 내부 브리지에만 연결하고 포털은 VM 루프백만 게시한다.
- ROS/MPS 화면은 비운용 상태·빈 명령·미정 FDS 일정을 읽기 전용으로 보여준다. 현재 C 코어 운용 서비스가 없으므로 실제 승인·송출·업링크·시퀀스 출력 API는 없다. 단계 9 전체 완료로 판정하지 않는다(결정 #20).
- 검증: [Ubuntu CI #44](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39899833305)에서 GCC/Clang·기존 CTest, Argon2id·TOTP·세션·TLS/mTLS·직접 API 거부 Python 시험, 단계 7 컨테이너 시험, 단계 9 Compose 구성·이미지 빌드 전체 성공. Windows 루프백 HTTPS 통합 시험도 통과했다.

### 2026-10-23 — NAS 연동 현 개발 범위에서 제외

- 사용자 결정: 장비·도입 시기 미정인 NAS는 단계 1–9와 현 배포 수락 범위에서 제외한다. 서비스 파일·매니페스트·SHA-256 계약과 로컬 영속 경로를 유지해 추후 장비 선정 시 별도 저장소 연동을 추가한다(결정 #19).
- 현재 로컬 스냅샷은 개발용 복원 시험이며 서버 전체 상실에 대비한 운용 백업은 아니다. 운용 배포에는 NAS와 무관한 독립 백업·복원 방안을 따로 정하고 검증해야 한다.

### 2026-10-23 — 단계 8 백업·복구·파서 퍼징 개발 기준선 완료

- 명시적 파일 목록만 포함하는 로컬 시험 스냅샷 도구 `tools/stage8_snapshot.py` 추가. 외부에 따로 기록한 매니페스트 SHA-256을 요구하고, 경로 탈출·키 경로·심볼릭 링크·변조 파일·기존 복원 목적지 덮어쓰기를 거부함. 암호화·NAS·실행 중 DB 덤프는 제공하지 않음.
- 복원된 비운용 명령 DB·TM DB·N2 파일을 실제 MPS·단계 6 C 시험 바이너리가 판독하고 변조된 스냅샷 복원을 거부하는 통합 시험 추가.
- 매니페스트·MPS 명령 목록 기존 libFuzzer 대상에 합성 TM 프레임·NORAD TLE 대상을 추가. 유효·빈 시드로 각 대상의 짧은 ASan+UBSan 캠페인을 Linux CI에 등록함. 실제 TM ICD 파서는 아직 없음.
- 검증: 로컬 Python 25개 시험 및 N2 검사 통과. [Ubuntu CI #40](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39897193087)에서 GCC/Clang 빌드·CTest, 복원 후 C 파서 판독·변조 거부, 네 파서 ASan+UBSan 퍼징, 정적 분석, 기존 Docker·SFTP·TC 보안 회귀 시험 전체 성공.
- 판정: 단계 8 **개발 기준선 완료**(결정 #18, #19). 독립 백업·복원 방안과 전체 서버·FlatSat 복구는 최종 배포 수락 항목이며 NAS 연동은 별도 후속 과제.

### 2026-10-23 — 단계 7 개발 기준선 완료, 현장 적용 이관

- 사용자 결정: 실제 Linux VM에 운용 서비스와 설정을 적용하는 작업은 모든 개발 후 배포 단계에서 수행한다(결정 #17).
- 완료 범위: N2 18개 확정 연결 계획·미정 3개 비개방, Docker 내부 연결 쌍/비연결 차단, journald 로그 probe, VMware VM UFW·NTP 기준선, 시뮬레이터 DGS-GS·ROS 앱 경계 시험, 서버 설정 매뉴얼. [Ubuntu CI #39](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39896254234) 통과.
- 배포 수락으로 이관: 운용 이미지·권한·영속 볼륨·비밀정보, 모든 N2 경로의 서비스 수준 방향 강제, 다른 출발지 SSH 차단, 로그 보존·재부팅·복구, 외부 ICD·SDC·Space-Track 적용. 단계 7 완료는 운용 배포 완료가 아님. NAS는 결정 #19에 따라 범위에서 제외.

### 2026-10-23 — 단계 7 단일 VM Docker 배포 검증 진행

- 서버 적용 매뉴얼: `deploy/stage7/SERVER_SETUP.md`에 현장값 기록, Docker 설치 확인, UFW·SSH 적용, N2 probe·호스트 감사, journald·NTP, 운용 서비스 배포 선행 조건, 재부팅·복구 판정 절차를 추가. 실제 서비스가 준비되기 전 probe를 운용 배포로 오인하지 않도록 구분함
- 호스트 감사 보완: `docker ps`/`docker inspect` 실패 또는 불완전한 목록을 공개 포트 0개로 통과시키지 않고 `docker_inventory_complete=false`와 오류를 보고하도록 수정
- 실제 앱 프로세스 경계 시험 추가: `tests/container/run_stage7_tm.py`가 제한된 DGS-GS·ROS 컨테이너 안에서 기존 `--sim-only` 실행기를 돌려 정상 TM 기록 2건, 오류 격리 5건, 비허용 상대 IP 거부를 확인함. [Ubuntu CI 실행 #38](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39895951024) 전체 성공. 시험 이미지는 운용 이미지로 취급하지 않음
- 구조 변경: 실제 운영도 Linux VM 한 대의 서브시스템별 Docker 컨테이너로 확정. 이전 다중 VM·VLAN·vm-mgmt 설계는 배포 기준에서 제외(결정 #16)
- 구현: N2의 확정된 연결 시작 방향 18건을 검사하는 `tools/check_flows.py`, 내부 연결 쌍 12개를 별도 비공개 Docker 브리지로 만드는 일회용 probe와 호스트 읽기 전용 감사 도구 추가. 미정 외부 ICD 경로와 공개 포트는 생성하지 않음
- 실제 VMware VM 시험: Docker 29.6.1, 시간 동기 `yes`; 연결된 컨테이너 쌍의 양방향 도달 24건, 연결되지 않은 쌍 거부 18건, 공개 포트 0개, 컨테이너 7개의 journald 시작 로그 확인. Docker 기본 로그 드라이버는 `json-file`이며 probe는 명시적으로 `journald` 사용
- 호스트 방화벽: 사용자가 VM 콘솔에서 UFW를 활성화하고 기존 80·2900·전역 22 허용을 삭제. 최종 기본 정책은 입력·라우팅 차단, 출력 허용이며 `192.168.45.1 → 22/tcp`만 허용. 관리 PC에서 SSH 22 연결 성공, 일시적 2900/tcp 리스너를 실행한 상태에서 2900 연결 거부 확인. UFW 활성화 후 Docker 격리·로그 시험을 다시 실행해 같은 결과를 확인. 시험 리스너와 Docker probe 컨테이너·네트워크는 제거됨
- CI: [Ubuntu 실행 #34](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39894144455)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python 17개, 비밀정보 검사, 단계 2 및 단계 7 Docker 네트워크 시험 성공
- 남은 검증: 다른 출발지의 SSH 거부, 실제 앱 컨테이너 이미지·권한·영속 볼륨·비밀정보 및 시작/복구, N2 연결 방향의 서비스 수준 강제, 로그 보존과 재부팅 복구. 브리지 쌍은 역방향 신규 연결을 차단하지 않으므로 현재 단계 7을 완료로 판정하지 않음


### 2026-10-23 — 단계 6 내부 교환 비운용 기준선 완료

- 구현: N05·N08–N18의 데이터·연결 방향과 유형을 N2 기준표에 대조하는 SFTP 경로표, NORAD TLE 구문·체크섬·위성 ID 검사, Archive 반입·메모리 카탈로그·SDC/MPS 목적지별 매니페스트 생성
- 시험: Windows Release·ASan CTest 각각 6/6, Python 10/10. Linux SFTP 시험에서 12개 경로의 파일 쌍 송수신과 SDC→Archive→MPS 릴레이를 확인. [Ubuntu CI 실행 #31](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39891674446)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 경계: 외부 Space-Track·발사체 연동, Java/Orekit 궤도 계산, FDS 산출물 본문, NAS 영속 저장, VM 계정·방화벽 설정은 아직 구현되지 않음
- 결정: DECISIONS.md #15, `interfaces/stage6/test_contract.md`

### 2026-10-21 — 단계 5 합성 TC 검증·승인·게이트·ACK 기준선

- 구현: 단계 4 시험 시퀀스의 ROS 재검증과 시험 명령 존재·정수 파라미터·스케줄·시간창·상태 조건, 일반/주의/위험 승인 분기, 런타임 시험 키를 받는 signer, tc-gate 형식·SCID·출처·시간·속도 제한, `fake_sat` HMAC·재전송 검사와 ACK
- 시험: 위험 TC 자가 승인·잘못된 역할·재인증 누락, 다섯 자동 검증 항목의 실패, signer 미승인, 게이트 출처·길이·SCID·버전·패스 차단, 태그 변조·재전송 거부 및 정상 ACK. Windows Release·ASan CTest 각각 5/5, Python 10/10 통과. [Ubuntu CI 실행 #27](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39616614345)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 경계: 실제 TC ICD·프레임·HMAC 사양 및 Portal/ROS/DGS 서비스 프로세스 연결이 없어 합성 계약에만 적용. 실제 항우연·위성으로 전송하는 코드 경로는 없음
- 결정: DECISIONS.md #14, `interfaces/tc/test_contract.md`

### 2026-10-21 — 단계 4-4·4-5 비운용 승인·ROS 검증·반입 기준선

- 구현: 최신 초안·패스·DB 버전·충돌 결과에 결합한 별도 lead 시험 승인, `TEST_ONLY_SEQUENCE`와 SHA-256 매니페스트 출력, ROS 별도 파서의 파일 쌍·의미 재검증. SDC→DGS-Archive→MPS 요청·업링크 매니페스트 체인, 반영·반려 판정, 독립 해시·목적·버전·FlatSat 기록 ID·역할 검사
- 시험: 승인 전·오래된 승인·자가 승인·재인증 누락·운용 출력명·해시 변조·매니페스트를 다시 만든 의미 변조·출처/버전 불일치·FlatSat 기록 누락·반입 후 파일 변조 거부. Windows Release·ASan CTest 각각 4/4, Python 10/10, 비밀정보 검사 105개 파일 통과. [Ubuntu CI 실행 #24](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39614494249)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
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
