# 复制为 local-env.ps1 后填写本机配置。local-env.ps1 已被 Git 忽略，不会提交密钥。
$env:EVENT_CENTER_DB_PASSWORD = "请填写 MySQL 密码"
$env:SCHEDULE_DB_PASSWORD = "请填写 MySQL 密码"
$env:TASK_DB_PASSWORD = "请填写 MySQL 密码"

# NapCat 由客户端扫码后自动创建 OneBot HTTP Server 和事件回调，默认无需填写 Token。
# 只有自动发现 WebUI Token 失败时，才需要从 NapCat WebUI 的系统配置中复制 Token。
# $env:NAPCAT_WEBUI_TOKEN = "请填写 NapCat WebUI Token"
# $env:NAPCAT_NATIVE_CONFIG_PATHS = "D:\napcat"

# 可选：自动创建的 OneBot HTTP Server Token。仅把 3011 端口暴露给其他机器时建议启用。
# $env:NAPCAT_API_TOKEN = "请填写自定义 OneBot Token"

# 可选：只有端口、账号或地址不是默认值时才需要取消注释。
# $env:EVENT_CENTER_DB_URL = "jdbc:mysql://127.0.0.1:3306/memo_echo_event_center?createDatabaseIfNotExist=true&useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&useSSL=false"
# $env:SCHEDULE_DB_URL = "jdbc:mysql://127.0.0.1:3306/memo_echo_schedule?createDatabaseIfNotExist=true&useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&useSSL=false"
# $env:TASK_DB_URL = "jdbc:mysql://127.0.0.1:3306/memo_echo_task?createDatabaseIfNotExist=true&useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&useSSL=false"
# $env:EVENT_CENTER_DB_USERNAME = "root"
# $env:SCHEDULE_DB_USERNAME = "root"
# $env:TASK_DB_USERNAME = "root"
# $env:NAPCAT_API_BASE_URL = "http://127.0.0.1:3011"

# 可选：NapCat 不是 Docker 部署时，事件回调地址应指向本机 Connector。
# $env:NAPCAT_EVENT_CALLBACK_URL = "http://127.0.0.1:8091/api/connectors/qq/napcat/events"
