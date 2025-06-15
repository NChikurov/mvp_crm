"""
Парсер Telegram каналов для поиска потенциальных клиентов (исправленная версия)
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from telegram import Update
from telegram.ext import ContextTypes

from database.operations import (
    create_or_update_channel, get_active_channels, create_lead,
    update_channel_stats
)
from database.models import ParsedChannel, Lead
from ai.claude_client import get_claude_client

logger = logging.getLogger(__name__)

class ChannelParser:
    """Парсер каналов для поиска лидов"""
    
    def __init__(self, config):
        self.config = config
        self.parsing_config = config.get('parsing', {})
        
        # Настройки парсинга
        self.enabled = self.parsing_config.get('enabled', True)
        
        # Безопасное получение каналов
        channels_raw = self.parsing_config.get('channels', [])
        if isinstance(channels_raw, list):
            self.channels = [str(ch) for ch in channels_raw]
        elif isinstance(channels_raw, (str, int)):
            self.channels = [str(channels_raw)]
        else:
            self.channels = []
            logger.warning(f"Некорректный формат каналов в конфигурации: {channels_raw}")
        
        self.min_interest_score = self.parsing_config.get('min_interest_score', 60)
        
        # Дедупликация сообщений (чтобы не обрабатывать одно сообщение дважды)
        self.processed_messages = set()
        
        # Кэш для недавно найденных лидов (чтобы не дублировать)
        self.recent_leads_cache = {}
        
        logger.info(f"Парсер инициализирован: {len(self.channels)} каналов, мин. скор: {self.min_interest_score}")

    async def process_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка сообщения из отслеживаемого канала/группы"""
        try:
            if not self.enabled:
                return
            
            chat_id = update.effective_chat.id
            message_id = update.message.message_id
            user = update.effective_user
            message_text = update.message.text
            
            # Проверяем дедупликацию
            message_key = f"{chat_id}:{message_id}"
            if message_key in self.processed_messages:
                return
            
            self.processed_messages.add(message_key)
            
            # Ограничиваем размер кэша
            if len(self.processed_messages) > 10000:
                # Удаляем половину старых записей
                old_messages = list(self.processed_messages)[:5000]
                for msg in old_messages:
                    self.processed_messages.discard(msg)
            
            # Получаем информацию о канале
            chat = update.effective_chat
            channel_identifier = str(chat_id)
            
            # Пытаемся найти username канала
            if chat.username:
                channel_identifier = f"@{chat.username}"
            
            logger.info(f"📺 Анализируем сообщение из {channel_identifier}: '{message_text[:100]}...'")
            
            # Анализируем сообщение
            interest_score = await self._analyze_message(message_text, channel_identifier)
            
            logger.info(f"📊 Скор заинтересованности: {interest_score}/100")
            
            # Если скор высокий - сохраняем как лид
            if interest_score >= self.min_interest_score:
                # Проверяем, что такой лид еще не существует
                lead_key = f"{user.id}:{hash(message_text[:100])}"
                
                # ИСПРАВЛЕНО: Синхронная проверка существования лида
                if not await self._lead_exists_fixed(user.id, message_text) and lead_key not in self.recent_leads_cache:
                    lead = Lead(
                        telegram_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        source_channel=channel_identifier,
                        interest_score=interest_score,
                        message_text=message_text,
                        message_date=update.message.date
                    )
                    
                    await create_lead(lead)
                    
                    # Добавляем в кэш недавних лидов
                    self.recent_leads_cache[lead_key] = datetime.now()
                    
                    # Очищаем старые записи из кэша (старше часа)
                    self._cleanup_leads_cache()
                    
                    logger.info(f"🎯 НАЙДЕН ЛИД: {user.first_name} (@{user.username}) - score: {interest_score}")
                    logger.info(f"📱 Telegram ID: {user.id}")
                    logger.info(f"📝 Текст: {message_text[:200]}...")
                    
                    # Уведомляем админов о новом лиде
                    await self._notify_admins_about_lead(context, lead)
                else:
                    logger.debug(f"Лид уже существует для пользователя {user.id}")
            else:
                logger.debug(f"Скор {interest_score} ниже порога {self.min_interest_score}")
            
            # Обновляем статистику канала
            await self._update_channel_stats(channel_identifier, message_id, interest_score >= self.min_interest_score)
            
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения из канала: {e}")
            import traceback
            traceback.print_exc()

    def _cleanup_leads_cache(self):
        """Очистка старых записей из кэша лидов"""
        try:
            current_time = datetime.now()
            expired_keys = []
            
            for key, timestamp in self.recent_leads_cache.items():
                if current_time - timestamp > timedelta(hours=1):
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.recent_leads_cache[key]
                
        except Exception as e:
            logger.error(f"Ошибка очистки кэша лидов: {e}")

    async def _analyze_message(self, message_text: str, channel_identifier: str) -> int:
        """Анализ сообщения на предмет потенциального лида"""
        try:
            claude_client = get_claude_client()
            if claude_client and claude_client.client:
                # Используем Claude для анализа с таймаутом
                try:
                    score = await asyncio.wait_for(
                        claude_client.analyze_potential_lead(message_text, channel_identifier),
                        timeout=10.0
                    )
                    return score
                except asyncio.TimeoutError:
                    logger.warning("Claude API таймаут, используем простой анализ")
                    return self._simple_lead_analysis(message_text)
            else:
                # Простой анализ без AI
                return self._simple_lead_analysis(message_text)
        
        except Exception as e:
            logger.error(f"Ошибка анализа сообщения: {e}")
            return self._simple_lead_analysis(message_text)

    def _simple_lead_analysis(self, message_text: str) -> int:
        """Улучшенный простой анализ потенциального лида без AI"""
        if not message_text:
            return 0
        
        message_lower = message_text.lower()
        score = 0
        
        # CRM и автоматизация - высший приоритет
        crm_words = [
            'crm', 'црм', 'customer relationship', 'клиентская база',
            'управление клиентами', 'автоматизация продаж', 'система продаж',
            'продажная воронка', 'sales funnel', 'лидогенерация', 'lead generation'
        ]
        
        # Боты и телеграм автоматизация
        bot_words = [
            'telegram bot', 'телеграм бот', 'чат бот', 'chatbot', 'бот для продаж',
            'автоответчик', 'автоматические ответы', 'обработка заявок',
            'прием заявок', 'telegram api', 'webhook'
        ]
        
        # Бизнес проблемы
        business_problems = [
            'не успеваем обрабатывать', 'много заявок', 'теряем клиентов',
            'нужна система', 'ищу решение', 'как автоматизировать',
            'эффективность продаж', 'увеличить конверсию', 'больше продаж',
            'автоматический ответ', 'обработка сообщений', 'учет клиентов'
        ]
        
        # Покупательские намерения
        buying_intent = [
            'ищу', 'нужно', 'требуется', 'хочу заказать', 'планирую купить',
            'рассматриваю покупку', 'интересует стоимость', 'бюджет есть',
            'готов платить', 'нужна консультация', 'где заказать', 'кто делает',
            'готов купить', 'готов заказать'
        ]
        
        # Технические запросы
        tech_requests = [
            'api интеграция', 'интеграция с', 'подключить к', 'разработка под заказ',
            'кастомная разработка', 'индивидуальное решение', 'техническое задание',
            'настройка системы', 'внедрение crm'
        ]
        
        # Конкуренты и альтернативы
        competitors = [
            'bitrix24', 'amocrm', 'megaplan', 'pipedrive', 'salesforce',
            'не устраивает текущая система', 'ищу альтернативу', 'смена crm'
        ]
        
        # Подсчет баллов с весами
        for word in crm_words:
            if word in message_lower:
                score += 50
                break
        
        for word in bot_words:
            if word in message_lower:
                score += 45
                break
        
        for word in business_problems:
            if word in message_lower:
                score += 40
                break
        
        for word in buying_intent:
            if word in message_lower:
                score += 35
                break
        
        for word in tech_requests:
            if word in message_lower:
                score += 30
                break
        
        for word in competitors:
            if word in message_lower:
                score += 25
                break
        
        # Дополнительные баллы за вопросы
        question_words = ['как', 'что', 'где', 'кто может', 'кто делает', 'посоветуйте', '?']
        if any(word in message_lower for word in question_words):
            score += 15
        
        # Бонус за длинные осмысленные сообщения
        if len(message_text) > 50:
            score += 10
        if len(message_text) > 150:
            score += 5
        
        # Штрафы за нерелевантные сообщения
        irrelevant = [
            'продаю', 'куплю авто', 'недвижимость', 'знакомства', 'работа',
            'вакансия', 'резюме', 'спам', 'реклама', '+', 'цена за'
        ]
        for word in irrelevant:
            if word in message_lower:
                score -= 30
                break
        
        # Штраф за короткие сообщения
        if len(message_text) < 20:
            score -= 15
        
        # Штраф за сообщения только из эмодзи или символов
        if len(re.sub(r'[^\w\s]', '', message_text, flags=re.UNICODE)) < 10:
            score -= 20
        
        return max(0, min(100, score))

    async def _lead_exists_fixed(self, user_id: int, message_text: str) -> bool:
        """ИСПРАВЛЕННАЯ проверка существования лида"""
        try:
            import aiosqlite
            
            # Используем прямое подключение без создания новых потоков
            db_path = self.config.get('database', {}).get('path', 'data/bot.db')
            
            async with aiosqlite.connect(db_path) as db:
                # Проверяем по пользователю за последние 24 часа
                cursor = await db.execute("""
                    SELECT id FROM leads 
                    WHERE telegram_id = ? 
                    AND created_at >= datetime('now', '-24 hours')
                    LIMIT 1
                """, (user_id,))
                result = await cursor.fetchone()
                return result is not None
                
        except Exception as e:
            logger.error(f"Ошибка проверки существования лида: {e}")
            # В случае ошибки разрешаем создание лида
            return False

    async def _update_channel_stats(self, channel_identifier: str, message_id: int, lead_found: bool):
        """Обновление статистики канала"""
        try:
            leads_count = 1 if lead_found else 0
            await update_channel_stats(channel_identifier, message_id, leads_count)
        except Exception as e:
            logger.error(f"Ошибка обновления статистики канала: {e}")

    async def _notify_admins_about_lead(self, context: ContextTypes.DEFAULT_TYPE, lead: Lead):
        """Уведомление админов о новом лиде"""
        try:
            admin_ids = self.config.get('bot', {}).get('admin_ids', [])
            
            if not admin_ids:
                logger.warning("Нет настроенных админов для уведомлений")
                return
            
            # Определяем приоритет лида
            if lead.interest_score >= 90:
                priority_emoji = "🔥🔥🔥"
                priority_text = "СУПЕР ГОРЯЧИЙ"
                urgency = "СРОЧНО СВЯЗАТЬСЯ!"
            elif lead.interest_score >= 80:
                priority_emoji = "🔥🔥"
                priority_text = "ОЧЕНЬ ГОРЯЧИЙ"
                urgency = "Свяжитесь в течение часа!"
            elif lead.interest_score >= 70:
                priority_emoji = "🔥"
                priority_text = "ГОРЯЧИЙ"
                urgency = "Свяжитесь в ближайшее время!"
            else:
                priority_emoji = "⭐"
                priority_text = "Потенциальный"
                urgency = "Отправьте информационные материалы"
            
            # Формируем сообщение
            username_text = f"@{lead.username}" if lead.username else "без username"
            
            # Анализируем ключевые слова в сообщении для персонализации
            message_lower = lead.message_text.lower()
            interests = []
            if any(word in message_lower for word in ['crm', 'система']):
                interests.append("CRM системы")
            if any(word in message_lower for word in ['бот', 'автоматизация']):
                interests.append("автоматизация")
            if any(word in message_lower for word in ['продажи', 'заявки']):
                interests.append("продажи")
            
            interests_text = ", ".join(interests) if interests else "наши услуги"
            
            message = f"""{priority_emoji} <b>НОВЫЙ ЛИД - {priority_text}</b>

👤 <b>Контакт:</b> {lead.first_name} ({username_text})
🆔 <b>ID:</b> <code>{lead.telegram_id}</code>
⭐ <b>Скор:</b> {lead.interest_score}/100
📺 <b>Источник:</b> {lead.source_channel}
⏰ <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}
💡 <b>Интересы:</b> {interests_text}

💬 <b>Сообщение:</b>
<i>"{lead.message_text[:400]}{'...' if len(lead.message_text) > 400 else ''}"</i>

🎯 <b>Действие:</b> {urgency}

🔗 <b>Связаться:</b> <a href="tg://user?id={lead.telegram_id}">Открыть диалог</a>"""

            # Отправляем всем админам
            successful_notifications = 0
            for admin_id in admin_ids:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=message,
                        parse_mode='HTML',
                        disable_web_page_preview=True
                    )
                    successful_notifications += 1
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
            
            logger.info(f"✅ Уведомления отправлены {successful_notifications}/{len(admin_ids)} админам")
        
        except Exception as e:
            logger.error(f"Ошибка отправки уведомлений админам: {e}")

    async def initialize_channels(self):
        """Инициализация каналов в базе данных"""
        try:
            for channel_identifier in self.channels:
                channel = ParsedChannel(
                    channel_username=channel_identifier,
                    channel_title=f"Канал {channel_identifier}",
                    enabled=True
                )
                await create_or_update_channel(channel)
            
            logger.info(f"Инициализировано {len(self.channels)} каналов в БД")
        except Exception as e:
            logger.error(f"Ошибка инициализации каналов: {e}")

    def get_parsing_status(self) -> Dict[str, Any]:
        """Получение статуса парсинга"""
        return {
            'enabled': self.enabled,
            'channels_count': len(self.channels),
            'channels': self.channels,
            'min_score': self.min_interest_score,
            'processed_messages_count': len(self.processed_messages),
            'recent_leads_cache_size': len(self.recent_leads_cache)
        }

    def is_channel_monitored(self, chat_id: int, chat_username: str = None) -> bool:
        """Проверка, отслеживается ли канал/группа"""
        if not self.enabled:
            return False
        
        # Проверяем по ID
        if str(chat_id) in self.channels:
            return True
        
        # Проверяем по username
        if chat_username:
            username_variants = [f"@{chat_username}", chat_username]
            for variant in username_variants:
                if variant in self.channels:
                    return True
        
        return False

    async def add_channel(self, channel_identifier: str) -> bool:
        """Добавление нового канала для мониторинга"""
        try:
            if channel_identifier not in self.channels:
                self.channels.append(channel_identifier)
                
                # Добавляем в БД
                channel = ParsedChannel(
                    channel_username=channel_identifier,
                    channel_title=f"Канал {channel_identifier}",
                    enabled=True
                )
                await create_or_update_channel(channel)
                
                logger.info(f"Канал {channel_identifier} добавлен для мониторинга")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Ошибка добавления канала {channel_identifier}: {e}")
            return False

    async def remove_channel(self, channel_identifier: str) -> bool:
        """Удаление канала из мониторинга"""
        try:
            if channel_identifier in self.channels:
                self.channels.remove(channel_identifier)
                
                # Отключаем в БД
                import aiosqlite
                db_path = self.config.get('database', {}).get('path', 'data/bot.db')
                
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        "UPDATE parsed_channels SET enabled = FALSE WHERE channel_username = ?",
                        (channel_identifier,)
                    )
                    await db.commit()
                
                logger.info(f"Канал {channel_identifier} удален из мониторинга")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Ошибка удаления канала {channel_identifier}: {e}")
            return False