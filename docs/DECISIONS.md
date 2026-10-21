# 결정 기록

## #1 첫 세션 빌드 골격

- 날짜: 2026-10-21
- 배경: 개발방향 11.2절에는 `libgs/` 구조가 정의되어 있고, 첫 세션 지시는 비어 있는 공통 라이브러리의 실제 빌드와 시험 실행 구조 확인을 요구한다.
- 선택지: (a) CMake `INTERFACE` 라이브러리만 선언 (b) 임시 C 심볼을 가진 정적 라이브러리를 빌드
- 결정: (b) `gs_bootstrap_ready`를 임시 심볼로 두고 CTest에서 링크·호출한다. 첫 실제 `libgs` 모듈을 구현할 때 제거한다.
- 이유: 컴파일과 링커가 모두 실제로 동작했는지 확인할 수 있다.
- 영향: 이 심볼은 운용 기능이 아니며 어떤 서비스도 호출하지 않는다. Windows 개발 환경에서는 MSVC의 대응 보안 옵션을 쓰고, Linux GNU/Clang 빌드는 개발방향 11.5절의 옵션을 적용한다. MSVC에는 UndefinedBehaviorSanitizer가 없어 Linux sanitizer 시험이 별도로 필요하다.

## #2 Payload Raw Data 회수 경로

- 날짜: 2026-10-21
- 배경: 원본 N2 chart는 DGS→CDPS/SDPS의 CREST/SIREN Raw Data를 요구한다. 보안 계획서 7.4절 R06은 DGS-GS→DGS 내부 데이터 방향을 지정하지만 개발방향 6.2절에는 대응 연결이 없었다. DMZ에서 운용 VLAN으로 새 연결을 시작할 수 없다.
- 선택지: (a) DGS-Archive가 DGS-GS에서 SFTP로 회수 (b) 기존 ROS 실시간 연결을 통해 중계 (c) 경로 확정 보류
- 결정: (a), 사용자 선택. DGS-Archive가 DGS-GS의 별도 읽기 전용 계정에 접속한다. 데이터는 DGS-GS에서 DGS-Archive로 이동한다.
- 이유: DMZ의 신규 내부망 연결 금지와 SDC의 Archive 전용 접근을 유지하면서 N2 데이터 방향을 충족한다. ROS의 TC/TM 실시간 경로에 대용량 원시데이터를 싣지 않는다.
- 영향: 개발방향 6.2절에 F19를 추가하고 6.3절 push 원칙의 예외를 기록한다. DGS-GS에는 용량·시간이 제한된 임시 영역과 수신 확인 후 삭제가 필요하다. 제한값과 수신 확인 프로토콜 세부는 원시데이터 규모·주기를 확인해 결정한다(TBD(ICD)). 방화벽·SFTP 계정은 단계 2에서 `check_flows`와 함께 구현한다.

## #3 TLE 수신처

- 날짜: 2026-10-21
- 배경: 원본 PDF N2 표는 DGS→ROS에 `TM, TLE`라고 적지만 같은 PDF의 설명과 개발방향 6.2절은 TLE를 FDS로 전달한다.
- 선택지: (a) FDS 수신 (b) ROS에도 전달 (c) 두 경로 보류
- 결정: (a), 사용자 선택. DGS-NET에서 검증한 TLE를 FDS로 전달한다.
- 이유: FDS가 궤도 전파와 패스 계산을 담당하며, 개발방향의 기존 F07과 일치한다.
- 영향: `interfaces/n2_chart.json`은 원본 표의 모순을 출처 기록으로 보존하되 ROS로의 TLE 전달을 허용하지 않는다.

## #4 미확정 TC·TM 기준 데이터

- 날짜: 2026-10-21
- 배경: 원본 PDF와 개발방향에는 명령 코드·파라미터 범위, TM 패킷 식별자·인코딩·Limit의 실제 값이 없다.
- 선택지: (a) 임의의 예시 값을 운용 DB에 넣기 (b) 빈 버전형 DB와 확장 가능한 스키마를 만들고 실제 값은 ICD 확정 후 추가
- 결정: (b), 사용자 지시. `operational: false`, 빈 `commands`·`parameters`, `TBD(ICD)` 상태로 둔다.
- 이유: 미확정 값이 실제 TC/TM 처리에 사용되는 것을 방지하면서 파일 경로와 버전형 계약을 마련한다.
- 영향: 실제 항목 스키마, 명령 DB 해시, TM 파서는 ICD 확정 후 구현·검증한다. 현재 DB를 로드하는 서비스는 없어 송출 기능이 없다.

## #5 단계 1 JSON·해시 구현

- 날짜: 2026-10-21
- 배경: 개발방향 9.2절은 JSON 매니페스트와 SHA-256 검증을 요구한다. 개발 환경은 Windows MSVC이고 목표 배포 환경은 Linux다.
- 선택지: (a) 직접 JSON·SHA-256 구현 (b) 개발방향 11.1절의 cJSON과 플랫폼 암호 API 사용
- 결정: (b). cJSON v1.7.19를 소스·라이선스와 함께 고정하고, Windows는 CNG BCrypt, Linux는 OpenSSL EVP를 사용한다.
- 이유: 자체 암호 알고리즘 구현을 피하고 두 환경에서 동일한 SHA-256 출력 계약을 시험한다.
- 영향: Linux 빌드에는 OpenSSL 개발 패키지가 필요하다. Windows 일반·ASan 시험은 수행했으나 Linux GNU/Clang·UBSan 시험은 아직 수행하지 못했다. `third_party/README.md`에 출처·해시·업데이트 주기를 기록한다.

## #6 Linux sanitizer 검증 자동화

- 날짜: 2026-10-21
- 배경: 개발 지시서 5.2절은 Linux `-fsanitize=address,undefined` 시험을 요구하지만 현재 Windows 작업 환경에는 Linux GNU/Clang, WSL, Docker가 없다.
- 선택지: (a) GitHub Actions Ubuntu CI 추가 (b) 로컬 Linux 환경을 기다림 (c) Windows ASan 결과만 유지
- 결정: (a), 사용자 승인. `.github/workflows/` 디렉터리에 Linux 일반·ASan+UBSan 빌드 및 시험을 추가한다.
- 이유: 같은 비공개 저장소의 커밋에서 재현 가능한 필수 검증을 수행한다.
- 영향: 푸시마다 GitHub Actions 사용 시간이 소모된다. `actions/checkout`은 검증한 v6.1.0 커밋 SHA(Node.js 24)로 고정한다. CI 성공을 확인하기 전까지 단계 1 완료로 표시하지 않는다.
