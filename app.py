# app.py
import os
import io
import json
import pandas as pd
import streamlit as st

# (선택) 로컬 개발 시 .env 사용 지원
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# AgGrid
from st_aggrid import AgGrid, GridOptionsBuilder

# -------------------------------------------------
# 기본 설정
# -------------------------------------------------
st.set_page_config(page_title="법안 목록 뷰어", layout="wide")

# -------------------------------------------------
# 공용 유틸
# -------------------------------------------------
def get_api_key(key_name="NA_OPEN_API_KEY"):
    """
    클라우드: st.secrets
    로컬: .env 또는 OS 환경변수
    """
    try:
        if "secrets" in dir(st) and key_name in st.secrets:
            return st.secrets[key_name]
    except Exception:
        pass
    return os.getenv(key_name, "").strip()

def normalize_blank(v, placeholder="—"):
    s = "" if v is None else str(v).strip()
    return s if s else placeholder

def coerce_columns(raw_rows):
    """
    입력 데이터(딕셔너리 리스트)를 표 컬럼 스펙에 맞춰 정규화.
    • '소관위상정일/소관위처리일/소관위처리결과' 등으로 들어와도
      내부 표시는 '상정일/처리일/처리결과' 로 맵핑.
    """
    rows = []
    for r in raw_rows:
        # 원본 키들
        bill_name = r.get("법률안명") or r.get("BILL_NAME") or r.get("BILL_TITLE")
        proposed  = r.get("제안일") or r.get("PROPOSE_DT")
        sponsor   = r.get("대표발의") or r.get("RST_PROPOSER") or r.get("대표발의자")
        # 소관위 관련 키들(들어오는 이름이 달라도 수용)
        cmt_present_dt = r.get("소관위상정일") or r.get("CMT_PRESENT_DT") or r.get("상정일")
        cmt_proc_dt    = r.get("소관위처리일") or r.get("CMT_PROC_DT") or r.get("처리일")
        cmt_proc_rslt  = r.get("소관위처리결과") or r.get("CMT_PROC_RESULT_CD") or r.get("처리결과")
        detail         = r.get("상세보기") or r.get("DETAIL") or r.get("LINK") or "바로가기"

        rows.append({
            "법률안명": normalize_blank(bill_name),
            "제안일": normalize_blank(proposed),
            "대표발의": normalize_blank(sponsor),
            # 소관위 그룹 하위열(표시명은 간단히)
            "상정일": normalize_blank(cmt_present_dt),
            "처리일": normalize_blank(cmt_proc_dt),
            "처리결과": normalize_blank(cmt_proc_rslt),
            "상세보기": normalize_blank(detail),
        })
    return rows

def preset_widths(compact=True):
    """
    픽셀 고정 프리셋.
    compact=True: 모바일 우선(법률안명 타이트 + 2줄 클램프)
    """
    if compact:
        return {
            "법률안명": 280,   # 모바일 타이트
            "제안일": 110,
            "대표발의": 110,
            "상정일": 110,
            "처리일": 110,
            "처리결과": 140,
            "상세보기": 110,
        }
    else:
        return {
            "법률안명": 360,   # 데스크톱 여유
            "제안일": 120,
            "대표발의": 120,
            "상정일": 120,
            "처리일": 120,
            "처리결과": 160,
            "상세보기": 120,
        }

def build_grid(df: pd.DataFrame, widths: dict):
    """
    AgGrid 옵션 구성:
    - 모든 텍스트 가운데 정렬
    - 법률안명 2줄 클램프(모바일 가독성)
    - 상단 그룹헤더: '소관위' 아래에 상정일/처리일/처리결과
    """
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        resizable=True,
        sortable=True,
        filter=True,
        cellStyle={"textAlign": "center"}  # 본문 가운데 정렬
    )

    # 헤더 가운데 정렬 + 2줄 클램프 CSS
    st.markdown("""
    <style>
      .ag-header-cell-label { justify-content: center !important; }
      .ag-cell .clamp2 {
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        line-height: 1.2em;
        max-height: calc(1.2em * 2);
      }
    </style>
    """, unsafe_allow_html=True)

    # 셀 렌더러(JS) - 법률안명 2줄까지만 표시 + 툴팁 전체 노출
    CLAMP_RENDERER = """
    class ClampRenderer {
      init(params) {
        const v = params.value == null ? "" : String(params.value);
        const div = document.createElement('div');
        div.className = 'clamp2';
        div.title = v;          // hover 시 전체 표시
        div.innerText = v;
        this.eGui = div;
      }
      getGui() { return this.eGui; }
    }
    """

    column_defs = [
        {
            "field": "법률안명",
            "headerName": "법률안명",
            "width": widths["법률안명"],
            "cellRenderer": "ClampRenderer",
            "tooltipField": "법률안명",
        },
        {"field": "제안일", "headerName": "제안일", "width": widths["제안일"]},
        {"field": "대표발의", "headerName": "대표발의", "width": widths["대표발의"]},
        {
            "headerName": "소관위",
            "children": [
                {"field": "상정일", "headerName": "상정일", "width": widths["상정일"]},
                {"field": "처리일", "headerName": "처리일", "width": widths["처리일"]},
                {"field": "처리결과", "headerName": "처리결과", "width": widths["처리결과"]},
            ]
        },
        {"field": "상세보기", "headerName": "상세보기", "width": widths["상세보기"]},
    ]

    grid_options = gb.build()
    grid_options["columnDefs"] = column_defs
    grid_options["frameworkComponents"] = {"ClampRenderer": CLAMP_RENDERER}
    grid_options["suppressSizeToFit"] = True  # 우리가 준 px 폭 유지

    AgGrid(
        df,
        gridOptions=grid_options,
        fit_columns_on_grid_load=False,   # px 폭 그대로
        enable_enterprise_modules=False,
        height=260
    )

# -------------------------------------------------
# 데이터 소스 섹션
# -------------------------------------------------
st.title("법안 목록 (모바일 압축 + 소관위 그룹)")

# 사이드바: 모드/입력
with st.sidebar:
    st.subheader("보기 설정")
    compact = st.checkbox("모바일 압축 모드", value=True, help="법률안명을 2줄까지만 보여주고 폭을 타이트하게 합니다.")
    st.markdown("---")
    st.subheader("데이터 입력")
    mode = st.radio("데이터 소스", ["예시 데이터", "CSV 업로드"], horizontal=True)

    api_key = get_api_key()
    if not api_key:
        st.info("f5e02e2f64fb4fab9011f81e04122e2b")

# 예시 또는 업로드
raw_rows = []
if mode == "CSV 업로드":
    up = st.file_uploader("CSV 업로드 (UTF-8 / 첫 행은 헤더)", type=["csv"])
    if up is not None:
        try:
            df_up = pd.read_csv(up)
            raw_rows = df_up.to_dict(orient="records")
        except Exception as e:
            st.error(f"CSV 읽기 오류: {e}")
else:
    # 질문에서 주신 예시 1행 (원하는 만큼 추가해도 됨)
    raw_rows = [{
        "법률안명": "정보통신망 이용촉진 및 정보보호 등에 관한 법률 일부개정법률안",
        "제안일": "2025-08-25",
        "대표발의": "이훈",
        "소관위상정일": "2024-12-09",
        "소관위처리일": "2024-12-09",
        "소관위처리결과": "대안반영폐기",
        "상세보기": "바로가기",
    }]

# 정규화 → DataFrame
rows = coerce_columns(raw_rows)
df = pd.DataFrame(rows)

# 폭 프리셋 → 그리드 렌더
widths = preset_widths(compact=compact)
build_grid(df, widths)
