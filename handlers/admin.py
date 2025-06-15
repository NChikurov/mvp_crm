"""
Обработчики админских команд - ИСПРАВЛЕННАЯ ВЕРСИЯ
"""

import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database.operations import (
    get_users, get_leads, get_active_channels, 
    create_or_update_channel, get_bot_stats, get_setting, set_setting
)
from database.models import ParsedChannel, Broadcast

logger = logging.getLogger(__name__)

class AdminHandler:
    """Обработчик админских команд"""
    
    def __init__(self, config):
        self.config = config
        self.admin_ids = config.get('bot', {}).get('admin_ids', [])
        
        # Callback handler - ТОЛЬКО для админских callback
        self.callback_handler = CallbackQueryHandler(
            self.handle_admin_callback,
            pattern=r'^admin_'  # Только callback начинающиеся с admin_
        )

    def _is_admin(self, user_id: int) -> bool:
        """Проверка является ли пользователь админом"""
        return user_id in self.admin_ids

    async def _admin_required(self, update: Update) -> bool:
        """Декоратор для проверки прав админа"""
        user_id = update.effective_user.id
        if not self._is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора")
            return False
        return True

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главная админ панель"""
        if not await self._admin_required(update):
            return
        
        keyboard = [
            [
                InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
                InlineKeyboardButton("🎯 Лиды", callback_data="admin_leads")
            ],
            [
                InlineKeyboardButton("📺 Каналы", callback_data="admin_channels"),
                InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")
            ],
            [
                InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
                InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")
            ]
        ]
        
        await update.message.reply_text(
            "🔧 <b>Админ панель</b>\n\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )

    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать статистику"""
        if not await self._admin_required(update):
            return
        
        try:
            stats = await get_bot_stats()
            
            message = "📊 <b>Статистика бота</b>\n\n"
            
            message += "👥 <b>Пользователи:</b>\n"
            message += f"• Всего: {stats.get('total_users', 0)}\n"
            message += f"• Активные за 24ч: {stats.get('active_users_today', 0)}\n\n"
            
            message += "💬 <b>Сообщения:</b>\n"
            message += f"• Всего: {stats.get('total_messages', 0)}\n\n"
            
            message += "🎯 <b>Лиды:</b>\n"
            message += f"• Всего: {stats.get('total_leads', 0)}\n"
            message += f"• За 24 часа: {stats.get('leads_today', 0)}\n"
            message += f"• За неделю: {stats.get('leads_week', 0)}\n\n"
            
            # Конверсия
            if stats.get('total_users', 0) > 0:
                conversion = stats.get('total_leads', 0) / stats.get('total_users', 1) * 100
                message += f"📈 <b>Конверсия в лиды:</b> {conversion:.1f}%"
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            await update.message.reply_text("❌ Ошибка получения статистики")

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Рассылка сообщения всем пользователям"""
        if not await self._admin_required(update):
            return
        
        # Получаем текст для рассылки из аргументов команды
        if context.args:
            broadcast_text = " ".join(context.args)
        else:
            await update.message.reply_text(
                "📢 <b>Рассылка</b>\n\n"
                "Используйте: <code>/broadcast Текст сообщения</code>\n\n"
                "Пример: <code>/broadcast Новая акция! Скидка 20%</code>",
                parse_mode='HTML'
            )
            return
        
        try:
            # Получаем всех пользователей
            users = await get_users(limit=1000)
            
            if not users:
                await update.message.reply_text("❌ Нет пользователей для рассылки")
                return
            
            # Отправляем уведомление о начале рассылки
            await update.message.reply_text(
                f"📢 Начинаю рассылку для {len(users)} пользователей...\n"
                f"Текст: <i>{broadcast_text[:100]}...</i>",
                parse_mode='HTML'
            )
            
            # Отправляем сообщения
            sent_count = 0
            failed_count = 0
            
            for user in users:
                try:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=broadcast_text,
                        parse_mode='HTML'
                    )
                    sent_count += 1
                    
                    # Пауза чтобы не нарушить лимиты Telegram
                    if sent_count % 20 == 0:
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"Не удалось отправить сообщение пользователю {user.telegram_id}: {e}")
            
            # Отправляем отчет
            success_rate = (sent_count/(sent_count+failed_count)*100) if (sent_count+failed_count) > 0 else 0
            await update.message.reply_text(
                f"✅ <b>Рассылка завершена</b>\n\n"
                f"📤 Отправлено: {sent_count}\n"
                f"❌ Ошибок: {failed_count}\n"
                f"📊 Успешность: {success_rate:.1f}%",
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка рассылки: {e}")
            await update.message.reply_text("❌ Ошибка при рассылке")

    async def handle_admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback запросов админки"""
        query = update.callback_query
        
        if not self._is_admin(query.from_user.id):
            await query.answer("❌ У вас нет прав администратора")
            return
        
        data = query.data
        logger.info(f"🔧 Admin callback от {query.from_user.id}: {data}")
        
        try:
            await query.answer()
            
            if data == "admin_panel":
                await self._show_admin_panel(query)
            elif data == "admin_users":
                await self._show_users_callback(query)
            elif data == "admin_leads":
                await self._show_leads_callback(query)
            elif data == "admin_channels":
                await self._show_channels_callback(query)
            elif data == "admin_stats":
                await self._show_stats_callback(query)
            elif data == "admin_broadcast":
                await self._show_broadcast_info(query)
            elif data == "admin_settings":
                await self._show_settings_callback(query)
            else:
                logger.warning(f"Неизвестная админская команда: {data}")
                await query.edit_message_text("❌ Неизвестная команда")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки admin callback '{data}': {e}")
            import traceback
            traceback.print_exc()
            try:
                await query.edit_message_text("❌ Произошла ошибка. Попробуйте еще раз.")
            except:
                pass

    async def _show_admin_panel(self, query):
        """Показать админ панель"""
        keyboard = [
            [
                InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
                InlineKeyboardButton("🎯 Лиды", callback_data="admin_leads")
            ],
            [
                InlineKeyboardButton("📺 Каналы", callback_data="admin_channels"),
                InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")
            ],
            [
                InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
                InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")
            ]
        ]
        
        await query.edit_message_text(
            "🔧 <b>Админ панель</b>\n\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )

    async def _show_users_callback(self, query):
        """Показать пользователей через callback"""
        try:
            users = await get_users(limit=10)
            
            # Добавляем timestamp для избежания дублирования
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            message = f"👥 <b>Пользователи бота</b> (обновлено {timestamp})\n\n"
            
            if users:
                message += f"📋 <b>Последние пользователи ({len(users)}):</b>\n"
                for user in users[:5]:
                    username = f"@{user.username}" if user.username else "без username"
                    activity = user.last_activity.strftime("%d.%m %H:%M") if user.last_activity else "никогда"
                    message += f"• {user.first_name} ({username}) - активен: {activity}\n"
            else:
                message += "Пользователей пока нет."
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="admin_users")],
                [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка показа пользователей: {e}")
            await query.edit_message_text("❌ Ошибка получения данных о пользователях")

    async def _show_leads_callback(self, query):
        """Показать лиды через callback"""
        try:
            leads = await get_leads(limit=10)
            
            # Добавляем timestamp для избежания дублирования
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            message = f"🎯 <b>Потенциальные клиенты</b> (обновлено {timestamp})\n\n"
            
            if leads:
                # Фильтруем лиды за последние 24 часа
                recent_leads = [
                    lead for lead in leads 
                    if lead.created_at and (datetime.now() - lead.created_at).days == 0
                ]
                
                message += f"🔥 <b>За 24 часа найдено: {len(recent_leads)}</b>\n\n"
                
                for lead in leads[:3]:
                    username = f"@{lead.username}" if lead.username else "без username"
                    message += f"• {lead.first_name or 'Аноним'} ({username})\n"
                    message += f"  Скор: {lead.interest_score}/100\n"
                    if lead.source_channel:
                        message += f"  Из: {lead.source_channel.replace('@', '')}\n\n"
            else:
                message += "Лидов пока нет.\n\n"
                message += "💡 Проверьте настройки парсинга каналов."
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="admin_leads")],
                [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка показа лидов: {e}")
            await query.edit_message_text("❌ Ошибка получения данных о лидах")

    async def _show_channels_callback(self, query):
        """Показать каналы через callback"""
        try:
            channels = await get_active_channels()
            
            # Добавляем timestamp для избежания дублирования
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            message = f"📺 <b>Каналы для парсинга</b> (обновлено {timestamp})\n\n"
            
            if channels:
                for channel in channels[:5]:  # Показываем только первые 5
                    status = "✅" if channel.enabled else "❌"
                    message += f"{status} <code>{channel.channel_username}</code>\n"
                    message += f"   📄 {channel.total_messages} сообщений, 🎯 {channel.leads_found} лидов\n"
                
                if len(channels) > 5:
                    message += f"\n... и еще {len(channels) - 5} каналов"
            else:
                message += "Каналы не настроены."
            
            message += f"\n\n📊 <b>Статус парсинга:</b>\n"
            message += f"• {'✅ Активен' if self.config.get('parsing', {}).get('enabled') else '❌ Отключен'}\n"
            message += f"• Интервал: {self.config.get('parsing', {}).get('parse_interval', 3600)} сек"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="admin_channels")],
                [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка показа каналов: {e}")
            await query.edit_message_text("❌ Ошибка получения данных о каналах")

    async def _show_stats_callback(self, query):
        """Показать статистику через callback"""
        try:
            stats = await get_bot_stats()
            
            # Добавляем timestamp для избежания дублирования
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            message = f"📊 <b>Статистика</b> (обновлено {timestamp})\n\n"
            message += f"👥 Пользователей: {stats.get('total_users', 0)}\n"
            message += f"💬 Сообщений: {stats.get('total_messages', 0)}\n"
            message += f"🎯 Лидов: {stats.get('total_leads', 0)}\n"
            message += f"🔥 За сегодня: {stats.get('leads_today', 0)}\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats")],
                [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка показа статистики: {e}")
            await query.edit_message_text("❌ Ошибка получения статистики")

    async def _show_broadcast_info(self, query):
        """Показать информацию о рассылке"""
        message = "📢 <b>Рассылка сообщений</b>\n\n"
        message += "Для отправки рассылки используйте команду:\n"
        message += "<code>/broadcast Текст сообщения</code>\n\n"
        message += "<b>Примеры:</b>\n"
        message += "• <code>/broadcast Новая акция!</code>\n"
        message += "• <code>/broadcast Скидка 20% до конца недели</code>\n\n"
        message += "⚠️ Рассылка отправляется всем пользователям бота."
        
        keyboard = [
            [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )

    async def _show_settings_callback(self, query):
        """Показать настройки через callback"""
        message = "⚙️ <b>Настройки</b>\n\n"
        
        # Проверяем Claude API
        from ai.claude_client import get_claude_client
        claude_client = get_claude_client()
        if claude_client:
            stats = claude_client.get_usage_stats()
            message += f"🤖 Claude: {'✅' if stats['api_available'] else '⚠️ Простой режим'}\n"
        else:
            message += "🤖 Claude: ❌ Не инициализирован\n"
        
        message += f"👑 Админов: {len(self.admin_ids)}\n"
        message += f"📺 Парсинг: {'✅' if self.config.get('parsing', {}).get('enabled') else '❌'}\n"
        message += f"💬 Автоответы: {'✅' if self.config.get('features', {}).get('auto_response') else '❌'}\n"
        
        message += "\nНастройки в <code>.env</code> и <code>config.yaml</code>"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )