# PADO-SAT TC/TM 패킷 자료 안내

> 이 문서는 TC/TM 패킷과 시험용 화면 자료를 설명한다. **N2 차트의 서브시스템 간 ICD는 [N2 인터페이스 정리](../N2_ICD/README.md)를 참고한다.** 이 문서의 TC/TM 형식은 비행 소프트웨어 및 외부 지상국이 승인한 운용 ICD와 다르다.

## 1. 현재 무엇이 결정됐는가

| 구분 | 현재 정리된 내용 | 결정 수준 |
|---|---|---|
| 서브시스템 간 자료 흐름 | 원본 N2의 21개 자료 방향을 N01–N21 경로로 정리했다. 외부 통신은 DGS만 담당하고 목록 밖 흐름은 차단한다. | **지상국 구조·접근 정책 기준**. 실제 연결 완료를 뜻하지 않음 |
| 외부 안테나 연동 | 안테나→DGS-GS의 TM, DGS-GS→안테나의 TC·안테나 지향 자료 방향을 정의했다. | **방향만 정의**. 항우연·연세대의 프로토콜·포트·프레임 ICD 미정 |
| UHF 계층 | PDR에 AX100 Mode 5와 CSP v1의 사용 맥락이 있다. 그 위에 지상국이 PADO 응용 PDU를 제안했다. | **PDR 근거 + 지상국 초안**. 실제 복조 출력과 CSP/PDU 경계 확인 필요 |
| 응용 PDU | 최대 200 B, 20 B 헤더 + 최대 176 B 본문 + 4 B CRC32. TC·TM에 공통 구조를 적용한다. | **시험용 바이트 배치 제안**. 비행 승인 전 |
| TC 카탈로그 | 후보 58종을 정리했다. 인자가 정의된 36종만 시험용 인코딩이 가능하며, 나머지 22종은 거부한다. | **기능 목록은 PDR 기반, 숫자 ID·인코딩은 제안** |
| TM 카탈로그 | 후보 16종. 전력·온도, 자세, GNSS, 비콘 요약, 명령 결과, 장치 상태 등을 포함한다. | **지상국 제안**. 실제 OBC 다운링크 매핑·단위·보정 미확정 |
| 인증·응답 | CRC32는 오류 검출에만 사용한다. 가상 위성의 명령 결과 TM은 시험 왕복에 사용한다. | **운용 인증·ACK ICD 미정**. 실제 TC 송출 불가 |

## 2. 주요 ICD 경계와 구현 상태

| N2 경로 | 자료 방향과 내용 | 현재 상태 |
|---|---|---|
| N01·N02 | 안테나 ↔ DGS-GS: TM·TC·안테나 지향 | 외부 상대의 포트·프레임·접속 방식 미정. 실제 송수신 차단. S-band payload 수신은 현 개발 범위 밖 |
| N03·N05 | Space-Track → DGS-NET → FDS: TLE | HTTPS 수집·SFTP 회수 방향 설계와 파일 검증 시험. 실제 외부 계정·서비스 연동 대기 |
| N06·N07 | DGS-GS ↔ ROS: TM 수신·TC 전달 | 내부 TLS 스트림 설계와 합성 시험. 실제 안테나/ROS 운용 경로 미연결 |
| N08 | FDS → DGS-GS: PASS 요청·안테나 지향 | 시험 산출물·매니페스트를 로컬 출고함에만 생성. 외부 전송 안 함 |
| N11·N13·N15 | ROS→FDS GNSS, FDS→MPS PASS, FDS→Archive 궤도력 | 검증된 시험 파일·일정·예측 산출물의 내부 경계 구현. 실제 상대별 수락 계약은 별도 확인 필요 |
| N12 | MPS → ROS: 계획·TC 시퀀스 | PASS별 시험 초안 전달·예약과 가상 위성 왕복. 실제 승인·서명·RF 송출과 분리 |

**데이터 방향과 네트워크 접속을 시작하는 주체는 다를 수 있다.** 정확한 방향·연결·포트 설계는 [`n2_chart.json`](../../interfaces/n2_chart.json)의 `approved_flows`를 기준으로 확인한다. 값이 `null` 또는 `TBD(ICD)`인 경로는 방화벽 허용 규칙으로 바꾸지 않는다.

## 3. 제안한 TC/TM 응용 PDU의 표

| 바이트 위치 | 길이 | 의미 |
|---:|---:|---|
| 0 | 2 B | 식별자 `PS` |
| 2–3 | 각 1 B | 초안 버전 `0`, 종류 `1=TC` 또는 `2=TM` |
| 4–7 | 각 2 B | 제안 TC/TM ID, 시퀀스 |
| 8–15 | 8 B | 발행·측정 시각, UTC Unix ms |
| 16–19 | 4 B | 본문 길이, 초안·예약 플래그, 예약 필드 |
| 20부터 | 0–176 B | JSON 카탈로그에 정한 순서의 본문 필드 |
| 마지막 | 4 B | 헤더와 본문의 CRC32 |

다중 바이트 값은 big endian이다. TC 본문은 예약 실행 시각 `execute_at_utc_ms` 8 B로 시작한다. 이 구조는 **CSP·AX100 무선 프레임 전체가 아니라 그 안에 들어갈 응용 PDU 초안**이다. PDU의 200 B 상한과 실제 RF·CSP 바이트 예산은 CS/CDHS와 함께 확인해야 한다.

| 시험 TM 예 | 제안 ID | 사용하는 화면·기능 |
|---|---:|---|
| `EPS_PDU1` | `0x2011` | ROS 전압·온도 추세와 시험 한계 |
| `AOCS_STATE` | `0x2020` | ROS 자세 쿼터니안·각속도 |
| `GNSS_PVT` | `0x2021` | ECEF 위치·속도 검증 후 FDS 시험 GNSS 입력 |
| `COMMAND_RESULT` | `0x2060` | 가상 위성의 TC 결과를 명령 ID·시퀀스와 연결 |

이 ID와 필드는 **승인된 비행 패킷 번호가 아니다.** 정확한 후보 목록과 필드 타입은 아래 JSON 카탈로그가 기준이다.

## 4. 발표용 PNG 네 장

| 파일 | 크기 | 무엇을 보여주는가 | 활용 |
|---|---:|---|---|
| [01 — UHF 통신 경로](01_uhf_frame_overview.png) | 1920×1080 | TC 업링크·TM 다운링크의 큰 흐름 | 위성과 지상의 역할을 설명할 때 |
| [02 — TC 구성과 실행](02_tc_packet_and_commands.png) | 1920×1080 | 명령·인자·실행 시각과 즉시·예약 실행 | TC가 담는 정보를 설명할 때 |
| [03 — TM 데이터와 활용](03_tm_packet_catalog.png) | 1920×1080 | TM 데이터 종류와 ROS/FDS 사용처 | 관제·궤도 입력의 관계를 설명할 때 |
| [04 — PDU에 담기는 정보](04_byte_field_layout.png) | 1920×1080 | 공통 구성과 TC·TM 데이터 예시 | PDU를 처음 소개할 때 |

PNG는 [`render_packet_diagrams.py`](../../tools/render_packet_diagrams.py)로 생성한 **흑백·검은 글자 설명용 도식**이다. 원본 데이터나 비행용 규격 증거가 아니다. 상세 표와 그림 설명은 [설계 문서](DESIGN.md)에 있다.

## 5. JSON 파일의 역할

| 파일 | 담긴 정보 | 해석할 때 주의할 점 |
|---|---|---|
| [`n2_chart.json`](../../interfaces/n2_chart.json) | N2 자료 흐름 21개와 향후 허용할 연결 방향·유형 | 설계 허용 목록이지 실제 배포·외부 ICD 확정 목록이 아님 |
| [`draft_v0.json`](../../interfaces/pado_packets/draft_v0.json) | `pado.packet-catalog.v0`: TC 58종·TM 16종의 후보 ID, 이름, 필드 순서·타입, 위험도 | `status=DRAFT_NOT_FLIGHT_APPROVED`, `operational=false`. JSON 자체를 위성에 송신하는 것이 아니라 **바이너리 PDU를 설명하는 카탈로그** |
| [`source_inventory.json`](../../interfaces/pado_packets/source_inventory.json) | PDR에서 확인한 장치별 HK·명령 기능의 출처 목록 | 필드의 실제 다운링크 바이트 배치를 승인한 자료가 아님 |
| [운용 TC DB](../../interfaces/command_db/commands.json) · [운용 TM DB](../../interfaces/tm_db/telemetry.json) | 향후 승인 ICD를 등록할 별도 데이터베이스 | 현재 운용 항목이 비어 있어 시험 카탈로그를 자동으로 활성화하지 않음 |
| [`preview_limits.example.json`](preview_limits.example.json) | ROS 그래프용 시험 센서 한계 예시 | 실제 위성의 허용범위나 운용 경보 기준이 아님 |

## 6. 운용 ICD로 확정하기 전에 필요한 것

CS/CDHS가 승인한 **실제 TC/TM 번호·인자·단위·비콘/전체 HK 배치**, OBC 명령 인증과 ACK, AX100/CSP 송수신 바이트 경계, OEM719A→OBC GNSS TM 매핑, CubeADCS 자세 좌표계가 필요하다. 양쪽 구현의 golden vector와 FlatSat 왕복 시험으로 검증한 뒤에만 운용 DB와 DGS 송출 게이트에 반영한다. 세부 인계 항목은 [검토표](REVIEW_GATES.md)에 있다.
