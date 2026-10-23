# 단계 9 웹 개발 기준선

현재는 **비운용 수직 경로**다. `portal`은 Argon2id 계정·TOTP·세션·
CSRF·계정 잠금·로그인 속도 제한을 제공하고 정적 ROS/MPS 화면을 제공한다.
`ros-api`와 `mps-api`는 별도 프로세스/컨테이너에서 mTLS 클라이언트 인증서를
요구한다. 포털이 로그인한 사용자와 역할을 내부 헤더로 전달하며, 백엔드
인증서의 SAN도 검증한다. 클라이언트가 보낸 동일한 헤더는 전달하지 않는다.

ROS는 `SIM_ONLY`와 TC 송출 차단 상태, MPS는 현재 비운용 명령 DB의 버전과 빈
명령 목록을 표시한다. FDS N13 일정 파일은 C 코어에서 검증해 메모리 계획에
가져와 검증하고 이 개발 Compose의 MPS 웹 서비스에 읽기 전용으로 제공한다.
**TC 송출, 승인, 업링크 반입, 운용 시퀀스 출력, 실제 TM 파라미터 조회 API는
존재하지 않는다.** 단계 9 전체 완료로 판정하지 않는다.

`/orbit`은 FDS가 오프라인으로 만든 `orbit_view.json`을 세션 인증 뒤 읽어
동시에 보이는 3D 지구와 2D 지상궤적, 주야간, 향후 3주기 궤적과 두 지상국의 PASS polar plot을
표시한다. 지도에서는 TLE나 GNSS 상태를 다시 전파하지 않는다. 지구 텍스처는
로컬 NASA Blue Marble이며 출처는 `web/assets/README.md`에 있다. FDS 실행·
개발용 `tools/receive_fds_inputs.py`가 N05 인증 SFTP pull·N11 완료 파일 수신함을 검증된 스냅샷으로 게시하고 `tools/run_fds_pipeline.py`가 이를 반복 계산할 수 있다. Compose에는 이 수신기·FDS 실행 서비스를 아직 추가하지 않았다. 파일이 없거나 낡으면 현재 위치로
표시하지 않는다. 시간 이동과 재생 기능은 없으며 화면은 현재 UTC 기준으로만 그린다.
현재 고정 ISS TLE 화면 시험 도구는 `services/fds/README.md`에 별도로 기록했다.

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
시뮬레이터 기록은 크기·파일 유형·권한·무결성을 검사한다. TLS/mTLS와 로그인
경계는 기존 포털 경로를 유지한다.

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
있으며 auditor는 읽기만 가능하다. POST에는 포털 세션·CSRF와 내부 mTLS가
필요하다. 초안은 `DRAFT_ONLY`이며 시간 도래 시 실행하는 타이머, TC 인코더,
signer, tc-gate, RF 송출 경로가 없다. 이는 기존 C 코어의 독립 승인·충돌·
명령 검증을 대신하지 않는다.
MPS는 검증된 N13 패스 일정, 후보 초안, 명령 카탈로그, 선택 항목 정보를 작업 순서에
맞춰 배치했다. 아직 입력 ICD가 없는 작업 버튼은 비활성이다.
패스를 선택하면 로그인한 planner/operator/lead가 대문자 후보 ID의 순서를
시험용으로 저장할 수 있다. `mps_candidates` 볼륨은 일정 SHA-256·패스
시간창·예상 리비전과 함께 이 초안을 보관한다. auditor는 조회만 가능하다.
후보 ID는 실제 TC 코드가 아니며 명령 인코딩, 충돌 판정, 승인, 출력·송출로
이어지지 않는다.

실제 TM 연결 단계에서는 확정된 패킷/파라미터 DB를 기준으로 센서 ID,
UTC 수신 시각, 보정 값, 단위, 품질, 하한·상한, 경보 등급 및 이력을 제공해야
한다. 경보 판정과 이력은 서버의 검증된 값으로 계산하고, UI는 값의 최신성 및
연결 끊김을 별도 상태로 표시해야 한다. 현재 합성 예시 자료는 이 계약을
대신하지 않는다. TC·MPS 승인 기능 역시 해당 ICD와 실제 권한·재인증 경로가
완성되기 전까지 활성화하지 않는다. 개발 Compose의 `ros_drafts` 볼륨은
시험 초안만 저장하며, 운용 시퀀스/승인 저장소로 승격하지 않는다.

## 단일 VM의 개발용 컨테이너 구성

`compose.dev.yaml`은 포털·ROS API·MPS API·ROS C 읽기 서비스·MPS C 읽기
서비스를 분리한다. ROS와 MPS는 각자
포털과의 내부 브리지만 사용하고 호스트 포트를 공개하지 않는다. 포털만 VM
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
승인·TC 송출에 사용하지 않는다. 이 경로는 ISS 시험 화면과 분리된다.

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
