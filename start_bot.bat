@echo off
echo ========================================
echo    Avito Parser - Telegram Bot
echo ========================================
echo.

REM Проверяем наличие виртуального окружения
if not exist "venv\Scripts\python.exe" (
    echo ОШИБКА: Виртуальное окружение не найдено!
    echo Создайте его командой: python -m venv venv
    echo Затем установите зависимости: venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM Проверяем наличие .env файла
if not exist ".env" (
    echo ОШИБКА: Файл .env не найден!
    echo Создайте файл .env с токеном бота:
    echo TELEGRAM_BOT_TOKEN=ваш_токен_здесь
    pause
    exit /b 1
)

REM Останавливаем все предыдущие экземпляры Python
echo Проверка запущенных экземпляров бота...
tasklist /fi "imagename eq python.exe" 2>nul | find /i "python.exe" >nul
if not errorlevel 1 (
    echo Найдены запущенные экземпляры Python. Останавливаем...
    taskkill /f /im python.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
    echo Предыдущие экземпляры остановлены.
) else (
    echo Предыдущие экземпляры не найдены.
)

echo.
echo Запуск Telegram бота...
echo Для остановки нажмите Ctrl+C
echo.

REM Запускаем бота
venv\Scripts\python.exe -m bot.runner

echo.
echo Бот остановлен.
pause
