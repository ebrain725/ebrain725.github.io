# ETS SIGNAL 설치·운영 매뉴얼

이 매뉴얼은 GitHub를 처음 사용하는 사람을 기준으로 작성했습니다. 명령어를 입력할 필요 없이 대부분 웹 화면에서 진행할 수 있습니다.

---

## 0. 완성되면 무엇이 만들어지나

| 구분 | 결과 |
|---|---|
| 공개 대시보드 | `https://ebrain725.github.io` |
| 시세 관리 | 최초 전기간 CSV 등록 후 KRX API가 매일 08:37 자동 갱신 |
| 정책자료 | 기후부 공식 RSS와 배출권 뉴스를 매일 08:17·12:17·18:17 확인 |
| 정책 키워드 | 관리자 도구에서 직접 변경 |
| 브리핑 | 텔레그램 발송 결과를 JSON으로 함께 게시 |
| 운영비 | 공개 저장소와 GitHub Pages 기준 0원 |

중요: GitHub Pages는 공개 사이트입니다. `public` 폴더에 저장한 데이터는 누구나 볼 수 있습니다.

---

# 1단계. GitHub Pages 저장소 만들기

## 1-1. GitHub 로그인

1. 브라우저에서 `https://github.com`을 엽니다.
2. 기존 GitHub 계정으로 로그인합니다.
3. 오른쪽 위 프로필 사진 옆의 `+` 버튼을 누릅니다.
4. `New repository`를 선택합니다.

## 1-2. 저장소 이름 입력

다음과 같이 설정합니다.

| 입력 항목 | 입력값 |
|---|---|
| Owner | `ebrain725` |
| Repository name | `ebrain725.github.io` |
| Description | `배출권 시장·정책 대시보드` |
| 공개 범위 | `Public` |

`Add a README file`, `.gitignore`, `License`는 선택하지 않아도 됩니다. 마지막에 `Create repository`를 누릅니다.

저장소 이름은 반드시 `ebrain725.github.io`와 정확히 같아야 기본 주소가 `https://ebrain725.github.io`가 됩니다.

---

# 2단계. 대시보드 파일 업로드

## 2-1. 압축파일 풀기

1. 전달받은 `ets-signal-github-pages.zip`을 PC에 저장합니다.
2. 파일에서 마우스 오른쪽 버튼을 누릅니다.
3. Windows의 `압축 풀기` 또는 `모두 추출`을 선택합니다.
4. 압축을 푼 폴더를 엽니다.

폴더 안에 다음 항목이 보여야 합니다.

```text
.github
config
examples
integrations
public
scripts
tools
BEGINNER_GUIDE.md
README.md
```

## 2-2. GitHub에 파일 올리기

1. 방금 만든 `ebrain725.github.io` 저장소 화면으로 이동합니다.
2. `uploading an existing file` 링크를 누릅니다. 링크가 없다면 `Add file → Upload files`를 선택합니다.
3. 압축을 푼 폴더 **안의 항목 전체**를 선택해 업로드 영역으로 끌어다 놓습니다.
4. 업로드 목록에 `.github/workflows`, `public/index.html` 등이 보이는지 확인합니다.
5. 화면 아래 `Commit changes` 버튼을 누릅니다.

주의: `ets-signal-github-pages` 폴더 자체가 한 단계 더 들어가면 안 됩니다. 저장소 첫 화면에 `public`, `config`, `.github`가 바로 보여야 합니다.

---

# 3단계. GitHub Pages 켜기

## 3-1. Pages 배포방식 선택

1. 저장소 상단의 `Settings`를 누릅니다.
2. 왼쪽 메뉴에서 `Pages`를 누릅니다.
3. `Build and deployment` 영역을 찾습니다.
4. `Source`를 `GitHub Actions`로 선택합니다.

## 3-2. 자동수집 쓰기 권한 허용

1. 같은 `Settings` 화면의 왼쪽 메뉴에서 `Actions → General`을 누릅니다.
2. 화면 아래 `Workflow permissions`로 이동합니다.
3. `Read and write permissions`를 선택합니다.
4. `Save`를 누릅니다.

이 권한은 자동수집기가 `public/data/policies.json`을 갱신할 때 필요합니다.

## 3-3. 첫 배포 실행

1. 저장소 상단의 `Actions`를 누릅니다.
2. 왼쪽에서 `Deploy GitHub Pages`를 선택합니다.
3. 오른쪽의 `Run workflow`를 누릅니다.
4. 초록색 `Run workflow` 버튼을 한 번 더 누릅니다.
5. 실행 항목이 노란색에서 초록색 체크로 바뀔 때까지 기다립니다.
6. `https://ebrain725.github.io`를 엽니다.

처음 배포는 몇 분 정도 걸릴 수 있습니다. 404가 보이면 2~3분 후 `Ctrl + F5`로 새로고침합니다.

---

# 4단계. 전기간 시세 등록

공개 대시보드에는 시세 입력 버튼이 없습니다. 관리 작업은 PC에 내려받은 `tools/admin.html`에서만 합니다.

## 4-1. 관리자 도구 열기

1. 압축을 푼 폴더에서 `tools` 폴더를 엽니다.
2. `admin.html`을 더블클릭합니다.
3. 브라우저에 `ETS SIGNAL 관리자 도구`가 열립니다.
4. `01 전기간 시세`가 선택되어 있는지 확인합니다.

인터넷 주소로 열리는 화면이 아니라 PC의 파일로 열리는 것이 정상입니다.

## 4-2. 엑셀 자료 붙여넣기

엑셀 또는 KRX 자료를 다음 열 순서로 준비합니다.

| 열 | 내용 | 예시 |
|---|---|---|
| 1 | 거래일 | `2026-08-19` |
| 2 | 종목명 | `KAU25` |
| 3 | 현재가 또는 종가 | `29,500` |
| 4 | 대비 | `1,150` |
| 5 | 등락률(%) | `4.06` |
| 6 | 시가 | `29,000` |
| 7 | 고가 | `29,500` |
| 8 | 저가 | `29,000` |
| 9 | 거래량(톤) | `396,644` |
| 10 | 거래대금(원) | `11,624,058,550` |

1. 엑셀에서 제목행을 포함한 전기간 표를 선택합니다.
2. `Ctrl + C`로 복사합니다.
3. 관리자 도구의 큰 입력창을 클릭합니다.
4. `Ctrl + V`로 붙여넣습니다.
5. `붙여넣기 검증`을 누릅니다.
6. 거래일 개수와 미리보기 내용을 확인합니다.
7. `prices.csv 다운로드`를 누릅니다.

날짜는 `2026-08-19`, `2026.08.19`, `20260819` 형식을 지원합니다. 같은 날짜·같은 종목이 두 번 있으면 마지막 행을 사용합니다.

## 4-3. prices.csv를 GitHub에 올리기

1. GitHub의 `ebrain725.github.io` 저장소를 엽니다.
2. `public` 폴더를 누릅니다.
3. `data` 폴더를 누릅니다.
4. `Add file → Upload files`를 누릅니다.
5. 방금 다운로드한 `prices.csv`를 끌어다 놓습니다.
6. `Commit changes`를 누릅니다.
7. `Actions`에서 `Deploy GitHub Pages`가 자동 실행되는지 확인합니다.
8. 초록색 체크가 표시된 후 대시보드를 새로고침합니다.

전기간 CSV 등록은 최초 한 번만 하면 됩니다. 이후에는 KRX Open API가 매일 최신 거래일을 자동으로 추가합니다. 관리자 도구는 과거자료 보정이나 전체 데이터 교체가 필요할 때만 사용합니다.

## 4-4. KRX 인증키 등록

기존 `ebrain725/energy-market-agent`에서 사용하는 KRX Open API 인증키를 동일하게 사용합니다. Secret 이름도 기존과 같은 `KRX_AUTH_KEY`입니다.

1. `ebrain725.github.io` 저장소의 `Settings`를 누릅니다.
2. 왼쪽에서 `Secrets and variables → Actions`를 누릅니다.
3. `New repository secret`을 누릅니다.
4. Name에 `KRX_AUTH_KEY`를 입력합니다.
5. Secret에 KRX에서 발급받은 실제 인증키를 붙여넣습니다.
6. `Add secret`을 누릅니다.

GitHub는 기존 저장소에 등록된 Secret 값을 다시 보여주지 않습니다. `energy-market-agent` 화면에서 값을 복사하는 방식이 아니라, 처음 KRX에서 발급받아 보관 중인 인증키를 다시 입력해야 합니다.

종목을 하나로 고정하고 싶다면 `Settings → Secrets and variables → Actions → Variables → New repository variable`에서 다음과 같이 설정할 수 있습니다.

| 항목 | 값 |
|---|---|
| Name | `KAU_SYMBOL` |
| Value | 예: `KAU25` |

`KAU_SYMBOL`을 만들지 않으면 KRX에서 거래된 KAU 종목을 모두 저장하고, 대시보드에서 종목을 선택할 수 있습니다.

## 4-5. KRX 자동수집 즉시 시험

1. 저장소 상단의 `Actions`를 누릅니다.
2. 왼쪽에서 `Sync KRX Market Data`를 선택합니다.
3. `Run workflow`를 누릅니다.
4. `target_date`는 비워둡니다.
5. 초록색 `Run workflow`를 누릅니다.
6. 실행 결과가 초록색 체크인지 확인합니다.
7. `public/data/prices.csv`에 최신 거래일이 추가됐는지 확인합니다.
8. 대시보드에서 현재가와 거래량을 확인합니다.

정상 작동하면 매일 한국시간 08:37에 자동 실행됩니다. 주말과 휴일에는 최근 거래일을 확인하고 같은 데이터는 중복 저장하지 않습니다.

---

# 5단계. 정책 키워드 설정과 자동수집

## 5-1. 키워드 파일 만들기

1. PC에서 `tools/admin.html`을 엽니다.
2. `02 정책 키워드`를 누릅니다.
3. 키워드를 한 줄에 하나씩 입력합니다.
4. 수집할 자료인 `기후부 보도자료`, `기후부 공지·공고`, `배출권시장 뉴스`를 선택합니다.
5. `settings.json 다운로드`를 누릅니다.

초기 키워드는 다음과 같습니다.

```text
배출권
배출권거래제
유상할당
유상경매
탄소시장
시장안정화
상쇄배출권
```

## 5-2. 설정파일 교체

1. GitHub 저장소의 `config` 폴더를 엽니다.
2. `Add file → Upload files`를 누릅니다.
3. 새로 받은 `settings.json`을 올립니다.
4. `Commit changes`를 누릅니다.

## 5-3. 정책수집 즉시 시험

1. 저장소 상단의 `Actions`를 누릅니다.
2. 왼쪽에서 `Sync Ministry Policies`를 누릅니다.
3. `Run workflow → Run workflow`를 누릅니다.
4. 초록색 체크가 표시되는지 확인합니다.
5. 저장소의 `public/data/policies.json`에 자료가 들어왔는지 확인합니다.
6. 대시보드의 `정책 레이더`를 확인합니다.

정상 작동하면 매일 한국시간 08:17, 12:17, 18:17에 기후부 공식 RSS와 Google News의 배출권시장 관련자료를 자동 확인합니다. GitHub 서버 상황에 따라 몇 분 지연될 수 있습니다.

---

# 6단계. 브리핑을 수동으로 먼저 시험

자동연결 전에 한 번 수동으로 올려보는 것이 좋습니다.

1. PC에서 `tools/admin.html`을 엽니다.
2. `03 데일리 브리핑`을 누릅니다.
3. 이전 브리핑을 유지하려면 GitHub의 `public/data/briefing.json`을 내려받아 `기존 briefing.json 불러오기`에서 선택합니다.
4. 날짜, 제목, 시장 방향, 핵심 요약, 상세 브리핑, 1주 전망을 입력합니다.
5. `briefing.json 다운로드`를 누릅니다.
6. GitHub의 `public/data` 폴더에 새 `briefing.json`을 업로드합니다.
7. `Commit changes`를 누릅니다.
8. 배포가 끝나면 대시보드의 `데일리 브리핑` 영역을 확인합니다.

---

# 7단계. 기존 텔레그램 브리핑 자동연결

현재 텔레그램 프로그램은 `eBrain725/energy-market-agent`에 있고, 대시보드는 별도 저장소에 있습니다. 한 저장소에서 다른 저장소의 JSON을 바꾸려면 대시보드 저장소만 수정할 수 있는 전용 토큰이 필요합니다.

## 7-1. 대시보드 전용 GitHub Token 만들기

1. GitHub 오른쪽 위 프로필 사진을 누릅니다.
2. `Settings`를 누릅니다.
3. 왼쪽 메뉴 맨 아래 `Developer settings`를 누릅니다.
4. `Personal access tokens → Fine-grained tokens`를 누릅니다.
5. `Generate new token`을 누릅니다.
6. Token name에 `ETS dashboard publisher`를 입력합니다.
7. Expiration은 회사 정책에 맞는 기간을 선택합니다.
8. Repository access에서 `Only select repositories`를 선택합니다.
9. 저장소는 `ebrain725.github.io` 하나만 선택합니다.
10. Permissions의 `Repository permissions`에서 `Contents`를 `Read and write`로 지정합니다.
11. `Generate token`을 누릅니다.
12. 화면에 표시된 토큰을 복사합니다. 이 값은 다시 표시되지 않습니다.

토큰을 메모장, 소스코드, 카카오톡 등에 저장하지 마세요.

## 7-2. 토큰을 기존 브리핑 저장소에 보관

1. GitHub에서 `energy-market-agent` 저장소를 엽니다.
2. `Settings → Secrets and variables → Actions`로 이동합니다.
3. `New repository secret`을 누릅니다.
4. Name에 `PAGES_REPO_TOKEN`을 입력합니다.
5. Secret에 방금 복사한 토큰을 붙여넣습니다.
6. `Add secret`을 누릅니다.

## 7-3. 연동 파일 추가

이 패키지의 `integrations/publish_to_dashboard.py`를 기존 `energy-market-agent` 저장소에 올립니다. GitHub에서 `Add file → Upload files`로 업로드하면 됩니다.

## 7-4. 텔레그램 발송 코드에 연결

텔레그램 전송이 성공한 직후 다음 형식으로 호출합니다.

```python
from publish_to_dashboard import publish_briefing

publish_briefing({
    "date": "2026-08-19",
    "title": "배출권 데일리 브리핑",
    "summary": "오늘 시장의 핵심 내용을 두세 문장으로 입력",
    "content": telegram_message,
    "marketTone": "강세",
    "outlook": "향후 1주 전망과 확인할 변수",
    "source": "Telegram"
})
```

실제 코드에서는 날짜와 분석결과 변수를 연결합니다. 텔레그램 발송이 실패했을 때 대시보드만 갱신되지 않도록 **텔레그램 전송 성공 이후**에 실행하는 것이 좋습니다.

GitHub Actions 실행 단계에는 다음 환경변수가 전달되어야 합니다.

```yaml
env:
  PAGES_REPO_TOKEN: ${{ secrets.PAGES_REPO_TOKEN }}
  DASHBOARD_REPO: ebrain725/ebrain725.github.io
```

기존 코드가 최종 브리핑을 JSON 파일로 저장한다면 명령줄로도 실행할 수 있습니다.

```yaml
- name: 대시보드 브리핑 게시
  env:
    PAGES_REPO_TOKEN: ${{ secrets.PAGES_REPO_TOKEN }}
    DASHBOARD_REPO: ebrain725/ebrain725.github.io
  run: python publish_to_dashboard.py briefing_payload.json
```

연결 후 `energy-market-agent`의 `Actions`에서 수동 실행해 먼저 시험합니다.

---

# 8단계. 데이터 API 주소

별도의 ID는 필요하지 않습니다. 다음 주소를 다른 프로그램에서도 읽을 수 있습니다.

| 데이터 | 주소 |
|---|---|
| 배출권 시세 | `https://ebrain725.github.io/data/prices.csv` |
| 정책자료 | `https://ebrain725.github.io/data/policies.json` |
| 데일리 브리핑 | `https://ebrain725.github.io/data/briefing.json` |

이 주소는 읽기 전용 공개 주소입니다. 여기에 POST 요청을 보내 데이터를 수정할 수는 없습니다. 수정은 GitHub 저장소의 파일 교체 또는 제공된 게시 연동기를 통해 진행합니다.

---

# 9단계. `.com` 도메인 연결 — 선택사항

도메인을 구매한 뒤 다음과 같이 연결합니다.

1. `ebrain725.github.io` 저장소의 `Settings → Pages`로 이동합니다.
2. `Custom domain`에 사용할 주소를 입력합니다.
3. `Save`를 누릅니다.
4. 도메인 구매업체의 DNS 관리화면에 GitHub가 안내하는 값을 등록합니다.
5. 연결이 완료되면 `Enforce HTTPS`를 선택합니다.

도메인 구매비는 별도지만 GitHub Pages 연결 자체에는 추가 호스팅비가 없습니다.

---

# 10단계. 일상 운영 순서

## 매일 시세 갱신

별도 작업이 필요하지 않습니다.

1. 08:37 KST에 `Sync KRX Market Data` 자동 실행
2. KRX 최신 거래일을 `prices.csv`에 추가
3. GitHub Pages 자동 재배포
4. 대시보드 현재가·차트 자동 갱신

과거자료를 수정할 때만 PC의 `tools/admin.html`을 사용합니다.

## 정책 키워드 변경

1. 관리자 도구에서 키워드 변경
2. `settings.json` 다운로드
3. GitHub `config`에 업로드
4. `Sync Ministry Policies` 수동 실행

## 브리핑 자동발송

1. 기존 프로그램이 텔레그램 전송
2. `publish_to_dashboard.py`가 `briefing.json` 갱신
3. GitHub Pages 자동 배포
4. 대시보드에 최신 브리핑 표시

---

# 11단계. 오류가 날 때 확인할 곳

## 사이트에 404가 표시될 때

- 저장소 이름이 정확히 `ebrain725.github.io`인지 확인합니다.
- `Settings → Pages → Source`가 `GitHub Actions`인지 확인합니다.
- `Actions → Deploy GitHub Pages`가 초록색인지 확인합니다.

## 정책수집이 빨간색으로 실패할 때

- `Settings → Actions → General → Workflow permissions`가 `Read and write permissions`인지 확인합니다.
- `config/settings.json`의 쉼표나 큰따옴표가 삭제되지 않았는지 확인합니다.
- `Actions → Sync Ministry Policies → Run workflow`로 다시 실행합니다.

## KRX 시세수집이 빨간색으로 실패할 때

- `Settings → Secrets and variables → Actions`에 `KRX_AUTH_KEY`가 있는지 확인합니다.
- KRX Open API에서 `배출권 시장 일별매매정보` 서비스가 승인 상태인지 확인합니다.
- 종목을 고정했다면 `KAU_SYMBOL` 값이 실제 KRX 종목명과 같은지 확인합니다.
- `Actions → Sync KRX Market Data → Run workflow`에서 수동 재실행합니다.

## 새 시세가 화면에 안 보일 때

- 업로드 위치가 `public/data/prices.csv`인지 확인합니다.
- 파일명이 `prices (1).csv`가 아니라 정확히 `prices.csv`인지 확인합니다.
- Pages 배포가 끝난 뒤 `Ctrl + F5`를 누릅니다.

## 관리자 화면을 인터넷에서 찾을 수 없을 때

정상입니다. 관리자 도구는 공개 배포 대상에서 제외되어 있습니다. PC에서 `tools/admin.html`을 더블클릭해 사용합니다.

## 텔레그램은 전송됐는데 대시보드가 안 바뀔 때

- `energy-market-agent → Actions`의 실행 로그에서 대시보드 게시 단계가 성공했는지 확인합니다.
- Secret 이름이 정확히 `PAGES_REPO_TOKEN`인지 확인합니다.
- Fine-grained token이 `ebrain725.github.io` 저장소의 `Contents: Read and write` 권한을 갖는지 확인합니다.

---

# 12단계. 절대로 공개하면 안 되는 값

다음 값은 `public`, `.js`, `.py`, `.json`, README에 직접 입력하면 안 됩니다.

- Telegram Bot Token
- OpenAI API Key
- GitHub Personal Access Token
- 회사 내부 인증키
- 개인 비밀번호

이 값들은 GitHub의 `Settings → Secrets and variables → Actions`에만 저장합니다.
