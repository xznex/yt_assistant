import os
import re

from googleapiclient.discovery import build
from dotenv import load_dotenv
load_dotenv()

# api_key = os.environ['YOUTUBE_API_TOKEN']
# print(api_key)


# def get_subscriber_count(channel_username):
#     # Создаем объект YouTube Data API
#     youtube = build('youtube', 'v3', developerKey=api_key)
#
#     # Выполняем запрос на получение информации о канале
#     channel_response = youtube.channels().list(
#         part='statistics',
#         forUsername=f"{channel_username}"
#     ).execute()
#
#     # Извлекаем количество подписчиков из ответа
#     # print(channel_response)
#
#     if 'items' in channel_response and channel_response['items']:
#         subscriber_count = int(channel_response['items'][0]['statistics']['subscriberCount'])
#         return subscriber_count
#     else:
#         print(f"Парсинг подписчиков для канала {channel_username} не доступен")
#         return 1


# print(get_subscriber_count('ctctv'))


def parse_views(views_str: str) -> int:
    if views_str.endswith("K views"):
        return int(float(views_str[:-7]) * 1000)
    elif views_str.endswith("K watching"):
        return int(float(views_str[:-10]) * 1000)
    elif views_str.endswith("M views"):
        return int(float(views_str[:-7]) * 1000000)
    else:
        return int(views_str)


def parse_publish_date(publish_date_str) -> float:
    if publish_date_str.endswith("years ago"):
        return float(float(publish_date_str[:-9]) * 365)
    elif publish_date_str.endswith("year ago"):
        return float(float(publish_date_str[:-8]) * 365)
    elif publish_date_str.endswith("months ago"):
        return float(float(publish_date_str[:-10]) * 30)
    elif publish_date_str.endswith("month ago"):
        return float(float(publish_date_str[:-9]) * 30)
    elif publish_date_str.endswith("weeks ago"):
        return float(float(publish_date_str[:-9]) * 7)
    elif publish_date_str.endswith("days ago"):
        return float(publish_date_str[:-8])
    elif publish_date_str.endswith("day ago"):
        return float(publish_date_str[:-7])
    elif publish_date_str.endswith("hours ago"):
        return float(float(publish_date_str[:-9]) / 24)
    elif publish_date_str.endswith("hour ago"):
        return float(float(publish_date_str[:-8]) / 24)


def extract_channel_username(url):
    # Регулярное выражение для извлечения channel_username
    pattern = r'^https?://(?:www\.)?youtube\.com/@?([^\s/]+)$'
    match = re.match(pattern, url)
    if match:
        return match.group(1)
    else:
        return None
