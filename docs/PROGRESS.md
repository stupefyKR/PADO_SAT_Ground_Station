# 진행 기록

## 현재 상태

- 현재 단계: 첫 세션 저장소 초기 구성 (단계 1 구현 전)
- 다음 작업: 단계 1의 `interfaces/` 기준 데이터와 `libgs/` 구현 범위 확정
- 미결 사항: TBD(ICD) 항우연·연세대 연동 포트·프로토콜·프레임, TBD(HMAC) 트랜시버 세부 사양

## 기록

### 2026-10-21

- 완료: 작업 폴더의 `pado-gs/`를 독립 개발 저장소로 구성하고, 11.2절 디렉터리 골격, 기준 문서 2종, `.gitignore`, 커밋 전 비밀정보 검사, CMake 정적 라이브러리 및 CTest 골격 작성
- 시험: Windows MSVC Release 빌드 경고 0, CTest 1/1 통과; MSVC AddressSanitizer 빌드 경고 0, CTest 1/1 통과; 비밀정보 검사 단위 시험 4/4 통과. Linux GNU/Clang 및 UndefinedBehaviorSanitizer 미실행
- 변경 파일: `CMakeLists.txt`, `libgs/`, `tests/`, `tools/check_secrets.py`, `.githooks/`, `docs/`, `README.md`
- 결정: 초기 빌드 확인용 임시 `gs_bootstrap_ready` 심볼과 Windows 빌드 옵션 → `DECISIONS.md` #1
- 문제: MSVC 기본 도구 선택 시 PDB 도구 버전 충돌이 발생하여, 설치된 `14.44.35207` 도구 버전을 빌드 명령에 지정함
- 원격 공개 범위: 첫 푸시 직후 GitHub 익명 API에서 공개 저장소로 확인됨. 사용자 확인을 받아 비공개로 전환했고, 설정 화면의 `currently private` 표시 및 익명 API 404를 확인함. 공개 상태였던 시간 동안 외부 열람 가능성은 배제할 수 없음
- 다음: 단계 1 세부 작업 계획 검토 후 구현 시작
