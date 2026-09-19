# -*- coding: utf-8 -*-
"""
将桌面 Excel 只读导入 SQLite（内存库），并用 SQL 查询业务问题。
不修改 Excel，也不在磁盘上生成数据库文件。
"""

import os
import sqlite3
import pandas as pd


# Excel 固定路径（只读，程序不会改写该文件）
EXCEL_PATH = r"C:\Users\兰\Desktop\统计数据A001.xlsx"


def 规范化日期(文本):
    """把 2025.09.19 / 2025-09-19 / 2025/09/19 统一成 2025-09-19，便于比较。"""
    文本 = str(文本).strip()
    return 文本.replace(".", "-").replace("/", "-")


def 打印表格(数据框):
    """在控制台打印查询结果；无数据时给出提示。"""
    if 数据框 is None or 数据框.empty:
        print("（无符合条件的记录）")
        return
    # 不截断列，方便看完整编码和日期
    with pd.option_context("display.max_columns", None, "display.width", 160, "display.max_rows", 200):
        print(数据框.to_string(index=False))
    print(f"共 {len(数据框)} 行。")


def 读取excel并导入sqlite():
    """
    只读打开 Excel，把两个工作表写入 SQLite 内存库。
    返回：数据库连接。
    """
    if not os.path.isfile(EXCEL_PATH):
        raise FileNotFoundError(f"找不到 Excel 文件：{EXCEL_PATH}")

    # :memory: 表示只在内存建库，不会在磁盘新建或改写任何文件
    连接 = sqlite3.connect(":memory:")

    # engine=openpyxl 且不调用保存，保证只读 Excel
    with pd.ExcelFile(EXCEL_PATH, engine="openpyxl") as excel文件:
        for 表名 in excel文件.sheet_names:
            数据 = pd.read_excel(excel文件, sheet_name=表名)
            # 表名与 Sheet 名一致：订单、供应商
            数据.to_sql(表名, 连接, if_exists="replace", index=False)
            print(f"已导入工作表「{表名}」，{len(数据)} 行，列：{list(数据.columns)}")

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


def 显示菜单():
    """打印交互菜单。"""
    print("\n========== 业务查询 ==========")
    print("1  某个日期之后的订单（WHERE）")
    print("2  每个供应商下单笔数与总采购金额（GROUP BY + SUM/COUNT）")
    print("3  按供应商编码拼接两个 Sheet（INNER JOIN）")
    print("4  检查孤儿数据（LEFT JOIN … IS NULL）")
    print("输入「退出」结束程序")
    print("==============================")


def 主程序():
    """程序入口：导入数据后循环查询，输入「退出」结束。"""
    print("Excel → SQLite 业务查询（只读 Excel，使用内存数据库）")
    print(f"数据文件：{EXCEL_PATH}")

    连接 = 读取excel并导入sqlite()
    try:
        while True:
            显示菜单()
            选择 = input("请输入序号或「退出」：").strip()

            # 用户输入「退出」则结束程序
            if 选择 == "退出":
                print("已退出。")
                break

            if 选择 == "1":
                日期 = input("请输入截止日期（如 2025.09.22 或 2025-09-22；输入「退出」结束）：").strip()
                if 日期 == "退出":
                    print("已退出。")
                    break
                print("\n【日期之后的订单】")
                打印表格(查询日期之后的订单(连接, 日期))

            elif 选择 == "2":
                print("\n【各供应商下单汇总】总采购金额 = 采购数量 * 单价")
                打印表格(查询供应商下单汇总(连接))

            elif 选择 == "3":
                print("\n【INNER JOIN：订单.供应商名称 = 供应商.供应商编码】")
                打印表格(按供应商编码内连接(连接))

            elif 选择 == "4":
                订单孤儿, 供应商孤儿 = 检查孤儿数据(连接)
                print("\n【订单孤儿】订单里有供应商编码，供应商表中没有对应档案")
                打印表格(订单孤儿)
                print("\n【供应商孤儿】供应商表有档案，订单里从未出现该编码")
                打印表格(供应商孤儿)

            else:
                print("无效输入。请输入 1/2/3/4，或输入「退出」。")
    finally:
        # 关闭内存库连接
        连接.close()


if __name__ == "__main__":
    主程序()
