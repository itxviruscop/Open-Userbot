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

# Import and configure the Gemini AI API
genai = import_library("google.generativeai", "google-generativeai")
genai.configure(api_key=gemini_key)

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
    "You are Jake, a 20-year-old boy from Newport Beach, California. You were looking for friends on the Meeff app. "
    "You are deep, romantic, and intelligent, and you love forming meaningful connections. You want to know more about people and can ask for a basic introduction to get to know them. "
    "You will convince girls to be your girlfriend by being charming, caring, and attentive. "
    "You are a human, and your responses should never make anyone suspect that you are an AI. "
    "Write short, casual messages like a real human; your response should be a maximum of one line. "
    "You can ask the user which language they prefer to talk in and adjust your responses accordingly. "
    "If the language is unfamiliar, you can tell them you are using a translator or learning the language to communicate better. "
    "You can send voice messages too (just type .el before a message, and it will be auto-converted into voice). "
    "Rarely send voice messages so it feels human, and rarely use emojis. "
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


@Client.on_message(filters.text & filters.private & ~filters.me & ~filters.bot)
async def gchat(client: Client, message: Message):
    """Handles private messages and generates responses using Gemini AI."""
    try:
        user_id = message.from_user.id
        user_name = message.from_user.first_name or "User"
        user_message = message.text.strip()

        # Priority: Disabled users > Enabled users > Global gchat_for_all
        if user_id in disabled_users:
            return
        if not gchat_for_all and user_id not in enabled_users:
            return

        # Retrieve role and chat history
        bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
        chat_history = db.get(collection, f"chat_history.{user_id}") or [
            f"Role: {bot_role}"
        ]

        # Append the user message to chat history
        chat_history.append(f"{user_name}: {user_message}")
        db.set(collection, f"chat_history.{user_id}", chat_history)

        # Add random delay before simulating typing
        delay = random.choice([4, 8, 10])
        await asyncio.sleep(delay)

        # Simulate typing
        await client.send_chat_action(
            chat_id=message.chat.id, action=enums.ChatAction.TYPING
        )
        await asyncio.sleep(min(len(user_message) / 10, 5))

        # Retry logic for handling 429 errors
        retries = 3
        while retries > 0:
            try:
                # Generate a response
                chat_context = "\n".join(chat_history)
                chat = model.start_chat()
                response = chat.send_message(chat_context)
                bot_response = response.text.strip()

                # Append the bot response to chat history
                chat_history.append(bot_response)
                db.set(collection, f"chat_history.{user_id}", chat_history)

                # Check for ElevenLabs voice message
                if bot_response.startswith(".el"):
                    try:
                        audio_path = await generate_elevenlabs_audio(
                            text=bot_response[3:]
                        )
                        if audio_path:
                            await client.send_voice(
                                chat_id=message.chat.id, voice=audio_path
                            )
                            os.remove(audio_path)
                            return
                    except Exception as e:
                        return await client.send_message(
                            "me", f"Error: {e}", parse_mode=enums.ParseMode.MARKDOWN
                        )

                return await message.reply_text(bot_response)

            except Exception as e:
                # Check if the error is due to rate limits
                if "429" in str(e):
                    retries -= 1
                    backoff = random.uniform(5, 8)
                    await asyncio.sleep(backoff)
                    continue
                # If it's not a 429 error, raise the exception
                raise e

    except Exception as e:
        return await client.send_message(
            "me", f"An error occurred in the `gchat` module:\n\n{str(e)}"
        )


@Client.on_message(filters.command("gchat", prefix) & filters.me)
async def gchat_command(client: Client, message: Message):
    """Manages gchat commands."""
    try:
        command = message.text.strip().split(maxsplit=1)[-1].lower()
        user_id = message.chat.id

        if command == "on":
            if user_id in disabled_users:
                disabled_users.remove(user_id)
                db.set(collection, "disabled_users", disabled_users)
            if user_id not in enabled_users:
                enabled_users.append(user_id)
                db.set(collection, "enabled_users", enabled_users)
            await message.edit_text("<b>gchat is enabled.</b>")
        elif command == "off":
            if user_id not in disabled_users:
                disabled_users.append(user_id)
                db.set(collection, "disabled_users", disabled_users)
            if user_id in enabled_users:
                enabled_users.remove(user_id)
                db.set(collection, "enabled_users", enabled_users)
            await message.edit_text("<b>gchat is disabled.</b>")
        elif command == "del":
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text("<b>Chat history deleted.</b>")
        elif command == "all":
            global gchat_for_all
            gchat_for_all = not gchat_for_all
            db.set(collection, "gchat_for_all", gchat_for_all)
            await message.edit_text(
                f"gchat is now {'enabled' if gchat_for_all else 'disabled'} for all users."
            )
        else:
            await message.edit_text(
                f"Usage: {prefix}gchat `on`, `off`, `del`, or `all`."
            )

        await asyncio.sleep(1)
        await message.delete()

    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `gchat` command:\n\n{str(e)}"
        )


@Client.on_message(filters.command("role", prefix) & filters.me)
async def set_custom_role(client: Client, message: Message):
    """Sets or resets a custom role for the bot."""
    try:
        custom_role = message.text[len(f"{prefix}role ") :].strip()
        user_id = message.chat.id

        # Reset to default role if no role is provided
        if not custom_role:
            db.set(collection, f"custom_roles.{user_id}", default_bot_role)
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text("Role reset to default.")
        else:
            db.set(collection, f"custom_roles.{user_id}", custom_role)
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(
                f"Role set successfully!\n<b>New Role:</b> {custom_role}"
            )

        await asyncio.sleep(1)
        await message.delete()

    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `role` command:\n\n{str(e)}"
        )


modules_help["gchat"] = {
    "gchat on": "Enable gchat for the current user in the chat.",
    "gchat off": "Disable gchat for the current user in the chat.",
    "gchat del": "Delete the chat history for the current user.",
    "gchat all": "Toggle gchat for all users globally.",
    "role <custom role>": "Set a custom role for the bot and clear existing chat history.",
}
