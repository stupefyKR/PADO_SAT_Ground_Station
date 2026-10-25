# PADO-SAT TC/TM 설계 자료

PDR의 CS·CDHS 자료를 바탕으로 만든 **비운용 검토 초안**입니다. 숫자 ID와 바이트 배치는 비행 소프트웨어에서 승인된 ICD가 아닙니다. 패킷 개요는 [TC/TM 패킷 자료 안내](ICD_현황_표와_자료안내.md), 상세 내용은 [TC/TM 설계·미결 사항 전체 정리](DESIGN.md)를 확인하세요. N2 차트의 서브시스템 간 자료 교환은 [N2 인터페이스 정리](../N2_ICD/README.md)에 있습니다.

현재 구현에서 운용 승인까지 필요한 입력은 [검토·인계표](REVIEW_GATES.md)에 정리했습니다.

`preview_limits.example.json`의 한계값은 ROS 화면 시험용 예시이며 실제 센서 허용범위가 아닙니다.

`tools/render_packet_diagrams.py`로 생성한 흑백·검은 글자 검토용 이미지입니다.
UHF 통신 경로와 TC/TM PDU가 담는 정보를 큰 흐름으로 보여줍니다. 상세 필드와
출처는 [설계 문서](DESIGN.md)와 JSON 카탈로그에서 확인합니다. 실제 비행용 ICD
확정 또는 송출 허가를 뜻하지 않습니다.

1. [UHF 통신 경로](01_uhf_frame_overview.png)
2. [TC 구성과 실행](02_tc_packet_and_commands.png)
3. [TM 데이터와 활용](03_tm_packet_catalog.png)
4. [PDU에 담기는 정보](04_byte_field_layout.png)

원본 규격과 보류 사항은 [패킷 설계 문서](../../interfaces/pado_packets/README.md),
정확한 후보 ID·필드 순서는
[`draft_v0.json`](../../interfaces/pado_packets/draft_v0.json)을 확인하세요.
