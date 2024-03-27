from database import engine, Base


def drop_all_tables():
    # Отражение существующей базы данных
    Base.metadata.reflect(engine)

    # Удаление всех отраженных таблиц
    Base.metadata.drop_all(engine)
    print("Все таблицы были успешно удалены.")


if __name__ == "__main__":
    drop_all_tables()
