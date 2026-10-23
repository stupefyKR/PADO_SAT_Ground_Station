# FDS 궤도 계산 코어 (비운용 기준선)

개발방향 11.3절에 따라 Java 17 + Orekit 13.1.9로 구현한 **오프라인 파일 계산기**다. 네트워크 리스너와 TC 경로는 없다. 입력을 계산해 새 디렉터리에 파일을 만든다. 운용 TM ICD, 안테나 실측 좌표, 실제 RF 주파수, 정밀도 검증 전까지 산출물은 `PRELIMINARY_NOT_FOR_OPERATIONS`다.

## 입력과 선택

- N05 TLE: 69자 2행, 체크섬, 설정한 `expected_norad_id`와 일치하는 위성 번호, 현재 계획 시작 시각보다 과거이며 72시간 미만인 epoch를 확인한다. Orekit이 실제 SGP4 경로를 선택한 LEO TLE만 받는다. 출처·전송 인증은 현재 이 CLI 밖의 단계 6 파일 교환 경계에서 수행하며, CLI를 운용 수신기로 직접 연결하지 않는다.
- N11 GNSS: 현재는 `fds.gnss-state.v1` 시험용 정규화 파일이다. `norad_id`, `epoch_utc`(UTC), `frame=ITRF`, `position_valid=true`, `velocity_valid=true`, `x_m/y_m/z_m`, `vx_mps/vy_mps/vz_mps`가 모두 필요하다. 실제 TM 패킷·NovAtel 로그 디코더는 **TBD(ICD)**다. OEM719의 `BESTXYZ`는 ECEF 위치·속도와 유효 상태를 제공할 수 있고 CubeADCS 공개 ICD의 기본 GNSS 연동도 ECEF 벡터를 사용한다. 이것이 PADO-SAT 다운링크의 확정 필드라는 뜻은 아니다. 자세 쿼터니안은 궤도 상태 벡터와 별개다.
- 계획 시작 시각에 유효 GNSS 상태의 나이가 **72시간 미만**이면 GNSS를 사용한다. GNSS 파일 부재 또는 **72시간 이상**이면 TLE로 전환한다. 손상된 GNSS 파일은 조용히 TLE로 대체하지 않고 실패한다. TLE까지 없거나 낡으면 실패한다. 선택한 입력과 이유·해시를 `run.properties`에 기록한다.
- HPOP 경로는 ITRF/ECEF 상태를 GCRF로 변환해 수치 적분한다. 현재 모델은 지구 구면조화 중력(차수/차수는 설정), 태양·달 중력이다. 항력·태양복사압·궤도결정·기동·오차 공분산은 미포함이다. 따라서 **운용 정밀도를 검증한 HPOP가 아니라 예비 수치 전파 기준선**이다.

## 두 안테나와 산출물

`station.yonsei`는 연세대 이과대학 UHF 1기, `station.kari`는 대전 항우연 S-band 1기다. 각 안테나의 측량 위경도, WGS84 타원체 기준 고도, 최소 고각, 수신 중심 주파수를 `config.example.properties`에 채워야 한다. 예시 435 MHz·2.2 GHz는 운용 주파수가 아니며 `frequency_example=true`로 표시된다. 주파수 미정 상태에서도 거리 변화율(m/s)은 계산할 수 있으나 Hz 도플러 값은 예시값에만 대응한다.

| 파일 | 내용 | N2 자료 유형 |
|---|---|---|
| `contact_schedule.csv` | 안테나별 AOS·LOS·최고 고각과 시각, 예측창 경계 잘림 표시 | N13 `CONTACT_SCHEDULE` |
| `pass_information.csv` | 가시 구간 10초 간격 방위각·고각·거리·거리 변화율·도플러 | N15 `PASS_INFORMATION` |
| `orbit_ephemeris.csv` | 60초 간격 GCRF 위치(m)·속도(m/s) | N15 `ORBIT_EPHEMERIS` |
| `orbit_view.json` | 같은 전파 결과의 60초 간격 ITRF 위치·지상궤적·태양 방향, 두 지상국 PASS 구간 | 포털 내부 읽기 전용 화면 자료 (N2 외부 교환물 아님) |

각 CSV는 `fds.*.v1` 스키마 열로 시작하고 UTC는 밀리초와 `Z`로 출력한다. 세 파일에 기존 매니페스트 스키마의 SHA-256·유형·송신자·수신자를 담은 `.manifest.json`을 붙인다. 도플러 부호는 거리 변화율이 음수(접근)일 때 양수 주파수 편이이며, `-carrier_hz × range_rate_mps / c`의 1차 근사다. 패스 경계는 설정한 최소 고각을 기준으로 탐색하고 0.1초 이하까지 세분화한다. 최대 전파 기간은 72시간이다.

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

`--gnss`가 없으면 TLE만 사용한다. `--out`은 존재하지 않아야 하며 산출물은 임시 디렉터리에서 완성한 뒤 원자적으로 공개한다. 시험 자료의 안테나 좌표·주파수·GNSS 벡터는 **합성값**이다. 실제 N05/N11 SFTP 수신기와 N13/N15 송신기, MPS 본문 파서, 운용 GNSS TM 디코더는 별도 통합 과제다.

## 위성 위치 화면에 게시

`orbit_view.json`에는 같은 FDS 전파기로 계산한 ITRF/ECEF 위치, 지상궤적, 태양 방향, 두 안테나의 PASS 시각이 포함된다. 웹 브라우저는 이 표본 사이를 보간해 현재 위치와 향후 3주기 선을 그린다. 브라우저가 TLE를 재전파하지 않는다. 60초 표본을 보간한 화면 고각은 표시용 근삿값이고 컨택 판정은 FDS PASS 경계를 사용한다.

완료된 FDS 실행 디렉터리를 `tools/publish_fds_view.py`로 읽기 전용 포털 디렉터리에 원자적으로 게시한다. 기본값은 **현재 시각을 포함하고 입력 epoch가 72시간 미만**인 결과만 허용한다. 과거 결과를 화면 구조 검토용으로 재생할 때에만 `--allow-replay`를 사용한다. 게시 경로는 개발 Compose의 `PADO_STAGE9_FDS_VIEW_DIR`와 같아야 한다.

```sh
python3 tools/publish_fds_view.py /path/to/completed-fds-run out/fds-view
export PADO_STAGE9_FDS_VIEW_DIR="$(pwd)/out/fds-view"
```

화면은 인증된 포털의 `/orbit`에서 볼 수 있다. `/api/fds/orbit`은 게시된 파일만 읽으며 TLE 업로드·계산·TC 경로는 제공하지 않는다. FDS 자동 실행과 N05 실제 TLE 수신, 안테나 실측값, 정확도 수락은 아직 별도로 연결해야 한다.

참고 원본: [NovAtel OEM7 BESTXYZ](https://docs.novatel.com/OEM7/Content/Logs/BESTXYZ.htm), [CubeADCS ICD 1.05](https://www.cubespace.co.za/uploads/c862e5e3-eff7-4519-b67f-b0dc2d5590a0.pdf), [Vallado SGP4 검증 자료](https://celestrak.org/publications/AIAA/2006-6753/), [Orekit 데이터 설정](https://test.orekit.org/site-orekit-development/data/default-configuration.html). 모델 수락 전에는 Orekit 물리 자료 세트의 버전과 EOP 유효 기간을 고정하고 기준 궤도력과 비교해야 한다.
