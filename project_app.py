# -*- coding: utf-8 -*-
"""
项目入口网页：侧边栏切换「采购数据分析」与「合同要素提取」。
采购功能与 purchase_app.py 一致；合同为一次性 JSON 提取。
"""

# 在这里填入你的 DeepSeek API Key（自己填，不要写死进仓库、不要打印、不要上传）
API_KEY = "sk-"

# DeepSeek 接口地址（兼容 OpenAI SDK）
BASE_URL = "https://api.deepseek.com"

# 对话模型名称
MODEL = "deepseek-chat"

# 随机性：数值越低回答越稳定
TEMPERATURE = 0.1

# 下面这几行是“工具箱”：网页、表格、数据库、JSON、模型接口
import json
import traceback

import sqlite3
import pandas as pd
import streamlit as st
from openai import OpenAI


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


# 系统身份：只做提取与风险标记（文字与 contract_chatbot.py 完全一致）
系统提示 = (
    "你是一个 合同管理员，只做信息提取和风险标记，不提供法律意见、不编造合同里没有的内容。\n"
    "规则：\n"
    "1. 与本合同无关的问题，用常识直接回答。\n"
    "2. 与合同有关的问题，必须依据合同原文回答；合同里没有的内容必须明说「未约定」，不许编造。\n"
    "3. 核对如涉及运算，必须列出完整算式（含每条明细的数量×单价，以及求和过程）。\n"
    "4. 无法从合同文本直接找到的字段：仅当存在可核对的推理时才填写并写明推理过程；"
    "否则直接填写「合同中未明确」，不允许不合理推断。\n"
    "5. 重点核对：合同各处供应商名称是否完全一致；合同总金额是否等于各明细金额总和；"
    "付款条件是否明确。"
)


def 构建首次提取提示(合同文本):
    """启动时自动提取用的用户消息：说明 JSON 三块结构与核对要求。"""
    return (
        "请阅读下面这份采购合同全文，只根据合同原文做信息提取和风险标记。\n"
        "请输出一个 JSON 对象，必须且只含以下三块：\n"
        "1. 「基本信息」：对象，字段为供应商、合同总金额、交货地点、付款条件、签订日期。\n"
        "2. 「物料清单」：列表，每条含名称、规格型号、单价、数量（只要这四个字段）。\n"
        "3. 「风险清单」：列表，每条含：什么风险、在合同哪个位置、为什么。\n"
        "核对要求：\n"
        "- 检查合同各处供应商名称是否完全一致（如标题、甲乙方、签章处）。\n"
        "- 检查合同总金额是否等于各明细金额总和，必须列出算式。\n"
        "- 检查付款条件是否明确。\n"
        "- 风险单独放在「风险清单」中，每条写清：什么风险、在合同哪个位置、为什么。\n"
        "- 合同中找不到的字段：能核对的推理须写出推理过程，否则填「合同中未明确」。\n"
        "不要输出 JSON 以外的任何文字。\n"
        "合同全文如下：\n"
        f"{合同文本}"
    )


def 读取上传的合同文本(上传文件):
    """只读解码上传的 txt，不改文件、不写回。依次尝试常见中文编码。"""
    原始字节 = 上传文件.getvalue()
    最后错误 = None
    for 编码 in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return 原始字节.decode(编码)
        except UnicodeDecodeError as 错误:
            最后错误 = 错误
    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"无法用 utf-8/gbk 解码合同文件：{最后错误}",
    )


def 调用模型(client, messages):
    """一次性提取调用：开启 JSON 模式，temperature 固定为顶部常量。"""
    参数 = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        # 开启 JSON 模式，要求模型返回合法 JSON 对象
        "response_format": {"type": "json_object"},
    }
    response = client.chat.completions.create(**参数)
    return response.choices[0].message.content or ""


def 解析json对象(文本):
    """把模型回复解析成 dict；不是对象则报错，便于交给模型修正。"""
    对象 = json.loads(文本)
    if not isinstance(对象, dict):
        raise ValueError("JSON 根节点必须是对象")
    return 对象


def 首次提取合同json(client, messages):
    """
    点按钮后第一次调用：JSON 模式提取合同。
    若解析或调用报错，把报错发给 DeepSeek 修正后再跑一次（最多重试 1 次）。
    成功则返回解析后的 dict。本步不做多轮追问、不保存对话历史。
    """
    上次回复 = ""
    try:
        上次回复 = 调用模型(client, messages)
        return 解析json对象(上次回复)
    except Exception:
        报错 = traceback.format_exc()
        if 上次回复:
            messages.append({"role": "assistant", "content": 上次回复})
        messages.append(
            {
                "role": "user",
                "content": (
                    "刚才的执行报错了，请根据报错修正后只输出一个合法 JSON 对象"
                    "（含基本信息、物料清单、风险清单三块），不要解释。报错信息如下：\n"
                    f"{报错}"
                ),
            }
        )
        上次回复 = 调用模型(client, messages)
        return 解析json对象(上次回复)


def 转成表格(数据):
    """把 JSON 的对象或列表转成 DataFrame，方便用表格展示。"""
    if 数据 is None:
        return pd.DataFrame()
    if isinstance(数据, dict):
        return pd.DataFrame([数据])
    if isinstance(数据, list):
        if not 数据:
            return pd.DataFrame()
        if all(isinstance(项, dict) for 项 in 数据):
            return pd.DataFrame(数据)
        return pd.DataFrame({"内容": 数据})
    return pd.DataFrame([{"内容": 数据}])


# ---------- 从这里开始是网页界面 ----------

# 侧边栏：两个功能二选一，不使用多页面
功能 = st.sidebar.radio("请选择功能", ["采购数据分析", "合同要素提取"])

# 合同：上传 txt，点按钮后一次性 JSON 提取，不做多轮追问
if 功能 == "合同要素提取":
    st.title("合同要素提取")

    # 本步只支持纯文本合同，只读上传内容
    合同文件 = st.file_uploader("请上传合同文件", type=["txt"])

    # 还没传文件时停住，避免空内容去调模型
    if 合同文件 is None:
        st.info("请先上传合同txt文件")
        st.stop()

    # 点了才调模型；未点则只显示按钮
    if st.button("开始提取"):
        # 密钥只从顶部变量读取，页面上不展示、不上传
        if not API_KEY:
            st.error("请先在文件顶部的 API_KEY 里填入你的 DeepSeek 密钥。")
            st.stop()

        合同文本 = 读取上传的合同文本(合同文件)
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        # 本步只发这一轮系统提示 + 首次提取提示，不保留后续对话
        messages = [
            {"role": "system", "content": 系统提示},
            {"role": "user", "content": 构建首次提取提示(合同文本)},
        ]
        try:
            对象 = 首次提取合同json(client, messages)
        except Exception:
            st.error("自动提取失败（已重试 1 次仍无法得到合法 JSON）")
            st.stop()

        # 结果分三块展示：基本信息、物料清单、风险清单
        st.subheader("基本信息")
        st.table(转成表格(对象.get("基本信息")))

        st.subheader("物料清单")
        st.dataframe(转成表格(对象.get("物料清单")))

        st.subheader("风险清单")
        st.dataframe(转成表格(对象.get("风险清单")))

# 采购：以下界面与查询逻辑与 purchase_app.py 保持一致
else:
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
