import asyncio
import os
import random
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from utils.scripts import import_library
from utils.db import db
from utils.misc import modules_help, prefix
from modules.custom_modules.elevenlabs import generate_elevenlabs_audio
from PIL import Image

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
    "Don't send voice messages (only send if they ask), don't use emojis, and rarely use person's name in message so it can feel like human, you only have telegram and instagram (username: @xzrcix) only share if they ask, You don't calls and if they ask for picture you can tell them to check your profile. (you are currently talking them on whatsapp). "
    "Your messages will always contain the history of our chat, where the user's name is in label. "
    "Take the history as a reference, but never send the history along with the message, and never use name labels in the message. "
    "Focus on being romantic and thoughtful in your responses, showing a genuine interest in the other person. "
    "Just respond normally and avoid buzzwords."
)
collection = "custom.wchat"

# Database initialization
enabled_topics = db.get(collection, "enabled_topics") or []
disabled_topics = db.get(collection, "disabled_topics") or []
wchat_for_all_groups = db.get(collection, "wchat_for_all_groups") or {}
group_roles = db.get(collection, "group_roles") or {}

# List of random smileys
smileys = ["-.-", "):", ":)", "*.*", ")*"]


def get_chat_history(topic_id, bot_role, user_message, user_name):
    chat_history = db.get(collection, f"chat_history.{topic_id}") or [
        f"Role: {bot_role}"
    ]
    chat_history.append(f"{user_name}: {user_message}")
    db.set(collection, f"chat_history.{topic_id}", chat_history)
    return chat_history


async def generate_gemini_response(input_data, chat_history, topic_id):
    retries = 3
    gemini_keys = db.get(collection, "gemini_keys")
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
            db.set(collection, f"chat_history.{topic_id}", chat_history)
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


async def handle_voice_message(client, chat_id, bot_response, thread_id=None):
    if ".el" in bot_response:
        start_index = bot_response.find(".el")

        if start_index != -1:
            bot_response = bot_response[start_index + len(".el") :].strip()
        try:
            audio_path = await generate_elevenlabs_audio(text=bot_response)
            if audio_path:
                if thread_id:
                    await client.send_voice(
                        chat_id=chat_id, voice=audio_path, message_thread_id=thread_id
                    )
                else:
                    await client.send_voice(chat_id=chat_id, voice=audio_path)
                os.remove(audio_path)
                return True
        except Exception:
            print("Error generating audio with ElevenLabs.")
            if thread_id:
                await client.send_message(
                    chat_id=chat_id,
                    text=bot_response,
                    message_thread_id=thread_id,
                )
            else:
                await client.send_message(chat_id, bot_response)
            return True
    return False


@Client.on_message(filters.sticker & filters.group & ~filters.me)
async def handle_sticker(client: Client, message: Message):
    try:
        group_id = str(message.chat.id)  # Convert group_id to string
        topic_id = f"{group_id}:{message.message_thread_id}"
        if topic_id in disabled_topics or (
            not wchat_for_all_groups.get(group_id, False)
            and topic_id not in enabled_topics
        ):
            return
        random_smiley = random.choice(smileys)
        await asyncio.sleep(random.uniform(5, 10))
        await message.reply_text(random_smiley)
    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `handle_sticker` function:\n\n{str(e)}"
        )


@Client.on_message(filters.text & filters.group & ~filters.me)
async def wchat(client: Client, message: Message):
    try:
        group_id = str(message.chat.id)  # Convert group_id to string
        topic_id = f"{group_id}:{message.message_thread_id}"
        user_name, user_message = (
            message.from_user.first_name or "User",
            message.text.strip(),
        )
        if topic_id in disabled_topics or (
            not wchat_for_all_groups.get(group_id, False)
            and topic_id not in enabled_topics
        ):
            return

        bot_role = (
            db.get(collection, f"custom_roles.{topic_id}")
            or group_roles.get(group_id)
            or default_bot_role
        )
        chat_history = get_chat_history(topic_id, bot_role, user_message, user_name)

        await asyncio.sleep(random.choice([4, 6]))
        await send_typing_action(client, message.chat.id, user_message)

        gemini_keys = db.get(collection, "gemini_keys")
        current_key_index = db.get(collection, "current_key_index") or 0
        retries = len(gemini_keys) * 2

        while retries > 0:
            try:
                current_key = gemini_keys[current_key_index]
                genai.configure(api_key=current_key)
                model = genai.GenerativeModel("gemini-2.0-flash-exp", generation_config=generation_config)
                model.safety_settings = safety_settings

                chat_context = "\n".join(chat_history)
                response = model.start_chat().send_message(chat_context)
                bot_response = response.text.strip()

                chat_history.append(bot_response)
                db.set(collection, f"chat_history.{topic_id}", chat_history)

                if ".el" in bot_response:
                    return await handle_voice_message(
                        client,
                        message.chat.id,
                        bot_response,
                        thread_id=message.message_thread_id,
                    )

                return await client.send_message(
                    message.chat.id,
                    bot_response,
                    message_thread_id=message.message_thread_id,
                )
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
        return await client.send_message(
            "me", f"An error occurred in the `wchat` module:\n\n{str(e)}"
        )


@Client.on_message(filters.group & ~filters.me)
async def handle_files(client: Client, message: Message):
    try:
        group_id = str(message.chat.id)  # Convert group_id to string
        topic_id = f"{group_id}:{message.message_thread_id}"
        user_name = message.from_user.first_name or "User"
        if topic_id in disabled_topics or (
            not wchat_for_all_groups.get(group_id, False)
            and topic_id not in enabled_topics
        ):
            return

        bot_role = (
            db.get(collection, f"custom_roles.{topic_id}")
            or group_roles.get(group_id)
            or default_bot_role
        )
        caption = message.caption.strip() if message.caption else ""
        chat_history = get_chat_history(topic_id, bot_role, caption, user_name)
        chat_context = "\n".join(chat_history)

        file_type, file_path = None, None  # Initialize file_path to None

        if message.photo:
            if not hasattr(client, "image_buffer"):
                client.image_buffer = {}
                client.image_timers = {}

            if topic_id not in client.image_buffer:
                client.image_buffer[topic_id] = []
                client.image_timers[topic_id] = None

            image_path = await client.download_media(message.photo)
            client.image_buffer[topic_id].append(image_path)

            if client.image_timers[topic_id] is None:

                async def process_images():
                    await asyncio.sleep(5)
                    image_paths = client.image_buffer.pop(topic_id, [])
                    client.image_timers[topic_id] = None

                    if not image_paths:
                        return

                    sample_images = [Image.open(img_path) for img_path in image_paths]
                    prompt = (
                        f"{chat_context}\n\nUser has sent multiple images."
                        f"{' Caption: ' + caption if caption else ''} Generate a response based on the content of the images, and our chat context. "
                        "Always follow the bot role, and talk like a human."
                    )
                    input_data = [prompt] + sample_images
                    response = await generate_gemini_response(
                        input_data, chat_history, topic_id
                    )
                    await message.reply_text(response)

                client.image_timers[topic_id] = asyncio.create_task(process_images())
            return

        if message.video or message.video_note:
            file_type, file_path = (
                "video",
                await client.download_media(message.video or message.video_note),
            )
        elif message.audio or message.voice:
            file_type, file_path = (
                "audio",
                await client.download_media(message.audio or message.voice),
            )
        elif message.document and message.document.file_name.endswith(".pdf"):
            file_type, file_path = "pdf", await client.download_media(message.document)
        elif message.document:
            file_type, file_path = (
                "document",
                await client.download_media(message.document),
            )

        if file_path and file_type:
            uploaded_file = await upload_file_to_gemini(file_path, file_type)
            prompt = (
                f"{chat_context}\n\nUser has sent a {file_type}."
                f"{' Caption: ' + caption if caption else ''} Generate a response based on the content of the {file_type}, and our chat context, always follow role."
            )
            input_data = [prompt, uploaded_file]
            response = await generate_gemini_response(
                input_data, chat_history, topic_id
            )
            return await message.reply_text(response)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        return await client.send_message(
            "me", f"An error occurred in the `handle_files` function:\n\n{str(e)}"
        )


@Client.on_message(filters.command(["wchat", "wc"], prefix) & filters.me)
async def wchat_command(client: Client, message: Message):
    try:
        parts = message.text.strip().split()
        command = parts[1].lower()
        group_id = str(message.chat.id)  # Convert group_id to string
        topic_id = f"{group_id}:{message.message_thread_id}"

        if command == "on":
            if topic_id in disabled_topics:
                disabled_topics.remove(topic_id)
                db.set(collection, "disabled_topics", disabled_topics)
            if topic_id not in enabled_topics:
                enabled_topics.append(topic_id)
                db.set(collection, "enabled_topics", enabled_topics)
            await message.edit_text(f"<b>wchat is enabled for topic {topic_id}.</b>")
        elif command == "off":
            if topic_id not in disabled_topics:
                disabled_topics.append(topic_id)
                db.set(collection, "disabled_topics", disabled_topics)
            if topic_id in enabled_topics:
                enabled_topics.remove(topic_id)
                db.set(collection, "enabled_topics", enabled_topics)
            await message.edit_text(f"<b>wchat is disabled for topic {topic_id}.</b>")
        elif command == "del":
            db.set(collection, f"chat_history.{topic_id}", None)
            await message.edit_text(
                f"<b>Chat history deleted for topic {topic_id}.</b>"
            )
        elif command == "all":
            wchat_for_all_groups[group_id] = not wchat_for_all_groups.get(
                group_id, False
            )
            db.set(collection, "wchat_for_all_groups", wchat_for_all_groups)
            await message.edit_text(
                f"wchat is now {'enabled' if wchat_for_all_groups[group_id] else 'disabled'} for all topics in this group."
            )
        else:
            await message.edit_text(
                f"<b>Usage:</b> {prefix}wchat `on`, `off`, `del`, or `all`."
            )
        await asyncio.sleep(1)
        await message.delete()
    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `wchat` command:\n\n{str(e)}"
        )


@Client.on_message(filters.command("role", prefix) & filters.me)
async def set_custom_role(client: Client, message: Message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            await message.edit_text(f"Usage: {prefix}role [group|topic] <custom role>")
            return

        scope = parts[1].lower()
        custom_role = " ".join(parts[2:]).strip()
        group_id = str(message.chat.id)  # Convert group_id to string
        topic_id = f"{group_id}:{message.message_thread_id}"

        if scope == "group":
            if not custom_role:
                # Reset role to default for the group
                group_roles.pop(group_id, None)
                db.set(collection, "group_roles", group_roles)
                await message.edit_text(f"Role reset to default for group {group_id}.")
            else:
                # Set custom role for the group
                group_roles[group_id] = custom_role
                db.set(collection, "group_roles", group_roles)
                await message.edit_text(
                    f"Role set successfully for group {group_id}!\n<b>New Role:</b> {custom_role}"
                )
        elif scope == "topic":
            if not custom_role:
                # Reset role to group's custom role or default role if no group role exists
                group_role = group_roles.get(group_id, default_bot_role)
                db.set(collection, f"custom_roles.{topic_id}", group_role)
                db.set(collection, f"chat_history.{topic_id}", None)
                await message.edit_text(
                    f"Role reset to group's role for topic {topic_id}."
                )
            else:
                # Set custom role for the topic
                db.set(collection, f"custom_roles.{topic_id}", custom_role)
                db.set(collection, f"chat_history.{topic_id}", None)
                await message.edit_text(
                    f"Role set successfully for topic {topic_id}!\n<b>New Role:</b> {custom_role}"
                )
        else:
            await message.edit_text(f"Invalid scope. Use 'group' or 'topic'.")

        await asyncio.sleep(1)
        await message.delete()
    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `role` command:\n\n{str(e)}"
        )


@Client.on_message(filters.command("setwkey", prefix) & filters.me)
async def set_gemini_key(client: Client, message: Message):
    try:
        command = message.text.strip().split()
        subcommand, key = (
            command[1] if len(command) > 1 else None,
            command[2] if len(command) > 2 else None,
        )

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
            keys_list = "\n".join(
                [f"{i + 1}. {key}" for i, key in enumerate(gemini_keys)]
            )
            current_key = gemini_keys[current_key_index] if gemini_keys else "None"
            await message.edit_text(
                f"<b>Gemini API keys:</b>\n\n<code>{keys_list}</code>\n\n<b>Current key:</b> <code>{current_key}</code>"
            )
        await asyncio.sleep(1)
    except Exception as e:
        await client.send_message(
            "me", f"An error occurred in the `setwkey` command:\n\n{str(e)}"
        )


modules_help["wchat"] = {
    "wchat on": "Enable wchat for the current topic.",
    "wchat off": "Disable wchat for the current topic.",
    "wchat del": "Delete the chat history for the current topic.",
    "wchat all": "Toggle wchat for all topics in the current group.",
    "role group <custom role>": "Set a custom role for the bot for the current group.",
    "role topic <custom role>": "Set a custom role for the bot for the current topic and clear existing chat history.",
    "role reset": "Reset the custom role for the current group to default.",
    "setwkey add <key>": "Add a new Gemini API key.",
    "setwkey set <index>": "Set the current Gemini API key by index.",
    "setwkey del <index>": "Delete a Gemini API key by index.",
    "setwkey": "Display all available Gemini API keys and the current key.",
}
