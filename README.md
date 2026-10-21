# PADO-SAT Ground System

## 개요

경희대학교 PWNLAB의 PADO-SAT 큐브위성 지상국 소프트웨어 저장소입니다. 작업 폴더 안의 `pado-gs/`가 Git 저장소 루트입니다. 개발방향은 온프레미스 VM과 컨테이너를 배포 단위로 삼고, VM을 보안 경계로 사용합니다. 현재는 첫 세션의 저장소·빌드·시험 골격만 있으며, 위성에 송신하거나 외부 지상국에 연결하는 실행 기능은 없습니다.

기준 문서: [개발방향](docs/PADO-SAT_지상국_개발방향.md), [보안 계획서](docs/PADO-SAT_지상국_보안설계_개발방향계획서.md), [진행 기록](docs/PROGRESS.md), [결정 기록](docs/DECISIONS.md)

## 진행 상태

| 단계 | 내용 | 상태 | 비고 |
|---|---|---|---|
| 초기 구성 | 저장소·빌드·시험 골격 | 완료 | Windows 일반·ASan 시험 통과; Linux UBSan 미실행 |
| 1 | `interfaces/`, `libgs/` | 대기 | 세부 계획 검토 필요 |
| 2 | 배포 기반 | 대기 | |
| 3 | TM 수신 경로 | 대기 | |
| 4 | 인증·관제 대시보드 | 대기 | |
| 5 | TC 경로 | 대기 | |
| 6 | MPS | 대기 | |
| 7 | FDS·DGS-NET·아카이브·SDC | 대기 | |
| 8 | 백업·복구·퍼징·내부 점검 | 대기 | |

## 아키텍처

개발방향 4.1절의 VM은 `vm-portal`, `vm-ros`, `vm-mps`, `vm-fds`, `vm-dgs-archive`, `vm-dgs-gs`, `vm-dgs-net`, `vm-mgmt`입니다. 이 저장소에는 아직 VM 배포 설정이 없습니다.

계획된 TC 경로는 MPS 시퀀스 승인 → `ros-core` 명령 DB 재검증 → `ros-signer` 승인 확인·HMAC → TLS → `dgs-gs` `tc-gate` → `kari-adapter`입니다. 현재 이 경로는 구현되지 않았습니다.

## 저장소 구조

| 디렉터리 | 계획된 역할 |
|---|---|
| `interfaces/` | N2 흐름, 명령·TM DB, 스키마, 프레임 규격 |
| `libgs/` | 공통 C 라이브러리; 현재 빌드 확인용 임시 심볼만 있음 |
| `third_party/` | 승인된 소스 내장 의존성 |
| `services/` | 서브시스템 서비스 |
| `web/` | 로컬 웹 리소스 |
| `docker/`, `deploy/` | 컨테이너 및 VM 배포 설정 |
| `sim/` | 외부 연동 시뮬레이터 |
| `tests/` | 단위·통합 시험 |
| `tools/` | 검사·운영 도구; 현재 비밀정보 검사만 있음 |

## 빌드와 시험

Windows에서는 Visual Studio 2022의 C 도구와 내장 CMake가 필요합니다. 아래는 이 환경에서 실행해 통과한 PowerShell 명령입니다. `14.44.35207`은 이 PC에 설치된 도구 버전이므로 다른 PC에서는 설치 버전에 맞춥니다.

```powershell
$cmake = 'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$ctest = 'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe'
& $cmake -S . -B build-msvc-1444 -G 'Visual Studio 17 2022' -A x64 -T 'v143,version=14.44.35207'
& $cmake --build build-msvc-1444 --config Release --parallel 1 -- /nr:false /p:VCToolsVersion=14.44.35207
& $ctest --test-dir build-msvc-1444 -C Release --output-on-failure
python -m unittest discover -s tests -p test_check_secrets.py -v
```

AddressSanitizer 빌드·시험:

```powershell
& $cmake -S . -B build-asan -G 'Visual Studio 17 2022' -A x64 -T 'v143,version=14.44.35207' -DGS_ENABLE_SANITIZERS=ON
& $cmake --build build-asan --config Release --parallel 1 -- /nr:false /p:VCToolsVersion=14.44.35207
$env:PATH = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64;' + $env:PATH
& $ctest --test-dir build-asan -C Release --output-on-failure
```

GNU/Clang 빌드에는 개발방향 11.5절의 경고·하드닝 옵션을 적용하고, `GS_ENABLE_SANITIZERS=ON`은 AddressSanitizer와 UndefinedBehaviorSanitizer를 함께 활성화합니다. MSVC는 대응 보안 옵션을 사용하며 sanitizer 빌드에는 AddressSanitizer만 제공합니다. Linux 빌드와 UndefinedBehaviorSanitizer 시험은 아직 실행하지 못했습니다.

## 로컬 개발 환경

현재 서비스와 시뮬레이터가 구현되지 않아 전체 경로 실행 방법은 없습니다.

## 보안 주의 사항

비밀 키, 인증서 개인키, 비밀번호, 토큰과 로컬 데이터는 Git에 넣지 않습니다. `.gitignore`와 `.githooks/pre-commit`의 검사는 기본 방어이며, 커밋 전 `python tools/check_secrets.py --staged`로 색인 전체를 검사합니다. 이 검사는 알려진 파일 경로와 일부 자격증명 패턴을 감지하므로 전문 비밀정보 감사의 대체재가 아닙니다.

TC는 계획된 검증·승인·서명·게이트 경로로만 송출하며, 위험 TC는 작성자와 승인자가 달라야 합니다. 관련 기능은 아직 구현되지 않았습니다.

## 미결 사항

- TBD(ICD): 항우연·연세대 연동 포트, 프로토콜, 프레임 형식
- TBD(HMAC): 트랜시버 HMAC 세부 사양
- 단계 1의 명령·TM 기준 데이터와 스키마 필드 확정

## 변경 이력 요약

단계 완료 이력은 아직 없습니다. 세부 작업 기록은 [진행 기록](docs/PROGRESS.md)을 참조합니다.
