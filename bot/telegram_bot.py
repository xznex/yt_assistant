from __future__ import annotations

import asyncio
import io
import logging
import os
from uuid import uuid4

from PIL import Image
from pydub import AudioSegment
from telegram import BotCommandScopeAllGroupChats, Update, constants
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle
from telegram import InputTextMessageContent, BotCommand
from telegram.error import RetryAfter, TimedOut, BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, \
    filters, InlineQueryHandler, CallbackQueryHandler, Application, ContextTypes, CallbackContext

from openai_helper import OpenAIHelper, localized_text
from usage_tracker import UsageTracker
from utils import is_group_chat, get_thread_id, message_text, wrap_with_indicator, split_into_chunks, \
    edit_message_with_retry, get_stream_cutoff_values, is_allowed, get_remaining_budget, is_within_budget, \
    get_reply_to_message_id, add_chat_request_to_usage_tracker, error_handler, is_direct_result, handle_direct_result, \
    cleanup_intermediate_files

from database import Session
from models import User


class UserContext:
    """
    Class user context
    """

    def __init__(self):
        self._chat_id = None
        self._name = None  # имя
        self._channel_description = None  # True или False
        self._channel_idea = None  # строка

    def update_user_name(self, user_id, new_name):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if user.name == new_name:
                return

            if not user:
                new_user = User(id=user_id, name=new_name)
                session.add(new_user)
            else:
                user.name = new_name

            self._name = new_name
            session.commit()

    def save_description_and_idea(self, user_id, new_description, new_idea):
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            if not user:
                new_user = User(id=user_id, channel_description=new_description, channel_idea=new_idea)
                session.add(new_user)
            else:
                user.channel_description = new_description
                user.channel_idea = new_idea

            self._channel_description = new_description
            self._channel_idea = new_idea
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
        self.commands = [
            BotCommand(command='help', description=localized_text('help_description', bot_language)),
            BotCommand(command='reset', description=localized_text('reset_description', bot_language)),
            BotCommand(command='stats', description=localized_text('stats_description', bot_language)),
            BotCommand(command='resend', description=localized_text('resend_description', bot_language))
        ]
        # If imaging is enabled, add the "image" command to the list
        if self.config.get('enable_image_generation', False):
            self.commands.append(
                BotCommand(command='image', description=localized_text('image_description', bot_language)))

        if self.config.get('enable_tts_generation', False):
            self.commands.append(BotCommand(command='tts', description=localized_text('tts_description', bot_language)))

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

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id

        await context.bot.send_photo(chat_id=chat_id, photo='start_photo.jpg')
        await update.message.reply_text(
            "Привет, я твой карманный YouTube продюсер 👋🏻\n"
            "Создатель назвал меня Сильвия, но для тебя я буду ассистентом по старту твоего канала на YouTube 🎥\n"
            "Я существую, чтобы ты сэкономил сотни тысяч рублей на найме команды или на дорогом продакшне и начал получать первые просмотры уже сегодня вечером❤️\n"
            "Я придумаю за тебя сценарии и даже пропишу теги к видео, тебе останется лишь снять и выложить ролик 😻",
        )
        await update.message.reply_text(
            "Но для начала давай познакомимся, как тебя зовут?"
        )

        # self.user_context.chat_id = chat_id
        self.user_states[update.effective_chat.id] = 'waiting_for_name'

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = self.user_states.get(update.effective_chat.id)
        chat_id = update.effective_chat.id
        if state == 'waiting_for_name':
            # Сохраняем имя пользователя
            # self.user_context.name = update.message.text

            if chat_id not in self.user_contexts:
                user_context = UserContext()
                self.user_contexts[chat_id] = user_context
            else:
                user_context = self.user_contexts[chat_id]

            user_context.update_user_name(chat_id, update.message.text)

            # self.user_names[update.effective_chat.id] = update.message.text
            # Меняем состояние
            self.user_states[update.effective_chat.id] = 'asking_about_channel'
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
            # СЮДА ВСТАВИТЬ
            await self.to_continue_or_see_features(update, context, user_input)

            # Вызываем функцию continue_or_see_features и передаем ей введенный текст
            # await self.turnkey_generation(update, context)
        elif state == 'waiting_user_description':
            user_input = update.message.text
            # self.user_input[update.effective_chat.id] = user_input
            await self.turnkey_generation(update, context, user_description=user_input)
        elif state == 'waiting_for_seo':
            user_input = update.message.text
            await update.message.reply_text(
                "Отлично! Ушла писать сценарии! 😇"
            )
            await self.seo_handler(update, context, user_input)

    # Да, все callback-запросы, созданные в вашем боте, будут перенаправляться в handle_callback_query.
    # Ваша задача — в этой функции различать идентификаторы (callback data) этих запросов и реагировать на них
    # соответствующим образом. Вы можете использовать уникальные идентификаторы для разных кнопок и проверять их
    # в handle_callback_query, чтобы определить, какую логику следует выполнить в ответ на действие пользователя.
    # Это стандартный подход для работы с callback-запросами в Telegram ботах.
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == 'channel_exists':
            await self.couple_of_questions(update, context)
        elif query.data == 'starting_channel':
            await self.couple_of_questions(update, context)
        elif query.data == "ready_to_continue":
            await self.input_channel_packaging(update, context)
        elif query.data == "turnkey_channel":
            # await self.to_continue_or_see_features(update, context)
            # await self.left_to_develop(update, context)
            await update.callback_query.message.reply_text(
                "Отлично, уже ушла разрабатывать концепцию для названия твоего канала, а пока ты можешь еще кое с чем мне помочь. Напиши от 10 до 40 слов, которыми можно описать идею твоего канала, это сильно поможет нам выводить наши ролик в топы запросов зрителей в будущем 😍"
            )
            await self.turnkey_generation(update, context)
        elif query.data == "view_features":
            await self.view_features(update, context)
        elif query.data == "start_creating_video":
            await self.congratulations_with_readiness(update, context)

    async def couple_of_questions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Ваша логика для обработки ситуации, когда у пользователя уже есть канал
        # await update.message.reply_text(
        #
        # )
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
            "Класс, давай начнем с упаковки канала и я придумаю тебе название и описание. Расскажи мне в 2-3х предложениях о чем твой канал? Постарайся раскрыться максимально подробно, это правда важно ❤️"
        )
        await update.callback_query.message.reply_text(
            "Напиши мне сообщения, начиная с \"О...\"\n\nНапример: О том, как помогать людям избавляться от тревожности с помощью трансовые техник и как стать более счастливым и ментально здоровым человеком. Мой канал про психологию, мышление и психическое здоровье. Про..."
        )
        self.user_states[update.effective_chat.id] = 'awaiting_channel_description'

    async def to_continue_or_see_features(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        # Просто выводим обратно текст, полученный от пользователя
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

        await update.message.reply_text(
            "Отлично, через 30 секунд вернусь к тебе с идеями с названием и с описанием канала, никуда не уходи!"
        )

        # Сохранение в БД
        if chat_id not in self.user_contexts:
            user_context = UserContext()
            self.user_contexts[chat_id] = user_context
        else:
            user_context = self.user_contexts[chat_id]

        user_context.save_description_and_idea(chat_id, user_description, user_input)

        titles_prompt = f"Придумай 50 версий названий для YouTube канала {user_input}. В названии должно содержаться от 2 до 4 слов, отражающих тематику канала, но они должны выглядеть как целостная фраза.На русском языке"
        description_prompt = f"Напиши описание к ютуб каналу про {user_description} В описании должно быть 400 слов. Укажи подробности о том, какой контент здесь люди смогут посмотреть и добавь призывы на подписку на канал и укажи, кому точно стоит оставаться на канале и смотреть его регулярно, чтобы не пропустить новых видео"
        titles_response, titles_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=titles_prompt)
        description_response, description_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=description_prompt)
        await update.message.reply_text(
            f"Придумала для тебя 50 идей для названия, выбери любое понравившееся 👇\n\n{user_input}"
        )
        await update.message.reply_text(
            str(titles_response)
        )
        await update.message.reply_text(
            "А вот и описание для канала! Можешь просто скопировать и вставить его. Кстати, я прикрепила ниже инструкцию, как это сделать 👇"
        )

        keyboard = [
            [InlineKeyboardButton("Приступить к созданию видео", callback_data='start_creating_video')],
            [InlineKeyboardButton("Открыть полный набор функций", callback_data='view_features')],
            [InlineKeyboardButton("Как поставить описание и название?",
                                  url='https://support.google.com/youtube/answer/2657964')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправка сообщения с кнопками
        await context.bot.send_message(chat_id=chat_id, text=description_response, reply_markup=reply_markup)

        # await self.prompt(update, context, f"Придумай 50 версий названий для YouTube канала {user_input}. В названии должно содержаться от 2 до 4 слов, отражающих тематику канала, но они должны выглядеть как целостная фраза.На русском языке")

        # self.prompt(upd)
        # необохдимо сделать запрос к ChatGPT через метод self.prompt(), в котором будет следующий промпт: ("Придумай 50 версий названий для YouTube канала {user_input}. В названии должно содержаться от 2 до 4 слов, отражающих тематику канала, но они должны выглядеть как целостная фраза.На русском языке")

    async def view_features(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.message.reply_text(
            "Добро пожаловать в главное меню! Я помогу тебе с созданием контента на YouTube и оптимизацией видео\n\nВот задачи, с которыми я могу помочь 👇  \n/video - Создать сценарий видео \n/shorts - Создать сценарий shorts  \n/seo - Придумать название и описание к видео  \n/restart - Перезапустить бота \n\nВыбирай нужную функцию в меню ниже"
        )

    async def congratulations_with_readiness(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = """Поздравляю, наш канал готов! Позже мы создадим шапку и логотип, а теперь предлагаю перейти к созданию первых Shorts, чтобы уже получить первые просмотры.\n\n*Теперь для тебя открыты другие функции, жми кнопку меню, чтобы получить информацию*"""
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data='view_features')]])
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='Markdown')

    # async def left_to_develop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    #     chat_id = update.message.chat_id
    #
    #     await update.message.reply_text(
    #         "Отлично, уже ушла разрабатывать концепцию для названия твоего канала, а пока ты можешь еще кое с чем мне помочь. Напиши от 10 до 40 слов, которыми можно описать идею твоего канала, это сильно поможет нам выводить наши ролик в топы запросов зрителей в будущем 😍"
    #     )
    #     await update.message.reply_text(
    #         "Напиши слова списком, сколько сможешь придумать. Пример: Психология, коучинг, мышление, состояние, тело, здоровье, ..."
    #     )
    #
    #     self.user_context.chat_id = chat_id
    #     self.user_states[update.effective_chat.id] = 'waiting_for_name'

    async def shorts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        await update.message.reply_text(
            "Приступим к созданию shorts!"
        )

        # TODO: для всех обращений к локальному хранилищу, проверять БД + оптимизировать
        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            # print(self.user_contexts[chat_id], self.user_contexts[chat_id]['_channel_description'])
            if user and user.channel_description:
                await update.message.reply_text(
                    "Отлично, я уже знаю, о чем твой канал! Пойду прописывать сценарий, буду меньше, чем через минуту 😇"
                )
                shorts_query = f"Распиши 3 сценариев коротких видео по теме {user.channel_description} :: указав место съемки, раскадровку с числом секунд :: Полный текст, описание ролика с призывом к действию"
                shorts_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=shorts_query)
                await update.message.reply_text(
                    "Вот твой ответ!"
                )
                await update.message.reply_text(
                    str(shorts_response), parse_mode='Markdown'
                )
                return

            keyboard = [
                [InlineKeyboardButton("Хорошо, давай начнём!", callback_data='ready_to_continue')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Я задам всего пару вопросов, а затем ты сможешь перейти к созданию шортсов по команде /shorts 🎥",
                reply_markup=reply_markup
            )

    async def seo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Отлично! Давай придумаем описание и название к твоему видео, чтобы оно выдавалось в поисковых запросах. Чтобы я смогла тебе помочь, для начала расскажи мне - о чем твое видео? Буквально в 5 предложениях"
        )

        self.user_states[update.effective_chat.id] = 'waiting_for_seo'

    async def seo_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        seo_query = f"Создай seo оптимизацию для видео на YouTube по заданию ниже. Придумай 3 названия для видеоролика на YouTube по теме {user_input}. Придумай описание к видео на YouTube по теме {user_input} :: Результат представь в следующем виде. Описание должно состоять из 3 абзацев, первый должен отражать содержание и содержать ключевые слова для выдачи в поиске. Количество предложений от 5 до 6. Второй рассказывает про ролик и так же содержит ключевые слова для seo, количество предложений от 7 до 10. В третьем абзаце должно рассказывать о канале, количество предложений от 7 до 10. В конце описания должно быть 5 хэштегов по теме видео, каждый хэштег - 1 слово. В Четвертом абзаце укажи 20 тегов от 1 до 3 слов по теме видео. Некоторые теги могут начинать со слова “как”, теги должны идти единым текстовым блоком, через запятую. :: В пятом абзаце придумай 3 идеи концепции для превью картинок на YouTube, какое должно быть фото на фоне, какого цвета фон, какие элементы расположить на картинке и какой должен быть указан текст. В шестом абзаце напиши, укажи ссылки на соцсети"

        shorts_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=seo_query)
        await update.message.reply_text(
            "Вот твой ответ!"
        )

        keyboard = [
            [InlineKeyboardButton("Посмотреть функции", callback_data='view_features')],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=str(shorts_response),
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        await update.message.reply_text(
            "Приступим к созданию видео!"
        )

        with Session() as session:
            user = session.query(User).filter(User.id == user_id).first()

            # TODO: добавить возможность создавать новые видео
            # TODO: добавить поддержку ответа (response) в несколько сообщений (когда ответы большие)
            if user and user.channel_description:
                await update.message.reply_text(
                    "Отлично, я уже знаю, о чем можно снять первое видео! Пойду прописывать сценарий, буду меньше, чем через минуту 😇"
                )
                video_query = f"Распиши сценарий видео на 5-10 минут по теме {user.channel_description} :: указав место съемки, подробную раскадровку с числом секунд, внешний вид автора :: Напиши полный текст, по каждому промежутку раскадровки, который произнесет автор, с завершением ролика призывом к действию :: А после укажи рекомендации, на что обратить внимание при съемке"
                video_response, shorts_total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=video_query)
                await update.message.reply_text(
                    "Вот твой ответ!"
                )
                await update.message.reply_text(
                    str(video_response), parse_mode='Markdown'
                )
                return

            keyboard = [
                [InlineKeyboardButton("Хорошо, давай начнём!", callback_data='ready_to_continue')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Я задам всего пару вопросов, а затем ты сможешь перейти к созданию видео по команде /video 🎥",
                reply_markup=reply_markup
            )

    async def restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        application.add_handler(CommandHandler('shorts', self.shorts))
        application.add_handler(CommandHandler('seo', self.seo))
        application.add_handler(CommandHandler('video', self.video))
        application.add_handler(CommandHandler('restart', self.restart))

        application.add_handler(CommandHandler('reset', self.reset))
        application.add_handler(CommandHandler('help', self.help))
        application.add_handler(CommandHandler('image', self.image))
        application.add_handler(CommandHandler('tts', self.tts))
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_message))
        application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        application.add_handler(CommandHandler('stats', self.stats))
        application.add_handler(CommandHandler('resend', self.resend))
        application.add_handler(CommandHandler(
            'chat', self.prompt, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        )
        # application.add_handler(MessageHandler(
        #     filters.PHOTO | filters.Document.IMAGE,
        #     self.vision))
        # application.add_handler(MessageHandler(
        #     filters.AUDIO | filters.VOICE | filters.Document.AUDIO |
        #     filters.VIDEO | filters.VIDEO_NOTE | filters.Document.VIDEO,
        #     self.transcribe))
        # application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.prompt))
        application.add_handler(InlineQueryHandler(self.inline_query, chat_types=[
            constants.ChatType.GROUP, constants.ChatType.SUPERGROUP, constants.ChatType.PRIVATE
        ]))
        # application.add_handler(CallbackQueryHandler(self.handle_callback_inline_query))

        application.add_error_handler(error_handler)

        application.run_polling()
