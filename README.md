# AI-CRM Telegram Bot

Telegram бот с искусственным интеллектом для автоматизации CRM процессов и поиска потенциальных клиентов.

## Возможности

- 🤖 AI-анализ сообщений пользователей с оценкой заинтересованности
- 🎯 Автоматический поиск лидов в Telegram каналах
- 👥 Управление пользователями и их сегментация
- 📊 Аналитика и статистика
- 📢 Массовые рассылки
- 🔧 Админ-панель для управления

## Быстрый старт

### 1. Получите необходимые ключи

**Telegram Bot:**
- Создайте бота у [@BotFather](https://t.me/BotFather)
- Получите Bot Token

**Ваш Telegram ID:**
- Узнайте свой ID у [@userinfobot](https://t.me/userinfobot)

**Claude API (опционально):**
- Зарегистрируйтесь на [console.anthropic.com](https://console.anthropic.com)
- Получите API ключ

**Telegram API (для парсинга каналов, опционально):**
- Зарегистрируйтесь на [my.telegram.org](https://my.telegram.org)
- Получите `api_id` и `api_hash`

### 2. Настройте переменные окружения

Скопируйте файл с примером:
```bash
cp .env.example .env
```

Отредактируйте `.env` файл:
```bash
# Обязательные параметры
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=your_telegram_id_here

# Опционально для AI функций
CLAUDE_API_KEY=your_claude_api_key_here

# Опционально для парсинга каналов
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
PARSING_CHANNELS=@channel1,@channel2,-1001234567890
```

### 3. Установите зависимости

```bash
# Создайте виртуальное окружение
python -m venv venv

# Активируйте его
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Установите зависимости
pip install -r requirements.txt
```

### 4. Запустите бота

```bash
python main.py
```

## Конфигурация

### Переменные окружения (.env)

| Переменная | Описание | Обязательная |
|------------|----------|--------------|
| `BOT_TOKEN` | Токен Telegram бота | ✓ |
| `ADMIN_IDS` | ID администраторов (через запятую) | ✓ |
| `CLAUDE_API_KEY` | Ключ Claude API | ✗ |
| `CLAUDE_MODEL` | Модель Claude | ✗ |
| `TELEGRAM_API_ID` | API ID для парсинга | ✗ |
| `TELEGRAM_API_HASH` | API Hash для парсинга | ✗ |
| `PARSING_CHANNELS` | Каналы для парсинга (через запятую) | ✗ |
| `PARSING_ENABLED` | Включить парсинг (true/false) | ✗ |
| `PARSING_MIN_SCORE` | Минимальный скор для лидов | ✗ |

### Примеры настройки каналов

```bash
# По username
PARSING_CHANNELS=@channel1,@channel2

# По ID
PARSING_CHANNELS=-1001234567890,-1001234567891

# Смешанный формат
PARSING_CHANNELS=@public_channel,-1001234567890,@another_channel
```

### Настройка сообщений и промптов

Отредактируйте файл `config.yaml` для изменения:
- Текстов сообщений бота
- Промптов для AI анализа
- Контактной информации

## Команды

### Для пользователей:
- `/start` - начать работу с ботом
- `/help` - справка по командам
- `/menu` - главное меню

### Для администраторов:
- `/admin` - админ панель
- `/users` - список пользователей
- `/leads` - найденные лиды
- `/stats` - статистика бота
- `/channels` - управление каналами
- `/broadcast <текст>` - рассылка сообщения
- `/settings` - настройки бота

## Режимы работы

### 1. Базовый режим (без API ключей)
- Простые ответы на основе ключевых слов
- Базовая статистика пользователей
- Админ-панель

### 2. AI режим (с Claude API)
- Умный анализ сообщений
- Оценка заинтересованности пользователей
- Генерация персонализированных ответов

### 3. Полный режим (с Telegram API)
- Парсинг каналов
- Автоматический поиск лидов
- Полная аналитика

## Структура проекта

```
mvp_crm/
├── .env                    # Переменные окружения (создать)
├── .env.example           # Пример переменных окружения
├── config.yaml            # Сообщения и промпты
├── main.py                # Главный файл запуска
├── requirements.txt       # Зависимости
├── ai/
│   └── claude_client.py   # Клиент для Claude API
├── database/
│   ├── models.py          # Модели данных
│   └── operations.py      # CRUD операции
├── handlers/
│   ├── user.py           # Обработчики пользователей
│   └── admin.py          # Админские команды
├── myparser/
│   └── channel_parser.py  # Парсер каналов
└── utils/
    ├── config_loader.py   # Загрузчик конфигурации
    └── helpers.py         # Вспомогательные функции
```

## Безопасность

- ✅ Все секретные данные хранятся в `.env` файле
- ✅ `.env` добавлен в `.gitignore`
- ✅ Предоставлен `.env.example` без реальных ключей
- ✅ Валидация конфигурации при запуске

## Развертывание

### Локально
```bash
git clone <repository>
cd mvp_crm
cp .env.example .env
# Отредактируйте .env
pip install -r requirements.txt
python main.py
```

### На сервере
```bash
# Используйте systemd или supervisor для автозапуска
# Пример systemd unit файла:
[Unit]
Description=AI CRM Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/path/to/mvp_crm
Environment=PATH=/path/to/mvp_crm/venv/bin
ExecStart=/path/to/mvp_crm/venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Устранение неполадок

### Проблема: Бот не запускается
- Проверьте правильность `BOT_TOKEN` в `.env`
- Убедитесь, что бот не заблокирован

### Проблема: AI не работает
- Проверьте `CLAUDE_API_KEY` в `.env`
- Убедитесь в наличии средств на аккаунте Claude

### Проблема: Парсинг не работает
- Проверьте `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`
- Убедитесь, что каналы указаны корректно

## Лицензия

MIT License

## Поддержка

Если у вас возникли вопросы или проблемы:
1. Проверьте логи бота
2. Убедитесь в правильности настройки `.env`
3. Создайте issue в репозитории