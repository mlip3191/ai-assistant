from collections import deque

class ConversationMemory:
    def __init__(self, max_turns: int = 20):
        self.history = deque(maxlen=max_turns * 2)  # user + assistant pairs

    def add_user(self, message: str):
        self.history.append({"role": "user", "content": message})

    def add_assistant(self, message: str):
        self.history.append({"role": "assistant", "content": message})

    def get_history(self) -> list:
        return list(self.history)

    def clear(self):
        self.history.clear()

# Global session memory (per container instance)
memory = ConversationMemory()
