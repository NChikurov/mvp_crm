"""
AI Context Parser - Интеллектуальный парсер каналов на основе контекстного анализа
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from telegram import Update, User
from telegram.ext import ContextTypes

from database.operations import create_lead, update_channel_stats
from database.models import Lead
from ai.claude_client import get_claude_client

logger = logging.getLogger(__name__)

@dataclass
class UserContext:
    """Контекст пользователя для анализа"""
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    messages: List[Dict[str, Any]]
    first_seen: datetime
    last_activity: datetime
    channel_info: Dict[str, Any]

@dataclass
class AIAnalysisResult:
    """Результат AI анализа"""
    is_lead: bool
    confidence_score: int  # 0-100
    lead_quality: str  # "hot", "warm", "cold", "not_lead"
    interests: List[str]
    buying_signals: List[str]
    urgency_level: str  # "immediate", "short_term", "long_term", "none"
    recommended_action: str
    key_insights: List[str]
    estimated_budget: Optional[str]
    timeline: Optional[str]
    pain_points: List[str]
    decision_stage: str  # "awareness", "consideration", "decision", "post_purchase"

class AIContextParser:
    """Интеллектуальный парсер на основе AI анализа контекста"""
    
    def __init__(self, config):
        self.config = config
        self.parsing_config = config.get('parsing', {})
        
        # Настройки
        self.enabled = self.parsing_config.get('enabled', True)
        self.channels = self._parse_channels()
        self.min_confidence_score = self.parsing_config.get('min_confidence_score', 70)
        self.context_window_hours = self.parsing_config.get('context_window_hours', 24)
        self.min_messages_for_analysis = self.parsing_config.get('min_messages_for_analysis', 1)
        self.max_context_messages = self.parsing_config.get('max_context_messages', 10)
        
        # Контекст пользователей (в памяти для быстрого доступа)
        self.user_contexts: Dict[int, UserContext] = {}
        
        # Кэш AI анализов (чтобы не анализировать одно и то же повторно)
        self.analysis_cache: Dict[str, AIAnalysisResult] = {}
        
        # Обработанные лиды (чтобы не создавать дубликаты)
        self.processed_leads: Dict[int, datetime] = {}
        
        logger.info(f"AI Context Parser инициализирован:")
        logger.info(f"  - Каналов: {len(self.channels)}")
        logger.info(f"  - Мин. уверенность: {self.min_confidence_score}%")
        logger.info(f"  - Окно контекста: {self.context_window_hours}ч")

    def _parse_channels(self) -> List[str]:
        """Парсинг каналов из конфигурации"""
        channels_raw = self.parsing_config.get('channels', [])
        if isinstance(channels_raw, list):
            return [str(ch) for ch in channels_raw]
        elif isinstance(channels_raw, (str, int)):
            return [str(channels_raw)]
        return []

    async def process_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главная функция обработки сообщения"""
        try:
            if not self.enabled:
                return
            
            chat_id = update.effective_chat.id
            user = update.effective_user
            message = update.message
            
            if not user or not message or not message.text:
                return
            
            # Проверяем, что канал отслеживается
            if not self.is_channel_monitored(chat_id, update.effective_chat.username):
                return
            
            logger.info(f"🔍 AI анализ сообщения от {user.first_name} (@{user.username})")
            
            # Обновляем контекст пользователя
            await self._update_user_context(user, message, update.effective_chat)
            
            # Получаем контекст для анализа
            user_context = self.user_contexts.get(user.id)
            if not user_context:
                return
            
            # Проверяем готовность к анализу
            if not self._should_analyze_user(user_context):
                logger.debug(f"Пользователь {user.id} не готов к анализу")
                return
            
            # Проверяем, не анализировали ли недавно
            if self._was_recently_analyzed(user.id):
                logger.debug(f"Пользователь {user.id} недавно анализировался")
                return
            
            # Запускаем AI анализ
            analysis = await self._analyze_user_context(user_context)
            
            if analysis and analysis.is_lead and analysis.confidence_score >= self.min_confidence_score:
                # Создаем лид
                await self._create_lead_from_analysis(user_context, analysis, context)
                
                # Запоминаем, что уже обработали
                self.processed_leads[user.id] = datetime.now()
                
            # Обновляем статистику канала
            await self._update_channel_stats(str(chat_id), message.message_id, 
                                           analysis.is_lead if analysis else False)
            
        except Exception as e:
            logger.error(f"Ошибка в AI Context Parser: {e}")
            import traceback
            traceback.print_exc()

    async def _update_user_context(self, user: User, message, chat):
        """Обновление контекста пользователя"""
        try:
            user_id = user.id
            current_time = datetime.now()
            
            # Создаем или обновляем контекст
            if user_id not in self.user_contexts:
                self.user_contexts[user_id] = UserContext(
                    user_id=user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    messages=[],
                    first_seen=current_time,
                    last_activity=current_time,
                    channel_info={
                        'id': chat.id,
                        'title': chat.title,
                        'username': chat.username,
                        'type': chat.type
                    }
                )
            
            user_context = self.user_contexts[user_id]
            
            # Добавляем новое сообщение
            message_data = {
                'text': message.text,
                'date': message.date,
                'message_id': message.message_id,
                'timestamp': current_time.isoformat()
            }
            
            user_context.messages.append(message_data)
            user_context.last_activity = current_time
            
            # Обновляем профиль пользователя (может измениться имя или username)
            user_context.username = user.username
            user_context.first_name = user.first_name
            user_context.last_name = user.last_name
            
            # Ограничиваем количество сообщений в контексте
            if len(user_context.messages) > self.max_context_messages:
                user_context.messages = user_context.messages[-self.max_context_messages:]
            
            # Очищаем старые контексты
            self._cleanup_old_contexts()
            
        except Exception as e:
            logger.error(f"Ошибка обновления контекста пользователя {user.id}: {e}")

    def _should_analyze_user(self, user_context: UserContext) -> bool:
        """Определяет, готов ли пользователь к анализу"""
        # Минимальное количество сообщений
        if len(user_context.messages) < self.min_messages_for_analysis:
            return False
        
        # Проверяем свежесть активности
        time_since_last = datetime.now() - user_context.last_activity
        if time_since_last > timedelta(minutes=30):  # 30 минут тишины = можно анализировать
            return True
        
        # Или если набралось достаточно сообщений
        if len(user_context.messages) >= 3:
            return True
        
        return False

    def _was_recently_analyzed(self, user_id: int) -> bool:
        """Проверяет, не анализировался ли пользователь недавно"""
        if user_id in self.processed_leads:
            last_analysis = self.processed_leads[user_id]
            if datetime.now() - last_analysis < timedelta(hours=self.context_window_hours):
                return True
        return False

    async def _analyze_user_context(self, user_context: UserContext) -> Optional[AIAnalysisResult]:
        """AI анализ контекста пользователя"""
        try:
            claude_client = get_claude_client()
            if not claude_client or not claude_client.client:
                logger.warning("Claude API недоступен")
                return None
            
            # Создаем ключ для кэша
            messages_text = " | ".join([msg['text'] for msg in user_context.messages[-5:]])
            cache_key = f"{user_context.user_id}:{hash(messages_text)}"
            
            # Проверяем кэш
            if cache_key in self.analysis_cache:
                logger.debug(f"Используем кэшированный анализ для {user_context.user_id}")
                return self.analysis_cache[cache_key]
            
            # Подготавливаем данные для анализа
            context_data = self._prepare_context_for_ai(user_context)
            
            # Формируем промпт для Claude
            analysis_prompt = self._create_analysis_prompt(context_data)
            
            logger.info(f"🤖 Запускаем AI анализ для пользователя {user_context.user_id}")
            
            # Отправляем запрос в Claude с таймаутом
            try:
                response = await asyncio.wait_for(
                    claude_client.client.messages.create(
                        model=claude_client.model,
                        max_tokens=2000,
                        messages=[{"role": "user", "content": analysis_prompt}],
                        temperature=0.1  # Низкая температура для более предсказуемых результатов
                    ),
                    timeout=15.0
                )
                
                # Парсим ответ
                analysis_result = self._parse_ai_response(response.content[0].text)
                
                # Кэшируем результат
                self.analysis_cache[cache_key] = analysis_result
                
                # Ограничиваем размер кэша
                if len(self.analysis_cache) > 1000:
                    # Удаляем половину старых записей
                    old_keys = list(self.analysis_cache.keys())[:500]
                    for key in old_keys:
                        del self.analysis_cache[key]
                
                logger.info(f"✅ AI анализ завершен: лид={analysis_result.is_lead}, "
                          f"уверенность={analysis_result.confidence_score}%")
                
                return analysis_result
                
            except asyncio.TimeoutError:
                logger.warning("AI анализ превысил таймаут")
                return None
            
        except Exception as e:
            logger.error(f"Ошибка AI анализа: {e}")
            return None

    def _prepare_context_for_ai(self, user_context: UserContext) -> Dict[str, Any]:
        """Подготовка контекста для AI анализа"""
        return {
            'user': {
                'id': user_context.user_id,
                'username': user_context.username,
                'first_name': user_context.first_name,
                'last_name': user_context.last_name,
                'first_seen': user_context.first_seen.isoformat(),
                'last_activity': user_context.last_activity.isoformat()
            },
            'messages': user_context.messages,
            'channel': user_context.channel_info,
            'messages_count': len(user_context.messages),
            'activity_span_hours': (user_context.last_activity - user_context.first_seen).total_seconds() / 3600
        }

    def _create_analysis_prompt(self, context_data: Dict[str, Any]) -> str:
        """Создание промпта для AI анализа"""
        
        messages_text = "\n".join([
            f"[{msg['date']}] {msg['text']}" 
            for msg in context_data['messages']
        ])
        
        return f"""Ты - эксперт по анализу потенциальных клиентов в сфере IT-услуг, CRM систем, автоматизации бизнеса и разработки Telegram ботов.

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
- Имя: {context_data['user']['first_name']} (@{context_data['user']['username']})
- Канал: {context_data['channel']['title']} ({context_data['channel']['type']})
- Количество сообщений: {context_data['messages_count']}
- Период активности: {context_data['activity_span_hours']:.1f} часов

СООБЩЕНИЯ ПОЛЬЗОВАТЕЛЯ:
{messages_text}

ЗАДАЧА:
Проанализируй контекст и определи, является ли этот пользователь потенциальным клиентом для услуг:
- CRM систем и автоматизации бизнеса
- Разработки Telegram ботов
- IT-консалтинга и внедрения
- Интеграций и API разработки

ВЕРНИ РЕЗУЛЬТАТ СТРОГО В JSON ФОРМАТЕ:
{{
    "is_lead": boolean,
    "confidence_score": number (0-100),
    "lead_quality": "hot|warm|cold|not_lead",
    "interests": ["список интересов"],
    "buying_signals": ["сигналы покупательского намерения"],
    "urgency_level": "immediate|short_term|long_term|none",
    "recommended_action": "рекомендуемое действие",
    "key_insights": ["ключевые инсайты"],
    "estimated_budget": "примерный бюджет или null",
    "timeline": "временные рамки или null",
    "pain_points": ["проблемы клиента"],
    "decision_stage": "awareness|consideration|decision|post_purchase"
}}

КРИТЕРИИ ОЦЕНКИ:
- is_lead: true если есть явные признаки интереса к нашим услугам
- confidence_score: 90-100 = очевидный клиент, 70-89 = вероятный, 50-69 = возможный, <50 = маловероятный
- lead_quality: hot = готов покупать, warm = изучает рынок, cold = только начинает поиск
- urgency_level: насколько срочно нужно решение
- buying_signals: что указывает на готовность покупать
- pain_points: какие проблемы у клиента

ВАЖНО:
- Анализируй ВЕСЬ контекст, не отдельные сообщения
- Ищи скрытые потребности и подтекст
- Обращай внимание на бизнес-контекст
- Высокий confidence_score только при явных сигналах
- Будь объективным, не завышай оценки"""

    def _parse_ai_response(self, response_text: str) -> AIAnalysisResult:
        """Парсинг ответа от AI"""
        try:
            # Ищем JSON в ответе
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                raise ValueError("JSON не найден в ответе AI")
            
            json_str = json_match.group()
            data = json.loads(json_str)
            
            # Валидация и создание объекта результата
            return AIAnalysisResult(
                is_lead=bool(data.get('is_lead', False)),
                confidence_score=max(0, min(100, int(data.get('confidence_score', 0)))),
                lead_quality=data.get('lead_quality', 'not_lead'),
                interests=data.get('interests', []),
                buying_signals=data.get('buying_signals', []),
                urgency_level=data.get('urgency_level', 'none'),
                recommended_action=data.get('recommended_action', ''),
                key_insights=data.get('key_insights', []),
                estimated_budget=data.get('estimated_budget'),
                timeline=data.get('timeline'),
                pain_points=data.get('pain_points', []),
                decision_stage=data.get('decision_stage', 'awareness')
            )
            
        except Exception as e:
            logger.error(f"Ошибка парсинга AI ответа: {e}")
            logger.debug(f"Ответ AI: {response_text}")
            
            # Возвращаем базовый результат
            return AIAnalysisResult(
                is_lead=False,
                confidence_score=0,
                lead_quality='not_lead',
                interests=[],
                buying_signals=[],
                urgency_level='none',
                recommended_action='Анализ не удался',
                key_insights=[],
                estimated_budget=None,
                timeline=None,
                pain_points=[],
                decision_stage='awareness'
            )

    async def _create_lead_from_analysis(self, user_context: UserContext, 
                                       analysis: AIAnalysisResult, 
                                       context: ContextTypes.DEFAULT_TYPE):
        """Создание лида на основе AI анализа"""
        try:
            # Объединяем все сообщения пользователя
            all_messages = " | ".join([msg['text'] for msg in user_context.messages])
            
            # Создаем объект лида
            lead = Lead(
                telegram_id=user_context.user_id,
                username=user_context.username,
                first_name=user_context.first_name,
                source_channel=user_context.channel_info['title'] or str(user_context.channel_info['id']),
                interest_score=analysis.confidence_score,
                message_text=all_messages,
                message_date=user_context.last_activity,
                
                # Дополнительные поля из AI анализа
                lead_quality=analysis.lead_quality,
                interests=json.dumps(analysis.interests, ensure_ascii=False),
                buying_signals=json.dumps(analysis.buying_signals, ensure_ascii=False),
                urgency_level=analysis.urgency_level,
                estimated_budget=analysis.estimated_budget,
                timeline=analysis.timeline,
                pain_points=json.dumps(analysis.pain_points, ensure_ascii=False),
                decision_stage=analysis.decision_stage
            )
            
            # Сохраняем в базу
            await create_lead(lead)
            
            logger.info(f"🎯 AI ЛИД СОЗДАН: {user_context.first_name} (@{user_context.username})")
            logger.info(f"   Качество: {analysis.lead_quality}")
            logger.info(f"   Уверенность: {analysis.confidence_score}%")
            logger.info(f"   Интересы: {', '.join(analysis.interests)}")
            
            # Отправляем уведомление админам
            await self._notify_admins_about_ai_lead(context, user_context, analysis)
            
        except Exception as e:
            logger.error(f"Ошибка создания лида: {e}")

    async def _notify_admins_about_ai_lead(self, context: ContextTypes.DEFAULT_TYPE, 
                                         user_context: UserContext, 
                                         analysis: AIAnalysisResult):
        """Уведомление админов о новом AI лиде"""
        try:
            admin_ids = self.config.get('bot', {}).get('admin_ids', [])
            if not admin_ids:
                return
            
            # Определяем приоритет и эмодзи
            priority_config = {
                'hot': {'emoji': '🔥🔥🔥', 'text': 'ГОРЯЧИЙ ЛИД', 'color': '🟥'},
                'warm': {'emoji': '🔥🔥', 'text': 'ТЕПЛЫЙ ЛИД', 'color': '🟨'},
                'cold': {'emoji': '🔥', 'text': 'ХОЛОДНЫЙ ЛИД', 'color': '🟦'}
            }
            
            priority = priority_config.get(analysis.lead_quality, 
                                         {'emoji': '⭐', 'text': 'ПОТЕНЦИАЛЬНЫЙ ЛИД', 'color': '⬜'})
            
            # Форматируем списки
            interests_text = ', '.join(analysis.interests) if analysis.interests else 'не определены'
            pain_points_text = '\n• '.join(analysis.pain_points) if analysis.pain_points else 'не выявлены'
            buying_signals_text = '\n• '.join(analysis.buying_signals) if analysis.buying_signals else 'не обнаружены'
            
            # Формируем сообщение
            username_text = f"@{user_context.username}" if user_context.username else "без username"
            
            message = f"""{priority['emoji']} <b>{priority['text']}</b> {priority['color']}

🤖 <b>AI АНАЛИЗ ЗАВЕРШЕН</b>

👤 <b>Контакт:</b> {user_context.first_name} ({username_text})
🆔 <b>ID:</b> <code>{user_context.user_id}</code>
🎯 <b>Уверенность:</b> {analysis.confidence_score}% 
📊 <b>Качество:</b> {analysis.lead_quality.upper()}
📺 <b>Источник:</b> {user_context.channel_info['title']}
💬 <b>Сообщений:</b> {len(user_context.messages)}
⏰ <b>Период активности:</b> {(user_context.last_activity - user_context.first_seen).total_seconds() / 3600:.1f}ч

🎪 <b>Интересы:</b> {interests_text}

🚩 <b>Болевые точки:</b>
• {pain_points_text}

💰 <b>Покупательские сигналы:</b>
• {buying_signals_text}

⚡ <b>Срочность:</b> {analysis.urgency_level}
💵 <b>Бюджет:</b> {analysis.estimated_budget or 'не указан'}
📅 <b>Временные рамки:</b> {analysis.timeline or 'не указаны'}
🎭 <b>Стадия решения:</b> {analysis.decision_stage}

🎯 <b>Рекомендуемое действие:</b>
<i>{analysis.recommended_action}</i>

🔍 <b>Ключевые инсайты:</b>
{chr(10).join([f"• {insight}" for insight in analysis.key_insights])}

🔗 <b>Связаться:</b> <a href="tg://user?id={user_context.user_id}">Открыть диалог</a>"""

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
            
            logger.info(f"✅ AI уведомления отправлены {successful_notifications}/{len(admin_ids)} админам")
            
        except Exception as e:
            logger.error(f"Ошибка отправки AI уведомлений: {e}")

    def _cleanup_old_contexts(self):
        """Очистка старых контекстов пользователей"""
        try:
            current_time = datetime.now()
            cutoff_time = current_time - timedelta(hours=self.context_window_hours * 2)
            
            users_to_remove = []
            for user_id, context in self.user_contexts.items():
                if context.last_activity < cutoff_time:
                    users_to_remove.append(user_id)
            
            for user_id in users_to_remove:
                del self.user_contexts[user_id]
            
            if users_to_remove:
                logger.debug(f"Очищено {len(users_to_remove)} старых контекстов")
                
        except Exception as e:
            logger.error(f"Ошибка очистки контекстов: {e}")

    async def _update_channel_stats(self, channel_id: str, message_id: int, lead_found: bool):
        """Обновление статистики канала"""
        try:
            leads_count = 1 if lead_found else 0
            await update_channel_stats(channel_id, message_id, leads_count)
        except Exception as e:
            logger.error(f"Ошибка обновления статистики: {e}")

    def is_channel_monitored(self, chat_id: int, chat_username: str = None) -> bool:
        """Проверка мониторинга канала"""
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

    def get_status(self) -> Dict[str, Any]:
        """Получение статуса парсера"""
        return {
            'enabled': self.enabled,
            'channels_count': len(self.channels),
            'channels': self.channels,
            'min_confidence_score': self.min_confidence_score,
            'context_window_hours': self.context_window_hours,
            'active_users': len(self.user_contexts),
            'analysis_cache_size': len(self.analysis_cache),
            'processed_leads_count': len(self.processed_leads)
        }

    async def force_analyze_user(self, user_id: int) -> Optional[AIAnalysisResult]:
        """Принудительный анализ пользователя (для тестирования)"""
        user_context = self.user_contexts.get(user_id)
        if not user_context:
            return None
        
        return await self._analyze_user_context(user_context)

    def get_user_context(self, user_id: int) -> Optional[UserContext]:
        """Получение контекста пользователя"""
        return self.user_contexts.get(user_id)
