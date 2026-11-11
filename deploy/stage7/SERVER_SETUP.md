# 단일 Linux VM 서버 설정 매뉴얼 (단계 7)

이 문서는 **Linux VM 한 대에서 서브시스템별 Docker 컨테이너를 운용할 때**의 설치·검증 절차다. 현재 저장소에는 N2 연결 시험용 컨테이너만 있고 운용 서비스 이미지와 Compose 파일은 없다. 따라서 아래의 `run_stage7_topology.py`는 배포 검증용이며, 그것을 실행했다고 지상국 앱이 기동된 것은 아니다. ROS/MPS 웹 UI와 관제 PC용 서비스 공개는 단계 9에서 다룬다.

## 1. 현장 정보 기록과 변경 전 준비

작업표를 서버 밖의 운영 기록에 채운다. 주소·경로를 이 저장소의 공통 문서에 실제 값으로 커밋하지 않는다.

| 항목 | 개발 VM에서 확인한 값 | 실제 서버에서 기록할 값 |
|---|---|---|
| Linux 배포판·버전 | Ubuntu 26.04 | 배포판, 버전, 커널 |
| 가상화·VM 주소 | VMware, `192.168.45.128` | 하이퍼바이저, VM 주소, 네트워크 모드 |
| SSH 허용 출발지 | `192.168.45.1/32` | 관리 PC의 고정 IPv4 또는 승인된 관리망 |
| Docker·Compose | Docker 29.6.1 확인 | 설치 출처, 버전, 업데이트 창 |
| 시간 동기 | `timedatectl` 동기 확인 | NTP 원천, 동기 상태 |
| 앱 데이터·백업 | 미정 | 영속 저장 경로, 용량, 소유자, 백업 대상 |
| 로그 | probe의 `journald` 확인 | 최대 사용량, 보존기간, 수집·백업 정책 |
| 외부 연동 | N01·N02·N04는 TBD(ICD) | 승인된 ICD와 상대 주소·인증 방식 |

VM 콘솔 접근과 복구 절차를 먼저 확보한다. 방화벽을 변경하기 전 다음 값을 기록하고, 관리 PC의 **서버에서 보이는 출발지 주소**를 확인한다. NAT 환경에서는 PC의 로컬 주소와 다를 수 있다.

```sh
cat /etc/os-release
uname -r
hostname -I
ip -br addr
ss -tn state established '( sport = :22 )'
docker version
docker compose version
timedatectl status
sudo ufw status verbose
```

Git 작업은 별도 운영 계정으로 수행하고 배포 커밋 SHA를 기록한다. 이미 Docker를 설치한 서버라면 재설치하지 않는다. 새 Ubuntu 서버의 설치 절차와 지원 배포판은 [Docker 공식 설치 문서](https://docs.docker.com/engine/install/ubuntu/)를 확인하고, Compose 플러그인이 없을 때는 [공식 Compose 설치 문서](https://docs.docker.com/compose/install/linux/)를 따른다. 설치 후 `docker info`와 `docker compose version`이 성공해야 한다. 단계 7·9의 내부 브리지 격리와 DNS 보호를 위해 Docker Engine 안정판 28.0.0 이상을 요구한다. Docker 소켓·`docker` 그룹 접근은 호스트 관리자 수준으로 제한하고 Docker 원격 API의 TCP 노출은 만들지 않는다.

## 2. 호스트 UFW와 SSH

**VM 콘솔에서** 아래 순서로 실행한다. `ADMIN_IP`에는 위에서 확인한 실제 관리 PC IPv4를 넣는다. 현재 개발 VM의 `192.168.45.1`을 운영 서버에 그대로 복사하지 않는다. 기존 UFW 규칙은 `sudo ufw status numbered`로 검토한 뒤 불필요한 전체 허용 규칙만 개별 삭제한다. 무작정 `ufw reset`을 실행하지 않는다.

```sh
ADMIN_IP='관리_PC의_실제_IPv4'
sudo ufw allow from "$ADMIN_IP" to any port 22 proto tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw default deny routed
sudo ufw logging on
sudo ufw --force enable
sudo ufw status verbose
```

기대 상태는 `active`, 입력·라우팅 기본 `deny`, 로그 `on`, 그리고 22/tcp에 대한 **해당 관리 출발지 한 개의 허용 규칙**이다. 관리 PC에서 새 SSH 세션을 열어 접속을 확인한 뒤 기존 세션을 닫는다. 잘못 차단했다면 VM 콘솔에서 관리 주소를 바로잡는다. 다른 출발지에서도 22/tcp 연결이 거부되는지 별도 네트워크에서 확인한다.

Docker가 공개한 컨테이너 포트는 UFW 입력 규칙을 우회할 수 있다. 그러므로 단계 7에는 `ports:` 또는 `-p`를 쓰지 않고, 호스트 UFW만 믿고 서비스를 노출하지 않는다. 이후 포털 공개를 설계할 때 Docker 방화벽 정책과 외부 호스트의 허용·거부 시험을 함께 추가한다. Docker의 `iptables`/`ip6tables` 관리를 끄지 않는다. 근거: [Docker 방화벽 문서](https://docs.docker.com/engine/network/packet-filtering-firewalls/), [Ubuntu UFW 문서](https://ubuntu.com/server/docs/firewalls/).

## 3. N2 연결과 격리 시험

비공개 저장소를 서버에 배포한 후 **저장소 루트**에서 실행한다. 운영용 개인키·비밀번호·인증서·실제 환경값은 Git에 넣지 않는다. 기준 문서와 생성 파일의 일치를 먼저 확인한다.
새 셸을 열었다면 `ADMIN_IP`를 실제 관리 PC 주소로 다시 설정한다.

```sh
ADMIN_IP='관리_PC의_실제_IPv4'
git rev-parse HEAD
python3 tools/check_flows.py check
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tests/container/run_stage7_topology.py --build
python3 tests/container/run_stage7_tm.py
sudo python3 tools/stage7_host_audit.py --admin-ip "$ADMIN_IP"
```

`run_stage7_topology.py`는 고유한 이름의 임시 브리지·컨테이너를 만들고 종료 시 제거한다. 성공 기준은 연결된 쌍 24개 방향의 도달, 연결되지 않은 쌍 18개 방향의 거부, 공개 포트 0개, 7개 probe 로그다. `flow-plan.json`은 승인된 N2의 연결 시작 방향 18건과 미정 3건을 반영한다. `compose.probe.yaml`은 실행 예시일 뿐 운용 배포 파일이 아니다. Docker 브리지는 같은 쌍의 **역방향 신규 연결**도 허용하므로, 운용 서비스는 허용된 상대 주소·인증서·계정·역할과 요청 방향을 별도로 검증해야 한다.

`run_stage7_tm.py`는 시험 이미지를 이용해 DGS-GS·ROS 시뮬레이터 바이너리를 각 컨테이너에서 실행한다. 정상 시험 프레임 두 건 기록, 오류 다섯 건 격리, 승인되지 않은 상대 IP 거부를 확인한다. 실 운영 TM 프레임이나 지상국 장비와는 연결하지 않는다.

`stage7_host_audit.py`는 읽기 전용이다. UFW 상태 조회에는 비대화식 `sudo` 권한이 필요하므로 `sudo python3 ...`로 실행한다. JSON의 `errors`가 비어 있고 `docker_inventory_complete=true`, `published_ports=[]`, UFW·NTP 항목이 통과해야 종료 코드 0이 나온다. 이 도구가 Docker 조회에 실패하면 포트가 0개인 것으로 판정하지 않는다. 실제 방화벽의 외부 차단과 서비스 수준 연결 방향까지 대신 검증하지는 않는다.

## 4. 로그·시간·데이터

서비스 컨테이너를 추가할 때는 컨테이너별로 `logging: {driver: journald}`를 지정하고, 애플리케이션 로그에서 비밀값과 원시 TC/TM 본문을 제외한다. 현 개발 VM의 Docker 기본 로그 드라이버는 `json-file`이므로 명시적 설정이 필요하다. 로그 저장 용량과 보존기간은 디스크 크기 및 운영 정책을 확인한 뒤 정한다. 아래는 **용량 1 GiB·최대 14일의 예시**이며 현장 값이 확정되기 전 그대로 적용하지 않는다.

```ini
# /etc/systemd/journald.conf.d/pado.conf 예시
[Journal]
Storage=persistent
SystemMaxUse=1G
MaxRetentionSec=14day
```

적용 후 `sudo systemctl restart systemd-journald`, `sudo journalctl --flush`, `journalctl --disk-usage`, `journalctl --list-boots`를 확인한다. 재부팅 후 `journalctl -b -1`로 이전 부팅 로그가 남는지 검증한다. `SystemMaxUse`와 `MaxRetentionSec`는 보존 정책의 상한 설정이므로 별도 백업을 대신하지 않는다. 근거: [systemd journald 설정](https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html).

시간은 `timedatectl show -p NTPSynchronized --value`가 `yes`인지 확인한다. 실제 NTP 원천은 현장 운영 정책에 맞춰 설정한다. 로그 타임스탬프와 승인·재전송 검사에 시간이 사용되므로 동기 실패 상태에서는 서비스 검증을 통과 처리하지 않는다.

운용 서비스별 데이터 경로·소유 UID/GID·용량 한도·백업 주기·복구 시간을 확정한 후에만 영속 볼륨을 마운트한다. SFTP 인박스는 수신자별 권한을 분리하고 DGS-GS 원시데이터 임시 영역은 용량·보관시간·수신 확인 후 삭제 규칙을 확정해야 한다. 미정인 값을 임의의 숫자로 채우거나 임시 probe 볼륨을 운용 데이터 저장소로 재사용하지 않는다.

## 5. 운용 서비스가 준비되었을 때의 배포 순서

현 저장소에는 ROS/DGS-GS의 `--sim-only` 실행기와 여러 정적 라이브러리가 있으나, 7개 서브시스템의 운용 데몬·이미지·Compose 정의는 없다. 아래 항목이 구현되고 검증된 뒤 별도 운용 Compose로 배포한다.

1. 이미지의 정확한 태그 또는 digest, 배포 커밋 SHA, 빌드 기록을 고정한다. 컨테이너를 비 root 사용자로 실행하고 불필요한 capability를 제거한다. 읽기 전용 루트 파일시스템과 필요한 쓰기 경로만 설정한다.
2. 각 컨테이너는 승인된 연결 쌍의 내부 네트워크에만 연결한다. `network_mode: host`, `privileged: true`, Docker 소켓 마운트, 호스트 장치 접근, `ports:` 공개를 심사 없이 추가하지 않는다.
3. mTLS 인증서·SSH 호스트 키·서비스 계정과 비밀정보는 Git 밖의 제한된 파일 또는 승인된 비밀 관리 수단에서 주입한다. 실제 TC/TM·외부 ICD가 미확정인 경로는 비활성 상태로 둔다.
4. 영속 볼륨의 권한, 데이터 무결성 검사, 건강 상태 확인, 재시작 정책, 종료 시 데이터 보존을 구현한다. `docker compose config`로 최종 구성을 확인하고 `docker compose ps`, `docker inspect`, `docker logs`로 기동 결과를 확인한다.
5. N2의 허용 발신자·수신자·포트별 정상 통신과 **역방향 신규 연결 거부**, 미승인 컨테이너 거부, 공개 포트 0개를 실제 서비스로 검증한다. 외부 SDC·Space-Track·지상국은 각각 ICD와 상대 인증·주소가 확정된 후 개별 승인한다. NAS 연동은 현재 범위에서 제외한다(결정 #19).

## 6. 변경·재부팅·복구 확인

배포 전에는 Git 커밋, 이미지 digest, Compose 설정, 서비스별 데이터 백업과 복구점을 기록한다. 변경 후에는 `docker compose ps`, `docker ps --format '{{.Names}} {{.Ports}}'`, `sudo ufw status verbose`, `timedatectl status`와 위의 호스트 감사를 반복한다. 재부팅 시험에서는 컨테이너의 자동 기동, 인증서·비밀정보 접근, 영속 데이터, 이전 부팅 로그, 외부에서의 SSH 거부를 다시 확인한다. [로컬 복원 시험](../../docs/RECOVERY.md)을 참고하되, 실제 백업 매체에서 격리된 위치로 복원하고 해시·애플리케이션 판독을 검사하는 현장 시험은 최종 배포 수락에서 수행한다.

배포 실패 시 새 서비스 컨테이너를 중지하고 직전의 고정 이미지·Compose 구성으로 되돌린다. 데이터 스키마가 바뀌었다면 단순 이미지 롤백으로 충분하지 않으므로 해당 변경의 복원 절차를 먼저 따른다. UFW 문제는 서버 콘솔에서 바로잡는다. **방화벽을 끄거나 전체 네트워크를 허용하는 방식으로 복구하지 않는다.**

## 7. 단계 7 판정표

| 판정 항목 | 현재 상태 |
|---|---|
| 단일 VM·컨테이너 연결 쌍과 비연결 차단 | VMware VM 및 Linux CI에서 probe 시험 통과 |
| DGS-GS·ROS 시뮬레이터의 컨테이너 간 TM·상대 IP 검사 | Linux CI #38에서 통과; 실제 운용 데몬 시험은 대기 |
| UFW 입력·라우팅 차단, 관리 PC 전용 SSH | VMware VM에서 적용·관리 PC 연결 확인 |
| 다른 출발지 SSH 차단 | 별도 출발지 시험 대기 |
| 운용 서비스 7종 이미지·권한·영속 데이터 | 운용 데몬과 이미지 미구현 |
| N2 연결 시작 방향과 인증 | 실제 서비스 시험 대기 |
| 로그 보존·재부팅 후 복구 | 정책 확정·재부팅 시험 대기 |
| 외부 ICD·SDC·Space-Track | 세부 계약 확정 대기 |

단계 7의 **개발 기준선은 완료**했다. 표의 미완료 항목은 모든 앱 개발 후 실제 서버 배포 수락 단계에서 검증한다(결정 #17). 현장 서버에 이 문서를 적용할 때는 실제 설정값과 시험 결과를 운영 기록에 남긴다.
