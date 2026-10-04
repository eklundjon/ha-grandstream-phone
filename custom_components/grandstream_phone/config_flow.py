"""Config flow: host, then the detected model, then credentials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    AccountLocked,
    CannotConnect,
    GrandstreamClient,
    GrandstreamError,
    InvalidAuth,
    LoginInfo,
    async_get_model_info,
)
from .const import (
    CONF_MODEL,
    DEFAULT_USERNAME,
    DEVICE_SUPPORT_URL,
    DOMAIN,
    VERIFIED_MODELS,
)
from .coordinator import async_get_session

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _credentials_schema(username: str = DEFAULT_USERNAME) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username): str,
            vol.Required(CONF_PASSWORD): _PASSWORD,
        }
    )


class GrandstreamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a handset."""

    VERSION = 1

    def __init__(self) -> None:
        self._host = ""
        self._model = ""
        self._title = ""
        self._data: dict[str, Any] = {}

    # ---- add a handset ---------------------------------------------------- #

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                info = await async_get_model_info(async_get_session(self.hass), host)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                self._host = host
                self._model = str(info["model"])
                return await self.async_step_credentials()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"model": self._model}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            result = await self._async_try_login(
                self._host, username, password, errors, placeholders
            )
            if result is not None:
                info, extension = result
                await self.async_set_unique_id(format_mac(info.mac))
                # Same handset at a new address: update it rather than add it twice.
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
                self._title = f"{self._model} {extension}" if extension else self._model
                self._data = {
                    CONF_HOST: self._host,
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                    CONF_MODEL: self._model,
                }
                if self._model not in VERIFIED_MODELS:
                    return await self.async_step_unverified_model()
                return self.async_create_entry(title=self._title, data=self._data)
        return self.async_show_form(
            step_id="credentials",
            data_schema=_credentials_schema(
                user_input[CONF_USERNAME] if user_input else DEFAULT_USERNAME
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_unverified_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The handset answers like a supported one but hasn't been tested."""
        if user_input is not None:
            return self.async_create_entry(title=self._title, data=self._data)
        return self.async_show_form(
            step_id="unverified_model",
            description_placeholders={
                "model": self._model,
                "verified": ", ".join(sorted(VERIFIED_MODELS)),
                "report_url": DEVICE_SUPPORT_URL,
            },
        )

    # ---- fix an existing handset ------------------------------------------ #

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders = {"model": entry.data.get(CONF_MODEL, ""), "host": entry.data[CONF_HOST]}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            result = await self._async_try_login(
                entry.data[CONF_HOST], username, user_input[CONF_PASSWORD], errors, placeholders
            )
            if result is not None:
                await self.async_set_unique_id(format_mac(result[0].mac))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(entry.data[CONF_USERNAME]),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing handset at a new address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders = {"model": entry.data.get(CONF_MODEL, "")}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                model = str((await async_get_model_info(async_get_session(self.hass), host))["model"])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                result = await self._async_try_login(
                    host, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD], errors, placeholders
                )
                if result is not None:
                    await self.async_set_unique_id(format_mac(result[0].mac))
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_HOST: host, CONF_MODEL: model}
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    # ---- helpers ----------------------------------------------------------- #

    async def _async_try_login(
        self,
        host: str,
        username: str,
        password: str,
        errors: dict[str, str],
        placeholders: dict[str, str],
    ) -> tuple[LoginInfo, str | None] | None:
        """Log in once and out again. On failure, fill in ``errors``.

        Returns the login info and the first SIP account's extension, used to
        tell handsets apart in the entry title.
        """
        client = GrandstreamClient(async_get_session(self.hass), host, username, password)
        try:
            info = await client.async_login()
            accounts = await client.async_get_accounts()
        except InvalidAuth as err:
            if err.attempts_left is not None:
                errors["base"] = "invalid_auth_attempts"
                placeholders["attempts_left"] = str(err.attempts_left)
            elif "disabled" in err.reason:
                errors["base"] = "user_access_disabled"
            else:
                errors["base"] = "invalid_auth"
            return None
        except AccountLocked as err:
            errors["base"] = "account_locked"
            placeholders["minutes"] = str(err.minutes or "a few")
            return None
        except GrandstreamError:
            errors["base"] = "cannot_connect"
            return None
        finally:
            await client.async_logout()
        extension = next(
            (str(a["sip_id"]) for a in accounts if isinstance(a, dict) and a.get("sip_id")),
            None,
        )
        return info, extension
