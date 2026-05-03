import asyncio
import subprocess
import tempfile
import os
import sqlite3
import json
import platform
import datetime
from pathlib import Path
import logging
import random
import numpy as np
import hashlib

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from geopy.geocoders import Nominatim
from PIL import Image, ImageFilter, ImageEnhance
from PIL.ExifTags import TAGS

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger('aiogram').setLevel(logging.WARNING)

# --- КОНФИГ ---
TOKEN = "8074636787:AAH8IzKzJuUnV5brx4N9ske7NRW6RWWUwgY"
PASSWORD = "pD$V<N2}9v8e!|oO|o}uZ1va_Hlrq+"
FREE_LIMIT = 3
COMPRESSION_SIZES = [5, 10, 20]

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMP = Path(tempfile.gettempdir()) / "privacy_bot"
TEMP.mkdir(exist_ok=True)
logger.info(f"📁 Temp: {TEMP}")

# ═══════════════════════════════════════════════════════════════
# СИСТЕМА ПАМЯТИ (ИСТОРИЯ ОБРАБОТКИ)
# ═══════════════════════════════════════════════════════════════

user_files = {}  # Текущие файлы пользователя


class FileHistory:
    """Хранит историю обработки файлов пользователя"""

    def __init__(self):
        self.history = {}  # user_id -> {filename, orig_path, processed_versions}

    def add_file(self, user_id, filename, orig_path, lang, is_audio=False):
        """Добавляет новый файл в историю"""
        self.history[user_id] = {
            'filename': filename,
            'orig_path': orig_path,
            'lang': lang,
            'is_audio': is_audio,
            'versions': {},  # Хранит разные версии обработки
            'created_at': datetime.datetime.now()
        }
        logger.info(f"File added to history: {user_id} -> {filename}")

    def add_version(self, user_id, version_name, file_path):
        """Добавляет обработанную версию файла"""
        if user_id in self.history:
            self.history[user_id]['versions'][version_name] = {
                'path': str(file_path),
                'created_at': datetime.datetime.now()
            }
            logger.info(f"Version added: {user_id} -> {version_name}")

    def get_file(self, user_id):
        """Получает информацию о файле"""
        return self.history.get(user_id)

    def get_version(self, user_id, version_name):
        """Получает путь к обработанной версии"""
        if user_id in self.history:
            version = self.history[user_id]['versions'].get(version_name)
            if version:
                return Path(version['path'])
        return None

    def get_original(self, user_id):
        """Получает путь к оригинальному файлу"""
        if user_id in self.history:
            return Path(self.history[user_id]['orig_path'])
        return None

    def cleanup(self, user_id):
        """Удаляет все файлы пользователя из памяти"""
        if user_id in self.history:
            file_data = self.history[user_id]
            try:
                # Удаляем оригинал
                if file_data['orig_path']:
                    Path(file_data['orig_path']).unlink(missing_ok=True)

                # Удаляем все версии
                for version_info in file_data['versions'].values():
                    Path(version_info['path']).unlink(missing_ok=True)

                del self.history[user_id]
                logger.info(f"Cleaned up history for user: {user_id}")
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    def list_versions(self, user_id):
        """Возвращает список доступных версий"""
        if user_id in self.history:
            return list(self.history[user_id]['versions'].keys())
        return []


# Глобальный объект истории
file_history = FileHistory()

try:
    geolocator = Nominatim(user_agent="privacy_bot_v2")
    logger.info("✅ Geocoder OK")
except:
    geolocator = None

# --- ТЕКСТЫ ---
TEXTS = {
    'ru': {
        'welcome': "👋 Привет!\n\nОсталось {remains} бесплатных попыток.",
        'no_attempts': "❌ Попытки закончились.\n\nПароль:",
        'auth_ok': "✅ Пароль верный! Безлимит.",
        'send_file': "📤 Отправьте файл!",
        'processing': "⏳ Обработка...",
        'choose_action': "📋 Выберите действие:",
        'choose_compression': "📦 Размер:",
        'choose_next': "📋 Выберите следующее действие:",
        'done': "✅ Готово!\n{original} → {compressed}",
        'done_unlim': "✅ Готово!\n{original} → {compressed}",
        'done_no_compression': "✅ Очищено!",
        'done_filter': "✅ Готово! Хэш изменён.",
        'error': "❌ Ошибка: {e}",
        'compression_btn': "📦 Сжать {size}МБ",
        'clean_btn': "🧹 Очистить",
        'analyze_btn': "📊 Анализ",
        'antdup_btn': "🔐 Антидублирование",
        'new_file_btn': "📁 Новый файл",
        'select_lang': "Язык:",
        'file_saved': "💾 Файл сохранён в памяти. Можете обработать его несколькими способами.",
        'available_versions': "📂 Доступные версии:\n{versions}",
    },
    'en': {
        'welcome': "👋 Hi!\n\n{remains} attempts left.",
        'no_attempts': "❌ No attempts.\n\nPassword:",
        'auth_ok': "✅ Unlimited!",
        'send_file': "📤 Send file!",
        'processing': "⏳ Processing...",
        'choose_action': "📋 Choose:",
        'choose_compression': "📦 Size:",
        'choose_next': "📋 Choose next action:",
        'done': "✅ Done!\n{original} → {compressed}",
        'done_unlim': "✅ Done!\n{original} → {compressed}",
        'done_no_compression': "✅ Cleaned!",
        'done_filter': "✅ Done! Hash changed.",
        'error': "❌ Error: {e}",
        'compression_btn': "📦 Compress {size}MB",
        'clean_btn': "🧹 Clean",
        'analyze_btn': "📊 Analyze",
        'antdup_btn': "🔐 Anti-Duplicate",
        'new_file_btn': "📁 New file",
        'select_lang': "Language:",
        'file_saved': "💾 File saved in memory. You can process it in multiple ways.",
        'available_versions': "📂 Available versions:\n{versions}",
    }
}


# ═══════════════════════════════════════════════════════════════
# АНТИДУБЛИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════

class AntiDuplicate:
    """
    Изменяет изображение так, чтобы:
    - Для глаза: полностью идентично оригиналу
    - Для хэша: полностью другое
    - Для соцсетей: проходит проверку на дублирование
    """

    @staticmethod
    def rotate_imperceptible(image):
        """Поворот на 0.01 градус + обратный поворот"""
        logger.info("Micro-rotation filter...")

        rotated = image.rotate(0.01, expand=False, resample=Image.BICUBIC)
        back = rotated.rotate(-0.01, expand=False, resample=Image.BICUBIC)
        return back

    @staticmethod
    def pixel_level_noise(image, seed=None):
        """Добавляет шум на уровне отдельных пикселей"""
        logger.info("Applying pixel-level noise...")

        img_array = np.array(image, dtype=np.int16)

        if seed:
            np.random.seed(seed)

        height, width = img_array.shape[:2]
        total_pixels = height * width
        pixels_to_modify = max(1, int(total_pixels * 0.001))

        for _ in range(pixels_to_modify):
            y = random.randint(0, height - 1)
            x = random.randint(0, width - 1)

            delta = random.choice([-2, -1, 1, 2])

            if len(img_array.shape) == 3:
                channel = random.randint(0, 2)
                img_array[y, x, channel] = np.clip(img_array[y, x, channel] + delta, 0, 255)
            else:
                img_array[y, x] = np.clip(img_array[y, x] + delta, 0, 255)

        result = Image.fromarray(np.uint8(np.clip(img_array, 0, 255)))
        return result

    @staticmethod
    def advanced_hash_change(input_path: Path, output_path: Path):
        """САМЫЙ ЭФФЕКТИВНЫЙ МЕТОД"""
        logger.info("Advanced hash change algorithm...")

        with Image.open(input_path) as img:
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # 1. Микроротация
            img = img.rotate(0.015, expand=False, resample=Image.BICUBIC)
            img = img.rotate(-0.015, expand=False, resample=Image.BICUBIC)

            # 2. Вибрирующий фильтр
            img_array = np.array(img, dtype=np.float32)

            np.random.seed(12345)
            noise = np.random.uniform(-0.5, 0.5, img_array.shape)
            img_array = img_array + noise
            img_array = np.clip(img_array, 0, 255)

            img = Image.fromarray(np.uint8(img_array))

            # 3. Сохраняем с разными JPEG параметрами
            import io
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=99, progressive=True, optimize=False)

            buffer.seek(0)
            img_reloaded = Image.open(buffer).convert('RGB')
            img_reloaded.save(output_path, format='JPEG', quality=100, progressive=False, optimize=False)

            logger.info(f"Advanced hash changed: {output_path}")


# ═══════════════════════════════════════════════════════════════
# METADATA
# ═══════════════════════════════════════════════════════════════

class MetadataExtractor:
    @staticmethod
    def extract_metadata(file_path: str) -> dict:
        """Извлекает метаданные"""
        metadata = {}

        try:
            with Image.open(file_path) as img:
                exif = img.getexif()

                if exif:
                    for tag_id, value in exif.items():
                        tag = TAGS.get(tag_id, tag_id)
                        try:
                            if isinstance(value, bytes):
                                value = value.decode('utf-8', errors='ignore')
                            metadata[f"EXIF:{tag}"] = str(value)[:100]
                        except:
                            pass

                try:
                    width, height = img.size
                    metadata['Image:Width'] = f"{width}px"
                    metadata['Image:Height'] = f"{height}px"
                except:
                    pass

        except Exception as e:
            logger.warning(f"Image read: {e}")

        try:
            stat_info = os.stat(file_path)

            file_size_mb = stat_info.st_size / (1024 * 1024)
            metadata['File:Size'] = f"{file_size_mb:.2f}MB"

            mod_time = datetime.datetime.fromtimestamp(stat_info.st_mtime)
            metadata['File:Modified'] = mod_time.strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            logger.warning(f"File stat: {e}")

        logger.info(f"Metadata fields: {len(metadata)}")
        return metadata

    @staticmethod
    def format_metadata(metadata: dict, filename: str, lang: str = 'ru') -> str:
        """Форматирует метаданные"""
        if lang not in TEXTS:
            lang = 'ru'

        result = f"📋 <b>Файл:</b> <code>{filename}</code>\n\n"

        file_size = metadata.get('File:Size')
        if file_size:
            result += f"💾 <b>Размер:</b> {file_size}\n"

        img_width = metadata.get('Image:Width')
        img_height = metadata.get('Image:Height')
        if img_width and img_height:
            result += f"🖼️ <b>Размер:</b> {img_width} x {img_height}\n"

        file_mod = metadata.get('File:Modified')
        exif_date = metadata.get('EXIF:DateTime')

        if exif_date or file_mod:
            result += f"📅 <b>Дата:</b> "
            if exif_date:
                result += f"{exif_date}\n"
            elif file_mod:
                result += f"{file_mod}\n"

        make = metadata.get('EXIF:Make')
        model = metadata.get('EXIF:Model')
        if make or model:
            result += f"📱 <b>Устройство:</b> {make} {model}\n"

        iso = metadata.get('EXIF:ISO')
        if iso:
            result += f"📊 <b>ISO:</b> {iso}\n"

        aperture = metadata.get('EXIF:FNumber')
        if aperture:
            result += f"🎯 <b>Диафрагма:</b> {aperture}\n"

        if metadata:
            result += f"\n<b>Всего:</b> {len(metadata)} полей\n\n"

            for i, (key, value) in enumerate(sorted(metadata.items())[:15]):
                if value:
                    val = str(value)
                    if len(val) > 80:
                        val = val[:77] + "..."
                    result += f"  <code>{key}:</code> {val}\n"

            if len(metadata) > 15:
                result += f"\n  <i>...и еще {len(metadata) - 15}</i>"
        else:
            result += "⚠️ <b>Метаданные удалены Telegram</b>\n"
            result += "Отправьте оригинальный файл с ПК"

        return result


# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect('users_v2.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users
                      (
                          user_id
                          INTEGER
                          PRIMARY
                          KEY,
                          usage_count
                          INTEGER
                          DEFAULT
                          0,
                          is_authorized
                          INTEGER
                          DEFAULT
                          0,
                          lang
                          TEXT
                          DEFAULT
                          'none'
                      )''')
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = sqlite3.connect('users_v2.db')
    cursor = conn.cursor()
    cursor.execute("SELECT usage_count, is_authorized, lang FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?, 0, 0, 'none')", (user_id,))
        conn.commit()
        user = (0, 0, 'none')
    conn.close()
    return user


def set_lang(user_id, lang):
    conn = sqlite3.connect('users_v2.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
    conn.commit()
    conn.close()


def increment_usage(user_id):
    conn = sqlite3.connect('users_v2.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET usage_count = usage_count + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def authorize_user(user_id):
    conn = sqlite3.connect('users_v2.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_authorized = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# COMPRESSION
# ═══════════════════════════════════════════════════════════════

def get_file_size_mb(path):
    try:
        return os.path.getsize(path) / (1024 * 1024) if path.exists() else 0
    except:
        return 0


def compress_image(input_path: Path, output_path: Path, target_size_mb: int):
    logger.info(f"Compressing image...")
    quality = 95
    for _ in range(20):
        img = Image.open(input_path)
        img.save(output_path, quality=quality, optimize=True)
        if get_file_size_mb(output_path) <= target_size_mb or quality <= 10:
            break
        quality -= 5


def compress_video_audio(input_path: Path, output_path: Path, target_size_mb: int, is_audio: bool = False):
    logger.info(f"Compressing...")
    try:
        duration = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1:nokey=1", str(input_path)],
            stderr=subprocess.DEVNULL
        ))
    except:
        duration = 0

    if duration == 0:
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-map_metadata", "-1", "-c", "copy", "-y", str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return

    bitrate = int((target_size_mb * 8192) / duration)

    if is_audio:
        cmd = ["ffmpeg", "-i", str(input_path), "-b:a", f"{min(bitrate, 128)}k",
               "-map_metadata", "-1", "-y", str(output_path)]
    else:
        cmd = ["ffmpeg", "-i", str(input_path), "-b:v", f"{max(bitrate - 128, 100)}k",
               "-b:a", f"{min(128, bitrate // 10)}k", "-map_metadata", "-1", "-y", str(output_path)]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def clean_metadata_only(input_path: Path, output_path: Path):
    ext = input_path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".bmp", ".gif"}:
        with Image.open(input_path) as img:
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(img.getdata())
            clean_img.save(output_path, quality=100)
    else:
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-map_metadata", "-1", "-c", "copy", "-y", str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )


# ═══════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"))
    builder.add(types.InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en"))
    await message.answer("Select language:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    set_lang(user_id, lang)

    usage, is_auth, _ = get_user(user_id)
    remains = FREE_LIMIT - usage

    text = (TEXTS[lang]['send_file'] if is_auth else
            TEXTS[lang]['welcome'].format(remains=remains) if remains > 0 else
            TEXTS[lang]['no_attempts'])

    await callback.message.edit_text(text)
    await callback.answer()


@dp.message(F.text)
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    usage, is_auth, lang = get_user(user_id)

    if lang == 'none':
        await cmd_start(message)
        return

    if message.text == PASSWORD:
        authorize_user(user_id)
        await message.answer(TEXTS[lang]['auth_ok'])
        return


@dp.message(F.photo | F.video | F.document | F.audio | F.voice | F.video_note)
async def handle_file(message: types.Message):
    user_id = message.from_user.id
    usage, is_auth, lang = get_user(user_id)

    if lang == 'none':
        await cmd_start(message)
        return

    if not is_auth and usage >= FREE_LIMIT:
        await message.answer(TEXTS[lang]['no_attempts'])
        return

    file = None
    name = "file"
    is_audio = False

    if message.photo:
        file = message.photo[-1]
        name = f"photo_{file.file_unique_id}.jpg"
    elif message.video:
        file = message.video
        name = file.file_name or "video.mp4"
    elif message.document:
        file = message.document
        name = file.file_name or "document"
    elif message.audio:
        file = message.audio
        is_audio = True
        name = file.file_name or "audio.mp3"
    elif message.voice or message.video_note:
        file = message.voice or message.video_note
        is_audio = True
        name = f"voice_{file.file_unique_id}.ogg"

    status = await message.reply(TEXTS[lang]['processing'])

    try:
        file_info = await bot.get_file(file.file_id)
        orig = TEMP / name
        await bot.download_file(file_info.file_path, orig)

        if not orig.exists():
            await status.edit_text("Download failed")
            return

        # ДОБАВЛЯЕМ В ИСТОРИЮ
        file_history.add_file(user_id, name, str(orig), lang, is_audio)

        await status.delete()

        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['analyze_btn'], callback_data="analyze"))
        builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['clean_btn'], callback_data="clean_only"))
        builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['antdup_btn'], callback_data="antdup"))

        await message.reply(TEXTS[lang]['file_saved'], reply_markup=builder.as_markup())

    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(TEXTS[lang]['error'].format(e=str(e)[:30]))


def create_next_action_buttons(lang, user_id):
    """Создаёт кнопки для выбора следующего действия"""
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['clean_btn'], callback_data="clean_only"))
    builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['antdup_btn'], callback_data="antdup"))
    builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['analyze_btn'], callback_data="analyze"))
    builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['new_file_btn'], callback_data="new_file"))
    return builder.as_markup()


@dp.callback_query(F.data == "analyze")
async def analyze(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    file_data = file_history.get_file(user_id)
    if not file_data:
        await callback.answer("❌ Файл не найден", show_alert=True)
        return

    orig = Path(file_data['orig_path'])
    filename = file_data['filename']
    lang = file_data.get('lang', 'ru')

    if lang not in TEXTS:
        lang = "ru"

    status = await callback.message.reply(TEXTS[lang]['processing'])

    try:
        if not orig.exists():
            await status.edit_text("Expired")
            return

        metadata = MetadataExtractor.extract_metadata(str(orig))
        report = MetadataExtractor.format_metadata(metadata, filename, lang)

        await status.delete()
        await callback.message.reply(report, parse_mode="HTML", disable_web_page_preview=True)

        # Показываем варианты сжатия
        builder = InlineKeyboardBuilder()
        for size in COMPRESSION_SIZES:
            builder.add(types.InlineKeyboardButton(
                text=TEXTS[lang]['compression_btn'].format(size=size),
                callback_data=f"compress_{size}"
            ))
        builder.add(types.InlineKeyboardButton(text=TEXTS[lang]['new_file_btn'], callback_data="new_file"))

        await callback.message.reply(TEXTS[lang]['choose_compression'], reply_markup=builder.as_markup())
        await callback.answer("✅")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(TEXTS[lang]['error'].format(e=str(e)[:30]))


@dp.callback_query(F.data.startswith("compress_"))
async def compress(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    target_size = int(callback.data.split("_")[1])

    file_data = file_history.get_file(user_id)
    if not file_data:
        await callback.answer("❌ Файл не найден", show_alert=True)
        return

    orig = Path(file_data['orig_path'])
    filename = file_data['filename']
    lang = file_data.get("lang", "ru")
    is_audio = file_data.get("is_audio", False)

    if lang not in TEXTS:
        lang = "ru"

    usage, is_auth, _ = get_user(user_id)

    if not is_auth and usage >= FREE_LIMIT:
        await callback.answer(TEXTS[lang]['no_attempts'], show_alert=True)
        return

    status = await callback.message.reply(TEXTS[lang]['processing'])

    try:
        if not orig.exists():
            await status.edit_text("Expired")
            return

        ext = orig.suffix.lower()
        out = TEMP / f"compressed_{target_size}mb_{filename}"
        original_size = get_file_size_mb(orig)

        if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".bmp", ".gif"}:
            compress_image(orig, out, target_size)
        elif ext in {".mp3", ".aac", ".flac", ".m4a", ".wma", ".ogg"}:
            compress_video_audio(orig, out, target_size, is_audio=True)
        else:
            compress_video_audio(orig, out, target_size, is_audio=False)

        if not out.exists():
            await status.edit_text("Failed")
            return

        compressed_size = get_file_size_mb(out)

        # СОХРАНЯЕМ В ИСТОРИЮ
        file_history.add_version(user_id, f'compressed_{target_size}mb', out)

        await status.delete()

        size_orig = f"{original_size:.1f}МБ" if lang == 'ru' else f"{original_size:.1f}MB"
        size_comp = f"{compressed_size:.1f}МБ" if lang == 'ru' else f"{compressed_size:.1f}MB"

        caption = TEXTS[lang]['done_unlim'].format(
            original=size_orig,
            compressed=size_comp
        ) if is_auth else TEXTS[lang]['done'].format(
            original=size_orig,
            compressed=size_comp
        )

        await callback.message.reply_document(
            types.FSInputFile(out, filename=f"COMPRESSED_{filename}"),
            caption=caption,
            reply_markup=create_next_action_buttons(lang, user_id)
        )

        if not is_auth:
            increment_usage(user_id)

        await callback.answer("✅")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(TEXTS[lang]['error'].format(e=str(e)[:30]))


@dp.callback_query(F.data == "clean_only")
async def clean(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    file_data = file_history.get_file(user_id)
    if not file_data:
        await callback.answer("❌ Файл не найден", show_alert=True)
        return

    orig = Path(file_data['orig_path'])
    filename = file_data['filename']
    lang = file_data.get("lang", "ru")

    if lang not in TEXTS:
        lang = "ru"

    usage, is_auth, _ = get_user(user_id)

    if not is_auth and usage >= FREE_LIMIT:
        await callback.answer(TEXTS[lang]['no_attempts'], show_alert=True)
        return

    status = await callback.message.reply(TEXTS[lang]['processing'])

    try:
        if not orig.exists():
            await status.edit_text("Expired")
            return

        out = TEMP / f"cleaned_{filename}"
        clean_metadata_only(orig, out)

        if not out.exists():
            await status.edit_text("Failed")
            return

        # СОХРАНЯЕМ В ИСТОРИЮ
        file_history.add_version(user_id, 'cleaned', out)

        await status.delete()

        await callback.message.reply_document(
            types.FSInputFile(out, filename=f"CLEANED_{filename}"),
            caption=TEXTS[lang]['done_no_compression'],
            reply_markup=create_next_action_buttons(lang, user_id)
        )

        if not is_auth:
            increment_usage(user_id)

        await callback.answer("✅")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(TEXTS[lang]['error'].format(e=str(e)[:30]))


@dp.callback_query(F.data == "antdup")
async def apply_antdup(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    file_data = file_history.get_file(user_id)
    if not file_data:
        await callback.answer("❌ Файл не найден", show_alert=True)
        return

    orig = Path(file_data['orig_path'])
    filename = file_data['filename']
    lang = file_data.get("lang", "ru")

    if lang not in TEXTS:
        lang = "ru"

    usage, is_auth, _ = get_user(user_id)

    if not is_auth and usage >= FREE_LIMIT:
        await callback.answer(TEXTS[lang]['no_attempts'], show_alert=True)
        return

    status = await callback.message.reply(TEXTS[lang]['processing'])

    try:
        if not orig.exists():
            await status.edit_text("Expired")
            return

        ext = orig.suffix.lower()

        if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".bmp", ".gif"}:
            out = TEMP / f"antdup_{filename}"
            AntiDuplicate.advanced_hash_change(orig, out)

            # СОХРАНЯЕМ В ИСТОРИЮ
            file_history.add_version(user_id, 'antdup', out)

            await status.delete()
            await callback.message.reply_document(
                types.FSInputFile(out, filename=f"ANTDUP_{filename}"),
                caption=TEXTS[lang]['done_filter'],
                reply_markup=create_next_action_buttons(lang, user_id)
            )

            if not is_auth:
                increment_usage(user_id)

            await callback.answer("✅")
        else:
            await status.edit_text("❌ " + ("Только для изображений" if lang == 'ru' else "Images only"))

    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(TEXTS[lang]['error'].format(e=str(e)[:30]))


@dp.callback_query(F.data == "new_file")
async def new_file(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = file_history.get_file(user_id)

    if not lang:
        await callback.answer("Error")
        return

    lang = lang.get('lang', 'ru')

    # Очищаем историю
    file_history.cleanup(user_id)

    await callback.message.edit_text(TEXTS[lang]['send_file'])
    await callback.answer("✅")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

async def main():
    init_db()
    logger.info("🚀 Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
