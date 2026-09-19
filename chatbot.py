# 随机性：数值越低回答越稳定，越高越发散，方便你在文件顶部直接改
TEMPERATURE = 0.1

# DeepSeek 接口地址（兼容 OpenAI SDK）
BASE_URL = "https://api.deepseek.com"

# 对话模型名称
MODEL = "deepseek-chat"

# 在这里填入你的 DeepSeek API Key
API_KEY = "sk-94feec9834c24a9685d73e3495917a8d"

import contextlib
import io
import os
import re
import traceback

import matplotlib

# 无界面保存图片，避免弹出窗口卡住命令行
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from openai import OpenAI

# 当前用户桌面路径（Windows 下一般为 USERPROFILE\Desktop）
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# 台账文件只读，程序中不会对它执行任何写入
EXCEL_PATH = os.path.join(DESKTOP, "模拟采购台账_50行.xlsx")

# 执行 AI 代码时，把相对路径的图片一律存到桌面
_原始_savefig = plt.savefig


def _保存到桌面的savefig(fname, *args, **kwargs):
    """拦截 savefig：相对路径自动改写为桌面路径。"""
    路径 = str(fname)
    if not os.path.isabs(路径):
        路径 = os.path.join(DESKTOP, os.path.basename(路径))
    return _原始_savefig(路径, *args, **kwargs)


plt.savefig = _保存到桌面的savefig


def 读取台账():
    """只读加载 Excel，不改文件、不写回。"""
    return pd.read_excel(EXCEL_PATH)


def 调用模型(client, messages):
    """每次都把完整历史 messages 发给 DeepSeek。"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE,
    )
    return response.choices[0].message.content or ""


def 是否无需代码(文本):
    """判断模型是否认定这是非数据问题。"""
    首行 = ""
    for 行 in 文本.splitlines():
        if 行.strip():
            首行 = 行.strip()
            break
    return 首行.upper().startswith("NO_CODE")


def 去掉NO_CODE标记(文本):
    """去掉第一行 NO_CODE，剩下作为常识回答。"""
    行列表 = 文本.splitlines()
    for 下标, 行 in enumerate(行列表):
        if 行.strip():
            return "\n".join(行列表[下标 + 1 :]).strip() or 文本
    return 文本.strip()


def 提取代码(文本):
    """从回复里取出 Python 代码块；没有围栏则把全文当作代码。"""
    匹配 = re.search(r"```(?:python)?\s*([\s\S]*?)```", 文本, re.IGNORECASE)
    if 匹配:
        return 匹配.group(1).strip()
    return 文本.strip()


def 执行分析代码(代码, 原始df):
    """
    用 exec 跑模型生成的 pandas 代码。
    每次传入 df 的副本，避免污染内存中的原始台账；源 Excel 始终只读。
    返回：(是否成功, 抓取到的文本结果或报错信息)
    """
    命名空间 = {
        "df": 原始df.copy(),
        "pd": pd,
        "np": np,
        "plt": plt,
        "sns": sns,
        "os": os,
        "DESKTOP": DESKTOP,
    }
    标准输出 = io.StringIO()
    try:
        with contextlib.redirect_stdout(标准输出):
            exec(代码, 命名空间)
        输出 = 标准输出.getvalue()
        # 若代码把最终结果放进 result，一并抓回来
        if "result" in 命名空间:
            结果 = 命名空间["result"]
            if isinstance(结果, (pd.DataFrame, pd.Series)):
                输出 += ("\n" if 输出 else "") + 结果.to_string()
            else:
                输出 += ("\n" if 输出 else "") + str(结果)
        if not 输出.strip():
            输出 = "代码已执行完成，没有打印输出。若已保存图表，请查看桌面上的 png 文件。"
        return True, 输出.strip()
    except Exception:
        return False, traceback.format_exc()


def 构建系统提示(列名):
    """系统消息：采购助手身份 + 代码/常识分流规则。"""
    列说明 = "、".join(列名)
    return (
        "你是一个采购数据分析助手。\n"
        "桌面上的采购台账已经用 pandas 只读加载到变量 df 中，列名如下："
        f"{列说明}。\n"
        "规则：\n"
        "1. 如果用户问题需要查询、统计、筛选、汇总或可视化这张台账，"
        "请只输出一段可执行的 Python 代码（可用 markdown 的 python 代码块），不要先写解释。\n"
        "2. 代码约定：数据在 df 里；可以使用 pandas、numpy、matplotlib、seaborn；"
        "把要展示给用户的表格或数字放到变量 result 中，或用 print 打印；"
        "需要图表时必须调用 plt.savefig('文件名.png')，图片会保存到桌面，不要 plt.show()；"
        "可用 DESKTOP 表示桌面路径；禁止对 Excel/CSV 等数据文件做任何写入或修改。\n"
        "3. 如果用户问题与台账数据无关，第一行必须写 NO_CODE，从第二行起用中文常识直接回答，不要写代码。\n"
        "4. 回答时结合对话历史，记住用户之前问过什么。"
    )


def main():
    """启动命令行多轮对话：按需生成并执行 pandas 代码，输入「退出」结束。"""
    if not API_KEY or API_KEY.startswith("在这里"):
        print("请先在文件顶部的 API_KEY 里填入你的 DeepSeek 密钥。")
        return

    if not os.path.isfile(EXCEL_PATH):
        print(f"未找到台账文件：{EXCEL_PATH}")
        return

    # 启动时只读一次，后续分析都用内存中的副本
    台账 = 读取台账()
    列名 = [str(c) for c in 台账.columns]

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 消息列表：第一项是系统设定，之后依次追加本轮及历史对话
    messages = [
        {
            "role": "system",
            "content": 构建系统提示(列名),
        }
    ]

    # 已完成的用户-助手对话轮数（不含 system，不含代码纠错的内部往返）
    round_count = 0

    print("采购数据分析助手已启动。输入「退出」结束程序。")
    print(f"已只读加载台账：{EXCEL_PATH}（共 {len(台账)} 行）")
    print(f"列名：{'、'.join(列名)}")
    print("-" * 40)

    while True:
        # 读取本轮用户输入；去掉首尾空白避免误判
        user_input = input("你：").strip()

        # 输入「退出」则结束循环并退出程序
        if user_input == "退出":
            print("已结束对话，再见。")
            break

        # 空输入不调用接口，避免浪费额度
        if not user_input:
            print("请输入内容，或输入「退出」结束。")
            continue

        # 把本轮用户话追加进历史，保证后续请求带上完整上下文
        messages.append({"role": "user", "content": user_input})

        # 第一次调用：判断是否跑代码，或直接常识回答
        初回复 = 调用模型(client, messages)

        if 是否无需代码(初回复):
            助手回复 = 去掉NO_CODE标记(初回复)
            messages.append({"role": "assistant", "content": 助手回复})
        else:
            代码 = 提取代码(初回复)
            成功, 执行信息 = 执行分析代码(代码, 台账)

            # 执行失败时，把报错发回模型修正后再跑一次（最多重试 1 次）
            if not 成功:
                messages.append({"role": "assistant", "content": 初回复})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "刚才的代码执行报错了，请根据报错修正后只输出完整可执行的 Python 代码，"
                            "不要解释。报错信息如下：\n"
                            f"{执行信息}"
                        ),
                    }
                )
                修正回复 = 调用模型(client, messages)
                代码 = 提取代码(修正回复)
                成功, 执行信息 = 执行分析代码(代码, 台账)
                初回复 = 修正回复

            if not 成功:
                助手回复 = (
                    "代码已重试一次仍然失败，报错信息如下：\n" + 执行信息
                )
                messages.append({"role": "assistant", "content": 助手回复})
            else:
                # 把生成的代码写入历史，便于后续轮次接着分析
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"```python\n{代码}\n```",
                    }
                )
                # 把真实执行结果交给模型，要求用自然语言解释
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "以上代码已在本地用 exec 执行（台账只读，未改任何数据文件）。"
                            "请根据下面的执行结果，用自然语言向用户解释，不要再输出代码：\n"
                            f"{执行信息}"
                        ),
                    }
                )
                助手回复 = 调用模型(client, messages)
                messages.append({"role": "assistant", "content": 助手回复})

        # 成功完成本轮用户提问后计数加一（内部纠错不计为新的一轮）
        round_count += 1

        print(f"助手：{助手回复}")
        print(f"当前对话已进行{round_count}轮")


if __name__ == "__main__":
    main()
