import json
import re
from ollama import chat


class SLMModel:
    def __init__(self, model_name='qwen2.5:7b', temperature=0):
        self.model_name = model_name
        self.temperature = temperature
        self.messages = []
        self.logs = []
        self.last_response = None
        self.last_parsed = None

    def chat_with_system_prompt(self, user_message, system_prompt):
        """
        Send a message to the model with a system prompt.
        NO chat history is included - only current message and system prompt.
        Returns tuple: (response_text, first_token_time, total_time)
        """
        import time
        
        # Build the messages list with system prompt only (no history)
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message}
        ]
        
        try:
            start_time = time.time()
            
            # Call ollama chat with streaming disabled
            response = chat(
                model=self.model_name,
                messages=messages,
                stream=False,
            )
            
            end_time = time.time()
            response_text = response['message']['content']
            total_time = end_time - start_time
            
            self.last_response = response_text
            
            # Log the interaction
            self.logs.append(f"USER: {user_message}")
            self.logs.append(f"ASSISTANT: {response_text}")
            self.logs.append(f"RESPONSE TIME: {total_time:.2f}s")
            
            return response_text, None, total_time
            
        except Exception as e:
            error_msg = f"Error calling model: {str(e)}"
            self.logs.append(error_msg)
            return None, None, None

    def stream_response(self, user_message, system_prompt):
        """
        Stream the response from the model with a system prompt.
        NO chat history is included - only current message and system prompt.
        Yields tuples: (token, first_token_time, total_time)
        """
        import time
        
        # Build the messages list with system prompt only (no history)
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message}
        ]
        
        try:
            start_time = time.time()
            first_token_time = None
            response_text = ""
            
            # Call ollama chat with streaming enabled
            for chunk in chat(
                model=self.model_name,
                messages=messages,
                stream=True,
            ):
                if first_token_time is None:
                    first_token_time = time.time() - start_time
                
                token = chunk['message']['content']
                response_text += token
                
                # Yield token with timing info
                yield token, first_token_time, None
            
            total_time = time.time() - start_time
            self.last_response = response_text
            
            # Final yield with complete timing
            yield None, first_token_time, total_time
            
            # Log the interaction
            self.logs.append(f"USER: {user_message}")
            self.logs.append(f"ASSISTANT: {response_text}")
            self.logs.append(f"FIRST TOKEN: {first_token_time:.2f}s | TOTAL: {total_time:.2f}s")
            
        except Exception as e:
            error_msg = f"Error in streaming: {str(e)}"
            self.logs.append(error_msg)
            raise

    def parse_last_response(self):
        """
        Parse the last response as JSON.
        Returns the parsed JSON object.
        """
        if self.last_response is None:
            return None
        
        try:
            # Try to extract JSON from the response
            # Look for JSON content between { and }
            json_match = re.search(r'\{.*\}', self.last_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                parsed = json.loads(json_str)
                self.last_parsed = parsed
                return parsed
            else:
                self.logs.append(f"No JSON found in response: {self.last_response}")
                return None
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON: {str(e)}"
            self.logs.append(error_msg)
            return None

    def get_logs(self):
        """Return all logs."""
        return self.logs

    def clear_logs(self):
        """Clear all logs."""
        self.logs = []
