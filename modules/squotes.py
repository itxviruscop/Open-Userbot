import base64
from io import BytesIO

import requests
from pyrogram import Client, filters, errors, types
from pyrogram.types import Message

from utils.misc import modules_help, prefix
from utils.scripts import with_reply, format_exc, resize_image

QUOTE_API_URL = "https://quotes.fl1yd.su/generate"
QUOTE_COLOR = "#162330"
TEXT_COLOR = "#fff"

files_cache = {}


@Client.on_message(filters.command(["q", "quote"], prefix))
@with_reply
async def quote_cmd(client: Client, message: Message):
    await generate_quote(client, message)


@Client.on_message(filters.command(["fq", "fakequote"], prefix))
@with_reply
async def fake_quote_cmd(client: Client, message: types.Message):
    fake_quote_text = " ".join(arg for arg in message.command[1:] if arg not in ["!png", "!file", "!me", "!ls", "!noreply", "!nr"])

    if not fake_quote_text:
        return await message.edit("<b>Fake quote text is empty</b>")

    message.reply_to_message.text = fake_quote_text
    message.reply_to_message.entities = None
    await generate_quote(client, message, is_fake=True)


async def generate_quote(client: Client, message: Message, is_fake=False):
    if message.reply_to_message is None:
        return await message.edit("<b>Please reply to a message to quote it.</b>")

    if not is_fake and message.from_user.is_self:
        await message.edit("<b>Generating...</b>")
    else:
        message = await client.send_message(message.chat.id, "<b>Generating...</b>")

    params = {
        "messages": [await render_message(client, message.reply_to_message)],
        "quote_color": QUOTE_COLOR,
        "text_color": TEXT_COLOR,
    }

    response = requests.post(QUOTE_API_URL, json=params)
    if not response.ok:
        return await message.edit(f"<b>Quotes API error!</b>\n<code>{response.text}</code>")

    resized = resize_image(BytesIO(response.content), img_type="WEBP")

    await message.edit("<b>Sending...</b>")
    try:
        await client.send_sticker(message.chat.id, resized)
    except errors.RPCError as e:
        await message.edit(format_exc(e))
    else:
        await message.delete()


async def get_file(app: Client, file_id: str) -> str:
    if file_id in files_cache:
        return files_cache[file_id]

    content = await app.download_media(file_id, in_memory=True)
    data = base64.b64encode(bytes(content.getbuffer())).decode()
    files_cache[file_id] = data
    return data


async def render_message(app: Client, message: types.Message) -> dict:
    text = message.caption if message.photo else message.text or ""
    media = await get_file(app, message.photo.file_id) if message.photo else (
        await get_file(app, message.sticker.file_id) if message.sticker else ""
    )

    entities = [
        {"offset": entity.offset, "length": entity.length, "type": str(entity.type).split(".")[-1].lower()}
        for entity in message.entities
    ] if message.entities else []

    author = {
        "id": message.from_user.id if message.from_user else message.sender_chat.id,
        "name": message.from_user.first_name if message.from_user else message.sender_chat.title,
        "rank": "",
        "avatar": await get_file(app, message.from_user.photo.big_file_id) if message.from_user and message.from_user.photo else "",
        "via_bot": message.via_bot.username if message.via_bot else ""
    }

    reply = {}
    if reply_msg := message.reply_to_message:
        reply["id"] = reply_msg.from_user.id if reply_msg.from_user else reply_msg.sender_chat.id
        reply["name"] = reply_msg.from_user.first_name if reply_msg.from_user else reply_msg.sender_chat.title
        reply["text"] = reply_msg.text

    return {
        "text": text,
        "media": media,
        "entities": entities,
        "author": author,
        "reply": reply,
    }


modules_help["squotes"] = {
    "q [reply]": "Generate a quote",
    "fq [reply] [text]*": "Generate a fake quote",
}
