# FDS PASS 결과와 도플러 프로파일

FDS는 계산 실행마다 새 결과 폴더를 만든다. 기존 PASS 일정과 시간별 관측값에
더해, 같은 계산의 도플러 프로파일을 PASS별 CSV로 저장한다. MPS는 이를 조회해
그래프와 다운로드를 제공하며 궤도나 도플러를 다시 계산하지 않는다.

## 저장 위치

| 실행 방식 | FDS 컨테이너 또는 로컬 파일 경로 |
|---|---|
| 일반 파이프라인 기본값 | `out/fds-runs/<실행 ID>/` |
| Docker 검증 위성 | `/app/out/scenario-ltan12-v1/fds-synthetic-truth-<실행 ID>/` 또는 GNSS 기반 `fds-synthetic-operator-<실행 ID>/` |
| Docker 운용 FDS | `/app/out/runs/<실행 ID>/` |

Docker 결과는 환경별 `fds_runs` named volume에 보관된다. 검증 프로젝트는
`pado-stage9-local_fds_runs`, 운용 프로젝트는 `pado-operations_fds_runs`다.
호스트 저장소의 `out/`에 자동 복사하는 bind mount는 아니다. `stop`과 업데이트는
볼륨을 보존하며 `cleanup`은 해당 환경의 볼륨을 삭제한다. 시뮬레이션은 기존
보존 정책에 따라 최근 8개 실행 그룹을 유지한다.

```text
<실행 ID>/
├── contact_schedule.csv          # AOS/LOS·최고 고각과 시각
├── pass_information.csv          # 모든 PASS의 시간별 관측값·도플러
├── doppler_profiles/
│   ├── yonsei-001.csv            # 연세대 UHF PASS
│   └── kari-001.csv              # 항우연 S-band PASS
├── orbit_ephemeris.csv           # 궤도력
└── orbit_view.json               # 웹 표시용 위치·PASS와 N13 일정 해시
```

PASS 번호는 계산 실행 내부의 식별자다. 같은 번호라도 새 계산의 일정 해시가
다르면 다른 산출물로 취급한다. 과거 번호만으로 현재 결과를 내려받지 않는다.

## MPS에서 확인

1. MPS의 PASS 일정에서 연세대 또는 항우연 PASS를 선택한다.
2. Ground view·Polar plot 아래의 도플러 그래프에서 UTC별 주파수 편이를 확인한다.
3. 기준 주파수와 예시 주파수 여부를 확인하고 CSV 다운로드를 누른다.

선택 변경·일정 갱신 중에는 이전 그래프와 다운로드를 비운다. 선택한 N13 일정과
프로파일이 일치하지 않으면 갱신 대기를 표시한다. 이전 버전 결과에는 프로파일이
없을 수 있으며, 업데이트 뒤 FDS의 첫 계산이 완료되면 파일이 생성된다.

CSV는 FDS에 저장한 원본과 같은 바이트다. 열은 지상국·대역·PASS ID·UTC,
방위각·고각·거리·거리 변화율, 기준 주파수·도플러 편이·수신 주파수 및 예시
주파수 여부다. 기본 표본 간격은 10초이고 설정의 `profile_step_s`를 따른다.
AOS와 LOS 표본을 포함하며 마지막 구간은 설정 간격보다 짧을 수 있다.

도플러 편이는 `−기준 주파수 × 거리 변화율 / 광속`으로 계산한다.
접근 시 양수, 멀어질 때 음수이며 수신 주파수는 기준 주파수에 편이를 더한 값이다.
실제 할당 주파수·안테나 설정 확정 전에는 현재 UHF 435 MHz와 S-band 2.2 GHz를
시험 예시로 사용한다. 주파수는 FDS 설정에서 변경할 수 있다.

## API와 게시 경계

인증된 포털의 `GET /api/fds/doppler`에 `pass_id`와 `schedule_sha256`을 지정하면
UTC·거리 변화율·편이·수신 주파수 표본을 반환한다. 같은 요청에 `format=csv`를
추가하면 원본 CSV를 내려받는다. 파일 경로를 입력하는 API는 제공하지 않는다.

FDS 게시기는 기존 읽기 전용 `fds_view` 볼륨에 현재 실행의
`doppler_profiles.json`을 원자적으로 게시한다. 일정 해시·입력·위성·계산 범위와
파일 무결성을 대조하며, 일정 변경은 409, 결과 대기는 503으로 반환한다.
별도 포트나 컨테이너 간 통신 경로를 추가하지 않고 기존 DGS 웹 진입점을 사용한다.
