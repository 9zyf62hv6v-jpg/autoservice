import urllib.parse
import urllib.request
import json
import logging

logger = logging.getLogger(__name__)


class SMSProvider:
    """Базовый класс для всех SMS-провайдеров."""
    def send(self, phone: str, message: str) -> tuple[bool, str]:
        """
        Отправляет SMS.
        Возвращает (успех, ответ_провайдера).
        """
        raise NotImplementedError


class SMSRuProvider(SMSProvider):
    """Отправка через SMS.RU (api_id)."""
    def __init__(self, api_id: str):
        self.api_id = api_id

    def send(self, phone, message):
        # Документация: https://sms.ru/api/send
        # Приводим номер к формату 7XXXXXXXXXX
        phone = ''.join(filter(str.isdigit, phone))
        if phone.startswith('8'):
            phone = '7' + phone[1:]
        params = urllib.parse.urlencode({
            "api_id": self.api_id,
            "to": phone,
            "msg": message,
            "json": 1,
        })
        url = f"https://sms.ru/sms/send?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "OK":
                    return True, "OK"
                return False, data.get("status_text", "Unknown error")
        except Exception as e:
            logger.exception("SMS.RU send error")
            return False, str(e)


class SMSAeroProvider(SMSProvider):
    """Отправка через SMS Aero (email + api_key)."""
    def __init__(self, email: str, api_key: str, sign: str = "SMS Aero"):
        self.email = email
        self.api_key = api_key
        self.sign = sign

    def send(self, phone, message):
        # Документация: https://smsaero.ru/api/
        params = urllib.parse.urlencode({
            "user": self.email,
            "password": self.api_key,
            "to": phone,
            "text": message,
            "sign": self.sign,
        })
        url = f"https://gate.smsaero.ru/v2/sms/send?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get("success"):
                    return True, "OK"
                return False, data.get("message", "Unknown error")
        except Exception as e:
            logger.exception("SMS Aero send error")
            return False, str(e)


class BytehandProvider(SMSProvider):
    """Отправка через Bytehand (login + password + sender)."""
    def __init__(self, login: str, password: str, sender: str):
        self.login = login
        self.password = password
        self.sender = sender

    def send(self, phone, message):
        # Документация: https://bytehand.com/api
        # Упрощённый пример, реальный API может отличаться
        params = urllib.parse.urlencode({
            "login": self.login,
            "password": self.password,
            "to": phone,
            "message": message,
            "sender": self.sender,
        })
        url = f"https://api.bytehand.com/send?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                # Проверьте фактический формат ответа в документации Bytehand
                if data.get("status") == "ok":
                    return True, "OK"
                return False, data.get("error", "Unknown error")
        except Exception as e:
            logger.exception("Bytehand send error")
            return False, str(e)


class IQSMSProvider(SMSProvider):
    """Отправка через iqsms (login + password)."""
    def __init__(self, login: str, password: str, sender: str = ""):
        self.login = login
        self.password = password
        self.sender = sender

    def send(self, phone, message):
        # Документация: https://iqsms.ru/api
        params = urllib.parse.urlencode({
            "login": self.login,
            "password": self.password,
            "phone": phone,
            "text": message,
            "sender": self.sender,
        })
        url = f"https://api.iqsms.ru/messages/v2/send/?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "ok":
                    return True, "OK"
                return False, data.get("error", "Unknown error")
        except Exception as e:
            logger.exception("IQSMS send error")
            return False, str(e)


class GoSMSProvider(SMSProvider):
    """Отправка через GoSMS (api_key)."""
    def __init__(self, api_key: str, sender: str = ""):
        self.api_key = api_key
        self.sender = sender

    def send(self, phone, message):
        # Документация: https://gosms.ru/api
        params = urllib.parse.urlencode({
            "api_key": self.api_key,
            "to": phone,
            "text": message,
            "sender": self.sender,
        })
        url = f"https://api.gosms.ru/send?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "success":
                    return True, "OK"
                return False, data.get("message", "Unknown error")
        except Exception as e:
            logger.exception("GoSMS send error")
            return False, str(e)


class SMSCProvider(SMSProvider):
    """Отправка через SMSC.ru (login + password)."""
    def __init__(self, login: str, password: str, sender: str = ""):
        self.login = login
        self.password = password
        self.sender = sender

    def send(self, phone, message):
        # Документация: https://smsc.ru/api/
        params = urllib.parse.urlencode({
            "login": self.login,
            "psw": self.password,
            "phones": phone,
            "mes": message,
            "sender": self.sender,
            "fmt": 3,  # JSON
        })
        url = f"https://smsc.ru/sys/send.php?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if "error" not in data:
                    return True, "OK"
                return False, data.get("error", "Unknown error")
        except Exception as e:
            logger.exception("SMSC.ru send error")
            return False, str(e)


def get_provider(settings) -> SMSProvider:
    """
    Фабрика: возвращает экземпляр провайдера на основе настроек.
    """
    provider = settings.sms_provider or "smsru"

    if provider == "smsru":
        if not settings.sms_api_id:
            raise ValueError("Не задан API ID для SMS.RU")
        return SMSRuProvider(settings.sms_api_id)

    elif provider == "smsaero":
        if not settings.sms_email or not settings.sms_api_key:
            raise ValueError("Не заданы email и API-ключ для SMS Aero")
        return SMSAeroProvider(settings.sms_email, settings.sms_api_key, settings.sms_sender or "SMS Aero")

    elif provider == "bytehand":
        if not settings.sms_login or not settings.sms_password:
            raise ValueError("Не заданы логин и пароль для Bytehand")
        return BytehandProvider(settings.sms_login, settings.sms_password, settings.sms_sender or "Bytehand")

    elif provider == "iqsms":
        if not settings.sms_login or not settings.sms_password:
            raise ValueError("Не заданы логин и пароль для iqsms")
        return IQSMSProvider(settings.sms_login, settings.sms_password, settings.sms_sender or "")

    elif provider == "gosms":
        if not settings.sms_api_key:
            raise ValueError("Не задан API-ключ для GoSMS")
        return GoSMSProvider(settings.sms_api_key, settings.sms_sender or "")

    elif provider == "smsc":
        if not settings.sms_login or not settings.sms_password:
            raise ValueError("Не заданы логин и пароль для SMSC.ru")
        return SMSCProvider(settings.sms_login, settings.sms_password, settings.sms_sender or "")

    else:
        raise ValueError(f"Неизвестный SMS-провайдер: {provider}")