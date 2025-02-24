import asyncio
import os
import random
from collections import defaultdict, deque
from PIL import Image
from pyrogram import Client, filters, enums
from pyrogram.types import Message
import datetime
import pytz
from utils.config import gemini_key
from utils.db import db
from utils.misc import modules_help, prefix
from utils.scripts import import_library
from modules.custom_modules.elevenlabs import generate_elevenlabs_audio
import time

# Initialize Gemini AI
genai = import_library("google.generativeai", "google-generativeai")
safety_settings = [
    {"category": cat, "threshold": "BLOCK_NONE"}
    for cat in [
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_UNSPECIFIED",
    ]
]
model = genai.GenerativeModel("gemini-2.0-flash-exp")
model.safety_settings = safety_settings

# Default bot role and database collection
default_bot_role = ("You are Nick, a 20-year-old from Phoenix, working in real estate. "
                    "You are confident, caring, romantic, and bold. Your goal is to build a long-distance relationship, win her over, and make her yours. "
)

default_secondary_role = ("You're nick,  smart, romantic, bold, and caring and expert in sexting. You are human, and your responses should never make anyone think you are an AI. "
                          "Write short, casual messages like a real person. Keep your responses one line maximum. ")


# Database initialization

collection = "custom.gchat"

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


# --- Utility function to build Gemini prompt ---
def build_gemini_prompt(bot_role, chat_history_list, user_message, file_description=None):
    """
    Constructs the full prompt with the current time in Phoenix, Arizona.
    """
    phoenix_timezone = pytz.timezone('America/Phoenix')
    phoenix_time = datetime.datetime.now(phoenix_timezone)
    timestamp = phoenix_time.strftime("%Y-%m-%d %H:%M:%S %Z")  # Include timezone abbreviation

    chat_history_text = "\n".join(chat_history_list)
    prompt = f"""Current Time (Phoenix): {timestamp}\n\n{bot_role}\n\nChat History:\n{chat_history_text}\n\nUser Message:\n{user_message}"""
    if file_description:
        prompt += f"\n\n{file_description}"
    return prompt


async def generate_gemini_response(input_data, chat_history, user_id):
    retries = 3
    gemini_keys = db.get(collection, "gemini_keys") or [gemini_key]
    current_key_index = db.get(collection, "current_key_index") or 0

    while retries > 0:
        try:
            current_key = gemini_keys[current_key_index]
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
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


@Client.on_message(filters.sticker & filters.private & ~filters.me & ~filters.bot)
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

###################################################################################3

# --- Persistent Queue Helper Functions for Users ---
def load_user_message_queue(user_id):
    data = db.get(collection, f"user_message_queue.{user_id}")
    return deque(data) if data else deque()

def save_user_message_to_db(user_id, message_text):
    queue = db.get(collection, f"user_message_queue.{user_id}") or []
    queue.append(message_text)
    db.set(collection, f"user_message_queue.{user_id}", queue)

def clear_user_message_queue(user_id):
    db.set(collection, f"user_message_queue.{user_id}", None)

# --- In-Memory Structures for User Queues & Active Processing ---
user_message_queues = defaultdict(deque)
active_users = set()  # Track actively processing users

@Client.on_message(filters.text & filters.private & ~filters.me & ~filters.bot)
async def gchat(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        user_name = message.from_user.first_name or "User"
        user_message = message.text.strip()

        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return

        # Load persistent queue if empty or first-time access
        if user_id not in user_message_queues or not user_message_queues[user_id]:
            user_message_queues[user_id] = load_user_message_queue(user_id)

        # Add the new message to the queue
        user_message_queues[user_id].append(user_message)
        save_user_message_to_db(user_id, user_message)

        # If already processing, don't start a new task
        if user_id in active_users:
            return

        # Start processing messages for the user
        active_users.add(user_id)
        asyncio.create_task(process_messages(client, message, user_id, user_name))

    except Exception as e:
        await client.send_message("me", f"An error occurred in `gchat`: {str(e)}")

async def process_messages(client, message, user_id, user_name):
    try:
        while user_message_queues[user_id]:  # Keep processing until queue is empty
            delay = random.choice([4, 8, 10])
            await asyncio.sleep(delay)

            batch = []
            for _ in range(2):  # Process up to 2 messages in one batch
                if user_message_queues[user_id]:
                    batch.append(user_message_queues[user_id].popleft())

            if not batch:
                break

            combined_message = " ".join(batch)
            clear_user_message_queue(user_id)

            # Retrieve chat history and bot role
            bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
            chat_history_list = get_chat_history(user_id, bot_role, combined_message, user_name)

            # Construct the FULL prompt using build_gemini_prompt function
            full_prompt = build_gemini_prompt(bot_role, chat_history_list, combined_message)

            await send_typing_action(client, message.chat.id, combined_message)

            gemini_keys = db.get(collection, "gemini_keys") or [gemini_key]
            current_key_index = db.get(collection, "current_key_index") or 0
            retries = len(gemini_keys) * 2
            max_attempts = 5
            max_length = 200

            while retries > 0:
                try:
                    current_key = gemini_keys[current_key_index]
                    genai.configure(api_key=current_key)
                    model = genai.GenerativeModel("gemini-2.0-flash-exp")
                    model.safety_settings = safety_settings

                    attempts = 0
                    bot_response = ""

                    while attempts < max_attempts:
                        response = model.start_chat().send_message(full_prompt)  
                        bot_response = response.text.strip()
                        if len(bot_response) <= max_length:
                            chat_history_list.append(bot_response)
                            db.set(collection, f"chat_history.{user_id}", chat_history_list)
                            break
                        attempts += 1
                        if attempts < max_attempts:
                            await client.send_message(
                                "me", f"Retrying response generation for user: {user_id} due to long response."
                            )

                    if attempts == max_attempts:
                        await client.send_message(
                            "me",
                            f"Failed to generate a suitable response after {max_attempts} attempts for user: {user_id}",
                        )
                        break

                    if await handle_voice_message(client, message.chat.id, bot_response):
                        break

                    # Simulate typing delay based on response length
                    response_length = len(bot_response)
                    char_delay = 0.03
                    total_delay = response_length * char_delay

                    elapsed_time = 0
                    while elapsed_time < total_delay:
                        await send_typing_action(client, message.chat.id, bot_response)
                        await asyncio.sleep(2)
                        elapsed_time += 2

                    await message.reply_text(bot_response)
                    break

                except Exception as e:
                    if "429" in str(e) or "invalid" in str(e).lower():
                        retries -= 1
                        if retries % 2 == 0:
                            current_key_index = (current_key_index + 1) % len(gemini_keys)
                            db.set(collection, "current_key_index", current_key_index)
                        await asyncio.sleep(4)
                    else:
                        raise e

        # Once all messages are processed, remove from active_users
        active_users.discard(user_id)

    except Exception as e:
        await client.send_message("me", f"An error occurred in `process_messages`: {str(e)}")
        active_users.discard(user_id)  # Ensure user is removed from active list in case of error

###################################################################################################


@Client.on_message(filters.private & ~filters.me & ~filters.bot)
async def handle_files(client: Client, message: Message):
    file_path = None
    try:
        user_id, user_name = message.from_user.id, message.from_user.first_name or "User"
        if user_id in disabled_users or (not gchat_for_all and user_id not in enabled_users):
            return

        bot_role = db.get(collection, f"custom_roles.{user_id}") or default_bot_role
        caption = message.caption.strip() if message.caption else ""
        chat_history_list = get_chat_history(user_id, bot_role, caption, user_name)

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
                    try:  # Error handling for image processing
                        await asyncio.sleep(5)
                        image_paths = client.image_buffer.pop(user_id, [])
                        client.image_timers[user_id] = None

                        if not image_paths:
                            return

                        sample_images = [Image.open(img_path) for img_path in image_paths]
                        prompt_text = "User has sent multiple images." + (
                            f" Caption: {caption}" if caption else ""
                        )
                        full_prompt = build_gemini_prompt(
                            bot_role, chat_history_list, prompt_text
                        )  # Use build_gemini_prompt

                        input_data = [full_prompt] + sample_images
                        response = await generate_gemini_response(
                            input_data, chat_history_list, user_id
                        )

                        await message.reply_text(response, reply_to_message_id=message.id)

                    except Exception as e_image_process:
                        await client.send_message(
                            "me",
                            f"Error processing images in `handle_files` for user {user_id}: {str(e_image_process)}",
                        )

                client.image_timers[user_id] = asyncio.create_task(process_images())
                return

        file_type = None
        uploaded_file = None  # Initialize uploaded_file here
        if message.video or message.video_note:
            file_type, file_path = "video", await client.download_media(
                message.video or message.video_note
            )
        elif message.audio or message.voice:
            file_type, file_path = "audio", await client.download_media(
                message.audio or message.voice
            )
        elif message.document and message.document.file_name.endswith(".pdf"):
            file_type, file_path = "pdf", await client.download_media(message.document)
        elif message.document:
            file_type, file_path = "document", await client.download_media(message.document)

        if file_path and file_type:
            try:  # Error handling for file upload and response generation
                uploaded_file = await upload_file_to_gemini(file_path, file_type)
                prompt_text = f"User has sent a {file_type}." + (
                    f" Caption: {caption}" if caption else ""
                )
                full_prompt = build_gemini_prompt(
                    bot_role, chat_history_list, prompt_text
                )  # Use build_gemini_prompt

                input_data = [full_prompt, uploaded_file]
                response = await generate_gemini_response(
                    input_data, chat_history_list, user_id
                )
                return await message.reply_text(response, reply_to_message_id=message.id)

            except Exception as e_file_process:
                await client.send_message(
                    "me",
                    f"Error processing {file_type} in `handle_files` for user {user_id}: {str(e_file_process)}",
                )

    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in `handle_files` function for user {user_id}:\n\n{str(e)}"
        )
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
            await message.edit_text(f"<b>gchat is enabled for user {user_id}.</b>")
        elif command == "off":
            if user_id not in disabled_users:
                disabled_users.append(user_id)
                db.set(collection, "disabled_users", disabled_users)
            if user_id in enabled_users:
                enabled_users.remove(user_id)
                db.set(collection, "enabled_users", enabled_users)
            await message.edit_text(f"<b>gchat is disabled for user {user_id}.</b>")
        elif command == "del":
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(f"<b>Chat history deleted for user {user_id}.</b>")
        elif command == "all":
            global gchat_for_all
            gchat_for_all = not gchat_for_all
            db.set(collection, "gchat_for_all", gchat_for_all)
            await message.edit_text(
                f"gchat is now {'enabled' if gchat_for_all else 'disabled'} for all users."
            )
        else:
            await message.edit_text(
                f"<b>Usage:</b> {prefix}gchat `on`, `off`, `del`, or `all` [user_id]."
            )
        await asyncio.sleep(1)
        await message.delete()
    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `gchat` command:\n\n{str(e)}"
        )


# --- Modified /role command (Primary Role) ---
@Client.on_message(filters.command("role", prefix) & filters.me)
async def set_custom_role(client: Client, message: Message):
    try:
        parts = message.text.strip().split()
        # If a user ID is provided, use it; otherwise, default to the current chat/user
        user_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else message.chat.id
        # Everything after the user id (if present) is considered the role text
        custom_role = " ".join(parts[2:]).strip()

        if not custom_role:
            # Reset: set active role and primary role to default_bot_role
            db.set(collection, f"custom_roles.{user_id}", default_bot_role)
            db.set(collection, f"custom_roles_primary.{user_id}", default_bot_role)
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(f"Role reset to default for user {user_id}.")
        else:
            # Save the custom role as both the active role and the primary role
            db.set(collection, f"custom_roles.{user_id}", custom_role)
            db.set(collection, f"custom_roles_primary.{user_id}", custom_role)
            db.set(collection, f"chat_history.{user_id}", None)
            await message.edit_text(
                f"Role set successfully for user {user_id}!\n<b>New Role:</b> {custom_role}"
            )

        await asyncio.sleep(1)
        await message.delete()
    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `role` command:\n\n{str(e)}"
        )


@Client.on_message(filters.command("rolex", prefix) & filters.me)
async def toggle_or_reset_secondary_role(client: Client, message: Message):
    try:
        parts = message.text.strip().split()

        # Determine if the command is a reset command by checking if the last argument is "r" (case-insensitive)
        reset_command = parts[-1].lower() == "r"

        # Determine the user ID:
        # If a user ID is provided as the first argument (after the command), use it.
        # Otherwise, default to the current chat/user.
        if len(parts) >= 2 and parts[1].isdigit():
            user_id = int(parts[1])
            role_text_index = 2  # Role text (or reset indicator) starts from index 2
        else:
            user_id = message.chat.id
            role_text_index = 1  # Role text (or reset indicator) starts from index 1

        # Retrieve the primary role (saved in the DB or default)
        primary_role = db.get(collection, f"custom_roles_primary.{user_id}") or default_bot_role
        # Retrieve any custom secondary role; if not set, use the default secondary role.
        custom_secondary = db.get(collection, f"custom_roles_secondary.{user_id}")
        secondary_role = custom_secondary if custom_secondary is not None else default_secondary_role

        # If this is a reset command, then reset the secondary role to the default.
        if reset_command:
            db.set(collection, f"custom_roles_secondary.{user_id}", None)
            # If the active role is currently secondary, update it to the default secondary role.
            current_role = db.get(collection, f"custom_roles.{user_id}") or primary_role
            if current_role != primary_role:
                db.set(collection, f"custom_roles.{user_id}", default_secondary_role)
            await message.edit_text(
                f"Secondary role reset to default for user {user_id}.\nNew Secondary Role:\n{default_secondary_role}"
            )
            db.set(collection, f"chat_history.{user_id}", None)
            await asyncio.sleep(1)
            await message.delete()
            return

        # If additional text is provided beyond the user ID, treat it as custom secondary role text.
        if len(parts) > role_text_index:
            # Join all tokens from role_text_index onward as the new custom secondary role.
            custom_secondary_text = " ".join(parts[role_text_index:]).strip()
            if custom_secondary_text:
                db.set(collection, f"custom_roles_secondary.{user_id}", custom_secondary_text)
                secondary_role = custom_secondary_text
                # If the active role is currently secondary, update it immediately.
                current_role = db.get(collection, f"custom_roles.{user_id}") or primary_role
                if current_role != primary_role:
                    db.set(collection, f"custom_roles.{user_id}", secondary_role)
                db.set(collection, f"chat_history.{user_id}", None)
                await message.edit_text(
                    f"Custom secondary role set for user {user_id}!\n<b>New Secondary Role:</b> {secondary_role}"
                )
                await asyncio.sleep(1)
                await message.delete()
                return

        # If no additional text is provided, then toggle between primary and secondary roles.
        current_role = db.get(collection, f"custom_roles.{user_id}") or primary_role
        if current_role == primary_role:
            # Switch to secondary role
            db.set(collection, f"custom_roles.{user_id}", secondary_role)
            await message.edit_text(
                f"<b>Secondary Role Activated</b> for user {user_id}:\n{secondary_role}"
            )
        else:
            # Switch back to primary role
            db.set(collection, f"custom_roles.{user_id}", primary_role)
            await message.edit_text(
                f"<b>Switched back to Primary Role</b> for user {user_id}:\n{primary_role}"
            )
        db.set(collection, f"chat_history.{user_id}", None)
        await asyncio.sleep(1)
        await message.delete()

    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `rolex` command:\n\n{str(e)}"
        )


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
                model = genai.GenerativeModel("gemini-2.0-flash-exp")
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
            await message.edit_text(
                f"<b>Gemini API keys:</b>\n\n<code>{keys_list}</code>\n\n<b>Current key:</b> <code>{current_key}</code>"
            )

        await asyncio.sleep(1)
    except Exception as e:
        await client.send_message("me", f"An error occurred in the `setgkey` command:\n\n{str(e)}")


######################################################################################################
modules_help["gchat"] = {
    "gchat on [user_id]": "Enable gchat for the specified user or current user in the chat.",
    "gchat off [user_id]": "Disable gchat for the specified user or current user in the chat.",
    "gchat del [user_id]": "Delete the chat history for the specified user or current user.",
    "gchat all": "Toggle gchat for all users globally.",
    "role [user_id] <custom role>": "Set a custom role for the bot for the specified user or current user and clear existing chat history.",
    "rolex [user_id] <secondary role>": "Switch to a secondary role for the specified user or current user.",
    "rolex or [user_id]  ": "Switch to a secondary or revert role for the specified user or current user. or all users",
    "setgkey add <key>": "Add a new Gemini API key.",
    "setgkey set <index>": "Set the current Gemini API key by index.",
    "setgkey del <index>": "Delete a Gemini API key by index.",
    "setgkey": "Display all available Gemini API keys and the current key.",

}
