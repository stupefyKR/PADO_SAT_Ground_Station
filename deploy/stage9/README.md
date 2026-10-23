# 단계 9 웹 개발 기준선

현재는 **비운용 읽기 전용 수직 경로**다. `portal`은 Argon2id 계정·TOTP·세션·
CSRF·계정 잠금·로그인 속도 제한을 제공하고 정적 ROS/MPS 화면을 제공한다.
`ros-api`와 `mps-api`는 별도 프로세스/컨테이너에서 mTLS 클라이언트 인증서를
요구한다. 포털이 로그인한 사용자와 역할을 내부 헤더로 전달하며, 백엔드
인증서의 SAN도 검증한다. 클라이언트가 보낸 동일한 헤더는 전달하지 않는다.

ROS는 `SIM_ONLY`와 TC 송출 차단 상태, MPS는 현재 비운용 명령 DB의 버전과 빈
명령 목록, 미정 FDS 패스 목록을 표시한다. **TC 송출, 승인, 업링크 반입, 운용
시퀀스 출력, TM 파라미터 조회 API는 존재하지 않는다.** 화면에 표시된 미래
기능은 버튼이나 입력 경로로 활성화되지 않는다. 현 API는 C 코어의 실행
서비스에 아직 연결되지 않았으며 이 기준선을 단계 9 전체 완료로 판정하지
않는다.

## 단일 VM의 개발용 컨테이너 구성

`compose.dev.yaml`은 포털·ROS API·MPS API를 분리한다. ROS와 MPS는 각자
포털과의 내부 브리지만 사용하고 호스트 포트를 공개하지 않는다. 포털만 VM
루프백 `127.0.0.1:8443`에 게시한다. 관제 PC 공개는 모든 앱 개발 뒤 서버
배포 수락에서 UFW·Docker 포트 정책과 별도 출발지 거부를 확인한 후 적용한다.
이 파일은 운영용 Compose가 아니다.

인증서와 계정 DB는 저장소에 넣지 않는다. 개발용 인증서 디렉터리는
`PADO_STAGE9_CERT_DIR`에 **절대 경로**로 지정한다. 필요한 파일은 `ca.crt`,
`portal.crt`, `portal.key`, `ros-api.crt`, `ros-api.key`, `mps-api.crt`,
`mps-api.key`다. CA는 이 디렉터리에 개인키를 두지 않는다. 개발 인증서에는
포털 서버용 `localhost`, ROS 서버용 `ros-api`, MPS 서버용 `mps-api` SAN이
필요하다. 포털 인증서는 내부 클라이언트 용도도 포함하고 주체 CN은
`pado-portal`이어야 한다. 개인키는 컨테이너 UID 10001만 읽을 수 있게 한다.
운영 PKI·인증서 갱신은 최종 배포 시 확정한다.

```sh
export PADO_STAGE9_CERT_DIR=/absolute/path/to/development-certs
docker compose -f deploy/stage9/compose.dev.yaml config
docker compose -f deploy/stage9/compose.dev.yaml build
docker compose -f deploy/stage9/compose.dev.yaml run --rm --no-deps portal \
  python3 -m services.webapp.main add-user --db /state/accounts.db \
  --username planner01 --role planner
docker compose -f deploy/stage9/compose.dev.yaml up -d
```

계정 생성 명령은 비밀번호를 화면에 표시하지 않는 입력으로 받는다. `lead`
역할은 TOTP가 필수이며 등록 비밀값을 한 번 출력하므로 운영 기록에 별도로
보관한다. 하드코딩된 초기 계정은 없다. 테스트 후 브라우저에서
`https://localhost:8443/login`에 접속한다. 포털은 등록된 개발 CA를
신뢰하는 브라우저에서만 사용한다. 인증서 오류를 우회해 사용하지 않는다.

## 검증과 남은 연결

```sh
python3 -m unittest tests.test_stage9_auth tests.test_stage9_web -v
docker compose -f deploy/stage9/compose.dev.yaml ps
```

Linux 시험은 Argon2id 실제 검증, TOTP 재사용 거부, 세션 만료·잠금,
로그인·로그아웃·CSRF, 포털→ROS/MPS mTLS와 백엔드 직접 접속 거부를 확인한다.
로컬 Windows에는 libsodium/openssl 명령이 없으면 해당 환경 의존 시험이
건너뛰어지며 Ubuntu CI에서 실행한다.
[Ubuntu CI #44](https://github.com/stupefyKR/PADO_SAT_Ground_Station/actions/runs/39899833305)에서
이 시험과 Compose 구성·이미지 빌드가 통과했다.

단계 9 전체 완료에는 C 코어의 실제 서비스·영속 데이터와 웹 API의 연결,
확정된 TC/TM·FDS·Mission Request ICD에 따른 화면 기능, 재인증을 실제 승인
함수에 결합한 증적, 관제 PC→포털의 현장 접근 및 ROS/MPS 직접 접근 거부
시험이 남아 있다. 이 항목이 준비되기 전까지는 현재 읽기 전용 화면을
운용 관제 화면으로 취급하지 않는다.
