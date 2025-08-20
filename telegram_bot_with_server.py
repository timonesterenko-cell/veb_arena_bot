#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Телеграм-бот для проверки мероприятий на ВЭБ Арене
с встроенным веб-сервером для поддержания активности на Replit
"""

import asyncio
import logging
import re
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from bs4 import BeautifulSoup
import schedule
import time
from threading import Thread
from aiohttp import web

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Токен из переменной окружения или файла
VEB_ARENA_URL = "https://veb-arena.com/events"
CHECK_INTERVAL_HOURS = 1  # Проверять каждый час
WEB_SERVER_PORT = int(os.getenv("PORT", 8080))  # Порт для веб-сервера

class VebArenaBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.subscribers = set()  # Здесь будут храниться ID пользователей
        self.current_events = []
        self.notified_dates = set()  # Даты, за которые уже отправлены уведомления
        
        # Регистрируем обработчики команд
        self.dp.message.register(self.start_command, Command("start"))
        self.dp.message.register(self.help_command, Command("help"))
        self.dp.message.register(self.check_command, Command("check"))
        self.dp.message.register(self.subscribe_command, Command("subscribe"))
        self.dp.message.register(self.unsubscribe_command, Command("unsubscribe"))
        self.dp.message.register(self.status_command, Command("status"))
        
    async def start_command(self, message: Message):
        """Обработчик команды /start"""
        welcome_text = (
            "🏟️ Добро пожаловать в бот ВЭБ Арены!\n\n"
            "Я помогу вам отслеживать мероприятия на стадионе "
            "и напомню, когда нельзя покупать алкоголь.\n\n"
            "📝 Доступные команды:\n"
            "/check - проверить мероприятия сегодня\n"
            "/subscribe - подписаться на уведомления\n"
            "/unsubscribe - отписаться от уведомлений\n"
            "/status - статус работы бота\n"
            "/help - показать справку"
        )
        await message.answer(welcome_text)
        
    async def help_command(self, message: Message):
        """Обработчик команды /help"""
        help_text = (
            "🤖 Справка по боту ВЭБ Арены\n\n"
            "Этот бот проверяет расписание мероприятий на ВЭБ Арене "
            "и уведомляет о днях, когда действует запрет на продажу алкоголя.\n\n"
            "📋 Команды:\n"
            "• /start - запустить бота\n"
            "• /check - проверить мероприятия на сегодня\n"
            "• /subscribe - подписаться на уведомления\n"
            "• /unsubscribe - отписаться от уведомлений\n"
            "• /status - показать статус работы бота\n"
            "• /help - показать эту справку\n\n"
            "🔔 Как работают уведомления:\n"
            "• Приходят ТОЛЬКО в дни мероприятий\n"
            "• Один раз в день (обычно утром)\n"
            "• Если событий нет - уведомлений нет\n\n"
            "🔍 Бот проверяет сайт каждый час автоматически."
        )
        await message.answer(help_text)
        
    async def status_command(self, message: Message):
        """Обработчик команды /status"""
        status_text = (
            "📊 Статус бота:\n\n"
            f"🔗 Подписчиков: {len(self.subscribers)}\n"
            f"⏰ Последняя проверка: {datetime.now().strftime('%H:%M:%S')}\n"
            f"🌐 Веб-сервер: активен на порту {WEB_SERVER_PORT}\n"
            f"✅ Бот работает штатно"
        )
        await message.answer(status_text)
        
    async def check_command(self, message: Message):
        """Обработчик команды /check"""
        await message.answer("🔍 Проверяю мероприятия на сегодня...")
        
        events = await self.get_events_for_today()
        
        if events:
            response = "🚫 ВНИМАНИЕ! Сегодня запрещена продажа алкоголя!\n\n"
            response += "📅 Мероприятия на сегодня:\n"
            for event in events:
                response += f"• {event['title']} в {event['time']}\n"
        else:
            response = "✅ Сегодня мероприятий нет. Продажа алкоголя разрешена."
            
        await message.answer(response)
        
    async def subscribe_command(self, message: Message):
        """Обработчик команды /subscribe"""
        user_id = message.from_user.id
        if user_id not in self.subscribers:
            self.subscribers.add(user_id)
            await message.answer(
                "✅ Вы подписались на уведомления!\n\n"
                "🔔 Я буду присылать уведомления ТОЛЬКО в дни, "
                "когда на ВЭБ Арене проходят мероприятия.\n\n"
                "📅 Уведомление приходит один раз в день утром.\n"
                "🚫 В эти дни действует запрет на продажу алкоголя.\n\n"
                "💡 Если мероприятий нет - уведомлений не будет!"
            )
        else:
            await message.answer("ℹ️ Вы уже подписаны на уведомления.")
            
    async def unsubscribe_command(self, message: Message):
        """Обработчик команды /unsubscribe"""
        user_id = message.from_user.id
        if user_id in self.subscribers:
            self.subscribers.remove(user_id)
            await message.answer("❌ Вы отписались от уведомлений.")
        else:
            await message.answer("ℹ️ Вы не были подписаны на уведомления.")

    async def get_events_for_today(self) -> List[Dict]:
        """Получить список мероприятий на сегодня"""
        try:
            events = await self.parse_veb_arena_events()
            today = datetime.now().date()
            
            today_events = []
            for event in events:
                if event['date'].date() == today:
                    today_events.append(event)
                    
            return today_events
            
        except Exception as e:
            logger.error(f"Ошибка при получении событий: {e}")
            return []
    
    async def parse_veb_arena_events(self) -> List[Dict]:
        """Парсинг событий с сайта ВЭБ Арены"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                async with session.get(VEB_ARENA_URL, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Ошибка HTTP: {response.status}")
                        return []
                        
                    html = await response.text()
                    
            soup = BeautifulSoup(html, 'html.parser')
            events = []
            
            # Ищем по шаблону даты в тексте
            date_pattern = r'(\d{1,2})\s*(вс|пн|вт|ср|чт|пт|сб).*?(\d{1,2}:\d{2})'
            text_content = soup.get_text()
            
            matches = re.finditer(date_pattern, text_content, re.IGNORECASE)
            for match in matches:
                try:
                    day = int(match.group(1))
                    time_str = match.group(3)
                    
                    # Определяем месяц и год (текущий)
                    now = datetime.now()
                    event_date = datetime(now.year, now.month, day)
                    
                    # Если дата уже прошла в этом месяце, берем следующий месяц
                    if event_date < now:
                        if now.month == 12:
                            event_date = datetime(now.year + 1, 1, day)
                        else:
                            event_date = datetime(now.year, now.month + 1, day)
                    
                    # Ищем название события рядом с датой
                    context_start = max(0, match.start() - 100)
                    context_end = min(len(text_content), match.end() + 100)
                    context = text_content[context_start:context_end]
                    
                    # Ищем ЦСКА в контексте
                    if 'ЦСКА' in context:
                        event_title = self.extract_event_title(context)
                        
                        events.append({
                            'title': event_title,
                            'date': event_date,
                            'time': time_str
                        })
                        
                except ValueError:
                    continue
            
            return events
            
        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}")
            return []
    
    def extract_event_title(self, context: str) -> str:
        """Извлечь название события из контекста"""
        # Ищем название команды-соперника
        teams = ['Акрон', 'Краснодар', 'Балтика', 'Спартак', 'Крылья Советов', 
                'Пари Нижний Новгород', 'Оренбург', 'Динамо', 'Динамо Махачкала',
                'Сочи', 'Ростов', 'Зенит', 'Локомотив']
        
        for team in teams:
            if team in context:
                return f"ЦСКА — {team}"
                
        # Если команда не найдена, возвращаем общее название
        if 'ЦСКА' in context:
            return "Матч ЦСКА"
            
        return "Мероприятие на ВЭБ Арене"
    
    async def check_and_notify(self):
        """Проверить события и отправить уведомления"""
        try:
            events = await self.get_events_for_today()
            today = datetime.now().date()
            
            # Отправляем уведомления только если:
            # 1. Есть события сегодня
            # 2. Есть подписчики
            # 3. Уведомление за сегодня ещё не отправлялось
            if events and self.subscribers and today not in self.notified_dates:
                message = "🚫 ВНИМАНИЕ! Сегодня запрещена продажа алкоголя!\n\n"
                message += "📅 Мероприятия на сегодня:\n"
                for event in events:
                    message += f"• {event['title']} в {event['time']}\n"
                
                message += "\n💡 Это уведомление отправляется только в дни мероприятий."
                
                # Отправляем уведомления всем подписчикам
                sent_count = 0
                for user_id in self.subscribers.copy():
                    try:
                        await self.bot.send_message(user_id, message)
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                        # Удаляем пользователя, если бот заблокирован
                        if "bot was blocked" in str(e).lower():
                            self.subscribers.discard(user_id)
                
                # Отмечаем, что за сегодня уведомление отправлено
                self.notified_dates.add(today)
                logger.info(f"📤 Отправлено уведомлений: {sent_count} пользователям о мероприятиях {today}")
                
            elif events:
                logger.info(f"ℹ️ События найдены на {today}, но уведомление уже отправлялось")
            else:
                logger.info(f"✅ Мероприятий на {today} не найдено")
                
            # Очищаем старые записи (старше 7 дней)
            week_ago = today - timedelta(days=7)
            self.notified_dates = {date for date in self.notified_dates if date > week_ago}
                            
        except Exception as e:
            logger.error(f"Ошибка в check_and_notify: {e}")
    
    def schedule_checker(self):
        """Запуск планировщика в отдельном потоке"""
        schedule.every().hour.do(lambda: asyncio.create_task(self.check_and_notify()))
        
        while True:
            schedule.run_pending()
            time.sleep(60)
    
    async def web_server(self):
        """Простой веб-сервер для поддержания активности на Replit"""
        async def health_check(request):
            """Главная страница для проверки работы бота"""
            return web.Response(
                text=f"✅ VEB Arena Bot is running!\n"
                     f"🕐 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                     f"👥 Subscribers: {len(self.subscribers)}\n"
                     f"🔄 Status: Active",
                content_type='text/plain'
            )
        
        async def api_status(request):
            """API endpoint для получения статуса"""
            return web.json_response({
                'status': 'active',
                'subscribers': len(self.subscribers),
                'timestamp': datetime.now().isoformat(),
                'uptime': 'running'
            })
        
        app = web.Application()
        app.router.add_get('/', health_check)
        app.router.add_get('/status', api_status)
        app.router.add_get('/health', health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT)
        await site.start()
        logger.info(f"🌐 Веб-сервер запущен на порту {WEB_SERVER_PORT}")
        logger.info(f"🔗 Доступен по адресу: http://localhost:{WEB_SERVER_PORT}")
    
    async def start(self):
        """Запуск бота"""
        logger.info("🚀 Запуск бота...")
        
        # Запускаем веб-сервер для поддержания активности
        await self.web_server()
        
        # Запускаем планировщик в отдельном потоке
        scheduler_thread = Thread(target=self.schedule_checker, daemon=True)
        scheduler_thread.start()
        
        # Начальная проверка
        await self.check_and_notify()
        
        logger.info("✅ Бот успешно запущен и готов к работе")
        
        # Запуск polling
        await self.dp.start_polling(self.bot)

def main():
    """Главная функция"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Ошибка: Не указан токен бота!")
        print("📝 Создайте секрет BOT_TOKEN в Replit или укажите токен в коде")
        return
        
    bot = VebArenaBot(BOT_TOKEN)
    
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")

if __name__ == "__main__":
    main()
