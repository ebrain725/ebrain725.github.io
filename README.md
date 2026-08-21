# ETS SIGNAL — GitHub Pages 대시보드

국내 배출권 가격, 기후에너지환경부 정책자료, 배출권시장 뉴스, 텔레그램 데일리 브리핑을 한 화면에서 보여주는 자동갱신형 대시보드입니다.

## 구성

- `public/`: 인터넷에 공개되는 대시보드와 데이터
- `tools/admin.html`: PC에서만 여는 관리자 도구
- `config/settings.json`: 정책 검색 키워드와 RSS 주소
- `scripts/sync_krx.py`: KRX 배출권 시세 일별 자동 누적
- `scripts/sync_policies.py`: 기후부 공식자료와 배출권시장 뉴스 자동수집
- `.github/workflows/`: 자동수집 및 Pages 배포 설정
- `integrations/publish_to_dashboard.py`: 텔레그램 브리핑 게시 연동기

## 공개 데이터 주소

- 대시보드: `https://ebrain725.github.io`
- 배출권 시세: `https://ebrain725.github.io/data/prices.csv`
- 정책자료: `https://ebrain725.github.io/data/policies.json`
- 데일리 브리핑: `https://ebrain725.github.io/data/briefing.json`

## 자동 실행시간

- KRX 시세: 매일 08:37 KST
- 정책·뉴스: 매일 08:17, 12:17, 15:17, 18:17 KST
