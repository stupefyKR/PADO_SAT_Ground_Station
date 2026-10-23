# PADO-SAT 지상국 개발 방향 (구현 아키텍처)

> **2026-10-23 배포 구조 변경:** 사용자 결정에 따라 실제 운영은 Linux VM
> **한 대**에 서브시스템별 Docker 컨테이너를 올린다. 이 문서의 다중 VM·VLAN·
> VM별 nftables·전용 vm-mgmt 내용은 이전 설계안이며 현재 배포 지시로
> 사용하지 않는다. 확정된 N2 데이터·연결 방향, 애플리케이션 보안 계약과
> ROS/MPS 웹 UI를 마지막에 구현하는 순서는 유지한다. 현재 단계 7의
> 배포·검증 기준은 [단일 VM 계획](../deploy/stage7/README.md)을 따른다.

> 온프레미스 VM 기반 지상국 구현 구조 — 웹 운용 화면, 서브시스템 VM·컨테이너 구성, 방화벽·Docker 보안 설정

| 항목 | 내용 |
|---|---|
| 작성일 | 2026. 10. 21. |
| 작성자 | 김경태 (경희대학교 PWNLAB) |
| 기준 문서 | ① 초소형(큐브) 위성 경연대회 2차 제안서 — 지상국 시스템 (PADO-SAT) ② PADO-SAT 지상국 보안 설계 개발 방향 계획서 (이하 「보안 계획서」) |
| 문서 성격 | 보안 계획서의 설계 개념을 실제 장비·VM·코드 구조로 옮기는 구현 방향서 |

---

## 1. 개요

### 1.1 구축 방식: 온프레미스

본 지상국은 **온프레미스(On-premise)** 방식으로 구축한다. 모든 서버·저장장치·운용 단말을 경희대 MOC 공간에 직접 설치하고 운영하며, 클라우드 서비스는 사용하지 않는다.

외부와의 연결은 다음 두 가지뿐이다. 둘 다 **우리 장비가 외부 서비스와 통신하는 것**이지, 우리 시스템의 일부를 외부에 두는 것이 아니다.

| 외부 연결 | 방식 | 담당 VM |
|---|---|---|
| 항우연·연세대 안테나 지상국 | IPsec 사이트 간 VPN | vm-dgs-gs |
| Space-Track (TLE) | 인터넷 HTTPS 아웃바운드 | vm-dgs-net |

온프레미스이므로 **물리 보안, 전원, 백업, 패치, 장비 장애 대응도 모두 우리 책임**이다. 이 부분은 8장(로그·백업)과 10장(운영 관리)에서 다룬다.

### 1.2 구현 원칙

보안 계획서의 설계 원칙을 구현 수준으로 옮기면 다음과 같다.

| 보안 계획서 원칙 | 구현 원칙 |
|---|---|
| 단일 접점 | 외부 회선은 DMZ의 두 VM(vm-dgs-gs, vm-dgs-net)에만 연결한다. 운용 VM은 외부 경로가 없다. |
| 명령 경로 우선 | TC는 ros-core → ros-signer → tc-gate 순서로만 흐르며, 각 단계는 서로 다른 프로세스·컨테이너·VM에 둔다. |
| 단순성 | 서비스 간 통신은 "실시간 스트림 1종 + 파일 교환 1종"만 쓴다. 모든 서비스는 같은 골격 코드를 쓴다. |
| 계층 방어 | 방화벽 장비 → 하이퍼바이저 → VM nftables → 컨테이너 설정 → 애플리케이션 검증의 5계층으로 통제한다. |
| 보안 경계 | **VM이 보안 경계**이고, 컨테이너는 배포 단위이자 보조 격리 수단이다. 컨테이너를 보안 경계로 간주하지 않는다. |
| 기록과 복구 | 모든 서비스가 같은 형식의 로그를 남기고, 모든 데이터는 3-2-1 원칙으로 백업한다. |

---

## 2. 물리 구성

### 2.1 장비 구성

| 장비 | 수량 | 역할 | 비고 |
|---|---|---|---|
| 관제용 PC | 2 | 웹 브라우저로 관제 화면·스케줄링 화면 접속 | 2인 승인(작성자 ≠ 승인자) 운용에 활용 |
| 서버 (고성능 PC) | 1 | 하이퍼바이저, 모든 VM 실행 | |
| NAS | 1 | DGS 아카이브 저장소, 백업 대상 | **운용 DB는 두지 않음** (2.3절) |

### 2.2 추가 필요 장비 (권장)

| 장비 | 필요 이유 | 대안 |
|---|---|---|
| **방화벽 장비** | VPN 종단, 인터넷·VPN·내부 VLAN 간 라우팅과 필터링. 외부 회선이 서버에 직접 닿지 않게 한다. | 하이퍼바이저 위 방화벽 VM (6.5절의 주의 사항 적용) |
| **VLAN 지원 스위치** | 운용·서비스·DMZ·저장·관리망 분리 | 없으면 구역 분리가 불가능하므로 사실상 필수 |
| UPS | 정전 시 패스 중 TC 송출 중단·DB 손상 방지 | |
| 오프라인 백업 디스크 2개 | 3-2-1 백업의 오프라인 사본 (교대 보관) | |
| 관리자 단말 | VM·하이퍼바이저 관리 전용 (관제 PC와 분리) | 서버 로컬 콘솔 |

### 2.3 DB를 NAS에 두지 않는 이유

- NAS나 저장망에 문제가 생기면 **패스 도중 ROS가 멈춘다.**
- PostgreSQL 데이터를 NFS 같은 네트워크 파일시스템 위에서 운영하면 잠금·쓰기 보장 문제로 데이터가 손상될 위험이 있다.
- 제안서의 "각 서브시스템은 운용 데이터를 자체 저장한다"는 원칙과도 맞지 않는다.

따라서 **운용 DB는 각 VM의 로컬 디스크**에 두고, NAS는 **아카이브 저장소(장기 보관)와 백업 사본**을 맡는다.

---

## 3. 네트워크 구성

### 3.1 VLAN 구성

보안 계획서의 구역(Z0~Z4)을 VLAN으로 구현한다. **VLAN 사이의 통신은 모두 방화벽 장비를 거쳐 라우팅**되도록 하여, 구역 간 정책을 한 곳에서 통제한다.

| VLAN | 이름 | 보안 계획서 구역 | 대역 (예시) | 구성원 |
|---|---|---|---|---|
| 10 | OPS | Z2 (운용 단말) | 10.10.10.0/24 | 관제 PC-1(.11), 관제 PC-2(.12) |
| 20 | SVC | Z2 (운용 서비스) | 10.10.20.0/24 | portal(.5), ros(.10), mps(.11), fds(.12), dgs-archive(.13) |
| 30 | DMZ-GS | Z1 | 10.10.30.0/24 | dgs-gs(.10) |
| 31 | DMZ-NET | Z1 | 10.10.31.0/24 | dgs-net(.10) |
| 40 | STOR | Z4 | 10.10.40.0/24 | NAS(.5), dgs-archive(.13), mgmt(.20) |
| 50 | MGMT | Z4 | 10.10.50.0/24 | 하이퍼바이저(.2), mgmt(.20), 관리자 단말(.100) |
| 60 | SDC | Z3 | 10.10.60.0/24 또는 학내망 | 탑재체팀 장비 |
| - | WAN | Z0 | - | 인터넷, 항우연·연세대 VPN |

※ 주소는 예시이며 구축 시 학교 정보처와 협의하여 확정한다.

### 3.2 구성도

```
                 인터넷 / 항우연·연세대 (IPsec VPN)
                               │
                     ┌─────────┴─────────┐
                     │    방화벽 장비      │  VPN 종단, VLAN 간 라우팅·필터링, NTP 상위 동기
                     └─┬───┬───┬───┬───┬─┘
          VLAN 10 OPS  │   │   │   │   │  VLAN 60 SDC
     [관제PC-1][관제PC-2]   │   │   │   └──[탑재체팀 장비]
                           │   │   │
            VLAN 20 SVC ───┘   │   └─── VLAN 30 DMZ-GS / 31 DMZ-NET
                               │
                     VLAN 50 MGMT (관리자 단말만)

 ┌──────────────────────────── 서버 (하이퍼바이저) ─────────────────────────────┐
 │  [VLAN 20] vm-portal  vm-ros  vm-mps  vm-fds  vm-dgs-archive                │
 │  [VLAN 30] vm-dgs-gs          [VLAN 31] vm-dgs-net                          │
 │  [VLAN 40/50] vm-mgmt (로그 수집, 레지스트리, 업데이트 저장소, NTP)            │
 │  하이퍼바이저 관리 인터페이스: VLAN 50에만 존재                                │
 └─────────────────────────────────────────────────────────────────────────────┘
                     [NAS] VLAN 40 (아카이브 저장소, 백업)
```

### 3.3 연결 시작 방향 원칙

**신뢰도가 낮은 구역에서 높은 구역으로 연결을 시작할 수 없게 한다.** 데이터가 안쪽으로 들어와야 하는 경우에도 안쪽에서 연결을 열어 가져온다.

| 데이터 방향 | 연결을 시작하는 쪽 | 예 |
|---|---|---|
| DMZ → 운용 (TM 수신) | 운용 (ros-core가 dgs-gs에 접속) | ros-core → dgs-gs:7001 |
| DMZ → 운용 (TLE 수신) | 운용 (fds가 dgs-net에서 가져옴) | fds → dgs-net:2222 (SFTP 다운로드) |
| 운용 → DMZ (TC 송출) | 운용 | 위와 같은 연결 사용 |
| SDC → 운용 (Mission Request) | SDC → dgs-archive 교환 영역 | 운용 VM 중 SDC가 닿는 곳은 dgs-archive뿐 |

이 원칙에 따라 **DMZ VM의 nftables에는 VLAN 20으로 나가는 새 연결 규칙이 하나도 없다.** DMZ VM이 탈취되어도 운용 VM으로 먼저 연결을 열 수 없다.

---

## 4. VM 구성

### 4.1 VM 목록

| VM | VLAN | 컨테이너 | 로컬 DB | vCPU / RAM (예시) |
|---|---|---|---|---|
| vm-portal | 20 | nginx, portal-auth, portal-db | 계정·세션 | 2 / 2 GB |
| vm-ros | 20 | ros-core, ros-api, ros-signer, ros-db | TM 이력, TC·승인 기록 | 4 / 8 GB |
| vm-mps | 20 | mps-core, mps-api, mps-db | 계획, 시퀀스, 임무 요청, 반입 승인 | 2 / 4 GB |
| vm-fds | 20 | fds-core | 궤도 산출물 (파일) | 4 / 8 GB |
| vm-dgs-archive | 20, 40 | archive, catalog, integrity, sftp-exchange, archive-db | 카탈로그 | 2 / 4 GB |
| vm-dgs-gs | 30 | kari-adapter, tm-validator, tc-gate, sftp-in | **없음 (무저장)** | 2 / 2 GB |
| vm-dgs-net | 31 | spacetrack-adapter, sv-receiver, sftp-out | **없음 (무저장)** | 1 / 1 GB |
| vm-mgmt | 40, 50 | log-collector, registry, update-mirror, ntp | 로그 | 2 / 4 GB |

※ vm-dgs-archive와 vm-mgmt만 두 VLAN에 걸친다. 두 VM 모두 IP 포워딩을 끄고(`net.ipv4.ip_forward=0`), 구역 간 중계 역할을 하지 않는다.

### 4.2 VM별 역할

**vm-portal** — 관제 PC가 접속하는 유일한 진입점. TLS 종단, 정적 웹 파일 제공, 로그인·세션·재인증, 인증된 요청만 ros-api·mps-api로 전달.

**vm-ros** — TM 수신·파싱·Limit 감시, TC 검증·승인 상태 관리·시퀀스 실행, HMAC 서명.

```
 portal ──HTTPS(mTLS)──► ros-api ──unix socket──► ros-core ──unix socket──► ros-signer
                         (웹 API,                  (TM 처리, TC 검증·         (HMAC 키 보유,
                          WebSocket TM)             시퀀스 실행)                네트워크 없음)
                                                        │
                                                        └──TLS──► vm-dgs-gs:7001
```

- ros-api와 ros-core를 나누는 이유: 웹 계층에 문제가 생겨도 패스 중 TM 수신과 TC 실행은 계속된다.
- ros-signer를 나누는 이유: HMAC 키를 공격 표면이 넓은 부분(TM 파싱, 웹)과 분리한다.

**vm-mps** — 명령 목록, 패스 목록, 시퀀스 편집, 충돌 분석, 승인, TC 시퀀스 파일 출력. 탑재체 Uplink 파일 반입 승인도 이 화면에서 처리한다.

**vm-fds** — 궤도 산출. 웹 화면 없음. Orekit(Java) 사용으로 **C 예외 서비스**다(11.3절).

**vm-dgs-archive** — 이관 파일 보관(NAS), 카탈로그, 무결성 검사, SDC 교환 영역(SFTP).

**vm-dgs-gs** — 항우연·연세대 프레임 송수신, TM 입력 검증, TC 게이트. **인터넷 경로 없음.**

**vm-dgs-net** — Space-Track TLE 조회, 발사체 State Vector 수신. **TC 경로와 무관.**

**vm-mgmt** — 로그 수집, 내부 컨테이너 레지스트리, OS 업데이트 저장소, 내부 NTP.

---

## 5. 컨테이너(Docker) 구성과 보안 설정

### 5.1 Docker를 쓰는 이유와 위치

| 역할 | 내용 |
|---|---|
| 배포 일관성 | 개발·시험·운용 환경이 같은 이미지를 쓴다. |
| 롤백 | 문제가 생기면 이전 이미지 다이제스트로 즉시 되돌린다. |
| 불변 배포 | 운용 중 컨테이너 안 파일을 고치지 않고, 이미지를 새로 만들어 교체한다. |
| 언어 혼용 | FDS(Java)와 나머지(C)를 같은 방식으로 배포한다. |
| 보조 격리 | 프로세스별 권한·파일시스템·능력(capability) 제한 |

**Docker는 VM 안에서만 쓰고, 보안 경계는 VM이다.** 컨테이너는 호스트 커널을 공유하므로 컨테이너 탈출 취약점에 대비해 "한 VM = 한 서브시스템" 원칙을 유지한다.

### 5.2 네트워크 방식: host 네트워크 + nftables 단일 정책

Docker의 기본 bridge 네트워크는 포트를 공개할 때 **iptables 규칙을 직접 추가하여 호스트 방화벽 규칙을 우회**한다. 이는 흔한 설정 실수의 원인이다. 본 지상국은 VM마다 한 서브시스템만 돌리므로 다음 방식으로 단순화한다.

| 컨테이너 유형 | 네트워크 모드 | 이유 |
|---|---|---|
| 네트워크가 필요한 서비스 | `network_mode: host` | VM의 nftables가 유일한 네트워크 정책이 된다. 규칙이 한 곳에만 있다. |
| ros-signer | `network_mode: none` | 네트워크 자체가 없음. unix socket으로만 통신 |
| DB | `network_mode: host`, **unix socket 또는 127.0.0.1에만 바인드** | 외부에서 DB 포트가 보이지 않음 |

각 서비스는 **특정 인터페이스 IP에만 바인드**한다(예: ros-api는 `10.10.20.10:8443`에만). `0.0.0.0` 바인드는 금지한다.

Docker 데몬은 bridge를 쓰지 않으므로 iptables 조작을 끈다.

```json
// /etc/docker/daemon.json (모든 VM 공통)
{
  "iptables": false,
  "ip-forward": false,
  "bridge": "none",
  "icc": false,
  "no-new-privileges": true,
  "live-restore": true,
  "log-driver": "journald",
  "userland-proxy": false,
  "registry-mirrors": [],
  "insecure-registries": []
}
```

※ 다른 bridge 방식이 필요해지면 `DOCKER-USER` 체인에 정책을 두는 방식으로 전환하며, 그 경우에도 공개 포트는 특정 IP에만 바인드한다.

### 5.3 컨테이너 보안 설정 기준

모든 컨테이너에 다음을 기본 적용한다.

| 설정 | 값 | 효과 |
|---|---|---|
| 실행 사용자 | `user: "<uid>:<gid>"` (서비스별 고유 비루트 UID) | 컨테이너 안 root 금지 |
| 루트 파일시스템 | `read_only: true` | 실행 중 바이너리·설정 변조 방지 |
| 임시 쓰기 공간 | `tmpfs: /tmp` (크기 제한) | 쓰기는 지정된 곳만 |
| 능력 | `cap_drop: [ALL]` (필요한 것만 개별 추가) | 커널 권한 최소화 |
| 권한 상승 | `security_opt: [no-new-privileges:true]` | setuid 등 상승 차단 |
| seccomp | Docker 기본 프로필 유지 (`unconfined` 금지) | 위험 시스템 호출 차단 |
| 특권 모드 | `privileged` 금지 | |
| Docker 소켓 | **어떤 컨테이너에도 마운트 금지** | 소켓 접근 = 호스트 root |
| 자원 제한 | `mem_limit`, `pids_limit`, `cpus` | 한 서비스의 폭주가 VM 전체를 멈추지 않게 |
| 재시작 | `restart: unless-stopped` | 장애 시 자동 복구 |
| 이미지 지정 | **다이제스트(`@sha256:...`)로 고정**, `latest` 금지 | 의도하지 않은 이미지 교체 방지 |
| 상태 확인 | `healthcheck` | ros-api가 ros-core 상태를 화면에 표시 |
| 로그 | `journald` → vm-mgmt 전송 | 8장 |

※ `userns-remap`은 host 네트워크 모드와 함께 쓸 수 없으므로, 대신 서비스별 비루트 UID를 명시하는 방식으로 같은 효과를 얻는다.

### 5.4 compose 예시: vm-ros

```yaml
# deploy/vm/ros/compose.yml
x-hardening: &hardening
  read_only: true
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  restart: unless-stopped
  logging: { driver: journald }

services:
  ros-db:
    <<: *hardening
    image: registry.gs.local/pado/postgres@sha256:<digest>
    user: "1100:1100"
    network_mode: host               # 127.0.0.1에만 바인드 (설정 파일에서 지정)
    volumes:
      - /srv/ros/db:/var/lib/postgresql/data
      - /run/ros-db:/var/run/postgresql
    tmpfs: ["/tmp:size=64m"]
    mem_limit: 2g
    pids_limit: 200

  ros-core:
    <<: *hardening
    image: registry.gs.local/pado/ros-core@sha256:<digest>
    user: "1101:1101"
    network_mode: host               # dgs-gs로 나가는 연결 (nftables가 통제)
    volumes:
      - /srv/ros/exchange:/exchange   # 파일 교환 inbox/outbox
      - /run/ros-ipc:/ipc             # api·signer와의 unix socket
      - /run/ros-db:/db
      - /etc/pado/interfaces:/interfaces:ro   # 명령 DB, TM 정의
      - /etc/pado/ros/core.json:/config.json:ro
    tmpfs: ["/tmp:size=64m"]
    mem_limit: 2g
    pids_limit: 256
    depends_on: [ros-db, ros-signer]

  ros-signer:
    <<: *hardening
    image: registry.gs.local/pado/ros-signer@sha256:<digest>
    user: "1102:1102"
    network_mode: none               # 네트워크 없음
    volumes:
      - /run/ros-ipc:/ipc
      - /etc/pado/keys/hmac.key:/keys/hmac.key:ro   # 호스트에서 소유자 1102, 권한 0400
    mem_limit: 128m
    pids_limit: 32

  ros-api:
    <<: *hardening
    image: registry.gs.local/pado/ros-api@sha256:<digest>
    user: "1103:1103"
    network_mode: host               # 10.10.20.10:8443에만 바인드
    volumes:
      - /run/ros-ipc:/ipc
      - /run/ros-db:/db
      - /etc/pado/tls/ros-api:/tls:ro
      - /etc/pado/ros/api.json:/config.json:ro
    tmpfs: ["/tmp:size=32m"]
    mem_limit: 512m
    pids_limit: 128
```

- `/run/ros-ipc`의 소켓 파일 권한으로 "ros-api → ros-core", "ros-core → ros-signer" 방향만 허용한다(그룹 권한으로 제어). **ros-api는 signer 소켓에 접근할 수 없다.**
- HMAC 키 파일은 이미지·환경 변수·저장소에 절대 넣지 않는다.

### 5.5 이미지 관리

| 단계 | 내용 |
|---|---|
| 빌드 | 개발 PC 또는 빌드 전용 환경에서 다단계 빌드. 실행 이미지에는 바이너리와 필요한 라이브러리만 포함(최소 베이스 이미지) |
| 점검 | 취약점 스캔(오프라인 DB 사용 가능 도구), SBOM 생성, 결과를 이미지 버전과 함께 보관 |
| 반입 | 점검 통과 이미지만 vm-mgmt 내부 레지스트리에 등록 |
| 배포 | 각 VM은 내부 레지스트리에서만 가져옴. compose 파일에 다이제스트 기록 |
| 기록 | 어느 날 어느 다이제스트를 배포했는지 배포 로그에 남김 |

운용 VM은 인터넷에 나갈 수 없으므로 **Docker Hub에서 직접 이미지를 받는 경로는 존재하지 않는다.**

---

## 6. 방화벽 설계

### 6.1 5계층 통제

| 계층 | 위치 | 통제 내용 | 막는 것 |
|---|---|---|---|
| L1 | 방화벽 장비 | VPN 종단, VLAN 간 라우팅 필터, 인터넷 아웃바운드 제한 | 구역 간 비인가 통신, 외부 침투 |
| L2 | 하이퍼바이저 | VM NIC별 VLAN 고정, MAC·IP 스푸핑 방지, VM 단위 방화벽 | VM이 다른 VLAN·주소로 위장 |
| L3 | VM nftables | 입력·**출력 모두 기본 차단**, 흐름표 기준 허용 | 같은 VLAN 내 측면 이동, 탈취 VM의 외부 유출 |
| L4 | 컨테이너 | 비루트·읽기 전용·능력 제거, signer 네트워크 없음 | 서비스 탈취 후 VM 내 확산 |
| L5 | 애플리케이션 | tc-gate, tm-validator, 매니페스트 검증, 인증·인가 | 허용된 경로 안에서의 악성 데이터·명령 |

같은 VLAN 20 안의 VM끼리는 방화벽 장비를 거치지 않으므로, **L2·L3가 VLAN 내부 통제를 맡는다.**

### 6.2 흐름 허용표 (포트 포함)

보안 계획서 7.4절 흐름 허용표를 구현 수준으로 구체화한 것이다. 포트 번호는 제안값이며 구축 시 확정한다.

| ID | 출발 → 목적 | 포트 | 내용 | 통제 계층 |
|---|---|---|---|---|
| F01 | 관제 PC → vm-portal | 443/tcp | 웹 운용 화면 | L1, L3 |
| F02 | vm-portal → vm-ros | 8443/tcp (mTLS) | ros-api | L2, L3 |
| F03 | vm-portal → vm-mps | 8443/tcp (mTLS) | mps-api | L2, L3 |
| F04 | vm-ros → vm-dgs-gs | 7001/tcp (TLS) | TM 스트림(↓), TC 프레임(↑). ros-core가 연결 시작 | L1, L3 |
| F05 | 항우연·연세대 ↔ vm-dgs-gs | VPN 내부, ICD 확정 포트 | 프레임 스트림, 운용 파일 | L1, L3 |
| F06 | vm-fds → vm-dgs-gs | 2222/tcp (SFTP) | Antenna Pointing File, 패스 요청 업로드 | L1, L3 |
| F07 | vm-fds → vm-dgs-net | 2222/tcp (SFTP) | 검증된 TLE·State Vector 다운로드 | L1, L3 |
| F08 | vm-dgs-net → 인터넷 | 443/tcp | Space-Track 조회 | L1, L3 |
| F09 | 발사체 제공자 → vm-dgs-net | 협의 | State Vector 수신 (LEOP 한시) | L1, L3 |
| F10 | VLAN 20 내부 서브시스템 간 | 2222/tcp (SFTP) | N2 chart 내부 파일 흐름 (6.3절) | L2, L3 |
| F11 | vm-ros·mps·fds → vm-dgs-archive | 2222/tcp (SFTP) | Backup File 이관 | L2, L3 |
| F12 | SDC → vm-dgs-archive | 2222/tcp (SFTP) | 교환 영역 접근 | L1, L3 |
| F13 | vm-dgs-archive → NAS | 2049/tcp (NFSv4) | 아카이브 저장 | L1, L3 |
| F14 | 전 VM → vm-mgmt | 6514/tcp (syslog TLS) | 로그 전송 | L1, L3 |
| F15 | 전 VM → vm-mgmt | 123/udp | NTP | L1, L3 |
| F16 | 전 VM → vm-mgmt | 5000/tcp, 3142/tcp | 이미지 레지스트리, OS 업데이트 | L1, L3 |
| F17 | 전 VM → NAS | 협의 (rsync/SMB 쓰기 전용) | 백업 전송 | L1, L3 |
| F18 | 관리자 단말 → 전 VM, 하이퍼바이저 | 22/tcp, 하이퍼바이저 관리 포트 | 시스템 관리 | L1 (VLAN 50에서만) |
| F19 | vm-dgs-archive → vm-dgs-gs | 2222/tcp (SFTP, 읽기 전용) | CREST·SIREN Payload Raw Data 회수. 연결은 archive가 시작하고 데이터는 DGS-GS → archive 방향 | L1, L3 |
| F99 | 그 외 전부 | - | **차단 및 기록** | 전 계층 |

### 6.3 VLAN 20 내부 파일 흐름 (F10 상세)

N2 chart의 내부 화살표 하나 = SFTP 계정 하나 = 수신 측 inbox 디렉터리 하나로 대응시킨다. **송신 측이 수신 측에 업로드(push)** 한다.

| 송신 → 수신 | 데이터 | 수신 측 SFTP 계정·디렉터리 |
|---|---|---|
| ros → fds | GNSS File | `fds:/inbox/from_ros` |
| ros → mps | ACK, Bus SOH File | `mps:/inbox/from_ros` |
| mps → ros | Mission Schedule File, TC 시퀀스 파일 | `ros:/inbox/from_mps` |
| fds → mps | Contact Schedule, Orbital Event File | `mps:/inbox/from_fds` |
| ros → dgs-archive | S/C Attitude, Bus SOH (SDC 제공용) | `archive:/exchange/to_sdc` |
| fds → dgs-archive | Orbit Ephemeris, Pass Information (SDC 제공용) | `archive:/exchange/to_sdc` |
| dgs-archive → mps | 승인 대기 Mission Request, Uplink 파일 | `mps:/inbox/from_archive` |
| mps → ros | 반입 승인된 Uplink 파일 | `ros:/inbox/from_mps` |

- SFTP 서버는 관리용 SSH(22)와 **별도 프로세스·별도 포트(2222)**로 띄우고, 각 계정은 자기 inbox에만 chroot되며 셸 접근이 없다(`ForceCommand internal-sftp`).
- 각 계정은 **SSH 키 인증만** 허용하고, 키는 송신 서비스 컨테이너에만 마운트한다.
- 계정·디렉터리 목록은 `interfaces/n2_chart.json`에서 생성하며, 여기에 없는 흐름은 계정이 없으므로 불가능하다.
- F19는 DMZ에서 운용 VLAN으로 새 연결을 열 수 없으므로 이 절의 push 원칙에 대한 **명시적 예외**다. DGS-GS는 별도 읽기 전용 SFTP 계정으로 CREST·SIREN 원시데이터와 매니페스트를 제공하고, DGS-Archive가 접속해 회수·해시 및 유형 검증·수신 확인을 수행한다. DGS-GS는 용량과 보관시간을 제한한 임시 영역에서 확인 후 삭제한다. 제한값은 원시데이터 규모와 수신 주기 확인 후 정한다(TBD(ICD)). SDC는 계속 DGS-Archive 교환 영역만 접근한다.
- 원본 N2 표의 DGS→ROS `TLE` 표기와 같은 문서의 FDS 수신 설명이 상충한다. TLE는 DGS-NET에서 검증한 뒤 FDS로 전달하는 경로를 기준으로 삼고, ROS로 직접 전달하는 TLE 경로는 만들지 않는다(결정 기록 #3).

### 6.4 VM nftables 예시

모든 VM은 같은 골격을 쓴다. **입력과 출력 모두 기본 차단**이며, 흐름표의 해당 행만 허용한다.

#### vm-ros

```nft
#!/usr/sbin/nft -f
flush ruleset

define PORTAL  = 10.10.20.5
define MPS     = 10.10.20.11
define FDS     = 10.10.20.12
define ARCHIVE = 10.10.20.13
define DGS_GS  = 10.10.30.10
define MGMT    = 10.10.40.20
define ADMIN   = 10.10.50.100

table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif lo accept

    ip saddr $PORTAL tcp dport 8443 accept          # F02 ros-api
    ip saddr $MPS    tcp dport 2222 accept          # F10 mps → ros inbox
    ip saddr $ADMIN  tcp dport 22   accept          # F18 관리 (방화벽 장비에서도 VLAN 50만 허용)

    limit rate 10/second log prefix "nft-in-drop " level warn
  }

  chain output {
    type filter hook output priority 0; policy drop;
    ct state established,related accept
    oif lo accept

    ip daddr $DGS_GS tcp dport 7001 accept          # F04 TM/TC 스트림 (ros-core가 시작)
    ip daddr { $MPS, $FDS, $ARCHIVE } tcp dport 2222 accept   # F10, F11
    ip daddr $MGMT tcp dport { 6514, 5000, 3142 } accept      # F14, F16
    ip daddr $MGMT udp dport 123 accept                        # F15

    limit rate 10/second log prefix "nft-out-drop " level warn
  }

  chain forward {
    type filter hook forward priority 0; policy drop;
  }
}
```

#### vm-dgs-gs (DMZ)

```nft
define ROS      = 10.10.20.10
define FDS      = 10.10.20.12
define ARCHIVE  = 10.10.20.13
define KARI     = <항우연 상대 주소>
define YONSEI   = <연세대 상대 주소>
define MGMT     = 10.10.40.20
define ADMIN    = 10.10.50.100

table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif lo accept

    ip saddr $ROS tcp dport 7001 accept                       # F04
    ip saddr $FDS tcp dport 2222 accept                       # F06
    ip saddr $ARCHIVE tcp dport 2222 accept                   # F19 원시데이터 회수 연결
    ip saddr { $KARI, $YONSEI } tcp dport <ICD 포트> accept   # F05
    ip saddr $ADMIN tcp dport 22 accept                       # F18

    limit rate 10/second log prefix "nft-in-drop " level warn
  }

  chain output {
    type filter hook output priority 0; policy drop;
    ct state established,related accept
    oif lo accept

    ip daddr { $KARI, $YONSEI } tcp dport <ICD 포트> accept   # F05 (프레임·파일 송신 방향이 우리 쪽 시작일 경우)
    ip daddr $MGMT tcp dport { 6514, 5000, 3142 } accept      # F14, F16
    ip daddr $MGMT udp dport 123 accept                        # F15
    # VLAN 20(운용)으로 새 연결을 여는 규칙은 존재하지 않음

    limit rate 10/second log prefix "nft-out-drop " level warn
  }

  chain forward {
    type filter hook forward priority 0; policy drop;
  }
}
```

#### 규칙 관리

- 규칙 파일은 `deploy/vm/<vm>/nftables.conf`로 저장소에서 버전 관리한다.
- `tools/check_flows`가 `n2_chart.json`·흐름 허용표와 각 VM 규칙 파일을 대조해 **표에 없는 허용 규칙**과 **규칙 없는 흐름**을 보고한다.
- 차단 로그(`nft-in-drop`, `nft-out-drop`)는 vm-mgmt로 수집되며, **운용 VM의 출력 차단 로그는 탈취 징후**로 보고 즉시 확인한다.

### 6.5 방화벽 장비 정책

| 출발 \ 목적 | OPS(10) | SVC(20) | DMZ-GS(30) | DMZ-NET(31) | STOR(40) | MGMT(50) | SDC(60) | WAN |
|---|---|---|---|---|---|---|---|---|
| OPS(10) | - | portal:443만 | ✕ | ✕ | ✕ | ✕ | ✕ | ✕ |
| SVC(20) | ✕ | (L2·L3) | 7001 (ros), 2222 (fds·archive) | 2222 (fds만) | NAS 백업 | mgmt 서비스 포트 | ✕ | ✕ |
| DMZ-GS(30) | ✕ | **✕ (새 연결 없음)** | - | ✕ | NAS 백업 | mgmt 서비스 포트 | ✕ | VPN만 |
| DMZ-NET(31) | ✕ | **✕ (새 연결 없음)** | ✕ | - | ✕ | mgmt 서비스 포트 | ✕ | 443 (+발사체 한시) |
| STOR(40) | ✕ | ✕ | ✕ | ✕ | - | ✕ | ✕ | ✕ |
| MGMT(50) | ✕ | 22 | 22 | 22 | 관리 | - | ✕ | 업데이트 저장소만 (제한) |
| SDC(60) | ✕ | archive:2222만 | ✕ | ✕ | ✕ | ✕ | - | (학내 정책) |
| WAN | ✕ | ✕ | VPN 상대→ICD 포트 | 발사체 한시 | ✕ | ✕ | ✕ | - |

추가 설정:

- **VPN**: 인증서 기반 상호 인증, 상대 주소 고정, 터널 안에서도 vm-dgs-gs의 ICD 포트로만 전달.
- **DMZ-NET 아웃바운드**: 443만 허용. 가능하면 DNS 응답 기반 주소 목록이나 명시적 프록시로 Space-Track 도메인에 한정한다.
- **관리 경로**: SSH와 하이퍼바이저 관리 포트는 **VLAN 50 출발만** 허용한다. 관제 PC(VLAN 10)에서는 어떤 VM에도 SSH가 열려 있지 않다.
- **방화벽 장비를 VM으로 대신하는 경우**: 외부 회선 NIC를 해당 VM에 직접 할당(패스스루)하고 하이퍼바이저 자체에는 외부 회선 주소를 주지 않는다. 하이퍼바이저가 탈취되면 방화벽도 함께 무력화되므로, 예산이 확보되면 물리 장비로 옮긴다.

### 6.6 하이퍼바이저 설정

- 하이퍼바이저 관리 인터페이스(웹 관리 화면, SSH)는 **VLAN 50 브리지에만** 둔다.
- 각 VM NIC는 정해진 VLAN 태그로 고정하고, VM 내부에서 VLAN을 바꿀 수 없게 한다.
- VM 단위 방화벽 기능이 있으면 nftables와 같은 허용 규칙을 한 번 더 적용하고, MAC·IP 위장 방지를 켠다.
- DMZ VM(vm-dgs-gs, vm-dgs-net)은 가능하면 **별도 물리 NIC**로 방화벽 장비에 연결한다.
- 하이퍼바이저 관리자 계정은 개인 계정 + MFA, 운용 계정과 분리한다.
- VM 스냅샷은 업데이트 전 롤백 용도로만 쓰고, 백업 대용으로 쓰지 않는다(백업은 8장).

### 6.7 관제 PC 설정

- 운용 전용 장비로 지정하고 인터넷·이메일·개인 용도 사용을 금지한다.
- 브라우저만 운용에 사용하며, 내부 CA 인증서를 신뢰 목록에 등록한다.
- 이동식 저장장치 사용을 차단하거나 승인제로 운영한다.
- 화면 잠금(유휴 5분 내외), 운영체제 자동 로그인 금지.

---

## 7. 웹 운용 화면

### 7.1 진입 구조

```
 관제 PC 브라우저
      │ HTTPS 443
      ▼
 vm-portal: nginx ── auth_request ──► portal-auth (로그인·세션·역할 확인)
      │ /ops/*, /mps/*, /login/*  → 정적 파일 (HTML/JS)
      │ /api/ros/*                → vm-ros:8443 (mTLS)
      │ /api/mps/*                → vm-mps:8443 (mTLS)
      │ /ws/ros                   → vm-ros:8443 WebSocket (실시간 TM)
```

- 관제 PC는 **vm-portal 하나만** 볼 수 있다. ros-api·mps-api는 portal에서 온 요청(방화벽 + mTLS 클라이언트 인증서)만 받는다.
- portal-auth가 확인한 사용자·역할을 nginx가 헤더로 붙여 전달하고, 백엔드는 이 헤더로 권한을 판단한다. 따라서 **portal을 거치지 않은 요청이 백엔드에 닿을 수 없어야** 한다(F02·F03 규칙과 mTLS).

### 7.2 관제 화면 (ROS)

| 화면 | 내용 | 통신 | 필요 역할 |
|---|---|---|---|
| 대시보드 | 링크 상태, 패스 카운트다운(AOS/LOS), 주요 SOH, Limit 경보, 서비스 상태 | WebSocket | 전 운용 역할 |
| TM 상세 | 파라미터별 값·그래프·이력, INVALID TM 표시 | REST + WebSocket | 전 운용 역할 |
| TC 콘솔 | 명령 DB에서 선택 → 파라미터 입력 → 자동 검증 결과 → (위험도별) 승인 요청 → 송출 → ACK | REST | 운용자 이상 |
| 시퀀스 실행 | MPS 시퀀스 파일 로드·재검증·단계 실행, 진행 상태, 미완료 TC | REST + WebSocket | 운용자 이상 |
| 승인함 | 위험 TC 승인·반려 (재인증) | REST | 운용 책임자 |
| 비상 명령 | 사전 승인된 비상 명령 세트 송출, 사유 입력 필수 | REST | 운용자 이상 |
| 이벤트 로그 | 송출·ACK·경보·검증 실패·게이트 차단 기록 | REST | 전 운용 역할 |

### 7.3 미션 스케줄링 화면 (MPS)

| 화면 | 내용 | 필요 역할 |
|---|---|---|
| 명령 목록 | 명령 DB 조회 (파라미터 범위, 위험도, 소요 시간, 사용 자원, 사전 조건) | 전 운용 역할 |
| 패스 목록 | FDS Contact Schedule 기반 패스 일정 | 전 운용 역할 |
| 시퀀스 편집 | 패스 선택 → 명령을 시간 순서로 배치 → 파라미터 입력 | 임무계획 담당 |
| 충돌 분석 | 규칙 검사 결과를 단계별로 표시 | 임무계획 담당 |
| 승인·출력 | 충돌 없음 확인 → 승인(재인증) → TC 시퀀스 파일 생성 → ROS 전달 | 운용 책임자 |
| 임무 요청 | 탑재체팀 Mission Request 조회·반영·반려 | 임무계획 담당 |
| 업링크 파일 반입 | 해시 대조 결과 확인, 버전·용도 확인, FlatSat 시험 여부 기록, 승인 | 운용 책임자 |

#### 충돌 분석 규칙

명령 DB의 명령별 속성(소요 시간, 사용 자원, 사전 조건, 상태 변화)을 이용한 **표 기반 규칙**으로만 구성하며, 규칙 하나 = 함수 하나로 구현한다.

1. 모든 명령이 패스 시간창(AOS~LOS, 앞뒤 여유 시간 제외) 안에 있는가
2. 명령끼리 실행 시간이 겹치지 않는가
3. 같은 자원(송신기, 특정 탑재체 등)을 동시에 쓰지 않는가
4. 사전 조건이 맞는가 (앞 명령의 상태 변화를 순서대로 적용하여 확인)
5. 업링크 데이터량이 (패스 시간 × 전송률) 안에 들어가는가
6. 패스당 명령 수 상한을 넘지 않는가

#### TC 시퀀스 파일

```json
{
  "type": "TC_SEQUENCE",
  "pass_id": "P-20261120-01",
  "cmd_db_version": "a3f9...",
  "created_by": "lee.planner",
  "approved_by": "kim.lead",
  "approved_at": "2026-11-19T09:12:00Z",
  "steps": [
    {"seq": 1, "t_offset_s": 30, "cmd": "PL_MODE_SET", "params": {"mode": 2}, "risk": "caution"},
    {"seq": 2, "t_offset_s": 45, "cmd": "TM_DUMP_REQ", "params": {"start": 0, "count": 100}, "risk": "normal"}
  ]
}
```

ROS는 MPS를 신뢰하지 않고 **자기 명령 DB로 다시 검증**한다. `cmd_db_version`이 ROS의 명령 DB 해시와 다르면 거부한다. 명령 DB는 `interfaces/command_db/` 하나를 두 VM에 같이 배포한다.

### 7.4 계정 인증

| 항목 | 구현 |
|---|---|
| 비밀번호 저장 | Argon2id (libsodium) |
| 세션 | 무작위 토큰, 쿠키 `HttpOnly; Secure; SameSite=Strict`, 유휴 30분·최대 8시간 |
| 다중 인증 | TOTP (운용 책임자·관리자 필수, 운용자는 단계적 적용) |
| 재인증 | 위험 TC 승인·송출, 시퀀스 승인, 업링크 파일 반입 승인 시 비밀번호 재입력 |
| 로그인 보호 | nginx 요청 속도 제한, 연속 실패 시 계정 일시 잠금 |
| CSRF | SameSite=Strict + 상태 변경 요청에 CSRF 토큰 |
| 응답 헤더 | `Content-Security-Policy: default-src 'self'`, `X-Frame-Options: DENY`, `Strict-Transport-Security`, `X-Content-Type-Options: nosniff` |
| 역할 | 운용자 / 운용 책임자 / 임무계획 담당 / 보안 감사자 (보안 계획서 7.5절) |
| 관리자 | 시스템 관리자는 웹 운용 계정을 갖지 않거나, 갖더라도 TC·승인 권한 없음 |

### 7.5 내부 인증서

- vm-mgmt(또는 오프라인 장비)에 내부 CA를 두고 portal, ros-api, mps-api, dgs-gs 스트림, syslog용 인증서를 발급한다.
- CA 개인키는 오프라인 보관하며, 인증서 유효기간은 1년 이내로 하고 만료 전 갱신을 정기 점검 항목에 넣는다.

---

## 8. 로그·백업 구현

보안 계획서 8장을 구현 수준으로 옮긴다.

### 8.1 로그 경로

```
 각 서비스 (libgs log: 한 줄 JSON)
      │ stdout
      ▼
 컨테이너 → journald (각 VM)
      │ syslog over TLS (6514)
      ▼
 vm-mgmt log-collector ── 일 단위 파일 + 해시 기록 (추가 전용)
      │
      ├─► NAS (백업 사본)
      └─► 오프라인 디스크 (주 1회)
```

- nftables 차단 로그, SSH 접속 로그, 하이퍼바이저 감사 로그도 같은 경로로 모은다.
- 모든 VM은 vm-mgmt NTP에 동기화하며, 방화벽 장비가 상위 시각원과 동기화한다(향후 GPS 시각원으로 대체 가능).
- 로그 형식·수집 대상·보관 기간·점검 주기는 보안 계획서 8.2절을 따른다.

### 8.2 백업 경로

| 대상 | 방법 | 주기 |
|---|---|---|
| VM 로컬 DB (ros, mps, portal, archive) | DB 덤프 → NAS 백업 공유 폴더로 전송 | 일 1회 + 패스 후(ros, mps) |
| 설정·compose·nftables·명령 DB | 저장소(Git)가 원본, NAS에 사본 | 변경 시 |
| 컨테이너 이미지 | 내부 레지스트리 + NAS 사본 | 배포 시 |
| 아카이브 데이터 | NAS 원본 → 오프라인 디스크 | 주 1회 |
| 로그 | vm-mgmt → NAS → 오프라인 디스크 | 일 1회 / 주 1회 |
| HMAC 키 | **네트워크 백업 금지.** 암호화 매체 2개 봉인 보관 | 변경 시 |

- 각 VM의 백업 전송 계정은 NAS에 **쓰기만 가능하고 삭제·덮어쓰기는 불가능**하게 설정한다.
- NAS는 백업 폴더에 **불변(삭제 잠금) 스냅샷**을 설정해, 운용 VM이 탈취되어도 과거 백업을 지울 수 없게 한다.
- 오프라인 디스크 2개를 주 단위로 교대하여 MOC 외부에 보관한다.
- 복구 시험은 보안 계획서 8.3절의 주기를 따르며, **"서버 전체 상실 → 새 하드웨어에 VM 재구성 → FlatSat 대상 TC 송출"** 시나리오를 발사 전 최소 1회 수행한다.

---

## 9. 서브시스템 간 통신

### 9.1 두 가지 방식만 사용

| 방식 | 적용 구간 | 구현 |
|---|---|---|
| 실시간 스트림 | ros-core ↔ dgs-gs (TM 프레임, TC 프레임) | TCP + TLS(상호 인증), 길이 접두 프레이밍. 메시지 브로커 미사용 |
| 파일 교환 | 그 외 모든 인터페이스 | 데이터 파일 + 매니페스트(JSON), SFTP 업로드 |
| (VM 내부) | ros-api ↔ ros-core ↔ ros-signer | unix socket + 길이 접두 JSON 메시지 |

### 9.2 매니페스트

```json
{
  "file": "mission_schedule_20261120_v3.json",
  "type": "MISSION_SCHEDULE",
  "from": "mps",
  "to": "ros",
  "version": 3,
  "created": "2026-11-20T01:00:00Z",
  "sha256": "9f2c..."
}
```

- 송신 측: 임시 이름으로 업로드 → 완료 후 이름 변경(원자적 공개). 매니페스트를 마지막에 올린다.
- 수신 측: 매니페스트 확인 → 해시 대조 → 유형이 `n2_chart.json`에서 허용된 것인지 확인 → 스키마 검증 → 처리. 실패 시 `rejected/`로 옮기고 로그를 남긴다.

### 9.3 TC 경로와 보안 통제

```
[MPS 화면] 시퀀스 편집 → 충돌 분석 → 승인(재인증)
     │ TC 시퀀스 파일 (SFTP, 매니페스트·해시)
     ▼
[vm-ros: ros-core] 명령 DB 재검증 (명령·범위·스케줄·시간창·상태)
     │                      ▲
     │            [관제 화면] 위험 TC 승인 (작성자 ≠ 승인자, 재인증)
     ▼
[vm-ros: ros-signer] 승인 상태 확인 → HMAC 부착 (네트워크 없음, 키는 여기만)
     │ unix socket
     ▼
[vm-ros: ros-core] ── TLS 7001 ──► [vm-dgs-gs: tc-gate] SCID·형식·시간창·속도 제한
                                        │
                                        ▼
                                  [kari-adapter] ── VPN ──► 항우연 → 위성 (HMAC 검증)
```

| 단계 | 보안 계획서 항목 | 구현 위치 |
|---|---|---|
| 계획·충돌 분석·승인 | 5.1절 1~4단계 | mps-core, MPS 화면 |
| 자동 검증 5항목 | 5.1절 3단계 | ros-core `tc_validate.c` |
| 위험도별 승인 | 5.2절 | ros-core `tc_manager.c`, 관제 화면 승인함 |
| HMAC 부착 | 4장 | ros-signer |
| 송출 TC 게이트 | 5.5절 (2) | dgs-gs `tc_gate.c` |
| 위성 측 인증 | 4장 | 트랜시버 HMAC |

ROS VM이 탈취되어도 vm-dgs-gs의 게이트가 남고, vm-dgs-gs가 탈취되어도 HMAC 키는 vm-ros의 signer에만 있다.

---

## 10. 운영 관리

| 항목 | 방법 |
|---|---|
| OS 업데이트 | vm-mgmt 업데이트 저장소를 통해 월 1회 (패스 없는 시간대), 업데이트 전 VM 스냅샷 |
| 이미지 업데이트 | 5.5절 절차, 다이제스트 기록, 문제 시 이전 다이제스트로 롤백 |
| 방화벽 규칙 변경 | 저장소에서 변경 → `check_flows` 대조 → 운용 책임자 승인 → 적용 → 변경 로그 |
| 서비스 상태 | 컨테이너 healthcheck → 관제 화면 대시보드에 표시 |
| 패스 전 점검 | 보안 계획서 5.3절 Go/No-Go에 "전 서비스 정상, 로그 수집 정상, 시각 동기 정상" 포함 |
| 권한 재검토 | 학기 시작 시 전체 계정·SFTP 키·인증서 점검 |

---

## 11. 소스 구조

### 11.1 기술 선택

| 용도 | 선택 | 이유 |
|---|---|---|
| 백엔드 언어 | C | 팀 결정 |
| HTTP·WebSocket 서버 | CivetWeb | 소스 내장 가능, WebSocket 지원, MIT 라이선스 |
| JSON | cJSON | 파일 2개, 단순 |
| DB 클라이언트 | libpq | PostgreSQL 공식 C 클라이언트 |
| TLS·HMAC·SHA-256 | OpenSSL | 표준 |
| 비밀번호 해시 | libsodium | Argon2id |
| 외부 HTTPS | libcurl | Space-Track 조회 |
| SFTP 클라이언트 | libssh | 파일 교환 송신 |
| 빌드 | CMake | 서비스별 실행파일 + 공통 정적 라이브러리 |
| 웹 | 순수 JavaScript (ES 모듈), 빌드 도구 없음 | 운용망 인터넷 불가, 유지보수 단순 |
| 웹 그래프 | 경량 그래프 라이브러리를 저장소에 파일로 포함 | CDN 사용 불가 |
| FDS | Java + Orekit (예외) | 11.3절 |

### 11.2 저장소 구조

```
pado-gs/
├── CMakeLists.txt
├── interfaces/                          # 모든 서비스의 기준 (ICD)
│   ├── n2_chart.json                    # 흐름 정의 → SFTP 계정, 허용 유형, 방화벽 대조 기준
│   ├── command_db/commands.json         # 명령: 코드, 파라미터 범위, 위험도, 소요시간, 자원, 사전조건, 상태변화
│   ├── tm_db/telemetry.json             # TM 파라미터 정의, Limit
│   ├── schemas/                         # tc_sequence, mission_schedule, gnss, antenna_pointing ...
│   └── frames/                          # TM/TC 프레임 규격
│
├── libgs/                               # C 공통 라이브러리
│   ├── include/gs/
│   │   ├── log.h          # 한 줄 JSON 로그
│   │   ├── config.h       # JSON 설정 읽기
│   │   ├── manifest.h     # 매니페스트 생성·검증, sha256
│   │   ├── exchange.h     # inbox 감시, 원자적 쓰기, rejected 처리, SFTP 업로드
│   │   ├── ipc.h          # unix socket + 길이 접두 메시지
│   │   ├── framing.h      # TCP+TLS 길이 접두 스트림
│   │   ├── db.h           # libpq 래퍼
│   │   ├── http.h         # CivetWeb 래퍼, 사용자·역할 헤더 추출, JSON 응답
│   │   ├── cmddb.h        # 명령 DB 로드·조회·버전 해시
│   │   └── timeutil.h     # UTC
│   └── src/
│
├── third_party/                         # civetweb, cjson (소스 포함, 버전 기록)
│
├── services/
│   ├── portal-auth/
│   │   ├── main.c          # auth_request 응답, 로그인·로그아웃 API
│   │   ├── users.c         # 계정, Argon2id
│   │   ├── session.c       # 세션 생성·만료
│   │   ├── totp.c          # TOTP
│   │   ├── reauth.c        # 재인증 토큰
│   │   └── lockout.c       # 연속 실패 잠금
│   ├── ros/
│   │   ├── core/
│   │   │   ├── main.c              # 이벤트 루프 (TM 스트림, IPC, 타이머, inbox)
│   │   │   ├── tm_receiver.c       # dgs-gs 스트림 수신
│   │   │   ├── tm_parser.c         # 프레임 → 패킷 → 파라미터
│   │   │   ├── limit_monitor.c     # Limit 검사, 경보
│   │   │   ├── distributor.c       # GNSS·SOH·Attitude·ACK 파일 → 각 서브시스템
│   │   │   ├── tc_validate.c       # 자동 검증 5항목
│   │   │   ├── tc_manager.c        # TC 상태 (작성→검증→승인→송출→ACK)
│   │   │   ├── sequence_runner.c   # 시퀀스 파일 재검증·단계 실행
│   │   │   ├── emergency.c         # 비상 명령 세트
│   │   │   └── tc_sender.c         # signer 호출 → dgs-gs 송출
│   │   ├── signer/
│   │   │   ├── main.c              # unix socket 대기
│   │   │   └── hmac_sign.c         # 승인 확인, HMAC 계산·부착, 순번 관리
│   │   └── api/
│   │       ├── main.c
│   │       ├── routes_tm.c
│   │       ├── routes_tc.c
│   │       ├── routes_seq.c
│   │       ├── routes_approval.c
│   │       └── ws_stream.c
│   ├── mps/
│   │   ├── core/
│   │   │   ├── main.c
│   │   │   ├── inputs.c            # Mission Request, Contact Schedule, ACK·SOH 수신
│   │   │   ├── plan_store.c        # 계획·시퀀스 저장, 버전·이력
│   │   │   ├── conflict.c          # 충돌 규칙 6개 (규칙 하나 = 함수 하나)
│   │   │   ├── uplink_intake.c     # 업링크 파일 해시 대조·반입 승인 상태
│   │   │   └── sequence_export.c   # 승인 시퀀스 → 파일 + 매니페스트 → ROS
│   │   └── api/
│   │       ├── main.c
│   │       ├── routes_cmd.c
│   │       ├── routes_pass.c
│   │       ├── routes_seq.c
│   │       ├── routes_request.c
│   │       └── routes_uplink.c
│   ├── fds/                        # Java + Orekit (예외)
│   ├── dgs-gs/
│   │   ├── main.c
│   │   ├── kari_adapter.c          # 항우연 쪽 프레임 송수신 (협의 후 교체 대상)
│   │   ├── tm_validator.c          # 길이·동기·CRC·SCID, 실패 프레임 격리
│   │   └── tc_gate.c               # SCID·형식·시간창·속도 제한
│   ├── dgs-net/
│   │   ├── main.c
│   │   ├── spacetrack.c
│   │   ├── tle_validate.c
│   │   └── sv_receiver.c
│   └── dgs-archive/
│       ├── main.c
│       ├── archive.c
│       ├── catalog.c
│       └── integrity.c
│
├── web/
│   ├── lib/                        # 그래프 라이브러리 등 (로컬 파일)
│   ├── common/
│   │   ├── api.js                  # fetch 래퍼 (CSRF, 오류, 401 → 로그인)
│   │   ├── ws.js                   # WebSocket 연결·재연결
│   │   ├── auth.js                 # 로그인 상태, 재인증 팝업
│   │   ├── ui.js                   # 표, 경보 배지, 확인 대화상자
│   │   └── style.css
│   ├── login/  index.html, login.js
│   ├── ops/                        # 관제 화면
│   │   ├── index.html
│   │   ├── dashboard.js
│   │   ├── tm_view.js
│   │   ├── tc_console.js
│   │   ├── sequence_run.js
│   │   ├── approvals.js
│   │   ├── emergency.js
│   │   └── events.js
│   └── mps/                        # 미션 스케줄링 화면
│       ├── index.html
│       ├── commands.js
│       ├── passes.js
│       ├── sequence_editor.js
│       ├── conflict_view.js
│       ├── requests.js
│       ├── uplink_intake.js
│       └── export.js
│
├── docker/                          # 서비스별 Dockerfile (다단계 빌드)
│
├── deploy/
│   ├── docker/daemon.json           # 5.2절 공통 데몬 설정
│   ├── nginx/portal.conf            # TLS, 정적 파일, 프록시, auth_request, 보안 헤더, 속도 제한
│   ├── vm/<vm이름>/
│   │   ├── compose.yml
│   │   ├── nftables.conf
│   │   ├── sftp/                    # 계정·chroot 설정 (n2_chart.json에서 생성)
│   │   └── config/                  # 서비스 설정 JSON
│   ├── firewall/                    # 방화벽 장비 정책 (6.5절)
│   └── hypervisor/                  # VM 목록, VLAN·NIC 할당
│
├── sim/
│   ├── fake_kari.c                  # 항우연 쪽 프레임 송수신 흉내
│   └── fake_sat.c                   # TC 수신·HMAC 검증·ACK·TM 생성
│
├── tests/                           # 단위 시험, TM 파서·TLE 검증 퍼징
└── tools/
    ├── check_flows                  # n2_chart ↔ nftables ↔ SFTP 계정 대조
    ├── log_check                    # 패스 후·주간 로그 점검
    ├── pass_report                  # 패스 보고서 생성
    └── restore/                     # 백업 복구 절차 스크립트
```

### 11.3 FDS 예외

제안서의 FDS 주 궤도원은 Orekit(Java) 기반 HPOP이다. 고정밀 궤도 전파기를 C로 새로 구현하는 것은 과제 범위를 넘으므로, FDS는 Java 컨테이너로 구성한다. FDS는 파일 교환으로만 다른 서브시스템과 통신하므로, 매니페스트 규칙만 지키면 전체 구조에 영향이 없다. 매니페스트 생성·검증은 Java에서 같은 규칙으로 구현한다.

### 11.4 서비스 골격

모든 C 서비스는 같은 형태의 `main.c`를 쓰고, 서비스마다 다른 것은 처리 함수뿐이다.

```c
int main(int argc, char **argv) {
    gs_config_t cfg;
    if (argc < 2 || gs_config_load(argv[1], &cfg) != 0) return 1;
    gs_log_init(cfg.service_name, cfg.zone);

    gs_item_t item;
    while (gs_exchange_next(&cfg.inbox, &item) == 0) {        /* 매니페스트 도착 대기 */
        if (gs_manifest_verify(&item, cfg.allowed_types) != 0) {
            gs_exchange_reject(&item);                        /* 해시·유형·스키마 실패 */
            continue;
        }
        if (process_item(&item, &cfg) != 0)                   /* 서비스별로 이 함수만 다름 */
            gs_log_event("process_failed", &item);
        gs_exchange_done(&item);
    }
    return 0;
}
```

### 11.5 C 보안 코딩 규칙

외부 입력을 처음 받는 코드(tm_validator, tm_parser, tle_validate, sv_receiver, 웹 API JSON 처리, 매니페스트 검증)는 특히 다음을 지킨다.

| 항목 | 규칙 |
|---|---|
| 컴파일 옵션 | `-Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE -pie -Wl,-z,relro,-z,now` |
| 시험 빌드 | `-fsanitize=address,undefined` |
| 파서 작성 | 길이를 먼저 확인하고 고정 크기 구조체로 복사. 가변 길이 할당 최소화 |
| 문자열 | `strcpy`/`sprintf` 금지, 길이 지정 함수만 사용 |
| 퍼징 | TM 파서, TLE 검증, 매니페스트 검증을 퍼징 시험 대상에 포함 |
| 정적 분석 | CI에서 정적 분석 도구 실행, 경고 0 유지 |
| 의존성 | `third_party/`의 버전을 기록하고 보안 패치를 월 1회 확인 |

---

## 12. 개발 순서

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1 | `interfaces/`(명령 DB, TM 정의, 스키마, n2_chart)와 `libgs/` | 매니페스트·로그·교환 단위 시험 통과 |
| 2 | VM 간 통신 기반: ROS↔DGS-GS 실시간 스트림, N2 파일·매니페스트 교환, VM 내부 Unix socket의 공통 구현과 개발용 격리 네트워크 연결 | 확정된 흐름의 송수신·거부 통합 시험 통과. 미정 ICD 경로는 열지 않음 |
| 3 | `sim/fake_kari` → `dgs-gs` → `ros-core` TM 수신 | 시험 프레임이 ROS에 기록되고 잘못된 프레임은 거부됨. 실제 TM 해석은 ICD 확정 후 검증 |
| 4 | MPS 핵심 기능: 명령·패스 목록, 시퀀스 편집·충돌 분석·출력, ROS 연동 | 확정된 규칙의 단위·통합 시험 통과. 미확정 TC 운용 값은 사용하지 않음 |
| 5 | TC 경로: 검증 → 승인 → signer → tc-gate → `fake_sat` ACK | 위험 TC 2인 승인, 게이트 차단 시험 통과. 실제 명령·프레임 시험은 ICD 확정 후 수행 |
| 6 | FDS, DGS-NET, DGS-Archive, SDC 교환 영역 | 확정된 N2 흐름의 서비스 간 교환 시험 통과. 외부 ICD 의존 흐름은 별도 판정 |
| 7 | 단일 Linux VM 배포 기반: Docker 서브시스템 분리·호스트 UFW·로그·시간 동기 | `check_flows` 통과, 컨테이너 간 격리·로그 수집·호스트 방화벽 확인. N2 연결 시작 방향은 실제 서비스 경계에서 확인 |
| 8 | 백업·복구, 파서 퍼징, 핵심 서비스 보안 시험 | 복구 시나리오와 핵심 서비스 시험 통과 |
| 9 | `portal-auth` + nginx + ROS·MPS 웹 UI 통합 | 관제용 PC가 vm-portal에 로그인해 ROS·MPS 기능을 사용하고, VM API에 직접 접속하지 않으며, 전체 경로 시험 통과 |

단계 4는 작은 검증 단위로 나누어 진행한다. 4-1부터 순서대로 완료 판정하며, 각 단계의 미정 외부 입력은 승인된 형식이 나오기 전까지 운용 데이터로 수용하지 않는다.

| 세부 단계 | 범위 | 완료 기준 |
|---|---|---|
| 4-1 | MPS 입력·카탈로그 기준선: 현재 비운용 명령 DB의 엄격한 판독, 내부 패스 목록 모델 | 미확정 명령의 운용 사용을 거부하고, 패스 시간창·식별자·목록 경계 시험 통과. FDS 파일 수신 형식은 미정으로 유지 |
| 4-2 | 시퀀스 초안 편집·계획 버전과 이력 | 패스와 명령 DB 버전에 결합된 초안 갱신·동시 수정 거부 시험 통과 |
| 4-3 | 시간창·중첩·자원·사전 조건·데이터량·명령 수 충돌 분석 | 규칙별 통과·위반 시험 통과. 명령 속성은 확정된 ICD가 없으면 시험 전용 계약만 사용 |
| 4-4 | 계획 확정·승인, TC 시퀀스/매니페스트 출력과 ROS 연동 | 승인되지 않은 초안 출력 차단, ROS 독립 재검증 경로 시험 통과 |
| 4-5 | Mission Request 반영·반려와 업링크 파일 반입 승인 | 출처·해시·버전·FlatSat 기록·권한 검사와 거부 시험 통과 |

단계 2~6의 개발용 통신 계약은 단일 Linux 서버에서 서브시스템별 VM을 실행하는 최종 구조를 기준으로 한다. 단계 2에서는 VMware Linux VM 한 대의 Docker 내부 네트워크를 개발용 시험 환경으로 사용한다. 물리 방화벽·VLAN 스위치와 서버 사양이 미정이므로 실제 장비 설정은 단계 7에서 확정한다. 개발용 컨테이너·가상 네트워크 시험은 단계 7의 실제 VM 배포·격리 검증을 대체하지 않는다. ROS·MPS 웹 UI는 각 서브시스템의 핵심 기능 구현 후 단계 9에서 구성한다.

---

## 13. 보안 계획서 대응표

| 보안 계획서 항목 | 본 문서 구현 위치 |
|---|---|
| 3장 연동 모델 (VPN, 프레임 단위) | 6.2절 F05, 6.5절, `kari_adapter.c` |
| 4장 HMAC·키 관리 | 4.2절 ros-signer, 5.4절 키 마운트, 8.2절 키 백업 |
| 5.1절 TC 8단계 절차 | 7.2·7.3절 화면, 9.3절 |
| 5.2절 위험도 분류 | `command_db/commands.json`, `tc_manager.c` |
| 5.4절 비상 운용 | 관제 화면 비상 명령, `emergency.c` |
| 5.5절 (1) 네트워크 방화벽 | 6장 전체 |
| 5.5절 (2) 송출 TC 게이트 | `tc_gate.c` |
| 5.5절 (3) TM 입력 검증 | `tm_validator.c` |
| 6.3절 지상국 내부 반입 검증 | MPS 업링크 파일 반입 화면, `uplink_intake.c` |
| 7.1절 DGS-GS / DGS-NET 분리 | vm-dgs-gs, vm-dgs-net (별도 VM, 별도 VLAN) |
| 7.2절 구역 구성 | 3.1절 VLAN |
| 7.4절 흐름 허용표 | 6.2·6.3절, `n2_chart.json` |
| 7.5절 계정·권한 | 7.4절 |
| 8장 로그·백업 | 8장 |

---

## 14. 미결 사항

| 항목 | 확인 대상 | 영향 |
|---|---|---|
| 방화벽 장비·VLAN 스위치 확보 여부 | 연구실 예산 | 6.5절 (VM 대체 시 위험 증가) |
| 항우연 연동 포트·프로토콜 | 항우연 | F05, `kari_adapter.c` |
| 학교망 고정 주소·VPN 허용 | 정보처 | 3.1절, 6.5절 |
| 서버 사양 (VM 8개 수용) | 장비 사양서 | 4.1절 |
| NAS 불변 스냅샷 기능 지원 여부 | NAS 사양서 | 8.2절 |
| SDC 연결 위치 (전용 VLAN / 학내망) | 탑재체팀 | VLAN 60 |
| 하이퍼바이저 종류 | 연구실 결정 | 6.6절 세부 설정 |
