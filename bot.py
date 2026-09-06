import os
import io
import json
import logging
import threading
from flask import Flask
from PIL import Image
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, firestore

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# এনভায়রনমেন্ট ভেরিয়েবল লোড
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MINI_APP_BOT_USERNAME = os.getenv("MINI_APP_BOT_USERNAME")
MINI_APP_SHORT_NAME = os.getenv("MINI_APP_SHORT_NAME")
ADMIN_USER_IDS = [int(uid.strip()) for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()]
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")

# রেন্ডারের পোর্ট স্ক্যানিং বা টাইমআউট সমস্যা সমাধানের জন্য ডামি ফ্লাস্ক সার্ভার
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Telegram Admin Bot is running live and active!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# ফায়ারবেস ইনিশিয়ালাইজেশন (JWT Signature Error এড়ানোর স্থায়ী সমাধানসহ)
if not firebase_admin._apps:
    if os.path.exists("firebase_key.json"):
        cred = credentials.Certificate("firebase_key.json")
    elif FIREBASE_CREDENTIALS_JSON:
        try:
            clean_json = FIREBASE_CREDENTIALS_JSON.strip()
            if clean_json.startswith("'") and clean_json.endswith("'"):
                clean_json = clean_json[1:-1]
            elif clean_json.startswith('"') and clean_json.endswith('"'):
                clean_json = clean_json[1:-1]
                
            cred_dict = json.loads(clean_json)
            
            # রেন্ডারের এনভায়রনমেন্ট ভেরিয়েবলে ভেঙে যাওয়া প্রাইভেট কি এর \n ঠিক করার ফিক্স
            if "private_key" in cred_dict:
                cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
                
            cred = credentials.Certificate(cred_dict)
        except Exception as e:
            raise ValueError(f"Invalid FIREBASE_CREDENTIALS_JSON format: {e}")
    else:
        raise ValueError("Firebase credentials not found! Please check firebase_key.json or environment variables.")
    
    firebase_admin.initialize_app(cred)

db = firestore.client()

# কনভারসেশন স্টেটস
TITLE, CATEGORY, ADS_COUNT, VIDEO_LINK, THUMBNAIL, CHANNELS, ADD_CHANNEL = range(7)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.message:
            await update.message.reply_text("⛔ আপনার এই বটটি ব্যবহারের অনুমতি নেই।")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("📢 New Publish Post", callback_data="menu_publish")],
        [InlineKeyboardButton("📁 Manage Categories", callback_data="menu_categories")],
        [InlineKeyboardButton("➕ Manage Channels", callback_data="menu_channels")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text("✨ **Admin Control Panel**\n\nনিচের অপশনগুলো থেকে সিলেক্ট করুন:", reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("✨ **Admin Control Panel**\n\nনিচের অপশনগুলো থেকে সিলেক্ট করুন:", reply_markup=reply_markup, parse_mode="Markdown")
    
    return ConversationHandler.END

# --- পোস্ট পাবলিশিং উইজার্ড শুরু ---
async def publish_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📝 ভিডিওর টাইটেল (Title) লিখুন:")
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    
    categories_ref = db.collection("categories").stream()
    keyboard = []
    for cat in categories_ref:
        cat_data = cat.to_dict()
        keyboard.append([InlineKeyboardButton(cat_data.get("name", "Unnamed"), callback_data=f"cat_{cat.id}")])
    
    if not keyboard:
        await update.message.reply_text("⚠️ কোনো ক্যাটেগরি পাওয়া যায়নি! ফায়ারবেসে আগে ক্যাটেগরি যোগ করুন। /start দিয়ে আবার শুরু করুন।")
        return ConversationHandler.END

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📁 একটি ক্যাটেগরি সিলেক্ট করুন:", reply_markup=reply_markup)
    return CATEGORY

async def get_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = query.data.split("_")[1]
    context.user_data["category"] = cat_id
    
    await query.message.reply_text("🔢 কতগুলো অ্যাড দেখলে ভিডিও আনলক হবে সংখ্যাটি লিখুন (যেমন: 1 বা 2):")
    return ADS_COUNT

async def get_ads_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ads_count"] = update.message.text
    await update.message.reply_text("🔗 মেইন ভিডিওর লিংক / ডাউনলোড লিংক দিন:")
    return VIDEO_LINK

async def get_video_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["video_link"] = update.message.text
    await update.message.reply_text("🖼️ ভিডিওর জন্য একটি থাম্বনেইল (Thumbnail Photo) দিন:")
    return THUMBNAIL

async def get_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("দয়া করে একটি সঠিক ছবি বা থাম্বনেইল দিন।")
        return THUMBNAIL
    
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    
    image = Image.open(io.BytesIO(photo_bytes))
    image = image.convert("RGB")
    image.thumbnail((1280, 720))
    
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    context.user_data["thumbnail_bytes"] = output.getvalue()
    
    channels_ref = db.collection("channels").stream()
    keyboard = []
    context.user_data["selected_channels"] = []
    
    for ch in channels_ref:
        ch_data = ch.to_dict()
        ch_id = ch_data.get("channel_id")
        ch_name = ch_data.get("name", "Channel")
        keyboard.append([InlineKeyboardButton(f"[ ] {ch_name}", callback_data=f"ch_toggle_{ch_id}")])
    
    if not keyboard:
        await update.message.reply_text("⚠️ কোনো চ্যানেল ডাটাবেজে যুক্ত করা নেই! আগে চ্যানেল যোগ করুন।")
        return ConversationHandler.END

    keyboard.append([InlineKeyboardButton("🚀 Confirm & Publish", callback_data="ch_publish")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("📢 যে চ্যানেলগুলোতে পোস্ট করতে চান সেগুলোতে ক্লিক করে টিক দিন, এরপর 'Confirm & Publish'-এ চাপ দিন:", reply_markup=reply_markup)
    return CHANNELS

async def channel_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "ch_publish":
        if not context.user_data.get("selected_channels"):
            await query.edit_message_text("⚠️ কমপক্ষে একটি চ্যানেল সিলেক্ট করুন!")
            return CHANNELS
        
        post_ref = db.collection("posts").document()
        post_id = post_ref.id
        
        post_data = {
            "title": context.user_data["title"],
            "category": context.user_data["category"],
            "ads_count": context.user_data["ads_count"],
            "video_link": context.user_data["video_link"],
            "created_at": firestore.SERVER_TIMESTAMP
        }
        post_ref.set(post_data)
        
        deep_link = f"https://t.me/{MINI_APP_BOT_USERNAME}/{MINI_APP_SHORT_NAME}?startapp={post_id}"
        
        keyboard = [[InlineKeyboardButton("🎥 Watch Video in App", url=deep_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        for ch_id in context.user_data["selected_channels"]:
            try:
                await context.bot.send_photo(
                    chat_id=ch_id,
                    photo=context.user_data["thumbnail_bytes"],
                    caption=f"🔥 **{context.user_data['title']}**",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error posting to channel {ch_id}: {e}")
                
        await query.message.edit_text("✅ সফলভাবে মিনি অ্যাপের ডাটাবেজে পোস্ট লাইভ হয়েছে এবং সিলেক্ট করা টেলিগ্রাম চ্যানেলগুলোতে পোস্ট করা হয়েছে!")
        return ConversationHandler.END

    elif data.startswith("ch_toggle_"):
        ch_id = data.replace("ch_toggle_", "")
        selected = context.user_data["selected_channels"]
        if ch_id in selected:
            selected.remove(ch_id)
        else:
            selected.append(ch_id)
        
        channels_ref = db.collection("channels").stream()
        keyboard = []
        for ch in channels_ref:
            ch_data = ch.to_dict()
            cid = ch_data.get("channel_id")
            cname = ch_data.get("name", "Channel")
            icon = "✅" if cid in selected else " "
            keyboard.append([InlineKeyboardButton(f"[{icon}] {cname}", callback_data=f"ch_toggle_{cid}")])
        keyboard.append([InlineKeyboardButton("🚀 Confirm & Publish", callback_data="ch_publish")])
        
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            pass
        return CHANNELS

async def manage_categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📁 ক্যাটেগরি ম্যানেজ করতে আপনার Firebase Firestore কনসোলে `categories` কালেকশনে একটি ডকুমেন্ট তৈরি করুন যেখানে `name` ফিল্ড থাকবে।")

# --- চ্যানেল ম্যানেজমেন্ট ও আইডি দিয়ে অটো-সেভ সিস্টেম ---
async def manage_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Channel", callback_data="add_channel_prompt")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(
        "➕ **Channel Management**\n\nবটটিকে আপনার টেলিগ্রাম চ্যানেলে **Admin** হিসেবে যুক্ত করুন। এরপর নিচের বাটনে ক্লিক করে চ্যানেলের **আইডি** (যেমন: `-100xxxxxxxxxx`) অথবা ইউজারনেম পাঠান:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return ADD_CHANNEL

async def prompt_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📢 যে চ্যানেলে বট অ্যাড করেছেন, সেই চ্যানেলের **আইডি** (যেমন: `-1001234567890`) অথবা ইউজারনেম পাঠান:")
    return ADD_CHANNEL

async def save_channel_to_firebase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text.lower() == "/start":
        return await start(update, context)

    target = text
    if text.startswith("-") or text.isdigit():
        try:
            target = int(text)
        except ValueError:
            pass

    try:
        chat = await context.bot.get_chat(target)
        channel_id = str(chat.id)
        channel_name = chat.title or chat.username or "Unknown Channel"
        
        db.collection("channels").document(channel_id).set({
            "channel_id": channel_id,
            "name": channel_name,
            "username": chat.username or ""
        })
        
        await update.message.reply_text(
            f"✅ সফলভাবে ফায়ারবেসে চ্যানেল যুক্ত হয়েছে!\n\n📌 **নাম:** {channel_name}\n🆔 **আইডি:** `{channel_id}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text(
            "❌ চ্যানেল খুঁজে পাওয়া যায়নি বা যুক্ত করা সম্ভব হয়নি! নিশ্চিত করুন যে:\n1. বটটি ওই চ্যানেলে অ্যাড করা আছে।\n2. বটটিকে চ্যানেলের **Admin** করা হয়েছে।\n3. সঠিক চ্যানেল আইডি (যেমন `-100...`) দেওয়া হয়েছে।"
        )
    
    return ConversationHandler.END

def main():
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(request).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(publish_start, pattern="^menu_publish$"),
            CallbackQueryHandler(manage_channels_menu, pattern="^menu_channels$"),
            CommandHandler("start", start)
        ],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            CATEGORY: [CallbackQueryHandler(get_category, pattern="^cat_")],
            ADS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ads_count)],
            VIDEO_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_video_link)],
            THUMBNAIL: [MessageHandler(filters.PHOTO, get_thumbnail)],
            CHANNELS: [CallbackQueryHandler(channel_selection_callback)],
            ADD_CHANNEL: [
                CallbackQueryHandler(prompt_add_channel, pattern="^add_channel_prompt$"),
                CallbackQueryHandler(start, pattern="^back_to_main$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_channel_to_firebase)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(manage_categories_menu, pattern="^menu_categories$"))
    app.add_handler(CommandHandler("start", start))

    print("Admin Bot is running with Flask Port Binding & Channel ID Support...")
    app.run_polling()

if __name__ == "__main__":
    main()
