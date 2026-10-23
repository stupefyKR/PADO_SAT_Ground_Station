# 단계 7: 단일 Linux VM의 Docker 배포 기준선

**개발 기준선 완료.** 실제 VM에 운용 서비스를 배포하고 현장 방화벽·로그·재부팅을
검증하는 절차는 모든 앱 개발 뒤 배포 수락 단계에서 수행한다(결정 #17).

2026-10-23 사용자 결정에 따라 운영 단위는 **Linux VM 한 대**이며 ROS, MPS,
FDS, DGS-Archive, DGS-GS, DGS-NET, Portal을 별도 Docker 컨테이너로 둔다.
이 폴더의 Compose 파일은 연결 경계를 시험하는 **일회용 probe**다. 실제
서브시스템 데몬, 운용 TC, 외부 지상국 연결을 배포하지 않는다.

실제 서버 준비·UFW·Docker·로그·검증·복구 절차는 [서버 설정 매뉴얼](SERVER_SETUP.md)에 정리했다.

## 생성과 검사

```sh
python3 tools/check_flows.py render
python3 tools/check_flows.py check
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tests/container/run_stage7_topology.py --build
python3 tests/container/run_stage7_tm.py
sudo python3 tools/stage7_host_audit.py --admin-ip 192.168.45.1
```

`flow-plan.json`은 N2 chart의 **연결 시작 방향**을 기록한다. 내부 컨테이너
쌍마다 별도 `internal: true` 브리지를 만들고, 공개 포트는 만들지 않는다.
Docker 시험은 서로 연결된 쌍의 도달성과 연결되지 않은 쌍의 거부, journald
로그 수신을 확인한다. 브리지는 같은 쌍의 **역방향 신규 연결을 막지 못하므로**
실제 서비스는 정해진 인터페이스에만 바인딩하고 상대 신원·역할을 검증해야 한다.
호스트·privileged 컨테이너·Docker 관리자에 대한 격리도 제공하지 않는다.
`run_stage7_tm.py`는 두 컨테이너에서 현재의 `--sim-only` DGS-GS·ROS
프로세스를 실행해 mTLS TM 전달과 비허용 상대 IP 거부를 확인한다. 이것 역시
일회용 시험 이미지이며 운용 서비스 배포가 아니다.

## 호스트 경계

현재 웹 포털은 9단계 대상이다. 7단계에는 **Docker 공개 포트를 0개**로
유지하고 VM SSH만 관리 PC에서 허용한다. Docker의 공개 포트는 Linux UFW
입력 규칙을 우회할 수 있으므로, 이후 공개가 필요하면 호스트 UFW와 Docker
`DOCKER-USER` 정책, 실제 외부 호스트 거부 시험을 함께 검토해야 한다.
Docker의 `iptables`/`ip6tables` 관리를 끄지 않는다.

호스트 UFW 적용은 콘솔에서 관리자 권한으로 수행한다. SSH의 실제 접속 원본
주소를 먼저 확인하고 해당 주소만 22/tcp 허용한 뒤 기본 입력 차단을 켠다.
현재 VMware VM에서는 `192.168.45.1 → 22/tcp`만 허용했고, 접속 중인 관리
PC에서 22/tcp 연결 및 실제 임시 2900/tcp 리스너 차단을 확인했다.
`tools/stage7_host_audit.py`는 변경 없이 UFW 기본 정책, Docker 공개 포트,
NTP 동기화와 Docker 로그 드라이버를 점검한다. 컨테이너별 journald 로그
드라이버는 별도로 설정되므로 Docker의 전역 기본값이 `json-file`이어도
probe 로그 수신 시험은 가능하다.

## 최종 배포 수락에 남은 항목

- **다른 출발지**에서 SSH 거부를 확인하고 운영 관리 PC 주소가 정해지면
  현 VMware 호스트 전용 주소를 교체한다.
- 운용 서비스별 이미지, 최소 권한, 영속 볼륨, 비밀정보 주입, 시작/재시작 및
  청결한 종료를 구성한다. 현재 ROS/DGS-GS는 `--sim-only` 실행 경로이고
  다른 서브시스템도 운용 데몬으로 연결되지 않았다.
- N2 연결 시작 방향, SFTP 계정·인박스 권한, mTLS 상대 신원, DGS-GS의
  운용 VM 신규 연결 거부를 실제 서비스로 검증한다.
- journald 보존 한도와 재부팅 후 로그 보존, 백업·복구를 검증한다.
- 외부 ICD와 SDC, Space-Track 경로는 확정 후 별도 활성화한다.
- NAS 연동은 현재 개발·배포 수락 범위에서 제외한다([결정 #19](../../docs/DECISIONS.md)).

Docker 근거: [방화벽과 UFW](https://docs.docker.com/engine/network/packet-filtering-firewalls/),
[브리지 격리](https://docs.docker.com/engine/network/drivers/bridge/),
[포트 공개](https://docs.docker.com/engine/network/port-publishing/).
