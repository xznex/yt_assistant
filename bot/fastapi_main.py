import os
from datetime import timedelta, datetime

import prodamuspy
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn
from pydantic import BaseModel
from yookassa.configuration import Configuration
from yookassa.webhook import Webhook

load_dotenv()

from database import Session
from models import User, Subscription

app = FastAPI()

# Настройка CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем запросы с любых источников
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Разрешаем использовать методы GET и POST
    allow_headers=["*"],
)

prodamus = prodamuspy.ProdamusPy(os.environ['PRODAMUS_TOKEN'])

Configuration.account_id = "378521"
Configuration.secret_key = "test_M7jr6sfIUyL3yHML0hAgVHHiD9DC4VDLLvnQIs1AtiA"


# Модель данных для вебхука
class YooKassaWebhook(BaseModel):
    type: str
    event: dict


# Расчёт даты истечения подписки
def calculate_expiration_date(start_date_str: str, sub_id: int) -> datetime:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%dT%H:%M:%S%z")

    # Определение количества дней для добавления
    if sub_id == 1779399:
        days_to_add = 7
    elif sub_id == 1779400:
        days_to_add = 30
    elif sub_id == 1000000:
        days_to_add = 1
    elif sub_id == 30:
        days_to_add = 30
    else:
        days_to_add = 0

    expiration_date = start_date + timedelta(days=days_to_add)

    return expiration_date


@app.get("/")
async def read_root():
    return {"message": "Hello World"}

    # return JSONResponse(content={"success": True}, status_code=200)


@app.post("/test-webhook")
async def test_webhook(request: Request):
    print("hello")
    body_str = await request.body()
    body_str_decoded = body_str.decode("utf-8")  # Декодируем тело запроса из байтов в строку
    print(body_str)
    print(body_str_decoded)
    return JSONResponse(content={"success": True, "data": "555data"}, status_code=200)


# Обработчик вебхука от ЮKасса
@app.post("/webhook")
async def yookassa_webhook(request: Request):
    # Получение данных вебхука
    data = await request.json()
    print("Received webhook data:", data)

    # Проверка подписи вебхука
    try:
        webhook = Webhook()
        webhook.handle_notification(data)
        print("Webhook signature validated successfully.")
    except Exception as e:
        print("Failed to validate webhook signature:", e)
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    # Обработка событий
    webhook_type = data.get("event", {}).get("type")
    print("Webhook type:", webhook_type)
    if webhook_type == "payment.succeeded":
        # Обработка успешного платежа
        payment_id = data.get("event", {}).get("object", {}).get("id")
        amount = data.get("event", {}).get("object", {}).get("amount", {}).get("value")
        currency = data.get("event", {}).get("object", {}).get("amount", {}).get("currency")
        # Здесь можете выполнить дополнительные действия, например, сохранить информацию о платеже в базу данных
        print(f"Payment {payment_id} succeeded. Amount: {amount} {currency}")
        return {"message": f"Payment {payment_id} succeeded. Amount: {amount} {currency}"}
    elif webhook_type == "payment.canceled":
        # Обработка отмененного платежа
        payment_id = data.get("event", {}).get("object", {}).get("id")
        # Здесь можете выполнить дополнительные действия
        print(f"Payment {payment_id} canceled.")
        return {"message": f"Payment {payment_id} canceled."}
    else:
        # Если получено событие, которое не обрабатывается, просто возвращаем сообщение!
        print(f"Unhandled event type: {webhook_type}")
        return {"message": f"Unhandled event type: {webhook_type}"}


@app.post("/prodamus-webhook")
async def handle_webhook(request: Request):
    try:
        # application/x-www-form-urlencoded
        body_str = await request.body()
        body_str_decoded = body_str.decode("utf-8")  # Декодируем тело запроса из байтов в строку

        # Преобразование полученной строки в словарь с помощью функции parse
        data = prodamus.parse(body_str_decoded)

        subscription_id = int(data['subscription']['id'])

        if subscription_id != 1779399 and subscription_id != 1779400 and data['products'][0][
            'name'] != 'Доступ к чат-боту YouTube ассистент на 1 день' \
                and data['products'][0]['name'] != '30 дней + 1 Аналитика конкурентов' and data['products'][0][
            'name'] != '1 Аналитика конкурентов':
            # убедиться здесь действительно нужен jsonresponse а не return
            return JSONResponse(content={"success": True}, status_code=200)

        received_sign = request.headers.get("sign")

        print(str(data))

        if not prodamus.verify(data, received_sign):
            print("problems", str(data))
            raise HTTPException(status_code=403, detail="Invalid signature")

        order_id = int(data.get('order_id'))
        user_id = int(data.get('order_num'))

        if data.get('subscription'):
            subscription_id = int(data['subscription']['id'])
            tariff = data['subscription']['name']
        else:
            if data['products'][0]['name'] == '30 дней + 1 Аналитика конкурентов':
                subscription_id = 30
            if data['products'][0]['name'] == '1 Аналитика конкурентов':
                subscription_id = 301
            elif data['products'][0]['name'] == 'Доступ к чат-боту YouTube ассистент на 1 день':
                subscription_id = 1000000
            tariff = data['products'][0]['name']

        email = data.get('customer_email')
        expiration_date = calculate_expiration_date(data.get('date'), subscription_id)

        if data.get('payment_status') != 'success':
            raise HTTPException(status_code=403, detail="Invalid payment_status")

        # Поиск пользователя и обновление информации о подписке
        with Session() as session:
            subscription = session.query(Subscription).filter_by(order_id=order_id).first()
            user = session.query(User).filter(User.id == user_id).first()

            if subscription_id == 301:
                user.analytics_attempts += 1
                session.add(user)
                session.commit()
                return JSONResponse(content={"success": True}, status_code=200)

            if subscription_id == 30:
                user.analytics_attempts += 1
                session.add(user)

            if not subscription:
                subscription = Subscription(order_id=order_id, user_id=user_id, subscription_id=subscription_id,
                                            tariff=tariff, email=email, expiration_date=expiration_date, user=user)
                session.add(subscription)
            else:
                subscription.subscription_id = subscription_id
                subscription.tariff = tariff
                subscription.email = email
                subscription.expiration_date = expiration_date
            session.commit()
    except Exception as e:
        print(f"Произошла ошибка при обработке веб-хука: {e}")
        # Возврат успешного статуса, несмотря на внутреннюю ошибку
        return JSONResponse(content={"success": True}, status_code=200)

    return JSONResponse(content={"success": True}, status_code=200)


if __name__ == '__main__':
    uvicorn.run("fastapi_main:app", host="0.0.0.0", port=443,
                ssl_keyfile="/etc/letsencrypt/live/vm4986646.25ssd.had.wf/privkey.pem",
                ssl_certfile="/etc/letsencrypt/live/vm4986646.25ssd.had.wf/fullchain.pem", log_level="info")
