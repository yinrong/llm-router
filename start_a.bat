@echo off
chcp 65001 >nul
echo llmrouter - 用户端 (A)
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 Python，请先安装 Python 3.10+
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 检查依赖
python -c "import aiohttp" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖...
    pip install aiohttp cryptography
)

REM 检查 .env
if not exist ".env" (
    echo 未找到 .env 配置文件，启动配置向导...
    python setup.py
)

echo.
echo 输入消息测试连接（Ctrl+C 退出）:
set /p MSG="你的消息: "
python api_client.py "%MSG%"
pause
