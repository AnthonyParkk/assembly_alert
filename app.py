def render_table(df: pd.DataFrame, title: str, *, height_px: int = 500):
    """표 렌더 (열 너비 픽셀 고정 + '소관위' 그룹 헤더 + 가운데 정렬 + 스크롤 + CSV 다운로드)"""
    import html as _html

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
            lambda url: f'<a href="{_html.escape(url, quote=True)}" target="_blank">바로가기</a>'
            if isinstance(url, str) and url and url != "-" else "-"
        )

    # 🔒 열 너비(px) — 모바일에서 타이트하게 보기 좋게 조정
    # 필요하면 아래 숫자만 바꾸면 전 화면에 동일 규격 적용됨
    col_width_px = {
        "법률안명": 280,   # 더 줄이고 싶으면 260/240으로
        "제안일": 110,
        "대표발의": 110,
        "소관위상정일": 110,  # 그룹: 소관위(상정일/처리일/처리결과)
        "소관위처리일": 110,
        "소관위처리결과": 140,
        "상세보기": 110,
    }

    # 🔤 가운데 정렬 + 2줄 클램프(법률안명)
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
        width: max-content;        /* 고정폭 테이블 */
        table-layout: fixed;
        font-size: 14px;
      }}
      table.billtable th, table.billtable td {{
        border: 1px solid #ddd;
        padding: 8px;
        vertical-align: middle;
        text-align: center;        /* 전체 가운데 정렬 */
        overflow: hidden;
        white-space: normal;
        word-wrap: break-word;
        overflow-wrap: anywhere;
      }}
      table.billtable th {{ background: #f6f6f6; }}
      table.billtable tr:nth-child(even) {{ background: #fbfbfb; }}

      /* 법률안명 2줄 클램프 */
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

    # 🔺colgroup으로 각 열 폭을 px로 고정
    def _colgroup_html(ordered_cols):
        parts = []
        for c in ordered_cols:
            w = col_width_px.get(c, 120)
            parts.append(f'<col style="width:{w}px" />')
        return "<colgroup>" + "".join(parts) + "</colgroup>"

    # ✅ 헤더 2줄 구성: 1행 = 일반 3개 + "소관위"(colspan=3) + 상세보기, 2행 = 하위 3개
    # 현재 데이터프레임에 없는 열은 자동으로 생략
    has_grp = all(c in show.columns for c in ["소관위상정일","소관위처리일","소관위처리결과"])
    header_row1 = []
    header_row2 = []

    # 열 순서 기준
    ordered = []
    for c in ["법률안명","제안일","대표발의"]:
        if c in show.columns:
            ordered.append(c)
            header_row1.append(f"<th rowspan='2'>{c}</th>")

    if has_grp:
        # 그룹 헤더
        span = sum(1 for c in ["소관위상정일","소관위처리일","소관위처리결과"] if c in show.columns)
        header_row1.append(f"<th colspan='{span}'>소관위</th>")
        # 하위 헤더
        for sub in ["소관위상정일","소관위처리일","소관위처리결과"]:
            if sub in show.columns:
                ordered.append(sub)
                header_row2.append(f"<th>{sub.replace('소관위','')}</th>")
    else:
        # 그룹 없으면 개별 헤더로
        for sub in ["소관위상정일","소관위처리일","소관위처리결과"]:
            if sub in show.columns:
                ordered.append(sub)
                header_row1.append(f"<th rowspan='2'>{sub}</th>")

    if "상세보기" in show.columns:
        ordered.append("상세보기")
        header_row1.append("<th rowspan='2'>상세보기</th>")

    # tbody
    body_rows = []
    for _, r in show.iterrows():
        tds = []
        for c in ordered:
            v = r[c]
            if c == "법률안명":
                # 2줄 클램프 + 전체 툴팁
                safe = _html.escape(str(v), quote=False) if "<a " not in str(v) else str(v)
                tds.append(f'<td title="{_html.escape(str(v), quote=True)}"><div class="clamp2">{safe}</div></td>')
            else:
                # 링크 그대로 유지(상세보기) / 일반 텍스트는 escape
                if c == "상세보기" and isinstance(v, str) and v.startswith("<a "):
                    tds.append(f"<td>{v}</td>")
                else:
                    tds.append(f"<td>{_html.escape(str(v), quote=False)}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    # 최종 HTML
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

    # CSV 다운로드 (현재 보이는 열 기준)
    csv_bytes = show[ordered].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="⬇️ 이 표를 CSV로 다운로드",
        data=csv_bytes,
        file_name=f"bills_{title.replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )
