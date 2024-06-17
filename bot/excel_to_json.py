import openpyxl
import json

def excel_to_json(excel_file, json_file):
    wb = openpyxl.load_workbook(excel_file)
    sheet = wb.active

    data = []

    for row in sheet.iter_rows(min_row=2, values_only=True):
        video_title, video_link, channel_name, channel_link, total_views, publish_date, description = row
        video_dict = {
            "Video_Title": video_title,
            "Video_Link": video_link,
            "Channel_Name": channel_name,
            "Channel_Link": channel_link,
            "Total_Views": total_views,
            "Publish_Date": publish_date,
            "Description": description
        }
        data.append(video_dict)

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Пример использования:
excel_file = "analytics_data/KhaiamYT2.xlsx"
json_file = "analytics_data/output.json"
excel_to_json(excel_file, json_file)
print(f"JSON данные сохранены в файл: {json_file}")
