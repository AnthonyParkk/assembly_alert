# app.py — 상임위 의안 대시보드 (위원회 선택: 과방위 or 국토위)
# - 22대 시작일(2024-05-30) 기본 필터
# - 한 번만 데이터 수집 → 선택한 위원회만 렌더
# - 표: 열 폭 px 고정 / '소관위' 그룹 헤더 / 가운데 정렬 / 법률안명 2줄 클램프
# - NaN/None → '-' 처리 / CSV 다운로드 / 캐시(10분)
# - 로컬(.env) 우선, 없으면 st.secrets 사용

import os, requests, pandas as pd, certifi, html as _html
from datetime import datetime, timedelta
from dotenv import load_dotenv
import streamlit as st

# ======================
# 기본 셋업 (.env, SSL)
# ======================
os.environ["SSL_CERT_FILE"] = certifi.where()
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()
load_dotenv(os.path.join(BASE_DIR, ".env"))

def env_or_secret(key: str, default=None):
    """로컬 .env → 없으면 st.secrets → 둘 다 없으면 default"""
    val = os.getenv(key, None)
    if val not in (None, ""):
        return val
    try:
        return st.secrets[key]
    except Exception:
        return default

API_KEY = env_or_secret("NA_OPEN_API_KEY")
AGE     = int(env_or_secret("NA_ASSEMBLY_AGE", "22"))

# 국회 Open API
DATA_ID   = "nzmimeepazxkubdpn"
BASE_URL  = f"https://open.assembly.go.kr/portal/openapi/{DATA_ID}"

# 22대 시작일
K22_START = pd.Timestamp("2024-05-30")

# UI 프리셋
DEFAULT_START     = K22_START
DEFAULT_PSIZE     = 100
DEFAULT_MAXPAGES  = 200
COMMITTEES        = ["과학기술정보방송통신위원회", "국토교통위원회"]

# ======================
# 페이지 메타 & 헤더
# ======================
st.set_page_config(
    page_title="과방·국토위 법안 현황",   # 🔗 카톡/슬랙 미리보기 제목
    page_icon="📜",
    layout="wide"
)

st.title("📜 과방·국토위 법안 현황")
st.caption("22대 기준, 과방위/국토위 법안 현황 대시보드")   # 🔗 카톡/슬랙 미리보기 설명

# ======================
# 사이드바 (필터 + 위원회 선택)
# ======================
with st.sidebar:
    st.header("필터")
    committee_choice = st.radio("위원회", COMMITTEES, index=0, horizontal=False)
    start_date  = st.date_input("시작일(22대 기준)", value=DEFAULT_START.date())
    page_size   = st.number_input("페이지 크기 (pSize)", min_value=50, max_value=1000, step=50, value=DEFAULT_PSIZE)
    max_pages   = st.number_input("최대 페이지 수", min_value=10, max_value=500, step=10, value=DEFAULT_MAXPAGES)
    search_kw   = st.text_input("검색어(법률안명 또는 대표발의자 포함)")
    clicked_refresh = st.button("🔄 데이터 새로고침")
    st.caption("※ 시작일 이후 + 위원회 일치 + (검색어 포함)")

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
    # 시작일
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

# ======================
# 표 렌더링 (px 고정폭 + 그룹 헤더 + 가운데 정렬 + 2줄 클램프)
# ======================
def render_table(df: pd.DataFrame, title: str, *, height_px: int = 500):
    st.subheader(title)
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        return

    show = df.copy()

    # 표 컬럼 순서
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

    # 링크
    if "상세보기" in show.columns:
        show["상세보기"] = show["상세보기"].apply(
            lambda url: f'<a href="{_html.escape(url, quote=True)}" target="_blank">바로가기</a>'
            if isinstance(url, str) and url and url != "-" else "-"
        )

    # 열 너비(px)
    col_width_px = {
        "법률안명": 280,
        "제안일": 110,
        "대표발의": 110,
        "소관위상정일": 110,
        "소관위처리일": 110,
        "소관위처리결과": 140,
        "상세보기": 110,
    }

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
        width: max-content;
        table-layout: fixed;
        font-size: 14px;
      }}
      table.billtable th, table.billtable td {{
        border: 1px solid #ddd;
        padding: 8px;
        vertical-align: middle;
        text-align: center;
        overflow: hidden;
        white-space: normal;
        word-wrap: break-word;
        overflow-wrap: anywhere;
      }}
      table.billtable th {{ background: #f6f6f6; }}
      table.billtable tr:nth-child(even) {{ background: #fbfbfb; }}
      .clamp2 {{
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        line-height: 1.2em;
        max-height: calc(1.2em * 2);
      }}
    </style>
    """

    def _colgroup_html(ordered_cols):
        parts = []
        for c in ordered_cols:
            w = col_width_px.get(c, 120)
            parts.append(f'<col style="width:{w}px" />')
        return "<colgroup>" + "".join(parts) + "</colgroup>"

    has_grp = all(c in show.columns for c in ["소관위상정일","소관위처리일","소관위처리결과"])
    header_row1, header_row2, ordered = [], [], []

    for c in ["법률안명","제안일","대표발의"]:
        if c in show.columns:
            ordered.append(c)
            header_row1.append(f"<th rowspan='2'>{c}</th>")

    if has_grp:
        span = sum(1 for c in ["소관위상정일","소관위처리일","소관위처리결과"] if c in show.columns)
        header_row1.append(f"<th colspan='{span}'>소관위</th>")
        for sub in ["소관위상정일","소관위처리일","소관위처리결과"]:
            if sub in show.columns:
                ordered.append(sub)
                header_row2.append(f"<th>{sub.replace('소관위','')}</th>")
    else:
        for sub in ["소관위상정일","소관위처리일","소관위처리결과"]:
            if sub in show.columns:
                ordered.append(sub)
                header_row1.append(f"<th rowspan='2'>{sub}</th>")

    if "상세보기" in show.columns:
        ordered.append("상세보기")
        header_row1.append("<th rowspan='2'>상세보기</th>")

    body_rows = []
    for _, r in show.iterrows():
        tds = []
        for c in ordered:
            v = r[c]
            if c == "법률안명":
                safe = _html.escape(str(v), quote=False) if "<a " not in str(v) else str(v)
                tds.append(f'<td title="{_html.escape(str(v), quote=True)}"><div class="clamp2">{safe}</div></td>')
            else:
                if c == "상세보기" and isinstance(v, str) and v.startswith("<a "):
                    tds.append(f"<td>{v}</td>")
                else:
                    tds.append(f"<td>{_html.escape(str(v), quote=False)}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    table_html = f"""
    <table class="billtable">
      {_colgroup_html(ordered)}
      <thead>
        <tr>{''.join(header_row1)}</tr>
        <tr>{''.join(header_row2)}</tr>
      </thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
    """

    st.components.v1.html(style + f'<div class="billwrap">{table_html}</div>', height=height_px + 70, scrolling=False)

    # CSV 다운로드
    csv_bytes = show[ordered].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="⬇️ 이 표를 CSV로 다운로드",
        data=csv_bytes,
        file_name=f"bills_{title.replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

# ======================
# 메인 로직 (한 번만 수집 → 선택한 위원회만 렌더)
# ======================
if not API_KEY:
    st.error("`.env`의 NA_OPEN_API_KEY가 비어 있습니다. (클라우드에서는 Secrets에 설정하세요)")
    st.stop()

if clicked_refresh:
    fetch_all_rows.clear()

with st.spinner("데이터 불러오는 중..."):
    try:
        rows, total_cnt, fetched_at = fetch_all_rows(AGE, API_KEY, int(page_size), int(max_pages))
    except Exception as e:
        st.exception(e)
        st.stop()

st.caption(f"총 수집: {total_cnt}건 • 캐시 시각: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')} • pSize={page_size}")

df_all = build_dataframe(rows)
if df_all.empty:
    st.warning("수집된 데이터가 없습니다. (API 응답이 비었거나 필드 구조가 변경되었을 수 있음)")
    st.stop()

# 선택한 위원회만 필터 → 탭 3종 렌더
now = pd.Timestamp.now()
one_week_ago = now - timedelta(days=7)

df_c = filter_dataframe(df_all, committee_choice, start_date, search_kw)

st.markdown(f"## {committee_choice}")
tabs = st.tabs(["최근 1주", "월별", "전체 목록"])

with tabs[0]:
    df_week = df_c[df_c["제안일"] >= one_week_ago] if "제안일" in df_c.columns else pd.DataFrame()
    render_table(df_week, f"최근 1주일 내 발의 법안 ({committee_choice})", height_px=500)

with tabs[1]:
    if df_c.empty or "제안일" not in df_c.columns:
        st.info("표시할 데이터가 없습니다.")
    else:
        tmp = df_c.copy()
        tmp["YYYY-MM"] = tmp["제안일"].dt.to_period("M").astype(str)
        for ym, g in sorted(tmp.groupby("YYYY-MM"), key=lambda x: x[0], reverse=True):
            render_table(g, f"{ym} 발의 법안 ({committee_choice})", height_px=(700 if ym == "2025-01" else 500))

with tabs[2]:
    render_table(df_c, f"전체 목록 ({committee_choice})", height_px=700)

st.success("완료")
