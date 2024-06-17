import json
from typing import List

import pandas as pd

from utils_parser import parse_views, parse_publish_date


# file_path = "input.json"

async def parser(file_path, keys: List):
    data = None
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
            print("Данные успешно загружены из JSON файла")

        print("обработка пошла!!")

        sheet1_data = []
        sheet2_data = []
        sheet3_data = []

        if data:
            unique_entries = set()

            for line in data:
                video_title = line['Video_Title'].strip()
                video_link = line['Video_Link']
                channel_name = line['Channel_Name']
                channel_link = line['Channel_Link']
                total_views = line['Total_Views']
                publish_date = line['Publish_Date']

                if "/shorts/" in video_link:
                    continue

                if "Streamed" in publish_date:
                    continue

                if not ("K" in total_views or "M" in total_views) or "Scheduled" in total_views or "Premieres" in total_views:
                    continue

                if publish_date == "" or publish_date == "nan" or publish_date.endswith("minutes ago"):
                    continue

                parsed_views = parse_views(total_views)

                publish_date_updated = parse_publish_date(publish_date)

                if publish_date_updated > 365:
                    continue

                if parsed_views < 100000 and not (publish_date_updated < 30 and parsed_views > 60000):
                    continue

                coefficient = parsed_views / publish_date_updated

                entry = (video_title, channel_link)
                if entry in unique_entries:
                    continue
                else:
                    unique_entries.add(entry)

                    # Проверяем, содержит ли заголовок хотя бы одно слово из списка keys
                    contains_key_word = any(key.lower() in video_title.lower() for key in keys)
                    if not contains_key_word:
                        continue

                if publish_date_updated <= 7:
                    sheet1_data.append(
                        (video_title, video_link, channel_name, channel_link, parsed_views))
                elif 7 < publish_date_updated < 30:
                    sheet2_data.append(
                        (video_title, video_link, channel_name, channel_link, parsed_views))
                elif publish_date_updated > 30:
                    sheet3_data.append(
                        (video_title, video_link, channel_name, channel_link, parsed_views))

            sheet1_data.sort(key=lambda x: x[4] / publish_date_updated, reverse=True)
            sheet2_data.sort(key=lambda x: x[4] / publish_date_updated, reverse=True)
            sheet3_data.sort(key=lambda x: x[4] / publish_date_updated, reverse=True)

            print("начинает сохранять", file_path)

            with pd.ExcelWriter(f'output_{file_path}.xlsx', engine='xlsxwriter') as writer:
                pd.DataFrame(sheet1_data,
                             columns=['Видео', 'Ссылка на видео', 'Канал', 'Ссылка на канал', 'Просмотры']
                             ).to_excel(writer, sheet_name='Тренды недели', index=False)
                pd.DataFrame(sheet2_data,
                             columns=['Видео', 'Ссылка на видео', 'Канал', 'Ссылка на канал', 'Просмотры']
                             ).to_excel(writer, sheet_name='Тренды месяца', index=False)
                pd.DataFrame(sheet3_data,
                             columns=['Видео', 'Ссылка на видео', 'Канал', 'Ссылка на канал', 'Просмотры']
                             ).to_excel(writer, sheet_name='Тренды года', index=False)

            print(f'output_{file_path}.xlsx')
            return f'output_{file_path}.xlsx'
    except Exception as e:
        print(f"Ошибка при загрузке данных из JSON файла: {e}")
    else:
        raise Exception("Проблема с чтением файла")
