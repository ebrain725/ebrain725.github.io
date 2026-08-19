# ETS SIGNAL — GitHub Pages 무료 대시보드

국내 배출권 가격, 기후에너지환경부 정책자료, 배출권시장 뉴스, 텔레그램 데일리 브리핑을 한 화면에서 보여주는 자동갱신형 대시보드입니다.

## 가장 먼저 할 일

1. GitHub에서 공개 저장소 `ebrain725.github.io`를 만듭니다.
2. 이 폴더 **안의 파일과 폴더 전체**를 저장소 최상위에 업로드합니다.
3. 저장소 `Settings → Pages → Source`에서 **GitHub Actions**를 선택합니다.
4. `Actions → Deploy GitHub Pages → Run workflow`를 실행합니다.
5. 초록색 체크가 표시되면 `https://ebrain725.github.io`를 엽니다.
6. 기존 브리핑 프로젝트에서 사용하는 KRX 인증키를 이 저장소에도 `KRX_AUTH_KEY` Secret으로 등록합니다.

처음 하신다면 [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md)를 위에서부터 순서대로 따라가세요.

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
- 정책·뉴스: 매일 08:17, 12:17, 18:17 KST

기존 `ebrain725/energy-market-agent`의 V9.2.1 브리핑은 08:10~08:20 구조를 그대로 유지합니다. 대시보드는 별도 실행되므로 Telegram 발송마감에 영향을 주지 않습니다. 자세한 내용은 [V9_2_1_INTEGRATION.md](V9_2_1_INTEGRATION.md)를 참고하세요.

## 보안 원칙

이 저장소는 공개됩니다. Telegram Bot Token, OpenAI API Key, GitHub Token 같은 비밀값을 파일이나 JavaScript에 입력하지 마세요. 비밀값은 반드시 GitHub의 `Settings → Secrets and variables → Actions`에만 저장합니다.
