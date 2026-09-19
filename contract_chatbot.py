# 随机性：数值越低回答越稳定，越高越发散，方便你在文件顶部直接改
TEMPERATURE = 0.1

# DeepSeek 接口地址（兼容 OpenAI SDK）
BASE_URL = "https://api.deepseek.com"

# 对话模型名称
MODEL = "deepseek-chat"

# 在这里填入你的 DeepSeek API Key
API_KEY = "sk-94feec9834c24a9685d73e3495917a8d"

import json
import os
import traceback

from openai import OpenAI

# 当前用户桌面路径（Windows 下一般为 USERPROFILE\Desktop）
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# 合同文件只读，程序中不会对它执行任何写入
合同路径 = os.path.join(DESKTOP, "练习用_虚构采购合同.txt")

# 系统身份：只做提取与风险标记
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


def 读取合同(路径):
    """只读加载合同文本，不改文件、不写回。依次尝试常见中文编码。"""
    最后错误 = None
    for 编码 in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(路径, "r", encoding=编码) as 文件:
                return 文件.read()
        except UnicodeDecodeError as 错误:
            最后错误 = 错误
    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"无法用 utf-8/gbk 解码合同文件：{最后错误}",
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


def 调用模型(client, messages, 使用json模式=False):
    """每次都把完整历史 messages 发给 DeepSeek；需要时开启 JSON 模式。"""
    参数 = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
    }
    if 使用json模式:
        # 开启 JSON 模式，要求模型返回合法 JSON 对象
        参数["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**参数)
    return response.choices[0].message.content or ""


def 解析json对象(文本):
    """把模型回复解析成 dict；不是对象则报错，便于交给模型修正。"""
    对象 = json.loads(文本)
    if not isinstance(对象, dict):
        raise ValueError("JSON 根节点必须是对象")
    return 对象


def 首次提取并打印(client, messages):
    """
    启动后第一次调用：JSON 模式提取合同。
    若解析或调用报错，把报错发给 DeepSeek 修正后再跑一次（最多重试 1 次）。
    成功则返回助手原文（合法 JSON 字符串）。
    """
    使用json模式 = True
    上次回复 = ""
    try:
        上次回复 = 调用模型(client, messages, 使用json模式=使用json模式)
        对象 = 解析json对象(上次回复)
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
        上次回复 = 调用模型(client, messages, 使用json模式=使用json模式)
        对象 = 解析json对象(上次回复)

    # 命令行直接打印格式化 JSON，不写文件
    print(json.dumps(对象, ensure_ascii=False, indent=2))
    return json.dumps(对象, ensure_ascii=False, indent=2)


def main():
    """启动：自动提取合同并打印 JSON，随后多轮追问；输入「退出」结束。"""
    if not API_KEY or API_KEY.startswith("在这里"):
        print("请先在文件顶部的 API_KEY 里填入你的 DeepSeek 密钥。")
        return

    if not os.path.isfile(合同路径):
        print(f"未找到合同文件：{合同路径}")
        return

    # 启动时只读一次合同，后续追问都靠对话历史，不再改磁盘上的任何文件
    合同文本 = 读取合同(合同路径)

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 消息列表：第一项是系统设定，之后依次追加本轮及历史对话
    messages = [
        {"role": "system", "content": 系统提示},
        {"role": "user", "content": 构建首次提取提示(合同文本)},
    ]

    # 已完成的用户-助手对话轮数（自动提取算第 1 轮；纠错内部往返不计新轮）
    round_count = 0

    print("合同管理员已启动（只读合同，不会修改任何文件）。输入「退出」结束程序。")
    print(f"已只读加载：{合同路径}")
    print("-" * 40)
    print("正在自动提取合同信息并生成风险清单……")

    try:
        助手回复 = 首次提取并打印(client, messages)
    except Exception:
        print("自动提取失败（已重试 1 次仍无法得到合法 JSON），报错如下：")
        print(traceback.format_exc())
        return

    messages.append({"role": "assistant", "content": 助手回复})
    round_count += 1
    print(f"当前对话已进行{round_count}轮")
    print("-" * 40)
    print("可以继续追问本合同；与合同无关的问题会用常识回答。")

    while True:
        # 读取本轮用户输入；去掉首尾空白避免误判
        user_input = input("你：").strip()

        if user_input == "退出":
            print("已结束对话，再见。")
            break

        if not user_input:
            print("请输入内容，或输入「退出」结束。")
            continue

        # 把本轮用户话追加进历史，保证后续请求带上完整上下文
        messages.append({"role": "user", "content": user_input})

        try:
            助手回复 = 调用模型(client, messages, 使用json模式=False)
        except Exception:
            报错 = traceback.format_exc()
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "刚才的执行报错了，请根据报错修正后重新完整回答，不要解释修正过程。"
                        "报错信息如下：\n"
                        f"{报错}"
                    ),
                }
            )
            try:
                助手回复 = 调用模型(client, messages, 使用json模式=False)
            except Exception:
                助手回复 = "本轮调用已重试一次仍然失败，报错信息如下：\n" + traceback.format_exc()

        messages.append({"role": "assistant", "content": 助手回复})
        round_count += 1
        print(f"助手：{助手回复}")
        print(f"当前对话已进行{round_count}轮")


if __name__ == "__main__":
    main()
