# 随机性：数值越低回答越稳定，越高越发散，方便在文件顶部直接改
TEMPERATURE = 0.1

# DeepSeek 接口地址（OpenAI 兼容协议）
BASE_URL = "https://api.deepseek.com"

# 对话模型名称
MODEL = "deepseek-chat"

# 在这里填入你的 DeepSeek API Key
API_KEY = "sk-e9fef2788a6f4e919a70dc346909b051"

# 系统人设：每轮请求都会带上这条身份设定
SYSTEM_PROMPT = "你是一个资深企业信息化项目顾问助手"

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI


def 创建模型():
    """用 ChatOpenAI 走 OpenAI 兼容协议，实际请求发往 DeepSeek。"""
    return ChatOpenAI(
        model=MODEL,
        temperature=TEMPERATURE,
        api_key=API_KEY,
        base_url=BASE_URL,
    )


def 创建对话链(模型):
    """
    组装 LCEL 管道：提示模板 | 模型 | 字符串解析器。
    ChatPromptTemplate 含系统人设、历史占位、本轮用户问题。
    StrOutputParser 把模型消息对象解析成普通字符串，便于打印和写入历史。
    """
    # 对话模板：system 固定人设；history 由程序注入多轮消息；question 是本轮用户输入
    提示模板 = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ]
    )
    # 输出解析器：得到程序可直接使用的字符串（若模型输出 JSON 文本，可用 json.loads 再解析）
    解析器 = StrOutputParser()
    # 用 | 把「模板 → 模型 → 解析器」串成一条 chain
    return 提示模板 | 模型 | 解析器


def main():
    """启动命令行多轮对话：保留历史与轮次计数，输入「退出」结束。"""
    if not API_KEY or "填入" in API_KEY:
        print("请先在文件顶部的 API_KEY 里填入你的 DeepSeek 密钥。")
        return

    # 初始化模型对象（temperature / base_url / model 均取自文件顶部变量）
    模型 = 创建模型()
    # LCEL 链：模板 | 模型 | 解析器
    chain = 创建对话链(模型)

    # 手动管理 messages：保存多轮 HumanMessage / AIMessage，作为 Memory 使用
    history = []
    # 已完成的用户-助手对话轮数（不含 system）
    round_count = 0

    print("企业信息化顾问助手已启动。输入「退出」结束程序。")
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

        # 调用 LCEL 链：带上历史消息和本轮问题，得到解析后的字符串回复
        助手回复 = chain.invoke(
            {
                "question": user_input,
                "history": history,
            }
        )

        # 把本轮问答写入历史，下一轮请求会通过 MessagesPlaceholder 带上
        history.append(HumanMessage(content=user_input))
        history.append(AIMessage(content=助手回复))

        # 成功完成本轮后计数加一
        round_count += 1

        print(f"助手：{助手回复}")
        print(f"当前对话已进行{round_count}轮")


if __name__ == "__main__":
    main()
