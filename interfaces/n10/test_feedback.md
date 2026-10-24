# N10 개발용 ROS → MPS 조회 투영

`GET /api/mps/test-ros-feedback`는 인증된 Portal이 ROS API의 고정된
`/test-n10-feedback` 경로에서 읽어 MPS 화면에 보여주는 **비운용 조회**다.
ROS와 MPS 컨테이너 사이에 새 네트워크나 공유 쓰기 볼륨을 열지 않는다.

응답 스키마 `pado.sim-n10-feedback.v1`은 `source=ROS_SIM_ONLY`,
`non_operational=true`를 반드시 포함한다. `ack_items`는 ROS가 검증해 보관한
가상 위성 `COMMAND_RESULT` 중 실제 ACK/거절을 받은 최근 64건만 담는다.
PASS ID, 일정 해시·리비전, 예약·단계 ID, 명령명, 예정 시각, 결과와 응답
코드·시각을 포함하며 TC 인자나 원시 패킷은 포함하지 않는다.

`bus_soh`는 ROS가 이미 검증한 `TEST_ONLY_TM_SNAPSHOT`의 EPS 센서 최신값만
투영한다. 표본과 수신 시각이 5분 이상 오래됐으면 `STALE`, 파일이 없으면
`UNAVAILABLE`, 형식이 잘못됐으면 `INVALID`다. 이 경우 센서 상태를 정상으로
판정하지 않는다. 한계값은 `services/sim_ground/test_limits.json`의 시험값이다.

이 경로는 원본 N2의 N10 `ACK`·`BUS_SOH`를 화면에서 먼저 확인하기 위한
개발 경계다. **N10 SFTP 본문·매니페스트 교환, 상대 인증, 수신 수락 ACK,
실제 위성 상태·운용 감사 기록을 구현한 것으로 간주하지 않는다.**
