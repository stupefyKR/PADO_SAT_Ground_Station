# PADO-SAT Ground System

경희대학교 PWNLAB의 PADO-SAT 큐브위성 지상국 소프트웨어입니다. Linux VM 한 대에서
DGS, ROS, MPS, FDS와 웹 포털을 역할별 Docker 컨테이너로 실행합니다.
관제 PC는 내부망의 웹 포털에 접속하며, **런타임 애플리케이션의 외부 통신은 DGS만 수행**합니다.

이 저장소의 소스 루트는 `pado-gs/`입니다. 상위 작업 폴더의 발표 산출물·임시 작업물과
저장소 내부의 `out/`, `build*/`는 실행 소스와 구분합니다.
개발·운용 절차의 전체 목록은 [문서 안내](docs/README.md)를 참고하세요.

## 시작하기

Linux VM에 Docker Engine 28 이상, Docker Compose v2 플러그인, Python 3,
`iptables`와 방화벽 규칙을 관리할 권한이 필요합니다. 첫 이미지 빌드에 필요한
의존성·Orekit 데이터 다운로드는 배포 준비 과정에서 수행합니다.

```sh
git clone --branch main https://github.com/stupefyKR/PADO_SAT_Ground_Station.git pado-gs
cd pado-gs
bash pado-validation.sh start
```

| 환경 | 실행 | Windows 관제 PC 접속 |
|---|---|---|
| 지상국 검증 | `bash pado-validation.sh start` | `http://192.168.45.128:8080/login` |
| 운용 구성 | `bash pado-operations.sh start` | `http://192.168.45.128:8082/login` |

검증·운용은 같은 이미지와 화면·파싱·예약 코드를 사용하며 계정, API, 설정,
데이터 볼륨과 네트워크를 분리합니다. 검증 로그인에 성공하면 가상 PADO-SAT가
활성화됩니다. 운용 구성에는 가상 위성이 없으며 실제 입력이 연결되기 전에는
TM·궤도·PASS를 입력 대기로 표시합니다.

처음 시작할 때 해당 환경의 계정을 생성합니다. VM 또는 관제 PC 주소가 다르면
실제 사설 주소를 지정합니다. 관제 PC 주소는 VM에서 보이는 출발지 주소입니다.

```sh
PADO_LOCAL_BIND_IP=192.168.45.128 \
PADO_OPERATOR_CIDR=192.168.45.1/32 \
bash pado-validation.sh start
```

검증 환경의 기본 시험은 PASS 대기 없이 연속 연결합니다. MPS 또는 ROS의
**Validation Packets**에서 TC 시퀀스와 10,000개 정상·손상·급변 TM 검증을
예약하고 송신 RAW, 수신 원문·파싱 값, 거부 사유와 센서 이벤트를 확인합니다.
자세한 순서는 [TC·TM 검증 절차](docs/TC_TM/VALIDATION.md)에 있습니다.

```sh
# 업데이트: 기존 데이터와 계정을 유지
git switch main
git pull --ff-only origin main
bash pado-validation.sh start

# 상태 확인 / 실행 중지
bash pado-validation.sh status
bash pado-validation.sh stop
```

운용 구성은 위 명령의 `pado-validation.sh`를 `pado-operations.sh`로 바꿉니다.
`stop`은 데이터를 보존합니다. `cleanup`은 확인 후 해당 환경의 컨테이너·네트워크와
데이터 볼륨을 삭제하므로 시험 결과를 유지하려면 사용하지 않습니다.
호스트 설정 파일과 두 환경이 사용하는 공통 이미지는 유지합니다.
기존 개발 스택 실행기 `pado-gs.sh`도 호환성을 위해 제공합니다.
상세 배포·방화벽·로그 절차는 [배포 안내](deploy/README.md)를 참고하세요.

## 주요 기능과 범위

| 구성 | 기능 |
|---|---|
| DGS | 외부 접점 통제, 수신 패킷 검증·격리, TC 송신 원문·TM 수신 기록 |
| ROS | 장비별 계측·고정 CAD 연결선, 센서 그래프·한계 초과 이벤트, RAW·파싱 조회, CAD 자세 표시·최근 3일 이력, TC 예약·응답 확인 |
| MPS | 두 지상국 PASS와 ground track·polar plot·도플러 그래프·CSV 조회, TC 순서·인자·AOS 기준 시퀀스 구성 |
| FDS | TLE/SGP4와 GNSS 기반 예비 수치 전파, 최대 72시간 궤도력·PASS·도플러 산출 |
| 검증 위성 | 500 km·98.4°·LTAN 12h 시험 궤도, −X Nadir, GNSS·장비 HK·전력/배터리 모델, 비콘·TC·저장 TM 왕복 |

ORBIT의 흰 경계는 현재 FDS 위치에서 선택한 최소 고각(기본 0°·지평선) 이상으로
위성을 볼 수 있는 지표 영역을 3D와 Ground track에 함께 표시합니다. 구형 지구의
기하학적 범위이며, 지상국별 컨택 예측은 기존 FDS PASS를 따릅니다.
ROS 종합 화면의 장비 위치·TM 연결은 [스택 배치 설정](docs/STACK_LAYOUT.md)에서
관리합니다. 수신한 측정 UTC·허용 범위·최신성을 함께 표시합니다.

현재 구현과 시험은 지상국 개발 기준선입니다. 실제 비행 TC/TM·인증·ACK 규격,
RF/CSP/AX100 및 항우연·연세대 연결, FDS 정확도와 실제 운용 승인 경로는 별도
합의·통합 검증이 필요합니다. 검증 환경의 ACK는 실제 장치 동작 완료를 증명하지
않습니다. FDS 기동 기능, NAS 및 S-band 탑재체 과학자료 수신은 현재 범위에서 제외합니다.

## 저장소 구조

```text
pado-gs/
├── config/       # 공통 물리 설정 예시; 실제 환경별 설정은 Git 제외
├── deploy/       # Compose, 환경 분리·DGS 정책 검사, 게이트웨이, 실행 안내
│   └── stage7/   # 컨테이너 격리 probe와 실제 서버 설정 절차
├── docker/       # 웹·DGS 링크·FDS Dockerfile
├── docs/         # 개발방향, 결정·진행 기록, 기능·인터페이스·복구 문서
├── interfaces/   # N2, 패킷 카탈로그, 스키마와 시험 계약
├── libgs/        # C 공통 검증·파일 교환·TLS/SFTP/Unix socket 라이브러리
├── services/     # 서브시스템 C·Python 코드와 FDS Java/Maven 코드
├── sim/          # C 경계 시험용 외부 지상국·위성 모듈
├── tests/        # 단위·통합·컨테이너 시험
├── third_party/  # 버전·출처를 고정한 외부 소스
├── tools/        # 전파 파이프라인·게시·검사·자료 생성 도구
└── web/          # 공통 리소스와 로그인·ROS·MPS·ORBIT·검증 화면
```

`out/`, `build*/`, FDS의 `target/`은 재생성 가능한 로컬 출력입니다.
Java의 `src/main/java/kr/ac/khu/padosat/fds/` 경로는 Maven·패키지 규칙을 따릅니다.
배포 파일의 개발 단계 번호 중첩은 제거하되, 독립 격리 시험인 `stage7/`은 유지합니다.
복구·퍼징 절차는 [복구 안내](docs/RECOVERY.md)에 있습니다.

## 빌드와 시험

Linux C 빌드는 CMake, Ninja, GCC/Clang, OpenSSL·libssh 개발 헤더를 사용합니다.
TLS/SFTP 시험에는 `openssl`, `ssh-keygen`, `sshd`도 필요합니다.

```sh
cmake -S . -B build-linux-release -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=gcc
cmake --build build-linux-release --parallel 2
ctest --test-dir build-linux-release --output-on-failure
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

```sh
cmake -S . -B build-linux-sanitized -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER=clang -DGS_ENABLE_SANITIZERS=ON
cmake --build build-linux-sanitized --parallel 2
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 ctest --test-dir build-linux-sanitized --output-on-failure
```

Windows에서는 Visual Studio 2022 C 도구와 CMake를 갖춘 개발자 PowerShell에서
다음처럼 실행합니다. Linux 전용 통신 시험은 Linux CI에서 검증합니다.

```powershell
cmake -S . -B build-msvc -G "Visual Studio 17 2022" -A x64
cmake --build build-msvc --config Release --parallel 2
ctest --test-dir build-msvc -C Release --output-on-failure
python -m unittest discover -s tests -p 'test_*.py' -v
```

FDS는 Java 17·Maven·Orekit을 사용합니다. 입력·물리 자료·자체 시험은
[FDS 안내](services/fds/README.md)를 따릅니다. Docker 네트워크 시험은
`python3 tests/container/run_network.py --build`로 실행하며, 전체 Linux 빌드·정적 분석·
ASan/UBSan·FDS·컨테이너 시험은 [GitHub Actions](.github/workflows/ci.yml)에서 수행합니다.

비밀번호·토큰·개인키와 실제 데이터는 Git에 저장하지 않습니다.
커밋 전에 `python tools/check_secrets.py --staged`를 실행합니다.
현재 상태와 남은 외부 협의는 [진행 기록](docs/PROGRESS.md),
[결정 기록](docs/DECISIONS.md), [TC·TM 검토표](docs/TC_TM/REVIEW_GATES.md)를 참고하세요.
