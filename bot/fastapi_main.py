import os
from datetime import timedelta, datetime

import prodamuspy
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

load_dotenv()

from database import Session
from models import User, Subscription

app = FastAPI()

prodamus = prodamuspy.ProdamusPy(os.environ['PRODAMUS_TOKEN'])


# Расчёт даты истечения подписки
def calculate_expiration_date(start_date_str: str, sub_id: int) -> datetime:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%dT%H:%M:%S%z")

    # Определение количества дней для добавления
    if sub_id == 1779399:
        days_to_add = 7
    elif sub_id == 1779400:
        days_to_add = 30
    else:
        days_to_add = 0

    expiration_date = start_date + timedelta(days=days_to_add)

    return expiration_date


@app.get("/")
async def read_root():
    return JSONResponse(content={"success": True}, status_code=200)


@app.post("/prodamus-webhook")
async def handle_webhook(request: Request):
    try:
        """
        {'date': '2024-04-04T20:06:13+03:00', 'order_id': '20344147', 'order_num': '627512965', 
        'domain': 'kirbudilovcoach.payform.ru', 'sum': '1490.00', 'currency': 'rub', 'customer_phone': '+79999999999', 
        'customer_email': 'test@yandex.ru', 'customer_extra': '', 'payment_type': 'Оплата картой, выпущенной в РФ', 
        'commission': '3.5', 'commission_sum': '52.15', 'attempt': '1', 'products': [{'name': 'Доступ к чат-боту YouTube
         ассистент на 7 дней ', 'price': '1490.00', 'quantity': '1', 'sum': '1490.00'}], 'subscription': 
         {'id': '1779399', 'profile_id': '564457', 'demo': '1', 'active_manager': '1', 'active_manager_date': '',
          'active_user': '1', 'active_user_date': '', 'cost': '1490.00', 'currency': 'rub', 'name': 'Доступ к чат-боту
           YouTube ассистент на 7 дней ', 'limit_autopayments': '', 'autopayments_num': '0', 'first_payment_discount':
            '0.00', 'next_payment_discount': '0.00', 'next_payment_discount_num': '', 'date_create': '2024-04-04 20:05:54',
             'date_first_payment': '2024-04-04 20:05:54', 'date_last_payment': '2024-04-04 20:05:54',
              'date_next_payment': '2024-04-11 20:05:54', 'date_alt_next_payment': '', 'date_next_payment_discount':
               '2024-04-04 20:05:54', 'date_completion': '', 'payment_num': '1', 'notification': '0',
                'process_started_at': '', 'autopayment': '0'}, 'payment_status': 'success', 'payment_status_description':
                 'Успешная оплата', 'payment_init': 'manual'}
        заносим user_id (order_num), order_id, subscription_id, tariff, email, expiration_date и объект user
        """
        # application/x-www-form-urlencoded
        body_str = await request.body()
        body_str_decoded = body_str.decode("utf-8")  # Декодируем тело запроса из байтов в строку

        # Преобразование полученной строки в словарь с помощью функции parse
        data = prodamus.parse(body_str_decoded)

        received_sign = request.headers.get("sign")

        print(str(data))

        if not prodamus.verify(data, received_sign):
            print("problems", str(data))
            raise HTTPException(status_code=403, detail="Invalid signature")

        order_id = int(data.get('order_id'))
        user_id = int(data.get('order_num'))
        subscription_id = int(data['subscription']['id'])
        tariff = data['subscription']['name']
        email = data.get('customer_email')
        expiration_date = calculate_expiration_date(data.get('date'), subscription_id)

        if data.get('payment_status') != 'success':
            raise HTTPException(status_code=403, detail="Invalid payment_status")

        # Поиск пользователя и обновление информации о подписке
        with Session() as session:
            subscription = session.query(Subscription).filter_by(order_id=order_id).first()
            if not subscription:
                user = session.query(User).filter(User.id == user_id).first()
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
    uvicorn.run("fastapi_main:app", host="0.0.0.0", port=8000, log_level="info")
