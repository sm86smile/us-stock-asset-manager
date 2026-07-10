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
DEFAULT_USDKRW_RATE = 1380.0  # settings 시트 환율을 읽지 못할 때만 사용하는 내부 기본값
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
SETTINGS_COLUMNS = ["key", "value"]
REBALANCE_PLAN_COLUMNS = [
    "saved_at",
    "eval_date",
    "strategy_price_month",
    "usdkrw_rate",
    "usdkrw_source",
    "usdkrw_rate_date",
    "investment_basis",
    "input_currency",
    "total_investment_usd",
    "total_investment_krw",
    "laa_selected",
    "vaa_selected",
    "odm_selected",
    "ticker",
    "asset_class",
    "target_weight",
    "target_usd",
    "target_krw",
    "current_quantity",
    "current_value_usd",
    "current_value_krw",
    "current_weight",
    "latest_price_usd",
    "price_date",
    "price_source",
    "target_shares",
    "current_shares",
    "trade_action",
    "trade_shares",
    "trade_amount_usd",
    "trade_amount_krw",
    "next_rebalance_date",
    "rebalance_status",
    "note",
]


REBALANCE_BASIS_COLUMNS = [
    "saved_at",
    "eval_date",
    "strategy_price_month",
    "section",
    "strategy",
    "ticker",
    "asset_class",
    "group",
    "item",
    "value",
    "value_numeric",
    "return_1m",
    "return_3m",
    "return_6m",
    "return_12m",
    "momentum_score",
    "rank",
    "selected",
    "decision_reason",
    "source_note",
]

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


def append_sheet_rows(sheet_name: str, columns: List[str], rows: List[Dict[str, object]]) -> None:
    """여러 행을 한 번에 Google Sheets에 추가합니다.

    gspread의 append_rows를 사용해 여러 건의 매매일지를 한 번에 저장하므로,
    1건씩 저장하는 것보다 Google Sheets API 호출 수를 줄일 수 있습니다.
    """
    if not rows:
        return
    ws = ensure_worksheet(sheet_name, columns)
    values = [[row.get(col, "") for col in columns] for row in rows]
    ws.append_rows(values, value_input_option="USER_ENTERED")


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


def load_settings() -> pd.DataFrame:
    """Google Sheets settings 시트를 읽습니다.

    settings 시트는 사용자가 직접 관리하는 간단한 key/value 저장소입니다.
    예: key=usdkrw_rate, value==GOOGLEFINANCE("CURRENCY:USDKRW")
    """
    df = load_sheet("settings", SETTINGS_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=SETTINGS_COLUMNS)
    df["key"] = df["key"].astype(str).str.strip()
    df["value"] = df["value"].astype(str).str.strip()
    return df


def parse_number_from_sheet_value(value: object) -> float:
    """Google Sheets에서 읽은 숫자/문자열 값을 float로 변환합니다."""
    if value is None or pd.isna(value):
        return float("nan")
    text = str(value).strip()
    if not text or text.upper() in {"#N/A", "#VALUE!", "#ERROR!", "N/A", "NONE", "NAN"}:
        return float("nan")
    text = text.replace(",", "")
    # 혹시 셀에 '1 USD = 1380 KRW'처럼 메모형 문자열이 들어가도 첫 숫자를 읽습니다.
    import re
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return float("nan")
    return float(match.group(0))


def load_usdkrw_from_settings(default_rate: float, eval_date: date) -> Tuple[float, str, pd.Timestamp]:
    """settings 시트의 usdkrw_rate 값을 사용하고, 실패하면 앱 내부 기본 환율로 대체합니다."""
    try:
        settings_df = load_settings()
        if settings_df.empty:
            return float(default_rate), "기본값(settings 시트 없음)", pd.Timestamp(eval_date)

        key_series = settings_df["key"].astype(str).str.strip().str.lower()
        rate_rows = settings_df[key_series == "usdkrw_rate"]
        if rate_rows.empty:
            return float(default_rate), "기본값(settings!usdkrw_rate 없음)", pd.Timestamp(eval_date)

        rate = parse_number_from_sheet_value(rate_rows.iloc[-1]["value"])
        if pd.isna(rate) or float(rate) <= 0:
            return float(default_rate), "기본값(settings 환율값 오류)", pd.Timestamp(eval_date)

        source_rows = settings_df[key_series == "usdkrw_source"]
        source = "Google Sheets GOOGLEFINANCE"
        if not source_rows.empty and str(source_rows.iloc[-1]["value"]).strip():
            source = str(source_rows.iloc[-1]["value"]).strip()

        date_rows = settings_df[key_series == "usdkrw_rate_date"]
        rate_date = pd.Timestamp(datetime.now().date())
        if not date_rows.empty and str(date_rows.iloc[-1]["value"]).strip():
            parsed = pd.to_datetime(date_rows.iloc[-1]["value"], errors="coerce")
            if not pd.isna(parsed):
                rate_date = pd.Timestamp(parsed).normalize()

        return float(rate), source, rate_date
    except Exception:
        return float(default_rate), "기본값(settings 읽기 실패)", pd.Timestamp(eval_date)


def _safe_str(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _safe_float(value: object, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return default
    return float(number)


def save_rebalance_plan_to_sheet(plan: pd.DataFrame, metadata: Dict[str, object]) -> None:
    """마지막 리밸런싱 주문안을 Google Sheets에 저장합니다.

    rebalance_plan 시트는 최신 계산 결과만 유지합니다.
    새로 전략 계산 버튼을 누르기 전까지 앱 재실행/새로고침 후에도 이 결과가 표시됩니다.
    """
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: List[Dict[str, object]] = []

    if plan is None or plan.empty:
        overwrite_sheet("rebalance_plan", REBALANCE_PLAN_COLUMNS, pd.DataFrame(columns=REBALANCE_PLAN_COLUMNS))
        return

    for _, row in plan.iterrows():
        rows.append({
            "saved_at": saved_at,
            "eval_date": _safe_str(metadata.get("eval_date")),
            "strategy_price_month": _safe_str(metadata.get("strategy_price_month")),
            "usdkrw_rate": _safe_float(metadata.get("usdkrw_rate")),
            "usdkrw_source": _safe_str(metadata.get("usdkrw_source")),
            "usdkrw_rate_date": _safe_str(metadata.get("usdkrw_rate_date")),
            "investment_basis": _safe_str(metadata.get("investment_basis")),
            "input_currency": _safe_str(metadata.get("input_currency")),
            "total_investment_usd": _safe_float(metadata.get("total_investment_usd")),
            "total_investment_krw": _safe_float(metadata.get("total_investment_krw")),
            "laa_selected": _safe_str(metadata.get("laa_selected")),
            "vaa_selected": _safe_str(metadata.get("vaa_selected")),
            "odm_selected": _safe_str(metadata.get("odm_selected")),
            "ticker": _safe_str(row.get("ticker")).upper().strip(),
            "asset_class": _safe_str(row.get("자산군")),
            "target_weight": _safe_float(row.get("전략 전체 비중")),
            "target_usd": _safe_float(row.get("목표 투자금(USD)")),
            "target_krw": _safe_float(row.get("목표 투자금(KRW)")),
            "current_quantity": _safe_float(row.get("quantity")),
            "current_value_usd": _safe_float(row.get("market_value_usd")),
            "current_value_krw": _safe_float(row.get("현재 평가액(KRW)")),
            "current_weight": _safe_float(row.get("weight")),
            "latest_price_usd": _safe_float(row.get("latest_price_usd"), default=float("nan")),
            "price_date": _safe_str(row.get("price_date")),
            "price_source": _safe_str(row.get("source")),
            "target_shares": _safe_float(row.get("목표 주수"), default=float("nan")),
            "current_shares": _safe_float(row.get("현재 주수")),
            "trade_action": _safe_str(row.get("매매 구분")),
            "trade_shares": _safe_float(row.get("매매 필요 주수"), default=float("nan")),
            "trade_amount_usd": _safe_float(row.get("매매 필요 금액(USD)"), default=float("nan")),
            "trade_amount_krw": _safe_float(row.get("매매 필요 금액(KRW)"), default=float("nan")),
            "next_rebalance_date": _safe_str(row.get("다음 리밸런싱일")),
            "rebalance_status": _safe_str(row.get("리밸런싱 상태")),
            "note": _safe_str(row.get("선정 사유")),
        })

    overwrite_sheet("rebalance_plan", REBALANCE_PLAN_COLUMNS, pd.DataFrame(rows))


def load_saved_rebalance_plan() -> pd.DataFrame:
    df = load_sheet("rebalance_plan", REBALANCE_PLAN_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=REBALANCE_PLAN_COLUMNS)

    numeric_cols = [
        "usdkrw_rate",
        "total_investment_usd",
        "total_investment_krw",
        "target_weight",
        "target_usd",
        "target_krw",
        "current_quantity",
        "current_value_usd",
        "current_value_krw",
        "current_weight",
        "latest_price_usd",
        "target_shares",
        "current_shares",
        "trade_shares",
        "trade_amount_usd",
        "trade_amount_krw",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    return df


def format_saved_rebalance_plan(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    show = df.copy()
    show = show.rename(columns={
        "saved_at": "저장시각",
        "eval_date": "평가 기준일",
        "strategy_price_month": "전략 기준월",
        "ticker": "티커",
        "asset_class": "자산군",
        "target_weight": "목표비중",
        "target_usd": "목표금액(USD)",
        "target_krw": "목표금액(KRW)",
        "current_quantity": "현재수량",
        "current_value_usd": "현재평가액(USD)",
        "current_value_krw": "현재평가액(KRW)",
        "current_weight": "현재비중",
        "latest_price_usd": "최근가(USD)",
        "price_date": "가격 기준일",
        "price_source": "가격 출처",
        "target_shares": "목표 주수",
        "current_shares": "현재 주수",
        "trade_action": "매매 구분",
        "trade_shares": "매매 필요 주수",
        "trade_amount_usd": "매매 필요 금액(USD)",
        "trade_amount_krw": "매매 필요 금액(KRW)",
        "next_rebalance_date": "다음 리밸런싱일",
        "rebalance_status": "리밸런싱 상태",
    })
    preferred_cols = [
        "저장시각", "전략 기준월", "티커", "자산군", "목표비중", "목표금액(USD)", "목표금액(KRW)",
        "현재수량", "현재평가액(USD)", "현재평가액(KRW)", "현재비중", "최근가(USD)", "가격 기준일", "가격 출처",
        "목표 주수", "현재 주수", "매매 구분", "매매 필요 주수", "매매 필요 금액(USD)", "매매 필요 금액(KRW)",
        "다음 리밸런싱일", "리밸런싱 상태",
    ]
    show = show[[c for c in preferred_cols if c in show.columns]]
    for col in ["목표비중", "현재비중"]:
        if col in show.columns:
            show[col] = show[col].apply(format_pct)
    for col in ["목표금액(USD)", "현재평가액(USD)", "매매 필요 금액(USD)"]:
        if col in show.columns:
            show[col] = show[col].apply(money_usd)
    for col in ["목표금액(KRW)", "현재평가액(KRW)", "매매 필요 금액(KRW)"]:
        if col in show.columns:
            show[col] = show[col].apply(money_krw)
    if "최근가(USD)" in show.columns:
        show["최근가(USD)"] = show["최근가(USD)"].apply(usd_price)
    for col in ["목표 주수", "현재 주수", "매매 필요 주수"]:
        if col in show.columns:
            show[col] = show[col].apply(format_fractional_shares)
    return show


def saved_rebalance_summary(df: pd.DataFrame) -> Dict[str, object]:
    if df is None or df.empty:
        return {"saved_at": "-", "total_usd": 0.0, "total_krw": 0.0, "buy_usd": 0.0, "sell_usd": 0.0}
    buy_usd = df.loc[df["trade_action"] == "BUY", "trade_amount_usd"].dropna().sum() if "trade_action" in df.columns else 0.0
    sell_usd = df.loc[df["trade_action"] == "SELL", "trade_amount_usd"].dropna().sum() if "trade_action" in df.columns else 0.0
    first = df.iloc[0]
    return {
        "saved_at": _safe_str(first.get("saved_at")),
        "total_usd": _safe_float(first.get("total_investment_usd")),
        "total_krw": _safe_float(first.get("total_investment_krw")),
        "buy_usd": float(buy_usd),
        "sell_usd": float(sell_usd),
        "strategy_price_month": _safe_str(first.get("strategy_price_month")),
    }




def _basis_row(
    saved_at: str,
    metadata: Dict[str, object],
    section: str,
    strategy: str = "",
    ticker: str = "",
    asset_class: str = "",
    group: str = "",
    item: str = "",
    value: object = "",
    value_numeric: object = "",
    return_1m: object = "",
    return_3m: object = "",
    return_6m: object = "",
    return_12m: object = "",
    momentum_score: object = "",
    rank: object = "",
    selected: object = "",
    decision_reason: str = "",
    source_note: str = "",
) -> Dict[str, object]:
    return {
        "saved_at": saved_at,
        "eval_date": _safe_str(metadata.get("eval_date")),
        "strategy_price_month": _safe_str(metadata.get("strategy_price_month")),
        "section": section,
        "strategy": strategy,
        "ticker": _safe_str(ticker).upper().strip(),
        "asset_class": asset_class,
        "group": group,
        "item": item,
        "value": _safe_str(value),
        "value_numeric": _safe_float(value_numeric, default=float("nan")) if value_numeric != "" else "",
        "return_1m": _safe_float(return_1m, default=float("nan")) if return_1m != "" else "",
        "return_3m": _safe_float(return_3m, default=float("nan")) if return_3m != "" else "",
        "return_6m": _safe_float(return_6m, default=float("nan")) if return_6m != "" else "",
        "return_12m": _safe_float(return_12m, default=float("nan")) if return_12m != "" else "",
        "momentum_score": _safe_float(momentum_score, default=float("nan")) if momentum_score != "" else "",
        "rank": _safe_float(rank, default=float("nan")) if rank != "" else "",
        "selected": _safe_str(selected),
        "decision_reason": decision_reason,
        "source_note": source_note,
    }


def build_rebalance_basis_rows(
    metadata: Dict[str, object],
    schedule_preview: pd.DataFrame,
    laa_variable: str,
    laa_reason: str,
    laa_defensive: bool,
    vaa_scores: pd.DataFrame,
    vaa_selected: str,
    vaa_reason: str,
    odm_returns: pd.DataFrame,
    odm_selected: str,
    odm_reason: str,
    quote_df: pd.DataFrame,
    portfolio_status_run: pd.DataFrame,
    plan: pd.DataFrame,
    strategy_weights: Dict[str, float],
    lookback_months: int,
    exclude_current_month: bool,
    zero_is_defensive: bool,
    use_cached_quotes_first: bool,
) -> pd.DataFrame:
    """리밸런싱 주문안이 나온 계산 근거를 행 단위로 정리합니다.

    rebalance_basis 시트는 최종 주문안(rebalance_plan)을 해석하기 위한 근거 로그입니다.
    새로 전략 계산 버튼을 누르면 최신 근거로 덮어씁니다.
    """
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: List[Dict[str, object]] = []

    # 공통 계산 설정
    setting_items = [
        ("조회기간", f"{lookback_months}개월", lookback_months, "VAA/ODM 수익률 계산에 사용한 월봉 개수"),
        ("진행 중인 월 데이터 제외", "Y" if exclude_current_month else "N", "", "월말 전략 계산 기준 옵션"),
        ("수동/세션 저장 최신가 우선 사용", "Y" if use_cached_quotes_first else "N", "", "가격 API 호출 절감 옵션"),
        ("VAA 0점 방어 처리", "Y" if zero_is_defensive else "N", "", "Y이면 0점은 방어 신호로 처리"),
        ("USD/KRW 환율", f"{_safe_float(metadata.get('usdkrw_rate')):,.2f}", metadata.get("usdkrw_rate"), f"출처: {_safe_str(metadata.get('usdkrw_source'))}, 기준일: {_safe_str(metadata.get('usdkrw_rate_date'))}"),
        ("리밸런싱 기준금액 USD", money_usd(_safe_float(metadata.get("total_investment_usd"))), metadata.get("total_investment_usd"), _safe_str(metadata.get("investment_basis"))),
        ("리밸런싱 기준금액 KRW", money_krw(_safe_float(metadata.get("total_investment_krw"))), metadata.get("total_investment_krw"), _safe_str(metadata.get("investment_basis"))),
        ("하위전략 비중", f"LAA {strategy_weights.get('LAA', 0):.2%} / VAA {strategy_weights.get('VAA', 0):.2%} / ODM {strategy_weights.get('ODM', 0):.2%}", "", "사용자 입력 비중을 합계 100% 기준으로 정규화"),
    ]
    for item, value, numeric, note in setting_items:
        rows.append(_basis_row(saved_at, metadata, "공통 설정", "공통", item=item, value=value, value_numeric=numeric, source_note=note))

    # LAA 근거
    rows.append(_basis_row(
        saved_at, metadata, "전략 선택 근거", "LAA", laa_variable, ETF_LABELS.get(laa_variable, ""), "변동 25%",
        item="LAA 변동자산 선택", value=laa_variable, selected="Y", decision_reason=laa_reason,
        source_note="사용자 입력 조건: S&P500 200일선 하회 + 미국 실업률 12개월 평균 상회 = " + ("O" if laa_defensive else "X"),
    ))
    for t in LAA_FIXED:
        rows.append(_basis_row(
            saved_at, metadata, "전략 선택 근거", "LAA", t, ETF_LABELS.get(t, ""), "고정 75%",
            item="LAA 고정자산", value="LAA 내 25%", value_numeric=0.25, selected="Y",
            decision_reason="IWD/GLD/IEF는 LAA 고정자산으로 각각 25% 배분",
        ))

    # VAA 모멘텀 근거
    if vaa_scores is not None and not vaa_scores.empty:
        vs = vaa_scores.copy()
        vs["_rank"] = vs["모멘텀 스코어"].rank(ascending=False, method="min")
        for _, r in vs.iterrows():
            ticker = _safe_str(r.get("ETF"))
            rows.append(_basis_row(
                saved_at, metadata, "전략 선택 근거", "VAA", ticker, _safe_str(r.get("자산군")), _safe_str(r.get("구분")),
                item="VAA 모멘텀 스코어", value=ticker, value_numeric=r.get("현재 조정종가", ""),
                return_1m=r.get("1개월 수익률", ""), return_3m=r.get("3개월 수익률", ""),
                return_6m=r.get("6개월 수익률", ""), return_12m=r.get("12개월 수익률", ""),
                momentum_score=r.get("모멘텀 스코어", ""), rank=r.get("_rank", ""),
                selected="Y" if ticker == vaa_selected else "N", decision_reason=vaa_reason,
                source_note="VAA 점수 = 12×1M + 4×3M + 2×6M + 1×12M",
            ))

    # ODM 근거
    if odm_returns is not None and not odm_returns.empty:
        od = odm_returns.copy()
        od["_rank"] = od["12개월 수익률"].rank(ascending=False, method="min")
        for _, r in od.iterrows():
            ticker = _safe_str(r.get("ETF"))
            rows.append(_basis_row(
                saved_at, metadata, "전략 선택 근거", "오리지널 듀얼 모멘텀", ticker, _safe_str(r.get("자산군")), "12개월 상대/절대 모멘텀",
                item="ODM 12개월 수익률", value=ticker, value_numeric=r.get("현재 조정종가", ""),
                return_1m=r.get("1개월 수익률", ""), return_3m=r.get("3개월 수익률", ""),
                return_6m=r.get("6개월 수익률", ""), return_12m=r.get("12개월 수익률", ""),
                rank=r.get("_rank", ""), selected="Y" if ticker == odm_selected else "N",
                decision_reason=odm_reason, source_note="SPY와 BIL 비교 후 SPY/EFA 또는 AGG 선택",
            ))

    # 전략별 최근 리밸런싱일 근거
    if schedule_preview is not None and not schedule_preview.empty:
        for _, r in schedule_preview.iterrows():
            rows.append(_basis_row(
                saved_at, metadata, "리밸런싱일 근거", _safe_str(r.get("구분")), item="최근/다음 리밸런싱일",
                value=f"최근 {_safe_str(r.get('최근 리밸런싱일'))} → 다음 {_safe_str(r.get('다음 리밸런싱일'))} / 상태 {_safe_str(r.get('상태'))}",
                decision_reason=_safe_str(r.get("상태")), source_note=_safe_str(r.get("적용 기준")),
            ))

    # 가격 근거
    if quote_df is not None and not quote_df.empty:
        q = quote_df.copy()
        q["ticker"] = q["ticker"].astype(str).str.upper().str.strip()
        for _, r in q.sort_values("ticker").iterrows():
            rows.append(_basis_row(
                saved_at, metadata, "가격 근거", "가격 조회", _safe_str(r.get("ticker")), ETF_LABELS.get(_safe_str(r.get("ticker")), ""),
                item="최근가", value=usd_price(_safe_float(r.get("latest_price_usd"), default=float("nan"))), value_numeric=r.get("latest_price_usd", ""),
                decision_reason="목표 주수와 현재 평가액 계산에 사용", source_note=f"출처: {_safe_str(r.get('source'))}, 기준일: {_safe_str(r.get('price_date'))}, 조회/입력시각: {_safe_str(r.get('fetched_at'))}",
            ))

    # 현재 보유 평가 근거
    if portfolio_status_run is not None and not portfolio_status_run.empty:
        ps = portfolio_status_run.copy()
        ps["ticker"] = ps["ticker"].astype(str).str.upper().str.strip()
        for _, r in ps.sort_values("ticker").iterrows():
            rows.append(_basis_row(
                saved_at, metadata, "현재 보유 근거", "현재 포트폴리오", _safe_str(r.get("ticker")), ETF_LABELS.get(_safe_str(r.get("ticker")), ""),
                item="현재 보유수량/평가액", value=f"{_safe_float(r.get('quantity')):,.6f}주 / {money_usd(_safe_float(r.get('market_value_usd')))}", value_numeric=r.get("market_value_usd", ""),
                decision_reason="현재 보유수량과 목표 주수 차이 계산에 사용", source_note=f"평가 기준: {_safe_str(r.get('valuation_source'))}, 평가가격: {usd_price(_safe_float(r.get('valuation_price_usd'), default=float('nan')))}",
            ))

    # 최종 주문안 산식 근거
    if plan is not None and not plan.empty:
        for _, r in plan.sort_values("ticker").iterrows():
            rows.append(_basis_row(
                saved_at, metadata, "주문안 산식 근거", _safe_str(r.get("하위전략", "최종")), _safe_str(r.get("ticker")), _safe_str(r.get("자산군")),
                item="목표주수-현재주수", value=f"목표 {_safe_float(r.get('목표 주수'), default=float('nan')):,.2f}주 - 현재 {_safe_float(r.get('현재 주수')):,.2f}주 = {_safe_float(r.get('매매 필요 주수'), default=float('nan')):,.2f}주", value_numeric=r.get("매매 필요 금액(USD)", ""),
                selected=_safe_str(r.get("매매 구분")), decision_reason=f"매매 구분: {_safe_str(r.get('매매 구분'))}",
                source_note="목표 주수 = floor(목표 투자금 USD / 최근가 USD)",
            ))

    return pd.DataFrame(rows, columns=REBALANCE_BASIS_COLUMNS)


def save_rebalance_basis_to_sheet(basis: pd.DataFrame) -> None:
    """마지막 리밸런싱 계산 근거를 Google Sheets에 저장합니다."""
    if basis is None or basis.empty:
        overwrite_sheet("rebalance_basis", REBALANCE_BASIS_COLUMNS, pd.DataFrame(columns=REBALANCE_BASIS_COLUMNS))
        return
    overwrite_sheet("rebalance_basis", REBALANCE_BASIS_COLUMNS, basis)


def load_saved_rebalance_basis() -> pd.DataFrame:
    df = load_sheet("rebalance_basis", REBALANCE_BASIS_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=REBALANCE_BASIS_COLUMNS)
    numeric_cols = ["value_numeric", "return_1m", "return_3m", "return_6m", "return_12m", "momentum_score", "rank"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    return df


def format_saved_rebalance_basis(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    show = df.copy()
    show = show.rename(columns={
        "saved_at": "저장시각", "eval_date": "평가 기준일", "strategy_price_month": "전략 기준월",
        "section": "근거 구분", "strategy": "전략", "ticker": "티커", "asset_class": "자산군", "group": "그룹",
        "item": "항목", "value": "값", "value_numeric": "수치값", "return_1m": "1개월 수익률",
        "return_3m": "3개월 수익률", "return_6m": "6개월 수익률", "return_12m": "12개월 수익률",
        "momentum_score": "모멘텀 스코어", "rank": "순위", "selected": "선택/구분",
        "decision_reason": "판정 사유", "source_note": "적용 근거",
    })
    for col in ["1개월 수익률", "3개월 수익률", "6개월 수익률", "12개월 수익률"]:
        if col in show.columns:
            show[col] = show[col].apply(lambda x: format_pct(x) if pd.notna(x) else "")
    if "모멘텀 스코어" in show.columns:
        show["모멘텀 스코어"] = show["모멘텀 스코어"].apply(lambda x: format_score(x) if pd.notna(x) else "")
    if "수치값" in show.columns:
        show["수치값"] = show["수치값"].apply(lambda x: f"{float(x):,.4f}" if pd.notna(x) else "")
    if "순위" in show.columns:
        show["순위"] = show["순위"].apply(lambda x: f"{float(x):.0f}" if pd.notna(x) else "")
    return show

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
    return {"ticker": symbol, "latest_price_usd": float(price), "price_date": latest_trading_day or "-", "source": "API"}


def load_latest_quotes(tickers: List[str], api_key: str) -> pd.DataFrame:
    rows, errors = [], []
    tickers = sorted(set([str(t).upper().strip() for t in tickers if str(t).strip()]))
    if not tickers:
        return pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source"])

    progress = st.progress(0, text="최신 종가를 불러오는 중입니다.")
    for i, ticker in enumerate(tickers, start=1):
        try:
            rows.append(fetch_global_quote(ticker, api_key))
        except Exception as e:
            errors.append(str(e))
            rows.append({"ticker": ticker, "latest_price_usd": pd.NA, "price_date": "-", "source": "API_ERROR"})
        progress.progress(i / len(tickers), text=f"최신 가격 로딩: {ticker} ({i}/{len(tickers)})")
        if i < len(tickers):
            time.sleep(API_CALL_DELAY_SECONDS)
    progress.empty()
    if errors:
        with st.expander("가격 로딩 오류 보기", expanded=True):
            for err in errors:
                st.error(err)
    return pd.DataFrame(rows)




def get_cached_quotes_for_tickers(tickers: List[str]) -> pd.DataFrame:
    """세션에 저장된 최신가만 읽습니다. 이 함수는 Alpha Vantage를 호출하지 않습니다."""
    cols = ["ticker", "latest_price_usd", "price_date", "source", "fetched_at"]
    cached = st.session_state.get("latest_quotes_df")
    if cached is None or not isinstance(cached, pd.DataFrame) or cached.empty:
        return pd.DataFrame(columns=cols)

    wanted = sorted(set([str(t).upper().strip() for t in tickers if str(t).strip()]))
    if not wanted:
        return pd.DataFrame(columns=cols)

    result = cached.copy()
    for col in cols:
        if col not in result.columns:
            result[col] = ""
    result["ticker"] = result["ticker"].astype(str).str.upper().str.strip()
    return result[result["ticker"].isin(wanted)][cols].copy()


def store_latest_quotes(quotes: pd.DataFrame) -> None:
    """새로 조회한 최신가를 세션에 저장합니다. 같은 티커는 가장 최근 조회값으로 덮어씁니다."""
    if quotes is None or quotes.empty:
        return

    new_quotes = quotes.copy()
    new_quotes["ticker"] = new_quotes["ticker"].astype(str).str.upper().str.strip()
    if "source" not in new_quotes.columns:
        new_quotes["source"] = "API"
    new_quotes["source"] = new_quotes["source"].fillna("API").astype(str)
    new_quotes["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cached = st.session_state.get("latest_quotes_df")
    if cached is None or not isinstance(cached, pd.DataFrame) or cached.empty:
        st.session_state["latest_quotes_df"] = new_quotes
        return

    combined = pd.concat([cached, new_quotes], ignore_index=True)
    combined["ticker"] = combined["ticker"].astype(str).str.upper().str.strip()
    combined = combined.drop_duplicates(subset=["ticker"], keep="last")
    st.session_state["latest_quotes_df"] = combined


def quote_cache_info(quotes: pd.DataFrame) -> str:
    if quotes is None or quotes.empty or "fetched_at" not in quotes.columns:
        return "최신가 미조회"
    fetched_values = [str(x) for x in quotes["fetched_at"].dropna().unique().tolist() if str(x).strip()]
    if not fetched_values:
        return "최신가 미조회"
    sources = []
    if "source" in quotes.columns:
        sources = [str(x) for x in quotes["source"].dropna().unique().tolist() if str(x).strip()]
    source_text = f" / 출처: {', '.join(sorted(set(sources)))}" if sources else ""
    return f"세션 저장 최신가 사용 / 최근 조회: {max(fetched_values)}{source_text}"




def build_manual_quote_template(positions: pd.DataFrame, cached_quotes: pd.DataFrame, today_value: date) -> pd.DataFrame:
    """보유종목 기준 수동 최신가 입력용 표를 만듭니다. 이 함수는 API를 호출하지 않습니다."""
    cols = ["ticker", "latest_price_usd", "price_date"]
    if positions is None or positions.empty:
        return pd.DataFrame(columns=cols)

    base = positions[["ticker"]].copy()
    base["ticker"] = base["ticker"].astype(str).str.upper().str.strip()
    base = base.drop_duplicates(subset=["ticker"]).sort_values("ticker")

    if cached_quotes is not None and not cached_quotes.empty:
        cached = cached_quotes.copy()
        cached["ticker"] = cached["ticker"].astype(str).str.upper().str.strip()
        cached = cached.drop_duplicates(subset=["ticker"], keep="last")
        keep_cols = [c for c in ["ticker", "latest_price_usd", "price_date"] if c in cached.columns]
        base = base.merge(cached[keep_cols], on="ticker", how="left")
    else:
        base["latest_price_usd"] = 0.0
        base["price_date"] = today_value.strftime("%Y-%m-%d")

    if "latest_price_usd" not in base.columns:
        base["latest_price_usd"] = 0.0
    if "price_date" not in base.columns:
        base["price_date"] = today_value.strftime("%Y-%m-%d")

    base["latest_price_usd"] = pd.to_numeric(base["latest_price_usd"], errors="coerce").fillna(0.0)
    base["price_date"] = base["price_date"].fillna(today_value.strftime("%Y-%m-%d")).astype(str)
    return base[cols]


def normalize_manual_quotes(input_df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """수동 입력 최신가 표를 세션 저장용 quote_df로 정리합니다."""
    errors: List[str] = []
    rows: List[Dict[str, object]] = []

    if input_df is None or input_df.empty:
        return pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source"]), ["수동 입력할 종목이 없습니다."]

    for idx, row in input_df.iterrows():
        ticker = str(row.get("ticker", "") or "").upper().strip()
        price = pd.to_numeric(row.get("latest_price_usd", pd.NA), errors="coerce")
        price_date = str(row.get("price_date", "") or "").strip()

        if not ticker and (pd.isna(price) or float(price or 0) == 0):
            continue
        if not ticker:
            errors.append(f"{idx + 1}번 행: 티커를 입력하세요.")
            continue
        if pd.isna(price) or float(price) <= 0:
            errors.append(f"{ticker}: 최신가(USD)는 0보다 커야 합니다.")
            continue
        if not price_date:
            price_date = datetime.now().strftime("%Y-%m-%d")

        rows.append({
            "ticker": ticker,
            "latest_price_usd": float(price),
            "price_date": price_date,
            "source": "MANUAL",
        })

    if not rows and not errors:
        errors.append("저장할 수동 최신가가 없습니다. 가격을 입력하세요.")

    return pd.DataFrame(rows, columns=["ticker", "latest_price_usd", "price_date", "source"]), errors


def combine_cached_and_api_quotes(quote_tickers: List[str], api_key: str, use_cached_first: bool) -> pd.DataFrame:
    """리밸런싱용 가격표를 만듭니다. 수동/세션 저장 가격을 우선 쓰고 부족한 종목만 API로 조회할 수 있습니다."""
    tickers = sorted(set([str(t).upper().strip() for t in quote_tickers if str(t).strip()]))
    if not tickers:
        return pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])

    if not use_cached_first:
        api_quotes = load_latest_quotes(tickers, api_key)
        store_latest_quotes(api_quotes)
        return get_cached_quotes_for_tickers(tickers)

    cached = get_cached_quotes_for_tickers(tickers)
    cached_valid = cached.dropna(subset=["latest_price_usd"]).copy() if not cached.empty else pd.DataFrame()
    cached_valid["latest_price_usd"] = pd.to_numeric(cached_valid.get("latest_price_usd", pd.Series(dtype=float)), errors="coerce") if not cached_valid.empty else pd.Series(dtype=float)
    cached_valid = cached_valid[cached_valid["latest_price_usd"] > 0] if not cached_valid.empty else cached_valid
    cached_tickers = set(cached_valid["ticker"].astype(str).str.upper().str.strip().tolist()) if not cached_valid.empty else set()
    missing = [t for t in tickers if t not in cached_tickers]

    if missing:
        st.info(f"수동/세션 저장 최신가가 없는 {len(missing)}개 티커만 API로 조회합니다: {', '.join(missing)}")
        api_quotes = load_latest_quotes(missing, api_key)
        store_latest_quotes(api_quotes)
    else:
        st.info("모든 리밸런싱 대상 티커에 수동/세션 저장 최신가가 있어 가격 API 조회를 생략합니다.")

    return get_cached_quotes_for_tickers(tickers)

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
    """보유수량과 가격표를 합쳐 총자산을 계산합니다.

    - API/수동 최신가가 있으면 최신가로 평가합니다.
    - 최신가가 아직 없으면 매매일지의 평균매수가를 임시 평가가격으로 사용해
      총자산이 현금만 표시되는 문제를 방지합니다.
    - 평균매수가도 없는 ADJUST 입고 종목은 가격을 알 수 없으므로 평가액에서 제외됩니다.
    """
    pos = positions.copy() if not positions.empty else pd.DataFrame(columns=["ticker", "quantity", "avg_buy_price_usd"])
    if quotes is None or quotes.empty:
        quotes = pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])

    for col in ["ticker", "latest_price_usd", "price_date", "source", "fetched_at"]:
        if col not in quotes.columns:
            quotes[col] = ""

    pos = pos.merge(quotes[["ticker", "latest_price_usd", "price_date", "source", "fetched_at"]], on="ticker", how="left")
    pos["quantity"] = pd.to_numeric(pos.get("quantity"), errors="coerce").fillna(0.0)
    pos["avg_buy_price_usd"] = pd.to_numeric(pos.get("avg_buy_price_usd"), errors="coerce")
    pos["latest_price_usd"] = pd.to_numeric(pos.get("latest_price_usd"), errors="coerce")

    has_latest = pos["latest_price_usd"].notna() & (pos["latest_price_usd"] > 0)
    has_avg = pos["avg_buy_price_usd"].notna() & (pos["avg_buy_price_usd"] > 0)
    pos["valuation_price_usd"] = pos["latest_price_usd"].where(has_latest, pos["avg_buy_price_usd"].where(has_avg, pd.NA))
    pos["valuation_source"] = "가격 없음"
    pos.loc[has_avg & ~has_latest, "valuation_source"] = "평균매수가 임시평가"
    pos.loc[has_latest, "valuation_source"] = pos.loc[has_latest, "source"].fillna("API/수동 최신가")

    pos["market_value_usd"] = pos["quantity"] * pd.to_numeric(pos["valuation_price_usd"], errors="coerce")
    pos["market_value_krw"] = pos["market_value_usd"] * usdkrw_rate
    pos["unrealized_pnl_usd"] = (pos["valuation_price_usd"] - pos["avg_buy_price_usd"]) * pos["quantity"]
    pos["unrealized_pnl_pct"] = (pos["valuation_price_usd"] / pos["avg_buy_price_usd"] - 1).where(pos["avg_buy_price_usd"] > 0)

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
        "priced_positions": int(pos["valuation_price_usd"].notna().sum()) if not pos.empty else 0,
        "total_positions": int(len(pos)) if not pos.empty else 0,
        "fallback_positions": int(((has_avg & ~has_latest).sum())) if not pos.empty else 0,
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


def appendix_strategy_calculation_table() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "구분": "LAA 고정 75%",
            "계산 방식": "LAA 배정금액의 25%씩 IWD, GLD, IEF에 배분",
            "판정 기준": "별도 모멘텀 계산 없음",
            "앱 반영": "하위전략 비중 × 25%로 목표금액 계산",
        },
        {
            "구분": "LAA 변동 25%",
            "계산 방식": "S&P500 200일선 하회와 미국 실업률 12개월 평균 상회를 사용자가 O/X로 입력",
            "판정 기준": "두 조건이 모두 O이면 SHY, 그 외에는 QQQ",
            "앱 반영": "선택 ETF에 LAA 배정금액의 25% 배분",
        },
        {
            "구분": "VAA 모멘텀 스코어",
            "계산 방식": "12×1개월 수익률 + 4×3개월 수익률 + 2×6개월 수익률 + 1×12개월 수익률",
            "판정 기준": "공격형 SPY/EFA/EEM/AGG 4개가 모두 기준 이상이면 공격형 1위, 하나라도 미달이면 안전자산 LQD/IEF/SHY 중 1위",
            "앱 반영": "Alpha Vantage 월별 조정종가로 1·3·6·12개월 수익률과 점수 자동 계산",
        },
        {
            "구분": "VAA 0점 처리",
            "계산 방식": "사이드바 옵션이 아니라 전략 탭의 체크박스 값 사용",
            "판정 기준": "체크 시 0점은 방어 신호, 해제 시 0점 이상도 공격형 조건 충족",
            "앱 반영": "zero_is_defensive 값으로 공격/방어 판단",
        },
        {
            "구분": "오리지널 듀얼 모멘텀",
            "계산 방식": "SPY, EFA, BIL의 12개월 수익률 비교",
            "판정 기준": "SPY > BIL이면 SPY/EFA 중 12개월 수익률 높은 ETF, SPY ≤ BIL이면 AGG",
            "앱 반영": "선택된 ETF에 ODM 배정금액 100% 배분",
        },
        {
            "구분": "최근 리밸런싱일",
            "계산 방식": "Google Sheets trades 시트에서 각 전략의 대상 ETF만 필터링한 뒤 그중 가장 최근 trade_date를 사용",
            "판정 기준": "LAA 고정은 IWD/GLD/IEF 기준, LAA 변동은 QQQ/SHY 기준, VAA는 SPY/EFA/EEM/AGG/LQD/IEF/SHY 기준, ODM은 SPY/EFA/BIL/AGG 기준",
            "앱 반영": "전략별 대상 ETF 매매일이 없으면 평가 기준일을 임시 적용하고, 일정표에 해당 기준을 함께 표시",
        },
        {
            "구분": "Google Sheets 저장 리밸런싱 결과",
            "계산 방식": "ETF 전략 계산 버튼을 눌러 생성된 최종 주문안을 rebalance_plan 시트에 덮어쓰기 저장",
            "판정 기준": "새로 API를 호출해 전략을 다시 계산하기 전까지 마지막 저장 결과 유지",
            "앱 반영": "앱 새로고침 후에도 2) 리밸런싱 탭에서 저장된 마지막 주문안 표시",
        },
        {
            "구분": "목표 주수/매매 주수",
            "계산 방식": "목표 주수 = 목표 투자금(USD) ÷ 최근가(USD) 후 소수점 버림",
            "판정 기준": "매매 필요 주수 = 목표 주수 - 현재 보유 주수",
            "앱 반영": "양수는 BUY, 음수는 SELL, 0은 HOLD",
        },
    ])


def latest_trade_date_for_tickers(trades: pd.DataFrame, tickers: List[str], fallback_date: date) -> Tuple[date, str]:
    """전략별 대상 ETF 목록에 해당하는 매매일지 중 가장 최근 trade_date를 반환합니다."""
    target_tickers = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    target_text = ", ".join(target_tickers)

    if trades is None or trades.empty or "trade_date" not in trades.columns or "ticker" not in trades.columns:
        return fallback_date, f"대상 ETF({target_text}) 매매일지 없음 - 평가 기준일 임시 적용"

    filtered = trades.copy()
    filtered["_ticker"] = filtered["ticker"].astype(str).str.upper().str.strip()
    filtered = filtered[filtered["_ticker"].isin(target_tickers)]

    if filtered.empty:
        return fallback_date, f"대상 ETF({target_text}) 매매내역 없음 - 평가 기준일 임시 적용"

    filtered["_trade_date"] = pd.to_datetime(filtered["trade_date"], errors="coerce")
    filtered = filtered.dropna(subset=["_trade_date"])

    if filtered.empty:
        return fallback_date, f"대상 ETF({target_text}) 유효한 매매일 없음 - 평가 기준일 임시 적용"

    latest_ts = filtered["_trade_date"].max()
    latest_date = latest_ts.date()
    latest_tickers = sorted(filtered.loc[filtered["_trade_date"] == latest_ts, "_ticker"].unique().tolist())
    latest_ticker_text = ", ".join(latest_tickers)
    return latest_date, f"대상 ETF({target_text}) 중 최신 매매일 · 해당 티커: {latest_ticker_text}"


def strategy_rebalance_dates_from_trades(trades: pd.DataFrame, fallback_date: date) -> Dict[str, Dict[str, object]]:
    """LAA 고정/변동, VAA, ODM별 최근 리밸런싱일을 각각 계산합니다."""
    specs = {
        "laa_fixed": {"label": "LAA 고정자산", "tickers": LAA_FIXED, "cycle": "연 1회"},
        "laa_variable": {"label": "LAA 변동자산", "tickers": LAA_VARIABLE, "cycle": "월 1회"},
        "vaa": {"label": "VAA", "tickers": sorted(set(VAA_ATTACK + VAA_SAFE)), "cycle": "월 1회"},
        "odm": {"label": "오리지널 듀얼 모멘텀", "tickers": ODM_ASSETS, "cycle": "월 1회"},
    }
    result: Dict[str, Dict[str, object]] = {}
    for key, spec in specs.items():
        last_date, source = latest_trade_date_for_tickers(trades, spec["tickers"], fallback_date)
        result[key] = {
            "label": spec["label"],
            "tickers": spec["tickers"],
            "cycle": spec["cycle"],
            "last_date": last_date,
            "source": source,
        }
    return result


def rebalance_schedule_preview_by_strategy(strategy_dates: Dict[str, Dict[str, object]], eval_date: date) -> pd.DataFrame:
    rows = []
    order = ["laa_fixed", "laa_variable", "vaa", "odm"]
    for key in order:
        info = strategy_dates[key]
        last_date = info["last_date"]
        cycle = str(info["cycle"])
        next_date = next_rebalance_date(last_date, cycle)
        rows.append(
            {
                "구분": info["label"],
                "대상 ETF": ", ".join(info["tickers"]),
                "주기": cycle,
                "최근 리밸런싱일": last_date,
                "다음 리밸런싱일": next_date,
                "상태": rebalance_status(next_date, eval_date),
                "적용 기준": info["source"],
            }
        )
    return pd.DataFrame(rows)

# =========================================================
# 매매일지 일괄 입력 유틸
# =========================================================
def make_empty_trade_row(default_date: date) -> Dict[str, object]:
    return {
        "trade_date": default_date,
        "ticker": "",
        "side": "BUY",
        "quantity": 0.0,
        "price_usd": 0.0,
        "fee_usd": 0.0,
        "memo": "",
    }


def normalize_trade_date(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, datetime):
        return value.date().strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.date().strftime("%Y-%m-%d")


def prepare_batch_trade_rows(edited_df: pd.DataFrame) -> Tuple[List[Dict[str, object]], List[str]]:
    """data_editor 입력값을 Google Sheets 저장용 행으로 변환하고 검증합니다."""
    rows: List[Dict[str, object]] = []
    errors: List[str] = []

    if edited_df is None or edited_df.empty:
        return rows, errors

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for idx, row in edited_df.reset_index(drop=True).iterrows():
        row_no = idx + 1
        ticker = str(row.get("ticker", "") or "").upper().strip()
        side = str(row.get("side", "") or "").upper().strip()
        trade_date_text = normalize_trade_date(row.get("trade_date"))
        quantity = pd.to_numeric(row.get("quantity", 0), errors="coerce")
        price_usd = pd.to_numeric(row.get("price_usd", 0), errors="coerce")
        fee_usd = pd.to_numeric(row.get("fee_usd", 0), errors="coerce")
        memo = str(row.get("memo", "") or "").strip()

        # 완전히 빈 행은 저장하지 않습니다.
        is_blank = (not ticker) and (pd.isna(quantity) or float(quantity or 0) == 0) and (pd.isna(price_usd) or float(price_usd or 0) == 0) and not memo
        if is_blank:
            continue

        if not trade_date_text:
            errors.append(f"{row_no}행: 매매일을 입력하세요.")
        if not ticker:
            errors.append(f"{row_no}행: 티커를 입력하세요.")
        if side not in ["BUY", "SELL", "ADJUST"]:
            errors.append(f"{row_no}행: 구분은 BUY, SELL, ADJUST 중 하나여야 합니다.")
        if pd.isna(quantity):
            errors.append(f"{row_no}행: 수량을 숫자로 입력하세요.")
        elif side in ["BUY", "SELL"] and float(quantity) <= 0:
            errors.append(f"{row_no}행: BUY/SELL 수량은 0보다 커야 합니다.")
        if pd.isna(price_usd):
            errors.append(f"{row_no}행: 체결가를 숫자로 입력하세요.")
        if pd.isna(fee_usd):
            errors.append(f"{row_no}행: 수수료를 숫자로 입력하세요.")

        if errors and any(err.startswith(f"{row_no}행:") for err in errors):
            continue

        rows.append({
            "trade_date": trade_date_text,
            "ticker": ticker,
            "side": side,
            "quantity": float(quantity),
            "price_usd": float(price_usd),
            "fee_usd": float(fee_usd),
            "memo": memo,
            "created_at": now_text,
        })

    return rows, errors


# =========================================================
# 화면 시작
# =========================================================
st.title("미국 주식 자산관리 + ETF 자산배분 리밸런싱")
st.caption("Google Sheets에 현금/매매일지/마지막 리밸런싱 결과/계산 근거를 저장하고, 최신 가격은 사용자가 버튼을 눌렀을 때만 Alpha Vantage로 조회하고, USD/KRW 환율은 Google Sheets settings 시트에서 읽습니다.")

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
    st.markdown("---")

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

# 환율은 자산 탭/전략 탭 모두 필요합니다.
# v14부터 USD/KRW 환율은 Alpha Vantage를 호출하지 않고 Google Sheets settings 시트 값을 사용합니다.
usdkrw_rate, usdkrw_source, usdkrw_rate_date = load_usdkrw_from_settings(DEFAULT_USDKRW_RATE, eval_date)
if str(usdkrw_source).startswith("기본값"):
    st.warning(f"USD/KRW 환율은 {usdkrw_source} 기준 {fx_rate_krw(usdkrw_rate)}를 사용합니다. Google Sheets settings 시트의 usdkrw_rate 값을 확인하세요.")
else:
    st.info(f"USD/KRW 환율은 Google Sheets settings 시트 값 {fx_rate_krw(usdkrw_rate)}를 사용합니다. 출처: {usdkrw_source}")

try:
    trades_df = load_trades()
    cash_balance = get_latest_cash_balance()
    saved_rebalance_plan_df = load_saved_rebalance_plan()
    saved_rebalance_basis_df = load_saved_rebalance_basis()
except Exception as e:
    st.error(f"Google Sheets 연결/초기화 오류: {e}")
    st.info("서비스 계정 이메일을 Google Sheet에 편집자로 공유했는지 확인하세요.")
    st.stop()

positions_base = calculate_positions_from_trades(trades_df)
strategy_rebalance_dates = strategy_rebalance_dates_from_trades(trades_df, eval_date)
# 중요: 앱 로딩/매매일지 저장 직후에는 Alpha Vantage를 호출하지 않습니다.
# 세션에 저장된 최신가가 있을 때만 현재 자산 평가에 사용합니다.
portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist()) if not positions_base.empty else pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)

tab_assets, tab_strategy = st.tabs(["1) 자산/매매일지", "2) ETF 자산배분 리밸런싱"])

# =========================================================
# 2) ETF 자산배분 리밸런싱
# =========================================================
with tab_strategy:
    st.subheader("ETF 전략 입력")
    with st.expander("Appendix. 매수전략 / 모멘텀 스코어 / 리밸런싱 계산 방식", expanded=False):
        app_tab1, app_tab2 = st.tabs(["매수 전략 요약", "계산 방식 상세"])
        with app_tab1:
            st.dataframe(appendix_buy_strategy_table(), use_container_width=True, hide_index=True)
        with app_tab2:
            st.dataframe(appendix_strategy_calculation_table(), use_container_width=True, hide_index=True)

    st.markdown("#### 최근 리밸런싱일")
    st.info(
        "최근 리밸런싱일은 전체 매매일지의 최신 날짜 하나를 공통 적용하지 않고, "
        "각 전략의 대상 ETF 매매내역만 필터링한 뒤 전략별로 가장 최근 매매일을 자동 적용합니다."
    )
    schedule_preview = rebalance_schedule_preview_by_strategy(strategy_rebalance_dates, eval_date)
    st.dataframe(schedule_preview, use_container_width=True, hide_index=True)

    st.markdown("#### Google Sheets에 저장된 마지막 리밸런싱 결과")
    if saved_rebalance_plan_df.empty:
        st.info("아직 저장된 리밸런싱 결과가 없습니다. 아래 계산 버튼을 누르면 결과가 rebalance_plan 시트에 저장됩니다.")
    else:
        saved_summary = saved_rebalance_summary(saved_rebalance_plan_df)
        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("저장시각", saved_summary["saved_at"])
        sm2.metric("저장 기준금액(USD)", money_usd(saved_summary["total_usd"]))
        sm3.metric("추가 매수 필요", money_usd(saved_summary["buy_usd"]))
        sm4.metric("매도 필요", money_usd(saved_summary["sell_usd"]))
        st.caption("새로 리밸런싱 계산 버튼을 누르기 전까지 이 결과가 Google Sheets에 유지됩니다. 이 표를 보는 것만으로는 Alpha Vantage API를 호출하지 않습니다.")
        with st.expander("저장된 리밸런싱 주문안 보기", expanded=True):
            st.dataframe(format_saved_rebalance_plan(saved_rebalance_plan_df), use_container_width=True, hide_index=True)
        if saved_rebalance_basis_df.empty:
            st.caption("저장된 계산 근거는 아직 없습니다. 새로 계산하면 rebalance_basis 시트에 함께 저장됩니다.")
        else:
            with st.expander("저장된 계산 근거 보기", expanded=False):
                st.dataframe(format_saved_rebalance_basis(saved_rebalance_basis_df), use_container_width=True, hide_index=True)

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
        laa_annual_last = strategy_rebalance_dates["laa_fixed"]["last_date"]
        laa_monthly_last = strategy_rebalance_dates["laa_variable"]["last_date"]
        vaa_monthly_last = strategy_rebalance_dates["vaa"]["last_date"]
        odm_monthly_last = strategy_rebalance_dates["odm"]["last_date"]
        st.caption("최근 리밸런싱일은 위 일정표의 전략별 대상 ETF 최신 매매일을 자동 사용합니다.")
        zero_is_defensive = st.checkbox("VAA 모멘텀 스코어 0점은 방어 신호로 처리", value=True)
        use_cached_quotes_first = st.checkbox(
            "수동/세션 저장 최신가 우선 사용",
            value=True,
            help="체크하면 수동 입력 또는 이전 조회 가격이 있는 티커는 재조회하지 않고, 부족한 티커만 Alpha Vantage로 조회합니다.",
        )

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
        preview_usd = portfolio_summary["total_usd"]
        preview_krw = portfolio_summary["total_krw"]
        preview_label_usd = "계산 시 최신가 조회" if positions_base.shape[0] > 0 and portfolio_quotes.empty else money_usd(preview_usd)
        preview_label_krw = "계산 시 최신가 조회" if positions_base.shape[0] > 0 and portfolio_quotes.empty else money_krw(preview_krw)
    else:
        if investment_currency == "KRW":
            preview_krw = float(manual_amount)
            preview_usd = preview_krw / usdkrw_rate
        else:
            preview_usd = float(manual_amount)
            preview_krw = preview_usd * usdkrw_rate
        preview_label_usd = money_usd(preview_usd)
        preview_label_krw = money_krw(preview_krw)

    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("리밸런싱 기준금액(USD)", preview_label_usd)
    pc2.metric("리밸런싱 기준금액(KRW)", preview_label_krw)
    pc3.metric("현재 포트폴리오 총자산", money_usd(portfolio_summary["total_usd"]) if not portfolio_quotes.empty or positions_base.empty else "최신가 미조회")
    pc4.metric("적용 환율", fx_rate_krw(usdkrw_rate))
    st.caption("매매일지 입력/저장 단계에서는 가격 API를 호출하지 않습니다. 이 버튼을 누를 때 LAA/VAA/ODM 전략 전체 ETF와 현재 보유종목 최신가 조회를 묶어서 실행합니다.")

    run = st.button("ETF 전략 계산 및 현재 보유수량 반영", type="primary")
    if run:
        data = load_all_monthly_prices(DATA_TICKERS, api_key)
        prices = build_price_matrix(data, DATA_TICKERS, eval_date, lookback_months, exclude_current_month)
        if prices.empty:
            st.error("ETF 월봉 데이터를 가져오지 못했습니다. API Key/호출 제한/기준일을 확인하세요.")
            st.stop()
        actual_eval_dt = prices.index.max()
        st.success(f"전략 계산 기준월: {actual_eval_dt.strftime('%Y-%m-%d')}")

        laa_variable = "SHY" if laa_defensive else "QQQ"
        laa_reason = "두 조건이 모두 충족되어 SHY 선택" if laa_defensive else "두 조건이 동시에 충족되지 않아 QQQ 선택"
        laa_inner = {"IWD": 0.25, "GLD": 0.25, "IEF": 0.25, laa_variable: 0.25}
        laa_rebalance = {
            "IWD": {"cycle": "연 1회", "last_date": laa_annual_last},
            "GLD": {"cycle": "연 1회", "last_date": laa_annual_last},
            "IEF": {"cycle": "연 1회", "last_date": laa_annual_last},
            laa_variable: {"cycle": "월 1회", "last_date": laa_monthly_last},
        }

        try:
            vaa_selected, vaa_scores, vaa_reason = calculate_vaa(prices, zero_is_defensive)
            odm_selected, odm_returns, odm_reason = calculate_dual_momentum(prices)
        except Exception as e:
            st.error(str(e))
            st.stop()

        # 최신가 조회는 이 지점에서 한 번만 실행합니다.
        # 대상: LAA/VAA/ODM 전략에 등장하는 모든 ETF + 현재 보유종목.
        # 이렇게 조회한 가격표를 1) 현재 총자산 평가와 2) 리밸런싱 주문안 계산에 함께 사용합니다.
        strategy_price_tickers = ALL_TICKERS.copy()
        holding_tickers = positions_base["ticker"].dropna().astype(str).str.upper().str.strip().tolist()
        quote_tickers = sorted(set(strategy_price_tickers + holding_tickers))
        quote_df = combine_cached_and_api_quotes(quote_tickers, api_key, use_cached_quotes_first)
        portfolio_status_run, portfolio_summary_run = build_portfolio_status(positions_base, quote_df, cash_balance, usdkrw_rate)

        # 2) 리밸런싱 탭에서 조회한 가격을 1) 자산/매매일지 탭의 총자산 계산에도 즉시 재사용합니다.
        portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist()) if not positions_base.empty else pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
        portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)

        if investment_basis == "현재 포트폴리오 총자산 사용":
            total_investment_usd = portfolio_summary_run["total_usd"]
            total_investment_krw = portfolio_summary_run["total_krw"]
            input_currency = "PORTFOLIO"
        else:
            if investment_currency == "KRW":
                total_investment_krw = float(manual_amount)
                total_investment_usd = total_investment_krw / usdkrw_rate
            else:
                total_investment_usd = float(manual_amount)
                total_investment_krw = total_investment_usd * usdkrw_rate
            input_currency = investment_currency

        if total_investment_usd <= 0:
            st.error("리밸런싱 기준 투자금이 0입니다. 현금/매매일지를 입력하거나 수동 금액을 입력하세요.")
            st.stop()

        t1, t2, t3 = st.columns(3)
        t1.metric("계산 기준 총자산(USD)", money_usd(total_investment_usd))
        t2.metric("계산 기준 총자산(KRW)", money_krw(total_investment_krw))
        valid_price_count = quote_df.dropna(subset=["latest_price_usd"]).shape[0] if not quote_df.empty else 0
        t3.metric("가격 반영 티커 수", f"{valid_price_count}/{len(quote_tickers)}개")

        rows: List[Dict[str, object]] = []
        rows += allocation_rows("LAA", w_laa, laa_inner, laa_rebalance, eval_date, total_investment_krw, total_investment_usd, input_currency, laa_reason)
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

        # 위에서 이미 조회한 quote_df와 portfolio_status_run을 재사용합니다.
        plan = add_rebalance_plan(final, quote_df, portfolio_status_run, usdkrw_rate)
        rebalance_metadata = {
            "eval_date": eval_date,
            "strategy_price_month": actual_eval_dt.date(),
            "usdkrw_rate": usdkrw_rate,
            "usdkrw_source": usdkrw_source,
            "usdkrw_rate_date": usdkrw_rate_date.date() if hasattr(usdkrw_rate_date, "date") else usdkrw_rate_date,
            "investment_basis": investment_basis,
            "input_currency": input_currency,
            "total_investment_usd": total_investment_usd,
            "total_investment_krw": total_investment_krw,
            "laa_selected": laa_variable,
            "vaa_selected": vaa_selected,
            "odm_selected": odm_selected,
        }
        save_rebalance_plan_to_sheet(plan, rebalance_metadata)
        basis_df = build_rebalance_basis_rows(
            rebalance_metadata,
            schedule_preview,
            laa_variable,
            laa_reason,
            laa_defensive,
            vaa_scores,
            vaa_selected,
            vaa_reason,
            odm_returns,
            odm_selected,
            odm_reason,
            quote_df,
            portfolio_status_run,
            plan,
            {"LAA": w_laa, "VAA": w_vaa, "ODM": w_odm},
            lookback_months,
            exclude_current_month,
            zero_is_defensive,
            use_cached_quotes_first,
        )
        save_rebalance_basis_to_sheet(basis_df)
        saved_rebalance_plan_df = load_saved_rebalance_plan()
        saved_rebalance_basis_df = load_saved_rebalance_basis()
        st.success("리밸런싱 결과와 계산 근거를 Google Sheets의 rebalance_plan / rebalance_basis 시트에 저장했습니다. 새로 계산하기 전까지 이 결과와 근거가 유지됩니다.")

        with st.expander("이번 계산 근거 보기", expanded=False):
            st.dataframe(format_saved_rebalance_basis(basis_df), use_container_width=True, hide_index=True)

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
# 1) 자산/매매일지
# =========================================================
with tab_assets:
    st.subheader("현재 자산 현황")
    st.caption("매매일지 저장만으로는 Alpha Vantage를 호출하지 않습니다. 현재가 평가가 필요할 때 아래 버튼을 누르세요.")

    refresh_cols = st.columns([1, 1, 4])
    if refresh_cols[0].button("현재 보유종목 최신가 조회"):
        if positions_base.empty:
            st.warning("조회할 보유종목이 없습니다. 매매일지를 먼저 입력하세요.")
        else:
            fetched_quotes = load_latest_quotes(positions_base["ticker"].tolist(), api_key)
            store_latest_quotes(fetched_quotes)
            portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist())
            portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)
            st.success("현재 보유종목 최신가를 조회해 이번 세션에 저장했습니다.")
    if refresh_cols[1].button("저장된 최신가 지우기"):
        st.session_state["latest_quotes_df"] = pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
        portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist())
        portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)
        st.success("이번 세션에 저장된 최신가를 지웠습니다.")

    if not positions_base.empty:
        with st.expander("종목별 최신가 수동 입력으로 임시 총자산 계산", expanded=portfolio_quotes.empty):
            st.caption("Alpha Vantage를 호출하지 않고 보유종목별 최신가를 직접 입력해 이번 세션의 임시 평가액과 총자산을 계산합니다. 입력값은 Google Sheets에 저장되지 않으며, 앱 세션에만 저장됩니다.")
            manual_template = build_manual_quote_template(positions_base, portfolio_quotes, today)
            manual_edited = st.data_editor(
                manual_template,
                key="manual_quote_editor",
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "ticker": st.column_config.TextColumn("티커", disabled=True),
                    "latest_price_usd": st.column_config.NumberColumn("수동 최신가(USD)", min_value=0.0, step=0.01, format="%.4f"),
                    "price_date": st.column_config.TextColumn("가격 기준일", help="예: 2026-07-10 또는 임시"),
                },
            )
            if st.button("수동 최신가로 임시 총자산 계산", type="secondary"):
                manual_quotes, manual_errors = normalize_manual_quotes(manual_edited)
                if manual_errors:
                    st.error("수동 최신가 저장 전 아래 내용을 확인하세요.")
                    for err in manual_errors:
                        st.write(f"- {err}")
                else:
                    store_latest_quotes(manual_quotes)
                    portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist())
                    portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)
                    st.success(f"수동 최신가 {len(manual_quotes)}건으로 임시 총자산을 계산했습니다. API 호출은 발생하지 않았습니다.")

    if positions_base.empty:
        st.info("아직 보유종목이 없습니다. 현금 또는 매매일지를 입력하세요.")
    elif portfolio_quotes.empty:
        fallback_count = portfolio_summary.get("fallback_positions", 0)
        if fallback_count:
            st.warning(f"최신가를 아직 조회/입력하지 않았습니다. 아래 총자산은 {fallback_count}개 종목을 평균매수가로 임시 반영해 계산합니다. 정확한 평가를 위해 수동 최신가 입력 또는 리밸런싱 계산 버튼을 사용하세요.")
        else:
            st.warning("최신가를 아직 조회/입력하지 않았고 평균매수가도 없는 종목은 총자산에 반영하지 못했습니다. 수동 최신가 입력 또는 리밸런싱 계산 버튼을 사용하세요.")
    else:
        st.info(quote_cache_info(portfolio_quotes))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 자산(USD)", money_usd(portfolio_summary["total_usd"]))
    c2.metric("총 자산(KRW)", money_krw(portfolio_summary["total_krw"]))
    c3.metric("주식/ETF 평가액", money_usd(portfolio_summary["stock_value_usd"]))
    c4.metric("적용 환율", fx_rate_krw(usdkrw_rate))
    st.caption(f"환율 기준: {usdkrw_source} / 기준일: {usdkrw_rate_date.strftime('%Y-%m-%d')}")
    if portfolio_summary.get("fallback_positions", 0):
        st.caption(f"참고: 최신가가 없는 {portfolio_summary['fallback_positions']}개 종목은 평균매수가를 임시 평가가격으로 사용해 총자산에 반영했습니다.")

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
    st.caption("입력란 추가 버튼으로 기존 매매일지 입력 폼 안에서 여러 매수/매도/보정 내역을 한 번에 저장할 수 있습니다. 완전히 빈 행은 자동으로 제외됩니다.")

    if "trade_form_row_count" not in st.session_state:
        st.session_state["trade_form_row_count"] = 1

    fc1, fc2, fc3, fc4 = st.columns([1, 1, 1, 4])
    if fc1.button("입력란 추가"):
        st.session_state["trade_form_row_count"] += 1
        st.rerun()
    if fc2.button("입력란 5개 추가"):
        st.session_state["trade_form_row_count"] += 5
        st.rerun()
    if fc3.button("입력란 초기화"):
        for key in list(st.session_state.keys()):
            if key.startswith("trade_form_") and key != "trade_form_row_count":
                del st.session_state[key]
        st.session_state["trade_form_row_count"] = 1
        st.rerun()

    with st.form("trade_form"):
        trade_inputs = []
        row_count = int(st.session_state.get("trade_form_row_count", 1))

        for i in range(row_count):
            st.markdown(f"**입력 {i + 1}**")
            tc1, tc2, tc3, tc4, tc5, tc6 = st.columns([1, 1, 1, 1, 1, 2])
            trade_date = tc1.date_input("매매일", value=today, key=f"trade_form_date_{i}")
            ticker = tc2.text_input("티커", value="SPY" if i == 0 else "", key=f"trade_form_ticker_{i}").upper().strip()
            side = tc3.selectbox(
                "구분",
                options=["BUY", "SELL", "ADJUST"],
                key=f"trade_form_side_{i}",
                help="ADJUST는 입고/출고/수량 보정용입니다. 수량에 음수를 넣으면 보유수량이 줄어듭니다.",
            )
            quantity = tc4.number_input("수량", value=1.0 if i == 0 else 0.0, step=1.0, format="%.6f", key=f"trade_form_quantity_{i}")
            price_usd = tc5.number_input("체결가(USD)", value=0.0, step=0.01, format="%.4f", key=f"trade_form_price_usd_{i}")
            fee_usd = tc5.number_input("수수료(USD)", value=0.0, step=0.01, format="%.4f", key=f"trade_form_fee_usd_{i}")
            memo = tc6.text_input("메모", value="", key=f"trade_form_memo_{i}")

            trade_inputs.append({
                "row_no": i + 1,
                "trade_date": trade_date,
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "price_usd": price_usd,
                "fee_usd": fee_usd,
                "memo": memo,
            })

        add_trade = st.form_submit_button("매매일지 저장")

    if add_trade:
        rows_to_save = []
        errors = []
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item in trade_inputs:
            row_no = item["row_no"]
            ticker = str(item["ticker"] or "").upper().strip()
            side = str(item["side"] or "").upper().strip()
            quantity = float(item["quantity"] or 0)
            price_usd = float(item["price_usd"] or 0)
            fee_usd = float(item["fee_usd"] or 0)
            memo = str(item["memo"] or "").strip()

            is_blank = (not ticker) and quantity == 0 and price_usd == 0 and fee_usd == 0 and not memo
            if is_blank:
                continue

            if not ticker:
                errors.append(f"{row_no}번 입력란: 티커를 입력하세요.")
                continue
            if side in ["BUY", "SELL"] and quantity <= 0:
                errors.append(f"{row_no}번 입력란: BUY/SELL 수량은 0보다 커야 합니다.")
                continue

            rows_to_save.append({
                "trade_date": item["trade_date"].strftime("%Y-%m-%d"),
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "price_usd": price_usd,
                "fee_usd": fee_usd,
                "memo": memo,
                "created_at": created_at,
            })

        if errors:
            st.error("저장 전 아래 내용을 확인하세요.")
            for err in errors:
                st.write(f"- {err}")
        elif not rows_to_save:
            st.warning("저장할 매매일지가 없습니다. 티커와 수량을 입력하세요.")
        else:
            append_sheet_rows("trades", TRADE_COLUMNS, rows_to_save)
            st.success(f"매매일지 {len(rows_to_save)}건을 Google Sheets에 저장했습니다. 새로고침하면 반영됩니다.")
            st.rerun()

    st.markdown("#### 종목별 보유 현황")
    show_portfolio = portfolio_status.copy()
    if not show_portfolio.empty:
        show_portfolio = show_portfolio.rename(columns={
            "ticker": "티커", "quantity": "보유수량", "avg_buy_price_usd": "평균매수가(USD)",
            "latest_price_usd": "최근가(USD)", "valuation_price_usd": "총자산 반영가격(USD)",
            "price_date": "가격 기준일", "market_value_usd": "평가액(USD)",
            "market_value_krw": "평가액(KRW)", "weight": "비중", "unrealized_pnl_usd": "평가손익(USD)", "unrealized_pnl_pct": "평가손익률",
            "source": "가격 출처", "valuation_source": "총자산 평가 기준", "fetched_at": "입력/조회시각",
        })
        for col in ["평균매수가(USD)", "최근가(USD)", "총자산 반영가격(USD)"]:
            if col in show_portfolio.columns:
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

