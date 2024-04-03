import os
import sys
from datetime import timedelta, datetime
from pathlib import Path
from typing import List

import prodamuspy
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

load_dotenv()

# root_dir = Path(__file__).resolve().parents[1]
# print(root_dir)
# sys.path.append(str(root_dir))

from database import Session
from models import User, Subscription

app = FastAPI()

prodamus = prodamuspy.ProdamusPy(os.environ['PRODAMUS_TOKEN'])

print(prodamus)
class Product(BaseModel):
    name: str
    price: str
    quantity: str
    sum: str


class PaymentNotification(BaseModel):
    date: str
    order_id: str
    order_num: str
    domain: str
    sum: str
    customer_phone: str
    customer_email: str
    customer_extra: str
    payment_type: str
    commission: str
    commission_sum: str
    attempt: str
    sys: str
    products: List[Product]
    payment_status: str
    payment_status_description: str


def calculate_expiration_date(start_date_str: str, sum: float) -> datetime:
    # Преобразование строки с датой начала в объект datetime
    start_date = datetime.strptime(start_date_str, "%Y-%m-%dT%H:%M:%S%z")

    # Определение количества дней для добавления
    if sum == 1490.00:
        days_to_add = 7
    elif sum == 4990.00:
        days_to_add = 30
    else:
        days_to_add = 0  # Или задайте значение по умолчанию

    # Расчёт даты истечения подписки
    expiration_date = start_date + timedelta(days=days_to_add)

    return expiration_date


@app.get("/")
async def read_root():
    return {"message": "Hello World!"}


@app.post("/prodamus-webhook")
async def handle_webhook(request: Request):
    print(request.headers.get('Content-Type'))
    body_str = await request.body()
    body_str_decoded = body_str.decode("utf-8")  # Декодируем тело запроса из байтов в строку

    # Преобразование полученной строки в словарь с помощью функции parse
    data = prodamus.parse(body_str_decoded)

    # data['order_id'] = '1'
    # data['user_id'] = 1712103633  # Предполагается, что order_id имеет формат user_id_datetime

    print(data)

    # order_id = '1'
    user_id = 1712103633  # Предполагается, что order_id имеет формат user_id_datetime

    received_sign = request.headers.get("X-Signature")

    # Проверка подписи
    if not prodamus.verify(data, received_sign):
        print("problems", received_sign)
        raise HTTPException(status_code=403, detail="Invalid signature")

    order_id = data.get('order_id')
    user_id = order_id.split('_')[0]  # Предполагается, что order_id имеет формат user_id_datetime

    payment_status = None
    if data.get('payment_status') == 'success':
        payment_status = "оплачено"
    expiration_date = calculate_expiration_date(data.get('date'), data.get('sum'))

    # Теперь data это словарь с данными, которые вы можете использовать
    # Например, вы можете обработать статус платежа и order_id здесь
    print(f"Order ID: {data.get('order_id')}, Status: {data.get('payment_status')}")

    # Поиск пользователя и обновление информации о подписке
    with Session() as session:
        subscription = session.query(Subscription).filter_by(order_id=order_id).first()
        if not subscription:
            user = session.query(User).filter(User.id == user_id).first()
            subscription = Subscription(order_id=order_id, user_id=user_id, expiration_date=expiration_date, user=user)
            if payment_status:
                subscription.status = payment_status
            session.add(subscription)
        else:
            subscription.status = payment_status
            subscription.expiration_date = expiration_date
        session.commit()

    return {"success": True}

if __name__ == '__main__':
    uvicorn.run("fastapi_main:app", host="0.0.0.0", port=8000, log_level="info")

"""
curl -X POST http://localhost:8000/prodamus-webhook -H "Content-Type: application/x-www-form-urlencoded" -H "X-Signature: c0eced1737e9fc3983d1c78cbd79ebe53bfc39b42b73d59b5cc19823a793107b" --data-urlencode "date=2024-04-02T00:00:00+03:00" --data-urlencode "order_id=1" --data-urlencode "order_num=test" --data-urlencode "domain=kirbudilovcoach.payform.ru" --data-urlencode "sum=1000.00" --data-urlencode "customer_phone=+79999999999" --data-urlencode "customer_email=email@domain.com" --data-urlencode "customer_extra=тест" --data-urlencode "payment_type=Пластиковая карта Visa, MasterCard, МИР" --data-urlencode "commission=3.5" --data-urlencode "commission_sum=35.00" --data-urlencode "attempt=1" --data-urlencode "sys=test" --data-urlencode "products[0][name]=Доступ к обучающим материалам" --data-urlencode "products[0][price]=1000.00" --data-urlencode "products[0][quantity]=1" --data-urlencode "products[0][sum]=1000.00" --data-urlencode "payment_status=success" --data-urlencode "payment_status_description=Успешная оплата"

"""
