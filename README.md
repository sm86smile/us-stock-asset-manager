# 미국 주식 자산관리 + ETF 자산배분 리밸런싱

> 대상 소스 코드: `us_stock_asset_manager_app_v16_min_cash.py`  
> 실행 방식: Python + Streamlit  
> 데이터 저장: Google Sheets  
> 시장 데이터: Alpha Vantage `TIME_SERIES_MONTHLY_ADJUSTED`

---

## 목차

1. [프로그램 개요](#1-프로그램-개요)
2. [핵심 기능 요약](#2-핵심-기능-요약)
3. [중요한 동작 원칙](#3-중요한-동작-원칙)
4. [전체 시스템 구조](#4-전체-시스템-구조)
5. [관리 대상 ETF](#5-관리-대상-etf)
6. [설치 전 준비사항](#6-설치-전-준비사항)
7. [로컬 설치 및 실행](#7-로컬-설치-및-실행)
8. [Alpha Vantage API 설정](#8-alpha-vantage-api-설정)
9. [Google Cloud 서비스 계정 설정](#9-google-cloud-서비스-계정-설정)
10. [Google Sheets 준비](#10-google-sheets-준비)
11. [Streamlit Secrets 설정](#11-streamlit-secrets-설정)
12. [Streamlit Community Cloud 배포](#12-streamlit-community-cloud-배포)
13. [프로그램 시작 시 처리 순서](#13-프로그램-시작-시-처리-순서)
14. [사이드바 공통 설정](#14-사이드바-공통-설정)
15. [자산매매일지 탭 사용법](#15-자산매매일지-탭-사용법)
16. [현금잔고 관리](#16-현금잔고-관리)
17. [매매일지 입력 및 수정](#17-매매일지-입력-및-수정)
18. [보유수량과 평균매수가 계산](#18-보유수량과-평균매수가-계산)
19. [현재 자산평가 방식](#19-현재-자산평가-방식)
20. [ETF 리밸런싱 탭 사용법](#20-etf-리밸런싱-탭-사용법)
21. [API 호출 구조](#21-api-호출-구조)
22. [월봉 RAW 데이터 재사용 구조](#22-월봉-raw-데이터-재사용-구조)
23. [LAA 전략 계산](#23-laa-전략-계산)
24. [VAA 전략 계산](#24-vaa-전략-계산)
25. [오리지널 듀얼 모멘텀 계산](#25-오리지널-듀얼-모멘텀-계산)
26. [하위전략 비중 정규화](#26-하위전략-비중-정규화)
27. [전략별 최근 리밸런싱일](#27-전략별-최근-리밸런싱일)
28. [최종 목표배분 통합](#28-최종-목표배분-통합)
29. [잔여 현금 최소화 정수 주수 최적화](#29-잔여-현금-최소화-정수-주수-최적화)
30. [BUYSELLHOLD 주문안 계산](#30-buysellhold-주문안-계산)
31. [Google Sheets 저장 구조](#31-google-sheets-저장-구조)
32. [rebalance_plan 컬럼 설명](#32-rebalance_plan-컬럼-설명)
33. [rebalance_basis 컬럼 설명](#33-rebalance_basis-컬럼-설명)
34. [화면에서 확인할 수 있는 결과](#34-화면에서-확인할-수-있는-결과)
35. [권장 사용 순서](#35-권장-사용-순서)
36. [오류 메시지와 해결 방법](#36-오류-메시지와-해결-방법)
37. [계산상 주의사항](#37-계산상-주의사항)
38. [데이터 백업과 보안](#38-데이터-백업과-보안)
39. [주요 함수 설명](#39-주요-함수-설명)
40. [프로젝트 파일 구성 예시](#40-프로젝트-파일-구성-예시)
41. [업데이트 시 점검사항](#41-업데이트-시-점검사항)
42. [면책사항](#42-면책사항)

---

# 1. 프로그램 개요

이 프로그램은 미국 주식과 ETF의 매매내역, 현금잔고, 보유수량, 평가금액을 관리하고 다음 세 가지 ETF 자산배분 전략을 계산하는 Streamlit 애플리케이션입니다.

- LAA 전략
- VAA 전략
- 오리지널 듀얼 모멘텀 전략

전략 계산 결과는 현재 보유수량과 비교되어 다음 항목으로 변환됩니다.

- 목표 투자비중
- 목표 투자금액
- 목표 주수
- 리밸런싱 후 예상 비중
- 매수 필요수량
- 매도 필요수량
- 예상 잔여 현금

모든 매매내역과 현금잔고, 마지막 리밸런싱 주문안, 전략 계산 근거, ETF 월봉 원시 데이터는 Google Sheets에 저장됩니다.

이 프로그램은 **증권사에 실제 주문을 전송하지 않습니다.** 계산된 주문안을 사용자가 검토한 뒤 직접 매매하는 의사결정 지원 도구입니다.

---

# 2. 핵심 기능 요약

| 구분 | 기능 |
|---|---|
| 자산관리 | USD·KRW 현금, 보유수량, 평가금액, 자산비중 계산 |
| 매매일지 | BUY, SELL, ADJUST 내역을 Google Sheets에 저장 |
| 가격 데이터 | 전체 전략 ETF 11개에 대해 월봉 API를 각각 1회 호출 |
| API 절감 | 월봉 응답의 최근 `close`를 현재 자산평가와 목표 주수 계산에도 사용 |
| RAW 저장 | ETF별 최근 61개 월봉을 `rebalance_basis`에 저장 |
| 데이터 재사용 | 앱 재실행 시 저장된 RAW에서 최근가격을 복원 |
| 수동 투자금 계산 | 저장된 RAW를 사용하므로 Alpha Vantage API 호출 0회 |
| LAA | IWD·GLD·IEF 고정 배분 + QQQ 또는 SHY 선택 |
| VAA | 가중 모멘텀 점수로 공격형 또는 안전자산 1개 선택 |
| ODM | SPY·EFA·BIL의 12개월 수익률을 비교하여 SPY·EFA·AGG 중 선택 |
| 주수 최적화 | 정수 주수 조건에서 목표비중 오차와 잔여 현금을 줄이도록 추가 배분 |
| 주문안 | BUY, SELL, HOLD, 가격 확인 필요 구분 |
| 영구 저장 | `rebalance_plan`과 `rebalance_basis`에 최신 결과 덮어쓰기 |
| 다운로드 | 최종 리밸런싱 주문안을 CSV로 다운로드 |

---

# 3. 중요한 동작 원칙

## 3.1 신규 전략 계산 시 API 호출은 총 11회

`현재 포트폴리오 총자산 사용`을 선택하고 계산 버튼을 누르면 전략에 포함된 ETF 11개에 대해 `TIME_SERIES_MONTHLY_ADJUSTED`를 각각 한 번씩 호출합니다.

- 월별 수익률 계산을 위한 별도 호출 없음
- 최신가격 조회를 위한 `GLOBAL_QUOTE` 추가 호출 없음
- 전략 외 보유종목을 위한 API 추가 호출 없음

따라서 정상적인 한 번의 신규 계산에서는 **11개 ETF × 1회 = 총 11회**가 호출됩니다.

## 3.2 다음 요청 전 1.25초 대기

API 요청은 한꺼번에 보내지 않습니다.

```text
1번째 ETF 요청
→ 1.25초 대기
→ 2번째 ETF 요청
→ 1.25초 대기
...
→ 11번째 ETF 요청
```

11개 ETF 호출 사이에는 총 10번의 대기가 발생합니다. 마지막 ETF 요청 후에는 다음 요청이 없으므로 추가 대기하지 않습니다.

## 3.3 전략 계산과 평가가격의 데이터 구분

한 번의 월봉 응답에서 다음 두 가격을 구분해서 사용합니다.

| 용도 | 사용 가격 |
|---|---|
| 1·3·6·12개월 수익률 | `adjusted_close` |
| 현재 자산평가 | 최근 월봉 행의 `close` |
| 목표 주수 계산 | 최근 월봉 행의 `close` |
| `close`가 없는 예외 상황 | `adjusted_close`로 대체 |

`adjusted_close`는 배당과 분할 등의 영향을 반영한 수익률 계산에 적합하고, `close`는 해당 월봉 응답에 기록된 실제 종가 기준 평가에 사용됩니다.

> 주의: 이 값은 실시간 시세나 일별 최신 호가가 아니라 `TIME_SERIES_MONTHLY_ADJUSTED` 응답 안의 가장 최근 월봉 `close`입니다.

## 3.4 수동 투자금 계산 시 API 호출 없음

`리밸런싱 기준 투자금`에서 `수동 입력`을 선택하면 Google Sheets의 `rebalance_basis`에 저장된 ETF 월봉 RAW를 불러와 전략을 다시 계산합니다.

저장된 RAW가 없다면 자동으로 API를 호출하지 않고, 먼저 현재 포트폴리오 기준 신규 계산을 한 번 실행하라는 오류를 표시합니다.

## 3.5 최신 결과만 유지

- `trades`: 거래내역을 누적 저장하거나 사용자가 전체 수정
- `cash`: 현금잔고 스냅샷을 누적 저장
- `rebalance_plan`: 마지막 주문안으로 전체 덮어쓰기
- `rebalance_basis`: 마지막 계산 근거와 월봉 RAW로 전체 덮어쓰기

---

# 4. 전체 시스템 구조

```text
사용자
  │
  ▼
Streamlit 화면
  ├─ 자산/매매일지 탭
  └─ ETF 자산배분 리밸런싱 탭
  │
  ├───────────────┐
  ▼               ▼
Google Sheets     Alpha Vantage
  │               │
  │               └─ ETF 11개 월봉 API
  │
  ├─ settings
  ├─ cash
  ├─ trades
  ├─ rebalance_plan
  └─ rebalance_basis
          │
          ├─ 전략 계산 근거
          └─ ETF 월봉 RAW 최근 61개월
```

## 4.1 사용 라이브러리

| 라이브러리 | 역할 |
|---|---|
| `streamlit` | 웹 화면과 사용자 입력 |
| `pandas` | 데이터 정리와 계산 |
| `requests` | Alpha Vantage HTTP 요청 |
| `gspread` | Google Sheets 읽기·쓰기 |
| `google-auth` | 서비스 계정 인증 |
| `python-dateutil` | 날짜 계산 보조 |
| `calendar` | 월말 날짜 처리 |
| `math` | 정수 주수 내림 계산 |
| `time` | API 요청 간 1.25초 대기 |

---

# 5. 관리 대상 ETF

## 5.1 전체 11개 ETF

| 티커 | 자산군 | 사용 전략 |
|---|---|---|
| IWD | 미국 대형 가치주 | LAA |
| GLD | 금 | LAA |
| IEF | 미국 중기국채 | LAA, VAA |
| QQQ | 나스닥100 | LAA |
| SHY | 미국 단기국채 | LAA, VAA |
| SPY | 미국 S&P500 | VAA, ODM |
| EFA | 미국 제외 선진국 주식 | VAA, ODM |
| EEM | 신흥국 주식 | VAA |
| AGG | 미국 종합채권 | VAA, ODM |
| LQD | 미국 투자등급 회사채 | VAA |
| BIL | 미국 초단기 국채 | ODM |

## 5.2 전략 수익률 계산에 직접 사용되는 ETF

`DATA_TICKERS`에는 VAA와 ODM 계산에 필요한 다음 8개 ETF가 포함됩니다.

```text
AGG, BIL, EEM, EFA, IEF, LQD, SHY, SPY
```

다만 API 호출은 데이터 재사용과 현재가격 반영을 위해 전체 11개 ETF에 대해 수행됩니다.

---

# 6. 설치 전 준비사항

다음 항목이 필요합니다.

1. Python 3.10 이상 권장
2. Alpha Vantage API Key
3. Google 계정
4. Google Cloud 프로젝트
5. Google Cloud 서비스 계정
6. 서비스 계정 인증정보
7. Google Spreadsheet
8. GitHub 계정과 Streamlit Community Cloud 계정 — 온라인 배포 시

---

# 7. 로컬 설치 및 실행

## 7.1 프로젝트 폴더 생성

```bash
mkdir us-etf-asset-manager
cd us-etf-asset-manager
```

## 7.2 소스 코드 배치

프로젝트 폴더에 다음 파일을 저장합니다.

```text
us_stock_asset_manager_app_v16_min_cash.py
```

원하는 경우 Streamlit 기본 파일명인 `app.py`로 변경해도 됩니다.

```bash
mv us_stock_asset_manager_app_v16_min_cash.py app.py
```

## 7.3 가상환경 생성

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
```

### macOS 또는 Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 7.4 `requirements.txt` 작성

프로젝트 루트에 `requirements.txt`를 만들고 다음 내용을 저장합니다.

```text
streamlit
pandas
requests
python-dateutil
gspread
google-auth
```

## 7.5 라이브러리 설치

```bash
pip install -r requirements.txt
```

## 7.6 Secrets 폴더 생성

```bash
mkdir .streamlit
```

`.streamlit/secrets.toml` 파일을 작성합니다. 자세한 예시는 [11. Streamlit Secrets 설정](#11-streamlit-secrets-설정)을 참고하십시오.

## 7.7 앱 실행

소스 파일명을 유지한 경우:

```bash
streamlit run us_stock_asset_manager_app_v16_min_cash.py
```

`app.py`로 변경한 경우:

```bash
streamlit run app.py
```

일반적으로 브라우저에서 다음 주소가 열립니다.

```text
http://localhost:8501
```

---

# 8. Alpha Vantage API 설정

## 8.1 API Key 준비

Alpha Vantage에서 API Key를 발급받아 Streamlit Secrets에 저장합니다.

이 프로그램은 다음 API 함수만 사용합니다.

```text
TIME_SERIES_MONTHLY_ADJUSTED
```

요청 예시는 다음과 같습니다.

```text
https://www.alphavantage.co/query
?function=TIME_SERIES_MONTHLY_ADJUSTED
&symbol=SPY
&apikey=YOUR_API_KEY
```

## 8.2 호출 순서

전체 티커를 정렬한 뒤 한 종목씩 순차 요청합니다.

```text
AGG → BIL → EEM → EFA → GLD → IEF → IWD → LQD → QQQ → SHY → SPY
```

각 요청 뒤 다음 요청 전 1.25초를 기다립니다.

## 8.3 오류 응답 처리

다음 응답 키가 있으면 오류로 처리합니다.

| 응답 키 | 의미 |
|---|---|
| `Error Message` | 잘못된 요청 또는 종목 오류 |
| `Note` | 호출 제한 관련 메시지 |
| `Information` | 호출 한도 또는 요금제 안내 |

한 종목에서 오류가 발생해도 나머지 종목 조회는 계속 진행됩니다. 오류가 발생한 종목은 화면의 `ETF 통합 데이터 로딩 오류 보기`에서 확인할 수 있습니다.

---

# 9. Google Cloud 서비스 계정 설정

## 9.1 Google Cloud 프로젝트 생성

1. Google Cloud Console에 접속합니다.
2. 새 프로젝트를 만듭니다.
3. 프로젝트 이름을 지정합니다.
4. 생성된 프로젝트를 선택합니다.

## 9.2 Google Sheets API 활성화

1. `API 및 서비스`로 이동합니다.
2. `라이브러리`를 선택합니다.
3. `Google Sheets API`를 검색합니다.
4. `사용`을 누릅니다.

필요한 경우 Google Drive API도 함께 활성화해 두는 것이 좋습니다.

## 9.3 서비스 계정 생성

1. `IAM 및 관리자` → `서비스 계정`으로 이동합니다.
2. `서비스 계정 만들기`를 누릅니다.
3. 서비스 계정 이름을 입력합니다.
4. 생성된 서비스 계정의 이메일 주소를 확인합니다.

예시:

```text
etf-manager@your-project-id.iam.gserviceaccount.com
```

## 9.4 인증정보 준비

서비스 계정 키 생성이 가능한 환경에서는 JSON 키 내용을 Streamlit Secrets의 `[gcp_service_account]` 블록에 옮깁니다.

JSON 파일 자체를 GitHub 저장소에 올리면 안 됩니다.

> 조직 정책으로 서비스 계정 키 생성이 차단되어 있다면 조직 관리자 정책 또는 지원되는 다른 인증방식을 확인해야 합니다. 현재 코드는 서비스 계정 JSON 형식의 인증정보를 전제로 작성되어 있습니다.

## 9.5 Google Sheet 공유

생성한 Google Spreadsheet의 `공유` 버튼을 누르고 서비스 계정 이메일을 **편집자**로 추가합니다.

이 권한이 없으면 다음 작업이 실패합니다.

- 시트 자동 생성
- 헤더 작성
- 매매내역 추가
- 리밸런싱 결과 덮어쓰기

---

# 10. Google Sheets 준비

## 10.1 Spreadsheet 생성

새 Google Spreadsheet를 생성합니다.

Spreadsheet URL 예시:

```text
https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit
```

`/d/`와 `/edit` 사이 문자열이 `GOOGLE_SHEET_ID`입니다.

```text
1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890
```

## 10.2 시트 자동 생성

프로그램이 실행되면 다음 워크시트가 없을 경우 자동으로 생성합니다.

- `trades`
- `cash`
- `settings`
- `rebalance_plan`
- `rebalance_basis`

필요한 열 수가 늘어나면 시트 그리드도 자동 확장합니다.

## 10.3 `settings` 시트 설정

`settings` 시트는 두 개의 열을 사용합니다.

| key | value |
|---|---|
| usdkrw_rate | 환율 숫자 또는 GOOGLEFINANCE 계산값 |
| usdkrw_source | 환율 출처 설명 |
| usdkrw_rate_date | 환율 기준일 |

권장 예시:

| key | value |
|---|---|
| usdkrw_rate | `=GOOGLEFINANCE("CURRENCY:USDKRW")` |
| usdkrw_source | Google Sheets GOOGLEFINANCE |
| usdkrw_rate_date | `=TODAY()` |

앱은 `usdkrw_rate` 값을 숫자로 변환하여 사용합니다. 쉼표가 포함된 문자열 또는 `1 USD = 1380 KRW`와 같은 메모형 문자열에서도 첫 번째 숫자를 추출할 수 있습니다.

## 10.4 환율을 읽지 못할 때

다음 상황에서는 내부 기본값인 1,380원/USD를 사용합니다.

- `settings` 시트가 비어 있음
- `usdkrw_rate` 행이 없음
- 값이 숫자로 변환되지 않음
- 값이 0 이하임
- Google Sheets 읽기 중 오류 발생

화면 상단에 기본값 사용 사유가 표시됩니다.

---

# 11. Streamlit Secrets 설정

프로젝트의 `.streamlit/secrets.toml`에 다음 형식으로 저장합니다.

```toml
ALPHA_VANTAGE_API_KEY = "YOUR_ALPHA_VANTAGE_API_KEY"
GOOGLE_SHEET_ID = "YOUR_GOOGLE_SHEET_ID"

[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "your-private-key-id"
private_key = "-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY\n-----END PRIVATE KEY-----\n"
client_email = "your-service-account@your-project-id.iam.gserviceaccount.com"
client_id = "your-client-id"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/your-service-account%40your-project-id.iam.gserviceaccount.com"
universe_domain = "googleapis.com"
```

## 11.1 주의사항

- `private_key`의 줄바꿈은 `\n` 형식으로 유지합니다.
- 코드는 읽은 뒤 `\n`을 실제 줄바꿈으로 변환합니다.
- `secrets.toml`은 GitHub에 업로드하지 않습니다.
- `.gitignore`에 다음 항목을 추가합니다.

```gitignore
.streamlit/secrets.toml
*.json
.venv/
__pycache__/
```

---

# 12. Streamlit Community Cloud 배포

## 12.1 GitHub 저장소 구성

최소 다음 파일을 저장소 루트에 둡니다.

```text
app.py
requirements.txt
README.md
```

소스 파일명을 그대로 사용할 경우 배포 화면에서 해당 Python 파일을 메인 파일로 선택합니다.

## 12.2 배포

1. Streamlit Community Cloud에 로그인합니다.
2. `Create app`을 선택합니다.
3. GitHub 저장소와 브랜치를 선택합니다.
4. 메인 Python 파일 경로를 지정합니다.
5. 앱의 Secrets 설정 화면에 `secrets.toml` 내용을 붙여 넣습니다.
6. 배포를 실행합니다.

## 12.3 배포 후 확인

사이드바에 다음 메시지가 보여야 합니다.

```text
Alpha Vantage API Key 확인
Google Sheet ID 확인
```

오류가 표시되면 Secrets 키 이름과 들여쓰기를 확인하십시오.

---

# 13. 프로그램 시작 시 처리 순서

앱을 열면 다음 순서로 초기화됩니다.

1. Streamlit 페이지 설정
2. Secrets에서 API Key와 Sheet ID 확인
3. 평가 기준일 입력
4. `settings` 시트에서 USD/KRW 환율 조회
5. `trades` 시트 불러오기
6. `cash` 시트의 마지막 잔고 불러오기
7. `rebalance_plan`의 마지막 주문안 불러오기
8. `rebalance_basis`의 마지막 근거와 RAW 불러오기
9. 매매일지로 현재 보유수량 계산
10. 전략별 최근 매매일 계산
11. 저장 RAW에서 최근 월봉 `close`를 복원
12. 복원한 가격을 Streamlit 세션 가격에 저장
13. 현재 총자산 계산
14. 자산 탭과 리밸런싱 탭 표시

앱 시작 단계에서는 Alpha Vantage API를 호출하지 않습니다.

---

# 14. 사이드바 공통 설정

## 14.1 인증 상태

- API Key가 있으면 `Alpha Vantage API Key 확인`
- Sheet ID가 있으면 `Google Sheet ID 확인`
- 누락되면 오류를 표시하고 앱을 중단

## 14.2 평가 기준일

평가 기준일은 다음 처리에 사용됩니다.

- 사용할 월봉의 마지막 날짜 제한
- 진행 중인 월 데이터 제외 판정
- 최근가격 선택
- 다음 리밸런싱일 도래 여부 판정
- 매매내역이 없을 때 임시 최근 리밸런싱일

과거 날짜를 선택하면 그 날짜 이하의 데이터만 사용합니다.

## 14.3 캐시 초기화

`캐시 초기화` 버튼은 Streamlit의 데이터 캐시와 리소스 캐시를 지웁니다.

현재 Google Spreadsheet 연결 함수는 리소스 캐시를 사용합니다. 세션 최신가격은 별도의 `session_state`에 저장되므로 자산 탭의 `세션 최신가 지우기` 버튼으로 관리합니다.

---

# 15. 자산매매일지 탭 사용법

이 탭은 다음 기능을 제공합니다.

1. 총 자산 확인
2. 저장된 ETF 가격 사용
3. 전략 외 종목의 수동 가격 입력
4. 세션 가격 삭제
5. USD·KRW 현금잔고 저장
6. 여러 건의 매매일지 일괄 입력
7. 보유종목 평가현황 확인
8. 종목별 비중 차트 확인
9. 기존 매매일지 전체 수정

이 탭에서는 Alpha Vantage API를 호출하지 않습니다.

## 15.1 세션 최신가 지우기

버튼을 누르면 현재 앱 세션에 저장된 가격이 삭제됩니다.

삭제 직후에는 다음 우선순위로 자산평가가 이루어집니다.

1. 세션에 남아 있는 가격
2. 평균매수가 임시평가
3. 가격 없음

앱 세션을 새로 시작하면 `rebalance_basis` 저장 RAW에서 가격이 다시 복원됩니다.

## 15.2 수동 최신가격 입력

보유종목별로 다음 항목을 입력할 수 있습니다.

- 티커 — 수정 불가
- 수동 최신가 USD
- 가격 기준일

수동 가격은 다음 특징이 있습니다.

- API 호출 없음
- 현재 Streamlit 세션에만 저장
- Google Sheets에는 저장하지 않음
- 출처는 `MANUAL`
- 같은 티커의 기존 세션 가격을 덮어씀

전략 외 보유종목의 자산평가에 특히 유용합니다.

---

# 16. 현금잔고 관리

## 16.1 입력 항목

- 현금 USD
- 현금 KRW
- 메모

## 16.2 저장 방식

`현금 잔고 저장` 버튼을 누르면 `cash` 시트에 새 행을 추가합니다.

```text
updated_at, cash_usd, cash_krw, memo
```

프로그램은 `cash` 시트의 마지막 행을 현재 잔고로 사용합니다.

따라서 `cash` 시트는 입출금 거래원장이 아니라 **잔고 스냅샷 기록**입니다.

예시:

| updated_at | cash_usd | cash_krw | memo |
|---|---:|---:|---|
| 2026-07-01 08:00:00 | 1,500 | 500,000 | 월초 잔고 |
| 2026-07-12 10:30:00 | 820 | 250,000 | ETF 매수 후 |

현재 잔고는 두 번째 행입니다.

## 16.3 현금의 총자산 반영

```text
KRW 현금의 USD 환산액 = cash_krw ÷ USD/KRW 환율
총자산 USD = ETF 평가액 USD + cash_usd + KRW 현금의 USD 환산액
총자산 KRW = 총자산 USD × USD/KRW 환율
```

---

# 17. 매매일지 입력 및 수정

## 17.1 매매 구분

| 구분 | 의미 | 수량 처리 |
|---|---|---|
| BUY | 매수 | 보유수량 증가 |
| SELL | 매도 | 보유수량 감소 |
| ADJUST | 입고·출고·수량보정 | 입력 수량을 그대로 더함 |

### ADJUST 예시

```text
외부 증권사에서 3주 입고: ADJUST, quantity = 3
수량 오류를 1주 감소: ADJUST, quantity = -1
```

## 17.2 입력란 조작

- `입력란 추가`: 한 개 추가
- `입력란 5개 추가`: 다섯 개 추가
- `입력란 초기화`: 입력 폼을 한 개로 초기화

## 17.3 저장 항목

| 컬럼 | 설명 |
|---|---|
| trade_date | 매매일 |
| ticker | 종목 티커 |
| side | BUY, SELL, ADJUST |
| quantity | 수량 |
| price_usd | 체결가격 USD |
| fee_usd | 수수료 USD |
| memo | 메모 |
| created_at | 앱 저장시각 |

## 17.4 입력 검증

- 티커는 필수
- 티커는 자동 대문자 변환
- BUY와 SELL의 수량은 0보다 커야 함
- 완전히 비어 있는 행은 저장하지 않음
- 여러 건은 `append_rows`로 한 번에 저장

## 17.5 기존 매매일지 수정

하단의 `매매일지 확인/수정` 표에서 행을 직접 수정하거나 추가·삭제할 수 있습니다.

`수정 내용 저장` 버튼을 누르면 `trades` 시트 전체가 편집된 내용으로 덮어쓰기 됩니다.

> 주의: 이 작업은 부분 수정이 아니라 전체 덮어쓰기입니다. 잘못 삭제한 행은 Google Sheets 버전 기록 또는 별도 백업이 필요할 수 있습니다.

---

# 18. 보유수량과 평균매수가 계산

## 18.1 보유수량

종목별 수량은 다음과 같이 계산됩니다.

```text
signed_qty:
BUY    → +quantity
SELL   → -quantity
ADJUST → +quantity

현재 보유수량 = signed_qty 합계
```

절대값이 사실상 0인 종목은 보유현황에서 제외합니다.

## 18.2 매수원가

BUY 거래에 대해서만 매수원가를 계산합니다.

```text
건별 매수원가 = quantity × price_usd + fee_usd
전체 매수원가 = 건별 매수원가 합계
전체 매수수량 = BUY quantity 합계
평균매수가 = 전체 매수원가 ÷ 전체 매수수량
```

## 18.3 평균매수가의 제한

현재 평균매수가는 모든 과거 BUY 수량과 원가를 누적한 단순 평균입니다.

부분매도 시 매도된 물량의 원가를 차감하지 않으므로 다음 목적에는 정확하지 않을 수 있습니다.

- 세금 신고용 취득원가
- FIFO 선입선출 원가
- 이동평균법 잔여 원가
- 증권사 표시 평균단가와의 완전 일치

ADJUST 수량에는 매수원가가 반영되지 않으므로 ADJUST만 있는 종목은 평균매수가가 비어 있을 수 있습니다.

---

# 19. 현재 자산평가 방식

## 19.1 평가가격 우선순위

각 종목의 평가가격은 다음 순서로 결정됩니다.

1. 저장 RAW에서 복원한 가격 또는 현재 세션의 수동 가격
2. 가격이 없으면 평균매수가
3. 평균매수가도 없으면 평가 제외

평가 기준은 다음 값으로 표시됩니다.

- `REBALANCE_BASIS_RAW`
- `API_MONTHLY_CLOSE`
- `MANUAL`
- `평균매수가 임시평가`
- `가격 없음`

## 19.2 평가금액

```text
평가액 USD = 보유수량 × 평가가격 USD
평가액 KRW = 평가액 USD × USD/KRW 환율
```

## 19.3 평가손익

```text
평가손익 USD = (평가가격 - 평균매수가) × 보유수량
평가손익률 = 평가가격 ÷ 평균매수가 - 1
```

평균매수가가 없으면 평가손익을 계산하지 않습니다.

## 19.4 자산비중

```text
종목 비중 = 종목 평가액 USD ÷ 총자산 USD
```

현금이 총자산에 포함되므로 화면의 종목 비중 합계는 100%보다 작을 수 있습니다.

---

# 20. ETF 리밸런싱 탭 사용법

리밸런싱 탭의 주요 순서는 다음과 같습니다.

1. 전략 설명 확인
2. 전략별 최근 리밸런싱일 확인
3. 저장된 마지막 주문안 확인
4. 리밸런싱 기준 투자금 선택
5. 데이터 조회기간 선택
6. LAA 조건 입력
7. VAA 0점 처리 설정
8. LAA·VAA·ODM 비중 입력
9. 계산 버튼 실행
10. 전략별 선택 결과 확인
11. VAA와 ODM 계산표 확인
12. 최종 주문안 확인
13. 계산 근거 확인
14. CSV 다운로드

## 20.1 투자금 기준

### 현재 포트폴리오 총자산 사용

- API 11회 신규 호출
- 월봉 RAW 최신화
- 최근 `close`로 현재 총자산 재평가
- 재평가된 총자산을 리밸런싱 예산으로 사용

### 수동 입력

- KRW 또는 USD 선택
- 사용자가 총 투자금 직접 입력
- 저장 RAW로 전략 계산
- API 호출 0회

## 20.2 데이터 조회기간

13개월부터 60개월까지 선택할 수 있습니다.

1·3·6·12개월 수익률 계산에는 최소 13개의 월봉이 필요합니다.

저장 RAW는 최대 61개 월봉이므로 최대 조회기간 60개월을 지원할 수 있습니다.

## 20.3 진행 중인 월 데이터 제외

체크하면 전략 수익률 계산에서 평가 기준일과 같은 연·월의 최신 월봉을 제외합니다.

예를 들어 평가 기준일이 2026년 7월 12일이고 API 응답에 2026년 7월 월봉이 있다면:

- 체크: 전략 계산 기준월은 2026년 6월
- 해제: 전략 계산 기준월은 2026년 7월

현재 자산평가용 가격은 이 옵션과 별개로 평가 기준일 이하의 가장 최근 월봉 `close`를 사용합니다.

---

# 21. API 호출 구조

## 21.1 신규 계산

```text
사용자가 현재 포트폴리오 총자산 사용 선택
       │
       ▼
11개 ETF 목록 생성
       │
       ▼
각 ETF에 TIME_SERIES_MONTHLY_ADJUSTED 1회 요청
       │
       ├─ adjusted_close → 전략 수익률
       ├─ close → 현재 자산평가
       └─ 전체 월봉 → rebalance_basis RAW 저장
```

## 21.2 호출 수

| 동작 | Alpha Vantage 호출 수 |
|---|---:|
| 앱 실행 | 0 |
| 자산 탭 열기 | 0 |
| 현금 저장 | 0 |
| 매매일지 저장 | 0 |
| 수동 최신가 입력 | 0 |
| 저장된 주문안 열기 | 0 |
| 수동 투자금 전략 계산 | 0 |
| 현재 포트폴리오 기준 신규 계산 | 11 |

## 21.3 일부 종목 조회 실패 시

한 종목이 실패해도 다른 ETF는 계속 조회합니다.

다만 전략 계산에 필요한 ETF 데이터가 누락되면 다음 문제가 발생할 수 있습니다.

- VAA 수익률 결측
- ODM 12개월 수익률 결측
- 목표 주수 계산 불가
- `가격 확인 필요` 표시

누락 종목은 화면 경고와 오류 확장창에서 확인합니다.

---

# 22. 월봉 RAW 데이터 재사용 구조

## 22.1 저장 범위

ETF별 최근 최대 61개 월봉을 `rebalance_basis`에 저장합니다.

저장 데이터:

- 날짜
- 시가
- 고가
- 저가
- 종가
- 조정종가
- 거래량
- 배당금
- 원 API 조회시각

정상적으로 11개 ETF 모두 61개월이 있으면 최대 약 671개의 RAW 행이 저장됩니다.

## 22.2 앱 재실행 시 복원

1. `rebalance_basis`에서 `section = ETF 월봉 RAW` 행만 필터링
2. 티커별로 DataFrame 복원
3. 평가 기준일 이하 최근 `close` 선택
4. 세션의 `latest_quotes_df`에 저장
5. 현재 자산평가에 사용

## 22.3 수동 투자금 계산 시 복원

저장 RAW를 동일한 월봉 DataFrame 구조로 재구성한 뒤 신규 API 응답과 같은 계산 함수에 전달합니다.

따라서 신규 계산과 수동 계산은 데이터 출처만 다르고 다음 계산 로직은 같습니다.

- 가격 행렬 작성
- VAA 계산
- ODM 계산
- 목표배분 계산
- 정수 주수 최적화
- 주문안 저장

## 22.4 수동 계산 후 저장

수동 계산을 실행해도 `rebalance_plan`과 `rebalance_basis`는 최신 계산 결과로 덮어쓰기 됩니다.

다만 복원된 RAW의 `raw_fetched_at`에는 원래 API 조회시각을 유지하여 수동 재계산 시각과 실제 API 조회시각을 구분합니다.

---

# 23. LAA 전략 계산

## 23.1 기본 구성

LAA 전략 안에서 네 자산을 각각 25%로 배분합니다.

| 구분 | ETF | LAA 내부비중 |
|---|---|---:|
| 고정 | IWD | 25% |
| 고정 | GLD | 25% |
| 고정 | IEF | 25% |
| 변동 | QQQ 또는 SHY | 25% |

## 23.2 변동자산 조건

사용자가 다음 결합조건 충족 여부를 직접 선택합니다.

```text
S&P500 200일 이동평균선 하회
+
미국 실업률이 12개월 평균 상회
```

| 입력 | 선택 ETF |
|---|---|
| O: 두 조건 모두 충족 | SHY |
| X: 동시에 충족하지 않음 | QQQ |

이 프로그램은 S&P500 이동평균과 실업률 데이터를 자동 조회하지 않습니다.

## 23.3 리밸런싱 주기

| 자산 | 주기 |
|---|---|
| IWD, GLD, IEF | 연 1회 |
| QQQ 또는 SHY | 월 1회 |

---

# 24. VAA 전략 계산

## 24.1 자산 그룹

### 공격형

- SPY
- EFA
- EEM
- AGG

### 안전자산

- LQD
- IEF
- SHY

## 24.2 수익률 계산

ETF별 조정종가 시계열 `s`에 대해 다음과 같이 계산합니다.

```text
1개월 수익률  = s[-1] ÷ s[-2]  - 1
3개월 수익률  = s[-1] ÷ s[-4]  - 1
6개월 수익률  = s[-1] ÷ s[-7]  - 1
12개월 수익률 = s[-1] ÷ s[-13] - 1
```

## 24.3 모멘텀 스코어

```text
VAA 점수
= 12 × 1개월 수익률
+ 4 × 3개월 수익률
+ 2 × 6개월 수익률
+ 1 × 12개월 수익률
```

최근 수익률에 더 큰 가중치를 부여합니다.

## 24.4 0점 처리 옵션

### 체크한 경우

```text
공격형 네 ETF 점수가 모두 0 초과여야 공격형 선택
```

0점은 방어 신호입니다.

### 체크 해제한 경우

```text
공격형 네 ETF 점수가 모두 0 이상이면 공격형 선택
```

## 24.5 최종 선택

```text
공격형 네 ETF가 모두 기준 충족
→ 공격형 중 모멘텀 점수 1위 ETF 100%

하나라도 기준 미충족
→ 안전자산 중 모멘텀 점수 1위 ETF 100%
```

VAA에 배정된 전체 금액은 선택된 한 ETF에 배분됩니다.

---

# 25. 오리지널 듀얼 모멘텀 계산

## 25.1 비교 자산

- SPY
- EFA
- BIL
- AGG

## 25.2 판정 순서

```text
1. SPY 12개월 수익률과 BIL 12개월 수익률 비교

2-A. SPY > BIL
     → SPY와 EFA 중 12개월 수익률이 높은 ETF 선택

2-B. SPY ≤ BIL
     → AGG 선택
```

## 25.3 최종 배분

ODM에 배정된 전체 금액을 선택된 한 ETF에 100% 배분합니다.

---

# 26. 하위전략 비중 정규화

기본값은 다음과 같습니다.

```text
LAA = 1/3
VAA = 1/3
ODM = 1/3
```

사용자가 입력한 합계가 정확히 1이 아니면 자동 정규화합니다.

```text
정규화 LAA 비중 = 입력 LAA ÷ 전체 입력합계
정규화 VAA 비중 = 입력 VAA ÷ 전체 입력합계
정규화 ODM 비중 = 입력 ODM ÷ 전체 입력합계
```

예시:

```text
입력: LAA 0.5, VAA 0.3, ODM 0.1
합계: 0.9

정규화:
LAA = 0.5 / 0.9 = 55.56%
VAA = 0.3 / 0.9 = 33.33%
ODM = 0.1 / 0.9 = 11.11%
```

합계가 0이면 계산을 중단합니다.

---

# 27. 전략별 최근 리밸런싱일

전체 매매일지의 가장 최근 날짜를 모든 전략에 공통 적용하지 않습니다.

## 27.1 검색 대상

| 구분 | 최근 매매일 검색 대상 | 주기 |
|---|---|---|
| LAA 고정자산 | IWD, GLD, IEF | 연 1회 |
| LAA 변동자산 | QQQ, SHY | 월 1회 |
| VAA | SPY, EFA, EEM, AGG, LQD, IEF, SHY | 월 1회 |
| ODM | SPY, EFA, BIL, AGG | 월 1회 |

## 27.2 매매내역이 없는 경우

해당 전략의 대상 ETF 매매내역이 없으면 평가 기준일을 임시 최근 리밸런싱일로 사용합니다.

화면의 `적용 기준` 열에서 다음과 같이 표시됩니다.

```text
대상 ETF(...) 매매내역 없음 - 평가 기준일 임시 적용
```

## 27.3 다음 리밸런싱일

```text
연 1회 → 최근일 + 1년
월 1회 → 최근일 + 1개월
```

최근 날짜가 월말이면 다음 달도 월말을 유지합니다.

예시:

```text
2026-01-31 + 1개월 = 2026-02-28
2024-02-29 + 1년 = 2025-02-28
```

## 27.4 상태

```text
다음 리밸런싱일 ≤ 평가 기준일 → 리밸런싱 필요
그 외 → 대기
```

> 이 계산은 대상 ETF의 마지막 매매일을 리밸런싱일로 간주합니다. 단순 추가매수도 최근 리밸런싱일로 인식될 수 있습니다.

---

# 28. 최종 목표배분 통합

## 28.1 종목별 전체 목표비중

```text
전체 목표비중 = 하위전략 정규화 비중 × 하위전략 내부 비중
```

예시:

```text
LAA 전체비중 = 33.33%
IWD의 LAA 내부비중 = 25%
IWD 전체 목표비중 = 33.33% × 25% = 8.33%
```

## 28.2 목표금액

```text
목표금액 USD = 총 투자금 USD × 전체 목표비중
목표금액 KRW = 총 투자금 KRW × 전체 목표비중
```

## 28.3 같은 ETF 중복 선택

같은 ETF가 여러 전략에 등장하면 최종 단계에서 하나로 합칩니다.

예시:

- LAA가 IEF 8.33% 배분
- VAA가 IEF 33.33% 선택

최종 IEF 목표비중:

```text
8.33% + 33.33% = 41.66%
```

## 28.4 전략 외 보유종목

현재 보유 중이지만 최종 전략 목표에 없는 종목은 자동으로 다음 행이 추가됩니다.

```text
자산군 = 전략 외 보유
목표비중 = 0
목표금액 = 0
목표 주수 = 0
```

현재수량이 0보다 크면 SELL 후보가 됩니다.

---

# 29. 잔여 현금 최소화 정수 주수 최적화

## 29.1 목적

ETF는 종목별 가격이 다르므로 단순히 목표금액을 가격으로 나눈 뒤 모두 버림하면 현금이 많이 남을 수 있습니다.

기존 단순 방식:

```text
목표 주수 = floor(목표금액 ÷ 가격)
```

현재 프로그램은 먼저 위 방식으로 기본 주수를 만든 뒤, 남은 현금으로 ETF를 추가 배정합니다.

## 29.2 1단계: 기본 정수 주수

가격이 있고 목표비중이 0보다 큰 종목에 대해:

```text
초기 목표 주수 = floor(목표금액 USD ÷ 최근가격 USD)
```

## 29.3 2단계: 배분 가능한 잔여 현금

```text
가격 확인된 종목의 예산 합계
- 초기 목표 주수 평가액 합계
= 추가 배분 가능한 현금
```

가격이 없는 종목의 목표금액은 다른 종목에 임의로 배분하지 않습니다.

## 29.4 3단계: 추가 1주 후보 평가

남은 현금으로 1주 살 수 있는 ETF를 후보로 정합니다.

각 후보에 1주를 추가했다고 가정한 뒤 다음 오차를 계산합니다.

```text
실제비중_i = 후보 주수_i × 가격_i ÷ 전체 전략 예산

전체 제곱오차
= Σ(실제비중_i - 목표비중_i)²
```

제곱오차가 가장 작은 후보에 1주를 추가합니다.

## 29.5 동점 처리

제곱오차가 같으면 다음 순서로 선택합니다.

1. 매수 후 남는 현금이 더 적은 종목
2. 그래도 같으면 티커 알파벳 순

## 29.6 종료 조건

남은 현금으로 어떤 전략 대상 ETF도 1주 살 수 없으면 반복을 종료합니다.

## 29.7 결과 해석

- 일부 종목은 목표비중을 소폭 초과할 수 있음
- 단순 내림보다 잔여 현금이 줄어듦
- 전체 목표비중과의 차이가 상대적으로 작은 조합을 선택
- 전수조합 최적화가 아니라 1주씩 추가하는 탐욕적 반복 방식

## 29.8 예상 잔여 현금

```text
예상 잔여 현금 USD = 전체 목표예산 - 최종 목표 주수 평가액 합계
```

가격이 없는 목표종목이 있으면 그 종목의 목표금액도 예상 잔여 현금에 포함될 수 있습니다.

---

# 30. BUYSELLHOLD 주문안 계산

## 30.1 매매 필요수량

```text
매매 필요 주수 = 최적화된 목표 주수 - 현재 보유수량
```

## 30.2 매매 구분

| 조건 | 결과 |
|---|---|
| 매매 필요 주수 > 0 | BUY |
| 매매 필요 주수 < 0 | SELL |
| 절대값이 사실상 0 | HOLD |
| 최근가격이 없음 | 가격 확인 필요 |

## 30.3 매매 필요금액

```text
매매 필요 금액 USD = |매매 필요 주수| × 최근가격
매매 필요 금액 KRW = 매매 필요 금액 USD × 환율
```

## 30.4 리밸런싱 후 값

```text
리밸런싱 후 평가액 USD = 목표 주수 × 최근가격
리밸런싱 후 평가액 KRW = 리밸런싱 후 평가액 USD × 환율
리밸런싱 후 비중 = 리밸런싱 후 평가액 USD ÷ 전체 전략 예산
목표 대비 비중차 = 리밸런싱 후 비중 - 목표비중
```

## 30.5 소수점 보유수량

목표 주수는 정수로 계산하지만 현재 보유수량은 매매일지에서 소수점 입력이 가능합니다.

예시:

```text
목표 주수 = 10주
현재 주수 = 8.5주
매매 필요 주수 = 1.5주
```

실제 소수점 주문 가능 여부는 이용하는 증권사 정책을 확인해야 합니다.

## 30.6 순매수 필요금액

```text
순매수 필요 = 전체 BUY 금액 - 전체 SELL 금액
```

이 값은 매도대금을 전부 매수에 사용할 수 있다고 가정한 단순 금액이며, 체결 순서·수수료·세금·환전비용은 반영하지 않습니다.

---

# 31. Google Sheets 저장 구조

## 31.1 `trades`

매매내역 저장소입니다.

- 신규 입력: 행 추가
- 여러 건: 일괄 추가
- 수정 화면 저장: 전체 덮어쓰기

## 31.2 `cash`

현금잔고 스냅샷 저장소입니다.

- 저장할 때마다 행 추가
- 가장 마지막 행을 현재 잔고로 사용

## 31.3 `settings`

환율 등 공통 설정을 key-value 방식으로 저장합니다.

## 31.4 `rebalance_plan`

마지막 리밸런싱 주문안을 저장합니다.

- 계산할 때마다 전체 덮어쓰기
- 앱 재실행 후에도 마지막 주문안 표시
- 단순 조회 시 API 호출 없음

## 31.5 `rebalance_basis`

다음 정보를 함께 저장합니다.

- 공통 계산 설정
- 전략별 선택 근거
- 전략별 최근·다음 리밸런싱일
- 가격 근거
- 현재 보유평가 근거
- 최종 주문안 산식
- 예상 잔여 현금 근거
- ETF 11개 월봉 RAW

계산할 때마다 최신 데이터로 전체 덮어쓰기 됩니다.

---

# 32. rebalance_plan 컬럼 설명

| 컬럼 | 설명 |
|---|---|
| saved_at | 주문안 저장시각 |
| eval_date | 평가 기준일 |
| strategy_price_month | 전략 수익률 기준월 |
| usdkrw_rate | 적용 USD/KRW 환율 |
| usdkrw_source | 환율 출처 |
| usdkrw_rate_date | 환율 기준일 |
| investment_basis | 현재 포트폴리오 또는 수동 입력 |
| input_currency | PORTFOLIO, USD, KRW |
| total_investment_usd | 리밸런싱 총예산 USD |
| total_investment_krw | 리밸런싱 총예산 KRW |
| laa_selected | LAA 변동 선택 ETF |
| vaa_selected | VAA 선택 ETF |
| odm_selected | ODM 선택 ETF |
| ticker | 종목 티커 |
| asset_class | 자산군 |
| target_weight | 목표비중 |
| target_usd | 목표금액 USD |
| target_krw | 목표금액 KRW |
| current_quantity | 현재 보유수량 |
| current_value_usd | 현재평가액 USD |
| current_value_krw | 현재평가액 KRW |
| current_weight | 현재비중 |
| latest_price_usd | 목표 주수 계산가격 |
| price_date | 가격 기준일 |
| price_source | 가격 출처 |
| target_shares | 최적화된 목표 주수 |
| current_shares | 현재 주수 |
| trade_action | BUY, SELL, HOLD, 가격 확인 필요 |
| trade_shares | 매매 필요 주수 |
| trade_amount_usd | 매매 필요금액 USD |
| trade_amount_krw | 매매 필요금액 KRW |
| next_rebalance_date | 다음 리밸런싱일 |
| rebalance_status | 리밸런싱 필요, 대기, 전략 외 |
| note | 선정 사유 |
| optimized_value_usd | 목표 주수 기준 평가액 USD |
| optimized_value_krw | 목표 주수 기준 평가액 KRW |
| optimized_weight | 목표 주수 기준 실제비중 |
| weight_gap | 실제비중 - 목표비중 |
| estimated_cash_usd | 정수 주수 배분 후 예상 잔여 현금 USD |
| estimated_cash_krw | 예상 잔여 현금 KRW |

---

# 33. rebalance_basis 컬럼 설명

## 33.1 공통 근거 컬럼

| 컬럼 | 설명 |
|---|---|
| saved_at | 계산결과 저장시각 |
| eval_date | 평가 기준일 |
| strategy_price_month | 전략 기준월 |
| section | 근거 분류 |
| strategy | 전략명 |
| ticker | 티커 |
| asset_class | 자산군 |
| group | 공격형·안전자산 등의 그룹 |
| item | 근거 항목명 |
| value | 표시용 값 |
| value_numeric | 숫자형 값 |
| return_1m | 1개월 수익률 |
| return_3m | 3개월 수익률 |
| return_6m | 6개월 수익률 |
| return_12m | 12개월 수익률 |
| momentum_score | VAA 모멘텀 점수 |
| rank | 순위 |
| selected | 선택 여부 또는 매매 구분 |
| decision_reason | 판정 사유 |
| source_note | 데이터 출처와 적용 설명 |

## 33.2 RAW 컬럼

| 컬럼 | 설명 |
|---|---|
| raw_date | 월봉 날짜 |
| raw_open | 월봉 시가 |
| raw_high | 월봉 고가 |
| raw_low | 월봉 저가 |
| raw_close | 월봉 실제 종가 |
| raw_adjusted_close | 월봉 조정종가 |
| raw_volume | 거래량 |
| raw_dividend | 배당금 |
| raw_fetched_at | Alpha Vantage 원 조회시각 |

## 33.3 `section` 예시

- 공통 설정
- 전략 선택 근거
- 리밸런싱일 근거
- 가격 근거
- 현재 보유 근거
- 주문안 산식 근거
- ETF 월봉 RAW

---

# 34. 화면에서 확인할 수 있는 결과

## 34.1 자산 탭

- 총 자산 USD
- 총 자산 KRW
- 주식·ETF 평가액
- 적용 환율
- 종목별 보유수량
- 평균매수가
- 최근가격
- 총자산 반영가격
- 평가액
- 평가손익
- 평가손익률
- 현재 자산비중
- 가격 출처
- 종목별 비중 막대그래프

## 34.2 리밸런싱 탭

- 전략별 최근 리밸런싱일
- 다음 리밸런싱일
- 리밸런싱 필요 여부
- 저장된 마지막 주문안
- 저장된 계산 근거와 RAW
- 리밸런싱 기준 총금액
- 전략 기준월
- 가격 반영 티커 수
- LAA 선택 ETF
- VAA 선택 ETF
- ODM 선택 ETF
- VAA 모멘텀 점수표
- ODM 12개월 수익률표
- 최종 목표비중
- 목표금액
- 목표 주수
- 리밸런싱 후 예상 비중
- 목표 대비 비중차
- BUY·SELL·HOLD
- 매매 필요 주수와 금액
- 예상 잔여 현금
- CSV 다운로드

---

# 35. 권장 사용 순서

## 최초 설정

1. Alpha Vantage API Key를 준비합니다.
2. Google Cloud 서비스 계정을 만듭니다.
3. Google Sheets API를 활성화합니다.
4. Google Spreadsheet를 생성합니다.
5. 서비스 계정 이메일을 Sheet 편집자로 공유합니다.
6. `secrets.toml`을 설정합니다.
7. 앱을 실행합니다.
8. `settings` 시트에 환율 설정을 입력합니다.

## 최초 데이터 입력

1. 자산 탭에서 USD와 KRW 현금잔고를 저장합니다.
2. 보유 중인 종목의 매수·매도·입출고 내역을 입력합니다.
3. 종목별 보유수량을 실제 증권계좌와 비교합니다.
4. 전략 외 종목은 필요하면 수동가격을 입력합니다.

## 최초 리밸런싱 계산

1. 평가 기준일을 확인합니다.
2. `현재 포트폴리오 총자산 사용`을 선택합니다.
3. 데이터 조회기간을 선택합니다.
4. 진행 중인 월 제외 여부를 결정합니다.
5. LAA 조건을 O 또는 X로 선택합니다.
6. VAA 0점 처리 옵션을 결정합니다.
7. LAA·VAA·ODM 비중을 입력합니다.
8. `11개 ETF 신규 조회 후 전략 계산`을 누릅니다.
9. API 11회 조회가 완료될 때까지 화면 진행률을 확인합니다.
10. 전략 선택 결과를 확인합니다.
11. 최종 주문안을 검토합니다.
12. `rebalance_basis`의 계산 근거를 확인합니다.
13. 필요한 경우 CSV를 다운로드합니다.

## 실제 매매 후

1. 실제 체결수량과 체결가격을 매매일지에 입력합니다.
2. 현금잔고를 실제 계좌잔고로 업데이트합니다.
3. 종목별 수량과 현금이 일치하는지 확인합니다.

## API를 사용하지 않는 재계산

1. `수동 입력`을 선택합니다.
2. KRW 또는 USD를 선택합니다.
3. 총 투자금을 입력합니다.
4. 전략 조건과 비중을 수정합니다.
5. `저장된 ETF RAW로 전략 계산(API 미호출)`을 누릅니다.

---

# 36. 오류 메시지와 해결 방법

## 36.1 `ALPHA_VANTAGE_API_KEY 필요`

원인:

- Secrets에 API Key가 없음
- 키 이름이 다름

해결:

```toml
ALPHA_VANTAGE_API_KEY = "발급받은 키"
```

## 36.2 `GOOGLE_SHEET_ID 필요`

해결:

```toml
GOOGLE_SHEET_ID = "스프레드시트 ID"
```

전체 URL이 아니라 ID만 입력합니다.

## 36.3 `gcp_service_account 정보가 Streamlit Secrets에 없습니다`

`[gcp_service_account]` 블록이 없거나 들여쓰기 형식이 잘못된 경우입니다.

## 36.4 Google Sheets 연결 또는 초기화 오류

확인 항목:

- Sheets API가 활성화되어 있는가
- Sheet ID가 정확한가
- 서비스 계정 이메일이 편집자로 공유되어 있는가
- private key의 줄바꿈이 정상인가
- 서비스 계정 키가 폐기되지 않았는가

## 36.5 Alpha Vantage 호출 제한 메시지

다음 중 하나가 화면에 표시될 수 있습니다.

```text
Alpha Vantage 호출 제한 메시지
Alpha Vantage 안내/호출 제한 메시지
```

해결:

- 같은 날 불필요한 신규 계산을 반복하지 않습니다.
- 전략 조건과 투자금만 바꿀 때는 수동 입력 모드를 사용합니다.
- 저장 RAW가 있는지 확인합니다.
- API Key와 사용량을 확인합니다.

## 36.6 `월봉 데이터를 찾지 못했습니다`

가능 원인:

- 티커 오류
- API 응답 제한
- API 서비스 오류
- 응답 형식 변경

## 36.7 `VAA 계산에 필요한 13개월 이상의 월봉 데이터가 부족합니다`

확인 항목:

- 저장 RAW에 누락 종목이 있는가
- 평가 기준일이 너무 과거인가
- 조회기간이 최소 13개월인가
- 진행 중인 월 제외 후 13개 미만이 되었는가

## 36.8 `rebalance_basis에 저장된 ETF 월봉 RAW가 없습니다`

수동 입력 계산 전에 현재 포트폴리오 기준 신규 계산을 한 번 실행해야 합니다.

## 36.9 `가격 확인 필요`

해당 목표 또는 보유종목의 최근가격이 없습니다.

- 전략 ETF: 신규 조회 데이터 누락 여부 확인
- 전략 외 종목: 자산 탭에서 수동 최신가 입력

## 36.10 총자산이 현금만 표시되는 경우

확인 항목:

- 매매일지에 보유수량이 있는가
- 저장 RAW 또는 수동가격이 있는가
- 평균매수가가 있는가
- ADJUST만 입력한 종목인가

최신가격이 없더라도 평균매수가가 있으면 임시 평가합니다. ADJUST만 있고 가격도 없다면 평가액에서 제외될 수 있습니다.

## 36.11 Google Sheets의 열이 맞지 않는 경우

코드는 헤더가 정의된 컬럼과 다르면 첫 행을 업데이트하고 필요한 열 수를 자동 확장합니다.

그러나 기존 데이터를 수동으로 열 순서 변경한 경우 백업 후 해당 시트를 비우고 앱을 다시 실행하는 방법이 안전할 수 있습니다.

---

# 37. 계산상 주의사항

## 37.1 실시간 가격이 아님

현재가격은 월봉 API 응답의 가장 최근 `close`입니다. 장중 실시간 가격이나 전일 종가 전용 API가 아닙니다.

실제 주문 전에 증권사 시세를 다시 확인하십시오.

## 37.2 전략 기준월과 평가가격 기준일이 다를 수 있음

`진행 중인 월 데이터 제외`를 체크하면:

- 전략은 직전 완료월 조정종가로 계산
- 자산평가와 주수는 평가 기준일 이하 최신 월봉 종가로 계산

따라서 두 날짜가 다를 수 있습니다.

## 37.3 수수료·세금·환전비용 미반영

최종 주문안에는 다음 항목이 반영되지 않습니다.

- 매매 수수료
- 환전 스프레드
- 환전 수수료
- 세금
- 호가 차이
- 체결 중 가격 변동

## 37.4 잔여 현금 최적화는 전역 최적해를 보장하지 않음

현재 알고리즘은 1주씩 추가하면서 그 시점의 목표비중 제곱오차가 가장 작은 ETF를 선택합니다.

빠르고 이해하기 쉬운 탐욕적 방법이지만 가능한 모든 정수 주수 조합을 전수조사하거나 수리 최적화하는 방식은 아닙니다.

## 37.5 현재 현금과 주문 후 현금의 차이

화면의 `예상 잔여 현금`은 전체 전략 예산에서 목표 주수 평가액을 뺀 값입니다.

현재 계좌의 실제 USD 현금잔고가 정확히 해당 금액으로 남는다는 뜻은 아닙니다. 다음 요소로 차이가 날 수 있습니다.

- 기존 보유종목 매도대금
- 실제 체결가격
- 수수료
- 환전
- 매매 순서
- 미체결 주문

## 37.6 리밸런싱일 판단

대상 ETF의 마지막 거래일을 최근 리밸런싱일로 사용합니다. 소액 추가매수나 수량 보정도 매매일에 포함될 수 있습니다.

## 37.7 투자전략의 원형과 차이 가능성

코드에 구현된 LAA, VAA, ODM 규칙은 코드에 정의된 ETF와 조건을 기준으로 합니다. 전략 저자의 최신 공식 규칙, 거래일 기준, 대체 ETF, 세부 리밸런싱 관행과 차이가 있을 수 있습니다.

---

# 38. 데이터 백업과 보안

## 38.1 반드시 보호할 정보

- Alpha Vantage API Key
- 서비스 계정 private key
- Google Sheet ID — 민감도는 낮지만 공개 저장소에서는 주의
- 개인 매매내역과 자산금액

## 38.2 GitHub에 올리면 안 되는 파일

```text
.streamlit/secrets.toml
서비스계정키.json
개인 자산내역 CSV
```

## 38.3 Google Sheets 백업

권장 방법:

- Google Sheets 버전 기록 사용
- 정기적으로 XLSX 또는 CSV 다운로드
- `trades`와 `cash` 시트 별도 백업
- 큰 코드 변경 전 `rebalance_plan`과 `rebalance_basis` 복사

## 38.4 서비스 계정 권한

가능한 경우 이 앱에서 사용하는 Spreadsheet에만 편집 권한을 부여하고 다른 민감한 파일에는 공유하지 않습니다.

---

# 39. 주요 함수 설명

## 39.1 설정과 표시

| 함수 | 역할 |
|---|---|
| `get_secret_api_key` | Secrets에서 Alpha Vantage Key 읽기 |
| `get_secret_sheet_id` | Secrets에서 Google Sheet ID 읽기 |
| `money_krw` | 원화 형식 출력 |
| `money_usd` | 달러 형식 출력 |
| `format_pct` | 백분율 표시 |
| `format_score` | 모멘텀 점수 표시 |
| `normalize_strategy_weights` | 전략 비중 합계를 100%로 정규화 |

## 39.2 날짜

| 함수 | 역할 |
|---|---|
| `is_last_day` | 월말 여부 확인 |
| `add_months` | 월말 규칙을 유지해 개월 추가 |
| `add_years` | 윤년을 고려해 연도 추가 |
| `next_rebalance_date` | 주기에 따른 다음 리밸런싱일 |
| `rebalance_status` | 평가일 기준 필요·대기 판정 |

## 39.3 Google Sheets

| 함수 | 역할 |
|---|---|
| `get_google_spreadsheet` | 서비스 계정 인증과 Spreadsheet 연결 |
| `ensure_worksheet` | 시트 자동 생성과 헤더·열 수 정리 |
| `load_sheet` | 시트를 DataFrame으로 읽기 |
| `append_sheet_row` | 한 행 추가 |
| `append_sheet_rows` | 여러 행 일괄 추가 |
| `overwrite_sheet` | 시트 전체 덮어쓰기 |
| `load_trades` | 매매내역 형식 정리 |
| `load_cash` | 현금 데이터 형식 정리 |
| `load_settings` | 설정 key-value 읽기 |
| `save_rebalance_plan_to_sheet` | 마지막 주문안 저장 |
| `save_rebalance_basis_to_sheet` | 근거와 RAW 저장 |

## 39.4 API와 가격

| 함수 | 역할 |
|---|---|
| `check_alpha_error` | Alpha Vantage 오류 메시지 검사 |
| `fetch_monthly_adjusted` | 한 ETF 월봉 요청 |
| `load_all_monthly_prices` | 전체 11개 ETF 순차 요청 |
| `build_price_matrix` | 조정종가 가격 행렬 생성 |
| `monthly_data_to_quotes` | 월봉의 최근 `close`를 평가가격으로 변환 |
| `monthly_data_from_saved_basis` | Sheet RAW를 월봉 DataFrame으로 복원 |
| `seed_session_quotes_from_saved_basis` | 앱 시작 시 저장 가격을 세션에 복원 |
| `store_latest_quotes` | 같은 티커의 세션 가격 갱신 |
| `add_non_strategy_session_quotes` | 전략 외 보유종목 수동가격 추가 |

## 39.5 포트폴리오

| 함수 | 역할 |
|---|---|
| `calculate_positions_from_trades` | 보유수량과 평균매수가 계산 |
| `build_portfolio_status` | 평가액, 손익, 비중, 총자산 계산 |

## 39.6 전략

| 함수 | 역할 |
|---|---|
| `calculate_returns` | 1·3·6·12개월 수익률 계산 |
| `calculate_vaa` | VAA 공격·방어 및 종목 선택 |
| `calculate_dual_momentum` | ODM 종목 선택 |
| `allocation_rows` | 전략별 목표배분 행 작성 |

## 39.7 최종 주문안

| 함수 | 역할 |
|---|---|
| `optimize_target_shares_min_cash` | 정수 주수와 잔여 현금 최적화 |
| `add_rebalance_plan` | 현재수량 비교 및 BUY·SELL·HOLD 계산 |
| `build_rebalance_basis_rows` | 모든 계산 근거와 RAW 행 생성 |
| `saved_rebalance_summary` | 저장 결과의 매수·매도·잔여현금 요약 |

## 39.8 매매일과 입력

| 함수 | 역할 |
|---|---|
| `latest_trade_date_for_tickers` | 대상 ETF 중 최근 거래일 검색 |
| `strategy_rebalance_dates_from_trades` | 전략별 최근일 계산 |
| `rebalance_schedule_preview_by_strategy` | 일정표 생성 |
| `normalize_trade_date` | 날짜 입력 형식 정리 |
| `prepare_batch_trade_rows` | 매매입력 검증용 보조 함수 |

---

# 40. 프로젝트 파일 구성 예시

```text
us-etf-asset-manager/
├─ app.py
├─ requirements.txt
├─ README.md
├─ .gitignore
└─ .streamlit/
   └─ secrets.toml        # GitHub 업로드 금지
```

## 40.1 `requirements.txt`

```text
streamlit
pandas
requests
python-dateutil
gspread
google-auth
```

## 40.2 `.gitignore`

```gitignore
.streamlit/secrets.toml
*.json
.venv/
__pycache__/
.DS_Store
```

---

# 41. 업데이트 시 점검사항

코드를 수정한 뒤 다음 항목을 확인하는 것이 좋습니다.

## 41.1 문법 검사

```bash
python -m py_compile app.py
```

## 41.2 실행 검사

```bash
streamlit run app.py
```

## 41.3 API 호출 수

현재 포트폴리오 기준 계산 한 번당 다음을 확인합니다.

- 호출 종목이 11개인지
- 같은 티커가 중복 호출되지 않는지
- `GLOBAL_QUOTE` 호출이 없는지
- 각 요청 사이에 1.25초 대기가 있는지

## 41.4 저장 검사

- `rebalance_plan`이 최신 주문안으로 덮어써지는지
- `rebalance_basis`에 근거와 RAW가 함께 저장되는지
- RAW가 ETF별 최근 최대 61개월인지
- 앱 재실행 후 저장가격이 복원되는지
- 수동 입력 계산에서 API가 호출되지 않는지

## 41.5 주수 최적화 검사

- 목표 주수가 정수인지
- 잔여 현금으로 살 수 있는 ETF가 남아 있지 않은지
- 목표 대비 비중차가 표시되는지
- 전략 외 종목은 목표 0주인지
- 가격 없는 종목이 다른 ETF로 임의 재배분되지 않는지

## 41.6 Google Sheets 호환성

컬럼을 추가할 때는 다음 상수와 저장·불러오기·표시 함수를 함께 수정해야 합니다.

- `TRADE_COLUMNS`
- `CASH_COLUMNS`
- `SETTINGS_COLUMNS`
- `REBALANCE_PLAN_COLUMNS`
- `REBALANCE_BASIS_COLUMNS`
- `save_rebalance_plan_to_sheet`
- `load_saved_rebalance_plan`
- `format_saved_rebalance_plan`
- `build_rebalance_basis_rows`
- `load_saved_rebalance_basis`
- `format_saved_rebalance_basis`

---

# 42. 면책사항

이 프로그램은 개인 자산관리와 투자전략 계산을 위한 참고 도구입니다.

- 투자수익을 보장하지 않습니다.
- 자동매매 기능이 아닙니다.
- 실제 주문 전에 가격, 수량, 환율, 거래비용을 확인해야 합니다.
- 데이터 제공업체의 지연, 누락, 오류가 있을 수 있습니다.
- 세금과 회계 처리는 세무전문가 또는 증권사 자료를 기준으로 확인해야 합니다.
- 평균매수가는 세금 신고용 취득원가와 다를 수 있습니다.
- 사용자는 계산 결과를 독립적으로 검토한 후 투자 판단을 내려야 합니다.

---

## 빠른 실행 요약

```bash
# 1. 가상환경
python -m venv .venv

# 2. 활성화: Windows
.venv\Scripts\activate

# 활성화: macOS/Linux
source .venv/bin/activate

# 3. 설치
pip install -r requirements.txt

# 4. 실행
streamlit run app.py
```

최초 실행 후 권장 순서:

```text
settings 환율 확인
→ 현금잔고 입력
→ 매매일지 입력
→ 현재 포트폴리오 기준 11개 ETF 신규 계산
→ 주문안 검토
→ 실제 매매
→ 체결내역과 현금 업데이트
```
