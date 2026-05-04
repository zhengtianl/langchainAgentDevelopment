import os
from dotenv import load_dotenv
from openai import OpenAI
from src.memory.chat_memory import build_messages_with_history, save_to_history

# 加载环境
load_dotenv()

# 初始化通义千问
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# 流式思考 + 多轮记忆
def stream_with_memory(query: str, session_id="default_user"):
    # 从 memory 模块获取带历史的消息
    messages, history = build_messages_with_history(query, session_id)

    # 流式请求
    stream = client.chat.completions.create(
        model="qwen3.6-plus",
        messages=messages,
        stream=True,
        extra_body={"enable_thinking": True}
    )

    is_answering = False
    full_answer = ""
    print("\n" + "=" * 20 + "思考过程" + "=" * 20)

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # 思考
        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
            print(delta.reasoning_content, end="", flush=True)

        # 回答
        if hasattr(delta, "content") and delta.content:
            if not is_answering:
                print("\n" + "=" * 20 + "完整回复" + "=" * 20)
                is_answering = True
            print(delta.content, end="", flush=True)
            full_answer += delta.content

    # 保存到记忆
    save_to_history(query, full_answer, history)
    print("\n")

if __name__ == "__main__":
    # 多轮对话测试（自动记忆上下文）
    stream_with_memory("什么是 LLM-as-a-Judge？")
    stream_with_memory("它能用来评测AI Agent吗？")
    stream_with_memory("那怎么搭建评测集？")