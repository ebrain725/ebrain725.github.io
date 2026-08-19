# KAU Market Briefing V9.2.1 연동 구조

기존 브리핑과 대시보드는 같은 KRX Open API 규격을 사용하지만 실행은 분리합니다.

## 기존 브리핑에서 그대로 유지하는 것

- 저장소: `ebrain725/energy-market-agent`
- KRX 서비스: `배출권 시장 일별매매정보`
- API ID: `ets_bydd_trd`
- 인증 헤더: `AUTH_KEY`
- GitHub Secret: `KRX_AUTH_KEY`
- 외부 스케줄러 호출: 08:10 KST
- Telegram 절대마감: 08:20 KST
- `main.py`, `prompt.txt`, `daily-energy.yml`: 이번 대시보드 시세 자동화 때문에 수정할 필요 없음

## 대시보드에서 추가되는 것

- 저장소: `ebrain725/ebrain725.github.io`
- KRX 자동수집: 매일 08:37 KST
- 정책·뉴스 자동수집: 08:17, 12:17, 18:17 KST
- 공개 시세파일: `public/data/prices.csv`
- 공개 정책파일: `public/data/policies.json`

## 왜 실행을 분리하나

V9.2.1은 08:20 이전 Telegram 발송이 최우선입니다. 대시보드 파일 저장과 Pages 재배포까지 같은 workflow에 넣으면 실행시간이 늘고, 파일 저장 오류가 Telegram 발송에 영향을 줄 수 있습니다.

따라서 다음 순서로 분리합니다.

1. 08:10 — 기존 V9.2.1 브리핑 실행
2. 08:20 — Telegram 발송마감
3. 08:37 — 대시보드가 KRX 시세를 별도로 수집
4. 수집 완료 — `prices.csv` 갱신 및 Pages 재배포

두 저장소에는 같은 실제 KRX 인증키를 `KRX_AUTH_KEY`라는 이름으로 각각 등록합니다. GitHub Secret은 저장소 단위이므로 기존 저장소에 값이 있어도 새 대시보드 저장소에 다시 등록해야 합니다.

## KRX 필드 연결

| 대시보드 | KRX 응답 필드 |
|---|---|
| 종목명 | `ISU_NM` |
| 종가 | `TDD_CLSPRC` |
| 전일대비 | `CMPPREVDD_PRC` 또는 직전 저장 종가로 계산 |
| 등락률 | `FLUC_RT` 또는 직전 저장 종가로 계산 |
| 시가 | `TDD_OPNPRC` |
| 고가 | `TDD_HGPRC` |
| 저가 | `TDD_LWPRC` |
| 거래량 | `ACC_TRDVOL` |
| 거래대금 | `ACC_TRDVAL` |
