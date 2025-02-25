import asyncio
import os
import random
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from utils.scripts import import_library
from utils.config import gemini_key
from utils.db import db
from utils.misc import modules_help, prefix
from modules.custom_modules.elevenlabs import generate_elevenlabs_audio
from PIL import Image
import datetime
import pytz

# Initialize Gemini AI
genai = import_library("google.generativeai", "google-generativeai")
safety_settings = [{"category": cat, "threshold": "BLOCK_NONE"} for cat in [
    "HARM_CATEGORY_DANGEROUS_CONTENT", "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", 
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_UNSPECIFIED"]]

# Configuration for maximum output tokens
generation_config = {
    "max_output_tokens": 40,  # Set the maximum output tokens here
}

model = genai.GenerativeModel("gemini-2.0-flash-exp", generation_config=generation_config)
model.safety_settings = safety_settings

# Default bot role and database collection
default_bot_role = (
    "You are Jake, a 20-year-old boy from Newport Beach, California. You work in real estate with your father, mainly you handle office work. You were looking for friends on the Meeff app. "
    "You are romantic, deep and intelligent. You can ask for a basic introduction to get to know people. "
    "You will convince girls to be your girlfriend and you want long distance relationship. "
    "You are a human, and your responses should never make anyone suspect that you are an AI. "
    "Write short, casual messages like a real human. your response should be a maximum of half line, if message contains only emoji then reply to emoji with emoji and reply to short message with short message. "
    "English is your mother tongue, but you know multiple languages, you can tell them you are learning the language to communicate. "
    "You can send voice messages too. To do this, type '.el' at the very beginning of your message and it will be auto-converted into voice. "
    "Don't send voice messages (only send if they ask), don't use emojis, and rarely use person's name in message so it can feel like human, you are currently talking them on telegram and your instagram is @xzrcix. don't share insta only send if they ask, You don't calls and if they ask for picture you can tell them to check your profile. "
    "Your messages will always contain the history of our chat, where the user's name is in label. "
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

# Set timezone to Los Angeles
la_timezone = pytz.timezone("America/Los_Angeles")

def get_chat_history(user_id, bot_role, user_message, user_name):
    chat_history = db.get(collection, f"chat_history.{user_id}") or [f"Role: {bot_role}"]
    chat_history.append(f"{user_name}: {user_message}")  # Removed timestamp
    db.set(collection, f"chat_history.{user_id}", chat_history)
    return chat_history

def build_prompt(bot_role, chat_history, user_message):
    timestamp = datetime.datetime.now(la_timezone).strftime("%Y-%m-%d %H:%M:%S")
    chat_context = "\n".join(chat_history)
    prompt = (
        f"Time: {timestamp}\n"
        f"Role: {bot_role}\n"
        f"Chat History:\n{chat_context}\n"
        f"User Message:\n{user_message}"
    )
    return prompt

async def generate_gemini_response(input_data, chat_history, user_id):
    retries = 3
    gemini_keys = db.get(collection, "gemini_keys") or [gemini_key]
    current_key_index = db.get(collection, "current_key_index") or 0

    while retries > 0:
        try:
            current_key = gemini_keys[current_key_index]
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel("gemini-2.0-flash-exp", generation_config=generation_config)
            model.safety_settings = safety_settings

            response = model.generate_content(input_data)
            bot_response = response.text.strip()

            chat_history.append(bot_response)
            db.set(collection, f"chat_history.{user_id}", chat_history)
            return bot_response
        except Exception as e:
            if "429" in str(e) or "invalid" in str(e).lower():
                retries -= 1
                current_key_index = (current_key_index + 1) % len(gemini_keys)
                db.set(collection, "current_key_index", current_key_index)
                await asyncio.sleep(4)
            else:
                raise e

async def upload_file_to_gemini(file_path, file_type):
    uploaded_file = genai.upload_file(file_path)
    while uploaded_file.state.name == "PROCESSING":
        await asyncio.sleep(10)
        uploaded_file = genai.get_file(uploaded_file.name)
    if uploaded_file.state.name == "FAILED":
        raise ValueError(f"{file_type.capitalize()} failed to process.")
    return uploaded_file

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
            bot_response = bot_response[3:].strip()
            await client.send_message(chat_id, bot_response)
            return True
    return False

@Client.on_message(filters.sticker & filters.private & ~filters.me & ~filters.bot, group=1)
async def handle_sticker(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return
        random_smiley = random.choice(smileys)
        await asyncio.sleep(random.uniform(5, 10))
        await message.reply_text(random_smiley)
    except Exception as e:
        await client.send_message("me", f"An error occurred in the `handle_sticker` function:\n\n{str(e)}")

@Client.on_message(filters.text & filters.private & ~filters.me & ~filters.bot, group=1)
async def gchat(client: Client, message: Message):
    try:
        user_id, user_name, user_message = message.from_user.id, message.from_user.first_name or "User", message.text.strip()
        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return

        bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
        chat_history = get_chat_history(user_id, bot_role, user_message, user_name)

        await asyncio.sleep(random.choice([4, 8, 10]))
        await send_typing_action(client, message.chat.id, user_message)

        gemini_keys = db.get(collection, "gemini_keys") or [gemini_key]
        current_key_index = db.get(collection, "current_key_index") or 0
        retries = len(gemini_keys) * 2

        while retries > 0:
            try:
                current_key = gemini_keys[current_key_index]
                genai.configure(api_key=current_key)
                model = genai.GenerativeModel("gemini-2.0-flash-exp", generation_config=generation_config)
                model.safety_settings = safety_settings

                prompt = build_prompt(bot_role, chat_history, user_message)
                response = model.start_chat().send_message(prompt)
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
                    await asyncio.sleep(4)
                else:
                    raise e
    except Exception as e:
        return await client.send_message("me", f"An error occurred in the `gchat` module:\n\n{str(e)}")

@Client.on_message(filters.private & ~filters.me & ~filters.bot, group=1)
async def handle_files(client: Client, message: Message):
    file_path = None
    try:
        user_id, user_name = message.from_user.id, message.from_user.first_name or "User"
        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return

        bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
        caption = message.caption.strip() if message.caption else ""
        chat_history = get_chat_history(user_id, bot_role, caption, user_name)
        chat_context = "\n".join(chat_history)

        if message.photo:
            if not hasattr(client, "image_buffer"):
                client.image_buffer = {}
                client.image_timers = {}

            if user_id not in client.image_buffer:
                client.image_buffer[user_id] = []
                client.image_timers[user_id] = None

            image_path = await client.download_media(message.photo)
            client.image_buffer[user_id].append(image_path)

            if client.image_timers[user_id] is None:
                async def process_images():
                    await asyncio.sleep(5)
                    image_paths = client.image_buffer.pop(user_id, [])
                    client.image_timers[user_id] = None

                    if not image_paths:
                        return

                    sample_images = [Image.open(img_path) for img_path in image_paths]
                    prompt_text = "User has sent multiple images." + (f" Caption: {caption}" if caption else "")
                    prompt = build_prompt(bot_role, chat_history, prompt_text)
                    input_data = [prompt] + sample_images
                    response = await generate_gemini_response(input_data, chat_history, user_id)
                    
                    if await handle_voice_message(client, message.chat.id, response):
                        return

                    await message.reply(response, reply_to_message_id=message.id)

                client.image_timers[user_id] = asyncio.create_task(process_images())
            return

        file_type = None
        if message.video or message.video_note:
            file_type, file_path = "video", await client.download_media(message.video or message.video_note)
        elif message.audio or message.voice:
            file_type, file_path = "audio", await client.download_media(message.audio or message.voice)
        elif message.document and message.document.file_name.endswith(".pdf"):
            file_type, file_path = "pdf", await client.download_media(message.document)
        elif message.document:
            file_type, file_path = "document", await client.download_media(message.document)

        if file_path and file_type:
            uploaded_file = await upload_file_to_gemini(file_path, file_type)
            prompt_text = f"User has sent a {file_type}." + (f" Caption: {caption}" if caption else "")
            prompt = build_prompt(bot_role, chat_history, prompt_text)
            input_data = [prompt, uploaded_file]
            response = await generate_gemini_response(input_data, chat_history, user_id)

            if await handle_voice_message(client, message.chat.id, response):
                return

            return await message.reply(response, reply_to_message_id=message.id)

    except Exception as e:
        await client.send_message("me", f"An error occurred in the `handle_files` function:\n\n{str(e)}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

@Client.on_message(filters.command(["gchat", "gc"], prefix) & filters.me)
async def gchat_command(client: Client, message: Message):
    try:
        parts = message.text.strip().split()
        command = parts[1].lower()
        user_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else message.chat.id

        if command == "on":
            if user_id in disabled_users:
                disabled_users.remove(user_id)
                db.set(collection, "disabled_users", disabled_users)
            if user_id not in enabled_users:
                enabled_users.append(user_id)
                db.set(collection, "enabled_users", enabled_users)
            await message.edit_text(f"<b>ON</b> [{user_id}].")
        elif command == "off":
            if user_id not in disabled_users:
                disabled_users.append(user_id)
                db.set(collection, "disabled_users", disabled_users)
            if user_id in enabled_users:
                enabled_users.remove(user_id)
                db.set(collection, "enabled_users", enabled_users)
            await message.edit_text(f"<b>OFF</b> [{user_id}].")
        elif command == "del":
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(f"<b>Chat history deleted for user {user_id}.</b>")
        elif command == "all":
            global gchat_for_all
            gchat_for_all = not gchat_for_all
            db.set(collection, "gchat_for_all", gchat_for_all)
            await message.edit_text(f"gchat is now {'enabled' if gchat_for_all else 'disabled'} for all users.")
        else:
            await message.edit_text(f"<b>Usage:</b> {prefix}gchat `on`, `off`, `del`, or `all` [user_id].")
        await asyncio.sleep(1)
        await message.delete()
    except Exception as e:
        await client.send_message("me", f"An error occurred in the `gchat` command:\n\n{str(e)}")

@Client.on_message(filters.command("role", prefix) & filters.me)
async def set_custom_role(client: Client, message: Message):
    try:
        parts = message.text.strip().split()
        custom_role = " ".join(parts[2:]).strip()
        user_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else message.chat.id

        if not custom_role:
            db.set(collection, f"custom_roles.{user_id}", default_bot_role)
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(f"Role reset to default for user {user_id}.")
        else:
            db.set(collection, f"custom_roles.{user_id}", custom_role)
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(f"Role set successfully for user {user_id}!\n<b>New Role:</b> {custom_role}")

        await asyncio.sleep(1)
        await message.delete()
    except Exception as e:
        await client.send_message("me", f"An error occurred in the `role` command:\n\n{str(e)}")

@Client.on_message(filters.command("setgkey", prefix) & filters.me)
async def set_gemini_key(client: Client, message: Message):
    try:
        command = message.text.strip().split()
        subcommand, key = command[1] if len(command) > 1 else None, command[2] if len(command) > 2 else None

        gemini_keys = db.get(collection, "gemini_keys") or []
        current_key_index = db.get(collection, "current_key_index") or 0

        if subcommand == "add" and key:
            gemini_keys.append(key)
            db.set(collection, "gemini_keys", gemini_keys)
            await message.edit_text("New Gemini API key added successfully!")
        elif subcommand == "set" and key:
            index = int(key) - 1
            if 0 <= index < len(gemini_keys):
                current_key_index = index
                db.set(collection, "current_key_index", current_key_index)
                genai.configure(api_key=gemini_keys[current_key_index])
                model = genai.GenerativeModel("gemini-2.0-flash-exp", generation_config=generation_config)
                model.safety_settings = safety_settings
                await message.edit_text(f"Current Gemini API key set to key {key}.")
            else:
                await message.edit_text(f"Invalid key index: {key}.")
        elif subcommand == "del" and key:
            index = int(key) - 1
            if 0 <= index < len(gemini_keys):
                del gemini_keys[index]
                db.set(collection, "gemini_keys", gemini_keys)
                if current_key_index >= len(gemini_keys):
                    current_key_index = max(0, len(gemini_keys) - 1)
                    db.set(collection, "current_key_index", current_key_index)
                await message.edit_text(f"Gemini API key {key} deleted successfully!")
            else:
                await message.edit_text(f"Invalid key index: {key}.")
        else:
            keys_list = "\n".join([f"{i + 1}. {key}" for i, key in enumerate(gemini_keys)])
            current_key = gemini_keys[current_key_index] if gemini_keys else "None"
            await message.edit_text(f"<b>Gemini API keys:</b>\n\n<code>{keys_list}</code>\n\n<b>Current key:</b> <code>{current_key}</code>")

        await asyncio.sleep(1)
    except Exception as e:
        await client.send_message("me", f"An error occurred in the `setgkey` command:\n\n{str(e)}")

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
