# Cookie Cats A/B 实验 —— MySQL 数仓 SQL 层

本目录是《Cookie Cats》移动游戏 A/B 实验项目的 **MySQL 8.0 数仓 SQL 层**，
负责把原始的 `cookie_cats.csv` 逐层加工成可直接用于分析结论和 Tableau 看板的结果表。

> **执行状态：本目录脚本已在 MySQL 8.0.46 上实测跑通。**
> 验证用的是 `mysqld --initialize-insecure` 建的临时实例（独立数据目录 + 3307 端口），
> 全程未触碰机器上原有的 MySQL 服务，验证结束后实例与数据目录已删除。
> 4 个脚本全部 `EXIT=0`，关键结果与 Python 报告**逐位对账一致**，详见第八节「实测验证记录」。
>
> 验证过程中修复了 3 个真实缺陷（CRLF 解析、`local_infile` 作用域、整数除法精度），
> 见第九节「已修复的 3 个坑」——这三个都藏得很深，**建议执行前先读一遍**。
>
> 数据核对：`ods_cookie_cats_raw` 与 `dwd_ab_user` 行数均应为 **90189**（CSV 共 90190 行，含 1 行表头）。

---

## 一、文件清单与执行顺序

| 顺序 | 文件 | 分层 | 产出 |
| --- | --- | --- | --- |
| 1 | `01_ods.sql` | ODS 原始层 | 库 `ab_cookie_cats`、表 `ods_cookie_cats_raw`、原始层质检 |
| 2 | `02_dwd.sql` | DWD 明细层 | 表 `dwd_ab_user`（一行一个用户 + 派生字段） |
| 3 | `03_ads.sql` | ADS 应用层 | 表 `ads_ab_overall`、`ads_ab_activity_quartile`、`ads_ab_srm` |
| 4 | `04_tableau_extract.sql` | 导出层 | 3 个 SELECT 结果集，导出 CSV 给 Tableau Public |

执行顺序不能颠倒：`02` 依赖 `01` 的原始表，`03` 依赖 `02` 的明细表，`04` 依赖 `03` 的结果表。

---

## 二、分层结构说明（ods → dwd → ads）

### ODS 原始层（`01_ods.sql`）
- **职责**：原样落地源数据，不做任何清洗、不做类型推断。
- **设计取舍**：`userid`、`sum_gamerounds` 也先以 `VARCHAR` 接进来，
  `retention_1/7` 保留 `TRUE/FALSE` 字符串。
  好处是脏数据（空串、带空格、`NA`、科学计数法）不会让 `LOAD DATA` 整批失败，
  所有类型转换与合法性校验集中在 DWD 层统一处理；代价是原始层不能直接参与计算。
- **顺带产出质检**：总行数、`userid` 重复、`version` 取值分布、空值/非法值、数值范围体检。

### DWD 明细层（`02_dwd.sql`）
- **职责**：清洗 + 去重 + 类型转换 + 派生实验分析必需的字段。
- 表 `dwd_ab_user` 一行 = 一个用户（`userid` 为主键）。
- 关键派生字段（建表注释里有逐列业务含义）：
  - `is_control` / `is_treatment`：`gate_30` / `gate_40` 的 0/1 标记，让 ADS 层直接 `SUM`；
  - `retention_1` / `retention_7`：`TINYINT(1)` 的 0/1；
  - `is_zero_round`：装完一局没玩的用户（本数据集 3994 人）；
  - `activity_quartile`：`NTILE(4) OVER (ORDER BY sum_gamerounds, userid)` 的 1–4 活跃度分箱。
    **tiebreaker（`, userid`）必须写**：回合数在 5 / 16 / 51 上有大量并列，
    不指定顺序时 MySQL 不保证分箱结果，实测会让 Q4 的 7 日留存效应从 −2.539pp 偏到 −2.571pp，
    与 pandas 对不上；
  - `gamerounds_capped`：`LEAST(sum_gamerounds, p95)` 缩尾值，`p95` 用
    `PERCENT_RANK()` 窗口函数现算（**没有硬编码 221**）。
- **去重口径**：`ROW_NUMBER() OVER (PARTITION BY userid ORDER BY sum_gamerounds DESC, version)`，
  保留 `dup_rank = 1`。本数据集 `userid` 实测无重复，因此不会丢行。

### ADS 应用层（`03_ads.sql`）
- **职责**：面向结论/看板做聚合，一行就是一个可展示的指标组合。
- 三张表都遵循两条约定：
  1. 先 `DROP TABLE IF EXISTS` 再 `CREATE TABLE ... AS SELECT`；
  2. **所有比率同时保留分子和分母列**（如 `retention_7_users` 与 `retention_7_rate`），
     比率只是派生列，口径可追溯、可复算。
- `ads_ab_overall`：按 `version` 汇总，末尾附一行 `TOTAL_ALL` 全量合计行。
- `ads_ab_activity_quartile`：`version × activity_quartile` 分群汇总（分群异质性分析）。
- `ads_ab_srm`：SRM 检验统计量，卡方 `POWER(n1-n2,2)/(n1+n2)`，`df = 1`。
  **MySQL 没有卡方分布函数，因此 p 值必须在 Python 端计算**：
  `scipy.stats.chi2.sf(chi_square, df=1)`（表中 `p_value_note` 列写明了这一点）。

---

## 三、前置条件

1. **MySQL 8.0**（脚本使用了 CTE 与窗口函数；MySQL 5.7 无法执行 `02`/`03`）。
2. **服务端开启 `local_infile`**（否则 `LOAD DATA LOCAL INFILE` 报
   `ERROR 3948 (42000): Loading local data is disabled`）：
   ```sql
   SET GLOBAL local_infile = 1;
   -- 确认：
   SHOW VARIABLES LIKE 'local_infile';
   ```
3. **命令行客户端必须加 `--local-infile=1`**：
   `mysql` 客户端从 8.0 起默认关闭本地文件读取，服务端开了、客户端没开一样会报错。
4. **CSV 文件路径**：本脚本按下面的位置写死，**请改成你自己机器上的真实路径**：
   ```
   D:/deepseek work/cookie_cats.csv
   ```
   - Windows 下 `LOAD DATA` 的路径 **必须用正斜杠 `/`**，
     反斜杠 `\` 在 MySQL 字符串里是转义符，会直接报错或找不到文件；
   - 路径含空格不需要引号外的特殊处理，但要保证整个路径被单引号包住；
   - 如果 CSV 放在别处（例如项目内的 `data/cookie_cats.csv`），
     请修改 `01_ods.sql` 里 `LOAD DATA LOCAL INFILE` 那一行。

---

## 四、执行命令示例

以下命令在项目根目录 `D:\deepseek work\ab-cookie-cats\sql\` 下执行（cmd 或 PowerShell 均可）：

```bat
:: 0) 确认 local_infile 已开（服务端）
mysql -u root -p -e "SET GLOBAL local_infile = 1; SHOW VARIABLES LIKE 'local_infile';"

:: 1) 建库 + 建原始表 + 导入 CSV + 原始层质检
mysql --local-infile=1 -u root -p --default-character-set=utf8mb4 < 01_ods.sql

:: 2) 明细层：清洗、去重、派生字段
mysql -u root -p --default-character-set=utf8mb4 < 02_dwd.sql

:: 3) 应用层：三张结果表
mysql -u root -p --default-character-set=utf8mb4 < 03_ads.sql

:: 4) 导出给 Tableau 的三个结果集（见 04 文件头部注释里的三种导出方式）
mysql -u root -p --default-character-set=utf8mb4 ab_cookie_cats ^
      -e "SELECT * FROM ads_ab_overall;" > ..\report\tableau_overall.csv
```

PowerShell 提示：`<` 输入重定向在 PowerShell 里不支持，请改成
`Get-Content .\01_ods.sql -Raw | mysql --local-infile=1 -u root -p`，
或直接用 `cmd /c "mysql ... < 01_ods.sql"`。

MySQL 客户端内执行也可以：
```sql
SOURCE D:/deepseek work/ab-cookie-cats/sql/01_ods.sql;
SOURCE D:/deepseek work/ab-cookie-cats/sql/02_dwd.sql;
SOURCE D:/deepseek work/ab-cookie-cats/sql/03_ads.sql;
SOURCE D:/deepseek work/ab-cookie-cats/sql/04_tableau_extract.sql;
```

---

## 五、执行后的核对点

**`01_ods.sql` 之后**
- `SELECT COUNT(*) FROM ods_cookie_cats_raw;` → **90189**
- `COUNT(*) - COUNT(DISTINCT userid)` → **0**（无重复）
- `version` 分布 → `gate_40 = 45489`、`gate_30 = 44700`
- 空值/非法值查询的全部列 → **0**
- 数值体检 → `min=0`、`max=49854`、`zero_round_rows=3994`

**`02_dwd.sql` 之后**
- `dwd_rows` → **90189**，`is_control = 44700`、`is_treatment = 45489`
- `dwd_r1_true` 必须等于 `ods_r1_true`（`retention_7` 同理），不等说明布尔转换有错
- `p95_gamerounds` → **221**（已用 pandas 按同口径预演，`PERCENT_RANK` 写法与
  `quantile(0.95)` 在本数据集上恰好都等于 221）
- 四个 `activity_quartile` 箱人数 → **22547 / 22547 / 22547 / 22548**，
  对应回合数区间 **0–5 / 5–16 / 16–51 / 51–49854**（已预演确认）
- `gamerounds_capped` 的 `max` 必须等于 `p95_gamerounds`（= 221）
- 预期缩尾后均值约 **42.57（gate_30）/ 42.44（gate_40）**

**`03_ads.sql` 之后**
- 行数：`ads_ab_overall = 3`、`ads_ab_activity_quartile = 8`、`ads_ab_srm = 1`
- `ads_ab_overall` 两个分组的 `user_cnt` 合计 = 90189（脚本里有断言查询）
- 与同项目 Python 报告对齐的预期值（`report/s2_metrics.md`）：

  | version | user_cnt | ret1_users | ret1_rate | ret7_users | ret7_rate | avg_rounds | median_rounds |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | gate_30 | 44700 | 20034 | 0.448188 | 8502 | 0.190201 | 52.4563 | 17 |
  | gate_40 | 45489 | 20019 | 0.442283 | 8279 | 0.182000 | 51.2988 | 16 |

- SRM：`n1 = 44700`、`n2 = 45489`，
  `chi_square ≈ 6.902405`（`789^2 / 90189`），
  对应 p 值约 **0.0086**（Python 端 `chi2.sf(6.9024, 1)` 已算出，属轻微 SRM）

**关于中位数口径的好消息**：`gate_30` 有 44700 行（偶数）、`gate_40` 有 45489 行（奇数）。
脚本的"上中位数"写法在本数据集上得到 17 / 16，与 pandas 的 `median()` 完全一致，
所以第六节提到的中位数差异在本项目里不会真实发生。

---

## 六、已知口径差异（不是 bug）

1. **中位数**：MySQL 无 `MEDIAN` 函数，脚本用 `ROW_NUMBER()` 取"上中位数"，
   与 pandas 在偶数样本下取中间两行平均会有 ±1 的差异。
2. **95 分位**：脚本用 `PERCENT_RANK()` 取"百分位 ≤ 0.95 的最大回合数"，
   与 `pandas.Series.quantile(0.95)` 的线性插值口径会有 1~2 的差异；
   若要与 Python 报告完全对齐，可把 `02`/`03` 里算 p95 的子查询换成 pandas 输出值。
3. **`activity_quartile` 是"按人数均分"**（`NTILE` 的定义），不是按回合数等距分箱，
   因此 Q1 里会聚集大量 0 回合用户；分群对比必须在**两个分组内部**比较同一分位，
   不能跨分位比较。
4. **缩尾阈值：SQL 与 Python 完全对齐**（两者都是全表 p95 = **221**）。
   注意 `report/s2_metrics.md` 里出现的 222 / 220 是**分组内**的 p95 **统计量**，
   不是缩尾阈值，这两件事不是一回事，不要混淆。
5. **`activity_quartile` 的并列值归属**：本数据集回合数在 5 / 16 / 51 上有大量并列，
   若不在 `NTILE` 的 `ORDER BY` 里指定 tiebreaker，MySQL 不保证分箱顺序，
   实测会让 Q4 的 7 日留存效应从 −2.539pp 偏到 −2.571pp（约 0.03pp）。
   现已写成 `ORDER BY sum_gamerounds, userid`（与本数据集 CSV 的行顺序、
   即 pandas `rank(method='first')` 的打破规则等价），实测四个分群的人数与效应**逐位一致**：
   对照/实验人数 11016/11532、11144/11403、11525/11022、11015/11532。
6. **`TRUE/FALSE` 大小写**：本仓库的 `cookie_cats.csv` 实测是大写 `TRUE/FALSE`，
   但 Kaggle 上有小写 `True/False` 的版本，脚本统一用 `UPPER(...)` 判断，两种都能兼容。

---

## 七、语法自评（最可能出问题的地方）

按风险从高到低：

1. **`LOAD DATA LOCAL INFILE` 的环境依赖**（最可能失败，与 SQL 正确性无关）：
   服务端 `local_infile`、客户端 `--local-infile=1`、CSV 绝对路径、正斜杠，
   四者任一不对都会失败。
2. **`01_ods.sql` 中 `SET` 子句的变量写法**：
   用 `@retention_1` 接字符串再 `CASE/ UPPER` 判断，逻辑上没问题，
   但如果 CSV 行尾是 CRLF，`retention_7` 可能残留 `\r`；
   脚本用 `TRIM()` 兜底，理论上安全，若仍异常请检查行尾符。
3. **`02_dwd.sql` 的 `INSERT ... WITH ... SELECT` 结构**：
   MySQL 要求 `WITH` 子句位于 `INSERT INTO ... ` 之后、`SELECT` 之前，
   脚本已按此书写；这是 MySQL 与 PostgreSQL 写法差异最大的一处。
4. **`03_ads.sql` 中带 `UNION ALL` 的 CTE 查询**：
   `TOTAL_ALL` 行用相关子查询拼装，若某组数据为空，`p95_rounds`/`median_rounds`
   会退化为 `0`（脚本已用 `COALESCE` 兜底，不会出现 NULL）。
5. **`p95_rounds` 的计算精度**：`PERCENT_RANK()` 在整数回合数上存在大量并列。
   全表口径下与 `pandas.quantile(0.95)` 实测**一致**（都是 221）；
   但**分组内**口径（gate_30 / gate_40 各自的 p95）可能因并列与插值方式差 1~2，
   这只影响 `ads_ab_overall` 里报告的统计量，**不影响缩尾阈值**（见第六节第 4 条）。
6. **编码**：所有 `.sql` 文件均为 **UTF-8（无 BOM）**，
   连接时请带 `--default-character-set=utf8mb4`，否则中文注释/别名可能乱码。
7. 脚本中**未使用** `FULL OUTER JOIN`、`TOP n` 以及任何 PostgreSQL/SQL Server 专有语法，
   只使用 MySQL 8.0 支持的 CTE、窗口函数、`LEAST`、`NTILE`、`PERCENT_RANK`、
   `ROW_NUMBER`、`POWER`、`REGEXP`、`CREATE TABLE ... AS SELECT`。

---

## 八、实测验证记录（MySQL 8.0.46）

**验证环境**：MySQL 8.0.46，`mysqld --initialize-insecure` 建的临时实例（独立数据目录、端口 3307，
未触碰机器上原有的 MySQL 服务；验证结束后实例与数据目录已删除）。
数据文件：本项目使用的 `cookie_cats.csv`（90,190 行含表头，CRLF 换行）。

**执行结果**：4 个脚本全部 `EXIT=0`，无 SQL 错误。

| 核对项 | 实测值 | 期望值 | 结论 |
| --- | --- | --- | --- |
| `ods_cookie_cats_raw` 行数 | 90,189 | 90,189 | ✅ |
| `userid` 重复行数 | 0 | 0 | ✅ |
| `retention_1` 非法值行数 | 0 | 0 | ✅ |
| `retention_7` 非法值行数 | 0 | 0 | ✅（修复前为 90,188，见第九节坑 1） |
| `dwd_ab_user` 行数 | 90,189 | 90,189 | ✅ 无丢行 |
| 对照组 / 实验组人数 | 44,700 / 45,489 | 44,700 / 45,489 | ✅ |
| 缩尾阈值 `p95_gamerounds` | 221 | 221（= pandas `quantile(0.95)`） | ✅ 完全一致 |
| 零回合用户数 | 3,994 | 3,994 | ✅ |
| 三张 ADS 表行数 | overall 3 / quartile 8 / srm 1 | 3 / 8 / 1 | ✅ |

**与 Python 报告的逐位对账**（这一步最关键：两个引擎算出的数字必须一样）：

| 指标 | SQL 产出 | Python 报告 | 一致 |
| --- | --- | --- | --- |
| 对照组次日留存率 | 0.448188 | 44.8188% | ✅ |
| 实验组次日留存率 | 0.442283 | 44.2283% | ✅ |
| 对照组 7 日留存率 | 0.190201 | 19.0201% | ✅ |
| 实验组 7 日留存率 | 0.182000 | 18.2000% | ✅ |
| 7 日留存效应（实验 − 对照） | −0.008201 | −0.8201 pp | ✅ |
| SRM 卡方统计量 | 6.902405 | 6.9024 | ✅ |
| 分群人数（对照/实验）Q1–Q4 | 11016/11532、11144/11403、11525/11022、11015/11532 | 同左 | ✅ 逐位一致 |
| 分群 7 日留存效应 Q1–Q4 | +0.000608 / −0.000458 / −0.015154 / −0.025393 | +0.000607 / −0.000458 / −0.015154 / −0.025393 | ✅（Q1 差异仅为 6 位小数舍入） |

**结论**：SQL 数仓层与 Python 分析层在整体指标、SRM 统计量、分群口径上完全可比，
可以在报告里放心地同时引用两层的结果。

---

## 九、已修复的 3 个坑（都是实测才暴露出来的）

这三个问题"读代码看不出来、跑一遍必然踩到"，记录下来以免重复踩坑：

### 坑 1：CRLF 换行让最后一个字段带上 `\r`（**最严重，会让 DWD 层几乎全空**）
- **现象**：`01_ods.sql` 执行成功、`ods_cookie_cats_raw` 行数 90,189 也完全正确，
  但 `dwd_ab_user` 里只有 **1 行**。
- **根因**：本 CSV 是 Windows 的 CRLF（`\r\n`）换行，脚本却用 `LINES TERMINATED BY '\n'` 切行，
  于是**每行最后一个字段**会残留一个 `\r`。本 CSV 的最后一列恰好是 `retention_7`，
  它变成 `'TRUE\r'`，与 DWD 层 `UPPER(retention_7) IN ('TRUE','FALSE')` 不匹配，被整批过滤掉。
- **关键认知**：**MySQL 的 `TRIM(expr)` 不带参数时只去空格，不去 `\r`**
  （原脚本注释曾写"由 TRIM 清掉"，是错的）。
- **修复**：对所有字段统一 `REPLACE(字段, '\r', '')` 再 `TRIM`，CRLF 与 LF 两种换行都能正确解析。
- **验证**：修复后 `bad_retention_7` 从 90,188 降为 **0**，`dwd_ab_user` 行数从 1 变为 **90,189**。

### 坑 2：`SET SESSION local_infile = 1` 是非法语句（**会让脚本直接中断**）
- **根因**：`local_infile` 是 **GLOBAL** 变量，不存在 SESSION 级设置。
  写 `SET SESSION local_infile = 1;` 会报 `ERROR 1229`，
  而 mysql 客户端在批处理模式下默认遇错即停，脚本就断在这一行。
- **修复**：脚本不再主动设置它，改为在文件头写明三处前置条件
  （服务端 `SET GLOBAL local_infile=1`、客户端 `--local-infile=1`、路径用正斜杠），
  并保留一行注释掉的可选语句，供有权限时手动执行。

### 坑 3：整数相除只保留 4 位小数（**让看板数字与报告对不上**）
- **现象**：`ads_ab_overall.retention_1_rate` 显示 `0.448200`，而真实值是 `0.448188`。
- **根因**：MySQL 的 `div_precision_increment` 默认为 **4**，
  两个整数相除（`BIGINT / BIGINT`）的结果 scale = 0 + 4 = 4，
  所以 `20034 / 44700` 被算成 `0.4482`；即使列声明是 `DECIMAL(10,6)` 也无法还原已丢失的精度。
- **修复**：在 `01` / `03` 开头加 `SET SESSION div_precision_increment = 10;`。
- **验证**：修复后留存率为 `0.448188 / 0.190201 / 0.182000`，与 Python 报告逐位一致。
