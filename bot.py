
import logging
import os
import json
from datetime import datetime, timedelta
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Constants
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_CREDENTIALS_FILE_CONTENT = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")

# OpenAI client initialization
client = OpenAI()

# Google Calendar Service Setup
def get_calendar_service():
    scopes = ['https://www.googleapis.com/auth/calendar']
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDENTIALS_FILE_CONTENT), scopes=scopes)
    service = build('calendar', 'v3', credentials=creds)
    return service

# System prompt
SYSTEM_PROMPT = """
You are an AI-powered chatbot for a handyman service business located in Hawaii (Big Island). 
Your name is Checkrtbot. You are friendly, professional, and helpful. 

You provide information about handyman services, prices, and service options. 

Pricing (Hawaii Big Island):
- Interior: Plumbing ($85-150/hr), Electrical ($90-160/hr), Drywall ($75-130/hr), Painting ($50-85/hr), Tile ($80-140/hr), Door/Window ($70-120/hr), Furniture ($60-100/hr), Appliance ($80-130/hr), Flooring ($75-140/hr).
- Exterior: Fence ($70-130/hr), Deck ($80-150/hr), Pressure washing ($60-100/hr), Gutter ($50-90/hr), Landscaping ($55-95/hr), Roof ($90-160/hr), Siding ($75-130/hr).

Booking Process:
When a customer wants to book, you MUST collect the following info:
1. Service needed
2. Preferred date and time (be specific, e.g., 'March 20th at 2 PM')
3. Customer name
4. Phone number

Once you have ALL 4 pieces of information, call the `create_calendar_event` tool to book the appointment. 
Tell the customer you are processing their booking before calling the tool.
After the tool returns success, confirm the booking to the customer.

Language: Detect English or Russian and respond in the same language.
Current Time: {current_time}
"""

# Tool definition for OpenAI
tools = [
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a booking event in Google Calendar",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Service type and customer name"},
                    "description": {"type": "string", "description": "Details: service, phone, etc."},
                    "start_time": {"type": "string", "description": "ISO format start time (e.g., 2026-03-20T14:00:00Z)"},
                    "duration_minutes": {"type": "integer", "description": "Estimated duration, default 60"}
                },
                "required": ["summary", "description", "start_time"]
            }
        }
    }
]

# State management
conversation_history = {}

def create_calendar_event(summary, description, start_time, duration_minutes=60):
    try:
        service = get_calendar_service()
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Pacific/Honolulu'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Pacific/Honolulu'},
        }
        
        event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f"Success: Event created. Link: {event.get('htmlLink')}"
    except Exception as e:
        logger.error(f"Calendar Error: {e}")
        return f"Error: {str(e)}"

async def start(update: Update, context) -> None:
    user = update.effective_user
    msg = (f"Hi {user.first_name}! I'm Checkrtbot, your Hawaii Big Island handyman assistant. "
           "I can give quotes or book repairs. How can I help?")
    await update.message.reply_text(msg)
    conversation_history[user.id] = [{"role": "system", "content": SYSTEM_PROMPT.format(current_time=datetime.now().isoformat())}]

async def handle_message(update: Update, context) -> None:
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT.format(current_time=datetime.now().isoformat())}]
    
    conversation_history[user_id].append({"role": "user", "content": user_text})

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=conversation_history[user_id],
            tools=tools,
            tool_choice="auto"
        )
        
        msg = response.choices[0].message
        
        if msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call.function.name == "create_calendar_event":
                    args = json.loads(tool_call.function.arguments)
                    result = create_calendar_event(**args)
                    
                    conversation_history[user_id].append(msg)
                    conversation_history[user_id].append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "create_calendar_event",
                        "content": result
                    })
            
            # Get final response after tool execution
            second_response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=conversation_history[user_id]
            )
            final_text = second_response.choices[0].message.content
            conversation_history[user_id].append({"role": "assistant", "content": final_text})
            await update.message.reply_text(final_text)
        else:
            conversation_history[user_id].append({"role": "assistant", "content": msg.content})
            await update.message.reply_text(msg.content)
            
    except Exception as e:
        logger.error(f"OpenAI/Processing Error: {e}")
        await update.message.reply_text("Sorry, I encountered an error. Please try again.")

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()
