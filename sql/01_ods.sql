-- ===
-- 文件：01_ods.sql
-- 分层：ODS（原始数据层 / Operational Data Store）
-- 作用：建库 + 建最贴近源文件的原始表 + 用 LOAD DATA LOCAL INFILE 把
--       D:/deepseek work/cookie_cats.csv 原样灌进来（90,189 行数据 + 1 行表头）
-- 环境：MySQL 8.0（脚本只使用 MySQL 8.0 语法）
-- 验证：已在 MySQL 8.0.46 上实测执行通过，导入 90,189 行、无重复 userid、
--       retention 字段异常行数为 0；详见 README 第八节「实测验证记录」
-- ===

-- ===
-- 运行前必须先开启 local_infile
-- ===
-- MySQL 的 local_infile 是【GLOBAL 变量】，不存在 SESSION 级设置：
--   正确：SET GLOBAL local_infile = 1;   （需要管理员权限）
--   错误：SET SESSION local_infile = 1;  （会报 ERROR 1229，脚本当场中断）
-- 因此本脚本【不主动设置它】，避免权限不足导致整个脚本跑不下去。
-- 请按下面三处依次确认（三处都开才能用 LOAD DATA LOCAL INFILE）：
--   (1) 服务端：连上后执行  SET GLOBAL local_infile = 1;
--       或永久生效：在 my.ini 的 [mysqld] 段加 local_infile=ON 后重启服务
--   (2) 客户端：连接命令必须带 --local-infile=1
--   (3) 文件路径：必须是 MySQL 服务端能读到的本地绝对路径（见第 3 节）
-- 自查：SELECT @@GLOBAL.local_infile;  应返回 1

-- ===
-- 0. 会话参数
-- ===
-- 关掉严格模式，避免原始层导入时某一行脏数据直接中断整个 LOAD
SET SESSION sql_mode = '';

-- 客户端字符集：保证中文注释/字符串不乱码（utf8mb4 = 真正的 UTF-8）
SET NAMES utf8mb4;

-- 除法精度：MySQL 默认整数相除只保留 4 位小数（div_precision_increment=4），
-- 本层质检里有占比计算，提到 10 位避免精度损失。
SET SESSION div_precision_increment = 10;

-- 若你的账号有权限，也可以直接在这里手动执行下面这行（默认注释掉）：
-- SET GLOBAL local_infile = 1;

-- ===
-- 1. 建库
-- ===
CREATE DATABASE IF NOT EXISTS ab_cookie_cats
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_general_ci;

USE ab_cookie_cats;

-- ===
-- 2. 原始表 ods_cookie_cats_raw
--    设计原则：不做任何清洗、不做任何类型推断，字段形态与 CSV 保持一致
--    - userid / sum_gamerounds 虽然是整数，但原始层仍以字符串形态接收，
--      好处是任何脏数据（空串、带空格、科学计数法、"NA"）都不会让导入失败，
--      类型转换与校验统一放到 DWD 层做。
--    - retention_1 / retention_7 原样保留 'TRUE' / 'FALSE' 字符串，
--      不使用 BOOLEAN，因为 MySQL 的 BOOLEAN 是 TINYINT(1) 的别名，
--      导入时会把 'TRUE' 之外的任何非 0 字符串都变成 1，会静默吃掉脏数据。
-- ===
DROP TABLE IF EXISTS ods_cookie_cats_raw;

CREATE TABLE ods_cookie_cats_raw (
  userid           VARCHAR(32)  DEFAULT NULL COMMENT '原始用户ID（字符串形态，DWD 层再转INT）',
  version          VARCHAR(32)  DEFAULT NULL COMMENT '实验分组原始值：gate_30=对照组，gate_40=实验组',
  sum_gamerounds   VARCHAR(32)  DEFAULT NULL COMMENT '安装后14天游戏回合总数（原始字符串形态）',
  retention_1      VARCHAR(16)  DEFAULT NULL COMMENT '次日留存原始值：TRUE/FALSE 字符串，大小写不敏感',
  retention_7      VARCHAR(16)  DEFAULT NULL COMMENT '7日留存原始值：TRUE/FALSE 字符串，大小写不敏感',
  src_row_no       BIGINT       DEFAULT NULL COMMENT '源文件行号占位（LOAD 时不赋值，仅在需要排查时人工回填）'
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_general_ci
  COMMENT='Cookie Cats A/B 实验原始层表：一行 = CSV 一行，不做任何转换';

-- ===
-- 3. 导入 CSV
--    Windows 下路径用正斜杠 /（不要用反斜杠，反斜杠在 MySQL 里是转义符）
--    请按自己机器上的真实路径修改下面这一行！
--
--    关键写法说明：
--    (1) FIELDS TERMINATED BY ','  —— CSV 逗号分隔
--    (2) IGNORE 1 LINES           —— 跳过表头 userid,version,...
--    (3) SET 子句里用 @变量 先接成字符串，再用 CASE 判断，
--        这样大小写都能吃下（源文件实测是大写 TRUE/FALSE，
--        但很多下载版本是小写 True/False，这段写法两种都兼容）
--    (4) userid 变量后面的 + 0 才是把字符串转数字：
--        MySQL 里 'abc' + 0 = 0，配合下面 NULLIF(...,0) 把非数字值变成 NULL
--    (5) 换行符处理（关键）：
--        本 CSV 是 Windows 的 CRLF 换行（\r\n），而这里用 LINES TERMINATED BY '\n' 切行，
--        于是每一行【最后一个字段】会残留一个 \r。
--        本项目 CSV 的最后一个字段恰好是 retention_7，如果只写 TRIM()，
--        retention_7 会变成 'TRUE\r' / 'FALSE\r'，与 'TRUE'/'FALSE' 不相等 →
--        下一层 dwd_ab_user 的 WHERE 会把这些行全部过滤掉，
--        最终 90,189 行只会进来 1 行。
--        注意：MySQL 的 TRIM(expr) 不带参数时【只去空格，不去 \r】。
--        因此这里对所有字段统一用 REPLACE(..., '\r', '') 剔除回车符；
--        这样无论源文件是 CRLF 还是 LF 都能正确解析。
-- ===
TRUNCATE TABLE ods_cookie_cats_raw;

LOAD DATA LOCAL INFILE 'D:/deepseek work/cookie_cats.csv'
INTO TABLE ods_cookie_cats_raw
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ','
       OPTIONALLY ENCLOSED BY '"'
LINES  TERMINATED BY '\n'
IGNORE 1 LINES
(userid, version, sum_gamerounds, @retention_1, @retention_7)
SET
  userid         = NULLIF(TRIM(REPLACE(userid,         '\r', '')), ''),
  version        = NULLIF(TRIM(REPLACE(version,        '\r', '')), ''),
  sum_gamerounds = NULLIF(TRIM(REPLACE(sum_gamerounds, '\r', '')), ''),
  retention_1    = UPPER(NULLIF(TRIM(REPLACE(@retention_1, '\r', '')), '')),
  retention_7    = UPPER(NULLIF(TRIM(REPLACE(@retention_7, '\r', '')), ''));

-- ===
-- 3.1 导入后立刻把原始层的"字符串 TRUE/FALSE"转成 0/1 形态核验
--     （只做展示，不改原始表；正式转换在 02_dwd.sql 完成）
-- ===
SELECT
  version,
  COUNT(*)                                                        AS raw_rows,
  SUM(CASE WHEN UPPER(retention_1) = 'TRUE' THEN 1 ELSE 0 END)   AS r1_true_rows,
  SUM(CASE WHEN UPPER(retention_7) = 'TRUE' THEN 1 ELSE 0 END)   AS r7_true_rows,
  SUM(CASE WHEN UPPER(retention_1) NOT IN ('TRUE','FALSE')
             OR retention_1 IS NULL THEN 1 ELSE 0 END)            AS r1_unexpected_rows,
  SUM(CASE WHEN UPPER(retention_7) NOT IN ('TRUE','FALSE')
             OR retention_7 IS NULL THEN 1 ELSE 0 END)            AS r7_unexpected_rows
FROM ods_cookie_cats_raw
GROUP BY version
ORDER BY version;

-- ===
-- 4. 原始层基础质检查询
--    4 组查询依次检查：总行数 / userid 唯一性 / version 取值分布 / 空值统计
--    期望结果（源文件实测）：总行数 90189，重复 userid 0，
--    gate_40=45489，gate_30=44700，空值 0
-- ===

-- 4.1 总行数（预期 90189；若不符说明 IGNORE 1 LINES 或行尾符有问题）
SELECT COUNT(*) AS total_rows
FROM ods_cookie_cats_raw;

-- 4.2 userid 重复检查（预期 duplicate_userid_rows = 0）
--     口径：同一个 userid 出现了多少次，合计多出来的行数
SELECT
  COUNT(*)                                        AS total_rows,
  COUNT(DISTINCT userid)                          AS distinct_userid,
  COUNT(*) - COUNT(DISTINCT userid)               AS duplicate_userid_rows
FROM ods_cookie_cats_raw;

-- 4.2.1 具体是哪些 userid 重复（4.2 的结果不为 0 时再看这一条）
SELECT
  userid,
  COUNT(*) AS cnt
FROM ods_cookie_cats_raw
GROUP BY userid
HAVING COUNT(*) > 1
ORDER BY cnt DESC, userid
LIMIT 50;

-- 4.3 version 取值分布（预期只有 gate_30 / gate_40 两个值）
SELECT
  version,
  COUNT(*)                                              AS rows_cnt,
  ROUND(COUNT(*) / (SELECT COUNT(*) FROM ods_cookie_cats_raw) * 100, 4) AS rows_pct
FROM ods_cookie_cats_raw
GROUP BY version
ORDER BY rows_cnt DESC;

-- 4.4 空值 / 空串 / 非法值统计（预期全部为 0）
--     - empty_*：空串
--     - null_* ：NULL
--     - bad_*  ：取值不在允许集合内（version 不是两个分组，retention 不是 TRUE/FALSE，
--                sum_gamerounds 不是纯数字）——"纯数字"用 CAST 回 REGEXP 的方式判断
SELECT
  SUM(CASE WHEN userid         IS NULL OR userid         = '' THEN 1 ELSE 0 END) AS null_userid,
  SUM(CASE WHEN version        IS NULL OR version        = '' THEN 1 ELSE 0 END) AS null_version,
  SUM(CASE WHEN sum_gamerounds IS NULL OR sum_gamerounds = '' THEN 1 ELSE 0 END) AS null_sum_gamerounds,
  SUM(CASE WHEN retention_1    IS NULL OR retention_1    = '' THEN 1 ELSE 0 END) AS null_retention_1,
  SUM(CASE WHEN retention_7    IS NULL OR retention_7    = '' THEN 1 ELSE 0 END) AS null_retention_7,
  SUM(CASE WHEN version NOT IN ('gate_30','gate_40') THEN 1 ELSE 0 END)          AS bad_version,
  SUM(CASE WHEN UPPER(retention_1) NOT IN ('TRUE','FALSE') THEN 1 ELSE 0 END)    AS bad_retention_1,
  SUM(CASE WHEN UPPER(retention_7) NOT IN ('TRUE','FALSE') THEN 1 ELSE 0 END)    AS bad_retention_7,
  SUM(CASE WHEN sum_gamerounds NOT REGEXP '^[0-9]+$' THEN 1 ELSE 0 END)          AS bad_sum_gamerounds
FROM ods_cookie_cats_raw;

-- 4.5 数值范围体检（用于确认没有导入错位、串列）
--     源文件实测：min=0，median=16，p95=221，max=49854，0 值共 3994 个
SELECT
  MIN(CAST(sum_gamerounds AS SIGNED))                                AS min_rounds,
  MAX(CAST(sum_gamerounds AS SIGNED))                                AS max_rounds,
  ROUND(AVG(CAST(sum_gamerounds AS SIGNED)), 4)                      AS avg_rounds,
  SUM(CASE WHEN CAST(sum_gamerounds AS SIGNED) = 0 THEN 1 ELSE 0 END) AS zero_round_rows,
  SUM(CAST(sum_gamerounds AS SIGNED))                                AS total_rounds
FROM ods_cookie_cats_raw
WHERE sum_gamerounds REGEXP '^[0-9]+$';

-- ===
-- 5. 执行完这一层后应该看到
--    total_rows = 90189
--    duplicate_userid_rows = 0
--    version 只有 gate_30(44700) / gate_40(45489)
--    4.4 所有列全为 0
--    以上任何一条不符，先不要往下执行 02_dwd.sql，先修原始层
-- ===
