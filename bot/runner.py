from __future__ import annotations

import os
import json
import requests
import tempfile
from typing import List
from pathlib import Path

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

from utils.logger import get_logger
from config.settings import get_settings
from database.db import init_db, get_session
from database.models import ParsedListing


logger = get_logger("bot")


def get_first_image_url(obj: ParsedListing) -> str | None:
    """Получает URL первой фотографии из JSON списка изображений"""
    if not obj.images_json:
        return None
    try:
        images = json.loads(obj.images_json)
        if images and len(images) > 0:
            return images[0]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def send_journal_message(bot: telebot.TeleBot, chat_id: int, items: List[ParsedListing], 
                        offset: int, limit: int, total: int, message_id: int = None):
    """Отправляет сообщения журнала с фотографиями"""
    if not items:
        text = "Журнал пуст"
        if message_id:
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
        else:
            bot.send_message(chat_id, text)
        return
    
    # Сначала отправляем заголовок журнала с пагинацией
    header_text = f"<b>📋 Журнал парсинга (стр. {offset//limit + 1} из {(total-1)//limit + 1})</b>\n"
    header_text += f"📄 Всего записей: {total}\n"
    header_text += f"📄 Страница {offset//limit + 1} из {(total-1)//limit + 1} | Всего записей: {total}"
    
    if message_id:
        # Редактируем существующее сообщение заголовка
        try:
            bot.edit_message_text(
                header_text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=build_pagination_markup(offset, limit, total),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.warning(f"Failed to edit header message: {e}")
            # Если не удалось отредактировать, отправляем новое сообщение
            bot.send_message(
                chat_id, 
                header_text,
                reply_markup=build_pagination_markup(offset, limit, total),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
    else:
        # Отправляем новое сообщение заголовка
        bot.send_message(
            chat_id, 
            header_text,
            reply_markup=build_pagination_markup(offset, limit, total),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    
    # Теперь отправляем каждое объявление отдельным сообщением
    for i, item in enumerate(items):
        item_text = format_listing_text(item)
        # Добавляем номер объявления
        full_text = f"<b>{i+1}.</b> {item_text}"
        
        # Получаем URL первой фотографии
        image_url = get_first_image_url(item)
        
        if image_url:
            logger.info(f"Attempting to send photo for item {item.id}: {image_url[:100]}...")
            
            # Сначала пробуем отправить по прямой ссылке
            try:
                bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=full_text,
                    parse_mode="HTML"
                )
                logger.info(f"Successfully sent photo via URL for item {item.id}")
            except Exception as e:
                logger.warning(f"Failed to send photo via URL for item {item.id}: {e}")
                
                # Пробуем загрузить и отправить файлом
                logger.info(f"Trying to download and send photo for item {item.id}")
                if send_photo_with_download(bot, chat_id, image_url, full_text):
                    logger.info(f"Successfully sent photo via download for item {item.id}")
                else:
                    # Если и это не получилось, отправляем текстовое сообщение
                    logger.warning(f"Failed to send photo via download for item {item.id}")
                    try:
                        fallback_text = f"{full_text}\n\n📸 Фото недоступно"
                        bot.send_message(
                            chat_id=chat_id,
                            text=fallback_text,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                        logger.info(f"Sent fallback text message for item {item.id}")
                    except Exception as e2:
                        logger.error(f"Failed to send fallback message for item {item.id}: {e2}")
        else:
            # Если нет фотографии, отправляем только текст
            logger.info(f"No image found for item {item.id}, sending text only")
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=full_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info(f"Successfully sent text message for item {item.id}")
            except Exception as e:
                logger.error(f"Failed to send text message for item {item.id}: {e}")


def format_listing_text(obj: ParsedListing) -> str:
    parts = []
    if obj.title:
        parts.append(f"<b>{obj.title[:100]}</b>")
    if obj.price_raw or obj.price:
        parts.append(f"💰 {obj.price_raw or obj.price}")
    if obj.bail_raw or obj.bail:
        parts.append(f"🏠 Залог: {obj.bail_raw or obj.bail}")
    if obj.commission_raw or obj.tax:
        parts.append(f"💳 Комиссия: {obj.commission_raw or obj.tax}")
    if obj.services_raw or obj.services:
        parts.append(f"🔧 Услуги: {obj.services_raw or obj.services}")
    if obj.address:
        parts.append(f"📍 {obj.address[:80]}")
    if obj.url:
        parts.append(f"🔗 <a href='{obj.url}'>Ссылка</a>")
    return "\n".join(parts)


def build_main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("▶ Запустить парсер"))
    kb.add(KeyboardButton("🗂 Журнал"))
    kb.add(KeyboardButton("⚙ Настройки"))
    return kb


def build_pagination_markup(offset: int, limit: int, total: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    prev_off = max(0, offset - limit)
    next_off = offset + limit if (offset + limit) < total else offset
    buttons = []
    buttons.append(InlineKeyboardButton(text="⏮", callback_data=f"log:{0}:{limit}"))
    buttons.append(InlineKeyboardButton(text="◀", callback_data=f"log:{prev_off}:{limit}"))
    buttons.append(InlineKeyboardButton(text="▶", callback_data=f"log:{next_off}:{limit}"))
    last_page_off = limit * ((total - 1) // limit)
    buttons.append(InlineKeyboardButton(text="⏭", callback_data=f"log:{last_page_off}:{limit}"))
    kb.row(*buttons)
    return kb


def build_settings_markup() -> InlineKeyboardMarkup:
    """Создает меню настроек с inline кнопками"""
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="🌐 Изменить URL парсинга", callback_data="settings:change_url"))
    kb.add(InlineKeyboardButton(text="🗑 Очистить базу данных", callback_data="settings:clear_db"))
    kb.add(InlineKeyboardButton(text="📊 Статистика БД", callback_data="settings:db_stats"))
    kb.add(InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back"))
    return kb


def build_clear_db_confirmation_markup() -> InlineKeyboardMarkup:
    """Создает меню подтверждения очистки базы данных"""
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="✅ Да, очистить", callback_data="confirm:clear_db:yes"))
    kb.add(InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:clear_db:no"))
    return kb


def fetch_recent(offset: int, limit: int) -> (List[ParsedListing], int):
    with get_session() as session:
        # Сначала получаем объявления с изображениями, затем без
        # Объявления с изображениями (не пустой images_json)
        items_with_images = session.query(ParsedListing).filter(
            ParsedListing.images_json.isnot(None),
            ParsedListing.images_json != '',
            ParsedListing.images_json != '[]'
        ).order_by(ParsedListing.created_at.desc()).all()
        
        # Объявления без изображений
        items_without_images = session.query(ParsedListing).filter(
            ParsedListing.images_json.is_(None) | 
            (ParsedListing.images_json == '') |
            (ParsedListing.images_json == '[]')
        ).order_by(ParsedListing.created_at.desc()).all()
        
        # Объединяем: сначала с изображениями, потом без
        all_items = items_with_images + items_without_images
        total = len(all_items)
        
        # Применяем пагинацию
        items = all_items[offset:offset + limit]
        
    return items, total


def get_db_stats() -> str:
    """Получает статистику базы данных"""
    with get_session() as session:
        total_count = session.query(ParsedListing).count()
        if total_count == 0:
            return "📊 База данных пуста"
        
        # Получаем самую старую и новую запись
        oldest = session.query(ParsedListing).order_by(ParsedListing.created_at.asc()).first()
        newest = session.query(ParsedListing).order_by(ParsedListing.created_at.desc()).first()
        
        stats = f"📊 <b>Статистика базы данных:</b>\n\n"
        stats += f"📄 Всего записей: <b>{total_count}</b>\n"
        
        if oldest and newest:
            stats += f"📅 Первая запись: {oldest.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            stats += f"📅 Последняя запись: {newest.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        
        return stats


def clear_database() -> bool:
    """Очищает всю базу данных"""
    try:
        with get_session() as session:
            # Удаляем все записи
            deleted_count = session.query(ParsedListing).delete()
            session.commit()
            logger.info(f"Database cleared: {deleted_count} records deleted")
            return True
    except Exception as e:
        logger.error(f"Failed to clear database: {e}")
        return False


def validate_url(url: str) -> bool:
    """Проверяет валидность URL"""
    import re
    # Простая проверка URL
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(url_pattern.match(url))


def save_target_url(url: str) -> bool:
    """Сохраняет новый URL в .env файл"""
    try:
        env_path = Path(".env")
        if not env_path.exists():
            # Создаем новый .env файл
            env_path.write_text(f"TARGET_URL={url}\n", encoding="utf-8")
        else:
            # Читаем существующий файл
            lines = env_path.read_text(encoding="utf-8").splitlines()
            
            # Ищем строку с TARGET_URL
            updated = False
            for i, line in enumerate(lines):
                if line.strip().upper().startswith("TARGET_URL"):
                    lines[i] = f"TARGET_URL={url}"
                    updated = True
                    break
            
            # Если не нашли, добавляем в конец
            if not updated:
                lines.append(f"TARGET_URL={url}")
            
            # Записываем обратно
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        
        # Обновляем переменную окружения
        os.environ["TARGET_URL"] = url
        logger.info(f"Target URL updated to: {url}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save target URL: {e}")
        return False


def get_current_target_url() -> str:
    """Получает текущий URL из настроек"""
    s = get_settings()
    return s.target_url or "Не установлен"


def send_photo_with_download(bot: telebot.TeleBot, chat_id: int, image_url: str, caption: str) -> bool:
    """Отправляет фотографию, предварительно загрузив её"""
    try:
        # Загружаем изображение
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(image_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            temp_file.write(response.content)
            temp_file_path = temp_file.name
        
        try:
            # Отправляем файл
            with open(temp_file_path, 'rb') as photo_file:
                bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption,
                    parse_mode="HTML"
                )
            return True
        finally:
            # Удаляем временный файл
            try:
                os.unlink(temp_file_path)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Failed to send photo with download: {e}")
        return False


def handle_url_input(message: Message):
    """Обрабатывает введенный пользователем URL"""
    try:
        new_url = message.text.strip()
        
        # Проверяем валидность URL
        if not validate_url(new_url):
            bot.reply_to(
                message, 
                "❌ <b>Неверный формат URL!</b>\n\n"
                "URL должен начинаться с http:// или https://\n"
                "Попробуйте еще раз или вернитесь в настройки.",
                parse_mode="HTML"
            )
            return
        
        # Сохраняем URL
        if save_target_url(new_url):
            success_text = (
                f"✅ <b>URL успешно обновлен!</b>\n\n"
                f"Новый URL: <code>{new_url}</code>\n\n"
                f"Теперь парсер будет использовать этот URL для поиска объявлений."
            )
            bot.reply_to(message, success_text, parse_mode="HTML")
        else:
            bot.reply_to(
                message, 
                "❌ <b>Ошибка при сохранении URL!</b>\n\n"
                "Попробуйте позже или обратитесь к администратору.",
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Error handling URL input: {e}")
        bot.reply_to(
            message, 
            "❌ <b>Произошла ошибка!</b>\n\n"
            "Попробуйте позже или обратитесь к администратору.",
            parse_mode="HTML"
        )


def _load_token() -> str | None:
    # 1) try env
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    # 2) try python-dotenv
    if not token:
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv()
            token = os.getenv("TELEGRAM_BOT_TOKEN")
        except Exception:
            pass
    # 3) manual .env parse (robust to BOM/extra spaces/quotes/accidental '=' prefix)
    if not token:
        env_path = os.path.join(os.getcwd(), ".env")
        try:
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for raw in f:
                        line = raw.strip().lstrip("\ufeff")
                        if not line or line.startswith("#"):
                            continue
                        if line.upper().startswith("TELEGRAM_BOT_TOKEN"):
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                val = parts[1].strip().strip('"').strip("'")
                                # Fix common mistake like '==token' or '=token'
                                while val.startswith("="):
                                    val = val[1:]
                                token = val
                            break
        except Exception:
            pass
    return token


def _is_valid_token(tok: str | None) -> bool:
    if not tok or ":" not in tok:
        return False
    left = tok.split(":", 1)[0]
    try:
        int(left)
        return True
    except Exception:
        return False


def run_bot() -> None:
    settings = get_settings()
    token = _load_token()
    if not _is_valid_token(token):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing or malformed. Put in .env as TELEGRAM_BOT_TOKEN=123456789:ABCdef..."
        )

    init_db()

    bot = telebot.TeleBot(token, parse_mode="HTML", skip_pending=True)
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    try:
        bot.set_my_commands([
            telebot.types.BotCommand("start", "Показать меню"),
            telebot.types.BotCommand("help", "Помощь"),
        ])
    except Exception:
        pass

    @bot.message_handler(commands=["start", "help"])
    def on_start(message: Message):
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=build_main_menu())

    @bot.message_handler(func=lambda m: m.text == "▶ Запустить парсер")
    def on_run_parser(message: Message):
        from src.main import main as cli_main
        try:
            # Run single pass with current settings (equivalent to run-once)
            os.environ.setdefault("PYTHONUNBUFFERED", "1")
            # We call the CLI main with default args; it will pick TARGET_URL from settings
            cli_main()
            bot.reply_to(message, "Парсинг выполнен. Проверьте журнал.")
        except SystemExit:
            # argparse may call sys.exit; ignore
            bot.reply_to(message, "Парсинг завершён.")
        except Exception as exc:
            logger.exception("Parser run failed: %s", exc)
            bot.reply_to(message, f"Ошибка запуска парсера: {exc}")

    @bot.message_handler(func=lambda m: m.text == "🗂 Журнал")
    def on_journal(message: Message):
        offset, limit = 0, 3  # Уменьшил количество элементов на странице
        items, total = fetch_recent(offset, limit)
        send_journal_message(bot, message.chat.id, items, offset, limit, total)

    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("log:"))
    def on_log_pagination(call: CallbackQuery):
        try:
            _, off_str, lim_str = call.data.split(":")
            offset = max(0, int(off_str))
            limit = max(1, min(10, int(lim_str)))
        except Exception:
            offset, limit = 0, 3
        items, total = fetch_recent(offset, limit)
        
        # Сначала пытаемся отредактировать основное сообщение
        try:
            send_journal_message(bot, call.message.chat.id, items, offset, limit, total, call.message.message_id)
        except Exception as e:
            logger.warning(f"Failed to edit message, sending new one: {e}")
            # Если не удалось отредактировать, отправляем новое сообщение
            send_journal_message(bot, call.message.chat.id, items, offset, limit, total)
        
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("settings:"))
    def on_settings_callback(call: CallbackQuery):
        try:
            action = call.data.split(":", 1)[1]
            
            if action == "change_url":
                # Показываем форму для ввода нового URL
                current_url = get_current_target_url()
                url_text = (
                    f"🌐 <b>Изменение URL для парсинга</b>\n\n"
                    f"Текущий URL: <code>{current_url}</code>\n\n"
                    f"Отправьте новый URL в следующем сообщении.\n"
                    f"URL должен начинаться с http:// или https://\n\n"
                    f"<i>Пример: https://www.avito.ru/moskva/kvartiry/sdam/na_dlitelnyy_srok</i>"
                )
                bot.edit_message_text(
                    url_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML"
                )
                # Сохраняем состояние ожидания URL для этого пользователя
                bot.register_next_step_handler(call.message, handle_url_input)
                
            elif action == "clear_db":
                # Показываем подтверждение очистки
                warning_text = (
                    "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
                    "Вы собираетесь <b>полностью очистить базу данных</b>.\n"
                    "Это действие <b>необратимо</b> и удалит все сохраненные объявления.\n\n"
                    "Вы уверены, что хотите продолжить?"
                )
                bot.edit_message_text(
                    warning_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=build_clear_db_confirmation_markup()
                )
                
            elif action == "db_stats":
                # Показываем статистику БД
                stats_text = get_db_stats()
                bot.edit_message_text(
                    stats_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=build_settings_markup()
                )
                
            elif action == "back":
                # Возвращаемся к основному меню
                s = get_settings()
                info = [
                    f"<b>⚙ Настройки системы:</b>\n",
                    f"🌐 TARGET_URL: {s.target_url}",
                    f"🗄️ DB: {s.database_url}",
                    f"🔗 PROXY_URL: {os.getenv('PROXY_URL') or s.proxy_url or '-'}",
                ]
                text = "\n".join(info)
                bot.edit_message_text(
                    text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=build_settings_markup()
                )
                
        except Exception as e:
            logger.error(f"Error in settings callback: {e}")
            bot.answer_callback_query(call.id, "Произошла ошибка", show_alert=True)
        else:
            bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("confirm:clear_db:"))
    def on_clear_db_confirmation(call: CallbackQuery):
        try:
            action = call.data.split(":")[-1]
            
            if action == "yes":
                # Очищаем базу данных
                success = clear_database()
                if success:
                    result_text = "✅ <b>База данных успешно очищена!</b>\n\nВсе записи удалены."
                else:
                    result_text = "❌ <b>Ошибка при очистке базы данных!</b>\n\nПопробуйте позже или обратитесь к администратору."
                
                bot.edit_message_text(
                    result_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=build_settings_markup()
                )
                
            elif action == "no":
                # Отменяем операцию
                s = get_settings()
                info = [
                    f"<b>⚙ Настройки системы:</b>\n",
                    f"🌐 TARGET_URL: {s.target_url}",
                    f"🗄️ DB: {s.database_url}",
                    f"🔗 PROXY_URL: {os.getenv('PROXY_URL') or s.proxy_url or '-'}",
                ]
                text = "\n".join(info)
                bot.edit_message_text(
                    text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=build_settings_markup()
                )
                
        except Exception as e:
            logger.error(f"Error in clear_db confirmation: {e}")
            bot.answer_callback_query(call.id, "Произошла ошибка", show_alert=True)
        else:
            bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: m.text == "⚙ Настройки")
    def on_settings(message: Message):
        s = get_settings()
        info = [
            f"<b>⚙ Настройки системы:</b>\n",
            f"🌐 TARGET_URL: {s.target_url}",
            f"🗄️ DB: {s.database_url}",
            f"🔗 PROXY_URL: {os.getenv('PROXY_URL') or s.proxy_url or '-'}",
        ]
        text = "\n".join(info)
        bot.send_message(
            message.chat.id, 
            text, 
            parse_mode="HTML",
            reply_markup=build_settings_markup()
        )

    @bot.message_handler(func=lambda m: True)
    def on_fallback(message: Message):
        # Any other message shows the main menu
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=build_main_menu())

    logger.info("Bot started")
    bot.infinity_polling(allowed_updates=["message", "callback_query"]) 


if __name__ == "__main__":
    run_bot()


