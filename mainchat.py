import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import os
import threading

# custom files
from Listen import listen, model
from Piper_tts import speak

class QwenChatbot:
    def __init__(self, model_name="Qwen/Qwen3-0.6B"):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(model_name).to(self.device)
        self.history = []

    def generate_response(self, user_input, stream=True):
        messages = self.history + [{"role": "user", "content": user_input}]

        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)
        response_ids = self.model.generate(**inputs, max_new_tokens=32768)[0][len(inputs.input_ids[0]):].tolist()
        response = self.tokenizer.decode(response_ids, skip_special_tokens=True)

        # Update history
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": response})

        return response

# Example Usage
if __name__ == "__main__":
    chatbot = QwenChatbot()
    print("Welcome to the Qwen Chatbot! You can ask questions or give commands. Use ctrl + c to exit, or say \"exit\".")
    user_input = listen(model)
    while user_input.lower() != "exit":
        response = chatbot.generate_response(user_input)
        print(f"Bot: {response}")
        speak(response)
        print("----------------------")
        user_input = listen(model)

