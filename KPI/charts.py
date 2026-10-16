"""Altair chart builders for dashboard KPI comparisons."""
from __future__ import annotations

import altair as alt
import pandas as pd

def daily_kpi_combo_chart(frame: pd.DataFrame) -> alt.LayerChart:
    """Build a date-only combo chart with aligned labels, visible lines, and clear KPI legends."""
    required = {"data_date", "province_name", "mtcl_2026", "tg_mllltb"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Thiếu cột biểu đồ KPI ngày: {sorted(missing)}")

    data = frame[list(required)].copy()
    data["data_date"] = pd.to_datetime(data["data_date"], errors="coerce").dt.date
    for metric in ("mtcl_2026", "tg_mllltb"):
        data[metric] = pd.to_numeric(data[metric], errors="coerce")
        
    # Làm sạch, nhóm dữ liệu và sắp xếp sẵn theo tỉnh và ngày để line nối đúng thứ tự
    data = (
        data.groupby(["data_date", "province_name"], as_index=False, dropna=False)
        .agg(mtcl_2026=("mtcl_2026", "mean"), tg_mllltb=("tg_mllltb", "mean"))
        .dropna(subset=["data_date"])
        .sort_values(["province_name", "data_date"])
    )
    if data.empty:
        raise ValueError("Không có ngày hợp lệ để vẽ biểu đồ KPI")

    # Tạo cột chuỗi ngày để cố định trục X danh mục, giúp nhãn và offset khớp hoàn toàn
    data["date_str"] = data["data_date"].astype(str)

    x_axis = alt.X(
        "date_str:N",
        title="Ngày",
        axis=alt.Axis(labelAngle=-45, labelOverlap=False),
    )
    
    tooltip = [
        alt.Tooltip("date_str:N", title="Ngày"),
        alt.Tooltip("province_name:N", title="Tỉnh"),
        alt.Tooltip("mtcl_2026:Q", title="MTCL_2026", format=".2f"),
        alt.Tooltip("tg_mllltb:Q", title="TG-MLLTB", format=".2f"),
    ]
    
    # 1. Biểu đồ cột (KPI: MTCL_2026)
    bars = alt.Chart(data).mark_bar(opacity=0.7).encode(
        x=x_axis,
        xOffset=alt.XOffset("province_name:N"),
        y=alt.Y(
            "mtcl_2026:Q", 
            title="MTCL_2026", 
            axis=alt.Axis(orient="left", titleAngle=0, titleAlign="right")
        ),
        color=alt.Color(
            "province_name:N",
            title="Tỉnh (MTCL_2026)",
            scale=alt.Scale(scheme="set3"),
        ),
        tooltip=tooltip,
    )
    
    # 2. Biểu đồ line (KPI: TG-MLLTB)
    line = alt.Chart(data).mark_line(
        point=alt.OverlayMarkDef(filled=True, size=60), 
        strokeWidth=3
    ).encode(
        x=x_axis,
        xOffset=alt.XOffset("province_name:N"),
        y=alt.Y(
            "tg_mllltb:Q", 
            title="TG-MLLTB", 
            axis=alt.Axis(orient="right", titleAngle=0, titleAlign="left")
        ),
        color=alt.Color(
            "province_name:N",
            title="Tỉnh (TG-MLLTB)",
            scale=alt.Scale(scheme="dark2"),
        ),
        detail="province_name:N",
        order="date_str:N",
        tooltip=tooltip,
    )
    
    return (
        alt.layer(bars, line)
        .resolve_scale(y="independent", color="independent")
        .properties(height=360)
    )
def monthly_kpi_comparison_chart(frame: pd.DataFrame, metric: str) -> alt.Chart:
    """Build grouped monthly bars for the province and every employee."""
    if metric not in {"mtcl_2026", "tg_mllltb"}:
        raise ValueError(f"Không hỗ trợ biểu đồ KPI {metric!r}")
    required = {"scope_label", "scope_type", metric}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Thiếu cột biểu đồ tháng: {sorted(missing)}")
    data = frame[list(required)].copy()
    data[metric] = pd.to_numeric(data[metric], errors="coerce")
    data = data.dropna(subset=["scope_label", metric])
    if data.empty:
        raise ValueError("Không có dữ liệu KPI để vẽ biểu đồ tháng")

    label = "MTCL_2026" if metric == "mtcl_2026" else "TG-MLLTB"
    return (
        alt.Chart(data)
        .mark_bar()
        .encode(
            x=alt.X(
                "scope_label:N",
                title="Tỉnh / chuyên viên",
                sort=None,
                axis=alt.Axis(labelAngle=-45, labelLimit=240),
            ),
            y=alt.Y(f"{metric}:Q", title=label),
            color=alt.Color(
                "scope_type:N",
                title="Đối tượng",
                scale=alt.Scale(
                    domain=["province", "employee"],
                    range=["#F28E2B", "#4E79A7"],
                ),
                legend=alt.Legend(
                    labelExpr="datum.label === 'province' ? 'Tỉnh' : 'Chuyên viên'"
                ),
            ),
            tooltip=[
                alt.Tooltip("scope_label:N", title="Đối tượng"),
                alt.Tooltip(f"{metric}:Q", title=label, format=".2f"),
            ],
        )
        .properties(height=380)
    )
