# 배출권 기초데이터 — 전력·에너지 원천데이터 수집기 v0.1

## 1. 목적과 분리 원칙

배출권 대시보드의 신규 데이터 영역 중 **① 전력·에너지** 원천데이터를 담당합니다.
기존 `energy-market-agent`의 코드, 캐시, 프롬프트, 텔레그램 전송 로직은 사용하지 않습니다.
대시보드 저장소 안에 별도 수집 모듈, 설정, 시험, GitHub Actions 워크플로를 둡니다.

데이터 계층은 처음부터 다음과 같이 분리합니다.

- API 원문: `source-data/ets-energy/raw/`
- 대시보드용 원천데이터: `public/data/fundamentals/power-energy/raw/`
- 향후 파생데이터: `public/data/fundamentals/power-energy/derived/`

원천값과 계산값은 같은 파일에 섞지 않습니다.

## 2. v0.1 활성 수집원

| ID | 기관 | 원천데이터 | 시간 단위 | 상태 |
|---|---|---|---|---|
| `kpx_supply_today_5m` | 한국전력거래소 | 당일 공급능력, 현재수요, 최대예측수요, 공급·운영예비력과 예비율 | 5분 | 활성 |

- 공공데이터포털 데이터셋: `한국전력거래소_오늘전력수급현황조회_GW`
- 데이터셋 ID: `15158703`
- 신규 GW 방식 전환일: 2026-06-30
- API 형식: REST/XML
- 개발계정 기본 호출량: 100회/일

`GW`는 공공데이터포털의 연계 방식 명칭이며 전력 값의 단위가 아닙니다. 공급능력·수요·예비력은 **MW**, 예비율은 **%**로 저장합니다.

### 정규화 필드

| field | 표시명 | 단위 |
|---|---|---|
| `observed_at` | 기준일시 | KST ISO 8601 |
| `supply_capacity` | 공급능력 | MW |
| `system_demand` | 현재수요 | MW |
| `forecast_peak_demand` | 최대예측수요 | MW |
| `supply_reserve` | 공급예비력 | MW |
| `supply_reserve_rate` | 공급예비율 | % |
| `operating_reserve` | 운영예비력 | MW |
| `operating_reserve_rate` | 운영예비율 | % |

## 3. 저장 구조

```text
config/
  ets_energy_sources.json
scripts/
  sync_ets_energy.py
  ets_fundamentals/
    __init__.py
    core.py
    models.py
    io_utils.py
    normalization.py
    publication.py
    kpx_parser.py
    kpx_supply.py
tests/
  fixtures/
    kpx_supply_today_5m.json
    kpx_supply_today_5m.xml
  test_ets_energy_collector.py
source-data/ets-energy/raw/
  kpx_supply_today_5m/YYYY/MM/YYYY-MM-DD.xml
  kpx_supply_today_5m/YYYY/MM/YYYY-MM-DD.xml.meta.json
public/data/fundamentals/power-energy/raw/
  index.json
  latest.json
  kpx_supply_today_5m/daily/YYYY/YYYY-MM-DD.json
  kpx_supply_today_5m/daily/YYYY/YYYY-MM-DD.csv
.github/workflows/
  sync-ets-energy.yml
```

수집원별 날짜 파일은 표 형태의 wide format입니다. 지표명과 단위는 각 JSON의 `source.metrics`에 한 번만 기록하여 장기간 누적 시 파일 크기가 불필요하게 커지지 않도록 했습니다.

## 4. 중복·누락 방지

- 한 행의 키는 `observed_at`입니다.
- 같은 날짜를 다시 수집하면 새 시각을 추가하고 같은 시각은 최신 값으로 갱신합니다.
- 늦은 실행에서 일부 행만 응답해도 기존의 더 많은 행을 삭제하지 않습니다.
- `coverage.record_count`, `expected_records_per_day=288`, `completion_ratio`, 첫·마지막 시각을 기록합니다.
- 원문과 공개 JSON·CSV에 SHA-256을 기록하고 워크플로 마지막에 다시 검증합니다.
- 응답일이 여러 개인 경우 가장 최근 날짜만 정규화하고 나머지는 경고로 남깁니다.

## 5. 자동 실행

워크플로: `.github/workflows/sync-ets-energy.yml`

한국시간 기준 매일 다음 시각에 당일 5분 자료를 누적 갱신합니다.

- 08:27
- 12:27
- 16:27
- 20:27
- 23:47

당일 API는 과거 기준일 조회 기능이 없으므로 한 번만 밤에 실행하면 GitHub 예약 지연으로 날짜가 넘어갈 위험이 있습니다. 여러 차례 같은 날짜 파일을 누적 갱신하고, 23:47 실행에서 약 99% 수준까지 채우는 구조로 잡았습니다. 향후 외부 정시 스케줄러 또는 KPX 과거 파일을 연결하면 익일 완전성 보정 단계도 추가할 수 있습니다.

실행 순서:

1. JSON·XML 파서와 중복 병합 단위시험
2. 공공데이터 API 호출
3. 응답 원문과 원문 메타데이터 저장
4. 수집원별 날짜 JSON·CSV 갱신
5. 전체 인덱스와 최신값 파일 재생성
6. 파일 존재·행 수·SHA-256 검증
7. 변경 파일만 현재 브랜치에 커밋·푸시
8. 기존 Pages 워크플로가 `public/**` 변경을 감지해 배포

## 6. 비밀정보 처리

워크플로와 데이터 파일에는 인증키를 출력하거나 저장하는 코드가 없습니다.

- Secret 이름: `KPX_OPENAPI_SERVICE_KEY`
- 저장하지 않는 항목: 서비스키, 서비스키가 포함된 전체 요청 URL
- 저장하는 항목: 비밀정보가 없는 엔드포인트 ID, 응답 형식, 수집시각, 행 수, SHA-256

## 7. 최초 설정

### 공공데이터포털 활용신청

1. 공공데이터포털에서 `한국전력거래소_오늘전력수급현황조회_GW`를 검색합니다.
2. **활용신청**을 선택합니다.
3. 사용할 프로젝트 서비스키 또는 개인 서비스키를 선택합니다.
4. 개발계정의 승인 상태와 서비스키 발급을 확인합니다.

### GitHub Secret 등록

1. `ebrain725/ebrain725.github.io` 저장소에서 **Settings**를 엽니다.
2. **Secrets and variables → Actions**로 이동합니다.
3. **New repository secret**을 선택합니다.
4. 이름을 `KPX_OPENAPI_SERVICE_KEY`로 입력합니다.
5. 공공데이터포털 서비스키를 값으로 저장합니다.

Secret이 없으면 예약 워크플로는 단위시험만 수행하고 실수집은 생략합니다.

## 8. 로컬 시험

```bash
python -m unittest discover -s tests -p "test_ets_energy_*.py" -v
python scripts/sync_ets_energy.py \
  --fixture tests/fixtures/kpx_supply_today_5m.xml \
  --expected-date 2026-09-02 \
  --public-root /tmp/ets-energy-public \
  --archive-root /tmp/ets-energy-archive
python scripts/sync_ets_energy.py \
  --public-root /tmp/ets-energy-public \
  --validate-only
```

실 API 실행은 Secret과 동일한 환경변수를 설정한 뒤 수행합니다.

```bash
export KPX_OPENAPI_SERVICE_KEY="발급받은_서비스키"
python scripts/sync_ets_energy.py
python scripts/sync_ets_energy.py --validate-only
```

## 9. 현 단계의 검증 범위

fixture 기반 JSON·XML 파싱과 파일 생성·병합·무결성 검증은 자동시험으로 확인합니다. 실제 공공데이터 응답 호출은 저장소 Secret 등록과 해당 API 활용승인 후 GitHub Actions에서 처음 검증됩니다. 실응답 필드가 공식 명세와 다르면 원문은 그대로 보존되고 정규화 단계가 실패하므로 잘못된 값을 조용히 누적하지 않습니다.

## 10. 다음 연결 순서

1. 전력수급예보
2. 시간별 SMP
3. 전국 연료원별 발전량·거래량과 재생에너지 발전량
4. LNG·원유·석탄 가격과 환율
5. 대시보드 원천데이터 탭
6. 일별 파생지표 탭

파생 탭의 첫 지표는 원천 5분 자료를 이용한 일 최대수요, 평균수요, 최저 예비율, 최대·최저 예비력, 완전성 점수로 구성할 수 있습니다.
