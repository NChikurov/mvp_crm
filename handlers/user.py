"""
Обработчики пользователей
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database.operations import (
    create_user, get_user_by_telegram_id, create_message,
    update_user_interest_score, increment_user_message_count,
    get_user_messages
)
from database.models import User, Message
from ai.claude_client import init_claude_client, get_claude_client

logger = logging.getLogger(__name__)

class UserHandler:
    """Обработчик пользовательских команд и сообщений"""
    
    def __init__(self, config):
        self.config = config
        self.messages_config = config.get('messages', {})
        self.features = config.get('features', {})
        
        # Инициализируем Claude клиента
        try:
            init_claude_client(config)
            logger.info("Claude клиент инициализирован в UserHandler")
        except Exception as e:
            logger.warning(f"Не удалось инициализировать Claude клиента: {e}")
        
        # Callback handler - ВАЖНО: только для пользовательских callback
        self.callback_handler = CallbackQueryHandler(
            self.handle_callback,
            pattern=r'^(main_menu|help|contact|about)$'  # Только пользовательские callback
        )
        
        logger.info("UserHandler инициализирован")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start"""
        try:
            logger.info(f"Команда /start от пользователя {update.effective_user.id} (@{update.effective_user.username})")
            
            user_data = update.effective_user
            
            # Создаем или обновляем пользователя
            user = User(
                telegram_id=user_data.id,
                username=user_data.username,
                first_name=user_data.first_name,
                last_name=user_data.last_name
            )
            
            await create_user(user)
            logger.info(f"Пользователь создан/обновлен: {user_data.id} (@{user_data.username})")
            
            # Отправляем приветственное сообщение
            welcome_message = self.messages_config.get('welcome', 
                '🤖 Добро пожаловать в AI-CRM бот!\n\nЯ помогу вам с информацией о наших услугах.\nНапишите мне что-нибудь!')
            
            keyboard = self._get_main_keyboard()
            
            await update.message.reply_text(
                welcome_message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка в команде /start: {e}")
            await update.message.reply_text("Добро пожаловать! Произошла небольшая ошибка, но я готов работать.")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /help"""
        try:
            logger.info(f"Команда /help от пользователя {update.effective_user.id}")
            help_message = self.messages_config.get('help', 
                'ℹ️ Помощь:\n\n/start - начать работу\n/help - справка\n/menu - главное меню')
            await update.message.reply_text(help_message, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Ошибка в команде /help: {e}")
            await update.message.reply_text("Вы можете использовать команды:\n/start - начать\n/help - справка\n/menu - меню")

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /menu"""
        try:
            logger.info(f"Команда /menu от пользователя {update.effective_user.id}")
            menu_message = self.messages_config.get('menu', '📋 Главное меню:\n\nВыберите действие.')
            keyboard = self._get_main_keyboard()
            
            await update.message.reply_text(
                menu_message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка в команде /menu: {e}")
            await update.message.reply_text("📋 Главное меню")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений пользователей"""
        try:
            user_data = update.effective_user
            message_text = update.message.text
            
            logger.info(f"Личное сообщение от {user_data.id} (@{user_data.username}): {message_text[:50]}...")
            
            # Получаем пользователя из БД
            user = await get_user_by_telegram_id(user_data.id)
            if not user:
                # Создаем нового пользователя если не существует
                user = User(
                    telegram_id=user_data.id,
                    username=user_data.username,
                    first_name=user_data.first_name,
                    last_name=user_data.last_name
                )
                user = await create_user(user)
                logger.info(f"Создан новый пользователь: {user_data.id}")
            
            # Увеличиваем счетчик сообщений
            await increment_user_message_count(user_data.id)
            
            # Анализируем сообщение
            interest_score = 0
            ai_analysis = ""
            response_text = "Спасибо за ваше сообщение!"
            
            try:
                claude_client = get_claude_client()
                if claude_client and claude_client.client:
                    logger.info("Используем Claude для анализа сообщения")
                    # Получаем контекст предыдущих сообщений
                    recent_messages = await get_user_messages(user.id, limit=5)
                    context_list = [msg.text for msg in recent_messages if msg.text]
                    
                    # Анализируем заинтересованность с таймаутом
                    import asyncio
                    try:
                        interest_task = asyncio.wait_for(
                            claude_client.analyze_user_interest(message_text, context_list),
                            timeout=10.0  # 10 секунд таймаут
                        )
                        interest_score = await interest_task
                        
                        # Генерируем ответ
                        response_task = asyncio.wait_for(
                            claude_client.generate_response(message_text, context_list, interest_score),
                            timeout=10.0  # 10 секунд таймаут
                        )
                        response_text = await response_task
                        
                        ai_analysis = f"Interest: {interest_score}/100"
                        
                        # Обновляем скор пользователя (берем максимальный)
                        if interest_score > user.interest_score:
                            await update_user_interest_score(user_data.id, interest_score)
                        
                        logger.info(f"AI анализ: score={interest_score}")
                        
                    except asyncio.TimeoutError:
                        logger.warning("Claude API таймаут, используем простой анализ")
                        interest_score = self._simple_interest_analysis(message_text)
                        response_text = self._simple_response_generation(message_text, interest_score)
                    except Exception as claude_error:
                        logger.warning(f"Claude API ошибка: {claude_error}, используем простой анализ")
                        interest_score = self._simple_interest_analysis(message_text)
                        response_text = self._simple_response_generation(message_text, interest_score)
                else:
                    logger.info("Claude API недоступен, используем простой анализ")
                    interest_score = self._simple_interest_analysis(message_text)
                    response_text = self._simple_response_generation(message_text, interest_score)
                
            except Exception as e:
                logger.error(f"Ошибка AI анализа: {e}")
                # Простой анализ без AI
                interest_score = self._simple_interest_analysis(message_text)
                response_text = self._simple_response_generation(message_text, interest_score)
            
            logger.info(f"Анализ завершен: score={interest_score}")
            
            # Сохраняем сообщение в БД если включено
            if self.features.get('save_all_messages', True):
                try:
                    message = Message(
                        user_id=user.id,
                        telegram_message_id=update.message.message_id,
                        text=message_text,
                        ai_analysis=ai_analysis,
                        interest_score=interest_score,
                        response_sent=True
                    )
                    await create_message(message)
                    logger.info("Сообщение сохранено в БД")
                except Exception as e:
                    logger.error(f"Ошибка сохранения сообщения: {e}")
            
            # Отправляем ответ если включены автоответы
            if self.features.get('auto_response', True):
                keyboard = None
                if interest_score >= 70:  # Высокая заинтересованность
                    keyboard = self._get_interested_user_keyboard()
                elif interest_score <= 30:  # Низкая заинтересованность
                    keyboard = self._get_help_keyboard()
                else:  # Средняя заинтересованность
                    keyboard = self._get_main_keyboard()
                
                await update.message.reply_text(
                    response_text,
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )
                
                logger.info(f"Ответ отправлен пользователю {user_data.id}: score={interest_score}")
            else:
                logger.info("Автоответы отключены")
            
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                await update.message.reply_text("Спасибо за сообщение! Мы обработаем его в ближайшее время.")
            except:
                logger.error("Не удалось отправить сообщение об ошибке")

    def _simple_interest_analysis(self, message: str) -> int:
        """Простой анализ заинтересованности без AI"""
        message_lower = message.lower()
        
        # Высокий интерес
        high_words = ['купить', 'заказать', 'цена', 'стоимость', 'сколько стоит', 'готов купить']
        # Средний интерес
        medium_words = ['интересно', 'подробнее', 'расскажите', 'как работает', 'хочу узнать']
        # Низкий интерес
        low_words = ['дорого', 'не нужно', 'не интересно', 'спам']
        
        for word in high_words:
            if word in message_lower:
                return 85
        
        for word in medium_words:
            if word in message_lower:
                return 60
        
        for word in low_words:
            if word in message_lower:
                return 20
        
        # Если есть вопрос - средний интерес
        if '?' in message or any(word in message_lower for word in ['как', 'что', 'где', 'когда']):
            return 50
        
        return 40

    def _simple_response_generation(self, message: str, interest_score: int) -> str:
        """Простая генерация ответа без AI"""
        if interest_score >= 70:
            return "Отлично! Вижу, что вас заинтересовали наши услуги. Наш менеджер свяжется с вами для обсуждения деталей! 📞"
        elif interest_score >= 40:
            return "Спасибо за интерес! Если у вас есть вопросы о наших услугах, я буду рад помочь. 😊"
        else:
            return "Спасибо за сообщение! Если понадобится помощь, обращайтесь. 👍"

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback запросов от инлайн кнопок"""
        query = update.callback_query
        try:
            data = query.data
            
            # Обрабатываем только пользовательские callback
            if data.startswith('admin_'):
                return  # Пропускаем админские
            
            await query.answer()
            logger.info(f"User callback от пользователя {query.from_user.id}: {data}")
            
            if data == "main_menu":
                await self._show_main_menu(query)
            elif data == "help":
                await self._show_help(query)
            elif data == "contact":
                await self._show_contact(query)
            elif data == "about":
                await self._show_about(query)
            else:
                logger.warning(f"Неизвестная пользовательская команда: {data}")
                
        except Exception as e:
            logger.error(f"Ошибка обработки user callback: {e}")
            try:
                await query.edit_message_text("❌ Произошла ошибка. Попробуйте еще раз.")
            except:
                pass

    def _get_main_keyboard(self):
        """Основная клавиатура для пользователей"""
        keyboard = [
            [
                InlineKeyboardButton("📞 Контакты", callback_data="contact"),
                InlineKeyboardButton("ℹ️ Помощь", callback_data="help")
            ],
            [
                InlineKeyboardButton("📋 О компании", callback_data="about")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _get_interested_user_keyboard(self):
        """Клавиатура для заинтересованных пользователей"""
        keyboard = [
            [
                InlineKeyboardButton("💬 Связаться с менеджером", callback_data="contact"),
                InlineKeyboardButton("📋 Узнать больше", callback_data="about")
            ],
            [
                InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _get_help_keyboard(self):
        """Клавиатура с помощью"""
        keyboard = [
            [
                InlineKeyboardButton("ℹ️ Помощь", callback_data="help"),
                InlineKeyboardButton("📞 Контакты", callback_data="contact")
            ],
            [
                InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def _show_main_menu(self, query):
        """Показать главное меню"""
        menu_message = self.messages_config.get('menu', '📋 Главное меню:\n\nВыберите действие.')
        keyboard = self._get_main_keyboard()
        
        try:
            await query.edit_message_text(
                menu_message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка показа главного меню: {e}")

    async def _show_help(self, query):
        """Показать справку"""
        help_message = self.messages_config.get('help', 
            'ℹ️ <b>Помощь</b>\n\n'
            '/start - начать работу с ботом\n'
            '/help - показать эту справку\n'
            '/menu - открыть главное меню\n\n'
            'Напишите любое сообщение и я помогу вам!')
        
        keyboard = [
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
        ]
        
        try:
            await query.edit_message_text(
                help_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка показа справки: {e}")

    async def _show_contact(self, query):
        """Показать контактную информацию"""
        contact_message = self.messages_config.get('contact', 
            '📞 <b>Контактная информация</b>\n\n'
            '• <b>Telegram:</b> @support\n'
            '• <b>Email:</b> support@example.com\n'
            '• <b>Телефон:</b> +7 (999) 123-45-67\n\n'
            'Мы работаем 24/7 и всегда готовы помочь!')
        
        keyboard = [
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
        ]
        
        try:
            await query.edit_message_text(
                contact_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка показа контактов: {e}")

    async def _show_about(self, query):
        """Показать информацию о компании"""
        about_message = """📋 <b>О нашей компании</b>

🚀 <b>AI-CRM Solutions</b> - ведущий поставщик решений для автоматизации продаж и управления клиентами.

<b>🔹 Наши услуги:</b>
• Разработка CRM систем
• Автоматизация продаж
• Telegram боты для бизнеса
• Интеграции с API
• Аналитика и отчеты

<b>🔹 Преимущества:</b>
• ✅ Профессиональный подход
• ✅ Индивидуальные решения  
• ✅ Поддержка 24/7
• ✅ Гарантия качества
• ✅ Доступные цены

<b>📈 Результаты наших клиентов:</b>
• Увеличение продаж до 40%
• Автоматизация 80% процессов
• Экономия времени до 60%

Свяжитесь с нами для бесплатной консультации!"""
        
        keyboard = [
            [
                InlineKeyboardButton("💬 Связаться", callback_data="contact"),
                InlineKeyboardButton("🔙 Меню", callback_data="main_menu")
            ]
        ]
        
        try:
            await query.edit_message_text(
                about_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка показа информации о компании: {e}")