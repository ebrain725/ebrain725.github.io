# 개발·운영 사이트 운영 방법

| 구분 | 저장소 / 브랜치 | 웹 주소 | 배포 |
| --- | --- | --- | --- |
| 개발 | `ebrain725/ets-dashboard-dev` / `develop` | Cloudflare 설정 후 `https://develop.ets-dashboard-dev.pages.dev` | `develop`의 `public/**` 변경 시 Cloudflare Pages 미리보기 |
| 운영 | `ebrain725/ebrain725.github.io` / `main` | `https://ebrain725.github.io` | `main`의 `public/**` 변경 시 GitHub Pages |

개발 사이트는 기존 `cloudflare-dev.yml`과 Cloudflare Access로 로그인 제한됩니다. 사이트가 아직 열리지 않는다면 개발 저장소의 Actions에서 해당 워크플로 실행 결과를 확인하세요. 개발 저장소 Secret `CLOUDFLARE_API_TOKEN`이 없으면 기존 워크플로는 안내만 남기고 배포하지 않습니다. 필요하면 `CLOUDFLARE_ACCESS_EMAIL`도 설정하세요. 토큰을 코드나 이슈에 붙여 넣지 마세요.

## 개발 → 운영 반영

1. 개발 저장소 `develop`에서 작업한 뒤 개발 사이트에서 화면과 데이터 표시를 검증합니다. 개발 데이터는 운영과 별도 스냅샷이며 자동으로 운영에 게시되지 않습니다.
2. 운영 저장소의 **Settings → Secrets and variables → Actions**에 `DEV_REPO_READ_TOKEN`을 등록합니다. Fine-grained PAT의 대상 저장소를 **`ebrain725/ets-dashboard-dev` 하나**로 제한하고 **Contents: Read-only**만 허용합니다. 운영 저장소의 **Settings → Actions → General → Workflow permissions**에서 `GITHUB_TOKEN`의 PR 생성 허용 설정도 확인합니다. 운영 저장소에 대한 쓰기 권한을 PAT에 추가하지 마세요.
3. 운영 저장소 **Actions → Promote DEV UI to production PR → Run workflow**에서 `develop`(또는 개발 사이트에서 검증한 40자리 커밋 SHA)을 선택하고 옮길 파일을 한 줄에 하나씩 입력합니다. 예: `public/index.html`, `public/assets/app.js`. 폴더나 `public/data/`는 지정할 수 없습니다.
4. 생성된 운영 저장소 PR에서 파일별 diff와 운영 전용 스크립트를 확인한 다음 병합합니다. 병합되면 기존 GitHub Pages 배포 워크플로가 실행됩니다. PR 생성만으로는 운영 사이트가 바뀌지 않습니다.

운영 저장소의 데이터 수집 워크플로, `public/data/`, `config/`, `scripts/`는 이 승격 절차에서 건드리지 않습니다. `dev-environment.js` 호출은 HTML 승격 시 제거됩니다. 반대로 개발 HTML이 운영 사이트에 이미 있는 필수 스크립트를 누락하면 승격을 중단하므로, 해당 스크립트와 호출을 개발 화면에 먼저 반영해 검증하세요. 현재 `public/index.html`은 운영의 `policy-motie-extension.js`가 개발에 없어 이 조건에 걸립니다.

데이터 수집 코드나 운영 설정도 변경해야 한다면, 해당 변경을 운영 저장소에서 별도 PR로 검토하세요. 이 절차는 화면 코드에만 적용됩니다.
