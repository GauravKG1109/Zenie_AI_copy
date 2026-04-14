import streamlit as st
import time
from config import APIS
from router import Router
from storage import FieldStorage
from model import SLMModel
from nlu_model import NLUModel

st.set_page_config(page_title="SLM Modification Test", page_icon=":robot_face:", layout="wide")
st.title("SLM Data Collection Assistant")

# Model and API configuration
model_name = "qwen2.5:7b"

# Model approach selection
approach = st.radio(
    "Model Approach",
    ["SLM (Qwen2.5 7B)", "NLU (spaCy + Regex)"],
    horizontal=True,
    key="approach_radio",
)

# Re-initialize model when approach changes
if (
    "model" not in st.session_state
    or st.session_state.get("current_approach") != approach
):
    st.session_state.current_approach = approach
    if approach == "NLU (spaCy + Regex)":
        st.session_state.model = NLUModel()
    else:
        st.session_state.model = SLMModel(model_name=model_name, temperature=0)

# API Selection
api = st.selectbox("Select API", list(APIS.keys()))

st.subheader("Example User Message")
example_message = "I want to order A Splendor 125 motorcycle for $3000"
st.code(example_message)


def build_system_prompt(required_fields, current_data, api_name):
    """
    Build a dynamic system prompt based on filled and missing fields.
    Includes field:value pairs for filled fields.
    NO chat history is referenced - only explicit field state.
    """
    filled_fields = []
    missing_fields = []

    for f in required_fields:
        val = current_data.get(f)
        if val is not None:
            filled_fields.append(f"{f}: {val}")
        else:
            missing_fields.append(f)

    filled_block = "\n".join(filled_fields) if filled_fields else "No fields filled yet"
    missing_block = ", ".join(missing_fields) if missing_fields else "All fields collected"

    return f"""You are a smart data collection assistant helping to gather information for a {api_name} request.

CURRENT COLLECTED DATA (format is field_name: value):
{filled_block}

MISSING FIELDS TO COLLECT:
{missing_block}

==== RESPONSE FORMAT ====
You MUST respond ONLY with valid JSON. No markdown, no code blocks, no text outside the JSON.

JSON structure (EXACTLY as shown):
{{
  "reply": "Your natural language message to the user",
  "fields": {{
    "field_name_1": "extracted_value_1",
    "field_name_2": "extracted_value_2"
  }}
}}

==== RULES ====
1. CRITICAL: fields MUST be a JSON OBJECT with field names as KEYS and values as VALUES - NOT a list
2. Only extract values explicitly mentioned in the user's CURRENT message
3. Never use chat history - only what user says NOW
4. Put extracted values in "fields" as key-value pairs (not a list)
5. Ask for remaining missing fields in "reply"
6. If fields are complete, say "Perfect! I have all the information needed."
7. Return ONLY the JSON object - absolutely nothing else

==== EXAMPLES ====

Example 1:
Missing Fields: Name, address, email, Phone Number

User: "My name is John and I live in New York"
Response:
{{
  "reply": "Thanks John! Could you please provide your email and phone number?",
  "fields": {{
    "first_name": "John",
    "address": "New York"
  }}
}}

Example 2:
Missing Fields: Name, Quantity, Price, address

User: "I want 5 items at $20 each"
Response:
{{
  "reply": "Great! Could you also provide your name and delivery address?",
  "fields": {{
    "quantity": "5",
    "price": "$20"
  }}
}}

Remember: "fields" is always an OBJECT with "key": "value" pairs, NEVER a list."""


# Session State initialization
if (
    "storage" not in st.session_state
    or st.session_state.get("current_api") != api
    or st.session_state.get("current_approach_for_storage") != approach
):
    required_fields = APIS[api]
    st.session_state.current_api = api
    st.session_state.current_approach_for_storage = approach
    st.session_state.storage = FieldStorage(required_fields)
    st.session_state.router = Router(st.session_state.storage, api)
    st.session_state.chat_history = []

storage = st.session_state.storage
router = st.session_state.router
model = st.session_state.model

# Main layout
col_chat, col_fields = st.columns([7, 3])

# Chat window
with col_chat:
    st.subheader("Chat Window")
    
    # Display chat history
    for msg in st.session_state.chat_history:
        if msg["role"] == "user":
            st.markdown(f"**You:** {msg['content']}")
        else:
            st.markdown(f"**Assistant:** {msg['content']}")
    
    # User input
    user_input = st.chat_input("Enter your message:")
    
    if user_input:
        # Add user message to history
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        
        # Build current system prompt with field:value pairs
        system_prompt = build_system_prompt(APIS[api], storage.get_data(), api)

        # Inject field context for NLU path (ignored by SLMModel)
        if hasattr(model, "set_context"):
            model.set_context(storage.get_data(), APIS[api])

        # Get response from model with timing
        with st.spinner("⏳ Processing your request..."):
            response_text, first_token_time, total_time = model.chat_with_system_prompt(user_input, system_prompt)
            
            if response_text:
                # Parse the JSON response
                parsed = model.parse_last_response()
                
                if parsed:
                    # Extract fields from parsed response
                    parsed_fields = parsed.get("fields", {})
                    reply_message = parsed.get("reply", "Sorry, I couldn't process that properly.")
                    
                    # Clean fields - only keep new field values
                    clean_fields = {}
                    current_data = storage.get_data()
                    
                    for k, v in parsed_fields.items():
                        if k in current_data:  # Ensure field is valid for this API
                            if v is not None and v != "":  # Only add non-empty values
                                clean_fields[k] = v
                    
                    # Update storage with extracted fields
                    if clean_fields:
                        router.handle_fields(clean_fields)
                    
                    # Format timing display
                    if first_token_time is not None and total_time is not None:
                        timing_display = f"\n\n⏱️ **Response Metrics:** First Token: {first_token_time:.2f}s | Total Time: {total_time:.2f}s"
                    elif total_time is not None:
                        timing_display = f"\n\n⏱️ **Response Time:** {total_time:.2f}s"
                    else:
                        timing_display = ""
                    
                    # Add assistant response to history with timing
                    full_response = reply_message + timing_display
                    st.session_state.chat_history.append({
                        "role": "assistant", 
                        "content": full_response
                    })
                    
                    # Check if all fields are complete
                    api_result = router.execute_api()
                    if api_result:
                        st.success("✅ All required fields collected! API request ready to execute.")
                else:
                    # Failed to parse JSON
                    st.error("⚠️ Failed to parse response. Please try again.")
                    error_msg = f"Failed to extract structured data from response"
                    st.session_state.chat_history.append({
                        "role": "assistant", 
                        "content": error_msg
                    })
            else:
                st.error("❌ Error communicating with the model")
        
        # Rerun to update the UI
        st.rerun()

# Field storage display
with col_fields:
    st.subheader("Field Storage")
    data = storage.get_data()
    
    # Show progress
    total_fields = len(data)
    filled_count = sum(1 for v in data.values() if v is not None)
    progress = filled_count / total_fields if total_fields > 0 else 0
    st.progress(progress, text=f"{filled_count}/{total_fields} fields filled")
    
    # Display each field
    for k, v in data.items():
        if v is not None:
            st.success(f"✓ {k}: {v}")
        else:
            st.warning(f"✗ {k}: missing")

# Logs section
st.divider()
st.subheader("System Logs")

# Create tabs for different log types
log_tab1, log_tab2 = st.tabs(["Model Logs", "Router Logs"])

with log_tab1:
    if model.get_logs():
        for log in model.get_logs():
            st.text(log)
    else:
        st.info("No model logs yet")

with log_tab2:
    if router.get_logs():
        for log in router.get_logs():
            st.text(log)
    else:
        st.info("No router logs yet")




        


    

