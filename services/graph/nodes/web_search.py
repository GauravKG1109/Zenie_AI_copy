from services.graph.state import GraphState
import logging
from anthropic import Anthropic
from tavily import TavilyClient
import os
from dotenv import load_dotenv

load_dotenv() # must be before initializing the clients to ensure environment variables are loaded
_client = Anthropic()
# Load environment variables


# Initialize the Tavily client
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

SYSTEM_PROMPT = """You are a helpful assistant that takes the user's query and relevant web search results 
    and provide a clear and concise answer to the user's query"""

# ---LangGraph node for web search---
def web_search_node(state: GraphState) -> dict:
    query = state.get("message", "")
    try:
        search_results = tavily_client.search(query=query, num_results=5)
        logging.info(f"Web search results for query '{query}': {search_results}")
        raw_result = search_results['results'][0]['content']
    except Exception as e:
        logging.error(f"Error during web search: {e}")
        return {"reply": "Sorry, I couldn't perform the web search at the moment."}

    claude_response = _client.messages.create(
        model      = "claude-haiku-4-5-20251001",
        max_tokens = 512,
        system     = SYSTEM_PROMPT,
        messages   = [
            {"role": "user", "content": f"User query: {query}\n\nWeb search results: {raw_result}\n\nPlease provide a clear and concise answer to the user's query based on the web search results."}
        ]
    )
    answer = claude_response.content[0].text.strip()
    return {"reply": answer}

