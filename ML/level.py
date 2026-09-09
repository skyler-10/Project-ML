import pandas as pd
import numpy as np

# ================== 配置区：检查这些列名与 Excel 是否一致 ==================
excel_in = "waterdata.xlsx"                        # 输入文件
excel_out = "waterdata_ready_for_ml_weighted.xlsx" # 输出文件

id_col = "id"                                      # 没有 id 也没关系
temp_col = "Temperatur(degrees C)"                 # 水温
sal_col = "Salinity"                               # 盐度
do_umol_col = "Oxygen(umol/kg)"                    # 溶解氧 μmol/kg
ph_col = "pH"                                      # pH

do_mgL_col = "DO_mgL"                              # 派生列：DO mg/L
ql_col = "QualityLevel"                            # 最终等级（0~3）
score_col = "QualityScore"                         # 可选：保留综合分数（0~100）

numeric_cols = [temp_col, sal_col, do_umol_col, ph_col]

# 权重配置（总和最好为 1.0）
w_DO = 0.35
w_pH = 0.3
w_T  = 0.2
w_S  = 0.15
# ======================================================================


def impute_numeric_with_median(df: pd.DataFrame, cols, by_id: bool = False) -> pd.DataFrame:
    """
    用中位数填充数值列缺失值。
    - by_id=True 且存在 id_col 时，按 id 分组中位数填补（组内全缺用全局中位数兜底）
    - 否则，全局中位数填补
    """
    df = df.copy()
    global_medians = {col: df[col].median(skipna=True) for col in cols}

    if by_id and (id_col in df.columns):
        def fill_group(group: pd.DataFrame) -> pd.DataFrame:
            for col in cols:
                m = group[col].median(skipna=True)
                if np.isnan(m):
                    m = global_medians[col]
                group[col] = group[col].fillna(m)
            return group

        df = df.groupby(id_col, group_keys=False).apply(fill_group)
    else:
        for col in cols:
            df[col] = df[col].fillna(global_medians[col])

    return df


def level_to_score(level: int) -> float:
    """
    单项等级(0~3)映射到分数（0~100）：
      3 -> 100
      2 -> 80
      1 -> 60
      0 -> 0
    """
    if level >= 3:
        return 100.0
    elif level == 2:
        return 80.0
    elif level == 1:
        return 60.0
    else:
        return 0.0


def calc_levels_for_row(row):
    """
    仅负责计算单项等级：do_level, ph_level, temp_level, sal_level
    不做综合等级（综合等级在 calc_quality_level_weighted 里完成）
    """
    T = row[temp_col]
    S = row[sal_col]
    DO = row[do_mgL_col]   # mg/L
    pH = row[ph_col]

    # 防御：如果仍有 NaN，就返回全 0
    if pd.isna(T) or pd.isna(S) or pd.isna(DO) or pd.isna(pH):
        return 0, 0, 0, 0

    # 1) DO 单项等级（先不做一票否决，这里只是单项评价）
    if DO >= 5.0:
        do_level = 3
    elif DO >= 4.0:
        do_level = 2
    elif DO >= 3.0:
        do_level = 1
    else:
        do_level = 0

    # 2) pH 单项等级
    if 7.8 <= pH <= 8.5:
        ph_level = 3
    elif 7.0 <= pH < 7.8:
        ph_level = 2
    elif (6.0 <= pH < 7.0) or (8.5 < pH <= 9.0):
        ph_level = 1
    else:
        ph_level = 0

    # 3) 温度单项等级（大黄鱼）
    if 18.0 <= T <= 24.0:
        temp_level = 3
    elif (15.0 <= T < 18.0) or (24.0 < T <= 28.0):
        temp_level = 2
    elif (10.0 <= T < 15.0) or (28.0 < T <= 30.0):
        temp_level = 1
    else:
        temp_level = 0

    # 4) 盐度单项等级（大黄鱼）
    if 24.0 <= S <= 32.0:
        sal_level = 3
    elif (20.0 <= S < 24.0) or (32.0 < S <= 35.0):
        sal_level = 2
    elif (15.0 <= S < 20.0) or (35.0 < S <= 38.0):
        sal_level = 1
    else:
        sal_level = 0

    return do_level, ph_level, temp_level, sal_level


def calc_quality_level_weighted(row) -> int:
    """
    加权评分版综合评价：
      1) 若 DO < 3 mg/L -> 0（硬性安全底线）
      2) 否则，将单项等级映射为分数，按权重做加权平均
      3) 得到 score (0~100)，再映射为 0~3 级：
         - score >= 90       -> 3
         - 70 <= score < 90  -> 2
         - 50 <= score < 70  -> 1
         - else              -> 0
      同时保留 score 到列 score_col 中（由主流程统一写入）
    """
    T = row[temp_col]
    S = row[sal_col]
    DO = row[do_mgL_col]
    pH = row[ph_col]

    # 缺失直接认为 0 分 / 0 级（训练时可以考虑是否丢弃这些样本）
    if pd.isna(T) or pd.isna(S) or pd.isna(DO) or pd.isna(pH):
        row[score_col] = 0.0
        return 0

    # 1) 一票否决：DO < 3 -> 0
    if DO < 3.0:
        row[score_col] = 0.0
        return 0

    # 2) 计算单项等级
    do_level, ph_level, temp_level, sal_level = calc_levels_for_row(row)

    # 3) 单项映射到分数
    do_score   = level_to_score(do_level)
    ph_score   = level_to_score(ph_level)
    temp_score = level_to_score(temp_level)
    sal_score  = level_to_score(sal_level)

    # 4) 加权平均得综合得分
    score = (do_score * w_DO +
             ph_score * w_pH +
             temp_score * w_T +
             sal_score * w_S)

    # 把 score 暂存在 row 对象里（主流程会统一写入 DataFrame）
    row[score_col] = score

    # 5) 综合得分映射回 0~3 级
    if score >= 95.0:
        return 3
    elif score >= 70.0:
        return 2
    elif score >= 50.0:
        return 1
    else:
        return 0


def main():
    print(f"读取文件: {excel_in}")
    df = pd.read_excel(excel_in)

    # 1) 缺失值填补：用全局中位数（适合机器学习特征预处理）
    df = impute_numeric_with_median(df, numeric_cols, by_id=False)

    # 2) Oxygen(umol/kg) -> DO_mgL
    df[do_mgL_col] = df[do_umol_col] * 0.032

    # 3) 计算加权综合等级和分数
    #    这里用 apply 时，需要支持在函数里写入 score_col。
    #    一个简洁做法：先创建 score 列，再单独 apply 计算等级。
    df[score_col] = np.nan

    def _apply_calc(row):
        level = calc_quality_level_weighted(row)
        return level

    df[ql_col] = df.apply(_apply_calc, axis=1)

    # 4) 输出结果
    df.to_excel(excel_out, index=False)
    print(f"完成：已生成带 {do_mgL_col}, {score_col}, {ql_col} 的文件 -> {excel_out}")


if __name__ == "__main__":
    main()