# ETS 전력·에너지 API 원문 보관소

이 디렉터리는 `scripts/sync_ets_energy.py`가 내려받은 공공 API 응답 원문을 날짜별로 보관합니다.

- 기존 `energy-market-agent`의 코드·캐시·데이터를 사용하지 않습니다.
- API 인증키와 인증키가 포함된 요청 URL은 저장하지 않습니다.
- 동일 수집원·기준일을 다시 실행하면 해당 날짜 원문을 최신 응답으로 교체합니다.
- 각 원문 옆의 `.meta.json`에는 SHA-256, 수집시각, 응답 형식, 비밀정보가 없는 엔드포인트 식별자만 기록합니다.
- 대시보드가 읽는 정규화 자료는 `public/data/fundamentals/power-energy/raw/`에 별도로 생성됩니다.
