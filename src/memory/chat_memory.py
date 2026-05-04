# memory/chat_memory.py
from langchain_core.chat_history import InMemoryChatMessageHistory

store = {}

def get_session_history(session_id: str):
    if session_id not in store:
        store[session_id] = InMemoryChatMessageHistory()
    return store[session_id]

def build_messages_with_history(query: str, session_id: str):
    history = get_session_history(session_id)
    messages = []

    for msg in history.messages:
        role = msg.type  # 可能是 human / ai

        # ========== 修复在这里 ==========
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        # ===============================

        messages.append({"role": role, "content": msg.content})

    messages.append({"role": "user", "content": query})
    return messages, history

def save_to_history(query: str, answer: str, history):
    history.add_user_message(query)
    history.add_ai_message(answer)