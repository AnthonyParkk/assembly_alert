# app.py — 과방위 법안 대시보드 (22대 시작일 필터 통합판 / secrets 없이도 동작)
# - 22대 시작일(2024-05-30)부터 필터
# - 헤더 가운데 정렬 / 열 폭 균등 / 2025-01 높이 확대
# - NaN/None → '-' 처리 / CSV 다운로드 / 새로고침 캐시 / 오류 표시
# - 로컬(.env) 우선, 없으면 Cloud의 st.secrets 사용(없어도 에러 안 남)

import os, requests, pandas as pd, certifi
from datetime import datetime, timedelta
from dotenv import load_dotenv
import streamlit as st

# ======================
# 기본 셋업 (.env, SSL)
# ======================
os.environ["SSL_CERT_FILE"] = certifi.where()

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # 스크립트로 실행 시
except NameError:
    BASE_DIR = os.getcwd()  # 노트북 등에서 테스트 시

# .env 로드
load_dotenv(os.path.join(BASE_DIR, ".env"))

def env_or_secret(key: str, default=None):
    """로컬 .env → 없으면 st.secrets → 둘 다 없으면 default"""
    val = os.getenv(key, None)
    if val not in (None, ""):
        return val
    try:
        # secrets.toml이 아예 없을 때 접근해도 except로 빠져 안전
        return st.secrets[key]
    except Exception:
        return default

# 환경 변수/시크릿 읽기
API_KEY = env_or_secret("NA_OPEN_API_KEY")
AGE     = int(env_or_secret("NA_ASSEMBLY_AGE", "22"))

# 국회 Open API 설정
DATA_ID   = "nzmimeepazxkubdpn"
BASE_URL  = f"https://open.assembly.go.kr/portal/openapi/{DATA_ID}"

# 22대 시작일(기본 필터)
K22_START = pd.Timestamp("2024-05-30")

# UI 기본값
DEFAULT_COMMITTEE = "과학기술정보방송통신위원회"
DEFAULT_START     = K22_START
DEFAULT_PSIZE     = 100
DEFAULT_MAXPAGES  = 200

# ======================
# Streamlit 레이아웃/UI
# ======================
st.set_page_config(page_title="과방위 법안 대시보드", page_icon="📜", layout="wide")
st.title("📜 과학기술정보방송통신위원회 법안 대시보드")

with st.sidebar:
    st.header("필터")
    committee   = st.text_input("소관위원회", value=DEFAULT_COMMITTEE)
    start_date  = st.date_input("시작일(22대 기준)", value=DEFAULT_START.date())
    page_size   = st.number_input("페이지 크기 (pSize)", min_value=50, max_value=1000, step=50, value=DEFAULT_PSIZE)
    max_pages   = st.number_input("최대 페이지 수", min_value=10, max_value=500, step=10, value=DEFAULT_MAXPAGES)
    search_kw   = st.text_input("검색어(법률안명 또는 대표발의자 포함)")
    clicked_refresh = st.button("🔄 데이터 새로고침")
    st.caption("※ 시작일 이후 + 소관위원회 일치 + (검색어 포함)")

# ======================
# 유틸
# ======================
def bill_detail_link(row: dict) -> str:
    link = row.get("DETAIL_LINK")
    if link and isinstance(link, str) and link.strip():
        return link
    bill_id = row.get("BILL_ID")
    return f"https://likms.assembly.go.kr/bill/billDetail.do?billId={bill_id}" if bill_id else ""

def _fetch_page(page: int, size: int, age: int, api_key: str):
    params = {"KEY": api_key, "Type": "json", "pIndex": page, "pSize": size, "AGE": age}
    r = requests.get(BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    js = r.json()
    # RESULT 체크
    if isinstance(js, dict) and "RESULT" in js:
        code = js["RESULT"].get("CODE")
        msg  = js["RESULT"].get("MESSAGE")
        if code != "INFO-000":
            raise RuntimeError(f"[국회API 오류] {code} - {msg}")
    block = next((b for b in js.get(DATA_ID, []) if isinstance(b, dict) and "row" in b), {})
    return block.get("row", [])

@st.cache_data(ttl=600, show_spinner=False)
def fetch_all_rows(age: int, api_key: str, page_size: int, max_pages: int):
    """여러 페이지 수집(10분 캐시) — 최신부터 과거로 충분히 긁어옴"""
    acc, total = [], 0
    for p in range(1, max_pages + 1):
        rows = _fetch_page(p, page_size, age, api_key)
        if not rows:
            break
        total += len(rows)
        acc.extend(rows)
        if len(rows) < page_size:
            break
    fetched_at = datetime.now()
    return acc, total, fetched_at

def build_dataframe(rows: list) -> pd.DataFrame:
    """원본 rows → 표시용 DataFrame(날짜는 datetime으로 보관)"""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "법률안명": r.get("BILL_NAME"),
        "제안일": r.get("PROPOSE_DT"),
        "대표발의": (r.get("RST_PROPOSER") or r.get("PROPOSER")),
        "소관위상정일": r.get("CMT_PRESENT_DT"),
        "소관위처리일": r.get("CMT_PROC_DT"),
        "소관위처리결과": r.get("CMT_PROC_RESULT_CD"),
        "상세보기": bill_detail_link(r),
        "소관위원회": r.get("COMMITTEE"),
    } for r in rows])

    # 날짜 파싱
    for col in ["제안일", "소관위상정일", "소관위처리일"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # 정렬(최신 제안일 우선)
    if "제안일" in df.columns:
        df = df.sort_values("제안일", ascending=False)

    return df

def filter_dataframe(df: pd.DataFrame, committee: str, start_date, search_kw: str):
    """소관위/시작일/검색어 필터 — 22대 시작일을 기본값으로 강제 반영"""
    if df.empty:
        return df
    # 소관위
    if committee:
        df = df[df["소관위원회"] == committee]
    # 시작일(22대 기본)
    start_ts = pd.Timestamp(start_date) if start_date else K22_START
    if "제안일" in df.columns:
        df = df[df["제안일"] >= start_ts]
    # 검색어(법률안명 OR 대표발의)
    if search_kw:
        mask = pd.Series(False, index=df.index)
        for col in ["법률안명", "대표발의"]:
            if col in df.columns:
                mask |= df[col].fillna("").astype(str).str.contains(search_kw, case=False)
        df = df[mask]
    return df

def render_table(df: pd.DataFrame, title: str, *, height_px: int = 500):
    """표 렌더 + 균등폭 + 헤더 중앙정렬 + 스크롤 + CSV 다운로드"""
    st.subheader(title)
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        return

    show = df.copy()

    # 표 컬럼 순서(고정)
    cols = ["법률안명","제안일","대표발의","소관위상정일","소관위처리일","소관위처리결과","상세보기"]
    cols = [c for c in cols if c in show.columns]
    show = show[cols].copy()

    # 날짜 → 문자열, 결측치 '-'
    for c in ["제안일","소관위상정일","소관위처리일"]:
        if c in show.columns:
            show[c] = show[c].dt.strftime("%Y-%m-%d")
            show[c] = show[c].fillna("-")
    for c in ["법률안명","대표발의","소관위처리결과","상세보기"]:
        if c in show.columns:
            show[c] = show[c].fillna("-")

    # 상세보기 링크를 a태그로
    if "상세보기" in show.columns:
        show["상세보기"] = show["상세보기"].apply(
            lambda url: f'<a href="{url}" target="_blank">바로가기</a>' if isinstance(url, str) and url and url != "-" else "-"
        )

    # 균등 폭 계산
    n_cols = len(cols)
    equal_width = f"{100 / max(1, n_cols):.3f}%"

    # DataFrame → HTML (링크 유지)
    df_html = show.to_html(index=False, escape=False)

    # 스타일 + 스크롤 박스
    style = f"""
    <style>
      .billwrap {{
        max-height: {height_px}px;
        overflow-y: auto;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
      }}
      table.billtable {{
        border-collapse: collapse;
        width: 100%;
        table-layout: fixed;
        font-size: 14px;
      }}
      table.billtable th, table.billtable td {{
        border: 1px solid #ddd;
        padding: 8px;
        vertical-align: top;
        word-wrap: break-word;
        overflow-wrap: anywhere;
        width: {equal_width};
      }}
      table.billtable th {{
        background: #f6f6f6;
        text-align: center;     /* 헤더 가운데 정렬 */
      }}
      table.billtable tr:nth-child(even) {{ background: #fbfbfb; }}
    </style>
    """

    # pandas가 생성한 <table> 태그를 우리 클래스명으로 교체
    inner = df_html.replace('<table border="1" class="dataframe">', '<table class="billtable">')
    html = f'{style}<div class="billwrap">{inner}</div>'

    st.components.v1.html(html, height=height_px + 50, scrolling=False)

    # CSV 다운로드 (현재 보이는 열 기준)
    csv_bytes = show.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="⬇️ 이 표를 CSV로 다운로드",
        data=csv_bytes,
        file_name=f"bills_{title.replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

# ======================
# 메인 로직
# ======================
# API 키 없으면 화면에 바로 표시하고 중단
if not API_KEY:
    st.error("`.env`의 NA_OPEN_API_KEY가 비어 있습니다. (클라우드에서는 Secrets에 설정하세요)")
    st.stop()

# 새로고침 누르면 캐시 무효화
if clicked_refresh:
    fetch_all_rows.clear()

# 데이터 가져오기 (에러도 화면에 표시)
with st.spinner("데이터 불러오는 중..."):
    try:
        rows, total_cnt, fetched_at = fetch_all_rows(AGE, API_KEY, int(page_size), int(max_pages))
    except Exception as e:
        st.exception(e)
        st.stop()

st.caption(f"총 수집: {total_cnt}건 • 캐시 시각: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}")

# DF 구축 & 필터 (22대 시작일을 기본으로 적용)
df_all = build_dataframe(rows)
if df_all.empty:
    st.warning("수집된 데이터가 없습니다. (API 응답이 비었거나 필드 구조가 변경되었을 수 있음)")
    st.stop()

df = filter_dataframe(df_all, committee, start_date, search_kw)

# 뷰: 최근 1주 / 월별 / 전체
tabs = st.tabs(["최근 1주", "월별", "전체 목록"])
now = pd.Timestamp.now()
one_week_ago = now - timedelta(days=7)

with tabs[0]:
    df_week = df[df["제안일"] >= one_week_ago] if "제안일" in df.columns else pd.DataFrame()
    render_table(df_week, "최근 1주일 내 발의 법안", height_px=500)

with tabs[1]:
    if df.empty or "제안일" not in df.columns:
        st.info("표시할 데이터가 없습니다.")
    else:
        tmp = df.copy()
        tmp["YYYY-MM"] = tmp["제안일"].dt.to_period("M").astype(str)
        # 최신 월부터 표시
        for ym, g in sorted(tmp.groupby("YYYY-MM"), key=lambda x: x[0], reverse=True):
            # 2025-01 월은 좀 더 높게(스크롤 박스 높이 700px)
            if ym == "2025-01":
                render_table(g, f"{ym} 발의 법안", height_px=700)
            else:
                render_table(g, f"{ym} 발의 법안", height_px=500)

with tabs[2]:
    render_table(df, "전체 목록", height_px=700)

st.success("완료")
