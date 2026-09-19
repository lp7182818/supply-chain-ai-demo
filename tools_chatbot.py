# 随机性：数值越低回答越稳定，越高越发散，方便你在文件顶部直接改
TEMPERATURE = 0.1

# DeepSeek 接口地址（兼容 OpenAI SDK）
BASE_URL = "https://api.deepseek.com"

# 对话模型名称
MODEL = "deepseek-chat"

# 在这里填入你的 DeepSeek API Key
API_KEY = "sk-2341ce55b9b04b1681c30610de045298"

import contextlib
import io
import json
import traceback

from openai import OpenAI

# 系统身份：采购员，由模型自主决定是否调用工具
系统提示 = (
    "你是一个采购员，可以根据用户所问的问题，自主选择需要使用的工具，并且输出结果。\n"
    "规则：\n"
    "1. 需要查供应商采购额时，必须调用工具「查采购额」；"
    "需要算库存周转时，必须调用工具「算库存周转」。不要自己编造这些数字。\n"
    "2. 工具返回的数据才是唯一依据。数据中没有的供应商或月份，必须明确说明无法得出结果，"
    "不允许根据名称相似、行业常识或其它月份做不合理推断。\n"
    "3. 无须调用工具即可回答的问题（如采购常识、流程说明、与测试数据无关的闲聊），直接用中文回答，不要强行调工具。\n"
    "4. 核对内容如涉及运算，必须在回复中列出完整算式（含代入数字的除法或求和过程），再给出结果。\n"
    "5. 可以一次调用多个工具；也可以先调工具再根据结果继续提问用户。"
)

# 工具1测试数据：供应商名称 → 采购总额
采购额表 = {"华为": 3800, "联想": 2100, "中兴": 1500}

# 工具2测试数据：月份 → (年销售成本_万元, 平均库存_万元)
库存表 = {
    1: (100, 30),
    2: (200, 10),
    3: (300, 60),
}

# OpenAI / DeepSeek 的 tools 定义（函数名用英文以满足接口命名约束，中文写在 description 里）
工具定义 = [
    {
        "type": "function",
        "function": {
            "name": "cha_caigoue",
            "description": (
                "查采购额：根据供应商名称查询该供应商的采购总额。"
                "仅查询已有测试数据，未知供应商不得推断。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "供应商名称": {
                        "type": "string",
                        "description": "供应商名称，需与数据中的名称一致，例如：华为、联想、中兴",
                    }
                },
                "required": ["供应商名称"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suan_kucun_zhouzhuan",
            "description": (
                "算库存周转：按「年销售成本 ÷ 平均库存」计算库存周转率。"
                "指定月份则只算该月；未指定月份时使用全部已有月份数据计算。"
                "未知月份不得推断。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "月份": {
                        "type": "integer",
                        "description": "要计算的月份数字，如 1、2、3。不传表示使用全部月份数据。",
                    }
                },
            },
        },
    },
]


def 查采购额(供应商名称):
    """工具1：按供应商名称查询采购总额；查不到则明确返回无法计算。"""
    名称 = str(供应商名称).strip()
    if 名称 not in 采购额表:
        已知 = "、".join(采购额表.keys())
        return json.dumps(
            {
                "成功": False,
                "说明": (
                    f"测试数据中没有供应商「{名称}」的采购额，无法得出结果，不允许推断。"
                    f"当前仅有：{已知}。"
                ),
            },
            ensure_ascii=False,
        )
    总额 = 采购额表[名称]
    return json.dumps(
        {
            "成功": True,
            "供应商名称": 名称,
            "采购总额": 总额,
            "说明": f"供应商「{名称}」的采购总额为 {总额}（来自给定测试数据，非推断）。",
        },
        ensure_ascii=False,
    )


def 解析月份(月份):
    """把模型传入的月份转成整数；未指定则返回 None；无法识别则返回错误说明。"""
    if 月份 is None or 月份 == "":
        return None, None
    if isinstance(月份, bool):
        return None, "月份参数无效，无法计算。"
    if isinstance(月份, int):
        return 月份, None
    if isinstance(月份, float) and 月份.is_integer():
        return int(月份), None
    文本 = str(月份).strip().replace("月份", "").replace("月", "")
    中文数字 = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
               "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    if 文本 in 中文数字:
        return 中文数字[文本], None
    try:
        return int(文本), None
    except (TypeError, ValueError):
        return None, f"无法识别月份「{月份}」，且不能据此推断，请指定 1、2 或 3 月。"


def 单月周转结果(月份):
    """按单月数据计算库存周转率，并列出算式。"""
    年销售成本, 平均库存 = 库存表[月份]
    周转率 = 年销售成本 / 平均库存
    算式 = (
        f"{月份}月：年销售成本 ÷ 平均库存 = {年销售成本}万 ÷ {平均库存}万 = {周转率}"
    )
    return {
        "月份": 月份,
        "年销售成本_万元": 年销售成本,
        "平均库存_万元": 平均库存,
        "算式": 算式,
        "库存周转率": 周转率,
    }


def 算库存周转(月份=None):
    """工具2：库存周转率 = 年销售成本 ÷ 平均库存。内部用月份映射表查数。"""
    月份整数, 错误 = 解析月份(月份)
    if 错误:
        return json.dumps({"成功": False, "说明": 错误}, ensure_ascii=False)

    # 指定了月份：只查该月，表中没有则拒绝推断
    if 月份整数 is not None:
        if 月份整数 not in 库存表:
            已知 = "、".join(f"{m}月" for m in sorted(库存表))
            return json.dumps(
                {
                    "成功": False,
                    "说明": (
                        f"测试数据中没有{月份整数}月的年销售成本与平均库存，无法计算库存周转，不允许推断。"
                        f"当前仅有：{已知}。"
                    ),
                },
                ensure_ascii=False,
            )
        单月 = 单月周转结果(月份整数)
        单月["成功"] = True
        return json.dumps(单月, ensure_ascii=False)

    # 未指定月份：用全部已有月份数据计算（各月明细 + 合计平均）
    各月 = [单月周转结果(m) for m in sorted(库存表)]
    成本合计 = sum(库存表[m][0] for m in 库存表)
    库存合计 = sum(库存表[m][1] for m in 库存表)
    整体周转 = 成本合计 / 库存合计
    成本项 = "+".join(str(库存表[m][0]) for m in sorted(库存表))
    库存项 = "+".join(str(库存表[m][1]) for m in sorted(库存表))
    各月周转 = [x["库存周转率"] for x in 各月]
    算术平均 = sum(各月周转) / len(各月周转)
    各月率项 = "+".join(str(x) for x in 各月周转)
    return json.dumps(
        {
            "成功": True,
            "说明": "用户未指定月份，已使用全部测试月份数据计算，未对其它月份做推断。",
            "各月明细": 各月,
            "按合计计算": {
                "算式": (
                    f"合计年销售成本 ÷ 合计平均库存 = ({成本项})万 ÷ ({库存项})万 "
                    f"= {成本合计}万 ÷ {库存合计}万 = {整体周转}"
                ),
                "库存周转率": 整体周转,
            },
            "各月周转率算术平均": {
                "算式": (
                    f"({各月率项}) ÷ {len(各月周转)} = {sum(各月周转)} ÷ {len(各月周转)} = {算术平均}"
                ),
                "库存周转率": 算术平均,
            },
        },
        ensure_ascii=False,
    )


# 接口函数名 → 本地实现
工具函数表 = {
    "cha_caigoue": 查采购额,
    "suan_kucun_zhouzhuan": 算库存周转,
}


def 取出参数(参数文本):
    """把模型返回的 JSON 参数字符串解析成字典。"""
    if not 参数文本 or not str(参数文本).strip():
        return {}
    解析结果 = json.loads(参数文本)
    if not isinstance(解析结果, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return 解析结果


def 执行工具函数(名称, 参数字典):
    """
    在本地真正执行工具函数。
    返回：(是否成功, 给模型看的字符串结果或报错堆栈)
    """
    if 名称 not in 工具函数表:
        return True, json.dumps(
            {"成功": False, "说明": f"未知工具「{名称}」，无法执行。"},
            ensure_ascii=False,
        )
    try:
        if 名称 == "cha_caigoue":
            供应商 = (
                参数字典.get("供应商名称")
                or 参数字典.get("supplier_name")
                or 参数字典.get("name")
            )
            结果 = 查采购额(供应商)
        elif 名称 == "suan_kucun_zhouzhuan":
            月份 = 参数字典.get("月份", 参数字典.get("month"))
            结果 = 算库存周转(月份)
        else:
            结果 = 工具函数表[名称](**参数字典)
        return True, 结果
    except Exception:
        return False, traceback.format_exc()


def 用代码修正再执行(client, 工具名, 参数字典, 报错信息):
    """
    工具执行抛错时：把报错发给 DeepSeek，请它输出修正后的 Python 再跑一次（最多 1 次）。
    使用独立请求，避免在 assistant.tool_calls 与 role=tool 之间插入其它消息导致接口校验失败。
    """
    修正请求 = (
        f"刚才本地执行工具「{工具名}」时报错了，参数为：{json.dumps(参数字典, ensure_ascii=False)}。\n"
        "请根据报错修正后，只输出一段完整可执行的 Python 代码（可用 markdown 的 python 代码块），不要解释。\n"
        "约定：已有变量 采购额表（供应商→采购总额的 dict）、"
        "库存表（月份int→(年销售成本万元, 平均库存万元) 的 dict）；"
        "把最终给用户看的结果放进变量 result，或用 print 打印。\n"
        "无法从给定数据得出时必须明确说明，不许编造。涉及运算必须在 result 里写出算式。\n"
        f"报错信息如下：\n{报错信息}"
    )
    响应 = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": 系统提示},
            {"role": "user", "content": 修正请求},
        ],
        temperature=TEMPERATURE,
    )
    回复 = 响应.choices[0].message.content or ""

    代码 = 回复
    if "```" in 回复:
        起始 = 回复.find("```")
        片段 = 回复[起始 + 3 :]
        if 片段.lstrip().lower().startswith("python"):
            片段 = 片段.lstrip()[6:]
        结束 = 片段.find("```")
        代码 = 片段[:结束] if 结束 >= 0 else 片段
    代码 = 代码.strip()

    命名空间 = {"采购额表": 采购额表, "库存表": 库存表, "json": json}
    标准输出 = io.StringIO()
    try:
        with contextlib.redirect_stdout(标准输出):
            exec(代码, 命名空间)
        打印 = 标准输出.getvalue().strip()
        if "result" in 命名空间:
            输出 = 命名空间["result"]
            if not isinstance(输出, str):
                输出 = json.dumps(输出, ensure_ascii=False, default=str)
        elif 打印:
            输出 = 打印
        else:
            输出 = "修正代码已执行完成，但没有 result 也没有捕获到其它返回值。"
        return True, 输出
    except Exception:
        return False, traceback.format_exc()


def 序列化助手消息(message):
    """把 SDK 的 assistant message 转成可写入历史的 dict（含 tool_calls）。"""
    条目 = {"role": "assistant", "content": message.content or None}
    if message.tool_calls:
        条目["tool_calls"] = [
            {
                "id": 调用.id,
                "type": "function",
                "function": {
                    "name": 调用.function.name,
                    "arguments": 调用.function.arguments or "{}",
                },
            }
            for 调用 in message.tool_calls
        ]
    return 条目


def 处理工具轮次(client, messages, message):
    """
    执行本轮模型返回的全部 tool_calls，把结果作为 role=tool 回填历史。
    某个工具函数抛错时，把报错交给模型修正代码再跑一次（每个工具最多 1 次）。
    """
    messages.append(序列化助手消息(message))
    for 调用 in message.tool_calls:
        名称 = 调用.function.name
        try:
            参数字典 = 取出参数(调用.function.arguments)
            解析成功 = True
            解析错误 = ""
        except Exception:
            解析成功 = False
            参数字典 = {}
            解析错误 = traceback.format_exc()

        if not 解析成功:
            成功, 执行信息 = False, 解析错误
        else:
            成功, 执行信息 = 执行工具函数(名称, 参数字典)

        # 执行报错：发给 DeepSeek 修正代码后再跑一次
        if not 成功:
            成功, 执行信息 = 用代码修正再执行(
                client, 名称, 参数字典, 执行信息
            )
            if not 成功:
                执行信息 = json.dumps(
                    {
                        "成功": False,
                        "说明": "工具执行失败，且修正代码重试 1 次后仍然失败。",
                        "报错": 执行信息,
                    },
                    ensure_ascii=False,
                )

        messages.append(
            {
                "role": "tool",
                "tool_call_id": 调用.id,
                "content": 执行信息,
            }
        )


def 调用并处理工具(client, messages):
    """
    带完整历史调用 DeepSeek。若返回 tool_calls，则本地执行工具并回填，再继续请求，
    直到得到普通文本回复。工具循环设上限，避免死循环。
    """
    最多工具轮 = 8
    for _ in range(最多工具轮):
        响应 = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            tools=工具定义,
            tool_choice="auto",
        )
        message = 响应.choices[0].message
        if not message.tool_calls:
            文本 = message.content or ""
            messages.append({"role": "assistant", "content": 文本})
            return 文本
        处理工具轮次(client, messages, message)

    兜底 = "工具调用轮次过多，已停止。请换种方式提问，或把问题拆成更小的步骤。"
    messages.append({"role": "assistant", "content": 兜底})
    return 兜底


def main():
    """启动命令行多轮对话：完整历史 + 轮次计数，输入「退出」结束。"""
    if not API_KEY or API_KEY.startswith("在这里"):
        print("请先在文件顶部的 API_KEY 里填入你的 DeepSeek 密钥。")
        return

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 消息列表：第一项是系统设定，之后追加本轮及全部历史（含 tool 回填与纠错）
    messages = [{"role": "system", "content": 系统提示}]

    # 已完成的用户-助手对话轮数（不含 system，不含工具回填和代码纠错的内部往返）
    round_count = 0

    print("采购员助手已启动（Function Calling）。输入「退出」结束程序。")
    print("工具：查采购额、算库存周转。无须工具的问题会直接回答。")
    print("-" * 40)

    while True:
        # 读取本轮用户输入；去掉首尾空白避免误判
        user_input = input("你：").strip()

        if user_input == "退出":
            print("已结束对话，再见。")
            break

        if not user_input:
            print("请输入内容，或输入「退出」结束。")
            continue

        # 把本轮用户话追加进历史，后续请求始终带上完整上下文
        messages.append({"role": "user", "content": user_input})

        助手回复 = 调用并处理工具(client, messages)

        # 成功完成本轮用户提问后计数加一（工具调用与纠错不计为新的一轮）
        round_count += 1

        print(f"助手：{助手回复}")
        print(f"当前对话已进行{round_count}轮")


if __name__ == "__main__":
    main()
