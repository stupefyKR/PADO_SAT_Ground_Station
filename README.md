# PADO-SAT Ground System

## 개요

경희대학교 PWNLAB의 PADO-SAT 큐브위성 지상국 소프트웨어 저장소입니다. 작업 폴더 안의 `pado-gs/`가 Git 저장소 루트입니다. 2026-10-23 사용자 결정에 따라 실제 운영은 **Linux VM 한 대에서 서브시스템별 Docker 컨테이너**로 구성합니다. 단계 1의 인터페이스 기준선부터 단계 6의 비운용 파일 교환 기준선까지 있으며, 단계 7의 Docker 연결·로그 검증을 진행 중입니다. 위성에 송신하거나 실제 외부 지상국에 연결하는 실행 기능은 없습니다.

GitHub 원격 저장소는 비공개로 설정되어 있습니다(2026-10-21 확인). 초기 푸시 뒤 공개 설정을 발견해 즉시 비공개로 전환한 기록은 [진행 기록](docs/PROGRESS.md)에 남겼습니다.

기준 문서: [원본 N2 포함 제안서](docs/지상국.pdf), [개발방향](docs/PADO-SAT_지상국_개발방향.md), [보안 계획서](docs/PADO-SAT_지상국_보안설계_개발방향계획서.md), [진행 기록](docs/PROGRESS.md), [결정 기록](docs/DECISIONS.md)

## 진행 상태

| 단계 | 내용 | 상태 | 비고 |
|---|---|---|---|
| 초기 구성 | 저장소·빌드·시험 골격 | 완료 | Windows 일반·ASan 시험 통과; 이후 단계 1에서 Linux CI 검증 |
| 1 | `interfaces/`, `libgs/` | 완료(기준선) | N2 21개 원본 화살표, 승인 흐름 21개, Windows·Linux 매니페스트·로그·파일 교환 시험 통과; TC/TM 실값은 ICD 대기 |
| 2 | 서브시스템 간 통신 기반 | 완료(개발 기준선) | VMware Linux VM의 Docker 격리 네트워크에서 mTLS·SFTP 송수신/거부 시험 통과 |
| 3 | TM 수신 경로 | 완료(시뮬레이터 기준선) | `fake_kari` → `dgs-gs` → `ros-core`; 실제 ICD·TM 해석 대기 |
| 4-1 | MPS 입력·카탈로그 기준선 | 완료(비운용 기준선) | 빈 명령 DB만 판독; 내부 패스 모델 검증, 실제 FDS 형식 대기 |
| 4-2 | 시퀀스 초안·버전 이력 | 완료(비운용 기준선) | 패스·DB 버전 결합, 불변 이력, 동시 수정 거부; 영속 저장소 미연결 |
| 4-3 | 충돌 분석 | 완료(시험 계약 기준선) | 6개 규칙의 시험용 속성 계약; 실제 명령 속성은 ICD 대기 |
| 4-4 | 승인·출력·ROS 연동 | 완료(비운용 시험 기준선) | 최신 초안 승인, `TEST_ONLY_SEQUENCE`·매니페스트, ROS 독립 재검증; 실제 TC ICD 대기 |
| 4-5 | Mission Request·업링크 반입 | 완료(비운용 시험 기준선) | 두 단계 출처 체인·해시·버전·권한·FlatSat 기록 ID 검사; 외부 본문 형식 대기 |
| 5 | TC 검증→승인→signer→tc-gate→`fake_sat` ACK | 완료(비운용 시험 기준선) | 위험 TC 별도 lead 승인·게이트 차단 시험 통과; 실제 명령·프레임·HMAC 사양 대기 |
| 6 | FDS·DGS-NET·아카이브·SDC 교환 | 완료(비운용 파일 교환 기준선) | N05·N08–N18 경로·유형·SFTP 시험 통과; 외부 수집·Orekit·영속 보관은 미구현 |
| 7 | 단일 VM의 Docker·UFW 배포 기반 | 진행 중 | N2 기반 연결 쌍·Docker 격리·journald·호스트 UFW 시험 통과; 운용 서비스 배포와 연결 시작 방향 검증 남음 |
| 8 | 백업·복구·퍼징·핵심 서비스 보안 시험 | 대기 | |
| 9 | 포털 인증·ROS/MPS 웹 UI | 대기 | 관제용 PC에서 포털로 접속 |

## 아키텍처

이전 개발방향 4.1절의 VM 8대 구성은 [결정 #16](docs/DECISIONS.md)으로 대체했습니다. 목표 구조는 Linux VM 한 대에 Portal, ROS, MPS, FDS, DGS-Archive, DGS-GS, DGS-NET 컨테이너를 두는 것입니다. [단계 7 배포 기준선](deploy/stage7/README.md)의 Compose 파일은 네트워크 검증용이며 운용 앱 배포 파일은 아직 아닙니다.

계획된 운용 TC 경로는 MPS 시퀀스 승인 → `ros-core` 명령 DB 재검증 → `ros-signer` 승인 확인·HMAC → TLS → `dgs-gs` `tc-gate` → `kari-adapter`입니다. 현재 단계 5는 합성 프레임을 이용한 모듈 간 시험 경로이며 실제 송출 경로는 구현되지 않았습니다. [개발방향 12장](docs/PADO-SAT_지상국_개발방향.md), [MPS 시험 계약](interfaces/mps/test_contract.md), [TC 시험 계약](interfaces/tc/test_contract.md)을 참고하세요.

## 저장소 구조

| 디렉터리 | 계획된 역할 |
|---|---|
| `interfaces/` | 원본 N2와 승인된 연결 방향, 비운용 명령·TM DB, 매니페스트 스키마, 프레임 TBD |
| `libgs/` | 매니페스트 파싱·검증, SHA-256, 한 줄 JSON 로그, 원자적 파일 공개·교환 검증 |
| `third_party/` | cJSON v1.7.19 소스·라이선스·해시 |
| `services/` | 서브시스템 서비스 |
| `web/` | 로컬 웹 리소스 |
| `docker/`, `deploy/` | 컨테이너 및 단일 VM 배포 설정 |
| `sim/` | 외부 연동 시뮬레이터 |
| `tests/` | 단위·통합 시험 |
| `tools/` | 인터페이스·비밀정보·배포 흐름 검사 및 호스트 감사 도구 |

## 빌드와 시험

Windows에서는 Visual Studio 2022의 C 도구와 내장 CMake가 필요합니다. 아래는 이 환경에서 실행해 통과한 PowerShell 명령입니다. `14.44.35207`은 이 PC에 설치된 도구 버전이므로 다른 PC에서는 설치 버전에 맞춥니다.

```powershell
$cmake = 'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$ctest = 'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe'
& $cmake -S . -B build-msvc-1444 -G 'Visual Studio 17 2022' -A x64 -T 'v143,version=14.44.35207'
& $cmake --build build-msvc-1444 --config Release --parallel 1 -- /nr:false /p:VCToolsVersion=14.44.35207
& $ctest --test-dir build-msvc-1444 -C Release --output-on-failure
python -m unittest discover -s tests -p 'test_*.py' -v
```

AddressSanitizer 빌드·시험:

```powershell
& $cmake -S . -B build-asan -G 'Visual Studio 17 2022' -A x64 -T 'v143,version=14.44.35207' -DGS_ENABLE_SANITIZERS=ON
& $cmake --build build-asan --config Release --parallel 1 -- /nr:false /p:VCToolsVersion=14.44.35207
$env:PATH = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64;' + $env:PATH
& $ctest --test-dir build-asan -C Release --output-on-failure
```

GNU/Clang 빌드에는 개발방향 11.5절의 경고·하드닝 옵션을 적용하고, `GS_ENABLE_SANITIZERS=ON`은 AddressSanitizer와 UndefinedBehaviorSanitizer를 함께 활성화합니다. MSVC는 대응 보안 옵션을 사용하며 sanitizer 빌드에는 AddressSanitizer만 제공합니다.

단계 2 이후 Linux 빌드에는 `libssh-dev`와 `openssh-server`도 필요합니다. CTest는 시험 실행 중 임시 CA·TLS 인증서·SSH 키를 생성하고 127.0.0.2의 임시 SFTP 서버를 사용합니다. 비밀 키와 인증서 개인키는 저장소에 생성하지 않습니다. `libgs`는 상호 인증 TLS 길이 접두 프레임, Unix socket JSON 프레임, SSH 호스트 키 고정 SFTP 파일·매니페스트 업로드와 읽기 전용 회수 경로를 제공합니다. 단계 3 서비스는 `--sim-only` 시험 경로로만 실행되며 VM 배포 설정은 아직 없습니다.

단계 4-4·4-5는 Windows와 Linux CTest의 `gs_stage4_tests`로 승인·출력·ROS 재검증 및 요청·업링크 반입의 허용·거부 경로를 확인합니다. 검증 결과는 [진행 기록](docs/PROGRESS.md)에 남깁니다. VMware Ubuntu 26.04 VM 안의 Ubuntu 24.04 시험 컨테이너에서 단계 2의 컨테이너 간 mTLS·SFTP·격리 시험을 수행했습니다. 매니페스트·MPS 입력 파서는 libFuzzer 대상으로 등록했으며 퍼징 실행은 단계 8에서 수행합니다. 공통 SFTP 송수신은 단계 2에 있고, 서비스별 inbox 감시·격리·삭제는 이후 서비스 통합에서 추가합니다.

단계 5의 `gs_stage5_tests`는 합성 TC 검증부터 승인·태그 부착·게이트·`fake_sat` ACK까지의 한 프로세스 시험과 거부 사례를 확인합니다. 시험 키는 실행 중에 생성하며 실제 위성 키나 프레임 형식을 사용하지 않습니다.

단계 6의 `gs_stage6_tests`는 N2 경로표와 코드의 일치, NORAD TLE 형식, Archive의 입력·전달·변조 거부를 검사합니다. Linux `gs_sftp_tests`는 N05·N08–N18의 SFTP 파일 쌍 송수신과 SDC→Archive→MPS 매니페스트 전달을 검사합니다. [시험 계약](interfaces/stage6/test_contract.md)에 실제 운용 경계를 기록했습니다.

`.github/workflows/ci.yml`은 Ubuntu GCC Release, Clang ASan+UBSan, 정적 분석, Python 검사를 푸시마다 실행합니다. 같은 시험을 Linux에서 재현하려면 OpenSSL 개발 헤더, Ninja, GCC, Clang, Python이 필요합니다.

```sh
cmake -S . -B build-linux-release -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=gcc
cmake --build build-linux-release --parallel 2
ctest --test-dir build-linux-release --output-on-failure
cmake -S . -B build-linux-sanitized -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER=clang -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DGS_ENABLE_SANITIZERS=ON
cmake --build build-linux-sanitized --parallel 2
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 ctest --test-dir build-linux-sanitized --output-on-failure
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tools/check_secrets.py --staged
```

## 로컬 개발 환경

단계 3의 `fake_kari` → `dgs-gs` → `ros-core` 수신 경로는 Linux에서 CTest의 `gs_stage3_tests`로 실행합니다. `openssl`이 시험용 CA·상호 인증서를 임시 디렉터리에 만들고, 두 유효 프레임의 ROS 기록과 다섯 무효 입력의 DGS-GS 격리를 검사합니다. 프레임은 [시험 전용 규격](interfaces/frames/simulator.md)이며 실제 TM 항목을 해석하지 않습니다.

단계 4-1의 `gs_mps_inputs_tests`는 Windows와 Linux CTest에 포함됩니다. 현재 `interfaces/command_db/commands.json`의 비운용 상태를 확인하고, 운용 활성화·미정 명령 항목·비정상 JSON을 거부합니다. 패스 목록은 ID와 UTC AOS/LOS, 여유 시간, 개수 경계를 확인하는 내부 모델입니다. 실제 FDS Contact Schedule 파일 형식, 수신·출처 검증과 명령 항목 스키마는 확정 전입니다. 입력 파서는 `GS_BUILD_MPS_CATALOG_FUZZER=ON`으로 Clang libFuzzer 대상으로 빌드할 수 있습니다.

단계 2 공통 통신의 Linux 시험도 위 CMake·CTest 명령으로 실행합니다. Docker가 있는 개발용 Linux VM에서는 다음 명령으로 두 컨테이너의 실제 네트워크 주소를 이용해 상호 인증 TLS, SFTP 파일·매니페스트, 잘못된 상대 인증서와 분리된 네트워크의 접속 거부를 시험합니다. `openssl`과 `ssh-keygen`이 필요하며, 시험 키·인증서는 임시 디렉터리에 생성됩니다. 스크립트는 시험 컨테이너·네트워크를 제거하고, 빌드된 이미지 `pado-stage2-test:local`은 재사용할 수 있게 남깁니다.

```sh
python3 tests/container/run_network.py --build
```

이 시험은 VMware의 단일 Linux VM 안에서 수행했습니다. 운영도 단일 VM으로 변경했으며, 단계 7에서는 컨테이너 연결 쌍·호스트 UFW·로그를 별도로 검증합니다. [단계 7 절차](deploy/stage7/README.md)를 참고하세요.

## 보안 주의 사항

비밀 키, 인증서 개인키, 비밀번호, 토큰과 로컬 데이터는 Git에 넣지 않습니다. `.gitignore`와 `.githooks/pre-commit`의 검사는 기본 방어이며, 커밋 전 `python tools/check_secrets.py --staged`로 색인 전체를 검사합니다. 이 검사는 알려진 파일 경로와 일부 자격증명 패턴을 감지하므로 전문 비밀정보 감사의 대체재가 아닙니다.

TC는 계획된 검증·승인·서명·게이트 경로로만 송출하며, 위험 TC는 작성자와 승인자가 달라야 합니다. 현재 검증·승인·서명·게이트 로직은 시험용으로 구현됐고 실제 송출 기능은 없습니다.

## 미결 사항

- TBD(ICD): 항우연·연세대 연동 포트, 프로토콜, 프레임 형식
- TBD(HMAC): 트랜시버 HMAC 세부 사양
- TBD(ICD): TC 명령·TM 실제 항목과 항목 스키마. 현재 DB는 `operational: false`
- TBD(ICD): DGS-GS 원시데이터 임시 영역의 용량·보관시간·회수 주기

## 변경 이력 요약

단계 1 인터페이스·공통 파일 교환 기준선은 2026-10-21 완료했습니다. 세부 시험·결정 기록은 [진행 기록](docs/PROGRESS.md)을 참조합니다.
