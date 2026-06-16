"""
AgentMax Internationalization (i18n) System

Complete i18n support with multiple languages,
right-to-left (RTL) support, and locale-aware formatting.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from fastapi import APIRouter

log = structlog.get_logger(__name__)


class Locale(str, Enum):
    """Supported locales"""

    EN_US = "en-US"
    EN_GB = "en-GB"
    ES_ES = "es-ES"
    ES_MX = "es-MX"
    FR_FR = "fr-FR"
    DE_DE = "de-DE"
    IT_IT = "it-IT"
    PT_BR = "pt-BR"
    ZH_CN = "zh-CN"
    ZH_TW = "zh-TW"
    JA_JP = "ja-JP"
    KO_KR = "ko-KR"
    AR_SA = "ar-SA"  # RTL
    HE_IL = "he-IL"  # RTL


@dataclass
class LocaleConfig:
    """Configuration for a locale"""

    code: Locale
    name: str
    native_name: str
    is_rtl: bool = False

    date_format: str = "YYYY-MM-DD"
    time_format: str = "HH:mm:ss"
    datetime_format: str = "YYYY-MM-DD HH:mm:ss"

    number_format: str = "."
    currency_symbol: str = "$"
    currency_position: str = "prefix"  # prefix or suffix

    plural_rules: str = "en"


@dataclass
class TranslationUnit:
    """Individual translation unit"""

    key: str
    value: str

    description: str | None = None
    context: str | None = None

    max_length: int | None = None

    alternative_values: dict[str, str] = field(default_factory=dict)
    # {"es-ES": "valor en español", "fr-FR": "valeur en français"}


class InternationalizationService:
    """Main i18n service for AgentMax"""

    # Locale configurations
    LOCALE_CONFIGS = {
        Locale.EN_US: LocaleConfig(
            code=Locale.EN_US,
            name="English (US)",
            native_name="English",
            is_rtl=False,
            date_format="MM/DD/YYYY",
            number_format=".",
            currency_symbol="$",
            currency_position="prefix",
        ),
        Locale.EN_GB: LocaleConfig(
            code=Locale.EN_GB,
            name="English (UK)",
            native_name="English",
            is_rtl=False,
            date_format="DD/MM/YYYY",
            number_format=".",
            currency_symbol="£",
            currency_position="prefix",
        ),
        Locale.ES_ES: LocaleConfig(
            code=Locale.ES_ES,
            name="Spanish (Spain)",
            native_name="Español",
            is_rtl=False,
            date_format="DD/MM/YYYY",
            number_format=",",
            currency_symbol="€",
            currency_position="suffix",
        ),
        Locale.ES_MX: LocaleConfig(
            code=Locale.ES_MX,
            name="Spanish (Mexico)",
            native_name="Español (México)",
            is_rtl=False,
            date_format="DD/MM/YYYY",
            number_format=".",
            currency_symbol="$",
            currency_position="prefix",
        ),
        Locale.FR_FR: LocaleConfig(
            code=Locale.FR_FR,
            name="French (France)",
            native_name="Français",
            is_rtl=False,
            date_format="DD/MM/YYYY",
            number_format=",",
            currency_symbol="€",
            currency_position="suffix",
        ),
        Locale.DE_DE: LocaleConfig(
            code=Locale.DE_DE,
            name="German (Germany)",
            native_name="Deutsch",
            is_rtl=False,
            date_format="DD.MM.YYYY",
            number_format=",",
            currency_symbol="€",
            currency_position="suffix",
        ),
        Locale.IT_IT: LocaleConfig(
            code=Locale.IT_IT,
            name="Italian (Italy)",
            native_name="Italiano",
            is_rtl=False,
            date_format="DD/MM/YYYY",
            number_format=".",
            currency_symbol="€",
            currency_position="suffix",
        ),
        Locale.PT_BR: LocaleConfig(
            code=Locale.PT_BR,
            name="Portuguese (Brazil)",
            native_name="Português (Brasil)",
            is_rtl=False,
            date_format="DD/MM/YYYY",
            number_format=".",
            currency_symbol="R$",
            currency_position="prefix",
        ),
        Locale.ZH_CN: LocaleConfig(
            code=Locale.ZH_CN,
            name="Chinese (Simplified)",
            native_name="简体中文",
            is_rtl=False,
            date_format="YYYY-MM-DD",
            number_format=".",
            currency_symbol="¥",
            currency_position="prefix",
        ),
        Locale.ZH_TW: LocaleConfig(
            code=Locale.ZH_TW,
            name="Chinese (Traditional)",
            native_name="繁體中文",
            is_rtl=False,
            date_format="YYYY-MM-DD",
            number_format=".",
            currency_symbol="¥",
            currency_position="prefix",
        ),
        Locale.JA_JP: LocaleConfig(
            code=Locale.JA_JP,
            name="Japanese",
            native_name="日本語",
            is_rtl=False,
            date_format="YYYY/MM/DD",
            number_format=".",
            currency_symbol="¥",
            currency_position="prefix",
        ),
        Locale.KO_KR: LocaleConfig(
            code=Locale.KO_KR,
            name="Korean",
            native_name="한국어",
            is_rtl=False,
            date_format="YYYY-MM-DD",
            number_format=".",
            currency_symbol="₩",
            currency_position="prefix",
        ),
        Locale.AR_SA: LocaleConfig(
            code=Locale.AR_SA,
            name="Arabic (Saudi Arabia)",
            native_name="العربية",
            is_rtl=True,
            date_format="DD/MM/YYYY",
            number_format=".",
            currency_symbol="ر.س",
            currency_position="suffix",
        ),
        Locale.HE_IL: LocaleConfig(
            code=Locale.HE_IL,
            name="Hebrew (Israel)",
            native_name="עברית",
            is_rtl=True,
            date_format="DD/MM/YYYY",
            number_format=".",
            currency_symbol="₪",
            currency_position="prefix",
        ),
    }

    def __init__(self, translations_path: str | None = None):
        self.translations_path = translations_path or "core/i18n/locales"

        self._translations: dict[Locale, dict[str, str]] = {locale: {} for locale in Locale}

        self._fallback_locale = Locale.EN_US
        self._current_locale = Locale.EN_US

        self._plural_rules: dict[str, Callable[[int], str]] = {}

        self._load_translations()
        self._init_plural_rules()

    def _load_translations(self):
        """Load all translation files"""

        # Load built-in translations
        self._translations[Locale.EN_US] = self._get_builtin_en()

        # Try to load from filesystem
        if os.path.exists(self.translations_path):
            for locale in Locale:
                file_path = os.path.join(self.translations_path, f"{locale.value}.json")

                if os.path.exists(file_path):
                    try:
                        with open(file_path, encoding="utf-8") as f:
                            self._translations[locale] = json.load(f)
                    except Exception as e:
                        log.warning("translation_load_failed", locale=locale.value, error=str(e))

        log.info(
            "translations_loaded", locales=len([k for k, v in self._translations.items() if v])
        )

    def _init_plural_rules(self):
        """Initialize pluralization rules for different languages"""

        self._plural_rules = {
            "en": lambda n: "one" if n == 1 else "other",
            "es": lambda n: "one" if n == 1 else "other",
            "fr": lambda n: "one" if n in [0, 1] else "other",
            "de": lambda n: "one" if n == 1 else "other",
            "it": lambda n: "one" if n == 1 else "other",
            "pt": lambda n: "one" if n in [0, 1] else "other",
            "zh": lambda n: "other",
            "ja": lambda n: "other",
            "ko": lambda n: "other",
            "ar": lambda n: "one" if n == 1 else "two" if n == 2 else "few" if n <= 10 else "other",
            "he": lambda n: "one" if n == 1 else "other",
        }

    def set_locale(self, locale: Locale):
        """Set current locale"""

        if locale not in self.LOCALE_CONFIGS:
            log.warning(
                "locale_not_supported", requested=locale.value, fallback=self._fallback_locale.value
            )
            locale = self._fallback_locale

        self._current_locale = locale

        log.info("locale_changed", locale=locale.value)

    def get_locale(self) -> Locale:
        """Get current locale"""
        return self._current_locale

    def is_rtl(self) -> bool:
        """Check if current locale is RTL"""
        config = self.LOCALE_CONFIGS.get(self._current_locale)
        return config.is_rtl if config else False

    def get_locale_config(self, locale: Locale | None = None) -> LocaleConfig:
        """Get locale configuration"""
        locale = locale or self._current_locale
        return self.LOCALE_CONFIGS.get(locale, self.LOCALE_CONFIGS[self._fallback_locale])

    def t(
        self,
        key: str,
        params: dict[str, Any] | None = None,
        locale: Locale | None = None,
        fallback: str | None = None,
    ) -> str:
        """
        Translate a key to the current locale.

        Args:
            key: Translation key (e.g., "auth.login.title")
            params: Parameters for interpolation
            locale: Override locale
            fallback: Fallback if key not found

        Returns:
            Translated string
        """
        locale = locale or self._current_locale

        # Try primary locale
        translation = self._translations.get(locale, {}).get(key)

        # Fall back to default locale
        if not translation:
            translation = self._translations.get(self._fallback_locale, {}).get(key)

        # Use provided fallback
        if not translation:
            translation = fallback or key

        # Interpolate parameters
        if params and translation:
            try:
                for param_key, param_value in params.items():
                    translation = translation.replace(f"{{{param_key}}}", str(param_value))
            except Exception as e:
                log.warning("interpolation_failed", key=key, error=str(e))

        return translation

    def tn(
        self,
        key: str,
        count: int,
        params: dict[str, Any] | None = None,
        locale: Locale | None = None,
    ) -> str:
        """
        Translate with pluralization.

        Args:
            key: Translation key
            count: Count for pluralization
            params: Parameters for interpolation
            locale: Override locale

        Returns:
            Translated string with correct plural form
        """
        locale = locale or self._current_locale

        # Get plural rule for locale
        lang_code = locale.value.split("-")[0]
        plural_rule = self._plural_rules.get(lang_code, self._plural_rules["en"])

        plural_form = plural_rule(count)

        # Try to get pluralized version
        plural_key = f"{key}.{plural_form}"
        translation = self._translations.get(locale, {}).get(plural_key)

        if not translation:
            # Fall back to singular
            translation = self._translations.get(locale, {}).get(key)

        if not translation:
            translation = self._translations.get(self._fallback_locale, {}).get(key)

        if not translation:
            translation = key

        # Interpolate count
        if params:
            params = {**params, "count": count}
        else:
            params = {"count": count}

        return self.t(key, params, locale)

    def format_date(self, date, format: str | None = None, locale: Locale | None = None) -> str:
        """Format date according to locale"""

        locale = locale or self._current_locale
        config = self.get_locale_config(locale)

        date_format = format or config.date_format

        # Simple date formatting (in production, use arrow or similar)

        if isinstance(date, str):
            date = datetime.fromisoformat(date)

        # Replace format tokens
        result = date_format
        result = result.replace("YYYY", f"{date.year:04d}")
        result = result.replace("MM", f"{date.month:02d}")
        result = result.replace("DD", f"{date.day:02d}")

        return result

    def format_time(self, time, format: str | None = None, locale: Locale | None = None) -> str:
        """Format time according to locale"""

        locale = locale or self._current_locale
        config = self.get_locale_config(locale)

        time_format = format or config.time_format

        if isinstance(time, str):
            if "T" in time:
                time = datetime.fromisoformat(time).time()
            else:
                time = datetime.strptime(time, "%H:%M:%S").time()

        result = time_format
        result = result.replace("HH", f"{time.hour:02d}")
        result = result.replace("mm", f"{time.minute:02d}")
        result = result.replace("ss", f"{time.second:02d}")

        return result

    def format_number(self, number: float, decimals: int = 0, locale: Locale | None = None) -> str:
        """Format number according to locale"""

        locale = locale or self._current_locale
        config = self.get_locale_config(locale)

        number_format = config.number_format

        if number_format == ",":
            return f"{number:,.{decimals}f}".replace(".", ",")
        else:
            return f"{number:,.{decimals}f}"

    def format_currency(
        self, amount: float, currency: str | None = None, locale: Locale | None = None
    ) -> str:
        """Format currency according to locale"""

        locale = locale or self._current_locale
        config = self.get_locale_config(locale)

        symbol = currency or config.currency_symbol

        number = self.format_number(amount, 2, locale)

        if config.currency_position == "prefix":
            return f"{symbol}{number}"
        else:
            return f"{number}{symbol}"

    def get_available_locales(self) -> list[Locale]:
        """Get list of locales with translations"""
        return [locale for locale, translations in self._translations.items() if translations]

    def get_all_locales_info(self) -> list[dict[str, Any]]:
        """Get info about all supported locales"""

        return [
            {
                "code": locale.value,
                "name": config.name,
                "native_name": config.native_name,
                "is_rtl": config.is_rtl,
                "available": bool(self._translations.get(locale)),
            }
            for locale, config in self.LOCALE_CONFIGS.items()
        ]

    def add_translation(self, locale: Locale, key: str, value: str):
        """Add a single translation (runtime)"""

        if locale not in self._translations:
            self._translations[locale] = {}

        self._translations[locale][key] = value

    def _get_builtin_en(self) -> dict[str, str]:
        """Get built-in English translations"""

        return {
            # Common
            "common.ok": "OK",
            "common.cancel": "Cancel",
            "common.save": "Save",
            "common.delete": "Delete",
            "common.edit": "Edit",
            "common.close": "Close",
            "common.loading": "Loading...",
            "common.error": "Error",
            "common.success": "Success",
            "common.warning": "Warning",
            "common.info": "Info",
            # Auth
            "auth.login.title": "Sign In",
            "auth.login.subtitle": "Welcome back!",
            "auth.login.email": "Email address",
            "auth.login.password": "Password",
            "auth.login.remember_me": "Remember me",
            "auth.login.forgot_password": "Forgot password?",
            "auth.login.submit": "Sign In",
            "auth.login.no_account": "Don't have an account?",
            "auth.login.sign_up": "Sign Up",
            "auth.register.title": "Create Account",
            "auth.register.subtitle": "Get started with AgentMax",
            "auth.register.name": "Full name",
            "auth.register.email": "Email address",
            "auth.register.password": "Password",
            "auth.register.confirm_password": "Confirm password",
            "auth.register.submit": "Create Account",
            "auth.register.already_have": "Already have an account?",
            "auth.logout.title": "Sign Out",
            "auth.logout.confirm": "Are you sure you want to sign out?",
            # License
            "license.title": "License",
            "license.current": "Current Plan",
            "license.tier.starter": "Starter",
            "license.tier.pro": "Pro",
            "license.tier.elite": "Elite",
            "license.tokens_remaining": "{count} tokens remaining",
            "license.tokens_used": "{count} tokens used",
            "license.expires": "Expires {date}",
            "license.activate": "Activate License",
            "license.deactivate": "Deactivate",
            # Subscription
            "subscription.title": "Subscription",
            "subscription.upgrade": "Upgrade Plan",
            "subscription.current_plan": "Current Plan",
            "subscription.billing": "Billing",
            "subscription.payment_method": "Payment Method",
            "subscription.invoices": "Invoices",
            "subscription.cancel": "Cancel Subscription",
            # Errors
            "error.network": "Network error. Please check your connection.",
            "error.server": "Server error. Please try again later.",
            "error.unauthorized": "Please sign in to continue.",
            "error.forbidden": "You don't have permission to perform this action.",
            "error.not_found": "The requested resource was not found.",
            "error.validation": "Please check the form for errors.",
            "error.rate_limit": "Too many requests. Please wait a moment.",
            # Settings
            "settings.title": "Settings",
            "settings.account": "Account",
            "settings.security": "Security",
            "settings.notifications": "Notifications",
            "settings.appearance": "Appearance",
            "settings.language": "Language",
            "settings.theme": "Theme",
            "settings.theme.light": "Light",
            "settings.theme.dark": "Dark",
            "settings.theme.system": "System",
            # Bot
            "bot.title": "AgentMax Bot",
            "bot.placeholder": "Type your message...",
            "bot.send": "Send",
            "bot.thinking": "Thinking...",
        }


# Global i18n instance
_i18n: InternationalizationService | None = None


def get_i18n() -> InternationalizationService:
    """Get global i18n instance"""
    global _i18n
    if _i18n is None:
        _i18n = InternationalizationService()
    return _i18n


def set_locale(locale: Locale):
    """Set global locale"""
    get_i18n().set_locale(locale)


def t(key: str, params: dict[str, Any] | None = None) -> str:
    """Shortcut for translate"""
    return get_i18n().t(key, params)


def tn(key: str, count: int, params: dict[str, Any] | None = None) -> str:
    """Shortcut for translate with plural"""
    return get_i18n().tn(key, count, params)


# FastAPI integration
class I18nMiddleware:
    """Middleware to detect and set locale from request"""

    def __init__(self, app, i18n_service: InternationalizationService):
        self.app = app
        self.i18n = i18n_service

    async def __call__(self, scope, receive, send):

        # Try to get locale from headers
        headers = dict(scope.get("headers", []))

        accept_language = headers.get(b"accept-language", b"").decode()

        if accept_language:
            # Parse accept-language header
            for locale in Locale:
                if locale.value.lower() in accept_language.lower():
                    self.i18n.set_locale(locale)
                    break

        await self.app(scope, receive, send)


class I18nRouter:
    """FastAPI router for i18n endpoints"""

    def __init__(self, i18n_service: InternationalizationService):
        self.i18n = i18n_service

    def get_router(self) -> APIRouter:
        from fastapi import APIRouter

        router = APIRouter(prefix="/i18n", tags=["Internationalization"])

        @router.get("/locales")
        async def get_locales():
            """Get all available locales"""
            return {
                "locales": self.i18n.get_all_locales_info(),
                "current": self.i18n.get_locale().value,
            }

        @router.get("/locale/current")
        async def get_current_locale():
            """Get current locale"""
            locale = self.i18n.get_locale()
            config = self.i18n.get_locale_config(locale)

            return {"locale": locale.value, "is_rtl": config.is_rtl, "name": config.name}

        @router.post("/locale/{locale}")
        async def set_locale(locale: Locale):
            """Set current locale"""
            self.i18n.set_locale(locale)
            return {"success": True, "locale": locale.value}

        @router.get("/translate/{key}")
        async def translate(key: str, locale: Locale | None = None, params: str | None = None):
            """Get translation for a key"""

            params_dict = {}
            if params:
                import json

                try:
                    params_dict = json.loads(params)
                except (json.JSONDecodeError, ValueError):
                    log.warning("i18n.translate_params_parse_failed", params=params[:200])

            return {"key": key, "translation": self.i18n.t(key, params_dict, locale)}

        @router.get("/format/date")
        async def format_date(date: str, format: str | None = None, locale: Locale | None = None):
            """Format date according to locale"""
            return {"formatted": self.i18n.format_date(date, format, locale)}

        @router.get("/format/number")
        async def format_number(number: float, decimals: int = 0, locale: Locale | None = None):
            """Format number according to locale"""
            return {"formatted": self.i18n.format_number(number, decimals, locale)}

        @router.get("/format/currency")
        async def format_currency(
            amount: float, currency: str | None = None, locale: Locale | None = None
        ):
            """Format currency according to locale"""
            return {"formatted": self.i18n.format_currency(amount, currency, locale)}

        return router
