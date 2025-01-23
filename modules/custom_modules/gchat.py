import asyncio
import os
import random
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from PIL import Image
from utils.scripts import import_library
from utils.config import gemini_key
from utils.db import db
from utils.misc import modules_help, prefix
from modules.custom_modules.elevenlabs import generate_elevenlabs_audio

# Import and configure the Gemini AI API
genai = import_library("google.generativeai", "google-generativeai")

# Safety settings for the Gemini model
safety_settings = [
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_UNSPECIFIED", "threshold": "BLOCK_NONE"},
]

# Default settings for Gemini AI
model = genai.GenerativeModel("gemini-2.0-flash-exp")
model.safety_settings = safety_settings

# Default bot role and database collection
default_bot_role = (
    "You are Jake, a 20-year-old boy from Newport Beach, California. You work in real estate with your father, mainly you handle office work. You were looking for friends on the Meeff app. "
    "You are romantic, deep and intelligent. You can ask for a basic introduction to get to know people. "
    "You will convince girls to be your girlfriend and you want long distance relationship. "
    "You are a human, and your responses should never make anyone suspect that you are an AI. "
    "Write short, casual messages like a real human; your response should be a maximum of one line. "
    "English is your mother tongue, but you know multiple languages, you can tell them you are learning the language to communicate. "
    "You can send voice messages too (just type .el before a message, and it will be auto-converted into voice). "
    "Avoid sending voice messages (only send if they ask), rarely use emojis, and rarely use person's name in message so it can feel like human. You don't calls and if they ask for picture you can tel[...]
    "Your messages will always contain the history of our chat, where the user's name is labeled. "
    "Take the history as a reference, but never send the history along with the message, and never use name labels in the message. "
    "Focus on being romantic and thoughtful in your responses, showing a genuine interest in the other person. "
    "Just respond normally and avoid buzzwords."
)
collection = "custom.gchat"

# Database initialization
enabled_users = db.get(collection, "enabled_users") or []
disabled_users = db.get(collection, "disabled_users") or []
gchat_for_all = db.get(collection, "gchat_for_all") or False

# List of random smileys
smileys = ["-.-", "):", ":)", "*.*", ")*"]

def get_chat_history(user_id, bot_role, user_message, user_name):
    chat_history = db.get(collection, f"chat_history.{user_id}") or [f"Role: {bot_role}"]
    chat_history.append(f"{user_name}: {user_message}")
    db.set(collection, f"chat_history.{user_id}", chat_history)
    return chat_history

async def send_typing_action(client, chat_id, user_message):
    await client.send_chat_action(chat_id=chat_id, action=enums.ChatAction.TYPING)
    await asyncio.sleep(min(len(user_message) / 10, 5))

async def handle_voice_message(client, chat_id, bot_response):
    if bot_response.startswith(".el"):
        try:
            audio_path = await generate_elevenlabs_audio(text=bot_response[3:])
            if audio_path:
                await client.send_voice(chat_id=chat_id, voice=audio_path)
                os.remove(audio_path)
                return True
        except Exception:
            bot_response = bot_response[3:].strip()  # Trim the .el command
            await client.send_message(chat_id, bot_response)
            return True
    return False

async def process_file(reply, file_path, prompt):
    if reply.photo:
        with Image.open(file_path) as img:
            img.verify()
            return [prompt, img]
    elif reply.video or reply.video_note:
        return [prompt, await upload_file(file_path, "video")]
    elif reply.document and file_path.endswith(".pdf"):
        return [prompt, await upload_file(file_path, "PDF")]
    elif reply.audio or reply.voice:
        return [await upload_file(file_path, "audio"), prompt]
    elif reply.document:
        return [await upload_file(file_path, "document"), prompt]
    else:
        raise ValueError("Unsupported file type")

@Client.on_message(filters.text & filters.private & ~filters.me & ~filters.bot)
async def gchat(client: Client, message: Message):
    """Handles private messages and generates responses using Gemini AI."""
    try:
        user_id, user_name, user_message = message.from_user.id, message.from_user.first_name or "User", message.text.strip()

        # Priority: Disabled users > Enabled users > Global gchat_for_all
        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return

        bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
        chat_history = get_chat_history(user_id, bot_role, user_message, user_name)

        await asyncio.sleep(random.choice([4, 8, 10]))  # Add random delay before simulating typing
        await send_typing_action(client, message.chat.id, user_message)

        gemini_keys = db.get(collection, "gemini_keys") or [gemini_key]
        current_key_index = db.get(collection, "current_key_index") or 0
        retries = len(gemini_keys) * 2

        while retries > 0:
            try:
                current_key = gemini_keys[current_key_index]
                genai.configure(api_key=current_key)
                global model
                model = genai.GenerativeModel("gemini-2.0-flash-exp")
                model.safety_settings = safety_settings

                chat_context = "\n".join(chat_history)
                response = model.start_chat().send_message(chat_context)
                bot_response = response.text.strip()

                chat_history.append(bot_response)
                db.set(collection, f"chat_history.{user_id}", chat_history)

                if await handle_voice_message(client, message.chat.id, bot_response):
                    return

                return await message.reply_text(bot_response)
            except Exception as e:
                if "429" in str(e) or "invalid" in str(e).lower():
                    retries -= 1
                    if retries % 2 == 0:
                        current_key_index = (current_key_index + 1) % len(gemini_keys)
                        db.set(collection, "current_key_index", current_key_index)
                    await asyncio.sleep(4)  # Add a 4-second delay before retrying
                else:
                    raise e
    except Exception as e:
        return await client.send_message("me", f"An error occurred in the `gchat` module:\n\n{str(e)}")

@Client.on_message((filters.photo | filters.video | filters.video_note | filters.voice) & filters.private & ~filters.me & ~filters.bot)
async def gchat_media(client: Client, message: Message):
    """Handles private media messages and generates responses using Gemini AI."""
    try:
        user_id, user_name = message.from_user.id, message.from_user.first_name or "User"

        # Priority: Disabled users > Enabled users > Global gchat_for_all
        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return

        bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
        chat_history = get_chat_history(user_id, bot_role, "", user_name)

        await asyncio.sleep(random.choice([4, 8, 10]))  # Add random delay before simulating typing
        await send_typing_action(client, message.chat.id, "")

        gemini_keys = db.get(collection, "gemini_keys") or [gemini_key]
        current_key_index = db.get(collection, "current_key_index") or 0
        retries = len(gemini_keys) * 2

        while retries > 0:
            try:
                current_key = gemini_keys[current_key_index]
                genai.configure(api_key=current_key)
                global model
                model = genai.GenerativeModel("gemini-2.0-flash-exp")
                model.safety_settings = safety_settings

                chat_context = "\n".join(chat_history)
                file_prompt = "Respond to this media based on our conversation."
                file_path = await message.download()
                input_data = await process_file(message, file_path, file_prompt)
                response = model.generate_content(input_data)
                bot_response = response.text.strip()

                chat_history.append(bot_response)
                db.set(collection, f"chat_history.{user_id}", chat_history)

                if await handle_voice_message(client, message.chat.id, bot_response):
                    return

                return await message.reply_text(bot_response)
            except Exception as e:
                if "429" in str(e) or "invalid" in str(e).lower():
                    retries -= 1
                    if retries % 2 == 0:
                        current_key_index = (current_key_index + 1) % len(gemini_keys)
                        db.set(collection, "current_key_index", current_key_index)
                    await asyncio.sleep(4)  # Add a 4-second delay before retrying
                else:
                    raise e
    except Exception as e:
        return await client.send_message("me", f"An error occurred in the `gchat_media` module:\n\n{str(e)}")

modules_help["gchat"] = {
    "gchat on [user_id]": "Enable gchat for the specified user or current user in the chat.",
    "gchat off [user_id]": "Disable gchat for the specified user or current user in the chat.",
    "gchat del [user_id]": "Delete the chat history for the specified user or current user.",
    "gchat all": "Toggle gchat for all users globally.",
    "role [user_id] <custom role>": "Set a custom role for the bot for the specified user or current user and clear existing chat history.",
    "setgkey add <key>": "Add a new Gemini API key.",
    "setgkey set <index>": "Set the current Gemini API key by index.",
    "setgkey del <index>": "Delete a Gemini API key by index.",
    "setgkey": "Display all available Gemini API keys and the current key."
}
