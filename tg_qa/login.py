"""Разовый вход в Telegram для чтения чатов хакатона (Telethon).

Запускает владелец аккаунта сам, один раз:
    python tg_qa/login.py          # вход по QR-коду (по умолчанию)
    python tg_qa/login.py --code   # вход по коду из Telegram
QR сканируется в приложении на телефоне: Настройки → Устройства →
Подключить устройство. Код нужен только если QR недоступен: Telegram
иногда не доставляет коды сторонним клиентам.
Телефон, код из Telegram и пароль 2FA вводятся в этом терминале и
обрабатываются локально. Скрипт их не печатает и не логирует.
Сессия ложится в tg_qa/.session/ (в .gitignore): это полный доступ
к аккаунту, не коммитить и никому не передавать.
Пока идёт вход, сессию не должен открывать никакой другой процесс
(fetch.py, проверки): Telegram отвечает «wrong session ID» и рвёт вход.
"""
import getpass
import os
import sys

from dotenv import load_dotenv
from telethon.errors import (PhoneCodeExpiredError, PhoneCodeInvalidError,
                             SessionPasswordNeededError)
from telethon.sync import TelegramClient
from telethon.tl import types

_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_DIR, ".env"))
SESSION_PATH = os.path.join(_DIR, ".session", "hackathon")

_DELIVERY = {
    types.auth.SentCodeTypeApp: "в приложение Telegram: чат от официального аккаунта «Telegram» (не SMS)",
    types.auth.SentCodeTypeSms: "по SMS",
    types.auth.SentCodeTypeCall: "звонком: код продиктуют",
    types.auth.SentCodeTypeFlashCall: "сбросом звонка: код = последние цифры номера звонившего",
    types.auth.SentCodeTypeFragmentSms: "через Fragment",
}


def _where(sent):
    for cls, text in _DELIVERY.items():
        if isinstance(sent.type, cls):
            return text
    return f"способ {sent.type.__class__.__name__}"


def _password(client):
    client.sign_in(password=getpass.getpass("Облачный пароль 2FA (ввод скрыт): "))


def _qr_login(client):
    import qrcode
    png = os.path.join(os.path.dirname(SESSION_PATH), "qr.png")  # .session/ в .gitignore
    qr = client.qr_login()
    run = client.loop.run_until_complete  # QRLogin telethon.sync не оборачивает
    try:
        while True:
            qrcode.make(qr.url).save(png)
            if os.name == "nt":
                os.startfile(png)
            q = qrcode.QRCode(border=1)
            q.add_data(qr.url)
            q.print_ascii(invert=True)
            print(f"Отсканируй QR в Telegram на телефоне: Настройки → Устройства → "
                  f"Подключить устройство. Картинка: {png}. Код живёт ~30 с, потом обновится.")
            try:
                run(qr.wait(timeout=30))
                return
            except SessionPasswordNeededError:
                _password(client)
                return
            except TimeoutError:
                run(qr.recreate())
    finally:
        if os.path.exists(png):
            os.remove(png)


def _code_login(client):
    phone = input("Телефон в формате +7XXXXXXXXXX: ").strip()
    sent = client.send_code_request(phone)
    print(f"Код отправлен {_where(sent)}.")
    while True:
        code = input("Код из Telegram (пусто — отправить ещё раз): ").strip()
        if not code:
            sent = client.send_code_request(phone, force_sms=True)
            print(f"Повторно отправлен {_where(sent)}.")
            continue
        try:
            client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
            return
        except PhoneCodeInvalidError:
            print("Неверный код, введи ещё раз. Это 5 цифр из сообщения, не номер телефона.")
        except PhoneCodeExpiredError:
            sent = client.send_code_request(phone)
            print(f"Код истёк, новый отправлен {_where(sent)}.")
        except SessionPasswordNeededError:
            _password(client)
            return


def main():
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        print("Нет TG_API_ID / TG_API_HASH в tg_qa/.env.")
        print("Получить: https://my.telegram.org -> API development tools.")
        sys.exit(1)

    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    client = TelegramClient(SESSION_PATH, int(api_id), api_hash)
    client.connect()
    if not client.is_user_authorized():
        _code_login(client) if "--code" in sys.argv else _qr_login(client)
    me = client.get_me()
    client.disconnect()
    uname = f"@{me.username}" if me.username else "—"
    print(f"Вход выполнен: {me.first_name} ({uname}). Окно можно закрыть.")


if __name__ == "__main__":
    main()
