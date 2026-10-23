# PADO-SAT TC/TM 설계 자료

PDR의 CS·CDHS 자료를 바탕으로 만든 **비운용 검토 초안**입니다. 숫자 ID와 바이트 배치는 지상국 제안이며 비행 소프트웨어에서 승인된 ICD가 아닙니다. [TC/TM 설계·미결 사항 전체 정리](DESIGN.md)를 먼저 확인하세요.

`preview_limits.example.json`의 한계값은 ROS 화면 시험용 예시이며 실제 센서 허용범위가 아닙니다.

`tools/render_packet_diagrams.py`로 생성한 검토용 이미지입니다. PDR에 명시된 UHF
링크 사실과 지상국이 제안한 응용 패킷 초안을 구분했습니다. 실제 비행용 ICD 확정
또는 송출 허가를 뜻하지 않습니다.

1. [UHF 프레임과 응용 패킷의 경계](01_uhf_frame_overview.png)
2. [TC 패킷 구조와 명령 카탈로그](02_tc_packet_and_commands.png)
3. [TM 패킷 카탈로그와 데이터 활용](03_tm_packet_catalog.png)
4. [응용 PDU 바이트 필드 정의](04_byte_field_layout.png)

원본 규격과 보류 사항은 [패킷 설계 문서](../../interfaces/pado_packets/README.md),
정확한 후보 ID·필드 순서는
[`draft_v0.json`](../../interfaces/pado_packets/draft_v0.json)을 확인하세요.
