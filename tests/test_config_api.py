from api.config import CONFIG_KEYS, ConfigUpdate, get_config, update_config


def test_config_api_allows_smsbower_price_steps(monkeypatch):
    saved = {}

    def fake_set_many(data):
        saved.update(data)

    monkeypatch.setattr("api.config.config_store.set_many", fake_set_many)

    response = update_config(
        ConfigUpdate(data={"smsbower_price_steps": "0.006,0.01,0.019"})
    )

    assert "smsbower_price_steps" in CONFIG_KEYS
    assert saved == {"smsbower_price_steps": "0.006,0.01,0.019"}
    assert response["updated"] == ["smsbower_price_steps"]


def test_config_api_defaults_sms_country_priority_to_fr_vn_us_last(monkeypatch):
    monkeypatch.setattr("api.config.config_store.get_all", lambda: {})

    response = get_config()

    assert response["smsbower_country"] == "78,10,6,22,73,16,187,52,12"
    assert response["smstome_country_slugs"] == "united-states"


def test_config_api_allows_payment_python_executable(monkeypatch):
    saved = {}

    def fake_set_many(data):
        saved.update(data)

    monkeypatch.setattr("api.config.config_store.set_many", fake_set_many)

    response = update_config(
        ConfigUpdate(data={"payment_python_executable": r"E:\ctf-pay\python.exe"})
    )

    assert "payment_python_executable" in CONFIG_KEYS
    assert saved == {"payment_python_executable": r"E:\ctf-pay\python.exe"}
    assert response["updated"] == ["payment_python_executable"]


def test_config_api_allows_payment_plus_flow_order(monkeypatch):
    saved = {}

    def fake_set_many(data):
        saved.update(data)

    monkeypatch.setattr("api.config.config_store.set_many", fake_set_many)

    response = update_config(
        ConfigUpdate(data={"payment_plus_flow_order": "before_oauth"})
    )

    assert "payment_plus_flow_order" in CONFIG_KEYS
    assert saved == {"payment_plus_flow_order": "before_oauth"}
    assert response["updated"] == ["payment_plus_flow_order"]


def test_config_api_allows_payment_provider_and_retry_keys(monkeypatch):
    saved = {}

    def fake_set_many(data):
        saved.update(data)

    monkeypatch.setattr("api.config.config_store.set_many", fake_set_many)

    response = update_config(
        ConfigUpdate(
            data={
                "payment_provider": "gopay_android",
                "payment_proxy_pool": "http://127.0.0.1:7890,http://127.0.0.1:7891",
                "payment_max_retries": "3",
                "payment_paypal_proxy_url": "socks5://jp.example:1080",
                "payment_promo_proxy_url": "socks5://jp-promo.example:1080",
                "payment_promo_proxy_geo": "JP",
            }
        )
    )

    assert "payment_provider" in CONFIG_KEYS
    assert "payment_proxy_pool" in CONFIG_KEYS
    assert "payment_max_retries" in CONFIG_KEYS
    assert "payment_paypal_proxy_url" in CONFIG_KEYS
    assert "payment_promo_proxy_url" in CONFIG_KEYS
    assert "payment_promo_proxy_geo" in CONFIG_KEYS
    assert saved == {
        "payment_provider": "gopay_android",
        "payment_proxy_pool": "http://127.0.0.1:7890,http://127.0.0.1:7891",
        "payment_max_retries": "3",
        "payment_paypal_proxy_url": "socks5://jp.example:1080",
        "payment_promo_proxy_url": "socks5://jp-promo.example:1080",
        "payment_promo_proxy_geo": "JP",
    }
    assert response["updated"] == [
        "payment_provider",
        "payment_proxy_pool",
        "payment_max_retries",
        "payment_paypal_proxy_url",
        "payment_promo_proxy_url",
        "payment_promo_proxy_geo",
    ]


def test_config_api_allows_gopay_android_keys(monkeypatch):
    saved = {}

    def fake_set_many(data):
        saved.update(data)

    monkeypatch.setattr("api.config.config_store.set_many", fake_set_many)

    response = update_config(
        ConfigUpdate(
            data={
                "payment_gopay_otp_retries": "3",
                "payment_gojek_app_version": "4.95.1",
                "payment_android_avd_name": "Pixel_8_Play",
                "payment_android_serial": "emulator-5554",
                "payment_android_headless": "0",
                "payment_android_gojek_apk": r"C:\apk\gojek.apk",
                "payment_android_gopay_apk": r"C:\apk\gopay.apk",
                "payment_android_adb_path": r"C:\Android\platform-tools\adb.exe",
                "payment_android_emulator_path": r"C:\Android\emulator\emulator.exe",
            }
        )
    )

    expected_keys = {
        "payment_gopay_otp_retries",
        "payment_gojek_app_version",
        "payment_android_avd_name",
        "payment_android_serial",
        "payment_android_headless",
        "payment_android_gojek_apk",
        "payment_android_gopay_apk",
        "payment_android_adb_path",
        "payment_android_emulator_path",
    }
    assert expected_keys.issubset(set(CONFIG_KEYS))
    assert saved == {
        "payment_gopay_otp_retries": "3",
        "payment_gojek_app_version": "4.95.1",
        "payment_android_avd_name": "Pixel_8_Play",
        "payment_android_serial": "emulator-5554",
        "payment_android_headless": "0",
        "payment_android_gojek_apk": r"C:\apk\gojek.apk",
        "payment_android_gopay_apk": r"C:\apk\gopay.apk",
        "payment_android_adb_path": r"C:\Android\platform-tools\adb.exe",
        "payment_android_emulator_path": r"C:\Android\emulator\emulator.exe",
    }
    assert response["updated"] == list(saved.keys())


def test_config_api_defaults_new_payment_keys(monkeypatch):
    monkeypatch.setattr("api.config.config_store.get_all", lambda: {})

    response = get_config()

    assert response["payment_provider"] == ""
    assert response["payment_max_retries"] == "2"
    assert response["payment_promo_proxy_geo"] == "JP"
    assert response["payment_gopay_otp_retries"] == "2"
    assert response["payment_android_headless"] == "1"
