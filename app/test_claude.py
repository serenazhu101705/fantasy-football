import os

from dotenv import load_dotenv
from anthropic import Anthropic


load_dotenv()

api_key = os.getenv("ANTHROPIC_API_KEY")

if not api_key:
    raise RuntimeError("ANTHROPIC_API_KEY not found in .env")


client = Anthropic(api_key=api_key)

response = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=100,
    messages=[
        {
            "role": "user",
            "content": "Say exactly: Claude connection successful.",
        }
    ],
)

print(response.content[0].text)