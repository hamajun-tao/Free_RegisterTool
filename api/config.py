from fastapi import APIRouter
from pydantic import BaseModel

from core.config_store import config_store

router = APIRouter(prefix="/config", tags=["config"])

CONFIG_KEYS = [
    "laoudo_auth",
    "laoudo_email",
    "laoudo_account_id",
    "yescaptcha_key",
    "twocaptcha_key",
    "default_executor",
    "default_captcha_solver",
    "duckmail_api_url",
    "duckmail_provider_url",
    "duckmail_bearer",
    "duckmail_domain",
    "duckmail_api_key",
    "duckduckgo_email",
    "duckduckgo_gmail_address",
    "duckduckgo_gmail_app_password",
    "duckduckgo_imap_host",
    "duckduckgo_imap_port",
    "duckduckgo_mailbox",
    "duckduckgo_all_mailbox",
    "duckduckgo_gmail_api_mode",
    "duckduckgo_gmail_api_credentials",
    "duckduckgo_gmail_api_token",
    "duckduckgo_api_token",
    "duckduckgo_alias_mode",
    "duckduckgo_private_addresses",
    "duckduckgo_alias_rotation",
    "freemail_api_url",
    "freemail_admin_token",
    "freemail_username",
    "freemail_password",
    "mail2925_login_name",
    "mail2925_password",
    "mail2925_alias_mode",
    "mail2925_domain",
    "moemail_api_url",
    "moemail_api_key",
    "skymail_api_base",
    "skymail_token",
    "skymail_domain",
    "mail_provider",
    "maliapi_base_url",
    "maliapi_api_key",
    "maliapi_domain",
    "maliapi_auto_domain_strategy",
    "gptmail_base_url",
    "gptmail_api_key",
    "gptmail_domain",
    "opentrashmail_api_url",
    "opentrashmail_domain",
    "opentrashmail_password",
    "cfworker_api_url",
    "cfworker_admin_token",
    "cfworker_custom_auth",
    "cfworker_domain",
    "cfworker_domains",
    "cfworker_enabled_domains",
    "cfworker_subdomain",
    "cfworker_random_subdomain",
    "cfworker_fingerprint",
    "smsbower_api_key",
    "sms_provider",
    "sim5_api_key",
    "herosms_api_key",
    "smsbower_country",
    "smsbower_type",
    "smsbower_max_price",
    "smsbower_min_price",
    "smsbower_price_steps",
    "smsbower_provider_ids",
    "smsbower_except_provider_ids",
    "smsbower_phone_attempts",
    "smsbower_add_phone_send_attempts",
    "smsbower_otp_timeout_seconds",
    "smsbower_code_attempts",
    "fraud_guard_proxy_rotations",
    "smstome_cookie",
    "smstome_country_slugs",
    "smstome_phone_attempts",
    "smstome_otp_timeout_seconds",
    "smstome_poll_interval_seconds",
    "smstome_sync_max_pages_per_country",
    "luckmail_base_url",
    "luckmail_api_key",
    "luckmail_project_code",
    "luckmail_email_type",
    "luckmail_domain",
    "cpa_api_url",
    "cpa_api_key",
    "cpa_cleanup_enabled",
    "cpa_cleanup_interval_minutes",
    "cpa_cleanup_threshold",
    "cpa_cleanup_concurrency",
    "cpa_cleanup_register_delay_seconds",
    "sub2api_api_url",
    "sub2api_api_key",
    "sub2api_group_ids",
    "team_manager_url",
    "team_manager_key",
    "codex_proxy_url",
    "codex_proxy_key",
    "codex_proxy_upload_type",
    "cliproxyapi_base_url",
    "cliproxyapi_management_key",
    "grok2api_url",
    "grok2api_app_key",
    "grok2api_pool",
    "grok2api_quota",
    "kiro_manager_path",
    "kiro_manager_exe",
]


class ConfigUpdate(BaseModel):
    data: dict


@router.get("")
def get_config():
    raw_cfg = config_store.get_all()
    all_cfg = {key: raw_cfg.get(key, "") for key in CONFIG_KEYS}
    if not all_cfg.get("mail_provider"):
        all_cfg["mail_provider"] = "luckmail"
    if not all_cfg.get("gptmail_base_url"):
        all_cfg["gptmail_base_url"] = "https://mail.chatgpt.org.uk"
    if not all_cfg.get("duckduckgo_imap_host"):
        all_cfg["duckduckgo_imap_host"] = "imap.gmail.com"
    if not all_cfg.get("duckduckgo_imap_port"):
        all_cfg["duckduckgo_imap_port"] = "993"
    if not all_cfg.get("duckduckgo_mailbox"):
        all_cfg["duckduckgo_mailbox"] = "INBOX"
    if not all_cfg.get("duckduckgo_all_mailbox"):
        all_cfg["duckduckgo_all_mailbox"] = "[Gmail]/All Mail"
    if not all_cfg.get("duckduckgo_gmail_api_mode"):
        all_cfg["duckduckgo_gmail_api_mode"] = "imap"
    if not all_cfg.get("duckduckgo_alias_mode"):
        all_cfg["duckduckgo_alias_mode"] = "fixed"
    if not all_cfg.get("duckduckgo_alias_rotation"):
        all_cfg["duckduckgo_alias_rotation"] = "random"
    if not all_cfg.get("luckmail_base_url"):
        all_cfg["luckmail_base_url"] = "https://mails.luckyous.com/"
    if not all_cfg.get("mail2925_domain"):
        all_cfg["mail2925_domain"] = "2925.com"
    if not all_cfg.get("mail2925_alias_mode"):
        all_cfg["mail2925_alias_mode"] = "main"
    if not all_cfg.get("smsbower_country"):
        all_cfg["smsbower_country"] = "10,6,22,73,187,52,12,78"
    if not all_cfg.get("sms_provider"):
        all_cfg["sms_provider"] = "smsbower"
    if not all_cfg.get("smsbower_phone_attempts"):
        all_cfg["smsbower_phone_attempts"] = "12"
    if not all_cfg.get("smsbower_add_phone_send_attempts"):
        all_cfg["smsbower_add_phone_send_attempts"] = "8"
    if not all_cfg.get("smsbower_otp_timeout_seconds"):
        all_cfg["smsbower_otp_timeout_seconds"] = "120"
    if not all_cfg.get("smsbower_code_attempts"):
        all_cfg["smsbower_code_attempts"] = "2"
    if not all_cfg.get("fraud_guard_proxy_rotations"):
        all_cfg["fraud_guard_proxy_rotations"] = "3"
    if not all_cfg.get("smstome_country_slugs"):
        all_cfg["smstome_country_slugs"] = "united-states"
    if not all_cfg.get("payment_plus_flow_order"):
        all_cfg["payment_plus_flow_order"] = "after_oauth"
    if not all_cfg.get("payment_max_retries"):
        all_cfg["payment_max_retries"] = "2"
    if not all_cfg.get("payment_promo_proxy_geo"):
        all_cfg["payment_promo_proxy_geo"] = "JP"
    if not all_cfg.get("payment_skip_if_not_free"):
        all_cfg["payment_skip_if_not_free"] = "1"
    if not all_cfg.get("payment_auto_cancel_after_subscribe"):
        # 默认开启：开通后立即取消防止下月扣费
        all_cfg["payment_auto_cancel_after_subscribe"] = "1"
    if not all_cfg.get("payment_gopay_auto_register"):
        # 默认关闭：需用户主动启用（首次会消耗 SMSBOWER 余额）
        all_cfg["payment_gopay_auto_register"] = "0"
    if not all_cfg.get("payment_gopay_sms_country"):
        all_cfg["payment_gopay_sms_country"] = "6"
    if not all_cfg.get("payment_gopay_sms_service"):
        all_cfg["payment_gopay_sms_service"] = "ot"
    if not all_cfg.get("payment_gopay_otp_retries"):
        all_cfg["payment_gopay_otp_retries"] = "2"
    if not all_cfg.get("payment_android_headless"):
        all_cfg["payment_android_headless"] = "1"
    if not all_cfg.get("payment_captcha_validate_online"):
        all_cfg["payment_captcha_validate_online"] = "1"
    if not all_cfg.get("payment_vlm_model"):
        all_cfg["payment_vlm_model"] = "gpt-4o"
    if not all_cfg.get("payment_vlm_timeout_s"):
        all_cfg["payment_vlm_timeout_s"] = "45"
    if not all_cfg.get("payment_phone_failure_keep_as_free"):
        all_cfg["payment_phone_failure_keep_as_free"] = "0"
    return all_cfg


@router.put("")
def update_config(body: ConfigUpdate):
    safe = {k: v for k, v in body.data.items() if k in CONFIG_KEYS}
    config_store.set_many(safe)
    return {"ok": True, "updated": list(safe.keys())}
