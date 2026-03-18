
import logging
import os
import json
from datetime import datetime, timedelta
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler
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
    scopes = ["https://www.googleapis.com/auth/calendar"]
    if not GOOGLE_CREDENTIALS_FILE_CONTENT:
        logger.error("GOOGLE_CREDENTIALS_JSON environment variable is not set.")
        raise ValueError("Google Calendar credentials not configured.")
    if not CALENDAR_ID:
        logger.error("GOOGLE_CALENDAR_ID environment variable is not set.")
        raise ValueError("Google Calendar ID not configured.")
    try:
        creds_info = json.loads(GOOGLE_CREDENTIALS_FILE_CONTENT)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding GOOGLE_CREDENTIALS_JSON: {e}")
        raise ValueError(f"Invalid Google Calendar credentials JSON: {e}")
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
    service = build("calendar", "v3", credentials=creds)
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
To book a service, the customer will provide:
1. A brief description of the problem/task.
2. A photo of the problem.
3. The approximate address (street name).
4. Preferred date and time for the service.
5. Customer's full name.
6. Customer's phone number.

Once you have ALL the necessary information, you will summarize it for the customer and ask for confirmation. 
Upon confirmation, you will call the `create_calendar_event` tool to book the appointment. 
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
                    "description": {"type": "string", "description": "Details: service, phone, photo URL, address, etc."},
                    "start_time": {"type": "string", "description": "ISO format start time (e.g., 2026-03-20T14:00:00Z)"},
                    "duration_minutes": {"type": "integer", "description": "Estimated duration, default 60"}
                },
                "required": ["summary", "description", "start_time"]
            }
        }
    }
]

# State management for general conversation
conversation_history = {}

# States for the booking conversation handler
DESCRIPTION, PHOTO, ADDRESS, DATETIME, NAME, PHONE, CONFIRM_BOOKING = range(7)

# Booking data storage per user
user_booking_data = {}

def create_calendar_event(summary, description, start_time, duration_minutes=60):
    try:
        service = get_calendar_service()
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Pacific/Honolulu"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Pacific/Honolulu"},
        }
        
        event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f"Success: Event created. Link: {event.get("htmlLink")}"
    except Exception as e:
        logger.error(f"Calendar Error: {e}")
        return f"Error creating calendar event: {str(e)}"

async def start_command(update: Update, context) -> None:
    user = update.effective_user
    msg = (f"Hi {user.first_name}! I\'m Checkrtbot, your Hawaii Big Island handyman assistant. "
           "I can give quotes or book repairs. How can I help?")
    await update.message.reply_text(msg)
    conversation_history[user.id] = [{"role": "system", "content": SYSTEM_PROMPT.format(current_time=datetime.now().isoformat())}]

async def book_service(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_booking_data[user_id] = {}
    await update.message.reply_text(
        "Okay, let\'s book a service! First, please provide a brief description of the problem or task you need help with."
    )
    return DESCRIPTION

async def get_description(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_booking_data[user_id]["description"] = update.message.text
    await update.message.reply_text(
        "Got it! Now, please send a photo of the problem. This helps us understand the issue better."
    )
    return PHOTO

async def get_photo(update: Update, context) -> int:
    user_id = update.effective_user.id
    if update.message.photo:
        # Get the largest photo
        photo_file = await update.message.photo[-1].get_file()
        photo_path = f"/tmp/{photo_file.file_id}.jpg"
        await photo_file.download_to_drive(photo_path)
        
        # In a real deployment, you\'d integrate with a cloud storage service like S3, Google Cloud Storage, etc.
        # For this simulation, we\'ll just store the local path and indicate it needs manual upload.
        photo_url = f"file://{photo_path}" # Placeholder for actual upload URL
        user_booking_data[user_id]["photo_url"] = photo_url
        await update.message.reply_text(
            "Thanks for the photo! Next, please provide the approximate address (street name) where the service is needed."
        )
        return ADDRESS
    else:
        await update.message.reply_text("Please send a photo, not just text.")
        return PHOTO

async def get_address(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_booking_data[user_id]["address"] = update.message.text
    await update.message.reply_text(
        "Thank you. What is your preferred date and time for the service? (e.g., 'March 20th at 2 PM')"
    )
    return DATETIME

async def get_datetime(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_booking_data[user_id]["preferred_datetime"] = update.message.text
    await update.message.reply_text(
        "And what is your full name, please?"
    )
    return NAME

async def get_name(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_booking_data[user_id]["customer_name"] = update.message.text
    await update.message.reply_text(
        "Finally, what is your phone number?"
    )
    return PHONE

async def get_phone(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_booking_data[user_id]["phone_number"] = update.message.text
    
    booking_info = user_booking_data[user_id]
    confirmation_message = (
        "Please confirm your booking details:\n\n"
        f"Problem: {booking_info.get(\'description\')}\n"
        f"Photo: {booking_info.get(\'photo_url\', \'N/A\')}\n"
        f"Address: {booking_info.get(\'address\', \'N/A\')}\n"
        f"Preferred Date/Time: {booking_info.get(\'preferred_datetime\', \'N/A\')}\n"
        f"Customer Name: {booking_info.get(\'customer_name\', \'N/A\')}\n"
        f"Phone Number: {booking_info.get(\'phone_number\', \'N/A\')}\n\n"
        "Is this correct? (Yes/No)"
    )
    await update.message.reply_text(confirmation_message)
    return CONFIRM_BOOKING

async def confirm_booking(update: Update, context) -> int:
    user_id = update.effective_user.id
    user_response = update.message.text.lower()

    if user_response == "yes":
        booking_info = user_booking_data[user_id]
        
        # Prepare data for Google Calendar event
        summary = f"Handyman Service: {booking_info.get(\'description\', \'No description\')}"
        description = (
            f"Problem: {booking_info.get(\'description\', \'N/A\')}\n"
            f"Location: {booking_info.get(\'address\', \'N/A\')}\n"
            f"Photo URL: {booking_info.get(\'photo_url\', \'N/A\')}\n"
            f"Preferred Date/Time: {booking_info.get(\'preferred_datetime\', \'N/A\')}\n"
            f"Customer Name: {booking_info.get(\'customer_name\', \'N/A\')}\n"
            f"Phone Number: {booking_info.get(\'phone_number\', \'N/A\')}\n"
            f"Telegram User ID: {user_id}"
        )
        
        # Attempt to parse preferred_datetime, default to 1 hour from now if parsing fails
        try:
            # This is a simplified parsing. A robust solution would use a more advanced date/time parser.
            # For now, we expect a somewhat structured input like 'March 20th at 2 PM'
            # This part will likely need refinement for real-world usage.
            # Example: 
            # For now, let's set it for 1 hour from now.
            start_time_obj = datetime.now() + timedelta(hours=1)
            start_time = start_time_obj.isoformat() + "Z"
        except Exception as e:
            logger.warning(f"Could not parse preferred_datetime 
{booking_info.get("preferred_datetime")}: {e}. Defaulting to 1 hour from now.")
            start_time_obj = datetime.now() + timedelta(hours=1)
            start_time = start_time_obj.isoformat() + "Z"
        
        # Call the create_calendar_event function directly (simulating tool call)
        await update.message.reply_text("Processing your booking... Please wait.")
        calendar_result = create_calendar_event(summary, description, start_time)
        
        if "Success" in calendar_result:
            await update.message.reply_text(f"Booking confirmed! {calendar_result}")
        else:
            await update.message.reply_text(f"I encountered a technical problem with booking: {calendar_result}. Please try again later or contact support.")
        
        # Clear booking data
        del user_booking_data[user_id]
        return ConversationHandler.END
    elif user_response == "no":
        await update.message.reply_text("Booking cancelled. You can start again with /book.")
        del user_booking_data[user_id]
        return ConversationHandler.END
    else:
        await update.message.reply_text("Please reply \'Yes\' or \'No\'.")
        return CONFIRM_BOOKING

async def cancel_booking(update: Update, context) -> int:
    user_id = update.effective_user.id
    if user_id in user_booking_data:
        del user_booking_data[user_id]
    await update.message.reply_text("Booking process cancelled.")
    return ConversationHandler.END

async def handle_message(update: Update, context) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT.format(current_time=datetime.now().isoformat())}]
    
    conversation_history[user_id].append({"role": "user", "content": user_message})

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
    
    # General commands and message handler
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Conversation handler for booking
    booking_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("book", book_service)],
        states={
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_datetime)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            CONFIRM_BOOKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)],
        },
        fallbacks=[CommandHandler("cancel", cancel_booking)],
    )
    application.add_handler(booking_conv_handler)

    application.run_polling()

if __name__ == "__main__":
    main()
