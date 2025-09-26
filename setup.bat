@echo off
echo ========================================
echo    Avito Parser - Setup
echo ========================================
echo.

REM Проверяем наличие Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: Python не найден!
    echo Установите Python 3.8+ с https://python.org
    pause
    exit /b 1
)

echo Python найден. Создание виртуального окружения...

REM Создаем виртуальное окружение
python -m venv venv
if errorlevel 1 (
    echo ОШИБКА: Не удалось создать виртуальное окружение!
    pause
    exit /b 1
)

echo Виртуальное окружение создано. Установка зависимостей...

REM Активируем виртуальное окружение и устанавливаем зависимости
call venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt

if errorlevel 1 (
    echo ОШИБКА: Не удалось установить зависимости!
    pause
    exit /b 1
)

echo.
echo ========================================
echo    Настройка завершена!
echo ========================================
echo.

REM Проверяем наличие .env файла
if not exist ".env" (
    echo Создание файла .env...
    echo TELEGRAM_BOT_TOKEN=ваш_токен_здесь > .env
    echo.
    echo ВАЖНО: Отредактируйте файл .env и укажите ваш токен бота!
    echo Формат: TELEGRAM_BOT_TOKEN=123456789:AAAbbbCccDDD_eeee-FFFFggggHHHHiiii
    echo.
) else (
    echo Файл .env уже существует.
)

echo Доступные команды:
echo - start_bot.bat     - запуск Telegram бота
echo - start_parser.bat  - запуск парсера в CLI режиме
echo.
echo Нажмите любую клавишу для выхода...
pause >nul
