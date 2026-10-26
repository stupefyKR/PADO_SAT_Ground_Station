# FDS 궤도 계산 코어 (비운용 기준선)

개발방향 11.3절에 따라 Java 17 + Orekit 13.1.9로 구현한 **오프라인 파일 계산기**다. 네트워크 리스너와 TC 경로는 없다. 입력을 계산해 새 디렉터리에 파일을 만든다. 운용 TM ICD, 안테나 실측 좌표, 실제 RF 주파수, 정밀도 검증 전까지 산출물은 `PRELIMINARY_NOT_FOR_OPERATIONS`다.

## 입력과 선택

- N05 TLE: 69자 2행, 체크섬, 설정한 `expected_norad_id`와 일치하는 위성 번호, 현재 계획 시작 시각보다 과거이며 72시간 미만인 epoch를 확인한다. Orekit이 실제 SGP4 경로를 선택한 LEO TLE만 받는다. 출처·전송 인증은 현재 이 CLI 밖의 단계 6 파일 교환 경계에서 수행하며, CLI를 운용 수신기로 직접 연결하지 않는다.
- N11 GNSS: 현재는 `fds.gnss-state.v1` 시험용 정규화 파일이다. `norad_id`, `epoch_utc`(UTC), `frame=ITRF`, `position_valid=true`, `velocity_valid=true`, `x_m/y_m/z_m`, `vx_mps/vy_mps/vz_mps`가 모두 필요하다. 실제 TM 패킷·NovAtel 로그 디코더는 **TBD(ICD)**다. OEM719의 `BESTXYZ`는 ECEF 위치·속도와 유효 상태를 제공할 수 있고 CubeADCS 공개 ICD의 기본 GNSS 연동도 ECEF 벡터를 사용한다. 이것이 PADO-SAT 다운링크의 확정 필드라는 뜻은 아니다. 자세 쿼터니안은 궤도 상태 벡터와 별개다.
- 공개 자료 재확인(2026-10-23): NovAtel [OEM719 `BESTXYZ` 로그](https://docs.novatel.com/OEM7/Content/Logs/BESTXYZ.htm)는 ECEF 위치·속도, 각각의 solution status, 좌표 표준편차, 속도 시각 지연을 정의한다. 출력 datum은 `OUTPUTDATUM` 설정에 좌우된다. [CubeADCS ICD 1.05 §3.2](https://www.cubespace.co.za/uploads/c862e5e3-eff7-4519-b67f-b0dc2d5590a0.pdf)는 일반적인 OBC→ADCS GNSS 입력에 ECEF 위치·속도 벡터를 요구한다. 두 자료 모두 **PADO-SAT OBC가 실제로 내려보낼 TM 패킷의 필드·프레임·바이트 순서·스케일·시간 태그를 정의하지 않는다**. 따라서 실제 어댑터에는 위성 측 TM 패킷/파라미터 ICD 또는 동일 내용을 담은 샘플 프레임과 해설이 필요하다.
- 계획 시작 시각에 유효 GNSS 상태의 나이가 **72시간 미만**이면 GNSS를 사용한다. GNSS 파일 부재 또는 **72시간 이상**이면 TLE로 전환한다. 손상된 GNSS 파일은 조용히 TLE로 대체하지 않고 실패한다. TLE까지 없거나 낡으면 실패한다. 선택한 입력과 이유·해시를 `run.properties`에 기록한다.
- GNSS 수치 전파 경로는 ITRF/ECEF 상태를 GCRF로 변환해 궤도를 예측한다. 현재 모델은 지구 구면조화 중력(차수/차수는 설정), 태양·달 중력이다. 항력·태양복사압·궤도결정·오차 공분산은 미포함이다. **궤도 기동 계획·추력 입력·기동 결과 계산은 FDS 범위 밖이다.** 따라서 운용 정밀도를 검증한 HPOP가 아니라 예비 수치 전파 기준선이다. `run.properties`의 `maneuver_model=NONE`은 기동을 고려하지 않은 예측임을 명시한다.

## 두 안테나와 산출물

`station.yonsei`는 연세대 이과대학 UHF 1기, `station.kari`는 대전 항우연 S-band 1기다. 실제 컨택 시각을 확정할 때 각 안테나의 측량 위경도, WGS84 타원체 기준 고도와 고각 기준을 설정한다. Hz 단위 도플러에만 수신 중심 주파수가 필요하다. 현재 설정 파일의 435 MHz·2.2 GHz는 화면·산출물 시험용 값이며 `frequency_example=true`로 표시된다. 주파수 없이도 거리 변화율(m/s)은 계산할 수 있다. 현재 중력장+태양/달 중력 모델은 위성 질량을 사용하지 않아 질량 설정을 요구하지 않는다. 항력·태양복사압 모델을 도입할 때 질량과 유효 면적을 함께 정한다.

### N13/N15 출고함 반복 송신

`tools/stage_fds_outbox.py`가 검증해 게시한 N13/N15 고유 실행 디렉터리를 `tools/send_fds_outbox.py`가 순서대로 읽는다. **N08 안테나 지향·패스 요청 초안은 별도 로컬 출고함에만 기록하며 이 송신기는 읽지 않는다.** 실제 서비스 설치 시 `outbox_peers.example.json`을 저장소 밖의 비공개 설정으로 복사해 MPS와 DGS-Archive의 SSH 호스트·계정·고정 호스트키 지문·키 파일을 입력한다. 아래는 Linux 배포 시 실행 형태이며, 현재 실제 상대 정보가 없어 현장 송신은 수행하지 않았다.

```sh
python3 tools/send_fds_outbox.py \
  --outbox /srv/pado/fds/outbox --receipts /srv/pado/fds/transport-receipts \
  --peers /run/secrets/fds-outbox-peers.json \
  --exchange-bin /usr/local/bin/gs_fds_exchange --watch --poll-seconds 60
```

송신기는 파일 본문·매니페스트 SHA-256과 목적지를 재검사하고, 기존 호스트키 고정 SFTP CLI로 N13 1쌍·N15 2쌍을 전송한다. 성공한 쌍만 로컬 receipt에 기록하고 실패 후 재시도한다. receipt는 **SFTP 전송 완료**일 뿐 MPS/Archive의 해석·수락 ACK가 아니다. 수신 확인 계약이 정해지기 전에는 이 상태를 운용 전달 성공으로 간주하지 않는다.

| 파일 | 내용 | N2 자료 유형 |
|---|---|---|
| `contact_schedule.csv` | 안테나별 AOS·LOS·최고 고각과 시각, 예측창 경계 잘림 표시 | N13 `CONTACT_SCHEDULE` |
| `pass_information.csv` | 가시 구간 10초 간격 방위각·고각·거리·거리 변화율·도플러 | N15 `PASS_INFORMATION` |
| `orbit_ephemeris.csv` | 60초 간격 GCRF 위치(m)·속도(m/s) | N15 `ORBIT_EPHEMERIS` |
| `pass_request.csv` | 두 지상국의 AOS·LOS, 최소 고각과 예측창 잘림 여부를 담은 시험 요청 초안 | N08 `PASS_REQUEST` — 로컬 전용 |
| `antenna_pointing.csv` | 같은 패스의 10초 간격 방위각·고각 시험 지향값 | N08 `ANTENNA_POINTING` — 로컬 전용 |
| `orbit_view.json` | 같은 전파 결과의 60초 간격 ITRF 위치·지상궤적·태양 방향, 두 지상국 PASS 구간 | 포털 내부 읽기 전용 화면 자료 (N2 외부 교환물 아님) |

각 CSV는 버전형 스키마 열로 시작하고 UTC는 밀리초와 `Z`로 출력한다. 다섯 파일에 기존 매니페스트 스키마의 SHA-256·유형·송신자·수신자를 담은 `.manifest.json`을 붙인다. N08 두 파일의 수신처는 `dgs-gs`지만 **외부 송신 기능은 없다.** 모든 N08 행은 `PRELIMINARY_NOT_FOR_OPERATIONS`와 `UNVERIFIED_TEST_VALUES`를 명시한다. 안테나 좌표·고각 기준·주파수는 현재 시험 설정이며, N08 파일을 실제 안테나 명령이나 패스 요청으로 사용할 수 없다. 도플러 부호는 거리 변화율이 음수(접근)일 때 양수 주파수 편이이며, `-carrier_hz × range_rate_mps / c`의 1차 근사다. 패스 경계는 설정한 최소 고각을 기준으로 탐색하고 0.1초 이하까지 세분화한다. 최대 전파 기간은 72시간이다.

## 개발 실행

공식 Orekit 물리 자료를 로컬 디렉터리에 준비해야 한다. 이 코어는 실행 중 인터넷에 접속하지 않는다. Maven 빌드는 인터넷 가능한 개발/CI 환경에서 의존성을 받아 로컬 캐시를 사용한다.

```sh
mkdir -p out
mvn -f services/fds/pom.xml test-compile
mvn -f services/fds/pom.xml exec:java \
  -Dexec.mainClass=kr.ac.khu.padosat.fds.FdsMain \
  -Dexec.args="--config /path/to/fds.properties --orekit-data /path/to/orekit-data --tle /path/to/tle.txt --gnss /path/to/normalized-gnss.properties --out /path/to/new-output-dir"
mvn -f services/fds/pom.xml test-compile exec:java \
  -Dexec.mainClass=kr.ac.khu.padosat.fds.FdsSelfTest \
  -Dexec.classpathScope=test \
  -Dexec.args="$(pwd)/services/fds/src/test/resources /path/to/orekit-data $(pwd)/out"
```

`--gnss`가 없으면 TLE만 사용한다. `--out`은 존재하지 않아야 하며 산출물은 임시 디렉터리에서 완성한 뒤 원자적으로 공개한다. 시험 자료의 안테나 좌표·주파수·GNSS 벡터는 **합성값**이다. MPS C 코어는 생성된 N13 `contact_schedule.csv`와 매니페스트를 검증해 PASS 목록으로 읽는다. 실제 SFTP 상대 설정·상주 호출과 운용 GNSS TM 디코더는 별도 통합 과제다.

## 위성 위치 화면에 게시

`orbit_view.json`에는 같은 FDS 전파기로 계산한 ITRF/ECEF 위치, 지상궤적, 태양 방향, 두 안테나의 PASS 시각이 포함된다. 웹 브라우저는 이 표본 사이를 보간해 현재 위치와 향후 3주기 선을 그린다. 브라우저가 TLE를 재전파하지 않는다. 60초 표본을 보간한 화면 고각은 표시용 근삿값이고 컨택 판정은 FDS PASS 경계를 사용한다.

완료된 FDS 실행 디렉터리를 `tools/publish_fds_view.py`로 읽기 전용 포털 디렉터리에 원자적으로 게시한다. **현재 시각을 포함하고 입력 epoch가 72시간 미만**인 결과만 허용한다. 게시 경로는 개발 Compose의 `PADO_STAGE9_FDS_VIEW_DIR`와 같아야 한다.

```sh
python3 tools/publish_fds_view.py /path/to/completed-fds-run out/fds-view
export PADO_STAGE9_FDS_VIEW_DIR="$(pwd)/out/fds-view"
```

화면은 인증된 포털의 `/orbit`에서 볼 수 있다. `/api/fds/orbit`은 게시된 파일만 읽으며 TLE 업로드·계산·TC 경로는 제공하지 않는다. 아래 수신 스냅샷 경계와 로컬 반복 계산기는 구현됐다. 실제 N11 SFTP 서버 계정, 원시 TM 해독, 안테나 실측값, 정확도 수락은 아직 별도로 연결해야 한다.

### 검증된 파일 쌍으로 반복 계산

`tools/run_fds_pipeline.py`는 개발 단계의 **로컬 파일 경계 실행기**다. N05 `NORAD_TLE`와 N11 정규화 `GNSS`의 본문·매니페스트를 각각 확인한다. 매니페스트의 파일명·유형·송신자·수신자·버전·SHA-256을 검증하고 크기 제한된 입력을 별도 스냅샷으로 복사한 뒤, 현재 UTC를 계획 시작 시각으로 넣어 기존 Java FDS를 실행한다. GNSS가 유효하면 GNSS를 우선 선택하고, 없거나 72시간 이상이면 TLE로 전환하는 판단은 `OrbitInput`이 맡는다. GNSS 파일 또는 매니페스트가 손상되면 TLE로 조용히 넘어가지 않는다.

```sh
python3 tools/run_fds_pipeline.py \
  --config /path/to/approved-fds.properties \
  --orekit-data /path/to/pinned-orekit-data \
  --tle-body /path/to/staged/n05.tle \
  --tle-manifest /path/to/staged/n05.tle.manifest.json \
  --gnss-body /path/to/staged/n11.properties \
  --gnss-manifest /path/to/staged/n11.properties.manifest.json \
  --watch --poll-seconds 60 --refresh-seconds 900
```

GNSS가 아직 없으면 N11의 두 인자를 생략한다. 아직 수신되지 않은 설정 경로의 두 파일이 모두 없으면 해당 입력을 기다린다. `--watch`를 생략하면 1회만 실행한다. Maven 경로·캐시는 `--mvn`, `--maven-repo`로 지정할 수 있다. 결과는 기본 `out/fds-runs`에 보존하고, N13/N15 전송용 출고함은 `out/fds-outbox`, N08 **로컬 전용 초안** 출고함은 `out/fds-n08-outbox`, 화면 파일은 **합성 화면 시험과 분리한** `out/fds-live-view`에 원자적으로 게시한다. MPS 읽기 전용 N13 스냅샷은 `out/mps-schedules`에 실행별로 게시한다. 각 위치는 `--outbox-dir`, `--n08-outbox-dir`, `--view-dir`, `--mps-schedule-dir`로 바꿀 수 있다. N08과 N13/N15 출고함을 같은 경로로 지정하면 계산 시작 전에 거부한다. 배포 시 포털의 `PADO_STAGE9_FDS_VIEW_DIR`과 MPS의 `PADO_STAGE9_MPS_SCHEDULE_DIR`을 각각 맞춘다. 이 실행기는 SFTP 수신·상대 신원 인증·원시 GNSS TM 해독을 수행하지 않는다. 수신 스냅샷 경계는 아래 도구를 사용한다.

### N05/N11 수신 스냅샷 경계

Linux의 `tools/receive_fds_inputs.py`는 N05를 기존 `gs_fds_exchange pull-n05`로 받아 호스트 키 고정과 키 인증을 적용한다. N11은 ROS가 **고유 파일명**으로 SFTP push한 본문과 `.manifest.json`이 있는 전용 수신함을 읽는다. SFTP 송신은 기존 파일을 덮어쓰지 않고 매니페스트를 마지막에 완료 파일명으로 게시해야 한다. `.partial`은 입력으로 사용하지 않는다. 두 경로 모두 매니페스트의 `from`/`to`/`type`/`file`/SHA-256, 크기와 파일 유형을 검증해 `out/fds-ingress/tle` 또는 `gnss`의 불변 해시 디렉터리에 게시한다. `current.txt`는 완료된 스냅샷으로만 원자적으로 바뀐다.

```sh
python3 tools/receive_fds_inputs.py \
  --destination out/fds-ingress \
  --exchange-bin build-linux-release/gs_fds_exchange \
  --n05-host DGS_NET_HOST --n05-port 2222 --n05-user FDS_READ_ACCOUNT \
  --n05-key /run/secrets/n05-read.key \
  --n05-host-key-sha256 64_LOWERCASE_HEX_DIGITS \
  --n05-remote-dir /outbox/to_fds --n05-filename current.tle \
  --n11-inbox /srv/fds/inbox/from_ros \
  --watch --poll-seconds 60

python3 tools/run_fds_pipeline.py \
  --config /path/to/approved-fds.properties \
  --orekit-data /path/to/pinned-orekit-data \
  --tle-current-root out/fds-ingress/tle \
  --gnss-current-root out/fds-ingress/gnss \
  --watch --poll-seconds 60 --refresh-seconds 900
```

실제 호스트·계정·호스트 키·수신함 경로는 배포 설정에서 지정한다. N05가 아직 구성되지 않았다면 N11 수신함만 지정해 실행할 수 있다. N11의 최신 완료 파일이 손상되면 `blocked.txt`가 생기고 FDS 계산이 중단된다. 유효한 새 파일이 들어오면 차단이 해제된다. N05 접속 장애는 기존의 검증된 TLE를 보존하며 그 사용 가능 여부는 FDS의 72시간 신선도 검사로 결정한다. 이 경계는 **정규화된 시험 GNSS 상태만** 받아들이며 PADO-SAT의 원시 GNSS TM 패킷 형식을 추정하거나 변환하지 않는다. N11 SFTP 서버 계정과 실제 상대 연결 시험은 아직 남아 있다.

Linux의 `gs_sftp_tests`는 CI에서 일시적인 키 전용 `sshd`를 시작해 N05를 인증된 SFTP로 내려받고, N11을 SFTP로 올린 뒤 수신 스냅샷에 반영하는 경로를 시험한다. 잘못된 호스트 키와 전송 뒤 변조된 N11은 거부해야 한다. 이 임시 서버는 운용 계정·권한 설정이나 최종 VM 배포 시험을 대신하지 않는다.

### N13/N15 전송용 출고함

완료된 FDS 실행 디렉터리의 N13/N15 CSV 세 개와 매니페스트를 `tools/stage_fds_outbox.py`로 다시 검증한다. 반복 계산 실행기는 계산 성공 후 이를 자동 호출한다. 도구는 각 파일에 실행 ID를 붙여 고유 이름의 불변 파일 쌍 3개를 원자적으로 출고함에 게시한다. 원본 FDS 결과와 합성 화면 시험 자료는 수정하지 않는다. 예를 들어 `out/fds-runs/fds-20261023T100000Z-1234abcd`를 지정하면 `out/fds-outbox/fds-20261023T100000Z-1234abcd/`에 `contact_schedule-fds-20261023T100000Z-1234abcd.csv`와 해당 매니페스트 등이 생긴다. MPS는 원본 고정 파일명과 출고함의 고유 파일명을 모두 검증할 수 있다. N08 두 초안 파일은 같은 실행에서 N13 PASS 경계·두 지상국 식별자·시각·방위각/고각 범위·시험 출처와 해시를 확인해 별도 로컬 출고함에 게시한다. 이 경계는 SFTP 계정이나 DGS-GS 외부 송신 서비스를 만들지 않는다.

```sh
python3 tools/stage_fds_outbox.py out/fds-runs/fds-20261023T100000Z-1234abcd
python3 tools/stage_fds_outbox.py --n08-only out/fds-runs/fds-20261023T100000Z-1234abcd
```

Linux 빌드의 `gs_fds_exchange`는 기존 단계 6 SFTP 경로 검증기를 사용한다. `push-n13`은 MPS로 일정 한 쌍, `push-n15`는 Archive로 궤도력·패스 정보 각 한 쌍을 송신한다. `pull-n05`는 DGS-NET에서 TLE 한 쌍을 받아 로컬 완료 파일로 게시한다. 호출 형식은 다음과 같다.

```text
gs_fds_exchange push-n13 HOST PORT USER PRIVATE_KEY HOST_KEY_SHA256 REMOTE_DIR BODY MANIFEST
gs_fds_exchange push-n15 HOST PORT USER PRIVATE_KEY HOST_KEY_SHA256 REMOTE_DIR BODY MANIFEST
gs_fds_exchange pull-n05 HOST PORT USER PRIVATE_KEY HOST_KEY_SHA256 REMOTE_DIR FILENAME LOCAL_DIR
```

호스트 키 SHA-256은 OpenSSH의 Base64 표시가 아니라 **64자 소문자 16진수**다. 개인키는 저장소 밖에서 마운트한다. 반복 전송 서비스와 실제 상대 주소·계정·호스트 키는 배포 시 설정하고, 실패한 전송은 재시도·중복 처리 정책을 정해야 한다. 아직 실제 SFTP 상대와의 연결 시험이나 MPS 서비스의 받은 편지함 감시가 완료된 것은 아니다.

### 가상 PADO-SAT SSO 화면 시험 (로컬 스택 기본값)

실제 위성 TLE가 아직 없으므로 `tools/build_synthetic_demo.py --test-only`가 **고도 약 500 km,
상승교점 지방 평균 태양시(LTAN) 약 06시**의 가상 태양동기궤도(SSO) TLE를 만든다.
TLE의 epoch와 궤도 위상은 시험 실행 시각에 맞춰 갱신한다. FDS는 이 TLE를
기존 SGP4 경로로 전파해 현재 UTC부터 최대 72시간의 궤도 보기와 두 지상국의
PASS 일정을 만든다. 이 TLE는 위성 추적에서 얻은 궤도결정 결과가 아니며
고도·LTAN도 시험 목표의 근사값이다. 실제 컨택 예보나 TC 예약 판단에 사용하지
않는다.

```sh
python3 tools/build_synthetic_demo.py --test-only --orekit-data /path/to/orekit-data
```

로컬 Compose의 `fds-synthetic-demo`는 독립 컨테이너에서 15분마다 다시 계산한다.
먼저 TLE만 사용한 궤도 truth를 별도 `fds-truth` 볼륨에 게시해 가상 위성의
위치 기준으로 쓴다. `sim-satellite`는 이 위치 표본으로부터 5초마다
`EPS_PDU1`, `AOCS_STATE`, `GNSS_PVT` **시험용 응용 PDU**를 만든다. 전용
`sim_tm` 내부망의 TCP 9200에서 `sim-ground`가 길이 접두 프레임을 검증·파싱한다.
ROS 시험 TM 스냅샷에는 전압·온도 추세와 자세 쿼터니안이 담기고, GNSS PDU는
첫 수신 및 이후 5분 간격으로 정규화된 N11 시험 본문·매니페스트가 된다.

FDS 실행기는 이 N11 파일 쌍을 다시 검증하고 GNSS가 신선하면 기존 예비
수치 전파로 **운영자용** 궤도를 계산한다. GNSS가 없거나 계획 시점에 72시간
이상 낡았으면 TLE/SGP4 결과를 사용한다. 손상된 GNSS 파일을 조용히 TLE로
대체하지 않는다. 운영자용 ORBIT 파일은 `out/fds-view`, 같은 실행의 검증된
N13 PASS 파일은 `out/mps-schedules`에 게시한다. 포털 `/api/fds/orbit`과 MPS
`/api/mps/passes`는 게시 파일을 각자 읽는다. Maven 실행기와 캐시를 별도로
지정할 때는 도구의 `--mvn`, `--maven-repo` 인자를 사용한다. 재계산할 때 N13
파일의 SHA-256이 바뀌면 이 해시에 묶인 MPS 시험 시퀀스 초안은 현재 목록에서
빠질 수 있다. 안정적인 PASS 식별과 초안 이어받기는 후속 작업이다.

오프라인 연결 시험은 Orekit 자료와 Maven을 갖춘 Linux 환경에서 다음처럼
실행할 수 있다. 이 시험은 TLE truth→가상 PDU→ROS 스냅샷·N11→FDS GNSS 선택과
MPS 일정 게시를 확인한다. 실제 VM의 Compose, Docker 볼륨·권한, 브라우저 경로는
`deploy/stage9/README.md`의 별도 통합 확인 절차를 따른다.

```sh
python3 tests/run_synthetic_demo_smoke.py /path/to/orekit-data
```

가상 TLE와 가상 위성이 만든 N11은 **실제** N05/N11 본문·매니페스트 수신
경로에 자동 등록되지 않는다. 이 가상 링크는 RF·CSP·AX100 프레임이나 실제
항우연/연세대 수신 장비를 거치지 않는다. 시험 TC·ACK 왕복은 내부 시뮬레이터
링크에서만 수행하며 실제 위성으로 송출하지 않는다. 연세대
`(37.5624, 126.9389, 0 m)`, 항우연 대전 `(36.3700, 127.3600, 0 m)`, 최소 고각
`10°`, UHF `435 MHz`, S-band `2.2 GHz`는 모두 **화면 시험용 근삿값·예시값**이다.
실제 안테나의 측량 좌표·고각 마스크·주파수로 교체하기 전 컨택/도플러 시각은
운용에 사용할 수 없다. 실제 N05 TLE 또는 정규화된 N11 GNSS 파일 쌍의 반복
계산기는 별도 경로로 남으며, 실제 수신기·운용 화면과의 배포 연결은 후속 작업이다.

### 고정 ISS TLE 자료 (화면 전용 시험)

사용자가 제공한 ISS (ZARYA) TLE 두 줄은 `services/fds/demo/iss-fixed.tle`에
변경 없이 보존한다. 원본 epoch는 **2026-10-22 22:03:10 UTC**이다.
`tools/build_iss_demo.py --test-only`는 이 고정 자료를 SGP4로 외삽한 뒤 현재
epoch에 다시 맞춘 **가상 TLE**로 별도의 ISS 화면 산출물을 만든다. 이는 실제 ISS의
현재 위치·관측 TLE가 아니며 교신 계획에 사용할 수 없다. 생성 TLE의 epoch가
새로워도 실제 입력이 최신이라는 뜻은 아니다. FDS의 72시간 입력 검사 자체는
변경하지 않는다. ISS 화면 결과는 별도 `fds-iss-view`에만 게시하고 MPS N13
일정이나 운용 N05/N11 입력으로 보내지 않는다. 로컬 Compose의 30분 간격 반복 실행 시 완료된
실행 결과는 최근 8개만 보존한다.

참고 원본: [NovAtel OEM7 BESTXYZ](https://docs.novatel.com/OEM7/Content/Logs/BESTXYZ.htm), [CubeADCS ICD 1.05](https://www.cubespace.co.za/uploads/c862e5e3-eff7-4519-b67f-b0dc2d5590a0.pdf), [Vallado SGP4 검증 자료](https://celestrak.org/publications/AIAA/2006-6753/), [Orekit 데이터 설정](https://test.orekit.org/site-orekit-development/data/default-configuration.html). 모델 수락 전에는 Orekit 물리 자료 세트의 버전과 EOP 유효 기간을 고정하고 기준 궤도력과 비교해야 한다.
