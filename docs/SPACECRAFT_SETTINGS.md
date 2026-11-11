# 위성·센서 공통 설정

실제로 사용하는 파일은 저장소의 `config/spacecraft.json` 하나다. 시뮬레이터와
지상국 수신기, ROS가 이 파일을 읽는다. `./pado-gs.sh start`가 파일이 없을 때만
`config/spacecraft.example.json`을 복사한다. Git 업데이트·재시작·`cleanup`으로
설정 파일을 지우거나 초기화하지 않는다. 실제 설정은 Git 추적에서 제외한다.

## 물리 모델 변경

VM에서 `config/spacecraft.json`을 편집한다. 단위는 키 끝의 `_v`, `_a`, `_w`,
`_wh`로 구분하며 모든 장치 정격·소비전력은 `power` 아래에 있다.

| 설정 위치 | 내용 |
| --- | --- |
| `power.solar.cell_types.DSP` | DSP 한 셀의 `voltage_v`, `current_a` |
| `power.solar.cell_types.MSP` | MSP 한 셀의 `voltage_v`, `current_a` |
| `power.solar.banks` | 셀 종류·수량·직병렬 구성·기체 좌표계 면 방향 |
| `power.solar.conversion_efficiency` | 발전 전력의 변환 효율, 기본 0.8 |
| `power.battery` | 용량·전압·내부저항·전류 제한·효율·OCV 곡선·열 모델 |
| `power.devices` | 장치별 공급 전압과 모드별 소비전력 |
| `power.telemetry` | 계측 전압·온도 오프셋·배터리 상태 구분 기준 |
| `sensor_limits` | 센서별 이름·단위·허용 하한·상한 |

예를 들어 DSP 전압을 바꿀 때는 `cell_types.DSP.voltage_v` 한 곳만 바꾼다.
양쪽 DSP 뱅크가 같은 정격을 참조한다. 발전량은 셀별 전력 합에 입사각·식·변환
효율을 적용한다. 셀 전압 합을 배터리 충전 전압으로 직접 사용하지 않는다.

저장한 유효 설정은 다음 TM 생성 표본부터 적용하므로 컨테이너를 다시 빌드할
필요가 없다. 배터리 저장 Wh·온도·누적량은 유지하고, 과거 TM은 당시 값을
유지한다. `initial_soc`는 저장소를 처음 만들 때만 적용한다. 용량 감소로 저장
Wh가 용량을 넘으면 새 용량으로 제한하고 변경을 기록한다.

편집 후 전체 설정을 검사할 수 있다.

```sh
python3 -c 'from services.spacecraft_config import load_settings; load_settings("config/spacecraft.json"); print("설정 정상")'
```

잘못된 실시간 편집은 마지막 정상 설정을 유지하며 서비스 로그에 표시한다.
처음 실행할 때부터 파일이 잘못되거나 지정한 파일이 없으면 해당 서비스가
오류로 시작하지 못한다. 배터리 모델은 시험용이며 셀/BMS 실측 모델을 대신하지 않는다.

## ROS에서 센서 허용 범위 변경

1. ROS의 종합 관제를 열고 **센서 허용 범위**를 펼친다.
2. 센서의 하한·상한을 입력하고 저장한다. 하한은 상한보다 작아야 한다.
3. 현재 그래프와 경보가 새 범위를 사용하는지 확인한다. 새 TM을 기다릴 필요는 없다.

화면에서 저장한 값도 `config/spacecraft.json`에 기록된다. 측정값·측정 UTC·수신
UTC와 과거 RAW 패킷은 바뀌지 않는다. 새 허용 범위는 현재 표시 중인 과거 표본의
초과 판정에도 적용되며, 과거 시점의 경보 정책 버전을 재현하는 기능은 아니다.
직접 가져온 시험 JSON은 그 파일에 명시한 범위를 유지한다.

`planner`, `operator`, `lead`는 편집할 수 있고 `auditor`는 조회만 한다.
다른 사용자가 먼저 저장했으면 덮어쓰지 않고 재조회하도록 안내한다. 웹에서는
하한·상한만 수정하고 장치 정격은 파일에서 편집한다.

## VM 적용과 파일 보존

```sh
cd ~/pado-gs
git pull --ff-only origin main
./pado-gs.sh start
./pado-gs.sh status
```

설정 디렉터리는 ROS API에 쓰기 가능, 시뮬레이터·수신기에는 읽기 전용으로
연결한다. 외부 공개 포트는 추가하지 않으며 DGS 관제 진입점을 계속 사용한다.
실행 스크립트는 호스트 그룹을 ROS에 연결하고 디렉터리 2775·파일 0664 권한을
준비한다. 설정은 임시 파일 작성 후 원자적으로 교체한다.

스크립트 대신 Compose로 진단할 때는 호스트 그룹을 지정한다.

```sh
export PADO_CONFIG_GID=$(id -g)
docker compose -p pado-stage9-local -f deploy/compose.local.yaml config --quiet
```

네이티브 실행에서 경로를 바꾸려면 `PADO_SPACECRAFT_CONFIG` 또는 수신기의
`--limits`에 같은 파일을 지정한다. Docker 구성에서는 `/config/spacecraft.json`을
사용한다. 실제 설정은 일반 데이터 백업에 별도로 포함한다.
