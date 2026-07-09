# 미국 주식 자산관리 + ETF 리밸런싱 Streamlit 앱

## 파일 구성

- `us_stock_asset_manager_app.py`: Streamlit 메인 앱입니다. GitHub 저장소에는 `app.py`로 올려도 됩니다.
- `requirements_us_stock_asset_manager.txt`: Streamlit Cloud 배포용 패키지 목록입니다. GitHub 저장소에는 `requirements.txt`로 올리는 것을 추천합니다.
- `secrets_example.toml`: Streamlit Secrets 입력 예시입니다. GitHub에는 실제 secrets 파일을 올리지 마세요.

## 저장 구조

Google Sheets를 클라우드 DB처럼 사용합니다.

- `cash`: 현금 잔고 저장. 마지막 행을 현재 현금으로 사용합니다.
- `trades`: 매매일지 저장. `BUY`, `SELL`, `ADJUST`로 보유수량을 계산합니다.

## 배포 순서

1. Google Cloud에서 서비스 계정을 만들고 JSON Key를 발급합니다.
2. 빈 Google Sheet를 만들고 서비스 계정의 `client_email`을 편집자로 공유합니다.
3. GitHub 저장소에 앱 파일과 requirements 파일을 올립니다.
4. Streamlit Community Cloud에서 앱을 배포합니다.
5. Streamlit 앱 설정의 Secrets 메뉴에 `secrets_example.toml` 형식으로 API Key와 서비스 계정 정보를 입력합니다.

## 주의사항

- Alpha Vantage 무료 API는 호출 제한이 있으므로 티커 수가 많으면 캐시를 활용하고, 반복 실행을 줄이는 것이 좋습니다.
- 매매일지는 실제 주문을 실행하지 않고 리밸런싱 참고용 수량을 계산합니다.
- 증권사 자동매매 기능은 포함하지 않았습니다.
