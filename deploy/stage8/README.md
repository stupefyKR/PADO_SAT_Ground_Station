# 단계 8: 백업·복구 및 입력 보안 시험

**개발 기준선 완료:** [Ubuntu CI #40](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39897193087)에서 로컬 복구·퍼징·핵심 보안 회귀 시험이 통과했다(결정 #18).

실제 서버 적용과 FlatSat 시험은 모든 개발 뒤 배포 수락 단계에서 수행한다.
NAS 연동은 [결정 #19](../../docs/DECISIONS.md)에 따라 현 개발·배포 수락 범위에서
제외하며, 장비가 정해진 뒤 별도 과제로 다룬다.
여기서는 **비운용 데이터로 재현 가능한 개발 기준선**을 검증한다. 현재 서비스의
운용 DB, 실 TC/TM, HMAC 키는 이 절차에 포함되지 않는다.

## 선택 파일 스냅샷

`tools/stage8_snapshot.py`는 명시적으로 선택한 일반 파일을 새 로컬 폴더에
복사하고 크기·SHA-256 매니페스트를 만든다. 기존 백업이나 복원 폴더를 덮어쓰지
않고, `..`·절대경로·심볼릭 링크·키로 보이는 파일 경로를 거부한다. 원본이
복사 도중 바뀌어도 실패한다. 검증·복원에는 **스냅샷 밖에 따로 보관한**
매니페스트 SHA-256이 필요하다.

선택 파일 예시(`selection.json`):

```json
{"version":1,"files":["interfaces/command_db/commands.json","interfaces/n2_chart.json"]}
```

아래 명령은 시험용 별도 폴더에서 실행한다. `source`에는 DB가 사용 중인
파일이 아니라, 먼저 애플리케이션별 일관된 덤프·내보내기로 고정한 파일을 둔다.

```sh
python3 tools/stage8_snapshot.py create --source /path/to/staged-data \
  --selection /path/to/selection.json --output /path/to/new-snapshot
python3 tools/stage8_snapshot.py verify --snapshot /path/to/new-snapshot \
  --expected-manifest-sha256 기록해_둔_64자리_해시
python3 tools/stage8_snapshot.py restore --snapshot /path/to/new-snapshot \
  --expected-manifest-sha256 기록해_둔_64자리_해시 \
  --destination /path/to/empty-restore-target
```

이 도구는 **암호화, 불변 저장, 3-2-1 매체 교대, 실행 중 DB의 일관된 덤프**를
제공하지 않는다. 운용 배포 전에 데이터의 독립된 백업·복원 방안을 정하고,
암호화·접근 권한·키 분리 보관과 복원 시험을 완료해야 한다. 저장 장비의
종류는 이 도구의 계약에 포함되지 않는다.
HMAC 키는 이 스냅샷에 넣지 않고 보안 계획서 8.4절의 별도 오프라인 절차를
따른다. 자세한 현장 수락 항목은 [서버 매뉴얼](../stage7/SERVER_SETUP.md)에
남겨둔다.

## 복구 시나리오 시험

```sh
python3 -m unittest tests.test_stage8_snapshot -v
python3 -m tests.run_stage8_recovery --build build-linux-release
```

통합 시험은 비운용 명령 DB·TM DB·N2를 스냅샷에서 빈 위치에 복원하고,
복원된 파일을 MPS와 단계 6 C 시험 바이너리가 실제로 읽게 한다. 파일 변조가
있으면 복원을 거부하고 목적지 폴더를 만들지 않는다. 이 시험은 **전체 서버
상실·FlatSat TC 송출** 시험을 대신하지 않는다.

## 파서 퍼징

Clang ASan+UBSan에서 매니페스트, MPS 명령 목록, **합성** TM 프레임,
NORAD TLE 입력을 각각 libFuzzer로 실행한다. CI는 각 대상에 유효·빈
시드를 넣고 20초/20,000회 상한으로 실행한다. 실제 TM ICD가 확정되면
합성 TM 대상과 별개로 운용 TM 파서를 추가해야 한다. 로컬 Linux 실행은
CI의 `build-linux-fuzz` 구성과 `python3 tests/run_stage8_fuzz.py --build
build-linux-fuzz`를 따른다.

퍼징은 입력 파서의 충돌·메모리 오류를 찾는 시험이다. TC 승인·역할 분리,
게이트 거부, SFTP 해시·경로·상대 신원 시험은 기존 CTest와 Docker CI에서
계속 실행하며, 최종 운용 서비스의 전체 경로 보안 판정은 앱 통합 후 수행한다.

| 개발 보안 판정 | 검증 위치 |
|---|---|
| TC 위험도별 별도 승인, signer·게이트 차단, 재전송 거부 | `gs_stage5_tests` CTest |
| 매니페스트 해시·경로·타입 및 SFTP 상대 신원·격리 | `gs_stage1_tests`, `gs_sftp_tests`, `run_network.py` |
| DGS-GS·ROS mTLS와 잘못된 프레임 격리·상대 IP 거부 | `gs_stage3_tests`, `run_stage7_tm.py` |
| 스냅샷 변조·경로 탈출·키 경로 거부와 복원 C 파서 판독 | `test_stage8_snapshot.py`, `run_stage8_recovery.py` |
| 매니페스트·MPS·합성 TM·TLE 입력 퍼징 | `run_stage8_fuzz.py` |
