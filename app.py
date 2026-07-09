import calendar
import math
import time
from datetime import date, datetime
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st
from dateutil.relativedelta import relativedelta

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(page_title="미국 주식 자산관리 + ETF 리밸런싱", page_icon="📈", layout="wide")

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
API_CALL_DELAY_SECONDS = 1.25
CACHE_TTL_SECONDS = 12 * 60 * 60
GSHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 기존 ETF 자산배분 전략 구성
LAA_FIXED = ["IWD", "GLD", "IEF"]
LAA_VARIABLE = ["QQQ", "SHY"]
VAA_ATTACK = ["SPY", "EFA", "EEM", "AGG"]
VAA_SAFE = ["LQD", "IEF", "SHY"]
ODM_ASSETS = ["SPY", "EFA", "BIL", "AGG"]
ALL_TICKERS = sorted(set(LAA_FIXED + LAA_VARIABLE + VAA_ATTACK + VAA_SAFE + ODM_ASSETS))
DATA_TICKERS = sorted(set(VAA_ATTACK + VAA_SAFE + ODM_ASSETS))

ETF_LABELS = {
    "IWD": "미국 대형 가치주",
    "GLD": "금",
    "IEF": "미국 중기국채",
    "QQQ": "나스닥100",
    "SHY": "미국 단기국채",
    "SPY": "미국 S&P500",
    "EFA": "선진국 주식(미국 제외)",
    "EEM": "신흥국 주식",
    "AGG": "미국 종합채권",
    "LQD": "미국 투자등급 회사채",
    "BIL": "초단기 미국 국채",
}

TRADE_COLUMNS = ["trade_date", "ticker", "side", "quantity", "price_usd", "fee_usd", "memo", "created_at"]
CASH_COLUMNS = ["updated_at", "cash_usd", "cash_krw", "memo"]

# =========================================================
# 포맷/날짜 유틸
# =========================================================
def get_secret_api_key() -> str:
    try:
        return str(st.secrets["ALPHA_VANTAGE_API_KEY"]).strip()
    except Exception:
        return ""


def get_secret_sheet_id() -> str:
    try:
        return str(st.secrets["GOOGLE_SHEET_ID"]).strip()
    except Exception:
        return ""


def money_krw(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"{int(round(float(x))):,}원"


def money_usd(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"${float(x):,.2f}"


def usd_price(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"${float(x):,.2f}"


def fx_rate_krw(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"{float(x):,.2f}원/USD"


def format_pct(x: float, digits: int = 2) -> str:
    if pd.isna(x):
        return "-"
    return f"{float(x) * 100:.{digits}f}%"


def format_score(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"{float(x):.4f}"


def format_fractional_shares(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"{float(x):,.2f}주"


def pct_cols(df: pd.DataFrame, cols: List[str], digits: int = 2) -> pd.DataFrame:
    show = df.copy()
    for col in cols:
        if col in show.columns:
            show[col] = show[col].apply(lambda x: format_pct(x, digits=digits))
    return show


def is_last_day(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def add_months(d: date, months: int = 1) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    last_day_target_month = calendar.monthrange(year, month)[1]
    day = last_day_target_month if is_last_day(d) else min(d.day, last_day_target_month)
    return date(year, month, day)


def add_years(d: date, years: int = 1) -> date:
    try:
        return date(d.year + years, d.month, d.day)
    except ValueError:
        return date(d.year + years, d.month, calendar.monthrange(d.year + years, d.month)[1])


def next_rebalance_date(last_date: date, cycle: str) -> date:
    if cycle == "연 1회":
        return add_years(last_date, 1)
    if cycle == "월 1회":
        return add_months(last_date, 1)
    raise ValueError(f"지원하지 않는 리밸런싱 주기입니다: {cycle}")


def rebalance_status(next_date: date, eval_date: date) -> str:
    return "리밸런싱 필요" if next_date <= eval_date else "대기"


def normalize_strategy_weights(w_laa: float, w_vaa: float, w_odm: float) -> Tuple[float, float, float, float]:
    total = w_laa + w_vaa + w_odm
    if total <= 0:
        return w_laa, w_vaa, w_odm, total
    return w_laa / total, w_vaa / total, w_odm / total, total

# =========================================================
# Google Sheets 저장소
# =========================================================
@st.cache_resource(show_spinner=False)
def get_google_spreadsheet():
    sheet_id = get_secret_sheet_id()
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID가 Streamlit Secrets에 없습니다.")
    if gspread is None or Credentials is None:
        raise RuntimeError("gspread/google-auth 패키지가 설치되지 않았습니다. requirements.txt를 확인하세요.")
    try:
        service_account_info = dict(st.secrets["gcp_service_account"])
    except Exception as e:
        raise RuntimeError("gcp_service_account 정보가 Streamlit Secrets에 없습니다.") from e

    if "private_key" in service_account_info:
        service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")

    creds = Credentials.from_service_account_info(service_account_info, scopes=GSHEET_SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def ensure_worksheet(sheet_name: str, columns: List[str]):
    sh = get_google_spreadsheet()
    try:
        ws = sh.worksheet(sheet_name)
    except Exception:
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=max(10, len(columns)))
        ws.update([columns], "A1")
        return ws

    values = ws.get_all_values()
    if not values:
        ws.update([columns], "A1")
    elif values[0] != columns:
        ws.update([columns], "A1")
    return ws


def load_sheet(sheet_name: str, columns: List[str]) -> pd.DataFrame:
    ws = ensure_worksheet(sheet_name, columns)
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=columns)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def append_sheet_row(sheet_name: str, columns: List[str], row: Dict[str, object]) -> None:
    ws = ensure_worksheet(sheet_name, columns)
    values = [row.get(col, "") for col in columns]
    ws.append_row(values, value_input_option="USER_ENTERED")


def overwrite_sheet(sheet_name: str, columns: List[str], df: pd.DataFrame) -> None:
    ws = ensure_worksheet(sheet_name, columns)
    clean = df.copy()
    for col in columns:
        if col not in clean.columns:
            clean[col] = ""
    clean = clean[columns].fillna("")
    values = [columns] + clean.astype(str).values.tolist()
    ws.clear()
    ws.update(values, "A1")


def load_trades() -> pd.DataFrame:
    df = load_sheet("trades", TRADE_COLUMNS)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["side"] = df["side"].astype(str).str.upper().str.strip()
    for col in ["quantity", "price_usd", "fee_usd"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def load_cash() -> pd.DataFrame:
    df = load_sheet("cash", CASH_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=CASH_COLUMNS)
    for col in ["cash_usd", "cash_krw"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def get_latest_cash_balance() -> Dict[str, float]:
    cash_df = load_cash()
    if cash_df.empty:
        return {"cash_usd": 0.0, "cash_krw": 0.0, "memo": ""}
    row = cash_df.iloc[-1]
    return {
        "cash_usd": float(row.get("cash_usd", 0.0) or 0.0),
        "cash_krw": float(row.get("cash_krw", 0.0) or 0.0),
        "memo": str(row.get("memo", "") or ""),
    }

# =========================================================
# Alpha Vantage 데이터 수집
# =========================================================
def check_alpha_error(symbol: str, data: Dict[str, object]) -> None:
    if "Error Message" in data:
        raise ValueError(f"{symbol}: Alpha Vantage 오류 - {data['Error Message']}")
    if "Note" in data:
        raise ValueError(f"{symbol}: Alpha Vantage 호출 제한 메시지 - {data['Note']}")
    if "Information" in data:
        raise ValueError(f"{symbol}: Alpha Vantage 안내/호출 제한 메시지 - {data['Information']}")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_monthly_adjusted(symbol: str, api_key: str) -> pd.DataFrame:
    params = {"function": "TIME_SERIES_MONTHLY_ADJUSTED", "symbol": symbol, "apikey": api_key}
    response = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    check_alpha_error(symbol, data)

    key = "Monthly Adjusted Time Series"
    if key not in data:
        raise ValueError(f"{symbol}: 월봉 데이터를 찾지 못했습니다. 응답 키: {list(data.keys())}")

    df = pd.DataFrame.from_dict(data[key], orient="index")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df.rename(columns={
        "1. open": "open", "2. high": "high", "3. low": "low", "4. close": "close",
        "5. adjusted close": "adjusted_close", "6. volume": "volume", "7. dividend amount": "dividend",
    })
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "adjusted_close" not in df.columns:
        raise ValueError(f"{symbol}: adjusted_close 컬럼이 없습니다.")
    df["symbol"] = symbol
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_usdkrw_daily(api_key: str) -> pd.DataFrame:
    params = {"function": "FX_DAILY", "from_symbol": "USD", "to_symbol": "KRW", "outputsize": "compact", "apikey": api_key}
    response = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    check_alpha_error("USD/KRW", data)

    key = "Time Series FX (Daily)"
    if key not in data:
        raise ValueError(f"USD/KRW: 일별 환율 데이터를 찾지 못했습니다. 응답 키: {list(data.keys())}")
    df = pd.DataFrame.from_dict(data[key], orient="index")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df.rename(columns={"1. open": "open", "2. high": "high", "3. low": "low", "4. close": "close"})
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def select_fx_rate(fx_df: pd.DataFrame, eval_date: date) -> Tuple[float, pd.Timestamp]:
    eval_ts = pd.Timestamp(eval_date)
    usable = fx_df.loc[fx_df.index <= eval_ts].dropna(subset=["close"])
    if usable.empty:
        usable = fx_df.dropna(subset=["close"])
    if usable.empty:
        raise ValueError("USD/KRW 환율 close 데이터를 찾지 못했습니다.")
    rate_date = usable.index.max()
    rate = float(usable.loc[rate_date, "close"])
    if rate <= 0:
        raise ValueError("USD/KRW 환율이 0 이하입니다. 데이터를 확인하세요.")
    return rate, rate_date


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_global_quote(symbol: str, api_key: str) -> Dict[str, object]:
    params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key}
    response = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    check_alpha_error(symbol, data)

    quote = data.get("Global Quote", {})
    if not quote:
        raise ValueError(f"{symbol}: 최신 가격 데이터를 찾지 못했습니다. 응답 키: {list(data.keys())}")

    price = pd.to_numeric(quote.get("05. price"), errors="coerce")
    latest_trading_day = quote.get("07. latest trading day")
    if pd.isna(price) or float(price) <= 0:
        raise ValueError(f"{symbol}: 유효한 최신 가격을 찾지 못했습니다.")
    return {"ticker": symbol, "latest_price_usd": float(price), "price_date": latest_trading_day or "-"}


def load_latest_quotes(tickers: List[str], api_key: str) -> pd.DataFrame:
    rows, errors = [], []
    tickers = sorted(set([str(t).upper().strip() for t in tickers if str(t).strip()]))
    if not tickers:
        return pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date"])

    progress = st.progress(0, text="최신 종가를 불러오는 중입니다.")
    for i, ticker in enumerate(tickers, start=1):
        try:
            rows.append(fetch_global_quote(ticker, api_key))
        except Exception as e:
            errors.append(str(e))
            rows.append({"ticker": ticker, "latest_price_usd": pd.NA, "price_date": "-"})
        progress.progress(i / len(tickers), text=f"최신 가격 로딩: {ticker} ({i}/{len(tickers)})")
        if i < len(tickers):
            time.sleep(API_CALL_DELAY_SECONDS)
    progress.empty()
    if errors:
        with st.expander("가격 로딩 오류 보기", expanded=True):
            for err in errors:
                st.error(err)
    return pd.DataFrame(rows)


def load_all_monthly_prices(tickers: List[str], api_key: str) -> Dict[str, pd.DataFrame]:
    result, errors = {}, []
    progress = st.progress(0, text="Alpha Vantage에서 ETF 월봉 데이터를 불러오는 중입니다.")
    for i, ticker in enumerate(tickers, start=1):
        try:
            result[ticker] = fetch_monthly_adjusted(ticker, api_key)
        except Exception as e:
            errors.append(str(e))
        progress.progress(i / len(tickers), text=f"ETF 데이터 로딩: {ticker} ({i}/{len(tickers)})")
        if i < len(tickers):
            time.sleep(API_CALL_DELAY_SECONDS)
    progress.empty()
    if errors:
        with st.expander("ETF 월봉 데이터 로딩 오류 보기", expanded=True):
            for err in errors:
                st.error(err)
    return result


def build_price_matrix(data: Dict[str, pd.DataFrame], tickers: List[str], eval_date: date, lookback_months: int, exclude_current_month: bool = True) -> pd.DataFrame:
    closes = []
    for ticker in tickers:
        if ticker in data:
            closes.append(data[ticker]["adjusted_close"].rename(ticker))
    if not closes:
        return pd.DataFrame()
    prices = pd.concat(closes, axis=1).sort_index().dropna(how="all")
    eval_ts = pd.Timestamp(eval_date)
    prices = prices.loc[prices.index <= eval_ts]
    if exclude_current_month and not prices.empty:
        latest = prices.index.max()
        if latest.year == eval_ts.year and latest.month == eval_ts.month:
            prices = prices.loc[prices.index < latest]
    if lookback_months > 0:
        prices = prices.tail(lookback_months)
    return prices

# =========================================================
# 포트폴리오 계산
# =========================================================
def calculate_positions_from_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["ticker", "quantity", "buy_qty", "gross_buy_usd", "avg_buy_price_usd"])
    df = trades.copy()
    df["signed_qty"] = 0.0
    df.loc[df["side"] == "BUY", "signed_qty"] = df["quantity"]
    df.loc[df["side"] == "SELL", "signed_qty"] = -df["quantity"]
    df.loc[df["side"] == "ADJUST", "signed_qty"] = df["quantity"]
    df["gross_buy_usd"] = 0.0
    df.loc[df["side"] == "BUY", "gross_buy_usd"] = df["quantity"] * df["price_usd"] + df["fee_usd"]
    df["buy_qty"] = 0.0
    df.loc[df["side"] == "BUY", "buy_qty"] = df["quantity"]

    grouped = df.groupby("ticker", as_index=False).agg(
        quantity=("signed_qty", "sum"),
        buy_qty=("buy_qty", "sum"),
        gross_buy_usd=("gross_buy_usd", "sum"),
    )
    grouped = grouped[grouped["quantity"].abs() > 1e-9].copy()
    grouped["avg_buy_price_usd"] = grouped.apply(
        lambda r: r["gross_buy_usd"] / r["buy_qty"] if r["buy_qty"] > 0 else pd.NA, axis=1
    )
    return grouped.sort_values("ticker")


def build_portfolio_status(positions: pd.DataFrame, quotes: pd.DataFrame, cash: Dict[str, float], usdkrw_rate: float) -> Tuple[pd.DataFrame, Dict[str, float]]:
    pos = positions.copy() if not positions.empty else pd.DataFrame(columns=["ticker", "quantity", "avg_buy_price_usd"])
    if quotes.empty:
        quotes = pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date"])
    pos = pos.merge(quotes, on="ticker", how="left")
    pos["market_value_usd"] = pos["quantity"] * pd.to_numeric(pos["latest_price_usd"], errors="coerce")
    pos["market_value_krw"] = pos["market_value_usd"] * usdkrw_rate
    pos["unrealized_pnl_usd"] = (pos["latest_price_usd"] - pos["avg_buy_price_usd"]) * pos["quantity"]
    pos["unrealized_pnl_pct"] = (pos["latest_price_usd"] / pos["avg_buy_price_usd"] - 1).where(pos["avg_buy_price_usd"] > 0)

    stock_value_usd = float(pos["market_value_usd"].dropna().sum()) if not pos.empty else 0.0
    cash_usd = float(cash.get("cash_usd", 0.0))
    cash_krw = float(cash.get("cash_krw", 0.0))
    cash_krw_as_usd = cash_krw / usdkrw_rate if usdkrw_rate > 0 else 0.0
    total_usd = stock_value_usd + cash_usd + cash_krw_as_usd
    total_krw = total_usd * usdkrw_rate

    pos["weight"] = pos["market_value_usd"] / total_usd if total_usd > 0 else pd.NA
    summary = {
        "stock_value_usd": stock_value_usd,
        "cash_usd": cash_usd,
        "cash_krw": cash_krw,
        "cash_krw_as_usd": cash_krw_as_usd,
        "total_usd": total_usd,
        "total_krw": total_krw,
    }
    return pos.sort_values("market_value_usd", ascending=False), summary

# =========================================================
# 전략 계산 함수
# =========================================================
def calculate_returns(prices: pd.DataFrame, tickers: List[str]) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        if ticker not in prices.columns:
            rows.append({"ETF": ticker, "자산군": ETF_LABELS.get(ticker, ""), "1개월 수익률": pd.NA, "3개월 수익률": pd.NA, "6개월 수익률": pd.NA, "12개월 수익률": pd.NA})
            continue
        s = prices[ticker].dropna()
        if len(s) < 13:
            rows.append({
                "ETF": ticker, "자산군": ETF_LABELS.get(ticker, ""),
                "기준월": s.index[-1].strftime("%Y-%m-%d") if len(s) else "-",
                "현재 조정종가": s.iloc[-1] if len(s) else pd.NA,
                "1개월 수익률": pd.NA, "3개월 수익률": pd.NA, "6개월 수익률": pd.NA, "12개월 수익률": pd.NA,
            })
            continue
        rows.append({
            "ETF": ticker, "자산군": ETF_LABELS.get(ticker, ""), "기준월": s.index[-1].strftime("%Y-%m-%d"),
            "현재 조정종가": s.iloc[-1],
            "1개월 수익률": s.iloc[-1] / s.iloc[-2] - 1,
            "3개월 수익률": s.iloc[-1] / s.iloc[-4] - 1,
            "6개월 수익률": s.iloc[-1] / s.iloc[-7] - 1,
            "12개월 수익률": s.iloc[-1] / s.iloc[-13] - 1,
        })
    return pd.DataFrame(rows)


def calculate_vaa(prices: pd.DataFrame, zero_is_defensive: bool) -> Tuple[str, pd.DataFrame, str]:
    tickers = VAA_ATTACK + VAA_SAFE
    returns = calculate_returns(prices, tickers)
    need_cols = ["1개월 수익률", "3개월 수익률", "6개월 수익률", "12개월 수익률"]
    if returns.empty or returns[need_cols].isna().any().any():
        raise ValueError("VAA 계산에 필요한 13개월 이상의 월봉 데이터가 부족합니다.")
    returns["모멘텀 스코어"] = 12 * returns["1개월 수익률"] + 4 * returns["3개월 수익률"] + 2 * returns["6개월 수익률"] + returns["12개월 수익률"]
    returns["구분"] = returns["ETF"].apply(lambda x: "공격형" if x in VAA_ATTACK else "안전자산")
    attack = returns[returns["ETF"].isin(VAA_ATTACK)].copy()
    safe = returns[returns["ETF"].isin(VAA_SAFE)].copy()
    if zero_is_defensive:
        attack_ok = bool((attack["모멘텀 스코어"] > 0).all())
        threshold_text = "공격형 4개 ETF의 모멘텀 스코어가 모두 0 초과"
    else:
        attack_ok = bool((attack["모멘텀 스코어"] >= 0).all())
        threshold_text = "공격형 4개 ETF의 모멘텀 스코어가 모두 0 이상"
    if attack_ok:
        pool = attack
        reason = f"{threshold_text} → 공격형 중 최고 점수 ETF 선택"
    else:
        pool = safe
        reason = f"{threshold_text} 조건 미충족 → 안전자산 중 최고 점수 ETF 선택"
    selected = pool.sort_values("모멘텀 스코어", ascending=False).iloc[0]["ETF"]
    return selected, returns.sort_values(["구분", "모멘텀 스코어"], ascending=[True, False]), reason


def calculate_dual_momentum(prices: pd.DataFrame) -> Tuple[str, pd.DataFrame, str]:
    returns = calculate_returns(prices, ODM_ASSETS)
    if returns.empty or returns["12개월 수익률"].isna().any():
        raise ValueError("오리지널 듀얼 모멘텀 계산에 필요한 13개월 이상의 월봉 데이터가 부족합니다.")
    r = returns.set_index("ETF")["12개월 수익률"]
    spy_r, efa_r, bil_r = float(r["SPY"]), float(r["EFA"]), float(r["BIL"])
    if spy_r > bil_r:
        selected = "SPY" if spy_r >= efa_r else "EFA"
        reason = "SPY 12개월 수익률이 BIL보다 높아 SPY/EFA 중 더 강한 ETF 선택"
    else:
        selected = "AGG"
        reason = "SPY 12개월 수익률이 BIL보다 낮거나 같아 AGG 선택"
    return selected, returns.sort_values("12개월 수익률", ascending=False), reason


def allocation_rows(strategy: str, strategy_weight: float, inner_weights: Dict[str, float], rebalance_info: Dict[str, Dict[str, object]], eval_date: date, total_investment_krw: float, total_investment_usd: float, input_currency: str, reason: str = "") -> List[Dict[str, object]]:
    rows = []
    for ticker, inner_weight in inner_weights.items():
        info = rebalance_info[ticker]
        cycle = str(info["cycle"])
        last_date = info["last_date"]
        next_date = next_rebalance_date(last_date, cycle)
        total_weight = strategy_weight * inner_weight
        rows.append({
            "하위전략": strategy,
            "ETF": ticker,
            "자산군": ETF_LABELS.get(ticker, ""),
            "하위전략 내 비중": inner_weight,
            "전략 전체 비중": total_weight,
            "목표 투자금(KRW)": total_investment_krw * total_weight,
            "목표 투자금(USD)": total_investment_usd * total_weight,
            "배분 기준 화폐": input_currency,
            "리밸런싱 주기": cycle,
            "최근 리밸런싱일": last_date,
            "다음 리밸런싱일": next_date,
            "리밸런싱 상태": rebalance_status(next_date, eval_date),
            "선정 사유": reason,
        })
    return rows


def add_rebalance_plan(target_df: pd.DataFrame, quote_df: pd.DataFrame, positions_df: pd.DataFrame, usdkrw_rate: float) -> pd.DataFrame:
    target = target_df.copy()
    if "ETF" in target.columns:
        target = target.rename(columns={"ETF": "ticker"})
    target["ticker"] = target["ticker"].astype(str).str.upper().str.strip()

    quote = quote_df.copy()
    quote["ticker"] = quote["ticker"].astype(str).str.upper().str.strip()

    if positions_df.empty:
        current = pd.DataFrame(columns=["ticker", "quantity", "market_value_usd", "weight"])
    else:
        current = positions_df[["ticker", "quantity", "market_value_usd", "weight"]].copy()
        current["ticker"] = current["ticker"].astype(str).str.upper().str.strip()

    # 전략 목표에 없는 기존 보유종목은 목표 0으로 추가해 매도 후보로 표시
    existing_tickers = set(current["ticker"].dropna().tolist())
    target_tickers = set(target["ticker"].dropna().tolist())
    extra_tickers = sorted(existing_tickers - target_tickers)
    if extra_tickers:
        extras = []
        for t in extra_tickers:
            extras.append({
                "ticker": t, "자산군": "전략 외 보유", "전략 전체 비중": 0.0,
                "목표 투자금(KRW)": 0.0, "목표 투자금(USD)": 0.0,
                "다음 리밸런싱일": "-", "리밸런싱 상태": "전략 외",
            })
        target = pd.concat([target, pd.DataFrame(extras)], ignore_index=True)

    plan = target.merge(current, on="ticker", how="left").merge(quote, on="ticker", how="left")
    plan["quantity"] = pd.to_numeric(plan["quantity"], errors="coerce").fillna(0.0)
    plan["latest_price_usd"] = pd.to_numeric(plan["latest_price_usd"], errors="coerce")
    plan["market_value_usd"] = pd.to_numeric(plan["market_value_usd"], errors="coerce").fillna(0.0)
    plan["현재 평가액(KRW)"] = plan["market_value_usd"] * usdkrw_rate

    target_shares, trade_shares, trade_action, trade_usd, trade_krw = [], [], [], [], []
    for _, row in plan.iterrows():
        target_usd = pd.to_numeric(row.get("목표 투자금(USD)"), errors="coerce")
        price = pd.to_numeric(row.get("latest_price_usd"), errors="coerce")
        cur_qty = float(row.get("quantity", 0.0) or 0.0)
        if pd.isna(target_usd) or pd.isna(price) or float(price) <= 0:
            target_shares.append(pd.NA)
            trade_shares.append(pd.NA)
            trade_action.append("가격 확인 필요")
            trade_usd.append(pd.NA)
            trade_krw.append(pd.NA)
            continue
        ts = math.floor(float(target_usd) / float(price))
        delta = ts - cur_qty
        if abs(delta) < 1e-9:
            action = "HOLD"
        elif delta > 0:
            action = "BUY"
        else:
            action = "SELL"
        amount_usd = abs(delta) * float(price)
        target_shares.append(ts)
        trade_shares.append(delta)
        trade_action.append(action)
        trade_usd.append(amount_usd)
        trade_krw.append(amount_usd * usdkrw_rate)
    plan["목표 주수"] = target_shares
    plan["현재 주수"] = plan["quantity"]
    plan["매매 구분"] = trade_action
    plan["매매 필요 주수"] = trade_shares
    plan["매매 필요 금액(USD)"] = trade_usd
    plan["매매 필요 금액(KRW)"] = trade_krw
    return plan.sort_values(["매매 구분", "전략 전체 비중"], ascending=[True, False])


def appendix_buy_strategy_table() -> pd.DataFrame:
    return pd.DataFrame([
        {"전략": "LAA", "대상 ETF": "IWD, GLD, IEF, QQQ, SHY", "매수 전략": "IWD/GLD/IEF 각 25% 고정. 나머지 25%는 S&P500 200일선 하회와 미국 실업률 12개월 평균 상회가 동시에 O이면 SHY, 아니면 QQQ.", "리밸런싱": "IWD/GLD/IEF 연 1회, QQQ/SHY 월 1회"},
        {"전략": "VAA 공격형", "대상 ETF": "공격형: SPY, EFA, EEM, AGG / 안전자산: LQD, IEF, SHY", "매수 전략": "공격형 4개 ETF의 모멘텀 스코어가 모두 양호하면 공격형 1위 100%, 하나라도 방어 신호면 안전자산 1위 100%.", "리밸런싱": "월 1회"},
        {"전략": "오리지널 듀얼 모멘텀", "대상 ETF": "SPY, EFA, BIL, AGG", "매수 전략": "SPY 12개월 수익률이 BIL보다 높으면 SPY/EFA 중 높은 ETF 100%, 낮거나 같으면 AGG 100%.", "리밸런싱": "월 1회"},
    ])

# =========================================================
# 화면 시작
# =========================================================
st.title("미국 주식 자산관리 + ETF 자산배분 리밸런싱")
st.caption("Google Sheets에 현금/매매일지를 저장하고, Alpha Vantage 최신 종가로 현재 자산과 ETF 전략 리밸런싱 주문안을 계산합니다.")

api_key = get_secret_api_key()
sheet_id = get_secret_sheet_id()
today = date.today()

with st.sidebar:
    st.header("공통 설정")
    if api_key:
        st.success("Alpha Vantage API Key 확인")
    else:
        st.error("ALPHA_VANTAGE_API_KEY 필요")
    if sheet_id:
        st.success("Google Sheet ID 확인")
    else:
        st.error("GOOGLE_SHEET_ID 필요")
    eval_date = st.date_input("평가 기준일", value=today)
    if st.button("캐시 초기화"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("캐시를 초기화했습니다.")

if not api_key:
    st.warning("Streamlit Secrets에 ALPHA_VANTAGE_API_KEY를 저장한 뒤 실행하세요.")
    st.stop()
if not sheet_id:
    st.warning("Streamlit Secrets에 GOOGLE_SHEET_ID와 gcp_service_account 정보를 저장한 뒤 실행하세요.")
    st.stop()

# 환율은 자산 탭/전략 탭 모두 필요하므로 먼저 조회
try:
    fx_df = fetch_usdkrw_daily(api_key)
    usdkrw_rate, usdkrw_rate_date = select_fx_rate(fx_df, eval_date)
except Exception as e:
    st.error(f"USD/KRW 환율 데이터를 가져오지 못했습니다: {e}")
    st.stop()

try:
    trades_df = load_trades()
    cash_balance = get_latest_cash_balance()
except Exception as e:
    st.error(f"Google Sheets 연결/초기화 오류: {e}")
    st.info("서비스 계정 이메일을 Google Sheet에 편집자로 공유했는지 확인하세요.")
    st.stop()

positions_base = calculate_positions_from_trades(trades_df)
portfolio_quotes = load_latest_quotes(positions_base["ticker"].tolist(), api_key) if not positions_base.empty else pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date"])
portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)

tab_assets, tab_strategy, tab_setup = st.tabs(["1) 자산/매매일지", "2) ETF 자산배분 리밸런싱", "3) 설정/배포 가이드"])

# =========================================================
# 1) 자산/매매일지
# =========================================================
with tab_assets:
    st.subheader("현재 자산 현황")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 자산(USD)", money_usd(portfolio_summary["total_usd"]))
    c2.metric("총 자산(KRW)", money_krw(portfolio_summary["total_krw"]))
    c3.metric("주식/ETF 평가액", money_usd(portfolio_summary["stock_value_usd"]))
    c4.metric("적용 환율", fx_rate_krw(usdkrw_rate))
    st.caption(f"환율 기준일: {usdkrw_rate_date.strftime('%Y-%m-%d')}")

    st.markdown("#### 현금 잔고 저장")
    with st.form("cash_form"):
        cc1, cc2, cc3 = st.columns([1, 1, 2])
        cash_usd_input = cc1.number_input("현금 USD", value=float(cash_balance.get("cash_usd", 0.0)), step=1.0, format="%.2f")
        cash_krw_input = cc2.number_input("현금 KRW", value=float(cash_balance.get("cash_krw", 0.0)), step=1000.0, format="%.0f")
        cash_memo = cc3.text_input("현금 메모", value=str(cash_balance.get("memo", "")))
        save_cash = st.form_submit_button("현금 잔고 저장")
    if save_cash:
        append_sheet_row("cash", CASH_COLUMNS, {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cash_usd": cash_usd_input,
            "cash_krw": cash_krw_input,
            "memo": cash_memo,
        })
        st.success("현금 잔고를 Google Sheets에 저장했습니다. 새로고침하면 반영됩니다.")

    st.markdown("#### 매매일지 입력")
    with st.form("trade_form"):
        tc1, tc2, tc3, tc4, tc5, tc6 = st.columns([1, 1, 1, 1, 1, 2])
        trade_date = tc1.date_input("매매일", value=today)
        ticker = tc2.text_input("티커", value="SPY").upper().strip()
        side = tc3.selectbox("구분", options=["BUY", "SELL", "ADJUST"], help="ADJUST는 입고/출고/수량 보정용입니다. 수량에 음수를 넣으면 보유수량이 줄어듭니다.")
        quantity = tc4.number_input("수량", value=1.0, step=1.0, format="%.6f")
        price_usd = tc5.number_input("체결가(USD)", value=0.0, step=0.01, format="%.4f")
        fee_usd = tc5.number_input("수수료(USD)", value=0.0, step=0.01, format="%.4f")
        memo = tc6.text_input("메모", value="")
        add_trade = st.form_submit_button("매매일지 추가")
    if add_trade:
        if not ticker:
            st.error("티커를 입력하세요.")
        elif side in ["BUY", "SELL"] and quantity <= 0:
            st.error("BUY/SELL 수량은 0보다 커야 합니다.")
        else:
            append_sheet_row("trades", TRADE_COLUMNS, {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "price_usd": price_usd,
                "fee_usd": fee_usd,
                "memo": memo,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            st.success(f"{ticker} {side} 내역을 저장했습니다. 새로고침하면 반영됩니다.")

    st.markdown("#### 종목별 보유 현황")
    show_portfolio = portfolio_status.copy()
    if not show_portfolio.empty:
        show_portfolio = show_portfolio.rename(columns={
            "ticker": "티커", "quantity": "보유수량", "avg_buy_price_usd": "평균매수가(USD)",
            "latest_price_usd": "최근가(USD)", "price_date": "가격 기준일", "market_value_usd": "평가액(USD)",
            "market_value_krw": "평가액(KRW)", "weight": "비중", "unrealized_pnl_usd": "평가손익(USD)", "unrealized_pnl_pct": "평가손익률",
        })
        for col in ["평균매수가(USD)", "최근가(USD)"]:
            show_portfolio[col] = show_portfolio[col].apply(usd_price)
        show_portfolio["평가액(USD)"] = show_portfolio["평가액(USD)"].apply(money_usd)
        show_portfolio["평가액(KRW)"] = show_portfolio["평가액(KRW)"].apply(money_krw)
        show_portfolio["비중"] = show_portfolio["비중"].apply(format_pct)
        show_portfolio["평가손익(USD)"] = show_portfolio["평가손익(USD)"].apply(money_usd)
        show_portfolio["평가손익률"] = show_portfolio["평가손익률"].apply(format_pct)
    st.dataframe(show_portfolio, use_container_width=True, hide_index=True)

    if not portfolio_status.empty:
        st.bar_chart(portfolio_status.set_index("ticker")["weight"])

    st.markdown("#### 매매일지 확인/수정")
    st.caption("아래 표를 직접 수정한 뒤 '수정 내용 저장'을 누르면 Google Sheets의 trades 시트가 덮어쓰기 됩니다.")
    editable = trades_df.copy()
    edited = st.data_editor(editable, use_container_width=True, hide_index=True, num_rows="dynamic")
    if st.button("수정 내용 저장"):
        overwrite_sheet("trades", TRADE_COLUMNS, edited)
        st.success("매매일지를 저장했습니다. 새로고침하면 다시 계산됩니다.")

# =========================================================
# 2) ETF 자산배분 리밸런싱
# =========================================================
with tab_strategy:
    st.subheader("ETF 전략 입력")
    with st.expander("전략 구성 확인", expanded=False):
        st.dataframe(appendix_buy_strategy_table(), use_container_width=True, hide_index=True)

    s1, s2 = st.columns(2)
    with s1:
        investment_basis = st.radio(
            "리밸런싱 기준 투자금",
            options=["현재 포트폴리오 총자산 사용", "수동 입력"],
            index=0,
            help="현재 포트폴리오 총자산은 Google Sheets의 현금 + 매매일지 기반 보유수량 × 최신가로 계산합니다.",
        )
        investment_currency = st.radio("수동 입력 화폐", options=["KRW", "USD"], horizontal=True)
        manual_amount = st.number_input("수동 총 투자금", min_value=0.0, value=10_000.0 if investment_currency == "USD" else 10_000_000.0, step=1.0)
        lookback_months = st.slider("데이터 조회기간", min_value=13, max_value=60, value=15)
        exclude_current_month = st.checkbox("진행 중인 월 데이터 제외", value=True)
    with s2:
        laa_defensive = st.radio(
            "LAA 조건: S&P500 200일선 하회 + 미국 실업률 12개월 평균 상회 여부",
            options=[False, True],
            format_func=lambda x: "X: 조건 미충족 → QQQ" if not x else "O: 조건 충족 → SHY",
            index=0,
        )
        default_monthly_last = today - relativedelta(months=1)
        default_annual_last = today - relativedelta(years=1)
        laa_annual_last = st.date_input("LAA 고정자산 최근 리밸런싱일", value=default_annual_last)
        laa_monthly_last = st.date_input("LAA 변동자산 최근 리밸런싱일", value=default_monthly_last)
        vaa_monthly_last = st.date_input("VAA 최근 리밸런싱일", value=default_monthly_last)
        odm_monthly_last = st.date_input("오리지널 듀얼 모멘텀 최근 리밸런싱일", value=default_monthly_last)
        zero_is_defensive = st.checkbox("VAA 모멘텀 스코어 0점은 방어 신호로 처리", value=True)

    st.markdown("#### 하위전략 비중")
    wc1, wc2, wc3 = st.columns(3)
    w_laa_input = wc1.number_input("LAA 비중", min_value=0.0, max_value=1.0, value=1 / 3, step=0.01, format="%.4f")
    w_vaa_input = wc2.number_input("VAA 비중", min_value=0.0, max_value=1.0, value=1 / 3, step=0.01, format="%.4f")
    w_odm_input = wc3.number_input("ODM 비중", min_value=0.0, max_value=1.0, value=1 / 3, step=0.01, format="%.4f")
    w_laa, w_vaa, w_odm, weight_sum = normalize_strategy_weights(w_laa_input, w_vaa_input, w_odm_input)
    if weight_sum <= 0:
        st.error("하위전략 비중 합계가 0입니다.")
        st.stop()
    if abs(weight_sum - 1.0) > 1e-8:
        st.warning(f"하위전략 비중 합계가 {weight_sum:.4f}입니다. 계산 시 100%로 자동 정규화합니다.")

    if investment_basis == "현재 포트폴리오 총자산 사용":
        total_investment_usd = portfolio_summary["total_usd"]
        total_investment_krw = portfolio_summary["total_krw"]
        input_currency = "PORTFOLIO"
    else:
        if investment_currency == "KRW":
            total_investment_krw = float(manual_amount)
            total_investment_usd = total_investment_krw / usdkrw_rate
        else:
            total_investment_usd = float(manual_amount)
            total_investment_krw = total_investment_usd * usdkrw_rate
        input_currency = investment_currency

    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("리밸런싱 기준금액(USD)", money_usd(total_investment_usd))
    pc2.metric("리밸런싱 기준금액(KRW)", money_krw(total_investment_krw))
    pc3.metric("현재 포트폴리오 총자산", money_usd(portfolio_summary["total_usd"]))
    pc4.metric("적용 환율", fx_rate_krw(usdkrw_rate))

    run = st.button("ETF 전략 계산 및 현재 보유수량 반영", type="primary")
    if run:
        if total_investment_usd <= 0:
            st.error("리밸런싱 기준 투자금이 0입니다. 현금/매매일지를 입력하거나 수동 금액을 입력하세요.")
            st.stop()

        data = load_all_monthly_prices(DATA_TICKERS, api_key)
        prices = build_price_matrix(data, DATA_TICKERS, eval_date, lookback_months, exclude_current_month)
        if prices.empty:
            st.error("ETF 월봉 데이터를 가져오지 못했습니다. API Key/호출 제한/기준일을 확인하세요.")
            st.stop()
        actual_eval_dt = prices.index.max()
        st.success(f"전략 계산 기준월: {actual_eval_dt.strftime('%Y-%m-%d')}")

        rows: List[Dict[str, object]] = []
        laa_variable = "SHY" if laa_defensive else "QQQ"
        laa_reason = "두 조건이 모두 충족되어 SHY 선택" if laa_defensive else "두 조건이 동시에 충족되지 않아 QQQ 선택"
        laa_inner = {"IWD": 0.25, "GLD": 0.25, "IEF": 0.25, laa_variable: 0.25}
        laa_rebalance = {
            "IWD": {"cycle": "연 1회", "last_date": laa_annual_last},
            "GLD": {"cycle": "연 1회", "last_date": laa_annual_last},
            "IEF": {"cycle": "연 1회", "last_date": laa_annual_last},
            laa_variable: {"cycle": "월 1회", "last_date": laa_monthly_last},
        }
        rows += allocation_rows("LAA", w_laa, laa_inner, laa_rebalance, eval_date, total_investment_krw, total_investment_usd, input_currency, laa_reason)

        try:
            vaa_selected, vaa_scores, vaa_reason = calculate_vaa(prices, zero_is_defensive)
            odm_selected, odm_returns, odm_reason = calculate_dual_momentum(prices)
        except Exception as e:
            st.error(str(e))
            st.stop()

        rows += allocation_rows("VAA 공격형", w_vaa, {vaa_selected: 1.0}, {vaa_selected: {"cycle": "월 1회", "last_date": vaa_monthly_last}}, eval_date, total_investment_krw, total_investment_usd, input_currency, vaa_reason)
        rows += allocation_rows("오리지널 듀얼 모멘텀", w_odm, {odm_selected: 1.0}, {odm_selected: {"cycle": "월 1회", "last_date": odm_monthly_last}}, eval_date, total_investment_krw, total_investment_usd, input_currency, odm_reason)

        st.markdown("#### 전략별 선택 결과")
        r1, r2, r3 = st.columns(3)
        r1.metric("LAA 변동 25%", f"{laa_variable} · {ETF_LABELS[laa_variable]}")
        r2.metric("VAA", f"{vaa_selected} · {ETF_LABELS[vaa_selected]}")
        r3.metric("ODM", f"{odm_selected} · {ETF_LABELS[odm_selected]}")

        st.markdown("#### VAA 모멘텀 스코어")
        vaa_display = pct_cols(vaa_scores, ["1개월 수익률", "3개월 수익률", "6개월 수익률", "12개월 수익률"])
        vaa_display["모멘텀 스코어"] = vaa_display["모멘텀 스코어"].apply(format_score)
        st.dataframe(vaa_display, use_container_width=True, hide_index=True)

        st.markdown("#### ODM 12개월 수익률")
        odm_display = pct_cols(odm_returns, ["1개월 수익률", "3개월 수익률", "6개월 수익률", "12개월 수익률"])
        st.dataframe(odm_display, use_container_width=True, hide_index=True)

        detail = pd.DataFrame(rows)
        final = detail.groupby(["ETF", "자산군"], as_index=False).agg({
            "전략 전체 비중": "sum",
            "목표 투자금(KRW)": "sum",
            "목표 투자금(USD)": "sum",
            "다음 리밸런싱일": lambda x: min(v for v in x if v is not None),
            "리밸런싱 상태": lambda x: "리밸런싱 필요" if "리밸런싱 필요" in list(x) else "대기",
        })

        quote_tickers = sorted(set(final["ETF"].tolist() + positions_base["ticker"].tolist()))
        quote_df = load_latest_quotes(quote_tickers, api_key)
        plan = add_rebalance_plan(final, quote_df, portfolio_status, usdkrw_rate)

        st.markdown("#### 최종 리밸런싱 주문안")
        plan_display = plan.copy()
        plan_display = plan_display.rename(columns={
            "ticker": "티커", "전략 전체 비중": "목표비중", "목표 투자금(USD)": "목표금액(USD)",
            "목표 투자금(KRW)": "목표금액(KRW)", "quantity": "현재수량", "market_value_usd": "현재평가액(USD)",
            "latest_price_usd": "최근가(USD)", "price_date": "가격 기준일", "weight": "현재비중",
        })
        for col in ["목표비중", "현재비중"]:
            if col in plan_display.columns:
                plan_display[col] = plan_display[col].apply(format_pct)
        for col in ["목표금액(USD)", "현재평가액(USD)", "매매 필요 금액(USD)"]:
            if col in plan_display.columns:
                plan_display[col] = plan_display[col].apply(money_usd)
        for col in ["목표금액(KRW)", "현재 평가액(KRW)", "매매 필요 금액(KRW)"]:
            if col in plan_display.columns:
                plan_display[col] = plan_display[col].apply(money_krw)
        if "최근가(USD)" in plan_display.columns:
            plan_display["최근가(USD)"] = plan_display["최근가(USD)"].apply(usd_price)
        for col in ["목표 주수", "현재 주수", "매매 필요 주수"]:
            if col in plan_display.columns:
                plan_display[col] = plan_display[col].apply(format_fractional_shares)
        st.dataframe(plan_display, use_container_width=True, hide_index=True)

        buy_usd = plan.loc[plan["매매 구분"] == "BUY", "매매 필요 금액(USD)"].dropna().sum()
        sell_usd = plan.loc[plan["매매 구분"] == "SELL", "매매 필요 금액(USD)"].dropna().sum()
        m1, m2, m3 = st.columns(3)
        m1.metric("추가 매수 필요", money_usd(buy_usd))
        m2.metric("매도 필요", money_usd(sell_usd))
        m3.metric("순매수 필요", money_usd(buy_usd - sell_usd))

        csv = plan.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("리밸런싱 주문안 CSV 다운로드", data=csv, file_name=f"rebalance_plan_{actual_eval_dt.strftime('%Y%m%d')}.csv", mime="text/csv")

# =========================================================
# 3) 설정/배포 가이드
# =========================================================
with tab_setup:
    st.subheader("Google Sheets 저장 구조")
    st.markdown(
        """
        이 앱은 Google Sheets 안에 다음 시트를 자동으로 만들고 사용합니다.

        - `cash`: 현재 현금 잔고를 저장합니다. 마지막 행을 현재 현금으로 사용합니다.
        - `trades`: 매매일지를 저장합니다. `BUY`는 수량 증가, `SELL`은 수량 감소, `ADJUST`는 수량 보정입니다.

        GitHub에는 코드만 저장하고, API Key와 Google 서비스 계정 JSON은 Streamlit Secrets에만 저장하세요.
        """
    )
    st.code(
        '''ALPHA_VANTAGE_API_KEY = "YOUR_ALPHA_VANTAGE_KEY"
GOOGLE_SHEET_ID = "YOUR_GOOGLE_SHEET_ID"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
client_email = "your-service-account@your-project.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"''',
        language="toml",
    )
    st.markdown(
        """
        설정 순서

        1. Google Cloud에서 서비스 계정을 만들고 JSON Key를 발급합니다.
        2. 빈 Google Sheet를 만들고, 서비스 계정의 `client_email`을 해당 Sheet에 편집자로 공유합니다.
        3. GitHub 저장소에는 `app.py`, `requirements.txt`만 올립니다.
        4. Streamlit Community Cloud에서 앱을 배포하고, Secrets 메뉴에 위 형식으로 값을 저장합니다.
        """
    )
