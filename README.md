# ETS SIGNAL — GitHub Pages 대시보드

국내 배출권 가격, 기후에너지환경부 정책자료, 배출권시장 뉴스, 텔레그램 데일리 브리핑을 한 화면에서 보여주는 자동갱신형 대시보드입니다.

## 구성

- `public/`: 인터넷에 공개되는 대시보드와 데이터
- `tools/admin.html`: PC에서만 여는 관리자 도구
- `config/settings.json`: 정책 검색 키워드와 RSS 주소
- `config/ets_energy_sources.json`: 배출권 기초데이터 중 전력·에너지 수집원 설정
- `scripts/sync_krx.py`: KRX 배출권 시세 일별 자동 누적
- `scripts/build_krx_annual.py`: 2015~2025 KRX 업체현황 연간자료 정규화
- `scripts/sync_policies.py`: 기후부 공식자료와 배출권시장 뉴스 자동수집
- `scripts/sync_ets_energy.py`: `energy-market-agent`와 분리된 전력·에너지 원천데이터 수집
- `.github/workflows/`: 자동수집 및 Pages 배포 설정
- `integrations/publish_to_dashboard.py`: 텔레그램 브리핑 게시 연동기

## 공개 데이터 주소

- 대시보드: `https://ebrain725.github.io`
- 배출권 시세: `https://ebrain725.github.io/data/prices.csv`
- KRX 일일 매매현황: `https://ebrain725.github.io/krx-trends.html`
- KRX 업체현황(연간): `https://ebrain725.github.io/krx-annual.html`
- KRX 업체현황 공개 데이터: `https://ebrain725.github.io/data/krx-annual.json`
- 정책자료: `https://ebrain725.github.io/data/policies.json`
- 데일리 브리핑: `https://ebrain725.github.io/data/briefing.json`
- 전력·에너지 원천데이터 인덱스: `https://ebrain725.github.io/data/fundamentals/power-energy/raw/index.json`
- 전력·에너지 최신값: `https://ebrain725.github.io/data/fundamentals/power-energy/raw/latest.json`

## 자동 실행시간

- KRX 시세: 매일 08:37 KST
- 정책·뉴스: 매일 08:17, 12:17, 15:17, 18:17 KST
- 전력·에너지 원천데이터: 매일 08:27, 12:27, 16:27, 20:27, 23:47 KST

전력·에너지 수집기의 구조와 Secret 설정은 `docs/ETS_ENERGY_DATA_PIPELINE.md`를 참고합니다.
