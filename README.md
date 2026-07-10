# 미국 주식 자산관리 + ETF 리밸런싱 v13

## 변경 사항

v13에서는 USD/KRW 환율을 Alpha Vantage API로 조회하지 않습니다.
앱은 Google Sheets의 `settings` 시트에 입력된 `usdkrw_rate` 값을 우선 읽고, 값이 없거나 오류가 있으면 사이드바의 수동 예비 환율을 사용합니다.

## GitHub 업로드 파일

아래 파일명을 바꿔서 GitHub 저장소 루트에 올리세요.

```text
us_stock_asset_manager_app_v13.py -> app.py
requirements_us_stock_asset_manager.txt -> requirements.txt
```

## Google Sheets settings 시트 작성법

Google Sheets에 `settings` 시트를 만들고 첫 행을 아래처럼 작성하세요.

| A | B |
|---|---|
| key | value |

그다음 아래 값을 입력하세요.

| A | B |
|---|---|
| usdkrw_rate | =GOOGLEFINANCE("CURRENCY:USDKRW") |
| usdkrw_source | Google Sheets GOOGLEFINANCE |
| usdkrw_rate_date | =TODAY() |

앱은 `key`가 `usdkrw_rate`인 행의 `value` 값을 읽어서 환율로 사용합니다.

## 동작 우선순위

1. `settings` 시트의 `usdkrw_rate` 값 사용
2. 값이 없거나 #N/A 등 오류면 사이드바의 `수동 USD/KRW 예비 환율` 사용
3. Alpha Vantage 환율 API는 호출하지 않음

## 주의

- Google Sheets의 GOOGLEFINANCE 값은 지연될 수 있습니다.
- 실제 환전 체결 환율, 증권사 고시 환율과 다를 수 있습니다.
- 평가용 환율로 사용하는 것을 권장합니다.
