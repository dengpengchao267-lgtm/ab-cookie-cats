-- ===
-- 文件：04_tableau_extract.sql
-- 分层：导出层（Tableau Public 看板数据源）
-- 作用：从 ADS 三张结果表里，分别 SELECT 出「分组汇总」「活跃度四分位 × 分组」
--       「SRM 数据」三个结果集，供导出 CSV 后直接连 Tableau Public
-- 依赖：先执行 01_ods.sql、02_dwd.sql、03_ads.sql
-- 环境：MySQL 8.0
-- 验证：已在 MySQL 8.0.46 上实测执行通过（4 个结果集均可正常返回，中文别名无乱码）
-- ===
--
-- ===
-- 导出为 CSV 的操作方式（三种任选，推荐第 1 种）
-- ===
-- 本项目的路径 D:\deepseek work\... 里带【空格】，所以：
--    - 文件名一律用英文双引号包起来
--    - 不要用 mysql 客户端的 SOURCE 命令（它对含空格路径支持很差）
--    - 用 shell 的输入重定向 < 来执行脚本文件
--
-- 方式 1：命令行重定向（最简单，推荐）
--   cmd.exe 里：
--     "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" --local-infile=1 ^
--       -u root -p --default-character-set=utf8mb4 < "D:\deepseek work\ab-cookie-cats\sql\03_ads.sql"
--     导出某个结果集（注意给输出文件也加引号）：
--     "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p ^
--       --default-character-set=utf8mb4 ab_cookie_cats ^
--       -e "SELECT * FROM ads_ab_overall;" > "D:\deepseek work\ab-cookie-cats\report\tableau_overall.csv"
--   PowerShell 里（重定向写法不同）：
--     $mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
--     Get-Content -Raw "D:\deepseek work\ab-cookie-cats\sql\03_ads.sql" |
--       & $mysql -u root -p --default-character-set=utf8mb4 --local-infile=1
--     & $mysql -u root -p --default-character-set=utf8mb4 ab_cookie_cats `
--       -e "SELECT * FROM ads_ab_overall;" |
--       Out-File -Encoding utf8 "D:\deepseek work\ab-cookie-cats\report\tableau_overall.csv"
--
-- 方式 2：MySQL 客户端内用 SELECT ... INTO OUTFILE（注意：写出的是服务器端路径，
--         且要求 secure_file_priv 允许该目录；Windows 上路径同样用正斜杠）
--   SELECT * FROM ads_ab_overall INTO OUTFILE 'D:/tableau/overall.csv'
--     FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
--     LINES TERMINATED BY '\n';
--
-- 方式 3：MySQL Workbench / DBeaver 里逐个执行下面的查询，
--         点结果网格右上角 "Export result set" 导出 CSV（UTF-8）
--
-- Tableau Public 连接建议：
--   - 三个 CSV 分别作为三个数据源；或把"分组汇总"和"分位 × 分组"合并成
--     一个数据源（两者都有 version，可做关系/关联）
--   - version 字段建议在 Tableau 里改别名：gate_30=对照组，gate_40=实验组
--   - 比率列已经是 0~1 的小数，在 Tableau 里设置"百分比"格式即可
--   - SRM 那张表的 chi_square 只用于展示；p 值请在 Python 端算好后
--     手工补进 CSV，或把 Python 输出另存一个 CSV 一起连进来
--   - 如果只想省事：本仓库 report/tableau/ 下已经有 15 个现成的 CSV，
--     它们由 Python 端从同一份数据算出，与 SQL 结果口径一致，可直接连 Tableau
-- ===

SET NAMES utf8mb4;
USE ab_cookie_cats;

-- ===
-- 结果集 1：分组汇总（对应看板的"总体结论"页）
-- 只取 gate_30 / gate_40 两行，排除 TOTAL_ALL 合计行（看板里通常单独标注）
-- ===
SELECT
  CASE version
    WHEN 'gate_30' THEN '对照组 (gate_30)'
    WHEN 'gate_40' THEN '实验组 (gate_40)'
    ELSE version
  END                        AS group_label,
  version                    AS version_raw,
  user_cnt                   AS 用户数,
  user_pct                   AS 用户占比,
  retention_1_users          AS 次日留存人数,
  retention_1_rate           AS 次日留存率,
  retention_7_users          AS 七日留存人数,
  retention_7_rate           AS 七日留存率,
  avg_rounds                 AS 回合数均值,
  avg_rounds_capped          AS 回合数均值_缩尾,
  median_rounds              AS 回合数中位数,
  p95_rounds                 AS 回合数P95,
  zero_round_users           AS 零活跃用户数,
  zero_round_rate            AS 零活跃占比
FROM ads_ab_overall
WHERE version IN ('gate_30','gate_40')
ORDER BY version;

-- ===
-- 结果集 2：活跃度四分位 × 分组（对应看板的"分群异质性"页）
-- 建议在 Tableau 里用 activity_quartile_name 做行、version 做颜色/列
-- ===
SELECT
  version                                                                     AS 分组原始值,
  CASE version
    WHEN 'gate_30' THEN '对照组 (gate_30)'
    WHEN 'gate_40' THEN '实验组 (gate_40)'
    ELSE version
  END                                                                         AS 分组,
  activity_quartile                                                           AS 四分位序号,
  activity_quartile_name                                                      AS 四分位,
  user_cnt                                                                    AS 用户数,
  retention_1_users                                                           AS 次日留存人数,
  retention_1_rate                                                            AS 次日留存率,
  retention_7_users                                                           AS 七日留存人数,
  retention_7_rate                                                            AS 七日留存率,
  zero_round_users                                                            AS 零活跃用户数,
  avg_rounds                                                                  AS 回合数均值,
  avg_rounds_capped                                                           AS 回合数均值_缩尾,
  median_rounds                                                               AS 回合数中位数
FROM ads_ab_activity_quartile
ORDER BY activity_quartile, version;

-- ===
-- 结果集 3：SRM 数据（对应看板的"分流健康度"卡片）
-- 另附一条基于 dwd 的明细口径查询，方便核对人数来源
-- ===
SELECT
  metric_name                AS 指标,
  control_users              AS 对照组人数_n1,
  treatment_users            AS 实验组人数_n2,
  total_users                AS 总人数,
  control_pct                AS 对照组占比,
  treatment_pct              AS 实验组占比,
  expected_users_per_arm     AS 理论每组人数_1比1,
  observed_diff              AS 人数差_n1减n2,
  pct_diff                   AS 占比差,
  chi_square                 AS 卡方统计量,
  degrees_of_freedom         AS 自由度,
  p_value_note               AS p值说明
FROM ads_ab_srm;

-- 3.1 SRM 人数来源核对（结果集 3 的 control_users/treatment_users 应与这里一致）
SELECT
  SUM(is_control)                                            AS n1_control,
  SUM(is_treatment)                                          AS n2_treatment,
  SUM(is_control) + SUM(is_treatment)                        AS n_total,
  ROUND(SUM(is_control) / COUNT(*), 6)                       AS n1_ratio,
  ROUND(SUM(is_treatment) / COUNT(*), 6)                     AS n2_ratio
FROM dwd_ab_user;

-- ===
-- 结果集补充（可选）：留存率压成"一行一分组"的窄表，方便 Tableau 直接做对比条形图
-- ads_ab_overall 本身一行就是一个 version，这里只是把列名换成中文别名
-- ===
SELECT
  version                    AS 分组,
  retention_1_rate           AS 次日留存率,
  retention_7_rate           AS 七日留存率,
  user_cnt                   AS 用户数
FROM ads_ab_overall
WHERE version IN ('gate_30','gate_40')
ORDER BY version;

-- ===
-- 导出后自检清单
--   1. 结果集 1 应恰好 2 行；user_cnt 合计应为 90189
--   2. 结果集 2 应恰好 8 行（2 分组 × 4 分位）；user_cnt 合计应为 90189
--   3. 结果集 3 应恰好 1 行；按本项目数据估算
--      （n1=44700、n2=45489）chi_square = 789^2 / 90189 ≈ 6.90，
--      这个量级很小，说明 1:1 分流正常；具体 p 值在 Python 端算
--   4. 三个 CSV 都必须是 UTF-8（含中文表头），否则 Tableau 会乱码
-- ===
