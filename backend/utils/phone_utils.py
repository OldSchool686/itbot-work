import re


def normalize_phone(phone: str) -> str:
    """Normalize phone number to +7XXXXXXXXXX format."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone.strip())
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if len(digits) == 11 and digits[0] == "8":
        return "+7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        return "+" + digits
    if len(digits) == 10:
        return "+79" + digits[1:]
    return f"+{digits}" if not phone.startswith("+") else phone
