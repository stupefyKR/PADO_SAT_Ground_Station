# 진행 기록

## 현재 상태

- 현재 단계: 단계 1–8 개발 기준선 완료, 단계 9 비운용 읽기 전용 웹 경계 진행 중. 9-1에서 C 명령 DB 파서·시험 TM 기록 검증기를 웹 API에 연결. 실제 VM·독립 백업·복원·FlatSat 적용은 모든 개발 후 배포 수락 절차로 이관. NAS 연동은 별도 후속 과제로 제외(결정 #19)
- 다음 작업: 단계 9에서 상주 ROS/MPS C 서비스·영속 상태와 웹 API를 연결하고, 확정된 ICD 기능을 화면에 추가. 운용 명령 항목·FDS 파일·Mission Request 본문·트랜시버 HMAC 형식은 확정 뒤 연결
- 미결 사항: TBD(ICD) 항우연·연세대 연동 포트·프로토콜·프레임, TC 명령·TM 실제 항목, DGS-GS 임시 원시데이터 용량·보관시간·회수 주기; TBD(HMAC) 트랜시버 세부 사양

## 기록

### 2026-10-23 — 단계 9-1 비운용 C 코어 읽기 연결

- ROS API는 단계 3 시뮬레이터 기록을 C 프레임 검증기로 확인해 시험 프레임 수와 마지막 순번을 반환한다. MPS 명령 목록 API는 기존 C 명령 DB 파서가 확인한 버전과 빈 목록을 반환한다. 기록 부재는 0건, 손상 파일이나 잘못된 명령 DB는 실패로 처리한다.
- 다단계 컨테이너 빌드에서 C 프로브를 컴파일하고 ROS 기록 볼륨을 API에 읽기 전용으로 연결했다. 현재 Compose에는 해당 볼륨에 기록하는 ROS 코어 서비스가 없다. TC/TM·FDS ICD, 계획 영속 저장, 관제 PC 접속 시험은 여전히 미완료이며 단계 9 전체 완료로 판정하지 않는다(결정 #21).
- 검증: 로컬 Python 38개 시험 중 환경 의존 6개를 제외하고 통과. [Ubuntu CI #47](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39902404739)에서 GCC Release, Clang ASan+UBSan, C 프로브 유효·변조 거부 및 포털 통합 시험, 정적 분석, 기존 퍼징·격리 시험, Stage 9 컨테이너 빌드 전체 성공.


### 2026-10-23 — 단계 9 포털·ROS/MPS 웹 경계 개발 진행

- Argon2id 계정, lead TOTP, 재사용 거부, 잠금, 유휴·절대 세션 만료, HttpOnly/Secure/SameSite 쿠키와 CSRF 로그아웃을 구현했다. 초기 계정이나 비밀번호는 저장소에 두지 않는다.
- 별도 ROS/MPS HTTPS API는 포털의 mTLS 클라이언트 인증서를 요구한다. 포털은 로그인한 사용자·역할만 내부로 전달한다. 개발용 Compose에서 백엔드는 내부 브리지에만 연결하고 포털은 VM 루프백만 게시한다.
- ROS/MPS 화면은 비운용 상태·빈 명령·미정 FDS 일정을 읽기 전용으로 보여준다. 현재 C 코어 운용 서비스가 없으므로 실제 승인·송출·업링크·시퀀스 출력 API는 없다. 단계 9 전체 완료로 판정하지 않는다(결정 #20).
- 검증: [Ubuntu CI #44](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39899833305)에서 GCC/Clang·기존 CTest, Argon2id·TOTP·세션·TLS/mTLS·직접 API 거부 Python 시험, 단계 7 컨테이너 시험, 단계 9 Compose 구성·이미지 빌드 전체 성공. Windows 루프백 HTTPS 통합 시험도 통과했다.

### 2026-10-23 — NAS 연동 현 개발 범위에서 제외

- 사용자 결정: 장비·도입 시기 미정인 NAS는 단계 1–9와 현 배포 수락 범위에서 제외한다. 서비스 파일·매니페스트·SHA-256 계약과 로컬 영속 경로를 유지해 추후 장비 선정 시 별도 저장소 연동을 추가한다(결정 #19).
- 현재 로컬 스냅샷은 개발용 복원 시험이며 서버 전체 상실에 대비한 운용 백업은 아니다. 운용 배포에는 NAS와 무관한 독립 백업·복원 방안을 따로 정하고 검증해야 한다.

### 2026-10-23 — 단계 8 백업·복구·파서 퍼징 개발 기준선 완료

- 명시적 파일 목록만 포함하는 로컬 시험 스냅샷 도구 `tools/stage8_snapshot.py` 추가. 외부에 따로 기록한 매니페스트 SHA-256을 요구하고, 경로 탈출·키 경로·심볼릭 링크·변조 파일·기존 복원 목적지 덮어쓰기를 거부함. 암호화·NAS·실행 중 DB 덤프는 제공하지 않음.
- 복원된 비운용 명령 DB·TM DB·N2 파일을 실제 MPS·단계 6 C 시험 바이너리가 판독하고 변조된 스냅샷 복원을 거부하는 통합 시험 추가.
- 매니페스트·MPS 명령 목록 기존 libFuzzer 대상에 합성 TM 프레임·NORAD TLE 대상을 추가. 유효·빈 시드로 각 대상의 짧은 ASan+UBSan 캠페인을 Linux CI에 등록함. 실제 TM ICD 파서는 아직 없음.
- 검증: 로컬 Python 25개 시험 및 N2 검사 통과. [Ubuntu CI #40](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39897193087)에서 GCC/Clang 빌드·CTest, 복원 후 C 파서 판독·변조 거부, 네 파서 ASan+UBSan 퍼징, 정적 분석, 기존 Docker·SFTP·TC 보안 회귀 시험 전체 성공.
- 판정: 단계 8 **개발 기준선 완료**(결정 #18, #19). 독립 백업·복원 방안과 전체 서버·FlatSat 복구는 최종 배포 수락 항목이며 NAS 연동은 별도 후속 과제.

### 2026-10-23 — 단계 7 개발 기준선 완료, 현장 적용 이관

- 사용자 결정: 실제 Linux VM에 운용 서비스와 설정을 적용하는 작업은 모든 개발 후 배포 단계에서 수행한다(결정 #17).
- 완료 범위: N2 18개 확정 연결 계획·미정 3개 비개방, Docker 내부 연결 쌍/비연결 차단, journald 로그 probe, VMware VM UFW·NTP 기준선, 시뮬레이터 DGS-GS·ROS 앱 경계 시험, 서버 설정 매뉴얼. [Ubuntu CI #39](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39896254234) 통과.
- 배포 수락으로 이관: 운용 이미지·권한·영속 볼륨·비밀정보, 모든 N2 경로의 서비스 수준 방향 강제, 다른 출발지 SSH 차단, 로그 보존·재부팅·복구, 외부 ICD·SDC·Space-Track 적용. 단계 7 완료는 운용 배포 완료가 아님. NAS는 결정 #19에 따라 범위에서 제외.

### 2026-10-23 — 단계 7 단일 VM Docker 배포 검증 진행

- 서버 적용 매뉴얼: `deploy/stage7/SERVER_SETUP.md`에 현장값 기록, Docker 설치 확인, UFW·SSH 적용, N2 probe·호스트 감사, journald·NTP, 운용 서비스 배포 선행 조건, 재부팅·복구 판정 절차를 추가. 실제 서비스가 준비되기 전 probe를 운용 배포로 오인하지 않도록 구분함
- 호스트 감사 보완: `docker ps`/`docker inspect` 실패 또는 불완전한 목록을 공개 포트 0개로 통과시키지 않고 `docker_inventory_complete=false`와 오류를 보고하도록 수정
- 실제 앱 프로세스 경계 시험 추가: `tests/container/run_stage7_tm.py`가 제한된 DGS-GS·ROS 컨테이너 안에서 기존 `--sim-only` 실행기를 돌려 정상 TM 기록 2건, 오류 격리 5건, 비허용 상대 IP 거부를 확인함. [Ubuntu CI 실행 #38](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39895951024) 전체 성공. 시험 이미지는 운용 이미지로 취급하지 않음
- 구조 변경: 실제 운영도 Linux VM 한 대의 서브시스템별 Docker 컨테이너로 확정. 이전 다중 VM·VLAN·vm-mgmt 설계는 배포 기준에서 제외(결정 #16)
- 구현: N2의 확정된 연결 시작 방향 18건을 검사하는 `tools/check_flows.py`, 내부 연결 쌍 12개를 별도 비공개 Docker 브리지로 만드는 일회용 probe와 호스트 읽기 전용 감사 도구 추가. 미정 외부 ICD 경로와 공개 포트는 생성하지 않음
- 실제 VMware VM 시험: Docker 29.6.1, 시간 동기 `yes`; 연결된 컨테이너 쌍의 양방향 도달 24건, 연결되지 않은 쌍 거부 18건, 공개 포트 0개, 컨테이너 7개의 journald 시작 로그 확인. Docker 기본 로그 드라이버는 `json-file`이며 probe는 명시적으로 `journald` 사용
- 호스트 방화벽: 사용자가 VM 콘솔에서 UFW를 활성화하고 기존 80·2900·전역 22 허용을 삭제. 최종 기본 정책은 입력·라우팅 차단, 출력 허용이며 `192.168.45.1 → 22/tcp`만 허용. 관리 PC에서 SSH 22 연결 성공, 일시적 2900/tcp 리스너를 실행한 상태에서 2900 연결 거부 확인. UFW 활성화 후 Docker 격리·로그 시험을 다시 실행해 같은 결과를 확인. 시험 리스너와 Docker probe 컨테이너·네트워크는 제거됨
- CI: [Ubuntu 실행 #34](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39894144455)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python 17개, 비밀정보 검사, 단계 2 및 단계 7 Docker 네트워크 시험 성공
- 남은 검증: 다른 출발지의 SSH 거부, 실제 앱 컨테이너 이미지·권한·영속 볼륨·비밀정보 및 시작/복구, N2 연결 방향의 서비스 수준 강제, 로그 보존과 재부팅 복구. 브리지 쌍은 역방향 신규 연결을 차단하지 않으므로 현재 단계 7을 완료로 판정하지 않음


### 2026-10-23 — 단계 6 내부 교환 비운용 기준선 완료

- 구현: N05·N08–N18의 데이터·연결 방향과 유형을 N2 기준표에 대조하는 SFTP 경로표, NORAD TLE 구문·체크섬·위성 ID 검사, Archive 반입·메모리 카탈로그·SDC/MPS 목적지별 매니페스트 생성
- 시험: Windows Release·ASan CTest 각각 6/6, Python 10/10. Linux SFTP 시험에서 12개 경로의 파일 쌍 송수신과 SDC→Archive→MPS 릴레이를 확인. [Ubuntu CI 실행 #31](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39891674446)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 경계: 외부 Space-Track·발사체 연동, Java/Orekit 궤도 계산, FDS 산출물 본문, NAS 영속 저장, VM 계정·방화벽 설정은 아직 구현되지 않음
- 결정: DECISIONS.md #15, `interfaces/stage6/test_contract.md`

### 2026-10-21 — 단계 5 합성 TC 검증·승인·게이트·ACK 기준선

- 구현: 단계 4 시험 시퀀스의 ROS 재검증과 시험 명령 존재·정수 파라미터·스케줄·시간창·상태 조건, 일반/주의/위험 승인 분기, 런타임 시험 키를 받는 signer, tc-gate 형식·SCID·출처·시간·속도 제한, `fake_sat` HMAC·재전송 검사와 ACK
- 시험: 위험 TC 자가 승인·잘못된 역할·재인증 누락, 다섯 자동 검증 항목의 실패, signer 미승인, 게이트 출처·길이·SCID·버전·패스 차단, 태그 변조·재전송 거부 및 정상 ACK. Windows Release·ASan CTest 각각 5/5, Python 10/10 통과. [Ubuntu CI 실행 #27](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39616614345)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 경계: 실제 TC ICD·프레임·HMAC 사양 및 Portal/ROS/DGS 서비스 프로세스 연결이 없어 합성 계약에만 적용. 실제 항우연·위성으로 전송하는 코드 경로는 없음
- 결정: DECISIONS.md #14, `interfaces/tc/test_contract.md`

### 2026-10-21 — 단계 4-4·4-5 비운용 승인·ROS 검증·반입 기준선

- 구현: 최신 초안·패스·DB 버전·충돌 결과에 결합한 별도 lead 시험 승인, `TEST_ONLY_SEQUENCE`와 SHA-256 매니페스트 출력, ROS 별도 파서의 파일 쌍·의미 재검증. SDC→DGS-Archive→MPS 요청·업링크 매니페스트 체인, 반영·반려 판정, 독립 해시·목적·버전·FlatSat 기록 ID·역할 검사
- 시험: 승인 전·오래된 승인·자가 승인·재인증 누락·운용 출력명·해시 변조·매니페스트를 다시 만든 의미 변조·출처/버전 불일치·FlatSat 기록 누락·반입 후 파일 변조 거부. Windows Release·ASan CTest 각각 4/4, Python 10/10, 비밀정보 검사 105개 파일 통과. [Ubuntu CI 실행 #24](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39614494249)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 경계: 실제 TC ICD·명령 DB, FDS Contact Schedule·Mission Request 본문·업링크 정책, 인증/FlatSat 증거 서비스는 아직 미정. 현재 ROS 연결은 로컬 시험 파일 쌍이며 운용 TC 경로를 열지 않음. 전체 단계 4의 운용 완료는 이 계약들이 확정된 후 별도 판정
- 결정: DECISIONS.md #13

### 2026-10-21 — 단계 4-2·4-3 MPS 초안과 충돌 분석 기준선

- 구현: 패스 시간창과 현 비운용 명령 DB 버전에 결합된 초안의 추가·교체·삭제, 불변 리비전 이력, 기대 리비전 기반 동시 수정 거부. 시간창·실행 중첩·자원·사전 조건·업링크 데이터량·명령 수의 여섯 규칙을 각각 함수로 구현
- 경계: 모든 초안과 분석은 비운용. 명령 속성과 분석 프로파일은 `interfaces/mps/test_contract.md`의 합성 시험 계약이며 실제 TC 명령·전송률·자원 비트를 뜻하지 않음. 현재 이력은 프로세스 메모리에만 저장하고 승인, 파일 출력, ROS 전달과 송출은 없음
- 시험: Windows MSVC Release·ASan CTest 각 3/3, Python 10/10, 비밀정보 검사 98개 파일 통과. [Ubuntu CI 실행 #20](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39611582454)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 결정: 비운용 계획·분석 속성 계약과 영속 저장 보류 → DECISIONS.md #12
- 다음: 단계 4-4. 실제 TC ICD와 명령 DB 해시 계약이 확정되기 전에는 운용 TC 시퀀스의 승인·출력을 완료 판정하지 않음

### 2026-10-21 — 단계 4 세분화와 4-1 MPS 입력 기준선

- 분할: 단계 4를 4-1 입력·카탈로그, 4-2 초안·버전, 4-3 충돌 분석, 4-4 승인·출력·ROS 연동, 4-5 Mission Request·업링크 반입으로 나눔. 웹 UI는 기존 단계 9에 유지
- 구현: `services/mps/core/inputs.c`에 현재 비운용 명령 DB의 정확한 봉투 판독과 내부 패스 목록 모델 추가. 운용 활성화, 미정 명령 항목, 중복 JSON 키, 길이 초과를 거부하고 패스 ID·AOS/LOS·여유 시간·목록 상한을 검사함
- 범위: 명령 DB는 계속 `operational: false`, 실제 TC 명령 0건. FDS Contact Schedule 파일 형식·출처 검증이 미정이라 내부 모델만 제공하며 외부 파일 수신, 시퀀스 승인·출력과 TC 송출 경로는 없음
- 시험: Windows MSVC Release·ASan CTest 각 2/2, Python 10/10, 비밀정보 검사 통과. [Ubuntu CI 실행 #16](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39574837655)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 기존 격리 컨테이너 시험 전체 성공. MPS JSON 파서의 libFuzzer 대상 등록, 퍼징 실행은 단계 8
- 결정: 단계 구분과 외부 입력 미정 처리 → DECISIONS.md #11
- 다음: 단계 4-2의 초안 편집·버전 이력

### 2026-10-21 — 단계 3 시험용 TM 수신 경로 완료

- 구현: `fake_kari`의 길이 접두 시험 프레임을 DGS-GS가 입력받아 길이·동기값·버전·종류·합성 SCID·CRC를 검증. 잘못된 입력은 사유 태그와 함께 비공개 파일에 격리하고 정상 프레임만 ROS가 시작한 상호 인증 TLS 연결로 전달. ROS는 프레임을 다시 검증한 후 원본 바이트를 기록하고 SHA-256을 포함한 JSON 이벤트를 남김
- 범위: 실행 파일은 `--sim-only`를 요구하며 시험용 규격은 `interfaces/frames/simulator.md`에 한정. 실제 외부 지상국 접속, 운용 TM DB와 파라미터 해석, 원시데이터 보관 정책은 미구현·미정
- 시험: 정상 프레임 2건 ROS 기록, CRC·SCID·동기값·길이 오류 5건 DGS-GS 격리, 개인 파일 권한을 통합 시험으로 검증. [Ubuntu CI 실행 #13](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39567480530)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 및 격리 컨테이너 시험 전체 성공
- 결정: 합성 프레임과 운영 ICD 분리 → DECISIONS.md #10
- 남은 검증: 실제 항우연·연세대 프레임의 수신·해석은 서명된 ICD 확보 후. 서브시스템별 VM 배포·UFW는 단계 7
- 다음: 단계 4 MPS 핵심 기능

### 2026-10-21 — 단계 2 개발용 컨테이너 네트워크 검증

- 환경: 사용자가 제공한 VMware Ubuntu 26.04 VM 한 대의 Docker 29.6.1. 최종 서브시스템별 VM 구조를 대체하지 않는 개발용 격리 네트워크로 사용
- 구현: `tests/container/`에 Ubuntu 24.04 시험 이미지, 서로 다른 컨테이너 IP의 mTLS 왕복·상대 IP SAN 거부, SSH 호스트 키 고정 SFTP ACK 업로드·F19 원시데이터 회수·오류 거부, 연결되지 않은 Docker 네트워크의 접속 거부 시험 추가. 비밀 키·인증서는 실행 시 임시 생성
- 시험: VM 시험 컨테이너에서 GCC Release CTest 3/3, Clang ASan+UBSan CTest 3/3 통과. 컨테이너 간 mTLS·SFTP·격리 시험 통과. 단계 2 개발 기준선 완료
- 남은 검증: 실제 서브시스템별 VM 네트워크, UFW, 호스트·장비 격리와 서비스 배포는 단계 7에서 수행
- 다음: 단계 3 모의 TM 수신 경로

### 2026-10-21 — 단계 2 공통 전송 기반

- 완료: Linux `libgs`에 상호 인증 TLS 길이 접두 프레임, VM 내부 Unix socket JSON 프레임, 호스트 키 고정 SFTP 데이터·매니페스트 업로드/회수 구현. 임시 인증서·SSH 키를 생성하는 통합 시험 추가
- 시험: Windows MSVC Release·ASan CTest 각 1/1, Python 10/10, 비밀정보 검사 통과. [Ubuntu CI 실행 #9](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39561766519)에서 GCC Release·Clang ASan+UBSan CTest 각 3/3, clang-tidy, Python·비밀정보 검사 통과
- 변경 파일: `libgs/include/gs/`, `libgs/src/`, `tests/`, `CMakeLists.txt`, `.github/workflows/ci.yml`, `README.md`, `docs/`
- 결정: 전송 계약과 제한값 → DECISIONS.md #8
- 당시 미완료: 실제 서브시스템별 VM 사이 연결·격리 시험. 이후 제공된 VMware VM의 Docker 격리 네트워크에서 개발용 통신 시험을 수행함
- 다음: 실제 VM 배포 검증은 단계 7에서 수행

### 2026-10-21 — 앱 우선 개발 순서 조정

- 결정: 서브시스템별 VM을 단일 Linux 서버에서 실행하는 구조와 관제용 PC → vm-portal 진입 경로를 유지한다. VM 간 통신과 핵심 기능을 먼저 구현하고 ROS·MPS 웹 UI를 마지막 개발 단계로 옮긴다.
- 변경: 개발방향 12장의 단계·완료 기준과 README의 단계 표를 갱신하고 결정 기록 #7을 추가함
- 미결: 하이퍼바이저 제품, 서버 사양, 물리 VLAN·방화벽 장비 및 UFW 적용 방식. 실제 배포·격리 검증은 수정된 단계 7에서 수행
- 다음: 수정된 단계 2의 확정된 VM 간 스트림·파일 교환 계약과 시험 환경 구현

### 2026-10-21

- 완료: 작업 폴더의 `pado-gs/`를 독립 개발 저장소로 구성하고, 11.2절 디렉터리 골격, 기준 문서 2종, `.gitignore`, 커밋 전 비밀정보 검사, CMake 정적 라이브러리 및 CTest 골격 작성
- 시험: Windows MSVC Release 빌드 경고 0, CTest 1/1 통과; MSVC AddressSanitizer 빌드 경고 0, CTest 1/1 통과; 비밀정보 검사 단위 시험 4/4 통과. Linux GNU/Clang 및 UndefinedBehaviorSanitizer 미실행
- 변경 파일: `CMakeLists.txt`, `libgs/`, `tests/`, `tools/check_secrets.py`, `.githooks/`, `docs/`, `README.md`
- 결정: 초기 빌드 확인용 임시 `gs_bootstrap_ready` 심볼과 Windows 빌드 옵션 → `DECISIONS.md` #1
- 문제: MSVC 기본 도구 선택 시 PDB 도구 버전 충돌이 발생하여, 설치된 `14.44.35207` 도구 버전을 빌드 명령에 지정함
- 원격 공개 범위: 첫 푸시 직후 GitHub 익명 API에서 공개 저장소로 확인됨. 사용자 확인을 받아 비공개로 전환했고, 설정 화면의 `currently private` 표시 및 익명 API 404를 확인함. 공개 상태였던 시간 동안 외부 열람 가능성은 배제할 수 없음
- 다음: 단계 1 세부 작업 계획 검토 후 구현 시작

### 2026-10-21 — 단계 1 기준선 구현

- 완료: 원본 `지상국.pdf`를 저장소에 보존하고 N2 표의 21개 데이터 화살표 및 승인된 21개 전송 경로를 `interfaces/n2_chart.json`으로 기록. 원시데이터의 DGS-Archive 시작 SFTP 회수(F19)와 TLE의 FDS 수신을 사용자와 확정
- 완료: 미확정 TC·TM 운용 값은 넣지 않고 버전형 빈 DB·확장 가능한 외피 스키마를 작성. 매니페스트 스키마, 엄격한 C 파서·경로/유형/해시 검증, JSON 로그, 원자적 파일 공개·검증, SHA-256 구현. cJSON v1.7.19 출처·라이선스·해시 고정
- 시험: Windows MSVC Release 경고 0, CTest 1/1 통과; MSVC ASan 경고 0, CTest 1/1 통과; Python 비밀정보·인터페이스 시험 10/10 통과. Linux GNU/Clang·UBSan 및 libFuzzer 실행은 이 환경에서 미수행
- 변경 파일: `interfaces/`, `libgs/`, `third_party/`, `tests/`, `CMakeLists.txt`, `README.md`, 기준 문서·결정 기록
- 결정: 원시데이터 회수 #2, TLE 수신처 #3, TC·TM 유보 #4, JSON·해시 구현 #5
- 문제: 실제 TC·TM ICD 부재로 운용 DB와 항목 스키마는 미확정. SFTP 송수신·inbox 감시·격리·삭제는 서비스 구현 단계에 남음
- 다음: Linux ASan+UBSan 필수 시험 완료 후 단계 2 배포 기반과 `check_flows` 구현

### 2026-10-21 — Linux 검증 경로 추가

- 완료: 사용자 승인으로 GitHub Actions Ubuntu 워크플로를 추가하고 GCC Release, Clang ASan+UBSan, clang-tidy, Python 검사를 등록함. POSIX `lstat` 선언에 필요한 기능 매크로를 명시함
- 시험: Windows MSVC Release·ASan CTest 각 1/1 통과; GitHub Actions 첫 실행 결과 대기
- 변경 파일: `.github/workflows/ci.yml`, `libgs/src/exchange.c`, `docs/DECISIONS.md`, `README.md`, `docs/PROGRESS.md`
- 결정: Linux CI 사용 → DECISIONS.md #6
- 문제: Linux CI 첫 실행 미확인
- 다음: CI 결과를 확인하고 오류를 수정한 뒤 단계 1 완료 판정

### 2026-10-21 — Linux CI 첫 검증

- 완료: 첫 CI 실행에서 GCC Release와 Clang ASan+UBSan 시험 통과. 정적 분석의 `memcpy` 경고 2건을 경계가 드러나는 복사로 수정한 뒤 두 번째 CI 실행에서 전체 성공
- 시험: GitHub Actions `Ground station CI` 실행 #2 성공 (`70ee53e`): GCC Release CTest, Clang ASan+UBSan CTest, clang-tidy, Python·비밀정보 검사 통과
- 변경 파일: `libgs/src/manifest.c`, `.github/workflows/ci.yml`
- 결정: CI가 보고한 checkout v4의 Node.js 20 지원 종료 안내를 없애기 위해 공식 v6.1.0(Node.js 24) 커밋으로 고정 → DECISIONS.md #6 갱신
- 문제: checkout 액션 업데이트 후 재실행 결과 대기
- 다음: 새 액션 버전의 CI를 확인하고 단계 1 완료 상태를 문서에 반영

### 2026-10-21 — 단계 1 완료

- 완료: GitHub Actions 실행 #3(`2110180`)에서 GCC Release, Clang ASan+UBSan, clang-tidy, Python·비밀정보 검사 전체 성공. Node.js 지원 종료 알림도 제거함
- 시험: Windows MSVC Release·ASan CTest 각 1/1, Python 10/10; [Linux CI 실행 #3](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39551198200) 성공
- 변경 파일: `README.md`, `docs/PROGRESS.md`
- 결정: 미확정 TC·TM 운용 값은 사용자 지시에 따라 미구현으로 유지 → DECISIONS.md #4
- 문제: 외부 ICD, HMAC 사양, DGS-GS 임시 원시데이터 제한값 미확정. 이는 후속 서비스 단계에서 확정할 사항
- 다음: 사용자 요청 시 단계 2 배포 기반 구현
