# KRX 업체현황(연간) 데이터 빌드

원본은 공개 페이지 밖의 `source-data/krx-annual/Policy.zip`에 보존합니다.

- [GitHub 저장 파일 확인](https://github.com/ebrain725/ebrain725.github.io/blob/main/source-data/krx-annual/Policy.zip)
- [원자료 ZIP 직접 다운로드](https://raw.githubusercontent.com/ebrain725/ebrain725.github.io/main/source-data/krx-annual/Policy.zip)

다음 명령으로 2015~2025년 정적 데이터를 다시 생성합니다.

```bash
python scripts/build_krx_annual.py --compact
```

다른 ZIP을 시험할 때만 입력 경로를 덮어씁니다.

```bash
python scripts/build_krx_annual.py \
  --input /path/to/Policy.zip \
  --relations /path/to/entity-relations.json \
  --output public/data/krx-annual.json \
  --compact
```

공식 합병·분할 근거는 같은 폴더의 `entity-relations.json`에 별도로 보존하며,
빌드 시 업체 ID와 정확히 연결되는지 검증합니다.

## 공개 JSON 계약

출력 경로는 `public/data/krx-annual.json`입니다. 최상위 필드는 다음과 같습니다.

- `source`: 원본 ZIP 해시, 빌더 버전, 대상기간
- `meta`: `years`, 연도별 공식 총수량 `annualCaps`, 계획기간 총수량
- `metrics`: 지표 설명, 산식, 0과 `null` 처리 규칙
- `classifications`: 표준 부문 순서, 표준 업종, 원문→표준 매핑
- `entities`: 안전하게 정규화한 업체 ID와 원문 별칭
- `entityRelations`: 공식 근거가 확인된 합병·분할 승계 관계
- `rows`: UI가 바로 사용하는 업체·연도별 flat 레코드
- `quality`: 공란, 중복 합산, 결측, 분류 품질 집계

`rows`의 필수 구조는 아래와 같습니다.

```json
{
  "year": 2025,
  "sector": "산업",
  "industry": "1차 철강 제조업",
  "companyId": "company-xxxxxxxxxxxx",
  "companyName": "업체명",
  "aliases": ["원문 업체명"],
  "relatedCompanyNames": ["승계 전·후 연관 업체명"],
  "entityRelations": [
    {"relationType": "흡수합병", "direction": "to"}
  ],
  "rawClassifications": [
    {"sector": "원문 부문", "industry": "원문 업종"}
  ],
  "allocationType": "Y",
  "metrics": {
    "preAllocation": 0,
    "additionalAllocation": 0,
    "cancellation": 0,
    "adjustedAllocation": 0,
    "verifiedEmissions": 0,
    "carryover": 0,
    "carryoverAllowance": 0,
    "carryoverOffset": 0,
    "borrow": 0,
    "offsetIssued": 0,
    "finalBalance": 0
  },
  "missing": {"finalBalance": false},
  "missingReason": {},
  "qualityFlags": []
}
```

`allocationType`은 사전할당 원문의 `Y`(유상), `N`(무상), `null`(미제공
또는 복수 원문 충돌)입니다.

## 원자료 정규화 산식

- 조정할당 = 해당 원자료 연도의 사전할당 + 추가할당 - 할당취소
- 이월량 = 할당배출권 이월 + 상쇄배출권 이월
- 상쇄발행은 참고지표이며 연초 과부족량 산식에는 넣지 않습니다.

## 대시보드 연초 과부족 산식

업체현황 화면은 `public/assets/krx-annual-balance-v2.js`에서 당해년도 할당과
전년도 이행실적을 연결하여 다음 값을 계산합니다.

- 반영 사전할당량 = 당해년도 사전할당량 + 전년도 추가할당량 - 전년도 할당취소량
- 연초 과부족량 = 반영 사전할당량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량
- 전체식 = (당해년도 사전할당량 + 전년도 추가할당량 - 전년도 할당취소량) + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량
- 당해년도 인증배출량은 연초 과부족량 계산에 넣지 않습니다.
- 표의 사전할당 첫 줄에는 원자료 값을 유지하고, 바로 아래 괄호에 반영 사전할당량을 표시합니다.
- 연초 과부족량은 유상여부 오른쪽의 강조 열에 표시합니다.
- 2015년은 2014년 자료가 조회범위에 없으므로 반영 사전할당량과 연초 과부족량을 `null`로 처리합니다.

## 지정 계산그룹

사용자가 별도 지정한 다음 두 법인은 화면 계산 단계에서 하나의 업체로 합칩니다.
원본 JSON과 원자료 ZIP은 수정하지 않고, 화면에 전달되는 연도별 행만 통합합니다.

- `포스코홀딩스 주식회사`
- `주식회사 포스코`

법인표기 변형인 `포스코홀딩스(주)`, `(주)포스코홀딩스`, `(주)포스코`,
`포스코(주)`도 같은 계산그룹에 포함합니다. 각 연도의 사전할당·추가할당·
할당취소·인증배출량·이월량·차입량 등을 먼저 합산한 뒤 위 연초 과부족 산식을
적용합니다. 일반 업체에는 이 예외 규칙을 확대하지 않습니다.

## 연도별 히스토리 검색

업체현황 표 아래의 두 번째 업체 검색에서 업체명을 입력하면 다음 항목을
2015~2025년 연도순으로 확인할 수 있습니다.

- 업체명, 부문, 업종
- 사전할당 원자료와 전년도 조정 반영 사전할당
- 전년도 추가할당, 할당취소, 이월량, 차입량, 인증배출량
- 연초 과부족량

포스코홀딩스 또는 포스코의 법인명을 입력하면 위 지정 계산그룹의 통합
히스토리를 표시합니다.

추가할당·취소·이월·차입·상쇄발행 공개 목록에 업체가 없으면 0입니다.
사전할당도 해당 계획기간 목록에 행이 없으면 0입니다. 원문 행은 있으나 수량
셀이 공란이면 `null`입니다. 인증배출량은 연간 핵심 명부이므로 업체·연도 행이
없거나 셀이 공란이면 `null`입니다. 파생지표는 필수 입력 중 하나라도
`null`이면 `null`입니다. `missingReason`은 목록 부재를 0으로 처리한 경우와
진짜 원문 공란을 구분합니다. 실제 JSON에서는
`absent-zero`(목록 부재를 0 처리), `absent-null`(인증명부 부재),
`blank`(원문 셀 공란), `dependency:...`(파생지표 필수값 결측) 코드를
사용합니다.

## 정규화 원칙

- 원문 업체명·부문·업종과 출처 파일을 보존합니다.
- `(주)`/`주식회사`, `(유)`/`유한회사`, `(합)`/`합자회사`처럼 core가 완전히
  같고 법인표기만 다른 경우에 한해 같은 `companyId`로 묶습니다.
- `유한책임회사`는 `유한회사`와 다른 법인형태로 봅니다.
- 공식 근거가 확인된 합병·분할은 `entity-relations.json`에서 승계 관계로
  연결하지만 서로 다른 법인의 수량은 합치지 않습니다.
- 공식 근거가 없는 인수·유사 이름은 자동 연결하지 않습니다.
- 포스코홀딩스와 포스코의 화면 계산 통합은 위 일반 정규화 원칙과 분리한 사용자 지정 예외입니다.
- 동일 업체·연도·지표의 복수 원문행은 삭제하지 않고 합산합니다. 그중 한 셀이라도
  공란이면 불완전 합계로 보고 지표를 `null`로 둡니다.
- 1차 사전할당 CSV의 `부문`은 실제로 구 업종군입니다. 인증배출량 등 더 정확한
  분류가 없으면 구 업종군을 전환·산업·건물·수송·폐기물·공공·기타의 넓은
  부문까지만 연결하고, 근거가 없는 세부 업종은 임의로 추정하지 않습니다.
