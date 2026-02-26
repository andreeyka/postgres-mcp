"""Общие вспомогательные функции."""

import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse


logger = logging.getLogger(__name__)

# If the recommendation cost is 0.0, we can't calculate the improvement multiple.
# Return 1000000.0 to indicate infinite improvement.
INFINITE_IMPROVEMENT_MULTIPLIER = 1000000.0


def calculate_improvement_multiple(base_cost: float, rec_cost: float) -> float:
    """Вычислить множитель улучшения от данной рекомендации.

    Args:
        base_cost: Базовая стоимость выполнения.
        rec_cost: Рекомендованная стоимость выполнения.

    Returns:
        Множитель улучшения (base_cost / rec_cost).
    """
    if base_cost <= 0.0:
        # base_cost or rec_cost might be zero, but as they are floats, the might be
        # represented as -0.0. That's why we compare to <= 0.0.
        return 1.0
    if rec_cost <= 0.0:
        # If the recommendation cost is 0.0, we can't calculate the improvement multiple.
        # Return INFINITE_IMPROVEMENT_MULTIPLIER to indicate infinite improvement.
        return INFINITE_IMPROVEMENT_MULTIPLIER
    return base_cost / rec_cost


def decode_bytes_to_utf8(obj: Any) -> Any:  # noqa: ANN401
    """Рекурсивно декодировать байты в строки UTF-8 для сериализации JSON.

    Args:
        obj: Объект, который может содержать байты (dict, list, bytes, str и т.д.)

    Returns:
        Объект с декодированными байтами в виде строк UTF-8.
    """
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.decode("latin-1")
    if isinstance(obj, dict):
        return {key: decode_bytes_to_utf8(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [decode_bytes_to_utf8(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(decode_bytes_to_utf8(item) for item in obj)
    return obj


def obfuscate_password(text: str | None) -> str | None:
    """Замаскировать пароль в любом тексте, содержащем информацию о подключении.

    Работает с URL подключений, сообщениями об ошибках и другими строками.

    Args:
        text: Текст, содержащий информацию о подключении.

    Returns:
        Текст с замаскированными паролями или None, если вход был None.
    """
    if text is None:
        return None

    if not text:
        return text

    # Try first as a proper URL
    try:
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc and parsed.password:
            # Replace password with asterisks in proper URL
            netloc = parsed.netloc.replace(parsed.password, "****")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception as e:
        # If URL parsing fails, fall back to regex-based obfuscation
        logger.debug("Failed to parse text as URL, using regex-based obfuscation: %s", e)

    # Handle strings that contain connection strings but aren't proper URLs
    # Match postgres://user:password@host:port/dbname pattern
    url_pattern = re.compile(r"(postgres(?:ql)?:\/\/[^:]+:)([^@]+)(@[^\/\s]+)")
    text = re.sub(url_pattern, r"\1****\3", text)

    # Match connection string parameters (password=xxx)
    # This simpler pattern captures password without quotes
    param_pattern = re.compile(r'(password=)([^\s&;"\']+)', re.IGNORECASE)
    text = re.sub(param_pattern, r"\1****", text)

    # Match password in DSN format with single quotes
    dsn_single_quote = re.compile(r"(password\s*=\s*')([^']+)(')", re.IGNORECASE)
    text = re.sub(dsn_single_quote, r"\1****\3", text)

    # Match password in DSN format with double quotes
    dsn_double_quote = re.compile(r'(password\s*=\s*")([^"]+)(")', re.IGNORECASE)
    return re.sub(dsn_double_quote, r"\1****\3", text)
