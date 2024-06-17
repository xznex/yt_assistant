from __future__ import annotations

import asyncio
from datetime import datetime
import io
import logging
import os
import re
from uuid import uuid4

import httpx
from PIL import Image
from pydub import AudioSegment
from telegram import BotCommandScopeAllGroupChats, Update, constants
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle
from telegram import InputTextMessageContent, BotCommand
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut, BadRequest, TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, \
    filters, CallbackQueryHandler, Application, ContextTypes, CallbackContext

from openai_helper import OpenAIHelper, localized_text
from usage_tracker import UsageTracker
from utils import is_group_chat, get_thread_id, message_text, wrap_with_indicator, split_into_chunks, \
    edit_message_with_retry, get_stream_cutoff_values, is_allowed, get_remaining_budget, is_within_budget, \
    get_reply_to_message_id, add_chat_request_to_usage_tracker, error_handler, is_direct_result, handle_direct_result, \
    cleanup_intermediate_files
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled, \
    TranslationLanguageNotAvailable

from database import Session
from models import User, Subscription

from octoparse import Octoparse
import json
from parser import parser


AWAITING_USER_ID, AWAITING_MESSAGE_TEXT, AWAITING_FILE = range(3)
ADMIN_CHAT_ID = 627512965
ADMINS_CHAT_ID = [627512965, 5235703016, 71087432]

# Устанавливаем уровень логгирования, чтобы видеть ошибки
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


class UserContext:
    """
    Class user context
    """

    def __init__(self):
        self._chat_id = None
        self._name = None  # имя
        self._channel_description = None  # True или False
        self._channel_idea = None  # строка
        self._analytics_channel_description = None
        self._analytics_channel_audience = None
        self._analytics_channel_goals = None
        self._analytics_words = None
        self._analytics_links = None
        self._analytics_channel_characteristics = None
        self.admin_chat_id_of_user_for_send_file = None
        self.admin_text_to_send_all_users = None

    def update_user_name(self, user_id, new_name):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, name=new_name)
                session.add(new_user)
            else:
                if user.name == new_name:
                    return
                user.name = new_name

            self._name = new_name
            session.commit()

    def save_description(self, user_id, new_description):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, channel_description=new_description)
                session.add(new_user)
            else:
                user.channel_description = new_description

            self._channel_description = new_description
            session.commit()

    def save_idea(self, user_id, new_idea):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, channel_idea=new_idea)
                session.add(new_user)
            else:
                user.channel_idea = new_idea

            self._channel_idea = new_idea
            session.commit()

    def save_analytics_channel_description(self, user_id, analytics_channel_description):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, analytics_channel_description=analytics_channel_description)
                session.add(new_user)
            else:
                user.analytics_channel_description = analytics_channel_description

            self._analytics_channel_description = analytics_channel_description
            session.commit()

    def save_analytics_channel_audience(self, user_id, analytics_channel_audience):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, analytics_channel_audience=analytics_channel_audience)
                session.add(new_user)
            else:
                user.analytics_channel_audience = analytics_channel_audience

            self._analytics_channel_audience = analytics_channel_audience
            session.commit()

    def save_analytics_channel_goals(self, user_id, analytics_channel_goals):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, analytics_channel_goals=analytics_channel_goals)
                session.add(new_user)
            else:
                user.analytics_channel_goals = analytics_channel_goals

            self._analytics_channel_goals = analytics_channel_goals
            session.commit()

    def save_analytics_words(self, user_id, analytics_words):
        print("Тут ошибка 2")
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, analytics_words=analytics_words)
                session.add(new_user)
            else:
                user.analytics_words = analytics_words

            self._analytics_words = analytics_words
            session.commit()

    def save_analytics_links(self, user_id, analytics_links):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, analytics_links=analytics_links)
                session.add(new_user)
            else:
                user.analytics_links = analytics_links

            self._analytics_links = analytics_links
            session.commit()

    def save_analytics_channel_characteristics(self, user_id, analytics_channel_characteristics):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, analytics_channel_characteristics=analytics_channel_characteristics)
                session.add(new_user)
            else:
                user.analytics_channel_characteristics = analytics_channel_characteristics

            self._analytics_channel_characteristics = analytics_channel_characteristics
            session.commit()


class ChatGPTTelegramBot:
    """
    Class representing a ChatGPT Telegram Bot.
    """

    def __init__(self, config: dict, openai: OpenAIHelper):
        """
        Initializes the bot with the given configuration and GPT bot object.
        :param config: A dictionary containing the bot configuration
        :param openai: OpenAIHelper object
        """
        self.config = config
        self.openai = openai
        bot_language = self.config['bot_language']
        # меню, личный кабинет, информация о проекте, связаться с поддержкой, реферальная система
        self.commands = [
            BotCommand(command='info', description="Информация о проекте"),
            BotCommand(command='menu', description="Меню"),
            BotCommand(command='account', description="Личный кабинет"),
            BotCommand(command='analytics', description="Получить аналитику видео"),
            BotCommand(command='naming', description="Упаковка канала"),
            BotCommand(command='video', description="Создать сценарий видео"),
            BotCommand(command='shorts', description="Создать сценарий shorts"),
            BotCommand(command='seo', description="Придумать название и описание к видео"),
            BotCommand(command='referral', description="Реферальная система"),
            BotCommand(command='support', description="Связаться с поддержкой"),
            BotCommand(command='faq', description="Служба поддержки"),
            BotCommand(command='restart', description="Перезапуск бота"),
        ]
        # If imaging is enabled, add the "image" command to the list
        # if self.config.get('enable_image_generation', False):
        #     self.commands.append(
        #         BotCommand(command='image', description=localized_text('image_description', bot_language)))

        # if self.config.get('enable_tts_generation', False):
        #     self.commands.append(BotCommand(command='tts', description=localized_text('tts_description', bot_language)))

        self.group_commands = [BotCommand(
            command='chat', description=localized_text('chat_description', bot_language)
        )] + self.commands
        self.disallowed_message = localized_text('disallowed', bot_language)
        self.budget_limit_message = localized_text('budget_limit', bot_language)
        self.usage = {}
        self.last_message = {}
        self.inline_queries_cache = {}

        self.user_contexts = {}
        self.user_states = {}
        self.user_input = {}

    async def check_subscription_status(self, user_id: int, feature: str) -> bool:
        # Проверяем наличие свободных попыток
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return False

            # Получаем количество свободных попыток для конкретной функции
            free_uses_attr = f"{feature}_free_uses"
            free_uses = getattr(user, free_uses_attr, 0)

            if feature == 'analytics_attempts':
                if user.analytics_attempts > 0:
                    user.analytics_attempts -= 1
                    session.commit()
                    return True
                else:
                    return False

            if free_uses > 0:
                # Уменьшаем количество свободных попыток и возвращаем True
                setattr(user, free_uses_attr, free_uses - 1)
                session.commit()
                return True

            current_time = datetime.now()
            subscription = session.query(Subscription) \
                .filter(Subscription.user_id == user_id, Subscription.expiration_date > current_time) \
                .first()
            if subscription:
                # Подписка действует
                return True

        # Нет действующей подписки и свободных попыток
        return False

    async def get_short_url(self, user_id: int):
        url_1_success = f"https://t.me/ytassistantbot?start=subscription_paid_1_days_{user_id}"
        prodamus_url = f"https://fabricbot.payform.ru/?order_id={user_id}&products[0][price]=290&products[0][quantity]=1&products[0][name]=Доступ к чат-боту YouTube ассистент на 1 день&do=link&urlSuccess={url_1_success}"
        async with httpx.AsyncClient() as client:
            response = await client.get(prodamus_url)
            if response.status_code == 200:
                # Извлечение укороченной ссылки из HTML ответа
                match = re.search(r'https://payform.ru/[^\s"]+', response.text)
                if match:
                    return match.group(0)  # Возвращает найденную укороченную ссылку
        return None  # Возвращает None, если ссылка не найдена

    async def check_and_handle_subscription_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                                   feature: str):
        user_id = update.effective_user.id
        has_subscription = await self.check_subscription_status(user_id, feature)

        if not has_subscription:
            subscription_7_id = 1779399
            subscription_30_id = 1779400
            short_url = await self.get_short_url(user_id)
            short_url_analytics_1_sub_30 = await self.get_short_url_analytics_1_sub_30(user_id)
            short_url_analytics_1 = await self.get_short_url_analytics_1(user_id)
            url_7_success = f"https://t.me/ytassistantbot?start=subscription_paid_7_days_{user_id}"
            url_30_success = f"https://t.me/ytassistantbot?start=subscription_paid_30_days_{user_id}"
            keyboard = [
                [InlineKeyboardButton("1 день - 290 рублей", url=short_url)],
                [InlineKeyboardButton("7 дней - 1490 рублей",
                                      url=f'https://fabricbot.payform.ru/?order_id={user_id}&subscription={subscription_7_id}&do=pay&urlSuccess={url_7_success}')],
                [InlineKeyboardButton("30 дней - 4990 рублей",
                                      url=f'https://fabricbot.payform.ru/?order_id={user_id}&subscription={subscription_30_id}&do=pay&urlSuccess={url_30_success}')],
                [InlineKeyboardButton("30 дней + 1 аналитика конкурентов - 7990 рублей",
                                      url=short_url_analytics_1_sub_30)],
                [InlineKeyboardButton("1 Аналитика конкурентов - 4990 рублей", url=short_url_analytics_1)]
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="У вас нет активной подписки или свободных попыток.\n\n"
                     "Чтобы пользоваться ботом без ограничений, необходимо оформить подписку\n\n"
                     "Выбери желаемый тариф и в течение 5-10 минут после оплаты я пришлю тебе сообщение👇🏻",
                reply_markup=reply_markup
            )
            return False
        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''
        args = context.args
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        # Проверяем, есть ли в контексте реферальный код
        referral_code = context.args[0] if context.args else None

        # analytics_1_success_ and analytics_1_sub_30_success_

        if args and args[0].startswith("analytics_1_success_"):
            await update.message.reply_text(
                f"Спасибо за покупку товара \"1 Аналитика конкурентов\"! В течение нескольких минут эта возможность активируется и вы сможете создать 1 аналитику в разделе /analytics.")
            return

        if args and args[0].startswith("analytics_1_sub_30_success_"):
            await update.message.reply_text(
                f"Спасибо за покупку товара \"30 дней + 1 Аналитика конкурентов\"! В течение нескольких минут подписка активируется и вы сможете пользоваться полным функционалом без ограничений, а также иметь возможность создать 1 Аналитику в разделе /analytics.")
            return

        if args and args[0].startswith("subscription_paid_"):
            # https://t.me/ytassistantbot?start=subscription_paid_7_days_627512965
            _, _, days, _, user_id = args[0].split("_")
            # Здесь вы можете добавить логику для определения срока окончания подписки и отправки сообщения пользователю
            # has_subscription = await self.check_subscription_status(user_id, "nothing")

            # if has_subscription:
            if days == '1':
                await update.message.reply_text(
                    f"Спасибо за покупку подписки на 1 день! В течение нескольких минут активируется и вы сможете пользоваться полным функционалом без ограничений.")
            else:
                await update.message.reply_text(
                    f"Спасибо за покупку подписки на {days} дней! В течение нескольких минут и вы сможете пользоваться полным функционалом без ограничений.")
            return

        # Получаем объект пользователя
        user = await context.bot.get_chat_member(chat_id, user_id)

        # Получаем никнейм пользователя
        if user.user.username:
            nickname = user.user.username
            text_start = f"Привет, {nickname}! Я твой карманный YouTube продюсер 👋🏻\n\n" \
                         f"Создатель назвал меня Сильвия, но для тебя я буду ассистентом по старту твоего канала на YouTube 🎥\n\n" \
                         f"Я существую, чтобы ты сэкономил сотни тысяч рублей на найме команды или на дорогом продакшне и начал получать первые просмотры уже сегодня вечером❤️\n\n" \
                         f"Я придумаю за тебя сценарии и даже пропишу теги к видео, тебе останется лишь снять и выложить ролик 😻"

            await context.bot.send_video(chat_id=chat_id, video=open("video/Визитка.mp4", 'rb'),
                                         caption=text_start)

            if chat_id not in self.user_contexts:
                user_context = UserContext()
                self.user_contexts[chat_id] = user_context
            else:
                user_context = self.user_contexts[chat_id]

            user_context.update_user_name(chat_id, nickname)

            self.user_states[update.effective_chat.id] = 'awaiting_channel_description'

            keyboard = [
                [InlineKeyboardButton("Уже есть", callback_data='channel_exists')],
                [InlineKeyboardButton("Собираюсь начать", callback_data='starting_channel')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"У тебя уже есть YouTube канал или ты только собираешься его начать?",
                reply_markup=reply_markup
            )
        else:
            # await context.bot.send_photo(chat_id=chat_id, photo='start_photo.jpg')
            # await update.message.reply_text(
            #     "Привет, я твой карманный YouTube продюсер 👋🏻\n\n"
            #     "Создатель назвал меня Сильвия, но для тебя я буду ассистентом по старту твоего канала на YouTube 🎥\n\n"
            #     "Я существую, чтобы ты сэкономил сотни тысяч рублей на найме команды или на дорогом продакшне и начал получать первые просмотры уже сегодня вечером❤️\n\n"
            #     "Я придумаю за тебя сценарии и даже пропишу теги к видео, тебе останется лишь снять и выложить ролик 😻",
            # )

            text_start = f"Привет, я твой карманный YouTube продюсер 👋🏻\n\n" \
                         f"Создатель назвал меня Сильвия, но для тебя я буду ассистентом по старту твоего канала на YouTube 🎥\n\n" \
                         f"Я существую, чтобы ты сэкономил сотни тысяч рублей на найме команды или на дорогом продакшне и начал получать первые просмотры уже сегодня вечером❤️\n\n" \
                         f"Я придумаю за тебя сценарии и даже пропишу теги к видео, тебе останется лишь снять и выложить ролик 😻"

            await context.bot.send_video(chat_id=chat_id, video=open("video/Визитка.mp4", 'rb'),
                                         caption=text_start)

            await update.message.reply_text(
                "Но для начала давай познакомимся, как тебя зовут?"
            )

            self.user_states[update.effective_chat.id] = 'waiting_for_name'

    async def get_user_context(self, chat_id):
        if chat_id not in self.user_contexts:
            user_context = UserContext()
            self.user_contexts[chat_id] = user_context
        return self.user_contexts[chat_id]

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        state = self.user_states.get(update.effective_chat.id)
        chat_id = update.effective_chat.id
        user_context = await self.get_user_context(chat_id)

        if state == 'waiting_for_name':
            # Сохраняем имя пользователя
            # self.user_context.name = update.message.text

            user_context.update_user_name(chat_id, update.message.text)

            # self.user_names[update.effective_chat.id] = update.message.text
            # Меняем состояние
            self.user_states[update.effective_chat.id] = 'awaiting_channel_description'
            # Задаем вопрос о канале
            keyboard = [
                [InlineKeyboardButton("Уже есть", callback_data='channel_exists')],
                [InlineKeyboardButton("Собираюсь начать", callback_data='starting_channel')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Рада, знакомству, {update.message.text}! У тебя уже есть YouTube канал или ты только собираешься его начать?",
                reply_markup=reply_markup
            )
        elif state == 'awaiting_channel_description':
            # Принимаем текст от пользователя
            user_input = update.message.text
            await self.to_continue_or_see_features(update, context, user_input)

            # Вызываем функцию continue_or_see_features и передаем ей введенный текст
            # await self.turnkey_generation(update, context)
        elif state == 'waiting_user_description':
            user_input = update.message.text
            await self.turnkey_generation(update, context, user_description=user_input)
        elif state == 'waiting_for_seo':
            user_input = update.message.text
            await self.seo_handler(update, context, user_input)
        elif state == 'create_new_video_handler':
            user_input = update.message.text
            await self.create_new_video_handler(update, context, user_input)
        elif state == 'create_new_shorts_handler':
            user_input = update.message.text
            await update.message.reply_text(
                "Отлично! Ушла писать сценарии! 😇"
            )
            await self.create_new_shorts_handler(update, context, user_input)
        elif state == "awaiting_correct_url":
            try:
                # Try processing the URL again
                await self.seo_handler(update, context, update.message.text)
                # If successful, reset the user's state
                self.user_states[user_id] = "normal"
            except ValueError as e:
                # If still invalid, inform the user and wait for another attempt
                await context.bot.send_message(chat_id=update.message.chat_id,
                                               text="Некорректный URL. Пожалуйста, введи правильную ссылку на видео YouTube.")
        elif state == 'input_analytics_channel_description_handler':
            user_context.save_analytics_channel_description(chat_id, update.message.text)
            await self.input_analytics_channel_audience(update, context)
        elif state == 'input_analytics_channel_audience_handler':
            user_context.save_analytics_channel_audience(chat_id, update.message.text)
            await self.input_analytics_channel_goals(update, context)
        elif state == 'input_analytics_channel_goals_handler':
            user_context.save_analytics_channel_goals(chat_id, update.message.text)
            await self.input_analytics_last_step(update, context)
        elif state == 'input_links_handler':
            # user_context.save_analytics_channel_goals(chat_id, update.message.text)
            await self.input_links_handler(update, context, update.message.text)
        elif state == 'input_links_change_text_correct':
            user_context = await self.get_user_context(chat_id)
            user_context.save_analytics_channel_characteristics(user_id, update.message.text)
            await update.message.reply_text(
                "Отлично! Начала генерацию аналитики 😍"
            )
        elif state == 'admin_input_task_id':
            # print(update.effective_chat.id)
            await update.message.reply_text(
                "Все! Как только генерация закончится, я пришлю и тебе и пользователю файл с сообщением. Например, 123-456-789? 123456789"
            )
            # task_id, chat_id = update.message.text.split(', ')
            await self.monitor_task_and_get_data(update, context, update.message.text)
        elif state == 'admin_input_task_id_test':
            task_id, chat_id, *keys = update.message.text.split(', ')

            print(keys)

            octoparse = Octoparse()
            print("пошел мониторинг")
            while True:
                status = octoparse.is_task_running(task_id=task_id)

                # если задача выполнена, выходим из цикла
                if not status:
                    break

                # ожидаем некоторое время перед повторной проверкой
                print("прошло 30 секунд")
                await asyncio.sleep(30)  # например, каждые 30 секунд

            # если status стал False, продолжаем выполнение кода
            print('получение данных')
            data = octoparse.get_task_data(task_id=task_id)

            cleaned_data = []
            for item in data:
                item["Video_Title"] = item["Video_Title"].strip()
                cleaned_data.append(item)

            # print(data)
            print("данные получены")
            try:

                # Открыть файл и загрузить данные
                print("Открыть файл и загрузить данные")

                json_data = json.dumps(cleaned_data, ensure_ascii=False)

                with open(f'analytics_data/data_{task_id}.json', 'w', encoding='utf-8') as f:
                    f.write(json_data)
                    print("Данные успешно сохранены в JSON файл")
                    print("данные отправились в парсер")
                    result_output_file_path = await parser(f'analytics_data/data_{task_id}.json', keys)
                    print("данные вернулись из парсера и отправляются пользователю")
                    await update.effective_message.chat.send_action(constants.ChatAction.UPLOAD_DOCUMENT)

                    # os.path.basename(file_path)
                    print(result_output_file_path, "ВОТ ЗДЕСЬ ПРОБЛЕМА?")
                    with open(result_output_file_path, 'rb') as file:
                        await context.bot.send_document(chat_id=chat_id, document=file,
                                                        filename=f'{result_output_file_path}',
                                                        caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
                                                                "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
                                                                "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
                                                                "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
                                                                "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")

                    for chat_admin_id in ADMINS_CHAT_ID:
                        with open(result_output_file_path, 'rb') as file:
                            await context.bot.send_document(chat_id=chat_admin_id, document=file,
                                                            filename=f'{result_output_file_path}',
                                                            caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
                                                                    "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
                                                                    "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
                                                                    "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
                                                                    "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")
            except Exception as e:
                print(f"Ошибка при сохранении данных в JSON файл: {e}")

        elif state == 'ai_faq':
            await self.prompt(update=update, context=context)
        elif state == 'admin_send_excel_user':
            user_context = await self.get_user_context(chat_id)
            user_context.admin_chat_id_of_user_for_send_file = update.message.text
            file_path = 'output_analytics_data/Dmitry2.json.xlsx'
            os.path.basename(file_path)

            with open(file_path, 'rb') as file:
                await context.bot.send_document(chat_id=update.message.text, document=file,
                                                filename='Аналитика.xlsx',
                                                caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
                                                        "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
                                                        "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
                                                        "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
                                                        "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")

            print("аналитика пошла")
            # await self.send_excel_file(update, context)
        elif state == 'admin_send_excel_file':
            print('zashli')
            user_context = await self.get_user_context(chat_id)
            print('тут', update.message)
            if 'document' in update.message:
                # Получаем объект файла
                excel_file = update.message.document

                # Получаем информацию о файле
                file_name = excel_file.file_name
                file_id = excel_file.file_id

                print(user_context.admin_chat_id_of_user_for_send_file)

                await context.bot.send_document(chat_id=user_context.admin_chat_id_of_user_for_send_file, document=excel_file)

                # Скачиваем файл
                # file_path = os.path.join('excel_files', file_name)
                # await context.bot.get_file(file_id).download(file_path)

                chat = await context.bot.get_chat(chat_id)

                # Отвечаем пользователю о успешном сохранении файла
                await update.message.reply_text(f"Файл '{file_name}' успешно отправлен пользователю {chat.id}.")
            else:
                # Если файл не был прикреплен, отвечаем пользователю об ошибке
                await update.message.reply_text("Пожалуйста, пришлите файл Excel.")
        elif state == 'admin_send_message_to_all_users':
            await self.send_message_to_all_users(update, context, update.message.text)

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.callback_query.from_user.id
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        query = update.callback_query
        await query.answer()
        if query.data == 'channel_exists':
            await self.couple_of_questions(update, context)
        elif query.data == 'starting_channel':
            await self.couple_of_questions(update, context)
        elif query.data == "ready_to_continue":
            await self.input_channel_packaging(update, context)
        elif query.data == "turnkey_channel":
            await update.callback_query.message.reply_text(
                "Отлично, уже ушла разрабатывать концепцию для названия твоего канала, а пока ты можешь еще кое с чем мне помочь. \n\nНапиши от 10 до 40 слов, которыми можно описать идею твоего канала, это сильно поможет нам выводить наши ролик в топы запросов зрителей в будущем 😍"
            )
            await self.turnkey_generation(update, context)
        elif query.data == "view_features":
            await self.view_features(update, context)
        elif query.data == "start_creating_video":
            await self.congratulations_with_readiness(update, context)
        elif query.data == "create_new_video":
            await self.create_new_video(update, context)
        elif query.data == "generate_video_ideas":
            await self.generate_video_ideas(update, context)
        elif query.data == "generate_shorts_ideas":
            await self.generate_shorts_ideas(update, context)
        elif query.data == "create_new_shorts":
            await self.create_new_shorts(update, context)
        elif query.data == "info":
            await self.info(update, context)
        elif query.data == "account":
            await self.account(update, context)
        elif query.data == "input_analytics":
            await self.input_analytics(update, context)
        elif query.data == 'input_links':
            await self.input_links(update, context)
        elif query.data == 'input_generate_analytics':
            await self.input_generate_analytics(update, context)
        elif query.data == "account":
            await self.account(update, context)
        elif query.data == 'input_links_change_text':
            await self.input_links_change_text_handler(update, context)
        elif query.data == 'input_links_generate_analytics':
            await self.input_links_generate_analytics(update, context)
        elif query.data == 'send_excel':
            await self.send_excel(update, context)
        elif query.data == 'send_message_to_all_users':
            await self.send_message_to_all_users_get_text(update, context)

    async def analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="*Что такое аналитика конкурентов?*\n\n"
                 "*Аналитика конкурентов* - это функция сбора данных об успешных видео, которые уже есть на YouTube в размере 100-500 видео за последнюю неделю, месяц и год\n"
                 "Это, по сути, поиск идей для вашего канала - но основанный не на воображении, а на *статистике* и *цифрах*\n\n"
                 "*Как это работает?*\n\n"
                 "Вы рассказываете нам о своем канале — мы анализируем ваши данные, формируем представление о том, что представляет собой ваш проект, формируем портреты целевых зрителей, и получаем информацию о том, кто УЖЕ сейчас ваши конкуренты среди авторов контента и какие видео от них успешны.\n\n"
                 "И собираем базу данных о выложенных видео из открытого доступа в 7-10-15 тысяч видео, а дальше по результатам и статистике этих видео фильтруем их до стадии, когда остаются лишь те идеи и ролики, которые уже «зашли», которые нравится людям 👍🏻\n\n"
                 "*В итоге вы получаете таблицу, в которой можете отследить лучшие ролики конкурентов, которые подходят и вашему проекту / интересны вашим зрителям за неделю, месяц и год. Обычно их около 100-500 штук 🚀*\n\n"
                 "Для чего это? Такая функция позволяет вам не исследовать весь ютюб и это очень важный шаг, чтобы изначально выбрать правильные идеи( ведь если чужие ролики набрали всего 10-20 тысяч просмотров по всему ютюбу, то на вряд ли видео от вас в той же теме соберет больше 30, даже если оно будет максимально качественным 🤝\n\n"
                 "Успех видео зависит не столько от картинки, качеств, сколько от самой идеи видео - если люди его смотрят, значит, ютюб его продвигает. А значит, если мы Создадим видео изначально в той теме, которую люди смотрят - просмотры будут 📽️\n\n"
                 "Поэтому вы можете за минимальную стоимость получить информацию, ценность которой колоссальна, ведь может приносить вам миллионы просмотров еще долгое время 💲\n\n"
                 "*Вам необходимо будет лишь проанализировать этот список и выбрать, какие ролики вы можете повторить и адаптировать под себя.*\n\n"
                 "А сценарии вы можете создать прямо здесь же 🎮",
            parse_mode=ParseMode.MARKDOWN
        )

        keyboard = [
            [InlineKeyboardButton("У меня есть канал", callback_data='input_links')],
            [InlineKeyboardButton("Создаю новый", callback_data='input_analytics')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="У тебя уже есть канал или ты только его создаешь?",
            reply_markup=reply_markup
        )

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        await update.message.reply_text(
            "Добро пожаловать в главное меню! Я помогу тебе с созданием контента на YouTube и оптимизацией видео\n\nВот задачи, с которыми я могу помочь 👇  \n/naming - Упаковка канала \n/analytics - Получить аналитику канала \n/video - Создать сценарий видео \n/shorts - Создать сценарий shorts  \n/seo - Придумать название и описание к видео  \n/restart - Перезапуск бота \n\nВыбирай нужную функцию в меню выше"
        )

    async def couple_of_questions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("Да", callback_data='ready_to_continue')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Я задам всего пару вопросов, а затем ты сможешь перейти к полному доступу и выбрать удобную для себя функцию, готов? 🎥",
            reply_markup=reply_markup
        )

    async def input_channel_packaging(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.message.reply_text(
            "Класс, давай начнем с упаковки канала и я придумаю тебе название и описание. Расскажи мне в 2-3х предложениях о чем твой канал?\n\nПостарайся раскрыться максимально подробно, это правда важно ❤️"
        )
        await update.callback_query.message.reply_text(
            "Напиши мне сообщения, начиная с \"О...\"\n\nНапример: О том, как помогать людям избавляться от тревожности с помощью трансовые техник и как стать более счастливым и ментально здоровым человеком. Мой канал про психологию, мышление и психическое здоровье. Про..."
        )
        self.user_states[update.effective_chat.id] = 'awaiting_channel_description'

    async def to_continue_or_see_features(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        # Сохраняем описание
        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id
        if chat_id not in self.user_contexts:
            user_context = UserContext()
            self.user_contexts[chat_id] = user_context
        else:
            user_context = self.user_contexts[chat_id]

        user_context.save_description(user_id, user_input)
        # user_context.save_description_and_idea(chat_id, user_description, user_input)

        keyboard = [
            [InlineKeyboardButton("Канал \"Под ключ\"", callback_data='turnkey_channel')],
            [InlineKeyboardButton("Посмотреть функции", callback_data='view_features')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # await update.message.reply_text(f"Твоё сообщение: {user_input}")
        self.user_input[update.effective_chat.id] = user_input
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Класс. Теперь ты можешь продолжить процесс создания канала \"под ключ\" или посмотреть доступные функции отдельно, что выбираешь?🎥",
            reply_markup=reply_markup
        )

    async def turnkey_generation(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_description=None):
        user_input = self.user_input.get(update.effective_chat.id)
        chat_id = update.effective_chat.id

        if not user_description:
            await update.callback_query.message.reply_text(
                "Напиши слова списком, сколько сможешь придумать. Пример: Психология, коучинг, мышление, состояние, тело, здоровье, ..."
            )

        self.user_states[update.effective_chat.id] = 'waiting_user_description'

        if user_description == None:
            return

        user_description = user_description

        feature = "naming"
        if not await self.check_and_handle_subscription_status(update, context, feature):
            return

        await update.message.reply_text(
            "Отлично, через 30 секунд вернусь к тебе с идеями с названием и с описанием канала, никуда не уходи!"
        )

        # Сохранение idea
        if chat_id not in self.user_contexts:
            user_context = UserContext()
            self.user_contexts[chat_id] = user_context
        else:
            user_context = self.user_contexts[chat_id]

        user_context.save_idea(chat_id, user_input)

        titles_prompt = f"Придумай 50 версий названий для YouTube канала {user_input}. В названии должно содержаться от 2 до 4 слов, отражающих тематику канала, но они должны выглядеть как целостная фраза. Пожалуйста, кроме 50 названий ничего больше не пиши в этом ответе. На русском языке"
        description_prompt = f"Напиши описание к ютуб каналу про {user_description} В описании должно быть 400 слов. Укажи подробности о том, какой контент здесь люди смогут посмотреть и добавь призывы на подписку на канал и укажи, кому точно стоит оставаться на канале и смотреть его регулярно, чтобы не пропустить новых видео. Ответ должен быть на Русском языке."
        titles_response, titles_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=titles_prompt)
        description_response, description_total_tokens = await self.openai.get_chat_response(chat_id=chat_id,
                                                                                             query=description_prompt)
        # await update.message.reply_text(
        #     f"Придумала для тебя 50 идей для названия, выбери любое понравившееся 👇\n\n{user_input}"
        # )
        await update.message.reply_text(
            f"Придумала для тебя 50 идей для названия, выбери любое понравившееся 👇\n\n{str(titles_response)}"
        )
        # await update.message.reply_text(
        #     f"А вот и описание для канала! Можешь просто скопировать и вставить его. Кстати, я прикрепила ниже инструкцию, как это сделать 👇"
        # )

        keyboard = [
            [InlineKeyboardButton("Приступить к созданию видео", callback_data='start_creating_video')],
            [InlineKeyboardButton("Открыть полный набор функций", callback_data='view_features')],
            [InlineKeyboardButton("Как поставить описание и название?",
                                  url='https://support.google.com/youtube/answer/2657964')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправка сообщения с кнопками
        await context.bot.send_message(chat_id=chat_id,
                                       text=f"А вот и описание для канала! Можешь просто скопировать и вставить его. Кстати, я прикрепила ниже инструкцию, как это сделать 👇\n\n{description_response}",
                                       reply_markup=reply_markup)

        # await self.prompt(update, context, f"Придумай 50 версий названий для YouTube канала {user_input}. В названии должно содержаться от 2 до 4 слов, отражающих тематику канала, но они должны выглядеть как целостная фраза.На русском языке")

        # self.prompt(upd)
        # необохдимо сделать запрос к ChatGPT через метод self.prompt(), в котором будет следующий промпт: ("Придумай 50 версий названий для YouTube канала {user_input}. В названии должно содержаться от 2 до 4 слов, отражающих тематику канала, но они должны выглядеть как целостная фраза.На русском языке")

    async def view_features(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.message.reply_text(
            "Добро пожаловать в главное меню! Я помогу тебе с созданием контента на YouTube и оптимизацией видео\n\nВот задачи, с которыми я могу помочь 👇  \n/naming - Упаковка канала \n/video - Создать сценарий видео \n/shorts - Создать сценарий shorts  \n/seo - Придумать название и описание к видео  \n/restart - Перезапуск бота \n\nВыбирай нужную функцию в меню выше"
        )

    async def congratulations_with_readiness(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = """Поздравляю, наш канал готов! Позже мы создадим шапку и логотип, а теперь предлагаю перейти к созданию первых Shorts, чтобы уже получить первые просмотры.\n\n*Теперь для тебя открыты другие функции, жми кнопку меню, чтобы получить информацию*"""
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data='view_features')]])
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='Markdown')

    async def naming(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        user_id = update.message.from_user.id
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                user.channel_description = None
                user.channel_idea = None
                session.commit()

        await update.message.reply_text(
            "Класс, начнем упаковывать канал. Расскажи мне в 2-3х предложениях о чем твой он?\n\nПостарайся раскрыться максимально подробно, это правда важно ❤️"
        )
        await update.message.reply_text(
            "Напиши мне сообщения, начиная с \"О...\"\n\nНапример: О том, как помогать людям избавляться от тревожности с помощью трансовые техник и как стать более счастливым и ментально здоровым человеком. Мой канал про психологию, мышление и психическое здоровье. Про..."
        )

        self.user_states[update.effective_chat.id] = 'awaiting_channel_description'

    async def shorts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if user and user.channel_description:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Давай посмотрим", callback_data='generate_shorts_ideas')],
                    [InlineKeyboardButton("Создать новый shorts", callback_data='create_new_shorts')]
                ])

                await context.bot.send_message(chat_id=chat_id,
                                               text="Я вижу, что ты уже загружал описание канала!\n\nУ меня появились мысли о чем можно снять твои первые шортсы!",
                                               reply_markup=reply_markup)
                return
            await update.message.reply_text(
                "Приступим к созданию шортсов! Напиши мне в нескольких предложениях о чем хочешь рассказать людям и я придумаю тебе сценарий 🎥\n\nНачинай свое сообщение с \"О...\""
            )

            self.user_states[update.effective_chat.id] = 'create_new_shorts_handler'

    async def generate_shorts_ideas(self, update: Update, context: CallbackContext):
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        user_id = update.callback_query.from_user.id
        with Session() as session:
            await update.callback_query.message.reply_text(
                "Отлично! Скоро вернусь со сценариями!"
            )
            feature = "shorts"
            if not await self.check_and_handle_subscription_status(update, context, feature):
                return
            user = session.query(User).filter(User.id == user_id).first()
            shorts_query = f"Распиши 3 сценариев коротких видео по теме {user.channel_description} :: указав место съемки, раскадровку с числом секунд :: Полный текст, описание ролика с призывом к действию. Ответ должен быть на Русском языке. При создания сценария можно использовать один из методов по списку ниже: 1. Метод «скользкой горки» 2. Техника «шевеления занавеса» 3. Техника «Ложных следов» 4. Метод «Внутренний конфликт» 5. Техника «Крючок»"
            shorts_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id,
                                                                                       query=shorts_query)

            keyboard = [
                [InlineKeyboardButton("Создать еще шортсы", callback_data='create_new_shorts')],
                [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=str(shorts_response),
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

    async def create_new_shorts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.message.reply_text(
            "Приступим к созданию шортсов! Напиши мне в нескольких предложениях о чем хочешь рассказать людям и я придумаю тебе сценарий 🎥\n\nНачинай свое сообщение с \"О...\""
        )

        self.user_states[update.effective_chat.id] = 'create_new_shorts_handler'

    async def create_new_shorts_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id

        feature = "shorts"
        if not await self.check_and_handle_subscription_status(update, context, feature):
            return
        shorts_query = f"Распиши 3 сценариев коротких видео по теме {user_input} :: указав место съемки, раскадровку с числом секунд :: Полный текст, описание ролика с призывом к действию. Ответ должен быть на Русском языке. При создания сценария можно использовать один из методов по списку ниже: 1. Метод «скользкой горки» 2. Техника «шевеления занавеса» 3. Техника «Ложных следов» 4. Метод «Внутренний конфликт» 5. Техника «Крючок»"
        shorts_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=shorts_query)
        keyboard = [
            [InlineKeyboardButton("Создать еще shorts", callback_data='create_new_shorts')],
            [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=str(shorts_response),
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def seo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        await update.message.reply_text(
            "Отлично! Теперь пришли мне ссылку на видео, например, может загрузить его в доступ по ссылке на YouTube и отправить ее мне"
        )

        self.user_states[update.effective_chat.id] = 'waiting_for_seo'

    def check_link(self, url):
        # Регулярные выражения для извлечения идентификатора видео из различных форматов URL YouTube
        regex_patterns = [
            r"(?:http[s]?://)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]+)",  # Сокращённый URL
            r"(?:http[s]?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",  # Стандартный URL с параметром v
            r"(?:http[s]?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]+)",  # URL с /v/
            r"(?:http[s]?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]+)",  # URL встроенного видео
            r"(?:http[s]?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)",  # URL встроенного видео
        ]

        video_id = None
        for pattern in regex_patterns:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                break

        if video_id is None:
            raise ValueError(f"Некорректный URL: {url}. Пожалуйста, введи правильную ссылку на видео YouTube.")

        return video_id

    async def get_subtitles(self, url):
        video_id = self.check_link(url)

        try:
            subtitles = YouTubeTranscriptApi.get_transcript(video_id, languages=['ru', 'en'])
            subtitles_text = " ".join(item['text'] for item in subtitles)
            return subtitles_text
        except NoTranscriptFound:
            raise ValueError("Для данного видео не найдены субтитры.")
        except TranslationLanguageNotAvailable:
            raise ValueError("Для данного видео нет субтитров на русском или английском языке.")
        except TranscriptsDisabled:
            raise ValueError("Субтитры для данного видео отключены, либо ссылка недействительная.")
        except Exception as e:
            raise ValueError(f"Ошибка при получении субтитров: {e}")

    async def seo_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        try:
            feature = "seo"
            if not await self.check_and_handle_subscription_status(update, context, feature):
                return
            await update.message.reply_text(
                "Отлично! Ушла разрабатывать seo! 😇"
            )
            subtitles = await self.get_subtitles(user_input)
            print(subtitles)

            YANDEXGPT_TOKEN = os.environ['YANDEXGPT_TOKEN']

            # seo_query = f"Текст популярного видео: {subtitles[:25000]}. на основании представленного выше текста из видео сделай следующие шаги :: Создай seo оптимизацию для видео на YouTube по заданию ниже: Придумай название для видеоролика на YouTube. Количество слов в названии от 3 до 10. Предложи мне 5 идей :: Придумай описание к видео на ютюбу :: Описание должно состоять из 3 абзацев, первый должен отражать содержание и содержать ключевые слова для выдачи в поиске. Количество предложений от 10 до 15. Второй рассказывает про ролик и так же содержит ключевые слова для seo, количество предложений от 12 до 15. В третьем абзаце должно рассказывать о канале, количество предложений от 14 до 18. В конце описания должно быть 5 хэштегов по теме видео, каждый хэштег - 1 слово. В. Четвертом абзаце к описанию укажи ссылки на мои социальные сети Инстаграм - Телеграмм - :: Придумай 20 тегов к видео на YouTube и перечисли их через запятую :: Фразы могут содержать от 1 до 3 слов. Некоторые теги могут начинать со слова “как”, представь теги единым списком разделив их запятой. :: Также придумай на основе информации выше 10 идей концепции для превью картинок на видео на YouTube, какое должно быть фото на фоне, какого цвета фон, какие элементы расположить на картинке и какой должен быть указан текст. Ответ должен быть на русском языке и, если надо, то с использованием Markdown: вместо ### оборачивай ту часть сообщения, которую хочешь сделать жирным шрифтом, в ** перед началом предложения и ** в конце"
            seo_query = f"Текст популярного видео: {subtitles[:25000]}. на основании представленного выше текста из видео сделай следующие шаги :: Создай seo оптимизацию для видео на YouTube по заданию ниже: Придумай название для видеоролика на YouTube. Количество слов в названии от 3 до 10. Предложи мне 5 идей :: Придумай описание к видео на ютюбу :: Описание должно состоять из 3 абзацев, первый должен отражать содержание и содержать ключевые слова для выдачи в поиске. Количество предложений от 10 до 15. Второй рассказывает про ролик и так же содержит ключевые слова для seo, количество предложений от 12 до 15. В третьем абзаце должно рассказывать о канале, количество предложений от 14 до 18. В конце описания должно быть 5 хэштегов по теме видео, каждый хэштег - 1 слово. В. Четвертом абзаце к описанию укажи ссылки на мои социальные сети Инстаграм - Телеграмq :: Также придумай на основе информации выше 10 идей концепции для превью картинок на видео на YouTube, какое должно быть фото на фоне, какого цвета фон, какие элементы расположить на картинке и какой должен быть указан текст. Ответ должен быть на русском языке и, если надо, то с использованием Markdown: вместо ### оборачивай ту часть сообщения, которую хочешь сделать жирным шрифтом, в ** перед началом предложения и ** в конце"

            seo_response, seo_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=seo_query)

            tags_query = f"Хорошо, теперь напиши к этому же видео теги. Важно учесть следующие правила: Теги - это те запросы, которые часто делают люди в интернете, которым может быть интересно это видео, поэтому нам нужно учитывать, как содержание видео, так и потенциальные интересы аудитории. Люди не гуглят «бизнес идеи», они обычно гуглят «как заработать денег», здесь же нам надо использовать этот принцип. То есть представь, что ты человек, у него есть проблем, ты делаешь запросы в интернете, и твоя задача - через них найти вот такое видео. Поэтому в тегах может содержаться как 1 слово, отражающее тему видео, так и серия из 2,3,4 слов. Тегов должно быть около 50 штук, присылай их в столбик без лишних комментариев, как минимум 10 штук из них должны являться запросами людей и начинаться со слова как."

            tags_response, tags_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=tags_query)

            keyboard = [
                [InlineKeyboardButton("Посмотреть функции", callback_data='view_features')],
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=str(seo_response),
                    parse_mode='Markdown'
                )

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Теги, которые можешь использовать:\n\n{str(tags_response)}",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            except Exception as e:
                # Если возникла ошибка, проверяем её тип
                if "can't parse entities" in str(e):
                    # Если ошибка связана с невозможностью разбора сущностей, повторяем без Markdown
                    try:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=str(seo_response),
                        )
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"Здесь я предложу теги для тебя:\n\n{str(tags_response)}",
                            reply_markup=reply_markup,
                        )
                    except Exception as e:
                        # Обработка других потенциальных ошибок при повторной попытке
                        print("Ошибка при отправке сообщения без Markdown: ", str(e))
                        await context.bot.send_message(chat_id=chat_id, text=str(e))
                else:
                    # Логирование или обработка других типов ошибок
                    print("Ошибка при отправке сообщения: ", str(e))
                    await context.bot.send_message(chat_id=chat_id, text=str(e))
        except ValueError as e:
            print(e)
            await context.bot.send_message(chat_id=chat_id, text=str(e))
            self.user_states[update.message.from_user.id] = "awaiting_correct_url"

    async def video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if user and user.channel_description:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Давай посмотрим", callback_data='generate_video_ideas')],
                    [InlineKeyboardButton("Создать новое видео", callback_data='create_new_video')]
                ])

                await context.bot.send_message(chat_id=chat_id,
                                               text="Я вижу, что ты уже загружал описание канала!\n\nУ меня появились мысли о чем можно снять твое первое видео!",
                                               reply_markup=reply_markup)
                return

            await update.message.reply_text(
                "Приступим к созданию видео! Напиши мне в нескольких предложениях о чем хочешь создать ролик и я придумаю тебе сценарий 🎥\n\nНачинай свое сообщение с \"О...\""
            )

            self.user_states[update.effective_chat.id] = 'create_new_video_handler'

    async def generate_video_ideas(self, update: Update, context: CallbackContext):
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        user_id = update.callback_query.from_user.id
        with Session() as session:
            await update.callback_query.message.reply_text(
                "Отлично! Скоро вернусь со сценарием!"
            )
            feature = "video"
            if not await self.check_and_handle_subscription_status(update, context, feature):
                return
            user = session.query(User).filter(User.id == user_id).first()
            video_query = f"Распиши сценарий видео на 5-10 минут по теме {user.channel_description} :: указав место съемки, подробную раскадровку с числом секунд, внешний вид автора :: Напиши полный текст, по каждому промежутку раскадровки, который произнесет автор, с завершением ролика призывом к действию :: А после укажи рекомендации, на что обратить внимание при съемке. Ответ должен быть на Русском языке. При создания сценария можно использовать один из методов по списку ниже: 1. Метод «скользкой горки» 2. Техника «шевеления занавеса» 3. Техника «Ложных следов» 4. Метод «Внутренний конфликт» 5. Техника «Крючок»"
            video_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id,
                                                                                      query=video_query)

            keyboard = [
                [InlineKeyboardButton("Создать еще видео", callback_data='create_new_video')],
                [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=str(video_response),
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

    # TODO: поощрать пользователей
    async def create_new_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.message.reply_text(
            "Приступим к созданию видео! Напиши мне в нескольких предложениях о чем хочешь создать ролик и я придумаю тебе сценарий 🎥\n\nНачинай свое сообщение с \"О...\""
        )

        self.user_states[update.effective_chat.id] = 'create_new_video_handler'

    async def create_new_video_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        feature = "video"
        if not await self.check_and_handle_subscription_status(update, context, feature):
            return
        await update.message.reply_text(
            "Отлично! Ушла писать сценарий! 😇"
        )
        video_query = f"Распиши сценарий видео на 5-10 минут по теме {user_input} :: указав место съемки, подробную раскадровку с числом секунд, внешний вид автора :: Напиши полный текст, по каждому промежутку раскадровки, который произнесет автор, с завершением ролика призывом к действию :: А после укажи рекомендации, на что обратить внимание при съемке. Ответ должен быть на Русском языке. При создания сценария можно использовать один из методов по списку ниже: 1. Метод «скользкой горки» 2. Техника «шевеления занавеса» 3. Техника «Ложных следов» 4. Метод «Внутренний конфликт» 5. Техника «Крючок»"
        video_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=video_query)
        await update.message.reply_text(
            "Вот твой ответ!"
        )
        keyboard = [
            [InlineKeyboardButton("Создать еще видео", callback_data='create_new_video')],
            [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=str(video_response),
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def input_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        feature = "analytics_attempts"
        if not await self.check_and_handle_subscription_status(update, context, feature):
            return
        await update.callback_query.message.reply_text(
            "Приступим к созданию аналитики! Расскажи о чем твой канал?"
        )

        self.user_states[update.effective_chat.id] = 'input_analytics_channel_description_handler'

    async def input_analytics_channel_audience(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Расскажи какая аудитория у твоего канала?"
        )

        self.user_states[update.effective_chat.id] = 'input_analytics_channel_audience_handler'

    async def input_analytics_channel_goals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Какие 3 ключевых цели канала?"
        )

        self.user_states[update.effective_chat.id] = 'input_analytics_channel_goals_handler'

    async def input_analytics_last_step(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        keyboard = [
            [InlineKeyboardButton("Все верно", callback_data='input_generate_analytics')],
            [InlineKeyboardButton("Изменить переменные", callback_data='input_analytics')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Правильно ли я поняла, что ваш задачу можно описать так:\n\n"
                     f"{user.analytics_channel_description}\n\n"
                     f"Аудитория: {user.analytics_channel_audience}\n\n"
                     f"Ключевые цели канала: {user.analytics_channel_goals}",
                reply_markup=reply_markup,
            )

    async def send_notification_to_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message):
        print("Тут ошибка")
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id

        for admin_chat_id in ADMINS_CHAT_ID:
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text=f"Пользователю ({update.effective_chat.id}, {chat_id}) нужен анализ с такими ключевыми словами:\n\n{message}"
            )
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text=f"Создай задачу в Octoparse, скопируй и введи сюда task_id:"
            )
            self.user_states[update.effective_chat.id] = 'admin_input_task_id'

    async def send_excel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("Назад", callback_data='admin')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Введите chat_id пользователя, которому собираетесь отправить файл",
            reply_markup=reply_markup,
        )

        self.user_states[update.effective_chat.id] = 'admin_send_excel_user'

    async def send_excel_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Загрузите файл, который необходимо отправить",
        )
        self.user_states[update.effective_chat.id] = 'admin_send_excel_file'

    async def send_excel_file_to_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        print('zashli')
        user_context = await self.get_user_context(chat_id)
        print('тут', update.message)
        if 'document' in update.message:
            # Получаем объект файла
            excel_file = update.message.document

            # Получаем информацию о файле
            file_name = excel_file.file_name
            file_id = excel_file.file_id

            print(user_context.admin_chat_id_of_user_for_send_file)

            await context.bot.send_document(chat_id=user_context.admin_chat_id_of_user_for_send_file,
                                            document=excel_file)

            # Скачиваем файл
            # file_path = os.path.join('excel_files', file_name)
            # await context.bot.get_file(file_id).download(file_path)

            chat = await context.bot.get_chat(chat_id)

            # Отвечаем пользователю о успешном сохранении файла
            await update.message.reply_text(f"Файл '{file_name}' успешно отправлен пользователю {chat.id}.")
        else:
            # Если файл не был прикреплен, отвечаем пользователю об ошибке
            await update.message.reply_text("Пожалуйста, пришлите файл Excel.")

    async def send_message_to_all_users_get_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Введите сообщение, которое собираетесь отправить всем пользователям",
        )

        self.user_states[update.effective_chat.id] = 'admin_send_message_to_all_users'

    async def send_message_to_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text):
        try:
            # Получаем количество участников текущего чата
            chat_id = update.effective_chat.id
            # chat_members_count = await context.bot.get_chat_members_count(chat_id)

            # Получаем список всех пользователей из базы данных
            with Session() as session:
                all_users = session.query(User).all()

            # Проходимся по каждому пользователю и отправляем сообщение
            print(all_users)
            for user in all_users:
                await context.bot.send_message(chat_id=user.id, text=text)

            # Проходимся по каждому участнику и отправляем сообщение

            # for offset in users_id:
            #     member = await context.bot.get_chat_member(chat_id, offset)
            #     print(member.user.id)
            # print(str(await context.bot.get_updates()))
            # print(list(await context.bot.get_updates()))
            # all_chat_ids = []
            # async for update in context.bot.get_updates():
            #     all_chat_ids.append(update.message.chat_id)
            # print(all_chat_ids)
            # all_chat_ids = [update.message.chat_id for update in bot.get_updates()]

            # all_chat_members = context.bot.get_chat_members_count(update.effective_chat.id)
            # async for update in context.bot.get_updates():
            #     all_chat_ids.append(update.effective_chat.id)
            # for member in all_chat_members:
                # Отправляем сообщение в каждый чат
                # print(member.user.id, text)
                # await context.bot.send_message(
                #     chat_id=chat_id,
                #     text=text,
                # )
        except TelegramError as e:
            logger.error(f"An error occurred while sending messages: {e}")
            print(e)

    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        print(update.message.chat_id)
        keyboard = [
            [InlineKeyboardButton("Отправить excel файл пользователю", callback_data='send_excel')],
            [InlineKeyboardButton("Загрузить id таски Octoparse и отправить пользователю", callback_data='input_analytics')],
            [InlineKeyboardButton("Посмотреть данные о пользователе", callback_data='input_analytics')],
            [InlineKeyboardButton("Получить ID пользователя", callback_data='input_analytics')],
            [InlineKeyboardButton("Получить CHAT_ID пользователя", callback_data='input_analytics')],
            [InlineKeyboardButton("Отправить сообщение всем пользователям", callback_data='send_message_to_all_users')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Админ панель. Если загрузить id таски Octoparse, файл вышлется сразу после того как выполнится задача",
            reply_markup=reply_markup,
        )

    async def test_send_notification_to_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Введи через запятую таску и чат id пользователя."
        )
        # print(update.effective_chat.id)
        # await context.bot.send_message(
        #     chat_id=update.effective_chat.id,
        #     text=f"Спасибо Кирюха или Мишаня! Получил ваш chat_id, теперь добавлю в админку)))"
        # )
        # for admin_chat_id in ADMINS_CHAT_ID:
            # await context.bot.send_message(
            #     chat_id=admin_chat_id,
            #     text=f"Пользователю нужен анализ с такими ключевыми словами:"
            # )
            # await context.bot.send_message(
            #     chat_id=admin_chat_id,
            #     text=f"Создай задачу в Octoparse, скопируй и введи сюда task_id, а через запятую chat_id клиента"
            # )
        self.user_states[update.effective_chat.id] = 'admin_input_task_id_test'

    async def input_generate_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        user_id = update.callback_query.from_user.id
        user_context = await self.get_user_context(chat_id)

        print(chat_id, user_id)

        keyboard = [
            [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Отлично, вся информация у меня уже есть, процесс аналитики занимает несколько часов. \n\nУже скоро я вернусь и вы получите 👇🏻\nТаблицу в формате «xslx» с результатами, не выключайте уведомления 🚀\n\nМожешь пока ознакомиться с другими возможностями бота",
            reply_markup=reply_markup,
        )

        with Session() as session:
            # feature = "analytics_attempts"
            # if not await self.check_and_handle_subscription_status(update, context, feature):
            #     return
            user = session.query(User).filter(User.id == user_id).first()
            analytics_words_1_query = f"Cейчас я отправлю тебе определённый набор информации о моем канале на YouTube и по нему нам нужно будет выполнить серию заданий. {user.analytics_channel_description}. {user.analytics_channel_audience}. {user.analytics_channel_goals} Для начала - мне важно понять к каким категориям контента можно вообще отнести мой канал  - какие это ниши? какие есть конкуренты в этой теме? с кем мне нужно будет конкурировать за просмотры."
            analytics_words_1_query_response, analytics_words_1_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_1_query)

            analytics_words_2_query = f"{analytics_words_1_query_response}. На основе того что мы разработали выше - помоги мне составить портрет целевого зрителя - я и его интересы - какие у него потребности и желания что ему хочется иметь в своей жизни какой контент он любит смотреть и вообще как нам сделать так чтобы зрители смотрели наши видео а не видео конкурентов"
            analytics_words_2_query_response, analytics_words_2_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_2_query)

            analytics_words_3_query = f"{analytics_words_2_query_response}. Хорошо теперь на основе всех данных мне необходимо подготовить 100 ключевых слов которые могут содержаться в названиях видео конкурентов - важно чтобы это были не просто слова по тематике а конкретные слова которые есть в названиях тех видео которое может интересно описанному выше целевому зрителю"
            analytics_words_3_query_response, analytics_words_3_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_3_query)

            analytics_words_4_query = f"{analytics_words_3_query_response}. Теперь давай выберем из них 15 самых приоритетных и лучших - я буду загружать эти слова в парсер - поэтому каждый пункт это только 1 слово и важно чтобы оно было максимально простым и отражало суть чтобы собрать лучшие видео со всего ютюба и переведи эти слова на английский - в итоге в списке должно получить 30 слов (15 рус и 15 англ). Напиши только список слов, каждое слово с новой строки, без воды, только список из 30 слов."
            analytics_words_4_query_response, analytics_words_4_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_4_query)

            user_context.save_analytics_words(user_id, analytics_words_4_query_response)

            await self.send_notification_to_admin(update, context, analytics_words_4_query_response)

            # await asyncio.sleep(5)
            #
            # await update.effective_message.chat.send_action(constants.ChatAction.UPLOAD_DOCUMENT)
            #
            # file_path = 'Аналитика.xlsx'
            # # os.path.basename(file_path)
            #
            # with open(file_path, 'rb') as file:
            #     await context.bot.send_document(chat_id=update.effective_chat.id, document=file,
            #                                     filename='Аналитика.xlsx',
            #                                     caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
            #                                             "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
            #                                             "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
            #                                             "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
            #                                             "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")
            #
            # print("аналитика пошла")

    async def input_links(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        feature = "analytics_attempts"
        if not await self.check_and_handle_subscription_status(update, context, feature):
            return
        await update.callback_query.message.reply_text(
            "Приступим к созданию аналитики! Предоставь мне ссылки на существующие ролики (до 5 шт.). Каждая ссылка должна быть с новой строки"
        )

        self.user_states[update.effective_chat.id] = 'input_links_handler'

    async def input_links_change_text_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.message.reply_text(
            "Пришли исправленную версию текста"
        )

        self.user_states[update.effective_chat.id] = 'input_links_change_text_correct'

    async def input_links_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, links):
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        user_id = update.message.from_user.id
        user_context = await self.get_user_context(chat_id)

        # try:
        links = links.split('\n')
        print("AAALLLOOO")
        print(links)

        try:
            video_ids = []
            for link in links:
                video_ids.append(self.check_link(link))
            if len(video_ids) > 5:
                await context.bot.send_message(chat_id=chat_id,
                                               text=f"Можно отправить только 5 видео. Пожалуйста, повтори попытку")
                self.user_states[update.message.from_user.id] = "input_links_handler"
                return
            user_context.save_analytics_links(user_id, links)

            all_subtitles = []

            all_generates_by_subtitles = []

            print(video_ids)

            print("wtf")

            try:
                await update.message.reply_text(
                    "Отлично! Ушла разрабатывать аналитику! 😇"
                )
                for link in links:
                    subtitles = await self.get_subtitles(link)
                    print(subtitles)
                    all_subtitles.append(subtitles[:25000])

                    subtitles_query = f"У меня есть субтитры к видео - напиши по ним краткое содержание в 3-4 предложения. И ничего более. СУБТИТРЫ: {subtitles[:25000]}"

                    subtitles_response, subtitles_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=subtitles_query)

                    all_generates_by_subtitles.append(subtitles_response)

                subtitles_end_query = f"У меня есть 5 кратких содержаний с ютуб канала. СОДЕРЖАНИЯ: {', СЛЕДУЮЩЕЕ СОДЕРЖАНИЕ: '.join(all_generates_by_subtitles)}. На основе этих данных мне необходимо заполнить 3 вопроса: Первый - Расскажите о чем канал (Вставь ответ содержащий 7 предложений начиная с «канал о…». Второй - Расскажите о своей аудитории (Вставь ответ содержащий информацию об аудитории такого канала - ее интересах и потребностях в 7 предложений). Выбери 3 категории из 6-и возможных - это категории «задачи канал» то есть то что важно для автора на основе этой информации. Категории следующие: Набор подписчиков, Повышение узнаваемости, Информирование людей, Получение клиентов, Личная реализация. Представь ответ в формате 3 пунктов по заданию выше. В выдаче должны быть только ответы, три абзаца."

                subtitles_end_query_response, subtitles_end_query_total_tokens = await self.openai.get_chat_response(chat_id=chat_id,
                                                                                                 query=subtitles_end_query)

                user_context.save_analytics_channel_characteristics(user_id, subtitles_end_query_response)

                keyboard = [
                    [InlineKeyboardButton("Изменить текст", callback_data='input_links_change_text')],
                    [InlineKeyboardButton("Начать генерацию аналитики", callback_data='input_links_generate_analytics')]
                ]

                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Снизу дана характеристика твоего канала. Убедить, что она исправная. \n\n{subtitles_end_query_response}",
                    reply_markup=reply_markup,
                )
            except ValueError as e:
                print(e)
                await context.bot.send_message(chat_id=chat_id, text=str(e))
                self.user_states[update.message.from_user.id] = "input_links_handler"

            # keyboard = [
            #     [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
            # ]
            # reply_markup = InlineKeyboardMarkup(keyboard)
            # await context.bot.send_message(
            #     chat_id=update.effective_chat.id,
            #     text="Отлично, вся информация у меня уже есть, процесс аналитики занимает несколько часов. \n\nУже скоро я вернусь и вы получите 👇🏻\nТаблицу в формате «xslx» с результатами, не выключайте уведомления 🚀\n\nМожешь пока ознакомиться с другими возможностями бота",
            #     reply_markup=reply_markup,
            # )
            #
            # print('wtf2')
            #
            # await asyncio.sleep(5)
            #
            # await update.effective_message.chat.send_action(constants.ChatAction.UPLOAD_DOCUMENT)
            #
            # file_path = 'Аналитика.xlsx'
            # # os.path.basename(file_path)
            #
            # with open(file_path, 'rb') as file:
            #     await context.bot.send_document(chat_id=update.effective_chat.id, document=file,
            #                                     filename='Аналитика.xlsx',
            #                                     caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
            #                                             "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
            #                                             "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
            #                                             "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
            #                                             "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")

        except ValueError as e:
            await context.bot.send_message(chat_id=chat_id,
                                           text=f"Кажется, чтот-то пошло не так, попробуй еще раз. Предоставь мне ссылки на существующие ролики (до 5 шт.). Каждая ссылка должна быть с новой строки\n\n{e}")
            self.user_states[update.message.from_user.id] = "input_links_handler"

    async def input_links_generate_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.callback_query.from_user.id
        chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        print("Chat id:", chat_id)
        user_context = await self.get_user_context(chat_id)
        with Session() as session:
            feature = "analytics_attempts"
            if not await self.check_and_handle_subscription_status(update, context, feature):
                return
            user = session.query(User).filter(User.id == user_id).first()

            keyboard = [
                [InlineKeyboardButton("Вернуться в меню", callback_data='view_features')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Отлично, вся информация у меня уже есть, процесс аналитики занимает несколько часов. \n\nУже скоро я вернусь и вы получите 👇🏻\nТаблицу в формате «xslx» с результатами, не выключайте уведомления 🚀\n\nМожешь пока ознакомиться с другими возможностями бота",
                reply_markup=reply_markup,
            )

            print("Chat id:", chat_id)

            analytics_words_1_query = f"Cейчас я отправлю тебе определённый набор информации о моем канале на YouTube и по нему нам нужно будет выполнить серию заданий. {user.analytics_channel_characteristics} Для начала - мне важно понять к каким категориям контента можно вообще отнести мой канал  - какие это ниши? какие есть конкуренты в этой теме? с кем мне нужно будет конкурировать за просмотры."
            analytics_words_1_query_response, analytics_words_1_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_1_query)

            analytics_words_2_query = f"{analytics_words_1_query_response}. На основе того что мы разработали выше - помоги мне составить портрет целевого зрителя - я и его интересы - какие у него потребности и желания что ему хочется иметь в своей жизни какой контент он любит смотреть и вообще как нам сделать так чтобы зрители смотрели наши видео а не видео конкурентов"
            analytics_words_2_query_response, analytics_words_2_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_2_query)

            analytics_words_3_query = f"{analytics_words_2_query_response}. Хорошо теперь на основе всех данных мне необходимо подготовить 100 ключевых слов которые могут содержаться в названиях видео конкурентов - важно чтобы это были не просто слова по тематике а конкретные слова которые есть в названиях тех видео которое может интересно описанному выше целевому зрителю"
            analytics_words_3_query_response, analytics_words_3_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_3_query)

            analytics_words_4_query = f"{analytics_words_3_query_response}. Теперь давай выберем из них 15 самых приоритетных и лучших - я буду загружать эти слова в парсер - поэтому каждый пункт это только 1 слово и важно чтобы оно было максимально простым и отражало суть чтобы собрать лучшие видео со всего ютюба и переведи эти слова на английский - в итоге в списке должно получить 30 слов (15 рус и 15 англ). Напиши только список слов, каждое слово с новой строки, без воды, только список из 30 слов."
            analytics_words_4_query_response, analytics_words_4_query_total_tokens = await self.openai.get_chat_response(
                chat_id=chat_id, query=analytics_words_4_query)

            print("Chat id:", chat_id)

            user_context.save_analytics_words(user_id, analytics_words_4_query_response)

            await self.send_notification_to_admin(update, context, analytics_words_4_query_response)

    async def monitor_task_and_get_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
        octoparse = Octoparse()
        print("пошел мониторинг")
        while True:
            status = octoparse.is_task_running(task_id=task_id)

            # если задача выполнена, выходим из цикла
            if not status:
                break

            # ожидаем некоторое время перед повторной проверкой
            print("прошло 30 секунд")
            await asyncio.sleep(30)  # например, каждые 30 секунд

        # если status стал False, продолжаем выполнение кода
        print('получение данных')
        data = octoparse.get_task_data(task_id=task_id)

        cleaned_data = []
        for item in data:
            item["Video_Title"] = item["Video_Title"].strip()
            cleaned_data.append(item)

        # print(data)
        print("данные получены")
        try:

            # Открыть файл и загрузить данные
            print("Открыть файл и загрузить данные")

            json_data = json.dumps(cleaned_data, ensure_ascii=False)

            with open(f'analytics_data/data_{task_id}.json', 'w', encoding='utf-8') as f:
                f.write(json_data)
                print("Данные успешно сохранены в JSON файл")
                print("данные отправились в парсер")
                result_output_file_path = await parser(f'analytics_data/data_{task_id}.json')
                print("данные вернулись из парсера и отправляются пользователю")
                await update.effective_message.chat.send_action(constants.ChatAction.UPLOAD_DOCUMENT)

                # os.path.basename(file_path)
                print(result_output_file_path, "ВОТ ЗДЕСЬ ПРОБЛЕМА?")
                with open(result_output_file_path, 'rb') as file:
                    await context.bot.send_document(chat_id=update.effective_chat.id, document=file,
                                                    filename=f'{result_output_file_path}',
                                                    caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
                                                            "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
                                                            "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
                                                            "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
                                                            "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")

                for chat_admin_id in ADMINS_CHAT_ID:
                    with open(result_output_file_path, 'rb') as file:
                        await context.bot.send_document(chat_id=chat_admin_id, document=file,
                                                        filename=f'{result_output_file_path}',
                                                        caption="Я провела аналитику, ниже отправила тебе файл 🙏🏻\n\nКак им пользоваться? \n\nВ этой таблице ролики твоих конкурентов, отсортированные из объема в 7-10 тысяч, по определенным критериям таким как просмотры, "
                                                                "дата публикации и т.д.\n\nВсего есть 3 параметра - лучшие видео за последнюю неделю, месяц и год \n\nТы можешь изучить этот контент и снять видео на похожие темы, либо даже полностью повторить их, все они — трендовые, "
                                                                "ибо собрали большие просмотры за короткий промежуток времени\n\nПроще говоря, из 7000 видео, которые уже сняли твои конкуренты, я выбрала 100-200 штук, уверена, больше 30 из них подойдут, чтобы твой канал начал активно "
                                                                "развиваться 📽\n\nДля анализа я взяла не только русских авторов, но и тех, кто создает видео на Английском языке\n\n*В таблице могут попадаться лишние темы, пока пропусти их, я активно работаю над этим🌟*\n\nЕсли у тебя "
                                                                "есть вопросы по самому YouTube и ты хочешь получить максимум эффекта, напиши моему создателю @fabricbothelper")
        except Exception as e:
            print(f"Ошибка при сохранении данных в JSON файл: {e}")

    async def support(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        keyboard = [
            [InlineKeyboardButton("Написать", url='https://t.me/fabricbothelper')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Для того, чтобы связаться с поддержкой, нажмите по кнопке ниже 🎥",
            reply_markup=reply_markup
        )

    async def faq(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''
        chat_id = update.effective_chat.id
        prompt = """
            Привет, этот контекст будет интегрирован в чат-бота и ты будешь выполнять роль консультанта, то есть являешься помощником по раздел FAQ 
            Ниже информация о проекте, которую тебе нужно знать, чтобы отвечать на вопросы клиента 
            Бот представляет собой сервис, помогающий в создании контента на YouTube, он полезен авторам каналов, агентствам, продюсерам и ассистентам
            Она умеет анализировать видео конкурентов, помогает с сео оптимизацией роликов и дает идеи для сценариев 
            Основные кнопки (функции) в боте следубщие👇🏻
            /info - здесь хранится информация о сервисе, тарифах, особенностях работы и нужные инструкции 
            /menu - это кнопка, вызывающая главное меню, в котором можно активировать различные функции 
            /account - эта кнопка вызывает меню личного кабинета, где вы можете найти информацию о подписке и статусе аккаунта 
            /analytics - это функция, вызывающая возможность получить общую аналитику по видеороликам в вашей нише среди других авторов 
            Аналитика конкурентов - это функция сбора данных об успешных видео, которые уже есть на YouTube в размере 100-500 видео за последнюю неделю, месяц и год  
            Это, по сути, поиск идей для вашего канала - но основанный не на воображении, а на статистике и цифрах 
            Как это работает? 
            Вы рассказываете нам о своем канале — мы анализируем ваши данные, формируем представление о том, что представляет собой ваш проект, формируем портреты целевых зрителей, и получаем информацию о том, кто УЖЕ сейчас ваши конкуренты среди авторов контента и какие видео от них успешны.
            И собираем базу данных о выложенных видео из открытого доступа в 7-10-15 тысяч видео, а дальше по результатам и статистике этих видео фильтруем их до стадии, когда остаются лишь те идеи и ролики, которые уже «зашли», которые нравится людям 👍🏻
            В итоге вы получаете таблицу в формате xslx, в которой можете отследить лучшие ролики конкурентов, которые подходят и вашему проекту / интересны вашим зрителям за неделю, месяц и год. Обычно их около 100-500 штук 🚀
            Для чего это? Такая функция позволяет вам не исследовать весь ютюб и это очень важный шаг, чтобы изначально выбрать правильные идеи( ведь если чужие ролики набрали всего 10-20 тысяч просмотров по всему ютюбу, то на вряд ли видео от вас в той же теме соберет больше 30, даже если оно будет максимально качественным 🤝
            Успех видео зависит не столько от картинки, качеств, сколько от самой идеи видео - если люди его смотрят, значит, ютюб его продвигает. А значит, если мы Создадим видео изначально в той теме, которую люди смотрят - просмотры будут 📽️
            Вам необходимо будет лишь проанализировать этот список и выбрать, какие ролики вы можете повторить и адаптировать под себя. 
            Для получения аналитики пользователю нужно выбрать 2 пути - либо отправить информацию о канале и рассказать подробно про проект, либо отправить ссылки на 5 видео, наиболее полно отражающие тематику канала 
            Важно быть внимательным в этом процессе, чтобы получить максимально качес
            Аналитика занимает от 2 до 4 часов, поскольку обрабатывается большой объем информации и отправляется с 10:00 до 22:00. Если в течение 12 часов вы не получили ответ, обратитесь в службу поддержку  
            /naming - это функция помогает придумать название и описание к каналу, предлагает список идей  
            /video - здесь вы можете создать сценарий горизонтального видеоролика, по введенным ранее данным, либо по вашему техническому заданию 
            /shorts - здесь вы можете создать сценарий горизонтального видеоролика, по введенным ранее данным, либо по вашему техническому заданию 
            /seo - функция, которая помогает прописать название, описание и теги к видео, в этой функции работает анализ частоты употребления слов в видеоролике 
            Важно - для получения этой информации необходимо отправить ссылку на само видео. Вы можете загрузить его на YouTube с доступом по ссылке и отправить боту 
            /referral - здесь хранится информация о реферальной системе. Реферальная система работает следующим образом - у каждого пользователя есть персональная ссылка, которая фиксирует количество оплат по вашей ссылке и накапливает 20% от их суммы на ваш баланс. Эти баллы начисляются вам на баланс, и вы можете использовать их для активации подписки, для этого свяжитесь с поддержкой @fabricbothelper 
            /support - здесь можно связаться с поддержкой напрямую  
            /faq - здесь вы можете задать вопрос о сервисе, и сразу получить ответ 
            /restart - функция, иногда перезапуск бота решает техническую проблему 
            Условия и тарифы 
            В боте есть демо-доступ, который позволяет 2 раза бесплатно использовать каждую функцию кроме аналитики 
            Всего в боте есть 5 тарифов 
            Тариф на 1 день - полный функционал бота на 1 день 
            Тариф на 7 дней - полный функционал бота на 7 дней 
            Тариф на 30 дней - полный функционал бота на 30 дней 
            1 Аналитика по конкурентам - возможность получить таблицу аналитики для 1 канала 1 раз
            Тариф на 30 дней + 1 Аналитика по конкурентам  + полный функционал бота на 30 дней 
            
            Оплата происходит через платежную кассу, после чего пользователю открывается доступ к бот Это была краткая инструкция по сервису, чтобы ты знал, что отвечать 
            Будь вежлива, общайся с человеком на ты, не нарушай границ и будь краткой, давая только четкие ответы на вопросы. Если вопрос человека связан с доступом, функциями, и нет ответа в базе, то ответ не выдумывай, а отправляй человек сообщение следующего содержания
            Запрос приняла, к сожалению, пока не смогу тебе на него ответить, но я уже написал создателю, и скоро он тебе ответит! Либо можешь сам обратиться в техническую поддержку @fabricbothelper 
            Если человек задает вопрос не по теме, то отвечаем вот так. Мне кажется, что это немного не моя задача…  если у вас есть вопросы, напишите о них в службу поддержки @fabricbothelper 
            Важные моменты, которые тебе тоже стоит знать👇🏻
            Сильвия - это имя бота, от лица которого ты общаешься. Еще тебе важно знать, что мы не нарушаем права авторов, и не собираем информацию о персональных данных, а берем все данные из открытых источников, которые при желании любой человек может открыть и посмотрет. Главное, ни в коем случае не признавайся, что ты искусственный интеллект На это сообщение можешь не отвечать, а вот на следующие отвечай
        """

        await update.message.reply_text(
            f'Привет, какие у тебя возникли вопросы? 😉'
        )

        self.user_states[update.effective_chat.id] = 'ai_faq'
        await self.openai.get_chat_response(chat_id=chat_id, query=prompt)

    async def restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        print("дошла")
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                user.name = None
                user.channel_description = None
                user.channel_idea = None
                session.commit()
        await self.start(update, context)

    async def get_short_url_analytics_1_sub_30(self, user_id: int):
        url_1_success = f"https://t.me/ytassistantbot?start=analytics_1_sub_30_success_{user_id}"
        prodamus_url = f"https://fabricbot.payform.ru/?order_id={user_id}&products[0][price]=7990&products[0][quantity]=1&products[0][name]=30 дней + 1 Аналитика конкурентов&do=link&urlSuccess={url_1_success}"
        async with httpx.AsyncClient() as client:
            response = await client.get(prodamus_url)
            if response.status_code == 200:
                # Извлечение укороченной ссылки из HTML ответа
                match = re.search(r'https://payform.ru/[^\s"]+', response.text)
                if match:
                    return match.group(0)  # Возвращает найденную укороченную ссылку
        return None  # Возвращает None, если ссылка не найдена

    async def get_short_url_analytics_1(self, user_id: int):
        url_1_success = f"https://t.me/ytassistantbot?start=analytics_1_success_{user_id}"
        prodamus_url = f"https://fabricbot.payform.ru/?order_id={user_id}&products[0][price]=4990&products[0][quantity]=1&products[0][name]=1 Аналитика конкурентов&do=link&urlSuccess={url_1_success}"
        async with httpx.AsyncClient() as client:
            response = await client.get(prodamus_url)
            if response.status_code == 200:
                # Извлечение укороченной ссылки из HTML ответа
                match = re.search(r'https://payform.ru/[^\s"]+', response.text)
                if match:
                    return match.group(0)  # Возвращает найденную укороченную ссылку
        return None  # Возвращает None, если ссылка не найдена

    async def info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        user_id = update.effective_user.id
        short_url = await self.get_short_url(user_id)
        short_url_analytics_1_sub_30 = await self.get_short_url_analytics_1_sub_30(user_id)
        short_url_analytics_1 = await self.get_short_url_analytics_1(user_id)
        subscription_7_id = 1779399
        subscription_30_id = 1779400

        url_7_success = f"https://t.me/ytassistantbot?start=subscription_paid_7_days_{user_id}"
        url_30_success = f"https://t.me/ytassistantbot?start=subscription_paid_30_days_{user_id}"

        keyboard_demo = [
            [InlineKeyboardButton("1 день - 290 рублей", url=short_url)],
            [InlineKeyboardButton("7 дней - 1490 рублей",
                                  url=f'https://fabricbot.payform.ru/?order_id={user_id}&subscription={subscription_7_id}&do=pay&urlSuccess={url_7_success}')],
            [InlineKeyboardButton("30 дней - 4990 рублей",
                                  url=f'https://fabricbot.payform.ru/?order_id={user_id}&subscription={subscription_30_id}&do=pay&urlSuccess={url_30_success}')],
            [InlineKeyboardButton("30 дней + 1 аналитика конкурентов - 7990 рублей", url=short_url_analytics_1_sub_30)],
            [InlineKeyboardButton("1 Аналитика конкурентов - 4990 рублей", url=short_url_analytics_1)]
        ]
        keyboard = [
            [InlineKeyboardButton("Меню", callback_data='view_features')],
            [InlineKeyboardButton("Личный кабинет", callback_data='account')],
        ]
        reply_markup_demo = InlineKeyboardMarkup(keyboard_demo)
        reply_markup = InlineKeyboardMarkup(keyboard)

        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                subscription = session.query(Subscription).filter(Subscription.user_id == user_id).first()
                if subscription:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"Привет, я твой карманный YouTube продюсер 👋🏻  \n\n"
                             f"Создатель назвал меня Сильвия, но для тебя я буду ассистентом по старту твоего канала на YouTube 🎥 \n\n"
                             f"Я существую, чтобы ты сэкономил сотни тысяч рублей на найме команды или на дорогом продакшне и начал получать первые просмотры уже сегодня вечером❤\n\n"
                             f"Я придумаю за тебя сценарии и даже пропишу теги к видео, тебе останется лишь снять и выложить ролик 😻\n\n"
                             f"Поздравляю! У тебя уже подключен тариф: {subscription.tariff} \n\n"
                             f"Можешь полноценно пользоваться функциями и развивать свой YouTube канал 😉",
                        reply_markup=reply_markup
                    )
                    return

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Привет, я твой карманный YouTube продюсер 👋🏻  \n\n"
                 f"Создатель назвал меня Сильвия, но для тебя я буду ассистентом по старту твоего канала на YouTube 🎥 \n\n"
                 f"Я существую, чтобы ты сэкономил сотни тысяч рублей на найме команды или на дорогом продакшне и начал получать первые просмотры уже сегодня вечером❤\n\n"
                 f"Я придумаю за тебя сценарии и даже пропишу теги к видео, тебе останется лишь снять и выложить ролик 😻\n\n"
                 f"По умолчанию ты можешь воспользоваться любыми функциями 2 раза без оплаты\n\n"
                 f"Чтобы пользоваться ботом без ограничений, необходимо оформить подписку\n\n"
                 f"Выбери желаемый тариф 👇🏻\n\n",
            reply_markup=reply_markup_demo
        )

    async def account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        user_id = update.message.from_user.id if update.message else update.effective_user.id
        # chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
        name = "Аноним"
        tariff_info = "демо"
        keyboard = [
            [InlineKeyboardButton("Наши другие сервисы", url="https://fabricbot.ru")]
        ]
        keyboard_demo = [
            [InlineKeyboardButton("Узнать о тарифных планах", callback_data='info')],
            # [InlineKeyboardButton("Изменить никнейм", callback_data='change_name')],
            [InlineKeyboardButton("Наши другие сервисы", url="https://fabricbot.ru")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        reply_markup_demo = InlineKeyboardMarkup(keyboard_demo)
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                name = user.name or name
                subscription = session.query(Subscription).filter(Subscription.user_id == user_id).first()
                if subscription:
                    tariff_info = subscription.tariff
                    # Дата окончания подписки в формате YYYY-MM-DD
                    expiration_date = subscription.expiration_date.strftime("%Y-%m-%d")
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"Добро пожаловать, {name}!\n\n"
                             f"Ваш текущий тариф: {tariff_info}\n"
                             f"Дата окончания:  {expiration_date}\n\n",
                        reply_markup=reply_markup
                    )
                    return
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Добро пожаловать, {name}!\n\n"
                     f"Ваш текущий тариф: {tariff_info}\n\n"
                     f"Бесплатные попытки:\n"
                     f"--Упаковка канала: {user.naming_free_uses}\n"
                     f"--Создание сцериев видео: {user.shorts_free_uses}\n"
                     f"--Создание сценариев shorts: {user.video_free_uses}\n"
                     f"--SEO для роликов: {user.seo_free_uses}\n"
                     f"--Аналитика конкурентов: {user.analytics_attempts}\n\n"
                     f"Чтобы пользоваться ботом без ограничений, необходимо оформить подписку 👇🏻",
                reply_markup=reply_markup_demo
            )

    async def referral(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states[update.effective_chat.id] = ''

        user_id = update.effective_user.id

        keyboard = [
            [InlineKeyboardButton("Связаться с поддержкой", url='https://t.me/fabricbothelper')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        referral_link = f'https://t.me/ytassistantbot?start={user_id}'

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"За каждого приглашенного пользователя, который оплатил любую подписку, я буду дарить тебе 20% от ее стоимости, воспользоваться ты ей можешь, оплатив любой тариф при достаточно балансе\n\n"
                 f"Для этого человек должен быть авторизован по вашей реферальной ссылке: {referral_link}\n\n",
            reply_markup=reply_markup
        )

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the help menu.
        """
        commands = self.group_commands if is_group_chat(update) else self.commands
        commands_description = [f'/{command.command} - {command.description}' for command in commands]
        bot_language = self.config['bot_language']
        help_text = (
                localized_text('help_text', bot_language)[0] +
                '\n\n' +
                '\n'.join(commands_description) +
                '\n\n' +
                localized_text('help_text', bot_language)[1] +
                '\n\n' +
                localized_text('help_text', bot_language)[2]
        )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Returns token usage statistics for current day and month.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            f'is not allowed to request their usage statistics')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                     f'requested their usage statistics')

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        tokens_today, tokens_month = self.usage[user_id].get_current_token_usage()
        images_today, images_month = self.usage[user_id].get_current_image_count()
        (transcribe_minutes_today, transcribe_seconds_today, transcribe_minutes_month,
         transcribe_seconds_month) = self.usage[user_id].get_current_transcription_duration()
        vision_today, vision_month = self.usage[user_id].get_current_vision_tokens()
        characters_today, characters_month = self.usage[user_id].get_current_tts_usage()
        current_cost = self.usage[user_id].get_current_cost()

        chat_id = update.effective_chat.id
        chat_messages, chat_token_length = self.openai.get_conversation_stats(chat_id)
        remaining_budget = get_remaining_budget(self.config, self.usage, update)
        bot_language = self.config['bot_language']

        text_current_conversation = (
            f"*{localized_text('stats_conversation', bot_language)[0]}*:\n"
            f"{chat_messages} {localized_text('stats_conversation', bot_language)[1]}\n"
            f"{chat_token_length} {localized_text('stats_conversation', bot_language)[2]}\n"
            f"----------------------------\n"
        )

        # Check if image generation is enabled and, if so, generate the image statistics for today
        text_today_images = ""
        if self.config.get('enable_image_generation', False):
            text_today_images = f"{images_today} {localized_text('stats_images', bot_language)}\n"

        text_today_vision = ""
        if self.config.get('enable_vision', False):
            text_today_vision = f"{vision_today} {localized_text('stats_vision', bot_language)}\n"

        text_today_tts = ""
        if self.config.get('enable_tts_generation', False):
            text_today_tts = f"{characters_today} {localized_text('stats_tts', bot_language)}\n"

        text_today = (
            f"*{localized_text('usage_today', bot_language)}:*\n"
            f"{tokens_today} {localized_text('stats_tokens', bot_language)}\n"
            f"{text_today_images}"  # Include the image statistics for today if applicable
            f"{text_today_vision}"
            f"{text_today_tts}"
            f"{transcribe_minutes_today} {localized_text('stats_transcribe', bot_language)[0]} "
            f"{transcribe_seconds_today} {localized_text('stats_transcribe', bot_language)[1]}\n"
            f"{localized_text('stats_total', bot_language)}{current_cost['cost_today']:.2f}\n"
            f"----------------------------\n"
        )

        text_month_images = ""
        if self.config.get('enable_image_generation', False):
            text_month_images = f"{images_month} {localized_text('stats_images', bot_language)}\n"

        text_month_vision = ""
        if self.config.get('enable_vision', False):
            text_month_vision = f"{vision_month} {localized_text('stats_vision', bot_language)}\n"

        text_month_tts = ""
        if self.config.get('enable_tts_generation', False):
            text_month_tts = f"{characters_month} {localized_text('stats_tts', bot_language)}\n"

        # Check if image generation is enabled and, if so, generate the image statistics for the month
        text_month = (
            f"*{localized_text('usage_month', bot_language)}:*\n"
            f"{tokens_month} {localized_text('stats_tokens', bot_language)}\n"
            f"{text_month_images}"  # Include the image statistics for the month if applicable
            f"{text_month_vision}"
            f"{text_month_tts}"
            f"{transcribe_minutes_month} {localized_text('stats_transcribe', bot_language)[0]} "
            f"{transcribe_seconds_month} {localized_text('stats_transcribe', bot_language)[1]}\n"
            f"{localized_text('stats_total', bot_language)}{current_cost['cost_month']:.2f}"
        )

        # text_budget filled with conditional content
        text_budget = "\n\n"
        budget_period = self.config['budget_period']
        if remaining_budget < float('inf'):
            text_budget += (
                f"{localized_text('stats_budget', bot_language)}"
                f"{localized_text(budget_period, bot_language)}: "
                f"${remaining_budget:.2f}.\n"
            )
        # No longer works as of July 21st 2023, as OpenAI has removed the billing API
        # add OpenAI account information for admin request
        # if is_admin(self.config, user_id):
        #     text_budget += (
        #         f"{localized_text('stats_openai', bot_language)}"
        #         f"{self.openai.get_billing_current_month():.2f}"
        #     )

        usage_text = text_current_conversation + text_today + text_month + text_budget
        await update.message.reply_text(usage_text, parse_mode=constants.ParseMode.MARKDOWN)

    async def resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resend the last request
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name}  (id: {update.message.from_user.id})'
                            f' is not allowed to resend the message')
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        if chat_id not in self.last_message:
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id})'
                            f' does not have anything to resend')
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('resend_failed', self.config['bot_language'])
            )
            return

        # Update message text, clear self.last_message and send the request to prompt
        logging.info(f'Resending the last prompt from user: {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')
        with update.message._unfrozen() as message:
            message.text = self.last_message.pop(chat_id)

        await self.prompt(update=update, context=context)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resets the conversation.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            f'is not allowed to reset the conversation')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'Resetting the conversation for user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})...')

        chat_id = update.effective_chat.id
        reset_content = message_text(update.message)
        self.openai.reset_chat_history(chat_id=chat_id, content=reset_content)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('reset_done', self.config['bot_language'])
        )

    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an image for the given prompt using DALL·E APIs
        """
        if not self.config['enable_image_generation'] \
                or not await self.check_allowed_and_within_budget(update, context):
            return

        image_query = message_text(update.message)
        if image_query == '':
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('image_no_prompt', self.config['bot_language'])
            )
            return

        logging.info(f'New image generation request received from user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')

        async def _generate():
            try:
                image_url, image_size = await self.openai.generate_image(prompt=image_query)
                if self.config['image_receive_mode'] == 'photo':
                    await update.effective_message.reply_photo(
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        photo=image_url
                    )
                elif self.config['image_receive_mode'] == 'document':
                    await update.effective_message.reply_document(
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        document=image_url
                    )
                else:
                    raise Exception(
                        f"env variable IMAGE_RECEIVE_MODE has invalid value {self.config['image_receive_mode']}")
                # add image request to users usage tracker
                user_id = update.message.from_user.id
                self.usage[user_id].add_image_request(image_size, self.config['image_prices'])
                # add guest chat request to guest usage tracker
                if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                    self.usage["guests"].add_image_request(image_size, self.config['image_prices'])

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('image_fail', self.config['bot_language'])}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )

        await wrap_with_indicator(update, context, _generate, constants.ChatAction.UPLOAD_PHOTO)

    async def tts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an speech for the given input using TTS APIs
        """
        if not self.config['enable_tts_generation'] \
                or not await self.check_allowed_and_within_budget(update, context):
            return

        tts_query = message_text(update.message)
        if tts_query == '':
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('tts_no_prompt', self.config['bot_language'])
            )
            return

        logging.info(f'New speech generation request received from user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')

        async def _generate():
            try:
                speech_file, text_length = await self.openai.generate_speech(text=tts_query)

                await update.effective_message.reply_voice(
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    voice=speech_file
                )
                speech_file.close()
                # add image request to users usage tracker
                user_id = update.message.from_user.id
                self.usage[user_id].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])
                # add guest chat request to guest usage tracker
                if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                    self.usage["guests"].add_tts_request(text_length, self.config['tts_model'],
                                                         self.config['tts_prices'])

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('tts_fail', self.config['bot_language'])}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )

        await wrap_with_indicator(update, context, _generate, constants.ChatAction.UPLOAD_VOICE)

    async def transcribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Transcribe audio messages.
        """
        if not self.config['enable_transcription'] or not await self.check_allowed_and_within_budget(update, context):
            return

        if is_group_chat(update) and self.config['ignore_group_transcriptions']:
            logging.info(f'Transcription coming from group chat, ignoring...')
            return

        chat_id = update.effective_chat.id
        filename = update.message.effective_attachment.file_unique_id

        async def _execute():
            filename_mp3 = f'{filename}.mp3'
            bot_language = self.config['bot_language']
            try:
                media_file = await context.bot.get_file(update.message.effective_attachment.file_id)
                await media_file.download_to_drive(filename)
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=(
                        f"{localized_text('media_download_fail', bot_language)[0]}: "
                        f"{str(e)}. {localized_text('media_download_fail', bot_language)[1]}"
                    ),
                    parse_mode=constants.ParseMode.MARKDOWN
                )
                return

            try:
                audio_track = AudioSegment.from_file(filename)
                audio_track.export(filename_mp3, format="mp3")
                logging.info(f'New transcribe request received from user {update.message.from_user.name} '
                             f'(id: {update.message.from_user.id})')

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=localized_text('media_type_fail', bot_language)
                )
                if os.path.exists(filename):
                    os.remove(filename)
                return

            user_id = update.message.from_user.id
            if user_id not in self.usage:
                self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

            try:
                transcript = await self.openai.transcribe(filename_mp3)

                transcription_price = self.config['transcription_price']
                self.usage[user_id].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

                allowed_user_ids = self.config['allowed_user_ids'].split(',')
                if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                    self.usage["guests"].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

                # check if transcript starts with any of the prefixes
                response_to_transcription = any(transcript.lower().startswith(prefix.lower()) if prefix else False
                                                for prefix in self.config['voice_reply_prompts'])

                if self.config['voice_reply_transcript'] and not response_to_transcription:

                    # Split into chunks of 4096 characters (Telegram's message limit)
                    transcript_output = f"_{localized_text('transcript', bot_language)}:_\n\"{transcript}\""
                    chunks = split_into_chunks(transcript_output)

                    for index, transcript_chunk in enumerate(chunks):
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                            text=transcript_chunk,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )
                else:
                    # Get the response of the transcript
                    response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=transcript)

                    self.usage[user_id].add_chat_tokens(total_tokens, self.config['token_price'])
                    if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                        self.usage["guests"].add_chat_tokens(total_tokens, self.config['token_price'])

                    # Split into chunks of 4096 characters (Telegram's message limit)
                    transcript_output = (
                        f"_{localized_text('transcript', bot_language)}:_\n\"{transcript}\"\n\n"
                        f"_{localized_text('answer', bot_language)}:_\n{response}"
                    )
                    chunks = split_into_chunks(transcript_output)

                    for index, transcript_chunk in enumerate(chunks):
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                            text=transcript_chunk,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('transcribe_fail', bot_language)}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )
            finally:
                if os.path.exists(filename_mp3):
                    os.remove(filename_mp3)
                if os.path.exists(filename):
                    os.remove(filename)

        await wrap_with_indicator(update, context, _execute, constants.ChatAction.TYPING)

    async def vision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Interpret image using vision model.
        """
        if not self.config['enable_vision'] or not await self.check_allowed_and_within_budget(update, context):
            return

        chat_id = update.effective_chat.id
        prompt = update.message.caption

        if is_group_chat(update):
            if self.config['ignore_group_vision']:
                logging.info(f'Vision coming from group chat, ignoring...')
                return
            else:
                trigger_keyword = self.config['group_trigger_keyword']
                if (prompt is None and trigger_keyword != '') or \
                        (prompt is not None and not prompt.lower().startswith(trigger_keyword.lower())):
                    logging.info(f'Vision coming from group chat with wrong keyword, ignoring...')
                    return

        image = update.message.effective_attachment[-1]

        async def _execute():
            bot_language = self.config['bot_language']
            try:
                media_file = await context.bot.get_file(image.file_id)
                temp_file = io.BytesIO(await media_file.download_as_bytearray())
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=(
                        f"{localized_text('media_download_fail', bot_language)[0]}: "
                        f"{str(e)}. {localized_text('media_download_fail', bot_language)[1]}"
                    ),
                    parse_mode=constants.ParseMode.MARKDOWN
                )
                return

            # convert jpg from telegram to png as understood by openai

            temp_file_png = io.BytesIO()

            try:
                original_image = Image.open(temp_file)

                original_image.save(temp_file_png, format='PNG')
                logging.info(f'New vision request received from user {update.message.from_user.name} '
                             f'(id: {update.message.from_user.id})')

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=localized_text('media_type_fail', bot_language)
                )

            user_id = update.message.from_user.id
            if user_id not in self.usage:
                self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

            if self.config['stream']:

                stream_response = self.openai.interpret_image_stream(chat_id=chat_id, fileobj=temp_file_png,
                                                                     prompt=prompt)
                i = 0
                prev = ''
                sent_message = None
                backoff = 0
                stream_chunk = 0

                async for content, tokens in stream_response:
                    if is_direct_result(content):
                        return await handle_direct_result(self.config, update, content)

                    if len(content.strip()) == 0:
                        continue

                    stream_chunks = split_into_chunks(content)
                    if len(stream_chunks) > 1:
                        content = stream_chunks[-1]
                        if stream_chunk != len(stream_chunks) - 1:
                            stream_chunk += 1
                            try:
                                await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                              stream_chunks[-2])
                            except:
                                pass
                            try:
                                sent_message = await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    text=content if len(content) > 0 else "..."
                                )
                            except:
                                pass
                            continue

                    cutoff = get_stream_cutoff_values(update, content)
                    cutoff += backoff

                    if i == 0:
                        try:
                            if sent_message is not None:
                                await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                                 message_id=sent_message.message_id)
                            sent_message = await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=content,
                            )
                        except:
                            continue

                    elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                        prev = content

                        try:
                            use_markdown = tokens != 'not_finished'
                            await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                          text=content, markdown=use_markdown)

                        except RetryAfter as e:
                            backoff += 5
                            await asyncio.sleep(e.retry_after)
                            continue

                        except TimedOut:
                            backoff += 5
                            await asyncio.sleep(0.5)
                            continue

                        except Exception:
                            backoff += 5
                            continue

                        await asyncio.sleep(0.01)

                    i += 1
                    if tokens != 'not_finished':
                        total_tokens = int(tokens)


            else:

                try:
                    interpretation, total_tokens = await self.openai.interpret_image(chat_id, temp_file_png,
                                                                                     prompt=prompt)

                    try:
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update),
                            text=interpretation,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )
                    except BadRequest:
                        try:
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=interpretation
                            )
                        except Exception as e:
                            logging.exception(e)
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=f"{localized_text('vision_fail', bot_language)}: {str(e)}",
                                parse_mode=constants.ParseMode.MARKDOWN
                            )
                except Exception as e:
                    logging.exception(e)
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        text=f"{localized_text('vision_fail', bot_language)}: {str(e)}",
                        parse_mode=constants.ParseMode.MARKDOWN
                    )
            vision_token_price = self.config['vision_token_price']
            self.usage[user_id].add_vision_tokens(total_tokens, vision_token_price)

            allowed_user_ids = self.config['allowed_user_ids'].split(',')
            if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                self.usage["guests"].add_vision_tokens(total_tokens, vision_token_price)

        await wrap_with_indicator(update, context, _execute, constants.ChatAction.TYPING)

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE, custom_prompt=None):
        """
        React to incoming messages and respond accordingly.
        """
        if update.edited_message or not update.message or update.message.via_bot:
            return

        if not await self.check_allowed_and_within_budget(update, context):
            return

        logging.info(
            f'New message received from user {update.message.from_user.name} (id: {update.message.from_user.id})')
        chat_id = update.effective_chat.id
        user_id = update.message.from_user.id

        if custom_prompt:
            prompt = message_text(custom_prompt)
        else:
            prompt = message_text(update.message)

        self.last_message[chat_id] = prompt

        if is_group_chat(update):
            trigger_keyword = self.config['group_trigger_keyword']

            if prompt.lower().startswith(trigger_keyword.lower()) or update.message.text.lower().startswith('/chat'):
                if prompt.lower().startswith(trigger_keyword.lower()):
                    prompt = prompt[len(trigger_keyword):].strip()

                if update.message.reply_to_message and \
                        update.message.reply_to_message.text and \
                        update.message.reply_to_message.from_user.id != context.bot.id:
                    prompt = f'"{update.message.reply_to_message.text}" {prompt}'
            else:
                if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
                    logging.info('Message is a reply to the bot, allowing...')
                else:
                    logging.warning('Message does not start with trigger keyword, ignoring...')
                    return

        try:
            total_tokens = 0

            if self.config['stream']:
                await update.effective_message.reply_chat_action(
                    action=constants.ChatAction.TYPING,
                    message_thread_id=get_thread_id(update)
                )

                stream_response = self.openai.get_chat_response_stream(chat_id=chat_id, query=prompt)
                i = 0
                prev = ''
                sent_message = None
                backoff = 0
                stream_chunk = 0

                async for content, tokens in stream_response:
                    if is_direct_result(content):
                        return await handle_direct_result(self.config, update, content)

                    if len(content.strip()) == 0:
                        continue

                    stream_chunks = split_into_chunks(content)
                    if len(stream_chunks) > 1:
                        content = stream_chunks[-1]
                        if stream_chunk != len(stream_chunks) - 1:
                            stream_chunk += 1
                            try:
                                await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                              stream_chunks[-2])
                            except:
                                pass
                            try:
                                sent_message = await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    text=content if len(content) > 0 else "..."
                                )
                            except:
                                pass
                            continue

                    cutoff = get_stream_cutoff_values(update, content)
                    cutoff += backoff

                    if i == 0:
                        try:
                            if sent_message is not None:
                                await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                                 message_id=sent_message.message_id)
                            sent_message = await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=content,
                            )
                        except:
                            continue

                    elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                        prev = content

                        try:
                            use_markdown = tokens != 'not_finished'
                            await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                          text=content, markdown=use_markdown)

                        except RetryAfter as e:
                            backoff += 5
                            await asyncio.sleep(e.retry_after)
                            continue

                        except TimedOut:
                            backoff += 5
                            await asyncio.sleep(0.5)
                            continue

                        except Exception:
                            backoff += 5
                            continue

                        await asyncio.sleep(0.01)

                    i += 1
                    if tokens != 'not_finished':
                        total_tokens = int(tokens)

            else:
                async def _reply():
                    nonlocal total_tokens
                    response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=prompt)

                    if is_direct_result(response):
                        return await handle_direct_result(self.config, update, response)

                    # Split into chunks of 4096 characters (Telegram's message limit)
                    chunks = split_into_chunks(response)

                    for index, chunk in enumerate(chunks):
                        try:
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config,
                                                                            update) if index == 0 else None,
                                text=chunk,
                                parse_mode=constants.ParseMode.MARKDOWN
                            )
                        except Exception:
                            try:
                                await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    reply_to_message_id=get_reply_to_message_id(self.config,
                                                                                update) if index == 0 else None,
                                    text=chunk
                                )
                            except Exception as exception:
                                raise exception

                await wrap_with_indicator(update, context, _reply, constants.ChatAction.TYPING)

            add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('chat_fail', self.config['bot_language'])} {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    async def inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle the inline query. This is run when you type: @botusername <query>
        """
        query = update.inline_query.query
        if len(query) < 3:
            return
        if not await self.check_allowed_and_within_budget(update, context, is_inline=True):
            return

        callback_data_suffix = "gpt:"
        result_id = str(uuid4())
        self.inline_queries_cache[result_id] = query
        callback_data = f'{callback_data_suffix}{result_id}'

        await self.send_inline_query_result(update, result_id, message_content=query, callback_data=callback_data)

    async def send_inline_query_result(self, update: Update, result_id, message_content, callback_data=""):
        """
        Send inline query result
        """
        try:
            reply_markup = None
            bot_language = self.config['bot_language']
            if callback_data:
                reply_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text=f'🤖 {localized_text("answer_with_chatgpt", bot_language)}',
                                         callback_data=callback_data)
                ]])

            inline_query_result = InlineQueryResultArticle(
                id=result_id,
                title=localized_text("ask_chatgpt", bot_language),
                input_message_content=InputTextMessageContent(message_content),
                description=message_content,
                thumb_url='https://user-images.githubusercontent.com/11541888/223106202-7576ff11-2c8e-408d-94ea'
                          '-b02a7a32149a.png',
                reply_markup=reply_markup
            )

            await update.inline_query.answer([inline_query_result], cache_time=0)
        except Exception as e:
            logging.error(f'An error occurred while generating the result card for inline query {e}')

    async def handle_callback_inline_query(self, update: Update, context: CallbackContext):
        """
        Handle the callback query from the inline query result
        """
        callback_data = update.callback_query.data
        user_id = update.callback_query.from_user.id
        inline_message_id = update.callback_query.inline_message_id
        name = update.callback_query.from_user.name
        callback_data_suffix = "gpt:"
        query = ""
        bot_language = self.config['bot_language']
        answer_tr = localized_text("answer", bot_language)
        loading_tr = localized_text("loading", bot_language)

        try:
            if callback_data.startswith(callback_data_suffix):
                unique_id = callback_data.split(':')[1]
                total_tokens = 0

                # Retrieve the prompt from the cache
                query = self.inline_queries_cache.get(unique_id)
                if query:
                    self.inline_queries_cache.pop(unique_id)
                else:
                    error_message = (
                        f'{localized_text("error", bot_language)}. '
                        f'{localized_text("try_again", bot_language)}'
                    )
                    await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                  text=f'{query}\n\n_{answer_tr}:_\n{error_message}',
                                                  is_inline=True)
                    return

                unavailable_message = localized_text("function_unavailable_in_inline_mode", bot_language)
                if self.config['stream']:
                    stream_response = self.openai.get_chat_response_stream(chat_id=user_id, query=query)
                    i = 0
                    prev = ''
                    backoff = 0
                    async for content, tokens in stream_response:
                        if is_direct_result(content):
                            cleanup_intermediate_files(content)
                            await edit_message_with_retry(context, chat_id=None,
                                                          message_id=inline_message_id,
                                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                                          is_inline=True)
                            return

                        if len(content.strip()) == 0:
                            continue

                        cutoff = get_stream_cutoff_values(update, content)
                        cutoff += backoff

                        if i == 0:
                            try:
                                await edit_message_with_retry(context, chat_id=None,
                                                              message_id=inline_message_id,
                                                              text=f'{query}\n\n{answer_tr}:\n{content}',
                                                              is_inline=True)
                            except:
                                continue

                        elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                            prev = content
                            try:
                                use_markdown = tokens != 'not_finished'
                                divider = '_' if use_markdown else ''
                                text = f'{query}\n\n{divider}{answer_tr}:{divider}\n{content}'

                                # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                                text = text[:4096]

                                await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                              text=text, markdown=use_markdown, is_inline=True)

                            except RetryAfter as e:
                                backoff += 5
                                await asyncio.sleep(e.retry_after)
                                continue
                            except TimedOut:
                                backoff += 5
                                await asyncio.sleep(0.5)
                                continue
                            except Exception:
                                backoff += 5
                                continue

                            await asyncio.sleep(0.01)

                        i += 1
                        if tokens != 'not_finished':
                            total_tokens = int(tokens)

                else:
                    async def _send_inline_query_response():
                        nonlocal total_tokens
                        # Edit the current message to indicate that the answer is being processed
                        await context.bot.edit_message_text(inline_message_id=inline_message_id,
                                                            text=f'{query}\n\n_{answer_tr}:_\n{loading_tr}',
                                                            parse_mode=constants.ParseMode.MARKDOWN)

                        logging.info(f'Generating response for inline query by {name}')
                        response, total_tokens = await self.openai.get_chat_response(chat_id=user_id, query=query)

                        if is_direct_result(response):
                            cleanup_intermediate_files(response)
                            await edit_message_with_retry(context, chat_id=None,
                                                          message_id=inline_message_id,
                                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                                          is_inline=True)
                            return

                        text_content = f'{query}\n\n_{answer_tr}:_\n{response}'

                        # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                        text_content = text_content[:4096]

                        # Edit the original message with the generated content
                        await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                      text=text_content, is_inline=True)

                    await wrap_with_indicator(update, context, _send_inline_query_response,
                                              constants.ChatAction.TYPING, is_inline=True)

                add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.error(f'Failed to respond to an inline query via button callback: {e}')
            logging.exception(e)
            localized_answer = localized_text('chat_fail', self.config['bot_language'])
            await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                          text=f"{query}\n\n_{answer_tr}:_\n{localized_answer} {str(e)}",
                                          is_inline=True)

    async def check_allowed_and_within_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                              is_inline=False) -> bool:
        """
        Checks if the user is allowed to use the bot and if they are within their budget
        :param update: Telegram update object
        :param context: Telegram context object
        :param is_inline: Boolean flag for inline queries
        :return: Boolean indicating if the user is allowed to use the bot
        """
        name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
        user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id

        if not await is_allowed(self.config, update, context, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) is not allowed to use the bot')
            await self.send_disallowed_message(update, context, is_inline)
            return False
        if not is_within_budget(self.config, self.usage, update, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) reached their usage limit')
            await self.send_budget_reached_message(update, context, is_inline)
            return False

        return True

    async def send_disallowed_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Seer.nds the disallowed message to the us
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=self.disallowed_message,
                disable_web_page_preview=True
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.disallowed_message)

    async def send_budget_reached_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the budget reached message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=self.budget_limit_message
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.budget_limit_message)

    async def post_init(self, application: Application) -> None:
        """
        Post initialization hook for the bot.
        """
        await application.bot.set_my_commands(self.group_commands, scope=BotCommandScopeAllGroupChats())
        await application.bot.set_my_commands(self.commands)

    async def admin_menu(self, update: Update, context: CallbackContext):
        self.user_states[update.effective_chat.id] = ''

        await update.message.reply_text("Введите ID пользователя, которому вы хотите отправить сообщение:")
        # return AWAITING_USER_ID

    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        application = ApplicationBuilder() \
            .token(self.config['token']) \
            .proxy_url(self.config['proxy']) \
            .get_updates_proxy_url(self.config['proxy']) \
            .post_init(self.post_init) \
            .concurrent_updates(True) \
            .build()

        """
            BotCommand(command='info', description="Информация о проекте"),
            BotCommand(command='menu', description="Меню"),
            BotCommand(command='account', description="Личный кабинет"),
            BotCommand(command='referral', description="Реферальная система"),
            BotCommand(command='support', description="Связаться с поддержкой"),
        """

        # YOUR_USER_ID = 627512965

        # conv_handler = ConversationHandler(
        #     entry_points=[CommandHandler('start_admin', self.admin_menu, Filters.user(user_id=YOUR_USER_ID))],
        #     states={
        #         AWAITING_USER_ID: [MessageHandler(Filters.text & ~Filters.command, get_user_id)],
        #         AWAITING_MESSAGE_TEXT: [MessageHandler(Filters.text & ~Filters.command, get_message_text)],
        #         AWAITING_FILE: [MessageHandler(Filters.document & ~Filters.command, get_file)]
        #     },
        #     fallbacks=[]
        # )
        #
        # dispatcher.add_handler(conv_handler)

        # application.add_handler(CommandHandler('admin_menu', self.admin_menu, filters=filters.User(user_id=627512965)))
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('analytics', self.analytics))
        application.add_handler(CommandHandler('naming', self.naming))
        application.add_handler(CommandHandler('shorts', self.shorts))
        application.add_handler(CommandHandler('seo', self.seo))
        application.add_handler(CommandHandler('video', self.video))
        application.add_handler(CommandHandler('restart', self.restart))

        application.add_handler(CommandHandler('info', self.info))
        application.add_handler(CommandHandler('menu', self.menu))
        application.add_handler(CommandHandler('account', self.account))
        application.add_handler(CommandHandler('referral', self.referral))
        application.add_handler(CommandHandler('support', self.support))
        application.add_handler(CommandHandler('faq', self.faq))
        application.add_handler(CommandHandler('admin', self.admin, filters=filters.User(user_id=627512965)))

        application.add_handler(CommandHandler('test', self.test_send_notification_to_admin, filters=filters.User(user_id=627512965)))

        # application.add_handler(MessageHandler(lambda update: update.message.document and update.message.from_user.id == 627512965, self.send_excel_file))
        application.add_handler(
            MessageHandler(filters.Document.ALL, self.send_excel_file))

        # application.add_handler(
        #     MessageHandler(
        #         filters.Document and filters.User(user_id=627512965),
        #         self.send_excel_file_to_user
        #     )
        # )

        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_message))
        application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        # application.add_handler(CommandHandler('reset', self.reset))
        # application.add_handler(CommandHandler('help', self.help))
        # application.add_handler(CommandHandler('image', self.image))
        # application.add_handler(CommandHandler('tts', self.tts))
        # application.add_handler(CommandHandler('stats', self.stats))
        # application.add_handler(CommandHandler('resend', self.resend))
        # application.add_handler(CommandHandler(
        #     'chat', self.prompt, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        # )
        # application.add_handler(MessageHandler(
        #     filters.PHOTO | filters.Document.IMAGE,
        #     self.vision))
        # application.add_handler(MessageHandler(
        #     filters.AUDIO | filters.VOICE | filters.Document.AUDIO |
        #     filters.VIDEO | filters.VIDEO_NOTE | filters.Document.VIDEO,
        #     self.transcribe))
        # application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.prompt))
        # application.add_handler(InlineQueryHandler(self.inline_query, chat_types=[
        #     constants.ChatType.GROUP, constants.ChatType.SUPERGROUP, constants.ChatType.PRIVATE
        # ]))
        # application.add_handler(CallbackQueryHandler(self.handle_callback_inline_query))

        application.add_error_handler(error_handler)

        application.run_polling()
