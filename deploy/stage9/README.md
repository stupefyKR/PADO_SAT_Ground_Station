# 단계 9 웹 개발 기준선

## Windows 관제 PC에서 VMware 서버 HTTP로 확인

사용자 검증 환경은 Windows 관제 PC와 VMware Linux 서버가 같은 시험용 사설망에
있는 구성이다. `compose.local.yaml`은 이 환경에서 **HTTP** DGS 관제 진입점
`dgs-ops-gateway`만 VM 주소 `192.168.45.128:8080`에 게시한다. 이 진입점은
고정된 내부 `portal:8080`으로만 TCP를 중계한다. 포털·ROS·MPS API는 Docker
내부망에서만 접속되며, Compose가 생성한 비공개 토큰을 가진 포털 요청만 받는다. 로그인,
역할 검사, 세션과 CSRF 검사는 HTTP 시험에서도 유지된다. 기존 TLS 시험 구성인
`compose.dev.yaml`과는 별개다.

VM에 **Docker Engine 28 이상**, Compose 플러그인, Python 3과
`iptables`가 설치돼 있다면 저장소를 clone하고
이번 작업 브랜치를 선택한 뒤 저장소 루트에서 실행한다. 스크립트의 `start`는
이미지를 빌드하고, 관제 PC에서 DGS 진입점으로 오는 트래픽에만 접근 규칙을 설정하고,
컨테이너를 올리고 포털 응답을 확인한다. 처음 실행해 계정 DB가 비어 있으면 사용자 이름
`planner01`·역할 `planner`의 비밀번호를 대화식으로 두 번 입력받는다. 비밀번호를
명령행 인자나 Git 파일에 넣지 않는다. 이후 `start`에서는 저장된 계정을 유지한다.
첫 빌드는 고정된 Orekit 데이터와 Maven 의존성을 받으므로 VM의 빌드 네트워크가
필요하다. 이는 이미지 준비 과정의 통신이며, 실행 중 FDS 컨테이너는 외부
네트워크에 연결되지 않는다. Engine 28 미만에서는 분리된 내부 브리지의
`isolated` 모드를 보장하지 않아 스크립트가 시작을 거부한다.

```sh
git clone https://github.com/stupefyKR/PADO_SAT_Ground_Station.git pado-gs
cd pado-gs
git switch feat/fds-core
./pado-gs.sh start
```

기본 바인드 주소는 VM의 `PADO_LOCAL_BIND_IP=192.168.45.128`, 허용할 관제 PC
주소 범위는 `PADO_OPERATOR_CIDR=192.168.45.1/32`이다. VM 주소 또는 관제 PC의
**VM에서 보이는 출발지 주소**가 다르면 실행 전에 각각 실제 값으로 지정한다.
`PADO_LOCAL_BIND_IP`는 VM에 할당된 관제망 IPv4여야 한다. 예를 들어:

```sh
PADO_LOCAL_BIND_IP=192.168.45.128 \
PADO_OPERATOR_CIDR=192.168.45.1/32 \
./pado-gs.sh start
```

`start`는 관제 PC 주소 범위를 호스트 방화벽과 DGS 진입점 양쪽에 동일하게
설정한다. DGS 진입점은 연결 상대의 IPv4가 이 범위에 없으면 포털에 연결하지
않는다. 기본값은 관제 PC 한 대(`192.168.45.1/32`)만 허용한다. VM의 Docker
포트 게시 방식이 실제 클라이언트 주소를 다른 주소로 바꾸면 접속은 거부된다.
이 경우 허용 범위를 무작정 넓히지 말고 VM에서 관측되는 주소와 게시 경로를
확인해야 한다.

### DGS 외부 통신 경계

실행 중 호스트 포트와 일반 Docker 브리지(`entry`)에 연결되는 서비스는
`dgs-ops-gateway` 하나다. 게이트웨이는 `dgs_portal` 내부망을 통해 포털에만
접속한다. 포털은 별도 내부망으로 ROS·MPS API에 연결되며, 가상 위성 수신기는
`sim_tm` 내부망에서만 통신한다. FDS와 C 읽기 서비스는 네트워크가 없다.
내부망에는 Docker Engine 28의 `isolated` IPv4 게이트웨이 모드를 적용하고
IPv6을 비활성화한다. 이 구성에서 DGS 관제 진입점은 **내부 관제 PC의 웹 접속
경계**이며, 향후 외부 TLE·지상국 데이터 수집용 DGS-GS/NET 구현과는 별개다.

`./pado-gs.sh start`는 Compose의 유효 설정, 실제 Docker 네트워크와 소속
컨테이너를 검사한다. `DOCKER-USER`에는 지정한 관제 PC의 게시 포트 접근만
허용하고, 직접 컨테이너 IP 접근과 게이트웨이의 신규 외부 연결을 차단하는
규칙을 설치한다. 호스트 `INPUT`에도 게이트웨이에서 VM 자체로 여는 신규
연결을 차단하는 규칙을 설치한다. 정책을 설치하거나 확인할 수 없으면 시작을
중단한다. `status`도 정책이 없거나 순서가 달라지면 오류를 반환한다.
정적 검사는 승인된 서비스·네트워크 소속과 서비스별 named volume의 출처·컨테이너
경로·읽기 모드와 Compose 프로젝트에 속한 실제 볼륨 이름을 정확히 비교한다.
다른 서브시스템의 데이터 볼륨이나 Docker 소켓,
외부·호스트 경로 기반 볼륨과 서비스·컨테이너 간 네임스페이스 공유는 거부한다.
로컬 구성의 호스트 bind mount는 거부하고, 개발용 TLS 구성의 지정된 인증서·시험
자료 bind mount는 읽기 전용으로만 허용한다.
UFW의 SSH 규칙은 스크립트가 변경하지 않는다.
기동 확인은 포털 내부 HTTP 응답과 DGS 진입점에서 `portal:8080`으로의 DNS·TCP
연결을 검사한다. 관제 PC에서 게시 포트까지의 실제 경로는 Linux 통합 시험과
VM 브라우저 접속으로 별도 확인한다.

관제 PC 접근 시험에는 `compose.local.yaml`을 **`./pado-gs.sh`로만** 시작한다.
`docker compose -f deploy/stage9/compose.local.yaml up`을 직접 실행하면
호스트의 접근·송신 차단 규칙을 설치하지 않으므로 DGS 전용 통신 정책을
충족하지 않는다. 게이트웨이의 상대 주소 검사는 이 우회 시에도 적용되지만,
호스트 방화벽의 대체 수단은 아니다. `compose.dev.yaml`은 개발자 VM의
`127.0.0.1:8443`에만 게시하는 별도 TLS 구성이다.

Windows에서 별도 SSH 포워딩 없이
`http://192.168.45.128:8080/login`에 접속해 로그인한다. ROS는 `/ops`,
MPS는 `/mps`, 가상 PADO-SAT 위치는 `/orbit`이다. 화면은 동일 출처의 인증된
API를 호출한다. `fds-synthetic-demo`가 고도 약 500 km, LTAN 06시의 합성 SSO
TLE를 사용해 15분마다 FDS를 계산하고 ORBIT 보기와 N13 PASS 일정을 공유
Docker 볼륨에 게시한다. 실행 때마다 TLE epoch를 현재 UTC 약 2초 전으로 갱신하며 예측
기간은 최대 72시간이다. 이는 실제 PADO-SAT 궤도나 운용 TLE가 아닌 화면·API
연결 시험 자료다. MPS의 선택 PASS 지도·polar plot과 시험 시퀀스도 이 자료를
사용한다. 초안 저장은 테스트 전용이다.
MPS Ground view는 선택한 PASS 전후 각 두 궤도 주기의 FDS 지상궤적을 함께
표시하고 교신 구간을 강조한다. FDS 표본 범위를 벗어나는 구간은 표시하지 않으며,
Polar plot은 선택한 PASS만 보여준다.

ORBIT의 위성 선택기는 가상 PADO-SAT, ISS 외삽 시험, 실제 운용 위성을 구분한다.
ISS 화면은 사용자가 제공한 고정 TLE를 외삽해 현재 epoch에 재적합한 **가상 궤도**를
별도 `fds-iss-display` 컨테이너에서 30분마다 계산한다. 실제 ISS 위치가 아니며
MPS N13 일정이나 가상 위성 TM 입력에는 섞이지 않는다. 실제 운용 위성은 검증된
N05/N11 입력과 별도 FDS 산출물 연결 전까지 `입력 대기`로 표시한다. MPS의
`N13 검증 완료`는 MPS 코어가 일정 파일을 읽었다는 뜻이며, 조회 API 장애와
FDS 산출물 대기는 화면에서 따로 표시한다.

### 가상 위성 TM과 FDS 입력 흐름

```text
fds-synthetic-demo ── TLE 기반 truth 궤도 ──→ sim-satellite
         │                                  │ 5초마다 EPS_PDU1 / AOCS_STATE / GNSS_PVT
         │                                  ▼
         │                        sim-ground (내부 TCP 9200)
         │                         ├─ 검증·파싱 → ROS 시험 TM 스냅샷
         │                         └─ GNSS → 시험 N11 파일 쌍 (최초 및 5분 간격)
         └─ 검증된 시험 N11 재반입 → GNSS 우선 FDS 예측 → ORBIT + MPS N13
```

`sim-satellite`는 FDS가 TLE로 만든 **별도 truth 파일**만 읽는다. 이후 GNSS로
다시 계산한 운영자 화면 파일을 위성 위치의 원천으로 되먹이지 않는다.
`sim-ground`는 truth 궤도의 연세대 UHF PASS(AOS 이상, LOS 미만)에서만
TM을 수신하고 시험 TC를 전달한다. PASS 밖에서는 생성 중인 TM을 폐기하며,
truth 파일이 없거나 잘못됐거나 오래됐을 때도 수신을 닫는다. 수신한 PDU의
길이·시험 표시·종류·시간·순번·GNSS 값·자세 쿼터니안을 확인한 뒤 ROS용
스냅샷을 게시한다. 화면의
EPS 버스 전압·온도 한계는 `services/sim_ground/test_limits.json`의 명시적인
시험값이다. GNSS는 N11 시험 본문·매니페스트로 게시해 FDS가 검증 후 읽는다.
FDS는 GNSS가 계획 시각 기준 72시간 미만이면 이를 우선 사용하고, 없거나
72시간 이상이면 가상 TLE로 복귀한다. 현재 반복 주기에서는 N11이 게시된 뒤
다음 FDS 계산까지 최대 약 15분이 걸릴 수 있다.
시험 N11 파일 쌍과 FDS가 검증한 해당 입력 스냅샷은 각각 최근 288개(약 24시간),
완료된 가상 FDS 실행 묶음은 최근 8개를 보존해 장시간 실행 시 볼륨 증가를 제한한다.
`sim-volume-init`는 첫 실행과 재시작 때 FDS·TM 공유 볼륨의 최상위 디렉터리를
컨테이너 UID 10001에 맞춘다. 기존 FDS 실행 결과의 비공개 하위 디렉터리는
변경하지 않는다.

### MPS → ROS → 가상 위성 TC 왕복 시험

`/mps`에서 미래의 **연세대 UHF PASS**에 읽기 전용 초안 명령을 저장한 뒤
`ROS 시험 초안으로 전달`을 누른다. 포털은 저장된 일정 해시·리비전을 MPS에
재확인하고 그 결과만 ROS에 전달한다. `/ops`의 `03 TC 준비 · 예약` 탭에서
별도 `SIM ONLY` 영역의 단계를 선택해 `가상 위성 예약`을 누르면 AOS와 저장된
오프셋으로 실행 시각을 정한다. 예약 때에도 포털이 MPS의 최신 일정과 저장된
리비전을 다시 확인하므로, 일정이 바뀐 이전 초안은 새로 전달해야 한다.
전송 시각에는 ROS API가 초안 TC PDU를 만들어
공유 볼륨의 비공개 Unix socket으로 `sim-ground`에 보낸다. `sim-ground`가
Docker 내부 TCP 9200의 기존 가상 위성 연결로 전달하면 `sim-satellite`가
`COMMAND_RESULT` TM PDU로 응답한다. ROS는 명령 ID·순번이 일치하는 응답을
검증해 예약 표에 ACK 또는 거절을 표시한다.

```text
MPS 저장 시퀀스 → 포털에서 재검증 → ROS SIM ONLY 예약
  → Unix socket (ROS API ↔ sim-ground) → 내부 TCP 9200 → sim-satellite
  ← 검증된 COMMAND_RESULT TM ←───────────────────────────────────┘
```

첫 왕복은 `mm_get_mode`, `hk_get`, `eps_get_hk`, `aocs_get_hk`, `cs_get_hk`,
`siren_get_hk`, `crest_get_hk`와 같은 **읽기 전용** 초안 명령만 허용한다.
예약은 해당 PASS의 미래 시각에만 만들 수 있고, 예정 시각에서 30초 넘게
지나거나 LOS가 끝나면 `MISSED`가 된다. 전송 뒤 응답을 확정하지 못한 예약은
자동 재시도하지 않으며 재시작 중 전송 상태였다면 `UNKNOWN`으로 남는다.
기존 파일 쌍 가져오기 및 `DRAFT_ONLY` 예약은 별도 검토 경로이며 자동 실행되지
않는다. 이 시험은 실제 위성의 인증·승인·명령 충돌 검사·RF 송출 기능이 아니다.
가상 위성의 TCP 연결은 상시 유지되므로 성공 ACK는 응용 패킷 왕복의 증거다.
실제 안테나 가시성이나 무선 링크 성공을 증명하지는 않는다.

관제용 DGS 진입점 외에 호스트에 게시된 포트는 없다. TCP 9200은 `sim_tm` Docker 내부망에서
가상 위성과 시험 수신기 사이에만 사용한다. 이 경로는 **RF·CSP·AX100 프레임,
실제 항우연/연세대 수신 장비, 위성 TC 송출**을 구현하지 않는다. 실제 N05/N11
수신 경계도 이 시험 볼륨과 분리돼 있다.

VM에서 시작한 뒤 컨테이너 상태와 로그를 확인하려면:

```sh
./pado-gs.sh status
docker compose -p pado-stage9-local -f deploy/stage9/compose.local.yaml \
  logs --tail=100 fds-synthetic-demo sim-satellite sim-ground ros-api mps-api
```

처음에는 TLE 결과가 게시되고 가상 TM 수신과 GNSS 파일 생성이 뒤따른다.
Windows 브라우저에서 `/orbit`의 데이터 출처가 `TLE`에서 `GNSS`로 바뀌는지,
`/ops`의 `02 TM · 자세`에서 계측·자세가 갱신되는지, `/mps`에서 PASS와
ground view·polar plot이 표시되는지 확인한다. 실제 VM의 Docker 볼륨·권한·
내부망·브라우저까지 포함한 연결 시험은 별도 통합 검증으로 기록한다.

VM의 사설망 인터페이스가 Windows에서 접속 가능한지와 Docker 포트 `8080`이
해당 인터페이스에만 게시됐는지 확인한다. Docker 게시 포트는 호스트 UFW의
일반 입력 규칙을 우회할 수 있으므로 위의 전용 정책을 적용해야 한다.
이 Compose는 외부망에 연결된 인터페이스에서 사용하지 않는다.
기본 합성 TLE의 epoch는 반복 계산 때 갱신한다. 별도로 보관한
ISS 고정 TLE는 **2026-10-25 22:03 UTC 이후** 72시간 신선도 조건을 충족하지
못하며 현재 위치 시험의 기본 입력으로 사용하지 않는다. 유효한 새 FDS 산출물이
없거나 예측 범위를 벗어나면 ORBIT는
현재 위치를 표시하지 않고 MPS 패스 경로도 유효 자료가 없다고 표시한다.

```sh
./pado-gs.sh stop
./pado-gs.sh start
./pado-gs.sh status
./pado-gs.sh cleanup
```

`stop`은 이 프로젝트의 컨테이너를 멈추고 스크립트가 만든 DGS 경계 규칙만
제거한다. 계정, 시퀀스 초안, FDS 결과 등 이름 있는 Docker 볼륨과 이미지는
남으므로 다음 `start`에서 재사용한다. `cleanup`은 `DELETE` 입력을 확인한 뒤 프로젝트
`pado-stage9-local`의 컨테이너·네트워크·이름 있는 볼륨·로컬 빌드 이미지를
제거한다. **계정 DB, 저장한 초안, 내부 토큰, 게시된 궤도·PASS 결과와 ROS
시험 기록도 삭제된다.** 비대화식 실행이 필요한 경우 `cleanup --yes`를 사용할
수 있다. 저장소 파일과 Git 이력은 삭제하지 않는다.

## Windows에서 화면 배치만 빠르게 확인

저장소 루트에서 `python tools/build_stage9_preview.py`를 실행한 뒤
`out/stage9-preview/ros-preview.html` 또는 `mps-preview.html`을 브라우저로 연다.
CSS와 합성 예시는 HTML에 포함되어 있어 Docker·계정·인증서 없이 볼 수 있다.
이 미리보기는 레이아웃 확인 전용이며 서버 API, FDS 일정, 실제 TM·TC와
연결되지 않는다. 아래 Compose 절차는 로그인과 내부 API를 포함하는 별도의
Linux 개발 환경이다.

현재는 **비운용 수직 경로**다. `portal`은 Argon2id 계정·TOTP·세션·
CSRF·계정 잠금·로그인 속도 제한을 제공하고 정적 ROS/MPS 화면을 제공한다.
`compose.local.yaml`에서 `ros-api`와 `mps-api`는 각각 별도 내부 네트워크의
HTTP 서비스를 제공하고, 포털이 보유한 내부 토큰을 검사한다. 포털은 로그인한
사용자와 역할을 전달하고 클라이언트가 보낸 동일한 헤더는 전달하지 않는다.
별도 `compose.dev.yaml` 구성에서는 내부 API에 mTLS와 인증서 SAN 검증을 쓴다.

ROS는 `SIM_ONLY`와 TC 송출 차단 상태를 표시하고, MPS는 비운용 명령 초안과
FDS PASS를 이용해 시험 시퀀스를 구성한다. FDS N13 일정 파일은 C 코어에서 검증해 메모리 계획에
가져와 검증하고 이 개발 Compose의 MPS 웹 서비스에 읽기 전용으로 제공한다.
**TC 송출, 승인, 업링크 반입, 운용 시퀀스 출력, 실제 TM 파라미터 조회 API는
존재하지 않는다.** 단계 9 전체 완료로 판정하지 않는다.

`/orbit`은 FDS 계산 프로세스가 만든 `orbit_view.json`을 세션 인증 뒤 읽어
동시에 보이는 3D 지구와 2D 지상궤적, 주야간, 향후 3주기 궤적과 두 지상국의 PASS polar plot을
표시한다. 지도에서는 TLE나 GNSS 상태를 다시 전파하지 않는다. 지구 텍스처는
로컬 NASA Blue Marble이며 출처는 `web/assets/README.md`에 있다.
`compose.local.yaml`의 `fds-synthetic-demo`가 15분마다 합성 TLE의 epoch를
갱신해 시험 계산을 수행하고 동일한 실행에서 ORBIT 파일과 N13 PASS 파일을
이름 있는 Docker 볼륨에 게시한다.
포털과 MPS는 각각의 읽기 API를 통해 표시한다.
FDS 계산 실패나 게시 중단으로 유효 기간이 지나면 ORBIT/MPS 경로 화면은
현재 위치나 패스를 유효한 자료로 표시하지 않는다. 일정 파일의 SHA-256은
재계산할 때 달라질 수 있다. MPS 시험 초안은 새 일정에서도 지상국·대역과
AOS·LOS 시각(각 5분 이내 차이, 접촉 시간의 절반 이상 중첩)이 같은 미래 PASS에
한해 이어진다. FDS가 PASS 번호를 다시 매겨도 초안의 리비전과 단계는 유지되며,
저장 요청은 항상 최신 일정 SHA-256과 예상 리비전을 확인한다. 경계가 잘린 PASS나
다른 지상국·다른 접촉에는 초안을 자동 연결하지 않는다. 아직 저장하지 않은 화면의
편집 내용은 일정 갱신 때 다시 입력해야 한다. 새 PASS가 짧아 기존 단계의 실행
오프셋이 범위를 벗어나면 초안을 유지하되 화면에 재검토를 표시하고 수정 전 저장을 막는다.
별도 개발용 `tools/receive_fds_inputs.py`는 N05 인증 SFTP pull·N11 완료 파일
수신함을 검증된 스냅샷으로 게시하고 `tools/run_fds_pipeline.py`는 이를 반복
계산할 수 있다. 이 실제 입력 경로의 수신기·FDS 서비스는 아직 Compose에
연결되지 않았다. 파일이 없거나 낡으면 현재 위치로
표시하지 않는다. 시간 이동과 재생 기능은 없으며 화면은 현재 UTC 기준으로만 그린다.
합성 PADO-SAT 기본 입력 도구와 과거 ISS 고정 자료의 선택형 시험 방법은
`services/fds/README.md`에 기록했다. 실제 N05/N11 입력은 이 시뮬레이터에
자동 반입되지 않는다.

## 9-1: 비운용 C 코어 읽기 연결

`gs_stage9_core_probe`는 기존 `gs_mps_command_catalog_parse`,
`gs_mps_fds_schedule_parse`, `gs_sim_tm_validate`를 호출하는 제한된 읽기 전용 실행 파일이다. MPS API의
`/catalog`는 이제 Python의 별도 JSON 해석이 아니라 C 명령 DB 파서가
검증한 버전과 빈 명령 수를 사용한다. ROS API의 `/overview`는 단계 3
`ros-core --sim-only`가 남긴 **시뮬레이터 기록 파일**을 C 프레임 검증기로
다시 확인하고 프레임 수와 마지막 시험 순번만 표시한다. 기록이 없으면 0건,
손상·변조·과대 파일이면 503으로 실패한다. API는 이 값을 센서 측정값이나
실제 TM 수신 시각으로 취급하지 않는다.

## 9-2: 상주 C 읽기 서비스와 내부 통신

ROS·MPS C 읽기 프로세스를 각각 별도 컨테이너에 상주시킨다. API는 요청마다
프로세스를 실행하지 않고 길이 제한 JSON 프레임을 전용 Unix 소켓으로 보낸다.
소켓은 서비스별 비공개 볼륨에 있으며 UID 10001만 접속하고, C 프로세스에는
네트워크 인터페이스가 없다. C 프로세스는 매 요청마다 원본을 다시 읽어 검증해
자료 변경과 손상을 즉시 반영한다. 연결 종료·시간 초과·잘못된 응답·손상
원본은 정상 데이터로 대체하지 않고 오류로 반환한다.

개발 Compose의 `ros_state`는 ROS C 읽기 서비스에만 읽기 전용으로 붙어 있다.
이 Compose에는 기록을 쓰는 ROS 코어 서비스가 아직 없으므로 기본값은
`/state/ros-tm.record` 부재·0건이다. 이 상주 프로세스는 **읽기 어댑터**이며
실제 ROS TM 수신기나 MPS 계획 처리 데몬을 대체하지 않는다. 운용 계획,
실제 TM 파라미터 변환과 경보 증적은 아직 연결되지 않았다. 명령 DB와
시뮬레이터 기록은 크기·파일 유형·권한·무결성을 검사한다. 로그인과 내부 API의
토큰 또는 mTLS 경계는 각 Compose 구성에 따라 유지한다.

## ROS·MPS 화면 구조

ROS는 상단 상태 요약, 센서 목록, 선택 센서의 추세·상하한, 한계 초과 이력,
자세 화면, MPS 시험 시퀀스 검토와 예약 초안으로 구성한다. 센서·차트·한계
평가·쿼터니안 사각 기둥 뷰는 명시적 `TEST_ONLY_TM_SNAPSHOT` 게시 파일,
수동 파일 또는 합성 예시에서만 활성화된다. 게시 파일은 ROS API가 재검증하고
화면에서 5초 간격으로 갱신하며 5분 이상 지난 표본을 오래된 시험 자료로 표시한다.
형식은 `interfaces/ros/test_tm_contract.md`에
기록했다. 기본 화면은 데이터 미연결·평가 불가로 표시한다.
MPS `TEST_ONLY_SEQUENCE` 본문과 매니페스트를 업로드하면 ROS API가 SHA-256,
비운용 표시, 단계와 패스 시간 형식을 확인해 초안 DB에 보존한다. 로그인한
operator/lead/planner는 패스 내부 시각의 단계별 예약 초안을 저장·취소할 수
있으며 auditor는 읽기만 가능하다. POST에는 포털 세션·CSRF와 내부 토큰
(`compose.local.yaml`) 또는 mTLS(`compose.dev.yaml`)가
필요하다. 이 **파일 가져오기** 초안은 `DRAFT_ONLY`이며 시간 도래 시 실행하는
타이머나 송출 경로가 없다. 위의 MPS 직접 전달 `SIM ONLY` 예약만 가상 위성
응답을 시험한다. 둘 다 signer, tc-gate, RF 송출 경로와 연결되지 않으며 기존
C 코어의 독립 승인·충돌·명령 검증을 대신하지 않는다.
MPS는 검증된 N13 패스 일정, 선택 패스의 ground view/polar plot, 초안 명령
카탈로그, 시험 시퀀스 편집기를 작업 순서에 맞춰 배치했다. 경로 화면은
`/api/fds/orbit`의 ECEF 위치 표본으로만 그리며, N13 일정과 AOS·LOS가
일치하지 않거나 TLE가 만료되면 표시를 차단한다. 화면은 60초마다 두 API를
다시 읽는다. 연세대 UHF 패스에서 planner/operator/lead는 카탈로그에 인자가
정의된 초안 명령의 인자와 AOS 기준 실행 오프셋을 순서대로 저장한다.
`/api/mps/test-sequences` POST는 포털 세션·CSRF·해당 Compose의 내부 인증을 거치며,
MPS API는 인자 형식과 패스 범위·일정 SHA-256·예상 리비전을 검증한다.
`mps_candidates` 볼륨의 별도 `sequence_drafts` 테이블이 초안을 보관한다.
auditor는 조회만 가능하다. 대전 S-band는 패스 경로 조회만 제공한다.
저장된 시퀀스는 위의 가상 위성 시험으로 전달할 수 있다. 실제 명령 인증,
충돌 판정, 승인, 출력·송출은 아직 연결되지 않는다.

실제 TM 연결 단계에서는 확정된 패킷/파라미터 DB를 기준으로 센서 ID,
UTC 수신 시각, 보정 값, 단위, 품질, 하한·상한, 경보 등급 및 이력을 제공해야
한다. 경보 판정과 이력은 서버의 검증된 값으로 계산하고, UI는 값의 최신성 및
연결 끊김을 별도 상태로 표시해야 한다. 현재 합성 예시 자료는 이 계약을
대신하지 않는다. TC·MPS 승인 기능 역시 해당 ICD와 실제 권한·재인증 경로가
완성되기 전까지 활성화하지 않는다. 개발 Compose의 `ros_drafts` 볼륨은
시험 초안만 저장하며, 운용 시퀀스/승인 저장소로 승격하지 않는다.

## 단일 VM의 개발용 컨테이너 구성

`compose.dev.yaml`은 DGS 관제 진입점·포털·ROS API·MPS API·ROS C 읽기 서비스·MPS C 읽기
서비스를 분리한다. ROS와 MPS는 각자
포털과의 내부 브리지만 사용하고 호스트 포트를 공개하지 않는다. DGS 진입점만 VM
루프백 `127.0.0.1:8443`에 게시한다. 관제 PC 공개는 모든 앱 개발 뒤 서버
배포 수락에서 UFW·Docker 포트 정책과 별도 출발지 거부를 확인한 후 적용한다.
이 파일은 운영용 Compose가 아니다.

포털은 `PADO_STAGE9_FDS_VIEW_DIR`(기본 `out/fds-view`)을 **읽기 전용**으로
마운트한다. FDS 실행 디렉터리의 `orbit_view.json`을 직접 수정하거나 부분
복사하지 말고 `python3 tools/publish_fds_view.py RUN_DIR out/fds-view`로
검증·원자적 게시한다. 파일이 없으면 `/api/fds/orbit`은 503을 반환하며
화면은 빈 지구와 데이터 미연결 상태를 표시한다. 개발 Compose 자체에는
FDS 계산 컨테이너나 N05 TLE 수집기가 포함되지 않는다.

ROS API는 `PADO_STAGE9_ROS_TM_PREVIEW_DIR`(기본 `out/ros-tm-preview`)을
읽기 전용으로 마운트한다. 먼저 디렉터리를 만든 뒤 초안 TM PDU와 명시적 시험
한계표로 다음과 같이 게시한다. ROS API는 파일을 재검증하고 포털
`/api/ros/test-tm-snapshot`으로만 전달한다. 이 경로는 운용 TM 수신과 분리된다.
게시 도구는 파일을 `0600`으로 쓰므로 Linux에서는 작성자와 ROS 컨테이너
UID 10001이 동일하거나, 배포 시 UID 10001의 읽기 권한을 명시적으로 부여해야
한다. 권한·마운트 검사는 실제 VM 배포 수락 단계에서 수행한다.

```sh
mkdir -p out/ros-tm-preview
python3 -m tools.preview_draft_tm --test-only \
  --limits docs/TC_TM/preview_limits.example.json \
  --out out/ros-tm-preview/test_tm_snapshot.json \
  path/to/draft-tm-packet.bin
```

MPS C 읽기 서비스는 `PADO_STAGE9_MPS_SCHEDULE_DIR`(기본
`out/mps-schedules`)을 읽기 전용으로 마운트한다. FDS 반복 계산기는 성공한
실행의 N13 본문·manifest를 실행 ID별 디렉터리에 검증·게시하고 마지막에
`current.txt`를 원자적으로 교체한다. 이미 완료된 실행은
`python3 -m tools.publish_mps_schedule RUN_DIR out/mps-schedules`로 게시할 수
있다. 서비스는 파일을 매 요청마다 C 파서로 다시 검증하며, 입력 부재는
빈 일정, 변조·오류는 503으로 표시한다. MPS 화면의 패스는 예비 전파 결과이며
승인·TC 송출에 사용하지 않는다. 이 실제 입력 경로는 합성 PADO-SAT 화면
시험과 분리된다.

인증서와 계정 DB는 저장소에 넣지 않는다. 개발용 인증서 디렉터리는
`PADO_STAGE9_CERT_DIR`에 **절대 경로**로 지정한다. 필요한 파일은 `ca.crt`,
`portal.crt`, `portal.key`, `ros-api.crt`, `ros-api.key`, `mps-api.crt`,
`mps-api.key`다. CA는 이 디렉터리에 개인키를 두지 않는다. 개발 인증서에는
포털 서버용 `localhost`, ROS 서버용 `ros-api`, MPS 서버용 `mps-api` SAN이
필요하다. 포털 인증서는 내부 클라이언트 용도도 포함하고 주체 CN은
`pado-portal`이어야 한다. 개인키는 컨테이너 UID 10001만 읽을 수 있게 한다.
운영 PKI·인증서 갱신은 최종 배포 시 확정한다.

```sh
export PADO_STAGE9_CERT_DIR=/absolute/path/to/development-certs
docker compose -f deploy/stage9/compose.dev.yaml config
docker compose -f deploy/stage9/compose.dev.yaml build
docker compose -f deploy/stage9/compose.dev.yaml run --rm --no-deps portal \
  python3 -m services.webapp.main add-user --db /state/accounts.db \
  --username planner01 --role planner
docker compose -f deploy/stage9/compose.dev.yaml up -d
```

계정 생성 명령은 비밀번호를 화면에 표시하지 않는 입력으로 받는다. `lead`
역할은 TOTP가 필수이며 등록 비밀값을 한 번 출력하므로 운영 기록에 별도로
보관한다. 하드코딩된 초기 계정은 없다. 테스트 후 브라우저에서
`https://localhost:8443/login`에 접속한다. 포털은 등록된 개발 CA를
신뢰하는 브라우저에서만 사용한다. 인증서 오류를 우회해 사용하지 않는다.

## 검증과 남은 연결

```sh
python3 -m unittest tests.test_stage9_auth tests.test_stage9_core_adapter \
  tests.test_stage9_core_probe tests.test_stage9_web -v
docker compose -f deploy/stage9/compose.dev.yaml ps
```

Linux 시험은 Argon2id 실제 검증, TOTP 재사용 거부, 세션 만료·잠금,
로그인·로그아웃·CSRF, 포털→ROS/MPS mTLS와 백엔드 직접 접속 거부를 확인한다.
로컬 Windows에는 libsodium/openssl 명령이 없으면 해당 환경 의존 시험이
건너뛰어지며 Ubuntu CI에서 실행한다.
[Ubuntu CI #44](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39899833305)에서
이 시험과 Compose 구성·이미지 빌드가 통과했다.
[Ubuntu CI #47](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39902404739)에서는
9-1 C 프로브의 GCC·Clang ASan+UBSan 빌드, 정상·변조 기록과 명령 DB 거부,
포털 경유 통합 시험, Stage 9 다단계 이미지 빌드도 통과했다.
[Ubuntu CI #51](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39903848929)에서는
9-2 상주 C 서비스의 소켓 통신·자료 재검증·손상 거부, API/포털 HTTPS 통합,
정적 분석·퍼징, 다섯 컨테이너의 Compose 구성·이미지 빌드와 ROS/MPS API
컨테이너에서 전용 C 읽기 서비스 소켓 조회가 통과했다.

단계 9 전체 완료에는 실제 ROS/MPS 운용 서비스·영속 데이터와 웹 API의 연결,
확정된 TC/TM·FDS·Mission Request ICD에 따른 화면 기능, 재인증을 실제 승인
함수에 결합한 증적, 관제 PC→포털의 현장 접근 및 ROS/MPS 직접 접근 거부
시험이 남아 있다. 이 항목이 준비되기 전까지는 현재 읽기 전용 화면을
운용 관제 화면으로 취급하지 않는다.
