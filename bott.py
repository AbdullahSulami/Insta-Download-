"""
🎬 بوت تحميل الفيديوهات - نسخة احترافية كاملة (معدلة)
بوت تيليجرام لتحميل الفيديوهات من يوتيوب، انستغرام، تيك توك، تويتر، فيسبوك
"""

import os
import json
import logging
import html
import time
import hashlib
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import re
import threading
from queue import Queue

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
try:
    from telegram.ext import (
        Updater, CommandHandler, MessageHandler, CallbackQueryHandler,
        Filters, CallbackContext, ConversationHandler
    )
except ImportError:
    # Handle PTB v20 compatibility
    from telegram.ext import (
        Application as Updater, CommandHandler, MessageHandler, CallbackQueryHandler,
        filters as Filters, CallbackContext, ConversationHandler
    )
    # Note: v20 is async, so this is just a name shim. 
    # But since the user is using v13 style, we should probably stick to v13 or fix the environment.

import yt_dlp

from dotenv import load_dotenv

# Load env variables
load_dotenv()

# ==================== الإعدادات ====================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7265784246"))  # معرف الآدمين
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")  # معرف القناة (عدله لاحقاً)

# ==================== الإعدادات المتقدمة ====================
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 ميجابايت
MAX_DURATION = 30 * 60  # 30 دقيقة
DOWNLOAD_TIMEOUT = 300  # 5 دقائق

# ==================== المجلدات ====================
TEMP_DIR = Path("temp")
DATA_DIR = Path("data")
VIDEOS_DIR = DATA_DIR / "videos"
LOGS_DIR = DATA_DIR / "logs"
USERS_FILE = DATA_DIR / "users.json"
MESSAGES_HTML = LOGS_DIR / "messages.html"
VIDEOS_ZIP = DATA_DIR / "exports" / "videos.zip"

# إنشاء المجلدات
for dir_path in [TEMP_DIR, DATA_DIR, VIDEOS_DIR, LOGS_DIR, DATA_DIR / "exports"]:
    dir_path.mkdir(parents=True, exist_ok=True)

# ==================== إعدادات التسجيل ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(DATA_DIR / 'bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== حالات المحادثة ====================
(WAITING_SUPPORT, WAITING_BROADCAST, WAITING_REPLY_ID, WAITING_REPLY_MSG) = range(4)

# ==================== مدير قاعدة البيانات ====================
class Database:
    def __init__(self):
        self.users_file = USERS_FILE
        self.users = self._load_users()
    
    def _load_users(self) -> Dict:
        if self.users_file.exists():
            try:
                with open(self.users_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_users(self):
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump(self.users, f, indent=2, ensure_ascii=False)
    
    def add_user(self, user_id: int, first_name: str, username: str = None) -> bool:
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {
                "id": int(user_id),
                "first_name": first_name,
                "username": username,
                "downloads": 0,
                "joined": datetime.now().isoformat(),
                "last_active": datetime.now().isoformat(),
                "total_size_mb": 0
            }
            self._save_users()
            return True
        else:
            self.users[user_id]["last_active"] = datetime.now().isoformat()
            self.users[user_id]["first_name"] = first_name
            self.users[user_id]["username"] = username
            self._save_users()
            return False
    
    def increment_download(self, user_id: int, size_mb: float = 0):
        user_id = str(user_id)
        if user_id in self.users:
            self.users[user_id]["downloads"] += 1
            self.users[user_id]["total_size_mb"] += size_mb
            self._save_users()
    
    def get_user(self, user_id: int) -> Dict:
        return self.users.get(str(user_id), {})
    
    def get_all_users(self) -> List[Dict]:
        return list(self.users.values())
    
    def get_total_stats(self) -> Dict:
        users = self.get_all_users()
        return {
            "total_users": len(users),
            "total_downloads": sum(u.get("downloads", 0) for u in users),
            "total_size_mb": sum(u.get("total_size_mb", 0) for u in users)
        }
    
    def get_top_users(self, limit: int = 10) -> List[Dict]:
        users = self.get_all_users()
        return sorted(users, key=lambda x: x.get("downloads", 0), reverse=True)[:limit]

# ==================== مدير السجلات ====================
class MessageLogger:
    def __init__(self):
        self.html_file = MESSAGES_HTML
        self._init_html()
    
    def _init_html(self):
        if not self.html_file.exists():
            with open(self.html_file, 'w', encoding='utf-8') as f:
                f.write("""<!DOCTYPE html>
<html dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>📬 سجل رسائل الدعم</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .message { background: white; padding: 15px; margin: 10px 0; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .header { background: #2196F3; color: white; padding: 10px; border-radius: 5px; }
        .user-id { color: #2196F3; font-weight: bold; }
        .time { color: #666; font-size: 0.9em; }
        .content { background: #f9f9f9; padding: 10px; border-radius: 5px; margin-top: 10px; }
        hr { border: 1px solid #ddd; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📬 سجل رسائل الدعم</h1>
    </div>
""")
    
    def log_message(self, user_id: int, username: str, first_name: str, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_message = html.escape(message)
        safe_name = html.escape(first_name)
        
        with open(self.html_file, 'a', encoding='utf-8') as f:
            f.write(f"""
    <div class="message">
        <div class="user-id">👤 <b>{safe_name}</b> (@{username or 'لا يوجد'})</div>
        <div class="time">🕐 {timestamp}</div>
        <div class="time">🆔 {user_id}</div>
        <div class="content">💬 {safe_message}</div>
    </div>
    <hr>
""")

# ==================== محمل الفيديو ====================
class VideoDownloader:
    PLATFORMS = {
        "youtube": {"name": "📺 يوتيوب", "pattern": r"(youtube\.com|youtu\.be)"},
        "instagram": {"name": "📸 انستغرام", "pattern": r"(instagram\.com)"},
        "tiktok": {"name": "🎵 تيك توك", "pattern": r"(tiktok\.com)"},
        "twitter": {"name": "🐦 تويتر", "pattern": r"(twitter\.com|x\.com)"},
        "facebook": {"name": "📘 فيسبوك", "pattern": r"(facebook\.com|fb\.watch)"},
    }
    
    QUALITIES = {
        "best": {"name": "🚀 أفضل جودة", "format": "best[ext=mp4]/best"},
        "medium": {"name": "📱 720p", "format": "best[height<=720][ext=mp4]/best[height<=720]"},
        "low": {"name": "📱 480p", "format": "best[height<=480][ext=mp4]/best[height<=480]"}
    }
    
    def __init__(self, download_path: Path):
        self.download_path = download_path
        self.download_path.mkdir(exist_ok=True)
    
    def detect_platform(self, url: str) -> tuple:
        url_lower = url.lower()
        for platform, info in self.PLATFORMS.items():
            if re.search(info["pattern"], url_lower):
                return platform, info["name"]
        return "unknown", "🌐 رابط خارجي"
    
    def extract_video_id(self, url: str, platform: str) -> str:
        try:
            if platform == "youtube":
                patterns = [
                    r"(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)",
                    r"(?:youtube\.com\/embed\/)([^&\n?#]+)"
                ]
                for pattern in patterns:
                    match = re.search(pattern, url)
                    if match:
                        return match.group(1)
            elif platform == "instagram":
                match = re.search(r"(?:reel|p)\/([^\/\n?#]+)", url)
                if match:
                    return match.group(1)
        except:
            pass
        return hashlib.md5(url.encode()).hexdigest()[:10]
    
    def get_quality_buttons(self, url_hash: str) -> InlineKeyboardMarkup:
        buttons = []
        row = []
        for i, (qid, qinfo) in enumerate(self.QUALITIES.items()):
            row.append(InlineKeyboardButton(
                qinfo["name"],
                callback_data=f"dl_{qid}_{url_hash}"
            ))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
        return InlineKeyboardMarkup(buttons)
    
    def download(self, url: str, quality: str) -> tuple:
        qconfig = self.QUALITIES.get(quality, self.QUALITIES["best"])
        platform_id, platform_name = self.detect_platform(url)
        video_id = self.extract_video_id(url, platform_id)
        
        timestamp = int(time.time())
        safe_filename = f"video_{video_id}_{timestamp}"
        output_template = str(self.download_path / f"{safe_filename}.%(ext)s")
        
        # تحسين صياغة الجودة لتكون أكثر مرونة
        if quality == "best":
            format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        elif quality == "medium":
            format_str = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]"
        else:
            format_str = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]"

        ydl_opts = {
            'format': format_str,
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'restrictfilenames': True,
            'socket_timeout': 30,
            'retries': 5,
            'fragment_retries': 5,
            'continuedl': True,
            'noplaylist': True,
            'geo_bypass': True,
            'no_check_certificate': True,
            'nocheckcertificate': True,
            'logger': logger,
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0',
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # محاولة استخراج المعلومات مع تحسينات للانستغرام
                try:
                    info = ydl.extract_info(url, download=False)
                except Exception as e:
                    logger.warning(f"محاولة استخراج أولى فشلت: {e}")
                    # محاولة ثانية بوضعية أقل صرامة وUA مختلف
                    ydl_opts['format'] = 'best'
                    if platform_id == 'instagram':
                        # تجربة تبديل الرابط لرابط الـ ddinstagram كحل احتياطي
                        # إزالة www. لأنها تسبب مشاكل DNS مع ddinstagram
                        alt_url = url.replace("www.instagram.com", "ddinstagram.com").replace("instagram.com", "ddinstagram.com")
                        logger.info(f"محاولة التحميل عبر رابط بديل: {alt_url}")
                        try:
                            # محاولة الاستخراج أولاً عبر yt-dlp بالرابط البديل
                            info = ydl.extract_info(alt_url, download=False)
                        except:
                            # حل أخير: محاولة كشط الرابط المباشر من ddinstagram يدوياً
                            try:
                                import requests
                                response = requests.get(alt_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
                                if response.status_code == 200:
                                    # البحث عن رابط الفيديو في الصفحة
                                    video_match = re.search(r'property="og:video" content="([^"]+)"', response.text)
                                    if video_match:
                                        direct_link = video_match.group(1)
                                        logger.info(f"تم العثور على رابط مباشر: {direct_link}")
                                        info = ydl.extract_info(direct_link, download=False)
                            except Exception as ex:
                                logger.error(f"فشلت جميع محاولات انستغرام: {ex}")
                                raise e
                    else:
                        info = ydl.extract_info(url, download=False)

                if not info:
                    return None, "❌ لا يمكن قراءة معلومات الفيديو"
                
                duration = info.get('duration') or 0
                if duration > MAX_DURATION:
                    minutes = duration // 60
                    return None, f"❌ الفيديو طويل جداً ({minutes} دقيقة)"
                
                # التحميل الفعلي
                try:
                    ydl.download([url])
                except Exception as e:
                    # إذا فشل التحميل بسبب "الملف فارغ"، نحاول بجودة 'best' مباشرة كحل أخير
                    if "empty" in str(e).lower():
                        logger.warning("محاولة التحميل بوضعية الاحتياط (fallback best)")
                        ydl_opts['format'] = 'best'
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl_retry:
                            ydl_retry.download([url])
                    else:
                        raise e
                
                files = list(self.download_path.glob(f"{safe_filename}.*"))
                if not files:
                    return None, "❌ لم يتم العثور على الملف بعد التحميل"
                
                file_path = files[0]
                file_size = file_path.stat().st_size
                
                # التحقق من أن الملف ليس فارغاً
                if file_size == 0:
                    try:
                        file_path.unlink()
                    except:
                        pass
                    return None, "❌ الملف المحمل فارغ، قد يكون الرابط محمي أو به مشكلة"
                
                if file_size > MAX_FILE_SIZE:
                    size_mb = file_size / (1024 * 1024)
                    try: file_path.unlink()
                    except: pass
                    return None, f"❌ الفيديو كبير جداً ({size_mb:.1f} MB)"
                
                size_mb = file_size / (1024 * 1024)
                video_info = {
                    "id": video_id,
                    "title": (info.get('title') or 'فيديو')[:50],
                    "duration": int(duration),
                    "size": size_mb,
                    "size_bytes": file_size,
                    "platform": platform_name,
                    "uploader": info.get('uploader') or 'غير معروف'
                }
                
                return file_path, video_info
                
        except Exception as e:
            logger.error(f"خطأ غير متوقع في المحمل: {e}")
            error_msg = str(e)
            if "empty" in error_msg.lower():
                return None, "❌ فشل التحميل: الخادم أرسل ملفاً فارغاً. جرب رابطاً آخر."
            return None, f"❌ حدث خطأ: {error_msg[:100]}"

# ==================== البوت الرئيسي ====================
class VideoBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.logger = MessageLogger()
        self.downloader = VideoDownloader(VIDEOS_DIR)
        
        if not token:
            logger.error("❌ TOKEN is missing! Please check your .env file or environment variables.")
            raise ValueError("TOKEN cannot be None. Make sure 'TOKEN' is set in your environment.")
            
        try:
            self.updater = Updater(token, use_context=True)
            self.dp = self.updater.dispatcher
        except Exception as e:
            logger.error(f"فشل في بدء Updater: {e}")
            # إذا فشل بسبب PTB v20، نحاول تحذير المستخدم
            if "unexpected keyword argument 'use_context'" in str(e):
                logger.error("❌ تم اكتشاف نسخة python-telegram-bot 20+ ولكن الكود مكتوب لنسخة 13.x")
                raise ImportError("Please install python-telegram-bot==13.15")
            raise e
        
        self._add_handlers()
        self._setup_commands()
        
        # تنظيف الملفات كل ساعة
        self.updater.job_queue.run_repeating(self.cleanup_job, interval=3600, first=10)
    
    def _setup_commands(self):
        commands = [
            ("start", "🚀 بدء"),
            ("help", "❓ مساعدة"),
            ("stats", "📊 إحصائياتي"),
            ("top", "🏆 المتصدرين"),
            ("support", "📬 دعم فني"),
            ("admin", "👑 لوحة التحكم"),
            ("cancel", "❌ إلغاء")
        ]
        try:
            from telegram import BotCommand
            self.updater.bot.set_my_commands([BotCommand(c[0], c[1]) for c in commands])
        except:
            pass
    
    def _add_handlers(self):
        # أوامر عامة
        self.dp.add_handler(CommandHandler("start", self.start))
        self.dp.add_handler(CommandHandler("help", self.help))
        self.dp.add_handler(CommandHandler("stats", self.stats))
        self.dp.add_handler(CommandHandler("top", self.top))
        self.dp.add_handler(CommandHandler("cancel", self.cancel))
        
        # نظام الدعم
        self.dp.add_handler(CommandHandler("support", self.support_start))
        self.dp.add_handler(MessageHandler(Filters.text & ~Filters.command & Filters.chat_type.private, self.handle_support_message))
        
        # نظام الرد للمشرف
        self.dp.add_handler(CommandHandler("reply", self.admin_reply_command))
        
        # لوحة تحكم الآدمين
        self.dp.add_handler(CommandHandler("admin", self.admin_panel))
        
        # معالج الأزرار
        self.dp.add_handler(CallbackQueryHandler(self.handle_buttons))
        
        # معالج النصوص (للروابط)
        self.dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_text))
        
        # معالج الأخطاء
        self.dp.add_error_handler(self.error_handler)
    
    def get_main_keyboard(self) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton("📥 تحميل فيديو", callback_data="main_download")],
            [InlineKeyboardButton("📊 إحصائياتي", callback_data="main_stats"), InlineKeyboardButton("🏆 المتصدرين", callback_data="main_top")],
            [InlineKeyboardButton("📬 دعم فني", callback_data="main_support"), InlineKeyboardButton("❓ مساعدة", callback_data="main_help")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    # ========== الأوامر العامة ==========
    
    def start(self, update: Update, context: CallbackContext):
        user = update.effective_user
        is_new = self.db.add_user(user.id, user.first_name, user.username)
        
        safe_name = html.escape(user.first_name)
        
        welcome = f"""
🎬 <b>مرحباً بك في بوت التحميل!</b>

{"✨ <b>مستخدم جديد!</b>" if is_new else f"👋 <b>أهلاً بعودتك يا {safe_name}!</b>"}

📥 <b>لتحميل أي فيديو:</b>
أرسل الرابط مباشرة

🎯 <b>المنصات المدعومة:</b>
• يوتيوب • انستغرام • تيك توك
• تويتر • فيسبوك

📬 <b>للتواصل مع الدعم:</b> /support
👑 <b>للمشرفين فقط:</b> /admin
        """
        
        update.effective_message.reply_text(
            welcome,
            parse_mode='HTML',
            reply_markup=self.get_main_keyboard()
        )
    
    def help(self, update: Update, context: CallbackContext):
        help_text = """
❓ **مساعدة البوت**

📌 **كيفية الاستخدام:**
1️⃣ انسخ رابط الفيديو
2️⃣ أرسله هنا
3️⃣ اختر الجودة
4️⃣ استلم الفيديو

✅ **نصائح:**
• تأكد أن الرابط عام
• الفيديوهات الطويلة تحتاج وقت
• الحد الأقصى: 50 ميجابايت

📬 **للاستفسارات:** /support
📊 **إحصائياتك:** /stats
        """
        update.effective_message.reply_text(help_text, parse_mode='Markdown')
    
    def stats(self, update: Update, context: CallbackContext):
        user = update.effective_user
        stats = self.db.get_user(user.id)
        
        if stats:
            safe_name = html.escape(stats.get('first_name', ''))
            text = f"""
📊 <b>إحصائياتك الشخصية</b>

👤 <b>الاسم:</b> {safe_name}
🆔 <b>المعرف:</b> {user.id}

📥 <b>التحميلات:</b> {stats.get('downloads', 0)}
💾 <b>الحجم الكلي:</b> {stats.get('total_size_mb', 0):.1f} MB

📅 <b>عضو منذ:</b> {stats.get('joined', '')[:10]}
            """
        else:
            text = "📊 لا توجد إحصائيات بعد"
        
        update.effective_message.reply_text(text, parse_mode='HTML')
    
    def top(self, update: Update, context: CallbackContext):
        top_users = self.db.get_top_users(10)
        
        if not top_users:
            update.effective_message.reply_text("🏆 لا يوجد مستخدمين بعد")
            return
        
        text = "🏆 <b>أفضل 10 مستخدمين</b>\n\n"
        
        for i, user in enumerate(top_users, 1):
            name = html.escape(user.get('first_name', 'مستخدم')[:20])
            downloads = user.get('downloads', 0)
            
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} {name}\n"
            text += f"   📥 {downloads} تحميل\n"
        
        update.effective_message.reply_text(text, parse_mode='HTML')
    
    def cancel(self, update: Update, context: CallbackContext):
        context.user_data.clear()
        update.effective_message.reply_text(
            "✅ تم الإلغاء",
            reply_markup=self.get_main_keyboard()
        )
        return ConversationHandler.END
    
    # ========== نظام الدعم ==========
    
    def support_start(self, update: Update, context: CallbackContext):
        update.effective_message.reply_text(
            "📬 **الدعم الفني**\n\n"
            "أرسل رسالتك وسيتم إرسالها للمشرف.\n"
            "أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['waiting_for_support'] = True
        return
    
    def handle_support_message(self, update: Update, context: CallbackContext):
        if context.user_data.get('waiting_for_support'):
            user = update.effective_user
            message = update.message.text
            
            # تسجيل الرسالة
            self.logger.log_message(user.id, user.username, user.first_name, message)
            
            # إرسال إشعار للمشرف
            try:
                context.bot.send_message(
                    ADMIN_ID,
                    f"📬 **رسالة دعم جديدة**\n\n"
                    f"👤 {user.first_name}\n"
                    f"🆔 {user.id}\n"
                    f"💬 {message}\n\n"
                    f"لإرسال رد:\n/reply {user.id} <الرسالة>",
                    parse_mode='Markdown'
                )
            except:
                pass
            
            update.effective_message.reply_text(
                "✅ تم إرسال رسالتك، سيتم الرد عليك قريباً",
                reply_markup=self.get_main_keyboard()
            )
            context.user_data['waiting_for_support'] = False
        else:
            self.handle_text(update, context)
    
    # ========== نظام الرد للمشرف ==========
    
    def admin_reply_command(self, update: Update, context: CallbackContext):
        if update.effective_user.id != ADMIN_ID:
            update.effective_message.reply_text("⛔ هذا الأمر للمشرف فقط")
            return
        
        try:
            args = context.args
            if len(args) < 2:
                update.effective_message.reply_text("❌ استخدم: /reply <user_id> <الرسالة>")
                return
            
            user_id = int(args[0])
            message = ' '.join(args[1:])
            
            context.bot.send_message(
                user_id,
                f"📬 **رد من الدعم الفني**\n\n{message}",
                parse_mode='Markdown'
            )
            
            update.effective_message.reply_text(f"✅ تم إرسال الرد للمستخدم {user_id}")
            
        except ValueError:
            update.effective_message.reply_text("❌ معرف المستخدم غير صحيح")
        except Exception as e:
            update.effective_message.reply_text(f"❌ فشل الإرسال: {str(e)[:100]}")
    
    # ========== لوحة تحكم الآدمين ==========
    
    def admin_panel(self, update: Update, context: CallbackContext):
        if update.effective_user.id != ADMIN_ID:
            update.effective_message.reply_text("⛔ هذا الأمر للمشرف فقط")
            return
        
        stats = self.db.get_total_stats()
        
        text = f"""
👑 **لوحة تحكم المشرف**

📊 **إحصائيات عامة:**
• المستخدمين: {stats['total_users']}
• التحميلات: {stats['total_downloads']}
• المساحة: {stats['total_size_mb']:.1f} MB

⚙️ **الإجراءات المتاحة:**
        """
        
        keyboard = [
            [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("📢 إذاعة رسالة", callback_data="admin_broadcast")],
            [InlineKeyboardButton("💾 تصدير الفيديوهات", callback_data="admin_export")],
            [InlineKeyboardButton("🧹 تنظيف الملفات", callback_data="admin_cleanup")],
            [InlineKeyboardButton("📋 معرف القناة", callback_data="admin_channel_id")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="cancel")]
        ]
        
        update.effective_message.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # ========== معالج الأزرار ==========
    
    def handle_buttons(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        data = query.data
        
        if data == "cancel":
            query.edit_message_text("✅ تم الإلغاء")
            return
            
        # أزرار القائمة الرئيسية
        if data.startswith("main_"):
            action = data.replace("main_", "")
            if action == "download":
                query.message.reply_text("📤 أرسل رابط الفيديو الآن")
            elif action == "stats":
                self.stats(update, context)
            elif action == "top":
                self.top(update, context)
            elif action == "support":
                self.support_start(update, context)
            elif action == "help":
                self.help(update, context)
            return
        
        # أزرار الآدمين
        if data.startswith("admin_"):
            if update.effective_user.id != ADMIN_ID:
                query.edit_message_text("⛔ هذا الأمر للمشرف فقط")
                return
            
            action = data.replace("admin_", "")
            
            if action == "stats":
                stats = self.db.get_total_stats()
                query.edit_message_text(
                    f"📊 **الإحصائيات**\n\n"
                    f"👥 المستخدمين: {stats['total_users']}\n"
                    f"📥 التحميلات: {stats['total_downloads']}\n"
                    f"💾 المساحة: {stats['total_size_mb']:.1f} MB"
                )
            
            elif action == "users":
                users = self.db.get_all_users()
                text = "👥 **قائمة المستخدمين**\n\n"
                for user in users[:20]:  # أول 20 مستخدم
                    text += f"• {user['first_name']} (@{user['username'] or 'لا يوجد'})\n"
                    text += f"  🆔 {user['id']} | 📥 {user['downloads']}\n\n"
                
                if len(users) > 20:
                    text += f"...و {len(users)-20} آخرين"
                
                query.edit_message_text(text[:4000], parse_mode='Markdown')
            
            elif action == "export":
                query.edit_message_text("⏳ جاري إنشاء ملف الفيديوهات...")
                
                zip_path = DATA_DIR / "exports" / f"videos_{int(time.time())}.zip"
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for video in VIDEOS_DIR.glob("*"):
                        zipf.write(video, video.name)
                
                with open(zip_path, 'rb') as f:
                    query.message.reply_document(
                        document=f,
                        filename="videos.zip",
                        caption="✅ ملف الفيديوهات المضغوط"
                    )
                
                zip_path.unlink()
                query.delete_message()
            
            elif action == "cleanup":
                cleaned = 0
                for f in VIDEOS_DIR.glob("*"):
                    try:
                        f.unlink()
                        cleaned += 1
                    except:
                        pass
                query.edit_message_text(f"🧹 تم حذف {cleaned} ملف")
            
            elif action == "channel_id":
                if CHANNEL_ID and CHANNEL_ID != "@your_channel_username":
                    query.edit_message_text(f"📋 معرف القناة الحالي:\n`{CHANNEL_ID}`")
                else:
                    query.edit_message_text(
                        "❌ لم يتم تعيين معرف القناة بعد\n\n"
                        "لتعيين القناة، أضف البوت مشرفاً في القناة\n"
                        "ثم أرسل أي رسالة في القناة وارسل معرفها هنا"
                    )
            
            elif action == "broadcast":
                context.user_data['admin_state'] = 'broadcast'
                query.edit_message_text(
                    "📢 **إذاعة رسالة**\n\n"
                    "أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين:"
                )
                return
        
        # أزرار التحميل
        if data.startswith("dl_"):
            parts = data.split('_')
            if len(parts) >= 3:
                quality = parts[1]
                url_hash = parts[2]
                url = context.user_data.get(f'url_{url_hash}')
                
                if not url:
                    query.edit_message_text("❌ انتهت صلاحية الرابط، أرسله مرة أخرى")
                    return
                
                self._process_download(query, context, url, quality, url_hash)
    
    def _process_download(self, query, context, url, quality, url_hash):
        quality_info = self.downloader.QUALITIES[quality]
        
        query.edit_message_text(
            f"⏳ **جاري التحميل...**\n"
            f"🎯 الجودة: {quality_info['name']}",
            parse_mode='Markdown'
        )
        
        result = self.downloader.download(url, quality)
        
        if isinstance(result, tuple) and len(result) == 2:
            if result[0] is None:
                query.edit_message_text(result[1])
                return
            file_path, info = result
        else:
            query.edit_message_text("❌ فشل التحميل")
            return
        
        # تحديث الإحصائيات
        self.db.increment_download(query.from_user.id, info['size'])
        
        # إرسال للقناة إذا وجدت
        if CHANNEL_ID and CHANNEL_ID != "@your_channel_username":
            try:
                with open(file_path, 'rb') as f:
                    context.bot.send_video(
                        chat_id=CHANNEL_ID,
                        video=f,
                        caption=f"📥 تم التحميل بواسطة {query.from_user.first_name}",
                        supports_streaming=True
                    )
            except Exception as e:
                logger.error(f"فشل إرسال للقناة: {e}")
        
        # رفع للمستخدم
        query.edit_message_text("📤 **جاري رفع الفيديو...**", parse_mode='Markdown')
        
        try:
            # تنظيف العنوان من الرموز التي قد تسبب أخطاء
            import html
            safe_title = html.escape(info['title'])
            safe_platform = html.escape(info['platform'])
            
            caption = f"""
✅ <b>تم التحميل بنجاح!</b>

🌐 <b>المصدر:</b> {safe_platform}
📹 <b>العنوان:</b> {safe_title}
⏱️ <b>المدة:</b> {info['duration']//60}:{info['duration']%60:02d}
📏 <b>الحجم:</b> {info['size']:.1f} MB
🎯 <b>الجودة:</b> {quality_info['name']}

📥 أرسل رابطاً آخر للتحميل
            """
            
            with open(file_path, 'rb') as f:
                query.message.reply_video(
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    timeout=300,
                    parse_mode='HTML'
                )
            
            query.delete_message()
            
        except Exception as e:
            logger.error(f"خطأ في إرسال الفيديو: {e}")
            query.edit_message_text(f"❌ فشل الرفع: {str(e)[:100]}")
        
        finally:
            try:
                file_path.unlink()
            except:
                pass
    
    def handle_text(self, update: Update, context: CallbackContext):
        if not update.message or not update.message.text:
            return
        text = update.message.text
        
        # أزرار لوحة المفاتيح
        if text == "📥 تحميل فيديو":
            update.effective_message.reply_text("📤 أرسل رابط الفيديو الآن")
            return
        elif text == "📊 إحصائياتي":
            self.stats(update, context)
            return
        elif text == "🏆 المتصدرين":
            self.top(update, context)
            return
        elif text == "📬 دعم فني":
            self.support_start(update, context)
            return
        elif text == "❓ مساعدة":
            self.help(update, context)
            return
        
        # التحقق من الرابط
        urls = re.findall(r'https?://[^\s]+', text)
        
        if urls:
            url = urls[0]
            platform_id, platform_name = self.downloader.detect_platform(url)
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            
            context.user_data[f'url_{url_hash}'] = url
            
            text = f"{platform_name} ✅ **تم اكتشاف الفيديو**\n\nاختر الجودة:"
            keyboard = self.downloader.get_quality_buttons(url_hash)
            
            update.effective_message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)
        else:
            # إذا كان المستخدم في وضع الدعم
            if context.user_data.get('waiting_for_support'):
                self.handle_support_message(update, context)
            # إذا كان المشرف في وضع الإذاعة
            elif context.user_data.get('admin_state') == 'broadcast' and update.effective_user.id == ADMIN_ID:
                self._handle_admin_broadcast(update, context)
            else:
                update.effective_message.reply_text(
                    "❌ هذا ليس رابط فيديو صحيح\n"
                    "أرسل رابطاً من يوتيوب، انستغرام، تيك توك..."
                )
    
    def _handle_admin_broadcast(self, update: Update, context: CallbackContext):
        message = update.message.text
        users = self.db.get_all_users()
        sent = 0
        
        status_msg = update.effective_message.reply_text(f"⏳ جاري الإرسال إلى {len(users)} مستخدم...")
        
        for user in users:
            try:
                context.bot.send_message(
                    user['id'],
                    f"📢 **رسالة إدارية**\n\n{message}",
                    parse_mode='Markdown'
                )
                sent += 1
                time.sleep(0.05)
            except:
                continue
        
        status_msg.edit_text(f"✅ تم إرسال الرسالة إلى {sent}/{len(users)} مستخدم")
        context.user_data['admin_state'] = None
    
    # ========== وظائف مساعدة ==========
    
    def cleanup_job(self, context: CallbackContext):
        try:
            cleaned = 0
            for f in VIDEOS_DIR.glob("*"):
                if time.time() - f.stat().st_mtime > 3600:
                    f.unlink()
                    cleaned += 1
            logger.info(f"تنظيف دوري: {cleaned} ملف")
        except Exception as e:
            logger.error(f"خطأ في التنظيف: {e}")
    
    def error_handler(self, update: Update, context: CallbackContext):
        logger.error(f"خطأ: {context.error}")
        try:
            if update and update.effective_message:
                update.effective_message.reply_text(
                    "❌ حدث خطأ غير متوقع\n"
                    "الرجاء المحاولة مرة أخرى"
                )
        except:
            pass
    
    def run(self):
        print("╔════════════════════════════════════╗")
        print("║    🚀 بوت التحميل الاحترافي       ║")
        print("╠════════════════════════════════════╣")
        print("║ ✅ الأزرار تعمل                    ║")
        print("║ ✅ نظام الدعم                       ║")
        print("║ ✅ لوحة تحكم المشرف                 ║")
        print("║ ✅ تصدير الفيديوهات                 ║")
        print("║ ✅ حفظ السجلات                      ║")
        print("║ ✅ الإذاعة للمستخدمين               ║")
        print("║ ✅ التحميل للقناة                    ║")
        print("╚════════════════════════════════════╝")
        print(f"\n👑 آي دي الآدمين: {ADMIN_ID}")
        print(f"📋 القناة: {CHANNEL_ID}\n")
        
        self.updater.start_polling()
        self.updater.idle()


# ==================== التشغيل ====================
def run_server():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    
    class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running and awake!")

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"🚀 Local HTTP server listening on port {port} (for health checks)")
    server.serve_forever()

def self_ping():
    import urllib.request
    ping_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PING_URL")
    if not ping_url:
        # Fallback to localhost if no external URL is set, so the HTTP server is at least hit locally
        port = os.environ.get("PORT", "8080")
        ping_url = f"http://localhost:{port}/"

    print(f"⏳ Setting up self-ping every 5 minutes to keep alive: {ping_url}")
    while True:
        try:
            # Ping first, then sleep
            req = urllib.request.Request(ping_url, headers={'User-Agent': 'Mozilla/5.0'})
            response = urllib.request.urlopen(req, timeout=10)
            print(f"[Self-Ping] Status: {response.getcode()} at {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"[Self-Ping] Error: {e} at {datetime.now().strftime('%H:%M:%S')}")
        
        time.sleep(5 * 60)

if __name__ == "__main__":
    import threading

    # Start the simple HTTP server thread
    threading.Thread(target=run_server, daemon=True).start()
    
    # Start the self pinging thread
    threading.Thread(target=self_ping, daemon=True).start()

    bot = VideoBot(TOKEN)
    bot.run()