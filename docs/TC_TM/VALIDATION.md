# TC · TM 지상국 통합 검증

FSW를 개발하기 전에 지상국의 TC 구성·예약·송신과 TM 수신·파싱·거부·이벤트 표출을 확인한다. 현재 저장소의 애플리케이션 PDU 규격을 사용하며, 비행 FSW나 무선 장비의 호환성 승인을 뜻하지 않는다.

## 실행 환경

| 항목 | 지상국 검증 | 운용 지상국 |
|---|---|---|
| 실행 | `bash pado-validation.sh start` | `bash pado-operations.sh start` |
| 로그인 | `http://192.168.45.128:8080/login` | `http://192.168.45.128:8082/login` |
| Compose 프로젝트 | `pado-stage9-local` | `pado-operations` |
| 공통 기능 | ROS·MPS·ORBIT, 인증·예약·파싱 코드 | 동일한 화면·이미지·코드 |
| 가상 위성 | 기존 500 km / 98.4° / LTAN 12h, −X Nadir | 포함하지 않음 |
| 시험 FDS 생성기 | 가상 PADO-SAT 1개, ISS 생성기 제외 | 포함하지 않음 |
| FDS 전파 기능 | 기존 합성 입력과 GNSS로 계산 | 공통 전파 파이프라인 `fds-worker`, 실제 입력 대기 |
| 계정·토큰·DB·볼륨 | 검증 프로젝트 전용 | 운용 프로젝트 전용 |
| 물리 설정·한계값 | `config/spacecraft.json` | `config/operations/spacecraft.json` |
| 추가 검증 API | `/api/ros/validation/*` | 비활성, 서버가 거부 |

웹·DGS 링크·FDS 이미지는 각각 `pado-gs-web:local`, `pado-gs-link:local`, `pado-gs-fds:local`로 공용 빌드한다. 컨테이너 인스턴스와 저장소는 분리한다. 로그인 쿠키 이름도 서로 달라 같은 Windows 브라우저에서 두 환경을 사용할 수 있다. 서로 다른 환경으로 계정·TM·TC·설정 파일을 자동 복사하지 않는다.

검증 가상 위성 컨테이너는 시작 후 대기하며 **검증 로그인 성공 시** TM 생성·연결을 활성화한다. 웹 서버에 Docker 제어 권한이나 Docker 소켓을 제공하지 않는다. 재시작마다 배포 ID를 바꾸므로 이전 로그인 활성화 파일이 새 실행을 활성화하지 않는다. 로그아웃 후에도 진행 중인 시험은 계속된다.

각 환경은 DGS 게이트웨이만 호스트 포트를 게시한다. ROS·MPS·가상 위성은 외부 네트워크에 붙지 않는다. 프로젝트별 내부망·외부 브리지·방화벽 체인을 분리하며 다른 환경을 `stop`하거나 정리하지 않는다. UFW 전체 포트를 추가로 열 필요는 없다. 기존 DOCKER-USER 및 호스트 접근 규칙을 사용한다.

운용 환경의 입력 저장소는 처음에 비어 있다. 외부 지상국의 실제 통신 어댑터·인증·비행 승인 경로를 이 변경으로 활성화하지 않는다. 실제 입력이 연결되기 전 ORBIT·TM·PASS는 입력 대기로 표시된다.

운용 FDS는 `config/operations/fds.properties`와 DGS가 반입한 N05·N11 본문/매니페스트 쌍을 기다린다. 설정 형식은 `services/fds/config.example.properties`를 사용한다. DGS 입력 볼륨의 `tle/`, `operations/`에서 검증된 쌍을 읽어 기존 전파·ORBIT·N13 게시 파이프라인을 실행하며 합성 TLE를 자동 생성하지 않는다.

## VM 업데이트와 웹 시험

```sh
cd ~/pado-gs
git pull --ff-only origin main
bash pado-validation.sh start
bash pado-validation.sh status
```

기존 시험 프로젝트의 볼륨과 계정은 유지한다. 이전 ISS 생성기 등 같은 프로젝트의 옛 컨테이너는 시작 과정에서 제거하며 볼륨은 보존한다. 초기 FDS 계산이 끝날 때까지 첫 비콘을 기다린다.

1. Windows에서 `http://192.168.45.128:8080/login`에 로그인한다. 상단의 **지상국 검증** 표시를 확인한다.
2. ROS 종합 관제에서 정상 HK 비콘을 확인한다. 기본 검증 연결은 연속 연결이므로 PASS까지 기다리지 않는다. 이 설정은 RF 가시성을 검증하기 위한 설정이 아니다.
3. MPS 또는 ROS의 **TC 준비 · 예약**에서 **Validation Packets**를 누른다.
4. 시퀀스 전체 시작 UTC와 seed를 정한다. 기본은 현재로부터 약 15초 후이며 최소 3초 이후·24시간 이내로 예약한다. MPS에서 PASS를 선택했다면 **선택한 PASS 기준 적용**으로 AOS 몇 초 후의 UTC를 적용할 수 있다.
5. **검증 시퀀스 예약**을 누른다. 한 실행의 TC를 순서대로 전송한 뒤 마지막 검증 요청 TC로 TM 데이터셋을 요청한다.
6. **TC 송신 원문 · 응답**에서 각 TC의 값·ACK와 **DGS 송신 RAW**를 펼친다. 표시 시각은 DGS가 가상 위성 TCP 소켓에 바이트를 쓴 시각이다. ROS가 송신 요청만 작성한 경우와 구분한다.
7. TM 수신 10,000개, 정상 파싱 7,500개, 손상 거부 2,500개, 오수락·오거부·값 불일치·급변 이벤트 누락 0을 확인한다. TC와 TM의 판정은 따로 표시한다. PC·디스크 성능에 따라 수신에는 수 분이 걸릴 수 있다.
8. **TM 수신 원문 · 파싱 결과**에서 정상 패킷은 파싱된 필드, 손상 패킷은 거부 이유와 RAW를 확인한다. 50개 단위로 이전 패킷을 조회한다. 정상 수신 패킷은 기존 ROS **수신 TM** 탭에서도 확인할 수 있다.
9. **센서 이벤트 · 측정 UTC**에서 급변 표본의 한계 초과를 확인한다. 데이터셋 수신이 끝나면 센서 그래프 자료도 공통 ROS 스냅샷으로 게시된다. 패킷 도착 순서와 그래프의 측정 시각 순서는 다를 수 있다.
10. **결과 JSON 저장**으로 실행별 TC 원문·응답·TM 판정·오류 사례를 저장한다. 중단·재시작으로 누락이 남았으면 **남은 검증 중단** 후 새 실행을 예약한다. 이미 송신한 TC는 취소할 수 없으며 불명확한 송신을 자동 재시도하지 않는다.

운용 환경은 별도로 `bash pado-operations.sh start`를 실행한다. 최초 계정은 그 환경에서 별도로 생성한다. IP가 달라지면 기존과 같이 `PADO_LOCAL_BIND_IP`, `PADO_OPERATOR_CIDR`를 지정한다. 두 스택을 동시에 올릴 수 있으며 포트는 각각 8080과 8082다.

`stop`은 해당 환경의 데이터와 계정을 보존한다. `cleanup`은 확인 후 해당 프로젝트의 볼륨을 삭제한다. 시험 결과를 유지하려면 `cleanup`을 사용하지 않는다. 호스트의 설정 디렉터리는 유지된다. 기존 `pado-gs.sh`는 이전 개발 스택 실행용으로 남겼으며 새 검증은 전용 실행 파일을 사용한다.

두 환경에서 사용하는 이름 붙은 공통 이미지는 다른 환경의 실행을 위해 유지된다.

## TC 범위와 원문 기록

현재 카탈로그의 TC 58종 중 필드가 정의된 36종을 모두 포함한다. 인자가 없는 명령, 정수 대표값·최소·최대값, 불리언 양쪽 값, 유효한 TLE 문자열과 UTC 인자를 조합한 **53개 시험 값**을 만든다. 같은 인자 바이트는 중복 제거한다. 시간 인자는 유효한 시각을 사용하며 모든 64비트 시각을 전수 시험하지 않는다. 마지막의 검증 전용 TM 요청을 포함하면 TC 54개다.

인자 정의가 없는 22종은 화면과 결과의 `undefined_commands`에 표시하며 임의 바이트를 만들지 않는다. 실제 장비의 모드·채널 허용값이나 물리 동작 범위는 FSW ICD와 함께 확정해야 한다. 극값 시험은 현재 **패킷 필드의 표현 범위** 시험이다.

검증 모드의 dispatcher는 TC 구조를 검사하고 상관관계가 맞는 `COMMAND_RESULT`를 반환하는 FSW 대역이다. 검증 시퀀스 중에는 리셋·히터·페이로드 등 실제 장치 동작을 일으키지 않는다. 원문 SHA와 일치하는 검증 요청만 이 모드로 실행하며, 기존 일반 `hk_dump` 요청의 저장 TM 송신 기능은 유지한다.

`validation_tm_dump`(ID 32766)는 검증 링크에서만 등록한다. 필드는 `seed:u32`, `count:u16`, `base_utc_ms:u64`이며 비행 카탈로그 파일은 바꾸지 않는다. ROS의 예약 서비스가 시각을 관리하고 TC 자체는 기존 즉시 실행 PDU로 송신한다. 위성 내부 예약 실행은 이 시험 범위에 포함하지 않는다.

DGS는 `transmitted_tc.sqlite3`에 실제 소켓 경계의 원문과 `PREPARED/SENT/UNKNOWN`을 기록한다. 화면의 송신 완료는 DGS 기록과 대조하며, ACK는 그와 별도로 위성이 수락했는지 나타낸다.

## TM 데이터셋

| 분류 | 수량 | 내용 |
|---|---:|---|
| 정상 | 7,000 | 정의된 TM 19종, 센서값 무작위화, 단위 쿼터니안·유효 GNSS 상태 |
| 손상 | 2,500 | 19개 오류 분류, 아래 항목을 반복 배치 |
| 정상 급변 | 500 | 유효한 프레임·필드 인코딩에 허용 범위 초과 센서값 삽입 |

손상 분류는 magic, version, kind, packet ID, payload 길이, flags, reserved, CRC, 절단, 초과 길이, NaN, Infinity, 불리언 값, ASCII, HK chunk index, 미래 시각, 쿼터니안 norm, GNSS fix, GNSS 위치 범위다. CRC 이외의 구조·필드 손상은 가능한 경우 CRC를 다시 맞춰 CRC 검사 뒤의 분기까지 시험한다.

모든 패킷에는 별도의 측정 UTC가 있고 seed로 섞인 순서대로 전송한다. 센서 범위는 예약 시점에 고정해, 중간에 ROS 설정을 변경해도 기대값과 생성값이 달라지지 않게 한다. 정상 급변도 파싱 실패로 처리하지 않고 ROS의 한계 초과 이벤트로 기록한다. 비행 센서의 변화율 경보 기준은 별도 요구사항이다.

유한한 10,000개 표본으로 모든 비트 조합·모든 FSW 상태를 전수 검증할 수는 없다. 이 데이터셋은 현재 정의된 TM 유형과 명시한 오류 분기의 재현 가능한 시험 집합이다. FULL_HK_CHUNK는 독립 복원이 가능한 단일 chunk 표본을 사용하며 기존 복수 chunk 복원·중복·누락 시험은 기존 회귀 시험에 유지한다.

## 수신·판정 경로

`MPS/ROS 예약 → 기존 단일 ROS 송신기 → Unix TC 브리지 → DGS → 가상 위성 → 기존 TM TCP 연결 → DGS 공통 디코더·측정값 검사 → 수신 보관 → ROS`

기대값·손상 분류는 TM 원문에 태그를 넣어 보내지 않는다. DGS는 수신한 바이트를 공통 디코더로 파싱한 뒤 별도 기대값과 비교한다. 검증 manifest의 SHA는 실행·표본을 연결하는 용도이며 정상/손상 판정을 대신하지 않는다. 순서가 뒤섞인 과거 표본은 해당 데이터셋의 측정 시각으로 정렬한다. 검증 수신 모드에서만 길이 접두부를 1–512바이트로 제한 수용하고 실제 PDU 길이는 공통 규격(24–200바이트)으로 검사한다. 손상 표본은 별도 저널에 원문·거부 이유를 남겨 다음 표본 수신을 계속한다.

가상 위성은 DGS의 영속 처리 확인(`PVR1 + SHA-256`)을 받은 표본을 전송 창에서 제거한다. 이는 손상 패킷을 정상 수락했다는 의미가 아니다. 연결이 끊기면 아직 확인되지 않은 표본을 다시 보내고 DGS는 동일 SHA 중복을 한 번만 집계한다. 프로세스 재시작으로 사라진 가상 위성의 메모리 대기열은 자동 복구하지 않으며 누락으로 남긴다.

TM PASS는 10,000개 모두 관측, 정상 파싱, 손상 거부, 정상 필드 값 일치, 급변 이벤트 검출, 예상 외 손상 패킷 없음이 모두 맞을 때만 표시한다. 오류 상세 목록은 분류별 최대 50개이며 전체 오류 건수는 별도로 보존한다. 센서 이벤트는 페이지당 최대 100개, RAW 조회는 50개다. 실행은 최대 20개를 보관하며 원문 저널 용량 제한도 적용한다.

무작위 GNSS를 FDS 정밀도 시험 입력으로 섞지 않도록 검증 데이터셋은 N11로 재게시하지 않는다. 정상 시뮬레이터의 물리 모델 TM과 GNSS→FDS 경로는 기존 흐름을 유지한다. 이 시험은 지상국 패킷 경로의 검증이다.

## 파일과 API

| 위치 | 내용 |
|---|---|
| `services/validation/corpus.py` | TC 시험 행렬·10,000개 TM 생성·정답 |
| `services/validation/scheduler.py` | 예약·단일 송신기 연결·결과 |
| `services/validation/recorder.py` | 수신 원문·실제 파싱·기대값 비교·센서 이벤트 |
| `services/sim_ground/measurement.py` | 일반 수신과 공통 GNSS·자세 검사 |
| `services/sim_ground/tc_journal.py` | DGS 송신 원문 |
| `web/validation/console.js` | ROS·MPS 공통 검증 화면 |
| `deploy/environment_profile.py` | 공통 Compose에서 실행 환경·저장소 분리 |
| `GET /api/environment` | 현재 환경 표시 |
| `GET /api/ros/validation/coverage` | TC 범위·미정 ICD 목록 |
| `POST /api/ros/validation/runs` | `{target_utc_s, seed}` 예약 |
| `GET /api/ros/validation/runs[/실행ID]` | 목록 또는 TC 원문·TM 결과 |
| `GET /api/ros/validation/packets/실행ID[/이전caseID]` | TM RAW·파싱·거부 이유 |
| `GET /api/ros/validation/events/실행ID` | 센서 이벤트 |
| `POST /api/ros/validation/cancel` | `{id}` 남은 검증 중단 |

로그인·역할·CSRF 검사를 거쳐야 예약/중단할 수 있다. auditor는 읽기만 가능하다. 검증 API를 운용 환경으로 호출해도 활성화되지 않는다.

독립 데이터셋 파일이 필요하면 저장소에서 다음을 실행한다. 새 출력 폴더를 사용하며 기존 폴더를 덮어쓰지 않는다.

```sh
python3 -m tools.validation_packets generate \
  --output out/validation-dataset-724 --seed 724 \
  --base-utc 2026-11-04T00:00:00Z
```

`tm_packets.jsonl`에는 각 RAW·SHA·기대 필드·오류 분류가 들어 있고 `tc_matrix.json`에는 TC 시험 인자가 들어 있다. 웹 예약 시에는 같은 생성기가 예약 시각 기준으로 새 데이터셋을 만든다.

자동 시험:

```sh
python3 -m unittest tests.test_validation_packets tests.test_validation_runtime tests.test_validation_http -v
python3 tests/container/check_environment_profiles.py  # Docker Compose 정규화·격리 정책 검사
```

Unix TC 브리지·가상 위성·DGS 소켓 왕복 시험은 Linux에서 수행한다. Windows에서는 해당 시험을 건너뛴다. 자동 시험 성공과 실제 VMware 배포·브라우저 확인은 구분해 기록한다.
