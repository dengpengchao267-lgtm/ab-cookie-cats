-- ===
-- 文件：02_dwd.sql
-- 分层：DWD（明细数据层 / Data Warehouse Detail）
-- 作用：把 ODS 的字符串原始表清洗成一行一个用户的实验明细表 dwd_ab_user
--       包含：去重、类型转换、布尔转 0/1、实验分组标记、活跃度分箱、缩尾
-- 依赖：先执行 01_ods.sql，且 ODS 层质检全部通过
-- 环境：MySQL 8.0（使用了 CTE、窗口函数 ROW_NUMBER / NTILE / PERCENT_RANK / COUNT OVER）
-- 验证：已在 MySQL 8.0.46 上实测执行通过，dwd_ab_user 得到 90,189 行（与 ODS 一致，
--       无丢行），p95 缩尾阈值 221 与 Python 端 pandas.quantile(0.95) 一致；
--       详见 README 第八节「实测验证记录」
-- ===

SET SESSION sql_mode = '';
SET NAMES utf8mb4;

USE ab_cookie_cats;

-- ===
-- 1. 建表 dwd_ab_user
--    字段口径说明（这是本层最重要的部分，ADS 层不再重复解释）：
--    userid              用户唯一 ID，主键（去重后保证唯一）
--    version             实验分组原始标签：gate_30 / gate_40
--    is_control          对照组标记：version='gate_30' 记 1，否则 0
--    is_treatment        实验组标记：version='gate_40' 记 1，否则 0
--                        （保留两个互斥 0/1 列，是为了让 ADS 层可以直接 SUM，
--                          不用到处写 CASE WHEN version='gate_40'）
--    sum_gamerounds      安装后 14 天游戏回合总数（原始值，可含极端值）
--    gamerounds_capped   缩尾后的回合数：LEAST(sum_gamerounds, p95)
--                        业务含义：p95 以上的长尾（少数超级肝用户 + 疑似脚本号）
--                        会严重拉偏均值，做均值对比时用这一列更稳健
--    p95_gamerounds      该行所属分位的 95 分位值（全表同一数值，冗余存列便于审计）
--    retention_1         次日留存：TRUE→1 / FALSE→0（TINYINT(1)）
--    retention_7         7 日留存：TRUE→1 / FALSE→0（TINYINT(1)）
--    is_zero_round       是否"装完一把没玩"：sum_gamerounds=0 记 1
--                        业务含义：D0 零活跃用户，是留存分析里必须单独看的一群
--    activity_quartile   活跃度四分位：NTILE(4) OVER (ORDER BY sum_gamerounds, ...)
--                        1=最不活跃，4=最活跃；注意 NTILE 是按行数均分，
--                        不是按回合数等距分箱，所以每箱人数几乎相等
--                        并列值的打破规则很重要：回合数在 5 / 16 / 51 上有大量并列，
--                        如果不指定 tiebreaker，边界并列值会被任意分箱（MySQL 不保证顺序），
--                        导致 SQL 与 pandas 的分群留存率对不上（实测 Q4 效应会差 0.03pp）。
--                        本数据集的 CSV 是按 userid 升序排列的，pandas 端
--                        rank(method='first') 因此等价于"按 userid 打破并列"，
--                        所以这里写 ORDER BY sum_gamerounds, userid —— 两层结果可逐位对账，
--                        同时保证分箱结果完全确定（可复现，不受物理存储顺序影响）。
--    activity_quartile_name 四分位的可读标签（Tableau 里直接当维度用）
--    src_dup_rank        去重时该行在 userid 组内的排序号，=1 即保留行（审计用）
--    dwd_created_at      本层生成时间戳
-- ===
DROP TABLE IF EXISTS dwd_ab_user;

CREATE TABLE dwd_ab_user (
  userid                 BIGINT       NOT NULL COMMENT '用户唯一ID（去重后主键）',
  version                VARCHAR(16)  NOT NULL COMMENT '实验分组：gate_30=对照组，gate_40=实验组',
  is_control             TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '对照组标记：gate_30=1，否则0',
  is_treatment           TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '实验组标记：gate_40=1，否则0',
  sum_gamerounds         BIGINT       NOT NULL DEFAULT 0 COMMENT '安装后14天游戏回合总数（未缩尾原值）',
  gamerounds_capped      BIGINT       NOT NULL DEFAULT 0 COMMENT '缩尾回合数=LEAST(原值, 全表95分位)，用于稳健均值',
  p95_gamerounds         BIGINT       NOT NULL DEFAULT 0 COMMENT '全表 sum_gamerounds 的95分位（审计列，全表同值）',
  retention_1            TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '次日留存：TRUE=1/FALSE=0',
  retention_7            TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '7日留存：TRUE=1/FALSE=0',
  is_zero_round          TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '零活跃标记：sum_gamerounds=0 记1',
  activity_quartile      TINYINT      NOT NULL DEFAULT 0 COMMENT '活跃度四分位：1=最低 ... 4=最高（按行数均分）',
  activity_quartile_name VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '四分位可读标签',
  src_dup_rank           BIGINT       NOT NULL DEFAULT 0 COMMENT '去重时组内排序号，=1 为保留行',
  dwd_created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本层生成时间'
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_general_ci
  COMMENT='Cookie Cats A/B 实验明细层：一行 = 一个去重后的用户';

-- ===
-- 2. 清洗 + 派生 + 写入
--    执行顺序（都在一条 INSERT ... WITH ... SELECT 里完成）：
--    rank_1  第一步：只做"去重 + 类型/布尔转换"，把 NOT REGEXP 的数字脏值剔除
--    stats_1 第二步：在全表范围内算 95 分位（用 COUNT(*) OVER () 取总行数，
--                    再用 PERCENT_RANK() 定位 0.95 分位，绝不硬编码 221）
--    q_2     第三步：NTILE(4) 分箱 + LEAST 缩尾 + 分组标记
-- ===
INSERT INTO dwd_ab_user (
  userid, version, is_control, is_treatment,
  sum_gamerounds, gamerounds_capped, p95_gamerounds,
  retention_1, retention_7, is_zero_round,
  activity_quartile, activity_quartile_name, src_dup_rank
)
WITH rank_1 AS (
  SELECT
    CAST(r.userid AS SIGNED)                                   AS userid,
    r.version                                                  AS version,
    CAST(r.sum_gamerounds AS SIGNED)                           AS sum_gamerounds,
    CASE WHEN UPPER(r.retention_1) = 'TRUE' THEN 1 ELSE 0 END AS retention_1,
    CASE WHEN UPPER(r.retention_7) = 'TRUE' THEN 1 ELSE 0 END AS retention_7,
    ROW_NUMBER() OVER (
      PARTITION BY CAST(r.userid AS SIGNED)
      ORDER BY
        CAST(r.sum_gamerounds AS SIGNED) DESC,  -- 同一 userid 多条时，保留回合数更大的那条
        r.version ASC                           -- 回合数也相同时，按 version 升序稳定排序
    )                                                          AS dup_rank
  FROM ods_cookie_cats_raw r
  WHERE r.userid         IS NOT NULL
    AND TRIM(r.userid)        REGEXP '^[0-9]+$'    -- 只保留合法数字 ID
    AND r.version             IN ('gate_30','gate_40')
    AND r.sum_gamerounds      REGEXP '^[0-9]+$'     -- 只保留合法非负回合数
    AND UPPER(r.retention_1) IN ('TRUE','FALSE')
    AND UPPER(r.retention_7) IN ('TRUE','FALSE')
),
stats_1 AS (
  SELECT
    MAX(CASE WHEN pr <= 0.95 THEN sum_gamerounds END) AS p95_gamerounds
  FROM (
    SELECT
      sum_gamerounds,
      PERCENT_RANK() OVER (ORDER BY sum_gamerounds) AS pr
    FROM rank_1
    WHERE dup_rank = 1
  ) t
),
q_2 AS (
  SELECT
    a.userid,
    a.version,
    a.sum_gamerounds,
    a.retention_1,
    a.retention_7,
    a.dup_rank,
    NTILE(4) OVER (
      ORDER BY a.sum_gamerounds,
               a.userid                        -- tiebreaker：与 pandas rank(method='first') 一致，且保证确定性
    )                                                          AS activity_quartile,
    COALESCE(s.p95_gamerounds, 0)                              AS p95_gamerounds
  FROM rank_1 a
  CROSS JOIN stats_1 s
  WHERE a.dup_rank = 1
)
SELECT
  q.userid,
  q.version,
  CASE WHEN q.version = 'gate_30' THEN 1 ELSE 0 END            AS is_control,
  CASE WHEN q.version = 'gate_40' THEN 1 ELSE 0 END            AS is_treatment,
  q.sum_gamerounds,
  LEAST(q.sum_gamerounds, q.p95_gamerounds)                    AS gamerounds_capped,
  q.p95_gamerounds,
  q.retention_1,
  q.retention_7,
  CASE WHEN q.sum_gamerounds = 0 THEN 1 ELSE 0 END             AS is_zero_round,
  q.activity_quartile,
  CASE q.activity_quartile
    WHEN 1 THEN 'Q1 最不活跃'
    WHEN 2 THEN 'Q2 较低活跃'
    WHEN 3 THEN 'Q3 较高活跃'
    WHEN 4 THEN 'Q4 最活跃'
    ELSE 'UNKNOWN'
  END                                                          AS activity_quartile_name,
  q.dup_rank
FROM q_2 q;

-- ===
-- 2.1 补主键（放在 INSERT 之后加，避免 LOAD 顺序影响；PRIMARY KEY 顺带保证唯一性）
--     若这一步报"Duplicate entry"，说明去重逻辑没生效，必须回头查
-- ===
ALTER TABLE dwd_ab_user
  ADD PRIMARY KEY (userid);

-- ===
-- 3. DWD 层质检
-- ===

-- 3.1 行数 & 分组人数（预期总行数 90189，gate_40=45489，gate_30=44700）
SELECT
  COUNT(*)                                                       AS dwd_rows,
  COUNT(DISTINCT userid)                                         AS distinct_userid,
  SUM(is_control)                                                AS control_rows,
  SUM(is_treatment)                                              AS treatment_rows,
  SUM(is_control) + SUM(is_treatment)                            AS tagged_rows,
  MIN(sum_gamerounds)                                            AS min_rounds,
  MAX(sum_gamerounds)                                            AS max_rounds
FROM dwd_ab_user;

-- 3.2 转换正确性对账：DWD 的留存人数必须与 ODS 里 TRUE 的条数完全一致
SELECT
  (SELECT SUM(retention_1) FROM dwd_ab_user)                     AS dwd_r1_true,
  (SELECT SUM(CASE WHEN UPPER(retention_1) = 'TRUE' THEN 1 ELSE 0 END)
     FROM ods_cookie_cats_raw)                                   AS ods_r1_true,
  (SELECT SUM(retention_7) FROM dwd_ab_user)                     AS dwd_r7_true,
  (SELECT SUM(CASE WHEN UPPER(retention_7) = 'TRUE' THEN 1 ELSE 0 END)
     FROM ods_cookie_cats_raw)                                   AS ods_r7_true;

-- 3.3 缩尾与分箱体检
--     预期：p95_gamerounds 是一个贴近 221 的整数（不硬编码，按数据算）
--           四个 activity_quartile 箱人数应几乎相等（90189/4 上下）
--           gamerounds_capped 的 max 必须等于 p95_gamerounds
--           zero_round 预期 3994
SELECT
  activity_quartile,
  activity_quartile_name,
  COUNT(*)                                                       AS rows_cnt,
  MIN(sum_gamerounds)                                            AS min_rounds,
  MAX(sum_gamerounds)                                            AS max_rounds,
  SUM(is_zero_round)                                             AS zero_round_rows
FROM dwd_ab_user
GROUP BY activity_quartile, activity_quartile_name
ORDER BY activity_quartile;

SELECT
  MAX(p95_gamerounds)                                            AS p95_gamerounds,
  MIN(p95_gamerounds)                                            AS p95_min_check,
  MAX(gamerounds_capped)                                         AS capped_max,
  SUM(is_zero_round)                                             AS zero_round_rows,
  SUM(CASE WHEN is_zero_round = 1 AND sum_gamerounds <> 0 THEN 1 ELSE 0 END) AS zero_flag_error
FROM dwd_ab_user;

-- 3.4 分组互斥性检查（预期 conflict_rows = 0；两个标记不能同时为 1）
SELECT
  SUM(CASE WHEN is_control = 1 AND is_treatment = 1 THEN 1 ELSE 0 END) AS conflict_rows,
  SUM(CASE WHEN is_control = 0 AND is_treatment = 0 THEN 1 ELSE 0 END) AS untagged_rows
FROM dwd_ab_user;

-- ===
-- 4. 备注
--    (1) 去重口径：如果 ODS 质检里 userid 重复为 0（本数据集实测就是 0），
--        那么保留的 90189 行与原始行数一致，不会丢数据。
--    (2) activity_quartile 用 NTILE(4) 按行数均分，0 回合用户会全部落在 Q1，
--        所以 Q1 的留存天然最低——做分群对比时要在两个分组内部对比 Q1 vs Q1，
--        不要跨分位比较（这一点在 03_ads.sql 里也是这样处理的）。
--    (3) 95 分位口径：PERCENT_RANK() 取"百分位 <= 0.95 的最大回合数"。
--        本数据集实测该写法与 pandas.quantile(0.95) 结果一致（都是 221），
--        因此缩尾阈值与 Python 报告完全对齐；
--        但在并列值更多的数据上两者可能差 1~2，届时建议以 Python 输出为准。
-- ===
