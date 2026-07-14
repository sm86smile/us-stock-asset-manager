import calendar
import html
import math
import re
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
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

# 모바일 화면에서도 긴 금액과 날짜가 말줄임표(...)로 잘리지 않도록
# 주요 지표를 반응형 카드로 표시합니다.
st.markdown(
    """
    <style>
    .app-metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
        gap: 0.75rem;
        margin: 0.25rem 0 0.85rem 0;
        width: 100%;
    }
    .app-metric-card {
        min-width: 0;
        padding: 0.82rem 0.92rem;
        border: 1px solid rgba(128, 128, 128, 0.28);
        border-radius: 0.68rem;
        background: rgba(128, 128, 128, 0.045);
        box-sizing: border-box;
    }
    .app-metric-label {
        font-size: 0.82rem;
        line-height: 1.35;
        opacity: 0.72;
        white-space: normal;
        overflow: visible;
        text-overflow: clip;
        overflow-wrap: anywhere;
        word-break: keep-all;
    }
    .app-metric-value {
        margin-top: 0.28rem;
        font-size: clamp(1.05rem, 2vw, 1.55rem);
        font-weight: 600;
        line-height: 1.32;
        white-space: normal;
        overflow: visible;
        text-overflow: clip;
        overflow-wrap: anywhere;
        word-break: break-word;
        font-variant-numeric: tabular-nums;
    }
    @media (max-width: 700px) {
        .app-metric-grid {
            grid-template-columns: 1fr;
            gap: 0.55rem;
        }
        .app-metric-card {
            padding: 0.72rem 0.82rem;
        }
        .app-metric-value {
            font-size: 1.22rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
API_CALL_DELAY_SECONDS = 1.25
RAW_MONTHS_TO_SAVE = 61  # rebalance_basis에 ETF별 최근 월봉 RAW를 저장하는 최대 개수
DEFAULT_USDKRW_RATE = 1380.0  # settings 시트 환율을 읽지 못할 때만 사용하는 내부 기본값
KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """실행 서버 위치와 무관하게 현재 한국 표준시를 반환합니다."""
    return datetime.now(KST)


def today_kst() -> date:
    """실행 서버 위치와 무관하게 한국 기준 오늘 날짜를 반환합니다."""
    return now_kst().date()
GSHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 전략별 ETF 티커는 Google Sheets의 strategy_tickers 시트에서 읽습니다.
# 시트가 처음 생성될 때 아래 기본값이 자동 저장됩니다.
STRATEGY_TICKER_COLUMNS = [
    "strategy_key", "strategy_name", "group_key", "group_name",
    "role_key", "role_name", "sort_order", "ticker", "asset_class", "updated_at",
]

DEFAULT_STRATEGY_TICKER_ROWS = [
    {"strategy_key": "laa", "strategy_name": "LAA", "group_key": "fixed", "group_name": "고정자산 75%", "role_key": "laa_fixed_1", "role_name": "고정자산-미국가치주", "sort_order": 1, "ticker": "IWD", "asset_class": "미국 대형·중형 가치주"},
    {"strategy_key": "laa", "strategy_name": "LAA", "group_key": "fixed", "group_name": "고정자산 75%", "role_key": "laa_fixed_2", "role_name": "고정자산-금", "sort_order": 2, "ticker": "GLD", "asset_class": "실물 금"},
    {"strategy_key": "laa", "strategy_name": "LAA", "group_key": "fixed", "group_name": "고정자산 75%", "role_key": "laa_fixed_3", "role_name": "고정자산-미국국채", "sort_order": 3, "ticker": "IEF", "asset_class": "미국 7~10년 국채"},
    {"strategy_key": "laa", "strategy_name": "LAA", "group_key": "variable", "group_name": "변동자산 25%", "role_key": "laa_variable_risk", "role_name": "타이밍 공격자산-미국성장주", "sort_order": 4, "ticker": "QQQ", "asset_class": "나스닥100"},
    {"strategy_key": "laa", "strategy_name": "LAA", "group_key": "variable", "group_name": "변동자산 25%", "role_key": "laa_variable_defensive", "role_name": "타이밍 방어자산-단기/초단기 국채", "sort_order": 5, "ticker": "SHY", "asset_class": "미국 1~3년 국채"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "attack", "group_name": "공격형", "role_key": "vaa_attack_1", "role_name": "공격자산-미국주식", "sort_order": 1, "ticker": "SPY", "asset_class": "S&P 500"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "attack", "group_name": "공격형", "role_key": "vaa_attack_2", "role_name": "공격자산-선진국주식", "sort_order": 2, "ticker": "EFA", "asset_class": "선진국 대형·중형주"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "attack", "group_name": "공격형", "role_key": "vaa_attack_3", "role_name": "공격자산-신흥국주식", "sort_order": 3, "ticker": "EEM", "asset_class": "신흥국 대형·중형주"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "attack", "group_name": "공격형", "role_key": "vaa_attack_4", "role_name": "공격형 채권후보-미국 종합채권", "sort_order": 4, "ticker": "AGG", "asset_class": "미국 종합채권"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "safe", "group_name": "방어형", "role_key": "vaa_safe_1", "role_name": "방어자산-투자등급 회사채", "sort_order": 5, "ticker": "LQD", "asset_class": "장기 투자등급 회사채"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "safe", "group_name": "방어형", "role_key": "vaa_safe_2", "role_name": "방어자산-미국국채", "sort_order": 6, "ticker": "IEF", "asset_class": "미국 7~10년 국채"},
    {"strategy_key": "vaa", "strategy_name": "VAA", "group_key": "safe", "group_name": "방어형", "role_key": "vaa_safe_3", "role_name": "방어자산-단기/초단기 국채", "sort_order": 7, "ticker": "SHY", "asset_class": "미국 1~3년 국채"},
    {"strategy_key": "odm", "strategy_name": "오리지널 듀얼 모멘텀", "group_key": "roles", "group_name": "비교/선택 자산", "role_key": "odm_us_equity", "role_name": "공격자산-미국주식", "sort_order": 1, "ticker": "SPY", "asset_class": "S&P 500"},
    {"strategy_key": "odm", "strategy_name": "오리지널 듀얼 모멘텀", "group_key": "roles", "group_name": "비교/선택 자산", "role_key": "odm_intl_equity", "role_name": "공격자산-해외주식", "sort_order": 2, "ticker": "EFA", "asset_class": "선진국 대형·중형주"},
    {"strategy_key": "odm", "strategy_name": "오리지널 듀얼 모멘텀", "group_key": "roles", "group_name": "비교/선택 자산", "role_key": "odm_cash", "role_name": "절대모멘텀 기준자산-현금성/초단기 국채", "sort_order": 3, "ticker": "BIL", "asset_class": "미국 1~3개월 국채"},
    {"strategy_key": "odm", "strategy_name": "오리지널 듀얼 모멘텀", "group_key": "roles", "group_name": "비교/선택 자산", "role_key": "odm_bond", "role_name": "방어자산-미국 종합채권", "sort_order": 4, "ticker": "AGG", "asset_class": "미국 종합채권"},
]

DEFAULT_ETF_LABELS = {row["ticker"]: row["asset_class"] for row in DEFAULT_STRATEGY_TICKER_ROWS}


# 첨부된 "미국 ETF 전략별·자산군별 선택 가이드"를 앱에서 직접 조회하고
# 전략 티커 변경 시 역할/자산군을 자동 입력하기 위한 내장 데이터입니다.
ETF_ASSET_GUIDE_ROWS = [{'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '미국 대형·중형 가치주',
  'ticker': 'IWD',
  'best_for': '원래 LAA 구조와 백테스트를 최대한 유지하고 중형 가치주까지 포함할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '미국 대형 가치주',
  'ticker': 'VTV',
  'best_for': '낮은 비용, 장기 보유, 대형 가치주 코어 구성을 원할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '미국 광범위 가치주',
  'ticker': 'IUSV',
  'best_for': '대형·중형 가치주를 폭넓고 저비용으로 보유할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '미국 대형 가치주',
  'ticker': 'SCHV',
  'best_for': 'Schwab 저비용 가치주 ETF를 선호할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': 'S&P 500 가치주',
  'ticker': 'SPYV',
  'best_for': 'S&P 500 구성 종목 중 가치주에 집중할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': 'S&P 500 가치주',
  'ticker': 'VOOV',
  'best_for': 'Vanguard 상품으로 S&P 500 가치주를 보유할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '미국 가치 팩터',
  'ticker': 'VLUE',
  'best_for': '시가총액형보다 가치 팩터 노출을 강화할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '배당·퀄리티 가치주',
  'ticker': 'SCHD',
  'best_for': '가치주 성격과 배당의 질·지속성을 함께 중시할 때'},
 {'section': '2.1 고정자산 - 미국 가치주',
  'category_key': 'laa_fixed_value',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국가치주',
  'asset_class': '미국 고배당주',
  'ticker': 'VYM',
  'best_for': '가치주 성격과 높은 배당수익을 함께 추구할 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'GLD',
  'best_for': '유동성과 거래량이 중요하거나 비교적 자주 매매할 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'GLDM',
  'best_for': '장기 보유 비용과 낮은 주당 가격을 중시할 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'IAU',
  'best_for': '낮은 비용과 충분한 유동성의 균형을 원할 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'IAUM',
  'best_for': '소액 장기투자와 낮은 보수를 우선할 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'SGOL',
  'best_for': '물리적 금 보유 구조와 보관 투명성을 중시할 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'BAR',
  'best_for': '저비용으로 금 현물 가격을 추종하고 싶을 때'},
 {'section': '2.2 고정자산 - 금',
  'category_key': 'laa_fixed_gold',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-금',
  'asset_class': '실물 금',
  'ticker': 'AAAU',
  'best_for': '다른 운용사의 물리적 금 보유 상품을 원할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 7~10년 국채',
  'ticker': 'IEF',
  'best_for': '경기침체·금리 하락 시 채권 가격 상승을 적극적으로 기대할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 중기국채',
  'ticker': 'VGIT',
  'best_for': '특정 7~10년에 집중하지 않고 중기 국채 전반에 분산할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 중기국채',
  'ticker': 'SCHR',
  'best_for': '저비용 중기 미국 국채를 선호할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 중기국채',
  'ticker': 'SPTI',
  'best_for': 'SPDR 계열로 중기 국채 노출을 구성할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 3~7년 국채',
  'ticker': 'IEI',
  'best_for': 'IEF보다 금리 민감도와 가격 변동을 낮추고 싶을 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 국채 전 만기',
  'ticker': 'GOVT',
  'best_for': '단기부터 장기까지 하나의 ETF로 분산할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'TLT',
  'best_for': '심각한 침체·디플레이션과 장기금리 급락에 베팅할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'VGLT',
  'best_for': '저비용 장기국채를 보유할 때'},
 {'section': '2.3 고정자산 - 미국 국채',
  'category_key': 'laa_fixed_treasury',
  'strategy': 'LAA',
  'guide_role': '고정자산',
  'auto_role': '고정자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'SPTL',
  'best_for': '장기 듀레이션 민감도를 크게 가져갈 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '나스닥100',
  'ticker': 'QQQ',
  'best_for': '원래 전략 유지, 풍부한 거래량, 잦은 매매가 필요할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '나스닥100',
  'ticker': 'QQQM',
  'best_for': 'QQQ와 같은 지수를 장기·저비용으로 보유할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '미국 대형 성장주',
  'ticker': 'VUG',
  'best_for': '나스닥 상장 여부와 무관하게 대형 성장주 전체에 투자할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '미국 대형 성장주',
  'ticker': 'SCHG',
  'best_for': '저비용 대형 성장주 ETF를 선호할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': 'Russell 1000 성장주',
  'ticker': 'IWF',
  'best_for': '대형·중형 성장주까지 폭넓게 포함할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': 'S&P 500 성장주',
  'ticker': 'SPYG',
  'best_for': 'S&P 500 내 성장주로 범위를 제한할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': 'S&P 500 성장주',
  'ticker': 'VOOG',
  'best_for': 'Vanguard 상품으로 S&P 500 성장주를 보유할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '나스닥 종합시장',
  'ticker': 'ONEQ',
  'best_for': '나스닥100보다 더 많은 나스닥 기업을 포함할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '미국 기술주',
  'ticker': 'VGT',
  'best_for': '성장주 전체보다 정보기술 업종에 집중할 때'},
 {'section': '2.4 타이밍 공격자산 - 미국 성장주',
  'category_key': 'laa_timing_risk',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 공격자산-미국성장주',
  'asset_class': '미국 기술주',
  'ticker': 'XLK',
  'best_for': 'S&P 500 내 대형 기술주에 집중할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 1~3년 국채',
  'ticker': 'SHY',
  'best_for': '위험회피 중에도 금리 하락에 따른 일부 채권 가격 상승을 기대할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 단기국채',
  'ticker': 'VGSH',
  'best_for': '저비용으로 단기 국채 전반을 보유할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 단기국채',
  'ticker': 'SCHO',
  'best_for': 'Schwab 저비용 단기 국채를 선호할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 단기국채',
  'ticker': 'SPTS',
  'best_for': 'SPDR 단기 국채 ETF를 이용할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 1~3개월 국채',
  'ticker': 'BIL',
  'best_for': '가격 변동을 거의 없애고 단기 이자수익을 기대할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 0~3개월 국채',
  'ticker': 'SGOV',
  'best_for': '저비용 초단기 국채를 장기 대기자금으로 활용할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 3개월 국채',
  'ticker': 'TBIL',
  'best_for': '3개월물 국채수익률에 가깝게 투자할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 초단기국채',
  'ticker': 'GBIL',
  'best_for': '여러 초단기 국채에 분산할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 1년 이하 국채',
  'ticker': 'SHV',
  'best_for': 'BIL보다 만기 범위를 넓혀 이자수익을 조금 높이고 싶을 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 변동금리 국채',
  'ticker': 'USFR',
  'best_for': '고금리·금리 상승기에 이자율이 빠르게 조정되는 구조를 원할 때'},
 {'section': '2.5 타이밍 방어자산 - 단기·초단기 국채',
  'category_key': 'laa_timing_defensive',
  'strategy': 'LAA',
  'guide_role': '타이밍자산',
  'auto_role': '타이밍 방어자산-단기/초단기 국채',
  'asset_class': '미국 변동금리 국채',
  'ticker': 'TFLO',
  'best_for': '금리 상승 위험을 낮추고 단기금리 연동 이자를 추구할 때'},
 {'section': '2.6 시장 판단지표',
  'category_key': 'laa_signal',
  'strategy': 'LAA',
  'guide_role': '추세 판단',
  'auto_role': '추세 판단지표-S&P 500',
  'asset_class': 'S&P 500',
  'ticker': 'SPY',
  'best_for': '풍부한 과거 데이터로 200일 이동평균을 계산하고 원형 신호를 유지할 때'},
 {'section': '2.6 시장 판단지표',
  'category_key': 'laa_signal',
  'strategy': 'LAA',
  'guide_role': '추세 판단',
  'auto_role': '추세 판단지표-S&P 500',
  'asset_class': 'S&P 500',
  'ticker': 'VOO',
  'best_for': '실제 보유 ETF와 신호 ETF를 VOO로 통일할 때'},
 {'section': '2.6 시장 판단지표',
  'category_key': 'laa_signal',
  'strategy': 'LAA',
  'guide_role': '추세 판단',
  'auto_role': '추세 판단지표-S&P 500',
  'asset_class': 'S&P 500',
  'ticker': 'IVV',
  'best_for': 'iShares 상품으로 투자와 신호를 통일할 때'},
 {'section': '2.6 시장 판단지표',
  'category_key': 'laa_signal',
  'strategy': 'LAA',
  'guide_role': '추세 판단',
  'auto_role': '추세 판단지표-S&P 500',
  'asset_class': 'S&P 500',
  'ticker': 'SPLG',
  'best_for': '낮은 비용과 낮은 주당 가격을 중시할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'SPY',
  'best_for': '원래 VAA와 백테스트 일관성, 높은 거래량을 중시할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'VOO',
  'best_for': '장기 보유 비용을 낮추고 싶을 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'IVV',
  'best_for': 'iShares 상품으로 통일할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'SPLG',
  'best_for': '낮은 비용과 낮은 주당 가격을 중시할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 전체시장',
  'ticker': 'VTI',
  'best_for': '중소형주까지 포함한 미국 전체시장 모멘텀을 이용할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 전체시장',
  'ticker': 'ITOT',
  'best_for': 'iShares 전체시장 ETF를 사용할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 전체시장',
  'ticker': 'SCHB',
  'best_for': 'Schwab 저비용 전체시장 ETF를 사용할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 대형주',
  'ticker': 'SCHX',
  'best_for': 'S&P 500보다 조금 넓은 미국 대형주에 투자할 때'},
 {'section': '3.1 공격형 - 미국 주식',
  'category_key': 'vaa_attack_us',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 대형주',
  'ticker': 'VV',
  'best_for': '미국 대형주 전반을 폭넓게 담을 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '선진국 대형·중형주',
  'ticker': 'EFA',
  'best_for': '원래 VAA 재현과 대형·중형주 중심의 신호를 유지할 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '선진국 대·중·소형주',
  'ticker': 'IEFA',
  'best_for': '소형주까지 포함해 선진국 시장을 폭넓게 담을 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '미국 제외 선진국',
  'ticker': 'VEA',
  'best_for': '장기 저비용과 넓은 선진국 분산을 중시할 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '미국 제외 선진국',
  'ticker': 'SCHF',
  'best_for': '대형·중형주 중심 저비용 국제주식 ETF를 원할 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '미국 제외 선진국',
  'ticker': 'SPDW',
  'best_for': 'SPDR 저비용 선진국 ETF를 사용할 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '미국 제외 선진국',
  'ticker': 'IDEV',
  'best_for': 'iShares의 광범위 선진국 ETF를 사용할 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '선진국 가치주',
  'ticker': 'IVLU',
  'best_for': '선진국 가치 팩터 노출을 강화할 때'},
 {'section': '3.2 공격형 - 미국 제외 선진국',
  'category_key': 'vaa_attack_developed',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-선진국주식',
  'asset_class': '해외 고배당주',
  'ticker': 'SCHY',
  'best_for': '해외 주식과 배당 품질을 함께 중시할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 대형·중형주',
  'ticker': 'EEM',
  'best_for': '원래 VAA 재현과 높은 거래량을 중시할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 대·중·소형주',
  'ticker': 'IEMG',
  'best_for': '광범위·저비용으로 신흥국 시장을 담을 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 광범위',
  'ticker': 'VWO',
  'best_for': 'Vanguard 상품으로 신흥국을 장기 보유할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 광범위',
  'ticker': 'SCHE',
  'best_for': 'Schwab 저비용 신흥국 ETF를 선호할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 광범위',
  'ticker': 'SPEM',
  'best_for': '대·중·소형주를 포괄하는 SPDR 상품을 사용할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '중국 제외 신흥국',
  'ticker': 'EMXC',
  'best_for': '중국 비중을 제외하고 다른 신흥국에 투자할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 펀더멘털',
  'ticker': 'FNDE',
  'best_for': '시가총액 대신 펀더멘털 가중을 선호할 때'},
 {'section': '3.3 공격형 - 신흥국',
  'category_key': 'vaa_attack_emerging',
  'strategy': 'VAA',
  'guide_role': '공격형',
  'auto_role': '공격자산-신흥국주식',
  'asset_class': '신흥국 액티브·팩터',
  'ticker': 'AVEM',
  'best_for': '단순 지수보다 팩터·액티브 운용을 선호할 때'},
 {'section': '3.4 공격형 내 채권 후보 - 미국 종합채권',
  'category_key': 'vaa_attack_bond',
  'strategy': 'VAA',
  'guide_role': '공격형 후보',
  'auto_role': '공격형 채권후보-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'AGG',
  'best_for': '원래 전략을 유지하고 미국 투자등급 채권시장 전반에 투자할 때'},
 {'section': '3.4 공격형 내 채권 후보 - 미국 종합채권',
  'category_key': 'vaa_attack_bond',
  'strategy': 'VAA',
  'guide_role': '공격형 후보',
  'auto_role': '공격형 채권후보-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'BND',
  'best_for': 'Vanguard 저비용 종합채권을 사용할 때'},
 {'section': '3.4 공격형 내 채권 후보 - 미국 종합채권',
  'category_key': 'vaa_attack_bond',
  'strategy': 'VAA',
  'guide_role': '공격형 후보',
  'auto_role': '공격형 채권후보-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'SCHZ',
  'best_for': 'Schwab 저비용 종합채권을 사용할 때'},
 {'section': '3.4 공격형 내 채권 후보 - 미국 종합채권',
  'category_key': 'vaa_attack_bond',
  'strategy': 'VAA',
  'guide_role': '공격형 후보',
  'auto_role': '공격형 채권후보-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'SPAB',
  'best_for': 'SPDR 미국 종합채권을 사용할 때'},
 {'section': '3.4 공격형 내 채권 후보 - 미국 종합채권',
  'category_key': 'vaa_attack_bond',
  'strategy': 'VAA',
  'guide_role': '공격형 후보',
  'auto_role': '공격형 채권후보-미국 종합채권',
  'asset_class': '미국 광범위 채권',
  'ticker': 'IUSB',
  'best_for': '일부 비투자등급을 포함한 더 넓은 채권시장을 원할 때'},
 {'section': '3.4 공격형 내 채권 후보 - 미국 종합채권',
  'category_key': 'vaa_attack_bond',
  'strategy': 'VAA',
  'guide_role': '공격형 후보',
  'auto_role': '공격형 채권후보-미국 종합채권',
  'asset_class': '액티브 코어채권',
  'ticker': 'FBND',
  'best_for': '운용자가 금리와 신용 비중을 조절하는 상품을 선호할 때'},
 {'section': '3.5 방어형 - 투자등급 회사채',
  'category_key': 'vaa_safe_corporate',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-투자등급 회사채',
  'asset_class': '장기 투자등급 회사채',
  'ticker': 'LQD',
  'best_for': '침체가 심하지 않고 회사채 스프레드가 안정적이며 금리 하락이 예상될 때'},
 {'section': '3.5 방어형 - 투자등급 회사채',
  'category_key': 'vaa_safe_corporate',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-투자등급 회사채',
  'asset_class': '단기 투자등급 회사채',
  'ticker': 'VCSH',
  'best_for': '회사채 이자를 원하지만 금리 변동성을 낮추고 싶을 때'},
 {'section': '3.5 방어형 - 투자등급 회사채',
  'category_key': 'vaa_safe_corporate',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-투자등급 회사채',
  'asset_class': '단기 투자등급 회사채',
  'ticker': 'IGSB',
  'best_for': '짧은 듀레이션과 투자등급 회사채 수익을 함께 추구할 때'},
 {'section': '3.5 방어형 - 투자등급 회사채',
  'category_key': 'vaa_safe_corporate',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-투자등급 회사채',
  'asset_class': '중기 투자등급 회사채',
  'ticker': 'VCIT',
  'best_for': 'LQD보다 만기를 줄이면서 회사채 수익을 추구할 때'},
 {'section': '3.5 방어형 - 투자등급 회사채',
  'category_key': 'vaa_safe_corporate',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-투자등급 회사채',
  'asset_class': '중기 투자등급 회사채',
  'ticker': 'IGIB',
  'best_for': '중기 회사채에 폭넓게 분산할 때'},
 {'section': '3.5 방어형 - 투자등급 회사채',
  'category_key': 'vaa_safe_corporate',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-투자등급 회사채',
  'asset_class': '단기 투자등급 회사채',
  'ticker': 'SPSB',
  'best_for': '가격 변동을 낮추면서 회사채 이자를 추구할 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 7~10년 국채',
  'ticker': 'IEF',
  'best_for': '경기침체와 금리 인하가 예상될 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 중기국채',
  'ticker': 'VGIT',
  'best_for': '중기 국채 전반에 분산할 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 중기국채',
  'ticker': 'SCHR',
  'best_for': '저비용 중기국채를 선호할 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 3~7년 국채',
  'ticker': 'IEI',
  'best_for': 'IEF보다 가격 변동을 낮출 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'TLT',
  'best_for': '심각한 침체와 장기금리 급락을 예상할 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'VGLT',
  'best_for': '저비용 장기국채를 선호할 때'},
 {'section': '3.6 방어형 - 미국 국채',
  'category_key': 'vaa_safe_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'SPTL',
  'best_for': '장기 듀레이션 방어를 강하게 가져갈 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 1~3년 국채',
  'ticker': 'SHY',
  'best_for': '방어하면서 금리 하락에 따른 일부 가격 상승도 기대할 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 단기국채',
  'ticker': 'VGSH',
  'best_for': '단기 국채를 저비용으로 분산할 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 단기국채',
  'ticker': 'SCHO',
  'best_for': 'Schwab 단기 국채를 사용할 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 초단기국채',
  'ticker': 'BIL',
  'best_for': '가격 변동 최소화가 최우선일 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 초단기국채',
  'ticker': 'SGOV',
  'best_for': '낮은 비용과 단기 이자수익을 중시할 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 변동금리 국채',
  'ticker': 'USFR',
  'best_for': '금리 상승·고금리 장기화에 대비할 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 변동금리 국채',
  'ticker': 'TFLO',
  'best_for': '단기금리에 연동되는 이자수익을 추구할 때'},
 {'section': '3.7 방어형 - 단기·초단기 국채',
  'category_key': 'vaa_safe_short_treasury',
  'strategy': 'VAA',
  'guide_role': '방어형',
  'auto_role': '방어자산-단기/초단기 국채',
  'asset_class': '미국 1년 이하 국채',
  'ticker': 'SHV',
  'best_for': 'BIL보다 만기를 넓혀 이자수익을 조금 높일 때'},
 {'section': '4.1 미국 공격자산',
  'category_key': 'odm_attack_us',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'SPY',
  'best_for': '원래 전략과 백테스트를 최대한 유지할 때'},
 {'section': '4.1 미국 공격자산',
  'category_key': 'odm_attack_us',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'VOO',
  'best_for': '장기 보유 비용을 낮추고 싶을 때'},
 {'section': '4.1 미국 공격자산',
  'category_key': 'odm_attack_us',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': 'S&P 500',
  'ticker': 'IVV',
  'best_for': 'iShares 상품으로 통일할 때'},
 {'section': '4.1 미국 공격자산',
  'category_key': 'odm_attack_us',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 전체시장',
  'ticker': 'VTI',
  'best_for': '중소형주까지 포함한 미국 전체시장 모멘텀을 이용할 때'},
 {'section': '4.1 미국 공격자산',
  'category_key': 'odm_attack_us',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-미국주식',
  'asset_class': '미국 전체시장',
  'ticker': 'ITOT',
  'best_for': 'iShares 전체시장 ETF를 사용할 때'},
 {'section': '4.2 해외 공격자산',
  'category_key': 'odm_attack_intl',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-해외주식',
  'asset_class': '선진국 대형·중형주',
  'ticker': 'EFA',
  'best_for': '원래 전략을 재현하고 선진국 대형·중형주 신호를 유지할 때'},
 {'section': '4.2 해외 공격자산',
  'category_key': 'odm_attack_intl',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-해외주식',
  'asset_class': '선진국 대·중·소형주',
  'ticker': 'IEFA',
  'best_for': '해외 선진국 전체시장으로 범위를 넓힐 때'},
 {'section': '4.2 해외 공격자산',
  'category_key': 'odm_attack_intl',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-해외주식',
  'asset_class': '미국 제외 선진국',
  'ticker': 'VEA',
  'best_for': '장기 비용을 낮추고 넓게 분산할 때'},
 {'section': '4.2 해외 공격자산',
  'category_key': 'odm_attack_intl',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-해외주식',
  'asset_class': '미국 제외 선진국',
  'ticker': 'SCHF',
  'best_for': '대형·중형 중심의 국제주식을 원할 때'},
 {'section': '4.2 해외 공격자산',
  'category_key': 'odm_attack_intl',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-해외주식',
  'asset_class': '전 세계 미국 제외',
  'ticker': 'VXUS',
  'best_for': '선진국뿐 아니라 신흥국까지 한 번에 포함할 때'},
 {'section': '4.2 해외 공격자산',
  'category_key': 'odm_attack_intl',
  'strategy': '듀얼모멘텀',
  'guide_role': '공격형',
  'auto_role': '공격자산-해외주식',
  'asset_class': '전 세계 미국 제외',
  'ticker': 'IXUS',
  'best_for': '미국 제외 글로벌 주식 전체에 투자할 때'},
 {'section': '4.3 절대모멘텀 기준자산',
  'category_key': 'odm_cash',
  'strategy': '듀얼모멘텀',
  'guide_role': '기준자산',
  'auto_role': '절대모멘텀 기준자산-현금성/초단기 국채',
  'asset_class': '미국 1~3개월 국채',
  'ticker': 'BIL',
  'best_for': '원형에 가까운 현금성 기준을 유지할 때'},
 {'section': '4.3 절대모멘텀 기준자산',
  'category_key': 'odm_cash',
  'strategy': '듀얼모멘텀',
  'guide_role': '기준자산',
  'auto_role': '절대모멘텀 기준자산-현금성/초단기 국채',
  'asset_class': '미국 0~3개월 국채',
  'ticker': 'SGOV',
  'best_for': '낮은 비용과 초단기 국채 수익률을 기준으로 사용할 때'},
 {'section': '4.3 절대모멘텀 기준자산',
  'category_key': 'odm_cash',
  'strategy': '듀얼모멘텀',
  'guide_role': '기준자산',
  'auto_role': '절대모멘텀 기준자산-현금성/초단기 국채',
  'asset_class': '미국 3개월 국채',
  'ticker': 'TBIL',
  'best_for': '3개월물 국채수익률에 가깝게 기준을 설정할 때'},
 {'section': '4.3 절대모멘텀 기준자산',
  'category_key': 'odm_cash',
  'strategy': '듀얼모멘텀',
  'guide_role': '기준자산',
  'auto_role': '절대모멘텀 기준자산-현금성/초단기 국채',
  'asset_class': '미국 1년 이하 국채',
  'ticker': 'SHV',
  'best_for': 'BIL보다 조금 높은 기준수익률을 적용할 때'},
 {'section': '4.3 절대모멘텀 기준자산',
  'category_key': 'odm_cash',
  'strategy': '듀얼모멘텀',
  'guide_role': '기준자산',
  'auto_role': '절대모멘텀 기준자산-현금성/초단기 국채',
  'asset_class': '미국 변동금리 국채',
  'ticker': 'USFR',
  'best_for': '고금리 환경에서 더 높은 현금성 기준을 적용할 때'},
 {'section': '4.3 절대모멘텀 기준자산',
  'category_key': 'odm_cash',
  'strategy': '듀얼모멘텀',
  'guide_role': '기준자산',
  'auto_role': '절대모멘텀 기준자산-현금성/초단기 국채',
  'asset_class': '미국 변동금리 국채',
  'ticker': 'TFLO',
  'best_for': '단기금리 변화가 빠르게 반영되는 기준을 원할 때'},
 {'section': '4.4 방어자산 - 미국 종합채권',
  'category_key': 'odm_safe_aggregate',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'AGG',
  'best_for': '원래 전략에 가까운 분산형 채권 방어를 원할 때'},
 {'section': '4.4 방어자산 - 미국 종합채권',
  'category_key': 'odm_safe_aggregate',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'BND',
  'best_for': '장기 저비용 종합채권을 선호할 때'},
 {'section': '4.4 방어자산 - 미국 종합채권',
  'category_key': 'odm_safe_aggregate',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'SCHZ',
  'best_for': 'Schwab 상품으로 통일할 때'},
 {'section': '4.4 방어자산 - 미국 종합채권',
  'category_key': 'odm_safe_aggregate',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국 종합채권',
  'asset_class': '미국 종합채권',
  'ticker': 'SPAB',
  'best_for': 'SPDR 상품으로 통일할 때'},
 {'section': '4.4 방어자산 - 미국 종합채권',
  'category_key': 'odm_safe_aggregate',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국 종합채권',
  'asset_class': '미국 광범위 채권',
  'ticker': 'IUSB',
  'best_for': '종합채권보다 조금 넓은 채권시장에 투자할 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 7~10년 국채',
  'ticker': 'IEF',
  'best_for': '경기침체와 금리 하락 시 강한 채권 가격 상승을 기대할 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 3~7년 국채',
  'ticker': 'IEI',
  'best_for': 'IEF보다 변동성을 줄일 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 중기국채',
  'ticker': 'VGIT',
  'best_for': '특정 만기에 집중하지 않고 중기국채를 보유할 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 국채 전 만기',
  'ticker': 'GOVT',
  'best_for': '단기부터 장기까지 국채를 분산할 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 장기국채',
  'ticker': 'TLT',
  'best_for': '강한 침체·디플레이션과 장기금리 급락에 대비할 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 단기국채',
  'ticker': 'SHY',
  'best_for': '방어자산의 가격 변동을 낮추면서 일부 금리 하락 수혜를 기대할 때'},
 {'section': '4.5 방어자산 - 미국 국채',
  'category_key': 'odm_safe_treasury',
  'strategy': '듀얼모멘텀',
  'guide_role': '방어형',
  'auto_role': '방어자산-미국국채',
  'asset_class': '미국 초단기국채',
  'ticker': 'BIL',
  'best_for': '주식과 중장기채 가격 하락을 모두 피하고 현금성 수익을 원할 때'},
 {'section': '5.1 물가연동국채',
  'category_key': 'supplement_tips',
  'strategy': 'LAA·VAA·듀얼',
  'guide_role': '보완 방어',
  'auto_role': '보완 방어자산-물가연동국채',
  'asset_class': '미국 물가연동국채',
  'ticker': 'TIP',
  'best_for': '중장기 기대인플레이션이 상승할 때'},
 {'section': '5.1 물가연동국채',
  'category_key': 'supplement_tips',
  'strategy': 'LAA·VAA·듀얼',
  'guide_role': '보완 방어',
  'auto_role': '보완 방어자산-물가연동국채',
  'asset_class': '단기 물가연동국채',
  'ticker': 'VTIP',
  'best_for': '인플레이션 방어는 원하지만 금리 변동을 줄일 때'},
 {'section': '5.1 물가연동국채',
  'category_key': 'supplement_tips',
  'strategy': 'LAA·VAA·듀얼',
  'guide_role': '보완 방어',
  'auto_role': '보완 방어자산-물가연동국채',
  'asset_class': '단기 물가연동국채',
  'ticker': 'STIP',
  'best_for': '단기 TIPS로 실질금리 위험을 낮출 때'},
 {'section': '5.1 물가연동국채',
  'category_key': 'supplement_tips',
  'strategy': 'LAA·VAA·듀얼',
  'guide_role': '보완 방어',
  'auto_role': '보완 방어자산-물가연동국채',
  'asset_class': '미국 물가연동국채',
  'ticker': 'SCHP',
  'best_for': '미국 TIPS 시장 전체에 저비용으로 투자할 때'},
 {'section': '5.2 원자재·에너지',
  'category_key': 'supplement_commodity',
  'strategy': 'LAA',
  'guide_role': '대체 실물자산',
  'auto_role': '대체 실물자산-원자재/에너지',
  'asset_class': '광범위 원자재',
  'ticker': 'DBC',
  'best_for': '에너지·금속·농산물 전반을 통한 인플레이션 대응을 원할 때'},
 {'section': '5.2 원자재·에너지',
  'category_key': 'supplement_commodity',
  'strategy': 'LAA',
  'guide_role': '대체 실물자산',
  'auto_role': '대체 실물자산-원자재/에너지',
  'asset_class': '광범위 원자재',
  'ticker': 'PDBC',
  'best_for': '원자재 선물의 세금 서류 편의성과 액티브 운용을 선호할 때'},
 {'section': '5.2 원자재·에너지',
  'category_key': 'supplement_commodity',
  'strategy': 'LAA',
  'guide_role': '대체 실물자산',
  'auto_role': '대체 실물자산-원자재/에너지',
  'asset_class': '광범위 원자재',
  'ticker': 'GSG',
  'best_for': '에너지 비중이 높은 원자재 지수에 투자할 때'},
 {'section': '5.2 원자재·에너지',
  'category_key': 'supplement_commodity',
  'strategy': 'LAA',
  'guide_role': '대체 실물자산',
  'auto_role': '대체 실물자산-원자재/에너지',
  'asset_class': '에너지 주식',
  'ticker': 'XLE',
  'best_for': '유가 상승과 에너지 기업 이익 증가에 집중할 때'}]

ETF_GUIDE_QUICK_ROWS = [{'전략': 'LAA', '역할': '가치주 고정자산', '우선 추천 ETF': 'VTV', '대체 후보': 'IWD, IUSV, SCHV', '핵심 선택 기준': '원형 재현은 IWD, 저비용 대형 가치주 코어는 VTV'},
 {'전략': 'LAA', '역할': '금 고정자산', '우선 추천 ETF': 'GLD 또는 GLDM', '대체 후보': 'IAU, IAUM, SGOL', '핵심 선택 기준': '유동성은 GLD, 장기 비용은 GLDM·IAU'},
 {'전략': 'LAA', '역할': '중기국채 고정자산', '우선 추천 ETF': 'IEF', '대체 후보': 'VGIT, SCHR, IEI', '핵심 선택 기준': '침체 방어는 IEF, 변동성 완화는 IEI·VGIT'},
 {'전략': 'LAA', '역할': '공격 타이밍자산', '우선 추천 ETF': 'QQQ 또는 QQQM', '대체 후보': 'VUG, SCHG, IWF', '핵심 선택 기준': '원형은 QQQ, 장기 저비용은 QQQM'},
 {'전략': 'LAA', '역할': '방어 타이밍자산', '우선 추천 ETF': 'SHY 또는 BIL', '대체 후보': 'SGOV, VGSH, USFR', '핵심 선택 기준': '가격 상승 기대는 SHY, 현금 보존은 BIL·SGOV'},
 {'전략': 'VAA', '역할': '미국 공격자산', '우선 추천 ETF': 'SPY', '대체 후보': 'VOO, IVV, VTI', '핵심 선택 기준': '원형은 SPY, 전체시장 확장은 VTI'},
 {'전략': 'VAA', '역할': '선진국 공격자산', '우선 추천 ETF': 'EFA 또는 IEFA', '대체 후보': 'VEA, SCHF, SPDW', '핵심 선택 기준': '원형은 EFA, 소형주 포함은 IEFA'},
 {'전략': 'VAA', '역할': '신흥국 공격자산', '우선 추천 ETF': 'EEM 또는 IEMG', '대체 후보': 'VWO, SPEM, SCHE', '핵심 선택 기준': '원형은 EEM, 광범위·저비용은 IEMG'},
 {'전략': 'VAA', '역할': '종합채권 후보', '우선 추천 ETF': 'AGG', '대체 후보': 'BND, SCHZ, SPAB', '핵심 선택 기준': '원형은 AGG, 저비용 대안은 BND·SCHZ'},
 {'전략': 'VAA', '역할': '방어자산', '우선 추천 ETF': 'LQD / IEF / SHY', '대체 후보': 'BIL, SGOV, VGSH', '핵심 선택 기준': '회사채·중기국채·단기국채의 역할 차이를 유지'},
 {'전략': '듀얼모멘텀', '역할': '공격자산', '우선 추천 ETF': 'SPY / EFA', '대체 후보': 'VOO / IEFA', '핵심 선택 기준': '백테스트 일관성은 SPY·EFA'},
 {'전략': '듀얼모멘텀', '역할': '기준 및 방어', '우선 추천 ETF': 'BIL / AGG', '대체 후보': 'SGOV / IEF / BND', '핵심 선택 기준': '현금 기준은 BIL, 종합 방어는 AGG, 침체 방어는 IEF'}]

ETF_GUIDE_PRACTICAL_ROWS = [{'전략': 'LAA', '구성 유형': '원형 유지', '추천 ETF 구성': 'IWD / GLD / IEF / QQQ / SHY', '특징': '원래 전략과 백테스트 재현성이 가장 높음'},
 {'전략': 'LAA', '구성 유형': '저비용 개선', '추천 ETF 구성': 'VTV / GLDM / IEF / QQQM / SGOV', '특징': '장기 보유 비용과 주당 가격을 낮춤'},
 {'전략': 'LAA', '구성 유형': '현금성 방어 강화', '추천 ETF 구성': 'VTV / GLD / IEF / QQQ / BIL', '특징': '위험회피 구간의 가격 변동 최소화'},
 {'전략': 'LAA', '구성 유형': '금리 하락 방어 유지', '추천 ETF 구성': 'VTV / GLD / IEF / QQQ / SHY', '특징': '단기채 가격 상승 가능성을 일부 유지'},
 {'전략': 'VAA', '구성 유형': '원형 유지', '추천 ETF 구성': 'SPY / EFA / EEM / AGG + LQD / IEF / SHY', '특징': '공격형·방어형 상대모멘텀 구조 유지'},
 {'전략': 'VAA', '구성 유형': '광범위 저비용', '추천 ETF 구성': 'VOO / IEFA / IEMG / BND + VCIT / VGIT / SGOV', '특징': '시장 범위를 넓히고 비용을 낮춤'},
 {'전략': 'VAA', '구성 유형': '보수적 단순형', '추천 ETF 구성': 'SPY / IEFA / IEMG / AGG + BIL', '특징': '방어자산을 BIL로 단순화'},
 {'전략': '듀얼모멘텀', '구성 유형': '원형 재현', '추천 ETF 구성': 'SPY / EFA / BIL / AGG', '특징': '원형과 백테스트 일관성 우선'},
 {'전략': '듀얼모멘텀', '구성 유형': '저비용 광범위', '추천 ETF 구성': 'VOO / IEFA / SGOV / BND', '특징': '장기 비용과 국제 분산 범위 개선'},
 {'전략': '듀얼모멘텀', '구성 유형': '경기침체 방어형', '추천 ETF 구성': 'SPY / IEFA / BIL / IEF', '특징': '위험회피 시 중기국채 가격 상승 기대'},
 {'전략': '듀얼모멘텀', '구성 유형': '금리 상승 방어형', '추천 ETF 구성': 'SPY / IEFA / SGOV / USFR', '특징': '중장기 채권 가격 하락 위험 최소화'}]

ETF_GUIDE_MASTER_ROWS = [{'자산군': '미국 가치주', '핵심 ETF': 'VTV', '선택 대안': 'IWD, IUSV, SCHV', '사용 전략': 'LAA', '선택 포인트': '원형은 IWD, 장기 저비용은 VTV'},
 {'자산군': '금', '핵심 ETF': 'GLD', '선택 대안': 'GLDM, IAU, IAUM', '사용 전략': 'LAA', '선택 포인트': '유동성은 GLD, 비용은 GLDM·IAU'},
 {'자산군': '미국 중기국채', '핵심 ETF': 'IEF', '선택 대안': 'VGIT, SCHR, IEI', '사용 전략': 'LAA·VAA·듀얼', '선택 포인트': '침체 방어는 IEF, 변동성 완화는 IEI'},
 {'자산군': '미국 성장주', '핵심 ETF': 'QQQ', '선택 대안': 'QQQM, VUG, SCHG', '사용 전략': 'LAA', '선택 포인트': '원형·유동성은 QQQ, 장기 비용은 QQQM'},
 {'자산군': '미국 초단기국채', '핵심 ETF': 'BIL', '선택 대안': 'SGOV, USFR, TFLO', '사용 전략': '전 전략', '선택 포인트': '현금성 방어와 절대모멘텀 기준'},
 {'자산군': '미국 대형주', '핵심 ETF': 'SPY', '선택 대안': 'VOO, IVV, VTI', '사용 전략': 'VAA·듀얼·LAA 신호', '선택 포인트': '원형 재현은 SPY'},
 {'자산군': '선진국 주식', '핵심 ETF': 'IEFA', '선택 대안': 'EFA, VEA, SCHF', '사용 전략': 'VAA·듀얼', '선택 포인트': '원형은 EFA, 광범위는 IEFA'},
 {'자산군': '신흥국 주식', '핵심 ETF': 'IEMG', '선택 대안': 'EEM, VWO, SPEM', '사용 전략': 'VAA', '선택 포인트': '원형은 EEM, 광범위는 IEMG'},
 {'자산군': '미국 종합채권', '핵심 ETF': 'AGG', '선택 대안': 'BND, SCHZ, SPAB', '사용 전략': 'VAA·듀얼', '선택 포인트': '분산형 방어자산'},
 {'자산군': '미국 단기국채', '핵심 ETF': 'SHY', '선택 대안': 'VGSH, SCHO, SPTS', '사용 전략': 'LAA·VAA·듀얼', '선택 포인트': '현금보존과 금리하락 수혜의 절충'},
 {'자산군': '투자등급 회사채', '핵심 ETF': 'LQD', '선택 대안': 'VCIT, VCSH, IGSB', '사용 전략': 'VAA', '선택 포인트': '국채보다 높은 이자, 위기 시 신용위험'}]

ETF_GUIDE_CAUTION_NOTES = ['같은 자산군 ETF라도 추종지수가 달라 국가·업종·시가총액 비중이 달라질 수 있습니다.',
 'EFA를 IEFA로 바꾸면 소형주가 추가되고, EEM을 IEMG로 바꾸면 신흥국 소형주까지 포함됩니다.',
 'SHY를 BIL로 바꾸는 것은 단순 티커 교체가 아니라 만기와 듀레이션을 크게 줄이는 전략 변경입니다.',
 'AGG를 IEF로 바꾸면 종합채권 분산이 사라지고 미국 중기국채 금리 민감도가 커집니다.',
 '절대모멘텀 기준 ETF를 BIL에서 SGOV·USFR 등으로 바꾸면 신호 발생 시점이 달라질 수 있습니다.',
 '백테스트 티커와 실제 투자 티커를 다르게 쓰는 경우, 추적오차·분배금·보수·상장기간 차이를 따로 검토해야 합니다.',
 '레버리지·인버스·커버드콜 ETF는 원래 LAA·VAA·듀얼모멘텀의 자산 대체재로 보지 않는 것이 안전합니다.']

ROLE_ALLOWED_GUIDE_CATEGORIES = {
    "laa_fixed_1": ["laa_fixed_value", "laa_fixed_gold", "laa_fixed_treasury"],
    "laa_fixed_2": ["laa_fixed_value", "laa_fixed_gold", "laa_fixed_treasury"],
    "laa_fixed_3": ["laa_fixed_value", "laa_fixed_gold", "laa_fixed_treasury"],
    "laa_variable_risk": ["laa_timing_risk"],
    "laa_variable_defensive": ["laa_timing_defensive"],
    "vaa_attack_1": ["vaa_attack_us", "vaa_attack_developed", "vaa_attack_emerging", "vaa_attack_bond"],
    "vaa_attack_2": ["vaa_attack_us", "vaa_attack_developed", "vaa_attack_emerging", "vaa_attack_bond"],
    "vaa_attack_3": ["vaa_attack_us", "vaa_attack_developed", "vaa_attack_emerging", "vaa_attack_bond"],
    "vaa_attack_4": ["vaa_attack_us", "vaa_attack_developed", "vaa_attack_emerging", "vaa_attack_bond"],
    "vaa_safe_1": ["vaa_safe_corporate", "vaa_safe_treasury", "vaa_safe_short_treasury"],
    "vaa_safe_2": ["vaa_safe_corporate", "vaa_safe_treasury", "vaa_safe_short_treasury"],
    "vaa_safe_3": ["vaa_safe_corporate", "vaa_safe_treasury", "vaa_safe_short_treasury"],
    "odm_us_equity": ["odm_attack_us"],
    "odm_intl_equity": ["odm_attack_intl"],
    "odm_cash": ["odm_cash"],
    "odm_bond": ["odm_safe_aggregate", "odm_safe_treasury"],
}

ROLE_GUIDE_STRATEGY = {
    "laa_fixed_1": "LAA", "laa_fixed_2": "LAA", "laa_fixed_3": "LAA",
    "laa_variable_risk": "LAA", "laa_variable_defensive": "LAA",
    "vaa_attack_1": "VAA", "vaa_attack_2": "VAA", "vaa_attack_3": "VAA", "vaa_attack_4": "VAA",
    "vaa_safe_1": "VAA", "vaa_safe_2": "VAA", "vaa_safe_3": "VAA",
    "odm_us_equity": "듀얼모멘텀", "odm_intl_equity": "듀얼모멘텀",
    "odm_cash": "듀얼모멘텀", "odm_bond": "듀얼모멘텀",
}

# Google Sheets를 읽기 전에도 함수 정의가 가능하도록 기본값으로 초기화합니다.
LAA_FIXED = ["IWD", "GLD", "IEF"]
LAA_VARIABLE_RISK = "QQQ"
LAA_VARIABLE_DEFENSIVE = "SHY"
LAA_VARIABLE = [LAA_VARIABLE_RISK, LAA_VARIABLE_DEFENSIVE]
VAA_ATTACK = ["SPY", "EFA", "EEM", "AGG"]
VAA_SAFE = ["LQD", "IEF", "SHY"]
ODM_ROLE_TICKERS = {
    "us_equity": "SPY", "intl_equity": "EFA", "cash": "BIL", "bond": "AGG",
}
ODM_ASSETS = list(ODM_ROLE_TICKERS.values())
ALL_TICKERS = sorted(set(LAA_FIXED + LAA_VARIABLE + VAA_ATTACK + VAA_SAFE + ODM_ASSETS))
DATA_TICKERS = sorted(set(VAA_ATTACK + VAA_SAFE + ODM_ASSETS))
ETF_LABELS = DEFAULT_ETF_LABELS.copy()

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
    # 기존 rebalance_plan 열 순서를 유지하기 위해 신규 최적화 항목은 마지막에 추가합니다.
    "optimized_value_usd",
    "optimized_value_krw",
    "optimized_weight",
    "weight_gap",
    "estimated_cash_usd",
    "estimated_cash_krw",
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
    # Alpha Vantage TIME_SERIES_MONTHLY_ADJUSTED 원시 데이터 저장 컬럼
    "raw_date",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
    "raw_adjusted_close",
    "raw_volume",
    "raw_dividend",
    "raw_fetched_at",
]

def render_responsive_metrics(items: List[Tuple[str, object]]) -> None:
    """긴 날짜·통화 값을 모바일에서도 생략 없이 보여주는 반응형 지표 카드입니다."""
    cards: List[str] = []
    for label, value in items:
        safe_label = html.escape(str(label))
        safe_value = html.escape("-" if value is None else str(value))
        cards.append(
            '<div class="app-metric-card">'
            f'<div class="app-metric-label">{safe_label}</div>'
            f'<div class="app-metric-value">{safe_value}</div>'
            '</div>'
        )
    st.markdown(
        f'<div class="app-metric-grid">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


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


def _coerce_display_number(x: object) -> float:
    """표시용 값을 안전하게 숫자로 변환합니다.

    Google Sheets에는 숫자뿐 아니라 빈 문자열, '-', 'N/A', 이미 포맷된
    '12.34%' 같은 문자열이 섞일 수 있습니다. 이런 값 때문에 화면 전체가
    중단되지 않도록 변환 불가능한 값은 NaN으로 처리합니다.
    """
    if x is None:
        return float("nan")
    try:
        missing = pd.isna(x)
        if isinstance(missing, bool) and missing:
            return float("nan")
    except Exception:
        pass

    text = str(x).strip()
    if not text or text.upper() in {"-", "N/A", "NA", "NONE", "NAN", "NULL", "#N/A", "#VALUE!", "#ERROR!"}:
        return float("nan")

    is_percent = text.endswith("%")
    text = text.rstrip("%").replace(",", "").strip()
    number = pd.to_numeric(text, errors="coerce")
    if pd.isna(number):
        return float("nan")
    number = float(number)
    return number / 100.0 if is_percent else number


def format_pct(x: object, digits: int = 2) -> str:
    number = _coerce_display_number(x)
    if pd.isna(number):
        return "-"
    return f"{number * 100:.{digits}f}%"


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


@st.cache_resource(show_spinner=False)
def get_cached_worksheet(sheet_name: str, columns_key: Tuple[str, ...]):
    """워크시트 객체와 헤더 확인 결과를 앱 프로세스에서 재사용합니다.

    기존 코드는 load_sheet()를 호출할 때마다 worksheet 조회와 전체 시트
    get_all_values()를 반복해 Google Sheets 읽기 쿼터를 빠르게 소진했습니다.
    워크시트 탐색과 헤더 확인은 시트/컬럼 조합별로 한 번만 수행합니다.
    """
    sh = get_google_spreadsheet()
    columns = list(columns_key)
    try:
        ws = sh.worksheet(sheet_name)
    except Exception as exc:
        # 429, 권한 오류 같은 API 오류를 '시트 없음'으로 오인해 새 시트를
        # 만들지 않도록 WorksheetNotFound인 경우에만 생성합니다.
        worksheet_not_found = getattr(gspread, "WorksheetNotFound", None)
        if worksheet_not_found is None or not isinstance(exc, worksheet_not_found):
            raise
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=max(10, len(columns)))
        ws.update([columns], "A1")
        return ws

    if getattr(ws, "col_count", 0) < len(columns):
        ws.resize(cols=len(columns))

    # 전체 시트를 읽지 않고 헤더 1행만 확인합니다.
    header = ws.row_values(1)
    if not header:
        ws.update([columns], "A1")
    elif header[:len(columns)] != columns:
        ws.update([columns], "A1")
    return ws


def ensure_worksheet(sheet_name: str, columns: List[str]):
    return get_cached_worksheet(sheet_name, tuple(columns))


@st.cache_data(ttl=60, show_spinner=False)
def _load_sheet_cached(sheet_name: str, columns_key: Tuple[str, ...]) -> pd.DataFrame:
    """동일 시트를 60초 동안 재조회하지 않는 읽기 캐시입니다."""
    columns = list(columns_key)
    ws = ensure_worksheet(sheet_name, columns)
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=columns)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def invalidate_sheet_read_cache() -> None:
    """Google Sheets에 쓰기 작업을 한 뒤 읽기 캐시를 무효화합니다."""
    _load_sheet_cached.clear()


def load_sheet(sheet_name: str, columns: List[str]) -> pd.DataFrame:
    # 캐시된 DataFrame이 호출부에서 수정되어도 캐시 원본이 변하지 않도록 복사합니다.
    return _load_sheet_cached(sheet_name, tuple(columns)).copy()


def append_sheet_row(sheet_name: str, columns: List[str], row: Dict[str, object]) -> None:
    ws = ensure_worksheet(sheet_name, columns)
    values = [row.get(col, "") for col in columns]
    ws.append_row(values, value_input_option="USER_ENTERED")
    invalidate_sheet_read_cache()


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
    invalidate_sheet_read_cache()


def overwrite_sheet(sheet_name: str, columns: List[str], df: pd.DataFrame) -> None:
    ws = ensure_worksheet(sheet_name, columns)
    clean = df.copy()
    for col in columns:
        if col not in clean.columns:
            clean[col] = ""
    clean = clean[columns].fillna("")
    values = [columns] + clean.astype(str).values.tolist()
    required_rows = max(1000, len(values) + 10)
    required_cols = max(len(columns), getattr(ws, "col_count", len(columns)))
    if getattr(ws, "row_count", 0) < required_rows or getattr(ws, "col_count", 0) < required_cols:
        ws.resize(rows=required_rows, cols=required_cols)
    ws.clear()
    ws.update(values, "A1")
    invalidate_sheet_read_cache()



def default_strategy_tickers_df() -> pd.DataFrame:
    """앱 최초 실행 시 strategy_tickers 시트에 저장할 기본 전략 구성을 반환합니다."""
    df = pd.DataFrame(DEFAULT_STRATEGY_TICKER_ROWS)
    df["updated_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    return df[STRATEGY_TICKER_COLUMNS]


def _normalize_strategy_ticker_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def etf_asset_guide_df() -> pd.DataFrame:
    """내장 ETF 자산군 가이드를 화면 표시와 자동 매핑용 DataFrame으로 반환합니다."""
    return pd.DataFrame(
        ETF_ASSET_GUIDE_ROWS,
        columns=[
            "section", "category_key", "strategy", "guide_role",
            "auto_role", "asset_class", "ticker", "best_for",
        ],
    )


def lookup_etf_guide_for_role(role_key: str, ticker: str) -> Dict[str, object]:
    """전략 역할과 티커에 가장 적합한 가이드 항목을 찾습니다.

    우선순위:
    1) 현재 설정 역할에서 허용되는 자산군
    2) 같은 전략의 다른 자산군
    3) 세 전략 공통 보완 자산군
    4) 가이드 미등록
    """
    symbol = _normalize_strategy_ticker_symbol(ticker)
    if not symbol:
        return {
            "matched": False,
            "match_level": "미입력",
            "role_name": "",
            "asset_class": "",
            "best_for": "",
            "section": "",
            "warning": "ETF 티커를 입력하세요.",
        }

    guide = etf_asset_guide_df()
    matches = guide[guide["ticker"].astype(str).str.upper() == symbol].copy()
    allowed_categories = ROLE_ALLOWED_GUIDE_CATEGORIES.get(str(role_key), [])
    expected_strategy = ROLE_GUIDE_STRATEGY.get(str(role_key), "")

    exact = matches[matches["category_key"].isin(allowed_categories)]
    if not exact.empty:
        row = exact.iloc[0]
        return {
            "matched": True,
            "match_level": "정상",
            "role_name": str(row["auto_role"]),
            "asset_class": str(row["asset_class"]),
            "best_for": str(row["best_for"]),
            "section": str(row["section"]),
            "warning": "",
        }

    strategy_matches = matches[
        (matches["strategy"].astype(str) == expected_strategy)
        | (matches["strategy"].astype(str) == "LAA·VAA·듀얼")
    ]
    if not strategy_matches.empty:
        row = strategy_matches.iloc[0]
        return {
            "matched": True,
            "match_level": "역할 불일치",
            "role_name": str(row["auto_role"]),
            "asset_class": str(row["asset_class"]),
            "best_for": str(row["best_for"]),
            "section": str(row["section"]),
            "warning": (
                f"{symbol}: 가이드에는 '{row['auto_role']}'로 분류되어 현재 설정 위치와 역할이 다릅니다. "
                "전략 구조가 달라질 수 있으므로 3) ETF 전략별 자산군 가이드를 확인하세요."
            ),
        }

    if not matches.empty:
        row = matches.iloc[0]
        return {
            "matched": True,
            "match_level": "다른 전략",
            "role_name": str(row["auto_role"]),
            "asset_class": str(row["asset_class"]),
            "best_for": str(row["best_for"]),
            "section": str(row["section"]),
            "warning": (
                f"{symbol}: 다른 전략의 가이드 항목으로만 확인됩니다. "
                "현재 전략에서 같은 역할을 수행하는지 별도로 검토하세요."
            ),
        }

    return {
        "matched": False,
        "match_level": "가이드 미등록",
        "role_name": "사용자 지정 ETF-가이드 미등록",
        "asset_class": f"{symbol} 사용자 지정 ETF",
        "best_for": "",
        "section": "",
        "warning": (
            f"{symbol}: 첨부 가이드에 없는 티커입니다. Alpha Vantage 조회 가능 여부와 "
            "현재 전략 역할에 적합한 자산인지 직접 확인하세요."
        ),
    }


def auto_fill_strategy_ticker_metadata(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """티커를 기준으로 역할과 자산군 설명을 가이드에서 자동 입력합니다."""
    source = df.copy() if df is not None else pd.DataFrame()
    source_by_role = source.set_index("role_key", drop=False) if "role_key" in source.columns else pd.DataFrame()
    rows: List[Dict[str, object]] = []
    warnings: List[str] = []

    for default_row in DEFAULT_STRATEGY_TICKER_ROWS:
        role_key = default_row["role_key"]
        current = source_by_role.loc[role_key] if not source_by_role.empty and role_key in source_by_role.index else pd.Series(default_row)
        if isinstance(current, pd.DataFrame):
            current = current.iloc[-1]

        ticker = _normalize_strategy_ticker_symbol(current.get("ticker", default_row["ticker"]))
        guide_match = lookup_etf_guide_for_role(role_key, ticker)

        row = dict(default_row)
        row["ticker"] = ticker
        row["role_name"] = str(guide_match.get("role_name") or default_row["role_name"])
        row["asset_class"] = str(guide_match.get("asset_class") or current.get("asset_class") or default_row["asset_class"]).strip()
        row["updated_at"] = str(current.get("updated_at", "") or "")
        rows.append(row)

        warning = str(guide_match.get("warning", "") or "").strip()
        if warning:
            warnings.append(f"{default_row['strategy_name']} / {role_key}: {warning}")

    normalized = pd.DataFrame(rows, columns=STRATEGY_TICKER_COLUMNS)
    return normalized, warnings


def strategy_ticker_guide_preview(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """티커 편집 직후 실제 저장될 역할·자산군과 가이드 설명을 미리 보여줍니다."""
    normalized, warnings = auto_fill_strategy_ticker_metadata(df)
    preview_rows: List[Dict[str, object]] = []
    for _, row in normalized.iterrows():
        match = lookup_etf_guide_for_role(str(row["role_key"]), str(row["ticker"]))
        preview_rows.append({
            "전략": row["strategy_name"],
            "구분": row["group_name"],
            "설정 ID": row["role_key"],
            "ETF 티커": row["ticker"],
            "자동 역할": row["role_name"],
            "자산군 설명": row["asset_class"],
            "가이드 적합성": match.get("match_level", ""),
            "더 적합한 상황": match.get("best_for", ""),
        })
    return pd.DataFrame(preview_rows), warnings


def validate_strategy_tickers(df: pd.DataFrame) -> List[str]:
    """전략 역할별 티커 설정이 계산 가능한 구조인지 검증합니다."""
    errors: List[str] = []
    required_roles = {row["role_key"] for row in DEFAULT_STRATEGY_TICKER_ROWS}
    actual_roles = set(df.get("role_key", pd.Series(dtype=str)).astype(str).str.strip())

    missing_roles = sorted(required_roles - actual_roles)
    extra_roles = sorted(actual_roles - required_roles)
    if missing_roles:
        errors.append(f"필수 설정 행이 없습니다: {', '.join(missing_roles)}")
    if extra_roles:
        errors.append(f"지원하지 않는 설정 행이 있습니다: {', '.join(extra_roles)}")

    if "role_key" in df.columns and df["role_key"].astype(str).duplicated().any():
        errors.append("role_key가 중복되어 있습니다.")

    ticker_pattern = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
    for _, row in df.iterrows():
        role_name = str(row.get("role_name", row.get("role_key", "설정")))
        ticker = _normalize_strategy_ticker_symbol(row.get("ticker"))
        if not ticker:
            errors.append(f"{role_name}: 티커를 입력하세요.")
        elif not ticker_pattern.fullmatch(ticker):
            errors.append(f"{role_name}: '{ticker}'는 지원하지 않는 티커 형식입니다. 영문, 숫자, 점(.), 하이픈(-)만 사용하세요.")

    # 같은 전략 안에서 동일 티커를 여러 역할에 넣으면 공격/방어 판정이나 비중 계산이 모호해집니다.
    duplicate_groups = {
        "LAA": ["laa_fixed_1", "laa_fixed_2", "laa_fixed_3", "laa_variable_risk", "laa_variable_defensive"],
        "VAA": ["vaa_attack_1", "vaa_attack_2", "vaa_attack_3", "vaa_attack_4", "vaa_safe_1", "vaa_safe_2", "vaa_safe_3"],
        "ODM": ["odm_us_equity", "odm_intl_equity", "odm_cash", "odm_bond"],
    }
    role_to_ticker = {
        str(row.get("role_key")): _normalize_strategy_ticker_symbol(row.get("ticker"))
        for _, row in df.iterrows()
    }
    for strategy_name, roles in duplicate_groups.items():
        tickers = [role_to_ticker.get(role, "") for role in roles]
        non_blank = [ticker for ticker in tickers if ticker]
        duplicates = sorted({ticker for ticker in non_blank if non_blank.count(ticker) > 1})
        if duplicates:
            errors.append(f"{strategy_name} 전략 내부에서 같은 티커를 중복 사용할 수 없습니다: {', '.join(duplicates)}")
    return errors


def normalize_strategy_ticker_editor(edited_df: pd.DataFrame, current_df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """화면에서 수정한 티커를 가이드 기준 역할·자산군 설명과 함께 저장 형태로 정리합니다."""
    edited = edited_df.copy() if edited_df is not None else pd.DataFrame()
    current = current_df.copy() if current_df is not None else pd.DataFrame()
    edited_by_role = edited.set_index("role_key", drop=False) if "role_key" in edited.columns else pd.DataFrame()
    current_by_role = current.set_index("role_key", drop=False) if "role_key" in current.columns else pd.DataFrame()

    rows: List[Dict[str, object]] = []
    updated_at = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    for default_row in DEFAULT_STRATEGY_TICKER_ROWS:
        role_key = default_row["role_key"]
        edited_row = edited_by_role.loc[role_key] if not edited_by_role.empty and role_key in edited_by_role.index else pd.Series(dtype=object)
        current_row = current_by_role.loc[role_key] if not current_by_role.empty and role_key in current_by_role.index else pd.Series(default_row)
        if isinstance(edited_row, pd.DataFrame):
            edited_row = edited_row.iloc[-1]
        if isinstance(current_row, pd.DataFrame):
            current_row = current_row.iloc[-1]

        old_ticker = _normalize_strategy_ticker_symbol(current_row.get("ticker", default_row["ticker"]))
        new_ticker = _normalize_strategy_ticker_symbol(edited_row.get("ticker", old_ticker))

        row = dict(default_row)
        row["ticker"] = new_ticker
        row["updated_at"] = updated_at
        rows.append(row)

    normalized, _ = auto_fill_strategy_ticker_metadata(pd.DataFrame(rows, columns=STRATEGY_TICKER_COLUMNS))
    normalized["updated_at"] = updated_at
    errors = validate_strategy_tickers(normalized)
    return normalized, errors


def load_strategy_tickers() -> pd.DataFrame:
    """strategy_tickers 시트를 읽고, 최초 실행이면 기본 전략 티커를 자동 생성합니다."""
    df = load_sheet("strategy_tickers", STRATEGY_TICKER_COLUMNS)
    if df.empty:
        df = default_strategy_tickers_df()
        overwrite_sheet("strategy_tickers", STRATEGY_TICKER_COLUMNS, df)
        return df

    for col in STRATEGY_TICKER_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["role_key"] = df["role_key"].astype(str).str.strip()
    df["ticker"] = df["ticker"].apply(_normalize_strategy_ticker_symbol)
    df["sort_order"] = pd.to_numeric(df["sort_order"], errors="coerce").fillna(0).astype(int)
    df["asset_class"] = df["asset_class"].astype(str).str.strip()
    df = df.sort_values(["strategy_key", "sort_order", "role_key"]).reset_index(drop=True)

    # 기존 strategy_tickers 시트가 예전의 일반 역할명/자산군 설명을 가지고 있어도
    # 현재 티커를 첨부 가이드에 다시 매칭해 앱 화면과 계산 결과에 즉시 반영합니다.
    df, _ = auto_fill_strategy_ticker_metadata(df)

    errors = validate_strategy_tickers(df)
    if errors:
        raise RuntimeError("strategy_tickers 시트 설정 오류: " + " / ".join(errors))
    return df[STRATEGY_TICKER_COLUMNS]


def save_strategy_tickers_to_sheet(df: pd.DataFrame) -> None:
    normalized, _ = auto_fill_strategy_ticker_metadata(df)
    normalized["updated_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    errors = validate_strategy_tickers(normalized)
    if errors:
        raise ValueError(" / ".join(errors))
    overwrite_sheet("strategy_tickers", STRATEGY_TICKER_COLUMNS, normalized)


def apply_strategy_ticker_config(df: pd.DataFrame) -> None:
    """Google Sheets 설정을 앱의 모든 전략 계산용 전역 구성에 반영합니다."""
    global LAA_FIXED, LAA_VARIABLE_RISK, LAA_VARIABLE_DEFENSIVE, LAA_VARIABLE
    global VAA_ATTACK, VAA_SAFE, ODM_ROLE_TICKERS, ODM_ASSETS
    global ALL_TICKERS, DATA_TICKERS, ETF_LABELS

    role_map = {
        str(row["role_key"]): _normalize_strategy_ticker_symbol(row["ticker"])
        for _, row in df.iterrows()
    }
    LAA_FIXED = [role_map[f"laa_fixed_{i}"] for i in range(1, 4)]
    LAA_VARIABLE_RISK = role_map["laa_variable_risk"]
    LAA_VARIABLE_DEFENSIVE = role_map["laa_variable_defensive"]
    LAA_VARIABLE = [LAA_VARIABLE_RISK, LAA_VARIABLE_DEFENSIVE]
    VAA_ATTACK = [role_map[f"vaa_attack_{i}"] for i in range(1, 5)]
    VAA_SAFE = [role_map[f"vaa_safe_{i}"] for i in range(1, 4)]
    ODM_ROLE_TICKERS = {
        "us_equity": role_map["odm_us_equity"],
        "intl_equity": role_map["odm_intl_equity"],
        "cash": role_map["odm_cash"],
        "bond": role_map["odm_bond"],
    }
    ODM_ASSETS = list(ODM_ROLE_TICKERS.values())
    ALL_TICKERS = sorted(set(LAA_FIXED + LAA_VARIABLE + VAA_ATTACK + VAA_SAFE + ODM_ASSETS))
    DATA_TICKERS = sorted(set(VAA_ATTACK + VAA_SAFE + ODM_ASSETS))

    labels = DEFAULT_ETF_LABELS.copy()
    for _, row in df.iterrows():
        ticker = _normalize_strategy_ticker_symbol(row.get("ticker"))
        asset_class = str(row.get("asset_class", "") or "").strip()
        if ticker:
            labels[ticker] = asset_class or f"{ticker} 사용자 지정 ETF"
    ETF_LABELS = labels


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
        rate_date = pd.Timestamp(now_kst().date())
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
    saved_at = now_kst().strftime("%Y-%m-%d %H:%M:%S")
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
            "optimized_value_usd": _safe_float(row.get("리밸런싱 후 평가액(USD)"), default=float("nan")),
            "optimized_value_krw": _safe_float(row.get("리밸런싱 후 평가액(KRW)"), default=float("nan")),
            "optimized_weight": _safe_float(row.get("리밸런싱 후 비중"), default=float("nan")),
            "weight_gap": _safe_float(row.get("목표 대비 비중차"), default=float("nan")),
            "estimated_cash_usd": _safe_float(row.get("예상 잔여 현금(USD)"), default=float("nan")),
            "estimated_cash_krw": _safe_float(row.get("예상 잔여 현금(KRW)"), default=float("nan")),
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
        "optimized_value_usd",
        "optimized_value_krw",
        "optimized_weight",
        "weight_gap",
        "estimated_cash_usd",
        "estimated_cash_krw",
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
        "optimized_value_usd": "리밸런싱 후 평가액(USD)",
        "optimized_value_krw": "리밸런싱 후 평가액(KRW)",
        "optimized_weight": "리밸런싱 후 비중",
        "weight_gap": "목표 대비 비중차",
        "estimated_cash_usd": "예상 잔여 현금(USD)",
        "estimated_cash_krw": "예상 잔여 현금(KRW)",
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
        "목표 주수", "리밸런싱 후 평가액(USD)", "리밸런싱 후 평가액(KRW)", "리밸런싱 후 비중", "목표 대비 비중차",
        "예상 잔여 현금(USD)", "예상 잔여 현금(KRW)", "현재 주수", "매매 구분", "매매 필요 주수", "매매 필요 금액(USD)", "매매 필요 금액(KRW)",
        "다음 리밸런싱일", "리밸런싱 상태",
    ]
    show = show[[c for c in preferred_cols if c in show.columns]]
    for col in ["목표비중", "현재비중", "리밸런싱 후 비중", "목표 대비 비중차"]:
        if col in show.columns:
            show[col] = show[col].apply(format_pct)
    for col in ["목표금액(USD)", "현재평가액(USD)", "리밸런싱 후 평가액(USD)", "예상 잔여 현금(USD)", "매매 필요 금액(USD)"]:
        if col in show.columns:
            show[col] = show[col].apply(money_usd)
    for col in ["목표금액(KRW)", "현재평가액(KRW)", "리밸런싱 후 평가액(KRW)", "예상 잔여 현금(KRW)", "매매 필요 금액(KRW)"]:
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
        return {"saved_at": "-", "total_usd": 0.0, "total_krw": 0.0, "buy_usd": 0.0, "sell_usd": 0.0, "estimated_cash_usd": 0.0}
    buy_usd = df.loc[df["trade_action"] == "BUY", "trade_amount_usd"].dropna().sum() if "trade_action" in df.columns else 0.0
    sell_usd = df.loc[df["trade_action"] == "SELL", "trade_amount_usd"].dropna().sum() if "trade_action" in df.columns else 0.0
    first = df.iloc[0]
    estimated_cash_usd = _safe_float(first.get("estimated_cash_usd")) if "estimated_cash_usd" in df.columns else 0.0
    return {
        "saved_at": _safe_str(first.get("saved_at")),
        "total_usd": _safe_float(first.get("total_investment_usd")),
        "total_krw": _safe_float(first.get("total_investment_krw")),
        "buy_usd": float(buy_usd),
        "sell_usd": float(sell_usd),
        "estimated_cash_usd": estimated_cash_usd,
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
    raw_date: object = "",
    raw_open: object = "",
    raw_high: object = "",
    raw_low: object = "",
    raw_close: object = "",
    raw_adjusted_close: object = "",
    raw_volume: object = "",
    raw_dividend: object = "",
    raw_fetched_at: object = "",
) -> Dict[str, object]:
    def raw_number(value: object) -> object:
        if value == "" or value is None:
            return ""
        return _safe_float(value, default=float("nan"))

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
        "raw_date": _safe_str(raw_date),
        "raw_open": raw_number(raw_open),
        "raw_high": raw_number(raw_high),
        "raw_low": raw_number(raw_low),
        "raw_close": raw_number(raw_close),
        "raw_adjusted_close": raw_number(raw_adjusted_close),
        "raw_volume": raw_number(raw_volume),
        "raw_dividend": raw_number(raw_dividend),
        "raw_fetched_at": _safe_str(raw_fetched_at),
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
    monthly_data: Dict[str, pd.DataFrame],
    data_source: str,
) -> pd.DataFrame:
    """리밸런싱 주문안이 나온 계산 근거를 행 단위로 정리합니다.

    rebalance_basis 시트는 최종 주문안(rebalance_plan)을 해석하기 위한 근거 로그입니다.
    새로 전략 계산 버튼을 누르면 최신 근거로 덮어씁니다.
    """
    saved_at = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    rows: List[Dict[str, object]] = []

    # 공통 계산 설정
    setting_items = [
        ("조회기간", f"{lookback_months}개월", lookback_months, "VAA/ODM 수익률 계산에 사용한 월봉 개수"),
        ("진행 중인 월 데이터 제외", "Y" if exclude_current_month else "N", "", "월말 전략 계산 기준 옵션"),
        ("ETF 데이터 사용 방식", data_source, "", f"현재 포트폴리오 기준은 현재 설정된 {len(ALL_TICKERS)}개 고유 ETF API 신규 조회, 수동 투자금 기준은 rebalance_basis 저장 RAW 재사용"),
        ("Alpha Vantage 호출 수", f"{len(ALL_TICKERS)}회" if data_source.startswith("API") else "0회", len(ALL_TICKERS) if data_source.startswith("API") else 0, f"TIME_SERIES_MONTHLY_ADJUSTED를 현재 설정된 {len(ALL_TICKERS)}개 고유 ETF에 각각 1회 호출"),
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
            decision_reason=f"{'/'.join(LAA_FIXED)}는 LAA 고정자산으로 각각 25% 배분",
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
                decision_reason=odm_reason, source_note=f"{ODM_ROLE_TICKERS['us_equity']}와 {ODM_ROLE_TICKERS['cash']} 비교 후 {ODM_ROLE_TICKERS['us_equity']}/{ODM_ROLE_TICKERS['intl_equity']} 또는 {ODM_ROLE_TICKERS['bond']} 선택",
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
                source_note="목표금액 기준 정수 주수를 먼저 계산한 뒤, 남은 현금으로 추가 매수 가능한 ETF 중 전체 목표비중 오차가 가장 작은 종목을 1주씩 배정",
            ))
        cash_values = pd.to_numeric(plan.get("예상 잔여 현금(USD)"), errors="coerce").dropna() if "예상 잔여 현금(USD)" in plan.columns else pd.Series(dtype=float)
        if not cash_values.empty:
            estimated_cash_usd = float(cash_values.iloc[0])
            rows.append(_basis_row(
                saved_at, metadata, "주문안 산식 근거", "최종", item="정수 주수 최적화 후 예상 잔여 현금",
                value=money_usd(estimated_cash_usd), value_numeric=estimated_cash_usd,
                decision_reason="더 이상 남은 현금으로 전략 대상 ETF를 1주도 추가 매수할 수 없는 상태",
                source_note="일부 종목의 최종 비중은 목표비중을 소폭 초과할 수 있으며, 추가 배정 시 전체 목표비중 제곱오차가 가장 작은 ETF를 선택",
            ))

    # Alpha Vantage 월봉 RAW 저장
    # 전체 응답 중 최근 RAW_MONTHS_TO_SAVE개를 저장해, 다음 API 호출 전까지
    # 현재 총자산 평가와 수동 투자금 전략 계산에 재사용합니다.
    if monthly_data:
        for ticker in sorted(monthly_data.keys()):
            raw_df = monthly_data.get(ticker)
            if raw_df is None or raw_df.empty:
                continue
            raw_df = raw_df.sort_index().tail(RAW_MONTHS_TO_SAVE)
            for raw_dt, raw in raw_df.iterrows():
                rows.append(_basis_row(
                    saved_at, metadata, "ETF 월봉 RAW", "Alpha Vantage", ticker, ETF_LABELS.get(ticker, ""),
                    item="TIME_SERIES_MONTHLY_ADJUSTED", value=_safe_str(raw_dt),
                    decision_reason="전략 수익률 및 최신 평가가격 계산용 원시 데이터",
                    source_note=data_source,
                    raw_date=raw_dt, raw_open=raw.get("open", ""), raw_high=raw.get("high", ""),
                    raw_low=raw.get("low", ""), raw_close=raw.get("close", ""),
                    raw_adjusted_close=raw.get("adjusted_close", ""), raw_volume=raw.get("volume", ""),
                    raw_dividend=raw.get("dividend", ""),
                    raw_fetched_at=raw_df.attrs.get("fetched_at", saved_at),
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
    numeric_cols = ["value_numeric", "return_1m", "return_3m", "return_6m", "return_12m", "momentum_score", "rank", "raw_open", "raw_high", "raw_low", "raw_close", "raw_adjusted_close", "raw_volume", "raw_dividend"]
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
        "raw_date": "RAW 기준일", "raw_open": "RAW 시가", "raw_high": "RAW 고가",
        "raw_low": "RAW 저가", "raw_close": "RAW 종가", "raw_adjusted_close": "RAW 조정종가",
        "raw_volume": "RAW 거래량", "raw_dividend": "RAW 배당", "raw_fetched_at": "RAW 원 조회시각",
    })
    for col in ["1개월 수익률", "3개월 수익률", "6개월 수익률", "12개월 수익률"]:
        if col in show.columns:
            show[col] = show[col].apply(
                lambda x: "" if pd.isna(_coerce_display_number(x)) else format_pct(x)
            )
    if "모멘텀 스코어" in show.columns:
        show["모멘텀 스코어"] = show["모멘텀 스코어"].apply(
            lambda x: "" if pd.isna(_coerce_display_number(x)) else format_score(_coerce_display_number(x))
        )
    if "수치값" in show.columns:
        show["수치값"] = show["수치값"].apply(
            lambda x: "" if pd.isna(_coerce_display_number(x)) else f"{_coerce_display_number(x):,.4f}"
        )
    if "순위" in show.columns:
        show["순위"] = show["순위"].apply(
            lambda x: "" if pd.isna(_coerce_display_number(x)) else f"{_coerce_display_number(x):.0f}"
        )
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
    df.attrs["fetched_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    df.attrs["data_source"] = "Alpha Vantage TIME_SERIES_MONTHLY_ADJUSTED"
    return df


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
    if "fetched_at" not in new_quotes.columns:
        new_quotes["fetched_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    else:
        new_quotes["fetched_at"] = new_quotes["fetched_at"].fillna(now_kst().strftime("%Y-%m-%d %H:%M:%S")).astype(str)

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
            price_date = now_kst().strftime("%Y-%m-%d")

        rows.append({
            "ticker": ticker,
            "latest_price_usd": float(price),
            "price_date": price_date,
            "source": "MANUAL",
        })

    if not rows and not errors:
        errors.append("저장할 수동 최신가가 없습니다. 가격을 입력하세요.")

    return pd.DataFrame(rows, columns=["ticker", "latest_price_usd", "price_date", "source"]), errors


def load_all_monthly_prices(tickers: List[str], api_key: str) -> Dict[str, pd.DataFrame]:
    """각 ETF에 TIME_SERIES_MONTHLY_ADJUSTED를 정확히 1회씩 호출합니다.

    한 종목의 응답에는 월별 수익률 계산용 과거 월봉과 현재 평가에 사용할
    가장 최근 실제 종가가 함께 들어 있으므로 GLOBAL_QUOTE를 추가 호출하지 않습니다.
    다음 종목 요청 전에는 항상 API_CALL_DELAY_SECONDS(1.25초)를 대기합니다.
    """
    result: Dict[str, pd.DataFrame] = {}
    errors: List[str] = []
    requested = sorted(set([str(t).upper().strip() for t in tickers if str(t).strip()]))
    progress = st.progress(0, text="Alpha Vantage에서 ETF 월봉/최근 실제 종가를 불러오는 중입니다.")
    for i, ticker in enumerate(requested, start=1):
        try:
            result[ticker] = fetch_monthly_adjusted(ticker, api_key)
        except Exception as e:
            errors.append(str(e))
        progress.progress(i / len(requested), text=f"ETF 통합 데이터 로딩: {ticker} ({i}/{len(requested)})")
        # 1 request/second 제한 준수: 다음 API 요청 전에 1.25초 대기
        if i < len(requested):
            time.sleep(API_CALL_DELAY_SECONDS)
    progress.empty()
    if errors:
        with st.expander("ETF 통합 데이터 로딩 오류 보기", expanded=True):
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


def monthly_data_to_quotes(data: Dict[str, pd.DataFrame], eval_date: date, source: str) -> pd.DataFrame:
    """월봉 API 응답의 가장 최근 실제 종가를 별도 API 호출 없이 평가가격으로 변환합니다.

    전략 수익률은 adjusted_close를 사용하지만, 현재 평가액과 목표 주수는 실제 close를
    우선 사용합니다. close가 없을 때만 adjusted_close로 대체합니다.
    """
    rows: List[Dict[str, object]] = []
    eval_ts = pd.Timestamp(eval_date)
    for ticker in sorted(data.keys()):
        df = data.get(ticker)
        if df is None or df.empty:
            continue
        price_col = "close" if "close" in df.columns else "adjusted_close"
        if price_col not in df.columns:
            continue
        usable = df.loc[df.index <= eval_ts].dropna(subset=[price_col]).sort_index()
        if usable.empty:
            continue
        price_dt = usable.index.max()
        price = pd.to_numeric(usable.loc[price_dt, price_col], errors="coerce")
        if (pd.isna(price) or float(price) <= 0) and "adjusted_close" in usable.columns:
            price = pd.to_numeric(usable.loc[price_dt, "adjusted_close"], errors="coerce")
        if pd.isna(price) or float(price) <= 0:
            continue
        fetched_at = df.attrs.get("fetched_at", now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        rows.append({
            "ticker": ticker,
            "latest_price_usd": float(price),
            "price_date": price_dt.strftime("%Y-%m-%d"),
            "source": source,
            "fetched_at": fetched_at,
        })
    return pd.DataFrame(rows, columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])


def monthly_data_from_saved_basis(basis_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """rebalance_basis의 ETF 월봉 RAW 행을 전략 계산용 DataFrame 사전으로 복원합니다."""
    if basis_df is None or basis_df.empty or "section" not in basis_df.columns:
        return {}
    raw = basis_df[basis_df["section"].astype(str) == "ETF 월봉 RAW"].copy()
    if raw.empty:
        return {}
    raw["ticker"] = raw["ticker"].astype(str).str.upper().str.strip()
    raw["raw_date"] = pd.to_datetime(raw["raw_date"], errors="coerce")
    raw = raw.dropna(subset=["ticker", "raw_date"])
    mapping = {
        "raw_open": "open", "raw_high": "high", "raw_low": "low", "raw_close": "close",
        "raw_adjusted_close": "adjusted_close", "raw_volume": "volume", "raw_dividend": "dividend",
    }
    result: Dict[str, pd.DataFrame] = {}
    for ticker, group in raw.groupby("ticker"):
        frame = group[["raw_date"] + list(mapping.keys())].copy().rename(columns=mapping)
        frame = frame.set_index("raw_date").sort_index()
        for col in mapping.values():
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame["symbol"] = ticker
        raw_fetch_values = [str(v) for v in group.get("raw_fetched_at", pd.Series(dtype=str)).dropna().tolist() if str(v).strip()]
        frame.attrs["fetched_at"] = max(raw_fetch_values) if raw_fetch_values else _safe_str(group.iloc[-1].get("saved_at"))
        frame.attrs["data_source"] = "Google Sheets rebalance_basis 저장 RAW"
        result[ticker] = frame
    return result


def seed_session_quotes_from_saved_basis(saved_basis_df: pd.DataFrame, eval_date: date) -> None:
    """새 앱 세션에서만 rebalance_basis의 최근 실제 종가를 세션 최신가로 복원합니다."""
    if st.session_state.get("basis_quotes_seeded", False):
        return
    saved_data = monthly_data_from_saved_basis(saved_basis_df)
    saved_quotes = monthly_data_to_quotes(saved_data, eval_date, "REBALANCE_BASIS_RAW")
    if not saved_quotes.empty:
        store_latest_quotes(saved_quotes)
    st.session_state["basis_quotes_seeded"] = True


def add_non_strategy_session_quotes(base_quotes: pd.DataFrame, holding_tickers: List[str]) -> pd.DataFrame:
    """전략 외 보유종목은 사용자가 세션에 수동 저장한 가격만 추가하며 API는 호출하지 않습니다."""
    extras = sorted(set(str(t).upper().strip() for t in holding_tickers if str(t).strip()) - set(ALL_TICKERS))
    extra_quotes = get_cached_quotes_for_tickers(extras)
    frames = [df for df in [base_quotes, extra_quotes] if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
    combined = pd.concat(frames, ignore_index=True)
    combined["ticker"] = combined["ticker"].astype(str).str.upper().str.strip()
    return combined.drop_duplicates(subset=["ticker"], keep="last")

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
        threshold_text = f"공격형 {len(VAA_ATTACK)}개 ETF의 모멘텀 스코어가 모두 0 초과"
    else:
        attack_ok = bool((attack["모멘텀 스코어"] >= 0).all())
        threshold_text = f"공격형 {len(VAA_ATTACK)}개 ETF의 모멘텀 스코어가 모두 0 이상"
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
    us_ticker = ODM_ROLE_TICKERS["us_equity"]
    intl_ticker = ODM_ROLE_TICKERS["intl_equity"]
    cash_ticker = ODM_ROLE_TICKERS["cash"]
    bond_ticker = ODM_ROLE_TICKERS["bond"]
    us_return = float(r[us_ticker])
    intl_return = float(r[intl_ticker])
    cash_return = float(r[cash_ticker])
    if us_return > cash_return:
        selected = us_ticker if us_return >= intl_return else intl_ticker
        reason = f"{us_ticker} 12개월 수익률이 {cash_ticker}보다 높아 {us_ticker}/{intl_ticker} 중 더 강한 ETF 선택"
    else:
        selected = bond_ticker
        reason = f"{us_ticker} 12개월 수익률이 {cash_ticker}보다 낮거나 같아 {bond_ticker} 선택"
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


def optimize_target_shares_min_cash(plan: pd.DataFrame) -> Tuple[Dict[int, int], float, float]:
    """정수 주수 제약에서 잔여 현금을 줄이면서 목표비중에 가깝게 배분합니다.

    1) 각 ETF 목표금액을 가격으로 나눈 값을 우선 내림합니다.
    2) 남은 현금으로 1주 이상 살 수 있는 동안, 추가 1주 매수 후의
       전체 목표비중 제곱오차가 가장 작은 ETF를 선택합니다.
    3) 오차가 같으면 매수 후 남는 현금이 더 적은 ETF를 선택합니다.

    목표비중이 0인 전략 외 보유종목과 가격이 없는 종목은 추가 배정에서 제외합니다.
    가격이 없는 목표종목의 목표금액은 다른 종목에 임의 재배분하지 않고 현금으로 남깁니다.
    """
    if plan is None or plan.empty:
        return {}, 0.0, 0.0

    work = plan.copy()
    work["전략 전체 비중"] = pd.to_numeric(work.get("전략 전체 비중"), errors="coerce").fillna(0.0)
    work["목표 투자금(USD)"] = pd.to_numeric(work.get("목표 투자금(USD)"), errors="coerce").fillna(0.0)
    work["latest_price_usd"] = pd.to_numeric(work.get("latest_price_usd"), errors="coerce")

    target_mask = (work["전략 전체 비중"] > 0) & (work["목표 투자금(USD)"] > 0)
    total_budget = float(work.loc[target_mask, "목표 투자금(USD)"].sum())
    valid_mask = target_mask & work["latest_price_usd"].notna() & (work["latest_price_usd"] > 0)
    valid_indices = work.index[valid_mask].tolist()

    shares: Dict[int, int] = {}
    for idx in valid_indices:
        target_usd = float(work.at[idx, "목표 투자금(USD)"])
        price = float(work.at[idx, "latest_price_usd"])
        shares[idx] = max(0, math.floor(target_usd / price))

    invested = sum(shares[idx] * float(work.at[idx, "latest_price_usd"]) for idx in valid_indices)
    # 가격이 확인된 종목에 배정된 예산 안에서만 추가 주수를 배분합니다.
    valid_budget = float(work.loc[valid_mask, "목표 투자금(USD)"].sum())
    allocatable_cash = max(0.0, valid_budget - invested)
    tolerance = 1e-9

    def tracking_error(candidate_shares: Dict[int, int]) -> float:
        if total_budget <= 0:
            return 0.0
        error = 0.0
        for idx in valid_indices:
            price = float(work.at[idx, "latest_price_usd"])
            actual_weight = candidate_shares[idx] * price / total_budget
            target_weight = float(work.at[idx, "전략 전체 비중"])
            error += (actual_weight - target_weight) ** 2
        return error

    while True:
        affordable = [idx for idx in valid_indices if float(work.at[idx, "latest_price_usd"]) <= allocatable_cash + tolerance]
        if not affordable:
            break

        candidates = []
        for idx in affordable:
            candidate = dict(shares)
            candidate[idx] += 1
            price = float(work.at[idx, "latest_price_usd"])
            cash_after = max(0.0, allocatable_cash - price)
            candidates.append((tracking_error(candidate), cash_after, str(work.at[idx, "ticker"]), idx))

        _, _, _, selected_idx = min(candidates)
        selected_price = float(work.at[selected_idx, "latest_price_usd"])
        shares[selected_idx] += 1
        allocatable_cash = max(0.0, allocatable_cash - selected_price)

    final_invested = sum(shares[idx] * float(work.at[idx, "latest_price_usd"]) for idx in valid_indices)
    estimated_cash = max(0.0, total_budget - final_invested)
    return shares, total_budget, estimated_cash


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

    optimized_shares, total_budget, estimated_cash_usd = optimize_target_shares_min_cash(plan)

    target_shares, optimized_values, optimized_weights, weight_gaps = [], [], [], []
    trade_shares, trade_action, trade_usd, trade_krw = [], [], [], []
    for idx, row in plan.iterrows():
        target_weight = _safe_float(row.get("전략 전체 비중"), default=0.0)
        target_usd = pd.to_numeric(row.get("목표 투자금(USD)"), errors="coerce")
        price = pd.to_numeric(row.get("latest_price_usd"), errors="coerce")
        cur_qty = float(row.get("quantity", 0.0) or 0.0)

        if pd.isna(target_usd) or pd.isna(price) or float(price) <= 0:
            target_shares.append(pd.NA)
            optimized_values.append(pd.NA)
            optimized_weights.append(pd.NA)
            weight_gaps.append(pd.NA)
            trade_shares.append(pd.NA)
            trade_action.append("가격 확인 필요")
            trade_usd.append(pd.NA)
            trade_krw.append(pd.NA)
            continue

        # 전략 외 보유종목은 목표 0주, 전략 대상은 최적화된 정수 주수를 사용합니다.
        ts = optimized_shares.get(idx, 0) if target_weight > 0 else 0
        optimized_value = ts * float(price)
        optimized_weight = optimized_value / total_budget if total_budget > 0 else pd.NA
        weight_gap = optimized_weight - target_weight if pd.notna(optimized_weight) else pd.NA
        delta = ts - cur_qty

        if abs(delta) < 1e-9:
            action = "HOLD"
        elif delta > 0:
            action = "BUY"
        else:
            action = "SELL"

        amount_usd = abs(delta) * float(price)
        target_shares.append(ts)
        optimized_values.append(optimized_value)
        optimized_weights.append(optimized_weight)
        weight_gaps.append(weight_gap)
        trade_shares.append(delta)
        trade_action.append(action)
        trade_usd.append(amount_usd)
        trade_krw.append(amount_usd * usdkrw_rate)

    plan["목표 주수"] = target_shares
    plan["리밸런싱 후 평가액(USD)"] = optimized_values
    plan["리밸런싱 후 평가액(KRW)"] = pd.to_numeric(plan["리밸런싱 후 평가액(USD)"], errors="coerce") * usdkrw_rate
    plan["리밸런싱 후 비중"] = optimized_weights
    plan["목표 대비 비중차"] = weight_gaps
    plan["예상 잔여 현금(USD)"] = estimated_cash_usd
    plan["예상 잔여 현금(KRW)"] = estimated_cash_usd * usdkrw_rate
    plan["현재 주수"] = plan["quantity"]
    plan["매매 구분"] = trade_action
    plan["매매 필요 주수"] = trade_shares
    plan["매매 필요 금액(USD)"] = trade_usd
    plan["매매 필요 금액(KRW)"] = trade_krw
    return plan.sort_values(["매매 구분", "전략 전체 비중"], ascending=[True, False])


def appendix_buy_strategy_table() -> pd.DataFrame:
    fixed_text = ", ".join(LAA_FIXED)
    variable_text = f"조건 미충족: {LAA_VARIABLE_RISK} / 조건 충족: {LAA_VARIABLE_DEFENSIVE}"
    attack_text = ", ".join(VAA_ATTACK)
    safe_text = ", ".join(VAA_SAFE)
    us_ticker = ODM_ROLE_TICKERS["us_equity"]
    intl_ticker = ODM_ROLE_TICKERS["intl_equity"]
    cash_ticker = ODM_ROLE_TICKERS["cash"]
    bond_ticker = ODM_ROLE_TICKERS["bond"]
    return pd.DataFrame([
        {"전략": "LAA", "대상 ETF": f"고정: {fixed_text} / 변동: {variable_text}", "매수 전략": f"{fixed_text}에 각각 25% 고정. 나머지 25%는 두 방어 조건이 동시에 O이면 {LAA_VARIABLE_DEFENSIVE}, 아니면 {LAA_VARIABLE_RISK}.", "리밸런싱": f"{fixed_text} 연 1회, {LAA_VARIABLE_RISK}/{LAA_VARIABLE_DEFENSIVE} 월 1회"},
        {"전략": "VAA 공격형", "대상 ETF": f"공격형: {attack_text} / 안전자산: {safe_text}", "매수 전략": f"공격형 {len(VAA_ATTACK)}개 ETF의 모멘텀 스코어가 모두 양호하면 공격형 1위 100%, 하나라도 방어 신호면 안전자산 1위 100%.", "리밸런싱": "월 1회"},
        {"전략": "오리지널 듀얼 모멘텀", "대상 ETF": f"미국주식: {us_ticker}, 해외주식: {intl_ticker}, 현금성 기준: {cash_ticker}, 방어채권: {bond_ticker}", "매수 전략": f"{us_ticker} 12개월 수익률이 {cash_ticker}보다 높으면 {us_ticker}/{intl_ticker} 중 높은 ETF 100%, 낮거나 같으면 {bond_ticker} 100%.", "리밸런싱": "월 1회"},
    ])


def appendix_strategy_calculation_table() -> pd.DataFrame:
    fixed_text = "/".join(LAA_FIXED)
    vaa_all_text = "/".join(VAA_ATTACK + VAA_SAFE)
    odm_text = "/".join(ODM_ASSETS)
    us_ticker = ODM_ROLE_TICKERS["us_equity"]
    intl_ticker = ODM_ROLE_TICKERS["intl_equity"]
    cash_ticker = ODM_ROLE_TICKERS["cash"]
    bond_ticker = ODM_ROLE_TICKERS["bond"]
    return pd.DataFrame([
        {
            "구분": "LAA 고정 75%",
            "계산 방식": f"LAA 배정금액의 25%씩 {fixed_text}에 배분",
            "판정 기준": "별도 모멘텀 계산 없음",
            "앱 반영": "하위전략 비중 × 25%로 목표금액 계산",
        },
        {
            "구분": "LAA 변동 25%",
            "계산 방식": "S&P500 200일선 하회와 미국 실업률 12개월 평균 상회를 사용자가 O/X로 입력",
            "판정 기준": f"두 조건이 모두 O이면 {LAA_VARIABLE_DEFENSIVE}, 그 외에는 {LAA_VARIABLE_RISK}",
            "앱 반영": "선택 ETF에 LAA 배정금액의 25% 배분",
        },
        {
            "구분": "VAA 모멘텀 스코어",
            "계산 방식": "12×1개월 수익률 + 4×3개월 수익률 + 2×6개월 수익률 + 1×12개월 수익률",
            "판정 기준": f"공격형 {'/'.join(VAA_ATTACK)} {len(VAA_ATTACK)}개가 모두 기준 이상이면 공격형 1위, 하나라도 미달이면 안전자산 {'/'.join(VAA_SAFE)} 중 1위",
            "앱 반영": f"현재 설정된 고유 ETF {len(ALL_TICKERS)}개의 TIME_SERIES_MONTHLY_ADJUSTED를 각 1회 조회하고, 같은 응답을 수익률·최근 평가가격에 함께 사용",
        },
        {
            "구분": "VAA 0점 처리",
            "계산 방식": "사이드바 옵션이 아니라 전략 탭의 체크박스 값 사용",
            "판정 기준": "체크 시 0점은 방어 신호, 해제 시 0점 이상도 공격형 조건 충족",
            "앱 반영": "zero_is_defensive 값으로 공격/방어 판단",
        },
        {
            "구분": "오리지널 듀얼 모멘텀",
            "계산 방식": f"{us_ticker}, {intl_ticker}, {cash_ticker}의 12개월 수익률 비교",
            "판정 기준": f"{us_ticker} > {cash_ticker}이면 {us_ticker}/{intl_ticker} 중 12개월 수익률 높은 ETF, {us_ticker} ≤ {cash_ticker}이면 {bond_ticker}",
            "앱 반영": "선택된 ETF에 ODM 배정금액 100% 배분",
        },
        {
            "구분": "최근 리밸런싱일",
            "계산 방식": "Google Sheets trades 시트에서 각 전략의 대상 ETF만 필터링한 뒤 그중 가장 최근 trade_date를 사용",
            "판정 기준": f"LAA 고정은 {fixed_text} 기준, LAA 변동은 {LAA_VARIABLE_RISK}/{LAA_VARIABLE_DEFENSIVE} 기준, VAA는 {vaa_all_text} 기준, ODM은 {odm_text} 기준",
            "앱 반영": "전략별 대상 ETF 매매일이 없으면 평가 기준일을 임시 적용하고, 일정표에 해당 기준을 함께 표시",
        },
        {
            "구분": "Google Sheets 저장 리밸런싱 결과",
            "계산 방식": f"최종 주문안은 rebalance_plan, 계산 근거와 현재 설정된 고유 ETF {len(ALL_TICKERS)}개 월봉 RAW는 rebalance_basis에 덮어쓰기 저장",
            "판정 기준": f"현재 포트폴리오 기준 신규 계산은 API {len(ALL_TICKERS)}회, 수동 투자금 기준 계산은 저장 RAW를 재사용해 API 0회",
            "앱 반영": "다음 신규 계산 전까지 저장 가격으로 현재 총자산을 평가하고 수동 투자금 전략도 다시 계산",
        },
        {
            "구분": "목표 주수/매매 주수",
            "계산 방식": "종목별 목표금액을 기준으로 정수 주수를 우선 계산한 후, 남은 현금으로 매수 가능한 ETF를 1주씩 추가 배정",
            "판정 기준": "추가 1주 매수 후 전체 목표비중과의 제곱오차가 가장 작은 ETF를 선택하며, 더 이상 어떤 ETF도 1주 살 수 없을 때 종료",
            "앱 반영": "일부 ETF가 목표비중을 소폭 초과할 수 있으며, 잔여 현금을 최소화한 목표 주수에서 현재 주수를 차감해 BUY/SELL/HOLD 판정",
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

    now_text = now_kst().strftime("%Y-%m-%d %H:%M:%S")
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
st.caption("전략별 ETF 티커는 Google Sheets strategy_tickers 시트에서 관리합니다. 신규 계산 시 현재 설정된 고유 ETF만 각각 1회 조회하고, 같은 응답의 최근 실제 종가를 자산평가에도 사용합니다.")

api_key = get_secret_api_key()
sheet_id = get_secret_sheet_id()
today = today_kst()

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
    st.caption("환율은 Alpha Vantage를 호출하지 않고 Google Sheets settings!usdkrw_rate 값만 읽습니다.")
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
    strategy_ticker_df = load_strategy_tickers()
    apply_strategy_ticker_config(strategy_ticker_df)
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
# 앱 로딩 시 API를 호출하지 않고 rebalance_basis에 저장된 마지막 ETF 월봉 RAW에서
# 최근 실제 종가를 복원해 현재 총자산 평가에 사용합니다. 세션 저장 기능도 그대로 유지합니다.
seed_session_quotes_from_saved_basis(saved_rebalance_basis_df, eval_date)
portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist()) if not positions_base.empty else pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)

tab_assets, tab_strategy, tab_guide = st.tabs(["1) 자산/매매일지", "2) ETF 자산배분 리밸런싱", "3) ETF 전략별 자산군 가이드"])

# =========================================================
# 2) ETF 자산배분 리밸런싱
# =========================================================
with tab_strategy:
    st.subheader("ETF 전략 입력")

    with st.expander("전략별 ETF 티커 설정", expanded=False):
        st.caption(
            "현재 설정은 Google Sheets의 strategy_tickers 시트에 저장됩니다. "
            "ETF 티커만 변경하면 첨부 자산군 가이드 기준으로 역할과 자산군 설명을 자동 입력합니다."
        )
        st.info(
            "예: LAA 고정자산에 VTV를 입력하면 역할은 '고정자산-미국가치주', "
            "자산군 설명은 '미국 대형 가치주'로 저장됩니다. "
            "LAA 방어 타이밍자산에 SHY를 입력하면 역할은 '타이밍 방어자산-단기/초단기 국채', "
            "자산군 설명은 '미국 1~3년 국채'로 저장됩니다."
        )
        st.warning(
            "티커 변경 후에는 기존 rebalance_basis RAW에 새 티커 데이터가 없을 수 있으므로, "
            "현재 포트폴리오 기준으로 신규 조회 계산을 한 번 실행하세요."
        )

        ticker_editor = st.data_editor(
            strategy_ticker_df[["strategy_name", "group_name", "role_key", "ticker"]],
            key="strategy_ticker_editor",
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            disabled=["strategy_name", "group_name", "role_key"],
            column_config={
                "strategy_name": st.column_config.TextColumn("전략"),
                "group_name": st.column_config.TextColumn("구분"),
                "role_key": st.column_config.TextColumn("설정 ID"),
                "ticker": st.column_config.TextColumn(
                    "ETF 티커",
                    help="3) ETF 전략별 자산군 가이드에 있는 미국 ETF 티커를 입력하면 역할과 자산군 설명이 자동 매핑됩니다.",
                ),
            },
        )

        preview_df, preview_warnings = strategy_ticker_guide_preview(ticker_editor)
        st.markdown("##### 자동 입력 결과")
        st.caption("아래 역할과 자산군 설명이 Google Sheets에 저장되고 앱의 매수 전략 요약·계산·API 조회에 사용됩니다.")
        st.dataframe(preview_df, use_container_width=True, hide_index=True)

        if preview_warnings:
            with st.expander("가이드 적합성 주의사항", expanded=True):
                for warning in preview_warnings:
                    st.warning(warning)

        ticker_save_col, ticker_info_col = st.columns([1, 3])
        if ticker_save_col.button("전략별 ETF 티커 저장", type="primary"):
            normalized_tickers, ticker_errors = normalize_strategy_ticker_editor(ticker_editor, strategy_ticker_df)
            if ticker_errors:
                st.error("전략 티커 설정을 저장하지 못했습니다.")
                for error in ticker_errors:
                    st.write(f"- {error}")
            else:
                save_strategy_tickers_to_sheet(normalized_tickers)
                st.success(
                    "strategy_tickers 시트에 티커·자동 역할·자산군 설명을 저장했습니다. "
                    "새 설정을 앱 전체에 반영합니다."
                )
                st.rerun()
        ticker_info_col.info(
            f"현재 API 신규 조회 대상은 중복을 제거한 {len(ALL_TICKERS)}개 ETF입니다: {', '.join(ALL_TICKERS)}"
        )

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
        render_responsive_metrics([
            ("저장시각", saved_summary["saved_at"]),
            ("저장 기준금액(USD)", money_usd(saved_summary["total_usd"])),
            ("추가 매수 필요", money_usd(saved_summary["buy_usd"])),
            ("매도 필요", money_usd(saved_summary["sell_usd"])),
            ("예상 잔여 현금", money_usd(saved_summary["estimated_cash_usd"])),
        ])
        st.caption("새로 리밸런싱 계산 버튼을 누르기 전까지 이 결과가 Google Sheets에 유지됩니다. 이 표를 보는 것만으로는 Alpha Vantage API를 호출하지 않습니다.")
        with st.expander("저장된 리밸런싱 주문안 보기", expanded=True):
            st.dataframe(format_saved_rebalance_plan(saved_rebalance_plan_df), use_container_width=True, hide_index=True)
        if saved_rebalance_basis_df.empty:
            st.caption("저장된 계산 근거와 ETF 월봉 RAW가 아직 없습니다. 현재 포트폴리오 기준 신규 계산을 실행하면 rebalance_basis에 저장됩니다.")
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
            format_func=lambda x: f"X: 조건 미충족 → {LAA_VARIABLE_RISK}" if not x else f"O: 조건 충족 → {LAA_VARIABLE_DEFENSIVE}",
            index=0,
        )
        laa_annual_last = strategy_rebalance_dates["laa_fixed"]["last_date"]
        laa_monthly_last = strategy_rebalance_dates["laa_variable"]["last_date"]
        vaa_monthly_last = strategy_rebalance_dates["vaa"]["last_date"]
        odm_monthly_last = strategy_rebalance_dates["odm"]["last_date"]
        st.caption("최근 리밸런싱일은 위 일정표의 전략별 대상 ETF 최신 매매일을 자동 사용합니다.")
        zero_is_defensive = st.checkbox("VAA 모멘텀 스코어 0점은 방어 신호로 처리", value=True)
        if investment_basis == "현재 포트폴리오 총자산 사용":
            st.info(f"계산 버튼을 누르면 현재 설정된 고유 전략 ETF {len(ALL_TICKERS)}개를 각각 1회 조회합니다. 월봉과 최근 실제 종가를 같은 응답에서 사용하므로 총 {len(ALL_TICKERS)}회 호출합니다.")
        else:
            st.info("수동 투자금 계산은 Google Sheets rebalance_basis에 저장된 최근 ETF RAW 데이터를 사용하며 Alpha Vantage를 호출하지 않습니다.")

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
        preview_label_usd = money_usd(preview_usd)
        preview_label_krw = money_krw(preview_krw)
    else:
        if investment_currency == "KRW":
            preview_krw = float(manual_amount)
            preview_usd = preview_krw / usdkrw_rate
        else:
            preview_usd = float(manual_amount)
            preview_krw = preview_usd * usdkrw_rate
        preview_label_usd = money_usd(preview_usd)
        preview_label_krw = money_krw(preview_krw)

    render_responsive_metrics([
        ("리밸런싱 기준금액(USD)", preview_label_usd),
        ("리밸런싱 기준금액(KRW)", preview_label_krw),
        ("현재 포트폴리오 총자산", money_usd(portfolio_summary["total_usd"])),
        ("적용 환율", fx_rate_krw(usdkrw_rate)),
    ])
    st.caption(f"현재 포트폴리오 기준 계산은 현재 설정된 고유 ETF {len(ALL_TICKERS)}개에 TIME_SERIES_MONTHLY_ADJUSTED를 각각 1회 호출하며, 각 요청 사이에는 다음 요청 전 1.25초를 대기합니다. 수동 투자금 기준은 저장 RAW만 사용합니다.")

    run_button_text = f"{len(ALL_TICKERS)}개 ETF 신규 조회 후 전략 계산" if investment_basis == "현재 포트폴리오 총자산 사용" else "저장된 ETF RAW로 전략 계산(API 미호출)"
    run = st.button(run_button_text, type="primary")
    if run:
        if investment_basis == "현재 포트폴리오 총자산 사용":
            # 현재 strategy_tickers에 설정된 고유 ETF에 월봉 API를 각각 정확히 1회 호출합니다.
            # 동일 응답에서 과거 월봉과 최근 실제 종가를 함께 사용하므로 GLOBAL_QUOTE 추가 호출은 없습니다.
            data = load_all_monthly_prices(ALL_TICKERS, api_key)
            data_source = "API 신규 조회 · Alpha Vantage TIME_SERIES_MONTHLY_ADJUSTED"
        else:
            # 수동 투자금 전략 계산은 저장된 RAW만 사용하며 API 호출을 하지 않습니다.
            data = monthly_data_from_saved_basis(saved_rebalance_basis_df)
            data_source = "Google Sheets rebalance_basis 저장 RAW 재사용"
            if not data:
                st.error(f"rebalance_basis에 저장된 ETF 월봉 RAW가 없습니다. 먼저 '현재 포트폴리오 총자산 사용'으로 현재 설정된 {len(ALL_TICKERS)}개 ETF 신규 조회 계산을 한 번 실행하세요.")
                st.stop()

        missing_all = [t for t in ALL_TICKERS if t not in data or data[t].empty]
        if missing_all:
            if investment_basis == "수동 입력":
                st.error(
                    "현재 strategy_tickers 설정과 저장된 rebalance_basis RAW가 일치하지 않습니다. "
                    f"RAW가 없는 티커: {', '.join(missing_all)}. 현재 포트폴리오 총자산 기준 신규 조회를 먼저 실행하세요."
                )
                st.stop()
            st.warning(f"현재 설정된 전체 {len(ALL_TICKERS)}개 고유 ETF 중 조회 데이터가 없는 종목: {', '.join(missing_all)}")

        prices = build_price_matrix(data, DATA_TICKERS, eval_date, lookback_months, exclude_current_month)
        if prices.empty:
            st.error("전략 계산용 ETF 월봉 데이터를 가져오지 못했습니다. API 호출 제한, 저장 RAW, 평가 기준일을 확인하세요.")
            st.stop()
        actual_eval_dt = prices.index.max()
        st.success(f"전략 계산 기준월: {actual_eval_dt.strftime('%Y-%m-%d')} / 데이터: {data_source}")

        laa_variable = LAA_VARIABLE_DEFENSIVE if laa_defensive else LAA_VARIABLE_RISK
        laa_reason = f"두 조건이 모두 충족되어 {LAA_VARIABLE_DEFENSIVE} 선택" if laa_defensive else f"두 조건이 동시에 충족되지 않아 {LAA_VARIABLE_RISK} 선택"
        laa_inner = {ticker: 0.25 for ticker in LAA_FIXED}
        laa_inner[laa_variable] = 0.25
        laa_rebalance = {
            ticker: {"cycle": "연 1회", "last_date": laa_annual_last}
            for ticker in LAA_FIXED
        }
        laa_rebalance[laa_variable] = {"cycle": "월 1회", "last_date": laa_monthly_last}

        try:
            vaa_selected, vaa_scores, vaa_reason = calculate_vaa(prices, zero_is_defensive)
            odm_selected, odm_returns, odm_reason = calculate_dual_momentum(prices)
        except Exception as e:
            st.error(str(e))
            st.stop()

        # 월봉 API/저장 RAW의 가장 최근 실제 종가를 최신 평가가격으로 사용합니다.
        # 별도의 GLOBAL_QUOTE 호출은 발생하지 않습니다.
        quote_df = monthly_data_to_quotes(data, eval_date, "API_MONTHLY_CLOSE" if data_source.startswith("API") else "REBALANCE_BASIS_RAW")
        store_latest_quotes(quote_df)  # 8.3 세션 최신가 저장 기능 유지
        holding_tickers = positions_base["ticker"].dropna().astype(str).str.upper().str.strip().tolist() if not positions_base.empty else []
        quote_df_for_portfolio = add_non_strategy_session_quotes(quote_df, holding_tickers)
        portfolio_status_run, portfolio_summary_run = build_portfolio_status(positions_base, quote_df_for_portfolio, cash_balance, usdkrw_rate)

        # 계산에 사용한 최근 실제 종가를 자산/매매일지 탭의 현재 총자산에도 즉시 재사용합니다.
        portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist()) if not positions_base.empty else pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
        portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)
        quote_tickers = ALL_TICKERS.copy()

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

        valid_price_count = quote_df.dropna(subset=["latest_price_usd"]).shape[0] if not quote_df.empty else 0
        render_responsive_metrics([
            ("계산 기준 총자산(USD)", money_usd(total_investment_usd)),
            ("계산 기준 총자산(KRW)", money_krw(total_investment_krw)),
            ("가격 반영 티커 수", f"{valid_price_count}/{len(quote_tickers)}개"),
        ])

        rows: List[Dict[str, object]] = []
        rows += allocation_rows("LAA", w_laa, laa_inner, laa_rebalance, eval_date, total_investment_krw, total_investment_usd, input_currency, laa_reason)
        rows += allocation_rows("VAA 공격형", w_vaa, {vaa_selected: 1.0}, {vaa_selected: {"cycle": "월 1회", "last_date": vaa_monthly_last}}, eval_date, total_investment_krw, total_investment_usd, input_currency, vaa_reason)
        rows += allocation_rows("오리지널 듀얼 모멘텀", w_odm, {odm_selected: 1.0}, {odm_selected: {"cycle": "월 1회", "last_date": odm_monthly_last}}, eval_date, total_investment_krw, total_investment_usd, input_currency, odm_reason)

        st.markdown("#### 전략별 선택 결과")
        render_responsive_metrics([
            ("LAA 변동 25%", f"{laa_variable} · {ETF_LABELS[laa_variable]}"),
            ("VAA", f"{vaa_selected} · {ETF_LABELS[vaa_selected]}"),
            ("ODM", f"{odm_selected} · {ETF_LABELS[odm_selected]}"),
        ])

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
        plan = add_rebalance_plan(final, quote_df_for_portfolio, portfolio_status_run, usdkrw_rate)
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
            quote_df_for_portfolio,
            portfolio_status_run,
            plan,
            {"LAA": w_laa, "VAA": w_vaa, "ODM": w_odm},
            lookback_months,
            exclude_current_month,
            zero_is_defensive,
            data,
            data_source,
        )
        save_rebalance_basis_to_sheet(basis_df)
        # 방금 저장한 결과는 이미 메모리에 있으므로 Google Sheets를 즉시 다시 읽지 않습니다.
        # 다음 Streamlit 재실행 시 60초 읽기 캐시 또는 새 시트 데이터가 사용됩니다.
        st.success(f"rebalance_plan에는 최신 주문안을, rebalance_basis에는 계산 근거와 현재 설정된 고유 ETF {len(ALL_TICKERS)}개 월봉 RAW를 덮어쓰기 저장했습니다. 다음 신규 계산 전까지 현재 자산평가와 수동 투자금 전략 계산에 재사용됩니다.")

        with st.expander("이번 계산 근거 보기", expanded=False):
            st.dataframe(format_saved_rebalance_basis(basis_df), use_container_width=True, hide_index=True)

        st.markdown("#### 최종 리밸런싱 주문안")
        st.caption("목표금액을 단순 버림한 뒤 남은 현금을 방치하지 않고, 추가 1주 매수 후 전체 목표비중 오차가 가장 작은 ETF에 반복 배정합니다. 따라서 일부 종목은 목표비중을 소폭 초과할 수 있습니다.")
        plan_display = plan.copy()
        plan_display = plan_display.rename(columns={
            "ticker": "티커", "전략 전체 비중": "목표비중", "목표 투자금(USD)": "목표금액(USD)",
            "목표 투자금(KRW)": "목표금액(KRW)", "quantity": "현재수량", "market_value_usd": "현재평가액(USD)",
            "latest_price_usd": "최근가(USD)", "price_date": "가격 기준일", "weight": "현재비중",
        })
        for col in ["목표비중", "현재비중", "리밸런싱 후 비중", "목표 대비 비중차"]:
            if col in plan_display.columns:
                plan_display[col] = plan_display[col].apply(format_pct)
        for col in ["목표금액(USD)", "현재평가액(USD)", "리밸런싱 후 평가액(USD)", "예상 잔여 현금(USD)", "매매 필요 금액(USD)"]:
            if col in plan_display.columns:
                plan_display[col] = plan_display[col].apply(money_usd)
        for col in ["목표금액(KRW)", "현재 평가액(KRW)", "리밸런싱 후 평가액(KRW)", "예상 잔여 현금(KRW)", "매매 필요 금액(KRW)"]:
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
        estimated_cash_usd = pd.to_numeric(plan.get("예상 잔여 현금(USD)"), errors="coerce").dropna().iloc[0] if "예상 잔여 현금(USD)" in plan.columns and plan["예상 잔여 현금(USD)"].notna().any() else 0.0
        render_responsive_metrics([
            ("추가 매수 필요", money_usd(buy_usd)),
            ("매도 필요", money_usd(sell_usd)),
            ("순매수 필요", money_usd(buy_usd - sell_usd)),
            ("예상 잔여 현금", money_usd(estimated_cash_usd)),
        ])

        csv = plan.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("리밸런싱 주문안 CSV 다운로드", data=csv, file_name=f"rebalance_plan_{actual_eval_dt.strftime('%Y%m%d')}.csv", mime="text/csv")


# =========================================================
# 3) ETF 전략별 자산군 가이드
# =========================================================
with tab_guide:
    st.subheader("ETF 전략별·자산군별 선택 가이드")
    st.caption(
        "첨부된 LAA·VAA·오리지널 듀얼모멘텀 가이드를 앱 내 Appendix로 옮긴 화면입니다. "
        "같은 자산군이라도 추종지수, 국가 범위, 만기, 듀레이션과 신용위험이 달라질 수 있습니다."
    )

    current_guide_preview, current_guide_warnings = strategy_ticker_guide_preview(strategy_ticker_df)
    with st.expander("현재 앱 전략 티커의 가이드 매칭 결과", expanded=True):
        st.dataframe(current_guide_preview, use_container_width=True, hide_index=True)
        if current_guide_warnings:
            for warning in current_guide_warnings:
                st.warning(warning)

    guide_quick_tab, guide_detail_tab, guide_portfolio_tab, guide_master_tab, guide_caution_tab = st.tabs(
        ["빠른 결론", "전략별 상세 가이드", "실전 구성안", "통합 ETF 목록", "교체 시 주의사항"]
    )

    with guide_quick_tab:
        st.markdown("#### 전략 역할별 우선 추천 ETF")
        st.dataframe(pd.DataFrame(ETF_GUIDE_QUICK_ROWS), use_container_width=True, hide_index=True)
        st.info(
            "LAA 기본 구조는 가치주 25% + 금 25% + 중기국채 25% + 타이밍자산 25%입니다. "
            "VAA는 공격형 후보의 모멘텀이 나쁘면 방어형으로 이동하고, 듀얼모멘텀은 미국·해외 주식의 상대모멘텀과 "
            "현금성 기준자산 대비 절대모멘텀을 함께 확인합니다."
        )

    with guide_detail_tab:
        guide_df = etf_asset_guide_df()
        filter_col1, filter_col2 = st.columns([1, 2])
        strategy_filter = filter_col1.selectbox(
            "전략 구분",
            options=["전체", "LAA", "VAA", "듀얼모멘텀", "보완 자산군"],
            key="guide_strategy_filter",
        )
        ticker_search = filter_col2.text_input(
            "ETF 티커 검색",
            value="",
            placeholder="예: VTV, SHY, IEFA",
            key="guide_ticker_search",
        ).upper().strip()

        filtered_guide = guide_df.copy()
        if strategy_filter == "LAA":
            filtered_guide = filtered_guide[
                (filtered_guide["strategy"] == "LAA")
                & ~filtered_guide["category_key"].astype(str).str.startswith("supplement_")
            ]
        elif strategy_filter == "VAA":
            filtered_guide = filtered_guide[filtered_guide["strategy"] == "VAA"]
        elif strategy_filter == "듀얼모멘텀":
            filtered_guide = filtered_guide[filtered_guide["strategy"] == "듀얼모멘텀"]
        elif strategy_filter == "보완 자산군":
            filtered_guide = filtered_guide[
                filtered_guide["category_key"].astype(str).str.startswith("supplement_")
            ]

        if ticker_search:
            filtered_guide = filtered_guide[
                filtered_guide["ticker"].astype(str).str.contains(ticker_search, case=False, na=False)
            ]

        if filtered_guide.empty:
            st.warning("조건에 맞는 ETF 가이드가 없습니다.")
        else:
            section_order = list(dict.fromkeys(row["section"] for row in ETF_ASSET_GUIDE_ROWS))
            for section in section_order:
                section_df = filtered_guide[filtered_guide["section"] == section]
                if section_df.empty:
                    continue
                with st.expander(section, expanded=bool(ticker_search)):
                    show = section_df[
                        ["strategy", "auto_role", "asset_class", "ticker", "best_for"]
                    ].rename(columns={
                        "strategy": "전략",
                        "auto_role": "자동 입력 역할",
                        "asset_class": "자산군 설명",
                        "ticker": "ETF",
                        "best_for": "더 적합한 상황",
                    })
                    st.dataframe(show, use_container_width=True, hide_index=True)

    with guide_portfolio_tab:
        st.markdown("#### 전략별 실전 구성안")
        st.dataframe(pd.DataFrame(ETF_GUIDE_PRACTICAL_ROWS), use_container_width=True, hide_index=True)

    with guide_master_tab:
        st.markdown("#### 세 전략 통합 ETF 마스터 목록")
        st.dataframe(pd.DataFrame(ETF_GUIDE_MASTER_ROWS), use_container_width=True, hide_index=True)
        st.info("최소 통합 목록: VTV, GLD, IEF, QQQ, BIL, SPY, IEFA, IEMG, AGG")
        st.info("원형 VAA 방어자산까지 유지하는 확장 목록: VTV, GLD, IEF, QQQ, BIL, SPY, IEFA, IEMG, AGG, SHY, LQD")

    with guide_caution_tab:
        st.markdown("#### ETF 교체 시 확인사항")
        for note in ETF_GUIDE_CAUTION_NOTES:
            st.markdown(f"- {note}")
        st.warning(
            "레버리지·인버스·커버드콜 ETF는 원래 LAA·VAA·오리지널 듀얼모멘텀의 "
            "동일 자산 대체재로 자동 분류하지 않습니다."
        )


# =========================================================
# 1) 자산/매매일지
# =========================================================
with tab_assets:
    st.subheader("현재 자산 현황")
    st.caption(f"이 탭에서는 Alpha Vantage를 호출하지 않습니다. 마지막 리밸런싱 계산 때 rebalance_basis에 저장한 전략 ETF 최근 실제 종가와 현재 세션의 수동 가격을 사용합니다. 현재 전략 설정은 고유 ETF {len(ALL_TICKERS)}개입니다.")

    refresh_cols = st.columns([1, 5])
    if refresh_cols[0].button("세션 최신가 지우기"):
        st.session_state["latest_quotes_df"] = pd.DataFrame(columns=["ticker", "latest_price_usd", "price_date", "source", "fetched_at"])
        portfolio_quotes = get_cached_quotes_for_tickers(positions_base["ticker"].tolist())
        portfolio_status, portfolio_summary = build_portfolio_status(positions_base, portfolio_quotes, cash_balance, usdkrw_rate)
        st.success("이번 세션의 최신가를 지웠습니다. 앱 세션을 새로 시작하면 rebalance_basis 저장 가격을 다시 복원합니다.")

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
            st.warning(f"rebalance_basis 저장 가격 또는 수동 가격이 없습니다. 아래 총자산은 {fallback_count}개 종목을 평균매수가로 임시 반영합니다. 현재 설정된 {len(ALL_TICKERS)}개 ETF 신규 리밸런싱 계산을 실행하세요.")
        else:
            st.warning("rebalance_basis 저장 가격과 평균매수가가 모두 없는 종목은 총자산에 반영하지 못했습니다. 전략 외 보유종목은 수동 최신가를 입력할 수 있습니다.")
    else:
        st.info(quote_cache_info(portfolio_quotes))

    render_responsive_metrics([
        ("총 자산(USD)", money_usd(portfolio_summary["total_usd"])),
        ("총 자산(KRW)", money_krw(portfolio_summary["total_krw"])),
        ("주식/ETF 평가액", money_usd(portfolio_summary["stock_value_usd"])),
        ("적용 환율", fx_rate_krw(usdkrw_rate)),
    ])
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
            "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
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
            ticker = tc2.text_input("티커", value=ODM_ROLE_TICKERS["us_equity"] if i == 0 else "", key=f"trade_form_ticker_{i}").upper().strip()
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
        created_at = now_kst().strftime("%Y-%m-%d %H:%M:%S")

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

