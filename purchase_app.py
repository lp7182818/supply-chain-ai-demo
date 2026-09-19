# -*- coding: utf-8 -*-
"""
采购台账网页查询：上传 Excel 后，在内存里建 SQLite，用原来的四条 SQL 查数据。
不改 Excel，也不在电脑上生成数据库文件。
"""

# 下面这几行是“工具箱”：网页、表格、数据库
import sqlite3
import pandas as pd
import streamlit as st


def 规范化日期(文本):
    """把 2025.09.19 / 2025-09-19 / 2025/09/19 统一成 2025-09-19，便于比较。"""
    文本 = str(文本).strip()
    return 文本.replace(".", "-").replace("/", "-")


def 读取excel并导入sqlite(上传文件):
    """
    只读打开用户上传的 Excel，把每个有数据的工作表写进内存数据库。
    空表（没有行或没有列）会跳过，并在网页上黄条提醒。
    返回：数据库连接（只存在于内存，关掉网页就消失）。
    """
    # :memory: 表示只在内存建库，不会在磁盘新建或改写任何文件
    连接 = sqlite3.connect(":memory:")

    # engine=openpyxl 且不调用保存，保证只读 Excel
    with pd.ExcelFile(上传文件, engine="openpyxl") as excel文件:
        for 表名 in excel文件.sheet_names:
            数据 = pd.read_excel(excel文件, sheet_name=表名)
            # 行数为 0 或列数为 0：这张表是空的，导入会出问题，所以跳过
            if len(数据) == 0 or len(数据.columns) == 0:
                st.warning(f"跳过空表：{表名}")
                continue
            # 表名与 Sheet 名一致：订单、供应商
            数据.to_sql(表名, 连接, if_exists="replace", index=False)

    return 连接


def 查询日期之后的订单(连接, 截止日期):
    """
    业务问题1：查出某个日期之后的订单。
    使用 WHERE；日期字段为「下单日期」，原始格式类似 2025.09.19。
    """
    截止 = 规范化日期(截止日期)
    sql = """
    SELECT *
    FROM 订单
    WHERE REPLACE(REPLACE(下单日期, '.', '-'), '/', '-') > ?
    ORDER BY REPLACE(REPLACE(下单日期, '.', '-'), '/', '-')
    """
    return pd.read_sql_query(sql, 连接, params=(截止,))


def 查询供应商下单汇总(连接):
    """
    业务问题2：每个供应商下了多少单、总采购金额（数量 * 单价）。
    使用 GROUP BY + COUNT / SUM。
    说明：订单表列名是「供应商名称」，但单元格里存的是供应商编码。
    """
    sql = """
    SELECT
        供应商名称 AS 供应商编码,
        COUNT(*) AS 下单笔数,
        SUM(采购数量 * 单价) AS 总采购金额
    FROM 订单
    GROUP BY 供应商名称
    ORDER BY 总采购金额 DESC
    """
    return pd.read_sql_query(sql, 连接)


def 按供应商编码内连接(连接):
    """
    业务问题3：以供应商编码为关键字段拼接两个 Sheet。
    使用 INNER JOIN：订单.供应商名称 = 供应商.供应商编码。
    只保留两边都能匹配上的行。
    """
    sql = """
    SELECT
        订单.序号 AS 订单序号,
        订单.订单编号,
        订单.供应商名称 AS 供应商编码,
        供应商.供应商名称,
        订单.采购数量,
        订单.单价,
        订单.采购数量 * 订单.单价 AS 采购金额,
        订单.下单日期
    FROM 订单
    INNER JOIN 供应商
        ON 订单.供应商名称 = 供应商.供应商编码
    ORDER BY 订单.序号
    """
    return pd.read_sql_query(sql, 连接)


def 检查孤儿数据(连接):
    """
    业务问题4：检查孤儿数据。
    使用 LEFT JOIN … IS NULL：
    - 订单有编码、供应商表没有：订单孤儿；
    - 供应商表有编码、订单没有：供应商孤儿。
    """
    订单孤儿sql = """
    SELECT
        订单.序号,
        订单.订单编号,
        订单.供应商名称 AS 供应商编码,
        订单.采购数量,
        订单.单价,
        订单.下单日期
    FROM 订单
    LEFT JOIN 供应商
        ON 订单.供应商名称 = 供应商.供应商编码
    WHERE 供应商.供应商编码 IS NULL
    """
    供应商孤儿sql = """
    SELECT
        供应商.序号,
        供应商.供应商编码,
        供应商.供应商名称
    FROM 供应商
    LEFT JOIN 订单
        ON 供应商.供应商编码 = 订单.供应商名称
    WHERE 订单.供应商名称 IS NULL
    """
    订单孤儿 = pd.read_sql_query(订单孤儿sql, 连接)
    供应商孤儿 = pd.read_sql_query(供应商孤儿sql, 连接)
    return 订单孤儿, 供应商孤儿


# ---------- 从这里开始是网页界面 ----------

# 网页标题：告诉用户这是干什么的
st.title("采购台账查询")

# 上传框：用户把 Excel 拖进来（xlsx / xls 都可以）
上传文件 = st.file_uploader("请上传 Excel 文件", type=["xlsx", "xls"])

# 还没传文件时，给一句提示并停住，避免后面查表时报错铺满屏幕
if 上传文件 is None:
    st.info("请先上传Excel")
    st.stop()

# 把 Excel 读进内存数据库（空 sheet 会跳过并黄条提示）
连接 = 读取excel并导入sqlite(上传文件)

# 四个查询用下拉框切换，一次只看一种
查询选项 = st.selectbox(
    "请选择要做的查询",
    [
        "查询1：某个日期之后的订单",
        "查询2：每个供应商下单笔数与总采购金额",
        "查询3：按供应商编码拼接订单与供应商",
        "查询4：检查孤儿数据",
    ],
)

# 查询1：选一个截止日期，列出比它更晚的订单
if 查询选项.startswith("查询1"):
    # 日期控件：点日历选日期即可，不用自己敲格式
    截止日期 = st.date_input("截止日期（只显示这一天之后的订单）")
    结果 = 查询日期之后的订单(连接, 截止日期)
    # 顶部数字：一共查出多少行
    st.metric("行数", len(结果))
    # 表格展示全部结果
    st.dataframe(结果)

# 查询2：按供应商汇总笔数和金额，表格 + 柱状图
elif 查询选项.startswith("查询2"):
    结果 = 查询供应商下单汇总(连接)
    # 两个数字并排：有多少家供应商、金额加总是多少
    左栏, 右栏 = st.columns(2)
    左栏.metric("供应商数", len(结果))
    总金额 = 0 if 结果.empty else 结果["总采购金额"].sum()
    右栏.metric("总金额", 总金额)
    # 先看明细表
    st.dataframe(结果)
    # 再画柱状图：横轴是供应商编码，竖轴是总采购金额
    if not 结果.empty:
        st.bar_chart(结果.set_index("供应商编码")["总采购金额"])

# 查询3：订单和供应商按编码对上号，只保留两边都有的行
elif 查询选项.startswith("查询3"):
    结果 = 按供应商编码内连接(连接)
    st.dataframe(结果)

# 查询4：找出对不上号的“孤儿”——订单里有、档案没有；或档案有、订单没有
else:
    订单孤儿, 供应商孤儿 = 检查孤儿数据(连接)
    st.subheader("订单孤儿")
    st.caption("订单里有供应商编码，供应商表中没有对应档案")
    st.dataframe(订单孤儿)
    st.subheader("供应商孤儿")
    st.caption("供应商表有档案，订单里从未出现该编码")
    st.dataframe(供应商孤儿)

# 用完就关掉内存库连接（数据不会存到硬盘）
连接.close()
