# N2 차트 기반 지상국 인터페이스 정리

이 문서는 원본 [지상국.pdf](../지상국.pdf)의 N2 차트에 나온 **서브시스템 간 자료 교환**을 설명한다. TC/TM 패킷 내부 필드와 바이트 배치는 별도 [TC/TM 문서](../TC_TM/README.md)의 주제다. 아래 표의 기준 데이터는 [`interfaces/n2_chart.json`](../../interfaces/n2_chart.json)이다.

발표용 흑백 그림: [전체 구조](N2_전체구조.png) · [자료 흐름 묶음](N2_자료흐름.png). 정확한 개별 경로와 현재 상태는 아래 표를 기준으로 한다.

## 읽는 방법

- **자료 방향**은 정보를 생산하는 쪽에서 사용하는 쪽으로 가는 방향이다. **연결 개시**는 TCP/SFTP 세션을 먼저 여는 쪽이다. SFTP로 파일을 가져올 때 두 방향은 반대일 수 있다.
- `DGS-GS`는 안테나 연동, `DGS-NET`은 TLE·상태벡터 출처 연동, `DGS-Archive`는 외부 과학자료 센터(SDC) 연동의 접점이다. ROS·MPS·FDS는 외부 상대에게 직접 연결하지 않는다. 표에 없는 통신은 허용하지 않는다.
- 아래 방식과 포트는 **연결 설계값**이다. JSON에 경로가 있어도 서비스가 배포·연결됐거나 상대 기관과 운용 ICD가 확정됐다는 뜻은 아니다. `미정`인 외부 경로는 방화벽에 허용 규칙을 만들지 않는다.
- 표의 `시험`은 가상 위성, 로컬 파일·컨테이너, 일회용 연결 시험을 뜻한다. 실제 RF, 항우연·연세대, Space-Track, SDC 연동과 구별한다.

## 전체 인터페이스

| ID | 자료 방향 | 교환 자료 | 연결 개시·방식 | 현재 구현 범위 |
|---|---|---|---|---|
| N01 | 연세대·항우연 안테나 → DGS-GS | TM, Payload Data | 미정: 상대 ICD 필요 | 외부 수신 미연결. 가상 위성 TM 수신은 별도 내부 시험이며 S-band payload 수신은 현재 범위에서 제외 |
| N02 | DGS-GS → 연세대·항우연 안테나 | TC, Antenna Pointing | 미정: 상대 ICD 필요 | 외부 송신 차단. 시험 TC 게이트와 가상 위성 왕복은 실제 안테나 송출이 아님 |
| N03 | Space-Track → DGS-NET | NORAD TLE | DGS-NET → Space-Track, HTTPS 443 | 수집·검증 경계 설계. 실제 Space-Track 계정과 상주 조회 미연결 |
| N04 | 발사체 제공기관 → DGS-NET | State Vector File | 미정: 상대 ICD 필요 | 외부 수신 차단 |
| N05 | DGS-NET → FDS | NORAD TLE, State Vector File | FDS → DGS-NET, SFTP 회수 2222 | 검증 파일·매니페스트, 인증 회수, 불변 입력 스냅샷 시험. 실제 외부 TLE 공급원은 미연결 |
| N06 | DGS-GS → ROS | TM | ROS → DGS-GS, TLS 스트림 7001 | 합성 TM의 상호 인증 스트림·거부 시험. 실제 안테나 수신 TM은 미연결 |
| N07 | ROS → DGS-GS | TC | ROS → DGS-GS, TLS 스트림 7001 | 시험 TC 구조·시간창 게이트만 검증. 운용 승인·서명·외부 송출 미연결 |
| N08 | FDS → DGS-GS | Antenna Pointing, Pass Request | FDS → DGS-GS, SFTP 송신 2222 | 두 지상국의 시험 지향·패스 요청을 로컬 출고함에 생성·검증. DGS-GS 및 안테나로 실제 전송하지 않음 |
| N09 | DGS-GS → DGS-Archive | CREST Raw, SIREN Raw | DGS-Archive → DGS-GS, 읽기 전용 SFTP 회수 2222 | 회수 경로 설계만 유지. S-band payload 수신·원시데이터 이관은 현재 제외 |
| N10 | ROS → MPS | ACK, Bus SOH | ROS → MPS, SFTP 송신 2222 | 가상 위성 ACK·전력 시험 SOH의 웹 조회. 운용 N10 파일 교환은 미연결 |
| N11 | ROS → FDS | GNSS | ROS → FDS, SFTP 송신 2222 | 가상 TM에서 시험 GNSS 파일·매니페스트를 게시하고 FDS에서 재검증. 실제 GNSS TM 매핑·운용 송신은 미확정 |
| N12 | MPS → ROS | Mission Schedule, TC Sequence, Approved Uplink | MPS → ROS, SFTP 송신 2222 | PASS별 시험 시퀀스 저장·ROS `SIM ONLY` 전달/예약·가상 위성 왕복. 운용 승인·파일 송신 미연결 |
| N13 | FDS → MPS | Contact Schedule, Orbital Event | FDS → MPS, SFTP 송신 2222 | 시험 접촉 일정 파일·매니페스트와 웹 PASS 조회. Orbital Event의 실제 항목·소비 규칙은 미정 |
| N14 | ROS → DGS-Archive → CDPS·SDPS | S/C Attitude, Bus SOH | ROS → DGS-Archive, SFTP 송신 2222 | 내부 전달 경로와 파일 검증 설계. SDC 전달·상대 수락은 미연결 |
| N15 | FDS → DGS-Archive → CDPS·SDPS | Orbit Ephemeris, Pass Information | FDS → DGS-Archive, SFTP 송신 2222 | 시험 산출물의 출고함·SFTP 송신기 검증. Archive·SDC 애플리케이션 수락은 미연결 |
| N16 | CDPS·SDPS(SDC) → DGS-Archive | Mission Request, Uplink File | SDC → DGS-Archive, SFTP 송신 2222 | 외부 SDC 연동 미연결. 상대·파일 계약 확정 전 수신 금지 |
| N17 | DGS-Archive → CDPS·SDPS(SDC) | CREST/SIREN Raw, S/C Attitude, Bus SOH, Orbit Ephemeris, Pass Information | SDC → DGS-Archive, SFTP 회수 2222 | 외부 SDC 연동 미연결. Payload Raw는 현재 개발 범위에서 제외 |
| N18 | DGS-Archive → MPS | Mission Request, Uplink File | DGS-Archive → MPS, SFTP 송신 2222 | 전달 경로·허용 자료형 설계. 실제 SDC 원본 수신과 MPS 반영은 미연결 |
| N19 | ROS → DGS-Archive | Backup File | ROS → DGS-Archive, SFTP 송신 2222 | 내부 보관 경로 설계·파일 검증 범위. NAS 연결 없음 |
| N20 | MPS → DGS-Archive | Backup File | MPS → DGS-Archive, SFTP 송신 2222 | 내부 보관 경로 설계·파일 검증 범위. NAS 연결 없음 |
| N21 | FDS → DGS-Archive | Backup File | FDS → DGS-Archive, SFTP 송신 2222 | 내부 보관 경로 설계·파일 검증 범위. NAS 연결 없음 |

## 원본 N2에서 현재 설계로 정리한 사항

| 원본 N2 표기·요구 | 현재 처리 |
|---|---|
| DGS → ROS의 `TM, TLE` | 원본 표의 TLE와 같은 PDF의 설명이 충돌한다. 사용자 결정에 따라 **TLE는 FDS**로 전달하고 ROS에는 TM만 보낸다. |
| DGS → CDPS/SDPS의 CREST·SIREN Raw | DGS-Archive가 DGS-GS 임시 저장소에서 **읽기 전용으로 회수**하는 N09를 둔다. Archive가 SDC의 유일한 자료 접점이다. Payload 수신 자체는 현재 제외한다. |
| CDPS/SDPS와 ROS·FDS 간 자료, DGS로 향하는 백업 | 외부 SDC 자료는 N14–N18의 DGS-Archive를 경유한다. 백업은 N19–N21로 DGS-Archive에 모으며 NAS를 전제로 하지 않는다. |
| 기능을 표현하는 원본 자료명 | `PASS_REQUEST`, `TC_SEQUENCE`, `APPROVED_UPLINK`, `UPLINK_FILE`처럼 설계 과정에서 세분화·추가한 유형은 원본 N2 자료명과 구별한다. |

N2 차트는 **누가 어떤 자료를 누구에게 주는가**의 기준이다. 상대 기관의 프레임, 파일 스키마, 인증, 주소, 운용 포트와 수락 응답이 확정되고 양쪽 연결 시험이 끝나야 해당 경로를 운용 ICD로 완료 처리할 수 있다. 현재 외부 N01·N02·N04와 N16·N17은 그 단계에 도달하지 않았다. 또한 항우연 S-band PASS는 조회용이며 현재 TC 구성·송출 대상이 아니다.
