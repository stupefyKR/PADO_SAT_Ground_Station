# 가상 위성 비콘·명령·저장 TM

## 동작

가상 위성은 TLE 기반 truth 궤도에서 5초마다 전력·온도, −X Nadir 자세,
GNSS TM을 만들고 최대 24시간 동안 자체 SQLite 볼륨에 저장한다.
교신 여부와 관계없이 표본 생성은 계속된다. 센서값은 연속적인 함수로 변하며,
GNSS와 자세는 같은 시각의 궤도에서 계산한다.

연세대 UHF PASS에서는 5초 주기의 `BEACON_CORE`만 기본 수신한다.
ROS에서 **TC 준비 · 예약 → 가상 위성 저장 TM 수신 → 저장 TM 요청**을 누르면
`hk_dump`가 가상 위성에 전달된다. 위성은 `COMMAND_RESULT`로 요청을 수락한 뒤
그 순간까지 저장돼 있던 TM을 원래 측정 시각 순서대로 내린다.

```text
가상 위성: 표본 생성 → 저장 ──────────────→ TC 요청 시 저장분 전송
                         비콘 ↓                 TM ↓
ROS: 비콘 확인 → 저장 TM 요청 → sim-ground → 가상 위성 dispatcher
              ← 요청 수락 ACK ←─────────────┘
              ← 센서·자세 그래프 / GNSS → FDS 예측
```

ACK는 **요청 수락**을 의미한다. 지상국의 모든 TM 수신 완료를 뜻하지 않는다.
요청 이후 새로 생성된 TM은 다음 요청까지 남긴다. LOS·접속 종료·컨테이너
재시작 시 전송 허가는 끝나며, 남은 자료는 다시 요청해야 한다. 불확실한
TC를 지상국에서 자동 재전송하지 않는다.

## 사용하는 패킷

기존 `interfaces/pado_packets/draft_v0.json`의 애플리케이션 PDU를 사용한다.
패킷 ID나 RF 프레임은 새로 만들지 않는다.

| 방향 | 이름 | 역할 |
| --- | --- | --- |
| 위성 → 지상국 | `BEACON_CORE` | 비콘 순번·주기·모드·시험 저장소 사용률 등 |
| 지상국 → 위성 | `hk_dump` | 저장된 TM 전송 요청 |
| 위성 → 지상국 | `COMMAND_RESULT` | TC ID·순번과 요청 수락 또는 거절 |
| 위성 → 지상국 | `EPS_PDU1`, `AOCS_STATE`, `GNSS_PVT` | 저장된 원본 계측·자세·GNSS |

`hk_dump`의 `start_utc_ms=0`, `end_utc_ms=0`은 요청 순간의 저장분 전체를
뜻하는 시험 규칙이다. 두 값을 지정하면 과거 24시간 이내의 엄격히 증가하는
UTC 구간을 선택한다. 이미 전송해 저장소에서 지운 자료는 다시 만들지 않는다.
ROS의 즉시 요청 버튼은 전체 저장분을 요청한다.

| 거절 이유 값 | 의미 |
| ---: | --- |
| 1 | 처리기 미등록 |
| 2 | 위성 내부 실행 시각 예약 미지원 |
| 3 | 중복 요청 또는 중복 방지 저장소 한도 |
| 4 | 요청 시각 만료·미래 시각 |
| 5 | 명령 인자 오류 |
| 6 | 처리기 실행 오류 |

## 기능 추가 위치

`services/sim_sat/dispatcher.py`의 `TcDispatcher`가 명령 이름과 처리기를 연결한다.
현재 `hk_dump`는 저장분 전송 동작을 만들며, 기존 조회 명령은 수락 응답만
돌려준다. 조회 응답에 실제 하위 시스템 자료를 추가하려면 별도의 처리 동작을
구현해야 한다.

1. 시험 카탈로그에 명령 ID·인자를 정의한다. 기존 명령이면 그대로 사용한다.
2. `(decoded_command, CommandContext) → CommandAction` 처리기를 작성하고
   `dispatcher.register(name, handler)`로 명시적으로 등록한다.
3. 새로운 동작이 필요하면 `CommandAction`과 `SatelliteSession`의 실행 부분을
   함께 확장한다. 처리기는 네트워크 전송을 직접 수행하지 않는다.
4. 지상국 송신 허용 목록·인자 검증·화면을 필요한 범위만 추가하고, 잘못된
   인자·중복 TC·LOS·재시작 시험을 추가한다.

CRC·PDU 길이·시험 표시·TC 종류 검사는 공통 코덱에서 수행한다. 위성은
최근 요청의 시각·순번을 SQLite에 저장해 재시작 직후의 재실행도 막는다.
명령 이름을 받아 임의의 Python 함수나 셸 명령을 실행하는 경로는 없다.

## 지상국 연결과 확인

웹 요청은 기존 로그인·역할·CSRF 검사를 거친다. ROS의 별도 요청 기록은
`QUEUED → SENDING → ACKED / REJECTED / TIMEOUT / SEND_FAILED` 상태를 가진다.
30초를 넘겨 대기한 요청은 `MISSED`, 송신 중 재시작된 요청은 `UNKNOWN`으로 남는다.
비콘은 `test_beacon.json`, 센서·자세 이력은 `test_tm_snapshot.json`으로
분리한다. 비콘을 받는 것만으로 센서값이 최신 상태로 바뀌지 않는다.

```sh
cd ~/pado-gs
git pull --ff-only origin feat/fds-core
./pado-gs.sh start
docker logs --tail=30 pado-stage9-local-sim-satellite-1
```

교신 중 비콘 상태를 확인한 뒤 ROS에서 요청한다. 요청 수락 로그와 TM 수신에
따른 그래프 갱신을 확인한다. PASS 밖의 요청은 송신하지 못하며, 다음 PASS에서
사용자가 다시 요청한다. N11에는 수신 시각 기준 120초 이내의 신선한 GNSS만
전달하므로 오래된 저장분만 받았을 때 FDS는 해당 자료로 갱신되지 않는다.

외부에 게시하는 포트는 기존 DGS 진입점 하나다. ROS와 `sim-ground`는 비공개
Unix socket, `sim-ground`와 가상 위성은 기존 Docker 내부 TCP 9200을 사용한다.
비콘·TC·TM 모두 양쪽의 truth PASS 검사 대상이며, 실제 RF·안테나·운용
명령 송출 경로와는 분리된다.

저장 한도 초과 시 가장 오래된 완전한 표본 묶음을 지우고 로그를 남긴다.
TCP `sendall` 성공이 위성 저장분 삭제 기준이므로 지상국 영속 저장을 확인하는
전송 완료 ACK는 아직 없다. ROS 그래프는 최근 1시간 이력만 보존한다.
