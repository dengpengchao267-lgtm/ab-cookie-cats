-- ===
-- 文件：03_ads.sql
-- 分层：ADS（应用数据层 / Application Data Store）
-- 作用：在 dwd_ab_user 之上产出三张"给分析结论/看板直接用"的结果表
--       1) ads_ab_overall            按 version 的整体实验汇总
--       2) ads_ab_activity_quartile  version × 活跃度四分位 的分群汇总
--       3) ads_ab_srm                SRM（样本比例失衡）检验统计量
-- 依赖：先执行 01_ods.sql、02_dwd.sql
-- 环境：MySQL 8.0（CTE + 窗口函数）
-- 约定：每张表都是先 DROP TABLE IF EXISTS 再 CREATE TABLE ... AS SELECT；
--       所有比率一律同时保留【分子】和【分母】两列，比率只是派生列，
--       这样口径可追溯、可复算，也方便下游重新汇总
-- 验证：已在 MySQL 8.0.46 上实测执行通过；三张表行数 3 / 8 / 1，
--       留存率（0.448188 / 0.190201 / 0.182000）与分群效应均与 Python 报告逐位一致；
--       详见 README 第八节「实测验证记录」
-- ===

SET SESSION sql_mode = '';
SET NAMES utf8mb4;

-- 除法精度：MySQL 默认 div_precision_increment = 4，
--    即【两个整数相除只保留 4 位小数】。本层全是"分子/分母"的比率计算，
--    若不调高，20034/44700 会被算成 0.4482 而不是 0.448188，
--    尽管列声明是 DECIMAL(10,6)，实际只有 4 位有效精度，
--    结果是看板上的留存率与 Python 报告对不上（差 0.0001 量级）。
--    提到 10 位后，比率可精确到小数点后 6 位以上。
SET SESSION div_precision_increment = 10;

USE ab_cookie_cats;

-- ===
-- 1. ads_ab_overall：按实验分组（version）的整体汇总
--    用户数 / 次日留存人数与比率 / 7日留存人数与比率 / 回合数的均值·中位数·95分位
--
--    口径说明：
--    - 中位数用 ROW_NUMBER() OVER (PARTITION BY version ORDER BY ...) 取中间行
--      （MySQL 没有 MEDIAN 函数），偶数样本时取"上中位数"（中间两行的较大者）。
--      本数据集两组实测结果与 pandas median() 一致（gate_30=17 / gate_40=16），
--      大样本下该近似一般不会产生可见差异。
--    - p95_gamerounds 用 PERCENT_RANK() 计算，不硬编码
--    - avg_rounds_capped 用缩尾列算，避免极值拉偏均值
--    - 最后 UNION ALL 一行 'TOTAL_ALL'，方便直接看合并口径（看板里可过滤掉）
-- ===
DROP TABLE IF EXISTS ads_ab_overall;

CREATE TABLE ads_ab_overall (
  version                  VARCHAR(16)   NOT NULL COMMENT '实验分组：gate_30/gate_40/TOTAL_ALL(全量合计行)',
  user_cnt                 BIGINT        NOT NULL DEFAULT 0 COMMENT '用户数（比率分母）',
  user_pct                 DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '该组用户占全量比例',
  retention_1_users        BIGINT        NOT NULL DEFAULT 0 COMMENT '次日留存人数（比率分子）',
  retention_1_rate         DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '次日留存率 = retention_1_users / user_cnt',
  retention_7_users        BIGINT        NOT NULL DEFAULT 0 COMMENT '7日留存人数（比率分子）',
  retention_7_rate         DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '7日留存率 = retention_7_users / user_cnt',
  avg_rounds               DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '回合数均值（未缩尾，受极值影响）',
  avg_rounds_capped        DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '回合数均值（95分位缩尾后，稳健口径）',
  median_rounds            BIGINT        NOT NULL DEFAULT 0 COMMENT '回合数中位数（上中位数）',
  p95_rounds               BIGINT        NOT NULL DEFAULT 0 COMMENT '回合数95分位',
  zero_round_users         BIGINT        NOT NULL DEFAULT 0 COMMENT '零活跃用户数（sum_gamerounds=0）',
  zero_round_rate          DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '零活跃用户占比 = zero_round_users / user_cnt'
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_general_ci
  COMMENT='ADS：Cookie Cats 实验整体汇总（按分组，另含 TOTAL_ALL 全量行）';

INSERT INTO ads_ab_overall
WITH base AS (
  SELECT
    d.version,
    d.userid,
    d.retention_1,
    d.retention_7,
    d.sum_gamerounds,
    d.gamerounds_capped,
    d.is_zero_round,
    COUNT(*)              OVER (PARTITION BY d.version)                                    AS cnt,
    ROW_NUMBER()          OVER (PARTITION BY d.version ORDER BY d.sum_gamerounds ASC, d.userid ASC) AS rn_asc,
    PERCENT_RANK()        OVER (PARTITION BY d.version ORDER BY d.sum_gamerounds ASC)       AS pr
  FROM dwd_ab_user d
),
agg AS (
  SELECT
    version,
    MAX(cnt)                                                                   AS user_cnt,
    SUM(retention_1)                                                           AS retention_1_users,
    SUM(retention_7)                                                           AS retention_7_users,
    SUM(sum_gamerounds)                                                        AS rounds_sum,
    SUM(gamerounds_capped)                                                     AS rounds_capped_sum,
    MAX(CASE WHEN rn_asc = FLOOR((cnt + 1) / 2) THEN sum_gamerounds END)       AS median_rounds,
    MAX(CASE WHEN pr <= 0.95 THEN sum_gamerounds END)                          AS p95_rounds,
    SUM(is_zero_round)                                                         AS zero_round_users
  FROM base
  GROUP BY version
),
labeled AS (
  SELECT
    version,
    user_cnt,
    retention_1_users,
    retention_7_users,
    rounds_sum,
    rounds_capped_sum,
    median_rounds,
    p95_rounds,
    zero_round_users,
    (SELECT SUM(user_cnt) FROM agg)                                            AS all_user_cnt
  FROM agg
  UNION ALL
  SELECT
    'TOTAL_ALL'                                                                AS version,
    (SELECT SUM(user_cnt) FROM agg)                                            AS user_cnt,
    (SELECT SUM(retention_1_users) FROM agg)                                   AS retention_1_users,
    (SELECT SUM(retention_7_users) FROM agg)                                   AS retention_7_users,
    (SELECT SUM(rounds_sum) FROM agg)                                          AS rounds_sum,
    (SELECT SUM(rounds_capped_sum) FROM agg)                                   AS rounds_capped_sum,
    -- TOTAL_ALL 行的中位数/P95 是"分组口径的近似汇总"：
    -- 中位数取两组中位数较大者，P95 取两组 P95 的平均。
    -- 若要全量精确中位数/P95，请直接对 dwd_ab_user 全表另算一条查询，
    -- 这里保留近似值只为让看板的合计行不出现空值。
    (SELECT MAX(median_rounds) FROM agg)                                       AS median_rounds,
    (SELECT ROUND(AVG(p95_rounds)) FROM agg)                                   AS p95_rounds,
    (SELECT SUM(zero_round_users) FROM agg)                                    AS zero_round_users,
    (SELECT SUM(user_cnt) FROM agg)                                            AS all_user_cnt
)
SELECT
  l.version,
  l.user_cnt,
  ROUND(l.user_cnt / NULLIF(l.all_user_cnt, 0), 6)                             AS user_pct,
  l.retention_1_users,
  ROUND(l.retention_1_users / NULLIF(l.user_cnt, 0), 6)                        AS retention_1_rate,
  l.retention_7_users,
  ROUND(l.retention_7_users / NULLIF(l.user_cnt, 0), 6)                        AS retention_7_rate,
  ROUND(l.rounds_sum / NULLIF(l.user_cnt, 0), 4)                               AS avg_rounds,
  ROUND(l.rounds_capped_sum / NULLIF(l.user_cnt, 0), 4)                        AS avg_rounds_capped,
  COALESCE(l.median_rounds, 0)                                                 AS median_rounds,
  COALESCE(l.p95_rounds, 0)                                                    AS p95_rounds,
  l.zero_round_users,
  ROUND(l.zero_round_users / NULLIF(l.user_cnt, 0), 6)                         AS zero_round_rate
FROM labeled l
ORDER BY (l.version = 'TOTAL_ALL'), l.version;   -- TOTAL_ALL 永远排最后

-- ===
-- 2. ads_ab_activity_quartile：version × activity_quartile 分群汇总
--    用途：看"门的位置改动"是否只影响某一段活跃度的人群（分群异质性分析）
--    重要提醒：activity_quartile 是全表统一分箱，两个分组在各分位内人数会略有差异，
--              所以跨分组对比时必须看 rate（分子/分母都存了，可以自己复算），
--              不要只看人数
-- ===
DROP TABLE IF EXISTS ads_ab_activity_quartile;

CREATE TABLE ads_ab_activity_quartile (
  version                  VARCHAR(16)   NOT NULL COMMENT '实验分组：gate_30=对照组，gate_40=实验组',
  activity_quartile        TINYINT       NOT NULL COMMENT '活跃度四分位：1=最低 ... 4=最高',
  activity_quartile_name   VARCHAR(32)   NOT NULL COMMENT '四分位可读标签',
  user_cnt                 BIGINT        NOT NULL DEFAULT 0 COMMENT '该分群用户数（各比率的分母）',
  retention_1_users        BIGINT        NOT NULL DEFAULT 0 COMMENT '次日留存人数（分子）',
  retention_1_rate         DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '次日留存率',
  retention_7_users        BIGINT        NOT NULL DEFAULT 0 COMMENT '7日留存人数（分子）',
  retention_7_rate         DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '7日留存率',
  zero_round_users         BIGINT        NOT NULL DEFAULT 0 COMMENT '零活跃用户数',
  avg_rounds               DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '该分群回合数均值（未缩尾）',
  avg_rounds_capped        DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '该分群回合数均值（缩尾后）',
  median_rounds            BIGINT        NOT NULL DEFAULT 0 COMMENT '该分群回合数中位数'
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_general_ci
  COMMENT='ADS：分组 × 活跃度四分位 的分群留存汇总（分群异质性分析用）';

INSERT INTO ads_ab_activity_quartile
WITH base AS (
  SELECT
    d.version,
    d.activity_quartile,
    d.activity_quartile_name,
    d.userid,
    d.retention_1,
    d.retention_7,
    d.sum_gamerounds,
    d.gamerounds_capped,
    d.is_zero_round,
    COUNT(*)       OVER (PARTITION BY d.version, d.activity_quartile)                                     AS cnt,
    ROW_NUMBER()   OVER (PARTITION BY d.version, d.activity_quartile
                         ORDER BY d.sum_gamerounds ASC, d.userid ASC)                                      AS rn_asc
  FROM dwd_ab_user d
)
SELECT
  version,
  activity_quartile,
  MAX(activity_quartile_name)                                                  AS activity_quartile_name,
  MAX(cnt)                                                                     AS user_cnt,
  SUM(retention_1)                                                             AS retention_1_users,
  ROUND(SUM(retention_1) / NULLIF(MAX(cnt), 0), 6)                             AS retention_1_rate,
  SUM(retention_7)                                                             AS retention_7_users,
  ROUND(SUM(retention_7) / NULLIF(MAX(cnt), 0), 6)                             AS retention_7_rate,
  SUM(is_zero_round)                                                           AS zero_round_users,
  ROUND(AVG(sum_gamerounds), 4)                                                AS avg_rounds,
  ROUND(AVG(gamerounds_capped), 4)                                             AS avg_rounds_capped,
  MAX(CASE WHEN rn_asc = FLOOR((cnt + 1) / 2) THEN sum_gamerounds END)          AS median_rounds
FROM base
GROUP BY version, activity_quartile
ORDER BY activity_quartile, version;

-- ===
-- 3. ads_ab_srm：SRM（Sample Ratio Mismatch，样本比例失衡）检验统计量
--    公式：卡方 = POWER(n1 - n2, 2) / (n1 + n2)，自由度 df = 1
--          （n1 = 对照组人数，n2 = 实验组人数；注意分子是【人数差】的平方，
--            不是占比差的平方——这是 1:1 分流下 df=1 的卡方简化式）
--    解释：分流比例正常时卡方应很小；卡方越大越可疑。
--    重要：MySQL 没有卡方分布函数，算不出 p 值，
--          p 值请在 Python 端用 scipy.stats.chi2.sf(chi_square, df=1) 计算，
--          再把结果回填/拼接给看板。本表只提供 MySQL 能算的部分。
-- ===
DROP TABLE IF EXISTS ads_ab_srm;

CREATE TABLE ads_ab_srm (
  metric_name              VARCHAR(64)   NOT NULL COMMENT '指标名，便于看板直接当维度',
  control_users            BIGINT        NOT NULL DEFAULT 0 COMMENT '对照组(n1)人数：gate_30',
  treatment_users          BIGINT        NOT NULL DEFAULT 0 COMMENT '实验组(n2)人数：gate_40',
  total_users              BIGINT        NOT NULL DEFAULT 0 COMMENT '总人数 n1+n2',
  control_pct              DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '对照组占比 n1/(n1+n2)',
  treatment_pct            DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '实验组占比 n2/(n1+n2)',
  expected_users_per_arm   DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '1:1 分流下的理论每组人数 (n1+n2)/2',
  observed_diff            BIGINT        NOT NULL DEFAULT 0 COMMENT '人数差 n1-n2（卡方分子用）',
  pct_diff                 DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '占比差 control_pct-treatment_pct',
  chi_square               DECIMAL(18,6) NOT NULL DEFAULT 0 COMMENT '卡方统计量 = POWER(n1-n2,2)/(n1+n2)',
  degrees_of_freedom       INT           NOT NULL DEFAULT 1 COMMENT '自由度，固定 1',
  p_value_note             VARCHAR(128)  NOT NULL DEFAULT '' COMMENT 'p 值说明：MySQL 无卡方分布函数，需 Python 端计算'
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_general_ci
  COMMENT='ADS：SRM 检验统计量（只含 MySQL 可计算部分，p 值由 Python 端计算）';

INSERT INTO ads_ab_srm
WITH srm_1 AS (
  SELECT
    SUM(is_control)                                                   AS n1,
    SUM(is_treatment)                                                 AS n2,
    SUM(is_control) + SUM(is_treatment)                               AS n_total
  FROM dwd_ab_user
)
SELECT
  'SRM_check_gate_30_vs_gate_40'                                      AS metric_name,
  s.n1                                                                AS control_users,
  s.n2                                                                AS treatment_users,
  s.n_total                                                           AS total_users,
  ROUND(s.n1 / NULLIF(s.n_total, 0), 6)                               AS control_pct,
  ROUND(s.n2 / NULLIF(s.n_total, 0), 6)                               AS treatment_pct,
  ROUND(s.n_total / 2.0, 4)                                           AS expected_users_per_arm,
  (s.n1 - s.n2)                                                       AS observed_diff,
  ROUND(s.n1 / NULLIF(s.n_total, 0) - s.n2 / NULLIF(s.n_total, 0), 6) AS pct_diff,
  ROUND(POWER(s.n1 - s.n2, 2) / NULLIF(s.n1 + s.n2, 0), 6)            AS chi_square,
  1                                                                   AS degrees_of_freedom,
  'p 值由 Python 端计算：scipy.stats.chi2.sf(chi_square, df=1)；MySQL 无卡方分布函数'
                                                                      AS p_value_note
FROM srm_1 s;

-- ===
-- 4. ADS 层质检与结果浏览
-- ===

-- 4.1 三张结果表行数（预期：overall=3，quartile=8，srm=1）
SELECT 'ads_ab_overall' AS table_name, COUNT(*) AS rows_cnt FROM ads_ab_overall
UNION ALL
SELECT 'ads_ab_activity_quartile', COUNT(*) FROM ads_ab_activity_quartile
UNION ALL
SELECT 'ads_ab_srm', COUNT(*) FROM ads_ab_srm;

-- 4.2 整体汇总预览（核心结论表）
SELECT
  version, user_cnt, user_pct,
  retention_1_users, retention_1_rate,
  retention_7_users, retention_7_rate,
  avg_rounds, avg_rounds_capped, median_rounds, p95_rounds,
  zero_round_users, zero_round_rate
FROM ads_ab_overall
ORDER BY (version = 'TOTAL_ALL'), version;

-- 4.3 分群汇总预览
SELECT
  version, activity_quartile, activity_quartile_name, user_cnt,
  retention_1_users, retention_1_rate, retention_7_users, retention_7_rate
FROM ads_ab_activity_quartile
ORDER BY activity_quartile, version;

-- 4.4 SRM 预览
SELECT * FROM ads_ab_srm;

-- 4.5 一致性断言：overall 各分组人数必须与 dwd 一致（预期 diff_rows = 0）
SELECT
  (SELECT SUM(user_cnt) FROM ads_ab_overall WHERE version IN ('gate_30','gate_40')) AS ads_rows,
  (SELECT COUNT(*) FROM dwd_ab_user)                                                AS dwd_rows,
  (SELECT SUM(user_cnt) FROM ads_ab_overall WHERE version IN ('gate_30','gate_40'))
    - (SELECT COUNT(*) FROM dwd_ab_user)                                            AS diff_rows;

-- 4.6 四分位表人数守恒（预期 diff_rows = 0）
SELECT
  (SELECT SUM(user_cnt) FROM ads_ab_activity_quartile) AS quartile_rows,
  (SELECT COUNT(*) FROM dwd_ab_user)                   AS dwd_rows,
  (SELECT SUM(user_cnt) FROM ads_ab_activity_quartile) - (SELECT COUNT(*) FROM dwd_ab_user) AS diff_rows;
